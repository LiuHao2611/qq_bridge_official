# -*- coding: utf-8 -*-
"""pic/templates/doupo_battle.py —— 斗破 · 完整战斗回看卡。

2026-11-17 用户硬要求：普通战斗回包继续只给高光（不膨胀），但要能主动查看
最后一次战斗的**完整逐招过程**：普攻/技能名、伤害、气血、斗气消耗、触发词条。

版式沿用 doupo_help 的单列折行骨架（度量优先 / 显式 gap / 测画共用 / boxes 自检），
图标换成 sword，标题换「战斗回看」。数据源是 doupo.battle_review.build_data，
与纯文本兜底同源，绝不在这里重算战斗数值。

card-design 合规：
  * 度量优先：_wrap 用 Canvas.text_width 逐字折行；
  * 列网格：分组名 x0+14，正文 x0+BULLET_INDENT；
  * 显式 gap：GROUP_GAP / ITEM_GAP 同层统一；
  * 高度内容驱动：_layout 一次算好，测高与绘制共用；
  * 字形安全：无 emoji，「→ × ｜」均为既有模板已验证符号；
  * boxes 自检：build(..., boxes=[...]) 收集包围盒供 check_boxes。
"""
from __future__ import annotations

from .. import icons
from ..canvas import Canvas
from ..theme import Theme

from .doupo import _card_alpha, _fit, _pal, _w
from . import doupo_style as S

PAD = 28
FS_TITLE = 30
FS_GROUP = S.FS_BODY
FS_BODY = S.FS_SUB
LINE_H = FS_BODY + 9
ITEM_GAP = 6
GROUP_GAP = 16
BULLET_INDENT = 6
ICON_STYLE = "line"

_DEMO = {
    "title": "战斗回看",
    "groups": [
        {"name": "战况", "rows": ["遭遇｜胜｜3回合｜对手 摩云藤", "气血 820/1000｜斗气 40/600"]},
        {"name": "逐招明细", "rows": [
            "01｜我方·摩云藤·回合1｜狂狮怒罡 328（力量·斗气·暴击敏捷）",
            "　气血 900→900　斗气 600→560（-40）　敌血 1200→872　词条 破甲、连击",
        ]},
    ],
    "tip": "发「战斗评价」听一句毒舌 · 只保留最后一场战斗",
}


def _rows_of(g: dict) -> list:
    return [r for r in (g.get("rows") or []) if r]


def _groups_of(data: dict) -> list:
    return [g for g in (data.get("groups") or []) if isinstance(g, dict)]


def _wrap(s: str, max_w: int) -> list[str]:
    return S.wrap_cjk(str(s), FS_BODY, "regular", max_w)


def _layout(data: dict, content_w: int) -> tuple[list[tuple], int]:
    text_w = content_w - BULLET_INDENT
    blocks: list[tuple] = []
    for g in _groups_of(data):
        blocks.append(("group", str(g.get("name") or ""), FS_GROUP + 12))
        for row in _rows_of(g):
            lines = _wrap(row, text_w)
            blocks.append(("item", lines, LINE_H * len(lines) + ITEM_GAP))
        blocks.append(("gap", "", max(0, GROUP_GAP - ITEM_GAP)))
    if blocks:
        blocks.pop()
    return blocks, sum(bh for _, _, bh in blocks)


def _sword_glyph(c: Canvas, cx: int, cy: int, color: str) -> None:
    icons.draw(c, "sword", cx, cy, 16, color, style=ICON_STYLE)


