# -*- coding: utf-8 -*-
"""斗破人物卡渲染单测（零依赖、exit-code 可验证）。

运行：
    python src/plugins/doupo/test_card.py     # 自带 runner，不依赖 pytest
    pytest src/plugins/doupo/test_card.py

导入前桩掉 nonebot，使插件可在无 QQ 环境中安全加载（只校验纯逻辑）。
"""
from __future__ import annotations

import asyncio
import os
import sys
import types

# ---- 让 doupo 包与 src.* 都可被导入 ----
# PLUGINS_DIR 供 `from doupo import ...`；REPO_ROOT 供插件 __init__.py 里的
# `from src.game_mode import ...`。裸跑脚本时 CWD 不保证是仓库根，两者缺一即 ModuleNotFoundError。
PLUGINS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
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

from doupo import card
from doupo import core, config as cfg
from doupo import commands


def _player(lv=1, **kw):
    p = core.create_player("10001", "测试", "20001")
    p["lv"] = lv
    p["exp"] = 0
    p["hp"] = core.stats(p)["体力"]
    p["mp"] = core.mp_max(p)
    p.update(kw)
    return p


def test_build_data_maps_player():
    p = _player(1)
    d = card.build_data(p)
    assert d["name"] == "测试"
    assert d["realm"] == cfg.full_realm(1)            # 斗之气一段（原著起步）
    assert set(["力量", "敏捷", "斗气", "防御", "机缘", "体力"]).issubset(d["stats"])
    assert d["weapon_stats"] and "hp_max" in d and "mp_max" in d
    assert d["can_break"] is False and d["realm_max"] is False


def test_build_data_can_break_flag():
    """修为满时 build_data 应标 can_break（卡片据此转暖金条）。"""
    lv = 5
    p = _player(lv)
    p["exp"] = cfg.exp_need(lv) + 10                 # 修为溢出 -> 可突破
    d = card.build_data(p)
    assert d["can_break"] is True
    assert d["realm"] == cfg.full_realm(lv)


def test_render_panel_produces_png():
    """正常环境应产出合法 PNG（机器验收：PNG 头 + 体积）。"""
    p = _player(3)
    p["weapon"] = 3
    p["alch"] = 1
    png = asyncio.run(card.render_panel(p))
    assert png and png[:8] == b"\x89PNG\r\n\x1a\n" and len(png) > 2000


def test_render_panel_fallback_when_pic_missing():
    """pic 包不可用（如字体/依赖缺失）时必须优雅降级为 None，绝不抛异常。"""
    saved = card._load_pic
    card._load_pic = lambda: None
    try:
        # render_panel 是 async def，必须用 asyncio.run 取真实返回值（coroutine 永不为 None）
        assert asyncio.run(card.render_panel({"lv": 1, "name": "x"})) is None
    finally:
        card._load_pic = saved


def test_build_menu_data_maps_groups():
    """菜单卡数据与命令层 MENU_GROUPS 同源，绝不与纯文本分流。"""
    d = card.build_menu_data()
    assert d["title"] == "斗破菜单"
    assert len(d["groups"]) == len(commands.MENU_GROUPS)
    total = sum(len(g["rows"]) for g in d["groups"])
    assert total == commands.menu_total()
    # 第一行分组第二条应为「入道」口令
    assert commands.MENU_GROUPS[0][1][1][0] == d["groups"][0]["rows"][1][0]


def test_render_menu_produces_png():
    """菜单卡正常环境应产出合法 PNG（机器验收：PNG 头 + 体积）。"""
    png = asyncio.run(card.render_menu())
    assert png and png[:8] == b"\x89PNG\r\n\x1a\n" and len(png) > 2000


