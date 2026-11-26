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
import math
import random
import re
import time

from . import config as cfg
from . import doupo_text as txt
from . import flame_bonus as _flame_bonus, temp_buff as tb, danwen as dw
from . import flame_drop as _fdrop
from .flame_drop import s_flame_gate  # noqa: E402  纯函数外迁，保持 core.s_flame_gate 入口
from . import factions as fac
from . import skills as sk_mod
from . import skill_effects as fx
from . import sect_mechanics as sm
from . import weapon_data as wd

# 纯函数 re-export（实现见 sect_mechanics.py）
from .sect_mechanics import (  # noqa: E402
    c_stat, level_dodge, mon_tier, mitigation, is_devour_sect, devour_amount, devour_of,
    is_guzu_sect, clan_tier_pct, is_danta_sect, danhui_bonus, craft_rate_bonus,
    is_sheren_sect, seal_atk_rate, sect_allstat_pct,
)
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
        # 2026-11-25 剧情章节（用户拍板 G1=A / G2=怪等级按章节走）：
        # 第 1 章开通的是「乌坦城·萧家后山」。**唯一写入点**是 main_submit，
        # 与 main_idx 同笔推进（story_chapter = main_idx + 1）。
        # 老档没有这个字段 → 读侧一律 `ensure_story_chapter()` 显式判空补
        # `main_idx + 1`（夹在 [1, CHAPTER_COUNT]），禁止用 `p.get(...) or 1`
        # （合法 0 会被吞成 1 并改写存档）。
        "story_chapter": 1,
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
    # 2026-11-24：入道不再送火（异火极稀有，用户口径）。空火开局，靠掉落/炼单方得火。
    # grant_faction_flame 保留为兼容入口，但 STARTER_FLAME="" 时它不发任何火。
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
# 剧情章节门禁（2026-11-25 用户拍板 Q1=A / G2=「怪等级按章节走」）
#
# 三层职责严格分开，禁止互相兜底：
#   1. `ensure_story_chapter` —— 读侧迁移（老档没有字段时显式判空补）；
#   2. `chapter_pool`         —— 章 → 可遇怪池（已开通章并集，硬锁，池空必抛）；
#   3. `_monster_lv_for`      —— 怪等级 = min(玩家等级, 该怪所属章的基准等级)，
#                                只能把怪压**低**（护章初新手），永远抬不**高**。
# ---------------------------------------------------------------------------
def ensure_story_chapter(p: dict) -> int:
    """读玩家当前剧情章；老档缺字段时按 `main_idx` 补一次并写回。

    🚨 必须 `is None` 显式判空，**禁止 `p.get(...) or 1`**：0 是合法输入
    （历史/脏档：尚未开通任何区域），`or` 会把它吞成 1 **并改写存档**，
    同时让下面的迁移分支变成永不可达的死代码。
    补值口径与 `main_submit` 的写入口径一致：`story_chapter = main_idx + 1`，
    并夹在 `[1, CHAPTER_COUNT]` 内（老档 main_idx 越界时不至于指向不存在的章）。
    """
    v = p.get("story_chapter")
    if v is None:
        v = int(p.get("main_idx", 0) or 0) + 1
        v = max(1, min(cfg.CHAPTER_COUNT, v))
        p["story_chapter"] = v
    return max(1, min(cfg.CHAPTER_COUNT, int(v)))


def chapter_pool(ch: int) -> list[dict]:
    """已开通章（1..ch）的怪种并集，返回 `cfg.MONSTERS` 里的 species dict（保序去重）。

    🚨 硬锁核心：**只含已开通章的怪**，绝不因为「池子太小」回落去抽未开通章的怪。
    池空说明 `CHAPTER_SPECIES` 配错，直接抛 `ValueError` 上抛——绝不静默降级
    （降级会让玩家在第 1 章遇到魂灭生，比报错更难查）。
    """
    ch = max(1, min(cfg.CHAPTER_COUNT, int(ch)))
    names: list[str] = []
    for c in range(1, ch + 1):
        for nm in cfg.CHAPTER_SPECIES.get(c, ()):
            if nm not in names:
                names.append(nm)
    by_name = {str(m["name"]): m for m in cfg.MONSTERS}
    out = [by_name[nm] for nm in names if nm in by_name]
    if not out:
        raise ValueError(
            "章节 %d 的可遇怪池为空：CHAPTER_SPECIES 前 %d 章没有任何命中 MONSTERS 的怪名"
            % (ch, ch))
    return out


def chapter_of_species(name: str) -> int | None:
    """物种名 → 它所属的章（`CHAPTER_SPECIES` 的逆）；不在任何章返回 None。

    同一物种若被多章引用，取**最小章号**（最先开通它的章）——怪等级以最先
    能遇到它的那一章为准，高章再引用不会把它悄悄抬强。
    """
    nm = str(name)
    for c in sorted(cfg.CHAPTER_SPECIES):
        if nm in cfg.CHAPTER_SPECIES[c]:
            return int(c)
    return None


def _monster_lv_for(p: dict, spec: dict, chapter: int | None) -> int | None:
    """章节门禁下这只怪的实例等级 = ``min(玩家等级, 该怪所属章的基准等级)``。

    用户 2026-11-25 拍板 G2：「取消玩家等级抬高怪等级，怪等级按章节走」。
    落地口径取**上限封顶**，各方向严格如下：

    * **封顶（这才是「取消抬高」）**：玩家等级**不能**把怪抬到章节基准之上。
      lv40 回第 1 章，怪仍是 lv5（`chapter_base_lv(1)=5`），经验按 lv5 给
      （实测 215/场），而 lv40 升级要 71111 —— 刷低章怪收益 0.30%，逼推主线。
    * **托底（保护章初）**：玩家等级低于章节基准时，怪降到玩家等级。
      lv1 新号在第 1 章遇到 lv1 的怪（而不是 lv5）—— 实测固定 lv5 时
      lv1 胜率只有 12%（劝退），托底后 39%（旧口径 35%，不是变难而是不再必死）。
    * **章内不变**：玩家一旦达到章节基准，怪等级就完全由章节决定，不再随玩家变。

    实测（ch1、1~5 敌随机、无丹、每格 120 场，胜率/均修为）：
    lv1 39%/8、lv5 40%/133、lv9 73%/188、lv40 100%/215。
    三方案对照（设计文档 §10.1）：A 固定中点不托底 lv1 只有 12%；
    C 只钳到玩家等级 lv40 能拿 2648/场（刷低章暴利，等于没惩罚）。

    :param chapter: 玩家当前章；None（非门禁调用）→ 返回 None，走旧口径。
    """
    if chapter is None:
        return None
    ch = chapter_of_species(spec.get("name")) or int(chapter)
    return min(int(p.get("lv", 1)), cfg.chapter_base_lv(ch))


#: 章节基准玩家的缓存（等级 → 裸装档）。只读，绝不写回任何真实玩家。
_BASELINE_CACHE: dict[int, dict] = {}


def _baseline_at(lv: int) -> dict:
    """构造「该等级的标准新号」，只用于按章节等级反解怪物数值。

    与 `create_player` 同源（同一套基础属性/默认武器/无增益、无火、无宗门加成），
    因此「第 N 章的怪有多强」= 「裸装练到该等级的新号打它要几回合」，与具体玩家
    的装备/异火/宗门无关 —— 这是「怪等级按章节走」不被玩家强度污染的前提。

    缓存：同一等级的基准档只构造一次（`stats` 只读它，不会写回）。
    """
    lv = max(1, min(cfg.MAX_LV, int(lv)))
    b = _BASELINE_CACHE.get(lv)
    if b is None:
        b = create_player("__baseline__", "__baseline__", "")
        b["lv"] = lv
        b["hp"] = stats(b)["体力"]
        b["mp"] = mp_max(b)
        _BASELINE_CACHE[lv] = b
    return b


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

    # 武器四词条（口径见上 docstring）：_weapon_rows 与 weapon_panel 同源。
    cur_weapon, _ = _weapon_rows(p)
    for k, v in cur_weapon.items():
        out[k] = out.get(k, 0) + v

    for k, v in (p.get("bonus") or {}).items():
        out[k] = out.get(k, 0) + int(v)

    # 2026-11-24 限时增益：与五维丹同口径（帝炎同样放大），逻辑见 temp_buff。
    tb.merge(out, p)

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

    _clan_pct = sect_allstat_pct(p)  # 古族/天府 六维比例
    if _clan_pct:
        for k in ("力量", "敏捷", "斗气", "防御", "体力"):
            out[k] = int(out[k] * (1.0 + _clan_pct))

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
    base += sm.taixu_evade_pts(p)   # 太虚闪避（自然 base）
    base += int((p.get("bonus") or {}).get("闪避", 0))
    return cfg.dodge_final(base, p)


def crit_rate(p) -> float:
    """暴击率（%）。[源码] y.java:155  a[0][2] += (a[0][6]+30)/100"""
    st = stats(p)
    base = cfg.HERO_CRIT + (st["敏捷"] + 30) // 100
    base += 10 * int((p.get("flames") or {}).get("净莲妖火", 0))
    base += int((p.get("bonus") or {}).get("暴击", 0))
    return float(min(80, base))


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
    base += sm.taixu_overflow_hit(p)  # 太虚闪避溢出→命中
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


