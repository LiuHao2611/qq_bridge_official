# -*- coding: utf-8 -*-
r"""C4 炼药成功率上限收紧守门（2026-11-27）。

旧行为：``craft_rate_for`` 一律封 ``CRAFT_RATE_CAP=0.95``，而 ``craft_rate(九品)``
本身就是 0.55+0.05×8 = 0.95 —— 九品炼药师**不带任何异火**也恒九成五，
「炼药师品阶」到顶之后与「异火提成功率」这条养成线一起失去意义。

现在（C4）：0.95 只留给「满级炼药师（九品）+ 异火」，其余封 0.90。异火仍只在
最后加一次、仍共用同一个上限，不新增第二条加成路径。

运行：
    python src/plugins/doupo/test_craft_rate_cap.py
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

TOP = cfg.ALCH_MAX          # 九品
FLAME = 0.08                # 异火合成加成（B 线正常档位）


def test_caps_are_ordered_and_documented():
    assert cfg.CRAFT_RATE_CAP_PLAIN < cfg.CRAFT_RATE_CAP
    assert cfg.CRAFT_RATE_CAP_PLAIN == 0.90 and cfg.CRAFT_RATE_CAP == 0.95


def test_top_alchemist_without_flame_no_longer_hits_cap():
    rate, diff = cfg.craft_rate_for(TOP, cfg.alch_tier(TOP)[0], 0.0)
    assert diff == 0
    assert rate == cfg.CRAFT_RATE_CAP_PLAIN, "九品不带火仍是 0.95（C4 没生效）"
    assert rate < cfg.CRAFT_RATE_CAP


def test_top_alchemist_with_flame_hits_the_true_cap():
    rate, _ = cfg.craft_rate_for(TOP, cfg.alch_tier(TOP)[0], FLAME)
    assert rate == cfg.CRAFT_RATE_CAP, "满级 + 异火也够不到 0.95，门槛立死了"


def test_lower_tier_cannot_reach_the_true_cap_even_with_flame():
    for idx in (cfg.ALCH_NONE, 0, 3, TOP - 1):
        rate, _ = cfg.craft_rate_for(idx, cfg.alch_tier(idx)[0], FLAME)
        assert rate <= cfg.CRAFT_RATE_CAP_PLAIN, (
            "第 %d 品借异火摸到了 0.95（C4 只该留给满级）" % idx)


def test_overreach_penalty_still_applies_under_new_cap():
    """门槛收紧不许顺手改掉越阶惩罚（越 1 档仍是 ×0.35）。"""
    base = cfg.craft_rate(3) + FLAME
    rate, diff = cfg.craft_rate_for(3, "五品", FLAME)     # 四品炼五品：越 1 档
    assert diff == 1
    assert abs(rate - min(cfg.CRAFT_RATE_CAP_PLAIN, base * 0.35)) < 1e-9
    deep, d2 = cfg.craft_rate_for(cfg.ALCH_NONE, "七品")
    assert d2 >= 2 and deep <= 0.05


def test_single_entry_recipe_path_matches():
    """丹方通道与直调必须同源，否则卡面与实炼两套数（历史坑）。"""
    rec = {"grade": cfg.alch_tier(TOP)[0]}
    p = {"alch": TOP}
    assert cfg.rate_for_recipe(p, rec, 0.0)[0] == cfg.craft_rate_for(TOP, rec["grade"], 0.0)[0]
    assert cfg.rate_for_recipe(p, rec, FLAME)[0] == cfg.CRAFT_RATE_CAP
    src = inspect.getsource(core.recipe_rate)
    assert "rate_for_recipe" in src, "实炼没走唯一入口"


def test_card_and_craft_read_the_same_function():
    from doupo import card
    src = inspect.getsource(card)
    assert "craft_rate_for" in src, "卡面另算了一套成功率"


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
