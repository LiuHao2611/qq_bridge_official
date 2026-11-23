# -*- coding: utf-8 -*-
"""pic/templates/doupo_faction.py —— 斗破·宗门卡（2026-11-15）。

为什么单独一张模板
------------------
用户要求「把宗门优化成卡片展示，注意设计美化风格」。宗门信息是**分区型**
（宗门概览 / 本门斗技统计 / 代表人物 / 传承异火 / 可转宗门），
塞进通用列表卡 ``doupo_list`` 的「标题 + 右侧 + 说明」三列行式版式会：
① 把异火/宝库/背包/排行一起改坏；② 代表人物、异火这类长段落没法折行。
因此另开专用模板，``doupo_list`` 保持原样（无创原则）。

版式（羊皮纸米 / 朱红 / 暗金，与人物卡、斗技卡同源）
------------------------------------------------
  * 顶部：大标题「宗门」+ globe 细描边图标 + 贯通分割线；
  * 宗门概览：宗门名(30px 粗体朱红) + 右端境界 + 两枚 pill（据点 / 卡池）
    + 一行「境界 lvX · 已开放卡池」小字；
  * 本门斗技统计：3 格大数字（普通 / 高阶 / 特殊），数字按阶位上色；
  * 代表人物：星标分区 + 自动折行段落；
  * 传承异火：bolt 分区 + 自动折行段落；
  * 可转宗门：list 分区 + 每卡池一个规整卡片块（块头：卡池名 / 宗门数 / 转宗金币；
    块体：两列网格，宗名左对齐、门数徽章右锚定）—— 取代旧的 " · " 流式断行；
  * 底部：分割线 + 用法提示。

设计约束（card-design 红线）
--------------------------
  * **度量优先**：所有宽度用 ``_w`` 实测、超宽用 ``_fit`` 截断；
    段落用 ``Canvas.wrap`` 折行（不估算字宽、不用空格凑间距）；
  * 间距统一走 ``BLOCK_GAP`` / ``LINE_H`` 常量，不写魔法偏移；
  * ``_measure`` 与 ``build`` 用**同一套步进**，卡片高度由内容撑开，
    底部不会被裁（测试断言 height == _measure）；
  * ``build(..., boxes=[...])`` 可收集全部文本包围盒，供 `check_boxes`
    自检（溢出 / 重叠 / 相邻间距），把红线拦在交差前；
  * 纯 Pillow 自绘；不使用 emoji；配色读 ``Theme.bg_overrides["doupo"]``。

输入（build 的 data）：
  name/pool/base/npcs/flames/tier_counts/total_skills/open_pools/lv
  groups: [{"pool": "进阶池", "names": ["云岚宗 9门", ...]}]
  switch_cost/tip
"""
from __future__ import annotations

import re

from .. import icons
from ..canvas import Canvas
from ..theme import Theme

# 与人物卡 / 斗技卡共用同一套配色与度量工具
from .doupo import _fit, _pal, _w
from . import doupo_ui as UI
from . import doupo_style as S

# ---- 排版 ----
PAD = 36              # 内容左右内边距（2026-11-22 由 28 加到 36：游戏风回纹外框
                      # 画在 10、内线 17，PAD=28 时内容距框线只剩 11px，
                      # 用户明确反馈「字和边框黏在一起」，故统一到 36 → 距框线 26px）
FS_TITLE = 36
FS_NAME = 30            # 宗门名
FS_BODY = 20
FS_SUB = 17             # 段落正文（清晰下限）
FS_LABEL = 15
FS_KAI = 22             # 卡池名（楷体，游戏牌匾字）——POOL_HEAD_H=26 内放得下
FS_STAT = 36            # 统计大数字
FS_TIP = 16

ICON = 22
ICON_STYLE = "line"

PILL_H = 32
STAT_CELL_H = 86
LINE_H = FS_SUB + 8
BLOCK_GAP = 12
SECTION_H = FS_SUB + 12     # 分区标题行高

H_TITLE = UI.TITLE_H    # 牌匾标题区（内核统一）
H_HEADER = FS_NAME + 12 + PILL_H + 14 + FS_LABEL + 8
H_STATS = SECTION_H + STAT_CELL_H + BLOCK_GAP
H_TIP = 12 + FS_TIP + 8

