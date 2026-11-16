# -*- coding: utf-8 -*-
"""守门：市场分「词条类型」必须参与——垃圾组合洗满不许拿 100 分。

=== 第30轮修的根因 ===
市场分 = `Σ(w×p)/Σw × 100 − 负面罚`，而 p→1 时该式**恒等于 100**：词条类型在满 roll
时完全不参与。构造反证实测（改前）：
    变焦 + 弹药最大值 + 武器后坐力（负弹速）全洗满 → mkt = **100.00**
    基础伤害 + 多重射击 + 暴击伤害（负变焦）全洗满 → mkt = **100.00**
两者一模一样。真实市场里垃圾组合只值零洗价（WFM 实测 rank0 中位 480P vs 正确词条
rank8 中位 5000–10000P），100 分是错的。

修法：以「该武器可洗 n 词条组合的 Σw 中位数」为**类型地板**，低于地板按比例折价；
中位以上一律 1.0 —— 因此阿利乌 85.7 / Coda 62.3 / Ocucor 59.2 / 冰凇 34.6 全部不动。

判据：
  W1 垃圾组合洗满必须显著低于 100（<25），且远低于神组合
  W2 神组合洗满仍 =100（不许把真神卡压下来）
  W3 四张 Rabbit 金标准卡的市场分与改前逐位一致（地板对它们 = 1.0）
  W4 同 p 下 Σw 越低分越低（地板以下严格单调，六档至少 5 个不同分值）
  W5 反证：地板打回 0（旧行为）→ 垃圾卡必回到 100（判据有判别力）
  W6 阵营伤：地板池与分子**同权重空间**（排除集子集方向 + 废卡不许借 96 权重顶分）
  W7 全量 623 把 mkt ∈[0,100] 且非 NaN
  W8 7 类零特例：六种阵营伤写法权重恒 0，正常词条恒正
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
        print("PASS %-56s %s" % (name, detail))
    else:
        _F += 1
        print("FAIL %-56s %s" % (name, detail))


def _stub_nonebot_httpx():
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


def _card(rc, weapon, rtype, zh, stats):
    """stats = [(en, zh, sign, value)]"""
    p = {"weapon": weapon, "weapon_zh": zh, "type": rtype, "disp": 1.2, "rank": 8,
         "stats": [{"en": e, "zh": z, "sign": sg, "unit": "%", "value": v}
                   for e, z, sg, v in stats]}
    fix = rc._fix_of(p)
    for s in p["stats"]:
        s["_rng"] = rc._range_of(s, p, fix)
    return p


def _run(rc, p):
    w = rc._weapon_raw(p["weapon"]) or {}
    sim = rc._sim(p, w)
    return sim, rc._scores(p, sim)


def _full_roll(rc, p, deep=True):
    """把每条词条洗到区间端点（正取上限、负取最深）。"""
    for s in p["stats"]:
        s["value"] = s["_rng"][1] - 1e-9
    return p


JUNK = [("Zoom", "变焦", "+", 0), ("Ammo Maximum", "弹药最大值", "+", 0),
        ("Weapon Recoil", "武器后坐力", "+", 0), ("Projectile Speed", "弹道速度", "-", 0)]
GOD = [("Damage", "基础伤害", "+", 0), ("Multishot", "多重射击", "+", 0),
       ("Critical Damage", "暴击伤害", "+", 0), ("Zoom", "变焦", "-", 0)]


def section_w1_w2():
    print("== W1/W2. 垃圾组合不许拿 100；神组合仍 =100 ==")
    rc = _load_card()
    pj = _full_roll(rc, _card(rc, "Verglas", "Rifle", "冰凇", JUNK))
    pg = _full_roll(rc, _card(rc, "Verglas", "Rifle", "冰凇", GOD))
    _, sj = _run(rc, pj)
    _, sg = _run(rc, pg)
    ok(sj["mkt"] < 25.0,
       "W1 垃圾组合（变焦/弹药/后坐 + 负弹速）洗满 <25 分", "mkt=%.2f" % sj["mkt"])
    ok(sj["mkt"] < sg["mkt"] - 50.0,
       "W1b 垃圾与神组合必须拉得开（差距 >50 分）",
       "垃圾%.1f vs 神%.1f" % (sj["mkt"], sg["mkt"]))
    ok(abs(sg["mkt"] - 100.0) < 1e-6, "W2 神组合洗满仍 =100", "mkt=%.2f" % sg["mkt"])


def section_w3_goldens():
    print("\n== W3. 四张 Rabbit 金标准卡的市场分不得被地板改动 ==")
    rc = _load_card()
    cards = {
        "阿利乌": ("Akarius", "Pistol", "阿利乌双枪",
                 [("Ammo Efficiency", "弹药效率", "+", 68.1), ("Damage", "基础伤害", "+", 355.3),
                  ("Weapon Recoil", "武器后坐力", "+", 58.1)], 85.65),
        "Coda": ("Coda Motovore", "Melee", "Coda 电吉他",
                 [("Critical Chance", "暴击几率", "+", 222.0), ("Impact Damage", "冲击伤害", "+", 146.4),
                  ("Heavy Attack Efficiency", "重击效率", "+", 90.4),
                  ("Critical Chance for Slide Attack", "滑砍暴击几率", "-", 109.3)], 62.31),
        "Ocucor": ("Ocucor", "Pistol", "视使之触",
                   [("Heat Damage", "火焰伤害", "+", 110.7), ("Viral Damage", "病毒伤害", "+", 104.2),
                    ("Multishot", "多重射击", "+", 132.7), ("Slash Damage", "切割伤害", "-", 111.4)], 59.17),
        "Verglas": ("Verglas", "Rifle", "冰凇",
                    [("Toxin Damage", "毒素伤害", "+", 91.5), ("Damage", "基础伤害", "+", 179.8),
                     ("Status Damage", "异常状态伤害", "+", 111.3),
                     ("Critical Chance", "暴击几率", "-", 135.3)], 34.63),
    }
    for name, (wn, rt, zh, st, want) in cards.items():
        p = _card(rc, wn, rt, zh, st)
        _, sc = _run(rc, p)
        _, floor = None, rc._market_type_floor(p, sum(1 for s in p["stats"]
                                                      if not rc._layout_curse(s)))
        tf = min(1.0, sum(float(rc.STAT_MARKET.get(s["en"], 30))
                          for s in p["stats"] if not rc._layout_curse(s)) / floor)
        ok(abs(sc["mkt"] - want) <= 0.05 and abs(tf - 1.0) < 1e-9,
           "W3 %s 市场分 %.2f 不变（type_factor=%.3f）" % (name, want, tf),
           "got=%.2f tf=%.3f" % (sc["mkt"], tf))


def section_w4_monotone():
    print("\n== W4. 同 roll 下，词条组合越弱分越低（跨地板五档） ==")
    rc = _load_card()
    combos = [
        ("垃圾", [("Zoom", "变焦", "+", 0), ("Ammo Maximum", "弹药最大值", "+", 0),
                  ("Weapon Recoil", "武器后坐力", "+", 0)]),
        ("次弱", [("Zoom", "变焦", "+", 0), ("Ammo Maximum", "弹药最大值", "+", 0),
                  ("Reload Speed", "装填速度", "+", 0)]),
        ("中下", [("Magazine Capacity", "弹匣容量", "+", 0), ("Reload Speed", "装填速度", "+", 0),
                  ("Punch Through", "穿透", "+", 0)]),
        ("临界", [("Magazine Capacity", "弹匣容量", "+", 0), ("Reload Speed", "装填速度", "+", 0),
                  ("Heat Damage", "火焰伤害", "+", 0)]),
        ("中上", [("Damage", "基础伤害", "+", 0), ("Heat Damage", "火焰伤害", "+", 0),
                  ("Magazine Capacity", "弹匣容量", "+", 0)]),
        ("顶级", [("Damage", "基础伤害", "+", 0), ("Multishot", "多重射击", "+", 0),
                  ("Critical Damage", "暴击伤害", "+", 0)]),
    ]
    vals = []
    for name, st in combos:
        p = _full_roll(rc, _card(rc, "Verglas", "Rifle", "冰凇", st))
        _, sc = _run(rc, p)
        vals.append((name, sc["mkt"]))
    # 地板以下四档必须**严格**递增（地板上是 min(1.0, …) 截断，允许并列 100）
    below = [v for _, v in vals[:4]]
    ok(all(below[i] < below[i + 1] for i in range(len(below) - 1)),
       "W4 地板以下四档严格单调递增（垃圾<次弱<中下<临界）",
       " < ".join("%.1f" % v for v in below))
    ok(len({round(v, 2) for _, v in vals}) >= 5,
       "W4b 六档里至少 5 个互不相同的分值（不是阶跃退化）",
       " ".join("%s%.1f" % (n, v) for n, v in vals))
    ok(vals[-1][1] - vals[0][1] > 50.0,
       "W4c 顶级与垃圾洗满后差距 >50 分",
       "顶级%.1f - 垃圾%.1f = %.1f" % (vals[-1][1], vals[0][1], vals[-1][1] - vals[0][1]))
    ok(abs(vals[-1][1] - 100.0) < 1e-6, "W4d 顶级洗满仍 =100",
       "mkt=%.2f" % vals[-1][1])


def section_w5_anti():
    print("\n== W5. 反证：地板打回 0（旧行为）→ 垃圾卡必回到 100 ==")
    rc = _load_card()
    p = _full_roll(rc, _card(rc, "Verglas", "Rifle", "冰凇", JUNK))
    orig = rc._market_type_floor
    try:
        rc._market_type_floor = lambda *a, **k: 0.0
        rc._MKT_FLOOR_CACHE.clear()
        _, old = _run(rc, p)
    finally:
        rc._market_type_floor = orig
        rc._MKT_FLOOR_CACHE.clear()
    ok(abs(old["mkt"] - 100.0) < 1e-6,
       "W5 无地板时垃圾组合复现 100.00（证明地板是唯一差异）", "old=%.2f" % old["mkt"])


def section_w6_exclude():
    print("\n== W6. 阵营伤：地板池与分子必须同权重空间 ==")
    rc = _load_card()
    _, base_tbl, _ = rc._load_riven_tables()
    FACTION = ("Grineer", "Corpus", "Infested")
    # --- W6a：全 7 类，真表里的阵营伤词条必须**全部**落在排除集内（子集方向，不是相等）---
    bad, per_type = [], []
    for rt in ("Archgun", "Zaw", "Kitgun", "Pistol", "Rifle", "Shotgun", "Melee"):
        keys = set(base_tbl.get(rt) or {})
        fac = {k for k in keys if any(s in k for s in FACTION)}
        leaked = fac - set(rc._MKT_FLOOR_EXCLUDE)
        per_type.append("%s:%d" % (rt, len(fac)))
        if leaked:
            bad.append("%s 泄漏 %s" % (rt, sorted(leaked)))
        if not fac:
            bad.append("%s 真表里没扫到阵营伤（判据失效）" % rt)
    ok(not bad, "W6a 全 7 类阵营伤词条（各 3 项）全部被地板排除（子集方向）",
       " ".join(per_type) if not bad else "; ".join(bad))

    # --- W6b：全 7 类地板单调（正词条越多地板越高）且落在合理区间 ---
    bad2, per_floor = [], []
    for rt in ("Archgun", "Zaw", "Kitgun", "Pistol", "Rifle", "Shotgun", "Melee"):
        fs = [rc._market_type_floor({"type": rt}, n) for n in (1, 2, 3, 4)]
        per_floor.append("%s[%s]" % (rt, ",".join("%.0f" % f for f in fs)))
        if not all(fs[i] < fs[i + 1] for i in range(3)):
            bad2.append("%s 非单调 %s" % (rt, fs))
        if not (60.0 < fs[2] < 200.0):
            bad2.append("%s 3 正地板 %.1f 越界" % (rt, fs[2]))
    ok(not bad2, "W6b 全 7 类地板随正词条数单调递增且 3 正档 ∈(60,200)",
       " ".join(per_floor) if not bad2 else "; ".join(bad2))

    # --- W6c：反证——把排除集清空，地板必被抬高（证明排除真的在起作用）---
    orig_ex = rc._MKT_FLOOR_EXCLUDE
    try:
        rc._MKT_FLOOR_EXCLUDE = frozenset()
        rc._MKT_FLOOR_CACHE.clear()
        f_raw = rc._market_type_floor({"type": "Rifle"}, 3)
    finally:
        rc._MKT_FLOOR_EXCLUDE = orig_ex
        rc._MKT_FLOOR_CACHE.clear()
    f_pure = rc._market_type_floor({"type": "Rifle"}, 3)
    ok(f_raw > f_pure + 1e-9,
       "W6c 反证：不排除阵营伤时地板被抬高（96 权重压在顶格）",
       "含阵营伤%.0f > 纯组合%.0f" % (f_raw, f_pure))

    # --- W6d：口径统一——废卡不许借 96 权重顶到高分 ---
    p = _full_roll(rc, _card(rc, "Verglas", "Rifle", "冰凇", [
        ("Damage to Grineer", "对Grineer伤害", "+", 0), ("Zoom", "变焦", "+", 0),
        ("Ammo Maximum", "弹药最大值", "+", 0)]))
    _, sc = _run(rc, p)
    ok(sc["mkt"] < 25.0,
       "W6d 阵营伤+两个垃圾洗满 <25（不许再拿 95.65）", "mkt=%.2f" % sc["mkt"])
    orig_w = rc._mkt_wstat
    try:
        rc._mkt_wstat = lambda en: float(rc.STAT_MARKET.get(en, 30))
        rc._MKT_FLOOR_CACHE.clear()
        _, old = _run(rc, p)
    finally:
        rc._mkt_wstat = orig_w
        rc._MKT_FLOOR_CACHE.clear()
    ok(abs(old["mkt"] - 95.65) <= 0.1,
       "W6e 反证：分子退回裸 STAT_MARKET 时复现 95.65（判据有判别力）",
       "old=%.2f" % old["mkt"])


def section_w8_seven_types():
    print("\n== W8. 7 类零特例：阵营伤权重恒 0、正常词条恒正 ==")
    rc = _load_card()
    FACTION = ("Damage to Grineer", "Damage vs Grineer", "Damage to Corpus",
               "Damage vs Corpus", "Damage to Infested", "Damage vs Infested")
    bad = [e for e in FACTION if rc._mkt_wstat(e) != 0.0]
    ok(not bad, "W8a 六种阵营伤字面写法权重恒为 0", "; ".join(bad) or "6/6")
    bad2 = [k for k in ("Damage", "Multishot", "Critical Damage", "Toxin Damage",
                        "Status Damage", "Magazine Capacity")
            if rc._mkt_wstat(k) <= 0.0]
    ok(not bad2, "W8b 正常词条权重恒为正（未被误伤）", "; ".join(bad2) or "6/6")
    # 未登记词条仍回落默认 30（Status Damage / Elemental Damage 目前不在 STAT_MARKET）
    ok(rc._mkt_wstat("Not A Real Stat") == 30.0 and rc._mkt_wstat("Status Damage") == 30.0,
       "W8c 未登记词条回落默认 30（如实暴露 Status Damage 缺登记）",
       "Status Damage=%.0f" % rc._mkt_wstat("Status Damage"))


def section_w7_all():
    print("\n== W7. 全量 623 把：mkt ∈[0,100] 且非 NaN ==")
    rc = _load_card()
    disp = json.load(open(_DISP, encoding="utf-8"))
    bad, n = [], 0
    for en, v in disp.items():
        if not isinstance(v, dict):
            continue
        rt = v.get("type")
        w = rc._weapon_raw(en) or {}
        if not w:
            continue
        n += 1
        p = {"weapon": en, "weapon_zh": v.get("zh") or en, "type": rt, "disp": v.get("disp") or 1.0,
             "rank": 8, "stats": [{"en": "Damage", "zh": "基础伤害", "sign": "+", "unit": "%", "value": 100.0},
                                  {"en": "Multishot", "zh": "多重射击", "sign": "+", "unit": "%", "value": 80.0},
                                  {"en": "Zoom", "zh": "变焦", "sign": "-", "unit": "%", "value": 50.0}]}
        fix = rc._fix_of(p)
        for s in p["stats"]:
            s["_rng"] = rc._range_of(s, p, fix)
        sc = rc._scores(p, rc._sim(p, w))
        m = sc["mkt"]
        if not (isinstance(m, float) and 0.0 <= m <= 100.0 and m == m):
            bad.append("%s=%r" % (en, m))
    ok(n >= 500, "W7a 全量武器跑到", "n=%d" % n)
    ok(not bad, "W7b 市场分全部 ∈[0,100] 且非 NaN", "; ".join(bad[:3]))


if __name__ == "__main__":
    print("== 模块W. 市场分「词条类型地板」守门 ==")
    section_w1_w2(); print()
    section_w3_goldens(); print()
    section_w4_monotone(); print()
    section_w5_anti(); print()
    section_w6_exclude(); print()
    section_w7_all(); print()
    section_w8_seven_types()
    print("\n%d PASS / %d FAIL" % (_P, _F))
    sys.exit(1 if _F else 0)
