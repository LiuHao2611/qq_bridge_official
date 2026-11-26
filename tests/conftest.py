# -*- coding: utf-8 -*-
"""pytest 公共 fixture 与同步协程入口。

两件事：
  1. `run(coro)` —— 同步执行协程，供**同步风格**的用例调用 async 业务逻辑
     （test_taskmgr / test_taskmgr_local / test_status_reporting 依赖它）。
     用 `asyncio.run`：每次新建事件循环，用例之间互不污染。
  2. `anyio_backend` fixture —— 异步用例（标 `@pytest.mark.anyio`）用 asyncio 后端。
     用 anyio 而非 pytest-asyncio：anyio 已随依赖装好且自带 pytest 插件，**零新增依赖**。

另：把 qq_bridge 根与 src 加入 sys.path，让 `src.plugins.qq_bridge.*` 可直接导入
（queue / intent / taskmgr 等均不依赖 nonebot，可裸跑单测）。
"""
import asyncio
import os
import tempfile

import pytest

# ---------------------------------------------------------------------------
# 日志隔离（必须在任何测试模块 import src.plugins.qq_bridge 之前生效）
# ---------------------------------------------------------------------------
# config.LOG_DIR 在 **import 时** 就按 QQ_LOG_DIR 定死了，晚一步就来不及换目录。
# conftest 由 pytest 最先加载，所以只能放这里。
#
# 不隔离的后果（2026-11-05 实测）：tests/test_plan_commands.py 直接调 game_tools
# 的 handler，伪造的群消息（group_id=900000001、user_id=900000002）连同真实
# 群号一起写进生产 logs/qq_bridge.log。事后排查时那批假消息和真群消息长得一模
# 一样，差点被判成「群聊总闸被绕过」。
os.environ.setdefault("QQ_LOG_DIR", tempfile.mkdtemp(prefix="qq_bridge_pytest_logs_"))

# 注意：**不要在这里往 sys.path 塞路径**。
# pyproject.toml 已配 pythonpath=["."]，pytest 会自动把 qq_bridge 根加入，
# `src.plugins.qq_bridge.*` 正常可导入。额外插入 ROOT/src 会让同一模块以
# 不同限定名被重复加载 → 出现「两份 Config 类」（setattr 改 A 份、业务读 B 份），
# 热生效类用例集体假失败。需要隔离路径的用例请各自在文件内 insert。


def run(coro):
    """同步跑一个协程并返回结果（测试专用）。"""
    return asyncio.run(coro)


@pytest.fixture
def anyio_backend():
    """固定用 asyncio 作为 anyio 后端。"""
    return "asyncio"


# ---------------------------------------------------------------------------
# 隐藏失败守门（2026-11-26）：让 check() 只记不抛的脚本在 pytest 下也真红
# ---------------------------------------------------------------------------
# 背景：23+ 个脚本式测试文件自带 `check()`，失败只 append 到模块级收集器、不抛
# 异常。单独 `python 文件.py` 时结尾的 `_run()` 会统计并 exit(1)，但 **pytest 只
# 收集 test_* 函数、不调 _run()** → 失败全被吞，`pytest tests/` 假绿。
# 已知因此藏了 12 条失败（craft_batch/puppet_anchor/guard/soul/immersion）。
#
# 修法放在 conftest（一处覆盖全部文件、脚本直跑行为零改变）：每个 test 函数
# 执行后，比对它所在模块的收集器长度，本次新增的失败立刻转成 pytest 失败。
# 两种收集器形状都支持：
#   * A 类 `_FAILS/FAILS/fails/FAILED/_failed`：非布尔列表，元素即失败描述
#   * B 类 `_RES/RESULTS`：装 (name, ok, detail) 三元组，筛 ok=False
_FAIL_COLLECTORS = ("_FAILS", "FAILS", "fails", "FAILED", "_failed")
_TUPLE_COLLECTORS = ("_RES", "RESULTS")


def _collector_len(module):
    """返回 {收集器名: 当前条目数}；B 类只数 ok=False 的条目。"""
    out = {}
    for name in _FAIL_COLLECTORS:
        v = getattr(module, name, None)
        if isinstance(v, (list, tuple, set)):
            out[name] = len(v)
    for name in _TUPLE_COLLECTORS:
        v = getattr(module, name, None)
        if isinstance(v, (list, tuple)):
            out[name] = sum(1 for row in v
                            if isinstance(row, (list, tuple)) and len(row) >= 2
                            and not row[1])
    return out


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_call(item):
    """每个用例体跑完后，把 check() 记下的新失败转成 pytest 失败。"""
    module = getattr(item, "module", None)
    before = _collector_len(module) if module is not None else {}
    outcome = yield
    # 用例自身已经失败时不再重复报，避免噪声盖掉真正的异常栈
    if outcome.excinfo is not None:
        return
    after = _collector_len(module) if module is not None else {}
    for name, n_after in after.items():
        delta = n_after - before.get(name, 0)
        if delta <= 0:
            continue
        v = getattr(module, name)
        # 取本次新增的那几条做错误文案
        if name in _TUPLE_COLLECTORS:
            new = [row for row in v
                   if isinstance(row, (list, tuple)) and len(row) >= 2 and not row[1]][-delta:]
        else:
            new = list(v)[-delta:]
        pytest.fail(
            "%s 新增 %d 条隐藏失败（check() 只记不抛，已被守门拦截）：\n  %s"
            % (name, delta, "\n  ".join(str(x)[:200] for x in new)),
            pytrace=False,
        )
