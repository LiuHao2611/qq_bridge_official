# -*- coding: utf-8 -*-
"""兽火系统守门（2026-11-24 prove-it）—— 入道不送火 / 对应魔兽掉火 / 只留异火兽火。

守玩家可感知行为（改坏常量、去掉钩子、恢复送火、称呼回潮，必红）：
  1. 入道不送火：新号 flames 全 0、STARTER_FLAME 为空、grant_faction_flame 不发；
  2. 空火禁炼药/禁养火（nofire），**但兽火单方是引火途径例外**（否则第一朵火死锁）；
  3. 引火通道：兽火单方成功率 = BEAST_SEAL_RATE(0.70)，不受炼药师品阶越阶惩罚
     （未入品炼四品紫火丹仍 0.70，而普通四品丹只有 0.025）；
  4. 服兽火丹得对应兽火；已持有不吞丹（flame_dup）；
  5. 野外「对应魔兽掉火」：只有带 flame 的魔兽掉、只掉对应那一朵、50% 概率、
     已持有折算药渣；非兽火魔兽（嗜血鼠）永远不掉；
  6. 九星 BOSS 分档：斗宗以下兽火档 50%、斗宗以上异火档 100%；
     兽火池按境界梯度解锁（lv1 只有紫火/桃花火，不会直接掉血妖焰火）；
  7. 玩家可见文案只有「异火/兽火」两种称呼（无天然火/人造火/融合火）；
  8. 老档阳火/化生火折算药渣后移除（不静默失效）；
  9. 兽火可喂养（feed_flame 查表含兽火）。

绝对量断言（不拿被测配置当答案），运行：python tests/test_doupo_flame_beast.py
"""
from __future__ import annotations

import os
import re
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

from doupo import (  # noqa: E402
    core, config as cfg, flame_bonus as fb, flame_drop as fd,
)


def _mk(lv=1, **kw):
    p = core.create_player("20001", "兽火测试", "g1")
    p["lv"] = lv
    p["exp"] = 0
    p["pill_exp"] = 0
    p["hp"] = 999999
    p["mp"] = 99999
    p.update(kw)
    return p


def _mats(p, pill, mult=1):
    for m, n in cfg.RECIPE_BY_NAME[pill]["need"].items():
        core.add_item(p, m, n * mult)


# ---------------------------------------------------------------------------
# 1 入道不送火
# ---------------------------------------------------------------------------
def test_no_starter_flame_granted():
    """新号空火：flames 全 0，STARTER_FLAME 空串，兼容入口不发火。"""
    p = _mk()
    assert cfg.STARTER_FLAME == ""
    assert fb.has_any_flame(p) is False
    assert [k for k, v in p["flames"].items() if int(v or 0) > 0] == []
    # 兼容入口 grant_faction_flame 仍存在但返回空，且不写任何火
    assert core.grant_faction_flame(p) == ""
    assert fb.has_any_flame(p) is False


# ---------------------------------------------------------------------------
# 2 空火禁炼药/禁养火 + 兽火单方例外
# ---------------------------------------------------------------------------
def test_empty_flame_blocks_craft_and_feed():
    """无火：普通丹（回灵丹）与养火都 nofire。"""
    p = _mk()
    assert core.craft(p, "回灵丹")["reason"] == "nofire"
    assert core.craft_batch(p, "回灵丹", 3)["reason"] == "nofire"
    assert core.feed_flame(p, "紫火")["reason"] == "nofire"


def test_beast_pill_is_exempt_from_nofire():
    """兽火单方是引火途径：空火也能开炉（否则永远拿不到第一朵火）。"""
    p = _mk()
    _mats(p, "紫火丹")
    r = core.craft(p, "紫火丹", rng=lambda: 0.0)
    assert r["ok"] is True, r
    assert r["rate"] == 0.70


# ---------------------------------------------------------------------------
# 3 引火通道：固定率，不受越阶惩罚
# ---------------------------------------------------------------------------
def test_beast_seal_rate_ignores_grade_penalty():
    """未入品炼四品紫火丹 = 0.70；同一玩家炼普通四品丹只有 0.025（被越阶惩罚）。"""
    p = _mk()
    assert p.get("alch", 0) == cfg.ALCH_NONE
    rec = cfg.RECIPE_BY_NAME["紫火丹"]
    rate, diff = cfg.rate_for_recipe(p, rec, 0.0)
    assert (rate, diff) == (0.70, 0)
    # 普通四品丹（紫心丹）仍走越阶：0.5 × 0.05 = 0.025
    plain = cfg.RECIPE_BY_NAME["紫心破障丹"] if "紫心破障丹" in cfg.RECIPE_BY_NAME else None
    if plain is not None and str(plain.get("grade")) == "四品":
        r2, d2 = cfg.rate_for_recipe(p, plain, 0.0)
        assert abs(r2 - 0.025) < 1e-9, (r2, d2)


