# -*- coding: utf-8 -*-
"""斗破代码问答 agent —— 跑在**独立沙盒容器**里的只读代码分析服务（2026-11-17）。

## 为什么是「搬一个真 agent」而不是自己写检索工具

用户原话：**「你直接搬一个完整的agent项目吧，运行在沙盒里，docker应该可以满足，
这样无论我怎么问他都能找到我想要的东西，而且不会影响我的电脑文件风险」**

上一版是自己写的白名单检索工具（`tools/code_explain.py`），天花板很低：
只能看 `src/plugins/doupo/*.py`，不能 grep、不能算数 —— 问「第 37 层爆装备概率
是多少」，它只能把源码贴给模型，模型还得自己在脑子里算；问细一点就露出
「只给签名」或「贴错片段」的马脚。真 agent 的三件事是自研工具补不上的：
**自己决定看哪个文件**、**自己拼命令缩小范围**、**自己写 Python 代数字**。

## 为什么底座是 openai-agents 而不是 smolagents

第一版搬的是 HuggingFace `smolagents.ToolCallingAgent`（1.26.0），在这条端点上
**多轮必然退化**，容器内 dump 抓到的证据（2026-11-17）：

    smolagents/models.py  tool_role_conversions = {
        MessageRole.TOOL_CALL:     MessageRole.ASSISTANT,
        MessageRole.TOOL_RESPONSE: MessageRole.USER,      # ← tool 降级成 user 文本
    }
    smolagents/models.py  get_clean_message_list() 只拷 role/content，
                          **assistant.tool_calls 整个字段被丢掉**

也就是每一轮历史都被压回「文本 ReAct」：模型看不到自己上一轮的结构化调用，
只看到一句 `Observation: ...` 的 user 消息。第一轮能出 tool_calls，第二轮
就退回纯文本吐 `Calling tools: [...]`，解析失败 → 跑满步数 → 偶尔蒙对一次
「成功」。实测日志：

    [dump] msgs=2  tool_calls=0 roles=[system, user]      tc='required'
    [dump] msgs=6  tool_calls=0 roles=[...,assistant,user]
    Error while parsing tool call from model output: The model output does not
    contain any JSON blob.        →  Reached max steps.

换成 **openai-agents（OpenAI 官方 Agents SDK）** 后走标准协议
（`assistant.tool_calls` → `role="tool"` 回灌），同一道题一次通过：
42s、9 次工具调用，答案连「第 37 层根本不是 BOSS 层」这个前提错误都纠了出来。
**别为了省依赖换回 smolagents**，那不是配置问题，是它的消息扁平化设计。

## 沙盒边界（用户「不影响我的电脑文件」是硬要求）

  * 宿主仓库以 `:ro` 只读挂进 `/repo`，且只挂斗破相关三处 —— 写不进去是**内核**
    保证（`touch` 返回 `Read-only file system`），不是靠代码自觉；
  * `.env` 根本不挂进来，容器里只有 compose 显式传的 4 个 `AI_*` 变量；
  * 容器内没有 docker socket、没有宿主管家能力、`cap_drop: ALL`、非 root（uid 10001）；
  * 进程根文件系统 `read_only: true`，只有 `/tmp` 是 tmpfs；
  * `shell` 工具再加一道命令黑名单（双保险，见 `_FORBID`）—— 防的是「哪天挂载
    配错」，不是防模型。

## 接口

  ``GET  /health``  → ``{"ok": true, "model": ..., "sdk": "openai-agents x.y.z"}``
  ``POST /ask``     → body ``{"question": "...", "max_turns": 8}``
                      → ``{"answer": "...", "steps": n, "tool_calls": n, "elapsed": 12.3}``

单进程串行（一把锁）：一次跑二十来秒，群里并发问也不该同时烧几份 token。
失败一律 500 + 原因，**不返回「看起来成功」的空答案**。
"""
from __future__ import annotations

import asyncio
import itertools
import json
import os
import re
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

