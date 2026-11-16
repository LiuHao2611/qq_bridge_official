# 视频链接解析插件入口：注册 on_message matcher，自动解析群聊/主人私聊转发的视频链接，
# 解析出标题/分区/UP 等信息后以卡片消息回复（含封面图）。
#
# 架构来源：fllesser/nonebot-plugin-parser (MIT) —— @handle 正则注册 + 统一 result 模型；
# 这里去掉 Alconna/uninfo 依赖，改用本项目标准 on_message + bot.send 发送。
from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import time

from nonebot import on_message
from nonebot.adapters.onebot.v11 import Bot, MessageEvent, Message, MessageSegment
from nonebot.log import logger

from .. import history
from ..config import Config
# AI 模式（2026-10-19）：本模块也是独立 matcher，与主流程并行收消息；
# 会话开着大模型陪聊时视频链接归 AI 回，避免解析卡 + AI 回复双重刷屏
# （与 game_tools / 主流程 _route 的豁免口径一致）。
from ..ai_mode import ai_mode_store, event_scope
from .base import BaseParser, VideoParseResult, fmt_duration

# 触发各平台解析器子类注册（import 即收集到 BaseParser._registry）
from . import bilibili, douyin, kuaishou, xiaohongshu  # noqa: F401,E402
from .bilibili import BilibiliParser  # noqa: F401
from .douyin import DouyinParser  # noqa: F401
from .kuaishou import KuaishouParser  # noqa: F401
from .xiaohongshu import XiaohongshuParser  # noqa: F401

# 消息文本里的 URL（排除中文与常见中文标点，避免截断）
URL_RE = re.compile(r"""https?://[^\s一-鿿，。、！？；："''（）【】\]]+""")
# 手机 QQ 的 B站分享卡片偶尔使用 bilibili:// 私有协议（而非 http 链接）
BILI_PRIVATE_RE = re.compile(r"bilibili://(?:video|bangumi|play)/?(?:BV[0-9A-Za-z]+|av\d+)", re.I)


def _collect_bili_urls(obj, out: list[str]) -> None:
    """递归从 json/xml 卡片结构中收集所有 bilibili 链接（http 短链/网页链/私有协议）。"""
    if isinstance(obj, str):
        out.extend(URL_RE.findall(obj))
        out.extend(BILI_PRIVATE_RE.findall(obj))
    elif isinstance(obj, dict):
        for v in obj.values():
            _collect_bili_urls(v, out)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            _collect_bili_urls(v, out)


_matcher = None


def _sweep_media(dir_path: str, ttl: int) -> None:
    """清掉落地目录里超过 ttl 的旧片（下一次发片时顺手做，不另起定时任务）。"""
    cutoff = time.time() - max(60, ttl)
    try:
        for name in os.listdir(dir_path):
            p = os.path.join(dir_path, name)
            try:
                if os.path.isfile(p) and os.path.getmtime(p) < cutoff:
                    os.remove(p)
            except OSError:
                continue
    except OSError as e:  # noqa: BLE001 — 清理失败绝不能挡发片
        logger.warning(f"video_media_sweep_failed dir={dir_path} err={e}")


def _stage_for_protocol(path: str) -> str:
    """把下载好的成片搬到协议端读得到的路径，返回要填进视频段的 file 值。

    同机部署（VIDEO_PARSE_PROTO_MEDIA_DIR/_SEEN 都留空）→ 原样返回绝对路径。
    双容器部署（SnowLuma）→ 拷贝到 bridge 侧 PROTO_MEDIA_DIR，返回协议端
    视角的 PROTO_MEDIA_SEEN/<name>（同一份宿主目录的两个挂载点）。
    配置只设一半属于配置错误：显式 raise，不猜、不降级。
    """
    src_dir = getattr(Config, "VIDEO_PARSE_PROTO_MEDIA_DIR", "") or ""
    seen_dir = getattr(Config, "VIDEO_PARSE_PROTO_MEDIA_SEEN", "") or ""
    if not src_dir and not seen_dir:
        return path
    if not (src_dir and seen_dir):
        raise RuntimeError(
            "VIDEO_PARSE_PROTO_MEDIA_DIR 与 VIDEO_PARSE_PROTO_MEDIA_SEEN 必须同时设置"
            f"（当前 {src_dir!r} / {seen_dir!r}）"
        )
    os.makedirs(src_dir, exist_ok=True)
    _sweep_media(src_dir, getattr(Config, "VIDEO_PARSE_PROTO_MEDIA_TTL", 7200))
    name = f"{int(time.time())}_{os.path.basename(path)}"
    dst = os.path.join(src_dir, name)
    shutil.copyfile(path, dst)
    # 协议端是另一个容器的另一个用户，显式给读权限
    os.chmod(dst, 0o644)
    logger.info(f"video_stage_ok src={path} seen={seen_dir}/{name} size={os.path.getsize(dst)}")
    return os.path.join(seen_dir, name)


