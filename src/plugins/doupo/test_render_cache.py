# -*- coding: utf-8 -*-
"""出卡渲染缓存验收（2026-11-25 玩家「查看物品卡输出很慢」）。

## 用户原话

「我发现查看物品卡片输出很慢，这种靠百分之99都是固定内容，怎么可能慢呢，
你是不是忘了加缓存，优化一下」

## 为什么能慢

一张卡的内容 = `(template, card_name, render_data)`。物品详情卡的内容 99% 来自
配置表（本效/丹纹/从何而来/售价），只有「背包 ×N」随存档变。也就是说同一件东西
看一眼再一眼，两次要画的是**同一张图**，却被当新图重画了一遍：取数 → 排版 →
PNG 编码，本地 400~750ms，手机端同量级，再叠一次素材上传就更久。

## 本文件守

  ① 同一份数据第二次直接命中（`render_cache_stats()["hit"]` 递增），
     且命中返回的字节与真渲染**逐字节相同**（上层素材 md5 缓存才敢复用）；
  ② 不同物品 = 不同 key，不会串图；
  ③ 背包持有量变了 = 内容真变了 → 必须重画（缓存不许把新数字吞掉）；
  ④ 同一份数据重画出来的字节是确定性的（md5 三次一致）——
     字节不稳则素材永远重传，缓存也白搭；
  ⑤ LRU 有上限，不会把手机内存吃穿；
  ⑥ 源码级：先查缓存再渲染，渲染成功才写缓存（失败/空图不写，否则坏图被永久冻结）。

运行：python src/plugins/doupo/test_render_cache.py
"""
from __future__ import annotations

import asyncio
import hashlib
import os
import pathlib
import sys
import time
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

from doupo import card, core, config as cfg  # noqa: E402

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
    p = core.create_player("render-cache", "缓存客", "g1")
    core.bag(p).clear()
    for k, v in (items or {}).items():
        core.add_item(p, k, v)
    return p


def _render(p, name: str):
    """跑一次真出图，返回 (png, 耗时毫秒)。"""
    t = time.time()
    png = asyncio.run(card.render_item_detail(p, name))
    return png, (time.time() - t) * 1000


# ---------------------------------------------------------------------------
# ①② 命中 & 字节一致
# ---------------------------------------------------------------------------
def test_second_render_hits_cache():
    p = _fresh({"破厄丹": 3})
    card.render_cache_clear()
    first, t0 = _render(p, "破厄丹")
    stats1 = card.render_cache_stats()
    second, t1 = _render(p, "破厄丹")
    stats2 = card.render_cache_stats()

    check("首绘：真渲染（miss+put 各 1）",
          stats1 == {"hit": 0, "miss": 1, "put": 1}, stats1)
    check("二次：命中缓存（hit 1，未再 put）",
          stats2["hit"] == 1 and stats2["put"] == 1, stats2)
    check("命中返回的字节与真渲染逐字节相同",
          bool(first) and first == second,
          "%s vs %s" % (hashlib.md5(first or b"").hexdigest(),
                        hashlib.md5(second or b"").hexdigest()))
    check("命中明显更快（%.0fms → %.0fms）" % (t0, t1), t1 < t0 / 2, (t0, t1))


def test_different_items_do_not_collide():
    p = _fresh()
    card.render_cache_clear()
    a, _ = _render(p, "破厄丹")
    b, _ = _render(p, "冰晶石")
    st = card.render_cache_stats()
    check("两件不同物品都 miss（不串图）", st["miss"] == 2 and st["hit"] == 0, st)
    check("两件不同物品字节不同",
          bool(a) and bool(b) and hashlib.md5(a).hexdigest() != hashlib.md5(b).hexdigest(),
          (hashlib.md5(a or b"").hexdigest(), hashlib.md5(b or b"").hexdigest()))


# ---------------------------------------------------------------------------
# ③ 内容变了必须重画（缓存不许吞掉新数字）
# ---------------------------------------------------------------------------
def test_held_count_change_invalidates():
    p = _fresh({"破厄丹": 3})
    card.render_cache_clear()
    before, _ = _render(p, "破厄丹")
    core.add_item(p, "破厄丹", 2)
    d = card.build_item_detail_data(p, "破厄丹")
    check("改持有量后卡面数据跟着变（×5）", "×5" in (d or {}).get("subtitle", ""),
          (d or {}).get("subtitle"))
    after, _ = _render(p, "破厄丹")
    st = card.render_cache_stats()
    check("改持有量后是 miss（重画一张）", st["miss"] == 2 and st["hit"] == 0, st)
    check("改持有量后图真的换了",
          bool(before) and bool(after)
          and hashlib.md5(before).hexdigest() != hashlib.md5(after).hexdigest())


# ---------------------------------------------------------------------------
# ④ 字节确定性（否则上层素材 md5 缓存永远重传）
# ---------------------------------------------------------------------------
def test_render_bytes_are_deterministic():
    p = _fresh({"炎星石": 4})
    md5s = []
    for _ in range(3):
        card.render_cache_clear()      # 每次都逼真渲染
        png, _ = _render(p, "炎星石")
        md5s.append(hashlib.md5(png or b"").hexdigest())
    check("同一数据三次真渲染 md5 一致（素材才能按 md5 复用）",
          len(set(md5s)) == 1, md5s)


