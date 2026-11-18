# -*- coding: utf-8 -*-
"""pic/templates/doupo_puppet.py —— 斗破 · 傀儡卡（2026-11-18）。

为什么单开模板
--------------
傀儡不是「背包里的一件东西」：每具都有档次/皮肤/躯体/魔核/灵魂/雷淬纯度/认主状态，
还牵着五道资格（境界·灵魂·控火·异火·斗气）。塞进通用列表卡 ``doupo_list``
会被压成几行纯文字，主次全丢。所以另开专用卡：

  * 一具一个圆角块，名字 24px 粗、攻击 30px 大字，余项按层级降到 17px；
  * 档次·皮肤做右侧徽章，不抢名字的阅读起点；
  * 未拥有时改渲染「五道资格」区，把差哪道说清楚，而不是给一句空提示。

card-design 合规（与 doupo_battle 同骨架）
------------------------------------------
  * 度量优先：``_wrap`` 用 ``S.wrap_cjk`` 实测折行，不按字数估；
  * 列网格：卡内文字左缘固定 ``x0+BLOCK_PAD_X``，徽章右锚定；
  * 显式 gap：``BLOCK_GAP`` / ``LINE_H`` / ``GATE_GAP`` 同层统一，不用空格凑；
  * 高度内容驱动：``_layout`` 一次算好，``_measure`` 与 ``build`` 共用；
  * 字形安全：无 emoji，「· ％ →」均为既有斗破卡已验证符号；
  * boxes 自检：``build(..., boxes=[...])`` 收集全部文本包围盒。

输入（build 的 data，JSON 友好）
--------------------------------
  title / subtitle / tip
  puppets: [{"name","badge","atk","lines":[...],"active":bool}]
  gates:   [{"name","ok","why"}]      # 可空；非空时渲染资格区

数据由 ``doupo.card.build_puppet_data`` 生成，与纯文本兜底同源，不在模板里重算数值。
"""
from __future__ import annotations

from .. import icons
from ..canvas import Canvas
from ..theme import Theme

from .doupo import _card_alpha, _fit, _pal, _w
from . import doupo_style as S

PAD = 28
FS_TITLE = 30
FS_NAME = S.FS_KEY        # 24
FS_ATK = S.FS_HERO        # 30
FS_BODY = S.FS_BODY       # 20
FS_SUB = S.FS_SUB         # 17
LINE_H = FS_SUB + 8
NAME_H = FS_NAME + 10
ATK_H = FS_ATK + 8
BLOCK_PAD_X = 16
BLOCK_PAD_Y = 14
BLOCK_GAP = 16
GATE_GAP = 5
ICON_STYLE = "line"

_DEMO = {
    "title": "傀儡",
    "subtitle": "持有 1 具 · 出战 1 具",
    "puppets": [{
        "name": "人级·玄", "badge": "人级傀儡 · 铜皮", "atk": "246",
        "lines": ["躯体 强者之躯（斗师）", "魔核 7阶火系 · 灵魂 无主灵魂",
                  "雷淬纯度 24% · 已认主·永不背叛"],
        "active": True,
    }],
    "gates": [],
    "tip": "发「傀儡 淬炼」用雷池提纯 · 发「炼制傀儡」再炼一具",
}


def _puppets(data: dict) -> list:
    return [x for x in (data.get("puppets") or []) if isinstance(x, dict)]


def _gates(data: dict) -> list:
    return [x for x in (data.get("gates") or []) if isinstance(x, dict)]


def _wrap(s: str, max_w: int) -> list[str]:
    return S.wrap_cjk(str(s), FS_SUB, "regular", max_w)


