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

#: 2026-11-19 用户「爬塔每层难度固定，不按玩家战力来」：
#: 每层怪的全部数值由一个**只由层号决定**的「标尺等级」反解，与真实玩家无关。
#: 第 1 层 = 1 级、第 10 层 = 19 级、第 30 层 = 59 级、第 52 层 = 103 级（封顶）。
#: 封顶后仍靠 floor_difficulty 的二次增长继续加深，保证无限层有终点。
REF_LV_PER_FLOOR = 2.0
#: 标尺等级上限（与 config.MAX_LV 同值，此处不 import config 以保持零依赖）。
REF_LV_CAP = 103


def floor_ref_lv(n: int) -> int:
    """第 n 层的「标尺等级」——**爬塔难度的唯一自变量**。

    以前塔怪按玩家面板反解（``monster_scaled(p, ...)``），玩家一升级，同一层
    的怪就同步变强，爬塔等于永远在原地跑。改成按层号定标尺后：同一个人今天
    打第 20 层和明天打第 20 层，面对的是同一只怪；变强只体现在「能爬更高」。
    """
    n = max(1, int(n))
    lv = int(round(1 + (n - 1) * REF_LV_PER_FLOOR))
    return max(1, min(REF_LV_CAP, lv))

#: 层间回血（每层胜利回一点，避免无限层瞬间耗尽）。
#: 塔怪反解成「约4回合互杀」，与普通遭遇战同险；连爬必须留一点续航。
#: ⚠ 2026-11-17 用户反馈「气血怎么没降低」：原值 0.35 会在单层伤害 <35% 上限时
#: 把伤害**全部盖掉**（实测 lv60 连续 8 层满血刷塔）。真正的续航来源应是三选一
#: 里的回血类战利品，而不是白送。
#: 2026-11-23 重标定（用户「400-500 层是正常范围」）：连爬 400+ 层需要每层净掉血
#: 接近 0，故回血上调到 0.45，同时受 FLOOR_REST_DMG_RATIO=0.9 约束（最多补回本层
#: 损失的 90%，保证仍单调不增）。真跑 10 种子：满级裸 389~425 / 中上(武器6)
#: 399~429 / 顶配(武器9+帝炎) 529~569 层。见 tmp/probe_tower_verify.py。
FLOOR_REST_HEAL = 0.45

#: 歇息最多只能补回**本层实际损失**的这个比例。
#: 这是「气血随层数单调不增」的硬保证：无伤则不回，受伤最多补这个比例。
#: 2026-11-23 从 0.5 上调到 0.9 以支撑 400+ 层连爬；仍<1 故单调性保持。
#: 门禁 tests/test_doupo_tower.py::test_hp_monotonic_across_floors 守着。
FLOOR_REST_DMG_RATIO = 0.9

#: 塔怪攻击缩放：爬塔是**持久消耗战**，不是单场决斗。
#: monster_scaled 把怪 atk 反解成「4~5回合杀玩家」，单场合理，但连爬会在
#: 第1~2层就耗尽气血。塔怪统一降攻，让玩家能连续爬多层；难度递增交给下方的
#: floor_difficulty（随层数持续叠加）与词缀，而非靠单场一击必杀。
#: 2026-11-17：原值 0.42 会让 lv70 只能爬 6 层，按实扫降到 0.18。
#: 2026-11-23 重标定（用户「400-500 层是正常范围，不是上限」）：0.18 下顶配满级
#: 只能到 45 层——瓶颈是怪攻 × 回合数 × 400 层的累积承伤。降到 0.015 后，
#: 配合回血 0.45 + 低 QUAD，中上战力可达 419 层、顶配 539 层（10 种子真跑）。
#: 难度递增改由下方 floor_difficulty 的二次项（500 层后变陡）承担。
TOWER_ATK_SCALE = 0.015

#: 随层数持续叠加的难度（无限层必须有终点，不能只靠周期 hard 的锯齿）。
#: 玩家属性在一轮内不增长，若难度只按 10 层周期循环，强者能无限爬。
#: 倍率 = 1 + k·((N-1)/10)^2。**hp_mult 直接决定怪血→回合数→承伤**，
#: 所以 400-500 层可达要求 500 层 hp_mult 只有约 1.75×，绝不能是旧值 374×。
#: 2026-11-23 重标定：k 从 0.15 降到 0.0003，形成「低层极平缓 + 500 层后变陡」软墙：
#:   100 层 1.03× / 300 层 1.27× / 500 层 1.75× / 800 层 2.92× / 1500 层 7.74×。
#: 旧 k=0.15 时 500 层 374× 是「假目标」（顶配战力仅 88× 上界，数学上不可达）。
DIFF_QUAD = 0.0003     # 每 10 层的二次难度增量（500 层后变陡的软墙斜率）
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