def test_beast_seal_rate_constant():
    """成功率常量是 0.70（值变了必须同步设计文档与文案）。"""
    assert abs(cfg.BEAST_SEAL_RATE - 0.70) < 1e-9


# ---------------------------------------------------------------------------
# 4 服丹得火 / 重复不吞丹
# ---------------------------------------------------------------------------
def test_beast_pill_grants_and_duplicate_keeps_pill():
    """服紫火丹得紫火；再服不吞丹（flame_dup），火等级不变。"""
    p = _mk()
    _mats(p, "紫火丹")
    assert core.craft(p, "紫火丹", rng=lambda: 0.0)["ok"] is True
    r = core.use_item(p, "紫火丹")
    assert r["ok"] is True and r["flame"] == "紫火"
    assert int(p["flames"]["紫火"]) == 1
    # 再造一颗，服用应拒绝且不扣丹
    _mats(p, "紫火丹")
    core.craft(p, "紫火丹", rng=lambda: 0.0)
    before = core.count_item(p, "紫火丹")
    d = core.use_item(p, "紫火丹")
    assert d["ok"] is False and d["reason"] == "flame_dup"
    assert core.count_item(p, "紫火丹") == before, "重复服用不该吞丹"


# ---------------------------------------------------------------------------
# 5 对应魔兽掉火
# ---------------------------------------------------------------------------
def test_monster_flame_map_is_exact():
    """7 种魔兽各掉自己那一朵；血妖焰火无单一魔兽（不在表里）。"""
    assert cfg.MONSTER_FLAME == {
        "紫晶翼狮王": "紫火",
        "奎木兽": "桃花火",
        "惊涛龙兽": "乾蓝水炎",
        "天骨蟒": "天寒火",
        "海魔兽": "海魔炎",
        "幻火蝎龙兽": "幻金火",
        "天阴三头狮": "天阴魔火",
    }
    assert "血妖焰火" not in cfg.MONSTER_FLAME.values()


def test_wild_drop_is_corresponding_flame():
    """打紫晶翼狮王只掉紫火；非兽火魔兽（嗜血鼠）永远不掉。"""
    p = _mk(45)
    got = fd.wild_flame_drops(p, ["紫晶翼狮王"], rng=lambda: 0.0, add_item_fn=core.add_item)
    assert got == ["紫火"]
    assert int(p["flames"]["紫火"]) == 1
    p2 = _mk(9)
    assert fd.wild_flame_drops(p2, ["嗜血鼠"], rng=lambda: 0.0, add_item_fn=core.add_item) == []
    assert fb.has_any_flame(p2) is False


def test_wild_drop_rate_50():
    """千分率 500：r=0.499 掉，r=0.5 不掉（边界锚定）。"""
    p = _mk(66)
    assert fd.wild_flame_drops(p, ["天骨蟒"], rng=lambda: 0.499, add_item_fn=core.add_item) == ["天寒火"]
    p2 = _mk(66)
    assert fd.wild_flame_drops(p2, ["天骨蟒"], rng=lambda: 0.5, add_item_fn=core.add_item) == []
    assert cfg.BEAST_MONSTER_DROP_RATE == 500


def test_wild_drop_duplicate_gives_dross():
    """已持有再掉 → 折算药渣 30，不重复给火、不涨等级。"""
    p = _mk(74)
    p["flames"]["海魔炎"] = 2
    got = fd.wild_flame_drops(p, ["海魔兽"], rng=lambda: 0.0, add_item_fn=core.add_item)
    assert got == []
    assert int(p["flames"]["海魔炎"]) == 2
    assert core.count_item(p, "药渣") == 30


# ---------------------------------------------------------------------------
# 6 九星分档 + 兽火池梯度
# ---------------------------------------------------------------------------
def test_nine_star_tier_by_realm():
    """斗宗前（realm<7）走兽火档(500‰)；斗宗起走异火档(1000‰ 必得)。"""
    assert cfg.YIHUO_DROP_MIN_REALM == 7
    assert fd.flame_tier_for_lv(60) == "beast"     # realm 6 斗皇
    assert fd.flame_tier_for_lv(72) == "yihuo"     # realm 7 斗宗
    # 兽火档：r=0.6 空手；异火档：r=0.99 也必得
    assert fd.roll_flame_drop(60, rng_val=0.6)["ok"] is False
    hit = fd.roll_flame_drop(72, rng_val=0.99)
    assert hit["ok"] is True and hit["name"] in ("海心焰", "火云水炎")


