# -*- coding: utf-8 -*-
"""命中 · 与闪避对冲的双向词条门禁（2026-11-20）。

用户口径（两句原话）：
  「我觉得应该有一个和闪避对应的词条，不然闪避太超标了」
  「无论小怪还是精英怪，还是 boos，都应该拥有和玩家一样的隐藏面板，
    这样战斗逻辑进行时才不会出现个别词条一边倒的情况」

所以要守的是**行为**，不是公式长得像：
  * 玩家闪避真的会被对手命中削掉（不是只写在卡面上）；
  * 玩家命中真的能把对手的闪避压掉（「被对手闪过」条数会掉到 0）；
  * 小怪/精英/BOSS 都按等级带面板，且强度单调、精英与 BOSS 更高；
  * 手搓怪（没有 panel）行为零回归 —— 这是既有 200+ 条逐招快照不被波及的前提；
  * 凝识丹真发命中，不是又一张「只加描述」的假丹。

prove-it 反向验证（tools/mutate_hit_rate.py）：
  把「减命中」改回「不减」→ test_player_dodge_pierced_by_monster_hit 必红；
  把 _hit 的 atk_hit 去掉 → test_player_hit_pierces_monster_dodge 必红；
  把 monster_panel 的等级项写死 → test_panel_grows_with_level 必红。

运行：python tests/test_doupo_hit_rate.py
"""
from __future__ import annotations

import importlib.util
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
        m.priority = 5
        m.block = True
        return m
    nb.on_regex = on_regex
    sys.modules["nonebot"] = nb
    for n in ("nonebot.adapters", "nonebot.adapters.onebot",
              "nonebot.adapters.onebot.v11"):
        sys.modules[n] = types.ModuleType(n)
    v = sys.modules["nonebot.adapters.onebot.v11"]
    v.MessageEvent = object
    v.MessageSegment = object
    v.Message = object


_stub_nonebot()
for _p in (ROOT, os.path.join(ROOT, "src", "plugins")):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


from doupo import config as cfg  # noqa: E402
from doupo import core  # noqa: E402

CORE_PY = os.path.join(ROOT, "src", "plugins", "doupo", "core.py")

_RES: list[tuple[str, bool, str]] = []


def check(name, ok, detail=""):
    _RES.append((name, bool(ok), detail))


def _p(lv=40, bonus=None, flames=None):
    p = core.create_player("hit-%d" % lv, "验招", "g")
    p["lv"] = lv
    p["hp"] = core.stats(p)["体力"]
    p["mp"] = core.mp_max(p)
    p["weapon"] = 0
    p["bag"] = {}
    if bonus:
        p["bonus"] = dict(bonus)
    if flames:
        p["flames"] = dict(flames)
    return p


# ---------------------------------------------------------------------------
# 1. 命中是对冲词条：等级涨、丹药能堆、有上限
# ---------------------------------------------------------------------------
def test_hit_grows_with_level_and_pills():
    lo, hi = _p(1), _p(102)
    h_lo, h_hi = core.hit_rate(lo), core.hit_rate(hi)
    check("高等级命中高于低等级", h_hi > h_lo, "lv1=%.1f lv102=%.1f" % (h_lo, h_hi))
    check("命中不会从敏捷白送（同等级换敏捷不变）",
          abs(core.hit_rate(_p(40, {"敏捷": 5000})) - core.hit_rate(_p(40))) < 1e-9,
          "堆 5000 敏捷后命中不该变")

    p = _p(40)
    before = core.hit_rate(p)
    p["bag"] = {"凝识丹": 1}
    r = core.use_item(p, "凝识丹")
    after = core.hit_rate(p)
    check("凝识丹真的发命中", bool(r.get("ok")) and after > before,
          "%s %.1f→%.1f" % (r.get("msg"), before, after))
    check("命中落到 bonus[\"命中\"]（唯一真值）",
          int((p.get("bonus") or {}).get("命中", 0)) > 0, str(p.get("bonus")))

    fat = _p(102, {"命中": 500})
    check("命中封顶（不许必中）", core.hit_rate(fat) <= cfg.HIT_CAP,
          "%.1f vs cap %.0f" % (core.hit_rate(fat), cfg.HIT_CAP))


