# -*- coding: utf-8 -*-
"""限时增益守门（2026-11-24）。

系统：``src/plugins/doupo/temp_buff.py``；设计：``docs/限时增益系统_设计草案_2026-11-24.md``。

守的是玩家真能感受到、且改坏了没人发现的八件事：
  1. 到期归零 —— 药力过了 ``stats()`` 必须逐字节回到服药前（不能留下半口气）；
  2. 同名刷新不叠加 —— 连嗑十枚龙力丹也只有 ×1.45，且时长不变短；
  3. 存档往返 —— 重启后仍生效，且 ``temp_buffs`` 只含 int/float/str（JSON 安全）；
  4. 野外怪随面板变厚（B1 已知代价）—— 锁住现状，将来改 B2 必须来这里改判据；
  5. 固定标尺玩法真赚 —— 塔/遗迹/周本怪不动，而玩家 ``power()`` 上去；
  6. 契约三件套 —— 标签/基础模板齐备，且 impl 里的百分比与分钟数**由配方字段拼出**；
  7. 清扫不误伤 —— 只删过期与坏数据，其余字段逐字节不变；
  8. 展示不刷屏 —— 没药力时战报/面板一行都不许多。

⚠️ 草案 §9 第 4 条标题写「怪不吃 buff」、内容却是「hp/atk 完全相等」，那是 B2 的
判据，与 §4 选定的 B1（接受同步）矛盾。本文件的第 4 条按**实际实现的 B1** 断言：
力量类 buff 让野外怪血变厚、而攻击不变（没 buff 体力/防御就不动攻击反解）。

注入验证（把错误实现放回去必须红）：
  * ``sweep`` 不清过期项 → 第 1 条红；
  * ``add`` 改成 append 不刷新 → 第 2 条红；
  * ``merge`` 改成写 ``p["bonus"]`` 永久 → 第 1、3 条红；
  * ``recipe_impl`` 不拼分钟数 → 第 6 条红；
  * ``battle_line`` 无增益也返回字符串 → 第 8 条红。

运行：python tests/test_doupo_temp_buff.py
"""
from __future__ import annotations

import json
import os
import sys
import time
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

from doupo import core, config as cfg, temp_buff as tb  # noqa: E402

#: 三枚改了口径的丹：它们的 impl 必须带时长，且与配方字段一致。
TB_NAMES = ("蓄力丹", "龙力丹", "速灵风丹")
#: 一瓶固定周号，避免跨周把快照刷新掉导致断言飘。
WEEK = "2026-W48"


def _p(lv=19, qq="10001"):
    p = core.create_player(qq, "限时增益", "20001")
    p["lv"] = lv
    p["hp"] = core.stats(p)["体力"]
    p["mp"] = core.mp_max(p)
    return p


# ---------------------------------------------------------------------------
# 1. 到期归零
# ---------------------------------------------------------------------------
def test_expired_buff_returns_to_baseline():
    p = _p()
    base = core.stats(p)
    # 用真实时钟：16 分钟前吃下 15 分钟的丹 ⇒ until 已在过去
    tb.add(p, "力量", 0.45, 15, "龙力丹", now=time.time() - 16 * 60)
    assert core.stats(p) == base, "过期增益仍在面板里（到点没归零）"
    # 边界：刚过期 1 秒也不算生效
    tb.add(p, "力量", 0.45, 15, "龙力丹", now=time.time() - 15 * 60 - 1)
    assert core.stats(p) == base, "until 边界的 -1 秒仍被算作生效"


# ---------------------------------------------------------------------------
# 2. 同名刷新不叠加
# ---------------------------------------------------------------------------
def test_same_source_refresh_does_not_stack():
    p = _p()
    base = core.stats(p)["力量"]
    tb.add(p, "力量", 0.45, 15, "龙力丹")
    one = core.stats(p)["力量"]
    assert one == int(round(base * 1.45)), (base, one)
    # 连嗑三枚：倍率不变，条数仍为 1
    for _ in range(3):
        tb.add(p, "力量", 0.45, 15, "龙力丹")
    assert len(p["temp_buffs"]) == 1, "同名丹叠加了条数: %r" % (p["temp_buffs"],)
    assert core.stats(p)["力量"] == one, "同名丹叠加了倍率（会囤丹叠满）"
    # 后续一枚时长更短也不许把到期时间提前（连嗑不该变短）
    short = tb.add(p, "力量", 0.45, 1, "龙力丹")
    assert short["until"] >= p["temp_buffs"][0]["until"] - 60, p["temp_buffs"]


def test_different_source_same_key_sums_once():
    """不同来源同键不是刷新而是求和，但只乘一次（防复利）。"""
    p = _p()
    base = core.stats(p)["力量"]
    tb.add(p, "力量", 0.20, 20, "蓄力丹")
    tb.add(p, "力量", 0.45, 15, "龙力丹")
    got = core.stats(p)["力量"]
    assert got == int(round(base * 1.65)), (base, got)


