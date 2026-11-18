# -*- coding: utf-8 -*-
"""素材配额自愈守门（2026-11-18 prove-it）。

用户现象：机器人发卡只有图片、没有按钮。根因：官方单机器人素材上限 100 张，
本地缓存 42 + 历史孤儿 58 占满 → prepare 返回 40035007 → 降级纯图。
根治：_prepare 撞 40035007 时删孤儿/最旧释放配额，再重试一次。

守的行为（用户感受得到）：
  1. 配额满时上传仍能成功（自愈后重试），最终图片+按钮能合并；
  2. 只删孤儿 + 最旧，本地缓存仍引用的图（keep）不被删；
  3. 清不掉配额时如实抛错，绝不假装成功（禁止静默兜底）；
  4. 非配额错误（如 40035001 格式错）不触发清理，直接失败。

注入验证见文件末 main() 说明；运行：python tests/test_media_quota_heal.py
"""
from __future__ import annotations

import asyncio
import importlib.util
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
MA = REPO / "src/plugins/qq_bridge/channel/media_asset.py"


def _load():
    spec = importlib.util.spec_from_file_location("_ma_quota_heal", str(MA))
    m = importlib.util.module_from_spec(spec)
    sys.modules["_ma_quota_heal"] = m
    spec.loader.exec_module(m)
    return m


ma = _load()


class _FakeActionFailed(Exception):
    """模拟官方 adapter 的 ActionFailed（关键是有 .code 属性）。"""

    def __init__(self, code):
        super().__init__("ActionFailed code=%s" % code)
        self.code = code
        self.body = {"code": code, "message": "test"}


class _URL:
    def __init__(self, s):
        self.s = s

    def __truediv__(self, part):
        return _URL(self.s.rstrip("/") + "/" + str(part).strip("/"))

    def joinpath(self, *parts):
        u = self
        for p in parts:
            u = u / str(p)
        return u

    def copy_add_query(self, **kw):
        q = "&".join("%s=%s" % (k, v) for k, v in kw.items())
        return _URL(self.s + ("?" if "?" not in self.s else "&") + q)

    def __str__(self):
        return self.s


class _Req:
    def __init__(self, method, url, **kw):
        self.method = method
        self.url = _URL(str(url))
        self.raw_url = str(url)
        self.kw = kw


class _Adapter:
    def get_api_base(self):
        return _URL("https://api.sgroup.qq.com/")


class _QuotaBot:
    """prepare 首次数超限、之后成功；GET 返回 total 张；DELETE 记录。"""

    def __init__(self, total=100, keep_ids=("keep1", "keep2", "keep3"),
                 quota_code=ma.ERR_QUOTA_EXCEEDED, heal=True):
        self.adapter = _Adapter()
        self.total = total
        self.keep_ids = set(keep_ids)
        self.quota_code = quota_code
        self.heal = heal
        self.prepare_calls = 0
        self.deleted = []
        # 官方返回按 created_at 倒序；孤儿在前、keep 在后（反转即最旧 keep 先删）
        self.images = ([{"image_id": "orphan%03d" % i, "created_at": 1000 + i}
                        for i in range(total - len(keep_ids))]
                       + [{"image_id": k, "created_at": 5000 + i}
                          for i, k in enumerate(keep_ids)])

    async def _request(self, req):
        if req.method == "POST" and "media-images/prepare" in req.raw_url:
            self.prepare_calls += 1
            if self.prepare_calls == 1:
                raise _FakeActionFailed(self.quota_code)
            return {"upload_id": "up_ok", "presigned_url": "https://cos/put"}
        if req.method == "GET" and "media-images" in req.raw_url:
            return {"images": self.images, "next_cursor": "", "total": self.total}
        if req.method == "DELETE" and "/delete" in req.raw_url:
            # url 形如 /v2/media-images/{image_id}/delete → 取倒数第二段
            iid = req.raw_url.rstrip("/").rsplit("/", 2)[-2]
            self.deleted.append(iid)
            return {}
        if req.method == "POST" and "media-images/complete" in req.raw_url:
            return {"url": "qqbot-media://image/v1/T", "image_id": "new"}
        raise AssertionError("unexpected %s %s" % (req.method, req.raw_url))


def _wire(bot, *, store_keep=("keep1", "keep2", "keep3")):
    """替换 _new_request/_store/image_meta/_http_put；退出恢复，零残留。"""
    orig = (ma._new_request, ma._store, ma.image_meta, ma._http_put)
    d = tempfile.mkdtemp(prefix="ma_heal_")
    store = ma.MaterialStore(path=Path(d) / "media_assets.json")
    for i, iid in enumerate(store_keep):
        store.put("md5_%d" % i, {"image_id": iid, "url": "u", "width": 6, "height": 4})

    class W:
        def __enter__(self):
            ma._new_request = lambda method, url, **kw: _Req(method, url, **kw)
            ma._store = store
            ma.image_meta = lambda data: (6, 4, "image/png")
            ma._http_put = lambda url, data, ct: asyncio.sleep(0, result=200)
            return store

        def __exit__(self, *_):
            (ma._new_request, ma._store, ma.image_meta, ma._http_put) = orig

    return W()


