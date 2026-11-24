# -*- coding: utf-8 -*-
"""限时增益（temp buff）—— 原著「短时间内」类丹药的唯一落点。

设计文档：``docs/限时增益系统_设计草案_2026-11-24.md``。

为什么单独一个模块
------------------
``core.py`` 长期贴着 200KB 提交红线（只剩 100 多字节），而这个系统的判定逻辑
（比例叠加 / 同名刷新 / 到期萎靡 / 时钟跳变钳位）有七八十行。放这里之后
``core.stats()`` 只多一行 ``temp_buff.merge(out, p)``，``use_item`` 只多一个
两行分支 —— 既不撑爆体积，也能被裸 Python 直接单测。

::

    p["temp_buffs"] = [
        {"k": "力量", "mul": 0.45, "start": 1764000000,
         "until": 1764000900, "src": "龙力丹"},
    ]

红线
----
1. **不写 ``p["bonus"]``**。写进去到期就得「精确扣回」，多端/宕机/换存档会永久
   留下假永久数（2026-11-18 假药事故的同类坑）。这里到期**自然消失**。
2. **按比例不按绝对值**。绝对值到高等级必然失效（五维丹踩过）。
3. **存 ``until`` 时间戳，不存剩余分钟**。否则休息/断线期间不走表。
4. **同名刷新，不叠加时长/倍率**，防囤一堆丹叠满。
5. 本模块纯函数、零 IO、**不 import core / config**（core 会 import 它）。
"""
from __future__ import annotations

import time

#: 同时生效的条数上限（主效果 + 到期萎靡，正常最多 2 条；留给将来扩展）
MAX_ITEMS = 6
#: 时钟跳变钳位：``until`` 比现在晚超过 30 天视为坏数据（手机无 RTC，校时可能乱跳）
MAX_AHEAD = 30 * 86400
#: ``start`` 比现在晚超过 1 天同样视为坏数据
MAX_START_AHEAD = 86400

#: 五维键（只有这些键能被限时增益放大；派生键如「斗气上限」走 mp_max 自然跟随）
STAT_KEYS = ("力量", "敏捷", "斗气", "防御", "体力")


def _now(now=None) -> int:
    return int(time.time() if now is None else now)


def _alive(it: dict, now: int) -> bool:
    """这条增益此刻是否生效（``start <= now < until``）。"""
    try:
        start, until = int(it.get("start", 0)), int(it.get("until", 0))
    except (TypeError, ValueError):
        return False
    return start <= now < until


def _same(it: dict, src: str, key: str) -> bool:
    return str(it.get("src") or "") == src and str(it.get("k") or "") == key


def add(p: dict, key: str, mul: float, minutes: float, src: str,
        now=None, delay_min: float = 0.0) -> dict:
    """写一条增益（同名同键**刷新**，不叠倍率）。

    ``delay_min`` >0 时表示「往后延一段才生效」（速灵风丹到期后的萎靡期）。
    """
    now = _now(now)
    start = now + int(delay_min * 60)
    until = start + int(float(minutes) * 60)
    item = {"k": str(key), "mul": round(float(mul), 4), "start": start,
            "until": until, "src": str(src)}
    items = [it for it in (p.get("temp_buffs") or []) if isinstance(it, dict)]
    kept = []
    for it in items:
        if _same(it, item["src"], item["k"]):
            # 同名刷新：取更晚的到期时间（连嗑两枚不该变短），倍率用最新一枚
            if int(it.get("until", 0)) > until and int(it.get("start", 0)) <= now:
                item["until"] = int(it["until"])
            continue
        kept.append(it)
    kept.append(item)
    # 条数上限：先丢「已过期」，再按 until 升序丢最早的
    kept.sort(key=lambda x: int(x.get("until", 0)))
    if len(kept) > MAX_ITEMS:
        kept = kept[-MAX_ITEMS:]
    p["temp_buffs"] = kept
    return item


