# -*- coding: utf-8 -*-
"""官方通道验收测试（docs/qq_official_bot_migration_plan.md §3.3 / §6.4 / §8）。

验收标准：
  1) 注册表：official / napcat 取得到；未知通道名回退 napcat（且只告警一次）
  2) normalize：群 @、群非 @、私聊 → ctx v2 字段正确；无关事件 → None
  3) send：群走 send_to_group、私聊走 send_to_c2c，并带上被动回复的 msg_id/msg_seq
  4) send：无在线 bot → ok=False（记日志、不抛异常，CLOSE-02）
  5) channel_of_bot：官方 bot → official，其它 → napcat

说明：用假类 + monkeypatch 隔离真实适配器，保证单测不依赖网络与真实凭据
（与项目既有 test_dispatcher / test_taskmgr 的 duck typing 风格一致）。
"""
import asyncio
import time
from types import SimpleNamespace

import pytest

from src.plugins.qq_bridge.channel import NAP_CAT, OFFICIAL, channel_of_bot, get_channel
from src.plugins.qq_bridge.channel import napcat as napcat_mod
from src.plugins.qq_bridge.channel import official as official_mod


# ---------------------------------------------------------------- 假对象
class FakeQQBot:
    """占位：用于 channel_of_bot 的 isinstance 判定。"""


class FakeAuthor:
    def __init__(self, user_openid: str = "", member_openid: str = ""):
        self.user_openid = user_openid
        self.member_openid = member_openid


class FakeC2CEvent:
    def __init__(self, openid: str = "user-openid-1", eid: str = "msg-1"):
        self.author = FakeAuthor(user_openid=openid)
        self.id = eid

    def get_message(self):
        return []


class FakeGroupEvent:
    def __init__(self, group_openid: str = "grp-openid-1",
                 member_openid: str = "member-openid-1", eid: str = "msg-2"):
        self.author = FakeAuthor(member_openid=member_openid)
        self.group_openid = group_openid
        self.id = eid

    def get_message(self):
        return []


class FakeGroupAtEvent(FakeGroupEvent):
    pass


class FakeMessage(list):
    """支持 msg += seg。

    只把 **list** 展平、其余当单个元素追加：这样 Segment 用 tuple/对象表示时，
    消息里每个段仍是独立元素（与真实 Message 语义一致），便于断言。
    """

    def __iadd__(self, other):
        if isinstance(other, list):
            self.extend(other)
        else:
            self.append(other)
        return self

    def __add__(self, other):
        out = FakeMessage(self)
        out.__iadd__(other)
        return out


class FakeSegment:
    @staticmethod
    def text(content: str):
        return ("text", content)

    @staticmethod
    def markdown(content: str):
        # 2026-11-25：官方纯文本改走 markdown(msg_type=2)，假段必须支持
        return ("markdown", content)

    @staticmethod
    def mention_user(openid: str, username: str | None = None):
        return ("mention_user", openid)

    @staticmethod
    def file_image(data, file_name: str | None = None):
        # file_name 必须给：适配器据此走本地上传分支（见 official.py 说明）
        return ("file_image", file_name, len(data))

    @staticmethod
    def file_video(data, file_name: str | None = None):
        return ("file_video", file_name, len(data))

    @staticmethod
    def keyboard(model):
        # 2026-11-25 第四轮：纯文本也要挂键盘（与卡片同形才到得了）
        return ("keyboard", model)


class FakeBot(FakeQQBot):
    """记录发送调用的假 Bot。"""

    def __init__(self):
        self.calls: list = []

    async def send_to_group(self, group_openid, message, msg_id=None, msg_seq=None,
                            event_id=None, msg_ref_id=None):
        self.calls.append(("group", group_openid, list(message), msg_id, msg_seq))
        return SimpleNamespace(id="ret-group-1")

    async def send_to_c2c(self, openid, message, msg_id=None, msg_seq=None,
                          event_id=None, msg_ref_id=None):
        self.calls.append(("c2c", openid, list(message), msg_id, msg_seq))
        return SimpleNamespace(id="ret-c2c-1")


