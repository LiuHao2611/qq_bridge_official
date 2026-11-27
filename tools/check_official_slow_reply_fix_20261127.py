# -*- coding: utf-8 -*-
"""守门（2026-11-27）：官方通道「纯文字+按钮慢回复」优化契约。

根因（线上铁证）：冷 DNS 解析 api.bot.qq.com 卡 2.5~5s（命中缓存 0ms），
按钮点击 ACK 串行 await 又叠 5s → text_card p99 13s。

本守门校验三类契约，纯静态/AST + 无网络行为测试，绝不访问线上：
  A. DNS 保活模块存在且语义正确（域名、开关、周期、只解析不建连）
  B. 插件 startup/shutdown 挂了保活的 start/stop，且只在官方通道启用时
  C. ACK 改为后台调度：调用点不再是 ``await _ack_interaction``；有强引用集合

用法：python tools/check_official_slow_reply_fix_20261127.py
退出码：0=PASS；1=契约破坏。
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DNS = ROOT / "src/plugins/qq_bridge/channel/dns_keepwarm.py"
INIT = ROOT / "src/plugins/qq_bridge/__init__.py"
INTER = ROOT / "src/plugins/qq_bridge/channel/qq_interaction.py"

_fail: list[str] = []


def check(cond: bool, msg: str) -> None:
    if cond:
        print("  PASS", msg)
    else:
        print("  FAIL", msg)
        _fail.append(msg)


def src(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def main() -> int:
    print("== 官方慢回复优化守门 ==")

    # --- A. DNS 保活模块 ---
    print("[A] DNS 保活模块")
    check(DNS.is_file(), "dns_keepwarm.py 存在")
    d = src(DNS)
    check("api.sgroup.qq.com" in d and "api.bot.qq.com" in d,
          "保活域名覆盖发送网关 + token/素材 API")
    check("OFFICIAL_DNS_KEEPWARM" in d, "提供一键关闭开关")
    check("keepwarm_interval" in d and "45" in d, "周期可配且有默认值")
    check("getaddrinfo" in d, "用 getaddrinfo 解析域名")
    check("create_task" in d and "_task" in d, "后台 task 有强引用（防 GC）")
    # 只解析不建连：不允许出现 socket.connect / create_connection
    check("create_connection" not in d and ".connect(" not in d,
          "只做域名解析，不建立 TCP 连接")
    # AST：warm_once 必须 catch 异常（保活失败不影响事件循环）
    tree = ast.parse(d)
    fns = {n.name: n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    check("warm_once" in fns, "warm_once 可单测")
    if "warm_once" in fns:
        has_try = any(isinstance(n, ast.Try) for n in ast.walk(fns["warm_once"]))
        check(has_try, "warm_once 内部有 try 兜底异常")

    # --- B. 插件生命周期挂钩 ---
    print("[B] startup/shutdown 挂钩")
    ini = src(INIT)
    check("dns_keepwarm.start()" in ini, "_on_startup 启动保活")
    check("await dns_keepwarm.stop()" in ini, "_on_shutdown 停止保活")
    check("Config.official_configured()" in ini,
          "仅在官方通道配置齐全时挂保活（双轨隔离）")

    # --- C. ACK 非阻塞 ---
    print("[C] ACK 后台调度")
    itv = src(INTER)
    check("_schedule_ack" in itv, "存在后台调度函数")
    # 调用点不得再串行 await _ack_interaction
    check("await _ack_interaction(bot, event)" not in itv,
          "调用点不再串行 await ACK（防冷 DNS 5s 拖慢业务）")
    check("_schedule_ack(bot, event)" in itv, "调用点改为 _schedule_ack")
    check("asyncio.create_task(_ack_interaction" in itv,
          "ACK 走 create_task 后台执行")
    check("_ack_tasks" in itv and "_ack_tasks.add(task)" in itv,
          "ACK task 存集合保持强引用")
    check("duration_ms=int((time.time() - t0) * 1000)" in itv,
          "ACK 有耗时打点")

    # --- D. 红线：不动原 NapCat / bot.py ---
    print("[D] 边界红线")
    botpy = ROOT / "bot.py"
    check("dns_keepwarm" not in src(botpy), "bot.py 未被改动（不碰入口）")

    if _fail:
        print("\nRESULT: FAIL (%d)" % len(_fail))
        for m in _fail:
            print("  -", m)
        return 1
    print("\nRESULT: PASS — DNS 保活 / 生命周期挂钩 / ACK 非阻塞 / 边界 四组契约齐备")
    return 0


if __name__ == "__main__":
    sys.exit(main())
