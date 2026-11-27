# -*- coding: utf-8 -*-
r"""ch5 异火主线死锁守门（D0 P0 + D1，2026-11-27）。

死锁复现（修前）：玩家在读到 ch5「将任意异火升到 1 级」之前就把火喂满 4 级，
``main_progress`` 只在当前主线是 flame 时由喂火事件累加 → 进度恒 0；
满级后再喂返回 ``reason='max'``、不推进事件 → 13 章永久断在 ch5。

修法：与武器同构，``main_value`` / ``task_value`` 对 ``kind=='flame'`` 取
**状态值** ``max(玩家已收服异火的等级)``。顺带修 D1：收服即 1 级，
文案「升到 N 级」从此真读等级，不再是喂养次数。

运行：
    python src/plugins/doupo/test_flame_task_state.py
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

from doupo import config as cfg, core  # noqa: E402


def _p(ch: int, flame_lv: int = 0, main_progress: int = 0) -> dict:
    p = core.create_player("flame-task-probe", "火探针", "g1")
    p["main_idx"] = max(0, ch - 1)
    p["story_chapter"] = ch
    p["main_progress"] = main_progress
    p["flames"] = {}
    if flame_lv:
        p["flames"]["青莲地心火"] = flame_lv
    # 本文件守的是「进度口径」，不是等级闸；按 Q4 后的 require_lv 造达标玩家。
    t = core.current_main(p)
    if t:
        p["lv"] = int(t.get("require_lv") or 1)
    return p


def _flame_task_index() -> int:
    return next(i for i, t in enumerate(cfg.MAIN_TASKS) if t.get("kind") == "flame")


def test_ch5_no_deadlock_when_flame_pre_maxed():
    """D0：提前喂满也不卡——状态值直接满足 1 级要求。"""
    idx = _flame_task_index()
    p = _p(idx + 1, flame_lv=cfg.FLAME_MAX_LV, main_progress=0)
    t = core.current_main(p)
    assert t and t.get("kind") == "flame", "探针没落在异火任务上"
    assert int(t["target"]) == 1
    assert core.main_value(p) >= int(t["target"]), "满级火仍交不了 ch5"
    r = core.main_submit(p)
    assert r.get("ok") is True, r


def test_ch5_d1_reward_matches_wording():
    """D1：收服即 1 级就能交「升到 1 级」，不必再喂一次。"""
    idx = _flame_task_index()
    p = _p(idx + 1, flame_lv=1, main_progress=0)
    assert core.main_value(p) >= 1
    assert core.main_submit(p).get("ok") is True


def test_ch11_flame_lv3_target():
    """ch11「升到 3 级」真读等级；2 级不够、3 级够。"""
    idxs = [i for i, t in enumerate(cfg.MAIN_TASKS) if t.get("kind") == "flame"]
    assert len(idxs) >= 2, "图鉴里应该有第二条异火主线"
    i = idxs[1]
    t = cfg.MAIN_TASKS[i]
    lo = _p(i + 1, flame_lv=int(t["target"]) - 1)
    hi = _p(i + 1, flame_lv=int(t["target"]))
    assert core.main_value(lo) < int(t["target"]), "等级不够却算达标"
    assert core.main_value(hi) >= int(t["target"])


def test_multiple_flames_take_highest_not_sum():
    """「任意异火」取最高级，不累加、不因新收低级火倒退。"""
    p = _p(_flame_task_index() + 1, flame_lv=4)
    p["flames"]["兽火"] = 1
    assert core._flame_task_value(p) == 4


def test_flame_value_does_not_affect_other_task_kinds():
    """回归：异火状态值不串到 kill/herb 等主线。"""
    p = _p(1, flame_lv=4, main_progress=0)
    t = core.current_main(p)
    assert t.get("kind") != "flame"
    assert core.main_value(p) == 0, "异火等级串进了非异火任务进度"


def test_task_value_also_uses_flame_state():
    """支线火焰任务同口径（task_value 与 main_value 不许分叉）。"""
    p = core.create_player("flame-side", "火", "g1")
    p["task"] = {"kind": "flame", "target": 2, "progress": 0}
    p["flames"] = {"青莲地心火": 3}
    assert core.task_value(p) >= 2


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
