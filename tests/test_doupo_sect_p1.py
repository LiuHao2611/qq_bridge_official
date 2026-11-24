# -*- coding: utf-8 -*-
"""宗门特色机制 P1 守门（2026-11-24 prove-it）—— 太虚/毒宗/天府。

守玩家可感知行为：
  1. 太虚空间穿梭：自然闪避随等级 +0/3/6/10（走 base 通道，非丹药 +8）；
     lv100 闪避触顶 80 后溢出转命中（22.5→32.5），非太虚逐字节不变；
  2. 毒宗仿毒体：吸血比例 12% × 根基系数，丹药修为越多吸血越低（12→9.8→6.6%），
     真战斗吸血回血且非毒宗无该被动；
  3. 天府全属性：六维 +4% 战力真实增长（10725→11153 / 58245→60573 / 175725→182753），
     非天府/老档逐字节不变，不锁宗。

绝对量断言（不拿被测配置当答案），保证改坏常量/去掉钩子必红。
运行：python tests/test_doupo_sect_p1.py
"""
from __future__ import annotations

import os
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

from doupo import core, config as cfg, sect_mechanics as sm  # noqa: E402


def _mk(lv=60, faction="yunlanzong", **kw):
    p = core.create_player("10001", "宗门P1测试", "20001")
    p["lv"] = lv
    p["faction"] = faction
    p["exp"] = 0
    p["hp"] = 99999
    p["mp"] = 9999
    p.update(kw)
    return p


# ---------------------------------------------------------------------------
# P1-1 太虚古龙 · 空间穿梭
# ---------------------------------------------------------------------------
def test_taixu_evade_pts_absolute():
    """闪避加点按绝对档位值，非太虚/老档 0。"""
    assert sm.taixu_evade_pts(_mk(lv=20, faction="taixugulong")) == 0.0
    assert sm.taixu_evade_pts(_mk(lv=40, faction="taixugulong")) == 3.0
    assert sm.taixu_evade_pts(_mk(lv=70, faction="taixugulong")) == 6.0
    assert sm.taixu_evade_pts(_mk(lv=100, faction="taixugulong")) == 10.0
    assert sm.taixu_evade_pts(_mk(lv=100, faction="yunlanzong")) == 0.0
    assert sm.taixu_evade_pts({"lv": 100}) == 0.0


def test_taixu_evade_goes_natural_base_not_pill_channel():
    """太虚闪避走自然 base：lv60 49→52（+3），非太虚 49 不变；不吃丹药 +8 通道。"""
    assert core.dodge_rate(_mk(lv=60, faction="yunlanzong")) == 49.0
    assert core.dodge_rate(_mk(lv=60, faction="taixugulong")) == 52.0
    # 服风行丹（bonus 闪避 +5）时自然部分仍按 +3 叠加，不被丹药通道吞掉
    plain = core.dodge_rate(_mk(lv=60, faction="yunlanzong", bonus={"闪避": 5}))
    taixu = core.dodge_rate(_mk(lv=60, faction="taixugulong", bonus={"闪避": 5}))
    assert abs(taixu - plain - 3.0) < 0.01, (plain, taixu)


def test_taixu_overflow_hit_at_cap():
    """lv100 自然闪避已顶 80，太虚 +10 全溢出转命中：22.5→32.5；低档无溢出。"""
    p = _mk(lv=100, faction="taixugulong")
    assert core.dodge_rate(p) == 80.0          # 触顶，加点不再涨闪避
    assert core.hit_rate(p) == 32.5            # 但命中 +10 真实生效（非空转）
    assert core.hit_rate(_mk(lv=100, faction="yunlanzong")) == 22.5
    assert sm.taixu_overflow_hit(_mk(lv=60, faction="taixugulong")) == 0.0
    assert sm.taixu_overflow_hit(_mk(lv=100, faction="yunlanzong")) == 0.0


