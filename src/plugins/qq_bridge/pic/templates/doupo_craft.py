# -*- coding: utf-8 -*-
"""pic/templates/doupo_craft.py —— 斗破 · 傀儡炼制成傀卡（2026-11-20）。

用户 2026-11-20：「炼制傀儡 自定 的回复不要纯文字，改成卡片」。
成傀是一条**不可逆的消耗**（材料＋斗气当场扣掉），纯文字回执里「成了什么档、
肉身攻击多少、料扣了哪些」全埋在句子里；卡片把「成傀 / 用料 / 降档提醒」三块
分开摆，玩家一眼看出这一炉值不值。

版式沿用 ``doupo_battle`` 的单列分组骨架（度量优先 / 显式 gap / boxes 自检），
只换标题与文案——不为「长得不一样」重写一套排版逻辑（与 ``doupo_ruin`` 同做法）。

输入由 ``doupo.card.build_craft_data`` 生成：
  title / subtitle / groups:[{name, rows:[...]}] / tip
"""
from __future__ import annotations

from . import doupo_battle as _battle

_DEMO = {
    "title": "傀儡炼制",
    "subtitle": "铜尸傀 · 人级傀儡·铜皮 · id=1",
    "groups": [
        {"name": "成傀", "rows": [
            "【人级·强】人级傀儡档 · 铜皮",
            "肉身攻击 1830　斗师级",
            "雷淬纯度 0%　未认主",
        ]},
        {"name": "用料", "rows": [
            "强者躯体×1、火属性魔核×1、无主灵魂×1、精铁×1",
            "斗气 -620（余 3480）",
        ]},
    ],
    "tip": "发「傀儡」看名录 · 发「傀儡 淬炼」雷淬升档 · 发「傀儡 配方」看全部配方",
}


def build(t, data: dict | None = None, width: int | None = None, **kw):
    data = dict(data or _DEMO)
    if not data.get("groups"):
        data = dict(_DEMO)
    data.setdefault("title", "傀儡炼制")
    data.setdefault("tip", _DEMO["tip"])
    return _battle.build(t, data, width, **kw)
