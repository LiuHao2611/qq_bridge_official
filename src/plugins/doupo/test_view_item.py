# -*- coding: utf-8 -*-
"""「查看 <物品名>」物品详情 + 炼药卡精简 验收（2026-11-25）。

## 用户原话

「加一个查看物品的功能，无论是背包还是宝库还是丹药，这些物品，都加一个查看物品
详情的功能，比如我输入查看 破厄丹，他就给我展示这个物品的卡片，有什么效果，几纹
有什么概率出什么加成。从何而来。其他物品也是一样……尽量展示详细，必须结合原著，
但是不要写明原著多少章什么的，就以药老或者普通商家的口吻描述就行。」

「原来炼药的卡片就只展示丹方和丹药本身效果就行，别写其他废话，丹药卡片收紧，
布局紧凑点，别搞得翻页太多了。」

## 本文件守什么

  ① ``item_lookup``：丹药 / 带纹丹 / 药材 / 天材地宝 / 系统杂项 / 傀儡材料都认得，
     查无此物返回空（不瞎猜）；
  ② 详情卡**三段齐**：本效 / 丹纹 / 从何而来；带纹丹标题用带纹全名、副标题标纹数；
  ③ 丹纹段必须给「出纹率 + 三档权重 + 六条赐福的档位差」——用户点名要的三件事；
  ④ 升技材料（焚血散）**不画丹纹段**：它根本没有带纹版，画了就是承诺打不出来的东西；
  ⑤ **卡面不许出现章节号**（红线：不要写明原著多少章），也不许出现工程口吻的
     「无 XX 系统」这类元说明（沉浸感红线）；
  ⑥ 炼药卡 ``desc`` 只剩本效：不许再拼〔功效标签〕/ 把握 / 丹纹率 / 越档；
  ⑦ 真出图（PNG 魔数 + 尺寸），不是只拼了个 dict。

运行：python src/plugins/doupo/test_view_item.py
"""
from __future__ import annotations

import asyncio
import os
import re
import sys
import types

