# -*- coding: utf-8 -*-
"""批量连炼口令层守门（2026-11-19）。

python tests/test_doupo_craft_batch_cmd.py
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLUGINS = os.path.join(ROOT, "src", "plugins")
for _p in (ROOT, PLUGINS):
    if _p not in sys.path:
        sys.path.insert(0, _p)
os.environ["DOUPO_CHANCE_RATE"] = "0"


def _stub():
    nb = types.ModuleType("nonebot")
    nb.get_driver = lambda: types.SimpleNamespace(on_startup=lambda f: f)
    def on_regex(*a, **k):
        m = types.SimpleNamespace()
        m.handle = lambda *a, **k: (lambda f: f)
        m.finish = lambda *a, **k: None
        return m
    nb.on_regex = on_regex
    sys.modules["nonebot"] = nb
    ad = types.ModuleType("nonebot.adapters")
    ob = types.ModuleType("nonebot.adapters.onebot")
    v11 = types.ModuleType("nonebot.adapters.onebot.v11")
    v11.MessageEvent = object; v11.MessageSegment = object; v11.Message = object
    sys.modules["nonebot.adapters"] = ad
    sys.modules["nonebot.adapters.onebot"] = ob
    sys.modules["nonebot.adapters.onebot.v11"] = v11


_stub()

from doupo import commands, core, config as cfg  # noqa: E402
from doupo.store import Store  # noqa: E402

A = "10001"
_FAILS = []
_N = 0


def check(name, cond, detail=""):
    global _N
    _N += 1
    if not cond:
        _FAILS.append(f"{name} {detail}")


def test_parse_count():
    f = commands._parse_craft_count
    check("单发默认1炉", f("回灵丹")[1] == 1)
    check("「5次」解析", f("回灵丹 5次") == ("回灵丹", 5))
    check("「×3」解析", f("回灵丹×3") == ("回灵丹", 3))
    check("「x2」解析", f("回灵丹x2") == ("回灵丹", 2))
    check("1炉仍单发", f("回灵丹 1")[1] == 1)
    check("超上限钳到BATCH_MAX", f("回灵丹 99")[1] == cfg.CRAFT_BATCH_MAX)


async def _cmd_test():
    tmp = tempfile.mkdtemp(prefix="doupo_batch_cmd_")
    store = Store(os.path.join(tmp, "doupo.json"))
    await store.load()
    p = core.create_player(A, "测试", "900001")
    p["alch"] = 8
    p["last_craft"] = 0
    for mat, qty in cfg.RECIPE_BY_NAME["回灵丹"]["need"].items():
        core.add_item(p, mat, qty * 8)
    await store.put_player(p)

    r = await commands.cmd_craft(store, A, "回灵丹 3次")
    check("连炼回包含炉数", "连炼" in r and "3炉" in r, r[:80])
    check("连炼回包一条含成败", "成" in r and "败" in r, r[:80])
    check("连炼回包含斗气", "斗气" in r, r[:80])
    p2 = await store.get_player(A)
    check("连炼后CD生效", core.craft_cd_left(p2, now_ts=1000) > 0 or p2.get("last_craft", 0) > 0,
          "last_craft should be set")

    # 未知丹方 + 批量写法仍要挡
    r2 = await commands.cmd_craft(store, A, "不存在丹 3次")
    check("批量未知丹方被挡", "无此丹方" in r2, r2[:40])


def _run():
    test_parse_count()
    asyncio.run(_cmd_test())
    print("CRAFT_BATCH_CMD_TOTAL=%d PASSED=%d FAILED=%d" % (
        _N, _N - len(_FAILS), len(_FAILS)))
    for f in _FAILS:
        print("FAIL", f)
    return 1 if _FAILS else 0


if __name__ == "__main__":
    sys.exit(_run())
