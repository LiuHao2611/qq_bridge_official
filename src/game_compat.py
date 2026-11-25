# -*- coding: utf-8 -*-
"""斗破 / 修仙 双通道兼容层（NapCat OneBot V11 + QQ 官方）。

## 为什么需要这一层（2026-11-16 线上故障根因）

斗破/修仙插件最初按 NapCat(OneBot V11) 写死：

  1. handler 注解 ``event: MessageEvent``（onebot 具体类型）。QQ 官方事件
     (``GroupMessageCreateEvent``) 在 NoneBot **依赖注入阶段**抛 TypeMisMatch
     (SkippedException) → handler 被静默跳过 → 日志只打
     ``Matcher(...) running complete``、没有任何发送记录、也没有异常栈。
     线上表现：用户发「斗破菜单」，机器人完全不回。
  2. 发图用 onebot 的 ``MessageSegment.image(bytes)``。QQ 官方 Bot 的
     ``_prepare_message`` 不认这个段类型（官方富媒体必须走
     ``MessageSegment.file_image(bytes, file_name=...)`` + 素材上传）。
  3. ``_mode_is`` 的 rule 注解同为 OneBot 具体类型 → 官方事件在 rule 阶段
     就 TypeMisMatch → ``False``，所有带 rule 的口令（除菜单/入道外全部）
     在官方通道永不响应。

## 本模块提供什么

* :class:`EvView` —— 把两种事件统一成斗破/修仙代码原本期望的「OneBot 形状」
  （``user_id`` / ``group_id`` / ``self_id`` / ``message`` / ``sender`` /
  ``get_plaintext()``），业务代码只多一行 ``event = view(event)``。
* :func:`view` —— 幂等包装：已是 EvView 直接返回。
* :func:`image_seg` —— 按通道构造图片段（官方 file_image / NapCat image）。
* :func:`is_official` / :func:`uid` / :func:`gid` —— 轻量取值工具。

## 身份说明（必须知悉）

官方通道身份是 **openid**（群 ``group_openid``、人 ``member_openid``），
与 NapCat 的 QQ 号不是同一命名空间，**无法互通**。因此官方通道下玩家存档
是独立新档（key=openid），NapCat 的老档不会自动继承——这是平台限制，
不是 bug。

## 边界

本文件是官方副本（qq_bridge_official）的专属适配层；原件 qq_bridge 不引用它。
"""
from __future__ import annotations

from typing import Any

#: 事件基类。运行时取 nonebot 的 ``Event``；斗破/修仙的单测会用
#: ``_stub_nonebot()`` 只桩 ``nonebot.adapters.onebot.v11``，没有 ``Event``
#: 属性——此时回退 object，保证「纯业务单测」不被适配器细节绑死。
try:  # pragma: no cover - 取决于运行环境
    from nonebot.adapters import Event as Event  # noqa: PLC0414
except Exception:  # noqa: BLE001 — 测试桩/极简环境
    Event = object  # type: ignore[assignment,misc]


def is_official(event: Any) -> bool:
    """事件是否来自 QQ 官方适配器（nonebot.adapters.qq）。

    用模块名前缀判断而非 isinstance，避免在官方适配器未安装时（NapCat 单跑）
    顶层 import 失败。
    """
    cls = type(event)
    return cls.__module__.startswith("nonebot.adapters.qq")


def uid(event: Any) -> str:
    """取发送者 id：官方=openid，NapCat=QQ 号。统一返回 str。"""
    if is_official(event):
        author = getattr(event, "author", None)
        for attr in ("member_openid", "user_openid", "id"):
            v = getattr(author, attr, None) if author is not None else None
            if v:
                return str(v)
        return ""
    return str(getattr(event, "user_id", "") or "")


def gid(event: Any) -> str | None:
    """取群 id：官方=group_openid，NapCat=group_id。私聊返回 None。"""
    if is_official(event):
        v = getattr(event, "group_openid", None)
        return str(v) if v else None
    v = getattr(event, "group_id", None)
    return str(v) if v else None


class _AtSeg:
    """把官方 mention 适配成 OneBot ``at`` 段的形状（只有 type/data）。"""

    __slots__ = ("type", "data")

    def __init__(self, qq: str):
        self.type = "at"
        self.data = {"qq": qq}


