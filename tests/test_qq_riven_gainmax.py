# -*- coding: utf-8 -*-
"""守门：紫卡评分全武器泛化 + 两张 Rabbit 金标准（官方副本，2026-11-12 第27轮重写）。

核心目标（用户铁律）：**反推别人的紫卡分析逻辑并泛化到全部武器**，零写死卡名/数值。
所有评分输入（基伤/暴击/词条基线）来自 `riven_stat_base.json` 与武器面板 json，
`_sim`/`_scores` 对任意武器走同一套公式；「近战/主武器/副武器/自制武器……只要是有
紫卡的武器都要搞清楚」→ E 段对 riven_disp 的 623 把武器 × 7 类做全量覆盖。

=== 第27轮修的三个根因（都留了反证判据）===
R1 配卡桶浅拷贝污染：`dict(std)` 让 cur/cur_max/best/b2 共用同一个 `phys_neg` 子字典，
   `_apply_riven` 原地 `+=` 反向污染源桶 → dps_base 虚高（Braton 1200.77→2278.73）、
   gain_max 被压低 → dmg 顶 100。判据 A12 / F 段。
R2 元素伤建模错：旧式 `elem_total*(1+elem)` 只放大**已有**元素伤，纯 IPS 武器（Braton
   等多数步枪/霰弹/近战）的元素词条边际恒为 0 → 元素类紫卡全部评 0 分。判据 A14。
R3 口径不一致：最优组合按「裸 DPS 边际」挑、比较对象却是「持续增益」，弹匣/装填类
   词条只抬持续增益不进裸边际 → 天花板系统性低估 → 全量扫描 56/623 把武器中位卡顶
   100。判据 A3b / A13 / E6。

=== 已反向还原并锁定的 Rabbit 金标准（两张参考卡）===
A) 阿利乌双枪 VisiPi（Pistol, disp=1.2, rank=8）—— 枪械
     高 roll: 伤害62.5(A) 市场85.5(S+) 综合85.5  | 增益 当前+100.7% → 最强+161.1%
     低 roll: 伤害10.1    市场0.0     综合10.1
   → 本项目实测：gain_max=178.5%、高roll dmg=62.2/mkt=85.7、低roll dmg=8.8/mkt=0.0。
B) Coda Motovore（Melee, disp=1.2, rank=8）—— 近战
     伤害11.0 市场62.1 综合62.1 | 增益 当前+15.2% → 最强+138.4%
   → 本项目实测：gain=15.2%/gain_max=137.5%/dmg=11.0/mkt=62.3（本轮已严格对齐）。

锚定 official 副本根（parents[1]）。纯 AST + 隔离装配真 riven_card，宿主/容器可跑。
"""
from __future__ import annotations

import ast
import importlib.util
import math
import sys
import types
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_CARD = _REPO / "src" / "plugins" / "qq_bridge" / "game_tools" / "riven_card.py"

_P = _F = 0


def ok(cond, name, detail=""):
    global _P, _F
    if cond:
        _P += 1
        print("PASS %-52s %s" % (name, detail))
    else:
        _F += 1
        print("FAIL %-52s %s" % (name, detail))


def _stub_nonebot_httpx():
    for name in list(sys.modules):
        if name.startswith("_gt") or name in ("nonebot", "nonebot.log", "httpx", "gt", "gt.warframe", "gt.riven_card"):
            del sys.modules[name]
    class _L:
        def info(self, *a, **k): pass
        def warning(self, *a, **k): pass
        def debug(self, *a, **k): pass
        def error(self, *a, **k): pass
        def exception(self, *a, **k): pass
    nb = types.ModuleType("nonebot"); nbl = types.ModuleType("nonebot.log")
    nbl.logger = _L(); nb.log = nbl
    sys.modules["nonebot"] = nb; sys.modules["nonebot.log"] = nbl
    httpx = types.ModuleType("httpx")
    for n in ("HTTPError", "HTTPStatusError", "RequestError", "TimeoutException"):
        setattr(httpx, n, type(n, (Exception,), {}))
    httpx.AsyncClient = object
    sys.modules["httpx"] = httpx


def _load_card():
    _stub_nonebot_httpx()
    GT = _CARD.parent
    pkg = types.ModuleType("gt"); pkg.__path__ = [str(GT)]; sys.modules["gt"] = pkg

    def load(n):
        s = importlib.util.spec_from_file_location("gt." + n, GT / (n + ".py"))
        m = importlib.util.module_from_spec(s); sys.modules["gt." + n] = m
        s.loader.exec_module(m); return m

    load("warframe")              # 真 warframe：读本地武器面板 json，不触网
    return load("riven_card")


def _fn(tree, name):
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name:
            return n
    return None


def _code_strings(node):
    doc_ids = set()
    for n in ast.walk(node):
        if isinstance(n, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            b = getattr(n, "body", None)
            if b and isinstance(b[0], ast.Expr) and isinstance(b[0].value, ast.Constant) \
                    and isinstance(b[0].value.value, str):
                doc_ids.add(id(b[0].value))
    return {n.value for n in ast.walk(node)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)
            and id(n) not in doc_ids}


# 阿利乌双枪 Rabbit 金标准输入（与 validate_scores.py 同源）
_AK_HI = (68.1, 355.3, 58.1)   # 高 roll
_AK_LO = (15.0, 50.0, 10.0)    # 低 roll（真实小值，Rabbit 市场0.0）


def _ak_parsed(vals):
    return {
        "weapon": "Akarius", "weapon_zh": "阿利乌双枪", "type": "Pistol",
        "disp": 1.2, "rank": 8, "riven_name": "VisiPi",
        "stats": [
            {"en": "Ammo Efficiency", "zh": "弹药效率", "sign": "+", "unit": "%", "value": vals[0]},
            {"en": "Damage",         "zh": "基础伤害", "sign": "+", "unit": "%", "value": vals[1]},
            {"en": "Weapon Recoil",  "zh": "武器后坐力", "sign": "+", "unit": "%", "value": vals[2]},
        ],
    }


_MELEE_CARDS = ("暗病打击", "压迫 Prime", "牺牲斩铁", "肢解", "北风", "病毒")


