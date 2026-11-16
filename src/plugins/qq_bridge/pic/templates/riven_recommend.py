# -*- coding: utf-8 -*-
"""pic/templates/riven_recommend.py —— 紫卡洗卡词条推荐卡（照 Rabbit 参考卡逐区复刻）。

参考卡 1195×1920（宽高比 ≈ 0.62）。本模板设计空间 BASE_W=600、按 width 等比缩放，
使版式比例与参考卡一致。配色（实测）：
    底色 #F3F1ED · 主文字 #1A191C · 金 #9B7414 · 品牌紫 #735787
    表头蓝 #3A6EA5 · 面板底 #F5F8FC / 边 #CBD8E8 · 警示红 #C0392B / 底 #FBE9E7
    溢价红 #A33B2E · 折价绿 #3F7A5A · 次级灰 #7F7E7A · 弱灰 #9C9890

版式（自上而下，与参考卡逐区对应）：
    [标题]   紫色品牌标 + 「紫卡洗卡词条推荐」（居中）
    [警示]   浅红底红框红字「【注意】洗卡先问大佬……」
    [面板]   「执法者 可能是暴击流武器」+ 右灰「基础面板 · 未装 MOD · 未计进化」
             蓝字表头 模式/暴击率/暴击倍率/触发率/攻速/基础伤害·击；普通行（灵化再一行）
             下方伤害构成
    [提示]   浅蓝底蓝左条「* 当前武器有配卡指南，使用"配卡指南 执法者"查看」
    [击杀]   魔法棒图标 + 「基于击杀效率计算的推荐词条」
             左「正面词条」(词条|收益) top3，右「推荐负面词条」(词条|伤害影响) top3
    [市场]   三栏：左「正面词条」[T0/T1/T2] + 价差 p/%；中「推荐 / 普通负面」
             （子块：推荐负面 / 普通负面 / 普通负面（存疑））；右「致命项」（红）
    [脚注]   6 条 * 说明
    [页脚]   「PC Warframe Rabbit · 需用右键/长按提取文字」+ 右 Q 版插画

数据纪律：价差来自真实 WFM 挂牌（带词条池 vs 基准池，同分位中位差），
击杀收益来自裸面板边际 DPS（含近战连击模型）；接口失败/样本不足如实标注，
绝不填假数、绝不写「暂无」糊过去。
"""
from __future__ import annotations

import logging

from .. import fonts
from ..canvas import Canvas
from ..theme import Theme

logger = logging.getLogger("qq_bridge")

BASE_W = 600.0
DESIGN_H = 2600.0

C_BG = "#F3F1ED"
C_INK = "#1A191C"
C_GOLD = "#9B7414"
C_BRAND = "#735787"
C_BLUE = "#3A6EA5"
C_PANEL = "#F5F8FC"
C_PANEL_ED = "#CBD8E8"
C_RED = "#C0392B"
C_REDBG = "#FBE9E7"
C_RED2 = "#A33B2E"
C_GREEN = "#3F7A5A"
C_GRAY = "#7F7E7A"
C_SUB = "#9C9890"

PAD = 16.0
#: 三栏列网格（一经定义全卡不变；见 card-design「列网格」）
COL1_X, COL1_W = PAD, 214.0
COL2_X, COL2_W = 244.0, 190.0
COL3_X, COL3_W = 448.0, 136.0
#: 行高/字号 token（4 的倍数）
LH_ROW = 19.0
FS_NAME, FS_VAL, FS_HEAD = 11.6, 10.6, 10.0
#: 三栏行数上限（避免长词条把卡片拉爆；参考卡分别是 8 / 4+9 / 8）。
#: 正面必须 ≥ 后端候选数（``riven_recommend._QUERY_POS = 9``）：少画一行就会
#: 出现「脚注有效词条 9 条 / 表格 8 行」的对不上（2026-11-16 实拍发现）。
_MAX_ROWS_POS, _MAX_ROWS_NEG, _MAX_ROWS_DOUBT, _MAX_ROWS_DEAD = 9, 4, 9, 8


def _font(role: str, size: int):
    return fonts.load(role, max(4, int(size)))


def _fm(v) -> str:
    """价差显示：整数不带小数位，非整数保留一位。"""
    try:
        f = float(v or 0)
    except (TypeError, ValueError):
        return "0"
    return ("%.0f" % f) if abs(f - round(f)) < 1e-9 else ("%.1f" % f)


