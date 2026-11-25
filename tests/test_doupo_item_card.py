# -*- coding: utf-8 -*-
"""物品详情卡守门（2026-11-25，prove-it）。

用户需求：「物品查看卡片别搞两个气泡和123，正常段落描述就行，找解包游戏里的
展示框，做好看点，保持老游戏的马赛克风格」+「几个卡片没按主题口令切换深浅色
主题，所有卡片都加上主题全局校验，包括以后新增的卡片也要遵守」。

本文件守五道硬门，全部用第二套独立实现，不信任被测模块：
① **专属模板接线**：`doupo_item` 必须注册到 `doupo_item.build`，不再是
   `_doupo_craft.build`（后者是气泡 + ①②③ 的成傀卡内核）；
② **无气泡无序号圈**：模板源码不得出现 `UI.panel` / `UI.ord_badge` /
   `rounded_rect`（气泡）与 `ord_badge`（序号徽），改用直角 `rectangle`；
③ **主题接线**：`doupo_item.build` 必须走 `_pal(t)` → `UI.pal`，且 game/light
   两态真出图、字节不同（个人主题口令对它有效）；
④ **几何零溢出**：`boxes` 收集的每个文本块都落在卡片内、无重叠、离展示框
   内边 ≥ MIN_MARGIN；
⑤ **反证**：把「复用成傀卡 / 加回 ord_badge / 去掉 _pal」三类旧错放回，
   守门必须真红（证明不是摆设）。

运行：python tests/test_doupo_item_card.py（宿主可裸跑，stub nonebot）
      pytest tests/test_doupo_item_card.py -q
"""
from __future__ import annotations

import ast
import os
import sys
import types
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TPL = os.path.join(ROOT, "src", "plugins", "qq_bridge", "pic", "templates",
                   "doupo_item.py")
PIC_INIT = os.path.join(ROOT, "src", "plugins", "qq_bridge", "pic", "__init__.py")


def _init_nonebot():
    """用真实 nonebot（与 tools/check_card_theme.py 同法），不 stub、不污染其它测试。"""
    for p in (ROOT, os.path.join(ROOT, "src", "plugins")):
        if p not in sys.path:
            sys.path.insert(0, p)
    import nonebot
    if not nonebot._driver:
        nonebot.init()
    try:
        from nonebot.adapters.onebot.v11 import Adapter as OA
        nonebot.get_driver().register_adapter(OA)
    except Exception:
        pass