def encounter_exp_divisor(lv: int) -> float:
    """整场经验池除数 K(lv)——**前快后慢曲线的唯一计算函数**（2026-11-23）。

    K(lv) = K_BASE × (1 + K_RAMP × (lv-1)/(MAX_LV-1))
      * lv=1        → K_BASE（≈4，前期 2.67 场/级，不卡新手）
      * lv=MAX_LV-1 → K_BASE×(1+K_RAMP)（≈27.8，后期显著变慢）
      * K_RAMP=0    → 退回恒定 K_BASE（旧行为，变异反证用）

    🚨 单一真源：反解、实战、守门测试都必须调本函数，禁止在别处手抄公式。
    系数乘在**除数**上（分母越大发得越少），实现「单场经验增长率 < 需求增长率」
    ⇒ 每级所需场数随等级单调增加 ⇒ 前快后慢。
    """
    base = float(cfg.ENCOUNTER_EXP_BUDGET_DIVISOR)
    ramp = float(getattr(cfg, "ENCOUNTER_EXP_K_RAMP", 0))
    span = max(1, int(cfg.MAX_LV) - 1)
    clamped = max(1, min(int(cfg.MAX_LV), int(lv)))
    return base * (1.0 + ramp * (clamped - 1) / float(span))


def encounter_exp_budget(p: dict, enemy_count: int, base_lv: int | None = None) -> int:
    """整场遭遇战的修为总预算（2026-11-22 v0.2 + 2026-11-23 前快后慢，唯一真源）。

    一场战斗的回报由「这场打几只怪」改成「这场战斗本身」：
        budget = exp_need(基准lv) / K(基准lv) × 异火倍率 × (1 + min(CAP, STEP×(敌数-1)))
    多敌只给有限补偿（每多 1 敌 +10%，封顶 +40%），不再逐敌线性累加。
    ``enemy_count`` 是**整场抽取的敌数**（encounter 开场已知），故预算在战斗中固定；
    实际发放按「已击败份额 = budget / enemy_count × 已胜敌数」，中途战败不补发。

    :param base_lv: 预算基准等级（2026-11-25 章节门禁）。给定 → 按**怪等级**算，
        不再按玩家等级；玩家 lv50 回第 1 章打 lv9 的怪，拿的就是 lv9 的预算
        （这正是「练级不推主线」的惩罚）。None → 旧口径（按玩家等级）。

    K(lv) / STEP / CAP 全部来自 config（K(lv) 经 encounter_exp_divisor），
    本函数不重复定义（单一真源）。
    """
    lv = int(p.get("lv", 1)) if base_lv is None else int(base_lv)
    need = cfg.exp_need(lv)
    n = max(1, int(enemy_count))
    hard = min(float(cfg.ENCOUNTER_EXP_HARD_CAP),
               float(cfg.ENCOUNTER_EXP_HARD_STEP) * (n - 1))
    # 异火倍率仍是**玩家**的加成（火是玩家资产，不该因怪等级低就失效）。
    budget = int(need / encounter_exp_divisor(lv)
                 * flame_exp_mult(p) * (1.0 + hard))
    return max(0, budget)


def encounter_exp_share(budget: int, enemy_count: int, wins: int) -> int:
    """整场预算里「已击败 wins 敌」应得的修为（整除到敌，余量给首敌）。

    Σ各敌份额恰好等于 budget：整场全胜 = budget；中途战败 wins<n = 已胜份额。
    """
    n = max(1, int(enemy_count))
    w = max(0, min(n, int(wins)))
    if w <= 0:
        return 0
    base, rem = divmod(int(budget), n)
    return int(base * w + (rem if w >= 1 else 0))


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
        # 帝品雏丹（effect=ascend）优先：本源帝气直接把这次判定钉成必成。
        if int(p.get("ascend", 0)) > 0:
            p["ascend"] = int(p["ascend"]) - 1
            rate = 1.0
        else:
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


def make_breakthrough_boss(p, rng=random.random, puppet=None, formation_n=0,
                           puppet_flame_mult=1.0) -> dict:
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
        "skills": [dict(s) for s in (ch.get("skills") or []) if isinstance(s, dict)],
        "character": _ch,
    }
    # 傀儡（2026-11-20 第二轮）：怪血锚快照 snap（锁难度），实战锚真人 p。
    # 只经 PUPPET_ENEMY_COMP 折扣后计入血量，攻击不受影响 —— 带傀儡是净赚而不是对消。
    _pu_bonus = 0.0
    if puppet:
        from . import puppet_core as _pc
        _pu_bonus = _pc.puppet_expected_damage(
            puppet, formation_n, flame_mult=puppet_flame_mult, p=snap)
    mon = monster_scaled(snap, species, puppet_bonus=_pu_bonus)
    mon["hp"] = max(1, int(mon["hp"] * float(cfg.BOSS_BREAK_HP_MULT)))
    mon["atk"] = max(1, int(mon["atk"] * float(cfg.BOSS_BREAK_ATK_MULT)))
    mon["is_boss"] = True
    mon["breakthrough_boss"] = True
    mon["boss_title"] = str(ch.get("title") or "")
    # 内部校验字段：**禁止**出现在任何玩家可见文案/卡片（见 commands.cmd_break 守门）
    mon["snapshot_power"] = power(p)
    return mon


def nine_star_breakthrough(p, rng=random.random, locked=None,
                           puppet=None, formation_n=0,
                           puppet_flame_mult=1.0) -> dict:
    """九星突破 BOSS 战完整流程（命令层调用；core 只做逻辑不改存档持久化）。

    前置必须由调用方先用 ``is_nine_star_breakthrough(p)`` 判定。本函数：
      1. 内部锁战力快照 + 按突破档取一名原著 BOSS（make_breakthrough_boss）；
      2. 用既有的 ``fight()`` 打这一场（自动服药/锁定物品口径与普通战斗一致）；
      3. 胜利 → 掉火焰（roll_breakthrough_flame）→ level_up 完成突破；
         失败 → 保留战斗损耗（掉血/耗药），经验不扣，可再次挑战。
    返回 dict 给命令层渲染文案；``p`` 就地被修改（胜负都由 fight / level_up 写）。

    ``puppet``（2026-11-20 第二轮）：出战傀儡。突破必须允许带傀儡，否则「炼傀儡」
    在唯一的境界关口上毫无意义。
    """
    mon = make_breakthrough_boss(p, rng, puppet=puppet, formation_n=formation_n,
                                 puppet_flame_mult=puppet_flame_mult)
    res = fight(p, mon, rng=rng, locked=locked or set(), auto_pill=True,
                puppet=puppet, formation_n=formation_n,
                puppet_flame_mult=puppet_flame_mult)
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
        # P1-1：透传护主战果，供命令层写回战损。
        "puppet_guard": res.get("puppet_guard"),
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
    """九星 BOSS 胜利掉火（2026-11-24 按境界分档；决策在 flame_drop，此处只落库）。

    低境界兽火档 50%、高境界异火档 100%。写入 p["flames"][name]=1（单一真值），
    已持有折算药渣（防刷）。A/S/帝炎不进池。
    """
    lv = int(p.get("lv", 1))
    # 突破成功后调用方已 level_up？此函数在 level_up 之前调用（见 nine_star_breakthrough），
    # 故用 lv+1 作为"突破后境界"决定掉落档（低境界掉兽火，斗宗以上掉异火）。
    d = _fdrop.roll_flame_drop(lv + 1, rng=rng)
    name, kind = d["name"], d["kind"]
    if not name:
        return {"ok": False, "name": "", "kind": ""}
    fl = p.setdefault("flames", {})
    if int(fl.get(name, 0)) > 0:
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
    mult = 1.0 + crit_rate(p) / 100.0 * (cfg.crit_mul(p) - 1.0)
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


