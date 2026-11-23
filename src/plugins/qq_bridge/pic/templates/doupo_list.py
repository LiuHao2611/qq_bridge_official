# -*- coding: utf-8 -*-
"""pic/templates/doupo_list.py —— 斗破·通用列表卡。

用于群聊「dp斗技 / dp异火 / dp宝库 / dp背包 / dp排行」等列表型口令的卡片化回复，
替代原先的多行纯文本（逐项扫描不便）。

版式（羊皮纸米 / 朱红 / 暗金玄幻观感，与 doupo.py 人物卡、doupo_menu.py 菜单卡同一套调色）：
  * 顶部居中标题 + 图标 + 贯通分割线；
  * 可选副标题（金币 / 药渣 / 进度 等概览）；
  * 列表行：可选左侧 badge（键位 / 名次）+ 主标题（左）+ 右侧对齐信息（价格 / 战力 / 状态）
    + 可选副行（说明，过长自动折行）+ 可选等级渐变条；
  * 底部提示 tip。

设计约束：
  * 纯 Pillow 自绘，不依赖浏览器/渲染引擎（项目铁律）；
  * **不使用 emoji**（图片里 emoji 易缺字形变方框）；
  * 颜色复用 ``Theme.bg_overrides["doupo"]``（与人物卡 / 菜单卡同一卡片主题）。

输入（build 的 params，均为 JSON 友好类型，便于编辑器预览）：
  icon      标题图标名（icons.ICONS 之一，缺省/非法回退 sword）
  title     标题
  subtitle  副标题（可留空）
  rows      [{"badge","title","right","sub","note","bar":{"cur","max"},"accent",
              "head"}]
             note = 可选的「下一行提示」（如「下级需 60 药渣」），独立成行、
             用显式 NOTE_GAP 与副行拉开，**禁止用全角空格塞进 sub 凑间距**。
             head = True 表示这一行是**分类标题**（如异火卡的「可收服的异火」），
             模板画成「玫红菱形 + 楷体金字」，与普通条目分层；行高不变。
  tip      底部提示（可留空）
"""
from __future__ import annotations

from PIL import ImageDraw

from .. import icons
from ..canvas import Canvas
from ..theme import Theme

# 与人物卡 / 菜单卡共用配色/度量/进度条工具（同包内复用，避免两份 _pal 各自漂移）
from .doupo import _bar, _fit, _pal, _w
from . import doupo_ui as UI
from . import doupo_style as S

# ---- 排版（2026-11-14 统一字号规范）----
PAD = 36              # 内容左右内边距（2026-11-22 由 28 加到 36：游戏风回纹外框
                      # 画在 10、内线 17，PAD=28 时内容距框线只剩 11px，
                      # 用户明确反馈「字和边框黏在一起」，故统一到 36 → 距框线 26px）
FS_TITLE = 34
FS_SUB = S.FS_SUB + 1          # 18 副标题
FS_ITEM = S.FS_KEY             # 24 主标题 / 价格 / 战力（重点，加粗）
FS_SMALL = S.FS_SUB            # 17 说明
TIP_GAP = 12             # 回纹横带到下方 tip 文字的间距
#: 最后一行内容底边到回纹横带顶边的留白。2026-11-22 生产数据实拍发现：异火卡最后
#: 一条「下级需 120 药渣」的下缘被横带描边**压住约 2px**（note 行墨迹会比行高预算
#: 出血 4px 以内），背包卡「地脉火芝，地灵丹主药」也只余 7px。故显式加 14px，
#: 且 build 与 _measure 必须同源（否则卡片底部会多出/少掉一段空白）。
BAND_GAP = 14
FS_TIP = S.FS_LABEL            # 16 底部提示
FS_BADGE = S.FS_SUB + 1        # 18 快捷键 / 名次
ICON = 22
BAR_H = 12
BADGE_W = 40
ROW_PAD = 14
ROW_MIN = 66
#: 主标题与副行说明之间的呼吸空间。2026-11-14 视觉验收发现：原来 cy 只比标题
#: 底部多约 0.5px，斗技/宝库卡上「技能名」与「升一级伤害 +20%」几乎粘成一行。
#: 行高与绘制位置必须同步加这个值，否则说明文字会被下一行盖住。
TITLE_SUB_GAP = 8
DESC_LH = FS_SMALL + 4
#: 独立提示行（2026-11-15 异火卡「下级需 N 药渣」不再用全角空格塞进 sub）：
#: 显式字号 + 显式 gap，行高与绘制位置共用同一组常量。
FS_NOTE = S.FS_LABEL          # 16
NOTE_LH = FS_NOTE + 4         # 20
NOTE_GAP = 8
ICON_STYLE = "line"

