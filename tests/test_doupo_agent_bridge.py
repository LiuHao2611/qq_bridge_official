# -*- coding: utf-8 -*-
"""斗破源码问答沙盒（agent 容器）与主进程桥接的回归测试（2026-11-17）。

覆盖四层，缺一层就会退回「问细了就不知道」：
  1. 工具注册：只有一个 ``doupo_code_ask``（不能和旧的片段工具并存）、safe、单参数；
  2. 超时口径：这个工具慢（30~90s），必须有自己的 timeout，不能被全局 30s 掐断；
  3. HTTP 客户端：200 / 500 / 超时 / 连不上 / 空答案 五种路径都要**显式报错**，
     不许吞成「查不到」；
  4. 沙盒边界与底座选型：compose 的只读挂载/read_only/cap_drop/不传 env_file，
     Dockerfile 预先建挂载点，服务端不用 smolagents（会退化）。
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _tool_stack import REPO, load_module, load_tool_stack  # noqa: E402


# ---------------- 1. 工具注册 ----------------
def test_only_one_code_qa_tool_registered_and_it_is_safe():
    registry_mod, builtins_mod = load_tool_stack("_ags_reg")
    reg = registry_mod.ToolRegistry()

    async def cb(question, ctx):
        return "stub"

    names = builtins_mod.register_builtin_tools(reg, {"doupo_agent_ask": cb})
    assert "doupo_code_ask" in names
    assert "doupo_code_explain" not in names, "旧的片段检索工具不许再注册"

    t = reg.get("doupo_code_ask")
    assert t.risk == registry_mod.RISK_SAFE            # 群聊可用
    assert t.parameters["required"] == ["question"]
    assert set(t.parameters["properties"]) == {"question"}
    # 描述必须把「多慢」「别重复调」教给模型，否则它会一轮调好几次
    assert "30~90" in t.description
    assert "一次" in t.description
    # 讲解时不主动声明权限口径（用户明确要求）
    for w in ("只读", "不能改", "readonly", "不可编辑"):
        assert w not in t.description


def test_callback_receives_original_question():
    """args→callback 必须原样传问题（压成关键词会让 agent 找错文件）。"""
    registry_mod, builtins_mod = load_tool_stack("_ags_fwd")
    reg = registry_mod.ToolRegistry()
    got = {}

    async def cb(question, ctx):
        got["q"] = question
        return "stub"

    builtins_mod.register_builtin_tools(reg, {"doupo_agent_ask": cb})
    tool = reg.get("doupo_code_ask")
    q = "爬塔第 37 层打死 BOSS 爆装备的概率是多少？"
    asyncio.run(tool.handler({"question": q}, object()))
    assert got["q"] == q


# ---------------- 2. 超时口径 ----------------
def test_per_tool_timeout_overrides_global_default():
    """工具自带 timeout 必须覆盖全局 AI_TOOL_TIMEOUT，否则 30s 就被掐断。"""
    registry_mod, _ = load_tool_stack("_ags_to")
    reg = registry_mod.ToolRegistry()

    async def slow(args, ctx):
        await asyncio.sleep(0.5)
        return "done"

    reg.register(registry_mod.Tool(
        name="slow_tool", description="慢工具",
        parameters={"type": "object", "properties": {}},
        handler=slow, timeout=0.05))
    reg.register(registry_mod.Tool(
        name="default_tool", description="默认工具",
        parameters={"type": "object", "properties": {}},
        handler=slow))
    assert reg.get("slow_tool").timeout == 0.05
    assert reg.get("default_tool").timeout is None

    out = asyncio.run(reg.dispatch("slow_tool", {}, registry_mod.ToolContext()))
    assert "执行超时" in out, out
    assert "（0s）" in out          # int(0.05) —— 证明用的是工具自己的值，不是全局 30


def test_code_ask_tool_carries_agent_timeout():
    registry_mod, builtins_mod = load_tool_stack("_ags_to2")
    reg = registry_mod.ToolRegistry()

    async def cb(question, ctx):
        return "stub"

    builtins_mod.register_builtin_tools(reg, {"doupo_agent_ask": cb})
    t = reg.get("doupo_code_ask")
    cfg = sys.modules["_ags_to2.config"].Config
    assert t.timeout == pytest.approx(cfg.DOUPO_AGENT_TIMEOUT)
    assert t.timeout >= 120, "agent 一次 30~90 秒，<120s 会在算到一半被掐"


# ---------------- 3. HTTP 客户端：失败必须显式 ----------------
def _client_mod(prefix: str = "_ags_ac"):
    return load_module("tools/agent_client.py", prefix)


def _run(coro):
    return asyncio.run(coro)


def test_client_returns_answer():
    ac = _client_mod()
    def handler(req: httpx.Request) -> httpx.Response:
        assert req.url.path == "/ask"
        import json
        assert json.loads(req.content)["question"] == "宗门权重怎么算"
        return httpx.Response(200, json={"answer": "按权重抽", "tool_calls": 3,
                                         "elapsed": 12.5})
    out = _run(ac.ask("http://x:1", "宗门权重怎么算",
                      transport=httpx.MockTransport(handler)))
    assert out["answer"] == "按权重抽"
    assert out["tool_calls"] == 3


def test_client_500_surfaces_detail():
    ac = _client_mod()
    def handler(req):
        return httpx.Response(500, json={"error": "RateLimitError: 429 额度到顶"})
    with pytest.raises(ac.AgentError) as ei:
        _run(ac.ask("http://x:1", "q", transport=httpx.MockTransport(handler)))
    assert "HTTP 500" in str(ei.value)
    assert "额度到顶" in str(ei.value)


def test_client_500_non_json_body_still_reported():
    ac = _client_mod()
    def handler(req):
        return httpx.Response(502, text="<html>bad gateway</html>")
    with pytest.raises(ac.AgentError) as ei:
        _run(ac.ask("http://x:1", "q", transport=httpx.MockTransport(handler)))
    assert "HTTP 502" in str(ei.value)
    assert "bad gateway" in str(ei.value)


def test_client_timeout_is_explicit():
    ac = _client_mod()
    def handler(req):
        raise httpx.ReadTimeout("too slow")
    with pytest.raises(ac.AgentError) as ei:
        _run(ac.ask("http://x:1", "q", timeout=7,
                    transport=httpx.MockTransport(handler)))
    assert "超时" in str(ei.value) and "7" in str(ei.value)


def test_client_connect_error_is_explicit():
    ac = _client_mod()
    def handler(req):
        raise httpx.ConnectError("connection refused")
    with pytest.raises(ac.AgentError) as ei:
        _run(ac.ask("http://x:1", "q", transport=httpx.MockTransport(handler)))
    assert "连不上" in str(ei.value)


def test_client_empty_answer_is_error_not_success():
    ac = _client_mod()
    def handler(req):
        return httpx.Response(200, json={"answer": "   "})
    with pytest.raises(ac.AgentError) as ei:
        _run(ac.ask("http://x:1", "q", transport=httpx.MockTransport(handler)))
    assert "空答案" in str(ei.value)


def test_client_rejects_empty_question_and_missing_url():
    ac = _client_mod()
    with pytest.raises(ac.AgentError):
        _run(ac.ask("http://x:1", "  "))
    with pytest.raises(ac.AgentError):
        _run(ac.ask("", "问题"))


# ---------------- 4. 沙盒边界 + 底座选型（守门用，不是文案检查） ----------------
def _compose_text() -> str:
    return (REPO / "docker-compose.official.yml").read_text(encoding="utf-8")


def _agent_service_block() -> str:
    """返回 agent 服务的 compose 片段（**去掉注释行**——注释里会提到 env_file
    这类字样，用全文断言会误报）。"""
    txt = _compose_text()
    # 注意要带前导两空格：bridge 服务里有一条
    # `DOUPO_AGENT_URL: "http://qq-doupo-agent:8765"`，裸 index 会先命中它
    i = txt.index("\n  qq-doupo-agent:") + 1
    out = []
    for line in txt[i:].splitlines():
        # 下一个顶层服务键（恰好两空格缩进 + 以冒号结尾）→ 停
        if out and line.startswith("  ") and not line.startswith("   ") \
                and line.rstrip().endswith(":"):
            break
        if line.lstrip().startswith("#"):
            continue
        out.append(line)
    return "\n".join(out)


def test_agent_service_is_sandboxed():
    blk = _agent_service_block()
    assert "read_only: true" in blk, "根文件系统必须只读"
    assert "cap_drop:" in blk and "ALL" in blk
    assert "no-new-privileges:true" in blk
    assert "/tmp:rw" in blk, "只读根文件系统要留一个 tmpfs 给临时文件"
    # 源码一律只读挂载
    for m in ("src/plugins/doupo", "docs"):
        assert m in blk
    assert blk.count(":ro") >= 3, "所有源码挂载都必须带 :ro"
    # 绝不能把整个 .env 塞进一个能被模型驱动 shell 的容器
    assert "env_file" not in blk
    # 不向宿主暴露端口（只允许同网络的 bridge 访问）
    assert "ports:" not in blk
    assert "volumes:" in blk


def test_agent_service_only_gets_ai_env():
    blk = _agent_service_block()
    env_lines = [ln.strip() for ln in blk.splitlines()
                 if ln.strip().startswith(("AI_", "AGENT_", "TZ"))]
    keys = {ln.split(":")[0] for ln in env_lines}
    assert keys <= {"AI_API_BASE", "AI_MODEL", "AI_API_KEY",
                    "AGENT_MAX_TURNS", "AGENT_TIMEOUT", "TZ"}, keys
    assert "AI_API_KEY" in keys


def test_dockerfile_precreates_mount_points():
    """read_only 根文件系统下 docker 不会自动建挂载目标 —— 少一个就静默空挂。"""
    df = (REPO / "docker/Dockerfile.agent").read_text(encoding="utf-8")
    assert "mkdir -p /repo/src/plugins/doupo" in df
    assert "/repo/docs" in df
    # 挂载点必须建在 USER 切换之前？不必，但必须在镜像里存在 —— 顺序无关，
    # 关键是不能写在 CMD 里（那时已经晚了）。
    assert "mkdir" in df.split("CMD")[0]


def test_agent_server_uses_openai_agents_not_smolagents():
    """smolagents 的 get_clean_message_list 会丢 assistant.tool_calls，
    多轮必然退化成文本 ReAct（2026-11-17 实测跑满步数）。别再换回去。

    只看 import 行：docstring 里解释这段历史时会出现 "smolagents" 字样，
    用全文 grep 会误报。
    """
    src = (REPO / "agent" / "agent_server.py").read_text(encoding="utf-8")
    imports = [ln for ln in src.splitlines()
               if ln.startswith(("import ", "from ")) or ln.startswith("import agents")]
    assert not any("smolagents" in ln for ln in imports), imports
    assert any(ln.startswith("from agents import") for ln in imports)
    assert "Runner.run" in src
    assert "set_tracing_disabled(True)" in src, "必须关掉 tracing，否则尝试往 OpenAI 外传"
    # 一个进程一个事件循环：AsyncOpenAI 的连接池绑定首次使用的事件循环，
    # 每请求 asyncio.run() 会让第二个请求直接炸 "Event loop is closed"
    assert "run_coroutine_threadsafe" in src
    assert "asyncio.run(" not in src
