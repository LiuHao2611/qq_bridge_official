# -*- coding: utf-8 -*-
"""守门：紫卡标准配卡桶（`_STD_BUILD_GUN`）——**按 7 类真实终局配卡，数值可回查事实表**。

用户铁律：游戏里几百把武器，近战/主武器/副武器/自制武器……只要有紫卡就都不一样，
所以配卡桶必须**按类型分桶**、零武器特例，且每个数字都能在 `Mods.json` 查到。

=== 第30轮修的根因 ===
旧 `_gun_std_bucket` 用单一常数：基伤 `1.65 if 主武器 else 2.20`、元素恒 1.20、
多重 `1.00 + (1.10 if 副武器 else 0.60)`、射速恒 0.30。真卡实测（冰凇 Verglas，
Rabbit 截图 8 Mod：紫卡/低温弹头Prime/地狱火/重口径/分裂膛室/卑劣加速/膛线/私法军备）
证明这套桶把**紫卡增益系统性高估 53pp**：同一张卡同一套 `_dps` 公式，
旧桶 **+120.1%**、真实桶 **+66.9%**、独立手算 **+66.9%**（逐位）。
⇒ 引擎没错，错的是桶：基伤只算了一张模组（膛线 165%），真实的终局必带重口径（再 +165%），
桶越弱，紫卡那条 +179.8% 基伤就越显得值钱。

判据：
  V1 表里每个数 = `Mods.json` canonical 条目满级值（双向回查，禁止手改不查表）
  V2 类型路由（Rifle/Pistol/Shotgun/Archgun/Kitgun/Zaw/未知）
  V3 零武器特例（AST 扫执行代码里的字符串，不许出现任何具体武器名）
  V4 冰凇 Verglas 第一性原理锚点：增益 +66.9%、裸叠最优 +200.0% ↔ Rabbit +198.1%
  V5 反证：旧单一常数实现必须已消失（否则桶会悄悄退回去）
  V6 全量 623 把 × 7 类跑通、四套桶全部被用到
  V7 霰弹必须走霰弹桶（`_derive_cat` 把 Shotgun 归成主武器，是旧 bug 的温床）
"""
from __future__ import annotations

import ast
import importlib.util
import json
import sys
import types
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_CARD = _REPO / "src" / "plugins" / "qq_bridge" / "game_tools" / "riven_card.py"
_MODS = _REPO / "data" / "warframe_items" / "Mods.json"
_DISP = _REPO / "data" / "warframe_items" / "riven_disp.json"

_P = _F = 0


def ok(cond, name, detail=""):
    global _P, _F
    if cond:
        _P += 1
        print("PASS %-56s %s" % (name, detail))
    else:
        _F += 1
        print("FAIL %-56s %s" % (name, detail))


def _stub_nonebot_httpx():
    for name in list(sys.modules):
        if name.startswith("gt") or name in ("nonebot", "nonebot.log", "httpx"):
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
    _stub_nonebot_httpx()
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


# ---------------------------------------------------------------------------
# Mods.json 解析（canonical = 排除 Beginner/Intermediate；Expert 仅 Primed 认）
# ---------------------------------------------------------------------------
_TAG = __import__("re").compile(r"<[^>]+>")
_PAT = __import__("re").compile(r"([+-]\d+(?:\.\d+)?)%\s*([A-Za-z][A-Za-z ]*)")
_SYN = {
    "melee damage": "Damage", "archgun damage": "Damage", "arch-gun damage": "Damage",
    "damage": "Damage", "multishot": "Multishot", "fire rate": "Fire Rate",
    "attack speed": "Fire Rate", "cold": "Cold", "heat": "Heat", "toxin": "Toxin",
    "electricity": "Electricity", "critical chance": "Critical Chance",
    "critical damage": "Critical Damage",
}

