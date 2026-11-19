# -*- coding: utf-8 -*-
"""斗破 · 完整战斗回看的数据整理层（零 nonebot / 零文件 IO）。

唯一数据源是 ``p["last_battle"]``（由 core.record_last_battle 写入）。本模块只做：
  * 把逐条 log 变成「招式 + 伤害 + 气血/斗气轨迹 + 词条」的卡片行；
  * 生成纯文本兜底；
  * 给战斗评价提供标签。

不在这里重新计算战斗数值——数值只来自 core，文案只来自这里，避免图文分叉。
"""
from __future__ import annotations


def _i(v, default=0) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _s(v, default="—") -> str:
    if v is None or v == "":
        return default
    return str(v)


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
    return "我方"


def _move(it: dict) -> str:
    if it.get("aid"):
        return "贵人相助"
    if it.get("pill"):
        return "服用" + _s(it.get("pill"), "丹药")
    return _s(it.get("move") or it.get("kind") or
              ("普攻" if not it.get("skill") else "斗技"), "出手")


def _via(it: dict) -> str:
    if it.get("aid"):
        return "机缘"
    # 2026-11-19 新战斗事件：不另建渲染路径，只在归因列给出正确来源。
    if it.get("pill"):
        return "丹药·自动"
    if it.get("mishap"):
        return "天赋·踉跄"
    if it.get("kind") == "心火共鸣":
        return "心火·气机"
    if it.get("kind") == "中毒":
        return "天赋·中毒"
    if it.get("miss"):
        return "斗技·控场" if _s(it.get("kind")) == "被控" else "敏捷·闪避"
    if it.get("dot"):
        return "斗技·持续"
    if it.get("reflect"):
        return "斗技·反伤"
    if it.get("taken"):
        return "防御减伤"
    via = "力量·斗气" if it.get("skill") else "力量·武器"
    if it.get("crit"):
        via += "·暴击敏捷"
    return via


def _notes(it: dict) -> str:
    notes = it.get("notes") or []
    if isinstance(notes, str):
        notes = [notes]
    out = [_s(x, "") for x in notes]
    out = [x for x in out if x and x != "—"]
    if it.get("aid"):
        out.insert(0, "贵人·" + _s(it.get("helper") or "无名斗皇", "无名斗皇"))
    return "、".join(dict.fromkeys(out))


def action_rows(battle: dict) -> list[str]:
    """把完整 log 变成两行一条的卡片行；不截断，不重算数值。"""
    rows: list[str] = []
    for i, it in enumerate(battle.get("log") or [], 1):
        if not isinstance(it, dict):
            continue
        rnd = _i(it.get("round"), i)
        scope = f"{_actor(it)}·{_s(it.get('foe') or '敌手')}"
        if it.get("pill"):
            dmg = "+%d" % _i(it.get("amount"))
        else:
            dmg = _i(it.get("dmg"))
        line1 = (f"{i:02d}｜{scope}·回合{rnd}｜{_move(it)} {dmg}"
                 f"（{_via(it)}）")
        parts = []
        hp_b, hp_a = it.get("hp_before"), it.get("hp_after")
        if hp_b is not None or hp_a is not None:
            parts.append(f"气血 {_s(hp_b)}→{_s(hp_a)}")
        mp_b, mp_a = it.get("mp_before"), it.get("mp_after")
        if mp_b is not None or mp_a is not None:
            cost = _i(it.get("mp_cost"))
            parts.append(f"斗气 {_s(mp_b)}→{_s(mp_a)}（-{cost}）" if cost
                         else f"斗气 {_s(mp_b)}→{_s(mp_a)}")
        fb, fa = it.get("foe_hp_before"), it.get("foe_hp_after")
        if fb is not None or fa is not None:
            parts.append(f"敌血 {_s(fb)}→{_s(fa)}")
        notes = _notes(it)
        if notes:
            parts.append("词条 " + notes)
        rows.append(line1)
        rows.append("　" + ("　".join(parts) if parts else "无额外资源变化"))
    return rows or ["（本场没有逐招记录）"]


def head_rows(battle: dict) -> list[str]:
    s = battle.get("summary") or {}
    pl = battle.get("player") or {}
    drops = s.get("drops") or []
    drop = "、".join(f"{n}×{c}" for n, c in drops if isinstance((n, c), (list, tuple)))
    verdict = "胜" if battle.get("win") else "败"
    out = [
        f"{_s(battle.get('kind'), '战斗')}｜{verdict}｜{_i(battle.get('rounds'))}回合"
        f"｜对手 {_s(battle.get('opponent'))}",
        f"战力 {_i(pl.get('power'))}｜境界 lv{_i(pl.get('lv'), 1)}"
        f"｜气血 {_i(pl.get('hp'))}/{_i(pl.get('hp_max'))}"
        f"｜斗气 {_i(pl.get('mp'))}/{_i(pl.get('mp_max'))}",
        f"代价 {_i(s.get('taken'))}｜修为 +{_i(s.get('exp'))}"
        f"｜金币 +{_i(s.get('gold'))}" + (f"｜战利 {drop}" if drop else ""),
    ]
    return out


def build_data(battle: dict) -> dict:
    """给 ``doupo_battle`` 模板的参数，纯文本兜底与图片卡共用。"""
    battle = battle or {}
    return {
        "title": "战斗回看",
        "groups": [
            {"name": "战况", "rows": head_rows(battle)},
            {"name": "逐招明细", "rows": action_rows(battle)},
        ],
        "tip": "发「战斗评价」听一句毒舌 · 只保留最后一场战斗",
    }


def text(battle: dict) -> str:
    """渲染失败时的纯文本兜底；用户要求完整过程时不能静默不回。"""
    battle = battle or {}
    data = build_data(battle)
    out = [f"⚔️ {data['title']}"]
    for g in data["groups"]:
        out.append(f"【{g['name']}】")
        out.extend(g["rows"])
    out.append(data["tip"])
    return "\n".join(out)


def tags(battle: dict) -> list[str]:
    """给评语库用的战斗标签；只读 last_battle，不改任何数值。"""
    battle = battle or {}
    s = battle.get("summary") or {}
    pl = battle.get("player") or {}
    logs = [it for it in (battle.get("log") or []) if isinstance(it, dict)]

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
