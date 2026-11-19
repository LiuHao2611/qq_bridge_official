# -*- coding: utf-8 -*-
"""气血/斗气自动调息门禁（2026-11-19 用户「给斗气和血量加上自动恢复」）。

口径（写在代码里、这里逐条钉死）：
  * 每 ``REGEN_TICK_SEC``(180s) 结算一次，气血 +4% 上限、斗气 +6% 上限。
  * 离线同样累计；时间基准落 ``regen_clock`` 表，**不进 players.data**。
  * 余数不丢：只推进整 tick 的秒数，零头留着下次算。
  * 满状态照吃时间，不攒 tick（否则满血挂机后掉血要白等一整轮）。
  * 首次见到某玩家只落基准，不补历史（老档上线不该瞬间满血）。

prove-it 反证：
  * 把 consumed 改成 ``elapsed`` 全吃 → test_remainder_kept 红；
  * 把满状态那支删掉 → test_full_state_consumes_elapsed 红；
  * 把 last<=0 那个分支删掉（直接按 now-0 算）→ test_first_sight_sets_baseline 红；
  * 把时间戳塞回 p（JSON）→ test_clock_not_in_player_json 红。

运行：python tests/test_doupo_regen.py
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import time
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


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
    v11 = sys.modules["nonebot.adapters.onebot.v11"]
    v11.MessageEvent = object
    v11.MessageSegment = object
    v11.Message = object


_stub_nonebot()
for _p in (ROOT, os.path.join(ROOT, "src", "plugins")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from doupo import commands, config as cfg, core  # noqa: E402
from doupo.store import Store  # noqa: E402

os.environ["DOUPO_CHANCE_RATE"] = "0"

TICK = float(cfg.REGEN_TICK_SEC)
HP_TICK = float(cfg.REGEN_HP_PCT)
MP_TICK = float(cfg.REGEN_MP_PCT)


def _p(lv: int = 40, hp_frac: float = 0.5, mp_frac: float = 0.5) -> dict:
    p = core.create_player("Q_REGEN", "测试", "群")
    p["lv"] = lv
    p["hp"] = int(core.stats(p)["体力"] * hp_frac)
    p["mp"] = int(core.mp_max(p) * mp_frac)
    return p


def _mk_store(d: str) -> Store:
    return Store(path=os.path.join(d, "doupo.json"))


# ---------------------------------------------------------------------------
# 纯函数：core.regen
# ---------------------------------------------------------------------------
def test_no_tick_before_interval():
    """不满一个间隔：一点不动，且不消耗时间（余数继续攒）。"""
    p = _p()
    hp0, mp0 = p["hp"], p["mp"]
    r = core.regen(p, TICK - 1)
    assert (p["hp"], p["mp"]) == (hp0, mp0), (p["hp"], hp0)
    assert r["ticks"] == 0 and r["consumed"] == 0, r


def test_one_tick_amounts():
    """一个间隔：气血 +4% 上限、斗气 +6% 上限。"""
    p = _p(40, 0.5, 0.5)
    hpmax, mpmax = core.stats(p)["体力"], core.mp_max(p)
    hp0, mp0 = p["hp"], p["mp"]
    r = core.regen(p, TICK)
    assert r["ticks"] == 1
    assert p["hp"] - hp0 == int(hpmax * HP_TICK), (p["hp"] - hp0, hpmax)
    assert p["mp"] - mp0 == int(mpmax * MP_TICK), (p["mp"] - mp0, mpmax)
    assert r["touched"] and r["consumed"] == TICK


def test_remainder_kept():
    """余数不丢：只推进整 tick 的秒数，零头留着凑下一 tick。

    regen 本身无状态，「余数」体现在调用方把 ``consumed`` 加回时间戳。
    这里完整模拟调用方的推进方式，验证零头真的被带到下一轮。
    """
    p = _p()
    last = 0.0
    t = TICK + 20                      # 第一轮：200 秒
    res = core.regen(p, t - last)
    assert res["consumed"] == TICK, res
    last += res["consumed"]            # 只推进 180 秒，20 秒零头留在 last 之后
    # 再过 100 秒（距 last 累计 120 秒）→ 还不够一个 tick
    res = core.regen(p, (t + 100) - last)
    assert res["ticks"] == 0 and res["consumed"] == 0, res
    # 再过 60 秒（距 last 累计 180 秒）→ 刚好凑满第二 tick
    res = core.regen(p, (t + 160) - last)
    assert res["ticks"] == 1, res


def test_multiple_ticks_accumulate():
    """离线一小时：一 tick 的量 × 20（未到上限前线性）。"""
    p = _p(40, 0.2, 0.2)
    hpmax = core.stats(p)["体力"]
    hp0 = p["hp"]
    r = core.regen(p, TICK * 3)
    assert r["ticks"] == 3
    assert p["hp"] - hp0 == 3 * int(hpmax * HP_TICK), (p["hp"] - hp0)


def test_capped_at_max():
    """回满即止，绝不越界（老档 hp 大于上限也先夹回来）。"""
    p = _p(40, 0.9, 0.9)
    p["hp"] = core.stats(p)["体力"] + 999      # 被写坏的存档
    p["mp"] = core.mp_max(p) + 999
    core.regen(p, TICK * 100)
    assert p["hp"] == core.stats(p)["体力"], p["hp"]
    assert p["mp"] == core.mp_max(p), p["mp"]


def test_full_state_consumes_elapsed():
    """满状态：时间照吃（不攒 tick），touched=False。"""
    p = _p(40, 1.0, 1.0)
    r = core.regen(p, 9999)
    assert r["ticks"] == 0 and not r["touched"]
    assert r["consumed"] == 9999, "满血时必须吃掉整段时间，否则掉血后要白等一轮"


def test_regen_never_touches_exp_or_mp_overflow():
    """调息只动 hp/mp：不产生修为、不改其它字段。"""
    p = _p()
    exp0 = p["exp"]
    core.regen(p, TICK)
    assert p["exp"] == exp0
    assert p["mp"] <= core.mp_max(p)


# ---------------------------------------------------------------------------
# 存储层：regen_clock 表
# ---------------------------------------------------------------------------
def test_clock_roundtrip_and_default():
    """没记录返回 0；写入后可读回；UPSERT 覆盖而不是插两行。"""
    with tempfile.TemporaryDirectory(prefix="regen_store_") as d:
        s = _mk_store(d)
        assert asyncio.run(s.regen_last("Q1")) == 0.0
        asyncio.run(s.set_regen_last("Q1", 1000.0))
        assert asyncio.run(s.regen_last("Q1")) == 1000.0
        asyncio.run(s.set_regen_last("Q1", 1180.0))
        assert asyncio.run(s.regen_last("Q1")) == 1180.0
        # 另一玩家互不影响
        assert asyncio.run(s.regen_last("Q2")) == 0.0


def test_clock_not_in_player_json():
    """时间基准必须只在真实列：players.data 里不许出现调息字段。"""
    with tempfile.TemporaryDirectory(prefix="regen_json_") as d:
        s = _mk_store(d)
        asyncio.run(s.load())
        p = _p()
        asyncio.run(s.put_player(p))
        asyncio.run(s.set_regen_last("Q_REGEN", 123.0))
        import json
        import sqlite3
        con = sqlite3.connect(s.db_path)
        try:
            raw = con.execute("SELECT data FROM players WHERE qq=?",
                              ("Q_REGEN",)).fetchone()[0]
        finally:
            con.close()
        blob = json.loads(raw)
        for bad in ("regen_last", "regen_clock", "last_at", "_regen"):
            assert bad not in blob, "调息时间戳被塞进了 players.data：%s" % bad


# ---------------------------------------------------------------------------
# 命令层集成：_player 结算并落盘
# ---------------------------------------------------------------------------
def test_first_sight_sets_baseline_without_healing():
    """老档第一次被看到：只落基准，不按「从 1970 年算起」白送满血。"""
    with tempfile.TemporaryDirectory(prefix="regen_first_") as d:
        s = _mk_store(d)
        asyncio.run(s.load())
        p = _p(40, 0.3, 0.3)
        asyncio.run(s.put_player(p))
        hp0, mp0 = p["hp"], p["mp"]
        got = asyncio.run(commands._player(s, "Q_REGEN"))
        assert (got["hp"], got["mp"]) == (hp0, mp0), (got["hp"], got["mp"])
        assert asyncio.run(s.regen_last("Q_REGEN")) > 0


def test_command_path_heals_and_persists():
    """走命令层取玩家：把时钟拨回 3 个间隔前，取一次就该真恢复并落盘。"""
    with tempfile.TemporaryDirectory(prefix="regen_cmd_") as d:
        s = _mk_store(d)
        asyncio.run(s.load())
        p = _p(40, 0.2, 0.2)
        asyncio.run(s.put_player(p))
        hpmax, mpmax = core.stats(p)["体力"], core.mp_max(p)
        hp0, mp0 = p["hp"], p["mp"]
        # 模拟「3 个间隔前结算过」
        asyncio.run(s.set_regen_last("Q_REGEN", time.time() - TICK * 3))

        got = asyncio.run(commands._player(s, "Q_REGEN"))
        assert got["hp"] > hp0 and got["mp"] > mp0, (got["hp"], got["mp"])
        assert got["hp"] == min(hpmax, hp0 + 3 * int(hpmax * HP_TICK)), got["hp"]
        assert got["mp"] == min(mpmax, mp0 + 3 * int(mpmax * MP_TICK)), got["mp"]

        # 另开实例重读：证明真的落盘，不是活字典的内存假象
        r2 = _mk_store(d)
        asyncio.run(r2.load())
        again = asyncio.run(r2.get_player("Q_REGEN"))
        assert (again["hp"], again["mp"]) == (got["hp"], got["mp"]), \
            ("未落盘", again["hp"], got["hp"])


def test_command_path_sets_baseline_on_new_player():
    """新档（regen_clock 无记录）取一次：写入基准且血量不动。"""
    with tempfile.TemporaryDirectory(prefix="regen_new_") as d:
        s = _mk_store(d)
        asyncio.run(s.load())
        p = _p(10, 0.4, 0.4)
        asyncio.run(s.put_player(p))
        hp0 = p["hp"]
        got = asyncio.run(commands._player(s, "Q_REGEN"))
        assert got["hp"] == hp0 and asyncio.run(s.regen_last("Q_REGEN")) > 0


def test_direct_get_player_heals_like_buttons():
    """按钮/卡片入口直接 ``store.get_player()`` 也必须触发调息（2026-11-19 事故）。

    用户原话「我的斗气和血量没有自动恢复」：当时调息挂在 `commands._player`，
    只有文本口令走那条路；按钮点击、人物卡/战报渲染全是直接 `get_player()`。
    生产库里一上午只写了一次 regen_clock —— 就是被绕过的铁证。
    """
    with tempfile.TemporaryDirectory(prefix="regen_direct_") as d:
        s = _mk_store(d)
        asyncio.run(s.load())
        p = _p(40, 0.2, 0.2)
        asyncio.run(s.put_player(p))
        hp0, mp0 = p["hp"], p["mp"]
        asyncio.run(s.set_regen_last("Q_REGEN", time.time() - TICK * 2))

        # 模拟按钮 dispatch：完全不经过 commands._player
        got = asyncio.run(s.get_player("Q_REGEN"))
        assert got is p and got["hp"] > hp0 and got["mp"] > mp0, \
            ("直接 get_player 没触发调息（按钮玩家永远看不到恢复）", got["hp"], hp0)

        # 新实例重读：恢复真的落盘
        r2 = _mk_store(d)
        asyncio.run(r2.load())
        again = asyncio.run(r2.get_player("Q_REGEN"))
        assert (again["hp"], again["mp"]) == (got["hp"], got["mp"])


def test_panel_shows_regen_rates():
    """人物卡必须写清速率与「离线也累计」（否则玩家只会觉得「血莫名涨了」）。"""
    with tempfile.TemporaryDirectory(prefix="regen_panel_") as d:
        s = _mk_store(d)
        asyncio.run(s.load())
        p = _p(40, 0.5, 0.5)
        asyncio.run(s.put_player(p))
        txt = asyncio.run(commands.cmd_panel(s, "Q_REGEN"))
        assert "调息" in txt, txt[-300:]
        assert "离线" in txt, "面板要说明离线也累计"
        assert "分钟" in txt, txt[-300:]


# ---------------------------------------------------------------------------
def _main() -> int:
    fns = [(n, f) for n, f in globals().items()
           if n.startswith("test_") and callable(f)]
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
            print("ERR ", n, "->", type(e).__name__, str(e)[:200])
    print("\n%d/%d 通过，失败 %d" % (len(fns) - failed, len(fns), failed))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(_main())
