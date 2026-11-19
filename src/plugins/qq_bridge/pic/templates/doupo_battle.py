# -*- coding: utf-8 -*-
"""pic/templates/doupo_battle.py —— 斗破 · 完整战斗回看卡（结算界面版）。

2026-11-19 用户：「战斗回看功能，纯文字展示看着太复杂，不够明了，优化一下，
学习学习别人的权威的优秀的游戏是怎么做的」。

旧版是一张「日志打印稿」：20 条 log 摊成 40 行长文本，伤害、气血、词条全埋在句子
里，玩家读三屏也看不出「谁打得狠、关键在哪」。参照 MOBA / DNF / WoW 结算界面的
通行做法（简版观看率 80%、条形图 30%、时间轴 20%；「人对图像形变的感知强于数字
变化」；「做减法」），改成自上而下的五层：

    胜负徽标（暖色胜 / 冷色败） → 三格 KPI → 双方输出占比双条（长度按真实数值
    缩放，必不同长） → 关键节点时间轴（机缘/心火/踉跄/暴击/服药/被控/反伤）
    → 逐招明细（一条一行，左招式右数值，隔行浅底便于横扫）

``doupo_ruin``（傀儡遗迹）复用本骨架：它不传 verdict/kpis/bars/beats/actions，
``_rich()`` 返回 False → 走原来的「分组 + 折行」路径，像素与改造前一致。

card-design 合规：
  * 度量优先：折行/截断都用 Canvas.text_width 实测，绘制与度量同 role；
  * 列网格：所有区块共用 PAD / content_w，KPI 与条形图用显式 gap 均分；
  * 显式 gap：VERDICT_GAP / KPI_GAP / 区块间隙都是常量，测画共用；
  * 高度内容驱动：_layout 一次算好，测高与绘制共用同一份 blocks；
  * 字形安全：无 emoji，「→ ｜ · ×」均为既有模板已验证符号；
  * 数值不重算：所有数字来自 doupo.battle_review，本文件只做排版。
"""
from __future__ import annotations

from .. import icons
from ..canvas import Canvas
from ..theme import Theme

from .doupo import _card_alpha, _fit, _pal, _w
from . import doupo_style as S

PAD = 28
FS_TITLE = 30
FS_GROUP = S.FS_BODY      # 20 区块标题
FS_BODY = S.FS_SUB        # 17 正文
FS_NUM = S.MIN_BOLD_FS    # 22 逐招数值（>=22 才允许真粗体，见 doupo_style）
LINE_H = FS_BODY + 9      # 26 折行行高
ITEM_GAP = 6
GROUP_GAP = 16
BULLET_INDENT = 6
ICON_STYLE = "line"

# ---- 结算界面版式的尺寸（改这里必须同时改 _draw_*，两边共用同一批常量）----
H_VERDICT = 58            # 胜负徽标高度
VERDICT_GAP = 18
KPI_H = 72                # 三格 KPI 高度
KPI_GAP = 10              # 格与格的横向间距
KPI_AFTER = 14
BAR_ROW = 32              # 一条占比条 + 其标签的高度
BEAT_H = 30               # 一个关键节点的高度
ACT_H = 28                # 一条逐招明细的高度（旧平面列表兜底用）
# ---- 回合气泡（2026-11-19 用户：一回合一个气泡，左右列出敌我行为）----
BUBBLE_PAD = 12           # 气泡内边距
BUBBLE_GAP = 10           # 气泡之间的间距
BUBBLE_HEADER_H = FS_GROUP + 8    # 回合号行
BUBBLE_SIDE_H = S.FS_LABEL + 8    # 我方 / 敌方 列头行
BUBBLE_ROW_GAP = 8        # 同一列内相邻动作间距
BUBBLE_TAG_H = S.FS_LABEL + 6     # 词条说明行
BUBBLE_COL_GAP = 16       # 左右两列之间的间距（不是用空格凑）
CAP_BEFORE = 14           # 区块标题与其上方内容的间距（标题不能贴在上一块上）
CAP_AFTER = 8             # 区块标题与其下方内容的间距
CHIP_MAX_FS = S.FS_LABEL  # 16，关键节点的回合/类型 chip

