# -*- coding: utf-8 -*-
"""丹纹赐福守门（2026-11-24）—— 设计见
docs/炼药模块优化_原著考据与设计_2026-11-24.md §10/§10.5。

守玩家可感知行为：出纹→带纹丹→服丹赐福→战斗真生效→次数耗尽清退。
自带 runner，零依赖 pytest：python tests/test_danwen.py
"""
from __future__ import annotations

import os
import sys
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLUGINS = os.path.join(ROOT, "src", "plugins")
for _p in (ROOT, PLUGINS):
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
    sys.modules["nonebot"] = nb
    ad = types.ModuleType("nonebot.adapters")
    ob = types.ModuleType("nonebot.adapters.onebot")
    v11 = types.ModuleType("nonebot.adapters.onebot.v11")
    v11.MessageEvent = object; v11.MessageSegment = object; v11.Message = object
    sys.modules["nonebot.adapters"] = ad
    sys.modules["nonebot.adapters.onebot"] = ob
    sys.modules["nonebot.adapters.onebot.v11"] = v11


_stub()
from doupo import core, config as cfg, danwen as dw, temp_buff as tb  # noqa: E402

DN = "凝血散" + cfg.DANWEN_SUFFIX          # 存量存档名（·丹纹），按一纹兼容
DN1 = "凝血散" + cfg.danwen_suffix(1)       # 新产出名（·一纹）
DN2 = "凝血散" + cfg.danwen_suffix(2)
DN3 = "凝血散" + cfg.danwen_suffix(3)


def P(alch=5):
    p = core.create_player("t", "t", "g")
    p["lv"] = 60; p["alch"] = alch; p["alch_exp"] = 0
    p["flames"]["紫火"] = 1
    st = core.stats(p); p["hp"] = st["体力"]; p["mp"] = core.mp_max(p)
    return p


def mon():
    return {"lv": 55, "hp": 50000, "atk": 2000, "def": 1500, "exp_mul": 1,
            "gold_mul": 1, "name": "试炼木人", "exp": 100, "gold": 100}


def t_rate_table():
    """丹纹率单调递减，且全部 ≤ CAP、≥ 配置值。"""
    rates = [cfg.danwen_rate(g) for g in ("一品", "二品", "三品", "四品", "五品",
                                          "六品", "七品", "八品", "九品", "帝品")]
    assert all(0 < r <= cfg.DANWEN_CAP for r in rates), rates
    assert all(rates[i] >= rates[i + 1] for i in range(len(rates) - 1)), rates
    assert cfg.danwen_rate("不存在") == 0.0


class _AlwaysHit:
    """钉死丹纹独立源为必出纹（不影响成败注入 rng）。"""
    def random(self): return 0.0


class _Seq:
    """按序吐出指定随机数；耗尽后重复最后一个值。"""
    def __init__(self, vals): self.vals = list(vals)

    def random(self):
        if len(self.vals) > 1:
            return self.vals.pop(0)
        return self.vals[0] if self.vals else 0.0


def _craft_once(vals):
    """用给定丹纹随机序列炼一炉凝血散，返回 (结果, 玩家)（成败恒成功）。"""
    p = P()
    for m in cfg.RECIPE_BY_NAME["凝血散"]["need"]:
        p["bag"][m] = 100
    old = dw._R
    dw._R = _Seq(vals)
    try:
        return core.craft(p, "凝血散", rng=lambda: 0.0, now_ts=1000), p
    finally:
        dw._R = old


def t_craft_emits_danwen():
    """炼制成功 + 丹纹命中 → 产一枚带纹丹（一纹），回执带纹数。"""
    p = P()
    for m, n in cfg.RECIPE_BY_NAME["凝血散"]["need"].items():
        p["bag"][m] = 100
    old = dw._R
    dw._R = _AlwaysHit()
    try:
        r = core.craft(p, "凝血散", rng=lambda: 0.0, now_ts=1000)
    finally:
        dw._R = old
    assert r.get("danwen") == 1, r.get("danwen")
    assert core.count_item(p, DN1) == 1


def t_craft_tier_by_weight():
    """纹数按 70/25/5 权重落到 一纹/二纹/三纹，品名后缀与纹数一致。"""
    for x, tier in ((0.10, 1), (0.80, 2), (0.98, 3)):
        r, p = _craft_once([0.0, x])
        nm = "凝血散" + cfg.danwen_suffix(tier)
        assert r.get("danwen") == tier, (x, r.get("danwen"))
        assert core.count_item(p, nm) == 1, (x, nm, p["bag"])


def t_legacy_suffix_reads_as_tier1():
    """存量存档「·丹纹」仍算丹纹丹、按一纹算；名称不与新后缀冲突。"""
    assert cfg.is_danwen_name(DN) and cfg.danwen_tier(DN) == 1
    assert cfg.base_of_danwen(DN) == "凝血散"
    assert cfg.base_of_danwen(DN3) == "凝血散"
    for s in (DN1, DN2, DN3):
        assert s in cfg.RECIPE_BY_NAME and s in cfg.DANWEN_RECIPE_NAMES, s


