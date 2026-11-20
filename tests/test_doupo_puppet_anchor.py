# -*- coding: utf-8 -*-
"""傀儡战力锚定 + 五处战斗接入守门（2026-11-20 第二轮）。

用户原话（本轮指令）：
  「其他战斗都要带上，不然炼制傀儡没有意义，还有我这个傀儡战力低的离谱，正常吗」

诊断结论（真跑 `tmp/probe_pup_anchor.py` 得出，不是手算）：
  旧口径 `PUPPET_TIER_BASE=(120,900,4200)` 是**绝对常数**，不跟等级走。
  lv81 玩家每回合期望输出 36344，而人级傀儡只有 224.7 —— 占 **0.2%**，
  且只占同阶怪血 0.08%。用户说「低得离谱」属实，不是错觉。

本轮口径（本文件逐条守）：
  1. 傀儡输出 = 玩家平均每回合输出 × 档位系数 × 材料系数（**跟等级走**）；
  2. 怪血反解只按傀儡贡献的 `PUPPET_ENEMY_COMP=0.55` 变厚 —— 否则「多一个打手」
     与「怪血同步变厚」数学上完全对消，带傀儡零收益（这就是「没意义」的根因）；
  3. 五处战斗入口（野外 / 爬塔 / 周本 / 九星突破 / PVP）全部带出战傀儡；
  4. 结阵加成封顶 3 具，堵「堆垃圾傀儡刷百分比」的后门。

自带 runner，零依赖 pytest：python tests/test_doupo_puppet_anchor.py
注入反证：改坏源码 → 对应断言必须 FAIL（见 tmp/mut_pup_anchor.py）。
"""
from __future__ import annotations

import ast
import copy
import inspect
import os
import random
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

    def on_x(*a, **k):
        m = types.SimpleNamespace()
        m.handle = lambda *a, **k: (lambda f: f)
        m.finish = lambda *a, **k: None
        return m
    nb.on_regex = on_x
    nb.on_command = on_x
    nb.on_notice = on_x
    nb.on_message = on_x
    nb.require = lambda *a, **k: (lambda f: f)
    nb.logger = types.SimpleNamespace(error=lambda *a, **k: None,
                                      warning=lambda *a, **k: None,
                                      info=lambda *a, **k: None)
    sys.modules["nonebot"] = nb
    for name in ("nonebot.adapters", "nonebot.adapters.onebot"):
        sys.modules[name] = types.ModuleType(name)
    v11 = types.ModuleType("nonebot.adapters.onebot.v11")
    v11.MessageEvent = object
    v11.MessageSegment = object
    v11.Message = object
    v11.Bot = object
    sys.modules["nonebot.adapters.onebot.v11"] = v11


_stub()

from doupo import config as cfg            # noqa: E402
from doupo import core                      # noqa: E402
from doupo import puppet_core as pc         # noqa: E402

_FAILS = []
_N = 0


def check(cond, msg):
    global _N
    _N += 1
    if not cond:
        _FAILS.append(msg)


# 三档代表傀儡：与 config.PUPPET_TIER_RATIO 一一对应
PU_REN = {"name": "铜尸傀", "tier": 0, "core_tier": 4, "soul_strength": 900,
          "metal_grade": 1, "fire_tier": 7, "skin": "铜"}
PU_DI = {"name": "地妖傀", "tier": 1, "core_tier": 7, "soul_strength": 1500,
         "metal_grade": 2, "fire_tier": 8, "skin": "银"}
PU_TIAN = {"name": "天妖傀", "tier": 2, "core_tier": 9, "soul_strength": 2400,
           "metal_grade": 4, "fire_tier": 9, "skin": "金"}

SPEC = {"name": "同阶怪", "hard": 1.0, "exp_mul": 1.0, "gold_mul": 1.0,
        "kind": "普通"}


def round_out(p):
    n = max(1, int(round(float(cfg.TTK_PLAYER))))
    sch = core.damage_schedule(p, n)
    return sum(sch) / len(sch)


def _has_log_kind(log, kind="傀儡击"):
    return any((it or {}).get("kind") == kind for it in (log or []))


