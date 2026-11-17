# -*- coding: utf-8 -*-
"""修仙文字游戏 · 数据存储层（SQLite）。

## 为什么从 JSON 换成 SQLite（2026-11-17，用户「玩家数据肯定是要存到库里」）

旧实现：「一个 xiuxian.json 装全服 + 每次 set_world/put_sect/put_player 整份重写」。
问题与斗破同源：写放大、`asyncio.Lock` 只在进程内有效（第二个写入者静默覆盖）、
落盘无 fsync（宿主断电可能截断，只能靠 .corrupt- 备份人工恢复）。

换库后：一人/一宗门/一群一行，写谁只写谁；WAL + 事务，崩溃自动回滚；跨进程文件锁。

## 设计要点（与 doupo/store.py 同款，改之前先看完）

* `self._data` 仍是**内存权威态**：`get_player()` / `get_sect()` / `get_world()`
  返回的都是**活字典**，调用方就地改完再 put/set —— 这是全项目既有写法
  （AST 全量核过：没有任何"改了不 put"的路径），不能改成返副本。
* 落盘两条路：put_* / set_world / enable_group 走**单行快路径**；
  `save()` 走**兜底 diff**（内存 vs 上次落盘快照），只写变化的行并删掉多余行。
* **不做自动回退**：SQLite 出错就抛 :class:`StoreError`，绝不偷偷退回 JSON。
* 旧 `xiuxian.json` 只当**一次性导入源**：库为空且文件存在时导一次，
  原文件原样不动（回滚备份），导入记录进 `meta` 表防重复导入。
"""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import time
from contextlib import contextmanager
from typing import Any, Dict, List, Optional

from . import config as cfg

