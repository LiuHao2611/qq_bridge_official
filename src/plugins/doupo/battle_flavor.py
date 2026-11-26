# -*- coding: utf-8 -*-
"""斗破 · 情境化梗味旁白（2026-11-26 用户「插入的语句一定要符合玩家当前实际情况」）。

## 为什么单独一个模块
素材库 ``docs/斗破苍穹出圈台词梗素材_战斗台词用.md`` 里的梗句**不能无脑贴进战报**：
玩家在乌坦城后山（ch1）打一只嗜血鼠，回包里蹦出「斗宗强者，恐怖如斯」就是穿帮——
不符合当前区域 / 境界 / 斗技。用户硬要求「别搞的不符合当前区域和斗技，境界等等，
搞清楚再开始加」。所以这里把每句梗绑定一组**真实字段门控**，条件不满足就不出句
（宁可不放，绝不硬塞——禁降级红线）。

判定字段全部来自既有战斗上下文，来自：
  - ``cfg.realm_index(p.lv)``         → 境界档（0..12）
  - ``detail[i]["kind"]``             → 敌人阵营（魂殿/黑盟/魔炎谷/魔兽/悍匪）
  - ``high["move"] / high["tier"]``   → 本场真实最强一击的招式 / 品阶
  - ``war_tier()``                    → 战况档（crush/narrow/sweep/great/lose）
  - ``cfg.CHAPTER_REGION[chapter]``   → 区域（本模块只用来挡地名梗，见下）

## 与 doupo_text.py 的边界（勿越）
``doupo_text.py`` 管**逐字原文台词 + 说话人绑定**（CHAPTER_QUOTE / NPC_SPEAKERS），
出处核对、绑定角色、出处不进回执。本模块**不碰那套**：
  1. 只出**无主旁白**（不加引号、不冒认角色说话），符合 doupo_text 硬纪律第 2 条；
  2. 只用素材库里**筛掉人名**后的句子；
  3. 与逐字原文重复的梗（第 1 章「莫欺少年穷」已在 CHAPTER_QUOTE）**不重复出**；
  4. 含具体地名的梗（「上云岚宗」等）——调用点必须传 ``region``，只有区域串真含
     该地名时才放行，否则宁可不出；本模块默认**不自动出任何地名梗**。
  5. 输出恒为 0 或 1 行，确定性（同 ctx 同句）。

## 优先级（越贴合当前实际越靠前，一场只出一句）
  反派阵营口吻 > 本场真实喊招 > 越级/惨胜「侥幸」 > 惨胜「神清气爽」 > 斗宗境界惊叹
"""
from __future__ import annotations

# 敌方阵营：只有这几类「反派势力」才配说反派口吻的梗（桀桀桀 / 猖狂）。
# 取自 config.MONSTERS[*].kind 与 recipes_data.BOSS_KIND_TOKENS 的实际取值。
_EVIL_KINDS = frozenset(("魂殿", "黑盟", "魔炎谷"))

# 境界档（cfg.realm_index 返回值）：斗宗=7、斗尊=8、半圣=10、斗圣=11、斗帝=12。
# 「斗宗强者，恐怖如斯」只有在场角色真到斗宗及以上才有意义，低境界出是穿帮。
_REALM_DOUZONG = 7

# 战况档（doupo_text.war_tier 返回值）：crush=越级斩杀、narrow=惨胜/险胜。
# sweep（无伤）/great（大捷）说「侥幸」「神清气爽」都是假的，不在此列。
_TIER_CRUSH = "crush"
_TIER_NARROW = "narrow"


def _ctx_int(ctx: dict, key: str) -> int:
    try:
        return int(ctx.get(key) or 0)
    except (TypeError, ValueError):
        return 0


def _ctx_str(ctx: dict, key: str) -> str:
    return str(ctx.get(key) or "")


def _pick(pool: tuple[str, ...], seed: int) -> str:
    """按确定性种子轮换；池空返回空串。"""
    if not pool:
        return ""
    return pool[int(seed) % len(pool)]


def flavor_line(ctx: dict) -> str:
    """按真实战斗上下文返回**至多一行**梗味旁白；无情境命中返回空串。

    ctx 字段（调用点从 cmd_fight 真实数据填，缺字段按「不满足」处理）：
      win: bool          本场是否胜利
      tier: str          war_tier 战况档 crush/narrow/sweep/great/lose
      realm_idx: int     玩家境界档 cfg.realm_index(lv)，0..12
      foe_kind: str      代表敌人阵营 kind
      move: str          本场最强一击招式名（high["move"]）
      move_tier: str     本场最强一击品阶（high["tier"]）
      seed: int          确定性轮换种子

    返回空串时调用点直接丢弃该行，**不得替换为任何通用句子**（禁兜底）。
    """
    if not isinstance(ctx, dict):
        return ""
    win = bool(ctx.get("win"))
    tier = _ctx_str(ctx, "tier")
    realm_idx = _ctx_int(ctx, "realm_idx")
    foe_kind = _ctx_str(ctx, "foe_kind")
    move = _ctx_str(ctx, "move")
    seed = _ctx_int(ctx, "seed")

    # 情境 1 · 反派势力：敌方真是魂殿/黑盟/魔炎谷才出反派口吻。
    # 普通魔兽/悍匪局绝不冒出反派笑声。两条按种子确定性轮换。
    if foe_kind in _EVIL_KINDS:
        return _pick(("桀桀桀。", "猖狂。"), seed)

    # 情境 2 · 本场真实喊招：最强一击精确等于技能库里确有的一招。
    # 比境界更具体，故排在境界前；玩家没打出的招绝不喊。
    if move == "焰分噬浪尺":
        return "焰分噬浪尺！"

    # 情境 3 · 越级斩杀的「侥幸」：真赢了 + 战况档 crush（敌方高出 2 级及以上）。
    # 只有真越级，谦虚「侥幸」才有反差；顺风局说侥幸是假的。
    if win and tier == _TIER_CRUSH:
        return _pick(("侥幸罢了。", "这一场，赢得侥幸。"), seed)

    # 情境 4 · 惨胜「神清气爽」：narrow（血量掉到一半以下才赢），濒死反杀的反差。
    if win and tier == _TIER_NARROW:
        return "神清气爽。"

    # 情境 5 · 斗宗境界惊叹：玩家真到斗宗档，作为无主旁白出，不冒充角色。
    if realm_idx >= _REALM_DOUZONG:
        return "斗宗强者，恐怖如斯。"

    # 其余一律不出：败局、普通刷怪、低境界、顺风局都不硬塞梗。
    return ""