def register():
    """在 qq_bridge 的 register() 里调用，注册消息监听。幂等。"""
    global _matcher
    if _matcher is not None:
        return
    _matcher = on_message(priority=12, block=False)
    _matcher.handle()(_handle)
    names = ", ".join(sorted(p.platform.display_name for p in BaseParser.get_parser_classes()))
    logger.info(f"video_parse registered, platforms={names}")


async def _handle(bot: Bot, event: MessageEvent) -> None:
    if not Config.VIDEO_PARSE_ENABLED:
        return

    scene = getattr(event, "message_type", "private")
    if scene == "group":
        if not Config.VIDEO_PARSE_GROUP_AUTO:
            return
    else:
        owners = Config.VIDEO_PARSE_OWNERS
        if owners and str(event.user_id) not in owners:
            return

    # AI 模式让位（2026-10-19）：该会话开着大模型陪聊 → 链接也交给 AI 回，
    # 本模块静默跳过，杜绝「解析卡 + AI 回复」双重消息
    _scope = event_scope(event)
    if ai_mode_store.is_on(_scope):
        logger.info(f"video_parse_ai_mode_skip scope={_scope}")
        return

    # 从纯文本 + QQ 小程序/图文卡片（json/xml 段）中提取链接。
    # B站分享卡片是 json 段：data.data 是 JSON 字符串，链接在
    # meta.detail_1.qqdocurl（小程序）或 meta.news.jumpUrl（图文），通常是 b23.tv 短链。
    sources: list[str] = []
    plain = event.get_plaintext()
    if plain:
        sources.append(plain)
    for seg in event.message:
        if seg.type in ("json", "xml"):
            payload = seg.data.get("data")
            parsed = payload
            if isinstance(payload, str):
                try:
                    parsed = json.loads(payload)
                except Exception:
                    parsed = payload  # 退化：当作纯文本扫描
            # 优先取小程序/图文卡片的标准链接字段（多为 b23.tv 短链或 bilibili.com 链接）
            if isinstance(parsed, dict):
                meta = parsed.get("meta")
                if isinstance(meta, dict):
                    detail = meta.get("detail_1") or {}
                    news = meta.get("news") or {}
                    for key in ("qqdocurl", "url", "jumpUrl", "prefixurl"):
                        for node in (detail, news):
                            if isinstance(node, dict) and isinstance(node.get(key), str):
                                sources.append(node[key])
            # 兜底：递归扫描整个结构的任何字符串，抓出所有 bilibili 链接
            _collect_bili_urls(parsed, sources)

    raw_text = " ".join(sources)
    urls = URL_RE.findall(raw_text)
    urls += BILI_PRIVATE_RE.findall(raw_text)
    # bilibili:// 私有协议 → 还原成可解析的 http 链接
    norm: list[str] = []
    for u in urls:
        if u.startswith("bilibili://"):
            m = re.search(r"(BV[0-9A-Za-z]+|av\d+)", u, re.I)
            if m:
                norm.append("https://www.bilibili.com/video/" + m.group(0))
        else:
            norm.append(u)
    urls = norm
    # 去重，保持顺序
    urls = list(dict.fromkeys(urls))

    logger.info(f"video_parse_handle scene={scene} user={event.user_id} urls={urls} raw_len={len(raw_text)}")
    if not urls:
        for seg in event.message:
            if seg.type in ("json", "xml"):
                d = seg.data.get("data")
                logger.warning(f"video_parse_no_url seg_type={seg.type} data_type={type(d).__name__} data_repr={repr(d)[:2000]}")
        logger.warning(f"video_parse_no_url plaintext={repr(plain)}")
        return

    for url in urls:
        result = await _try_parse_one(url)
        if result:
            card = render_card(result)
            try:
                sent = await bot.send(event, Message(card))
            except Exception as e:  # noqa: BLE001 — 发送失败绝不拖垮消息循环
                logger.error(f"video_parse_send_failed: {type(e).__name__}: {e}")
                sent = None
            # 落盘：管理页聊天记录里能看到机器人回了什么
            await _record_out(event, card, sent)
            # 统一走各平台 download_video 下载并发送视频（失败/不支持则只发文字卡）
            # 时长闸（2026-11-14 用户要求）：「视频超过10分钟就不发了，只发文本解析结果」。
            # 放在下载之前判定：超长片连下载都省掉，不白占带宽和落盘。
            # 时长拿不到（None/0）时无法判定，按「不拦」处理并留痕，绝不瞎猜。
            max_min = int(getattr(Config, "VIDEO_PARSE_MAX_MINUTES", 10) or 0)
            dur = int(getattr(result, "duration", 0) or 0)
            skip_video = bool(max_min > 0 and dur > max_min * 60)
            if skip_video:
                logger.info(
                    f"video_too_long_skip platform={result.platform} duration={dur}s limit={max_min * 60}s")
            elif max_min > 0 and dur <= 0:
                logger.info(f"video_duration_unknown platform={result.platform}（不拦，照常发）")
            parser_cls = BaseParser.get_parser_by_name(result.platform)
            if parser_cls is not None and not skip_video:
                # 平台没重写 download_video = 有意只发元数据卡片（图片笔记等），
                # 这种情况不该报「视频没下下来」。
                supports_video = parser_cls.download_video is not BaseParser.download_video
                notice_mid = 0
                try:
                    # 下载偶发 DNS/连接抖动（实测 "ConnectError: [Errno -5] No address
                    # associated with hostname"，重试一次即成功）。同一条路重试一次不是
                    # 换备选实现，只是把网络抖动挡掉；第二次仍失败就如实告诉用户。
                    path = None
                    for attempt in ((1, 2) if supports_video else (1,)):
                        path = await parser_cls().download_video(result)
                        if path and os.path.exists(path):
                            break
                        logger.warning(
                            f"video_download_miss platform={result.platform} attempt={attempt}")
                        if attempt == 1:
                            await asyncio.sleep(1.5)
                    if path and os.path.exists(path):
                        size = os.path.getsize(path)
                        limit = max(1, int(getattr(Config, "VIDEO_PARSE_MAX_MB", 200))) * 1024 * 1024
                        logger.info(
                            f"video_download_ok platform={result.platform} size={size} limit={limit} path={path}")
                        if size > limit:
                            # 超限**必须说话**：原来只写一行 warning 就沉默，
                            # 用户侧表现为「视频没解析出来」（2026-11-14 实况：
                            # 一条 78.8MB 的片子被 50MB 老上限静默丢弃）。
                            logger.warning(
                                f"video_too_large_skip size={size} limit={limit}")
                            await _say(bot, event,
                                       "视频有 %.1fMB，超过 %.0fMB 上限，这条发不了（卡片在上面）。"
                                       % (size / 1048576, limit / 1048576))
                        else:
                            # 协议端在另一个容器里，本地绝对路径它读不到（ENOENT），
                            # 先搬到双方都能看见的目录，再发协议端视角的路径。
                            notice_mid = 0
                            if size > 30 * 1024 * 1024:
                                # 79MB 首次上传实测 ~150s（同一支重传走秒传只要几秒），
                                # 先给个话，别让人干等；**发完立刻撤回**，不留废话
                                # （用户 2026-11-14：「这种发送中的提示，视频发出后就
                                # 自动撤回，别占地方，这属于无意义的聊天对话」）。
                                notice_mid = await _say(
                                    bot, event, "视频 %.1fMB，正在上传，稍等一下。" % (size / 1048576))
                            seg_file = await asyncio.to_thread(_stage_for_protocol, path)
                            # _timeout：22MB 上传就要 30s+，nonebot 默认 30s 会抛
                            # "WebSocket call api send_msg timeout"——视频其实已经发成功，
                            # 却被判失败再补一句「发不出去」（2026-11-14 实况），必须放宽。
                            sent_v = await bot.send(
                                event, Message(MessageSegment.video(file=seg_file)), _timeout=600)
                            await _record_out(event, f"[CQ:video,file={seg_file}]", sent_v)
                            logger.info(f"video_send_ok platform={result.platform} path={seg_file}")
                            if notice_mid:
                                await _recall(bot, notice_mid)
                    else:
                        # 下载彻底失败也要说话——沉默是用户最不能接受的
                        if supports_video:
                            logger.error(f"video_download_failed platform={result.platform}")
                            await _say(bot, event, "视频这次没下下来（网络或平台风控），重发一次卡片我再试。")
                        else:
                            logger.info(f"video_not_supported platform={result.platform} (只发卡片)")
                except Exception as e:  # noqa: BLE001
                    logger.error(f"video_send_failed platform={result.platform} error={type(e).__name__}: {e}")
                    # 先把「正在上传」收回，再报失败，避免两条互相矛盾的废话
                    if notice_mid:
                        await _recall(bot, notice_mid)
                    # 超时是**不确定**状态：协议端可能已经把视频发出去了（实测过一次），
                    # 所以措辞要留余地，不能一口咬定「没发出去」。
                    if "timeout" in str(e).lower():
                        await _say(bot, event, "视频上传超时了，没收到的话重发一次卡片我再试。")
                    else:
                        await _say(bot, event, "视频没发出去（%s），重发一次卡片我再试。"
                                   % type(e).__name__)
            break  # 一条消息只回一个解析卡片，避免刷屏


