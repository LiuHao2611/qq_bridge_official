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
    原著逐字台词仍只走 ``doupo_text.CHAPTER_QUOTE``；
  - 但**口吻必须像小说里当场说的话**（用户 2026-11-28 三次纠正：「你这个台词太 AI 了，
    完全没有厮杀的氛围，你去学学小说里怎么说的吧，这个完全不能用」）。
    风格真源 = 已逐字核对的 ``boss_taunts.py`` / ``config.BREAK_BOSSES`` /
    ``doupo_text.CHAPTER_QUOTE``：短句、当场、带杀气、带身份，绝不写「战后点评」。
    骂阵 ≤24 字，旁白 ≤28 字且只写画面/反应；魔兽用龇牙低吼，人型才用桀桀/抽刀。
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
    detail_last = (res.get("detail") or [{}])[-1] or {}
    mon_info = detail_last or (res.get("mon") or {})
    foe_kind = str(mon_info.get("kind") or "")
    try:
        from . import config as _cfg
        foe_beast = foe_kind == "魔兽" or any(
            str(m.get("name") or "") == _foe_name(res) for m in _cfg.MONSTERS)
    except Exception:  # pragma: no cover
        foe_beast = foe_kind == "魔兽"
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

    out = {"win": win, "case": "", "foe": _foe_name(res), "foe_kind": foe_kind,
           "foe_beast": foe_beast, "skill": main,
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
# 台词风格真源（2026-11-28 用户「太 AI 了，没有厮杀的氛围，去学学小说里怎么说的」）
# ---------------------------------------------------------------------------
# 项目里已有逐字核对过的原著语料：boss_taunts.py、config.BREAK_BOSSES、
# doupo_text.CHAPTER_QUOTE、docs/战斗BOSS体系重做_原著考据_2026-11-20.md。
# 里面的人物当场说话是这样的：
#   「桀桀，我魂殿可有的是手段收拾你」「在绝对的实力面前，这些都没有用」
#   「我就不信，你这乌龟壳还真的打不烂！」「既然你们找死，那本王就成全你们，一群蝼蚁！」
#   「你才是大鱼」「生死，各安天命」「这笔账，不得不算」
# 共同点：**当场开口、短、狠、带杀气**；说话人带着自己的身份（老夫/本王/我魂殿）。
#
# 🚨 禁写「战后点评」：他不像只会一招的人 / 练满是好事可惜阶位低 /
#    这就是真本事 / 道理没人教过你 / 场面从一开始就不一样……
#    那是战报评语，不是厮杀对白，用户明确判为「完全不能用」。
# 🚨 魔兽不「负手而立」：兽类用龇牙/低吼/甩尾，人型才用冷笑/桀桀/抽刀。
# ---------------------------------------------------------------------------

#: 胜局赞叹：判词行之后的**现场旁白**（不加引号）。只写看得见的画面与反应，
#: 不写道理、不写评语。每维 6 句。
_PRAISE: dict[str, tuple[str, ...]] = {
    "clutch": (
        "四下里的呼吸声都停了。",
        "血顺着指尖往下滴，人却没退。",
        "所有人都以为他要倒，他没有。",
        "最后一息，他愣是撑住了。",
        "场边有人失声喊了出来。",
        "他站着，对面躺着。",
    ),
    "comeback": (
        "先前被压得抬不起头，如今反手一击。",
        "血线跌到底，又硬生生爬了回来。",
        "起手狼狈，收尾却漂亮。",
        "被逼到墙角的人，打得更狠。",
        "局势翻过来的那一刻，全场都愣了。",
        "跌下去那么深，还是爬起来了。",
    ),
    "crush": (
        "越级斩杀，四下鸦雀无声。",
        "等级压着他的人，反倒被他压着打。",
        "这一战，等级没帮上忙。",
        "本该仰头看人，结果他低头看了对方一眼。",
        "差着境界的仗，被他打成了单方面。",
        "对面连他的衣角都没沾到。",
    ),
    "burst": (
        "一击落下，对面连惨叫都没来得及。",
        "那一拳砸实，事情就结束了。",
        "出手只有一次，结果不用看了。",
        "气机炸开的瞬间，对面就飞了出去。",
        "没人看清那一招是怎么出的。",
        "胜负就在这一下。",
    ),
    "puppet": (
        "那具傀儡的拳头，比主人还狠。",
        "人还没动，傀儡已经把场面收了。",
        "对面看的是他，挨打的却是傀儡的手下。",
        "傀儡踏前一步，对手就倒了。",
        "养了这么久，今天全靠它。",
        "他在后面看着，傀儡把事办完了。",
    ),
    "crit_storm": (
        "接连几声爆响，对面被打得抬不起头。",
        "一击比一击狠，围观的人不敢出声。",
        "暴击连成一片，血花一路溅开。",
        "他的斗气像是长了眼睛，专挑要害打。",
        "对面连躲的余地都没有。",
        "每一下都砸在要害上。",
    ),
    "tier_high": (
        "高阶斗技一亮，四周瞬间静了。",
        "那一招出手，气机压得人喘不过气。",
        "阶位摆在这儿，对手连招都接不住。",
        "光是那股气机，就逼得人后退半步。",
        "对手的脸色，在招式亮出时变了。",
        "这一招不是这个境界该有的。",
    ),
    "skillful": (
        "招式一套接一套，对手摸不着节奏。",
        "他手里像是永远还有下一招。",
        "刚挡住这一招，下一招已经到了。",
        "对面根本猜不出他要用什么。",
        "换招快得让人眼花。",
        "他一招接一招，场面全被他牵着走。",
    ),
    "devour": (
        "对手的气机，硬生生被他吞了进去。",
        "一吞之下，局势立刻翻了个个儿。",
        "他把敌人的力道，炼成了自己的。",
        "被吞的那一口，成了他的补益。",
        "对面的脸色，肉眼可见地垮了下去。",
        "吞完这一口，他的气息更沉了。",
    ),
    "lucky": (
        "气机自己撞了上来。",
        "天时站在他这边。",
        "谁也没想到，机缘偏偏落在他头上。",
        "风往他那边吹，他站对了位置。",
        "贵人搭了一把手，路就顺了。",
        "这一场赢得有点邪门。",
    ),
    "potion": (
        "一炉接一炉地嗑，硬是把命续了下来。",
        "药罐子也有药罐子的打法。",
        "空瓶子堆了一地，人还站着。",
        "靠丹药吊着的那口气，撑到了最后。",
        "别人拼斗气，他拼家底。",
        "最后一炉丹咽下去，他又冲了上去。",
    ),
    "speed": (
        "三两下结束，看的人都愣了。",
        "还没看清怎么打的，人已经倒了。",
        "从出手到收手，没拖一息。",
        "对面还没站稳，事情就完了。",
        "这一场没有拖泥带水。",
        "一眨眼，胜负已分。",
    ),
    "frugal": (
        "斗气还剩大半，对手已经躺下。",
        "赢得毫不费力，气都没喘匀。",
        "收手时，他的气息还稳得很。",
        "对手倒下时，他连衣角都没乱。",
        "力道掐得刚刚好。",
        "打完这一场，他看起来像没打过。",
    ),
    "clean": (
        "一滴血没掉，衣角都没被碰到。",
        "从头到尾没挨一下。",
        "对手连他的边都没沾着。",
        "他站着的地方，连尘土都没扬起来。",
        "对面从头到尾没摸到他。",
        "连衣角都没让人碰到。",
    ),
    "plain": (
        "打得稳，赢得也干脆。",
        "没什么悬念，拿下。",
        "这一架平平无奇，他还是赢了。",
        "该赢的仗，赢下来了。",
        "收手时，对面已经躺平。",
        "他收招，对面倒地。",
    ),
}

#: 败局嘲讽的**话**（不含说话人和动作）：敌人当场骂阵，短句、带杀气。
#: {skill} 只出现在真有主力技的档，避免渲染出「『』」空引号。
_SPEECH: dict[str, tuple[str, ...]] = {
    "reflect": (
        "你打我的力道，自己尝个够！",
        "老子还没动手，你自己就躺了？",
        "越用力，死得越快！",
        "反噬的滋味，比挨打还爽吧？",
        "你这一击，送了自己的命！",
        "用全力打我？蠢货！",
    ),
    "dot_death": (
        "毒已经进了你的骨头，挣扎什么？",
        "等着，看你的血一点点凉！",
        "不用我动手，等一会儿你自己就倒！",
        "这毒的滋味，好不好受？",
        "你以为是打输了？是熬输了！",
        "毒替我拿了你的命！",
    ),
    "controlled": (
        "动不了了吧？那就乖乖受死！",
        "被定在原地的滋味，怎么样？",
        "你那一身本事，动不了就是废的！",
        "别挣扎了，省点力气！",
        "钉在这儿，挨打都跑不掉！",
        "连抬手都做不到，还谈什么翻盘？",
    ),
    "dodged": (
        "你打的是空气！",
        "连老子一片衣角都没碰到！",
        "慢半拍，就差了一整场！",
        "手挥得挺响，全落空了！",
        "追得上我的影子再说！",
        "你打的到底是哪儿？",
    ),
    "potion_futile": (
        "丹药吃了一堆，还是挡不住这一爪！",
        "药罐子，留着丹药给自己收尸吧！",
        "嗑到瓶子空了，命还是保不住！",
        "靠丹撑着打，药一停就散！",
        "你的丹药救不了你！",
        "丹炼得再好，也挡不住我这一击！",
    ),
    "no_mp": (
        "斗气都没了，还敢叫嚣？",
        "就这点家底，回去多炼几炉丹！",
        "连斗技都放不出，拿什么跟我打？",
        "气都干了还往前撞，嫌命长？",
        "靠两条胳膊硬凿，你当自己是炼体的？",
        "拳头砸了半晌，挠痒呢？",
    ),
    "skill_short": (
        "你那手{skill}，火候还差得远！",
        "{skill}？给老子挠痒都不够！",
        "三脚猫的{skill}，也敢拿出来现世？",
        "你把{skill}当试探，我没把你当对手！",
        "{skill}的劲道太软，再练十年！",
        "就凭这门{skill}，也配挡我？",
    ),
    "skill_cap": (
        "{skill}练到顶了？也就这点能耐！",
        "练满了又如何？这破招压不住我！",
        "换门像样的斗技再来，别拿{skill}丢人！",
        "老子就吃你这一招，有何稀奇？",
        "满级又怎样？你的{skill}打不穿我的皮！",
        "这一招我早看穿了，你还没发现？",
    ),
    "one_trick": (
        "一招用到老，你也配？",
        "这一招，老子看了十几遍了！",
        "下一招呢？没有了？",
        "别拿一招走天下，笑死我了！",
        "同样的招式放一整场，你累不累？",
        "你那点本事，老子都会背了！",
    ),
    "tier_low": (
        "黄阶的玩意儿，也敢往我身上招呼？",
        "低阶斗技打一辈子，还是低阶！",
        "这点阶位，我闭着眼都接得住！",
        "手里就没门拿得出手的？",
        "低阶就是低阶，别不自量力！",
        "拿这种招来送死，太看得起自己了！",
    ),
    "near_win": (
        "就差一口气？可惜，你没有！",
        "差一点？老子不会给你第二次！",
        "你把我打到了门口，却没力气推门！",
        "我的血都快流干了，你却先倒了！",
        "不甘心？那就下回再来！",
        "你那点力气，到这儿就用完了？",
    ),
    "crit_dry": (
        "打半天一下狠的都没有，挠痒呢？",
        "你的运气，全用在挨打上了！",
        "一记暴击都没有，你打的是棉花？",
        "力气倒是匀，可惜全是软的！",
        "连要害都摸不着，还打什么！",
        "下回出手前，先把斗气炼实！",
    ),
    "squishy": (
        "这身子骨，一捏就碎！",
        "血比纸还薄，也敢进山？",
        "我还没用力，你怎么就倒了？",
        "斗技再花，身子不经打也是白搭！",
        "这种身板，适合待在药堂！",
        "别怪我下手重，是你不经打！",
    ),
    "fast_loss": (
        "这就倒了？老子还没热身！",
        "两招都接不住，回去练十年！",
        "连回合都没凑够，也敢来叫阵？",
        "你倒得比我想的还快！",
        "热身都没结束，人就躺下了！",
        "下回多撑两招，给我点面子！",
    ),
    "overmatched": (
        "蝼蚁，也敢挡我的路？",
        "回去再练十年！",
        "不够看。",
        "连我的真身都没逼出来！",
        "下次挑个同级的，起码体面点！",
        "不服？再来一次，结果一样！",
    ),
}

#: 动作前缀按对手类型分：魔兽是兽性动作，人型是人物动作。
#: 🚨 绝不能让岩蛇「负手而立」——那不是小说，是 AI 给魔兽套了 humans 的词。
_BEAST_ACTIONS = (
    "龇牙低吼", "喉咙里滚出一声闷哼", "吐着信子", "甩了甩尾巴",
    "眯起竖瞳", "咧开獠牙", "猛地扑前半步", "鼻翼翕动",
)
_HUMAN_ACTIONS = (
    "冷笑一声", "桀桀怪笑", "嗤笑", "阴恻恻地笑",
    "冷哼", "缓缓抽刀", "负手而立", "眯起眼",
)


def _pick(pool: tuple[str, ...], seed: int) -> str:
    """确定性随机：同一场同句（回看/回执一致），不同场次抽到不同句。

    🚨 不用 ``seed % len(pool)``：那会让种子按回合数递增时反复抽同一句。
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


def taunt(case: str, seed: int = 0, foe: str = "", skill: str = "",
          beast: bool = True) -> str:
    """败局嘲讽一句（战报最后一行）。缺说话人时返回空串——不冒认谁在说话。

    ``beast`` 由 ``analyze`` 按对手 kind / 名字判定：兽类用龇牙低吼，人型才用
    桀桀怪笑，避免给魔兽套上人才有的动作。
    """
    if not foe:
        return ""
    speech = _pick(_SPEECH.get(str(case or "")), seed)
    if not speech:
        return ""
    acts = _BEAST_ACTIONS if beast else _HUMAN_ACTIONS
    action = _pick(acts, (int(seed) ^ 0x85EBCA6B) & 0x7FFFFFFF)
    spoken = speech.replace("{skill}", str(skill or ""))
    return f"{foe}{action}：「{spoken}」"
