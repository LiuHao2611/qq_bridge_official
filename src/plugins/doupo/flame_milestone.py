# -*- coding: utf-8 -*-
"""火焰收集里程碑 · 永久属性加成（2026-11-28，用户拍板）。

用户原话与例子
--------------
「加一个火焰等级里程碑，比如玩家收集了一个异火、三个兽火，初始都是一级，
那里程碑就是四级；如果任意一个火焰升级了，那里程碑就是五级，再多收集一个
一级火焰就是六级……达到一定里程碑奖励永久属性加成，这个加成给多点，因为能
收集足够多的火焰那就是到后期的玩家。」

口径（唯一真源）
----------------
* **里程碑 = 所有持有火焰的等级之和**（不是"收了几朵"，也不是"score"）：
  - 1 异火(1级) + 3 兽火(1级) = 4；
  - 任一火焰升到 2 级 = 5；
  - 再收 1 朵 1 级火 = 6。与用户例子逐条对齐。
  - 帝炎走 ``p["diyan"]`` 成就位，不在 ``p["flames"]`` 里，计 1 级。
  - S/A 榜位收藏火收服后也在 ``p["flames"]`` 里（值 1），照常计入。
* **档位奖励**：里程碑达到某档，永久全属性 +N%（对六维同时生效，含体力/斗气
  上限）。取"已达成的最高档"，不累加各档（避免后期爆炸）。
* **加成给多点**：后期档位陡增（+4% → +85%），因为能收集养满几十朵火的是
  大后期玩家；早期档位温和，不破坏新手平衡。

为什么独立模块
--------------
config.py 已贴 200KB 红线（204730 / 204800），按项目惯例（weapon_curve /
cultivate_balance / danlei）新增数值与纯函数独立成模块，config 一行不改。
零 nonebot 依赖，纯函数，可直接单测。
"""
from __future__ import annotations

try:
    from . import config as cfg
except (ImportError, ValueError):  # 单文件直载兜底
    import config as cfg  # type: ignore[no-redef]

#: 里程碑档位：(所需等级总和, 永久全属性加成小数)。取已达成的最高档。
#: 数值刻意后期给足——收集/养满几十朵火是大后期成就，回报要配得上。
#: 早期（4~8）温和，避免刚开局叠几朵 1 级火就战力翻倍。
MILESTONE_TIERS: tuple[tuple[int, float], ...] = (
    (4, 0.04),     # 用户例子的起点：1 异火 + 3 兽火
    (6, 0.07),     # 任一升一级或再收一朵
    (10, 0.12),
    (16, 0.20),
    (25, 0.30),
    (40, 0.45),
    (60, 0.65),
    (85, 0.85),    # 大后期：全部收集并养满
)


def milestone_level(p: dict) -> int:
    """玩家当前的里程碑等级 = 所有持有火焰等级之和。"""
    total = 0
    for name, lv in (p.get("flames") or {}).items():
        try:
            n = int(lv or 0)
        except (TypeError, ValueError):
            n = 0
        if n > 0:
            total += n
    # 帝炎走斗帝成就位，不在 flames 字典里，计 1 级。
    if p.get("diyan"):
        total += 1
    return max(0, int(total))


def tier_of(level: int) -> tuple[int, float]:
    """里程碑等级 → (已达成档位门槛, 加成小数)。未达首档返回 (0, 0.0)。"""
    gate, pct = 0, 0.0
    for g, v in MILESTONE_TIERS:
        if int(level) >= g:
            gate, pct = g, v
        else:
            break
    return gate, pct


def bonus_pct(p: dict) -> float:
    """里程碑永久全属性加成（小数）。供 stats 与卡面共用同一口径。"""
    return tier_of(milestone_level(p))[1]


def next_tier(p: dict) -> tuple[int, int]:
    """返回 (距下一档还差多少级, 下一档门槛)；已封顶返回 (0, 0)。"""
    level = milestone_level(p)
    for g, _v in MILESTONE_TIERS:
        if level < g:
            return g - level, g
    return 0, 0
