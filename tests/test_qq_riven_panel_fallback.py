# -*- coding: utf-8 -*-
"""紫卡「面板来源」守门：`attacks[0]` 回填 + 同名去重识别（2026-11-13 第31轮）。

锁死两个**读键 bug**，防止下一轮被改回去：

  BUG1 顶层面板是占位值（`damage=null` / `cc=cm=proc=fr=0`），真值只在 `attacks[0]`。
       旧实现只读顶层 ⇒ Zaw 11 把 `_dps` 恒 0 ⇒ 伤害分给不出。
  BUG2 同名去重只认顶层 `damage.total` ⇒ 把 `Melee.json` 的 22 键占位记录判成
       「更好」，丢掉 `ExtraWeapons.json` 里带 `attacks` 的完整记录。

判据：
  P1 回填后 damage.total = sum(attacks[0].damage 各部位)
  P2 键名映射：crit_chance→criticalChance / crit_mult→criticalMultiplier /
     status_chance→procChance / speed→fireRate
  P3 已有真值不被 `attacks[0]` 覆盖（只在空/0 时回填）
  P4 顶层 damage.total>0 时必须是 **no-op**（零字段变化、无 `_panel_from_attacks` 标记）
  P5 attacks[0].damage 为空时 no-op（Kitgun 占位记录不得被「修」成有面板）
  P6 幂等：连续调用两次 total 不变
  P7 `_raw_has_panel` 认 attacks-only、不认占位
  P8 端到端：Zaw 11/11 panel=True 且 dps_base>0
  P9 端到端：Kitgun 6/6 panel=False（模块化，确证无固定面板）
  P10 端到端：623 把里 617 有面板；**除 Kitgun 外零无面板**
  P11 反证：构造一条「顶层 total>0 但 attacks 有别的数」的记录，回填不得改动它
"""
from __future__ import annotations

import copy
import importlib
import os
import sys
import types

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

if "nonebot" not in sys.modules:
    _nb = types.ModuleType("nonebot")
    _nbl = types.ModuleType("nonebot.log")

    class _SL:
        def __getattr__(self, _n):
            return lambda *a, **k: None

    _nbl.logger = _SL()
    _nb.log = _nbl
    sys.modules["nonebot"] = _nb
    sys.modules["nonebot.log"] = _nbl

if "httpx" not in sys.modules:
    try:
        import httpx  # noqa: F401
    except Exception:
        class _C:
            def __init__(self, *a, **k):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

        _hx = types.ModuleType("httpx")
        _hx.AsyncClient = _C
        _hx.Client = _C
        sys.modules["httpx"] = _hx

_GT = os.path.join(_ROOT, "src", "plugins", "qq_bridge", "game_tools")
if "gt" not in sys.modules:
    _pkg = types.ModuleType("gt")
    _pkg.__path__ = [_GT]
    sys.modules["gt"] = _pkg

# 出图包 `src/plugins/qq_bridge/pic`：宿主用两级虚拟包 `qqb` 挂上去（见 P13）。
_QB = os.path.join(_ROOT, "src", "plugins", "qq_bridge")
if "qqb" not in sys.modules:
    _qb = types.ModuleType("qqb")
    _qb.__path__ = [_QB]
    _qb.__package__ = "qqb"
    sys.modules["qqb"] = _qb

rc = importlib.import_module("gt.riven_card")

_P = _F = 0


def ok(name, cond, detail=""):
    global _P, _F
    if cond:
        _P += 1
        print("  ✅ %s %s" % (name, detail))
    else:
        _F += 1
        print("  ❌ %s %s" % (name, detail))


def zaw_rec(**over):
    """一条「Zaw Component 形状」的记录：顶层占位，真值在 attacks[0]。"""
    rec = {
        "name": "Dokrahm", "type": "Zaw Component", "category": "Melee",
        "damage": None,
        "criticalChance": 0, "criticalMultiplier": 0, "procChance": 0, "fireRate": 0,
        "attacks": [{
            "name": "Dokrahm Strike",
            "damage": {"impact": 46.4, "slash": 154.5, "puncture": 108.1},
            "crit_chance": 0.18, "crit_mult": 2.0,
            "status_chance": 0.22, "speed": 1.0,
        }],
    }
    rec.update(over)
    return rec