# ---- 可转宗门：卡池块 + 两列网格（取代旧的 " · " 流式断行） ----
POOL_PAD = 14           # 卡池块内边距
POOL_HEAD_H = 26        # 卡池块头行高（池名 + 统计右锚）
GRID_COLS = 2           # 每行两列
GRID_GAP_X = 16         # 列间距
GRID_CELL_H = 30        # 网格行高
BADGE_H = 24            # 门数徽章高
BADGE_PAD = 10          # 徽章左右内边距
BADGE_FS = FS_LABEL     # 徽章字号（<22 不加粗，清晰优先）
ENTRIES_GAP = 8         # 宗名与徽章之间最小间距（> min_gap token）

#: 三阶统计的数字色（普通/高阶/特殊 逐级升温，一眼看出阶位）
TIER_COLORS = ("text_color", "accent_color", "title_color")

#: 宗门条目格式："萧家 4门" / "魂殿 / 魂族 19门" —— 末尾「数字门」拆成徽章
_COUNT_RE = re.compile(r"^(.*\S)\s+(\d{1,3}门)$")


def _entry(raw) -> tuple[str, str]:
    """宗门条目 →（宗名, 门数）。无「数字门」后缀时门数留空，安全降级。"""
    s = str(raw or "").strip()
    m = _COUNT_RE.match(s)
    return (m.group(1), m.group(2)) if m else (s, "")


def _grid_col_w(content_w: int) -> int:
    inner = content_w - POOL_PAD * 2
    return (inner - GRID_GAP_X) // GRID_COLS


def _rows(entries: list) -> list[list]:
    return [entries[i:i + GRID_COLS] for i in range(0, len(entries), GRID_COLS)]


def _pool_block_h(nrows: int) -> int:
    return POOL_PAD * 2 + POOL_HEAD_H + max(1, nrows) * GRID_CELL_H

# ---- 代表人物：每人一枚胶囊（姓名 + 身份），自动换行 ----
NPC_PAD_X = 12          # 胶囊左右内边距
NPC_GAP = 10            # 同行胶囊间距
NPC_GAP_Y = 8           # 胶囊行间距
NPC_PILL_H = 30         # 胶囊高度
NPC_IN_GAP = 6          # 胶囊内 姓名/身份 间距


def _npc_item(raw) -> tuple[str, str]:
    """'云破天（创始人）' → ('云破天','创始人')；'萧鼎' → ('萧鼎','')。

    只认末尾的全角括号（身份），括号内的 '/'、'，' 原样保留；人名里若含
    全角括号但不在末尾（异常数据）则整条当姓名，不误拆。
    """
    s = str(raw or "").strip()
    l, r = s.rfind("（"), s.rfind("）")
    if l > 0 and r > l:
        return s[:l].strip(), s[l + 1:r].strip()
    return s, ""


def _npc_pill_w(name: str, role: str) -> int:
    w = NPC_PAD_X * 2 + int(_w(name, FS_SUB, "regular"))
    if role:
        w += NPC_IN_GAP + int(_w(role, FS_LABEL, "regular"))
    return w


def _npc_rows(items, wrap_w: int) -> list[list]:
    """人物 → 按行分组的胶囊 [(姓名, 身份, 宽)]，宽度实测、按 NPC_GAP 换行。

    每人是完整单元，不在名字/身份中间断行（根治旧的「云山（上任宗主」/「/斗宗）」
    被拆断）；极端超长单人独占一行，由调用方 _fit 收口，不越界。
    """
    rows: list[list] = []
    cur: list = []
    cur_w = 0
    for raw in (items or []):
        name, role = _npc_item(raw)
        if not name:
            continue
        pw = _npc_pill_w(name, role)
        add = pw if not cur else NPC_GAP + pw
        if cur and cur_w + add > wrap_w:
            rows.append(cur)
            cur, cur_w = [], 0
            add = pw
        cur.append((name, role, pw))
        cur_w += add
    if cur:
        rows.append(cur)
    return rows


def _pack(items, sep: str, width: int) -> list[str]:
    """按条目打包折行：优先整条不拆，单条超宽才字符折行。

    为什么不用 Canvas.wrap 直接折：中文按字符折会把「血宗 6门」拆成
    「…血宗 6」/「门」，读起来像断字。这里以「、」「 · 」为界整条搬运。
    """
    items = [str(x).strip() for x in (items or []) if str(x).strip()]
    out: list[str] = []
    cur = ""
    for it in items:
        cand = it if not cur else cur + sep + it
        if _w(cand, FS_SUB, "regular") <= width:
            cur = cand
            continue
        if cur:
            out.append(cur)
        if _w(it, FS_SUB, "regular") > width:
            out.extend(Canvas.wrap(it, FS_SUB, "regular", width))
            cur = ""
        else:
            cur = it
    if cur:
        out.append(cur)
    return out


