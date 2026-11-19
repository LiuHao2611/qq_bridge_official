# -*- coding: utf-8 -*-
"""hw_market_vg —— DIY 硬件行情卡（VRAMGlass 新数据源视图）。

两视图：
  * ``view="vg_overview"`` 闲鱼二手全型号 24h 涨跌总览（涨/跌/平 + 涨跌榜 + sparkline）；
  * ``view="vg_model"``    单型号三市场（闲鱼/eBay/日亚）中位价对比 + 走势 + 区间。

口径铁律：价格是「在售挂单中位价，非成交价」，三市场分列，页脚必现来源；
价格数字来自 vgmarket payload，模板不臆造、不改写涨跌符号。
颜色遵循 A 股习惯：涨=红(t.err)、跌=绿(t.ok)、平=弱文字。

度量纪律：所有文本先测宽高再落笔（Canvas.text_size），build(boxes=...) 把每个
叶子真实包围盒登记，供布局自检；坐标先算（_overview_layout/_model_layout）后画。
"""
from __future__ import annotations

from ..canvas import Canvas
from ..theme import Theme
from .. import decor, widgets as w

FS_TITLE = 40
FS_SUB = 16
FS_SEC = 21
FS_ROW = 21
FS_PCT = 23
FS_PRICE = 16
FS_FOOT = 13
FS_PANEL_NAME = 18
FS_NUM = 30

PAD = 24
GAP_M = 12
GAP_L = 20
PANEL_H = 116
ROW_H = 52
SEC_TO_ROW = 26
CHART_H = 190
IDX_H = 104   # 大盘指数条高度（视频「显卡价格指数」同款）


