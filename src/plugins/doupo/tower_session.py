# -*- coding: utf-8 -*-
"""爬塔会话状态机（2026-11-17）。

仿 duel_challenge.ChallengeStore：按 qq 隔离、惰性 TTL 超时、纯进程内存、不落盘。
本轮临时状态（当前层 / 临时 hp·mp / 属性增益 / 战斗效果 / 种子 / 宝箱记录）
只活在会话里，本轮结束即清空；只有 tower_best / 武器 写回玩家永久存档。

用户拍板：无限层冲榜、无中途退出刷奖励——本轮结束（死亡/收手/超时）才结算一次。

⚠ 2026-11-17 修正：**必须落盘**。原先纯内存，运维重建容器（改平衡时连重建 3 次）
就把玩家正在爬的层数清零了，用户原话「爬塔层数被你重置掉了，你是不是没把爬塔数据
保存好」。现在会话写 ``config.TOWER_SESSION_PATH``（跟 doupo.json 同目录，容器里就在
挂载卷 /app/data/doupo/ 下），重启/重建都能接着爬；TTL 也从 15 分钟拉到 6 小时。
"""
from __future__ import annotations

import json
import math
import os
import random
import threading
import time

from . import config as cfg

#: 会话超时（秒）：长时间无操作自动结算，不吞奖励。
#: 2026-11-17 从 900（15 分钟）拉到 6 小时：会话已落盘，短 TTL 只会让
#: 「吃完饭回来继续爬」变成「层数没了」。真正闲置由下面的 _expired 补结算兜住。
DEFAULT_TTL = 21600         # 6 小时

#: 战利品三选一有效期（秒）：超过就作废且**该层奖励失去**。
#: 2026-11-17 用户要求「凡是需要回答的业务都 1 分钟内回复，超时失效并失去奖励」。
#: 产品唯一源是 ``config.CHOICE_TTL``；此处默认值必须与之一致（有守门测试）。
DEFAULT_LOOT_TTL = 60.0


def new_state(qq, seed=None, floor: int = 0) -> dict:
    """开一轮新塔的初始会话状态。

    :param floor: 起始**已通过层数**（「爬塔」「自动爬塔」传 tower_checkpoint，
                  即从上次止步层的下一层开打，满状态；2026-11-25 电梯已删除）。
                  默认 0 = 从第 1 层重开。只影响层数起点，临时增益一律清空。
    """
    s = seed
    if s is None:
        s = str(qq) + ":" + str(int(time.time() * 1000))
    return {
        "qq": str(qq),
        "seed": str(s),
        "floor": max(0, int(floor)),  # 已通过的层数（当前在第 floor+1 层）
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


def _log(level: str, msg: str) -> None:
    """写日志：优先 nonebot.log（线上 loguru），裸跑单测时退回 stdlib。"""
    try:
        from nonebot.log import logger as _lg
    except Exception:  # noqa: BLE001
        import logging

        _lg = logging.getLogger("doupo")
    getattr(_lg, level)(msg)


class TowerStore:
    """按 qq 索引的爬塔会话；惰性超时清理，**落盘可跨重启恢复**。"""

    def __init__(self, ttl: int = DEFAULT_TTL, path: str | None = None):
        self._data: dict[str, dict] = {}
        #: 刚超时、尚未补结算的会话（qq → state）。命令层取走后结算，避免白丢奖励。
        self._expired: dict[str, dict] = {}
        self._ttl = int(ttl)
        self._path = (cfg.TOWER_SESSION_PATH if path is None else path) or None
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
            # 不静默：明确报错，但保留空会话（否则连命令都起不来）
            _log("error", "爬塔会话读盘失败 %s：%r" % (self._path, e))
            return
        if not isinstance(raw, dict):
            _log("error", "爬塔会话格式非法（非 dict）：%s" % self._path)
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
            _log("info", "爬塔会话恢复 %d 条（超时丢弃 %d 条）" % (kept, dropped))

    def _flush(self) -> None:
        """原子写（同目录临时文件 + os.replace）。失败**抛错**，不静默降级。"""
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
            _log("error", "爬塔会话写盘失败 %s：%r" % (self._path, e))
            raise

    # ---- 会话 ----------------------------------------------------------
    def _alive(self, st: dict) -> bool:
        return (time.time() - float(st.get("ts", 0))) <= self._ttl

    def begin(self, qq, seed=None, floor: int = 0) -> dict:
        """开新塔；若已有未结算会话则先作废旧会话（不结算，防刷）。

        ``floor`` > 0 用于电梯直达：从该已通过层数起、满状态挑战下一层。
        """
        with self._lock:
            qq = str(qq)
            self._data[qq] = new_state(qq, seed, floor=floor)
            self._flush()
            return dict(self._data[qq])

    def get(self, qq) -> dict | None:
        """取当前会话；超时则移入「待补结算」并返回 None。"""
        with self._lock:
            qq = str(qq)
            st = self._data.get(qq)
            if st is None:
                return None
            if not self._alive(st):
                self._data.pop(qq, None)
                self._expired[qq] = st     # 超时也要结算，不能白丢进度
                self._flush()
                return None
            st["ts"] = time.time()
            return st

    def take_expired(self, qq) -> dict | None:
        """取走「刚超时、还没结算」的会话（一次性）。"""
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
