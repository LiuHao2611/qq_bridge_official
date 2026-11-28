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
        # 🚨 不能只按名字匹配：魂殿鹜护法这类人型反派也在 MONSTERS 表里，
        #    只看名字会给它套上「吐着信子」这种兽性动作。先看 detail 的 kind，
        #    缺失才回落到同名配置的 kind。
        if not foe_kind:
            same = next((m for m in _cfg.MONSTERS
                         if str(m.get("name") or "") == _foe_name(res)), None)
            foe_kind = str((same or {}).get("kind") or "")
        foe_beast = foe_kind == "魔兽"
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
# 天蚕土豆文风（2026-11-28 用户「好好去学学习学习天蚕土豆的文笔风格，你去恶补」）
# ---------------------------------------------------------------------------
# 这次不是「凭感觉改几句」，是拿原著原文对着学。已核对的原文片段：
#
#   鹜护法（第692章）：「桀桀，不愧是闻名大陆的『药』尊者，到了这个份上竟然还这么
#     难啃，不过，这样的战斗，你还能坚持多久?」
#   鹜护法（第692章）：「不自量力，滚开!」「小子，乖乖的将你萧家的东西叫出来，
#     本护法还能让你死得痛快点!」
#   地魔老鬼（第883章）：「老夫对你的兴趣还真的是越来越大了呀。」
#   魂天帝：「成王败寇，不讲过程，今日我若为胜者，他日臭名，自然有你背负!」「死吧!」
#   萧炎：「当年，你可曾想到你会有今天的结局啊?」「怎么? 很痛苦是不是?」
#
# 归纳出的三条硬特征（本模块据此写句，不是靠「短句」了事）：
#   1. **身份感**：反派自称「本护法/老夫/本王/我魂殿」，叫对手「小子」「蝼蚁」；
#      兽类不套人型自称，只会低吼威胁。{you} 占位符由 taunt() 按对手类型填。
#   2. **狠话是反问 + 四字短语 + 感叹**：「你还能坚持多久?」「不自量力」「找死」
#      「成王败寇」「滚开!」——是当场压人，不是事后分析「你练得不够」。
#   3. **旁白是微表情白描**：「眼瞳一缩」「面色一变」「嘴角缓缓掀起一抹冷笑」
#      「眼中闪过一抹异色」「声音因为惊骇而变得尖锐」；比喻取材自然（如湖水/鹰）。
#      只写看得见的动作与反应，绝不写「这才是真本事」这类评语。
#
# 🚨 反复踩过的坑（三次纠正）：战后点评「练满是好事，可惜练的是下风招」
#    「他不像只会一招的人」「赢得干净」——那是战报，不是小说；
#    以及让岩蛇「负手而立」——魔兽不套人型动作。
# ---------------------------------------------------------------------------

