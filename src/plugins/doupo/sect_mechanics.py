# -*- coding: utf-8 -*-
"""宗门特色机制的纯函数层（魂殿邪修 + P0 古族/丹塔/蛇人）。

2026-11-24 从 core.py 抽出：core.py 逼近 200KB 单文件红线（HEAD 200727B），
把宗门机制的纯判定/计算函数集中到这里，core.py 只留 re-export 与单点钩子。
本模块只依赖 config / flame_bonus，**不 import core**，无循环依赖。

设计单一真源：
  * 常量全在 config.py（DEVOUR_*/GUZU_*/CLAN_TIER_PCT/DANTA_*/DANHUI_*/SHEREN_*/SEAL_*）
  * 本文件是这些常量唯一的"玩家 → 数值"解释函数；反解、实战、展示共用。
  * 系数 0 是合法的"关闭机制"，显式判 0，不用 `or` 兜底。
"""
from . import config as cfg
from . import flame_bonus as _flame_bonus


# ---------------------------------------------------------------------------
# 魂殿邪修 · 吞噬炼化被动
# ---------------------------------------------------------------------------
def is_devour_sect(p) -> bool:
    """玩家是否已入魂殿（邪修身份）。入教锁死转宗、吞噬被动都认这一处口径。"""
    return str((p or {}).get("faction") or "") == cfg.DEVOUR_SECT


def devour_amount(monster_exp: int) -> int:
    """吞噬修为 = 被斩怪 exp × DEVOUR_RATE，单点定义（设计文档 U02）。

    DEVOUR_RATE=0 是合法值（等价关闭吞噬），显式判 0 不用 or；结果向下取整，
    负数按 0（怪 exp 恒正，属防御式）。
    """
    rate = float(cfg.DEVOUR_RATE)
    if rate <= 0:
        return 0
    return max(0, int(int(monster_exp or 0) * rate))


def devour_of(p, mon, tier_fn=None) -> int:
    """本场吞噬结算：仅魂殿弟子斩 boss/elite 档怪才吞，否则 0。

    ``tier_fn`` 默认用本模块 mon_tier（塔/周本/野外/遗迹 kind 字面全收敛到它）；
    core 可注入同名函数保持旧调用。只返回额外修为，由 core.fight 写入存档。
    """
    if tier_fn is None:
        tier_fn = mon_tier
    if not is_devour_sect(p) or tier_fn(mon.get("kind")) not in ("boss", "elite"):
        return 0
    return devour_amount(int(mon.get("exp") or 0))


# ---------------------------------------------------------------------------
# 古族 · 族纹（P0）
# ---------------------------------------------------------------------------
def is_guzu_sect(p) -> bool:
    """玩家是否已入古族（族纹被动持有者）。"""
    return str((p or {}).get("faction") or "") == cfg.GUZU_SECT


def clan_tier_pct(p) -> float:
    """古族族纹的六维比例加成（0.03=+3%），按等级从 CLAN_TIER_PCT 取最高达标档。

    单一真源是 cfg.CLAN_TIER_PCT；非古族恒 0.0。系数为 0 是合法值，不加 `or`。
    """
    if not is_guzu_sect(p):
        return 0.0
    lv = int((p or {}).get("lv", 1))
    pct = 0.0
    for gate, value in cfg.CLAN_TIER_PCT:
        if lv >= int(gate):
            pct = float(value)
    return pct


# ---------------------------------------------------------------------------
# 丹塔 · 丹会（P0）
# ---------------------------------------------------------------------------
def is_danta_sect(p) -> bool:
    """玩家是否已入丹塔（丹会加成持有者）。"""
    return str((p or {}).get("faction") or "") == cfg.DANTA_SECT


