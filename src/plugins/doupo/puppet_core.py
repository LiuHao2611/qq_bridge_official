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
# 炼制五步（E11-E21）—— 校验材料/条件，产出傀儡真实列 dict
# ---------------------------------------------------------------------------
def first_craft_tier() -> int:
    """E06/E07：首炼恒人级铜（0），与材料档次无关。材料只定成长上限。"""
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
                 tier: int = 0) -> dict:
    """炼制前置校验（挂 §3.6 五步），返回 {ok, reason}。

    mats: 傀儡专用材料表（真实列 store 提供）；body: 躯体 dict。
    """
    if not body_fresh_ok(body):
        return {"ok": False, "reason": "躯体已腐烂，须冰封保鲜的尸身（E10）"}
    if not body.get("mind_stripped", False):
        return {"ok": False, "reason": "灵魂神智未炼尽，入料必反噬（E15）"}
    required = {"强者躯体": 1, "火属性魔核": 1, "无主灵魂": 1, "暗金玄铁": 1}
    if not p.get("puppet_lore", False) and not p.get("lore", False):
        # 传承标记在独立资格表；此处由调用方把 lore 合入 p 的临时键
        return {"ok": False, "reason": "尚未习得傀儡之技（无竹简，E01）"}
    missing = [n for n, q in required.items() if int(mats.get(n, 0)) < q]
    if missing:
        return {"ok": False, "reason": "材料不足：" + "、".join(missing)}
    return {"ok": True, "reason": ""}


def make_puppet(p: dict, body: dict, core_info: dict, soul: dict,
                metal: dict, fire: dict, origin: str = "self") -> dict:
    """构造一具傀儡的真实列数据 dict（供 store.add_puppet 落库）。

    E03：炼制之火是独立定档变量；E14：魔核属性须配炼制之火；
    E06：首炼恒人级铜；E18：金属溶液淬体。
    """
    tier = first_craft_tier()
    tier_name, skin = cfg.PUPPET_TIER_BY_IDX[tier]
    return {
        "name": "%s·%s" % (tier_name[:-2] or "傀儡", str(body.get("body_name") or "玄")[:1]),
        "tier": tier, "tier_name": tier_name, "skin": skin,
        "body_name": str(body.get("body_name") or ""),
        "body_realm": str(body.get("body_realm") or ""),
        "body_star": int(body.get("body_star") or 0),
        "body_fresh": bool(body.get("body_fresh", True)),
        "body_fresh_until": float(body.get("body_fresh_until") or 0),
        "core_tier": int(core_info.get("core_tier") or 0),
        "core_attr": str(core_info.get("core_attr") or ""),
        "soul_source": str(soul.get("soul_source") or ""),
        "soul_strength": int(soul.get("soul_strength") or 0),
        "mind_stripped": bool(soul.get("mind_stripped", False)),
        "metal_name": str(metal.get("metal_name") or ""),
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
    """傀儡肉身攻击（E32/E33/E35）：不吃 mp/斗技，纯躯体+魔核+灵魂。"""
    if not puppet:
        return 0.0
    base = cfg.PUPPET_TIER_BASE[int(puppet.get("tier", 0))]
    core = int(puppet.get("core_tier", 0)) * cfg.PUPPET_CORE_K1
    soul = int(puppet.get("soul_strength", 0)) * cfg.PUPPET_SOUL_K2
    return (base + core + soul) * cfg.PUPPET_ATK_FACTOR


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
    """一次雷淬：purity += 增量，达阈值则升档；天级封顶。返回新字段 dict。"""
    if int(puppet.get("tier", 0)) >= 2:
        return {"purity": cfg.PUPPET_PURITY_MAX, "tier": 2,
                "tier_name": cfg.PUPPET_TIER_BY_IDX[2][0], "skin": cfg.PUPPET_SKINS[2],
                "damaged": False, "capped": True}
    purity = min(cfg.PUPPET_PURITY_MAX,
                 int(puppet.get("purity", 0)) + cfg.PUPPET_TEMPER_PURITY)
    tier = int(puppet.get("tier", 0))
    if tier < 2 and purity >= cfg.PUPPET_TIER_UP_PURITY[tier]:
        tier += 1
    tier_name, skin = cfg.PUPPET_TIER_BY_IDX[tier]
    return {"purity": purity, "tier": tier, "tier_name": tier_name, "skin": skin,
            "damaged": False, "capped": tier >= 2}
