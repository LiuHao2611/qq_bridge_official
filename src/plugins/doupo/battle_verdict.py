# -*- coding: utf-8 -*-
"""斗破 · 对局诊断 + 针对性台词（2026-11-28 用户「要从各个维度去评判，语句一定要多、随机」）。

## 为什么要有这个模块

用户原话：「后台要有一个对局情况分析，准确判断出失败原因或者成功原因，针对性的嘲讽或者吹捧」
「别搞那些莫名其妙的台词硬插入」；追加纠正：「你怎么只把我说到的纬度加上了，
你要从各个维度去评判啊，各种语句一定要多，随机，懂吗」。

第一版只做了用户**点名**的几条（斗气枯竭 / 斗技没满级 / 满级也就这样），一场战斗里
大量真实信号都被浪费了。本版把 ``res`` 与逐条出手记录里能拿到的维度全部用上：

  **败局 15 维**：反伤、毒磨、被控、打不中、对面残血、药白吃、零暴击、只会低阶、
  一招到底、斗气枯竭、斗技没满级、斗技满级但阶位低、身板脆、速败、硬实力差。
  **胜局 15 维**：残血翻盘、中途血线跌落再翻、越级、一击定局、傀儡带飞、暴击串、
  高阶压场、多招轮转、吞噬得手、气运/贵人、靠丹续航、速胜、省气、无伤、平胜。

## 判据全部来自真实 log，不猜

一场战斗的 ``res`` 里躺着逐条出手记录（见 ``battle_core._hit`` / ``fight``）：

  - 玩家出手：``skill``（False=普攻）、``move``、``dmg``、``crit``、``tier``
    （黄/玄/地/天阶，走 ``skill_book_schema.tier_of`` 归三阶）、``mp_cost``、``evaded``；
  - 敌方出手：``taken=True``，``kind`` ∈ 兽击/反伤/中毒/持续伤害，``dot``/``reflect`` 标记；
  - 傀儡出手：``puppet=True``（伤害不算玩家的）；控制：``kind="被控"``；
  - 顶层：``rounds`` / ``taken`` / ``hp_end`` / ``mp_end`` / ``foe_hp_end`` /
    ``exhausted`` / ``auto_pills`` / ``fortune`` / ``hero_aid`` / ``devour``。

诊断只做**计数与比例**，不引入任何新数值、不改战斗结果。

## 台词纪律

  - **胜局赞叹**只放在判词行之后（用户指定位置），旁白口吻、不加引号；
  - **败局嘲讽**只放在战报最后一行，且**由活着的怪开口**（带说话人 + 引号），
    嘲讽必须点名这次的失败点；
  - 每档 6 句，**确定性随机**抽取（同一场回看/回执必须同句，不同场次要不同句）：
    种子混进伤害、暴击、回合、嗑药、出手条数等对局特征，再用 ``random.Random``
    取一条；不是 ``seed % len``——后者会让相邻回合反复抽到同一句。
  - 敌方嘲讽是**本作自编**（用户「你自己多编几句」），**绝不冒充原著台词、不标出处**；
    原著逐字台词仍只走 ``doupo_text.CHAPTER_QUOTE``。
"""
from __future__ import annotations

import random

#: 敌方出手里算「打在我身上」的 kind（dot 单独剔，避免把中毒算成硬碰硬）
_TAKEN_KINDS = frozenset(("兽击", "反伤", "中毒", "持续伤害"))
_DOT_KINDS = frozenset(("中毒", "持续伤害"))

