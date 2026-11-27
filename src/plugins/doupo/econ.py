# -*- coding: utf-8 -*-
"""出售定价 · 经济平衡——**卖出价的唯一真源**。

为什么单独成文件：``config.py`` 已 204797 B，贴着 pre-commit 的 200KB 红线。

用户 2026-11-27「增加背包商品出售，注意丹药，药材，买价和卖价的平衡，
让玩家可以通过炼药赚钱」。

三条定价原则
------------
1. **药材 / 可买丹药 / 天材地宝：卖出价 < 买入价**（一律 5 折）。
   买进来再卖出去必亏一半，从数学上堵死「扫货转卖」的套利路径。
2. **可炼丹药：卖出价 = 材料成本 × 2.0**，材料按商店买价计。
   这是「炼药能赚钱」的唯一保证——拿商店价买料、开炉、卖成丹，只要
   炼丹成功率 > 50% 就净赚。刚好够格（diff=0）的成功率是
   ``craft_rate(idx) = 0.55 + 0.05×idx``（0.55~0.95），乘 2.0 得
   1.10~1.90 倍成本，**每一档都是正的**（``test_econ`` 全量扫 80 枚）。
3. **丹纹加成**：带纹丹更值钱（一纹 1.15 / 二纹 1.3 / 三纹 1.5）。
   否则炼出稀有纹丹和普丹一个价，等于惩罚运气好的玩家。

为什么不能直接拿 ``RECIPES[*]["price"]`` 当卖价
------------------------------------------------
那个字段是**市场参考价**（原著金币售价 ``buy`` 的别名），与材料成本完全脱钩：
九品金丹成本 80700 标价 90000（1.11 倍，能赚），聚气散成本 18220 却只标 120
（0.007 倍，卖一枚亏 18100）。80 枚可炼丹药里有 **25 枚** 成本高于标价——
拿它当卖价，玩家炼一枚亏一枚，与「让玩家通过炼药赚钱」正好相反。

越阶炼丹仍然可以亏——那不归定价管
----------------------------------
``craft_rate_for`` 的越阶惩罚（越 1 档 ×0.35、越 ≥2 档 ×0.05）是原著设定
（第 495 章「四品炼药师勉强炼五品，成功率太小」）。定价保证的是「你有本事
炼成，卖掉就赚钱」；没那个品阶硬要越阶，亏本是应有的代价。
"""
from __future__ import annotations

from . import config as cfg

#: 药材 / 可买丹药 / 天材地宝的回收比例（5 折）。
#: 必须 < 1.0：否则「买 60 卖 60」原地套利，商店变成提款机。
RECYCLE_RATE = 0.5

#: 可炼丹药的卖出倍数（相对材料成本）。
#: 下限由「刚好够格的最低成功率」倒推：一品炼一品 0.55 × 2.0 = 1.10 > 1。
#: 别往下调到 1.8 以下——0.55 × 1.8 = 0.99，最低档会变成「炼一枚亏一点」。
CRAFT_PROFIT_MULT = 2.0

#: 丹纹加成（索引 = 纹数，0 为普通丹）。
#: 三纹 1.5 是刻意保守的：丹纹率本身有 20% 硬顶（``DANWEN_CAP``），
#: 再往上加会让「刷三纹卖钱」压过正常玩法。
DANWEN_BONUS = (1.0, 1.15, 1.3, 1.5)


def buy_price_of(name: str) -> int:
    """该物品的商店买入价；不可买返回 0。

    唯一入口——避免「商店一套价、定价一套价」的双真源。
    """
    h = cfg.HERB_BY_NAME.get(name)
    if h:
        return int(h["price"])
    s = next((x for x in cfg.SHOP_EXTRA if x["name"] == name), None)
    if s:
        return int(s["price"])
    rec = cfg.RECIPE_BY_NAME.get(name)
    if rec:
        return int(rec.get("buy") or 0)
    m = cfg.MATERIAL_BY_NAME.get(name)
    if m:
        return int(m.get("price") or 0)
    return 0


def recipe_cost(rec: dict) -> int:
    """一枚丹方的材料成本（按商店买价逐味累加）。卖价与「能否赚钱」共用此口径。"""
    return sum(buy_price_of(k) * int(v) for k, v in (rec.get("need") or {}).items())


def _base_sell(name: str) -> int:
    """不含丹纹的基准卖出价；0 = 本作不收这件东西。"""
    # ① 药材 / 炎星石：按买入价 5 折回收
    bought = buy_price_of(name)
    if name in cfg.HERB_BY_NAME or any(x["name"] == name for x in cfg.SHOP_EXTRA):
        return int(bought * RECYCLE_RATE)

    # ② 丹药：能炼的按材料成本给利润，只能买的按买入价 5 折
    rec = cfg.RECIPE_BY_NAME.get(name)
    if rec:
        if rec.get("need"):
            return int(recipe_cost(rec) * CRAFT_PROFIT_MULT)
        if bought > 0:
            return int(bought * RECYCLE_RATE)
        return 0

    # ③ 天材地宝：掉落物，按标价 5 折
    if name in cfg.MATERIAL_BY_NAME and bought > 0:
        return int(bought * RECYCLE_RATE)

    return 0


def sell_price(name: str) -> int:
    """卖出价（单件）——**唯一入口**。返回 0 表示这件东西商店不收。

    带纹丹按基础丹定价后乘纹数加成（``DANWEN_BONUS``），
    否则「聚气散·三纹」和「聚气散」卖同一个价。
    """
    if not name:
        return 0
    name = str(name).strip()
    if cfg.is_danwen_name(name):
        base = cfg.base_of_danwen(name)
        tier = int(cfg.danwen_tier(name) or 0)
        price = _base_sell(base)
        if price <= 0:
            return 0
        return max(1, int(round(price * DANWEN_BONUS[min(3, tier)])))
    return max(0, int(_base_sell(name)))


def sellable(name: str) -> bool:
    """商店收不收这件东西。"""
    return sell_price(name) > 0
