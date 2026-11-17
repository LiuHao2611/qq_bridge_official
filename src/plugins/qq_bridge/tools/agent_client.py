# -*- coding: utf-8 -*-
"""tools.agent_client —— 斗破源码问答沙盒的 HTTP 客户端（2026-11-17）。

## 为什么单独一个模块

主包 ``__init__`` 里的 deps 回调是函数内闭包，测试碰不到。把「发请求 + 判错」
挪到这里：单测能用 ``httpx.MockTransport`` 直接喂各种失败（超时/500/空答案），
而且**失败口径只有一处** —— 模型拿到的错误话术不会因为改了个调用点就变。

## 纪律

失败一律抛 :class:`AgentError`，消息是**给人看的**（dispatch 会原样转给模型，
模型再转告用户）。绝不返回空字符串假装成功，也不吞成「查不到」——
2026-11-17 的教训：假成功比报错更难查。
"""
from __future__ import annotations

import httpx


class AgentError(RuntimeError):
    """源码问答服务不可用 / 返回失败（消息可直接展示给用户）。"""


async def ask(base_url: str, question: str, timeout: float = 160.0,
              transport: httpx.AsyncBaseTransport | None = None) -> dict:
    """问一次源码问答沙盒，返回 ``{answer, tool_calls, elapsed}``。

    Args:
        base_url: 服务根地址，如 ``http://qq-doupo-agent:8765``
        question: 用户原问题（整句，不要压成关键词）
        timeout: 总预算秒数（agent 侧自己 30~90 秒）
        transport: 仅测试用（``httpx.MockTransport``）；生产传 None
    """
    base = (base_url or "").strip().rstrip("/")
    q = (question or "").strip()
    if not base:
        raise AgentError("未配置源码分析服务地址（DOUPO_AGENT_URL）")
    if not q:
        raise AgentError("问题为空")
    try:
        async with httpx.AsyncClient(timeout=timeout, transport=transport) as cli:
            r = await cli.post(base + "/ask", json={"question": q})
    except httpx.TimeoutException:
        raise AgentError("源码分析服务超时（%ds 未返回）" % int(timeout))
    except httpx.HTTPError as e:
        raise AgentError("源码分析服务连不上（%s: %s）"
                         % (type(e).__name__, str(e)[:120]))
    if r.status_code != 200:
        detail = ""
        try:
            detail = str((r.json() or {}).get("error") or "")
        except Exception:                        # noqa: BLE001  非 JSON 错误页
            detail = (r.text or "")[:160]
        raise AgentError("源码分析服务失败 HTTP %d：%s"
                         % (r.status_code, detail[:220] or "(无错误信息)"))
    try:
        data = r.json() or {}
    except Exception:                            # noqa: BLE001
        raise AgentError("源码分析服务返回了非 JSON 响应")
    answer = str(data.get("answer") or "").strip()
    if not answer:
        raise AgentError("源码分析服务返回空答案")
    usage = data.get("usage") or {}
    return {"answer": answer,
            "tool_calls": data.get("tool_calls"),
            "usage": usage if isinstance(usage, dict) else {},
            "model": data.get("model"),
            "elapsed": data.get("elapsed")}
