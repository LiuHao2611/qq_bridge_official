# -*- coding: utf-8 -*-
"""斗破 · 战斗点评毒舌评语库（2026-11-17 建库，2026-11-23 全量换皮）。

## 2026-11-23 全量换皮（用户拍板「全改」）
旧版素材来自游戏社区网络梗（「一顿操作猛如虎」「打了个寂寞」「战绩替你发言」等），
玩家反馈完全跳出斗气大陆语境。本版把整池改成**药老点评式毒舌**：
* 语气保留毒舌，但只用斗气大陆语汇（斗气 / 丹田 / 气旋 / 斗技 / 经脉 / 招式 / 血条）；
* 只嘲讽战斗表现（伤害、斗气、回合、暴击、闪避、机缘），不攻击真实玩家；
* 不带人身攻击、地域/性别/职业标签，不带网络流行梗。

## 选择规则（未改）
标签来自 battle_review.tags(last_battle)。用 openid + ts + log 长度做种子，
同一场战斗反复点击评价结果稳定；约 1/4 概率出长评，其余为短评。
"""
from __future__ import annotations

import hashlib

from . import battle_review as br

_SRC_LOCAL = "药老点评式毒舌 · 斗气大陆语境原创"
_SRC_WECHAT = "药老点评式毒舌 · 语汇换皮"
_SRC_MEME = "药老点评式毒舌 · 反差节奏改写"
_SRC_NGA = "药老点评式毒舌 · 招式与结果反差改写"

#: 短评（一句收工）
SHORT = (
    {"tag": "low_dmg", "text": "这伤害藏得比密函还紧，敌人站着挨完也没见掉多少。", "source": _SRC_WECHAT},
    {"tag": "low_dmg", "text": "斗气催得热闹，落到敌人身上却轻得像拂尘扫过。", "source": _SRC_LOCAL},
    {"tag": "low_dmg", "text": "招式喊得响亮，敌人衣角都没碰到。", "source": _SRC_MEME},
    {"tag": "no_mp", "text": "斗气一丝未动，莫非想凭气势把敌人劝退？", "source": _SRC_LOCAL},
    {"tag": "low_mp", "text": "斗气省成传家宝，临到要害才发现没带钥匙。", "source": _SRC_LOCAL},
    {"tag": "high_taken", "text": "血条掉得比瀑布还急，防御减伤像是摆设。", "source": _SRC_LOCAL},
    {"tag": "many_rounds", "text": "十几回合过去，你和对面倒像先约好了熬鹰。", "source": _SRC_MEME},
    {"tag": "crit", "text": "全场就那一下暴击像样，其余全是凑数的招。", "source": _SRC_NGA},
    {"tag": "miss", "text": "闪避倒是勤快，轮到出手时人却不在场。", "source": _SRC_LOCAL},
    {"tag": "control", "text": "被控得那般安详，敌人都省了后手。", "source": _SRC_LOCAL},
    {"tag": "dot", "text": "余威烧了半天，主伤害还在路上。", "source": _SRC_LOCAL},
    {"tag": "reflect", "text": "反伤比主动出手还争气，敌人见你都得多看两眼。", "source": _SRC_LOCAL},
    {"tag": "aid", "text": "贵人一招收工，你在旁边连斗气都没热起来。", "source": _SRC_LOCAL},
    {"tag": "lose", "text": "这战绩不必多言——再来一局便是。", "source": _SRC_WECHAT},
    {"tag": "lose", "text": "这一场，说是给敌人热身也不为过。", "source": _SRC_WECHAT},
    {"tag": "win", "text": "赢是赢了，只是赢得像途中顺手捡的。", "source": _SRC_LOCAL},
    {"tag": "win", "text": "别急着张扬，对面许是让你三招。", "source": _SRC_MEME},
    {"tag": "general", "text": "招式一个比一个响，伤害数字一个比一个谦逊。", "source": _SRC_NGA},
    {"tag": "general", "text": "这一战看得人安静，安静地想问伤害何在。", "source": _SRC_LOCAL},
    {"tag": "general", "text": "打得挺忙，战果挺闲。", "source": _SRC_LOCAL},
    {"tag": "general", "text": "战报翻开尽是承诺，没有一条兑现。", "source": _SRC_WECHAT},
    {"tag": "general", "text": "别的不说，回合数倒是撑得体面。", "source": _SRC_MEME},
    {"tag": "general", "text": "这一场，敌人赢得都没什么成就感。", "source": _SRC_WECHAT},
    {"tag": "general", "text": "你不是在交手，是在给战斗日志添字数。", "source": _SRC_LOCAL},
)