def test_build_and_render_list_cards():
    """斗技/异火/宝库/背包/排行 五张列表卡：数据同源 + 出图合法 PNG。"""
    p = _player(6)
    p["weapon"] = 3
    p["alch"] = 2
    p["skills"] = {1: 1, 3: 3, 7: 5, 9: 9}
    p["flames"] = {0: 4, 1: 0, 2: 4, 3: 4, 4: 4}
    p["bag"] = {"回灵丹": 3, "血灵草": 2, "炎星石": 1, "紫叶兰": 1}
    # 宝库/异火依赖金币与药渣，给足以渲染价格与收服状态
    p["gold"] = 5200
    p["slag"] = 18

    # 数据层：清单与命令层纯文本源一致（同源，避免图文分流）
    d_sk = card.build_skills_data(p)
    d_fl = card.build_flames_data(p)
    d_sh = card.build_shop_data(p)
    d_bg = card.build_bag_data(p)
    assert d_sk["title"] == "斗技" and len(d_sk["rows"]) >= 1
    # 2026-11-16：异火卡首行加「本宗传承异火」+「可自行收服」提示，其余仍是 5 种养成异火
    assert d_fl["title"] == "异火" and len(d_fl["rows"]) >= len(cfg.FLAMES)
    assert any("本宗传承" in str(r.get("title", "")) for r in d_fl["rows"]), \
        "异火卡缺本宗传承行"
    # 宝库卡片直接消费 core.shop_list()（与纯文本兜底同源），以它为准而非 cfg.HERBS 字面量
    assert d_sh["title"] == "随身商店" and len(d_sh["rows"]) == len(core.shop_list())
    assert d_bg["title"] == "包裹" and "24 格" in d_bg["subtitle"]

    # 渲染层：正常环境应产出合法 PNG（机器验收）
    pngs = {
        "skills": asyncio.run(card.render_skills(p)),
        "flames": asyncio.run(card.render_flames(p)),
        "shop": asyncio.run(card.render_shop(p)),
        "bag": asyncio.run(card.render_bag(p)),
    }
    for key, png in pngs.items():
        assert png and png[:8] == b"\x89PNG\r\n\x1a\n" and len(png) > 2000, f"{key} 卡片未产出合法 PNG"

    # 排行榜：传入前 N 名玩家（与 core.rank 排序口径一致）
    ranked = core.rank([p, _player(4, name="老二"), _player(9, name="大佬")])[:10]
    png_rank = asyncio.run(card.render_rank(ranked))
    assert png_rank and png_rank[:8] == b"\x89PNG\r\n\x1a\n" and len(png_rank) > 2000


def test_render_rank_empty_is_safe():
    """排行无玩家时也应优雅产出 PNG（空榜提示），不抛异常。"""
    png = asyncio.run(card.render_rank([]))
    assert png and png[:8] == b"\x89PNG\r\n\x1a\n" and len(png) > 500


def test_build_and_render_recipes_card():
    """炼药卡：等级过滤 + 分页 + 配方与纯文本同源 + 出合法 PNG。"""
    p = _player(6)
    p["alch"] = 5
    # 备齐回灵丹材料 → 该丹 ready=True；其余不备 → ready=False
    for k in cfg.RECIPE_BY_NAME["回灵丹"]["need"]:
        core.add_item(p, k, 99)
    d = card.build_recipes_data(p, 1)
    assert d["title"] == "炼药"
    assert "六品炼药师" in d["subtitle"] and "成功率" in d["subtitle"]

    avail = core.available_recipes(p)
    chunk, page, total_pages = core.recipes_page(p, 1)
    assert d["page"] == page == 1
    assert d["total_pages"] == total_pages
    assert d["total_avail"] == len(avail)
    assert d["shown"] == len(chunk), "卡面展示数必须=当页切片数（算的画得下）"

    all_rows = [r for g in d["groups"] for r in g["rows"]]
    assert len(all_rows) == len(chunk), "第一页条数必须与 recipes_page 同源"
    # 分组顺序遵循 cfg.GRADE_ORDER（只登记本页实际存在的品阶）
    grades = [g["grade"] for g in d["groups"]]
    assert grades == [g for g in cfg.GRADE_ORDER
                      if any(r["grade"] == g for r in chunk)], "分组顺序错"
    for g in d["groups"]:
        want = {r["name"] for r in chunk if r["grade"] == g["grade"]}
        assert {r["name"] for r in g["rows"]} == want, "分组 %s 归属错" % g["grade"]
    # 无药方丹药必须标记 buyonly（卡面显示购买价而非空材料行）
    for r in all_rows:
        rec = cfg.RECIPE_BY_NAME[r["name"]]
        assert r["buyonly"] == (not bool(rec.get("need"))), \
            "%s 的 buyonly 标记与原方不符" % r["name"]
    # 出图合法 PNG
    png = asyncio.run(card.render_recipes(p, 1))
    assert png and png[:8] == b"\x89PNG\r\n\x1a\n" and len(png) > 2000


