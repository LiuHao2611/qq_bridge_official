# -*- coding: utf-8 -*-
"""12 档突破 BOSS 四技能门禁（2026-11-23）。

用户原话：`给BOOS搞四个技能，三个普通，一个大招，结合原著去做`。

逐条钉死：
  1. 12 档每档 4 技能：3 普通 + 1 大招，大招固定排第 4。
  2. 技能名的每个 `·` 词根都能在本地全本逐字命中。
  3. 总施放概率 <= 0.70；大招实际概率 <= 0.12；倍率/机制不越界。
  4. 四档概率分档严格递增，低档不照搬终局强度。
  5. make_breakthrough_boss() 生成 mon 时携带 skills。
  6. 固定 rng 下每回合最多一条 monster_skill。
  7. 大招第 1、2 回合不出现，第 3 回合起可出现。
  8. 大招命中后 4 回合内不再出现大招。
  9. 旧单技能路径仍生效；普通怪/精英不受 skills 影响。
  10. 禁技时四技能分支不掷技能骰。
  11. 技能名/notes 零元说明，skills 不写回玩家存档。

反证（改坏必红）：
  * 删一条技能、大招挪到第 1 条、cap 改 1.0、冷却改 0、技能名塞元说明、
    把 skills 写进 p，任一项都会让本文件报红。

运行：python tests/test_doupo_boss_four_skills.py（exit 0 = 全绿）
"""
from __future__ import annotations

import copy
import inspect
import os
import re
import sys
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NOVEL = os.environ.get(
    "DOUPO_NOVEL_PATH", "D:/AI/manju_agent/_novel_doupo_20261118/斗破苍穹_全本.txt")


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

_RES = []


def check(name, ok, detail=""):
    _RES.append((name, bool(ok), str(detail)))


def _p(lv=9):
    p = core.create_player("Q4S", "四技能", "群")
    p["lv"] = lv
    core._refill(p)
    return p


def _mon(p, character, *, hp=100000, atk=1, skills=None):
    mon = {"name": character["name"], "lv": int(p["lv"]),
           "hp": int(hp), "atk": int(atk), "def": 0, "exp": 1, "gold": 1,
           "kind": "魂殿", "is_boss": True,
           "skill": dict(character["skill"]), "character": copy.deepcopy(character)}
    if skills is not None:
        mon["skills"] = [dict(s) for s in skills]
    return mon


def _quiet_character(ch):
    """行为探针用：去掉台词/机制追加掷骰，让 rng 序列只覆盖判定主链。"""
    ch = copy.deepcopy(ch)
    ch["quotes"] = ()
    ch["mech"] = None
    for s in ch.get("skills") or []:
        s["mech"] = None
    return ch


def _probe_rng(skill_roll=0.0):
    """每回合固定 6 次采样：暴击否、失误否、闪避否、伤害浮云、技能命中、福利否。"""
    vals = [0.99, 0.99, 0.99, 0.50, skill_roll, 0.99]
    idx = [0]

    def _rng():
        v = vals[idx[0] % len(vals)]
        idx[0] += 1
        return v
    return _rng


def _totals():
    return [sum(float(s.get("chance") or 0) for s in b["skills"]) * 2.0
            for b in cfg.BREAK_BOSSES]


# ---------------------------------------------------------------------------
# ① 数据形状
# ---------------------------------------------------------------------------
def test_every_boss_has_four_skills():
    for b in cfg.BREAK_BOSSES:
        sk = b.get("skills")
        check("%s 有 4 技能" % b["name"],
              isinstance(sk, list) and len(sk) == 4, str(sk)[:80])
        flags = [bool(s.get("ultimate")) for s in sk or []]
        check("%s 前三普通一大招" % b["name"],
              flags == [False, False, False, True], str(flags))


def test_skill_roots_are_in_novel():
    if not os.path.isfile(NOVEL):
        check("原著语料存在（守门前置）", False, NOVEL)
        return
    with open(NOVEL, encoding="utf-8", errors="replace") as fh:
        novel = fh.read()
    missing = []
    for b in cfg.BREAK_BOSSES:
        for s in b.get("skills") or []:
            for root in str(s.get("name") or "").split("·"):
                root = root.strip()
                if root and root not in novel:
                    missing.append((b["name"], root))
    check("四技能名的每个词根逐字见于原著", not missing, str(missing)[:220])


