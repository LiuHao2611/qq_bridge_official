# -*- coding: utf-8 -*-
"""爬塔会话状态机（2026-11-17）。

仿 duel_challenge.ChallengeStore：按 qq 隔离、惰性 TTL 超时、纯进程内存、不落盘。
本轮临时状态（当前层 / 临时 hp·mp / 属性增益 / 战斗效果 / 种子 / 宝箱记录）
只活在会话里，本轮结束即清空；只有 tower_best / 武器 写回玩家永久存档。

用户拍板：无限层冲榜、无中途退出刷奖励——本轮结束（死亡/收手/超时）才结算一次。
"""
from __future__ import annotations

import math
import random
import threading
import time

#: 会话超时（秒）：长时间无操作自动结算，不吞奖励
DEFAULT_TTL = 900          # 15 分钟

#: 战利品三选一有效期（秒）：超过就作废且**该层奖励失去**。
#: 2026-11-17 用户要求「凡是需要回答的业务都 1 分钟内回复，超时失效并失去奖励」。
#: 产品唯一源是 ``config.CHOICE_TTL``；此处默认值必须与之一致（有守门测试）。
DEFAULT_LOOT_TTL = 60.0


def new_state(qq, seed=None) -> dict:
    """开一轮新塔的初始会话状态。"""
    s = seed
    if s is None:
        s = str(qq) + ":" + str(int(time.time() * 1000))
    return {
        "qq": str(qq),
        "seed": str(s),
        "floor": 0,              # 已通过的层数（当前在第 floor+1 层）
        "temp_hp": None,         #  None = 用玩家满血；进入时初始化
        "temp_mp": None,
        "temp_bonus": {},        # {属性: 增量绝对值}，三选一累加
        "temp_effect_items": [],  # 三选一里 effect 类条目的累计（commands 直接读写此键）
        "chests": [],            # 本轮开箱记录 [{floor, item, weapon?}]
        "loot_taken": [],        # 已选战利品 key（展示用）
        "pending_loot": None,    # 当前待选的三选一 [{key,...}]
        "loot_deadline": 0.0,    # 待选的截止时间（epoch 秒）；0 = 无待选
        "loot_expired": False,   # 本轮是否发生过「战利品超时作废」（战报提示用）
        "settled": False,        # 是否已结算（防重复领取）
        "ts": time.time(),
    }


class TowerStore:
    """按 qq 索引的爬塔会话；惰性超时清理。"""

    def __init__(self, ttl: int = DEFAULT_TTL):
        self._data: dict[str, dict] = {}
        self._ttl = int(ttl)
        self._lock = threading.RLock()

    def _alive(self, st: dict) -> bool:
        return (time.time() - float(st.get("ts", 0))) <= self._ttl

    def begin(self, qq, seed=None) -> dict:
        """开新塔；若已有未结算会话则先作废旧会话（不结算，防刷）。"""
        with self._lock:
            self._data[str(qq)] = new_state(qq, seed)
            return dict(self._data[str(qq)])

    def get(self, qq) -> dict | None:
        """取当前会话；超时则清除并返回 None。"""
        with self._lock:
            st = self._data.get(str(qq))
            if st is None:
                return None
            if not self._alive(st):
                self._data.pop(str(qq), None)
                return None
            st["ts"] = time.time()
            return st

    def save(self, qq, st: dict) -> None:
        with self._lock:
            st["ts"] = time.time()
            self._data[str(qq)] = st

    def clear(self, qq) -> None:
        with self._lock:
            self._data.pop(str(qq), None)

    def rng(self, qq) -> callable:
        """按会话种子生成确定性 rng（同种子同层结果可复现）。"""
        st = self.get(qq)
        seed = st["seed"] if st else str(qq)
        return random.Random(seed + ":" + str(st["floor"] if st else 0)).random


STORE = TowerStore()


# ---------------------------------------------------------------------------
# 战利品三选一：60 秒时效（2026-11-17 用户要求）
# ---------------------------------------------------------------------------
def set_loot(st: dict, choices, ttl: float = DEFAULT_LOOT_TTL,
             now: float | None = None) -> dict:
    """挂起一组三选一，并写入截止时间。"""
    st["pending_loot"] = list(choices or [])
    st["loot_deadline"] = (time.time() if now is None else float(now)) + float(ttl)
    return st


def loot_left(st: dict | None, now: float | None = None) -> int:
    """待选剩余秒数（向上取整）。无待选 / 已过期返回 0。"""
    if not st or not st.get("pending_loot"):
        return 0
    deadline = float(st.get("loot_deadline") or 0)
    t = time.time() if now is None else float(now)
    return max(0, int(math.ceil(deadline - t)))


def expire_loot(st: dict | None, now: float | None = None) -> bool:
    """待选若已超时则作废（奖励失去）。返回「本次是否刚刚作废」。"""
    if not st or not st.get("pending_loot"):
        return False
    if loot_left(st, now) > 0:
        return False
    st["pending_loot"] = None
    st["loot_deadline"] = 0.0
    st["loot_expired"] = True
    return True


def take_loot(st: dict | None, idx: int, now: float | None = None) -> tuple:
    """消费第 ``idx``（0-based）个选项。返回 ``(loot, reason)``。

    reason：``ok`` 成功 / ``none`` 当前没有待选 / ``expired`` 已超时作废 /
    ``bad_index`` 越界。超时路径会把待选清掉——奖励不再补发（用户明确要求）。
    """
    if not st or not st.get("pending_loot"):
        return None, "none"
    if expire_loot(st, now):
        return None, "expired"
    choices = st["pending_loot"]
    try:
        i = int(idx)
    except (TypeError, ValueError):
        return None, "bad_index"
    if i < 0 or i >= len(choices):
        return None, "bad_index"
    loot = choices[i]
    st["pending_loot"] = None
    st["loot_deadline"] = 0.0
    return loot, "ok"
