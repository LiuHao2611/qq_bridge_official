# -*- coding: utf-8 -*-
"""pic/templates/doupo_ruin.py —— 斗破 · 傀儡遗迹日副本卡（2026-11-18）。

三段遗迹（魔炎谷库房 / 天墓·能量之层 / 虚空雷池）按同日水位线推进。
版式沿用 ``doupo_battle`` 的单列分组骨架（度量优先 / 显式 gap / boxes 自检），
只换标题、图标语义与数据文案——不为「长得不一样」重写一套排版逻辑。

输入由 ``doupo.card.build_ruin_data`` 生成：
  title / subtitle / groups:[{name, rows:[...]}] / tip
"""
from __future__ import annotations

from . import doupo_battle as _battle

_DEMO = {
    "title": "傀儡遗迹",
    "subtitle": "今日进度 0/3 · 每日重置",
    "groups": [
        {"name": "第一段 · 魔炎谷库房", "rows": [
            "原始出处：萧炎自魔炎谷抄家得《天妖傀》竹简",
            "守库尸卫 ｜ 掉 强者躯体×1、暗金玄铁×1 ｜ 待挑战",
        ]},
        {"name": "第二段 · 天墓·能量之层", "rows": [
            "能量之层游荡无主凶魂，须炼尽神智方可入料",
            "远古凶魂 ｜ 掉 无主灵魂×1、火属性魔核×1 ｜ 未解锁",
        ]},
        {"name": "第三段 · 虚空雷池", "rows": [
            "雷霆淬体之地，胜守者方得传承竹简",
            "雷池守护者 ｜ 掉 雷淬石×2、天妖傀竹简×1 ｜ 未解锁",
        ]},
    ],
    "tip": "发「傀儡遗迹」逐段挑战 · 发「傀儡」看名录 · 发「炼制傀儡」开炉",
}


def build(t, data: dict | None = None, width: int | None = None, **kw):
    data = dict(data or _DEMO)
    if not data.get("groups"):
        data = dict(_DEMO)
    data.setdefault("title", "傀儡遗迹")
    data.setdefault("tip", _DEMO["tip"])
    return _battle.build(t, data, width, **kw)