_PLAIN_RATIO_DRY = 0.40      # 普攻占出手多少算「斗气枯竭只能硬凿」
_EVADE_MIN = 3               # 被闪过几次才算「打不中」
_EVADE_RATIO = 0.25
_CTRL_MIN = 2
_SQUISHY_MULT = 1.2          # 承伤 / 自身上限
_REFLECT_SHARE = 0.35
_DOT_SHARE = 0.40            # 毒/持续伤害占总承伤
_CRIT_DRY_MIN = 4            # 出手至少这么多次、一次暴击没有才说「零暴击」
_NEAR_WIN = 0.20             # 对手剩血 <=20% 却输了
_FAST_LOSS = 2               # 回合数 <=2 的速败
_TIER_LOW_MIN = 3            # 至少这么多次技能出手才判「只会低阶」
_ONE_TRICK_MIN = 3
_BURST_SHARE = 0.50
_CRIT_STORM_MIN = 3
_CRIT_STORM_RATIO = 0.40
_VARIETY_MIN = 3             # 用了这么多种斗技才算「多招轮转」
_SPEED_WIN = 3
_FRUGAL_MP = 0.45            # 战后剩余斗气占比
_COMEBACK_FLOOR = 0.30       # 中途血线最低点


def _int(v) -> int:
    try:
        return int(v or 0)
    except (TypeError, ValueError):
        return 0


def _log_of(res: dict) -> list[dict]:
    """整场逐条出手（多敌按序拼接；detail 缺失时回落聚合 log）。"""
    out: list[dict] = []
    for x in (res.get("detail") or []):
        for e in (x.get("log") or []):
            if isinstance(e, dict):
                out.append(e)
    if out:
        return out
    return [e for e in (res.get("log") or []) if isinstance(e, dict)]


def _foe_name(res: dict) -> str:
    """活着的那个怪的名字（败局时 = 最后一场 detail 的对手）。"""
    detail = res.get("detail") or []
    if detail:
        return str(detail[-1].get("name") or "")
    mon = res.get("mon") or {}
    return str(mon.get("name") or "")


def _skill_cap(p: dict, name: str) -> tuple[int, int]:
    """(已练到几级, 该技上限)。查不到阶位上限时上限返回 0（调用方据此闭嘴）。"""
    if not name:
        return 0, 0
    have = _int((p.get("skills") or {}).get(name))
    cap = 0
    try:
        from . import core as _core
        from . import skill_book as _book
    except Exception:  # pragma: no cover - 循环导入保护，正常路径不会走到
        return have, 0
    entry = _book.get(name)
    if entry:
        cap = _int(_core.skill_max(entry))
    return have, cap


def _tier3(rank: str) -> str:
    """斗技全称 → 普通/高阶/特殊。真源走 skill_book_schema，不自己抄映射。"""
    try:
        from . import skill_book_schema as _schema
        return _schema.tier_of(rank)
    except Exception:  # pragma: no cover
        return ""