def section_a_static():
    print("== A. 静态纪律：理论最优组合从真表派生、零写死卡名、三个根因判据 ==")
    src = _CARD.read_text(encoding="utf-8")
    tree = ast.parse(src)

    f_tb = _fn(tree, "_theoretical_best_stats")
    ok(f_tb is not None, "A1 存在 _theoretical_best_stats 唯一出口")
    tb_src = ast.get_source_segment(src, f_tb) or ""
    ok("_load_riven_tables" in tb_src or "base_tbl" in tb_src,
       "A2 理论最优组合读 riven_stat_base 真表")
    ok("_dps(" in tb_src, "A3a 边际用 _dps 真函数（非静态热度表）")
    # R3：边际口径必须与 gain（持续 DPS）同度量
    ok("_sustain_ratio" in tb_src,
       "A3b 边际走持续 DPS 口径（与 gain 同度量，防天花板低估）")
    ok("_DPS_STAT_KEYS" in tb_src, "A4 只从可进 DPS 桶的词条键筛选")
    ok("_ZERO_IMPACT_NEG" in tb_src, "A5 负槽取零影响候选（不伪造 DPS 增益）")

    tb_strings = _code_strings(f_tb)
    leaked = [s for s in tb_strings if any(c in s for c in _MELEE_CARDS)]
    ok(not leaked, "A6 理论最优组合执行代码零写死具体卡名", "leak=%s" % leaked[:3])

    f_sim = _fn(tree, "_sim")
    sim_src = ast.get_source_segment(src, f_sim) or ""
    ok("_theoretical_best_stats" in sim_src, "A7 _sim 用理论最优组合作 best 基准")
    ok("best_b = cur_max_b" in sim_src,
       "A8 _sim 真表缺失兜底 = 当前组合满 roll（cur_max_b，非旧 best=当前4条洗满）")

    f_sc = _fn(tree, "_scores")
    sc_src = ast.get_source_segment(src, f_sc) or ""
    ok("max(dmg_score, mkt)" in sc_src, "A9 综合分 = max(伤害, 市场)")
    ok('"total": total' in sc_src, "A10 _scores 返回 total=新口径")
    ok("wsum_roll" in sc_src and "STAT_MARKET" in sc_src,
       "A11 市场分 = STAT_MARKET 加权平均 roll 完成度（无地板）")

    # R1 浅拷贝污染：_sim 里禁止再出现裸 dict(std)，必须走 _clone_bucket
    ok("dict(std)" not in sim_src and sim_src.count("_clone_bucket(std)") >= 3,
       "A12 _sim 全部走 _clone_bucket 深拷贝（禁 dict(std) 共享 phys_neg）",
       "clone=%d" % sim_src.count("_clone_bucket(std)"))
    # A13：天花板不变式必须同时纳入 g_cur_max 与 g_cur（否则弹匣/装填类持续增益会被漏掉，
    # dmg 被 min(100) 顶满）。实现已从 `gain_max = max(...)` 改为带标签的 max（暴露 ceil_src），
    # 判据改为**语义断言**：两个持续口径候选必须都在 max 候选集里。
    ok(('("cur_max", g_cur_max)' in sim_src and '("cur", g_cur)' in sim_src),
       "A13 天花板不变式含持续口径 g_cur_max/g_cur（防 dmg 顶 100）")
    ok('"ceil_src": _ceil_tag' in sim_src,
       "A13b _sim 回传 ceil_src（分母口径可观测，不许静默抬升）")

    f_dps = _fn(tree, "_dps")
    dps_src = ast.get_source_segment(src, f_dps) or ""
    # R2 元素建模：必须按「基伤 × 百分比」追加，不能只放大已有元素伤
    ok('elem_innate + bucket["elem"] * total' in dps_src,
       "A14 元素伤按基伤百分比追加（纯 IPS 武器元素词条不再评 0）")
    ok('elem_total * bm * (1.0 + bucket["elem"])' not in dps_src,
       "A14b 旧式「放大已有元素伤」实现已移除")

    f_lt = _fn(tree, "_letter")
    lt_src = ast.get_source_segment(src, f_lt) or ""
    ok('"S+"' in lt_src and "60" in lt_src,
       "A15 字母档含 S+ 且 A 档覆盖 62.5（对齐 Rabbit 62.5A / 85.5S+）")


def section_b_gun():
    print("== B. 枪械真行为：阿利乌高/低 roll 双向命中 Rabbit 金标准 ==")
    rc = _load_card()
    w = rc._weapon_raw("Akarius")
    ok(w is not None, "B0 真武器面板 Akarius 可加载")

    def run(vals):
        parsed = _ak_parsed(vals)
        fix = rc._fix_of(parsed)
        for s in parsed["stats"]:
            s["_rng"] = rc._range_of(s, parsed, fix)
        sim = rc._sim(parsed, w)
        return sim, rc._scores(parsed, sim)

    sim_hi, sc_hi = run(_AK_HI)
    sim_lo, sc_lo = run(_AK_LO)

    # B1 天花板口径（2026-11-13 第30轮改为**真实终局配卡桶**后的重锚）：
    # 天花板 = 理论最优裸叠增益 × `_OPTIMAL_CALIB_GUN`，桶一换裸叠基数就变（阿利乌
    # 595% → 432.3%），所以不能再按旧桶的 178.5% 卡死。改为「与 Rabbit 最强 +161.1%
    # 同量级（±15%）」——精确锚点移到**配卡已知**的冰凇卡（Rabbit +198.1% ↔ 裸叠 +200.0%，
    # 见 test_qq_riven_std_build.py 的 V4），阿利乌的配卡从没在截图里出现过，本身就是反推。
    ok(abs(sc_hi["gain_max"] * 100 - 161.1) <= 161.1 * 0.15,
       "B1 高roll gain_max 与 Rabbit 最强+161.1% 同量级（±15%，裸叠口径随配卡桶变）",
       "gain_max=%.1f%%" % (sc_hi["gain_max"] * 100))
    ok(abs(sc_hi["gain_max"] - sc_lo["gain_max"]) < 1e-6,
       "B2 增益天花板与 roll 无关（同一武器固定）",
       "hi=%.1f lo=%.1f" % (sc_hi["gain_max"] * 100, sc_lo["gain_max"] * 100))

    ok(abs(sc_hi["dmg"] - 62.5) <= 3.0, "B3 高roll 伤害分≈62.5（A）", "dmg=%.1f" % sc_hi["dmg"])
    ok(abs(sc_hi["mkt"] - 85.5) <= 2.0, "B4 高roll 市场分≈85.5（S+）", "mkt=%.1f" % sc_hi["mkt"])
    ok(abs(sc_hi["total"] - max(sc_hi["dmg"], sc_hi["mkt"])) < 1e-6,
       "B5 综合分 = max(伤害,市场)", "total=%.1f" % sc_hi["total"])
    ok(rc._letter(sc_hi["dmg"]) == "A", "B6a 62 分档显示 A（Rabbit 62.5A）",
       rc._letter(sc_hi["dmg"]))
    ok(rc._letter(sc_hi["mkt"]) == "S+", "B6b 85 分档显示 S+（Rabbit 85.5S+）",
       rc._letter(sc_hi["mkt"]))

    ok(abs(sc_lo["dmg"] - 10.1) <= 2.0, "B7 低roll 伤害分≈10.1", "dmg=%.1f" % sc_lo["dmg"])
    ok(sc_lo["mkt"] < 1.0, "B8 低roll 市场分≈0.0（跌破区间下限）", "mkt=%.1f" % sc_lo["mkt"])
    ok(abs(sc_lo["total"] - sc_lo["dmg"]) < 1e-6,
       "B9 低roll 综合=伤害（市场0）", "total=%.1f" % sc_lo["total"])

    exp_hi = sc_hi["gain"] / sc_hi["gain_max"] * 100.0
    ok(abs(sc_hi["dmg"] - exp_hi) < 1e-6,
       "B10 伤害分=当前增益/天花板（公式锁定）", "%.1f" % exp_hi)
    ok(sim_hi["ke_best"] > sim_hi["ke_cur"] > sim_hi["ke_base"],
       "B11 击杀效率 无紫卡<当前<最强", "%.1f/%.1f/%.1f" % (
           sim_hi["ke_base"], sim_hi["ke_cur"], sim_hi["ke_best"]))


