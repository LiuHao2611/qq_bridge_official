# -*- coding: utf-8 -*-
"""战斗掉落疗伤丹门禁（2026-11-19 用户「任何战斗掉落增加补血补斗气丹药」）。

设计口径：
  * 普通战斗按 ``HEAL_DROP_RATE`` 掉 1 件（气血/斗气二选一）；
  * 精英层 / Boss 层**必掉两件**（气血 + 斗气各一），硬仗本来就该喝药；
  * 掉的丹名必须是 ``RECIPES`` 里真实存在的 ``effect=hp/mp`` 配方；
  * ``mon=None``（旧调用路径）一律不掉 —— 逐字节不变；
  * 疗伤丹靠配方上的 ``pct``（上限百分比）跟随境界，不然高境界捡到等于白捡。

prove-it 反证：
  * 删掉掉落块 → 前三条红；
  * 把疗伤丹放到材料判定之前（插队 rng）→ test_old_path_unchanged 红；
  * 把 use_item 的 pct 去掉 → test_pct_scales_with_realm 红。

运行：python tests/test_doupo_heal_drops.py
"""
from __future__ import annotations

import os
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

from doupo import config as cfg, core  # noqa: E402

os.environ["DOUPO_CHANCE_RATE"] = "0"

HEAL_NAMES = {cfg.HEAL_PILL_HP, cfg.HEAL_PILL_MP}


def _p(lv: int = 40) -> dict:
    p = core.create_player("Q_HEAL", "测试", "群")
    p["lv"] = lv
    p["hp"] = core.stats(p)["体力"]
    p["mp"] = core.mp_max(p)
    return p


def _mon(lv: int = 40, is_boss: bool = False, kind: str = "魔兽") -> dict:
    # hard 只有 monster_scaled 用得到（端到端那条），_roll_drops 只读 lv/is_boss/kind
    return {"name": "测试怪", "lv": lv, "hard": 0.9, "is_boss": is_boss, "kind": kind}


def _always(v: float):
    return lambda: v


def _heal_of(drops) -> list[str]:
    return [n for n, _ in drops if n in HEAL_NAMES]


# ---------------------------------------------------------------------------
def test_heal_pills_are_real_recipes():
    """掉的两个丹名必须在配方表里，且 effect 分别是 hp / mp。"""
    hp = cfg.RECIPE_BY_NAME[cfg.HEAL_PILL_HP]
    mp = cfg.RECIPE_BY_NAME[cfg.HEAL_PILL_MP]
    assert hp["effect"] == "hp", hp
    assert mp["effect"] == "mp", mp
    # 必须带 pct，否则高境界捡到只有固定值、等于白捡
    assert float(hp.get("pct") or 0) > 0, "回血丹缺 pct：%s" % hp
    assert float(mp.get("pct") or 0) > 0, "回气丹缺 pct：%s" % mp


def test_normal_fight_drops_one_heal_pill():
    """普通怪、rng 命中掉落率 → 恰好一件，且只有一件。"""
    p = _p(40)
    drops = core._roll_drops(p, _always(0.0), _mon(40))
    got = _heal_of(drops)
    assert len(got) == 1, "普通战斗应恰好掉 1 件疗伤丹：%s" % got
    assert core.count_item(p, got[0]) == 1, "掉落没进背包：%s" % got[0]


def test_normal_fight_can_skip_when_rng_rolls_high():
    """rng 高于掉落率 → 不掉疗伤丹（不是必掉，普通怪要留随机性）。"""
    p = _p(40)
    drops = core._roll_drops(p, _always(0.99), _mon(40))
    assert _heal_of(drops) == [], "rng=0.99 不该掉疗伤丹：%s" % drops


def test_boss_drops_both_heal_pills():
    """Boss 必掉气血+斗气各一件，且不看 rng。"""
    for v in (0.0, 0.5, 0.99):
        p = _p(40)
        drops = core._roll_drops(p, _always(v), _mon(40, is_boss=True))
        got = set(_heal_of(drops))
        assert got == HEAL_NAMES, "Boss(rng=%.2f) 应掉两件疗伤丹：%s" % (v, got)
        assert core.count_item(p, cfg.HEAL_PILL_HP) == 1
        assert core.count_item(p, cfg.HEAL_PILL_MP) == 1