def _list_lines(items, sep: str, width: int) -> list[str]:
    """条目列表 → 折行后的行列表（空列表表示无内容，由调用方画占位）。"""
    return _pack(items, sep, width)


def _measure(data: dict, width: int) -> int:
    """卡片总高。步进与 build 完全一致（否则底部会裁/留白）。"""
    content_w = max(400, width - PAD * 2)
    wrap_w = content_w - 40
    y = PAD + H_TITLE + H_HEADER + H_STATS

    npc_rows = _npc_rows(data.get("npcs"), wrap_w)
    y += SECTION_H + max(1, len(npc_rows)) * NPC_PILL_H + \
        max(0, len(npc_rows) - 1) * NPC_GAP_Y + BLOCK_GAP
    flame_lines = _list_lines(data.get("flames"), "、", wrap_w)
    y += SECTION_H + max(1, len(flame_lines)) * LINE_H + BLOCK_GAP

    y += SECTION_H                      # 可转宗门 总标题
    groups = data.get("groups") or []
    if not groups:
        y += LINE_H + BLOCK_GAP
    else:
        for g in groups:
            entries = [_entry(n) for n in (g.get("names") or [])]
            y += _pool_block_h(len(_rows(entries))) + BLOCK_GAP

    y += H_TIP
    return max(420, y + UI.PAD_B)   # 底部比左右宽 18（见 doupo_ui.PAD_B）


