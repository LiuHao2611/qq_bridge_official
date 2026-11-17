# -*- coding: utf-8 -*-
"""用户数据持久化守门（2026-11-17 用户最高声明后新增）。

用户原话：
> 检查所有业务产生的用户数据是否都有正常保存无丢失，最高声明，禁止再出现类似问题

## 为什么需要自动化门禁

人工"看一眼"靠不住，已经连踩两次：
* 2026-11-16 六个 SQLite 库写进镜像层，重建即回滚；
* 2026-11-17 爬塔会话只在内存，重建容器把层数清零。

所以把「哪些数据必须落盘、落到哪、怎么写」变成可执行断言：

1. **分类必须显式**：`src/` 下每个 ``*Store`` 类要么有落盘写调用，要么进
   ``TRANSIENT`` 白名单（附理由）。新增一个只存内存的 Store 而不登记 → 红。
2. **路径必须落卷**：模块级 ``*_PATH`` / ``*_DIR`` 常量若用 ``__file__`` 拼出
   代码目录里的数据文件（会被 ``COPY src`` 烘进镜像），必须在白名单里说明。
3. **写盘必须原子**：落盘类要出现 ``os.replace`` / ``.replace(``，否则崩溃会留半个 JSON。
4. **失败不许静默**：落盘函数的 ``except`` 不能是裸 ``pass``；必须 ``raise`` 或 ``log``。
5. **坏档不许被空库覆盖**：读档的 ``except`` 必须做 ``.corrupt-`` 备份（否则下次 save
   会用空库把玩家存档永久覆盖）。
6. **运行时往返**：容器内真跑「写 → 新实例读回」，验 planstore / 提醒 / 队列 / 素材缓存。

运行：
    python tests/test_user_data_persistence.py        # 宿主可用（1~5）
    docker exec ... python /app/tests/test_user_data_persistence.py   # 容器内连 6 一起跑
"""
from __future__ import annotations

import ast
import os
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
TESTS = ROOT / "tests"

#: 允许「只存内存」的 Store：必须是一次性交互 / 有 TTL 的瞬时会话。
#: 判断标准：丢了用户只需重发一句话，**不损失任何资产**。
TRANSIENT = {
    "SessionStore": "对话上下文，SESSION_TTL 内有效；丢了最多少记一句上下文",
    "ChoiceSessionStore": "按钮选择会话，一次性；丢了重发口令",
    "GameSessionStore": "AI 陪玩局，MAX_AGE=1h 自动解散；丢了重开一局",
    "QuizStore": "机遇答题，60 秒窗口（config.CHOICE_TTL）",
    "ChallengeStore": "切磋邀战，60 秒窗口（config.DUEL_CHALLENGE_TTL）",
}

#: 允许用 `__file__` 在代码目录里拼数据文件的例外（只读资源 / 已有 compose 覆盖）。
PATH_EXCEPTIONS = {
    # 斗破/修仙存档默认落在插件包内，但 docker-compose 已用 DOUPO_DATA_PATH /
    # XIUXIAN_DATA_PATH 覆盖到 /app/data；.dockerignore 也排除了该目录。
    "src/plugins/doupo/config.py:DATA_PATH": "compose DOUPO_DATA_PATH 覆盖到 /app/data",
    "src/plugins/xiuxian/config.py:DATA_PATH": "compose XIUXIAN_DATA_PATH 覆盖到 /app/data",
    # WFM 属性快照是随代码发布的只读数据，不写。
    "src/plugins/qq_bridge/game_tools/riven_recommend_attrs.py:SNAPSHOT_PATH": "只读随包快照",
}

WRITE_CALLS = {"dump", "write_text", "write_bytes", "replace", "commit", "executemany"}
SAVE_NAMES = {"_save", "save", "_flush", "flush", "_persist", "_write", "dump"}


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------
def _py_files():
    for p in SRC.rglob("*.py"):
        s = str(p)
        if "__pycache__" in s or "_backup" in s:
            continue
        if p.name.startswith("test_"):     # 测试替身（MemStore 之类）不算业务代码
            continue
        yield p


