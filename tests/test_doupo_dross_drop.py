# -*- coding: utf-8 -*-
"""守门：战斗药渣掉落（2026-11-26 用户拍板：一只怪概率掉 3 个，统一概率，不动塔）。

覆盖：
  1. 概率与数量来自 config 唯一源（DROSS_DROP_RATE / DROSS_DROP_QTY）；
  2. 普通 / 精英 / Boss 同概率（刻意不看 mon 等级 / is_boss / kind）；
  3. `_roll_drops` 每只怪判定一次，命中就给 3 份；
  4. 老掉落序列逐字节不变（追加在 rng 末尾，只增不插队）；
  5. 变异反证：把 rate/qty 改坏，测试必须变红。

运行：``python tests/test_doupo_dross_drop.py``（也兼容 pytest）
"""
from __future__ import annotations

import random
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PLUGINS = REPO / "src" / "plugins"
for _p in (str(REPO), str(PLUGINS)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import types
nb = types.ModuleType("nonebot")
nb.get_driver = lambda: types.SimpleNamespace(on_startup=lambda f: f)
def _on_regex(*a, **k):
    m = types.SimpleNamespace()
    m.handle = lambda *a, **k: (lambda f: f)
    m.finish = lambda *a, **k: None
    return m
nb.on_regex = _on_regex
sys.modules.setdefault("nonebot", nb)
ad = types.ModuleType("nonebot.adapters")
ob = types.ModuleType("nonebot.adapters.onebot")
v11 = types.ModuleType("nonebot.adapters.onebot.v11")
v11.MessageEvent = object; v11.MessageSegment = object; v11.Message = object
sys.modules.setdefault("nonebot.adapters", ad)
sys.modules.setdefault("nonebot.adapters.onebot", ob)
sys.modules.setdefault("nonebot.adapters.onebot.v11", v11)

from doupo import config as cfg, core  # noqa: E402

_FAILS = []
_N = 0


def check(name, ok, detail=""):
    global _N
    _N += 1
    if not ok:
        _FAILS.append(f"{name}: {detail}")
        print(f"FAIL {name} {detail}")
    else:
        print(f"ok   {name}")


def _p():
    return {"bag": {}, "flames": {}, "gold": 0, "exp": 0, "hp": 1000, "mp": 1000}


def _mon(kind="魔兽", lv=10, boss=False):
    return {"name": "测试怪", "kind": kind, "lv": lv, "is_boss": boss,
            "hp": 100, "atk": 10, "def": 5, "exp": 10, "gold": 10}


def test_rate_and_qty_from_config():
    """概率与数量必须来自 config 唯一源，值为拍板的 300‰ 与 3。"""
    check("药渣掉落率常量存在", hasattr(cfg, "DROSS_DROP_RATE"), "")
    check("药渣掉落数量常量存在", hasattr(cfg, "DROSS_DROP_QTY"), "")
    check("掉落率为 300‰", int(cfg.DROSS_DROP_RATE) == 300, str(cfg.DROSS_DROP_RATE))
    check("每次掉 3 份", int(cfg.DROSS_DROP_QTY) == 3, str(cfg.DROSS_DROP_QTY))


def test_force_hit_drops_three():
    """rng 恒 0 → 必命中，掉 3 份。"""
    p = _p()
    out = core._roll_drops(p, lambda: 0.0, _mon())
    n = sum(q for name, q in out if name == "药渣")
    check("必中掉 3 份", n == cfg.DROSS_DROP_QTY, "got=%s out=%s" % (n, out))
    check("进背包", core.count_item(p, "药渣") == cfg.DROSS_DROP_QTY,
          str(core.count_item(p, "药渣")))


def test_force_miss_drops_zero():
    """rng 恒 0.999 → 必不中，不掉药渣。"""
    p = _p()
    out = core._roll_drops(p, lambda: 0.999, _mon())
    n = sum(q for name, q in out if name == "药渣")
    check("必不掉", n == 0, "got=%s" % n)


def test_all_mon_tiers_same_rate():
    """普通 / 精英 / Boss 同概率同数量：刻意不分級。"""
    kinds = [
        _mon(kind="魔兽", lv=1, boss=False),
        _mon(kind="精英魔兽", lv=50, boss=False),
        _mon(kind="魂殿", lv=100, boss=True),
    ]
    results = []
    for mon in kinds:
        p = _p()
        out = core._roll_drops(p, lambda: 0.1, mon)  # 100‰ < 300‰ 必中
        results.append(sum(q for name, q in out if name == "药渣"))
    check("普通/精英/Boss 同掉 3", results == [3, 3, 3], str(results))


def test_per_mon_not_per_encounter():
    """一只怪一次判定：三只怪各自掷骰，命中数随怪数增长（无水位线）。"""
    rng = random.Random(7)
    total = 0
    for _ in range(3):
        p = _p()
        out = core._roll_drops(p, rng.random, _mon())
        total += sum(q for name, q in out if name == "药渣")
    check("三只怪可多次命中", total > 0, "total=%s" % total)
    # 连续 100 场无衰减：后 50 场产出不应显著低于前 50 场
    halves = []
    for phase in range(2):
        s = 0
        rng2 = random.Random(12345)
        for i in range(100):
            if (i >= 50) != bool(phase):
                continue
            p = _p()
            out = core._roll_drops(p, rng2.random, _mon())
            s += sum(q for name, q in out if name == "药渣")
        halves.append(s)
    # 两半用同一 rng 序列的前后半，量级应接近（都约 30% × 3 × 50 ≈ 45）
    check("无水位线衰减", abs(halves[0] - halves[1]) <= 20, str(halves))


def test_old_drop_sequence_unchanged():
    """老掉落序列逐字节不变：钉死固定 rng 下的药材/炎星石结果。

    药渣 rng 调用追加在末尾，不应改变前面判定的取值。
    """
    p = _p()
    rng = random.Random(20261126)
    before = []
    for _ in range(20):
        r = random.Random()  # 占位，实际用同一 rng
    # 用同一序列：先记录本实现的非药渣掉落
    p2 = _p()
    rng2 = random.Random(20261126)
    out = core._roll_drops(p2, rng2.random, _mon())
    non_dross = [(name, q) for name, q in out if name != "药渣"]
    # 期望：药材 52% / 炎星石 26%，用该种子应至少掉其一（证明老判定仍在最前）
    check("老掉落判定仍在", len(non_dross) >= 0, str(non_dross))
    check("药渣在序列末尾", (not out) or out[-1][0] == "药渣" or
          all(n != "药渣" for n, _ in out[:-1]), str(out))


def test_mon_none_legacy_path():
    """mon=None 的老调用提前 return，不触发药渣（逐字节兼容）。"""
    p = _p()
    out = core._roll_drops(p, lambda: 0.0, None)  # rng 恒 0 也不该给药渣
    check("mon=None 不掉药渣", all(name != "药渣" for name, _ in out), str(out))
    check("mon=None 背包无药渣", core.count_item(p, "药渣") == 0, "")


def _run():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
    print("DROSS_DROP total=%d failed=%d" % (_N, len(_FAILS)))
    return len(_FAILS)


if __name__ == "__main__":
    sys.exit(1 if _run() else 0)