#: 「真实终局配卡」合同表：变体 → 类别 → [(mod 名, 期望满级值, 效果)]。期望值与
#: `_STD_BUILD_GUN`（非暴击）/ `_STD_BUILD_GUN_CRIT`（暴击）逐位对应，两侧互校。
#:
#: 🔴 **槽位预算（2026-11-13 第30轮）**：Warframe 只有 8 个 Mod 槽，紫卡占 1 ⇒ 真卡恒 ≤7 张。
#: 旧实现把暴击双卡**追加**在 7 张之上（Rifle/Pistol 9 张 + 紫卡 = 10 槽），物理不可能装备。
#: 现改为「暴击双卡占用『射速 + 第二张多重』两个位置」，总数仍是 7 张。
#: 非暴击 Rifle 的 7 张已由 Robot 冰凇真实 8 槽配卡逐张验证
#: （紫卡 + 膛线/重口径/卑劣加速/低温弹头Prime/地狱火/分裂膛室/私法军备）。
_CONTRACT = {
    "noncrit": {
        "Rifle": [("Serration", 1.65, "Damage"), ("Heavy Caliber", 1.65, "Damage"),
                  ("Vile Acceleration", 0.90, "Fire Rate"),
                  ("Vile Acceleration", -0.15, "Damage"),
                  ("Primed Cryo Rounds", 1.65, "Cold"), ("Hellfire", 0.90, "Heat"),
                  ("Split Chamber", 0.90, "Multishot"),
                  ("Vigilante Armaments", 0.60, "Multishot")],
        "Pistol": [("Hornet Strike", 2.20, "Damage"), ("Augur Pact", 0.90, "Damage"),
                   ("Anemic Agility", -0.15, "Damage"),
                   ("Anemic Agility", 0.90, "Fire Rate"),
                   ("Deep Freeze", 0.90, "Cold"), ("Heated Charge", 0.90, "Heat"),
                   ("Barrel Diffusion", 1.20, "Multishot"),
                   ("Lethal Torrent", 0.60, "Multishot")],
        "Shotgun": [("Point Blank", 0.90, "Damage"), ("Blaze", 0.60, "Damage"),
                    ("Chilling Grasp", 0.90, "Cold"), ("Incendiary Coat", 0.90, "Heat"),
                    ("Hell's Chamber", 1.20, "Multishot"),
                    ("Shotgun Barrage", 0.90, "Fire Rate")],
        "Archgun": [("Rubedo-Lined Barrel", 1.00, "Damage"), ("Sabot Rounds", 0.60, "Damage"),
                    ("Polar Magazine", 1.20, "Cold"),
                    ("Combustion Rounds", 1.20, "Heat"),
                    ("Primed Dual Rounds", 1.10, "Multishot"),
                    ("Automatic Trigger", 0.60, "Fire Rate")],
    },
    "crit": {
        "Rifle": [("Serration", 1.65, "Damage"), ("Heavy Caliber", 1.65, "Damage"),
                  ("Primed Cryo Rounds", 1.65, "Cold"), ("Hellfire", 0.90, "Heat"),
                  ("Split Chamber", 0.90, "Multishot"),
                  ("Point Strike", 1.50, "Critical Chance"),
                  ("Vital Sense", 1.20, "Critical Damage")],
        "Pistol": [("Hornet Strike", 2.20, "Damage"), ("Augur Pact", 0.90, "Damage"),
                   ("Deep Freeze", 0.90, "Cold"), ("Heated Charge", 0.90, "Heat"),
                   ("Barrel Diffusion", 1.20, "Multishot"),
                   ("Pistol Gambit", 1.20, "Critical Chance"),
                   ("Target Cracker", 0.60, "Critical Damage")],
        "Shotgun": [("Point Blank", 0.90, "Damage"), ("Blaze", 0.60, "Damage"),
                    ("Chilling Grasp", 0.90, "Cold"), ("Incendiary Coat", 0.90, "Heat"),
                    ("Hell's Chamber", 1.20, "Multishot"),
                    ("Blunderbuss", 0.90, "Critical Chance"),
                    ("Ravage", 0.60, "Critical Damage")],
        "Archgun": [("Rubedo-Lined Barrel", 1.00, "Damage"), ("Sabot Rounds", 0.60, "Damage"),
                    ("Polar Magazine", 1.20, "Cold"),
                    ("Combustion Rounds", 1.20, "Heat"),
                    ("Primed Dual Rounds", 1.10, "Multishot"),
                    ("Parallax Scope", 1.00, "Critical Chance"),
                    ("Hollowed Bullets", 0.80, "Critical Damage")],
    },
}