_WHITE = "#FFFFFF"

_DEMO = {
    "title": "战斗回看",
    "verdict": {"win": True, "kind": "遭遇", "opponent": "魔炎谷库房",
                "rounds": 4, "exhausted": False},
    "kpis": [
        {"label": "我方输出", "value": "12,846", "tone": "mine"},
        {"label": "敌方输出", "value": "3,902", "tone": "foe"},
        {"label": "每回合", "value": "3,211", "tone": "plain"},
    ],
    "bars": [
        {"label": "我方", "value": 12846, "text": "12,846", "ratio": 1.0,
         "share": 77, "tone": "mine"},
        {"label": "敌方", "value": 3902, "text": "3,902", "ratio": 0.3037,
         "share": 23, "tone": "foe"},
    ],
    "beats": [
        {"round": 1, "tag": "暴击", "text": "狂狮怒罡 2,107", "tone": "mine"},
        {"round": 3, "tag": "心火", "text": "心火翻涌，攻势自涨", "tone": "mine"},
        {"round": 4, "tag": "服药", "text": "九转还魂丹 +520", "tone": "mine"},
    ],
    "actions": [
        {"idx": 1, "round": 1, "side": "我方", "move": "狂狮怒罡",
         "value": "2,107", "tone": "mine", "tags": "暴击·斗气-40"},
        {"idx": 2, "round": 1, "side": "敌方", "move": "兽击",
         "value": "318", "tone": "foe", "tags": "防御减伤"},
    ],
    "rounds": [
        {"round": 1, "mine": [{"move": "狂狮怒罡", "value": "2,107",
                               "tone": "mine", "tags": "暴击·斗气-40"}],
         "foe": [{"move": "兽击", "value": "318",
                  "tone": "foe", "tags": "防御减伤"}]},
    ],
    "groups": [
        {"name": "战况", "rows": ["战力 8,214｜境 lv37｜气血 412/1,000｜斗气 40/600",
                                 "代价 318｜修为 +320｜金币 +85"]},
        {"name": "逐招明细", "rows": [
            "01｜回合1 我方 狂狮怒罡（暴击·斗气-40） 2,107",
            "02｜回合1 敌方 兽击（防御减伤） 318",
        ]},
    ],
    "tip": "发「战斗评价」听一句毒舌 · 只保留最后一场战斗",
}


def _gi(v, default=0) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _gs(v, default="—") -> str:
    return default if v is None or v == "" else str(v)


def _rows_of(g: dict) -> list:
    return [r for r in (g.get("rows") or []) if r]


def _groups_of(data: dict) -> list:
    return [g for g in (data.get("groups") or []) if isinstance(g, dict)]


def _rich(data: dict) -> bool:
    """结算界面版式的判据：任一新字段存在就走新版，否则沿用旧分组骨架。

    ``doupo_ruin`` 只传 title/subtitle/groups/tip，因此永远走旧路径 —— 这是
    「同一份骨架给两张卡复用」的兼容边界，改动这里前先看 doupo_ruin.py。
    """
    return any(data.get(k) for k in
               ("verdict", "kpis", "bars", "beats", "actions", "rounds"))


def _wrap(s: str, max_w: int) -> list[str]:
    return S.wrap_cjk(str(s), FS_BODY, "regular", max_w)


# ---------------------------------------------------------------- 配色
def _tone_color(p: dict, tone: str) -> str:
    """阵营配色：我方暖（朱红）/ 敌方冷（斗气蓝）/ 丹药药青 / 无伤害事件灰。"""
    if tone == "mine":
        return p["title_color"]
    if tone == "foe":
        return p["mp_to"]
    if tone == "pill":
        return p["pill_text"]
    if tone == "dim":
        return p["dim_color"]
    return p["accent_color"]


def _tone_grad(p: dict, tone: str) -> tuple[str, str]:
    if tone == "foe":
        return p["mp_from"], p["mp_to"]
    return p["bar_from"], p["bar_to"]


