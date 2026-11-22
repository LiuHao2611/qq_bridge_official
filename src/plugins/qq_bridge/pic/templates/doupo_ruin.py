# -*- coding: utf-8 -*-
"""pic/templates/doupo_ruin.py —— 斗破 · 傀儡遗迹日副本卡（2026-11-18 建 / 11-22 气泡版重做）。

2026-11-22 用户反馈：「卡片展示不够明了，把通过的副本用气泡包裹起来比较直观，
  自动挑战 1/7 展示不对应该 6/7，多一些字体或气泡元素区分，既详细又直观」。
据此从「复用 doupo_battle 的纯文本分组骨架」升级为**遗迹专用气泡版式**：

  * 顶部 7 段进度气泡条：已通关=暖色实心+勾，当前段=高亮描边，未解锁=灰；
  * 本次挑战气泡：累计进度（6/7，修掉旧「连过 1/7」）+ 逐段胜负 chip；
  * 每段一个包裹气泡：序号圆标 + 段名粗体 + 状态 chip + 描述 + 怪物/掉落，
    三种状态用色分层，一眼看出哪些过了、现在该打哪段。

card-design 合规（与 doupo_battle 同一套工具，不重写度量逻辑）：
  * 度量优先：折行/截断/气泡宽全部用 Canvas.text_width 实测，绘制与度量同 role；
  * 列网格 + 显式 gap：所有常量集中在上方，测画共用同一份 plan；
  * 高度内容驱动：_ruin_plan 一次算好每段块高，card_height/测高/绘制共用；
  * 字形安全：只用既有模板验证过的「→ ｜ · × ✓」符号，无 emoji 豆腐框；
  * 数值不重算：段名/掉落/进度全来自 doupo.card.build_ruin_data，本文件只排版。

兼容：无 ``stage_cards`` 字段（旧调用/测试构造的裸 data）时回退到 doupo_battle 的
legacy 分组骨架，像素与改造前一致——不破坏 test_battle_review 的复用断言。

输入由 ``doupo.card.build_ruin_data`` 生成：
  title / subtitle / progress:{done,total,day} / stage_cards:[{ord,name,desc,monster,
  drops:[[name,n]],status,status_text}] / report / groups:[{name,rows}] / tip
"""
from __future__ import annotations

from .. import icons
from ..canvas import Canvas
from ..theme import Theme

from . import doupo_battle as _battle
from .doupo import _card_alpha, _fit, _pal, _w
from . import doupo_style as S

PAD = 28
FS_TITLE = 30
FS_SEG = S.FS_KEY          # 24 段名（粗体）
FS_LABEL = S.FS_LABEL      # 16 chip / 小标签
FS_BODY = S.FS_SUB         # 17 描述 / 怪物掉落行
LINE_H = FS_BODY + 8       # 25
GROUP_GAP = 14             # 段气泡之间的间距

# 进度气泡条
PROG_BUBBLE_W = 30
PROG_BUBBLE_H = 34
PROG_GAP = 10
PROG_AFTER = 14
PROG_CHIP_H = 16

# 本次挑战块
RUN_PAD = 12
RUN_HEADER_H = S.FS_KEY + 8
RUN_ROW_H = S.FS_SUB + 7
RUN_CHIP_H = S.FS_LABEL + 8
RUN_AFTER = 16

# 段气泡
SEG_PAD = 12
SEG_ORD = 30              # 序号圆标直径
SEG_HEAD_H = FS_SEG + 12
SEG_META_H = LINE_H
SEG_STATUS_W = 76         # 状态 chip 宽度（"已通关"/"可挑战"/"未解锁" 3 字 + 余量）
SEG_INNER_GAP = 10

_WHITE = "#FFFFFF"

