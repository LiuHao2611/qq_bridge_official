# -*- coding: utf-8 -*-
"""官方通道纯文本收口守门（2026-11-25，prove-it，独立基准）。

## 线上事故（用户原话）

> 看看日志 我上阵斗技和学习斗技都没反应

## 实测证据（`~/qq_bridge/logs/service.log`，2026-11-25 00:15–00:25）

| 时间 | 用户输入 | 命中 matcher | 结果 |
|---|---|---|---|
| 00:15:29 | 点「上阵」按钮 | —（按钮派发） | `official_sent kind=text` ✅ 用户看到了，还照着手打了「上阵 14」 |
| 00:15:48 | `上阵 14` | doupo:575 `running complete` | 无日志、无报错、**用户收不到** |
| 00:16:21 | `升级 帝炎破世` | doupo:564 | 同上 |
| 00:20:05/33/48 | `上阵 11 14` ×2、`上阵 11` | doupo:575 | 连续重试三次 = 典型「没反应」 |
| 00:22:36 | `锁定 破厄丹` | doupo:1051 | 同上 |
| 00:25:00 | `主题 浅色` | doupo:200 | 同上 |
| 对照 | `背包`/`斗技`/`斗破菜单` | 出**卡片** | 全部正常到达 |

结论：**同一机器人，卡片与按钮文本都能到，只有 `matcher.finish(str)` 的原生纯文本
到不了**。故 2026-11-25 把官方通道所有纯文本统一收口到 `OfficialChannel.send`。

## 本文件守什么

① `install_official_text_bridge()` 幂等、只在官方适配器可用时生效；
② 字符串消息 → 走通道（且原生 `send` 不被调用）；
③ 非字符串（图片段 / `Message`）→ **原样走原生**，出图链路零影响；
④ 通道返回 False / 抛异常 → **必须回退原生**（绝不比现状更差）；
⑤ NapCat 事件 → `finish_text` 仍走原生，行为零变化；
⑥ `msg_seq` 与原生 `event._reply_seq` 同一计数器（根治 40054005 双发撞车）；
⑦ 2026-11-25 19:45 次生事故：官方 API 偶发 `NetworkError`，`finish_text` 首发
   失败后降级兜底桥丢了 `card_key="fight_text"` → 战斗回执挂成主菜单按钮（用户体感
   「战斗没反应/发错」）。守 `_channel_send_text` 对**网络类失败**即时重试一次且
   **保留同一个 keyboard_cmd**；业务类失败（去重/配额/4005xxx）不重试。

跑法：python tests/test_official_text_bridge.py（宿主纯标准库，无需 nonebot/pytest）
"""
from __future__ import annotations

import asyncio
import importlib
import sys
import types
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


# ---------------------------------------------------------------------------
# 假的 nonebot.adapters.qq（真适配器宿主没装；只为让 is_official/补丁生效）
# ---------------------------------------------------------------------------
def _install_fake_qq() -> types.ModuleType:
    """重建一份「全新」的假 qq 适配器，Bot.send 记录调用。"""
    for name in ("nonebot", "nonebot.adapters", "nonebot.adapters.qq"):
        sys.modules.pop(name, None)

    nb = types.ModuleType("nonebot")
    nb.__path__ = []  # type: ignore[attr-defined]
    ad = types.ModuleType("nonebot.adapters")
    ad.__path__ = []  # type: ignore[attr-defined]
    ad.Event = type("Event", (), {})
    qq = types.ModuleType("nonebot.adapters.qq")
    qq.__path__ = []  # type: ignore[attr-defined]

    class GroupMessageCreateEvent:
        def __init__(self) -> None:
            self._reply_seq = 0
            self.id = "MSGID_TEST"

        @property
        def _reply_seq(self) -> int:
            return self.__dict__["_reply_seq"]

        @_reply_seq.setter
        def _reply_seq(self, v) -> None:
            self.__dict__["_reply_seq"] = v

    class C2CMessageCreateEvent(GroupMessageCreateEvent):
        pass

    # is_official() 靠模块名前缀判定，必须跟真适配器一致
    for cls in (GroupMessageCreateEvent, C2CMessageCreateEvent):
        cls.__module__ = "nonebot.adapters.qq"

    calls: list = []

    class Bot:
        async def send(self, event, message, **kwargs):  # noqa: ANN001
            calls.append(message)
            return {"id": "NATIVE%d" % len(calls)}

    Bot.calls = calls  # type: ignore[attr-defined]

    qq.Bot = Bot
    qq.GroupMessageCreateEvent = GroupMessageCreateEvent
    qq.C2CMessageCreateEvent = C2CMessageCreateEvent
    sys.modules["nonebot"] = nb
    sys.modules["nonebot.adapters"] = ad
    sys.modules["nonebot.adapters.qq"] = qq
    return qq


