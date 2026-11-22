# -*- coding: utf-8 -*-
"""pic/templates/doupo_craft.py —— 斗破 · 傀儡炼制成傀卡（2026-11-20 建 / 11-22 游戏风重做）。

用户 2026-11-20：「炼制傀儡 自定 的回复不要纯文字，改成卡片」。
成傀是一条**不可逆的消耗**（材料＋斗气当场扣掉），纯文字回执里「成了什么档、
肉身攻击多少、料扣了哪些」全埋在句子里；卡片把「成傀 / 用料 / 降档提醒 / 下一步」
分开摆，玩家一眼看出这一炉值不值。

2026-11-22 游戏风重做（第三轮）：前两版分别是「battle legacy 纯文本骨架」和
「浅色分组气泡」。用户看了斗破游戏本体截图后要求「没有游戏的效果，认真设计」，
故整卡改走 :mod:`doupo_ui` 内核：牌匾标题 + 回纹外框 + 深蓝紫底 + 云纹，
正文换成游戏面板（:func:`doupo_ui.panel`）+ 金色段序号徽（:func:`doupo_ui.ord_badge`）
+ 楷体段名 + 回纹横带分隔；成傀摘要单独做成金色描边强调条。

card-design 合规：度量优先（S.wrap_cjk / UI.tw / UI.fit）、显式 gap（全部常量集中
在上方）、高度内容驱动（_plan 一次算好，测高与绘制共用）、boxes 自检；配色只走
_pal() → doupo_ui.pal()，与其余 11 张卡同源。

输入由 ``doupo.card.build_craft_data`` 生成：
  title / subtitle / groups:[{name, rows:[...]}] / tip
"""
from __future__ import annotations

from PIL import ImageDraw

from ..canvas import Canvas
from ..theme import Theme

from . import doupo_ui as UI
from .doupo import _pal
from . import doupo_style as S

PAD = UI.PAD_X        # 内容左右内边距 36（外框画在 10、内线 17 → 距框线 19px）
PAD_B = UI.PAD_B      # 底部 54（底部两角只有短 L 线，36 收尾会贴框）
FS_SUB = S.FS_BODY            # 20 成傀摘要
FS_GROUP = S.FS_KEY           # 24 组名（楷体，比正文明显一档）
FS_BODY = S.FS_SUB            # 17 要点正文
LINE_H = FS_BODY + 9          # 26
ITEM_GAP = 6                  # 同一组内两条要点之间
GROUP_GAP = 14                # 组面板之间
GRP_PAD = 16                  # 面板内边距
GRP_HEAD = 34                 # 序号徽 + 组名这一行的高度
ORD_D = 28                    # 段序号徽直径
ORD_GAP = 14                  # 徽到组名间距
SUB_H = 40                    # 成傀摘要条高
SUB_GAP = 16                  # 摘要条到第一组面板
TIP_GAP = 18                  # 最后一组到回纹横带
TIP_LEAD = 14                 # 横带到 tip 文字

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


def _text_x(content_w: int) -> int:
    """组内正文左起点（跳过序号徽列）。"""
    return PAD + GRP_PAD + ORD_D + ORD_GAP


def _text_w(content_w: int) -> int:
    return content_w - GRP_PAD - ORD_D - ORD_GAP - GRP_PAD


def _plan(data: dict, content_w: int) -> tuple[list[dict], int]:
    """每组面板计划 + 总块高，测画共用。"""
    tw_ = _text_w(content_w)
    plans: list[dict] = []
    for gi, g in enumerate(_groups_of(data)):
        rows = [S.wrap_cjk(str(row), FS_BODY, "regular", tw_) for row in _rows_of(g)]
        rows = [r for r in rows if r]
        row_h = sum(LINE_H * len(lines) + ITEM_GAP for lines in rows)
        h = GRP_PAD + GRP_HEAD + row_h - (ITEM_GAP if rows else 0) + GRP_PAD
        plans.append({"no": gi + 1, "name": str(g.get("name") or ""),
                      "rows": rows, "h": h})
    total = sum(pl["h"] for pl in plans) + GROUP_GAP * max(0, len(plans) - 1)
    return plans, total


