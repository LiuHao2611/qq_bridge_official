# -*- coding: utf-8 -*-
"""素材上传热路径守门（2026-11-26 出卡提速·第二版）。

真机实测（同一台手机、同一个接口、连续三张卡）：

    prepare_ms   637 / 1606 / 3262     ← 唯一在抖的段
    put_ms       348 /  339 /  339     ← 恒定：170KB 上行 ≈ 0.34s，压图无收益
    complete_ms 1040 / 1040 / 1120     ← 恒定：不是网络抖动，是固定结构开销
                                         （一次 complete POST + 一次 LRU DELETE）

两条改动各配一条守门，改回去立刻红：

  A. **LRU 淘汰的 DELETE 不许阻塞出图**。``MAX_MATERIALS=92`` 且常用态就在 92 张，
     意味着每张新卡必然淘汰一张旧卡 → 每张冷卡白等约 1.0s。删的是「上一张旧卡」，
     玩家等的是「这一张新卡」，没有理由同步等。

  B. **prepare 超阈值必须对冲补发**（削 3 秒级尾延迟）；快路径不许发第二个，
     否则平白多一次请求、多一个 upload_id。

运行：``python tests/test_media_hotpath.py``（也兼容 pytest）
"""
from __future__ import annotations

import asyncio
import importlib.util
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
MA = REPO / "src/plugins/qq_bridge/channel/media_asset.py"


def _load():
    spec = importlib.util.spec_from_file_location("_ma_hotpath", str(MA))
    m = importlib.util.module_from_spec(spec)
    sys.modules["_ma_hotpath"] = m
    spec.loader.exec_module(m)
    return m


ma = _load()

_PNG = b"\x89PNG\r\n\x1a\n" + b"x" * 100


class _Adapter:
    def get_api_base(self):
        return Path("/api")


class _Req:
    def __init__(self, method, url, **kw):
        self.method = method
        # Path.joinpath 在 Windows 上产出反斜杠，统一成正斜杠再匹配（跨平台）
        self.raw_url = str(url).replace("\\", "/")


class _Bot:
    """假 adapter：只有 prepare / complete / delete 三个端点。

    ``prepare_delays`` 按调用序号取延迟，用尽后 0 —— 这样才能构造「第一次慢、
    第二次快」的对冲场景（全部一样慢时对冲无意义，也不该被断言为更快）。
    """

    def __init__(self, prepare_delays=(), delete_delay=0.0):
        self.adapter = _Adapter()
        self.prepare_calls = 0
        self.deleted = []
        self.prepare_delays = list(prepare_delays)
        self.delete_delay = delete_delay

    async def _request(self, req):
        if req.method == "POST" and "media-images/prepare" in req.raw_url:
            self.prepare_calls += 1
            delay = self.prepare_delays.pop(0) if self.prepare_delays else 0.0
            if delay:
                await asyncio.sleep(delay)
            return {"upload_id": "up_%d" % self.prepare_calls,
                    "presigned_url": "https://cos/put"}
        if req.method == "POST" and "media-images/complete" in req.raw_url:
            return {"url": "qqbot-media://image/v1/T", "image_id": "new"}
        if req.method == "DELETE" and "/delete" in req.raw_url:
            iid = req.raw_url.rstrip("/").rsplit("/", 2)[-2]
            if self.delete_delay:
                await asyncio.sleep(self.delete_delay)
            self.deleted.append(iid)
            return {}
        raise AssertionError("unexpected %s %s" % (req.method, req.raw_url))


class _Wire:
    """替换 _new_request/_store/image_meta/_http_put；退出逐字恢复，零残留。"""

    def __init__(self, store):
        self.store = store

    def __enter__(self):
        self.orig = (ma._new_request, ma._store, ma.image_meta, ma._http_put,
                     ma._PENDING_DELETE, ma._drain_task)
        ma._new_request = lambda method, url, **kw: _Req(method, url, **kw)
        ma._store = self.store
        ma.image_meta = lambda data: (6, 4, "image/png")
        ma._http_put = lambda url, data, ct: asyncio.sleep(0, result=200)
        ma._PENDING_DELETE = set()
        ma._drain_task = None
        return self.store

    def __exit__(self, *_):
        (ma._new_request, ma._store, ma.image_meta, ma._http_put,
         ma._PENDING_DELETE, ma._drain_task) = self.orig


def _store(max_items: int, keep: str | None = None):
    d = tempfile.mkdtemp(prefix="ma_hotpath_")
    st = ma.MaterialStore(path=Path(d) / "media_assets.json", max_items=max_items)
    if keep:
        st.put("md5_old", {"image_id": keep, "url": "u", "width": 6, "height": 4})
    return st


def _run(bot, data=_PNG):
    async def go():
        return await ma.ensure_material(bot, data, "card.png")
    return asyncio.run(go())


