# -*- coding: utf-8 -*-
"""周副本门禁：7层一命通关 / 周一快照锁定 / 失败全清含武器 / 奖励锁定到通关。

prove-it：把真实犯过的错（失败不清暂存、提前 commit、怪物按当前画像生成、
新入道不给固定档）放回去，这里的断言必须红。

运行：python tests/test_doupo_weekly.py
"""
from __future__ import annotations

import asyncio
import importlib.util
import os
import random
import sys
import tempfile
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
        m.priority = 5
        m.block = True
        return m
    nb.on_regex = on_regex
    sys.modules["nonebot"] = nb
    for n in ("nonebot.adapters", "nonebot.adapters.onebot",
              "nonebot.adapters.onebot.v11"):
        sys.modules[n] = types.ModuleType(n)
    v = sys.modules["nonebot.adapters.onebot.v11"]
    v.MessageEvent = object
    v.MessageSegment = object
    v.Message = object


_stub_nonebot()
for _p in (ROOT, os.path.join(ROOT, "src", "plugins")):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


from doupo import core, weekly_data as wd, weekly_session as ws  # noqa: E402
from doupo.store import Store  # noqa: E402


def _player(lv=50, qq="q"):
    p = core.create_player(qq, "测试", "群")
    p["lv"] = lv
    p["hp"] = core.stats(p)["体力"]
    p["mp"] = core.mp_max(p)
    return p


# ---------------------------------------------------------------------------
def test_week_structure():
    assert wd.WEEKLY_FLOORS == 7
    assert wd.floor_kind(5) == "elite" and wd.floor_kind(7) == "boss"
    assert all(wd.floor_hard(i) > wd.floor_hard(i + 1) for i in range(1, 7))
    assert wd.floor_hard(1) > wd.floor_hard(7)


def test_week_key_stable():
    a = wd.week_key(1700000000)
    assert len(a) == 8 and a[4:6] == "-W"
    assert a == wd.week_key(1700000000 + 60)      # 同一分钟内同 key


def test_snapshot_isolated_copy():
    p = _player(60)
    sp = wd.snapshot_profile(p)
    sp["lv"] = 999
    assert p["lv"] == 60, "快照必须是深拷贝，不能引用玩家活字典"


def test_rookie_fixed_tier():
    """新入道（无快照）固定新手档，与真实等级无关。"""
    p = _player(100, qq="rookie")                # 哪怕满级小号
    assert "weekly_snapshot" not in p
    snap = core.weekly_snapshot(p, wd.week_key())
    assert int(snap["lv"]) == int(wd.ROOKIE_PROFILE["lv"]), snap
    # 一周内再取，仍锁定同一份（不因玩家变强而刷新）
    p["lv"] = 120
    snap2 = core.weekly_snapshot(p, wd.week_key())
    assert int(snap2["lv"]) == int(wd.ROOKIE_PROFILE["lv"])


def test_snapshot_refresh_next_week():
    """老玩家下周刷新为当前画像（本周锁定不变）。"""
    p = _player(50, qq="old")
    wk = wd.week_key()
    core.weekly_snapshot(p, "2020-W01")          # 先造一个旧快照
    p["lv"] = 66
    snap = core.weekly_snapshot(p, wk)
    assert int(snap["lv"]) == 66, snap


def test_difficulty_locked_to_snapshot():
    """怪物由快照反解，不由玩家当前画像——验证难度真的锁定。"""
    snap_weak = dict(wd.ROOKIE_PROFILE)
    snap_strong = dict(wd.snapshot_profile(_player(100, "strong")))
    p = _player(60)
    st = ws.new_state("x", seed="same")
    a = core.weekly_floor(p, snap_weak, dict(st), rng=lambda: 0.5)
    b = core.weekly_floor(p, snap_strong, dict(st), rng=lambda: 0.5)
    assert a["mon"]["hp"] < b["mon"]["hp"], (a["mon"]["hp"], b["mon"]["hp"])


def test_staged_rewards_locked_until_commit():
    """奖励先入暂存，不写玩家；commit 才一次性到账。"""
    p = _player(50)
    gold0, exp0 = p["gold"], p["exp"]
    st = ws.new_state("x", seed="s")
    rew = {"gold": 500, "exp": 300}
    core.weekly_stage(st, {"reward": rew})
    assert p["gold"] == gold0 and p["exp"] == exp0, "暂存阶段绝不写永久存档"
    core.weekly_stage_loot(st, [{"type": "weapon", "name": "血斧"},
                                {"type": "material", "name": "炎星石", "n": 2}])
    assert "血斧" not in core.owned_weapons(p), "武器通关前不进持有表"
    res = core.weekly_commit(p, st)
    assert p["gold"] == gold0 + 500 and p["exp"] == exp0 + 300
    assert "血斧" in core.owned_weapons(p)
    assert core.count_item(p, "炎星石") >= 2
    assert res["ok"] and st["cleared"]


def test_weekly_session_isolated_and_persist():
    tmp = tempfile.mkdtemp(prefix="wk_store_")
    path = os.path.join(tmp, "weekly_session.json")
    s1 = ws.WeeklyStore(ttl=60, path=path)
    st = s1.begin("a"); st["floor"] = 3; s1.save("a", st)
    # 新实例从盘恢复
    s2 = ws.WeeklyStore(ttl=60, path=path)
    assert s2.get("a")["floor"] == 3, "会话必须落盘可恢复"


class _MemStore:
    """命令层测试替身：只在内存里放玩家，绝不碰生产 SQLite。"""

    def __init__(self, players):
        self.players = players

    async def get_player(self, qq):
        return self.players.get(str(qq))

    async def put_player(self, p):
        self.players[str(p["qq"])] = p


def test_command_failure_clears_staged_including_weapon():
    """失败路径（cmd_weekly）：暂存奖励含神兵全清，玩家永久存档一分不加。"""
    from doupo import commands

    tmp = tempfile.mkdtemp(prefix="wk_fail_")
    saved_path = ws.STORE._path
    ws.STORE._path = os.path.join(tmp, "weekly_session.json")
    ws.STORE._data.clear()
    ws.STORE._expired.clear()
    try:
        # 极弱玩家 vs 极强快照怪 → 必败
        p = _player(1, qq="loser")
        strong = wd.snapshot_profile(_player(100, "strong"))
        p["weekly_snapshot"] = {"week": wd.week_key(), "profile": strong}
        gold0, exp0 = p["gold"], p["exp"]
        stones0 = core.count_item(p, "炎星石")
        st = ws.STORE.begin("loser")
        st["staged"] = {"gold": 5000, "exp": 3000,
                        "items": {"炎星石": 9}, "weapons": ["血斧"]}
        ws.STORE.save("loser", st)
        store = _MemStore({"loser": p})
        msg = asyncio.run(commands.cmd_weekly(store, "loser"))
        assert "力竭倒下" in msg, msg
        assert "全部清空" in msg
        assert p["gold"] == gold0, "失败绝不能发金币"
        assert p["exp"] == exp0, "失败绝不能发修为"
        assert "血斧" not in core.owned_weapons(p), "失败绝不能入神兵"
        assert core.count_item(p, "炎星石") == stones0, "失败绝不能入材料"
        assert ws.STORE.get("loser") is None, "失败必须清会话，从头再来"
    finally:
        ws.STORE._path = saved_path
        ws.STORE._data.clear()


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
    print("\n%d/%d 通过，失败 %d" % (len(fns) - failed, len(fns), failed))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(_main())