#: 长评（偶尔放一段）
LONG = (
    {"tag": "low_dmg",
     "text": "招式名喊得震天响，落下来却轻得像拂尘扫过——敌人站在原地，斗气丝毫未损。建议下回把气沉丹田，别把力气全花在嗓门上。",
     "source": _SRC_NGA},
    {"tag": "low_dmg",
     "text": "这一场伤害藏得极深，翻遍战报也寻不见几处像样的落点。敌人倒是很沉得住气，站那儿由你把回合数凑够，场面一度十分客气。",
     "source": _SRC_WECHAT},
    {"tag": "no_mp",
     "text": "整场斗气颗粒未动，把丹田守得比藏宝阁还严。可敌人不看你的存货，只看你打不打得动——这一战，省是真省了，输也是真输了。",
     "source": _SRC_LOCAL},
    {"tag": "low_mp",
     "text": "斗气见底才想起斗技栏里还有几门功夫，这就好比药已备齐才发现炉子没生火。省是省了，代价是输出全程在旁观赏。",
     "source": _SRC_LOCAL},
    {"tag": "high_taken",
     "text": "血条掉得跟开闸似的，防御减伤使了全力，却像拿斗篷去接瀑布。你确实一直在接招，只是接的法子比较——全盘照收。",
     "source": _SRC_LOCAL},
    {"tag": "many_rounds",
     "text": "十几回合缠斗下来，敌人都不急着收工，八成是在等你亮出压箱底的斗技。等了半天才明白，出招的是回合，你只负责在旁边见证。",
     "source": _SRC_MEME},
    {"tag": "crit",
     "text": "全场就那一下暴击撑了门面，其余几十次出手全在陪衬。要夸也夸得出口：至少你证明过自己能打中要害，只是没把这事当成日常。",
     "source": _SRC_NGA},
    {"tag": "miss",
     "text": "你的身法躲得极勤，出手却似羞于见人。敌人一挥手你便闪得没影，轮到你进攻，倒像临时忘了兵器搁在哪儿——这身法，是拿来走位的还是拿来避战的？",
     "source": _SRC_LOCAL},
    {"tag": "control",
     "text": "被控住那一下，全场都静了——不是惊讶，是终于有人替你把节奏按住。你立在原地纹丝不动，像在等一句「是否继续」。",
     "source": _SRC_LOCAL},
    {"tag": "dot",
     "text": "余威烧了半晌，主伤害还堵在半道上。敌人盯着那点火光，估计也在琢磨：这是来收命的，还是来暖炉子的？",
     "source": _SRC_LOCAL},
    {"tag": "reflect",
     "text": "反伤比主动出手还像回事，敌人打你一下，回头瞧瞧自己血条，兴许比你更委屈。这路子不妨留着，毕竟是你全场最有存在感的门道。",
     "source": _SRC_LOCAL},
    {"tag": "aid",
     "text": "贵人破空而来，一招便把敌人送走，你这边招式还没热透。机缘终究是外物，这一场的胜负归属，战报比谁都清楚。",
     "source": _SRC_LOCAL},
    {"tag": "lose",
     "text": "这一场不是简单的落败，是把自己打成了敌人的试招木桩。对方回去复盘都能记上三条：其一别急，其二等他出完，其三收工。",
     "source": _SRC_WECHAT},
    {"tag": "win",
     "text": "赢是赢了，像在路边顺手拾了一枚金币——过程不华丽，结果倒实在。敌人倒下前怕还在琢磨，方才那几下究竟是斗技还是手滑。",
     "source": _SRC_MEME},
    {"tag": "general",
     "text": "这份战报信息量极足：招式一个比一个响亮，伤害一个比一个含蓄。你不是在交手，是在用行动证明——声势和实际斗气，原是两条互不相干的道。",
     "source": _SRC_NGA},
    {"tag": "general",
     "text": "看完整场，最忙的是战报，最闲的是敌人血条。你把每个回合都填得满满当当，唯独没把伤害填上；下回少喊两嗓子招式名，让斗气多说两句。",
     "source": _SRC_WECHAT},
)


def _choose(pool: tuple[dict, ...], tags: list[str], seed: int) -> dict:
    tagged = [x for x in pool if x["tag"] in tags]
    general = [x for x in pool if x["tag"] == "general"]
    candidates = tagged or general or pool
    return candidates[seed % len(candidates)]


def pick(battle: dict, who: str = "", force_length: str | None = None) -> str:
    """按最后一场战斗选一句点评；同一场 + 同一用户结果稳定。

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