def _height(data: dict, blocks_h: int) -> int:
    sub_h = (SUB_H + SUB_GAP) if str(data.get("subtitle") or "") else 0
    return UI.TITLE_H + sub_h + blocks_h + TIP_GAP + TIP_LEAD + FS_BODY + PAD_B


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
    plans, blocks_h = _plan(data, content_w)
    boxes = kw.pop("boxes", None)

    sub = str(data.get("subtitle") or "")
    sub_h = SUB_H if sub else 0
    h = max(380, _height(data, blocks_h))

    c = Canvas(width, h, p["page_color"], theme=t)

    def rec(label, txt, x, y, size, anchor="lm"):
        if boxes is None:
            return
        w = UI.tw(txt, size, "regular")
        hh = int(size)
        if anchor == "lm":
            bb = (x, y - hh // 2, x + w, y + hh // 2)
        elif anchor == "rm":
            bb = (x - w, y - hh // 2, x, y + hh // 2)
        else:
            bb = (x, y, x + max(1, w), y + hh)
        boxes.append((label,) + bb)

    x0, x1 = PAD, PAD + content_w
    y = UI.card_shell(c, p, str(data.get("title") or "傀儡炼制"), width=width)

    # ---- 成傀摘要条：金边深底 + 左金菱钉 + 高亮文字 -------------------------
    if sub_h:
        UI.panel(c, p, (x0, y, x1, y + SUB_H), state="current", radius=12,
                 watermark=False, outline=p["gold_dk"])
        d = UI.img_of(c)
        dd = ImageDraw.Draw(d)
        pcy = y + SUB_H / 2
        dd.polygon([(x0 + 20, pcy - 8), (x0 + 28, pcy), (x0 + 20, pcy + 8), (x0 + 12, pcy)],
                   fill=UI.rgb(p["gold"]) + (255,))
        dd.polygon([(x0 + 20, pcy - 4), (x0 + 24, pcy), (x0 + 20, pcy + 4), (x0 + 16, pcy)],
                   fill=UI.rgb(p["gold_dk"]) + (255,))
        s = UI.fit(sub, FS_SUB, "bold", content_w - 72)
        c.text((x0 + 38, pcy), s, size=FS_SUB, color=p["gold_hi"], role="bold",
               bold=True, anchor="lm")
        rec("subtitle", s, x0 + 38, pcy, FS_SUB)
        y += SUB_H + SUB_GAP

    # ---- 分组面板 -----------------------------------------------------------
    for gi, gp in enumerate(plans):
        gh = gp["h"]
        state = "current" if gi == 0 else "done"
        UI.panel(c, p, (x0, y, x1, y + gh), state=state, radius=13)
        ocx = x0 + GRP_PAD + ORD_D // 2
        ocy = y + GRP_PAD + GRP_HEAD // 2
        UI.ord_badge(c, p, ocx, ocy, gp["no"], "done" if gi else "current",
                     r=ORD_D // 2)
        gx = x0 + GRP_PAD + ORD_D + ORD_GAP
        gname = UI.fit(gp["name"], FS_GROUP, "kai", _text_w(content_w))
        UI.kai_text(c, (gx, ocy), gname, FS_GROUP,
                    p["gold"] if gi == 0 else p["gold_hi"], anchor="lm")
        rec("g%d_name" % gi, gname, gx, ocy, FS_GROUP)
        iy = y + GRP_PAD + GRP_HEAD
        for ri, lines in enumerate(gp["rows"]):
            for li, line in enumerate(lines):
                cy = iy + li * LINE_H + LINE_H // 2
                color = p["text"] if li == 0 else p["dim"]
                c.text((gx, cy), line, size=FS_BODY, color=color,
                       role="regular", anchor="lm")
                rec("i%d_%d_%d" % (gi, ri, li), line, gx, cy, FS_BODY)
            iy += LINE_H * len(lines) + ITEM_GAP
        y += gh + GROUP_GAP
    if plans:
        y -= GROUP_GAP

    # ---- 底部：回纹横带 + 提示 ----------------------------------------------
    y += TIP_GAP
    UI.divider(c, p, y, x0 + 6, x1 - 6, style="band")
    y += TIP_LEAD
    tip = str(data.get("tip") or "")
    s = UI.fit(tip, FS_BODY, "regular", content_w - 24)
    c.text((x0 + 10, y + FS_BODY // 2), s, size=FS_BODY,
           color=p["dim"], role="regular", anchor="lm")
    rec("tip", s, x0 + 10, y + FS_BODY // 2, FS_BODY)

    if source:
        c.text((width - PAD, h - 34), source, size=14, color=p["weak"], anchor="rb")
    return c


def card_height(data: dict, content_w: int) -> float:
    data = dict(data or _DEMO)
    if not _groups_of(data):
        data = dict(_DEMO)
    _plans, blocks_h = _plan(data, content_w)
    return _height(data, blocks_h)