_DEMO = {
    "title": "傀儡遗迹",
    "subtitle": "今日进度 0/3 · 每日重置",
    "progress": {"done": 0, "total": 3, "day": ""},
    "stage_cards": [
        {"no": 1, "ord": "一", "name": "魔炎谷库房", "monster": "守库尸卫",
         "desc": "竹简原藏之处，残存尸卫守着未及带走的躯体与玄铁。",
         "drops": [["强者躯体", 1], ["暗金玄铁", 1]], "status": "current",
         "status_text": "可挑战"},
        {"no": 2, "ord": "二", "name": "天墓·能量之层", "monster": "远古凶魂",
         "desc": "能量之层游荡无主凶魂，须炼尽神智方可入料。",
         "drops": [["无主灵魂", 1], ["火属性魔核", 1]], "status": "locked",
         "status_text": "未解锁"},
        {"no": 3, "ord": "三", "name": "虚空雷池", "monster": "雷池守护者",
         "desc": "雷霆淬体之地，胜守者方得传承竹简。",
         "drops": [["雷淬石", 2], ["天妖傀竹简", 1]], "status": "locked",
         "status_text": "未解锁"},
    ],
    "groups": [
        {"name": "第一段 · 魔炎谷库房", "rows": [
            "原始出处：萧炎自魔炎谷抄家得《天妖傀》竹简",
            "守库尸卫 ｜ 掉 强者躯体×1、暗金玄铁×1 ｜ 待挑战"]},
    ],
    "tip": "发「傀儡遗迹」自动连打至力竭 · 发「傀儡」看名录 · 发「炼制傀儡」开炉",
}


def _rich(data: dict) -> bool:
    """新气泡版式的判据：有结构化 stage_cards 就走新版，否则回退 legacy。"""
    return bool(data.get("stage_cards"))


def _status_color(p: dict, status: str) -> str:
    """语义色统一（2026 前沿配色）：已通关=薄荷绿 / 可挑战=靛紫焦点 / 未解锁=灰。

    青色（mp_to）专供战斗斗气条，不在这里复用——否则紫+青+绿三套强调色同时
    出现显得杂。焦点一律靛紫，完成一律薄荷绿，弱信息灰紫。
    """
    if status == "done":
        return p["pill_text"]
    if status == "current":
        return p["title_color"]
    return p["dim_color"]


def _status_bg(p: dict, status: str) -> str:
    if status == "done":
        return p["pill_bg"]
    if status == "current":
        return p["chip_bg"]
    return p["track_color"]


def _drops_text(drops) -> str:
    out = []
    for d in (drops or []):
        if isinstance(d, dict):
            out.append("%s×%d" % (d.get("name", ""), int(d.get("n", 0) or 0)))
        elif isinstance(d, (list, tuple)) and len(d) >= 2:
            out.append("%s×%d" % (str(d[0]), int(d[1] or 0)))
    return "、".join(out) or "无"


# ---------------------------------------------------------------- 测画共用计划
def _ruin_plan(data: dict, content_w: int) -> dict:
    """一次算好所有块的尺寸/文本，测高与绘制共用，禁止绘制阶段再猜坐标。"""
    stages = [s for s in (data.get("stage_cards") or []) if isinstance(s, dict)]
    prog = data.get("progress") or {}
    done = max(0, int(prog.get("done", 0)))
    total = max(1, int(prog.get("total", len(stages))))
    report = data.get("report") or {}

    # 本次挑战逐段文本（新口径：今日累计 done/total + 本次连胜）。
    run_rows: list[tuple[str, str]] = []   # (text, tone)
    runs = list(report.get("runs") or [])
    if runs:
        wins = [r for r in runs if r.get("win")]
        loss = next((r for r in runs if not r.get("win")), None)
        run_rows.append(("今日累计 %d/%d 段 · 本次再连胜 %d 段" % (done, total, len(wins)),
                         "accent"))
        for r in runs:
            idx = int(r.get("stage_idx") or 0) + 1
            name = str(r.get("stage_name") or "傀儡遗迹")
            win = bool(r.get("win"))
            txt = "%d · %s ｜ %s ｜ %d 回合" % (
                idx, name, "胜" if win else "力竭", int(r.get("rounds") or 0))
            if not win:
                txt += " · 敌余血 %s" % _fmt_hp(r.get("mon_hp_left"))
            run_rows.append((txt, "done" if win else "foe"))
        agg: dict[str, int] = {}
        for r in runs:
            for k, v in (r.get("drops") or {}).items():
                agg[str(k)] = agg.get(str(k), 0) + int(v)
        if agg:
            run_rows.append(("累计掉落 " + "、".join("%s×%d" % (k, v) for k, v in agg.items()),
                             "dim"))
    else:
        run_rows.append(("今日累计 %d/%d 段" % (done, total), "accent"))

    # 每个段气泡的折行与高度。
    seg_plans = []
    for s in stages:
        desc_lines = S.wrap_cjk(str(s.get("desc") or ""), FS_BODY,
                                 "regular", content_w - 2 * SEG_PAD)
        meta = "%s ｜ 掉 %s" % (s.get("monster") or "守关者", _drops_text(s.get("drops")))
        meta_fit = _fit(meta, content_w - 2 * SEG_PAD, FS_BODY, "regular")
        h = (SEG_PAD + SEG_HEAD_H + LINE_H * max(1, len(desc_lines))
             + SEG_META_H + SEG_PAD)
        seg_plans.append({"stage": s, "desc_lines": desc_lines, "meta": meta_fit, "h": h})

    run_h = (RUN_PAD + RUN_HEADER_H + RUN_ROW_H * len(run_rows) + RUN_PAD)
    return {"stages": seg_plans, "prog": {"done": done, "total": total,
                                          "day": str(prog.get("day") or "")},
            "run_rows": run_rows, "run_h": run_h}