def monster_scaled(p, mon, puppet_bonus: float = 0.0, at_lv=None) -> dict:
    """把"物种"实例化成一只同阶怪。

    为什么不写死数值：玩家每 9 级 c_stat 有一次 阶² 的平方级跃升，
    固定血量的怪在跃升前后会从"白给"直接变"必死"（实测断崖：16~18 级胜率 0%）。

    血量：反解"玩家刚好在 TTK_PLAYER 回合斩杀"对应的累计伤害。
    攻击：反解"怪刚好在 TTK_PLAYER × MON_ROUND_RATIO 回合斩杀玩家"的单击值。

    :param at_lv: 把怪实例化到**指定等级**（2026-11-25 章节门禁用）。
        给定时不看真实玩家，改用 ``_baseline_at(at_lv)``（该等级的裸装新号）反解，
        并把实例的 ``lv`` 就地改成该等级 —— 玩家再强也抬不动低章怪的数值。
        None（默认）→ 沿用旧口径（按玩家等级反解），爬塔/周本/切磋/突破逐字节不变。
    """
    if at_lv is not None:
        _lv_at = max(1, min(cfg.MAX_LV, int(at_lv)))
        p = _baseline_at(_lv_at)
        mon = dict(mon, lv=_lv_at)   # 复制：绝不改 cfg.MONSTERS 里的物种真源
    st = stats(p)
    lv = int(p.get("lv", 1))
    # 等级差只微调"几回合打完"，上下限收紧到 ±20%（原 ±60%/+80%）：
    # 原著 13 阶补齐后，物种表(7 只)不可能覆盖 103 级，总有些等级只能遇到
    # 隔了十几级的怪；旧钳位会把这种战斗压成 2.9 回合秒杀或拖到 9 回合，
    # 越过 test_monster_scaled_tracks_player 的 3~8 回合设计窗口。
    delta = max(-0.2, min(0.2, (int(mon["lv"]) - lv) * cfg.LV_DELTA_STEP))
    m_def = max(20, int(st["防御"] * cfg.MON_DEF_RATIO))
    # P3 远古人傀「灰白布条」（第1235章「即便是斗宗强者的全力一击，都是无法
    # 震断这种奇异布条」）：布条把力道卸掉 ⇒ 额外防御。
    # 🚨 必须加在 ``m_hp`` 之前：血条按同一个 mitigation 换算，于是「几回合打完」
    #    仍由 k_rounds 决定，玩家看到的只是**每一击的数字变小**（布条真的卸力），
    #    而不是被偷偷改了难度曲线 —— 与「面板只决定谁打得疼、不改几回合」同一条
    #    契约。没写 cloth 的怪（全部旧怪）乘 0，数值逐字节不变。
    _cloth = float(mon.get("cloth") or 0.0)
    if _cloth > 0:
        m_def = max(20, int(m_def * (1.0 + _cloth)))

    # 玩家打完需要几回合（等级差只影响时长，不影响难度）
    k_rounds = cfg.TTK_PLAYER * (1.0 + delta)
    # 傀儡期望输出计入反解，但**只按 PUPPET_ENEMY_COMP 比例变厚**（2026-11-20 第二轮）。
    # 全额计入（旧口径）会让「多一个打手」与「怪血同步变厚」在数学上完全对消：
    # 回合数、胜率、承伤全都不变，带傀儡等于白带 —— 这正是用户说「没意义」的根因。
    # 0.55 折扣后带傀儡净赚 45% 傀儡输出，回合数实打实变少。
    _pu_hp = float(puppet_bonus) * float(cfg.PUPPET_ENEMY_COMP)
    m_hp = int(cum_damage(p, k_rounds, puppet_bonus=_pu_hp) * mitigation(m_def))
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
    #
    # 🚨 P3 修：遗迹段落的 dict 里 ``name`` 是**段名**（「斗圣主殿」）、
    #    ``monster`` 才是物种名（「远古人傀」）。旧代码一律按 ``name`` 查角色包与
    #    技能表，于是遗迹里的怪永远只能拿到 kind 兜底的通用技（boss → 魂殿秘技），
    #    远古人傀「会施斗技」这条原著设定根本落不了地。物种优先、name 兜底，
    #    不传 monster 的既有调用方逐字节不变。
    _species = str(mon.get("monster") or mon.get("name") or "")
    _character = mon.get("character") or cfg.monster_character(_species)
    # 怪物技能：species 自带 > 角色包专属 > 野外技能表 > kind 兜底。
    # 角色包优先于 SKILL_BY_KIND，塔层 BOSS 才能用「血刀」「摘星手」而不是
    # 千篇一律的「魂殿秘技」；数值口径仍沿用既有表，不重算面板。
    _skill = (mon.get("skill") or (_character or {}).get("skill")
              or cfg.MONSTER_SKILLS.get(_species))
    if not _skill:
        for _tok, _sk in cfg.SKILL_BY_KIND.items():
            if _tok in _kind:
                _skill = _sk
                break
    _panel = monster_panel(int(mon["lv"]), _kind, _is_boss)
    # 只在 species 显式带 skills 时新增字段；其余怪旧路径逐字节不变。
    _skills = [dict(s) for s in (mon.get("skills") or []) if isinstance(s, dict)]
    _out = {
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
    if _skills:
        _out["skills"] = _skills
    return _out


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
    # 11-20 修：降攻此前只显示文案不真降伤害（玩家出手从不读 p_fx）；
    # 这里按 self 侧降攻打折，无降攻恒 1.0 → 普通战斗逐字节不变。
    if fx_self is not None:
        raw *= fx.target_atk_mult(fx_self)

    # 基础单击（未计破防/条件）：也是 DoT 的伤害基数
    # def_mult 是本次实际生效的防御减伤系数（破防会改写它）；回看卡据它算
    # 「面板 → 防御削减 → 实收」，纯展示，不参与任何数值计算。
    def_mult = mitigation(target_def)
    base = max(1, int(raw * def_mult * (cfg.crit_mul(p) if crit else 1.0)))
    notes: list[str] = []
    heal_ratio = 0.0
    dmg = base
    # 11-20 修：目标闪避此前从没结算（怪 evade 纯文案、7 门压闪避斗技只加描述）。
    # 目标闪避 = evade + 压制量(负)；位置在 on_hit 前，闪掉则不挂毒/降攻。
    # 命中对冲：atk_hit(%) 减在目标闪避上，与「玩家躲怪」同一公式另一半；
    # 默认 0 → PVP 逐字节不变；t_ev<=0 不掷骰 → 普通战斗 rng 序列不变。
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

    # 魂殿邪修入教即锁宗（扣款前拦截；转回本宗仍走 same 分支）
    if is_devour_sect(p) and hit["faction"] != str(p.get("faction") or ""):
        return {"ok": False, "reason": "devour_locked", "name": hit["name"]}

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
        # 2026-11-24 BUG 修复（用户裁定「转宗不应舍弃已学斗技，不符合原著」）：
        # 已学斗技一律全保留（含老宗门专属技）——玩家辛苦领悟的技不因改换门庭而永久消失，
        # 「斗技」口令能看到全部已学，「宗门斗技」口令单独看本宗。
        # 赠 1 门新派低阶入门技作为「改换门庭的传承」，其余靠「学斗技」逐个领悟。
        kept = [n for n in p["learned"]]
        starters = [e["name"] for e in sk_mod.learnable(p)
                    if e.get("origin") != "异火" and e["name"] not in kept]
        if starters:
            kept.append(random.Random(
                "faction-gift:" + str(p.get("qq")) + hit["faction"]).choice(starters))
        p["learned"] = list(dict.fromkeys(kept))
        # 四槽仍按新宗可用技重配（test_switch_works_at_pool_level 约束：四槽全属新派），
        # 但候选只从「已领悟」里选，不凭空上阵未学的技。
        new_names = {e["name"] for e in sk_mod.catalog(hit["faction"])}
        new_loadout = [n for n in p["learned"] if n in new_names][:cfg.LOADOUT_SIZE]
        p["loadout"] = new_loadout or list(p["learned"])[:cfg.LOADOUT_SIZE]
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


def learned_skill_pool(p) -> list[dict]:
    """**所有已领悟斗技**（跨宗），供「斗技」口令展示（2026-11-24）。

    与 ``skill_pool``（本宗池 + 异火）不同：转宗保留全部已学技后，玩家会持有
    老宗门专属技，这些不在本宗池里，必须按 ``p["learned"]`` 逐名还原。
    条目与 skill_pool 同形（含 tier/unlocked/equipped/origin），排序也一致。
    """
    entries = sk_mod.learned_entries(p)
    tier_order = {t: i for i, t in enumerate(cfg.SKILL_TIERS)}
    return sorted(entries, key=lambda e: tier_order.get(e.get("tier"), 9))


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
    skill_pool_sorted 的同一顺序；这里把该顺序编成 {1: name, 2: name, ...}。
    ⚠️ 2026-11-25 起「上阵 N」**不再用这张表**——宗门池含未解锁技，序号会
    跳号，玩家照着上阵必然错位。上阵改用 :func:`learned_no_map`（我的斗技卡
    连续序号）。本函数保留仅为「宗门斗技」卡的展示/历史测试兼容。
    """
    return {i: e["name"] for i, e in enumerate(skill_pool_sorted(p), 1)}


def learned_no_map(p) -> dict[int, str]:
    """「我的斗技」卡连续序号 → 技名（2026-11-25 上阵序号唯一真值源）。

    用户根因：学了斗技却无法按序号上阵——旧口径 ``roster_no_map`` 用本宗
    **全池（含未解锁）**编号，序号跳号；玩家在宗门卡记的号，和真正能上阵的
    技对不上。改为只给「已领悟/可上阵」的技编号，且与 card.build_learned_data
    的显示顺序严格一致：① 已上阵四槽（出战顺序）在前；② 其余已领悟按三阶
    稳定排序。序号连续无空洞，玩家「上阵 N」直达。

    这是 ``build_learned_data`` 与 ``cmd_loadout`` 的共享真值源，任何一侧
    不得另写一份 sorted——否则卡片序号与上阵序号再次错位（守门会抓）。
    """
    lo = loadout_entries(p)
    seen: set[str] = set()
    ordered: list[str] = []
    for e in lo:  # ① 已上阵四槽优先，顺序即出战顺序
        n = e["name"]
        if n not in seen:
            seen.add(n)
            ordered.append(n)
    for e in learned_skill_pool(p):  # ② 其余已领悟，三阶稳定排序
        n = e["name"]
        if n not in seen:
            seen.add(n)
            ordered.append(n)
    return {i: n for i, n in enumerate(ordered, 1)}


def learned_no_of(p, name: str) -> int | None:
    """技名 → 「我的斗技」卡序号（学习成功后提示序号用；找不到返 None）。"""
    target = str(name or "").strip()
    for no, n in learned_no_map(p).items():
        if n == target:
            return no
    return None


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


# 2026-11-25 PVE 战斗内核外迁：实现见 battle_core.py（本文件逼近 200KB 上传红线）。
# 保留同名 re-export，所有既有 core.fight(...) 调用、monkeypatch、源码守门不变。
from .battle_core import fight  # noqa: E402


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


def pick_encounter(p, n, rng=random.random, chapter=None) -> list[dict]:
    """抽 n 个敌人 species：优先与玩家同档，且尽量跨类型（魔兽/悍匪/反派）。

    复用 pick_monster 的"同大境界窗口"口径保证不会出现越级秒杀；
    再去重 + 跨 kind，让一场遭遇读起来像"魔兽 + 山贼"而不是 5 只同种鼠。

    :param chapter: 剧情章号（2026-11-25 章节门禁）。给定 → **只从已开通章
        （1..chapter）的怪种并集里抽**，且**不做等级窗口筛选/截断**：
        全池等概率，高章怪才抽得到，低章怪也仍能遇到（收益低）。
        None（默认）→ 沿用旧的「按等级贴近」口径；爬塔/周本/切磋/突破等
        既有调用不传该参数，行为逐字节不变。
    """
    n = max(1, int(n))
    lv = int(p.get("lv", 1))
    if chapter is not None:
        # 硬锁：池子只含已开通章的怪；池空由 chapter_pool 抛错，绝不回落全表。
        cand = list(chapter_pool(chapter))
    else:
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


def encounter(p, rng=random.random, locked=None, auto_pill=True,
              puppet=None, formation_n=0, puppet_flame_mult=1.0,
              chapter=None) -> dict:
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

    ``puppet``（2026-11-20 第二轮）：出战傀儡真实列 dict。**每一敌都带**——
    傀儡是常驻打手，不是一次性外援。怪血反解与实战出手用**同一个 p 与同一个
    ``puppet_expected_damage``**（红线），不会出现「带傀儡怪血变厚却不见它出手」。
    """
    # 2026-11-25 章节门禁（用户拍板 Q1=A / G2=怪等级按章节走）：
    # chapter 给定 → 只抽已开通章的怪（硬锁），且每只怪实例化到
    # min(玩家等级, 该怪所属章的基准等级)；None → 走旧口径，逐字节不变。
    specs = pick_encounter(p, roll_encounter_count(p, rng), rng, chapter=chapter)
    st = stats(p)
    fade = max(0.0, float(cfg.ENCOUNTER_FADE))
    heal = max(0.0, float(cfg.ENCOUNTER_REST_HEAL))
    # 同一场可能混章：lv40 玩家在第 3 章，既会遇到 ch1 的 lv5 嗜血鼠，
    # 也会遇到 ch3 的 lv23 怪 —— 逐敌按**自己所属章**定级，不按玩家当前章。
    _mon_lvs = [_monster_lv_for(p, sp, chapter) for sp in specs]
    # 整场预算的基准等级：多敌混章时取均值（逐敌分别发会破坏
    # encounter_exp_share「Σ份额=预算」的整除性质，收益差 <1%）。
    _budget_lv = (int(round(sum(_mon_lvs) / len(_mon_lvs)))
                  if chapter is not None and _mon_lvs else None)

    # 2026-11-22 经验平衡 v0.2：整场修为预算开场一次算定（敌数已知），
    # 按「已击败份额」逐敌发放；中途战败只发已胜份额，不补发。
    # 公式唯一真源 = encounter_exp_budget / encounter_exp_share。
    _n_enemies = len(specs)
    _exp_budget = encounter_exp_budget(p, _n_enemies, base_lv=_budget_lv)
    _exp_base, _exp_rem = divmod(_exp_budget, _n_enemies)  # 余量给首敌，Σ恰等于预算

    # 傀儡每回合期望输出：整场算一次（p / puppet / 异火倍率都不变），
    # 每敌反解与实战注入共用这一个数 —— 两处同源是红线。
    _pu_bonus = 0.0
    if puppet:
        from . import puppet_core as _pc  # 延迟导入避免与 core 环
        _pu_bonus = _pc.puppet_expected_damage(
            puppet, formation_n, flame_mult=puppet_flame_mult, p=p)

    # 机缘·贵人相助：**整场只掷一次**（2026-11-16 修正）。
    # 旧实现让 fight() 每敌各掷一次，N 敌场累积概率 1-(1-2%)^N，5 敌≈9.6%，
    # 突破「硬顶 2%」设计口径；且只要任一敌触发就报「此战毫发无伤」，
    # 但其他敌照打、玩家照常受伤，文案自相矛盾。
    # 现在：整场开场掷一次——命中则贵人把整场敌手一招全灭（每敌传 aid、
    # roll_aid=False，各自 taken=0、正常结算），未命中则全程禁掷。
    whole_aid = roll_hero_aid(p, rng)

    detail: list[dict] = []
    total_rounds = total_taken = total_exp = total_gold = 0
    total_devour = 0   # 魂殿整场吞噬修为（汇总供战报展示）
    fire_gain = 0
    all_drops: list[tuple[str, int]] = []
    win = True
    hero_aid = whole_aid
    # 自动服药预算整场共享：多敌连战按敌数放宽，但仍有硬顶（见 config 注释）。
    pill_budget = {"hp": 0, "mp": 0,
                   "cap": min(6, int(cfg.AUTO_PILL_MAX_PER_FIGHT) * max(1, len(specs)))}
    for i, spec in enumerate(specs):
        mon = dict(monster_scaled(p, spec, puppet_bonus=_pu_bonus,
                                  at_lv=_mon_lvs[i]))
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
        # 整场经验池单敌份额：余量给首敌，Σ各敌份额恰等于整场预算。
        _share = _exp_base + (_exp_rem if i == 0 else 0)
        r = fight(p, mon, rng=rng, aid=whole_aid, roll_aid=False,
                  pill_budget=pill_budget, locked=locked, auto_pill=auto_pill,
                  puppet=puppet, formation_n=formation_n,
                  puppet_flame_mult=puppet_flame_mult, exp_override=_share)
        total_rounds += int(r["rounds"])
        total_taken += int(r["taken"])
        total_exp += int(r.get("exp") or 0)
        total_gold += int(r.get("gold") or 0)
        total_devour += int(r.get("devour") or 0)
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
    # 野外对应魔兽掉火：只掉该魔兽对应的兽火（50%），已持有折算药渣
    _bf = _fdrop.wild_flame_drops(p, [x["name"] for x in detail if x["win"]], rng, add_item)
    merged: dict[str, int] = {}
    for name, cnt in all_drops:
        merged[name] = merged.get(name, 0) + cnt
    # 终局：取最后一场 fight 的真实结束值（前面场次的血/斗气已结转进下一场，
    # 所以最后一场就是整场结束那一刻）。缺字段的旧调用方按 .get 消费，不炸。
    _last = r if specs else {}
    return {
        "win": win, "rounds": total_rounds, "taken": total_taken,
        "exp": total_exp, "gold": total_gold,
        # 魂殿整场吞噬修为（已写 p，这里仅展示）。
        "devour": total_devour,
        "drops": list(merged.items()), "log": [d for x in detail for d in x["log"]],
        "flame_drops": _bf,
        "detail": detail, "enemies": [x["name"] for x in detail],
        "enemy_count": len(detail), "hero_aid": hero_aid,
        "hp_end": _last.get("hp_end"), "mp_end": _last.get("mp_end"),
        "foe_hp_end": _last.get("foe_hp_end"),
        "foe_hp_max": int((_last.get("mon") or {}).get("hp") or 0),
        "downed": bool(_last.get("downed")),
        "exhausted": bool(_last.get("exhausted")),
        "fortune": bool(_last.get("fortune")),
        # P1-1：透传最后一战的护主战果（含 broken/id），供命令层写回战损。
        "puppet_guard": _last.get("puppet_guard"),
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


#: 傀儡遗迹「战力推荐 → 标尺等级」缓存（2026-11-22）。key=power_rec，value=ref_lv。
_RUIN_REF_LV_CACHE: dict[int, int] = {}


def ruin_ref_lv(power_rec: int) -> int:
    """后台战力推荐 → 标尺玩家等级（与爬塔同一条参考战力曲线）。

    2026-11-22 用户选定「标尺固定难度」：遗迹怪数值只由 ``power_rec`` 决定，
    与打它的真人战力无关 —— 斗尊打斗宗段是真碾压，不是怪同步变厚原地踏步。

    🚨 ``power_rec`` 是难度唯一真值；段里的 ``lv`` 只管面板/经验展示。两者初值
    取自同一点 ``power(tower_ref_player(lv))``，但改难度只改 power_rec。
    曲线 ``power(tower_ref_player(lv))`` 单调不降（守门
    ``test_doupo_ruin_fixed.py::test_ref_curve_is_monotonic``），取第一个
    power ≥ 推荐值的等级；推荐值超过封顶则钉在 MAX_LV。
    """
    key = max(0, int(power_rec))
    hit = _RUIN_REF_LV_CACHE.get(key)
    if hit is not None:
        return hit
    lo, hi, ans = 1, int(cfg.MAX_LV), int(cfg.MAX_LV)
    while lo <= hi:
        mid = (lo + hi) // 2
        if power(tower_ref_player(mid)) >= key:
            ans, hi = mid, mid - 1
        else:
            lo = mid + 1
    _RUIN_REF_LV_CACHE[key] = ans
    return ans


def ruin_scaled_monster(sg: dict, puppet=None, formation_n: int = 0,
                        puppet_flame_mult: float = 1.0) -> dict:
    """一段遗迹的实际怪物：数值锚**标尺玩家**，与真人完全无关（固定难度）。

    ``puppet_bonus`` 也按标尺算（与 ``tower_floor`` 同一口径）：怪血只按标尺傀儡
    贡献变厚，真人那具更强/同档的傀儡在 ``fight`` 里全额出手，收益全落袋。
    """
    ref = tower_ref_player(ruin_ref_lv(int(sg["power_rec"])))
    _pu_bonus = 0.0
    if puppet:
        from . import puppet_core as _pc  # 延迟导入避免与 core 环
        _pu_bonus = _pc.puppet_expected_damage(
            puppet, formation_n, flame_mult=puppet_flame_mult, p=ref)
    return monster_scaled(ref, dict(sg, name=sg.get("monster") or sg["name"]),
                          puppet_bonus=_pu_bonus)


def tower_floor(p, st: dict, rng=random.random, temp_bonus=None,
                temp_effects=None, puppet=None, formation_n=0,
                puppet_flame_mult=1.0) -> dict:
    """打塔里的一层（第 st['floor']+1 层）。

    :param p        玩家（只读；战斗在深副本上进行）
    :param st       会话状态 dict（tower_session.new_state）
    :param rng      确定性随机（按会话种子）
    :return {win, floor, kind, name, affixes, rounds, taken, hp, mp, log, ...}
            其中 hp/mp 是**战斗后会话应保留的值**，由调用方写回 st，不动 p。

    难度（2026-11-19 改造）：怪物数值由 ``td.floor_ref_lv(floor)`` 的标尺玩家
    反解 —— **同层永远同一只怪**，与打它的玩家是谁、多少战力无关。玩家变强只
    体现在「能爬更高」，而不是「同一层越来越难」。

    ``puppet``（2026-11-20 第二轮）：出战傀儡。**注意塔的特殊性**——怪血反解
    必须锚在**标尺玩家**上（否则同层怪血会随打它的人变厚，破坏「同层同一只怪」），
    而实战傀儡击锚在真人 ``fp`` 上。带傀儡时怪血只按标尺傀儡贡献变厚，
    真人傀儡若是同档更强的一具，收益全部落袋。
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
    # 傀儡：塔的怪血必须锚标尺（同层同一只怪），故反解用 _ref、实战用 fp（见 docstring）。
    _ref = tower_ref_player(ref_lv)
    _pu_bonus = 0.0
    if puppet:
        from . import puppet_core as _pc  # 延迟导入避免与 core 环
        _pu_bonus = _pc.puppet_expected_damage(
            puppet, formation_n, flame_mult=puppet_flame_mult, p=_ref)
    mon = monster_scaled(_ref, species, puppet_bonus=_pu_bonus)
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
    # 傀儡（2026-11-20 第二轮）：实战锚真人 fp，怪血则已按标尺 _ref 反解（同层同一只怪）。
    r = fight(fp, mon, rng=rng, tower_mod=mod, auto_pill=False,
              puppet=puppet, formation_n=formation_n,
              puppet_flame_mult=puppet_flame_mult)

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
        # P1-1：透传护主战果，供命令层写回战损。
        "puppet_guard": r.get("puppet_guard"),
    }


def tower_open_chest(p, st: dict, rng=random.random, floor: int | None = None,
                     chest_bonus: float = 0.0) -> list[dict]:
    """本层开宝箱：判定宝箱 → 判定武器 → 入背包/持有表。返回掉落记录。

    ``floor`` 必须是**刚打完的这一层**。不能默认读 ``st['floor']``：
    胜利后调用方已把通过层数推进到本层，若再读 st 会按下一层判定（曾经错位一层，
    导致第 10 层 Boss 宝箱按第 11 层算、武器解锁层提前）。

    ``chest_bonus``（2026-11-25 自动爬塔）：宝箱率的额外倍率增量，如 0.5 表示
    宝箱率 ×1.5。用于「无加成自动爬塔」——不吃三选一加成=难度更高，用更高掉落
    补偿。默认 0 时行为与改造前逐字节一致。
    """
    floor = int(floor if floor is not None else st.get("floor", 0))
    # 已结算水位线 <= floor 的层，其宝箱在首轮已经领过（重打旧层不重复给）。
    if floor <= max(0, int(p.get("tower_paid_floor", 0))):
        return []
    rate = min(1.0, float(td.chest_rate(floor)) * (1.0 + max(0.0, float(chest_bonus))))
    if rng() >= rate:
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


def writeback_exhaustion(p, r: dict) -> dict:
    """力竭战损写回永久存档（2026-11-26 用户「扣血力竭是不是假的」）。

    背景：塔与周副本的战斗都跑在 ``copy.deepcopy(p)`` 上，损耗只写回会话
    ``st['temp_hp'/'temp_mp']``，结算从不动 ``p``。于是「💀 力竭倒下」之后
    玩家打开人物卡仍是满血——文案在骗人。修法不是改文案（那是糊弄），
    而是**真的把这一场战损落回存档**。

    口径与 ``battle_core.fight`` 完全一致，不另造第二套：

    * ``hp`` —— 取 ``r['hp']``。战败时 fight 已把它统一压到
      「体力上限 × LOSE_HP_FLOOR（5%）」，与普通战斗、战斗回看卡同一个数
      （见 battle_core 里 2026-11-19「回看 0 血/人物卡有血」的注释）。
    * ``mp`` —— 取 ``r['mp']``（斗气结转），与普通战斗同口径。

    只在**力竭（打输）**时调用。胜利或主动收手是活着走下塔：塔的续航由会话
    ``temp_hp`` 承担，不扣永久气血，否则一轮爬塔要玩家反复服丹，破坏玩法。
    """
    p["hp"] = max(1, int(r.get("hp") or 0))
    p["mp"] = max(0, int(r.get("mp") or 0))
    return {"hp": p["hp"], "mp": p["mp"]}


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
    # 2026-11-23 逐层分档掉落（材料+丹药）：跟随同一水位线，旧层不重复。
    loot = td.incremental_loot(floors, paid)
    for _nm, _qty in loot.items():
        add_item(p, _nm, _qty)
    new_paid = max(paid, floors)
    p["tower_paid_floor"] = new_paid
    p["tower_checkpoint"] = max(int(p.get("tower_checkpoint", 0)), floors)
    best = int(p.get("tower_best", 0))
    new_best = max(best, floors)
    p["tower_best"] = new_best
    st["settled"] = True
    return {"ok": True, "floors": floors, "gold": rew["gold"], "exp": rew["exp"],
            "loot": loot, "best": new_best, "new_record": new_best > best,
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


def weekly_floor(p, snapshot: dict, st: dict, rng=random.random,
                 puppet=None, formation_n=0, puppet_flame_mult=1.0) -> dict:
    """打周副本的一层（第 st['floor']+1 层），不改玩家永久存档。

    怪物由**快照画像**反解（难度锁定），战斗用**当前玩家**深副本（当周变强生效）。
    返回 {win, floor, kind, name, affixes, rounds, hp, mp, log, reward}。

    ``puppet``（2026-11-20 第二轮）：出战傀儡。与无限塔同口径——怪血锚**快照画像**
    （当周锁定，不因当周新炼的傀儡而变厚），实战傀儡击锚真人 ``fp``。
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
    # 傀儡贡献同样锚快照（当周难度锁定），实战则锚真人 fp。
    _pu_bonus = 0.0
    if puppet:
        from . import puppet_core as _pc
        _pu_bonus = _pc.puppet_expected_damage(
            puppet, formation_n, flame_mult=puppet_flame_mult, p=snapshot)
    mon = monster_scaled(snapshot, species, puppet_bonus=_pu_bonus)
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
    # 傀儡：实战锚真人 fp（怪血已按快照反解，见上）。
    r = fight(fp, mon, rng=rng, tower_mod=mod, auto_pill=False,
              puppet=puppet, formation_n=formation_n,
              puppet_flame_mult=puppet_flame_mult)
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
        # 2026-11-22 v0.2：修为改锚快照等级（封顶 PEAK_LV），层数不再放大修为。
        "reward": wd_weekly.floor_reward(
            floor, snapshot_lv=int(snapshot.get("lv", p.get("lv", 1)))),
        # P1-1：透传护主战果，供命令层写回战损。
        "puppet_guard": r.get("puppet_guard"),
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


def duel(p, vic, rng=random.random, puppet=None, formation_n=0,
         puppet_flame_mult=1.0, vic_puppet=None, vic_formation_n=0,
         vic_flame_mult=1.0) -> dict:
    """掠夺：双方各用自己四槽随机互殴（p 先手）。斗气不足转武器普攻。

    只算胜负与战报，不改双方 hp/gold（结算由 commands.cmd_rob 负责），
    与 `fight` 共用四槽 / 按阶斗气 / 普攻兜底逻辑。

    ``puppet`` / ``vic_puppet``（2026-11-20 第二轮）：双方各自的出战傀儡。
    PVP 没有「怪血反解」这一步，所以傀儡是**净增输出**——这正是「炼傀儡要有用」
    的落点。谁炼了谁受益，双方对称：被挑战方若也有出战傀儡，按存档一起带。
    时序与 ``fight`` 对齐：本人出手 → 傀儡出手 → 对方行动。
    """
    sp, sv = stats(p), stats(vic)
    # 根基裁定（隐形）：双方各自的隐藏系数，防御侧按本人系数打折；输出侧在
    # _hit 内按攻击方系数打折。PVP 双方对称生效，root=1 时逐字节不变。
    root_p, root_v = cfg.root_factor(p), cfg.root_factor(vic)
    hp_p, hp_v = int(sp["体力"]), int(sv["体力"])
    mp_p, mp_v = mp_max(p), mp_max(vic)
    log, rounds = [], 0
    # 傀儡每回合输出：循环不变量，提前算一次（与 fight 同函数同锚）。
    _pup_atk_p = _pup_atk_v = 0.0
    if puppet or vic_puppet:
        from . import puppet_core as _pc0  # 延迟导入避免与 core 环
        if puppet:
            _pup_atk_p = (_pc0.puppet_attack(puppet, p)
                          * max(1.0, float(puppet_flame_mult)))
        if vic_puppet:
            _pup_atk_v = (_pc0.puppet_attack(vic_puppet, vic)
                          * max(1.0, float(vic_flame_mult)))
    # 防御侧有效值（与对应 _hit 调用同口径，含根基打折）
    _def_v = max(0, int(sv["防御"] * root_v))
    _def_p = max(0, int(sp["防御"] * root_p))
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
        # 傀儡击（2026-11-20 第二轮）：与 fight 同时序 —— 本人出手后、对手行动前。
        if _pup_atk_p > 0 and hp_v > 0:
            from . import puppet_core as _pcp
            _pd = max(1, int(_pup_atk_p * mitigation(_def_v)))
            _pd = min(_pd, hp_v)
            hp_v -= _pd
            log.append(_mark(_pcp.puppet_log_entry(puppet, _pd, formation_n),
                             "p", hp_p, mp_p, hp_v))
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
        # vic 的傀儡击（对称）：被控则该轮傀儡同罚不出手，与 vic 本人一致。
        if _pup_atk_v > 0 and hp_p > 0 and not vs["skip"]:
            from . import puppet_core as _pcq
            _vd = max(1, int(_pup_atk_v * mitigation(_def_p)))
            _vd = min(_vd, hp_p)
            hp_p -= _vd
            log.append(_mark(_pcq.puppet_log_entry(vic_puppet, _vd, vic_formation_n),
                             "v", hp_v, mp_v, hp_p))
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


def _roll_drops(p, rng, mon=None, puppet_out=None) -> list:
    """[源码 h.java:57-78] 千分率判定 + 基础值到 2 倍随机。

    2026-11-18 天材地宝分级掉落（用户「天才地宝也按怪物等级分配，还有 boos」）：
      * 原有的炎星石/普通药材判定放在**最前**，rng 调用顺序与数量对老路径零改变；
        ``mon=None`` 时函数在普通药材后立刻 return，行为与改动前逐字节一致。
      * 传入 mon 后，按 ``mon['lv']`` 选可掉 tier（lv 越高池越深），逐档独立千分率判定；
        ``mon['is_boss']`` 为真时**额外必掉一件 tier4 专属天材地宝**。
      * 每件天材地宝自带 effect/value，服下即生效（见 use_item），不是背包摆设。

    2026-11-20 傀儡多配方材料：同样追加在**最后**，老路径 rng 序列只增不改。
    这些料属于傀儡专用（真实列 ``puppet_materials``），**不进 players.data 的 bag**，
    所以只收集进 ``puppet_out``，由命令层 ``await pstore.add_material`` 落库。
    ``puppet_out=None``（老调用）时整段跳过 —— 行为与改动前逐字节一致。
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

    # ---- 傀儡专用材料（2026-11-20 多配方；只在命令层要收集时才跑）----
    # 只写 puppet_out，**不写 out**：out 是「进背包的战利」，这些料进傀儡库真实列，
    # 混进 out 会让展示与账目对不上（还挤掉真正的战利品位置）。
    if puppet_out is not None:
        _elite = is_boss or mon_tier(mon.get("kind")) == "elite"
        for _nm, _spec in cfg.PUPPET_DROPS.items():
            _minlv, _rate, _need_elite = _spec
            if lv < int(_minlv) or (_need_elite and not _elite):
                continue
            if int(rng() * 1000) < int(_rate):
                puppet_out.append((_nm, 1))
        if _elite:
            for _minlv, _nm in cfg.PUPPET_DROPS_SURE:
                if lv >= int(_minlv):
                    puppet_out.append((_nm, 1))
                    break
    return out


# ---------------------------------------------------------------------------
# 背包
# ---------------------------------------------------------------------------
def bag(p) -> dict:
    return p.setdefault("bag", {})


def migrate_danwen_bag(p) -> list[tuple[str, str]]:
    """把旧后缀带纹丹（``破厄丹·丹纹``）改名成新前缀名（``一纹破厄丹``）。

    2026-11-25 用户「改成一纹破厄丹这种，几纹直接写在名字前面」。
    物品名就是唯一键，所以改名=合并键（同档重复时数量相加），幂等。
    :return: ``[(旧名, 新名), ...]``，供调用方同步改锁定表；无改动返回空列表。
    """
    b = bag(p)
    moved: list[tuple[str, str]] = []
    for old in list(b.keys()):
        new = cfg.migrate_danwen_name(old)
        if not new or new == old:
            continue
        b[new] = int(b.get(new, 0) or 0) + int(b.pop(old, 0) or 0)
        moved.append((old, new))
    for name in [k for k, v in b.items() if int(v or 0) <= 0]:
        b.pop(name, None)
    return moved


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
    # 空火禁炼药；兽火单方是引火途径，例外
    if str(rec.get("effect") or "") != "beast_flame" and not _flame_bonus.has_any_flame(p):
        return {"ok": False, "reason": "nofire"}
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
    # 2026-11-20 修：傀儡资格③「控火闸」读 ``craft_count`` / ``high_grade_crafts``，
    # 但这两个字段**以前从没被生产代码写过**（只有测试手塞）→ 控火闸永远不过，
    # 傀儡炼制永久不可能。每开一炉都计一次（成败都算，原著「烧药得经验」）；
    # 成品品阶 ≥ ``PUPPET_HIGH_GRADE_FROM`` 才计「高阶丹药」。
    p["craft_count"] = int(p.get("craft_count", 0) or 0) + 1
    _is_high = (cfg.alch_grade_idx(rec.get("grade", ""))
                >= cfg.alch_grade_idx(cfg.PUPPET_HIGH_GRADE_FROM))

    # 成功率唯一入口：兽火单方走引火通道，其余按炼药师品阶判定（见 config）。
    rate, diff = cfg.rate_for_recipe(p, rec, craft_rate_bonus(p))
    _danhui = danhui_bonus(p)  # 丹塔丹会：仅加成>0挂键，非丹塔字节不变
    if rng() < rate:
        add_item(p, name, 1)
        if _is_high:
            p["high_grade_crafts"] = int(p.get("high_grade_crafts", 0) or 0) + 1
        p["alch_exp"] = int(p.get("alch_exp", 0)) + cfg.CRAFT_EXP
        up = _alch_level_up(p)
        res = {"ok": True, "rate": rate, "diff": diff, "alch_up": up}
        # 丹纹：成功之后按品阶掷，命中额外产一枚带纹丹（纹数 1~3，服用给随机赐福）
        # 2026-11-25 补洞：必须先过 `cfg.danwen_allowed` —— 焚血散这类「升技材料/
        # 引火/养魂」丹根本没有注册带纹版，掷出来就是一枚吃不了也锁不了的死物品
        # （线上实测抓到 `焚血散·一纹`）。
        if cfg.danwen_allowed(rec) and (_t := dw.roll(rec.get("grade", ""))):
            add_item(p, cfg.danwen_name(name, _t), 1)
            res["danwen"] = _t
        if _danhui > 0:
            res["danhui"] = round(_danhui, 4)
        return res
    add_item(p, "药渣", cfg.CRAFT_FAIL_DROSS)
    p["alch_exp"] = int(p.get("alch_exp", 0)) + cfg.CRAFT_EXP // 2
    up = _alch_level_up(p)
    res = {"ok": False, "reason": "fail", "rate": rate, "diff": diff,
           "dross": cfg.CRAFT_FAIL_DROSS, "alch_up": up}
    if _danhui > 0:
        res["danhui"] = round(_danhui, 4)
    return res


def _alch_level_up(p) -> str:
    """结算炼药师晋阶（经验够则连续升级，返回晋到的品阶名，未升返回空串）。

    2026-11-19 原著门槛：晋入七品及以上须灵魂境界（实战场次）达标，否则经验
    攒着不升——呼应第1103章「很多炼药师一辈子停留在七品，正是灵魂境界缘故」，
    防止刷低级丹绕过灵魂境界冲满级。此闸对单发/批量两种入口统一生效。
    """
    start = int(p.get("alch", cfg.ALCH_START))
    idx = start
    # 2026-11-23 未入品起步：idx=-1 时 alch_tier 给「未入品/400」，攒够升一品。
    # 绝不能写 ALCHEMIST[idx] —— 负索引会取到九品（门槛 0）导致首炉白跳一品。
    while idx < cfg.ALCH_MAX:
        _name, need = cfg.alch_tier(idx)
        if int(p.get("alch_exp", 0)) < need:
            break
        nxt = idx + 1
        ok, _need_battles = cfg.soul_realm_ok(p, nxt)
        if not ok:
            break   # 经验保留，等灵魂境界达标再升
        p["alch_exp"] = int(p["alch_exp"]) - need
        idx = nxt
        p["alch"] = idx
    name, _need = cfg.alch_tier(idx)
    return name if idx != start else ""


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
    # 兽火单方引火，例外
    if str(rec.get("effect") or "") != "beast_flame" and not _flame_bonus.has_any_flame(p):
        return {"ok": False, "reason": "nofire"}
    if not rec.get("need") and int(rec.get("buy", 0)) > 0:
        return {"ok": False, "reason": "buyonly", "buy": int(rec["buy"])}
    now_ts = time.time() if now_ts is None else now_ts
    cd = craft_cd_left(p, now_ts)
    if cd > 0:
        return {"ok": False, "reason": "cd", "left": cd}

    n = max(1, min(int(n), cfg.CRAFT_BATCH_MAX))
    grade_idx = cfg.alch_grade_idx(rec.get("grade", ""))
    rate, diff = cfg.rate_for_recipe(p, rec, craft_rate_bonus(p))
    exp_factor = cfg.low_grade_exp_factor(p.get("alch", 0), grade_idx)
    mp_per = max(1, int(mp_max(p) * cfg.CRAFT_MP_PER))

    n_done = n_ok = n_fail = n_danwen = 0
    exp = 0
    limited = ""
    _is_high = (grade_idx >= cfg.alch_grade_idx(cfg.PUPPET_HIGH_GRADE_FROM))
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
        # 控火闸计数（口径同单发 craft）：每炉一次，高阶成品另计
        p["craft_count"] = int(p.get("craft_count", 0) or 0) + 1
        if rng() < rate:
            add_item(p, name, 1)
            n_ok += 1
            if _is_high:
                p["high_grade_crafts"] = int(p.get("high_grade_crafts", 0) or 0) + 1
            exp += int(cfg.CRAFT_EXP * exp_factor)
            # 丹纹：每炉成功独立掷，命中额外产带纹丹（纹数 1~3 各自成物）
            # 同单发 craft：升技材料/引火/养魂丹没有带纹版，不许掷（会成死物品）
            if cfg.danwen_allowed(rec) and (_t := dw.roll(rec.get("grade", ""))):
                add_item(p, cfg.danwen_name(name, _t), 1)
                n_danwen += 1
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
            "n_danwen": n_danwen,
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


def use_item(p, name, used_counts=None, rng=random.random, _skip_take=False) -> dict:
    """服丹/炼化。effect 分支与 msg 前缀沿用旧口径，既有测试逐字不变。
    used_counts=破境丹一阶限次；rng 可注入；_skip_take 内部递归用（带纹丹）。"""
    # 丹纹带纹丹：先验有丹→跑基础药效→成功才扣（反序会白吃稀有丹，同养魂涎吞丹洞）
    if cfg.is_danwen_name(name):
        if count_item(p, name) < 1:
            return {"ok": False, "reason": "none"}
        res = use_item(p, cfg.base_of_danwen(name), used_counts=used_counts,
                       rng=rng, _skip_take=True)
        if not res.get("ok"):
            return res
        take_item(p, name, 1)
        g = dw.grant(p, name, rng=rng, add_fn=lambda k, m, mi, s: tb.add(p, k, m, mi, s))
        res["msg"] += "\n" + txt.danwen_grant(g["name"], g["desc"])
        res["danwen"] = g
        return res
    rec = cfg.RECIPE_BY_NAME.get(name)
    is_material = False
    if not rec:
        rec = cfg.MATERIAL_BY_NAME.get(name)
        is_material = bool(rec)
    if not rec:
        return {"ok": False, "reason": "nodrug"}
    # P3 养魂（第757章 N08）：养魂涎是**渡进傀儡体内**的，不是口服丹。
    # 🚨 必须拦在 take_item **之前**：旧兜底分支会先把丹扣掉再返回 unknown，
    #    玩家看到的就是「丹没了，什么都没发生」—— 这正是要堵的洞。
    if str(rec.get("effect") or "") == "puppet_soul":
        return {"ok": False, "reason": "need_puppet", "pill": name}
    # 2026-11-23 修 P0 吞丹：焚血散是升斗技的材料，旧版末尾才 notuse 而 take_item
    # 在最前 —— 玩家白丢一枚。与养魂涎同理，必须拦在扣物前。
    if str(rec.get("effect") or "") == "skill":
        return {"ok": False, "reason": "notuse", "pill": name}
    # 兽火单方：服后获得对应兽火（判定在 flame_drop，扣丹/写库在此，防吞丹）。
    _bp = _fdrop.beast_pill_result(p, rec)
    if _bp is not None:
        if not _bp["ok"]:
            return {"ok": False, "reason": _bp["reason"], "pill": name}
        if not take_item(p, name, 1):
            return {"ok": False, "reason": "none"}
        p.setdefault("flames", {})[_bp["flame"]] = 1
        return {"ok": True, "msg": "炼化【%s】，兽火【%s】已认你为主" % (name, _bp["flame"]),
                "flame": _bp["flame"], "kind": "beast"}
    st = stats(p)
    # 2026-11-23：气血/斗气已满时不扣物（旧版先扣再算，白丢一枚只回「+0」）。
    eff = str(rec.get("effect") or "")
    if (eff == "hp" and int(p.get("hp", 0)) >= int(st["体力"])) or \
            (eff == "mp" and int(p.get("mp", 0)) >= mp_max(p)):
        return {"ok": False, "reason": "full", "pill": name}
    if not _skip_take and not take_item(p, name, 1):
        return {"ok": False, "reason": "none"}

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
        # 原著第 315/321 章两枚破境丹共用这一套结算（三纹青灵丹 break_boundary=True
        # +star 0.30+限 1；紫心破障丹 break_boundary=False+star 1.0+限 2）。三档都
        # 不空手：①界限前一级且可破界 → 顶开壁障；②目标阶内 → 限次掷升星；
        # ③其余境界 → 药不对症转修为。
        to_realm = int(rec.get("to_realm") or 1)
        max_per = max(1, int(rec.get("max_per_realm") or 1))
        lv = int(p.get("lv", 1))
        ri = cfg.realm_index(lv)
        realm_name = cfg.REALM_NAMES[min(to_realm, len(cfg.REALM_NAMES) - 1)]
        if rec.get("break_boundary") and ri == to_realm - 1 and lv < cfg.MAX_LV \
                and cfg.realm_index(lv + 1) == to_realm:
            _advance_one(p)
            return {"ok": True, "breakthrough": True,
                    "msg": lead + txt.pill_break(realm_name,
                                                 cfg.full_realm(int(p["lv"])))}
        if ri == to_realm:
            used = int((used_counts or {}).get(ri, 0))
            if used_counts is not None and used >= max_per:
                # 丹药已被扣掉（take_item 在最前面），如实告知「白服」。
                return {"ok": False, "reason": "resisted",
                        "msg": lead + txt.pill_resist(realm_name, max_per)}
            if rng() < float(rec.get("star_chance", 0.0)):
                # 2026-11-23 修 P1-4：九星位不得跨大境界。旧版 _advance_one 只做
                # lv+1，斗灵丹 lv54→55 直接晋斗皇，绕过了九星突破 BOSS 战。
                if cfg.realm_index(lv + 1) != ri:
                    p["exp"] = int(p.get("exp", 0)) + val
                    p["pill_exp"] = int(p.get("pill_exp", 0)) + val
                    return {"ok": True, "mark_used": ri, "msg": lead + txt.pill_gate(val)}
                _advance_one(p)
                return {"ok": True, "star_up": True, "mark_used": ri,
                        "msg": lead + txt.pill_star(cfg.full_realm(int(p["lv"])))}
            p["exp"] = int(p.get("exp", 0)) + val
            p["pill_exp"] = int(p.get("pill_exp", 0)) + val
            return {"ok": True, "mark_used": ri,
                    "msg": lead + txt.pill_no_star(val)}
        p["exp"] = int(p.get("exp", 0)) + val
        p["pill_exp"] = int(p.get("pill_exp", 0)) + val
        return {"ok": True, "msg": lead + txt.pill_wrong_realm(realm_name, val)}
    if eff == "luck":
        p["luck"] = min(9999, int(p.get("luck", 0)) + val)
        return {"ok": True, "msg": lead + f"机缘 +{val}（现 {p['luck']}）"}
    if eff in cfg.PCT_BONUS:
        # 永久百分比（闪避/暴击/命中/暴伤）共用一处；键名与量纲见 cfg.PCT_BONUS。
        k, label = cfg.PCT_BONUS[eff]
        b = p.setdefault("bonus", {})
        b[k] = int(b.get(k, 0)) + val
        return {"ok": True, "msg": lead + f"{label} +{val}%"}
    if eff == "stat":
        b = p.setdefault("bonus", {})
        for k in ("力量", "敏捷", "斗气", "防御", "体力"):
            b[k] = int(b.get(k, 0)) + val
        _refill(p)
        return {"ok": True, "msg": lead + f"全属性 +{val}"}
    if eff == "ascend":
        # 帝品雏丹：一缕本源帝气，下一次 102→103 必成（见 level_up）。
        p["ascend"] = int(p.get("ascend", 0)) + 1
        return {"ok": True, "msg": lead + "本源帝气入体——斗帝壁障已可叩开"}
    if eff == "temp_buff":
        return {"ok": True, "msg": lead + tb.eat(p, rec)}
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


#: 玩家手打技名时最常见的同音/形近混淆（2026-11-25 用户「学习 帝印诀」被判 noskill）。
#: 只做**改写等价**，不做编辑距离/拼音猜测——宁可提示「你是不是想学 X」，也不许瞎猜一门。
_NAME_ALIASES = str.maketrans({"诀": "决", "绝": "决", "卬": "印", "坡": "破"})


def _name_key(s: str) -> str:
    """技名归一化 key：去空白 + 同音字折叠（用于精确/唯一近似匹配）。"""
    return str(s or "").strip().translate(_NAME_ALIASES).replace(" ", "")


def resolve_skill_name(p, name: str, names: list[str] | None = None) -> str:
    """把玩家手打的技名解析成**唯一**正式名（解不出返回空串）。

    三级：① 原样精确 → ② 同音字折叠后精确（必须唯一）→ ③ 互相包含（必须唯一）。
    任何一级出现多义就放弃（返回空串），由调用方提示候选，绝不替玩家拍板。

    :param names: 候选名清单；缺省取「本宗可领悟池」（已解锁、不含异火技）。
                  升级/上阵这类要覆盖已拥有技的场合，把 ``learned_names`` 传进来。
    """
    if names is None:
        pool = [e["name"] for e in sk_mod.learnable(p) if e.get("origin") != "异火"]
    else:
        pool = [str(n) for n in names if n]
    raw = str(name or "").strip()
    if not raw:
        return ""
    if raw in pool:
        return raw
    key = _name_key(raw)
    if not key:
        return ""
    same = [n for n in pool if _name_key(n) == key]
    if len(same) == 1:
        return same[0]
    if same:
        return ""
    near = [n for n in pool if key in _name_key(n) or _name_key(n) in key]
    return near[0] if len(near) == 1 else ""


def learn_named(p, name: str) -> dict:
    """定向领悟一门**指定的**斗技（用户 2026-11-16「学习 风灵分形剑」诉求）。

    与 insight() 完全同一套门槛口径（解锁等级 / 战力 / 金币 / 已领悟去重），
    区别只是「随机取一门」→「取玩家点名的那一门」。老档同样惰性迁移。

    失败 reason：noskill（本宗没有这门/异火技）、learned（已会）、
    level（等级不够）、power（战力不足）、gold（金币不足）。
    ``noskill`` 时附带 ``suggest``（近似技名候选），供命令层回「你是不是想学 X」。
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
        # 2026-11-25：同音字/漏字是玩家最常见的错法（「帝印诀」≠「帝印决」），
        # 唯一可解就用正式名，不拿「没有这门斗技」糊玩家脸。
        canon = resolve_skill_name(p, name)
        if canon:
            hit = next((e for e in pool if e["name"] == canon), None)
            if hit is not None:
                name = canon
    if hit is None:
        # 本宗有这门但等级未到 → 明确告诉差几级；完全没有 → noskill
        any_tier = next((e for e in sk_mod.entries_for(p)
                         if e["name"] == name and e.get("origin") != "异火"), None)
        if any_tier:
            return {"ok": False, "reason": "level", "name": name,
                    "need_lv": int(any_tier["unlock"]), "lv": int(p.get("lv", 1)),
                    "migrated": migrated}
        chars = set(_name_key(name))
        sug = sorted(
            (e["name"] for e in pool if len(set(_name_key(e["name"])) & chars) >= 2),
            key=lambda n: -len(set(_name_key(n)) & chars),
        )[:3]
        return {"ok": False, "reason": "noskill", "name": name,
                "suggest": sug, "migrated": migrated}
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
    if not _flame_bonus.has_any_flame(p):
        return {"ok": False, "reason": "nofire"}  # 空火无火可养
    # 可养五火 + B 线异火 + 新生异火 + 兽火，共用一张查表口径。
    fl = (cfg.FLAME_BY_NAME.get(name) or getattr(cfg, "BONUS_FLAME_BY_NAME", {}).get(name)
          or getattr(cfg, "OTHER_FLAME_BY_NAME", {}).get(name))
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
    # 2026-11-23 修速通：min_lv 旧版只过滤「炼药名录」，商店不认 —— lv1 拿 1 万
    # 起始金币买 66 枚破障丹跳 23 级。丹药品阶本就是门槛（低阶修士受不住）。
    _need_lv = cfg.recipe_min_lv(name)
    if _need_lv > int(p.get("lv", 1)):
        return {"ok": False, "reason": "lv", "need_lv": _need_lv}
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


def _weapon_task_value(p) -> int:
    """武器类任务的**状态值**：当前强化等级。

    2026-11-20 真 bug（用户反馈「主线卡在升级玄重尺，我其实早就升级过，已经满级」）：
    进度只由 ``advance_task('weapon')`` 事件累加，而这条事件只在**真的点强化时**触发。
    玩家在轮到该任务之前就把武器练满，之后满级再也强化不了 → 进度永远 0 → 主线永久卡死。

    「强化玄重尺至 5 级」是**状态要求**（到没到 5 级），不是次数要求；
    只看事件计数等于要求玩家「先读到这条任务、再去强化」，顺序错了就死。
    """
    return max(0, int(p.get("weapon", 0) or 0))


def main_value(p) -> int:
    """当前主线的**有效进度**：事件计数与状态值取大（渲染与提交都用它）。"""
    t = current_main(p)
    if not t:
        return 0
    n = int(p.get("main_progress", 0))
    if t.get("kind") == "weapon":
        n = max(n, _weapon_task_value(p))
    return n


def task_value(p) -> int:
    """当前支线的有效进度（口径同 ``main_value``）。"""
    t = p.get("task")
    if not t:
        return 0
    n = int(t.get("progress", 0))
    if t.get("kind") == "weapon":
        n = max(n, _weapon_task_value(p))
    return n


def task_accept(p, rng=random.random) -> dict:
    if p.get("task"):
        return {"ok": False, "reason": "busy", "task": p["task"]}
    # 已经达成的武器类支线不再发（否则「接一次交一次」就是白刷金币）。
    pool = [t for t in cfg.SIDE_TASK_POOL
            if not (t.get("kind") == "weapon"
                    and _weapon_task_value(p) >= int(t.get("target", 1)))]
    t = dict(rng_choice(pool or cfg.SIDE_TASK_POOL, rng))
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
    if task_value(p) < int(t.get("target", 1)):
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
    if main_value(p) < int(t["target"]):
        return {"ok": False, "reason": "unfinished", "task": t,
                "progress": main_value(p)}
    # 2026-11-25 用户拍板 G1=A：章节推进要求等级到位（`require_lv` = 本章区间上限）。
    # 为什么需要：主线任务本身很轻（杀 3 只 / 采 5 株），lv1 也能做完；若直接放行，
    # 玩家会带着 lv1 进第 2 章打 lv10 的怪（必死）。等级门槛把「练够」写成硬条件，
    # 与「低章怪经验被钳制」一起构成「必须推主线才能变强、必须练够才能推主线」闭环。
    _need_lv = int(t.get("require_lv") or 0)
    if _need_lv and int(p.get("lv", 1)) < _need_lv:
        return {"ok": False, "reason": "lv", "task": t, "need_lv": _need_lv,
                "lv": int(p.get("lv", 1))}
    rw = t["reward"]
    p["gold"] = int(p.get("gold", 0)) + int(rw.get("gold", 0))
    p["exp"] = int(p.get("exp", 0)) + int(rw.get("exp", 0))
    p["luck"] = min(9999, int(p.get("luck", 0)) + int(rw.get("luck", 0)))
    p["main_idx"] = int(p.get("main_idx", 0)) + 1
    # 章节与任务**同一笔**推进：绝不出现「给了奖励没推章」或反之。
    # 口径唯一：story_chapter = main_idx + 1（夹在合法章号内）。
    p["story_chapter"] = max(1, min(cfg.CHAPTER_COUNT, int(p["main_idx"]) + 1))
    p["main_progress"] = 0
    return {"ok": True, "task": t, "reward": rw, "next": current_main(p),
            "story_chapter": int(p["story_chapter"])}


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
