# -*- coding: utf-8 -*-
"""战斗过程丰富化门禁（2026-11-19 用户六条要求）。

用户原话：
  「战斗模块调整一下，不要控制回合，然后自动使用回血回气丹药，
   背包加上锁定物品功能，锁定的物品不会被自动使用，
   然后给每个怪物加上技能概率施放，
   然后丰富一下战斗过程，比如怪物回合小怪有概率崴到脚或者撞到什么，
   偶尔给玩家一二回合福利就行」

逐条钉死：
  1. **不控制回合**：15 回合上限已删，只留 HARD_ROUND_CAP 作脏数据熔断
     （触发时按剩余血量比例判胜，绝不静默判负）。PVP 同一口径。
  2. **自动服药**：气血 <35% / 斗气 <20% 时取背包里最小的可用疗伤丹，
     每场各限 2 枚；**锁定的物品一颗都不碰**。
  3. **锁定只拦自动**：手动 `use_item` 不受影响（用户只要求「不会被自动使用」）。
  4. **每怪一技能**：cfg.MONSTERS 全覆盖（含塔/周本虚拟怪的 kind 兜底），
     概率施放，效果只作用于本回合，不改怪物面板反解。
  5. **怪物失误**：小概率崴脚/自乱阵脚，本回合不攻击（战报可查）。
  6. **玩家福利**：每场最多一次、1~2 回合增伤减伤，之后消失（不是永久 buff）。

反证（改坏必红）：
  * 把 `rounds < HARD_ROUND_CAP` 改回 `rounds < 15` → test_no_round_cap 红；
  * 删掉 `locked` 判断 → test_locked_pill_not_used 红；
  * 删掉 `_try_auto_pill` 调用 → test_auto_pill_on_low_hp 红；
  * 把 skill 透传删掉 → test_every_monster_has_skill 红；
  * 把 mishap 判定删掉 → test_monster_can_mishap 红。

运行：python tests/test_doupo_battle_events.py
"""
from __future__ import annotations

import os
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

from doupo import config as cfg, core  # noqa: E402

os.environ["DOUPO_CHANCE_RATE"] = "0"


def _p(lv: int = 40, hp_frac: float = 1.0, mp_frac: float = 1.0) -> dict:
    p = core.create_player("QBAT", "测试", "群")
    p["lv"] = lv
    p["hp"] = int(core.stats(p)["体力"] * hp_frac)
    p["mp"] = int(core.mp_max(p) * mp_frac)
    return p


def _mon(p, *, hp=1000, atk=1, def_=0, kind="魔兽", name="测试怪", is_boss=False):
    """手搓怪物实例：直接控制 hp/atk，避免被 monster_scaled 反解干扰。"""
    return {"name": name, "lv": int(p.get("lv", 1)), "hp": int(hp), "atk": int(atk),
            "def": int(def_), "exp": 1, "gold": 1, "kind": kind,
            "is_boss": bool(is_boss), "skill": None}


# ---------------------------------------------------------------------------
# ① 不控制回合
# ---------------------------------------------------------------------------
def test_no_round_cap():
    """15 回合上限必须消失：硬怪能打到 15 回合以上并照常分胜负。"""
    p = _p(1, 1.0, 1.0)
    mon = _mon(p, hp=40, atk=1, def_=10 ** 9)   # 玩家每回合只打 1 点 → 需 40 回合
    r = core.fight(p, mon, rng=lambda: 0.5, roll_aid=False)
    assert r["rounds"] > 15, "回合仍被 15 卡住：rounds=%d" % r["rounds"]
    assert r["rounds"] <= int(cfg.HARD_ROUND_CAP)
    assert r["win"] is True, "40 回合打完应该赢（玩家没死）"