def _plan(data: dict, content_w: int) -> tuple[list[tuple], int]:
    """一次算出所有块与总高；build 只负责按同一份计划绘制。"""
    blocks: list[tuple] = []
    inner_w = content_w - BLOCK_PAD_X * 2
    for i, pu in enumerate(_puppets(data)):
        # 名字与徽章实测宽度；inner_w 在循环外定义，纯 gates 卡也不会 NameError

        name_w = _w(str(pu.get("name") or "傀儡"), FS_NAME, "bold")
        badge_w = _w(str(pu.get("badge") or ""), FS_SUB, "regular")
        # 名字与徽章同一行：两者实测宽度之和不能超过内宽，否则徽章另起一行。
        same_row = name_w + badge_w + 18 <= inner_w
        lines: list[str] = []
        for raw in (pu.get("lines") or []):
            lines.extend(_wrap(raw, inner_w))
        h = BLOCK_PAD_Y * 2 + NAME_H + ATK_H + LINE_H * len(lines)
        if not same_row:
            h += FS_SUB + 6
        blocks.append(("puppet", (pu, same_row, lines), h))
        if i < len(_puppets(data)) - 1:
            blocks.append(("gap", None, BLOCK_GAP))

    gs = _gates(data)
    if gs:
        if blocks:
            blocks.append(("gap", None, BLOCK_GAP + 4))
        blocks.append(("gate_title", None, FS_BODY + 8))
        for gi, g in enumerate(gs):
            if gi:
                blocks.append(("gap", None, GATE_GAP))
            txt = "%s %s%s" % ("达成" if g.get("ok") else "未达",
                               g.get("name") or "",
                               ("（%s）" % g["why"]) if g.get("why") else "")
            ls = _wrap(txt, content_w - 14)
            blocks.append(("gate", ls, LINE_H * len(ls)))
    return blocks, sum(bh for _, _, bh in blocks)


def _measure(data: dict, width: int) -> int:
    content_w = int(width) - PAD * 2
    blocks_h = _plan(data, content_w)[1]
    return max(340, PAD + FS_TITLE + 8 + 1 + 14 + 10
               + blocks_h + 14 + FS_BODY + PAD)


