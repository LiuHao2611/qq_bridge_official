# -*- coding: utf-8 -*-
"""斗破 · 完整战斗回看的数据整理层（零 nonebot / 零文件 IO）。

唯一数据源是 ``p["last_battle"]``（由 core.record_last_battle 写入）。本模块只做：
  * 把逐条 log 整理成「结算界面」需要的结构化数据；
  * 生成纯文本兜底；
  * 给战斗评价提供标签。

2026-11-19 改版（用户：「纯文字展示看着太复杂，不够明了」）
-------------------------------------------------------------
旧版把 20 条 log 摊成 40 行长文本，伤害/血量全埋在句子里，玩家读不出「谁打得
更狠、关键在哪」。参照 MOBA / DNF / WoW 结算界面的通行做法改为五段：

  1. ``verdict``   胜负徽标 + 玩法 + 对手 + 回合数（力竭熔断会额外标注）
  2. ``kpis``      三格：我方输出 / 敌方输出 / 每回合输出
  3. ``bars``      双方输出占比双条（长度按真实数值缩放——**必须不同长**，
                   这是「看得出形变」的唯一手段）
  4. ``beats``     关键节点时间轴（机缘/心火/暴击/踉跄/服药/被控/反伤，≤5 条）
  5. ``actions``   逐招明细，一条一行（左：招式，右：数值），不再一条占两行

``groups`` 仍按「战况 / 逐招明细」两段输出，供 ``doupo_ruin`` 复用同一套排版骨架；
新字段是**纯增量**，模板拿不到就退回旧的分组渲染。

不在这里重新计算战斗数值——数值只来自 core，文案只来自这里，避免图文分叉。
"""
from __future__ import annotations

#: 关键节点上限：超过 5 条就不是「关键」了，页面也会被撑长。
BEAT_LIMIT = 5
#: 逐招明细里每条最多带几个词条（再多就该做减法）。
TAG_LIMIT = 4


def _i(v, default=0) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _s(v, default="—") -> str:
    if v is None or v == "":
        return default
    return str(v)


def _num(v) -> str:
    """千分位。5 位伤害裸写「12846」在卡上难认，``12,846`` 一眼读得出来。"""
    return f"{_i(v):,}"


def _pct(part: int, total: int) -> int:
    if total <= 0:
        return 0
    return int(round(part * 100.0 / total))


def _actor(it: dict) -> str:
    who = str(it.get("who") or "")
    if who == "v":
        return "对方"
    if who == "p":
        return "我方"
    # PVE 的 fight() 不写 who：怪打人那条带 taken=True，必须显示成「敌方」，
    # 否则战报会写成「我方·赤炎狼 兽击 700」，读起来像自己打自己
    # （2026-11-19 实拍卡确认）。
    if it.get("taken"):
        return "敌方"
    # 敌人出手但没打中的三类也属敌方动作：闪避(miss)/敌人自己失误(mishap 踉跄)/
    # 敌人被控(miss)。core.fight 写它们时既无 who、也无 taken（没造成伤害），
    # 旧判据只看 taken → 全归我方 → 气泡卡「我方」列混进敌人的闪避/踉跄/被控，
    # 而敌方列反而空白（2026-11-19 实拍：R7/R18 我方列"闪避/踉跄"、敌方列空）。
    if it.get("miss") or it.get("mishap"):
        return "敌方"
    return "我方"


def _move(it: dict) -> str:
    if it.get("aid"):
        return "贵人相助"
    if it.get("pill"):
        return "服用" + _s(it.get("pill"), "丹药")
    return _s(it.get("move") or it.get("kind") or
              ("普攻" if not it.get("skill") else "斗技"), "出手")


def _logs(battle: dict) -> list[dict]:
    return [it for it in (battle.get("log") or []) if isinstance(it, dict)]


def display_rounds(logs: list[dict]) -> list[int]:
    """把多敌遭遇战里「每个对手都从第 1 回合重新数」的回合号拉平成连续回合。

    依据：``res["rounds"]`` 是整场的**累计**回合数（5+4=9），而 log 里每个对手的
    ``fight()`` 各自从 1 开始数 —— 直接显示会出现两个「回合1」，最后一行也与表头
    的「9 回合」对不上（2026-11-19 实拍生产战报确认）。

    换段判据**优先看 ``foe`` 变化**：同一回合里玩家和敌人各一条，``round`` 相等，
    用「round <= 上一条」判会每回合都误判成换段。
    """
    out: list[int] = []
    offset = 0
    prev_r = 0
    prev_foe = ""
    last = 0
    for it in logs:
        r = max(1, _i(it.get("round"), 1))
        foe = _s(it.get("foe"), "")
        if out and ((foe and prev_foe and foe != prev_foe) or r < prev_r):
            offset = last
        g = offset + r
        out.append(g)
        last = max(last, g)
        prev_r = r
        if foe:
            prev_foe = foe
    return out