def _fresh_mod(qq: types.ModuleType):
    """重载 game_compat，并让补丁装到「当前这份」假 Bot 上。"""
    sys.modules.pop("src.game_compat", None)
    mod = importlib.import_module("src.game_compat")
    mod._BRIDGE_INSTALLED = False
    assert mod.install_official_text_bridge() is True, "官方适配器可用时必须装得上"
    return mod


class _Matcher:
    """记录 finish 调用的假 matcher（空 finish = 已发出，带参 = 原生兜底）。"""

    def __init__(self) -> None:
        self.done: list = []

    async def finish(self, *args, **kwargs):  # noqa: ANN001
        self.done.append(args if args else ())
        raise _Finished()


class _Finished(Exception):
    """模拟 nonebot 的 FinishedException（handler 在 finish 处结束）。"""


async def _noop(_raw, _text, _kcmd=""):  # 通道替身：不发，只回报 True
    return True


def _install_fake_channel(send_results):
    """桩出官方通道层，直接驱动 ``_channel_send_text`` 内部的重试逻辑。

    旧用例把 ``_channel_send_text`` 整个替换掉，测不到函数内的重试；这里改桩
    更下一层的 ``get_channel / Outgoing / attach_ctx``，让真实函数跑起来。

    :param send_results: 每次 ``ch.send`` 按顺序返回的结果（dict）。
    :return: ``(calls, ch)``，calls 记录每次 send 的 Outgoing（含 keyboard_cmd）。
    """
    calls: list = []

    class Outgoing:
        def __init__(self, text="", images=None, keyboard_cmd=""):  # noqa: ANN001
            self.text = text
            self.images = images
            self.keyboard_cmd = keyboard_cmd

    class FakeChannel:
        async def normalize(self, raw, self_id=""):  # noqa: ANN001
            return {"reply_ref": {}, "user_key": "qq:OPENID_TEST"}

        async def send(self, ctx, outgoing):  # noqa: ANN001
            calls.append(outgoing)
            i = len(calls) - 1
            return send_results[i] if i < len(send_results) else {"ok": True}

    ch = FakeChannel()
    for pkg in ("src.plugins", "src.plugins.qq_bridge"):
        if pkg not in sys.modules:
            m = types.ModuleType(pkg)
            m.__path__ = []  # type: ignore[attr-defined]
            sys.modules[pkg] = m

    chan = types.ModuleType("src.plugins.qq_bridge.channel")
    chan.__path__ = []  # type: ignore[attr-defined]
    chan.OFFICIAL = "official"
    chan.get_channel = lambda name: ch
    sys.modules["src.plugins.qq_bridge.channel"] = chan

    base = types.ModuleType("src.plugins.qq_bridge.channel.base")
    base.Outgoing = Outgoing
    base.attach_official_ctx = lambda ctx: object()  # noqa: ARG005
    base.detach_official_ctx = lambda token: None  # noqa: ARG005
    sys.modules["src.plugins.qq_bridge.channel.base"] = base
    return calls, ch


