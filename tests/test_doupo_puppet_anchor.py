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


# 三档代表傀儡：tier 与**躯体**共同决定战力（2026-11-20 第三轮口径）。
# body_realm/body_star 取自 cfg.PUPPET_BODY_TIERS：躯体 n 星 → 战力 n−2 星。
PU_REN = {"name": "铜尸傀", "tier": 0, "core_tier": 4, "soul_strength": 900,
          "metal_grade": 1, "fire_tier": 7, "skin": "铜",
          "body_name": "强者躯体", "body_realm": "斗皇", "body_star": 0}
PU_DI = {"name": "地妖傀", "tier": 1, "core_tier": 7, "soul_strength": 1500,
         "metal_grade": 2, "fire_tier": 8, "skin": "银",
         "body_name": "斗宗强者之躯", "body_realm": "斗宗", "body_star": 7}
PU_TIAN = {"name": "天妖傀", "tier": 2, "core_tier": 9, "soul_strength": 2400,
           "metal_grade": 4, "fire_tier": 9, "skin": "金",
           "body_name": "斗尊强者之躯", "body_realm": "斗尊", "body_star": 0}
# 北王傀：斗圣之躯（五星），后期唯一能跟上斗圣玩家的料。
PU_BEI = {"name": "北王傀", "tier": 2, "core_tier": 9, "soul_strength": 3000,
          "metal_grade": 4, "fire_tier": 9, "skin": "金",
          "body_name": "北龙王之躯", "body_realm": "斗圣", "body_star": 5}

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
    """人<地<天 单调，且**由躯体境界驱动**（不是比例常数）。"""
    p = core.tower_ref_player(70)
    a = [pc.puppet_attack(x, p) for x in (PU_REN, PU_DI, PU_TIAN)]
    check(a[0] < a[1] < a[2], "人<地<天 梯度破坏：%s" % a)
    # 旧的「档位比例必须等于 PUPPET_TIER_RATIO」断言已作废：本轮起三档的差距
    # 来自躯体境界（斗皇/斗宗/斗尊），不再是 0.15/0.30/0.50 的线性比例。
    check(not hasattr(cfg, "PUPPET_TIER_RATIO"),
          "cfg.PUPPET_TIER_RATIO 复活了 —— 旧口径（玩家输出百分比）已被"
          "「躯体境界锚定」取代，两套并存会让战力口径分叉")
    check(not hasattr(cfg, "PUPPET_REF_LV"),
          "cfg.PUPPET_REF_LV 复活了 —— 已改名 PUPPET_TIER_REALM_LV（语义从"
          "「标尺等级」变成「该档标准躯体的折算境界」）")


def test_realm_comes_from_body():
    """战力境界必须由**躯体**决定，且按原著折 n−2 星（第757处）。"""
    # 七星斗宗之躯 → 「不会比一名五星乃至六星的斗宗强者弱」
    check(pc.puppet_realm_lv(PU_DI) == cfg.realm_lv(7) + 4,
          "七星斗宗之躯应折成五星斗宗（lv%d），实得 lv%d"
          % (cfg.realm_lv(7) + 4, pc.puppet_realm_lv(PU_DI)))
    check(cfg.full_realm(pc.puppet_realm_lv(PU_DI)) == "五星斗宗",
          "折算称呼应为「五星斗宗」，实得 %s"
          % cfg.full_realm(pc.puppet_realm_lv(PU_DI)))
    # 同一 tier 下，换更好的躯体会更强（这条守住「料好就该更强」）
    better = dict(PU_TIAN, body_name="北龙王之躯",
                  body_realm="斗圣", body_star=5)
    check(pc.puppet_attack(better) > pc.puppet_attack(PU_TIAN),
          "斗圣之躯的天级傀儡应强于斗尊之躯：%.0f vs %.0f"
          % (pc.puppet_attack(better), pc.puppet_attack(PU_TIAN)))
    # 境界标签必须与材料一致
    check(pc.puppet_realm_label(PU_DI) == "斗宗级",
          "地妖傀标签应为「斗宗级」，实得 %s" % pc.puppet_realm_label(PU_DI))
    check(pc.puppet_realm_label(PU_BEI) == "斗圣级",
          "北王傀标签应为「斗圣级」，实得 %s" % pc.puppet_realm_label(PU_BEI))