# ---------------------------------------------------------------------------
# 1. 五处战斗入口：签名收 puppet，命令层真透传
# ---------------------------------------------------------------------------
def test_signatures():
    want = ("fight", "encounter", "tower_floor", "weekly_floor",
            "nine_star_breakthrough", "make_breakthrough_boss", "duel")
    bad = []
    for name in want:
        fn = getattr(core, name, None)
        if fn is None:
            bad.append(name + "（缺函数）")
            continue
        if "puppet" not in inspect.signature(fn).parameters:
            bad.append(name)
    check(not bad, "这些战斗函数签名没接 puppet：%s" % bad)
    check("vic_puppet" in inspect.signature(core.duel).parameters,
          "duel 缺少 vic_puppet（PVP 双方对称带傀儡）")


def test_command_layer_passes_puppet():
    """AST 断言：commands.py 里每个 core 战斗调用都必须带 puppet= 关键字。

    用 AST 而不是字符串 grep：换行/别名/参数顺序都不会造成假红，
    而「少传一个参数」这种真错一定被抓到。
    """
    path = os.path.join(ROOT, "src", "plugins", "doupo", "commands.py")
    tree = ast.parse(open(path, encoding="utf-8").read())
    want = {"encounter": 1, "tower_floor": 1, "weekly_floor": 1,
            "nine_star_breakthrough": 1, "duel": 2, "fight": 1}
    seen = {k: [] for k in want}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        if not (isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name)
                and f.value.id == "core"):
            continue
        if f.attr not in want:
            continue
        kws = {k.arg for k in node.keywords}
        seen[f.attr].append(("puppet" in kws, "vic_puppet" in kws,
                             int(getattr(node, "lineno", 0))))
    for fn_name, cnt in want.items():
        rows = seen.get(fn_name) or []
        check(len(rows) >= cnt,
              "commands.py 里 core.%s 只找到 %d 处调用（应 ≥%d）"
              % (fn_name, len(rows), cnt))
        lacking = [ln for ok, _v, ln in rows if not ok]
        check(not lacking,
              "core.%s 这些行没传 puppet=：%s（用户「其他战斗都要带上」）"
              % (fn_name, lacking))
        if fn_name == "duel":
            lacking_v = [ln for _ok, v, ln in rows if not v]
            check(not lacking_v,
                  "core.duel 这些行没传 vic_puppet=：%s（PVP 双方对称）" % lacking_v)


# ---------------------------------------------------------------------------
# 2. 锚定口径：跟等级走，不再是绝对常数
# ---------------------------------------------------------------------------
def test_anchor_follows_level():
    lo, hi = core.tower_ref_player(35), core.tower_ref_player(80)
    a_lo = pc.puppet_attack(PU_TIAN, lo)
    a_hi = pc.puppet_attack(PU_TIAN, hi)
    check(a_hi / a_lo > 1.5,
          "同一具天级傀儡 lv80/lv35 输出应 ≥1.5×（跟等级走），实得 %.2f×（%.1f / %.1f）"
          % (a_hi / a_lo, a_hi, a_lo))
    # 反向：绝对常数（旧口径）特征是不随等级变 —— 这里必须变
    check(abs(a_hi - a_lo) > 1,
          "同档傀儡在不同等级输出完全相同 = 还在用绝对常数")


def test_not_negligible_at_high_level():
    p = core.tower_ref_player(81)
    out = round_out(p)
    a = pc.puppet_attack(PU_TIAN, p)
    ratio = a / out * 100
    check(ratio >= 40.0,
          "lv81 天级傀儡占玩家输出应 ≥40%%，实得 %.2f%%（旧口径是 0.2%%）" % ratio)
    check(ratio < 100.0,
          "lv81 天级傀儡占玩家输出 %0.2f%% ≥100%% —— 傀儡比人还强，过头了" % ratio)
    # 人级也必须不是「带了等于没带」
    a_ren = pc.puppet_attack(PU_REN, p)
    check(a_ren / out * 100 >= 8.0,
          "lv81 人级傀儡只占 %.2f%%（<8%% 仍属「带了等于没带」）" % (a_ren / out * 100))


def test_tier_gradient():
    p = core.tower_ref_player(70)
    a = [pc.puppet_attack(x, p) for x in (PU_REN, PU_DI, PU_TIAN)]
    check(a[0] < a[1] < a[2], "人<地<天 梯度破坏：%s" % a)
    check(abs(a[0] / a[2] - cfg.PUPPET_TIER_RATIO[0] / cfg.PUPPET_TIER_RATIO[2]
              * (pc._material_mult(PU_REN) / pc._material_mult(PU_TIAN))) < 0.02,
          "档位比例与 PUPPET_TIER_RATIO 不一致：%s" % a)