def sweep(p: dict, now=None) -> bool:
    """清掉过期/坏数据，返回是否改过 ``p``（``store.get_player`` 每读一次调一次）。"""
    now = _now(now)
    items = p.get("temp_buffs")
    if not items:
        if items is not None:
            p.pop("temp_buffs", None)
            return True
        return False
    kept = []
    for it in items:
        if not isinstance(it, dict):
            continue
        try:
            start, until = int(it.get("start", 0)), int(it.get("until", 0))
            mul = float(it.get("mul", 0))
        except (TypeError, ValueError):
            continue
        # 坏数据：时钟乱跳导致的天外时间戳 / 倍率为 0 / 键名不认识
        if until <= now or start > now + MAX_START_AHEAD \
                or until > now + MAX_AHEAD or not mul \
                or str(it.get("k") or "") not in STAT_KEYS:
            continue
        kept.append(it)
    if len(kept) != len(items):
        if kept:
            p["temp_buffs"] = kept
        else:
            p.pop("temp_buffs", None)
        return True
    return False


def active(p: dict, now=None) -> list:
    """当前生效的增益（展示与结算共用同一口径）。"""
    now = _now(now)
    return [it for it in (p.get("temp_buffs") or [])
            if isinstance(it, dict) and _alive(it, now)]


def merge(out: dict, p: dict, now=None) -> dict:
    """把生效中的增益按比例并进面板（``core.stats()`` 唯一调用点）。

    同一键多条（不同来源）**先求和再乘一次**，避免复利；负值（萎靡）同样走这里。
    只动已存在的五维键 —— 不凭空造键，免得人物卡/战斗读到脏字段。
    """
    tot: dict = {}
    for it in active(p, now):
        k = str(it.get("k") or "")
        if k not in STAT_KEYS or k not in out:
            continue
        tot[k] = tot.get(k, 0.0) + float(it.get("mul", 0) or 0)
    for k, mul in tot.items():
        base = int(out.get(k, 0) or 0)
        out[k] = max(1, int(round(base * (1.0 + mul))))
    return out


def eat(p: dict, rec: dict, now=None) -> str:
    """服丹入口：按配方写增益，返回给玩家的一句话（``core.use_item`` 用）。

    配方字段：``buff_key``（默认力量）/ ``value``（百分点）/ ``buff_min``（分钟）/
    ``buff_penalty``（可选 ``{"min": 10, "mul": -0.15}``，到期后的萎靡期）。
    """
    key = str(rec.get("buff_key") or "力量")
    pct = int(rec.get("value") or 0)
    mins = float(rec.get("buff_min") or 0)
    name = str(rec.get("name") or "药力")
    add(p, key, pct / 100.0, mins, name, now=now)
    label = "%s +%d%%（%d 分钟）" % (key, pct, int(mins))
    pen = rec.get("buff_penalty") or {}
    if pen:
        pen_min = float(pen.get("min") or 0)
        pen_pct = int(round(float(pen.get("mul") or 0) * 100))
        if pen_min > 0 and pen_pct:
            add(p, key, pen_pct / 100.0, pen_min, name + "·萎靡",
                now=now, delay_min=mins)
            label += "；到期后 %d 分钟 %s %d%%" % (int(pen_min), key, pen_pct)
    return label


def battle_line(p: dict, now=None) -> str:
    """战报开场那一行；没有生效增益返回空串（调用方据此决定加不加）。

    与 ``lines`` 的区别：这里把同键多条**合并成一条 ×总倍率**，战报只要一行。
    """
    act = active(p, now)
    if not act:
        return ""
    now = _now(now)
    tot: dict = {}
    for it in act:
        k = str(it.get("k") or "")
        tot[k] = tot.get(k, 0.0) + float(it.get("mul", 0) or 0)
    parts = ["%s ×%.2f" % (k, 1.0 + mul) for k, mul in tot.items()]
    left = max(1, (min(int(it.get("until", 0)) for it in act) - now) // 60)
    return "💊 药力未散：%s · 最短剩 %d 分钟" % ("、".join(parts), left)


def lines(p: dict, now=None) -> list:
    """展示用：「⏳ 龙力丹 · 力量 +45% · 剩 12 分钟」。"""
    now = _now(now)
    out = []
    for it in sorted(active(p, now), key=lambda x: int(x.get("until", 0))):
        left = max(1, (int(it["until"]) - now) // 60)
        out.append("⏳ %s · %s %+d%% · 剩 %d 分钟"
                   % (it.get("src", "药力"), it.get("k", "?"),
                      int(round(float(it.get("mul", 0)) * 100)), left))
    return out
