# -*- coding: utf-8 -*-
"""丹雷命令层（异步）：读傀儡上下文 → 把挡下的雷淬炼进傀儡库 → 出玩家文案。

分层
----
``danlei.py`` 同步纯函数（判定/伤害/文案）；``core.craft`` 成丹后调它算判定、扣气血，
结果挂 ``res["danlei"]``。本模块只做两件异步事：

* :func:`has_puppet` —— 读库判断有没有出战傀儡，供 core 掷抵挡骰前引用；
* :func:`finish` —— 挡下时读当前出战傀儡、调 ``puppet_core.temper()``（与「傀儡 淬炼」
  口令同源）、写回独立傀儡库，并组装玩家可见文案。

为什么不在 core 里做
--------------------
core 是同步纯函数、不读库；傀儡在 ``puppet_data.sqlite`` 走异步 pstore。同步/异步
边界必须在命令层。本模块内聚这段，让贴红线的 commands.py 只加极薄几行接线。

单一真源：淬炼一律走 ``puppet_core.temper``；写回列白名单与 ``commands_puppet`` 一致
（改列须同步）；玩家气血已在 core 扣完，这里不二次写 players 库。
"""
from __future__ import annotations

from .puppet_store import PUPPET_STORE as DEFAULT_PUPPET_STORE

# 写回傀儡库的列白名单 —— 与 commands_puppet._PUPPET_COLUMNS 同源，改列须同步。
_PUPPET_COLUMNS = frozenset({
    "name", "tier", "tier_name", "skin", "body_name", "body_realm", "body_star",
    "body_fresh", "body_fresh_until", "core_tier", "core_attr", "soul_source",
    "soul_strength", "soul_fed", "mind_stripped", "metal_name", "metal_grade",
    "fire_tier", "fire_attr", "purity", "bonded", "awakened", "origin", "damaged",
})


async def has_puppet(qq, pstore=None) -> bool:
    """是否有出战傀儡（命令层异步读库；core 掷抵挡骰的前提）。"""
    pstore = pstore or DEFAULT_PUPPET_STORE
    qq = str(qq)
    roster = await pstore.list_puppets(qq)
    if not roster:
        return False
    elig = await pstore.eligibility(qq)
    return any(x["id"] == elig["active_id"] for x in roster)


async def finish(qq, res: dict, pstore=None) -> str:
    """丹雷落库 + 文案。无丹雷返回空串，命令层追加前判空即可。

    ``res["danlei"]`` 由 core 挂：含 grade/hurt/n/blocked/hurt_total/hurt_done。
    有挡下且有出战傀儡 → 每道挡下的雷各淬炼一次（temper 与「傀儡 淬炼」同源）；
    无挡下/无傀儡 → 不写库，只出「硬扛」文案。玩家气血 core 已扣，这里不重复写。
    """
    from . import danlei
    from . import puppet_core as pc

    info = (res or {}).get("danlei")
    if not info:
        return ""
    blocked = int(info.get("blocked") or 0)
    name, purity, maxed = "", None, False
    if blocked > 0:
        pstore = pstore or DEFAULT_PUPPET_STORE
        qq = str(qq)
        roster = await pstore.list_puppets(qq)
        if roster:
            elig = await pstore.eligibility(qq)
            cur = next((x for x in roster if x["id"] == elig["active_id"]), roster[0])
            new = dict(cur)
            for _ in range(blocked):
                new = dict(new, **pc.temper(new))
            payload = {k: v for k, v in new.items() if k in _PUPPET_COLUMNS}
            await pstore.update_puppet(qq, cur["id"], **payload)
            name = pc.display_name(cur)
            purity = int(new.get("purity", 0))
            maxed = bool(new.get("maxed")) or purity >= pc.purity_hard_max()
    return danlei.line(info, int(info.get("hurt_done") or 0), name, purity, maxed)
