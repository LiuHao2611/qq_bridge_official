# -*- coding: utf-8 -*-
"""后期天焚炼气塔（聚火壶）修炼门禁（2026-11-19 大改）。

用户原话：「完善修炼功能，这个很久没改了，大改一下，你去参考原著的天焚练气塔，
注意是后期的，是萧炎走之后的天焚练气塔」。

原著依据（考据见 docs/战斗与修炼大改_2026-11-19.md）：
  * 第百五十章「一劳永逸」聚火壶 + 注入斗气 → 源源不断产生心火；
  * 第百十三章「情报」幼生体陨落心炎 → 塔「永久开启」；
  * 第百十四章「商议」心火覆盖全塔 →「修炼速度起码能够成倍翻长」；
  * 第百五十七章「奖励」火晶卡黑蓝青赤紫，决定可入层数、换卡缴火能；
  * 第四百七十四章「中级修炼室」心火掺入火焰狂暴之力（火毒），「修炼半天，休息一夜」，
    斗灵可一次修四五天；
  * 第四百七十六章「第二层」火毒累积会出大事。

本作口径（逐条钉死）：
  1. 心火倍率黑 1.5 / 蓝 2.0 / 青 2.8 / 赤 4.0 / 紫 6.0，紫卡 + 斗灵(lv37) 开岩浆世界 ×1.5。
  2. 火能 1 点 = 10 分钟塔修，**只结算整块**，零头留在时间戳里（不白送也不吞）。
  3. 火毒按卡档累积（斗灵减半），触顶 100 强制出塔，离塔每分钟 -2。
  4. 每日免费火能只发一次（同日不发）。
  5. 塔状态全部落 ``alchemy_tower`` 真实列；**players.data 一个字段都不许加**。

反证（改坏必红）：
  * 把 1 点 = 10 分钟改成 1 = 1 → test_energy_block 红；
  * 去掉斗灵减半 → test_dou_ling_halves_poison 红；
  * 去掉触顶强制出塔 → test_poison_cap_forces_exit 红；
  * 把状态塞进 players.data → test_state_not_in_player_json 红。

运行：python tests/test_doupo_furnace.py
"""
from __future__ import annotations

import asyncio
import json
import os
import sqlite3
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

PER = int(cfg.TOWER_MINUTES_PER_ENERGY)


def _p(lv: int = 40) -> dict:
    p = core.create_player("QFURN", "塔修", "群")
    p["lv"] = lv
    p["exp"] = 0
    return p


def _state(**kw) -> dict:
    st = {"card_tier": 0, "fire_energy": 0, "fire_poison": 0, "last_ts": 0.0,
          "free_day": "", "enters": 0, "magma": 0, "in_tower": 0}
    st.update(kw)
    return st


def _mk_store(d: str) -> Store:
    return Store(path=os.path.join(d, "doupo.json"))


# ---------------------------------------------------------------------------
# 倍率 / 层数 / 岩浆
# ---------------------------------------------------------------------------
def test_mult_table():
    """黑蓝青赤紫 = 1.5 / 2.0 / 2.8 / 4.0 / 6.0（原著「成倍翻长」）。"""
    got = [core.tower_mult(t) for t in range(5)]
    assert got == [float(x) for x in cfg.FACTION_CARD_MULT], got
    assert got[0] >= 1.5 and got[-1] >= 6.0


def test_max_floor_table():
    assert [core.tower_max_floor(t) for t in range(5)] == [2, 4, 6, 8, 10]


def test_magma_needs_purple_and_lv37():
    p = _p(36)
    assert core.tower_magma_open(p, 4) is False, "未到斗灵就开岩浆了"
    p["lv"] = 37
    assert core.tower_magma_open(p, 4) is True
    assert core.tower_magma_open(p, 3) is False, "赤卡不该开岩浆"


def test_magma_multiplies_gain():
    p = _p(37)
    st = _state(card_tier=4, fire_energy=10, in_tower=1,
                last_ts=time.time() - PER * 60)
    r = core.alchemy_settle(p, st, now_ts=time.time(), entering=True)
    assert r["magma"] is True
    assert abs(r["mult"] - float(cfg.FACTION_CARD_MULT[4]) * float(cfg.TOWER_MAGMA_MULT)) < 1e-6