# ---------------------------------------------------------------------------
# 3. 存档往返（模拟重启）
# ---------------------------------------------------------------------------
def test_survives_json_roundtrip():
    p = _p()
    tb.eat(p, cfg.RECIPE_BY_NAME["速灵风丹"])
    blob = json.dumps(p)
    back = json.loads(blob)
    assert core.stats(back) == core.stats(p), "JSON 往返后增益失效（重启就丢）"
    for it in back["temp_buffs"]:
        for k, v in it.items():
            assert isinstance(v, (int, float, str)), (k, type(v), v)
        assert "," not in str(it["k"]), "键名不许是逗号拼接的复合串"
    # 老存档没有 temp_buffs 键：不许炸，也不许凭空造键
    old = _p(qq="10002")
    old.pop("temp_buffs", None)
    tb.sweep(old)
    assert "temp_buffs" not in old, "空列表应当被删掉而不是留个空壳"


# ---------------------------------------------------------------------------
# 4. 野外怪随面板变厚（B1 已知代价，锁住现状）
# ---------------------------------------------------------------------------
def test_wild_monster_tracks_panel_b1_tradeoff():
    """B1：野外遭遇战与九星突破 BOSS 的怪按**真面板**反解 ⇒ buff 同步喂胖。

    这是设计草案 §4 明确接受的代价（与五维丹同口径，impl 里已如实告诉玩家：
    「爬塔·傀儡遗迹·周本真生效；野外随你面板变强」）。
    将来若改 B2（``monster_scaled`` 读不含限时层的面板），本函数应改为
    「hp/atk 完全相等」并改名，不允许两套判据同时存在。
    """
    p = _p()
    mon = core.pick_monster(p)
    before = core.monster_scaled(p, mon)
    tb.eat(p, cfg.RECIPE_BY_NAME["龙力丹"])       # 只加力量
    after = core.monster_scaled(p, mon)
    assert after["hp"] > before["hp"], (
        "怪血没随力量变厚 —— 说明 buff 没进 stats()，那玩家等于白吃 %r"
        % (before["hp"], after["hp"]))
    assert after["atk"] == before["atk"], (
        "只加力量却动了怪攻（攻击反解只吃体力/防御）: %r -> %r"
        % (before["atk"], after["atk"]))
    assert after["name"] == before["name"] and after["lv"] == before["lv"]


# ---------------------------------------------------------------------------
# 5. 固定标尺玩法真赚
# ---------------------------------------------------------------------------
def test_fixed_scale_play_really_profits():
    """塔 / 傀儡遗迹 / 周本 锚的是标尺或周快照 ⇒ 怪一动不动，玩家面板真涨。"""
    p = _p(lv=20)
    week_prof = dict(core.weekly_snapshot(p, WEEK))
    ref = core.tower_ref_player(20)
    mon = core.pick_monster(p)
    tower_before = core.monster_scaled(ref, mon)
    ruin_sg = dict(cfg.PUPPET_RUIN_STAGES[0])
    ruin_before = core.ruin_scaled_monster(ruin_sg)
    power_before = core.power(p)

    tb.eat(p, cfg.RECIPE_BY_NAME["龙力丹"])

    power_after = core.power(p)
    assert power_after > power_before, (power_before, power_after)
    # 标尺与快照都是纸面角色/冻结画像，绝不该读到真人 temp_buffs
    assert core.weekly_snapshot(p, WEEK) == week_prof, "周快照被限时增益污染了"
    tower_after = core.monster_scaled(core.tower_ref_player(20), mon)
    assert (tower_after["hp"], tower_after["atk"]) == \
           (tower_before["hp"], tower_before["atk"]), "塔怪随真人变厚了"
    ruin_after = core.ruin_scaled_monster(ruin_sg)
    assert (ruin_after["hp"], ruin_after["atk"]) == \
           (ruin_before["hp"], ruin_before["atk"]), "遗迹怪随真人变厚了"


