# -*- coding: utf-8 -*-
"""game_tools/riven_recommend_attrs.py —— 紫卡词条 ↔ Warframe.Market 属性的权威映射层。

**数据源（唯一权威，不靠译名猜）**
    ``GET https://api.warframe.market/v2/riven/attributes``
    返回 50 条紫卡属性，每条给出 ``slug``（拍卖过滤参数 ``positive_stats`` /
    ``negative_stats`` 接受的值）、``gameRef``（DE 内部引用名）、``exclusiveTo``
    （合法武器类别）、``positiveOnly``、``i18n.zh-hans.name`` / ``i18n.en.name``。

**为什么必须换掉旧的 ``_DE_TO_WFM`` 手写表（2026-11-16 实测证据）**
    旧表把 6 条词条标成「WFM 确无此 slug」，其中 **Initial Combo / Additional Combo
    Count Chance 是错的**：
      * ``slug=channeling_damage``，``gameRef=WeaponMeleeComboInitialBonusMod``，
        ``en=Initial Combo``，``zh=初始连击`` —— 沿用「引导(channeling)」旧命名，
        按英文名猜 ``initial_combo`` 必然 400，于是被误判成「不存在」。
      * ``slug=chance_to_gain_extra_combo_count``，``en=Additional Combo Count Chance``。
    同一份接口还暴露 ``slam_attack_damage``（震地攻击伤害）与 3 条新派系
    （Orokin / Scaldra / Techrot），旧表全部缺失。手写表的维护成本与错判风险是
    这个模块存在的原因：**运行时按接口快照自动建索引，词条新增不需要改代码。**

**离线优先**
    快照随代码交付（``wfm_riven_attrs.json``，本目录），生产环境不依赖运行期网络；
    ``data/warframe_items/wfm_riven_attributes.json`` 若存在且比快照新则优先使用
    （运维可手工刷新）。两者都不在 → 显式 ``RivenAttrError``，绝不静默空表。
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Optional

#: 随代码交付的快照（git 跟踪、随镜像 COPY src 进容器）
SNAPSHOT_PATH = Path(__file__).with_name("wfm_riven_attrs.json")
#: 运维可刷新的缓存（data/ 是挂载卷；被 gitignore，故只能当覆盖层）
DATA_CACHE_PATH = Path(__file__).resolve().parents[4] / "data" / "warframe_items" / "wfm_riven_attributes.json"

#: DE 显示名 ↔ WFM 英文名的**术语**别名（不是数值，也不含任何武器特例）。
#: 只处理「同一个词条两家叫法不同」的情况；其余全部靠规范化自动匹配。
_ALIAS = {
    "Base Damage": "Damage",
    "Melee Damage": "Damage",
    "Fire Rate": "Fire Rate / Attack Speed",
    "Attack Speed": "Fire Rate / Attack Speed",
    "Damage vs Grineer": "Damage to Grineer",
    "Damage vs Corpus": "Damage to Corpus",
    "Damage vs Infested": "Damage to Infested",
}

#: 紫卡类别 → ``exclusiveTo`` 里的武器族名（接口值：melee/zaw/rifle/shotgun/pistol/kitgun/archgun）
_CLS_FAMILY = {
    "Melee": "melee", "Zaw": "zaw",
    "Rifle": "rifle", "Shotgun": "shotgun", "Pistol": "pistol",
    "Kitgun": "kitgun", "Archgun": "archgun",
}


class RivenAttrError(RuntimeError):
    """属性表缺失/损坏。调用方必须在卡面如实显示原因，不许回退到猜。"""


def _norm(s: str) -> str:
    """规范化：小写、只留字母数字，并去掉末尾的 ``damage``。

    ``Cold Damage`` 与接口的 ``Cold`` 因此归一到同一个键；``Electricity Damage``
    与 ``Electricity`` 同理。**不做同义词推断**（``Impact`` 不会被折成 ``Puncture``）。
    """
    k = re.sub(r"[^a-z0-9]", "", (s or "").lower())
    return k[:-6] if k.endswith("damage") and len(k) > 6 else k


_ATTRS: dict | None = None


def load_attrs() -> dict:
    """加载属性快照并建索引（进程内缓存）。

    返回 ``{"by_slug": {...}, "by_norm": {...}, "by_en": {...},
             "origin": str, "fetched_at": str}``。
    """
    global _ATTRS
    if _ATTRS is not None:
        return _ATTRS

    doc, origin = None, ""
    # 缓存层优先（运维刷新过就用新的）；坏文件直接跳过，不让它挡住随包快照
    for path, tag in ((DATA_CACHE_PATH, "data 缓存"), (SNAPSHOT_PATH, "随包快照")):
        try:
            if path.exists():
                doc = json.loads(path.read_text(encoding="utf-8"))
                origin = tag
                break
        except Exception:  # noqa: BLE001 — 坏缓存不致命，退下一层
            doc = None
    if not isinstance(doc, dict) or not isinstance(doc.get("attributes"), list):
        raise RivenAttrError(
            "紫卡属性表缺失或损坏（%s / %s）：无法把词条映射到 Warframe.Market 属性，"
            "拒绝凭空猜 slug" % (DATA_CACHE_PATH, SNAPSHOT_PATH))

    by_slug, by_norm, by_en = {}, {}, {}
    for a in doc["attributes"]:
        slug = (a.get("slug") or "").strip()
        if not slug:
            continue
        rec = {
            "slug": slug,
            "en": (a.get("en") or "").strip(),
            "zh": (a.get("zh") or "").strip(),
            "exclusive_to": list(a.get("exclusive_to") or []),
            "positive_only": bool(a.get("positive_only")),
            "group": a.get("group") or "default",
            "game_ref": a.get("game_ref") or "",
        }
        by_slug[slug] = rec
        if rec["en"]:
            by_en[rec["en"].lower()] = rec
            by_norm.setdefault(_norm(rec["en"]), rec)
    _ATTRS = {"by_slug": by_slug, "by_norm": by_norm, "by_en": by_en,
              "origin": origin, "fetched_at": str(doc.get("fetched_at") or ""),
              "count": len(by_slug)}
    return _ATTRS


def attr_of(en: str) -> Optional[dict]:
    """DE 词条名 → 属性记录（查不到返回 None，**不猜 slug**）。"""
    a = load_attrs()
    name = (en or "").strip()
    if not name:
        return None
    rec = a["by_en"].get(name.lower())
    if rec:
        return rec
    alias = _ALIAS.get(name)
    if alias:
        rec = a["by_en"].get(alias.lower())
        if rec:
            return rec
    return a["by_norm"].get(_norm(name))


def slug_of(en: str) -> str:
    """DE 词条名 → 拍卖过滤用 slug（查不到返回空串）。"""
    rec = attr_of(en)
    return rec["slug"] if rec else ""


def supports(en: str, riven_type: str) -> bool:
    """该词条是否适用于此紫卡类别（依据接口的 ``exclusiveTo``）。

    接口 ``exclusiveTo=[]`` 表示「全类别通用」（暴率/暴伤/基伤/元素/多重等）；
    非空表示仅限列出的武器族。未知紫卡类别 → 一律 False（宁可不出，不硬套）。
    """
    rec = attr_of(en)
    if not rec:
        return False
    fam = _CLS_FAMILY.get((riven_type or "").strip())
    if not fam:
        return False
    ex = rec["exclusive_to"]
    return (not ex) or (fam in ex)


def is_positive_only(en: str) -> bool:
    """``positiveOnly`` 词条（当前只有穿透）无法作为负面洗出。"""
    rec = attr_of(en)
    return bool(rec and rec["positive_only"])


def zh_of_slug(slug: str) -> str:
    """slug → 中文名（接口 ``i18n.zh-hans``；查不到原样返回 slug，不编译名）。"""
    rec = load_attrs()["by_slug"].get(slug or "")
    return (rec or {}).get("zh") or (slug or "")


def short_zh(en: str, is_melee=None) -> str:
    """DE 词条名 → 卡面短名（接口中文名过长时压缩，压缩规则是纯术语替换）。

    ``is_melee``：WFM 把枪的射速和近战攻速合成同一条
    ``fire_rate_/_attack_speed``（中文「射速/攻击速度」），卡面必须按武器类别
    二选一 —— 近战写「射速/攻速」是错的（2026-11-16 实拍发现）。
    传 ``None`` 表示类别未知，保留合并写法。
    """
    rec = attr_of(en)
    if not rec:
        return en
    zh = rec["zh"] or rec["en"]
    for a, b in (("攻击速度", "攻速"), ("射速/攻击速度", "射速/攻速"),
                 ("暴击几率", "暴率"), ("暴击率", "暴率"), ("暴击伤害", "暴伤"),
                 ("触发几率", "触发"), ("多重射击", "多重"),
                 ("连击持续时间", "连击持续"), ("额外连击数几率", "额外连击几率"),
                 ("滑行攻击暴击几率", "滑暴"), ("震地攻击伤害", "震地"),
                 ("重击准备速度", "重击蓄力"), ("在重击时的近战伤害", "重击伤"),
                 ("的几率来获得连击数", "获得连击几率"), ("攻击范围", "范围")):
        zh = zh.replace(a, b)
    if zh == "射速/攻速" and is_melee is not None:
        zh = "攻速" if is_melee else "射速"
    return zh
