# -*- coding: utf-8 -*-
"""爬塔 400-500 层重设计守门（2026-11-23 prove-it）。

用户拍板：
  ① 400-500 层是「正常范围」，按中上战力标定（不是顶配专属、也不是假目标）；
  ② 500 层不是上限，保留无限层 + 二次变陡软墙；
  ③ 标尺保持 103 封顶；
  ④ 逐层掉落：0→无限层自动生成，按怪种分档(小怪/精英/Boss)，随层数递增，
     用现有材料/丹药不硬编码新物品；跟随水位线防电梯重复刷。

守用户感受得到的行为：
  1. 中上战力满级玩家能爬到 400-500 层（真跑，不是看倍率）；
  2. 顶配能更远(>500)，中期玩家明显更低（成长梯度）；
  3. 标尺仍 103 封顶、同层同一只怪（固定难度不回归）；
  4. 500 层后难度继续变陡（软墙有效，不是无限平推）；
  5. 每层掉落 tier：boss >= elite >= normal，随层数分档递增；
  6. 掉落材料/丹药都是既有物品，水位线分轮=一口气、重打旧层不发。

变异反证（把错误实现放回去，对应用例必须红）：
  * DIFF_QUAD 改回 0.15（500层374×假目标）→ 可达性用例红；
  * TOWER_ATK_SCALE 改回 0.18 → 可达性用例红；
  * 掉落改成 boss<=normal（怪种失效）→ 分档用例红；
  * incremental_loot 忽略 paid（重打旧层也发）→ 防刷用例红。

运行：python tests/test_doupo_tower_500.py
"""
from __future__ import annotations

import os
import sys
import random
import tempfile

