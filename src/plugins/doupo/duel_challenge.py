# -*- coding: utf-8 -*-
"""无惩罚切磋 · 挑战会话状态机（纯逻辑、零依赖、可直接单测）。

为什么单独成模块
----------------
发起 → 应战 是两段式交互，中间要维护「谁向谁挑战、在哪个群、何时发起」。
把它换成纯数据状态机，好处：

* 不碰 nonebot / store / asyncio timer，零依赖 runner 可以完整验收；
* 超时用「惰性判定」而不是 asyncio.TimerHandle——进程重启不残留半开会话，
  也不会因为事件循环没跑起来导致 timer 永不触发；
* 会话按 ``(group, target)`` 索引，实现「仅本群」隔离：A 群的挑战
  不会被 B 群的同名 openid 冒领，跨群串聊拿不到别人的邀请。

生命周期：ask() 建/覆盖 → take() 一次性消费（应战）→ decline() 放弃；
pending_for() 只查看不消费，超时会顺手清理。
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

#: 默认挑战有效期（秒）。config.DUEL_CHALLENGE_TTL 是产品唯一源，构造时传入。
DEFAULT_TTL = 60.0


@dataclass
class Challenge:
    challenger: str          # 发起者 qq / openid
    target: str              # 被挑战者 qq / openid
    group: str               # 群作用域（私聊 pv: 前缀由命令层拦掉）
    created_at: float = field(default_factory=time.time)

    def expired(self, ttl: float, now: float | None = None) -> bool:
        t = time.time() if now is None else now
        return t - self.created_at > ttl


class ChallengeStore:
    """按 (group, target) 隔离的轻量挑战会话。"""

    def __init__(self, ttl: float = DEFAULT_TTL) -> None:
        self._ttl = float(ttl)
        self._data: dict[tuple[str, str], Challenge] = {}

    @staticmethod
    def key(group, target) -> tuple[str, str]:
        return (str(group), str(target))

    def ask(self, challenger, target, group, now: float | None = None) -> Challenge:
        """发起/覆盖挑战：同一目标在同一群只保留最新一条。"""
        key = self.key(group, target)
        c = Challenge(str(challenger), str(target), str(group))
        if now is not None:
            c.created_at = float(now)
        self._data[key] = c
        return c

    def pending_for(self, target, group, now: float | None = None) -> Challenge | None:
        """查看待应战挑战（不消费）；超时则清理并返回 None。"""
        key = self.key(group, target)
        c = self._data.get(key)
        if c is None:
            return None
        if c.expired(self._ttl, now):
            self._data.pop(key, None)
            return None
        return c

    def take(self, target, group, now: float | None = None) -> Challenge | None:
        """应战：一次性消费。超时或不存在返回 None。"""
        c = self.pending_for(target, group, now)
        if c is None:
            return None
        self._data.pop(self.key(group, target), None)
        return c

    def decline(self, target, group) -> bool:
        """婉拒：清掉该挑战，返回是否确实有挑战被取消。"""
        return self._data.pop(self.key(group, target), None) is not None

    def __len__(self) -> int:
        return len(self._data)