def affixes_for(n: int, rng=None) -> list[str]:
    """按层号派生词缀：精英 1 个、Boss 2 个。

    ``rng=None``（2026-11-19 起 ``tower_floor`` 的默认调用）时种子取自层号本身，
    保证「同一层每次打，怪和词缀完全一样」——这是用户「每层难度固定」的一部分。
    传 rng 的旧调用（单测）行为不变。
    """
    k = floor_kind(n)
    if k == "normal":
        return []
    if rng is None:
        import random as _r
        rng = _r.Random("tower-affix:%d" % int(n)).random
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

# 2026-11-22 经验平衡 v0.2：塔修为改锚等级曲线（不再固定 90/层，高等级无感）。
# 本模块刻意零依赖 config（裸跑门禁），故在此内联两个常量/公式的镜像：
#   PEAK_LV == config.PEAK_LV == config.MAX_LV-1 == 102
#   exp_need(lv) == config.exp_need(lv) == 400·lv²//9, 且 lv>=MAX_LV(103) 时为 0
# 一致性由 tests/test_doupo_exp_balance.py 的守门测试逐等级比对，禁止手抄漂移。
TOWER_PEAK_LV = 102
TOWER_MAX_LV = 103
TOWER_EXP_REF_RATIO = 0.05   # == config.TOWER_EXP_REF_RATIO，守门测试钉住


def _ref_exp_need(lv: int) -> int:
    """config.exp_need 的零依赖镜像（仅本模块奖励锚点用）。"""
    lv = int(lv)
    if lv >= TOWER_MAX_LV:
        return 0
    return 400 * lv * lv // 9


def _tower_exp_cum(floors: int) -> int:
    """0→floors 层「每层 exp_need(min(floor_ref_lv,PEAK))×5%」的累计修为。

    显式 min() 封顶 PEAK_LV：52 层起 floor_ref_lv=103，若不封顶 exp_need(103)=0，
    新增层奖励归零（假机制，见 docs/经验产出平衡评审 v0.2 §0.1 A04）。
    """
    total = 0
    for i in range(1, max(0, int(floors)) + 1):
        ref = min(floor_ref_lv(i), TOWER_PEAK_LV)
        total += int(_ref_exp_need(ref) * TOWER_EXP_REF_RATIO)
    return total


