# -*- coding: utf-8 -*-
"""火焰加成聚合：异火（B 线数值火）+ 兽火 + 新生异火（2026-11-24 统一）。

火焰身份只有两类——异火、兽火；本模块汇总所有"给数值"的火（B 线异火 /
兽火 / 新生异火），供 core 炼药、六维、傀儡和卡面共用同一口径。

为什么单独成模块：
  * config 只放数据（BONUS_FLAMES / BEAST_FLAMES / FUSION_FLAMES 的 kind/value）；
  * core / puppet_core 各自读玩家已收服的火 → 汇总口径必须唯一，否则炼药卡说的
    加成和实际成功率两张皮（同「战斗终局只有一个数」的教训）。
  * 零 nonebot 依赖，纯函数，可直接单测。

口径：
  * 已收服 = p["flames"][name] > 0；等级 lv 即加成级数（与喂养等级同源）。
  * alchemy → 炼药成功率加成（小数，如 0.06 = +6 个百分点），调用方封顶 CRAFT_RATE_CAP；
    **一炉只认加成最强的 ALCHEMY_FLAME_LIMIT 朵**（见 :func:`_alchemy_contribs`）；
  * atk/hp → 六维比例加成；puppet → 傀儡攻防加成。
  * S/A 榜位火不参与（它们走招式/剧情特效）。
"""
from __future__ import annotations

from . import config as cfg


def _owned_level(p: dict, name: str) -> int:
    return max(0, int((p.get("flames") or {}).get(name, 0)))


def has_any_flame(p: dict) -> bool:
    """玩家是否持有任意火焰（异火/兽火/新生异火）。入道空火开局为 False。

    2026-11-24 用户「没火不准炼药和养火」——craft / feed_flame 的单一前置。
    """
    return any(int(v or 0) > 0 for v in (p.get("flames") or {}).values())


def _numeric_flames():
    """所有给数值的火：B 线异火 + 新生异火 + 兽火（alchemy/atk/hp/puppet 四类）。"""
    return list(cfg.BONUS_FLAMES) + list(cfg.FUSION_FLAMES) + list(cfg.BEAST_FLAMES)


#: 一炉同时投入的火种上限（2026-11-26 用户拍板「取最强 2 种」）。
#:
#: 原著口径是「一炉一火种」（第六章：材料、火种、灵魂感知力三条件），同时操控多种
#: 火焰是极少数天才的天赋（第 1100 章旁白）。本作放宽到 2，既不再是「11 朵全叠」
#: 的人人天才，也不再逼玩家只养一朵。**玩家可见文案不声明这条限制**（用户要求
#: 「不要声明、不要多话」）——卡面只如实列已收服的火，不解释为什么只有两朵在算。
ALCHEMY_FLAME_LIMIT = 2


def _alchemy_contribs(p: dict) -> list[dict]:
    """已收服的 alchemy 型火，按**当前加成**降序取前 :data:`ALCHEMY_FLAME_LIMIT` 朵。

    返回 ``[{"name","lv","value","contrib"}]``，``contrib = value × lv``。
    排序键：贡献降序 → 单级 value 降序 → 名字（兜底保证同存档多次调用结果稳定，
    否则卡面写 A 朵、成功率按 B 朵算，又是「两张皮」）。

    这是 ``alchemy_rate_bonus`` 与 ``alchemy_flame_names`` 的**唯一**选择口径：
    改上限只改常量，加成数值与播报名单自动同步。
    """
    out: list[dict] = []
    for f in _numeric_flames():
        is_alchemy = (f.get("kind") == "alchemy") or (f.get("bonus") == "alchemy")
        if not is_alchemy:
            continue
        lv = _owned_level(p, f["name"])
        if lv <= 0:
            continue
        val = float(f["value"])
        out.append({"name": f["name"], "lv": lv, "value": val, "contrib": val * lv})
    out.sort(key=lambda x: (-x["contrib"], -x["value"], x["name"]))
    return out[:ALCHEMY_FLAME_LIMIT]


def alchemy_rate_bonus(p: dict) -> float:
    """炼药成功率加成（小数，未封顶；调用方封顶 CRAFT_RATE_CAP）。

    只累加加成最强的 :data:`ALCHEMY_FLAME_LIMIT` 朵（异火/兽火/新生异火同源），
    与 :func:`alchemy_flame_names` 同一份选择结果，卡面与实战成功率一致。
    """
    return sum(c["contrib"] for c in _alchemy_contribs(p))



def other_flame_ratio(p: dict) -> dict:
    """兽火 + 新生异火给六维的**比例**加成，供 stats() 单一源消费。

    返回 ``{"atk": x, "hp": y}``（小数，如 0.05 = +5%）。atk 作用于力量（攻击主
    属性），hp 作用于体力上限。alchemy 型不在此（走 alchemy_rate_bonus）。
    只有已收服（flames[name]>0）才计；等级 = 加成级数。
    """
    out = {"atk": 0.0, "hp": 0.0}
    for f in list(cfg.FUSION_FLAMES) + list(cfg.BEAST_FLAMES):
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


def alchemy_flame_names(p: dict) -> list[dict]:
    """播报用：真正参与炼药成功率的火焰明细（最多 :data:`ALCHEMY_FLAME_LIMIT` 朵）。

    2026-11-26 用户「异火兽火加成要在炼药播报体现，别太生硬」。判定与
    ``alchemy_rate_bonus`` **同一份选择结果**（``_alchemy_contribs``），只列
    「播报说在帮忙 → 成功率也真算了」的火，杜绝两张皮。identity 只保留异火 /
    兽火两种身份（新生异火归入异火），与玩家口径一致。

    返回 ``[{"name": str, "identity": "异火"|"兽火", "lv": int}]``，按加成降序。
    """
    beast_names = {f["name"] for f in cfg.BEAST_FLAMES}
    return [{"name": c["name"],
             "identity": "兽火" if c["name"] in beast_names else "异火",
             "lv": c["lv"]}
            for c in _alchemy_contribs(p)]
