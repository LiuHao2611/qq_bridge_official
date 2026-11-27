# -*- coding: utf-8 -*-
"""斗技阶级 —— 原著口径的唯一真源（2026-11-26）。

## 为什么要有这个模块

用户原话：「不要搞普通 高阶 特殊的说法了，严格按照原著的阶级表达」。

``普通 / 高阶 / 特殊`` 是**本项目的数值档**，不是原著说法：

* ``config.SKILL_TIER_DMG`` = 1.0 / 1.4 / 1.8（伤害倍率档）
* ``config.SKILL_TIER_MP_FRAC``（斗气消耗）
* ``config.SKILL_TIER_UNLOCK``（解锁等级）

原著里斗技只按 **天 / 地 / 玄 / 黄** 四阶排，每阶再分 **低级 / 中级 / 高级**；
另有不分阶的 **秘法**（焚决、天火三玄变一类）。玩家在卡面上该看到「玄阶中级」，
而不是「高阶」。

因此本模块只做一件事：把 ``skill_book`` 的 ``rank`` 解析成

* **大阶**（``fam_of``）—— 卡面分组用，5 个值；
* **小阶序**（``level_of``）—— 同组内排序用，1/2/3。

``tier`` 继续只做数值，**不进卡面**。

## 🚨 不许把 rank 和 tier 绑死

两者是不同维度：``tier`` 是平衡档（改它会动战斗数值），``rank`` 是原著阶级
（改它只动展示）。数据里确实存在 ``tier=特殊`` 而 ``rank=天阶低级`` 的条目
（龙凰古甲 —— 原著无明确阶级标注，按其龙族顶尖体术定位取天阶低级，但伤害
仍走特殊档）。任何「由 rank 反推 tier」的写法都会把这两条线重新缠死，
战斗数值会跟着卡面文案漂移，禁止。
"""
from __future__ import annotations

#: 大阶，由低到高。卡面分组顺序就是这个顺序。
FAMILIES: tuple[str, ...] = ("黄阶", "玄阶", "地阶", "天阶", "秘法")

#: 同阶内的小阶，由低到高。
LEVELS: tuple[str, ...] = ("低级", "中级", "高级")

#: 旧的三阶说法。**只允许出现在数值层与兼容代码里，禁止再出现在卡面上。**
LEGACY_TIERS: tuple[str, ...] = ("普通", "高阶", "特殊")

#: 未标阶 / 解析失败时的分组标题（排在最后，不与任何大阶混）。
UNKNOWN = "未标阶"


def fam_of(rank) -> str:
    """``"玄阶中级"`` → ``"玄阶"``；``"秘法"`` → ``"秘法"``；认不出 → ``"未标阶"``。

    先判秘法：秘法在原著里不属四阶，字符串里也不会带「阶」字以外的阶名，
    但把判断放最前可以防住「秘法（天阶）」这类混写。
    """
    s = str(rank or "")
    if "秘法" in s:
        return "秘法"
    for fam in FAMILIES:
        if fam in s:
            return fam
    return UNKNOWN


def level_of(rank) -> int:
    """小阶序：低级 1 / 中级 2 / 高级 3；秘法与未标阶 0。

    0 排在 1 前面 —— 「天阶」不带小阶的写法（数据里没有，但补录时可能出现）
    应该压在本阶最前，而不是被当成「高于天阶高级」。
    """
    s = str(rank or "")
    for i, lv in enumerate(LEVELS, 1):
        if lv in s:
            return i
    return 0


def sort_key(rank) -> tuple[int, int]:
    """排序键：(大阶序, 小阶序)。未标阶排最后。"""
    fam = fam_of(rank)
    idx = FAMILIES.index(fam) if fam in FAMILIES else len(FAMILIES)
    return (idx, level_of(rank))


def group_label(entries) -> str:
    """一组斗技的**阶位标题**（E4 2026-11-27）：实际出现的大阶，去重保序。

    文本页过去按数值档写「普通 / 高阶 / 特殊」当分组标题 —— 那是内部档位名，
    卡面早已改原著阶级，两处说法打架。这里从条目自己的 ``rank`` 反推标题，
    所以「特殊档里混了一门天阶」（龙凰古甲）也不会出现标题与内容不符。
    """
    seen: list[str] = []
    for e in entries or ():
        fam = fam_of((e or {}).get("rank"))
        if fam not in seen:
            seen.append(fam)
    seen.sort(key=lambda f: FAMILIES.index(f) if f in FAMILIES else len(FAMILIES))
    return "·".join(seen) if seen else UNKNOWN


def is_legal(rank) -> bool:
    """是不是一个合法的原著阶别写法（守门用）。"""
    return fam_of(rank) in FAMILIES


def legal_ranks() -> tuple[str, ...]:
    """全部合法写法，供报错时列清单（4 阶 × 3 小阶 + 秘法）。"""
    out = [f + lv for f in FAMILIES if f != "秘法" for lv in LEVELS]
    return tuple(out) + ("秘法",)