def build(t: Theme, data: dict | None = None, width: int | None = None,
          source: str | None = None, **kw) -> Canvas:
    data = data or {}
    if not _puppets(data) and not _gates(data):
        data = _DEMO
    width = int(width or getattr(t, "width", 720) or 720)
    width = max(560, min(width, 900))
    p = _pal(t)
    content_w = width - PAD * 2
    inner_w = content_w - BLOCK_PAD_X * 2
    blocks, _ = _plan(data, content_w)
    h = _measure(data, width)
    boxes = kw.pop("boxes", None)

    c = Canvas(width, h, p["page_color"], theme=t)
    c.rounded_rect((12, 12, width - 12, h - 12), radius=24,
                   fill=p["card_color"], outline=p["border_color"], width=1,
                   fill_alpha=_card_alpha(t) * 255 // 100)

    def rec(label, txt, x, y, size, anchor="lm"):
        if boxes is None:
            return
        w = _w(str(txt), size, "bold" if size >= S.MIN_BOLD_FS else "regular")
        hh = int(size)
        if anchor == "rm":
            bb = (x - w, y - hh // 2, x, y + hh // 2)
        elif anchor == "lm":
            bb = (x, y - hh // 2, x + w, y + hh // 2)
        else:
            bb = (x, y, x + w, y + hh)
        boxes.append((label,) + bb)

    x0 = PAD
    y = PAD

    # ---- 标题 ----
    title = str(data.get("title") or "傀儡")
    tw = _w(title, FS_TITLE, "bold")
    ico_w = 16 + 12
    tx = x0 + (content_w - tw - ico_w) // 2
    icons.draw(c, "wand", tx + 8, y + FS_TITLE // 2, 16,
               p["title_color"], style=ICON_STYLE)
    S.key(c, (tx + ico_w, y + FS_TITLE // 2), title,
          size=FS_TITLE, color=p["title_color"])
    rec("title", title, tx + ico_w, y + FS_TITLE // 2, FS_TITLE)
    y += FS_TITLE + 8
    c.line((x0, y, x0 + content_w, y), fill=p["line_color"], width=1)
    y += 14

    # ---- 副标题 ----
    sub = _fit(str(data.get("subtitle") or ""), content_w, FS_BODY, "regular")
    if sub:
        S.small(c, (x0, y + FS_BODY // 2), sub, size=FS_BODY,
                color=p["dim_color"])
        rec("subtitle", sub, x0, y + FS_BODY // 2, FS_BODY)
    y += 14 + 10

    # ---- 主体块 ----
    for bi, (kind, payload, bh) in enumerate(blocks):
        if kind == "gap":
            y += bh
            continue
        if kind == "puppet":
            pu, same_row, lines = payload
            bx0, bx1 = x0, x0 + content_w
            c.rounded_rect((bx0, y + 2, bx1, y + bh - 2), radius=14,
                           fill=p["cell_bg"], outline=p["cell_line"], width=1)
            # 出战标记：左侧 4px 实色条，不占文字列宽
            if pu.get("active"):
                c.rounded_rect((bx0 + 1, y + 8, bx0 + 5, y + NAME_H + 4),
                               radius=2, fill=p["accent_color"])
            iy = y + BLOCK_PAD_Y
            itx = bx0 + BLOCK_PAD_X
            name = str(pu.get("name") or "傀儡")
            S.key(c, (itx, iy + NAME_H // 2), name, size=FS_NAME,
                  color=p["title_color"], max_w=inner_w - 20)
            rec("name%d" % bi, name, itx, iy + NAME_H // 2, FS_NAME)
            badge = str(pu.get("badge") or "")
            if badge:
                by = iy + NAME_H // 2 if same_row else iy + NAME_H + FS_SUB // 2 + 2
                S.small(c, (bx1 - BLOCK_PAD_X, by), badge, size=FS_SUB,
                        color=p["accent_color"], anchor="rm", max_w=inner_w - 20)
                rec("badge%d" % bi, badge, bx1 - BLOCK_PAD_X, by, FS_SUB, "rm")
            iy += NAME_H + (0 if same_row else FS_SUB + 6)
            atk = str(pu.get("atk") or "0")
            S.key(c, (itx, iy + ATK_H // 2), "肉身攻击 " + atk,
                  size=FS_ATK, color=p["accent_color"],
                  max_w=inner_w)
            rec("atk%d" % bi, "肉身攻击 " + atk, itx, iy + ATK_H // 2, FS_ATK)
            iy += ATK_H
            for li, ln in enumerate(lines):
                cy = iy + li * LINE_H + LINE_H // 2
                S.small(c, (itx, cy), ln, size=FS_SUB, color=p["text_color"])
                rec("line%d_%d" % (bi, li), ln, itx, cy, FS_SUB)
        elif kind == "gate_title":
            c.rounded_rect((x0 + 1, y + 3, x0 + 5, y + FS_BODY - 3), radius=2,
                           fill=p["accent_color"])
            S.key(c, (x0 + 14, y + FS_BODY // 2), "炼制资格",
                  size=FS_BODY, color=p["accent_color"])
            rec("gate_title", "炼制资格", x0 + 14, y + FS_BODY // 2, FS_BODY)
        elif kind == "gate":
            for li, ln in enumerate(payload):
                cy = y + li * LINE_H + LINE_H // 2
                S.small(c, (x0 + 14, cy), ln, size=FS_SUB, color=p["text_color"])
                rec("gate%d_%d" % (bi, li), ln, x0 + 14, cy, FS_SUB)
        y += bh

    # ---- 底部提示 ----
    c.line((x0, y + 4, x0 + content_w, y + 4), fill=p["line_color"], width=1)
    y += 12
    tip = _fit(str(data.get("tip") or ""), content_w - 26, FS_SUB, "regular")
    if tip:
        icons.draw(c, "bolt", x0 + 8, y + FS_SUB // 2, 14,
                   p["accent_color"], style=ICON_STYLE)
        S.small(c, (x0 + 26, y + FS_SUB // 2), tip, size=FS_SUB,
                color=p["dim_color"])
        rec("tip", tip, x0 + 26, y + FS_SUB // 2, FS_SUB)

    if source:
        c.text((width - PAD, h - PAD + 6), source, size=14,
               color=p["dim_color"], anchor="rb")
    return c
