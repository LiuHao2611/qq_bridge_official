# -*- coding: utf-8 -*-
"""宗门卡验收（零依赖、exit-code 可验证）。

运行：
    python src/plugins/doupo/test_faction_card.py     # 自带 runner，不依赖 pytest

覆盖：
  1. 模板注册（doupo_faction + 中文显示名）；
  2. build_faction_data 与 core/factions 同源（名称/据点/卡池/统计/可转宗门）；
  3. 出图：PNG、宽 720、高度 == _measure（底部不被裁）、非空白；
  4. **check_boxes 自检**：文本包围盒溢出 0 / 重叠 0 / 相邻间距 0；
  5. 卡片外 12px 页边无墨迹（不越界出血）。

产物：tmp/doupo_faction_cards/*.png，供人工视觉核对。
"""
from __future__ import annotations

import asyncio
import importlib
import os
import sys
import tempfile
import types

PLUGINS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO_ROOT = os.path.dirname(os.path.dirname(PLUGINS_DIR))
for _p in (REPO_ROOT, PLUGINS_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

OUT_DIR = os.path.join(REPO_ROOT, "tmp", "doupo_faction_cards")
os.makedirs(OUT_DIR, exist_ok=True)
_CACHE_TMP = tempfile.mkdtemp(prefix="doupo_faction_")


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
    for name in ("nonebot.adapters", "nonebot.adapters.onebot",
                 "nonebot.adapters.onebot.v11"):
        sys.modules[name] = types.ModuleType(name)
    v11 = sys.modules["nonebot.adapters.onebot.v11"]
    v11.MessageEvent = object
    v11.MessageSegment = object
    v11.Message = object


_stub_nonebot()

from doupo import card, core, config as cfg  # noqa: E402
from doupo import factions as fac  # noqa: E402


def _pic():
    pic = card._load_pic()
    assert pic is not None, "pic 包加载失败（宗门卡不可用）"
    pc = pic.pic_cache
    import pathlib
    pc.dir = pathlib.Path(_CACHE_TMP) / "pic_cache"
    pc._loaded = False
    pc._index = {}
    return pic


def _tpl():
    _pic()          # 确保独立 pic 包已注册到 sys.modules（否则子模块 import 失败）
    return importlib.import_module(card._PKG_NAME + ".templates.doupo_faction")


def _theme():
    _pic()
    return importlib.import_module(card._PKG_NAME + ".theme").get("light")


def _player(lv: int = 40, fid: str = "yunlanzong"):
    p = core.create_player("10001", "测试", "20001")
    p["lv"] = lv
    p["faction"] = fid
    p["faction_name"] = fac.FACTIONS[fid]["name"]
    p["loadout"] = []
    return p


# ---------------------------------------------------------------------------
# 1. 注册 + 数据同源
# ---------------------------------------------------------------------------
def test_template_registered():
    pic = _pic()
    assert "doupo_faction" in pic.list_templates(), "宗门卡模板未注册"
    assert pic.TEMPLATE_LABELS.get("doupo_faction") == "斗破宗门卡"
    assert "doupo_skills" in pic.list_templates(), "斗技卡必须保留"


def test_build_faction_data_matches_source():
    p = _player(40)
    d = card.build_faction_data(p)
    fd = fac.FACTIONS["yunlanzong"]
    assert d["name"] == fd["name"] == "云岚宗"
    assert d["base"] == fd["base"]
    assert d["pool"] == "新手池"
    assert d["npcs"] == fd["npcs"]
    assert d["flames"] == fd["flames"]
    # 斗技统计与本门池子一致（三阶之和 == 总门数）
    assert d["total_skills"] == len(core.skill_pool_sorted(p))
    assert sum(d["tier_counts"].values()) == d["total_skills"]
    # 可转宗门：只列已开放卡池，且每组都有门数
    assert d["switch_cost"] == core.faction_switch_cost(p) > 0
    open_cn = {"starter": "新手池", "advanced": "进阶池", "hidden": "隐世池"}
    want = [open_cn[x] for x in core.faction_pools_open(p)]
    assert [g["pool"] for g in d["groups"]] == want
    for g in d["groups"]:
        assert g["names"] and all("门" in n for n in g["names"])


def test_high_level_opens_all_three_pools():
    d = card.build_faction_data(_player(95))
    assert [g["pool"] for g in d["groups"]] == ["新手池", "进阶池", "隐世池"]


# ---------------------------------------------------------------------------
# 2. check_boxes：溢出 / 重叠 / 间距
# ---------------------------------------------------------------------------
def _overlap_2d(a, b) -> bool:
    return not (a[2] <= b[0] or b[2] <= a[0] or a[3] <= b[1] or b[3] <= a[1])


def _check_boxes(bounds, boxes, min_gap: int = 2) -> list[str]:
    """与 card-design `check_boxes` 同规则：溢出 / 两方向重叠 / 同行间距过小。"""
    bx0, by0, bx1, by1 = bounds
    problems: list[str] = []
    items = list(boxes)
    for label, x0, y0, x1, y1 in items:
        if x0 < bx0 - 1 or y0 < by0 - 1 or x1 > bx1 + 1 or y1 > by1 + 1:
            problems.append("[溢出] %s 越界: (%d,%d,%d,%d) bounds=%s"
                            % (label, x0, y0, x1, y1, bounds))
    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            la, a = items[i][0], items[i][1:]
            lb, b = items[j][0], items[j][1:]
            if _overlap_2d(a, b):
                problems.append("[重叠] %s <-> %s: %s vs %s" % (la, lb, a, b))
            if a[1] < b[3] and b[1] < a[3]:
                gap_x = b[0] - a[2] if a[0] <= b[0] else a[0] - b[2]
                if 0 <= gap_x < min_gap:
                    problems.append("[间距] %s <-> %s 水平间距 %dpx" % (la, lb, gap_x))
    return problems


def test_layout_passes_check_boxes():
    tpl, t = _tpl(), _theme()
    for lv in (40, 95):
        data = card.build_faction_data(_player(lv))
        boxes: list[tuple] = []
        c = tpl.build(t, data=data, width=720, boxes=boxes)
        assert len(boxes) > 20, "没收集到文本包围盒，自检会假通过"
        h = c.height
        problems = _check_boxes((12, 12, 720 - 12, h - 12), boxes)
        assert not problems, "宗门卡触线：\n" + "\n".join(problems[:8])


# ---------------------------------------------------------------------------
# 3. 出图：尺寸 / 高度 / 非空白 / 页边无墨迹
# ---------------------------------------------------------------------------
def test_render_real_card_and_preview():
    p = _player(40)
    png = asyncio.run(card.render_faction(p))
    assert png and png[:8] == b"\x89PNG\r\n\x1a\n" and len(png) > 2000
    with open(os.path.join(OUT_DIR, "real_yunlanzong.png"), "wb") as f:
        f.write(png)

    from PIL import Image
    import io
    img = Image.open(io.BytesIO(png)).convert("RGB")
    assert img.width == 720, "卡片宽度异常：%d" % img.width
    data = card.build_faction_data(p)
    assert img.height == _tpl()._measure(data, 720), (
        "卡片高度 != _measure（底部会裁/留白）：%d vs %d"
        % (img.height, _tpl()._measure(data, 720)))

    # 非空白
    small = img.resize((80, 80)).tobytes()
    colors = len({small[i:i + 3] for i in range(0, len(small), 3)})
    assert colors > 20, "疑似空白卡片"

    # 页边 12px 无墨迹（不越界出血）
    w, h = img.size
    px = img.load()

    def dark_in(x0, y0, x1, y1):
        for yy in range(y0, y1):
            for xx in range(x0, x1):
                r, g, b = px[xx, yy]
                if r + g + b < 520:
                    return (xx, yy, (r, g, b))
        return None

    hits = [dark_in(0, 0, 12, h), dark_in(w - 12, 0, w, h),
            dark_in(0, 0, w, 12), dark_in(0, h - 12, w, h)]
    assert not any(hits), "页边出现墨迹（越界/裁切）：%s" % [x for x in hits if x]

    # 三档境界各出一张（视觉核对用）
    for lv in (40, 95):
        png2 = asyncio.run(card.render_faction(_player(lv)))
        with open(os.path.join(OUT_DIR, "real_lv%d.png" % lv), "wb") as f:
            f.write(png2)


# ---------------------------------------------------------------------------
# 4. 可转宗门区结构门禁：卡池块 + 两列网格 + 徽章右锚（防回退流式断行）
# ---------------------------------------------------------------------------
def _grid_col_w(width=720):
    inner = (width - 28 * 2) - 14 * 2        # content_w - POOL_PAD*2
    return (inner - 16) // 2                 # // GRID_COLS, GRID_GAP_X


BADGE_PAD = 10          # 与 doupo_faction.BADGE_PAD 同源（徽章文字右边界内缩）


def _switch_grid_problems(boxes, col_w: int) -> list[str]:
    """可转宗门区布局契约：每池有块头 / 宗名两列左对齐 / 徽章右锚到列右边界。

    这是 2026-11-16「底部文本太随意」返工后加的检查：旧写法是把宗门用
    「 · 」串成一条流式文本，门数混在名字里、断行随机、无视觉层级。任何
    回退到那种写法（缺块头 / 宗名起点不成两列 / 徽章不右锚）都在这里被拦。
    """
    problems: list[str] = []
    by_label = {b[0]: b for b in boxes}
    pools = [b for b in boxes if b[0].startswith("pool_")]
    sects = [b for b in boxes if b[0].startswith("sect_")]
    badges = [b for b in boxes if b[0].startswith("badge_")]
    if not pools:
        problems.append("[结构] 缺少卡池块头 pool_*，回退到无层级流式文本")
    if not sects or not badges:
        problems.append("[结构] 宗名/门数未拆分成两列网格（sect_*/badge_* 缺失）")
        return problems
    if len(badges) != len(sects):
        problems.append("[结构] 门数徽章数(%d) != 宗名数(%d)，有宗门没拆出门数"
                        % (len(badges), len(sects)))
    lefts = sorted({b[1] for b in sects})
    if len(lefts) != 2:
        problems.append("[对齐] 宗名起点不是两列: %s" % lefts)
    for b in badges:
        parts = b[0].split("_")            # badge_<gi>_<ri>_<ci>
        key = "sect_" + "_".join(parts[1:])
        s = by_label.get(key)
        if s is None:
            problems.append("[对齐] 徽章 %s 找不到同行宗名 %s" % (b[0], key))
            continue
        # 徽章文字盒是「框 - BADGE_PAD 内边距」（rec 记的是文字盒，不是徽章框），
        # 故右锚判据 = 列宽 - BADGE_PAD，与徽章数字宽度无关。
        want = col_w - BADGE_PAD
        if b[3] - s[1] != want:
            problems.append("[对齐] 徽章 %s 未右锚列右边界: badge_x1-sect_x0=%d want=%d"
                            % (b[0], b[3] - s[1], want))
    return problems


def test_switch_pools_structured_grid():
    tpl, t = _tpl(), _theme()
    data = card.build_faction_data(_player(95))
    boxes: list[tuple] = []
    tpl.build(t, data=data, width=720, boxes=boxes)
    problems = _switch_grid_problems(boxes, _grid_col_w())
    assert not problems, "可转宗门区布局触线：\n" + "\n".join(problems[:8])
    # 源码契约：禁回退旧的 " · " 流式断行
    import inspect
    src = inspect.getsource(tpl)
    assert '_list_lines(g.get("names"), " · "' not in src, (
        "回退到旧的 ' · ' 流式断行写法")


def _npc_gate_problems(boxes, npcs) -> list[str]:
    """代表人物区契约：每人姓名独立成盒（胶囊内不拆断）、有身份的紧跟身份。

    2026-11-16「代表人物也调整」返工后加：旧写法把人用「、」串成裸文本，
    长身份会被按宽度硬拆断。回退表现 = 某个 npc 的姓名不再单独成盒、或
    姓名盒数 < npc 数（被合并成一行流式文本）。
    """
    problems: list[str] = []
    name_boxes = [b for b in boxes if b[0].startswith("npc_") and b[0].endswith("_name")]
    want = len([n for n in (npcs or []) if str(n or "").strip()])
    if len(name_boxes) != want:
        problems.append("[胶囊] 姓名盒数(%d) != 人物数(%d)，疑似回退流式文本"
                        % (len(name_boxes), want))
    # 每个姓名胶囊起点必须在安全区内（check_boxes 已查全局溢出，这里再锁语义）
    for b in name_boxes:
        if b[1] < 12 - 1:
            problems.append("[溢出] 胶囊 %s 起点越界 x0=%d" % (b[0], b[1]))
    return problems


def test_npc_capsules_structured():
    tpl, t = _tpl(), _theme()
    data = card.build_faction_data(_player(95))
    boxes: list[tuple] = []
    tpl.build(t, data=data, width=720, boxes=boxes)
    problems = _npc_gate_problems(boxes, data.get("npcs"))
    assert not problems, "代表人物区布局触线：\n" + "\n".join(problems[:8])
    import inspect
    src = inspect.getsource(tpl)
    assert '_list_lines(data.get("npcs"), "、"' not in src, (
        "代表人物回退到旧的 '、' 流式断行写法（长身份会被拆断）")
    assert "_npc_rows" in src and "NPC_PILL_H" in src, "人物胶囊布局被移除"


def test_switch_grid_gate_catches_regressions():
    """反例注入：把这次真实犯过的错放回去，门禁必须报错（从没报过错的闸等于没加）。"""
    # 真值：正常布局 0 问题
    tpl, t = _tpl(), _theme()
    data = card.build_faction_data(_player(95))
    boxes: list[tuple] = []
    tpl.build(t, data=data, width=720, boxes=boxes)
    assert not _switch_grid_problems(boxes, _grid_col_w())

    col_w = _grid_col_w()
    # C1 回退流式：删掉块头 → 必拦
    no_pools = [b for b in boxes if not b[0].startswith("pool_")]
    assert any("[结构]" in p for p in _switch_grid_problems(no_pools, col_w))
    # C2 徽章不右锚：把徽章整体左移 5px → 必拦
    drifted = [(b[0], b[1] - 5, b[2], b[3] - 5) if b[0].startswith("badge_") else b
               for b in boxes]
    assert any("[对齐]" in p for p in _switch_grid_problems(drifted, col_w))
    # C3 宗名塌成单列：把所有宗名起点改成同一个 x → 必拦
    first_x = min(b[1] for b in boxes if b[0].startswith("sect_"))
    collapsed = [(b[0], first_x, b[2], first_x + (b[3] - b[1]))
                 if b[0].startswith("sect_") else b for b in boxes]
    assert any("[对齐]" in p for p in _switch_grid_problems(collapsed, col_w))
    # C4 人物胶囊回退：删掉一个姓名盒（模拟流式合并/拆断）→ 必拦
    npcs = data.get("npcs")
    name_boxes = [b for b in boxes if b[0].startswith("npc_") and b[0].endswith("_name")]
    assert not _npc_gate_problems(boxes, npcs)
    merged = [b for b in boxes if b is not name_boxes[0]]
    assert any("[胶囊]" in p for p in _npc_gate_problems(merged, npcs))


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
            print("PASS  %s" % name)
        except Exception as e:  # noqa: BLE001
            fail.append((name, e))
            print("FAIL  %s  ->  %s: %s" % (name, type(e).__name__, e))
    print("=" * 70)
    print("FACTION_CARD_TOTAL=%d PASSED=%d FAILED=%d" % (len(fns), ok, len(fail)))
    return 0 if not fail else 1


if __name__ == "__main__":
    sys.exit(_run_all())