def test_beast_pool_gated_by_realm():
    """兽火池按境界解锁：lv1 只有弱火两朵，高阶火不提前出现。"""
    assert fd.beast_pool_for_lv(1) == ["紫火", "桃花火"]
    mid = fd.beast_pool_for_lv(25)
    assert mid == ["紫火", "桃花火", "乾蓝水炎", "天寒火"]
    full = fd.beast_pool_for_lv(60)
    assert "血妖焰火" in full and "天阴魔火" in full
    assert "血妖焰火" not in fd.beast_pool_for_lv(1)


# ---------------------------------------------------------------------------
# 7 称呼只有异火/兽火
# ---------------------------------------------------------------------------
def test_no_legacy_flame_wording_visible():
    """玩家可见文件里不得再出现「天然火/人造火/融合火/其他火焰」。"""
    bad = re.compile("天然火|人造火|融合火|其他火焰")
    targets = ["card.py", "commands.py", "doupo_text.py", "flame_bonus.py", "flame_drop.py"]
    for fn in targets:
        path = os.path.join(PLUGINS_DIR, "doupo", fn)
        with open(path, encoding="utf-8") as f:
            txt = f.read()
        hits = bad.findall(txt)
        assert not hits, (fn, hits)


def test_beast_data_complete():
    """8 朵兽火齐备：每朵有魔兽、丹方、稀有度；幻金火/天阴魔火的魔兽是原创留痕。"""
    assert len(cfg.BEAST_FLAMES) == 8
    assert cfg.BEAST_FLAME_NAMES == [
        "紫火", "桃花火", "乾蓝水炎", "天寒火",
        "海魔炎", "天阴魔火", "血妖焰火", "幻金火",
    ]
    for f in cfg.BEAST_FLAMES:
        assert f["beast"], f
        assert f["pill"] in cfg.RECIPE_BY_NAME, f
        assert int(f["need"]) > 0
        assert f["bonus"] in ("alchemy", "atk")
    assert cfg.BEAST_FLAME_BY_NAME["幻金火"].get("game_only") is True
    # 8 个丹方都注册且 effect=beast_flame
    for f in cfg.BEAST_FLAMES:
        rec = cfg.RECIPE_BY_NAME[f["pill"]]
        assert rec["effect"] == "beast_flame"
        assert rec["flame"] == f["name"]


# ---------------------------------------------------------------------------
# 8 老档迁移
# ---------------------------------------------------------------------------
def test_legacy_flames_migrated_not_silent():
    """阳火3级+化生火1级 → 移除并折算 120 药渣；已有兽火不受影响；幂等。"""
    p = _mk()
    p["flames"]["阳火"] = 3
    p["flames"]["化生火"] = 1
    p["flames"]["紫火"] = 1
    gone = fd.migrate_legacy_flames(p, core.add_item)
    assert sorted(gone) == ["化生火", "阳火"]
    assert "阳火" not in p["flames"] and "化生火" not in p["flames"]
    assert int(p["flames"]["紫火"]) == 1
    assert core.count_item(p, "药渣") == 120
    assert fd.migrate_legacy_flames(p, core.add_item) == []


# ---------------------------------------------------------------------------
# 9 兽火可喂养
# ---------------------------------------------------------------------------
def test_beast_flame_feedable():
    """兽火能喂药渣升级（feed_flame 查表必须含 BEAST_FLAMES）。"""
    p = _mk(60)
    p["flames"]["紫火"] = 1
    core.add_item(p, "药渣", 300)
    # 紫火 need=55，1→2 级需 55×2=110 药渣
    r = core.feed_flame(p, "紫火", 200)
    assert r["ok"] is True, r
    assert r["done"] is True and r["need"] == 110, r
    assert int(p["flames"]["紫火"]) == 2


if __name__ == "__main__":
    fns = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_")]
    ok = bad = 0
    for name, fn in fns:
        try:
            fn()
            print("PASS %s" % name)
            ok += 1
        except Exception as e:  # noqa: BLE001
            print("FAIL %s → %r" % (name, e))
            bad += 1
    print("\n%d PASSED, %d FAILED" % (ok, bad))
    sys.exit(1 if bad else 0)
