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


async def send_card_via_channel(ctx: dict, data: bytes, card_key: str) -> dict:
    """用官方通道直接发一张「图片+按钮」卡片（供出图 handler 与按钮回调共用）。

    ``ctx`` 必须含完整字段（openid/reply_ref），由调用方负责 attach 到 ContextVar
    （:func:`attach_official_ctx`）。返回 OfficialChannel.send 的结果 dict。
    本函数不吞异常——失败由调用方降级（图绝不能因按钮丢）。
    """
    from src.plugins.qq_bridge.channel.base import Outgoing  # noqa: PLC0415
    from src.plugins.qq_bridge.channel import OFFICIAL, get_channel  # noqa: PLC0415

    kcmd = DOUPO_CARD_PREFIX + str(card_key)
    ctx["keyboard_cmd"] = kcmd
    return await get_channel(OFFICIAL).send(ctx, Outgoing(images=[data], keyboard_cmd=kcmd))


async def finish_card(matcher: Any, event: Any, data: bytes, card_key: str,
                      file_name: str = "doupo.png",
                      page: int | None = None,
                      total_pages: int | None = None) -> None:
    """出一张斗破/修仙卡片；官方通道在卡片下方挂对应功能按钮，NapCat 行为不变。

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
    if not is_official(raw):
        # NapCat：完全保持原行为（图片段直接 finish）
        await matcher.finish(image_seg(event, data, file_name))
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
        ret = await send_card_via_channel(ctx, data, effective_key)
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
        # 降级：走 NoneBot 原生发送纯图段（无按钮，但绝不丢图）。
        await matcher.finish(image_seg(event, data, file_name))


async def finish_text(matcher: Any, event: Any, text: str,
                      file_name: str = "") -> None:
    """发一条纯文本回复；官方通道若本回合有竞猜答题则挂选项按钮。

    ## 为什么需要（2026-11-16 有奖竞猜按钮化）
    机遇文本拼在战斗/炼丹等命令的返回文本里，原路径 ``matcher.finish(text)``
    走 NoneBot 原生 ``bot.send``，拿不到 ``OfficialChannel.send`` 的 keyboard
    注入能力。命令层在触发竞猜时把按钮 token 写进 ContextVar（见
    ``doupo.quiz_session.arm_button``），本函数读取它：

    * 官方通道 + 有 token：normalize ctx → ``OfficialChannel.send`` 发文本 +
      ``keyboard_cmd = "doupo:quiz:<token>"`` → 派生 4 个选项按钮，与文本同条下发
      （msg_type=2 markdown + keyboard）；空 finish 仅结束 handler。
    * NapCat / 无 token：保持原生 ``matcher.finish(text)`` 原样，零行为变化。
    * 任何异常降级原生文本，绝不因按钮丢回复。

    ``event`` 可传 EvView 或原始事件；``text`` 已是完整回复文本。
    """
    raw = event.raw if isinstance(event, EvView) else event
    if not is_official(raw):
        await matcher.finish(text)
        return

    # 只有本回合 arm 了竞猜按钮才走官方通道；无按钮保持原生发送，减少试探面。
    try:
        from doupo import quiz_session as qs  # noqa: PLC0415 — 包内模块
        token = qs.take_button()
    except Exception:  # noqa: BLE001
        token = ""
    if not token:
        await matcher.finish(text)
        return

    from src.plugins.qq_bridge.channel.base import (  # noqa: PLC0415
        attach_official_ctx, detach_official_ctx, Outgoing,
    )
    from src.plugins.qq_bridge.channel import OFFICIAL, get_channel  # noqa: PLC0415
    from src.plugins.qq_bridge.channel import doupo_buttons as db  # noqa: PLC0415
    from src.plugins.qq_bridge.channel.qq_keyboard import (  # noqa: PLC0415
        DOUPO_QUIZ_PREFIX,
    )

    # 把选项文字登记到发送层 ContextVar（buttons_for 同 task 读取生成按钮）。
    quiz = None
    try:
        from doupo import quiz_session as qs  # noqa: PLC0415
        quiz = qs.STORE.peek_by_token(token)
    except Exception:  # noqa: BLE001
        quiz = None

    ctx_token = None
    quiz_ctx_token = None
    sent = False
    try:
        if quiz is None:
            raise RuntimeError("quiz_session_not_found")
        quiz_ctx_token = db.arm_quiz(token, quiz.options)
        ch = get_channel(OFFICIAL)
        ctx = await ch.normalize(raw, self_id=str(getattr(raw, "self_id", "") or ""))
        if ctx is None:
            raise RuntimeError("official_normalize_returned_none")
        ctx_token = attach_official_ctx(ctx)
        kcmd = DOUPO_QUIZ_PREFIX + token
        ret = await ch.send(ctx, Outgoing(text=text, keyboard_cmd=kcmd))
        sent = isinstance(ret, dict) and bool(ret.get("ok"))
    except Exception as e:  # noqa: BLE001 — 按钮增强失败不能丢文本
        import logging  # noqa: PLC0415

        logging.getLogger("qq_bridge.doupo").warning(
            "finish_text 官方通道发送失败，降级原生文本：%s: %s", type(e).__name__, e)
    finally:
        if ctx_token is not None:
            detach_official_ctx(ctx_token)
        if quiz_ctx_token is not None:
            db.disarm_quiz(quiz_ctx_token)

    if sent:
        await matcher.finish()
    else:
        await matcher.finish(text)
