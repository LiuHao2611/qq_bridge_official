# -*- coding: utf-8 -*-
"""pic/templates/doupo_item.py —— 斗破 ·「查看 <物品名>」详情卡（2026-11-25 重做）。

用户原话（附截图）：「这个物品查看卡片别搞两个气泡和123，正常段落描述就行，
找解包游戏里的展示框，做好看点，保持老游戏的马赛克风格」。

旧版直接复用 :mod:`doupo_craft` 的「成傀卡」内核 —— 两个圆角气泡面板 +
①②③ 金色序号徽。那是炼制回执的「分步骤」语义，套到「这是一件东西、它有什么
用、从哪来」的静态说明上就显得像任务流程，而且圆角+渐变离解包游戏的
硬边像素框相去甚远。本模板按 ``D:\\AI\\doupo\\emu\\yh_desc_x5.png`` 的信息框
重画：

  * **硬边直角框**（``ImageDraw.rectangle``，不是 ``rounded_rect``）：
    玫红 2px 外线 + 蓝灰 1px 内线 + 左上回纹角 —— 解包图里每个描述框
    左上角都有那个小云雷纹角花；
  * **纯色深底** ``g["panel"]``，不在框内铺渐变/云纹（解包图框内是纯色，
    云纹在框**外**，由 :func:`doupo_ui.card_shell` 统一铺）；
  * **正常段落**：段名 = 小金菱形 + 金色楷体；正文 = 逐行折行的点阵黄字
    （浅色主题换深墨字，保证对比度），段与段之间只有一条细暗线，
    **没有序号圆徽、没有圆角气泡**；
  * 底部沿用回纹横带 + tip 单行。

card-design 合规：度量优先（``UI.tw`` / ``UI.fit`` / ``S.wrap_cjk``）、
显式 gap（常量集中在文件头）、高度内容驱动（``_plan`` 测画共用）、
``boxes`` 自检；配色只走 ``_pal(t)`` → ``doupo_ui.pal()``，双主题零改动跟随。

输入由 ``doupo.card.build_item_detail_data`` 生成：
  title / subtitle / groups:[{name, rows:[...]}] / tip
"""
from __future__ import annotations

from PIL import ImageDraw

from ..canvas import Canvas
from ..theme import Theme

from . import doupo_ui as UI
from . import doupo_style as S
from .doupo import _pal

PAD = UI.PAD_X              # 36 内容左右内边距（距外框内线 19px）
PAD_B = UI.PAD_B            # 54 底部留白（避开底部两角短 L 线）
FS_SUB = 17                 # 副标题
FS_HEAD = S.FS_KEY          # 24 段名（楷体，比正文一档）
FS_BODY = S.FS_SUB          # 17 正文
LINE_H = FS_BODY + 9        # 26
HEAD_H = FS_HEAD + 14       # 38 段名行高
SEP_H = 16                  # 段间细线占高
FRAME_PAD_X = 22            # 框内左留白（回纹角 unit=3 占 18px，22 刚好让开）
FRAME_PAD_T = 20            # 框内上留白
FRAME_PAD_B = 22            # 框内下留白
DIAMOND_R = 6               # 段名前小金菱形半径（**不是**序号圆徽）
DIAMOND_GAP = 12            # 菱形到段名文字
TIP_GAP = 20                # 框到回纹横带
TIP_LEAD = 14               # 横带到 tip 文字
MIN_H = 360

_DEMO = {
    "title": "火属性魔核",
    "subtitle": "傀儡材料 · 背包 ×1",
    "groups": [
        {"name": "本效", "rows": [
            "傀儡的一件料，不入口；凑齐配方即开炉",
            "品阶越高，成傀档位与魔核加成越好",
        ]},
        {"name": "从何而来", "rows": [
            "傀儡遗迹 / 魔兽掉落（发「傀儡 遗迹」探）",
        ]},
    ],
    "tip": "发「傀儡 配方」看整套配方 · 发「傀儡」看名录",
}


def _groups_of(data: dict) -> list:
    return [g for g in (data.get("groups") or []) if isinstance(g, dict)]


def _rows_of(g: dict) -> list[str]:
    return [str(r) for r in (g.get("rows") or []) if r]


def _plan(data: dict, content_w: int) -> tuple[list[dict], int]:
    """逐段测高（测画共用）：段名行 + 折行正文 + 段间细线。"""
    text_x = FRAME_PAD_X + DIAMOND_R * 2 + DIAMOND_GAP
    text_w = max(40, content_w - text_x - FRAME_PAD_X)
    plans: list[dict] = []
    for g in _groups_of(data):
        rows = [S.wrap_cjk(row, FS_BODY, "regular", text_w) for row in _rows_of(g)]
        rows = [r for r in rows if r]
        body_h = sum(LINE_H * len(lines) for lines in rows)
        h = HEAD_H + body_h
        plans.append({"name": str(g.get("name") or ""), "rows": rows, "h": h})
    total = (sum(p["h"] for p in plans)
             + SEP_H * max(0, len(plans) - 1))
    return plans, total


def _height(data: dict, blocks_h: int) -> int:
    sub_h = (FS_SUB + 12) if str(data.get("subtitle") or "") else 0
    frame_h = FRAME_PAD_T + blocks_h + FRAME_PAD_B
    return UI.TITLE_H + sub_h + frame_h + TIP_GAP + TIP_LEAD + FS_BODY + PAD_B


