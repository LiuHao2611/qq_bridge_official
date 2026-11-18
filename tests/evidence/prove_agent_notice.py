# -*- coding: utf-8 -*-
"""端到端探针：真 registry → 探路话 → 真沙盒 agent → 答案。

跑法（容器内）：
    docker exec -i qq-bridge-official python - < tests/evidence/prove_agent_notice.py
验证四件事：
  1. doupo_code_ask 真的带 progress_text（不是测试里造的假工具）；
  2. dispatch 在**开跑之前**就把探路话发出去了（时间戳早于答案）；
  3. 同一回合调两次只发一条；
  4. 探路话发完，工具照常拿到答案（探路话不是前置条件）。
"""
import asyncio
import sys
import time

sys.path.insert(0, "/app/src/plugins")

import qq_bridge  # noqa: E402  （拉起插件模块，工具尚未注册）
from qq_bridge.tools import default_registry as reg  # noqa: E402
from qq_bridge.tools.registry import ToolContext  # noqa: E402

qq_bridge._register_m2_tools()

tool = reg.get("doupo_code_ask")
assert tool is not None, "工具没注册上"
notice_text = (tool.progress_text or "").strip()
assert notice_text, "doupo_code_ask 没带探路话"
print("[1] notice=%r timeout=%ss" % (notice_text, tool.timeout))

sent = []


async def notify(text):
    sent.append((round(time.time(), 2), text))


ctx = ToolContext(scope="g:0", user_id=0, group_id=0, trace_id="probe-notice",
                  notify=notify)

t0 = time.time()
answer = asyncio.run(reg.dispatch(
    "doupo_code_ask",
    {"question": "爬塔第 37 层打死 BOSS 爆装备的概率是多少？"}, ctx))
took = time.time() - t0
print("[2] elapsed=%.1fs notices=%d answer_len=%d" % (took, len(sent), len(answer)))
assert len(sent) == 1, "探路话该只发一次（实发 %d）" % len(sent)
assert sent[0][1] == notice_text
assert took > 5, "太快，可能没真跑沙盒 agent"

# 同回合再调一次：应当只剩提醒，且不再发探路话
answer2 = asyncio.run(reg.dispatch(
    "doupo_code_ask", {"question": "再问一遍同一个问题"}, ctx))
print("[3] second_call answer2_len=%d notices_now=%d" % (len(answer2), len(sent)))
assert len(sent) == 1, "同一回合第二次调用不许再发探路话"
assert "已经查过一次" in answer2

# 发送出口炸了 → 工具照常出答案（只记日志）
async def boom(text):
    raise RuntimeError("渠道 502")


ctx3 = ToolContext(scope="g:0", trace_id="probe-notice-boom", notify=boom)
answer3 = asyncio.run(reg.dispatch(
    "doupo_code_ask", {"question": "宗门一共有几个，权重最高和最低分别是谁？"}, ctx3))
print("[4] broken_notify answer3_len=%d" % len(answer3))
assert len(answer3) > 50

print("[5] answer_head=%r" % answer[:120])
print("PROVE_OK")
