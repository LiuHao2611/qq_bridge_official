# -*- coding: utf-8 -*-
r"""B7 熔断判胜守门（2026-11-27）。

旧行为：打满 ``HARD_ROUND_CAP``(300) 回合时按「玩家剩余血量比例 > 怪剩余血量比例」
判胜。怪血量按等级反解、可被章节门禁抬到十几倍，于是出现：
玩家血满、怪还剩 83%，1.0 > 0.17 照样判**胜**并发全额经验/金币/掉落 —— 可刷奖励。

现在：只认**对怪伤害进度 > 50%**（``HARD_ROUND_WIN_PROGRESS``）。不到一半判负，
不发奖。PVP 的同名熔断不动（两个真人之间血量比例才合理）。

运行：
    python src/plugins/doupo/test_hard_round_verdict.py
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


def test_progress_rule_basics():
    v = bc.hard_round_verdict
    assert v(100, 1000) is True, "打掉 90% 还不算胜"
    assert v(830, 1000) is False, "报告里的刷奖励场景（怪剩 83%）仍判胜"
    assert v(500, 1000) is False, "刚好 50% 不该算胜（要「超过」）"
    assert v(499, 1000) is True
    assert v(0, 1000) is True, "怪被清空必须算胜"
    assert v(0, 0) is True, "血量上限脏数据不该判负"
    assert v(10 ** 9, 10 ** 9) is False


def test_constant_is_the_documented_half():
    assert 0.0 < bc.HARD_ROUND_WIN_PROGRESS < 1.0
    assert bc.HARD_ROUND_WIN_PROGRESS == 0.5, "门槛被人偷偷调低就失去防刷意义"


def _player(lv=30):
    p = core.create_player("b7-probe", "熔断探针", "g1")
    p["lv"] = lv
    p["learned"] = []
    p["loadout"] = []
    p["skills"] = {}
    p["bag"] = {}
    p["hp"] = core.stats(p)["体力"]
    p["mp"] = core.mp_max(p)
    return p


def _fight(p, mon, seed=13):
    return bc.fight(p, mon, rng=random.Random(seed).random, roll_aid=False,
                    auto_pill=False)


def test_exhausted_lose_grants_nothing():
    """报告的刷奖励场景：打满 300 回合、怪几乎满血 —— 必须判负且一分奖励不给。"""
    p = _player()
    mon = core.monster_scaled(p, cfg.MONSTERS[0], at_lv=30)
    mon["hp"] = 10 ** 9          # 打不动
    mon["atk"] = 1               # 也打不死玩家 → 必进熔断分支
    mon["def"] = 0
    mon["dodge"] = 0.0
    mon.pop("skill", None)
    exp0, gold0, wins0 = p.get("exp", 0), p.get("gold", 0), p.get("wins", 0)
    res = _fight(p, mon)
    assert res["rounds"] >= int(cfg.HARD_ROUND_CAP), "没跑到熔断，用例无效"
    assert res["exhausted"] is True
    assert res["win"] is False, "怪还剩 99.99% 血却判胜（刷奖励漏洞）"
    assert "exp" not in res and "drops" not in res, "力竭判负还发了奖励"
    assert p.get("exp", 0) == exp0 and p.get("gold", 0) == gold0
    assert p.get("wins", 0) == wins0


def test_normal_kill_still_wins_without_exhausted():
    p = _player()
    mon = core.monster_scaled(p, cfg.MONSTERS[0], at_lv=30)
    res = _fight(p, mon)
    assert res["win"] is True and res["exhausted"] is False, "正常击杀被 B7 改坏了"
    assert "exp" in res


def test_duel_cap_is_not_touched():
    """PVP 熔断仍是血量比例口径：两个真人之间进度 50% 不是合理门槛。"""
    src = inspect.getsource(core.duel)
    assert "hard_round_verdict" not in src, "PVP 被套上了 PVE 的门槛"


def test_fight_is_wired():
    src = inspect.getsource(bc.fight)
    assert "hard_round_verdict" in src, "战斗没接 B7 判胜"
    assert "ratio_m" not in src.split("win = m_hp <= 0")[-1][:400], (
        "旧的「血量比例判胜」还留着")


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