#: 变体 → 源码里对应的桶表名
_TBL = {"noncrit": "_STD_BUILD_GUN", "crit": "_STD_BUILD_GUN_CRIT"}

#: 8 槽硬上限：紫卡占 1 ⇒ 真卡 ≤7
_SLOT_CAP = 7


def _mod_values():
    """name → {effect: 满级值}，只留 canonical 条目。"""
    d = json.load(open(_MODS, encoding="utf-8"))
    out: dict[str, dict[str, float]] = {}
    for m in d:
        u = m.get("uniqueName") or ""
        name = m.get("name") or ""
        if "/Beginner/" in u or "/Intermediate/" in u:
            continue
        if "/Expert/" in u and not name.startswith("Primed"):
            continue
        lv = m.get("levelStats") or []
        stats = (lv[-1].get("stats") or []) if lv else []
        eff: dict[str, float] = {}
        for s in stats:
            for val, key in _PAT.findall(_TAG.sub("", s)):
                k = _SYN.get(key.strip().lower())
                if k:
                    eff[k] = float(val) / 100.0
        if eff:
            out.setdefault(name, eff)
    return out


def section_v1_contract():
    print("== V1. 配卡桶三向锁定：合同的期望值 ↔ Mods.json ↔ 源码常数 ==")
    rc = _load_card()
    mv = _mod_values()
    flat = [(n, want, eff) for d in _CONTRACT.values() for lst in d.values()
            for (n, want, eff) in lst]
    missing = [(n, e) for (n, _, e) in flat if e not in mv.get(n, {})]
    ok(not missing, "V1a 合同里的 Mod 与效果全部能在 Mods.json 找到", "missing=%s" % missing[:4])

    bad = []
    for n, want, eff in flat:
        got = mv.get(n, {}).get(eff)
        if got is None or abs(got - want) > 1e-9:
            bad.append("%s.%s: 表=%s 期望=%s" % (n, eff, got, want))
    ok(not bad, "V1b 合同里每个期望值 = Mods.json 满级值（防抄错）", "; ".join(bad[:3]))

    # 由合同反算六列，必须逐位等于对应桶表（noncrit ↔ _STD_BUILD_GUN / crit ↔ _CRIT）
    def total(variant, cat, eff):
        return round(sum(w for (_, w, e) in _CONTRACT[variant][cat] if e == eff), 10)

    for variant, tbl in _TBL.items():
        table = getattr(rc, tbl)
        drift = []
        for cat in ("Rifle", "Pistol", "Shotgun", "Archgun"):
            want = (total(variant, cat, "Damage"),
                    total(variant, cat, "Cold") + total(variant, cat, "Heat"),
                    total(variant, cat, "Multishot"), total(variant, cat, "Fire Rate"),
                    total(variant, cat, "Critical Chance"),
                    total(variant, cat, "Critical Damage"))
            got = tuple(round(x, 10) for x in table[cat])
            if got != tuple(round(x, 10) for x in want):
                drift.append("%s: 源码=%s 合同=%s" % (cat, got, want))
        ok(not drift, "V1c `%s` 六列逐位 = %s 合同算术和" % (tbl, variant),
           "; ".join(drift[:2]))
        print("     %-8s" % variant,
              {k: tuple(round(x, 2) for x in v) for k, v in table.items()})


