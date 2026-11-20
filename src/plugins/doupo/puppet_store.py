# -*- coding: utf-8 -*-
"""斗破苍穹 · 傀儡系统数据存储（独立 SQLite，真实字段列）。

## 🚨 最高声明（2026-11-18 用户原话）

> 「所有玩家数据必须保存到数据库，禁止存 JSON，这是最高声明」

因此本模块**绝不**把傀儡数据塞进 ``players.data`` 那列 JSON blob，也不新建任何
``*_json`` / ``data TEXT`` 列。每一样都是**真实字段列**，一行一具傀儡：

* ``puppet_eligibility`` —— 资格（传承 / 开通标记），一列一字段；
* ``puppets``            —— 傀儡本体，三主材/金属/皮肤/淬炼/认主全是独立列；
* ``puppet_materials``   —— 傀儡专用材料，一行一名一数量（不进 ``bag`` 的 JSON）；
* ``meta``               —— 结构版本。

## 路径铁律（.workbuddy/memory/MEMORY.md）

* 库路径**只由 ``cfg.DB_PATH`` 推导**（``DATA_PATH`` → 同目录 .sqlite），
  **不新开环境变量**——曾有人加 ``DOUPO_DB_PATH`` 导致测试写进生产库。
* 容器里落在 ``/app/data/doupo/puppet_data.sqlite``（挂载卷），绝不盘符路径。
* 与 ``store.Store`` 同构：WAL + 事务 + ``busy_timeout``；写失败抛
  :class:`PuppetStoreError`，绝不静默当成功。

## 并发

与 ``store.STORE`` 一样用 ``asyncio.Lock`` 串本进程写；跨进程靠 SQLite 文件锁。
``PUPPET_STORE`` 是进程级单例，由 ``doupo/__init__.py`` 持有。
"""

from __future__ import annotations

import asyncio
import os
import sqlite3
import time
from contextlib import contextmanager
from typing import Any, Dict, List, Optional

from . import config as cfg

DB_VER = 2

#: 裸 ALTER 迁移表：``CREATE TABLE IF NOT EXISTS`` 只对**新库**生效，老库不会因为
#: schema 字符串改了而多出一列 —— 玩家老存档会直接 ``no such column: soul_fed``。
#: 只允许 ADD COLUMN 这种幂等操作（SQLite 不支持带默认值的复杂改列）。
#: (表, 列, 列定义)
_MIGRATIONS = (
    # P3 养魂（第757章 N08）：老库补列，默认 0 ⇒ 数值与改造前完全一致。
    ("puppets", "soul_fed", "INTEGER NOT NULL DEFAULT 0"),
)