def build(t: Theme, data: dict | None = None, width: int | None = None, **kw) -> Canvas:
    data = data or {}
    width = int(width or getattr(t, "width", 800) or 800)
    S = width / BASE_W
    c = Canvas(width, int(DESIGN_H * S), C_BG, theme=t)
    d = c.draw

    def P(v: float) -> int:
        return int(round(v * S))

    def T(x, y, text, size, color, role="regular", anchor="ls"):
        d.text((P(x), P(y)), str(text), font=_font(role, size * S), fill=color, anchor=anchor)

    def tw(text, size, role="regular") -> float:
        return c.text_width(str(text), size * S, role) / S

    def wrap(text, size, role, max_w):
        return c.wrap(str(text), size * S, role, P(max_w))

    def fit(text, size, role, max_w) -> str:
        """单行截断（列内文本超宽用省略号，保证行高恒定、不挤压相邻列）。"""
        s = str(text)
        if tw(s, size, role) <= max_w:
            return s
        while s and tw(s + "…", size, role) > max_w:
            s = s[:-1]
        return s + "…"

    bot = [0.0]

    def mark(y):
        bot[0] = max(bot[0], float(y))

    # ---------- 错误态 ----------
    if data.get("status") != "ok":
        msg = data.get("msg") or "生成失败"
        _brand_title(c, P, T, d, tw)
        al = wrap(msg, 13, "bold", BASE_W - 2 * PAD - 26)[:4]
        ay = 58.0
        ah = 12.0 + len(al) * 20.0
        c.rounded_rect((P(PAD), P(ay), P(BASE_W - PAD), P(ay + ah)), radius=P(8),
                       fill=C_REDBG, outline=C_RED, width=max(1, P(1.2)))
        for i, ln in enumerate(al):
            T(PAD + 13, ay + 12 + i * 20, ln, 13, C_RED, "bold")
        _finish(c, P, ay + ah + 10, width)
        return c

    # ---------- [标题] ----------
    _brand_title(c, P, T, d, tw)
    mark(46)

    # ---------- [警示] ----------
    alert = ("【注意】洗卡先问大佬，不要直接按推荐顺序洗卡，搭配的战甲、武器流派不同，"
             "词条选择都会不同。")
    al = wrap(alert, 13, "bold", BASE_W - 2 * PAD - 26)
    ah = 14.0 + len(al) * 20.0 + 12.0
    ay = 56.0
    c.rounded_rect((P(PAD), P(ay), P(BASE_W - PAD), P(ay + ah)), radius=P(8),
                   fill=C_REDBG, outline=C_RED, width=max(1, P(1.2)))
    for i, ln in enumerate(al):
        T(PAD + 13, ay + 14 + i * 20, ln, 13, C_RED, "bold")
    mark(ay + ah + 12)

    # ---------- [武器面板] ----------
    panel = (data.get("panel") or {}).get("normal") or {}
    is_inc = bool(data.get("is_incarnon"))
    py = bot[0]
    ph = 104.0 + (24.0 if is_inc else 0.0)
    c.rounded_rect((P(PAD), P(py), P(BASE_W - PAD), P(py + ph)), radius=P(7),
                   fill=C_PANEL, outline=C_PANEL_ED, width=max(1, P(1.0)))

    wtxt = data.get("weapon") or "武器"
    desc = data.get("weapon_desc") or ""
    T(PAD + 12, py + 20, "%s %s" % (wtxt, desc), 14.5, C_INK, "bold")
    T(BASE_W - PAD - 12, py + 20, "基础面板 · 未装 MOD · 未计进化", 10.0, C_GRAY,
      "regular", anchor="rs")

    hy = py + 40
    cols = ["模式", "暴击率", "暴击倍率", "触发率",
            "攻速" if panel.get("is_melee") else "射速", "基础伤害/击"]
    mode_w = 56.0
    val_l = PAD + 12 + mode_w
    colw = (BASE_W - PAD - 12 - val_l) / 5.0
    T(PAD + 12, hy, cols[0], FS_HEAD, C_BLUE, "bold")
    for i, h in enumerate(cols[1:]):
        T(val_l + colw * i + colw / 2, hy, h, FS_HEAD, C_BLUE, "bold", anchor="ms")
    ry = hy + 21

    def _row(label, vals):
        nonlocal ry
        T(PAD + 12, ry, label, 12.0, C_INK, "regular")
        for i, v in enumerate(vals):
            T(val_l + colw * i + colw / 2, ry, v, 11.6, C_INK, "regular", anchor="ms")
        ry += 24

    _row("普通", [panel.get("cc", "—"), panel.get("cm", "—"), panel.get("status", "—"),
                  panel.get("fr", "—"), panel.get("base", "—")])
    inc = (data.get("panel") or {}).get("incarnon")
    if is_inc:
        _row("灵化", ([inc.get(k, "—") for k in ("cc", "cm", "status", "fr", "base")]
                      if inc else ["未收录"] * 5))

    T(PAD + 12, ry + 2, "普通：" + (panel.get("comp") or "—"), 10.6, C_GRAY, "regular")
    mark(py + ph + 10)

    # ---------- [配卡指南提示条] ----------
    gy = bot[0]
    tip = ('* 当前武器有配卡指南，使用"配卡指南 %s"查看' % wtxt)
    th = 26.0
    c.rounded_rect((P(PAD), P(gy), P(BASE_W - PAD), P(gy + th)), radius=P(5),
                   fill=C_PANEL, outline=C_PANEL_ED, width=max(1, P(1.0)))
    d.rectangle((P(PAD), P(gy + 4), P(PAD + 3), P(gy + th - 4)), fill=C_BLUE)
    T(PAD + 10, gy + th / 2 + 4, fit(tip, 10.4, "bold", BASE_W - 2 * PAD - 20),
      10.4, C_BLUE, "bold")
    mark(gy + th + 12)

    # ---------- [击杀效率] ----------
    ky = bot[0]
    _block_title(T, P, ky, "基于击杀效率计算的推荐词条")
    kb = ky + 28
    ke = data.get("kill_eff") or {}
    pos = ke.get("pos") or []
    negn = ke.get("neg") or []
    negv = ke.get("neg_loss") or []

    T(PAD, kb, "正面词条", 12.4, C_GOLD, "bold")
    T(PAD, kb + 18, "词条", FS_HEAD, C_GRAY, "regular")
    T(PAD + COL1_W, kb + 18, "收益", FS_HEAD, C_GRAY, "regular", anchor="rs")
    ly = kb + 34
    if pos:
        for i, it in enumerate(pos[:3]):
            T(PAD, ly, "[%d]%s" % (i + 1, it.get("zh", "")), FS_NAME, C_INK, "regular")
            T(PAD + COL1_W, ly, "+%.1f%%" % float(it.get("gain", 0.0)), FS_VAL,
              C_GREEN, "bold", anchor="rs")
            ly += LH_ROW
    else:
        for ln in wrap(ke.get("na") or "该武器暂无可用词条基准", 10.0, "regular",
                       COL1_W - 8)[:2]:
            T(PAD, ly, ln, 10.0, C_GRAY, "regular")
            ly += 16

    rx = 320.0
    rw = BASE_W - PAD - rx
    T(rx, kb, "推荐负面词条", 12.4, C_GOLD, "bold")
    T(rx, kb + 18, "词条", FS_HEAD, C_GRAY, "regular")
    T(rx + rw, kb + 18, "伤害影响", FS_HEAD, C_GRAY, "regular", anchor="rs")
    ny = kb + 34
    if negn:
        for i, z in enumerate(negn[:3]):
            T(rx, ny, "[%d]%s" % (i + 1, z), FS_NAME, C_INK, "regular")
            if i < len(negv):
                lv = float(negv[i])
                # 0 显示「0%」而不是「-0.0%」（浮点负零在卡面上很刺眼）
                txt = "0%" if abs(lv) < 0.05 else "-%.1f%%" % lv
                T(rx + rw, ny, txt, FS_VAL, C_GRAY, "regular", anchor="rs")
            ny += LH_ROW
    else:
        T(rx, ny, "—", FS_NAME, C_GRAY, "regular")
        ny += LH_ROW
    mark(max(ly, ny) + 10)

    # ---------- [市场价差] ----------
    my = bot[0]
    _block_title(T, P, my, "基于市场价差的推荐词条")
    mb = my + 28
    market = data.get("market") or {}

    def mkt_head(x, w, y, left="词条", right="挂牌价差"):
        """列头（**必须显式传 y**：早期版本用外层 ``mb`` 导致每个子块都画回区块顶部）。"""
        T(x, y, left, FS_HEAD, C_GRAY, "regular")
        T(x + w, y, right, FS_HEAD, C_GRAY, "regular", anchor="rs")
        return y + 17

    def mkt_row(x, w, y, name, price, name_color, price_color, bold=False, tag=None,
                tag_color=C_GOLD):
        """一行「左词条 + 右价差」。``tag``（[T0]/[T1]/[T2]）单独着色并参与度量。"""
        role = "bold" if bold else "regular"
        pw = tw(price, FS_VAL, "bold")
        tag_s = "[%s]" % tag if tag else ""
        tag_w = tw(tag_s, FS_NAME, "bold") if tag_s else 0.0
        nm = fit(name, FS_NAME, role, w - pw - tag_w - 9)
        if tag_s:
            T(x, y, tag_s, FS_NAME, tag_color, "bold")
        T(x + tag_w + 3, y, nm, FS_NAME, name_color, role)
        T(x + w, y, price, FS_VAL, price_color, "bold", anchor="rs")

    def price_of(it):
        return "%+.1fp/%+.0f%%" % (float(it.get("delta", 0)), float(it.get("pct", 0)))

    if not market.get("ok"):
        err = market.get("err") or "市场数据不可用"
        for ln in wrap(err, 10.6, "regular", BASE_W - 2 * PAD)[:3]:
            T(PAD, mb + 6, ln, 10.6, C_RED, "regular")
            mb += 16
        mark(mb + 16)
    else:
        posm = [x for x in (market.get("pos") or []) if x.get("tier") != "致命"]
        y1 = mb
        T(COL1_X, y1, "正面词条", 12.4, C_GOLD, "bold")
        y1 += 22
        y1 = mkt_head(COL1_X, COL1_W, y1)
        if posm:
            for it in posm[:_MAX_ROWS_POS]:
                mkt_row(COL1_X, COL1_W, y1, it.get("zh", ""), price_of(it),
                        C_INK, C_RED2, tag=it.get("tier"))
                y1 += LH_ROW
        else:
            T(COL1_X, y1, "无显著溢价词条", FS_NAME, C_GRAY, "regular")
            y1 += LH_ROW

        # 中栏：推荐 / 普通负面（含存疑子块）
        y2 = mb
        T(COL2_X, y2, "推荐 / 普通负面", 12.4, C_GOLD, "bold")
        y2 += 22
        groups = [("推荐负面", (market.get("neg") or {}).get("rec") or [], C_GREEN),
                  ("普通负面", (market.get("neg") or {}).get("ord") or [], C_INK)]
        for gname, rows, color in groups:
            if not rows:
                continue
            T(COL2_X, y2, gname, 10.8, C_GRAY, "bold")
            y2 += 21
            y2 = mkt_head(COL2_X, COL2_W, y2)
            for it in rows[:_MAX_ROWS_NEG]:
                mkt_row(COL2_X, COL2_W, y2, "-" + it.get("zh", ""), price_of(it), color, color)
                y2 += LH_ROW
            y2 += 10
        doubt = (market.get("neg") or {}).get("doubt") or []
        if doubt:
            T(COL2_X, y2, "普通负面（存疑）", 10.8, C_GRAY, "bold")
            y2 += 21
            y2 = mkt_head(COL2_X, COL2_W, y2)
            for it in doubt[:_MAX_ROWS_DOUBT]:
                mkt_row(COL2_X, COL2_W, y2, "-" + it.get("zh", ""), price_of(it), C_INK, C_INK)
                y2 += LH_ROW

        # 右栏：致命项（洗到也不加价的正面词条）
        y3 = mb
        T(COL3_X, y3, "致命项", 12.4, C_RED, "bold")
        y3 += 22
        y3 = mkt_head(COL3_X, COL3_W, y3)
        deadly = market.get("deadly") or []
        if deadly:
            for it in deadly[:_MAX_ROWS_DEAD]:
                mkt_row(COL3_X, COL3_W, y3, "+" + it.get("zh", ""), price_of(it),
                        C_RED, C_RED)
                y3 += LH_ROW
        else:
            # 空 ≠ 没算：说明「本次候选确实都有溢价」，否则会被误读成数据缺失
            n_pos = len([x for x in (market.get("pos") or []) if x.get("tier") != "致命"])
            T(COL3_X, y3, fit("无（%d 条均有溢价）" % n_pos, FS_NAME, "regular", COL3_W),
              FS_NAME, C_GRAY, "regular")
            y3 += LH_ROW
        mark(max(y1, y2, y3) + 8)

    # ---------- [脚注] ----------
    ny2 = bot[0] + 6
    for note in data.get("notes") or []:
        for ln in wrap("* " + note, 10.0, "regular", BASE_W - 2 * PAD)[:2]:
            T(PAD, ny2, ln, 10.0, C_GRAY, "regular")
            ny2 += 15
        ny2 += 2
    mark(ny2 + 2)

    # ---------- [页脚] ----------
    fy = bot[0] + 8
    T(PAD, fy, "PC Warframe Rabbit · 需用右键/长按提取文字", 10.2, C_GRAY, "regular")
    _q_version(c, P, BASE_W - PAD - 46, fy - 24, 46)
    mark(fy + 34)

    _finish(c, P, bot[0], width)
    return c


