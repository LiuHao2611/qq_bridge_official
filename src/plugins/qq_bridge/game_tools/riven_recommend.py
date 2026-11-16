# -*- coding: utf-8 -*-
"""game_tools/riven_recommend.py —— 紫卡洗卡词条推荐（击杀效率 + 市场挂牌价差双维度）。

## 这张卡在回答什么
用户手里有一把**未洗**的紫卡，问「该洗出哪几条」。两个维度：
  1. **击杀效率**：这条词条按该武器真实倾向满 roll 折进面板后，DPS 涨多少 → 该留哪 3 条正词条；
  2. **市场挂牌价差**：这条词条在 Warframe.Market 上「带它」比「不带它」贵多少白金 → 洗出来能卖多少。

## 数据源（全部可核对，零硬编码结论）
  * **武器面板**：``data/warframe_items/{Primary,Secondary,Melee}.json``（WFCD 官方镜像）
    → 暴击率 / 暴击倍率 / 触发率 / 攻速 / 基础伤害 / 冲击·穿刺·切割构成。
  * **紫卡类别 + 倾向**：``riven_disp.json``（DE 官方紫卡表 623 把，含 ``disp`` 与 ``type``）。
    Magistar（执法者）实测 ``disp=1.2`` —— 这正是参考卡脚注「按统一 1.2 倾向」的来源：
    **1.2 不是全局常数，是该武器自己的 disposition**（2026-11-16 实测坐实）。
  * **词条满 roll 基准**：``riven_stat_base.json``（3 正属性、disp=1.0 的官方满值）。
    满 roll 值 = base × disp，例如近战暴率 180 × 1.2 = 216%（与 WFM 实盘最高 roll
    215.7% 逐位吻合，见测试 ``test_riven_recommend_spread``）。
  * **词条 ↔ WFM 属性**：``/v2/riven/attributes`` 快照（见 ``riven_recommend_attrs``），
    用 ``gameRef`` / ``i18n`` 自动建索引，**不再手写 slug 表**。
  * **挂牌价**：``/v1/auctions/search?type=riven&weapon_url_name=<slug>``
    + ``positive_stats`` / ``negative_stats`` 单条过滤；一口价、PC、同武器。

## 价差怎么算（为什么不是「全站中位 − 全站中位」）
WFM 公开检索对紫卡只暴露「最便宜 100 / 最贵 100」两个**截断视图**（``page`` 被忽略，
实测第 2 页与第 1 页 100/100 重合）。所以：
    base      = 未过滤视图（最便宜 100 条）的**中位数**（该武器地板价）
    pool_i    = 带词条 i 的视图（最便宜 100 条）的中位数
    k         = min(len(base), len(pool_i))          ← **同分位**，避免拿整段比首尾
    delta_i   = median(pool_i[:k]) − median(base[:k])
    pct_i     = delta_i / median(base[:k]) × 100
同分位口径是项目既有实证（``riven_card.fetch_market_bundle`` 与 Robot 阿利乌卡
base 400 + Damage 89.5 + Recoil 410 = 899.5 ↔ Robot 972），**不是本卡新发明的**。
实测执法者：base 466p，震地 800p(+71.5%)、初始连击 700p(+50.1%)、
暴率 500p(+7.2%)——量级与参考卡（base 480、震地 +84%、初始连击 +37%）一致，
逐位差异来自截断视图与挂单时效，**方向与排序才是这张卡的结论**。

## 分档（阈值由参考卡反推，非拍脑袋）
参考卡正面八条：+84/+80/+79 → T0，+71/+46/+37 → T1，+6/+5 → T2。
    pct ≥ 75 → **T0**；≥ 30 → **T1**；≥ 3 → **T2**；≤ 0 → **致命项**（洗到白占一个正词条位）
三个阈值同时吻合八条样本，且 75/30/3 是唯一能同时把 71 分到 T1、6 分到 T2 的组合。

## 击杀效率的模型边界（必须诚实）
  * 用**裸面板**（不含配卡）算边际 —— 这是参考卡「暴伤 +18.2%」能被复现的唯一口径：
    近战暴伤满 roll 108% → ``cm 2.0→3.08`` → ``1+0.2×2.08`` / ``1.2`` = **+18.0%**。
    若掺入配卡（肢解 +90% 暴伤）会被稀释到 +13.8%，与参考卡对不上。
  * 通用词条（基伤 / 元素 / 多重 / 攻速 / 派系）**不进推荐池**：它们对所有武器都是
    好词条，回答不了「这把武器该留哪三条」；参考卡击杀区也只列流派词条。
  * 近战连击按官方倍率表 ``1 + 0.5×floor(combo/5)``（上限 12×）建模，战斗窗口取 60 s：
    攻速 0.833 → 50 combo（6.0×），初始连击 +29.4 → 79.4（8.5×）= +41.7%，
    排序上与参考卡「初始连击第一」一致。窗口是**显式假设**，写在脚注里。
  * 触发流不建 DoT 模型：``-触发几率`` 这类词条的伤害影响如实标「模型未覆盖」，
    绝不给假数字。
"""
from __future__ import annotations

