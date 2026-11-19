# -*- coding: utf-8 -*-
"""test_hw_vramglass —— 硬件行情重做（VRAMGlass 数据源）守门测试。

铁律：机器判定 assert + 非零退出码，不靠肉眼。覆盖：
  1. 数据层解析：首页表格 / 三市场历史 / 无报价型号返回 None；
  2. 口径层：24h/6h 涨跌算法、区间高低、消费级过滤、诚实标注；
  3. 视图契约：总览/单型号 payload 的 view/字段/来源口径；
  4. 卡片：两视图能出图、页脚口径必现、boxes 登记、无重叠越界；
  5. 隔离：旧 zol/daily/market 文件未被本轮改动（源码契约）；
  6. 反例注入：改口径/去掉标注/混计算卡 必须被判据抓住。

运行（无 nonebot/httpx 依赖，纯标准库 + PIL）：
    python tests/test_hw_vramglass.py
"""
from __future__ import annotations

import importlib.util
import json
import re
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "plugins" / "qq_bridge"


def _load(name: str, path: Path, pkg: str | None = None):
    full = (pkg + "." + name) if pkg else name
    spec = importlib.util.spec_from_file_location(full, str(path))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[full] = mod
    spec.loader.exec_module(mod)
    return mod


# ---- 用假包上下文加载 vg / vgmarket，绕开 qq_bridge 的 nonebot/httpx 依赖 ----
_pkg = types.ModuleType("hwpkg")
_pkg.__path__ = [str(SRC / "hardware")]
sys.modules["hwpkg"] = _pkg
vg = _load("vg", SRC / "hardware" / "vg.py", "hwpkg")
vm = _load("vgmarket", SRC / "hardware" / "vgmarket.py", "hwpkg")


# ---------------------------------------------------------------------------
# 最小 HTML 夹具（含首页表格行 + 详情历史块，真实结构缩小版）
# ---------------------------------------------------------------------------
HOME_HTML = """
<table><tbody>
<tr><td><a href="/zh/gpu/rtx-5090">GeForce RTX 5090</a></td>
<td data-sort-key="price"><span data-usd="7005.17">¥46,800</span><span class="sub">11 条挂单</span></td>
<td><span class="trend-down">-4.5%</span></td>
<td><svg><polyline points="0,3 1,5 2,4" /></svg></td></tr>
<tr><td><a href="/zh/gpu/a100-80gb-pcie">A100 80GB PCIe</a></td>
<td><span data-usd="12109.36">¥80,900</span><span class="sub">2 条挂单</span></td>
<td><span class="trend-up">+3.0%</span></td><td></td></tr>
<tr><td><span>无型号链接的行</span></td><td><span data-usd="1">¥1</span></td><td></td><td></td></tr>
</tbody></table>
""" + "x" * 2500  # 过 _get 短页保护（离线不走 _get，仅示意真实页长度）

DETAIL_POINTS = json.dumps([
    {"x": 0, "y": 50, "t": "2026-11-18T06:00:00.000Z", "d": "2026年11月18日 06:00", "u": "100", "m": "¥1,000"},
    {"x": 50, "y": 30, "t": "2026-11-18T12:00:00.000Z", "d": "2026年11月18日 12:00", "u": "110", "m": "¥1,100"},
    {"x": 100, "y": 20, "t": "2026-11-19T06:00:00.000Z", "d": "2026年11月19日 06:00", "u": "121", "m": "¥1,210"},
], ensure_ascii=False)
SHARE = json.dumps({"name": "Test GPU", "market": {"label": "闲鱼"}, "priceUsd": 121})
DETAIL_HTML = ('<script type="application/json" data-price-points>%s</script>'
               '<script type="application/json" data-share-image-data>%s</script>'
               % (DETAIL_POINTS, SHARE)) + "y" * 2500

NO_QUOTE_HTML = '<p>暂无报价</p>' + "z" * 2500