class _Sender:
    """把官方 author 适配成 OneBot sender（斗破只读 card / nickname）。"""

    __slots__ = ("card", "nickname")

    def __init__(self, card: str = "", nickname: str = ""):
        self.card = card
        self.nickname = nickname


class EvView:
    """统一事件视图：两种通道都长成斗破/修仙代码原本期望的样子。

    只读代理，不改原始事件；``raw`` 保留原始对象以便排障。
    """

    __slots__ = ("raw", "official")

    def __init__(self, raw: Any):
        self.raw = raw
        self.official = is_official(raw)

    # ---- 身份 ----
    @property
    def user_id(self) -> str:
        return uid(self.raw)

    @property
    def group_id(self) -> str | None:
        return gid(self.raw)

    @property
    def self_id(self) -> str:
        return str(getattr(self.raw, "self_id", "") or "")

    # ---- 文本 ----
    def get_plaintext(self) -> str:
        try:
            return self.raw.get_plaintext()
        except Exception:  # noqa: BLE001 — 任何协议差异都不该炸业务
            return str(getattr(self.raw, "content", "") or "")

    def get_message(self) -> Any:
        try:
            return self.raw.get_message()
        except Exception:  # noqa: BLE001
            return None

    # ---- @ 对象（斗破「抢 / 送」用）----
    @property
    def message(self) -> list:
        """返回 OneBot 形状的段列表。

        NapCat：原样返回原始 Message 段列表。
        官方：把 ``mentions`` 适配成 ``[{"type":"at","data":{"qq":openid}}]``，
        跳过机器人自己（``bot``）与发送者本人（``is_you``）。
        """
        if not self.official:
            raw_msg = getattr(self.raw, "message", None)
            return list(raw_msg) if raw_msg else []
        out: list = []
        me = self.user_id
        for m in (getattr(self.raw, "mentions", None) or []):
            if getattr(m, "bot", False) or getattr(m, "is_you", False):
                continue
            openid = getattr(m, "member_openid", None) or getattr(m, "id", None)
            if openid and str(openid) != me:
                out.append(_AtSeg(str(openid)))
        return out

    # ---- 昵称 ----
    @property
    def sender(self) -> Any:
        if not self.official:
            return getattr(self.raw, "sender", None)
        author = getattr(self.raw, "author", None)
        name = str(getattr(author, "username", "") or "")
        return _Sender(card=name, nickname=name) if name else None


def view(event: Any) -> EvView:
    """幂等包装：已是 EvView 直接返回，避免重复代理。"""
    return event if isinstance(event, EvView) else EvView(event)


def image_seg(event: Any, data: bytes, file_name: str = "doupo.png") -> Any:
    """按通道构造图片消息段。

    官方：``file_image`` 必须带 ``file_name``，否则适配器会走 URL 模式
    （本地图必然失败）——见 channel/official.py 文件头 1.7.2 事实记录。
    NapCat：传统 ``image`` 段。
    """
    raw = event.raw if isinstance(event, EvView) else event
    if is_official(raw):
        from nonebot.adapters.qq import MessageSegment as QQS  # noqa: PLC0415

        return QQS.file_image(data, file_name=file_name)
    from nonebot.adapters.onebot.v11 import MessageSegment  # noqa: PLC0415

    return MessageSegment.image(data)


#: 斗破按钮哨兵前缀（与 channel/qq_keyboard.py 同源；此处复制常量避免 game_compat
#: 顶层 import channel 触发 nonebot 依赖）。出图 handler 用它告诉官方发送层
#: 「这张卡该挂哪组按钮」——``keyboard_cmd = "doupo:" + card_key``。
DOUPO_CARD_PREFIX = "doupo:"


