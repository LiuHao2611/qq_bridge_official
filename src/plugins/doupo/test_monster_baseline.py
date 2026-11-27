# -*- coding: utf-8 -*-
r"""野外自适应怪「投入净生效」守门（Q2①，用户 2026-11-27 拍板）。

旧行为：``monster_scaled`` 用**真实玩家面板**反解怪的血/攻。玩家力量 +10000
时怪血同步变厚，胜率/回合/受伤逐字节不变 —— 装备、丹药、异火、额外六维
在最高频玩法里全是假属性（报告 B2）。

新口径：
  * 野外自适应（默认）→ 反解只用 ``_baseline_at(玩家等级)``（裸装新号），
    玩家额外投入在 ``fight()`` 里净生效；
  * 固定难度（爬塔 / 遗迹 / 周本 / 九星突破 BOSS）→ 传入的 p 本来就是
    **标尺**（tower_ref_player / 周一快照 / 突破快照），必须显式
    ``use_baseline=False`` 保留标尺口径，难度仍按层/周锁定；
  * 怪物隐藏面板（命中/暴击/闪避）依旧**不进 hp/atk 反解**。

运行：
    python src/plugins/doupo/test_monster_baseline.py
"""
from __future__ import annotations

import inspect
import os
import random
import sys
import types

_HERE = os.path.dirname(os.path.abspath(__file__))
_PLUGINS = os.path.dirname(_HERE)
_ROOT = os.path.dirname(os.path.dirname(_PLUGINS))
for _p in (_ROOT, _PLUGINS):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def _stub_nonebot():
    nb = types.ModuleType("nonebot")
    nb.get_driver = lambda: types.SimpleNamespace(on_startup=lambda f: f)
    nb.get_bot = lambda: types.SimpleNamespace(send=lambda *a, **k: None)

    def on_regex(*a, **k):
        return types.SimpleNamespace(handle=lambda *a, **k: (lambda f: f),
                                     finish=lambda *a, **k: None)
    nb.on_regex = on_regex
    sys.modules["nonebot"] = nb
    for mod in ("nonebot.adapters", "nonebot.adapters.onebot",
                "nonebot.adapters.onebot.v11"):
        sys.modules[mod] = types.ModuleType(mod)
    for attr in ("Message", "MessageEvent", "MessageSegment"):
        setattr(sys.modules["nonebot.adapters.onebot.v11"], attr, object)


_stub_nonebot()

from doupo import config as cfg, core  # noqa: E402

_SPECIES = cfg.MONSTERS[3]


def _p(lv: int, power: int = 0, qq: str = "baseline-probe") -> dict:
    p = core.create_player(qq, "探针", "g1")
    p["lv"] = int(lv)
    p["hp"] = core.stats(p)["体力"]
    if power:
        p.setdefault("bonus", {})["力量"] = int(power)
    return p


def test_field_scaling_ignores_player_extra_stats():
    """核心：野外怪面板不再跟着玩家额外属性变厚。"""
    for lv in (10, 40, 80):
        base = core.monster_scaled(_p(lv, qq="b:" + str(lv)), _SPECIES)
        rich = core.monster_scaled(_p(lv, 10000, "r:" + str(lv)), _SPECIES)
        for k in ("hp", "atk", "def", "lv", "exp", "gold"):
            assert base[k] == rich[k], (
                "lv%d 的%s 被玩家额外力量改变：%s → %s" % (lv, k, base[k], rich[k]))


def test_given_anchor_keeps_old_scale():
    """固定难度的标尺口径必须保留：标尺更强 → 怪更厚（否则难度被削弱）。"""
    base = core.monster_scaled(_p(40, qq="anchor-b"), _SPECIES, use_baseline=False)
    rich = core.monster_scaled(_p(40, 10000, "anchor-r"), _SPECIES, use_baseline=False)
    assert rich["hp"] > base["hp"], "use_baseline=False 没保留标尺反解"


def test_at_lv_still_overrides_baseline():
    """章节门禁不变：at_lv 指定后怪按该等级基线，玩家再强也抬不动。"""
    lo = core.monster_scaled(_p(1, qq="at-lo"), _SPECIES, at_lv=5)
    hi = core.monster_scaled(_p(40, 10000, "at-hi"), _SPECIES, at_lv=5)
    assert lo["hp"] == hi["hp"] and lo["atk"] == hi["atk"]
    assert lo["lv"] == hi["lv"] == 5


def test_field_encounter_extra_investment_really_lands():
    """玩家感受：整场遭遇战的回合/受伤因额外投入而真实改善。"""
    def run(power):
        wins = rounds = taken = 0
        for i in range(20):
            p = _p(40, power, "enc:%d:%d" % (power, i))
            r = core.encounter(p, rng=random.Random(500 + i).random, chapter=None)
            wins += int(bool(r["win"]))
            rounds += int(r["rounds"])
            taken += int(r["taken"])
        return wins, rounds, taken
    base, rich = run(0), run(10000)
    assert rich[0] >= base[0], "额外投入反而更难过"
    assert rich[1] < base[1], "额外投入没让战斗变快：%s → %s" % (base, rich)
    assert rich[2] < base[2], "额外投入没让玩家少挨打：%s → %s" % (base, rich)


def test_hidden_panel_not_in_hp_atk_reverse_solve():
    """红线：怪物隐藏面板只管命中/暴击/闪避，绝不进难度反解。"""
    plain = dict(_SPECIES)
    with_panel = dict(_SPECIES, panel=core.monster_panel(40, "", False))
    a = core.monster_scaled(_p(40, qq="panel-a"), plain)
    b = core.monster_scaled(_p(40, qq="panel-b"), with_panel)
    for k in ("hp", "atk", "def"):
        assert a[k] == b[k], "隐藏面板污染了 %s 反解" % k


def test_fixed_difficulty_callers_opt_out_explicitly():
    """四处标尺调用（突破/遗迹/塔/周本）必须显式 use_baseline=False。"""
    src = inspect.getsource(core.monster_scaled)
    assert "use_baseline: bool = True" in src, "默认必须走等级基线"
    body = inspect.getsource(core)
    # 突破 snap / 遗迹 ref / 塔 _ref / 周本 snapshot —— 少一处就会把固定难度
    # 悄悄改成按等级基线反解，玩家投入污染周本难度。
    anchors = ("monster_scaled(snap, species", "monster_scaled(ref, dict(sg",
               "monster_scaled(_ref, species", "monster_scaled(snapshot, species")
    for anchor in anchors:
        assert anchor in body, "标尺调用找不到：%s" % anchor
        # 用 rindex：docstring 注释里也有同样的调用文本，真正调用在后面。
        start = body.rindex(anchor)
        chunk = body[start:start + 240]
        assert "use_baseline=False" in chunk, "标尺调用丢了 use_baseline=False：%s" % anchor


if __name__ == "__main__":
    fns = sorted(k for k in globals() if k.startswith("test_"))
    fails = 0
    for fn in fns:
        try:
            globals()[fn]()
            print("PASS", fn)
        except Exception as exc:  # noqa: BLE001
            fails += 1
            print("FAIL", fn, exc)
    print("== %d passed, %d failed ==" % (len(fns) - fails, fails))
    sys.exit(1 if fails else 0)
