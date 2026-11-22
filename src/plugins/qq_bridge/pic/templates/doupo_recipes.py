# -*- coding: utf-8 -*-
"""pic/templates/doupo_recipes.py —— 斗破 · 炼药丹方卡（两列 + 分页 / 2026-11-16）。

把原著考据扩充后的丹方清单做成**两列网格卡**：品阶分组标题横跨两列，组内丹药
两两成对、同一行两块等高（``max(h_left, h_right)``），左右列起点固定；顶部副标题
给出炼药师品阶 / 成功率 / 药渣，底部提示显示「第 X/Y 页 · 本页 N 枚」（翻页由
卡片下方按钮切换，data 由发送层注入，模板只负责呈现页码）。

2026-11-16 用户诉求
------------------
  * 「分两列展示」→ 每页 10 枚丹药排成 5 行 × 2 列，不再单列拉长；
  * 「等级不够时丹药不展示」→ 由 card.build_recipes_data 用
    ``core.available_recipes`` 过滤，模板拿到的 rows 已是可见项；
  * 「丹药多了分页」→ ``page/total_pages/total_avail`` 由数据层透出，卡面显示页码。

设计遵循 card-design
--------------------
  * 单一布局入口 ``_plan()``：卡片高、分组、两列成对、每块等高、折行全在此算，
    ``build`` 不各自算（杜绝底部裁切 / 标题压到块上）；
  * 度量优先：``_w`` / ``_fit`` / ``Canvas.wrap`` 实测，绝不估算字宽；
  * 显式 gap：同层间距用 token（4 的倍数），不用空格凑；
  * 字形安全：不用 emoji（齐备用 check 图标），无豆腐框风险；
  * boxes 自检：``build(..., boxes=[...])`` 收集文本包围盒供 check_boxes 校验。

配色复用 ``Theme.bg_overrides["doupo"]``（与人物/菜单/名录卡同一套主题），
数据由 card.build_recipes_data 组装，与 commands.cmd_recipes 同源。
"""
from __future__ import annotations

from PIL import ImageDraw

from .. import icons
from ..canvas import Canvas
from ..theme import Theme

from .doupo import _fit, _pal, _w
from . import doupo_ui as UI
from . import doupo_style as S

# ---- 排版 token（4 的倍数，全卡统一；两列 720 宽，字号主动收小换列数）----
PAD = 36              # 内容左右内边距（2026-11-22 由 28 加到 36：游戏风回纹外框
                      # 画在 10、内线 17，PAD=28 时内容距框线只剩 11px，
                      # 用户明确反馈「字和边框黏在一起」，故统一到 36 → 距框线 26px）
COL_GAP = 12
FS_TITLE = 34
FS_SUB = S.FS_SUB + 1          # 18
FS_GRADE = S.FS_BODY           # 20 品阶分组标题（跨两列）
FS_NAME = S.FS_KEY             # 24 丹名（重点，真粗体）
FS_MAT = S.FS_SUB              # 17 材料清单 / 购买价
FS_DESC = S.FS_LABEL           # 16 功效说明（两列收小）
FS_TIP = S.FS_LABEL            # 16 底部提示
ICON = 20
ICON_STYLE = "line"

# 间距
HEAD_GAP = 12                  # 标题分割线到副标题
SUB_GAP = 14                   # 副标题到第一组
GRADE_GAP = 16                 # 组间距
GRADE_H = FS_GRADE + 12        # 分组标题行高
BLOCK_GAP = 10                 # 组内丹药块间距（行间）
BLOCK_PAD_X = 16               # 块左右内边距（2026-11-22 12→16：单元格内
                               # 文字离边框太近，用户点名「字和边框黏在一起」）
BLOCK_PAD_Y = 12               # 块上下内边距
BLOCK_RADIUS = 10
NAME_MAT_GAP = 6               # 丹名行与材料行
MAT_DESC_GAP = 5               # 材料行与功效行
MAT_LH = FS_MAT + 4            # 材料折行行高
DESC_LH = FS_DESC + 5          # 功效折行行高
READY_W = 56                   # 右侧「可炼/缺料/购买」标记预留宽
FOOT_GAP = 16                  # 列表到底部提示
H_TIP = 12 + FS_TIP + 8

