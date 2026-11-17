# -*- coding: utf-8 -*-
"""爬塔会话状态机（2026-11-17）。

仿 duel_challenge.ChallengeStore：按 qq 隔离、惰性 TTL 超时、纯进程内存、不落盘。
本轮临时状态（当前层 / 临时 hp·mp / 属性增益 / 战斗效果 / 种子 / 宝箱记录）
只活在会话里，本轮结束即清空；只有 tower_best / 武器 写回玩家永久存档。

用户拍板：无限层冲榜、无中途退出刷奖励——本轮结束（死亡/收手/超时）才结算一次。
"""
from __future__ import annotations

import random
import threading
import time

#: 会话超时（秒）：长时间无操作自动结算，不吞奖励
DEFAULT_TTL = 900          # 15 分钟


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
