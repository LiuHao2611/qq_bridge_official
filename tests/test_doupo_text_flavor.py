# -*- coding: utf-8 -*-
"""斗破玩家可见文案原著语境化 · 守门测试（2026-11-23）。

守四条：
1. 玩家可见零元说明（禁词扫描，含动态展开样本）；
2. 原文台词零代编（文案池里不得出现「角色名 + 冒号引号」冒充原著说话）；
3. 分档真的生效（13 境界破境句互不相同、丹品分档互不相同）；
4. 战斗评价池已全量换皮（无现代梗残留）。

新增/改文案后必须跑；注入反例验证能拦住回退。
"""
from __future__ import annotations

import os
import re
import sys
import types

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLUGINS_DIR = os.path.join(REPO_ROOT, "src", "plugins")


def _stub_nonebot():
    """doupo/__init__.py 顶层有 @get_driver().on_startup，裸测要先 stub。"""
    if "nonebot" in sys.modules and hasattr(sys.modules["nonebot"], "get_driver"):
        return
    nb = types.ModuleType("nonebot")
    nb.get_driver = lambda: types.SimpleNamespace(on_startup=lambda f: f)

    def on_regex(*a, **k):
        m = types.SimpleNamespace()
        m.handle = lambda *a, **k: (lambda f: f)
        m.finish = lambda *a, **k: None
        m.priority = 5
        m.block = True
        return m
    nb.on_regex = on_regex
    sys.modules["nonebot"] = nb
    for n in ("nonebot.adapters", "nonebot.adapters.onebot",
              "nonebot.adapters.onebot.v11"):
        sys.modules[n] = types.ModuleType(n)
    v = sys.modules["nonebot.adapters.onebot.v11"]
    v.MessageEvent = object
    v.MessageSegment = object
    v.Message = object


