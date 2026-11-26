# -*- coding: utf-8 -*-
"""守门（2026-11-26）：炼药播报体现异火/兽火加成，且不生硬、符合原著调性。

用户原话：「确认异火和兽火是否对炼药真的有加成……炼药结果播报应该体现，
而且说法不要太生硬，要符合原著」。

本文件守四件事：
  1. alchemy_flame_names 只列「真进成功率」的火，异火/兽火身份分对；
  2. 护炉行有火才出、无火空串，三类文案互斥且按 seed 轮换；
  3. 玩家可见护炉行**不出现**百分比/成数/「加成」「成功率」等开发向术语；
  4. cmd_craft 单炉成功/失败、批量回包三处都接了护炉行（AST 接线守门）。
"""
from __future__ import annotations

import ast
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLUGINS = os.path.join(REPO, "src", "plugins")
for _p in (REPO, PLUGINS):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def _stub_nonebot():
    import types
    if "nonebot" in sys.modules:
        return
    nb = types.ModuleType("nonebot")
    nb.get_driver = lambda: types.SimpleNamespace(on_startup=lambda f: f)

    def on_regex(*a, **k):
        m = types.SimpleNamespace()
        m.handle = lambda *a, **k: (lambda f: f)
        m.finish = lambda *a, **k: None
        m.on_command = lambda *a, **k: m
        return m
    nb.on_regex = on_regex
    nb.on_command = on_regex
    nb.on_notice = on_regex
    sys.modules["nonebot"] = nb
    ad = types.ModuleType("nonebot.adapters")
    ob = types.ModuleType("nonebot.adapters.onebot")
    v11 = types.ModuleType("nonebot.adapters.onebot.v11")
    v11.MessageEvent = object
    v11.MessageSegment = object
    v11.Message = object
    sys.modules["nonebot.adapters"] = ad
    sys.modules["nonebot.adapters.onebot"] = ob
    sys.modules["nonebot.adapters.onebot.v11"] = v11


_stub_nonebot()

from doupo import config as cfg  # noqa: E402
from doupo import doupo_text as txt  # noqa: E402
from doupo import flame_bonus as fb  # noqa: E402

_XIAN = next(f["name"] for f in cfg.BONUS_FLAMES if f.get("kind") == "alchemy")
_BEAST = next(f["name"] for f in cfg.BEAST_FLAMES if f.get("bonus") == "alchemy")
_ATK = next(f["name"] for f in cfg.BEAST_FLAMES if f.get("bonus") == "atk")


# ---------------------------------------------------------------------------
# 1 明细口径：只列真进成功率的火
# ---------------------------------------------------------------------------
def test_alchemy_flame_names_classifies_identity():
    p = {"flames": {_XIAN: 2, _BEAST: 1}}
    got = {d["name"]: d for d in fb.alchemy_flame_names(p)}
    assert got[_XIAN]["identity"] == "异火", got
    assert got[_BEAST]["identity"] == "兽火", got
    assert got[_XIAN]["lv"] == 2 and got[_BEAST]["lv"] == 1, got


def test_alchemy_flame_names_excludes_non_alchemy_and_unowned():
    p = {"flames": {_ATK: 3, _XIAN: 0}}
    assert fb.alchemy_flame_names(p) == [], "atk 型火与未收服的火不得进炼药播报"


def test_detail_matches_rate_source():
    """播报列出的火，必须就是 alchemy_rate_bonus 真正计分的火（防两张皮）。"""
    p = {"flames": {_XIAN: 1, _BEAST: 2, _ATK: 5}}
    names = {d["name"] for d in fb.alchemy_flame_names(p)}
    # 把播报名单上的火全部拿掉，加成必须归零（说明名单之外的火没贡献）
    p2 = {"flames": {_ATK: 5}}
    assert fb.alchemy_rate_bonus(p2) == 0.0
    assert _XIAN in names and _BEAST in names and _ATK not in names


