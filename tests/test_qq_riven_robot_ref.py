# -*- coding: utf-8 -*-
"""模块R. Warframe Robot 四卡对照 —— 参考点固化 + 命中/失配分桶守门。

## 为什么有这套守门

2026-11-13 第30轮，我曾在记录里写下「冰凇市场 34.63 vs Robot 60.1 是错记」，
理由是「60.1 其实是阿利乌弹药效率区间下界」。**那条是错的**：复核 Robot 原图切块
（`tmp/robot_stats.png` + `tmp/robot_scores.png`）确认参考卡就是**冰凇 `Plaga-VisiTox`**，
卡面明写 **伤害分 8.1(F) / 市场分 60.1(A) / 综合 60.1(A)**。`60.1` 是市场分本体，
与弹药效率下界 `60.1` 纯属数值巧合。

教训：口头/记忆里的数字必须以原图为准。故把 4 张有原图依据的参考点写成**结构化数据**，
任何后续改动都必须面对这张表，而不是面对"我记得好像是……"。

## 四卡对照（原图依据）

| 卡 | Robot 伤害 | Robot 市场 | Robot 综合 | 我方 `dmg` | 我方 `mkt` | `ceil_src` |
|---|---|---|---|---|---|---|
| 阿利乌 Akarius | 62.5 (A) | 85.5 (S+) | 85.5 (S+) | 62.50 | 85.65 | calib |
| Coda Motovore | 11.0 | 62.1 | 62.1 | 11.03 | 62.31 | calib |
| Ocucor | 62.2 (A) | 87.0 (S+) | 87.0 (S+) | 90.75 | 59.17 | cur_max |
| 冰凇 Verglas | 8.1 (F) | 60.1 (A) | 60.1 (A) | 82.11 | 34.63 | cur_max |

**`calib` 分支 2/2 逐位命中；`cur_max` 分支 2/2 大幅失配。** 这是结构性结论，不是巧合：
兜底 `gain_max = max(calib_ceiling, g_cur_max, g_cur)` 走到 `cur_max` 时 `dmg ≡ roll`，
词条类型与武器强弱被抹掉，冰凇因此从 F 变 S。

## 判据

  R1 两张 `calib` 分支卡：伤害分/市场分与 Robot 逐位一致（±0.5）→ 回归保护
  R2 综合分 = max(伤害, 市场)：4/4 Robot 卡与我方同时成立（**唯一被 4 点证实的口径**）
  R3 两张 `cur_max` 分支卡：current 值 characterization + 打印 delta，标 KNOWN-GAP
  R4 `ceil_src` 与命中率的相关性（calib 2/2 中、cur_max 2/2 失配）不得无意打破
  R5 全量退化现状登记：r=0.5 时 `ceil_src=cur_max` 的比例（修复后必须主动更新本文件）
"""
from __future__ import annotations

import importlib.util
import json
import sys
import types
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_CARD = _REPO / "src" / "plugins" / "qq_bridge" / "game_tools" / "riven_card.py"
_DISP = _REPO / "data" / "warframe_items" / "riven_disp.json"

_P = _F = 0


def ok(cond, name, detail=""):
    global _P, _F
    if cond:
        _P += 1
        print("PASS %-58s %s" % (name, detail))
    else:
        _F += 1
        print("FAIL %-58s %s" % (name, detail))


def _stub():
    for name in list(sys.modules):
        if name == "gt" or name.startswith("gt.") or name in ("nonebot", "nonebot.log", "httpx"):
            del sys.modules[name]

    class _L:
        def info(self, *a, **k): pass
        def warning(self, *a, **k): pass
        def debug(self, *a, **k): pass
        def error(self, *a, **k): pass
        def exception(self, *a, **k): pass

    nb = types.ModuleType("nonebot")
    nbl = types.ModuleType("nonebot.log")
    nbl.logger = _L()
    nb.log = nbl
    sys.modules["nonebot"] = nb
    sys.modules["nonebot.log"] = nbl
    httpx = types.ModuleType("httpx")
    for n in ("HTTPError", "HTTPStatusError", "RequestError", "TimeoutException"):
        setattr(httpx, n, type(n, (Exception,), {}))
    httpx.AsyncClient = object
    sys.modules["httpx"] = httpx


def _load_card():
    _stub()
    GT = _CARD.parent
    pkg = types.ModuleType("gt")
    pkg.__path__ = [str(GT)]
    sys.modules["gt"] = pkg

    def load(n):
        s = importlib.util.spec_from_file_location("gt." + n, GT / (n + ".py"))
        m = importlib.util.module_from_spec(s)
        sys.modules["gt." + n] = m
        s.loader.exec_module(m)
        return m

    load("warframe")
    return load("riven_card")


def _card(rc, weapon, rtype, zh, disp, stats):
    p = {"weapon": weapon, "weapon_zh": zh, "type": rtype, "disp": disp, "rank": 8,
         "stats": [{"en": e, "zh": z, "sign": sg, "unit": "%", "value": v}
                   for e, z, sg, v in stats]}
    fix = rc._fix_of(p)
    for s in p["stats"]:
        s["_rng"] = rc._range_of(s, p, fix)
    return p