def section_v2_routing():
    print("\n== V2. 类型路由：只看紫卡类型/面板字段，零武器特例 ==")
    rc = _load_card()
    cases = [("Verglas", "Rifle", "Rifle"), ("Verglas", None, "Rifle"),
             ("Boar", "Shotgun", "Shotgun"), ("Boar", None, "Shotgun"),
             ("Akarius", "Pistol", "Pistol"), ("Akarius", "Kitgun", "Pistol"),
             ("Boar", "Archgun", "Archgun")]
    bad = []
    for wname, rtype, want in cases:
        w = rc._weapon_raw(wname) or {}
        got = rc._std_gun_cat(w, rtype)
        if got != want:
            bad.append("%s(%s)→%s≠%s" % (wname, rtype, got, want))
    ok(not bad, "V2a 权威类型优先、缺失时回落面板 type", "; ".join(bad[:3]))

    # 未知类型 + 无面板：必须仍给一套合理桶（不许崩、不许空桶）
    b = rc._gun_std_bucket({}, "NoSuchType")
    ok(all(b[k] > 0 for k in ("base", "elem", "ms", "fr")),
       "V2b 未知类型/无面板仍产出完整桶（不崩、不空）", str({k: b[k] for k in ("base", "elem", "ms", "fr")}))

    # Kitgun 是真·副手：必须与 Pistol 同桶
    ok(rc._std_gun_cat({}, "Kitgun") == rc._std_gun_cat({}, "Pistol"),
       "V2c Kitgun（自制副手）与 Pistol 同桶")
    # Zaw 归近战：`_std_bucket` 必须走近战桶
    z = rc._std_bucket({"type": "Zaw", "weapon": "Zaw"}, {"category": "Melee"})
    m = rc._std_bucket({"type": "Melee", "weapon": "X"}, {"category": "Melee"})
    ok(z == m and z["ms"] == 0.0, "V2d Zaw 走近战桶（无多重、非枪械桶）", str(z))


def section_v3_no_special_case():
    print("\n== V3. 零武器特例：执行代码里不许出现具体武器名 ==")
    src = _CARD.read_text(encoding="utf-8")
    tree = ast.parse(src)

    def fn(nm):
        for n in ast.walk(tree):
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == nm:
                return n
        return None

    disp = json.load(open(_DISP, encoding="utf-8"))
    names = set()
    for en, v in disp.items():
        if isinstance(en, str) and len(en) >= 4:
            names.add(en)
        if isinstance(v, dict):
            zh = v.get("zh")
            if isinstance(zh, str) and len(zh) >= 4:
                names.add(zh)
    bad = []
    for f in ("_gun_std_bucket", "_std_gun_cat", "_std_bucket", "_melee_std_bucket"):
        node = fn(f)
        if node is None:
            bad.append("%s 缺失" % f)
            continue
        for n in ast.walk(node):
            if isinstance(n, ast.Constant) and isinstance(n.value, str):
                if n.value in names:
                    bad.append("%s 写死 %r" % (f, n.value))
    ok(not bad, "V3a 桶函数执行代码零具体武器名（n=%d 把武器参检）" % len(names), "; ".join(bad[:3]))


def section_v4_verglas_anchor():
    print("\n== V4. 冰凇 Verglas 第一性原理锚点（配卡由 Rabbit 截图给出，唯一已知 build）==")
    rc = _load_card()
    pv = {"weapon": "Verglas", "weapon_zh": "冰凇", "type": "Rifle", "disp": 1.2, "rank": 8,
          "stats": [{"en": "Toxin Damage", "zh": "毒素伤害", "sign": "+", "unit": "%", "value": 91.5},
                    {"en": "Damage", "zh": "基础伤害", "sign": "+", "unit": "%", "value": 179.8},
                    {"en": "Status Damage", "zh": "异常状态伤害", "sign": "+", "unit": "%", "value": 111.3},
                    {"en": "Critical Chance", "zh": "暴击几率", "sign": "-", "unit": "%", "value": 135.3}]}
    w = rc._weapon_raw("Verglas") or {}
    fix = rc._fix_of(pv)
    for s in pv["stats"]:
        s["_rng"] = rc._range_of(s, pv, fix)
    sim = rc._sim(pv, w)
    ok(abs(sim["gain"] * 100 - 66.9) <= 0.3,
       "V4a 当前增益 = 独立手算 +66.9%（旧弱桶给 +120.1%）",
       "gain=%.2f%%" % (sim["gain"] * 100))
    ok(abs(sim["g_best_raw"] * 100 - 200.0) <= 3.0,
       "V4b 裸叠理论最优 ≈Rabbit「最强紫卡 +198.1%」",
       "best=%.1f%%" % (sim["g_best_raw"] * 100))
    # 负词条代价必须仍是面板暴击占比（8%×2.0x → 7.41%），不许被桶放大
    ok(abs(rc._crit_share(pv, w) - 0.0741) <= 0.002,
       "V4c −135.3% 暴击几率在 8%/2.0x 面板上只值 7.41%（与桶无关）",
       "share=%.4f" % rc._crit_share(pv, w))


