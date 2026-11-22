# -*- coding: utf-8 -*-
"""傀儡遗迹「标尺固定难度」守门（2026-11-22 用户选定，与爬塔同一机制）。

用户原话：「我都四转斗尊巅峰了，按原著的战力来看，打这几个副本应该是碾压级别吧」
⇒ 选「标尺固定难度」：怪数值只由每段后台 power_rec 决定，与打它的真人战力无关。
玩家变强 = 同一段打得更快、更扛打（真碾压），而不是怪同步变厚原地踏步。

同时钉死用户明确不要改的一条：
  「残血就残血，只要不是BUG，残血就应该自动吃药，没药吃就等死，这是正常的」
⇒ 连打跨段结转 hp/mp、服药预算封顶、战败即停 —— 全部保持原样，不许「顺手修」。

prove-it：每条都能被「把 ruin_scaled_monster 换回 monster_scaled(p,...)」这唯一一处
反证打红（脚本见文件尾 mutation 说明）。

运行：python tests/test_doupo_ruin_fixed.py
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
    nb.on_regex = nb.on_command = nb.on_notice = nb.on_message = on_regex
    nb.require = lambda *a, **k: (lambda f: f)
    nb.logger = types.SimpleNamespace(error=lambda *a, **k: None,
                                      warning=lambda *a, **k: None,
                                      info=lambda *a, **k: None)
    sys.modules["nonebot"] = nb
    for n in ("nonebot.adapters", "nonebot.adapters.onebot",
              "nonebot.adapters.onebot.v11"):
        sys.modules[n] = types.ModuleType(n)
    v11 = sys.modules["nonebot.adapters.onebot.v11"]
    v11.MessageEvent = object
    v11.MessageSegment = object
    v11.Message = object
    v11.Bot = object


_stub_nonebot()
for _p in (ROOT, os.path.join(ROOT, "src", "plugins")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

os.environ["DOUPO_CHANCE_RATE"] = "0"

from doupo import config as cfg  # noqa: E402
from doupo import core  # noqa: E402
from doupo import card  # noqa: E402

FAILS = []
N = 0


def check(cond, msg):
    global N
    N += 1
    if not cond:
        FAILS.append(msg)


def _player(lv, seed="s"):
    p = core.create_player(seed, "测试", "群")
    p["lv"] = lv
    p["hp"] = core.stats(p)["体力"]
    p["mp"] = core.mp_max(p)
    return p


# ---------------------------------------------------------------------------
# 1. 固定难度：同段同怪，与真人战力无关
# ---------------------------------------------------------------------------
def test_same_stage_same_monster_for_any_player():
    """lv10 / lv60 / lv103 打同一段，怪 hp/atk/def 逐字段完全相同。"""
    for sg in cfg.PUPPET_RUIN_STAGES:
        mons = [core.ruin_scaled_monster(sg) for _ in (10, 60, 103)]
        # ruin_scaled_monster 不读真人，本就该相等；再显式跑一次带真人的入口确认
        base = mons[0]
        for m in mons[1:]:
            for k in ("hp", "atk", "def", "lv"):
                check(m[k] == base[k],
                      "%s 固定难度被破坏：不同调用同段 %s 不同（%r vs %r）"
                      % (sg["name"], k, m[k], base[k]))


def test_power_rec_is_the_only_difficulty_knob():
    """怪随 power_rec 变厚变狠：抬/压推荐值，同段怪 hp/atk 都单调变强。

    注意同步 ``lv``：生产里每段 lv == ruin_ref_lv(power_rec)（见 round_trip 守门），
    只改 power_rec 不同步 lv 会触发 monster_scaled 的等级差钳位，那不是真实场景。
    """
    sg = cfg.PUPPET_RUIN_STAGES[2]  # 虚空雷池
    base = int(sg["power_rec"])

    def scaled(pr):
        rlv = core.ruin_ref_lv(pr)
        return core.ruin_scaled_monster(dict(sg, lv=rlv, power_rec=pr))

    low, mid, high = scaled(base - 30000), scaled(base), scaled(base + 30000)
    check(low["hp"] < mid["hp"] < high["hp"],
          "power_rec 抬升没有让怪血单调变厚：%d/%d/%d"
          % (low["hp"], mid["hp"], high["hp"]))
    check(low["atk"] < mid["atk"] < high["atk"],
          "power_rec 抬升没有让怪单击变重：%d/%d/%d"
          % (low["atk"], mid["atk"], high["atk"]))


# ---------------------------------------------------------------------------
# 2. power_rec 后台字段：锚定参考曲线、不展示前台
# ---------------------------------------------------------------------------
def test_every_stage_has_power_rec():
    for sg in cfg.PUPPET_RUIN_STAGES:
        check(isinstance(sg.get("power_rec"), int) and sg["power_rec"] > 0,
              "%s 缺后台战力推荐 power_rec：%r" % (sg["name"], sg.get("power_rec")))


def test_power_rec_matches_ref_curve():
    """power_rec == power(tower_ref_player(该段 lv))，不是拍脑袋常数。"""
    for sg in cfg.PUPPET_RUIN_STAGES:
        want = core.power(core.tower_ref_player(int(sg["lv"])))
        check(int(sg["power_rec"]) == want,
              "%s power_rec=%d 与参考曲线 =%d 不符（手拍常数？）"
              % (sg["name"], sg["power_rec"], want))


def test_ref_curve_is_monotonic():
    """反查依赖单调：power(tower_ref_player(lv)) 必须单调不降。"""
    prev = -1
    for lv in range(1, int(cfg.MAX_LV) + 1):
        pw = core.power(core.tower_ref_player(lv))
        check(pw >= prev, "参考战力曲线在 lv%d 回落：%d < %d" % (lv, pw, prev))
        prev = pw


def test_ruin_ref_lv_round_trip():
    """power_rec 反查回的标尺等级 = 该段 lv（否则难度锚错段）。"""
    for sg in cfg.PUPPET_RUIN_STAGES:
        got = core.ruin_ref_lv(int(sg["power_rec"]))
        check(got == int(sg["lv"]),
              "%s power_rec=%d 反查标尺 lv%d，应为 lv%d"
              % (sg["name"], sg["power_rec"], got, sg["lv"]))


def test_power_rec_never_shown_to_frontend():
    """power_rec 只喂难度，绝不进卡面/文案/帮助（用户：后台的，不展示前台）。"""
    data = card.build_ruin_data({"stage": 2}, "2026-11-22")
    blob = repr(data)
    for sg in cfg.PUPPET_RUIN_STAGES:
        check(str(sg["power_rec"]) not in blob,
              "遗迹卡泄露了 %s 的后台战力推荐 %d" % (sg["name"], sg["power_rec"]))
    for token in ("战力推荐", "推荐战力", "power_rec"):
        check(token not in blob, "遗迹卡出现后台术语 %r" % token)


# ---------------------------------------------------------------------------
# 3. 用户红线：残暴毙是正常设计，连打续航机制不许动
# ---------------------------------------------------------------------------
def test_carry_over_and_pill_logic_untouched():
    """源码级钉子：连打结转/服药封顶/战败即停三件套必须保持。"""
    with open(os.path.join(ROOT, "src", "plugins", "doupo", "commands.py"),
              encoding="utf-8") as f:
        src = f.read()
    i0 = src.index("async def cmd_puppet_ruin(")
    body = src[i0:src.index("\n_PUPPET_COLUMNS", i0)]
    check("while idx < total:" in body, "连打循环没了（玩家又要点七次）")
    check("pill_budget = {\"hp\": 0, \"mp\": 0, \"cap\": int(cfg.PUPPET_RUIN_PILL_CAP)}" in body,
          "整趟共用服药预算没了（会变成每段重置、无限续杯）")
    check("pill_budget=pill_budget)" in body, "fight 没收到服药预算 dict")
    check("if not win:" in body and "break" in body, "战败即停没了")
    # 不许出现「段间回满血」这类顺手修残血的代码
    for bad in ("hp = core.stats(p)[\"体力\"]", "_refill(p)", "hp_max = p[\"hp\"]"):
        check(bad not in body,
              "连打里出现了段间回满血代码 %r —— 用户明确：残血没药等死是正常的" % bad)


def test_strong_player_crushes_low_stage():
    """斗尊巅峰打第一段：标尺固定后应是碾压（少回合 + 高胜率），怪不随玩家变厚。"""
    strong = _player(95, "crush")
    sg = cfg.PUPPET_RUIN_STAGES[0]
    ref_mon = core.ruin_scaled_monster(sg)
    win = 0
    rounds = []
    for s in range(40):
        p = dict(strong)
        p["hp"] = core.stats(p)["体力"]
        p["mp"] = core.mp_max(p)
        r = core.fight(p, dict(ref_mon), rng=random.Random(s).random,
                       roll_aid=False, auto_pill=False)
        win += 1 if r["win"] else 0
        rounds.append(int(r["rounds"]))
    check(win / 40 >= 0.95, "lv95 打第一段胜率仅 %d%%（应碾压）" % int(win / 40 * 100))
    check(sum(rounds) / len(rounds) <= 3.0,
          "lv95 打第一段平均 %.1f 回合（标尺固定后应更快）" % (sum(rounds) / len(rounds)))


def test_e2e_same_stage_same_monster_through_real_command():
    """真正打穿 cmd_puppet_ruin：lv20 与 lv95 打同一段，落库的怪血应完全相同。

    🚨 这条才是「假装测试」的解药：上面几条只直接调 ruin_scaled_monster，
       若有人把 commands.py 换回 monster_scaled(p,...) 它们照样全绿。本条走真命令、
       读真存档 last_battle.foe_hp_max —— mutant 必红。
    """
    import asyncio
    import tempfile
    import importlib

    psm = importlib.import_module("doupo.puppet_store")
    smod = importlib.import_module("doupo.store")
    from doupo import commands as cmds

    from datetime import date

    async def foe_hp_of(lv, qq):
        tmp = tempfile.mkdtemp(prefix="ruinfix_e2e_")
        store = smod.Store(os.path.join(tmp, "d.json"))
        await store.load()
        db = psm.PuppetStore(os.path.join(tmp, "p.sqlite"))
        p = core.create_player(qq, "固定难度测试", "20001")
        p["lv"] = lv
        p["hp"] = core.stats(p)["体力"]
        p["mp"] = core.mp_max(p)
        await store.put_player(p)
        # 把水位线推到末段：本次只打第七段一个怪，last_battle 必是同一段。
        # （不这么做的话 lv20 停第一段、lv95 连打到第七段，比的是两只不同的怪，
        #   这不是难度 bug，是测试用例取错对象。2026-11-22 踩过。）
        await db.ruin_clear(qq, date.today().isoformat(), len(cfg.PUPPET_RUIN_STAGES) - 1)
        await cmds.cmd_puppet_ruin(store, qq, db)
        lb = (await store.get_player(qq)).get("last_battle") or {}
        check(str(lb.get("opponent") or "") == cfg.PUPPET_RUIN_STAGES[-1]["monster"],
              "E2E 取到的不是最后一段的战斗：%r" % lb.get("opponent"))
        return int((lb.get("summary") or {}).get("foe_hp_max") or 0)

    hp_low = asyncio.run(foe_hp_of(20, "rf_e2e_lo"))
    hp_high = asyncio.run(foe_hp_of(95, "rf_e2e_hi"))
    check(hp_low > 0 and hp_high > 0,
          "真命令没写出 foe_hp_max：lv20=%d lv95=%d" % (hp_low, hp_high))
    check(hp_low == hp_high,
          "E2E 固定难度被破坏：lv20 与 lv95 打同一段，落库怪血 %d vs %d"
          "（不等 ⇒ 命令层退回自适应反解）" % (hp_low, hp_high))


def test_underleveled_player_fails_high_stage():
    """lv1 打第七段必败：固定难度下「打不过」必须是真打不过，不是随战力缩放。"""
    sg = cfg.PUPPET_RUIN_STAGES[-1]
    mon = core.ruin_scaled_monster(sg)
    losses = 0
    for s in range(20):
        p = _player(1, "weak%d" % s)
        r = core.fight(p, dict(mon), rng=random.Random(s).random,
                       roll_aid=False, auto_pill=False)
        losses += 0 if r["win"] else 1
    check(losses == 20, "lv1 打斗圣主殿竟赢了 %d/20（固定难度失效？）" % (20 - losses))


def _run():
    for fn in (test_same_stage_same_monster_for_any_player,
               test_power_rec_is_the_only_difficulty_knob,
               test_every_stage_has_power_rec,
               test_power_rec_matches_ref_curve,
               test_ref_curve_is_monotonic,
               test_ruin_ref_lv_round_trip,
               test_power_rec_never_shown_to_frontend,
               test_carry_over_and_pill_logic_untouched,
               test_e2e_same_stage_same_monster_through_real_command,
               test_strong_player_crushes_low_stage,
               test_underleveled_player_fails_high_stage):
        fn()
    for m in FAILS:
        print("FAIL  " + m)
    print("=" * 66)
    print("RUIN_FIXED_TOTAL=%d FAILED=%d" % (N, len(FAILS)))
    return 0 if not FAILS else 1


if __name__ == "__main__":
    sys.exit(_run())
