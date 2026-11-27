# -*- coding: utf-8 -*-
"""玄重尺觉醒后（L>=9）的强化成长曲线——纯函数，不依赖 config。

单独成文件的原因：``config.py`` 已 201911 B，贴着 pre-commit 的 200KB 红线
（204800 B）。曲线连同事故注释放进去会直接顶破，故拆出。

数值真源仍在 ``config.WEAPON``（``awaken_internal`` / ``awaken_grow`` /
``max_stats``），本模块只负责"给定基数和等级，算出这一级的实际值"。

2026-11-27 事故背景（用户原话「我发强化玄重尺，怎么加成 0？？浪费我一堆
炎星石」）：上限从 L=9 提到 L=998 后，若 L>=9 一律返回同一张 max_stats，
则 L 与 L+1 完全相同 → 强化框恒显示「力量 ↗0 敏捷 ↗0」，却照扣炎星石。
玩家连点三次（9→10、10→11、11→12）烧掉 10+11+12=33 块换回三个 0；
照此走到 998 要烧 498456 块，全部换 0。
"""
from __future__ import annotations

import math


def weapon_stat(base: int, internal: int) -> int:
    """未觉醒段（L=0..8）的原著成长公式。h.java:80-102

    收益 = base × (1 + 0.1·L(L+1)/2)。L>=9 返回 0 是**哨兵值**，
    调用方必须转 ``awakened_value``——当属性直接用就是 2026-11-27 的
    「加成 0」事故（属性全归零 + 白烧炎星石）。
    """
    if internal >= 9:
        return 0
    total = base
    for k in range(internal + 1):
        total += base * (k * 10) // 100
    return total


def awakened_value(max_value: int, internal: int,
                   awaken_at: int, grow: float) -> int:
    """觉醒后第 ``internal`` 级的四词条实际值。

    曲线 ``gain = max_value × grow × √(lv) + lv``（lv = internal - awaken_at）：
      * ``√lv`` 项：前期给得爽，后期自然收敛（越练越慢，但不是练了没用）；
      * ``+ lv`` 项：**数学上保证每级至少 +1**。只用 √ 会整数坍缩——
        L=909 时 1000 基数每级只涨 0.998，截断后差值变 0（实测踩过）；
      * 觉醒那一级（lv<=0）原样返回 max_value，即原著硬编码
        2000/2000/1000/1200，一分不加。

    实算（max_value=2000, grow=0.04）：
      L=9 +0  ·  L=10 +81  ·  L=12 +141  ·  L=40 +476  ·  L=998 +3504（约 2.75 倍）

    为什么不用固定增量：强化消耗是 ``L+1``（递增），收益若恒定则越往后越亏；
    √ 让「每块炎星石换到的属性」衰减得足够慢，又不至于战力爆表。
    """
    lv = int(internal) - int(awaken_at)
    if lv <= 0:
        return int(max_value)
    gain = int(int(max_value) * float(grow) * math.sqrt(lv)) + lv
    return int(max_value) + gain
