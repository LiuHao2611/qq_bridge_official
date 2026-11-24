# -*- coding: utf-8 -*-
"""包裹视图守门（2026-11-24）—— 丹药优先排序 + 全量不翻页 + 格子角标。

事故背景：背包卡与「背包」文本都写死 ``items[:24]``，且排序是「数量降序」，
lv100+ 的号 64 种物品里前 24 全是堆量药材，**刚炼的丹药被整段截掉**
（用户原话「我炼制的丹药没显示在背包里」）。

本测试钉死四件事：
1. 丹药（含带纹丹）必须排在任何材料之前；
2. **全量展示、不翻页**（用户口径「背包别搞翻页，延长」）——64 种物品一行不许丢；
3. 带纹丹**名称不变**（卡面/文本都显示基础丹名），纹数只走右上角标
   ``corner_tr``，几纹写几；锁定只走左上角标 ``corner_tl``＝「锁」；
4. 角标颜色与卡片标题同族金（``gold``），不吃 accent/text 色。

自带 runner：python tests/test_bagview.py
"""
from __future__ import annotations

import os
import sys
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (ROOT, os.path.join(ROOT, "src", "plugins")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

os.environ["DOUPO_CHANCE_RATE"] = "0"


def _stub():
    nb = types.ModuleType("nonebot")
    nb.get_driver = lambda: types.SimpleNamespace(on_startup=lambda f: f)

    def on_regex(*a, **k):
        m = types.SimpleNamespace()
        m.handle = lambda *a, **k: (lambda f: f)
        m.finish = lambda *a, **k: None
        return m

    nb.on_regex = on_regex
    nb.on_command = on_regex
    nb.on_notice = on_regex
    nb.on_message = on_regex
    sys.modules["nonebot"] = nb
    ad = types.ModuleType("nonebot.adapters")
    ad.Event = type("Event", (), {})
    sys.modules["nonebot.adapters"] = ad
    for n in ("nonebot.adapters.onebot", "nonebot.adapters.onebot.v11"):
        sys.modules[n] = types.ModuleType(n)
    v11 = sys.modules["nonebot.adapters.onebot.v11"]
    v11.MessageEvent = ad.Event
    v11.MessageSegment = object
    v11.Message = object


_stub()

from doupo import bagview, card, config as cfg  # noqa: E402


def _tpl_mod():
    """取 standalone pic 包里的 doupo_list 模板模块（宿主零依赖，不需要 nonebot）。"""
    pic = card._load_pic()
    assert pic is not None, "pic 包加载失败"
    return sys.modules[pic.__name__ + ".templates.doupo_list"]

#: 带纹丹名（一纹），全测试共用
DW1 = "聚气散" + cfg.danwen_suffix(1)


def _big_bag():
    """造一个「64 种物品、新材料堆量、丹药各 1~2 枚」的极端背包（复刻线上号）。"""
    p = {"gold": 4370584, "bag": {}}
    # 60 种高堆量材料（模拟血蟒枝 998 这类）
    for i in range(60):
        p["bag"]["材料%02d" % i] = 900 - i * 10
    # 4 种刚炼出来的丹药（数量很小 → 旧排序必被截断）
    p["bag"]["聚气散"] = 2
    p["bag"][DW1] = 1
    p["bag"]["破厄丹"] = 1
    p["bag"]["回灵丹"] = 3
    return p


def t_pills_first():
    """🔒 丹药（含带纹丹）必须排在任何材料之前——本次事故的就医点。"""
    p = _big_bag()
    items = bagview.sorted_items(p)
    names = [n for n, _ in items]
    first_material = next(i for i, n in enumerate(names) if not bagview.is_pill(n))
    pill_pos = [i for i, n in enumerate(names) if bagview.is_pill(n)]
    assert pill_pos, "没识别出任何丹药"
    assert max(pill_pos) < first_material, (
        "丹药被材料挤到后面了：丹药位次 %s，第一个材料位次 %s" % (pill_pos, first_material))
    # 具体到玩家最关心的四枚（全量展示，位次无关页码）
    for nm in ("聚气散", DW1, "破厄丹", "回灵丹"):
        assert nm in names, "%s 不在包裹里" % nm


def t_no_truncation():
    """🔒 全量展示：64 种物品一件都不许丢（旧版 items[:24] 会砍掉 40 种）。"""
    p = _big_bag()
    items = bagview.sorted_items(p)
    assert len(items) == 64, len(items)
    d = card.build_bag_data(p)
    assert len(d["rows"]) == 64, "卡片只画了 %d 行，丢了 %d 种" % (
        len(d["rows"]), 64 - len(d["rows"]))
    assert "64 种物品" in d["subtitle"], d["subtitle"]
    # 不翻页：卡面不许再出现页码字段
    assert "page" not in d and "total_pages" not in d, sorted(d)


def t_pill_name_unchanged():
    """🔒 带纹丹在格子里**显示基础丹名**，纹数只走右上角标（用户：名称不要变）。"""
    p = _big_bag()
    d = card.build_bag_data(p)
    row = next(r for r in d["rows"] if r.get("corner_tr"))
    assert row["title"].startswith("聚气散 ×"), "格子标题被改名了：%r" % row["title"]
    assert "一纹" not in row["title"], "纹数不该进标题：%r" % row["title"]
    assert row["corner_tr"] == "一纹", row["corner_tr"]
    # 普通丹不该有角标
    plain = next(r for r in d["rows"] if r["title"].startswith("破厄丹 "))
    assert "corner_tr" not in plain and "corner_tl" not in plain, plain


def t_tier_badge_matches_tier():
    """几纹就显示几：一纹/二纹/三纹各自对应角标。"""
    p = {"gold": 0, "bag": {"聚气散": 1}}
    for i in range(1, len(cfg.DANWEN_TIERS) + 1):
        p["bag"]["聚气散" + cfg.danwen_suffix(i)] = 1
    d = card.build_bag_data(p)
    got = sorted(r["corner_tr"] for r in d["rows"] if r.get("corner_tr"))
    assert got == sorted(cfg.DANWEN_TIERS), got
    # 三个带纹版本 + 基础丹 = 4 行，名称全部是「聚气散」
    assert len(d["rows"]) == 4, len(d["rows"])
    assert all(r["title"].startswith("聚气散") for r in d["rows"]), d["rows"]


def t_lock_corner():
    """锁定只写左上角「锁」，名字与纹数都不受影响。"""
    p = _big_bag()
    d = card.build_bag_data(p, locked={"破厄丹", DW1})
    locked_rows = [r for r in d["rows"] if r.get("corner_tl")]
    assert len(locked_rows) == 2, [r["title"] for r in locked_rows]
    assert all(r["corner_tl"] == "锁" for r in locked_rows)
    dw_row = next(r for r in locked_rows if r.get("corner_tr"))
    assert dw_row["corner_tr"] == "一纹" and dw_row["corner_tl"] == "锁"
    assert dw_row["title"].startswith("聚气散 ×"), dw_row["title"]
    # 锁定的普通丹不带纹角标
    plain_row = next(r for r in locked_rows if not r.get("corner_tr"))
    assert plain_row["title"].startswith("破厄丹 ×"), plain_row["title"]


def t_corner_geometry_inside_cell():
    """🔒 角标必须**骑在格子上边框**（截断框线）、用楷体+金色光晕、且不压名称。

    2026-11-24 首版把角标塞在格子内部（``S.small`` 默认 ``anchor="lm"`，
    传 ``cell_y+3`` 被当中线 → 小字上缘浮到格子外）；用户看完实图要求
    「用特殊字体 + 金色光晕 + 骑在边框上，看上去是截断边框的感觉」。
    本用例钉死改版后的几何与画法：上缘越过格顶（框线真被截断）、下缘进格子、
    上探量 ≤ 行间距（不啃上一行）、内缩 > 圆角半径（不糊角）、走楷体 + 高斯光晕。
    """
    tpl = _tpl_mod()
    # ① **骑在框线上**：上缘必须高于格顶（否则只是塞在格子里，框线不会断），
    #    下缘必须进入格子（否则整块浮在行缝里，看着像另一行的东西）。
    for cell_y in (0, 120, 640):
        top = tpl.corner_top(cell_y)
        bot = tpl.corner_bottom(cell_y)
        assert top < cell_y, "角标上缘没越过格顶，框线不会被截断：top=%s cell=%s" % (
            top, cell_y)
        assert bot > cell_y, "角标整块浮在框线上方：bot=%s cell=%s" % (bot, cell_y)
        assert tpl.corner_cy(cell_y) - tpl.corner_badge_h() // 2 == top, "中线换算不一致"
        assert bot <= cell_y + tpl.GRID_CELL_H, "角标超出格底"
    # ② 上探量必须 >0（真截断）且 ≤ 行间距（不许啃上一行格底）
    over = tpl.corner_overhang()
    assert 0 < over <= tpl.GRID_GAP, "上探 %spx 越界（GRID_GAP=%s）" % (over, tpl.GRID_GAP)
    # ③ 不压名称
    gap = tpl.corner_name_gap()
    assert gap >= 2, "角标下缘与名称上缘净空只有 %spx（会黏字）" % gap
    # ④ 不糊圆角：内缩必须大于格子圆角半径
    assert tpl.CORNER_INSET > 8, "CORNER_INSET=%s 会盖掉格子圆角" % tpl.CORNER_INSET
    # ⑤ 光晕必须外扩（等于色块边界就成了描边，不是「晕」）
    assert tpl.CORNER_GLOW > 0, tpl.CORNER_GLOW
    # ⑥ 特殊字体：必须走楷体（doupo_kai.ttf），不能退回等线
    import inspect
    src = inspect.getsource(tpl.draw_corner_badge)
    assert 'UI.font(size, "kai")' in src, "角标没走楷体（特殊字体）"
    assert "GaussianBlur" in src, "角标没有高斯光晕"
    assert 'g["panel"]' in src, "角标没铺格子底色去截断边框"
    # ⑦ 名称中线与「有无角标」无关（常量，不是条件表达式）
    name_top = tpl.GRID_NAME_CY - tpl.FS_SMALL // 2
    assert name_top > tpl.CORNER_CY_OFF + tpl.corner_badge_h() // 2, name_top
    assert tpl.GRID_NAME_CY * 2 <= tpl.GRID_CELL_H, "名称中线把格子撑爆"


def t_row_baseline_aligned():
    """🔒 同一行内「有角标」与「无角标」格子的名称必须同一基线（不许下移）。

    2026-11-24 实图事故：名称曾按「有角标就 +4px」下移，同一行里破厄丹比
    三纹青灵丹低 4px，肉眼一眼就能看出歪。这里用模板自带的 ``boxes`` 自检协议
    拿真实文字盒，直接比同一行四格的名称中心 y。
    """
    tpl = _tpl_mod()
    pic = card._load_pic()
    p = {"qq": "1", "gold": 0, "bag": {
        "聚气散" + cfg.danwen_suffix(2): 1,   # 有角标（二纹）
        "回灵丹": 2,                          # 无角标
        "血蟒枝": 3,                          # 无角标（材料）
        "冰晶石": 4,                          # 无角标（材料）
    }}
    boxes: list[tuple] = []
    theme = pic.resolve_theme("light", "斗破背包")
    tpl.build(theme, card.build_bag_data(p), card.CARD_WIDTH, boxes=boxes)
    names = {b[0]: b for b in boxes if str(b[0]).startswith("g") and "." not in str(b[0])}
    assert len(names) == 4, sorted(names)
    centers = {}
    for k in range(4):
        b = names["g%d" % k]
        centers[k] = (b[2] + b[4]) / 2.0       # (top + bottom) / 2
    ys = set(round(v) for v in centers.values())
    assert len(ys) == 1, "同一行名称高低不一：%s（角标格不得下移）" % centers
    # 且本行确实是「有角标 + 无角标」混排（否则用例白跑）
    badged = {str(b[0]).split(".")[0] for b in boxes if "." in str(b[0])
              and str(b[0]).startswith("g") and str(b[0]).split(".")[1] in ("tl", "tr")}
    assert badged, "本行没有角标盒，用例失效"
    assert badged != {"g0", "g1", "g2", "g3"}, "四格全有角标，混排断言失效"


def t_zero_and_dirty_filtered():
    """零值/负值/非数字不进背包（显式判空，不用 or 兜底）。"""
    p = {"bag": {"凝血散": 0, "回灵丹": -3, "聚气散": 1, "怪东西": "x"}}
    names = [n for n, _ in bagview.sorted_items(p)]
    assert names == ["聚气散"], names


TESTS = [t_pills_first, t_no_truncation, t_pill_name_unchanged,
         t_tier_badge_matches_tier, t_lock_corner, t_corner_geometry_inside_cell,
         t_row_baseline_aligned, t_zero_and_dirty_filtered]

if __name__ == "__main__":
    ok = 0
    for fn in TESTS:
        try:
            fn(); ok += 1; print("PASS", fn.__name__)
        except Exception as e:
            print("FAIL", fn.__name__, type(e).__name__, str(e)[:220])
    print(f"\n{ok}/{len(TESTS)} passed")
    sys.exit(0 if ok == len(TESTS) else 1)
