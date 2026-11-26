# -*- coding: utf-8 -*-
"""批量连炼守门（2026-11-19）—— 原著口径见
docs/炼药加速与快速炼丹_原著考据_2026-11-19.md。

自带 runner，零依赖 pytest：python tests/test_doupo_craft_batch.py
注入反例：临时改坏源码→对应断言必须 FAIL，用于 prove-it。
"""
from __future__ import annotations

import os
import sys
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLUGINS = os.path.join(ROOT, "src", "plugins")
for _p in (ROOT, PLUGINS):
    if _p not in sys.path:
        sys.path.insert(0, _p)

os.environ["DOUPO_CHANCE_RATE"] = "0"


def _stub():
    nb = types.ModuleType("nonebot")
    nb.get_driver = lambda: types.SimpleNamespace(on_startup=lambda f: f)

    def on_regex(*a, **k):
        m = types.SimpleNamespace()
        m.handle = lambda *a, **k: (lambda f: f)
        m.finish = lambda *a, **k: None
        return m
    nb.on_regex = on_regex
    nb.on_command = on_regex
    nb.on_notice = on_regex
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


_stub()

from doupo import config as cfg  # noqa: E402
from doupo import core  # noqa: E402

_FAILS = []
_N = 0


def check(cond, msg):
    global _N
    _N += 1
    if not cond:
        _FAILS.append(msg)


def _p(alch=3, mp=None, wins=0, losses=0):
    p = core.create_player("10001", "测试", "20001")
    p["alch"] = alch
    p["alch_exp"] = 0
    p["last_craft"] = 0
    # 2026-11-24 入道不送火：炼丹前须持有任意火（紫火=atk 型，不污染 alchemy 加成）
    p["flames"]["紫火"] = 1
    p["wins"], p["losses"] = wins, losses
    if mp is not None:
        p["mp"] = mp
    return p


def _stock(p, drug, n):
    for mat, qty in cfg.RECIPE_BY_NAME[drug]["need"].items():
        core.add_item(p, mat, qty * n)


# --- 1. 批量=连续开炉 N 炉，各判成败、出一丹/炉 -------------------------------
def test_batch_crafts_n_and_counts_success():
    p = _p(alch=8)
    _stock(p, "回灵丹", 10)
    before = core.count_item(p, "回灵丹")   # 初始可能有新手礼，按增量断言
    r = core.craft_batch(p, "回灵丹", 5, rng=lambda: 0.0, now_ts=1000)
    check(r["ok"] and r["n_done"] == 5, "批量开炉5炉应全成")
    check(r["n_ok"] == 5 and r["n_fail"] == 0, "全成计数")
    check(core.count_item(p, "回灵丹") - before == 5, "一炉一丹：5炉=5丹（非一炉多丹）")


def test_batch_each_roll_independent():
    p = _p(alch=8)
    _stock(p, "回灵丹", 10)
    before = core.count_item(p, "回灵丹")
    seq = iter([0.0, 0.999, 0.0, 0.999, 0.0])
    r = core.craft_batch(p, "回灵丹", 5, rng=lambda: next(seq), now_ts=1000)
    check(r["n_ok"] == 3 and r["n_fail"] == 2, "逐炉独立判定成败")
    check(core.count_item(p, "回灵丹") - before == 3, "成功炉数=丹数")
    check(core.count_item(p, "药渣") == 2 * cfg.CRAFT_FAIL_DROSS, "失败炉给药渣")


# --- 2. 斗气消耗（原著第6章「极其消耗斗气」）---------------------------------
def test_batch_consumes_mp_per_furnace():
    p = _p(alch=8)
    _stock(p, "回灵丹", 10)
    before = int(p["mp"])
    r = core.craft_batch(p, "回灵丹", 3, rng=lambda: 0.0, now_ts=1000)
    check(r["mp_used"] > 0 and p["mp"] < before, "每炉扣斗气")
    check(r["mp_left"] == p["mp"], "回包 mp_left 与存档一致")


def test_batch_stops_when_mp_exhausted():
    p = _p(alch=8, mp=1)   # 低于单炉消耗
    _stock(p, "回灵丹", 10)
    before = core.count_item(p, "回灵丹")
    r = core.craft_batch(p, "回灵丹", 5, rng=lambda: 0.0, now_ts=1000)
    check(not r["ok"] and r["reason"] == "mp", "斗气不足应拒绝且 reason=mp")
    check(core.count_item(p, "回灵丹") == before, "斗气不足不出丹")


# --- 3. 药材按实际库存收炉（非整批拒绝）-------------------------------------
def test_batch_limited_by_materials():
    p = _p(alch=8)
    _stock(p, "回灵丹", 2)   # 只够 2 炉
    r = core.craft_batch(p, "回灵丹", 5, rng=lambda: 0.0, now_ts=1000)
    check(r["ok"] and r["n_done"] == 2, "药材只够2炉→实炼2炉")
    check(r["limited"] == "mat", "标记 mat 限额")