#: 关掉 tracing：trace 默认会往 OpenAI 上传（本机没有 OpenAI key，也不该外传）。
#: 环境变量 + 函数双保险，顺序不能反（必须在任何 Runner.run 之前）。
os.environ.setdefault("OPENAI_AGENTS_DISABLE_TRACING", "1")

import agents as _agents                                  # noqa: E402
from agents import (Agent, RunHooks, Runner, function_tool,  # noqa: E402
                    set_tracing_disabled)
from agents.models.openai_chatcompletions import OpenAIChatCompletionsModel  # noqa: E402
from openai import AsyncOpenAI                             # noqa: E402

set_tracing_disabled(True)

REPO = os.environ.get("AGENT_REPO", "/repo")
PORT = int(os.environ.get("AGENT_PORT", "8765"))
MAX_TURNS = int(os.environ.get("AGENT_MAX_TURNS", "14"))
TIMEOUT = float(os.environ.get("AGENT_TIMEOUT", "150"))
#: 单次模型调用的 HTTP 超时。2026-11-18 真机：上下文涨到 2.7 万字符后，
#: 一次调用（带 thinking）跑过 90s 直接 APITimeoutError，答案全丢。
#: 90s 对这种长上下文太紧，放到 120s；总闸 TIMEOUT 仍兜住整体上限。
HTTP_TIMEOUT = float(os.environ.get("AGENT_HTTP_TIMEOUT", "120"))
VERBOSE = os.environ.get("AGENT_VERBOSE") == "1"

# ===========================================================================
# 逐步日志（2026-11-18 用户要求「日志打印不够详细，必须每一步都打印出来」）
# ===========================================================================
# 口径：**一行一步**，每行带请求号 `[#12]`，stdout 直出（compose 收走）。
#   收到问题 → 第 N 轮请求模型 → 第 N 轮模型返回（决定调哪个工具）
#   → 工具入参 → 工具返回（耗时/字数/首行）→ …… → 完成/失败（总耗时/总 token）
# 不追求好看，追求「事后看日志能完整复盘模型当时怎么想的、查了什么、为什么这么答」。
# 别用 logging 模块：容器里只看 docker logs，print + flush 最不容易丢。
_REQ_SEQ = itertools.count(1)
#: 当前请求号（串行执行，模块级够用；换并发要改成 contextvar）
_REQ = "-"
#: 日志锁：同一轮里 SDK 会并发执行多个 function tool，两个线程同时 print
#: 会把两行拼成一行（2026-11-18 真机：`工具 8/10 read_file ← …[#2] 工具 9/10 …`）。
_log_lock = threading.Lock()


def _log(msg: str) -> None:
    with _log_lock:
        print("[%s] %s" % (_REQ, msg), flush=True)


def _oneline(s: object, limit: int = 200) -> str:
    """多行压成一行（命令里带换行会把日志撑爆）。"""
    t = " ".join(str(s or "").split())
    return t if len(t) <= limit else t[:limit] + "…"


def _chars(items) -> int:
    """上下文规模（按 JSON 长度估，只要量级对就行）。"""
    try:
        return len(json.dumps(items, ensure_ascii=False, default=str))
    except Exception:                                   # noqa: BLE001
        return -1