async def _say(bot: Bot, event: MessageEvent, text: str) -> int:
    """补一句说明（超限/上传中），返回 message_id 供后续撤回；失败只记日志。"""
    try:
        sent = await bot.send(event, Message(MessageSegment.text(text)))
        await _record_out(event, text, sent)
        return int(sent.get("message_id") or 0) if isinstance(sent, dict) else 0
    except Exception as e:  # noqa: BLE001
        logger.warning(f"video_parse_say_failed: {type(e).__name__}: {e}")
        return 0


async def _recall(bot: Bot, message_id: int) -> None:
    """撤回自己发的过渡性提示（「正在上传」这类占地方的废话）。失败无所谓。"""
    try:
        await bot.delete_msg(message_id=int(message_id))
        logger.info(f"video_notice_recalled message_id={message_id}")
    except Exception as e:  # noqa: BLE001 — 撤回失败不影响已发出的视频
        logger.warning(f"video_notice_recall_failed message_id={message_id} "
                       f"error={type(e).__name__}: {e}")


async def _record_out(event: MessageEvent, raw: str, sent=None) -> None:
    """把机器人自己发出的消息写进历史（与入站同一套 sqlite），管理页聊天记录可见。"""
    try:
        mid = 0
        if isinstance(sent, dict):
            mid = int(sent.get("message_id") or 0)
        elif isinstance(sent, int):
            mid = sent
        ctx = {
            "group_id": int(getattr(event, "group_id", 0) or 0),
            "user_id": int(getattr(event, "user_id", 0) or 0),
        }
        self_id = str(getattr(event, "self_id", "") or "")
        await asyncio.to_thread(
            history.record_outgoing, ctx, raw, self_id, mid
        )
    except Exception as e:  # noqa: BLE001 — 旁路能力，失败不影响主流程
        logger.warning(f"video_parse_history_failed: {type(e).__name__}: {e}")


