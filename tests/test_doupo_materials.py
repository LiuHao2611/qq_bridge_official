# -*- coding: utf-8 -*-
"""天材地宝行为守门（2026-11-18 prove-it）。

守三件用户感受得到的事：
  1. 怪物等级决定掉落池：低阶怪掉不出高阶天材地宝，高阶怪能掉；
  2. Boss 必掉 tier4 专属天材地宝（万年血灵参/九彩原石）；
  3. 天材地宝服下/炼化后属性真实变化，不是背包摆设。

注入验证（把错误实现放回去，对应用例必须红）：
  * _roll_drops 丢掉 mon 参数、退回全局均匀池 → test_tier_gating / test_boss 红；
  * use_item 不查 MATERIAL_BY_NAME → test_material_* 红。

运行：python tests/test_doupo_materials.py
"""
from __future__ import annotations

import os
import random
import sys
import types

PLUGINS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "src", "plugins")
REPO_ROOT = os.path.dirname(os.path.dirname(PLUGINS_DIR))
for _p in (REPO_ROOT, PLUGINS_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)


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
    adapters = types.ModuleType("nonebot.adapters")
    onebot = types.ModuleType("nonebot.adapters.onebot")
    v11 = types.ModuleType("nonebot.adapters.onebot.v11")
    v11.MessageEvent = object
    v11.MessageSegment = object
    v11.Message = object
    sys.modules["nonebot.adapters"] = adapters
    sys.modules["nonebot.adapters.onebot"] = onebot
    sys.modules["nonebot.adapters.onebot.v11"] = v11


_stub_nonebot()

from doupo import core, config as cfg  # noqa: E402


def _p(lv=1):
    p = core.create_player("10001", "材料测试", "20001")
    p["lv"] = lv
    p["exp"] = 0
    st = core.stats(p)
    p["hp"] = st["体力"]
    p["mp"] = core.mp_max(p)
    return p


def _mon(lv, is_boss=False):
    return {"name": "测试怪", "lv": lv, "is_boss": is_boss, "kind": "魔兽"}


def _always(v):
    """固定 rng：所有千分率判定都命中（v 极小），池选择稳定走第一项。"""
    return lambda: v


def test_tier_gating_by_monster_level():
    """低阶怪(lv10)只可能掉 tier1；高阶怪(lv70)的池含 tier3。"""
    low = [m["name"] for m in cfg.NOVEL_MATERIALS if m["tier"] == 1]
    high = [m["name"] for m in cfg.NOVEL_MATERIALS if m["tier"] == 3]
    # 用固定 rng 让每档都通过千分率，跑多次覆盖池选择
    seen_low, seen_high = set(), set()
    for i in range(60):
        p = _p(70)
        # 关闭炎星石/普通药材的干扰：直接只取材料名
        drops = core._roll_drops(p, _always(0.0), _mon(10))
        for n, _ in drops:
            if n in low or n in high:
                seen_low.add(n)
        drops = core._roll_drops(p, _always(0.0), _mon(70))
        for n, _ in drops:
            if n in high:
                seen_high.add(n)
    assert seen_low <= set(low), "低阶怪掉出了非 tier1 材料：%s" % (seen_low - set(low))
    assert not (seen_low & set(high)), "lv10 怪掉出了 tier3 材料：%s" % (seen_low & set(high))
    assert seen_high, "lv70 高阶怪掉不出任何 tier3 材料——分级失效"
    assert seen_high <= set(high), "高阶池混入了非 tier3：%s" % (seen_high - set(high))


