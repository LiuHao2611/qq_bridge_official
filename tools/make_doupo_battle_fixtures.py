# -*- coding: utf-8 -*-
"""生成**冻结夹具**：把真实打出来的 `last_battle` 存成 JSON。

为什么需要夹具：`cmd_fight` 的文本正文本来就**必须**随 G2 变化（怪按章节走 →
不同的怪、不同的回合数），拿它做跨版本字节比对是错的口径。真正该守的是
「回看卡 / 战斗评价的**渲染器**没被碰」——把一场真实战斗的 `last_battle`
冻成 JSON，两边喂同一份夹具，渲染结果就必须逐字节相同。

跑法：python tools/make_doupo_battle_fixtures.py
产出：tools/fixtures/doupo_battle_fixtures.json
"""
from __future__ import annotations

import asyncio
import json
import os
import random
import sys
import tempfile
import time
import types

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLUGINS_DIR = os.path.join(REPO_ROOT, "src", "plugins")
for _p in (REPO_ROOT, PLUGINS_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

_TMP = tempfile.mkdtemp(prefix="doupo_fix_")
os.environ["DOUPO_DATA_PATH"] = os.path.join(_TMP, "doupo.json")
os.environ["QQ_LOG_DIR"] = os.path.join(_TMP, "logs")
os.environ["QQ_DATA_DIR"] = os.path.join(_TMP, "qdata")
os.environ["DOUPO_CHANCE_RATE"] = "0"
os.environ.setdefault("DOUPO_TOWER_SESSION_PATH",
                      os.path.join(_TMP, "tower_session.json"))


def _stub_nonebot():
    nb = types.ModuleType("nonebot")
    nb.get_driver = lambda: types.SimpleNamespace(on_startup=lambda f: f)
    nb.get_bot = lambda: types.SimpleNamespace(send=lambda *a, **k: None)

    def on_regex(*a, **k):
        m = types.SimpleNamespace()
        m.handle = lambda *a, **k: (lambda f: f)
        m.finish = lambda *a, **k: None
        return m

    nb.on_regex = on_regex
    sys.modules["nonebot"] = nb
    for n in ("nonebot.adapters", "nonebot.adapters.onebot",
              "nonebot.adapters.onebot.v11"):
        sys.modules[n] = types.ModuleType(n)
    sys.modules["nonebot.adapters.onebot.v11"].MessageEvent = object
    sys.modules["nonebot.adapters.onebot.v11"].MessageSegment = \
        types.SimpleNamespace(image=lambda data: ("[image]", len(data or b"")))
    sys.modules["nonebot.adapters.onebot.v11"].Message = object


_stub_nonebot()

from doupo import commands, core, config as cfg  # noqa: E402
from doupo.store import Store  # noqa: E402

G = "900000011"


async def main():
    fixtures = {}
    for i, (lv, seed) in enumerate([(5, 11), (9, 7), (40, 3)], 1):
        qq = "780000%03d" % i
        tmp = tempfile.mkdtemp(prefix="doupo_fix_store_%d_" % i)
        store = Store(os.path.join(tmp, "doupo.json"))
        await store.load()
        p = core.create_player(qq, "夹%d" % i, G)
        p["lv"] = int(lv)
        p["exp"] = 0
        core._refill(p)
        await store.put_player(p)
        p = await store.get_player(qq)
        p["last_settle"] = time.time()
        await store.put_player(p)
        random.seed(seed)
        await commands.cmd_fight(store, qq)
        p = await store.get_player(qq)
        last = p.get("last_battle")
        assert isinstance(last, dict) and last.get("log"), "夹具 %d 没打出 last_battle" % i
        fixtures["fx_lv%d" % lv] = {"qq": qq, "battle": last}
    return fixtures


if __name__ == "__main__":
    data = asyncio.run(main())
    dest = os.path.join(REPO_ROOT, "tools", "fixtures",
                        "doupo_battle_fixtures.json")
    with open(dest, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=1, sort_keys=True)
    print("FIXTURES_WRITTEN %s (%d blocks, %d bytes)" %
          (dest, len(data), os.path.getsize(dest)))
