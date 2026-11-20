# -*- coding: utf-8 -*-
"""斗破苍穹 · 核心逻辑（纯函数，零 nonebot 依赖，可直接单测）。

约定：
  * 所有函数只就地改 player dict，持久化由调用方（commands 层）负责
  * 所有随机都走可注入的 rng(返回 0..1)，方便测试复现
  * 不 import nonebot / 不发消息 / 不读文件 —— 保证 test_core 能裸跑
"""

from __future__ import annotations

import copy
import json
import random
import re
import time

from . import config as cfg
from . import flame_bonus as _flame_bonus
from . import factions as fac
from . import skills as sk_mod
from . import skill_effects as fx
from . import weapon_data as wd
from . import tower_data as td

#: 装备默认武器名（老档没有 equipped_weapon 字段时兜底）
DEFAULT_WEAPON = "玄重尺"


def _load_weapon_data():
    """weapon_data 模块（已 import，包一层便于测试替换 / 语义清晰）。"""
    return wd


def equipped_weapon(p) -> str:
    """当前装备的武器名；老档 / 空值兜底玄重尺。"""
    return p.get("equipped_weapon") or DEFAULT_WEAPON


def weapon_base_row(p) -> dict:
    """当前装备武器的四词条**基础值**（未乘强化成长）。

    唯一口径：``stats()`` / ``enhance_preview()`` / 人物面板三处共用，
    杜绝「人物卡写玄重尺、实际拿血斧」的图文不一致（2026-11-17 用户要求武器可替换）。
    """
    name = equipped_weapon(p)
    if name == "玄重尺":
        return dict(cfg.WEAPON["stats"])
    row = (wd.WEAPONS.get(name) or {}).get("stats")
    return dict(row) if row else dict(cfg.WEAPON["stats"])


def _weapon_rows(p) -> tuple[dict, dict]:
    """(当前, 下一级) 四词条实际值。满级只有玄重尺走硬编码 max_stats 表。"""
    name = equipped_weapon(p)
    w = int(p.get("weapon", 0))
    base = weapon_base_row(p)
    cap = cfg.WEAPON["max_internal"]      # 9
    top = cap - 1                          # 无硬编码表的武器封顶用 L=8

    def at(internal: int) -> dict:
        if name == "玄重尺" and internal >= cap:
            return {k: int(cfg.WEAPON["max_stats"][k]) for k in base}
        return {k: cfg.weapon_stat(v, min(internal, top)) for k, v in base.items()}

    return at(w), at(w + 1)


def weapon_panel(p) -> dict:
    """当前武器的面板/卡片展示数据（名称、显示强化级、四词条）。

    强化等级是「武器强化」而非某把武器专属，换武器后成长一并继承，
    所以 level 对任意武器都返回 ``L+1``（1~10）。
    玄重尺升到十级觉醒为「异火恒古尺」——显示名沿用原实机文案，键名仍是玄重尺。
    """
    w = int(p.get("weapon", 0))
    cur, _ = _weapon_rows(p)
    name = equipped_weapon(p)
    if name == "玄重尺" and w >= cfg.WEAPON["max_internal"]:
        name = cfg.WEAPON["name_max"]
    return {"name": name, "level": cfg.weapon_display_level(w), "stats": cur}


# ---------------------------------------------------------------------------
# 等级成长  [源码 ah.java:165]
# ---------------------------------------------------------------------------
def c_stat(base: int, lv: int) -> int:
    """原版成长公式：base*10 + 阶²*base*10 + base*lv。

    2026-11-14：阶改成按**大境界下标**取（cfg.realm_index），
    不再写死 (lv-1)//9 —— 原著补全 斗之气/斗尊巅峰/半圣 后各境界小段数不同
    （半圣 3 段、斗帝 1 级），用除法会把斗圣前五星算成半圣阶。
    实机校验（形象 0 / 玄重尺 +0 / 等级 1）：
      体力 880  力量 275  斗气 165  敏捷 220  防御 132
    与人物面板显示逐格吻合。
    """
    k = cfg.realm_index(lv)
    return int(base) * 10 + k * k * int(base) * 10 + int(base) * int(lv)


# ---------------------------------------------------------------------------
# 角色创建
# ---------------------------------------------------------------------------
def create_player(qq, name, group) -> dict:
    now = time.time()
    st = {
        "qq": str(qq), "name": name, "group": str(group),
        "lv": 1, "exp": 0,
        "lv_ver": 3,          # 原著13阶存档标记（store 用它判断要不要迁移）
        "gold": cfg.START_GOLD, "luck": cfg.START_LUCK,
        "weapon": 0,
        # 2026-11-17 装备/爬塔：weapon 保留为玄重尺强化等级 0-9；
        # weapons=持有武器表、equipped_weapon=当前装备、tower_best=历史最高层。
        "weapons": ["玄重尺"], "equipped_weapon": "玄重尺", "tower_best": 0,
        "hp": 0, "mp": 0,
        "skills": {},
        # 三层投放：可养五火 + B 线十一火都占位（0=未收服）。单一真值，不另建解锁字段。
        "flames": {n: 0 for n in getattr(cfg, "OWNED_FLAME_NAMES", cfg.FLAME_NAMES)},
        "bag": {"回灵丹": cfg.START_HP_POTION, "炎星石": cfg.START_STONES},
        "alch": cfg.ALCH_START, "alch_exp": 0,
        "bonus": {},
        "last_settle": now, "last_fight": 0, "last_craft": 0,
        "wins": 0, "losses": 0,
        # 无惩罚切磋的独立荣誉战绩：与战斗胜负(wins/losses)分账，
        # 切磋只动这两个字段，绝不扣 hp/gold/exp。
        "duel_wins": 0, "duel_losses": 0,
        "main_idx": 0, "task": None,
        "diyan": False,
    }
    st["hp"] = stats(st)["体力"]
    st["mp"] = mp_max(st)

    # 入道随机宗门 + 随机领悟 1 门低阶斗技（2026-11-16 用户：「刚入道不可能
    # 直接就会四个技能，应该随机给一个低阶斗技，后面要逐一学习」）
    # 按 qq 定种子：每个玩家分到一个"随机但稳定"的宗门（不可反复重掷）
    try:
        fac.assign_faction(st, rng=random.Random("faction:" + str(qq)).random)
    except Exception:
        st.setdefault("faction", "")
        st.setdefault("faction_name", "")
    # 入道发放「本宗所藏」异火（1 级）：原著有据的三处之一，无则不发放。
    grant_faction_flame(st)
    # learned = 已领悟技名表。新号只有 1 门随机普通阶技；其余靠「学斗技」逐个领悟。
    try:
        low = [e["name"] for e in sk_mod.learnable(st) if e["tier"] == "普通"]
        if not low:                       # 极少数宗门入池等级无普通阶 → 任意最低解锁技
            low = [e["name"] for e in sk_mod.learnable(st)]
        st["learned"] = [random.Random("learn:" + str(qq)).choice(low)] if low else []
    except Exception:
        st["learned"] = []
    # 入门技同时上阵（新档 ensure_loadout 不自动补未领悟的技，须显式带上）
    st["loadout"] = list(st["learned"])[:cfg.LOADOUT_SIZE]
    try:
        sk_mod.ensure_loadout(st)
    except Exception:
        pass
    return st


# ---------------------------------------------------------------------------
# 属性面板
# ---------------------------------------------------------------------------
def stats(p) -> dict:
    """当前六维（力量/敏捷/斗气/防御/体力）+ 机缘。

    [源码] 基础值走 c_stat；玄重尺 4 词条加算；L=9 走硬编码 2000/2000/1000/1200。

    2026-11-17 装备系统：默认装备玄重尺时**逐字节沿用原逻辑**（实机数值断言 / 平衡
    全部不动）；装备副本掉落的其他武器时，四词条改取 weapon_data.WEAPONS[name]["stats"]，
    仍按同一强化等级成长（weapon_stat），保证换武器真实改变面板与战斗。
    """
    lv = int(p.get("lv", 1))
    out = {k: c_stat(v, lv) for k, v in cfg.HERO_BASE.items()}

    # 武器四词条：与 weapon_panel / enhance_preview 同一口径（_weapon_rows）。
    # 装备玄重尺且 w<9 时逐字节等于原逻辑（实机数值断言 / 平衡全部不动）。
    cur_weapon, _ = _weapon_rows(p)
    for k, v in cur_weapon.items():
        out[k] = out.get(k, 0) + v

    for k, v in (p.get("bonus") or {}).items():
        out[k] = out.get(k, 0) + int(v)

    # 其他火焰（兽火/天然/人造/融合火）的比例加成：atk→力量、hp→体力。
    # 与 flame_bonus 同源；只有已收服才生效，新号无这些火 → 逐字节不变。
    _ofr = _flame_bonus.other_flame_ratio(p)
    if _ofr.get("atk"):
        out["力量"] = int(out.get("力量", 0) * (1.0 + float(_ofr["atk"])))
    if _ofr.get("hp"):
        out["体力"] = int(out.get("体力", 0) * (1.0 + float(_ofr["hp"])))

    if p.get("diyan"):
        for k in out:
            out[k] = int(out[k] * 1.2)

    out["机缘"] = int(p.get("luck", 0))
    return out


def mp_max(p) -> int:
    """斗气槽上限。青莲地心火每级 +25%（实机说明原文）。"""
    st = stats(p)
    flame = int((p.get("flames") or {}).get("青莲地心火", 0))
    return int(st["斗气"] * 2 * (1 + 0.25 * flame))


def dodge_rate(p) -> float:
    """闪避率（%）。[源码] y.java:157  a[0][1] += (a[0][6]+20)/200

    净莲妖火：实机说明「每提升一级增加 10% 暴击和闪避几率」-> 闪避也按 10%/级。
    """
    st = stats(p)
    base = cfg.HERO_DODGE + (st["敏捷"] + 20) // 200
    base += 10 * int((p.get("flames") or {}).get("净莲妖火", 0))
    base += int((p.get("bonus") or {}).get("闪避", 0))
    return float(min(80, base))


def crit_rate(p) -> float:
    """暴击率（%）。[源码] y.java:155  a[0][2] += (a[0][6]+30)/100"""
    st = stats(p)
    base = cfg.HERO_CRIT + (st["敏捷"] + 30) // 100
    base += 10 * int((p.get("flames") or {}).get("净莲妖火", 0))
    base += int((p.get("bonus") or {}).get("暴击", 0))
    return float(min(80, base))


