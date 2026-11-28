# -*- coding: utf-8 -*-
"""斗破 · 对局诊断 + 针对性台词（2026-11-28 用户「别搞那些莫名其妙的台词硬插入」）。

## 为什么要有这个模块

用户原话：「战斗播报的结尾台词恐怖如斯有点莫名其妙……胜局赞叹的台词应该加到
『二星大斗师斗晶映体』这一句的后面」「败局就放在结尾让活着的怪讲出，嘲讽语句」
「后台要有一个对局情况分析，准确判断出失败原因或者成功原因，针对性的嘲讽或者吹捧」。

旧实现（``battle_flavor``）只有**战况档**（crush/narrow/great/lose）+ 境界 + 招式，
判据太粗：一场「斗气枯竭后只能普攻被磨死」和一场「斗技打不动被反杀」在它眼里
都是 ``lose``，只能说同一句「无奈地摇了摇头」。这就是「莫名其妙」的根：
**台词没有对应的失败点**。

## 判据全部来自真实 log，不猜

一场战斗的 ``res`` 里躺着逐条出手记录（见 ``battle_core._hit`` / ``fight``）：

  - 玩家出手：带 ``skill``（bool，False = 普攻）、``move``（招式名）、``dmg``、
    ``mp_cost``、``evaded``（被对手闪过）；
  - 敌方出手：``taken=True``（兽击 / 反伤 / 中毒，其中 ``dot`` 标 DoT）；
  - 傀儡出手：``puppet=True``（伤害不算玩家的）；
  - 控制：``kind="被控"``；反伤：``kind="反伤"``。

诊断只做**计数与比例**，不引入任何新数值、不改战斗结果。

## 失败原因（按证据优先级，先命中先出）

  1. ``reflect``     自己的力道被反伤弹死（反伤占总承伤 ≥35%）
  2. ``no_mp``       斗气枯竭：普攻占出手 ≥40% 且（末斗气 ≤0 或 exhausted）
  3. ``dodged``      打不中：被闪过 ≥3 次或占出手 ≥25%
  4. ``skill_short`` 主力斗技**没满级**（有该技阶位上限可比才判）
  5. ``skill_cap``   主力斗技**已满级**仍打不动 → 阶位/用法问题
  6. ``controlled``  被控 ≥2 次
  7. ``squishy``     承伤 ≥1.2 倍自身上限 → 皮薄
  8. ``overmatched`` 上面都不成立 → 纯粹实力差距（对击杀者输出不足 50%）

  🚨 4/5 必须真的查到该技的阶位上限（``core.skill_max``）才敢说「没满级」/
  「已满级」；查不到就跳过这两条，绝不瞎猜玩家练没练满。

## 胜利原因

  1. ``clutch``  残血翻盘（战后气血 <20%）
  2. ``crush``   越级斩杀（对手最高等级 ≥ 自己 +2）
  3. ``burst``   一击打掉击杀者 ≥50% 血量
  4. ``puppet``  傀儡输出超过本人
  5. ``lucky``   贵人相助 / 心火共鸣
  6. ``potion``  嗑药 ≥2 次续下来的
  7. ``clean``   一滴血没掉
  8. ``plain``   普通取胜

## 台词纪律

  - **胜局赞叹**只放在判词行之后（用户指定位置），旁白口吻、不加引号；
  - **败局嘲讽**只放在战报最后一行，且**由活着的怪开口**（带说话人 + 引号），
    嘲讽必须点名这次的失败点（没气 / 哪招没练够 / 哪招练满了也就这样 / 打不中 / 皮薄）；
  - 敌方嘲讽是**本作自编**（用户 2026-11-28 明确要求「你自己多编几句」），
    **绝不冒充原著台词、不标出处**；原著逐字台词仍只走 ``doupo_text.CHAPTER_QUOTE``。
"""
from __future__ import annotations

#: 敌方出手里算「打在我身上」的 kind（dot 单独剔，避免把中毒算成硬碰硬）
_TAKEN_KINDS = frozenset(("兽击", "反伤", "中毒", "持续伤害"))

