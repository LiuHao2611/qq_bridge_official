# -*- coding: utf-8 -*-
"""爬塔「每层难度固定」门禁（2026-11-19 用户硬要求）。

用户原话：「爬塔每层难度固定，不按玩家战力来」。

改造前：``tower_floor`` 把**真实玩家**喂给 ``monster_scaled`` 反解，玩家一升级，
同一层的怪就同步变厚 —— 爬塔等于永远原地跑，等级越高越没有「爬高了」的感觉。
改造后：怪的全部数值由 ``tower_data.floor_ref_lv(层号)`` 的「标尺玩家」反解，
词缀也按层号定种子。于是**同一层永远是同一只怪**。

prove-it：下面每一条都能被「把 monster_scaled(p, ...) 换回来」这唯一一处反证打红。
  * 三个等级差 90 级的玩家打同一层，怪 hp/atk/def/name/词缀逐字段相同。
  * 同一层连打两次，词缀与怪完全一致（不含任何会话随机）。
  * 标尺等级单调不降、封顶 103。
  * 变强只体现在「能爬更高」：lv79 明显比 lv23 爬得高。
  * lv1 打第 50 层必败（不是「随战力缩放所以总能打」）。

运行：python tests/test_doupo_tower_fixed.py
"""
from __future__ import annotations

import os
import random
import sys
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

from doupo import core, tower_data as td, tower_session as ts  # noqa: E402

os.environ["DOUPO_CHANCE_RATE"] = "0"   # 关掉机缘：贵人相助会把怪一招秒，掩盖数值对比


def _player(lv: int, seed: str = "s") -> dict:
    p = core.create_player(seed, "测试", "群")
    p["lv"] = lv
    p["hp"] = core.stats(p)["体力"]
    p["mp"] = core.mp_max(p)
    return p


def _floor_mon(floor: int, lv: int, seed: str = "s") -> dict:
    """用玩家 lv 打第 floor 层，返回该层实际生成的那只怪。"""
    p = _player(lv, seed)
    st = ts.new_state(seed, seed=seed + ":t")
    st["floor"] = int(floor) - 1
    r = core.tower_floor(p, st, random.Random(seed + ":r").random)
    return r


# ---------------------------------------------------------------------------
def test_same_floor_same_monster_for_any_player():
    """同层同怪：lv10 / lv50 / lv103 打第 20 层，怪逐字段一致。

    这条是「不按玩家战力来」的核心断言。改造前 lv103 看到的怪血量是 lv10 的
    十几倍，这条必红。
    """
    fields = ("name", "lv", "hp", "atk", "def")
    base = _floor_mon(20, 10, "a")["mon"]
    for lv in (50, 103):
        mon = _floor_mon(20, lv, "a%d" % lv)["mon"]
        assert {k: mon[k] for k in fields} == {k: base[k] for k in fields}, \
            ("lv%d 在第20层看到的怪与 lv10 不同：%s vs %s"
             % (lv, {k: mon[k] for k in fields}, {k: base[k] for k in fields}))


def test_same_floor_same_affixes_and_name():
    """词缀也按层号定：同一层连打两次，Boss 名与词缀完全一致。"""
    a = _floor_mon(10, 40, "x1")
    b = _floor_mon(10, 40, "x2")
    assert a["affixes"] == b["affixes"], (a["affixes"], b["affixes"])
    assert len(a["affixes"]) == 2, "Boss 层应两个词缀：%s" % (a["affixes"],)
    assert a["name"] == b["name"] == td.boss_name(10)


def test_ref_lv_monotonic_and_capped():
    """标尺等级只由层号决定：单调不降、第 1 层为 1、超高层封顶。"""
    assert td.floor_ref_lv(1) == 1
    lvs = [td.floor_ref_lv(n) for n in range(1, 120)]
    assert lvs == sorted(lvs), "标尺等级出现回落"
    assert td.floor_ref_lv(999) == td.REF_LV_CAP == 103
    # 封顶后不再涨（后续难度交给 floor_difficulty 的二次增长）
    assert td.floor_ref_lv(80) == td.floor_ref_lv(200) == 103


def test_monster_numbers_grow_with_floor_only():
    """层数越高怪越强（这是唯一允许影响难度的自变量）。"""
    hp = [_floor_mon(n, 50, "g%d" % n)["mon"]["hp"] for n in (1, 10, 20, 30)]
    assert hp == sorted(hp) and hp[-1] > hp[0] * 3, hp


def _climb(lv: int, seed: str, cap: int = 200) -> int:
    p = _player(lv, seed)
    st = ts.new_state(seed, seed=seed + ":tower")
    rng = random.Random(seed + ":tower2").random
    reached = 0
    while reached < cap:
        st["floor"] = reached
        r = core.tower_floor(p, st, rng)
        if not r["win"]:
            break
        reached += 1
        st["temp_hp"], st["temp_mp"] = r["hp"], r["mp"]
    return reached


def test_stronger_player_climbs_higher():
    """变强的意义是「能爬更高」，不是「同层变难」。"""
    low = sorted(_climb(23, "cmp%d" % i) for i in range(4))
    high = sorted(_climb(79, "cmp%d" % i) for i in range(4))
    assert sum(high) / 4.0 > sum(low) / 4.0 + 10, (low, high)


def test_underleveled_player_fails_high_floor():
    """lv1 打第 50 层必败：固定难度下「打不过」必须是真打不过。

    改造前 monster_scaled 按玩家反解，lv1 打第 50 层照样是「四回合互杀」的
    五五开，这条会红。
    """
    p = _player(1, "under")
    for i in range(5):
        st = ts.new_state("under%d" % i, seed="under:%d" % i)
        st["floor"] = 49
        r = core.tower_floor(p, st, random.Random("under:%d" % i).random)
        assert not r["win"], "lv1 打第 50 层居然赢了（难度仍随玩家缩放？）"


def test_overleveled_player_trivializes_low_floor():
    """lv80 打第 1 层：速杀且几乎不掉血（低层对强者就该是白给）。"""
    p = _player(80, "over")
    st = ts.new_state("over", seed="over:t")
    r = core.tower_floor(p, st, random.Random("over:r").random)
    assert r["win"] and int(r["rounds"]) <= 2, (r["win"], r["rounds"])
    assert int(r["taken"]) <= int(core.stats(p)["体力"]) * 0.05, r["taken"]


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