def analyze(res: dict, p: dict | None = None, lv: int = 0,
            hp_ratio: float | None = None, hp_max: int = 0) -> dict:
    """对局诊断。返回 ``{"win", "case", "foe", "skill", "seed", ...证据字段}``。

    ``hp_ratio`` / ``hp_max`` 由调用点传（战报已经算过同一组数）；不传则涉及气血
    的分支一律不判——宁可少说一句，也不拿错数据下结论。
    """
    p = p or {}
    res = res or {}
    win = bool(res.get("win"))
    log = _log_of(res)
    atk = [e for e in log if "skill" in e and not e.get("puppet")]
    plain = [e for e in atk if not e["skill"]]
    skilled = [e for e in atk if e["skill"]]
    evaded = [e for e in atk if e.get("evaded")]
    crits = [e for e in atk if e.get("crit")]
    my_dmg = sum(_int(e.get("dmg")) for e in atk)
    pup_dmg = sum(_int(e.get("dmg")) for e in log if e.get("puppet"))
    pup_hits = sum(1 for e in log if e.get("puppet"))
    taken = [e for e in log if e.get("taken") and str(e.get("kind") or "") in _TAKEN_KINDS]
    foe_dmg = sum(_int(e.get("dmg")) for e in taken)
    reflect_dmg = sum(_int(e.get("dmg")) for e in taken if e.get("reflect"))
    dots = [e for e in taken if str(e.get("kind") or "") in _DOT_KINDS or e.get("dot")]
    dot_dmg = sum(_int(e.get("dmg")) for e in dots)
    ctrl = sum(1 for e in log if str(e.get("kind") or "") == "被控")
    best_hit = max([_int(e.get("dmg")) for e in atk] or [0])

    # 主力斗技（本场放得最多的那门）；只用真实的出手记录，不读配置猜
    count: dict[str, int] = {}
    for e in skilled:
        nm = str(e.get("move") or e.get("kind") or "")
        if nm and nm != "普攻":
            count[nm] = count.get(nm, 0) + 1
    main = max(count, key=lambda k: count[k]) if count else ""
    lv_have, lv_cap = _skill_cap(p, main)
    tier3 = {_tier3(str(e.get("tier") or "")) for e in skilled}

    n_atk = len(atk)
    plain_ratio = (len(plain) / float(n_atk)) if n_atk else 0.0
    evade_ratio = (len(evaded) / float(n_atk)) if n_atk else 0.0
    crit_ratio = (len(crits) / float(n_atk)) if n_atk else 0.0
    dot_share = (dot_dmg / float(foe_dmg)) if foe_dmg > 0 else 0.0
    reflect_share = (reflect_dmg / float(foe_dmg)) if foe_dmg > 0 else 0.0
    foe_hp = _int(res.get("foe_hp_max"))
    foe_hp_end = _int(res.get("foe_hp_end"))
    foe_left = (foe_hp_end / float(foe_hp)) if foe_hp > 0 else 1.0
    dealt_ratio = ((my_dmg + pup_dmg) / float(foe_hp)) if foe_hp > 0 else 0.0
    mp_end = _int(res.get("mp_end"))
    mp_spent = sum(_int(e.get("mp_cost")) for e in atk)
    mp_max = _int(core_mp_max(p))
    mp_left = (mp_end / float(mp_max)) if mp_max > 0 else 0.0
    low_mp = mp_end <= 0 or bool(res.get("exhausted"))
    foe_top = max([_int(x.get("lv")) for x in (res.get("detail") or [])] or [0])
    ratio = None if hp_ratio is None else max(0.0, min(1.0, float(hp_ratio)))
    hp_marks = [_int(e.get("hp_after")) for e in log if "hp_after" in e]
    hp_floor = (min(hp_marks) / float(hp_max)) if hp_marks and hp_max > 0 else 1.0
    rounds = _int(res.get("rounds"))
    pills = res.get("auto_pills") or {}
    pill_n = _int(pills.get("hp")) + _int(pills.get("mp"))
    n_total = _int(res.get("enemy_count")) or len(res.get("detail") or []) or 1
    killed = sum(1 for x in (res.get("detail") or []) if x.get("win"))

    out = {"win": win, "case": "", "foe": _foe_name(res), "skill": main,
           "skill_lv": lv_have, "skill_cap": lv_cap,
           "plain_ratio": round(plain_ratio, 3),
           "evade_ratio": round(evade_ratio, 3),
           "crit_ratio": round(crit_ratio, 3),
           "dot_share": round(dot_share, 3),
           "reflect_share": round(reflect_share, 3),
           "dealt_ratio": round(dealt_ratio, 3), "best_hit": best_hit,
           "foe_hp": foe_hp, "foe_hp_end": foe_hp_end,
           "foe_left": round(foe_left, 3),
           "foe_dmg": foe_dmg, "reflect_dmg": reflect_dmg, "dot_dmg": dot_dmg,
           "pup_dmg": pup_dmg, "pup_hits": pup_hits, "my_dmg": my_dmg,
           "crit_n": len(crits), "ctrl": ctrl, "pill_n": pill_n,
           "rounds": rounds, "hp_ratio": ratio, "hp_floor": round(hp_floor, 3),
           "mp_end": mp_end, "mp_spent": mp_spent, "mp_left": round(mp_left, 3),
           "skill_variety": len(count), "tier3": sorted(x for x in tier3 if x),
           "devour": bool(res.get("devour")),
           "hero_aid": bool(res.get("hero_aid")),
           "fortune": bool(res.get("fortune")),
           "fire_gain": _int(res.get("fire_energy_gain"))}

    # 确定性随机种子：混进对局本身的差异，避免同章节同等级永远抽同一句。
    # 🚨 必须在下面的提前返回之前算好——调用点无条件读 out["seed"]，
    #    空 res 漏这个键会让整条战报 KeyError（2026-11-28 实测踩到）。
    out["seed"] = (
        rounds * 7919 + my_dmg * 31 + foe_dmg * 17 + len(log) * 13
        + len(crits) * 101 + pill_n * 37 + len(main) * 7 + foe_hp_end * 3
        + int(lv) * 11 + n_total * 23 + killed * 5
    ) & 0x7FFFFFFF

    if not n_atk and not taken:
        # 一条出手记录都没有（空 res / 桩数据）→ 不诊断，宁可不说话
        return out

    if win:
        # 先看「怎么赢的」（过程戏剧性），再看「靠什么赢的」（手段），最后才是平胜。
        if ratio is not None and ratio < 0.2:
            out["case"] = "clutch"
        elif ratio is not None and hp_floor < _COMEBACK_FLOOR:
            out["case"] = "comeback"
        elif lv and foe_top >= int(lv) + 2:
            out["case"] = "crush"
        elif foe_hp > 0 and best_hit >= foe_hp * _BURST_SHARE:
            out["case"] = "burst"
        elif pup_dmg > 0 and pup_dmg > my_dmg:
            out["case"] = "puppet"
        elif len(crits) >= _CRIT_STORM_MIN or (n_atk >= 4 and crit_ratio >= _CRIT_STORM_RATIO):
            out["case"] = "crit_storm"
        elif "高阶" in tier3 or "特殊" in tier3:
            out["case"] = "tier_high"
        elif len(count) >= _VARIETY_MIN:
            out["case"] = "skillful"
        elif res.get("devour"):
            out["case"] = "devour"
        elif res.get("hero_aid") or res.get("fortune"):
            out["case"] = "lucky"
        elif pill_n >= 2:
            out["case"] = "potion"
        elif rounds <= _SPEED_WIN:
            out["case"] = "speed"
        elif mp_spent > 0 and mp_left >= _FRUGAL_MP:
            out["case"] = "frugal"
        elif _int(res.get("taken")) <= 0:
            out["case"] = "clean"
        else:
            out["case"] = "plain"
    else:
        # 败局：先「怎么死的」（防守面），再「为什么打不死」（进攻面），最后才是差距。
        if reflect_share >= _REFLECT_SHARE and foe_dmg > 0:
            out["case"] = "reflect"
        elif dot_share >= _DOT_SHARE and foe_dmg > 0:
            out["case"] = "dot_death"
        elif ctrl >= _CTRL_MIN:
            out["case"] = "controlled"
        elif len(evaded) >= _EVADE_MIN or evade_ratio >= _EVADE_RATIO:
            out["case"] = "dodged"
        elif pill_n >= 2:
            out["case"] = "potion_futile"
        elif plain_ratio >= _PLAIN_RATIO_DRY and low_mp:
            # 「没气硬凿」必须在「零暴击」前判：斗气枯竭的普攻本来就几乎不暴击，
            # 反过来排会被泛化的 crit_dry 抢走用户点名要的那句「多去炼药」。
            out["case"] = "no_mp"
        elif lv_cap > 0 and lv_have < lv_cap:
            out["case"] = "skill_short"
        elif lv_cap > 0 and lv_have >= lv_cap:
            out["case"] = "skill_cap"
        elif len(count) == 1 and sum(count.values()) >= _ONE_TRICK_MIN and \
                "高阶" not in tier3 and "特殊" not in tier3:
            out["case"] = "one_trick"
        elif len(skilled) >= _TIER_LOW_MIN and skilled and \
                all(_tier3(str(e.get("tier") or "")) == "普通" for e in skilled):
            out["case"] = "tier_low"
        elif foe_hp > 0 and foe_left <= _NEAR_WIN:
            # 「差一口气」是情绪，不是可改进的点；必须在没气 / 招式没练够 /
            # 满级阶位低 / 一招到底 / 只会低阶这些**能照着去做**的失败点之后。
            out["case"] = "near_win"
        elif n_atk >= _CRIT_DRY_MIN and not crits:
            out["case"] = "crit_dry"
        elif hp_max > 0 and foe_dmg >= _SQUISHY_MULT * int(hp_max):
            out["case"] = "squishy"
        elif rounds <= _FAST_LOSS:
            out["case"] = "fast_loss"
        else:
            # 前面所有细分都没命中时，最诚实的结论：实力差距。
            out["case"] = "overmatched"

    return out