class _StepLog(RunHooks):
    """把 SDK 的每一轮/每个工具都打出来。

    为什么用 hooks 而不是事后读 `RunResult`：撞 `MaxTurnsExceeded` 时
    `RunResult` 根本拿不到 —— 恰恰是最需要复盘的那一次。
    """

    def __init__(self) -> None:
        self.round = 0
        self.t_llm = 0.0

    async def on_agent_start(self, context, agent) -> None:
        _log("▶ 开跑：agent=%s 工具=%s"
             % (getattr(agent, "name", "?"),
                [getattr(t, "name", "?") for t in (getattr(agent, "tools", []) or [])]))

    async def on_llm_start(self, context, agent, system_prompt, input_items) -> None:
        self.round += 1
        self.t_llm = time.time()
        _log("第%d轮 ▸ 请求模型：历史 %d 条，上下文约 %d 字"
             % (self.round, len(input_items or []), _chars(input_items)))

    async def on_llm_end(self, context, agent, response) -> None:
        dt = time.time() - self.t_llm
        u = getattr(response, "usage", None)
        calls = []
        text = ""
        for it in (getattr(response, "output", None) or []):
            kind = getattr(it, "type", "")
            if kind == "function_call":
                calls.append("%s(%s)" % (getattr(it, "name", "?"),
                                         _oneline(getattr(it, "arguments", ""), 220)))
            elif kind == "message":
                for c in (getattr(it, "content", None) or []):
                    text += str(getattr(c, "text", "") or "")
        _log("第%d轮 ◂ 模型返回：%.1fs in=%s out=%s cached=%s total=%s"
             % (self.round, dt,
                getattr(u, "input_tokens", "?"), getattr(u, "output_tokens", "?"),
                _cached(u), getattr(u, "total_tokens", "?")))
        if calls:
            for c in calls:
                _log("第%d轮   → 决定调用 %s" % (self.round, c))
        elif text:
            _log("第%d轮   → 直接作答（%d 字）：%s"
                 % (self.round, len(text), _oneline(text, 120)))

    async def on_agent_end(self, context, agent, output) -> None:
        _log("■ agent 结束：轮数=%d 输出 %d 字"
             % (self.round, len(str(output or ""))))


def _cached(usage) -> object:
    """缓存命中的输入 token（Ark 前缀缓存），拿不到就 '-'。"""
    try:
        d = getattr(usage, "input_tokens_details", None)
        return getattr(d, "cached_tokens", "-")
    except Exception:                                   # noqa: BLE001
        return "-"

#: 破坏性命令黑名单（只读挂载已经拦住了写入，这里是第二道）。
#: 注意别把 `>` 一刀切 —— `2>/dev/null` 很常见，但要挡住 `>` 重定向到文件。
_FORBID = (
    "rm ", "rmdir", "mv ", "chmod", "chown", "dd ", "mkfs", "shutdown", "reboot",
    "apt", "apt-get", "pip install", "npm install", "curl ", "wget ", "nc ",
    "kill ", "pkill", "docker", "sudo", "su ", "truncate", "tee ", "ln ",
)
#: 重定向写文件的正则。**必须放过比较运算符**（2026-11-18 真机）：
#: 老版本 `(?<![0-9])>{1,2}\s*[^\s&|]` 把 Python 的 `x >= 12`、`a->b` 全当成写文件，
#: 模型连着 3 轮被拒（日志：`✗ shell 被拒绝：不允许输出重定向写文件`），
#: 白烧三次模型调用才绕过去 —— 守卫误杀比命中更贵。
#: 现在排除：前面是数字/`<`/`=`/`!`/`-`（`2>`、`<=`、`>=`、`!=`、`->`），
#: 或后面紧跟 `=`（`>=`、`=>`）。
_FORBID_RE = re.compile(r"(?<![0-9<>=!\-])>{1,3}(?![=>])\s*\S")

#: 单次工具输出上限。这两个数字直接决定**总耗时**：容器内实测，每次模型调用
#: 的耗时随上下文线性上涨（约 5~8s），把 2500 砍到 1500 后同一道题从 67s 降到 44s。
#: 别为了「看得全」调回 2500+ —— 模型自己会用 head/-m5 缩范围，看不到全量也不影响结论。
_SH_OUT = 1500
_READ_LINES = 150       # 单次 read_file 默认行数（原 400 同理）

#: 本轮工具调用轨迹（排查用）。放在工具内部记录而不是从 RunResult 里翻 ——
#: 步数耗尽（MaxTurnsExceeded）时 RunResult 拿不到，恰恰最需要知道它查了什么。
_calls: list[tuple[str, str]] = []
_calls_lock = threading.Lock()

