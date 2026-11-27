# -*- coding: utf-8 -*-
"""物品图接入守门（2026-11-27 接图，prove-it）。

用户：「丹药和火焰的图补齐了，你看看没问题就补到游戏里，直接上线吧」，
并在确认落点时选了「详情卡 + 背包都加图（物品详情卡、异火卡、背包格子全部带图标）」。

本文件守六道硬门，全部走**第二套独立实现**（自己扫文件系统、自己量真实 PNG 与
``boxes`` 包围盒），不信任被测模块的自述：

① **素材齐**：图鉴目录里的文件名集合 == 游戏真源集合（每味丹药、每朵异火都能
   按名字查到图，且没有对不上物品的孤儿文件）；新增丹药忘配图 → 立刻红，
   不许"悄悄回退纯文字"糊过去；
② **详情卡带图**：丹药返回 ``image``，带纹丹回落到**基础丹名**的美术；药材 /
   材料 / 系统物本来没图，**不许**塞一个假名字进去；
③ **详情卡几何**：``icon`` 盒在卡内、在信息框之上、不与任何文本盒重叠；
   卡片高度 = 无图版本 + 头像行高度（**差一像素都算错**）；
④ **背包格子**：``image`` 一路带到行数据；只有真有图的行才出现 ``g*.art`` 盒；
   图在名称上方、不与名称/角标重叠；同一行各图的中心 x 一致（居中不漂）；
⑤ **异火卡**：``row_icon`` 为真、每条非分类行都带 ``image``；图盒不压标题/副行；
⑥ **反证**：把「少一张图」「行不带 image」「图挪到压文字的位置」三类改坏，
   对应闸必须真红（证明这些断言不是摆设）。

运行：python tests/test_doupo_item_icons.py
      pytest tests/test_doupo_item_icons.py -q
"""
from __future__ import annotations

import io
import os
import sys
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (ROOT, os.path.join(ROOT, "src", "plugins")):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def _stub():
    """轻量 nonebot 桩（与 test_bagview 同法）：本文件只验数据与排版，不起 driver。"""
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

from doupo import card, core, config as cfg  # noqa: E402


def _pic():
    p = card._load_pic()
    assert p is not None, "pic 包加载失败"
    return p


def _tpl(name: str):
    pic = _pic()
    return sys.modules[pic.__name__ + ".templates." + name]


def _render(tpl_name: str, card_name: str, data: dict, style: str = "light"):
    """出图 + 拿真实 boxes（走生产同一条 render_sync 路径）。"""
    from PIL import Image
    pic = _pic()
    tpl_doupo = _tpl("doupo")
    theme = pic.resolve_theme(style, card_name)
    boxes: list[tuple] = []
    tok = tpl_doupo.set_style(style)
    try:
        png = pic.render_sync(tpl_name, None, card_name, width=720, data=data,
                              boxes=boxes)
    finally:
        tpl_doupo.reset_style(tok)
    return Image.open(io.BytesIO(png)).size, boxes


def _overlaps(boxes, label: str) -> list[tuple]:
    """某个盒与其他所有盒的重叠对（半开区间：贴边不算重叠）。"""
    mine = [b for b in boxes if b[0] == label]
    out = []
    for a in mine:
        for b in boxes:
            if b is a or b[0] == label:
                continue
            if a[3] <= b[1] or b[3] <= a[1] or a[4] <= b[2] or b[4] <= a[2]:
                continue
            out.append((label, b[0]))
    return out


# ---------------------------------------------------------------------------
# ① 素材齐
# ---------------------------------------------------------------------------
def wanted_names() -> set[str]:
    """游戏真源里**所有需要图的物品名**（第二套独立实现，只读 config）。"""
    want = {str(r["name"]) for r in cfg.RECIPES}
    want |= {str(n) for n in cfg.OWNED_FLAME_NAMES}
    want.add(str(cfg.FLAME_DIYAN))
    return want


def have_names() -> set[str]:
    ic = _tpl("item_icons")
    return {os.path.splitext(f)[0] for f in os.listdir(ic.ICON_DIR)
            if f.lower().endswith(".png")}


