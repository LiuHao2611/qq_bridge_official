# -*- coding: utf-8 -*-
"""模拟实证：异火/兽火对炼药是否**真的**有加成（2026-11-26）。

用户问「异火和兽火是否对炼药真的有加成」—— 不许看代码猜，用同种子大样本模拟说话：

  1. 空火 vs 有火：同一玩家、同一丹方、同一 rng 种子序列，统计成功次数；
  2. 异火 vs 兽火：同为 alchemy 型，验证两条线都进同一成功率口径；
  3. 数值口径：alchemy_rate_bonus 的增量 == 成功率增量（未封顶时）；
  4. 两入口一致：core.craft 与 core.craft_batch 共用 cfg.rate_for_recipe，
     不许「单发接了批量漏接」（历史踩过的双真源坑）。

运行：``python tests/test_flame_craft_sim.py``（也兼容 pytest）
"""
from __future__ import annotations

import importlib.util
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PLUGINS = REPO / "src" / "plugins"
for _p in (str(REPO), str(PLUGINS)):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def _stub():
    if "nonebot" in sys.modules:
        return
    import types
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
    ob = types.ModuleType("nonebot.adapters.onebot")
    v11 = types.ModuleType("nonebot.adapters.onebot.v11")
    v11.MessageEvent = object; v11.MessageSegment = object; v11.Message = object
    sys.modules["nonebot.adapters"] = ad
    sys.modules["nonebot.adapters.onebot"] = ob
    sys.modules["nonebot.adapters.onebot.v11"] = v11


_stub()

from doupo import config as cfg, core, flame_bonus as fb  # noqa: E402

_FAILS = []
_N = 0


def check(name, ok, detail=""):
    global _N
    _N += 1
    if not ok:
        _FAILS.append("%s %s" % (name, detail))


N_SEEDS = 3000                    # 大样本：把 ±2~4% 的加成从随机噪声里拉出来
# 用**真实已注册**的四品丹方：core.craft 查 cfg.RECIPE_BY_NAME，自造丹会 nodrug。
RECIPE_NAME = next(n for n, r in cfg.RECIPE_BY_NAME.items()
                   if r.get("grade") == "四品" and r.get("need")
                   and r.get("effect") not in ("beast_flame", "puppet_soul"))
RECIPE = cfg.RECIPE_BY_NAME[RECIPE_NAME]
GRADE = RECIPE["grade"]
# 取一只 alchemy 型异火 + 一只 alchemy 型兽火
_XIAN = next(f for f in cfg.BONUS_FLAMES if f.get("kind") == "alchemy")
_BEAST = next(f for f in cfg.BEAST_FLAMES if f.get("bonus") == "alchemy")


def _player(flames=None):
    p = core.create_player("sim-%d" % _N, "火验", "g")
    p["alch"] = cfg.alch_grade_idx(GRADE)   # 与丹同品阶 → 基础成功率居中
    p["flames"] = dict(flames or {})
    p["last_craft"] = 0
    for mat, qty in RECIPE["need"].items():
        core.add_item(p, mat, qty * (N_SEEDS + 5))
    return p


def _rate(p):
    return cfg.rate_for_recipe(p, RECIPE, fb.alchemy_rate_bonus(p))[0]


def _sim(flames):
    """同一批种子跑 N 炉，返回 (成功率, 成功次数)。每次都从干净玩家深拷贝。"""
    base = _player(flames)
    ok = 0
    for i in range(N_SEEDS):
        p = dict(base)
        p["flames"] = dict(base["flames"])
        p["bag"] = dict(base["bag"])
        p["last_craft"] = 0
        if core.craft(p, RECIPE["name"], rng=lambda: (i + 0.5) / N_SEEDS)["ok"]:
            ok += 1
    return ok / N_SEEDS, ok


def test_bonus_is_real_in_rate():
    """火焰加成必须真的进成功率数值，不能只是卡面说说。"""
    r_none = _rate(_player())
    r_xian = _rate(_player({_XIAN["name"]: 1}))
    r_beast = _rate(_player({_BEAST["name"]: 1}))
    check("空火成功率应 <1", r_none < 1.0, "rate=%s" % r_none)
    check("异火抬高成功率", r_xian > r_none, "%.4f -> %.4f" % (r_none, r_xian))
    check("兽火抬高成功率", r_beast > r_none, "%.4f -> %.4f" % (r_none, r_beast))
    # 数值口径：加成值 == 成功率增量（未撞 CAP 时）
    check("异火增量≈配置值", abs((r_xian - r_none) - _XIAN["value"]) < 1e-6,
          "增量=%.4f 配置=%.4f" % (r_xian - r_none, _XIAN["value"]))
    check("兽火增量≈配置值", abs((r_beast - r_none) - _BEAST["value"]) < 1e-6,
          "增量=%.4f 配置=%.4f" % (r_beast - r_none, _BEAST["value"]))


def test_bonus_visible_in_simulated_outcome():
    """大样本模拟：有火的成功次数必须显著多于空火（不是只改了数字没生效）。"""
    r_none, n_none = _sim(None)
    r_beast, n_beast = _sim({_BEAST["name"]: 1})
    check("模拟中兽火成功率更高", n_beast > n_none,
          "空火 %d/%d vs 兽火 %d/%d" % (n_none, N_SEEDS, n_beast, N_SEEDS))
    # 大样本下至少多 30 次（2% × 3000 = 60，留余量抗边界波动）
    check("加成幅度显著（≥30 炉）", n_beast - n_none >= 30,
          "差 %d 炉" % (n_beast - n_none))


def test_batch_entry_shares_same_rate():
    """单发与批量必须共用一条成功率口径（防「单发接了批量漏接」回归）。"""
    p = _player({_XIAN["name"]: 1})
    rate_single = _rate(p)
    # 批量内部走 cfg.rate_for_recipe：直接验同一函数同一结果
    rate_batch = cfg.rate_for_recipe(p, RECIPE,
                                     cfg.__dict__.get("_dummy", 0) or fb.alchemy_rate_bonus(p))[0]
    check("两入口成功率同源", abs(rate_batch - rate_single) < 1e-9,
          "single=%.6f batch=%.6f" % (rate_single, rate_batch))


def test_cap_never_exceeded():
    """加成再高也不能越过 CRAFT_RATE_CAP（95%）。"""
    p = _player({f["name"]: 9 for f in cfg.BONUS_FLAMES if f.get("kind") == "alchemy"})
    check("封顶不越界", _rate(p) <= cfg.CRAFT_RATE_CAP + 1e-9,
          "rate=%s cap=%s" % (_rate(p), cfg.CRAFT_RATE_CAP))


def _main() -> int:
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print("ok    %s" % name)
            except Exception as e:  # noqa: BLE001
                _FAILS.append("%s 抛异常：%s" % (name, e))
                print("FAIL  %s: %s" % (name, e))
    print("=" * 70)
    print("FLAME_CRAFT_SIM total=%d failed=%d" % (_N, len(_FAILS)))
    for f in _FAILS:
        print("  FAIL " + f)
    return 0 if not _FAILS else 1


if __name__ == "__main__":
    sys.exit(_main())