# 编辑器预览示例（pic 包不 import 插件层）
_DEMO = {
    "icon": "wand",
    "title": "炼药",
    "subtitle": "五品炼药师 · 低阶更稳/越阶骤降 · 药渣 ×12",
    "page": 1, "total_pages": 2, "total_avail": 16, "shown": 10,
    "groups": [
        {"grade": "五品", "rows": [
            {"name": "凝血散", "ready": True, "buyonly": False, "buy": 280,
             "mats": [("凝血草", 1), ("活气果", 1), ("罂粟花", 2)],
             "desc": "疗伤药，药力胜过回春散"},
            {"name": "回气丹", "ready": False, "buyonly": True, "buy": 300,
             "mats": [], "desc": "短时间内加速斗气回复"},
        ]},
        {"grade": "四品", "rows": [
            {"name": "三纹青灵丹", "ready": False, "buyonly": False, "buy": 1500,
             "mats": [("青焰草", 1), ("黑天麻", 1)],
             "desc": "助斗师突破成大斗师；大斗师服下有几率升一星，无药效反噬"},
        ]},
    ],
    "tip": "第 1/2 页 · 本页 10 枚 · 可用丹方 16 枚 · 点下方按钮翻页",
}


def _rows_of(g: dict) -> list:
    return [r for r in (g.get("rows") or []) if isinstance(r, dict)]


def _groups_of(data: dict) -> list:
    return [g for g in (data.get("groups") or []) if isinstance(g, dict)]


def _mat_text(mat) -> str:
    """(name, n) → 'name×n'；兼容 dict。"""
    if isinstance(mat, dict):
        return "%s×%s" % (mat.get("name", ""), mat.get("n", ""))
    if isinstance(mat, (tuple, list)) and len(mat) >= 2:
        return "%s×%s" % (mat[0], mat[1])
    return str(mat)


def _mat_lines(row: dict, max_w: int) -> list[str]:
    """材料行折行；无药方（buyonly）改为「N 金购买」一行。"""
    if row.get("buyonly"):
        return ["%d 金购买" % int(row.get("buy", 0))]
    mats = row.get("mats") or []
    if not mats:
        return []
    line = "、".join(_mat_text(m) for m in mats)
    return Canvas.wrap(line, FS_MAT, "regular", max(60, max_w))


def _desc_lines(row: dict, max_w: int) -> list[str]:
    desc = str(row.get("desc") or "")
    if not desc:
        return []
    return Canvas.wrap(desc, FS_DESC, "regular", max(60, max_w))


def _block_h(row: dict, inner_w: int) -> int:
    """单块高度：丹名 + 材料折行 + 功效折行（度量驱动，measure/draw 共用）。"""
    h = BLOCK_PAD_Y * 2 + FS_NAME
    ml = _mat_lines(row, inner_w - READY_W - BLOCK_PAD_X)
    if ml:
        h += NAME_MAT_GAP + len(ml) * MAT_LH + MAT_DESC_GAP
    dl = _desc_lines(row, inner_w)
    if dl:
        h += len(dl) * DESC_LH
    return max(h, BLOCK_PAD_Y * 2 + FS_NAME + 28)


# ---------------------------------------------------------------------------
# 单一布局入口：卡片高 / 分组 / 两列成对 / 每块 y 全在这里算
# ---------------------------------------------------------------------------
def _cols(width: int):
    """(content_w, col_w, inner_w, bx0, bx1) —— 两列固定起点。"""
    content_w = width - PAD * 2
    col_w = (content_w - COL_GAP) // 2
    inner_w = col_w - BLOCK_PAD_X * 2
    return content_w, col_w, inner_w, PAD, PAD + col_w + COL_GAP