def test_old_save_falls_back_not_scrap():
    """老存档 body_realm 为空时回落该档标准躯体，绝不许掉成废铁。

    ⚠️ 断言写**绝对境界名**，不拿 ``cfg.PUPPET_TIER_REALM_LV`` 当期望值 ——
       拿配置当期望，配置被改成 (1,1,1) 时测试会跟着一起过（注入反证实测白守）。
    """
    p = core.tower_ref_player(81)
    want_realm = ("斗皇", "斗宗", "斗尊")   # 三档标准躯体的境界
    for pu, tier in ((PU_REN, 0), (PU_DI, 1), (PU_TIAN, 2)):
        old = {k: v for k, v in pu.items()
               if k not in ("body_realm", "body_star", "body_name")}
        lv = pc.puppet_realm_lv(old)
        check(cfg.realm_name(lv) == want_realm[tier],
              "tier%d 无 body_realm 应回落到%s级，实得 %s（lv%d）"
              % (tier, want_realm[tier], cfg.realm_name(lv), lv))
        check(pc.puppet_attack(old, p) > 0,
              "tier%d 老存档战力算成 0 —— 变成废铁了" % tier)


def test_cap_survives_material_bonus():
    """封顶必须管**最终值**：好材料/结阵/异火都不许把「不抢风头」撑破。"""
    own = round_out(core.tower_ref_player(81))
    for t, pu in ((0, PU_REN), (1, PU_DI), (2, PU_TIAN)):
        cap = float(cfg.PUPPET_ATK_CAP[t])
        # ① 单体（材料系数已含）
        a = pc.puppet_attack(pu, core.tower_ref_player(81))
        check(a <= own * cap + 1e-6,
              "tier%d 单体 %.0f 超过封顶 %.0f（自己 %.0f × %.2f）"
              % (t, a, own * cap, own, cap))
        # ② 结阵 3 具 + 异火 1.3 倍 —— 两个乘数都不许突破封顶
        d = pc.puppet_expected_damage(
            pu, 3, flame_mult=1.3, p=core.tower_ref_player(81))
        check(d <= own * cap + 1e-6,
              "tier%d 结阵+异火后 %.0f 超过封顶 %.0f（结阵/异火把封顶撑破了）"
              % (t, d, own * cap))


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
    # ⚠️ 结阵收益要用**没贴顶的档**验（人级 lv80 只到 52%，离 55% 封顶还有余量）。
    #    天级已经贴顶 95%，结阵必然被收口 —— 那是设计意图（不抢风头），不是 bug。
    one = pc.puppet_expected_damage(PU_REN, 1, p=p)
    three = pc.puppet_expected_damage(PU_REN, 3, p=p)
    eight = pc.puppet_expected_damage(PU_REN, 8, p=p)
    check(three > one, "结阵无加成：3 具 %.1f 未高于 1 具 %.1f" % (three, one))
    check(abs(eight - three) < 1e-6,
          "结阵未封顶：8 具 %.1f 与 3 具 %.1f 应相等" % (eight, three))
    check(pc.formation_count([{"id": i} for i in range(8)]) == cfg.PUPPET_FORMATION_MAX,
          "formation_count 8 具应封顶到 %d" % cfg.PUPPET_FORMATION_MAX)
    # 天级：无论结阵几具，最终输出都不许越过封顶（结阵/8 具一起验）
    tian8 = pc.puppet_expected_damage(PU_TIAN, 8, p=p)
    check(tian8 / out <= float(cfg.PUPPET_ATK_CAP[2]) + 1e-6,
          "天级 8 具封顶后仍占玩家输出 %.1f%%（超过封顶 %.0f%% 等于外挂）"
          % (tian8 / out * 100, float(cfg.PUPPET_ATK_CAP[2]) * 100))


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
    check(float(cfg.PUPPET_ATK_CAP[0]) < float(cfg.PUPPET_ATK_CAP[1])
          < float(cfg.PUPPET_ATK_CAP[2]),
          "PUPPET_ATK_CAP 梯度破坏：%s" % (cfg.PUPPET_ATK_CAP,))
    check(float(cfg.PUPPET_ATK_CAP[2]) < 1.0,
          "PUPPET_ATK_CAP[2]=%.2f ≥1.0 —— 天级傀儡可追平玩家全体输出，"
          "「不抢风头」红线破了" % float(cfg.PUPPET_ATK_CAP[2]))
    # 旧口径常量必须彻底退场（两套并存会让战力口径分叉）
    for ghost in ("PUPPET_TIER_RATIO", "PUPPET_REF_LV"):
        check(not hasattr(cfg, ghost), "cfg.%s 复活了 —— 旧比例口径已弃用" % ghost)


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


