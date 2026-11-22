# -*- coding: utf-8 -*-
"""周副本数据层（2026-11-17）：7 层一命通关 / 周一快照锁定难度 / 奖励暂存锁定。

设计见 docs/斗破无限塔电梯与周副本_设计方案.md。用户拍板：
  ① 失败奖励全清（含武器），通关前奖励锁定不可用；
  ② 周一快照锁定个人难度（一周内不变，给玩家提升战力的机会）；
  ③ 新入道固定新手难度档。

本文件只放**纯数据与公式**，零副作用、不 import core / nonebot，可被裸跑门禁加载。
⚠ "7 层塔"是游戏化设计，不是原著考据（原著只有天焚炼气塔 / 试炼之塔九层）。
"""
from __future__ import annotations

import datetime

WEEKLY_FLOORS = 7

# 层类型：第 5 层精英、第 7 层 Boss（守塔者意象取自原著，非"原著七层塔"）
ELITE_FLOOR = 5
BOSS_FLOOR = 7


def floor_kind(n: int) -> str:
    n = int(n)
    if n == BOSS_FLOOR:
        return "boss"
    if n == ELITE_FLOOR:
        return "elite"
    return "normal"


#: 每层 hard（越小越难，与 monster_scaled 同口径）。周本固定 7 层，曲线比无限塔更陡：
#: 前 4 层热身、第 5 层精英收紧、第 7 层 Boss 是硬关。想一周内通关必须当周变强。
WEEKLY_HARD = (0.92, 0.88, 0.84, 0.80, 0.74, 0.70, 0.60)


def floor_hard(n: int) -> float:
    n = max(1, min(WEEKLY_FLOORS, int(n)))
    return WEEKLY_HARD[n - 1]


#: 周本怪攻击缩放（连战 7 层的持久战，但比无限塔 0.18 高：层数少要压总时长）
WEEKLY_ATK_SCALE = 0.22
#: Boss / 精英攻击基数倍率
ATK_TIER = {"boss": 1.5, "elite": 1.25, "normal": 1.0}

BOSS_NAME = "天焚塔灵"      # 意象：天焚炼气塔封印的异火守卫（游戏设计，非原著角色）
ELITE_NAME = "塔卫统领"


def monster_name(n: int) -> str:
    k = floor_kind(n)
    if k == "boss":
        return BOSS_NAME
    if k == "elite":
        return ELITE_NAME
    return "塔卫"


def monster_lv(n: int, snapshot_lv: int) -> int:
    """周本怪登记等级：随层数缓升；monster_scaled 的 ±20% 钳位会兜住跨境界。"""
    return int(snapshot_lv) + (int(n) - 1) // 2


# ---------------------------------------------------------------------------
# ISO 周 key（快照与"本周已通关"的唯一时间锚）
# ---------------------------------------------------------------------------
def week_key(ts: float | None = None) -> str:
    """返回 ``YYYY-Www``（ISO 年-周），周一为一周之始。"""
    d = (datetime.datetime.fromtimestamp(ts)
         if ts is not None else datetime.datetime.now()).date()
    y, w, _ = d.isocalendar()
    return "%04d-W%02d" % (y, w)


# ---------------------------------------------------------------------------
# 快照画像：从玩家取战斗相关字段的**深拷贝**（monster_scaled 靠它反解怪数值）
# ---------------------------------------------------------------------------
SNAPSHOT_FIELDS = (
    "lv", "weapon", "weapons", "equipped_weapon", "bonus", "flames",
    "diyan", "luck", "skills", "learned", "loadout", "faction",
)


def snapshot_profile(p: dict) -> dict:
    """抽取战斗画像快照（深拷贝，不引用玩家活字典）。"""
    import copy  # 局部 import：保持本模块"零依赖 core/nonebot"，copy 是 stdlib
    return {k: copy.deepcopy(p[k]) for k in SNAPSHOT_FIELDS if k in p}


