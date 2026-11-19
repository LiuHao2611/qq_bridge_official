# -*- coding: utf-8 -*-
"""B 线数值异火的加成聚合（2026-11-19 三层投放）。

为什么单独成模块：
  * config 只放数据（BONUS_FLAMES 的 kind/value）；
  * core / puppet_core 各自读玩家已收服的火 → 汇总口径必须唯一，否则炼药卡说的
    加成和实际成功率两张皮（同「战斗终局只有一个数」的教训）。
  * 零 nonebot 依赖，纯函数，可直接单测。

口径：
  * 已收服 = p["flames"][name] > 0；等级 lv 即加成级数（与喂养等级同源）。
  * alchemy → 炼药成功率加成（小数，如 0.06 = +6 个百分点），调用方封顶 CRAFT_RATE_CAP；
  * puppet_atk / puppet_def → 傀儡攻防加成（小数）。
  * 只统计 config.BONUS_FLAMES 里登记的火；S/A 火不参与（它们走招式/剧情特效）。
"""
from __future__ import annotations

from . import config as cfg


def _owned_level(p: dict, name: str) -> int:
    return max(0, int((p.get("flames") or {}).get(name, 0)))


def alchemy_rate_bonus(p: dict) -> float:
    """已收服 B 线炼药火的合成成功率加成（小数，未封顶）。

    2026-11-19 其他火焰（兽火等）的 alchemy 型也并入：与 B 线同源聚合，
    卡面与实战成功率永远一致。
    """
    total = 0.0
    for f in cfg.BONUS_FLAMES:
        if f["kind"] == "alchemy":
            total += float(f["value"]) * _owned_level(p, f["name"])
    for f in getattr(cfg, "OTHER_FLAMES", []):
        if f["bonus"] == "alchemy":
            total += float(f["value"]) * _owned_level(p, f["name"])
    return total


def other_flame_ratio(p: dict) -> dict:
    """其他火焰（兽火/天然/人造/融合火）给六维的**比例**加成，供 stats() 单一源消费。

    返回 ``{"atk": x, "hp": y}``（小数，如 0.05 = +5%）。atk 作用于力量（攻击主
    属性），hp 作用于体力上限。alchemy 型不在此（走 alchemy_rate_bonus）。
    只有已收服（flames[name]>0）才计；等级 = 加成级数。
    """
    out = {"atk": 0.0, "hp": 0.0}
    for f in getattr(cfg, "OTHER_FLAMES", []):
        lv = _owned_level(p, f["name"])
        if lv <= 0:
            continue
        key = "atk" if f["bonus"] == "atk" else ("hp" if f["bonus"] == "hp" else "")
        if key:
            out[key] += float(f["value"]) * lv
    return out


def puppet_atk_bonus(p: dict) -> float:
    """已收服 B 线傀儡攻型火的攻击加成（小数）。"""
    total = 0.0
    for f in cfg.BONUS_FLAMES:
        if f["kind"] == "puppet":
            total += float(f["value"]) * _owned_level(p, f["name"])
    return total


def puppet_def_bonus(p: dict) -> float:
    """已收服 B 线傀儡防型火的防御加成（小数）。

    目前 B 线傀儡火的 value 都计入攻击（伤害是傀儡唯一输出口径，
    `puppet_expected_damage`）；防御档留给后续接 temper。此处保留入口，
    按 kind 细分由 BONUS_FLAMES 的 stat 字段驱动（当前无 stat 字段则全归 atk）。
    """
    total = 0.0
    for f in cfg.BONUS_FLAMES:
        if f["kind"] == "puppet" and f.get("stat") == "def":
            total += float(f["value"]) * _owned_level(p, f["name"])
    return total


def breakdown(p: dict) -> list[dict]:
    """给卡面/文案的逐朵明细：[{name, kind, lv, value}]（只列已收服）。"""
    out = []
    for f in cfg.BONUS_FLAMES:
        lv = _owned_level(p, f["name"])
        if lv > 0:
            out.append({"name": f["name"], "kind": f["kind"],
                        "lv": lv, "value": float(f["value"])})
    return out