# ---------------------------------------------------------------------------
# 2. 玩家防守侧：怪的命中把玩家闪避压掉（真结算，不是文案）
# ---------------------------------------------------------------------------
def _dodge_count(mon_hit=None, lv=63, seed=7, rounds=40):
    """打一场，数「玩家闪避掉怪反击」的条数。"""
    p = _p(lv)
    mon = {"name": "试招傀儡", "lv": lv, "hp": 10 ** 9, "atk": 1, "def": 0,
           "exp": 1, "gold": 1, "kind": "傀儡", "is_boss": False,
           "panel": {"hit": 0.0 if mon_hit is None else float(mon_hit),
                     "crit": 0.0, "dodge": 0.0}}
    old = cfg.HARD_ROUND_CAP
    cfg.HARD_ROUND_CAP = rounds
    try:
        res = core.fight(p, mon, rng=random.Random(seed).random, roll_aid=False,
                         auto_pill=False)
    finally:
        cfg.HARD_ROUND_CAP = old
    return sum(1 for it in res["log"]
               if isinstance(it, dict) and it.get("kind") == "闪避")


def test_player_dodge_pierced_by_monster_hit():
    # 同等级、同种子、同血量：唯一变量是「怪有没有命中」。
    plain = _dodge_count(mon_hit=0.0)
    pierced = _dodge_count(mon_hit=core.dodge_rate(_p(63)))
    check("玩家本来会闪（前提成立）", plain > 0, "闪避 %d 次" % plain)
    check("怪的命中把玩家闪避压到 0（真对冲）", pierced == 0,
          "怪命中=玩家闪避时仍闪了 %d 次" % pierced)


# ---------------------------------------------------------------------------
# 3. 玩家进攻侧：命中把怪的闪避压掉
# ---------------------------------------------------------------------------
def _mon_dodged(hit_bonus=0, mon_dodge=0.35, rounds=40):
    """打一场，数「被对手闪过」的玩家出手条数。"""
    p = _p(63, {"命中": hit_bonus})
    mon = {"name": "试招傀儡", "lv": 63, "hp": 10 ** 9, "atk": 1, "def": 0,
           "exp": 1, "gold": 1, "kind": "傀儡", "is_boss": False,
           "panel": {"hit": 0.0, "crit": 0.0, "dodge": mon_dodge * 100}}
    old = cfg.HARD_ROUND_CAP
    cfg.HARD_ROUND_CAP = rounds
    try:
        res = core.fight(p, mon, rng=random.Random(11).random, roll_aid=False,
                         auto_pill=False)
    finally:
        cfg.HARD_ROUND_CAP = old
    return sum(1 for it in res["log"]
               if isinstance(it, dict) and it.get("evaded"))


def test_player_hit_pierces_monster_dodge():
    raw = _mon_dodged(0)
    deep = _mon_dodged(200)
    check("怪本来会闪（前提成立）", raw > 0, "被闪过 %d 次" % raw)
    check("堆够命中后怪闪不掉（真对冲）", deep == 0, "仍被闪过 %d 次" % deep)


# ---------------------------------------------------------------------------
# 4. 怪物隐藏面板：按等级成长、精英/BOSS 更狠、眼力只由等级定
# ---------------------------------------------------------------------------
def test_panel_grows_with_level():
    seq = [core.monster_panel(lv) for lv in (1, 20, 45, 63, 81, 102)]
    for key in ("hit", "crit", "dodge"):
        vals = [s[key] for s in seq]
        check("面板 %s 随等级单调不减" % key,
              all(b >= a for a, b in zip(vals, vals[1:])), str(vals))
    check("高等级怪确实有命中（旧版恒 0）",
          seq[-1]["hit"] > 0 and seq[-1]["crit"] > 0, str(seq[-1]))


def test_elite_and_boss_panel_is_stronger():
    norm = core.monster_panel(60, "魔兽", False)
    elite = core.monster_panel(60, "精英魔兽", False)
    boss = core.monster_panel(60, "魂殿", True)
    check("精英暴击/身法高于普通", elite["crit"] > norm["crit"]
          and elite["dodge"] > norm["dodge"], "%s vs %s" % (elite, norm))
    check("BOSS 暴击/身法高于精英", boss["crit"] >= elite["crit"]
          and boss["dodge"] > elite["dodge"], "%s vs %s" % (boss, elite))
    check("眼力只由等级决定（精英/BOSS 不加）",
          abs(boss["hit"] - norm["hit"]) < 1e-9,
          "%s vs %s" % (boss["hit"], norm["hit"]))


def test_scaled_monster_carries_panel():
    p = _p(63)
    for spec in (cfg.MONSTERS[0], cfg.MONSTERS[-1]):
        mon = core.monster_scaled(p, spec)
        check("monster_scaled 带面板：%s" % spec["name"],
              isinstance(mon.get("panel"), dict)
              and mon["panel"]["hit"] >= 0
              and mon["panel"]["crit"] > 0,
              str(mon.get("panel")))