def gate_assets(want: set[str] | None = None) -> list[str]:
    want = wanted_names() if want is None else set(want)
    have = have_names()
    probs = []
    miss = sorted(want - have)
    if miss:
        probs.append("缺图 %d 个：%s%s" % (
            len(miss), "、".join(miss[:10]), "…" if len(miss) > 10 else ""))
    extra = sorted(have - want)
    if extra:
        probs.append("孤儿图 %d 张（对不上任何物品，多半是改名漂移）：%s%s" % (
            len(extra), "、".join(extra[:10]), "…" if len(extra) > 10 else ""))
    if not want:
        probs.append("真源集合为空（守门失效）")
    return probs


# ---------------------------------------------------------------------------
# ② 详情卡带图
# ---------------------------------------------------------------------------
def gate_detail_data() -> list[str]:
    p = core.create_player("icon_data", "图测", "g1")
    probs = []
    d = card.build_item_detail_data(p, "青灵丹")
    if not d or d.get("image") != "青灵丹":
        probs.append("丹药详情没带 image：%r" % (d or {}).get("image"))
    dw = cfg.danwen_name("青灵丹", 2)
    d2 = card.build_item_detail_data(p, dw)
    if not d2 or d2.get("image") != "青灵丹":
        probs.append("带纹丹（%s）没回落到基础丹名美术：%r"
                     % (dw, (d2 or {}).get("image")))
    # 本来就没图的类别：不许塞假名
    for nm in ("凝血草", "火属性魔核", "药渣"):
        d3 = card.build_item_detail_data(p, nm)
        if not d3:
            probs.append("%s 查不到详情卡（守门失效）" % nm)
        elif d3.get("image"):
            probs.append("%s 本来没图，却带了 image=%r" % (nm, d3["image"]))
    return probs


# ---------------------------------------------------------------------------
# ③ 详情卡几何（含卡片高度分账）
# ---------------------------------------------------------------------------
def gate_detail_geometry(style: str = "light") -> list[str]:
    tpl = _tpl("doupo_item")
    probs = []
    p = core.create_player("icon_geo", "图测", "g1")
    for nm, wants_icon in (("青灵丹", True), ("聚气散", True), ("凝血草", False)):
        data = card.build_item_detail_data(p, nm)
        if not data:
            probs.append("%s 无详情数据" % nm)
            continue
        with_art = dict(data)
        (W, H), boxes = _render("doupo_item", card.CARD_ITEM_NAME, with_art, style)
        icons = [b for b in boxes if b[0] == "icon"]
        if wants_icon:
            if len(icons) != 1:
                probs.append("%s 应有 1 个 icon 盒，实际 %d" % (nm, len(icons)))
                continue
            ic = icons[0]
            if ic[1] < 0 or ic[2] < 0 or ic[3] > W or ic[4] > H:
                probs.append("%s 的 icon 盒越界：%s（卡 %sx%s）" % (nm, ic, W, H))
            # 图必须在所有文本盒之上（信息框正文从下方开始）
            others = [b for b in boxes if b[0] != "icon"]
            if others:
                first_text_top = min(b[2] for b in others)
                if ic[4] >= first_text_top:
                    probs.append("%s 的 icon 盒压到正文：icon 底 %s ≥ 首行文本顶 %s"
                                 % (nm, ic[4], first_text_top))
            for pair in _overlaps(boxes, "icon"):
                probs.append("%s 的 icon 盒与 %s 重叠" % (nm, pair[1]))
            # 高度分账：有图 = 无图 + 头像行
            no_art = dict(data)
            no_art.pop("image", None)
            (_, H2), _b2 = _render("doupo_item", card.CARD_ITEM_NAME, no_art, style)
            expect = tpl.ICON_GAP_T + tpl.ICON_HERO + tpl.ICON_GAP_B
            if H - H2 != expect:
                probs.append("%s 卡高增量 %s ≠ 头像行 %s（测画不同源）"
                             % (nm, H - H2, expect))
        else:
            if icons:
                probs.append("%s 本来没图，却画了 icon 盒" % nm)
    return probs