#: 四张 Robot 参考卡。`ceil` 为**期望**分支（当前实测），`kind` 决定断言强度：
#:   hit  = 与 Robot 逐位一致（回归保护）
#:   gap  = 已知失配（characterization，打印 delta）
REF = [
    dict(name="阿利乌 Akarius", kind="hit", weapon="Akarius", type="Pistol", zh="阿利乌双枪",
         disp=1.20, ceil="calib",
         stats=[("Ammo Efficiency", "弹药效率", "+", 68.1),
                ("Damage", "基础伤害", "+", 355.3),
                ("Weapon Recoil", "武器后坐力", "+", 58.1)],
         robot_dmg=62.5, robot_mkt=85.5, robot_total=85.5, robot_letter="A"),
    dict(name="Coda Motovore", kind="hit", weapon="Coda Motovore", type="Melee", zh="终幕·噬轮",
         disp=1.20, ceil="calib",
         stats=[("Critical Chance", "暴击几率", "+", 222.0),
                ("Impact Damage", "冲击伤害", "+", 146.4),
                ("Heavy Attack Efficiency", "重击效率", "+", 90.4),
                ("Critical Chance for Slide Attack", "滑砍暴击几率", "-", 109.3)],
         robot_dmg=11.0, robot_mkt=62.1, robot_total=62.1, robot_letter=None),
    dict(name="Ocucor", kind="gap", weapon="Ocucor", type="Pistol", zh="视使之触",
         disp=1.20, ceil="cur_max",
         stats=[("Heat Damage", "火焰伤害", "+", 110.7),
                ("Viral Damage", "病毒伤害", "+", 104.2),
                ("Multishot", "多重射击", "+", 132.7),
                ("Slash Damage", "切割伤害", "-", 111.4)],
         robot_dmg=62.2, robot_mkt=87.0, robot_total=87.0, robot_letter="A",
         cur_dmg=90.36, cur_mkt=59.17),
    dict(name="冰凇 Verglas", kind="gap", weapon="Verglas", type="Rifle", zh="冰凇",
         disp=1.20, ceil="cur_max",
         stats=[("Toxin Damage", "毒素伤害", "+", 91.5),
                ("Damage", "基础伤害", "+", 179.8),
                ("Status Damage", "异常状态伤害", "+", 111.3),
                ("Critical Chance", "暴击几率", "-", 135.3)],
         robot_dmg=8.1, robot_mkt=60.1, robot_total=60.1, robot_letter="F",
         cur_dmg=82.11, cur_mkt=34.63),
]


def _run_all(rc):
    out = []
    for r in REF:
        p = _card(rc, r["weapon"], r["type"], r["zh"], r["disp"], r["stats"])
        w = rc._weapon_raw(r["weapon"]) or {}
        sim = rc._sim(p, w)
        sc = rc._scores(p, sim)
        out.append((r, sc, sim))
    return out


def section_r1_hits(rows):
    print("== R1. calib 分支两卡：与 Robot 逐位一致（回归保护） ==")
    for r, sc, sim in rows:
        if r["kind"] != "hit":
            continue
        ok(abs(sc["dmg"] - r["robot_dmg"]) <= 0.5,
           "R1 %s 伤害分 %.1f ↔ Robot %.1f" % (r["name"], r["robot_dmg"], r["robot_dmg"]),
           "ours=%.2f Δ%+.2f" % (sc["dmg"], sc["dmg"] - r["robot_dmg"]))
        ok(abs(sc["mkt"] - r["robot_mkt"]) <= 0.5,
           "R1 %s 市场分 %.1f ↔ Robot %.1f" % (r["name"], r["robot_mkt"], r["robot_mkt"]),
           "ours=%.2f Δ%+.2f" % (sc["mkt"], sc["mkt"] - r["robot_mkt"]))
        ok(sc["ceil_src"] == r["ceil"],
           "R1 %s ceil_src=%s（分支决定口径）" % (r["name"], r["ceil"]),
           "got=%s" % sc["ceil_src"])
        if r["robot_letter"]:
            ok(rc._letter(sc["dmg"]) == r["robot_letter"],
               "R1 %s 伤害分字母 %s ↔ Robot %s" % (r["name"], r["robot_letter"],
                                                 r["robot_letter"]),
               "got=%s" % rc._letter(sc["dmg"]))


def section_r2_total(rows, rc):
    print("\n== R2. 综合分 = max(伤害, 市场) —— 唯一被 4/4 Robot 卡证实的口径 ==")
    bad_robot = [r["name"] for r in REF
                 if abs(r["robot_total"] - max(r["robot_dmg"], r["robot_mkt"])) > 0.05]
    ok(not bad_robot, "R2a Robot 四卡自身 综合 = max(伤害, 市场)", "4/4 成立"
       if not bad_robot else "破: %s" % bad_robot)
    bad_ours = [r["name"] for r, sc, _ in rows if abs(sc["total"] - max(sc["dmg"], sc["mkt"])) > 1e-9]
    ok(not bad_ours, "R2b 我方四卡同样满足 total = max(dmg, mkt)", "4/4 成立"
       if not bad_ours else "破: %s" % bad_ours)