async def send_card_via_channel(ctx: dict, data: bytes | list[bytes],
                                card_key: str) -> dict:
    """用官方通道直接发一张「图片+按钮」卡片（供出图 handler 与按钮回调共用）。

    ``data`` 可以是单张 bytes，也可以是**多页** ``list[bytes]`` —— 多页会由
    ``OfficialChannel.send`` 的合并逻辑内嵌进**同一条** markdown 消息
    （``_build_merged_card_segment`` 本来就遍历 ``image_datas``），按钮仍挂在那条
    消息上：既满足「一处输出 = 一条消息」，也满足 2026-11-19 的「超长就分页」。

    ``ctx`` 必须含完整字段（openid/reply_ref），由调用方负责 attach 到 ContextVar
    （:func:`attach_official_ctx`）。返回 OfficialChannel.send 的结果 dict。
    本函数不吞异常——失败由调用方降级（图绝不能因按钮丢）。
    """
    from src.plugins.qq_bridge.channel.base import Outgoing  # noqa: PLC0415
    from src.plugins.qq_bridge.channel import OFFICIAL, get_channel  # noqa: PLC0415

    images = [d for d in (data if isinstance(data, (list, tuple)) else [data]) if d]
    kcmd = DOUPO_CARD_PREFIX + str(card_key)
    ctx["keyboard_cmd"] = kcmd

    # 2026-11-18 玩家自定义每张卡底部按钮：读玩家存档该卡配置，arm 进
    # 按钮派生层（命中则替换固定按钮）。菜单卡禁止自定义，由 custom_buttons
    # 的 card_key_valid 守；读档失败只当无自定义（绝不阻断出图）。
    from src.plugins.qq_bridge.channel import doupo_buttons as db  # noqa: PLC0415

    custom_token = None
    try:
        from src.plugins.doupo.custom_buttons import (  # noqa: PLC0415
            card_key_valid, get_buttons,
        )
        from src.plugins.doupo.store import STORE as _DOUPO_STORE  # noqa: PLC0415

        ck = str(card_key)
        if card_key_valid(ck):
            user_key = str(ctx.get("user_key") or "")
            openid = user_key.split(":")[-1] if ":" in user_key else user_key
            if openid:
                await _DOUPO_STORE.load()
                p = await _DOUPO_STORE.get_player(openid)
                if p:
                    my = get_buttons(p, ck)
                    if my:
                        custom_token = db.arm_custom(my, ck)
    except Exception as e:  # noqa: BLE001 — 按钮是增强项，读档失败绝不阻断出图
        import logging  # noqa: PLC0415

        logging.getLogger("qq_bridge.doupo").warning(
            "custom_buttons_arm_failed card=%s: %s: %s", card_key, type(e).__name__, e)

    try:
        return await get_channel(OFFICIAL).send(ctx, Outgoing(images=images, keyboard_cmd=kcmd))
    finally:
        if custom_token is not None:
            db.disarm_custom(custom_token)


