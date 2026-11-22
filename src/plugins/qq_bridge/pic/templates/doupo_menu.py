# -*- coding: utf-8 -*-
"""pic/templates/doupo_menu.py —— 斗破苍穹 · 口令菜单卡。

用于群聊「斗破菜单 / dp菜单」的回复：把全部口令按玩法分组列成一张卡，
替代 20 多行纯文本（手机上要滑很久，且难扫读）。

版式（羊皮纸米 / 朱红 / 暗金玄幻观感，与 doupo.py 人物卡同一套调色）：
  * 顶部居中标题「斗破菜单」+ sword 细描边图标 + 贯通分割线；
  * 每个玩法分组：左侧 4px 暗金竖条 + 分组名（强调色），组间一条暗金分隔线；
  * 每行两列：左=口令（墨色真粗体，主信息）／右=一句话说明（灰，次信息）；
  * 行与行之间统一 8px gap + 淡色分隔线；底部提示。

设计约束：
  * 纯 Pillow 自绘，不依赖浏览器/渲染引擎（项目铁律）；
  * **不使用 emoji**（图片里 emoji 易缺字形变方框）；
  * 颜色复用 ``Theme.bg_overrides["doupo"]``（与人物卡同一卡片主题，编辑器改一次两卡同步）。

2026-11-20 两列并排重写（用户「菜单太长，能两排就两排」）：
  * 每个玩法分组内，口令两两配对成左右两个 cell（窄屏仍两列，cell 自适应半宽）；
  * cell 内口令在上（真粗体首词）、说明在下（小字折行），半宽下说明最多折 3 行；
  * 页面从单列 52 行压成两列 26 行，卡片高度约减半；
  * 口令显示用 card.menu_cmd_label 去掉具体示例参数（「收服 火云水炎」→「收服」），
    模板层只认传入的 data，命令系统真值 MENU_GROUPS 不动。
  * ``layout()`` 仍是唯一布局入口，``_row_parts()`` 保留供门禁校验折行保字。

2026-11-15 card-design 度量优先重写（用户「优化菜单，加粗字看着模糊」）：
  1. ``layout()`` 是**唯一布局入口**：build 与 ``layout_boxes()`` 自检共用同一份
     节点坐标，杜绝「高度按一种算法算、渲染按另一种画」的底部裁切。
  2. 口令列宽由**实测最长口令**推导（``Canvas.text_width(..., "bold")``，与绘制同字体），
     上限 48% 内容宽；旧版写死 236px 且 ``max_w=CMD_COL-26`` 会把
     「上阵 技1 技2 技3 技4」截断成「上阵 技1 技2 技3…」。现在口令按列宽折行，
     **一个字都不丢**（``"".join(cmd_lines) == cmd`` 由门禁锁住）。
  3. 列间距是显式常量 ``COL_GAP=24``，不是靠空格或经验 x 偏移凑出来的；
     说明列右边界 = content_right，越界由 check_boxes 拦。
  4. 口令与说明两个文本块共用同一竖直中轴居中，折行不再上下错位。
  5. 加粗字形统一走项目内置真粗体（见 fonts.py::_BUNDLED_BOLD），不再叠伪粗描边。

输入（build 的 params，均为 JSON 友好类型，便于编辑器预览）：
  title     标题，默认「斗破菜单」
  groups    [{"name": 分组名, "rows": [[口令, 说明], ...]}, ...]
  tip       底部提示（可留空）
"""
from __future__ import annotations

from .. import icons
from ..canvas import Canvas
from ..theme import Theme

# 与人物卡共用配色/度量工具（同包内复用，避免两份 _pal 各自漂移）
from .doupo import _fit, _pal, _w
from . import doupo_ui as UI
from . import doupo_style as S