#: 工具预算（机械刹车）。2026-11-17 实测：提示词里写「控制在 6 次以内」，模型照样
#: 一路查到 11~14 次，最长 141 秒；而一旦撞上 max_turns，SDK 抛
#: `MaxTurnsExceeded`，**答案整个丢掉**（不是答得差，是一个字都没有）。
#: 所以在第 N 次之后由工具自己喊停，逼它用已有材料作答 —— 宁可少查一格，
#: 也不能把已经查到的全丢掉。改这个数字前先想清楚「用户愿意等多少秒」。
_MAX_TOOLS = int(os.environ.get("AGENT_MAX_TOOLS", "10"))
_BRAKE = ("⚠ 工具预算已用完（%d/%d 次）。**立刻停止检索**，用上面已经拿到的代码"
          "直接作答：数值就按已读到的公式算，缺的部分写「源码里未查到这部分」。"
          "不要再调用任何工具。")


def _brake() -> str:
    """返回刹车提示；空串 = 还能继续查。"""
    with _calls_lock:
        n = len(_calls)
    if n >= _MAX_TOOLS:
        return _BRAKE % (n, _MAX_TOOLS)
    return ""


def _note(name: str, arg: str) -> None:
    with _calls_lock:
        _calls.append((name, str(arg)[:200]))
        n = len(_calls)
    _log("   · 工具 %d/%d %s ← %s"
         % (n, _MAX_TOOLS, name, _oneline(arg, 300)))
    if VERBOSE:
        print("[tool] %s %s" % (name, str(arg)[:160].replace("\n", " ")), flush=True)


def _tool_done(name: str, t0: float, out: str, extra: str = "") -> None:
    """工具返回：耗时 + 输出规模 + 首行（首行往往就是答案线索）。"""
    head = (str(out or "").splitlines() or [""])[0]
    _log("   · 工具 %s → %.2fs %d 字%s 首行：%s"
         % (name, time.time() - t0, len(str(out or "")), extra, _oneline(head, 120)))

INSTRUCT = """你是斗破小游戏（一个 QQ 群里的文字 RPG）的代码讲解 agent。源码只读挂载在 {repo}。
你手上有两个工具：shell（在 {repo} 里跑只读命令，可以用 grep/sed/head/python3）和
read_file（按行号读文件）。用它们把答案**查出来**，不要凭印象猜。

模块地图（先按这张表直接 open 对应文件，比全仓库乱 grep 快得多）：
  src/plugins/doupo/config.py        全局数值表：等级/属性/战斗系数/暴击/称谓（多数常量在这）
  src/plugins/doupo/core.py          战斗与角色核心纯函数：属性计算、battle()、斗技结算、爬塔开箱
  src/plugins/doupo/commands.py      口令业务层：每个口令的完整流程与文案（cmd_* 函数）
  src/plugins/doupo/factions.py      宗门/门派：FACTIONS 宗门表、权重、宗门异火
  src/plugins/doupo/skills.py        斗技三阶 + 四槽上阵内核（学习/升级/装配）
  src/plugins/doupo/skill_book.py    斗技图鉴：每本斗技的全部参数（倍率/连击 n,v/条件）
  src/plugins/doupo/skill_effects.py 斗技效果词表与战斗消费（连击/吸血/破防等）
  src/plugins/doupo/weapon_data.py   武器数值 + 武器×斗技搭配
  src/plugins/doupo/flame_skills.py  异火主动技/被动权威表
  src/plugins/doupo/tower_data.py    爬塔数据：层型判定、宝箱率、武器解锁层、塔增益
  src/plugins/doupo/tower_session.py 爬塔会话状态机（进度/电梯）
  src/plugins/doupo/store.py         存档层（SQLite）
  src/plugins/doupo/card.py          人物卡/斗技卡渲染
  {repo}/docs                       设计文档（只当线索，与代码冲突时以代码为准）

工作纪律：
1. 先定位再回答。按上面的地图直接读可疑文件；只有真不知道在哪才 `grep -rn`，
   而且要把范围限定到具体文件/目录。用 read_file 按行号读那几行，不要整文件读。
   **能并行的查询合并成一次 shell**（`grep -rn "甲\\|乙" 文件1 文件2` 或 `命令1; 命令2`），
   一轮拿全，别一次只查一个词 —— 总工具调用控制在 6 次以内（每次都是几秒的等待）。
2. 要算就算。涉及概率、倍率、阈值、等级门槛的，把公式和每个系数都找出来，
   然后用 `python3 -c "..."` 自己代进去算出**具体百分数**。禁止把算式丢给用户。
3. 注意边界。同一个数值在不同分支往往不同（BOSS 层 vs 普通层、首通 vs 重打、
   池子空 vs 未集齐、周副本 vs 爬塔）。这些分支要分开讲，不要只挑一种回答。
4. 多文件找齐再下结论。**源码是唯一事实来源**。找不到就说「源码里没找到」，
   绝不编造数字或函数名。

回答格式（中文，800 字以内，**说人话**）：
这段答案会被群里的机器人**用自己的人设口吻转述**给用户，所以：
1. **禁止 Markdown**：不要 `#` 标题、不要 `**加粗**`、不要表格、不要代码块、不要 `-` / `1.` 列表符号。
2. 用平常说话的分段来组织：第一段一句话结论加关键数字；中间几段讲公式和判定顺序，
   用「先…再…最后…」「因为…所以…」这类口语连接；最后一段讲分支差异（BOSS 层/普通层之类）。
3. 出处写成「文件名:行号」直接嵌在句子里，不要单独列成清单。
   只引必要的 1~2 行代码，不要整段贴源码。"""