import asyncio
import math
import re
import time
from typing import Optional

from nonebot.log import logger

from . import warframe, riven_card as rc
from . import riven_recommend_attrs as attrs

# ===========================================================================
# 常量
# ===========================================================================
#: 击杀效率候选池要排除的「通用词条」—— 任何武器都该有，不构成洗卡建议
_GENERIC = {
    "Damage", "Base Damage", "Melee Damage", "Multishot",
    "Fire Rate", "Attack Speed", "Fire Rate / Attack Speed",
    "Electricity Damage", "Heat Damage", "Cold Damage", "Toxin Damage",
    "Viral Damage", "Corrosive Damage", "Radiation Damage", "Magnetic Damage",
    "Blast Damage", "Gas Damage", "Status Damage",
    "Impact Damage", "Puncture Damage", "Slash Damage",
}
#: 派系词条单列（正负都是"只对某系生效"的独立卡，不宜混进通用推荐）
_FACTION = {
    "Damage to Grineer", "Damage vs Grineer", "Damage to Corpus", "Damage vs Corpus",
    "Damage to Infested", "Damage vs Infested", "Damage to Orokin",
    "Damage to Scaldra", "Damage to Techrot",
}
#: 近战连击：官方倍率 ``1 + 0.5×floor(combo/5)``，基础上限 12×
_COMBO_CAP = 12.0
#: 战斗窗口（秒）：近战持续交战的显式假设，写进卡面脚注
_COMBO_WINDOW = 60.0

#: 市场分档阈值（由参考卡 8 条正面样本反推，见模块 docstring）
TIER_T0, TIER_T1, TIER_T2 = 75.0, 30.0, 3.0
#: 价差**查询**候选数：正面 9 / 负面 4（合计 13 次 + 基准 2 次 ≈ 15 请求 ≈ 20s）。
#: 再多会触发 Cloudflare 429（实测 16 请求即命中，retry-after 60~117s）。
_QUERY_POS, _QUERY_NEG = 9, 4
#: 卡面显示行数上限。正面必须 ≥ 候选数，否则「算出来的」与「画出来的」不一致
#: （脚注「有效词条 N 条」会跟表格行数对不上，2026-11-16 实拍发现 9 vs 8）。
_MAX_POS, _MAX_NEG = _QUERY_POS, 8

#: 面板 type → 紫卡类别（仅在 ``riven_disp.json`` 查不到该武器时作次选；
#: 面板分类 Throwing / Dual Pistols 与 DE 紫卡类别是两套词表，直接查会查空）
RIVEN_CLASS = {
    "Rifle": "Rifle", "Shotgun": "Shotgun", "Pistol": "Pistol",
    "Throwing": "Pistol", "Dual Pistols": "Pistol",
    "Melee": "Melee", "Zaw": "Zaw", "Zaw Component": "Zaw",
    "Kitgun": "Kitgun", "Archgun": "Archgun",
}
#: 家族后缀：紫卡拍卖按武器家族挂单（rubico_prime 会 400，必须查 rubico）
_FAMILY_SUFFIX = re.compile(r"\s+(Prime|Wraith|Vandal|Dex|Kuva|Tenet|Prisma|Mara)$", re.I)


