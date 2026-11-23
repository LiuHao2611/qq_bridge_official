# -*- coding: utf-8 -*-
"""斗破卡片「双主题」偏好存储（个人口令 > 全局默认 > 游戏本体风）。

背景（2026-11-23 用户「两个主题可以自由切换」）
------------------------------------------------
昨天 12 张斗破卡统一换成游戏本体 UI 深色风（commit eb33cce），用户又看上
11-22 被否的浅色现代风（commit 0312077），要求两套主题自由切换。绘图内核
``pic/templates/doupo_ui.py`` 已把两套接成 ``G`` / ``G_LIGHT``，本模块只负责
「某个 QQ 这次该用哪套」的**偏好解析与落盘**。

为什么不塞 players.data / 不新开环境变量
------------------------------------------
* 卡片主题是 UI 偏好，不是玩家玩法数据 → 不进 SQLite（红线：玩法数据才进库），
  用独立轻量 JSON，不污染 players.data blob。
* 路径只从 config.DATA_PATH 推导（和 tower/weekly session 同一条铁律）：
  容器 DOUPO_DATA_PATH=/app/data/doupo/doupo.json → /app/data/doupo/doupo_theme.json；
  单测改了 DATA_PATH 就自动跟着改，绝不会读/写生产文件。

优先级：``users[qq]``（口令即时切）> ``default``（全局配置）> ``game``（安全默认）。
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
from pathlib import Path

from . import config as cfg

#: 对外主题名（与 pic/templates/doupo_ui.py 的 STYLE_GAME / STYLE_LIGHT 对齐）
GAME = "game"
LIGHT = "light"
STYLES = (GAME, LIGHT)

#: 中文别名 → 规范名（口令用）
ALIASES = {
    "game": GAME, "游戏": GAME, "游戏风": GAME, "游戏本体": GAME,
    "深色": GAME, "暗色": GAME, "原版": GAME,
    "light": LIGHT, "浅色": LIGHT, "浅色风": LIGHT, "现代": LIGHT,
    "现代风": LIGHT, "亮色": LIGHT, "白": LIGHT,
}

_LABEL = {
    GAME: "游戏本体风（深蓝紫 + 玫红回纹 + 青金牌匾）",
    LIGHT: "浅色现代风（冷白 + 靛紫 + 薄荷绿）",
}


def label(style: str) -> str:
    return _LABEL.get(style, style)


def normalize(value: str | None) -> str | None:
    """口令参数 → 规范主题名；无法识别返回 None。"""
    if not value:
        return None
    return ALIASES.get(str(value).strip().lower())


def path() -> Path:
    """主题偏好文件路径：只由 DATA_PATH 推导（单源，容器/单测同进退）。"""
    return Path(cfg.DATA_PATH).parent / "doupo_theme.json"


_LOCK = threading.RLock()
_CACHE: dict | None = None
_CACHE_MTIME: float = -1.0


def _read() -> dict:
    global _CACHE, _CACHE_MTIME
    p = path()
    try:
        st = p.stat()
    except OSError:
        return {"default": GAME, "users": {}}
    with _LOCK:
        if _CACHE is not None and st.st_mtime_ns == _CACHE_MTIME:
            return _CACHE
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        if not isinstance(data, dict):
            data = {}
        default = normalize(data.get("default"))
        users_raw = data.get("users")
        users = users_raw if isinstance(users_raw, dict) else {}
        clean_users = {}
        for qq, style in users.items():
            s = normalize(style)
            if s:
                clean_users[str(qq)] = s
        _CACHE = {"default": default or GAME, "users": clean_users}
        _CACHE_MTIME = st.st_mtime_ns
        return _CACHE


def _write(data: dict) -> None:
    global _CACHE, _CACHE_MTIME
    p = path()
    p.parent.mkdir(parents=True, exist_ok=True)
    # 原子写：临时文件 + os.replace，避免渲染进程与口令进程并发时读到半截 JSON。
    fd, tmp = tempfile.mkstemp(prefix=".doupo_theme-", suffix=".json", dir=str(p.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2, sort_keys=True)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, p)
        _CACHE = data
        try:
            _CACHE_MTIME = p.stat().st_mtime_ns
        except OSError:
            _CACHE_MTIME = -1.0
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def resolve(qq: str | None) -> str:
    """个人偏好 > 全局默认 > game。qq 为空（菜单/排行/战报等公共卡）走全局默认。"""
    data = _read()
    if qq:
        s = data["users"].get(str(qq))
        if s in STYLES:
            return s
    d = data.get("default")
    return d if d in STYLES else GAME


def get_user(qq: str) -> str:
    return _read()["users"].get(str(qq)) or ""


def set_user(qq: str, style: str) -> str:
    s = normalize(style)
    if s is None:
        raise ValueError("未知主题：%s（可用：游戏 / 浅色）" % style)
    with _LOCK:
        data = dict(_read())
        users = dict(data["users"])
        if s == resolve_default():
            users.pop(str(qq), None)   # 与全局一致 → 不存冗余个人项，跟随全局
        else:
            users[str(qq)] = s
        data["users"] = users
        _write(data)
    return s


def clear_user(qq: str) -> str:
    """清除个人偏好，回到全局默认。"""
    with _LOCK:
        data = dict(_read())
        users = dict(data["users"])
        users.pop(str(qq), None)
        data["users"] = users
        _write(data)
    return resolve(qq)


def resolve_default() -> str:
    d = _read().get("default")
    return d if d in STYLES else GAME


def set_default(style: str) -> str:
    """设置全局默认（运维/管理员口令）。已有个人偏好不受影响。"""
    s = normalize(style)
    if s is None:
        raise ValueError("未知主题：%s（可用：游戏 / 浅色）" % style)
    with _LOCK:
        data = dict(_read())
        data["default"] = s
        _write(data)
    return s


def status(qq: str | None = None) -> dict:
    """供口令回执展示：当前全局默认、个人偏好、该 QQ 实际生效主题。"""
    data = _read()
    out = {
        "default": resolve_default(),
        "user": data["users"].get(str(qq)) if qq else "",
        "effective": resolve(qq),
    }
    return out
