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

from PIL import Image, ImageDraw, ImageFilter

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
FS_CORNER = 14                 # 格子角标（丹纹「几纹」/ 锁定「锁」）：小字，别抢名称
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


#: 网格模式固定格高（4 列，每格只放名称 + 数量 + 角标）。
#: 2026-11-24：52 → 60。格子右上/左上要放「几纹」「锁」角标；52 高时角标
#: 与名称上缘会黏在一起，加 8px 拉开净空。
GRID_CELL_H = 60
#: 网格格子间距（格子有自身边框，间距比普通列表行小）。
GRID_GAP = 8
#: 网格格子内名称的垂直中线距格顶的距离。**与有无角标无关**——同一行必须基线一致。
GRID_NAME_CY = GRID_CELL_H // 2 - 1

# ---- 格子角标（2026-11-24 二次改版：用户「特殊字体 + 金色光晕 + 骑在边框上」）----
#: 角标字用**楷体**（doupo_kai.ttf，与「包裹」牌匾同一族；缺文件时 fonts 会告警
#: 并退等线 Bold）。16px + 1px 伪粗是小字号下不糊的上限，绝不放大到 22（格子装不下）。
FS_CORNER = 18
#: 角标中线比格子顶边（= 上边框线）偏移 5px：字的墨迹压在框线上、且大部分落在格子内（归属明确，不会被看成上一行的）（几何中线上移
#: 会让字看着浮在缝里）。色块上探 6px < GRID_GAP(8)，不会啃上一行格子。
CORNER_CY_OFF = 5
#: 遮边框色块在字四周的留白（左右 / 上下）。左右够宽，断口才不会贴字。
CORNER_PAD_X = 5
CORNER_PAD_Y = 1
#: 角标距格子左右内沿的距离。必须 > 格子圆角半径(8)，否则会把左上/右上圆角整块糊掉
#: ——2026-11-24 实图抓到「锁」把圆角啃成方角，看着像渲染坏了。
CORNER_INSET = 12
#: 光晕外扩：金雾画在比色块更大的透明层上，才不会被色块边界截成硬边，
#: 顺便让金光微微溢到断口两侧的框线上（这才是「光晕」而不是描边）。
CORNER_GLOW = 6


def corner_badge_h() -> int:
    """角标色块高度（= 字高 + 上下留白）。"""
    return FS_CORNER + CORNER_PAD_Y * 2


def corner_cy(cell_y: int) -> int:
    """角标色块的垂直**中线**坐标（格子顶边往下 CORNER_CY_OFF）。"""
    return cell_y + CORNER_CY_OFF


def corner_top(cell_y: int) -> int:
    """角标色块上缘 y（应 < cell_y：要压在边框线上，「截断」才看得出来）。"""
    return corner_cy(cell_y) - corner_badge_h() // 2


def corner_bottom(cell_y: int) -> int:
    """角标色块下缘 y。"""
    return corner_top(cell_y) + corner_badge_h()