_CODA_STATS = [
    {"en": "Critical Chance", "value": 222.0, "unit": "%", "sign": "+"},
    {"en": "Impact Damage", "value": 146.4, "unit": "%", "sign": "+"},
    {"en": "Heavy Attack Efficiency", "value": 90.4, "unit": "%", "sign": "+"},
    {"en": "Critical Chance for Slide Attack", "value": 109.3, "unit": "%", "sign": "-"},
]


def section_c_melee():
    print("== C. 近战真行为：Coda Motovore 逐值命中 Rabbit 金标准 ==")
    rc = _load_card()
    w = rc._weapon_raw("Coda Motovore")
    ok(w is not None, "C0 真武器面板 Coda Motovore 可加载")
    parsed = {"weapon": "Coda Motovore", "type": "Melee", "disp": 1.2, "rank": 8,
              "stats": [dict(s) for s in _CODA_STATS]}
    fix = rc._fix_of(parsed)
    for s in parsed["stats"]:
        s["_rng"] = rc._range_of(s, parsed, fix)
    sim = rc._sim(parsed, w)
    sc = rc._scores(parsed, sim)

    ok(abs(sc["gain"] * 100 - 15.2) <= 1.0, "C1 当前增益 ≈Rabbit +15.2%",
       "gain=%.1f%%" % (sc["gain"] * 100))
    ok(abs(sc["gain_max"] * 100 - 138.4) <= 3.0, "C2 增益天花板 ≈Rabbit +138.4%",
       "gain_max=%.1f%%" % (sc["gain_max"] * 100))
    ok(abs(sc["dmg"] - 11.0) <= 1.0, "C3 伤害分 ≈Rabbit 11.0", "dmg=%.1f" % sc["dmg"])
    ok(abs(sc["mkt"] - 62.1) <= 1.5, "C4 市场分 ≈Rabbit 62.1", "mkt=%.1f" % sc["mkt"])
    ok(sc["dmg"] < 100.0, "C5 近战伤害分不虚高到 100(S)", "dmg=%.1f" % sc["dmg"])
    ok(abs(sc["total"] - max(sc["dmg"], sc["mkt"])) < 1e-6,
       "C6 综合 = max(伤害,市场)", "total=%.1f" % sc["total"])


def section_d_injection():
    print("== D. 注入反证：旧实现必须被守门判据抓住 ==")
    src = _CARD.read_text(encoding="utf-8")

    inj = ast.parse(src)
    inj_sim = _fn(inj, "_sim")
    found = False
    for n in ast.walk(inj_sim):
        if isinstance(n, ast.Assign) and isinstance(n.value, ast.Call) \
                and isinstance(n.value.func, ast.Name) \
                and n.value.func.id == "_theoretical_best_stats":
            n.value = ast.Constant(value=[])
            found = True
            break
    ok(found, "D1 注入 _best=[] 强制旧语义（反证样本构造）")
    ok("_theoretical_best_stats" not in ast.unparse(inj_sim),
       "D2 注入后 _sim 不再调用理论最优组合（A7 判据可抓）")

    inj2 = ast.parse(src)
    inj_sc = _fn(inj2, "_scores")
    replaced = False
    for n in ast.walk(inj_sc):
        if isinstance(n, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "total" for t in n.targets):
            n.value = ast.Name(id="dmg_score", ctx=ast.Load())
            replaced = True
            break
    ok(replaced, "D3 注入 total=dmg_score（旧口径反证）")
    ok("max(dmg_score, mkt)" not in ast.unparse(inj_sc),
       "D4 注入后 max 口径消失（A9 判据可抓）")

    # R1 反证：把 _clone_bucket 换回 dict，A12 判据必须抓
    inj3 = ast.parse(src)
    inj_sim3 = _fn(inj3, "_sim")
    n_repl = 0
    for n in ast.walk(inj_sim3):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) \
                and n.func.id == "_clone_bucket":
            n.func = ast.Name(id="dict", ctx=ast.Load()); n_repl += 1
    ok(n_repl >= 3, "D5 注入 _clone_bucket→dict 构造浅拷贝反证样本", "n=%d" % n_repl)
    ok("dict(std)" in ast.unparse(inj_sim3), "D6 注入后出现 dict(std)（A12 判据可抓）")


def section_f_pollution():
    print("== F. R1 反证：_sim 不得污染配卡桶的 phys_neg（dmg=100 根因）==")
    rc = _load_card()
    w = rc._weapon_raw("Braton") or {}
    if not w:
        ok(False, "F0 Braton 面板可加载（污染反证需要物理词条武器）")
        return
    parsed = {"weapon": "Braton", "type": "Rifle", "disp": 1.2, "rank": 8, "stats": []}
    fix = rc._fix_of(parsed)
    for en in ("Damage", "Multishot", "Slash Damage"):
        s = {"en": en, "sign": "+", "unit": "%", "value": 0.0}
        rng = rc._range_of(s, parsed, fix)
        s["value"] = round((rng[0] + rng[1]) / 2, 1)
        s["_rng"] = rng
        parsed["stats"].append(s)

    std_before = rc._std_bucket(parsed, w)
    base_before = rc._dps(w, std_before)
    sim = rc._sim(parsed, w)
    std_after = rc._std_bucket(parsed, w)
    base_after = rc._dps(w, std_after)
    ok(all(abs(v) < 1e-12 for v in std_before["phys_neg"].values()),
       "F1 源桶 phys_neg 构造后全 0", str(std_before["phys_neg"]))
    ok(abs(base_before - base_after) < 1e-9,
       "F2 _sim 前后 _std_bucket 重算 dps 完全一致（无跨桶污染）",
       "%.4f/%.4f" % (base_before, base_after))
    ok(all(abs(v) < 1e-12 for v in std_after["phys_neg"].values()),
       "F3 _sim 后源桶 phys_neg 仍全 0", str(std_after["phys_neg"]))
    dmg = min(100.0, sim["gain"] / sim["gain_max"] * 100.0) if sim["gain_max"] > 0 else 0.0
    ok(dmg < 100.0, "F4 含物理词条的卡不再顶 100", "dmg=%.1f gain=%.1f%% max=%.1f%%"
       % (dmg, sim["gain"] * 100, sim["gain_max"] * 100))

    # 浅拷贝必须能复现污染（证明判据不是空转）
    std2 = rc._std_bucket(parsed, w)
    b0 = rc._dps(w, std2)
    shallow = dict(std2)
    rc._apply_riven(shallow, parsed["stats"])
    b1 = rc._dps(w, std2)
    ok(abs(b1 - b0) > 1e-6,
       "F5 反证：dict() 浅拷贝确实污染源桶（判据有效）", "%.2f→%.2f" % (b0, b1))