@pytest.fixture
def patched(monkeypatch):
    """把 _import_qq 替换成假类，返回 (module, bot_holder)。"""
    monkeypatch.setattr(
        official_mod,
        "_import_qq",
        lambda: (FakeQQBot, FakeMessage, FakeSegment,
                 FakeC2CEvent, FakeGroupEvent, FakeGroupAtEvent),
    )
    holder = {}

    def _get(_cls):
        return holder.get("bot")

    monkeypatch.setattr(official_mod, "_get_official_bot", _get)
    return holder


@pytest.fixture(autouse=True)
def _clean_limiter():
    """每个用例前后清空日额度计数（单例，避免用例互相污染）。"""
    from src.plugins.qq_bridge.channel.limiter import official_limiter

    official_limiter._count.clear()
    official_limiter._warned.clear()
    yield
    official_limiter._count.clear()
    official_limiter._warned.clear()


def _ctx_of(event, self_id: str = "bot-1"):
    ch = get_channel(OFFICIAL)
    return asyncio.run(ch.normalize(event, self_id=self_id, trace_id="t-1"))


# ---------------------------------------------------------------- 1) 注册表
def test_registry_returns_both_channels():
    assert get_channel(OFFICIAL).name == OFFICIAL
    assert get_channel(NAP_CAT).name == NAP_CAT


def test_registry_unknown_falls_back_to_napcat():
    ch = get_channel("no-such-channel")
    assert isinstance(ch, napcat_mod.NapCatChannel)
    # 幂等：重复取仍是同一个实例（缓存生效）
    assert get_channel(NAP_CAT) is get_channel(NAP_CAT)


# ---------------------------------------------------------------- 2) normalize
def test_normalize_group_at(patched):
    ctx = _ctx_of(FakeGroupAtEvent())
    assert ctx is not None
    assert ctx["channel"] == OFFICIAL
    assert ctx["is_group"] is True
    assert ctx["at_bot"] is True
    assert ctx["group_key"] == "official:g:grp-openid-1"
    assert ctx["user_key"] == "official:u:member-openid-1"
    assert ctx["user_id"] == 0 and ctx["group_id"] == 0   # 官方无 int 型 QQ 号
    # ts 用于判断是否还能被动回复（平台硬限 5 分钟）
    assert ctx["reply_ref"]["msg_id"] == "msg-2"
    assert ctx["reply_ref"]["msg_seq"] == 1
    assert isinstance(ctx["reply_ref"].get("ts"), float)


def test_normalize_group_plain_not_at(patched):
    ctx = _ctx_of(FakeGroupEvent())
    assert ctx["at_bot"] is False
    assert ctx["is_group"] is True


def test_normalize_c2c_private(patched):
    ctx = _ctx_of(FakeC2CEvent())
    assert ctx["is_group"] is False
    assert ctx["group_key"] == ""
    assert ctx["user_key"] == "official:u:user-openid-1"
    assert ctx["reply_ref"]["msg_id"] == "msg-1"


def test_normalize_unknown_event_returns_none(patched):
    assert _ctx_of(SimpleNamespace(id="x")) is None


def test_normalize_missing_openid_returns_none(patched, caplog):
    assert _ctx_of(FakeC2CEvent(openid="")) is None


# ---------------------------------------------------------------- 3) send
def test_send_group_uses_send_to_group(patched):
    bot = FakeBot()
    patched["bot"] = bot
    ctx = _ctx_of(FakeGroupAtEvent())
    from src.plugins.qq_bridge.channel.base import Outgoing

    ret = asyncio.run(get_channel(OFFICIAL).send(ctx, Outgoing(text="你好", at_user=True)))
    assert ret["ok"] is True
    assert len(bot.calls) == 1
    kind, target, msg, msg_id, msg_seq = bot.calls[0]
    assert kind == "group" and target == "grp-openid-1"
    assert msg_id == "msg-2" and msg_seq == 1          # 被动回复引用
    assert ("mention_user", "member-openid-1") in msg   # at_user
    assert ("text", "你好") in msg