# ---------------------------------------------------------------- 汇总
def totals(battle: dict) -> dict:
    """双方输出 / 斗气消耗 / 暴击数 / 回合均输出。全部由 log 现算，不猜。

    ⚠️ 阵营判据必须用 ``_actor``：PVE 靠 ``taken`` 认敌方，切磋（``who="v"``）
    压根没有 ``taken`` 字段 —— 只看 ``taken`` 会把切磋里对手的输出全算成我方。
    """
    logs = _logs(battle)
    mine = sum(_i(it.get("dmg")) for it in logs if _actor(it) == "我方")
    foe = sum(_i(it.get("dmg")) for it in logs if _actor(it) != "我方")
    mp = sum(_i(it.get("mp_cost")) for it in logs)
    rounds = max(1, _i(battle.get("rounds"), 1))
    return {
        "mine": mine,
        "foe": foe,
        "mp": mp,
        "per_round": int(round(mine / rounds)),
        "crit": sum(1 for it in logs if it.get("crit")),
        "hit": sum(1 for it in logs if not it.get("taken") and _i(it.get("dmg")) > 0),
        "pills": sum(1 for it in logs if it.get("pill")),
        "rounds": rounds,
    }


def verdict_info(battle: dict) -> dict:
    s = battle.get("summary") or {}
    return {
        "win": bool(battle.get("win")),
        "kind": _s(battle.get("kind"), "战斗"),
        "opponent": _s(battle.get("opponent"), "未知对手"),
        "rounds": _i(battle.get("rounds")),
        # 2026-11-19 熔断：300 回合脏数据判定，必须显式标出来，不能冒充正常取胜
        "exhausted": bool(s.get("exhausted")),
    }


def kpi_cells(battle: dict) -> list[dict]:
    """三格 KPI：读者三秒内要知道「我打得多还是挨得多」。"""
    t = totals(battle)
    return [
        {"label": "我方输出", "value": _num(t["mine"]), "tone": "mine"},
        {"label": "敌方输出", "value": _num(t["foe"]), "tone": "foe"},
        {"label": "每回合", "value": _num(t["per_round"]), "tone": "plain"},
    ]


def damage_bars(battle: dict) -> list[dict]:
    """双方输出占比条。``ratio`` 相对较大值缩放 —— 长度差就是实力差。"""
    t = totals(battle)
    top = max(t["mine"], t["foe"], 1)
    total = t["mine"] + t["foe"]
    return [
        {"label": "我方", "value": t["mine"], "text": _num(t["mine"]),
         "ratio": round(t["mine"] / top, 4), "share": _pct(t["mine"], total),
         "tone": "mine"},
        {"label": "敌方", "value": t["foe"], "text": _num(t["foe"]),
         "ratio": round(t["foe"] / top, 4), "share": _pct(t["foe"], total),
         "tone": "foe"},
    ]


# ---------------------------------------------------------------- 关键节点
#: 优先级顺序：越靠前越值得占一个位置（心火/踉跄是 2026-11-19 新增战斗逻辑）
_BEAT_ORDER = ("机缘", "心火", "踉跄", "暴击", "服药", "被控", "反伤")


def _beat_tag(it: dict) -> str:
    if it.get("aid"):
        return "机缘"
    if it.get("fortune") or _s(it.get("kind")) == "心火共鸣":
        return "心火"
    if it.get("mishap"):
        return "踉跄"
    if it.get("crit") and _i(it.get("dmg")) > 0:
        return "暴击"
    if it.get("pill"):
        return "服药"
    if _s(it.get("kind")) == "被控":
        return "被控"
    if it.get("reflect"):
        return "反伤"
    return ""


def _beat_text(tag: str, it: dict) -> str:
    notes = [x for x in (it.get("notes") or []) if isinstance(x, str) and x]
    if tag == "机缘":
        return "%s %s（%s）" % (_move(it), _num(it.get("dmg")),
                               _s(it.get("helper"), "无名斗皇"))
    if tag == "心火":
        return notes[0] if notes else "心火翻涌，攻势自涨"
    if tag == "踉跄":
        return notes[0] if notes else "气血逆行，这一手落了空"
    if tag == "暴击":
        return "%s %s" % (_move(it), _num(it.get("dmg")))
    if tag == "服药":
        return "%s %s" % (_s(it.get("pill"), "丹药"), "+" + _num(it.get("amount")))
    if tag == "被控":
        return "%s，受制一回合" % _move(it)
    return "%s 反震 %s" % (_move(it), _num(it.get("dmg")))


