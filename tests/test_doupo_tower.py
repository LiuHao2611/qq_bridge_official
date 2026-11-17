# -*- coding: utf-8 -*-
"""爬塔副本门禁：难度曲线 / 装备生效 / 结算 / 无重复领取 / 零回归。

prove-it：把真实犯过的错（连爬必死、无限层无终点、武器不生效、重复领取）
放回去，这里的断言必须红。

运行：python tests/test_doupo_tower.py
"""
from __future__ import annotations

import importlib.util
import os
import random
import sys
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
    ad = types.ModuleType("nonebot.adapters")
    ao = types.ModuleType("nonebot.adapters.onebot")
    v = types.ModuleType("nonebot.adapters.onebot.v11")
    v.MessageEvent = object
    v.MessageSegment = object
    v.Message = object
    sys.modules["nonebot.adapters"] = ad
    sys.modules["nonebot.adapters.onebot"] = ao
    sys.modules["nonebot.adapters.onebot.v11"] = v


_stub_nonebot()
for _p in (ROOT, os.path.join(ROOT, "src", "plugins")):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


from doupo import core, tower_data as td, tower_session as ts  # noqa: E402


def _player(lv=50, weapon=None, seed="s"):
    p = core.create_player(seed, "测试", "群")
    p["lv"] = lv
    if weapon and weapon != "玄重尺":
        core.grant_weapon(p, weapon)
        core.equip_weapon(p, weapon)
    p["hp"] = core.stats(p)["体力"]
    p["mp"] = core.mp_max(p)
    return p


def _climb(lv, seed, weapon=None, cap=200):
    p = _player(lv, weapon, seed)
    st = ts.new_state(seed, seed=seed + ":tower")
    rng = random.Random(seed + ":tower2").random
    reached = 0
    while reached < cap:
        st["floor"] = reached
        r = core.tower_floor(p, st, rng)
        if not r["win"]:
            break
        reached += 1
        st["temp_hp"] = r["hp"]
        st["temp_mp"] = r["mp"]
    return reached, p, st


# ---------------------------------------------------------------------------
def test_floor_kind_and_hard():
    assert td.floor_kind(10) == "boss" and td.floor_kind(5) == "elite"
    assert td.floor_kind(20) == "boss" and td.floor_kind(7) == "normal"
    hards = [td.floor_hard(n) for n in range(1, 201)]
    assert all(td.HARD_MIN <= h <= td.HARD_MAX for h in hards)
    # 难度随层数持续叠加（无限层有终点）
    d5, d20, d50 = (td.floor_difficulty(n)["hp_mult"] for n in (5, 20, 50))
    assert 1.0 < d5 < d20 < d50 and d50 > 3.0, (d5, d20, d50)


def test_tower_is_sustainable_but_finite():
    """连爬可持续（见得到精英/Boss），但强者也有终点（不无限爬）。

    用**中位**判定「见得到第10层」：随机种子下 lv70 偶尔会 9 层止步（Boss 词缀歪），
    拿 max 当门槛会把正常波动判成失败；无限层失控看 max，那才是真的失控。
    """
    reached = sorted(_climb(70, "s%d" % i)[0] for i in range(8))
    med = (reached[3] + reached[4]) / 2.0
    assert med >= 10, "强者中位爬不到第10层（见不到Boss）：%s" % reached
    assert max(reached) < 200, "强者无限爬（难度失控）：%s" % reached