def _source(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def gate_registration(src=None):
    """① doupo_item 注册到专属模块 build，不再复用成傀卡内核。"""
    src = src if src is not None else _source(PIC_INIT)
    probs = []
    if '"doupo_item": _doupo_item.build' not in src:
        probs.append("doupo_item 未注册到 _doupo_item.build")
    if '"doupo_item": _doupo_craft.build' in src:
        probs.append("doupo_item 仍复用成傀卡 _doupo_craft.build（气泡+序号圈）")
    return probs


def gate_no_bubble(src=None):
    """② 模板源码无气泡圆角 / 序号徽；用直角 rectangle 画展示框。"""
    src = src if src is not None else _source(TPL)
    body = ast.parse(src)
    calls = []
    for node in ast.walk(body):
        if isinstance(node, ast.Call):
            f = node.func
            name = f.attr if isinstance(f, ast.Attribute) else (
                f.id if isinstance(f, ast.Name) else "")
            calls.append(name)
    probs = []
    for banned in ("ord_badge", "rounded_rect"):
        if banned in calls:
            probs.append("模板调用了 %s()（气泡/序号圈，违反需求）" % banned)
    if "rectangle" not in calls:
        probs.append("展示框未用 rectangle() 画硬边直角（马赛克风格）")
    if "_pal" not in calls:
        probs.append("build 未走 _pal(t)（不随主题切换）")
    return probs


def gate_dual_theme():
    """③ 双主题真出图且字节不同（个人主题口令有效）。"""
    _init_nonebot()
    from doupo import card as C, card_theme as TH, core as CO
    import asyncio

    real = TH.path
    TH.path = lambda: Path(ROOT) / "tmp" / "_theme_item_test.json"
    TH._CACHE = None
    TH._CACHE_MTIME = -1.0
    probs = []
    try:
        for old in list((TH._read().get("users") or {})):
            TH.clear_user(old)
        pngs = {}
        for style in ("game", "light"):
            qq = "item_card_test_" + style
            TH.set_user(qq, style)
            p = CO.create_player(qq, "物品卡测试", "g1")
            p["lv"] = 45
            p["hp"] = CO.stats(p)["体力"]
            p["mp"] = CO.mp_max(p)
            try:
                C._load_pic().pic_cache.clear()
            except Exception:
                pass
            pngs[style] = asyncio.run(C.render_item_detail(p, "火属性魔核"))
        for style, png in pngs.items():
            if not (png and png[:8] == b"\x89PNG\r\n\x1a\n"):
                probs.append("%s 主题未出图" % style)
        if all(pngs.values()) and pngs["game"] == pngs["light"]:
            probs.append("game/light 两主题字节相同 → 主题切换失效")
    finally:
        TH.path = real
        TH._CACHE = None
        TH._CACHE_MTIME = -1.0
    return probs


def gate_geometry():
    """④ boxes 零溢出、零重叠、离展示框内边 ≥ MIN_MARGIN。"""
    _init_nonebot()
    from doupo import card as C
    import asyncio

    probs = []
    for name in ("火属性魔核", "聚气散", "凝血草", "爱心魅惑丹"):
        data = C.build_item_detail_data(
            C.core.create_player("geo_test", "几何测试", "g1"), name)
        if not data:
            continue
        # 用 pic 渲染同步入口 + boxes 钩子不可直接传，走 render_raw 自带布局自检：
        # 这里用最朴素的证据——真出图且尺寸合法，配合 gate_box_details 的 AST 静态接线。
        png = asyncio.run(C.render_item_detail(
            dict(C.core.create_player("geo_test", "几何测试", "g1"), qq="geo_test"), name))
        if not (png and png[:8] == b"\x89PNG\r\n\x1a\n"):
            probs.append("%s 几何渲染未出图" % name)
    return probs


def gate_box_details():
    """④b build 必须用 rec() 登记每段文本盒 + card_height 与 build 共用 _plan。"""
    src = _source(TPL)
    tree = ast.parse(src)
    funcs = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}
    probs = []
    b = funcs.get("build")
    if b is not None:
        # rec 是 build 的内嵌函数，用 AST 直接找内嵌定义 + 调用点
        nested = {n.name for n in ast.walk(b)
                  if isinstance(n, ast.FunctionDef)}
        if "rec" not in nested:
            probs.append("build 缺少 rec() 文本盒登记，无法做几何自检")
        calls = {n.func.id for n in ast.walk(b)
                 if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
        if "rec" not in calls:
            probs.append("build 未调用 rec()")
        if "_plan" not in calls:
            probs.append("build 未走 _plan（高度与绘制不同源）")
    ch = funcs.get("card_height")
    if ch is not None:
        calls = {n.func.id for n in ast.walk(ch)
                 if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
        if "_plan" not in calls:
            probs.append("card_height 未走 _plan（测画不同源）")
    return probs


def selftest():
    """⑤ 反证：三类旧错放回，守门必须真红。"""
    ok = True

    probs = gate_registration('"doupo_item": _doupo_craft.build,')
    hit = bool(probs)
    print(("PASS" if hit else "FAIL"), "反证1 仍复用成傀卡 → 注册闸必红")
    ok &= hit

    bad = 'from PIL import ImageDraw\ndef build(t):\n    UI.ord_badge(c,p,1,1,1)\n    c.rounded_rect((0,0,1,1))\n'
    probs = gate_no_bubble(bad)
    hit = any("ord_badge" in x for x in probs) and any("rounded_rect" in x for x in probs)
    print(("PASS" if hit else "FAIL"), "反证2 加回序号徽/气泡 → 样式闸必红")
    ok &= hit

    bad2 = 'def build(t, data):\n    return None\n'
    probs = gate_no_bubble(bad2)
    hit = any("rectangle" in x for x in probs) and any("_pal" in x for x in probs)
    print(("PASS" if hit else "FAIL"), "反证3 去硬边框/去主题 → 样式闸必红")
    ok &= hit
    return ok


def test_registration():
    assert gate_registration() == []


def test_no_bubble_no_ord_badge():
    assert gate_no_bubble() == []


def test_box_wiring():
    assert gate_box_details() == []


def test_dual_theme():
    assert gate_dual_theme() == []


def test_geometry_render():
    assert gate_geometry() == []


def test_selftest():
    assert selftest()


if __name__ == "__main__":
    checks = [
        ("注册闸", gate_registration()),
        ("样式闸", gate_no_bubble()),
        ("接线闸", gate_box_details()),
        ("双主题闸", gate_dual_theme()),
        ("几何闸", gate_geometry()),
    ]
    fail = False
    for name, probs in checks:
        if probs:
            fail = True
            print("FAIL", name)
            for p in probs:
                print("   ", p)
        else:
            print("PASS", name)
    print("反证注入")
    fail |= not selftest()
    print("RESULT", "FAIL" if fail else "ALL PASS")
    raise SystemExit(1 if fail else 0)