def level_dodge(lv: int) -> float:
    """**不靠任何装备/异火/丹药**、纯等级带来的闪避百分点（上限同 dodge_rate）。

    两个用途，同一条曲线，保证「同等级同身法」：
      * 怪物眼力的基准（``monster_panel`` 用它抵掉玩家白拿的身法）；
      * 让「等级涨上去自然就闪」这件事可被度量，而不是散在 dodge_rate 里。
    """
    agi = c_stat(cfg.HERO_BASE["敏捷"], int(lv))
    return float(min(80, cfg.HERO_DODGE + (agi + 20) // 200))


def hit_rate(p) -> float:
    """命中率（%）。**与闪避对冲的那一半**：实际闪避 = max(0, 目标闪避 − 命中)。

    2026-11-20 用户：「应该有一个和闪避对应的词条，不然闪避太超标了」。

    刻意**不吃敏捷**：敏捷已经同时产闪避与暴击（``STAT_BATTLE_ROLE``），
    若命中也从敏捷来，两边同速成长 → 命中永远把闪避抵平，等于加了个假词条。
    这里按大境界给基数，剩下的留给后天投入（凝识丹 ``bonus["命中"]``），
    玩家才有一条能自己决定的解法。
    """
    lv = int(p.get("lv", 1))
    base = cfg.HERO_HIT + cfg.HERO_HIT_PER_REALM * cfg.realm_index(lv)
    base += int((p.get("bonus") or {}).get("命中", 0))
    return float(min(cfg.HIT_CAP, base))


def power(p) -> int:
    """战力：用于排行榜/切磋。"""
    s = stats(p)
    return int(
        s["力量"] * 2.0 + s["敏捷"] * 1.5 + s["斗气"] * 1.2
        + s["防御"] * 1.5 + s["体力"] * 0.2
        + int(p.get("weapon", 0)) * 200
    )


# ---------------------------------------------------------------------------
# 被动修炼（离线也涨，不强制打卡）
# ---------------------------------------------------------------------------
def flame_exp_mult(p) -> float:
    """陨落心炎：每级获得经验 +50%。"""
    return 1.0 + 0.5 * int((p.get("flames") or {}).get("陨落心炎", 0))


def settle(p, now_ts=None) -> dict:
    now_ts = time.time() if now_ts is None else now_ts
    elapsed = max(0.0, now_ts - float(p.get("last_settle", now_ts)))
    gain = int(elapsed / 60.0 * cfg.CULTIVATE_PER_MIN * flame_exp_mult(p))
    if gain > 0:
        p["exp"] = int(p.get("exp", 0)) + gain
    p["last_settle"] = now_ts
    return {"gain": gain, "minutes": int(elapsed // 60)}


# ---------------------------------------------------------------------------
# 后期天焚炼气塔（聚火壶）——2026-11-19 修炼大改
#
# 原著依据（详见 docs/战斗与修炼大改_2026-11-19.md）：
#   「第百五十章 一劳永逸」聚火壶注入本源陨落心炎 + 持续灌注斗气 → 源源不断产生心火；
#   「第百十三章 情报」改为幼生体，「依旧能够令得天焚炼气塔再次焕发作用」；
#   「第百十四章 商议」心火覆盖全塔 →「修炼速度，起码能够成倍翻长」；
#   「第百五十七章 奖励」火晶卡黑蓝青赤紫五档，决定可入层数；
#   「第四百十六章 闭关」每下一层，所需火能递增；
#   「第四百七十四章 中级修炼室」心火会掺入火焰狂暴之力（火毒），
#        「最好修炼半天，再休息一夜」；斗灵可一次修四五天；
#   「第四百七十六章 第二层」火毒累积会出大事，必须坚持第一层一周。
#
# 本作的抽象：火能 = 灌注斗气的凭证（1 点 = 10 分钟塔修），火毒 = 硬性代价。
# 所有状态都在 ``alchemy_tower`` 真实列里（最高声明：玩家数据不进 JSON）。
# ---------------------------------------------------------------------------
def tower_tier(card_tier) -> int:
    """卡档夹取到 0~4（黑蓝青赤紫）。脏数据不该让倍率表越界。"""
    return max(0, min(int(card_tier or 0), len(cfg.FACTION_CARD_MULT) - 1))


def tower_mult(card_tier, magma: bool = False) -> float:
    """心火修炼倍率。原著「成倍翻长」，故最低档也有 1.5×。"""
    m = float(cfg.FACTION_CARD_MULT[tower_tier(card_tier)])
    if magma:
        m *= float(cfg.TOWER_MAGMA_MULT)
    return m


def tower_poison_rate(card_tier, lv: int = 1, magma: bool = False) -> float:
    """每分钟火毒累积速率。斗灵（lv37）起减半 —— 原著「能一次修四五天」。"""
    r = float(cfg.TOWER_POISON_PER_MIN[tower_tier(card_tier)])
    if magma:
        r *= float(cfg.TOWER_MAGMA_POISON_MULT)
    if int(lv) >= int(cfg.TOWER_DOU_LING_LV):
        r *= 0.5
    return r


def tower_max_floor(card_tier) -> int:
    """该卡可进入的最高塔层（原著黑卡一二层、蓝卡三四层……）。"""
    return int(cfg.FACTION_CARD_MAX_FLOOR[tower_tier(card_tier)])


def tower_magma_open(p, card_tier) -> bool:
    """塔底岩浆世界：紫卡 + 斗灵（lv37）才可入。"""
    return (tower_tier(card_tier) >= len(cfg.FACTION_CARD_MULT) - 1
            and int(p.get("lv", 1)) >= int(cfg.TOWER_DOU_LING_LV))


def tower_daily_energy(st: dict, today: str) -> int:
    """每日免费火能（原地改 st）。返回本次实际发放量。"""
    if str(st.get("free_day") or "") == str(today):
        return 0
    st["free_day"] = str(today)
    st["fire_energy"] = int(st.get("fire_energy", 0)) + int(cfg.TOWER_FREE_ENERGY)
    return int(cfg.TOWER_FREE_ENERGY)


def tower_add_energy(st: dict, n: int) -> int:
    """战斗胜利夺取火能（原著「战斗中夺取火能」）。返回实际入账值。"""
    n = max(0, int(n))
    if n:
        st["fire_energy"] = int(st.get("fire_energy", 0)) + n
    return n


def alchemy_settle(p, st: dict, now_ts=None, entering: bool = True) -> dict:
    """进塔 / 续修 / 出塔的一次结算（原地改 ``st``，返回给玩家的账）。

    ``entering=True``（「入塔」）：
      * 不在塔中 → 先按离塔时间消解火毒，再标记入塔（本次不计修为）；
      * 已在塔中 → 结算塔修：火能按 1 点 = 10 分钟扣减，修为按心火倍率算，
        火毒按卡档累积；火毒触顶立刻强制出塔（``capped=True``）。
    ``entering=False``（「出塔」）：结算塔修后把 ``in_tower`` 归零，开始冷却。

    时间源由调用方给（``now_ts``），纯函数可测；**不写任何持久层**。
    """
    now = time.time() if now_ts is None else float(now_ts)
    tier = tower_tier(st.get("card_tier", 0))
    lv = int(p.get("lv", 1))
    magma = bool(tower_magma_open(p, tier))
    st["magma"] = 1 if magma else 0
    res = {
        "ok": True, "mode": "enter", "minutes": 0, "gain": 0,
        "energy_spent": 0, "energy": int(st.get("fire_energy", 0)),
        "poison": int(st.get("fire_poison", 0)), "poison_add": 0, "cooled": 0,
        "tier": tier, "magma": magma, "mult": tower_mult(tier, magma),
        "capped": False, "max_floor": tower_max_floor(tier),
        "in_tower": bool(st.get("in_tower")),
    }
    last = float(st.get("last_ts") or 0)

    # ---- 不在塔中：先冷却，再入塔 ----
    if not int(st.get("in_tower") or 0):
        if last > 0:
            outside_min = max(0.0, (now - last) / 60.0)
            cool = int(outside_min * float(cfg.TOWER_POISON_COOL_PER_MIN))
            if cool:
                before = int(st.get("fire_poison", 0))
                st["fire_poison"] = max(0, before - cool)
                res["cooled"] = before - int(st["fire_poison"])
        if not entering:
            res["mode"] = "outside"
            res["poison"] = int(st.get("fire_poison", 0))
            res["energy"] = int(st.get("fire_energy", 0))
            return res
        st["in_tower"] = 1
        st["last_ts"] = now
        st["enters"] = int(st.get("enters", 0)) + 1
        res["mode"] = "enter"
        res["in_tower"] = True
        res["poison"] = int(st.get("fire_poison", 0))
        res["energy"] = int(st.get("fire_energy", 0))
        return res

    # ---- 已在塔中：结算塔修 ----
    elapsed_min = max(0.0, (now - last) / 60.0)
    minutes = min(int(elapsed_min), int(cfg.TOWER_MAX_MINUTES))
    energy = int(st.get("fire_energy", 0))
    per = int(cfg.TOWER_MINUTES_PER_ENERGY)
    can_min = energy * per
    if minutes <= 0:
        res["mode"] = "wait"
        res["in_tower"] = True
        return res
    if can_min <= 0:
        # 火能耗尽：留在塔里只会空耗时间，直接按出塔处理（不静默吞时间）
        st["in_tower"] = 0
        st["last_ts"] = now
        res.update({"mode": "no_energy", "in_tower": False,
                    "energy": energy, "poison": int(st.get("fire_poison", 0))})
        return res

    minutes = min(minutes, can_min)
    # 火能是整数「块」：1 点 = TOWER_MINUTES_PER_ENERGY 分钟。只结算整块，
    # 零头时间**留在时间戳里**（否则 3 分钟的短修会被白送修为却不扣火能）。
    per = max(1, per)
    blocks = minutes // per
    if blocks <= 0:
        res["mode"] = "wait"
        res["in_tower"] = True
        res["energy"] = energy
        res["poison"] = int(st.get("fire_poison", 0))
        return res
    billable = blocks * per
    gain = int(billable * float(cfg.CULTIVATE_PER_MIN) * tower_mult(tier, magma)
               * flame_exp_mult(p))
    p["exp"] = int(p.get("exp", 0)) + gain
    # 塔中时间不算静修时间（同一段时间不能被「修炼」再领一次）
    p["last_settle"] = now

    st["fire_energy"] = max(0, energy - blocks)
    poison_add = int(billable * tower_poison_rate(tier, lv, magma))
    st["fire_poison"] = int(st.get("fire_poison", 0)) + poison_add
    capped = int(st["fire_poison"]) >= int(cfg.TOWER_POISON_CAP)
    if capped:
        st["fire_poison"] = int(cfg.TOWER_POISON_CAP)
        st["in_tower"] = 0     # 火毒触顶：强制出塔，必须冷却
    # 只推进「已结算」的时间；零头留给下一次（不吞时间也不白送修为）
    st["last_ts"] = last + billable * 60.0
    if not entering:
        st["in_tower"] = 0
        st["last_ts"] = now    # 主动出塔：零头不再挂钟，避免冷却被多算

    res.update({
        "mode": "settle" if not capped else "capped",
        "minutes": billable, "gain": gain, "energy_spent": blocks,
        "energy": int(st.get("fire_energy", 0)),
        "poison": int(st.get("fire_poison", 0)), "poison_add": poison_add,
        "capped": capped, "in_tower": bool(st.get("in_tower")),
        "leftover_min": max(0, int(elapsed_min) - billable),
    })
    return res


def tower_upgrade_card(st: dict) -> dict:
    """升一档火晶卡（原地改 st）。原著「黑换蓝缴纳一百天火能」。"""
    tier = tower_tier(st.get("card_tier", 0))
    names = cfg.FACTION_CARD_NAMES
    if tier >= len(names) - 1:
        return {"ok": False, "reason": "max", "tier": tier,
                "name": names[tier]}
    cost = int(cfg.FACTION_CARD_UPGRADE_COST[tier])
    have = int(st.get("fire_energy", 0))
    if have < cost:
        return {"ok": False, "reason": "energy", "tier": tier, "need": cost,
                "have": have, "name": names[tier], "next": names[tier + 1]}
    st["fire_energy"] = have - cost
    st["card_tier"] = tier + 1
    return {"ok": True, "tier": tier + 1, "cost": cost,
            "name": names[tier], "next": names[tier + 1],
            "energy": int(st["fire_energy"])}


def regen(p, elapsed: float) -> dict:
    """按经过时间结算气血/斗气的自动恢复（纯函数，时间源由调用方给）。

    2026-11-19 用户「给斗气和血量加上自动恢复」。设计口径：

      * **慢速保底，不取代丹药**：每 ``REGEN_TICK_SEC``（180 秒）回一次，
        气血 4%/斗气 6% 上限。满血约 75 分钟、满蓝约 50 分钟 —— 打一两场
        战斗掉掉的血，靠调息回来不如直接服掉落的疗伤丹。
      * **离线同样累计**：``elapsed`` 是「上次结算到现在」的秒数，时间戳由
        store 的 ``regen_clock`` 表持久化（不写 players.data，守最高声明）。
      * **余数不丢**：只消耗整 tick 的秒数，剩下的零头留在时间戳里，
        下次接着算（否则每次命令都把零头抹掉，玩家永远等不到下一 tick）。
      * **满状态不吃时间**：气血斗气都满时直接吃掉全部 elapsed 并返回 0，
        避免「满血挂机一小时，掉血后还要再等 3 分钟才回第一口」。

    :return: ``{ticks, hp, mp, consumed, touched}``。
             ``consumed`` 是本次应向前推进的时间戳秒数（0 表示不推进）。
             ``touched`` 表示 hp/mp 是否真的被改写。
    """
    st = stats(p)
    hp_max, mp_cap = int(st["体力"]), mp_max(p)
    hp = max(0, min(hp_max, int(p.get("hp", hp_max))))
    mp = max(0, min(mp_cap, int(p.get("mp", mp_cap))))
    # 先夹回合法区间：老档/被外部写坏的值不该在调息时继续放大。
    if hp != int(p.get("hp", hp)) or mp != int(p.get("mp", mp)):
        p["hp"], p["mp"] = hp, mp
    elapsed = max(0.0, float(elapsed))
    if hp >= hp_max and mp >= mp_cap:
        # 满状态：时间照吃，不攒 tick（否则挂满机后掉血要再等一整轮）
        return {"ticks": 0, "hp": 0, "mp": 0, "consumed": elapsed, "touched": False}
    ticks = int(elapsed // float(cfg.REGEN_TICK_SEC))
    if ticks <= 0:
        return {"ticks": 0, "hp": 0, "mp": 0, "consumed": 0.0, "touched": False}
    hp_add = min(hp_max - hp, ticks * int(hp_max * float(cfg.REGEN_HP_PCT)))
    mp_add = min(mp_cap - mp, ticks * int(mp_cap * float(cfg.REGEN_MP_PCT)))
    if hp_add > 0:
        p["hp"] = hp + hp_add
    if mp_add > 0:
        p["mp"] = mp + mp_add
    return {
        "ticks": ticks, "hp": int(hp_add), "mp": int(mp_add),
        "consumed": ticks * float(cfg.REGEN_TICK_SEC),
        "touched": bool(hp_add or mp_add),
    }


# ---------------------------------------------------------------------------
# 升级 / 突破
# ---------------------------------------------------------------------------
def can_level(p) -> bool:
    lv = int(p.get("lv", 1))
    return lv < cfg.MAX_LV and int(p.get("exp", 0)) >= cfg.exp_need(lv)


def level_up(p, rng=random.random) -> dict:
    """升一级。102 级九星斗圣 -> 103 级（斗帝）走机缘判定。"""
    lv = int(p.get("lv", 1))
    if lv >= cfg.MAX_LV:
        return {"ok": False, "reason": "max"}
    need = cfg.exp_need(lv)
    if int(p.get("exp", 0)) < need:
        return {"ok": False, "reason": "exp", "need": need - int(p.get("exp", 0))}

    if lv >= cfg.PEAK_LV:
        # 进阶斗帝：机缘决定成功率（m.java：机缘值影响进阶斗帝几率）
        rate = cfg.tiandi_rate(p.get("luck", 0), p.get("diyan", False))
        if rng() < rate:
            p["exp"] = int(p["exp"]) - need
            p["lv"] = cfg.MAX_LV
            p["diyan"] = True
            p["luck"] = 9999
            _refill(p)
            return {"ok": True, "tiandi": True, "rate": rate}
        p["exp"] = int(p["exp"] * (1 - cfg.LOSE_HP_ON_DEFEAT))
        return {"ok": False, "reason": "tiandi_fail", "rate": rate}

    p["exp"] = int(p["exp"]) - need
    p["lv"] = lv + 1
    _refill(p)
    return {"ok": True, "lv": p["lv"], "name": cfg.full_realm(p["lv"])}


def is_nine_star_breakthrough(p) -> bool:
    """当前是否「九星突破点」：经验已满、且这一级突破会跨入下一个大境界。

    lv9→10(斗之气九段→斗者)、lv18→19(斗者九星→斗师) … lv102→103(斗圣九星→斗帝)
    都算。半圣只有 3 阶（lv91-93），lv93→94 也归入。普通升级（一星升二星）不算。
    """
    lv = int(p.get("lv", 1))
    if lv >= cfg.MAX_LV or not can_level(p):
        return False
    return cfg.realm_index(lv) != cfg.realm_index(lv + 1)


def make_breakthrough_boss(p, rng=random.random) -> dict:
    """九星突破 BOSS：**原著有名有姓的反派** + 内部战力快照锁难度。

    2026-11-20 用户澄清原话：「战力快照要有，只是不要发出来，发出来会影响游戏体验」。
    因此分两层，谁都不许混：

      * **内部**：触发瞬间 ``copy.deepcopy(p)`` 锁一份战力快照，作为
        ``monster_scaled`` 的反解基准 —— BOSS 强度锁定在"触发那一刻"，不受战斗中
        掉血/服药/临时增益影响；``snapshot_power`` 留在 mon / out 里供校验。
      * **对外**：只有原著身份、登场引言、台词、技能、机制。
        命令层、战报、卡片都**禁止**输出快照战力数字或「心魔劫影」字样。

    旧版把快照做成"打自己的镜像"且把战力印在文案里，用户评「敷衍」+影响体验。
    本版只保留快照的数值锁定作用，身份改从 ``cfg.BREAK_BOSSES`` 取
    （药老/云棱/鹜护法/摘星老鬼/魂灭生/美杜莎/魂天帝…），不放大快照属性本身
    ——早先放大属性会让低等级怪攻击反解畸高，卡死新手。
    ``rng`` 保留只是兼容既有调用签名，不消费（BOSS 由突破档确定，不随机）。
    """
    snap = copy.deepcopy(p)   # 内部快照：触发瞬间锁定，战斗过程不再变化
    ch = cfg.break_boss_for_lv(int(snap.get("lv", 1)))
    # 出场描写集中在 cfg.CHARACTER_APPEARANCE，避免角色包里各写一份
    _ch = dict(ch)
    _ch["appearance"] = str(ch.get("appearance") or cfg.character_appearance(ch["name"]) or "")
    species = {
        "name": ch["name"], "lv": int(snap["lv"]),
        "hard": 1.0, "exp_mul": 1.0, "gold_mul": 1.0,
        "kind": cfg.BOSS_BREAK_KIND,
        "skill": dict(ch["skill"]),
        "character": _ch,
    }
    mon = monster_scaled(snap, species)
    mon["hp"] = max(1, int(mon["hp"] * float(cfg.BOSS_BREAK_HP_MULT)))
    mon["atk"] = max(1, int(mon["atk"] * float(cfg.BOSS_BREAK_ATK_MULT)))
    mon["is_boss"] = True
    mon["breakthrough_boss"] = True
    mon["boss_title"] = str(ch.get("title") or "")
    # 内部校验字段：**禁止**出现在任何玩家可见文案/卡片（见 commands.cmd_break 守门）
    mon["snapshot_power"] = power(p)
    return mon


def nine_star_breakthrough(p, rng=random.random, locked=None) -> dict:
    """九星突破 BOSS 战完整流程（命令层调用；core 只做逻辑不改存档持久化）。

    前置必须由调用方先用 ``is_nine_star_breakthrough(p)`` 判定。本函数：
      1. 内部锁战力快照 + 按突破档取一名原著 BOSS（make_breakthrough_boss）；
      2. 用既有的 ``fight()`` 打这一场（自动服药/锁定物品口径与普通战斗一致）；
      3. 胜利 → 掉火焰（roll_breakthrough_flame）→ level_up 完成突破；
         失败 → 保留战斗损耗（掉血/耗药），经验不扣，可再次挑战。
    返回 dict 给命令层渲染文案；``p`` 就地被修改（胜负都由 fight / level_up 写）。
    """
    mon = make_breakthrough_boss(p, rng)
    res = fight(p, mon, rng=rng, locked=locked or set(), auto_pill=True)
    out = {
        "boss": mon, "win": bool(res.get("win")),
        "rounds": int(res.get("rounds", 0)),
        "taken": int(res.get("taken", 0)),
        "hp_end": int(p.get("hp", 0)),
        # 内部校验字段，绝不渲染：用户「快照要有，但不要发出来」。
        "snapshot_power": int(mon.get("snapshot_power", 0)),
        # 原著 BOSS 元数据：命令层据此渲染沉浸式引言/台词。
        "boss_name": str(mon.get("name") or ""),
        "boss_title": str(mon.get("boss_title") or ""),
        "character": dict(mon.get("character") or {}),
        "log": list(res.get("log") or []),
    }
    if not res.get("win"):
        out["leveled"] = False
        out["flame"] = None
        return out
    # 胜利：先掉火焰再突破（level_up 会回满 hp/mp）。
    out["flame"] = roll_breakthrough_flame(p, rng)
    lvres = level_up(p, rng=rng)
    out["leveled"] = bool(lvres.get("ok"))
    out["tiandi"] = bool(lvres.get("tiandi"))
    out["tiandi_fail"] = lvres.get("reason") == "tiandi_fail"
    out["rate"] = lvres.get("rate")
    out["new_lv"] = int(p.get("lv", 0))
    return out


def roll_breakthrough_flame(p, rng=random.random) -> dict:
    """九星 BOSS 战胜利后的火焰掉落。兽火/其他火焰概率高于异火（用户口径）。

    只掉 B 线数值异火与其他火焰；A 线隐藏彩蛋 / S 线门槛火 / 帝炎不进池
    （保护已拍板的三层设计：它们的获取方式各自独立）。
    写入 p["flames"][name] = 1（单一真值，与收服/支线同源）；已有则折算成药渣补偿。
    """
    r = int(rng() * 1000)
    kind, name = "", ""
    if r < int(getattr(cfg, "BOSS_FLAME_DROP_OTHER", 0)):
        # 其他火焰池：融合新生火权重更低（更稀有）
        pool = []
        for f in cfg.OTHER_FLAMES:
            w = (cfg.BOSS_FUSION_RARE_WEIGHT if f["kind"] == "fusion"
                 else cfg.BOSS_FUSION_WEIGHT)
            pool.extend([f["name"]] * max(1, int(w * 100)))
        name = rng_choice(pool, rng) if pool else ""
        kind = "other"
    elif r < int(getattr(cfg, "BOSS_FLAME_DROP_OTHER", 0)) + int(
            getattr(cfg, "BOSS_FLAME_DROP_BONUS", 0)):
        name = rng_choice(cfg.BONUS_FLAME_NAMES, rng) if cfg.BONUS_FLAME_NAMES else ""
        kind = "bonus"
    if not name:
        return {"ok": False, "name": "", "kind": ""}

    fl = p.setdefault("flames", {})
    if int(fl.get(name, 0)) > 0:
        # 已持有 → 折算药渣（不重复给 1 级，避免刷数值）
        add_item(p, "药渣", 30)
        return {"ok": True, "name": name, "kind": kind, "duplicate": True, "dross": 30}
    fl[name] = 1
    return {"ok": True, "name": name, "kind": kind, "duplicate": False}


def _refill(p) -> None:
    st = stats(p)
    p["hp"] = st["体力"]
    p["mp"] = mp_max(p)


# ---------------------------------------------------------------------------
# 战斗
# ---------------------------------------------------------------------------
def mitigation(defence: int) -> float:
    """减伤系数：防御 800 时减半。"""
    d = max(0, int(defence))
    return 800.0 / (800.0 + d)


def pick_monster(p, rng=random.random) -> dict:
    """按玩家等级挑选一个"够得着但不算白给"的怪。

    九星制把等级轴拉到 83，而物种表只有 7 只，用原来的「等级差 ≤2」会大面积
    选不到（中高等级玩家一直掉回最弱的地鬼）。改成两步：
      1) 先只留「同一大境界内」的物种（等级差 ≤ LV_PER_REALM），保证不会
         让 1 级新手撞上 27 级的赤炎狼（等级差修正会把战斗拖成 9 回合）；
      2) 再在窗口内取等级最接近的 3 只随机，保留一点随机性。
    窗口内一只都没有（物种表没覆盖到）时退到全表最近的一只。
    """
    lv = int(p.get("lv", 1))
    window = [m for m in cfg.MONSTERS if abs(int(m["lv"]) - lv) <= cfg.LV_PER_REALM]
    pool = window or cfg.MONSTERS
    cand = sorted(pool, key=lambda m: abs(int(m["lv"]) - lv))[:3]
    return rng_choice(cand or cfg.MONSTERS[:1], rng)


def rng_choice(seq, rng=random.random):
    if not seq:
        return None
    return seq[min(len(seq) - 1, int(rng() * len(seq)))]


def damage_schedule(p, rounds: int = 15, puppet_bonus: float = 0.0) -> list:
    """逐回合的期望输出（四槽模型 · 已算暴击期望，未乘减伤）。

    与 `fight` 同口径：每回合在已上阵四槽里**随机释放**，期望≈四槽平均；
    斗气按阶扣除，不足以释放任一技时转武器普攻。用于反解同阶怪血量，
    使「几回合打完」保持为设计值（TTK_PLAYER）。

    ``puppet_bonus``（2026-11-18 傀儡）：傀儡每回合期望肉身输出（未乘减伤），
    由唯一函数 ``puppet_core.puppet_expected_damage`` 算出后从命令层传入。
    **默认 0 → 无傀儡时既有调用逐字节不变。** 与 fight 的傀儡击必须同涨同落（红线）。
    """
    st = stats(p)
    normal_raw = st["力量"] * float(cfg.WEAPON_BASE_MULT)
    entries = _loadout_entries(p)
    if not entries:
        return [(normal_raw + puppet_bonus)] * max(1, rounds)

    mpmax = mp_max(p)
    lv_map = p.get("skills") or {}
    slots = []
    for e in entries:
        lv = int(lv_map.get(e["name"], 1))
        raw = (st["力量"] * 0.5 + st["斗气"] * 1.5) * sk_mod.skill_mult(e, lv)
        slots.append((raw, sk_mod.tier_cost(e["tier"], mpmax)))
    avg_dmg = sum(d for d, _ in slots) / len(slots)
    avg_cost = sum(c for _, c in slots) / len(slots)

    pool = mpmax
    out = []
    for _ in range(max(1, rounds)):
        if pool >= avg_cost:
            out.append(avg_dmg)
            pool -= avg_cost
        else:
            out.append(normal_raw)
    mult = 1.0 + crit_rate(p) / 100.0 * 0.5
    # 傀儡肉身输出不吃暴击加成（原著「稳」的打手），故在 mult 之外单独加。
    return [d * mult + puppet_bonus for d in out]


def cum_damage(p, rounds: int, puppet_bonus: float = 0.0) -> float:
    """前 rounds 个回合的累计期望输出（未乘减伤）。"""
    sch = damage_schedule(p, int(rounds) + 1, puppet_bonus=puppet_bonus)
    whole = int(rounds)
    frac = rounds - whole
    total = sum(sch[:whole])
    if frac > 0 and whole < len(sch):
        total += sch[whole] * frac
    return total


def mon_tier(kind) -> str:
    """怪物战斗档位：``"boss"`` / ``"elite"`` / ``"normal"``。**唯一判定入口**。

    三处来源的 ``kind`` 字面并不统一，别再各自写字符串包含：
      * 野外 —— 势力名：``"魔兽"`` / ``"悍匪"`` / ``"魂殿"`` / ``"黑盟"`` / ``"魔炎谷"``
      * 爬塔 —— ``"塔·normal"`` / ``"塔·elite"`` / ``"塔·boss"``
      * 周本 —— ``"周本·normal"`` / ``"周本·elite"`` / ``"周本·boss"``

    2026-11-20 修的真 bug：旧版面板、怪失误率、怪技能概率、精英保底掉落四处都写
    ``"精英" in kind``，而塔/周本传的是 ``"塔·elite"`` / ``"周本·elite"`` ——
    **不含「精英」二字**（那两个字只在怪名「精英·嗜血鼠」里）。于是精英层全程按
    小怪结算：``MON_PANEL_ELITE`` 是死常量、技能概率少 ×1.5、失误率少减半、
    硬仗必掉的两件疗伤丹也没了。

    BOSS 档沿用 ``cfg.BOSS_KIND_TOKENS``：野外魂殿/黑盟/魔炎谷的反派本来就是
    BOSS 档（``recipes_data`` 里那句注释写明了「普通遭遇战的反派…都覆盖」，
    并据此解锁 tier4 天材地宝），这条口径不动。
    """
    k = str(kind or "")
    if any(t in k for t in cfg.BOSS_KIND_TOKENS):
        return "boss"
    if any(t in k for t in cfg.MON_TIER_ELITE_TOKENS):
        return "elite"
    return "normal"


def monster_panel(lv: int, kind: str = "", is_boss: bool = False) -> dict:
    """怪物隐藏面板：与玩家同构的**命中 / 暴击 / 闪避**（单位都是 %）。

    用户 2026-11-20 口径：「按照等级来，然后无论是小怪还是精英怪，还是 boos，
    都应该拥有和玩家一样的隐藏面板，这样战斗逻辑进行时才不会出现个别词条
    一边倒的情况」。

    旧版怪物这三项全无：玩家把闪避堆到 80 就是物理免疫（九星突破 BOSS 实测
    全场 ``taken=0``），怪物也不会闪、不会暴 —— 词条天平彻底一边倒。

    **不进 hp/atk 反解**：难度仍只由物种等级 / 塔层 / 周本层决定，
    这个面板只决定「谁打得中谁、谁打得疼」，不改「几回合打完」的设计值。
    """
    k = cfg.realm_index(max(1, int(lv)))
    _tier = "boss" if is_boss else mon_tier(kind)
    mul = 1.0
    if _tier == "boss":
        mul = float(cfg.MON_PANEL_BOSS)
    elif _tier == "elite":
        mul = float(cfg.MON_PANEL_ELITE)
    # 眼力 = 该等级白拿的身法 − 残值：同等级正好抵掉「等级送的闪避」。
    # 不加精英/BOSS 系数 —— 眼力再拔高就会把必打关（九星突破）压成必输。
    hit = max(0.0, level_dodge(lv) - float(cfg.MON_HIT_RESIDUAL))
    return {
        "hit": round(min(95.0, hit), 2),
        "crit": round(min(cfg.MON_CRIT_CAP, (cfg.MON_CRIT_BASE
                                            + cfg.MON_CRIT_PER_REALM * k) * mul), 2),
        "dodge": round(min(cfg.MON_DODGE_CAP, (cfg.MON_DODGE_BASE
                                              + cfg.MON_DODGE_PER_REALM * k) * mul), 2),
    }


def monster_scaled(p, mon, puppet_bonus: float = 0.0) -> dict:
    """把"物种"实例化成一只同阶怪。

    为什么不写死数值：玩家每 9 级 c_stat 有一次 阶² 的平方级跃升，
    固定血量的怪在跃升前后会从"白给"直接变"必死"（实测断崖：16~18 级胜率 0%）。

    血量：反解"玩家刚好在 TTK_PLAYER 回合斩杀"对应的累计伤害。
    攻击：反解"怪刚好在 TTK_PLAYER × MON_ROUND_RATIO 回合斩杀玩家"的单击值。
    """
    st = stats(p)
    lv = int(p.get("lv", 1))
    # 等级差只微调"几回合打完"，上下限收紧到 ±20%（原 ±60%/+80%）：
    # 原著 13 阶补齐后，物种表(7 只)不可能覆盖 103 级，总有些等级只能遇到
    # 隔了十几级的怪；旧钳位会把这种战斗压成 2.9 回合秒杀或拖到 9 回合，
    # 越过 test_monster_scaled_tracks_player 的 3~8 回合设计窗口。
    delta = max(-0.2, min(0.2, (int(mon["lv"]) - lv) * cfg.LV_DELTA_STEP))
    m_def = max(20, int(st["防御"] * cfg.MON_DEF_RATIO))

    # 玩家打完需要几回合（等级差只影响时长，不影响难度）
    k_rounds = cfg.TTK_PLAYER * (1.0 + delta)
    # 傀儡期望输出计入反解：玩家多一个打手，怪血同步变厚（红线，与 fight 同源）
    m_hp = int(cum_damage(p, k_rounds, puppet_bonus=puppet_bonus) * mitigation(m_def))
    # 怪打死玩家需要几回合 —— 物种难度直接就是这个比值，hard 越小越可怕
    m_rounds = k_rounds * cfg.MON_ROUND_RATIO * float(mon["hard"])
    m_atk = int(st["体力"] / m_rounds / mitigation(st["防御"]))
    exp = int(cfg.exp_need(lv) / cfg.EXP_FIGHTS_PER_LV * float(mon.get("exp_mul", 1.0)))
    gold = int(exp * 0.25 * float(mon.get("gold_mul", 1.0)))
    #     kind/is_boss 纯增量透传（2026-11-18 天材地宝分级掉落）：默认空/False 时
    # 既有调用与测试逐字节不变；遭遇战反派(魂殿/黑盟/魔炎谷)与塔/周本 boss 层据此
    # 解锁 tier4 专属掉落。掉落层只认这两个字段，不反推战斗数值。
    _kind = str(mon.get("kind") or "")
    _is_boss = any(tok in _kind for tok in cfg.BOSS_KIND_TOKENS)
    # 2026-11-20 原著角色包：species 自带 character（九星突破 BOSS）时透传，
    # 野外/塔/周本按怪名查同一张表。供 fight() 渲染登场引言/台词、结算机制。
    # 纯展示与效果，不进数值反解（红线：难度只由层号/战力决定）。
    _character = mon.get("character") or cfg.monster_character(mon["name"])
    # 怪物技能：species 自带 > 角色包专属 > 野外技能表 > kind 兜底。
    # 角色包优先于 SKILL_BY_KIND，塔层 BOSS 才能用「血刀」「摘星手」而不是
    # 千篇一律的「魂殿秘技」；数值口径仍沿用既有表，不重算面板。
    _skill = (mon.get("skill") or (_character or {}).get("skill")
              or cfg.MONSTER_SKILLS.get(mon["name"]))
    if not _skill:
        for _tok, _sk in cfg.SKILL_BY_KIND.items():
            if _tok in _kind:
                _skill = _sk
                break
    _panel = monster_panel(int(mon["lv"]), _kind, _is_boss)
    return {
        "name": mon["name"], "lv": int(mon["lv"]),
        "hp": max(1, m_hp), "atk": max(1, m_atk), "def": m_def,
        "exp": max(1, exp), "gold": max(1, gold),
        "kind": _kind, "is_boss": _is_boss,
        "skill": dict(_skill) if _skill else None,
        "character": dict(_character) if _character else None,
        # 2026-11-20 隐藏战斗面板（命中/暴击/闪避，%）。挂在实例上而不是
        # 现算，是为了让「物种/塔层/周本」三条路径拿到**同一份**面板口径，
        # 也让手搓怪（测试里直接给 hp/atk 的字典）保持零面板 → 行为零回归。
        "panel": _panel,
    }


def _legacy_entries() -> list[dict]:
    """无宗门旧档的兜底：沿用原 4 门核心斗技（按普通阶计）。"""
    return [{
        "name": s["name"], "tier": "普通", "faction": "",
        "mp": cfg.SKILL_TIER_MP_FRAC["普通"], "dmg": float(s["dmg_mult"]),
        "unlock": 1, "desc": s.get("desc", ""),
    } for s in cfg.SKILLS]


def _ensure_faction(p) -> bool:
    """旧存档没有 faction 时惰性补一个（入道分宗的兜底）。

    ⚠️ 必须按 qq 定种子，与 create_player 同一套种子：否则旧档每次读出来都现抽一个，
    玩家会看到「宗门」每次问都不一样（实测 lv47 老档 faction=None，走的就是这条路径）。
    """
    if p.get("faction"):
        return True
    try:
        seed = "faction:" + str(p.get("qq") or "")
        fac.assign_faction(p, rng=random.Random(seed).random)
    except Exception:
        return False
    return bool(p.get("faction"))


def _loadout_entries(p) -> list[dict]:
    """当前上阵四槽的完整技能对象；无宗门旧档退回原 4 核心。"""
    _ensure_faction(p)
    ents = sk_mod.loadout_entries(p)
    return ents if ents else _legacy_entries()


def _pick_skill(p, mp: int, rng=random.random):
    """在「已上阵且斗气足够」的技中随机挑一个；都不够 -> (None, 0) 转普攻。"""
    entries = _loadout_entries(p)
    if not entries:
        return None, 0
    mpmax = mp_max(p)
    options = []
    for e in entries:
        c = sk_mod.tier_cost(e["tier"], mpmax)
        if c <= mp:
            options.append((e, c))
    if not options:
        return None, 0
    e, c = options[min(len(options) - 1, int(rng() * len(options)))]
    return e, c


def _hit(p, target_def, entry, rng, fx_self=None, fx_target=None, ctx=None,
         crit_add=0.0, extra_effects=None, atk_hit=0.0) -> dict:
    """一次出手：entry 为 None 时用武器普攻，否则按该斗技算伤害。

    2026-11-15 多维效果：传入 ``fx_target`` 时才结算技能效果（破防/连击/增伤/
    吸血/挂 DoT/控制），并把逐维文案放进 ``notes``。**不传这两个状态时，行为与
    改造前逐字节一致**，只是多两个空字段，任何既有调用方/测试都不受影响。
    """
    st = stats(p)
    # 2026-11-18 根基裁定（隐形）：投机靠丹药堆修为者出手打折（原著「斗气虚浮、
    # 取巧提升→实战吃亏」）。root_factor 不进 stats/人物卡/战报，前台零感知；
    # _hit 是 PVE(fight) 与 PVP(duel) 共用的唯一出手口，一处注入双侧同效。
    root = cfg.root_factor(p)
    crit = rng() * 100 < (crit_rate(p) + float(crit_add or 0))
    if entry is not None:
        lv = int((p.get("skills") or {}).get(entry["name"], 1))
        raw = (st["力量"] * 0.5 + st["斗气"] * 1.5) * sk_mod.skill_mult(entry, lv)
        kind = entry["name"]
    else:
        raw = st["力量"] * float(cfg.WEAPON_BASE_MULT)
        kind = "普攻"
    raw *= root   # 根基虚浮 → 实际输出打折；适量丹药 root=1 逐字节不变
    # 2026-11-20 修真 bug：BOSS 的 debuff_atk 通过 fx.on_hit 挂到玩家的 fx_self，
    # 但旧版只在怪出手时读 target_atk_mult(m_fx)，玩家出手从不读自己的 p_fx，
    # 于是"降攻"只显示在文案里、不真降伤害。这里按 self 侧的降攻打折；
    # 无降攻时倍率恒 1.0，普通战斗逐字节不变。
    if fx_self is not None:
        raw *= fx.target_atk_mult(fx_self)

    # 基础单击（未计破防/条件）：也是 DoT 的伤害基数
    # def_mult 是本次实际生效的防御减伤系数（破防会改写它）；回看卡据它算
    # 「面板 → 防御削减 → 实收」，纯展示，不参与任何数值计算。
    def_mult = mitigation(target_def)
    base = max(1, int(raw * def_mult * (1.5 if crit else 1.0)))
    notes: list[str] = []
    heal_ratio = 0.0
    dmg = base
    # 2026-11-20 修真 bug：**目标侧的闪避从没被结算过**。
    # 玩家打怪时只算了防御减伤，怪身上挂着「身法」也不会闪；于是
    # ① 怪物的 evade 类效果纯文案，② 玩家 7 门「控场·压闪避」斗技
    # （凶熊碎地拳 / 青湮剑罡 / 湮幻刺 / 气化天地 / 邪风斩 / 光耀印 /
    # 千幻玄冰刺）压了个不存在的属性 → 等于只加了描述。
    # 现在补上：目标闪避 = 自身 evade + 被压制的量（debuff_evade 为负）。
    # 位置在 on_hit **之前**：闪掉了就不该挂毒/降攻（miss 不产生命中效果）。
    # 2026-11-20 命中对冲：攻击方的命中（``atk_hit``，%）直接减在目标闪避上，
    # 与「玩家躲怪」是同一条公式的另一半（见 cfg 的 HERO_HIT/MON_* 注释）。
    # ``atk_hit`` 默认 0.0 → PVP(duel) 与既有调用方的阈值逐字节不变。
    # ⚠️ t_ev<=0 时**不掷骰**，所以普通战斗的 rng 序列逐字节不变。
    if fx_target is not None:
        t_ev = max(0.0, float(fx_target.get("evade", 0))
                   + fx.target_evade_delta(fx_target)
                   - max(0.0, float(atk_hit)) / 100.0)
        if t_ev > 0 and rng() < t_ev:
            return {"dmg": 0, "crit": False, "kind": kind, "skill": entry is not None,
                    "move": kind, "tier": str((entry or {}).get("tier") or ""),
                    "notes": ["被对手闪过"], "heal_ratio": 0.0,
                    "raw": int(raw), "def_cut": 0, "evaded": True}
    if entry is not None and fx_target is not None:
        self_st = fx_self if fx_self is not None else fx.new_state()
        effs = list(entry.get("effects") or []) + list(extra_effects or [])
        r = fx.on_hit(effs, self_st, fx_target, base, rng, ctx)
        heal_ratio, notes = r["heal_ratio"], r["notes"]
        # 破防在 on_hit 里写入 fx_target["def_shred"]，这里读取后重算本次伤害；
        # 条件/连击/增幅加成走 bonus。
        eff_def = int(target_def * fx.target_def_mult(fx_target))
        def_mult = mitigation(eff_def)
        dmg = max(1, int(raw * def_mult * (1.5 if crit else 1.0)))
        if r["bonus"] > 0:
            dmg = max(1, int(dmg * (1.0 + r["bonus"])))
    # 2026-11-17 完整战斗回看：kind 本来就是招式名（普攻叫「普攻」），这里再补一个
    # 显式 move / tier，避免文案层靠 kind 猜是不是技能；旧调用方继续只读原字段，
    # 新增字段不会改变任何既有战斗数值或回包。
    return {"dmg": dmg, "crit": crit, "kind": kind, "skill": entry is not None,
            "move": kind, "tier": str((entry or {}).get("tier") or ""),
            "notes": notes, "heal_ratio": heal_ratio,
            # 2026-11-19 回看卡「伤害构成」：raw = 面板原始（防御/暴击之前），
            # def_cut = 被对方防御稀释掉的量。展示层据此写「面板 X · 防御 -Y」，
            # 让玩家看得见「伤害被稀释了多少」。只加字段，不改任何数值计算。
            "raw": int(raw),
            "def_cut": max(0, int(raw - raw * def_mult))}


def faction_pools_open(p) -> list[str]:
    """当前境界已开放的卡池（新手 / 进阶 / 隐世）。"""
    lv = int(p.get("lv", 1))
    return [pool for pool in ("starter", "advanced", "hidden")
            if lv >= int(fac.POOL_UNLOCK.get(pool, 1))]


def faction_switch_cost(p) -> int:
    """转宗所需金币（随境界上涨）。"""
    return cfg.faction_switch_cost(int(p.get("lv", 1)))


def faction_candidates(p) -> list[dict]:
    """当前境界可以转去的宗门（排除本门）。

    入道只进新手池，进阶池要 lv40、隐世池要 lv90 —— 不转宗的话，
    25 派里 17 派的斗技玩家永远见不到。
    """
    lv = int(p.get("lv", 1))
    cur = str(p.get("faction") or "")
    out = []
    for pool in ("starter", "advanced", "hidden"):
        if lv < int(fac.POOL_UNLOCK.get(pool, 1)):
            continue
        for fid, f in fac.FACTIONS.items():
            if f["pool"] != pool or fid == cur:
                continue
            out.append({
                "faction": fid, "name": f["name"], "pool": pool,
                "unlock_lv": int(f["unlock_lv"]), "weight": int(f["weight"]),
                "skills": len(sk_mod.catalog(fid)),
            })
    return out


def change_faction(p, key: str) -> dict:
    """转宗：改换门庭，重配四槽。

    * 只能转去**当前境界已开放**的卡池里的宗门；
    * 扣金币（`cfg.faction_switch_cost`）；
    * **不重新发该派的入道物品**（否则转一次刷一次）；
    * 已升的技能等级按技名保留。
    """
    key = str(key or "").strip()
    if not key:
        return {"ok": False, "reason": "noarg"}
    cands = faction_candidates(p)
    if not cands:
        return {"ok": False, "reason": "nopool"}

    hit = next((c for c in cands if c["faction"] == key or c["name"] == key), None)
    if not hit:
        hit = next((c for c in cands if key in c["name"] or key in c["faction"]), None)
    if not hit:
        same = fac.FACTIONS.get(str(p.get("faction") or ""))
        if same and (key in same["name"] or key == p.get("faction")):
            return {"ok": False, "reason": "same", "name": same["name"]}
        return {"ok": False, "reason": "notfound", "options": [c["name"] for c in cands]}

    cost = faction_switch_cost(p)
    if int(p.get("gold", 0)) < cost:
        return {"ok": False, "reason": "gold", "need": cost,
                "have": int(p.get("gold", 0))}

    old_name = str(p.get("faction_name") or "")
    p["gold"] = int(p.get("gold", 0)) - cost
    p["faction"] = hit["faction"]
    p["faction_name"] = hit["name"]
    # 转宗不重复发放（同「不发入道物品」）；仅当玩家一朵异火都没有时补发本宗所藏，
    # 既解决老档无火，也让「转去迦南/丹塔」成为一条真实的得火路径。
    flame = grant_faction_flame(p)
    if isinstance(p.get("learned"), list):
        # 新模型：保留已领悟的通用技（跨宗仍会用）；新宗门专属技未领悟不能凭空上阵。
        # 赠 1 门新派低阶入门技作为「改换门庭的传承」，其余靠「学斗技」逐个领悟。
        common = {e["name"] for e in sk_mod.catalog(hit["faction"])
                  if e.get("origin") == "通用"}
        kept = [n for n in p["learned"] if n in common]
        starters = [e["name"] for e in sk_mod.learnable(p)
                    if e.get("origin") != "异火" and e["name"] not in kept]
        if starters:
            kept.append(random.Random(
                "faction-gift:" + str(p.get("qq")) + hit["faction"]).choice(starters))
        p["learned"] = list(dict.fromkeys(kept))
        p["loadout"] = list(p["learned"])[:cfg.LOADOUT_SIZE]
    else:
        p["loadout"] = []                  # 老档保持原行为：清空后按新派补满
        sk_mod.ensure_loadout(p)
    return {"ok": True, "faction": hit["faction"], "name": hit["name"],
            "old": old_name, "cost": cost, "flame": flame,
            "loadout": list(p.get("loadout") or []),
            "pool": hit["pool"]}


def _best_skill(p) -> str:
    """兼容旧接口：取上阵四槽里期望伤害最高的一门。"""
    entries = _loadout_entries(p)
    if not entries:
        return cfg.SKILL_NAMES[0]
    st = stats(p)
    lv_map = p.get("skills") or {}
    best = max(entries, key=lambda e: (st["力量"] * 0.5 + st["斗气"] * 1.5)
               * sk_mod.skill_mult(e, int(lv_map.get(e["name"], 1))))
    return best["name"]


def skill_pool(p) -> list[dict]:
    """本宗全部斗技（含未解锁），带 unlocked / equipped 标记。"""
    _ensure_faction(p)
    ents = sk_mod.entries_for(p) or _legacy_entries()
    lv = int(p.get("lv", 1))
    lo = set(sk_mod.ensure_loadout(p))
    out = []
    for e in ents:
        d = dict(e)
        d["unlocked"] = int(d["unlock"]) <= lv
        d["equipped"] = d["name"] in lo
        out.append(d)
    return out


def skill_pool_sorted(p) -> list[dict]:
    """本宗斗技（含未解锁）按三阶稳定排序：普通 → 高阶 → 特殊。

    宗门卡统计（build_faction_data）与斗技名录共用同一顺序，
    保证「卡片上的门数统计」与 skill_pool 完全同源、不会错位。
    """
    pool = skill_pool(p)
    tier_order = {t: i for i, t in enumerate(cfg.SKILL_TIERS)}
    return sorted(pool, key=lambda e: (tier_order.get(e["tier"], 9),))


def equippable_names(p) -> set[str]:
    """可上阵技名 = 已领悟 ∪ 已收服异火主动技（老档视为全会，单一口径）。

    供 cmd_loadout 在按序号/技名上阵时校验「未领悟先学」，避免把未领悟技
    静默过滤成默认四槽（用户 2026-11-16 照名录卡序号上阵时暴露的体验问题）。
    """
    return set(sk_mod.equippable_names(p))


def roster_no_map(p) -> dict[int, str]:
    """名录卡序号 → 技名（1-based，与卡片显示顺序严格同源）。

    card.build_roster_data 的 ``no`` 与 doupo_roster 卡上的序号圆，都来自
    skill_pool_sorted 的同一顺序；这里把该顺序编成 {1: name, 2: name, ...}，
    供「上阵 1 3 5 7」按号选技。序号含未解锁项（卡片也画未解锁），
    上阵解锁校验由 cmd_loadout 照旧执行。
    """
    return {i: e["name"] for i, e in enumerate(skill_pool_sorted(p), 1)}


def loadout_entries(p) -> list[dict]:
    """公开版：当前上阵四槽的完整技能对象。"""
    return _loadout_entries(p)


def set_loadout(p, names) -> list[str]:
    """设置四槽：只保留本宗已解锁技能，最多 4 个。"""
    allowed = {e["name"] for e in skill_pool(p) if e["unlocked"]}
    p["loadout"] = [n for n in names if n in allowed][:cfg.LOADOUT_SIZE]
    return sk_mod.ensure_loadout(p)


# ---------------------------------------------------------------------------
# 机缘·贵人相助（2026-11-16 用户设计，让机缘与战斗强关联）
# ---------------------------------------------------------------------------
def hero_aid_rate(p) -> float:
    """本场战斗遇贵人相助的概率：极低基础 + 随机缘加成，硬顶 2%。

    * 机缘的战斗关联点之一——人物卡「机缘」不再是摆设：
      机缘越高，越容易在绝境时遇到无名斗皇一招制敌（符合原著气运设定）。
    * 默认概率极低（≤2%），不改变 TTK/胜率平衡；纯稀有彩蛋。
    """
    luck = max(0, int(p.get("luck", 0)))
    rate = float(cfg.HERO_AID_BASE_RATE) + luck / float(cfg.HERO_AID_LUCK_SCALE)
    return min(float(cfg.HERO_AID_MAX_RATE), max(0.0, rate))


def roll_hero_aid(p, rng=random.random) -> dict | None:
    """战斗开场掷一次机缘：命中则返回贵人信息，未命中返回 None。

    独立消耗一次 rng（调用方在回合循环之前调用，不影响战斗内随机序列的语义）。
    返回 ``{"name","rate","luck"}``；名字从 ``HERO_AID_NAMES`` 按 rng 取。
    """
    rate = hero_aid_rate(p)
    if rng() >= rate:
        return None
    names = cfg.HERO_AID_NAMES
    name = names[min(len(names) - 1, int(rng() * len(names)))]
    return {"name": name, "rate": rate, "luck": max(0, int(p.get("luck", 0)))}


def auto_pill_candidates(p, effect: str, locked: set[str] | None = None) -> list[tuple[int, str]]:
    """背包里所有可用的「回血/回气」丹药，按恢复量从小到大排（先省小药）。

    2026-11-19 自动服药：锁定的物品（``locked``）直接跳过 —— 这是玩家给
    「留着卖 / 留着冲关」的药上的护栏，自动逻辑绝不碰。
    只认 effect=hp/mp 的配方或天材地宝，与 ``use_item`` 同一套解析口径。
    """
    locked = locked or set()
    out: list[tuple[int, str]] = []
    cap = int(stats(p)["体力"]) if effect == "hp" else mp_max(p)
    for name, n in bag(p).items():
        if int(n) <= 0 or name in locked:
            continue
        rec = cfg.RECIPE_BY_NAME.get(name) or cfg.MATERIAL_BY_NAME.get(name)
        if not rec or str(rec.get("effect") or "") != effect:
            continue
        gain = max(int(rec.get("value") or 0),
                   int(cap * float(rec.get("pct") or 0)))
        if gain > 0:
            out.append((gain, str(name)))
    out.sort(key=lambda x: (x[0], x[1]))
    return out


def fight(p, mon, rng=random.random, aid=None, roll_aid=True, tower_mod=None,
          puppet=None, formation_n=0, locked=None, auto_pill=True,
          pill_budget=None, puppet_flame_mult=1.0) -> dict:
    """回合制模拟，返回战报。不直接发消息，只改 p。

    ``puppet`` / ``formation_n``（2026-11-18 傀儡）：出战傀儡真实列 dict 与同场
    傀儡数。默认 None/0 → 既有调用逐字节不变。傀儡每回合追加一次「傀儡击」，
    伤害口径走 ``puppet_core.puppet_attack``（不吃 mp/斗技，忠于原著），
    log 进 dmg_log 供战斗回看。它的期望输出已在 ``monster_scaled(p, mon,
    puppet_bonus=...)`` 反解里计入怪血，两处同源（红线）。

    ``tower_mod``（2026-11-17 爬塔扩展点，默认 None 时逐字节不影响任何既有调用）：
      塔词缀/三选一增益通过它注入，**不改内核战斗逻辑、不新增 effect 类型**：
        atk_mult / hp_mult / def_mult —— 怪物数值缩放（狂暴/难度）
        self_effects  —— 玩家每次出手追加的 effect 列表（连击/破防/吸血）
        evade_add / crit_add —— 玩家闪避/暴击率加成（百分点）
        heal_mult —— 玩家吸血/回血倍率（Boss 禁疗 <1）
        reflect —— 怪物反伤比例（Boss 反弹玩家伤害的占比）

    斗气是**跨战斗结转的资源**（2026-11-16 用户「去掉强制回满斗气，不然突破
    就失去了意义」）：开战用玩家当前剩余 ``p["mp"]``，战中按技能阶位逐次扣减，
    战后写回剩余值——不再每战白送满。斗气见底后自动转武器普攻（伤害约 42%），
    仍有输出、能慢慢攒经验，但效率大降；要回满只能靠**突破/升级**（``_refill``）
    或稀有回气手段。这让斗气上限成长（升级/青莲地心火 +25%）与突破真正有意义。

    :param aid: 已掷好的机缘结果（dict）或 None。仅当 ``roll_aid=False`` 时生效。
    :param roll_aid: 是否在开场自己掷机缘。**默认 True**——单敌直接调用
      （PVP / 平衡脚本 / 既有测试）语义完全不变；多敌 ``encounter`` 传 False，
      把掷骰权上收到整场一次，避免每敌各掷导致累积概率突破 2% 硬顶。
    :param locked: 背包里被玩家锁定的物品名集合。自动服药**绝不碰**这些药
      （2026-11-19 用户「锁定的物品不会被自动使用」）。
    :param auto_pill: 是否允许自动服药。爬塔/周本打在深副本上，传 False
      ——它们有专属三选一补给，不能消耗真实背包。

    2026-11-19 战斗过程丰富化（用户要求）：
      * **不控制回合**：删掉 15 回合硬上限（双方每回合至少 1 点伤害，必分胜负）；
        只留 ``HARD_ROUND_CAP=300`` 作异常熔断，触发时按血量比例判胜。
      * **自动服药**：气血 <35% / 斗气 <20% 时从背包取最小可用疗伤丹，
        各限 2 枚，锁定物品跳过。
      * **怪物技能**：每个怪按表概率施放 burst / poison / drain，战报可查。
      * **战斗意外**：怪物小概率崴脚/自乱阵脚（本回合不攻击）；
        玩家小概率「心火共鸣」1~2 回合增伤减伤（原著依据见设计文档）。
    """
    st = stats(p)
    root = cfg.root_factor(p)   # 根基裁定（隐形）：PVE 承伤侧打折；输出侧在 _hit 内
    hp = int(p.get("hp") or st["体力"])
    # 开战沿用当前斗气（结转），不再无条件满蓝；clamp 到上限防越界存档。
    mp = max(0, min(mp_max(p), int(p.get("mp", mp_max(p)))))
    m_hp = int(mon["hp"])
    m_atk = int(mon["atk"])
    m_def = int(mon["def"])

    tm = tower_mod or {}
    if tm:
        m_hp = max(1, int(m_hp * float(tm.get("hp_mult", 1.0))))
        m_atk = max(1, int(m_atk * float(tm.get("atk_mult", 1.0))))
        if tm.get("def_mult"):
            m_def = max(0, int(m_def * float(tm["def_mult"])))

    rounds, dmg_log, taken = 0, [], 0
    # 2026-11-19 战斗事件状态（全是本场局部变量，绝不写回存档）
    locked_set = set(locked or ())
    #: 自动服药计数。多敌遭遇战由 encounter 传**同一份**预算进来，
    #: 否则 5 连战会每战各嗑 2 枚、一场吃掉 10 枚家底。
    pill_used = pill_budget if pill_budget is not None else {"hp": 0, "mp": 0}
    pill_cap = int(pill_used.get("cap") or cfg.AUTO_PILL_MAX_PER_FIGHT)
    fortune_left = 0
    fortune_seen = False
    fortune_round = 0       # 福利发放的回合号（当回合不递减，否则 1 回合福利只剩半程）
    exhausted = False
    _kind = str(mon.get("kind") or "")

    def _try_auto_pill(kind: str) -> None:
        """血/蓝低于阈值时自动服一枚最小可用疗伤丹（锁定物品跳过）。"""
        nonlocal hp, mp
        if not auto_pill or pill_used.get(kind, 0) >= pill_cap:
            return
        cap = int(st["体力"]) if kind == "hp" else mp_max(p)
        cur = hp if kind == "hp" else mp
        ratio = (cfg.AUTO_PILL_HP_RATIO if kind == "hp"
                 else cfg.AUTO_PILL_MP_RATIO)
        if cap <= 0 or cur >= cap * float(ratio):
            return
        for _gain, _name in auto_pill_candidates(p, kind, locked_set):
            if not take_item(p, _name, 1):
                continue
            # 2026-11-20 减疗真落地：BOSS 的 debuff_heal 也压低自动服药回血量。
            # 旧版只压吸血，而玩家主要靠服药回血 → "减疗"几乎无感，是假机制。
            _gain = max(1, int(_gain * fx.heal_mult(p_fx)))
            actual = min(cap, cur + _gain) - cur
            if kind == "hp":
                hp += actual
            else:
                mp += actual
            pill_used[kind] += 1
            dmg_log.append(_mark({"dmg": 0, "kind": "服药", "pill": _name,
                                  "amount": int(actual), "move": "服药",
                                  "auto": True}))
            return

    crit_add = float(tm.get("crit_add", 0))
    lifesteal = 0.02 * int((p.get("flames") or {}).get("三千焱炎火", 0))
    heal_mult = float(tm.get("heal_mult", 1.0))
    self_effects = list(tm.get("self_effects") or [])
    reflect = float(tm.get("reflect", 0))
    var = max(0.0, float(cfg.MON_DMG_VAR))
    # 2026-11-15 多维效果：本场临时状态（战后即弃，绝不写回 p/mon）
    p_fx, m_fx = fx.new_state(), fx.new_state()
    # 2026-11-20 怪物隐藏面板（命中/暴击/闪避，%）。**只认 monster_scaled 挂在
    # 实例上的 panel**：手搓怪（测试/工具直接给 hp/atk 的字典）没有 panel →
    # 三项全 0 → 判定阈值逐字节等于旧版，既有逐招快照零回归。
    _panel = mon.get("panel") or {}
    m_hit = max(0.0, float(_panel.get("hit") or 0.0))
    m_crit = max(0.0, float(_panel.get("crit") or 0.0))
    # 怪物闪避的既有口径是**分数**（规格里手写 mon["dodge"]=0.25 表示 25%），
    # 面板是百分数 —— 这里显式区分：实例显式带走 dodge 就用它，否则读面板换算。
    if "dodge" in mon:
        m_dodge_frac = max(0.0, min(0.9, float(mon["dodge"] or 0)))
    else:
        m_dodge_frac = max(0.0, min(0.9, float(_panel.get("dodge") or 0.0) / 100.0))
    m_fx["evade"] = m_dodge_frac
    # 命中对冲（玩家防守侧）：怪物的命中直接减在玩家闪避上，与 _hit 里
    # 「玩家命中减怪闪避」是同一条公式的另一半。m_hit=0（手搓怪）时不变。
    p_hit = hit_rate(p)
    dodge = max(0.0, min(95.0, dodge_rate(p) + float(tm.get("evade_add", 0))) - m_hit)
    m_ctx = {"target_flags": set(), "target_hp_ratio": 1.0}

    # 2026-11-17 完整战斗回看的「逐条快照」：每条 log 都带当时的敌我血量/斗气，
    # 回看卡直接读字段，不再靠上下文猜。只加字段，不改任何结算顺序。
    def _mark(entry: dict, foe_hp: int | None = None) -> dict:
        entry["round"] = max(1, rounds)
        entry["foe"] = str(mon.get("name") or "敌手")
        entry["hp_after"] = int(hp)
        entry["mp_after"] = int(mp)
        if foe_hp is not None:
            entry["foe_hp_after"] = max(0, int(foe_hp))
        return entry

    # 2026-11-20 原著 BOSS 登场：沉浸式"突如其来的惊喜/惊吓"。
    # 三段式（对齐原著写法与同类游戏惯例）：
    #   ① 剪影 / 外貌气势（appearance，先让玩家看见"什么东西来了"）
    #   ② 报身份称号（move = title，再让玩家知道"这是谁"）
    #   ③ 接台词或动作引子（intro，最后听他说第一句话）
    # 用 intro=True 标记，dmg=0 —— 不进任何伤害/回合统计，回看卡单独提成引言块。
    _character = mon.get("character")
    _appear = str((_character or {}).get("appearance") or "").strip()
    if not _appear and _character:
        # 双保险：角色包没挂 appearance 时（老档 log、手搓怪、PVP 拼出来的角色包）
        # 统一回落到 CHARACTER_APPEARANCE 这张唯一真值表，避免「有的怪有出场描写、
        # 有的没有」这种靠路径运气的不一致。
        _appear = str(cfg.character_appearance(
            _character.get("name") or mon.get("name")) or "").strip()
    _intro = str((_character or {}).get("intro") or "").strip()
    if _character and (_appear or _intro):
        _notes = []
        if _appear:
            _notes.append(_appear)
        if _intro and _intro != _appear:
            _notes.append(_intro)
        dmg_log.append(_mark({
            "dmg": 0, "kind": "登场", "intro": True,
            "move": str(_character.get("title") or "强敌"),
            "speaker": str(_character.get("name") or mon.get("name") or "敌手"),
            "notes": _notes,
        }))

    # 机缘·贵人相助：默认单敌调用自己掷一次（极低概率），命中则一招制敌。
    # 多敌 encounter 传 roll_aid=False + 传入整场预掷的 aid，把掷骰权上收到
    # 整场一次——修复「每敌各掷 → 累积概率突破 2% 硬顶」的 bug（2026-11-16）。
    # 无论哪种路径，都只消耗一次 rng，不影响战斗内随机序列的可复现性。
    if roll_aid:
        aid = roll_hero_aid(p, rng)
    if aid:
        m_hp = 0
        dmg_log.append(_mark({"dmg": int(mon["hp"]), "kind": "贵人相助", "source": "机缘",
                              "helper": aid["name"], "aid": True, "skill": False,
                              "move": "贵人相助"}, m_hp))

    while not aid and rounds < int(cfg.HARD_ROUND_CAP) and m_hp > 0 and hp > 0:
        rounds += 1
        hp_before, mp_before, foe_hp_before = hp, mp, m_hp
        # 怪物毒技能：DoT 统一由 skill_effects 状态机结算（见下），
        # 旧的 poison_turns 计数器已删除——两套并存会把两回合毒算成四回合。
        # 2026-11-20 原著 BOSS 机制：玩家回合开始结算挂在自己身上的 DoT / 眩晕。
        # 普通怪 p_fx 全空 → on_turn_start 返回全 0，既有战斗逐字节不变。
        _pfx_start = fx.on_turn_start(p_fx, rng)
        if _pfx_start["dot_dmg"]:
            hp -= _pfx_start["dot_dmg"]
            taken += _pfx_start["dot_dmg"]
            dmg_log.append(_mark({"dmg": _pfx_start["dot_dmg"], "kind": "中毒",
                                  "taken": True, "dot": True, "move": "中毒",
                                  "notes": _pfx_start["notes"]}))
            if hp <= 0:
                break
        # 自动服药：先补给再出手（血低时先吃药是当前玩家的直觉操作）
        _try_auto_pill("hp")
        _try_auto_pill("mp")
        entry, cost = _pick_skill(p, mp, rng)   # 随机释放「已上阵且斗气够」的技
        # 禁技（2026-11-19 修）：被怪挂 silence 或眩晕时本回合只能武器普攻，斗气不扣。
        if int(p_fx.get("silence", 0)) > 0 or _pfx_start["skip"]:
            entry, cost = None, 0
        m_ctx["target_hp_ratio"] = m_hp / max(1, mon["hp"])
        hit = _hit(p, m_def, entry, rng, p_fx, m_fx, m_ctx,
                   crit_add=crit_add, extra_effects=self_effects, atk_hit=p_hit)
        # 心火共鸣：玩家 1~2 回合增伤（承伤减免在怪击那段）
        # 注意**不在这里递减**：递减放在本回合双方都行动完之后，否则 1 回合的
        # 共鸣只有增伤吃到、减伤吃不到（怪行动时计数已经归零）。
        if fortune_left > 0:
            hit["dmg"] = max(1, int(hit["dmg"] * float(cfg.FORTUNE_DMG_MULT)))
            hit["fortune"] = True
        if entry is not None:
            mp -= cost
        m_hp -= hit["dmg"]
        hit.update({
            "hp_before": int(hp_before), "mp_before": int(mp_before),
            "mp_cost": int(cost if entry is not None else 0),
            "foe_hp_before": max(0, int(foe_hp_before)),
        })
        dmg_log.append(_mark(hit, m_hp))
        # 傀儡击：玩家出手后常驻追击一次（E32/E33/E35，非概率外援）。
        # 肉身伤害不吃 mp/斗技、不吃暴击加成；用同一 mitigation 减伤；
        # log 字段齐 .get() 消费（2026-11-16 回包事故）。
        if puppet and m_hp > 0:
            from . import puppet_core as pc  # 延迟导入避免与 core 环
            p_atk = pc.puppet_attack(puppet)
            # B 线傀儡异火加成：与怪血反解用的 puppet_expected_damage(flame_mult) 同源。
            p_atk = p_atk * max(1.0, float(puppet_flame_mult))
            p_dmg = max(1, int(p_atk * mitigation(m_def)))
            p_dmg = min(p_dmg, m_hp)
            m_hp -= p_dmg
            p_entry = pc.puppet_log_entry(puppet, p_dmg, formation_n)
            dmg_log.append(_mark(p_entry, m_hp))
        # 吸血：异火被动 + 技能自带（只回血，不改伤害主值）× 塔禁疗倍率
        # 减疗（2026-11-19 修）：对手挂到 p_fx 的 debuff_heal 压低本次回血。
        heal_frac = (lifesteal + float(hit.get("heal_ratio", 0))) * heal_mult * fx.heal_mult(p_fx)
        if heal_frac > 0:
            hp = min(st["体力"], hp + int(hit["dmg"] * heal_frac))
        # 塔·怪物反伤：玩家造成伤害后按比例反弹（Boss 词缀）
        if reflect > 0 and hit["dmg"] > 0:
            back = max(1, int(hit["dmg"] * reflect))
            hp -= back
            taken += back
            dmg_log.append(_mark({"dmg": back, "kind": "反伤", "taken": True,
                                  "reflect": True, "move": "反伤"}, m_hp))
        # 吸血/反伤都发生在本次出手之后，回头把第一条的 hp_after 改成最终值
        hit["hp_after"] = int(hp)
        # 逐维文案并入主 hit 的 notes（主日志 kind/dmg/skill 字段一律不动）

        if m_hp <= 0:
            break
        # 目标行动前：结算挂在怪身上的 DoT + 眩晕/定身
        start = fx.on_turn_start(m_fx, rng)
        if start["dot_dmg"]:
            m_hp -= start["dot_dmg"]
            dmg_log.append(_mark({"dmg": start["dot_dmg"], "kind": "持续伤害", "dot": True,
                                  "move": "持续伤害", "notes": start["notes"]}, m_hp))
        if m_hp <= 0:
            break
        # 怪反击（被眩晕/定身则跳过）
        if not start["skip"]:
            # 怪物失误：原著「脚掌一崴，一屁股坐在地上」（第191章）/
            # 「手忙脚乱，自乱阵脚」（第601章）。本回合怪不攻击。
            mishap = float(cfg.MISHAP_CHANCE)
            # 档位判定统一走 mon_tier：塔/周本的 kind 是 "塔·elite"/"周本·elite"，
            # 旧版 `"精英" in _kind` 永远 False → 精英层失误率没减半。
            if mon.get("is_boss") or mon_tier(_kind) in ("elite", "boss"):
                mishap *= float(cfg.MISHAP_BOSS_FACTOR)
            if rng() < mishap:
                _why = ("脚掌一崴，一屁股坐在地上" if "魔兽" in _kind
                        else "手忙脚乱，自乱阵脚")
                # move 只放短标签（战报一行要读得下去），完整描写进 notes
                dmg_log.append(_mark({"dmg": 0, "kind": "失误", "mishap": True,
                                      "move": "踉跄", "notes": [_why]}))
            elif rng() * 100 < dodge:
                dmg_log.append(_mark({"dmg": 0, "kind": "闪避", "miss": True,
                                      "move": "闪避"}))
            else:
                roll = 1.0 - var + 2.0 * var * rng()
                # 回看卡「承伤构成」：atk_raw = 防御前原始，dealt0 = 防御后/技能前。
                # 三个字段只供展示，不参与任何数值计算（旧行为逐字节不变）。
                atk_raw = max(1, int(m_atk * roll))
                # 降攻效果（2026-11-19 修）：玩家技挂 debuff_atk 到 m_fx，
                # 怪实际出手前按 target_atk_mult 打折。无降攻时系数=1，逐字节不变。
                m_atk_eff = m_atk * fx.target_atk_mult(m_fx)
                dealt_atk_raw = max(1, int(m_atk_eff * roll))
                # 根基裁定（隐形）：虚浮者防御同样打折 → 同怪受到更多伤害。
                # PVE 玩家承伤处；root=1（适量/正常修炼）时逐字节不变。
                dealt = max(1, int(m_atk_eff * mitigation(st["防御"] * root) * roll))
                dealt0 = dealt
                # 怪物技能（2026-11-19）：概率施放，只作用于本回合，不改面板。
                # 效果一律并入「兽击」那一条 log 的 notes —— 不额外插 dmg=0 的条目，
                # 否则回看卡会把一次攻击渲染成两行、并把总量算重。
                sk_note, sk_name = [], ""
                # 禁技（2026-11-19 修）：玩家技挂 silence 到 m_fx，本回合怪放不出技能
                sk = mon.get("skill") if int(m_fx.get("silence", 0)) <= 0 else None
                if sk:
                    _sc = float(sk.get("chance") or cfg.MONSTER_SKILL_BASE_CHANCE)
                    if mon.get("is_boss"):
                        _sc *= 2.0
                    elif mon_tier(_kind) == "elite":
                        _sc *= 1.5
                    if rng() < _sc:
                        _se, _sm = str(sk.get("effect") or ""), float(sk.get("mult") or 0)
                        sk_name = str(sk.get("name") or "怪技")
                        if _se == "burst":
                            dealt = max(1, int(dealt * _sm))
                            sk_note.append("%s！伤害×%.1f" % (sk_name, _sm))
                        elif _se == "drain":
                            _drain = max(1, int(mp_max(p) * _sm))
                            mp = max(0, mp - _drain)
                            sk_note.append("%s！斗气被夺 -%d" % (sk_name, _drain))
                        elif _se == "poison":
                            # 2026-11-20 修真 bug：旧版只算 poison_dmg 写进 notes，
                            # 从没挂到 p_fx["dot"] —— 玩家根本不掉血，是假效果。
                            # 现在真挂 DoT：base=本次毒伤，v=1.0，两回合各扣一次。
                            # ⚠️ 只走 p_fx 一条路：旧 poison_turns 计数器会与 DoT
                            # 叠加成 4 次扣血，已整段删除（见 on_turn_start 调用处）。
                            poison_dmg = max(1, int(dealt * _sm))
                            p_fx["dot"].append({"v": 1.0, "d": 2,
                                                "kind": sk_name, "base": poison_dmg})
                            sk_note.append("%s！中毒 %d×2 回合" % (sk_name, poison_dmg))
                        else:
                            dealt = max(1, int(dealt * max(1.0, _sm)))
                            sk_note.append(sk_name)
                        # 2026-11-20 原著 BOSS：技能命中时滚动播一条原著台词，
                        # 并把"恶心机制"（禁技/眩晕/反伤/减疗/降攻/毒）挂到玩家 p_fx。
                        # mech 复用 skill_effects.on_hit，效果类型全在既有系统内，无新轮子。
                        if _character:
                            _quotes = tuple(_character.get("quotes") or ())
                            if _quotes:
                                _q = _quotes[int(rng() * len(_quotes)) % len(_quotes)]
                                sk_note.append("「%s」" % _q)
                            _mech = _character.get("mech")
                            if _mech:
                                _mn = str(_mech.get("kind") or sk_name)
                                _mech_eff = dict(_mech)
                                _mech_eff["t"] = _mech.get("t") or "debuff_atk"
                                # 2026-11-20 复核更正：这里原有一行
                                #   if t == "dot": _mech_eff["base"] = ...
                                # 是**死代码**——skill_effects.on_hit 的 dot 分支一律用
                                # 本次命中伤害 dmg 作 base（"base": max(1, int(dmg))），
                                # 根本不读 e["base"]。已删，避免以后有人当它是有效开关。
                                # 真正保证"机制毒不是每回合掉 1 点"的是 on_hit 那行。
                                if _mech_eff["t"] == "reflect":
                                    # 反伤要被玩家受击侧 on_incoming(p_fx) 读到：
                                    # fx.on_hit 把 reflect 写到 self_state，故这里
                                    # self=p_fx（反伤归玩家），不能像其他机制那样 self=m_fx。
                                    fx.on_hit([_mech_eff], p_fx, p_fx, dealt, rng,
                                              ctx={"target_flags": set(), "target_hp_ratio": 0.5})
                                else:
                                    fx.on_hit([_mech_eff], m_fx, p_fx, dealt, rng,
                                              ctx={"target_flags": set(), "target_hp_ratio": 0.5})
                                sk_note.append("%s！" % _mn)
                # 怪击暴击（2026-11-20 隐藏面板）：与伤害浮动**共用同一次采样**。
                # roll 是那次采样的单调变换，判「roll 落在区间顶端 2·var·c 内」
                # 等价于独立掷一次暴击（概率精确等于 c），却不多消耗一次 rng ——
                # 既有逐招快照的随机序列不被打乱（test_battle_review 211 条）。
                # ⚠️ MON_DMG_VAR=0 时没有浮动也就没有暴击（同一次采样的必然结果）。
                _m_crit = (m_crit > 0 and var > 0
                           and roll > 1.0 + var - 2.0 * var * min(1.0, m_crit / 100.0))
                if _m_crit:
                    dealt = max(1, int(dealt * 1.5))
                # 心火共鸣：承伤减免（原著「短暂晕眩足以令战局顷刻间转变」）
                if fortune_left > 0:
                    dealt = max(1, int(dealt * float(cfg.FORTUNE_TAKEN_MULT)))
                    sk_note.append("心火护体，伤害减轻")
                # 玩家侧减伤/闪避/反伤（卸力水镜/雷蝠天翼/燕返击）
                # 命中对冲：怪物的命中同样压玩家的**临时**闪避（技能给的身法），
                # 否则「命中」只对基础闪避生效，留一个白嫖洞。
                inc = fx.on_incoming(p_fx, dealt, rng, hit=m_hit / 100.0)
                hp -= inc["final"]
                taken += inc["final"]
                log_entry = {"dmg": inc["final"], "kind": "兽击", "taken": True,
                             "move": "兽击",
                             # 怪击暴击：回看卡词条行据此打「暴击」标记，
                             # 解释「实收 > 面板−防御」的缺口（纯展示字段）。
                             "crit": bool(_m_crit),
                             # 回看卡「承伤构成」：原始 → 防御削减 → 减伤吸收 → 实际。
                             # 纯展示字段，不参与结算（旧行为逐字节不变）。
                             "atk_raw": int(dealt_atk_raw),
                             "def_cut": max(0, int(dealt_atk_raw - dealt0)),
                             "absorbed": max(0, int(dealt - inc["final"])),
                             # 降攻生效展示：原始攻击被降攻压低（纯展示，不参与结算）
                             "atk_debuff": max(0, atk_raw - dealt_atk_raw)}
                notes = list(sk_note) + list(inc["notes"] or [])
                if notes:
                    log_entry["notes"] = notes
                if sk_name:
                    log_entry["monster_skill"] = sk_name
                if fortune_left > 0:
                    log_entry["fortune_guard"] = True
                dmg_log.append(_mark(log_entry, m_hp))
                if inc["reflect"]:
                    m_hp -= inc["reflect"]
                    dmg_log.append(_mark({"dmg": inc["reflect"], "kind": "反伤",
                                          "reflect": True, "move": "反伤"}, m_hp))
        else:
            dmg_log.append(_mark({"dmg": 0, "kind": "被控", "miss": True,
                                  "move": "被控"}))
        # 玩家福利：每场最多一次，1~2 回合的短时气机顺畅（不是永久 buff）
        if not fortune_seen and rng() < float(cfg.FORTUNE_CHANCE):
            lo, hi = cfg.FORTUNE_ROUNDS
            fortune_left = int(rng() * (int(hi) - int(lo) + 1)) + int(lo)
            fortune_seen = True
            fortune_round = rounds      # 本回合刚开始生效，不许当回合就递减
            dmg_log.append(_mark({"dmg": 0, "kind": "心火共鸣", "fortune": True,
                                  "move": "心火共鸣",
                                  "notes": ["气机顺畅，接下来 %d 回合增伤减伤" % fortune_left]}))
        # 本回合双方都行动完才递减（且发放当回合不递减），保证增伤与减伤同进同出
        if fortune_left > 0 and rounds > fortune_round:
            fortune_left -= 1
        fx.expire_turn(p_fx)
        fx.expire_turn(m_fx)

    win = m_hp <= 0
    if rounds >= int(cfg.HARD_ROUND_CAP) and m_hp > 0 and hp > 0:
        # 异常熔断（不是玩法回合上限）：按剩余血量比例判胜，绝不静默判负。
        ratio_p = hp / max(1, int(st["体力"]))
        ratio_m = m_hp / max(1, int(mon["hp"]))
        win = ratio_p > ratio_m
        exhausted = True
    # 回看卡「终局」：先留住**战斗结束时**的真实血/斗气，再改写存档。
    # 2026-11-19 用户：「回看显示 0 血量，人物卡片显示还有血量」—— 旧版回看读
    # hp_end（真值，可能 -287），人物卡读战败重置的 70%，两处对不上。现在统一：
    # 战败**不归零**，结算就是「上限 × LOSE_HP_FLOOR（5%）」，回看卡与人物卡
    # 用同一个数；`downed` 只用来挑收场文案（被打到 0 以下 = 差点没命 = 被救）。
    downed = int(hp) <= 0
    floor_hp = max(1, int(st["体力"] * cfg.LOSE_HP_FLOOR))
    hp_end = max(1, int(hp)) if win else floor_hp
    mp_end = max(0, int(mp))
    foe_hp_end = max(0, int(m_hp))
    p["hp"] = hp_end
    p["mp"] = mp   # 斗气结转：写回本场消耗后的剩余值，不再强制回满
    if downed:
        # 战败收尾：把「被打到 0 以下」那几条 log 的气血快照统一成保底值。
        # 不改就是逐招末行写「余血 0」、终局块写「余血 832」——正是用户 2026-11-19
        # 报的「两处对不上」。保底规则下，那一刻的真实剩余就是这 5%。
        for e in reversed(dmg_log):
            if e.get("hp_after") is None:
                break
            if int(e["hp_after"]) > 0:
                break
            e["hp_after"] = hp_end

    # 贵人相助：毫发无伤、1 回合速胜，胜负结算照常走下方 win 分支。
    shown_rounds = max(1, rounds)
    res = {
        "win": win, "rounds": shown_rounds, "log": dmg_log,
        "taken": taken, "mon": mon, "mon_hp_left": max(0, m_hp),
        "hero_aid": aid,  # None 或 {name,rate,luck}，文案层据此标注机缘因果
        "puppet": (str(puppet.get("name") or "傀儡") if puppet else None),
        "formation": int(formation_n or 0),
        # 2026-11-19 战斗事件（供战报/回看卡渲染；数值层不消费）
        # 终局快照：战斗结束那一刻的真实血/斗气（不是战后重置值），回看卡据此
        # 显示「气血归零而败」而不是误导性的战后血量。
        "hp_end": hp_end, "mp_end": mp_end, "foe_hp_end": foe_hp_end,
        # downed：这一场有没有被真打到气血 0 以下（判负的直接原因）。
        # 只影响收场文案（被小医仙所救 / 落荒而逃），不参与任何数值结算。
        "downed": bool(downed),
        # 对手血量上限：回看卡「终局」条要按比例画条，没有分母就画不出形变
        "foe_hp_max": max(0, int(mon.get("hp") or 0)),
        "exhausted": bool(exhausted),
        "auto_pills": {"hp": int(pill_used.get("hp", 0)),
                       "mp": int(pill_used.get("mp", 0))},
        "fortune": bool(fortune_seen),
        # 原著「战斗中夺取火能」：由命令层写进 alchemy_tower 表（真实列）。
        "fire_energy_gain": max(1, int(mon.get("lv", 1)) // int(cfg.TOWER_ENERGY_PER_LV)),
    }
    if win:
        p["wins"] = int(p.get("wins", 0)) + 1
        exp = int(mon["exp"] * flame_exp_mult(p))
        p["exp"] = int(p.get("exp", 0)) + exp
        gold = int(mon["gold"] * (cfg.DROP_GOLD_MULT[0] + rng()))
        p["gold"] = int(p.get("gold", 0)) + gold
        res.update(exp=exp, gold=gold, drops=_roll_drops(p, rng, mon))
    else:
        p["losses"] = int(p.get("losses", 0)) + 1
        # 气血已在上面统一成「上限 × LOSE_HP_FLOOR」，这里不再二次改写：
        # 回看卡的终局条与人物卡必须是同一个数（2026-11-19 用户指出两处不一致）。
        res["hp_after"] = p["hp"]
    return res


def roll_encounter_count(p, rng=random.random) -> int:
    """本场遭遇几个敌人（1~5，用户 2026-11-16 需求）。

    分布刻意偏向少敌（1 敌 ≈40%），多敌递减——5 连战是稀有"硬仗"，
    不是日常。用「掷 5 次伯努利 + 至少 1 个」而非均匀随机，避免一半战斗
    都是 5 连战把节奏拖垮。高等级/高机缘略有加成（见下的 luck 微调）。
    """
    lo = int(cfg.ENCOUNTER_MIN)
    hi = int(cfg.ENCOUNTER_MAX)
    luck = max(0, int(p.get("luck", 0)))
    # 每个敌人出现概率：基础 0.42，随机缘每 20 点 +0.02（硬顶 0.60）
    per = min(0.60, 0.42 + luck / 2000.0)
    n = sum(1 for _ in range(hi) if rng() < per)
    return max(lo, min(hi, n))


def pick_encounter(p, n, rng=random.random) -> list[dict]:
    """抽 n 个敌人 species：优先与玩家同档，且尽量跨类型（魔兽/悍匪/反派）。

    复用 pick_monster 的"同大境界窗口"口径保证不会出现越级秒杀；
    再去重 + 跨 kind，让一场遭遇读起来像"魔兽 + 山贼"而不是 5 只同种鼠。
    """
    n = max(1, int(n))
    lv = int(p.get("lv", 1))
    window = [m for m in cfg.MONSTERS if abs(int(m["lv"]) - lv) <= cfg.LV_PER_REALM]
    pool = window or cfg.MONSTERS
    # 等级贴近度排序后取一个稍大的候选池（同档至少 6 只），再随机抽
    cand = sorted(pool, key=lambda m: abs(int(m["lv"]) - lv))
    cand = cand[:max(n * 2, 6)]
    out: list[dict] = []
    used: set[str] = set()
    # 第一轮：每个 kind 先拿一只，保证类型多样
    kinds = sorted({str(m.get("kind", "魔兽")) for m in cand})
    for kind in kinds:
        if len(out) >= n:
            break
        same = [m for m in cand if str(m.get("kind", "魔兽")) == kind and m["name"] not in used]
        if same:
            pick = rng_choice(same, rng)
            out.append(pick)
            used.add(pick["name"])
    # 第二轮：补足数量（允许重复 kind，但物种不重复）
    rest = [m for m in cand if m["name"] not in used]
    while len(out) < n and rest:
        pick = rng_choice(rest, rng)
        out.append(pick)
        used.add(pick["name"])
        rest = [m for m in rest if m["name"] not in used]
    # 候选池耗尽（极端：n=5 但同档只有 3 种）——允许同种再来一只
    while len(out) < n and cand:
        out.append(rng_choice(cand, rng))
    return out[:n]


def encounter(p, rng=random.random, locked=None, auto_pill=True) -> dict:
    """一场遭遇战：连续打 1~5 个敌人，逐个调用 ``fight``（单敌内核零改动）。

    设计要点（守既有纪律）：
      * **逐个结算**：每个敌人都是一次完整 ``fight`` —— 斗气/气血跨战结转、
        伤害/DoT/控制/机缘全复用同一套内核，不在本层复制战斗逻辑（业务零分叉）。
      * **斗气结转**：开战用玩家当前剩余斗气，逐敌扣减、不再每战回满；见底转普攻。
      * **气血结转**：上一战剩多少血就带多少进下一战；每放倒一个敌人回复
        18% 最大气血（ENCOUNTER_REST_HEAL），避免 5 连战必死但不回满。
      * **连战衰减**：第 i 个敌人的 atk/hp 按 ENCOUNTER_FADE 递减（≤20%），
        让"多敌硬仗"总难度可控，不至于越打越必输。
      * **败即收兵**：任一战败，整场判负（不继续打后面的敌人）。
      * **聚合**：rounds/log/taken/exp/gold/drops 全部累加，供展示层做高光剪辑；
        ``detail`` 保留每个敌人的独立战报（名字/胜负/回合/ log），供战报分节。
      * ``fight`` 内部已写 p 的 wins/losses/exp/gold/hp —— 本层不再重复结算，
        只做气血结转与统计聚合（唯一的写入是 p["hp"] 的连战回血）。
    """
    specs = pick_encounter(p, roll_encounter_count(p, rng), rng)
    st = stats(p)
    fade = max(0.0, float(cfg.ENCOUNTER_FADE))
    heal = max(0.0, float(cfg.ENCOUNTER_REST_HEAL))

    # 机缘·贵人相助：**整场只掷一次**（2026-11-16 修正）。
    # 旧实现让 fight() 每敌各掷一次，N 敌场累积概率 1-(1-2%)^N，5 敌≈9.6%，
    # 突破「硬顶 2%」设计口径；且只要任一敌触发就报「此战毫发无伤」，
    # 但其他敌照打、玩家照常受伤，文案自相矛盾。
    # 现在：整场开场掷一次——命中则贵人把整场敌手一招全灭（每敌传 aid、
    # roll_aid=False，各自 taken=0、正常结算），未命中则全程禁掷。
    whole_aid = roll_hero_aid(p, rng)

    detail: list[dict] = []
    total_rounds = total_taken = total_exp = total_gold = 0
    fire_gain = 0
    all_drops: list[tuple[str, int]] = []
    win = True
    hero_aid = whole_aid
    # 自动服药预算整场共享：多敌连战按敌数放宽，但仍有硬顶（见 config 注释）。
    pill_budget = {"hp": 0, "mp": 0,
                   "cap": min(6, int(cfg.AUTO_PILL_MAX_PER_FIGHT) * max(1, len(specs)))}
    for i, spec in enumerate(specs):
        mon = dict(monster_scaled(p, spec))
        # 连战第 i 敌削弱（首敌不削），只缩放 hp/atk，def 与产出不动
        scale = max(0.8, 1.0 - i * fade)
        if i:
            mon["hp"] = max(1, int(mon["hp"] * scale))
            mon["atk"] = max(1, int(mon["atk"] * scale))
        # 战间回血：上一战 fight 已把 p["hp"] 写成剩余值，这里补一口
        if i:
            p["hp"] = min(int(st["体力"]),
                          int(p.get("hp") or 0) + int(st["体力"] * heal))
        # 整场机缘已预掷：传 aid + roll_aid=False，禁止每敌重复掷骰。
        r = fight(p, mon, rng=rng, aid=whole_aid, roll_aid=False,
                  pill_budget=pill_budget, locked=locked, auto_pill=auto_pill)
        total_rounds += int(r["rounds"])
        total_taken += int(r["taken"])
        total_exp += int(r.get("exp") or 0)
        total_gold += int(r.get("gold") or 0)
        if r["win"]:
            fire_gain += int(r.get("fire_energy_gain") or 0)
        for name, cnt in (r.get("drops") or []):
            all_drops.append((name, int(cnt)))
        detail.append({
            "name": spec["name"], "kind": str(spec.get("kind") or "魔兽"),
            "lv": int(mon["lv"]), "win": bool(r["win"]),
            "rounds": int(r["rounds"]), "taken": int(r["taken"]),
            "log": r["log"], "index": i,
        })
        if not r["win"]:
            win = False
            break
    # 汇总掉落（同名合并）
    merged: dict[str, int] = {}
    for name, cnt in all_drops:
        merged[name] = merged.get(name, 0) + cnt
    # 终局：取最后一场 fight 的真实结束值（前面场次的血/斗气已结转进下一场，
    # 所以最后一场就是整场结束那一刻）。缺字段的旧调用方按 .get 消费，不炸。
    _last = r if specs else {}
    return {
        "win": win, "rounds": total_rounds, "taken": total_taken,
        "exp": total_exp, "gold": total_gold,
        "drops": list(merged.items()), "log": [d for x in detail for d in x["log"]],
        "detail": detail, "enemies": [x["name"] for x in detail],
        "enemy_count": len(detail), "hero_aid": hero_aid,
        "hp_end": _last.get("hp_end"), "mp_end": _last.get("mp_end"),
        "foe_hp_end": _last.get("foe_hp_end"),
        "foe_hp_max": int((_last.get("mon") or {}).get("hp") or 0),
        "downed": bool(_last.get("downed")),
        "exhausted": bool(_last.get("exhausted")),
        "fortune": bool(_last.get("fortune")),
        # 2026-11-19：整场夺取的火能（仅胜利累加）+ 自动服药总数（供文案）
        "fire_energy_gain": int(fire_gain),
        "auto_pills": {"hp": int(pill_budget.get("hp", 0)),
                       "mp": int(pill_budget.get("mp", 0))},
    }


# ---------------------------------------------------------------------------
# 完整战斗回看（2026-11-17）—— 唯一的「战斗结束」落盘点
#
# 为什么不在 fight() 内直接写：爬塔/周本用玩家深副本战斗，战斗结果只回会话，
# 写 p 只会污染临时对象。由 commands 层在真实玩家上调用本函数，所有战斗入口
# 共用同一份结构，既不改 SQLite schema，也不新增任何落盘路径。
# ---------------------------------------------------------------------------
def _jsonable(value):
    """log 里的 notes 可能是非 str 对象；落盘前统一夹成 JSON 可序列化值。"""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    return str(value)


def record_last_battle(p: dict, kind: str, res: dict, opponent: str = "",
                       extra: dict | None = None) -> dict:
    """把一场战斗的完整逐条 log 快照写入 ``p["last_battle"]``。

    :param p: **真实玩家存档**（爬塔/周本传永久档，不传战斗用深副本）
    :param kind: 遭遇 / 切磋 / 掠夺 / 爬塔 / 周副本
    :param res: fight/encounter/duel/tower_floor/weekly_floor 的返回值
    :param opponent: 供卡片标题展示的对手名
    :param extra: 各玩法特有字段（floor/week/kind_tag 等），只进摘要不进 log
    """
    st = stats(p)
    logs = []
    for i, item in enumerate(res.get("log") or [], 1):
        if not isinstance(item, dict):
            continue
        row = {str(k): _jsonable(v) for k, v in item.items()}
        row.setdefault("index", i)
        logs.append(row)

    # 终局血/斗气：优先取 fight() 留下的 hp_end/mp_end（战斗结束那一刻），
    # 没有才回退存档值。战败时 p["hp"] 已被重置成上限×0.7（及调息），
    # 直接读 p 会显示「还有大半管血却判负」——用户 2026-11-19 质疑的假象。
    _hp_end = res.get("hp_end")
    _mp_end = res.get("mp_end")
    hp_show = int(_hp_end) if _hp_end is not None else int(p.get("hp") or 0)
    mp_show = int(_mp_end) if _mp_end is not None else int(p.get("mp") or 0)
    foe_hp_end = res.get("foe_hp_end")

    # 收场原因（回看卡顶部直写，不许让玩家自己猜）。
    # 2026-11-19 用户：「不要 0 血量…战斗结尾就显示落荒而逃或者被小医仙所救就行」。
    # 战败不再有「气血耗尽」这一说（气血保底 5%，人没死），改用世界观内的收场：
    #   * downed（真被打到 0 以下）→ 被小医仙所救（她把你从鬼门关拉回来）
    #   * 还有血却判负（血量比判负 / 力竭 / 切磋）→ 落荒而逃
    win = bool(res.get("win"))
    exhausted = bool(res.get("exhausted"))
    downed = bool(res.get("downed"))
    if win and exhausted:
        end_reason, end_text = "exhausted_win", "力竭判胜"
    elif win:
        end_reason, end_text = "kill", "击杀对手"
    elif exhausted:
        end_reason, end_text = "exhausted_lose", "力竭判负"
    elif str(kind or "") == "切磋":
        # 切磋是零惩罚比武，谈不上「逃」，也谈不上被救
        end_reason, end_text = "outmatched", "甘拜下风"
    elif downed:
        end_reason, end_text = "rescued", "被小医仙所救"
    else:
        end_reason, end_text = "fled", "落荒而逃"

    snap = {
        "version": 1,
        "ts": time.time(),
        "kind": str(kind or "战斗"),
        "win": win,
        "rounds": int(res.get("rounds") or 0),
        "opponent": str(opponent or ""),
        "player": {
            "name": str(p.get("name") or "无名"),
            "lv": int(p.get("lv") or 1),
            "power": int(power(p)),
            "hp": int(hp_show), "hp_max": int(st["体力"]),
            "mp": int(mp_show), "mp_max": int(mp_max(p)),
        },
        "summary": {
            "taken": int(res.get("taken") or 0),
            "exp": int(res.get("exp") or 0),
            "gold": int(res.get("gold") or 0),
            "drops": [[str(n), int(c)] for n, c in (res.get("drops") or [])],
            "enemy_count": int(res.get("enemy_count") or 0),
            "enemies": [str(x) for x in (res.get("enemies") or [])],
            "hero_aid": _jsonable(res.get("hero_aid")),
            "hp_p": res.get("hp_p"), "hp_v": res.get("hp_v"),
            # 2026-11-19 战斗事件的三项结论：缺了这三个字段，回看卡就画不出
            # 「力竭判胜 / 自动服药 / 心火共鸣」—— 今天的战斗逻辑等于没同步。
            "exhausted": bool(res.get("exhausted")),
            "auto_pills": {"hp": int((res.get("auto_pills") or {}).get("hp") or 0),
                           "mp": int((res.get("auto_pills") or {}).get("mp") or 0)},
            "fortune": bool(res.get("fortune")),
            # 终局与败因：foe_hp_end = 战斗结束时对手剩余血；end_reason 供卡片直写
            # 「气血耗尽 / 力竭判负 / 击杀对手」，不再让玩家自己猜为什么输。
            "foe_hp_end": (int(foe_hp_end) if foe_hp_end is not None else None),
            "foe_hp_max": int(res.get("foe_hp_max") or 0),
            "end_reason": end_reason,
            "end_text": end_text,
            "extra": _jsonable(extra or {}),
        },
        "log": logs,
    }
    p["last_battle"] = snap
    return snap


# ---------------------------------------------------------------------------
# 爬塔副本（2026-11-17）—— 见 docs/斗破爬塔副本_设计方案.md
#
# 纪律：
#   * 塔战斗用 player 的**深副本**，会话临时增益并入副本，战斗结果只回写会话，
#     绝不污染永久存档的 hp/mp/wins/exp；只有死亡/结算时才据层数结算一次。
#   * 怪物数值仍走 monster_scaled 反解，难度只通过虚拟 species 的 hard 调，
#     不碰 MON_ROUND_RATIO / TTK_PLAYER 全局平衡常量。
#   * 词缀/增益全部映射到 fight 的 tower_mod 扩展点，不新增 effect 类型。
# ---------------------------------------------------------------------------
def tower_mod_from_affixes(affixes: list[str], temp_effects: list[dict]) -> dict:
    """把 Boss 词缀 + 玩家三选一效果折算成 fight 的 tower_mod。"""
    mod = {"hp_mult": 1.0, "atk_mult": float(td.TOWER_ATK_SCALE), "def_mult": 1.0,
           "evade_add": 0.0, "crit_add": 0.0, "heal_mult": 1.0,
           "reflect": 0.0, "self_effects": []}

    # 怪物侧词缀（AFFIXES 的 effect → 数值化注入）
    for a in affixes or []:
        e = td.AFFIXES.get(a)
        if not e:
            continue
        t, v = e["t"], float(e.get("v", 0))
        if t == "empower":          # 狂暴 → 怪物攻击提升
            mod["atk_mult"] *= 1.0 + v
        elif t == "lifesteal":      # 再生 → 怪物更耐打（等效血上限）
            mod["hp_mult"] *= 1.0 + v
        elif t == "reflect":        # 反伤 → 怪物反弹
            mod["reflect"] += v
        elif t == "debuff_heal":    # 禁疗 → 玩家回血减半
            mod["heal_mult"] *= 1.0 - v
        elif t == "debuff_atk":     # 吸攻 → 玩家伤害降低（等效怪防提升）
            mod["def_mult"] *= 1.0 + v
        elif t == "pierce":         # 破甲 → 怪物无视部分防御（等效攻击提升）
            mod["atk_mult"] *= 1.0 + v

    # 玩家侧三选一（temp_effects 是 loot 的 effect 类条目）
    for loot in temp_effects or []:
        t = td.LOOT_EFFECT_T.get(loot.get("key"))
        v = float(loot.get("v", 0))
        if t == "crit":
            mod["crit_add"] += v * 100
        elif t in ("chain", "pierce", "lifesteal"):
            mod["self_effects"].append({"t": t, "v": v, "d": 99, "kind": "塔增益"})
    return mod


_TOWER_REF_CACHE: dict[int, dict] = {}


def tower_ref_player(ref_lv: int) -> dict:
    """爬塔「标尺玩家」：只由层号决定的纸面角色，用来反解该层怪物的数值。

    2026-11-19 用户「爬塔每层难度固定，不按玩家战力来」。以前塔怪按**真实玩家**
    面板反解，玩家一升级同层怪就同步变厚，爬塔等于原地踏步。现在把反解的对象
    换成这只「标尺」：同样走 ``create_player`` 的完整初始流程（宗门/斗技/异火），
    保证数值曲线与既有战斗平衡同一口径，但**不再读真实玩家的任何字段**。

    缓存按等级，返回深拷贝：``stats()``/``ensure_loadout()`` 都会写对象，
    共享同一份会互相污染（第 20 层打完，第 21 层的标尺就带着脏状态）。

    ⚠️ 固定 qq ``tower-ref``：宗门/异火按 qq 定种子，换 qq 会让不同层的标尺
    落到不同宗门，难度曲线出现不连续台阶。
    """
    ref_lv = max(1, min(int(cfg.MAX_LV), int(ref_lv)))
    base = _TOWER_REF_CACHE.get(ref_lv)
    if base is None:
        base = create_player("tower-ref", "塔之标尺", "")
        base["lv"] = ref_lv
        base["hp"] = stats(base)["体力"]
        base["mp"] = mp_max(base)
        _TOWER_REF_CACHE[ref_lv] = base
    return copy.deepcopy(base)


def tower_floor(p, st: dict, rng=random.random, temp_bonus=None,
                temp_effects=None) -> dict:
    """打塔里的一层（第 st['floor']+1 层）。

    :param p        玩家（只读；战斗在深副本上进行）
    :param st       会话状态 dict（tower_session.new_state）
    :param rng      确定性随机（按会话种子）
    :return {win, floor, kind, name, affixes, rounds, taken, hp, mp, log, ...}
            其中 hp/mp 是**战斗后会话应保留的值**，由调用方写回 st，不动 p。

    难度（2026-11-19 改造）：怪物数值由 ``td.floor_ref_lv(floor)`` 的标尺玩家
    反解 —— **同层永远同一只怪**，与打它的玩家是谁、多少战力无关。玩家变强只
    体现在「能爬更高」，而不是「同一层越来越难」。
    """
    floor = int(st.get("floor", 0)) + 1
    kind = td.floor_kind(floor)
    affixes = td.affixes_for(floor)   # 不传 rng：词缀按层号固定，同层可复现
    name = td.boss_name(floor) if kind == "boss" else (
        "精英·" + rng_choice(cfg.MONSTERS, rng)["name"] if kind == "elite"
        else rng_choice(cfg.MONSTERS, rng)["name"])

    # 玩家深副本：并入本轮临时属性增益
    fp = copy.deepcopy(p)
    bonus = dict(fp.get("bonus") or {})
    for k, v in (temp_bonus or {}).items():
        bonus[k] = int(bonus.get(k, 0)) + int(v)
    fp["bonus"] = bonus
    # 气血/斗气来自会话临时态（首层用满值）
    st_max = stats(fp)
    fp["hp"] = int(st.get("temp_hp") if st.get("temp_hp") is not None else st_max["体力"])
    fp["mp"] = int(st.get("temp_mp") if st.get("temp_mp") is not None else mp_max(fp))
    hp_before = int(fp["hp"])       # 进场血量（层间回血的「损失」基准）

    # 虚拟 species：只给 hard/lv/倍率，数值由 monster_scaled 反解（反解对象是标尺）
    ref_lv = td.floor_ref_lv(floor)
    species = {
        "name": name,
        "lv": ref_lv,
        "hard": td.floor_hard(floor),
        "exp_mul": 1.0, "gold_mul": 1.0,
        "kind": "塔·" + kind,
    }
    mon = monster_scaled(tower_ref_player(ref_lv), species)
    mod = tower_mod_from_affixes(affixes, temp_effects)
    # 武器专属效果注入（2026-11-17 用户"武器影响斗技效果"）：当前装备武器的
    # special 按 DIM_MAP 映射到 tower_mod。仅玄重尺默认时同样生效，
    # 让"换武器真的改变战斗"，而不是只有面板数字变化。
    eq = equipped_weapon(p)
    sp = (wd.WEAPONS.get(eq) or {}).get("special") or {}
    wdim, wv = sp.get("dim"), float(sp.get("v", 0))
    if wdim == "damage":
        mod["self_effects"].append({"t": "empower", "v": wv, "d": 99, "kind": eq + "·" + sp.get("scope", "")})
    elif wdim == "hits":
        mod["self_effects"].append({"t": "chain", "v": wv, "d": 99, "kind": eq})
    elif wdim == "evade":
        mod["evade_add"] += wv * 100
    elif wdim == "defense":
        mod["heal_mult"] *= (1.0 + wv)   # 减伤用等效回血近似（不改内核减伤口径）
    elif wdim == "speed":
        mod["crit_add"] += wv * 50       # 敏捷→暴击的一半（映射口径见 weapon_data）
    # Boss/精英层提高攻击基数（在 TOWER_ATK_SCALE 之上），让机制层真正更难：
    # Boss 1.6×、精英 1.25×、普通 1.0×。难度的"血"维度已由 hard 承担。
    atk_tier = {"boss": 1.6, "elite": 1.25, "normal": 1.0}[kind]
    # 随层数持续叠加的难度（无限层的终点），叠加在词缀倍率之上。
    diff = td.floor_difficulty(floor)
    mod["atk_mult"] = float(mod.get("atk_mult", 1.0)) * atk_tier * diff["atk_mult"]
    mod["hp_mult"] = float(mod.get("hp_mult", 1.0)) * diff["hp_mult"]
    # 深副本战斗不自动嗑药（fp 是 deepcopy，真实背包不会减少；
    # 打了「服药」日志却不扣药 = 文案与数据两张皮）。塔/周本有各自的三选一补给。
    r = fight(fp, mon, rng=rng, tower_mod=mod, auto_pill=False)

    # 层间回血：胜利后回一口，写进返回 hp（由调用方写回会话），避免连爬必死。
    # 战斗副本 fp 的 hp 已被 fight 写为剩余；这里只在胜利时加成。
    # 2026-11-17 用户反馈「气血怎么没降低」：只按上限比例回血会把单层伤害全部盖掉
    # （lv60 连爬 8 层满血）。改为双重约束——最多 FLOOR_REST_HEAL 比例、且最多补回
    # 本层实际损失的一半，保证跨层气血单调不增；要续航请选三选一的回血战利品。
    hp_after = int(fp.get("hp", 0))
    dmg_taken = max(0, hp_before - hp_after)
    rest = 0
    if r["win"]:
        cap = int(st_max["体力"])
        want = int(cap * float(td.FLOOR_REST_HEAL))
        want = min(want, int(dmg_taken * float(td.FLOOR_REST_DMG_RATIO)))
        # 只报「实际回了多少」：满血时 rest=0，战报不该出现「歇息 +12040」的假数字。
        rest = max(0, min(cap, hp_after + want) - hp_after)
        hp_after += rest

    return {
        "win": bool(r["win"]), "floor": floor, "kind": kind, "name": name,
        "affixes": affixes, "rounds": int(r["rounds"]), "taken": int(r["taken"]),
        "hp": hp_after, "mp": int(fp.get("mp", 0)), "rest_heal": rest,
        "log": r["log"], "mon": mon, "hero_aid": r.get("hero_aid"),
    }


def tower_open_chest(p, st: dict, rng=random.random, floor: int | None = None) -> list[dict]:
    """本层开宝箱：判定宝箱 → 判定武器 → 入背包/持有表。返回掉落记录。

    ``floor`` 必须是**刚打完的这一层**。不能默认读 ``st['floor']``：
    胜利后调用方已把通过层数推进到本层，若再读 st 会按下一层判定（曾经错位一层，
    导致第 10 层 Boss 宝箱按第 11 层算、武器解锁层提前）。
    """
    floor = int(floor if floor is not None else st.get("floor", 0))
    # 电梯重打旧层不再出宝箱（2026-11-17 用户「不重复获得之前挑战过层数的奖励」）：
    # 已结算水位线 <= floor 的层，其宝箱（材料/武器）在首轮已经领过。
    if floor <= max(0, int(p.get("tower_paid_floor", 0))):
        return []
    if rng() >= td.chest_rate(floor):
        return []
    out = []
    owned = set(owned_weapons(p))
    pool = td.weapon_pool_for(floor, owned)
    if pool and rng() < td.WEAPON_DROP_IN_CHEST:
        w = rng_choice(pool, rng)
        if grant_weapon(p, w):
            out.append({"type": "weapon", "name": w})
            return out
    # 否则给材料（炎星石 1~3）
    n = 1 + int(rng() * 3)
    add_item(p, "炎星石", n)
    out.append({"type": "material", "name": "炎星石", "n": n})
    return out


def tower_settle(p, st: dict) -> dict:
    """结算本轮：只发**未领过**的增量层数奖励、更新水位线/止步层/tower_best。仅调用一次。

    2026-11-17 电梯改造：新增 ``tower_paid_floor``（已结算水位线）与
    ``tower_checkpoint``（上次止步层，电梯目标）。奖励按
    ``clear_reward(floors) - clear_reward(paid)`` 增量发——电梯重打旧层不再重复领奖。
    老存档缺字段按 0 处理，首个到达层全额领，自然迁移。
    """
    if st.get("settled"):
        return {"ok": False, "msg": "本轮已结算", "gold": 0, "exp": 0}
    floors = int(st.get("floor", 0))
    paid = max(0, int(p.get("tower_paid_floor", 0)))
    rew = td.incremental_reward(floors, paid)
    p["gold"] = int(p.get("gold", 0)) + rew["gold"]
    p["exp"] = int(p.get("exp", 0)) + rew["exp"]
    new_paid = max(paid, floors)
    p["tower_paid_floor"] = new_paid
    p["tower_checkpoint"] = max(int(p.get("tower_checkpoint", 0)), floors)
    best = int(p.get("tower_best", 0))
    new_best = max(best, floors)
    p["tower_best"] = new_best
    st["settled"] = True
    return {"ok": True, "floors": floors, "gold": rew["gold"], "exp": rew["exp"],
            "best": new_best, "new_record": new_best > best,
            "paid": new_paid}


# ---------------------------------------------------------------------------
# 周副本（2026-11-17）—— 7 层一命通关 / 周一快照难度 / 奖励暂存锁定
#
# 纪律：
#   * 怪物数值用**周一快照画像**反解（monster_scaled(snapshot, species)），玩家用
#     当前真实 p 战斗——一周内升级/换武器后怪不变 → 越来越轻松（用户要的"提升机会"）。
#   * 每层奖励只进会话 staged，通关第 7 层才 commit 到永久存档；失败/超时整段丢弃。
#   * 宝箱武器在 staging 阶段只记名字，通关才 grant_weapon（失败不含武器）。
# ---------------------------------------------------------------------------
def weekly_snapshot(p: dict, week: str) -> dict:
    """取玩家本周难度画像：存档有本周快照就用它，否则写入并返回。

    新入道（存档从无 weekly_snapshot）用固定新手档 weekly_data.ROOKIE_PROFILE，
    并把"新手快照"落进存档本周 key —— 一周内恒定，下周再刷新。
    """
    from . import weekly_data as wd_weekly

    snap = dict(p.get("weekly_snapshot") or {})
    if snap.get("week") == week and isinstance(snap.get("profile"), dict) \
            and snap["profile"]:
        return snap["profile"]
    if not p.get("weekly_snapshot"):
        profile = dict(wd_weekly.ROOKIE_PROFILE)   # 新入道固定档
    else:
        profile = wd_weekly.snapshot_profile(p)    # 老玩家刷新为本周画像
    p["weekly_snapshot"] = {"week": week, "profile": dict(profile)}
    return profile


def weekly_floor(p, snapshot: dict, st: dict, rng=random.random) -> dict:
    """打周副本的一层（第 st['floor']+1 层），不改玩家永久存档。

    怪物由**快照画像**反解（难度锁定），战斗用**当前玩家**深副本（当周变强生效）。
    返回 {win, floor, kind, name, affixes, rounds, hp, mp, log, reward}。
    """
    from . import weekly_data as wd_weekly

    floor = int(st.get("floor", 0)) + 1
    kind = wd_weekly.floor_kind(floor)
    name = wd_weekly.monster_name(floor)
    eq = equipped_weapon(p)
    species = {
        "name": name,
        "lv": wd_weekly.monster_lv(floor, int(snapshot.get("lv", p.get("lv", 1)))),
        "hard": wd_weekly.floor_hard(floor),
        "exp_mul": 1.0, "gold_mul": 1.0,
        "kind": "周本·" + kind,
    }
    # 难度源头：快照画像反解怪血/攻；玩家侧用当前真实 p 的深副本。
    mon = monster_scaled(snapshot, species)
    fp = copy.deepcopy(p)
    fp["hp"] = int(st.get("temp_hp") if st.get("temp_hp") is not None
                   else stats(fp)["体力"])
    fp["mp"] = int(st.get("temp_mp") if st.get("temp_mp") is not None
                   else mp_max(fp))
    hp_before = int(fp["hp"])

    mod = {"hp_mult": 1.0, "atk_mult": float(wd_weekly.WEEKLY_ATK_SCALE),
           "def_mult": 1.0, "evade_add": 0.0, "crit_add": 0.0,
           "heal_mult": 1.0, "reflect": 0.0, "self_effects": []}
    # Boss / 精英：攻击基数上调 + 两/一个词缀（复用 tower_mod_from_affixes 口径）
    mod["atk_mult"] *= wd_weekly.ATK_TIER[kind]
    affixes = []
    if kind == "boss":
        affixes = ["狂暴", "反伤"]
    elif kind == "elite":
        affixes = ["破甲"]
    aff_mod = tower_mod_from_affixes(affixes, [])
    for k in ("hp_mult", "atk_mult", "def_mult", "reflect"):
        mod[k] *= aff_mod.get(k, 1.0) if k != "reflect" else 1.0
    mod["reflect"] += aff_mod.get("reflect", 0.0)
    # 武器专属效果（与无限塔同口径）
    sp = (wd_mod_weapon(eq) or {})
    wdim, wv = sp.get("dim"), float(sp.get("v", 0))
    if wdim == "damage":
        mod["self_effects"].append({"t": "empower", "v": wv, "d": 99, "kind": eq})
    elif wdim == "hits":
        mod["self_effects"].append({"t": "chain", "v": wv, "d": 99, "kind": eq})
    elif wdim == "evade":
        mod["evade_add"] += wv * 100
    elif wdim == "defense":
        mod["heal_mult"] *= (1.0 + wv)
    elif wdim == "speed":
        mod["crit_add"] += wv * 50

    # 深副本战斗不自动嗑药（fp 是 deepcopy，真实背包不会减少；
    # 打了「服药」日志却不扣药 = 文案与数据两张皮）。塔/周本有各自的三选一补给。
    r = fight(fp, mon, rng=rng, tower_mod=mod, auto_pill=False)
    hp_after = int(fp.get("hp", 0))
    rest = 0
    if r["win"]:
        cap = int(stats(fp)["体力"])
        want = min(int(cap * 0.10), int(max(0, hp_before - hp_after) * 0.5))
        rest = max(0, min(cap, hp_after + want) - hp_after)
        hp_after += rest
    return {
        "win": bool(r["win"]), "floor": floor, "kind": kind, "name": name,
        "affixes": affixes, "rounds": int(r["rounds"]), "taken": int(r["taken"]),
        "hp": hp_after, "mp": int(fp.get("mp", 0)), "rest_heal": rest,
        "log": r["log"], "mon": mon, "hero_aid": r.get("hero_aid"),
        "reward": wd_weekly.floor_reward(floor),
    }


def wd_mod_weapon(name: str) -> dict:
    """取武器 special（与无限塔同源，抽成小函数便于单测）。"""
    return (wd.WEAPONS.get(name) or {}).get("special") or {}


def weekly_stage(st: dict, floor_result: dict) -> dict:
    """把一层的战斗奖励入**暂存区**（不写永久存档）；宝箱武器只记名字。"""
    staged = st.setdefault("staged", {"gold": 0, "exp": 0, "items": {}, "weapons": []})
    rew = floor_result.get("reward") or {}
    staged["gold"] = int(staged.get("gold", 0)) + int(rew.get("gold", 0))
    staged["exp"] = int(staged.get("exp", 0)) + int(rew.get("exp", 0))
    return staged


def weekly_open_chest(p, snapshot: dict, st: dict, floor: int,
                      rng=random.random) -> list[dict]:
    """周本开宝箱：判定掉落 → 只入会话暂存区，绝不直接写玩家（失败要能全清）。"""
    from . import tower_data as td_weekly

    if rng() >= td_weekly.chest_rate(floor):
        return []
    out = []
    owned = set(owned_weapons(p)) | set((st.get("staged") or {}).get("weapons") or [])
    pool = td_weekly.weapon_pool_for(floor, owned)
    if pool and rng() < td_weekly.WEAPON_DROP_IN_CHEST:
        w = rng_choice(pool, rng)
        out.append({"type": "weapon", "name": w})
    else:
        out.append({"type": "material", "name": "炎星石", "n": 1 + int(rng() * 3)})
    weekly_stage_loot(st, out)
    return out


def weekly_stage_loot(st: dict, loot: list[dict]) -> None:
    """宝箱掉落入暂存区：武器只记名字，材料记数量；通关才真正入包/持枪。"""
    staged = st.setdefault("staged", {"gold": 0, "exp": 0, "items": {}, "weapons": []})
    items = staged.setdefault("items", {})
    weapons = staged.setdefault("weapons", [])
    for c in loot or []:
        if c.get("type") == "weapon" and c.get("name") not in weapons:
            weapons.append(c["name"])
        elif c.get("type") == "material":
            items[c["name"]] = int(items.get(c["name"], 0)) + int(c.get("n", 1))


def weekly_commit(p, st: dict) -> dict:
    """通关第 7 层：把暂存区一次性结算进永久存档。失败路径**绝不调用**。"""
    staged = st.get("staged") or {}
    gold = int(staged.get("gold", 0))
    exp = int(staged.get("exp", 0))
    p["gold"] = int(p.get("gold", 0)) + gold
    p["exp"] = int(p.get("exp", 0)) + exp
    for name, n in (staged.get("items") or {}).items():
        add_item(p, name, int(n))
    granted = []
    for name in (staged.get("weapons") or []):
        if grant_weapon(p, name):
            granted.append(name)
    st["cleared"] = True
    return {"ok": True, "gold": gold, "exp": exp,
            "items": dict(staged.get("items") or {}), "weapons": granted}


def duel(p, vic, rng=random.random) -> dict:
    """掠夺：双方各用自己四槽随机互殴（p 先手）。斗气不足转武器普攻。

    只算胜负与战报，不改双方 hp/gold（结算由 commands.cmd_rob 负责），
    与 `fight` 共用四槽 / 按阶斗气 / 普攻兜底逻辑。
    """
    sp, sv = stats(p), stats(vic)
    # 根基裁定（隐形）：双方各自的隐藏系数，防御侧按本人系数打折；输出侧在
    # _hit 内按攻击方系数打折。PVP 双方对称生效，root=1 时逐字节不变。
    root_p, root_v = cfg.root_factor(p), cfg.root_factor(vic)
    hp_p, hp_v = int(sp["体力"]), int(sv["体力"])
    mp_p, mp_v = mp_max(p), mp_max(vic)
    log, rounds = [], 0
    # 2026-11-15 多维效果：p 的增益在 p_fx、挂给 vic 的减益在 v_fx，反之亦然
    p_fx, v_fx = fx.new_state(), fx.new_state()
    v_ctx = {"target_flags": set(), "target_hp_ratio": 1.0}
    p_ctx = {"target_flags": set(), "target_hp_ratio": 1.0}
    hp_p0, hp_v0 = max(1, hp_p), max(1, hp_v)

    def _mark(entry: dict, actor: str, hp: int, mp: int, foe_hp: int | None = None) -> dict:
        """PVP 逐条快照：与 fight() 同字段，回看卡可共用一套渲染。"""
        entry["who"] = actor
        entry["round"] = max(1, rounds)
        entry["foe"] = "对手"
        entry["hp_after"] = int(hp)
        entry["mp_after"] = int(mp)
        if foe_hp is not None:
            entry["foe_hp_after"] = max(0, int(foe_hp))
        return entry

    # 2026-11-19「不要控制回合」：PVP 同样去掉 15 回合硬上限。
    # 双方每回合至少 1 点伤害，数学上必分胜负；HARD_ROUND_CAP 只作脏数据熔断，
    # 触发时按剩余血量比例判定（与 fight 同口径）。
    while rounds < int(cfg.HARD_ROUND_CAP) and hp_p > 0 and hp_v > 0:
        rounds += 1
        # p 出手
        hp_before, mp_before, foe_hp_before = hp_p, mp_p, hp_v
        v_ctx["target_hp_ratio"] = hp_v / hp_v0
        e, c = _pick_skill(p, mp_p, rng)
        if int(p_fx.get("silence", 0)) > 0:   # 禁技：只能普攻
            e, c = None, 0
        hit = _hit(p, int(sv["防御"] * root_v), e, rng, p_fx, v_fx, v_ctx)
        if e is not None:
            mp_p -= c
        hp_v -= hit["dmg"]
        hit.update({"hp_before": int(hp_before), "mp_before": int(mp_before),
                    "mp_cost": int(c if e is not None else 0),
                    "foe_hp_before": max(0, int(foe_hp_before))})
        log.append(_mark(hit, "p", hp_p, mp_p, hp_v))
        if hit.get("heal_ratio"):
            # 减疗（2026-11-19 修）：vic 挂到 p_fx 的 debuff_heal 压低 p 回血
            hp_p = min(hp_p0, hp_p + int(hit["dmg"] * hit["heal_ratio"] * fx.heal_mult(p_fx)))
            log[-1]["hp_after"] = int(hp_p)
        if hp_v <= 0:
            break
        # vic 行动前：DoT + 控制
        vs = fx.on_turn_start(v_fx, rng)
        if vs["dot_dmg"]:
            hp_v -= vs["dot_dmg"]
            log.append(_mark({"dmg": vs["dot_dmg"], "kind": "持续伤害", "dot": True,
                              "notes": vs["notes"], "move": "持续伤害",
                              "hp_before": int(hp_v + vs["dot_dmg"]),
                              "mp_before": int(mp_v), "foe_hp_before": int(hp_v + vs["dot_dmg"])},
                             "v", hp_v, mp_v, hp_v))
        if hp_v <= 0:
            break
        if not vs["skip"]:
            # vic 出手
            hp_before_v, mp_before_v, foe_hp_before_v = hp_v, mp_v, hp_p
            p_ctx["target_hp_ratio"] = hp_p / hp_p0
            e2, c2 = _pick_skill(vic, mp_v, rng)
            if int(v_fx.get("silence", 0)) > 0:   # 禁技：只能普攻
                e2, c2 = None, 0
            hit2 = _hit(vic, int(sp["防御"] * root_p), e2, rng, v_fx, p_fx, p_ctx)
            if e2 is not None:
                mp_v -= c2
            inc = fx.on_incoming(p_fx, hit2["dmg"], rng)
            hp_p -= inc["final"]
            if inc["notes"]:
                hit2["notes"] = (hit2.get("notes") or []) + inc["notes"]
            hit2.update({"hp_before": int(hp_before_v), "mp_before": int(mp_before_v),
                         "mp_cost": int(c2 if e2 is not None else 0),
                         "foe_hp_before": max(0, int(foe_hp_before_v))})
            log.append(_mark(hit2, "v", hp_v, mp_v, hp_p))
            if hit2.get("heal_ratio"):
                # 减疗（2026-11-19 修）：p 挂到 v_fx 的 debuff_heal 压低 vic 回血
                hp_v = min(hp_v0, hp_v + int(inc["final"] * hit2["heal_ratio"] * fx.heal_mult(v_fx)))
                log[-1]["hp_after"] = int(hp_v)
            if inc["reflect"]:
                hp_v -= inc["reflect"]
                log.append(_mark({"dmg": inc["reflect"], "kind": "反伤", "reflect": True,
                                  "move": "反伤"}, "p", hp_v, mp_p, hp_p))
        else:
            log.append(_mark({"dmg": 0, "kind": "被控", "miss": True,
                              "move": "被控"}, "v", hp_v, mp_v, hp_p))
        if hp_p <= 0:
            break
        fx.expire_turn(p_fx)
        fx.expire_turn(v_fx)
    win = hp_v <= 0 and hp_p > 0
    if rounds >= int(cfg.HARD_ROUND_CAP) and hp_v > 0 and hp_p > 0:
        # 异常熔断（不是玩法回合上限）：按剩余血量比例判胜，绝不静默判负。
        win = (hp_p / hp_p0) > (hp_v / hp_v0)
    return {"win": win, "rounds": rounds, "log": log,
            "hp_p": max(0, hp_p), "hp_v": max(0, hp_v),
            "exhausted": rounds >= int(cfg.HARD_ROUND_CAP)}


def _roll_drops(p, rng, mon=None) -> list:
    """[源码 h.java:57-78] 千分率判定 + 基础值到 2 倍随机。

    2026-11-18 天材地宝分级掉落（用户「天才地宝也按怪物等级分配，还有 boos」）：
      * 原有的炎星石/普通药材判定放在**最前**，rng 调用顺序与数量对老路径零改变；
        ``mon=None`` 时函数在普通药材后立刻 return，行为与改动前逐字节一致。
      * 传入 mon 后，按 ``mon['lv']`` 选可掉 tier（lv 越高池越深），逐档独立千分率判定；
        ``mon['is_boss']`` 为真时**额外必掉一件 tier4 专属天材地宝**。
      * 每件天材地宝自带 effect/value，服下即生效（见 use_item），不是背包摆设。
    """
    out = []
    if int(rng() * 1000) < cfg.DROP_RATE_STONE:
        n = 1 + int(rng() * 2)
        add_item(p, "炎星石", n)
        out.append(("炎星石", n))
    if int(rng() * 1000) < cfg.DROP_RATE_HERB:
        h = rng_choice(cfg.HERBS, rng)
        n = 1 + int(rng() * 2)
        add_item(p, h["name"], n)
        out.append((h["name"], n))
    if mon is None:
        return out

    lv = int(mon.get("lv") or 0)
    is_boss = bool(mon.get("is_boss"))
    max_tier = 4 if is_boss else max(
        [t for t, minlv in cfg.MATERIAL_TIER_LV.items()
         if t < 4 and lv >= minlv], default=1)
    for tier in range(1, max_tier + 1):
        # tier4 专属：非 Boss 永不进入循环；Boss 也走独立千分率，不是 100% 但明显偏高。
        if int(rng() * 1000) >= cfg.MATERIAL_DROP_RATE[tier]:
            continue
        pool = [m for m in cfg.NOVEL_MATERIALS if m["tier"] == tier]
        if not pool:
            continue
        m = rng_choice(pool, rng)
        n = 1 + int(rng() * 2) if tier <= 2 else 1   # 高阶天材地宝只掉 1 件
        add_item(p, m["name"], n)
        out.append((m["name"], n))

    # 2026-11-19 用户「任何战斗掉落增加补血补斗气丹药」：
    # 放在**所有既有判定之后**，rng 调用只追加不插队 —— 老路径的掉落序列逐字节不变。
    # 精英/Boss 必掉气血+斗气各一件（硬仗本来就该喝药）；普通战斗按 HEAL_DROP_RATE
    # 掉一件、气血斗气二选一。丹名取既有配方（回春散/回气丹），效果靠配方 pct 跟随上限。
    if is_boss or mon_tier(mon.get("kind")) == "elite":
        for _nm in (cfg.HEAL_PILL_HP, cfg.HEAL_PILL_MP):
            add_item(p, _nm, 1)
            out.append((_nm, 1))
    elif rng() < float(cfg.HEAL_DROP_RATE):
        _nm = cfg.HEAL_PILL_HP if rng() < 0.5 else cfg.HEAL_PILL_MP
        add_item(p, _nm, 1)
        out.append((_nm, 1))
    return out


# ---------------------------------------------------------------------------
# 背包
# ---------------------------------------------------------------------------
def bag(p) -> dict:
    return p.setdefault("bag", {})


def count_item(p, name) -> int:
    return int(bag(p).get(name, 0))


def add_item(p, name, n=1) -> None:
    b = bag(p)
    b[name] = int(b.get(name, 0)) + int(n)
    if b[name] <= 0:
        b.pop(name, None)


def take_item(p, name, n=1) -> bool:
    if count_item(p, name) < int(n):
        return False
    add_item(p, name, -int(n))
    return True


def bag_slots(p) -> list:
    """按 24 格展示（实机包裹 4×6）。超出部分追加。"""
    items = [(k, v) for k, v in bag(p).items() if v > 0]
    items.sort(key=lambda kv: (-kv[1], kv[0]))
    return items


# ---------------------------------------------------------------------------
# 装备系统（2026-11-17：副本掉落武器 → 持有表 → 切换装备）
# ---------------------------------------------------------------------------
def owned_weapons(p) -> list[str]:
    """玩家持有的武器名。老档无字段时兜底只有玄重尺。"""
    ws = p.get("weapons")
    if not ws:
        ws = ["玄重尺"]
        p["weapons"] = ws
    return list(ws)


def grant_weapon(p, name: str) -> bool:
    """获得一把武器；已有返回 False（不重复发放）。"""
    if name not in wd.WEAPONS:
        return False
    ws = owned_weapons(p)
    if name in ws:
        return False
    ws.append(name)
    p["weapons"] = ws
    return True


def equip_weapon(p, name: str) -> dict:
    """切换当前装备；须已持有。返回 {ok, name, msg}。"""
    if name not in owned_weapons(p):
        return {"ok": False, "msg": "你还没有「%s」，去爬塔开宝箱吧。" % name}
    p["equipped_weapon"] = name
    return {"ok": True, "name": name, "msg": "已装备「%s」。" % name}


# ---------------------------------------------------------------------------
# 强化（玄重尺）  [源码 l.java:361-368 / h.java:80-102]
# ---------------------------------------------------------------------------
def enhance_cost(p) -> int:
    """需要炎星石数量 = 内部 L + 1（实机 L=0 时显示「需要 1 块」）。"""
    return int(p.get("weapon", 0)) + 1


def enhance(p) -> dict:
    w = int(p.get("weapon", 0))
    if w > 8:
        return {"ok": False, "reason": "max"}
    cost = enhance_cost(p)
    if not take_item(p, "炎星石", cost):
        return {"ok": False, "reason": "stone", "need": cost, "have": count_item(p, "炎星石")}
    p["weapon"] = w + 1
    _refill(p)
    # 「异火恒古尺」是玄重尺专属觉醒；装备其它武器时只报等级，不冒这条文案。
    evolved = p["weapon"] > 8 and equipped_weapon(p) == "玄重尺"
    return {
        "ok": True, "internal": p["weapon"],
        "display": cfg.weapon_display_level(p["weapon"]),
        "name": cfg.WEAPON["name_max"] if evolved else equipped_weapon(p),
        "evolved": evolved,
    }


def enhance_preview(p) -> dict:
    """强化框预览：当前 4 词条 -> 下一级 4 词条（实机那个「1级 ↗ 2级」框）。

    2026-11-17：改用 ``_weapon_rows``，换武器后预览的是**当前武器**成长，
    不再永远显示玄重尺（否则强化血斧时预览数字与实际面板不一致）。
    """
    w = int(p.get("weapon", 0))
    cur, nxt = _weapon_rows(p)
    return {"cur": cur, "next": nxt, "cost": w + 1,
            "disp": cfg.weapon_display_level(w),
            "next_disp": cfg.weapon_display_level(w + 1)}


# ---------------------------------------------------------------------------
# 炼药  [实机丹方 + 提示语「失败也得药渣」「炼药师等级影响成功率」]
# ---------------------------------------------------------------------------
def craft_cd_left(p, now_ts=None) -> int:
    now_ts = time.time() if now_ts is None else now_ts
    left = int(cfg.CRAFT_CD_SEC - (now_ts - float(p.get("last_craft", 0))))
    return max(0, left)


def craft(p, name, rng=random.random, now_ts=None) -> dict:
    rec = cfg.RECIPE_BY_NAME.get(name)
    if not rec:
        return {"ok": False, "reason": "nodrug"}
    # 2026-11-16：原著未载药方的丹药没有炼制路径，只能金币购买
    if not rec.get("need") and int(rec.get("buy", 0)) > 0:
        return {"ok": False, "reason": "buyonly", "buy": int(rec["buy"])}
    now_ts = time.time() if now_ts is None else now_ts
    cd = craft_cd_left(p, now_ts)
    if cd > 0:
        return {"ok": False, "reason": "cd", "left": cd}

    missing = []
    for mat, n in rec["need"].items():
        if count_item(p, mat) < n:
            missing.append(f"{mat}×{n}(有{count_item(p, mat)})")
    if missing:
        return {"ok": False, "reason": "mat", "missing": missing}

    for mat, n in rec["need"].items():
        take_item(p, mat, n)
    p["last_craft"] = now_ts

    # 2026-11-18 原著口径：成功率随「炼药师品阶 vs 丹药品阶」变化，不再所有丹药一个值。
    # diff>0 越阶衰减（第495章四品勉强炼五品「成功率太小」），diff<=0 够格用基础率。
    rate, diff = cfg.craft_rate_for(
        p.get("alch", 0), rec.get("grade", ""),
        flame_bonus=_flame_bonus.alchemy_rate_bonus(p))
    if rng() < rate:
        add_item(p, name, 1)
        p["alch_exp"] = int(p.get("alch_exp", 0)) + cfg.CRAFT_EXP
        up = _alch_level_up(p)
        return {"ok": True, "rate": rate, "diff": diff, "alch_up": up}
    add_item(p, "药渣", cfg.CRAFT_FAIL_DROSS)
    p["alch_exp"] = int(p.get("alch_exp", 0)) + cfg.CRAFT_EXP // 2
    up = _alch_level_up(p)
    return {"ok": False, "reason": "fail", "rate": rate, "diff": diff,
            "dross": cfg.CRAFT_FAIL_DROSS, "alch_up": up}


def _alch_level_up(p) -> str:
    """结算炼药师晋阶（经验够则连续升级，返回晋到的品阶名，未升返回空串）。

    2026-11-19 原著门槛：晋入七品及以上须灵魂境界（实战场次）达标，否则经验
    攒着不升——呼应第1103章「很多炼药师一辈子停留在七品，正是灵魂境界缘故」，
    防止刷低级丹绕过灵魂境界冲满级。此闸对单发/批量两种入口统一生效。
    """
    start = int(p.get("alch", cfg.ALCH_START))
    idx = start
    while idx < cfg.ALCH_MAX:
        need = cfg.ALCHEMIST[idx][1]
        if int(p.get("alch_exp", 0)) < need:
            break
        nxt = idx + 1
        ok, _need_battles = cfg.soul_realm_ok(p, nxt)
        if not ok:
            break   # 经验保留，等灵魂境界达标再升
        p["alch_exp"] = int(p["alch_exp"]) - need
        idx = nxt
        p["alch"] = idx
    return cfg.ALCHEMIST[idx][0] if idx != start else ""


def craft_batch(p, name, n, rng=random.random, now_ts=None) -> dict:
    """连续开炉 N 炉（原著「烧药得经验」的批量形态）。

    与单发 ``craft()`` 的关系：**新增独立入口，不改单发语义**（单发被 test_core
    钉死 CD/经验/药渣）。批量口径：
      * 一次过 CD 检查，整批只占一次 CD（打完立即 ``last_craft``）；
      * 每炉独立判定成败、独立扣药材、加丹/药渣，逐炉累加经验；
      * 每炉额外扣斗气（原著第6章「极其消耗斗气」），耗尽则提前收炉；
      * 药材不够则按「实际能炼几炉」收炉（不是直接拒绝整批）；
      * 低阶丹经验按 ``low_grade_exp_factor`` 递减（防刷低级丹冲级）；
      * 整批结束统一结算晋阶（七品以上走灵魂境界闸）。

    返回 dict 含 ``ok`` / ``n_done`` / ``n_ok`` / ``n_fail`` / ``exp`` /
    ``mp_used`` / ``alch_up`` / ``limited`` 等，供命令层组一条回包。
    """
    rec = cfg.RECIPE_BY_NAME.get(name)
    if not rec:
        return {"ok": False, "reason": "nodrug"}
    if not rec.get("need") and int(rec.get("buy", 0)) > 0:
        return {"ok": False, "reason": "buyonly", "buy": int(rec["buy"])}
    now_ts = time.time() if now_ts is None else now_ts
    cd = craft_cd_left(p, now_ts)
    if cd > 0:
        return {"ok": False, "reason": "cd", "left": cd}

    n = max(1, min(int(n), cfg.CRAFT_BATCH_MAX))
    grade_idx = cfg.alch_grade_idx(rec.get("grade", ""))
    rate, diff = cfg.craft_rate_for(
        p.get("alch", 0), rec.get("grade", ""),
        flame_bonus=_flame_bonus.alchemy_rate_bonus(p))
    exp_factor = cfg.low_grade_exp_factor(p.get("alch", 0), grade_idx)
    mp_per = max(1, int(mp_max(p) * cfg.CRAFT_MP_PER))

    n_done = n_ok = n_fail = 0
    exp = 0
    limited = ""
    for _ in range(n):
        # 药材不足 → 按实际可炼炉数收炉，整批其余不结算
        if any(count_item(p, mat) < qty for mat, qty in rec["need"].items()):
            limited = "mat"
            break
        # 斗气不足 → 提前收炉（原著：炼药极耗斗气）
        if int(p.get("mp", 0)) < mp_per:
            limited = "mp"
            break
        for mat, qty in rec["need"].items():
            take_item(p, mat, qty)
        p["mp"] = int(p.get("mp", 0)) - mp_per
        n_done += 1
        if rng() < rate:
            add_item(p, name, 1)
            n_ok += 1
            exp += int(cfg.CRAFT_EXP * exp_factor)
        else:
            add_item(p, "药渣", cfg.CRAFT_FAIL_DROSS)
            n_fail += 1
            exp += int(cfg.CRAFT_EXP // 2 * exp_factor)

    if n_done == 0:
        return {"ok": False, "reason": limited or "mat", "rate": rate,
                "diff": diff, "n_done": 0}

    p["alch_exp"] = int(p.get("alch_exp", 0)) + exp
    alch_up = _alch_level_up(p)
    p["last_craft"] = now_ts
    return {"ok": True, "name": name, "rate": rate, "diff": diff,
            "n": n, "n_done": n_done, "n_ok": n_ok, "n_fail": n_fail,
            "exp": exp, "exp_factor": exp_factor, "mp_used": mp_per * n_done,
            "mp_left": int(p.get("mp", 0)), "dross": cfg.CRAFT_FAIL_DROSS * n_fail,
            "alch_up": alch_up, "alch": int(p.get("alch", 0)),
            "alch_exp": int(p.get("alch_exp", 0)), "limited": limited}


def _advance_one(p) -> int:
    """硬推一级（丹药破境专用）：不查修为、不掷斗帝骰，回满气血斗气。

    与 ``level_up`` 的区别：``level_up`` 是「够了修为才能升」，这里是**丹药顶替
    瓶颈**（原著三纹青灵丹就是干这个的）。所以只做三件事：lv+1、钳上限、回满。
    """
    lv = int(p.get("lv", 1))
    if lv >= cfg.MAX_LV:
        return lv
    p["lv"] = lv + 1
    _refill(p)
    return p["lv"]


def use_item(p, name, used_counts=None, rng=random.random) -> dict:
    """服用丹药 / 炼化天材地宝（2026-11-18 材料不再是背包摆设）。

    解析优先级：先按丹药配方，未命中再按天材地宝表。两者的 effect/value 走同一
    结算分支（hp/mp/exp/luck/dodge/crit/stat/skill/breakthrough），只对 ``msg``
    前缀做区分——既有丹药的 reason/msg 文案逐字不变，既有测试与安全网关不受影响。

    ``used_counts``（2026-11-18 破境丹）：``{境界下标: 已服次数}``，由命令层从
    ``pill_use`` 表读出后传入，用来判「一阶限 N 枚」（原著抗性）。默认 None = 不做
    次数限制（既有单测/脚本调用行为逐字节不变）。

    ``rng``：升星判定用的随机源，默认 ``random.random``，便于单测钉死概率。
    """
    rec = cfg.RECIPE_BY_NAME.get(name)
    is_material = False
    if not rec:
        rec = cfg.MATERIAL_BY_NAME.get(name)
        is_material = bool(rec)
    if not rec:
        return {"ok": False, "reason": "nodrug"}
    if not take_item(p, name, 1):
        return {"ok": False, "reason": "none"}

    st = stats(p)
    eff, val = rec["effect"], int(rec["value"])
    lead = "炼化【%s】，" % name if is_material else ""
    if eff == "hp":
        before = int(p.get("hp", 0))
        # ``pct``（2026-11-19）：疗伤药按「固定值 / 上限百分比」取高。
        # 低境界时上限 12% 还不到 200，行为与改造前逐字节一致；高境界时
        # 固定值已经无感，靠比例跟上（否则掉落等于白捡）。
        gain = max(val, int(st["体力"] * float(rec.get("pct") or 0)))
        p["hp"] = min(st["体力"], before + gain)
        return {"ok": True, "msg": lead + f"气血 +{p['hp'] - before}（{p['hp']}/{st['体力']}）"}
    if eff == "mp":
        mx = mp_max(p)
        before = int(p.get("mp", 0))
        gain = max(val, int(mx * float(rec.get("pct") or 0)))
        p["mp"] = min(mx, before + gain)
        return {"ok": True, "msg": lead + f"斗气 +{p['mp'] - before}（{p['mp']}/{mx}）"}
    if eff == "exp":
        p["exp"] = int(p.get("exp", 0)) + val
        # 2026-11-18 根基裁定：记账「丹药来源修为」，供战斗隐藏系数 root_factor 用。
        # 这是全项目唯一的「服药→修为」入口，苦修/战斗/任务修为都不走这里，
        # 天然区分「外力速成」与「扎实修炼」；老档无此字段视为 0（无惩罚）。
        p["pill_exp"] = int(p.get("pill_exp", 0)) + val
        return {"ok": True, "msg": lead + f"修为 +{val}"}
    if eff == "breakthrough":
        # 原著第 315 / 321 章两枚破境丹，共用同一套结算（口径唯一，不写两份）：
        #   三纹青灵丹：break_boundary=True、star_chance=0.30、max_per_realm=1
        #     「助斗师强者突破晋入大斗师的界限；大斗师服下有少许几率提升一星，
        #      并无药效反噬」「在一阶之中唯有一次服用机会，再服则因药体抗性白费」
        #   紫心破障丹：break_boundary=False、star_chance=1.0、max_per_realm=2
        #     「只能作用于大斗师级别……能够提升一星的实力……同一级别能够接连的
        #      服用两枚而不至于产生太大的抗性……稳步的提升两星实力」
        # 三档分支，任何一档都不空手：
        #   ① 卡在界限前一级（斗师九星）且该丹有破界之能 → 丹力顶开壁障，晋入；
        #   ② 已在目标阶 → 按 max_per_realm 限次，掷 star_chance 升一星；
        #   ③ 其他境界     → 药不对症，按 value 转修为（不白白浪费）。
        to_realm = int(rec.get("to_realm") or 1)
        max_per = max(1, int(rec.get("max_per_realm") or 1))
        lv = int(p.get("lv", 1))
        ri = cfg.realm_index(lv)
        realm_name = cfg.REALM_NAMES[min(to_realm, len(cfg.REALM_NAMES) - 1)]
        if rec.get("break_boundary") and ri == to_realm - 1 and lv < cfg.MAX_LV \
                and cfg.realm_index(lv + 1) == to_realm:
            _advance_one(p)
            return {"ok": True, "breakthrough": True,
                    "msg": (lead + "丹力冲关，一举踏破【%s】壁障——晋入【%s】，气血斗气已满"
                            % (realm_name, cfg.full_realm(int(p["lv"]))))}
        if ri == to_realm:
            used = int((used_counts or {}).get(ri, 0))
            if used_counts is not None and used >= max_per:
                # 丹药已被扣掉（take_item 在最前面），如实告知「白服」。
                return {"ok": False, "reason": "resisted",
                        "msg": ("药体已生抗性，同阶再服只是白费——【%s】这一阶"
                                "至多服 %d 枚见效，晋阶后可再服"
                                % (realm_name, max_per))}
            if rng() < float(rec.get("star_chance", 0.0)):
                _advance_one(p)
                return {"ok": True, "star_up": True, "mark_used": ri,
                        "msg": (lead + "药力化开，硬生生拔起一星——晋入【%s】"
                                % cfg.full_realm(int(p["lv"])))}
            p["exp"] = int(p.get("exp", 0)) + val
            p["pill_exp"] = int(p.get("pill_exp", 0)) + val
            return {"ok": True, "mark_used": ri,
                    "msg": (lead + "药力打了个转，星位未动（无药效反噬），"
                            "余力化修为 +%d" % val)}
        p["exp"] = int(p.get("exp", 0)) + val
        p["pill_exp"] = int(p.get("pill_exp", 0)) + val
        return {"ok": True, "msg": lead + "此丹专为破【%s】而炼，"
                "当前境界药力不显，化修为 +%d" % (realm_name, val)}
    if eff == "luck":
        p["luck"] = min(9999, int(p.get("luck", 0)) + val)
        return {"ok": True, "msg": lead + f"机缘 +{val}（现 {p['luck']}）"}
    if eff == "dodge":
        b = p.setdefault("bonus", {})
        b["闪避"] = int(b.get("闪避", 0)) + val
        return {"ok": True, "msg": lead + f"永久闪避 +{val}%"}
    if eff == "crit":
        b = p.setdefault("bonus", {})
        b["暴击"] = int(b.get("暴击", 0)) + val
        return {"ok": True, "msg": lead + f"永久暴击 +{val}%"}
    if eff == "hit":
        # 2026-11-20 命中（与风行丹的永久闪避成对）：落到 bonus["命中"]，
        # 由 hit_rate() 唯一读取 —— 卡面/战斗/人物卡不会再各算一套。
        b = p.setdefault("bonus", {})
        b["命中"] = int(b.get("命中", 0)) + val
        return {"ok": True, "msg": lead + f"永久命中 +{val}%"}
    if eff == "stat":
        b = p.setdefault("bonus", {})
        for k in ("力量", "敏捷", "斗气", "防御", "体力"):
            b[k] = int(b.get(k, 0)) + val
        _refill(p)
        return {"ok": True, "msg": lead + f"全属性 +{val}"}
    if eff == "skill":
        return {"ok": False, "reason": "notuse",
                "msg": "焚血散不是口服之物，请在「升级 技能名」时使用"}
    return {"ok": False, "reason": "unknown"}


# ---------------------------------------------------------------------------
# 斗技升级
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# 领悟斗技（2026-11-16 用户：「入道给一个低阶技，后面逐一学习，达到等级和战力后随机领悟」）
# ---------------------------------------------------------------------------
def insight_cost(learned_count: int) -> int:
    """第 N+1 门领悟的金币花费：前三门便宜，之后线性递增。"""
    return 200 + 150 * max(0, int(learned_count))


def insight_power_need(learned_count: int) -> int:
    """领悟门槛战力：随已领悟门数递增，防止低战力空手套高级技。"""
    return 120 * max(0, int(learned_count))


def migrate_legacy_learned(p) -> list[str]:
    """老档（无 learned 字段）惰性迁移：用四槽里真实在用的技初始化 learned。

    2026-11-16 用户诉求：老玩家发「学斗技」不该被「存档老版本、斗技已全会」
    挡住——他其实只想继续学。老档历史上「全会」是兼容口径，但会让新玩法
    （入道给 1 门、其余逐一领悟）对老档完全关闭。

    迁移只认玩家**当前四槽里真实带的技**（loadout），不凭空给他全宗技能：
      * 保留进度：loadout 里的技原样进 learned，不削弱；
      * 开放后续：learned 变成 list 后 insight()/learn_named() 正常走候选；
      * 惰性、单玩家、经 store.put_player 持久化，绝不批量改生产库。
    """
    if isinstance(p.get("learned"), list):
        return list(p["learned"])
    names = {e["name"] for e in sk_mod.learnable(p)}
    keep = [str(n) for n in (p.get("loadout") or []) if str(n) in names]
    migrated = list(dict.fromkeys(keep))
    # 返回拷贝：调用方随后 append 新领悟的技时，不能让「迁移了几门」的
    # 提示数字被一起撑大（否则 4 门老档会说成 5 门）。
    p["learned"] = list(migrated)
    return migrated


def insight(p, rng=random.random) -> dict:
    """随机领悟一门「已达解锁等级、尚未领悟」的本宗/通用斗技。

    条件（用户 2026-11-16 定）：达到等级（候选已按 unlock 过滤）和战力门槛 + 金币足够；
    成功则从候选里随机取一门加入 p["learned"]。异火主动技「收服即会」不进领悟池。
    老档无 learned 字段：惰性迁移（见 migrate_legacy_learned）后照常领悟，不再拒绝。
    """
    migrated: list[str] | None = None
    if not isinstance(p.get("learned"), list):
        migrated = migrate_legacy_learned(p)
    learned = list(p["learned"])
    owned = set(learned) | set(sk_mod.learned_names(p))
    # 候选 = 已达解锁等级、未领悟、且非异火技
    cand = [e for e in sk_mod.learnable(p)
            if e["name"] not in owned and e.get("origin") != "异火"]
    if not cand:
        cur_lv = int(p.get("lv", 1))
        nxt = [e for e in sk_mod.entries_for(p)
               if e["name"] not in owned and e.get("origin") != "异火"]
        if nxt:
            need = min(int(e["unlock"]) for e in nxt)
            return {"ok": False, "reason": "level", "need_lv": need, "lv": cur_lv,
                    "migrated": migrated}
        return {"ok": False, "reason": "all", "migrated": migrated}

    need_power = insight_power_need(len(learned))
    if power(p) < need_power:
        return {"ok": False, "reason": "power", "need": need_power, "have": power(p),
                "migrated": migrated}
    cost = insight_cost(len(learned))
    if int(p.get("gold", 0)) < cost:
        return {"ok": False, "reason": "gold", "need": cost, "have": int(p.get("gold", 0)),
                "migrated": migrated}

    pick = cand[int(rng() * len(cand)) % len(cand)]
    p["gold"] = int(p.get("gold", 0)) - cost
    p.setdefault("learned", []).append(pick["name"])
    return {"ok": True, "name": pick["name"], "tier": pick["tier"],
            "cost": cost, "count": len(p["learned"]), "migrated": migrated}


def learn_named(p, name: str) -> dict:
    """定向领悟一门**指定的**斗技（用户 2026-11-16「学习 风灵分形剑」诉求）。

    与 insight() 完全同一套门槛口径（解锁等级 / 战力 / 金币 / 已领悟去重），
    区别只是「随机取一门」→「取玩家点名的那一门」。老档同样惰性迁移。

    失败 reason：noskill（本宗没有这门/异火技）、learned（已会）、
    level（等级不够）、power（战力不足）、gold（金币不足）。
    """
    name = str(name or "").strip()
    migrated: list[str] | None = None
    if not isinstance(p.get("learned"), list):
        migrated = migrate_legacy_learned(p)
    learned = list(p["learned"])
    owned = set(learned) | set(sk_mod.learned_names(p))

    # 只在本宗「可领悟池」里找（已解锁；异火技收服即会，不进池）
    pool = [e for e in sk_mod.learnable(p) if e.get("origin") != "异火"]
    hit = next((e for e in pool if e["name"] == name), None)
    if hit is None:
        # 本宗有这门但等级未到 → 明确告诉差几级；完全没有 → noskill
        any_tier = next((e for e in sk_mod.entries_for(p)
                         if e["name"] == name and e.get("origin") != "异火"), None)
        if any_tier:
            return {"ok": False, "reason": "level", "name": name,
                    "need_lv": int(any_tier["unlock"]), "lv": int(p.get("lv", 1)),
                    "migrated": migrated}
        return {"ok": False, "reason": "noskill", "name": name, "migrated": migrated}
    if name in owned:
        return {"ok": False, "reason": "learned", "name": name, "migrated": migrated}

    need_power = insight_power_need(len(learned))
    if power(p) < need_power:
        return {"ok": False, "reason": "power", "name": name,
                "need": need_power, "have": power(p), "migrated": migrated}
    cost = insight_cost(len(learned))
    if int(p.get("gold", 0)) < cost:
        return {"ok": False, "reason": "gold", "name": name,
                "need": cost, "have": int(p.get("gold", 0)), "migrated": migrated}

    p["gold"] = int(p.get("gold", 0)) - cost
    p.setdefault("learned", []).append(name)
    return {"ok": True, "name": name, "tier": hit["tier"],
            "cost": cost, "count": len(p["learned"]), "migrated": migrated}


def skill_up(p, name) -> dict:
    # 新档：只能升级已领悟的技（先「学斗技」领悟才能升）；老档：保持全会可升
    if isinstance(p.get("learned"), list):
        allowed = set(sk_mod.learned_names(p))
    else:
        allowed = {e["name"] for e in skill_pool(p)} | set(cfg.SKILL_NAMES)
    if name not in allowed:
        return {"ok": False, "reason": "noskill"}
    cur = int((p.get("skills") or {}).get(name, 1))
    mx = int(cfg.SKILL_BY_NAME.get(name, {}).get("max", 5))
    if cur >= mx:
        return {"ok": False, "reason": "max", "lv": cur}
    cost = cfg.skill_up_cost(cur)
    if not take_item(p, cfg.SKILL_UP_ITEM, cost):
        return {"ok": False, "reason": "mat", "need": cost,
                "item": cfg.SKILL_UP_ITEM, "have": count_item(p, cfg.SKILL_UP_ITEM)}
    p.setdefault("skills", {})[name] = cur + 1
    return {"ok": True, "lv": cur + 1, "max": mx}


# ---------------------------------------------------------------------------
# 异火：本宗所藏发放（2026-11-18 A 方案）
# ---------------------------------------------------------------------------
def faction_flame(fid) -> str:
    """入道 starter 异火名（三层投放后全宗门统一一朵 B 线数值火）。

    旧口径按宗门发青莲/陨落/三千三朵超标火，已收回：青莲=A线隐藏彩蛋、
    陨落/三千=S线大后期。这里返回唯一的 starter（config.STARTER_FLAME），
    供卡面/文案的「本宗异火」提示位沿用（文案待统一，见 AGENTS.md）。
    """
    return str(getattr(cfg, "STARTER_FLAME", "") or "")


def grant_faction_flame(p) -> str:
    """入道 / 转宗时发放 starter 异火（1 级），返回火名或空串。

    只在玩家**一朵异火都没有**时发放：
      * 老档（全 0）首次入道/转宗即补发，不需要额外标记字段；
      * 已有任意异火就不再发 → 天然防「反复转宗刷」；
      * 异火只升不降，故一次生效，不会重复触发。

    三层投放（2026-11-19）：发的是 B 线数值火（无招式特效），S/A 火走各自解锁路径。
    """
    if not isinstance(p, dict):
        return ""
    fl = p.get("flames") or {}
    if any(int(v or 0) > 0 for v in fl.values()):
        return ""
    name = faction_flame(p.get("faction"))
    if not name:
        return ""
    p.setdefault("flames", {})[name] = 1
    return name


def s_flame_gate(p, name: str) -> tuple[bool, str]:
    """S 线异火解锁判定：境界索引 + 主线进度双门槛。

    返回 ``(可解锁, 未满足原因)``。帝炎不在此列（走 diyan 斗帝成就）。
    """
    gate = (getattr(cfg, "S_FLAME_GATE", {}) or {}).get(name)
    if not gate:
        return True, ""
    if int(p.get("lv", 1)) < cfg.realm_lv(int(gate["realm"])):
        return False, "境界未至（需斗尊以上）"
    if int(p.get("main_idx", 0)) < int(gate["main"]):
        return False, "主线剧情未推进到相应章节"
    return True, ""


def unlock_flame(p, name: str) -> dict:
    """统一收服入口：S 线校验门槛，A 线只认支线回调，B 线可直接收服。

    返回 ``{"ok": bool, "name":..., "reason":...}``。
    单一真值仍是 ``p["flames"][name] > 0``，不新增解锁字段。
    """
    if name not in getattr(cfg, "FLAME_TIER", {}):
        return {"ok": False, "reason": "noflame", "name": name}
    fl = p.setdefault("flames", {})
    if int(fl.get(name, 0)) > 0:
        return {"ok": False, "reason": "owned", "name": name}
    tier = cfg.FLAME_TIER[name]
    if tier == "S":
        ok, why = s_flame_gate(p, name)
        if not ok:
            return {"ok": False, "reason": "locked", "name": name, "why": why}
    fl[name] = 1
    return {"ok": True, "name": name, "tier": tier}


# ---------------------------------------------------------------------------
# 异火喂养
# ---------------------------------------------------------------------------
def feed_flame(p, name, amount=None) -> dict:
    # B 线十一火也可喂养（数值火）：与可养五火合并查表，口径同源。
    fl = cfg.FLAME_BY_NAME.get(name) or getattr(cfg, "BONUS_FLAME_BY_NAME", {}).get(name)
    if not fl:
        return {"ok": False, "reason": "noflame"}
    if name == cfg.FLAME_DIYAN:
        return {"ok": False, "reason": "diyan"}
    cur = int((p.get("flames") or {}).get(name, 0))
    # 2026-11-18 A 方案：未收服不得喂养。旧逻辑 0 级也能直接喂到 1 级，
    # 等于绕过了「降服」这一原著硬前提（也曾让傀儡异火闸形同虚设）。
    if cur <= 0:
        return {"ok": False, "reason": "unowned", "name": name}
    if cur >= cfg.FLAME_MAX_LV:
        return {"ok": False, "reason": "max", "lv": cur}
    need = int(fl["need"]) * (cur + 1)
    have = count_item(p, "药渣")
    if have <= 0:
        return {"ok": False, "reason": "nodross"}
    give = have if amount is None else min(int(amount), have)
    give = min(give, need)
    take_item(p, "药渣", give)
    done = give >= need
    if done:
        p.setdefault("flames", {})[name] = cur + 1
        _refill(p)
    return {"ok": True, "name": name, "give": give, "need": need,
            "left": max(0, need - give), "lv": cur + (1 if done else 0), "done": done}


# ---------------------------------------------------------------------------
# 商店
# ---------------------------------------------------------------------------
def shop_list() -> list:
    out = []
    for h in cfg.HERBS:
        out.append({"name": h["name"], "price": h["price"], "desc": h["desc"]})
    for s in cfg.SHOP_EXTRA:
        out.append({"name": s["name"], "price": s["price"], "desc": s["desc"]})
    return out


def shop_buy(p, name, n=1) -> dict:
    n = max(1, int(n))
    item = next((x for x in shop_list() if x["name"] == name), None)
    # 2026-11-16：原著丹药「无药方者纯金币卖」——丹药也能用「买」直接购得。
    # 只在未命中药材时兜底查丹药表，不改药材购买的任何既有行为。
    if item is None:
        rec = cfg.RECIPE_BY_NAME.get(name)
        if rec and int(rec.get("buy", 0)) > 0:
            item = {"name": name, "price": int(rec["buy"]), "desc": str(rec.get("desc", ""))}
    if not item:
        return {"ok": False, "reason": "noitem"}
    total = int(item["price"]) * n
    if int(p.get("gold", 0)) < total:
        return {"ok": False, "reason": "poor", "need": total, "have": int(p.get("gold", 0))}
    p["gold"] = int(p["gold"]) - total
    add_item(p, name, n)
    return {"ok": True, "name": name, "n": n, "total": total}


def available_recipes(p: dict) -> list:
    """按玩家境界等级过滤丹方：只返回 lv >= min_lv 的丹药（2026-11-16 用户要求）。

    等级不够的丹药完全不展示（纯文本与卡片共用这一个过滤口，杜绝两处口径分叉）。
    """
    lv = int(p.get("lv", 1))
    return [r for r in cfg.RECIPES if lv >= int(r.get("min_lv", 1))]


def recipes_page(p: dict, page=1) -> tuple[list, int, int]:
    """当前玩家可见丹方的「一页切片」——纯文本与卡片共用的唯一分页口径。

    返回 ``(chunk, page, total_pages)``：
      * chunk     当前页丹药（已按等级过滤，按 cfg.RECIPES 原序）
      * page      收敛后的实际页码（越界自动夹到 [1, total_pages]）
      * total_pages 至少为 1

    「只显示当前境界可用丹药」由 :func:`available_recipes` 保证；本函数只在其上
    做分页。page 越界做夹取是常规边界处理（不是失败降级）——手打「炼药99」时
    落到最后一页，而不是报错或吐空页。
    """
    avail = available_recipes(p)
    per = max(1, int(cfg.RECIPES_PER_PAGE))
    total = max(1, (len(avail) + per - 1) // per)
    page = max(1, min(total, int(page or 1)))
    chunk = avail[(page - 1) * per: page * per]
    return chunk, page, total


# ---------------------------------------------------------------------------
# 任务
# ---------------------------------------------------------------------------
def current_main(p) -> dict | None:
    idx = int(p.get("main_idx", 0))
    if idx >= len(cfg.MAIN_TASKS):
        return None
    return cfg.MAIN_TASKS[idx]


def task_accept(p, rng=random.random) -> dict:
    if p.get("task"):
        return {"ok": False, "reason": "busy", "task": p["task"]}
    t = rng_choice(cfg.SIDE_TASK_POOL, rng)
    t = dict(t)
    t["progress"] = 0
    p["task"] = t
    return {"ok": True, "task": t}


def task_progress(p, kind, n=1) -> None:
    """打怪/采药/炼药/强化时推进支线。"""
    t = p.get("task")
    if not t or t.get("kind") != kind:
        return
    t["progress"] = int(t.get("progress", 0)) + int(n)


def task_submit(p) -> dict:
    t = p.get("task")
    if not t:
        return {"ok": False, "reason": "none"}
    if int(t.get("progress", 0)) < int(t.get("target", 1)):
        return {"ok": False, "reason": "unfinished", "task": t}
    rw = t["reward"]
    p["gold"] = int(p.get("gold", 0)) + int(rw.get("gold", 0))
    p["exp"] = int(p.get("exp", 0)) + int(rw.get("exp", 0))
    p["luck"] = min(9999, int(p.get("luck", 0)) + int(rw.get("luck", 0)))
    p["task"] = None
    # A 线隐藏彩蛋：支线 desc/reward 不写火名，完成时静默收服，回包才揭晓（惊喜）。
    surprise = ""
    hidden = (getattr(cfg, "A_FLAME_SIDE", {}) or {}).get(str(t.get("name")), "")
    if hidden:
        res = unlock_flame(p, hidden)
        if res.get("ok"):
            surprise = hidden
    return {"ok": True, "task": t, "reward": rw, "surprise_flame": surprise}


def main_progress(p, kind, n=1) -> None:
    t = current_main(p)
    if not t or t.get("kind") != kind:
        return
    p["main_progress"] = int(p.get("main_progress", 0)) + int(n)


def main_submit(p) -> dict:
    t = current_main(p)
    if not t:
        return {"ok": False, "reason": "done"}
    if int(p.get("main_progress", 0)) < int(t["target"]):
        return {"ok": False, "reason": "unfinished", "task": t,
                "progress": int(p.get("main_progress", 0))}
    rw = t["reward"]
    p["gold"] = int(p.get("gold", 0)) + int(rw.get("gold", 0))
    p["exp"] = int(p.get("exp", 0)) + int(rw.get("exp", 0))
    p["luck"] = min(9999, int(p.get("luck", 0)) + int(rw.get("luck", 0)))
    p["main_idx"] = int(p.get("main_idx", 0)) + 1
    p["main_progress"] = 0
    return {"ok": True, "task": t, "reward": rw, "next": current_main(p)}


# ---------------------------------------------------------------------------
# 任务途中的机遇（原版世界地图随机事件，文案见 cfg.CHANCE_EVENTS）
# ---------------------------------------------------------------------------
#: 可注入的随机源。验收脚本用 set_chance_rng(lambda: 1.0) 关掉随机，
#: 用 set_chance_rng(lambda: 0.0) 强制必触发，避免概率让测试变 flaky。
_chance_rng = random.random


def set_chance_rng(fn) -> None:
    """注入机遇随机源（仅测试/调试用）；传 None 恢复 random.random。"""
    global _chance_rng
    _chance_rng = fn or random.random


def _pick_chance(rng) -> dict:
    total = sum(max(0, int(e.get("weight", 1))) for e in cfg.CHANCE_EVENTS)
    pick = rng() * total
    acc = 0
    for e in cfg.CHANCE_EVENTS:
        acc += max(0, int(e.get("weight", 1)))
        if pick < acc:
            return e
    return cfg.CHANCE_EVENTS[-1]


def _apply_chance(p, ev: dict, rng) -> dict:
    """把一次机遇真正结算到玩家 dict，返回可展示的结果（不改存档 schema）。"""
    text = str(ev.get("text") or "")
    tone = str(ev.get("tone") or "neutral")

    # 竞猜：改成真·选项按钮问答（2026-11-16 用户要求「不要默认正确」）。
    # 这里只出题、不结算奖励、不显示答案；奖励/答案下移到命令层的答题会话，
    # 由按钮点击回调判定答对后才发放。core 保持纯函数（不碰 store/会话）。
    quiz = ev.get("quiz")
    if quiz:
        idx = min(len(quiz) - 1, int(rng() * len(quiz)))
        raw_q = str(quiz[idx])
        items = ev.get("quiz_items") or []
        item = items[idx] if idx < len(items) else {}
        # question 从原版文案去掉「（答案：X）」，逐字保留题干；干扰项/答案下标
        # 来自 config.quiz_items（与原版 5 题一一对应），答案绝不进展示文本。
        question = re.sub(r"（答案：[^）]*）\s*$", "", raw_q).strip()
        options = [str(x) for x in (item.get("options") or [])]
        answer = int(item.get("answer", 0))
        return {"name": ev["name"], "text": "《斗破苍穹》有奖竞猜！" + question,
                "tone": tone, "gain": "选项作答（答对得奖）", "src": ev.get("src", ""),
                "reward": {}, "quiz": {"question": question, "options": options,
                                       "answer": answer,
                                       "reward": dict(ev.get("reward") or {})}}


    # 条件：需要交出丹药。原版「丹不够，老者悻悻走开」是真实分支，不扣不奖。
    need = ev.get("need") or {}
    for name, n in need.items():
        if count_item(p, name) < int(n):
            return {"name": ev["name"], "text": str(ev.get("missing_text") or text),
                    "tone": "neutral", "gain": "一无所获", "src": ev.get("src", ""),
                    "reward": {}}
    # 条件：破财事件金币不足 → 原版「匪头嫌你穷，踹走」分支，零损失
    guard = int(ev.get("guard_gold") or 0)
    if guard > 0 and int(p.get("gold", 0)) < guard:
        return {"name": ev["name"], "text": str(ev.get("poor_text") or text),
                "tone": "neutral", "gain": "毫发无伤", "src": ev.get("src", ""),
                "reward": {}}

    for name, n in need.items():
        take_item(p, name, int(n))

    rw = ev.get("reward") or {}
    parts: list[str] = []
    gold = int(rw.get("gold") or 0)
    if gold:
        p["gold"] = max(0, int(p.get("gold", 0)) + gold)
        parts.append("金币 %+d" % gold)
    exp = int(rw.get("exp") or 0)
    if exp:
        p["exp"] = int(p.get("exp", 0)) + exp
        parts.append("修为 %+d" % exp)
    luck = int(rw.get("luck") or 0)
    if luck:
        p["luck"] = min(9999, max(0, int(p.get("luck", 0)) + luck))
        parts.append("机缘 %+d" % luck)
    for name, n in (rw.get("item") or {}).items():
        if int(n) > 0:
            add_item(p, name, int(n))
            parts.append("%s ×%d" % (name, int(n)))
    if need:
        parts.append("交出 " + "、".join("%s ×%d" % (k, int(v)) for k, v in need.items()))
    return {"name": ev["name"], "text": text, "tone": tone,
            "gain": " · ".join(parts) if parts else "无事发生",
            "src": ev.get("src", ""), "reward": rw}


def roll_chance(p, rng=None) -> dict | None:
    """掷一次机遇；命中则**直接结算**奖励到 p 并返回展示数据。

    只在「做任务途中」调用（见 advance_task），没任务时不触发。
    """
    if not cfg.CHANCE_EVENTS:
        return None
    r = rng or _chance_rng
    if r() >= float(cfg.CHANCE_RATE):
        return None
    return _apply_chance(p, _pick_chance(r), r)


def advance_task(p, kind, n: int = 1, rng=None) -> dict | None:
    """推进任务进度，并在任务途中按概率触发一次机遇。

    :param kind: 单个 kind 字符串；或 {kind: n} 一次推进多种（一次动作只掷一次机遇）。
    :return: 命中的机遇 dict；没接支线且主线已通关 / 未命中则 None。

    注意：core.task_progress() / main_progress() 保持纯进度语义不变（测试直接调它们），
    机遇只在命令层走这个统一入口，避免一次动作被掷两次。
    """
    if isinstance(kind, dict):
        for k, v in kind.items():
            task_progress(p, k, v)
            main_progress(p, k, v)
    else:
        task_progress(p, kind, n)
        main_progress(p, kind, n)
    if not (p.get("task") or current_main(p)):
        return None
    return roll_chance(p, rng)


# ---------------------------------------------------------------------------
# 战力榜
# ---------------------------------------------------------------------------
def rank(players) -> list:
    ps = sorted(players, key=power, reverse=True)
    return ps