# ===========================================================================
# 面板
# ===========================================================================
def _fix_ips(raw: dict) -> dict:
    """修正本地 WFCD 快照的 **puncture / slash 互换**（按字段形状判定，零武器硬编码）。

    2026-11-16 实测（官方 Wiki ``wiki.warframe.com/w/Magistar``）：
        Normal Attack  168 (80%) Impact · **31.5 (15%) Puncture** · **10.5 (5%) Slash**
    本地 ``Melee.json`` 顶层 ``damage`` 却是 ``puncture=10.5 / slash=31.5``（正好互换），
    而同一份记录的 ``attacks[0].damage`` 是正确的。``attacks[]`` 直接来自 Wiki 信息框
    （带 ``AttackIndex``），顶层是派生字段 —— 项目在 Phenmor 上已记录过同类错位
    （``riven_card.DAMAGE_COMP_OVERRIDE`` 注释），这里改为**按形状自动纠正**：
    只有当顶层与 ``attacks[0]`` 恰好互换、且两者总伤害一致时才采用 ``attacks[0]``；
    其余情况原样返回（不猜、不覆盖真值）。
    """
    dmg = raw.get("damage") or {}
    atk = (raw.get("attacks") or [{}])[0]
    ad = atk.get("damage") or {}
    try:
        top_p, top_s = float(dmg.get("puncture") or 0), float(dmg.get("slash") or 0)
        atk_p, atk_s = float(ad.get("puncture") or 0), float(ad.get("slash") or 0)
        top_i, atk_i = float(dmg.get("impact") or 0), float(ad.get("impact") or 0)
    except (TypeError, ValueError):
        return raw
    if not (atk_p and atk_s and top_p and top_s):
        return raw
    swapped = (abs(top_p - atk_s) < 1e-3 and abs(top_s - atk_p) < 1e-3
               and abs(top_i - atk_i) < 1e-3)
    if not swapped:
        return raw
    fixed = dict(raw)
    d = dict(dmg)
    d["puncture"], d["slash"] = atk_p, atk_s
    fixed["damage"] = d
    return fixed


def _panel(raw: dict, is_melee: bool) -> dict:
    """基础面板 → 卡面六列 + 伤害构成（缺字段一律「—」，不拿 0 冒充）。"""
    dmg = raw.get("damage") or {}
    phys = {k: float(dmg.get(k) or 0) for k in ("impact", "puncture", "slash")}
    phys_sum = sum(phys.values())
    parts = []
    if phys_sum > 0:
        for k in ("impact", "puncture", "slash"):
            if phys[k] > 0:
                parts.append("%s %.0f%%" % ({"impact": "冲击", "puncture": "穿刺",
                                             "slash": "切割"}[k], phys[k] / phys_sum * 100))
    else:
        tot = float(dmg.get("total") or 0) or 1e-9
        for k, zh in (("heat", "火焰"), ("cold", "冰冻"), ("electricity", "电击"),
                      ("toxin", "毒素"), ("viral", "病毒"), ("corrosive", "腐蚀")):
            v = float(dmg.get(k) or 0)
            if v > 0:
                parts.append("%s %.0f%%" % (zh, v / tot * 100))

    def _pct(v, digits=0):
        if v is None:
            return "—"
        return ("%." + str(digits) + "f%%") % (float(v) * 100.0)

    cm = raw.get("criticalMultiplier")
    if cm is None:
        cm_s = "—"
    elif abs(float(cm) - round(float(cm))) < 1e-9:
        cm_s = "×%.0f" % float(cm)
    else:
        cm_s = "×%.1f" % float(cm)
    fr = raw.get("fireRate")
    total = raw.get("totalDamage")
    if total is None:
        total = dmg.get("total")
    return {
        "cc": _pct(raw.get("criticalChance")),
        "cm": cm_s,
        "status": _pct(raw.get("statusChance") if raw.get("statusChance") is not None
                       else raw.get("procChance")),
        "fr": ("%.2f/秒" % float(fr)) if isinstance(fr, (int, float)) else "—",
        "base": ("%.0f" % float(total)) if isinstance(total, (int, float)) else "—",
        "comp": " · ".join(parts) if parts else "—",
        "is_melee": bool(is_melee),
    }


def _dps_bare(w: dict) -> float:
    """裸面板期望 DPS（不掺任何配卡）：唯一目的是算词条的边际收益。"""
    return rc._dps(w, rc._base_bucket())


def _combo_mult(combo: float) -> float:
    """近战连击倍率（官方表，封顶 12×）。"""
    return min(_COMBO_CAP, 1.0 + 0.5 * math.floor(max(0.0, combo) / 5.0))


def _melee_extra(w: dict, en: str, val: float, sign: float = 1.0) -> Optional[float]:
    """机制类词条的等效 DPS 收益；不在机制内的返回 None。

    * ``Initial Combo``：开局赠送 combo 点数 → 60 s 窗口末尾的总 combo 提高；
    * ``Additional Combo Count Chance`` **刻意不建模**：它加快连击获取，但收益完全
      取决于战斗节奏与是否被打断，给不出可信数字；参考卡的击杀区也没列它。
      如实返回 None（收益按 0 处理），绝不编一个系数。
    * 其余（连击持续 / 范围 / 处决 / 重击 / 滑暴）同样不估算，返回 None。

    ``sign=-1`` 用于「洗成该负面」的反向测算（组合点数为负向偏移）。
    """
    fr = float(w.get("fireRate") or 0.0)
    if fr <= 0:
        return None
    hits = fr * _COMBO_WINDOW
    m0 = _combo_mult(hits)
    if m0 <= 0:
        return None
    amount = max(0.0, float(val)) * (1.0 if sign >= 0 else -1.0)
    if en == "Initial Combo":
        m1 = _combo_mult(hits + amount)
    else:
        return None
    return (m1 / m0 - 1.0) * 100.0


