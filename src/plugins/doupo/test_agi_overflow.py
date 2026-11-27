# -*- coding: utf-8 -*-
r"""B4 敏捷溢出兑换守门（2026-11-27）。

旧行为：敏捷同时产闪避与暴击，两者各封顶 80。lv55 起暴击恒 80、lv82 起闪避
恒 80，再堆 10 万敏捷只涨面板数字与战力，战斗里一动不动，而人物卡仍写
「敏捷 = 闪避·暴击」—— 后期敏捷成废属性。

现在：自然溢出不再作废。暴击溢出 1:0.5 折暴伤（接 ``core.crit_mul``），
闪避溢出 1:0.5 折命中（接 ``core.hit_rate``，90% 封顶兜住）。
只兑换**自然部分**（敏捷 + 净莲妖火 + 太虚加点）：丹药 ``bonus["暴击"]`` /
``bonus["闪避"]`` 不参与，否则会和 ``dodge_final`` 的「封顶后另计 min(8,pill)」
两边打架。

运行：
    python src/plugins/doupo/test_agi_overflow.py
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

from doupo import config as cfg  # noqa: E402
from doupo import core  # noqa: E402


def _p(lv=1, agi=0):
    p = core.create_player("b4-probe", "溢出探针", "g1")
    p["lv"] = lv
    p.setdefault("bonus", {})
    if agi:
        p["bonus"]["敏捷"] = int(agi)
    p["hp"] = core.stats(p)["体力"]
    return p


def test_no_overflow_keeps_old_value():
    """没溢出时逐字节沿用旧口径（既有战斗零回归）。"""
    p = _p(lv=1)
    assert core.crit_overflow_dmg(p) == 0.0
    assert core.dodge_overflow_hit(p) == 0.0
    assert core.crit_mul(p) == cfg.crit_mul(p), "未溢出就改了暴击倍率"


def test_crit_overflow_converts_to_crit_damage():
    p = _p(lv=60)
    before = core.crit_mul(p)
    p["bonus"]["敏捷"] = 100000
    assert core.crit_rate(p) == 80.0, "暴击率面板应仍封顶 80"
    assert core.crit_overflow_dmg(p) > 0, "暴击溢出没转暴伤"
    assert core.crit_mul(p) > before, "暴伤没随溢出上涨"


def test_dodge_overflow_converts_to_hit_and_respects_cap():
    p = _p(lv=90)
    p["bonus"]["敏捷"] = 400000
    assert core.dodge_rate(p) <= 80.0, "闪避面板应仍封顶 80"
    assert core.dodge_overflow_hit(p) > 0, "闪避溢出没转命中"
    assert core.hit_rate(p) <= cfg.HIT_CAP, "命中破了 90% 封顶"


def test_pill_bonus_does_not_count_as_overflow():
    """丹药暴击/闪避是封顶后另计的，不能混进自然溢出再赚一次。"""
    p = _p(lv=85, agi=100000)
    crit_before = core.crit_overflow_dmg(p)
    hit_before = core.dodge_overflow_hit(p)
    p["bonus"]["暴击"] = 40
    p["bonus"]["闪避"] = 40
    assert core.crit_overflow_dmg(p) == crit_before, "丹药暴击被算进溢出了"
    assert core.dodge_overflow_hit(p) == hit_before, "丹药闪避被算进溢出了"


def test_hit_damage_really_grows_in_battle():
    """真打一下：同一必暴 rng 下，溢出后的伤害必须更高。"""
    p = _p(lv=60)
    low = core._hit(p, 100, None, lambda: 0.0)["dmg"]
    p["bonus"]["敏捷"] = 100000
    high = core._hit(p, 100, None, lambda: 0.0)["dmg"]
    assert high > low, "溢出只涨面板，真打伤害没变（假属性）"


def test_hit_paths_use_core_crit_mul():
    """战斗两处结算必须读 core.crit_mul，不许绕回 cfg.crit_mul。"""
    src = inspect.getsource(core._hit)
    assert "crit_mul(p)" in src and "cfg.crit_mul" not in src, "_hit 没走统一入口"
    esrc = inspect.getsource(core.damage_schedule)
    assert "crit_mul(p)" in esrc and "cfg.crit_mul" not in esrc, "期望伤害没走统一入口"


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