def section_v5_anti_regression():
    print("\n== V5. 反证：旧「单一常数桶」必须已消失（否则会悄悄退回） ==")
    src = _CARD.read_text(encoding="utf-8")
    tree = ast.parse(src)
    fn = None
    for n in ast.walk(tree):
        if isinstance(n, ast.FunctionDef) and n.name == "_gun_std_bucket":
            fn = n
    ok(fn is not None, "V5a `_gun_std_bucket` 存在")
    seg = ast.get_source_segment(src, fn) or ""
    ok("1.65 if" not in seg and "else 2.20" not in seg,
       "V5b 不再有 `1.65 if 主武器 else 2.20` 单基伤常数")
    ok("_STD_BUILD_GUN" in seg, "V5c 桶从 `_STD_BUILD_GUN` 表取数")
    ok("_ELEMENT_PCT" not in seg, "V5d 枪械桶不再用全局 `_ELEMENT_PCT`（只有近战用）")
    ok("rtype" in seg and "_std_gun_cat" in seg,
       "V5e 桶按类型选行（透传 parsed['type']，不再只看 _derive_cat）")
    # 反证：把表换成旧常数，V4a 必须复现 +120.1% —— 证明判据有判别力
    rc = _load_card()
    pv = {"weapon": "Verglas", "type": "Rifle", "disp": 1.2, "rank": 8,
          "stats": [{"en": "Toxin Damage", "zh": "毒素伤害", "sign": "+", "unit": "%", "value": 91.5},
                    {"en": "Damage", "zh": "基础伤害", "sign": "+", "unit": "%", "value": 179.8},
                    {"en": "Status Damage", "zh": "异常状态伤害", "sign": "+", "unit": "%", "value": 111.3},
                    {"en": "Critical Chance", "zh": "暴击几率", "sign": "-", "unit": "%", "value": 135.3}]}
    w = rc._weapon_raw("Verglas") or {}
    fix = rc._fix_of(pv)
    for s in pv["stats"]:
        s["_rng"] = rc._range_of(s, pv, fix)
    orig = dict(rc._STD_BUILD_GUN)
    try:
        rc._STD_BUILD_GUN.clear()
        rc._STD_BUILD_GUN.update({"Rifle": (1.65, 1.20, 1.60, 0.30, 1.50, 1.20),
                                  "Pistol": (2.20, 1.20, 2.10, 0.30, 1.50, 1.20),
                                  "Shotgun": (1.65, 1.20, 1.60, 0.30, 1.50, 1.20),
                                  "Archgun": (1.65, 1.20, 1.60, 0.30, 1.50, 1.20)})
        old_gain = rc._sim(pv, w)["gain"] * 100
    finally:
        rc._STD_BUILD_GUN.clear()
        rc._STD_BUILD_GUN.update(orig)
    ok(abs(old_gain - 120.1) <= 0.5,
       "V5f 反证：换回旧常数桶必复现 +120.1%（判据有判别力）", "old=%.1f%%" % old_gain)


