# -*- coding: utf-8 -*-
"""包裹视图守门（2026-11-24）—— 丹药优先排序 + 全量分页。

事故背景：背包卡与「背包」文本都写死 ``items[:24]``，且排序是「数量降序」，
lv100+ 的号 64 种物品里前 24 全是堆量药材，**刚炼的丹药被整段截掉**
（用户原话「我炼制的丹药没显示在背包里」）。

本测试钉死三件事：
1. 丹药（含丹纹丹）必须排在任何材料之前；
2. 24 格一页，全量内容都能翻到（不丢任何一件）；
3. 页码越界落末页，不吐空页。

自带 runner：python tests/test_bagview.py
"""
from __future__ import annotations

import os
import sys
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (ROOT, os.path.join(ROOT, "src", "plugins")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

os.environ["DOUPO_CHANCE_RATE"] = "0"


def _stub():
    nb = types.ModuleType("nonebot")
    nb.get_driver = lambda: types.SimpleNamespace(on_startup=lambda f: f)

    def on_regex(*a, **k):
        m = types.SimpleNamespace()
        m.handle = lambda *a, **k: (lambda f: f)
        m.finish = lambda *a, **k: None
        return m

    nb.on_regex = on_regex
    nb.on_command = on_regex
    nb.on_notice = on_regex
    nb.on_message = on_regex
    sys.modules["nonebot"] = nb
    ad = types.ModuleType("nonebot.adapters")
    ad.Event = type("Event", (), {})
    sys.modules["nonebot.adapters"] = ad
    for n in ("nonebot.adapters.onebot", "nonebot.adapters.onebot.v11"):
        sys.modules[n] = types.ModuleType(n)
    v11 = sys.modules["nonebot.adapters.onebot.v11"]
    v11.MessageEvent = ad.Event
    v11.MessageSegment = object
    v11.Message = object


_stub()

from doupo import bagview, card, config as cfg  # noqa: E402


def _big_bag():
    """造一个「64 种物品、新材料堆量、丹药各 1~2 枚」的极端背包（复刻线上号）。"""
    p = {"gold": 4370584, "bag": {}}
    # 60 种高堆量材料（模拟血蟒枝 998 这类）
    for i in range(60):
        p["bag"]["材料%02d" % i] = 900 - i * 10
    # 4 种刚炼出来的丹药（数量很小 → 旧排序必被截断）
    p["bag"]["聚气散"] = 2
    p["bag"]["聚气散" + cfg.DANWEN_SUFFIX] = 1
    p["bag"]["破厄丹"] = 1
    p["bag"]["回灵丹"] = 3
    return p


def t_pills_first():
    """🔒 丹药（含丹纹丹）必须排在任何材料之前——本次事故的就医点。"""
    p = _big_bag()
    items = bagview.sorted_items(p)
    names = [n for n, _ in items]
    first_material = next(i for i, n in enumerate(names) if not bagview.is_pill(n))
    pill_pos = [i for i, n in enumerate(names) if bagview.is_pill(n)]
    assert pill_pos, "没识别出任何丹药"
    assert max(pill_pos) < first_material, (
        "丹药被材料挤到后面了：丹药位次 %s，第一个材料位次 %s" % (pill_pos, first_material))
    # 具体到玩家最关心的四枚
    for nm in ("聚气散", "聚气散" + cfg.DANWEN_SUFFIX, "破厄丹", "回灵丹"):
        assert names.index(nm) < bagview.PER_PAGE, "%s 不在第一页" % nm


def t_page1_covers_all_pills():
    """第一页必须装得下全部丹药（当前丹药种类远小于 24）。"""
    p = _big_bag()
    items = bagview.sorted_items(p)
    chunk, page, total = bagview.page_of(items, 1)
    got = {n for n, _ in chunk}
    pills = {n for n, _ in items if bagview.is_pill(n)}
    assert pills <= got, "第一页漏了丹药：%s" % (pills - got)
    assert page == 1 and total == 3, (page, total, len(items))  # 64 件 / 24 = 3 页


def t_no_item_lost():
    """翻完所有页 = 全量物品，一件都不许丢。"""
    p = _big_bag()
    items = bagview.sorted_items(p)
    seen = []
    total_pages = bagview.page_of(items, 1)[2]
    for pg in range(1, total_pages + 1):
        chunk, _, _ = bagview.page_of(items, pg)
        seen.extend(n for n, _ in chunk)
    assert len(seen) == len(items), (len(seen), len(items))
    assert set(seen) == {n for n, _ in items}


def t_page_clamp():
    """页码越界落末页，不吐空页；非法页码回第 1 页。"""
    p = _big_bag()
    items = bagview.sorted_items(p)
    chunk, page, total = bagview.page_of(items, 99)
    assert page == total and chunk, (page, total)
    chunk, page, total = bagview.page_of(items, 0)
    assert page == 1 and chunk
    chunk, page, total = bagview.page_of(items, "abc")
    assert page == 1 and chunk


def t_zero_and_dirty_filtered():
    """零值/负值/非数字不进背包（显式判空，不用 or 兜底）。"""
    p = {"bag": {"凝血散": 0, "回灵丹": -3, "聚气散": 1, "怪东西": "x"}}
    names = [n for n, _ in bagview.sorted_items(p)]
    assert names == ["聚气散"], names


def t_card_data_is_paged():
    """卡片数据：页码/页数进卡面，且行数不超 24（防一次性画 64 行）。"""
    p = _big_bag()
    d = card.build_bag_data(p, set(), 1)
    assert d["page"] == 1 and d["total_pages"] == 3, (d.get("page"), d.get("total_pages"))
    assert len(d["rows"]) <= bagview.PER_PAGE, len(d["rows"])
    assert "第 1/3 页" in d["subtitle"], d["subtitle"]
    d2 = card.build_bag_data(p, set(), 2)
    assert len(d2["rows"]) <= bagview.PER_PAGE and d2["page"] == 2


TESTS = [t_pills_first, t_page1_covers_all_pills, t_no_item_lost,
         t_page_clamp, t_zero_and_dirty_filtered, t_card_data_is_paged]

if __name__ == "__main__":
    ok = 0
    for fn in TESTS:
        try:
            fn(); ok += 1; print("PASS", fn.__name__)
        except Exception as e:
            print("FAIL", fn.__name__, type(e).__name__, str(e)[:220])
    print(f"\n{ok}/{len(TESTS)} passed")
    sys.exit(0 if ok == len(TESTS) else 1)