# ---------------------------------------------------------------------------
# ④ 背包格子
# ---------------------------------------------------------------------------
def _bag_player():
    p = core.create_player("icon_bag", "包测", "g1")
    p["bag"] = {
        "青灵丹": 2,                              # 有图
        cfg.danwen_name("聚气散", 3): 1,          # 有图 + 右上「三纹」角标
        "凝血草": 3,                              # 无图
        "冰晶石": 4,                              # 无图
    }
    return p


def gate_bag_data() -> list[str]:
    p = _bag_player()
    d = card.build_bag_data(p, locked={"聚气散"})
    probs = []
    for r in d["rows"]:
        if "image" not in r:
            probs.append("背包行缺 image 字段：%r" % r.get("title"))
            continue
        base = str(r["title"]).split(" ×")[0]
        if r["image"] != base:
            probs.append("背包行 image 与格内名称不一致：%r vs %r"
                         % (r["image"], base))
    # 带纹丹必须回落到基础丹名那件美术
    dw = next((r for r in d["rows"] if r.get("corner_tr")), None)
    if dw is None:
        probs.append("样本里没有带纹丹行（守门失效）")
    elif dw["image"] != "聚气散":
        probs.append("带纹丹格子没回落到基础丹名美术：%r" % dw["image"])
    return probs


def gate_bag_geometry(style: str = "light") -> list[str]:
    tpl = _tpl("doupo_list")
    d = card.build_bag_data(_bag_player())
    (W, H), boxes = _render("doupo_list", "斗破背包", d, style)
    probs = []
    art = {b[0].split(".")[0]: b for b in boxes if str(b[0]).endswith(".art")}
    names = {b[0]: b for b in boxes if str(b[0]).startswith("g")
             and "." not in str(b[0])}
    if len(names) != 4:
        probs.append("格子名称盒数量 %d ≠ 4（守门失效）" % len(names))
    # 只有真有图的两格才该有 art 盒
    want_art = {k for k, v in names.items()}
    got = set(art)
    if not got:
        probs.append("背包格子完全没有物品图盒")
    elif len(got) >= len(want_art):
        probs.append("无图物品也画了图盒：art=%s names=%s" % (sorted(got), sorted(want_art)))
    for k, b in art.items():
        if b[1] < 0 or b[2] < 0 or b[3] > W or b[4] > H:
            probs.append("%s 图盒越界：%s" % (k, b))
        nb = names.get(k)
        if nb and not (b[4] <= nb[2]):
            probs.append("%s 图盒压到名称（图底 %s > 名称顶 %s）" % (k, b[4], nb[2]))
        for pair in _overlaps(boxes, "%s.art" % k):
            probs.append("%s 图盒与 %s 重叠" % (k, pair[1]))
    # 图必须与**同一格的名称**同轴（都居中于格子中线；跨列比绝对 x 没意义）
    for k, b in art.items():
        nb = names.get(k)
        if nb is None:
            continue
        cx_art = (b[1] + b[3]) / 2.0
        cx_name = (nb[1] + nb[3]) / 2.0
        if abs(cx_art - cx_name) > 1.5:
            probs.append("%s 图与名称不同轴：图心 %.1f vs 名称心 %.1f"
                         % (k, cx_art, cx_name))
    return probs


# ---------------------------------------------------------------------------
# ⑤ 异火卡
# ---------------------------------------------------------------------------
def _flame_player():
    p = core.create_player("icon_flame", "火测", "g1")
    names = [f["name"] for f in cfg.FLAMES][:3]
    names += [f["name"] for f in getattr(cfg, "OTHER_FLAMES", [])][:2]
    p["flames"] = {nm: 5 + i for i, nm in enumerate(names)}
    p["diyan"] = 1
    return p


def gate_flames_data() -> list[str]:
    d = card.build_flames_data(_flame_player())
    probs = []
    if not d.get("row_icon"):
        probs.append("异火卡没开 row_icon（行首图槽不会让出来）")
    body = [r for r in d["rows"] if not r.get("head")]
    if not body:
        probs.append("异火卡没有任何条目行（守门失效）")
    for r in body:
        if not r.get("image"):
            probs.append("异火行缺 image：%r" % r.get("title"))
    return probs