def section_e_all_weapons():
    print("== E. 全量覆盖：riven_disp 623 把武器 × 7 类（全类型统一评分）==")
    rc = _load_card()
    disp_tbl, base_tbl, _ = rc._load_riven_tables()
    ok(len(disp_tbl) >= 600, "E0 倾向表覆盖 600+ 可装紫卡武器", "n=%d" % len(disp_tbl))
    types = [t for t in ("Rifle", "Shotgun", "Pistol", "Melee", "Zaw", "Kitgun", "Archgun")
             if t in base_tbl]
    ok(len(types) == 7, "E1 词条真表覆盖 7 类武器", ",".join(types))

    def pick(typ):
        tbl = base_tbl.get(typ) or {}
        pos = sorted(e for e in tbl if e in rc._DPS_STAT_KEYS and abs(float(tbl.get(e) or 0)) > 0)
        neg = sorted(e for e in tbl if e in rc._ZERO_IMPACT_NEG)
        return pos[:3], (neg[0] if neg else None)

    def card(weapon, typ, mode):
        p = {"weapon": weapon, "weapon_zh": weapon, "type": typ,
             "disp": float((disp_tbl.get(weapon) or {}).get("disp") or 1.0),
             "rank": 8, "stats": []}
        pos, neg = pick(typ)
        if not pos:
            return None
        fix = rc._fix_of(p)
        for en, sign in [(e, "+") for e in pos] + ([(neg, "-")] if neg else []):
            s = {"en": en, "sign": sign, "unit": "%", "value": 0.0}
            rng = rc._range_of(s, p, fix)
            if not rng:
                continue
            s["value"] = round({"lo": rng[0], "mid": (rng[0] + rng[1]) / 2, "hi": rng[1]}[mode], 1)
            s["_rng"] = rng
            p["stats"].append(s)
        return p if p["stats"] else None

    def bad(x):
        return x is None or (isinstance(x, float) and (math.isnan(x) or math.isinf(x)))

    n_exc = n_range = n_inv = n_panel = n_mid100 = n_mono = 0
    n_dim = n_dim_n = n_dim_none = 0
    per_type = {}
    for weapon, meta in disp_tbl.items():
        typ = (meta or {}).get("type")
        if typ not in base_tbl:
            n_exc += 1
            continue
        got = {}
        for mode in ("mid", "hi", "lo"):
            try:
                p = card(weapon, typ, mode)
                if p is None:
                    continue
                w = rc._weapon_raw(weapon) or {}
                sim = rc._sim(p, w)
                sc = rc._scores(p, sim)
            except Exception:  # noqa: BLE001
                n_exc += 1
                continue
            got[mode] = sc
            for k in ("dmg", "mkt", "total"):
                v = sc.get(k)
                if bad(v) or not (0 <= v <= 100):
                    n_range += 1
                    break
            g, gm = sc.get("gain", 0.0), sc.get("gain_max", 0.0)
            if bad(g) or bad(gm) or gm * (1 + 1e-9) < g:
                n_inv += 1
            panel = bool(sc.get("panel", True))
            dpsb = float(sim.get("dps_base") or 0)
            if (panel and dpsb <= 0) or ((not panel) and dpsb > 0):
                n_panel += 1
            if mode == "mid" and sc["dmg"] >= 99.999:
                n_mid100 += 1
            # E6d 双维自洽：恒等式 dmg == min(潜力/calib, roll)（不是近似、不是"两者之积"）。
            # ceil_src=="cur"（当前增益本身超过同词条洗满，理论上仅 _rng 缺失时出现）
            # 时 dmg 被顶 100，不参与该恒等式。无面板武器先判 panel，不放进来。
            if not sc.get("panel", True):
                # E6e 无伤害面板 → pot/roll 必须为 None（不填 0 冒充）
                if sc.get("pot") is not None or sc.get("roll") is not None:
                    n_dim_none += 1
            elif sc.get("ceil_src") in ("calib", "cur_max"):
                _pot, _roll = sc.get("pot"), sc.get("roll")
                if _pot is None or _roll is None:
                    n_dim_none += 1
                else:
                    n_dim_n += 1
                    _cal = (rc._OPTIMAL_CALIB_MELEE if rc._is_melee(p, w)
                            else rc._OPTIMAL_CALIB_GUN)
                    if abs(min(_pot / _cal, _roll) - sc["dmg"]) > 0.05:
                        n_dim += 1
            if mode == "hi":
                per_type.setdefault(typ, set()).add(weapon)
        if len(got) == 2 and got["hi"]["dmg"] + 1e-6 < got["lo"]["dmg"]:
            n_mono += 1

    ok(n_exc == 0, "E2 全类型 _sim/_scores 零异常", "exc=%d" % n_exc)
    ok(n_range == 0, "E3 dmg/mkt/total 全部 ∈[0,100] 且非 NaN", "bad=%d" % n_range)
    ok(n_inv == 0, "E4 天花板不变式 gain_max ≥ gain 全部成立", "bad=%d" % n_inv)
    ok(n_panel == 0, "E5 panel 标志与 dps_base 全部一致（无面板武器诚实降级）", "bad=%d" % n_panel)
    ok(n_mid100 == 0, "E6 中位 roll 普通卡零「伤害分顶 100」", "mid100=%d" % n_mid100)
    # E6b（2026-11-12 新增，补 E6 的假健康）：E6 的造卡是 `sorted(_DPS_STAT_KEYS)[:3]`，
    # 即**字母序前三**（弹药上限/爆炸/冰冻一类弱词条），永远不会撞天花板 → 只能证明弱卡
    # 不顶 100，证明不了强卡。这里改用 `_theoretical_best_stats` 的词条类型 + 中位 roll
    # 造「强词条卡」，判据不是「不许顶 100」（强卡顶 100 语义正确），而是**顶 100 必须
    # 能被 ceil_src 解释**——静默抬升才算失败。
    n_strong = n_strong100 = n_strong_blind = 0
    for weapon, meta in disp_tbl.items():
        typ = (meta or {}).get("type")
        if typ not in base_tbl:
            continue
        w = rc._weapon_raw(weapon) or {}
        if not w or not (w.get("damage") or {}).get("total"):
            continue
        p0 = {"weapon": weapon, "type": typ, "rank": 8,
              "disp": float((meta or {}).get("disp") or 1.0), "stats": []}
        try:
            best = rc._theoretical_best_stats(p0, w)
            if not best:
                continue
            p = dict(p0, stats=[])
            fix = rc._fix_of(p)
            for s in best:
                ns = {"en": s["en"], "sign": s["sign"], "unit": "%", "value": s["value"]}
                ns["_rng"] = rc._range_of(ns, p, fix) or s.get("_rng")
                if ns["_rng"]:
                    ns["value"] = round((ns["_rng"][0] + ns["_rng"][1]) / 2, 1)
                p["stats"].append(ns)
            sc = rc._scores(p, rc._sim(p, w))
        except Exception:  # noqa: BLE001
            n_strong_blind += 1
            continue
        n_strong += 1
        if sc["dmg"] >= 99.999:
            n_strong100 += 1
            if sc.get("ceil_src") not in ("cur", "cur_max"):
                n_strong_blind += 1
    ok(n_strong > 500, "E6b 强词条卡扫描有效（>500 把武器跑到）", "n=%d" % n_strong)
    ok(n_strong_blind == 0, "E6c 强词条卡顶 100 时 ceil_src 必须可解释（无静默抬升）",
       "strong100=%d blind=%d" % (n_strong100, n_strong_blind))
    ok(n_dim_n > 1000 and n_dim == 0,
       "E6d 恒等式 dmg == min(潜力/calib, roll) 全量成立",
       "n=%d bad=%d" % (n_dim_n, n_dim))
    ok(n_dim_none == 0, "E6e 无面板武器 pot/roll 必须为 None（不填 0 冒充）",
       "bad=%d" % n_dim_none)
    ok(n_mono == 0, "E7 满 roll ≥ 低 roll（同武器评分单调）", "bad=%d" % n_mono)
    detail = " ".join("%s=%d" % (t, len(per_type.get(t, ()))) for t in types)
    ok(len(per_type) == 7, "E8 七类武器全部跑到（近战/主/副/自制/Archgun）", detail)