def test_plain_text_uses_markdown_not_text(patched):
    """🔴 2026-11-25 回归（用户「我上阵斗技和学习斗技都没反应」）。

    官方群机器人 ``msg_type=0`` 纯文本**平台接受（200 + 回 msg_id）但客户端不下发**：
    日志只有 ``official_sent kind=text``，没有 ``channel_send_failed``，用户端一片空白。
    同一机器人 ``msg_type=2`` markdown 的卡片每次都到 → 普通文本（不带 @）必须发
    markdown 段，否则等于静默丢消息。带 @ 的场景仍用 text 段（markdown 的 @ 语法
    与适配器 mention 段不通用）。
    """
    bot = FakeBot()
    patched["bot"] = bot
    ctx = _ctx_of(FakeGroupAtEvent())
    from src.plugins.qq_bridge.channel.base import Outgoing

    # 1) 普通文本（绝大多数口令回包）：必须是 markdown
    asyncio.run(get_channel(OFFICIAL).send(ctx, Outgoing(text="当前上阵：风灵分形剑[三阶]")))
    msg = bot.calls[-1][2]
    assert ("markdown", "当前上阵：风灵分形剑[三阶]") in msg, \
        "纯文本必须走 markdown（msg_type=0 在官方群聊不下发），实际段：%r" % (msg,)
    assert not any(seg[0] == "text" for seg in msg if isinstance(seg, tuple)), \
        "不得再混入 text 段（会让整条退化成 msg_type=0）"

    # 2) 带 @ 的文本：保持 mention + text（老行为）
    asyncio.run(get_channel(OFFICIAL).send(ctx, Outgoing(text="你好", at_user=True)))
    msg2 = bot.calls[-1][2]
    assert ("mention_user", "member-openid-1") in msg2 and ("text", "你好") in msg2


def test_plain_text_carries_keyboard_from_fallback(patched):
    """🔴 2026-11-25 第四轮回归（用户「发上阵 11 14 没反应」）。

    01:34:53 / 01:35:02 两条**纯 markdown（无键盘）**平台照样 200 + 回 msg_id，
    用户端依旧空白；而同批次 01:35:22 的卡片（markdown+keyboard）正常到达。
    ⇒ 「卡片到、文本不到」的分界是 **有没有 keyboard**。
    故文本段必须能按 ``ctx["kb_fallback"]`` 挂上键盘，且**同一条消息**先 markdown 后
    keyboard（分两条发会退化成独立 keyboard 段，平台 40034011）。
    """
    from src.plugins.qq_bridge.channel import qq_keyboard
    from src.plugins.qq_bridge.channel.base import Outgoing

    seen: list = []
    orig = qq_keyboard.make_keyboard

    class _B:
        label = "斗破菜单"

    def _fake_mk(cmd):
        seen.append(cmd)
        return ("KB-MODEL", [_B()])

    qq_keyboard.make_keyboard = _fake_mk
    try:
        bot = FakeBot()
        patched["bot"] = bot
        ctx = _ctx_of(FakeGroupEvent())
        ctx["kb_fallback"] = "doupo:menu"
        asyncio.run(get_channel(OFFICIAL).send(ctx, Outgoing(text="还没领悟：帝印决")))
        msg = bot.calls[-1][2]
    finally:
        qq_keyboard.make_keyboard = orig

    assert seen == ["doupo:menu"], "必须用 ctx 给的兜底口令建键盘，实际 %r" % seen
    kinds = [seg[0] for seg in msg if isinstance(seg, tuple)]
    assert kinds == ["markdown", "keyboard"], \
        "文本必须一条消息内「markdown 后 keyboard」，实际 %r" % (kinds,)


def test_plain_text_without_fallback_stays_markdown_only(patched):
    """没有兜底口令时不得凭空造键盘（避免给 warframe/私聊塞游戏按钮）。"""
    bot = FakeBot()
    patched["bot"] = bot
    ctx = _ctx_of(FakeGroupEvent())
    from src.plugins.qq_bridge.channel.base import Outgoing

    asyncio.run(get_channel(OFFICIAL).send(ctx, Outgoing(text="纯文字")))
    msg = bot.calls[-1][2]
    kinds = [seg[0] for seg in msg if isinstance(seg, tuple)]
    assert kinds == ["markdown"], "无兜底时只发 markdown，实际 %r" % (kinds,)


