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
import time
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
#: 官方硬上限（bot.q.qq.com 素材管理文档：单机器人默认 100 张），prepare/complete
#: 超限返回 40035007。本地 MAX_MATERIALS=92 只管「自己缓存的图」，管不到官方库里
#: 别的模块/历史遗留的孤儿素材——2026-11-18 实测官方 100/100（本地仅 42，58 张
#: 孤儿无人清）导致所有新图 prepare 失败、降级成纯图片没按钮。故加配额自愈。
OFFICIAL_QUOTA = 100
#: 自愈清理后至少腾出的空位（避免清一张立刻又被并发上传填满）
QUOTA_VACANCY_TARGET = 16
#: 服务端「图片数超限」错误码
ERR_QUOTA_EXCEEDED = 40035007
#: 全局并发锁：同一机器人多个并发 prepare 同时撞配额时，只允许一个在清理，
#: 避免重复删除/竞态。进程级单例，与 _store 同生命周期。
_quota_lock = asyncio.Lock()

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

#: prepare 单独放宽到 2 次（2026-11-26 线上：一次 30s 窗口里 5 次 prepare 全撞 5s
#: connect 超时 → 整张卡发不出去）。依据有两条：
#:   * prepare **幂等**——只为拿 presigned_url，未 complete 的上传任务 4 小时后自然
#:     失效、不占配额（官方「素材管理」文档），多试几次没有副作用；
#:   * 连接阶段的失败请求**没到服务端**，不存在「重复创建上传任务」的代价。
#: 实测多一次尝试就能跨过这种秒级的坏边窗口。PUT 不放宽：单次总超时 30s。
_PREPARE_RETRIES = 2

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


async def _retry_net(make_coro, step: str, retries: int | None = None):
    """执行 ``make_coro()``，遇上瞬时网络错误重试 ``retries`` 次（默认 ``_NET_RETRY``）。

    用工厂而不是现成 coroutine：每次重试都要新建请求对象（适配器会往 Request
    上挂状态，复用同一个对象不安全）。

    ``retries`` 仅供**幂等**步骤放宽（2026-11-26：prepare 给 2 次）。PUT 保持 1 次——
    它单次总超时就 30s，再多几次会把被动回复窗口吃光。
    """
    n = _NET_RETRY if retries is None else max(0, int(retries))
    for attempt in range(n + 1):
        try:
            return await make_coro()
        except Exception as e:  # noqa: BLE001
            if attempt >= n or not _is_transient(e):
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

    def known_image_ids(self) -> set[str]:
        """本地缓存登记的全部 image_id（配额自愈时用作「非孤儿」白名单）。"""
        self._ensure_loaded()
        return {str(v.get("image_id")) for v in self._items.values()
                if v.get("image_id")}

    def forget_image_id(self, image_id: str) -> None:
        """按 image_id 摘除本地缓存条目（官方侧已删除时同步，防误引用失效引用）。"""
        self._ensure_loaded()
        for md5, item in list(self._items.items()):
            if str(item.get("image_id") or "") == str(image_id):
                self._items.pop(md5, None)
        self._save()

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
_PUT_TOTAL_TIMEOUT = 30.0
#: **连接阶段**单独压到 3s（2026-11-26 实测）。
#: 线上慢卡证据：``23:26:06 pic_render`` → ``23:26:11 material_net_retry step=prepare
#: NetworkError`` → ``23:26:17 material_net_retry step=put ConnectError`` →
#: ``23:26:19 material_uploaded``，一张图 13s，其中 11s 全是「连不上」在等系统
#: TCP 超时；而 0.4s 后的重试一次就通。故把 connect 单独压短：最坏路径从
#: 「等满系统超时再重试」变成「3s 认输 + 0.4s 重试」，直接砍掉两位数的秒数。
#: 注意只压 connect —— 上传本身要传 150~350KB，总超时仍留 30s 给弱网上行。
_PUT_CONNECT_TIMEOUT = 3.0

#: prepare 对冲阈值（秒）：一次 ``POST /v2/media-images/prepare`` 超过这么久没回，
#: 就**并发补发第二个**，谁先回用谁。
#:
#: 依据 2026-11-26 真机三样本：prepare_ms = 637 / 1606 / 3262，而同一时刻的
#: put_ms = 348 / 339 / 339（纹丝不动）。带宽与算力都被排除，剩下的只能是那一次
#: 往返的**尾延迟**（TLS 重连 / 服务端排队）。尾延迟用「重试」治不好——超时时间
#: 一到就已经输了；用「对冲（hedged request）」治得好：把 P99 钉在阈值附近，
#: 代价是偶尔多一个 upload_id。
#:
#: 多出来的 upload_id 不占配额：官方配额算的是 ``complete`` 之后产生的 image_id，
#: 未 complete 的上传任务 4 小时后自然失效（官方「素材管理」文档）。
_PREPARE_HEDGE_AFTER = 1.2

