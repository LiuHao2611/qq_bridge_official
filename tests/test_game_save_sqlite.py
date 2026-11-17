# -*- coding: utf-8 -*-
"""斗破 / 修仙存档：JSON → SQLite 迁移门禁（2026-11-17）。

用户原话：「玩家数据肯定是要存到库里，你存到 JSON 里安全吗，稳定吗？」
迁移本身有风险，所以这里把**真实犯过的错**固化成断言：

1. **导入一次且只导一次** —— 重复导入会把新进度覆盖成旧档（等于回档）。
2. **旧 JSON 一个字节都不许动** —— 它就是回滚备份；被改写就没退路了。
3. **写一个玩家只写一行** —— 旧实现整份重写全服（36 个写入点，写放大）。
4. **"改了活字典但没调 put_player" 仍要落盘** —— 旧实现靠整份重写碰巧存住，
   改成行级写以后必须由 `save()` 的兜底 diff 接住，否则静默丢档。
5. **坏档先备份再启动** —— 不许被空库覆盖。
6. **写盘失败必须抛 StoreError** —— 不许谎报"已保存"（用户纪律：禁止兜底）。
7. **等级迁移（lv_ver）要落盘** —— 换算是不可重复执行的，标记必须写进库。

运行：python tests/test_game_save_sqlite.py
"""

from __future__ import annotations