def test_text_without_button_table_uses_single_button_fallback(patched):
    """🔴 修仙这类**没有按钮表**的模式：造单按钮兜底键盘，别退回裸 markdown。

    `xiuxian:menu` 实测返回空 → `make_keyboard` 返 None → 文本退化成裸 markdown
    → 又到不了（斗破 01:34:53 同一指纹）。故必须能走单按钮兜底。
    """
    from src.plugins.qq_bridge.channel import qq_keyboard
    from src.plugins.qq_bridge.channel.base import Outgoing

    seen: list = []
    orig_mk, orig_btn = qq_keyboard.make_keyboard, qq_keyboard.make_single_button_keyboard
    try:
        qq_keyboard.make_keyboard = lambda cmd: (None, ())       # 没有按钮表
        qq_keyboard.make_single_button_keyboard = (
            lambda label, cmd: seen.append((label, cmd)) or "KB-FB")
        bot = FakeBot()
        patched["bot"] = bot
        ctx = _ctx_of(FakeGroupEvent())
        ctx["kb_fallback_btn"] = ["修仙菜单", "修仙菜单"]
        asyncio.run(get_channel(OFFICIAL).send(ctx, Outgoing(text="灵石不足")))
        msg = bot.calls[-1][2]
    finally:
        qq_keyboard.make_keyboard = orig_mk
        qq_keyboard.make_single_button_keyboard = orig_btn

    assert seen == [("修仙菜单", "修仙菜单")], "必须用 ctx 的单按钮兜底，实际 %r" % (seen,)
    kinds = [seg[0] for seg in msg if isinstance(seg, tuple)]
    assert kinds == ["markdown", "keyboard"], "仍须一条消息内 markdown+keyboard：%r" % (kinds,)


def test_c2c_text_stays_markdown_only(patched):
    """私聊文本**不许**挂键盘：C2C 对 keyboard 没有实测样本，别为修群聊搭进私聊。

    ⚠️ 必须把 ``make_keyboard`` 换成**返回真模型**的假件——否则走真件时它会因
    （宿主没有 nonebot models）抛异常被发送层吞掉，用例会「假通过」，
    证明不了 is_group 这道门真的在。
    """
    from src.plugins.qq_bridge.channel import qq_keyboard
    from src.plugins.qq_bridge.channel.base import Outgoing

    orig, orig_btn = qq_keyboard.make_keyboard, qq_keyboard.make_single_button_keyboard
    try:
        qq_keyboard.make_keyboard = lambda cmd: ("KB", [])
        qq_keyboard.make_single_button_keyboard = lambda label, cmd: "KB-FB"
        bot = FakeBot()
        patched["bot"] = bot
        ctx = _ctx_of(FakeC2CEvent())
        ctx["kb_fallback"] = "doupo:menu"
        ctx["kb_fallback_btn"] = ["修仙菜单", "修仙菜单"]
        asyncio.run(get_channel(OFFICIAL).send(ctx, Outgoing(text="私聊文本")))
        msg = bot.calls[-1][2]
    finally:
        qq_keyboard.make_keyboard = orig
        qq_keyboard.make_single_button_keyboard = orig_btn
    kinds = [seg[0] for seg in msg if isinstance(seg, tuple)]
    assert kinds == ["markdown"], "私聊只发 markdown，实际 %r" % (kinds,)