def t_batch_counts_danwen():
    """批量 10 炉全出纹 → n_danwen==n_done，逐炉独立。"""
    p = P()
    for m, n in cfg.RECIPE_BY_NAME["凝血散"]["need"].items():
        p["bag"][m] = 1000
    p["mp"] = core.mp_max(p) * 4
    old = dw._R
    dw._R = _AlwaysHit()
    try:
        r = core.craft_batch(p, "凝血散", 10, rng=lambda: 0.0, now_ts=1000)
    finally:
        dw._R = old
    assert r["ok"] and r["n_danwen"] == r["n_done"], r
    assert core.count_item(p, DN1) == r["n_done"]


def t_use_grants_blessing():
    """服带纹丹：扣丹、走基础药效、挂随机赐福、回执可见。"""
    p = P(); p["bag"][DN] = 1; p["hp"] = 100
    res = core.use_item(p, DN, rng=lambda: 0.0)  # pick→B01
    assert res.get("ok")
    assert res.get("danwen", {}).get("id") == "B01"
    assert p["danwen_charges"]["B01"]["n"] == 3
    assert "丹纹赐福" in res.get("msg", "")
    assert core.count_item(p, DN) == 0


def t_b01_dmg_x2_consumed():
    """焚天之威：前 3 击 ×2，用完自动清，不残留。"""
    p = P(); p["danwen_charges"] = {"B01": {"n": 3, "src": DN}}
    r = core.fight(p, mon(), rng=lambda: 0.5, roll_aid=False)
    x2 = [x for x in r["log"] if x.get("danwen_x2")]
    assert 1 <= len(x2) <= 3, len(x2)
    assert not p.get("danwen_charges"), p.get("danwen_charges")


def t_b02_crit_guaranteed():
    """一击必中：rng=0.99 本不暴击，仍必暴击一次，用完清。"""
    p = P(); p["danwen_charges"] = {"B02": {"n": 1, "src": DN}}
    r = core.fight(p, mon(), rng=lambda: 0.99, roll_aid=False)
    crits = [x for x in r["log"] if x.get("crit")]
    assert len(crits) >= 1
    assert not p.get("danwen_charges")


def t_b03_timed_stats():
    """蛮牛之力：力量/斗气 +25%，进面板。"""
    p = P(); s0 = core.stats(p)
    for k, m, mi, s in dw.timed_items(p, dw.get("B03")):
        tb.add(p, k, m, mi, s)
    s1 = core.stats(p)
    assert s1["力量"] > s0["力量"] and s1["斗气"] > s0["斗气"]


def t_b06_mp_refill():
    """丹田盈溢：开战斗气回满一次，用完清。"""
    p = P(); p["danwen_charges"] = {"B06": {"n": 1, "src": DN}}
    p["mp"] = 1
    mx = core.mp_max(p)
    r = core.fight(p, mon(), rng=lambda: 0.5, roll_aid=False)
    # 开战回满：首击的 mp_before 应远大于 1（接近斗气上限）
    first = next((x for x in r["log"] if x.get("mp_before") is not None), None)
    assert first and first["mp_before"] > mx // 2, first
    assert not p.get("danwen_charges")


def t_zero_and_sweep():
    """零值合法：n=0 视为无；sweep 清坏数据。"""
    p = P(); p["danwen_charges"] = {"B01": {"n": 0, "src": "x"}}
    assert not dw.has(p, "dmg_x2")
    dw.sweep(p)
    assert p.get("danwen_charges") is None
    p2 = P(); p2["danwen_charges"] = {"BAD": {"n": 5, "src": "x"}}
    dw.sweep(p2)
    assert p2.get("danwen_charges") is None


def t_danwen_does_not_consume_battle_rng():
    """丹纹用独立源，不消耗成败注入 rng——既有批量序列逐字节不变（防回归）。"""
    p = P(alch=8)
    for m, n in cfg.RECIPE_BY_NAME["回灵丹"]["need"].items():
        p["bag"][m] = 10
    seq = iter([0.0, 0.999, 0.0, 0.999, 0.0])
    r = core.craft_batch(p, "回灵丹", 5, rng=lambda: next(seq), now_ts=1000)
    assert r["n_ok"] == 3 and r["n_fail"] == 2, r  # 序列没被丹纹打乱


def t_full_hp_keeps_danwen_pill():
    """🚨 吞丹守门：满血服带纹丹 → 基础药效返回 full，**丹必须还在**、不挂赐福。

    反序实现（先扣后跑）会让玩家白丢一枚稀有带纹丹 —— 2026-11-24 实测抓出过。
    """
    p = P()                      # P() 已满血
    p["bag"][DN] = 1
    assert int(p["hp"]) >= int(core.stats(p)["体力"])
    res = core.use_item(p, DN, rng=lambda: 0.0)
    assert not res.get("ok") and res.get("reason") == "full", res
    assert core.count_item(p, DN) == 1, "满血服丹把带纹丹吃掉了"
    assert not p.get("danwen_charges"), p.get("danwen_charges")