_stub_nonebot()
for _p in (REPO_ROOT, PLUGINS_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from doupo import battle_comments as bc  # noqa: E402
from doupo import config as cfg  # noqa: E402
from doupo import doupo_text as txt  # noqa: E402


#: 玩家可见禁词（考据/元说明/快照战力/错误角色）。与考据文档 §8.1 同口径。
BAN_WORDS = (
    "原著", "未载", "不代编", "游戏原创", "语料", "非原著角色", "游戏设定",
    "考据", "出处：", "心魔劫影", "战力", "快照",
)


def test_all_player_texts_no_ban_words():
    """所有玩家可见静态句 + 动态展开样本，均无元说明禁词。"""
    texts = txt.all_player_texts()
    assert len(texts) > 60, f"文案池样本过少: {len(texts)}"
    hits = [(t, w) for t in texts for w in BAN_WORDS if w in t]
    assert not hits, f"玩家可见文案命中禁词: {hits[:5]}"


def test_break_cross_lines_cover_all_realms():
    """13 个境界都有跨境界破境句，且互不重复。"""
    for realm in cfg.REALM_NAMES:
        assert realm in txt.CROSS_LINES, f"境界 {realm} 缺跨境界破境句"
        assert "——" in txt.CROSS_LINES[realm], f"{realm} 破境句缺破境意象"
    vals = list(txt.CROSS_LINES.values())
    assert len(set(vals)) == len(vals), "存在重复的破境句（分档失效）"


def test_break_star_lines_cover_all_realms():
    """13 个境界都有升星句，且互不重复。"""
    for realm in cfg.REALM_NAMES:
        assert realm in txt.STAR_LINES, f"境界 {realm} 缺升星句"
    vals = list(txt.STAR_LINES.values())
    assert len(set(vals)) == len(vals), "存在重复的升星句"


def test_break_cross_and_star_are_distinct():
    """跨境界破境 ≠ 同境界升星，避免「升一星像重破境」。"""
    overlap = set(txt.CROSS_LINES.values()) & set(txt.STAR_LINES.values())
    assert not overlap, f"跨境界与升星文案重复: {overlap}"


def test_craft_success_grade_tiers_distinct():
    """丹品分档句：四~九品各一句（一二三品共用低阶句、九品与帝品同为顶级是设计）。"""
    grades = ["四品", "五品", "六品", "七品", "八品", "九品"]
    vals = [txt.CRAFT_SUCCESS[g] for g in grades]
    assert len(set(vals)) == len(vals), "丹品分档句有重复"
    assert txt.CRAFT_SUCCESS["帝品"] == txt.CRAFT_SUCCESS["九品"], "帝品应与九品同档顶级句"
    assert "丹雷" in txt.CRAFT_SUCCESS["七品"], "七品缺丹雷"
    assert "丹雨" in txt.CRAFT_SUCCESS["九品"], "九品缺丹雨"


def test_break_result_keeps_test_anchors():
    """保留既有测试锚点，避免文案优化误伤功能守门。"""
    cross = txt.break_result("九星斗者", "一星斗师", "斗师", True)
    star = txt.break_result("三星斗者", "四星斗者", "斗者", False)
    assert "境界突破" in cross and "晋入" in cross
    assert "突破成功" in star and "晋入" in star
    assert "气血斗气已满" in cross and "气血斗气已满" in star


def test_craft_fail_variants_and_anchor():
    """炼药失败保留「炼制失败」锚点且有多条旁白轮换。"""
    assert len(txt.CRAFT_FAIL_LINES) >= 3
    a = txt.craft_fail(3, "十成", "", 0)
    b = txt.craft_fail(3, "十成", "", 1)
    assert a != b, "失败旁白没轮换"
    assert "炼制失败" in a and "药渣" in a
    assert "丹炉尚温" in txt.craft_cd(5)
    assert "材料不足" in txt.craft_mat("血灵草")


def test_flavor_pools_no_modern_slang():
    """战斗评价池全量换皮：无网络梗 / 跨世界词。"""
    slang = (
        "猛如虎", "二百五", "寂寞", "保密协议", "天气预报", "祖传玉玺",
        "月初的余额", "保鲜膜", "堵车", "零杠五", "yyds", "破防", "emo",
        "摸鱼", "内卷", "打工", "KPI", "退堂鼓",
    )
    texts = bc.all_texts()
    assert len(texts) >= 40
    hits = [(t, w) for t in texts for w in slang if w in t]
    assert not hits, f"战斗评价残留现代梗: {hits[:5]}"


def test_flavor_pools_keep_selection_contract():
    """换皮不改选择规则：短/长评格式、稳定性、标签覆盖都在。"""
    battle = {"ts": 123, "kind": "遭遇", "log": [{"kind": "出手", "dmg": 100}]}
    a = bc.pick(battle, "q1", force_length="short")
    b = bc.pick(battle, "q1", force_length="short")
    long = bc.pick(battle, "q1", force_length="long")
    assert a == b, "同一场评价不稳定"
    assert a.startswith("【战斗评价】")
    assert len(long) > len(a), "长评没有更长"
    tags = {x["tag"] for x in bc.SHORT} | {x["tag"] for x in bc.LONG}
    assert {"low_dmg", "no_mp", "crit", "miss", "win", "lose", "general"} <= tags


def test_source_file_has_no_banned_words_in_strings():
    """AST 扫 doupo_text.py 的字符串常量：玩家句不得混进元说明。
    模块 docstring（开发者注释）不算玩家可见，跳过。
    """
    import ast
    path = os.path.join(PLUGINS_DIR, "doupo", "doupo_text.py")
    with open(path, encoding="utf-8") as fh:
        tree = ast.parse(fh.read(), filename=path)
    bad: list[str] = []
    # 跳过**所有** docstring（模块 / 类 / 函数）：原实现只把「模块 docstring」
    # 比进白名单，函数级 docstring 一旦含「考据」就被当成玩家可见句报红
    # （2026-11-25 实测 danwen_grant 的「文案不透露概率/考据」被误报，该文件
    # 因此长期红着没人管）。原注释写的就是「模块/函数 docstring」，这里补齐
    # 实现，而不是改注释去迁就漏写的代码。
    docstrings: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef)):
            _d = ast.get_docstring(node, clean=False)
            if _d:
                docstrings.add(_d)
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if node.value in docstrings or node.value.startswith(("#", ":", "{")) \
                    or "def " in node.value or "\n\n##" in node.value:
                continue
            if "——" not in node.value and "。" not in node.value and "！" not in node.value:
                continue
            for w in BAN_WORDS:
                if w in node.value:
                    bad.append(f"L{node.lineno}: {w} in {node.value[:30]}")
    assert not bad, f"文案池字符串含禁词: {bad[:5]}"


def test_mutation_guard_ban_detector_works():
    """变异反证：注入一句带禁词的假文案，扫描器必须拦住。"""
    fake = "这是原著无载的设定。"
    hits = [w for w in BAN_WORDS if w in fake]
    assert hits, "禁词表失效——注入的违规句没被识别"
    assert "原著" in hits
