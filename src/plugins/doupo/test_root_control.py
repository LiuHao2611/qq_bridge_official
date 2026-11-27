# -*- coding: utf-8 -*-
r"""定身概率 / BOSS 抗性守门（Q5·B3，2026-11-27）。

旧行为：`root` 直接写状态 = 100% 必中，BOSS 也一样；低阶控制技锁死必打关，
新手起手强度取决于随机赠技。现在：
  * 默认成功率 `ROOT_BASE_P`（数据 effects[i].p 只能更低，不能更高）；
  * BOSS / 精英乘 (1 - BOSS_ROOT_RESIST)；
  * 同一目标连续被定身逐次递减，最低保留 20%（不做完全免疫）。

运行：
    python src/plugins/doupo/test_root_control.py
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

from doupo import skill_effects as fx  # noqa: E402


def _root(rng_value: float, ctx=None, target=None, effect=None):
    self_st = fx.new_state()
    target_st = target if target is not None else fx.new_state()
    r = fx.on_hit([effect or {"t": "root", "d": 1}], self_st, target_st,
                  100, lambda: rng_value, ctx or {})
    return target_st, r


def test_normal_monster_root_is_probabilistic():
    assert 0.0 < fx.ROOT_BASE_P < 1.0, "定身不能必中，也不能必失败"
    hit, _ = _root(fx.ROOT_BASE_P - 0.01)
    assert int(hit.get("root") or 0) >= 1
    miss, _ = _root(fx.ROOT_BASE_P + 0.01)
    assert int(miss.get("root") or 0) == 0


def test_boss_has_control_resistance():
    ctx = {"boss_resist": fx.BOSS_ROOT_RESIST}
    eff_p = fx.ROOT_BASE_P * (1.0 - fx.BOSS_ROOT_RESIST)
    hit, _ = _root(eff_p - 0.01, ctx=ctx)
    miss, _ = _root(eff_p + 0.01, ctx=ctx)
    assert int(hit.get("root") or 0) >= 1, "BOSS 不该完全免疫"
    assert int(miss.get("root") or 0) == 0, "BOSS 不该和普通怪一样好定"
    assert eff_p < fx.ROOT_BASE_P / 2, "BOSS 抗性太弱"


def test_repeated_root_diminishes_but_never_zero():
    t = fx.new_state()
    first_p = fx.ROOT_BASE_P
    second_p = fx.ROOT_BASE_P * (1.0 - fx.ROOT_DIMINISH)
    mid = (first_p + second_p) / 2
    after1, _ = _root(first_p - 0.01, target=t)
    assert int(after1.get("root") or 0) >= 1
    after2, r2 = _root(mid, target=t)
    assert "定身未成" in r2["notes"], "连续定身没有递减"
    assert int(after2.get("root_times") or 0) == 1, "失败也计入了定身次数"
    assert fx.ROOT_MIN_FACTOR > 0, "递减不能把定身彻底废掉"


def test_data_cannot_raise_root_above_base():
    _, r1 = _root(0.7, effect={"t": "root", "d": 1, "p": 1.0})
    assert "定身未成" in r1["notes"], "数据里写 p=1 又把定身变必中"
    _, r2 = _root(0.1, effect={"t": "root", "d": 1, "p": 0.2})
    assert "定身" in r2["notes"] and "定身未成" not in r2["notes"]


def test_battle_passes_boss_resist_into_ctx():
    import doupo.battle_core as bc
    src = inspect.getsource(bc.fight)
    assert "boss_resist" in src and "BOSS_ROOT_RESIST" in src, (
        "战斗没把 BOSS 抗性传进效果上下文")
    # 普通怪必须传 0，否则新手怪也会被当 BOSS 抗定身。
    assert 'else 0.0' in src
    esrc = inspect.getsource(fx.on_hit)
    assert "boss_resist" in esrc and "ROOT_BASE_P" in esrc


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