def test_card_shows_realm_label():
    """卡面必须给出「战力 X级」，且整段不许出现嵌套括号。"""
    from doupo import card
    p = core.tower_ref_player(81)
    data = card.build_puppet_data([dict(PU_DI, id=1), dict(PU_TIAN, id=2)],
                                  {"active_id": 1}, p)
    blob = "\n".join("\n".join(x["lines"]) for x in data["puppets"])
    check("战力 " in blob, "傀儡卡没有战力对标行：%s" % blob)
    check("斗宗级" in blob, "地级（七星斗宗躯）卡面没标「斗宗级」：%s" % blob)
    check("斗尊级" in blob, "天级（斗尊躯）卡面没标「斗尊级」：%s" % blob)
    # 嵌套括号：``（斗皇级（受你境界所限），不吃斗气/斗技）`` 这种（实测踩过）
    for seg in blob.split("\n"):
        if seg.count("（") > 1 or seg.count("(") > 1:
            check(False, "卡面出现嵌套括号：%s" % seg)
    # 数字必须与 puppet_attack 同源，不许渲染层另算
    check(data["puppets"][1]["atk"] == str(int(pc.puppet_attack(PU_TIAN, p))),
          "卡面攻击与 puppet_attack 不同源：%s vs %s"
          % (data["puppets"][1]["atk"], int(pc.puppet_attack(PU_TIAN, p))))


def test_fight_single_source():
    """fight 的傀儡输出必须来自 ``puppet_expected_damage``（唯一函数）。

    用 ``puppet_attack`` 会漏掉结阵与异火，而 ``monster_scaled`` 的反解是按前者
    让怪变厚的 —— 两处不同源就是「带结阵反而吃亏」（第二轮遗留 bug，本轮修）。
    """
    src = open(os.path.join(ROOT, "src", "plugins", "doupo", "core.py"),
               encoding="utf-8").read()
    check("_pup_atk = _pc0.puppet_expected_damage(" in src,
          "fight 的 _pup_atk 没走 puppet_expected_damage —— 结阵/异火会漏算，"
          "而怪血反解是按它算的，带结阵反而吃亏")


def test_tower_anchors_reference_not_player():
    """塔的怪血必须锚标尺玩家（同层同一只怪），源码级红线。"""
    src = open(os.path.join(ROOT, "src", "plugins", "doupo", "core.py"),
               encoding="utf-8").read()
    check("_ref = tower_ref_player(ref_lv)" in src,
          "tower_floor 没再锚 tower_ref_player(ref_lv) —— 同层怪血会随打它的人变")
    check("monster_scaled(_ref, species, puppet_bonus=_pu_bonus)" in src,
          "tower_floor 的怪血反解没锚标尺 _ref（不能用真人 p）")


