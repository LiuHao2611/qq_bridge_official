# -*- coding: utf-8 -*-
r"""A4 属性亲和诚实标注 + A5 领悟门槛收敛（2026-11-27）。

A4：炼火焚「需火属性亲和」、青木剑诀「需木属性亲和」是卡面承诺，但本作
没有玩家属性字段，学了也没反噬 —— 玩家按卡面规划全落空。原著 desc 保留
（忠于原文），gate 行必须显式标「未实装属性相克」，不许继续冒充已实现。

A5：旧领悟门槛 `120×已学门数` 战力 + `200+150×门数` 金币，对 lv1 新号
（战力约 2937、初始金币 10000）恒真，从来没拦住过谁。战力门槛已删，
「能不能学」唯一走 Q1 的 `unlock` 境界闸；金币消耗保留（它是真 sink）。

运行：
    python src/plugins/doupo/test_learn_gate_honest.py
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

from doupo import core  # noqa: E402


def _p(lv=10, gold=10000, learned=("碎石掌",)):
    p = core.create_player("a4a5-probe", "门槛探针", "g1")
    p["lv"] = lv
    p["gold"] = int(gold)
    p["learned"] = list(learned)
    p["loadout"] = list(learned)
    p["hp"] = core.stats(p)["体力"]
    return p


def test_affinity_claims_are_labeled_not_implemented():
    """凡是卡面写「属性亲和」的 gate，必须同时写明「未实装」。"""
    from doupo import skill_book_data as d

    bad = []
    for r in getattr(d, "BOOK_ROWS", ()):
        gate = str(r.get("gate") or "")
        if "属性亲和" in gate and "未实装" not in gate:
            bad.append("%s：%s" % (r.get("name"), gate))
    assert not bad, "以下 gate 还在冒充已实装属性相克：\n" + "\n".join(bad)


def test_power_gate_would_be_useless_by_construction():
    """外部基准：lv1 战力就远超旧门槛，证明它不是有效闸门（删它不亏）。"""
    p = _p(lv=1, learned=())
    assert core.power(p) > core.insight_power_need(10), (
        "旧战力门槛居然会拦人？先别删，重新评估 A5")


def test_learn_named_no_longer_blocks_on_power():
    """战力不是门槛了：把已学门数堆到旧门槛之上，仍然必须能学。"""
    from doupo import skill_book_data as d

    names = [r["name"] for r in getattr(d, "BOOK_ROWS", ())
             if r.get("name") != "裂爪击"][:40]
    p = _p(lv=1, gold=10 ** 6, learned=names)
    assert core.power(p) < core.insight_power_need(len(names)), (
        "构造失败：战力没被旧门槛压住，测不出东西")
    r = core.learn_named(p, "裂爪击")
    assert r.get("ok"), "领悟被废弃的战力门槛挡住：%s" % (r.get("reason"),)


def test_gold_gate_still_works():
    p = _p(lv=10, gold=0, learned=("碎石掌",))
    r = core.learn_named(p, "裂爪击")
    assert not r.get("ok") and r.get("reason") == "gold", "金币门槛被误删"


def test_level_gate_still_works():
    """Q1 的境界闸仍是唯一「能不能学」门槛。"""
    p = _p(lv=1, gold=100000, learned=())
    r = core.learn_named(p, "碎石掌")   # 原著三段斗之气才能习（Q1 写进 unlock=3）
    assert not r.get("ok") and r.get("reason") == "level", r
    assert r.get("need_lv") == 3, "报的差几级不是 Q1 的段位闸：%s" % r


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