# ---------------------------------------------------------------------------
# 局部绘制
# ---------------------------------------------------------------------------
def _brand_title(c: Canvas, P, T, d, tw) -> None:
    """居中品牌标题（紫色方块图标 + 文案）。

    正常态与错误态**必须**共用这一套：早期错误态另写了一份左对齐无图标标题，
    同一张卡两种外观（2026-11-16 实拍发现）。
    """
    title = "紫卡洗卡词条推荐"
    icon_s, tsize = 22.0, 23
    total_w = icon_s + 9 + tw(title, tsize, "bold")
    sx = (BASE_W - total_w) / 2
    c.rounded_rect((P(sx), P(13), P(sx + icon_s), P(13 + icon_s)), radius=P(4), fill=C_BRAND)
    d.rectangle((P(sx + icon_s * 0.30), P(13 + icon_s * 0.42),
                 P(sx + icon_s * 0.70), P(13 + icon_s)), fill=C_BG)
    T(sx + icon_s + 9, 13 + icon_s * 0.5 + 8, title, tsize, C_INK, "bold")


def _finish(c: Canvas, P, bottom: float, width: int) -> None:
    c.img = c.img.crop((0, 0, width, max(20, P(bottom) + 12)))


def _block_title(T, P, y: float, text: str) -> None:
    """区块标题：金色魔法棒图标（✦）+ 金色加粗标题。"""
    T(PAD, y + 11, "✦", 15, C_GOLD, "bold")
    T(PAD + 19, y + 11, text, 15, C_GOLD, "bold")