def _kill_eff(w: dict, cls: str, disp: float) -> dict:
    """击杀效率维度：正面 top3 边际收益 + 推荐负面（伤害影响最小的三条）。"""
    base_tbl = rc._load_riven_tables()[1]
    table = base_tbl.get(cls) or {}
    if not table:
        return {"pos": [], "neg": [], "na": "缺 %s 类词条基准表，无法计算收益" % (cls or "未知")}

    base_dps = _dps_bare(w) if w else 0.0
    is_melee = cls in ("Melee", "Zaw")
    neg_cand = [e for e in table if not attrs.is_positive_only(e)]

    def _gain(en: str, val: float) -> Optional[float]:
        """该词条的边际 DPS 收益%（机制类走连击模型）。"""
        if is_melee:
            mech = _melee_extra(w, en, val)
            if mech is not None:
                return mech
        if base_dps <= 0:
            return None
        b = rc._base_bucket()
        rc._apply_riven(b, [{"en": en, "value": float(val), "unit": "%", "sign": "+"}])
        return (rc._dps(w, b) / base_dps - 1.0) * 100.0

    pos = []
    for en in table:
        if en in _GENERIC or en in _FACTION:
            continue
        if not attrs.supports(en, cls):
            continue
        base_val = table.get(en)
        if not base_val or base_val < 1.0:
            continue
        val = float(base_val) * float(disp or 1.0)
        g = _gain(en, val)
        if g is None or g <= 0.05:
            continue
        pos.append({"zh": attrs.short_zh(en, is_melee), "en": en, "val": round(val, 1),
                    "gain": round(g, 1), "tier": _gain_tier(g)})
    pos.sort(key=lambda x: -x["gain"])

    # 推荐负面：逐条算「洗成该负面」的 DPS 损失，取最小者；并列时按市场友好度
    neg = []
    for en in neg_cand:
        if en in _FACTION or not attrs.supports(en, cls):
            continue
        base_val = table.get(en)
        if not base_val or base_val < 1.0:
            continue
        loss = 0.0
        if is_melee:
            mech = _melee_extra(w, en, float(base_val) * float(disp or 1.0), sign=-1.0)
            if mech is not None:
                loss = max(0.0, -mech)
        if not loss and base_dps > 0:
            b = rc._base_bucket()
            rc._apply_riven(b, [{"en": en, "value": float(base_val) * float(disp or 1.0),
                                 "unit": "%", "sign": "-"}])
            loss = max(0.0, (1.0 - rc._dps(w, b) / base_dps) * 100.0)
        neg.append({"zh": attrs.short_zh(en, is_melee), "en": en, "loss": round(loss, 1),
                    "market": rc.NEG_MARKET.get(en, 20)})
    neg.sort(key=lambda x: (x["loss"], -x["market"]))
    return {
        "pos": pos[:3],
        "neg": [x["zh"] for x in neg[:3]],
        "neg_en": [x["en"] for x in neg[:3]],
        "neg_loss": [x["loss"] for x in neg[:3]],
        "base_dps": round(base_dps, 1),
        "model": ("裸面板 + 近战连击（%ds 窗口）" % int(_COMBO_WINDOW)) if is_melee else "裸面板",
    }


def _gain_tier(g: float) -> str:
    """击杀效率分档（仅用于排序着色）：≥30 强 / ≥10 中 / 其余弱。"""
    return "强" if g >= 30 else ("中" if g >= 10 else "弱")


# ===========================================================================
# 市场维度：挂牌价差
# ===========================================================================
def _median(prices: list) -> float:
    return rc._robust_center(prices) if prices else 0.0


def _pool_prices(auc: list) -> list:
    return rc._prices_of(auc)


def _spread_of(base_prices: list, pool_prices: list) -> Optional[dict]:
    """同分位中位差：``k = min(n_base, n_pool)``，各取最便宜的 k 条再比中位。

    两池都来自 ``price_asc`` 截断视图；若直接拿「整段比整段」，样本量差 6 倍时
    会得到 +700% 的伪溢价（``riven_card`` 2026-11-04 实测记录）。同分位是唯一
    能让两池可比的口径。
    """
    if len(base_prices) < rc._WFM_MIN_SAMPLES or len(pool_prices) < rc._WFM_MIN_SAMPLES:
        return None
    k = min(len(base_prices), len(pool_prices))
    bm = _median(base_prices[:k])
    pm = _median(pool_prices[:k])
    if bm <= 0:
        return None
    return {"k": k, "base": bm, "median": pm, "delta": pm - bm,
            "pct": (pm - bm) / bm * 100.0}


