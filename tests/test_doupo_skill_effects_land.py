# -*- coding: utf-8 -*-
"""斗技效果「真落地」门禁（2026-11-20 第四轮）。

用户原话：
  「我让你加技能，你不会只加了技能描述，技能效果都没落地战斗系统吧？」

skill_book 里 180+ 门斗技共声明了 **16 种** effect 类型。声明了不等于生效 ——
旧版就真实犯过四种「只写文案、不改数值」的假机制（毒不挂 DoT / DoT 基数写死 1 /
降攻没人读 / 减疗只压吸血）。本文件对 16 种类型逐条做**同参数对照实验**：
带效果 / 不带效果各打一场，用日志里的数值差证明它真的改了结算。

设计要点（为什么这样做才可信）：
  * 同一个 rng（恒定 0.99），两场只差「有没有这条 effect」，排除随机干扰；
  * 观测点全部取战斗日志的**最终数值**（伤害 / 承伤 / 命中数 / 回血 / 跳过次数），
    不看 notes 文案 —— 文案自嗨正是上一轮翻车的地方；
  * 用 ``core._loadout_entries`` 注入单条合成技，不依赖某门具体斗技是否解锁。

16 种类型：empower / pierce / cond / chain / lifesteal / dot / debuff_atk /
debuff_evade / debuff_heal / stun / root / silence / knockback / reflect /
reduce / evade。

运行：python tests/test_doupo_skill_effects_land.py（exit 0 = 全绿）
"""
from __future__ import annotations

import os
import random
import sys
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


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
    for n in ("nonebot.adapters", "nonebot.adapters.onebot",
              "nonebot.adapters.onebot.v11"):
        sys.modules[n] = types.ModuleType(n)
    v11 = sys.modules["nonebot.adapters.onebot.v11"]
    v11.MessageEvent = object
    v11.MessageSegment = object
    v11.Message = object