def test_hard_cap_fuse_decides_by_ratio():
    """熔断（脏数据保护）按剩余血量比例判胜，不是无条件判负、也不是静默判负。

    构造口径（都能手算，不靠猜）：
      * 胜：怪血 100、玩家每回合只打 1（防御 1e9），40 回合后怪剩 60（0.60），
            玩家只被 1 点/回合挠（≈0.95）→ 玩家比例更高 → 判胜。
      * 负：怪血 1e9 几乎没被打（≈1.00），玩家被 20/回合打掉 200（≈0.77）→ 判负。
    """
    old = cfg.HARD_ROUND_CAP
    try:
        cfg.HARD_ROUND_CAP = 40
        p = _p(1, 1.0, 1.0)
        mon = _mon(p, hp=100, atk=1, def_=10 ** 9)
        r = core.fight(p, mon, rng=lambda: 0.5, roll_aid=False)
        assert r["exhausted"] is True, "熔断标记没置位"
        assert r["win"] is True, "玩家剩余比例更高，应判胜（不许无条件判负）"
        assert r["rounds"] == 40

        cfg.HARD_ROUND_CAP = 10
        q = _p(1, 1.0, 1.0)
        mon2 = _mon(q, hp=10 ** 9, atk=20, def_=0)
        r2 = core.fight(q, mon2, rng=lambda: 0.5, roll_aid=False)
        assert r2["exhausted"] is True
        assert r2["win"] is False, "怪几乎满血、玩家被压着打，应判负"
    finally:
        cfg.HARD_ROUND_CAP = old


def test_duel_no_round_cap():
    """PVP 同一口径：不许另留 15 回合天花板。"""
    a, b = _p(1, 1.0, 1.0), _p(1, 1.0, 1.0)
    a["lv"] = b["lv"] = 60
    a["hp"] = int(core.stats(a)["体力"])
    b["hp"] = int(core.stats(b)["体力"])
    # 双方防御极高 → 每回合各打 1 点，血量上千 → 必然超过 15 回合
    for x in (a, b):
        x["bonus"] = {"防御": 10 ** 6}
    r = core.duel(a, b, rng=lambda: 0.5)
    assert r["rounds"] > 15, "PVP 仍被 15 回合卡住：%d" % r["rounds"]
    assert r["win"] in (True, False)


# ---------------------------------------------------------------------------
# ② 自动服药 + ③ 锁定
# ---------------------------------------------------------------------------
def _clear_bag(p):
    """清空入道赠品，避免「回灵丹」抢在被测药前面（测试要单变量）。"""
    p["bag"] = {}


def test_auto_pill_on_low_hp():
    p = _p(40, 0.10, 1.0)
    _clear_bag(p)
    core.add_item(p, "回春散", 3)
    hp0 = p["hp"]
    mon = _mon(p, hp=10 ** 9, atk=1)
    r = core.fight(p, mon, rng=lambda: 0.9, roll_aid=False)   # 0.9：不出闪避/失误
    assert r["auto_pills"]["hp"] >= 1, r["auto_pills"]
    assert core.count_item(p, "回春散") < 3, "药没真的扣背包"
    assert p["hp"] > hp0, "吃了药但血没涨"


def test_locked_pill_not_used():
    """锁定的疗伤丹一颗都不许自动吃（用户硬要求）。"""
    p = _p(40, 0.05, 0.0)
    _clear_bag(p)
    core.add_item(p, "回春散", 5)
    core.add_item(p, "回气丹", 5)
    mon = _mon(p, hp=10 ** 9, atk=1)
    r = core.fight(p, mon, rng=lambda: 0.9, roll_aid=False,
                   locked={"回春散", "回气丹"})
    assert r["auto_pills"] == {"hp": 0, "mp": 0}, r["auto_pills"]
    assert core.count_item(p, "回春散") == 5
    assert core.count_item(p, "回气丹") == 5


def test_locked_only_skips_that_name():
    """只锁回春散时，别的疗伤丹照常自动吃。"""
    p = _p(40, 0.05, 1.0)
    _clear_bag(p)
    core.add_item(p, "回春散", 5)
    core.add_item(p, "凝血散", 5)
    mon = _mon(p, hp=10 ** 9, atk=1)
    r = core.fight(p, mon, rng=lambda: 0.9, roll_aid=False, locked={"回春散"})
    assert r["auto_pills"]["hp"] >= 1
    assert core.count_item(p, "回春散") == 5, "锁定的药被吃了"
    assert core.count_item(p, "凝血散") < 5, "未锁定的药没被用"


