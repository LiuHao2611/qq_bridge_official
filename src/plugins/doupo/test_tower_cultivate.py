# -*- coding: utf-8 -*-
r"""天焚炼气塔 · 修炼守门（2026-11-28，用户「这个天焚炼气塔修炼我怎么看不懂呢」）。

三个被玩家当场问出来的问题，各钉一条：

1. **塔修速率必须跟静修同源分档**。2026-11-28 静修改按境界分档
   （lv29 = 56.4/分），而 ``alchemy_settle`` 还写死 ``CULTIVATE_PER_MIN=6``
   → 黑卡塔修 9/分 = 静修的 **16%**：烧火能、涨火毒，换来比挂机慢六倍。
   ``cultivate_balance`` 文档写的是「core.settle / tower 都从这里取」，代码没取。
2. **结算按「整块」10 分钟**（1 火能 = ``TOWER_MINUTES_PER_ENERGY`` 分钟）。
   不满一块 → 0 修为 0 耗能，回包必须说清**还差几分钟**；旧文案写死
   「不足 1 分钟」，玩家隔 86 秒按一次看到这句，只会更糊涂。
3. **第一次「修炼」只是入塔，不发修为**，回包必须写明，否则玩家以为机制坏了。

运行：
    python src/plugins/doupo/test_tower_cultivate.py
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import time
import types

_HERE = os.path.dirname(os.path.abspath(__file__))
_PLUGINS = os.path.dirname(_HERE)
_ROOT = os.path.dirname(os.path.dirname(_PLUGINS))
for _p in (_ROOT, _PLUGINS):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def _stub_nonebot():
    nb = types.ModuleType("nonebot")
    nb.get_driver = lambda: types.SimpleNamespace(on_startup=lambda f: f)
    nb.get_bot = lambda: types.SimpleNamespace(send=lambda *a, **k: None)

    def on_regex(*a, **k):
        return types.SimpleNamespace(handle=lambda *a, **k: (lambda f: f),
                                     finish=lambda *a, **k: None)
    nb.on_regex = on_regex
    sys.modules["nonebot"] = nb
    for mod in ("nonebot.adapters", "nonebot.adapters.onebot",
                "nonebot.adapters.onebot.v11"):
        sys.modules[mod] = types.ModuleType(mod)
    for attr in ("Message", "MessageEvent", "MessageSegment"):
        setattr(sys.modules["nonebot.adapters.onebot.v11"], attr, object)


_stub_nonebot()

from doupo import commands  # noqa: E402
from doupo import config as cfg  # noqa: E402
from doupo import core  # noqa: E402
from doupo import cultivate_balance as cb  # noqa: E402
from doupo.store import Store  # noqa: E402

PER = int(cfg.TOWER_MINUTES_PER_ENERGY)
T0 = 1800000000.0


def _player(lv=29, tier=0, qq="tower-probe"):
    p = core.create_player(qq, "塔修探针", "g1")
    p["lv"] = lv
    p["bag"] = {}
    return p


def _st(lv=29, tier=0, energy=100, poison=0, in_tower=1, last=T0):
    return {"card_tier": tier, "fire_energy": energy, "fire_poison": poison,
            "in_tower": in_tower, "last_ts": last, "enters": 1}


def _settle(lv=29, minutes=10.0, tier=0, energy=100, poison=0):
    p = _player(lv, tier)
    st = _st(lv, tier, energy, poison, 1, T0)
    return p, st, core.alchemy_settle(p, st, now_ts=T0 + minutes * 60, entering=True)


# ---------------------------------------------------------------------------
# 1. 速率同源：塔修 = 该境界静修速率 × 心火倍率
# ---------------------------------------------------------------------------
def test_tower_gain_equals_realm_rate_times_card_mult():
    for lv in (1, 10, 29, 46, 64, 90):
        _p, _s, r = _settle(lv=lv, minutes=10.0)
        want = int(PER * cb.cultivate_rate(lv) * core.tower_mult(0)
                   * core.flame_exp_mult(_p))
        assert r["gain"] == want, (
            "lv%d 塔修 10 分钟给了 %d，按境界分档应是 %d（塔修没跟静修同源）"
            % (lv, r["gain"], want))


def test_tower_is_never_worse_than_idle():
    """核心红线：塔要烧火能、涨火毒，收益必须高于挂机，否则就是负收益玩法。"""
    for lv in (1, 10, 29, 46, 64, 80, 99):
        idle = cb.cultivate_rate(lv)
        _p, _s, r = _settle(lv=lv, minutes=60.0)
        tower_per_min = r["gain"] / 60.0
        assert tower_per_min > idle, (
            "lv%d 塔修 %.1f/分 不高于静修 %.1f/分 —— 白烧火能还涨火毒"
            % (lv, tower_per_min, idle))


def test_gain_moves_with_level_not_a_flat_constant():
    """旧版写死 6/分：任何等级 10 分钟都是 90。这条就是抓那个常量。"""
    gains = [_settle(lv=lv, minutes=10.0)[2]["gain"] for lv in (10, 29, 46)]
    assert len(set(gains)) == 3, "不同境界塔修收益一样（速率与等级脱钩）：%s" % gains


def test_upgraded_card_multiplies():
    _p, _s, black = _settle(tier=0, minutes=60.0)
    _p2, _s2, purple = _settle(tier=4, minutes=60.0)
    ratio = purple["gain"] / float(black["gain"])
    want = core.tower_mult(4) / core.tower_mult(0)
    assert abs(ratio - want) < 0.01, "紫卡/黑卡收益比 %.2f，心火倍率比 %.2f" % (ratio, want)


# ---------------------------------------------------------------------------
# 2. 整块门槛 + 文案说清还差几分钟
# ---------------------------------------------------------------------------
def test_under_one_block_settles_nothing():
    p = _player(29)
    st = _st(energy=100)
    before_exp = int(p["exp"])
    r = core.alchemy_settle(p, st, now_ts=T0 + (PER - 0.1) * 60, entering=True)
    assert r["mode"] == "wait", "不满一块却结算了：%s" % r["mode"]
    assert r["gain"] == 0 and r["energy_spent"] == 0
    assert int(p["exp"]) == before_exp
    assert int(st["fire_energy"]) == 100, "不满一块就扣了火能"
    assert r["need_min"] == 1, "没告诉玩家还差几分钟（need_min=%r）" % r.get("need_min")


def test_full_block_settles_exactly_one_energy():
    _p, st, r = _settle(minutes=PER + 0.5)
    assert r["mode"] == "settle"
    assert r["minutes"] == PER and r["energy_spent"] == 1
    assert int(st["fire_energy"]) == 99


def test_wait_reply_says_real_gap_not_one_minute():
    src = _await_reply(seconds=86)
    assert "不足 1 分钟" not in src, "86 秒按一次仍说「不足 1 分钟」（门槛是 10 分钟）"
    assert "还差" in src and str(PER) in src, "没写清还差几分钟/整块门槛：\n%s" % src


def test_enter_reply_says_it_pays_nothing():
    src = _await_reply(seconds=None)
    assert "只是入塔" in src and "不发修为" in src, (
        "第一次「修炼」零修为却不说，玩家会以为机制坏了：\n%s" % src)


def _await_reply(seconds):
    """真跑一次 cmd_furnace_enter：seconds=None 表示塔外首按，否则塔中已过 N 秒。"""

    async def run():
        tmp = tempfile.mkdtemp(prefix="doupo_tower_")
        store = Store(os.path.join(tmp, "doupo.json"))
        qq = "100001"
        p = _player(29, qq=qq)
        await store.put_player(p)
        if seconds is None:
            await store.set_tower_state(qq, _st(energy=100, in_tower=0, last=0.0))
            return await commands.cmd_furnace_enter(store, qq)
        await store.set_tower_state(qq, _st(energy=100, in_tower=1,
                                           last=time.time() - seconds))
        return await commands.cmd_furnace_enter(store, qq)

    return asyncio.run(run())


# ---------------------------------------------------------------------------
# 3. 代价：火毒触顶 / 火能耗尽
# ---------------------------------------------------------------------------
def test_poison_cap_kicks_out():
    _p, st, r = _settle(minutes=120.0, poison=0)
    assert r["capped"] and r["mode"] == "capped", "火毒满了还留在塔里"
    assert int(st["fire_poison"]) == int(cfg.TOWER_POISON_CAP)
    assert not int(st["in_tower"]), "火毒触顶没被强制出塔"


def test_no_energy_exits_instead_of_silently_burning_time():
    p = _player(29)
    st = _st(energy=0)
    r = core.alchemy_settle(p, st, now_ts=T0 + 60 * 60, entering=True)
    assert r["mode"] == "no_energy", "火能为 0 却没走 no_energy：%s" % r["mode"]
    assert not int(st["in_tower"]), "火能耗尽还把玩家挂在塔里"


if __name__ == "__main__":
    fns = sorted(k for k in globals() if k.startswith("test_"))
    fails = 0
    for fn in fns:
        try:
            globals()[fn]()
            print("PASS", fn)
        except Exception as exc:  # noqa: BLE001
            fails += 1
            print("FAIL", fn, exc)
    print("== %d passed, %d failed ==" % (len(fns) - fails, fails))
    sys.exit(1 if fails else 0)