def _q_version(c: Canvas, P, x: float, y: float, sz: float) -> None:
    """右下角 Q 版插画占位（棕发粉裙、坐在粉色坐垫上，几何近似）。"""
    s = sz
    c.rounded_rect((P(x), P(y + s * 0.74), P(x + s), P(y + s)), radius=P(7), fill="#E9BFD2")
    c.ellipse((P(x + s * 0.16), P(y + s * 0.16), P(x + s * 0.38), P(y + s * 0.62)), fill="#8A5A3B")
    c.ellipse((P(x + s * 0.62), P(y + s * 0.16), P(x + s * 0.84), P(y + s * 0.62)), fill="#8A5A3B")
    c.ellipse((P(x + s * 0.26), P(y + s * 0.44), P(x + s * 0.74), P(y + s * 0.90)), fill="#E6A6C2")
    c.ellipse((P(x + s * 0.32), P(y + s * 0.10), P(x + s * 0.68), P(y + s * 0.48)), fill="#F6DFCB")
    c.ellipse((P(x + s * 0.30), P(y + s * 0.06), P(x + s * 0.70), P(y + s * 0.30)), fill="#8A5A3B")
    c.ellipse((P(x + s * 0.42), P(y + s * 0.28), P(x + s * 0.47), P(y + s * 0.33)), fill="#3A2A33")
    c.ellipse((P(x + s * 0.53), P(y + s * 0.28), P(x + s * 0.58), P(y + s * 0.33)), fill="#3A2A33")
