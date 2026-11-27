# -*- coding: utf-8 -*-
r"""D4 Boss 层宝箱补偿守门（2026-11-27）。

旧行为：「无加成自动爬塔」用 ``chest_bonus=0.6`` 补偿放弃的三选一加成，但 Boss 层
宝箱率本就是 1.0，``min(1.0, 1.0×1.6)`` 把补偿整段吃掉 —— 回执却还写着
「无加成硬闯，宝箱掉落已提高」。承诺在 Boss 层不成立。

现在：被 ``min`` 吃掉的溢出率折算成额外炎星石（``td.CHEST_BONUS_STONE_RATE``），
单独记一条 ``comp`` 掉落，回执显式写「无加成补偿」。

运行：
    python src/plugins/doupo/test_boss_chest_comp.py
"""
from __future__ import annotations

import inspect
import os
import sys
import types

_HERE = os.path.dirname(os.path.abspath(__file__))
_PLUGINS = os.path.dirname(_HERE)
_ROOT = os.path.dirname(os.path.dirname(_PLUGINS))
for _p in (_ROOT, _PLUGINS):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def _stub_nonebot():
    nb = types.ModuleType("nonebot")
    nb.get_driver = lambda: types.SimpleNamespace(on_startup=lambda f: f)
    nb.get_bot = lambda: types.SimpleNamespace(send=lambda *a, **k: None)

    def on_regex(*a, **k):
        return types.SimpleNamespace(handle=lambda *a, **k: (lambda f: f),
                                     finish=lambda *a, **k: None)
    nb.on_regex = on_regex
    sys.modules["nonebot"] = nb
    for mod in ("nonebot.adapters", "nonebot.adapters.onebot",
                "nonebot.adapters.onebot.v11"):
        sys.modules[mod] = types.ModuleType(mod)
    for attr in ("Message", "MessageEvent", "MessageBody", "MessageSegment"):
        setattr(sys.modules["nonebot.adapters.onebot.v11"], attr, object)


_stub_nonebot()

from doupo import commands  # noqa: E402
from doupo import config as cfg  # noqa: E402
from doupo import core  # noqa: E402
from doupo import tower_data as td  # noqa: E402

BOSS_FLOOR = 10
NORMAL_FLOOR = 1


def _rng(vals):
    it = iter(vals)
    return lambda: next(it, 0.99)


def _player():
    p = core.create_player("d4-probe", "宝箱探针", "g1")
    p["tower_paid_floor"] = 0
    p["bag"] = {}
    p["weapons"] = {}
    return p


def _stones(p):
    return core.count_item(p, "炎星石")


def test_boss_rate_is_already_full():
    assert td.chest_rate(BOSS_FLOOR) == 1.0
    assert td.chest_rate(NORMAL_FLOOR) < 1.0


def test_boss_floor_compensates_with_stones():
    p = _player()
    before = _stones(p)
    out = core.tower_open_chest(p, {}, _rng([0.0, 0.5, 0.0]),
                                floor=BOSS_FLOOR, chest_bonus=0.6)
    comp = [c for c in out if c.get("comp")]
    assert comp, "Boss 层无加成补偿没兑现（D4 没生效）"
    assert comp[0]["name"] == "炎星石" and int(comp[0]["n"]) >= 1
    assert _stones(p) - before == sum(int(c["n"]) for c in out
                                      if c["name"] == "炎星石")


def test_comp_scales_with_the_wasted_rate():
    small = [c for c in core.tower_open_chest(_player(), {}, _rng([0.0, 0.5, 0.0]),
                                              floor=BOSS_FLOOR, chest_bonus=0.2)
             if c.get("comp")]
    big = [c for c in core.tower_open_chest(_player(), {}, _rng([0.0, 0.5, 0.0]),
                                            floor=BOSS_FLOOR, chest_bonus=0.6)
           if c.get("comp")]
    assert small and big
    assert int(big[0]["n"]) > int(small[0]["n"]), "补偿量与放弃的加成脱钩"


def test_normal_floor_gets_no_comp():
    """普通层没被 min 吃掉，不许白送补偿（否则等于全民加石头）。"""
    out = core.tower_open_chest(_player(), {}, _rng([0.0, 0.5, 0.0]),
                                floor=NORMAL_FLOOR, chest_bonus=0.6)
    assert out and not any(c.get("comp") for c in out)


def test_no_bonus_keeps_old_behaviour_exactly():
    """chest_bonus=0（手动爬塔）时掉落结构必须与改造前一致：没有 comp 条目。"""
    out = core.tower_open_chest(_player(), {}, _rng([0.0, 0.5, 0.0]),
                                floor=BOSS_FLOOR, chest_bonus=0.0)
    assert [c["name"] for c in out] == ["炎星石"]
    assert out[0]["n"] == 1 and not out[0].get("comp")


def test_weapon_drop_still_gets_comp():
    """Boss 层开出神兵时补偿也不能丢（旧版这里 return 直接跑掉了）。"""
    p = _player()
    out = core.tower_open_chest(p, {}, _rng([0.0, 0.0, 0.0]),
                                floor=BOSS_FLOOR, chest_bonus=0.6)
    assert any(c["type"] == "weapon" for c in out)
    assert any(c.get("comp") for c in out), "出神兵就把补偿吞了"


def test_auto_tower_reports_comp_in_text():
    src = inspect.getsource(commands.cmd_auto_tower)
    assert "comp_stone" in src, "自动爬塔没统计补偿量"
    assert "无加成补偿" in src, "补偿进了背包却不告诉玩家"
    manual = inspect.getsource(commands.cmd_tower)
    assert "无加成补偿" in manual, "手动爬塔回执不认 comp 条目"
    assert cfg is not None


if __name__ == "__main__":
    fns = sorted(k for k in globals() if k.startswith("test_"))
    fails = 0
    for fn in fns:
        try:
            globals()[fn]()
            print("PASS", fn)
        except Exception as exc:  # noqa: BLE001
            fails += 1
            print("FAIL", fn, exc)
    print("== %d passed, %d failed ==" % (len(fns) - fails, fails))
    sys.exit(1 if fails else 0)
