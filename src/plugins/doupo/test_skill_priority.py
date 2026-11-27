# -*- coding: utf-8 -*-
r"""B5 选招条件优先守门（2026-11-27）。

旧行为：四槽上阵后每回合**均匀随机**放技（真跑 208 次出手 ≈25% 均分），
玩家不能「这回合先控住」，配招没有决策感。

现在只做两条最直观的条件、不引入手动操作：
  * 自己血量 < 35% → 候选收窄到「防御 / 续航」技（保命）；
  * 目标血量 < 30% → 候选收窄到倍率最高的技（斩杀）；
  * 两者同时满足 → 保命优先。
``ctx=None``（纯计算/既有测试）逐字节沿用旧口径。

运行：
    python src/plugins/doupo/test_skill_priority.py
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
    for attr in ("Message", "MessageEvent", "MessageSegment"):
        setattr(sys.modules["nonebot.adapters.onebot.v11"], attr, object)


_stub_nonebot()

from doupo import core  # noqa: E402


def _p(skills=("玄岩盾", "青木剑诀", "古帝碎涅指", "碎石掌"), lv=90):
    p = core.create_player("b5-probe", "选招探针", "g1")
    p["lv"] = lv
    p["learned"] = list(skills)
    p["loadout"] = list(skills)
    p["hp"] = core.stats(p)["体力"]
    return p


def _pick(p, ctx=None, rng=lambda: 0.99):
    """默认 rng=0.99：旧均匀随机会取最后一个（碎石掌），一定有区分度。"""
    return core._pick_skill(p, core.mp_max(p), rng=rng, ctx=ctx)[0]


def test_no_ctx_keeps_old_uniform_behavior():
    p = _p()
    a = core._pick_skill(p, core.mp_max(p), rng=lambda: 0.42)
    b = core._pick_skill(p, core.mp_max(p), rng=lambda: 0.42, ctx=None)
    assert a == b, "不传态势时行为被改变"


def test_low_self_hp_picks_defensive_skill():
    p = _p()
    e = _pick(p, {"hp_ratio": 0.20, "foe_hp_ratio": 1.0})
    assert e is not None and str(e.get("role")) in ("防御", "续航"), (
        "残血没优先保命技，实际选了 %s" % (e or {}).get("name"))


def test_low_foe_hp_picks_burst_skill():
    p = _p()
    e = _pick(p, {"hp_ratio": 1.0, "foe_hp_ratio": 0.15}, rng=lambda: 0.0)
    best = max((x for x in core._loadout_entries(p)),
               key=lambda x: float(x.get("dmg") or 0))
    assert e is not None and e["name"] == best["name"], (
        "目标残血没选最高倍率，实际选了 %s" % (e or {}).get("name"))


def test_both_low_prefers_survival():
    p = _p()
    e = _pick(p, {"hp_ratio": 0.10, "foe_hp_ratio": 0.05})
    assert str(e.get("role")) in ("防御", "续航"), "双方残血没先保命"


def test_no_defensive_skill_falls_back_safely():
    p = _p(skills=("碎石掌", "古帝碎涅指"))
    e = _pick(p, {"hp_ratio": 0.10, "foe_hp_ratio": 1.0})
    assert e is not None, "没有防御技时选招崩了"


def test_fight_and_duel_pass_battle_context():
    import doupo.battle_core as bc

    fsrc = inspect.getsource(bc.fight)
    assert "hp_ratio" in fsrc and "foe_hp_ratio" in fsrc, "fight 没传血量态势"
    dsrc = inspect.getsource(core.duel)
    assert dsrc.count("hp_ratio") >= 2, "duel 没给双方各传态势"


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