def test_recipes_card_level_filter():
    """等级不够的丹药完全不展示：lv1 不得出现高阶丹，lv103 才见帝品。"""
    low = card.build_recipes_data(_player(1), 1)
    low_names = {r["name"] for g in low["groups"] for r in g["rows"]}
    # 逐页扫全（lv1 可能不足一页）
    low_all = set()
    for pg in range(1, low["total_pages"] + 1):
        d = card.build_recipes_data(_player(1), pg)
        low_all |= {r["name"] for g in d["groups"] for r in g["rows"]}
    assert "帝品雏丹" not in low_all and "雷劫丹" not in low_all, \
        "lv1 不该看到高阶丹药"
    assert all(cfg.RECIPE_BY_NAME[n]["min_lv"] <= 1 for n in low_all), \
        "lv1 卡上出现 min_lv>1 的丹药"
    high = _player(103)
    high_all = set()
    d0 = card.build_recipes_data(high, 1)
    for pg in range(1, d0["total_pages"] + 1):
        d = card.build_recipes_data(high, pg)
        high_all |= {r["name"] for g in d["groups"] for r in g["rows"]}
    assert "帝品雏丹" in high_all, "满级应见帝品丹药"
    assert d0["total_avail"] == len(cfg.RECIPES), "满级应可见全部丹药"


def test_recipes_card_pagination_distinct():
    """分页：不同页是不同丹药集合；越界页码夹取到末页；页码信息自洽。"""
    p = _player(6)
    d1 = card.build_recipes_data(p, 1)
    assert d1["total_pages"] > 1, "lv6 可用丹药应超过一页（否则分页无从验证）"
    n1 = {r["name"] for g in d1["groups"] for r in g["rows"]}
    d2 = card.build_recipes_data(p, 2)
    n2 = {r["name"] for g in d2["groups"] for r in g["rows"]}
    assert n1 and n2 and not (n1 & n2), "相邻两页不应有重复丹药"
    assert d1["shown"] + d2["shown"] <= d1["total_avail"]
    # 越界夹取
    last = card.build_recipes_data(p, 9999)
    assert last["page"] == last["total_pages"]
    assert card.build_recipes_data(p, 0)["page"] == 1


def test_recipes_two_column_boxes_no_overlap():
    """两列布局盒子自检：满页 10 枚（含多味药方 + 长描述）三种宽度零溢出/重叠。

    用 check_doupo_typography.check_boxes（card-design 同款算法）反向设防：
    改模板排版若把 tip 挤出边界、或丹名/材料/功效重叠，这里必红。
    """
    import importlib.util

    pic_dir = os.path.join(REPO_ROOT, "src", "plugins", "qq_bridge", "pic")
    if pic_dir not in sys.path:
        sys.path.insert(0, pic_dir)
    spec = importlib.util.spec_from_file_location(
        "_doupo_typecheck",
        os.path.join(REPO_ROOT, "tools", "check_doupo_typography.py"))
    tc = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tc)
    from pic import theme as pic_theme
    from pic.templates import doupo_recipes as R

    p = _player(103)        # 满级 → 全丹药可见，第一页 10 枚
    d = card.build_recipes_data(p, 1)
    assert d["shown"] == cfg.RECIPES_PER_PAGE, "满级第一页应画满 10 枚"
    t = pic_theme.get("light")
    for w in (560, 720, 900):
        L = R._plan(d, w)
        boxes: list = []
        R.build(t, d, width=w, boxes=boxes)
        bounds = (R.PAD, R.PAD, w - R.PAD, L["h"] - R.PAD)
        probs = tc.check_boxes(bounds, boxes, min_gap=8)
        assert not probs, "炼药卡 w=%d 排版越界/重叠：%s" % (w, probs[:5])