def section_r3_gaps(rows):
    print("\n== R3. cur_max 分支两卡：已知失配（characterization，任何人改动都会在此显形） ==")
    for r, sc, sim in rows:
        if r["kind"] != "gap":
            continue
        ok(sc["ceil_src"] == r["ceil"],
           "R3 %s ceil_src=%s（失配分支）" % (r["name"], r["ceil"]),
           "got=%s" % sc["ceil_src"])
        ok(abs(sc["dmg"] - r["cur_dmg"]) <= 0.6,
           "R3 %s 伤害分登记值 %.2f" % (r["name"], r["cur_dmg"]),
           "ours=%.2f Δ=%+.2f vs Robot %+.1f" % (sc["dmg"], sc["dmg"] - r["cur_dmg"],
                                                sc["dmg"] - r["robot_dmg"]))
        ok(abs(sc["mkt"] - r["cur_mkt"]) <= 0.6,
           "R3 %s 市场分登记值 %.2f" % (r["name"], r["cur_mkt"]),
           "ours=%.2f Δ=%+.2f vs Robot %+.1f" % (sc["mkt"], sc["mkt"] - r["cur_mkt"],
                                                sc["mkt"] - r["robot_mkt"]))
        ok(sc["dmg"] > r["robot_dmg"],
           "R3 %s 已知偏差方向：我方伤害分**偏高**" % r["name"],
           "ours=%.2f > Robot=%.1f" % (sc["dmg"], r["robot_dmg"]))


def section_r4_pattern(rows):
    print("\n== R4. ceil_src ↔ 命中率相关性（calib 2/2 中、cur_max 2/2 失配） ==")
    calib = [(r, sc) for r, sc, _ in rows if sc["ceil_src"] == "calib"]
    curm = [(r, sc) for r, sc, _ in rows if sc["ceil_src"] == "cur_max"]
    hit = lambda r, sc: (abs(sc["dmg"] - r["robot_dmg"]) <= 0.5
                         and abs(sc["mkt"] - r["robot_mkt"]) <= 0.5)
    n_cal_hit = sum(1 for r, sc in calib if hit(r, sc))
    n_cur_hit = sum(1 for r, sc in curm if hit(r, sc))
    ok(n_cal_hit == len(calib), "R4a calib 分支全命中",
       "%d/%d" % (n_cal_hit, len(calib)))
    ok(n_cur_hit == 0, "R4b cur_max 分支全失配（不是巧合，是兜底换分母导致）",
       "%d/%d" % (n_cur_hit, len(curm)))


def section_r5_degeneracy(rc):
    print("\n== R5. 全量退化现状登记（修复后必须主动改本文件） ==")
    disp = json.load(open(_DISP, encoding="utf-8"))
    TPL = [("Damage", "基础伤害"), ("Multishot", "多重射击"), ("Critical Damage", "暴击伤害")]
    n = cur_m = 0
    dmgs = []
    for en, v in disp.items():
        if not isinstance(v, dict):
            continue
        w = rc._weapon_raw(en)
        if not w:
            continue
        p = {"weapon": en, "weapon_zh": v.get("zh") or en, "type": v.get("type"),
             "disp": v.get("disp") or 1.0, "rank": 8,
             "stats": [{"en": e, "zh": z, "sign": "+", "unit": "%", "value": 0.0}
                       for e, z in TPL]}
        fix = rc._fix_of(p)
        bad = False
        for s in p["stats"]:
            s["_rng"] = rc._range_of(s, p, fix)
            if not s["_rng"]:
                bad = True
                break
            a, b = s["_rng"]
            s["value"] = a + (b - a) * 0.5          # 中位 roll
        if bad:
            continue
        sc = rc._scores(p, rc._sim(p, w))
        if sc["dmg"] is None:
            continue
        n += 1
        dmgs.append(sc["dmg"])
        if sc["ceil_src"] == "cur_max":
            cur_m += 1
    share = cur_m / n if n else 0.0
    spread = (max(dmgs) - min(dmgs)) if dmgs else 0.0
    ok(n >= 300, "R5a 全量参检", "n=%d" % n)
    ok(share >= 0.95, "R5b 现状：中位 roll 卡几乎全走 cur_max 分支（dmg≡roll）",
       "%.1f%% (%d/%d)" % (share * 100, cur_m, n))
    ok(spread < 5.0, "R5c 现状：跨全部武器伤害分极差 <5 分（武器强弱被抹平）",
       "max-min=%.2f" % spread)
    print("     ↑ 这两条是**登记已知缺陷**，不是期望行为。修好刻度后必须同步更新本文件。")


if __name__ == "__main__":
    print("== 模块R. Robot 四卡对照 ==")
    rc = _load_card()
    rows = _run_all(rc)
    section_r1_hits(rows); print()
    section_r2_total(rows, rc); print()
    section_r3_gaps(rows); print()
    section_r4_pattern(rows); print()
    section_r5_degeneracy(rc)
    print("\n%d PASS / %d FAIL" % (_P, _F))
    sys.exit(1 if _F else 0)