def section_g_neg_weight():
    """G 段：负词条扣分必须按**当前武器面板伤害占比**缩放（2026-11-12 用户实卡 Ocucor）。

    背景：旧实现 `(100-NEG_MARKET)*0.44` 是武器无关常数，把 `-111.4% 切割` 对
    Ocucor（slash 1/11 = 9.09%）判成重罚 32.56 分 → 市场 29.57(D)。而 Warframe Robot
    对同卡标「切割 +24.9P / +7%」（免费负面）。修后市场 59.17(B)。
    """
    print("== G. 负词条武器相关性：扣分按面板伤害占比缩放 ==")
    rc = _load_card()
    ocu = {"weapon": "Ocucor", "weapon_zh": "视使之触", "type": "Pistol",
           "disp": 1.2, "rank": 8, "riven_name": "Igni-carican",
           "stats": [
               {"en": "Heat Damage", "zh": "火焰伤害", "sign": "+", "unit": "%", "value": 110.7},
               {"en": "Viral Damage", "zh": "病毒伤害", "sign": "+", "unit": "%", "value": 104.2},
               {"en": "Multishot", "zh": "多重射击", "sign": "+", "unit": "%", "value": 132.7},
               {"en": "Slash Damage", "zh": "切割伤害", "sign": "-", "unit": "%", "value": 111.4},
           ]}

    w = rc._weapon_raw("Ocucor")
    ok(w is not None, "G0 Ocucor 面板可加载")
    dmg = (w or {}).get("damage") or {}
    share = float(dmg.get("slash") or 0) / float(dmg.get("total") or 1)

    g1 = rc._neg_weight(ocu, ocu["stats"][3])
    ok(abs(g1 - share) < 1e-9, "G1 -切割 权重 = 面板 slash/total = 9.09%",
       "w=%.4f share=%.4f" % (g1, share))

    # G2 有面板但该项为 0：Ocucor 面板无病毒 → -病毒 视为免费负面
    neg_viral = {"en": "Viral Damage", "zh": "病毒伤害", "sign": "-", "unit": "%", "value": 100.0}
    ok(rc._neg_weight(ocu, neg_viral) == 0.0, "G2 面板无该元素（-病毒 对 Ocucor）→ 0.0")

    # G3 无面板武器：不知道构成 ≠ 无影响，必须保守沿用原常数
    zaw = dict(ocu); zaw["weapon"] = "Zaw"; zaw["type"] = "Zaw"
    ok(rc._neg_weight(zaw, ocu["stats"][3]) == 1.0, "G3 无面板武器（Zaw）→ 1.0 保守")
    ghost = dict(ocu); ghost["weapon"] = "NotARealWeaponXYZ"
    ok(rc._neg_weight(ghost, ocu["stats"][3]) == 1.0, "G3b 未收录武器 → 1.0 保守")

    # G4 非伤害类词条与伤害构成无关，维持原常数
    rate = {"en": "Fire Rate", "zh": "射速", "sign": "-", "unit": "%", "value": 50.0}
    ok(rc._neg_weight(ocu, rate) == 1.0, "G4 非伤害类（-射速）→ 1.0 不变")

    # G5 端到端：市场分落点
    fix = rc._fix_of(ocu)
    for s in ocu["stats"]:
        s["_rng"] = rc._range_of(s, ocu, fix)
    sim = rc._sim(ocu, w)
    sc = rc._scores(ocu, sim)
    ok(abs(sc["mkt"] - 59.17) <= 0.6, "G5 Ocucor 市场分 ≈59.2（修前 29.6）",
       "mkt=%.2f" % sc["mkt"])
    ok(rc._letter(sc["mkt"]) == "B", "G5b 市场字母 B（修前 D）",
       rc._letter(sc["mkt"]))
    # G6 伤害分：本轮补齐 6 个复合元素后 Ocucor 71.8 → 91.0（病毒+104.2 不再白算）。
    # 分母已走 cur_max 分支（137.2 / 150.7），故该分反映**roll 完成度**而非武器潜力占比，
    # `ceil_src` 必须如实标识，展示层据此加「刻度压平」提示 —— 不许再静默。
    ok(abs(sc["dmg"] - 90.36) <= 0.6, "G6 Ocucor 伤害分 ≈90.4（补复合元素后；第30轮槽位修复 91.03→90.36）",
       "dmg=%.2f" % sc["dmg"])
    ok(sc.get("ceil_src") == "cur_max", "G6b 分母口径如实暴露 ceil_src=cur_max",
       str(sc.get("ceil_src")))

    # G6c 反证（2026-11-13 改为**机制断言**，不再锚死某个桶下的分数）：只把 `_ELEM_KEYS`
    # 退回 4 基础元素，病毒词条贡献必须被静默打成 0，且当前增益必须掉 ≥30pp。
    # 旧写法锚死「dmg 必须复现 71.8」——那个数会随配卡桶变（第30轮换真实桶后变成 91.1），
    # 判据自己会失效；改成断言**因果链**，桶怎么改都仍然有判别力。
    _ck = rc._ELEM_KEYS
    try:
        rc._ELEM_KEYS = _ck[:4]
        sim_old = rc._sim(ocu, w)
    finally:
        rc._ELEM_KEYS = _ck
    ok(abs(sim_old["contrib"].get("Viral Damage+", -1.0)) < 1e-9,
       "G6c 反证：_ELEM_KEYS 退回 4 元素时病毒词条贡献必须为 0（判据有效）",
       "contrib=%.3f" % sim_old["contrib"].get("Viral Damage+", -1.0))
    ok((sim["gain"] - sim_old["gain"]) * 100 >= 30.0,
       "G6c2 复合元素词条为当前增益贡献 ≥30pp（禁止再被静默丢弃）",
       "正常%.1f%% vs 退回%.1f%%" % (sim["gain"] * 100, sim_old["gain"] * 100))

    # G7 反证：把 _neg_weight 打回恒 1.0（旧行为），必须复现 29.57
    orig = rc._neg_weight
    try:
        rc._neg_weight = lambda parsed, s: 1.0
        old = rc._scores(ocu, sim)
    finally:
        rc._neg_weight = orig
    ok(abs(old["mkt"] - 29.57) <= 0.6, "G7 反证：旧常数口径复现 29.57（判据有效）",
       "old_mkt=%.2f" % old["mkt"])


