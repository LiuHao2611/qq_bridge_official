# -*- coding: utf-8 -*-
"""斗破苍穹 · 数据存储层（SQLite）。

## 为什么从 JSON 换成 SQLite（2026-11-17，用户「玩家数据肯定是要存到库里」）

旧实现是「一个 doupo.json 装全服 + 整份重写」：

* **写放大**：`put_player` 走 `save()` → `json.dump` 整份存档。代码里有 36 个写入点，
  1000 玩家时每敲一条命令就要重写约 700KB。
* **并发即丢档**：`asyncio.Lock` 只在**单进程内**有效。第二个实例、测试脚本、
  运维手工改档 —— 谁后落盘谁赢，输的那方**静默丢失**且日志里一个字都没有。
* **断电靠人恢复**：旧 `_write()` 只 `close()` 没有 `flush+fsync`，
  宿主断电可能截断文件；现在的兜底是「备份成 .corrupt- + 空库启动」，
  也就是那次回档必须人工找备份。

换库后：**一人一行**，写谁只写谁；WAL + 事务，崩溃自动回滚；跨进程文件锁。

## 设计要点（改之前先看完，别踩回去）

* `self._data` 仍是**内存权威态**：`get_player()` 返回的是**活字典**，调用方
  就地改完再 `put_player()`。这是全项目 36 个调用点的既有写法，不能改成
  「每次 get 都查库 + 返副本」，否则就地改的那部分会静默丢掉。
* 因此落盘分两条路：
  1. `put_player()` / `enable_group()` 走**快路径**：只 UPSERT 那一行；
  2. `save()` 走**兜底 diff**：拿内存和上次落盘快照逐条比对，只写变化的行，
     顺便删掉内存里已不存在的行 —— 这样"改了活字典但没调 put_player"的老写法
     照样落盘（以前靠整份重写碰巧生效，这里显式保住）。
* **不做自动回退**：SQLite 出错就抛 :class:`StoreError`，绝不偷偷退回 JSON。
  用户纪律：备选只能以显式选择存在，默认路径失败必须如实报错。
* 旧 `doupo.json` 只当**一次性导入源**：库为空且文件存在时导一次，
  原文件**原样不动**（就是回滚备份），导入记录写进 `meta` 表防重复导入。
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

#: 存档结构版本。3 = 原著 13 阶九星制（斗之气 1-9 … 斗帝 103）。
#: 1 = 最初「每境界 3 星 / 满级 29」；2 = 九星制但缺 斗之气/斗尊巅峰/半圣（满级 83）。
#: 旧存档在 load 时按 `cfg.migrate_lv(old, src_ver)` 就地换算，并按经验曲线
#: 等比缩放修为（保住「离下一级还差多少」的手感），逐玩家打 `lv_ver` 标记 ——
#: 即使写盘失败也不会二次换算（换算不可重复执行）。
SCHEMA_VER = 3

#: 库结构版本（PRAGMA user_version）。改表结构时 +1 并在 `_migrate_db` 里补迁移。
DB_VER = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS players (
    qq         TEXT PRIMARY KEY,
    group_id   TEXT    NOT NULL DEFAULT '',
    lv         INTEGER NOT NULL DEFAULT 1,
    exp        INTEGER NOT NULL DEFAULT 0,
    gold       INTEGER NOT NULL DEFAULT 0,
    updated_at REAL    NOT NULL DEFAULT 0,
    data       TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_players_group ON players(group_id);
CREATE INDEX IF NOT EXISTS idx_players_lv    ON players(lv);
CREATE TABLE IF NOT EXISTS groups (
    group_id TEXT PRIMARY KEY,
    added_at REAL NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS meta (
    k TEXT PRIMARY KEY,
    v TEXT NOT NULL
);
-- 2026-11-18 破境丹「一阶限 N 枚」：原著「大斗师在此阶唯有一次服用机会，
-- 再服则因药体抗性白费」「紫心破障丹同阶可连服两枚」。这是**玩家数据**，
-- 按最高声明必须是真实列，不能塞进 players.data 的 JSON blob。
-- 用自增 id 而不是复合主键：紫心破障丹同阶要吃两枚，复合主键会把第二枚压掉。
CREATE TABLE IF NOT EXISTS pill_use (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    qq        TEXT    NOT NULL,
    name      TEXT    NOT NULL,
    realm_idx INTEGER NOT NULL,
    used_at   REAL    NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_pill_use ON pill_use(qq, name, realm_idx);
-- 2026-11-19 自动调息：只存「上次结算的时刻」，气血/斗气本身仍在既有存档字段里。
-- 这也是**玩家数据**（不回它会凭空多算几小时恢复），按最高声明进真实列，不塞 JSON。
CREATE TABLE IF NOT EXISTS regen_clock (
    qq      TEXT PRIMARY KEY,
    last_at REAL NOT NULL DEFAULT 0
);
"""