# ---- 排版（4 的倍数 token；改这里必须同步跑 tools/check_doupo_typography.py）----
PAD = 36              # 内容左右内边距（2026-11-22 由 28 加到 36：游戏风回纹外框
                      # 画在 10、内线 17，PAD=28 时内容距框线只剩 11px，
                      # 用户明确反馈「字和边框黏在一起」，故统一到 36 → 距框线 26px）
FS_TITLE = 34
FS_GROUP = S.FS_KEY - 2        # 22 分组名（真粗体）
FS_CMD = S.FS_KEY              # 24 口令——整张卡最重要的可扫读信息
FS_DESC = S.FS_SUB             # 17 说明
FS_TIP = S.FS_LABEL            # 16 底部提示

CMD_INSET = 14                 # 口令文字左缩进（与分组竖条对齐）
COL_GAP = 24                   # 左右两列 cell 的固定列间距（>= 24px 字号的 10px 阈值）
CMD_MIN_COL = 208              # 【兼容保留】旧单列口令列下限；门禁不直接读，勿删防外部引用
CMD_MAX_RATIO = 0.48           # 【兼容保留】旧单列口令列上限
ROW_GAP = 8                    # 行与行统一间距
ROW_PAD = 12                   # 行内上下留白合计
ROW_MIN = 48
CMD_LH = FS_CMD + 6            # 30（1.25×）——口令折行行高
DESC_LH = FS_DESC + 6          # 23（1.35×）——说明折行行高
GROUP_GAP = 24                 # 分组之间统一间距
HEAD_GAP = 12                  # 分组名 → 首行
ICON_STYLE = "line"
CELL_PAD = 8                   # cell 内上下留白
CELL_CMD_W = 100               # 水平 cell 内口令列宽（精简后口令最长 4 字≈96px）
CELL_GAP = 8                   # 水平 cell 内 口令列 ↔ 说明列 的间距
CMD_DESC_GAP = 4               # 窄屏堆叠 cell 内 口令行 ↔ 说明块 的间距
STACK_CELL_W = 260             # cell 宽低于此值改用堆叠（口令上/说明下），避免说明列过窄折行爆炸

_DEFAULT_TIP = "与「修仙」重名的 商店/突破/任务/排行榜/炼丹 已统一让出，斗破一律 dp 前缀。"

#: 编辑器预览用的示例数据。业务调用总会传真实 groups；这里只保证
#: 主题编辑器点开该模板时不是一张空白卡（pic 包不 import 插件层，故内置示例）。
_DEMO = [
    {"name": "入门 · 建立角色",
     "rows": [["斗破菜单", "就是这条，随时回来看全部口令"],
              ["入道", "创建角色：斗之气一段，赠 10000 金币 + 玄重尺 + 回灵丹×5"]]},
    {"name": "修炼 · 涨修为升境界",
     "rows": [["修炼", "结算离线修为（每分钟自动 +6）"],
              ["突破", "修为满时晋升一星；102 级九星斗圣冲斗帝看机缘"]]},
]


def menu_cmd_label(cmd: str) -> str:
    """菜单卡口令标签：带具体示例参数的只留首词口令。

    ``收服 火云水炎`` → ``收服``、``买 血灵草 2`` → ``买``。示例参数只是
    用法演示，说明列已写清玩法；卡面逐条印全名既占版面又像「只能这样用」。
    只改**卡片显示**，命令系统真值 MENU_GROUPS 不动，口令仍可用。
    放在 pic 层（纯函数无依赖），供 card.build_menu_data 与门禁共用单一真值。
    """
    return str(cmd).strip().split(" ", 1)[0]


def _rows_of(g: dict) -> list:
    return [r for r in (g.get("rows") or []) if r]


def _groups(data: dict) -> list:
    groups = [g for g in (data.get("groups") or []) if isinstance(g, dict)]
    return groups or _DEMO