def _mkt_bundle(card_neg):
    """最小可用 market bundle（字段与 fetch_market_bundle 成功态同构）。"""
    return {
        "status": "ok", "slug": "ocucor", "ts": "2026-11-12 21:00",
        "source": "WM 挂牌要价·非成交价｜同等级 rank8｜同分位 k=20｜匹配：正负面相同",
        "price_kind": "挂牌要价·非成交价", "rank": 8,
        "match_tier": "正负面相同", "pair_k": 20,
        "n": 20, "lo": 340.0, "hi": 375.0, "med": 354.5, "zero": 280.0,
        "sample_n": 291, "base": 354.5,
        "rec_pos": ["多重", "暴伤", "病毒"],
        "rec_neg": {"slug": "zoom", "zh": "变焦", "n": 20, "pool_n": 79,
                    "median": 500.0, "delta": 145.5, "pct": 41.0, "hot_n": 23},
        "card_neg": card_neg,
        "exact": {"status": "ok", "n": 3, "median": 1288.0, "reason": ""},
        "desc_price_used": False,
    }


def section_h_card_neg():
    """H 段：市场负面参考必须取**卡面实际负面**，而非「推荐洗卡负面」。

    用户 2026-11-12 对照 Warframe Robot：Robot 显示「切割 +24.9p/+7%」（卡上这条），
    旧实现显示「变焦 +141p/+39%」（推荐负面）—— 语义不同，用户对不上自己的卡。
    该逻辑必须**从 parsed 动态取**（全 623 把武器同一套），不得按武器硬编码。
    """
    print("== H. 市场负面参考取卡面实际负面（全武器通用，零硬编码）==")
    rc = _load_card()
    ocu = {"weapon": "Ocucor", "weapon_zh": "视使之触", "type": "Pistol",
           "disp": 1.2, "rank": 8, "mr": 8, "riven_name": "Igni-carican",
           "stats": [
               {"en": "Heat Damage", "zh": "火焰伤害", "sign": "+", "unit": "%", "value": 110.7},
               {"en": "Viral Damage", "zh": "病毒伤害", "sign": "+", "unit": "%", "value": 104.2},
               {"en": "Multishot", "zh": "多重射击", "sign": "+", "unit": "%", "value": 132.7},
               {"en": "Slash Damage", "zh": "切割伤害", "sign": "-", "unit": "%", "value": 111.4},
           ]}
    fix = rc._fix_of(ocu)
    for s in ocu["stats"]:
        s["_rng"] = rc._range_of(s, ocu, fix)

    # H1 有 card_neg → 用卡面负面
    cn = {"slug": "slash_damage", "zh": "切割", "stat": "Slash Damage",
          "n": 20, "median": 500.0, "delta": 145.5, "pct": 41.0}
    cd = rc.build_card_data(ocu, _mkt_bundle(cn))
    neg = str((cd.get("recs") or {}).get("neg") or "")
    ok("切割" in neg and "卡面负面" in neg, "H1 有 card_neg → 展示卡面负面(切割)", neg[:46])

    # H2 无 card_neg（词条未收录 WFM / 样本不足）→ 退回推荐负面并**标明来源**
    cd2 = rc.build_card_data(ocu, _mkt_bundle(None))
    neg2 = str((cd2.get("recs") or {}).get("neg") or "")
    ok("变焦" in neg2 and "推荐负面" in neg2, "H2 无 card_neg → 退回推荐负面并标明", neg2[:46])

    # H3 通用性：负词条从 parsed 动态取（不是写死 Slash Damage）
    ocu2 = dict(ocu)
    ocu2["stats"] = [
        {"en": "Heat Damage", "zh": "火焰伤害", "sign": "+", "unit": "%", "value": 110.7},
        {"en": "Multishot", "zh": "多重射击", "sign": "+", "unit": "%", "value": 132.7},
        {"en": "Zoom", "zh": "变焦", "sign": "-", "unit": "%", "value": 50.0},
    ]
    for s in ocu2["stats"]:
        s["_rng"] = rc._range_of(s, ocu2, rc._fix_of(ocu2))
    cn2 = {"slug": "zoom", "zh": "变焦", "stat": "Zoom",
           "n": 20, "median": 400.0, "delta": 40.0, "pct": 12.0}
    cd3 = rc.build_card_data(ocu2, _mkt_bundle(cn2))
    neg3 = str((cd3.get("recs") or {}).get("neg") or "")
    ok("变焦" in neg3 and "卡面负面" in neg3,
       "H3 换一条负面（变焦）同样生效（非写死切割）", neg3[:46])

    # H4 反证：源码必须是 card_neg 优先，否则旧行为回流
    src = _CARD.read_text(encoding="utf-8")
    ok('market.get("card_neg") or market["rec_neg"]' in src,
       "H4 源码断言 card_neg 优先（反证旧行为回流）")

    # H5 百分比护栏：极低价武器（Zaw/Kitgun/Archgun 实测 +233%~+900%）不许出失真百分比
    cn_big = {"slug": "slash_damage", "zh": "切割", "stat": "Slash Damage",
              "n": 3, "median": 135.0, "delta": 135.0, "pct": 900.0}
    cd5 = rc.build_card_data(ocu, _mkt_bundle(cn_big))
    neg5 = str((cd5.get("recs") or {}).get("neg") or "")
    ok("900" not in neg5 and "仅参考方向" in neg5,
       "H5 |pct|>100 → 不出失真百分比，只给方向", neg5[:46])
    # H5b 正常区间仍出百分比（不能一刀切砍掉）
    ok("%" in neg and "仅参考方向" not in neg,
       "H5b 正常 pct 仍输出百分比", neg[:40])