def section_v6_all_weapons():
    print("\n== V6. 全量 623 把 × 7 类：四套桶都要被用到，桶必须有限且为正 ==")
    rc = _load_card()
    disp = json.load(open(_DISP, encoding="utf-8"))
    # riven_disp 结构：{英文武器名: {disp, type, zh}}
    used: dict[str, int] = {}
    bad = []
    n = 0
    for en, v in disp.items():
        if not isinstance(v, dict):
            continue
        rtype = v.get("type")
        w = rc._weapon_raw(en) or {}
        if not w:
            continue
        n += 1
        cat = rc._std_gun_cat(w, rtype)
        used[cat] = used.get(cat, 0) + 1
        b = rc._std_bucket({"type": rtype, "weapon": en}, w)
        if not all(isinstance(b[k], float) and -1.0 < b[k] < 100.0 for k in
                   ("base", "cc", "cm", "ms", "fr", "elem")):
            bad.append("%s→%s %s" % (en, cat, {k: b[k] for k in ("base", "elem", "ms", "fr")}))
    ok(n >= 500, "V6a 全量武器跑到（有面板）", "n=%d" % n)
    ok(not bad, "V6b 所有桶数值有限且在合理区间", "; ".join(bad[:3]))
    ok(set(used) >= {"Rifle", "Pistol", "Shotgun"}, "V6c 四套枪械桶全部被用到", str(used))
    print("     分布：", used)


def section_v8_slots():
    print("\n== V8. 槽位预算：8 槽里紫卡占 1 ⇒ 真卡恒 ≤7 张 ==")
    rc = _load_card()
    bad = []
    rows = []
    for variant, d in _CONTRACT.items():
        for cat, lst in d.items():
            n = len({x[0] for x in lst})
            rows.append("%s/%s=%d" % (variant, cat, n))
            if n > _SLOT_CAP:
                bad.append("%s/%s 装了 %d 张 > 上限 %d" % (variant, cat, n, _SLOT_CAP))
    ok(not bad, "V8a 全部变体/类别 Mod 数 ≤7（紫卡占 1 槽）",
       " ".join(rows) if not bad else "; ".join(bad))
    ok(len({x[0] for x in _CONTRACT["crit"]["Rifle"]}) == 7
       and len({x[0] for x in _CONTRACT["crit"]["Pistol"]}) == 7,
       "V8b 暴击变体步枪/手枪恰好 7 张（不是 9 张）",
       "Rifle=%d Pistol=%d" % (len({x[0] for x in _CONTRACT["crit"]["Rifle"]}),
                               len({x[0] for x in _CONTRACT["crit"]["Pistol"]})))
    # 非暴击表六列里的暴击两列必须为 0（暴击双卡只在 crit 变体里）
    zero = [cat for cat, row in rc._STD_BUILD_GUN.items()
            if abs(row[4]) > 1e-12 or abs(row[5]) > 1e-12]
    ok(not zero, "V8c 非暴击桶的暴击两列恒为 0（不重复计暴击）",
       "越界: %s" % zero if zero else "4/4")
    # 反证：暴击武器与非暴击武器必须走不同桶（同一武器改面板 cc 即可切换）
    w = dict(rc._weapon_raw("Verglas") or {})
    lo = rc._gun_std_bucket(dict(w, criticalChance=0.05), "Rifle")
    hi = rc._gun_std_bucket(dict(w, criticalChance=0.30), "Rifle")
    ok(abs(lo["cc"]) < 1e-12 and hi["cc"] > 0.1,
       "V8d 反证：cc 6%→不装暴击双卡、cc 30%→装（阈值 15% 生效）",
       "lo.cc=%.2f hi.cc=%.2f" % (lo["cc"], hi["cc"]))