#: 库结构版本（PRAGMA user_version）。改表结构时 +1 并在 `_init_db` 里补迁移。
DB_VER = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS players (
    qq         TEXT PRIMARY KEY,
    group_id   TEXT    NOT NULL DEFAULT '',
    realm      TEXT    NOT NULL DEFAULT '',
    exp        INTEGER NOT NULL DEFAULT 0,
    ling_shi   INTEGER NOT NULL DEFAULT 0,
    updated_at REAL    NOT NULL DEFAULT 0,
    data       TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_xx_players_group ON players(group_id);
CREATE TABLE IF NOT EXISTS sects (
    id         TEXT PRIMARY KEY,
    updated_at REAL NOT NULL DEFAULT 0,
    data       TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS enabled (
    group_id TEXT PRIMARY KEY,
    added_at REAL NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS world (
    group_id   TEXT PRIMARY KEY,
    updated_at REAL NOT NULL DEFAULT 0,
    data       TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS meta (
    k TEXT PRIMARY KEY,
    v TEXT NOT NULL
);
"""


def _log(level: str, msg: str) -> None:
    """写日志：线上走 nonebot.log，裸跑单测退回 stdlib（不硬依赖 nonebot）。"""
    try:
        from nonebot.log import logger as _lg
    except Exception:  # noqa: BLE001
        import logging

        _lg = logging.getLogger("xiuxian.store")
    getattr(_lg, level)(msg)


def _log_error(msg: str) -> None:
    """写 ERROR（保留旧名，调用点不用改）。"""
    _log("error", msg)


class StoreError(RuntimeError):
    """存档库读写失败。**必须**向上抛：不许静默当成功（会谎报"已保存"）。"""


def _dumps(v: Dict[str, Any]) -> str:
    """压缩序列化（稳定输出：同一份内容永远同一串，用于 diff 判等）。"""
    return json.dumps(v, ensure_ascii=False, separators=(",", ":"))


class Store:
    def __init__(self, path: str | None = None, db_path: str | None = None):
        #: 旧 JSON 存档路径 —— 现在只用作「一次性导入源」，另行保留不动
        self.path = str(path or cfg.DATA_PATH)
        #: SQLite 库路径。显式 path 时按同目录同名推导，保证单测天然隔离
        self.db_path = str(db_path or (cfg.DB_PATH if path is None
                                       else os.path.splitext(self.path)[0] + ".sqlite"))
        self._lock = asyncio.Lock()
        self._data: Dict[str, Any] = {
            "players": {},      # qq(str) -> player dict
            "sects": {},        # sect_id(str) -> sect dict
            "enabled": [],      # 已启用游戏的群 group_id(str) 列表
            "world": {},        # group_id(str) -> {boss:{...}, secret:{...}}
        }
        #: 与磁盘一致的快照（键 -> 序列化串），save() 用它算 diff
        self._snap: Dict[str, Dict[str, str]] = {
            "players": {}, "sects": {}, "enabled": {}, "world": {}}
        self._loaded = False
        self._db_ready = False

    # ---- 底层连接 ----
    def _conn(self) -> sqlite3.Connection:
        d = os.path.dirname(self.db_path)
        if d:
            os.makedirs(d, exist_ok=True)
        con = sqlite3.connect(self.db_path, timeout=10.0)
        con.execute("PRAGMA busy_timeout=5000")   # 跨进程等锁，别立刻 SQLITE_BUSY
        con.execute("PRAGMA journal_mode=WAL")    # 读写并发 + 崩溃自动回滚
        con.execute("PRAGMA synchronous=FULL")    # 断电也不丢已提交事务
        return con

    def _ensure_ready(self) -> None:
        """确保表已建好（第一次 DB 访问时自动执行，不能只依赖 load()）。"""
        if not self._db_ready:
            self._init_db()
            self._db_ready = True

    @contextmanager
    def _tx(self):
        """一次事务：成功 commit、异常 rollback，**并且一定 close**（防 WinError 32）。"""
        self._ensure_ready()
        con = self._conn()
        try:
            with con:
                yield con
        finally:
            con.close()

    def _init_db(self) -> None:
        # 注意：这里不能用 _tx()（会递归回 _ensure_ready），直接开连接；
        # 连开连接也要包在 try 里 —— makedirs 失败也要变成 StoreError。
        con = None
        try:
            con = self._conn()
            with con:
                con.executescript(_SCHEMA)
                ver = con.execute("PRAGMA user_version").fetchone()[0]
                if ver < DB_VER:
                    con.execute("PRAGMA user_version=%d" % DB_VER)
        except Exception as e:  # noqa: BLE001
            raise StoreError("修仙存档库初始化失败（%s）：%s"
                             % (type(e).__name__, str(e)[:200])) from e
        finally:
            if con is not None:
                con.close()

    # ---- 生命周期 ----
    async def load(self) -> None:
        async with self._lock:
            if self._loaded:
                return
            self._ensure_ready()
            self._read_all()
            if not self._data["players"] and not self._data["sects"]:
                self._import_legacy_json()
            self._loaded = True

    def _read_all(self) -> None:
        try:
            with self._tx() as con:
                for row in con.execute("SELECT qq, data FROM players"):
                    v = json.loads(row[1])
                    if isinstance(v, dict):
                        self._data["players"][str(row[0])] = v
                        self._snap["players"][str(row[0])] = row[1]
                for row in con.execute("SELECT id, data FROM sects"):
                    v = json.loads(row[1])
                    if isinstance(v, dict):
                        self._data["sects"][str(row[0])] = v
                        self._snap["sects"][str(row[0])] = row[1]
                for row in con.execute("SELECT group_id FROM enabled"):
                    self._data["enabled"].append(str(row[0]))
                    self._snap["enabled"][str(row[0])] = str(row[0])
                for row in con.execute("SELECT group_id, data FROM world"):
                    v = json.loads(row[1])
                    if isinstance(v, dict):
                        self._data["world"][str(row[0])] = v
                        self._snap["world"][str(row[0])] = row[1]
        except Exception as e:  # noqa: BLE001
            # 库读不出来 = 玩家资产看不见。绝不"空库启动"（那会被后续写盘覆盖）。
            raise StoreError("修仙存档库读取失败（%s）：%s"
                             % (type(e).__name__, str(e)[:200])) from e

    def _import_legacy_json(self) -> None:
        """首次启动：把旧 xiuxian.json 一次性导入。原文件原样保留（回滚备份）。"""
        if not os.path.exists(self.path):
            return
        try:
            with self._tx() as con:
                done = con.execute(
                    "SELECT v FROM meta WHERE k='imported_from'").fetchone()
            if done:
                return       # 导过了，绝不重复导（会把新进度覆盖成旧档）
        except Exception as e:  # noqa: BLE001
            raise StoreError("修仙存档库 meta 读取失败（%s）" % type(e).__name__) from e

        try:
            with open(self.path, "r", encoding="utf-8") as f:
                raw = json.load(f)
            players = dict(raw.get("players") or {})
            sects = dict(raw.get("sects") or {})
            enabled = list(raw.get("enabled") or [])
            world = dict(raw.get("world") or {})
        except Exception as e:  # noqa: BLE001
            # 坏档不导入，但也**不许**删除/覆盖原文件：备份成 .corrupt- 留证据。
            bak = "%s.corrupt-%s" % (self.path, time.strftime("%Y%m%d-%H%M%S"))
            try:
                os.replace(self.path, bak)
            except Exception as e2:  # noqa: BLE001
                _log_error("修仙旧存档损坏且无法备份 %s：%r / %r" % (self.path, e, e2))
                raise
            _log_error("修仙旧存档损坏，已备份为 %s（%r），本次以空库启动"
                       "——请用备份恢复，勿直接继续玩" % (bak, e))
            return

        self._data["players"] = {str(k): v for k, v in players.items() if isinstance(v, dict)}
        self._data["sects"] = {str(k): v for k, v in sects.items() if isinstance(v, dict)}
        self._data["enabled"] = [str(g) for g in enabled]
        self._data["world"] = {str(k): v for k, v in world.items() if isinstance(v, dict)}
        self._flush_changed()
        try:
            with self._tx() as con:
                con.execute("INSERT OR REPLACE INTO meta(k, v) VALUES('imported_from', ?)",
                            (os.path.abspath(self.path),))
                con.execute("INSERT OR REPLACE INTO meta(k, v) VALUES('imported_at', ?)",
                            (time.strftime("%Y-%m-%dT%H:%M:%S"),))
                con.execute("INSERT OR REPLACE INTO meta(k, v) VALUES('imported_players', ?)",
                            (str(len(self._data["players"])),))
        except Exception as e:  # noqa: BLE001
            raise StoreError("旧存档导入记录写入失败（%s）" % type(e).__name__) from e
        _log("info", "修仙旧存档已导入 SQLite：%d 名玩家 / %d 宗门 / %d 群（原文件保留：%s）"
             % (len(self._data["players"]), len(self._data["sects"]),
                len(self._data["enabled"]), self.path))

    # ---- 落盘 ----
    @staticmethod
    def _upsert_player(con, qq: str, p: dict, s: str, ts: float) -> None:
        con.execute(
            "INSERT INTO players(qq, group_id, realm, exp, ling_shi, updated_at, data)"
            " VALUES(?,?,?,?,?,?,?)"
            " ON CONFLICT(qq) DO UPDATE SET"
            "  group_id=excluded.group_id, realm=excluded.realm, exp=excluded.exp,"
            "  ling_shi=excluded.ling_shi, updated_at=excluded.updated_at,"
            "  data=excluded.data",
            (qq, str(p.get("group") or ""), str(p.get("realm") or ""),
             int(p.get("exp") or 0), int(p.get("ling_shi") or 0), ts, s))

    def _diff(self) -> tuple:
        """算出「内存 vs 快照」的差异（只算，不写）。"""
        out = {}
        for kind, key in (("players", "qq"), ("sects", "id"), ("world", "group_id")):
            mem, snap = self._data.get(kind) or {}, self._snap[kind]
            changed = [(str(k), _dumps(v), v) for k, v in mem.items()
                       if snap.get(str(k)) != _dumps(v)]
            gone = [k for k in snap if k not in {str(x) for x in mem}]
            out[kind] = (changed, gone)
        mem_g, snap_g = self._data.get("enabled") or [], self._snap["enabled"]
        out["enabled"] = ([str(g) for g in mem_g if str(g) not in snap_g],
                          [g for g in snap_g if g not in {str(x) for x in mem_g}])
        return out

    def _flush_changed(self) -> bool:
        """把内存里与快照不同的行写回库；返回是否真的写了东西。"""
        d = self._diff()
        if not any(d[k][0] or d[k][1] for k in d):
            return False
        now = time.time()
        try:
            with self._tx() as con:
                for qq, s, p in d["players"][0]:
                    self._upsert_player(con, qq, p, s, now)
                for sid, s, sec in d["sects"][0]:
                    con.execute(
                        "INSERT INTO sects(id, updated_at, data) VALUES(?,?,?)"
                        " ON CONFLICT(id) DO UPDATE SET"
                        "  updated_at=excluded.updated_at, data=excluded.data",
                        (sid, now, s))
                for g, s, w in d["world"][0]:
                    con.execute(
                        "INSERT INTO world(group_id, updated_at, data) VALUES(?,?,?)"
                        " ON CONFLICT(group_id) DO UPDATE SET"
                        "  updated_at=excluded.updated_at, data=excluded.data",
                        (g, now, s))
                if d["enabled"][0]:
                    con.executemany("INSERT OR IGNORE INTO enabled(group_id, added_at)"
                                    " VALUES(?,?)", [(g, now) for g in d["enabled"][0]])
                for kind, table, col in (("players", "players", "qq"),
                                         ("sects", "sects", "id"),
                                         ("world", "world", "group_id"),
                                         ("enabled", "enabled", "group_id")):
                    if d[kind][1]:
                        con.executemany("DELETE FROM %s WHERE %s=?" % (table, col),
                                        [(k,) for k in d[kind][1]])
        except Exception as e:  # noqa: BLE001
            # 快照不更新 → 下次 save 会重试，不会"以为存好了"
            raise StoreError("修仙存档写入失败（%s）：%s"
                             % (type(e).__name__, str(e)[:200])) from e

        for kind in ("players", "sects", "world"):
            for k, s, _v in d[kind][0]:
                self._snap[kind][k] = s
            for k in d[kind][1]:
                self._snap[kind].pop(k, None)
        for g in d["enabled"][0]:
            self._snap["enabled"][g] = g
        for g in d["enabled"][1]:
            self._snap["enabled"].pop(g, None)
        return True

    async def save(self) -> None:
        async with self._lock:
            self._flush_changed()

    # ---- 玩家 ----
    async def get_player(self, qq: str) -> Optional[dict]:
        async with self._lock:
            # 统一按字符串取：库里主键是 TEXT，调用方偶尔传 int（事件对象）
            return self._data["players"].get(str(qq))

    async def put_player(self, p: dict) -> None:
        async with self._lock:
            qq = str(p["qq"])
            self._data["players"][qq] = p
            s = _dumps(p)
            if self._snap["players"].get(qq) == s:
                return          # 内容没变：一个字节都不写
            try:
                with self._tx() as con:
                    self._upsert_player(con, qq, p, s, time.time())
            except Exception as e:  # noqa: BLE001
                raise StoreError("修仙存档写入失败（%s）：%s"
                                 % (type(e).__name__, str(e)[:200])) from e
            self._snap["players"][qq] = s

    async def all_players(self, group: Optional[str] = None) -> List[dict]:
        async with self._lock:
            ps = list(self._data["players"].values())
        if group is not None:
            ps = [p for p in ps if p.get("group") == group]
        return ps

    # ---- 宗门 ----
    async def get_sect(self, sid: str) -> Optional[dict]:
        async with self._lock:
            return self._data["sects"].get(str(sid))

    async def put_sect(self, s: dict) -> None:
        async with self._lock:
            sid = str(s["id"])
            self._data["sects"][sid] = s
            raw = _dumps(s)
            if self._snap["sects"].get(sid) == raw:
                return
            try:
                with self._tx() as con:
                    con.execute(
                        "INSERT INTO sects(id, updated_at, data) VALUES(?,?,?)"
                        " ON CONFLICT(id) DO UPDATE SET"
                        "  updated_at=excluded.updated_at, data=excluded.data",
                        (sid, time.time(), raw))
            except Exception as e:  # noqa: BLE001
                raise StoreError("宗门存档写入失败（%s）：%s"
                                 % (type(e).__name__, str(e)[:200])) from e
            self._snap["sects"][sid] = raw

    async def all_sects(self) -> List[dict]:
        async with self._lock:
            return list(self._data["sects"].values())

    # ---- 群启用状态（后台随机事件需要知道往哪发）----
    async def enable_group(self, group: str) -> None:
        async with self._lock:
            group = str(group)
            if group in self._data["enabled"]:
                return
            self._data["enabled"].append(group)
            try:
                with self._tx() as con:
                    con.execute("INSERT OR IGNORE INTO enabled(group_id, added_at)"
                                " VALUES(?,?)", (str(group), time.time()))
            except Exception as e:  # noqa: BLE001
                raise StoreError("群启用状态写入失败（%s）" % type(e).__name__) from e
            self._snap["enabled"][str(group)] = str(group)

    async def enabled_groups(self) -> List[str]:
        async with self._lock:
            return list(self._data["enabled"])

    # ---- 世界事件状态（世界Boss / 秘境）----
    async def get_world(self, group: str) -> dict:
        async with self._lock:
            return self._data["world"].setdefault(str(group), {})

    async def set_world(self, group: str, world: dict) -> None:
        async with self._lock:
            self._data["world"][str(group)] = world
            raw = _dumps(world)
            if self._snap["world"].get(str(group)) == raw:
                return
            try:
                with self._tx() as con:
                    con.execute(
                        "INSERT INTO world(group_id, updated_at, data) VALUES(?,?,?)"
                        " ON CONFLICT(group_id) DO UPDATE SET"
                        "  updated_at=excluded.updated_at, data=excluded.data",
                        (str(group), time.time(), raw))
            except Exception as e:  # noqa: BLE001
                raise StoreError("世界事件状态写入失败（%s）"
                                 % type(e).__name__) from e
            self._snap["world"][str(group)] = raw

    async def touch(self, group: str) -> None:
        """确保 world 键存在（后台循环用）。"""
        async with self._lock:
            self._data["world"].setdefault(str(group), {})


def now() -> float:
    return time.time()
