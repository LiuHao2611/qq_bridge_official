# -*- coding: utf-8 -*-
"""守门：洗卡推荐的数据源与算法（2026-11-16 重做）。

这张卡的价值全在「数据不是编的」。所以本文件钉死四类契约：

  A. **词条映射**（``riven_recommend_attrs``）
     * 7 类紫卡词表里的**每一个**词条都必须映射到 WFM 属性（旧手写表漏了 6 条，
       其中 Initial Combo / Additional Combo Count Chance 被误判成「WFM 不存在」）；
     * 三条历史误判必须钉死正确 slug（``channeling_damage`` 等），源码里再出现
       ``initial_combo`` 这种按英文名猜的 slug 即失败。

  B. **价差口径**（``_spread_of``）
     同分位 k 条中位差 —— 拿「整段比整段」会算出 33.3%，同分位是 100%，
     两者差 3 倍。注入这组数就能把口径钉死（``riven_card`` 2026-11-04 的 +700%
     伪溢价事故就是整段比整段造成的）。

  C. **分档阈值**（``_tier_pos``）
     参考卡正面八条实测 +84/+80/+79/+71/+46/+37/+6/+5 —— 这段数字是**外部基准**，
     阈值必须正好把它们切成 T0×3 / T1×3 / T2×2。阈值一改就红。

  D. **数值自洽**
     Magistar 倾向 1.2 → 近战暴率满 roll 180×1.2 = 216%，WFM 实盘最高 roll 215.7%
     （差 < 1%）；连击倍率表与官方公式逐点对齐；IPS 互换修正只动真互换的记录。

  E. **算得下就要留得住**
     后端算 9 条、模板只画 8 行 → 脚注写「有效词条 9 条」而表格 8 行（2026-11-16
     实拍发现）；近战把 Attack Speed 写成「射速/攻速」；脚注残留 ``**加粗**``。
     三条都是「数字/文案自洽」，用户一眼能看出，必须钉死。

跑法：``python tests/test_riven_recommend_spread.py``（容器内，需 Pillow/nonebot）。
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.plugins.qq_bridge.game_tools import riven_recommend as rr          # noqa: E402
from src.plugins.qq_bridge.game_tools import riven_recommend_attrs as attrs  # noqa: E402

_P = _F = 0


def ok(cond, name, detail=""):
    global _P, _F
    if cond:
        _P += 1
        print("  PASS %s" % name)
    else:
        _F += 1
        print("  FAIL %s %s" % (name, detail))


# ---------------------------------------------------------------------------
# A. 词条映射
# ---------------------------------------------------------------------------
def section_a():
    print("A. 词条映射（WFM /v2/riven/attributes 权威表）")
    base = json.loads((_ROOT / "data" / "warframe_items" / "riven_stat_base.json")
                      .read_text(encoding="utf-8"))
    total = 0
    missing, blocked = [], []
    for cls, table in base.items():
        for en in table:
            total += 1
            rec = attrs.attr_of(en)
            if not rec:
                missing.append((cls, en))
            elif not attrs.supports(en, cls):
                blocked.append((cls, en, rec["exclusive_to"]))
    ok(total >= 250, "A1 词表总量真实（%d 条 ≥ 250）" % total, "total=%d" % total)
    ok(not missing, "A2 7 类词表 100%% 映射到 WFM 属性", "missing=%r" % missing[:6])
    ok(not blocked, "A3 每类词条都通过 exclusiveTo 校验", "blocked=%r" % blocked[:6])

    # A4 三条历史误判（旧注释写「WFM 确无此 slug」，实测全都有）
    for en, slug in (("Initial Combo", "channeling_damage"),
                     ("Additional Combo Count Chance", "chance_to_gain_extra_combo_count"),
                     ("Chance to Gain Combo Count", "chance_to_gain_combo_count"),
                     ("Slam Attack Damage", "slam_attack_damage")):
        ok(attrs.slug_of(en) == slug, "A4 %s → %s" % (en, slug),
           "got=%r" % attrs.slug_of(en))

    # A5 中文名来自接口（不是自造译名）
    ok(attrs.short_zh("Slam Attack Damage") == "震地", "A5 震地中文名来自 WFM i18n",
       "got=%r" % attrs.short_zh("Slam Attack Damage"))
    ok(attrs.short_zh("Critical Chance") == "暴率", "A6 暴率短名压缩",
       "got=%r" % attrs.short_zh("Critical Chance"))

    # A7 源码不得存在「按英文名猜 slug」的**映射条目**（注释里讲这段历史是允许的）
    src = (_ROOT / "src" / "plugins" / "qq_bridge" / "game_tools" / "riven_recommend.py"
           ).read_text(encoding="utf-8")
    asrc = (_ROOT / "src" / "plugins" / "qq_bridge" / "game_tools"
            / "riven_recommend_attrs.py").read_text(encoding="utf-8")
    pat = re.compile(r"[\"'](initial_combo|additional_combo_count_chance|slam_attack)"
                     r"[\"']\s*:")
    bad = [m.group(1) for m in pat.finditer(src + asrc)]
    ok(not bad, "A7 源码无「按英文名猜」的错误 slug 映射条目", "bad=%r" % bad)

    # A8 快照自带来源与时间（可审计，不是无源硬表）
    meta = attrs.load_attrs()
    ok(meta["count"] >= 45 and "warframe.market" in
       json.loads((_ROOT / "src" / "plugins" / "qq_bridge" / "game_tools"
                   / "wfm_riven_attrs.json").read_text(encoding="utf-8"))["source"],
       "A8 属性快照可审计（count=%d origin=%s）" % (meta["count"], meta["origin"]))


# ---------------------------------------------------------------------------
# B. 价差口径
# ---------------------------------------------------------------------------
def section_b():
    print("\nB. 价差口径（同分位中位差）")
    base = [100.0, 200.0, 300.0, 400.0, 500.0]
    pool = [200.0, 400.0, 600.0]
    sp = rr._spread_of(base, pool)
    ok(sp is not None and sp["k"] == 3, "B1 k = min(n_base, n_pool) = 3",
       "sp=%r" % (sp,))
    # 同分位：base[:3] 中位 200，pool[:3] 中位 400 → +200 / +100%
    ok(abs(sp["delta"] - 200.0) < 1e-6, "B2 同分位 delta = +200p", "got=%r" % (sp["delta"],))
    ok(abs(sp["pct"] - 100.0) < 1e-6, "B3 同分位 pct = +100%", "got=%r" % (sp["pct"],))
    # 反例：整段比整段只有 +100p / +33.3% —— 必须不是这个数
    naive = (300.0, (400.0 - 300.0) / 300.0 * 100.0)
    ok(abs(sp["delta"] - naive[0]) > 1e-6,
       "B4 明确拒绝「整段比整段」（+100p/+33.3%）", "naive=%r" % (naive,))

    ok(rr._spread_of([1.0, 2.0], [3.0, 4.0]) is None,
       "B5 样本 < 3 条返回 None（拒绝估算）")
    ok(rr._spread_of([0.0] * 5, [1.0] * 5) is None, "B6 基准价为 0 返回 None")


# ---------------------------------------------------------------------------
# C. 分档阈值（参考卡外部基准）
# ---------------------------------------------------------------------------
#: Warframe Rabbit 参考卡「基于市场价差的推荐词条」正面八条（2026-11-16 截图实录）
REF_POS = [
    (405.6, 84.0), (382.7, 80.0), (379.5, 79.0), (341.7, 71.0),
    (219.3, 46.0), (179.5, 37.0), (26.7, 6.0), (26.4, 5.0),
]


def section_c():
    print("\nC. 分档阈值（由参考卡八条反推）")
    rows = [{"en": "x%d" % i, "zh": "s%d" % i, "delta": p, "pct": q}
            for i, (p, q) in enumerate(REF_POS)]
    out = rr._tier_pos(rows)
    tiers = [r["tier"] for r in out]
    ok(tiers.count("T0") == 3, "C1 T0 = 3 条（+84/+80/+79）", "tiers=%r" % tiers)
    ok(tiers.count("T1") == 3, "C2 T1 = 3 条（+71/+46/+37）", "tiers=%r" % tiers)
    ok(tiers.count("T2") == 2, "C3 T2 = 2 条（+6/+5）", "tiers=%r" % tiers)
    ok([r["pct"] for r in out] == [84.0, 80.0, 79.0, 71.0, 46.0, 37.0, 6.0, 5.0],
       "C4 输出按价差降序、逐位复现参考卡顺序")

    # C5 阈值本身（改一个数就该红）
    ok((rr.TIER_T0, rr.TIER_T1, rr.TIER_T2) == (75.0, 30.0, 3.0),
       "C5 阈值常量 = 75 / 30 / 3", "got=%r" % ((rr.TIER_T0, rr.TIER_T1, rr.TIER_T2),))

    # C6 致命项：正面词条 pct ≤ 0 → 不占 T 档
    deadly_rows = rr._tier_pos([{"en": "a", "zh": "a", "delta": -5.0, "pct": -1.2},
                                {"en": "b", "zh": "b", "delta": 90.0, "pct": 20.0}])
    ok([r["tier"] for r in deadly_rows] == ["T2", "致命"],
       "C6 pct<0 的正面词条进「致命」档", "got=%r" % [r["tier"] for r in deadly_rows])
    ok(len(rr._deadly(deadly_rows)) == 1, "C7 _deadly 只取 pct≤0 的正面词条")

    # C8 负面三档边界
    nb = rr._bucket_neg([
        {"en": "a", "zh": "a", "delta": -3.0, "pct": -3.0},    # 推荐（不掉价）
        {"en": "b", "zh": "b", "delta": -20.0, "pct": -20.0},  # 普通（边界内）
        {"en": "c", "zh": "c", "delta": -50.0, "pct": -25.0},  # 存疑（掉价 >20%）
        {"en": "d", "zh": "d", "delta": 30.0, "pct": 15.0},    # 推荐（逆势加价）
    ])
    ok([x["en"] for x in nb["rec"]] == ["d", "a"], "C8 推荐负面 = 不伤价（≥ −3%）",
       "got=%r" % [x["en"] for x in nb["rec"]])
    ok([x["en"] for x in nb["ord"]] == ["b"], "C9 普通负面 = −20%~−3%",
       "got=%r" % [x["en"] for x in nb["ord"]])
    ok([x["en"] for x in nb["doubt"]] == ["c"], "C10 存疑 = 掉价 > 20%",
       "got=%r" % [x["en"] for x in nb["doubt"]])

    # C11 负面改「同负面池相对基准」：完整卡溢价必须被抵消
    rows = [{"en": "a", "zh": "a", "median": 300.0},
            {"en": "b", "zh": "b", "median": 600.0},
            {"en": "c", "zh": "c", "median": 900.0}]
    rr._rebase_neg(rows)
    ok([r["delta"] for r in rows] == [-300.0, 0.0, 300.0],
       "C11 负面 delta 相对负面池中位（-300/0/+300）", "got=%r" % [r["delta"] for r in rows])
    ok(rows[2]["neg_base"] == 600.0, "C12 负面基准一并回传（卡面可解释）")


# ---------------------------------------------------------------------------
# D. 数值自洽
# ---------------------------------------------------------------------------
def section_d():
    print("\nD. 数值自洽")
    disp = rr._disp_of("Magistar")
    ok(abs(disp - 1.2) < 1e-9, "D1 Magistar 倾向 = 1.2（DE 官方紫卡表）", "got=%r" % disp)
    base_tbl = json.loads((_ROOT / "data" / "warframe_items" / "riven_stat_base.json")
                          .read_text(encoding="utf-8"))["Melee"]
    full = base_tbl["Critical Chance"] * disp
    ok(abs(full - 216.0) < 0.6 and abs(full - 215.7) / 215.7 < 0.01,
       "D2 近战暴率满 roll 216%% ↔ WFM 实盘 215.7%%", "got=%.1f" % full)

    # D3 连击倍率表（官方 1 + 0.5×⌊combo/5⌋，上限 12×）
    pts = [(0, 1.0), (4, 1.0), (5, 1.5), (9, 1.5), (10, 2.0), (49, 5.5), (110, 12.0), (300, 12.0)]
    bad = [(c, rr._combo_mult(c), e) for c, e in pts if abs(rr._combo_mult(c) - e) > 1e-9]
    ok(not bad, "D3 连击倍率逐点对齐官方公式", "bad=%r" % bad)

    # D4 IPS 互换修正（本地快照真 bug：官方 Wiki 168/31.5/10.5）
    raw = {"damage": {"total": 210, "impact": 168, "puncture": 10.5, "slash": 31.5},
           "attacks": [{"damage": {"impact": 168, "puncture": 31.5, "slash": 10.5}}]}
    fixed = rr._fix_ips(dict(raw))["damage"]
    ok(abs(fixed["puncture"] - 31.5) < 1e-6 and abs(fixed["slash"] - 10.5) < 1e-6,
       "D4 puncture/slash 互换被纠正为 168/31.5/10.5", "got=%r" % fixed)
    ok(rr._panel(rr._fix_ips(dict(raw)), True)["comp"] == "冲击 80% · 穿刺 15% · 切割 5%",
       "D5 伤害构成与官方 Wiki 一致（80/15/5）")

    # D6 非互换记录不得被改（防止「修正」把正确数据改错）
    raw2 = {"damage": {"total": 100, "impact": 50, "puncture": 30, "slash": 20},
            "attacks": [{"damage": {"impact": 50, "puncture": 30, "slash": 20}}]}
    ok(rr._fix_ips(dict(raw2))["damage"] == raw2["damage"],
       "D6 非互换记录原样保留")

    # D7 面板列名：近战用「攻速」而非「射速」
    panel = rr._panel({"criticalChance": 0.2, "criticalMultiplier": 2,
                       "procChance": 0.1, "fireRate": 0.833, "totalDamage": 210,
                       "damage": {"total": 210, "impact": 168, "puncture": 31.5, "slash": 10.5}},
                      True)
    ok(panel["cc"] == "20%" and panel["cm"] == "×2" and panel["status"] == "10%"
       and panel["fr"] == "0.83/秒" and panel["base"] == "210",
       "D7 面板六列与参考卡逐位一致", "got=%r" % panel)
    ok(panel["is_melee"] is True, "D8 近战标记透传（模板据此切换攻速/射速）")


def section_e():
    """E. 算出来的必须画得下（2026-11-16 实拍发现的 9 vs 8 事故）。"""
    print("\n[E] 容量与文案自洽")

    # E1/E2 后端算 9 条、模板只画 8 行 → 脚注「有效词条 9 条」跟表格 8 行对不上。
    ok(rr._MAX_POS >= rr._QUERY_POS,
       "E1 后端展示上限 ≥ 候选数（算得下就要留得住）",
       "MAX_POS=%r QUERY_POS=%r" % (rr._MAX_POS, rr._QUERY_POS))
    tpl = (_ROOT / "src/plugins/qq_bridge/pic/templates/riven_recommend.py").read_text(
        encoding="utf-8")
    m = re.search(r"_MAX_ROWS_POS,\s*_MAX_ROWS_NEG,\s*_MAX_ROWS_DOUBT,\s*_MAX_ROWS_DEAD\s*=\s*"
                  r"(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)", tpl)
    ok(m is not None, "E2 模板行数常量可解析")
    if m:
        ok(int(m.group(1)) >= rr._QUERY_POS,
           "E3 模板正面行数 ≥ 后端候选数（少一行就数字对不上）",
           "MAX_ROWS_POS=%s QUERY_POS=%r" % (m.group(1), rr._QUERY_POS))

    # E4 WFM 把枪射速与近战攻速合成同一条，卡面必须按类别二选一
    zh = attrs.short_zh("Fire Rate / Attack Speed", True)
    ok(zh == "攻速", "E4 近战 Attack Speed → 攻速", "got=%r" % zh)
    zh = attrs.short_zh("Fire Rate / Attack Speed", False)
    ok(zh == "射速", "E5 枪械 Attack Speed → 射速", "got=%r" % zh)
    zh = attrs.short_zh("Fire Rate / Attack Speed")
    ok(zh == "射速/攻速", "E6 类别未知保留合并写法（不猜）", "got=%r" % zh)

    # E7 脚注「有效词条」必须排除致命项（它们单独占右栏），否则表格行数对不上
    notes = rr._notes({"disp": 1.2, "market": {"ok": True, "n_base": 100, "base_med": 468,
                                               "pos": [{"tier": "T0"}, {"tier": "T2"},
                                                       {"tier": "致命"}, {"tier": "致命"}]}})
    tail = notes[-1]
    ok("有效词条 2 条" in tail, "E7 有效词条排除致命项（2 而非 4）", "got=%r" % tail)

    # E8 卡面文案不得残留 markdown 标记（``**加粗**`` 会被当成字面星号画出来）
    bad = [n for n in notes if "**" in n or "``" in n]
    ok(not bad, "E8 脚注无 markdown 残留", "bad=%r" % bad[:2])


def main() -> int:
    print("== 模块. 洗卡推荐数据源与算法守门 ==")
    section_a()
    section_b()
    section_c()
    section_d()
    section_e()
    print("\n%d PASS / %d FAIL" % (_P, _F))
    return 1 if _F else 0


def test_riven_recommend_spread():
    """pytest 入口：任一契约不过 → 非 0 → 用例失败。"""
    assert main() == 0


if __name__ == "__main__":
    sys.exit(main())