#: 胜局赞叹：判词行之后的现场旁白（不加引号）。微表情 + 动作白描，只写画面。
_PRAISE: dict[str, tuple[str, ...]] = {
    "clutch": (
        "他嘴角溢血，脚下却没退半步。",
        "四周的呼吸声，齐齐一滞。",
        "所有人都以为他要倒——他没有。",
        "眼瞳一缩，那一息他硬是撑住了。",
        "场边有人失声惊呼出来。",
        "血染红了衣襟，他还在笑。",
    ),
    "comeback": (
        "先前被压得抬不起头，此刻反手一击。",
        "眼瞳一缩，局势已经翻了过来。",
        "嘴角缓缓掀起一抹冷笑，你动了。",
        "被逼到墙角的人，出手更狠。",
        "众人面色一变，谁也没料到。",
        "血线到底，又硬生生爬了起来。",
    ),
    "crush": (
        "越级而斩，全场一片死寂。",
        "对面面色一变，再不敢抬头。",
        "等级压着他，却被他压着打。",
        "一招落下，对面眼中满是骇然。",
        "本该仰视的人，此刻低下了头。",
        "四下里，没人敢出声。",
    ),
    "burst": (
        "一击落下，对面连惨叫都没来得及。",
        "那一拳砸实，气机轰然炸开。",
        "出手只有一次，够了。",
        "血花溅开，对面倒飞了出去。",
        "快得没人看清那一招。",
        "胜负就在一瞬之间。",
    ),
    "puppet": (
        "傀儡踏前一步，对面就倒了。",
        "那具傀儡的拳头，比主人还狠。",
        "他还没动，傀儡已经收了场。",
        "对面盯着他，挨打的却是傀儡。",
        "金光一闪，傀儡补上了最后一击。",
        "傀儡立功，全场变色。",
    ),
    "crit_storm": (
        "接连几声爆响，对面抬不起头。",
        "一击比一击狠，众人不敢出声。",
        "血花一路溅开，没有停过。",
        "他的斗气像长了眼睛，专挑要害。",
        "对面连躲都来不及。",
        "每一下都砸在要害上。",
    ),
    "tier_high": (
        "高阶斗技一亮，四周瞬间静了。",
        "气机压得人喘不过气来。",
        "对面面色一变，后退了半步。",
        "那一招出手，天色都暗了一瞬。",
        "阶位摆在那儿，想接也接不住。",
        "不少人眼中闪过一抹惊色。",
    ),
    "skillful": (
        "招式一套接一套，对手摸不着节奏。",
        "刚挡住一招，下一招已经到了。",
        "他手里像是永远还有下一招。",
        "对面眼中满是惊疑。",
        "换招之快，让人眼花缭乱。",
        "整场下来，对面都被你牵着鼻子走。",
    ),
    "devour": (
        "对手的气机，被他一口吞了下去。",
        "一吞之下，局势陡然逆转。",
        "敌人的力道，尽数成了他的补益。",
        "对面的脸色瞬间垮了下来。",
        "气机如湖水般涌进他体内。",
        "吞完这一口，他的气息更沉了。",
    ),
    "lucky": (
        "气机自己撞了上来。",
        "天时站在他这一边。",
        "谁也没想到，机缘偏偏落在他头上。",
        "一阵风来，局势就变了。",
        "有贵人出手，路便顺了。",
        "这一场赢得有些邪门。",
    ),
    "potion": (
        "空瓶子堆了一地，人还站着。",
        "一炉接一炉，命硬是续了下来。",
        "靠丹药吊着那口气，撑到了最后。",
        "别人拼斗气，他拼家底。",
        "最后一炉丹咽下去，他又冲了上去。",
        "嘴角还沾着药渍，人已经赢了。",
    ),
    "speed": (
        "三两下结束，看的人都愣了。",
        "还没看清，人已经倒了。",
        "从出手到收手，没拖一息。",
        "对面还没站稳，事情就完了。",
        "一眨眼，胜负已分。",
        "快得只剩下一道残影。",
    ),
    "frugal": (
        "斗气还剩大半，对面已经躺下。",
        "收手时，气息还稳得很。",
        "衣角都没乱，人就赢了。",
        "力道掐得刚刚好。",
        "打完一场，像没打过一样。",
        "气机未动，胜负已定。",
    ),
    "clean": (
        "一滴血没掉，衣角都没被碰到。",
        "从头到尾没挨一下。",
        "对手连他的边都没沾着。",
        "脚下的尘土都没扬起来。",
        "对面始终没摸到他。",
        "连衣角都没让人碰到。",
    ),
    "plain": (
        "打得稳，赢得干脆。",
        "没什么悬念，拿下。",
        "这一架平平无奇，他还是赢了。",
        "该赢的仗，赢下来了。",
        "收手时，对面已经倒地。",
        "他收招，对面倒地。",
    ),
}