def _load_card_pkg():
    """两级包加载（qqb.game_tools）：riven_trend 有 `from ..config import`，
    单层 gt 包会让 `..` 越界 → 静默走兜底 → Prime 拼成 xxx_prime（假阳性）。
    仅 slug 相关段用，避免影响既有段。"""
    _stub_nonebot_httpx()
    PB = _CARD.parent.parent
    qqb = types.ModuleType("qqb")
    qqb.__path__ = [str(PB)]
    sys.modules["qqb"] = qqb
    gt = types.ModuleType("qqb.game_tools")
    gt.__path__ = [str(_CARD.parent)]
    sys.modules["qqb.game_tools"] = gt
    return importlib.import_module("qqb.game_tools.riven_card")


#: 用户实卡 2026-11-12「冰凇 Verglas」：三正（毒素/伤害/异常状态伤害）+ 一负（-135.3% 暴击几率）。
_VERGLAS_STATS = [
    {"en": "Toxin Damage", "zh": "毒素伤害", "value": 91.5, "unit": "%", "sign": "+"},
    {"en": "Damage", "zh": "基础伤害", "value": 179.8, "unit": "%", "sign": "+"},
    {"en": "Status Damage", "zh": "异常状态伤害", "value": 111.3, "unit": "%", "sign": "+"},
    {"en": "Critical Chance", "zh": "暴击几率", "value": 135.3, "unit": "%", "sign": "-"},
]


def section_k_neg_crit_weight():
    """K 段：暴击类负词条必须按**该武器暴击贡献占比**定权（2026-11-12 用户实卡 Verglas）。

    背景：`_neg_weight` 原先只覆盖伤害类词条，`-135.3% 暴击几率` 按武器无关常数满额扣
    ``(100-8)*0.44 = 40.48`` 分；而 Verglas 基础暴击仅 8%，std 配卡口径下暴击只占 DPS
    **7.41%**，实战近乎免费负面。修前「加权 roll 完成度 37.63 − 40.48 < 0」被 clamp 成
    市场 **0.0**，把一张三正词条卡判成零价值；修后 34.63。
    """
    rc = _load_card()
    ok(set(rc._NEG_CRIT_STAT) == {"Critical Chance", "Critical Damage"},
       "K0 暴击负词条集合恰为基础两项（滑砍暴击不入，保 Coda 金标准）",
       str(sorted(rc._NEG_CRIT_STAT)))

    wv = rc._weapon_raw("Verglas") or {}
    pv = {"weapon": "Verglas", "type": "Rifle", "disp": 1.2, "rank": 8,
          "stats": [dict(s) for s in _VERGLAS_STATS]}
    fix = rc._fix_of(pv)
    for s in pv["stats"]:
        s["_rng"] = rc._range_of(s, pv, fix)
    share_v = rc._crit_share(pv, wv)
    ok(abs(share_v - 0.0741) <= 0.001, "K1 Verglas 暴击贡献占比 ≈0.0741（8% 暴击武器）",
       "%.4f" % share_v)

    neg_cc = pv["stats"][3]
    ok(abs(rc._neg_weight(pv, neg_cc) - share_v) < 1e-9,
       "K2 -暴击几率 权重 = 暴击贡献占比（不再满额）",
       "%.4f" % rc._neg_weight(pv, neg_cc))

    sc = rc._scores(pv, rc._sim(pv, wv))
    ok(abs(sc["mkt"] - 34.63) <= 0.8, "K3 Verglas 市场分 ≈34.6（修前 0.0）",
       "mkt=%.2f" % sc["mkt"])

    # K4 反证：把 _crit_share 打回 1.0（旧满额常数）必须复现 0.0 —— 证明差额只来自这一项
    orig = rc._crit_share
    try:
        rc._crit_share = lambda parsed, w: 1.0
        sc_old = rc._scores(pv, rc._sim(pv, wv))
    finally:
        rc._crit_share = orig
    ok(abs(sc_old["mkt"]) <= 0.01, "K4 反证：旧满额常数必复现 mkt=0.0（判据有效）",
       "old=%.2f" % sc_old["mkt"])

    # K5 高暴击武器上 -暴击仍应重罚（否则就是把「武器相关」做成了「一律免罚」）
    wh = rc._weapon_raw("Coda Hirudo") or {}
    share_h = rc._crit_share({"weapon": "Coda Hirudo", "type": "Melee", "disp": 1.2,
                              "rank": 8, "stats": []}, wh)
    ok(share_h > 0.95, "K5 高暴击武器 Coda Hirudo 占比 >0.95（-暴击仍重罚）",
       "%.4f" % share_h)

    # K6 滑砍暴击不在集合内 → 权重仍 1.0，Coda 参考卡 -109.3% 滑砍暴击口径不动
    slide = {"en": "Critical Chance for Slide Attack", "value": 109.3, "unit": "%", "sign": "-"}
    ok(rc._neg_weight({"weapon": "Coda Motovore", "type": "Melee", "disp": 1.2,
                       "rank": 8, "stats": []}, slide) == 1.0,
       "K6 滑砍暴击权重仍 1.0（不波及 Coda 金标准）")

    # K7 完全不吃暴击（cc=0）→ 占比 0，-暴击是免费负面
    wz = dict(wv)
    wz["criticalChance"] = 0.0
    ok(rc._crit_share({"weapon": "Verglas", "type": "Rifle", "disp": 1.2, "rank": 8,
                       "stats": []}, wz) == 0.0, "K7 cc=0 武器占比 0（-暴击免费）")

    # K8 未建模词条不许显示 +0.0%（读起来像「没用」）：异常状态伤害 必须标「未计入」
    txt = rc.build_card(pv, _mkt_bundle(None))
    lines = {ln.split("|")[1]: ln for ln in txt.splitlines() if ln.startswith("@chipline|")}
    ok("—(未计入)" in lines.get("异常状态伤害", ""),
       "K8 异常状态伤害 标注「—(未计入)」（模型无 DoT 乘区）",
       lines.get("异常状态伤害", "<缺行>"))
    ok("+" in lines.get("基础伤害", "") and "未计入" not in lines.get("基础伤害", ""),
       "K9 已建模词条仍给百分比（不误标）", lines.get("基础伤害", "<缺行>"))
    ok("不代表它没有价值" in txt, "K10 脚注说明「未计入」语义（不静默）")

    # K11 防漂移：`_DPS_STAT_KEYS` 必须与 `_apply_riven` 真正认识的键**完全一致**。
    # 否则「标了百分比但折算为 0」或「折算有值却标未计入」会再次出现（本轮两个 bug 的根）。
    std = rc._std_bucket(pv, wv)
    unrecognized = []
    for en in sorted(rc._DPS_STAT_KEYS):
        b = rc._clone_bucket(std)
        rc._apply_riven(b, [{"en": en, "value": 50.0, "unit": "%", "sign": "+", "_rng": None}])
        if b == std:
            unrecognized.append(en)
    ok(not unrecognized, "K11 _DPS_STAT_KEYS 与 _apply_riven 认识集合一致（无漂移）",
       ",".join(unrecognized))