_TASK = """用户问：{question}

请按工作纪律查源码并回答。回答里出现的每个数字都要有出处（文件名:行号）。"""


def _guard(cmd: str) -> str:
    """命令守卫：返回拒绝理由；空串 = 放行。

    单独抽成普通函数（而不是写在 ``shell`` 里）是为了能被容器内的证明脚本直接调用——
    ``@function_tool`` 包装后的对象要构造 SDK 的上下文才能调，验沙盒边界时没必要
    拖上那一层。``shell`` 第一件事就是问它，路径完全一致。
    """
    low = (cmd or "").lower()
    for b in _FORBID:
        if b in low:
            return "拒绝执行：沙盒只允许只读命令（命中黑名单 %r）" % b
    if _FORBID_RE.search(cmd or ""):
        return "拒绝执行：不允许输出重定向写文件"
    return ""


def _safe_path(path: str) -> Path | None:
    """把入参解析成仓库内的绝对路径；越界返回 None（同样是为了可被证明脚本直接调）。"""
    p = (Path(REPO) / (path or "").lstrip("/").replace(REPO + "/", "", 1)).resolve()
    if not str(p).startswith(REPO):
        return None
    return p


@function_tool
def shell(cmd: str) -> str:
    """在仓库里执行只读 shell 命令（grep / sed / cat / head / wc / python3 -c 等），返回命令输出。

    Args:
        cmd: 要执行的 shell 命令；工作目录固定为仓库根，只允许只读操作。
    """
    stop = _brake()
    if stop:                                     # 先判预算再记账：第 N+1 次才被拦
        _log("   ⛔ 工具预算已用完（%d/%d），拒绝再调 shell：%s"
             % (_MAX_TOOLS, _MAX_TOOLS, _oneline(cmd, 120)))
        return stop
    _note("shell", cmd)
    deny = _guard(cmd)
    if deny:
        _log("   ✗ shell 被拒绝：%s" % deny)
        return deny
    t0 = time.time()
    try:
        r = subprocess.run(["sh", "-c", cmd], cwd=REPO, capture_output=True,
                           text=True, timeout=40)
    except subprocess.TimeoutExpired:
        _log("   ✗ shell 超时（40s）：%s" % _oneline(cmd, 120))
        return "执行超时（40s），请换更小的范围（加 head / -m5）"
    except Exception as e:                       # noqa: BLE001
        _log("   ✗ shell 执行失败：%s" % e)
        return "执行失败：%s" % e
    out = (r.stdout or "") + (r.stderr or "")
    out = out.replace(REPO + "/", "")            # 去掉冗长前缀，省 token
    cut = ""
    if len(out) > _SH_OUT:
        out = out[:_SH_OUT] + ("\n…（输出过长已截断，共 %d 字；请加 head / -m5 缩小范围）"
                               % len(out))
        cut = "（已截断）"
    _tool_done("shell", t0, out, cut)
    return out or "(无输出)"