# ---------------------------------------------------------------------------
# P1-2 毒宗 · 仿毒体
# ---------------------------------------------------------------------------
def test_venom_lifesteal_root_absolute():
    """吸血 = 12% × 根基，丹药占比 0/50/100% → 12/9.8/6.6%；非毒宗 0。"""
    for ratio, expect in ((0.0, 0.12), (0.5, 0.0978), (1.0, 0.0660)):
        p = _mk(lv=60, faction="duzong")
        p["pill_exp"] = int(cfg.cum_exp_need(60) * ratio)
        assert abs(sm.venom_lifesteal(p) - expect) < 0.0001, (ratio, sm.venom_lifesteal(p))
    p = _mk(lv=60, faction="yunlanzong")
    p["pill_exp"] = int(cfg.cum_exp_need(60))
    assert sm.venom_lifesteal(p) == 0.0
    assert sm.venom_lifesteal({"lv": 60}) == 0.0


def test_venom_lifesteal_zero_disables():
    """VENOM_LIFESTEAL=0 是合法关闭值（显式判 0，不用 or 兜底）。"""
    orig = cfg.VENOM_LIFESTEAL
    try:
        cfg.VENOM_LIFESTEAL = 0
        p = _mk(lv=60, faction="duzong")
        p["pill_exp"] = 0
        assert sm.venom_lifesteal(p) == 0.0
    finally:
        cfg.VENOM_LIFESTEAL = orig


def test_venom_battle_actually_heals():
    """真战斗：同一个毒宗玩家，开/关吸血被动对照首击 hp_after（开被动回血更多）。

    用 monkeypatch 只切 venom_lifesteal 这一个真源函数，其余（rng/属性/怪）完全相同，
    避免 create_player 按 faction 发不同入门物品引入噪声。
    """
    import random
    mon = {"name": "试炼傀儡", "hp": 500000, "atk": 10000, "def": 100,
           "exp": 1000, "gold": 100, "kind": "elite"}
    orig = sm.venom_lifesteal

    def run():
        p = _mk(faction="duzong", pill_exp=0, learned=["铁山靠"], loadout=["铁山靠"])
        p["hp"] = 50000
        return core.fight(p, dict(mon), rng=random.Random(42).random)

    try:
        on = run()                              # 吸血 0.12 生效
        sm.venom_lifesteal = lambda p, root_fn=None: 0.0   # 关掉被动（对照）
        off = run()
    finally:
        sm.venom_lifesteal = orig
    # 同 rng/同属性，唯一差别是吸血 → 开被动的最终血量更高
    assert on["hp_end"] > off["hp_end"], (on["hp_end"], off["hp_end"])


# ---------------------------------------------------------------------------
# P1-3 天府 · 全属性
# ---------------------------------------------------------------------------
def test_tianfu_power_absolute():
    """天府 +4% 战力真实增长（绝对数字锚，配置改坏必红）。"""
    for lv, plain_expected, tf_expected in ((20, 10725, 11153),
                                            (60, 58245, 60573),
                                            (100, 175725, 182753)):
        plain = core.power(_mk(lv=lv, faction="yunlanzong"))
        tf = core.power(_mk(lv=lv, faction="tianfu"))
        assert plain == plain_expected, (lv, plain)
        assert tf == tf_expected, (lv, tf)
        assert abs((tf - plain) / plain - 0.04) < 0.001


def test_tianfu_not_locked_and_old_save_safe():
    """天府不锁宗（与魂殿对照）；老档无 faction 不触发，六维与非天府一致。"""
    assert sm.is_tianfu_sect(_mk(faction="tianfu"))
    assert not sm.is_tianfu_sect(_mk(faction="yunlanzong"))
    old = {"lv": 100, "bonus": {}, "flames": {}}
    assert sm.tianfu_allstat_pct(old) == 0.0
    assert core.stats(old)["力量"] == core.stats({**old, "faction": "yunlanzong"})["力量"]
    # 反证钩子接在 stats 上
    assert core.stats({**old, "faction": "tianfu"})["力量"] > core.stats(old)["力量"]
    # 天府转宗自由：change_faction 不因宗门被动拒（仅魂殿锁）
    p = _mk(faction="tianfu", gold=9999999)
    r = core.change_faction(p, "yunlanzong")
    assert r["ok"], r


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