def test_consecutive_sends_share_reply_ref_get_increasing_msg_seq(patched):
    """🔴 40054005 回归（用户「紫卡分析没出结果」，2026-11-11）：

    紫卡一次事件内 **send 两次**（排队话术 text → 结果图 image），共享同一个
    ctx/reply_ref。适配器要求同一 msg_id 下第 N 次被动回复 msg_seq==N；旧实现
    ``msg_seq=(i+1)`` 两次都从 1 起 → 平台去重拒收，图渲染成功却发不出。
    本测试用同一 ctx 连发两条 Outgoing，断言 msg_seq 严格 1、2 不重复。
    """
    bot = FakeBot()
    patched["bot"] = bot
    ctx = _ctx_of(FakeGroupEvent())          # reply_ref.msg_seq 初始=1
    from src.plugins.qq_bridge.channel.base import Outgoing

    # 第一次 send：模拟「已加入分析队列」排队话术
    r1 = asyncio.run(get_channel(OFFICIAL).send(ctx, Outgoing(text="已加入分析队列，稍等！")))
    # 第二次 send：模拟「紫卡分析结果图」（共享同一 ctx/reply_ref）
    r2 = asyncio.run(get_channel(OFFICIAL).send(ctx, Outgoing(text="分析结果图")))
    assert r1["ok"] and r2["ok"]
    assert len(bot.calls) == 2
    seqs = [c[4] for c in bot.calls]
    msg_ids = [c[3] for c in bot.calls]
    assert seqs == [1, 2], \
        "紫卡两次 send 的 msg_seq 必须严格递增 [1,2]，拿到 %r（重复=40054005 去重）" % seqs
    assert msg_ids == ["msg-2", "msg-2"], "两次被动回复应引用同一 msg_id"
    assert all(m == "passive" for m in r1["modes"] + r2["modes"])


def test_msg_seq_counter_is_per_reply_ref_not_global(patched):
    """不同事件（不同 reply_ref）的 msg_seq 计数必须互相独立，绝不串号。"""
    bot = FakeBot()
    patched["bot"] = bot
    from src.plugins.qq_bridge.channel.base import Outgoing

    ctx_a = _ctx_of(FakeGroupEvent(eid="msg-A"))
    ctx_b = _ctx_of(FakeGroupEvent(eid="msg-B"))
    asyncio.run(get_channel(OFFICIAL).send(ctx_a, Outgoing(text="a1")))
    asyncio.run(get_channel(OFFICIAL).send(ctx_a, Outgoing(text="a2")))
    asyncio.run(get_channel(OFFICIAL).send(ctx_b, Outgoing(text="b1")))
    seqs = [c[4] for c in bot.calls]
    assert seqs == [1, 2, 1], \
        "msg_seq 必须按 reply_ref 独立计数（A:1,2 / B:1），拿到 %r" % seqs


def test_msg_seq_stops_at_five_then_goes_active(patched):
    """同一 reply_ref 被动回复累计 5 次后，第 6 条必须转主动（不撞平台 seq 上限）。"""
    bot = FakeBot()
    patched["bot"] = bot
    ctx = _ctx_of(FakeGroupEvent())
    ctx["reply_ref"]["ts"] = time.time()   # 确保在被动窗口内
    from src.plugins.qq_bridge.channel.base import Outgoing

    for _ in range(6):
        asyncio.run(get_channel(OFFICIAL).send(ctx, Outgoing(text="x")))
    seqs = [c[4] for c in bot.calls]
    assert seqs[:5] == [1, 2, 3, 4, 5], "前 5 条被动 seq 应为 1..5：%r" % seqs
    assert seqs[5] is None, "第 6 条应转主动（msg_seq=None），不得重复 seq=1：%r" % seqs


def test_send_c2c_uses_send_to_c2c(patched):
    bot = FakeBot()
    patched["bot"] = bot
    ctx = _ctx_of(FakeC2CEvent())
    from src.plugins.qq_bridge.channel.base import Outgoing

    ret = asyncio.run(get_channel(OFFICIAL).send(ctx, Outgoing(text="hi")))
    assert ret["ok"] is True
    kind, target, msg, msg_id, _seq = bot.calls[0]
    assert kind == "c2c" and target == "user-openid-1"
    assert ("mention_user", "user-openid-1") not in msg  # 私聊不 @


def test_send_without_bot_returns_failed(patched):
    patched["bot"] = None
    ctx = _ctx_of(FakeGroupEvent())
    from src.plugins.qq_bridge.channel.base import Outgoing

    ret = asyncio.run(get_channel(OFFICIAL).send(ctx, Outgoing(text="hi")))
    assert ret["ok"] is False
    assert "no official bot online" in ret["reason"]


def test_send_empty_message_is_skipped(patched):
    bot = FakeBot()
    patched["bot"] = bot
    ctx = _ctx_of(FakeGroupEvent())
    from src.plugins.qq_bridge.channel.base import Outgoing

    ret = asyncio.run(get_channel(OFFICIAL).send(ctx, Outgoing()))
    assert ret.get("skipped") is True
    assert bot.calls == []