async def finish_card(matcher: Any, event: Any, data: bytes | list[bytes], card_key: str,
                      file_name: str = "doupo.png",
                      page: int | None = None,
                      total_pages: int | None = None) -> None:
    """出一张斗破/修仙卡片；官方通道在卡片下方挂对应功能按钮，NapCat 行为不变。

    ``data`` 支持 ``list[bytes]``（2026-11-19 战斗回看「超长就分页」）：官方通道
    内嵌成**一条** markdown 多图消息（按钮照挂），NapCat 则一条消息多个图片段。
    单页传 bytes，行为与以前逐字节一致。

    ## 为什么需要这一层（2026-11-16）

    斗破出图原本一律 ``await matcher.finish(image_seg(event, png))``——走 NoneBot
    原生 ``bot.send``，**绕过** ``OfficialChannel.send``，因而拿不到该发送层注入的
    按钮键盘（``official.py::send`` 从 ``keyboard_cmd`` → ``buttons_for`` 派生按钮）。
    要让用户要给每张卡配的按钮真正生效，必须让官方通道的出图改走
    ``OfficialChannel.send``。

    ## 做法

    * NapCat：保持 ``matcher.finish(image_seg(...))`` 原样，零行为变化；
    * 官方：从原始事件 normalize 出完整 ctx（openid/group_key/reply_ref），
      注入 ``keyboard_cmd = "doupo:<card_key>"`` 并 attach 到 ContextVar，
      调 ``OfficialChannel.send(ctx, Outgoing(images=[data]))``——复用已验证的
      「图片内嵌 markdown + keyboard 一条消息」链路（含素材上传降级、被动回复 seq）；
      最后 ``matcher.finish()``（无参数，仅结束 handler，不再发消息）。
    * 任何异常都降级回 ``matcher.finish(image_seg(...))``（无按钮但绝不丢图），
      并显示记日志（CLOSE-02 禁止静默吞）。

    ``event`` 可传 EvView 或原始事件；``card_key`` ∈ CARD_ACTIONS 的 key
    （menu/panel/skills/roster/faction/flames/shop/bag/rank）。

    ``page`` / ``total_pages``（2026-11-16 炼药卡分页）：多页时 arm 分页状态并把
    按钮切到「上一页/下一页」哨兵；单页或不传则维持普通功能按钮，零行为变化。
    """
    raw = event.raw if isinstance(event, EvView) else event
    datas = [d for d in (data if isinstance(data, (list, tuple)) else [data]) if d]
    if not datas:
        await matcher.finish()
        return
    if not is_official(raw):
        # NapCat：一条消息多个图片段（分页时就是这个路径；单页仍是原来那一段）
        segs = [image_seg(event, d, file_name if i == 0 else "%d_%s" % (i + 1, file_name))
                for i, d in enumerate(datas)]
        await matcher.finish(*segs)
        return

    from src.plugins.qq_bridge.channel.base import (  # noqa: PLC0415
        attach_official_ctx, detach_official_ctx,
    )
    from src.plugins.qq_bridge.channel import OFFICIAL, get_channel  # noqa: PLC0415

    token = None
    page_token = None
    sent_via_channel = False
    try:
        ch = get_channel(OFFICIAL)
        ctx = await ch.normalize(raw, self_id=str(getattr(raw, "self_id", "") or ""))
        if ctx is None:
            raise RuntimeError("official_normalize_returned_none")
        token = attach_official_ctx(ctx)
        # 炼药卡多页：登记本回合分页状态，按钮派生读取后挂「上一页/下一页」。
        effective_key = card_key
        if page is not None and total_pages is not None and int(total_pages) > 1:
            from src.plugins.qq_bridge.channel import doupo_buttons as db  # noqa: PLC0415

            page_token = db.arm_page(int(page), int(total_pages))
            effective_key = "page:%d" % int(page)   # → keyboard_cmd "doupo:page:N"
        ret = await send_card_via_channel(ctx, datas, effective_key)
        if not (isinstance(ret, dict) and ret.get("ok")):
            raise RuntimeError("official_send_not_ok: %s" % (ret.get("reason") if isinstance(ret, dict) else type(ret).__name__))
        sent_via_channel = True
    except Exception as e:  # noqa: BLE001 — 图绝不能因按钮/通道异常而丢失
        import logging  # noqa: PLC0415

        logging.getLogger("qq_bridge.doupo").warning(
            "finish_card 官方通道发送失败，降级纯图片：%s: %s", type(e).__name__, e)
    finally:
        if token is not None:
            detach_official_ctx(token)
        if page_token is not None:
            from src.plugins.qq_bridge.channel import doupo_buttons as db  # noqa: PLC0415

            db.disarm_page(page_token)

    if sent_via_channel:
        # 已由 OfficialChannel.send 发出（图片+按钮一条），空 finish 仅结束 handler。
        await matcher.finish()
    else:
        # 降级：走 NoneBot 原生发送纯图段（无按钮，但绝不丢图；多页同一条消息）。
        segs = [image_seg(event, d, file_name if i == 0 else "%d_%s" % (i + 1, file_name))
                for i, d in enumerate(datas)]
        await matcher.finish(*segs)


# ---------------------------------------------------------------------------
# 官方通道纯文本收口（2026-11-25：「上阵 / 学习 / 锁定 / 主题… 打字全没反应」）
#
# 现象：手打口令**命中了 matcher**、日志有 ``running complete``、无异常栈，但用户端
# 一个字都收不到；同一机器人发的**卡片**（走 OfficialChannel.send）和**按钮回包**
# 每次都正常到达。唯一差别就是发送出口：
#   * finish_card / 按钮派发 → OfficialChannel.send  → 到达 + official_sent 日志
#   * matcher.finish(str)   → NoneBot 原生 bot.send → 静默不到（无回执、无报错）
# 故把官方通道**所有纯文本**统一收口到 OfficialChannel.send：
#   ① 与卡片共用同一套 msg_seq 分配（顺带根治同一 msg_id 双发撞 40054005）；
#   ② 每条文本都有 official_sent / channel_send_failed 日志，下次一查即知；
#   ③ 任何异常仍回退原生发送，绝不比现状更差。
# ---------------------------------------------------------------------------
def _seq_seed(raw: Any) -> int:
    """本次被动回复应使用的 msg_seq（与原生 bot.send 共用 event._reply_seq）。

    原生 ``QQBot.send`` 是 ``event._reply_seq += 1`` 后再下发；通道侧从
    ``reply_ref["msg_seq"]`` 起算。二者若不是同一个计数器，同一 msg_id 下第二条
    必然撞 seq=1 → 平台按去重拒收 40054005（2026-11-24 帮助卡实测）。
    """
    try:
        return int(getattr(raw, "_reply_seq", 0) or 0) + 1
    except Exception:  # noqa: BLE001
        return 1