# ---------------------------------------------------------------- 布局
def _bubble_plan(rounds: list[dict], content_w: int) -> list[dict]:
    """回合气泡的测画共用计划：先量测再定高，绘制阶段只读计划不再猜坐标。"""
    col_w = (content_w - 2 * BUBBLE_PAD - BUBBLE_COL_GAP) // 2
    plan: list[dict] = []
    for r in rounds:
        sides: dict[str, list[dict]] = {}
        for key in ("mine", "foe"):
            entries: list[dict] = []
            for a in (r.get(key) or []):
                if not isinstance(a, dict):
                    continue
                val = _gs(a.get("value"), "—")
                val_w = _w(val, FS_NUM, "bold")
                room = max(24, col_w - val_w - 8)
                mv = _fit(_gs(a.get("move"), "出手"), room, FS_BODY, "regular")
                tags = _fit(_gs(a.get("tags"), ""), col_w, S.FS_LABEL, "regular")
                rh = LINE_H + (BUBBLE_TAG_H if tags else 0) + BUBBLE_ROW_GAP
                entries.append({"move": mv, "value": val, "tags": tags,
                                "tone": _gs(a.get("tone"), "mine"), "h": rh})
            sides[key] = entries
        col_h = max(
            sum(e["h"] for e in sides["mine"]),
            sum(e["h"] for e in sides["foe"]),
            BUBBLE_ROW_GAP + 8,   # 空列也保留一行高度，气泡不会一边塌掉
        )
        plan.append({"round": _gi(r.get("round")), "sides": sides,
                     "col_w": col_w, "col_h": col_h,
                     "h": BUBBLE_PAD + BUBBLE_HEADER_H + BUBBLE_SIDE_H
                          + col_h + BUBBLE_PAD})
    return plan


def _cap(label: str, after: int = CAP_AFTER) -> tuple:
    """区块标题。高度含**前置留白**：标题不许贴在上一块内容上（实拍卡踩过）。"""
    return ("cap", str(label), CAP_BEFORE + FS_GROUP + after)


def _layout(data: dict, content_w: int) -> tuple[list[tuple], int]:
    if _rich(data):
        blocks = _rich_layout(data, content_w)
    else:
        blocks = _legacy_layout(data, content_w)
    return blocks, sum(bh for _, _, bh in blocks)


def _legacy_layout(data: dict, content_w: int) -> list[tuple]:
    """旧骨架（doupo_ruin 等复用方）：分组 + 折行，逐字节保持原样。"""
    text_w = content_w - BULLET_INDENT
    blocks: list[tuple] = []
    for g in _groups_of(data):
        blocks.append(("group", str(g.get("name") or ""), FS_GROUP + 12))
        for row in _rows_of(g):
            lines = _wrap(row, text_w)
            blocks.append(("item", lines, LINE_H * len(lines) + ITEM_GAP))
        blocks.append(("gap", None, max(0, GROUP_GAP - ITEM_GAP)))
    if blocks:
        blocks.pop()
    return blocks


