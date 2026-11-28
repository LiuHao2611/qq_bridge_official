# -*- coding: utf-8 -*-
"""九星突破 BOSS 战败后的原著嘲讽台词池（2026-11-28，用户拍板）。

设计口径（唯一真源）：
  * **原著台词为主，逐字核对**。基础池直接复用 ``config.BREAK_BOSSES[].quotes``
    ——那一批已带章节出处、逐字对齐过原著（见 config 里每条的 source 注释）。
    本模块只做「战败收尾取一句」的分派，不重写、不改写那些台词。
  * **补充台词必须带章节出处**，逐字对照原著正文，见 ``SUPPLEMENTAL``。
    二手解读 / AI 生成文案一律不收。
  * **无原著台词的 BOSS 不代编**：狼头佣兵·穆蛇、紫晶翼狮王（魔兽）、
    魂天帝在 config 里 quotes 为空，本模块也不给它们造词——战败就只出
    ``boss_fail`` 正文，不附台词。魔兽不会开口，硬编反而破坏原著感。

为什么独立成文件（而不是塞进 config.py）
------------------------------------------
config.py 已到 204730 B，贴着 200KB = 204800 B 的单文件红线，再塞就破线。
沿用 weapon_curve.py / cultivate_balance.py 的拆分做法，新增数值池独立成模块，
config 一行不改。
"""
from __future__ import annotations

import random

#: 独立随机源：**绝不复用调用方注入的 rng**。core 的突破战 rng 被测试注入以
#: 控制胜败（见 test_nine_star_breakthrough），嘲讽若共享会推进其序列、破坏
#: 既有胜败断言。战败台词用本实例，与战斗判定彻底解耦。
_PICK_RNG = random.Random()

try:
    from . import config as cfg
except (ImportError, ValueError):  # 单文件直载兜底
    import config as cfg  # type: ignore[no-redef]

#: 本次新增、已逐字对照原著正文的补充嘲讽。key 必须与
#: ``config.BREAK_BOSSES[].name`` 完全一致；值为 (台词, 原著章节出处)。
#: 只收能在原著正文里逐字找到的句子，出处写进注释方便复核。
SUPPLEMENTAL: dict[str, tuple[tuple[str, str], ...]] = {
    "魂殿摘星老鬼": (
        # 第一千一百九十九章「摘星老鬼」：萧炎燃三玄变后，摘星老鬼笑声
        ("终于打算拼命了么", "第一千一百九十九章「摘星老鬼」"),
        # 第八百七十二话：遗迹中忌惮异火，压下杀意后的狠话
        ("待得遗迹之事完毕，老夫再来好好收拾你！", "第八百七十二话「奇怪的卷轴」"),
    ),
    "药老·药尘": (
        # 第一千零一十章「一星斗宗」：魂殿紫衣老者劝降，药老冷笑回绝
        ("老夫可不是你这等软骨头", "第一千零一十章「一星斗宗」"),
    ),
}

#: 明确无原著台词、不代编的 BOSS（守门断言：它们取出的池必须为空）。
NO_CANON_LINES: frozenset[str] = frozenset({
    "狼头佣兵·穆蛇", "紫晶翼狮王", "魂天帝",
})


def taunt_pool(name: str) -> tuple[str, ...]:
    """返回该 BOSS 的战败嘲讽池（原著 quotes + 带出处的补充）。

    未收录 / 无原著台词的 BOSS 返回空元组，调用方据此不附台词。
    """
    ch = next((c for c in cfg.BREAK_BOSSES if c.get("name") == name), None)
    if ch is None:
        return ()
    base = tuple(str(s) for s in (ch.get("quotes") or ()) if s)
    extra = tuple(t for t, _src in SUPPLEMENTAL.get(name, ()))
    # 去重且保持顺序；quotes 里的句子已核对，补充句带出处，合起来做池。
    out: list[str] = []
    for s in base + extra:
        if s not in out:
            out.append(s)
    return tuple(out)


def pick_taunt(name: str, rnd=None) -> str:
    """从池里随机取一句战败嘲讽；池空返回空串（调用方不附加台词）。

    ``rnd`` 可注入以做确定性测试，线上 None 用 ``random.random``。
    """
    pool = taunt_pool(name)
    if not pool:
        return ""
    r = (rnd or _PICK_RNG.random)()
    idx = int(r * len(pool))
    idx = max(0, min(len(pool) - 1, idx))
    return pool[idx]