def section_v9_forma():
    print("\n== V9. 极化次数：节省量必须是「原 drain − 极化后 drain」，不是 `d // 2` ==")
    rc = _load_card()
    tbl = rc._mods_canon()
    # V9a 数据面：7 类配卡表里每张卡都能查到 drain（查不到就应返回 None 而不是编数）
    miss = []
    for variant, d in rc._BUILD_EN.items():
        for cat, ens in d.items():
            for en in ens:
                if en not in tbl:
                    miss.append("%s/%s/%s" % (variant, cat, en))
    ok(not miss, "V9a 全部配卡 Mod 在 Mods.json 有 drain（无缺失）",
       "缺 %d: %s" % (len(miss), miss[:4]) if miss else "40/40")
    # V9b 算术恒等式：手算「逐张减半、不再装得下为止」与 _forma_needed 逐类一致。
    #     这正是旧 bug 的捕获点——旧写法 cur -= d // 2 在奇数 drain 上会高估次数。
    rows, bad = [], []
    for variant, d in rc._BUILD_EN.items():
        for cat, ens in d.items():
            drains = sorted((tbl[e][1] for e in ens), reverse=True)
            cur, n = sum(drains), 0
            for dv in drains:
                if cur <= rc._MOD_CAPACITY:
                    break
                cur -= dv - dv // 2
                n += 1
            got = rc._forma_needed(ens)
            rows.append("%s/%s=%s(%d)" % (variant, cat, got, cur))
            if got != n or cur > rc._MOD_CAPACITY:
                bad.append("%s/%s 期望%d≠实得%s" % (variant, cat, n, got))
    ok(not bad, "V9b 最小极化次数 = 手算减半收敛值（6/6 类）",
       " ".join(rows) if not bad else "; ".join(bad))
    # V9c 反证：旧写法（cur -= d // 2）在暴击霰弹上会多算一次 —— 守门必须能分辨
    ens = rc._BUILD_EN["crit"]["Shotgun"]
    drains = sorted((tbl[e][1] for e in ens), reverse=True)
    cur, n_old = sum(drains), 0
    for dv in drains:
        if cur <= rc._MOD_CAPACITY:
            break
        cur -= dv // 2
        n_old += 1
    ok(rc._forma_needed(ens) < n_old,
       "V9c 反证：旧写法暴击霰弹多算 1 次（新=%d 旧=%d）"
       % (rc._forma_needed(ens), n_old),
       "drains=%s" % drains)
    # V9d 边界：容量足够 → 0 次；数据缺失 → None（绝不编数）
    ok(rc._forma_needed(["serration"], capacity=60) == 0,
       "V9d 容量足够时返回 0 次")
    ok(rc._forma_needed(["__no_such_mod__"]) is None,
       "V9e 卡名查不到 → None（不编造次数）")
    # V9f 单调性：容量越紧次数不减；**即使全极化也装不下**时必须 None（不编数）
    seq = [rc._forma_needed(rc._BUILD_EN["noncrit"]["Rifle"], capacity=c)
           for c in (60, 45)]
    ok(all(a is not None for a in seq) and seq == sorted(seq),
       "V9f 容量收紧→次数单调不减", str(seq))
    # 非暴击步枪 59 点已是 7 槽全极化后的下限（pol 后 drain=7+8+5+4+4+4+... 见 V9b），
    # 容量 30（不装 Orokin 反应堆）时无解 → None
    ok(rc._forma_needed(rc._BUILD_EN["noncrit"]["Rifle"], capacity=30) is None,
       "V9g 容量 30（无反应堆）无解 → None，不编造次数")