import asyncio
import hashlib
import importlib
import json
import os
import shutil
import sys
import tempfile
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLUGINS_DIR = os.path.join(ROOT, "src", "plugins")
for _p in (ROOT, PLUGINS_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def _stub_nonebot():
    """与 test_core.py 同款：让插件能在无 QQ 环境加载（只校验存储层）。"""
    nb = types.ModuleType("nonebot")
    nb.get_logger = lambda *a, **k: types.SimpleNamespace(
        info=lambda *a, **k: None, warning=lambda *a, **k: None,
        error=lambda *a, **k: None, debug=lambda *a, **k: None)

    def _matcher(*a, **k):
        m = types.SimpleNamespace()
        m.handle = lambda *a, **k: (lambda f: f)
        m.finish = lambda *a, **k: None
        m.receive = lambda *a, **k: (lambda f: f)
        return m

    nb.on_regex = _matcher
    nb.on_command = _matcher
    nb.on_message = _matcher
    nb.on_notice = _matcher
    nb.on_request = _matcher
    nb.on_startup = lambda f: f
    nb.on_shutdown = lambda f: f
    nb.require = lambda *a, **k: None
    sys.modules["nonebot"] = nb
    log = types.ModuleType("nonebot.log")
    log.logger = types.SimpleNamespace(
        info=lambda *a, **k: None, warning=lambda *a, **k: None,
        error=lambda *a, **k: None, debug=lambda *a, **k: None)
    sys.modules["nonebot.log"] = log


def _load_pkg(name: str):
    """只把插件当命名空间包挂上，**不执行**它的 __init__.py（那要连群/出图）。

    store.py 里是 `from . import config as cfg`，只要有 __path__ 就能相对导入。
    """
    if name not in sys.modules:
        pkg = types.ModuleType(name)
        pkg.__path__ = [os.path.join(PLUGINS_DIR, name)]
        sys.modules[name] = pkg
    return importlib.import_module(name + ".store")


_stub_nonebot()
ds = _load_pkg("doupo")
xs = _load_pkg("xiuxian")


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------
def _tmpdir(tag: str) -> str:
    return tempfile.mkdtemp(prefix="save_sqlite_%s_" % tag)


def _sha1(p: str) -> str:
    with open(p, "rb") as f:
        return hashlib.sha1(f.read()).hexdigest()


def _write_json(p: str, obj: dict) -> None:
    with open(p, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)


def _player(qq="10001", name="萧炎", group="g1", lv=12, exp=345, gold=678):
    # lv_ver=3 → 不触发等级迁移，方便逐字段比对导入是否无损
    return {"qq": qq, "name": name, "group": group, "lv": lv, "exp": exp,
            "gold": gold, "lv_ver": 3, "bag": [], "skills": ["焰分噬浪尺"]}


def _legacy_doupo() -> dict:
    return {"players": {"10001": _player(),
                        "10002": _player("10002", "药老", "g1", 40, 999, 4321)},
            "groups": ["g1", "g2"], "ver": 3}


# ---------------------------------------------------------------------------
# 0. 库路径必须只由 DATA_PATH 推导（两条来源 = 测试隔离必漏）
# ---------------------------------------------------------------------------
def test_db_path_follows_data_path_single_source():
    """存档库和旧 JSON 必须在同一目录同名，且**没有独立的环境变量入口**。

    真实事故（2026-11-17）：一开始给库路径留了 `DOUPO_DB_PATH`，compose 注入成
    生产路径；而 test_e2e / test_menu_full 只改 `DOUPO_DATA_PATH` 到临时目录。
    结果跑一次测试就往**生产库**塞进 4 个假玩家（10001/10002/10003/10021）
    和假群 900000001。同一个东西只许有一个来源。
    """
    for mod, name in ((ds, "斗破"), (xs, "修仙")):
        cfg = mod.cfg
        want = os.path.splitext(cfg.DATA_PATH)[0] + ".sqlite"
        assert cfg.DB_PATH == want, (
            "%s的库路径没有跟着 DATA_PATH 走：DB_PATH=%r，应为 %r"
            % (name, cfg.DB_PATH, want))
        assert os.path.dirname(cfg.DB_PATH) == os.path.dirname(cfg.DATA_PATH), \
            "%s库和旧档不在同一目录（测试隔离会漏）" % name


# ---------------------------------------------------------------------------
# 1. 斗破：导入一次、只导一次、原件不动
# ---------------------------------------------------------------------------
def test_doupo_import_once_and_source_untouched():
    d = _tmpdir("dp_import")
    jp = os.path.join(d, "doupo.json")
    _write_json(jp, _legacy_doupo())
    before = _sha1(jp)

    st = ds.Store(jp)
    asyncio.run(st.load())
    assert sorted(await_all(st)) == ["10001", "10002"], "导入后玩家不全"
    p = asyncio.run(st.get_player("10001"))
    assert p["gold"] == 678 and p["lv"] == 12, "导入内容与旧档不一致"
    assert _sha1(jp) == before, "旧 JSON 被改写了（它是回滚备份，绝不许动）"

    # 改一条再重开：绝不许被旧档二次覆盖
    p["gold"] = 999999
    asyncio.run(st.put_player(p))
    st2 = ds.Store(jp)
    asyncio.run(st2.load())
    assert asyncio.run(st2.get_player("10001"))["gold"] == 999999, \
        "第二次启动又导了一遍旧档（新进度被旧档覆盖 = 回档）"
    assert _sha1(jp) == before, "第二次启动改写了旧 JSON"


def await_all(st):
    return [p["qq"] for p in asyncio.run(st.all_players())]


def test_import_not_repeated_after_all_players_deleted():
    """库被清空（比如运维手动清人）后重开，**不许**把旧 JSON 又导回来。

    这就是 `meta.imported_from` 那条护栏存在的唯一理由：光看"库空不空"不够，
    库空也可能是"玩家被合法删光了"，再导一次等于让旧档诈尸。
    """
    import sqlite3
    d = _tmpdir("dp_noimport")
    jp = os.path.join(d, "doupo.json")
    _write_json(jp, _legacy_doupo())
    st = ds.Store(jp)
    asyncio.run(st.load())
    assert await_all(st), "前置：首次导入应有玩家"

    con = sqlite3.connect(os.path.join(d, "doupo.sqlite"))
    try:
        with con:
            # 玩家和群都清掉 —— 只清玩家的话"库是否为空"的判据仍为假，
            # 根本进不到导入分支，测不到护栏。
            con.execute("DELETE FROM players")
            con.execute("DELETE FROM groups")
    finally:
        con.close()

    st2 = ds.Store(jp)
    asyncio.run(st2.load())
    assert await_all(st2) == [], "库清空后又把旧 JSON 导回来了（旧档诈尸）"


# ---------------------------------------------------------------------------
# 2. 斗破：写一个玩家只写一行（不再全服重写）
# ---------------------------------------------------------------------------
def test_doupo_put_player_writes_only_that_row():
    d = _tmpdir("dp_row")
    st = ds.Store(os.path.join(d, "doupo.json"))
    asyncio.run(st.load())
    for qq in ("10001", "10002"):
        asyncio.run(st.put_player(_player(qq)))
    ts_before = _row_ts(os.path.join(d, "doupo.sqlite"), "10002")

    p = asyncio.run(st.get_player("10001"))
    p["gold"] = 12345
    asyncio.run(st.put_player(p))

    assert _row_ts(os.path.join(d, "doupo.sqlite"), "10002") == ts_before, \
        "改一个玩家把别人的行也重写了（写放大没解决）"
    assert _row_gold(os.path.join(d, "doupo.sqlite"), "10001") == 12345


def _row_ts(db: str, qq: str):
    import sqlite3
    con = sqlite3.connect(db)
    try:
        r = con.execute("SELECT updated_at FROM players WHERE qq=?", (qq,)).fetchone()
        return r[0] if r else None
    finally:
        con.close()


def _row_gold(db: str, qq: str):
    import sqlite3
    con = sqlite3.connect(db)
    try:
        r = con.execute("SELECT gold FROM players WHERE qq=?", (qq,)).fetchone()
        return r[0] if r else None
    finally:
        con.close()


# ---------------------------------------------------------------------------
# 3. 斗破：「改了活字典但没调 put_player」也要落盘（save 兜底 diff 接住）
# ---------------------------------------------------------------------------
def test_doupo_implicit_mutation_caught_by_save():
    d = _tmpdir("dp_implicit")
    jp = os.path.join(d, "doupo.json")
    st = ds.Store(jp)
    asyncio.run(st.load())
    asyncio.run(st.put_player(_player("10001")))

    live = asyncio.run(st.get_player("10001"))   # 活字典：就地改
    live["exp"] = 777777
    asyncio.run(st.save())                        # 没有 put_player

    st2 = ds.Store(jp)
    asyncio.run(st2.load())
    assert asyncio.run(st2.get_player("10001"))["exp"] == 777777, \
        "就地改了活字典、只调 save() 的写法丢档了（老代码就是这么写的）"


# ---------------------------------------------------------------------------
# 4. 斗破：坏档先备份、写盘失败要抛错、等级迁移要落盘
# ---------------------------------------------------------------------------
def test_doupo_corrupt_json_backed_up_not_overwritten():
    d = _tmpdir("dp_corrupt")
    jp = os.path.join(d, "doupo.json")
    with open(jp, "w", encoding="utf-8") as f:
        f.write('{"players": {"10001": ')     # 截断
    st = ds.Store(jp)
    asyncio.run(st.load())                    # 不许崩、不许清空原件
    baks = [n for n in os.listdir(d) if ".corrupt-" in n]
    assert baks, "坏档没有备份就被丢弃（玩家存档等于销毁）"
    assert not os.path.exists(jp), "坏档还留在原位置，下次启动会再报错"


def test_doupo_write_failure_raises():
    d = _tmpdir("dp_werr")
    blocker = os.path.join(d, "blocker")
    with open(blocker, "w", encoding="utf-8") as f:
        f.write("x")
    st = ds.Store(os.path.join(d, "doupo.json"))
    st.db_path = os.path.join(blocker, "doupo.sqlite")   # 父路径是文件 → 必失败
    raised = False
    try:
        asyncio.run(st.load())
    except ds.StoreError:
        raised = True
    assert raised, "库打不开却当成功——会谎报「已保存」"


def test_doupo_lv_migration_is_persisted():
    d = _tmpdir("dp_mig")
    jp = os.path.join(d, "doupo.json")
    old = _player()
    old.pop("lv_ver")            # 老档没有版本标记 → 触发迁移
    _write_json(jp, {"players": {"10001": old}, "groups": []})

    st = ds.Store(jp)
    asyncio.run(st.load())
    lv1 = asyncio.run(st.get_player("10001"))["lv"]
    assert lv1 != 12, "老档等级没按九星制换算"

    st2 = ds.Store(jp)
    asyncio.run(st2.load())
    assert asyncio.run(st2.get_player("10001"))["lv"] == lv1, \
        "迁移没落盘，二次启动又换算一遍（等级会跑飞）"


# ---------------------------------------------------------------------------
# 5. 修仙：导入 / 宗门 / 群启用 / 世界状态 / 就地改
# ---------------------------------------------------------------------------
def _legacy_xx() -> dict:
    return {"players": {"20001": {"qq": "20001", "name": "韩立", "group": "g1",
                                  "realm": "筑基", "exp": 100, "ling_shi": 55}},
            "sects": {"s1": {"id": "s1", "name": "青云门", "members": ["20001"]}},
            "enabled": ["g1"],
            "world": {"g1": {"boss": {"name": "赤炎兽", "hp": 1000}}},
            "schema_version": 1}


def test_xiuxian_import_and_all_entities():
    d = _tmpdir("xx_import")
    jp = os.path.join(d, "xiuxian.json")
    _write_json(jp, _legacy_xx())
    before = _sha1(jp)

    st = xs.Store(jp)
    asyncio.run(st.load())
    assert asyncio.run(st.get_player("20001"))["ling_shi"] == 55
    assert asyncio.run(st.get_sect("s1"))["name"] == "青云门", "宗门没导进来"
    assert asyncio.run(st.enabled_groups()) == ["g1"], "群启用没导进来"
    w = asyncio.run(st.get_world("g1"))
    assert w["boss"]["hp"] == 1000, "世界Boss状态没导进来"
    assert _sha1(jp) == before, "旧 JSON 被改写了"

    # 各实体都能跨实例读回
    st2 = xs.Store(jp)
    asyncio.run(st2.load())
    assert asyncio.run(st2.all_sects()) and asyncio.run(st2.all_players())
    assert asyncio.run(st2.enabled_groups()) == ["g1"]


def test_xiuxian_implicit_world_mutation_caught_by_save():
    d = _tmpdir("xx_world")
    jp = os.path.join(d, "xiuxian.json")
    _write_json(jp, _legacy_xx())
    st = xs.Store(jp)
    asyncio.run(st.load())

    w = asyncio.run(st.get_world("g1"))   # 活字典：get_world/set_world 的老写法
    w["boss"]["hp"] = 1
    asyncio.run(st.save())

    st2 = xs.Store(jp)
    asyncio.run(st2.load())
    assert asyncio.run(st2.get_world("g1"))["boss"]["hp"] == 1, \
        "就地改世界状态没落盘（下次刷新又被满血覆盖）"


def test_xiuxian_put_player_writes_only_that_row():
    d = _tmpdir("xx_row")
    st = xs.Store(os.path.join(d, "xiuxian.json"))
    asyncio.run(st.load())
    for qq in ("20001", "20002"):
        asyncio.run(st.put_player({"qq": qq, "group": "g1", "realm": "筑基",
                                   "exp": 1, "ling_shi": 1}))
    db = os.path.join(d, "xiuxian.sqlite")
    ts_before = _row_ts(db, "20002")
    p = asyncio.run(st.get_player("20001"))
    p["ling_shi"] = 4242
    asyncio.run(st.put_player(p))
    assert _row_ts(db, "20002") == ts_before, "改一个道友把别人的行也重写了"


# ---------------------------------------------------------------------------
# 6. 真实存档：现网 data/ 里的文件导进来必须一条不少
# ---------------------------------------------------------------------------
def test_real_save_files_import_without_loss():
    cases = [
        (ds, os.path.join(ROOT, "data", "doupo", "doupo.json"), "players"),
        (xs, os.path.join(ROOT, "data", "xiuxian", "xiuxian.json"), "players"),
    ]
    for mod, real, _key in cases:
        if not os.path.exists(real):
            print("      SKIP 没有现网存档：%s" % real)
            continue
        with open(real, "r", encoding="utf-8") as f:
            raw = json.load(f)
        d = _tmpdir("real")
        jp = os.path.join(d, os.path.basename(real))
        shutil.copy2(real, jp)
        before = _sha1(jp)

        st = mod.Store(jp)
        asyncio.run(st.load())
        got = {p["qq"] for p in asyncio.run(st.all_players())}
        want = {str(k) for k in (raw.get("players") or {})}
        assert got == want, "%s 导入后玩家数不一致：%d vs %d" % (
            os.path.basename(real), len(got), len(want))
        assert _sha1(jp) == before, "现网存档被改写了：%s" % real
        for qq in want:      # 逐字段核（迁移过的等级字段除外）
            a, b = asyncio.run(st.get_player(qq)), dict(raw["players"][str(qq)])
            for k, v in b.items():
                if k in ("lv", "lv_ver", "exp"):
                    continue
                assert a.get(k) == v, "%s:%s 字段 %s 导入后不一致" % (
                    os.path.basename(real), qq, k)


# ---------------------------------------------------------------------------
def _main():
    fns = [(n, f) for n, f in sorted(globals().items())
           if n.startswith("test_") and callable(f)]
    failed = 0
    for n, f in fns:
        try:
            f()
            print("PASS", n)
        except AssertionError as e:
            failed += 1
            print("FAIL", n, "->", e)
        except Exception as e:  # noqa: BLE001
            failed += 1
            print("FAIL", n, "-> %s: %s" % (type(e).__name__, e))
    print("\n%d/%d 通过，失败 %d" % (len(fns) - failed, len(fns), failed))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(_main())