def danhui_bonus(p) -> float:
    """丹塔丹会炼丹成功率加成 = 每品系数 × (炼药师品阶+1)；未入品/非丹塔为 0。

    一品(0)=1×系数、四品(3)=4×系数、七品(6)=7×系数。系数 0 是合法关闭值。
    与探针 probe_sect_balance.py / 设计文档 §10 三档表同口径（U02）。
    """
    if not is_danta_sect(p):
        return 0.0
    alch = int((p or {}).get("alch", 0))
    if alch < 0:
        return 0.0   # 未入品（ALCH_NONE=-1）不享有丹会加成
    return float(cfg.DANHUI_BONUS_PER_TIER) * (alch + 1)


def craft_rate_bonus(p) -> float:
    """炼丹成功率总加成（B 线异火 + 丹塔丹会），craft/craft_batch 两入口共用的单一真源。

    两处 craft_rate_for 调用都读这里，避免"改一处漏一处"；CAP 仍由 craft_rate_for
    统一收口（CRAFT_RATE_CAP=0.95），本函数不重复封顶。
    """
    return float(_flame_bonus.alchemy_rate_bonus(p)) + danhui_bonus(p)


# ---------------------------------------------------------------------------
# 蛇人族 · 封印咒（P0）
# ---------------------------------------------------------------------------
def is_sheren_sect(p) -> bool:
    """玩家是否已入蛇人族（封印咒被动持有者）。"""
    return str((p or {}).get("faction") or "") == cfg.SHEREN_SECT


def seal_atk_rate(p) -> float:
    """蛇人封印咒对怪物的降攻比例（0.15=-15%）；非蛇人恒 0.0。"""
    if not is_sheren_sect(p):
        return 0.0
    return float(cfg.SEAL_ATK_RATE)


def apply_seal(m_fx: dict, p) -> float:
    """把蛇人封印咒挂到怪物 effect 状态，返回降攻率（0=非蛇人，不挂键）。

    走 skill_effects 现成的 debuff_atk 原子 + target_atk_mult 通道（怪出手处读），
    不新增 effect 类型、不改结算顺序。持续回合覆盖 HARD_ROUND_CAP → 整场常驻。
    """
    rate = seal_atk_rate(p)
    if rate > 0:
        m_fx["debuff_atk"] = min(0.9, rate)
        m_fx["debuff_atk_d"] = int(cfg.HARD_ROUND_CAP) + 1
    return rate


# ---------------------------------------------------------------------------
# 太虚古龙 · 空间穿梭（P1）
# ---------------------------------------------------------------------------
def is_taixu_sect(p) -> bool:
    """玩家是否已入太虚古龙族（空间穿梭被动持有者）。"""
    return str((p or {}).get("faction") or "") == cfg.TAIXU_SECT


def taixu_evade_pts(p) -> float:
    """太虚空间穿梭的自然闪避加成百分点，按等级取 TAIXU_EVADE_TIERS 最高达标档。

    单一真源 cfg.TAIXU_EVADE_TIERS；非太虚恒 0.0。设计文档 §10 P04：
    加点进 dodge_rate 的**自然 base**（像净莲妖火），被自然 80 硬顶收口。
    """
    if not is_taixu_sect(p):
        return 0.0
    lv = int((p or {}).get("lv", 1))
    pts = 0.0
    for gate, value in cfg.TAIXU_EVADE_TIERS:
        if lv >= int(gate):
            pts = float(value)
    return pts


