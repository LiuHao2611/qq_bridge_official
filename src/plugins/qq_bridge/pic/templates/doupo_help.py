# -*- coding: utf-8 -*-
"""pic/templates/doupo_help.py —— 斗破 · 玩法细则卡。

把原本 44 行全静态的纯文本「帮助/玩法/攻略/规则」做成卡片。2026-11-22 气泡版：
上一版只有「4px 竖条 + 逐行文字」，2074px 的纯文本墙视觉很累；改为**每个【】
分组一个圆角气泡包裹**，组标题靛紫粗体 + 分隔线，组内要点逐条折行（首行圆点、
折行悬挂缩进）。与 doupo_ruin 的分段气泡同一套思路与配色，视觉统一。

为什么单列：细则动辄 40+ 字（斗技组 11 行、宗门组 6 行），两列会挤成 12px
且频繁截断；帮助卡的价值就在「说全」，单列折行才能把数值完整说完。

card-design 合规：
  * 度量优先：_wrap 用 Canvas.text_width 逐字折行，绝不按字数估算；
  * 列网格：组标题起点固定 x0+GROUP_PAD+14，要点悬挂缩进固定 BULLET_INDENT；
  * 显式 gap：GROUP_GAP/ITEM_GAP 同层统一 token；
  * 高度内容驱动：_layout 一次算好每组气泡高度，测高与绘制共用；
  * 字形安全：无 emoji；「→ × % ｜」均为字体已验证符号；
  * boxes 自检：build(..., boxes=[...]) 收集包围盒供 check_boxes。

数据源：card.build_help_data 读 commands.HELP_GROUPS，与纯文本兜底同源。
"""
from __future__ import annotations

from .. import icons
from ..canvas import Canvas
from ..theme import Theme

from .doupo import _fit, _pal, _w
from . import doupo_ui as UI
from . import doupo_style as S

PAD = 36              # 内容左右内边距（2026-11-22 由 28 加到 36：游戏风回纹外框
                      # 画在 10、内线 17，PAD=28 时内容距框线只剩 11px，
                      # 用户明确反馈「字和边框黏在一起」，故统一到 36 → 距框线 26px）
FS_TITLE = 30
FS_GROUP = S.FS_BODY          # 20 分组标题
FS_BODY = S.FS_SUB            # 17 要点正文（清晰下限）
LINE_H = FS_BODY + 9          # 26 折行行高
ITEM_GAP = 6
GROUP_GAP = 14                # 组气泡之间的间距
GROUP_PAD = 14                # 组气泡内边距（左右/上下）
GROUP_HEAD_H = FS_GROUP + 10  # 组标题行高
GROUP_DIV_GAP = 8             # 组标题与首条要点的分隔留白
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


def _layout(data: dict, content_w: int) -> tuple[list[dict], int]:
    """每组气泡的排版计划 + 总块高；测高与绘制共用同一份（禁止绘制阶段再猜）。

    每个 group plan：{name, rows:[lines], h}。气泡内宽 = content_w - 2*GROUP_PAD，
    要点悬挂缩进 BULLET_INDENT，故折行可用宽再减 BULLET_INDENT。
    """
    inner_w = content_w - 2 * GROUP_PAD
    text_w = inner_w - BULLET_INDENT
    plans: list[dict] = []
    for g in _groups_of(data):
        rows = [_wrap(row, text_w) for row in _rows_of(g)]
        rows = [r for r in rows if r]
        row_h = sum(LINE_H * len(lines) + ITEM_GAP for lines in rows)
        h = (GROUP_PAD + GROUP_HEAD_H + GROUP_DIV_GAP + row_h
             - (ITEM_GAP if rows else 0) + GROUP_PAD)
        plans.append({"name": str(g.get("name") or ""), "rows": rows, "h": h})
    total = sum(pl["h"] for pl in plans) + GROUP_GAP * max(0, len(plans) - 1)
    return plans, total


def build(t: Theme, data: dict | None = None, width: int | None = None,
          source: str | None = None, **kw) -> Canvas:
    data = data or {}
    if not _groups_of(data):
        data = _DEMO
    width = int(width or getattr(t, "width", 720) or 720)
    width = max(560, min(width, 900))
    p = _pal(t)
    content_w = width - PAD * 2
    inner_w = content_w - 2 * GROUP_PAD
    plans, blocks_h = _layout(data, content_w)
    boxes = kw.pop("boxes", None)

    h = UI.TITLE_H + blocks_h + 14 + FS_BODY + 28
    h = max(400, h)

    c = Canvas(width, h, p["page_color"], theme=t)

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

    # ---- 标题（青蓝云纹牌匾，内核统一）----
    title = str(data.get("title") or "斗破玩法")
    y = UI.card_shell(c, p, title, width=width)
    rec("title", title, width // 2, y - 40, FS_TITLE)

    # ---- 分组气泡 + 组内要点 ----
    for gi, gp in enumerate(plans):
        gh = gp["h"]
        # 游戏面板：深底 + 暗金边 + 面板内祥云水印（与 doupo_ruin 同一套语义）
        UI.panel(c, p, (x0, y, x0 + content_w, y + gh), state="done", radius=12)
        # 组标题：左侧靛紫竖条 + 靛紫粗体名。
        gy = y + GROUP_PAD
        c.rounded_rect((x0 + GROUP_PAD, gy + 3, x0 + GROUP_PAD + 4,
                        gy + FS_GROUP - 3), radius=2, fill=p["title_color"])
        gname = _fit(gp["name"], inner_w - 14, FS_GROUP, "regular")
        c.text((x0 + GROUP_PAD + 14, gy + GROUP_HEAD_H // 2), gname, size=FS_GROUP,
               color=p["title_color"], role="regular", anchor="lm")
        rec("g%d" % gi, gname, x0 + GROUP_PAD + 14, gy + GROUP_HEAD_H // 2, FS_GROUP)
        # 组标题与要点之间的分割线。
        sep_y = gy + GROUP_HEAD_H + GROUP_DIV_GAP // 2
        c.line((x0 + GROUP_PAD, sep_y, x0 + content_w - GROUP_PAD, sep_y),
               fill=p["line_color"], width=1)
        # 组内要点：逐条折行，首行圆点，折行悬挂缩进。
        iy = gy + GROUP_HEAD_H + GROUP_DIV_GAP
        for ri, lines in enumerate(gp["rows"]):
            for li, line in enumerate(lines):
                cy = iy + li * LINE_H + LINE_H // 2
                if li == 0:
                    c.rounded_rect((x0 + GROUP_PAD + 3, cy - 3,
                                    x0 + GROUP_PAD + 9, cy + 3), radius=3,
                                   fill=p["pill_text"])
                color = p["text_color"] if li == 0 else p["dim_color"]
                tx = x0 + GROUP_PAD + BULLET_INDENT
                c.text((tx, cy), line, size=FS_BODY, color=color,
                       role="regular", anchor="lm")
                rec("i%d_%d_%d" % (gi, ri, li), line, tx, cy, FS_BODY)
            iy += LINE_H * len(lines) + ITEM_GAP
        y += gh + GROUP_GAP
    if plans:
        y -= GROUP_GAP

    # ---- 底部提示 ----
    UI.divider(c, p, y + 6, x0 + 6, x0 + content_w - 6, style="band")
    y += 16
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
