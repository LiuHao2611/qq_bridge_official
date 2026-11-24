# -*- coding: utf-8 -*-
"""模式路由兜底：用户没选模式、或打了另一游戏的口令时，给友好提示。

只有当「没有游戏 matcher 抢到这条消息」时才会落到这里——游戏 matcher 命中后会
finish + block，本兜底就不会再跑。故本兜底只响应"看起来像游戏口令但没人接"的情况，
绝不误伤闲聊。

优先级 6：低于游戏 matcher 的 5（保证游戏命中后本兜底被 block），高于主聊天处理器 10。
"""
from __future__ import annotations

from nonebot import on_regex

from src.game_compat import Event, view, finish_text
from src.game_mode import get_mode, looks_like_command, GAME_LABELS

PRI = 6


def _is_command(event: Event) -> bool:
    # 🚨 注解必须是兼容基类 ``Event``，不能是 OneBot 的 ``MessageEvent``。
    # 2026-11-24 事故：官方 QQ 通道的事件类（GroupMessageCreateEvent）注入
    # ``MessageEvent`` 会 TypeMisMatch → Rule 静默 False → 本兜底**永不触发**，
    # 于是「模式不对时打游戏口令」变成机器人一声不响（用户原话：报错了）。
    # 与 doupo._mode_is 里踩过的同一个坑，别再改回具体适配器类型。
    try:
        return looks_like_command(view(event).get_plaintext())
    except Exception:  # noqa: BLE001 — 探测失败一律当闲聊，绝不炸消息处理
        return False


router = on_regex(r".+", priority=PRI, block=True, rule=_is_command)


@router.handle()
async def _(event: Event):
    ev = view(event)
    m = await get_mode(str(ev.user_id))
    if m is None:
        await finish_text(router, ev, "请先发送「修仙菜单」或「斗破菜单」选择你想玩的游戏～")
        return
    label = GAME_LABELS.get(m, m)
    menu = "修仙菜单" if m == "xiuxian" else "斗破菜单"
    await finish_text(
        router, ev,
        f"当前为{label}模式，未识别该口令。发送「{menu}」查看可用口令～",
    )