def test_hp_monotonic_across_floors():
    """连爬时气血必须单调不增（用户 2026-11-17：气血怎么没降低）。

    真实错：FLOOR_REST_HEAL=0.35 时，单层伤害只要小于 35% 上限就被回血全部盖掉，
    实测 lv60 连爬 8 层每层都是满血 —— 玩家看到的就像「每层重置状态」，爬塔没有消耗感。
    修法：回血同时受「本层实际损失的一半」约束（FLOOR_REST_DMG_RATIO）。

    口径边界：本用例**不带任何战利品**。三选一里的「回春散」会主动回血、
    「吸血+12%」会在战斗中回血，那两种情况下气血允许上涨（是玩家自己选的续航）。
    """
    for lv in (30, 60, 90):
        p = _player(lv, seed="mono%d" % lv)
        st = ts.new_state("mono%d" % lv, seed="mono:%d" % lv)
        rng = random.Random("mono:%d" % lv).random
        prev = int(core.stats(p)["体力"])
        ups, drops = [], 0
        for i in range(1, 13):
            st["floor"] = i - 1
            r = core.tower_floor(p, st, rng)
            if not r["win"]:
                break
            if int(r["hp"]) > prev:
                ups.append((i, prev, int(r["hp"])))
            if int(r["hp"]) < prev:
                drops += 1
            prev = int(r["hp"])
            st["temp_hp"], st["temp_mp"] = r["hp"], r["mp"]
        assert not ups, "连爬出现掉血后回满（像重置状态）：%s" % ups[:3]
        assert drops >= 1, "lv%d 连爬完全没有掉血，塔不消耗气血" % lv


def test_weapon_changes_panel_and_battle():
    """换武器真实改变面板；闪避流武器在塔里更容易活。"""
    p = _player(50)
    s0 = {k: core.stats(p)[k] for k in ("力量", "敏捷", "斗气", "防御")}
    core.grant_weapon(p, "血斧")
    core.equip_weapon(p, "血斧")
    s1 = {k: core.stats(p)[k] for k in ("力量", "敏捷", "斗气", "防御")}
    assert s1["力量"] > s0["力量"] and s1["敏捷"] < s0["敏捷"], (s0, s1)
    core.equip_weapon(p, "玄重尺")
    s2 = {k: core.stats(p)[k] for k in ("力量", "敏捷", "斗气", "防御")}
    assert s0 == s2, "换回玄重尺未逐字节还原（破坏老档平衡）"


def test_panel_and_card_show_equipped_weapon():
    """图文一致：换了武器，人物文本面板与人物卡都必须显示当前武器。

    真实犯过的错：cmd_panel / card.build_panel_data 各自硬编码 cfg.WEAPON，
    装备血斧后仍印「玄重尺 300/260/150/210」，与 stats() 实际生效值不符。
    """
    from doupo import card, commands

    p = _player(50)
    core.grant_weapon(p, "血斧")
    core.equip_weapon(p, "血斧")
    wv = core.weapon_panel(p)
    assert wv["name"] == "血斧", wv
    base = core.wd.WEAPONS["血斧"]["stats"]
    assert wv["stats"] == {k: core.cfg.weapon_stat(v, int(p["weapon"])) for k, v in base.items()}, wv

    d = card.build_data(p)
    assert d["weapon_name"] == "血斧", d["weapon_name"]
    assert d["weapon_stats"] == wv["stats"], (d["weapon_stats"], wv["stats"])

    # 强化预览也必须跟着当前武器走，否则点强化看到的增幅和实际不符
    pv = core.enhance_preview(p)
    assert pv["cur"] == wv["stats"], (pv["cur"], wv["stats"])

    # 十级觉醒名只属于玄重尺
    p2 = _player(50)
    p2["weapon"] = 9
    assert core.weapon_panel(p2)["name"] == core.cfg.WEAPON["name_max"]
    core.grant_weapon(p2, "血斧")
    core.equip_weapon(p2, "血斧")
    assert core.weapon_panel(p2)["name"] == "血斧"
    # 装备血斧时强化到 9 不能冒「异火恒古尺现世」
    p3 = _player(50)
    p3["weapon"] = 8
    core.grant_weapon(p3, "血斧")
    core.equip_weapon(p3, "血斧")
    core.add_item(p3, "炎星石", 20)
    r = core.enhance(p3)
    assert r["ok"] and r["display"] == 10 and r["name"] == "血斧" and not r["evolved"], r


def test_default_xuanzhong_zero_regression():
    """默认玄重尺 1 级实机面板不变（守 test_core 的同源断言）。"""
    p = core.create_player("1", "t", "g")
    st = core.stats(p)
    assert (st["力量"], st["敏捷"], st["斗气"], st["防御"]) == (575, 480, 315, 342), st