def beats(battle: dict, limit: int = BEAT_LIMIT) -> list[dict]:
    """关键节点：每类只取最狠的一条，按优先级挑满，再按回合先后排序显示。"""
    logs = _logs(battle)
    rmap = display_rounds(logs)
    best: dict[str, tuple[int, int, dict]] = {}
    for i, it in enumerate(logs):
        tag = _beat_tag(it)
        if not tag:
            continue
        score = _i(it.get("dmg"))
        cur = best.get(tag)
        if cur is None or score > cur[0]:
            best[tag] = (score, i, it)
    chosen = []
    for tag in _BEAT_ORDER:
        if tag in best and len(chosen) < limit:
            score, pos, it = best[tag]
            chosen.append((pos, {
                "round": rmap[pos] if pos < len(rmap) else pos + 1,
                "tag": tag,
                "text": _beat_text(tag, it),
                "tone": "foe" if it.get("taken") else "mine",
            }))
    chosen.sort(key=lambda x: x[0])
    return [b for _, b in chosen]


# ---------------------------------------------------------------- 逐招明细
def action_items(battle: dict) -> list[dict]:
    """一条 log 一行：左「序号｜回合 阵营 招式」，右「数值」，中间夹词条。"""
    out: list[dict] = []
    logs = _logs(battle)
    rmap = display_rounds(logs)
    for i, it in enumerate(logs, 1):
        side = _actor(it)
        tags: list[str] = []
        if it.get("crit"):
            tags.append("暴击")
        if _s(it.get("kind")) == "心火共鸣":
            tags.append("心火")
        if it.get("mishap"):
            tags.append("踉跄")
        if it.get("miss"):
            tags.append("闪避")
        if it.get("reflect"):
            tags.append("反伤")
        if it.get("dot"):
            tags.append("持续")
        if it.get("aid"):
            tags.append("机缘")
        cost = _i(it.get("mp_cost"))
        if cost:
            tags.append("斗气-%d" % cost)
        for n in (it.get("notes") or [])[:2]:
            # 不在数据层硬切：卡片由 _fit 按实际可用宽度出省略号，硬切会切在词中间
            s = _s(n, "")
            if s and s not in tags:
                tags.append(s)

        move = _move(it)
        # 「踉跄·气血逆行」「心火·心火冲顶」这种自我复读要压掉：招式名已含该词时不再重复打标
        tags = [t for t in tags if t and t not in move]

        if it.get("pill"):
            value, tone = "+" + _num(it.get("amount")), "pill"
        elif _i(it.get("dmg")) > 0:
            value, tone = _num(it.get("dmg")), ("mine" if side == "我方" else "foe")
        else:
            # 心火共鸣 / 踉跄 / 被控这类 0 伤害事件：写「—」比写「0」诚实且不误导
            value, tone = "—", "dim"
        out.append({
            "idx": _i(it.get("index"), i),
            "round": rmap[i - 1] if (i - 1) < len(rmap) else i,
            "side": side,
            "move": move,
            "value": value,
            "tone": tone,
            "tags": "·".join(tags[:TAG_LIMIT]),
        })
    return out


def round_bubbles(battle: dict) -> list[dict]:
    """按回合把逐招聚成气泡数据：一个回合一条，左右两列分别是双方行为。

    ``action_items`` 的 ``round`` 已是多敌拉平后的连续回合号（见 display_rounds），
    所以这里直接按它分组即可。``who='v'`` 的切磋对手写成「对方」，也归到敌方列。

    结构：``[{"round": n, "mine": [action, ...], "foe": [action, ...]}, ...]``。
    这是**纯增量字段**：``groups`` / ``actions`` / 文本兜底全部保持原样，
    复用 doupo_battle 骨架的 doupo_ruin 看不到 rounds，继续走旧路径。
    """
    buckets: dict[int, dict] = {}
    for a in action_items(battle):
        rnd = _i(a.get("round"), 1)
        b = buckets.setdefault(rnd, {"round": rnd, "mine": [], "foe": []})
        # 只按阵营分列，不改 action 本身（值/词条/配色都沿用 action_items 的口径）
        b["mine" if a.get("side") == "我方" else "foe"].append(a)
    return [buckets[k] for k in sorted(buckets)]