async def _try_parse_one(url: str) -> VideoParseResult | None:
    for cls in BaseParser.get_parser_classes():
        try:
            keyword, m = cls.search_url(url)
        except Exception:
            continue
        try:
            return await cls().parse(keyword, m)
        except Exception as e:  # noqa: BLE001
            # loguru 用 {} 占位，原来的 "%s" 不会格式化（线上只看到字面量 %s），改 f-string。
            logger.error("video_parse_failed platform={}: {}".format(
                getattr(cls.platform, "name", "?"), e))
            continue
    return None


def render_card(r: VideoParseResult) -> str:
    lines = [f"【{r.platform_display}】"]
    if r.title:
        lines.append(f"标题：{r.title}")
    if r.author_name:
        a = f"UP：{r.author_name}"
        if r.author_id:
            a += f"（{r.author_id}）"
        lines.append(a)
    if r.category:
        lines.append(f"分区：{r.category}")
    if r.duration:
        d = fmt_duration(r.duration)
        if d:
            lines.append(f"时长：{d}")
    if r.stats:
        lines.append("  ".join(f"{k} {v}" for k, v in r.stats.items()))
    if r.text:
        t = (r.text or "").strip()
        if t:
            lines.append(f"简介：{t[:200]}")
    if r.url:
        lines.append(r.url)

    msg = "\n".join(lines)
    if r.cover_url:
        msg = f"[CQ:image,file={r.cover_url}]\n" + msg
    return msg
