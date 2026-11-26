# -*- coding: utf-8 -*-
"""斗破 · 情境化梗味旁白（2026-11-26 用户「插入的语句一定要符合玩家当前实际情况」）。

## 为什么单独一个模块
素材库 ``docs/斗破苍穹出圈台词梗素材_战斗台词用.md`` 里的梗句**不能无脑贴进战报**：
玩家在乌坦城后山（ch1）打一只嗜血鼠，回包里蹦出「斗宗强者，恐怖如斯」就是穿帮——
不符合当前区域 / 境界 / 斗技。用户硬要求「别搞的不符合当前区域和斗技，境界等等，
搞清楚再开始加」。所以这里把每一句梗绑定一组**真实字段门控**，条件不满足就不出句
（宁可不放，绝不硬塞——禁降级红线）。

## 判定字段（全部来自既有战斗上下文）
  - ``detail[i]["kind"]``             → 敌人阵营（魂殿/黑盟/魔炎谷/魔兽/悍匪）
  - ``cfg.realm_index(p.lv)``         → 境界档（0..12）
  - ``high["move"] / high["tier"]``   → 本场真实最强一击的招式 / 品阶
  - ``war_tier()``                    → 战况档（crush/narrow/sweep/great/lose）
  - ``pup_hits`` / ``res["rounds"]``  → 是否傀儡助阵 / 本场回合数

## 双槽设计（一场最多两句，各走各的门控）
  - **登场口吻**：敌人阵营决定（魂殿/黑盟/魔炎谷才出反派梗），一场最多一句；
  - **结算口吻**：本场招式 / 战况档 / 玩家境界 / 傀儡 / 拖沓 决定，一场最多一句。
  两槽句子来自不同池，不会撞句。这是「梗味更足」与「不刷屏」的平衡。

## 与 doupo_text.py 的边界（勿越）
``doupo_text.py`` 管**逐字原文台词 + 说话人绑定**（CHAPTER_QUOTE / NPC_SPEAKERS）。
本模块**不碰那套**：只出**无主旁白**（不加引号、不冒认角色），符合其硬纪律第 2 条；
只用素材库里**筛掉人名**后的句子；与逐字原文重复的（第 1 章「莫欺少年穷」）不重复出；
技能库不存在的招（「毁灭火莲」）永久剔除；含具体地名的句子（「上云岚宗」）门控复杂、
极易穿帮，**不自动落地**（只在素材库里保留，不做运行时注入）。

## 剔除清单（素材库里没落地的，以及原因）
  - 毁灭火莲 / 火莲变烟花 / 火莲不行上主角挂 —— 技能库无此招，落地即穿帮；
  - 三十年河东莫欺少年穷 —— 已在 CHAPTER_QUOTE 第 1 章逐字出，不重复；
  - 我天妖傀又何尝不是天才 —— 玩家傀儡未必是「天妖傀」，改用泛化句「我又何尝不是天才」；
  - 上云岚宗 / 不把云岚宗灭了 —— 含具体地名，须区域门控且第一人称口吻易穿帮，暂不注入；
  - 你躲开点技能老锁定你 —— 系统没有「目标锁错」事件，无可挂真实字段；
  - 终章已至 / 再见了我的青春 / 莫欺中年穷 —— 情怀收尾，不进普通战斗；
  - 斗气化马 / 三千雷动一动不动 等电视剧吐槽 —— 只做菜单彩蛋，不进正经战报。
"""
from __future__ import annotations

# 敌方阵营：只有这几类「反派势力」才配说反派口吻的梗。
_EVIL_KINDS = frozenset(("魂殿", "黑盟", "魔炎谷"))

# 境界档（cfg.realm_index 返回值）：斗宗=7、半圣=10、斗圣=11、斗帝=12。
_REALM_DOUZONG = 7
_REALM_BANSHENG = 10

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