def action_rows(battle: dict) -> list[str]:
    """逐招明细的纯文本形态（``text()`` 兜底与旧分组渲染共用同一条数据源）。"""
    rows = []
    for v in action_items(battle):
        tag = f"（{v['tags']}）" if v["tags"] else ""
        rows.append(f"{v['idx']:02d}｜回合{v['round']} {v['side']} {v['move']}{tag} {v['value']}")
    return rows or ["（本场没有逐招记录）"]


def head_rows(battle: dict) -> list[str]:
    """战况事实行（KPI 之外的文字部分，最多 3 行）。"""
    s = battle.get("summary") or {}
    pl = battle.get("player") or {}
    drops = [d for d in (s.get("drops") or []) if isinstance(d, (list, tuple)) and len(d) >= 2]
    drop = "、".join(f"{n}×{c}" for n, c in drops)
    out = [
        f"战力 {_num(pl.get('power'))}｜境 lv{_i(pl.get('lv'), 1)}"
        f"｜气血 {_num(pl.get('hp'))}/{_num(pl.get('hp_max'))}"
        f"｜斗气 {_num(pl.get('mp'))}/{_num(pl.get('mp_max'))}",
        f"代价 {_num(s.get('taken'))}｜修为 +{_num(s.get('exp'))}"
        f"｜金币 +{_num(s.get('gold'))}",
    ]
    if drop:
        out.append(f"战利 {drop}")
    return out


# ---------------------------------------------------------------- 出口
def build_data(battle: dict) -> dict:
    """给 ``doupo_battle`` 模板的参数，纯文本兜底与图片卡共用。"""
    battle = battle or {}
    rows = head_rows(battle)
    return {
        "title": "战斗回看",
        "verdict": verdict_info(battle),
        "kpis": kpi_cells(battle),
        "bars": damage_bars(battle),
        "beats": beats(battle),
        "actions": action_items(battle),
        "rounds": round_bubbles(battle),
        "groups": [
            {"name": "战况", "rows": rows},
            {"name": "逐招明细", "rows": action_rows(battle)},
        ],
        "tip": "发「战斗评价」听一句毒舌 · 只保留最后一场战斗",
    }


def text(battle: dict) -> str:
    """渲染失败时的纯文本兜底；用户要求完整过程时不能静默不回。"""
    battle = battle or {}
    data = build_data(battle)
    v = data["verdict"]
    out = [f"⚔️ {data['title']}｜{'胜' if v['win'] else '败'}｜{v['kind']}"
           f"｜{v['rounds']}回合｜对手 {v['opponent']}"]
    t = totals(battle)
    out.append(f"我方输出 {_num(t['mine'])}｜敌方输出 {_num(t['foe'])}"
               f"｜每回合 {_num(t['per_round'])}")
    for g in data["groups"]:
        out.append(f"【{g['name']}】")
        out.extend(g["rows"])
    if data["beats"]:
        out.append("【关键节点】")
        out.extend(f"回合{b['round']} {b['tag']}·{b['text']}" for b in data["beats"])
    out.append(data["tip"])
    return "\n".join(out)


def tags(battle: dict) -> list[str]:
    """给评语库用的战斗标签；只读 last_battle，不改任何数值。"""
    battle = battle or {}
    s = battle.get("summary") or {}
    pl = battle.get("player") or {}
    logs = _logs(battle)

    dmg = sum(_i(it.get("dmg")) for it in logs
              if not it.get("taken") and not it.get("miss")
              and not it.get("dot") and not it.get("reflect"))
    spent = sum(_i(it.get("mp_cost")) for it in logs)
    rounds = _i(battle.get("rounds"))
    taken = _i(s.get("taken"))
    hp_max = max(1, _i(pl.get("hp_max"), 1))
    mp_max = max(1, _i(pl.get("mp_max"), 1))

    out = {"win" if battle.get("win") else "lose"}
    if any(it.get("aid") for it in logs):
        out.add("aid")
    if any(it.get("crit") for it in logs):
        out.add("crit")
    if any(it.get("miss") and _s(it.get("kind")) != "被控" for it in logs):
        out.add("miss")
    if any(_s(it.get("kind")) == "被控" for it in logs):
        out.add("control")
    if any(it.get("dot") for it in logs):
        out.add("dot")
    if any(it.get("reflect") for it in logs):
        out.add("reflect")
    if rounds >= 10:
        out.add("many_rounds")
    if dmg / max(1, rounds) < 80:
        out.add("low_dmg")
    if spent == 0 and rounds >= 3:
        out.add("no_mp")
    elif _i(pl.get("mp")) <= mp_max * 0.12:
        out.add("low_mp")
    if taken >= hp_max * 0.55:
        out.add("high_taken")
    return sorted(out)