def _stub_quiz_no_button() -> None:
    """让 finish_text 里 take_button() 返回空（本组用例只测 card_key 链路）。"""
    if "doupo" not in sys.modules:
        pkg = types.ModuleType("doupo")
        pkg.__path__ = [str(_ROOT / "src/plugins/doupo")]  # type: ignore[attr-defined]
        sys.modules["doupo"] = pkg
    qs = types.ModuleType("doupo.quiz_session")
    qs.take_button = lambda: ""
    sys.modules["doupo.quiz_session"] = qs
    setattr(sys.modules["doupo"], "quiz_session", qs)


# ---------------------------------------------------------------------------
# 守门用例
# ---------------------------------------------------------------------------
def t_install_idempotent() -> None:
    qq = _install_fake_qq()
    mod = _fresh_mod(qq)
    sent1 = qq.Bot.send
    assert mod.install_official_text_bridge() is True
    assert qq.Bot.send is sent1, "二次安装不得重复包装（会叠成多次发送）"
    assert getattr(qq.Bot.send, "_qq_text_bridge", False) is True


def t_text_goes_channel() -> None:
    qq = _install_fake_qq()
    mod = _fresh_mod(qq)
    seen: list = []

    async def fake(raw, text, kcmd=""):  # noqa: ANN001
        seen.append((text, kcmd))
        return True

    mod._channel_send_text = fake  # type: ignore[assignment]
    bot, ev = qq.Bot(), qq.GroupMessageCreateEvent()
    asyncio.run(bot.send(ev, "「斗技」不可上阵（未解锁或非本宗）。可上阵：…"))
    assert len(seen) == 1, "纯文本必须走通道，实际 %d 次" % len(seen)
    assert seen[0][0].startswith("「斗技」不可上阵")
    assert qq.Bot.calls == [], "走通道成功时不得再调原生 send（会双发）"


def t_non_text_untouched() -> None:
    qq = _install_fake_qq()
    mod = _fresh_mod(qq)
    called: list = []

    async def spy(raw, text, kcmd=""):  # noqa: ANN001
        called.append(text)                 # 记录，不抛（抛会被回退逻辑吞掉）
        return True

    mod._channel_send_text = spy  # type: ignore[assignment]

    class _Seg:
        pass

    bot, ev = qq.Bot(), qq.GroupMessageCreateEvent()
    asyncio.run(bot.send(ev, _Seg()))            # 单段（图片/语音）
    asyncio.run(bot.send(ev, [_Seg(), _Seg()]))  # 多段（Message）
    assert called == [], "图片/富媒体段绝不能进通道文本分支（会丢图），实际进 %d 次" % len(called)
    assert len(qq.Bot.calls) == 2, "非字符串消息必须原样走原生，实际 %d" % len(qq.Bot.calls)


def t_channel_false_falls_back() -> None:
    qq = _install_fake_qq()
    mod = _fresh_mod(qq)

    async def not_ok(raw, text, kcmd=""):  # noqa: ANN001
        return False

    mod._channel_send_text = not_ok  # type: ignore[assignment]
    bot, ev = qq.Bot(), qq.GroupMessageCreateEvent()
    asyncio.run(bot.send(ev, "✅ 已上阵：风灵分形剑[三阶]"))
    assert qq.Bot.calls == ["✅ 已上阵：风灵分形剑[三阶]"], "通道没发出去必须回退原生"


def t_channel_raise_falls_back() -> None:
    qq = _install_fake_qq()
    mod = _fresh_mod(qq)

    async def boom(raw, text, kcmd=""):  # noqa: ANN001
        raise RuntimeError("no official bot online")

    mod._channel_send_text = boom  # type: ignore[assignment]
    bot, ev = qq.Bot(), qq.GroupMessageCreateEvent()
    asyncio.run(bot.send(ev, "还没领悟：焰分噬浪尺（名录卡 14 号）。"))
    assert len(qq.Bot.calls) == 1, "通道异常必须回退原生（消息绝不能丢）"


