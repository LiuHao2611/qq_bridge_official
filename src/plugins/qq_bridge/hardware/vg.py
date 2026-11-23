# -*- coding: utf-8 -*-
"""hardware.vg —— VRAMGlass 数据源（每日/6小时行情的**新**数据层）。

为什么换它（2026-11-19 深度调研结论，见 docs/硬件行情重做_2026-11-19.md）：
  * 旧源 ZOL「参考报价」是媒体指导价，且取样本最低价——单个异常低价带偏全局，
    这是「数据极其不准」的技术根因；CPU 不采、月度档案过期 48 天。
  * 京东 p.3.cn 空返回、闲鱼搜索页 11KB 风控壳、什么值得买 202、慢慢买 302：
    无登录态拿不到电商真实价；Playwright+登录态属账号风控红线，容器不碰。
  * B站/头条「显卡日报」UP 主的可复现做法就是**多源聚合**，其二手基准源正是
    VRAMGlass（vramglass.com）的闲鱼快照——本项目直接对接同一公开源，
    不臆造数据、不登录、不破解。

VRAMGlass 提供什么（页面服务端渲染，公开可机读）：
  * ``/zh``                 首页表格：全部型号的最新中位价 + 24h 涨跌 + 挂单数 + 30d 走势；
  * ``/zh/index``           大盘价格指数（4 条 price-points 序列）；
  * ``/zh/gpu/<slug>``      单型号详情：``data-price-points`` 全历史（6 小时粒度，
                            2026-09-04 起）+ ``data-share-image-data`` 摘要；
  * ``?market=xianyu|ebay|amazon_jp``  三市场各自独立序列：
                            闲鱼=二手挂单，eBay/日亚=电商在售挂单，均为**中位价**。

口径铁律（卡面必须如实标注）：
  * 价格是「在售挂单中位价」，**不是已成交价**（VRAMGlass 官方口径）；
  * 三市场分开统计，绝不混成一个价；有效样本<3 的型号官网不发布，本层也不编；
  * 涨跌来自真实历史序列的时点对比，不是拿月度环比冒充日环比。

工程约束：
  * 只读公开 HTML，urllib + 浏览器头；超时/重试/显式异常，绝不静默回空；
  * 进程内 TTL 缓存（首页 30 分钟、详情 2 小时），避免频繁打第三方；
  * 本模块**不 import 旧 zol/daily/market 的可变状态**，可独立裸单测。
"""
from __future__ import annotations

import html as _html
import json
import re
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta

BASE = "https://vramglass.com"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")
TIMEOUT = 20.0
RETRIES = 2
CST = timezone(timedelta(hours=8))

MARKETS = {
    "xianyu": "闲鱼二手",
    "ebay": "eBay",
    "amazon_jp": "日本亚马逊",
}
DEFAULT_MARKET = "xianyu"

_list_cache: tuple[float, "Listing"] | None = None
_detail_cache: dict[tuple[str, str], tuple[float, "Detail"]] = {}
_cache_lock = threading.Lock()
LIST_TTL = 30 * 60.0
DETAIL_TTL = 2 * 60 * 60.0


class VgError(Exception):
    """抓取/解析失败（显式上抛；调用方必须让用户看见，绝不静默伪造）。"""


def now() -> datetime:
    return datetime.now(timezone.utc).astimezone(CST).replace(tzinfo=None)


def _get(url: str) -> str:
    headers = {
        "User-Agent": UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9",
    }
    last = None
    for i in range(RETRIES + 1):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:  # noqa: S310 — 固定 https
                raw = r.read()
            text = raw.decode("utf-8", "ignore")
            if len(text) < 2000:
                raise VgError("页面异常短（%d B），疑似风控/改版：%s" % (len(text), url))
            return text
        except urllib.error.HTTPError as e:
            last = e
            if e.code in (403, 429) and i < RETRIES:
                time.sleep(2.0 * (i + 1))
                continue
            if i < RETRIES:
                time.sleep(1.0 * (i + 1))
                continue
        except Exception as e:  # noqa: BLE001 — 重试后仍失败上抛
            last = e
            if i < RETRIES:
                time.sleep(1.0 * (i + 1))
    raise VgError("%s: %s" % (url.split("?")[0], type(last).__name__))


def _json_block(page: str, pattern: str) -> str:
    m = re.search(pattern, page, re.S)
    if not m:
        raise VgError("页面缺少数据块：%s" % pattern[:40])
    return m.group(1)


# ---------------------------------------------------------------------------
# 首页表格：一次拿到全部型号的最新中位价 / 24h 涨跌 / 挂单数 / 30d 走势
# ---------------------------------------------------------------------------
@dataclass
class Row:
    name: str
    path: str
    price: int                 # 人民币中位价（取页面显示的 ¥ 整数）
    usd: float                 # 美元基准价（汇率换算用）
    change_pct: float | None   # 24h 涨跌（%）
    samples: int               # 有效挂单数
    trend: list[float] = field(default_factory=list)  # 30d 走势 polyline 的 y 序列