# ---------------------------------------------------------------------------
# 6. 契约三件套（标签 / 基础模板 / impl 由配方字段拼出）
# ---------------------------------------------------------------------------
def test_contract_trio_and_duration_match():
    assert cfg.EFFECT_TAG.get("temp_buff"), "temp_buff 没有展示标签（卡面缺角标）"
    assert cfg.IMPL_BY_EFFECT.get("temp_buff"), "temp_buff 没有基础 impl 模板"
    for name in TB_NAMES:
        rec = cfg.RECIPE_BY_NAME[name]
        assert rec.get("effect") == "temp_buff", (name, rec.get("effect"))
        impl = cfg.recipe_impl(rec)
        key = str(rec["buff_key"])
        val = int(rec["value"])
        mins = int(float(rec["buff_min"]))
        assert key in impl, "%s: impl 没写加的是哪一维: %r" % (name, impl)
        assert "+%d%%" % val in impl, "%s: impl 的百分比与 value=%d 不一致: %r" % (
            name, val, impl)
        assert "%d 分钟" % mins in impl, (
            "%s: impl 缺时长（原著「短时间」是承诺），或与 buff_min=%d 不一致: %r"
            % (name, mins, impl))
        pen = rec.get("buff_penalty") or {}
        if pen:
            assert "%d 分钟" % int(pen["min"]) in impl, (
                "%s: 有萎靡惩罚但 impl 没写萎靡时长: %r" % (name, impl))
            assert "%+d%%" % int(round(float(pen["mul"]) * 100)) in impl, (
                "%s: 萎靡幅度没进 impl: %r" % (name, impl))
    # 不写 p["bonus"]：限时层必须是独立字段（写进 bonus 就永久扣不回来）
    p = _p()
    bonus0 = dict(p.get("bonus") or {})
    tb.eat(p, cfg.RECIPE_BY_NAME["龙力丹"])
    core.stats(p)      # 🚨 必须真读一次面板：写坏实现的那一刻就在这里落笔
    assert dict(p.get("bonus") or {}) == bonus0, (
        "限时增益写进了 p['bonus']（会变永久）: %r -> %r"
        % (bonus0, p.get("bonus")))


# ---------------------------------------------------------------------------
# 7. 清扫不误伤
# ---------------------------------------------------------------------------
def test_sweep_only_removes_expired():
    p = _p()
    tb.add(p, "力量", 0.45, 15, "龙力丹")                 # 活的
    tb.add(p, "斗气", 0.35, 20, "速灵风丹")               # 活的
    tb.add(p, "敏捷", 0.10, 10, "过期丹", now=time.time() - 3600)
    p["temp_buffs"].append({"k": "防御", "mul": 0, "start": 0, "until": 1, "src": "坏"})
    p["temp_buffs"].append({"k": "法力", "mul": 0.1, "start": 0,
                            "until": int(time.time()) + 600, "src": "键名不认识"})
    p["temp_buffs"].append({"k": "体力", "mul": 0.1, "start": 0,
                            "until": int(time.time()) + 400 * 86400, "src": "天外"})
    p["temp_buffs"].append("不是 dict")
    other0 = {k: v for k, v in p.items() if k != "temp_buffs"}

    assert tb.sweep(p) is True
    kept = p["temp_buffs"]
    assert sorted(x["src"] for x in kept) == ["速灵风丹", "龙力丹"], kept
    assert {k: v for k, v in p.items() if k != "temp_buffs"} == other0, \
        "清扫动了 temp_buffs 以外的字段"
    # 再扫一次应当无变化（幂等），不能每读一次存档都写一次盘
    assert tb.sweep(p) is False, "sweep 不幂等（会导致每次读档都触发落盘）"


# ---------------------------------------------------------------------------
# 8. 展示不刷屏
# ---------------------------------------------------------------------------
def test_display_silent_when_no_buff():
    p = _p()
    assert tb.battle_line(p) == "", "没药力时战报仍多出一行（会刷屏）"
    assert tb.lines(p) == [], "没药力时人物面板仍多出一行"
    tb.eat(p, cfg.RECIPE_BY_NAME["龙力丹"])
    bl = tb.battle_line(p)
    assert "药力未散" in bl and "×1.45" in bl and "剩" in bl, bl
    ls = tb.lines(p)
    assert len(ls) >= 1 and "龙力丹" in ls[0] and "剩" in ls[0], ls
    # 萎靡期也要如实显示负号，不能让玩家以为丹只留好处
    p2 = _p(qq="10003")
    tb.eat(p2, cfg.RECIPE_BY_NAME["速灵风丹"])
    p2["temp_buffs"] = [x for x in p2["temp_buffs"] if "萎靡" in x["src"]]
    p2["temp_buffs"][0]["start"] = int(time.time()) - 60
    p2["temp_buffs"][0]["until"] = int(time.time()) + 600
    assert "-15%" in tb.lines(p2)[0], tb.lines(p2)


def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in tests:
        try:
            fn()
            print("  PASS %s" % fn.__name__)
        except AssertionError as e:
            failed += 1
            print("  FAIL %s:\n%s" % (fn.__name__, e))
        except Exception as e:  # noqa: BLE001
            failed += 1
            print("  ERROR %s: %s: %s" % (fn.__name__, type(e).__name__, e))
    print("\n%d/%d passed" % (len(tests) - failed, len(tests)))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
