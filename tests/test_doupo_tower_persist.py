# -*- coding: utf-8 -*-
"""爬塔会话落盘门禁（2026-11-17 「层数被重置」事故后新增）。

用户原话：
> 我发现爬塔层数被你重置掉了，你是不是没把爬塔数据保存好

事故真因：TowerStore 只存进程内存，改平衡时连续 `docker compose up -d --build`
重建容器 3 次 → 正在爬的层数/气血/增益全部清零。本文件把这条钉死：

1. 会话必须写盘，换一个 TowerStore 实例（= 进程重启）能原样读回；
2. 待选战利品连同截止时间一起恢复（重启不该白送/白吃一次三选一）；
3. 真超时也要**补结算**，不能因为挂机久就白爬；
4. 落盘路径必须跟 doupo.json 同目录（容器里即在挂载卷 /app/data/doupo 下），
   不许用插件包内 `__file__` 当默认目录（会被烘进镜像，重建即回滚）。

运行：python tests/test_doupo_tower_persist.py
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DP = os.path.join(ROOT, "src", "plugins", "doupo")


def _stub_nonebot():
    nb = types.ModuleType("nonebot")
    nb.get_driver = lambda: types.SimpleNamespace(on_startup=lambda f: f)

    def on_regex(*a, **k):
        m = types.SimpleNamespace()
        m.handle = lambda *a, **k: (lambda f: f)
        m.finish = lambda *a, **k: None
        return m
    nb.on_regex = on_regex
    sys.modules["nonebot"] = nb
    for n in ("nonebot.adapters", "nonebot.adapters.onebot",
              "nonebot.adapters.onebot.v11"):
        sys.modules[n] = types.ModuleType(n)
    sys.modules["nonebot.adapters.onebot.v11"].MessageEvent = object
    sys.modules["nonebot.adapters.onebot.v11"].MessageSegment = object
    sys.modules["nonebot.adapters.onebot.v11"].Message = object


_stub_nonebot()
for _p in (ROOT, os.path.join(ROOT, "src", "plugins")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from doupo import config as cfg  # noqa: E402
from doupo import core, tower_data as td, tower_session as ts  # noqa: E402


def _path(name="tower_session.json"):
    d = tempfile.mkdtemp(prefix="tower_persist_")
    return os.path.join(d, name)


# ---------------------------------------------------------------------------
def test_session_survives_restart():
    """换进程（新 TowerStore）后，层数/气血/增益/宝箱都要还在。"""
    path = _path()
    a = ts.TowerStore(path=path)
    st = a.begin("10001", seed="persist")
    st["floor"] = 7
    st["temp_hp"] = 4321
    st["temp_mp"] = 88
    st["temp_bonus"] = {"力量": 120}
    st["temp_effect_items"] = [{"kind": "effect", "key": "crit", "v": 0.1, "label": "暴击+10%"}]
    st["chests"] = [{"floor": 5, "type": "item"}]
    a.save("10001", st)

    b = ts.TowerStore(path=path)          # ← 模拟容器重启
    got = b.get("10001")
    assert got is not None, "重启后会话没了（层数会被清零）"
    assert int(got["floor"]) == 7, got.get("floor")
    assert int(got["temp_hp"]) == 4321, got.get("temp_hp")
    assert int(got["temp_mp"]) == 88
    assert got["temp_bonus"] == {"力量": 120}
    assert got["chests"] and int(got["chests"][0]["floor"]) == 5
    assert got["seed"] == "persist", "种子丢了会导致同层结果不可复现"


def test_pending_loot_survives_restart():
    """待选三选一连截止时间一起恢复（别让重启白送一次奖励）。"""
    path = _path()
    a = ts.TowerStore(path=path)
    st = a.begin("10002", seed="loot")
    st["floor"] = 3
    ts.set_loot(st, [{"kind": "hp", "key": "回春散", "v": 0.35, "label": "回春散", "desc": "x"},
                     {"kind": "mp", "key": "回气丹", "v": 1.0, "label": "回气丹", "desc": "x"},
                     {"kind": "attr", "key": "力量", "v": 0.15, "label": "力量+15%", "desc": "x"}],
                 ttl=60.0)
    a.save("10002", st)

    b = ts.TowerStore(path=path)
    got = b.get("10002")
    assert got is not None and got.get("pending_loot"), "重启后待选丢了"
    assert 0 < ts.loot_left(got) <= 60, ts.loot_left(got)
    loot, reason = ts.take_loot(got, 2)
    assert reason == "ok" and loot["key"] == "力量", (loot, reason)


def test_expired_session_is_settled_not_lost():
    """真超时（超过 6 小时）也要能拿回奖励：进入待补结算队列。"""
    path = _path()
    a = ts.TowerStore(path=path)
    st = a.begin("10003", seed="exp")
    st["floor"] = 6
    a.save("10003", st)
    st["ts"] = time.time() - (ts.DEFAULT_TTL + 60)   # 拨到超时（save 后改同一对象）

    assert a.get("10003") is None, "超时应返回 None"
    stale = a.take_expired("10003")
    assert stale is not None and int(stale["floor"]) == 6, "超时会话被直接丢弃＝白爬"

    p = core.create_player("10003", "挂机者", "群")
    before = int(p["gold"])
    res = core.tower_settle(p, stale)
    assert res["ok"] and res["floors"] == 6, res
    assert int(p["gold"]) > before, "超时结算没有发奖"
    assert int(p["tower_best"]) == 6, p.get("tower_best")


def test_ttl_is_long_enough():
    """会话已落盘，TTL 不能再是 15 分钟（吃饭回来就被结算太蠢）。"""
    assert ts.DEFAULT_TTL >= 3600, ts.DEFAULT_TTL
    assert ts.DEFAULT_TTL == 6 * 3600, ts.DEFAULT_TTL


def test_session_path_lives_beside_save_file():
    """落盘路径必须跟 doupo.json 同目录：容器里就是挂载卷，不许落镜像层。"""
    assert os.path.dirname(cfg.TOWER_SESSION_PATH) == os.path.dirname(cfg.DATA_PATH), (
        cfg.TOWER_SESSION_PATH, cfg.DATA_PATH)
    assert cfg.TOWER_SESSION_PATH.endswith(".json")
    # 容器内口径：/app/data/doupo/doupo.json → /app/data/doupo/tower_session.json
    probe = "/app/data/doupo/doupo.json"
    assert os.path.dirname(probe) + "/tower_session.json" == "/app/data/doupo/tower_session.json"


def test_flush_is_atomic_and_returns_readable_json():
    """写盘用临时文件 + replace：中途崩溃不会留下半个 JSON。"""
    import json

    path = _path()
    a = ts.TowerStore(path=path)
    a.begin("10004", seed="atomic")
    assert os.path.exists(path), "落盘文件不存在（_flush 没真正落地）"
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)           # 必须是完整 JSON
    assert "10004" in raw
    assert not os.path.exists(path + ".tmp"), "临时文件没被 replace 掉"


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
            # 实现坏掉时（比如文件根本没写出来）不要整份测试崩在 traceback，
            # 逐条报红才能看出是哪条口径被破坏了。
            failed += 1
            print("FAIL", n, "-> %s: %s" % (type(e).__name__, e))
    print("\n%d/%d 通过，失败 %d" % (len(fns) - failed, len(fns), failed))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(_main())