def test_send_exception_returns_reason(patched):
    class BoomBot(FakeQQBot):
        async def send_to_group(self, *a, **k):
            raise RuntimeError("platform 40034105")

    patched["bot"] = BoomBot()
    ctx = _ctx_of(FakeGroupEvent())
    from src.plugins.qq_bridge.channel.base import Outgoing

    ret = asyncio.run(get_channel(OFFICIAL).send(ctx, Outgoing(text="hi")))
    assert ret["ok"] is False
    assert "40034105" in ret["reason"]


# ---------------------------------------------------------------- P2：富媒体 / 额度 / 被动转主动
def test_send_image_downloads_and_uploads(patched, monkeypatch):
    async def fake_download(url):
        return b"IMGBYTES"

    monkeypatch.setattr(official_mod, "_download", fake_download)
    bot = FakeBot()
    patched["bot"] = bot
    ctx = _ctx_of(FakeGroupEvent())
    from src.plugins.qq_bridge.channel.base import Outgoing

    ret = asyncio.run(get_channel(OFFICIAL).send(ctx, Outgoing(image_urls=["http://x/a.png"])))
    assert ret["ok"] is True
    assert len(bot.calls) == 1
    _kind, _target, msg, _mid, _seq = bot.calls[0]
    # file_name 必须非空，否则适配器会走 URL 上传分支
    assert ("file_image", "image.png", len(b"IMGBYTES")) in msg


def test_send_multiple_images_are_separate_messages(patched, monkeypatch):
    async def fake_download(url):
        return b"IMG"

    monkeypatch.setattr(official_mod, "_download", fake_download)
    bot = FakeBot()
    patched["bot"] = bot
    ctx = _ctx_of(FakeGroupEvent())
    from src.plugins.qq_bridge.channel.base import Outgoing

    ret = asyncio.run(get_channel(OFFICIAL).send(ctx, Outgoing(image_urls=["http://x/a.png", "http://x/b.png"])))
    assert ret["ok"] is True
    # 官方 msg_type 互斥（0=文本 / 7=富媒体）→ 每张图一条消息
    assert len(bot.calls) == 2
    assert len(ret["msg_ids"]) == 2


def test_passive_reply_inside_window(patched):
    bot = FakeBot()
    patched["bot"] = bot
    ctx = _ctx_of(FakeGroupEvent())          # ts=now → 在窗口内
    from src.plugins.qq_bridge.channel.base import Outgoing

    ret = asyncio.run(get_channel(OFFICIAL).send(ctx, Outgoing(text="hi")))
    assert ret["ok"] is True
    assert ret["modes"] == ["passive"]       # 不占日额度
    assert bot.calls[0][3] == "msg-2"        # 带被动 msg_id


def test_expired_ref_switches_to_active_and_counts_quota(patched, monkeypatch):
    from src.plugins.qq_bridge.config import Config

    monkeypatch.setattr(Config, "OFFICIAL_PASSIVE_MAX_AGE", 1)
    bot = FakeBot()
    patched["bot"] = bot
    ctx = _ctx_of(FakeGroupEvent())
    ctx["reply_ref"]["ts"] = time.time() - 3600      # 已过期
    from src.plugins.qq_bridge.channel.base import Outgoing

    ret = asyncio.run(get_channel(OFFICIAL).send(ctx, Outgoing(text="hi")))
    assert ret["ok"] is True
    assert ret["modes"] == ["active"]
    assert bot.calls[0][3] is None                  # 主动消息不带 msg_id
    from src.plugins.qq_bridge.channel.limiter import official_limiter

    assert official_limiter.used(ctx["group_key"]) == 1