async def _market_spread(slug: str, cls: str, ke_pos: list, ke_neg: list) -> dict:
    """正面 / 负面候选逐条查真实挂牌价差。任何失败都如实进 ``skipped``。

    候选筛选（决定「查谁」，不影响价格算法）：
      1. 击杀效率维度已推荐的词条**必须**入选（两区结论不能互相矛盾）；
      2. 其余按 ``price_desc``（最贵 100 条）里的**出现频次**排序 —— 高价卡爱用的
         词条才有价差可测。该视图的价格**绝不参与**任何计算（项目既有铁律）；
      3. 各取前 10 条，控制请求数（每卡约 22 次请求，0.35s 节流 ≈ 15s）。
    """
    if not slug:
        return {"ok": False, "err": "无法解析该武器的 Warframe.Market 标识（family slug）"}
    is_melee = cls in ("Melee", "Zaw")   # 只为卡面文案（射速/攻速）取值
    try:
        base_pool = await rc._fetch_auction_page(slug, "price_asc")
        desc_pool = await rc._fetch_auction_page(slug, "price_desc")   # 仅频次
    except rc.RivenMarketError as e:
        return {"ok": False, "err": str(e)[:160]}
    except Exception as e:  # noqa: BLE001 — 未预期异常同样上卡面
        return {"ok": False, "err": "市场数据获取失败：%s: %s" % (type(e).__name__, str(e)[:100])}
    base_prices = _pool_prices(base_pool)
    if len(base_prices) < rc._WFM_MIN_SAMPLES:
        return {"ok": False,
                "err": "该武器挂牌仅 %d 条 < %d，价差不可估" % (len(base_prices), rc._WFM_MIN_SAMPLES)}

    # 双路预筛：高价视图频次（词条值不值钱）× 基准视图频次（词条常不常见）。
    # 只用 desc 会漏掉「高价卡里不常见但正常卡常见」的词条（实测 Magistar 的震地就漏了）。
    hot_pos = rc._hot_counts(desc_pool, True)
    hot_neg = rc._hot_counts(desc_pool, False)
    for k, v in rc._hot_counts(base_pool, True).items():
        hot_pos[k] = hot_pos.get(k, 0) + v
    for k, v in rc._hot_counts(base_pool, False).items():
        hot_neg[k] = hot_neg.get(k, 0) + v
    legal = [e for e in (rc._load_riven_tables()[1].get(cls) or {})
             if not attrs.is_positive_only(e) and attrs.supports(e, cls)]
    # 正面 / 负面**分别**给优先级：击杀效率推荐的正面词条必须查（否则卡面两区打架），
    # 但它的负面推荐不该挤占正面候选名额（那是两套词条池）。
    ke_pos_set = {x["en"] for x in ke_pos}
    ke_neg_set = set(ke_neg)

    def _ordered(pool: list, hot: dict, limit: int, first: set) -> list:
        return sorted(pool, key=lambda e: (0 if e in first else 1,
                                           -hot.get(attrs.slug_of(e), 0)))[:limit]

    pos_cand = _ordered([e for e in legal if e not in _FACTION], hot_pos,
                        _QUERY_POS, ke_pos_set)
    neg_cand = _ordered(legal, hot_neg, _QUERY_NEG, ke_neg_set)

    pos_rows, neg_rows, skipped = [], [], []
    logger.info(f"wf_wash_spread_start slug={slug} base_n={len(base_prices)} "
                f"pos={len(pos_cand)} neg={len(neg_cand)}")
    for en, field in ([(e, "positive_stats") for e in pos_cand]
                      + [(e, "negative_stats") for e in neg_cand]):
        slug_stat = attrs.slug_of(en)
        if not slug_stat:
            skipped.append("%s(无 WFM 属性)" % attrs.short_zh(en, is_melee))
            continue
        try:
            pool = await rc._fetch_auction_page(
                slug, "price_asc", "&%s=%s" % (field, slug_stat), allow_empty=True)
        except rc.RivenMarketError as e:
            skipped.append("%s(%s)" % (attrs.short_zh(en, is_melee), str(e)[:26]))
            continue
        sp = _spread_of(base_prices, _pool_prices(pool))
        if sp is None:
            skipped.append("%s(样本不足)" % attrs.short_zh(en, is_melee))
            continue
        if abs(sp["pct"]) > 400.0:
            # 结构性异常（池里全是天价 3正1负 卡 / 挂单离群）：宁可不出，不出假数
            skipped.append("%s(价差异常 %+.0f%%)" % (attrs.short_zh(en, is_melee), sp["pct"]))
            continue
        logger.info(f"wf_wash_spread {field} {slug_stat} k={sp['k']} "
                    f"med={sp['median']:.0f} delta={sp['delta']:+.1f} pct={sp['pct']:+.1f}")
        row = {"en": en, "zh": attrs.short_zh(en, is_melee), "delta": round(sp["delta"], 1),
               "pct": round(sp["pct"], 1), "k": sp["k"], "median": round(sp["median"], 0)}
        (pos_rows if field == "positive_stats" else neg_rows).append(row)

    # ---- 负面词条改「同负面池相对基准」 ----
    # 2026-11-16 实测：``negative_stats=X`` 返回的 86 条**全部带该负面**，且中位
    # 1000p 远高于全量池 468p —— 因为「有负面位」本身就筛出了 3 正 1 负的完整卡，
    # 2 正垃圾卡根本没有负面位。拿它跟全量池比，得到的是「完整卡溢价」而非
    # 「这条负面值多少」，方向都会算反（-触发 竟显示 +117%）。
    # 所以负面一律取**该武器负面池的中位**为基准：只保留词条之间的相对价差
    # （-冲击 加价 / -攻速 掉价），这才是「该选哪条负面」的答案。
    if len(neg_rows) >= 3:
        _rebase_neg(neg_rows)

    base_med = _median(base_prices)
    return {
        "ok": True, "base_med": round(base_med, 0), "n_base": len(base_prices),
        "pos": _tier_pos(pos_rows)[:_MAX_POS], "neg": _bucket_neg(neg_rows),
        "neg_base": (neg_rows[0].get("neg_base") if neg_rows else None),
        "skipped": skipped, "slug": slug,
    }