# ---------------------------------------------------------------------------
# 5. 手搓怪零回归：没有 panel 就没有面板行为
# ---------------------------------------------------------------------------
def test_handmade_monster_has_no_panel_behaviour():
    """测试/工具里手搓的怪（直接给 hp/atk）不该突然会闪、会暴击。

    这是既有 200+ 条逐招快照不被这次改动波及的前提：面板只在
    ``monster_scaled`` 挂上去的实例上生效。取满级怪等级 + 拖满回合，
    否则 12% 的身子闪/暴击在小样本里可能一次都不出现，条门禁就是假的。
    """
    p = _p(102)
    mon = {"name": "手搓", "lv": 102, "hp": 10 ** 9, "atk": 200, "def": 100,
           "exp": 1, "gold": 1, "kind": "傀儡", "is_boss": False}
    old = cfg.HARD_ROUND_CAP
    cfg.HARD_ROUND_CAP = 30
    try:
        res = core.fight(p, mon, rng=random.Random(3).random, roll_aid=False,
                         auto_pill=False)
    finally:
        cfg.HARD_ROUND_CAP = old
    crits = [it for it in res["log"]
             if isinstance(it, dict) and it.get("crit") and it.get("taken")]
    check("手搓怪不会凭空暴击", not crits, "暴击 %d 条" % len(crits))
    check("手搓怪不会凭空闪避玩家",
          not any(it.get("evaded") for it in res["log"] if isinstance(it, dict)),
          "")


# ---------------------------------------------------------------------------
# 5b. 技能给的**临时**身法同样吃命中（否则留一个白嫖洞）
# ---------------------------------------------------------------------------
def test_temporary_evade_also_pierced():
    from doupo import skill_effects as fx  # noqa: PLC0415

    def _hit(hit):
        st = fx.new_state()
        st["evade"], st["evade_d"] = 0.8, 3      # 卸力水镜一类的临时身法
        return fx.on_incoming(st, 1000, lambda: 0.5, hit=hit)

    plain = _hit(0.0)
    deep = _hit(1.0)          # 攻击方命中 100% → 临时身法被完全压掉
    check("临时身法本来能闪（前提成立）", plain["evaded"] is True, str(plain))
    check("命中压掉临时身法（真对冲）", deep["evaded"] is False
          and deep["final"] > 0, str(deep))


# ---------------------------------------------------------------------------
# 6. 面板不进 hp/atk 反解（红线：难度只由等级/战力决定）
# ---------------------------------------------------------------------------
def test_panel_does_not_touch_reverse_solve():
    """把面板整体调高，反解出来的 hp/atk 必须一字不变。"""
    p = _p(63)
    spec = cfg.MONSTERS[0]
    before = core.monster_scaled(p, spec)
    old = (cfg.MON_HIT_RESIDUAL, cfg.MON_CRIT_BASE, cfg.MON_DODGE_BASE)
    try:
        cfg.MON_HIT_RESIDUAL = 1.0
        cfg.MON_CRIT_BASE = 40.0
        cfg.MON_DODGE_BASE = 40.0
        after = core.monster_scaled(p, spec)
    finally:
        cfg.MON_HIT_RESIDUAL, cfg.MON_CRIT_BASE, cfg.MON_DODGE_BASE = old
    check("面板变化不改怪血（难度源头不动）", before["hp"] == after["hp"],
          "%d vs %d" % (before["hp"], after["hp"]))
    check("面板变化不改怪攻（难度源头不动）", before["atk"] == after["atk"],
          "%d vs %d" % (before["atk"], after["atk"]))
    check("面板确实变了（前提成立）", before["panel"] != after["panel"],
          "%s vs %s" % (before["panel"], after["panel"]))


# ---------------------------------------------------------------------------
def main() -> int:
    fns = (test_hit_grows_with_level_and_pills,
           test_player_dodge_pierced_by_monster_hit,
           test_player_hit_pierces_monster_dodge,
           test_panel_grows_with_level,
           test_elite_and_boss_panel_is_stronger,
           test_scaled_monster_carries_panel,
           test_handmade_monster_has_no_panel_behaviour,
           test_temporary_evade_also_pierced,
           test_panel_does_not_touch_reverse_solve)
    for fn in fns:
        try:
            fn()
        except Exception as e:  # 单条炸掉也要出报告，不能静默
            import traceback
            check("%s 未抛异常" % fn.__name__, False, "%s: %s" % (type(e).__name__, e))
            traceback.print_exc()

    failed = [r for r in _RES if not r[1]]
    for name, ok, detail in _RES:
        print("%s  %s%s" % ("PASS" if ok else "FAIL", name,
                            ("  -> %s" % detail) if detail else ""))
    print("=" * 70)
    print("HIT_TOTAL=%d PASSED=%d FAILED=%d"
          % (len(_RES), len(_RES) - len(failed), len(failed)))
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