# 独立库文件：与 doupo.sqlite 同目录，文件名固定。
PUPPET_DB_PATH = str(
    __import__("pathlib").Path(cfg.DB_PATH).with_name("puppet_data.sqlite")
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS puppet_eligibility (
    qq        TEXT PRIMARY KEY,
    lore      INTEGER NOT NULL DEFAULT 0,   -- 是否已习得竹简传承
    ready     INTEGER NOT NULL DEFAULT 0,   -- 五道资格达标
    active_id INTEGER NOT NULL DEFAULT 0,   -- 当前出战傀儡 id
    updated_at REAL NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS puppets (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    qq            TEXT    NOT NULL,
    name          TEXT    NOT NULL,
    tier          INTEGER NOT NULL DEFAULT 0,   -- 0=人级铜 1=地级银 2=天级金
    tier_name     TEXT    NOT NULL DEFAULT '人级傀儡',
    skin          TEXT    NOT NULL DEFAULT '铜',
    body_name     TEXT    NOT NULL DEFAULT '',
    body_realm    TEXT    NOT NULL DEFAULT '',
    body_star     INTEGER NOT NULL DEFAULT 0,
    body_fresh    INTEGER NOT NULL DEFAULT 1,
    body_fresh_until REAL NOT NULL DEFAULT 0,
    core_tier     INTEGER NOT NULL DEFAULT 0,
    core_attr     TEXT    NOT NULL DEFAULT '',
    soul_source   TEXT    NOT NULL DEFAULT '',
    soul_strength INTEGER NOT NULL DEFAULT 0,
    soul_fed      INTEGER NOT NULL DEFAULT 0,   -- P3 养魂累计（0..200，与基准值分开记）
    mind_stripped INTEGER NOT NULL DEFAULT 0,
    metal_name    TEXT    NOT NULL DEFAULT '',
    metal_grade   INTEGER NOT NULL DEFAULT 0,
    fire_tier     INTEGER NOT NULL DEFAULT 0,  -- 炼制之火强度（定档变量）
    fire_attr     TEXT    NOT NULL DEFAULT '',
    purity        INTEGER NOT NULL DEFAULT 0,  -- 雷淬进度 0..100
    bonded        INTEGER NOT NULL DEFAULT 0,
    awakened      INTEGER NOT NULL DEFAULT 0,
    origin        TEXT    NOT NULL DEFAULT 'self',
    damaged       INTEGER NOT NULL DEFAULT 0,
    born          REAL    NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_puppets_qq ON puppets(qq);
CREATE TABLE IF NOT EXISTS puppet_materials (
    qq    TEXT  NOT NULL,
    name  TEXT  NOT NULL,
    qty   INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (qq, name)
);
CREATE TABLE IF NOT EXISTS puppet_ruin_daily (
    qq         TEXT NOT NULL,
    day        TEXT NOT NULL,          -- YYYY-MM-DD，本地日期
    stage      INTEGER NOT NULL DEFAULT 0,  -- 今日已通关到第几段 0..3
    attempts   INTEGER NOT NULL DEFAULT 0,
    updated_at REAL NOT NULL DEFAULT 0,
    PRIMARY KEY (qq, day)
);
CREATE TABLE IF NOT EXISTS meta (
    k TEXT PRIMARY KEY,
    v TEXT NOT NULL
);
"""


class PuppetStoreError(RuntimeError):
    """傀儡库读写失败，必须向上抛，不许静默当成功。"""


def _now() -> float:
    return time.time()


class PuppetStore:
    def __init__(self, db_path: str | None = None):
        self.db_path = str(db_path or PUPPET_DB_PATH)
        self._lock = asyncio.Lock()
        self._ready = False

    # ---- 底层 ----
    def _conn(self) -> sqlite3.Connection:
        d = os.path.dirname(self.db_path)
        if d:
            os.makedirs(d, exist_ok=True)
        con = sqlite3.connect(self.db_path, timeout=10.0)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA busy_timeout=5000")
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA synchronous=FULL")
        return con

    def _ensure_ready(self) -> None:
        if self._ready:
            return
        con = None
        try:
            con = self._conn()
            with con:
                con.executescript(_SCHEMA)
                for _table, _col, _ddl in _MIGRATIONS:
                    _have = {r["name"] for r in
                             con.execute("PRAGMA table_info(%s)" % _table).fetchall()}
                    if _col not in _have:
                        con.execute("ALTER TABLE %s ADD COLUMN %s %s"
                                    % (_table, _col, _ddl))
                ver = con.execute("PRAGMA user_version").fetchone()[0]
                if ver < DB_VER:
                    con.execute("PRAGMA user_version=%d" % DB_VER)
        except Exception as e:  # noqa: BLE001
            raise PuppetStoreError("傀儡库初始化失败（%s）：%s"
                                   % (type(e).__name__, str(e)[:200])) from e
        finally:
            if con is not None:
                con.close()
        self._ready = True

    @contextmanager
    def _tx(self):
        self._ensure_ready()
        con = self._conn()
        try:
            with con:
                yield con
        finally:
            con.close()

    # ---- 资格 ----
    async def eligibility(self, qq: str) -> dict:
        qq = str(qq)
        with self._tx() as con:
            row = con.execute(
                "SELECT lore, ready, active_id FROM puppet_eligibility WHERE qq=?",
                (qq,)).fetchone()
        if not row:
            return {"lore": False, "ready": False, "active_id": 0}
        return {"lore": bool(row["lore"]), "ready": bool(row["ready"]),
                "active_id": int(row["active_id"])}

    async def set_lore(self, qq: str, value: bool = True) -> None:
        await self._set_elig(qq, "lore", int(bool(value)))

    async def set_ready(self, qq: str, value: bool = True) -> None:
        await self._set_elig(qq, "ready", int(bool(value)))

    async def set_active(self, qq: str, puppet_id: int) -> None:
        await self._set_elig(qq, "active_id", int(puppet_id))

    async def _set_elig(self, qq: str, col: str, value: int) -> None:
        # col 只来自本模块内部常量调用，不做外部拼接
        if col not in ("lore", "ready", "active_id"):
            raise PuppetStoreError("非法资格列：" + col)
        qq = str(qq)
        async with self._lock:
            try:
                with self._tx() as con:
                    con.execute(
                        "INSERT INTO puppet_eligibility(qq, %s, updated_at) VALUES(?,?,?)"
                        " ON CONFLICT(qq) DO UPDATE SET %s=excluded.%s,"
                        " updated_at=excluded.updated_at" % (col, col, col),
                        (qq, value, _now()))
            except Exception as e:  # noqa: BLE001
                raise PuppetStoreError("傀儡资格写入失败（%s）：%s"
                                       % (type(e).__name__, str(e)[:200])) from e

    # ---- 傀儡本体 ----
    async def add_puppet(self, qq: str, data: Dict[str, Any]) -> int:
        """新增一具傀儡，返回自增 id。所有字段都是真实列，无 JSON。"""
        qq = str(qq)
        cols = (
            "qq", "name", "tier", "tier_name", "skin",
            "body_name", "body_realm", "body_star", "body_fresh", "body_fresh_until",
            "core_tier", "core_attr", "soul_source", "soul_strength", "soul_fed",
            "mind_stripped",
            "metal_name", "metal_grade", "fire_tier", "fire_attr",
            "purity", "bonded", "awakened", "origin", "damaged", "born",
        )
        vals = [
            qq, str(data.get("name") or "傀儡"),
            int(data.get("tier") or 0), str(data.get("tier_name") or "人级傀儡"),
            str(data.get("skin") or "铜"),
            str(data.get("body_name") or ""), str(data.get("body_realm") or ""),
            int(data.get("body_star") or 0), int(bool(data.get("body_fresh", True))),
            float(data.get("body_fresh_until") or 0),
            int(data.get("core_tier") or 0), str(data.get("core_attr") or ""),
            str(data.get("soul_source") or ""), int(data.get("soul_strength") or 0),
            int(data.get("soul_fed") or 0),
            int(bool(data.get("mind_stripped", False))),
            str(data.get("metal_name") or ""), int(data.get("metal_grade") or 0),
            int(data.get("fire_tier") or 0), str(data.get("fire_attr") or ""),
            int(data.get("purity") or 0), int(bool(data.get("bonded", False))),
            int(bool(data.get("awakened", False))), str(data.get("origin") or "self"),
            int(bool(data.get("damaged", False))), float(data.get("born") or _now()),
        ]
        ph = ",".join("?" for _ in cols)
        async with self._lock:
            try:
                with self._tx() as con:
                    cur = con.execute(
                        "INSERT INTO puppets(%s) VALUES(%s)" % (",".join(cols), ph), vals)
                    pid = int(cur.lastrowid)
                    # 第一具自动设为出战
                    row = con.execute(
                        "SELECT COUNT(*) AS n FROM puppets WHERE qq=?", (qq,)).fetchone()
                    if int(row["n"]) == 1:
                        con.execute(
                            "INSERT INTO puppet_eligibility(qq, active_id, updated_at)"
                            " VALUES(?,?,?) ON CONFLICT(qq) DO UPDATE SET"
                            " active_id=excluded.active_id, updated_at=excluded.updated_at",
                            (qq, pid, _now()))
            except Exception as e:  # noqa: BLE001
                raise PuppetStoreError("傀儡写入失败（%s）：%s"
                                       % (type(e).__name__, str(e)[:200])) from e
        return pid

    async def list_puppets(self, qq: str) -> List[dict]:
        qq = str(qq)
        with self._tx() as con:
            rows = con.execute(
                "SELECT * FROM puppets WHERE qq=? ORDER BY id", (qq,)).fetchall()
        return [self._row_to_dict(r) for r in rows]

    async def get_puppet(self, qq: str, pid: int) -> Optional[dict]:
        with self._tx() as con:
            row = con.execute(
                "SELECT * FROM puppets WHERE qq=? AND id=?", (str(qq), int(pid))).fetchone()
        return self._row_to_dict(row) if row else None

    async def active_puppet(self, qq: str) -> Optional[dict]:
        elig = await self.eligibility(qq)
        return await self.get_puppet(qq, elig["active_id"])

    async def update_puppet(self, qq: str, pid: int, **fields) -> None:
        """更新指定傀儡的真实列；字段白名单，防注入。"""
        allowed = {
            "name", "tier", "tier_name", "skin", "body_name", "body_realm", "body_star",
            "body_fresh", "body_fresh_until", "core_tier", "core_attr", "soul_source",
            "soul_strength", "soul_fed", "mind_stripped", "metal_name", "metal_grade",
            "fire_tier", "fire_attr", "purity", "bonded", "awakened", "origin", "damaged",
        }
        bad = set(fields) - allowed
        if bad:
            raise PuppetStoreError("非法傀儡字段：" + ",".join(sorted(bad)))
        if not fields:
            return
        sets = ",".join("%s=?" % k for k in fields)
        vals = []
        for k, v in fields.items():
            if k in ("body_fresh", "mind_stripped", "bonded", "awakened", "damaged"):
                vals.append(int(bool(v)))
            elif k == "soul_fed":
                vals.append(int(v))
            else:
                vals.append(v)
        vals += [str(qq), int(pid)]
        async with self._lock:
            try:
                with self._tx() as con:
                    con.execute(
                        "UPDATE puppets SET %s WHERE qq=? AND id=?" % sets, vals)
            except Exception as e:  # noqa: BLE001
                raise PuppetStoreError("傀儡更新失败（%s）：%s"
                                       % (type(e).__name__, str(e)[:200])) from e

    async def remove_puppet(self, qq: str, pid: int) -> None:
        async with self._lock:
            with self._tx() as con:
                con.execute("DELETE FROM puppets WHERE qq=? AND id=?", (str(qq), int(pid)))

    async def add_wild_puppet(self, data: Dict[str, Any]) -> int:
        """掉落一只无主残破傀儡（origin='wild'），供「收服」转化。"""
        d = dict(data)
        d.setdefault("origin", "wild")
        d.setdefault("bonded", False)
        d.setdefault("damaged", True)
        return await self.add_puppet("wild", d)

    async def wild_puppets(self) -> List[dict]:
        with self._tx() as con:
            rows = con.execute(
                "SELECT * FROM puppets WHERE qq='wild' AND origin='wild' ORDER BY id"
            ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    async def claim_puppet(self, qq: str, pid: int) -> bool:
        """收服无主傀儡：改主 + 认主 + 清受损。失败返回 False（不静默写坏）。"""
        qq = str(qq)
        async with self._lock:
            with self._tx() as con:
                row = con.execute(
                    "SELECT origin, bonded FROM puppets WHERE id=?", (int(pid),)).fetchone()
                if not row or row["origin"] != "wild" or row["bonded"]:
                    return False
                con.execute(
                    "UPDATE puppets SET qq=?, origin='other', bonded=1, damaged=0 WHERE id=?",
                    (qq, int(pid)))
                n = con.execute(
                    "SELECT COUNT(*) AS n FROM puppets WHERE qq=?", (qq,)).fetchone()
                if int(n["n"]) == 1:
                    con.execute(
                        "INSERT INTO puppet_eligibility(qq, active_id, updated_at)"
                        " VALUES(?,?,?) ON CONFLICT(qq) DO UPDATE SET"
                        " active_id=CASE WHEN active_id=0 THEN excluded.active_id"
                        " ELSE active_id END, updated_at=excluded.updated_at",
                        (qq, int(pid), _now()))
        return True

    @staticmethod
    def _row_to_dict(r: sqlite3.Row) -> dict:
        d = dict(r)
        for k in ("body_fresh", "mind_stripped", "bonded", "awakened", "damaged"):
            d[k] = bool(d.get(k))
        d["id"] = int(d.get("id") or 0)
        return d

    # ---- 傀儡专用材料（真实列，不进 bag JSON）----
    async def add_material(self, qq: str, name: str, n: int = 1) -> None:
        if n <= 0:
            return
        async with self._lock:
            try:
                with self._tx() as con:
                    con.execute(
                        "INSERT INTO puppet_materials(qq, name, qty) VALUES(?,?,?)"
                        " ON CONFLICT(qq,name) DO UPDATE SET qty=qty+excluded.qty",
                        (str(qq), str(name), int(n)))
            except Exception as e:  # noqa: BLE001
                raise PuppetStoreError("傀儡材料写入失败（%s）：%s"
                                       % (type(e).__name__, str(e)[:200])) from e

    async def material_qty(self, qq: str, name: str) -> int:
        with self._tx() as con:
            row = con.execute(
                "SELECT qty FROM puppet_materials WHERE qq=? AND name=?",
                (str(qq), str(name))).fetchone()
        return int(row["qty"]) if row else 0

    async def transfer_material(self, src_qq: str, dst_qq: str, name: str,
                                n: int = 1) -> bool:
        """把一种傀儡材料从 ``src`` 转给 ``dst``：两行写在同一事务里。

        2026-11-20 第十轮（用户选「让傀儡材料也能送」）：
        `puppet_materials` 是一张表、主键 `(qq, name)`，所以 A→B 转账**本来就
        在同一个库里**，一个 `_tx()` 就能原子写完 —— 不需要把傀儡库并进
        `doupo.sqlite`（并库只会动 `DATA_PATH` 单源红线，换不来任何原子性）。

        返回 False = 前置条件不成立（数量不足 / 自己送自己 / 数量非正），
        不是错误；真写库失败抛 :class:`PuppetStoreError` 并整笔回滚。
        """
        src_qq, dst_qq = str(src_qq), str(dst_qq)
        name, n = str(name), int(n)
        if n <= 0 or src_qq == dst_qq or not name:
            return False
        async with self._lock:
            try:
                with self._tx() as con:
                    row = con.execute(
                        "SELECT qty FROM puppet_materials WHERE qq=? AND name=?",
                        (src_qq, name)).fetchone()
                    if not row or int(row["qty"]) < n:
                        return False
                    left = int(row["qty"]) - n
                    if left > 0:
                        con.execute(
                            "UPDATE puppet_materials SET qty=? WHERE qq=? AND name=?",
                            (left, src_qq, name))
                    else:
                        # 送空就删行：留 qty=0 会让「材料面板」多出一行空条目
                        con.execute(
                            "DELETE FROM puppet_materials WHERE qq=? AND name=?",
                            (src_qq, name))
                    con.execute(
                        "INSERT INTO puppet_materials(qq, name, qty) VALUES(?,?,?)"
                        " ON CONFLICT(qq,name) DO UPDATE SET qty=qty+excluded.qty",
                        (dst_qq, name, n))
                    if name == cfg.PUPPET_LORE_MATERIAL:
                        # 竹简 = 传承本身（遗迹掉落也是「进材料 + set_lore」一起做）。
                        # 只给物品不给 lore，受赠者会拿到一张永远用不掉的废纸。
                        # 同一事务内写，避免「物品到了、资格没到」的中间态。
                        con.execute(
                            "INSERT INTO puppet_eligibility(qq, lore, ready,"
                            " active_id, updated_at) VALUES(?,1,0,0,?)"
                            " ON CONFLICT(qq) DO UPDATE SET lore=1,"
                            " updated_at=excluded.updated_at",
                            (dst_qq, time.time()))
            except Exception as e:  # noqa: BLE001
                raise PuppetStoreError("赠送材料写盘失败，双方材料已回滚（%s）：%s"
                                       % (type(e).__name__, str(e)[:200])) from e
        return True

    async def take_materials(self, qq: str, cost: Dict[str, int]) -> bool:
        """原子扣料：任一不足则整笔不扣，返回 False。"""
        qq = str(qq)
        async with self._lock:
            with self._tx() as con:
                for name, n in cost.items():
                    row = con.execute(
                        "SELECT qty FROM puppet_materials WHERE qq=? AND name=?",
                        (qq, str(name))).fetchone()
                    if not row or int(row["qty"]) < int(n):
                        return False
                for name, n in cost.items():
                    con.execute(
                        "UPDATE puppet_materials SET qty=qty-? WHERE qq=? AND name=?",
                        (int(n), qq, str(name)))
        return True

    async def materials(self, qq: str) -> Dict[str, int]:
        with self._tx() as con:
            rows = con.execute(
                "SELECT name, qty FROM puppet_materials WHERE qq=? AND qty>0",
                (str(qq),)).fetchall()
        return {r["name"]: int(r["qty"]) for r in rows}

    # ---- 傀儡遗迹日副本（真实列，不进会话 JSON）----
    async def ruin_state(self, qq: str, day: str) -> dict:
        with self._tx() as con:
            row = con.execute(
                "SELECT stage, attempts FROM puppet_ruin_daily WHERE qq=? AND day=?",
                (str(qq), str(day))).fetchone()
        if not row:
            return {"stage": 0, "attempts": 0}
        return {"stage": int(row["stage"]), "attempts": int(row["attempts"])}

    async def ruin_clear(self, qq: str, day: str, stage: int) -> None:
        """通关一段：抬水位线（只记录最高已过段），并累计尝试次数。"""
        async with self._lock:
            try:
                with self._tx() as con:
                    con.execute(
                        "INSERT INTO puppet_ruin_daily(qq, day, stage, attempts, updated_at)"
                        " VALUES(?,?,?,?,?) ON CONFLICT(qq,day) DO UPDATE SET"
                        " stage=MAX(stage,excluded.stage),"
                        " attempts=attempts+1, updated_at=excluded.updated_at",
                        (str(qq), str(day), int(stage), 1, _now()))
            except Exception as e:  # noqa: BLE001
                raise PuppetStoreError("傀儡遗迹进度写入失败（%s）：%s"
                                       % (type(e).__name__, str(e)[:200])) from e


#: 进程级单例，与 store.STORE 同生命周期；由 doupo/__init__.py 持有。
PUPPET_STORE = PuppetStore()