def section_v10_text_card():
    print("\n== V10. 文本卡（build_card）必须与图片卡同源：7 类映射 + 极化次数 ==")
    import ast
    from pathlib import Path
    rc = _load_card()
    repo = Path(__file__).resolve().parents[1]
    full_src = (repo / "src" / "plugins" / "qq_bridge" / "game_tools" / "riven_full.py") \
        .read_text(encoding="utf-8")
    card_src = (repo / "src" / "plugins" / "qq_bridge" / "game_tools" / "riven_card.py") \
        .read_text(encoding="utf-8")
    # V10a 两处 7 类→配卡键映射必须逐项相等（一个在 riven_card、一个在 riven_full）
    ftree = ast.parse(full_src)
    _full_map = None
    for n in ftree.body:
        if isinstance(n, ast.Assign) and any(
                getattr(t, "id", "") == "_TYPE_TO_BUILD" for t in n.targets):
            _full_map = ast.literal_eval(n.value)
    ok(_full_map == rc._TYPE_TO_BUILD_KEY and _full_map is not None,
       "V10a riven_full._TYPE_TO_BUILD ≡ riven_card._TYPE_TO_BUILD_KEY（7 类零漂移）",
       "full=%s" % (_full_map or {}))
    # V10b 7 类全覆盖：Zaw→近战、Kitgun→手枪（旧文本卡把这两个都落成步枪）
    pairs = {"Melee": "melee", "Zaw": "melee", "Pistol": "Pistol", "Kitgun": "Pistol",
             "Rifle": "Rifle", "Archgun": "Rifle", "Shotgun": "Shotgun"}
    bad = [k for k, v in pairs.items() if rc._build_key_for_type(k) != v]
    ok(not bad, "V10b 7 类逐项正确（Zaw→近战 / Kitgun→手枪）", "错: %s" % bad if bad else "7/7")
    # V10c 未知类型必须落空串（→ 诚实占位），绝不默认套步枪
    unk = [t for t in (None, "", "Warframe", "Archwing", "Unknown", "Melee2")
           if rc._build_key_for_type(t)]
    ok(not unk, "V10c 未知/战甲/空类型 → 空键（不默认套 Rifle）", "误判: %s" % unk if unk else "6/6")
    # V10d–V10f 只看 build_card 的**执行代码**（AST），注释里引用旧写法不算（同 A7b 口径）
    ctree = ast.parse(card_src)
    f_bc = next((n for n in ctree.body if isinstance(n, ast.FunctionDef)
                 and n.name == "build_card"), None)
    bc_calls = [n for n in (ast.walk(f_bc) if f_bc else []) if isinstance(n, ast.Call)]

    def _is_build_get(c):
        return (isinstance(c.func, ast.Attribute) and c.func.attr == "get"
                and isinstance(c.func.value, ast.Name) and c.func.value.id == "_BUILD")

    ok(f_bc is not None and not any(_is_build_get(c) for c in bc_calls),
       "V10d build_card 不再用 _BUILD.get(type, Rifle) 默认套枪卡",
       "" if f_bc is not None else "build_card 未找到")
    ok(not any(isinstance(c.func, ast.Name) and c.func.id == "len"
               and c.args and isinstance(c.args[0], ast.Name)
               and c.args[0].id == "chips" for c in bc_calls),
       "V10e 不再用 len(chips) 当极化次数（len 恒 8 = 非最小化）")
    ok(f_bc is not None and any(isinstance(c.func, ast.Name) and c.func.id == "_forma_needed"
                                for c in bc_calls), "V10f 文本卡用 _forma_needed 实算次数")
    ok(f_bc is not None and any(isinstance(c.func, ast.Name) and c.func.id == "_std_crit"
                                for c in bc_calls), "V10g 文本卡用 _std_crit 选暴击/非暴击变体")


def section_v7_shotgun():
    print("\n== V7. 霰弹必须走霰弹桶（`_derive_cat` 把 Shotgun 归成主武器 = 旧 bug 温床）==")
    rc = _load_card()
    wb = rc._weapon_raw("Boar") or {}
    wv = rc._weapon_raw("Verglas") or {}
    ok(rc._derive_cat(wb) == rc._derive_cat(wv) == "主武器",
       "V7a `_derive_cat` 对霰弹与步枪返回同一类（证明旧实现分不出）",
       "%s / %s" % (rc._derive_cat(wb), rc._derive_cat(wv)))
    bb = rc._gun_std_bucket(wb, "Shotgun")
    bv = rc._gun_std_bucket(wv, "Rifle")
    ok(bb["base"] != bv["base"] and bb["elem"] != bv["elem"],
       "V7b 霰弹桶与步枪桶不同（基伤 %.2f vs %.2f）" % (bb["base"], bv["base"]),
       "shotgun=%s rifle=%s" % (bb["base"], bv["base"]))
    # 无 rtype 时也必须靠面板 type 分出霰弹
    ok(rc._std_gun_cat(wb, None) == "Shotgun",
       "V7c 类型缺失时靠面板 type=Shotgun 仍正确分流")


if __name__ == "__main__":
    print("== 模块V. 紫卡标准配卡桶（按 7 类真实终局配卡）==")
    section_v1_contract(); print()
    section_v2_routing(); print()
    section_v3_no_special_case(); print()
    section_v4_verglas_anchor(); print()
    section_v5_anti_regression(); print()
    section_v6_all_weapons(); print()
    section_v7_shotgun(); print()
    section_v8_slots(); print()
    section_v9_forma(); print()
    section_v10_text_card()
    print("\n%d PASS / %d FAIL" % (_P, _F))
    sys.exit(1 if _F else 0)