@dataclass
class Listing:
    rows: list[Row]
    fetched_at: str
    source: str = "vramglass"


_TR_RE = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S)
_CELL_RE = re.compile(r"<td[^>]*>(.*?)</td>", re.S)
_TD_TEXT_RE = re.compile(r"<[^>]+>")
_NAME_RE = re.compile(r'<a href="(/zh/gpu/[^"]+)">([^<]+)</a>')
_PRICE_RE = re.compile(r'data-usd="([\d.]+)"[^>]*>([^<]*¥[\d,]+)')
_DISPLAY_RE = re.compile(r"¥([\d,]+)")
_SAMPLES_RE = re.compile(r"(\d+)\s*条挂单")
_CHANGE_RE = re.compile(r"([+-]?[\d.]+)%")
_POLY_RE = re.compile(r'<polyline[^>]*points="([^"]+)"')


def parse_listing(page: str) -> list[Row]:
    """解析首页表格。价格缺失/无报价的型号跳过（不编造）。"""
    rows: list[Row] = []
    for tr in _TR_RE.findall(page):
        nm = _NAME_RE.search(tr)
        pr = _PRICE_RE.search(tr)
        if not nm or not pr:
            continue
        path, name = nm.group(1), _html.unescape(nm.group(2)).strip()
        disp = _DISPLAY_RE.search(pr.group(2))
        if not disp:
            continue
        price = int(disp.group(1).replace(",", ""))
        sm = _SAMPLES_RE.search(_TD_TEXT_RE.sub(" ", tr))
        ch = _CHANGE_RE.search(_TD_TEXT_RE.sub(" ", tr))
        poly = _POLY_RE.search(tr)
        trend = []
        if poly:
            for pair in poly.group(1).split():
                if "," in pair:
                    try:
                        trend.append(float(pair.split(",", 1)[1]))
                    except ValueError:
                        pass
        rows.append(Row(
            name=name, path=path, price=price, usd=float(pr.group(1)),
            change_pct=float(ch.group(1)) if ch else None,
            samples=int(sm.group(1)) if sm else 0,
            trend=trend,
        ))
    if not rows:
        raise VgError("首页表格解析 0 行（页面改版？）")
    return rows


def fetch_listing(force: bool = False) -> Listing:
    """抓首页全型号快照（带 TTL 缓存）。"""
    global _list_cache
    with _cache_lock:
        if not force and _list_cache and time.time() - _list_cache[0] < LIST_TTL:
            return _list_cache[1]
    page = _get(BASE + "/zh")
    rows = parse_listing(page)
    obj = Listing(rows=rows, fetched_at=now().isoformat(timespec="seconds"))
    with _cache_lock:
        _list_cache = (time.time(), obj)
    return obj


# ---------------------------------------------------------------------------
# 单型号详情：完整历史 + 三市场
# ---------------------------------------------------------------------------
@dataclass
class Point:
    t: str          # ISO 时间戳（UTC）
    label: str      # 页面显示「2026年11月19日 06:00」
    usd: float
    cny: int        # 页面显示人民币中位价


@dataclass
class Detail:
    name: str
    market: str
    market_label: str
    points: list[Point]
    price_cny: int
    fetched_at: str

    @property
    def latest(self) -> Point:
        return self.points[-1]


_POINT_KEYS = re.compile(r'"t":\s*"([^"]+)".*?"u":\s*"([\d.]+)".*?"m":\s*"(¥[\d,]+)"')
_SHARE_RE = re.compile(r'<script type="application/json" data-share-image-data[^>]*>(.*?)</script>', re.S)
_LABEL_RE = re.compile(r'"d":\s*"([^"]+)"')


def parse_detail(page: str, market: str) -> Detail:
    raw = _json_block(page, r'<script type="application/json" data-price-points[^>]*>(.*?)</script>')
    pts_raw = json.loads(raw)
    if not isinstance(pts_raw, list) or not pts_raw:
        raise VgError("型号无有效价格历史（样本不足？）")
    pts = []
    for p in pts_raw:
        try:
            cny = int(str(p.get("m", "")).replace("¥", "").replace(",", ""))
            pts.append(Point(t=str(p.get("t", "")), label=str(p.get("d", "")),
                             usd=float(p.get("u", 0)), cny=cny))
        except (ValueError, TypeError):
            continue
    if not pts:
        raise VgError("价格历史解析 0 点")
    share = {}
    try:
        share = json.loads(_json_block(page, _SHARE_RE.pattern))
    except VgError:
        pass
    name = str(share.get("name") or "")
    label = str((share.get("market") or {}).get("label") or MARKETS.get(market, market))
    price = pts[-1].cny
    return Detail(name=name, market=market, market_label=label,
                  points=pts, price_cny=price, fetched_at=now().isoformat(timespec="seconds"))


