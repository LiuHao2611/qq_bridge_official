# -*- coding: utf-8 -*-
"""斗技斗气消耗占比表（2026-11-26 从 config.py 拆出，遵守单文件 200KB 红线）。

背景：旧版 ``SKILL_TIER_MP_FRAC`` 恒定 20/34/50%，导致从斗之气到斗圣，普通斗技
永远扣 20%、每战永远 5 次——玩家辛苦升上去的斗气上限「毫无实感」。现在按大境界
分档递减：境界越高，同阶斗技越省。高阶/特殊降幅更小（后期主力，占比本就该更高）。

以 realm_index 分档（0=斗之气 … 7=斗宗 … 8=斗尊 … 12=斗帝），取「>=起点」的最后一档。
卡面与实算共用 ``frac_for(tier, realm_idx)``，杜绝两张皮。
"""
from __future__ import annotations

#: 基础占比（=斗之气档），与 config.SKILL_TIER_MP_FRAC 同值，作为未命中档位的兜底。
BASE_FRAC = {"普通": 0.20, "高阶": 0.34, "特殊": 0.50}

#: (realm_index 起点, {普通, 高阶, 特殊})
BY_REALM = (
    (0, {"普通": 0.20, "高阶": 0.34, "特殊": 0.50}),
    (3, {"普通": 0.17, "高阶": 0.30, "特殊": 0.46}),   # 斗灵起
    (6, {"普通": 0.14, "高阶": 0.26, "特殊": 0.42}),   # 斗皇起
    (8, {"普通": 0.12, "高阶": 0.24, "特殊": 0.40}),   # 斗尊起
)


def frac_for(tier: str, realm_idx: int) -> float:
    """某阶斗技在某大境界下的斗气消耗占比（唯一真源）。"""
    frac = BASE_FRAC.get(tier, 0.20)
    ri = int(realm_idx)
    for start, table in BY_REALM:
        if ri >= start:
            frac = table.get(tier, frac)
    return float(frac)