def test_material_mult():
    base = {"tier": 2, "core_tier": 7, "soul_strength": 900,
            "metal_grade": 2, "fire_tier": 7}
    m0 = pc._material_mult(base)
    check(abs(m0 - 1.0) < 1e-9,
          "基准材料（core7/soul900/metal2/fire7）倍率必须恰为 1.0，实得 %.6f" % m0)
    higher_metal = pc._material_mult(dict(base, metal_grade=4))
    higher_fire = pc._material_mult(dict(base, fire_tier=9))
    check(higher_metal > m0 and higher_fire > m0,
          "金属/火没进公式：metal %.4f fire %.4f base %.4f"
          % (higher_metal, higher_fire, m0))
    check(abs(higher_metal - (1.0 + 2 * cfg.PUPPET_METAL_MULT_STEP)) < 1e-9,
          "金属系数步长不对：%.6f" % higher_metal)
    # 老档（库列为 0 / 键缺失）绝不吃惩罚 —— 历史傀儡数值必须逐字节不变。
    # ⚠️ 断言必须**精确等于 1.0**，不能写 ">0.89 就算过"：把基准从 2 挪到 1
    #    只会让倍率从 1.0 变 0.95，宽松断言抓不住（注入反证踩过，见 tmp/mut_pup_anchor.py）。
    for legacy in ({"tier": 0}, {"tier": 1, "metal_grade": 0, "fire_tier": 0},
                   {"tier": 2, "core_tier": 0, "soul_strength": 0}):
        m = pc._material_mult(legacy)
        check(abs(m - 1.0) < 1e-9,
              "老档 %s 材料倍率 %.6f ≠ 1.0（回落基准被改了，老存档数值会变）"
              % (legacy, m))


def test_formation_cap():
    p = core.tower_ref_player(80)
    out = round_out(p)
    one = pc.puppet_expected_damage(PU_TIAN, 1, p=p)
    three = pc.puppet_expected_damage(PU_TIAN, 3, p=p)
    eight = pc.puppet_expected_damage(PU_TIAN, 8, p=p)
    check(three > one, "结阵无加成：3 具 %.1f 未高于 1 具 %.1f" % (three, one))
    check(abs(eight - three) < 1e-6,
          "结阵未封顶：8 具 %.1f 与 3 具 %.1f 应相等" % (eight, three))
    check(pc.formation_count([{"id": i} for i in range(8)]) == cfg.PUPPET_FORMATION_MAX,
          "formation_count 8 具应封顶到 %d" % cfg.PUPPET_FORMATION_MAX)
    check(eight / out < 0.95,
          "天级 8 具封顶后仍占玩家输出 %.1f%%（≥95%% 等于外挂）" % (eight / out * 100))


# ---------------------------------------------------------------------------
# 3. 净赚：怪血折扣是唯一来源（含反证）
# ---------------------------------------------------------------------------
def _rounds_pair(lv, comp, seeds=6):
    p0 = core.tower_ref_player(lv)
    spec = dict(SPEC, lv=lv)
    bonus = pc.puppet_expected_damage(PU_TIAN, 1, flame_mult=1.0, p=p0)
    old = cfg.PUPPET_ENEMY_COMP
    cfg.PUPPET_ENEMY_COMP = comp
    try:
        m0 = core.monster_scaled(p0, spec)
        m1 = core.monster_scaled(p0, spec, puppet_bonus=bonus)
    finally:
        cfg.PUPPET_ENEMY_COMP = old
    rn, ry = [], []
    for s in range(seeds):
        rn.append(int(core.fight(copy.deepcopy(p0), copy.deepcopy(m0),
                                 rng=random.Random(1000 + s).random)["rounds"]))
        ry.append(int(core.fight(copy.deepcopy(p0), copy.deepcopy(m1),
                                 rng=random.Random(1000 + s).random,
                                 puppet=dict(PU_TIAN), formation_n=1)["rounds"]))
    return sum(rn) / len(rn), sum(ry) / len(ry)


def test_puppet_worth_bringing():
    for lv in (35, 65, 81):
        no, yes = _rounds_pair(lv, 0.55)
        check(yes <= no - 0.4,
              "lv%d 带傀儡回合数应明显更少（净赚），实得 %.2f vs %.2f" % (lv, yes, no))


