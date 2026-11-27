# -*- coding: utf-8 -*-
r"""C1 金币 sink 跟随产出守门（2026-11-27）。

旧行为：金币产出随等级线性放大（lv20 单场 2222 → lv88 单场 43022，19.4×），
但转宗费只从 7000 涨到 20600（4.1×）—— 后期金币失去约束力，固定价目被通胀稀释。

现在：转宗费加立方项，让高等级转宗费跟单场产出同量级（lv88 ≈ 2 场金币）。
升技消耗随品阶缩放见 A6（`test_skill_upgrade_tier`）。

运行：
    python src/plugins/doupo/test_gold_sink.py
"""
from __future__ import annotations

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

from doupo import config as cfg  # noqa: E402

#: 报告实测的单场金币（外部基准，不是抄代码输出）。
GOLD_PER_FIGHT = {20: 2222, 50: 13889, 88: 43022}


def test_lv1_cost_unchanged():
    """lv1 不能涨——新人第一次转宗的口径保持原值。"""
    assert cfg.faction_switch_cost(1) == (
        cfg.FACTION_SWITCH_COST_BASE + cfg.FACTION_SWITCH_COST_PER_LV)


def test_cost_grows_superlinearly():
    """产出 19.4 倍，转宗费不能只涨 4 倍（线性项已被证明跟不上）。"""
    ratio = cfg.faction_switch_cost(88) / cfg.faction_switch_cost(20)
    assert ratio > 6.0, "转宗费仍被产出曲线甩开（只涨 %.1f 倍）" % ratio


def test_cost_tracks_gold_output():
    """高等级转宗费应落在 1.5~3 场金币之间：太便宜没约束力，太贵等于禁止转宗。"""
    for lv, per in GOLD_PER_FIGHT.items():
        span = cfg.faction_switch_cost(lv) / per
        assert 1.0 <= span <= 5.0, "lv%d 转宗费 = %.1f 场金币" % (lv, span)


def test_cost_monotonic_every_level():
    vals = [cfg.faction_switch_cost(lv) for lv in range(1, 104)]
    assert all(b > a for a, b in zip(vals, vals[1:])), "转宗费出现平台或倒退"


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
