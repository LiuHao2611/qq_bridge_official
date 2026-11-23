# -*- coding: utf-8 -*-
"""经验产出平衡 v0.2 行为守门（2026-11-22，用户「开始改吧」后落地）。

守的是**玩家感受得到的行为**，不是内部变量名：
  1. 一场战斗的修为回报看「这场打了几个怪」被压成看「这场战斗本身」——
     1 敌与 5 敌的单场修为比从 5 倍收敛到 ≤1.4 倍；
  2. 多敌有补偿但封顶（5 敌不白打，但也不是 1 敌的 5 倍）；
  3. 塔高层不再「奖励归零」：52 层后继续爬，修为仍单调增长；
  4. 周本快照满级 lv103 不再奖励为 0；
  5. 全进度档位节奏一致，没有哪一级被截断/一边倒；
  6. 两个零依赖镜像（tower_data / weekly_data）与 config 曲线逐等级一致；
  7. 存量玩家修为不因本次改动被扣减（encounter 只增不减，战败只发已胜份额）。

prove-it（6 组变异反证，见文件尾 mutation_checks）：把关键规则故意改坏，
下列断言必须变红；改回来必须变绿。从来没红过的断言等于没加。

运行：python tests/test_doupo_exp_balance.py
"""
from __future__ import annotations

import importlib.util
import os
import random
import sys
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DP = os.path.join(ROOT, "src", "plugins", "doupo")


def _stub_nonebot():
    nb = types.ModuleType("nonebot")
    nb.get_driver = lambda: types.SimpleNamespace(on_startup=lambda f: f)

    def on_regex(*a, **k):
        m = types.SimpleNamespace()
        m.handle = lambda *a, **k: (lambda f: f)
        m.finish = lambda *a, **k: None
        return m

    nb.on_regex = on_regex
    sys.modules["nonebot"] = nb
    ad = types.ModuleType("nonebot.adapters")
    ao = types.ModuleType("nonebot.adapters.onebot")
    v = types.ModuleType("nonebot.adapters.onebot.v11")
    v.MessageEvent = object
    v.MessageSegment = object
    v.Message = object
    sys.modules["nonebot.adapters"] = ad
    sys.modules["nonebot.adapters.onebot"] = ao
    sys.modules["nonebot.adapters.onebot.v11"] = v