def test_daily_quota_blocks_extra_active_messages(patched, monkeypatch):
    from src.plugins.qq_bridge.config import Config

    monkeypatch.setattr(Config, "OFFICIAL_PASSIVE_MAX_AGE", 1)
    monkeypatch.setattr(Config, "OFFICIAL_DAILY_GROUP_LIMIT", 1)

    async def fake_download(url):
        return b"IMG"

    monkeypatch.setattr(official_mod, "_download", fake_download)
    bot = FakeBot()
    patched["bot"] = bot
    ctx = _ctx_of(FakeGroupEvent())
    ctx["reply_ref"]["ts"] = time.time() - 3600
    from src.plugins.qq_bridge.channel.base import Outgoing

    asyncio.run(get_channel(OFFICIAL).send(ctx, Outgoing(image_urls=["http://x/a.png", "http://x/b.png"])))
    # 额度=1 → 第二张图被拦下，只发出 1 条（不撞平台 40034100）
    assert len(bot.calls) == 1


def test_limiter_resets_on_new_day():
    from src.plugins.qq_bridge.channel.limiter import OfficialLimiter

    lim = OfficialLimiter(limit_getter=lambda: 2)
    lim.add("g1")
    assert lim.used("g1") == 1
    assert lim.allow("g1") is True
    lim.add("g1")
    assert lim.allow("g1") is False
    # 模拟跨天
    lim._day = "2000-01-01"
    assert lim.used("g1") == 0
    assert lim.allow("g1") is True


# ---------------------------------------------------------------- P3：业务接入
class FakeSeg:
    def __init__(self, type_: str, data: dict):
        self.type = type_
        self.data = data


class FakeGroupEventWithText(FakeGroupEvent):
    def get_message(self):
        return [
            FakeSeg("text", {"text": " 生图 一只猫 "}),
            FakeSeg("image", {"url": "http://x/1.png"}),
        ]


class FakeChannelRecorder:
    """记录通道层收到的 Outgoing（用于验证 media 分派）。"""

    name = "official"

    def __init__(self):
        self.sent: list = []

    async def send(self, ctx, out):
        self.sent.append(out)
        return {"ok": True}


def test_to_legacy_event_maps_openid_to_negative_id(patched):
    """官方事件 → QQEvent：伪 ID 必须为负（真实 QQ 号恒为正，永不串号）。"""
    ch = get_channel(OFFICIAL)
    ev = asyncio.run(ch.to_legacy_event(FakeGroupEventWithText(), self_id="bot-1", trace_id="t-1"))
    assert ev is not None
    assert ev.user_id < 0
    assert ev.group_id < 0
    assert ev.message_type == "group"
    assert ev.raw_text == "生图 一只猫"
    assert ev.images == ["http://x/1.png"]
    assert getattr(ev, "channel") == OFFICIAL      # media 层据此分派


def test_pseudo_id_is_stable_and_negative():
    from src.plugins.qq_bridge.channel.official import pseudo_id

    a, b = pseudo_id("official:u:abc"), pseudo_id("official:u:abc")
    assert a == b                      # 稳定：同一 openid 永远同一 ID
    assert a < 0 and b < 0
    assert pseudo_id("official:u:abc") != pseudo_id("official:u:def")


def test_media_dispatches_by_channel(monkeypatch):
    """ctx 带 channel=official → media 交给通道层，不再走 OneBot 消息段。"""
    from src.plugins.qq_bridge import media

    fake = FakeChannelRecorder()
    monkeypatch.setattr("src.plugins.qq_bridge.channel.get_channel", lambda _n: fake)

    ctx = {"user_id": -1, "group_id": -2, "channel": "official"}
    asyncio.run(media.send_text(ctx, "hi"))
    asyncio.run(media.send_pic(ctx, b"PNGDATA"))
    assert len(fake.sent) == 2
    assert fake.sent[0].text == "hi"
    assert fake.sent[1].images == [b"PNGDATA"]


def test_media_without_channel_field_keeps_napcat_path():
    """老调用点没有 channel 字段 → 判定为 NapCat，行为与改造前一致。"""
    from src.plugins.qq_bridge.media import _foreign_channel

    assert _foreign_channel({"user_id": 10001, "group_id": 0}) == ""
    assert _foreign_channel({"user_id": 1, "channel": "napcat"}) == ""
    assert _foreign_channel({"channel": "official"}) == "official"