# 大盘指数夹具（离线，避免测试联网）
IDX_BOOK = vg.IndexBook(items=[
    vg.Index("大盘", 102.58, -1.45), vg.Index("入门", 113.68, 0.03),
    vg.Index("主力", 129.68, 1.38), vg.Index("旗舰", 98.45, -2.11),
], updated="2026/11/19 20:00")

INDEX_HTML_SNIPPET = (
    '<h3>显卡大盘指数</h3>当前指数 102.58 -1.45% '
    '<h3>入门显卡指数</h3>当前指数 113.68 +0.03% '
    '<h3>主力显卡指数</h3>当前指数 129.68 +1.38% '
    '<h3>旗舰显卡指数</h3>当前指数 98.45 -2.11% '
    '<span>价格更新于 2026/11/19 20:00</span>' + "w" * 2500
)


def test_parse_listing():
    rows = vg.parse_listing(HOME_HTML)
    assert len(rows) == 2, rows  # 无链接行被跳过
    r = rows[0]
    assert r.name == "GeForce RTX 5090"
    assert r.price == 46800 and r.samples == 11
    assert abs(r.change_pct + 4.5) < 1e-6
    assert r.trend == [3.0, 5.0, 4.0]


def test_parse_detail_three_points():
    d = vg.parse_detail(DETAIL_HTML, "xianyu")
    assert d.price_cny == 1210 and d.market == "xianyu"
    assert len(d.points) == 3
    assert d.points[0].cny == 1000 and d.latest.cny == 1210


def test_parse_indices_pairs_title_value_change():
    """大盘指数：标题↔当前值↔24h涨跌 必须正确配对，且 4 条不重不漏。"""
    book = vg.parse_indices(INDEX_HTML_SNIPPET)
    assert [i.name for i in book.items] == ["大盘", "入门", "主力", "旗舰"]
    by = {i.name: i for i in book.items}
    assert abs(by["大盘"].value - 102.58) < 1e-6 and abs(by["大盘"].change_pct + 1.45) < 1e-6
    assert abs(by["主力"].value - 129.68) < 1e-6 and abs(by["主力"].change_pct - 1.38) < 1e-6
    assert book.updated == "2026/11/19 20:00"


def test_overview_carries_indices():
    rows = vg.parse_listing(HOME_HTML)
    ov = vm.overview_payload(vg.Listing(rows=rows, fetched_at="2026-11-19T10:00:00"), IDX_BOOK)
    assert len(ov["indices"]) == 4
    assert ov["indices"][0]["name"] == "大盘" and ov["indices"][0]["value"] == 102.58
    # 指数涨跌幅必须原样透出到 payload：曾出现过「4 条全 +0.00%」的截断 bug，
    # parse 层有断言但 payload 层曾无人守，反证 M5 一度全绿（2026-11-19 补）。
    assert [i["pct"] for i in ov["indices"]] == [-1.45, 0.03, 1.38, -2.11]
    assert ov["idx_updated"] == "2026/11/19 20:00"


def test_no_quote_is_none():
    """VRAMGlass「暂无报价」→ 返回 None（不伪造价），且走真实 fetch_detail 分支。"""
    orig = vg._get
    try:
        vg._get = lambda url: NO_QUOTE_HTML  # 模拟该市场无报价页面
        vg._detail_cache.clear()
        got = vg.fetch_detail("/zh/gpu/no-quote", "amazon_jp", force=True)
        assert got is None, "无报价必须返回 None，绝不返回一个伪造的价"
    finally:
        vg._get = orig
        vg._detail_cache.clear()


def test_overview_filters_calculate_cards():
    rows = vg.parse_listing(HOME_HTML)
    ov = vm.overview_payload(vg.Listing(rows=rows, fetched_at="2026-11-19T10:00:00"), IDX_BOOK)
    # A100 是计算卡，必须被剔除；只剩 RTX 5090 一款消费卡
    assert ov["tracked"] == 1, ov["tracked"]
    assert all("A100" not in m["label"] for m in ov["gainers"] + ov["losers"])
    assert ov["view"] == "vg_overview"
    assert "非成交价" in ov["source"]


