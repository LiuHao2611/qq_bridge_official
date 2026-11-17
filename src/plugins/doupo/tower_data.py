# -*- coding: utf-8 -*-
"""爬塔副本数据层（2026-11-17）。

设计见 docs/斗破爬塔副本_设计方案.md。用户拍板：
  ① 无限层冲榜（爬到死记最高层、每10层Boss）；
  ② 武器宝箱极低概率出、无保底；
  ③ 三选一复用现有属性/effect，不新增 effect 类型。

本文件只放**纯数据与公式**，零副作用、不 import nonebot / core，
可被裸 Python 直接加载做门禁。
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# 楼层类型与难度曲线
# ---------------------------------------------------------------------------
BOSS_EVERY = 10     # 每 10 层一个 Boss
ELITE_EVERY = 5     # 每 5 层一个精英（与 Boss 重合时以 Boss 为准）

#: hard 公式参数（hard 越小越难，与 core.monster_scaled 同口径）
HARD_BASE = 0.93
HARD_PER_FLOOR = 0.015     # 周期内每层递减
HARD_PER_CYCLE = 0.01      # 每跨一个 10 层周期再减，封顶 HARD_CYCLE_CAP
HARD_CYCLE_CAP = 0.18
HARD_ELITE_BONUS = -0.02   # 精英层额外难度
HARD_BOSS_BONUS = -0.05    # Boss 层额外难度
HARD_MIN = 0.62            # 硬下限：保证无限层也不会 100% 必死
HARD_MAX = 1.00

#: 高层怪登记等级提升（受 monster_scaled 的 delta ±20% 钳位）
LV_PER_CYCLE = 2

#: 层间回血（每层胜利回一点，避免无限层瞬间耗尽）。
#: 塔怪反解成「约4回合互杀」，与普通遭遇战同险；连爬必须留一点续航。
#: ⚠ 2026-11-17 用户反馈「气血怎么没降低」：原值 0.35 会在单层伤害 <35% 上限时
#: 把伤害**全部盖掉**（实测 lv60 连续 8 层满血刷塔）。真正的续航来源应是三选一
#: 里的回血类战利品，而不是白送。现降到 0.12，并受 FLOOR_REST_DMG_RATIO 约束。
FLOOR_REST_HEAL = 0.12

#: 歇息最多只能补回**本层实际损失**的这个比例。
#: 这是「气血随层数单调不增」的硬保证：无伤则不回，受伤最多补一半。
#: 门禁 tests/test_doupo_tower.py::test_hp_monotonic_across_floors 守着。
FLOOR_REST_DMG_RATIO = 0.5

#: 塔怪攻击缩放：爬塔是**持久消耗战**，不是单场决斗。
#: monster_scaled 把怪 atk 反解成「4~5回合杀玩家」，单场合理，但连爬会在
#: 第1~2层就耗尽气血。塔怪统一降攻，让玩家能连续爬多层；难度递增交给下方的
#: floor_difficulty（随层数持续叠加）与词缀，而非靠单场一击必杀。
#: 2026-11-17：回血从「白送 35% 上限」改成「最多补回本层损失一半」后，单层净掉血
#: 变成约一半伤害，原 0.42 会让 lv70 只能爬 6 层。按实扫曲线降到 0.18，
#: 恢复成 lv10~30 中位 7~8 层 / lv50 9 / lv70 13~14 / lv100 19，终局剩 15~20% 气血。
TOWER_ATK_SCALE = 0.18

#: 随层数持续叠加的难度（无限层必须有终点，不能只靠周期 hard 的锯齿）。
#: 玩家属性在一轮内不增长，若难度只按 10 层周期循环，强者能无限爬。
#: 用**二次增长**：前期平缓（让玩家见得到第5层精英/第10层Boss），后期陡峭
#: （无限层必有终点）。倍率 = 1 + k·((N-1)/10)^2：
#: 第 5 层≈1.04×、第10层≈1.15×、第20层≈1.6×、第40层≈3.4×、第60层≈6.6×。
DIFF_QUAD = 0.15       # 每 10 层的二次难度增量
DIFF_SCALE_ATK = 0.9   # 攻击增长略慢于血量（避免后期一击必杀，保留可学习性）


def floor_difficulty(n: int) -> dict:
    """第 n 层的持续难度倍率（叠加在 hard/词缀之上）。"""
    n = max(1, int(n))
    q = ((n - 1) / 10.0) ** 2
    return {"hp_mult": 1.0 + DIFF_QUAD * q,
            "atk_mult": 1.0 + DIFF_QUAD * DIFF_SCALE_ATK * q}

#: 普通层相对普通遭遇战略微宽松（hard 更大=更容易），让前期可连续爬；
#: 精英/Boss 再按 HARD_ELITE_BONUS / HARD_BOSS_BONUS 收紧。
HARD_NORMAL_EASE = 0.04


def floor_kind(n: int) -> str:
    """'boss' / 'elite' / 'normal'。Boss 优先于精英。"""
    n = int(n)
    if n <= 0:
        return "normal"
    if n % BOSS_EVERY == 0:
        return "boss"
    if n % ELITE_EVERY == 0:
        return "elite"
    return "normal"


def floor_hard(n: int) -> float:
    """第 n 层的物种 hard 系数；单调趋势 + 周期锯齿 + 下限钳位。"""
    n = max(1, int(n))
    c = (n - 1) // BOSS_EVERY
    p = (n - 1) % BOSS_EVERY
    v = HARD_BASE - HARD_PER_FLOOR * p - min(HARD_CYCLE_CAP, c * HARD_PER_CYCLE)
    k = floor_kind(n)
    if k == "boss":
        v += HARD_BOSS_BONUS
    elif k == "elite":
        v += HARD_ELITE_BONUS
    else:
        v += HARD_NORMAL_EASE
    return round(max(HARD_MIN, min(HARD_MAX, v)), 4)


def floor_monster_lv(n: int, player_lv: int) -> int:
    """塔层怪的登记等级：随周期上移，钳在不至于跨大境界的窗口内。"""
    c = (n - 1) // BOSS_EVERY
    return int(player_lv) + (c - 1) * LV_PER_CYCLE


# ---------------------------------------------------------------------------
# Boss / 精英战斗词缀（全部用现有 effect，不改战斗内核）
# ---------------------------------------------------------------------------
#: 每条词缀是一个 effect 字典，直接塞进虚拟 species 的临时标记，
#: 由 core.tower_floor 在战斗开始时注入 fx（本轮有效、不写存档）。
AFFIXES: dict[str, dict] = {
    "狂暴": {"t": "empower", "v": 0.20, "d": 99, "kind": "狂暴"},
    "再生": {"t": "lifesteal", "v": 0.15, "d": 99, "kind": "再生"},
    "反伤": {"t": "reflect", "v": 0.18, "d": 99, "kind": "反伤"},
    "禁疗": {"t": "debuff_heal", "v": 0.50, "d": 99, "kind": "禁疗"},
    "破甲": {"t": "pierce", "v": 0.20, "d": 99, "kind": "破甲"},
    "吸攻": {"t": "debuff_atk", "v": 0.15, "d": 99, "kind": "吸攻"},
}
AFFIX_KEYS = tuple(AFFIXES)

#: Boss 层名称（原著反派，按层号确定性轮转）
BOSS_NAMES = (
    "地魔老鬼", "血刀圣者", "摘星老鬼", "魂魔老人", "狮天",
    "天蛇老怪", "蝎山", "穆蛇", "范痨", "韩枫",
)


def boss_name(n: int) -> str:
    """第 n 个 Boss 层的名字（n 必须是 Boss 层）。"""
    idx = (int(n) // BOSS_EVERY - 1) % len(BOSS_NAMES)
    return BOSS_NAMES[idx]


def affixes_for(n: int, rng) -> list[str]:
    """按层号派生词缀：精英 1 个、Boss 2 个，同种子可复现。"""
    k = floor_kind(n)
    if k == "normal":
        return []
    count = 2 if k == "boss" else 1
    # 用确定性洗牌避免同一层反复刷出不同词缀
    keys = list(AFFIX_KEYS)
    out = []
    for _ in range(count):
        i = min(len(keys) - 1, int(rng() * len(keys)))
        out.append(keys.pop(i))
    return out


# ---------------------------------------------------------------------------
# 三选一战利品（复用现有属性 / effect，用户拍板③）
# ---------------------------------------------------------------------------
#: 每条 loot: {kind, key, v, label, desc}
#:   kind = "hp" 回血 / "mp" 回气 / "attr" 属性 / "effect" 战斗效果
LOOT_POOL: tuple[dict, ...] = (
    {"kind": "hp",   "key": "回春散",   "v": 0.35, "label": "回春散",   "desc": "立刻恢复 35% 气血"},
    {"kind": "mp",   "key": "回气丹",   "v": 1.00, "label": "回气丹",   "desc": "斗气立刻回满"},
    {"kind": "attr", "key": "力量",     "v": 0.15, "label": "力量+15%", "desc": "本轮力量提升 15%"},
    {"kind": "attr", "key": "敏捷",     "v": 0.15, "label": "敏捷+15%", "desc": "本轮敏捷提升 15%（闪避/暴击）"},
    {"kind": "attr", "key": "斗气",     "v": 0.15, "label": "斗气+15%", "desc": "本轮斗气提升 15%"},
    {"kind": "attr", "key": "防御",     "v": 0.15, "label": "防御+15%", "desc": "本轮防御提升 15%（减伤）"},
    {"kind": "effect", "key": "chain",     "v": 0.15, "label": "连击+15%", "desc": "本轮斗技附带连击效果"},
    {"kind": "effect", "key": "pierce",    "v": 0.15, "label": "破防+15%", "desc": "本轮斗技附带破防效果"},
    {"kind": "effect", "key": "lifesteal", "v": 0.12, "label": "吸血+12%", "desc": "本轮斗技附带吸血效果"},
    {"kind": "effect", "key": "crit",      "v": 0.10, "label": "暴击+10%", "desc": "本轮暴击率提升 10%"},
)
LOOT_CHOICES = 3

#: effect 类 loot → skill_effects 的 t（crit 走 bonus 而非 effect）
LOOT_EFFECT_T = {
    "chain": "chain", "pierce": "pierce", "lifesteal": "lifesteal", "crit": "crit",
}


def roll_loot_choices(rng) -> list[dict]:
    """抽 3 个不重复的战利品选项（同种子确定性）。"""
    pool = list(LOOT_POOL)
    out = []
    for _ in range(min(LOOT_CHOICES, len(pool))):
        i = min(len(pool) - 1, int(rng() * len(pool)))
        out.append(pool.pop(i))
    return out


# ---------------------------------------------------------------------------
# 宝箱与武器掉落（用户拍板②：极低概率、无保底）
# ---------------------------------------------------------------------------
CHEST_RATE_NORMAL = 0.25
CHEST_RATE_ELITE = 0.40
CHEST_RATE_BOSS = 1.00       # Boss 层宝箱必开
WEAPON_DROP_IN_CHEST = 0.08  # 宝箱内出武器的概率（普通层期望 ≈ 0.25*0.08=2%/层）

#: 武器按层数解锁：min_level = 最早可能在第几层的宝箱里出现
#: 层号越高解锁越强的武器（避免第1层掉血斧）
WEAPON_MIN_FLOOR: dict[str, int] = {
    "玄重尺": 1, "淡青色长剑": 1, "雁翎剑": 2, "银色长枪": 2,
    "金环锯": 3, "裂山枪": 4, "寒铁棍": 4, "七彩蛇剑": 5,
    "蓝棍": 6, "鬼头大刀": 7, "昊雷锤": 8, "金色大刀": 9,
    "蛇杖": 10, "血斧": 12,
}


def chest_rate(n: int) -> float:
    k = floor_kind(n)
    return {"boss": CHEST_RATE_BOSS, "elite": CHEST_RATE_ELITE,
            "normal": CHEST_RATE_NORMAL}[k]


def weapon_pool_for(n: int, owned: set[str]) -> list[str]:
    """第 n 层可掉、且玩家未拥有的武器（无保底：全收集后返回空）。"""
    return [w for w, mf in WEAPON_MIN_FLOOR.items()
            if int(n) >= mf and w not in owned]


# ---------------------------------------------------------------------------
# 结算奖励
# ---------------------------------------------------------------------------
REWARD_GOLD_PER_FLOOR = 120
REWARD_EXP_PER_FLOOR = 90


def clear_reward(floors: int) -> dict:
    """从 0 连续爬到 floors 层的**累计**金币/经验奖励（非线性）。

    ⚠ 电梯分层重打时必须用 ``incremental_reward(to_floor, paid_floor)`` 取差值，
    不能把每轮的 clear_reward 相加——那会重复发低层奖励（2026-11-17 用户硬要求
    「不会重复获得之前挑战过层数的奖励」）。
    """
    f = max(0, int(floors))
    return {
        "gold": int(REWARD_GOLD_PER_FLOOR * f * (1 + 0.05 * f)),
        "exp": int(REWARD_EXP_PER_FLOOR * f * (1 + 0.05 * f)),
    }


def incremental_reward(to_floor: int, paid_floor: int) -> dict:
    """增量奖励：只发 ``(paid_floor, to_floor]`` 这段从未领过的层。

    ``paid_floor`` 是已结算水位线（玩家存档 ``tower_paid_floor``）。
    用累计差而非逐段求和，保证「分 N 轮电梯打完」与「一口气打完」总额完全相同，
    既不重复也不缩水。to_floor <= paid_floor 时返回全 0（电梯重打旧层不白嫖）。
    """
    hi = max(0, int(to_floor))
    lo = min(max(0, int(paid_floor)), hi)
    a, b = clear_reward(lo), clear_reward(hi)
    return {"gold": max(0, b["gold"] - a["gold"]),
            "exp": max(0, b["exp"] - a["exp"])}


# ---------------------------------------------------------------------------
# 电梯（2026-11-17）：失败回 1 层后，花金币直达上次止步层，满状态重打。
# ---------------------------------------------------------------------------
ELEVATOR_GOLD_RATIO = 0.35   # 电梯费 = 目标层累计金币奖励的这个比例
ELEVATOR_MIN_GOLD = 200      # 费用下限（低层也要有代价）


def elevator_cost(floor: int) -> int:
    """直达 ``floor`` 层的金币费用；目标层越高越贵，占累计奖励固定比例。"""
    f = max(1, int(floor))
    return max(ELEVATOR_MIN_GOLD,
               int(clear_reward(f)["gold"] * ELEVATOR_GOLD_RATIO))


# ---------------------------------------------------------------------------
# 自洽性自检（裸跑：python tower_data.py）
# ---------------------------------------------------------------------------
def _self_test() -> int:
    ok = True

    def chk(name, cond):
        nonlocal ok
        print(("  OK  " if cond else "  FAIL ") + name)
        ok = ok and cond

    # 难度：层数趋势向下、有下限、Boss 最难
    hs = [floor_hard(n) for n in range(1, 31)]
    chk("hard 全在 [下限,上限]", all(HARD_MIN <= h <= HARD_MAX for h in hs))
    chk("hard 有下限不破", min(floor_hard(n) for n in range(1, 500)) >= HARD_MIN)
    chk("Boss 层是周期内最难", all(
        floor_hard(b) < floor_hard(b - 1) for b in range(10, 101, 10)))
    chk("floor_kind 分类正确", (
        floor_kind(10) == "boss" and floor_kind(5) == "elite"
        and floor_kind(20) == "boss" and floor_kind(7) == "normal"))
    # 武器池无保底、按层解锁、已拥有剔除
    chk("低层不掉高阶武器", "血斧" not in weapon_pool_for(1, set()))
    chk("12层解锁血斧", "血斧" in weapon_pool_for(12, set()))
    chk("已拥有剔除", "玄重尺" not in weapon_pool_for(50, {"玄重尺"}))
    chk("全收集返回空", weapon_pool_for(99, set(WEAPON_MIN_FLOOR)) == [])
    # 三选一/词缀合法
    import random as _r
    chk("三选一不重复", len({x["key"] for x in roll_loot_choices(_r.Random(1).random)}) == 3)
    chk("Boss 两词缀/精英一词缀", (
        len(affixes_for(10, _r.Random(2).random)) == 2
        and len(affixes_for(5, _r.Random(2).random)) == 1
        and len(affixes_for(7, _r.Random(2).random)) == 0))
    chk("宝箱率 Boss=100%", chest_rate(10) == 1.0)
    chk("结算线性递增", clear_reward(10)["gold"] > clear_reward(5)["gold"] > 0)
    print("SELF_TEST", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(_self_test())