def _cmd_col(groups: list, content_w: int) -> int:
    """口令列宽 = 实测最长口令 + 左缩进 + 显式列间距，夹在 [下限, 48% 内容宽]。

    度量与绘制同 role（"bold" → 项目内置真粗体），所以换字体后列宽自动跟着变，
    不需要手调魔法数字，也不会再出现「测量按常规字、绘制按粗体字」的溢出。
    """
    max_w = 0
    for g in groups:
        for r in _rows_of(g):
            max_w = max(max_w, Canvas.text_width(str(r[0]), FS_CMD, "bold"))
    want = CMD_INSET + max_w + COL_GAP
    return max(CMD_MIN_COL, min(int(content_w * CMD_MAX_RATIO), want))


def _cell_parts(row, cell_w: int) -> dict:
    """单个 cell（半宽）内容，自适应两种模式（同源度量，build/门禁共用）：

    * 宽屏（cell_w >= STACK_CELL_W）：口令在左窄列、说明在右宽列，同一水平行；
      cell 高 = max(口令行, 说明折行)，最省高度。
    * 窄屏（cell_w < STACK_CELL_W）：口令上、说明下堆叠，说明用满整个 cell 宽，
      避免说明列过窄折行爆炸（窄屏水平会让卡高超过 4000px 门禁上限）。

    返回 mode/desc_x 供 layout 与 build 定位；口令/说明折行保字由门禁校验。
    """
    cmd = str(row[0]) if len(row) > 0 else ""
    desc = str(row[1]) if len(row) > 1 else ""
    if cell_w >= STACK_CELL_W:
        cmd_w = min(CELL_CMD_W, max(1, cell_w - CELL_GAP - CMD_INSET - 40))
        desc_x = CMD_INSET + cmd_w + CELL_GAP
        desc_w = max(1, cell_w - desc_x - CMD_INSET)
        cmd_lines = Canvas.wrap(cmd, FS_CMD, "bold", cmd_w) or [""]
        desc_lines = Canvas.wrap(desc, FS_DESC, "regular", desc_w) if desc else []
        block_h = max(len(cmd_lines) * CMD_LH, len(desc_lines) * DESC_LH)
        return {"mode": "h", "cmd": cmd_lines, "desc": desc_lines,
                "block_h": block_h, "desc_x": desc_x,
                "ch": max(ROW_MIN, block_h + CELL_PAD * 2)}
    # 窄屏堆叠
    text_w = max(1, cell_w - CMD_INSET - CMD_INSET)
    cmd_lines = Canvas.wrap(cmd, FS_CMD, "bold", text_w) or [""]
    desc_lines = Canvas.wrap(desc, FS_DESC, "regular", text_w) if desc else []
    block_h = len(cmd_lines) * CMD_LH
    if desc_lines:
        block_h += CMD_DESC_GAP + len(desc_lines) * DESC_LH
    return {"mode": "v", "cmd": cmd_lines, "desc": desc_lines,
            "block_h": block_h, "desc_x": CMD_INSET,
            "ch": max(ROW_MIN, block_h + CELL_PAD * 2)}


def _row_parts(row, content_w: int, cmd_col: int) -> dict:
    """【门禁兼容】按 cell 半宽校验折行保字。

    门禁用 ``_row_parts(row, L['content_w'], L['cmd_col'])`` 检查口令/说明
    折行后 ``''.join(lines) == 原文``（一个字都不丢）。两列布局下每个 cell
    宽 = L['content_w']，故这里以 content_w 作 cell 宽度量；cmd_col 参数保留
    兼容旧调用，语义为 cell 可用文字宽，缺省时按 content_w 推。
    """
    cell_w = max(1, int(cmd_col or content_w))
    return _cell_parts(row, cell_w)


