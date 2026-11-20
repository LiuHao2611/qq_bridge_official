# -*- coding: utf-8 -*-
"""pic/templates/doupo_battle.py —— 斗破 · 完整战斗回看卡（结算界面版）。

2026-11-19 用户：「战斗回看功能，纯文字展示看着太复杂，不够明了，优化一下，
学习学习别人的权威的优秀的游戏是怎么做的」。

旧版是一张「日志打印稿」：20 条 log 摊成 40 行长文本，伤害、气血、词条全埋在句子
里，玩家读三屏也看不出「谁打得狠、关键在哪」。参照 MOBA / DNF / WoW 结算界面的
通行做法（简版观看率 80%、条形图 30%、时间轴 20%；「人对图像形变的感知强于数字
变化」；「做减法」），改成自上而下的五层：

    胜负徽标（暖色胜 / 冷色败） → 终局资源条（我气血/我斗气/敌气血 + 收场原因）
    → 三格 KPI → 双方输出占比双条（长度按真实数值缩放，必不同长） → 关键节点
    时间轴（机缘/心火/踉跄/暴击/服药/被控/反伤） → 回合气泡（一回合一个，左右
    两列敌我行为，逐条带伤害构成与回合末余量）

2026-11-19 补料（用户：「每回合我受伤多少、对方受伤多少、多少被稀释都看不到，
还有血量怎么就判失败」）：气泡抬头右侧写「我受 X · 敌受 Y」，每条动作下面加
一行「面板 X · 防御 -Y（ · 技能 -Z）」，气泡底部写「余血/余气 · 敌血」；
顶部新增终局资源条，用真实结束值画条 —— 血量归零就是空条，不再让战后回血
冒充终局（战败会把气血重置成上限×0.7，那是回血不是剩余）。

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
BUBBLE_COMP_H = S.FS_LABEL + 6    # 伤害构成行（面板 → 防御 → 实收）
BUBBLE_FOOT_H = S.FS_LABEL + 10   # 气泡底部「余血 / 余气 / 敌血」行
BUBBLE_COL_GAP = 16       # 左右两列之间的间距（不是用空格凑）
CAP_BEFORE = 14           # 区块标题与其上方内容的间距（标题不能贴在上一块上）
CAP_AFTER = 8             # 区块标题与其下方内容的间距
CHIP_MAX_FS = S.FS_LABEL  # 16，关键节点的回合/类型 chip
# ---- 终局资源条（2026-11-19：气血归零才是败因，别让战后回血冒充剩余）----
END_ROW = 26              # 一行资源条（我气血 / 我斗气 / 敌气血）
END_LABEL_W = 78          # 「我气血」标签列宽（FS_BODY 下 3 个汉字 ≈60px，58 会截成「我气…」）
END_VAL_W = 150           # 右侧「8,412 / 10,883」数值列宽
END_AFTER = 8             # 与下方 KPI 的间距
# ---- 原著 BOSS 登场引言（2026-11-20）：胜负徽标下方一条强调引文 ----
INTRO_ROW = LINE_H        # 引言正文行高（折行按实测行数累加）
INTRO_AFTER = 12          # 与下方"终局"块的间距（独立于 CAP_BEFORE，避免双重间距）

_WHITE = "#FFFFFF"

_DEMO = {
    "title": "战斗回看",
    "verdict": {"win": False, "kind": "遭遇", "opponent": "血战佣兵、狮翼兽",
                "rounds": 4, "exhausted": False, "end_text": "气血耗尽"},
    "end": {"text": "气血耗尽", "reason": "hp_zero", "win": False,
            "rows": [
                {"label": "我气血", "value": 0, "max": 10883, "tone": "mine"},
                {"label": "我斗气", "value": 84, "max": 5466, "tone": "mp"},
                {"label": "敌气血", "value": 7120, "max": 12000, "tone": "foe"},
            ]},
    "kpis": [
        {"label": "我方输出", "value": "12,846", "tone": "mine"},
        {"label": "敌方输出", "value": "15,902", "tone": "foe"},
        {"label": "每回合", "value": "3,211", "tone": "plain"},
    ],
    "bars": [
        {"label": "我方", "value": 12846, "text": "12,846", "ratio": 1.0,
         "share": 45, "tone": "mine"},
        {"label": "敌方", "value": 15902, "text": "15,902", "ratio": 1.0,
         "share": 55, "tone": "foe"},
    ],
    "beats": [
        {"round": 1, "tag": "暴击", "text": "狂狮怒罡 2,107", "tone": "mine"},
        {"round": 3, "tag": "心火", "text": "心火翻涌，攻势自涨", "tone": "mine"},
        {"round": 4, "tag": "服药", "text": "九转还魂丹 +520", "tone": "mine"},
    ],
    "actions": [
        {"idx": 1, "round": 1, "side": "我方", "move": "狂狮怒罡",
         "value": "2,107", "tone": "mine", "tags": "暴击·斗气-40",
         "comp": "面板 2,860 · 防御 -680"},
        {"idx": 2, "round": 1, "side": "敌方", "move": "兽击",
         "value": "318", "tone": "foe", "tags": "防御减伤",
         "comp": "面板 520 · 防御 -140 · 技能 -62"},
    ],
    "rounds": [
        {"round": 1,
         "mine": [{"move": "狂狮怒罡", "value": "2,107", "tone": "mine",
                   "tags": "暴击·斗气-40", "comp": "面板 2,860 · 防御 -680"}],
         "foe": [{"move": "兽击", "value": "3,418", "tone": "foe",
                  "tags": "防御减伤", "comp": "面板 5,200 · 防御 -1,140 · 技能 -642"}],
         "sum_text": "我受 3,418 · 敌受 2,107",
         "foot_l": "余血 8,412/10,883 · 余气 2,340/5,466", "foot_r": "敌血 7,120"},
    ],
    "groups": [
        {"name": "战况", "rows": ["战力 8,214｜境 lv37",
                                 "代价 15,902｜修为 +0｜金币 +0"]},
        {"name": "逐招明细", "rows": [
            "01｜回合1 我方 狂狮怒罡（暴击·斗气-40） 2,107",
            "02｜回合1 敌方 兽击（防御减伤） 3,418",
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


def _mn(v) -> str:
    """千分位。数字本身来自 battle_review，模板只负责印得看得懂。"""
    return f"{_gi(v):,}"


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
               ("verdict", "end", "kpis", "bars", "beats", "actions", "rounds", "intro"))


def _wrap(s: str, max_w: int) -> list[str]:
    return S.wrap_cjk(str(s), FS_BODY, "regular", max_w)


def _wrap_no_orphan(s: str, max_w: int, min_last: int = 6) -> list[str]:
    """折行后消「孤字 / 寡行」：末行不足 ``min_last`` 字时，从上一行末尾搬字过来。

    登场引言是两段长文，末行掉一个「来。」这种单字行非常刺眼，属于
    card-design 明确禁止的排版瑕疵。先试整段合并（上一行 + 末行不超宽），
    合并不下就做**均衡搬运**（把上一行末尾几个字挪到末行开头），
    搬运时跳过行首标点，避免行首出现「。」。
    只作用于引言块，不动全站折行（不改其他卡尺寸）。
    """
    lines = _wrap(s, max_w)
    if len(lines) < 2 or len(lines[-1]) >= min_last:
        return lines
    merged = lines[-2] + lines[-1]
    if Canvas.text_width(merged, FS_BODY, "regular") <= max_w:
        return lines[:-2] + [merged]
    prev, last = lines[-2], lines[-1]
    need = min_last - len(last)
    move = prev[-need:] if need <= len(prev) else prev
    while move and move[0] in getattr(S, "HEAD_PUNCT", ()):
        move = move[1:]
    if move and len(prev) > len(move):
        lines[-2] = prev[:-len(move)]
        lines[-1] = move + last
    return lines


# ---------------------------------------------------------------- 配色
def _tone_color(p: dict, tone: str) -> str:
    """阵营配色：我方暖（朱红）/ 敌方冷（斗气蓝）/ 丹药药青 / 无伤害事件灰。"""
    if tone == "mine":
        return p["title_color"]
    if tone in ("foe", "mp"):   # 斗气条与敌方同属冷色系（蓝），语义一致
        return p["mp_to"]
    if tone == "pill":
        return p["pill_text"]
    if tone == "dim":
        return p["dim_color"]
    return p["accent_color"]


def _resource_grad(p: dict, tone: str) -> tuple[str, str]:
    """资源条渐变：气血红、斗气蓝。敌我气血同色，靠左侧标签区分（不靠颜色猜）。"""
    if tone == "mp":
        return p["mp_from"], p["mp_to"]
    return p["hp_from"], p["hp_to"]


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
                # 2026-11-20：BOSS 的技能/原著台词/机制是三条 notes，旧版
                # _fit 单行会把第三条直接省略。按真实列宽折行、重算高度，
                # 保证「九幽·锁魂（空间崩塌）！」这类机制名完整可见。
                tag_lines = S.wrap_cjk(_gs(a.get("tags"), ""), S.FS_LABEL,
                                       "regular", col_w) if a.get("tags") else []
                tag_lines = [ln for ln in tag_lines if ln]
                # 伤害构成可能放不下（「面板 3,100 · 防御 -520 · 技能 -386」≈262px）：
                # 按真实列宽折行，不截断 —— 截掉「技能 -386」等于把用户要看的稀释量切了
                comp_lines = S.wrap_cjk(_gs(a.get("comp"), ""), S.FS_LABEL,
                                        "regular", col_w) if a.get("comp") else []
                comp_lines = [ln for ln in comp_lines if ln][:2]
                rh = (LINE_H + (BUBBLE_COMP_H * len(comp_lines))
                      + (BUBBLE_TAG_H * len(tag_lines)) + BUBBLE_ROW_GAP)
                entries.append({"move": mv, "value": val, "tags": tag_lines,
                                "comp": comp_lines,
                                "tone": _gs(a.get("tone"), "mine"), "h": rh})
            if not entries:
                # 空列（敌人已死 / 本回合真没动作）给一个淡色占位，
                # 否则气泡半边纯空白，玩家以为卡坏了（2026-11-19 实拍反馈）。
                # 2026-11-19 再修：数据层 empty_foe 区分「击杀换敌」与「真无动作」，
                # 敌方空列读它换一句人话；我方空列/旧数据缺省仍是「无动作」。
                _ph = _gs(r.get("empty_foe"), "无动作") if key == "foe" else "无动作"
                entries = [{"move": _ph, "value": "", "tags": "",
                            "tone": "dim", "h": LINE_H + BUBBLE_ROW_GAP,
                            "comp": [], "placeholder": True}]
            sides[key] = entries
        col_h = max(
            sum(e["h"] for e in sides["mine"]),
            sum(e["h"] for e in sides["foe"]),
            BUBBLE_ROW_GAP + 8,   # 空列也保留一行高度，气泡不会一边塌掉
        )
        sum_text = _gs(r.get("sum_text"), "")
        foot_l = _gs(r.get("foot_l"), "")
        foot_r = _gs(r.get("foot_r"), "")
        foot_h = BUBBLE_FOOT_H if (foot_l or foot_r) else 0
        plan.append({"round": _gi(r.get("round")), "sides": sides,
                     "sum_text": sum_text, "foot_l": foot_l, "foot_r": foot_r,
                     "col_w": col_w, "col_h": col_h,
                     "h": BUBBLE_PAD + BUBBLE_HEADER_H + BUBBLE_SIDE_H
                          + col_h + foot_h + BUBBLE_PAD})
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


# ---------------------------------------------------------------- 分页
#: 单页最大图高（px）。2026-11-19 用户「超长就分页」：26 回合的完整战报约
#: 5,200px，手机上要滑好几屏才看得完；超过本高度就按**回合边界**切页。
#: 2,600 让常见 6~8 回合战报仍然单页（不打扰），长战报才分。
PAGE_MAX_H = 2600
#: 最多几页。一条消息内多图是 markdown 内嵌（每条一个素材上传），页数过多拖慢
#: 发送；超出上限时把剩余回合并入末页 —— 宁可末页高，绝不丢回合。
MAX_PAGES = 5

#: 卡片上下固定开销（标题行 + 分隔线 + 底部提示行 + 内边距），与 build() 同式。
CHROME_H = PAD + FS_TITLE + 8 + 1 + 18 + 12 + FS_BODY + PAD
#: 页副标题行高（多页时才有）。
SUB_H = FS_BODY + 8
#: 续页固定内容只有「胜负徽标 + 逐招明细标题」——玩家翻页仍知道打的是谁、什么结果。
CONT_FIXED_H = H_VERDICT + VERDICT_GAP + CAP_BEFORE + FS_GROUP + CAP_AFTER


def card_height(data: dict, content_w: int) -> float:
    """一张卡成品的真实高度（与 :func:`build` 的 ``h`` 同式）。

    分页切点、门禁「每页都不超预算」、实拍核对都读这一个函数，
    绝不在别处按「回合数 × 估计值」拍脑袋。
    """
    _blocks, bh = _layout(data, content_w)
    sub_h = SUB_H if str(data.get("subtitle") or "") else 0
    return CHROME_H + sub_h + bh


def split_pages(data: dict, width: int | None = None,
                max_h: int = PAGE_MAX_H, max_pages: int = MAX_PAGES) -> list[dict]:
    """按实测高度把回看卡切成多页；装得下就原样返回 ``[data]``（短战报零变化）。

    * 切点只在**回合边界**：一回合的气泡绝不被劈成两半；
    * 首页保留终局/输出对比/关键节点/战况，续页只留胜负徽标 + 逐招明细；
    * 每页写 ``page``（index/total/from/to，供门禁与审计）和 ``subtitle``
      （顶部直接印「第 2/3 页 · 回合 9-16」，翻到哪页一眼看得出来）；
    * 单回合气泡高过预算时也必须整块放进某页（丢回合比页高更糟）。
    """
    rounds = [r for r in (data.get("rounds") or []) if isinstance(r, dict)]
    if len(rounds) < 2:
        return [data]
    width = max(560, min(int(width or 720), 900))
    content_w = width - PAD * 2
    if card_height(data, content_w) <= max_h:
        return [data]

    plan = _bubble_plan(rounds, content_w)
    _blocks, bh = _layout(data, content_w)
    rounds_h = sum(b["h"] for b in plan) + BUBBLE_GAP * (len(plan) - 1)
    first_cap = max_h - CHROME_H - SUB_H - (bh - rounds_h)
    cont_cap = max_h - CHROME_H - SUB_H - CONT_FIXED_H

    def _caps(k: int) -> list[float]:
        return [first_cap] + [cont_cap] * max(0, k - 1)

    def _pack(caps: list[float], scale: float = 1.0) -> list[tuple[int, int]]:
        """按容量贪心装页；``caps`` 按页序取（超出末项就沿用末项）。"""
        out_: list[tuple[int, int]] = []
        i = 0
        while i < len(plan):
            cap = caps[min(len(out_), len(caps) - 1)] * scale
            cap = max(cap, plan[i]["h"])   # 单气泡超预算：整块给它，绝不丢回合
            used, take = 0.0, 0
            while i + take < len(plan):
                cost = plan[i + take]["h"] + (BUBBLE_GAP if take else 0)
                if take and used + cost > cap:
                    break
                used += cost
                take += 1
            out_.append((i, i + take))
            i += take
        return out_

    slices = _pack(_caps(len(plan) + 1))
    capped = False
    if len(slices) > 1:
        # 页数能减就减（少一张图少一次素材上传）；减不动时**均匀填充**：
        # 贪心会把末页剩成「只有一回合」的半空卡（实拍 26 回合 → 2557/2539/443）。
        # 二分找「还能装进 k 页的最小容量」，等价于把总高均摊到 k 页。
        k = len(slices)
        total_h = rounds_h
        while k - 1 >= 1 and total_h <= sum(_caps(k - 1)):
            k -= 1
        lo, hi = 0.2, 1.0
        for _ in range(24):
            mid = (lo + hi) / 2
            if len(_pack(_caps(k), mid)) <= k:
                hi = mid
            else:
                lo = mid
        balanced = _pack(_caps(k), hi)
        if len(balanced) == k:
            slices = balanced

    if len(slices) > max_pages:
        # 页数封顶后**按回合均分**（不是把尾巴全丢进末页 —— 实拍会变成 39,937px 的巨图）。
        # 只有 300 回合熔断这种脏数据才会走到这里：每页都会超出单页预算，
        # 用 page["capped"] 明示「这是上限妥协」，门禁据此放行高度、但仍卡「不丢回合」。
        capped = True
        per = (len(plan) + max_pages - 1) // max_pages
        slices = [(i, min(i + per, len(plan))) for i in range(0, len(plan), per)]

    total = len(slices)
    out: list[dict] = []
    for n, (a, b) in enumerate(slices, 1):
        seg = rounds[a:b]
        page = dict(data)
        page["rounds"] = seg
        page["page"] = {"index": n, "total": total,
                        "from": _gi(seg[0].get("round")),
                        "to": _gi(seg[-1].get("round")),
                        "capped": capped}
        page["subtitle"] = "第 %d/%d 页 · 回合 %d-%d" % (
            n, total, page["page"]["from"], page["page"]["to"])
        if n > 1:
            # 续页只讲「接着打的那几回合」：结算区已在前一页，重复印反而更长。
            for k in ("end", "kpis", "bars", "beats", "groups", "actions"):
                page.pop(k, None)
        out.append(page)
    return out


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

    # 2026-11-20 强敌登场：紧跟胜负徽标，沉浸式「先看见，再听他说」。
    # appearance 与 intro 是两段，分段折行——挤成一段会变成一坨字，读不出层次。
    intro = data.get("intro") or {}
    if intro.get("quote"):
        paras = [str(x) for x in (intro.get("paras") or []) if str(x).strip()]
        if not paras:
            paras = [str(intro["quote"])]
        q_lines = []
        for pa in paras:
            q_lines.extend(_wrap_no_orphan(pa, content_w - 16))
        speaker = str(intro.get("speaker") or "")
        title = str(intro.get("title") or "")
        head = "【登场】%s%s" % (speaker, ("·" + title) if title else "")
        blocks.append(("intro", {"head": head, "lines": q_lines},
                       FS_BODY + len(q_lines) * INTRO_ROW + INTRO_AFTER))

    # 终局资源条紧跟胜负徽标：先回答「为什么输」，再谈打得多狠。
    # 旧档（无 end_reason）end 为 {}，整块不出现。
    end = data.get("end") or {}
    end_rows = [r for r in (end.get("rows") or []) if isinstance(r, dict)]
    if end_rows:
        blocks.append(_cap("终局"))
        blocks.append(("end", end_rows, END_ROW * len(end_rows) + END_AFTER))

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
def _draw_intro(c: Canvas, payload: dict, x0: int, y: int, content_w: int,
                p: dict, rec) -> None:
    """原著 BOSS 登场引言：左侧强调色竖条 + 「登场」头 + 引文（2026-11-20）。"""
    head = str(payload.get("head") or "")
    lines = list(payload.get("lines") or [])
    c.text((x0, y + FS_BODY // 2), head, size=FS_BODY, color=p["accent_color"],
           role="regular", anchor="lm")
    rec("intro_head", head, x0, y, FS_BODY)
    yy = y + FS_BODY
    # 竖条强调引文，长度随实际行数
    bar_h = len(lines) * INTRO_ROW
    if bar_h > 0:
        c.rounded_rect((x0 + 1, yy + 3, x0 + 4, yy + bar_h - 3), radius=2,
                       fill=p["accent_color"])
    for i, line in enumerate(lines):
        ly = yy + i * INTRO_ROW + INTRO_ROW // 2
        c.text((x0 + 12, ly), line, size=FS_BODY, color=p["text_color"],
               role="regular", anchor="lm")
        rec("intro_%d" % i, line, x0 + 12, ly - INTRO_ROW // 2, FS_BODY)


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
    if v.get("exhausted") or v.get("end_text"):
        # 收场原因必须写在徽标旁：「气血耗尽」「力竭判负」「击杀对手」。
        # 力竭熔断（300 回合脏数据）不许冒充正常取胜，也走这一行。
        ex = _gs(v.get("end_text"), "") or "力竭判胜"
        ex_w = _w(ex, FS_BODY, "regular")
        sx = x0 + content_w - ex_w
        if sx > tx + _w(sub, FS_BODY, "regular") + 10:
            # 败因用冷色（同敌方），胜因用暗金：颜色本身也在讲结果
            c.text((sx, y + 44), ex, size=FS_BODY,
                   color=(p["mp_to"] if not win else p["accent_color"]),
                   role="regular", anchor="lm")
            rec("vex", ex, sx, y + 44, FS_BODY)


def _draw_end(c: Canvas, rows: list, x0: int, y: int, content_w: int,
              p: dict, rec) -> None:
    """终局资源条：一条一行「标签 ▸ 按真实比例长的条 ▸ 数值/上限」。

    气血归零那条只会剩一条空轨道 —— 这就是「为什么判负」的图形答案，
    比任何一句「气血耗尽」都直接。``max<=0``（没有分母）时只印数字不画条。
    """
    bar_x0 = x0 + END_LABEL_W
    for i, r in enumerate(rows):
        ry = y + i * END_ROW
        tone = _gs(r.get("tone"), "mine")
        lab = _fit(_gs(r.get("label"), "—"), END_LABEL_W - 10, FS_BODY, "regular")
        c.text((x0, ry + 12), lab, size=FS_BODY, color=p["dim_color"],
               role="regular", anchor="lm")
        rec("n%d_l" % i, lab, x0, ry + 12, FS_BODY)

        val = _gi(r.get("value"))
        mx = _gi(r.get("max"))
        txt = ("%s / %s" % (_mn(val), _mn(mx))) if mx > 0 else _mn(val)
        # 轨道右端按**本行数值实测宽度**让位：高等级战斗的敌血/上限能到 7~9 位数
        # （「999,814,075 / 1,000,000,000」≈ 200px > END_VAL_W），固定 150px 会让
        # 数字压在条上（实拍抓到）。数值本身绝不截断，改缩条。
        txt_w = _w(txt, FS_BODY, "regular")
        bar_x1 = x0 + content_w - max(END_VAL_W, txt_w + 8)
        track_w = max(60, bar_x1 - bar_x0)
        if mx > 0:
            c.rounded_rect((bar_x0, ry + 6, bar_x0 + track_w, ry + 18), radius=6,
                           fill=p["track_color"])
            ratio = max(0.0, min(1.0, val / float(mx)))
            fw = int(track_w * ratio)
            if val > 0:
                fw = max(6, fw)   # 还有血就得看得见，别缩成一根头发
            if fw > 0:
                g1, g2 = _resource_grad(p, tone)
                c.gradient_rect((bar_x0, ry + 6, bar_x0 + fw, ry + 18),
                                g1, g2, radius=6)
            # 条宽写进盒名（n{i}_bar{px}）：几何自检只认矩形，认不出「0 血却画满条」，
            # 门禁靠这个名字断言「归零的空条真的是空条」。
            rec("n%d_bar%d" % (i, fw), "", bar_x0, ry + 6, 12, anchor="la")
        c.text((x0 + content_w, ry + 12), txt, size=FS_BODY,
               color=p["text_color"], role="regular", anchor="rm")
        rec("n%d_v" % i, txt, x0 + content_w - txt_w, ry + 12, FS_BODY)


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
        line_r = x0 + content_w - BUBBLE_PAD
        # 回合战损摘要放抬头右侧：本回合挨了多少、打掉对方多少（零额外高度）
        if b["sum_text"]:
            s = _fit(b["sum_text"], line_r - line_x - 12, S.FS_LABEL, "regular")
            c.text((line_r, hy), s, size=S.FS_LABEL, color=p["dim_color"],
                   role="regular", anchor="rm")
            rec("r%d_sum" % ri, s, line_r - _w(s, S.FS_LABEL), hy, S.FS_LABEL)
            line_r -= _w(s, S.FS_LABEL) + 12
        if line_x < line_r - 8:
            c.line((line_x, hy, line_r, hy), fill=p["line_color"], width=1)

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
                if e.get("placeholder"):
                    # 空列占位只画淡色提示，不画数值盒（否则污染右锚定自检）
                    c.text((cx, cy), e["move"], size=S.FS_LABEL,
                           color=p["dim_color"], role="regular", anchor="lm")
                    rec("r%d_%s%d_ph" % (ri, key, ei), e["move"], cx, cy, S.FS_LABEL)
                    ey += e["h"]
                    continue
                c.text((cx, cy), e["move"], size=FS_BODY, color=p["text_color"],
                       role="regular", anchor="lm")
                rec("r%d_%s%d_m" % (ri, key, ei), e["move"], cx, cy, FS_BODY)
                vw = _w(e["value"], FS_NUM, "bold")
                c.text((right, cy), e["value"], size=FS_NUM,
                       color=_tone_color(p, e["tone"]), role="bold", bold=True,
                       anchor="rm")
                rec("r%d_%s%d_v" % (ri, key, ei), e["value"], right - vw, cy,
                    FS_NUM, role="bold")
                # 伤害构成行：面板 → 防御削减 → 技能吸收。实收就是右侧那个大数字，
                # 不重复印（重复=噪声），没有削减时计划里根本没有这一行。
                ny = ey + LINE_H
                for li, ln in enumerate(e.get("comp") or []):
                    c.text((cx, ny + BUBBLE_COMP_H // 2), ln, size=S.FS_LABEL,
                           color=p["dim_color"], role="regular", anchor="lm")
                    rec("r%d_%s%d_c%d" % (ri, key, ei, li), ln, cx,
                        ny + BUBBLE_COMP_H // 2, S.FS_LABEL)
                    ny += BUBBLE_COMP_H
                for ti, tag in enumerate(e["tags"] or []):
                    ty = ny + BUBBLE_TAG_H * ti + BUBBLE_TAG_H // 2
                    c.text((cx, ty), tag, size=S.FS_LABEL,
                           color=p["dim_color"], role="regular", anchor="lm")
                    rec("r%d_%s%d_t%d" % (ri, key, ei, ti), tag, cx, ty, S.FS_LABEL)
                ey += e["h"]

        # 气泡底部：回合末余量。左「余血/余气」右「敌血」，两端锚定便于竖扫
        if b["foot_l"] or b["foot_r"]:
            fy = by + bh - BUBBLE_PAD - BUBBLE_FOOT_H // 2
            if b["foot_l"]:
                c.text((x0 + BUBBLE_PAD, fy), b["foot_l"], size=S.FS_LABEL,
                       color=p["dim_color"], role="regular", anchor="lm")
                rec("r%d_fl" % ri, b["foot_l"], x0 + BUBBLE_PAD, fy, S.FS_LABEL)
            if b["foot_r"]:
                c.text((x0 + content_w - BUBBLE_PAD, fy), b["foot_r"],
                       size=S.FS_LABEL, color=_tone_color(p, "foe"),
                       role="regular", anchor="rm")
                rec("r%d_fr" % ri, b["foot_r"],
                    x0 + content_w - BUBBLE_PAD - _w(b["foot_r"], S.FS_LABEL),
                    fy, S.FS_LABEL)


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
        elif kind == "intro":
            _draw_intro(c, payload, x0, y, content_w, p, rec)
        elif kind == "end":
            _draw_end(c, payload, x0, y, content_w, p, rec)
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