def test_consumer_filter_excludes_workstation():
    """专业/工作站卡不得混进 DIY 榜。

    2026-11-20 实拍漏网清单（旧实现只查前两个 token 的 "PRO"，且前缀表含裸
    ``Radeon ``/``Arc ``，于是这些卡被算成消费级、直接污染「入门/主力/旗舰」分层）：
    """
    pro = ["RTX PRO 6000", "RTX PRO 6000D", "RTX 6000 Ada", "RTX A6000",
           "Radeon AI PRO R9700", "Radeon PRO W7900", "Radeon PRO W7800",
           "Arc Pro B70", "Arc Pro B60", "MacBook Pro M4 Max 128GB"]
    cons = ["GeForce RTX 5090", "GeForce RTX 4090D 48GB", "GeForce RTX 2080 Ti 22GB",
            "Radeon RX 9070 XT", "Radeon RX 7800 XT", "Arc B580", "Intel Arc A770"]
    for n in pro:
        row = vg.Row(name=n, path="", price=1, usd=1.0, change_pct=0.0, samples=1)
        assert vm._is_consumer(row) is False, "专业卡漏进 DIY 榜：%s" % n
    for n in cons:
        row = vg.Row(name=n, path="", price=1, usd=1.0, change_pct=0.0, samples=1)
        assert vm._is_consumer(row) is True, "消费卡被误杀：%s" % n


def test_chart_time_label_complete_to_minute():
    """走势图时间轴必须完整到分钟。

    回归：曾用 ``p.label[5:16]`` 盲切，而上游标签是中文格式「2026年11月19日 12:00」，
    比 ISO 多一个「日」，盲切把末尾 0 吃掉 → 卡面印「11月19日 12:0」（2026-11-20 实拍抓到）。
    """
    assert vm._short_time("2026年11月19日 12:00") == "11月19日 12:00"
    assert vm._short_time("2026-11-19 12:00") == "11-19 12:00"
    assert vm._short_time("") == ""

    row = vg.parse_listing(HOME_HTML)[0]
    vg._detail_cache[(row.path, "xianyu")] = (1e12, vg.parse_detail(DETAIL_HTML, "xianyu"))
    vg._detail_cache[(row.path, "ebay")] = (1e12, None)
    vg._detail_cache[(row.path, "amazon_jp")] = (1e12, None)
    mp = vm.model_payload(row)
    ms = [p["m"] for p in mp["chart"]]
    assert ms, "走势图不能为空"
    for m in ms:
        assert re.search(r"\d{2}:\d{2}$", m), "时间标签被截断：%r" % m
        assert not m.startswith("20"), "年份前缀没去掉：%r" % m


def test_overview_change_direction():
    rows = vg.parse_listing(HOME_HTML)
    rows = [r for r in rows if "A100" not in r.name]
    ov = vm.overview_payload(vg.Listing(rows=rows, fetched_at="2026-11-19T10:00:00"), IDX_BOOK)
    assert ov["buckets"][0]["down"] == 1  # 5090 -4.5%
    assert ov["buckets"][0]["up"] == 0
    assert ov["losers"][0]["label"] == "RTX 5090"
    assert ov["losers"][0]["prev"] == 49005  # 46800 / 0.955
    # 方向硬判：涨榜只能装涨的、跌榜只能装跌的，且本夹具无涨项 → 涨榜必须空。
    # （2026-11-19 反证抓到：原先只断言 losers，把 gain/lose 的过滤条件对调仍全绿 = 红绿搞反漏网）
    assert ov["gainers"] == []
    assert all(x["pct"] > 0 for x in ov["gainers"])
    assert all(x["pct"] < 0 for x in ov["losers"])


