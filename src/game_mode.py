# -*- coding: utf-8 -*-
"""跨游戏「当前模式」共享存储 + 口令探测。

斗破 / 修仙 现在都用纯中文口令，靠「按用户记录的当前游戏」来分流：
  * 谁发了 修仙菜单 → 他后续口令走修仙；发了 斗破菜单 → 走斗破。
  * 只有菜单（及 入道 / 我要修仙 这类入口）会切换模式；
    其它口令按模式命中对应游戏，打错游戏或没选模式给友好提示。

落盘：data/game_mode.json（相对 cwd；容器内 cwd=/app 即挂载卷 ./data）。
环境红线：绝不写 C 盘用户目录，路径默认落在仓库/容器数据目录。
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import time
from typing import Dict, List, Optional

#: 仓库根（src/ 的上一级）。容器内即 /app，所以下面拼出的 data/ 就是挂载卷。
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _log_error(msg: str) -> None:
    """写 ERROR：线上走 nonebot.log，裸跑单测退回 stdlib。"""
    try:
        from nonebot.log import logger as _lg
    except Exception:  # noqa: BLE001
        import logging

        _lg = logging.getLogger("game_mode")
    _lg.error(msg)


#: 2026-11-17：原来默认是相对路径 "data/game_mode.json"（依赖进程 CWD）。
#: CWD 一变就写到别处、且**静默**；改为基于仓库根的绝对路径，
#: 容器内固定 /app/data/game_mode.json（bind-mount 卷），单测用 GAME_MODE_PATH 覆盖。
DATA_PATH = os.environ.get("GAME_MODE_PATH", os.path.join(_ROOT, "data", "game_mode.json"))

GAME_LABELS = {"xiuxian": "修仙", "doupo": "斗破"}

_lock = asyncio.Lock()
_modes: Dict[str, str] = {}
_loaded = False
_keywords: Dict[str, List[str]] = {}


async def _ensure_loaded() -> None:
    global _modes, _loaded
    if _loaded:
        return
    async with _lock:
        if _loaded:
            return
        try:
            with open(DATA_PATH, "r", encoding="utf-8") as f:
                _modes = json.load(f)
        except FileNotFoundError:
            _modes = {}
        except Exception as e:  # noqa: BLE001
            # 坏档不许被空库覆盖（用户最高声明：数据不许丢）：先备份再继续。
            bak = DATA_PATH + ".corrupt-" + time.strftime("%Y%m%d-%H%M%S")
            try:
                os.replace(DATA_PATH, bak)
            except Exception as e2:  # noqa: BLE001
                _log_error("游戏模式文件损坏且备份失败 %s：%r / %r" % (DATA_PATH, e, e2))
                raise
            _log_error("游戏模式文件损坏，已备份为 %s（%r）" % (bak, e))
            _modes = {}
        _loaded = True


async def get_mode(uid: str) -> Optional[str]:
    """返回用户当前游戏模式（'xiuxian' / 'doupo' / None）。"""
    await _ensure_loaded()
    return _modes.get(str(uid))


async def set_mode(uid: str, game: str) -> None:
    """设定用户当前游戏模式。"""
    await _ensure_loaded()
    async with _lock:
        _modes[str(uid)] = game
        _save()


def _save() -> None:
    d = os.path.dirname(DATA_PATH)
    if d:
        os.makedirs(d, exist_ok=True)
    tmp = DATA_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(_modes, f, ensure_ascii=False, indent=1)
    os.replace(tmp, DATA_PATH)


def register_keywords(game: str, kws: List[str]) -> None:
    """各游戏插件在 import 时登记自己的口令关键字，供兜底路由探测用。"""
    _keywords[game] = kws


def all_keywords() -> List[str]:
    out: List[str] = []
    for kws in _keywords.values():
        out.extend(kws)
    # 长关键字优先，避免短前缀（如「送」）吞掉更长口令
    return sorted(set(out), key=len, reverse=True)


def looks_like_command(text: str) -> bool:
    """消息去掉前导 CQ 码 / 空白 / # 后，是否以某个游戏口令开头。

    用于兜底路由判断「这条消息像不像在打游戏口令」，从而只在像口令时才给提示，
    不误伤闲聊。
    """
    t = re.sub(r"^(?:\[CQ:[^\]]*\]|\s)*#?\s*", "", text or "")
    return any(t.startswith(kw) for kw in all_keywords())