_PLAIN_RATIO_DRY = 0.40      # 普攻占出手多少算「斗气枯竭只能硬凿」
_EVADE_MIN = 3               # 被闪过几次才算「打不中」
_EVADE_RATIO = 0.25
_CTRL_MIN = 2
_SQUISHY_MULT = 1.2          # 承伤 / 自身上限
_REFLECT_SHARE = 0.35
_BURST_SHARE = 0.50


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


def analyze(res: dict, p: dict | None = None, lv: int = 0,
            hp_ratio: float | None = None, hp_max: int = 0) -> dict:
    """对局诊断。返回 ``{"win", "case", "foe", "skill", ...证据字段}``。

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
    my_dmg = sum(_int(e.get("dmg")) for e in atk)
    pup_dmg = sum(_int(e.get("dmg")) for e in log if e.get("puppet"))
    taken = [e for e in log if e.get("taken") and str(e.get("kind") or "") in _TAKEN_KINDS]
    foe_dmg = sum(_int(e.get("dmg")) for e in taken)
    reflect_dmg = sum(_int(e.get("dmg")) for e in taken if e.get("reflect"))
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

    n_atk = len(atk)
    plain_ratio = (len(plain) / float(n_atk)) if n_atk else 0.0
    evade_ratio = (len(evaded) / float(n_atk)) if n_atk else 0.0
    foe_hp = _int(res.get("foe_hp_max"))
    dealt_ratio = ((my_dmg + pup_dmg) / float(foe_hp)) if foe_hp > 0 else 0.0
    low_mp = _int(res.get("mp_end")) <= 0 or bool(res.get("exhausted"))
    foe_top = max([_int(x.get("lv")) for x in (res.get("detail") or [])] or [0])
    ratio = None if hp_ratio is None else max(0.0, min(1.0, float(hp_ratio)))

    out = {"win": win, "case": "", "foe": _foe_name(res), "skill": main,
           "skill_lv": lv_have, "skill_cap": lv_cap,
           "plain_ratio": round(plain_ratio, 3),
           "evade_ratio": round(evade_ratio, 3),
           "dealt_ratio": round(dealt_ratio, 3), "best_hit": best_hit,
           "foe_hp": foe_hp, "foe_dmg": foe_dmg, "reflect_dmg": reflect_dmg,
           "pup_dmg": pup_dmg, "my_dmg": my_dmg, "ctrl": ctrl,
           "hp_ratio": ratio}

    if not n_atk and not taken:
        # 一条出手记录都没有（空 res / 桩数据）→ 不诊断，宁可不说话
        return out

    if win:
        if ratio is not None and ratio < 0.2:
            out["case"] = "clutch"
        elif lv and foe_top >= int(lv) + 2:
            out["case"] = "crush"
        elif foe_hp > 0 and best_hit >= foe_hp * _BURST_SHARE:
            out["case"] = "burst"
        elif pup_dmg > 0 and pup_dmg > my_dmg:
            out["case"] = "puppet"
        elif res.get("hero_aid") or res.get("fortune"):
            out["case"] = "lucky"
        elif _int((res.get("auto_pills") or {}).get("hp")) + \
                _int((res.get("auto_pills") or {}).get("mp")) >= 2:
            out["case"] = "potion"
        elif _int(res.get("taken")) <= 0:
            out["case"] = "clean"
        else:
            out["case"] = "plain"
        return out

    # ---- 败局：先看「怎么死的」（防守面），再看「为什么打不死它」（进攻面）----
    if foe_dmg > 0 and reflect_dmg >= foe_dmg * _REFLECT_SHARE:
        out["case"] = "reflect"
    elif plain_ratio >= _PLAIN_RATIO_DRY and low_mp:
        out["case"] = "no_mp"
    elif len(evaded) >= _EVADE_MIN or evade_ratio >= _EVADE_RATIO:
        out["case"] = "dodged"
    elif lv_cap > 0 and lv_have < lv_cap:
        out["case"] = "skill_short"
    elif lv_cap > 0 and lv_have >= lv_cap:
        out["case"] = "skill_cap"
    elif ctrl >= _CTRL_MIN:
        out["case"] = "controlled"
    elif hp_max > 0 and foe_dmg >= _SQUISHY_MULT * int(hp_max):
        out["case"] = "squishy"
    else:
        # 打不死它（对击杀者输出不到一半）与「被它打死」是同一枚硬币两面，
        # 前面所有细分都没命中时，这就是最诚实的结论：实力差距。
        out["case"] = "overmatched"
    return out


# ---------------------------------------------------------------------------
# 胜局赞叹：放在判词行之后（用户 2026-11-28 指定位置），旁白口吻、不加引号
# ---------------------------------------------------------------------------
_PRAISE: dict[str, tuple[str, ...]] = {
    "clutch": ("只差一线——站到最后的还是他。",
               "血都快流干了，偏偏没倒。"),
    "crush": ("越级而斩，这一手不像这个境界该有的。",
              "对面等级压着他，他倒压着对面打。"),
    "burst": ("一击定局，其余都是多余的。",
              "这一下，直接把话说完。"),
    "puppet": ("那具傀儡，比他本人还狠。",
               "人还没发力，傀儡先把场面收了。"),
    "lucky": ("天也在帮他。",
              "气机自己撞上来，赢了。"),
    "potion": ("靠丹续下来的命，也是本事。",
               "药罐子，也赢了。"),
    "clean": ("干净利落，一根头发都没掉。",
              "连衣角都没让人碰到。"),
    "plain": ("赢了就是赢了。",
              "这一场，赢得干净。"),
}

# ---------------------------------------------------------------------------
# 败局嘲讽：战报最后一行，活着的怪开口（{foe} 说话人、{skill} 本场主力斗技）
# ---------------------------------------------------------------------------
_TAUNT: dict[str, tuple[str, ...]] = {
    "no_mp": (
        "{foe}嗤笑：「斗气都见底了还硬撑？就这点家底，也敢来叫嚣。」",
        "{foe}抖了抖爪子：「拳头砸了半晌，挠痒呢？回去多炼几炉丹，先把气养足。」",
        "{foe}斜眼：「连斗技都放不出来，你拿什么跟我打？」",
    ),
    "skill_short": (
        "{foe}侧身让过：「你那手『{skill}』，练得跟没练一样。」",
        "{foe}嗤笑：「『{skill}』这点火候也敢拿出来？再多喂几炉焚血散吧。」",
        "{foe}摇头：「招式不差，是你使不出来。」",
    ),
    "skill_cap": (
        "{foe}大笑：「『{skill}』练到顶了？就这阶位的破技，练到天上也就这样。」",
        "{foe}冷冷道：「招是好招，可惜阶位太低，压不住我。换本高阶的再来。」",
        "{foe}拍了拍身上的灰：「满级又怎样？你还是打不动我。」",
    ),
    "dodged": (
        "{foe}轻轻一扭就避开了：「连我衣角都摸不到，你打谁呢？」",
        "{foe}嗤笑：「拳头挥得挺响，可惜全是空气。」",
    ),
    "squishy": (
        "{foe}舔了舔牙：「皮这么薄，一巴掌就没了，也敢进山？」",
        "{foe}冷笑：「气血比纸还薄，回去先把身子练结实。」",
    ),
    "controlled": (
        "{foe}俯视着你：「被钉在原地的滋味，好受么？」",
    ),
    "reflect": (
        "{foe}负手而立：「我可没动手——是你自己的力道把自己震死的。」",
    ),
    "overmatched": (
        "{foe}连正眼都懒得给：「回去再练十年。」",
        "{foe}转身就走：「不够看。」",
    ),
}


def _pick(pool: tuple[str, ...], seed: int) -> str:
    if not pool:
        return ""
    return pool[int(seed) % len(pool)]


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
