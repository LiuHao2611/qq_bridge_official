# -*- coding: utf-8 -*-
r"""D2 任务物品硬校验守门（2026-11-27）。

旧行为：任务进度只比 ``kind`` —— 「采集 5 株血灵草」随便买 5 株别的草药就算完，
「炼成 2 份焚血散」随便炼两枚丹也算完。文案在承诺具体物品，判定却只数次数。

现在：任务可声明 ``item``，事件必须带**同一个物品名**才计数；
``item=None``（调用方没带）**不计数** —— 不做「没带就放过」的兜底，
否则谁少传一个参数，gate 就静默失效。

运行：
    python src/plugins/doupo/test_task_item_match.py
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

from doupo import commands  # noqa: E402
from doupo import config as cfg  # noqa: E402
from doupo import core  # noqa: E402


def _p(main_idx=1):
    p = core.create_player("d2-probe", "物品探针", "g1")
    p["main_idx"] = main_idx
    p["task"] = None
    p["lv"] = 60
    return p


# ---------------------------------------------------------------------------
# 1. 数据契约：声明了 item 的任务，文案里必须真的写着这件东西
# ---------------------------------------------------------------------------
def test_declared_items_are_promised_in_text():
    all_tasks = list(cfg.MAIN_TASKS) + list(cfg.SIDE_TASK_POOL)
    with_item = [t for t in all_tasks if t.get("item")]
    assert len(with_item) >= 5, "带物品校验的任务太少（D2 几乎没落地）"
    for t in with_item:
        assert t["item"] in str(t.get("desc") or ""), (
            "任务「%s」声明校验 %s，但 desc 没写它：%s"
            % (t.get("name"), t["item"], t.get("desc")))
    names = {str(t.get("name")): t.get("item") for t in with_item}
    assert names.get("寻药天山") == "血灵草"
    assert names.get("焚血淬体") == "焚血散"
    assert names.get("帝都风云") == "七叶花"


def test_items_exist_in_game_data():
    herbs = set(cfg.HERB_NAMES)
    recipes = set(cfg.RECIPE_NAMES)
    for t in list(cfg.MAIN_TASKS) + list(cfg.SIDE_TASK_POOL):
        item = t.get("item")
        if not item:
            continue
        kind = t.get("kind")
        pool = herbs if kind == "herb" else recipes
        assert item in pool, "任务「%s」校验的 %s 不是%s类物品" % (
            t.get("name"), item, "药材" if kind == "herb" else "丹方")


# ---------------------------------------------------------------------------
# 2. 纯函数：错物品不计数 / 对物品计数 / 不带物品不计数
# ---------------------------------------------------------------------------
def test_main_herb_task_only_counts_the_named_herb():
    p = _p(1)
    m = core.current_main(p)
    assert m["name"] == "寻药天山" and m["item"] == "血灵草"
    core.main_progress(p, "herb", 3, item="紫叶兰")
    assert int(p.get("main_progress") or 0) == 0, "买错药材也算进度（D2 没生效）"
    core.main_progress(p, "herb", 3, item="血灵草")
    assert int(p["main_progress"]) == 3
    core.main_progress(p, "herb", 1)     # 不带物品名
    assert int(p["main_progress"]) == 3, "没带物品名却放过了（兜底漏洞）"


def test_side_task_gate():
    p = _p(1)
    p["task"] = dict(cfg.SIDE_TASK_POOL[0])     # 药农之托 · 紫叶兰
    p["task"]["progress"] = 0
    core.task_progress(p, "herb", 2, item="血灵草")
    assert int(p["task"]["progress"]) == 0
    core.task_progress(p, "herb", 2, item="紫叶兰")
    assert int(p["task"]["progress"]) == 2


def test_task_without_item_accepts_anything():
    """没声明物品的任务（斩杀类）不能被 D2 误伤。"""
    p = _p(0)
    m = core.current_main(p)
    assert m["kind"] == "kill" and not m.get("item")
    core.main_progress(p, "kill", 2)
    assert int(p["main_progress"]) == 2
    core.main_progress(p, "kill", 1, item="随便什么东西")
    assert int(p["main_progress"]) == 3


def test_advance_task_passes_item_through():
    p = _p(1)
    core.advance_task(p, "herb", 1, rng=lambda: 1.0, item="紫叶兰")
    assert int(p.get("main_progress") or 0) == 0
    core.advance_task(p, {"herb": 2}, rng=lambda: 1.0, item="血灵草")
    assert int(p["main_progress"]) == 2


# ---------------------------------------------------------------------------
# 3. 命令层接线：买/炼必须把真实物品名传下去
# ---------------------------------------------------------------------------
def test_commands_pass_the_real_item_name():
    buy = inspect.getsource(commands.cmd_buy)
    assert 'item=res.get("name")' in buy, "买药材没传物品名，采药类任务永远推不动"
    craft = inspect.getsource(commands.cmd_craft)
    assert "item=hit" in craft, "炼丹没传丹名"
    batch = inspect.getsource(commands._cmd_craft_batch)
    assert "item=hit" in batch, "批量炼丹没传丹名"


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