def _plan(data: dict, width: int) -> dict:
    width = max(560, min(int(width), 900))
    content_w, col_w, inner_w, bx0, bx1 = _cols(width)

    y = UI.TITLE_H
    if data.get("subtitle"):
        y += FS_SUB + SUB_GAP

    entries: list[dict] = []
    for gi, g in enumerate(_groups_of(data)):
        y += GRADE_GAP if gi else 0
        entries.append({"kind": "section", "y": y, "h": GRADE_H,
                        "grade": str(g.get("grade") or ""), "group": g})
        y += GRADE_H
        rows = _rows_of(g)
        for i in range(0, len(rows), 2):
            pair = rows[i:i + 2]
            hs = [_block_h(r, inner_w) for r in pair]
            rh = max(hs) if hs else 0
            cells = [{"row": r, "x": (bx0 if k == 0 else bx1),
                      "w": col_w, "h": hs[k]}
                     for k, r in enumerate(pair)]
            entries.append({"kind": "pair", "y": y, "h": rh, "cells": cells})
            y += rh + BLOCK_GAP
        if rows:
            y -= BLOCK_GAP

    y += H_TIP
    return {"width": width, "content_w": content_w, "col_w": col_w,
            "inner_w": inner_w, "entries": entries, "h": max(360, y + PAD)}


def _measure(data: dict, width: int) -> int:
    return _plan(data, width)["h"]