def _opening(ctx: dict) -> str:
    """登场口吻：敌方真是反派势力才出；普通魔兽/悍匪局绝不冒出反派笑声。"""
    foe_kind = _ctx_str(ctx, "foe_kind")
    seed = _ctx_int(ctx, "seed")
    if foe_kind == "魂殿":
        return _pick(("桀桀桀。", "今日谁保他，谁与我魂殿为敌！",
                      "此子断不可留，否则日后必成大患！"), seed)
    if foe_kind in _EVIL_KINDS:
        return _pick(("猖狂。", "此子断不可留，否则日后必成大患！",
                      "桀桀桀。"), seed)
    return ""


def _closing(ctx: dict) -> str:
    """结算口吻：本场招式 / 战况 / 境界 / 傀儡 / 拖沓，一场最多一句。"""
    win = bool(ctx.get("win"))
    tier = _ctx_str(ctx, "tier")
    realm_idx = _ctx_int(ctx, "realm_idx")
    move = _ctx_str(ctx, "move")
    has_puppet = bool(ctx.get("has_puppet"))
    rounds = _ctx_int(ctx, "rounds")
    seed = _ctx_int(ctx, "seed")

    # 1) 本场真打出的招：最具体，优先于战况/境界。
    if move == "焰分噬浪尺":
        return "焰分噬浪尺！"

    # 2) 越级斩杀：真越级 + 真赢，才配「侥幸 / 撕裂虚空」。
    if win and tier == _TIER_CRUSH:
        return _pick(("侥幸罢了。",
                      "这一击，竟撕裂了虚空。他发出的斗气波动，竟然比我还强。"), seed)

    # 3) 惨胜险胜：血掉一半以下才赢，才有「神清气爽 / 想说一句猖狂」的反差。
    if win and tier == _TIER_NARROW:
        return _pick(("神清气爽。", "怎么很想说一句猖狂。"), seed)

    # 4) 傀儡助阵：本场真有傀儡出手才出召唤物梗。
    if has_puppet:
        return _pick(("平 A 尊者。", "我又何尝不是天才。"), seed)

    # 5) 拖沓局：回合数真的多（≥10）才自嘲「卡点」。
    if rounds >= 10:
        return "别急，我在卡点。"

    # 6) 斗宗及以上境界：作为无主旁白出，低境界绝不出。
    #    放在大捷之前——「恐怖如斯」那批境界句不该被顺风局的普通梗抢走。
    if realm_idx >= _REALM_DOUZONG:
        if realm_idx >= _REALM_BANSHENG:
            return "半圣之下，皆为蝼蚁。"
        return "斗宗强者，恐怖如斯。"

    # 7) 败局：真输了才有轻蔑旁白（不加引号，不冒认角色）。
    if not win:
        return _pick(("无奈地摇了摇头。", "别急，我在卡点。"), seed)

    # 8) 大捷/无伤（仅低境界）：顺风局按种子轻描一句，梗味更足。
    #    句子专属本池，不与傀儡池/惨胜池/越级池复用（避免门控串味）。
    if win and tier in ("great", "sweep"):
        return _pick(("恐怖如斯。", "这一场，赢得干净。"), seed)

    return ""


def flavor_lines(ctx: dict) -> list[str]:
    """返回至多两行梗味旁白：登场口吻 + 结算口吻，各自独立门控。

    任一槽不满足就少一行，绝不用通用句回填（禁兜底）。同 ctx 必得同结果。
    """
    if not isinstance(ctx, dict):
        return []
    # 无战斗数据守卫：一场真实战斗至少有 tier / foe_kind / move / 回合数之一。
    # 全空（如误传空 dict）不该凭 win=False 就吐一句轻蔑——宁可不放。
    if not (_ctx_str(ctx, "tier") or _ctx_str(ctx, "foe_kind")
            or _ctx_str(ctx, "move") or _ctx_int(ctx, "rounds")):
        return []
    out: list[str] = []
    for line in (_opening(ctx), _closing(ctx)):
        if line:
            out.append(line)
    return out


def flavor_line(ctx: dict) -> str:
    """兼容单句入口：返回第一句（登场优先，无登场则结算）；无则空串。"""
    lines = flavor_lines(ctx)
    return lines[0] if lines else ""
