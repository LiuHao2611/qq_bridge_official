# -*- coding: utf-8 -*-
"""官方通道 DNS 保活（2026-11-27，仅官方副本）。

背景 / 根因（线上 service.err.log 铁证 + 真机实测，勿删注释）：
  * 用户反馈「纯文字加按钮后机器人回复很慢」；日志里 ``text_card`` 的
    ``duration_ms`` p50≈1477ms 正常，但 p90≈4068ms / p99≈13177ms / max≈15792ms，
    每条慢发送前必有 ``official_send_connect_retry duration_ms≈5030``。
  * 5030ms 不是 httpx connect timeout，也不是服务端慢——真机实测：
      - ``socket.getaddrinfo('api.bot.qq.com', 443)`` 冷缓存 **2.5s**、失败时 5.0s；
        命中 dnsmasq 缓存后 **0.000s**。
      - 手机 ``/etc/resolv.conf`` 的 nameserver=127.0.0.1（NetworkManager 自带
        dnsmasq，``--cache-size=400``）。WiFi 休眠 / 边缘 IP 不可达时，dnsmasq
        向上游查询要等满超时才回 EAI_AGAIN，``adapter.request`` 抛 NetworkError。
      - 这与长期红线「DNS 热 ≠ 射频醒」完全吻合：缓存命中时根本不出网卡，
        所以休眠后第一发真实查询会丢、要等 5s 超时。
  * 结论：**保持 dnsmasq 缓存常热**即可让发送路径永远命中缓存（0ms），
    从根本上消除偶发 5s，而不是靠重试赌运气。

为什么不用别的方式：
  * 改第三方库（nonebot / httpx / dnsmasq）源码 —— 违反红线，且升级即失效；
  * 改 httpx connect timeout —— 实测 5s 来自 glibc/musl getaddrinfo，不是 httpx；
  * 发送前同步预热 —— 冷解析本身就要 2.5~5s，只会把耗时挪个位置。
  后台定期解析是唯一零风险、对业务透明、且能让「DNS 常热」的方案。

契约：
  * 只做域名解析（getaddrinfo），**不建 TCP、不鉴权、不发消息、不碰账号**；
  * 任何异常只记日志，绝不让保活任务把事件循环搞崩；
  * 可用 ``OFFICIAL_DNS_KEEPWARM=false`` 一键关闭；周期可配。
"""
from __future__ import annotations

import asyncio
import os
from typing import Any

try:  # 正常包内 import
    from ..config import get_logger, jlog
except (ImportError, ValueError):  # 单文件直载（离线单测）时兜底到纯 logging
    import logging

    def get_logger():  # type: ignore[no-redef]
        return logging.getLogger("qq_bridge.dns_keepwarm")

    def jlog(evt, **kw):  # type: ignore[no-redef]
        return {"event": evt, **kw}


log = get_logger()

#: 需要保持缓存热的官方域名（发送 API / token / 素材上传都会用到）。
#: api.sgroup.qq.com=群消息发送网关；api.bot.qq.com=token + 素材 API。
DOMAINS = ("api.sgroup.qq.com", "api.bot.qq.com")


def _env_bool(name: str, default: bool = True) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return str(raw).strip().lower() not in ("false", "0", "no", "off")


def keepwarm_enabled() -> bool:
    """是否启用 DNS 保活（默认开；OFFICIAL_DNS_KEEPWARM=false 关闭）。"""
    return _env_bool("OFFICIAL_DNS_KEEPWARM", True)


def keepwarm_interval() -> float:
    """保活周期秒（默认 45s；可配 OFFICIAL_DNS_KEEPWARM_INTERVAL）。

    取 45s 是因为 dnsmasq 默认缓存这些域名的 TTL 多在 60~300s，45s 足够在
    记录过期前刷新，又不会频繁到打扰射频休眠（每次解析本身是纯内存命中，
    缓存热时 0ms，几乎零成本）。
    """
    try:
        val = float(os.environ.get("OFFICIAL_DNS_KEEPWARM_INTERVAL", "45"))
    except (TypeError, ValueError):
        return 45.0
    return min(3600.0, max(10.0, val))


async def warm_once(loop: Any = None) -> dict[str, bool]:
    """解析一遍所有域名，返回 ``{domain: ok}``。

    用 ``loop.getaddrinfo``（线程池执行，不阻塞事件循环）。单个域名失败不
    影响其它域名；逐条记日志（失败只 warning，不抛）。
    """
    loop = loop or asyncio.get_running_loop()
    result: dict[str, bool] = {}
    for domain in DOMAINS:
        try:
            await loop.getaddrinfo(domain, 443, type=1)  # type 1 = SOCK_STREAM
            result[domain] = True
        except Exception as e:  # noqa: BLE001 — 保活失败绝不影响事件循环
            result[domain] = False
            log.warning(jlog("official_dns_keepwarm_fail", domain=domain,
                             error=type(e).__name__, detail=str(e)[:120]))
    ok = sum(1 for v in result.values() if v)
    log.info(jlog("official_dns_keepwarm_tick", ok=ok, total=len(result),
                  domains=",".join(DOMAINS)))
    return result


class DNSKeepWarm:
    """后台 DNS 保活任务（幂等 start/stop）。"""

    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()

    async def run(self) -> None:
        interval = keepwarm_interval()
        log.info(jlog("official_dns_keepwarm_start", interval=interval,
                      domains=",".join(DOMAINS)))
        # 启动即预热一次：进程刚起、缓存可能已冷，先补一发。
        await warm_once()
        while not self._stop.is_set():
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=interval)
                break  # stop 被置位
            except asyncio.TimeoutError:
                await warm_once()

    def start(self) -> bool:
        """启动后台任务；未启用或已启动返回 False。"""
        if not keepwarm_enabled():
            log.info(jlog("official_dns_keepwarm_disabled"))
            return False
        if self._task is not None and not self._task.done():
            return False
        self._stop.clear()
        try:
            self._task = asyncio.create_task(self.run(), name="official-dns-keepwarm")
        except RuntimeError as e:  # 无运行中事件循环（如离线单测）
            log.warning(jlog("official_dns_keepwarm_start_failed",
                             error=type(e).__name__, detail=str(e)[:120]))
            self._task = None
            return False
        return True

    async def stop(self) -> None:
        """优雅停止后台任务。"""
        self._stop.set()
        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, timeout=5)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                self._task.cancel()
            self._task = None
        log.info(jlog("official_dns_keepwarm_stopped"))


#: 模块级单例（插件 startup 挂钩它）
_keepwarm = DNSKeepWarm()


def start() -> bool:
    return _keepwarm.start()


async def stop() -> None:
    await _keepwarm.stop()