def _log(level: str, msg: str) -> None:
    """写日志：优先 nonebot.log（线上 loguru），裸跑单测时退回 stdlib。

    单测会桩掉 nonebot（见 test_core.py 头部），此处不能硬依赖。
    只做日志降级，不影响任何业务分支。
    """
    try:
        from nonebot.log import logger as _lg
    except Exception:  # noqa: BLE001
        import logging

        _lg = logging.getLogger("doupo")
    getattr(_lg, level)(msg)


class StoreError(RuntimeError):
    """存档库读写失败。**必须**向上抛：不许静默当成功（会谎报"已保存"）。"""


#: 最近一次调息的实际恢复量（旁路缓存，**绝不写进玩家存档**）。
#: 由 Store.get_player() 填入，commands._regen_line() 读取展示。
#: 为什么不挂在玩家 dict 上：那是 store 里的活字典，任何临时字段都会被
#: put_player() 序列化进 players.data（最高声明：玩家数据只走真实列）。
REGEN_NOTICE: dict[str, dict] = {}


def _dumps(p: Dict[str, Any]) -> str:
    """压缩序列化（稳定输出：同一份内容永远同一串，用于 diff 判等）。"""
    return json.dumps(p, ensure_ascii=False, separators=(",", ":"))


class Store:
    def __init__(self, path: str | None = None, db_path: str | None = None):
        #: 旧 JSON 存档路径 —— 现在只用作「一次性导入源」，另行保留不动
        self.path = str(path or cfg.DATA_PATH)
        #: SQLite 库路径。显式 path 时按同目录同名推导，保证单测天然隔离
        self.db_path = str(db_path or (cfg.DB_PATH if path is None
                                       else os.path.splitext(self.path)[0] + ".sqlite"))
        self._lock = asyncio.Lock()
        self._data: Dict[str, Any] = {"players": {}, "groups": []}
        #: 与磁盘一致的快照（qq/group -> 序列化串），save() 用它算 diff
        self._snap: Dict[str, Dict[str, str]] = {"players": {}, "groups": {}}
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
        """确保表已建好。

        必须在**第一次 DB 访问**时自动执行，不能只依赖 `load()`：e2e / menu_full
        这类用例会先 `put_player()` 再造档，没建表就 `no such table: players`
        （2026-11-17 迁移当天就是这么红了 33 条）。
        """
        if not self._db_ready:
            self._init_db()
            self._db_ready = True

    @contextmanager
    def _tx(self):
        """一次事务：成功 commit、异常 rollback，**并且一定 close**。

        `with sqlite3.Connection` 只提交/回滚，不关连接。Windows 下没关的句柄会
        让临时库文件删不掉（WinError 32），所以这里显式收口。
        """
        self._ensure_ready()
        con = self._conn()
        try:
            with con:
                yield con
        finally:
            con.close()

    def _init_db(self) -> None:
        # 注意：这里不能用 _tx()（会递归回 _ensure_ready），直接开连接；
        # 连开连接都要包在 try 里 —— makedirs 失败（父路径是文件）也要变成
        # StoreError，否则上层收到裸 FileExistsError 就不算"如实报错"了。
        con = None
        try:
            con = self._conn()
            with con:
                self._rename_legacy_pill_use(con)
                con.executescript(_SCHEMA)
                self._copy_legacy_pill_use(con)
                ver = con.execute("PRAGMA user_version").fetchone()[0]
                if ver < DB_VER:
                    con.execute("PRAGMA user_version=%d" % DB_VER)
        except Exception as e:  # noqa: BLE001
            raise StoreError("存档库初始化失败（%s）：%s"
                             % (type(e).__name__, str(e)[:200])) from e
        finally:
            if con is not None:
                con.close()

    #: 破境丹记录表的旧形状（复合主键、无法记「同阶第二枚」）。
    _PILL_USE_OLD = "pill_use_legacy_20261118"

    @classmethod
    def _rename_legacy_pill_use(cls, con: sqlite3.Connection) -> None:
        """把「没有 id 列」的旧 pill_use 改名挪开，等新表建好再迁回来。

        只在旧形状存在时动手；新库/已迁移库原样不动。**不删数据**：
        旧行会在 ``_copy_legacy_pill_use`` 里逐条搬进新表。
        """
        cols = [r[1] for r in con.execute("PRAGMA table_info(pill_use)")]
        if not cols or "id" in cols:
            return
        con.execute("DROP TABLE IF EXISTS %s" % cls._PILL_USE_OLD)
        con.execute("ALTER TABLE pill_use RENAME TO %s" % cls._PILL_USE_OLD)

    @classmethod
    def _copy_legacy_pill_use(cls, con: sqlite3.Connection) -> None:
        """把挪开的旧行搬进新表，搬完 drop 旧表（幂等：旧表不存在就直接返回）。"""
        has = con.execute("SELECT name FROM sqlite_master WHERE type='table'"
                          " AND name=?", (cls._PILL_USE_OLD,)).fetchone()
        if not has:
            return
        con.execute(
            "INSERT INTO pill_use(qq, name, realm_idx, used_at)"
            " SELECT qq, name, realm_idx, used_at FROM %s" % cls._PILL_USE_OLD)
        con.execute("DROP TABLE %s" % cls._PILL_USE_OLD)

    # ---- 生命周期 ----
    async def load(self) -> None:
        async with self._lock:
            if self._loaded:
                return
            self._ensure_ready()
            self._read_all()
            if not self._data["players"] and not self._data["groups"]:
                self._import_legacy_json()
            self._migrate_if_needed()
            self._loaded = True

    def _read_all(self) -> None:
        try:
            with self._tx() as con:
                for row in con.execute("SELECT qq, data FROM players"):
                    p = json.loads(row[1])
                    if isinstance(p, dict):
                        self._data["players"][str(row[0])] = p
                        self._snap["players"][str(row[0])] = row[1]
                for row in con.execute("SELECT group_id FROM groups"):
                    self._data["groups"].append(str(row[0]))
                    self._snap["groups"][str(row[0])] = str(row[0])
        except Exception as e:  # noqa: BLE001
            # 库读不出来 = 玩家资产看不见。绝不"空库启动"（那会被后续写盘覆盖）。
            raise StoreError("存档库读取失败（%s）：%s"
                             % (type(e).__name__, str(e)[:200])) from e

    def _import_legacy_json(self) -> None:
        """首次启动：把旧 doupo.json 一次性导入。原文件原样保留（回滚备份）。"""
        if not os.path.exists(self.path):
            return
        try:
            with self._tx() as con:
                done = con.execute(
                    "SELECT v FROM meta WHERE k='imported_from'").fetchone()
            if done:
                return       # 导过了，绝不重复导（会把新进度覆盖成旧档）
        except Exception as e:  # noqa: BLE001
            raise StoreError("存档库 meta 读取失败（%s）" % type(e).__name__) from e

        try:
            with open(self.path, "r", encoding="utf-8") as f:
                raw = json.load(f)
            players = dict(raw.get("players") or {})
            groups = list(raw.get("groups") or [])
        except Exception as e:  # noqa: BLE001
            # 坏档不导入，但也**不许**删除/覆盖原文件：备份成 .corrupt- 留证据。
            bak = "%s.corrupt-%s" % (self.path, time.strftime("%Y%m%d-%H%M%S"))
            try:
                os.replace(self.path, bak)
            except Exception as e2:  # noqa: BLE001
                _log("error", "斗破旧存档损坏且无法备份 %s：%r / %r" % (self.path, e, e2))
                raise
            _log("error", "斗破旧存档损坏，已备份为 %s（%r），本次以空库启动"
                          "——请用备份恢复，勿直接继续玩" % (bak, e))
            return

        self._data["players"] = {str(k): v for k, v in players.items() if isinstance(v, dict)}
        self._data["groups"] = [str(g) for g in groups]
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
        _log("info", "斗破旧存档已导入 SQLite：%d 名玩家 / %d 个群（原文件保留：%s）"
                     % (len(self._data["players"]), len(self._data["groups"]), self.path))

    def _migrate_if_needed(self) -> None:
        """旧存档 → 原著 13 阶九星制（斗之气 1-9 … 斗帝 103）。

        逐玩家打 `lv_ver=SCHEMA_VER` 标记后再换 `lv`：换算**不可重复执行**
        （20 级再算一次会被当成旧 20 级，跑飞），所以哪怕写盘失败，
        下次启动时旧值仍在磁盘上、没有标记，也就不会双重换算。

        修为也要跟着缩放：经验曲线是 400·lv²/9，等级整体抬高后
        「离下一级还差多少」的占比会变，直接沿用原数值会让老玩家凭空变穷/变富。
        按 need_new / need_old 等比放大，手感不变。
        """
        players = self._data.get("players") or {}
        migrated = []
        for qq, p in players.items():
            if not isinstance(p, dict):
                continue
            cur_ver = int(p.get("lv_ver", 1) or 1)
            if cur_ver >= SCHEMA_VER:
                continue
            old = int(p.get("lv", 1) or 1)
            new = cfg.migrate_lv(old, cur_ver)
            # 旧版经验需求：1 版是 400·lv²，2 版起是 400·lv²/9
            old_need = (400 * old * old) if cur_ver <= 1 else (400 * old * old // 9)
            new_need = cfg.exp_need(new)
            if old_need > 0 and new_need > 0:
                p["exp"] = int(int(p.get("exp", 0) or 0) * new_need // old_need)
            p["lv"] = new
            p["lv_ver"] = SCHEMA_VER
            if new != old:
                migrated.append(f"{qq}:{old}->{new}")
        if migrated:
            _log("info", "斗破存档升级到原著13阶 lv=" + ",".join(migrated))
            try:
                self._flush_changed()
            except Exception as e:  # noqa: BLE001 — 写盘失败不能让机器人起不来
                _log("error", "斗破存档升级后写盘失败（内存已生效，下次启动会重算）："
                     + type(e).__name__ + ": " + str(e))

    # ---- 落盘 ----
    @staticmethod
    def _upsert_player(con: sqlite3.Connection, qq: str, p: dict, s: str,
                       ts: float) -> None:
        con.execute(
            "INSERT INTO players(qq, group_id, lv, exp, gold, updated_at, data)"
            " VALUES(?,?,?,?,?,?,?)"
            " ON CONFLICT(qq) DO UPDATE SET"
            "  group_id=excluded.group_id, lv=excluded.lv, exp=excluded.exp,"
            "  gold=excluded.gold, updated_at=excluded.updated_at,"
            "  data=excluded.data",
            (qq, str(p.get("group") or ""), int(p.get("lv") or 1),
             int(p.get("exp") or 0), int(p.get("gold") or 0), ts, s))

    def _flush_changed(self) -> bool:
        """把内存里与快照不同的行写回库；返回是否真的写了东西。

        只写变化的行 —— 这正是不再「全服重写」的关键。
        """
        players, snap_p = self._data.get("players") or {}, self._snap["players"]
        changed = []
        for qq, p in players.items():
            s = _dumps(p)
            if snap_p.get(qq) != s:
                changed.append((str(qq), s, p))
        gone = [qq for qq in snap_p if qq not in players]
        groups, snap_g = self._data.get("groups") or [], self._snap["groups"]
        new_groups = [g for g in groups if str(g) not in snap_g]
        gone_groups = [g for g in snap_g if g not in {str(x) for x in groups}]
        if not (changed or gone or new_groups or gone_groups):
            return False

        now = time.time()
        try:
            with self._tx() as con:
                for qq, s, p in changed:
                    self._upsert_player(con, qq, p, s, now)
                if gone:
                    con.executemany("DELETE FROM players WHERE qq=?",
                                    [(q,) for q in gone])
                if new_groups:
                    con.executemany("INSERT OR IGNORE INTO groups(group_id, added_at)"
                                    " VALUES(?,?)", [(str(g), now) for g in new_groups])
                if gone_groups:
                    con.executemany("DELETE FROM groups WHERE group_id=?",
                                    [(str(g),) for g in gone_groups])
        except Exception as e:  # noqa: BLE001
            # 快照不更新 → 下次 save 会重试，不会"以为存好了"
            raise StoreError("斗破存档写入失败（%s）：%s"
                             % (type(e).__name__, str(e)[:200])) from e

        for qq, s, _p in changed:
            snap_p[qq] = s
        for qq in gone:
            snap_p.pop(qq, None)
        for g in new_groups:
            snap_g[str(g)] = str(g)
        for g in gone_groups:
            snap_g.pop(g, None)
        return True

    async def save(self) -> None:
        async with self._lock:
            self._flush_changed()

    # ---- 玩家 ----
    async def get_player(self, qq: str) -> Optional[dict]:
        """取玩家存档（**所有读玩家数据的唯一底层入口**）。

        2026-11-19 事故：自动调息最初挂在 `commands._player()`，但按钮点击、
        人物卡/战报渲染等大量入口直接调 `store.get_player()` —— 用户点了一上午
        卡片，调息时钟一动不动。修法是把结算下沉到这里：不管口令还是按钮，
        只要读玩家存档，就先结算一次调息。

        ⚠️ 必须**先出锁再调 `_maybe_regen`**：后者要拿同一把锁写 regen_clock /
        put_player，asyncio.Lock 不可重入，在持锁状态下调用会死锁。
        """
        key = str(qq)
        async with self._lock:
            p = self._data["players"].get(key)
        if p is not None:
            await self._maybe_regen(key, p)
        return p

    async def _maybe_regen(self, qq: str, p: dict) -> None:
        """按 regen_clock 结算一次气血/斗气恢复（失败只记日志，不拦任何读取）。"""
        try:
            now = time.time()
            last = await self.regen_last(qq)
            if last <= 0:
                # 首次见到（含老档迁移）：只落基准，不按「从 1970 年算起」补历史。
                await self.set_regen_last(qq, now)
                return
            # 延迟 import：core 不依赖 store，运行时无环；用 importlib 避开
            # __init__.py 把包属性 `store` 替换成 STORE 实例的同名遮蔽坑。
            import importlib
            core = importlib.import_module(__package__ + ".core")
            res = core.regen(p, now - last)
            if not res["consumed"]:
                return
            await self.set_regen_last(qq, last + float(res["consumed"]))
            if res["touched"]:
                REGEN_NOTICE[qq] = res
                await self.put_player(p)
        except Exception as e:  # noqa: BLE001
            # 调息是附加能力，失败不许拦任何口令/按钮；但必须留痕，不能静默。
            _log("warning", "调息结算失败（%s）：%s" % (type(e).__name__, str(e)[:160]))

    async def put_player(self, p: dict) -> None:
        async with self._lock:
            qq = str(p["qq"])
            self._data["players"][qq] = p
            s = _dumps(p)
            if self._snap["players"].get(qq) == s:
                return          # 内容没变：一个字节都不写
            now = time.time()
            try:
                with self._tx() as con:
                    self._upsert_player(con, qq, p, s, now)
            except Exception as e:  # noqa: BLE001
                raise StoreError("斗破存档写入失败（%s）：%s"
                                 % (type(e).__name__, str(e)[:200])) from e
            self._snap["players"][qq] = s

    async def all_players(self, group: Optional[str] = None) -> List[dict]:
        async with self._lock:
            ps = list(self._data["players"].values())
        if group is not None:
            ps = [p for p in ps if str(p.get("group")) == str(group)]
        return ps

    # ---- 群启用 ----
    async def enable_group(self, group: str) -> None:
        async with self._lock:
            if str(group) not in self._data["groups"]:
                self._data["groups"].append(str(group))
                self._flush_changed()

    async def groups(self) -> List[str]:
        async with self._lock:
            return list(self._data["groups"])

    # ---- 丹药服用记录（pill_use，真实列） ----
    async def pill_used_counts(self, qq: str, name: str) -> dict:
        """该玩家在这枚丹药上**每个境界各服过几次** → ``{realm_idx: 次数}``。

        破境丹的「一阶限 N 枚」靠它判（原著抗性）。返回 dict 而不是集合：
        紫心破障丹同阶可连服两枚，只记「服过没有」会把它压成一枚。
        """
        async with self._lock:
            try:
                with self._tx() as con:
                    rows = con.execute(
                        "SELECT realm_idx, COUNT(*) FROM pill_use"
                        " WHERE qq=? AND name=? GROUP BY realm_idx",
                        (str(qq), str(name))).fetchall()
            except Exception as e:  # noqa: BLE001
                raise StoreError("丹药记录读取失败（%s）：%s"
                                 % (type(e).__name__, str(e)[:200])) from e
        return {int(r[0]): int(r[1]) for r in rows}

    async def mark_pill_used(self, qq: str, name: str, realm_idx: int) -> None:
        """记一次「这枚丹药在该境界又服了一枚」，返回该境界累计次数。

        主键改成 (qq,name,realm_idx,used_at) 才能记多枚；这里用 **INSERT 不 OR
        REPLACE**，重复服用是真实的第二行 —— 五枚同阶丹药不能被压成一行。
        """
        async with self._lock:
            try:
                with self._tx() as con:
                    con.execute(
                        "INSERT INTO pill_use(qq, name, realm_idx, used_at)"
                        " VALUES(?,?,?,?)",
                        (str(qq), str(name), int(realm_idx), time.time()))
                    row = con.execute(
                        "SELECT COUNT(*) FROM pill_use WHERE qq=? AND name=?"
                        " AND realm_idx=?",
                        (str(qq), str(name), int(realm_idx))).fetchone()
            except Exception as e:  # noqa: BLE001
                raise StoreError("丹药记录写入失败（%s）：%s"
                                 % (type(e).__name__, str(e)[:200])) from e
        return int(row[0]) if row else 0

    # ---- 自动调息的时间基准 ----
    async def regen_last(self, qq: str) -> float:
        """该玩家上次结算调息的 unix 秒；没有记录返回 0（= 首次见到，不补历史）。"""
        async with self._lock:
            try:
                with self._tx() as con:
                    row = con.execute(
                        "SELECT last_at FROM regen_clock WHERE qq=?",
                        (str(qq),)).fetchone()
            except Exception as e:  # noqa: BLE001
                raise StoreError("调息时刻读取失败（%s）：%s"
                                 % (type(e).__name__, str(e)[:200])) from e
        return float(row[0]) if row else 0.0

    async def set_regen_last(self, qq: str, ts: float) -> None:
        """写入新的调息时刻（UPSERT）。

        ⚠️ 调用方只能传「上次时刻 + 整 tick 秒数」，不能直接传 now：
        余数被抹掉的话玩家永远等不到下一 tick（每次命令都从头计时）。
        """
        async with self._lock:
            try:
                with self._tx() as con:
                    con.execute(
                        "INSERT INTO regen_clock(qq, last_at) VALUES(?,?)"
                        " ON CONFLICT(qq) DO UPDATE SET last_at=excluded.last_at",
                        (str(qq), float(ts)))
            except Exception as e:  # noqa: BLE001
                raise StoreError("调息时刻写入失败（%s）：%s"
                                 % (type(e).__name__, str(e)[:200])) from e


def now() -> float:
    return time.time()


#: 进程级单例（2026-11-16）：斗破出图/命令与「官方按钮点击分发」必须共享**同一**
#: Store 实例，否则按钮回调自建 Store 读不到玩家存档（或读到旧内存态）。
#: 由 ``doupo/__init__.py`` 持有并 ``load()``；按钮回调（button_dispatch）直接
#: 引用本常量，不依赖 nonebot 插件模块名（nonebot 的模块命名随 search_path 变化，
#: 用 sys.modules 猜名字不可靠）。测试可自行 new Store 隔离。
STORE = Store()