def t_blank_text_ignored() -> None:
    qq = _install_fake_qq()
    mod = _fresh_mod(qq)
    called: list = []

    async def spy(raw, text, kcmd=""):  # noqa: ANN001
        called.append(text)
        return True

    mod._channel_send_text = spy  # type: ignore[assignment]
    bot, ev = qq.Bot(), qq.GroupMessageCreateEvent()
    asyncio.run(bot.send(ev, "   "))
    assert called == [], "空白文本不该占用一条通道消息"
    assert len(qq.Bot.calls) == 1, "空白文本按原样交给原生实现（保持旧行为）"


def t_napcat_finish_text_native() -> None:
    qq = _install_fake_qq()
    mod = _fresh_mod(qq)
    called: list = []

    async def spy(raw, text, kcmd=""):  # noqa: ANN001
        called.append(text)
        return True

    mod._channel_send_text = spy  # type: ignore[assignment]

    class NapCatEvent:                      # 模块名不是 nonebot.adapters.qq
        user_id = 1743124119
        group_id = -595244844

        def get_plaintext(self) -> str:
            return "上阵 1 2"

    m = _Matcher()
    try:
        asyncio.run(mod.finish_text(m, NapCatEvent(), "✅ 已上阵：…"))
    except _Finished:
        pass
    assert called == [], "NapCat 事件不该进官方通道"
    assert m.done == [("✅ 已上阵：…",)], "NapCat 必须保持原生 finish，零行为变化"


def t_official_finish_text_channel() -> None:
    qq = _install_fake_qq()
    mod = _fresh_mod(qq)

    # 棋局：把 doupo.quiz_session 换成空实现（本用例只测「无按钮也走通道」）
    if "doupo" not in sys.modules:
        pkg = types.ModuleType("doupo")
        pkg.__path__ = [str(_ROOT / "src/plugins/doupo")]  # type: ignore[attr-defined]
        sys.modules["doupo"] = pkg
    qs = types.ModuleType("doupo.quiz_session")
    qs.take_button = lambda: ""              # 本回合没有竞猜按钮
    sys.modules["doupo.quiz_session"] = qs
    setattr(sys.modules["doupo"], "quiz_session", qs)

    seen: list = []

    async def fake(raw, text, kcmd=""):  # noqa: ANN001
        seen.append(text)
        return True

    mod._channel_send_text = fake  # type: ignore[assignment]
    m = _Matcher()
    ev = qq.GroupMessageCreateEvent()
    try:
        asyncio.run(mod.finish_text(m, ev, "当前上阵：（空）\n可上阵：1.风灵分形剑"))
    except _Finished:
        pass
    assert seen == ["当前上阵：（空）\n可上阵：1.风灵分形剑"], "官方文本必须走通道"
    assert m.done == [()], "走通道成功后应空 finish（否则双发）"


def t_seq_shared_with_native() -> None:
    qq = _install_fake_qq()
    mod = _fresh_mod(qq)
    ev = qq.GroupMessageCreateEvent()

    assert mod._seq_seed(ev) == 1, "首条被动回复必须 seq=1"
    ev._reply_seq = 2
    assert mod._seq_seed(ev) == 3, "原生已发过 2 条时，通道要接着数到 3"

    ctx = {"reply_ref": {"msg_seq": 4}}       # 通道发完把计数推进到 4
    mod._seq_commit(ev, ctx)
    assert ev._reply_seq == 3, "回写后原生下一次 send 应为 seq=4（不撞车）"
    mod._seq_commit(ev, {})                   # 脏 ctx 不得抛
    assert ev._reply_seq == 0


def t_grep_no_raw_text_finish_left_unbridged() -> None:
    """结构性守门：斗破插件里所有 `X.finish(` 纯文本站点都被补丁覆盖。

    补丁装在最底层 QQBot.send，所以只要 install 成功，任何写法的 finish 都覆盖；
    这里额外断言「补丁入口真的在插件 import 时被调用」，防止有人删掉那三行。
    """
    src = (_ROOT / "src/plugins/doupo/__init__.py").read_text(encoding="utf-8")
    assert "install_official_text_bridge()" in src, "插件 import 时必须安装文本收口补丁"
    compat = (_ROOT / "src/game_compat.py").read_text(encoding="utf-8")
    assert "_qq_text_bridge" in compat and "QQBot.send = send" in compat
    # 纯文本分支必须限定字符串（否则会吞掉图片段）
    assert "isinstance(message, str)" in compat, "补丁必须只拦字符串，别吞图片段"