def fetch_detail(path: str, market: str = DEFAULT_MARKET,
                 force: bool = False) -> Detail | None:
    """抓单型号某市场的完整历史（带 TTL 缓存）。

    该市场无有效报价（VRAMGlass 显示「暂无报价」）时返回 ``None``——如实呈现，
    绝不伪造一个价。这是 VRAMGlass「样本<3 不发布」口径在本层的延伸。
    """
    if market not in MARKETS:
        raise VgError("未知市场：%s" % market)
    key = (path, market)
    with _cache_lock:
        hit = _detail_cache.get(key)
        if not force and hit and time.time() - hit[0] < DETAIL_TTL:
            return hit[1]
    url = BASE + path + ("?market=" + market if market != DEFAULT_MARKET else "")
    page = _get(url)
    if "暂无报价" in page and "data-share-image-data" not in page:
        with _cache_lock:
            _detail_cache[key] = (time.time(), None)
        return None
    detail = parse_detail(page, market)
    with _cache_lock:
        _detail_cache[key] = (time.time(), detail)
    return detail


# ---------------------------------------------------------------------------
# 大盘价格指数（/zh/index，视频「显卡价格指数」同款大盘视图）
# ---------------------------------------------------------------------------
@dataclass
class Index:
    name: str          # 大盘 / 入门 / 主力 / 旗舰
    value: float       # 当前指数（基期=100）
    change_pct: float  # 24h 涨跌（%）
    base: float = 100.0
    base_date: str = "2026-09-04"


@dataclass
class IndexBook:
    items: list[Index]
    updated: str       # 页面「价格更新于」文本
    source: str = "vramglass-index"


_index_cache: tuple[float, IndexBook] | None = None
INDEX_TTL = 30 * 60.0

_IDX_TITLE = {
    "显卡大盘指数": "大盘", "大盘指数": "大盘",
    "入门显卡指数": "入门", "主力显卡指数": "主力", "旗舰显卡指数": "旗舰",
}


def parse_indices(page: str) -> IndexBook:
    """从指数页文本提取每个指数的「标题 → 当前指数 → 24h 涨跌幅」。

    不用 data-price-points 的块顺序（块都在页首、与 section 分离，硬编码顺序会错），
    改为就近文本配对：在每个「当前指数」前 400 字里找标题，后 120 字找数值与涨跌。
    """
    found: list[Index] = []
    for m in re.finditer(r"当前指数", page):
        before = re.sub(r"<[^>]+>", " ", page[max(0, m.start() - 400):m.start()])
        # HTML 标签多，涨跌幅文本落在 140 字符之后；先截 600 再去标签
        after = re.sub(r"<[^>]+>", " ", page[m.start():m.start() + 600])
        # 取离「当前指数」最近的标题（before 里最后出现的那个），
        # 不能按字典顺序取第一个——紧凑文本下多个标题会在窗口里共存导致错配。
        name, best_pos = None, -1
        for title, short in _IDX_TITLE.items():
            pos = before.rfind(title)
            if pos > best_pos:
                best_pos, name = pos, short
        if not name:
            continue
        vm = re.search(r"当前指数\s*([\d.]+)", after)
        cm = re.search(r"([+-]?[\d.]+)\s*%", after)
        if not vm:
            continue
        val = float(vm.group(1))
        chg = float(cm.group(1)) if cm else 0.0
        if any(x.name == name for x in found):
            continue
        found.append(Index(name=name, value=val, change_pct=chg))
    if not found:
        raise VgError("指数页解析 0 条（页面改版？）")
    upd = ""
    um = re.search(r"价格更新于\s*([\d/]+\s*[\d:]+)", re.sub(r"<[^>]+>", " ", page))
    if um:
        upd = um.group(1).strip()
    return IndexBook(items=found, updated=upd)


def fetch_indices(force: bool = False) -> IndexBook:
    """抓四大盘指数（带 TTL 缓存）。"""
    global _index_cache
    with _cache_lock:
        if not force and _index_cache and time.time() - _index_cache[0] < INDEX_TTL:
            return _index_cache[1]
    book = parse_indices(_get(BASE + "/zh/index"))
    with _cache_lock:
        _index_cache = (time.time(), book)
    return book


def resolve_path(query: str, listing: Listing | None = None) -> list[Row]:
    """型号模糊查询（在首页表格的 name/path 上做，不等号则前缀/包含）。"""
    listing = listing or fetch_listing()
    q = re.sub(r"[\s\-_]", "", str(query or "")).lower()
    if not q:
        return []
    scored = []
    for r in listing.rows:
        n = re.sub(r"[\s\-_]", "", r.name).lower()
        p = re.sub(r"[\s\-_]", "", r.path.split("/")[-1]).lower()
        if n == q or p == q:
            sc = 0
        elif n.startswith(q) or p.startswith(q):
            sc = 1
        elif q in n or q in p:
            sc = 2
        else:
            continue
        scored.append((sc, len(n), r))
    scored.sort(key=lambda x: (x[0], x[1]))
    return [x[2] for x in scored]