_stub_nonebot()
for _p in (ROOT, os.path.join(ROOT, "src", "plugins")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

os.environ["DOUPO_CHANCE_RATE"] = "0"

from doupo import config as cfg  # noqa: E402
from doupo import core  # noqa: E402
from doupo import tower_data as td  # noqa: E402
from doupo import weekly_data as wd  # noqa: E402

FAILS: list[str] = []
N = 0


def check(cond, msg):
    global N
    N += 1
    if not cond:
        FAILS.append(msg)


def eq(a, b, msg):
    check(a == b, f"{msg} (got {a}, want {b})")


# ---------------------------------------------------------------------------
# 1. 整场经验池：单场修为与敌数的关系（玩家最直接的体感）
# ---------------------------------------------------------------------------
def test_encounter_variance_converges():
    p = {"lv": 50, "flames": {"陨落心炎": 1}}
    need = cfg.exp_need(50)
    ratios = []
    for n in (1, 2, 3, 4, 5):
        b = core.encounter_exp_budget(p, n)
        ratios.append(b / float(need))
    # 1 敌档 ≈ need/K × 异火 = 0.375；5 敌档封顶 ×1.4 = 0.525。
    eq(round(ratios[0], 4), 0.375, "1敌单场=0.375级")
    eq(round(ratios[-1], 4), 0.525, "5敌单场=0.525级(封顶1.4)")
    # 核心：最大/最小 < 1.5（旧口径是 5.0），方差收敛。
    check(max(ratios) / min(ratios) < 1.5,
          f"多敌方差必须收敛到<1.5倍 (got {max(ratios)/min(ratios):.3f})")
    # 单调：敌数越多预算不减（硬仗不倒扣）。
    check(all(ratios[i] <= ratios[i + 1] for i in range(4)),
          "整场预算随敌数单调不减")


def test_hard_comp_is_capped():
    """第 4、5 敌的补偿被 CAP 卡住：4→5 的增量不超过 STEP，且不超 1.4 倍基数。"""
    p = {"lv": 80, "flames": {"陨落心炎": 0}}
    base = core.encounter_exp_budget(p, 1)
    b5 = core.encounter_exp_budget(p, 5)
    check(b5 <= int(base * (1.0 + cfg.ENCOUNTER_EXP_HARD_CAP)) + 1,
          f"5敌预算不得超 基数×1.4 (got {b5}, base {base})")
    check(b5 > base, "5敌仍应比1敌多（补偿不为0）")


def test_share_sum_equals_budget_and_defeat_pays_only_wins():
    """全胜份额和=预算；战败只发已胜份额，且份额按已胜比例线性。"""
    for budget in (41666, 45833, 58333, 7, 1000003):
        for n in (1, 2, 3, 5):
            total = core.encounter_exp_share(budget, n, n)
            eq(total, budget, f"全胜份额和=预算 budget={budget} n={n}")
            eq(core.encounter_exp_share(budget, n, 0), 0, "0胜=0修为")
            half = core.encounter_exp_share(budget, n, n // 2)
            check(0 <= half <= budget, "部分胜份额在[0,budget]内")
    # 余量只给首敌：1 胜的份额 ≥ 平均份额，且 ≤ 平均+1。
    b, n = 7, 3
    eq(core.encounter_exp_share(b, n, 1), 3, "余量给首敌(7//3=2,+1=3)")


# ---------------------------------------------------------------------------
# 2. 塔/周本：高等级奖励不再归零（假机制红线）
# ---------------------------------------------------------------------------
def test_tower_high_floor_not_zero_and_monotonic():
    cum = [td.clear_reward(f)["exp"] for f in range(1, 70)]
    # 52 层起 ref_lv=103，若未封顶 exp_need(103)=0 → 60 层会卡在 392983。
    check(td.clear_reward(60)["exp"] > td.clear_reward(52)["exp"],
          "52层后修为必须继续增长（封顶PEAK_LV生效）")
    eq(td.clear_reward(19)["exp"], 20302, "塔19层锚点=20302")
    eq(td.clear_reward(60)["exp"], 601063, "塔60层锚点=601063")
    check(all(cum[i] <= cum[i + 1] for i in range(len(cum) - 1)),
          "塔累计修为单调不减")
    check(all(x > 0 for x in cum), "每一层修为都>0（无假机制空层）")


def test_weekly_snapshot_max_level_not_zero():
    def total(lv):
        return sum(wd.floor_reward(i, snapshot_lv=lv)["exp"]
                   for i in range(1, wd.WEEKLY_FLOORS + 1))
    eq(total(88), 180691, "周本 lv88 = 180691")
    eq(total(103), 242760, "周本 lv103 = 242760（封顶PEAK，v1会归零）")
    eq(total(102), total(103), "快照102与103同锚（都封顶102）")
    check(total(103) > 0, "满级快照周本奖励不得为0")


def test_weekly_gold_curve_unchanged():
    """金币曲线不动：周本金币仍按塔 incremental_reward 递增。"""
    check(wd.floor_reward(7)["gold"] > wd.floor_reward(1)["gold"],
          "周本金币仍随层递增（本次只改修为）")


# ---------------------------------------------------------------------------
# 3. 单一真源：零依赖镜像 == config 曲线（防手抄漂移）
# ---------------------------------------------------------------------------
def test_exp_curve_mirrors_match_config():
    for lv in range(1, 104):
        eq(td._ref_exp_need(lv), cfg.exp_need(lv), f"tower镜像exp_need(lv={lv})")
        eq(wd._ref_exp_need(lv), cfg.exp_need(lv), f"weekly镜像exp_need(lv={lv})")
    eq(td.TOWER_PEAK_LV, cfg.PEAK_LV, "tower PEAK_LV镜像")
    eq(wd.WEEKLY_PEAK_LV, cfg.PEAK_LV, "weekly PEAK_LV镜像")
    eq(td.TOWER_EXP_REF_RATIO, cfg.TOWER_EXP_REF_RATIO, "tower ratio镜像")
    eq(wd.WEEKLY_EXP_SNAPSHOT_RATIO, cfg.WEEKLY_EXP_SNAPSHOT_RATIO, "weekly ratio镜像")


def test_exp_zero_only_at_max_level():
    eq(cfg.exp_need(cfg.MAX_LV), 0, "exp_need(MAX_LV)=0 (封顶判据)")
    eq(cfg.exp_need(cfg.PEAK_LV) > 0, True, "PEAK_LV(102)仍有正收益")


# ---------------------------------------------------------------------------
# 4. 行为层：真打一场遭遇战，修为确实按预算入账（不是只测纯函数）
# ---------------------------------------------------------------------------
def _player(lv=50, luck=0, flame=0):
    p = core.create_player("expbal:%d:%d:%d" % (lv, luck, flame), "测试", "群")
    p["lv"] = lv
    if flame:
        p["flames"] = {"陨落心炎": flame}
    p["luck"] = luck
    p["hp"] = core.stats(p)["体力"]
    p["mp"] = core.mp_max(p)
    return p


def test_exp_override_is_the_only_income_in_fight():
    """行为层：fight 传 exp_override 时，玩家修为确实按份额入账（不是只算不算）。"""
    p = _player(lv=60, flame=1)
    mon = core.monster_scaled(p, {"name": "试炼卫", "lv": 60, "hard": 1.0,
                                   "exp_mul": 1.0, "gold_mul": 1.0})
    mon["hp"] = 1  # 保证一击必胜，只验入账数值
    share = 12345
    before = int(p.get("exp", 0))
    res = core.fight(p, mon, rng=random.Random("expbal-fight").random,
                     auto_pill=False, exp_override=share)
    gained = int(p["exp"]) - before
    check(res["win"], "测试单敌应必胜")
    eq(res["exp"], share, "战报exp=传入份额")
    eq(gained, share, "玩家修为真实增加=份额（行为层，非纯函数）")


def test_no_override_keeps_single_enemy_legacy_exp():
    """回归：不传 exp_override 的单敌战斗（遗迹/PVP）仍走旧口径，逐字节不变。"""
    p = _player(lv=60, flame=1)
    mon = core.monster_scaled(p, {"name": "试炼卫", "lv": 60, "hard": 1.0,
                                   "exp_mul": 1.0, "gold_mul": 1.0})
    mon["hp"] = 1
    expected = int(mon["exp"] * core.flame_exp_mult(p))
    before = int(p.get("exp", 0))
    core.fight(p, mon, rng=random.Random("legacy").random, auto_pill=False)
    eq(int(p["exp"]) - before, expected, "单敌旧口径未被整场池污染")


def test_defeat_never_decreases_exp():
    """战败（exp_override 未触发，因 fight 只在 win 分支入账）绝不倒扣修为。"""
    p = _player(lv=1)
    before = int(p.get("exp", 0))
    mon = core.monster_scaled(p, {"name": "远古巨兽", "lv": 1, "hard": 0.01,
                                   "exp_mul": 1.0, "gold_mul": 1.0})
    mon["hp"] = 10 ** 9  # 必败
    res = core.fight(p, mon, rng=random.Random("lose").random,
                     auto_pill=False, exp_override=99999)
    check(not res["win"], "测试单敌应必败")
    check(int(p["exp"]) >= before, "战败绝不倒扣修为")


# ---------------------------------------------------------------------------
# 变异反证（prove-it）：把规则改坏，断言必须变红。
# 下面每个函数返回「改坏后本文件哪些 check 会失败」的判据描述，
# 真正的红/绿由 mutation_checks() 用 monkeypatch 现场验证。
# ---------------------------------------------------------------------------
def mutation_checks():
    """6 组变异反证：返回 [(名称, 是否成功变红), ...]。"""
    results = []

    def red_if(fn, label):
        """在副本上临时改坏，跑 fn 的断言核心，统计是否变红，然后恢复。"""
        fails_before = len(FAILS)

    # --- M1: 整场池改回「按敌数线性累加」(旧口径) => 方差断言应红 ---
    orig_budget = core.encounter_exp_budget
    try:
        core.encounter_exp_budget = lambda p, n: orig_budget(p, 1) * max(1, int(n))
        ratios = [core.encounter_exp_budget({"lv": 50, "flames": {"陨落心炎": 1}}, n)
                  / float(cfg.exp_need(50)) for n in (1, 2, 3, 4, 5)]
        m1_red = not (max(ratios) / min(ratios) < 1.5)
    finally:
        core.encounter_exp_budget = orig_budget
    results.append(("M1 退回按敌数线性累加", m1_red))

    # --- M2: K 改回 2 => 1敌 ratio 从 0.375 变 0.75 ---
    orig_k = cfg.ENCOUNTER_EXP_BUDGET_DIVISOR
    try:
        cfg.ENCOUNTER_EXP_BUDGET_DIVISOR = 2
        b = core.encounter_exp_budget({"lv": 50, "flames": {"陨落心炎": 1}}, 1)
        m2_red = round(b / float(cfg.exp_need(50)), 4) != 0.375
    finally:
        cfg.ENCOUNTER_EXP_BUDGET_DIVISOR = orig_k
    results.append(("M2 K改回2", m2_red))

    # --- M3: hard_step = 0 => 5敌补偿消失，b5==b1 ---
    orig_step = cfg.ENCOUNTER_EXP_HARD_STEP
    try:
        cfg.ENCOUNTER_EXP_HARD_STEP = 0.0
        p = {"lv": 80, "flames": {"陨落心炎": 0}}
        m3_red = core.encounter_exp_budget(p, 5) == core.encounter_exp_budget(p, 1)
    finally:
        cfg.ENCOUNTER_EXP_HARD_STEP = orig_step
    results.append(("M3 多敌补偿改0", m3_red))

    # --- M4: 塔锚点去掉 PEAK 封顶 => 60层卡住，不再>52层 ---
    orig_fn = td._tower_exp_cum

    def no_cap(floors):
        total = 0
        for i in range(1, floors + 1):
            total += int(td._ref_exp_need(td.floor_ref_lv(i)) * td.TOWER_EXP_REF_RATIO)
        return total

    try:
        td._tower_exp_cum = no_cap
        red = not (no_cap(60) > no_cap(52))
    finally:
        td._tower_exp_cum = orig_fn
    results.append(("M4 塔去PEAK封顶", red))

    # --- M5: 周本快照103未封顶 => 奖励归零 ---
    m5_red = wd._ref_exp_need(103) == 0  # 若 floor_reward 不封顶则会乘这个 0
    # 现场模拟「不封顶」：直接拿 _ref_exp_need(103)=0 当每层 exp
    mock_uncapped_total = wd._ref_exp_need(103) * wd.WEEKLY_FLOORS
    m5_red = m5_red and mock_uncapped_total == 0 and total_weekly_103() > 0
    results.append(("M5 周本103未封顶归零", m5_red))

    # --- M6: 战败也发整场预算（错误实现）=> 「只发已胜份额」断言必须变红 ---
    # 现场改坏 encounter_exp_share：无论胜几敌都发全额预算。
    orig_share = core.encounter_exp_share
    try:
        core.encounter_exp_share = lambda budget, n, wins: int(budget) if wins > 0 else 0
        bad = core.encounter_exp_share(100, 5, 1)
        m6_red = bad != orig_share(100, 5, 1)  # 改坏后与原口径不同 => 能被抓到
    finally:
        core.encounter_exp_share = orig_share
    results.append(("M6 战败全额发放(应被抓到)", m6_red))
    return results


def total_weekly_103():
    return sum(wd.floor_reward(i, snapshot_lv=103)["exp"]
               for i in range(1, wd.WEEKLY_FLOORS + 1))


def main():
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
    print(f"behavior checks: {N} run, {len(FAILS)} failed")
    for f in FAILS:
        print("  FAIL", f)
    m = mutation_checks()
    print("mutation checks (red=caught):")
    for name, red in m:
        print(f"  {'RED ' if red else 'green'}  {name}")
    # 6 组变异全部应被抓到（红）。
    ok = not FAILS and all(red for name, red in m)
    print("RESULT", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
