# -*- coding: utf-8 -*-
"""宗门特色机制 P0 守门（2026-11-24 prove-it）—— 古族/丹塔/蛇人。

守玩家可感知行为：
  1. 古族族纹：战力随等级档位真实增长（+3/9/12%），非古族/老档逐字节不变；
  2. 丹塔丹会：炼丹成功率随品阶 +0.03×(品+1)，回执带 danhui，非丹塔无该键，触 CAP 0.95；
  3. 蛇人封印：怪出手攻击 -15%，战报首次 notes 可见"封印"，非蛇人无。

绝对量断言（不拿被测配置当答案），保证改坏常量/去掉钩子必红：
  * 战力用具体数字区间，不写 `power == base*(1+CLAN_TIER_PCT)`；
  * 成功率用 probe 实测的 0.55/0.58/0.82/0.95 锚点；
  * 降攻用 seal_atk_rate==0.15 与 m_atk 倍率 0.85 锚点。

变异反证（改坏 → 红，改回 → 绿）：
  * 把 clan_tier_pct 恒返回 0 → 古族档位测试红；
  * 把 danhui_bonus 恒返回 0 → 丹塔回执/成功率红；
  * 把 fight 封印挂载删掉 → 蛇人战报可见性红。

运行：python tests/test_doupo_sect_p0.py
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

from doupo import core, config as cfg  # noqa: E402


def _mk(lv=60, faction="yunlanzong", **kw):
    p = core.create_player("10001", "宗门测试", "20001")
    p["lv"] = lv
    p["faction"] = faction
    p["exp"] = 0
    p["alch"] = 3
    p["hp"] = 99999
    p["mp"] = 9999
    p.update(kw)
    return p


# ---------------------------------------------------------------------------
# P0-1 古族族纹
# ---------------------------------------------------------------------------
def test_guzu_tier_pct_absolute():
    """族纹比例按绝对档位值，不读配置当答案。"""
    assert core.clan_tier_pct(_mk(lv=1, faction="guzu")) == 0.0
    assert core.clan_tier_pct(_mk(lv=20, faction="guzu")) == 0.03
    assert core.clan_tier_pct(_mk(lv=60, faction="guzu")) == 0.09
    assert core.clan_tier_pct(_mk(lv=100, faction="guzu")) == 0.12
    # 非古族/老档一律 0
    assert core.clan_tier_pct(_mk(lv=100, faction="yunlanzong")) == 0.0
    assert core.clan_tier_pct({"lv": 100}) == 0.0


def test_guzu_power_actually_increases():
    """战力真实随档位增长：lv60 古族比同配云岚高约 9%（绝对数字锚）。"""
    plain = core.power(_mk(lv=60, faction="yunlanzong"))
    guzu = core.power(_mk(lv=60, faction="guzu"))
    assert plain == 58245, plain          # 绝对量，配置改坏也会红
    assert guzu == 63485, guzu
    assert abs((guzu - plain) / plain - 0.09) < 0.001


def test_guzu_only_six_stats_not_luck():
    """族纹加成六维，不加机缘（luck）；老档无 faction 不触发。"""
    p = _mk(lv=60, faction="guzu", luck=50)
    s = core.stats(p)
    assert s["机缘"] == 50               # 机缘不受族纹放大
    old = {"lv": 100, "bonus": {}, "flames": {}}
    # 老档无 faction → 纯函数口径 0，stats 六维与"明确非古族"完全一致（不放大）
    assert core.clan_tier_pct(old) == 0.0
    assert core.stats(old)["力量"] == core.stats({**old, "faction": "yunlanzong"})["力量"]
    # 而显式古族同档会放大（反证钩子真的接在 stats 上）
    assert core.stats({**old, "faction": "guzu"})["力量"] > core.stats(old)["力量"]


# ---------------------------------------------------------------------------
# P0-2 丹塔丹会
# ---------------------------------------------------------------------------
def test_danta_bonus_absolute():
    """丹会加成 = 0.03×(品+1)，未入品/非丹塔为 0。"""
    assert core.danhui_bonus(_mk(faction="danta", alch=0)) == 0.03
    assert abs(core.danhui_bonus(_mk(faction="danta", alch=3)) - 0.12) < 1e-9
    assert abs(core.danhui_bonus(_mk(faction="danta", alch=6)) - 0.21) < 1e-9
    assert core.danhui_bonus(_mk(faction="danta", alch=-1)) == 0.0  # 未入品
    assert core.danhui_bonus(_mk(faction="yunlanzong", alch=6)) == 0.0


def test_danta_rate_anchors_and_cap():
    """成功率锚点来自真跑探针：四品 0.70→0.82，七品触顶 0.95。

    隔离入门异火火云水炎的 +0.02（create_player 会发），只验丹会加成本身。
    """
    base4 = _mk(faction="danta", alch=3); base4["flames"] = {}
    base7 = _mk(faction="danta", alch=6); base7["flames"] = {}
    r4, _ = cfg.craft_rate_for(3, cfg.ALCHEMIST[3][0], core.craft_rate_bonus(base4))
    r7, _ = cfg.craft_rate_for(6, cfg.ALCHEMIST[6][0], core.craft_rate_bonus(base7))
    assert abs(r4 - 0.82) < 0.001, r4
    assert abs(r7 - 0.95) < 0.001, r7       # CAP 截断，不会超过 0.95
    # 真实场景：入门异火 +0.02 与丹会 +0.12 共用通道叠加 → 0.84（不各自封顶）
    real = _mk(faction="danta", alch=3)
    rr, _ = cfg.craft_rate_for(3, cfg.ALCHEMIST[3][0], core.craft_rate_bonus(real))
    assert abs(rr - 0.84) < 0.001, rr


def test_danta_receipt_key_only_for_danta():
    """回执 danhui 键仅丹塔持有；非丹塔回执键集不含它（逐字节兼容）。"""
    t = int(cfg.CRAFT_CD_SEC) + 10
    bag = {"血灵草": 99, "紫叶兰": 99}
    danta = core.craft(_mk(faction="danta", alch=3, bag=dict(bag)),
                       "回灵丹", rng=lambda: 0.0, now_ts=t)
    plain = core.craft(_mk(faction="yunlanzong", alch=3, bag=dict(bag)),
                       "回灵丹", rng=lambda: 0.0, now_ts=t)
    assert danta["ok"] is True and plain["ok"] is True
    assert "danhui" in danta and danta["danhui"] == 0.12
    assert "danhui" not in plain


def test_craft_batch_uses_same_bonus():
    """批量炼丹入口也必须走同一加成（防'改一处漏一处'双真源坑）。"""
    t = int(cfg.CRAFT_CD_SEC) + 10
    bag = {"血灵草": 99, "紫叶兰": 99}
    p = _mk(faction="danta", alch=3, bag=dict(bag))
    p["flames"] = {}   # 隔离入门异火 +0.02，只验丹会 +0.12
    r = core.craft_batch(p, "回灵丹", 3, rng=lambda: 0.0, now_ts=t)
    # 批量回执率与单发同源（都是 craft_rate_bonus 的结果）
    assert "rate" in r and abs(r["rate"] - 0.82) < 0.001, r.get("rate")


# ---------------------------------------------------------------------------
# P0-3 蛇人封印咒
# ---------------------------------------------------------------------------
def test_sheren_rate_and_mult_absolute():
    """封印率 0.15、怪攻倍率 0.85，非蛇人 0/1。"""
    assert core.seal_atk_rate(_mk(faction="sherenzu")) == 0.15
    assert core.seal_atk_rate(_mk(faction="yunlanzong")) == 0.0
    from doupo import skill_effects as fx
    st = {"debuff_atk": core.seal_atk_rate(_mk(faction="sherenzu"))}
    assert abs(fx.target_atk_mult(st) - 0.85) < 1e-9


def test_sheren_battle_visible_and_reduces_taken():
    """真战斗：蛇人战报 notes 出现封印，且承伤低于非蛇人；非蛇人无封印字样。"""
    import random
    mon = {"name": "试炼傀儡", "hp": 500000, "atk": 10000, "def": 100,
           "exp": 1000, "gold": 100, "kind": "elite"}
    rs = core.fight(_mk(faction="sherenzu", learned=["铁山靠"], loadout=["铁山靠"]),
                    dict(mon), rng=random.Random(42).random)
    rp = core.fight(_mk(faction="yunlanzong", learned=["铁山靠"], loadout=["铁山靠"]),
                    dict(mon), rng=random.Random(42).random)
    assert any("封印" in str(e.get("notes", "")) for e in rs["log"])
    assert not any("封印" in str(e.get("notes", "")) for e in rp["log"])
    # 同随机流下蛇人承伤更低（降攻真实减伤）
    assert rs["taken"] < rp["taken"], (rs["taken"], rp["taken"])


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