def core_mp_max(p: dict) -> int:
    """取玩家斗气上限；拿不到就返回 0（调用方据此不判斗气占比）。"""
    try:
        from . import core as _core
        return _int(_core.mp_max(p))
    except Exception:  # pragma: no cover
        return 0


# ---------------------------------------------------------------------------
# 胜局赞叹：判词行之后（用户指定位置），旁白口吻、不加引号。每维 6 句。
# ---------------------------------------------------------------------------
_PRAISE: dict[str, tuple[str, ...]] = {
    "clutch": (
        "只差一线——站到最后的还是他。",
        "血都快流干了，偏偏没倒。",
        "最后一息攥着不放，硬是把这场拽了回来。",
        "命悬一线的时候，他连眼皮都没垂。",
        "所有人都以为结束了，他还站着。",
        "这一场不是赢出来的，是熬出来的。",
    ),
    "comeback": (
        "前半场被压着打，后半场翻了过来。",
        "血线跌到谷底那一刻，谁都没想到结局。",
        "先亏后赚，他把局面一寸寸掰了回来。",
        "被逼到墙角的人，反而打得更狠。",
        "起手难看，收尾漂亮。",
        "跌下去那么深，还能爬起来赢——这才难。",
    ),
    "crush": (
        "越级而斩，这一手不像这个境界该有的。",
        "对面等级压着他，他倒压着对面打。",
        "境界差了一截，胜负却反着写。",
        "越阶出手还能这么稳，不像寻常人。",
        "本该仰着头看，结果他低头看了对方一眼。",
        "差着等级的仗，被他打成了单方面。",
    ),
    "burst": (
        "一击定局，其余都是多余的。",
        "这一下，直接把话说完。",
        "前面都是铺垫，真正的一招在最后。",
        "出手只有一次，结果已经不用看了。",
        "那一击落下，对手连反应的余地都没有。",
        "他没打算打持久，一招就够了。",
    ),
    "puppet": (
        "那具傀儡，比他本人还狠。",
        "人还没发力，傀儡先把场面收了。",
        "这场的主角，恐怕是那具傀儡。",
        "他在后面看着，傀儡在前头把事办了。",
        "傀儡的拳头，比他自己的斗气还争气。",
        "养兵千日，这一场全靠它。",
    ),
    "crit_storm": (
        "一下比一下狠，暴击接连炸开。",
        "手气旺得不像话，招招都打在要害。",
        "这场他的斗气像是长了眼睛。",
        "接连几下重击，对面连喘气的节奏都乱了。",
        "运气来了挡不住，要害一个接一个。",
        "暴击串成一条线，看得人头皮发麻。",
    ),
    "tier_high": (
        "高阶斗技压下来，场面从一开始就不一样。",
        "一出手就是高阶，格局摆在那儿。",
        "阶位压人，这话今天应验了。",
        "低阶斗技打的是力气，他打的是品阶。",
        "那门高阶一亮，胜负就已经定了大半。",
        "境界之上的手段，终究不是寻常货色。",
    ),
    "skillful": (
        "一门接一门，招式换得比对手反应还快。",
        "手里有几门真本事，轮着用就是不一样。",
        "不是一招吃遍天，这场他打得花样百出。",
        "招式一套接一套，对面根本摸不清套路。",
        "会得多，就能在关键时刻掏出对的那一个。",
        "他不像只会一招的人——每一手都有后手。",
    ),
    "devour": (
        "吞噬得手，气机又壮了一分。",
        "对手的力道，最后成了他的补益。",
        "吞下那一口，局面立刻翻了个方向。",
        "他把敌人的气势，硬生生炼成了自己的。",
        "这一吞，赚的不只是血量。",
        "最狠的不是打垮对手，是把对手吸干。",
    ),
    "lucky": (
        "天也在帮他。",
        "气机自己撞上来，赢了。",
        "贵人搭了一把手，路就顺了。",
        "机缘这种东西，偏偏让他赶上了。",
        "有几分运气，但接得住才算本事。",
        "风往他那边吹，他也确实站对了位置。",
    ),
    "potion": (
        "靠丹续下来的命，也是本事。",
        "药罐子，也赢了。",
        "丹药没白吃，每一炉都换成了活下去的机会。",
        "别人靠斗气，他靠一整袋丹撑到了最后。",
        "会炼丹的人，连命都比别人多几条。",
        "药能救命，也能赢仗——今天两样都占了。",
    ),
    "speed": (
        "几回合就收了，干净得很。",
        "出手不多，结束得也不拖沓。",
        "快得对面还没站稳，事情就完了。",
        "这场没有拖泥带水，三两下见分晓。",
        "速战速决，他最擅长这个。",
        "还没看清楚怎么打的，已经赢了。",
    ),
    "frugal": (
        "斗气没怎么耗，事却办成了。",
        "气机留着一大半，仗已经打完。",
        "省着用也能赢，这才是真本事。",
        "不靠蛮力堆伤害，掐得刚刚好。",
        "每一分斗气都花在刀刃上。",
        "打完还有余力，跟没打过似的。",
    ),
    "clean": (
        "干净利落，一根头发都没掉。",
        "连衣角都没让人碰到。",
        "从头到尾没挨一下，赢得像散步。",
        "对面连他的边都没沾着。",
        "血没掉一滴，气势倒涨了不少。",
        "这一场，赢得连灰尘都没扬起来。",
    ),
    "plain": (
        "赢了就是赢了。",
        "这一场，赢得干净。",
        "没有戏文里的曲折，该赢就赢了。",
        "一板一眼打下来，结果没出意外。",
        "不必吹得天花乱坠，胜了就是胜了。",
        "稳稳当当，拿下。",
    ),
}

