# -*- coding: utf-8 -*-
"""pic/templates/doupo_craft.py —— 斗破 · 傀儡炼制成傀卡（2026-11-20 建 / 11-22 气泡版重做）。

用户 2026-11-20：「炼制傀儡 自定 的回复不要纯文字，改成卡片」。
成傀是一条**不可逆的消耗**（材料＋斗气当场扣掉），纯文字回执里「成了什么档、
肉身攻击多少、料扣了哪些」全埋在句子里；卡片把「成傀 / 用料 / 降档提醒 / 下一步」
分开摆，玩家一眼看出这一炉值不值。

2026-11-22 气泡版：旧版直接 ``return _battle.build(...)``，但传入 data 只有
``title/subtitle/groups/tip``，缺 ``_battle._rich()`` 要的 verdict/kpis/rounds，
实际落到 battle 的 **legacy 纯文本分组骨架**（只有竖条 + 逐行文字）。改为遗迹卡
同款**分组气泡**：每组一个圆角气泡 + 序号圆标 + 组名，subtitle 单独成强调条。

card-design 合规：度量优先（S.wrap_cjk / _w / _fit）、显式 gap、高度内容驱动、
boxes 自检；只用 _pal() 配色，与人物/遗迹/帮助卡视觉统一。

输入由 ``doupo.card.build_craft_data`` 生成：
  title / subtitle / groups:[{name, rows:[...]}] / tip
"""
from __future__ import annotations

from .. import icons
from ..canvas import Canvas
from ..theme import Theme

from .doupo import _card_alpha, _fit, _pal, _w
from . import doupo_style as S

PAD = 28
FS_TITLE = 30
FS_SUB = S.FS_BODY            # 20 副标题（成傀摘要）
FS_GROUP = S.FS_BODY          # 20 组名
FS_BODY = S.FS_SUB            # 17 要点正文
LINE_H = FS_BODY + 9          # 26
GROUP_GAP = 12
GROUP_PAD = 14
GROUP_HEAD_H = 30            # 序号圆标 + 组名
ITEM_GAP = 6
ORD_D = 26                   # 序号圆标直径
ORD_GAP = 12                 # 圆标到组名间距
SUB_BAR_H = 42               # subtitle 强调条高度
_WHITE = "#FFFFFF"

_DEMO = {
    "title": "傀儡炼制",
    "subtitle": "铜尸傀 · 人级傀儡·铜皮 · id=1",
    "groups": [
        {"name": "成傀", "rows": [
            "【人级·强】人级傀儡档 · 铜皮",
            "肉身攻击 1830　斗师级",
            "雷淬纯度 0%　未认主",
        ]},
        {"name": "用料", "rows": [
            "强者躯体×1、火属性魔核×1、无主灵魂×1、精铁×1",
            "斗气 -620（余 3480）",
        ]},
    ],
    "tip": "发「傀儡」看名录 · 发「傀儡 淬炼」雷淬升档 · 发「傀儡 配方」看全部配方",
}


def _rows_of(g: dict) -> list:
    return [r for r in (g.get("rows") or []) if r]


def _groups_of(data: dict) -> list:
    return [g for g in (data.get("groups") or []) if isinstance(g, dict)]


def _wrap(s: str, max_w: int) -> list[str]:
    return S.wrap_cjk(str(s), FS_BODY, "regular", max_w)


def _plan(data: dict, content_w: int) -> tuple[list[dict], int]:
    """每组气泡计划 + 总块高，测画共用。"""
    inner_w = content_w - 2 * GROUP_PAD
    text_w = inner_w - ORD_D - ORD_GAP
    plans: list[dict] = []
    for gi, g in enumerate(_groups_of(data)):
        rows = [_wrap(row, text_w) for row in _rows_of(g)]
        rows = [r for r in rows if r]
        row_h = sum(LINE_H * len(lines) + ITEM_GAP for lines in rows)
        h = GROUP_PAD + GROUP_HEAD_H + row_h - (ITEM_GAP if rows else 0) + GROUP_PAD
        plans.append({"no": gi + 1, "name": str(g.get("name") or ""),
                      "rows": rows, "h": h})
    total = sum(pl["h"] for pl in plans) + GROUP_GAP * max(0, len(plans) - 1)
    return plans, total