def clear_reward(floors: int) -> dict:
    """从 0 连续爬到 floors 层的**累计**金币/经验奖励（非线性）。

    ⚠ 电梯分层重打时必须用 ``incremental_reward(to_floor, paid_floor)`` 取差值，
    不能把每轮的 clear_reward 相加——那会重复发低层奖励（2026-11-17 用户硬要求
    「不会重复获得之前挑战过层数的奖励」）。

    2026-11-22 v0.2：金币曲线保持原样不动；修为改按标尺等级锚点（见 _tower_exp_cum）。
    """
    f = max(0, int(floors))
    return {
        "gold": int(REWARD_GOLD_PER_FLOOR * f * (1 + 0.05 * f)),
        "exp": _tower_exp_cum(f),
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
# 逐层分档掉落（2026-11-23 用户「从0到无限层，奖励自动生成，不要硬编码，
#   小怪一般材料丹药、精英好一点、Boss更好，以此类推」）。
#
# 设计纪律：
#   * **确定性、不掷骰**：奖励只由层号 + 怪种决定，同层永远同一份掉落 —— 与
#     「同层同一只怪」固定难度同源；也让「电梯分层重打不重复领」可用水位线差值实现
#     （带 rng 的逐层掉落无法做累计差，会重复刷）。
#   * **不新增物品**：材料取 NOVEL_MATERIALS 的 tier1~4，丹药取既有配方名，
#     全部是背包里真实可服/可用的东西。
#   * **层数分档位、怪种定品级**：层数决定基础档（0/51/151/301 起 → tier1~4），
#     怪种在基础档上升降（普通 -1 / 精英 0 / Boss +1，钳在 1~4）；tier 封顶后
#     数量随层数递增，故无限层永不枯竭（「以此类推」）。
# ---------------------------------------------------------------------------
#: 层档起点（含）。层号 >= 该值即进入对应 tier；最后的 301+ 为 tier4 长线档。
LOOT_TIER_FLOORS = (0, 51, 151, 301)
#: 怪种对基础档的品级修正：普通低一档、精英本档、Boss 高一档。
LOOT_KIND_TIER_DELTA = {"normal": -1, "elite": 0, "boss": 1}
#: 每档丹药名（既有配方，服下回血/回斗气/增修为），与 tier 一一对应。
LOOT_PILL_BY_TIER = {
    1: "回灵丹",    # 四品·回血
    2: "回春散",    # 回血（上限 pct）
    3: "回气丹",    # 回斗气（上限 pct）
    4: "复伤丹",    # 五品·疗伤珍品
}
#: 高层每多少层，材料/丹药数量 +1（tier4 长线档的「以此类推」递增节奏）。
LOOT_QTY_PER_FLOORS = 100


def floor_loot_tier(n: int) -> int:
    """层号 → 基础材料档位 tier(1~4)，只由层数决定。"""
    n = max(0, int(n))
    tier = 1
    for i, start in enumerate(LOOT_TIER_FLOORS, start=1):
        if n >= start:
            tier = i
    return tier


def floor_loot(n: int) -> dict:
    """第 n 层的确定性掉落 ``{"material": (name, qty), "pill": (name, qty)}``。

    n 为该层**层号（1 起）**；材料按怪种在该层基础档上升降，tier 封顶后用数量递增
    体现「越高层越好」，绝不返回空（高层也有收益）。
    """
    n = max(1, int(n))
    base = floor_loot_tier(n)
    delta = LOOT_KIND_TIER_DELTA.get(floor_kind(n), 0)
    tier = max(1, min(4, base + delta))
    # 材料：该 tier 池内按层号确定性取一个（同层恒定）；数量随层数递增。
    # 本模块零依赖 config，材料名在此内联（与 recipes_data.NOVEL_MATERIALS 同步，
    # 守门 test_tower_loot_progression 比对，禁止漂移）。
    pool = _LOOT_MATERIAL_POOL[tier]
    mat = pool[(n - 1) % len(pool)]
    qty = 1 + (n - 1) // LOOT_QTY_PER_FLOORS
    pill = LOOT_PILL_BY_TIER[tier]
    pill_qty = 1 + (n - 1) // (LOOT_QTY_PER_FLOORS * 2)
    return {"material": (mat, qty), "pill": (pill, pill_qty), "tier": tier}


# 材料池镜像（零依赖裸跑门禁用；唯一真源仍是 recipes_data.NOVEL_MATERIALS，
# 由 tests/test_tower_loot_progression.py 逐 tier 比对，防止两边漂移）。
_LOOT_MATERIAL_POOL = {
    1: ["回灵赤果", "厚土芝", "鬼脸花"],
    2: ["木灵三针花", "火灵根", "伴生紫晶源", "纳石"],
    3: ["冰火蛇鳞果", "玉骨果", "冰火龙须果", "天麻翡石精", "丹灵浆"],
    4: ["九彩原石", "万年血灵参"],
}


def incremental_loot(to_floor: int, paid_floor: int) -> dict:
    """只发 ``(paid_floor, to_floor]`` 新层的掉落，按物品汇总 ``{name: qty}``。

    与金币修为共用 ``tower_paid_floor`` 水位线：电梯重打旧层不再掉材料/丹药。
    确定性掉落（floor_loot 不掷骰），故可逐层累加、升降序无关、可分轮等价。
    """
    hi = max(0, int(to_floor))
    lo = min(max(0, int(paid_floor)), hi)
    agg: dict[str, int] = {}
    for n in range(lo + 1, hi + 1):
        d = floor_loot(n)
        for key in ("material", "pill"):
            name, qty = d[key]
            agg[name] = agg.get(name, 0) + int(qty)
    return agg


# ---------------------------------------------------------------------------
# 2026-11-25 用户「去掉电梯，默认从最后一次爬塔的层数开始爬」：
# 电梯功能与电梯费常量已删除；新会话直接从 tower_checkpoint 的下一层开打，
# 「爬塔」本身就是电梯。增量奖励仍靠 tower_paid_floor 水位线防重复领。
# ---------------------------------------------------------------------------


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
    # 2026-11-19 固定难度：标尺等级只由层号决定，且单调、封顶
    chk("标尺等级单调不降", all(floor_ref_lv(n) <= floor_ref_lv(n + 1)
                          for n in range(1, 400)))
    chk("标尺等级封顶", floor_ref_lv(999) == REF_LV_CAP)
    chk("第1层标尺1级", floor_ref_lv(1) == 1)
    chk("词缀按层号固定", affixes_for(10) == affixes_for(10))
    print("SELF_TEST", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(_self_test())