# ---------------------------------------------------------------------------
# 火能块 / 修为
# ---------------------------------------------------------------------------
def test_energy_block():
    """1 点火能 = PER 分钟：120 分钟应扣 120/PER 点，零头不扣。"""
    p = _p(40)
    now = 100000.0
    st = _state(card_tier=0, fire_energy=20, in_tower=1,
                last_ts=now - 125 * 60)      # 125 分钟 = 12 整块 + 5 分钟零头
    r = core.alchemy_settle(p, st, now_ts=now, entering=True)
    assert r["energy_spent"] == 120 // PER, r["energy_spent"]
    assert r["minutes"] == (120 // PER) * PER, r["minutes"]
    assert st["fire_energy"] == 20 - 120 // PER
    # 零头 5 分钟留在时间戳里：last_ts 只推进已结算的部分
    assert abs(st["last_ts"] - (now - 5 * 60)) < 1e-6, st["last_ts"]


def test_gain_matches_mult():
    """2026-11-28 反转：塔修速率必须与静修同源分档。

    旧断言钉的是「写死 CULTIVATE_PER_MIN=6/分」那个 bug（lv29 只有静修的 16%），
    用户实测「烧火能涨火毒反而比挂机慢」——现在这条测试反过来钉住修复本身。
    """
    from doupo import cultivate_balance as _cult
    p = _p(40)
    now = 100000.0
    st = _state(card_tier=2, fire_energy=100, in_tower=1, last_ts=now - PER * 60)
    r = core.alchemy_settle(p, st, now_ts=now, entering=True)
    expect = int(PER * _cult.cultivate_rate(int(p["lv"])) * float(cfg.FACTION_CARD_MULT[2])
                 * core.flame_exp_mult(p))
    assert r["gain"] == expect, (r["gain"], expect)
    assert int(p["exp"]) == expect


def test_short_session_is_wait_not_free():
    """不足一块：不结算、不扣火能（否则会白送修为）。"""
    p = _p(40)
    now = 100000.0
    st = _state(card_tier=0, fire_energy=10, in_tower=1, last_ts=now - 60)
    r = core.alchemy_settle(p, st, now_ts=now, entering=True)
    assert r["mode"] == "wait", r
    assert r["gain"] == 0 and r["energy_spent"] == 0
    assert st["fire_energy"] == 10
    assert int(p["exp"]) == 0


def test_no_energy_exits():
    p = _p(40)
    now = 100000.0
    st = _state(card_tier=0, fire_energy=0, in_tower=1, last_ts=now - 600 * 60)
    r = core.alchemy_settle(p, st, now_ts=now, entering=True)
    assert r["mode"] == "no_energy"
    assert st["in_tower"] == 0, "火能耗尽却还留在塔里（会一直空转）"
    assert r["gain"] == 0


# ---------------------------------------------------------------------------
# 火毒
# ---------------------------------------------------------------------------
def test_poison_accumulates():
    p = _p(40)
    now = 100000.0
    st = _state(card_tier=0, fire_energy=100, in_tower=1, last_ts=now - PER * 60)
    r = core.alchemy_settle(p, st, now_ts=now, entering=True)
    assert r["poison_add"] > 0
    assert st["fire_poison"] == r["poison_add"]


def test_dou_ling_halves_poison():
    """原著「斗灵可一次修四五天」→ 火毒增速减半（lv37 分水岭）。"""
    rate_low = core.tower_poison_rate(0, lv=36)
    rate_high = core.tower_poison_rate(0, lv=37)
    assert abs(rate_high - rate_low / 2.0) < 1e-9, (rate_low, rate_high)


def test_poison_cap_forces_exit():
    p = _p(36)          # 非斗灵：火毒快
    now = 100000.0
    st = _state(card_tier=4, fire_energy=1000, in_tower=1, fire_poison=95,
                last_ts=now - PER * 60)
    r = core.alchemy_settle(p, st, now_ts=now, entering=True)
    assert r["capped"] is True
    assert st["fire_poison"] == int(cfg.TOWER_POISON_CAP)
    assert st["in_tower"] == 0, "火毒触顶却没被强制出塔"


def test_cooling_when_outside():
    p = _p(40)
    now = 100000.0
    st = _state(fire_poison=100, in_tower=0, last_ts=now - 10 * 60)
    r = core.alchemy_settle(p, st, now_ts=now, entering=True)
    assert r["cooled"] >= int(10 * float(cfg.TOWER_POISON_COOL_PER_MIN)) - 1
    assert st["fire_poison"] < 100


# ---------------------------------------------------------------------------
# 换卡 / 每日火能
# ---------------------------------------------------------------------------
def test_upgrade_card_cost():
    st = _state(fire_energy=100)
    r = core.tower_upgrade_card(st)
    assert r["ok"] and st["card_tier"] == 1
    assert st["fire_energy"] == 0, st["fire_energy"]
    assert r["cost"] == int(cfg.FACTION_CARD_UPGRADE_COST[0])


def test_upgrade_card_rejects_poor_and_max():
    st = _state(fire_energy=0)
    r = core.tower_upgrade_card(st)
    assert not r["ok"] and r["reason"] == "energy"
    st2 = _state(card_tier=4, fire_energy=10 ** 6)
    r2 = core.tower_upgrade_card(st2)
    assert not r2["ok"] and r2["reason"] == "max"


def test_daily_energy_once():
    st = _state()
    n1 = core.tower_daily_energy(st, "2026-11-19")
    n2 = core.tower_daily_energy(st, "2026-11-19")
    assert n1 == int(cfg.TOWER_FREE_ENERGY) and n2 == 0
    assert st["fire_energy"] == int(cfg.TOWER_FREE_ENERGY)
    n3 = core.tower_daily_energy(st, "2026-11-20")
    assert n3 == int(cfg.TOWER_FREE_ENERGY)


def test_battle_energy_gain():
    st = _state()
    core.tower_add_energy(st, 8)
    assert st["fire_energy"] == 8
    p = _p(40)
    mon = core.monster_scaled(p, {"name": "赤炎狼", "lv": 40, "hard": 1.0})
    r = core.fight(p, mon, rng=lambda: 0.5, roll_aid=False)
    if r["win"]:
        assert r["fire_energy_gain"] >= 1


# ---------------------------------------------------------------------------
# 持久化（最高声明）
# ---------------------------------------------------------------------------
def test_state_roundtrip_in_sqlite():
    with tempfile.TemporaryDirectory(prefix="furnace_") as d:
        s = _mk_store(d)
        asyncio.run(s.load())
        p = _p(40)
        asyncio.run(s.put_player(p))
        st = _state(card_tier=2, fire_energy=77, fire_poison=33,
                    last_ts=1234.0, free_day="2026-11-19", enters=5,
                    magma=1, in_tower=1)
        asyncio.run(s.set_tower_state("QFURN", st))
        got = asyncio.run(s.tower_state("QFURN"))
        for k, v in st.items():
            assert got[k] == v, (k, got[k], v)
        # 新实例重读（不是内存缓存）
        s2 = _mk_store(d)
        asyncio.run(s2.load())
        got2 = asyncio.run(s2.tower_state("QFURN"))
        assert got2["fire_energy"] == 77 and got2["card_tier"] == 2


def test_state_not_in_player_json():
    """塔状态必须落真实列，**绝不能**塞 players.data 的 JSON blob。"""
    with tempfile.TemporaryDirectory(prefix="furnace_json_") as d:
        s = _mk_store(d)
        asyncio.run(s.load())
        p = _p(40)
        asyncio.run(s.put_player(p))
        asyncio.run(s.set_tower_state("QFURN", _state(card_tier=3, fire_energy=9)))
        raw = asyncio.run(s.get_player("QFURN"))
        text = json.dumps(raw, ensure_ascii=False)
        for bad in ("card_tier", "fire_energy", "fire_poison", "alchemy_tower"):
            assert bad not in text, "塔状态漏进了 players.data：%s" % bad
        # 表必须真的存在（库文件按 path 同名推导成 .sqlite）
        con2 = sqlite3.connect(s.db_path)
        tabs = [r[0] for r in con2.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
        con2.close()
        assert "alchemy_tower" in tabs, tabs
        assert "item_lock" in tabs, tabs


def test_item_lock_roundtrip():
    with tempfile.TemporaryDirectory(prefix="furnace_lock_") as d:
        s = _mk_store(d)
        asyncio.run(s.load())
        asyncio.run(s.put_player(_p(40)))
        assert asyncio.run(s.item_locks("QFURN")) == set()
        assert asyncio.run(s.set_item_lock("QFURN", "回春散", True)) is True
        assert asyncio.run(s.item_locks("QFURN")) == {"回春散"}
        # 重复锁：幂等，不报「变了」
        assert asyncio.run(s.set_item_lock("QFURN", "回春散", True)) is False
        assert asyncio.run(s.set_item_lock("QFURN", "回春散", False)) is True
        assert asyncio.run(s.item_locks("QFURN")) == set()


# ---------------------------------------------------------------------------
# 命令层：塔中不许静修 / 入塔文案
# ---------------------------------------------------------------------------
def test_cultivate_refuses_in_tower():
    with tempfile.TemporaryDirectory(prefix="furnace_cmd_") as d:
        s = _mk_store(d)
        asyncio.run(s.load())
        asyncio.run(s.put_player(_p(40)))
        asyncio.run(s.set_tower_state("QFURN", _state(in_tower=1,
                                                      last_ts=time.time())))
        txt = asyncio.run(commands.cmd_cultivate(s, "QFURN"))
        assert "塔" in txt and "白费" in txt, txt
        # 修为不该因为被拒绝而增加
        p = asyncio.run(s.get_player("QFURN"))
        assert int(p.get("exp", 0)) == 0


def test_enter_then_settle_text():
    with tempfile.TemporaryDirectory(prefix="furnace_cmd2_") as d:
        s = _mk_store(d)
        asyncio.run(s.load())
        asyncio.run(s.put_player(_p(40)))
        first = asyncio.run(commands.cmd_furnace_enter(s, "QFURN"))
        assert "已入塔" in first, first
        assert "不发修为" in first, "入塔没说清本次不给修为：%s" % first
        st = asyncio.run(s.tower_state("QFURN"))
        assert st["in_tower"] == 1 and st["fire_energy"] >= int(cfg.TOWER_FREE_ENERGY)
        st["last_ts"] = time.time() - PER * 60 * 2
        asyncio.run(s.set_tower_state("QFURN", st))
        second = asyncio.run(commands.cmd_furnace_enter(s, "QFURN"))
        assert "修为 +" in second, second
        status = asyncio.run(commands.cmd_furnace_status(s, "QFURN"))
        assert "火毒" in status and "火能" in status


def test_lock_command_and_bag_mark():
    with tempfile.TemporaryDirectory(prefix="furnace_lock2_") as d:
        s = _mk_store(d)
        asyncio.run(s.load())
        p = _p(40)
        core.add_item(p, "回春散", 2)
        asyncio.run(s.put_player(p))
        txt = asyncio.run(commands.cmd_lock(s, "QFURN", "回春散", True))
        assert "已锁定" in txt
        bag = asyncio.run(commands.cmd_bag(s, "QFURN"))
        assert "🔒回春散" in bag, bag[:200]
        txt2 = asyncio.run(commands.cmd_lock(s, "QFURN", "回春散", False))
        assert "已解锁" in txt2
        bag2 = asyncio.run(commands.cmd_bag(s, "QFURN"))
        assert "🔒回春散" not in bag2


def test_lock_command_rejects_missing_item():
    with tempfile.TemporaryDirectory(prefix="furnace_lock3_") as d:
        s = _mk_store(d)
        asyncio.run(s.load())
        asyncio.run(s.put_player(_p(40)))
        txt = asyncio.run(commands.cmd_lock(s, "QFURN", "不存在丹", True))
        assert "用法" in txt or "没有" in txt, txt


def test_exit_clears_in_tower_when_under_one_minute():
    """2026-11-26 黑盒 N1a：入塔后不足 1 分钟就出塔（minutes<=0 提前 return），
    也必须真的清 in_tower，否则「修炼」会被永久挡在塔中。"""
    p = _p(40)
    st = _state(in_tower=1, fire_energy=20, last_ts=time.time() - 20)
    r = core.alchemy_settle(p, st, entering=False)
    assert st["in_tower"] == 0, st
    assert r["in_tower"] is False, r


def test_exit_clears_in_tower_when_under_one_energy_block():
    """N1b：塔修 1~9 分钟（不足一个火能整块，blocks<=0 提前 return）出塔也要清。"""
    p = _p(40)
    st = _state(in_tower=1, fire_energy=3, last_ts=time.time() - (PER - 1) * 60)
    r = core.alchemy_settle(p, st, entering=False)
    assert st["in_tower"] == 0, st
    assert r["in_tower"] is False, r


def test_exit_command_does_not_say_rest_when_poison_zero():
    """N1c：火毒已归零时出塔回包不许再说「火毒未散，须歇一夜」。"""
    with tempfile.TemporaryDirectory(prefix="furnace_exit_") as d:
        s = _mk_store(d)
        asyncio.run(s.load())
        asyncio.run(s.put_player(_p(40)))
        asyncio.run(s.set_tower_state("QFURN", _state(in_tower=1, fire_energy=20,
                                                      last_ts=time.time() - 20)))
        txt = asyncio.run(commands.cmd_furnace_exit(s, "QFURN"))
        assert "火毒未散" not in txt, txt
        assert "火毒已清" in txt, txt
        st = asyncio.run(s.tower_state("QFURN"))
        assert st["in_tower"] == 0, st


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