def layout(data: dict | None = None, width: int | None = None) -> dict:
    """唯一布局入口：算出画布高度与全部绘制节点（含精确 y 坐标）。

    ``build()`` 只负责照节点画；``layout_boxes()`` 照同一批节点算真实包围盒。
    """
    data = data or {}
    width = max(560, min(int(width or 720), 900))
    groups = _groups(data)
    content_w = width - PAD * 2
    # 两列：每个 cell 占半宽，中间 COL_GAP 分隔。cmd_col 兼容字段 = cell 宽，
    # 供门禁 _row_parts(row, content_w, cmd_col) 按 cell 实宽度量折行保字。
    cell_w = (content_w - COL_GAP) // 2
    cmd_col = cell_w
    nodes: list[dict] = []

    y = PAD
    # 标题由 doupo_ui.card_shell 画成青蓝云纹牌匾，节点只占位（高度 = TITLE_H）
    nodes.append({"k": "title", "cy": y + UI.TITLE_H // 2})
    y += UI.TITLE_H
    nodes.append({"k": "line", "y": y, "strong": True})
    y += 1 + 6

    for gi, g in enumerate(groups):
        if gi:
            nodes.append({"k": "divider", "y": y - GROUP_GAP // 2, "strong": True})
        nodes.append({"k": "group", "name": str(g.get("name") or ""),
                      "cy": y + FS_GROUP // 2, "bar": (y + 3, y + FS_GROUP - 3)})
        y += FS_GROUP + HEAD_GAP
        rows = _rows_of(g)
        # 两两配对：奇数最后一个 cell 独占左列（右列留白）
        for pi in range(0, len(rows), 2):
            pair = rows[pi:pi + 2]
            parts = [_cell_parts(r, cell_w) for r in pair]
            row_h = max(p["ch"] for p in parts)
            for ci, part in enumerate(parts):
                cx = PAD + ci * (cell_w + COL_GAP)
                cmd_block_h = len(part["cmd"]) * CMD_LH
                desc_block_h = len(part["desc"]) * DESC_LH
                if part["mode"] == "h":
                    # 水平：口令与说明块各自在 cell 竖直中轴居中
                    cmd_top = y + CELL_PAD + max(0, (row_h - CELL_PAD * 2 - cmd_block_h) // 2)
                    desc_top = y + CELL_PAD + max(0, (row_h - CELL_PAD * 2 - desc_block_h) // 2)
                else:
                    # 窄屏堆叠：口令在上、说明在下
                    cmd_top = y + CELL_PAD
                    desc_top = cmd_top + cmd_block_h + CMD_DESC_GAP
                nodes.append({
                    "k": "row", "y": y, "rh": row_h, "cx": cx, "cw": cell_w,
                    "mode": part["mode"], "desc_x": part["desc_x"],
                    "cmd": [(ln, cmd_top + i * CMD_LH + CMD_LH // 2)
                            for i, ln in enumerate(part["cmd"])],
                    "desc": [(ln, desc_top + i * DESC_LH + DESC_LH // 2)
                             for i, ln in enumerate(part["desc"])],
                })
            y += row_h
            if pi + 2 < len(rows):
                nodes.append({"k": "divider", "y": y + ROW_GAP // 2, "strong": False})
                y += ROW_GAP
        y += GROUP_GAP

    y -= 4
    nodes.append({"k": "line", "y": y, "strong": True})
    y += 1 + 12
    tip = data.get("tip")
    if tip is None:
        tip = _DEFAULT_TIP
    nodes.append({"k": "tip", "cy": y + FS_TIP // 2, "text": str(tip or "")})
    y += FS_TIP + PAD

    return {"width": width, "h": max(360, y), "nodes": nodes,
            "content_w": content_w, "cmd_col": cmd_col,
            "title": str(data.get("title") or "斗破菜单")}


def layout_boxes(L: dict) -> list[tuple]:
    """把 layout() 的节点翻译成真实包围盒，供 check_boxes 自检（同源度量）。"""
    x0 = PAD
    cmd_col = L["cmd_col"]
    boxes: list[tuple] = []
    for i, n in enumerate(L["nodes"]):
        k = n["k"]
        if k == "title":
            tw = _w(L["title"], FS_TITLE, "light")
            ico_w = FS_TITLE + 10
            tx = x0 + (L["content_w"] - tw - ico_w) // 2
            boxes.append(("title", tx, n["cy"] - FS_TITLE // 2,
                          tx + ico_w + tw, n["cy"] + FS_TITLE // 2))
        elif k == "group":
            w = Canvas.text_width(n["name"], FS_GROUP, "bold")
            boxes.append(("group:%d:%s" % (i, n["name"]), x0 + CMD_INSET,
                          n["cy"] - FS_GROUP // 2, x0 + CMD_INSET + w,
                          n["cy"] + FS_GROUP // 2))
        elif k == "row":
            cx = n["cx"]
            for j, (ln, cy) in enumerate(n["cmd"]):
                w = Canvas.text_width(ln, FS_CMD, "bold")
                boxes.append(("cmd.%d.%d" % (i, j), cx + CMD_INSET,
                              cy - CMD_LH // 2, cx + CMD_INSET + w, cy + CMD_LH // 2))
            desc_x = n["desc_x"]
            for j, (ln, cy) in enumerate(n["desc"]):
                w = Canvas.text_width(ln, FS_DESC, "regular")
                boxes.append(("desc.%d.%d" % (i, j), cx + desc_x,
                              cy - DESC_LH // 2, cx + desc_x + w, cy + DESC_LH // 2))
        elif k == "tip" and n["text"]:
            txt = _fit(n["text"], L["content_w"], FS_TIP, "regular")
            w = Canvas.text_width(txt, FS_TIP, "regular")
            boxes.append(("tip", x0, n["cy"] - FS_TIP // 2,
                          x0 + w, n["cy"] + FS_TIP // 2))
    return boxes


def build(t: Theme, data: dict | None = None, width: int | None = None,
          source: str | None = None, **kw) -> Canvas:
    L = layout(data, width)
    w, h = L["width"], L["h"]
    p = _pal(t)
    x0 = PAD
    content_w, cmd_col = L["content_w"], L["cmd_col"]

    c = Canvas(w, h, p["page_color"], theme=t)
    UI.card_shell(c, p, L["title"], width=w)

    for n in L["nodes"]:
        k = n["k"]
        if k == "title":
            continue        # 牌匾由 UI.card_shell 统一绘制
        elif k == "line":
            UI.divider(c, p, n["y"], x0 + 6, x0 + content_w - 6, style="gold")
        elif k == "divider":
            # 分组分隔线贯通、行分隔线从口令缩进处起：层级一眼可分，又不喧宾夺主
            left = x0 if n["strong"] else x0 + CMD_INSET
            c.line((left, n["y"], x0 + content_w, n["y"]),
                   fill=p["accent_color"] if n["strong"] else p["line_color"], width=1)
        elif k == "group":
            c.rounded_rect((x0 + 1, n["bar"][0], x0 + 5, n["bar"][1]),
                           radius=2, fill=p["fret"])
            c.rounded_rect((x0 + 1, n["bar"][0], x0 + 2, n["bar"][1]),
                           radius=1, fill=p["fret_hi"])
            # 分组标题：玫红竖条 + 楷体金字（与 recipes/ruin/craft 的段标题同一套字）
            UI.kai_text(c, (x0 + CMD_INSET, n["cy"]), n["name"], FS_GROUP,
                        p["gold_hi"], anchor="lm")
        elif k == "row":
            cx = n["cx"]
            desc_x = n["desc_x"]
            for ln, cy in n["cmd"]:
                S.key(c, (cx + CMD_INSET, cy), ln, size=FS_CMD, color=p["text_color"])
            for ln, cy in n["desc"]:
                S.small(c, (cx + desc_x, cy), ln, size=FS_DESC, color=p["dim_color"])
        elif k == "tip":
            if n["text"]:
                S.small(c, (x0, n["cy"]), n["text"], size=FS_TIP,
                        color=p["dim_color"], max_w=content_w)

    if source:
        S.small(c, (w - PAD, h - PAD + 6), source, size=14,
                color=p["dim_color"], anchor="rb")

    return c