# ---------------------------------------------------------------------------
# 败局嘲讽：战报最后一行，活着的怪开口（{foe} 说话人、{skill} 本场主力斗技）。
# 每维 6 句；{skill} 只出现在真有主力技的档，避免渲染出「『』」空引号。
# ---------------------------------------------------------------------------
_TAUNT: dict[str, tuple[str, ...]] = {
    "reflect": (
        "{foe}负手而立：「我可没动手——是你自己的力道把自己震死的。」",
        "{foe}慢悠悠地拍了拍胸口：「疼么？这叫以彼之道，还施彼身。」",
        "{foe}咧嘴：「你越用力，我越省事。接着来？」",
        "{foe}蹲下来看他：「把自己撞成这样，我都不好意思说是我赢的。」",
        "{foe}收了爪子：「斗气越猛，反噬越狠——这道理没人教过你？」",
        "{foe}叹了口气：「下回出手前，先想想自己的身子骨受不受得住。」",
    ),
    "dot_death": (
        "{foe}晃了晃尾巴：「毒早钻进你经脉了，你还在硬撑什么？」",
        "{foe}舔了舔嘴唇：「不必动手，等一等，你自己就倒下了。」",
        "{foe}慢条斯理：「血一点点凉下去的滋味，比一刀痛快如何？」",
        "{foe}眯起眼：「我连牙都懒得露——毒替我把活干完了。」",
        "{foe}歪头看他：「你以为是打输了？其实是熬输了。」",
        "{foe}嗅了嗅空气：「这股药味，现在全是你自己的了。」",
    ),
    "controlled": (
        "{foe}俯视着你：「被钉在原地的滋味，好受么？」",
        "{foe}绕着他走了一圈：「动不了的感觉，是不是很新奇？」",
        "{foe}伸爪按住他的肩：「别急，我没有那么快——你有的是时间慢慢想。」",
        "{foe}轻笑：「你那一身本事，被定住的时候一点用都没有。」",
        "{foe}收起杀意：「连抬手都做不到，还谈什么翻盘。」",
        "{foe}蹲下身：「记住这个感觉，下次别让自己停在原地。」",
    ),
    "dodged": (
        "{foe}轻轻一扭就避开了：「连我衣角都摸不到，你打谁呢？」",
        "{foe}嗤笑：「拳头挥得挺响，可惜全是空气。」",
        "{foe}原地转了个身：「你打了这么久，碰到我一根毛没有？」",
        "{foe}歪头躲过最后一击：「慢半拍，就差了整整一场。」",
        "{foe}懒洋洋地侧身：「你追得上我的影子，再说赢我的话。」",
        "{foe}拍掉身上并不存在的灰：「全是空招，看着还挺热闹。」",
    ),
    "near_win": (
        "{foe}喘着气，却还站着：「就差这么一点——你偏偏没有那一点。」",
        "{foe}低头看了看自己的伤口：「我快倒了，可惜，你比我先。」",
        "{foe}咬牙：「别不甘心，活到最后的人才有资格说话。」",
        "{foe}靠着石头站稳：「你把我打到了门口，却没能把我推出去。」",
        "{foe}缓缓抬爪：「差一口气，就是差一整个境界。」",
        "{foe}擦掉嘴角的血：「我认你这一身狠劲，但不认你输了还能嘴硬。」",
    ),
    "potion_futile": (
        "{foe}看了看他空掉的药瓶：「药当水喝，也救不回这条命。」",
        "{foe}嗤笑：「丹药是给你续命的，不是给你送死的。」",
        "{foe}踢了踢地上的空瓶：「这么些丹，全喂了这场败仗。」",
        "{foe}摇摇头：「靠药撑着打，药一停你就散了。」",
        "{foe}嗅了嗅残余药气：「好大的药味——可惜没有一分变成胜算。」",
        "{foe}冷冷道：「下一回，把炼药的功夫分一半到斗技上。」",
    ),
    "crit_dry": (
        "{foe}拍了拍衣裳：「打了这么久，一下暴击都没有——运气全用在挨打上了？」",
        "{foe}眯眼：「你那点力气，平平整整，像在给我挠痒。」",
        "{foe}咧嘴：「一次都没打中要害，你说你是不是在让着我？」",
        "{foe}歪头：「斗气不是省着攒的，是打出来的——你倒好，一下都不肯爆。」",
        "{foe}轻蔑地哼了一声：「连个爆点都没有，这场赢得也太省力了。」",
        "{foe}慢悠悠：「下回别指望运气，先把斗技练出狠劲。」",
    ),
    "tier_low": (
        "{foe}抖了抖毛：「翻来覆去就那几门低阶招式，你也不嫌腻？」",
        "{foe}嗤笑：「黄阶的玩意儿也想压我？回去翻翻斗技阁吧。」",
        "{foe}随手挡下：「这点阶位，我闭着眼都接得住。」",
        "{foe}摇头：「招不差，可惜一开始就输了阶位。」",
        "{foe}冷声：「低阶斗技打到天亮，也还是低阶。」",
        "{foe}俯身：「手里就没有一门拿得出手的高阶？」",
    ),
    "one_trick": (
        "{foe}学着他刚才的姿势比划了一下：「你就会这一招？」",
        "{foe}嗤笑：「同一招放了一整场，我都会背了。」",
        "{foe}无聊地打了个哈欠：「下一招呢？没有？那就是没有了。」",
        "{foe}摇头：「把一门招练到老，不如多学两门保命的。」",
        "{foe}侧身躲过又一遍熟悉的招式：「这招我比你熟。」",
        "{foe}认真起来：「敬畏点对手——别拿一招走天下。」",
    ),
    "no_mp": (
        "{foe}嗤笑：「斗气都见底了还硬撑？就这点家底，也敢来叫嚣。」",
        "{foe}抖了抖爪子：「拳头砸了半晌，挠痒呢？回去多炼几炉丹，先把气养足。」",
        "{foe}斜眼：「连斗技都放不出来，你拿什么跟我打？」",
        "{foe}漫不经心：「气都没了还往前冲，是勇敢还是不清楚自己几斤几两？」",
        "{foe}低头看了看他空荡荡的气机：「斗气养到这个份上，也该回去重修了。」",
        "{foe}冷哼：「靠两条胳膊硬凿，你当自己是炼体修士？」",
    ),
    "skill_short": (
        "{foe}侧身让过：「你那手『{skill}』，练得跟没练一样。」",
        "{foe}嗤笑：「『{skill}』这点火候也敢拿出来？再多喂几炉焚血散吧。」",
        "{foe}摇头：「招式不差，是你使不出来。」",
        "{foe}漫不经心：「『{skill}』的劲道还差得远，练到骨头里再来。」",
        "{foe}轻笑：「你把『{skill}』当试探，我可没把你当对手。」",
        "{foe}蹲下身：「回去把『{skill}』再练三百遍，别急着出门。」",
    ),
    "skill_cap": (
        "{foe}大笑：「『{skill}』练到顶了？就这阶位的破技，练到天上也就这样。」",
        "{foe}冷冷道：「招是好招，可惜阶位太低，压不住我。换本高阶的再来。」",
        "{foe}拍了拍身上的灰：「满级又怎样？你还是打不动我。」",
        "{foe}嗤笑：「练满是好事，可惜练的是一门天生的下风招。」",
        "{foe}摇摇头：「你把一门技练到尽头，却发现尽头就这么高——难受吧？」",
        "{foe}俯视：「不是你不努力，是这招的上限就在那儿。」",
    ),
    "squishy": (
        "{foe}舔了舔牙：「皮这么薄，一巴掌就没了，也敢进山？」",
        "{foe}冷笑：「气血比纸还薄，回去先把身子练结实。」",
        "{foe}收住爪子：「我都没用力，你怎么就倒了？」",
        "{foe}嗤笑：「斗技练得再花，身子一碰就碎，有什么用。」",
        "{foe}低头看他：「你这副身板，适合在药堂待着，不适合在山里。」",
        "{foe}漫不经心：「别怪我下手重，是你太不经打。」",
    ),
    "fast_loss": (
        "{foe}伸了个懒腰：「这么快？我还没热完身。」",
        "{foe}看了看自己的爪子：「两下就完了？我都没来得及认真。」",
        "{foe}打了个哈欠：「连回合都没凑够，回去练练再来。」",
        "{foe}嗤笑：「你倒下得比我想的还快，我都准备好久战了。」",
        "{foe}摇摇头：「热身都没结束，人就躺下了。」",
        "{foe}慢慢走过来：「别急，下一次多撑两回合，算是给我面子。」",
    ),
    "overmatched": (
        "{foe}连正眼都懒得给：「回去再练十年。」",
        "{foe}转身就走：「不够看。」",
        "{foe}低头看了他一眼：「差距不在这一招一式，在整个根基。」",
        "{foe}嗤笑：「你不是输给我，是输给还没到的那个境界。」",
        "{foe}抖了抖身上的毛：「下次挑个跟你同级的对手，起码能体面点。」",
        "{foe}没回头：「别把不甘心当本事，那不值钱。」",
    ),
}


def _pick(pool: tuple[str, ...], seed: int) -> str:
    """确定性随机：同一场同句（回看/回执一致），不同场次抽到不同句。

    🚨 不用 ``seed % len(pool)``：那会让种子按回合数递增时反复抽同一句，
    玩家看到的「随机」就变成几场一循环。这里用独立 PRNG 取一条。
    """
    if not pool:
        return ""
    if len(pool) == 1:
        return pool[0]
    rng = random.Random((int(seed) ^ 0x9E3779B9) & 0x7FFFFFFF)
    return pool[rng.randrange(len(pool))]


def praise(case: str, seed: int = 0) -> str:
    """胜局赞叹一句（判词行之后）。未知 case 返回空串，不拿通用句凑。"""
    return _pick(_PRAISE.get(str(case or "")), seed)


def taunt(case: str, seed: int = 0, foe: str = "", skill: str = "") -> str:
    """败局嘲讽一句（战报最后一行）。缺说话人时返回空串——不冒认谁在说话。"""
    if not foe:
        return ""
    tpl = _pick(_TAUNT.get(str(case or "")), seed)
    if not tpl:
        return ""
    return tpl.replace("{foe}", foe).replace("{skill}", str(skill or ""))