def _seq_commit(raw: Any, ctx: Any) -> None:
    """把通道实际消耗到的序号写回事件，让后续原生 send 接着往上数。

    直接读 ``reply_ref["msg_seq"]``（与 ``passive_seq.peek_msg_seq`` 同一口径），
    不 import 通道包——避免顶层拉进 httpx 等重依赖（宿主纯标准库要能跑）。
    """
    try:
        ref = (ctx or {}).get("reply_ref") or {}
        raw._reply_seq = max(0, int(ref.get("msg_seq", 1)) - 1)
    except Exception:  # noqa: BLE001 — 序号回写是增强项，失败不影响已发出的消息
        pass


#: 单行短回复的补齐页脚（2026-11-25 第三次修复）。
#:
#: 证据链（`logs/qq_bridge.log` + `service.log` 同一晚）：
#:   * 00:15:29 `kind=text`，内容是 `cmd_loadout()` 的**多行**全文（150 字）→ 用户看到了；
#:   * 00:15:48 `上阵 14`、00:49:42 `上阵 11 14`、00:57:16 `上阵 11 14`，内容都是
#:     **单行**短句（「14」不可上阵…）→ 全部 `official_sent` 成功、客户端一条不见。
#: 即同一机器人同一晚、同一 send 通路：**多行到达、单行消失**（与 msg_type 无关，
#: 当时纯文本还是 msg_type=0，改 markdown 后又出现同样的短句丢失）。
#: 结论：官方客户端对「单行无换行」的正文渲染异常（空白气泡）。故短单行一律补第二行，
#: 既绕开该渲染缺陷，也给玩家一个出口口令。
_SINGLE_LINE_FOOTER = "（发「斗破菜单」看全部口令）"


def pad_single_line(text: str) -> str:
    """把「单行正文」补成多行，规避官方客户端单行正文渲染空白（幂等、可单测）。

    * 已含换行 → 原样返回（多行本来就正常，别动它）；
    * 空白 → 原样返回（空文本由调用方判空）；
    * 单行 → 追加一行 ``_SINGLE_LINE_FOOTER``。
    """
    body = (text or "").rstrip()
    if not (text or "").strip():
        return text
    if "\n" in body:
        return text
    return body + "\n" + _SINGLE_LINE_FOOTER


#: 尖括号 → 全角（视觉几乎无差，但不再被 markdown 当 HTML 标签）。
_ANGLE_MAP = {"<": "＜", ">": "＞"}


def escape_markdown_angles(text: str) -> str:
    """把 ``<x>`` 换成 ``＜x＞``。

    2026-11-25 改 markdown 载体后新增的风险点：斗破文案里有 ``<技名>``（`上阵`
    用法提示）这类尖括号占位，官方 markdown 会按 HTML 标签解析，轻则被吞、重则
    整条 ``40034011 无效 markdown content`` 被拒。换全角后既保留可读性又不会解析。
    """
    return "".join(_ANGLE_MAP.get(ch, ch) for ch in (text or ""))


def prepare_official_text(text: str) -> str:
    """官方文本通道的最终正文准备：先去 markdown 危险尖括号，再补多行。"""
    return pad_single_line(escape_markdown_angles(text))


#: 模式 → 纯文本兜底键盘口令（key 是 `qq_keyboard` 的按钮表 key，不是中文口令）。
#: 2026-11-25 第四轮：官方群「卡片到达、文本不到」的分界是 **有没有 keyboard**，
#: 故纯文本必须与卡片同形（markdown + keyboard 同段）。
_KB_FALLBACKS = {"doupo": "doupo:menu"}

#: 没有按钮表的模式 → 直接造「单按钮键盘」(label, cmd)。
#: 修仙通道层没有按钮表（`xiuxian:menu` 实测返回空），裸 markdown 依旧到不了；
#: 给它一个「修仙菜单」按钮，点一下就是发那条口令，能力上不比纯文本差。
_KB_DIRECT = {"xiuxian": ("修仙菜单", "修仙菜单")}