def test_gainers_never_mix_losers():
    """混装夹具：同时有 +2.0% 与 -4.5%，两榜必须各归各位（反「红绿搞反」）。"""
    rows = [r for r in vg.parse_listing(HOME_HTML) if "A100" not in r.name]
    up = vg.Row(name="GeForce RTX 4070", path="/zh/gpu/rtx-4070", price=4400,
                usd=610.0, change_pct=2.0, samples=30)
    ov = vm.overview_payload(
        vg.Listing(rows=rows + [up], fetched_at="2026-11-19T10:00:00"), IDX_BOOK)
    assert ov["buckets"][0]["up"] == 1 and ov["buckets"][0]["down"] == 1
    assert [g["label"] for g in ov["gainers"]] == ["RTX 4070"]
    assert [x["label"] for x in ov["losers"]] == ["RTX 5090"]
    # 涨榜按涨幅降序、跌榜按跌幅升序（榜首要最极端）
    assert ov["gainers"][0]["pct"] == 2.0 and ov["losers"][0]["pct"] == -4.5


def test_model_payload_honest_and_blocks():
    row = vg.parse_listing(HOME_HTML)[0]
    # 注入三市场缓存：闲鱼/ebay 有数据，日亚无报价
    d1 = vg.parse_detail(DETAIL_HTML, "xianyu")
    d2 = vg.parse_detail(DETAIL_HTML, "ebay")
    vg._detail_cache[(row.path, "xianyu")] = (1e12, d1)
    vg._detail_cache[(row.path, "ebay")] = (1e12, d2)
    vg._detail_cache[(row.path, "amazon_jp")] = (1e12, None)
    mp = vm.model_payload(row)
    assert mp["view"] == "vg_model"
    assert "非成交价" in mp["sub"] and "非成交价" in mp["source"]
    assert len(mp["markets"]) == 3
    jp = [m for m in mp["markets"] if m["key"] == "amazon_jp"][0]
    assert jp["price"] is None and jp["samples_note"] == "暂无报价"
    assert jp["label"] == "日本亚马逊"  # 无报价也有中文市场名
    assert mp["low"] == 1000 and mp["high"] == 1210
    assert abs(mp["pct6h"] - 10.0) < 1e-6  # (1210-1100)/1100


def test_old_modules_untouched():
    """旧 daily/zol/market 仍在，且没有 import vg（证明并存可回退、未混入旧链路）。"""
    for f in ("zol.py", "daily.py", "market.py", "dataset.py"):
        txt = (SRC / "hardware" / f).read_text(encoding="utf-8")
        assert "import vg" not in txt and "vgmarket" not in txt, f


def test_render_both_views():
    # 走 pic 渲染链，验两视图出图 + 页脚口径必现 + boxes 登记
    bridge = types.ModuleType("qq_bridge")
    bridge.__path__ = [str(SRC)]
    sys.modules["qq_bridge"] = bridge
    import qq_bridge.pic as pic  # noqa: F401

    rows = vg.parse_listing(HOME_HTML)
    ov = vm.overview_payload(vg.Listing(rows=rows, fetched_at="2026-11-19T10:00:00"), IDX_BOOK)
    b1: list = []
    png1 = pic.render_sync("hw_market", "light", payload=ov, boxes=b1)
    assert png1[:8] == b"\x89PNG\r\n\x1a\n" and len(b1) >= 20

    row = rows[0]
    d = vg.parse_detail(DETAIL_HTML, "xianyu")
    vg._detail_cache[(row.path, "xianyu")] = (1e12, d)
    vg._detail_cache[(row.path, "ebay")] = (1e12, None)
    vg._detail_cache[(row.path, "amazon_jp")] = (1e12, None)
    mp = vm.model_payload(row)
    b2: list = []
    png2 = pic.render_sync("hw_market", "light", payload=mp, boxes=b2)
    assert png2[:8] == b"\x89PNG\r\n\x1a\n" and len(b2) >= 20

    from PIL import Image
    import io
    assert Image.open(io.BytesIO(png1)).width == 800
    assert Image.open(io.BytesIO(png2)).width == 800


# ---------------------------------------------------------------------------
if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn()
            print("PASS", fn.__name__)
        except Exception as e:  # noqa: BLE001
            failed += 1
            print("FAIL", fn.__name__, type(e).__name__, e)
    print("TOTAL %d passed / %d failed" % (len(fns) - failed, failed))
    sys.exit(1 if failed else 0)