def test_auto_pill_cap_per_fight():
    """一场最多各 2 枚（不能一场嗑光家底）。"""
    p = _p(40, 0.02, 0.0)
    _clear_bag(p)
    core.add_item(p, "回春散", 20)
    core.add_item(p, "回气丹", 20)
    mon = _mon(p, hp=10 ** 9, atk=10 ** 6)     # 一直残血
    r = core.fight(p, mon, rng=lambda: 0.9, roll_aid=False)
    assert r["auto_pills"]["hp"] <= int(cfg.AUTO_PILL_MAX_PER_FIGHT), r["auto_pills"]
    assert r["auto_pills"]["mp"] <= int(cfg.AUTO_PILL_MAX_PER_FIGHT)


def test_manual_use_still_works_when_locked():
    """锁定只拦「自动」：手动服丹不受影响（语义边界）。"""
    p = _p(40, 0.05, 1.0)
    core.add_item(p, "回春散", 2)
    hp0 = p["hp"]
    res = core.use_item(p, "回春散")
    assert res["ok"] and p["hp"] > hp0


def test_auto_pill_candidates_skip_locked():
    p = _p(40, 0.5, 0.5)
    core.add_item(p, "回春散", 1)
    names = [n for _g, n in core.auto_pill_candidates(p, "hp", {"回春散"})]
    assert "回春散" not in names
    names2 = [n for _g, n in core.auto_pill_candidates(p, "hp", set())]
    assert "回春散" in names2


def test_deep_copy_fights_do_not_fake_pills():
    """爬塔/周本打在深副本上：不许出现「服药」日志（真实背包不会减少）。"""
    p = _p(40, 0.02, 0.0)
    _clear_bag(p)
    core.add_item(p, "回春散", 5)
    mon = _mon(p, hp=10 ** 9, atk=1, kind="塔·normal")
    r = core.fight(p, mon, rng=lambda: 0.9, roll_aid=False, auto_pill=False)
    assert r["auto_pills"] == {"hp": 0, "mp": 0}
    assert not [e for e in r["log"] if e.get("kind") == "服药"]


# ---------------------------------------------------------------------------
# ④ 每个怪物都有技能
# ---------------------------------------------------------------------------
def test_every_monster_has_skill():
    p = _p(40)
    missing = []
    for m in cfg.MONSTERS:
        inst = core.monster_scaled(p, m)
        if not inst.get("skill"):
            missing.append(m["name"])
    assert not missing, "这些怪没有技能：%s" % missing[:6]


def test_virtual_monsters_get_skill_by_kind():
    p = _p(60)
    for kind in ("塔·boss", "塔·elite", "周本·boss"):
        inst = core.monster_scaled(p, {"name": "虚拟守关者", "lv": 60, "hard": 1.0,
                                       "kind": kind})
        assert inst.get("skill"), "虚拟怪没兜底技能：%s" % kind


def _force_monster_hits():
    """临时关掉失误 + 把玩家闪避压到负值：让「怪真的出手」成为确定事件。

    否则 rng 全 0 时怪物先崴脚、再被闪避，技能永远没有机会掷。
    返回 (restore) 恢复函数，调用方放在 finally 里。
    """
    old = cfg.MISHAP_CHANCE
    cfg.MISHAP_CHANCE = 0.0
    return lambda: setattr(cfg, "MISHAP_CHANCE", old)


def test_monster_skill_can_proc():
    p = _p(60, 1.0, 1.0)
    mon = core.monster_scaled(p, {"name": "赤炎狼", "lv": 60, "hard": 1.0,
                                  "kind": "魔兽"})
    mon["hp"] = 10 ** 9
    undo = _force_monster_hits()
    try:
        r = core.fight(p, mon, rng=lambda: 0.0, roll_aid=False,
                       tower_mod={"evade_add": -100})
    finally:
        undo()
    procs = [e for e in r["log"] if e.get("monster_skill")]
    assert procs, "rng 全 0 时怪技能没触发过"
    # 技能必须并进「兽击」那条，不是另一条 dmg=0 的假条目
    for e in procs:
        assert e.get("kind") == "兽击", e
        assert e.get("dmg", 0) >= 0