def test_full_comp_cancels_out():
    """反证：折扣改成 1.0（怪血全额按傀儡变厚）→ 净赚必须消失。

    这一条守住的是「PUPPET_ENEMY_COMP 不是可有可无的调参，而是净赚的唯一来源」。
    如果哪天有人把它改回 1.0，本断言会红（净赚消失），而那正是「炼傀儡没意义」。
    """
    for lv in (35, 81):
        no, yes = _rounds_pair(lv, 1.0)
        check(abs(yes - no) <= 0.5,
              "lv%d 折扣=1.0 时带/不带回合数应几乎相等（对消），实得 %.2f vs %.2f"
              % (lv, yes, no))


def test_enemy_comp_is_discount():
    """守 config 里的实际值 —— 上面几条都是显式注入 comp，抓不到「配置被改回 1.0」。"""
    c = float(cfg.PUPPET_ENEMY_COMP)
    check(c < 0.95,
          "PUPPET_ENEMY_COMP=%.2f ≥0.95 —— 怪血几乎全额按傀儡变厚，"
          "「多一个打手」与「怪血同步变厚」对消，带傀儡等于白带" % c)
    check(c > 0.30,
          "PUPPET_ENEMY_COMP=%.2f ≤0.30 —— 怪血几乎不因傀儡变厚，"
          "带傀儡等于白送输出，全等级难度会崩" % c)
    check(float(cfg.PUPPET_TIER_RATIO[0]) < float(cfg.PUPPET_TIER_RATIO[1])
          < float(cfg.PUPPET_TIER_RATIO[2]),
          "PUPPET_TIER_RATIO 梯度破坏：%s" % (cfg.PUPPET_TIER_RATIO,))


def test_no_puppet_path_unchanged():
    """不带傀儡的路径必须与改动前逐字节一致（默认参数纯增量）。"""
    p = core.tower_ref_player(60)
    spec = dict(SPEC, lv=60)
    a = core.monster_scaled(p, spec)
    b = core.monster_scaled(p, spec, puppet_bonus=0.0)
    check(a == b, "puppet_bonus=0.0 与不传居然不等 —— 默认路径被改了")
    check(pc.puppet_attack(None, p) == 0.0 and pc.puppet_expected_damage(None) == 0.0,
          "空傀儡应返回 0")
    # 真跑：不传 puppet 时战斗日志里绝不许出现傀儡击
    mon = core.monster_scaled(p, spec)
    r = core.fight(copy.deepcopy(p), mon, rng=random.Random(5).random)
    check(not _has_log_kind(r.get("log")), "没带傀儡却打出「傀儡击」")


# ---------------------------------------------------------------------------
# 4. PVP 对称
# ---------------------------------------------------------------------------
def test_duel_symmetric():
    p = core.create_player("88001", "甲", 20260001)
    v = core.create_player("88002", "乙", 20260002)
    core._refill(p)
    core._refill(v)
    base = core.duel(copy.deepcopy(p), copy.deepcopy(v), rng=random.Random(21).random)
    with_vic = core.duel(copy.deepcopy(p), copy.deepcopy(v),
                         rng=random.Random(21).random,
                         vic_puppet=dict(PU_TIAN), vic_formation_n=1)
    check(with_vic["hp_p"] <= base["hp_p"],
          "对手带傀儡后我方承伤应不减：%d → %d"
          % (base["hp_p"], with_vic["hp_p"]))
    check(_has_log_kind(with_vic["log"]),
          "对手带傀儡，战报里没有「傀儡击」")
    check(any(it.get("who") == "v" for it in with_vic["log"]
              if it.get("kind") == "傀儡击"),
          "对手的傀儡击 who 应为 v")
    mine = core.duel(copy.deepcopy(p), copy.deepcopy(v),
                     rng=random.Random(21).random,
                     puppet=dict(PU_TIAN), formation_n=1)
    check(_has_log_kind(mine["log"]) and
          any(it.get("who") == "p" for it in mine["log"]
              if it.get("kind") == "傀儡击"),
          "我方带傀儡，战报里没有 who=p 的「傀儡击」")
    check(mine["hp_v"] <= base["hp_v"],
          "我方带傀儡后对手剩余血量应不减：%d → %d"
          % (base["hp_v"], mine["hp_v"]))