@function_tool
def read_file(path: str, start: int = 1, end: int = 0) -> str:
    """读取仓库里的一个文本文件，返回带行号的内容（默认 200 行）。

    Args:
        path: 相对仓库根的路径，例如 src/plugins/doupo/tower_data.py
        start: 起始行号（从 1 开始）
        end: 结束行号；0 表示从 start 起读 200 行
    """
    stop = _brake()
    if stop:
        _log("   ⛔ 工具预算已用完（%d/%d），拒绝再调 read_file：%s"
             % (_MAX_TOOLS, _MAX_TOOLS, _oneline(path, 120)))
        return stop
    _note("read_file", "%s:%s-%s" % (path, start, end or "auto"))
    p = _safe_path(path)
    if p is None:
        _log("   ✗ read_file 越界：%s" % _oneline(path, 120))
        return "拒绝读取：路径越出仓库"
    t0 = time.time()
    try:
        lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as e:
        _log("   ✗ read_file 失败：%s" % e)
        return "读取失败：%s" % e
    a = max(1, start)
    b = min(len(lines), (end if end > 0 else a + _READ_LINES - 1))
    out = "\n".join("%5d| %s" % (i, lines[i - 1]) for i in range(a, b + 1))
    _tool_done("read_file", t0, out, "（%d:%d / 共 %d 行）" % (a, b, len(lines)))
    return out


_lock = threading.Lock()
_model: OpenAIChatCompletionsModel | None = None
_agent: Agent | None = None

#: 常驻事件循环。**不能每请求新建事件循环**（`asyncio.run` 那种写法）：
#: `AsyncOpenAI` 内部的 httpx 连接池会绑定在「第一次用它的事件循环」上，循环一关
#: 连接就废，下一个请求直接炸 `RuntimeError: Event loop is closed`
#: （2026-11-17 真机踩到：同一个容器第 1 题正常、第 2 题秒失败）。
#: 一个进程一个循环，请求用 `run_coroutine_threadsafe` 投递进去。
_loop: asyncio.AbstractEventLoop | None = None
_loop_lock = threading.Lock()


def _get_loop() -> asyncio.AbstractEventLoop:
    global _loop
    with _loop_lock:
        if _loop is None:
            _loop = asyncio.new_event_loop()
            threading.Thread(target=_loop.run_forever, name="agent-loop",
                             daemon=True).start()
        return _loop


def _api_base() -> str:
    """Ark 兼容端点。宿主的 `AI_API_BASE` 结尾带 `/responses`（那条链路用 Responses
    API），agent 这边走标准 chat/completions，必须削掉。"""
    b = (os.environ.get("AI_API_BASE") or "").strip().rstrip("/")
    if b.endswith("/responses"):
        b = b[: -len("/responses")]
    return b


def _model_id() -> str:
    """agent 用哪个模型。

    `AGENT_MODEL` 优先，没配就跟随主 bot 的 `AI_MODEL`。
    **为什么允许分开**：火山 Ark 的免费额度是**按模型独立计**的，"安全体验模式"
    用完一个模型就暂停那一个。翻源码一次要几万 token，跟日常闲聊抢同一个池子时，
    一深查就把闲聊额度吃光。分成两个模型池互不影响（2026-11-18 用户提问
    「消耗额度这么快吗，那我后面聊两句不就没额度了」之后加的）。
    """
    return ((os.environ.get("AGENT_MODEL") or "").strip()
            or (os.environ.get("AI_MODEL") or "").strip())