async def _fallback_kb_cmd(ctx: dict) -> str:
    """按玩家当前模式挑一个兜底键盘口令；没模式/查不到返回空串（宁可不挂）。

    同时把「无按钮表模式」的单按钮配置写进 ``ctx["kb_fallback_btn"]``。
    """
    try:
        # 前缀常量定义在 official.py（`official:u:`），**别猜、别重定义**，否则
        # 前缀一改这里就静默取不到 uid（2026-11-25 首次上线就因为写错 base 而炸）。
        from src.plugins.qq_bridge.channel.official import USER_PREFIX  # noqa: PLC0415
        from src.game_mode import get_mode  # noqa: PLC0415

        uid = str((ctx or {}).get("user_key") or "").replace(USER_PREFIX, "")
        if not uid:
            return ""
        mode = await get_mode(uid)
    except Exception:  # noqa: BLE001 — 兜底键盘是增强项，查不到就不挂
        return ""
    key = str(mode or "")
    direct = _KB_DIRECT.get(key)
    if direct:
        ctx["kb_fallback_btn"] = list(direct)
        return ""
    return _KB_FALLBACKS.get(key, "")


async def _channel_send_text(raw: Any, text: str, keyboard_cmd: str = "") -> bool:
    """把一条纯文本经 ``OfficialChannel.send`` 发出（官方唯一可靠文本出口）。

    :return: True=已发出（调用方只需空 finish 结束 handler）；False=没发出去，
             调用方应回退原生发送。
    """
    from src.plugins.qq_bridge.channel import OFFICIAL, get_channel  # noqa: PLC0415
    from src.plugins.qq_bridge.channel.base import (  # noqa: PLC0415
        Outgoing, attach_official_ctx, detach_official_ctx,
    )

    ch = get_channel(OFFICIAL)
    ctx = await ch.normalize(raw, self_id=str(getattr(raw, "self_id", "") or ""))
    if ctx is None:
        return False
    ref = ctx.get("reply_ref")
    if isinstance(ref, dict):
        ref["msg_seq"] = _seq_seed(raw)
    # 纯文本到达的前提是与卡片同形（markdown + keyboard）：没显式按钮时，给通道一个
    # **兜底键盘口令**（`official.send` 先试文本自身口令，试不出才用这个）。
    if not keyboard_cmd:
        try:
            ctx["kb_fallback"] = await _fallback_kb_cmd(ctx)
        except Exception:  # noqa: BLE001 — 兜底失败就发纯 markdown（不比现状差）
            pass
    token = None
    try:
        token = attach_official_ctx(ctx)
        ret = await ch.send(ctx, Outgoing(text=prepare_official_text(text),
                                          keyboard_cmd=keyboard_cmd or ""))
    finally:
        if token is not None:
            detach_official_ctx(token)
        _seq_commit(raw, ctx)
    return isinstance(ret, dict) and bool(ret.get("ok"))