def t_pad_single_line_roundtrip() -> None:
    """单行短正文必须补成两行；多行/空白一律不动。"""
    qq = _install_fake_qq()
    mod = _fresh_mod(qq)
    pad = mod.pad_single_line

    one = "「14」不可上阵（未解锁或非本宗）。当前可上阵：帝炎破世"
    out = pad(one)
    assert out.splitlines()[0] == one, "正文不得被改写，实际 %r" % out
    assert len(out.splitlines()) == 2, "单行必须补成两行，实际 %d 行" % len(out.splitlines())
    assert mod._SINGLE_LINE_FOOTER in out, "第二行必须是页脚口令"
    assert pad(out) == out, "已多行必须幂等（不能越补越长）"

    multi = "当前上阵：帝炎破世[黄]\n可上阵（已领悟）：1.帝炎破世\n用法：上阵 1 3"
    assert pad(multi) == multi, "多行正文不得被改写"
    assert pad(multi + "\n") == multi + "\n", "多行（带尾换行）也不得被改写"
    assert pad("") == "" and pad("   \n ") == "   \n ", "空白原样返回"
    assert pad("单行").count("\n") == 1, "补完只应多一个换行"


def t_escape_markdown_angles() -> None:
    """`<技名>` 这类尖括号占位必须换全角，否则 markdown 当 HTML 标签吞掉。"""
    qq = _install_fake_qq()
    mod = _fresh_mod(qq)

    src = "用法：上阵 1 3 5 7 或 <技名>（最多 4 门）"
    out = mod.escape_markdown_angles(src)
    assert "<" not in out and ">" not in out, "尖括号必须被换掉，实际 %r" % out
    assert "＜技名＞" in out, "占位语义要保留可读性，实际 %r" % out
    assert mod.escape_markdown_angles("无尖括号") == "无尖括号", "无关正文不得被改写"
    assert mod.escape_markdown_angles("") == "", "空串安全"

    # prepare = 换尖括号 + 补多行，两步都要生效
    one = mod.prepare_official_text("不可上阵 <技名>")
    assert len(one.splitlines()) == 2 and "＜技名＞" in one, "组合函数两步都得生效：%r" % one


def t_channel_text_is_padded() -> None:
    """结构守门：通道发送必须走 prepare_official_text（防有人绕过把它删掉）。"""
    compat = (_ROOT / "src/game_compat.py").read_text(encoding="utf-8")
    assert "Outgoing(text=prepare_official_text(text)" in compat, (
        "官方通道文本必须经 prepare_official_text（单行必被平台吞，见 2026-11-25 证据链）")


def t_network_fail_retries_keeping_kcmd() -> None:
    """事故守门①：NetworkError 首发失败必须重试一次，且保留同一个 keyboard_cmd。"""
    qq = _install_fake_qq()
    mod = _fresh_mod(qq)
    calls, _ch = _install_fake_channel([
        {"ok": False, "reason": "NetworkError detail=<NetWorkError message=API request failed>"},
        {"ok": True, "id": "OK2"},
    ])
    ev = qq.GroupMessageCreateEvent()
    ok = asyncio.run(mod._channel_send_text(ev, "战斗回执正文", "doupo:fight_text"))
    assert ok is True, "重试成功后必须回报 True（否则 finish_text 仍降级丢按钮）"
    assert len(calls) == 2, "网络类失败必须恰好重试一次，实际 send %d 次" % len(calls)
    assert [c.keyboard_cmd for c in calls] == ["doupo:fight_text"] * 2, (
        "两次发送必须都带同一个 keyboard_cmd，实际 %r" % [c.keyboard_cmd for c in calls])