#: 编辑器预览用的示例数据（pic 包不 import 插件层，故内置示例）。
_DEMO = {
    "icon": "bolt",
    "title": "斗技",
    "subtitle": "快捷键 1/3/7/9 · 升一级伤害 +20%",
    "rows": [
        {"badge": "1", "title": "龙吟碎　3/5", "sub": "升一级伤害 +20%，将目标击晕几率 +20%",
         "bar": {"cur": 3, "max": 5}},
        {"badge": "3", "title": "天火三玄变　1/5", "sub": "焚炎附体，三段火劲贯体",
         "bar": {"cur": 1, "max": 5}},
    ],
    "tip": "用法：升级 龙吟碎 · 现有 焚血散 ×3",
}


def _rows_of(data) -> list:
    return [r for r in (data.get("rows") or []) if isinstance(r, dict)]


#: 网格模式固定格高（4 列 × 6 行 = 24 格，每格只放名称 + 数量）。
GRID_CELL_H = 52
#: 网格格子间距（格子有自身边框，间距比普通列表行小）。
GRID_GAP = 8


def _row_h(item: dict, content_w: int, grid: bool = False) -> int:
    """单行高度：主标题 + 可选副行（折行）+ 可选等级条。

    ``grid=True``（背包 4 列紧凑网格）固定为 :data:`GRID_CELL_H`，不渲染长说明，
    24 格只需 6 行，杜绝「背包卡一长列 3000px」。
    """
    if grid:
        return GRID_CELL_H
    h = FS_ITEM + 12
    sub = item.get("sub") or ""
    if sub:
        lines = Canvas.wrap(sub, FS_SMALL, "regular", content_w - BADGE_W - 14)
        h += max(1, len(lines)) * DESC_LH + TITLE_SUB_GAP
    note = item.get("note") or ""
    if note:
        lines = Canvas.wrap(note, FS_NOTE, "regular", content_w - BADGE_W - 14)
        h += NOTE_GAP + max(1, len(lines)) * NOTE_LH
    if item.get("bar"):
        # 字号放大后要给渐变条留足空间，否则说明文字会压到条上
        h += BAR_H + 18
    return max(ROW_MIN, h)


#: 多列布局的列间距。data["columns"] 控制列数（宝库=2，背包=4）：
#: 宝库药材 50+ 种单列会到 5000px；背包 24 格按游戏实机 4 列网格展示，避免长列。
COL_GAP = 18
MAX_COLS = 4


def _cols(data: dict) -> int:
    """列数：data["columns"] 钳到 [1, MAX_COLS]；>=3 时启用紧凑网格行样式。"""
    try:
        return max(1, min(MAX_COLS, int(data.get("columns") or 1)))
    except (TypeError, ValueError):
        return 1


def _grid(data: dict) -> bool:
    """紧凑网格模式（背包 4 列）：每格只画名称 + 数量，不画长说明。

    游戏实机「宝库·包裹」是 4×6 格，每格一个物品图标 + 名称，说明在选中时
    才显示。QQ 卡片无交互，这里用「格内名称 + 数量、说明并入 tip」等价表达，
    24 格 6 行即可看完，不再是一长列 24 行带说明的巨型卡片。
    """
    return _cols(data) >= 3 and bool(data.get("grid"))


def _col_width(content_w: int, cols: int) -> int:
    return (content_w - COL_GAP * (cols - 1)) // cols


def _measure(data: dict, content_w: int) -> int:
    """总高度（与 build 里的 y 步进严格一致，否则卡底留白会歪）。"""
    y_head = UI.TITLE_H
    if data.get("subtitle"):
        y_head += FS_SUB + 10
    cols = _cols(data)
    cw = _col_width(content_w, cols)
    grid = _grid(data)
    ys = [y_head] * cols
    for i, r in enumerate(_rows_of(data)):
        k = i % cols
        ys[k] += _row_h(r, cw, grid=grid) + (GRID_GAP if grid else ROW_PAD)
    # 末尾留白用 UI.PAD_B(54)：底部两角只有短 L 线，26 收尾会贴框（2026-11-22）
    tail = GRID_GAP if grid else ROW_PAD   # 扣回最后一行多加的间距（网格/列表各自口径）
    y = max(ys) - tail + BAND_GAP + 1 + TIP_GAP + FS_TIP + UI.PAD_B
    return max(330, y)