def _build() -> Agent:
    global _model, _agent
    if _agent is not None:
        return _agent
    api_base = _api_base()
    model_id = _model_id()
    api_key = (os.environ.get("AI_API_KEY") or "").strip()
    if not (api_base and model_id and api_key):
        raise RuntimeError("缺少 AI_API_BASE / AI_MODEL(或 AGENT_MODEL) / AI_API_KEY")
    client = AsyncOpenAI(
        base_url=api_base, api_key=api_key,
        # 关键：默认 openai SDK 会对 429 静默重试退避，表现成「请求挂死 300 秒」。
        # 2026-11-17 踩过：Ark 返回 SetLimitExceeded（该模型额度到顶），
        # 上层只看到超时，完全查不出原因。这里关掉重试 + 压短超时，
        # 让失败**立刻显式化**（/ask 会把原因如实返回调用方）。
        max_retries=0, timeout=HTTP_TIMEOUT,
    )
    _model = OpenAIChatCompletionsModel(model=model_id, openai_client=client)
    _agent = Agent(
        name="doupo-code",
        instructions=INSTRUCT.format(repo=REPO),
        tools=[shell, read_file],
        model=_model,
    )
    return _agent


def _trace() -> list[str]:
    """工具轨迹（一行一条；命令里的换行必须压掉，否则总账日志会被撑成十几行）。"""
    with _calls_lock:
        return ["%s:%s" % (n, _oneline(a, 70)) for n, a in _calls]


def _usage(res) -> dict:
    """取本轮真实 token 用量。

    **为什么要报这个**：用户问「消耗额度这么快吗，我聊两句不就没额度了」——
    没有用量数据就只能靠猜。每轮把 input/output/total 打出来，谁吃额度一目了然。
    拿不到就返回空 dict：统计失败不该拖垮问答本身。
    """
    try:
        u = res.context_wrapper.usage
    except Exception:                            # noqa: BLE001  SDK 版本差异
        return {}
    out: dict = {}
    for k in ("requests", "input_tokens", "output_tokens", "total_tokens"):
        v = getattr(u, k, None)
        if isinstance(v, int):
            out[k] = v
    return out


async def _run(question: str, max_turns: int) -> dict:
    agent = _build()
    t0 = time.time()
    _log("──── 收到问题 ────")
    _log("问题：%s" % _oneline(question, 500))
    _log("参数：max_turns=%d 工具预算=%d 超时=%.0fs 模型=%s 仓库=%s"
         % (max_turns, _MAX_TOOLS, TIMEOUT, _model_id(), REPO))
    hooks = _StepLog()
    res = await Runner.run(agent, _TASK.format(question=question),
                           max_turns=max_turns, hooks=hooks)
    trace = _trace()
    usage = _usage(res)
    answer = str(getattr(res, "final_output", "") or "").strip()
    _log("════ 完成 %.1fs｜轮数=%d｜工具 %d 次｜答案 %d 字｜tokens %s (in=%s out=%s)"
         % (time.time() - t0, hooks.round, len(trace), len(answer),
            usage.get("total_tokens"), usage.get("input_tokens"),
            usage.get("output_tokens")))
    _log("答案开头：%s" % _oneline(answer, 160))
    if trace:
        _log("轨迹：%s" % " | ".join(trace[-12:]))
    print("[agent] done tools=%d tokens=%s (in=%s out=%s)"
          % (len(trace), usage.get("total_tokens"), usage.get("input_tokens"),
             usage.get("output_tokens")), flush=True)
    return {"answer": answer,
            "steps": len(getattr(res, "new_items", []) or []),
            "tool_calls": len(trace),
            "tools": [x.split(":", 1)[0] for x in trace][:20],
            "usage": usage,
            "model": _model_id(),
            "elapsed": round(time.time() - t0, 1)}


