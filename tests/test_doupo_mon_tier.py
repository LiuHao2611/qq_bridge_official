# -*- coding: utf-8 -*-
"""怪物战斗档位（小怪 / 精英 / BOSS）唯一判定 + 四处消费点真的吃到系数。

真 bug（2026-11-20 第五轮）：塔/周本传给 ``monster_scaled`` 的 kind 是
``"塔·elite"`` / ``"周本·elite"``，而四处判定写的是 ``"精英" in kind`` ——
**不含那两个字**（它们只在怪名「精英·嗜血鼠」里）。于是精英层全程按小怪结算：

  1. ``monster_panel`` 的 ×1.20 拿到手是 ×1.00（``MON_PANEL_ELITE`` 成死常量）
  2. 怪失误率没减半（``MISHAP_BOSS_FACTOR`` 不生效）
  3. 怪技能概率没 ×1.5
  4. 硬仗必掉的气血+斗气丹没发（``_roll_drops`` 走成普通随机路径）

prove-it 反证（把任一条改回 ``"精英" in kind``）：
  * test_elite_skill_chance_is_boosted → 红
  * test_elite_mishap_is_halved → 红
  * test_elite_guaranteed_pills → 红
  * test_tower_elite_floor_gets_elite_panel → 红
  * test_no_scattered_elite_string_check → 红

运行：python tests/test_doupo_mon_tier.py
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

from doupo import config as cfg, core  # noqa: E402
from doupo import tower_data as td, tower_session as ts  # noqa: E402
from doupo import weekly_data as wd  # noqa: E402

os.environ["DOUPO_CHANCE_RATE"] = "0"


def _p(lv: int = 40) -> dict:
    p = core.create_player("Q_TIER", "测试", "群")
    p["lv"] = lv
    p["hp"] = core.stats(p)["体力"]
    p["mp"] = core.mp_max(p)
    return p


# ---------------------------------------------------------------------------
# 1. 判定表本身
# ---------------------------------------------------------------------------
def test_mon_tier_table():
    """三处来源的 kind 字面全部归到正确的三档。"""
    cases = {
        # 野外（势力名）
        "魔兽": "normal", "悍匪": "normal",
        "魂殿": "boss", "黑盟": "boss", "魔炎谷": "boss",
        # 爬塔
        "塔·normal": "normal", "塔·elite": "elite", "塔·boss": "boss",
        # 周本
        "周本·normal": "normal", "周本·elite": "elite", "周本·boss": "boss",
        # 兼容旧字面（手搓怪 / 旧日志）
        "精英": "elite", "精英·赤炎狼": "elite", "": "normal", None: "normal",
    }
    for kind, want in cases.items():
        got = core.mon_tier(kind)
        assert got == want, (kind, got, want)


def test_elite_tokens_cover_both_literals():
    """elite 关键字必须同时含英文 elite 与中文「精英」——只留一个就会复发。"""
    toks = tuple(cfg.MON_TIER_ELITE_TOKENS)
    assert "elite" in toks, toks
    assert "精英" in toks, toks


# ---------------------------------------------------------------------------
# 2. 面板三档乘数
# ---------------------------------------------------------------------------
def test_panel_elite_is_1_2x_normal():
    for lv in (9, 45, 69, 103):
        k = cfg.realm_index(lv)
        n = core.monster_panel(lv, "塔·normal")
        e = core.monster_panel(lv, "塔·elite")
        want_crit = round(min(cfg.MON_CRIT_CAP,
                              (cfg.MON_CRIT_BASE + cfg.MON_CRIT_PER_REALM * k)
                              * cfg.MON_PANEL_ELITE), 2)
        want_dodge = round(min(cfg.MON_DODGE_CAP,
                               (cfg.MON_DODGE_BASE + cfg.MON_DODGE_PER_REALM * k)
                               * cfg.MON_PANEL_ELITE), 2)
        assert (e["crit"], e["dodge"]) == (want_crit, want_dodge), (lv, e)
        assert e["crit"] > n["crit"], (lv, n, e)
        # 眼力不参与档位乘数（抬了会把必打关压成必输）
        assert e["hit"] == n["hit"], (lv, n, e)


def test_panel_boss_is_1_45x_normal():
    for lv in (9, 63, 103):
        k = cfg.realm_index(lv)
        n = core.monster_panel(lv, "周本·normal")
        b = core.monster_panel(lv, "周本·boss", True)
        want_crit = round(min(cfg.MON_CRIT_CAP,
                              (cfg.MON_CRIT_BASE + cfg.MON_CRIT_PER_REALM * k)
                              * cfg.MON_PANEL_BOSS), 2)
        want_dodge = round(min(cfg.MON_DODGE_CAP,
                               (cfg.MON_DODGE_BASE + cfg.MON_DODGE_PER_REALM * k)
                               * cfg.MON_PANEL_BOSS), 2)
        assert (b["crit"], b["dodge"]) == (want_crit, want_dodge), (lv, b)
        assert b["hit"] == n["hit"], (lv, n, b)


def test_wild_faction_intentionally_boss():
    """野外魂殿/黑盟/魔炎谷本来就是 BOSS 档（解锁 tier4 掉落），这条口径**不动**。

    改这条 = 改野外难度，属于设计变更，必须用户拍板。
    """
    for kind in ("魂殿", "黑盟", "魔炎谷"):
        p = _p(40)
        mon = core.monster_scaled(p, {"name": "反派", "lv": 40, "hard": 0.9,
                                      "kind": kind})
        assert mon["is_boss"] is True, kind
        assert mon["panel"] == core.monster_panel(40, kind, True), (kind, mon["panel"])


# ---------------------------------------------------------------------------
# 3. 真实路径：塔 / 周本的精英层
# ---------------------------------------------------------------------------
def test_tower_elite_floor_gets_elite_panel():
    """爬塔第 5 层（elite）面板必须是 ×1.20，不是小怪值。"""
    p = _p(40)
    st = ts.new_state("t5", seed="t5:tower")
    st["floor"] = 4                      # 下一步就是第 5 层
    r = core.tower_floor(p, st, random.Random("t5:rng").random)
    assert r["floor"] == 5 and r["kind"] == "elite", r
    ref = td.floor_ref_lv(5)
    want = core.monster_panel(ref, "塔·elite")
    plain = core.monster_panel(ref, "塔·normal")
    got = r["mon"]["panel"]
    assert got == want, (got, want)
    assert got["crit"] > plain["crit"], (got, plain)


def test_tower_normal_floor_stays_normal():
    """第 4 层是普通层，不能被精英系数连坐。"""
    p = _p(40)
    st = ts.new_state("t4", seed="t4:tower")
    st["floor"] = 3
    r = core.tower_floor(p, st, random.Random("t4:rng").random)
    ref = td.floor_ref_lv(4)
    assert r["mon"]["panel"] == core.monster_panel(ref, "塔·normal"), r["mon"]["panel"]


def test_tower_boss_floor_stays_boss():
    """第 10 层 BOSS 仍是 ×1.45。"""
    p = _p(40)
    st = ts.new_state("t10", seed="t10:tower")
    st["floor"] = 9
    r = core.tower_floor(p, st, random.Random("t10:rng").random)
    assert r["kind"] == "boss", r
    ref = td.floor_ref_lv(10)
    assert r["mon"]["panel"] == core.monster_panel(ref, "塔·boss", True)


def test_weekly_elite_floor_gets_elite_panel():
    """周本第 5 层（elite / 塔卫统领）面板必须 ×1.20。"""
    p = _p(60)
    snap = core.weekly_snapshot(p, "2026-W47")
    st = {"floor": 4}
    r = core.weekly_floor(p, snap, st, random.Random("w5:rng").random)
    assert r["floor"] == 5 and r["kind"] == "elite", r
    assert wd.floor_kind(5) == "elite"
    lv = wd.monster_lv(5, int(snap.get("lv", 60)))
    assert r["mon"]["panel"] == core.monster_panel(lv, "周本·elite"), r["mon"]["panel"]


# ---------------------------------------------------------------------------
# 4. 战斗内两条吃档位的概率（统计）
# ---------------------------------------------------------------------------
def _run(mon_kind: str, n: int = 120) -> tuple[int, int, int]:
    """跑 n 场长战，返回 (技能次数, 失误次数, 总回合)。"""
    skill = mishap = rounds = 0
    for i in range(n):
        p = _p(40)
        mon = {"name": "靶子", "lv": 40, "hp": 400000, "atk": 1, "def": 0,
               "exp": 1, "gold": 1,
               "kind": mon_kind, "panel": {"hit": 0.0, "crit": 0.0, "dodge": 0.0},
               "skill": {"name": "试炼爪", "chance": float(cfg.MONSTER_SKILL_BASE_CHANCE),
                         "effect": "burst", "mult": 1.0}}
        r = core.fight(p, mon, rng=random.Random("s%d" % i).random,
                       auto_pill=False, roll_aid=False)
        rounds += r["rounds"]
        for it in r["log"]:
            if it.get("monster_skill"):
                skill += 1
            if it.get("mishap"):
                mishap += 1
    return skill, mishap, rounds


def test_elite_skill_chance_is_boosted():
    """精英「塔·elite」的技能概率 ×1.5；普通层不涨。"""
    se, _, re_ = _run("塔·elite")
    sn, _, rn = _run("塔·normal")
    rate_e, rate_n = se / float(re_), sn / float(rn)
    assert rate_e > rate_n * 1.25, ("精英", rate_e, "普通", rate_n)


def test_elite_mishap_is_halved():
    """精英失误率减半；普通层不减。"""
    _, me, re_ = _run("塔·elite")
    _, mn, rn = _run("塔·normal")
    rate_e, rate_n = me / float(re_), mn / float(rn)
    assert rate_e < rate_n * 0.75, ("精英", rate_e, "普通", rate_n)


# ---------------------------------------------------------------------------
# 5. 保底掉落
# ---------------------------------------------------------------------------
def test_elite_guaranteed_pills():
    """塔/周本精英层必掉气血+斗气各一（旧版走成普通随机路径，一件都不给）。"""
    for kind in ("塔·elite", "周本·elite"):
        p = _p(40)
        drops = core._roll_drops(p, lambda: 0.99,
                                {"name": "塔卫统领", "lv": 62, "hard": 0.9,
                                 "is_boss": False, "kind": kind})
        got = {n for n, _ in drops
               if n in (cfg.HEAL_PILL_HP, cfg.HEAL_PILL_MP)}
        assert got == {cfg.HEAL_PILL_HP, cfg.HEAL_PILL_MP}, (kind, drops)


# ---------------------------------------------------------------------------
# 6. 防回归：别再散写字符串包含
# ---------------------------------------------------------------------------
def test_no_scattered_elite_string_check():
    """core.py 里禁止再出现 `"精英" in kind` 这种散写判定（必须走 mon_tier）。

    用 AST 扫 ``in`` 比较，不误伤注释与文档串。
    """
    import ast
    path = os.path.join(ROOT, "src", "plugins", "doupo", "core.py")
    tree = ast.parse(open(path, encoding="utf-8").read())
    bad = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare):
            continue
        if not any(isinstance(op, ast.In) for op in node.ops):
            continue
        for side in [node.left] + list(node.comparators):
            if (isinstance(side, ast.Constant) and isinstance(side.value, str)
                    and "精英" in side.value):
                bad.append((getattr(node, "lineno", 0), side.value))
    assert not bad, "core.py 又散写了精英判定（应走 mon_tier）：%s" % bad


# ---------------------------------------------------------------------------
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
            print("ERR ", n, "->", type(e).__name__, str(e)[:300])
    print("\n%d/%d 通过，失败 %d" % (len(fns) - failed, len(fns), failed))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(_main())