def test_batch_zero_furnace_reports_missing_detail():
    """2026-11-26 黑盒 N2：连炼一炉都没开成（药材不足）时必须带缺料明细，
    口径与单发 craft 逐字一致，否则命令层只能回泛化的「炉中火候难成」。"""
    p = _p(alch=8)   # 一味药材都没有
    r = core.craft_batch(p, "回灵丹", 5, rng=lambda: 0.0, now_ts=1000)
    check(not r.get("ok") and r.get("reason") == "mat", "一炉没开成→reason=mat")
    check(bool(r.get("missing")), "必须带 missing 明细")
    need = cfg.RECIPE_BY_NAME["回灵丹"]["need"]
    for mat in need:
        check(any(mat in m for m in r.get("missing", [])),
              "缺料明细列出 %s" % mat)
    # 与单发逐字同源
    single = core.craft(p, "回灵丹", rng=lambda: 0.0, now_ts=1000)
    check(single.get("missing") == r.get("missing"), "连炼/单发缺料明细逐字一致")


def test_batch_zero_furnace_mp_reason():
    """斗气不足一炉没开成→reason=mp，命令层据此走 craft_mp 而非泛化失败。"""
    p = _p(alch=8, mp=0)
    _stock(p, "回灵丹", 10)
    r = core.craft_batch(p, "回灵丹", 5, rng=lambda: 0.0, now_ts=1000)
    check(not r.get("ok") and r.get("reason") == "mp", "斗气不足→reason=mp")


# --- 4. CD：整批只占一次，批内不逐炉卡 CD -----------------------------------
def test_batch_single_cd_and_blocks_immediate_second():
    p = _p(alch=8)
    _stock(p, "回灵丹", 20)
    core.craft_batch(p, "回灵丹", 5, rng=lambda: 0.0, now_ts=1000)
    r2 = core.craft_batch(p, "回灵丹", 5, rng=lambda: 0.0, now_ts=1001)
    check(r2.get("reason") == "cd" and r2.get("left") == cfg.CRAFT_CD_SEC - 1,
          "整批结束后仍吃CD，立即再炼被挡")


# --- 5. 低阶经验递减（防刷低级丹冲级）---------------------------------------
def test_low_grade_exp_factor_monotonic():
    f = cfg.low_grade_exp_factor
    check(f(3, 3) == 1.0, "够格不减")
    check(f(3, 4) == 1.0, "越阶不减")
    check(f(3, 2) == f(3, 3) == 1.0, "低1档不减")
    check(f(3, 1) < 1.0, "低2档开始递减")
    check(f(3, 0) < f(3, 1), "低得越多经验越少")
    check(f(3, 0) >= cfg.CRAFT_LOW_EXP_FACTOR[-1] - 1e-9, "有保底不为零")


def test_batch_low_drug_yields_less_exp():
    # 四品炼一品（低3档）vs 四品炼四品，成功炉经验应显著更少
    pa = _p(alch=3)
    _stock(pa, "凝血散", 1)   # 二品，低1档——先找低阶丹
    ra = core.craft_batch(pa, "凝血散", 1, rng=lambda: 0.0, now_ts=1000)
    grade_idx_a = cfg.alch_grade_idx(cfg.RECIPE_BY_NAME["凝血散"]["grade"])
    pb = _p(alch=3)
    _stock(pb, "回灵丹", 1)   # 四品，够格
    rb = core.craft_batch(pb, "回灵丹", 1, rng=lambda: 0.0, now_ts=1000)
    check(ra["exp_factor"] <= 1.0 and rb["exp_factor"] == 1.0, "递减系数口径")
    if grade_idx_a < 2:       # 凝血散若真低2档以上，经验应更少
        check(ra["exp"] < rb["exp"], "低阶丹经验应少于本阶丹")


# --- 6. 七品灵魂境界闸（原著第1103章，防刷满级）-----------------------------
def test_soul_realm_gate_blocks_alch_without_battles():
    need = cfg.SOUL_REALM_BATTLES[6]   # 升 idx6（七品）所需实战
    p = _p(alch=5, wins=0, losses=0)   # 五品( idx5)，经验足够升 idx6
    p["alch_exp"] = cfg.ALCHEMIST[5][1] + 100
    up = core._alch_level_up(p)
    check(up == "" and p["alch"] == 5, "无实战场次→卡在五品，经验保留")
    check(int(p["alch_exp"]) >= cfg.ALCHEMIST[5][1], "被卡时经验不吞")


def test_soul_realm_gate_passes_with_battles():
    p = _p(alch=5, wins=cfg.SOUL_REALM_BATTLES[6], losses=0)
    p["alch_exp"] = cfg.ALCHEMIST[5][1]
    up = core._alch_level_up(p)
    check(p["alch"] == 6 and up == "七品", "实战达标→晋七品")


# --- 7. 单发语义未被污染 -----------------------------------------------------
def test_single_craft_still_one_furnace():
    p = _p(alch=8)
    _stock(p, "回灵丹", 1)
    before = core.count_item(p, "回灵丹")
    r = core.craft(p, "回灵丹", rng=lambda: 0.0, now_ts=1000)
    check(r["ok"] and core.count_item(p, "回灵丹") - before == 1, "单发仍一次一炉")


def _run():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
    print("CRAFT_BATCH_TOTAL=%d PASSED=%d FAILED=%d" % (
        _N, _N - len(_FAILS), len(_FAILS)))
    for f in _FAILS:
        print("FAIL", f)
    return 1 if _FAILS else 0


if __name__ == "__main__":
    sys.exit(_run())
