# -*- coding: utf-8 -*-
"""包裹视图：丹药优先排序 + 24 格分页（2026-11-24）。

为什么单独一个模块
------------------
``core.py`` 长期贴着 200KB 提交红线；「排序 / 分页」是纯展示口径，既不参与战斗
也不参与结算。卡片（``card.build_bag_data``）与文本（``commands.cmd_bag``）
**必须共用这一份口径**，否则同一个背包在卡里和文字里顺序不同、页数对不上。

事故背景
--------
旧口径是「按数量降序」直接 ``items[:24]``：lv100+ 的号有 64 种物品，前 24 格
全是几百上千的堆量药材，刚炼出来的丹药（数量 1~11）排在末尾被整段截掉。
用户原话「我炼制的丹药没显示在背包里」。修法两条，缺一不可：

1. **丹药优先**：丹药（含 ``丹名·丹纹``）永远排在材料前面；
2. **分页**：超出 24 格的内容能翻页看到，不再无声丢弃。
"""
from __future__ import annotations

from . import config as _cfg

#: 一页格数 = 实机包裹 4 列 × 6 行。
PER_PAGE = 24


def is_pill(name: str) -> bool:
    """丹药判定：``RECIPE_BY_NAME`` 里的名字（含丹纹丹的 ``丹名·丹纹``）。

    注意``RECIPE_BY_NAME`` 同时收了天材地宝以外的真丹方与带纹丹别名，但**不含**
    药材（药材在 ``HERBS``），所以这里天然只覆盖「炼出来的东西」。
    """
    return str(name) in _cfg.RECIPE_BY_NAME


def sorted_items(p: dict) -> list[tuple[str, int]]:
    """包裹全量排序：``(丹药, 数量降序, 名称)``。不做任何截断。"""
    raw = p.get("bag")
    if not isinstance(raw, dict):
        return []
    items: list[tuple[str, int]] = []
    for k, v in raw.items():
        try:
            n = int(v)
        except (TypeError, ValueError):
            continue
        if n > 0:                      # 零值/负值不属于「持有」，显式判空
            items.append((str(k), n))
    items.sort(key=lambda kv: (0 if is_pill(kv[0]) else 1, -kv[1], kv[0]))
    return items


def page_of(items: list, page: int = 1, per: int = PER_PAGE):
    """夹取页码并切出本页，返回 ``(chunk, page, total_pages)``。

    页码越界**落末页**而不是吐空页（与 ``core.recipes_page`` 同口径），
    避免「背包9」把玩家丢进一片空白。
    """
    per = max(1, int(per))
    total_pages = max(1, (len(items) + per - 1) // per)
    try:
        page = int(page)
    except (TypeError, ValueError):
        page = 1
    page = max(1, min(total_pages, page))
    start = (page - 1) * per
    return items[start:start + per], page, total_pages