# ---------------------------------------------------------------- 5) 分发
def test_channel_of_bot_distinguishes(monkeypatch):
    import src.plugins.qq_bridge.channel as channel_pkg

    monkeypatch.setattr(channel_pkg, "_official_bot_cls", lambda: FakeQQBot)
    assert channel_of_bot(FakeBot()) == OFFICIAL
    assert channel_of_bot(SimpleNamespace()) == NAP_CAT
    assert channel_of_bot(None) == NAP_CAT


def test_channel_of_bot_without_adapter(monkeypatch):
    import src.plugins.qq_bridge.channel as channel_pkg

    # 适配器未安装 → 一律判为 NapCat，绝不把官方事件误判成官方通道
    monkeypatch.setattr(channel_pkg, "_official_bot_cls", lambda: None)
    assert channel_of_bot(FakeBot()) == NAP_CAT


# ------------------------------------------------- 6) 连接阶段失败的有界重发
# 2026-11-26 线上事故（用户「报错了」）：13:30 两张卡全丢，日志
#   channel_send_failed ... duration_ms=5033 / 5031 / 5025
# nonebot 驱动 httpx 默认 connect=5.0（read=30s）——耗时卡在 5s 说明**连接没建立**，
# 请求根本没到服务端。故只对这一类失败重发，且每次换新 msg_seq（防平台去重）。
class ConnectTimeout(Exception):
    """名字即判据（_is_connect_phase_error 按类名识别，不 import httpx）。"""


class ReadTimeout(Exception):
    """回包阶段超时：服务端可能已处理，**绝不重发**（会重复发消息）。"""


class NetworkError(Exception):
    """模拟适配器包装：NetworkError("API request failed") from <底层异常>。"""


def _group_event_and_outgoing():
    from src.plugins.qq_bridge.channel.base import Outgoing

    return _ctx_of(FakeGroupAtEvent()), Outgoing(text="你好")


def test_connect_phase_classifier_walks_cause_chain():
    assert official_mod._is_connect_phase_error(ConnectTimeout("x"))
    assert not official_mod._is_connect_phase_error(ReadTimeout("x"))
    try:
        try:
            raise ConnectTimeout("x")
        except Exception as inner:      # noqa: BLE001 — 模拟适配器 from e 包装
            raise NetworkError("API request failed") from inner
    except NetworkError as err:
        assert official_mod._is_connect_phase_error(err), \
            "适配器把底层异常包成 NetworkError 后，仍必须判出是连接失败"


def test_send_retries_connect_timeout_then_succeeds(patched, monkeypatch):
    monkeypatch.setattr(official_mod, "_SEND_RETRY_BASE_DELAY", 0)

    class FlakyBot(FakeBot):
        def __init__(self):
            super().__init__()
            self.attempts = []

        async def send_to_group(self, group_openid, message, msg_id=None, msg_seq=None,
                                event_id=None, msg_ref_id=None):
            self.attempts.append(msg_seq)
            if len(self.attempts) <= 2:
                raise ConnectTimeout("connect timed out")
            return await super().send_to_group(
                group_openid, message, msg_id=msg_id, msg_seq=msg_seq, event_id=event_id)

    bot = FlakyBot()
    patched["bot"] = bot
    ctx, out = _group_event_and_outgoing()
    ret = asyncio.run(get_channel(OFFICIAL).send(ctx, out))
    assert ret["ok"] is True, ret
    assert len(bot.attempts) == 3, bot.attempts
    # 每次重发必须换新 msg_seq：同一 msg_id 下重复 seq 会被平台按去重拒收
    assert bot.attempts == [1, 2, 3], bot.attempts


def test_send_does_not_retry_read_timeout(patched, monkeypatch):
    monkeypatch.setattr(official_mod, "_SEND_RETRY_BASE_DELAY", 0)

    class SlowBot(FakeBot):
        def __init__(self):
            super().__init__()
            self.attempts = []

        async def send_to_group(self, *a, **k):
            self.attempts.append(1)
            raise ReadTimeout("read timed out")

    bot = SlowBot()
    patched["bot"] = bot
    ctx, out = _group_event_and_outgoing()
    ret = asyncio.run(get_channel(OFFICIAL).send(ctx, out))
    assert ret["ok"] is False, "读超时必须如实失败，不能假装成功"
    assert len(bot.attempts) == 1, "读超时绝不许重发（会重复投递）"
