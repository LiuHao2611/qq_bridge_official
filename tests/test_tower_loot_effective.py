# -*- coding: utf-8 -*-
"""爬塔三选一「增益虚加」回归（2026-11-18 用户实测）。

用户原话：最后一次选择「3. 回气丹 — 斗气立刻回满」，但斗气并没有回满。
根因：_apply_loot 对 10 种 loot 全部只写会话态 st，「立刻」文案却承诺即时
生效；且选 loot 分支没有 put_player，末层选完直接结算时 st 被 clear，
增益从未被任何战斗消费 —— 即虚加。

prove-it：守用户感受得到的行为——
  * 选回气丹 → 存档里的斗气立刻满，**另开一个 Store 实例重读仍是满**（真落盘）。
  * 选回春散 → 存档里的气血立刻涨，重读不回退。
  * 末层选完直接结算 → 即时类增益仍保留（不随会话 clear 丢失）。
  * 本轮属性/效果类 → 不污染永久存档，但真实注入下一层战斗（伤害/暴击/效果）。
  * 穷举 10 种 loot → 每一种都有明确的即时 or 本轮归处，未知 kind 直接抛错。

注入验证：把修复放回去（即时类只写 st / 去掉 put_player），前三条必须红。

运行：python tests/test_tower_loot_effective.py
"""
from __future__ import annotations

import asyncio
import copy
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
        return m
    nb.on_regex = on_regex
    sys.modules["nonebot"] = nb
    for n in ("nonebot.adapters", "nonebot.adapters.onebot", "nonebot.adapters.onebot.v11"):
        sys.modules[n] = types.ModuleType(n)
    v11 = sys.modules["nonebot.adapters.onebot.v11"]
    v11.MessageEvent = object
    v11.MessageSegment = object
    v11.Message = object