def corner_name_gap() -> int:
    """角标下缘到名称上缘的净空（像素）；名称中线见 :data:`GRID_NAME_CY`。"""
    name_top = GRID_NAME_CY - FS_SMALL // 2
    return name_top - (CORNER_CY_OFF + corner_badge_h() // 2)


def corner_overhang() -> int:
    """色块向上探出格子顶边的像素数（必须 ≤ GRID_GAP，别啃上一行）。"""
    return max(0, int(-(corner_top(0))))



def draw_corner_badge(c: Canvas, g: dict, text: str, cell_x: int, cell_w: int,
                      cell_y: int, *, right: bool) -> tuple[int, int, int, int]:
    """在格子上边框画一个「骑框」角标：底色块截断边框 + 金色光晕楷体字。

    用户 2026-11-24 口径（二次改版）：
      * **特殊字体**：楷体（``doupo_kai.ttf``，与「包裹」牌匾同族），不是等线；
      * **金色光晕**：字外围一圈高斯模糊的金雾（描边做不到「晕」）；
      * **骑在边框上**：先用格子底色铺一块把边框线盖断，再压字 —— 看上去就是
        边框被这块牌子截断了，比塞在格子里直观。

    做法：色块（panel 实心，盖断框线）先画在画布上；再把「模糊金雾 + 伪粗楷体字」
    画在一块**比色块更大**的透明 tile 上整块贴回 —— 光晕因此能溢出断口两侧，
    不会被色块边界切成硬边。只对小球做模糊，64 格背包也不卡。

    返回色块包围盒 ``(x0, y0, x1, y1)``（供 ``boxes`` 自检登记）。
    """
    size = FS_CORNER
    # kai 的 1px 伪粗会向左右各溢 1px，量宽度时补回来，否则字贴到断口边缘
    tw_ = int(UI.tw(text, size, "kai")) + 2
    pw = tw_ + CORNER_PAD_X * 2
    ph = corner_badge_h()
    cy = corner_cy(cell_y)
    px0 = (cell_x + cell_w - 2 - CORNER_INSET - pw) if right else (cell_x + CORNER_INSET)
    py0 = cy - ph // 2
    img = UI.img_of(c)

    # ① 截断边框：格子底色实心块，无描边 —— 框线在这里就断了
    ImageDraw.Draw(img).rounded_rectangle(
        (px0, py0, px0 + pw - 1, py0 + ph - 1), radius=3,
        fill=UI.rgb(g["panel"]) + (255,))

    # ② 光晕 + 楷体字：透明层比色块大 CORNER_GLOW，金雾可外溢
    m = CORNER_GLOW
    bw, bh = pw + m * 2, ph + m * 2
    f = UI.font(size, "kai")
    ccx, ccy = bw / 2.0, bh / 2.0
    glow = Image.new("RGBA", (bw, bh), (0, 0, 0, 0))
    ImageDraw.Draw(glow).text((ccx, ccy), text, font=f, anchor="mm",
                              fill=UI.rgb(g["gold_hi"]) + (255,))
    glow = glow.filter(ImageFilter.GaussianBlur(3.0))
    tile = Image.new("RGBA", (bw, bh), (0, 0, 0, 0))
    tile.alpha_composite(glow)          # 单层：叠两层会把笔画缝隙糊成一片
    dd = ImageDraw.Draw(tile)
    col = UI.rgb(g["gold"]) + (255,)          # 字取正金，晕取高光金：亮环绕实金
    for dx in (-1, 1):                        # 只横向伪粗：竖向叠加会在小字号上发虚
        dd.text((ccx + dx, ccy), text, font=f, fill=col, anchor="mm")
    dd.text((ccx, ccy), text, font=f, fill=col, anchor="mm")
    img.alpha_composite(tile, (int(px0) - m, int(py0) - m))
    return (px0, py0, px0 + pw, py0 + ph)


def _row_h(item: dict, content_w: int, grid: bool = False) -> int:
    """单行高度：主标题 + 可选副行（折行）+ 可选等级条。

    ``grid=True``（背包 4 列紧凑网格）固定为 :data:`GRID_CELL_H`，不渲染长说明，
    4 格一行，卡片高度随物品数自然增长（2026-11-24 起背包不再截断/翻页）。
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
#: 宝库药材 50+ 种单列会到 5000px；背包按游戏实机 4 列网格铺开，行数随物品数增长。
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
    才显示。    QQ 卡片无交互，这里用「格内名称 + 数量、说明并入 tip」等价表达；格数不设上限，
    物品多时卡片向下延长（2026-11-24 用户口径「背包别搞翻页，延长」）。
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
            # （说明是选中态信息，QQ 卡无交互，故并入底部 tip）。
            c.rounded_rect((cx0, y, cx0 + cw - 2, y + rh - 2), radius=8,
                           fill=p["panel"], outline=p["panel_ln"], width=1)
            name_txt = str(item.get("title") or "")
            is_locked = bool(item.get("corner_tl")) or name_txt.startswith("〔锁〕")
            tr = str(item.get("corner_tr") or "")
            tl = str(item.get("corner_tl") or "")
            # 角标（2026-11-24 二次改版）：右上 = 丹纹「几纹」，左上 = 「锁」。
            # 骑在格子上边框上（底色块截断边框 + 金色光晕楷体），见 draw_corner_badge。
            # 必须在格子 rounded_rect 之后画，否则框线会把色块盖回去。
            for _txt, _right in ((tl, False), (tr, True)):
                if not _txt:
                    continue
                bb = draw_corner_badge(c, p, _txt, cx0, cw, y, right=_right)
                if boxes is not None:
                    boxes.append(("g%d.%s" % (ri, "tr" if _right else "tl"),) + bb)
            shown = S.fit(name_txt, cw - 16, FS_SMALL, "regular")
            tcol = p["accent_color"] if is_locked else p["text_color"]
            cell_cx = cx0 + (cw - 2) // 2
            # 名称中线**恒定**，不因角标下移：同行有无角标的格子必须基线对齐
            # （card-design 红线「同列同排元素起点一致」，2026-11-24 实图见破厄丹比
            # 三纹青灵丹低 4px → 已修）。角标只是占掉格子上缘，不挤名称。
            cell_cy = y + GRID_NAME_CY
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

        # 主标题（放大加粗）。网格模式可用 grid_title 短标题（带纹丹格窄，保住丹名）
        title_txt = str(item.get("grid_title") or "") if grid else ""
        title_txt = title_txt or str(item.get("title") or "")
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
