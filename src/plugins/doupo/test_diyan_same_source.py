# -*- coding: utf-8 -*-
r"""E2 帝炎加成 / 突破文案同源守门（2026-11-27）。

审计报告 E2 原写「core.stats 没有帝炎分支、突破实际封顶 90% 却写 100%」。
2026-11-27 真跑复核：`core.stats` 对 `p["diyan"]` 有五维 ×1.2 分支，
`cfg.tiandi_rate(luck, True)` 返回 1.0 —— 报告这两条属误报。
**唯一真实缺口**：帮助卡写「30% + 机缘/2000」，没写非帝炎的 90% 上限，
机缘堆过 1200 后玩家会以为还能继续涨。

本守门锁两件事：
  1. 帝炎五维真 ×1.2、突破真 100%（防以后被谁删掉）；
  2. 帮助卡的上限数字必须等于 ``tiandi_rate`` 的实际封顶（同源，不手抄）。

运行：
    python src/plugins/doupo/test_diyan_same_source.py
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


def _p(diyan=False):
    p = core.create_player("e2-probe", "帝炎探针", "g1")
    p["lv"] = 103
    p["diyan"] = bool(diyan)
    return p


def test_diyan_multiplies_all_five_stats():
    a, b = core.stats(_p(False)), core.stats(_p(True))
    for k in ("力量", "敏捷", "斗气", "防御", "体力"):
        assert b[k] == int(a[k] * 1.2), "%s 没吃到帝炎 ×1.2：%d -> %d" % (k, a[k], b[k])


def test_diyan_breakthrough_is_certain():
    assert cfg.tiandi_rate(0, True) == 1.0, "帝炎突破不是 100%"


def test_non_diyan_breakthrough_caps_at_90():
    assert cfg.tiandi_rate(99999, False) == 0.90, "非帝炎突破上限不是 90%"


def test_help_text_cap_matches_real_cap():
    """帮助卡写的「上限 N%」必须等于真实封顶，禁止手抄一个数。"""
    from doupo import commands

    cap = int(round(cfg.tiandi_rate(10 ** 6, False) * 100))
    src = inspect.getsource(commands)
    assert ("上限 %d%%" % cap) in src, "帮助卡没写非帝炎上限（或数字与实现不同源）"
    assert "帝炎 100%" in src, "帮助卡的帝炎 100% 被删了"


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
