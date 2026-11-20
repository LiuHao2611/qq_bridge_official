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
def puppet_attack(puppet: Optional[dict]) -> float:
    """傀儡肉身攻击（E32/E33/E35）：不吃 mp/斗技，纯躯体+魔核+灵魂。

    2026-11-20 多配方：**金属与炼制之火进公式**（原著第900章「等级之差，取决
    炼制材料以及炼制之火」）。此前两者只存库不进公式，同档傀儡毫无差别。

    基准点刻意取旧档值（``metal_grade=2`` / ``fire_tier=7``），故：
      * 老傀儡（全存 2 / 7）倍率恒为 1.0 → 数值逐字节不变；
      * 手搓 dict 缺这两个键、或库列为 0（未记录）→ 同样按基准算，不吃惩罚。
    """
    if not puppet:
        return 0.0
    base = cfg.PUPPET_TIER_BASE[int(puppet.get("tier", 0))]
    core = int(puppet.get("core_tier", 0)) * cfg.PUPPET_CORE_K1
    soul = int(puppet.get("soul_strength", 0)) * cfg.PUPPET_SOUL_K2
    metal_grade = int(puppet.get("metal_grade") or 2)
    fire_tier = int(puppet.get("fire_tier") or 7)
    metal_mult = 1.0 + (metal_grade - 2) * cfg.PUPPET_METAL_MULT_STEP
    fire_mult = 1.0 + (fire_tier - 7) * cfg.PUPPET_FIRE_MULT_STEP
    return (base + core + soul) * cfg.PUPPET_ATK_FACTOR * metal_mult * fire_mult


def puppet_expected_damage(puppet: Optional[dict], formation_n: int = 0,
                           flame_mult: float = 1.0) -> float:
    """傀儡每回合期望输出——**唯一函数**。

    ``fight`` 注入傀儡击、``damage_schedule`` 计入反解都必须调它，同涨同落。
    结阵（E36/E38）增益也在这里统一算，避免只加 fight 不进反解。

    ``flame_mult``（2026-11-19）：B 线傀儡异火的攻击倍率（1.0+加成），由命令层从
    玩家已收服的火聚合传入（flame_bonus.puppet_atk_bonus）。**必须两处同源**：
    怪血反解（commands 传给 monster_scaled）与实战傀儡击都吃同一个 bonus。
    默认 1.0 → 旧调用逐字节不变。
    """
    if not puppet:
        return 0.0
    atk = puppet_attack(puppet)
    bonus = 1.0 + max(0, formation_n - 1) * 0.15  # 结阵每人 +15% [调参]
    return atk * bonus * max(1.0, float(flame_mult))


def formation_count(puppets: List[dict]) -> int:
    """E36/E38：同场傀儡数≥2 才算结阵。"""
    return len([x for x in (puppets or []) if x])


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