# ---------------------------------------------------------------- P1 / P2
def p1_p2():
    print("\n-- P1/P2 回填与键名映射 --")
    w = rc._lift_attack_panel(zaw_rec())
    d = w.get("damage") or {}
    ok("P1 damage.total=309.0", abs(float(d.get("total") or 0) - 309.0) < 1e-6,
       "total=%s" % d.get("total"))
    ok("P1 部位逐项", (d.get("impact"), d.get("slash"), d.get("puncture"))
       == (46.4, 154.5, 108.1), "%s" % {k: d.get(k) for k in ("impact", "slash", "puncture")})
    ok("P1 缺的元素键补 0", float(d.get("heat") or 0) == 0.0 and "viral" in d,
       "keys=%d" % len(d))
    ok("P2 cc/cm", abs(float(w["criticalChance"]) - 0.18) < 1e-9
       and abs(float(w["criticalMultiplier"]) - 2.0) < 1e-9,
       "cc=%s cm=%s" % (w["criticalChance"], w["criticalMultiplier"]))
    ok("P2 proc/fr", abs(float(w["procChance"]) - 0.22) < 1e-9
       and abs(float(w["fireRate"]) - 1.0) < 1e-9,
       "proc=%s fr=%s" % (w["procChance"], w["fireRate"]))
    ok("P2 打标记", w.get("_panel_from_attacks") is True)


# ---------------------------------------------------------------- P3 / P11
def p3_p11():
    print("\n-- P3/P11 已有真值不得被覆盖 --")
    w = rc._lift_attack_panel(zaw_rec(
        criticalChance=0.25, criticalMultiplier=2.5, procChance=0.3, fireRate=2.0))
    ok("P3 cc 保留 0.25", abs(float(w["criticalChance"]) - 0.25) < 1e-9, str(w["criticalChance"]))
    ok("P3 cm 保留 2.5", abs(float(w["criticalMultiplier"]) - 2.5) < 1e-9,
       str(w["criticalMultiplier"]))
    ok("P3 proc 保留 0.3", abs(float(w["procChance"]) - 0.3) < 1e-9, str(w["procChance"]))
    ok("P3 fr 保留 2.0", abs(float(w["fireRate"]) - 2.0) < 1e-9, str(w["fireRate"]))

    rec = zaw_rec(damage={"total": 500.0, "impact": 500.0, "puncture": 0.0, "slash": 0.0})
    w = rc._lift_attack_panel(rec)
    ok("P11 顶层有值则 damage 不动", float(w["damage"]["total"]) == 500.0,
       "total=%s" % w["damage"]["total"])
    ok("P11 顶层有值则不打标记", "_panel_from_attacks" not in w)


# ---------------------------------------------------------------- P4 / P5 / P6
def p4_p5_p6():
    print("\n-- P4/P5/P6 no-op 与幂等 --")
    rec = {"name": "Skana", "damage": {"total": 120.0, "impact": 18.0,
                                       "slash": 84.0, "puncture": 18.0},
           "criticalChance": 0.05, "criticalMultiplier": 1.5,
           "procChance": 0.16, "fireRate": 0.83,
           "attacks": [{"damage": {"impact": 1.0, "slash": 2.0, "puncture": 3.0}}]}
    snap = copy.deepcopy(rec)
    w = rc._lift_attack_panel(rec)
    ok("P4 有面板记录零改动", w == snap and "_panel_from_attacks" not in w,
       "changed=%s" % [k for k in snap if snap[k] != w.get(k)])

    kg = {"name": "Catchmoon", "type": "Kitgun Component",
          "damage": {"total": 0, "magnetic": 0.5},
          "criticalChance": 0, "criticalMultiplier": 0, "procChance": 0, "fireRate": 0,
          "attacks": [{"name": "Normal Attack", "damage": {}}]}
    snap2 = copy.deepcopy(kg)
    w = rc._lift_attack_panel(kg)
    ok("P5 占位记录 no-op", w == snap2 and "_panel_from_attacks" not in w,
       "total=%s" % (w.get("damage") or {}).get("total"))

    w = rc._lift_attack_panel(zaw_rec())
    t1 = float(w["damage"]["total"])
    w = rc._lift_attack_panel(w)
    t2 = float(w["damage"]["total"])
    ok("P6 幂等", abs(t1 - t2) < 1e-9, "%s → %s" % (t1, t2))