def _tb(pos, text, size, role="regular", anchor="lm"):
    tw, th = Canvas.text_size(str(text), size, role)
    x, y = pos
    if anchor == "lm":
        return (x, y - th // 2, x + tw, y + th // 2)
    if anchor == "rm":
        return (x - tw, y - th // 2, x, y + th // 2)
    if anchor == "mm":
        return (x - tw // 2, y - th // 2, x + tw // 2, y + th // 2)
    return (x, y, x + tw, y + th)


def _rec(boxes, key, box):
    if boxes is not None:
        boxes.append((key, int(box[0]), int(box[1]), int(box[2]), int(box[3])))


def _money(v):
    if v is None:
        return "—"
    return "¥" + "{:,.0f}".format(float(v))


def _pct(p):
    return "—" if p is None else "%+.1f%%" % p


def _tone(p, t):
    if p is None or abs(p) < 0.05:
        return t.text_sub
    return t.err if p > 0 else t.ok


def _arrow(p):
    if p is None or abs(p) < 0.05:
        return "·"
    return "▲" if p > 0 else "▼"


# ---------------------------------------------------------------------------
# 总览布局
# ---------------------------------------------------------------------------
def _overview_layout(pad, n_up, n_down, has_index):
    y = pad + 42
    L = {"title": y}
    y += 38
    L["sub"] = y
    y += 20 + GAP_M
    L["indices"] = y if has_index else None
    if has_index:
        y += IDX_H + GAP_M
    L["panels"] = y
    y += PANEL_H + GAP_L
    L["sec_up"] = y
    L["rows_up"] = y + SEC_TO_ROW
    y = L["rows_up"] + n_up * ROW_H + 20
    L["sec_down"] = y
    L["rows_down"] = y + SEC_TO_ROW
    y = L["rows_down"] + n_down * ROW_H
    L["foot"] = y + 24
    L["h"] = L["foot"] + 28 + pad
    return L


def _spark(c, t, box, trend, color, boxes, key):
    """30 天走势缩略折线（首页 polyline 的 y 序列，越小价越高）。"""
    x0, y0, x1, y1 = box
    if len(trend) < 2:
        return
    lo, hi = min(trend), max(trend)
    span = (hi - lo) or 1.0
    pts = []
    for i, v in enumerate(trend):
        gx = x0 + int(i / (len(trend) - 1) * (x1 - x0))
        gy = y0 + int((v - lo) / span * (y1 - y0))
        pts.append((gx, gy))
    prev = None
    for cur in pts:
        if prev:
            c.line((prev[0], prev[1], cur[0], cur[1]), fill=color, width=2)
        prev = cur
    _rec(boxes, key, box)


def _index_bar(c, t, box, indices, updated, boxes):
    """大盘价格指数条（视频「显卡价格指数」同款）：四个价位档，各显示当前点位+24h。"""
    x0, y0, x1, y1 = box
    c.rounded_rect(box, radius=t.r_ctrl, fill=t.track)
    _rec(boxes, "idx.bar", box)
    head = "显卡大盘指数" + (" · " + updated if updated else "")
    c.text((x0 + 14, y0 + 18), head, size=t.fs(15), color=t.accent, role="bold", anchor="lm")
    _rec(boxes, "idx.head", _tb((x0 + 14, y0 + 18), head, t.fs(15), "bold"))
    base_note = "基期 100 · 6h 更新"
    c.text((x1 - 14, y0 + 18), base_note, size=t.fs(12), color=t.text_weak, anchor="rm")
    _rec(boxes, "idx.base", _tb((x1 - 14, y0 + 18), base_note, t.fs(12), "rm"))
    n = max(1, len(indices))
    gap = 8
    iw = (x1 - x0 - 28 - gap * (n - 1)) // n
    for i, ix in enumerate(indices):
        ix0 = x0 + 14 + i * (iw + gap)
        name = str(ix.get("name") or "")
        c.text((ix0, y0 + 46), name, size=t.fs(14), color=t.text_sub, anchor="lm")
        _rec(boxes, "idx.%d.name" % i, _tb((ix0, y0 + 46), name, t.fs(14)))
        val = "%.2f" % float(ix["value"])
        c.text((ix0, y0 + 76), val, size=t.fs(26), color=t.text, role="bold", anchor="lm")
        _rec(boxes, "idx.%d.val" % i, _tb((ix0, y0 + 76), val, t.fs(26), "bold"))
        pct = _pct(ix.get("pct"))
        pc = _tone(ix.get("pct"), t)
        pw = Canvas.text_width(val, t.fs(26), "bold")
        c.text((ix0 + pw + 14, y0 + 80), pct, size=t.fs(14), color=pc, role="bold", anchor="lm")
        _rec(boxes, "idx.%d.pct" % i, _tb((ix0 + pw + 14, y0 + 80), pct, t.fs(14), "bold"))


def _panel_overview(c, t, box, b, boxes):
    x0, y0, x1, y1 = box
    c.rounded_rect(box, radius=t.r_ctrl, fill=t.track)
    _rec(boxes, "stat", box)
    name = "%s · 24h" % b["name"]
    c.text((x0 + 14, y0 + 24), name, size=t.fs(FS_PANEL_NAME), color=t.accent,
           role="bold", anchor="lm")
    _rec(boxes, "stat.name", _tb((x0 + 14, y0 + 24), name, t.fs(FS_PANEL_NAME), "bold"))
    cx, ny = x0 + 14, y0 + 64
    for num, tag, color in ((b["up"], "涨", t.err), (b["down"], "跌", t.ok),
                            (b["flat"], "平", t.text_sub)):
        s = str(num)
        c.text((cx, ny), s, size=t.fs(FS_NUM), color=color, role="bold", anchor="lm")
        _rec(boxes, "stat.%s" % tag, _tb((cx, ny), s, t.fs(FS_NUM), "bold"))
        cx += Canvas.text_width(s, t.fs(FS_NUM), "bold") + 2
        c.text((cx, ny + 8), tag, size=t.fs(15), color=t.text_sub, anchor="lm")
        _rec(boxes, "stat.%s.tag" % tag, _tb((cx, ny + 8), tag, t.fs(15)))
        cx += Canvas.text_width(tag, t.fs(15)) + 12
    cover = "跟踪 %d 款消费级显卡 · %s 抓取" % (b["tracked"], b["asof"])
    c.text((x0 + 14, y1 - 18), cover, size=t.fs(13), color=t.text_weak, anchor="lm")
    _rec(boxes, "stat.cover", _tb((x0 + 14, y1 - 18), cover, t.fs(13)))


def _mover_row(c, t, x, y, width, m, up, boxes, key):
    color = t.err if up else t.ok
    c.rounded_rect((x, y - 14, x + 4, y + 14), radius=2, fill=color)
    _rec(boxes, key + ".bar", (x, y - 14, x + 4, y + 14))
    label = Canvas.ellipsis(str(m["label"]), t.fs(FS_ROW), "bold", int(width * 0.30))
    c.text((x + 14, y), label, size=t.fs(FS_ROW), color=t.text, role="bold", anchor="lm")
    _rec(boxes, key + ".label", _tb((x + 14, y), label, t.fs(FS_ROW), "bold"))
    # sparkline 放型号名与百分比之间，右端留足间距给百分比文字（否则压线重叠）
    sx0, sx1 = x + int(width * 0.33), x + int(width * 0.53)
    _spark(c, t, (sx0, y - 9, sx1, y + 9), m.get("trend") or [], color, boxes, key + ".spark")
    pct = "%s %s" % (_arrow(m["pct"]), _pct(m["pct"]))
    px = x + int(width * 0.72)
    c.text((px, y), pct, size=t.fs(FS_PCT), color=color, role="bold", anchor="rm")
    _rec(boxes, key + ".pct", _tb((px, y), pct, t.fs(FS_PCT), "bold", "rm"))
    price = _money(m["latest"])
    c.text((x + width, y), price, size=t.fs(FS_PRICE + 2), color=t.text, role="bold", anchor="rm")
    _rec(boxes, key + ".price", _tb((x + width, y), price, t.fs(FS_PRICE + 2), "bold", "rm"))
    sub = "%d 条挂单" % int(m.get("samples") or 0)
    c.text((x + 14, y + 17), sub, size=t.fs(12), color=t.text_weak, anchor="lm")
    _rec(boxes, key + ".samples", _tb((x + 14, y + 17), sub, t.fs(12)))


def _section(c, t, x, y, width, text, boxes, key):
    c.rounded_rect((x, y - 9, x + 4, y + 9), radius=2, fill=t.accent)
    c.text((x + 12, y), text, size=t.fs(FS_SEC), color=t.accent, role="bold", anchor="lm")
    _rec(boxes, key + ".title", _tb((x + 12, y), text, t.fs(FS_SEC), "bold"))
    lx = x + 12 + Canvas.text_width(text, t.fs(FS_SEC), "bold") + 12
    c.line((lx, y, x + width, y), fill=t.divider, width=1)
    _rec(boxes, key + ".line", (lx, y - 1, x + width, y + 1))


def build_overview(t: Theme, payload: dict, width: int, boxes=None) -> Canvas:
    pad = int(getattr(t, "pad", PAD) or PAD)
    x0, x1 = pad * 2, width - pad * 2
    cw = x1 - x0
    gain = payload.get("gainers") or []
    lose = payload.get("losers") or []
    indices = payload.get("indices") or []
    bucket = payload["buckets"][0]
    L = _overview_layout(pad, len(gain), len(lose), bool(indices))
    c = Canvas(width, L["h"], t.bg, theme=t)
    decor.background(c, t, tpl="hw")
    w.card(c, t, (pad, pad, width - pad, L["h"] - pad))

    c.text((x0, L["title"]), "DIY 硬件行情 · 每日", size=t.fs(FS_TITLE), color=t.text,
           role="bold", anchor="lm")
    _rec(boxes, "hdr.title", _tb((x0, L["title"]), "DIY 硬件行情 · 每日", t.fs(FS_TITLE), "bold"))
    asof = str(payload.get("asof") or "")
    c.text((x1, L["title"]), asof[5:], size=t.fs(22), color=t.accent, role="bold", anchor="rm")
    _rec(boxes, "hdr.asof", _tb((x1, L["title"]), asof[5:], t.fs(22), "bold", "rm"))

    sub = "闲鱼二手 · 在售挂单中位价（非成交价）· 6 小时更新"
    sub = Canvas.ellipsis(sub, t.fs(FS_SUB), "regular", cw)
    c.text((x0, L["sub"]), sub, size=t.fs(FS_SUB), color=t.text_sub, anchor="lm")
    _rec(boxes, "hdr.sub", _tb((x0, L["sub"]), sub, t.fs(FS_SUB)))

    if indices and L["indices"] is not None:
        _index_bar(c, t, (x0, L["indices"], x1, L["indices"] + IDX_H),
                   indices, payload.get("idx_updated") or "", boxes)

    _panel_overview(c, t, (x0, L["panels"], x1, L["panels"] + PANEL_H), bucket, boxes)

    _section(c, t, x0, L["sec_up"], cw, "24h 涨幅榜", boxes, "sec.up")
    for i, m in enumerate(gain):
        _mover_row(c, t, x0, L["rows_up"] + i * ROW_H + ROW_H // 2, cw, m, True,
                   boxes, "up.%d" % i)
    _section(c, t, x0, L["sec_down"], cw, "24h 跌幅榜", boxes, "sec.down")
    for i, m in enumerate(lose):
        _mover_row(c, t, x0, L["rows_down"] + i * ROW_H + ROW_H // 2, cw, m, False,
                   boxes, "down.%d" % i)

    foot = Canvas.ellipsis(payload.get("source") or "VRAMGlass · 在售挂单中位价", t.fs(FS_FOOT), "regular", cw)
    c.text((x0, L["foot"]), foot, size=t.fs(FS_FOOT), color=t.text_weak, anchor="lm")
    _rec(boxes, "footer", _tb((x0, L["foot"]), foot, t.fs(FS_FOOT)))
    return c


# ---------------------------------------------------------------------------
# 单型号
# ---------------------------------------------------------------------------
def _model_layout(pad, n_markets):
    y = pad + 42
    L = {"title": y}
    y += 38
    L["sub"] = y
    y += 20 + GAP_M
    L["price"] = y
    L["price_note"] = y + 62
    L["markets"] = y + 104
    L["chart"] = L["markets"] + 118 + GAP_L
    L["metric"] = L["chart"] + CHART_H + GAP_M
    L["foot"] = L["metric"] + 52
    L["h"] = L["foot"] + 28 + pad
    return L


def _market_card(c, t, box, b, boxes, idx):
    x0, y0, x1, y1 = box
    c.rounded_rect(box, radius=t.r_ctrl, fill=t.track)
    _rec(boxes, "mkt.%d" % idx, box)
    label = b.get("label") or "—"
    c.text((x0 + 12, y0 + 22), label, size=t.fs(16), color=t.accent, role="bold", anchor="lm")
    _rec(boxes, "mkt.%d.label" % idx, _tb((x0 + 12, y0 + 22), label, t.fs(16), "bold"))
    price = _money(b.get("price"))
    c.text((x0 + 12, y0 + 56), price, size=t.fs(26), color=t.text, role="bold", anchor="lm")
    _rec(boxes, "mkt.%d.price" % idx, _tb((x0 + 12, y0 + 56), price, t.fs(26), "bold"))
    p24 = b.get("pct24h")
    p6 = b.get("pct6h")
    c.text((x0 + 12, y0 + 86), "24h %s" % _pct(p24), size=t.fs(14), color=_tone(p24, t), anchor="lm")
    _rec(boxes, "mkt.%d.p24" % idx, _tb((x0 + 12, y0 + 86), "24h %s" % _pct(p24), t.fs(14)))
    c.text((x1 - 12, y0 + 86), "6h %s" % _pct(p6), size=t.fs(14), color=_tone(p6, t), anchor="rm")
    _rec(boxes, "mkt.%d.p6" % idx, _tb((x1 - 12, y0 + 86), "6h %s" % _pct(p6), t.fs(14), "rm"))


def _chart(c, t, box, chart, color, boxes):
    x0, y0, x1, y1 = box
    c.rounded_rect(box, radius=t.r_ctrl, fill=t.track)
    _rec(boxes, "chart", box)
    ix0, ix1, iy0, iy1 = x0 + 16, x1 - 16, y0 + 30, y1 - 28
    if len(chart) < 2:
        msg = "历史不足 · 暂无法绘制走势"
        c.text(((x0 + x1) // 2, (y0 + y1) // 2), msg, size=t.fs(16), color=t.text_weak, anchor="mm")
        _rec(boxes, "chart.empty", _tb(((x0 + x1) // 2, (y0 + y1) // 2), msg, t.fs(16), "mm"))
        return
    prices = [p["p"] for p in chart]
    lo, hi = min(prices), max(prices)
    span = (hi - lo) or 1.0
    for k in (0.0, 0.5, 1.0):
        gy = int(iy1 - k * (iy1 - iy0))
        c.line((ix0, gy, ix1, gy), fill=t.divider, width=1)

    def pt(p):
        return (ix0 + int(p["t"] * (ix1 - ix0)),
                iy1 - int((p["p"] - lo) / span * (iy1 - iy0)))

    prev = None
    for p in chart:
        cur = pt(p)
        if prev:
            c.line((prev[0], prev[1], cur[0], cur[1]), fill=color, width=3)
        prev = cur
    lx, ly = pt(chart[-1])
    c.ellipse((lx - 5, ly - 5, lx + 5, ly + 5), fill=color)
    lab_hi, lab_lo = _money(hi), _money(lo)
    c.text((ix0, y0 + 16), lab_hi, size=t.fs(13), color=t.text_weak, anchor="lm")
    c.text((ix0, iy1 + 14), lab_lo, size=t.fs(13), color=t.text_weak, anchor="lm")
    _rec(boxes, "chart.hi", _tb((ix0, y0 + 16), lab_hi, t.fs(13)))
    _rec(boxes, "chart.lo", _tb((ix0, iy1 + 14), lab_lo, t.fs(13)))
    # 区间起止：右锚在绘图区右边界，按可用宽度省略，防长标签撞上左侧 Y 轴刻度（lab_lo）
    rng = "%s → %s" % (chart[0]["m"], chart[-1]["m"])
    rng_max = max(40, (ix1 - ix0) - Canvas.text_width(lab_lo, t.fs(13), "regular") - 18)
    rng = Canvas.ellipsis(rng, t.fs(13), "regular", rng_max)
    c.text((ix1, iy1 + 14), rng, size=t.fs(13), color=t.text_weak, anchor="rm")
    _rec(boxes, "chart.range", _tb((ix1, iy1 + 14), rng, t.fs(13), "rm"))


def _metric(c, t, x, y, width, label, value, color, boxes, key):
    c.text((x, y), label, size=t.fs(14), color=t.text_weak, anchor="lm")
    _rec(boxes, key + ".label", _tb((x, y), label, t.fs(14)))
    value = Canvas.ellipsis(value, t.fs(18), "bold", width - 8)
    c.text((x, y + 22), value, size=t.fs(18), color=color, role="bold", anchor="lm")
    _rec(boxes, key + ".value", _tb((x, y + 22), value, t.fs(18), "bold"))


def build_model(t: Theme, payload: dict, width: int, boxes=None) -> Canvas:
    pad = int(getattr(t, "pad", PAD) or PAD)
    x0, x1 = pad * 2, width - pad * 2
    cw = x1 - x0
    markets = payload.get("markets") or []
    L = _model_layout(pad, len(markets))
    c = Canvas(width, L["h"], t.bg, theme=t)
    decor.background(c, t, tpl="hw")
    w.card(c, t, (pad, pad, width - pad, L["h"] - pad))

    c.text((x0, L["title"]), "DIY 硬件行情", size=t.fs(FS_TITLE), color=t.text, role="bold", anchor="lm")
    _rec(boxes, "hdr.title", _tb((x0, L["title"]), "DIY 硬件行情", t.fs(FS_TITLE), "bold"))
    bucket = str(payload.get("bucket_name") or "")
    c.text((x1, L["title"]), bucket, size=t.fs(22), color=t.accent, role="bold", anchor="rm")
    _rec(boxes, "hdr.bucket", _tb((x1, L["title"]), bucket, t.fs(22), "bold", "rm"))

    sub = "%s · %s" % (payload.get("label") or "", payload.get("sub") or "")
    sub = Canvas.ellipsis(sub, t.fs(FS_SUB), "regular", cw)
    c.text((x0, L["sub"]), sub, size=t.fs(FS_SUB), color=t.text_sub, anchor="lm")
    _rec(boxes, "hdr.sub", _tb((x0, L["sub"]), sub, t.fs(FS_SUB)))

    price = _money(payload.get("latest"))
    c.text((x0, L["price"]), price, size=t.fs(46), color=t.text, role="bold", anchor="la")
    _rec(boxes, "price", _tb((x0, L["price"]), price, t.fs(46), "bold", "la"))
    pnote = "闲鱼二手中位价 · %s 更新 · %s" % (payload.get("asof", "")[5:], payload.get("samples_note") or "")
    pnote = Canvas.ellipsis(pnote, t.fs(14), "regular", cw)
    c.text((x0, L["price_note"]), pnote, size=t.fs(14), color=t.text_sub, anchor="lm")
    _rec(boxes, "price.note", _tb((x0, L["price_note"]), pnote, t.fs(14)))

    n = max(1, len(markets))
    gap = GAP_M
    mw = (cw - gap * (n - 1)) // n
    for i, b in enumerate(markets):
        bx = x0 + i * (mw + gap)
        _market_card(c, t, (bx, L["markets"], bx + mw, L["markets"] + 118), b, boxes, i)

    _chart(c, t, (x0, L["chart"], x1, L["chart"] + CHART_H),
           payload.get("chart") or [], _tone(payload.get("pct"), t), boxes)

    col = cw // 3
    _metric(c, t, x0, L["metric"], col, "24h 涨跌（闲鱼）",
            _pct(payload.get("pct24h")), _tone(payload.get("pct24h"), t), boxes, "m.24h")
    _metric(c, t, x0 + col, L["metric"], col, "较区间最低点",
            _pct(payload.get("vs_low_pct")), t.text, boxes, "m.vslow")
    _metric(c, t, x0 + col * 2, L["metric"], col, "区间 高 / 低",
            "%s / %s" % (_money(payload.get("high")), _money(payload.get("low"))),
            t.text, boxes, "m.range")

    foot = Canvas.ellipsis(payload.get("source") or "VRAMGlass · 在售挂单中位价", t.fs(FS_FOOT), "regular", cw)
    c.text((x0, L["foot"]), foot, size=t.fs(FS_FOOT), color=t.text_weak, anchor="lm")
    _rec(boxes, "footer", _tb((x0, L["foot"]), foot, t.fs(FS_FOOT)))
    return c


def build(t: Theme, payload: dict, width: int, boxes=None) -> Canvas:
    v = payload.get("view") if payload else None
    if v == "vg_overview":
        return build_overview(t, payload, width, boxes=boxes)
    if v == "vg_model":
        return build_model(t, payload, width, boxes=boxes)
    raise ValueError("hw_market_vg.build 未知 view：%r" % (v,))