def test_elite_drops_both_heal_pills():
    """精英层必掉两件 —— 三种 kind 写法都算精英。

    2026-11-20 修：本条旧版把「``塔·elite`` 不该掉」写成了断言，等于把 bug 钉成
    正确（塔/周本传进来的 kind 就是 ``"塔·elite"`` / ``"周本·elite"``，旧判定只认
    「精英」二字 → 精英层的硬仗保底药全程没发）。现在必须同时认 ``elite``。
    """
    for kind in ("塔·elite", "周本·elite", "精英·赤炎狼", "精英"):
        p = _p(40)
        drops = core._roll_drops(p, _always(0.99), _mon(40, kind=kind))
        assert set(_heal_of(drops)) == HEAL_NAMES, (kind, _heal_of(drops))
    # 普通层不能白送（别把「有 elite 字样就送」写成「啥都送」）
    p = _p(40)
    assert _heal_of(core._roll_drops(p, _always(0.99), _mon(40, kind="塔·normal"))) == []


def test_old_path_no_mon_still_unchanged():
    """mon=None 的旧调用路径：一件疗伤丹都不掉（既有调用逐字节不变）。"""
    p = _p(40)
    before = dict(core.bag(p))
    drops = core._roll_drops(p, _always(0.0), None)
    assert _heal_of(drops) == [], drops
    for n in HEAL_NAMES:
        assert core.count_item(p, n) == int(before.get(n, 0)), n


def test_pct_scales_with_realm():
    """疗伤丹按「固定值 / 上限百分比取高」：低境界等于固定值，高境界跟上限。"""
    # 低境界：上限 12% 还不到 200，服下就是 200
    low = _p(1)
    low["hp"] = 100
    core.add_item(low, cfg.HEAL_PILL_HP, 1)
    r = core.use_item(low, cfg.HEAL_PILL_HP)
    assert r["ok"] and low["hp"] == 100 + 200, (r, low["hp"])

    # 高境界：12% 超过 200，按比例给
    high = _p(60)
    mx = core.stats(high)["体力"]
    high["hp"] = 100
    core.add_item(high, cfg.HEAL_PILL_HP, 1)
    core.use_item(high, cfg.HEAL_PILL_HP)
    expect = 100 + max(200, int(mx * float(cfg.RECIPE_BY_NAME[cfg.HEAL_PILL_HP]["pct"])))
    assert high["hp"] == min(mx, expect), (high["hp"], expect, mx)

    # 斗气同理
    pm = _p(60)
    mm = core.mp_max(pm)
    pm["mp"] = 0
    core.add_item(pm, cfg.HEAL_PILL_MP, 1)
    core.use_item(pm, cfg.HEAL_PILL_MP)
    assert pm["mp"] == min(mm, max(300, int(mm * float(
        cfg.RECIPE_BY_NAME[cfg.HEAL_PILL_MP]["pct"])))), (pm["mp"], mm)


def test_impl_text_mentions_pct():
    """卡面承诺必须写出「或上限 N%」，否则高境界服下会觉得被骗（假药事故同源）。"""
    for n in HEAL_NAMES:
        txt = cfg.recipe_impl(cfg.RECIPE_BY_NAME[n])
        assert "%" in txt or "上限" in txt, "%s 的 impl 没写比例：%s" % (n, txt)


def test_fight_end_to_end_drops_heal_pill():
    """真实 fight 走通：Boss 必掉疗伤丹并进背包（不是只测 _roll_drops）。"""
    p = _p(40)
    mon = core.monster_scaled(p, dict(_mon(40, is_boss=True, kind="魂殿")))
    mon["hp"] = 1                     # 一刀斩杀，避免随机败北
    mon["atk"] = 0
    before = core.count_item(p, cfg.HEAL_PILL_HP)
    res = core.fight(p, mon, rng=_always(0.5), roll_aid=False)
    assert res["win"], res
    assert core.count_item(p, cfg.HEAL_PILL_HP) == before + 1, res["drops"]


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