def _rich_layout(data: dict, content_w: int) -> list[tuple]:
    blocks: list[tuple] = []
    v = data.get("verdict") or {}
    if v:
        blocks.append(("verdict", v, H_VERDICT + VERDICT_GAP))

    kpis = [k for k in (data.get("kpis") or []) if isinstance(k, dict)]
    if kpis:
        blocks.append(("kpi", kpis, KPI_H + KPI_AFTER))

    bars = [b for b in (data.get("bars") or []) if isinstance(b, dict)]
    if bars:
        blocks.append(_cap("输出对比"))
        blocks.append(("bars", bars, BAR_ROW * len(bars)))

    beats = [b for b in (data.get("beats") or []) if isinstance(b, dict)]
    if beats:
        blocks.append(_cap("关键节点"))
        blocks.append(("beats", beats, BEAT_H * len(beats)))

    groups = _groups_of(data)
    facts = _rows_of(groups[0]) if groups else []
    fact_name = _gs(groups[0].get("name") if groups else "战况", "战况")
    if facts:
        blocks.append(_cap(fact_name))
        blocks.append(("fact", facts, LINE_H * len(facts) + ITEM_GAP))

    acts = [a for a in (data.get("actions") or []) if isinstance(a, dict)]
    act_name = _gs(groups[-1].get("name") if groups else "逐招明细", "逐招明细")

    # 回合气泡优先；老数据（如缓存的旧卡参数）只有 actions 时退回平面列表。
    bubbles = [r for r in (data.get("rounds") or []) if isinstance(r, dict)]
    if bubbles:
        plan = _bubble_plan(bubbles, content_w)
        blocks.append(_cap(act_name))
        blocks.append(("rounds", plan,
                       sum(b["h"] for b in plan) + BUBBLE_GAP * (len(plan) - 1)))
    elif acts:
        blocks.append(_cap(act_name))
        blocks.append(("act", acts, ACT_H * len(acts)))
    elif groups:
        rows = _rows_of(groups[-1])
        if rows:
            blocks.append(_cap(act_name))
            text_w = content_w - BULLET_INDENT
            for row in rows:
                lines = _wrap(row, text_w)
                blocks.append(("item", lines, LINE_H * len(lines) + ITEM_GAP))
    return blocks


