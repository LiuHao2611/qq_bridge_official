# -*- coding: utf-8 -*-
"""pic/templates/doupo_help.py —— 斗破 · 玩法细则卡。

把原本 44 行全静态的纯文本「帮助/玩法/攻略/规则」做成卡片：10 个【】分组，
每组一条 4px 竖条 + 分组名，组内要点逐条折行显示（首行圆点、折行悬挂缩进）。
与 xiuxian_help 同构单列版式，但用 doupo 主题配色，与人物/菜单/名录卡一致。

为什么单列：细则动辄 40+ 字（斗技组 11 行、宗门组 6 行），两列会挤成 12px
且频繁截断；帮助卡的价值就在「说全」，单列折行才能把数值完整说完。

card-design 合规：
  * 度量优先：_wrap 用 Canvas.text_width 逐字折行，绝不按字数估算；
  * 列网格：分组名起点固定 x0+14，要点悬挂缩进固定 BULLET_INDENT；
  * 显式 gap：GROUP_GAP/ITEM_GAP 同层统一 token；
  * 高度内容驱动：_layout 一次算好每块高度，测高与绘制共用；
  * 字形安全：无 emoji；「→ × % ｜」均为字体已验证符号；
  * boxes 自检：build(..., boxes=[...]) 收集包围盒供 check_boxes。

数据源：card.build_help_data 读 commands.HELP_GROUPS，与纯文本兜底同源。
"""
from __future__ import annotations

from .. import icons
from ..canvas import Canvas
from ..theme import Theme

from .doupo import _card_alpha, _fit, _pal, _w
from . import doupo_style as S

PAD = 28
FS_TITLE = 30
FS_GROUP = S.FS_BODY          # 20 分组标题
FS_BODY = S.FS_SUB            # 17 要点正文（清晰下限）
LINE_H = FS_BODY + 9          # 26 折行行高
ITEM_GAP = 6
GROUP_GAP = 16
BULLET_INDENT = 18            # 圆点后文字起点 / 折行悬挂缩进
ICON_STYLE = "line"

_DEMO = {
    "title": "斗破玩法",
    "groups": [
        {"name": "属性", "rows": ["力量=普攻，斗气=技能，敏捷→暴击+闪避"]},
        {"name": "炼药", "rows": ["成丹把握看炼药师品阶与丹药品阶：四品炼一品九成、炼四品七成"]},
    ],
    "tip": "发「斗破菜单」看全部口令 · 数值改动以本卡为准",
}


def _list_glyph(c: Canvas, cx: int, cy: int, color: str) -> None:
    """手绘「清单」字形（三条不等长圆头横线）。

    icons 的 book 在 line 风格下是空心方框，看着像字体豆腐框（card-design 视觉瑕疵），
    故与 xiuxian_help 同款自绘字形，标题一眼可辨「这是清单/细则」。
    """
    for i, w in enumerate((16, 16, 10)):
        yy = cy - 6 + i * 6
        c.rounded_rect((cx - 8, yy - 1, cx - 8 + w, yy + 1), radius=1, fill=color)


def _rows_of(g: dict) -> list:
    return [r for r in (g.get("rows") or []) if r]


def _groups_of(data: dict) -> list:
    return [g for g in (data.get("groups") or []) if isinstance(g, dict)]


def _wrap(s: str, max_w: int) -> list[str]:
    """逐字折行（宽度实测），中文避头标点复用 doupo_style.wrap_cjk。"""
    return S.wrap_cjk(str(s), FS_BODY, "regular", max_w)


def _layout(data: dict, content_w: int) -> tuple[list[tuple], int]:
    """排版块 [(kind, payload, h)] + 总块高；测高与绘制共用同一份。"""
    text_w = content_w - BULLET_INDENT
    blocks: list[tuple] = []
    for g in _groups_of(data):
        blocks.append(("group", str(g.get("name") or ""), FS_GROUP + 12))
        for row in _rows_of(g):
            lines = _wrap(row, text_w)
            blocks.append(("item", lines, LINE_H * len(lines) + ITEM_GAP))
        blocks.append(("gap", "", max(0, GROUP_GAP - ITEM_GAP)))
    if blocks:
        blocks.pop()  # 末尾不留 group gap
    total = sum(bh for _, _, bh in blocks)
    return blocks, total


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

    h = PAD + FS_TITLE + 8 + 1 + 18 + blocks_h + 12 + FS_BODY + PAD
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
    title = str(data.get("title") or "斗破玩法")
    tw = _w(title, FS_TITLE, "light")
    ico_w = 16 + 12
    tx = x0 + (content_w - tw - ico_w) // 2
    _list_glyph(c, tx + 8, y + FS_TITLE // 2, p["title_color"])
    c.text((tx + ico_w, y + FS_TITLE // 2), title, size=FS_TITLE,
           color=p["title_color"], role="light", anchor="lm")
    rec("title", title, tx + ico_w, y + FS_TITLE // 2, FS_TITLE)
    y += FS_TITLE + 8
    c.line((x0, y, x0 + content_w, y), fill=p["line_color"], width=1)
    y += 18

    # ---- 分组 + 要点 ----
    for bi, (kind, payload, bh) in enumerate(blocks):
        if kind == "group":
            c.rounded_rect((x0 + 1, y + 3, x0 + 5, y + FS_GROUP - 3), radius=2,
                           fill=p["accent_color"])
            c.text((x0 + 14, y + FS_GROUP // 2), payload, size=FS_GROUP,
                   color=p["accent_color"], role="regular", anchor="lm")
            rec("g%d" % bi, payload, x0 + 14, y + FS_GROUP // 2, FS_GROUP)
        elif kind == "item":
            lines = payload
            for li, line in enumerate(lines):
                cy = y + li * LINE_H + LINE_H // 2
                if li == 0:
                    c.rounded_rect((x0 + 3, cy - 3, x0 + 9, cy + 3), radius=3,
                                   fill=p["dim_color"])
                color = p["text_color"] if li == 0 else p["dim_color"]
                c.text((x0 + BULLET_INDENT, cy), line, size=FS_BODY,
                       color=color, role="regular", anchor="lm")
                rec("i%d_%d" % (bi, li), line, x0 + BULLET_INDENT, cy, FS_BODY)
        y += bh

    # ---- 底部提示 ----
    c.line((x0, y + 4, x0 + content_w, y + 4), fill=p["line_color"], width=1)
    y += 12
    tip = str(data.get("tip") or "发「斗破菜单」看全部口令 · 数值改动以本卡为准")
    s = _fit(tip, content_w, FS_BODY, "regular")
    icons.draw(c, "bolt", x0 + 8, y + FS_BODY // 2, 14, p["accent_color"], style=ICON_STYLE)
    c.text((x0 + 26, y + FS_BODY // 2), s, size=FS_BODY, color=p["dim_color"],
           role="regular", anchor="lm")
    rec("tip", s, x0 + 26, y + FS_BODY // 2, FS_BODY)

    if source:
        c.text((width - PAD, h - PAD + 6), source, size=14,
               color=p["dim_color"], anchor="rb")
    return c
