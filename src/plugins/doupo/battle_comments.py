# -*- coding: utf-8 -*-
"""斗破 · 战斗评价毒舌评语库（2026-11-17）。

## 调研与合规
素材来自 2026-11-17 对游戏社区吐槽语的检索：
* 微信公众号《20 条攻击力超强的王者怼人语录》——「输出像签了保密协议」「战绩替你发言」；
* 什么梗百科 / 新词百科《一顿操作猛如虎》——「一顿操作猛如虎，一看战绩零杠五」；
* NGA《鸟德改了个寂寞！》——「一顿操作猛如虎，一看伤害还是二百五」；
* 游戏社区常见「打了个寂寞」「像天气预报」比喻。

**入库文本全部二次原创/改写**，只嘲讽战斗表现（伤害、斗气、回合、血量、暴击、
闪避、机缘），不Attack真实玩家，不带人身攻击、地域/性别/职业标签。每条保留
``source`` 作为灵感溯源，避免大段照搬造成合规风险；只攻击战斗表现，不攻击真实玩家。

## 选择规则
标签来自 battle_review.tags(last_battle)。用 openid + ts + log 长度做种子，
同一场战斗反复点击评价结果稳定；约 1/4 概率出长评，其余为短评。
"""
from __future__ import annotations

import hashlib

from . import battle_review as br

_SRC_WECHAT = "微信公众号《20条攻击力超强的王者怼人语录》句式改写"
_SRC_MEME = "《一顿操作猛如虎》网络梗反差节奏改写"
_SRC_NGA = "NGA 战斗吐槽「操作与结果反差」方向改写"
_SRC_LOCAL = "斗破战斗系统数值语境原创"

#: 短评（一句收工）
SHORT = (
    {"tag": "low_dmg", "text": "这伤害挺保密的，敌人生死全靠猜。", "source": _SRC_WECHAT},
    {"tag": "low_dmg", "text": "你的输出像天气预报，报得热闹，落不到实处。", "source": _SRC_LOCAL},
    {"tag": "low_dmg", "text": "打了个寂寞，蚊子看了都要写感谢信。", "source": _SRC_MEME},
    {"tag": "no_mp", "text": "斗气一分不花，打算靠意念把敌人劝退吗？", "source": _SRC_LOCAL},
    {"tag": "low_mp", "text": "斗气省成传家宝，临门一脚才发现没带钥匙。", "source": _SRC_LOCAL},
    {"tag": "high_taken", "text": "血条是租来的吧，掉得比借的还快。", "source": _SRC_LOCAL},
    {"tag": "many_rounds", "text": "十几回合过去，你和对面像是先约好了熬鹰。", "source": _SRC_MEME},
    {"tag": "crit", "text": "暴击一次，剩下全是气氛组。", "source": _SRC_NGA},
    {"tag": "miss", "text": "闪避倒是挺积极，攻击像临时请假。", "source": _SRC_LOCAL},
    {"tag": "control", "text": "被控得这么安详，敌人都省了控制链。", "source": _SRC_LOCAL},
    {"tag": "dot", "text": "余威烧了半天，主伤害还在路上。", "source": _SRC_LOCAL},
    {"tag": "reflect", "text": "反伤打得比你主动出手还像样。", "source": _SRC_LOCAL},
    {"tag": "aid", "text": "贵人一招收工，你在旁边负责鼓掌是吧。", "source": _SRC_LOCAL},
    {"tag": "lose", "text": "战绩已经替你发言了，它说的是「再来」。", "source": _SRC_WECHAT},
    {"tag": "lose", "text": "你这波不是输，是给敌人做了场热身。", "source": _SRC_WECHAT},
    {"tag": "win", "text": "赢是赢了，就是赢得有点像路过捡的。", "source": _SRC_LOCAL},
    {"tag": "win", "text": "别急着嚣张，对面可能只是让你三招。", "source": _SRC_MEME},
    {"tag": "general", "text": "一顿操作猛如虎，一看伤害二百五。", "source": _SRC_NGA},
    {"tag": "general", "text": "这战斗看得人很安静，安静地想问伤害呢。", "source": _SRC_LOCAL},
    {"tag": "general", "text": "打得挺忙，战果挺闲。", "source": _SRC_LOCAL},
    {"tag": "general", "text": "你的战报像账本，翻开全是没有兑现的承诺。", "source": _SRC_WECHAT},
    {"tag": "general", "text": "别的不说，回合数倒是撑得挺体面。", "source": _SRC_MEME},
    {"tag": "general", "text": "这一场，敌人赢得都不太有成就感。", "source": _SRC_WECHAT},
    {"tag": "general", "text": "你不是在打架，你是在给战斗日志凑字数。", "source": _SRC_LOCAL},
)

