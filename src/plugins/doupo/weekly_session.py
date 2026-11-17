# -*- coding: utf-8 -*-
"""周副本会话（2026-11-17）：7 层一命通关 / 奖励暂存锁定 / 落盘可恢复。

仿 tower_session.TowerStore：按 qq 隔离、惰性 TTL、原子写落盘（挂载卷）、失败抛错不静默。
与无限塔会话**分开存放**（否则互相顶掉）：``config.WEEKLY_SESSION_PATH`` 从 DATA_PATH
单源推导，容器里是 /app/data/doupo/weekly_session.json。

会话态：
  * floor           已通过层数（0..7）；下一层 = floor+1
  * temp_hp/temp_mp  本轮临时气血/斗气（满状态起步，None=取满）
  * staged          暂存奖励 {gold, exp, items:{名:数}, weapons:[名]}
                    ⚠ 通关前**不写永久存档**；失败/超时/退出整段丢弃（含武器）
  * week            本轮对应的 ISO 周 key（跨周自动作废）
"""
from __future__ import annotations

import json
import os
import random
import threading
import time

from . import config as cfg
from . import weekly_data as wd

#: 会话超时（秒）：与无限塔一致 6 小时；超时按"失败"处理——暂存奖励全清。
DEFAULT_TTL = 21600


def new_state(qq, week: str | None = None, seed=None) -> dict:
    """开一轮周副本的初始会话状态。"""
    s = seed if seed is not None else str(qq) + ":" + str(int(time.time() * 1000))
    return {
        "qq": str(qq),
        "week": str(week or wd.week_key()),
        "seed": str(s),
        "floor": 0,
        "temp_hp": None,
        "temp_mp": None,
        "staged": {"gold": 0, "exp": 0, "items": {}, "weapons": []},
        "loot_taken": [],
        "cleared": False,
        "ts": time.time(),
    }


def _log(level: str, msg: str) -> None:
    try:
        from nonebot.log import logger as _lg
    except Exception:  # noqa: BLE001
        import logging
        _lg = logging.getLogger("doupo")
    getattr(_lg, level)(msg)


class WeeklyStore:
    """按 qq 索引的周副本会话；惰性超时清理，落盘可跨重启恢复。"""

    def __init__(self, ttl: int = DEFAULT_TTL, path: str | None = None):
        self._data: dict[str, dict] = {}
        self._expired: dict[str, dict] = {}
        self._ttl = int(ttl)
        self._path = (cfg.WEEKLY_SESSION_PATH if path is None else path) or None
        self._lock = threading.RLock()
        self._load()

    # ---- 落盘 ----------------------------------------------------------
    def _load(self) -> None:
        if not self._path:
            return
        try:
            with open(self._path, encoding="utf-8") as f:
                raw = json.load(f)
        except FileNotFoundError:
            return
        except Exception as e:  # noqa: BLE001
            _log("error", "周副本会话读盘失败 %s：%r" % (self._path, e))
            return
        if not isinstance(raw, dict):
            _log("error", "周副本会话格式非法（非 dict）：%s" % self._path)
            return
        now = time.time()
        kept = dropped = 0
        for qq, st in raw.items():
            if not isinstance(st, dict):
                continue
            if (now - float(st.get("ts") or 0)) <= self._ttl:
                self._data[str(qq)] = st
                kept += 1
            else:
                dropped += 1
        if kept or dropped:
            _log("info", "周副本会话恢复 %d 条（超时丢弃 %d 条）" % (kept, dropped))

    def _flush(self) -> None:
        """原子写（同目录临时文件 + os.replace + fsync）。失败抛错，不静默。"""
        if not self._path:
            return
        parent = os.path.dirname(self._path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        tmp = self._path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self._data, f, ensure_ascii=False, separators=(",", ":"))
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self._path)
        except Exception as e:  # noqa: BLE001
            _log("error", "周副本会话写盘失败 %s：%r" % (self._path, e))
            raise

    # ---- 会话 ----------------------------------------------------------
    def _alive(self, st: dict) -> bool:
        return (time.time() - float(st.get("ts", 0))) <= self._ttl

    def begin(self, qq, week: str | None = None, seed=None) -> dict:
        with self._lock:
            qq = str(qq)
            self._data[qq] = new_state(qq, week, seed)
            self._flush()
            return dict(self._data[qq])

    def get(self, qq) -> dict | None:
        with self._lock:
            qq = str(qq)
            st = self._data.get(qq)
            if st is None:
                return None
            if not self._alive(st):
                self._data.pop(qq, None)
                self._expired[qq] = st
                self._flush()
                return None
            # 跨周会话自动作废（周本按周结算，上周残留不带入）
            if st.get("week") != wd.week_key():
                self._data.pop(qq, None)
                self._flush()
                return None
            st["ts"] = time.time()
            return st

    def take_expired(self, qq) -> dict | None:
        with self._lock:
            return self._expired.pop(str(qq), None)

    def save(self, qq, st: dict) -> None:
        with self._lock:
            st["ts"] = time.time()
            self._data[str(qq)] = st
            self._flush()

    def clear(self, qq) -> None:
        with self._lock:
            self._data.pop(str(qq), None)
            self._flush()

    def rng(self, qq) -> callable:
        st = self.get(qq)
        seed = st["seed"] if st else str(qq)
        return random.Random(seed + ":" + str(st["floor"] if st else 0)).random


STORE = WeeklyStore()