def build(t: Theme, data: dict | None = None, width: int | None = None,
          source: str | None = None, **kw):
    data = dict(data or _DEMO)
    if not _groups_of(data):
        data = dict(_DEMO)
    data.setdefault("title", "查看物品")
    data.setdefault("tip", _DEMO["tip"])

    width = int(width or getattr(t, "width", 720) or 720)
    width = max(560, min(width, 900))
    p = _pal(t)
    g = UI.pal(p)
    content_w = width - PAD * 2
    plans, blocks_h = _plan(data, content_w)
    boxes = kw.pop("boxes", None)

    sub = str(data.get("subtitle") or "")
    h = max(MIN_H, int(_height(data, blocks_h)))
    c = Canvas(width, h, g["page"], theme=t)

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
    y = UI.card_shell(c, g, str(data.get("title") or "查看物品"), width=width)
    if sub:
        y = UI.sub_line(c, g, y, sub, size=FS_SUB)

    # ---- 解包游戏展示框：硬边直角 + 双线 + 左上回纹角 ----------------------
    fy0 = int(y)
    fy1 = int(y + FRAME_PAD_T + blocks_h + FRAME_PAD_B)
    d = ImageDraw.Draw(UI.img_of(c))
    d.rectangle((x0, fy0, x1, fy1), fill=UI.rgb(g["panel"]) + (255,))
    d.rectangle((x0, fy0, x1, fy1), outline=UI.rgb(g["fret"]) + (255,), width=2)
    d.rectangle((x0 + 5, fy0 + 5, x1 - 5, fy1 - 5),
                outline=UI.rgb(g["plaq_mid"]) + (255,), width=1)
    # 左上云雷纹角花（解包图 yh_desc_x5.png 每个框左上角都有）
    UI.fret_corner(d, x0 + 7, fy0 + 7, 1, 1, g["fret"], unit=3, width=2)
    # 右下角短 L 呼应（不放完整回纹，避免压正文末尾）
    d.line([(x1 - 22, fy1 - 7), (x1 - 7, fy1 - 7), (x1 - 7, fy1 - 22)],
           fill=UI.rgb(g["fret"]) + (255,), width=2, joint="curve")

    # 游戏本体正文是点阵黄字；浅色主题压成深墨紫，保证白底对比度。
    body_col = g["gold_hi"] if g["page"] == UI.G["page"] else g["text"]
    text_x = x0 + FRAME_PAD_X + DIAMOND_R * 2 + DIAMOND_GAP

    iy = fy0 + FRAME_PAD_T
    for gi, gp in enumerate(plans):
        if gi:
            ly = iy - SEP_H // 2
            d.line((x0 + FRAME_PAD_X, ly, x1 - FRAME_PAD_X, ly),
                   fill=UI.rgb(g["panel_ln"]) + (255,), width=1)
        # 段名：小金菱形（不是 ①②③ 圆徽）+ 楷体段名
        cy = iy + FS_HEAD / 2
        cx = x0 + FRAME_PAD_X + DIAMOND_R
        d.polygon([(cx, cy - DIAMOND_R), (cx + DIAMOND_R, cy),
                   (cx, cy + DIAMOND_R), (cx - DIAMOND_R, cy)],
                  fill=UI.rgb(g["gold"]) + (255,))
        d.polygon([(cx, cy - DIAMOND_R // 2), (cx + DIAMOND_R // 2, cy),
                   (cx, cy + DIAMOND_R // 2), (cx - DIAMOND_R // 2, cy)],
                  fill=UI.rgb(g["gold_hi"]) + (255,))
        name = UI.fit(gp["name"], FS_HEAD, "kai", content_w - text_x + x0 - FRAME_PAD_X)
        UI.kai_text(c, (text_x, cy), name, FS_HEAD, g["gold"], anchor="lm")
        rec("g%d_name" % gi, name, text_x, cy, FS_HEAD)
        iy += HEAD_H
        for ri, lines in enumerate(gp["rows"]):
            for li, line in enumerate(lines):
                ty = iy + li * LINE_H + LINE_H // 2
                c.text((text_x, ty), line, size=FS_BODY, color=body_col,
                       role="regular", anchor="lm")
                rec("i%d_%d_%d" % (gi, ri, li), line, text_x, ty, FS_BODY)
            iy += LINE_H * len(lines)
        if gi < len(plans) - 1:
            iy += SEP_H

    # ---- 底部回纹横带 + tip -----------------------------------------------
    y = fy1 + TIP_GAP
    UI.divider(c, g, y, x0 + 6, x1 - 6, style="band")
    y += TIP_LEAD
    tip = str(data.get("tip") or "")
    s = UI.fit(tip, FS_BODY, "regular", content_w - 24)
    c.text((x0 + 10, y + FS_BODY // 2), s, size=FS_BODY, color=g["dim"],
           role="regular", anchor="lm")
    rec("tip", s, x0 + 10, y + FS_BODY // 2, FS_BODY)

    if source:
        c.text((width - PAD, h - 34), source, size=14, color=g["weak"], anchor="rb")
    return c


def card_height(data: dict, content_w: int) -> float:
    data = dict(data or _DEMO)
    if not _groups_of(data):
        data = dict(_DEMO)
    _plans, blocks_h = _plan(data, content_w)
    return _height(data, blocks_h)