def test_settle_once_and_reward():
    p = _player(50)
    st = ts.new_state("q", seed="x")
    st["floor"] = 12
    gold0 = p["gold"]
    r = core.tower_settle(p, st)
    assert r["ok"] and r["floors"] == 12 and p["gold"] > gold0
    assert p["tower_best"] == 12
    # 重复结算必须拒绝（防反复进出刷奖励）
    r2 = core.tower_settle(p, st)
    assert not r2["ok"] and p["gold"] == gold0 + r["gold"]


def test_tower_does_not_pollute_permanent_hp():
    """塔战斗只在副本上打；玩家永久 hp/wins 不被单层战斗改写。"""
    p = _player(50)
    hp0, wins0 = p["hp"], p["wins"]
    st = ts.new_state("q", seed="z")
    core.tower_floor(p, st, random.Random("z").random)
    assert p["hp"] == hp0 and p["wins"] == wins0, (hp0, p["hp"])


def test_loot_and_chest_rules():
    """三选一不重复；宝箱按层解锁、已拥有剔除、无保底全收集返回空。"""
    choices = td.roll_loot_choices(random.Random(1).random)
    assert len({c["key"] for c in choices}) == 3
    assert "血斧" not in td.weapon_pool_for(1, set())
    assert "血斧" in td.weapon_pool_for(12, set())
    assert td.weapon_pool_for(99, set(td.WEAPON_MIN_FLOOR)) == []
    assert td.chest_rate(10) == 1.0 and td.chest_rate(7) < 1.0


def test_session_isolated_and_ttl():
    s1 = ts.TowerStore(ttl=1)
    a = s1.begin("qa"); a["floor"] = 5; s1.save("qa", a)
    b = s1.begin("qb"); b["floor"] = 9; s1.save("qb", b)
    assert s1.get("qa")["floor"] == 5 and s1.get("qb")["floor"] == 9
    s1.clear("qa")
    assert s1.get("qa") is None and s1.get("qb")["floor"] == 9


def test_fight_tower_mod_default_zero_regression():
    """tower_mod=None 时 fight 行为与改造前一致（暴击/伤害不变）。"""
    p = _player(30)
    mon = {"name": "靶", "lv": 30, "hp": 4000, "atk": 1500, "def": 200,
           "exp": 0, "gold": 0}
    a = core.fight(dict(p), dict(mon), rng=random.Random(7).random)
    b = core.fight(dict(p), dict(mon), rng=random.Random(7).random, tower_mod=None)
    assert a["win"] == b["win"] and a["rounds"] == b["rounds"]
    assert [x["dmg"] for x in a["log"]] == [x["dmg"] for x in b["log"]]


def test_chest_rate_uses_passed_floor_not_state():
    """宝箱必须按「刚打完的那一层」判定，不能跟着已推进的 st['floor'] 漂。

    真实踩过的坑：胜利后 st['floor'] 已是本层，若宝箱再读 st 就按下一层算，
    第 10 层 Boss 的必掉宝箱会变成第 11 层的普通宝箱。
    """
    p = _player(50)
    # rng 固定 0.99：Boss 层（chest_rate=1.0）必开；普通层（<0.99）不开。
    assert core.tower_open_chest(p, {"floor": 9}, rng=lambda: 0.99, floor=10), \
        "第10层Boss必须必开宝箱"
    assert core.tower_open_chest(p, {"floor": 10}, rng=lambda: 0.99, floor=11) == [], \
        "第11层普通层不该必开宝箱"
    # 不传 floor 时才回退读 st（向后兼容）
    assert core.tower_open_chest(p, {"floor": 10}, rng=lambda: 0.99), \
        "省略 floor 时应读 st['floor']"


def test_effect_label_aggregation():
    """塔层状态里的增益必须聚合同名项（连选三次破防不能列三遍）。"""
    from doupo import commands

    items = [{"label": "破防+15%"}, {"label": "连击+15%"},
             {"label": "破防+15%"}, {"label": ""}, {"label": "破防+15%"}]
    assert commands._labels(items) == "破防+15%×3、连击+15%", commands._labels(items)
    assert commands._labels([]) == ""


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
