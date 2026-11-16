# -*- coding: utf-8 -*-
"""斗破·机遇「有奖竞猜」答题会话（纯逻辑、零依赖、可直接单测）。

为什么单独成模块
----------------
原版「有奖竞猜」现在改成**真选项按钮问答**：

1. 任务途中掷到竞猜时，只把题目 + 选项按钮发给玩家，**不发奖、不显示答案**；
2. 玩家点某个选项 → 回调携带 ``doupo:quiz:<token>:<idx>`` → 这里判定对错，
   答对才发奖。

题目要求
--------
* **不写盘**：答题态是短时交互，放进程内存即可；进程重启即失效（和
  ``duel_challenge`` 会话同一原则），绝不污染 ``doupo.json`` 存档 schema。
* **答案只在服务端**：按钮 data 只带 ``token + 选项下标``，正确答案不存在
  data 里，玩家翻包也看不到答案。
* **一次性消费**：答完（对或错）立即清除，防止重复点击重复领奖。
* **惰性超时**：不启 asyncio timer，超时在读取时顺手清理（进程重启不残留）。
* **一人一题**：按 qq（openid）索引，新题覆盖旧题。
"""
from __future__ import annotations

import secrets
import time
from contextvars import ContextVar
from dataclasses import dataclass, field

#: 答题有效期（秒）。超时后选项按钮点击会被判为「题目已过期」。
DEFAULT_TTL = 300.0


@dataclass
class Quiz:
    token: str
    qq: str
    question: str
    options: list[str]
    answer: int                 # 正确选项的下标（0-based），只在服务端内存
    reward: dict
    created_at: float = field(default_factory=time.time)

    def expired(self, ttl: float, now: float | None = None) -> bool:
        t = time.time() if now is None else now
        return t - self.created_at > ttl


@dataclass
class AnswerResult:
    valid: bool                 # 是否有效作答（token 匹配 + 未过期 + 有会话）
    correct: bool
    answer_idx: int             # 正确答案下标（错误时也回传，供展示）
    quiz: Quiz | None
    reason: str = ""            # valid=False 时的原因（expired / no_session / bad_token）


class QuizStore:
    """按 qq（openid）索引的轻量答题会话；进程级单例见模块末尾 :data:`STORE`。"""

    def __init__(self, ttl: float = DEFAULT_TTL) -> None:
        self._ttl = float(ttl)
        self._data: dict[str, Quiz] = {}

    def start(self, qq, question, options, answer: int, reward,
              now: float | None = None) -> Quiz:
        """发起一题（同一玩家新题覆盖旧题），返回含随机 token 的会话。"""
        q = Quiz(
            token=secrets.token_hex(4),
            qq=str(qq),
            question=str(question),
            options=[str(x) for x in options],
            answer=int(answer),
            reward=dict(reward or {}),
            created_at=(time.time() if now is None else float(now)),
        )
        self._data[q.qq] = q
        return q

    def pending_for(self, qq, now: float | None = None) -> Quiz | None:
        """查看待答题目（不消费）；超时顺手清理。"""
        q = self._data.get(str(qq))
        if q is None:
            return None
        if q.expired(self._ttl, now):
            self._data.pop(str(qq), None)
            return None
        return q

    def answer(self, qq, token, idx, now: float | None = None) -> AnswerResult:
        """作答并一次性消费该题。token 必须匹配、题目未过期。

        答对/答错都清除会话——防止连续点击重复领奖；错误也回传正确答案下标，
        由调用方决定是否在回复里公布答案。
        """
        q = self.pending_for(qq, now)
        if q is None:
            return AnswerResult(False, False, -1, None, "no_session")
        if str(token) != q.token:
            return AnswerResult(False, False, q.answer, q, "bad_token")
        try:
            choice = int(idx)
        except (TypeError, ValueError):
            return AnswerResult(False, False, q.answer, q, "bad_index")
        if choice < 0 or choice >= len(q.options):
            return AnswerResult(False, False, q.answer, q, "bad_index")
        self._data.pop(str(qq), None)   # 一次性消费，先清再判，杜绝并发重复领奖
        return AnswerResult(True, choice == q.answer, q.answer, q)

    def clear(self, qq) -> None:
        self._data.pop(str(qq), None)

    def peek_by_token(self, token: str) -> Quiz | None:
        """按 token 反查待答题（供发送层取选项文字挂按钮；不消费、不校验 qq）。"""
        for q in self._data.values():
            if q.token == str(token):
                return q
        return None


#: 进程级单例：与 matcher / 按钮回调共享同一答题态（不同 token 隔离，qq 隔离）。
STORE = QuizStore()


# ---------------------------------------------------------------------------
# per-task 按钮指令上下文（把本回合生成的 quiz 按钮 data 传给发送层）
# ---------------------------------------------------------------------------
# 为什么用 ContextVar 而不是全局 dict：NoneBot 每事件独立 task，ContextVar 天然
# 并发隔离，A 用户的竞猜按钮绝不会串到 B 用户的回复上。命令层注册答题后写入
# 「doupo:quiz:<token>」，发送层读取并挂到这条回复的 keyboard_cmd。
_quiz_button: ContextVar[str] = ContextVar("doupo_quiz_button", default="")


def arm_button(token: str) -> object:
    """把本回合的 quiz 按钮指令写入当前 task 上下文，返回 token 供 detach。"""
    return _quiz_button.set(str(token))


def disarm_button(token) -> None:
    """恢复上下文（handler finally 里调用，避免跨事件残留）。"""
    try:
        _quiz_button.reset(token)
    except (ValueError, TypeError):
        pass


def take_button() -> str:
    """取本回合 quiz 按钮口令；无则空串（不消费，由 handler 负责 detach）。"""
    return _quiz_button.get() or ""