async def finish_text(matcher: Any, event: Any, text: str,
                      file_name: str = "") -> None:
    """发一条纯文本回复（官方通道统一经 OfficialChannel，NapCat 走原生）。

    ## 两次改动的来龙去脉
    * 2026-11-16（有奖竞猜按钮化）：机遇文本要挂 4 个选项按钮，原生
      ``matcher.finish`` 拿不到 keyboard 注入能力 → 只在**有竞猜 token** 时才走
      通道，其余仍原生。
    * 2026-11-25（「上阵 / 学习 / 锁定 / 主题」打字没反应）：原生纯文本在官方
      通道被平台静默吞掉（无回执无报错），实测只有经通道的文本能到 → 改成
      **官方通道一律走通道**，不再看是否 arm 按钮；异常仍降级原生。

    ``event`` 可传 EvView 或原始事件；``text`` 已是完整回复文本。
    """
    raw = event.raw if isinstance(event, EvView) else event
    if not is_official(raw):
        await matcher.finish(text)
        return

    # 本回合若 arm 了竞猜按钮，把 keyboard_cmd 一起带上（行为不变）。
    try:
        from doupo import quiz_session as qs  # noqa: PLC0415 — 包内模块
        token = qs.take_button()
    except Exception:  # noqa: BLE001
        token = ""
    # 本回合若 arm 了竞猜按钮：登记选项文字并在通道侧派生 4 个按钮（同一条下发）。
    kcmd = ""
    quiz_ctx_token = None
    if token:
        try:
            from src.plugins.qq_bridge.channel import doupo_buttons as db  # noqa: PLC0415
            from src.plugins.qq_bridge.channel.qq_keyboard import (  # noqa: PLC0415
                DOUPO_QUIZ_PREFIX,
            )

            quiz = qs.STORE.peek_by_token(token)
            if quiz is not None:
                quiz_ctx_token = db.arm_quiz(token, quiz.options)
                kcmd = DOUPO_QUIZ_PREFIX + token
        except Exception:  # noqa: BLE001 — 按钮增强失败不影响文本
            quiz_ctx_token = None
            kcmd = ""

    # 2026-11-25 主修复：**一律走通道**（含无按钮的普通文本），原生纯文本到不了用户。
    sent = False
    try:
        sent = await _channel_send_text(raw, text, kcmd)
    except Exception as e:  # noqa: BLE001 — 通道异常不能丢消息
        import logging  # noqa: PLC0415

        logging.getLogger("qq_bridge.doupo").warning(
            "finish_text 官方通道发送失败，降级原生文本：%s: %s", type(e).__name__, e)
    finally:
        if quiz_ctx_token is not None:
            from src.plugins.qq_bridge.channel import doupo_buttons as db  # noqa: PLC0415

            db.disarm_quiz(quiz_ctx_token)

    if sent:
        await matcher.finish()
    else:
        await matcher.finish(text)


# ---------------------------------------------------------------------------
# 原生 finish 的兜底收口（2026-11-25）
# ---------------------------------------------------------------------------
# 斗破/修仙里有 47 处直接用 ``await matcher.finish(<字符串>)``（历史写法）。逐个改
# 成 finish_text 风险大、易漏；改在**唯一出口** QQBot.send 上更稳：字符串消息一律
# 先试通道，失败/非字符串（图片段、Message）原样交给原生实现。补丁幂等、可关。
_BRIDGE_INSTALLED = False


def install_official_text_bridge() -> bool:
    """给官方 ``QQBot.send`` 装「纯文本走通道」补丁（幂等；失败不抛）。

    为什么补在 Bot.send 而不是改 47 个调用点：
      * ``matcher.finish(str)`` 最终必然落到 ``bot.send``，一处收口覆盖全部口令；
      * 图片/富媒体段（``Message``/``MessageSegment``）**不拦截**，仍然走原生，
        出图链路零影响；
      * 通道发送失败自动回退原生，行为上不会比现状更差。

    :return: True=已安装（或本来就在）；False=官方适配器不可用，行为不变。
    """
    global _BRIDGE_INSTALLED
    if _BRIDGE_INSTALLED:
        return True
    try:
        from nonebot.adapters.qq import (  # noqa: PLC0415
            Bot as QQBot, C2CMessageCreateEvent, GroupMessageCreateEvent,
        )
    except Exception:  # noqa: BLE001 — NapCat 单跑 / 适配器缺失
        return False
    if getattr(QQBot.send, "_qq_text_bridge", False):
        _BRIDGE_INSTALLED = True
        return True

    orig_send = QQBot.send

    async def send(self, event, message, **kwargs):  # noqa: ANN001, ANN003
        if (isinstance(message, str) and message.strip()
                and isinstance(event, (GroupMessageCreateEvent, C2CMessageCreateEvent))):
            try:
                if await _channel_send_text(event, message):
                    return None
            except Exception as e:  # noqa: BLE001 — 兜底失败一律回退原生
                import logging  # noqa: PLC0415

                logging.getLogger("qq_bridge.doupo").warning(
                    "官方纯文本通道发送失败，回退原生：%s: %s", type(e).__name__, e)
        return await orig_send(self, message=message, event=event, **kwargs)

    send._qq_text_bridge = True  # type: ignore[attr-defined]
    QQBot.send = send  # type: ignore[method-assign]
    _BRIDGE_INSTALLED = True
    import logging  # noqa: PLC0415

    logging.getLogger("qq_bridge.doupo").info(
        "[qq_bridge] official_text_bridge_installed（官方纯文本统一走通道）")
    return True