# ---------------------------------------------------------------------------
# ⑤ LRU 上限
# ---------------------------------------------------------------------------
def test_cache_is_bounded():
    card.render_cache_clear()
    cap = card._RENDER_CACHE_MAX
    for i in range(cap + 12):
        card._render_cache_put("k%d" % i, b"\x89PNG" + str(i).encode())
    check("缓存不超过 %d 张上限（实际 %d）" % (cap, len(card._RENDER_CACHE)),
          len(card._RENDER_CACHE) <= cap, len(card._RENDER_CACHE))
    check("淘汰的是最老的那批（k0 已被挤掉）", "k0" not in card._RENDER_CACHE)
    check("最新的还在（LRU 从尾淘汰）", ("k%d" % (cap + 11)) in card._RENDER_CACHE)
    card.render_cache_clear()


def test_empty_png_is_not_cached():
    card.render_cache_clear()
    card._render_cache_put("empty", b"")
    check("空图不写缓存（否则降级文本会被永久冻住）", len(card._RENDER_CACHE) == 0)


# ---------------------------------------------------------------------------
# ⑥ 源码级：先查后画、画成才写
# ---------------------------------------------------------------------------
def test_source_order():
    src = pathlib.Path(__file__).with_name("card.py").read_text(encoding="utf-8")
    body = src.split("async def _render_list(", 1)[1].split("\ndef ", 1)[0]
    i_key = body.find("_render_cache_key(")
    i_get = body.find("_render_cache_get(")
    i_render = body.find("await pic.render(")
    i_put = body.find("_render_cache_put(")
    check("顺序：算 key → 查缓存 → 真渲染 → 写缓存",
          -1 < i_key < i_get < i_render < i_put, (i_key, i_get, i_render, i_put))
    i_ret = body.find("return cached")
    check("命中即 return（不往下渲染）", -1 < i_ret < i_render, (i_ret, i_render))
    check("命中分支判的是 get() 的返回值",
          "if cached is not None:" in body, body[i_get:i_get + 120])
    check("主题指纹进了 key（改主题自动换 key）",
          "_styled(data, style)" in body and "_render_cache_key(" in body, "")


# ---------------------------------------------------------------------------
# ⑦ 人物卡必须也走这条缓存（2026-11-26 用户「今天卡片发送又变慢了」）
# ---------------------------------------------------------------------------
def test_panel_goes_through_render_cache():
    """``render_panel`` 是**唯一**自己重写了一遍渲染的入口，因而绕过了 LRU。

    2026-11-26 排查发现：背包/斗技/物品/周本全走 ``_render_list``，只有人物卡
    （最高频的一张）单独实现 ``_style_for`` + ``pic.render``，既不记 stats，
    也没有「命中即字节稳定 → 素材 md5 可复用」的保证。

    prove-it：把 ``render_panel`` 改回旧写法（自己调 pic.render），
    下面「调的是 _render_list」立刻为假，断言必红。
    """
    p = _fresh({"破厄丹": 1})
    p["qq"] = "10086"
    card.render_cache_clear()

    hits: list[tuple] = []
    orig = card._render_list

    async def spy(template, data, card_name, qq=None):
        hits.append((template, card_name, qq))
        return b"\x89PNG-fake"

    try:
        card._render_list = spy
        asyncio.run(card.render_panel(p))
    finally:
        card._render_list = orig

    check("人物卡走 _render_list（同一套缓存/主题/兜底）",
          len(hits) == 1, hits)
    if hits:
        tpl, name, qq = hits[0]
        check("模板/卡名正确（doupo / 斗破人物）",
              tpl == "doupo" and name == card.CARD_NAME, (tpl, name))
        check("玩家 QQ 透传（个人主题才对得上）", str(qq) == "10086", qq)


def test_panel_source_has_no_private_render_path():
    """源码级：``render_panel`` 体内不许再出现自己的 pic.render 调用。

    防止有人「怕麻烦」又把它抄回去——那等于把最高频的卡踢出缓存。
    """
    src = pathlib.Path(__file__).with_name("card.py").read_text(encoding="utf-8")
    body = src.split("async def render_panel(", 1)[1].split("\nasync def ", 1)[0]
    check("render_panel 已委托 _render_list", "_render_list(" in body, body[:300])
    check("render_panel 不再自己调 pic.render", "pic.render(" not in body, body[:300])
    check("render_panel 不再自己注入主题", "_style_for(" not in body, body[:300])



def _run():
    fns = [(k, v) for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    for name, fn in fns:
        print("--", name)
        fn()
    print("=" * 70)
    print("RENDERCACHE_TOTAL=%d PASSED=%d FAILED=%d"
          % (_PASSED + _FAILED, _PASSED, _FAILED))
    return 0 if _FAILED == 0 else 1


if __name__ == "__main__":
    sys.exit(_run())