# ---------------------------------------------------------------------------
# 2 护炉行：有火才出、分类互斥、轮换
# ---------------------------------------------------------------------------
def test_flame_aid_empty_when_no_fire():
    assert txt.craft_flame_aid() == ""
    assert txt.craft_flame_aid((), ()) == ""


def test_flame_aid_three_branches_distinct():
    x = txt.craft_flame_aid((_XIAN,), ())
    b = txt.craft_flame_aid((), (_BEAST,))
    m = txt.craft_flame_aid((_XIAN,), (_BEAST,))
    assert x and b and m
    assert len({x, b, m}) == 3, "异火/兽火/混合三分支文案不得雷同"
    assert _XIAN in x and _BEAST in b
    assert _XIAN in m and _BEAST in m


def test_flame_aid_rotates_with_seed():
    a = txt.craft_flame_aid((_XIAN,), (), seed=0)
    b = txt.craft_flame_aid((_XIAN,), (), seed=1)
    assert a != b, "同名火不同 seed 应轮换旁白"


JARGON = ("%", "加成", "成功率", "几成", "十成", "把握", "概率", "数值", "+")


def test_flame_aid_no_dev_jargon():
    """玩家可见护炉行不得出现生硬术语——只讲火候，不讲数字。"""
    samples = [
        txt.craft_flame_aid((_XIAN,), (), seed=k) for k in range(4)
    ] + [
        txt.craft_flame_aid((), (_BEAST,), seed=k) for k in range(4)
    ] + [
        txt.craft_flame_aid((_XIAN,), (_BEAST,), seed=0),
    ]
    for s in samples:
        for w in JARGON:
            assert w not in s, "护炉行不该出现 %r：%s" % (w, s)