def section_i_weapon_slug():
    """I 段：WFM 紫卡 slug 解析必须对全部武器通用（Prime 去后缀 / & 转 and）。

    背景（2026-11-12）：
      * Prime 变体 WFM 只挂**基础族名**（Strun Prime→strun），但有 19 个例外
        自带独立 slug（Dakra Prime→dakra_prime），不能一律砍后缀；
      * 含 `&` 的武器（Ack & Brunt / Cobra & Crane / Silva & Aegis …）旧实现把
        `&` 连空格压成一个 `_` → ack_brunt 恒查不到 → resolve_weapon 返回 None →
        `_weapon_slug` 直接抛异常，整把武器的市场数据拿不到。
    """
    print("== I. WFM slug 解析全武器通用（Prime 去后缀 / & 转 and）==")
    rc = _load_card_pkg()

    def slug(w):
        try:
            return rc._weapon_slug({"weapon": w})
        except Exception as e:  # noqa: BLE001
            return "EXC:" + type(e).__name__

    ok(slug("Strun Prime") == "strun", "I1 Prime 去后缀 → strun", slug("Strun Prime"))
    ok(slug("Rubico Prime") == "rubico", "I1b Prime 去后缀 → rubico", slug("Rubico Prime"))
    ok(slug("Braton Prime") == "braton", "I1c Prime 去后缀 → braton", slug("Braton Prime"))

    # I2 自带独立 slug 的 Prime 必须保留（表内 19 条，不能一律砍后缀）
    ok(slug("Dakra Prime") == "dakra_prime", "I2 独立 slug 的 Prime 保留 dakra_prime",
       slug("Dakra Prime"))

    # I3 含 & 的武器：& → and（表内 10 条）
    for w, want in (("Ack & Brunt", "ack_and_brunt"),
                    ("Cobra & Crane Prime", "cobra_and_crane"),
                    ("Silva & Aegis", "silva_and_aegis"),
                    ("Sun & Moon", "sun_and_moon"),
                    ("Sigma & Octantis", "sigma_and_octantis")):
        got = slug(w)
        ok(got == want, "I3 & 转 and: %s" % w, got)

    # I4 中文名同样能解析（卡面可能是中文武器名）
    got_zh = slug("视使之触")
    ok(got_zh == "ocucor", "I4 中文武器名 → ocucor", got_zh)

    # I5 反证：& 未转 and 时必须失败（旧行为），证明 I3 判据有效
    src = (_CARD.parent / "riven_trend.py").read_text(encoding="utf-8")
    ok('.replace("&", " and ")' in src, "I5 源码断言 &→and 归一化存在（反证旧行为）")


def section_j_429():
    """J 段：429 限流必须给明确原因与等待时间，不许把 NoneType 当结果抛给用户。

    实测（2026-11-12）：连续几十请求 → Cloudflare 429 / Error 1015 / retry-after 43s，
    而 ``warframe._fetch`` 失败统一返回 None → 卡面只报「返回 NoneType（不是 JSON 对象）」。
    """
    print("== J. 429 限流显式化（retry-after 透传 + 重试上限）==")
    import asyncio as _aio
    rc = _load_card_pkg()

    orig_fetch = rc.warframe._fetch
    orig_probe = rc._probe_429
    orig_interval = rc._WFM_MIN_INTERVAL
    rc._WFM_MIN_INTERVAL = 0.0        # 免去测试里的节流等待

    async def _none(*a, **k):
        return None

    try:
        # J1 retry-after 超过上限 → 不傻等，抛带秒数的明确错误
        rc.warframe._fetch = _none

        async def _probe_43(url):
            return 43.0
        rc._probe_429 = _probe_43
        msg = ""
        try:
            _aio.run(rc._fetch_auction_page("jtest1", "price_asc"))
        except Exception as e:  # noqa: BLE001
            msg = str(e)
        ok("429" in msg and "retry-after" in msg and "43" in msg,
           "J1 429 → 报明确限流与 retry-after 秒数", msg[:52])
        ok("NoneType" not in msg, "J1b 不再是 NoneType 天书")

        # J2 非 429 的失败 → 保持原有 NoneType 语义（不把所有失败都说成限流）
        async def _probe_none(url):
            return None
        rc._probe_429 = _probe_none
        msg2 = ""
        try:
            _aio.run(rc._fetch_auction_page("jtest2", "price_asc"))
        except Exception as e:  # noqa: BLE001
            msg2 = str(e)
        ok("NoneType" in msg2 and "429" not in msg2,
           "J2 非限流失败仍报原语义（不误判）", msg2[:46])

        # J3 限流后重试成功 → 返回真实数据（不是直接失败）
        calls = {"n": 0}

        async def _fetch_ok(*a, **k):
            calls["n"] += 1
            if calls["n"] == 1:
                return None
            return {"payload": {"auctions": [{"id": "j3", "buyout_price": 123}]}}

        async def _probe_1(url):
            return 1.0
        rc.warframe._fetch = _fetch_ok
        rc._probe_429 = _probe_1
        got = _aio.run(rc._fetch_auction_page("jtest3", "price_asc"))
        ok(len(got) == 1 and calls["n"] == 2, "J3 限流后重试一次并成功", "calls=%d" % calls["n"])

        # J4 重试次数必须有上限（防把用户等穿）
        src = _CARD.read_text(encoding="utf-8")
        ok("_WFM_429_RETRY = 1" in src and "_WFM_429_MAX_WAIT" in src,
           "J4 重试次数与单次等待均有上限")
    finally:
        rc.warframe._fetch = orig_fetch
        rc._probe_429 = orig_probe
        rc._WFM_MIN_INTERVAL = orig_interval


if __name__ == "__main__":
    print("== 模块D. 紫卡评分全武器泛化 + 两张 Rabbit 金标准守门 ==")
    section_a_static(); print()
    section_b_gun(); print()
    section_c_melee(); print()
    section_d_injection(); print()
    section_f_pollution(); print()
    section_g_neg_weight(); print()
    section_k_neg_crit_weight(); print()
    section_h_card_neg(); print()
    section_i_weapon_slug(); print()
    section_j_429(); print()
    section_e_all_weapons()
    print("\n%d PASS / %d FAIL" % (_P, _F))
    sys.exit(1 if _F else 0)