def build(t: Theme, data: dict | None = None, width: int | None = None,
          source: str | None = None, **kw) -> Canvas:
    data = data or {}
    width = int(width or getattr(t, "width", 720) or 720)
    width = max(560, min(width, 900))
    p = _pal(t)
    content_w = width - PAD * 2
    wrap_w = content_w - 40
    h = _measure(data, width)

    #: 可选：收集文本包围盒（供 check_boxes 自检；默认 None 不记录开销）
    boxes = kw.pop("boxes", None)

    c = Canvas(width, h, p["page_color"], theme=t)

    def rec(label: str, txt: str, x: int, y: int, size: int, role: str,
            anchor: str) -> None:
        """记录文本包围盒（绘制与度量同 role，锚点换算与 Canvas 一致）。"""
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
        else:                                  # la
            bb = (x, y, x + w, y + hh)
        boxes.append((label,) + bb)

    def ktext(label: str, x: int, y: int, txt, size: int, color: str,
              anchor: str = "lm", max_w: int | None = None) -> None:
        """重点文字：>=22px 真粗体，小字降级为清晰常规字（doupo_style 规范）。"""
        bold = int(size) >= S.MIN_BOLD_FS
        role = "bold" if bold else "regular"
        t = _fit(str(txt), max_w, size, role) if max_w is not None else str(txt)
        c.text((x, y), t, size=int(size), color=color, role=role,
               bold=bold, anchor=anchor)
        rec(label, t, x, y, size, role, anchor)

    def stext(label: str, x: int, y: int, txt, size: int, color: str,
              anchor: str = "lm", max_w: int | None = None) -> None:
        """次要文字：明确不加粗，只靠字号与颜色分层。"""
        t = _fit(str(txt), max_w, size, "regular") if max_w is not None else str(txt)
        c.text((x, y), t, size=int(size), color=color, role="regular",
               bold=False, anchor=anchor)
        rec(label, t, x, y, size, "regular", anchor)

    x0 = PAD
    y = PAD

    # ---- 标题（青蓝云纹牌匾，内核统一）----
    title = str(data.get("title") or "宗门")
    y = UI.card_shell(c, p, title, width=width)
    rec("title", title, width // 2, y - 40, FS_TITLE, "kai", "mm")

    # ---- 宗门概览 ----
    name = str(data.get("name") or "无宗门")
    lv = int(data.get("lv") or 1)
    lv_text = "Lv.%d" % lv
    lv_w = _w(lv_text, FS_SUB, "bold")
    ktext("faction_name", x0, y + FS_NAME // 2, name, FS_NAME,
          p["title_color"], max_w=content_w - lv_w - 16)
    ktext("lv", x0 + content_w, y + FS_NAME // 2, lv_text, FS_SUB,
          p["chip_text"], anchor="rm")
    y += FS_NAME + 12

    # 两枚 pill：据点 / 卡池（宽度实测，从左往右排，显式 10px gap）
    pills = [("globe", "据点", str(data.get("base") or "")),
             ("tag", "卡池", str(data.get("pool") or ""))]
    px = x0
    for icon, label, val in pills:
        if not val:
            continue
        txt = "%s：%s" % (label, val)
        pw = ICON + 10 + _w(txt, FS_SUB, "bold") + 28
        pw = min(pw, x0 + content_w - px)          # 极端长名不越界
        c.rounded_rect((px, y, px + pw, y + PILL_H), radius=PILL_H // 2,
                       fill=p["pill_bg"], outline=p["pill_line"], width=1)
        icons.draw(c, icon, px + 16, y + PILL_H // 2, ICON,
                   p["pill_text"], style=ICON_STYLE)
        ktext("pill_" + label, px + 16 + ICON + 2, y + PILL_H // 2, txt, FS_SUB,
              p["pill_text"], max_w=max(20, pw - (ICON + 18) - 12))
        px += pw + 10
    y += PILL_H + 14

    # 开放卡池一行
    open_pools = data.get("open_pools") or []
    pools_txt = "境界 lv%d · 已开放卡池：%s" % (lv, " ".join(open_pools) or "（无）")
    stext("open_pools", x0, y + FS_LABEL // 2, pools_txt, FS_LABEL,
          p["dim_color"], max_w=content_w)
    y += FS_LABEL + 8

    # ---- 本门斗技统计 ----
    total = int(data.get("total_skills") or 0)
    y = _section(c, x0, y, content_w, p, "sword",
                 "本门斗技统计 · 共 %d 门" % total, boxes)
    tiers = ("普通", "高阶", "特殊")
    cell_gap = 12
    cell_w = (content_w - cell_gap * 2) // 3
    counts = data.get("tier_counts") or {}
    for i, tier in enumerate(tiers):
        cx = x0 + i * (cell_w + cell_gap)
        UI.panel(c, p, (cx, y, cx + cell_w, y + STAT_CELL_H), state="done",
                 radius=12, watermark=False)
        ktext("stat_%s" % tier, cx + cell_w // 2, y + 34,
              str(int(counts.get(tier, 0))), FS_STAT, p[TIER_COLORS[i]], anchor="mm")
        stext("stat_label_%s" % tier, cx + cell_w // 2, y + STAT_CELL_H - 20,
              tier + "斗技", FS_LABEL, p["dim_color"], anchor="mm")
    y += STAT_CELL_H + BLOCK_GAP

    # ---- 代表人物：每人一枚胶囊（姓名正文 + 身份小字），按宽度自动换行 ----
    y = _section(c, x0, y, content_w, p, "star", "代表人物", boxes)
    npc_rows = _npc_rows(data.get("npcs"), wrap_w)
    if not npc_rows:
        stext("npc_empty", x0 + 20, y + NPC_PILL_H // 2, "（未记载）",
              FS_SUB, p["dim_color"])
        y += NPC_PILL_H
    else:
        for ri, row in enumerate(npc_rows):
            cy = y + NPC_PILL_H // 2
            cx = x0
            for ci, (name, role, pw) in enumerate(row):
                # 胶囊底：chip 浅底，与门数徽章同色系；整枚宽度实测不越界
                c.rounded_rect((cx, y, cx + pw, y + NPC_PILL_H),
                               radius=NPC_PILL_H // 2, fill=p["chip_bg"],
                               outline=p["chip_line"], width=1)
                # 姓名：正文色；身份：灰小字，紧跟姓名后（gap 为像素常量，不用空格）
                nx = cx + NPC_PAD_X
                name_w = int(_w(name, FS_SUB, "regular"))
                stext("npc_%d_%d_name" % (ri, ci), nx, cy, name, FS_SUB,
                      p["text_color"])
                if role:
                    stext("npc_%d_%d_role" % (ri, ci), nx + name_w + NPC_IN_GAP,
                          cy, role, FS_LABEL, p["dim_color"])
                cx += pw + NPC_GAP
            y += NPC_PILL_H + NPC_GAP_Y
        y -= NPC_GAP_Y                      # 末行不留行距
    y += BLOCK_GAP

    # ---- 传承异火 ----
    y = _section(c, x0, y, content_w, p, "bolt", "传承异火", boxes)
    flame_lines = _list_lines(data.get("flames"), "、", wrap_w)
    for i, ln in enumerate(flame_lines or ["（未记载）"]):
        stext("flame_%d" % i, x0 + 20, y + LINE_H // 2, ln, FS_SUB, p["text_color"])
        y += LINE_H
    y += BLOCK_GAP

    # ---- 可转宗门（每个卡池一个规整块：块头 + 两列网格）----
    cost = int(data.get("switch_cost") or 0)
    y = _section(c, x0, y, content_w, p, "list", "可转宗门", boxes)
    groups = data.get("groups") or []
    if not groups:
        stext("no_group", x0 + 20, y + LINE_H // 2, "当前境界暂无可转宗门",
              FS_SUB, p["dim_color"])
        y += LINE_H + BLOCK_GAP
    else:
        col_w = _grid_col_w(content_w)
        badge_inner = BADGE_PAD * 2
        for gi, g in enumerate(groups):
            entries = [_entry(n) for n in (g.get("names") or [])]
            rows = _rows(entries)
            bh = _pool_block_h(len(rows))
            # 块底：浅底圆角，与统计格同色系，视觉上把该池条目圈起来
            UI.panel(c, p, (x0, y, x0 + content_w, y + bh), state="done",
                     radius=13)
            # 块头：池名（左，accent）+ 该池宗门数 / 转宗金币（右锚，灰）
            hy = y + POOL_PAD + POOL_HEAD_H // 2
            UI.kai_text(c, (x0 + POOL_PAD, hy), str(g.get("pool") or ""),
                        FS_KAI, p["gold_hi"], anchor="lm")
            if boxes is not None:
                pw_ = _w(str(g.get("pool") or ""), FS_KAI, "regular")
                boxes.append(("pool_%d" % gi, x0 + POOL_PAD, hy - FS_KAI // 2,
                              x0 + POOL_PAD + pw_, hy + FS_KAI // 2))
            head_right = "共 %d 宗门 · 需 %d 金币" % (len(entries), cost)
            stext("pool_head_right_%d" % gi, x0 + content_w - POOL_PAD, hy,
                  head_right, FS_LABEL, p["dim_color"], anchor="rm")
            # 网格：每行两列，宗名左对齐、门数徽章右锚到列右边界
            gy = y + POOL_PAD + POOL_HEAD_H
            for ri, row in enumerate(rows):
                for ci, (nm, cnt) in enumerate(row):
                    cell_left = x0 + POOL_PAD + ci * (col_w + GRID_GAP_X)
                    cell_right = cell_left + col_w
                    cy = gy + ri * GRID_CELL_H + GRID_CELL_H // 2
                    badge_w = badge_inner + _w(cnt, BADGE_FS, "regular") if cnt else 0
                    name_max = col_w - badge_w - (ENTRIES_GAP if badge_w else 0)
                    shown = _fit(nm, name_max, FS_SUB, "regular")
                    stext("sect_%d_%d_%d" % (gi, ri, ci), cell_left, cy, shown,
                          FS_SUB, p["text_color"], max_w=name_max)
                    if cnt:
                        bx1 = cell_right
                        c.rounded_rect((bx1 - badge_w, cy - BADGE_H // 2,
                                        bx1, cy + BADGE_H // 2),
                                       radius=BADGE_H // 2, fill="#1C1830",
                                       outline=p["gold_dk"], width=1)
                        stext("badge_%d_%d_%d" % (gi, ri, ci),
                              bx1 - BADGE_PAD, cy, cnt, BADGE_FS,
                              p["gold_hi"], anchor="rm")
            y += bh + BLOCK_GAP

    # ---- 底部提示 ----
    c.line((x0, y, x0 + content_w, y), fill=p["line_color"], width=1)
    y += 12
    tip = data.get("tip")
    if tip:
        stext("tip", x0, y + FS_TIP // 2, str(tip), FS_TIP,
              p["dim_color"], max_w=content_w)

    if source:
        stext("source", width - PAD, h - PAD + 6, str(source), 14,
              p["dim_color"], anchor="rb")
    return c


def _section(c: Canvas, x: int, y: int, w: int, p: dict, icon: str, label: str,
             boxes=None) -> int:
    """分区标题：monoline 图标 + 标签 + 右侧贯通细线。返回下一行 y。"""
    icons.draw(c, icon, x + ICON // 2, y + FS_SUB // 2, ICON,
               p["accent_color"], style=ICON_STYLE)
    t = _fit(label, w - ICON - 10, FS_SUB, "regular")
    c.text((x + ICON + 10, y + FS_SUB // 2), t, size=FS_SUB,
           color=p["text_color"], role="regular", bold=False, anchor="lm")
    if boxes is not None:
        lw = _w(t, FS_SUB, "regular")
        boxes.append(("section_" + label, x + ICON + 10, y + FS_SUB // 2 - FS_SUB // 2,
                      x + ICON + 10 + lw, y + FS_SUB // 2 + FS_SUB // 2))
    lx = x + ICON + 10 + _w(t, FS_SUB, "regular") + 12
    if lx < x + w:
        c.line((lx, y + FS_SUB // 2, x + w, y + FS_SUB // 2),
               fill=p["line_color"], width=1)
    return y + SECTION_H