def _fmt_hp(v) -> str:
    try:
        return "{:,}".format(int(v or 0))
    except (TypeError, ValueError):
        return "0"


# ---------------------------------------------------------------- 绘制
def _draw_prog(c: Canvas, plan: dict, x0: int, y: int, content_w: int, p: dict, rec) -> None:
    """顶部 7 段进度气泡条 + 今日进度文案。"""
    done, total = plan["done"], plan["total"]
    # 文案与气泡条同一行：左侧文案，气泡条右对齐（显式列，不用空格凑）。
    label = "今日进度 %d/%d" % (done, total)
    if plan.get("day"):
        label += " · " + plan["day"]
    lw = _w(label, FS_BODY, "regular")
    c.text((x0, y + PROG_BUBBLE_H // 2), label, size=FS_BODY,
           color=p["dim_color"], role="regular", anchor="lm")
    rec("prog_label", label, x0, y + PROG_BUBBLE_H // 2, FS_BODY)

    bar_w = total * PROG_BUBBLE_W + (total - 1) * PROG_GAP
    bx = x0 + content_w - bar_w
    for i in range(total):
        bxx = bx + i * (PROG_BUBBLE_W + PROG_GAP)
        if i < done:
            # 已通关=薄荷绿渐变（完成态 Neo Mint），与段气泡/状态 chip 同语义。
            c.gradient_rect((bxx, y, bxx + PROG_BUBBLE_W, y + PROG_BUBBLE_H),
                            p["pill_line"], p["pill_text"], radius=10)
            # 勾（对勾图标，白色，实心）压在气泡中心。
            icons.draw(c, "check", bxx + PROG_BUBBLE_W // 2,
                       y + PROG_BUBBLE_H // 2, 14, _WHITE, style="solid")
        elif i == done:
            # 当前可挑战=靛紫焦点色（Digital Lavender），青色留给战斗斗气条。
            c.rounded_rect((bxx, y, bxx + PROG_BUBBLE_W, y + PROG_BUBBLE_H), radius=10,
                           fill=p["chip_bg"], outline=p["title_color"], width=2)
            c.text((bxx + PROG_BUBBLE_W // 2, y + PROG_BUBBLE_H // 2), str(i + 1),
                   size=FS_LABEL, color=p["title_color"], role="regular", anchor="mm")
        else:
            c.rounded_rect((bxx, y, bxx + PROG_BUBBLE_W, y + PROG_BUBBLE_H), radius=10,
                           fill=p["track_color"], outline=p["border_color"], width=1)
            c.text((bxx + PROG_BUBBLE_W // 2, y + PROG_BUBBLE_H // 2), str(i + 1),
                   size=FS_LABEL, color=p["dim_color"], role="regular", anchor="mm")
        rec("prog_%d" % i, str(i + 1), bxx, y, PROG_BUBBLE_H)


def _draw_run(c: Canvas, plan: dict, x0: int, y: int, content_w: int, p: dict, rec) -> None:
    """本次挑战气泡：整块包裹，逐段 chip 分色。"""
    h = plan["run_h"]
    c.rounded_rect((x0, y, x0 + content_w, y + h - 4), radius=12,
                   fill=p["cell_bg"], outline=p["cell_line"], width=1)
    ty = y + RUN_PAD
    c.text((x0 + RUN_PAD, ty + RUN_HEADER_H // 2), "本次挑战", size=S.FS_KEY,
           color=p["title_color"], role="bold", bold=True, anchor="lm")
    rec("run_head", "本次挑战", x0 + RUN_PAD, ty + RUN_HEADER_H // 2, S.FS_KEY, role="bold")
    ty += RUN_HEADER_H
    tone_color = {"accent": p["title_color"], "done": p["pill_text"],
                  "foe": p["hp_to"], "dim": p["dim_color"]}
    for i, (txt, tone) in enumerate(plan["run_rows"]):
        c.text((x0 + RUN_PAD + BULLET(), ty + RUN_ROW_H // 2), txt, size=FS_BODY,
               color=tone_color.get(tone, p["text_color"]), role="regular", anchor="lm")
        rec("run_%d" % i, txt, x0 + RUN_PAD + BULLET(), ty + RUN_ROW_H // 2, FS_BODY)
        ty += RUN_ROW_H


def BULLET() -> int:
    return 4


def _draw_seg(c: Canvas, sp: dict, x0: int, y: int, content_w: int, p: dict, rec,
              idx: int) -> None:
    """一段 = 一个包裹气泡：暖色（已通关）/ 高亮（可挑战）/ 灰（未解锁）。"""
    s = sp["stage"]
    status = str(s.get("status") or "locked")
    h = sp["h"]
    # 气泡底色：已通关=薄荷绿淡底，可挑战=靛紫高亮描边，未解锁=中性底。
    if status == "done":
        fill, outline, ow = p["pill_bg"], p["pill_line"], 1
    elif status == "current":
        fill, outline, ow = p["chip_bg"], p["title_color"], 2
    else:
        fill, outline, ow = p["cell_bg"], p["border_color"], 1
    c.rounded_rect((x0, y, x0 + content_w, y + h), radius=12,
                   fill=fill, outline=outline, width=ow)

    # 序号圆标。
    oc = _status_color(p, status)
    ocx = x0 + SEG_PAD + SEG_ORD // 2
    ocy = y + SEG_PAD + SEG_HEAD_H // 2
    c.ellipse((ocx - SEG_ORD // 2, ocy - SEG_ORD // 2,
               ocx + SEG_ORD // 2, ocy + SEG_ORD // 2), fill=oc)
    no = s.get("no") or (idx + 1)
    c.text((ocx, ocy), str(no), size=S.FS_KEY, color=_WHITE,
           role="bold", bold=True, anchor="mm")
    rec("seg%d_ord" % idx, str(no), ocx - SEG_ORD // 2, ocy - SEG_ORD // 2, SEG_ORD)

    # 段名（粗体，分层）+ 状态 chip（右端）。
    name_x = x0 + SEG_PAD + SEG_ORD + SEG_INNER_GAP
    name_max = content_w - (name_x - x0) - SEG_PAD - SEG_STATUS_W - SEG_INNER_GAP
    name = _fit(str(s.get("name") or ""), name_max, FS_SEG, "bold")
    c.text((name_x, ocy), name, size=FS_SEG, color=p["text_color"],
           role="bold", bold=True, anchor="lm")
    rec("seg%d_name" % idx, name, name_x, ocy - FS_SEG // 2, FS_SEG, role="bold")

    st_txt = str(s.get("status_text") or "")
    chip_x = x0 + content_w - SEG_PAD - SEG_STATUS_W
    chip_y = ocy - PROG_CHIP_H // 2
    c.rounded_rect((chip_x, chip_y, chip_x + SEG_STATUS_W, chip_y + PROG_CHIP_H),
                   radius=8, fill=_status_bg(p, status), outline=oc, width=1)
    # 已通关加勾图标；其余写状态文字。
    if status == "done":
        icons.draw(c, "check", chip_x + 11, chip_y + PROG_CHIP_H // 2, 12, oc, style="solid")
        c.text((chip_x + 22, chip_y + PROG_CHIP_H // 2), st_txt, size=FS_LABEL,
               color=oc, role="regular", anchor="lm")
        rec("seg%d_status" % idx, st_txt, chip_x + 22, chip_y + PROG_CHIP_H // 2, FS_LABEL)
    else:
        c.text((chip_x + SEG_STATUS_W // 2, chip_y + PROG_CHIP_H // 2), st_txt,
               size=FS_LABEL, color=oc, role="regular", anchor="mm")
        rec("seg%d_status" % idx, st_txt, chip_x, chip_y + PROG_CHIP_H // 2, FS_LABEL)

    # 描述行（小字，逐行实测折行）。
    dy = y + SEG_PAD + SEG_HEAD_H
    for li, line in enumerate(sp["desc_lines"]):
        c.text((x0 + SEG_PAD, dy + LINE_H // 2), line, size=FS_BODY,
               color=p["text_color"], role="regular", anchor="lm")
        rec("seg%d_desc%d" % (idx, li), line, x0 + SEG_PAD, dy + LINE_H // 2, FS_BODY)
        dy += LINE_H
    # 怪物 / 掉落行。
    c.text((x0 + SEG_PAD, dy + SEG_META_H // 2), sp["meta"], size=FS_BODY,
           color=p["dim_color"], role="regular", anchor="lm")
    rec("seg%d_meta" % idx, sp["meta"], x0 + SEG_PAD, dy + SEG_META_H // 2, FS_BODY)


def build(t: Theme, data: dict | None = None, width: int | None = None,
          source: str | None = None, **kw):
    data = data or {}
    if not _rich(data):
        # 兼容：无 stage_cards 时回退到原 doupo_battle 分组骨架（逐字节不变）。
        return _battle.build(t, data, width, source=source, **kw)

    width = int(width or getattr(t, "width", 720) or 720)
    width = max(560, min(width, 900))
    p = _pal(t)
    content_w = width - PAD * 2
    plan = _ruin_plan(data, content_w)
    boxes = kw.pop("boxes", None)

    sub_h = FS_BODY + 8 if str(data.get("subtitle") or "") else 0
    blocks_h = (PROG_BUBBLE_H + PROG_AFTER + plan["run_h"] + RUN_AFTER
                + sum(sp["h"] for sp in plan["stages"])
                + GROUP_GAP * max(0, len(plan["stages"]) - 1))
    h = PAD + FS_TITLE + 8 + 1 + 18 + sub_h + blocks_h + 12 + FS_BODY + PAD
    h = max(360, h)

    c = Canvas(width, h, p["page_color"], theme=t)
    c.rounded_rect((12, 12, width - 12, h - 12), radius=24,
                   fill=p["card_color"], outline=p["border_color"], width=1,
                   fill_alpha=_card_alpha(t) * 255 // 100)

    def rec(label, txt, x, y, size, anchor="lm", role="regular"):
        if boxes is None:
            return
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
    # 标题
    title = str(data.get("title") or "傀儡遗迹")
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
    # 副标题（进度条已含进度文案，这里保留兼容 subtitle 但画淡色小字于气泡条上方留白；
    # 实际上不重复——进度气泡条自带「今日进度 X/Y」，subtitle 仅在无 progress.day 时补日期）
    if sub_h:
        # 不重复「今日进度 X/Y」：subtitle 由 _draw_prog 内的 label 覆盖，此处跳过。
        y += 0

    # 进度气泡条
    _draw_prog(c, plan["prog"], x0, y, content_w, p, rec)
    y += PROG_BUBBLE_H + PROG_AFTER
    # 本次挑战
    _draw_run(c, plan, x0, y, content_w, p, rec)
    y += plan["run_h"] + RUN_AFTER
    # 段气泡
    for i, sp in enumerate(plan["stages"]):
        _draw_seg(c, sp, x0, y, content_w, p, rec, i)
        y += sp["h"] + GROUP_GAP
    if plan["stages"]:
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
    if not _rich(data):
        return _battle.card_height(data, content_w)
    plan = _ruin_plan(data, content_w)
    blocks_h = (PROG_BUBBLE_H + PROG_AFTER + plan["run_h"] + RUN_AFTER
                + sum(sp["h"] for sp in plan["stages"])
                + GROUP_GAP * max(0, len(plan["stages"]) - 1))
    sub_h = FS_BODY + 8 if str(data.get("subtitle") or "") else 0
    return PAD + FS_TITLE + 8 + 1 + 18 + sub_h + blocks_h + 12 + FS_BODY + PAD


def split_pages(data: dict, width: int | None = None, **kw) -> list[dict]:
    """遗迹卡单页即可（7 段气泡高度在预算内），兼容接口直接返回单页。"""
    return [data]