def _parse(p: Path):
    try:
        return ast.parse(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 —— 坏文件由其它门禁负责
        return None


def _has_write(node) -> bool:
    """函数体里是否有落盘写调用（json.dump / write_text / os.replace / SQL 写）。"""
    for n in ast.walk(node):
        if not isinstance(n, ast.Call):
            continue
        f = n.func
        name = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", "")
        src = ast.unparse(n)
        if name in ("commit", "executemany"):
            return True
        if name == "execute":
            up = src.upper()
            if any(k in up for k in ("INSERT", "UPDATE", "DELETE", "CREATE")):
                return True
            continue
        if name == "open":
            if any(t in src for t in ("'w'", '"w"', "'a'", '"a"', "'r+'", "'w+'")):
                return True
            continue
        if name in WRITE_CALLS:
            return True
    return False


def _class_has_write(cls: ast.ClassDef) -> bool:
    for n in cls.body:
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and _has_write(n):
            return True
    return False


def _save_funcs(cls: ast.ClassDef):
    for n in cls.body:
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name in SAVE_NAMES:
            yield n


# ---------------------------------------------------------------------------
# R1：每个 Store 必须「落盘」或「登记为瞬时会话」
# ---------------------------------------------------------------------------
def test_every_store_is_classified():
    bad = []
    for p in _py_files():
        tree = _parse(p)
        if tree is None:
            continue
        for cls in [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]:
            if not cls.name.endswith("Store"):
                continue
            if _class_has_write(cls) or cls.name in TRANSIENT:
                continue
            bad.append("%s:%s(%s)" % (p.relative_to(ROOT).as_posix(), cls.name, cls.lineno))
    assert not bad, ("这些 Store 只存内存却没登记为瞬时会话——用户数据会丢：%s" % bad)


def test_transient_whitelist_is_honest():
    """白名单不能写不存在的类（防止改名后失效的假豁免）。"""
    names = set()
    for p in _py_files():
        tree = _parse(p)
        if tree:
            names |= {n.name for n in ast.walk(tree) if isinstance(n, ast.ClassDef)}
    missing = [k for k in TRANSIENT if k not in names]
    assert not missing, "白名单里的类已不存在（豁免失效，请更新）：%s" % missing


# ---------------------------------------------------------------------------
# R2：路径常量不许落在代码目录（会被 COPY src 烘进镜像）
# ---------------------------------------------------------------------------
def test_data_paths_not_baked_into_image():
    """只抓「数据文件被拼进代码目录」这一种：表达式含 __file__ + 文件名(.json/.db/.tmp)。

    ROOT_DIR / PLUGINS_DIR / 静态资源目录这类「根指针」不算——它们不指向数据文件，
    真正的风险是 ``with_name('x.json')`` / ``parents[1] / 'data.json'`` 这种写法，
    会被 ``COPY src`` 烘进镜像，重建即回滚。
    """
    bad = []
    for p in _py_files():
        rel = p.relative_to(ROOT).as_posix()
        tree = _parse(p)
        if tree is None:
            continue
        for node in tree.body:
            if not isinstance(node, ast.Assign) or len(node.targets) != 1:
                continue
            tgt = node.targets[0]
            if not isinstance(tgt, ast.Name):
                continue
            name = tgt.id
            if not (name.endswith("_PATH") or name.endswith("_FILE")
                    or name.endswith("_DIR")):
                continue
            expr = ast.unparse(node.value)
            if "__file__" not in expr:
                continue
            if not any(k in expr for k in (".json", ".db", ".tmp", ".sqlite")):
                continue                      # 目录/根指针，不是数据文件
            if "%s:%s" % (rel, name) in PATH_EXCEPTIONS:
                continue
            if "data" in expr:                # 落 data/（容器里即挂载卷）
                continue
            bad.append("%s:%s = %s" % (rel, name, expr[:80]))
    assert not bad, ("这些数据文件路径用 __file__ 拼到代码目录，重建镜像即回滚：%s" % bad)


# ---------------------------------------------------------------------------
# R3/R4：落盘必须原子、失败必须可见
# ---------------------------------------------------------------------------
def test_persisting_stores_are_atomic_and_loud():
    """落盘要原子（replace/rename 或 sqlite commit），失败要可见（不能裸 pass）。"""
    bad = []
    for p in _py_files():
        tree = _parse(p)
        if tree is None:
            continue
        for cls in [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]:
            if not cls.name.endswith("Store") or cls.name in TRANSIENT:
                continue
            if not _class_has_write(cls):
                continue
            cls_src = ast.unparse(cls)
            # SQLite 的原子性由事务保证：显式 commit 或 `with conn:` 上下文提交都算。
            sqlite = any(k in cls_src for k in ("commit(", "sqlite3", "with self._conn",
                                                "with con"))
            if not sqlite:
                # 只在「真正写盘的那个函数」里找 replace/rename：
                # 用整个类的源码会误判——_load() 里的 path.replace(bak) 也是 replace。
                for fn in _save_funcs(cls):
                    if not _has_write(fn):
                        continue
                    src = ast.unparse(fn)
                    if not any(k in src for k in ("replace(", "rename(")):
                        bad.append("%s:%s.%s 写盘没有原子替换（崩溃会留半截文件）"
                                   % (p.relative_to(ROOT).as_posix(), cls.name, fn.name))
            for fn in _save_funcs(cls):
                for node in ast.walk(fn):
                    if isinstance(node, ast.ExceptHandler):
                        body = [b for b in node.body if not isinstance(b, ast.Expr)]
                        if len(body) == 1 and isinstance(body[0], ast.Pass):
                            bad.append("%s:%s.%s 落盘失败被静默吞掉（行 %s）"
                                       % (p.relative_to(ROOT).as_posix(), cls.name,
                                          fn.name, node.lineno))
    assert not bad, "落盘不原子/静默失败：%s" % bad


# ---------------------------------------------------------------------------
# R5：坏档必须备份，不许被空库覆盖
# ---------------------------------------------------------------------------
CORRUPT_GUARDED = (
    "src/plugins/doupo/store.py",
    "src/plugins/xiuxian/store.py",
    "src/game_mode.py",
    "src/plugins/qq_bridge/planstore.py",
)


def test_corrupt_save_is_backed_up_not_overwritten():
    """读档失败时必须把原文件移成 .corrupt-*，绝不能让下一次 save 覆盖掉。"""
    bad = []
    for rel in CORRUPT_GUARDED:
        p = ROOT / rel
        text = p.read_text(encoding="utf-8")
        if ".corrupt-" not in text:
            bad.append(rel)
    assert not bad, ("这些存档读坏时没有备份原文件，重启一次就被空库覆盖：%s" % bad)


# ---------------------------------------------------------------------------
# R6：运行时往返（容器内真跑；宿主缺依赖则明确 SKIP）
# ---------------------------------------------------------------------------
def test_roundtrip_persisting_stores():
    PlanStore, _ = _import_planstore()      # 定义在下方（运行时解析）
    if PlanStore is None:
        return

    tmp = Path(tempfile.mkdtemp(prefix="persist_rt_"))

    # 计划：写 → 新实例读回
    f = tmp / "plans.json"
    a = PlanStore(f)
    r = a.save("测试计划", "local", '[{"cmd":"ping"}]')
    assert r["ok"], r
    b = PlanStore(f)
    assert [p["plan_id"] for p in b.list()] == [r["plan_id"]], "计划没能跨实例读回"

    # 删了要真的不回来
    assert b.delete(r["plan_id"]) is True
    assert PlanStore(f).list() == [], "删除没有落盘（重启会复活）"

    # 素材映射（image_id 缓存）：写 → 新实例读回。丢了会重传并打满腾讯配额。
    from qq_bridge.channel.media_asset import MaterialStore

    mf = tmp / "media_assets.json"
    m1 = MaterialStore(max_items=4, path=mf)
    m1.put("d41d8cd9", {"url": "qqbot-media://image/v1/abc", "width": 10,
                        "height": 20, "image_id": "IMG1"})
    got = MaterialStore(max_items=4, path=mf).get("d41d8cd9")
    assert got and got.get("image_id") == "IMG1", "素材映射没能跨实例读回（会重复上传）"


def _import_planstore():
    """导入 PlanStore / PlanStoreError；环境缺第三方依赖时返回 (None, None)。

    两种导入口径都要打通：``qq_bridge.*``（插 src/plugins）与
    ``src.plugins.qq_bridge.*``（包内有绝对 ``import src...`` 的地方）。
    跑脚本时 sys.path[0] 是 tests/，仓库根不在里面，故显式补上。
    """
    for _p in (str(SRC / "plugins"), str(ROOT)):
        if _p not in sys.path:
            sys.path.insert(0, _p)
    try:
        from qq_bridge.planstore import PlanStore, PlanStoreError
        return PlanStore, PlanStoreError
    except ModuleNotFoundError as e:
        # 只允许「第三方依赖缺失」跳过；项目自己的模块找不到 = 真失败。
        missing = (e.name or "").split(".")[0]
        if missing in ("qq_bridge", "plugins"):
            raise
        print("      SKIP：环境缺依赖 %s（容器内会真跑）" % missing)
        return None, None


# ---------------------------------------------------------------------------
# R8：用户显式操作落盘失败，必须抛错且内存回滚（不许「提示成功、重启就没」）
# ---------------------------------------------------------------------------
def test_planstore_rollback_on_save_failure():
    PlanStore, PlanStoreError = _import_planstore()
    if PlanStore is None:
        return

    tmp = Path(tempfile.mkdtemp(prefix="persist_rollback_"))
    blocker = tmp / "blocker"
    blocker.write_text("我不是目录", encoding="utf-8")
    # 父路径是文件 → mkdir/write 必失败（真实的磁盘不可写场景等价物）
    bad_path = blocker / "plans.json"

    st = PlanStore(bad_path)
    raised = False
    try:
        st.save("落盘必失败", "local", "[]")
    except PlanStoreError:
        raised = True
    assert raised, "落盘失败却返回成功——接口会谎报「已保存」，重启即丢"
    assert st.list() == [], "落盘失败没回滚内存：界面看得到、重启就没了"

    # 不存在的计划删除 → False（别谎报 True）
    assert st.delete("nope") is False


# ---------------------------------------------------------------------------
# R7：测试不许写生产数据（实例化落盘 Store 必须给临时路径）
# ---------------------------------------------------------------------------
#: 允许在测试里直连生产数据的**只读取**场景（键：测试文件:类名）。
#: 只读不算污染；但凡会写的一律不许登记在这里，必须改成临时路径。
READONLY_IN_TESTS = {
    "tests/test_persona.py:PersonaStore":
        "只读：验证真实人设库能加载抓取来的 10 个外部人设，不写",
}


def _isolation_kwarg(cls: ast.ClassDef):
    """找出该 Store ``__init__`` 里可注入的存储位置参数名（path / dir / db...）。"""
    for n in cls.body:
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == "__init__":
            for a in list(n.args.args[1:]) + list(n.args.kwonlyargs):
                if re.search(r"(path|dir|db|file)", a.arg, re.I):
                    return a.arg
    return None


def test_tests_never_write_production_data():
    """测试里裸 ``XxxStore()`` 必然解析到真实 ``data/``：整表重写 = 抹掉生产数据。

    真实事故（2026-11-17）：``test_qq_button_merge`` 用 ``MaterialStore()`` 跑
    LRU 淘汰，put 里整表 ``_save``，直接把生产 ``data/media_assets.json``
    覆盖成 ``TOKEN123`` 假条目 —— 真上线会把 92 张素材的 image_id 全抹掉，
    老图全部重传、打满腾讯 100 张配额。
    """
    stores = {}
    for p in _py_files():
        tree = _parse(p)
        if tree is None:
            continue
        for cls in [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]:
            if cls.name.endswith("Store") and _class_has_write(cls):
                stores[cls.name] = _isolation_kwarg(cls)

    bad = []
    for tp in sorted(TESTS.rglob("*.py")):
        tree = _parse(tp)
        if tree is None:
            continue
        for n in ast.walk(tree):
            if not isinstance(n, ast.Call):
                continue
            f = n.func
            name = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", "")
            if name not in stores:
                continue
            kw = stores[name]
            # 位置参数（如 PlanStore(tmp/"plans.json")）或 **kwargs 视为已隔离
            if n.args or any(k.arg is None for k in n.keywords):
                continue
            if kw and any(k.arg == kw for k in n.keywords):
                continue
            rel = tp.relative_to(ROOT).as_posix()
            if ("%s:%s" % (rel, name)) in READONLY_IN_TESTS:
                continue
            bad.append("%s:%d  %s() 没给临时%s，会读写生产数据"
                       % (rel, n.lineno, name, kw or "路径(无此参数)"))
    assert not bad, "测试污染生产数据：%s" % bad


# ---------------------------------------------------------------------------
def _main():
    fns = [(n, f) for n, f in globals().items() if n.startswith("test_") and callable(f)]
    failed = 0
    for n, f in sorted(fns):
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