def taixu_natural_dodge(p) -> float:
    """太虚玩家「未加本被动、未加丹药」的自然闪避 base（等级 + 净莲妖火）。

    与 core.dodge_rate 的自然部分同构（``HERO_DODGE + (敏捷+20)//200 + 净莲妖火×10``），
    但这里用 sect_mechanics 自己的 c_stat 重算，避免 import core 循环。
    """
    agi = c_stat(cfg.HERO_BASE["敏捷"], int((p or {}).get("lv", 1)))
    lotus = 10 * int(((p or {}).get("flames") or {}).get("净莲妖火", 0))
    return float(cfg.HERO_DODGE + (agi + 20) // 200 + lotus)


def taixu_overflow_hit(p, dodge_cap: float = 80.0) -> float:
    """闪避触顶后溢出点数转命中百分点（满级替代收益，防空转假机制）。

    太虚加点 ``pts`` 里有 ``min(cap, base+pts) - min(cap, base)`` 被自然闪避吃到，
    剩余 ``pts - 吃到`` 即溢出，转为命中百分点（core.hit_rate 里 min HIT_CAP 收口）。
    自然 base 用 taixu_natural_dodge 自算（不 import core）。非太虚 / 未触顶恒 0.0。
    """
    pts = taixu_evade_pts(p)
    if pts <= 0:
        return 0.0
    base = taixu_natural_dodge(p)
    eaten = min(dodge_cap, base + pts) - min(dodge_cap, base)
    return max(0.0, pts - max(0.0, eaten))


# ---------------------------------------------------------------------------
# 毒宗 · 仿毒体（P1）
# ---------------------------------------------------------------------------
def is_duzong_sect(p) -> bool:
    """玩家是否已入毒宗（仿毒体被动持有者）。"""
    return str((p or {}).get("faction") or "") == cfg.VENOM_SECT


def venom_lifesteal(p, root_fn=None) -> float:
    """毒宗仿毒体吸血比例 = VENOM_LIFESTEAL × 根基系数（丹药修为越多吸血越低）。

    非毒宗恒 0.0。root_fn 默认 config.root_factor，可注入便于测试。系数 0 合法关闭。
    """
    if not is_duzong_sect(p):
        return 0.0
    rf = root_fn if root_fn is not None else cfg.root_factor
    return float(cfg.VENOM_LIFESTEAL) * float(rf(p))


# ---------------------------------------------------------------------------
# 天府联盟 · 全属性（P1）
# ---------------------------------------------------------------------------
def is_tianfu_sect(p) -> bool:
    """玩家是否已入天府联盟（全属性 +4% 被动持有者，不锁宗）。"""
    return str((p or {}).get("faction") or "") == cfg.TIANFU_SECT


def tianfu_allstat_pct(p) -> float:
    """天府全属性比例加成（0.04=+4%）；非天府恒 0.0。系数 0 合法关闭。"""
    if not is_tianfu_sect(p):
        return 0.0
    return float(cfg.TIANFU_ALLSTAT)


def sect_allstat_pct(p) -> float:
    """六维比例加成的宗门汇总（古族族纹 + 天府全属性），供 core.stats 单点读。

    一玩家只属一派，两派不会同时生效；合并成一个入口让 core 只改一个 token。
    """
    return clan_tier_pct(p) + tianfu_allstat_pct(p)


# ---------------------------------------------------------------------------
# 怪物战斗档位（从 core.py 外迁；core re-export 保持 core.mon_tier 入口）
# ---------------------------------------------------------------------------
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


# ---------------------------------------------------------------------------
# 战斗减伤（从 core.py 外迁，仅为解 core.py 200KB 体积；core re-export 保持入口）
# ---------------------------------------------------------------------------
def mitigation(defence: int) -> float:
    """减伤系数：防御 = ``cfg.MITIGATION_W`` 时减半。半值点必须与反解护甲同源。"""
    w = float(cfg.MITIGATION_W)
    d = max(0, int(defence))
    return w / (w + d)


# ---------------------------------------------------------------------------
# 等级成长纯函数（从 core.py 外迁，仅为解 core.py 200KB 体积；core re-export 保持入口）
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

def level_dodge(lv: int) -> float:
    """**不靠任何装备/异火/丹药**、纯等级带来的闪避百分点（上限同 dodge_rate）。

    两个用途，同一条曲线，保证「同等级同身法」：
      * 怪物眼力的基准（``monster_panel`` 用它抵掉玩家白拿的身法）；
      * 让「等级涨上去自然就闪」这件事可被度量，而不是散在 dodge_rate 里。
    """
    agi = c_stat(cfg.HERO_BASE["敏捷"], int(lv))
    return float(min(80, cfg.HERO_DODGE + (agi + 20) // 200))