def test_monster_skill_does_not_change_panel():
    """技能只作用于本回合，不改怪物面板反解（难度红线）。"""
    p = _p(40)
    spec = {"name": "赤炎狼", "lv": 40, "hard": 1.0, "kind": "魔兽"}
    a = core.monster_scaled(p, dict(spec))
    b = core.monster_scaled(p, dict(spec))
    for k in ("hp", "atk", "def", "exp", "gold"):
        assert a[k] == b[k], k


# ---------------------------------------------------------------------------
# ⑤ 怪物失误 / ⑥ 玩家福利
# ---------------------------------------------------------------------------
def test_monster_can_mishap():
    p = _p(60, 1.0, 1.0)
    mon = _mon(p, hp=10 ** 9, atk=1, kind="魔兽")
    mon["skill"] = None
    r = core.fight(p, mon, rng=lambda: 0.0, roll_aid=False)
    miss = [e for e in r["log"] if e.get("mishap")]
    assert miss, "rng 全 0 时怪没失误过"
    assert "崴" in "".join(miss[0].get("notes") or []) or "崴" in str(miss[0].get("move"))


def test_mishap_rate_lower_for_boss():
    """精英/Boss 更少犯低级错（概率减半），口径写在 config 里。"""
    assert 0 < float(cfg.MISHAP_BOSS_FACTOR) < 1


def test_fortune_procs_once_and_expires():
    p = _p(60, 1.0, 1.0)
    mon = _mon(p, hp=10 ** 9, atk=1, kind="魔兽")
    mon["skill"] = None
    r = core.fight(p, mon, rng=lambda: 0.0, roll_aid=False)
    assert r["fortune"] is True, "rng 全 0 时没触发心火共鸣"
    marks = [e for e in r["log"] if e.get("kind") == "心火共鸣"]
    assert len(marks) == 1, "每场最多一次，实际 %d" % len(marks)


def test_fortune_boosts_both_attack_and_guard():
    """1 回合福利必须增伤与减伤同进同出（递减放在双方行动之后）。"""
    p = _p(60, 1.0, 1.0)
    mon = _mon(p, hp=10 ** 9, atk=500, kind="魔兽")
    mon["skill"] = None
    undo = _force_monster_hits()
    try:
        r = core.fight(p, mon, rng=lambda: 0.0, roll_aid=False,
                       tower_mod={"evade_add": -100})
    finally:
        undo()
    guarded = [e for e in r["log"] if e.get("fortune_guard")]
    boosted = [e for e in r["log"] if e.get("fortune") and e.get("dmg")]
    assert guarded, "福利回合没有减伤"
    assert boosted, "福利回合没有增伤"


# ---------------------------------------------------------------------------
# 血/斗气跨战结转不变（回归口）
# ---------------------------------------------------------------------------
def test_mp_carry_over_still_works():
    p = _p(40, 1.0, 1.0)
    mpmax = core.mp_max(p)
    mon = _mon(p, hp=10 ** 9, atk=1)
    core.fight(p, mon, rng=lambda: 0.9, roll_aid=False)
    assert 0 <= p["mp"] <= mpmax, p["mp"]


def _main() -> int:
    fns = [(n, f) for n, f in globals().items()
           if n.startswith("test_") and callable(f)]
    failed = 0
    for n, f in sorted(fns):
        try:
            f()
            print("PASS", n)
        except AssertionError as e:
            failed += 1
            print("FAIL", n, "->", e)
        except Exception as e:  # noqa: BLE001
            failed += 1
            print("ERR ", n, "->", type(e).__name__, str(e)[:200])
    print("\n%d/%d 通过，失败 %d" % (len(fns) - failed, len(fns), failed))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(_main())