PLUGINS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO_ROOT = os.path.dirname(os.path.dirname(PLUGINS_DIR))
for _p in (REPO_ROOT, PLUGINS_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def _stub_nonebot():
    nb = types.ModuleType("nonebot")

    def on_regex(*a, **k):
        m = types.SimpleNamespace()
        m.handle = lambda *a, **k: (lambda f: f)
        m.finish = lambda *a, **k: None
        return m

    nb.on_regex = on_regex
    nb.get_driver = lambda: types.SimpleNamespace(on_startup=lambda f: f)
    sys.modules["nonebot"] = nb
    for name in ("nonebot.adapters", "nonebot.adapters.onebot",
                 "nonebot.adapters.onebot.v11"):
        sys.modules[name] = types.ModuleType(name)
    v11 = sys.modules["nonebot.adapters.onebot.v11"]
    v11.MessageEvent = object
    v11.MessageSegment = object
    v11.Message = object


_stub_nonebot()

from doupo import commands, core, danwen as dw, config as cfg  # noqa: E402

_PASSED = 0
_FAILED = 0


def check(name, cond, detail=""):
    global _PASSED, _FAILED
    if cond:
        _PASSED += 1
        print("PASS  %s" % name)
    else:
        _FAILED += 1
        print("FAIL  %s  ->  %s" % (name, detail))


def _fresh(items: dict | None = None):
    """造一个清空背包的档（create_player 会发新手物资，不清空则持有量断言失真）。"""
    p = core.create_player("view-item", "看客", "g1")
    core.bag(p).clear()
    p["alch"] = 8
    p["flames"] = {"紫火": 1}
    for k, v in (items or {}).items():
        core.add_item(p, k, v)
    return p


def _card():
    """延迟拿 card 模块（它要独立加载 pic 包，loading 失败不该拖垮纯数据用例）。"""
    from doupo import card
    return card


def _all_rows(data: dict) -> list[str]:
    out = []
    for g in data.get("groups") or []:
        out.append(str(g.get("name") or ""))
        out.extend(str(r) for r in (g.get("rows") or []))
    return out


# ---------------------------------------------------------------------------
# ① 名称归一
# ---------------------------------------------------------------------------
def test_item_lookup_covers_every_category():
    cases = {
        "破厄丹": "破厄丹",
        "二纹破厄丹": "二纹破厄丹",
        "破厄丹 二纹": "二纹破厄丹",      # 纹数后置手打
        "冰晶石": "冰晶石",               # 药材
        "赤焰果": "赤焰果",               # 药材
        "药渣": "药渣",                   # 系统杂项
        "炎星石": "炎星石",
        "火属性魔核": "火属性魔核",        # 傀儡材料（动态命名）
        "强者躯体": "强者躯体",
    }
    bad = {k: commands.item_lookup(k) for k, v in cases.items()
           if commands.item_lookup(k) != v}
    check("查图鉴：各品类都认得", not bad, str(bad))
    check("查图鉴：查无此物返回空", commands.item_lookup("不存在的东西") == "")
    check("查图鉴：空串返回空", commands.item_lookup("") == "")
    # 没有带纹版的丹，写了纹数也要退回到基础丹（不能回空让玩家以为没这药）
    check("查图鉴：无带纹版时退回基础丹",
          commands.item_lookup(cfg.danwen_name(cfg.SKILL_UP_ITEM, 1)) == cfg.SKILL_UP_ITEM,
          commands.item_lookup(cfg.danwen_name(cfg.SKILL_UP_ITEM, 1)))


def test_cmd_view_text_fallback():
    check("空参给用法", "查看 <物品名>" in asyncio.run(commands.cmd_view(None, "x", "")),
          asyncio.run(commands.cmd_view(None, "x", ""))[:80])
    miss = asyncio.run(commands.cmd_view(None, "x", "不存在的东西"))
    check("查无此物给引导", "没找到" in miss and "包裹" in miss, miss[:80])
    txt = asyncio.run(commands.cmd_view(None, "x", "破厄丹"))
    check("文本兜底给本效", "本效" in txt and "丹纹" in txt, txt[:120])


# ---------------------------------------------------------------------------
# ②③ 详情卡内容
# ---------------------------------------------------------------------------
def test_pill_detail_has_three_sections():
    p = _fresh()
    d = _card().build_item_detail_data(p, "破厄丹")
    check("丹药：查得到", bool(d), d)
    if not d:
        return
    names = [g["name"] for g in d["groups"]]
    check("丹药：三段齐（本效/丹纹/从何而来）",
          names == ["本效", "丹纹", "从何而来"], names)
    rows = _all_rows(d)
    check("丹药：本效给的是本作实现（recipe_impl）",
          cfg.recipe_impl(cfg.RECIPE_BY_NAME["破厄丹"]) in rows,
          cfg.recipe_impl(cfg.RECIPE_BY_NAME["破厄丹"]))
    check("丹药：有药老口吻", any("药老言" in r for r in rows), rows[:4])
    # 用户点名的三件事：出纹率 / 三档概率 / 各档赐福
    rate = int(round(cfg.danwen_rate("五品") * 100))
    check("丹纹：给出纹率", any("%d%%" % rate in r and "出纹" in r for r in rows), rate)
    w = cfg.DANWEN_TIER_W
    check("丹纹：给三档概率",
          any(all("%d%%" % int(round(x * 100)) in r for x in w) for r in rows), w)
    pooled = [b["name"] for b in dw.BLESSINGS]
    check("丹纹：六条赐福全在卡上",
          all(any(b in r for r in rows) for b in pooled), pooled)
    check("丹纹：高档差值也写明（次数/时长放大）",
          any("二纹" in r and "三纹" in r for r in rows),
          [r for r in rows if "赐福池" in r or "二纹" in r][:3])
    check("从何而来：有炼制材料或售价",
          any("炼制｜" in r for r in rows) or any("宝库｜" in r for r in rows),
          rows[-4:])


def test_danwen_pill_shows_its_tier():
    p = _fresh({"二纹破厄丹": 2})
    d = _card().build_item_detail_data(p, "二纹破厄丹")
    check("带纹丹：标题是带纹全名", d and d["title"] == "二纹破厄丹", d and d["title"])
    check("带纹丹：副标题标纹数", d and "二纹" in d["subtitle"], d and d["subtitle"])
    check("带纹丹：副标题给持有量", d and "×2" in d["subtitle"], d and d["subtitle"])
    check("带纹丹：本效段说明会额外给赐福",
          d and any("带纹丹" in r for r in _all_rows(d)), d and _all_rows(d)[:4])


def test_skip_effect_pill_has_no_danwen_section():
    """焚血散是升技材料，注册表不给它建带纹版 —— 卡面就不能画丹纹池。"""
    d = _card().build_item_detail_data(_fresh(), cfg.SKILL_UP_ITEM)
    names = [g["name"] for g in (d or {}).get("groups") or []]
    check("升技材料：不画丹纹段", "丹纹" not in names, names)
    check("升技材料：本效与来历仍在", "本效" in names and "从何而来" in names, names)


def test_herb_and_material_detail():
    c = _card()
    herb = c.build_item_detail_data(_fresh({"冰晶石": 3}), "冰晶石")
    rows = _all_rows(herb)
    check("药材：本效只一行（不把同一句话印两遍）",
          len(herb["groups"][0]["rows"]) == 1, herb["groups"][0]["rows"])
    check("药材：商家口吻", any("老药师言" in r for r in rows), rows[:2])
    check("药材：给宝库价格", any("宝库" in r and "金" in r for r in rows), rows[-3:])
    check("药材：给用于哪些丹方", any("丹方" in r for r in rows), rows[-3:])

    mat = c.build_item_detail_data(_fresh(), "火属性魔核")
    check("傀儡材料：查得到", bool(mat), mat)
    check("傀儡材料：指路配方",
          mat and any("配方" in r or "傀儡" in r for r in _all_rows(mat)),
          mat and _all_rows(mat))


# ---------------------------------------------------------------------------
# ⑤ 红线：不出现章节号 / 元说明
# ---------------------------------------------------------------------------
def test_no_chapter_number_or_meta_on_card():
    c = _card()
    p = _fresh()
    pat_ch = re.compile(r"第\s*\d+\s*[章节]")
    pat_meta = re.compile(r"(无[^）]{0,8}(系统|机制|判定|流程|状态)|未上线|不做[^）]{0,6}加成)")
    bad = []
    # 全量扫：药名只是 key，任何一条 desc 里漏出「第900章」都会被玩家看到
    samples = (list(cfg.RECIPE_NAMES) + list(cfg.HERB_BY_NAME)
               + list(cfg.MATERIAL_BY_NAME) + list(cfg.MISC_ITEMS))
    for n in samples:
        d = c.build_item_detail_data(p, n)
        if not d:
            continue
        blob = "\n".join(_all_rows(d) + [d.get("subtitle") or "", d.get("tip") or ""])
        if pat_ch.search(blob):
            bad.append((n, "章节号", pat_ch.search(blob).group(0)))
        if pat_meta.search(blob):
            bad.append((n, "元说明", pat_meta.search(blob).group(0)))
    check("卡面不出现章节号 / 工程元说明（抽样 %d 件）" % len(samples), not bad, bad[:6])


# ---------------------------------------------------------------------------
# ⑥ 炼药卡精简
# ---------------------------------------------------------------------------
def test_recipes_card_is_slim():
    c = _card()
    p = _fresh({"赤焰果": 3, "冰晶石": 2})
    d = c.build_recipes_data(p, 1)
    descs = [r["desc"] for g in d["groups"] for r in g["rows"]]
    check("炼药卡：desc 非空", all(descs) and bool(descs), descs[:3])
    check("炼药卡：desc 不再拼〔功效标签〕",
          not any("〔" in s for s in descs), [s for s in descs if "〔" in s][:3])
    check("炼药卡：desc 不再印把握/越档",
          not any("把握" in s or "越档" in s or "勉强" in s for s in descs),
          [s for s in descs if "把握" in s][:3])
    check("炼药卡：desc 不再印丹纹率",
          not any(re.search(r"丹纹\s*\d+\s*%", s) for s in descs),
          [s for s in descs if "丹纹" in s][:3])
    same = [r["name"] for g in d["groups"] for r in g["rows"]
            if r["desc"] != cfg.recipe_impl(cfg.RECIPE_BY_NAME[r["name"]])]
    check("炼药卡：desc 就是 recipe_impl（逐条同源）", not same, same)
    check("炼药卡：每页满 %d 枚" % cfg.RECIPES_PER_PAGE,
          d["shown"] <= cfg.RECIPES_PER_PAGE, d["shown"])
    total = d["total_avail"]
    want = max(1, -(-total // cfg.RECIPES_PER_PAGE))
    check("炼药卡：页数按新容量算（%d 枚 → %d 页，旧口径 10/页会多翻 %d 次）"
          % (total, want, -(-total // 10) - want),
          d["total_pages"] == want, "%s vs %s" % (d["total_pages"], want))


# ---------------------------------------------------------------------------
# ⑦ 真出图 + 结构门禁
# ---------------------------------------------------------------------------
def test_render_and_wiring():
    import pathlib
    c = _card()
    p = _fresh({"二纹破厄丹": 1})
    png = asyncio.run(c.render_item_detail(p, "二纹破厄丹"))
    check("真出图：PNG 魔数", bool(png) and png[:8] == b"\x89PNG\r\n\x1a\n",
          (png or b"")[:8])
    check("真出图：有宽度（>500px）",
          bool(png) and int.from_bytes(png[16:20], "big") > 500,
          int.from_bytes((png or b"\0\0\0\0")[16:20], "big"))
    check("查无此物返回 None（不拿占位卡糊人）",
          asyncio.run(c.render_item_detail(p, "不存在的东西")) is None)

    init_src = pathlib.Path(__file__).with_name("__init__.py").read_text(encoding="utf-8")
    check("结构：查看口令已注册", "view_cmd = on_regex(" in init_src)
    check("结构：查看走 item_lookup 而非要求持有",
          "commands.item_lookup(arg)" in init_src)
    bt = (pathlib.Path(__file__).resolve().parents[1]
          / "qq_bridge" / "channel" / "doupo_buttons.py").read_text(encoding="utf-8")
    check("结构：物品详情卡有下钻按钮", '"item": ("服丹"' in bt)
    pic = (pathlib.Path(__file__).resolve().parents[1]
           / "qq_bridge" / "pic" / "__init__.py").read_text(encoding="utf-8")
    # 2026-11-25：物品详情卡改用专属模板 doupo_item（去气泡/序号圈、解包硬边框），
    # 不再复用 doupo_craft。这里守的是「注册到的是专属模板」而不是旧复用。
    check("结构：doupo_item 模板已注册", '"doupo_item": _doupo_item.build' in pic)


def test_danwen_tier_table_shape():
    """档位差值只标「变了的那一个数」，且不在没有占位符的条上乱标。"""
    by_id = {b["id"]: b for b in dw.BLESSINGS}
    t1 = dw.tier_table(by_id["B01"])
    check("B01 给次数差", "二纹" in t1 and "三纹" in t1, t1)
    # 档位差里单位只写一次（「二纹 4 / 三纹 6 次」），否则每条都带单位会多折一行
    delta = t1.split("｜")[-1]
    check("B01 档位差里单位只写一次", delta.count("次") == 1, delta)
    t2 = dw.tier_table(by_id["B02"])
    check("B02（文案无占位符）不编造档位差", "二纹" not in t2, t2)
    t3 = dw.tier_table(by_id["B03"])
    check("B03 给分钟差", "分钟" in t3 and "二纹" in t3, t3)


def _run():
    fns = [(k, v) for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    for name, fn in fns:
        fn()
    print("=" * 70)
    print("VIEWITEM_TOTAL=%d PASSED=%d FAILED=%d"
          % (_PASSED + _FAILED, _PASSED, _FAILED))
    return 0 if _FAILED == 0 else 1


if __name__ == "__main__":
    sys.exit(_run())
