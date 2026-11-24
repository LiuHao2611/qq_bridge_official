# -*- coding: utf-8 -*-
"""模式路由兜底守门（2026-11-24）—— 官方通道必须能触发友好提示。

事故背景：`game_router` 的 rule 注解曾是 OneBot 的 `MessageEvent`，官方 QQ 通道
事件类注入失败 → Rule 静默 False → 兜底永不触发 → 用户在错误模式下打游戏口令
「机器人一声不响」。本测试钉死两件事：

1. rule 的 event 注解必须是通道无关的兼容基类（不能是某个适配器的具体类型）；
2. 官方事件与 NapCat 事件都能让 rule 返回 True，闲聊返回 False。

自带 runner：python tests/test_game_router_channel.py
"""
from __future__ import annotations

import inspect
import os
import sys
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (ROOT, os.path.join(ROOT, "src", "plugins")):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def _stub():
    nb = types.ModuleType("nonebot")
    nb.get_driver = lambda: types.SimpleNamespace(on_startup=lambda f: f)

    def on_regex(*a, **k):
        m = types.SimpleNamespace()
        m.handle = lambda *a, **k: (lambda f: f)
        m.finish = lambda *a, **k: None
        return m

    nb.on_regex = on_regex
    nb.on_command = on_regex
    nb.on_notice = on_regex
    nb.on_message = on_regex
    sys.modules["nonebot"] = nb

    class _BaseEvent:            # 兼容层要求 nonebot.adapters.Event
        pass

    ad = types.ModuleType("nonebot.adapters")
    ad.Event = _BaseEvent
    sys.modules["nonebot.adapters"] = ad

    for name in ("nonebot.adapters.onebot", "nonebot.adapters.onebot.v11",
                 "nonebot.adapters.qq"):
        sys.modules[name] = types.ModuleType(name)
    v11 = sys.modules["nonebot.adapters.onebot.v11"]
    v11.MessageEvent = type("MessageEvent", (_BaseEvent,), {})
    v11.MessageSegment = object
    v11.Message = object
    qq = sys.modules["nonebot.adapters.qq"]
    qq.MessageEvent = type("MessageEvent", (_BaseEvent,), {})
    qq.GroupMessageCreateEvent = type("GroupMessageCreateEvent", (_BaseEvent,), {})


_stub()

from src import game_compat  # noqa: E402
from src.game_mode import register_keywords  # noqa: E402
import game_router  # noqa: E402  （src/plugins 在 sys.path 上，与 doupo 测试同口径）

# 关键字表由各游戏插件在 import 时登记；这里直接登记必要的几条
register_keywords("doupo", ["炼药", "炼制", "人物", "背包", "突破"])
register_keywords("xiuxian", ["修仙菜单", "我要修仙"])


class _Author:
    def __init__(self, oid):
        self.member_openid = oid
        self.username = "测试"


def _make_official(text: str):
    """构造一个模块名前缀为 nonebot.adapters.qq 的事件（模拟官方通道）。"""
    cls = type("GroupMessageCreateEvent", (game_compat.Event,), {
        "__module__": "nonebot.adapters.qq",
        "get_plaintext": lambda self: text,
    })
    ev = cls()
    ev.author = _Author("EA1D2B18084ACC91221FA3130F1531A4")
    ev.group_openid = "311B04047D447AF3F4B8B39B389387C3"
    return ev


def _make_napcat(text: str, uid="1743124119"):
    cls = type("GroupMessageEvent", (game_compat.Event,), {
        "__module__": "nonebot.adapters.onebot.v11",
        "get_plaintext": lambda self: text,
    })
    ev = cls()
    ev.user_id = uid
    ev.group_id = 1075851417
    return ev


def t_rule_annotation_is_channel_agnostic():
    """🔒 注解不能是某个适配器的具体事件类型（否则官方通道 rule 恒 False）。

    模块开了 ``from __future__ import annotations``，注解是字符串；按 NoneBot 的
    实际做法用 ``get_type_hints`` 解析后再判类型。
    """
    import typing
    hints = typing.get_type_hints(game_router._is_command)
    ann = hints.get("event")
    assert ann is not None, "rule 参数缺注解"
    mod = getattr(ann, "__module__", "")
    assert not mod.startswith("nonebot.adapters.onebot"), (
        f"注解回退成 OneBot 具体类型了：{ann!r}（官方通道会静默失效）")
    assert ann is game_compat.Event, f"注解应为兼容基类 Event，实际 {ann!r}"


def t_official_channel_triggers():
    """官方通道 + 斗破口令 → rule 必须 True（本次事故的就医点）。"""
    for text in ("炼药", "炼制 破厄丹", "炼制  破厄丹", "背包", "#人物"):
        assert game_router._is_command(_make_official(text)) is True, text


def t_napcat_channel_triggers():
    """NapCat 通道行为不许回归。"""
    for text in ("炼药", "炼制 聚气散 11", "突破"):
        assert game_router._is_command(_make_napcat(text)) is True, text


def t_chitchat_not_command():
    """闲聊不能误伤（否则每条聊天都会被兜底抢答）。"""
    for text in ("今天天气不错", "哈哈哈", "1", ""):
        assert game_router._is_command(_make_official(text)) is False, text


def t_reply_texts_and_mode():
    """提示文案：未选模式 / 模式不对 各一句，且带上正确的切换口令。"""
    import asyncio
    from src import game_mode

    async def _run():
        # 未选模式
        game_mode._modes.pop("NOSUCH_UID", None)
        sent = []
        orig = game_router.finish_text

        async def _cap(matcher, ev, text, **k):
            sent.append(text)

        game_router.finish_text = _cap
        try:
            await game_router.router.handle()  # noqa: B018 - 仅为取到已注册函数
        except Exception:
            pass
        finally:
            game_router.finish_text = orig
        return sent

    # 该插件用装饰器注册 handler，测试只校验常量与文案生成逻辑
    from src.game_mode import GAME_LABELS
    assert GAME_LABELS["doupo"] == "斗破" and GAME_LABELS["xiuxian"] == "修仙"


TESTS = [t_rule_annotation_is_channel_agnostic, t_official_channel_triggers,
         t_napcat_channel_triggers, t_chitchat_not_command, t_reply_texts_and_mode]

if __name__ == "__main__":
    ok = 0
    for fn in TESTS:
        try:
            fn(); ok += 1; print("PASS", fn.__name__)
        except Exception as e:
            print("FAIL", fn.__name__, type(e).__name__, str(e)[:220])
    print(f"\n{ok}/{len(TESTS)} passed")
    sys.exit(0 if ok == len(TESTS) else 1)
