# -*- coding: utf-8 -*-
"""斗破 · 出售定价与经济平衡单测（零依赖、exit-code 可验证）。

运行：
    python src/plugins/doupo/test_econ.py

守的是用户 2026-11-27 的两句要求：
  * 「注意丹药，药材，买价和卖价的平衡」→ 买进来再卖出去必亏（防套利）
  * 「让玩家可以通过炼药赚钱」→ 够格炼一炉、卖掉之后期望为正
"""
from __future__ import annotations

import os
import sys
import types

PLUGINS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO_ROOT = os.path.dirname(os.path.dirname(PLUGINS_DIR))
for _p in (REPO_ROOT, PLUGINS_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

os.environ["DOUPO_CHANCE_RATE"] = "0"


def _stub_nonebot():
    nb = types.ModuleType("nonebot")
    nb.get_driver = lambda: types.SimpleNamespace(on_startup=lambda f: f)

    def on_regex(*a, **k):
        m = types.SimpleNamespace()
        m.handle = lambda *a, **k: (lambda f: f)
        m.finish = lambda *a, **k: None
        return m

    nb.on_regex = on_regex
    for name in ("on_command", "on_startswith", "on_keyword", "on_message"):
        setattr(nb, name, on_regex)
    nb.logger = types.SimpleNamespace(info=lambda *a, **k: None,
                                      warning=lambda *a, **k: None,
                                      error=lambda *a, **k: None,
                                      debug=lambda *a, **k: None,
                                      exception=lambda *a, **k: None)
    sys.modules["nonebot"] = nb

    ad = types.ModuleType("nonebot.adapters")
    ad.__path__ = []
    sys.modules["nonebot.adapters"] = ad
    for full in ("nonebot.adapters.qq", "nonebot.adapters.qq.message",
                 "nonebot.adapters.qq.event", "nonebot.adapters.qq.bot",
                 "nonebot.adapters.onebot", "nonebot.adapters.onebot.v11"):
        m = types.ModuleType(full)
        m.__path__ = []
        sys.modules[full] = m
        parent, _, leaf = full.rpartition(".")
        if parent in sys.modules:
            setattr(sys.modules[parent], leaf, m)
    sys.modules["nonebot.adapters.qq.message"].MessageSegment = type("MS", (object,), {})
    sys.modules["nonebot.adapters.onebot.v11"].Message = list
    sys.modules["nonebot.adapters.onebot.v11"].MessageSegment = type("MS2", (object,), {})


_stub_nonebot()

from doupo import config as cfg  # noqa: E402
from doupo import core, econ  # noqa: E402

_TOTAL = 0
_FAILED = 0


def check(label: str, cond, detail=""):
    global _TOTAL, _FAILED
    _TOTAL += 1
    if cond:
        print("PASS  %s" % label)
    else:
        _FAILED += 1
        print("FAIL  %s  ->  %s" % (label, detail))


def _p(lv=1, flame=False):
    p = core.create_player("10001", "测试", "20001")
    p["lv"] = lv
    p["exp"] = 0
    if flame:
        p["flames"]["紫火"] = 1
    p["hp"] = core.stats(p)["体力"]
    p["mp"] = core.mp_max(p)
    return p


# ---------------------------------------------------------------------------
# 一、防套利：买得进来的东西，卖出去必须亏
# ---------------------------------------------------------------------------
def test_herb_sell_below_buy():
    """92 种药材：卖出价必须严格小于买入价（5 折，亏一半）。"""
    bad = []
    for h in cfg.HERBS:
        buy = int(h["price"])
        sell = econ.sell_price(h["name"])
        if not (0 < sell < buy):
            bad.append((h["name"], buy, sell))
    check("药材卖价 < 买价（全量 %d 种）" % len(cfg.HERBS), not bad, bad[:5])


def test_buyonly_drug_sell_below_buy():
    """只能金币买的丹药（无药方）：卖出价必须低于买价。"""
    rows, bad = [], []
    for r in cfg.RECIPES:
        if r.get("need"):
            continue
        buy = int(r.get("buy") or 0)
        if buy <= 0:
            continue
        sell = econ.sell_price(r["name"])
        rows.append(r["name"])
        if not (0 < sell < buy):
            bad.append((r["name"], buy, sell))
    check("可买丹药卖价 < 买价（%d 枚）" % len(rows), rows and not bad, bad[:5])


def test_shop_extra_sell_below_buy():
    """炎星石等商店额外货：卖出价低于买价。"""
    bad = []
    for s in cfg.SHOP_EXTRA:
        buy, sell = int(s["price"]), econ.sell_price(s["name"])
        if not (0 < sell < buy):
            bad.append((s["name"], buy, sell))
    check("商店额外货卖价 < 买价（%d 种）" % len(cfg.SHOP_EXTRA), not bad, bad)


def test_no_round_trip_profit():
    """穷举：任何「买 N 个再卖掉」的组合都不可能赚钱（含批量）。"""
    bad = []
    for item in core.shop_list():
        name, buy = item["name"], int(item["price"])
        sell = econ.sell_price(name)
        if sell >= buy:
            bad.append((name, buy, sell))
        # 批量也不该例外（线性定价，买 10 卖 10 同样亏）
        if sell * 10 >= buy * 10 and sell < buy:
            bad.append((name, "批量", buy, sell))
    check("买→卖 无任何套利路径", not bad, bad[:5])


# ---------------------------------------------------------------------------
# 二、炼药能赚钱：够格炼一炉，期望为正
# ---------------------------------------------------------------------------
def test_craft_profit_positive_at_own_grade():
    """每枚可炼丹药，在「刚好够格」的成功率下，卖价 × 成功率 必须 > 材料成本。

    这是「让玩家可以通过炼药赚钱」的机器可验证定义：
    玩家按商店价买齐材料、以自己品阶对应的成功率开炉、把成丹卖掉，
    期望收益必须为正。定价系数 ``CRAFT_PROFIT_MULT`` 一下调就会红。
    """
    bad, rows = [], 0
    for rec in cfg.RECIPES:
        if not (rec.get("need") or {}):
            continue
        rows += 1
        cost = econ.recipe_cost(rec)
        sell = econ.sell_price(rec["name"])
        grade = str(rec.get("grade") or "")
        idx = cfg.alch_grade_idx(grade)
        rate, _diff = cfg.craft_rate_for(idx, grade)   # diff = 0，刚好够格
        expected = rate * sell
        if not (cost > 0 and expected > cost):
            bad.append((rec["name"], grade, cost, sell, round(rate, 2), round(expected, 1)))
    check("够格炼一炉期望为正（全量 %d 枚）" % rows, rows >= 70 and not bad, bad[:5])


def test_craft_profit_margin_reasonable():
    """利润率不能低到「炼一炉赚 1 金」，也不能高到一次暴富。"""
    lows, highs = [], []
    for rec in cfg.RECIPES:
        if not (rec.get("need") or {}):
            continue
        cost = econ.recipe_cost(rec)
        sell = econ.sell_price(rec["name"])
        if cost <= 0:
            continue
        ratio = sell / cost
        if ratio < 1.9:
            lows.append((rec["name"], round(ratio, 2)))
        if ratio > 2.1:
            highs.append((rec["name"], round(ratio, 2)))
    check("卖价恒为材料成本 2.0 倍（不被 price 字段带偏）", not lows and not highs,
          (lows[:3], highs[:3]))


def test_craft_chain_gold_increases():
    """端到端：1000 金起步，买料 → 开炉 → 卖丹，金币必须净增。"""
    p = _p(lv=20, flame=True)
    p["gold"] = 1000
    start = p["gold"]
    rec = cfg.RECIPE_BY_NAME["回灵丹"]
    for mat, n in rec["need"].items():
        res = core.shop_buy(p, mat, n)
        assert res["ok"], (mat, res)
    paid = start - p["gold"]
    assert paid == econ.recipe_cost(rec), (paid, econ.recipe_cost(rec))
    before_hi = core.count_item(p, "回灵丹")
    out = core.craft(p, "回灵丹", rng=lambda: 0.0)       # 强制成功
    assert out["ok"], out
    # 入道自带回灵丹，只能比「炼之前」多一枚，不能写死成 1
    assert core.count_item(p, "回灵丹") == before_hi + 1, (
        before_hi, core.count_item(p, "回灵丹"))
    assert p["gold"] == start - paid                     # 炼药本身不再扣钱
    sold = core.sell_item(p, "回灵丹", 1)
    assert sold["ok"], sold
    check("买料→炼制→卖出 金币净增（%d → %d，成本 %d 卖 %d）"
          % (start, p["gold"], paid, sold["total"]),
          p["gold"] > start, (start, p["gold"]))


def test_craft_fail_still_loses_less_than_nothing():
    """炼失败（得药渣）不产生金币，账目必须如实为负——不假装成功。"""
    p = _p(lv=20, flame=True)
    p["gold"] = 1000
    for mat, n in cfg.RECIPE_BY_NAME["回灵丹"]["need"].items():
        core.shop_buy(p, mat, n)
    before = p["gold"]
    out = core.craft(p, "回灵丹", rng=lambda: 0.999)     # 强制失败
    assert out["ok"] is False and out["reason"] == "fail", out
    check("炼药失败照旧得药渣、不返金币",
          core.count_item(p, "药渣") == cfg.CRAFT_FAIL_DROSS and p["gold"] == before,
          (core.count_item(p, "药渣"), p["gold"]))


# ---------------------------------------------------------------------------
# 三、丹纹与定价细节
# ---------------------------------------------------------------------------
def test_danwen_bonus_monotonic():
    """带纹丹必须比普丹值钱，且纹数越高越贵。"""
    base = econ.sell_price("回灵丹")
    tiers = [econ.sell_price("回灵丹·%s" % w) for w in ("一纹", "二纹", "三纹")]
    check("丹纹加成单调递增", base < tiers[0] < tiers[1] < tiers[2],
          (base, tiers))
    check("三纹溢价 ≈ 1.5 倍（不打折卖）",
          abs(tiers[2] / base - 1.5) < 0.02, (tiers[2], base))


def test_danwen_uses_base_price():
    """带纹丹按**基础丹**定价，不是按带纹名查不到就返回 0。"""
    check("带纹丹有价（不因名字带后缀变哑巴）",
          econ.sell_price("聚气散·一纹") > 0 and econ.sell_price("回灵丹·三纹") > 0)


def test_unsellable_unknown():
    """不认识的东西一律不收（0），绝不猜个价卖出去。"""
    for name in ("不存在的丹药", "", "   ", "上古神器"):
        check("不收【%s】" % (name or "<空>"), econ.sell_price(name) == 0)


def test_sell_price_positive_for_every_recipe():
    """83 枚丹药全部有价——包裹里躺着的丹不该是「卖不掉的哑巴」。"""
    zeros = [r["name"] for r in cfg.RECIPES if econ.sell_price(r["name"]) <= 0]
    check("全部 %d 枚丹药均可回收" % len(cfg.RECIPES), not zeros, zeros[:6])


def test_sell_price_positive_for_every_herb():
    """92 种药材全部有价。"""
    zeros = [h["name"] for h in cfg.HERBS if econ.sell_price(h["name"]) <= 0]
    check("全部 %d 种药材均可回收" % len(cfg.HERBS), not zeros, zeros[:6])


# ---------------------------------------------------------------------------
# 四、core.sell_item 的边界
# ---------------------------------------------------------------------------
def test_sell_item_basic():
    p = _p()
    p["gold"] = 0
    core.add_item(p, "血灵草", 3)
    unit = econ.sell_price("血灵草")
    res = core.sell_item(p, "血灵草", 2)
    check("卖出 2 份血灵草",
          res["ok"] and res["n"] == 2 and res["unit"] == unit
          and res["total"] == unit * 2 and p["gold"] == unit * 2, res)
    check("背包剩 1 份", core.count_item(p, "血灵草") == 1)


def test_sell_item_short_and_none():
    p = _p()
    core.add_item(p, "血灵草", 1)
    r1 = core.sell_item(p, "血灵草", 5)
    check("数量不足如实报 have", r1["ok"] is False and r1["reason"] == "short"
          and r1["have"] == 1, r1)
    r2 = core.sell_item(p, "七叶花", 1)
    check("背包没有如实报 none", r2["ok"] is False and r2["reason"] == "none", r2)
    r3 = core.sell_item(p, "不存在的丹药", 1)
    check("不收的东西报 unsellable", r3["ok"] is False and r3["reason"] == "unsellable", r3)


def test_sell_item_does_not_create_items():
    """卖出绝不反向加物品（卖 0 个 / 卖负数都不许刷）。"""
    p = _p()
    p["gold"] = 0
    core.add_item(p, "血灵草", 2)
    core.sell_item(p, "血灵草", 0)
    core.sell_item(p, "血灵草", -5)
    check("卖 0 个/负数不刷物品、不加钱",
          core.count_item(p, "血灵草") == 2 and p["gold"] == 0,
          (core.count_item(p, "血灵草"), p["gold"]))


def test_coin_conservation():
    """炼+卖全链路：金币增减必须与「材料支出 / 卖丹收入」逐枚对得上。"""
    p = _p(lv=20, flame=True)
    p["gold"] = 100000
    rec = cfg.RECIPE_BY_NAME["回灵丹"]
    before = p["gold"]
    spent = got = 0
    for _ in range(5):
        for mat, n in rec["need"].items():
            g0 = p["gold"]
            assert core.shop_buy(p, mat, n)["ok"]
            spent += g0 - p["gold"]
        p["last_craft"] = 0            # 跳过 CD，专测账目
        out = core.craft(p, "回灵丹", rng=lambda: 0.0)
        assert out["ok"], out
        got += core.sell_item(p, "回灵丹", 1)["total"]
    net = p["gold"] - before
    check("5 炉账目守恒（支出 %d 收入 %d 净 %+d）" % (spent, got, net),
          net == got - spent and net > 0, (net, got, spent))


def main():
    for fn in sorted(k for k in dict(globals()) if k.startswith("test_")):
        globals()[fn]()
    print("=" * 70)
    print("ECON_TOTAL=%d PASSED=%d FAILED=%d"
          % (_TOTAL, _TOTAL - _FAILED, _FAILED))
    return 1 if _FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