# ---------------------------------------------------------------------------
# P0-1 结阵临时升档（2026-11-20 设计文档）：成阵=升档，不是 +15% 百分比
# ---------------------------------------------------------------------------
def test_formation_burst_same_tier_three():
    """3 具同档才升档，且绝不改原对象（写回=永久升档白嫖）。"""
    roster = [dict(PU_REN, name="铜尸甲"), dict(PU_REN, name="铜尸乙"),
              dict(PU_REN, name="铜尸丙")]
    src = copy.deepcopy(PU_REN)
    up = pc.formation_burst(src, roster, 3)
    check(bool(up.get("_formation_burst")), "3 具同档未标记成阵")
    check(int(up.get("tier", 0)) == src["tier"] + 1,
          "成阵未升一档：%s → %s" % (src["tier"], up.get("tier")))
    check(src == PU_REN and "_formation_burst" not in src,
          "formation_burst 就地改了传入的存档 dict —— 会写回成永久升档")
    check(pc.formation_burst(src, roster[:2], 2) is src, "2 具不该成阵")
    mixed = [dict(PU_REN), dict(PU_REN), dict(PU_DI)]
    check(pc.formation_burst(src, mixed, 3) is src, "混档不该成阵")
    check(pc.formation_burst(PU_TIAN, [dict(PU_TIAN)] * 3, 3) is PU_TIAN,
          "天级没有更高档，不该再从伤害上升档")


def test_formation_burst_low_level_not_zero():
    """旧结阵在低等级全 +0%（假机制）；升档后必须真的打出来。"""
    for lv in (20, 50, 81):
        p = core.tower_ref_player(lv)
        roster = [dict(PU_REN, name="甲"), dict(PU_REN, name="乙"),
                  dict(PU_REN, name="丙")]
        up = pc.formation_burst(PU_REN, roster, 3)
        old = pc.puppet_expected_damage(PU_REN, 3, p=p)
        new = pc.puppet_expected_damage(up, 3, p=p)
        cap = round_out(p) * float(cfg.PUPPET_ATK_CAP[1])
        check(new > old, "lv%d 升档后 %.0f 未高于旧结阵 %.0f" % (lv, new, old))
        check(new <= cap + 1.0,
              "lv%d 升档后 %.0f 越过新档 CAP %.0f（不抢风头红线）" % (lv, new, cap))


def test_formation_burst_battle_notes_no_persist():
    """战报必须写「阵成/退档」，且临时副本不能写回原 dict。"""
    src = copy.deepcopy(PU_REN)
    roster = [dict(PU_REN, name="甲"), dict(PU_REN, name="乙"),
              dict(PU_REN, name="丙")]
    up = pc.formation_burst(src, roster, 3)
    p = core.tower_ref_player(50)
    r = core.fight(p, core.monster_scaled(p, dict(SPEC, lv=50)),
                   rng=random.Random(1).random, puppet=up,
                   formation_n=3, auto_pill=True)
    check(bool(r.get("formation_burst")), "res 未标记本场升档")
    all_notes = []
    for e in (r.get("log") or []):
        all_notes.extend(e.get("notes") or [])
    check(any("阵成" in str(n) for n in all_notes), "战报缺「阵成」高光提示")
    check(any("金光散" in str(n) for n in all_notes), "战报缺「退档」提示，玩家会以为永久升档")
    check(src == PU_REN and "_formation_burst" not in src,
          "战斗把临时升档写回了原 dict")


# ---------------------------------------------------------------------------
def _run() -> int:
    for fn in (test_signatures, test_command_layer_passes_puppet,
               test_anchor_follows_level, test_not_negligible_at_high_level,
               test_tier_gradient, test_realm_comes_from_body,
               test_old_save_falls_back_not_scrap, test_cap_survives_material_bonus,
               test_material_mult, test_formation_cap,
               test_puppet_worth_bringing, test_full_comp_cancels_out,
               test_enemy_comp_is_discount,
               test_no_puppet_path_unchanged, test_duel_symmetric,
               test_five_battle_entries_bring_puppet,
               test_card_shows_realm_label,
               test_fight_single_source,
               test_tower_anchors_reference_not_player,
               test_formation_burst_same_tier_three,
               test_formation_burst_low_level_not_zero,
               test_formation_burst_battle_notes_no_persist):
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