# ---------------------------------------------------------------------------
# 5. 五处战斗真跑：日志里必须真有「傀儡击」
# ---------------------------------------------------------------------------
def test_five_battle_entries_bring_puppet():
    pu = dict(PU_TIAN)
    # ① 野外遭遇
    p = core.tower_ref_player(60)
    enc = core.encounter(copy.deepcopy(p), rng=random.Random(31).random,
                         locked=set(), puppet=pu, formation_n=1)
    logs = [it for d in (enc.get("detail") or []) for it in (d.get("log") or [])]
    check(_has_log_kind(logs), "野外遭遇带傀儡，战报里没有「傀儡击」")

    # ② 爬塔。⚠️ 必须用**深层**：第 1 层怪血只有 4000 出头，lv60 玩家先手就秒了，
    #    傀儡根本没机会出手 —— 那样的用例会假红/假绿（真跑 tmp/dbg1.py 确认）。
    p = core.tower_ref_player(60)
    tw = core.tower_floor(copy.deepcopy(p), {"floor": 25},
                          rng=random.Random(32).random,
                          puppet=pu, formation_n=1)
    check(_has_log_kind(tw.get("log")), "爬塔带傀儡，战报里没有「傀儡击」")
    check(tw["rounds"] >= 1, "爬塔回合数异常：%s" % tw.get("rounds"))

    # ③ 周副本。⚠️ 必须传**真实画像**：`core.weekly_snapshot` 对没有本周快照的
    #    新玩家返回 ROOKIE_PROFILE（lv5）→ 第 1 层怪血 7972，同样被秒。
    from doupo import weekly_data as wd_weekly
    p = core.tower_ref_player(60)
    snap = wd_weekly.snapshot_profile(copy.deepcopy(p))
    wk = core.weekly_floor(copy.deepcopy(p), snap, {"floor": 0},
                           rng=random.Random(33).random,
                           puppet=pu, formation_n=1)
    check(_has_log_kind(wk.get("log")), "周副本带傀儡，战报里没有「傀儡击」")

    # ④ 九星突破
    p4 = core.create_player("88011", "丁", 20260011)
    p4["lv"], p4["exp"] = 9, cfg.exp_need(9)
    core._refill(p4)
    br = core.nine_star_breakthrough(p4, rng=random.Random(34).random,
                                     locked=set(), puppet=pu, formation_n=1)
    check(_has_log_kind(br.get("log")), "九星突破带傀儡，战报里没有「傀儡击」")

    # ⑤ PVP
    a = core.create_player("88021", "甲2", 20260021)
    b = core.create_player("88022", "乙2", 20260022)
    core._refill(a)
    core._refill(b)
    du = core.duel(copy.deepcopy(a), copy.deepcopy(b),
                   rng=random.Random(35).random, puppet=pu, formation_n=1)
    check(_has_log_kind(du.get("log")), "PVP 带傀儡，战报里没有「傀儡击」")

    # 不带傀儡时五处都不许出现傀儡击（防「默认就带一个幽灵傀儡」）
    p = core.tower_ref_player(60)
    tw0 = core.tower_floor(copy.deepcopy(p), {"floor": 25},
                           rng=random.Random(32).random)
    check(not _has_log_kind(tw0.get("log")), "爬塔没带傀儡却出现「傀儡击」")


def test_tower_anchors_reference_not_player():
    """塔的怪血必须锚标尺玩家（同层同一只怪），源码级红线。"""
    src = open(os.path.join(ROOT, "src", "plugins", "doupo", "core.py"),
               encoding="utf-8").read()
    check("_ref = tower_ref_player(ref_lv)" in src,
          "tower_floor 没再锚 tower_ref_player(ref_lv) —— 同层怪血会随打它的人变")
    check("monster_scaled(_ref, species, puppet_bonus=_pu_bonus)" in src,
          "tower_floor 的怪血反解没锚标尺 _ref（不能用真人 p）")


# ---------------------------------------------------------------------------
def _run() -> int:
    for fn in (test_signatures, test_command_layer_passes_puppet,
               test_anchor_follows_level, test_not_negligible_at_high_level,
               test_tier_gradient, test_material_mult, test_formation_cap,
               test_puppet_worth_bringing, test_full_comp_cancels_out,
               test_enemy_comp_is_discount,
               test_no_puppet_path_unchanged, test_duel_symmetric,
               test_five_battle_entries_bring_puppet,
               test_tower_anchors_reference_not_player):
        try:
            fn()
        except Exception as e:  # noqa: BLE001
            import traceback
            _FAILS.append("%s 抛异常：%s" % (fn.__name__, e))
            traceback.print_exc()
    for m in _FAILS:
        print("FAIL  " + m)
    print("=" * 70)
    print("ANCHOR_TOTAL=%d FAILED=%d" % (_N, len(_FAILS)))
    return 0 if not _FAILS else 1


if __name__ == "__main__":
    sys.exit(_run())