def ask(question: str, max_turns: int | None = None) -> dict:
    """跑一次问答。串行执行，返回 {answer, steps, tool_calls, usage, elapsed}。"""
    global _REQ
    question = (question or "").strip()
    if not question:
        return {"answer": "（空问题）", "steps": 0, "tool_calls": 0,
                "usage": {}, "elapsed": 0.0}
    n = max(2, min(int(max_turns or MAX_TURNS), 20))
    t0 = time.time()
    with _lock:                                  # 串行：并发问不该同时烧几份 token
        _REQ = "#%d" % next(_REQ_SEQ)
        with _calls_lock:
            _calls.clear()
        try:
            fut = asyncio.run_coroutine_threadsafe(
                asyncio.wait_for(_run(question, n), timeout=TIMEOUT), _get_loop())
            out = fut.result()
        except Exception as e:                   # noqa: BLE001
            # 失败也把轨迹打出来 —— 步数耗尽时最需要知道它到底查了什么
            _log("✗✗ 失败 %.1fs：%s: %s"
                 % (time.time() - t0, type(e).__name__, str(e)[:300]))
            _log("已查过：%s" % (" | ".join(_trace()[-12:]) or "（一次工具都没调）"))
            print("[agent] FAILED %s: %s | trace=%s"
                  % (type(e).__name__, str(e)[:200], _trace()[-12:]), flush=True)
            raise
    out["elapsed"] = round(time.time() - t0, 1)
    if not out["answer"]:
        # 空答案 = 失败，绝不伪装成「已回答」
        _log("✗✗ 空答案：模型没给 final_output（步数耗尽或空回复）")
        raise RuntimeError("agent 未产出答案（可能步数耗尽或模型空回复）")
    return out


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):           # 安静：日志走 stdout 由 compose 收
        return

    def _send(self, code: int, obj: dict) -> None:
        raw = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):                            # noqa: N802
        if self.path.rstrip("/") in ("/health", ""):
            self._send(200, {"ok": True, "model": _model_id(),
                             "repo": REPO, "max_turns": MAX_TURNS,
                             "max_tools": _MAX_TOOLS, "timeout": TIMEOUT,
                             "http_timeout": HTTP_TIMEOUT,
                             "sdk": "openai-agents %s"
                                    % getattr(_agents, "__version__", "?")})
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):                           # noqa: N802
        if self.path.rstrip("/") != "/ask":
            self._send(404, {"error": "not found"})
            return
        try:
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n) or b"{}")
        except Exception:                        # noqa: BLE001
            self._send(400, {"error": "bad json"})
            return
        q = str(body.get("question") or "")
        try:
            res = ask(q, body.get("max_turns"))
        except Exception as e:                   # noqa: BLE001
            # 显式化失败：不返回"看起来成功"的空答案（否则上层无从判断）
            self._send(500, {"error": "%s: %s" % (type(e).__name__, str(e)[:400])})
            return
        self._send(200, res)


def main() -> None:
    #: 启动横幅：把「挂了什么、模型是谁、边界多少」一次打全。
    #: 排查「源码仓库不存在」这类问题时，先看这几行就不用再猜挂载有没有生效。
    print("[agent] ================ 启动 ================", flush=True)
    print("[agent] repo=%s port=%d model=%s max_turns=%d max_tools=%d "
          "timeout=%.0fs http_timeout=%.0fs sdk=%s"
          % (REPO, PORT, _model_id(), MAX_TURNS, _MAX_TOOLS, TIMEOUT,
             HTTP_TIMEOUT, getattr(_agents, "__version__", "?")), flush=True)
    for rel in ("src/plugins/doupo", "src/plugins/qq_bridge/tools",
                "src/plugins/qq_bridge/pic/templates", "docs"):
        d = Path(REPO) / rel
        if d.is_dir():
            files = [x for x in d.rglob("*") if x.is_file()]
            print("[agent] 挂载 %-42s OK  %d 个文件" % (rel, len(files)), flush=True)
        else:
            print("[agent] 挂载 %-42s 缺失（bind mount 没生效？）" % rel, flush=True)
    srv = ThreadingHTTPServer(("0.0.0.0", PORT), _Handler)
    print("[agent] listening on %d" % PORT, flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