def test_probability_and_effect_bounds():
    check("总概率 cap 常数为 0.70",
          abs(float(cfg.BOSS_SKILL_MAX_CHANCE) - 0.70) < 1e-9,
          str(cfg.BOSS_SKILL_MAX_CHANCE))
    bad_cap = [(b["name"], round(t, 4)) for b, t in zip(cfg.BREAK_BOSSES, _totals())
               if t > float(cfg.BOSS_SKILL_MAX_CHANCE) + 1e-9]
    check("总施放概率不越 0.70 cap", not bad_cap, str(bad_cap))
    bad_ult = [(b["name"], float(b["skills"][3]["chance"]) * 2)
               for b in cfg.BREAK_BOSSES
               if float(b["skills"][3]["chance"]) * 2 > 0.12 + 1e-9]
    check("大招实际单次概率 <= 0.12", not bad_ult, str(bad_ult))
    bad_mult, bad_mech = [], []
    for b in cfg.BREAK_BOSSES:
        for s in b["skills"]:
            mult = float(s.get("mult") or 0)
            if mult > (2.5 if s.get("ultimate") else 1.9) + 1e-9:
                bad_mult.append((b["name"], s["name"], mult))
            mech = s.get("mech")
            if mech and (float(mech.get("p") or 0) > 0.35 + 1e-9
                         or float(mech.get("v") or 0) > 1.5 + 1e-9):
                bad_mech.append((b["name"], s["name"], mech))
    check("技能倍率不越档位上限", not bad_mult, str(bad_mult))
    check("机制 p/v 不越既有效果上限", not bad_mech, str(bad_mech)[:180])


def test_probability_tiers_increase():
    totals = _totals()
    sample = [totals[0], totals[3], totals[8], totals[9]]
    check("四档总概率严格递增",
          all(a < b for a, b in zip(sample, sample[1:])),
          str([round(x, 3) for x in sample]))
    check("1-3 档不照搬终局强度", totals[0] < totals[-1],
          "%.3f < %.3f" % (totals[0], totals[-1]))


def test_skills_have_no_meta_words():
    banned = ("原著", "语料", "考据", "游戏原创", "未载", "不代编", "战力", "快照")
    hits = []
    for b in cfg.BREAK_BOSSES:
        for s in b["skills"]:
            for w in banned:
                if w in str(s.get("name") or "") or w in str(s.get("mech") or {}):
                    hits.append((b["name"], s["name"], w))
    check("玩家可见技能名/机制零元说明", not hits, str(hits)[:180])


# ---------------------------------------------------------------------------
# ② 真实路径接入
# ---------------------------------------------------------------------------
def test_make_breakthrough_boss_carries_skills():
    for lv in (9, 18, 27, 36, 45, 54, 63, 72, 81, 90, 93, 102):
        p = _p(lv)
        boss = core.make_breakthrough_boss(p)
        sk = boss.get("skills") or []
        check("lv%d 真实 BOSS 带 4 技能" % lv, len(sk) == 4, str(len(sk)))
    boss = core.make_breakthrough_boss(_p(9))
    check("skills 是 mon 实例副本，不是配置表本体",
          boss["skills"] is not cfg.BREAK_BOSSES[0]["skills"], "")


def test_one_skill_per_round():
    p = _p(9)
    p["mp"] = 0   # 玩家不出斗技 → 测量用 rng 序列只覆盖怪技能主链
    ch = _quiet_character(cfg.BREAK_BOSSES[0])
    boss = _mon(p, ch, skills=ch["skills"])
    r = core.fight(p, boss, rng=_probe_rng(), roll_aid=False, auto_pill=False)
    by_round = {}
    for e in r["log"]:
        if isinstance(e, dict) and e.get("monster_skill"):
            by_round[int(e.get("round") or 0)] = by_round.get(int(e.get("round") or 0), 0) + 1
    check("固定 rng 下出现过技能", bool(by_round), str(by_round)[:120])
    check("每回合最多一条 monster_skill", all(v == 1 for v in by_round.values()),
          str(by_round)[:120])
    check("tier 字段只写 normal/ultimate",
          all(e.get("monster_skill_tier") in ("normal", "ultimate")
              for e in r["log"] if isinstance(e, dict) and e.get("monster_skill")), "")