# ---------------------------------------------------------------------------
# 3 接线守门：三处调用点都带护炉行
# ---------------------------------------------------------------------------
def test_cmd_craft_wires_flame_aid_at_all_three_sites():
    path = os.path.join(PLUGINS, "doupo", "commands.py")
    with open(path, encoding="utf-8") as fh:
        tree = ast.parse(fh.read(), filename=path)
    funcs = {n.name: n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    for name in ("cmd_craft", "_cmd_craft_batch"):
        assert name in funcs, "缺函数 %s" % name
        calls = [
            n for n in ast.walk(funcs[name])
            if isinstance(n, ast.Call) and getattr(n.func, "id", None) == "_craft_flame_aid_line"
        ]
        # 单炉有成功/失败两条路径，批量一条
        want = 2 if name == "cmd_craft" else 1
        assert len(calls) >= want, "%s 护炉行接线不足：%d < %d" % (name, len(calls), want)


def test_cmd_craft_renders_aid_line_end_to_end():
    """端到端：有炼药火的玩家，单炉回包出现护炉旁白；无火玩家不出现。"""
    import asyncio

    from doupo import commands, core

    class _Mem:
        def __init__(self, p):
            self.p = p

        async def get_player(self, qq):
            return dict(self.p)

        async def put_player(self, p):
            self.p = dict(p)

    def _player(flames):
        p = core.create_player("flame-aid-test", "护炉", "g")
        p["alch"] = cfg.alch_grade_idx("一品")
        p["flames"] = dict(flames)
        p["last_craft"] = 0
        rec = cfg.RECIPE_BY_NAME["聚气散"]
        for mat, qty in rec["need"].items():
            core.add_item(p, mat, qty * 5)
        return p

    with_fire = asyncio.run(commands.cmd_craft(_Mem(_player({_XIAN: 1})), "90001", "聚气散"))
    no_fire = asyncio.run(commands.cmd_craft(_Mem(_player({})), "90002", "聚气散"))
    assert _XIAN in with_fire, "有火回包应点名护炉火：%s" % with_fire
    # 无火玩家走 nofire 前置（不会硬塞护炉行）
    assert _XIAN not in no_fire, no_fire


# ---------------------------------------------------------------------------
# 3 一炉只认最强的两朵（2026-11-26 用户拍板「取最强 2 种」）
# ---------------------------------------------------------------------------
def _alch_flames():
    """全部 alchemy 型火（B 线异火 + 兽火/新生异火），按 config 顺序。"""
    return [f for f in cfg.BONUS_FLAMES if f.get("kind") == "alchemy"] + \
           [f for f in cfg.BEAST_FLAMES if f.get("bonus") == "alchemy"]


def test_only_top_two_alchemy_flames_count():
    """三朵同等级时，第三朵不得再进加成，也不得进播报名单（防「说在帮忙却没用」）。"""
    fl = _alch_flames()
    assert len(fl) >= 3, "配置里应至少有三朵 alchemy 火，否则这条守门没意义"
    vals = {f["name"]: float(f["value"]) for f in fl}
    p = {"flames": {n: 1 for n in vals}}
    # 实现口径：贡献（value×lv）降序 → value 降序 → 名字；lv 全 1 时等价于 value 降序
    top2 = [n for n, _v in sorted(vals.items(), key=lambda kv: (-kv[1], kv[0]))[:2]]
    assert abs(fb.alchemy_rate_bonus(p) - sum(vals[n] for n in top2)) < 1e-9
    assert {d["name"] for d in fb.alchemy_flame_names(p)} == set(top2), \
        "播报名单必须就是计分的那两朵"


def test_ranking_uses_contribution_not_base_value():
    """排序按**当前贡献**（value×等级），不是按单级 value：喂得高可以反超。"""
    fl = sorted(_alch_flames(), key=lambda f: -float(f["value"]))
    hi, mid, lo = fl[0], fl[1], fl[-1]
    p = {"flames": {hi["name"]: 1, mid["name"]: 1, lo["name"]: cfg.FLAME_MAX_LV}}
    cands = [(hi["name"], float(hi["value"]) * 1, float(hi["value"])),
             (mid["name"], float(mid["value"]) * 1, float(mid["value"])),
             (lo["name"], float(lo["value"]) * cfg.FLAME_MAX_LV, float(lo["value"]))]
    # 文档口径：贡献降序 → 单级 value 降序 → 名字（同分时才用到后两级）
    cands.sort(key=lambda c: (-c[1], -c[2], c[0]))
    expect = {cands[0][0], cands[1][0]}
    got = {d["name"] for d in fb.alchemy_flame_names(p)}
    assert got == expect, (cands, got)
    # 加成额必须正好是这两朵的**贡献**之和（lv 已经乘进去，不是单级 value×2）
    assert abs(fb.alchemy_rate_bonus(p) - (cands[0][1] + cands[1][1])) < 1e-9


def test_flame_limit_constant_is_the_only_knob():
    """变异反证：只改 ALCHEMY_FLAME_LIMIT，加成与播报同步跟随（不是写死 2）。"""
    p = {"flames": {f["name"]: 1 for f in _alch_flames()}}
    old = fb.ALCHEMY_FLAME_LIMIT
    try:
        fb.ALCHEMY_FLAME_LIMIT = 1
        one = fb.alchemy_rate_bonus(p)
        assert len(fb.alchemy_flame_names(p)) == 1
        fb.ALCHEMY_FLAME_LIMIT = 3
        three = fb.alchemy_rate_bonus(p)
        assert len(fb.alchemy_flame_names(p)) == 3
        assert three > one, "放宽上限必须真的多算"
    finally:
        fb.ALCHEMY_FLAME_LIMIT = old


if __name__ == "__main__":
    _fails = 0
    for _name, _fn in sorted(globals().items()):
        if _name.startswith("test_") and callable(_fn):
            try:
                _fn()
                print("ok    %s" % _name)
            except Exception as _e:  # noqa: BLE001
                _fails += 1
                print("FAIL  %s: %s" % (_name, _e))
    print("=" * 70)
    print("CRAFT_FLAME_AID total=%d failed=%d" % (
        sum(1 for n in globals() if n.startswith("test_")), _fails))
    sys.exit(1 if _fails else 0)