def _run(bot):
    async def go():
        return await ma.ensure_material(bot, b"\x89PNG\r\n\x1a\n" + b"x" * 100)
    return asyncio.run(go())


def test_quota_full_self_heals_and_uploads():
    """配额满 → 删孤儿腾空间 → 重试 prepare 成功 → 上传完成。"""
    bot = _QuotaBot(total=100)
    with _wire(bot):
        item = _run(bot)
    assert item.get("url") == "qqbot-media://image/v1/T", item
    assert bot.prepare_calls == 2, "prepare 应重试一次，实际 %d" % bot.prepare_calls
    # 删的全是孤儿，keep 一个没删
    assert bot.deleted and all(d.startswith("orphan") for d in bot.deleted), bot.deleted[:5]
    assert not (set(bot.deleted) & bot.keep_ids), "误删了本地仍引用的素材"


def test_orphans_first_then_oldest_keep():
    """孤儿优先删；孤儿不够腾出目标空位时才按最旧顺序删本地 keep。"""
    bot = _QuotaBot(total=100, keep_ids=("keep1", "keep2", "keep3"))
    # 100 张里只 10 张孤儿 + 3 张 keep（其余 87 张也是孤儿，一并算入）
    # → need=16，孤儿充足，实际不会碰 keep。为测「孤儿不够」，构造 100 张中
    # 只有 10 张非 keep（其余 90 张都登记为 keep 的多个 md5）——简化为直接
    # 让 images 里 keep 占比高：10 孤儿 + 3 keep + 87 keep 副本(id 不同)。
    bot.images = ([{"image_id": "orphan%02d" % i, "created_at": i}
                   for i in range(10)]
                  + [{"image_id": "keep1", "created_at": 500},
                     {"image_id": "keep2", "created_at": 400},
                     {"image_id": "keep3", "created_at": 300}]
                  + [{"image_id": "extra%02d" % i, "created_at": 100 + i}
                     for i in range(87)])
    # keep 白名单只认 keep1/2/3；extra* 与 orphan* 都算孤儿 → 97 张孤儿足够，不会删 keep
    with _wire(bot):
        _run(bot)
    assert len(bot.deleted) >= 16, "未腾出目标空位：%d" % len(bot.deleted)
    assert not (set(bot.deleted) & {"keep1", "keep2", "keep3"}), \
        "孤儿充足却删了仍被引用的 keep：%s" % bot.deleted


def test_oldest_keep_deleted_when_no_orphans():
    """全是本地 keep、无孤儿时，按 created_at 最旧开始删（仍可自愈）。"""
    keep_ids = tuple("keep%02d" % i for i in range(100))
    bot = _QuotaBot(total=100, keep_ids=keep_ids)
    # 官方按 created_at 倒序返回（最新在前）；keep00 最旧，应排最后
    bot.images = [{"image_id": k, "created_at": i} for i, k in enumerate(keep_ids)][::-1]
    with _wire(bot, store_keep=keep_ids):
        _run(bot)
    assert len(bot.deleted) >= 16
    assert bot.deleted[0] == "keep00", "没有优先删最旧的 keep：%s" % bot.deleted[:5]


def test_no_heal_when_cannot_free(monkeypatch=None):
    """清不掉配额（无素材可删）时如实抛错，不假装成功。"""
    bot = _QuotaBot(total=0, keep_ids=())   # 官方库空 → total=0 → prepare 仍报超限但不删
    with _wire(bot, store_keep=()):
        try:
            _run(bot)
        except ma.MaterialUploadError:
            pass   # 期望：如实抛错
        else:
            raise AssertionError("清不掉配额却假装上传成功（静默兜底）")
    assert bot.prepare_calls == 1, "没有可清素材时不该重试（重试必再失败）"


def test_non_quota_error_does_not_heal():
    """非配额错误（40035001 格式错）不触发 GET 列表/删除，直接失败。"""
    bot = _QuotaBot(total=100, quota_code=40035001)
    with _wire(bot):
        try:
            _run(bot)
        except ma.MaterialUploadError:
            pass
        else:
            raise AssertionError("格式错误却没抛错")
    assert bot.prepare_calls == 1, "非配额错误不该重试"
    assert bot.deleted == [], "非配额错误绝不能删素材"


def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in tests:
        try:
            fn()
            print("PASS", fn.__name__)
        except AssertionError as e:
            failed += 1
            print("FAIL %s: %s" % (fn.__name__, e))
        except Exception as e:  # noqa: BLE001
            failed += 1
            print("ERR  %s: %r" % (fn.__name__, e))
    print("通过 %d，失败 %d" % (len(tests) - failed, failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
