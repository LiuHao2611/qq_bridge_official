# -*- coding: utf-8 -*-
"""QQ 官方「素材管理」客户端：把图片字节上传成可在 Markdown 内嵌的引用。

为什么需要（2026-11-11，用户「图片要和按钮一起发」）：
  官方群消息的 ``msg_type`` 互斥——富媒体图片走 7、markdown+按钮走 2，
  一条消息无法既带 ``file_image`` 又带 ``keyboard``。要把「一张图 + 多个按钮」
  合成**一条**消息，唯一官方途径是：把图片先上传到素材库，换成
  ``qqbot-media://image/v1/<token>`` 引用，内嵌进 markdown content：
      ![ #800px #1019px](qqbot-media://image/v1/<token>)
  然后 markdown content 与 keyboard 同段下发（msg_type=2）。

官方素材管理 API（bot.q.qq.com/.../material-management.html，三步）：
  1. ``POST /v2/media-images/prepare``  申请上传 → upload_id + presigned_url
  2. ``PUT <presigned_url>``            直传图片二进制 → HTTP 200
  3. ``POST /v2/media-images/complete`` 完成 → url(qqbot-media://) + image_id
  引用：``![说明 #宽px #高px](<url>)``；单机器人上限 100 张、滑动 90 天过期。

🔴 第 2 步的真平台铁律（2026-11-11 真网络矩阵实验坐实，勿再改错）：
  * ``presigned_url`` 是腾讯云 COS 直传地址（q-sign-algorithm/q-signature 在
    URL query 里），**不是**官方 API 域名。PUT 时 ``Content-Type`` 头必须
    **逐字等于** prepare 申报的 ``content_type``，否则 COS 返回 403
    ``AccessDenied``。同一 URL 实测：无 CT→403、octet-stream→403、
    image/png→200、带官方鉴权头+image/png→200。故 CT 是唯一根因。
  * **绝不能**走 ``bot.put_upload_part`` / ``bot._request``：适配器 ``_request``
    会给每个请求注入 ``Authorization: QQBot <token>`` 并按官方 ``{code,...}``
    JSON 解析响应，而 COS 返回空 body+ETag，解析不了抛 ActionFailed——
    这正是上一版 ``put_failed: ActionFailed`` 每次降级成两条的根因。
    改用干净 httpx PUT（收口在 ``_http_put``，可 monkeypatch）。

适配器 nonebot-adapter-qq 1.7.2 **未封装** material-images API，故此处裸调：
  * prepare/complete 走 ``bot._request(Request(...))`` —— 自带 access_token
    鉴权且过期自动刷新（这俩是官方 API 域名，需要鉴权，适配器内部就这么用）；
  * PUT 直传走 ``_http_put`` 干净 httpx（COS 域名，带正确 Content-Type，不解析 JSON）。
不引入新网络依赖；PIL 仅用于解析图片宽高与格式（容器已有 pillow）。

降级契约：本模块任何失败都抛 :class:`MaterialUploadError`，由 ``official.py::send``
捕获后**回退原「图片条 + 按钮条」两条发送**，绝不让素材上传拖垮主卡片，也绝不
静默吞错（记 warning + 原因）。
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
from collections import OrderedDict
from pathlib import Path

try:  # 正常包内 import
    from ..config import get_logger, jlog
except (ImportError, ValueError):  # 单文件直载（离线单测）时兜底到纯 logging
    import logging

    def get_logger():  # type: ignore[no-redef]
        return logging.getLogger("qq_bridge.media_asset")

    def jlog(evt, **kw):  # type: ignore[no-redef]
        return {"event": evt, **kw}

log = get_logger()

#: 单机器人素材上限 100 张，留 8 张安全余量
MAX_MATERIALS = 92

#: 素材映射落盘（2026-11-17 用户最高声明「用户数据不许丢」的连带项）。
#: 原先只在内存：容器一重启，92 张已上传素材的 image_id 全忘，于是把同一批图
#: 重新上传——腾讯侧「90 天内被引用即续期」的配额很快被打满，直接表现为发图失败。
#: 存到 data 卷里即可跨重启复用；写盘失败只影响"下次是否重传"，故只报错不阻断。
def _cache_path() -> Path:
    """素材映射的落盘位置。必须与 ``../queue`` 的 DATA_DIR 同一处（data 卷）。

    延迟 import 是为了避免包初始化顺序问题；但离线单测会用
    ``importlib`` 把本文件**当顶层模块直载**（如 ``test_qq_button_merge``），
    此时没有父包，相对 import 必然 ImportError。故与文件顶部 ``..config``
    同款兜底：按同样的 parents[4] 规则自行推导项目根，保证单文件直载时
    也落在同一个 ``data/`` 目录，不会写进代码目录。
    """
    try:
        from ..queue import DATA_DIR  # 包内正常路径
    except (ImportError, ValueError):  # 单文件直载（离线单测）
        # 本文件比 qq_bridge/ 下的模块深一层，parents[4] 才是项目根
        # （与 queue/__init__.py::ROOT_DIR 口径一致），parents[3] 会算成 src/。
        DATA_DIR = Path(os.environ.get("QQ_DATA_DIR")
                        or (Path(__file__).resolve().parents[4] / "data"))
    return Path(DATA_DIR) / "media_assets.json"


class MaterialUploadError(RuntimeError):
    """素材上传任一步失败。上层据此降级回两条发送，不静默吞。"""


#: 网络瞬时失败的重试次数（2026-11-17 生产实测：容器 DNS 偶发解析失败/超时——
#: 冷缓存下 api.sgroup.qq.com 曾耗时 4.1s，cos.ap-beijing 直接 gaierror -3）。
#: 表现为 ``put_failed: ConnectError`` / ``prepare_failed: NetworkError``，
#: 用户侧看到的是「菜单只有图、没有按钮」。
#:
#: 🔴 只重试「连接没建立起来」这类**瞬时**错误。4xx/5xx、响应格式错误、403
#: Content-Type 不对 —— **一律不重试**：那是真拒绝/真 bug，重试只会掩盖问题，
#: 违反「禁止兜底」纪律。重试也不是降级：走的还是同一条实现，只是再试一次。
_NET_RETRY = 1
_NET_RETRY_DELAY = 0.4

#: 视为瞬时网络错误的异常名（httpx 的 ConnectError 里还包着 OSError/gaierror）
_TRANSIENT_NAMES = frozenset({
    "ConnectError", "ConnectTimeout", "ReadTimeout", "WriteTimeout",
    "NetworkError", "RemoteProtocolError", "PoolTimeout",
    "gaierror", "TimeoutError", "ConnectionResetError",
})


def _is_transient(e: BaseException, depth: int = 0) -> bool:
    if type(e).__name__ in _TRANSIENT_NAMES:
        return True
    cause = getattr(e, "__cause__", None) or getattr(e, "__context__", None)
    if cause is not None and cause is not e and depth < 4:
        return _is_transient(cause, depth + 1)
    return False


async def _retry_net(make_coro, step: str):
    """执行 ``make_coro()``，遇上瞬时网络错误重试 ``_NET_RETRY`` 次。

    用工厂而不是现成 coroutine：每次重试都要新建请求对象（适配器会往 Request
    上挂状态，复用同一个对象不安全）。
    """
    for attempt in range(_NET_RETRY + 1):
        try:
            return await make_coro()
        except Exception as e:  # noqa: BLE001
            if attempt >= _NET_RETRY or not _is_transient(e):
                raise
            log.info(jlog("material_net_retry", step=step,
                          error=type(e).__name__, attempt=attempt + 1))
            await asyncio.sleep(_NET_RETRY_DELAY)


class MaterialStore:
    """md5 → qqbot-media 引用 的缓存 + LRU 容量管理。

    缓存命中直接复用（官方素材 90 天内被引用即续期，重复图不必重传）。
    LRU：超过 MAX_MATERIALS 时删最久未用的（调 ``DELETE /v2/media-images/{
    image_id}/delete`` 释放配额），删除失败只记日志不影响本次发送。
    """

    def __init__(self, max_items: int = MAX_MATERIALS, path: Path | None = None):
        self.max_items = max(1, int(max_items))
        # md5 -> {"url":..., "width":..., "height":..., "image_id":...}
        self._items: OrderedDict[str, dict] = OrderedDict()
        # 路径延迟解析：避免 import 期就去 import ../queue（可能有初始化顺序问题）
        self._path: Path | None = Path(path) if path else None
        # 读盘也延迟到首次使用：模块级单例在 import 期就构造，若在此读盘等于
        # 每次 import 都做一次磁盘 IO，且会与 ../queue 形成 import 期循环依赖。
        self._loaded = False

    @property
    def path(self) -> Path:
        if self._path is None:
            self._path = _cache_path()
        return self._path

    def _ensure_loaded(self) -> None:
        if not self._loaded:
            self._loaded = True      # 先置位：_load 内部失败也不反复重试
            self._load()

    # ---- 落盘（缓存语义：失败只报错，不影响本次发送） ----
    def _load(self) -> None:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return
        except Exception as e:  # noqa: BLE001
            log.error(jlog("media_assets_load_failed", path=str(self.path),
                           error=str(e)[:120]))
            return
        if not isinstance(raw, dict):
            log.error(jlog("media_assets_bad_format", path=str(self.path)))
            return
        for md5, item in raw.items():
            if isinstance(item, dict):
                self._items[str(md5)] = item

    def _save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = str(self.path) + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(dict(self._items), f, ensure_ascii=False, separators=(",", ":"))
            os.replace(tmp, self.path)
        except Exception as e:  # noqa: BLE001
            # 缓存不是用户资产：不影响本次发送，但必须留痕（禁止静默）
            log.error(jlog("media_assets_save_failed", path=str(self.path),
                           error=str(e)[:120]))

    def get(self, md5: str) -> dict | None:
        self._ensure_loaded()
        if md5 not in self._items:
            return None
        self._items.move_to_end(md5)  # LRU：命中即最近使用
        return self._items[md5]

    def put(self, md5: str, item: dict) -> None:
        # 必须先读回已落盘的条目，否则新写入会把旧缓存整表覆盖掉（等于丢缓存）
        self._ensure_loaded()
        self._items[md5] = item
        self._items.move_to_end(md5)
        self._save()

    def _evict_candidates(self) -> list[dict]:
        """超出容量时最久未用的条目（含 image_id 供删除）。"""
        self._ensure_loaded()
        excess = len(self._items) - self.max_items
        if excess <= 0:
            return []
        evicted = []
        for _ in range(excess):
            md5, item = self._items.popitem(last=False)
            evicted.append(item)
        if evicted:
            self._save()
        return evicted

    def __len__(self) -> int:
        self._ensure_loaded()
        return len(self._items)


#: 进程级单例（官方 Bot 单连接，素材库按 AppID 全局）
_store = MaterialStore()


def _new_request(method: str, url, **kw):
    """构造 ``nonebot.drivers.Request``。

    收口到本函数、而非在三处端点里各写 ``from nonebot.drivers import Request``：
      * 生产：延迟导入（适配器缺失时不拖垮模块顶层导入）；
      * 测试：monkeypatch 本函数即可注入假 Request，**不污染 sys.modules**，
        宿主（无 nonebot）与容器（真 nonebot）共用同一套测试代码。
    """
    from nonebot.drivers import Request  # noqa: PLC0415

    return Request(method, url, **kw)


#: 容器出网 COS 预签名直传超时上限（CLOSE-05：给足但绝不无限等）
_PUT_TIMEOUT = 30.0

#: PIL format -> 官方 content_type。prepare 申报值与 PUT 的 Content-Type 头
#: **必须完全一致**（真网络实验 2026-11-11 实证：不一致 COS 返回 403
#: AccessDenied，一致才 200；见 _put_bytes 文档）。
_CONTENT_TYPES = {
    "PNG": "image/png",
    "JPEG": "image/jpeg",
    "GIF": "image/gif",
    "WEBP": "image/webp",
}


def image_meta(data: bytes) -> tuple[int, int, str]:
    """从图片字节解析 ``(宽, 高, content_type)``；失败抛 MaterialUploadError。

    content_type 由真实格式探测，供 prepare 申报与 PUT 头**共用同一个值**。
    """
    try:
        from io import BytesIO

        from PIL import Image  # noqa: PLC0415 — 容器已装 pillow

        with Image.open(BytesIO(data)) as im:
            fmt = str(getattr(im, "format", "") or "").upper()
            return int(im.width), int(im.height), _CONTENT_TYPES.get(fmt, "image/png")
    except Exception as e:  # noqa: BLE001
        raise MaterialUploadError("image_size_failed: %s" % type(e).__name__) from e


def image_size(data: bytes) -> tuple[int, int]:
    """兼容旧调用：只取 (宽, 高)。"""
    w, h, _ = image_meta(data)
    return w, h


async def _prepare(bot, file_name: str, data: bytes, content_type: str) -> dict:
    """POST /v2/media-images/prepare → {upload_id, presigned_url, ...}。

    ``content_type`` 必须与后续 PUT 的 ``Content-Type`` 头逐字一致，否则 COS
    预签名校验失败（403 AccessDenied）——2026-11-11 真网络矩阵实验坐实。
    """
    base = bot.adapter.get_api_base()

    def _make_req():
        # 每次重试都新建 Request（适配器会往对象上挂状态，复用不安全）
        return _new_request(
            "POST",
            base.joinpath("v2", "media-images", "prepare"),
            json={
                "file_name": file_name,
                "file_size": len(data),
                "content_type": content_type,
                "md5": hashlib.md5(data).hexdigest(),
                "sha1": hashlib.sha1(data).hexdigest(),
            },
        )

    try:
        ret = await _retry_net(lambda: bot._request(_make_req()), "prepare")
    except Exception as e:  # noqa: BLE001
        raise MaterialUploadError("prepare_failed: %s" % type(e).__name__) from e
    if not isinstance(ret, dict) or not ret.get("upload_id") or not ret.get("presigned_url"):
        raise MaterialUploadError("prepare_bad_response: missing upload_id/presigned_url")
    return ret


async def _http_put(url: str, data: bytes, content_type: str) -> int:
    """干净 HTTP PUT 到 COS 预签名地址，返回 status_code。

    ⚠️ **绝不能**走 ``bot.put_upload_part`` / ``bot._request``：那条路径会把
    ``Authorization: QQBot <token>`` 按官方 API JSON 解析响应，而 COS 预签名
    直传返回空 body + ETag，解析不了会抛 ActionFailed（2026-11-11 生产日志
    ``put_failed: ActionFailed`` 的直接来源）。

    ⚠️ ``Content-Type`` 头必须**逐字等于** prepare 申报的 content_type：真网络
    矩阵实验（同一 presigned_url）：不带 CT→403、application/octet-stream→403、
    image/png→200、带官方鉴权头+image/png→200。故 CT 是唯一根因，鉴权头无害。
    收口成本函数、测试可 monkeypatch 注入假响应，不碰真实网络、不污染 sys.modules。
    """
    import httpx  # noqa: PLC0415 — official.py 已依赖，容器有 0.28.x

    async with httpx.AsyncClient(timeout=_PUT_TIMEOUT) as client:
        resp = await client.put(url, content=data, headers={"Content-Type": content_type})
    return int(getattr(resp, "status_code", 0))


async def _put_bytes(presigned_url: str, data: bytes, content_type: str) -> str:
    """PUT 预签名 URL 直传字节 → 成功返回 md5，失败抛 MaterialUploadError。

    ``content_type`` 由 ``ensure_material`` 从图片字节探测，与 prepare 申报值
    同源。COS 单块 PUT 的 ETag 就是内容 md5，官方 complete 以 COS 实际 ETag
    比对，故这里返回本地 md5。
    """
    try:
        status = await _retry_net(
            lambda: _http_put(presigned_url, data, content_type), "put")
    except MaterialUploadError:
        raise                      # 状态码不对：真拒绝，不重试也不改写
    except Exception as e:  # noqa: BLE001
        raise MaterialUploadError("put_failed: %s" % type(e).__name__) from e
    if not (200 <= status < 300):
        raise MaterialUploadError("put_bad_status: %d (content_type=%s)" % (status, content_type))
    return hashlib.md5(data).hexdigest()


async def _complete(bot, upload_id: str, md5_hex: str) -> dict:
    """POST /v2/media-images/complete → {url, image_id, ...}。"""
    base = bot.adapter.get_api_base()

    def _make_req():
        return _new_request(
            "POST",
            base.joinpath("v2", "media-images", "complete"),
            json={"upload_id": upload_id, "md5": md5_hex},
        )

    async def _go():
        return await _retry_net(lambda: bot._request(_make_req()), "complete")

    try:
        ret = await _go()          # 瞬时网络错误重试一次（见 _retry_net 说明）
    except Exception as e:  # noqa: BLE001
        raise MaterialUploadError("complete_failed: %s" % type(e).__name__) from e
    if not isinstance(ret, dict) or not ret.get("url"):
        raise MaterialUploadError("complete_bad_response: missing url")
    return ret


async def _delete(bot, image_id: str) -> None:
    """DELETE /v2/media-images/{image_id}/delete（LRU 释放配额），失败只记日志。"""
    try:
        base = bot.adapter.get_api_base()
        # 官方素材删除是 DELETE /v2/media-images/{image_id}/delete（不是 POST）。
        # 2026-11-11 静态核对修正：误用 POST 会拿 404/405，LRU 配额释放不掉。
        req = _new_request("DELETE", base.joinpath("v2", "media-images", image_id, "delete"))
        await bot._request(req)
    except Exception as e:  # noqa: BLE001
        log.warning(jlog("material_lru_delete_failed", error=type(e).__name__,
                         image_id=str(image_id)[:24]))


async def ensure_material(bot, data: bytes, file_name: str = "card.png") -> dict:
    """确保图片字节在素材库有引用，返回 ``{"url",width",height",md5"}``。

    缓存命中不上传；未命中走 prepare→PUT→complete 三步。任一失败抛
    :class:`MaterialUploadError`（由上层降级，不静默吞）。成功后做 LRU 淘汰。
    """
    if not data:
        raise MaterialUploadError("empty_image_bytes")
    md5 = hashlib.md5(data).hexdigest()
    cached = _store.get(md5)
    if cached:
        return {**cached, "md5": md5, "cached": True}

    width, height, content_type = image_meta(data)
    prepared = await _prepare(bot, file_name, data, content_type)
    md5_hex = await _put_bytes(prepared["presigned_url"], data, content_type)
    completed = await _complete(bot, prepared["upload_id"], md5_hex)
    item = {
        "url": completed["url"],
        "image_id": str(completed.get("image_id") or ""),
        "width": width,
        "height": height,
    }
    _store.put(md5, item)
    for old in _store._evict_candidates():
        if old.get("image_id"):
            await _delete(bot, old["image_id"])
    log.info(jlog("material_uploaded", md5=md5[:12], width=width, height=height,
                  cached_store=len(_store)))
    return {**item, "md5": md5, "cached": False}


def markdown_image(url: str, width: int, height: int, alt: str = "card") -> str:
    """构造官方 Markdown 内嵌图片语法：``![alt #宽px #高px](url)``。"""
    return "![%s #%dpx #%dpx](%s)" % (alt, int(width), int(height), url)


def reset_store() -> None:
    """清空内存缓存（仅供测试隔离）。"""
    _store._items.clear()
    # 标记已读：否则下一次 get/put 会再从磁盘把条目读回来，reset 等于没做
    _store._loaded = True