def build(t: Theme, data: dict | None = None, width: int | None = None,
          source: str | None = None, **kw) -> Canvas:
    data = data or {}
    if not _groups_of(data):
        data = _DEMO
    width = int(width or getattr(t, "width", 720) or 720)
    width = max(560, min(width, 900))
    p = _pal(t)
    content_w = width - PAD * 2
    blocks, blocks_h = _layout(data, content_w)
    boxes = kw.pop("boxes", None)

    # 副标题是可选增量（doupo_ruin 复用本骨架时显示「今日进度」）；
    # 不传 subtitle 时 sub_h=0，战斗回看卡的尺寸与像素位置逐字节不变。
    sub_h = FS_BODY + 8 if str(data.get("subtitle") or "") else 0
    h = PAD + FS_TITLE + 8 + 1 + 18 + sub_h + blocks_h + 12 + FS_BODY + PAD
    h = max(360, h)

    c = Canvas(width, h, p["page_color"], theme=t)
    c.rounded_rect((12, 12, width - 12, h - 12), radius=24,
                   fill=p["card_color"], outline=p["border_color"], width=1,
                   fill_alpha=_card_alpha(t) * 255 // 100)

    def rec(label, txt, x, y, size, anchor="lm"):
        if boxes is None:
            return
        w = _w(txt, size, "regular")
        hh = int(size)
        if anchor == "lm":
            bb = (x, y - hh // 2, x + w, y + hh // 2)
        elif anchor == "rm":
            bb = (x - w, y - hh // 2, x, y + hh // 2)
        else:
            bb = (x, y, x + w, y + hh)
        boxes.append((label,) + bb)

    x0 = PAD
    y = PAD

    # ---- 标题 ----
    title = str(data.get("title") or "战斗回看")
    tw = _w(title, FS_TITLE, "light")
    ico_w = 16 + 12
    tx = x0 + (content_w - tw - ico_w) // 2
    _sword_glyph(c, tx + 8, y + FS_TITLE // 2, p["title_color"])
    c.text((tx + ico_w, y + FS_TITLE // 2), title, size=FS_TITLE,
           color=p["title_color"], role="light", anchor="lm")
    rec("title", title, tx + ico_w, y + FS_TITLE // 2, FS_TITLE)
    y += FS_TITLE + 8
    c.line((x0, y, x0 + content_w, y), fill=p["line_color"], width=1)
    y += 18

    # ---- 可选副标题（不改既有战斗卡：无 subtitle 时整段跳过）----
    if sub_h:
        s = _fit(str(data.get("subtitle") or ""), content_w, FS_BODY, "regular")
        c.text((x0, y + FS_BODY // 2), s, size=FS_BODY, color=p["dim_color"],
               role="regular", anchor="lm")
        rec("subtitle", s, x0, y + FS_BODY // 2, FS_BODY)
        y += sub_h

    # ---- 分组 + 逐行 ----
    for bi, (kind, payload, bh) in enumerate(blocks):
        if kind == "group":
            c.rounded_rect((x0 + 1, y + 3, x0 + 5, y + FS_GROUP - 3), radius=2,
                           fill=p["accent_color"])
            c.text((x0 + 14, y + FS_GROUP // 2), payload, size=FS_GROUP,
                   color=p["accent_color"], role="regular", anchor="lm")
            rec("g%d" % bi, payload, x0 + 14, y + FS_GROUP // 2, FS_GROUP)
        elif kind == "item":
            for li, line in enumerate(payload):
                cy = y + li * LINE_H + LINE_H // 2
                color = p["text_color"] if li == 0 else p["dim_color"]
                c.text((x0 + BULLET_INDENT, cy), line, size=FS_BODY,
                       color=color, role="regular", anchor="lm")
                rec("i%d_%d" % (bi, li), line, x0 + BULLET_INDENT, cy, FS_BODY)
        y += bh

    # ---- 底部提示 ----
    c.line((x0, y + 4, x0 + content_w, y + 4), fill=p["line_color"], width=1)
    y += 12
    tip = str(data.get("tip") or "发「战斗评价」听一句毒舌 · 只保留最后一场战斗")
    s = _fit(tip, content_w, FS_BODY, "regular")
    icons.draw(c, "bolt", x0 + 8, y + FS_BODY // 2, 14, p["accent_color"], style=ICON_STYLE)
    c.text((x0 + 26, y + FS_BODY // 2), s, size=FS_BODY, color=p["dim_color"],
           role="regular", anchor="lm")
    rec("tip", s, x0 + 26, y + FS_BODY // 2, FS_BODY)

    if source:
        c.text((width - PAD, h - PAD + 6), source, size=14,
               color=p["dim_color"], anchor="rb")
    return c
