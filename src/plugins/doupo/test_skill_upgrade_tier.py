# -*- coding: utf-8 -*-
r"""A6 + C2 斗技升阶按品阶分档守门（2026-11-27）。

旧行为：150 门图鉴技全走 ``.get("max", 5)`` 兜底 —— 黄阶低级与天阶高级一样
5 级、一样 10 份焚血散（cost=cur），品阶只决定初始倍率，养成深度与品阶无关；
而升技材料又能用金币直购，后期升技等价于金币兑换（C2）。

现在：
  * 上限按阶位分档：黄 5 / 玄 6 / 地 7 / 天 8（最高只抬到 8，避免吃掉炼药产出）；
  * 消耗按阶位缩放：黄 1.0 / 玄 1.2 / 地 1.5 / 天 2.0（乘 cur 向上取整）；
  * 卡面 ``mx`` / ``item_need`` 与 ``skill_up`` 读**同一函数**（core.skill_max /
    core.skill_up_cost），不许再各写一份。

运行：
    python src/plugins/doupo/test_skill_upgrade_tier.py
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
from doupo import core  # noqa: E402

RANK_SAMPLE = {
    "黄": "碎石掌", "玄": "狮山裂", "地": "大悲撕风手", "天": "九幽冥手",
}


def _entry(name):
    """从图鉴取一条真实技能记录（带 rank 字段）。"""
    from doupo import skill_book_data as d
    rec = next(r for r in getattr(d, "BOOK_ROWS", ()) if r.get("name") == name)
    return dict(rec)


def _p(names):
    p = core.create_player("a6-probe", "升阶探针", "g1")
    p["lv"] = 60
    p["learned"] = list(names)
    p["loadout"] = list(names)[:4]
    p["hp"] = core.stats(p)["体力"]
    p["gold"] = 10 ** 9
    return p


def test_cap_by_rank():
    want = {"黄": 5, "玄": 6, "地": 7, "天": 8}
    for rank, name in RANK_SAMPLE.items():
        e = _entry(name)
        assert core.skill_max(e) == want[rank], "%s 上限 %d" % (rank, core.skill_max(e))


def test_cost_scales_with_rank():
    """同一等级下天阶消耗必须高于黄阶（不是只改上限不改成本）。"""
    cost = {rank: core.skill_up_cost(3, _entry(n)) for rank, n in RANK_SAMPLE.items()}
    assert cost["黄"] < cost["玄"] <= cost["地"] <= cost["天"], cost
    assert cost["天"] >= 2 * cost["黄"], "天阶消耗没拉开差距：%s" % cost


def test_skill_up_blocks_at_new_cap_and_consumes_more():
    """真升一次：天阶要更多焚血散；升到新上限必须被挡回。"""
    e = _entry("九幽冥手")
    p = _p(["九幽冥手"])
    core.add_item(p, cfg.SKILL_UP_ITEM, 999)
    need = core.skill_up_cost(1, e)
    before = core.count_item(p, cfg.SKILL_UP_ITEM)
    r = core.skill_up(p, "九幽冥手")
    assert r["ok"], r
    assert core.count_item(p, cfg.SKILL_UP_ITEM) == before - need, "扣的份数与卡面不同源"
    # 外部基准：天阶上限是 8，lv5 必须还能升（变异回 5 时这里会红）。
    p["skills"]["九幽冥手"] = 5
    assert core.skill_up(p, "九幽冥手")["ok"], "天阶 lv5 没升到 6（上限还是 5？）"
    p["skills"]["九幽冥手"] = 8
    r2 = core.skill_up(p, "九幽冥手")
    assert not r2["ok"] and r2["reason"] == "max", "到新上限没挡回"


def test_legacy_skills_keep_five():
    """config.SKILLS 里的 5 门旧技没有 rank，必须仍是 5 级上限。"""
    for name in cfg.SKILL_NAMES:
        assert core.skill_max({"name": name}) == 5


def test_card_and_core_share_one_source():
    import inspect
    from doupo import card

    assert "core.skill_max(e)" in inspect.getsource(card.build_skills_data)
    assert "core.skill_up_cost(lv, e)" in inspect.getsource(card.build_skills_data)
    src = inspect.getsource(core.skill_up)
    assert "skill_max(entry)" in src and "skill_up_cost(cur, entry)" in src


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