def test_boss_drops_tier4_exclusive():
    """Boss 必掉 tier4（万年血灵参/九彩原石），且低阶非 Boss 永远掉不出 tier4。"""
    tier4 = {m["name"] for m in cfg.NOVEL_MATERIALS if m["tier"] == 4}
    assert tier4 == {"九彩原石", "万年血灵参"}
    boss_got = set()
    for i in range(80):
        p = _p(5)   # 即使玩家/怪 lv 很低，Boss 也掉 tier4
        drops = core._roll_drops(p, random.Random("boss%d" % i).random,
                                 _mon(5, is_boss=True))
        boss_got |= {n for n, _ in drops if n in tier4}
    assert boss_got == tier4, "Boss 未掉全 tier4 专属：%s" % (tier4 - boss_got)

    # 非 Boss 无论多高级、rng 怎样，都不该掉 tier4
    for lv in (10, 50, 90):
        for v in (0.0, 0.3, 0.7):
            p = _p(lv)
            drops = core._roll_drops(p, _always(v), _mon(lv, is_boss=False))
            leaked = [n for n, _ in drops if n in tier4]
            assert not leaked, "非 Boss lv%d 掉出了 tier4：%s" % (lv, leaked)


def test_mp_material_changes_bag_and_mp():
    """炼化回灵赤果：背包 -1、斗气增加（满斗气时先压低再验证）。"""
    p = _p(1)
    core.add_item(p, "回灵赤果", 1)
    p["mp"] = 0
    before = core.count_item(p, "回灵赤果")
    res = core.use_item(p, "回灵赤果")
    assert res["ok"], res
    assert core.count_item(p, "回灵赤果") == before - 1, "材料没被消耗"
    assert p["mp"] > 0, "回灵赤果没有回斗气——是背包摆设"
    assert "炼化" in res["msg"], res["msg"]


def test_hp_material_changes_hp():
    """服用厚土芝：背包 -1、气血增加。"""
    p = _p(1)
    core.add_item(p, "厚土芝", 1)
    p["hp"] = 1
    res = core.use_item(p, "厚土芝")
    assert res["ok"] and p["hp"] > 1, res
    assert core.count_item(p, "厚土芝") == 0


def test_stat_material_grants_permanent_bonus():
    """炼化纳石：永久全属性 +3（读的是 p['bonus']，人物卡/战斗都认）。"""
    p = _p(1)
    core.add_item(p, "纳石", 1)
    before = dict(p.get("bonus") or {})
    res = core.use_item(p, "纳石")
    assert res["ok"], res
    bonus = p.get("bonus") or {}
    for k in ("力量", "敏捷", "斗气", "防御", "体力"):
        assert int(bonus.get(k, 0)) - int(before.get(k, 0)) == 3, "%s 未 +3" % k


def test_every_material_has_effective_use():
    """每件天材地宝都必须有可结算的 effect——防「摆设」回归。"""
    bad = []
    for m in cfg.NOVEL_MATERIALS:
        if m["effect"] not in ("hp", "mp", "exp", "luck", "dodge", "crit", "stat"):
            bad.append("%s effect=%r 不可服用" % (m["name"], m["effect"]))
    assert not bad, "\n".join(bad)
    # 关键回路：每件都能在 use_item 里取到（先扣背包再结算）
    for m in cfg.NOVEL_MATERIALS:
        p = _p(1)
        # 2026-11-23：满血/满气服丹**不再扣物**（core.use_item 前置判定），所以先把
        # 气血斗气打到一半 —— 否则这里测的不是「材料有没有用」，而是「满血给不给吃」。
        p["hp"] = max(1, int(core.stats(p)["体力"]) // 2)
        p["mp"] = 0
        core.add_item(p, m["name"], 1)
        res = core.use_item(p, m["name"])
        assert res.get("ok"), "%s 服下失败：%s" % (m["name"], res)


def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in tests:
        try:
            fn()
            print("PASS %s" % fn.__name__)
        except AssertionError as e:
            failed += 1
            print("FAIL %s: %s" % (fn.__name__, e))
        except Exception as e:  # noqa: BLE001
            failed += 1
            print("ERR  %s: %r" % (fn.__name__, e))
    print("通过 %d，失败 %d" % (len(tests) - failed, failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