def t_business_fail_no_retry() -> None:
    """事故守门②：业务失败（去重/配额/4005xxx）不得重试——重试无意义还撞 40054005。"""
    for reason in ("40054005 消息被去重", "配额不足", "4005xxx appid invalid"):
        qq = _install_fake_qq()
        mod = _fresh_mod(qq)
        calls, _ch = _install_fake_channel([{"ok": False, "reason": reason}])
        ev = qq.GroupMessageCreateEvent()
        ok = asyncio.run(mod._channel_send_text(ev, "正文", "doupo:fight_text"))
        assert ok is False, "业务失败应回报 False 让上层降级，reason=%s" % reason
        assert len(calls) == 1, "业务失败不得重试，reason=%s 实际 send %d 次" % (reason, len(calls))


def t_success_no_retry() -> None:
    """事故守门③：首发成功不得重试（防重试逻辑变成双发）。"""
    qq = _install_fake_qq()
    mod = _fresh_mod(qq)
    calls, _ch = _install_fake_channel([{"ok": True, "id": "OK1"}])
    ev = qq.GroupMessageCreateEvent()
    ok = asyncio.run(mod._channel_send_text(ev, "正文", "doupo:fight_text"))
    assert ok is True
    assert len(calls) == 1, "首发成功不得再发一次，实际 send %d 次" % len(calls)


def t_fight_text_button_survives_net_error() -> None:
    """事故守门④（真场景）：finish_text(card_key=fight_text) 首发 NetworkError 后，
    重试成功 → matcher 空 finish，不降级兜底桥 → 按钮仍是战斗三键而非主菜单。
    """
    qq = _install_fake_qq()
    mod = _fresh_mod(qq)
    _stub_quiz_no_button()
    calls, _ch = _install_fake_channel([
        {"ok": False, "reason": "NetworkError detail=<NetWorkError message=API request failed>"},
        {"ok": True, "id": "OK2"},
    ])
    m = _Matcher()
    ev = qq.GroupMessageCreateEvent()
    try:
        asyncio.run(mod.finish_text(m, ev, "【战斗】风起\n你击败了对手", card_key="fight_text"))
    except _Finished:
        pass
    assert len(calls) == 2, "战斗回执应重试一次，实际 %d 次" % len(calls)
    assert all(c.keyboard_cmd == "doupo:fight_text" for c in calls), (
        "战斗按钮口令必须在重试中保留，实际 %r" % [c.keyboard_cmd for c in calls])
    assert m.done == [()], (
        "重试成功后必须空 finish（不降级兜底桥），否则按钮掉回主菜单，实际 %r" % m.done)


CASES = (
    ("install_idempotent", t_install_idempotent),
    ("text_goes_channel", t_text_goes_channel),
    ("non_text_untouched", t_non_text_untouched),
    ("channel_false_falls_back", t_channel_false_falls_back),
    ("channel_raise_falls_back", t_channel_raise_falls_back),
    ("blank_text_ignored", t_blank_text_ignored),
    ("napcat_finish_text_native", t_napcat_finish_text_native),
    ("official_finish_text_channel", t_official_finish_text_channel),
    ("seq_shared_with_native", t_seq_shared_with_native),
    ("pad_single_line_roundtrip", t_pad_single_line_roundtrip),
    ("escape_markdown_angles", t_escape_markdown_angles),
    ("channel_text_is_padded", t_channel_text_is_padded),
    ("no_raw_text_finish_left_unbridged", t_grep_no_raw_text_finish_left_unbridged),
    ("network_fail_retries_keeping_kcmd", t_network_fail_retries_keeping_kcmd),
    ("business_fail_no_retry", t_business_fail_no_retry),
    ("success_no_retry", t_success_no_retry),
    ("fight_text_button_survives_net_error", t_fight_text_button_survives_net_error),
)


def main() -> int:
    bad = 0
    for name, fn in CASES:
        try:
            fn()
        except Exception as e:  # noqa: BLE001
            bad += 1
            print("FAIL  %-38s %s: %s" % (name, type(e).__name__, e))
        else:
            print("ok    %s" % name)
    total = len(CASES)
    print("\n%d/%d 通过" % (total - bad, total))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