def build(t: Theme, data: dict | None = None, width: int | None = None,
          source: str | None = None, **kw):
    data = dict(data or _DEMO)
    if not _groups_of(data):
        data = dict(_DEMO)
    data.setdefault("title", "傀儡炼制")
    data.setdefault("tip", _DEMO["tip"])

    width = int(width or getattr(t, "width", 720) or 720)
    width = max(560, min(width, 900))
    p = _pal(t)
    content_w = width - PAD * 2
    inner_w = content_w - 2 * GROUP_PAD
    plans, blocks_h = _plan(data, content_w)
    boxes = kw.pop("boxes", None)

    sub = str(data.get("subtitle") or "")
    sub_h = SUB_BAR_H if sub else 0
    h = (PAD + FS_TITLE + 8 + 1 + 18 + sub_h + (12 if sub_h else 0)
         + blocks_h + 12 + FS_BODY + PAD)
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
            bb = (x, y, x + max(1, w), y + hh)
        boxes.append((label,) + bb)

    x0 = PAD
    y = PAD
    # 标题
    title = str(data.get("title") or "傀儡炼制")
    tw = _w(title, FS_TITLE, "light")
    ico_w = 16 + 12
    tx = x0 + (content_w - tw - ico_w) // 2
    icons.draw(c, "sword", tx + 8, y + FS_TITLE // 2, 16, p["title_color"], style="line")
    c.text((tx + ico_w, y + FS_TITLE // 2), title, size=FS_TITLE,
           color=p["title_color"], role="light", anchor="lm")
    rec("title", title, tx + ico_w, y + FS_TITLE // 2, FS_TITLE)
    y += FS_TITLE + 8
    c.line((x0, y, x0 + content_w, y), fill=p["line_color"], width=1)
    y += 18

    # subtitle 强调条（淡紫底 + 靛紫字）
    if sub_h:
        c.rounded_rect((x0, y, x0 + content_w, y + SUB_BAR_H - 6), radius=10,
                       fill=p["chip_bg"], outline=p["chip_line"], width=1)
        s = _fit(sub, content_w - 2 * GROUP_PAD, FS_SUB, "regular")
        c.text((x0 + GROUP_PAD, y + (SUB_BAR_H - 6) // 2), s, size=FS_SUB,
               color=p["title_color"], role="regular", anchor="lm")
        rec("subtitle", s, x0 + GROUP_PAD, y + (SUB_BAR_H - 6) // 2, FS_SUB)
        y += sub_h + 12

    # 分组气泡
    for gi, gp in enumerate(plans):
        gh = gp["h"]
        c.rounded_rect((x0, y, x0 + content_w, y + gh), radius=12,
                       fill=p["cell_bg"], outline=p["border_color"], width=1)
        # 序号圆标（靛紫实心 + 白字）
        ocx = x0 + GROUP_PAD + ORD_D // 2
        ocy = y + GROUP_PAD + GROUP_HEAD_H // 2
        c.ellipse((ocx - ORD_D // 2, ocy - ORD_D // 2,
                   ocx + ORD_D // 2, ocy + ORD_D // 2), fill=p["title_color"])
        c.text((ocx, ocy), str(gp["no"]), size=S.FS_KEY, color=_WHITE,
               role="bold", bold=True, anchor="mm")
        rec("g%d_ord" % gi, str(gp["no"]), ocx - ORD_D // 2, ocy - ORD_D // 2, ORD_D)
        # 组名
        gx = x0 + GROUP_PAD + ORD_D + ORD_GAP
        gname = _fit(gp["name"], inner_w - ORD_D - ORD_GAP, FS_GROUP, "bold")
        c.text((gx, ocy), gname, size=FS_GROUP, color=p["title_color"],
               role="bold", bold=True, anchor="lm")
        rec("g%d_name" % gi, gname, gx, ocy - FS_GROUP // 2, FS_GROUP, anchor="lm")
        # 组内要点
        iy = y + GROUP_PAD + GROUP_HEAD_H
        for ri, lines in enumerate(gp["rows"]):
            for li, line in enumerate(lines):
                cy = iy + li * LINE_H + LINE_H // 2
                color = p["text_color"] if li == 0 else p["dim_color"]
                c.text((gx, cy), line, size=FS_BODY, color=color,
                       role="regular", anchor="lm")
                rec("i%d_%d_%d" % (gi, ri, li), line, gx, cy, FS_BODY)
            iy += LINE_H * len(lines) + ITEM_GAP
        y += gh + GROUP_GAP
    if plans:
        y -= GROUP_GAP

    # 底部提示
    c.line((x0, y + 4, x0 + content_w, y + 4), fill=p["line_color"], width=1)
    y += 12
    tip = str(data.get("tip") or "")
    s = _fit(tip, content_w - 26, FS_BODY, "regular")
    icons.draw(c, "bolt", x0 + 8, y + FS_BODY // 2, 14, p["accent_color"], style="line")
    c.text((x0 + 26, y + FS_BODY // 2), s, size=FS_BODY,
           color=p["dim_color"], role="regular", anchor="lm")
    rec("tip", s, x0 + 26, y + FS_BODY // 2, FS_BODY)

    if source:
        c.text((width - PAD, h - PAD + 6), source, size=14, color=p["dim_color"], anchor="rb")
    return c


def card_height(data: dict, content_w: int) -> float:
    data = dict(data or _DEMO)
    if not _groups_of(data):
        data = dict(_DEMO)
    plans, blocks_h = _plan(data, content_w)
    sub_h = SUB_BAR_H if str(data.get("subtitle") or "") else 0
    return (PAD + FS_TITLE + 8 + 1 + 18 + sub_h + (12 if sub_h else 0)
            + blocks_h + 12 + FS_BODY + PAD)