#: 复用同一个 AsyncClient：每张卡都新建 client 等于每次都重做一遍 DNS+TCP+TLS
#: （手机 ARM 上 0.2~0.6s/张）。按 **事件循环** 持有，避免测试里
#: ``asyncio.run()`` 反复起停导致「拿到上一条已关闭的 loop 的 client」。
_put_client = None
_put_client_loop = None


def _put_client_for(loop):
    """取当前事件循环专属的 PUT 客户端（惰性建、跨请求复用连接）。"""
    global _put_client, _put_client_loop
    if _put_client is None or _put_client.is_closed or _put_client_loop is not loop:
        import httpx  # noqa: PLC0415 — 与 _http_put 同源，避免模块级硬依赖
        _put_client = httpx.AsyncClient(
            timeout=httpx.Timeout(_PUT_TOTAL_TIMEOUT, connect=_PUT_CONNECT_TIMEOUT),
        )
        _put_client_loop = loop
    return _put_client

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


def _err_code(e: BaseException) -> int | None:
    """从各类异常里尽力取出官方错误码，用于识别配额触顶 40035007。

    ActionFailed 有 .code 属性；httpx/其他包装层可能把 code 藏在 .body 或消息里。
    取不到返回 None——调用方据此决定是否触发配额自愈，不靠字符串猜。
    """
    for attr in ("code", "err_code"):
        v = getattr(e, attr, None)
        if isinstance(v, int):
            return v
        if isinstance(v, str) and v.isdigit():
            return int(v)
    body = getattr(e, "body", None)
    if isinstance(body, dict):
        v = body.get("code") or body.get("err_code")
        if isinstance(v, int):
            return v
    return None


async def _prepare_hedged(attempt):
    """首发 + 超时对冲：先发一个；超过 ``_PREPARE_HEDGE_AFTER`` 未回就并发补发第二个，
    取**先成功**的那个，另一个取消。两个都失败则抛最后一个错误（交给上层判配额）。

    ``attempt`` 是无参协程工厂（内部已含 ``_retry_net``）。用 ``ensure_future`` 而不是
    直接传协程给 ``asyncio.wait``：Python 3.12+ 已禁止后者（裸协程会 TypeError）。
    """
    first = asyncio.ensure_future(attempt())
    second = None
    try:
        done, _ = await asyncio.wait({first}, timeout=_PREPARE_HEDGE_AFTER)
        if done:
            return first.result()      # 快路径：1.2s 内回来，行为与改造前完全一致
        log.warning(jlog("material_prepare_hedged", after_ms=int(_PREPARE_HEDGE_AFTER * 1000)))
        second = asyncio.ensure_future(attempt())
        pending = {first, second}
        last_err = None
        while pending:
            done, pending = await asyncio.wait(
                pending, return_when=asyncio.FIRST_COMPLETED)
            for t in done:
                try:
                    return t.result()
                except Exception as e:   # noqa: BLE001 — 一个输了看另一个
                    last_err = e
        raise last_err or RuntimeError("prepare_hedge_no_result")
    finally:
        for t in (first, second):
            if t is not None and not t.done():
                t.cancel()


async def _prepare_once(bot, file_name: str, data: bytes, content_type: str) -> dict:
    """单次 POST /v2/media-images/prepare；不含配额自愈。"""
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

    async def _attempt():
        return await _retry_net(lambda: bot._request(_make_req()), "prepare",
                                retries=_PREPARE_RETRIES)

    try:
        ret = await _prepare_hedged(_attempt)
    except Exception as e:  # noqa: BLE001
        # 日志带上真实错误码/消息（旧实现只记类名，把 40035007 藏起来，排障全靠猜）
        log.warning(jlog("material_prepare_failed", error=type(e).__name__,
                         code=_err_code(e), detail=str(e)[:160]))
        raise MaterialUploadError("prepare_failed: %s(code=%s)"
                                  % (type(e).__name__, _err_code(e))) from e
    if not isinstance(ret, dict) or not ret.get("upload_id") or not ret.get("presigned_url"):
        raise MaterialUploadError("prepare_bad_response: missing upload_id/presigned_url")
    return ret


