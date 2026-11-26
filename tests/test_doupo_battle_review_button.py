# -*- coding: utf-8 -*-
"""战斗回看按钮必须出卡（2026-11-26 用户实测回归）。

背景：手打「战斗回看」走 matcher → ``card.render_battle`` 出多页卡；
但战斗回执上的同名按钮走 ``button_dispatch``，2026-11-25 为躲
``renderer(p)`` 假卡问题被改成 ``review_text`` 纯文本兜底。日志证据
（生产 11-26 13:10:40）``official_sent kind=text_card buttons=战斗评价,…``。
本文件用真实 dispatch_button 源码 + 注入假 store/card/channel，守两件事：

1. 有战报 → 必须发**图片卡**（多页 list 也按一条发），card_key=battle；
2. 无战报 → 回文本，但按钮族仍是 review_text（继续战斗/任务不能丢）。

拒绝纯 AST 断言：把 _PLAN 改回 review_text、或删掉专用出卡分支，用例必红。
"""
import asyncio
import importlib.util
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOUPO = ROOT / "src" / "plugins" / "doupo"


def _load_dispatch(store_player, card_pages, fake_cmd_msg="逐招文本"):
    """加载 button_dispatch 并注入桩，返回 (模块, 记录字典)。"""
    called = {"render_for": [], "cmd": [], "cards": [], "texts": []}

    async def _ok():
        return None

    async def _send_card(ctx, data, card_key):
        called["cards"].append((data, card_key))
        return {"ok": True}

    async def _send(ctx, outgoing):
        called["texts"].append(outgoing)
        return {"ok": True}

    async def fake_cmd_battle_review(store, qq):
        called["cmd"].append(qq)
        return fake_cmd_msg

    async def fake_render_battle_for(qq, p):
        called["render_for"].append((qq, p))
        return card_pages

    # 唯一出卡入口必须存在；没有它按钮端就只能回文本
    for pkg_name in ("revpkg", "revpkg.doupo"):
        pkg = types.ModuleType(pkg_name)
        pkg.__path__ = []
        sys.modules[pkg_name] = pkg
    commands = types.ModuleType("revpkg.doupo.commands")
    commands.cmd_battle_review = fake_cmd_battle_review
    sys.modules["revpkg.doupo.commands"] = commands
    sys.modules["revpkg.doupo"].commands = commands

    card = types.ModuleType("revpkg.doupo.card")
    card.render_battle_for = fake_render_battle_for
    sys.modules["revpkg.doupo.card"] = card
    sys.modules["revpkg.doupo"].card = card

    fake_store = types.ModuleType("revpkg.doupo.store")
    fake_store.STORE = types.SimpleNamespace(
        load=_ok,
        get_player=lambda qq: _ok_result(store_player),
        all_players=lambda gid: _ok_result([]),
    )
    sys.modules["revpkg.doupo.store"] = fake_store

    ch_base = types.ModuleType("rev_base_stub")
    ch_base.Outgoing = lambda **kw: kw
    sys.modules["src.plugins.qq_bridge.channel.base"] = ch_base
    ch_mod = types.ModuleType("rev_channel_stub")
    ch_mod.OFFICIAL = "official"
    ch_mod.get_channel = lambda name: types.SimpleNamespace(send=_send)
    sys.modules["src.plugins.qq_bridge.channel"] = ch_mod

    gm = types.ModuleType("src.game_mode")
    gm.set_mode = _ok
    sys.modules["src.game_mode"] = gm
    gc = types.ModuleType("src.game_compat")
    gc.send_card_via_channel = _send_card
    sys.modules["src.game_compat"] = gc

    src = DOUPO.joinpath("button_dispatch.py").read_text(encoding="utf-8")
    src = src.replace("from . import commands as cmd_mod",
                      "from revpkg.doupo import commands as cmd_mod")
    src = src.replace("from . import card as card_mod",
                      "from revpkg.doupo import card as card_mod")
    src = src.replace("from .store import STORE as _STORE",
                      "from revpkg.doupo.store import STORE as _STORE")
    mod = types.ModuleType("bd_review_button")
    sys.modules["bd_review_button"] = mod
    exec(compile(src, "bd_review_button", "exec"), mod.__dict__)  # noqa: S102
    return mod, called


async def _ok_result(value):
    return value


def _ctx():
    return {"user_key": "group:u1", "group_key": "group:g1"}


def test_review_button_sends_battle_card_not_text():
    """有战报：必须走图片卡，多页 list 原样下发，card_key=battle。"""
    p = {"qq": "u1", "last_battle": {"log": [{"text": "一招"}], "win": True}}
    pages = [b"\x89PNG\r\npage1", b"\x89PNG\r\npage2"]
    mod, called = _load_dispatch(p, pages)

    assert asyncio.run(mod.dispatch_button(None, _ctx(), "战斗回看")) is True
    assert called["cards"], "战斗回看按钮仍然只回文本（用户看到的 BUG）"
    data, key = called["cards"][0]
    assert data == pages and key == "battle", (key, len(data))
    assert not called["texts"], "出图后又发了一条文本，违反一条消息契约"
    assert called["render_for"] == [("u1", p)], "没走按 qq 取数唯一入口（会渲假卡）"


def test_review_button_without_log_keeps_review_text_buttons():
    """无战报：文本兜底仍挂 review_text 按钮族，不能退化成无按钮。"""
    mod, called = _load_dispatch({"qq": "u1", "last_battle": None}, [])

    assert asyncio.run(mod.dispatch_button(None, _ctx(), "战斗回看")) is True
    assert not called["cards"], "没有战报却渲出卡（编造战报）"
    assert len(called["texts"]) == 1
    assert called["texts"][0]["keyboard_cmd"] == "doupo:review_text"
    assert called["texts"][0]["text"] == "逐招文本"