def test_build_and_render_help_card():
    """玩法卡：数据源 HELP_GROUPS 与纯文本同源 + 出合法 PNG。"""
    d = card.build_help_data()
    assert len(d["groups"]) == len(commands.HELP_GROUPS)
    for (name, rows), g in zip(commands.HELP_GROUPS, d["groups"]):
        assert g["name"] == name and list(g["rows"]) == list(rows), \
            "分组 %s 图文不同源" % name
    total = sum(len(g["rows"]) for g in d["groups"])
    assert total == sum(len(rows) for _, rows in commands.HELP_GROUPS)
    png = asyncio.run(card.render_help())
    assert png and png[:8] == b"\x89PNG\r\n\x1a\n" and len(png) > 2000


# ---------------------------------------------------------------------------
# 人物卡内嵌静态头像（2026-11-07 用户要求，禁止退回「另发一张动图」）
# ---------------------------------------------------------------------------
def _dark_pixels(png, box=(28, 96, 124, 192)):
    """数出卡片头像框区域内的深色像素（头发/脸/武器），用于像素级机器验收。"""
    import io
    from PIL import Image

    raw = Image.open(io.BytesIO(png)).convert("RGB").crop(box).tobytes()
    return sum(1 for i in range(0, len(raw), 3)
               if raw[i] < 120 and raw[i + 1] < 120 and raw[i + 2] < 140)


def test_build_data_embeds_avatar():
    """build_data 必须把头像 base64 交给模板（不能只发独立动图）。"""
    d = card.build_data(_player(6))
    import base64

    assert d["lv"] == 6
    assert d["avatar_b64"] and isinstance(d["avatar_b64"], str)
    head = base64.b64decode(d["avatar_b64"])
    assert head[:8] == b"\x89PNG\r\n\x1a\n", "头像必须是静态 PNG，不是 GIF 动图"
    assert d["avatar_caption"] in ("持剑少年", "赤手行者", "蓄力之姿", "降魔之姿")


def test_render_panel_avatar_pixels_are_inside_card():
    """人物卡 PNG 的头像框里必须真的有角色像素，且比无头像版本多得多。

    ⚠️ 两边都走 render_sync（**绕开 pic 磁盘缓存**）。曾经只让 render_panel 走
    缓存链路，结果注入「模板忽略 avatar_b64」时命中了旧缓存 PNG，测试假通过。
    缓存只能证明「以前渲染过」，不能证明「当前代码画得出」。
    """
    p = _player(3)
    png = asyncio.run(card.render_panel(p))
    assert png and png[:8] == b"\x89PNG\r\n\x1a\n", "真实链路必须出 PNG 卡片"

    pic = card._load_pic()
    assert pic is not None
    d = card.build_data(p)
    assert d["avatar_b64"]
    d_no = dict(d)
    d_no["avatar_b64"] = ""
    png_yes = pic.render_sync("doupo", "light", card.CARD_NAME,
                              width=card.CARD_WIDTH, data=d)
    png_no = pic.render_sync("doupo", "light", card.CARD_NAME,
                             width=card.CARD_WIDTH, data=d_no)
    n_with = _dark_pixels(png_yes)
    n_without = _dark_pixels(png_no)
    assert n_with > 1500, f"卡片里没看到头像：深色像素仅 {n_with}"
    assert n_with > n_without + 1200, f"头像没画进卡片 with={n_with} without={n_without}"


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
    print(f"CARD_TOTAL={len(fns)} PASSED={ok} FAILED={len(fail)}")
    return 0 if not fail else 1


if __name__ == "__main__":
    sys.exit(_run_all())