# ---------------------------------------------------------------- P7
def p7():
    print("\n-- P7 _raw_has_panel 判据 --")
    ok("P7 attacks-only = True", rc._raw_has_panel(zaw_rec()) is True)
    ok("P7 顶层 total = True",
       rc._raw_has_panel({"damage": {"total": 120.0}}) is True)
    ok("P7 占位记录 = False", rc._raw_has_panel(
        {"damage": {"total": 0}, "attacks": [{"damage": {}}]}) is False)
    ok("P7 无 damage 无 attacks = False", rc._raw_has_panel({"name": "x"}) is False)
    ok("P7 attacks 里全 0 = False", rc._raw_has_panel(
        {"damage": None, "attacks": [{"damage": {"impact": 0.0, "slash": 0.0}}]}) is False)


# ---------------------------------------------------------------- P8 / P9 / P10
def p8_p10():
    print("\n-- P8/P9/P10 端到端 7 类面板覆盖 --")
    disp, base, _ = rc._load_riven_tables()
    ok("P10 武器总数 623", len(disp) == 623, str(len(disp)))
    by = {}
    for name, meta in disp.items():
        t = (meta or {}).get("type")
        by.setdefault(t, []).append(name)

    def probe(name, typ):
        w = rc._weapon_raw(name) or {}
        parsed = {"type": typ, "weapon": name, "disp": 1.0,
                  "stats": [], "rank": 8, "riven_name": "gate"}
        sim = rc._sim(parsed, w)
        return w, sim, rc._scores(parsed, sim)

    zbad, zok = [], 0
    for n in sorted(by.get("Zaw") or []):
        w, sim, sc = probe(n, "Zaw")
        if sc.get("panel") and float(sim.get("dps_base") or 0) > 0:
            zok += 1
        else:
            zbad.append(n)
    ok("P8 Zaw 11/11 有面板", zok == 11 and not zbad, "ok=%d bad=%s" % (zok, zbad))

    kbad = []
    for n in sorted(by.get("Kitgun") or []):
        w, sim, sc = probe(n, "Kitgun")
        if sc.get("panel"):
            kbad.append(n)
    ok("P9 Kitgun 6/6 无面板", len(kbad) == 0 and len(by.get("Kitgun") or []) == 6,
       "误标=%s" % kbad)

    nopanel, total = [], 0
    for t, names in by.items():
        for n in names:
            total += 1
            w, sim, sc = probe(n, t)
            if not sc.get("panel"):
                nopanel.append("%s/%s" % (t, n))
    ok("P10 无面板仅 Kitgun", all(x.startswith("Kitgun/") for x in nopanel),
       "无面板 %d 把" % len(nopanel))
    ok("P10 有面板 617", total - len(nopanel) == 617,
       "%d/%d" % (total - len(nopanel), total))


def p12_copy():
    """P12 文案口径：Kitgun 必须说「模块化」，Zaw 必须是真数字（不许再说面板缺失）。"""
    print("\n-- P12 展示文案口径 --")
    rf = importlib.import_module("gt.riven_full")
    disp, base, _ = rc._load_riven_tables()

    def mk(weapon, typ):
        parsed = {"weapon": weapon, "weapon_zh": weapon, "type": typ,
                  "disp": float((disp.get(weapon) or {}).get("disp") or 1.0),
                  "rank": 8, "riven_name": "gate", "stats": []}
        fix = rc._fix_of(parsed)
        pool = [e for e in sorted(base.get(typ) or {})
                if e in rc._DPS_STAT_KEYS and abs(float(base[typ].get(e) or 0)) > 0]
        for en in pool[:3]:
            s = {"en": en, "zh": rc._stat_zh(en), "sign": "+", "unit": "%", "value": 0.0}
            rng = rc._range_of(s, parsed, fix)
            if not rng:
                continue
            s["value"] = round((rng[0] + rng[1]) / 2.0, 1)
            s["_rng"] = rng
            parsed["stats"].append(s)
        return parsed

    kg = [k for k, v in disp.items() if (v or {}).get("type") == "Kitgun"]
    market = rc.market_error_bundle(RuntimeError("gate 离线"))
    d = rf.riven_full_data(parsed=mk(kg[0], "Kitgun"), weapon=kg[0], market=market)
    kill = str(d.get("kill_line") or "")
    ok("P12 Kitgun 说「模块化」", "模块化" in kill, kill)
    ok("P12 Kitgun 不说「面板未收录」", "面板未收录" not in kill, kill)
    ok("P12 Kitgun 伤害分标不可用",
       any("不可用" in str(s.get("value")) for s in d.get("scores") or []),
       "%s" % [s.get("value") for s in d.get("scores") or []])

    z = [k for k, v in disp.items() if (v or {}).get("type") == "Zaw"]
    d2 = rf.riven_full_data(parsed=mk(z[0], "Zaw"), weapon=z[0], market=market)
    kill2 = str(d2.get("kill_line") or "")
    ok("P12 Zaw 给真击杀效率", "击杀效率" in kill2 and "无法评估" not in kill2, kill2)
    ok("P12 Zaw 有伤害分",
       any(str(s.get("label")) == "伤害分" and "%" not in str(s.get("value"))
           for s in d2.get("scores") or []),
       "%s" % [(s.get("label"), s.get("value")) for s in d2.get("scores") or []])