def build(t: Theme, data: dict | None = None, width: int | None = None,
          source: str | None = None, **kw) -> Canvas:
    data = data or {}
    width = int(width or getattr(t, "width", 720) or 720)
    width = max(560, min(width, 900))
    p = _pal(t)
    content_w = width - PAD * 2
    h = _measure(data, content_w)

    #: 可选：card-design check_boxes 自检包围盒（默认 None，零开销、无创）。
    boxes = kw.pop("boxes", None)

    c = Canvas(width, h, p["page_color"], theme=t)

    def rec(label: str, txt: str, x: int, y: int, size: int, role: str,
            anchor: str) -> None:
        if boxes is None:
            return
        w = _w(txt, size, role)
        hh = int(size)
        if anchor == "lm":
            bb = (x, y - hh // 2, x + w, y + hh // 2)
        elif anchor == "rm":
            bb = (x - w, y - hh // 2, x, y + hh // 2)
        elif anchor == "mm":
            bb = (x - w // 2, y - hh // 2, x + w / 2, y + hh / 2)
        else:                                  # la
            bb = (x, y, x + w, y + hh)
        boxes.append((label,) + bb)

    x0 = PAD
    y = PAD

    # ---- 标题（青蓝云纹牌匾，内核统一）----
    title = str(data.get("title") or "斗破")
    y = UI.card_shell(c, p, title, width=width)
    rec("title", title, width // 2, y - 40, FS_TITLE, "kai", "mm")

    # ---- 副标题 ----
    sub = data.get("subtitle")
    if sub:
        S.small(c, (x0, y + FS_SUB // 2), sub, size=FS_SUB,
                color=p["dim_color"], max_w=content_w)
        rec("subtitle", S.fit(sub, content_w, FS_SUB, "regular"), x0, y + FS_SUB // 2,
            FS_SUB, "regular", "lm")
        y += FS_SUB + 10

    # ---- 列表行 ----
    cols = _cols(data)
    cw = _col_width(content_w, cols)
    grid = _grid(data)
    # 表头高度必须与 _measure 完全一致（含副标题），否则双列底边会错位
    col_y = [y] * cols
    for ri, item in enumerate(_rows_of(data)):
        k = ri % cols
        cx0 = x0 + k * (cw + COL_GAP)
        y = col_y[k]
        rh = _row_h(item, cw, grid=grid)
        if grid:
            # 游戏包裹式紧凑格子：每格只画「名称 + 数量」，长说明不进格子
            # （说明是选中态信息，QQ 卡无交互，故并入底部 tip）。锁定项用玫红字。
            c.rounded_rect((cx0, y, cx0 + cw - 2, y + rh - 2), radius=8,
                           fill=p["panel"], outline=p["panel_ln"], width=1)
            name_txt = str(item.get("title") or "")
            is_locked = name_txt.startswith("〔锁〕")
            shown = S.fit(name_txt, cw - 16, FS_SMALL, "regular")
            tcol = p["accent_color"] if is_locked else p["text_color"]
            cell_cx = cx0 + (cw - 2) // 2
            cell_cy = y + rh // 2 - 1
            S.small(c, (cell_cx, cell_cy), shown, size=FS_SMALL, color=tcol,
                    anchor="mm", max_w=cw - 16)
            rec("g%d" % ri, shown, cell_cx, cell_cy, FS_SMALL, "regular", "mm")
            col_y[k] += rh + GRID_GAP
            continue
        badge = item.get("badge")
        # 角标必须与主标题同一行居中对齐（card-design 红线 4：同列元素起点一致）。
        # 2026-11-15 多维度重构后行变高，旧写法「行内垂直居中」会让角标漂到副行位置、
        # 与技能名脱开 —— 这里改成锚定主标题中线（ty 在下方计算，用同一表达式）。
        ty = y + FS_ITEM // 2 + 2
        if badge:
            btext = p["accent_color"] if item.get("accent") else p["chip_text"]
            bcolor = p["accent_color"] if item.get("accent") else p["chip_line"]
            bh = FS_BADGE + 12
            # 钳到行顶：badge 比「行顶到标题中线」的 14px 更高，
            # 直接 ty-h/2 会算出 -1px 向上出血（check_boxes 会报溢出）。
            by = max(y, ty - bh // 2)
            c.rounded_rect((cx0, by, cx0 + BADGE_W - 8, by + bh), radius=8,
                           fill=p["chip_bg"], outline=bcolor, width=1)
            S.key(c, (cx0 + (BADGE_W - 8) // 2, by + bh // 2),
                  badge, size=FS_BADGE, color=btext, anchor="mm",
                  max_w=BADGE_W - 16)
            rec("r%d.badge" % ri, S.fit(badge, BADGE_W - 16, FS_BADGE, "regular"),
                cx0 + (BADGE_W - 8) // 2, by + bh // 2,
                FS_BADGE, "regular", "mm")
            title_x = cx0 + BADGE_W + 4
        else:
            title_x = cx0 + 14

        # 右侧信息测量（绘制走 bold，度量也必须走 bold，否则标题会被挤掉）
        right = item.get("right")
        right_w = _w(str(right), FS_ITEM, "bold") + 14 if right else 0

        # 主标题（放大加粗）
        title_txt = str(item.get("title") or "")
        title_max = (cx0 + cw) - title_x - 8 - right_w
        if item.get("head"):
            # 分组头行（数据层显式标 head=True，如异火卡的「本宗传承 / 可收服的异火」）：
            # 玫红菱形 + 楷体金字。以前这种行和普通条目一样是白色粗体，玩家分不清
            # 「这是分类」还是「这是某一项」（2026-11-22 实拍确认层级不清）。
            txx = cx0 + 20
            ImageDraw.Draw(UI.img_of(c)).polygon(
                [(cx0 + 6, ty - 7), (cx0 + 13, ty), (cx0 + 6, ty + 7), (cx0 - 1, ty)],
                fill=UI.rgb(p["fret"]) + (255,))
            UI.kai_text(c, (txx, ty), S.fit(title_txt, (cx0 + cw) - txx - 8 - right_w,
                                            FS_ITEM, "kai"),
                        FS_ITEM, p["gold_hi"], anchor="lm")
            rec("r%d.title" % ri,
                S.fit(title_txt, (cx0 + cw) - txx - 8 - right_w, FS_ITEM, "kai"),
                txx, ty, FS_ITEM, "kai", "lm")
            title_x = txx
        else:
            tcolor = p["accent_color"] if item.get("accent") else p["text_color"]
            S.key(c, (title_x, ty), title_txt,
                  size=FS_ITEM, color=tcolor, max_w=title_max)
            rec("r%d.title" % ri, S.fit(title_txt, title_max, FS_ITEM, "bold"),
                title_x, ty, FS_ITEM, "bold", "lm")
        # 右侧信息（价格 / 战力 / 状态，同为重点：暗金加粗）
        if right:
            S.key(c, (cx0 + cw, ty), right, size=FS_ITEM,
                  color=p["accent_color"], anchor="rm",
                  max_w=(cx0 + cw) - title_x)
            rec("r%d.right" % ri,
                S.fit(str(right), (cx0 + cw) - title_x, FS_ITEM, "bold"),
                cx0 + cw, ty, FS_ITEM, "bold", "rm")

        # 副行（折行，小字明确不加粗）
        cy = ty + FS_ITEM // 2 + 8 + TITLE_SUB_GAP
        sub = item.get("sub") or ""
        if sub:
            lines = Canvas.wrap(sub, FS_SMALL, "regular", (cx0 + cw) - title_x - 8)
            for i, ln in enumerate(lines):
                S.small(c, (title_x, cy + i * DESC_LH), ln,
                        size=FS_SMALL, color=p["dim_color"])
                rec("r%d.sub%d" % (ri, i), ln, title_x, cy + i * DESC_LH,
                    FS_SMALL, "regular", "lm")
            cy += len(lines) * DESC_LH
        # 独立提示行：显式 gap，不与副行连读（card-design 红线 5：禁止空格凑间距）
        note = item.get("note") or ""
        if note:
            lines = Canvas.wrap(note, FS_NOTE, "regular", (cx0 + cw) - title_x - 8)
            ny = cy + NOTE_GAP + NOTE_LH // 2
            for i, ln in enumerate(lines):
                S.small(c, (title_x, ny + i * NOTE_LH), ln,
                        size=FS_NOTE, color=p["accent_color"])
                rec("r%d.note%d" % (ri, i), ln, title_x, ny + i * NOTE_LH,
                    FS_NOTE, "regular", "lm")
        # 等级渐变条
        bar = item.get("bar")
        if bar:
            by = y + rh - BAR_H - 8
            cur = int(bar.get("cur", 0))
            mx = max(1, int(bar.get("max", 1)))
            _bar(c, title_x, by, (cx0 + cw) - title_x, int(cur * 100 / mx),
                 p["bar_from"], p["bar_to"], p["track_color"])

        col_y[k] += rh + ROW_PAD

    # ---- 底部提示 ----
    # 最后一行 -> BAND_GAP -> 回纹横带 -> TIP_GAP -> tip 文字
    y = max(col_y) - ROW_PAD + BAND_GAP
    UI.divider(c, p, y, x0 + 6, x0 + content_w - 6, style="band")
    y += TIP_GAP
    tip = data.get("tip")
    if tip:
        S.small(c, (x0, y + FS_TIP // 2), tip, size=FS_TIP,
                color=p["dim_color"], max_w=content_w)
        rec("tip", S.fit(tip, content_w, FS_TIP, "regular"), x0, y + FS_TIP // 2,
            FS_TIP, "regular", "lm")

    if source:
        S.small(c, (width - PAD, h - PAD + 6), source, size=14,
                color=p["dim_color"], anchor="rb")
    return c