async def _prepare(bot, file_name: str, data: bytes, content_type: str) -> dict:
    """POST /v2/media-images/prepare → {upload_id, presigned_url, ...}。

    ``content_type`` 必须与后续 PUT 的 ``Content-Type`` 头逐字一致，否则 COS
    预签名校验失败（403 AccessDenied）——2026-11-11 真网络矩阵实验坐实。

    2026-11-18 配额自愈：官方单机器人素材上限 100，prepare 撞 40035007 时，
    先删孤儿/最旧素材腾空间，再重试一次。只针对配额错误、只重试一次；其它
    错误（格式/网络/鉴权）维持原失败路径，绝不静默吞。
    """
    try:
        return await _prepare_once(bot, file_name, data, content_type)
    except MaterialUploadError as e:
        code = _err_code(e.__cause__) if e.__cause__ else None
        if code != ERR_QUOTA_EXCEEDED:
            raise
        log.warning(jlog("material_quota_exceeded_healing", code=code))
        freed = await _quota_vacancy(bot)
        if not freed:
            raise   # 清不掉（无孤儿/删失败），如实抛 40035007，不假装成功
        log.info(jlog("material_quota_healed", freed=freed, retry="prepare"))
        return await _prepare_once(bot, file_name, data, content_type)


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
    client = _put_client_for(asyncio.get_running_loop())
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


async def _list_images(bot) -> list[dict]:
    """GET /v2/media-images 分页拉全量有效素材（官方库真实占用，不只本地缓存）。

    用 bot._request（官方 API 域名、带鉴权、token 过期自动刷新），与 prepare/
    complete 同源。返回按上传时间倒序的 image dict 列表；任一步失败抛原异常，
    由 _quota_vacancy 记为「自愈失败」并让 prepare 保持失败（不静默吞）。
    """
    base = bot.adapter.get_api_base()
    url = base.joinpath("v2", "media-images")
    out: list[dict] = []
    cursor = ""
    for _ in range(20):  # 100 张 / 每页通常 ≥50，20 页足够；硬上限防死循环
        req = _new_request("GET", url if not cursor else url.copy_add_query(cursor=cursor))
        ret = await bot._request(req)
        if not isinstance(ret, dict):
            raise MaterialUploadError("quota_list_bad_response")
        out.extend(ret.get("images") or [])
        cursor = str(ret.get("next_cursor") or "")
        if not cursor:
            break
    return out


async def _quota_vacancy(bot) -> int:
    """官方素材配额撞顶时释放空位，返回实际删除张数。

    清理顺序（最安全优先）：
      1. 孤儿素材——官方库有、本地 media_assets.json 不记录的 image_id。它们
         对应的消息早已发出（平台已转存副本），且本地 LRU 永远不会删它们，
         是 2026-11-18 配额触顶的元凶；
      2. 若孤儿不足以腾出 QUOTA_VACANCY_TARGET 个空位，再按 created_at 最旧
         删本地缓存里的素材，并同步从 _store 移除（避免下次误引用已删的）。

    全程持 _quota_lock（并发 prepare 同时撞顶时只清一次）。删除失败只记日志、
    不阻断——删不掉就返回已删数，prepare 重试后仍 40035007 会如实抛错。
    """
    async with _quota_lock:
        try:
            images = await _list_images(bot)
        except Exception as e:  # noqa: BLE001
            log.warning(jlog("quota_vacancy_list_failed", error=type(e).__name__))
            return 0
        total = len(images)
        keep = set(_store.known_image_ids())
        # 孤儿优先，其次最旧（官方返回按 created_at 倒序，反转即最旧在前）
        orphans = [im for im in images if str(im.get("image_id") or "") not in keep]
        locals_old = [im for im in reversed(images)
                      if str(im.get("image_id") or "") in keep]
        targets = orphans + locals_old
        need = max(0, total - (OFFICIAL_QUOTA - QUOTA_VACANCY_TARGET))
        deleted = 0
        for im in targets:
            if deleted >= need:
                break
            iid = str(im.get("image_id") or "")
            if not iid:
                continue
            await _delete(bot, iid)
            deleted += 1
            _store.forget_image_id(iid)   # 本地缓存同步摘除，防误引用已删素材
        log.info(jlog("quota_vacancy_done", before=total, need=need,
                      deleted=deleted, orphans=len(orphans)))
        return deleted


#: 待删除的旧素材 image_id（进程内队列）。
#:
#: 2026-11-26 真机实测（三张卡，同机同接口）::
#:
#:     prepare_ms  637 / 1606 / 3262     ← 唯一在抖的段
#:     put_ms      348 /  339 /  339     ← 恒定，170KB 上行 ≈ 0.34s
#:     complete_ms 1040 / 1040 / 1120    ← 恒定，且偏大
#:
#: ``put_ms`` 恒定说明带宽没问题（压图无收益）；``complete_ms`` 恒定 1 秒说明那
#: 不是网络抖动而是**固定结构开销**——它等于一次 ``complete`` POST **加上一次 LRU
#: 淘汰 DELETE**。``MAX_MATERIALS=92`` 且在库 92 张，意味着**每张新卡都必然淘汰
#: 一张旧卡、必然多发一次 DELETE**，玩家每次都要为「删上一张」等约 1 秒。
#:
#: 删的是上一张旧卡，玩家等的是这一张新卡 —— 没有理由让它阻塞。改为后台补删：
#: 本次不 await，下一次 ``ensure_material`` 或进程空闲时继续清。删失败只记日志
#: （``_delete`` 内部已兜底），残留孤儿由 ``_quota_vacancy`` 在撞 40035007 时统一
#: 清理，不会泄漏配额。
_PENDING_DELETE: set[str] = set()
_drain_task = None