def p13_render():
    """P13 真出图：无面板卡必须能渲染（`kill_line` 契约是 token 列表，不是字符串）。

    2026-11-13 第31轮抓到的**真 bug**：无面板分支把 `kill_line` / `gain_line` 赋成普通
    字符串，模板 `token_line` 里 `for txt, col in parts` 会逐字符拆包 →
    `ValueError: not enough values to unpack (expected 2, got 1)`。此前 Archgun 20 +
    Zaw 11 + Kitgun 6 = 37 把武器出图全崩，线上静默回落文本卡所以没暴露。
    """
    print("\n-- P13 无面板卡真出图 --")
    try:
        pic = importlib.import_module("qqb.pic")
    except Exception as e:  # noqa: BLE001
        ok("P13 pic 可导入", False, "%s: %s" % (type(e).__name__, e))
        return
    rf = importlib.import_module("gt.riven_full")
    disp, base, _ = rc._load_riven_tables()
    market = rc.market_error_bundle(RuntimeError("gate 离线"))

    def mk(weapon, typ):
        parsed = {"weapon": weapon, "weapon_zh": weapon, "type": typ,
                  "disp": float((disp.get(weapon) or {}).get("disp") or 1.0),
                  "rank": 8, "riven_name": "gate", "stats": []}
        fix = rc._fix_of(parsed)
        pool = [e for e in sorted(base.get(typ) or {})
                if e in rc._DPS_STAT_KEYS and abs(float(base[typ].get(e) or 0)) > 0]
        for en in pool[:3]:
            s = {"en": en, "zh": rc._stat_zh(en), "sign": "+", "unit": "%", "value": 0.0}
            rng = rc._range_of(s, parsed, fix)
            if not rng:
                continue
            s["value"] = round((rng[0] + rng[1]) / 2.0, 1)
            s["_rng"] = rng
            parsed["stats"].append(s)
        return parsed

    for typ in ("Kitgun", "Zaw"):
        names = [k for k, v in disp.items() if (v or {}).get("type") == typ]
        d = rf.riven_full_data(parsed=mk(names[0], typ), weapon=names[0], market=market)
        kl = d.get("kill_line")
        ok("P13 %s kill_line 是 token 列表" % typ,
           isinstance(kl, list) and all(isinstance(t, (tuple, list)) and len(t) == 2
                                        for t in kl),
           "%s" % (type(kl).__name__,))
        try:
            buf = pic.render_sync("riven_full", data=d)
            b = bytes(buf) if isinstance(buf, (bytes, bytearray)) else buf.getvalue()
            ok("P13 %s 出图成功" % typ, len(b) > 100000, "%d bytes" % len(b))
        except Exception as e:  # noqa: BLE001
            ok("P13 %s 出图成功" % typ, False, "%s: %s" % (type(e).__name__, e))


def main():
    print("== 模块E. 紫卡面板来源守门（attacks[0] 回填 + 去重识别）==")
    p1_p2()
    p3_p11()
    p4_p5_p6()
    p7()
    p8_p10()
    p12_copy()
    p13_render()
    print("\n%d PASS / %d FAIL" % (_P, _F))
    return 1 if _F else 0


if __name__ == "__main__":
    sys.exit(main())
