# -*- coding: utf-8 -*-
"""斗技图鉴 · 阶位规则与记录工厂（纯数据，零副作用，不 import nonebot）。

为什么单独成文件（2026-11-27 拆分）
-----------------------------------
``skill_book.py`` 曾一路涨到 196 KB，逼近项目 200 KB 单文件红线。其中 165 KB 是
逐条数据（``BOOK_ROWS``），真正需要人读的逻辑只有阶位规则和 ``_s()`` 工厂。
拆成三层，职责单一：

    skill_book_schema.py  阶位常量 + tier_of() + _s()   ← 本文件，逻辑
    skill_book_data.py    BOOK_ROWS 逐条斗技数据         ← 纯数据，最常改
    skill_book.py         索引 / 分派 / 卡片短释         ← 对外唯一入口

``skill_book.py`` 仍是唯一对外入口，``_BOOK_ROWS`` / ``tier_of`` / ``ALLOWED_*``
全部原样再导出，``_gen_book_doc.py`` 等以顶层模块方式 import 的脚本不受影响。
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# 阶位 → 三阶 / 默认数值
# ---------------------------------------------------------------------------
#: 阶位首字 → 三阶归类
_TIER_BY_PREFIX = {
    "黄": "普通", "玄": "普通",
    "地": "高阶", "天": "高阶",
}

#: 没有「黄玄地天」前缀的，按性质直接归特殊
_SPECIAL_RANKS = ("秘法", "血脉", "体质", "异火", "防身技", "瞳术", "绝学", "秘术")

#: 各阶默认斗气占比（与 config.SKILL_TIER_MP_FRAC 对齐）
_MP_BY_TIER = {"普通": 20, "高阶": 34, "特殊": 50}

#: 各阶默认冷却
_CD_BY_TIER = {"普通": 2, "高阶": 3, "特殊": 3}

#: 阶位 → 默认伤害系数（100 = 攻击力 100%）
_DMG_BY_RANK = {
    "黄阶低级": 95, "黄阶中级": 100, "黄阶高级": 108,
    "玄阶低级": 122, "玄阶中级": 130, "玄阶高级": 140,
    "地阶低级": 160, "地阶中级": 170, "地阶高级": 185,
    "天阶低级": 200, "天阶中级": 220, "天阶高级": 240,
}

#: 斗者起始等级 = config.realm_lv(1)（斗之气 9 段之后的第一个等级）。
#: 用户 2026-11-27 拍板 Q1②「分技与功法」：**功法与玄阶以上斗技须斗者**，
#: 黄阶斗技则允许斗之气按段位修习（段位写在显式 unlock 里，如碎石掌=3）。
#: 本模块刻意不 import config（图鉴可被 _gen_book_doc.py 顶层非包导入），
#: 由 test_skill_gate 断言本常量恒等于 config.realm_lv(1)，防两处手抄漂移。
DOUZHE_LV = 10

#: 需要斗者资格才能修习的阶位首字（玄阶及以上，含地阶、天阶）
_ADVANCED_HEAD = ("玄", "地", "天")


def learn_floor(rank: str, kind: str) -> int:
    """境界闸：这门斗技最低要什么境界才有资格修习。

    只表达「境界资格」，不含段位 / 显式解锁等级（那是 ``unlock`` 的职责）。
    黄阶斗技返回 1 —— 斗之气可按段位修，与图鉴里的原著引文
    （碎石掌三段 / 劈山掌五段 / 铁山拳七段）对齐。
    """
    if str(kind or "") == "功法" or str(rank or "")[:1] in _ADVANCED_HEAD:
        return DOUZHE_LV
    return 1


#: 特殊阶的解锁等级（按性质分档；未列出的特殊阶默认 40）
_UNLOCK_BY_RANK = {
    # 高阶（地/天）：同属「高阶」一档斗气单价，但解锁仍按原著阶位递进
    "地阶低级": 20, "地阶中级": 30, "地阶高级": 40,
    "天阶低级": 50, "天阶中级": 60, "天阶高级": 70,
    # 特殊阶
    "秘法": 40, "秘术": 40, "瞳术": 40,
    "体质": 50, "血脉": 50, "防身技": 50,
    "异火": 60,
}

ALLOWED_KINDS = ("攻击", "防御", "增益", "控制", "身法", "召唤", "秘法", "功法", "技艺")
ALLOWED_SCOPES = ("单体", "群体", "自身", "直线", "场地", "队友")


def tier_of(rank: str) -> str:
    """阶位字符串 → 三阶。"""
    r = str(rank or "")
    head = r[:1]
    if head in _TIER_BY_PREFIX:
        return _TIER_BY_PREFIX[head]
    for s in _SPECIAL_RANKS:
        if s in r:
            return "特殊"
    return "特殊"


def _s(name, rank, kind, scope, desc, source, novel="", mp=None, cd=None, dmg=None,
       unlock=None, role="", effects=None, gate="", tier=None):
    """建一条记录；未指定的数值按阶位补默认。

    2026-11-15 多维度重构：新增 ``role/effects/gate``（主定位 / 效果原子数组 /
    门控代价）。三者都有默认值，**其余 160+ 门斗技完全不受影响**；只有天冥宗
    天冥宗 15 门（本宗 11 + 通用 4，见 FACTION_SKILLS/COMMON_SKILLS）显式填写，由
    ``tools/check_skill_multidim.py`` 门禁兜底，防止低数值技能退化成纯数字。

    2026-11-26 ``tier`` 参数：**只有「原著阶级」与「数值档」故意不一致时才传**。
    卡面已改按原著阶级展示（见 ``skill_rank``），而 tier 管的是伤害倍率 / 斗气
    消耗 / 解锁等级。个别原著无明确阶级标注的技（龙凰古甲）需要一个好看的阶级名，
    但数值必须留在原档位——传 ``tier`` 钉住，不让 ``tier_of(rank)`` 顺手改平衡。
    """
    tier = tier_of(rank) if tier is None else tier

    if unlock is None:
        unlock = _UNLOCK_BY_RANK.get(str(rank), 40 if tier == "特殊" else 1 if tier == "普通" else 20)
    # 境界闸折进 unlock：learnable / 卡面「LvN 解锁」/ learn_named「差几级」
    # 全部只读 unlock 一个字段，改这里就全链路一致，无需逐处补丁。
    unlock = max(int(unlock), learn_floor(rank, kind))
    return {
        "name": name,
        "rank": rank,
        "tier": tier,
        "kind": kind,
        "scope": scope,
        "desc": desc,
        "source": source,
        "novel": novel,
        "mp": _MP_BY_TIER[tier] if mp is None else mp,
        "cd": _CD_BY_TIER[tier] if cd is None else cd,
        "dmg": _DMG_BY_RANK.get(rank, 160) if dmg is None else dmg,
        "unlock": int(unlock),
        "role": role,
        "effects": list(effects) if effects else [],
        "gate": gate,
    }