_stub_nonebot()
for _p in (ROOT, os.path.join(ROOT, "src", "plugins")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from doupo import commands, core, tower_data as td, tower_session as ts  # noqa: E402
from doupo.store import Store  # noqa: E402

os.environ["DOUPO_CHANCE_RATE"] = "0"   # 关掉机缘，战斗对比只看属性


def _mk_store(tmpd: str) -> Store:
    return Store(path=os.path.join(tmpd, "doupo.json"))


async def _player_in(store: Store, qq: str, lv: int = 40, mp_frac: float = 0.25):
    p = core.create_player(qq, "测试", "g")
    p["lv"] = lv
    p["hp"] = core.stats(p)["体力"]
    p["mp"] = max(1, int(core.mp_max(p) * mp_frac))
    await store.put_player(p)
    return p


def _loot(key: str) -> dict:
    return next(x for x in td.LOOT_POOL if x["key"] == key)


async def _pick_via_command(tmpd: str, qq: str, loot: dict) -> dict:
    """走真实 cmd_tower 选 loot 分支；tower 会话换成临时 Store，绝不碰生产。"""
    store = _mk_store(tmpd)
    await store.load()
    await _player_in(store, qq)

    tstore = ts.TowerStore(path=os.path.join(tmpd, "tower.json"))
    old = commands.ts.STORE
    commands.ts.STORE = tstore
    try:
        st = tstore.begin(qq)
        st["floor"] = 1
        st["temp_hp"] = 1           # 塔内已损耗，测回春散基准取会话值
        st["temp_mp"] = 1
        ts.set_loot(st, [loot], 60)
        tstore.save(qq, st)
        await commands.cmd_tower(store, qq, "1")
    finally:
        commands.ts.STORE = old

    # 另开实例重读：证明真的落盘，不是活字典的内存假象
    reread = _mk_store(tmpd)
    await reread.load()
    return await reread.get_player(qq), tstore


# ---------------------------------------------------------------------------
def test_instant_mp_loot_writes_save_and_rereads():
    """用户实测的那一条：选回气丹 → 存档斗气立刻满，重开 Store 仍是满。"""
    with tempfile.TemporaryDirectory(prefix="loot_mp_") as d:
        p, _ = asyncio.run(_pick_via_command(d, "Q_MP", _loot("回气丹")))
        assert p["mp"] == core.mp_max(p), (p["mp"], core.mp_max(p))


def test_instant_hp_loot_writes_save_and_rereads():
    """同类：回春散文案「立刻恢复 35% 气血」→ 存档气血按塔内基准涨，重读不回退。"""
    with tempfile.TemporaryDirectory(prefix="loot_hp_") as d:
        store = _mk_store(d)
        asyncio.run(store.load())
        base = asyncio.run(_player_in(store, "Q_HP"))
        mx = core.stats(base)["体力"]
        p, _ = asyncio.run(_pick_via_command(d, "Q_HP", _loot("回春散")))
        assert p["hp"] == min(mx, 1 + int(mx * 0.35)), (p["hp"], mx)


def test_instant_loot_survives_immediate_settle():
    """用户场景：末层选完回气丹就直接结算——增益不能随会话 clear 消失。"""
    with tempfile.TemporaryDirectory(prefix="loot_settle_") as d:
        store = _mk_store(d)
        asyncio.run(store.load())
        asyncio.run(_player_in(store, "Q_ST"))

        tstore = ts.TowerStore(path=os.path.join(d, "tower.json"))
        old = commands.ts.STORE
        commands.ts.STORE = tstore
        try:
            st = tstore.begin("Q_ST")
            st["floor"] = 3
            st["temp_mp"] = 1
            ts.set_loot(st, [_loot("回气丹")], 60)
            tstore.save("Q_ST", st)
            asyncio.run(commands.cmd_tower(store, "Q_ST", "1"))
            asyncio.run(commands.cmd_tower_settle(store, "Q_ST"))
        finally:
            commands.ts.STORE = old

        reread = _mk_store(d)
        asyncio.run(reread.load())
        p = asyncio.run(reread.get_player("Q_ST"))
        assert p["mp"] == core.mp_max(p), "结算后斗气回退 = 末层增益虚加"
        assert tstore.get("Q_ST") is None, "结算必须清会话"


def test_attr_loot_is_not_virtual_reaches_battle():
    """属性类不污染存档，但下一层战斗真的更强（固定 rng 对比玩家伤害）。"""
    loot = _loot("力量")
    p = core.create_player("Q_ATTR", "t", "g")
    p["lv"] = 40
    p["mp"] = core.mp_max(p)
    st = ts.new_state("Q_ATTR")
    before = int(core.stats(p)["力量"])
    commands._apply_loot(p, st, loot)

    assert "力量" not in (p.get("bonus") or {}), "本轮属性不该写进永久存档"
    # temp_bonus 存的是增量绝对值（mx * v），不是含本体的新值
    assert st["temp_bonus"]["力量"] == int(before * 0.15) > 0, st["temp_bonus"]

    # 把会话增益按 tower_floor 的口径并入战斗副本，对比同 rng 下的玩家输出
    mon = {"name": "靶", "lv": 40, "hp": 200000, "atk": 800, "def": 400,
           "exp": 0, "gold": 0}
    plain = core.fight(dict(p), dict(mon), rng=random.Random(9).random, roll_aid=False)
    boosted = copy.deepcopy(p)
    boosted["bonus"] = dict(boosted.get("bonus") or {})
    boosted["bonus"]["力量"] = st["temp_bonus"]["力量"]
    strong = core.fight(boosted, dict(mon), rng=random.Random(9).random, roll_aid=False)

    def player_dmg(r):
        return sum(int(x["dmg"]) for x in r["log"] if not x.get("taken"))

    assert player_dmg(strong) > player_dmg(plain), \
        (player_dmg(plain), player_dmg(strong))


def test_effect_loot_is_not_virtual_reaches_tower_mod():
    """效果类不污染存档，但真实折算进 fight 的 tower_mod（暴击/连击/吸血/破防）。"""
    p = core.create_player("Q_EFF", "t", "g")
    p["lv"] = 40
    p["mp"] = core.mp_max(p)
    st = ts.new_state("Q_EFF")
    for key in ("crit", "chain", "lifesteal", "pierce"):
        commands._apply_loot(p, st, _loot(key))
    assert not p.get("temp_effect_items"), "本轮效果不该写进永久存档"
    assert len(st["temp_effect_items"]) == 4

    mod = core.tower_mod_from_affixes([], st["temp_effect_items"])
    assert mod["crit_add"] >= 10.0, mod          # 暴击 +10% → crit_add 10 个百分点
    kinds = {e["t"] for e in mod["self_effects"]}
    assert {"chain", "lifesteal", "pierce"} <= kinds, mod


def test_every_loot_kind_has_effective_destination_or_raises():
    """穷举：即时类必须写 p，本轮类必须写会话对应槽；未知 kind 必须抛错而非静默虚加。"""
    for loot in td.LOOT_POOL:
        p = core.create_player("Q_ALL", "t", "g")
        p["lv"] = 40
        p["hp"] = core.stats(p)["体力"]
        p["mp"] = core.mp_max(p)
        st = ts.new_state("Q_ALL")
        st["temp_hp"] = 1
        st["temp_mp"] = 1
        commands._apply_loot(p, st, loot)
        if loot["kind"] == "hp":
            assert p["hp"] > 1 and st["temp_hp"] == p["hp"], loot
        elif loot["kind"] == "mp":
            assert p["mp"] == core.mp_max(p) == st["temp_mp"], loot
        elif loot["kind"] == "attr":
            assert st["temp_bonus"].get(loot["key"], 0) > 0, loot
            assert not p.get("bonus"), loot
        elif loot["kind"] == "effect":
            assert st["temp_effect_items"], loot

    bad = dict(td.LOOT_POOL[0], kind="unknown", key="虚加丹")
    try:
        commands._apply_loot(core.create_player("x", "t", "g"), ts.new_state("x"), bad)
    except ValueError:
        pass
    else:
        raise AssertionError("未知 loot kind 被静默吞掉 = 新的虚加")


# ---------------------------------------------------------------------------
def _main():
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