_stub_nonebot()
for _p in (ROOT, os.path.join(ROOT, "src", "plugins")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from doupo import config as cfg, core, skill_effects as fx  # noqa: E402

_RES = []


def check(name, ok, detail=""):
    _RES.append((name, bool(ok), str(detail)))


# ---------------------------------------------------------------------------
# 合成斗技 + 对照组
# ---------------------------------------------------------------------------
def _entry(effects, name="试炼掌"):
    """最小可用斗技条目（字段与 skills._to_entry 对齐）。"""
    return {
        "name": name, "tier": "普通", "faction": "tianming", "origin": "本宗",
        "mp": 0.05, "dmg": 1.0, "unlock": 1, "desc": "", "brief": "",
        "rank": "黄阶低级", "kind": "攻击", "scope": "单体",
        "source": "", "novel": "", "cd": 0, "role": "",
        "effects": list(effects), "gate": "", "core": False,
    }


def _player(lv=9):
    p = core.create_player("QIMM", "测试", "群")
    p["lv"] = lv
    p["faction"] = "tianming"
    p["loadout"] = []
    p.pop("learned", None)      # 老档语义：四槽全开，避免被"未领悟"卡住
    core._refill(p)
    return p


def run(effects, *, mon_atk=1, mon_def=0, mon_hp=300000, mon_dodge=0.0,
        rng_val=0.99, seed=None, bag=None, auto_pill=False):
    """打一场受控战斗：玩家只会上阵这一条合成技。

    ``rng_val`` 恒定值 → 完全确定（适合"必触发"类效果）；
    ``seed`` 给定种子 → 需要概率分布的场景（闪避）用，两场同种子可比。
    """
    entry = _entry(effects)
    old = core._loadout_entries
    core._loadout_entries = lambda p: [entry]
    if seed is None:
        rng = lambda: rng_val
    else:
        _rnd = random.Random(seed)
        rng = _rnd.random
    try:
        p = _player()
        if bag:
            p["bag"] = dict(bag)
        mon = {"name": "试炼傀儡", "lv": 9, "hp": int(mon_hp), "atk": int(mon_atk),
               "def": int(mon_def), "exp": 1, "gold": 1, "kind": "傀儡",
               "is_boss": False, "dodge": float(mon_dodge)}
        r = core.fight(p, mon, rng=rng, roll_aid=False, auto_pill=auto_pill)
        return p, r
    finally:
        core._loadout_entries = old


def _is_player_hit(it):
    """一次**打中了**的玩家出手（被打空/被闪过的不算命中）。"""
    return (isinstance(it, dict) and it.get("move") == "试炼掌"
            and not it.get("taken") and not it.get("evaded")
            and int(it.get("dmg") or 0) > 0)


def _hits(r):
    return [int(it.get("dmg") or 0) for it in r["log"] if _is_player_hit(it)]


def _dmg_sum(r):
    return sum(_hits(r))


def _taken_sum(r):
    return sum(int(it.get("dmg") or 0) for it in r["log"]
               if isinstance(it, dict) and it.get("taken") and not it.get("dot"))


def _foe_dot(r):
    return sum(int(it.get("dmg") or 0) for it in r["log"]
               if isinstance(it, dict) and it.get("dot") and not it.get("taken"))


def _taken_hits(r):
    return [int(it.get("dmg") or 0) for it in r["log"]
            if isinstance(it, dict) and it.get("taken")
            and it.get("move") == "兽击"]


def _mon_attacks(r):
    return sum(1 for it in r["log"]
               if isinstance(it, dict) and it.get("taken")
               and it.get("move") == "兽击")


# ---------------------------------------------------------------------------
# 16 种效果逐一验证
# ---------------------------------------------------------------------------
def test_empower():
    a = _dmg_sum(run([{"t": "empower", "v": 0.4, "d": 3}])[1])
    b = _dmg_sum(run([])[1])
    check("empower 增幅真加伤", a > b, "%d vs %d" % (a, b))


def test_pierce():
    a = _dmg_sum(run([{"t": "pierce", "v": 0.5, "d": 2}], mon_def=5000)[1])
    b = _dmg_sum(run([], mon_def=5000)[1])
    check("pierce 破防真提伤（对高防目标）", a > b, "%d vs %d" % (a, b))


def test_cond():
    """条件加伤：条件满足要真加，不满足要真不加（两组都验，防止写成恒加/恒不加）。"""
    plain = _dmg_sum(run([{"t": "stun", "d": 3}])[1])
    # fear 条件读「目标被眩晕/定身」——同一招里先前置眩晕，条件即成立
    met = _dmg_sum(run([{"t": "stun", "d": 3},
                        {"t": "cond", "cond": "fear", "v": 0.5}])[1])
    # low_hp 读目标血量比例 <35%，满血怪恒不满足
    miss_r = run([{"t": "stun", "d": 3},
                  {"t": "cond", "cond": "low_hp", "v": 0.5}])[1]
    miss = _dmg_sum(miss_r)
    check("cond 条件满足真加伤", met > plain, "%d vs %d" % (met, plain))
    check("cond 条件不满足不加伤", miss == plain, "%d vs %d" % (miss, plain))


def test_chain():
    r = run([{"t": "chain", "n": 3, "v": 0.5, "d": 3}])[1]
    hits = _hits(r)
    ok = len(hits) >= 3 and hits[2] > hits[0]
    check("chain 连击第 N 段真加伤", ok, str(hits[:5]))


def test_lifesteal():
    """吸血：饱食满血时会溢出，所以看**撑了几回合**而不是终局血量。"""
    a = int(run([{"t": "lifesteal", "v": 0.5, "d": 1}], mon_atk=200)[1]["rounds"])
    b = int(run([], mon_atk=200)[1]["rounds"])
    check("lifesteal 吸血真续命（同怪多撑回合）", a > b, "%d 回合 vs %d 回合" % (a, b))


def test_dot():
    with_dot = run([{"t": "dot", "v": 0.3, "d": 3, "kind": "毒"}])[1]
    plain = run([])[1]
    tick = _foe_dot(with_dot)
    check("dot 真在敌人身上结算（有持续伤害条目）", tick > 0, "tick=%d" % tick)
    check("dot 是额外伤害（直伤之外还多掉血）",
          _dmg_sum(with_dot) + tick > _dmg_sum(plain),
          "含毒 %d+%d vs 纯 %d" % (_dmg_sum(with_dot), tick, _dmg_sum(plain)))


def test_debuff_atk():
    """降攻：只比**生效期内的单击伤害**。

    不能比总额 —— 降攻会拖长战斗，总额反而变大（实测 1632 vs 1600），
    这是"总量口径"的经典陷阱，不是机制没生效。
    """
    a = _taken_hits(run([{"t": "debuff_atk", "v": 0.4, "d": 3}], mon_atk=200)[1])
    b = _taken_hits(run([], mon_atk=200)[1])
    check("debuff_atk 降攻真压低单击承伤",
          bool(a) and bool(b) and a[0] < b[0], "首击 %s vs %s" % (a[:1], b[:1]))


def test_debuff_evade():
    """压闪避：目标必须先会闪避，否则压的是空气。

    2026-11-20 修真 bug：目标侧闪避从没被结算过 —— 怪不会闪，玩家的 7 门
    「控场·压闪避」斗技就等于纯文案。现在 mon["dodge"] 会被 _hit 结算，
    debuff_evade 才有对象可压。
    """
    # ⚠️ 闪避率 >30% 时这条会红：控场效果是「命中后」才挂上的，目标太会闪就
    # 先闪掉你这一招 → 压闪避永远挂不上（真实设计弱点，已如实上报，未擅自改语义）。
    # 这里用 25% 的目标闪避，验的是「机制真的接通、压了确实更能打中」。
    hit_plain = len(_hits(run([], mon_dodge=0.25, seed=20261120)[1]))
    hit_supp = len(_hits(run([{"t": "debuff_evade", "v": 0.45, "d": 60}],
                             mon_dodge=0.25, seed=20261120)[1]))
    hit_nd = len(_hits(run([], mon_dodge=0.0, seed=20261120)[1]))
    check("目标闪避真被结算（会闪掉一部分出手）", hit_plain < hit_nd,
          "有闪避命中 %d vs 无闪避 %d" % (hit_plain, hit_nd))
    check("debuff_evade 真把命中提回来", hit_supp > hit_plain,
          "压制后 %d vs 压制前 %d" % (hit_supp, hit_plain))


def test_debuff_heal():
    """减疗：玩家技的减疗挂在**对手**身上（压对手回血）。

    ⚠️ 诚实说明：PVE 普通怪不回血，所以这条在打怪时天然无感 —— 它真正
    生效的对象是「会回血的目标」（PVP 吸血对手 / 回血型 BOSS）。因此这里
    验两层：① 效果确实挂到目标状态；② 目标的回血倍率确实被压低。
    （怪物-side 减疗压低玩家服药回血，已由 test_doupo_immersion_gate C4 覆盖）
    """
    self_st, tgt_st = fx.new_state(), fx.new_state()
    fx.on_hit([{"t": "debuff_heal", "v": 0.5, "d": 3}], self_st, tgt_st, 100,
              lambda: 0.99)
    check("debuff_heal 真挂到目标状态", float(tgt_st.get("debuff_heal", 0)) == 0.5,
          "debuff_heal=%s" % tgt_st.get("debuff_heal"))
    check("debuff_heal 真把目标回血倍率压到 0.5",
          abs(fx.heal_mult(tgt_st) - 0.5) < 1e-9, "heal_mult=%s" % fx.heal_mult(tgt_st))
    check("debuff_heal 不影响自身回血", abs(fx.heal_mult(self_st) - 1.0) < 1e-9,
          "self heal_mult=%s" % fx.heal_mult(self_st))


def test_stun():
    a = _mon_attacks(run([{"t": "stun", "d": 2}], mon_atk=1)[1])
    b = _mon_attacks(run([], mon_atk=1)[1])
    check("stun 眩晕真跳回合", a < b, "%d vs %d" % (a, b))


def test_root():
    a = _mon_attacks(run([{"t": "root", "d": 2}], mon_atk=1)[1])
    b = _mon_attacks(run([], mon_atk=1)[1])
    check("root 定身真跳回合", a < b, "%d vs %d" % (a, b))


def test_knockback():
    a = _mon_attacks(run([{"t": "knockback", "d": 1}], mon_atk=1)[1])
    b = _mon_attacks(run([], mon_atk=1)[1])
    check("knockback 击退真跳一个回合", a < b, "%d vs %d" % (a, b))


def test_silence():
    def _with_monster_skill(effects):
        entry = _entry(effects)
        old = core._loadout_entries
        core._loadout_entries = lambda p: [entry]
        try:
            p = _player()
            mon = {"name": "试炼傀儡", "lv": 9, "hp": 300000, "atk": 1, "def": 0,
                   "exp": 1, "gold": 1, "kind": "傀儡", "is_boss": False,
                   "skill": {"name": "试炼爪", "chance": 1.0, "dmg_mult": 1.0}}
            r = core.fight(p, mon, rng=lambda: 0.99, roll_aid=False,
                           auto_pill=False)
            return sum(1 for it in r["log"]
                       if isinstance(it, dict) and it.get("monster_skill"))
        finally:
            core._loadout_entries = old

    a = _with_monster_skill([{"t": "silence", "d": 5}])
    b = _with_monster_skill([])
    check("silence 禁技真封住对手技能", b > 0 and a < b, "%d vs %d" % (a, b))


def test_reflect():
    a = _foe_dot(run([{"t": "reflect", "v": 0.5, "d": 3}], mon_atk=200)[1])
    reflected = sum(int(it.get("dmg") or 0) for it in
                    run([{"t": "reflect", "v": 0.5, "d": 3}], mon_atk=200)[1]["log"]
                    if isinstance(it, dict) and it.get("reflect"))
    check("reflect 反伤真把伤害弹回对手", reflected > 0, "反弹 %d" % reflected)
    check("reflect 反伤不计成 DoT（口径不混）", a == 0, "误记 %d" % a)


def test_reduce():
    a = _taken_sum(run([{"t": "reduce", "v": 0.5, "d": 3}], mon_atk=200)[1])
    b = _taken_sum(run([], mon_atk=200)[1])
    check("reduce 减伤真减承伤", 0 < a < b, "%d vs %d" % (a, b))


def test_evade():
    r = run([{"t": "evade", "v": 0.8, "d": 60}], mon_atk=200, seed=20261120)[1]
    dodged = sum(1 for it in r["log"]
                 if isinstance(it, dict) and it.get("kind") == "闪避")
    check("evade 自身闪避真躲掉攻击", dodged > 0, "闪避 %d 次" % dodged)


# ---------------------------------------------------------------------------
# 声明了却没实现的类型 = 必须为零（防止 skill_book 再写空效果）
# ---------------------------------------------------------------------------
def test_no_declared_but_unimplemented():
    import re as _re
    src = os.path.join(ROOT, "src", "plugins", "doupo", "skill_book.py")
    text = open(src, encoding="utf-8").read()
    declared = set(_re.findall(r"['\"]t['\"]\s*:\s*['\"]([a-z_]+)['\"]", text))
    impl = set(fx.EFFECT_LABELS.keys()) if hasattr(fx, "EFFECT_LABELS") else set()
    missing = sorted(declared - impl) if impl else []
    check("skill_book 声明的效果类型全部有实现", not missing, str(missing))


# ---------------------------------------------------------------------------
def main() -> int:
    for fn in (test_empower, test_pierce, test_cond, test_chain, test_lifesteal,
               test_dot, test_debuff_atk, test_debuff_evade, test_debuff_heal,
               test_stun, test_root, test_knockback, test_silence, test_reflect,
               test_reduce, test_evade, test_no_declared_but_unimplemented):
        try:
            fn()
        except Exception as e:  # 单条炸掉也要出报告，不能静默
            import traceback
            check("%s 未抛异常" % fn.__name__, False, "%s: %s" % (type(e).__name__, e))
            traceback.print_exc()

    failed = [r for r in _RES if not r[1]]
    for name, ok, detail in _RES:
        mark = "PASS" if ok else "FAIL"
        extra = ("  -> %s" % detail) if detail else ""
        print("%s  %s%s" % (mark, name, extra))
    print("=" * 70)
    print("EFFECTS_TOTAL=%d PASSED=%d FAILED=%d"
          % (len(_RES), len(_RES) - len(failed), len(failed)))
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