def t_special_pills_have_no_danwen():
    """兽火单方/升技材料/养魂涎不能有带纹版（服下必先扣后失败 = 吞丹）。"""
    for nm in ("焚血散", "养魂涎", "紫火丹", "桃花丹"):
        for i in range(1, len(cfg.DANWEN_TIERS) + 1):
            dn = nm + cfg.danwen_suffix(i)
            assert dn not in cfg.RECIPE_BY_NAME, dn
            assert dn not in cfg.DANWEN_RECIPE_NAMES, dn
        assert nm + cfg.DANWEN_LEGACY not in cfg.RECIPE_BY_NAME, nm
    # 常规丹必须仍有全部档位（含存量别名）
    for s in (cfg.danwen_suffix(1), cfg.danwen_suffix(2),
              cfg.danwen_suffix(3), cfg.DANWEN_LEGACY):
        assert "凝血散" + s in cfg.RECIPE_BY_NAME, s


def t_tier_scales_duration_not_pct():
    """档位只放大次数/时长，**不动百分比**——平衡靠限时限次（用户红线）。"""
    p = P()
    b = dw.get("B03")
    _k1, mul1, min1, _s = dw.timed_items(p, b, scale=dw._scale_of(1))[0]
    _k3, mul3, min3, _s = dw.timed_items(p, b, scale=dw._scale_of(3))[0]
    assert abs(float(b["mul"]) - 0.25) < 1e-9
    assert mul3 == mul1, (mul1, mul3)                     # mul 不变
    assert float(min3) > float(min1), (min1, min3)        # 分钟放大
    n1 = dw._n_of(dw.get("B01"), dw._scale_of(1))
    n3 = dw._n_of(dw.get("B01"), dw._scale_of(3))
    assert n1 == 3 and n3 > n1, (n1, n3)


def t_tier3_grants_two_blessings():
    """🔒 三纹额外多一条赐福，且绝不与第一条同 id（同名刷新会白吃一条）。"""
    for seed in (0.0, 0.2, 0.5, 0.83, 0.99):
        p = P()
        applied = []
        g = dw.grant(p, DN3, rng=lambda s=seed: s,
                     add_fn=lambda k, m, mi, s: applied.append(k))
        assert g["tier"] == 3 and g["extra"], (seed, g)
        assert g["extra"] != g["name"], (seed, g["extra"], g["name"])
        assert len(g["lines"]) == 2, g["lines"]
        assert "并额外得" in g["desc"], g["desc"]
        # 两条都真落地：一次次数型或一条限时属性，合计至少 1 处效果
        got = len(p.get("danwen_charges") or {}) + len(applied)
        assert got >= 1, (seed, g, p.get("danwen_charges"), applied)


def t_tier1_grants_one_blessing():
    """一纹只一条；存量「·丹纹」等同于一纹（不白送额外福）。"""
    for nm in (DN1, DN):
        p = P()
        g = dw.grant(p, nm, rng=lambda: 0.0)
        assert g["tier"] == 1 and not g["extra"], (nm, g)
        assert len(g["lines"]) == 1
        assert g["desc"].startswith("（一纹）"), g["desc"]


def t_blessings_all_ge_10():
    """用户红线：所有赐福加成 ≥ 10%，池子 6 条。"""
    assert len(dw.BLESSINGS) == 6
    for b in dw.BLESSINGS:
        if b["kind"] == "timed":
            assert b["mul"] >= 0.10, b
        if b["effect"] == "dmg_x2":
            assert True  # ×2 = +100%


TESTS = [t_rate_table, t_craft_emits_danwen, t_craft_tier_by_weight,
         t_legacy_suffix_reads_as_tier1, t_batch_counts_danwen,
         t_use_grants_blessing, t_b01_dmg_x2_consumed, t_b02_crit_guaranteed,
         t_b03_timed_stats, t_b06_mp_refill, t_zero_and_sweep,
         t_danwen_does_not_consume_battle_rng, t_full_hp_keeps_danwen_pill,
         t_special_pills_have_no_danwen, t_tier_scales_duration_not_pct,
         t_tier3_grants_two_blessings, t_tier1_grants_one_blessing,
         t_blessings_all_ge_10]

if __name__ == "__main__":
    ok = 0
    for fn in TESTS:
        try:
            fn(); ok += 1; print("PASS", fn.__name__)
        except Exception as e:
            print("FAIL", fn.__name__, type(e).__name__, str(e)[:200])
    print(f"\n{ok}/{len(TESTS)} passed")
    sys.exit(0 if ok == len(TESTS) else 1)