#: 长评（偶尔放一段）
LONG = (
    {"tag": "low_dmg",
     "text": "一顿操作猛如虎，一看伤害二百五。斗气烧得挺热闹，血条却像没收到通知；建议下次把招式名喊得再响一点，至少气势能先结算。",
     "source": _SRC_NGA},
    {"tag": "low_dmg",
     "text": "你的输出像签了保密协议，全程不透露伤害；敌人站在原地等你把回合数凑够，场面一度非常礼貌。",
     "source": _SRC_WECHAT},
    {"tag": "no_mp",
     "text": "整场斗气颗粒未动，把蓝条守得比祖传玉玺还严。问题是敌人不看藏品，只看你打不打得动；这一战属于省到了点子上，也输在了点子上。",
     "source": _SRC_LOCAL},
    {"tag": "low_mp",
     "text": "斗气见底才想起技能栏里还有四门斗技，这就好比饭端上桌才发现没带筷子。省是省了，代价是输出全程在旁边看热闹。",
     "source": _SRC_LOCAL},
    {"tag": "high_taken",
     "text": "血条掉得像月初的余额，防御减伤努力得像在给瀑布盖保鲜膜。你确实一直在接招，只是接的方式比较像全盘照收。",
     "source": _SRC_LOCAL},
    {"tag": "many_rounds",
     "text": "十几回合打下来，敌人都没急着收工，可能也在观察你到底准备出什么绝招。结果观察完发现，是回合数在出招，你只是负责见证。",
     "source": _SRC_MEME},
    {"tag": "crit",
     "text": "全场就那一下暴击像样，剩下几十次出手全是陪跑。要夸也能夸：至少你证明了自己偶尔能打中，而且没把这件事做成日常。",
     "source": _SRC_NGA},
    {"tag": "miss",
     "text": "你的闪避很积极，攻击很害羞。敌人一挥手你躲得比谁都快，轮到你出手时却像刚想起今天没带武器；这叫战术性低调，还是进攻性失踪？",
     "source": _SRC_LOCAL},
    {"tag": "control",
     "text": "被控住的那一刻，全场都安静了——不是因为惊讶，是因为终于有人替你把节奏停下来了。你站在原地，像在等系统提示「是否继续」。",
     "source": _SRC_LOCAL},
    {"tag": "dot",
     "text": "持续伤害烧了半天，主伤害像还在路上堵车。敌人看着那点余威，估计也在思考：这点火是来收命的，还是来暖场的？",
     "source": _SRC_LOCAL},
    {"tag": "reflect",
     "text": "反伤比主动输出还像回事，敌人打你一下，回头看自己血条，可能比你还委屈。建议保持，毕竟这是你全场最有存在感的战斗机制。",
     "source": _SRC_LOCAL},
    {"tag": "aid",
     "text": "贵人破空而至，一招把敌人送走，你在旁边连招式都还没热完。机缘确实是属性，但这一场的实际输出归属，战报比谁都清楚。",
     "source": _SRC_LOCAL},
    {"tag": "lose",
     "text": "你这波不是简单的失败，是把自己打成了敌人的训练关卡。对方回去复盘都能写三条经验：第一，别急；第二，等他出完；第三，收工。",
     "source": _SRC_WECHAT},
    {"tag": "win",
     "text": "赢是赢了，但赢得像在路边捡到一枚金币：过程不华丽，结果很实在。敌人倒下前大概还在想，刚才那几下到底是招式还是误触。",
     "source": _SRC_MEME},
    {"tag": "general",
     "text": "这份战报信息量很大：招式名一个比一个响，伤害数字一个比一个谦逊。你不是在打架，你是在用行动证明，气势和实际输出可以是两条独立赛道。",
     "source": _SRC_NGA},
    {"tag": "general",
     "text": "看完整场，最忙的是日志，最闲的是敌人血量。你把每个回合都填满了，唯独没把伤害填满；下次试试让招式名少说两句，让血条多说两句。",
     "source": _SRC_WECHAT},
)


def _choose(pool: tuple[dict, ...], tags: list[str], seed: int) -> dict:
    tagged = [x for x in pool if x["tag"] in tags]
    general = [x for x in pool if x["tag"] == "general"]
    candidates = tagged or general or pool
    return candidates[seed % len(candidates)]


def pick(battle: dict, who: str = "", force_length: str | None = None) -> str:
    """按最后一场战斗选一句评语；同一场 + 同一用户结果稳定。

    :param force_length: 测试用，``short``/``long``；正常路径留空由种子决定。
    """
    battle = battle or {}
    raw = "|".join([str(who or ""), str(battle.get("ts") or ""),
                    str(battle.get("kind") or ""),
                    str(len(battle.get("log") or []))])
    seed = int(hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12], 16)
    length = force_length or ("long" if seed % 4 == 0 else "short")
    tags = br.tags(battle)
    item = _choose(LONG if length == "long" else SHORT, tags, seed)
    return f"【战斗评价】{item['text']}"


def all_texts() -> tuple[str, ...]:
    """所有入库文本（测试守门用，避免空库/重复）。"""
    return tuple(x["text"] for x in SHORT + LONG)
