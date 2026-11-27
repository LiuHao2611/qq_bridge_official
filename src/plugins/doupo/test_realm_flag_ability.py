# -*- coding: utf-8 -*-
r"""A7 境界标志能力守门（2026-11-27）。

旧行为：突破斗王只弹一句「斗气化翼，第一次离地三尺」，战斗里没有飞行/减伤，
境界纯粹是数字变大 —— 玩家突破完回去打同一只怪，感受不到任何变化。

现在只做两条（清单 A7 建议「别一次做全」）：
  * 斗王起「斗气化翼」：离地三尺，兽类普通扑击够不到实处 → 承伤 ×0.90；
  * 斗宗起「空间锁」：能量类怪技/大招被空间扭曲 → 承伤 ×0.85。
两条**叠加**（斗宗同时享斗王那条），低于斗王恒 1.0（老玩家逐字节不变）。

运行：
    python src/plugins/doupo/test_realm_flag_ability.py
"""
from __future__ import annotations

import inspect
import os
import random
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
    for attr in ("Message", "MessageEvent", "MessageSegment"):
        setattr(sys.modules["nonebot.adapters.onebot.v11"], attr, object)


_stub_nonebot()

from doupo import battle_core as bc  # noqa: E402
from doupo import config as cfg  # noqa: E402
from doupo import core  # noqa: E402


# ---------------------------------------------------------------------------
# 纯函数：边界必须锚在**境界名**上，不许写魔法数字
# ---------------------------------------------------------------------------
def test_boundaries_are_anchored_to_realm_names():
    assert cfg.realm_lv(core.FLIGHT_REALM) == 46, "斗王起始等级不是 46"
    assert cfg.realm_name(46) == "斗王"
    assert cfg.realm_name(45) == "斗灵", "斗王前一级应仍是斗灵"
    assert cfg.realm_lv(core.SPACE_REALM) == 64, "斗宗起始等级不是 64"
    assert cfg.realm_name(64) == "斗宗"
    assert cfg.realm_name(63) == "斗皇"


def test_below_douwang_is_exactly_neutral():
    for lv in (1, 20, 45):
        assert core.realm_flag_taken_mult(lv) == 1.0, "斗王前被偷偷减伤了"
        assert core.realm_flag_taken_mult(lv, skill=True) == 1.0
    # 非法等级按 1.0（不猜、不抛），避免坏档把玩家打成纸人
    for bad in (None, "", "x"):
        assert core.realm_flag_taken_mult(bad) == 1.0


def test_douwang_cuts_plain_hits_only():
    assert core.realm_flag_taken_mult(46) == core.FLIGHT_TAKEN_MULT
    assert core.realm_flag_taken_mult(54) == core.FLIGHT_TAKEN_MULT
    # 斗王还没有空间锁，怪技照吃
    assert core.realm_flag_taken_mult(54, skill=True) == 1.0
    assert 0.5 < core.FLIGHT_TAKEN_MULT < 1.0, "化翼减伤幅度不现实"


def test_douzong_stacks_both_not_either_or():
    """🚨 斗宗必须**同时**享两条：只给空间锁、把化翼丢掉 = 升境界掉防御。"""
    assert core.realm_flag_taken_mult(64) == core.realm_flag_taken_mult(46), (
        "斗宗被普通扑击打时丢了化翼减伤")
    assert core.realm_flag_taken_mult(46) < 1.0, "化翼那条本身就没生效"
    assert core.realm_flag_taken_mult(64, skill=True) == core.SPACE_TAKEN_MULT
    assert core.SPACE_TAKEN_MULT < core.FLIGHT_TAKEN_MULT, "空间锁该比化翼更硬"


# ---------------------------------------------------------------------------
# 真战斗：同玩家、同种子、只切境界标志，差值必须真实存在
# ---------------------------------------------------------------------------
def _player(lv):
    p = core.create_player("a7-probe", "境界探针", "g1")
    p["lv"] = lv
    p["learned"] = []
    p["loadout"] = []          # 不出技：不吃斗气、不消耗选招 rng
    p["bag"] = {}
    p["skills"] = {}
    p["hp"] = core.stats(p)["体力"]
    p["mp"] = core.mp_max(p)
    return p


def _mon(lv, skill=None):
    mon = core.monster_scaled(_player(lv), cfg.MONSTERS[0], at_lv=lv)
    mon["hp"] = 10 ** 9            # 打不死：整场都在承伤
    mon["atk"] = 200               # 打得死玩家就没法累计，压低
    mon["def"] = 0
    mon["dodge"] = 0.0
    if skill is None:
        mon.pop("skill", None)
    else:
        mon["skill"] = dict(skill)
    return mon


def _run(lv, seed=7, skill=None, patch_neutral=False):
    """跑一场；``patch_neutral`` 把境界标志打成恒 1.0（对照组）。"""
    p = _player(lv)
    mon = _mon(lv, skill)
    orig = core.realm_flag_taken_mult
    if patch_neutral:
        core.realm_flag_taken_mult = lambda *a, **k: 1.0
    try:
        return bc.fight(p, mon, rng=random.Random(seed).random, roll_aid=False,
                        auto_pill=False)
    finally:
        core.realm_flag_taken_mult = orig


def _notes(res):
    out = []
    for row in res["log"]:
        out.extend(str(n) for n in (row.get("notes") or ()))
    return out


def test_below_douwang_battle_is_byte_identical():
    a = _run(45)
    b = _run(45, patch_neutral=True)
    assert a["taken"] == b["taken"], "斗灵的战斗被境界标志改动了"
    assert a["rounds"] == b["rounds"]
    assert a["log"] == b["log"], "低境界战报与改动前不逐字节一致"


def test_douwang_really_takes_less_from_plain_hits():
    on = _run(46)
    off = _run(46, patch_neutral=True)
    assert off["taken"] > 0, "对照组没吃到伤害，用例无效"
    assert on["taken"] < off["taken"], "斗王没有减伤，机制是死的"
    assert on["taken"] >= int(off["taken"] * 0.5), "减伤过头了（一滴血不掉）"
    assert any("化翼" in n for n in _notes(on)), "战报里没提化翼，玩家看不见"


def test_douzong_really_takes_less_from_monster_skills():
    sk = {"name": "试技", "chance": 1.0, "effect": "burst", "mult": 1.5}
    on = _run(64, skill=sk)
    off = _run(64, skill=sk, patch_neutral=True)
    assert off["taken"] > 0
    assert on["taken"] < off["taken"], "斗宗的空间锁没生效"
    assert any("空间锁" in n for n in _notes(on)), "战报里没提空间锁"


def test_fight_is_wired_to_the_single_entry():
    src = inspect.getsource(bc.fight)
    assert "realm_flag_taken_mult" in src, "战斗没接境界标志（只写了函数没人调）"
    # 必须按「本次是不是怪技」分流，否则两条能力退化成一条
    assert "_sk_hit" in src.split("realm_flag_taken_mult")[1][:200], (
        "没把怪技与否传给境界标志")
    esrc = inspect.getsource(core.realm_flag_taken_mult)
    assert "FLIGHT_TAKEN_MULT" in esrc and "SPACE_TAKEN_MULT" in esrc


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
