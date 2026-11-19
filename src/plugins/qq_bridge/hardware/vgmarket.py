# -*- coding: utf-8 -*-
"""hardware.vgmarket —— VRAMGlass 行情口径层（总览 / 单型号 payload）。

只做「数据 → 卡面 payload」的口径转换，不碰 nonebot / 文件写 / 旧模块状态。
所有涨跌来自 VRAMGlass 的真实序列或首页 24h 字段，绝不臆造；
价格口径在 payload 里显式标注（在售挂单中位价，非成交价），由模板印到卡面。

两视图（与 pic/templates/hw_market.py 的 view 分支对齐）：
  * ``vg_overview``：闲鱼二手全型号 24h 涨跌家数 + 涨跌榜（首页一次请求）；
  * ``vg_model``：单型号三市场（闲鱼/eBay/日亚）中位价 + 6h/24h 涨跌 + 走势 + 区间。

为什么总览只做闲鱼：首页表格的 24h 涨跌字段就是闲鱼市场的；eBay/日亚
要逐型号抓详情才有，全市场逐个抓=几十次请求，既慢又打爆第三方。三市场
对比放在单型号卡（用户点名型号时才抓），既诚实又省。
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta

from . import vg

#: 口径文案（卡面页脚唯一来源）。「挂单中位价 / 非成交价」必须出现，禁止写成成交价。
SOURCE_NOTE = "VRAMGlass · 闲鱼/eBay/日亚在售挂单中位价（非成交价）"
MARKET_ORDER = ("xianyu", "ebay", "amazon_jp")
FLAT_EPS = 0.05   # 涨跌绝对占比小于 0.05% 视为持平


def _short_time(label: str) -> str:
    """时间轴短标签：去掉年份前缀，保留「11月19日 12:00」/「11-19 12:00」。

    ⚠️ 别用固定切片（曾写 ``p.label[5:16]``）：上游标签是中文格式
    ``2026年11月19日 12:00``，比 ISO 多一个「日」，盲切会把末尾的 0 吃掉，
    卡面显示成「11月19日 12:0」（2026-11-20 实拍抓到）。
    """
    s = str(label or "").strip()
    s = re.sub(r"^\d{4}\s*[年\-/.]\s*", "", s)
    return s or str(label or "")


def _pct(now: float, before: float | None) -> float | None:
    if not before:
        return None
    return (now - before) / before * 100.0


#: 总览只收录消费级显卡（DIY 装机视角），剔除计算卡/服务器卡/专业卡——
#: 否则 A100/Tesla/DGX 的暴涨暴跌会霸榜，跟「装机行情」无关（实测踩坑）。
_CONSUMER_PREFIX = ("GeForce ", "Radeon RX ", "Arc ", "Intel Arc ")

#: 专业卡/工作站卡标记（命中即剔除）。2026-11-20 实拍抓到漏网：
#:   Radeon AI PRO R9700 / Radeon PRO W7900 / Arc Pro B60·B65·B70 / RTX 6000 Ada / RTX A6000
#: —— 旧实现只在**前两个 token** 里查 "PRO"，且前缀表里有裸 ``Radeon ``/``Arc ``，
#:   于是这些专业卡全被当成消费级放进「入门/主力/旗舰」分层，榜单一失真。
#: 判定改为：任意位置出现 PRO/ADA、或形如 ``RTX A6000`` 的字母+数字专业型号，一律剔除。
_DENY_TOKENS = ("PRO", "ADA")
_DENY_RE = re.compile(r"^RTX\s+[A-Z]\d{3,4}\b")


def _is_consumer(row: vg.Row) -> bool:
    n = str(row.name or "").strip()
    up = n.upper()
    toks = up.replace("-", " ").replace("_", " ").split()
    if any(t in _DENY_TOKENS for t in toks):
        return False
    if _DENY_RE.match(up):
        return False
    if any(n.startswith(p) for p in _CONSUMER_PREFIX):
        return True
    # RTX / RX / Arc 系列的标准写法（去掉品牌前缀后仍以这些开头）
    return up.startswith(("RTX ", "RX ", "ARC "))


# ---------------------------------------------------------------------------
# 总览：闲鱼二手全型号（首页一次请求）
# ---------------------------------------------------------------------------
def overview_payload(listing: vg.Listing | None = None,
                     book: vg.IndexBook | None = None) -> dict:
    rows_all = (listing or vg.fetch_listing()).rows
    rows = [r for r in rows_all if _is_consumer(r)]
    buckets = []
    movers = []
    up = [r for r in rows if r.change_pct is not None and r.change_pct > FLAT_EPS]
    down = [r for r in rows if r.change_pct is not None and r.change_pct < -FLAT_EPS]
    flat = [r for r in rows if r.change_pct is None or abs(r.change_pct) <= FLAT_EPS]
    buckets.append({
        "name": "闲鱼二手", "tracked": len(rows),
        "up": len(up), "down": len(down), "flat": len(flat),
        "asof": vg.now().strftime("%m-%d %H:%M"),
    })
    for r in rows:
        if r.change_pct is None:
            continue
        movers.append({
            "label": _short(r.name), "full": r.name,
            "prev": round(r.price / (1 + r.change_pct / 100.0)) if r.change_pct != -100 else 0,
            "latest": r.price, "pct": r.change_pct,
            "unit": "元", "samples": r.samples, "trend": r.trend,
        })
    gain = sorted([m for m in movers if m["pct"] > FLAT_EPS],
                  key=lambda m: m["pct"], reverse=True)[:3]
    lose = sorted([m for m in movers if m["pct"] < -FLAT_EPS],
                  key=lambda m: m["pct"])[:3]
    # 大盘指数（视频「显卡价格指数」同款）：抓不到只标不可用，绝不让行情榜失败
    indices = []
    idx_updated = ""
    if book is None:
        try:
            book = vg.fetch_indices()
        except vg.VgError:
            book = None
    if book:
        idx_updated = book.updated
        indices = [{"name": i.name, "value": i.value, "pct": i.change_pct,
                    "base": i.base, "base_date": i.base_date} for i in book.items]

    return {
        "view": "vg_overview",
        "asof": vg.now().strftime("%Y-%m-%d %H:%M"),
        "market": "闲鱼二手",
        "buckets": buckets,
        "gainers": gain, "losers": lose,
        "tracked": len(rows),
        "indices": indices,
        "idx_updated": idx_updated,
        "notes": ["价格取在售挂单中位价，非已成交价；样本数见每行"],
        "source": SOURCE_NOTE,
    }


# ---------------------------------------------------------------------------
# 单型号：三市场中位价 + 6h/24h 涨跌 + 走势
# ---------------------------------------------------------------------------
def _short(name: str) -> str:
    """卡面短名：去掉 GeForce / Radeon 等前缀，保留型号主体。"""
    s = str(name or "").strip()
    for p in ("GeForce ", "Radeon ", "AMD ", "NVIDIA ", "Intel "):
        if s.startswith(p):
            s = s[len(p):]
    return s


def _point_back_24h(points: list[vg.Point], last: vg.Point) -> vg.Point | None:
    """找 24 小时前最接近的价格点（6 小时粒度，容差 ±3h）。"""
    try:
        t_last = datetime.fromisoformat(last.t.replace("Z", "+00:00"))
    except ValueError:
        return None
    target = t_last - timedelta(hours=24)
    best, best_gap = None, 1e18
    for p in points:
        try:
            t = datetime.fromisoformat(p.t.replace("Z", "+00:00"))
        except ValueError:
            continue
        gap = abs((t - target).total_seconds())
        if gap < best_gap:
            best, best_gap = p, gap
    return best if best_gap <= 3 * 3600 else None


def _market_block(detail: vg.Detail | None, market: str = "") -> dict:
    if detail is None:
        return {"key": market, "label": vg.MARKETS.get(market, market),
                "price": None, "pct6h": None, "pct24h": None,
                "samples_note": "暂无报价"}
    pts = detail.points
    last = pts[-1]
    prev = pts[-2] if len(pts) >= 2 else None
    back24 = _point_back_24h(pts, last)
    return {
        "key": detail.market,
        "label": detail.market_label,
        "price": detail.price_cny,
        "pct6h": _pct(last.cny, prev.cny) if prev else None,
        "pct24h": _pct(last.cny, back24.cny) if back24 else None,
        "samples_note": "%d 个价格点" % len(pts),
    }


def model_payload(row: vg.Row, markets: tuple[str, ...] = MARKET_ORDER) -> dict:
    """抓单型号三市场详情，产出卡面 payload。无报价的市场返回 None 块（如实呈现）。"""
    blocks = {}
    primary: vg.Detail | None = None
    for m in markets:
        try:
            d = vg.fetch_detail(row.path, m)
        except vg.VgError:
            d = None
        blocks[m] = _market_block(d, m)
        if m == vg.DEFAULT_MARKET:
            primary = d

    # 主走势默认闲鱼（样本最全）；闲鱼无数据时退回第一个有数据的市场
    chart_src = primary
    if chart_src is None:
        for m in markets:
            # 重新取一次详情对象（market_block 只留了摘要），命中缓存
            chart_src = vg.fetch_detail(row.path, m)
            if chart_src:
                break

    chart, low, high, low_t, high_t = [], None, None, "", ""
    if chart_src:
        pts = chart_src.points[-60:]  # 最近 60 个点（≈15 天）
        lo = min(pts, key=lambda p: p.cny)
        hi = max(pts, key=lambda p: p.cny)
        low, high = lo.cny, hi.cny
        low_t, high_t = lo.label, hi.label
        span = len(pts) - 1
        chart = [{"t": (i / span if span else 0.5), "p": p.cny,
                  "m": _short_time(p.label)} for i, p in enumerate(pts)]
        cur = pts[-1].cny
        cur_detail = chart_src
    else:
        cur = row.price
        cur_detail = None

    main = blocks.get(vg.DEFAULT_MARKET) or next(
        (b for b in blocks.values() if b.get("price")), {})
    vs_low = _pct(cur, low) if low else None

    return {
        "view": "vg_model",
        "label": _short(row.name),
        "full": row.name,
        "asof": vg.now().strftime("%Y-%m-%d %H:%M"),
        "bucket_name": "显卡",  # VRAMGlass 当前收录以显卡为主（含部分计算卡）
        "sub": "在售挂单中位价 · 非成交价",
        "unit": "元",
        "latest": main.get("price"),
        "latest_month": (cur_detail.fetched_at[5:16] if cur_detail else ""),
        "pct": main.get("pct24h"),
        "pct6h": main.get("pct6h"),
        "pct24h": main.get("pct24h"),
        "low": low, "high": high,
        "low_month": low_t[5:16], "high_month": high_t[5:16],
        "vs_low_pct": vs_low,
        "chart": chart,
        "markets": [blocks[m] for m in markets if blocks.get(m)],
        "chart_market": (chart_src.market_label if chart_src else ""),
        "hist_days": len(chart_src.points) if chart_src else 0,
        "source": SOURCE_NOTE,
        "samples_note": (cur_detail and "%d 个价格点" % len(cur_detail.points)) or "暂无报价",
    }
