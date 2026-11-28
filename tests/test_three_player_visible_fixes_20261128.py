# -*- coding: utf-8 -*-
"""2026-11-28 三项玩家可见反馈的守门：

1. 「斗技」卡要看到当前焚血散数量 + 每门升下一级的焚血散消耗；
2. 「服丹 气修丹 3」实际生效三枚，回包不能只显示最后一枚；
3. 自动爬塔结算不能只说「宝箱 N 次」，要说清宝箱和层数奖励开出了什么。

运行：python tests/test_three_player_visible_fixes_20261128.py
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_TMP = tempfile.mkdtemp(prefix="qb_three_fixes_")
os.environ["DOUPO_DATA_PATH"] = os.path.join(_TMP, "doupo.json")
os.environ["XIUXIAN_DATA_PATH"] = os.path.join(_TMP, "xiuxian.json")
os.environ["QQ_BRIDGE_CACHE_DIR"] = _TMP


def _stub_nonebot():
    nb = types.ModuleType("nonebot")
    nb.get_driver = lambda: types.SimpleNamespace(on_startup=lambda f: f)

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
    v11 = sys.modules["nonebot.adapters.onebot.v11"]
    v11.MessageEvent = object
    v11.MessageSegment = object
    v11.Message = object


_stub_nonebot()
for _p in (ROOT, os.path.join(ROOT, "src", "plugins")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from doupo import card, commands, config as cfg, core, tower_session as ts  # noqa: E402

_RES: list[tuple[str, bool, str]] = []


def check(name, ok, detail=""):
    _RES.append((name, bool(ok), str(detail)))


class MemStore:
    def __init__(self, p):
        self.p = p

    async def get_player(self, qq):
        return self.p

    async def put_player(self, p):
        self.p = p


def _p(qq, lv=70):
    p = core.create_player(qq, "探针", "群")
    p["lv"] = lv
    p["hp"] = core.stats(p)["体力"]
    p["mp"] = core.mp_max(p)
    return p


def test_learned_card_shows_material_and_cost():
    p = _p("CARD1")
    p["learned"] = ["血煞掌"]
    p["loadout"] = ["血煞掌"]
    p["skills"] = {"血煞掌": 2}
    core.add_item(p, cfg.SKILL_UP_ITEM, 7)
    data = card.build_learned_data(p)
    check("卡顶显示当前焚血散持有量",
          data.get("item") == cfg.SKILL_UP_ITEM
          and int(data.get("item_count") or -1) == 7
          and f"{cfg.SKILL_UP_ITEM} ×7" in str(data.get("subtitle")),
          str(data.get("subtitle")))
    e = core.loadout_entries(p)[0]
    row = next(r for r in data["rows"] if r["name"] == "血煞掌")
    want = core.skill_up_cost(2, e)
    check("每门斗技显示升下一级所需焚血散",
          int(row.get("up_cost") or -1) == want
          and any(c.get("label") == cfg.SKILL_UP_ITEM
                  and c.get("value") == f"升3 ×{want}"
                  for c in row["chips"]),
          f"up_cost={row.get('up_cost')} want={want} chips={row['chips']}")

    # 满级不能给出假消耗（用户会被误导继续囤药）
    p["skills"] = {"血煞掌": core.skill_max(e)}
    full = next(r for r in card.build_learned_data(p)["rows"]
                if r["name"] == "血煞掌")
    check("满级斗技标已满、消耗归零",
          full.get("maxed") is True and int(full.get("up_cost") if full.get("up_cost") is not None else -1) == 0
          and any(c.get("label") == cfg.SKILL_UP_ITEM
                  and c.get("value") == "已满" for c in full["chips"]),
          f"up_cost={full.get('up_cost')} chips={full['chips']}")


def test_multi_pill_reply_shows_every_effect():
    p = _p("PILL1")
    p["mp"] = 0
    core.add_item(p, "气修丹", 3)
    store = MemStore(p)
    out = asyncio.run(commands.cmd_use(store, "PILL1", "气修丹 3"))
    check("连服三枚逐枚显示真实效果",
          out.count("斗气 +") == 3 and "连服【气修丹】3 枚" in out, out[:400])
    check("三枚丹药确实都生效（不是只改文案）",
          int(store.p.get("mp") or 0) >= 3 * 1400,
          f"mp={store.p.get('mp')}")


def test_auto_tower_settlement_lists_loot():
    p = _p("AUTO1")
    store = MemStore(p)
    ts.STORE.clear("AUTO1")
    orig = core.tower_open_chest
    calls = {"n": 0}

    def spy(p, st, rng=None, floor=None, chest_bonus=0.0):
        calls["n"] += 1
        if calls["n"] == 1:
            return [{"type": "material", "name": "炎星石", "n": 2}]
        return []

    core.tower_open_chest = spy
    try:
        out = asyncio.run(commands.cmd_auto_tower(store, "AUTO1", "否"))
    finally:
        core.tower_open_chest = orig
        ts.STORE.clear("AUTO1")
    check("自动结算展示宝箱开出的具体战利品",
          "宝箱开出：炎星石×2" in out, out[:800])
    check("自动结算展示层数奖励（不再只说箱子）",
          "🎁 层数奖励：" in out
          and any("×" in ln for ln in out.splitlines() if "层数奖励" in ln),
          out[:800])


def main():
    for fn in (test_learned_card_shows_material_and_cost,
               test_multi_pill_reply_shows_every_effect,
               test_auto_tower_settlement_lists_loot):
        try:
            fn()
        except Exception as exc:  # noqa: BLE001
            check(fn.__name__ + " 未抛异常", False, repr(exc))
    bad = [x for x in _RES if not x[1]]
    for name, ok, detail in _RES:
        print(("PASS " if ok else "FAIL ") + name + ((" | " + detail) if not ok else ""))
    print("TOTAL=%d PASS=%d FAIL=%d" % (len(_RES), len(_RES) - len(bad), len(bad)))
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