def _rebase_neg(rows: list) -> list:
    """负面词条改「同负面池相对基准」（原地改 delta/pct，返回同一列表）。

    为什么不能拿负面池跟全量池比（2026-11-16 实测坐实）：
        ``negative_stats=X`` 返回的 86 条**全部带该负面**，中位 **1000p**，
        而全量池（最便宜 100 条）中位 468p。原因是「有负面位」本身就筛出了
        3 正 1 负的完整卡 —— 2 正垃圾卡根本没有负面位。拿两者相减，得到的是
        「完整卡溢价」而不是「这条负面值多少」，连方向都会错（-触发 显示 +117%）。
    改法：以该武器**负面池中位**为基准，只保留词条间的相对价差
    （-冲击 加价 / -攻速 掉价），这才是「该选哪条负面」的答案。
    """
    if not rows:
        return rows
    nb = sorted(r["median"] for r in rows)[len(rows) // 2]
    for r in rows:
        d = r["median"] - nb
        r["delta"] = round(d, 1)
        r["pct"] = round(d / nb * 100.0, 1) if nb > 0 else 0.0
        r["neg_base"] = round(nb, 0)
    return rows


def _tier_pos(rows: list) -> list:
    """正面词条分档：pct ≥75 T0 / ≥30 T1 / ≥3 T2；≤0 进「致命项」。"""
    out = []
    for r in rows:
        p = r["pct"]
        r["tier"] = ("T0" if p >= TIER_T0 else "T1" if p >= TIER_T1
                     else "T2" if p >= TIER_T2 else "致命")
        out.append(r)
    rank = {"T0": 0, "T1": 1, "T2": 2, "致命": 3}
    out.sort(key=lambda x: (rank[x["tier"]], -x["delta"]))
    return out


def _bucket_neg(rows: list) -> dict:
    """负面词条分三档（语义见卡面标题）：

    * ``推荐``：不伤价（pct ≥ −3%）——洗出来不掉钱，首选；
    * ``普通``：小幅掉价（−20% ≤ pct < −3%）——能接受，换正词条质量；
    * ``存疑``：掉价 > 20% 或逆势加价 > 15% —— 数据自相矛盾，仅作参考。
    """
    rec, ord_, doubt = [], [], []
    for r in rows:
        p = r["pct"]
        if p >= -3.0:
            rec.append(r)
        elif p >= -20.0:
            ord_.append(r)
        else:
            doubt.append(r)
    rec.sort(key=lambda x: -x["delta"])
    ord_.sort(key=lambda x: -x["delta"])
    doubt.sort(key=lambda x: x["delta"])
    return {"rec": rec[:_MAX_NEG], "ord": ord_[:_MAX_NEG], "doubt": doubt[:_MAX_NEG]}


def _deadly(rows: list) -> list:
    """致命项 = 正面词条里 pct ≤ 0 的（洗到白占一个正词条位）。"""
    return [r for r in rows if r["tier"] == "致命"][:8]


# ===========================================================================
# 候选筛选
# ===========================================================================
def _riven_class(name_en: str, type_key: str) -> str:
    """武器 → 紫卡类别（决定满 roll 表）。``riven_disp.json`` 为权威口径。"""
    disp_tbl = rc._load_riven_tables()[0]
    for key in (name_en, _FAMILY_SUFFIX.sub("", (name_en or "").strip())):
        rec = disp_tbl.get(key or "")
        if isinstance(rec, dict) and rec.get("type"):
            return str(rec["type"])
    return rc.RIVEN_CLASS.get((type_key or "").strip(), "")


def _disp_of(name_en: str) -> float:
    """该武器真实 disposition（DE 官方紫卡表；查不到按 1.0，不假装 1.2）。"""
    rec = rc._load_riven_tables()[0].get(name_en or "")
    try:
        return float((rec or {}).get("disp") or 1.0)
    except (TypeError, ValueError):
        return 1.0


# ===========================================================================
# 主入口
# ===========================================================================
def _family_slug(name_en: str) -> str:
    """武器英文名 → WFM 紫卡族 slug（下划线；Prime 等变体按家族挂单）。"""
    low = (name_en or "").strip().lower()
    for suf in (" prime", " vandal", " wraith", " dex"):
        if low.endswith(suf):
            low = low[: -len(suf)].strip()
            break
    rec = (warframe._riven_weapons().get("by_en") or {}).get(low)
    slug = (rec or {}).get("slug") or low.replace(" ", "_")
    return (slug or "").replace("-", "_").strip()


def _weapon_label(raw: dict, fallback: str) -> str:
    en = (raw.get("name") or "").strip()
    if not en:
        return fallback
    try:
        return (warframe._weapon_zh(en) or "").strip() or en
    except Exception:  # noqa: BLE001 — 中文名只是显示层
        return en


def _weapon_desc(raw: dict) -> str:
    """「可能是暴击流武器」——按真实面板阈值判定，不按武器名写死。"""
    cc = float(raw.get("criticalChance") or 0)
    sc = float(raw.get("statusChance") or raw.get("procChance") or 0)
    if cc >= 0.20 and sc >= 0.20:
        return "可能是暴击触发双修武器"
    if cc >= 0.20:
        return "可能是暴击流武器"
    if sc >= 0.20:
        return "可能是触发流武器"
    return "基础面板武器"


def _notes(data: dict) -> list:
    """脚注（对齐参考卡五条，并把本实现的口径差异写清）。"""
    n = [
        "伤害词条推荐按该武器真实倾向 ×%.1f 计算（DE 官方紫卡表），"
        "收益＝该词条满 roll 折进裸面板后的边际 DPS，不含配卡" % (data.get("disp") or 1.0),
        "其他未列出词条样本不足或 WFM 无对应属性，无法计算可靠价差，请谨慎参考",
        "价差为「带该词条」与「不带该词条」的挂牌价差，非整卡售价；"
        "两池各取同分位最低 k 条中位，避开截断视图的样本量差",
        "负面价差以该武器「负面池中位」为基准（「有负面位」本身带来的完整卡溢价不计入），"
        "只保留词条之间的相对贵贱",
        "市场「致命项」表示洗到也不加价（白占一个正词条位）；"
        "伤害风险请结合具体数值与你的配装判断",
        "同一张卡最多一个融合词条；推荐会随武器、配装与模拟场景变化",
    ]
    if data.get("is_melee"):
        n.append("近战连击按官方倍率 1+0.5×⌊combo/5⌋（上限 12×）、"
                 "战斗窗口 %ds 建模；窗口是显式假设，短交战会放大初始连击收益"
                 % int(_COMBO_WINDOW))
    mk = data.get("market") or {}
    if mk.get("ok"):
        # 「有效词条」必须等于正面词条表格的实际行数：致命项单独占右栏，
        # 不能算进来（否则脚注 9 条、表格 8 行，一眼就是对不上）。
        n_pos = len([x for x in (mk.get("pos") or []) if x.get("tier") != "致命"])
        n.append("样本：基准视图 %d 条（中位 %sp）· 有效词条 %d 条 · 价差已剔除非同分位偏差"
                 % (mk.get("n_base", 0), _fm(mk.get("base_med", 0)), n_pos))
    return n


def _fm(v) -> str:
    try:
        f = float(v or 0)
    except (TypeError, ValueError):
        return "0"
    return ("%.0f" % f) if abs(f - round(f)) < 1e-9 else ("%.1f" % f)


async def recommend_data(weapon_query: str) -> dict:
    """紫卡洗卡词条推荐主入口。返回可直接喂给 pic 模板的 dict。"""
    raw = rc._weapon_raw(weapon_query)
    if not raw:
        try:
            res = warframe._resolve_weapon(weapon_query)
            if res:
                raw = rc._weapon_raw(res.get("name") or res.get("en") or "")
        except Exception:  # noqa: BLE001 — 解析失败即如实报未收录
            raw = None
    if not raw:
        return {"status": "error",
                "msg": "未收录该武器：%s（例：洗卡推荐 执法者）" % weapon_query}

    name_en = raw.get("name") or weapon_query
    type_key = raw.get("type") or raw.get("category") or ""
    cls = _riven_class(name_en, type_key)
    if not cls:
        return {"status": "error",
                "msg": "%s 不在紫卡类别表内（该武器没有紫卡），无洗卡推荐" % weapon_query}
    disp = _disp_of(name_en)
    is_melee = cls in ("Melee", "Zaw")
    w = _fix_ips(dict(raw))
    w["cat"] = "近战武器" if is_melee else (
        "副武器" if cls in ("Pistol", "Kitgun") else "主武器")

    panel = _panel(w, is_melee)
    ke = _kill_eff(w, cls, disp)

    slug = _family_slug(name_en) or warframe._resolve_riven_weapon(name_en)
    market = await _market_spread(slug, cls, ke.get("pos") or [], ke.get("neg_en") or [])

    data = {
        "status": "ok",
        "weapon": _weapon_label(raw, weapon_query),
        "weapon_en": name_en,
        "weapon_desc": _weapon_desc(raw),
        "slug": slug,
        "riven_class": cls,
        "disp": disp,
        "is_melee": panel["is_melee"],
        "panel": {"normal": panel},
        "kill_eff": ke,
        "market": market,
        "market_err": None if market.get("ok") else (market.get("err") or "市场数据不可用"),
        "attrs_origin": attrs.load_attrs().get("origin") or "",
        "attrs_at": attrs.load_attrs().get("fetched_at") or "",
        "ts": time.strftime("%Y-%m-%d %H:%M"),
    }
    if market.get("ok"):
        data["deadly"] = _deadly(market.get("pos") or [])
    data["notes"] = _notes(data)
    return data


def recommend_text(data: dict) -> str:
    """纯文本降级（渲染失败时用）。口径与卡片一致，不静默。"""
    if data.get("status") != "ok":
        return data.get("msg") or "未收录该武器"
    p = (data.get("panel") or {}).get("normal") or {}
    lines = ["紫卡洗卡词条推荐 · %s（倾向 ×%.1f）" % (data.get("weapon"), data.get("disp") or 1.0),
             "面板：暴击%s / 暴伤%s / 触发%s / %s%s / 基伤%s（%s）" % (
                 p.get("cc"), p.get("cm"), p.get("status"),
                 "攻速" if data.get("is_melee") else "射速", p.get("fr"),
                 p.get("base"), p.get("comp"))]
    ke = data.get("kill_eff") or {}
    if ke.get("pos"):
        lines.append("【击杀效率·正面】" + " / ".join(
            "%s +%.1f%%" % (x["zh"], x["gain"]) for x in ke["pos"]))
    if ke.get("neg"):
        lines.append("【击杀效率·推荐负面】" + " / ".join(ke["neg"]))
    mk = data.get("market") or {}
    if mk.get("ok"):
        lines.append("【市场价差】基准中位 %sp（%d 条挂牌）" % (
            _fm(mk.get("base_med")), mk.get("n_base", 0)))
        for x in (mk.get("pos") or [])[:8]:
            lines.append("  [%s]%s %+.1fp / %+.1f%%" % (x["tier"], x["zh"], x["delta"], x["pct"]))
        for tag, key in (("推荐负面", "rec"), ("普通负面", "ord"), ("存疑", "doubt")):
            rows = (mk.get("neg") or {}).get(key) or []
            if rows:
                lines.append("  %s：%s" % (tag, " / ".join(
                    "%s %+.1fp/%+.1f%%" % (x["zh"], x["delta"], x["pct"]) for x in rows)))
        if mk.get("skipped"):
            lines.append("  未计入：" + "、".join(mk["skipped"][:6]))
    else:
        lines.append("市场价差：" + (data.get("market_err") or "不可用"))
    lines.append("（价差=带该词条与不带该词条的挂牌中位差，非整卡售价；"
                 "击杀收益=满 roll 裸面板边际 DPS）")
    return "\n".join(lines)