# ---------------------------------------------------------------- 各区块绘制
def _draw_verdict(c: Canvas, v: dict, x0: int, y: int, content_w: int,
                  p: dict, rec) -> None:
    win = bool(v.get("win"))
    cw, ch = 104, H_VERDICT
    c1, c2 = _tone_grad(p, "mine" if win else "foe")
    c.gradient_rect((x0, y, x0 + cw, y + ch), c1, c2, radius=14)
    label = "胜" if win else "败"
    c.text((x0 + cw // 2, y + ch // 2), label, size=FS_TITLE, color=_WHITE,
           role="bold", bold=True, anchor="mm")
    rec("verdict", label, x0 + cw // 2 - FS_TITLE // 2, y + ch // 2, FS_TITLE, role="bold")

    tx = x0 + cw + 18
    max_w = x0 + content_w - tx
    head = "%s · %d 回合" % (_gs(v.get("kind"), "战斗"), _gi(v.get("rounds")))
    head = _fit(head, max_w, S.FS_KEY, "bold")
    c.text((tx, y + 18), head, size=S.FS_KEY, color=p["text_color"],
           role="bold", bold=True, anchor="lm")
    rec("vhead", head, tx, y + 18, S.FS_KEY, role="bold")

    sub = "对手 " + _gs(v.get("opponent"), "未知对手")
    sub = _fit(sub, max_w, FS_BODY, "regular")
    c.text((tx, y + 44), sub, size=FS_BODY, color=p["dim_color"],
           role="regular", anchor="lm")
    rec("vsub", sub, tx, y + 44, FS_BODY)
    if v.get("exhausted"):
        # 300 回合熔断判胜必须显式标注，不能冒充正常取胜
        ex = "力竭判胜"
        ex_w = _w(ex, FS_BODY, "regular")
        sx = x0 + content_w - ex_w
        if sx > tx + _w(sub, FS_BODY, "regular") + 10:
            c.text((sx, y + 44), ex, size=FS_BODY, color=p["accent_color"],
                   role="regular", anchor="lm")
            rec("vex", ex, sx, y + 44, FS_BODY)


def _draw_kpi(c: Canvas, kpis: list, x0: int, y: int, content_w: int,
              p: dict, rec) -> None:
    n = len(kpis)
    gap = KPI_GAP
    cell_w = (content_w - gap * (n - 1)) // max(1, n)
    for i, k in enumerate(kpis):
        cx = x0 + i * (cell_w + gap)
        c.rounded_rect((cx, y, cx + cell_w, y + KPI_H - 4), radius=12,
                       fill=p["cell_bg"], outline=p["cell_line"], width=1)
        lab = _fit(_gs(k.get("label"), "—"), cell_w - 24, S.FS_LABEL, "regular")
        c.text((cx + 14, y + 22), lab, size=S.FS_LABEL, color=p["dim_color"],
               role="regular", anchor="lm")
        rec("k%d_l" % i, lab, cx + 14, y + 22, S.FS_LABEL)
        val = _fit(_gs(k.get("value"), "0"), cell_w - 24, FS_TITLE, "bold")
        c.text((cx + 14, y + 48), val, size=FS_TITLE,
               color=_tone_color(p, _gs(k.get("tone"), "plain")),
               role="bold", bold=True, anchor="lm")
        rec("k%d_v" % i, val, cx + 14, y + 48, FS_TITLE, role="bold")


def _draw_bars(c: Canvas, bars: list, x0: int, y: int, content_w: int,
               p: dict, rec) -> None:
    lab_w = 44
    val_w = 132
    bar_x0 = x0 + lab_w
    bar_x1 = x0 + content_w - val_w
    track_w = max(40, bar_x1 - bar_x0)
    for i, b in enumerate(bars):
        ry = y + i * BAR_ROW
        tone = _gs(b.get("tone"), "mine")
        lab = _fit(_gs(b.get("label"), "—"), lab_w - 8, FS_BODY, "regular")
        c.text((x0, ry + 16), lab, size=FS_BODY, color=p["dim_color"],
               role="regular", anchor="lm")
        rec("b%d_l" % i, lab, x0, ry + 16, FS_BODY)

        ratio = float(b.get("ratio") or 0.0)
        ratio = 0.0 if ratio < 0 else (1.0 if ratio > 1 else ratio)
        c.rounded_rect((bar_x0, ry + 8, bar_x0 + track_w, ry + 8 + 14), radius=7,
                       fill=p["track_color"])
        fw = int(track_w * ratio)
        if _gi(b.get("value")) > 0:
            fw = max(6, fw)   # 有输出就至少留 6px，别看起来像 0
        if fw > 0:
            c1, c2 = _tone_grad(p, tone)
            c.gradient_rect((bar_x0, ry + 8, bar_x0 + fw, ry + 8 + 14),
                            c1, c2, radius=7)
        rec("b%d_bar" % i, "", bar_x0, ry + 8, 14, anchor="la")

        val = "%s · %d%%" % (_gs(b.get("text"), _gs(b.get("value"), "0")),
                             _gi(b.get("share")))
        c.text((x0 + content_w, ry + 16), val, size=FS_BODY,
               color=_tone_color(p, tone), role="regular", anchor="rm")
        rec("b%d_v" % i, val, x0 + content_w - _w(val, FS_BODY), ry + 16, FS_BODY)


def _draw_beats(c: Canvas, beats: list, x0: int, y: int, content_w: int,
                p: dict, rec) -> None:
    rail_x = x0 + 5
    last = len(beats) - 1
    for i, b in enumerate(beats):
        ry = y + i * BEAT_H
        if i < last:
            c.line((rail_x, ry + 15, rail_x, ry + BEAT_H + 15),
                   fill=p["line_color"], width=1)
        tone = _gs(b.get("tone"), "mine")
        c.ellipse((rail_x - 3, ry + 12, rail_x + 3, ry + 18),
                  fill=_tone_color(p, tone))
        chip = "回%d %s" % (_gi(b.get("round")), _gs(b.get("tag"), "—"))
        chip_w = _w(chip, CHIP_MAX_FS, "regular") + 16
        cx = x0 + 18
        c.rounded_rect((cx, ry + 6, cx + chip_w, ry + 26), radius=8,
                       fill=p["chip_bg"], outline=p["chip_line"], width=1)
        c.text((cx + 8, ry + 16), chip, size=CHIP_MAX_FS, color=p["chip_text"],
               role="regular", anchor="lm")
        rec("e%d_c" % i, chip, cx + 8, ry + 16, CHIP_MAX_FS)

        tx = cx + chip_w + 12
        room = x0 + content_w - tx
        if room < 30:
            continue
        txt = _fit(_gs(b.get("text"), ""), room, FS_BODY, "regular")
        c.text((tx, ry + 16), txt, size=FS_BODY, color=p["text_color"],
               role="regular", anchor="lm")
        rec("e%d_t" % i, txt, tx, ry + 16, FS_BODY)


def _draw_fact(c: Canvas, rows: list, x0: int, y: int, content_w: int,
               p: dict, rec) -> None:
    text_w = content_w - BULLET_INDENT
    yy = y
    for ri, row in enumerate(rows):
        lines = _wrap(row, text_w)
        for li, line in enumerate(lines):
            cy = yy + li * LINE_H + LINE_H // 2
            c.text((x0 + BULLET_INDENT, cy), line, size=FS_BODY,
                   color=p["dim_color"], role="regular", anchor="lm")
            rec("f%d_%d" % (ri, li), line, x0 + BULLET_INDENT, cy, FS_BODY)
        yy += LINE_H * len(lines) + ITEM_GAP


def _draw_acts(c: Canvas, acts: list, x0: int, y: int, content_w: int,
               p: dict, rec) -> None:
    for i, a in enumerate(acts):
        ry = y + i * ACT_H
        if i % 2 == 1:
            # 隔行浅底：长列表靠底色横扫，比加分割线干净（DNF/WoW 战报同款）
            c.rounded_rect((x0, ry, x0 + content_w, ry + ACT_H - 4), radius=8,
                           fill=p["cell_bg"])
        left = "%02d｜回合%d %s %s" % (_gi(a.get("idx")), _gi(a.get("round")),
                                     _gs(a.get("side"), "我方"),
                                     _gs(a.get("move"), "出手"))
        left = _fit(left, int(content_w * 0.54), FS_BODY, "regular")
        c.text((x0 + 8, ry + 12), left, size=FS_BODY, color=p["text_color"],
               role="regular", anchor="lm")
        rec("a%d_l" % i, left, x0 + 8, ry + 12, FS_BODY)

        val = _gs(a.get("value"), "0")
        val_w = _w(val, FS_NUM, "bold")
        vx = x0 + content_w - 10
        c.text((vx, ry + 12), val, size=FS_NUM,
               color=_tone_color(p, _gs(a.get("tone"), "mine")),
               role="bold", bold=True, anchor="rm")
        rec("a%d_v" % i, val, vx - val_w, ry + 12, FS_NUM, role="bold")

        tags = _gs(a.get("tags"), "")
        if not tags:
            continue
        tx = x0 + 8 + _w(left, FS_BODY, "regular") + 12
        room = (vx - val_w - 12) - tx
        if room < 40:
            continue
        tags = _fit(tags, room, S.FS_LABEL, "regular")
        c.text((tx, ry + 13), tags, size=S.FS_LABEL, color=p["dim_color"],
               role="regular", anchor="lm")
        rec("a%d_t" % i, tags, tx, ry + 13, S.FS_LABEL)


def _draw_rounds(c: Canvas, plan: list[dict], x0: int, y: int,
                 content_w: int, p: dict, rec) -> None:
    """一回合一个气泡：顶部回合号，内部左右两列分别是「我方 / 敌方」的行为。"""
    for ri, b in enumerate(plan):
        by = y + sum(x["h"] for x in plan[:ri]) + BUBBLE_GAP * ri
        bh = b["h"]
        c.rounded_rect((x0, by, x0 + content_w, by + bh), radius=12,
                       fill=p["cell_bg"], outline=p["cell_line"], width=1)

        head_txt = "回合 %d" % b["round"]
        hx = x0 + BUBBLE_PAD
        hy = by + BUBBLE_PAD + FS_GROUP // 2
        c.text((hx, hy), head_txt, size=FS_GROUP, color=p["accent_color"],
               role="regular", anchor="lm")
        rec("r%d_h" % ri, head_txt, hx, hy, FS_GROUP)
        line_x = hx + _w(head_txt, FS_GROUP, "regular") + 10
        if line_x < x0 + content_w - BUBBLE_PAD - 8:
            c.line((line_x, hy, x0 + content_w - BUBBLE_PAD, hy),
                   fill=p["line_color"], width=1)

        col_w = b["col_w"]
        col_x = {
            "mine": x0 + BUBBLE_PAD,
            "foe": x0 + BUBBLE_PAD + col_w + BUBBLE_COL_GAP,
        }
        side_y = by + BUBBLE_PAD + BUBBLE_HEADER_H + S.FS_LABEL // 2
        for key, label, tone in (("mine", "我方", "mine"), ("foe", "敌方", "foe")):
            cx = col_x[key]
            c.text((cx, side_y), label, size=S.FS_LABEL,
                   color=_tone_color(p, tone), role="regular", anchor="lm")
            rec("r%d_%s_s" % (ri, key), label, cx, side_y, S.FS_LABEL)

        body_y = by + BUBBLE_PAD + BUBBLE_HEADER_H + BUBBLE_SIDE_H
        for key in ("mine", "foe"):
            cx = col_x[key]
            right = cx + col_w
            ey = body_y
            for ei, e in enumerate(b["sides"][key]):
                cy = ey + LINE_H // 2
                c.text((cx, cy), e["move"], size=FS_BODY, color=p["text_color"],
                       role="regular", anchor="lm")
                rec("r%d_%s%d_m" % (ri, key, ei), e["move"], cx, cy, FS_BODY)
                vw = _w(e["value"], FS_NUM, "bold")
                c.text((right, cy), e["value"], size=FS_NUM,
                       color=_tone_color(p, e["tone"]), role="bold", bold=True,
                       anchor="rm")
                rec("r%d_%s%d_v" % (ri, key, ei), e["value"], right - vw, cy,
                    FS_NUM, role="bold")
                if e["tags"]:
                    ty = ey + LINE_H + BUBBLE_TAG_H // 2
                    c.text((cx, ty), e["tags"], size=S.FS_LABEL,
                           color=p["dim_color"], role="regular", anchor="lm")
                    rec("r%d_%s%d_t" % (ri, key, ei), e["tags"], cx, ty, S.FS_LABEL)
                ey += e["h"]


def _sword_glyph(c: Canvas, cx: int, cy: int, color: str) -> None:
    icons.draw(c, "sword", cx, cy, 16, color, style=ICON_STYLE)


def build(t: Theme, data: dict | None = None, width: int | None = None,
          source: str | None = None, **kw) -> Canvas:
    data = data or {}
    if not _groups_of(data) and not _rich(data):
        data = _DEMO
    width = int(width or getattr(t, "width", 720) or 720)
    width = max(560, min(width, 900))
    p = _pal(t)
    content_w = width - PAD * 2
    blocks, blocks_h = _layout(data, content_w)
    boxes = kw.pop("boxes", None)

    # 副标题是可选增量（doupo_ruin 复用本骨架时显示「今日进度」）；
    # 不传 subtitle 时 sub_h=0。
    sub_h = FS_BODY + 8 if str(data.get("subtitle") or "") else 0
    h = PAD + FS_TITLE + 8 + 1 + 18 + sub_h + blocks_h + 12 + FS_BODY + PAD
    h = max(360, h)

    c = Canvas(width, h, p["page_color"], theme=t)
    c.rounded_rect((12, 12, width - 12, h - 12), radius=24,
                   fill=p["card_color"], outline=p["border_color"], width=1,
                   fill_alpha=_card_alpha(t) * 255 // 100)

    def rec(label, txt, x, y, size, anchor="lm", role="regular"):
        if boxes is None:
            return
        # 度量必须与绘制同 role：粗体数值用 regular 量会低估宽度，check_boxes 假通过
        w = _w(txt, size, role) if txt else 0
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

    # ---- 标题 ----
    title = str(data.get("title") or "战斗回看")
    tw = _w(title, FS_TITLE, "light")
    ico_w = 16 + 12
    tx = x0 + (content_w - tw - ico_w) // 2
    _sword_glyph(c, tx + 8, y + FS_TITLE // 2, p["title_color"])
    c.text((tx + ico_w, y + FS_TITLE // 2), title, size=FS_TITLE,
           color=p["title_color"], role="light", anchor="lm")
    rec("title", title, tx + ico_w, y + FS_TITLE // 2, FS_TITLE)
    y += FS_TITLE + 8
    c.line((x0, y, x0 + content_w, y), fill=p["line_color"], width=1)
    y += 18

    # ---- 可选副标题 ----
    if sub_h:
        s = _fit(str(data.get("subtitle") or ""), content_w, FS_BODY, "regular")
        c.text((x0, y + FS_BODY // 2), s, size=FS_BODY, color=p["dim_color"],
               role="regular", anchor="lm")
        rec("subtitle", s, x0, y + FS_BODY // 2, FS_BODY)
        y += sub_h

    # ---- 区块（旧骨架与结算界面共用同一个游标）----
    for bi, (kind, payload, bh) in enumerate(blocks):
        if kind == "verdict":
            _draw_verdict(c, payload, x0, y, content_w, p, rec)
        elif kind == "kpi":
            _draw_kpi(c, payload, x0, y, content_w, p, rec)
        elif kind == "bars":
            _draw_bars(c, payload, x0, y, content_w, p, rec)
        elif kind == "beats":
            _draw_beats(c, payload, x0, y, content_w, p, rec)
        elif kind == "fact":
            _draw_fact(c, payload, x0, y, content_w, p, rec)
        elif kind == "act":
            _draw_acts(c, payload, x0, y, content_w, p, rec)
        elif kind == "rounds":
            _draw_rounds(c, payload, x0, y, content_w, p, rec)
        elif kind == "cap":
            cy = y + CAP_BEFORE + FS_GROUP // 2
            c.rounded_rect((x0 + 1, cy - FS_GROUP // 2 + 3, x0 + 5,
                            cy - FS_GROUP // 2 + FS_GROUP - 3), radius=2,
                           fill=p["accent_color"])
            c.text((x0 + 14, cy), payload, size=FS_GROUP,
                   color=p["accent_color"], role="regular", anchor="lm")
            rec("g%d" % bi, payload, x0 + 14, cy, FS_GROUP)
        elif kind == "group":       # 旧骨架：分组标题（无底部间距差异）
            c.rounded_rect((x0 + 1, y + 3, x0 + 5, y + FS_GROUP - 3), radius=2,
                           fill=p["accent_color"])
            c.text((x0 + 14, y + FS_GROUP // 2), payload, size=FS_GROUP,
                   color=p["accent_color"], role="regular", anchor="lm")
            rec("g%d" % bi, payload, x0 + 14, y + FS_GROUP // 2, FS_GROUP)
        elif kind == "item":
            for li, line in enumerate(payload):
                cy = y + li * LINE_H + LINE_H // 2
                color = p["text_color"] if li == 0 else p["dim_color"]
                c.text((x0 + BULLET_INDENT, cy), line, size=FS_BODY,
                       color=color, role="regular", anchor="lm")
                rec("i%d_%d" % (bi, li), line, x0 + BULLET_INDENT, cy, FS_BODY)
        y += bh

    # ---- 底部提示 ----
    c.line((x0, y + 4, x0 + content_w, y + 4), fill=p["line_color"], width=1)
    y += 12
    tip = str(data.get("tip") or "发「战斗评价」听一句毒舌 · 只保留最后一场战斗")
    s = _fit(tip, content_w, FS_BODY, "regular")
    icons.draw(c, "bolt", x0 + 8, y + FS_BODY // 2, 14, p["accent_color"], style=ICON_STYLE)
    c.text((x0 + 26, y + FS_BODY // 2), s, size=FS_BODY, color=p["dim_color"],
           role="regular", anchor="lm")
    rec("tip", s, x0 + 26, y + FS_BODY // 2, FS_BODY)

    if source:
        c.text((width - PAD, h - PAD + 6), source, size=14,
               color=p["dim_color"], anchor="rb")
    return c