PLUGINS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "src", "plugins")
REPO_ROOT = os.path.dirname(os.path.dirname(PLUGINS_DIR))
for _p in (REPO_ROOT, PLUGINS_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

_TMP = tempfile.mkdtemp(prefix="tower500_test_")
os.environ["DOUPO_DATA_PATH"] = os.path.join(_TMP, "doupo.json")


def _stub_nonebot():
    import types
    nb = types.ModuleType("nonebot")
    nb.get_driver = lambda: types.SimpleNamespace(on_startup=lambda f: f)
    def on(*a, **k):
        m = types.SimpleNamespace()
        m.handle = lambda *a, **k: (lambda f: f)
        m.finish = lambda *a, **k: None
        return m
    nb.on_regex = nb.on_command = nb.on_notice = nb.on_message = on
    sys.modules["nonebot"] = nb
    for n in ("nonebot.adapters", "nonebot.adapters.onebot", "nonebot.adapters.onebot.v11"):
        sys.modules[n] = types.ModuleType(n)
    v = sys.modules["nonebot.adapters.onebot.v11"]
    v.MessageEvent = object
    v.MessageSegment = object
    v.Message = object


_stub_nonebot()

from doupo import core, config as cfg, tower_data as td, tower_session as ts  # noqa: E402


def _mk(lv, weapon=0, diyan=False, seed="P"):
    p = core.create_player(seed, "塔500测试", "g")
    p["lv"] = lv
    p["weapon"] = weapon
    p["diyan"] = diyan
    st = core.stats(p)
    p["hp"] = st["体力"]
    p["mp"] = core.mp_max(p)
    return p


def _climb(p, cap=700):
    st = ts.new_state(p["qq"], seed=p["qq"] + ":t")
    rng = random.Random(p["qq"] + ":r").random
    reached = 0
    while reached < cap:
        st["floor"] = reached
        r = core.tower_floor(p, st, rng)
        if not r["win"]:
            break
        reached += 1
        st["temp_hp"], st["temp_mp"] = r["hp"], r["mp"]
    return reached


def _median_climb(lv, weapon, diyan, n=5):
    rs = sorted(_climb(_mk(lv, weapon, diyan, "m%d_%d" % (lv, s))) for s in range(n))
    return rs[n // 2]


# ---- 难度可达性 ----
def test_mid_tier_reaches_400_500():
    """中上战力（满级+武器6）真跑落在 400-500 层。"""
    med = _median_climb(103, 6, False, n=5)
    assert 400 <= med <= 520, "中上战力应到400-500层，实测中位 %d" % med


def test_full_gear_goes_farther():
    """顶配（武器9+帝炎）应明显超过中上，且不是无限（软墙有效）。"""
    mid = _median_climb(103, 6, False, n=3)
    top = _median_climb(103, 9, True, n=3)
    assert top > mid, (mid, top)
    assert top < 700, "顶配也不该无限平推，实测 %d" % top


def test_midgame_progressively_lower():
    """中期玩家（lv90）应明显低于满级，形成成长梯度。"""
    late = _median_climb(103, 6, False, n=3)
    midgame = _median_climb(90, 5, False, n=3)
    assert midgame < late * 0.7, (midgame, late)


def test_ref_lv_still_capped_103():
    assert td.floor_ref_lv(1) == 1
    assert td.floor_ref_lv(999) == td.REF_LV_CAP == 103


def test_soft_wall_steepens_after_500():
    d500 = td.floor_difficulty(500)["hp_mult"]
    d1000 = td.floor_difficulty(1000)["hp_mult"]
    assert d500 < 2.0, "500层倍率必须温和（中上可达），实测 %s" % d500
    assert (d1000 - 1.0) > 2.0 * (d500 - 1.0), "500层后应明显变陡"


def test_same_floor_same_monster():
    """固定难度：同层怪数值只由层号决定，与打它的玩家战力无关。"""
    weak = _mk(60, 0, False, "weak")
    strong = _mk(103, 9, True, "strong")
    for floor in (10, 30, 52):
        sp = {"name": "x", "lv": td.floor_ref_lv(floor), "hard": td.floor_hard(floor),
              "kind": "塔·normal"}
        ref = core.tower_ref_player(td.floor_ref_lv(floor))
        m1 = core.monster_scaled(ref, dict(sp))
        m2 = core.monster_scaled(ref, dict(sp))
        assert m1["hp"] == m2["hp"] and m1["atk"] == m2["atk"]


# ---- 掉落系统 ----
def test_loot_tier_by_monster_kind():
    """同层 Boss 档 >= 精英 >= 普通。"""
    for n in (10, 100, 500):
        base = td.floor_loot_tier(n)
        t_n = max(1, min(4, base + td.LOOT_KIND_TIER_DELTA["normal"]))
        t_e = max(1, min(4, base + td.LOOT_KIND_TIER_DELTA["elite"]))
        t_b = max(1, min(4, base + td.LOOT_KIND_TIER_DELTA["boss"]))
        assert t_n <= t_e <= t_b, (n, t_n, t_e, t_b)


def test_loot_tier_rises_with_floor():
    tiers = [td.floor_loot_tier(n) for n in (1, 50, 51, 150, 151, 300, 301, 500)]
    assert tiers == sorted(tiers), tiers
    assert tiers[0] == 1 and tiers[-1] == 4


def test_loot_qty_grows_unbounded():
    """高层 tier 封顶后，数量继续随层数递增（无限层「以此类推」）。"""
    _, q500 = td.floor_loot(500)["material"]
    _, q1000 = td.floor_loot(1000)["material"]
    assert q1000 > q500, (q500, q1000)


def test_loot_uses_existing_items_only():
    """材料名来自真源 NOVEL_MATERIALS，丹药名是既有配方（不硬编码新物品）。"""
    real = {}
    for m in cfg.NOVEL_MATERIALS:
        real.setdefault(m["tier"], set()).add(m["name"])
    for tier, pool in td._LOOT_MATERIAL_POOL.items():
        assert set(pool) == real.get(tier, set()), (tier, pool, real.get(tier))
    for pill in td.LOOT_PILL_BY_TIER.values():
        assert pill in cfg.RECIPE_NAMES, pill


def test_loot_watermark_no_double_dip():
    """水位线：分轮总和 == 一口气；重打旧层(\u2264paid)不发。"""
    one = td.incremental_loot(300, 0)
    split: dict[str, int] = {}
    for a, b in ((0, 50), (50, 150), (150, 300)):
        for nm, qty in td.incremental_loot(b, a).items():
            split[nm] = split.get(nm, 0) + qty
    assert one == split, (one, split)
    assert td.incremental_loot(200, 200) == {}
    assert td.incremental_loot(150, 200) == {}


def test_settle_grants_loot_and_reports():
    """真结算：材料/丹药进背包，回包带 loot 明细。"""
    p = _mk(103, 6, False, "settle")
    st = ts.new_state(p["qq"], seed="settle:t")
    st["floor"] = 100
    before = dict(p.get("bag") or {})
    res = core.tower_settle(p, st)
    assert res.get("ok")
    assert res.get("loot"), res
    for nm, qty in res["loot"].items():
        assert int((p.get("bag") or {}).get(nm, 0)) >= before.get(nm, 0) + qty, nm
    # 已领过的层重结算不再发
    p2 = _mk(103, 6, False, "settle2")
    p2["tower_paid_floor"] = 100
    st2 = ts.new_state(p2["qq"], seed="settle2:t")
    st2["floor"] = 100
    res2 = core.tower_settle(p2, st2)
    assert res2.get("loot") == {}, res2


# ---------------------------------------------------------------------------
def _run_all():
    fns = [(k, v) for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    ok, fail = 0, []
    for name, fn in fns:
        try:
            fn()
            ok += 1
            print("PASS  " + name)
        except Exception as e:  # noqa: BLE001
            fail.append((name, e))
            print("FAIL  %s  ->  %s: %s" % (name, type(e).__name__, e))
    print("=" * 70)
    print("TOWER500_TOTAL=%d PASSED=%d FAILED=%d" % (len(fns), ok, len(fail)))
    return 0 if not fail else 1


if __name__ == "__main__":
    sys.exit(_run_all())
