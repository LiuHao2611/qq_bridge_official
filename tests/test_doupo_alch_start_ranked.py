# -*- coding: utf-8 -*-
"""新号「未入品」起步守门（2026-11-23 prove-it）。

用户诉求：新入道就四品炼药师不合理，改「未入品」起步（原著萧炎开局并非炼药师）。

守四件用户感受得到的事：
  1. create_player 新号 alch == ALCH_NONE(-1)，面板显示「未入品」而非「九品炼药师」；
  2. 首炉经验不足绝不白跳一品（负索引 ALCHEMIST[-1] 门槛 0 的老坑）；
  3. 未入品攒够一品的经验才晋一品，门槛与 ALCHEMIST[0][1] 同源，不新造魔数；
  4. 老存档 alch=0..8 语义零变更（alch=3 仍四品炼药师）。

注入验证（把错误实现放回去，对应用例必须红）：
  * ALCH_START 改回 3 → test_new_player_starts_unranked 红；
  * alch_tier 里 -1 分支删掉（走 ALCHEMIST[-1]）→ test_tier_none + test_panel 红；
  * _alch_level_up 改回 cfg.ALCHEMIST[idx][1] → test_no_free_first_tier 红。

运行：python tests/test_doupo_alch_start_ranked.py
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

from doupo import core, config as cfg, card  # noqa: E402


def _new():
    return core.create_player("NEWBIE_AUDIT", "审计新号", "900000009")


def test_new_player_starts_unranked():
    p = _new()
    assert p["alch"] == cfg.ALCH_NONE == -1, (p["alch"], cfg.ALCH_NONE)
    assert p["alch_exp"] == 0


def test_tier_none_maps_to_unranked_with_first_grade_cost():
    name, need = cfg.alch_tier(-1)
    assert name == cfg.ALCH_NONE_NAME == "未入品", name
    assert need == cfg.ALCHEMIST[0][1] == 400, (need, cfg.ALCHEMIST[0][1])


def test_tier_out_of_range_raises_no_silent_fallback():
    for bad in (-2, -9, 9, 100):
        try:
            cfg.alch_tier(bad)
        except ValueError:
            continue
        raise AssertionError("越界索引 %r 未显式报错（禁止静默兜底成九品）" % bad)


def test_label_none_has_no_alchemist_suffix():
    assert cfg.alch_label(-1) == "未入品", cfg.alch_label(-1)
    assert cfg.alch_label(3) == "四品炼药师", cfg.alch_label(3)
    assert cfg.alch_label(0) == "一品炼药师", cfg.alch_label(0)


def test_panel_shows_unranked_not_ninth_grade():
    p = _new()
    d = card.build_data(p)
    assert d["alch_name"] == "未入品", d["alch_name"]
    assert "九品" not in d["alch_name"], d["alch_name"]
    assert d["alch_need"] == 400, d["alch_need"]


def test_recipe_card_subtitle_shows_unranked():
    p = _new()
    d = card.build_recipes_data(p, 1)
    assert "未入品" in d["subtitle"], d["subtitle"]
    assert "九品炼药师" not in d["subtitle"], d["subtitle"]


def test_no_free_first_tier_at_zero_exp():
    """首炉 exp=0 时 alch=-1 不得跳阶（ALCHEMIST[-1] 门槛 0 的老坑）。"""
    p = _new()
    up = core._alch_level_up(p)
    assert p["alch"] == cfg.ALCH_NONE, p["alch"]
    assert up == "", up
    assert p["alch_exp"] == 0


def test_unranked_promotes_only_after_first_grade_cost():
    """攒够 400 经验 → 晋一品；经验被扣除、alch 变 0。"""
    p = _new()
    p["alch_exp"] = cfg.ALCHEMIST[0][1]           # 400
    up = core._alch_level_up(p)
    assert p["alch"] == 0, p["alch"]
    assert p["alch_exp"] == 0, p["alch_exp"]
    assert up == "一品", up


def test_unranked_below_first_grade_cost_stays():
    p = _new()
    p["alch_exp"] = cfg.ALCHEMIST[0][1] - 1       # 399
    up = core._alch_level_up(p)
    assert p["alch"] == cfg.ALCH_NONE, p["alch"]
    assert p["alch_exp"] == 399, p["alch_exp"]
    assert up == "", up


def test_old_save_alch_3_still_fourth_grade():
    """老存档 alch=3 语义零变更：仍是四品炼药师，面板不回退。"""
    p = _new()
    p["alch"] = 3
    assert cfg.alch_tier(3)[0] == "四品", cfg.alch_tier(3)
    assert cfg.alch_label(3) == "四品炼药师"
    d = card.build_data(p)
    assert d["alch_name"] == "四品炼药师", d["alch_name"]
    assert d["alch_need"] == 2080, d["alch_need"]


def test_old_save_all_ranks_unchanged():
    """0..8 逐档核对：名字与门槛与原表逐字节一致。"""
    for idx, (name, need) in enumerate(cfg.ALCHEMIST):
        t_name, t_need = cfg.alch_tier(idx)
        assert (t_name, t_need) == (name, need), (idx, (t_name, t_need), (name, need))


def test_unranked_can_still_try_low_recipes():
    """业务流程畅通：未入品仍能炼一品丹（越1档、成功率低，但不被禁）。"""
    p = _new()
    rate, diff = cfg.craft_rate_for(p["alch"], "一品")
    assert diff == 1, diff                 # 未入品炼一品 = 越 1 档
    assert 0 < rate < cfg.craft_rate(0), (rate, cfg.craft_rate(0))
    avail = core.available_recipes(p)
    assert any(r["grade"] == "一品" for r in avail), "未入品新号看不到一品丹方"


# ---------------------------------------------------------------------------
# runner
# ---------------------------------------------------------------------------
def _run_all():
    fns = [(k, v) for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    ok = 0
    fail = []
    for name, fn in fns:
        try:
            fn()
            ok += 1
            print(f"PASS  {name}")
        except Exception as e:  # noqa: BLE001
            fail.append((name, e))
            print(f"FAIL  {name}  ->  {type(e).__name__}: {e}")
    print("=" * 70)
    print(f"ALCH_START_TOTAL={len(fns)} PASSED={ok} FAILED={len(fail)}")
    return 0 if not fail else 1


if __name__ == "__main__":
    sys.exit(_run_all())