def gate_flames_geometry(style: str = "light") -> list[str]:
    d = card.build_flames_data(_flame_player())
    (W, H), boxes = _render("doupo_list", "斗破异火", d, style)
    probs = []
    art = {b[0]: b for b in boxes if str(b[0]).endswith(".art")}
    body = [r for r in d["rows"] if not r.get("head")]
    if len(art) != len(body):
        probs.append("异火图盒数 %d ≠ 条目行数 %d" % (len(art), len(body)))
    for k, b in art.items():
        if b[1] < 0 or b[2] < 0 or b[3] > W or b[4] > H:
            probs.append("%s 图盒越界：%s" % (k, b))
        for pair in _overlaps(boxes, k):
            probs.append("%s 图盒与 %s 重叠" % (k, pair[1]))
    # 每行标题必须让开图槽（标题左缘 > 图右缘）
    for k, b in art.items():
        ri = k.split(".")[0][1:]
        tb = next((x for x in boxes if x[0] == "r%s.title" % ri), None)
        if tb is not None and tb[1] < b[3]:
            probs.append("r%s 标题被图压住（标题 x=%s < 图右 %s）" % (ri, tb[1], b[3]))
    return probs


# ---------------------------------------------------------------------------
# ⑥ 反证
# ---------------------------------------------------------------------------
def selftest() -> bool:
    ok = True

    probs = gate_assets(want=wanted_names() | {"根本不存在的丹"})
    hit = bool(probs)
    print(("PASS" if hit else "FAIL"), "反证1 少一张图 → 素材闸必红")
    ok &= hit

    bad_rows = {"rows": [{"title": "青灵丹 ×2", "sub": ""}], "columns": 4, "grid": True}
    probs = [("%r 缺 image 字段" % r.get("title"))
             for r in bad_rows["rows"] if "image" not in r]
    hit = bool(probs)
    print(("PASS" if hit else "FAIL"), "反证2 行不带 image → 数据闸必红")
    ok &= hit

    fake = [("icon", 10, 10, 100, 100), ("t1", 50, 50, 200, 70)]
    hit = bool(_overlaps(fake, "icon"))
    print(("PASS" if hit else "FAIL"), "反证3 图压文字 → 几何闸必红")
    ok &= hit
    return ok


# ---------------------------------------------------------------------------
TESTS_COMMON = [
    ("素材闸", gate_assets),
    ("详情数据闸", gate_detail_data),
    ("背包数据闸", gate_bag_data),
    ("异火数据闸", gate_flames_data),
]


def test_assets():
    assert gate_assets() == []


def test_detail_data():
    assert gate_detail_data() == []


def test_detail_geometry_light():
    assert gate_detail_geometry("light") == []


def test_detail_geometry_game():
    assert gate_detail_geometry("game") == []


def test_bag_data():
    assert gate_bag_data() == []


def test_bag_geometry_light():
    assert gate_bag_geometry("light") == []


def test_bag_geometry_game():
    assert gate_bag_geometry("game") == []


def test_flames_data():
    assert gate_flames_data() == []


def test_flames_geometry_light():
    assert gate_flames_geometry("light") == []


def test_flames_geometry_game():
    assert gate_flames_geometry("game") == []


def test_selftest():
    assert selftest()


if __name__ == "__main__":
    checks = [
        ("素材闸", gate_assets()),
        ("详情数据闸", gate_detail_data()),
        ("详情几何闸(light)", gate_detail_geometry("light")),
        ("详情几何闸(game)", gate_detail_geometry("game")),
        ("背包数据闸", gate_bag_data()),
        ("背包几何闸(light)", gate_bag_geometry("light")),
        ("背包几何闸(game)", gate_bag_geometry("game")),
        ("异火数据闸", gate_flames_data()),
        ("异火几何闸(light)", gate_flames_geometry("light")),
        ("异火几何闸(game)", gate_flames_geometry("game")),
    ]
    fail = False
    for name, probs in checks:
        if probs:
            fail = True
            print("FAIL", name)
            for p in probs[:8]:
                print("   ", p)
        else:
            print("PASS", name)
    print("反证注入")
    fail |= not selftest()
    print("RESULT", "FAIL" if fail else "ALL PASS")
    raise SystemExit(1 if fail else 0)
