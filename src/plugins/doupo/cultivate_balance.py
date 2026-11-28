# -*- coding: utf-8 -*-
"""修炼速率平衡（2026-11-28，用户「前中期修炼更快，后期打怪升得快」）。

背景 / 数据（勿删注释）：
  * 旧常量 ``CULTIVATE_PER_MIN=6`` 固定不变，而打怪单场经验锚
    ``exp_need(lv)/K(lv)`` 随等级暴涨（lv22≈1647、lv94≈8901）。修炼 6/min
    相对打怪形同虚设——用户实测「修炼还不如打怪快」属实。
  * 用户拍板按**大境界自然分档**：前中期（斗之气~斗王）修炼是有效甚至更快的
    升级手段，斗皇起逐步衰减，后期靠打怪/副本。

设计口径（唯一真源，core.settle / tower 都从这里取）：
  * lv=1 精确返回 ``CULTIVATE_PER_MIN``(6.0)，保持既有离线封顶测试锚点不变；
  * ri 0~5（斗之气→斗王）随境界抬升到峰值 ``EARLY_PEAK_PER_MIN``，让挂机成为
    前中期主线：8h 封顶 × 峰值 ≈ exp_need(lv)；
  * ri≥6（斗皇起）按 ``LATE_DECAY`` 逐步回落到低于打怪单场，后期转打怪主导；
  * 数值只在本模块定义——config.py 已贴 200KB 红线，不再往里塞公式（沿用
    weapon_curve.py 的拆分做法）。

为什么不用「锚定 exp_need(lv) 的比例」：那会让高等级挂机收益爆炸（exp_need 是
平方增长），且与离线 8h 封顶叠加后后期挂机也能一天一级，直接违背「后期打怪快」。
用境界分档的绝对值曲线，前中期可控、后期可控。
"""
from __future__ import annotations

try:
    from . import config as cfg
except (ImportError, ValueError):  # 单文件直载兜底
    import config as cfg  # type: ignore[no-redef]

#: 前中期修炼峰值（每分钟修为）。ri=5 斗王时达到。
#: lv46(斗王) exp_need≈94044，8h=480min × 90 = 43200 ≈ 0.46 级/天（挂机当天有效），
#: 配合打怪才是完整节奏；峰值再高会让离线收益压过在线玩法。
EARLY_PEAK_PER_MIN = 90.0

#: 后期衰减：每升一个大境界（ri≥6），速率乘以本系数。0.72 ⇒ 斗皇≈65、斗宗≈47、
#: 斗尊≈34、斗圣≈12/min，明显低于同级打怪单场收益，逼玩家后期靠打怪。
LATE_DECAY = 0.72

#: 后期衰减下限（斗尊巅峰/半圣/斗圣/斗帝不再无限衰减，保底挂机有点收益）。
LATE_FLOOR_PER_MIN = 8.0

#: 分档锚点：ri 0~5 用「基线→峰值」线性过渡；ri=0 必须 == CULTIVATE_PER_MIN。
_EARLY_RANMS = 5  # ri 0..5 共 6 档，跨 5 段


def _clamp_ri(lv: int) -> int:
    try:
        return max(0, min(12, cfg.realm_index(int(lv))))
    except Exception:  # noqa: BLE001
        return 0


def cultivate_rate(lv: int) -> float:
    """等级 -> 被动修炼修为/分钟（前中期升、后期降）。

    - lv=1（ri=0）精确返回 ``CULTIVATE_PER_MIN``，保持既有测试与手感锚点；
    - ri 1..5 在 6 与峰值之间线性抬升；
    - ri>=6 从峰值按 ``LATE_DECAY`` 逐档衰减，钉 ``LATE_FLOOR_PER_MIN`` 下限。
    """
    ri = _clamp_ri(lv)
    base = float(cfg.CULTIVATE_PER_MIN)
    if ri <= 0:
        return base
    if ri <= _EARLY_RANMS:
        # 6 档线性：ri=1 → base+(peak-base)/5, ..., ri=5 → peak
        t = ri / _EARLY_RANMS
        return base + (EARLY_PEAK_PER_MIN - base) * t
    # ri>=6：斗皇起逐档衰减
    over = ri - _EARLY_RANMS          # 斗皇=1
    val = EARLY_PEAK_PER_MIN * (LATE_DECAY ** over)
    return max(LATE_FLOOR_PER_MIN, val)