def _drain_evicted(bot) -> None:
    """把待删 image_id 丢到后台清（**绝不阻塞**本次出图）。无运行中事件循环则留待下次。"""
    global _drain_task
    if not _PENDING_DELETE:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:      # 同步上下文（脚本/测试收尾）→ 留到下次 ensure_material
        return
    if _drain_task is not None and not _drain_task.done():
        return                # 已有清理任务在跑，不重复起
    _drain_task = loop.create_task(_drain_evicted_loop(bot))


async def _drain_evicted_loop(bot) -> None:
    """逐个补删。用「先 peek 再删再摘」而不是 pop：中途被取消时 id 仍留在队列里。"""
    while _PENDING_DELETE:
        iid = next(iter(_PENDING_DELETE))
        await _delete(bot, iid)          # 内部已吞异常只记日志，不会中断循环
        _PENDING_DELETE.discard(iid)


async def ensure_material(bot, data: bytes, file_name: str = "card.png") -> dict:
    """确保图片字节在素材库有引用，返回 ``{"url",width",height",md5"}``。

    缓存命中不上传；未命中走 prepare→PUT→complete 三步。任一失败抛
    :class:`MaterialUploadError`（由上层降级，不静默吞）。成功后做 LRU 淘汰。

    2026-11-26 加分段计时日志 ``material_timing``：出图慢到底是慢在
    「接口往返」还是「上行带宽」只能靠数据说话，不许猜。``cached=True`` 表示
    整段没走网络。

    2026-11-26 晚（第二版，依据上条实测数据）：LRU 淘汰的那一次 ``DELETE`` 移出
    热路径（见 ``_PENDING_DELETE``），``complete_ms`` 从此只含 ``complete`` 本身；
    prepare 加超时对冲（见 ``_PREPARE_HEDGE_AFTER``），削掉 3 秒级尾延迟。
    """
    if not data:
        raise MaterialUploadError("empty_image_bytes")
    md5 = hashlib.md5(data).hexdigest()
    cached = _store.get(md5)
    if cached:
        return {**cached, "md5": md5, "cached": True}

    t0 = time.time()
    width, height, content_type = image_meta(data)
    prepared = await _prepare(bot, file_name, data, content_type)
    t1 = time.time()
    md5_hex = await _put_bytes(prepared["presigned_url"], data, content_type)
    t2 = time.time()
    completed = await _complete(bot, prepared["upload_id"], md5_hex)
    item = {
        "url": completed["url"],
        "image_id": str(completed.get("image_id") or ""),
        "width": width,
        "height": height,
    }
    _store.put(md5, item)
    # LRU 淘汰：只摘记账、把旧素材 id 塞进待删队列，**不当场 await 删除**。
    # 这一改把每张冷卡的固定开销砍掉约 1.0s（真机 complete_ms 1040~1120ms 全在这里）。
    evicted = _store._evict_candidates()
    if evicted:
        _PENDING_DELETE.update(
            str(o["image_id"]) for o in evicted if o.get("image_id"))
        _drain_evicted(bot)
    t3 = time.time()
    log.info(jlog("material_uploaded", md5=md5[:12], width=width, height=height,
                  cached_store=len(_store)))
    log.info(jlog("material_timing",
                  prepare_ms=int((t1 - t0) * 1000),
                  put_ms=int((t2 - t1) * 1000),
                  complete_ms=int((t3 - t2) * 1000),
                  total_ms=int((t3 - t0) * 1000),
                  bytes=len(data)))
    return {**item, "md5": md5, "cached": False}


def markdown_image(url: str, width: int, height: int, alt: str = "card") -> str:
    """构造官方 Markdown 内嵌图片语法：``![alt #宽px #高px](url)``。"""
    return "![%s #%dpx #%dpx](%s)" % (alt, int(width), int(height), url)


def reset_store() -> None:
    """清空内存缓存（仅供测试隔离）。"""
    _store._items.clear()
    # 标记已读：否则下一次 get/put 会再从磁盘把条目读回来，reset 等于没做
    _store._loaded = True