def _run_settle(bot, data=_PNG, wait: float = 0.05):
    """跑完后给事件循环一个执行窗口，让后台清理任务真正跑起来（生产里循环常在）。"""
    async def go():
        item = await ma.ensure_material(bot, data, "card.png")
        await asyncio.sleep(wait)
        return item
    return asyncio.run(go())


# ---------------------------------------------------------------------------
# A. LRU 删除不许卡热路径
# ---------------------------------------------------------------------------
def test_lru_delete_does_not_block_upload():
    """库满时上传仍要**立刻**返回；那一次 DELETE 必须留给后台，不许当场 await。"""
    bot = _Bot(delete_delay=1.5)
    store = _store(max_items=1, keep="old1")
    assert len(store) == 1
    with _Wire(store):
        t0 = time.time()
        item = _run(bot)
        dt = time.time() - t0
        assert item.get("url") == "qqbot-media://image/v1/T", item
        assert dt < 0.6, "出图被 LRU 删除阻塞了 %.2fs（应 <0.6s）" % dt
        # asyncio.run 结束会取消后台任务 → 删除未完成，但 id 必须留在队列里待下次清
        assert "old1" in ma._PENDING_DELETE, \
            "淘汰的 image_id 丢了（既没当场删、也没进待删队列）：%s" % ma._PENDING_DELETE


def test_pending_delete_drained_on_next_upload():
    """上次遗留的待删 id，下次上传时必须被真正清掉（不许无声堆积成孤儿）。"""
    bot = _Bot(delete_delay=0.2)      # 让第一次的清理任务来不及跑完（模拟进程重启）
    store = _store(max_items=1, keep="old1")
    with _Wire(store):
        _run(bot)                                   # 第一次：排队 old1 后循环被关
        # 合法终态只有两种：已经删掉（循环还活着）或留在待删队列（循环已关）。
        # 既没删又没排队 = 静默泄漏配额，正是这条要钉死的。
        assert ("old1" in bot.deleted) or ("old1" in ma._PENDING_DELETE), \
            "淘汰的素材既没删也没排队：deleted=%s pending=%s" % (
                bot.deleted, ma._PENDING_DELETE)
        bot.deleted.clear()
        # 第二次：新卡顶掉第一张 → 触发清理任务，循环活到它把队列清空
        _run_settle(bot, b"\x89PNG\r\n\x1a\n" + b"y" * 100, wait=0.9)
        assert "old1" in bot.deleted, \
            "上次遗留的待删素材没被清理：deleted=%s pending=%s" % (
                bot.deleted, ma._PENDING_DELETE)


def test_no_evict_no_pending():
    """库没满时不许凭空产生删除任务（零开销路径）。"""
    bot = _Bot()
    store = _store(max_items=5)
    with _Wire(store):
        _run(bot)
        assert not ma._PENDING_DELETE
        assert ma._drain_task is None


# ---------------------------------------------------------------------------
# B. prepare 对冲
# ---------------------------------------------------------------------------
def test_prepare_hedges_when_first_attempt_is_slow():
    """第一次 3.0s、第二次 0s → 整体应 ≈1.2s 回来，且确实补发了第二次。"""
    bot = _Bot(prepare_delays=(3.0, 0.0))
    store = _store(max_items=5)
    with _Wire(store):
        t0 = time.time()
        item = _run(bot)
        dt = time.time() - t0
    assert item.get("url") == "qqbot-media://image/v1/T", item
    assert bot.prepare_calls == 2, "没有对冲补发，实际调用 %d 次" % bot.prepare_calls
    assert dt < 2.0, "对冲没生效：%.2fs（应 ≈1.2s）" % dt


def test_prepare_fast_path_does_not_hedge():
    """快路径（远低于阈值）**不许**多发一次请求 —— 否则平白多占一个 upload_id。"""
    bot = _Bot(prepare_delays=(0.0, 0.0))
    store = _store(max_items=5)
    with _Wire(store):
        _run(bot)
    assert bot.prepare_calls == 1, "快路径多发了请求：%d 次" % bot.prepare_calls


def test_both_attempts_fail_still_raises():
    """两个对冲都失败时必须如实抛出，不许假装成功（禁止静默兜底）。"""

    class _Boom(_Bot):
        async def _request(self, req):
            if req.method == "POST" and "media-images/prepare" in req.raw_url:
                self.prepare_calls += 1
                await asyncio.sleep(0)
                raise RuntimeError("boom")
            return await super()._request(req)

    bot = _Boom()
    store = _store(max_items=5)
    with _Wire(store):
        try:
            _run(bot)
        except ma.MaterialUploadError:
            pass
        else:
            raise AssertionError("prepare 全失败却返回了成功")


def _main() -> int:
    fails = []
    for name, fn in sorted(globals().items()):
        if not name.startswith("test_") or not callable(fn):
            continue
        try:
            fn()
            print("ok    %s" % name)
        except Exception as e:  # noqa: BLE001
            fails.append("%s: %s" % (name, e))
            print("FAIL  %s: %s" % (name, e))
    print("=" * 70)
    print("HOTPATH_DONE fails=%d" % len(fails))
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(_main())