#: 新入道固定新手档：一个稳定的低阶画像，所有新手同一难度。
#: lv 低 + 默认玄重尺 + 无增益 → monster_scaled 反解出的怪对谁都一样。
ROOKIE_PROFILE = {
    "lv": 5,
    "weapon": 0,
    "weapons": ["玄重尺"],
    "equipped_weapon": "玄重尺",
    "bonus": {},
    "flames": {},
    "diyan": False,
    "luck": 0,
    "skills": {},
    "learned": [],
    "loadout": [],
    "faction": "jianan",
}


# ---------------------------------------------------------------------------
# 奖励：每层增量 × 周本倍率；宝箱武器在通关前只进暂存区
# ---------------------------------------------------------------------------
WEEKLY_REWARD_MULT = 1.5       # 周本单位层奖励倍率（高于无限塔，因一命通关风险大）

# 2026-11-22 经验平衡 v0.2：周本修为改锚快照等级（不再吃塔的递增固定 90/层）。
# 镜像是 tower_data._ref_exp_need（同公式），一致性由 test_doupo_exp_balance 守门。
WEEKLY_PEAK_LV = 102           # == config.PEAK_LV，显式封顶避免 exp_need(103)=0 归零
WEEKLY_EXP_SNAPSHOT_RATIO = 0.05   # == config.WEEKLY_EXP_SNAPSHOT_RATIO


def _ref_exp_need(lv: int) -> int:
    """config.exp_need 的零依赖镜像（仅本模块奖励锚点用）。"""
    lv = int(lv)
    if lv >= 103:
        return 0
    return 400 * lv * lv // 9


def floor_reward(n: int, snapshot_lv: int | None = None) -> dict:
    """第 n 层的金币/修为（增量，不含之前层）。

    2026-11-22 v0.2：金币仍走塔的 incremental_reward 递增曲线；修为改按**快照等级**
    每层定额 = exp_need(min(snapshot_lv, PEAK_LV))×5%×WEEKLY_REWARD_MULT（层数无关，
    因为一周难度锁定同一快照）。``snapshot_lv`` 由 core.weekly_floor 从快照带入；
    显式 min() 封顶 PEAK_LV，杜绝快照 lv103 时 exp_need(103)=0 奖励归零。
    缺省 None 仅用于裸跑 self_test 的金币断言（修为给 0，不冒充真实快照）。
    """
    try:                                   # 包内导入（core/commands 调用路径）
        from .tower_data import incremental_reward
    except ImportError:                    # 裸跑根目录脚本路径
        from tower_data import incremental_reward

    r = incremental_reward(max(1, int(n)), max(0, int(n) - 1))
    gold = int(r["gold"] * WEEKLY_REWARD_MULT)
    if snapshot_lv is None:
        return {"gold": gold, "exp": 0}
    lv = min(max(1, int(snapshot_lv)), WEEKLY_PEAK_LV)
    exp = int(_ref_exp_need(lv) * WEEKLY_EXP_SNAPSHOT_RATIO * WEEKLY_REWARD_MULT)
    return {"gold": gold, "exp": exp}


# ---------------------------------------------------------------------------
# 自洽性自检
# ---------------------------------------------------------------------------
def _self_test() -> int:
    ok = True

    def chk(name, cond):
        nonlocal ok
        print(("  OK  " if cond else "  FAIL ") + name)
        ok = ok and cond

    chk("7 层固定", WEEKLY_FLOORS == 7)
    chk("层类型 5精英 7Boss", floor_kind(5) == "elite" and floor_kind(7) == "boss"
        and floor_kind(3) == "normal")
    chk("hard 逐层递减", all(floor_hard(i) > floor_hard(i + 1) for i in range(1, 7)))
    chk("ISO 周 key 形状", len(week_key()) == 8 and week_key()[4:6] == "-W")
    chk("同周同 key", week_key(1700000000) == week_key(1700000000 + 60))
    chk("Boss 奖励 > 普通层", floor_reward(7)["gold"] > floor_reward(1)["gold"])

    def _iso_check():
        p = {"lv": 1, "weapon": 0}
        sp = snapshot_profile(p)
        sp["lv"] = 999
        return p["lv"] == 1
    chk("快照深拷贝隔离", _iso_check())
    chk("新手档稳定", ROOKIE_PROFILE["lv"] == 5 and not ROOKIE_PROFILE["bonus"])
    print("SELF_TEST", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(_self_test())