def build(t: Theme, data: dict | None = None, width: int | None = None,
          source: str | None = None, **kw) -> Canvas:
    data = data or {}
    if not data.get("groups"):
        data = _DEMO
    width = int(width or getattr(t, "width", 720) or 720)
    width = max(560, min(width, 900))
    p = _pal(t)
    L = _plan(data, width)
    content_w, inner_w, h = L["content_w"], L["inner_w"], L["h"]
    boxes = kw.pop("boxes", None)

    c = Canvas(width, h, p["page_color"], theme=t)

    def rec(label, txt, x, y, size, role, anchor):
        if boxes is None:
            return
        w = _w(txt, size, role)
        hh = int(size)
        if anchor == "lm":
            bb = (x, y - hh // 2, x + w, y + hh // 2)
        elif anchor == "rm":
            bb = (x - w, y - hh // 2, x, y + hh // 2)
        elif anchor == "mm":
            bb = (x - w // 2, y - hh // 2, x + w // 2, y + hh // 2)
        else:
            bb = (x, y, x + w, y + hh)
        boxes.append((label,) + bb)

    x0 = PAD
    y = PAD

    # ---- 标题（青蓝云纹牌匾，内核统一）----
    title = str(data.get("title") or "炼药")
    y = UI.card_shell(c, p, title, width=width)
    rec("title", title, width // 2, y - 40, FS_TITLE, "kai", "mm")

    # ---- 副标题 ----
    sub = data.get("subtitle")
    if sub:
        s = _fit(str(sub), content_w, FS_SUB, "regular")
        c.text((x0, y + FS_SUB // 2), s, size=FS_SUB, color=p["dim_color"],
               role="regular", anchor="lm")
        rec("subtitle", s, x0, y + FS_SUB // 2, FS_SUB, "regular", "lm")
        y += FS_SUB + SUB_GAP

    # ---- 分组标题（跨两列）+ 两列丹药块，全部用 _plan 预算的 y ----
    for ent in L["entries"]:
        if ent["kind"] == "section":
            gy, grade = ent["y"], ent["grade"]
            dd = ImageDraw.Draw(UI.img_of(c))
            gy_c = gy + GRADE_H // 2
            dd.polygon([(x0 + 6, gy_c - 7), (x0 + 13, gy_c), (x0 + 6, gy_c + 7),
                        (x0 - 1, gy_c)], fill=UI.rgb(p["fret"]) + (255,))
            dd.polygon([(x0 + 6, gy_c - 3), (x0 + 9, gy_c), (x0 + 6, gy_c + 3),
                        (x0 + 3, gy_c)], fill=UI.rgb(p["gold_hi"]) + (255,))
            UI.kai_text(c, (x0 + 20, gy_c), grade, FS_GRADE, p["gold_hi"], anchor="lm")
            rec("grade_%s" % grade, grade, x0 + 20, gy_c, FS_GRADE, "kai", "lm")
            lx = x0 + 20 + _w(grade, FS_GRADE, "kai") + 12
            if lx < x0 + content_w:
                c.line((lx, gy + GRADE_H // 2, x0 + content_w, gy + GRADE_H // 2),
                       fill=p["line_color"], width=1)
            continue

        for ci, cell in enumerate(ent["cells"]):
            row, bx, bw = cell["row"], cell["x"], cell["w"]
            by, rh = ent["y"], ent["h"]
            UI.panel(c, p, (bx, by, bx + bw, by + rh), state="done",
                     radius=BLOCK_RADIUS, watermark=False)
            ix = bx + BLOCK_PAD_X
            right = bx + bw - BLOCK_PAD_X
            cy = by + BLOCK_PAD_Y

            # 丹名行：丹名（左，重点粗体）+ 齐备/购买标记（右锚定）
            ny = cy + FS_NAME // 2
            name = str(row.get("name") or "")
            c.text((ix, ny), name, size=FS_NAME, color=p["text_color"],
                   role="bold", bold=True, anchor="lm")
            rec("b%d_%d.name" % (ent["y"], ci), name, ix, ny, FS_NAME, "bold", "lm")

            buyonly = bool(row.get("buyonly"))
            if buyonly:
                rtxt, rcolor = "购买", p["accent_color"]
            else:
                ready = bool(row.get("ready"))
                rtxt = "可炼" if ready else "缺料"
                rcolor = p["accent_color"] if ready else p["dim_color"]
            rx = right - READY_W
            if not buyonly and row.get("ready"):
                icons.draw(c, "check", rx + ICON // 2, ny, ICON, rcolor, style=ICON_STYLE)
            c.text((rx + ICON + 6, ny), rtxt, size=FS_SUB, color=rcolor,
                   role="regular", anchor="lm")
            rec("b%d_%d.ready" % (ent["y"], ci), rtxt, rx + ICON + 6,
                ny, FS_SUB, "regular", "lm")
            cy += FS_NAME + NAME_MAT_GAP

            # 材料行 / 购买价
            ml = _mat_lines(row, inner_w - READY_W - BLOCK_PAD_X)
            for mi, ln in enumerate(ml):
                ly = cy + mi * MAT_LH + MAT_LH // 2
                c.text((ix, ly), ln, size=FS_MAT, color=p["dim_color"],
                       role="regular", anchor="lm")
                rec("b%d_%d.mat%d" % (ent["y"], ci, mi), ln, ix, ly,
                    FS_MAT, "regular", "lm")
            if ml:
                cy += len(ml) * MAT_LH + MAT_DESC_GAP

            # 功效说明（折行）
            for di, ln in enumerate(_desc_lines(row, inner_w)):
                dy = cy + di * DESC_LH + DESC_LH // 2
                c.text((ix, dy), ln, size=FS_DESC, color=p["chip_text"],
                       role="regular", anchor="lm")
                rec("b%d_%d.desc%d" % (ent["y"], ci, di), ln, ix, dy,
                    FS_DESC, "regular", "lm")

    # ---- 底部提示（页码 / 翻页提示由数据层给出，与按钮切换配套）----
    # ty = _plan 里内容块结束的 y（h - PAD - H_TIP），与布局严格同源；
    # 早期版本用 h - PAD - FS_TIP - 8 倒推，与 y_content 差 12px 导致 tip 越界。
    ty = h - PAD - H_TIP
    c.line((x0, ty, x0 + content_w, ty), fill=p["line_color"], width=1)
    tip = data.get("tip")
    if tip:
        s = _fit(str(tip), content_w, FS_TIP, "regular")
        c.text((x0, ty + 12 + FS_TIP // 2), s, size=FS_TIP, color=p["dim_color"],
               role="regular", anchor="lm")
        rec("tip", s, x0, ty + 12 + FS_TIP // 2, FS_TIP, "regular", "lm")

    if source:
        c.text((width - PAD, h - PAD + 6), source, size=14,
               color=p["dim_color"], anchor="rb")
    return c