#: 败局骂阵：当场压人的话，短、狠、带反问与四字狠话。
#: {skill}=本场主力斗技，{you}=「小子」(人型) / 「你」(兽类)。
_SPEECH: dict[str, tuple[str, ...]] = {
    "reflect": (
        "你打我的力道，自己尝！",
        "哼，反噬的滋味如何？",
        "不自量力，拿命来拼？",
        "用全力？那是你找死！",
        "这力道，原样还给你！",
        "连自己都护不住，还来杀我？",
    ),
    "dot_death": (
        "毒已入骨，还挣扎什么？",
        "等着，看你的血一点点凉。",
        "哼，不用我动手。",
        "这毒，够你受的！",
        "熬不住了吧？",
        "你的命，是毒收的。",
    ),
    "controlled": (
        "动不了了？哼！",
        "被定住的滋味，如何？",
        "{you}就这点本事，还想翻盘？",
        "乖乖受死吧！",
        "连手都抬不起来，可笑！",
        "不自量力。",
    ),
    "dodged": (
        "你打的是空气！",
        "哼，连我衣角都没碰到！",
        "慢了，太慢了！",
        "再来？我让你三招！",
        "打空了，很爽？",
        "你的招，我早看穿了。",
    ),
    "potion_futile": (
        "丹药吃了一堆，还是挡不住！",
        "哼，药能救你几次？",
        "空瓶子留着陪葬吧！",
        "靠药撑着，也配称强者？",
        "你的丹，救不了你的命！",
        "嗑到死，也就这点出息！",
    ),
    "no_mp": (
        "斗气都没有了，也敢来送死？",
        "哼，就这点斗气，也敢叫嚣？",
        "{you}回去炼丹吧，不配用斗技。",
        "气都干了还往前撞？找死！",
        "靠两条胳膊硬凿，不知所谓！",
        "拳头砸了半晌，挠痒呢？",
    ),
    "skill_short": (
        "你那手{skill}，火候还差得远！",
        "哼，这招也敢拿出来？",
        "{you}把招练熟了再来！",
        "{skill}？挠痒都嫌软！",
        "不自量力，就凭这门破招？",
        "这点斗技，我闭着眼都能接！",
    ),
    "skill_cap": (
        "练到顶了？不过如此！",
        "满级又如何？撑不起你的命！",
        "换门斗技吧，这招废了。",
        "哼，这一招我早看穿了！",
        "就这点本事，也敢来挑战？",
        "招式到头了，人还没到。",
    ),
    "one_trick": (
        "一招用到老，你也配？",
        "这一招，我看过十几遍了！",
        "下一招呢？没有了？",
        "别拿一招走天下，可笑！",
        "同样的招放一整场，不累么？",
        "你那点本事，我都会背了！",
    ),
    "tier_low": (
        "黄阶的东西，也敢往我身上招呼？",
        "低阶斗技打一辈子，还是低阶！",
        "这点阶位，我闭着眼都接得住！",
        "手里就没门拿得出手的？",
        "低阶就是低阶，不自量力！",
        "拿这种招来送死，太高看自己了！",
    ),
    "near_win": (
        "就差一口气？可惜，你没有！",
        "哼，你倒下了，我还站着！",
        "差一点？那是你的命数！",
        "你把我逼到这份上，够本了！",
        "再差半寸？没机会了！",
        "你尽力了，可惜不够！",
    ),
    "crit_dry": (
        "打半天，一下狠的都没有！",
        "哼，挠痒呢？",
        "你的运气，全用在挨打上了！",
        "连要害都摸不着，打什么！",
        "软绵绵的，多吃点苦头吧！",
        "再来，我让你三招！",
    ),
    "squishy": (
        "一捏就碎的身子，也敢来？",
        "血比纸薄，找死！",
        "哼，我没用力呢。",
        "这身子骨，不经打。",
        "进山前，先把命练硬！",
        "不自量力，滚回去！",
    ),
    "fast_loss": (
        "这就倒了？我还没热身！",
        "两招都接不住，找死！",
        "哼，太弱了。",
        "连回合都没凑够。",
        "回去练十年，再来！",
        "不堪一击！",
    ),
    "overmatched": (
        "蝼蚁，也敢挡我的路？",
        "回去再练十年！",
        "不自量力！",
        "不够看。",
        "哼，就凭你？",
        "滚！",
    ),
}

#: 动作前缀按对手类型分：魔兽是兽性动作，人型是人物动作。
#: 🚨 绝不能让岩蛇「负手而立」。
_BEAST_ACTIONS = (
    "龇牙低吼", "喉咙里滚出一声闷哼", "吐着信子", "甩了甩尾巴",
    "眯起竖瞳", "咧开獠牙", "猛地扑前半步", "鼻翼翕动",
)
_HUMAN_ACTIONS = (
    "冷笑一声", "桀桀怪笑", "嗤笑", "阴恻恻地笑",
    "冷哼", "缓缓抽刀", "负手而立", "眯起眼",
)


def _pick(pool: tuple[str, ...], seed: int) -> str:
    """确定性随机：同一场同句（回看/回执一致），不同场次抽到不同句。"""
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
    """败局骂阵一句（战报最后一行）。缺说话人时返回空串——不冒认谁在说话。

    ``{you}`` 按对手类型填：人型反派喊「小子」（原著鹜护法口吻），兽类只说「你」——
    魔兽不套人型的自称。
    """
    if not foe:
        return ""
    speech = _pick(_SPEECH.get(str(case or "")), seed)
    if not speech:
        return ""
    acts = _BEAST_ACTIONS if beast else _HUMAN_ACTIONS
    action = _pick(acts, (int(seed) ^ 0x85EBCA6B) & 0x7FFFFFFF)
    who = "" if beast else "小子，"
    spoken = speech.replace("{skill}", str(skill or "")).replace("{you}", who)
    return f"{foe}{action}：「{spoken}」"
