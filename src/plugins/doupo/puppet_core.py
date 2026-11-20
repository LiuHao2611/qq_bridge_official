# -*- coding: utf-8 -*-
"""斗破苍穹 · 傀儡系统核心逻辑（纯函数，不碰 IO）。

考据来源：`docs/斗破傀儡_原文证据台账_2026-11-18.md`，每条规则注 E 编号。
设计来源：`docs/斗破傀儡系统_设计方案_2026-11-18.md`（从零重写版）。

红线：``puppet_expected_damage`` 是傀儡期望输出的**唯一函数**，
``core.fight`` 与 ``core.damage_schedule`` 都必须调它——只改一处守门必红。
傀儡数据由 ``puppet_store`` 提供（独立 SQLite 真实列，非 JSON），本模块只收 dict。
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from . import config as cfg


# ---------------------------------------------------------------------------
# 资格五道（E22-E31）—— 全部挂靠玩家已有养成，读 p，不新增玩家存档字段
# ---------------------------------------------------------------------------
def gate_realm(p: dict, tier: int) -> tuple[bool, str]:
    """① 境界闸（E31）：大斗师碰不了地级。下限 [调参] PUPPET_REALM_MIN。"""
    need = cfg.PUPPET_REALM_MIN.get(int(tier), 0)
    cur = cfg.realm_index(int(p.get("lv", 1)))
    if cur < need:
        return False, "境界不足，须%s以上" % cfg.REALM_NAMES[need]
    return True, ""


def gate_soul(p: dict) -> tuple[bool, str]:
    """② 灵魂境界闸（E20/E22/E23/E24）：炼药品阶达标才有灵境灵魂去'分半'认主。"""
    if int(p.get("alch", 0)) < cfg.PUPPET_ALCH_MIN:
        grade = cfg.ALCHEMIST[min(cfg.PUPPET_ALCH_MIN, cfg.ALCH_MAX)][0]
        return False, "灵魂境界未达灵境，须炼药师品阶到%s" % grade
    return True, ""


def craft_floor(p: dict) -> int:
    """该玩家**必然**至少开过几炉（由炼药师品阶反推的下限）。

    晋阶吃掉的炼药经验是可查的：从一品升到当前品阶共需
    ``sum(ALCHEMIST[:alch][1])`` 点，而每炉至少给 ``CRAFT_EXP``（失败给一半，
    所以真实炉数只会更多）。再加上当前档位未消化的 ``alch_exp``。

    2026-11-20 之前生产代码**从没写过** ``p['craft_count']``（只有测试手塞），
    老档这个字段是空的 —— 直接读 0 会让所有老玩家永远卡在「火候不济」。
    用这个下限兜住，不需要改存档、也不需要给谁补数据。
    """
    total = int(p.get("alch_exp", 0) or 0)
    for idx in range(0, int(p.get("alch", 0) or 0)):
        try:
            total += int(cfg.ALCHEMIST[idx][1])
        except IndexError:
            break
    return total // max(1, int(cfg.CRAFT_EXP))


def gate_fire_ctrl(p: dict) -> tuple[bool, str]:
    """③ 控火闸（E16/E25）：靠多炼药练出，读炼药次数/高阶丹药记录。

    不新增字段：从 ``p['craft_count']`` / ``p['high_grade_crafts']`` 读，
    老档无这两个键视为 0（引导去炼药，不白送）。
    """
    # 炉数取「真实计数」与「品阶下限」的较大者：下限是能证明的，不会被老档吃掉。
    cnt = max(int(p.get("craft_count", 0) or 0), craft_floor(p))
    high = int(p.get("high_grade_crafts", 0) or 0)
    if "high_grade_crafts" not in p and int(p.get("alch", 0) or 0) >= cfg.PUPPET_ALCH_MIN:
        # 老档无法回溯「高阶丹」明细（该字段历史上从没写过）。但走到七品必须
        # 累计 238 炉以上，火候本身已被证明 —— 视为满足，只有明细数未知。
        high = max(high, cfg.PUPPET_HIGH_GRADE_MIN)
    if cnt < cfg.PUPPET_CRAFT_COUNT_MIN or high < cfg.PUPPET_HIGH_GRADE_MIN:
        # 2026-11-20：把「差多少」写出来（旧版只写「须多炼药」，玩家看不出还差几次）。
        return False, "火候不济，须多炼药（累计炼制 %d/%d 次，%s以上丹药 %d/%d 枚）" % (
            cnt, cfg.PUPPET_CRAFT_COUNT_MIN, cfg.PUPPET_HIGH_GRADE_FROM,
            high, cfg.PUPPET_HIGH_GRADE_MIN)
    return True, ""


def has_flame(p: dict) -> bool:
    """④ 异火闸（E26/E27/E28）：持有任一异火且强度>0（传承/Boss 掉落所得）。"""
    return any(int(v or 0) > 0 for v in (p.get("flames") or {}).values())


def gate_flame(p: dict) -> tuple[bool, str]:
    if not has_flame(p):
        return False, "尚未持有异火，须宗门传承或降服异火"
    return True, ""


def mp_endurance_ok(p: dict, tier: int) -> tuple[bool, str]:
    """⑤ 斗气续航（E29/E30/E45）：按天烧，斗气池须撑得起全程总量。

    斗气见底则中断、材料不退（由炼制流程层执行）。
    """
    from . import core  # 延迟导入避免环
    need = core.mp_max(p) * float(cfg.PUPPET_MP_FACTOR.get(int(tier), 1.0))
    cur = int(p.get("mp", 0) or 0)
    if cur < need:
        return False, "斗气不足以撑完炼制（需 %d，现 %d），须突破或用回气丹" % (need, cur)
    return True, ""


def puppet_gate(p: dict, tier: int, need_mp: bool = True) -> dict:
    """五道资格总判定（开通 + 档次）。返回 {ok, reason, missing:[...]}。"""
    checks = [
        ("境界", gate_realm(p, tier)),
        ("灵魂境界", gate_soul(p)),
        ("控火", gate_fire_ctrl(p)),
        ("异火", gate_flame(p)),
    ]
    if need_mp:
        checks.append(("斗气", mp_endurance_ok(p, tier)))
    missing = [(name, reason) for name, (ok, reason) in checks if not ok]
    return {"ok": not missing, "missing": [m[0] for m in missing],
            "reason": "；".join(m[1] for m in missing)}


# ---------------------------------------------------------------------------
# 五维材料分档 + 木桶判档（2026-11-20 多配方）
#
# 原著第900章：「妖傀分三等，天地人，等级之差，取决炼制材料以及炼制之火」
# 原著第906章：「天为金，地为银，人为铜」；地妖傀 =「七星斗宗强者的躯体 +
#   一枚七阶魔核 + 一道斗宗强者的灵魂」，天妖傀 = 斗尊之躯 / 高阶魔核 / 斗尊灵魂。
#
# 所以档位不是「首炼恒人级」，而是**最弱那一环**决定——拿斗尊之躯配五阶魔核，
# 也只能出人级。这是把实现修回原著，不是加戏。
# ---------------------------------------------------------------------------
#: 四个耗材维（火不耗材，只判档）
SLOT_KINDS = ("body", "core", "soul", "metal")

#: 档位 → 傀儡 tier 的换算：tier = min(五维) − 1，封顶 2（人/地/天）
_TIER_CAP = 2


def _tier_table(kind: str) -> dict:
    return {
        "body": cfg.PUPPET_BODY_TIERS,
        "core": cfg.PUPPET_CORE_TIERS,
        "soul": cfg.PUPPET_SOUL_TIERS,
        "metal": cfg.PUPPET_METAL_TIERS,
    }[kind]


def slot_tier(kind: str, name: str) -> int:
    """材料名 → 档位（1..4）。不在表里返回 0（视为缺料，不许开炉）。"""
    row = _tier_table(kind).get(str(name or ""))
    return int(row[0]) if row else 0


def slot_row(kind: str, name: str) -> tuple:
    """材料名 → 该维原始行（躯体含境界/星级、魔核含阶与属性、灵魂含强度）。"""
    return tuple(_tier_table(kind).get(str(name or "")) or ())


def fire_grade(p: dict) -> dict:
    """炼制之火档位（不消耗）：取玩家已收服火里最高一档。

    榜上异火按 ``cfg.FLAME_TIER`` 分 S/A/B；榜外兽火/天然火按 B 档。
    帝炎走 ``p["diyan"]`` 成就位（不进 flames），单独算 S 档。
    """
    best = (0, 7, "")
    for name, lv in (p.get("flames") or {}).items():
        if int(lv or 0) <= 0:
            continue
        grade, ft = cfg.PUPPET_FIRE_GRADES.get(
            str(cfg.FLAME_TIER.get(str(name), "B")), (1, 7))
        if grade > best[0]:
            best = (grade, ft, str(name))
    if p.get("diyan"):
        grade, ft = cfg.PUPPET_FIRE_GRADES["S"]
        if grade > best[0]:
            best = (grade, ft, cfg.FLAME_DIYAN)
    return {"grade": best[0], "fire_tier": best[1], "name": best[2] or "斗气火焰",
            "attr": "火"}


def fire_grade_of_tier(fire_tier: int) -> int:
    """反查：写库的 fire_tier（7/8/9）→ 档位（1/2/3）。"""
    for grade, ft in cfg.PUPPET_FIRE_GRADES.values():
        if int(ft) == int(fire_tier or 0):
            return int(grade)
    return 1 if int(fire_tier or 0) <= 7 else 2


def recipe_tier(slots: dict) -> int:
    """**木桶判档唯一口径**：tier = min(躯体/魔核/灵魂/金属/火 五档) − 1，封顶 2。

    任一维缺失（档位 0）返回 ``-1``——表示这组材料不可开炉，绝不静默当人级。
    """
    tiers = [int((slots or {}).get(k) or 0) for k in SLOT_KINDS]
    tiers.append(int((slots or {}).get("fire") or 0))
    low = min(tiers)
    if low <= 0:
        return -1
    return max(0, min(_TIER_CAP, low - 1))


def recipe_slots(recipe: dict, p: dict | None = None) -> dict:
    """配方 → 五维档位（火档取玩家实况；不传 p 则用配方写死的火档字母）。"""
    r = recipe or {}
    if p is not None:
        fire = int(fire_grade(p)["grade"])
    else:
        fire = int(cfg.PUPPET_FIRE_GRADES.get(str(r.get("fire") or "B"), (1, 7))[0])
    return {"body": slot_tier("body", r.get("body")),
            "core": slot_tier("core", r.get("core")),
            "soul": slot_tier("soul", r.get("soul")),
            "metal": slot_tier("metal", r.get("metal")),
            "fire": fire}


def recipe_materials(recipe: dict) -> Dict[str, int]:
    """配方 → {材料名: 数量}（四维耗材各一，火不耗材）。"""
    r = recipe or {}
    out: Dict[str, int] = {}
    for k in SLOT_KINDS:
        name = str(r.get(k) or "")
        if name:
            out[name] = out.get(name, 0) + 1
    return out


def recipe_by_name(name: str) -> Optional[dict]:
    return cfg.PUPPET_RECIPE_BY_NAME.get(str(name or ""))


# ---------------------------------------------------------------------------
# 自选材料炼制（2026-11-20 第十轮）
#
# 玩家手里攥着好几具躯体和魔核时，固定配方不够用：他要用**自己指定的**
# 四样材料开炉。这里只做「文本 → 临时配方」的解析，判档仍走 recipe_tier、
# 耗材仍走 recipe_materials、落库仍走 make_puppet —— 三条红线一个都不能绕。
# ---------------------------------------------------------------------------
CUSTOM_KINDS = ("body", "core", "soul", "metal")
CUSTOM_LABELS = ("躯体", "魔核", "灵魂", "金属")


def custom_material_help() -> str:
    """「炼制傀儡 自定」的帮助：列出四维可选材料与档位。"""
    lines = ["自定炼制：炼制傀儡 自定 <躯体> <魔核> <灵魂> <金属>"]
    for kind, label in zip(CUSTOM_KINDS, CUSTOM_LABELS):
        tbl = _tier_table(kind)
        names = "、".join("%s(%d档)" % (n, int(v[0])) for n, v in tbl.items())
        lines.append("　%s：%s" % (label, names))
    lines.append("　炼制之火按你已收服的最高档自动取，不消耗。")
    return "\n".join(lines)


def custom_recipe_from_arg(arg: str, p: Optional[dict] = None) -> tuple[Optional[dict], str]:
    """解析「自定 <躯体> <魔核> <灵魂> <金属>」→ (临时配方, 错误/帮助)。

    ``err`` 非空且 ``recipe`` 为空：要么是帮助文本（以「自定炼制：」开头），
    要么是错误提示 —— 命令层直接回给玩家，不许继续走到扣料。
    ``p`` 只用于火档（与 ``recipe_slots(rec, p)`` 同一口径）；火不耗材、不自选。
    """
    tail = str(arg or "").strip()
    parts = tail.split()
    if parts and parts[0] in ("自定", "自定?", "自定？"):
        parts = parts[1:]
    if not parts or parts[0] in ("?", "？", "帮助"):
        return None, custom_material_help()
    if len(parts) != 4:
        return None, ("自定炼制需按序给 4 样材料：躯体 魔核 灵魂 金属。"
                      "发「炼制傀儡 自定」看清单。")
    for kind, name, label in zip(CUSTOM_KINDS, parts, CUSTOM_LABELS):
        if slot_tier(kind, name) <= 0:
            return None, ("「%s」不是有效的%s材料。发「炼制傀儡 自定」看清单。"
                          % (name, label))
    # ⚠️ 火档必须写玩家实况，不能用占位 B：recipe_slots(rec) 在没有 p 的调用
    #    （如单测/卡面反查）会读这个字母，占位 B 会把木桶判档凭空拉低一档。
    g = int(fire_grade(p)["grade"]) if p else 0
    letter = "S" if g >= 3 else ("A" if g == 2 else "B")
    rec = {
        "name": "自定·%s" % str(parts[0])[:2],
        "body": parts[0], "core": parts[1], "soul": parts[2], "metal": parts[3],
        "fire": letter, "note": "自选材料", "custom": True,
    }
    return rec, ""


def missing_materials(mats: Dict[str, int], recipe: dict) -> List[str]:
    """缺哪些料（带数量），用于「缺 寒铁×1、七阶魔核×1」这类如实提示。"""
    lack = []
    for name, q in recipe_materials(recipe).items():
        have = int((mats or {}).get(name, 0))
        if have < int(q):
            lack.append("%s×%d" % (name, int(q) - have) if have else "%s×%d" % (name, q))
    return lack


def craftable_recipes(p: dict, mats: Dict[str, int]) -> List[dict]:
    """当前材料能凑齐的配方，附带缺料信息与**两个档位**。

    2026-11-20 关键区分（漏了会显示错档）：
      * ``tier`` —— **实际出档**（火按玩家实况）：材料齐但只有 A 火，天级配方只能出地级；
      * ``tier_nominal`` —— **配方标称档**（火按配方写死的字母）：列表分组用它，
        否则「金身傀」会被玩家的 A 火误导显示成地级，玩家以为配方写错了。

    排序按 cfg 顺序（同档内从省料排到贵重），不按名字——按名字会让「北王傀」
    排在地妖傀前面，自动选配方时白烧一份北龙王之躯。
    """
    idx = {r["name"]: i for i, r in enumerate(cfg.PUPPET_RECIPES)}
    out = []
    for r in cfg.PUPPET_RECIPES:
        lack = missing_materials(mats, r)
        out.append({"recipe": r, "lack": lack, "ok": not lack,
                    "tier": recipe_tier(recipe_slots(r, p)),
                    "tier_nominal": recipe_tier(recipe_slots(r))})
    out.sort(key=lambda x: (-int(x["tier"]), idx.get(x["recipe"]["name"], 999)))
    return out


def best_recipe(p: dict, mats: Dict[str, int]) -> Optional[dict]:
    """自动选配方：材料齐、且境界够的最高档；同档取 cfg 顺序最前一条。

    若材料齐的配方**全都**境界不够，仍返回其中最高档的那条——让 ``puppet_gate``
    报出「境界不足，须斗宗以上」这类准确原因，而不是含糊地说「材料不足」。
    """
    items = [x for x in craftable_recipes(p, mats) if x["ok"] and int(x["tier"]) >= 0]
    if not items:
        return None
    ok = [x for x in items if gate_realm(p, int(x["tier"]))[0]]
    # craftable_recipes 已按「档位高→低、同档按 cfg 顺序」排好，稳定筛选后取首条
    return (ok or items)[0]


def match_recipe(puppet: dict) -> Optional[dict]:
    """反查这具傀儡是按哪条配方炼的（材料完全匹配）；对不上返回 None。"""
    q = puppet or {}
    pairs = (("body", q.get("body_name")), ("core", core_name_of(q)),
             ("soul", q.get("soul_source")), ("metal", q.get("metal_name")))
    for r in cfg.PUPPET_RECIPES:
        if all(str(r.get(k) or "") == str(v or "") for k, v in pairs):
            return r
    return None


def core_name_of(puppet: dict) -> str:
    """魔核材料名：库只存阶位，按阶反查名著（老档 core_tier=7 → 七阶魔核）。"""
    tier = int((puppet or {}).get("core_tier", 0) or 0)
    for name, (_t, ct, _a) in cfg.PUPPET_CORE_TIERS.items():
        if int(ct) == tier:
            return name
    return ""


def first_craft_tier() -> int:
    """**已废弃**（2026-11-20）：档位改由五维材料木桶决定，见 ``recipe_tier``。

    保留函数只为不动老调用方的 import；新代码一律不许调它——
    ``test_doupo_puppet_recipes.py`` 有一条源码红线钉住这件事。
    """
    return 0


def body_fresh_ok(body: dict, now_ts: Optional[float] = None) -> bool:
    """E10：躯体须冰封保鲜，过期腐烂不得入料。"""
    if not body or not body.get("body_fresh", True):
        return False
    until = float(body.get("body_fresh_until") or 0)
    if until <= 0:
        return True
    return (now_ts or time.time()) <= until


def craft_checks(p: dict, mats: Dict[str, int], body: dict,
                 tier: int = 0, need: Optional[Dict[str, int]] = None) -> dict:
    """炼制前置校验（挂 §3.6 五步），返回 {ok, reason}。

    mats: 傀儡专用材料表（真实列 store 提供）；body: 躯体 dict。
    ``need``（2026-11-20）：按配方传本次要扣的材料，缺省仍是旧四主材——
    多配方后每条配方的耗材不同，不能再用一份写死的清单去校验。
    """
    if not body_fresh_ok(body):
        return {"ok": False, "reason": "躯体已腐烂，须冰封保鲜的尸身（E10）"}
    if not body.get("mind_stripped", False):
        return {"ok": False, "reason": "灵魂神智未炼尽，入料必反噬（E15）"}
    if not p.get("puppet_lore", False) and not p.get("lore", False):
        # 传承标记在独立资格表；此处由调用方把 lore 合入 p 的临时键
        return {"ok": False, "reason": "尚未习得傀儡之技（无竹简，E01）"}
    req = dict(need) if need else {
        "强者躯体": 1, "火属性魔核": 1, "无主灵魂": 1, "暗金玄铁": 1}
    lack = []
    for n, q in req.items():
        have = int(mats.get(n, 0))
        if have < int(q):
            lack.append("%s×%d" % (n, int(q) - have))
    if lack:
        return {"ok": False, "reason": "材料不足：" + "、".join(lack)}
    return {"ok": True, "reason": ""}


def make_puppet(p: dict, body: dict, core_info: dict, soul: dict,
                metal: dict, fire: dict, origin: str = "self") -> dict:
    """构造一具傀儡的真实列数据 dict（供 store.add_puppet 落库）。

    2026-11-20：档位改由**五维材料木桶**判定（原著第900章「等级之差，取决炼制
    材料以及炼制之火」），不再「首炼恒人级」。材料名不在分档表里（遗迹残傀、
    测试手搓）时回落人级，保持旧行为。

    E03：炼制之火是独立定档变量；E14：魔核属性须配炼制之火；
    E18：金属溶液淬体。
    """
    b_name = str(body.get("body_name") or "")
    c_name = str(core_info.get("core_name") or "") or core_name_of(core_info)
    s_name = str(soul.get("soul_source") or "")
    m_name = str(metal.get("metal_name") or "")
    slots = {
        "body": slot_tier("body", b_name),
        "core": slot_tier("core", c_name),
        "soul": slot_tier("soul", s_name),
        "metal": slot_tier("metal", m_name),
        "fire": int(fire.get("fire_grade") or 0)
                or fire_grade_of_tier(fire.get("fire_tier")),
    }
    tier = recipe_tier(slots)
    if tier < 0:
        tier = 0
    tier_name, skin = cfg.PUPPET_TIER_BY_IDX[tier]
    b_row = slot_row("body", b_name)
    body_realm = str(body.get("body_realm") or (b_row[1] if len(b_row) > 1 else ""))
    body_star = int(body.get("body_star") or (b_row[2] if len(b_row) > 2 else 0))
    return {
        "name": "%s·%s" % (tier_name[:-2] or "傀儡", str(body.get("body_name") or "玄")[:1]),
        "tier": tier, "tier_name": tier_name, "skin": skin,
        "body_name": b_name,
        "body_realm": body_realm,
        "body_star": body_star,
        "body_fresh": bool(body.get("body_fresh", True)),
        "body_fresh_until": float(body.get("body_fresh_until") or 0),
        "core_tier": int(core_info.get("core_tier") or 0),
        "core_attr": str(core_info.get("core_attr") or ""),
        "soul_source": s_name,
        "soul_strength": int(soul.get("soul_strength") or 0),
        "mind_stripped": bool(soul.get("mind_stripped", False)),
        "metal_name": m_name,
        "metal_grade": int(metal.get("metal_grade") or 0),
        "fire_tier": int(fire.get("fire_tier") or 0),
        "fire_attr": str(fire.get("fire_attr") or ""),
        "purity": 0, "bonded": True, "awakened": False, "origin": origin,
        "damaged": False, "born": time.time(),
    }


# ---------------------------------------------------------------------------
# 战斗集成（红线：唯一期望输出函数）
# ---------------------------------------------------------------------------
def puppet_realm_lv(puppet: Optional[dict]) -> int:
    """傀儡战力对应的等级 —— 由**躯体**境界决定（第900章「等级之差取决炼制材料」）。

    折算规则来自第757处：用「一名七星斗宗强者的躯体」（第906章）炼出的地妖傀，
    「恐怕不会比一名**五星乃至六星**的斗宗强者弱」⇒ 战力 = 躯体境界 − 2 星。
    ``body_star=0``（强者躯体 / 斗尊强者之躯未标小星）按一星算。

    认不出躯体境界（老存档 ``body_realm`` 为空）时回落
    ``cfg.PUPPET_TIER_REALM_LV[tier]`` —— 该档**标准躯体**折算出的等级，
    与同一档新傀同料同档，不会因为缺字段就掉成废铁。
    """
    pu = puppet or {}
    tier = max(0, min(2, int(pu.get("tier", 0) or 0)))
    realm = str(pu.get("body_realm") or "")
    try:
        idx = cfg.REALM_NAMES.index(realm)
    except ValueError:
        return int(cfg.PUPPET_TIER_REALM_LV[tier])
    star = int(pu.get("body_star") or 0)
    n = max(1, min(9, star if star > 0 else 1))   # 未标小星 = 一星
    lv = cfg.realm_lv(idx) + max(0, n - 1 - int(cfg.PUPPET_BODY_STAR_PENALTY))
    # 封在该境界之内：傀儡不会因为躯体小星高就跨到下一个大境界去
    return min(lv, cfg.realm_lv(idx) + 8)


def _round_output_at(lv: int) -> float:
    """某等级的标尺玩家每回合期望输出（卡面「战力对标」反查专用）。"""
    from . import core
    lv = max(1, min(int(cfg.MAX_LV), int(lv)))
    return _player_round_output(core.tower_ref_player(lv))


def puppet_effective_lv(puppet: Optional[dict], p: Optional[dict] = None) -> int:
    """**当前实际**战力对应的等级（不是材料境界）。

    卡面的「斗宗级」必须用它而不是 ``puppet_realm_lv()``：被
    ``PUPPET_ATK_CAP`` 压住时实际发挥会低于材料境界，标签得跟着降 ——
    否则就是「写着斗宗级、打出来 1734」的假标签，比不写更糟。
    """
    if not puppet:
        return 0
    # atk 已含材料系数（见 puppet_attack），反查直接拿它比标尺输出，**别再除一次**。
    target = puppet_attack(puppet, p)
    lo, hi = 1, int(cfg.MAX_LV)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if _round_output_at(mid) <= target:
            lo = mid
        else:
            hi = mid - 1
    # 二分只保证「≤ target 的最大等级」，若等级曲线有抖动，就近取更接近的一侧
    if lo < int(cfg.MAX_LV):
        if abs(_round_output_at(lo + 1) - target) < abs(_round_output_at(lo) - target):
            lo += 1
    return lo


def puppet_realm_label(puppet: Optional[dict], p: Optional[dict] = None) -> str:
    """战力境界的玩家可见称呼（「斗宗级」）—— 卡面与文档同一口径。

    ``p`` 给了就看**实际发挥**：发挥不满（被 ``PUPPET_ATK_CAP`` 压住）时标实际
    档位并注明原因，否则一律标材料境界 —— 这样标签永远与「肉身攻击」那个数字
    自洽，不会出现「写着斗宗级、打出来一千七」的假标签。
    注意「同境界内被压」不降档：斗尊躯被压到斗尊初段，仍叫斗尊级。

    ⚠️ 返回值**不许带括号**：卡面把它塞进「肉身攻击 N（…）」里，再套一层就成了
        ``（斗皇级（受你境界所限），不吃斗气/斗技）`` 这种嵌套括号（实测踩过）。
       用「·」断句。
    """
    if not puppet:
        return "—"
    mat_lv = puppet_realm_lv(puppet)
    mat_name = cfg.realm_name(mat_lv)
    if p is None:
        return "%s级" % mat_name
    eff = puppet_effective_lv(puppet, p)
    if cfg.realm_index(eff) >= cfg.realm_index(mat_lv):
        return "%s级" % mat_name
    return "%s级 · 受你境界所限" % cfg.realm_name(eff)


def _ref_player(tier: int, puppet: Optional[dict] = None) -> dict:
    """无玩家上下文时的「标尺玩家」：走 core.tower_ref_player，与爬塔同一套标尺。

    不另造一份假玩家——两份标尺一定会漂，卡面与实战就成了两张皮。
    给了 puppet 就按**它的躯体境界**取标尺，保证同一具傀儡在任何调用点算出的
    战力都是同一个数（卡面 vs 实战 vs 怪血反解）。
    """
    from . import core  # 延迟导入避免环
    if puppet:
        lv = puppet_realm_lv(puppet)
    else:
        lv = int(cfg.PUPPET_TIER_REALM_LV[max(0, min(2, int(tier)))])
    return core.tower_ref_player(lv)


def _player_round_output(p: dict) -> float:
    """玩家平均每回合期望原始输出（未乘减伤），与 damage_schedule 同口径。

    取 ``TTK_PLAYER`` 个回合的均值而不是第一回合：第一回合玩家斗气满、放的是
    最高倍率斗技，用它当锚会让傀儡在斗气耗尽后强得离谱（实测 lv81 首回合
    111493 vs 续航期 28389，差 4 倍）。
    """
    from . import core  # 延迟导入避免环（core.damage_schedule 不回调本模块）
    n = max(1, int(round(float(cfg.TTK_PLAYER))))
    try:
        sch = core.damage_schedule(p, n)
        return max(1.0, float(sum(sch) / len(sch)))
    except Exception:  # noqa: BLE001  脏存档兜底，绝不因算不出锚就返回 0
        try:
            return max(1.0, float(core.stats(p)["力量"] * cfg.WEAPON_BASE_MULT))
        except Exception:  # noqa: BLE001
            return 1.0


def _material_mult(puppet: dict) -> float:
    """材料系数（同档之内的差异）：基准 core7 / soul900 / metal2 / fire7 = 1.00。

    老傀儡全存这四个基准值 ⇒ 系数恰为 1.0，与「同档新傀同料」完全一致。
    """
    core_t = int(puppet.get("core_tier") or 7)
    soul = int(puppet.get("soul_strength") or 900)
    mg = int(puppet.get("metal_grade") or 2)
    ft = int(puppet.get("fire_tier") or 7)
    m = (1.0
         + (core_t - 7) * cfg.PUPPET_CORE_STEP
         + (soul - 900) * cfg.PUPPET_SOUL_STEP
         + (mg - 2) * cfg.PUPPET_METAL_MULT_STEP
         + (ft - 7) * cfg.PUPPET_FIRE_MULT_STEP)
    return max(0.5, float(m))


def _damage_atk_mult(puppet: Optional[dict]) -> float:
    """P1-1 战损攻击倍率（唯一口径）：damaged 时 ×0.8，否则 1.0。

    卡面 ``puppet_attack`` 与战斗 ``puppet_full_attack`` 都乘它 —— 两处同源，
    避免「卡面写着受损降攻、实战照旧满伤」两张皮。战损乘在基数上，CAP 仍在
    ``puppet_expected_damage`` 末端落一次，降效只会更低、不会顶破封顶。
    """
    if puppet and puppet.get("damaged"):
        return float(cfg.PUPPET_DAMAGED_ATK_MULT)
    return 1.0


def puppet_attack(puppet: Optional[dict], p: Optional[dict] = None) -> float:
    """傀儡肉身攻击（E32/E33/E35）：不吃 mp/斗技，纯躯体+魔核+灵魂+材料。

    **2026-11-20 第三轮：锚躯体境界，档位只封顶。**

    三轮演进，别再改回去：
      ① 初版 ``(120/900/4200 + core×40 + soul×0.15) × 0.42`` 是绝对常数，lv81
         玩家身上只有 224.7，占其每回合输出 0.2% —— 用户原话「低得离谱」。
      ② 第二轮改成 ``玩家每回合输出 × (0.15/0.30/0.50)``：比例有了，但 lv81 人级
         也只有 5.5 千，与原著「地妖傀 = 五星六星斗宗」对不上；反过来给同境界
         满额又让 lv20 到 1134%（实测崩坏）。
      ③ 本轮（用户「应该有原著中说的，有对应斗宗或者斗尊级别的战力」）：

         ``min(躯体境界标尺输出, 玩家每回合输出 × PUPPET_ATK_CAP[档]) × 材料系数``

     第一项给「斗宗/斗尊级战力」（来自躯体，见 ``puppet_realm_lv``），第二项是
     「不抢风头」的安全阀（玩家比傀儡弱时往下压），两者取小即同时满足两条红线。

    ``p=None``（旧脚本只给 puppet dict）时直接给躯体境界标尺值 —— 即「这具傀儡
    的标配战力」，不掺玩家强弱；卡面与实战都传 p，所以不会出现两张皮。
    """
    if not puppet:
        return 0.0
    tier = max(0, min(2, int(puppet.get("tier", 0) or 0)))
    # ⚠️ 材料系数必须乘在**境界标尺**上、而不是乘在 min() 之后：
    #    乘在后面会让好材料把「不抢风头」的封顶撑破（实测天级 core9/soul2400
    #    /metal4/fire9 的 1.31 倍直接把 100% 顶成 131%）。
    realm_out = _player_round_output(_ref_player(tier, puppet)) \
        * _material_mult(puppet) * _damage_atk_mult(puppet)
    if p is None:
        return realm_out
    return min(realm_out,
               _player_round_output(p) * float(cfg.PUPPET_ATK_CAP[tier]))


def puppet_full_attack(puppet: Optional[dict]) -> float:
    """躯体境界满配战力（含材料系数，**不含**玩家封顶/结阵/异火）。

    战斗口径 ``puppet_expected_damage`` 拿它当基数再叠结阵与异火，最后一次性
    落在 ``PUPPET_ATK_CAP`` 上 —— 封顶必须管**最终值**，否则结阵 ×1.30 会把
    「天级不超过自己 95%」顶成 123%（实测）。
    """
    if not puppet:
        return 0.0
    tier = max(0, min(2, int(puppet.get("tier", 0) or 0)))
    return _player_round_output(_ref_player(tier, puppet)) \
        * _material_mult(puppet) * _damage_atk_mult(puppet)


def puppet_expected_damage(puppet: Optional[dict], formation_n: int = 0,
                           flame_mult: float = 1.0,
                           p: Optional[dict] = None) -> float:
    """傀儡每回合期望输出——**唯一函数**。

    ``fight`` 注入傀儡击、``damage_schedule``/``monster_scaled`` 计入反解都必须
    调它，同涨同落。

    ``flame_mult``（2026-11-19）：B 线傀儡异火的攻击倍率（1.0+加成），由命令层从
    玩家已收服的火聚合传入（flame_bonus.puppet_atk_bonus）。**必须两处同源**。

    ``p``（2026-11-20 第二轮）：锚定玩家输出用。**同一个 p 必须同时喂给
    ``monster_scaled(puppet_bonus=...)`` 与 ``fight(puppet=...)``**，否则怪血按
    一个锚变厚、实战按另一个锚出手，就是两张皮。默认 None → 标尺玩家。
    """
    if not puppet:
        return 0.0
    tier = max(0, min(2, int(puppet.get("tier", 0) or 0)))
    atk = puppet_full_attack(puppet)
    # 结阵加成（2026-11-20 第二轮加上限）：`formation_count` 数的是玩家**拥有的
    # 全部**傀儡，人级材料便宜，不封顶就能靠堆垃圾傀儡白拿 +60% → 封顶 3 具。
    n_eff = min(int(getattr(cfg, "PUPPET_FORMATION_MAX", 3)), max(0, formation_n))
    bonus = 1.0 + max(0, n_eff - 1) * float(getattr(cfg, "PUPPET_FORMATION_STEP", 0.15))
    total = atk * bonus * max(1.0, float(flame_mult))
    # 🚨 封顶只在这里落一次：管的是**最终值**（结阵/异火叠完之后）。
    #    若改回在 puppet_attack 里封顶，结阵 ×1.30 会把「天级 ≤ 自己 95%」
    #    顶成 123%（实测），「不抢风头」当场破功。
    if p is not None:
        total = min(total,
                    _player_round_output(p) * float(cfg.PUPPET_ATK_CAP[tier]))
    return total


def guard_profile(puppet: Optional[dict], p: Optional[dict] = None,
                  formation_n: int = 0) -> dict:
    """傀儡护主面板（**唯一口径**）：气血 / 护甲 / 挡伤概率 / 大额阈值。

    ``p`` 是**玩家**（拿他的气血上限与防御当基准），None 时回落该档标尺玩家
    （与 ``puppet_attack`` 同一套 ``_ref_player``，不另造假玩家）。

    卡面（`cmd_puppet`）与 ``core.fight()`` 必须都调本函数 —— 否则「卡面写的
    挡伤概率」与「战斗里真掷的骰子」就是两张皮（与 ``puppet_expected_damage``
    同一条红线，谁只改一处守门必红）。

    返回值字段：
      * ``hp_max``   傀儡气血上限（被挡下的伤害经护甲减免后从这里扣）
      * ``armor``    傀儡护甲（喂 ``core.mitigation()`` 得受击乘数）
      * ``chance``   本场每次「大额伤害」掷挡伤骰的命中率（已封顶）
      * ``threshold``「大额」判定线（原始伤害 ≥ 此值才掷骰）
      * ``leak``     挡下后玩家**自己仍承受**的比例（不抢风头）
      * ``tier``     0/1/2
    """
    if not puppet:
        return {"hp_max": 0, "armor": 0, "chance": 0.0,
                "threshold": 0, "leak": 0.0, "tier": 0, "awakened": False,
                "damaged": False}
    from . import core  # 延迟导入避免与 core 循环

    tier = max(0, min(2, int(puppet.get("tier", 0) or 0)))
    anchor = p if p is not None else _ref_player(tier, puppet)
    st = core.stats(anchor)
    mm = _material_mult(puppet)
    hp_max = max(1, int(int(st["体力"]) * float(cfg.PUPPET_HP_RATIO[tier]) * mm))
    # 护甲由**目标穿透率**反解，不按玩家防御比例放大（详见 cfg 里那段踩坑注释）：
    #   take = MITIGATION_W/(MITIGATION_W+armor)  ⇒  armor = MITIGATION_W×(1/take − 1)
    # 这样「挡一次吃掉多少」与玩家等级脱钩 —— lv10 与 lv81 的报废节奏一致，
    # 而血量仍随玩家体力涨，档位越高越厚。
    _take = float(cfg.PUPPET_ARMOR_TAKE[tier])
    armor = max(0, int(float(cfg.MITIGATION_W) * (1.0 / max(1e-6, _take) - 1.0) * mm))
    # P1-1：战损时护甲减半 —— 与攻击 ×0.8 同源于 puppet 的 damaged 字段。
    if puppet.get("damaged"):
        armor = int(armor * float(cfg.PUPPET_DAMAGED_ARMOR_MULT))
    n_eff = min(int(getattr(cfg, "PUPPET_FORMATION_MAX", 3)), max(0, int(formation_n)))
    chance = (float(cfg.PUPPET_GUARD_CHANCE[tier])
              + max(0, n_eff - 1) * float(cfg.PUPPET_FORMATION_GUARD_STEP))
    chance = min(float(cfg.PUPPET_GUARD_CAP), max(0.0, chance))
    _th = max(1, int(int(st["体力"]) * float(cfg.PUPPET_GUARD_THRESHOLD)))
    # P0-2：有灵＝主动拦截，门槛在此处（唯一口径）下调，卡面与战斗同步。
    if puppet.get("awakened"):
        _th = max(1, int(_th * float(cfg.PUPPET_AWAKEN_GUARD_THRESHOLD_MULT)))
    return {
        "hp_max": hp_max, "armor": armor, "chance": chance,
        "threshold": _th,
        "leak": float(cfg.PUPPET_GUARD_LEAK),
        "tier": tier,
        # P0-2：有灵行为由战斗侧读取（小额也拦、残血不打折），卡面也读同一个
        # 字段做说明，避免「写了 awakened 却只改卡面」的白守。
        "awakened": bool(puppet.get("awakened")),
        # P1-1：战损透出，卡面据此显示「待雷淬修复」，战斗同源读 armor/atk。
        "damaged": bool(puppet.get("damaged")),
    }


def guard_note(puppet: dict, raw: int, soak: int, taken: int,
               hp_left: int, hp_max: int, awakened: bool = False) -> str:
    """护主成功的一句战报文案（挂到「兽击」条目的 notes 上）。

    **不新增 log kind**：回看卡的 kind 白名单与配色是按既有条目写的，
    多一个 kind 要动渲染层与快照测试，收益为零。挂在兽击条目上，玩家看到的是
    「这一下本来要打我 X，傀儡挡了 Y，我自己挨了 Z」——三个数都在，才不算隐式数值。

    ⚠️ 文案内**不许用括号**：这段会整句塞进战报的 `招式（tags）` 里，再套一层
    括号就成了 `兽击（…抢身挡下 6868（自损 5360））`，两层中文括号读起来像嵌套
    报错。用逗号断句，外层括号只留一层。
    """
    return "%s%s 抢身挡下 %d，自损 %d，余血 %d/%d" % (
        "心念一动，" if awakened else "",
        str(puppet.get("name") or "傀儡"), int(soak), int(taken),
        max(0, int(hp_left)), int(hp_max))


def formation_count(puppets: List[dict]) -> int:
    """E36/E38：同场傀儡数≥2 才算结阵。

    返回**参与结阵的具数**，已按 ``cfg.PUPPET_FORMATION_MAX`` 封顶（2026-11-20
    第二轮）。封顶放在这里而不是只放公式里，是因为命令层/卡面也用这个数报
    「结阵 N 具」—— 报的数与真正吃到的加成必须是同一个数，否则又是一张皮。
    """
    n = len([x for x in (puppets or []) if x])
    return min(int(getattr(cfg, "PUPPET_FORMATION_MAX", 3)), n)


def formation_burst(puppet: Optional[dict], roster: Optional[list] = None,
                    formation_n: Optional[int] = None) -> dict:
    """P0-1：3 具同档成阵 → 领头**临时升一档**，返回只读副本。

    原著「收服」章：十具地妖傀成阵 → 领头暂化金色天妖傀，战后金光消散复为地妖傀。
    本作结阵封顶 3 具，故门槛取 3，且必须同档（原著「十具地妖傀」）。

    🚨 三条红线：
      1. 绝不改传入的 dict —— 它是 ``PuppetStore`` 读出的存档数据，就地改 tier
         会随下一次写库变成**永久升档白嫖**；
      2. 天级无更高档时不升档，紫皮变体由既有结阵护主步进表达，输出不叠倍率；
      3. 副本只在 ``fight()`` 生命周期内存活，战后丢弃，不进任何持久化路径。

    返回值：升档副本（带 ``_formation_burst=True``）或原 dict（未成阵）。
    """
    if not puppet:
        return puppet
    tier = max(0, min(2, int(puppet.get("tier", 0) or 0)))
    if tier not in tuple(getattr(cfg, "PUPPET_FORMATION_BURST_TIERS", (0, 1))):
        return puppet
    need = int(getattr(cfg, "PUPPET_FORMATION_BURST_N", 3))
    if formation_n is None:
        n = formation_count(roster)
    else:
        n = min(int(getattr(cfg, "PUPPET_FORMATION_MAX", 3)), max(0, int(formation_n)))
    if n < need:
        return puppet
    same = sum(1 for x in (roster or []) if x
               and max(0, min(2, int(x.get("tier", 0) or 0))) == tier)
    if same < need:
        return puppet
    up = dict(puppet)
    up["tier"] = min(2, tier + 1)
    up["tier_name"], up["skin"] = cfg.PUPPET_TIER_BY_IDX[up["tier"]]
    up["_formation_burst"] = True
    return up


def puppet_log_entry(puppet: dict, dmg: int, formation_n: int = 0) -> dict:
    """傀儡击 log 条目；字段由 _mark 补齐，渲染层只 .get()。"""
    return {
        "dmg": int(dmg), "kind": "傀儡击", "puppet": True,
        "helper": str(puppet.get("name") or "傀儡"),
        "tier": int(puppet.get("tier") or 0),
        "skin": str(puppet.get("skin") or "铜"),
        "awakened": bool(puppet.get("awakened")),
        "formation": max(0, formation_n),
        "skill": False, "move": "傀儡击",
    }


# ---------------------------------------------------------------------------
# 雷淬升级 / 修复（E07/E08/E41/E42）
# ---------------------------------------------------------------------------
def temper(puppet: dict, gold_ok: bool = True) -> dict:
    """一次雷淬：purity += 增量，达阈值则升档；天级封顶。返回新字段 dict。

    2026-11-20 修 bug：``PUPPET_TIER_UP_PURITY[tier]`` 语义是「**从 tier 升到
    tier+1** 所需的 purity」。旧表首元素是 0，导致 tier=0 时门槛为 0，第一次
    雷淬（purity 12）就直接跳地级；现表为 ``(60, 100)``，与索引语义对齐。
    同时改成 while 逐档判定：不再依赖「一次只升一档」的隐含假设。
    """
    cap = cfg.PUPPET_TIER_BY_IDX[2]
    if int(puppet.get("tier", 0)) >= 2:
        return {"purity": cfg.PUPPET_PURITY_MAX, "tier": 2,
                "tier_name": cap[0], "skin": cap[1],
                "damaged": False, "capped": True}
    purity = min(cfg.PUPPET_PURITY_MAX,
                 int(puppet.get("purity", 0)) + cfg.PUPPET_TEMPER_PURITY)
    tier = int(puppet.get("tier", 0))
    while tier < 2 and purity >= cfg.PUPPET_TIER_UP_PURITY[tier]:
        tier += 1
    tier_name, skin = cfg.PUPPET_TIER_BY_IDX[tier]
    return {"purity": purity, "tier": tier, "tier_name": tier_name, "skin": skin,
            "damaged": False, "capped": tier >= 2}