def _ult_only_mon(p):
    ch = _quiet_character(cfg.BREAK_BOSSES[0])
    normal = dict(ch["skills"][0])
    normal["chance"] = 0.0
    ult = dict(ch["skills"][3])
    ult["chance"] = 1.0
    ch["skills"] = [normal, ult]
    return _mon(p, ch, skills=ch["skills"])


def test_ultimate_round_gate_and_cooldown():
    p = _p(9)
    p["mp"] = 0
    r = core.fight(p, _ult_only_mon(p), rng=_probe_rng(),
                   roll_aid=False, auto_pill=False)
    ults = [int(e.get("round") or 0) for e in r["log"]
            if isinstance(e, dict) and e.get("monster_skill_tier") == "ultimate"]
    check("大招至少出现一次", bool(ults), str(ults)[:120])
    check("第 1、2 回合无大招", all(x >= 3 for x in ults), str(ults)[:120])
    check("大招首次出现在第 3 回合", ults and ults[0] == 3, str(ults)[:120])
    gaps = [b - a for a, b in zip(ults, ults[1:])]
    check("两次大招间隔 >= 4 回合", all(g >= 4 for g in gaps), str(gaps)[:120])


def test_legacy_single_skill_path_still_works():
    p = _p(9)
    p["mp"] = 0
    ch = _quiet_character(cfg.BREAK_BOSSES[0])
    ch["skill"] = dict(ch["skill"])
    ch["skill"]["chance"] = 1.0
    mon = _mon(p, ch)   # 不带 skills → 旧路径
    r = core.fight(p, mon, rng=_probe_rng(), roll_aid=False, auto_pill=False)
    hits = [e for e in r["log"] if isinstance(e, dict) and e.get("monster_skill")]
    check("旧单技能路径仍施放", bool(hits), str(len(hits)))
    check("旧路径 tier=normal",
          all(e.get("monster_skill_tier") == "normal" for e in hits), "")


def test_normal_monster_unaffected():
    p = _p(1)
    mon = core.monster_scaled(p, dict(cfg.MONSTERS[0]))
    check("普通怪不带 skills 字段", "skills" not in mon, str(mon.get("skills")))
    r = core.fight(p, mon, rng=random_zero(), roll_aid=False, auto_pill=False)
    check("普通怪仍能走既有技能结算", isinstance(r.get("log"), list), "")


def random_zero():
    return lambda: 0.0


def test_silence_gate_wraps_four_skills():
    src = inspect.getsource(core.fight)
    check("四技能判定在 silence 闸门内",
          'if int(m_fx.get("silence", 0)) <= 0:' in src and 'if _boss_skills:' in src, "")
    check("skills 不写回玩家 p", not re.search(r"p\[\s*['\"]skills['\"]\s*\]\s*=", src), "")
    check("大招冷却是本场局部变量", "_boss_ult_cd = 0" in src, "")


# ---------------------------------------------------------------------------
def main() -> int:
    for fn in (
        test_every_boss_has_four_skills,
        test_skill_roots_are_in_novel,
        test_probability_and_effect_bounds,
        test_probability_tiers_increase,
        test_skills_have_no_meta_words,
        test_make_breakthrough_boss_carries_skills,
        test_one_skill_per_round,
        test_ultimate_round_gate_and_cooldown,
        test_legacy_single_skill_path_still_works,
        test_normal_monster_unaffected,
        test_silence_gate_wraps_four_skills,
    ):
        try:
            fn()
        except Exception as exc:  # 守门脚本：单点异常也算失败
            check(fn.__name__ + " 执行异常", False, repr(exc))
    bad = [x for x in _RES if not x[1]]
    for name, ok, detail in _RES:
        print("%s %s%s" % ("PASS" if ok else "FAIL", name,
                           (" | " + detail) if detail and not ok else ""))
    print("---")
    print("%d PASSED / %d FAILED" % (len(_RES) - len(bad), len(bad)))
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
