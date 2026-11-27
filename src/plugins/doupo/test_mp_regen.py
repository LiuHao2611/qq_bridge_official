# -*- coding: utf-8 -*-
r"""B6 普攻回气守门（2026-11-27）。

旧行为：斗气跨战结转、耗尽后只能武器普攻且**一点不回**，要回满只能靠突破/升级。
真跑 lv90 连 12 场：场 3 之后多败 —— 斗气上限这条养成线（青莲地心火 +25%）
在长线连战里几乎等于没有。

现在：**不放技的回合**（斗气不够 / 无技 / 被禁技）普攻回上限 3%，至少 1 点，
封顶不越上限；放技的回合绝不回气（否则高消耗技白嫖）。

运行：
    python src/plugins/doupo/test_mp_regen.py
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

from doupo import battle_core as bc  # noqa: E402
from doupo import battle_review as br  # noqa: E402
from doupo import config as cfg  # noqa: E402
from doupo import core  # noqa: E402


def _player(lv=30, skills=(), mp=None):
    p = core.create_player("b6-probe", "回气探针", "g1")
    p["lv"] = lv
    p["learned"] = list(skills)
    p["loadout"] = list(skills)
    # 🚨 三处都要清：`skills` 是已学等级表，`_pick_skill` 也读它 ——
    # 只清 learned/loadout 的话新号仍会自己放「五轮离火法」，用例就不是纯普攻。
    p["skills"] = {}
    p["bag"] = {}
    p["hp"] = core.stats(p)["体力"]
    p["mp"] = core.mp_max(p) if mp is None else mp
    return p


def _mon(lv=30):
    mon = core.monster_scaled(_player(lv), cfg.MONSTERS[0], at_lv=lv)
    mon["hp"] = 10 ** 9
    mon["atk"] = 50          # 打不死玩家，才能跑满整场看回气
    mon["def"] = 0
    mon["dodge"] = 0.0
    mon.pop("skill", None)
    return mon


def _fight(p, seed=11):
    return bc.fight(p, _mon(int(p["lv"])), rng=random.Random(seed).random,
                    roll_aid=False, auto_pill=False)


def test_regen_amount_is_small_and_proportional():
    p = _player(lv=30)
    cap = core.mp_max(p)
    reg = core.mp_regen_on_plain(p)
    assert reg == max(1, int(cap * core.MP_REGEN_PLAIN) + (1 if cap * core.MP_REGEN_PLAIN % 1 else 0))
    assert 0 < reg < cap * 0.1, "回气量太大：一场就能把斗气刷满"
    # 低斗气上限的新号也至少回 1 点（不能出现 0 点回气的死机制）
    tiny = _player(lv=1)
    assert core.mp_regen_on_plain(tiny) >= 1


def test_plain_attack_refills_mp():
    p = _player(lv=30, mp=0)
    assert p["mp"] == 0
    res = _fight(p)
    plain = [it for it in res["log"] if int(it.get("mp_cost") or 0) == 0
             and it.get("mp_regen") is not None]
    assert plain, "整场没有普攻回合，用例无效"
    assert any(int(it.get("mp_regen") or 0) > 0 for it in plain), (
        "普攻回合没回气，B6 是死机制")
    # 逐回合真的在涨（不是只有末回合一次性补）
    grown = [(int(it["mp_before"]), int(it["mp_after"])) for it in plain
             if int(it.get("mp_regen") or 0) > 0]
    assert grown and all(after > before for before, after in grown), (
        "回气没写进本回合快照，玩家在回看卡上看不到")
    assert p["mp"] <= core.mp_max(p)


def test_regen_keeps_late_fight_casting():
    """B6 的**真实收益**：后半场还有斗气放技，而不是场 3 之后永久普攻。"""
    p = _player(lv=40, skills=("碎石掌", "青木剑诀"))
    on = _fight(p)
    tail_on = [it for it in on["log"][-60:] if int(it.get("mp_cost") or 0) > 0]
    orig = core.mp_regen_on_plain
    core.mp_regen_on_plain = lambda *a, **k: 0
    try:
        off = _fight(_player(lv=40, skills=("碎石掌", "青木剑诀")))
    finally:
        core.mp_regen_on_plain = orig
    tail_off = [it for it in off["log"][-60:] if int(it.get("mp_cost") or 0) > 0]
    assert not tail_off, "对照组后半场还在放技，用例无效（斗气本来就没耗尽）"
    assert tail_on, "后半场一次技都没放出来，回气没接上"


def test_skill_rounds_never_refill():
    p = _player(lv=40, skills=("碎石掌", "青木剑诀"))
    res = _fight(p)
    casts = [it for it in res["log"] if int(it.get("mp_cost") or 0) > 0]
    assert casts, "整场没放过技，用例无效"
    for it in casts:
        assert int(it.get("mp_regen") or 0) == 0, "放技的回合也在回气（白嫖斗气）"
    # 斗气账目必须对得上：有人偷偷加气却不记账，这条会红。
    for it in res["log"]:
        if it.get("mp_before") is None or it.get("mp_after") is None:
            continue
        assert int(it["mp_after"]) == (int(it["mp_before"]) - int(it.get("mp_cost") or 0)
                                      + int(it.get("mp_regen") or 0)), (
            "斗气账目对不上：%s" % (it.get("kind"),))


def test_regen_never_exceeds_cap():
    p = _player(lv=30)
    cap = core.mp_max(p)
    gain = core.mp_regen_on_plain(p)
    # 封顶是函数自己的责任（战斗只负责加上去）——满槽 0、差 1 点只补 1 点。
    assert core.mp_regen_on_plain(p, cap) == 0, "满槽还在回气，斗气会越打越多"
    assert core.mp_regen_on_plain(p, cap - 1) == 1
    assert core.mp_regen_on_plain(p, 0) == gain
    # 战斗侧：任何一条快照都不许越过上限
    p["mp"] = cap - 1
    res = _fight(p)
    assert p["mp"] <= cap, "回气把斗气顶过了上限"
    for it in res["log"]:
        if it.get("mp_after") is not None:
            assert int(it["mp_after"]) <= cap, (
                "回气当场就把斗气顶过上限（快照 %s > %s）" % (it["mp_after"], cap))


def test_fight_and_review_are_wired():
    src = inspect.getsource(bc.fight)
    assert "mp_regen_on_plain" in src, "战斗没接普攻回气"
    # 必须只在 else（不放技）分支里，放技分支不许出现
    after = src.split("mp_regen_on_plain")[0][-400:]
    assert "else" in after, "回气没挂在普攻分支上"
    rsrc = inspect.getsource(br)
    assert "mp_regen" in rsrc, "回看卡不显示回气，玩家只能看到斗气莫名上涨"


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
