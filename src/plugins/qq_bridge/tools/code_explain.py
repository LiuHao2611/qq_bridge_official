# -*- coding: utf-8 -*-
"""tools.code_explain —— 斗破源码讲解用的**只读**检索模块（2026-11-17 重构）。

用途：AI 模式下群友问「斗破某功能/口令是怎么设计的」，模型调本工具拿到素材，
再用自己的话把设计逻辑讲清楚。工具只喂素材，讲解由模型完成。

## 架构（搬运自三个成熟项目，见 docs/AI源码讲解重构_权威项目调研.md）

三段式，与 aider / repomix / 三家 code-index MCP 服务收敛出的口径一致：

  ① 概览 outline —— 全部符号的多行签名 + 行号 + 一行说明（repomix ``--compress`` 口径：
     保留真实签名、折叠函数体）。回答「这模块有什么」。
  ② 取体 body    —— 按符号名返回**完整函数体**（grep-ast ``TreeContext`` 口径：真实源码行，
     过大时用 ``⋮`` 折叠）。回答「这个函数到底怎么写的」。
  ③ 定位 where   —— ``文件:行号`` → 最内层符号 + 完整外层链（code-index MCP 的
     ``where_am_i`` 口径）。让模型看到概览里的行号能直接跳进去。

## 为什么不能只给签名（2026-11-17 真实事故）

上一版 ``_defs()`` 只输出 ``签名 // 一行摘要``，函数体永不进上下文。群友问
「宗门的权重是怎么算的」，实测输出 4549 字符里 ``acc +=`` 出现 **0** 次、
``pool_map`` **0** 次 —— 18 行的 ``random_faction`` 一行都没给，模型只能瞎猜。
更糟的是「代码节选」按行级匹配，中文话题词命中的是模块 docstring 的散文，
于是把说明文字当代码贴了出来。两个缺陷叠加：**行级检索 + 无函数体**。

## 为什么解析后端用标准库 ``ast`` 而不是 tree-sitter

容器是**离线 wheel 构建**（``docker/Dockerfile.bridge``：``pip --no-index --find-links``），
任何新依赖都要手工预拉 manylinux wheel。``tree-sitter-language-pack`` 是把几十套
grammar 打成一个包，为一个只需要 Python 的只读讲解工具付这个代价不划算。
而白名单里 44 个文件全是 Python（5 个 md 走标题行），``ast`` 与 tree-sitter 同构
（``lineno`` / ``end_lineno`` / ``walk``），且是解释器本体，对新语法永远不滞后。
**算法照搬 grep-ast，解析层换成 ast** —— 零新增依赖、零 Dockerfile 改动。

## 为什么不用别名表（更早的返工教训）

初版维护了一张中文别名表（斗技→skill、宗门→faction…），每遇到新话题答不对就加一条。
用户原话：**「你这是打算一个模块改一次呢？我要是有一百个模块你是不是打算让我测试一百次？」**
—— 别名表本质是枚举，漏一个错一个。现方案把语义判断交还给模型：给**候选清单**和
**完整概览**，模型自己挑、自己点名。新增模块只要写了 docstring 就自动出现，不改本文件。

## 安全（默认拒绝，白名单制）

  * 只放 doupo 业务源码 / 斗破卡片模板 / 斗破设计文档；
  * 排除 test_* / _gen_* / _verify_* / _throttle 等脚本；
  * 拒绝绝对路径、路径穿越、符号链接逃逸；
  * 三段各有字符预算，``tools.registry`` 另有 6000 字兜底截断。

纪律：本模块零 nonebot / 零第三方依赖，可裸单测；**只有读，没有任何写操作**；
返回给模型的文本里不出现「只读 / 不能改」之类字样（讲设计时不主动声明）。
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

#: (相对仓库根的目录, 文件名 glob) —— 白名单全集，新增必须显式登记
_SPECS: tuple[tuple[str, str], ...] = (
    ("src/plugins/doupo", "*.py"),
    ("src/plugins/qq_bridge/pic/templates", "doupo_*.py"),
    ("docs", "斗破*.md"),
)
#: 文件名前缀黑名单（测试 / 一次性生成/校验脚本 / 已停用模块）
_EXCLUDE_PREFIX = ("test_", "_gen_", "_verify_", "_throttle")

_MAX_CANDIDATES = 8        # 候选清单最多列几个模块（模型从中自选）

# ---- 三段预算（合计必须 < AI_TOOL_RESULT_MAX_CHARS=6000）----
#: 总预算。tools.registry 会在 6000 字硬截断，一旦截在函数中间就是"半截代码"，
#: 比不给更糟（模型会把半截当全部）。所以本模块自己先算账，永远不靠上层截断。
_MAX_TOTAL_CHARS = 5300
_MAX_DOC_CHARS = 700       # 模块设计说明（docstring）上限
_MAX_OUTLINE_CHARS = 1600  # ① 概览上限，超了提示还有多少符号
_MAX_CODE_CHARS = 2800     # ② 代码段上限（按分数整段装，装不下整个跳过）
_MAX_SOLO_CODE = 3600      # 只读单个符号时，代码段可以更大
_CODE_RESERVE = 2400       # 有话题时给代码段预留的空间
#: 概览的两级预算：完整签名段 / 紧凑名单段。没有代码段时可以把名单放得更开。
_OUTLINE_FULL_TOPIC = 1150
_OUTLINE_COMPACT_TOPIC = 1150
_OUTLINE_FULL_NOTOPIC = 2000
_OUTLINE_COMPACT_NOTOPIC = 2900
_OUTLINE_MAX_SYMS = 400    # 概览最多列多少个符号（core.py 上百个，防刷屏）
_HEADER_MAX = 12           # 多行签名最多保留几行（grep-ast header_max 口径）
_MAX_BODY_LINES = 120      # 单个函数体超过这么多行才启用 ⋮ 折叠
_SIG_JOIN_MAX = 150        # 签名拼成一行后的字符上限

_MAX_CODE_LINES = 60       # md 文档无命中时给开头多少行
_CONTEXT_BEFORE = 3
_CONTEXT_AFTER = 4

#: 中文停用 2~4gram（无区分度，避免刷屏）
_STOP = {
    "怎么", "如何", "设计", "逻辑", "功能", "什么", "为什", "这个", "那个",
    "看看", "讲讲", "说说", "帮我", "一下", "的设", "设计逻", "计逻辑",
    "是怎么", "么设计", "介绍", "说明", "原理", "机制", "实现", "模块",
    "现在", "是不", "不是", "有没", "没有",
}
_CJK_RE = re.compile(r"[\u4e00-\u9fff]+")
_WORD_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{2,}")
_DEF_RE = re.compile(r"^(?:async\s+)?(?:def|class)\s+([A-Za-z_][A-Za-z0-9_]*)")
_AT_LINE_RE = re.compile(r"^(?P<f>[^:\s]+\.(?:py|md)):(?P<ln>\d+)$")


class _Sym:
    """一个符号（函数 / 类）。行号一律 1-based，含装饰器。

    刻意用普通类而不是 ``@dataclass``：本模块会被测试以
    ``importlib.util.module_from_spec`` 方式裸加载（不注册进 ``sys.modules``），
    而 ``dataclass`` 解析字符串注解时要求 ``sys.modules[cls.__module__]`` 存在，
    会在 import 阶段直接炸。这里字段少，手写 ``__init__`` 更稳。
    """

    __slots__ = ("name", "kind", "start", "end", "sig_start", "sig_end",
                 "doc", "qual", "depth", "node")

    def __init__(self, name: str, kind: str, start: int, end: int,
                 sig_start: int, sig_end: int, doc: str, qual: str,
                 depth: int, node: object = None) -> None:
        self.name = name
        self.kind = kind          # "def" | "class"
        self.start = start        # 起始行（含装饰器）
        self.end = end            # 结束行
        self.sig_start = sig_start
        self.sig_end = sig_end    # 签名结束行（body 首句前一行）
        self.doc = doc            # docstring 全文
        self.qual = qual          # 全名 Outer.inner
        self.depth = depth        # 嵌套深度，用于概览缩进
        self.node = node          # ast 节点（取子语句行用）

    @property
    def nlines(self) -> int:
        return self.end - self.start + 1


def _first_line(text: str, limit: int = 60) -> str:
    """docstring → 一行摘要。"""
    for line in (text or "").splitlines():
        s = line.strip().strip("：:·-—")
        if s and not set(s) <= set("-—=· "):
            return s[:limit]
    return ""


class _FileIndex:
    """单文件符号索引。

    ``ast`` 后端，等价替代 tree-sitter：``scopes`` / ``header`` 两张行表照搬
    grep-ast ``TreeContext`` 的结构，只是遍历从 tree-sitter 节点换成 ``ast`` 节点。
    """

    def __init__(self, rel: str, text: str) -> None:
        self.rel = rel
        self.lines = text.splitlines()
        self.syms: list[_Sym] = []
        self.by_name: dict[str, list[_Sym]] = {}
        self.by_qual: dict[str, _Sym] = {}
        self.parse_error = ""
        self.tree: ast.AST | None = None
        try:
            self.tree = ast.parse(text)
        except (SyntaxError, ValueError) as e:      # 回退：概览退正则，不抛错
            self.parse_error = "%s" % e
        if self.tree is not None:
            self._collect(self.tree, "", 0)
        for s in self.syms:
            self.by_name.setdefault(s.name, []).append(s)
            self.by_qual.setdefault(s.qual, s)
        # ---- grep-ast 的 scopes / header 行表 ----
        self.scopes: dict[int, set[int]] = {}
        self.header: dict[int, tuple[int, int]] = {}
        for s in self.syms:
            for ln in range(s.start, min(s.end, len(self.lines)) + 1):
                self.scopes.setdefault(ln, set()).add(s.start)
            self.header[s.start] = (
                s.sig_start,
                min(s.sig_end, s.sig_start + _HEADER_MAX - 1),
            )

    # ---------------- ast 遍历 ----------------
    def _collect(self, node: ast.AST, parent_qual: str, depth: int,
                 allow_data: bool = True) -> None:
        """收集符号。

        ``allow_data``：只在模块级与类体内把**顶层赋值**登记为符号，进到函数体就
        关掉 —— 否则满屏局部变量，反而淹没真正的符号。

        为什么必须收数据常量（2026-11-17 真机暴露）：斗破是**数据密集**仓库，
        ``factions.FACTIONS`` 单条 1209 行、``tower_data`` 32 个常量、``config`` 90 个
        常量。只索引 def/class 的话，模型问「各宗门权重是多少」时 ``symbol=POOL_UNLOCK``
        直接「没找到符号」，而答案就在这些常量里。
        """
        for child in ast.iter_child_nodes(node):
            if isinstance(
                child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
            ):
                kind = "class" if isinstance(child, ast.ClassDef) else "def"
                decos = [d.lineno for d in getattr(child, "decorator_list", []) or []]
                start = min(decos + [child.lineno])
                end = getattr(child, "end_lineno", None) or child.lineno
                body0 = child.body[0].lineno if child.body else end
                # 签名末行 = 首个 body 语句前一行；单行 def f(): return 1 时取 def 行
                sig_end = (child.lineno if body0 <= child.lineno
                           else max(start, body0 - 1))
                qual = ("%s.%s" % (parent_qual, child.name)
                        if parent_qual else child.name)
                self.syms.append(_Sym(
                    name=child.name, kind=kind, start=start, end=end,
                    sig_start=start, sig_end=sig_end,
                    doc=ast.get_docstring(child) or "", qual=qual,
                    depth=depth, node=child,
                ))
                # 类体内允许继续收常量；函数体内只找嵌套函数
                self._collect(child, qual, depth + 1,
                              allow_data=isinstance(child, ast.ClassDef))
                continue
            if not allow_data:
                continue
            target = None
            if isinstance(child, ast.Assign) and len(child.targets) == 1:
                target = child.targets[0]
            elif isinstance(child, ast.AnnAssign):
                target = child.target
            if not isinstance(target, ast.Name):
                continue
            end = getattr(child, "end_lineno", None) or child.lineno
            qual = ("%s.%s" % (parent_qual, target.id)
                    if parent_qual else target.id)
            self.syms.append(_Sym(
                name=target.id, kind="data", start=child.lineno, end=end,
                sig_start=child.lineno, sig_end=child.lineno,
                doc="", qual=qual, depth=depth, node=child,
            ))
        self.syms.sort(key=lambda s: (s.start, s.depth))

    # ---------------- 签名原文（repomix 口径：真实多行签名）----------------
    def sig_text(self, s: _Sym, join_max: int = _SIG_JOIN_MAX) -> str:
        a = max(1, s.sig_start)
        b = min(s.sig_end, len(self.lines))
        if b < a:
            return s.name
        raw = [self.lines[i - 1] for i in range(a, b + 1)]
        if s.kind == "data":
            # 数据常量：只留首行，超长截断；多行字面量标出行数（FACTIONS 1209 行）
            head = raw[0].strip()
            if len(head) > join_max:
                head = head[:join_max] + "…"
            if s.nlines > 1:
                head += "   · 共 %d 行" % s.nlines
            return head
        if len(raw) == 1:
            return raw[0].strip()
        # 多行签名全部保留参数，拼成一行；过长才截断
        joined = " ".join(x.strip() for x in raw)
        joined = re.sub(r"\(\s+", "(", joined)
        joined = re.sub(r"\s*,\s*", ", ", joined)
        joined = re.sub(r",\s*\)", ")", joined)      # "(a, b, )" → "(a, b)"
        joined = re.sub(r"\s+\)", ")", joined)
        joined = re.sub(r"\s+", " ", joined)
        if len(joined) > join_max:
            head = raw[0].strip()
            return head if len(head) <= join_max else head[:join_max] + "…"
        return joined

    def _stmt_head_lines(self, s: _Sym) -> list[int]:
        """该符号的直接子语句行 + 复合语句收尾行（grep-ast child_context 口径）。"""
        out: set[int] = set()
        if s.node is None:
            return []
        try:
            children = list(ast.iter_child_nodes(s.node))
        except Exception:
            return []
        for ch in children:
            ln = getattr(ch, "lineno", None)
            if ln:
                out.add(ln)
            en = getattr(ch, "end_lineno", None)
            if en and en != ln:
                out.add(en)
        return sorted(out)

    # ---------------- 渲染：真实源码行 + ⋮ 折叠（grep-ast format()）--------
    def _fmt(self, show: set[int], lo: int, hi: int) -> str:
        out: list[str] = []
        dots = lo not in show
        hi = min(hi, len(self.lines))
        for ln in range(lo, hi + 1):
            if ln not in show:
                if dots:
                    out.append("  ⋮")
                    dots = False
                continue
            out.append("%4d| %s" % (ln, self.lines[ln - 1]))
            dots = True
        return "\n".join(out)

    def window(self, a: int, b: int, center: int, span: int = 40) -> str:
        """以 ``center`` 为中心开窗，给一段真实源码行（``⋮`` 标出被跳过的部分）。

        用于「``文件:行号`` 落在一个上百科的大常量里」—— 此时从常量开头展示毫无意义
        （``FACTIONS`` 1209 行，180/230/290/350 行会得到一模一样的首屏，模型以为没变化，
        于是反复换行号重试，把工具轮次全烧光）。
        """
        a = max(1, a)
        b = min(b, len(self.lines))
        center = min(max(center, a), b)
        lo = max(a, center - span // 2)
        hi = min(b, lo + span - 1)
        rows = ["%4d| %s" % (ln, self.lines[ln - 1]) for ln in range(lo, hi + 1)]
        if lo > a:
            rows.insert(0, "  ⋮（前 %d 行略）" % (lo - a))
        if hi < b:
            rows.append("  ⋮（后 %d 行略）" % (b - hi))
        return "\n".join(rows)

    def _render_data(self, s: _Sym, a: int, b: int, max_lines: int) -> str:
        """数据常量：能给全就给全，给不全就给**开头一段真实条目**。

        数据常量不像函数有"骨架"可抽 —— ``FACTIONS`` 就是个 1209 行的嵌套 dict，
        抽子语句只会剩下首尾两行。真正有用的是**开头几条真实条目**（模型由此看到
        ``"weight": 30`` 这种实际数值），所以这里按行开窗，并明确标出总行数。
        """
        total = b - a + 1
        cap = max(8, max_lines)
        rows: list[str] = []
        used = 0
        for ln in range(a, min(b, a + cap - 1) + 1):
            row = "%4d| %s" % (ln, self.lines[ln - 1])
            if used + len(row) > _MAX_SOLO_CODE:
                break
            rows.append(row)
            used += len(row) + 1
        if len(rows) >= total:
            return "\n".join(rows)
        return "\n".join(rows) + (
            "\n  ⋮（%s 共 %d 行，此处显示前 %d 行；想跳到中间某段，"
            '用 symbol="%s:%d" 这种「文件:行号」写法）'
            % (s.name, total, len(rows), Path(self.rel).name, a + total // 2))

    def render_sym(self, s: _Sym, max_lines: int = _MAX_BODY_LINES) -> str:
        """完整函数体；超大时按 grep-ast 的 add_child_context 只露骨架。"""
        a = max(1, s.start)
        b = min(s.end, len(self.lines))
        if b < a:
            return ""
        if s.kind == "data":
            return self._render_data(s, a, b, max_lines)
        if b - a + 1 <= max_lines:
            return "\n".join("%4d| %s" % (ln, self.lines[ln - 1])
                             for ln in range(a, b + 1))
        show = set(range(s.sig_start, min(s.sig_end, b) + 1))   # 签名必给
        heads = self._stmt_head_lines(s)
        cap = max(5, min(int((b - a) * 0.10), 25))              # 10% 且夹在 [5,25]
        for ln in heads[:cap]:
            if a <= ln <= b:
                show.add(ln)
        show.add(a)
        show.add(b)
        # close_small_gaps：只差 1 行的空洞填上，避免满屏 ⋮
        prev = None
        for ln in sorted(show):
            if prev is not None and ln - prev == 2:
                show.add(prev + 1)
            prev = ln
        body = self._fmt(show, a, b)
        return body + "\n  ⋮（此函数 %d 行，已折叠中间部分；需要细节可再问具体分支）" % (b - a + 1)

    # ---------------- ① 概览 ----------------
    def outline(self, budget: int = _MAX_OUTLINE_CHARS,
                compact_budget: int = 0) -> str:
        """符号概览。两级：先用完整签名+说明，预算用完转**紧凑名单**（只留名字）。

        紧凑续列是必须的：``core.py`` 有 101 个符号，签名+说明约 6000 字装不下，
        但模型**必须拿到全部名字**才能用 ``symbol`` 点名 —— 否则又退回"问细致就
        只能猜"的老毛病。名字优先于签名，这是 hard requirement。
        """
        if not self.syms:
            if self.parse_error:
                return self._outline_by_regex(budget)
            return "（此模块没有 def/class，为纯数据或纯注册表）"
        out: list[str] = []
        used = 0
        i = 0
        for i, s in enumerate(self.syms):
            if i >= _OUTLINE_MAX_SYMS:
                break
            sig = self.sig_text(s)
            doc = _first_line(s.doc)
            line = "%sL%-5d %s%s" % (
                "  " * s.depth, s.start, sig, ("   // " + doc) if doc else "")
            if used + len(line) > budget:
                break
            out.append(line)
            used += len(line) + 1
        else:
            i = len(self.syms)          # 全部装下
            return "\n".join(out)

        # ---- 紧凑续列：只有名字，保证可点名 ----
        rest = self.syms[i:]
        if compact_budget > 0:
            out.append("  ⋮  （以下只列符号名，细节用 symbol 参数点名）")
            used = 0
            for k, s in enumerate(rest):
                if i + k >= _OUTLINE_MAX_SYMS:
                    out.append("  ⋮（还有 %d 个符号未列出）"
                               % (len(self.syms) - i - k))
                    break
                line = "%sL%-5d %s" % ("  " * s.depth, s.start, s.name)
                if used + len(line) > compact_budget:
                    out.append("  ⋮（还有 %d 个符号未列出，可用 file 参数分次读）"
                               % (len(self.syms) - i - k))
                    break
                out.append(line)
                used += len(line) + 1
        else:
            out.append("  ⋮（还有 %d 个符号未列出，可用 symbol 参数直接点名）" % len(rest))
        return "\n".join(out)

    def _outline_by_regex(self, budget: int) -> str:
        """语法错误文件的兜底概览（repomix 同款静默回退）。"""
        out, used = [], 0
        for i, line in enumerate(self.lines, 1):
            m = _DEF_RE.match(line)
            if not m:
                continue
            ln = "%sL%-5d %s" % (" " * (len(line) - len(line.lstrip())), i, line.strip())
            if used + len(ln) > budget:
                break
            out.append(ln)
            used += len(ln) + 1
        if not out:
            return "（模块解析失败且未识别到 def/class）"
        return "\n".join(out) + "\n  （文件语法不完整，概览为逐行识别结果）"

    # ---------------- ③ where_am_i：行号 → 最内层符号链 ----------------
    def chain_at(self, line: int) -> list[_Sym]:
        """返回包含该行的符号链，由外到内。"""
        best = [s for s in self.syms if s.start <= line <= s.end]
        best.sort(key=lambda s: (s.start, -s.end))
        return best


class CodeExplainer:
    """斗破源码只读检索器。root 可注入（测试用 tmp 树），默认仓库根。"""

    def __init__(self, root: Path | None = None) -> None:
        # code_explain.py 在 <root>/src/plugins/qq_bridge/tools/ 下 → parents[4] = root
        self.root = Path(root).resolve() if root else Path(__file__).resolve().parents[4]
        self._cache: dict[str, str] = {}      # rel(posix) -> 文本
        self._mtime: dict[str, float] = {}
        self._idx: dict[str, tuple[float, _FileIndex]] = {}
        # 白名单扫描结果短缓存：一次 explain 里 _resolve_module / _index / map_text
        # 会各自调用 _iter_allowed，40 个文件 × resolve() 全是 syscall。
        # 3 秒 TTL 足够覆盖单次对话，又不会让新增模块藏到重启之后。
        self._allowed: tuple[float, list[Path]] | None = None

    # ---------------- 白名单与路径安全 ----------------
    def _iter_allowed(self) -> list[Path]:
        """返回白名单内全部文件（已 resolve，均已确认未逃逸）。"""
        import time as _time
        now = _time.monotonic()
        if self._allowed is not None and now - self._allowed[0] < 3.0:
            return list(self._allowed[1])
        out: list[Path] = []
        for base_rel, pattern in _SPECS:
            base = (self.root / base_rel).resolve()
            if not base.is_dir():
                continue
            for p in sorted(base.glob(pattern)):
                if not p.is_file() or p.name.startswith(_EXCLUDE_PREFIX):
                    continue
                rp = p.resolve()
                # 符号链接 / .. 逃逸防护：解析后必须仍在 base 之内
                if base not in rp.parents and rp != base:
                    continue
                out.append(rp)
        self._allowed = (now, list(out))
        return out

    def _rel(self, p: Path) -> str:
        return p.resolve().relative_to(self.root).as_posix()

    @staticmethod
    def _is_safe_rel(rel: str) -> bool:
        """只接受普通相对路径；拒绝绝对路径 / 盘符 / 反斜杠 / .. 段。"""
        if not rel or not isinstance(rel, str):
            return False
        if rel.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:", rel):
            return False
        if "\\" in rel or ".." in rel.split("/"):
            return False
        return True

    # ---------------- 读取（带 mtime 失效缓存）----------------
    def _load(self, p: Path) -> str:
        rel = self._rel(p)
        try:
            mt = p.stat().st_mtime
        except OSError:
            return ""
        if self._cache.get(rel) is not None and self._mtime.get(rel) == mt:
            return self._cache[rel]
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""
        self._cache[rel] = text
        self._mtime[rel] = mt
        return text

    def _index(self, p: Path) -> _FileIndex:
        """符号索引，按 mtime 失效。"""
        rel = self._rel(p)
        try:
            mt = p.stat().st_mtime
        except OSError:
            mt = 0.0
        got = self._idx.get(rel)
        if got is not None and got[0] == mt:
            return got[1]
        idx = _FileIndex(rel, self._load(p))
        self._idx[rel] = (mt, idx)
        return idx

    # ---------------- 模块职责（docstring 首段）----------------
    @staticmethod
    def _docstring(text: str) -> str:
        """取模块开头 docstring —— 斗破各模块的 docstring 就是设计说明本身
        （如 skills.py 写明「三阶 / 四槽 / 随机释放 / 斗气消耗」）。"""
        m = re.search(r'"""(.+?)"""', text, re.S)
        if not m:
            return ""
        return m.group(1).strip()

    @staticmethod
    def _brief(doc: str, limit: int = 42) -> str:
        """docstring → 一句话职责（取首行有效内容）。"""
        return _first_line(doc, limit)

    @classmethod
    def _desc(cls, text: str, limit: int = 42) -> str:
        """模块职责：优先 docstring 首行；md 文档退化为第一个标题行。

        设计文档没有 ``\"\"\"`` docstring，不回退标题会显示「无模块说明」，
        模型就不知道该文档讲什么（2026-11-17 真机）。
        """
        d = cls._brief(cls._docstring(text), limit)
        if d:
            return d
        for line in (text or "").splitlines():
            s = line.strip()
            if s.startswith("#"):
                t = s.lstrip("#").strip()
                if t:
                    return t[:limit]
        return ""

    # ---------------- 模块清单 ----------------
    def map_text(self) -> str:
        """全部模块清单：``模块名 | 职责``。模型据此自己挑要读哪个。"""
        lines: list[str] = []
        for p in self._iter_allowed():
            name = p.name[:-3] if p.name.endswith(".py") else p.stem
            doc = self._desc(self._load(p))
            lines.append("%s | %s" % (name, doc or "（无模块说明）"))
        return "\n".join(lines)

    # ---------------- 话题 → 候选模块 ----------------
    @staticmethod
    def _tokens(topic: str) -> list[str]:
        """中文 2/3/4gram + 英文标识符，长词优先（命中更精准）。"""
        toks: set[str] = set()
        for run in _CJK_RE.findall(topic or ""):
            for n in (2, 3, 4):
                for i in range(len(run) - n + 1):
                    g = run[i:i + n]
                    if g not in _STOP:
                        toks.add(g)
        for w in _WORD_RE.findall(topic or ""):
            toks.add(w.lower())
        return sorted(toks, key=lambda s: (-len(s), s))

    @staticmethod
    def _score(rel: str, text: str, toks: list[str]) -> int:
        """候选排序打分（只用于「把相关的排前面」，不要求排第一正确）。

        分层依据（2026-11-17 真机教训）：
          * docstring 是模块**自述职责**，命中权重最高（×10）；
          * 文件名次之（英文词根精确匹配 +70 / 包含 +26）；
          * 正文命中权重最低且封顶 —— 否则 skill_book.py 因正文大量提到
            「宗门」会挤掉真正讲门派系统的 factions.py；
          * 卡片模板 ×0.6（问「怎么设计」多指玩法，不是绘制）。
        """
        name = rel.rsplit("/", 1)[-1].lower()
        stem = name.rsplit(".", 1)[0]
        stem_s = stem[:-1] if stem.endswith("s") else stem   # skills → skill
        doc = ""
        m = re.search(r'"""(.+?)"""', text, re.S)
        if m:
            doc = m.group(1).lower()
        low = text.lower()
        s = 0
        for t in toks:
            w = len(t)
            if t in doc:
                s += w * 10
            if t.isascii():
                if stem_s == t or stem == t:
                    s += 70
                elif t in name:
                    s += 26
            elif t in name:
                s += w * 12
            c = low.count(t)
            if c:
                s += w * (1 + min(c, 3))    # 正文最多按 4 次计，严格封顶
        if "/pic/templates/" in rel:
            s = int(s * 0.6)
        return s

    def candidates(self, topic: str) -> tuple[list[tuple[int, Path, str]], str]:
        """返回 (候选列表, 兜底文本)。候选为空时兜底文本=全部模块清单。"""
        toks = self._tokens(topic)
        if not toks:
            return [], self.map_text()
        scored: list[tuple[int, Path, str]] = []
        for p in self._iter_allowed():
            rel = self._rel(p)
            text = self._load(p)
            sc = self._score(rel, text, toks)
            if sc > 0:
                scored.append((sc, p, text))
        scored.sort(key=lambda x: (-x[0], self._rel(x[1])))
        return scored[:_MAX_CANDIDATES], self.map_text()

    # ---------------- 符号打分（aider RepoMap 权重公式）----------------
    @staticmethod
    def _symbol_score(s: _Sym, toks: list[str]) -> float:
        """符号级打分。搬 aider RepoMap 的 ``mul`` 权重，丢掉 PageRank。

        aider 原式：提及标识符 ×10、长 snake/camel(≥8) ×10、``_`` 私有 ×0.1、
        同名多定义(>5) ×0.1、引用方在上下文 ×50。这里 44 文件 / 900 符号的规模
        用不上图排序，只留权重当排序分。
        """
        lname = s.name.lower()
        doc = (s.doc or "").lower()
        sc = 0.0
        for t in toks:
            w = len(t)
            if t.isascii():
                if lname == t:
                    sc += 90
                elif t in lname:
                    sc += 40
            elif t in lname:
                sc += 30
            if t in doc:
                sc += w * 12
        mul = 1.0
        if len(s.name) >= 8 and ("_" in s.name or any(c.isupper() for c in s.name)):
            mul *= 1.5                      # 长标识符更有信息量
        if s.name.startswith("_"):
            mul *= 0.1                      # 私有符号
        if s.nlines > 80:
            mul *= 0.6                      # 巨型函数权重降，把预算让给聚焦实现
        return sc * mul

    def rank_symbols(self, idx: _FileIndex, toks: list[str],
                     top: int = 4) -> list[_Sym]:
        """按分数取前 top 个符号（分数 > 0 才返回）。"""
        if not toks:
            return []
        scored = [(self._symbol_score(s, toks), s) for s in idx.syms]
        scored = [(sc, s) for sc, s in scored if sc > 0]
        scored.sort(key=lambda x: (-x[0], x[1].start))
        return [s for _sc, s in scored[:top]]

    # ---------------- 模块名 → 文件 ----------------
    def _resolve_module(self, name: str) -> tuple[Path | None, list[str]]:
        """模块名 → 文件。支持 factions / factions.py / doupo/factions.py 全路径。

        精确同名优先；同名多命中（skills vs doupo_skills）时返回候选让模型再点名。
        """
        raw = (name or "").strip().replace("\\", "/")
        if not raw:
            return None, []
        base = raw.rsplit("/", 1)[-1]
        # 不预设扩展名：文档是 .md、源码是 .py，按 stem 比对才能同时命中
        stem_target = base[:-3] if base.endswith((".py", ".md")) else base
        exact: list[Path] = []
        loose: list[Path] = []
        for p in self._iter_allowed():
            if p.name == base or p.stem == stem_target:
                exact.append(p)
            elif stem_target in p.stem:
                loose.append(p)
        # 全路径指定时只认该路径
        if "/" in raw:
            hit = [p for p in exact + loose if self._rel(p) == raw.lstrip("/")]
            if hit:
                return hit[0], []
        if len(exact) == 1:
            return exact[0], []
        if exact:
            return None, [self._rel(p) for p in exact]
        if len(loose) == 1:
            return loose[0], []
        return None, [self._rel(p) for p in loose]

    # ---------------- md 文档（无 AST，走标题 + 命中段）----------------
    def _read_doc(self, p: Path, toks: list[str], doc: str) -> str:
        text = self._load(p)
        lines = text.splitlines()
        parts = ["【斗破文档】%s" % self._rel(p)]
        if doc:
            head = doc[:_MAX_DOC_CHARS]
            if len(doc) > _MAX_DOC_CHARS:
                head += "……"
            parts.append("【设计说明】\n" + head)
        heads = [(i + 1, ln) for i, ln in enumerate(lines) if ln.startswith("#")]
        if heads:
            brief = []
            for i, ln in heads[:_OUTLINE_MAX_SYMS]:
                lvl = len(ln) - len(ln.lstrip("#"))
                brief.append("%sL%-4d %s" % ("  " * (lvl - 1), i, ln.strip("# ").strip()))
            parts.append("【文档结构】\n" + "\n".join(brief))
        hits = [i for i, ln in enumerate(lines)
                if any(t in ln.lower() for t in toks)] if toks else []
        if hits:
            ranges: list[list[int]] = []
            for h in hits:
                a = max(0, h - _CONTEXT_BEFORE)
                b = min(len(lines), h + _CONTEXT_AFTER + 1)
                if ranges and a <= ranges[-1][1]:
                    ranges[-1][1] = max(ranges[-1][1], b)
                else:
                    ranges.append([a, b])
            out: list[str] = []
            used = 0
            for a, b in ranges:
                if used >= _MAX_CODE_CHARS:
                    out.append("  ⋮")
                    break
                for i in range(a, b):
                    row = "%4d| %s" % (i + 1, lines[i])
                    if used + len(row) > _MAX_CODE_CHARS:
                        out.append("  ⋮")
                        break
                    out.append(row)
                    used += len(row) + 1
            parts.append("【相关段落】\n" + "\n".join(out))
        else:
            head = lines[:_MAX_CODE_LINES]
            parts.append("【开头部分】\n" + "\n".join(
                "%4d| %s" % (i + 1, ln) for i, ln in enumerate(head)))
        return "\n\n".join(parts)

    # ---------------- 读模块（① 概览 + ② 取体）----------------
    def read_module(self, name: str, topic: str = "") -> str:
        p, alts = self._resolve_module(name)
        if p is None:
            if alts:
                return ("「%s」匹配到多个模块，请用完整文件名再指定一次：\n%s"
                        % (name, "\n".join(alts[:12])))
            return ("没找到模块「%s」。可用模块：\n%s" % (name, self.map_text()))
        text = self._load(p)
        doc = self._docstring(text)
        toks = self._tokens(topic) if topic else []

        if p.suffix == ".md":
            return self._read_doc(p, toks, doc)

        idx = self._index(p)
        head = "【斗破模块】%s（%d 行，%d 个符号）" % (
            self._rel(p), len(idx.lines), len(idx.syms))
        parts = [head]
        used = len(head) + 2

        doc_txt = ""
        if doc:
            cap = max(200, min(_MAX_DOC_CHARS, _MAX_TOTAL_CHARS - used - 600))
            doc_txt = doc[:cap] + ("……" if len(doc) > cap else "")
            parts.append("【设计说明】\n" + doc_txt)
            used += len(doc_txt) + 10

        # 概览预算 = 总预算 - 已用 - （有话题时给代码段预留）
        outline_cap = _MAX_TOTAL_CHARS - used - (_CODE_RESERVE if toks else 0) - 80
        if toks:
            full_b = min(_OUTLINE_FULL_TOPIC, max(400, outline_cap // 2))
            compact_b = max(0, max(300, outline_cap - full_b))
        else:
            full_b = _OUTLINE_FULL_NOTOPIC
            compact_b = max(600, outline_cap - full_b)
        outline_txt = idx.outline(full_b, compact_b)
        parts.append("【符号概览】\n" + outline_txt)
        used += len(outline_txt) + 10

        picked = self.rank_symbols(idx, toks)
        if picked:
            # 行数上限跟着剩余预算走：一个 120 行的函数约 4800 字，会独占整段预算
            code_budget = max(0, min(_MAX_CODE_CHARS, _MAX_TOTAL_CHARS - used - 200))
            blocks: list[str] = []
            for s in picked:
                if code_budget < 300:
                    break
                lines_cap = max(6, min(_MAX_BODY_LINES, code_budget // 38))
                block = idx.render_sym(s, max_lines=lines_cap)
                if not block:
                    continue
                if len(block) + 60 > code_budget and blocks:
                    break                       # 装不下就整个跳过，不切半截函数
                blocks.append("▸ %s  (L%d-%d，%d 行)\n%s"
                              % (s.qual, s.start, s.end, s.nlines, block))
                code_budget -= len(block) + 60
            if blocks:
                parts.append("【完整实现】话题「%s」命中的符号：\n\n"
                             % topic[:40] + "\n  ⋮\n\n".join(blocks))
        else:
            # 例子要挑公开**函数**：拿 _sp 这种私有工具函数、或 FACTIONS 这种数据常量
            # 当样例，模型会以为这模块就这水平/只有数据
            sample = next((s.name for s in idx.syms
                           if s.kind == "def" and not s.name.startswith("_")),
                          next((s.name for s in idx.syms
                                if not s.name.startswith("_")), "random_faction"))
            parts.append(
                "【要看某个函数的完整实现】用 symbol 参数点名，例如 "
                'symbol="%s"（可以填上面的符号名，或 "文件:行号"）。' % sample)
        return self._clamp("\n\n".join(parts))

    @staticmethod
    def _clamp(text: str) -> str:
        """最终硬夹紧。按行边界切（绝不切断一行代码），并明确标注已截断。

        上层 ``tools.registry`` 也会在 6000 字截断，但那是**裸截**——截在函数中间
        会让模型把半截当全部。本模块自己先算账，永远不把烂摊子留给上层。
        """
        if len(text) <= _MAX_TOTAL_CHARS:
            return text
        tail = "\n  ⋮（本次结果较长已截断，缩小话题范围或指定 symbol 再问）"
        cut = text[:_MAX_TOTAL_CHARS - len(tail)]
        return cut.rsplit("\n", 1)[0] + tail

    # ---------------- ③ 符号定位 / 取体 ----------------
    @staticmethod
    def _solo_body(idx: _FileIndex, s: _Sym) -> str:
        """单符号取体：能装下就给整段，装不下走 ⋮ 折叠。

        绝不裸切字符串 —— 切在函数中间，模型会把半截当全部，比不给更糟。
        """
        full = idx.render_sym(s, max_lines=10 ** 6)
        if len(full) <= _MAX_SOLO_CODE:
            return full
        # 超了就走 ⋮ 折叠；行数上限仍按字符预算折算，防止再超
        return idx.render_sym(s, max_lines=max(10, _MAX_SOLO_CODE // 38))

    def where(self, symbol: str, file: str = "") -> str:
        """``symbol`` 取体入口。支持三种写法：

          * ``random_faction``            —— 全库按名定位
          * ``factions.random_faction``   —— 按全名定位（嵌套类方法）
          * ``factions.py:1340``          —— 行号 → 最内层符号 + 外层链
        """
        raw = (symbol or "").strip()
        if not raw:
            return "请给出符号名，或「文件:行号」。"

        # ---- 文件:行号 ----
        m = _AT_LINE_RE.match(raw)
        if m:
            fname, ln = m.group("f"), int(m.group("ln"))
            p, alts = self._resolve_module(fname)
            if p is None:
                return "没找到文件「%s」。可用模块：\n%s" % (fname, self.map_text())
            idx = self._index(p)
            chain = idx.chain_at(ln)
            if not chain:
                # 不在任何符号里（模块 docstring / 空行 / 注释散段）时不能死胡同：
                # 给该行前后一小段真实代码，并指路到模块概览。
                lo = max(1, ln - 8)
                hi = min(len(idx.lines), ln + 8)
                snippet = "\n".join("%4d| %s" % (i, idx.lines[i - 1])
                                    for i in range(lo, hi + 1))
                return self._clamp(
                    "【定位】%s:%d 不在任何符号范围内（可能是模块说明、注释或空行）。\n"
                    "【附近代码】\n%s\n\n【该模块符号概览】\n%s"
                    % (self._rel(p), ln, snippet, idx.outline(900, 900)))
            head = chain[-1]
            outer = " › ".join("%s(L%d)" % (s.qual, s.start) for s in chain[:-1])
            out = ["【定位】%s:%d → %s  (%s，L%d-%d，%d 行)"
                   % (self._rel(p), ln, head.qual, head.kind,
                      head.start, head.end, head.nlines)]
            if outer:
                out.append("【外层作用域】" + outer)
            if head.doc:
                out.append("【说明】" + _first_line(head.doc, 200))
            # 大常量里按行号跳读时，必须给**以该行为中心**的窗口
            if head.kind == "data" and head.nlines > _MAX_BODY_LINES:
                body = idx.window(head.start, head.end, ln)
            else:
                body = self._solo_body(idx, head)
            out.append("【完整实现】\n" + body)
            return self._clamp("\n\n".join(out))

        # ---- 按名 / 按全名 ----
        want = raw
        scoped: Path | None = None
        if file.strip():
            p, _alts = self._resolve_module(file)
            if p is not None and p.suffix == ".py":
                scoped = p

        hits: list[tuple[Path, _FileIndex, _Sym]] = []
        pool = [scoped] if scoped is not None else self._iter_allowed()
        for p in pool:
            if p.suffix != ".py":
                continue
            idx = self._index(p)
            got = idx.by_qual.get(want) or []
            if not got:
                by = idx.by_name.get(want) or []
                got = by
            for s in (got if isinstance(got, list) else [got]):
                hits.append((p, idx, s))

        if not hits:
            return ("没找到符号「%s」。%s"
                    % (want, ("可以在概览里的名字里挑一个，"
                              "或用 doupo_code_explain(topic=...) 先拿模块清单。")))

        # 同名多处：全列出来，让模型指到文件
        if len(hits) > 1 and scoped is None:
            lines = ["符号「%s」在 %d 处出现，请加 file 参数指定：" % (want, len(hits))]
            for p, idx, s in hits[:12]:
                lines.append("- %s  L%d（%s，%d 行）%s"
                             % (idx.rel, s.start, s.kind, s.nlines,
                                ("  // " + _first_line(s.doc)) if s.doc else ""))
            return "\n".join(lines)

        p, idx, s = hits[0]
        out = ["【符号】%s  (%s，%s，L%d-%d，%d 行)"
               % (s.qual, s.kind, idx.rel, s.start, s.end, s.nlines)]
        if s.doc:
            out.append("【说明】" + _first_line(s.doc, 300))
        out.append("【完整实现】\n" + self._solo_body(idx, s))
        return self._clamp("\n\n".join(out))

    # ---------------- 主入口 ----------------
    def explain(self, topic: str = "", file: str = "", symbol: str = "") -> str:
        """模型入口（三个参数都可选，按组合决定返回什么）。

        * ``symbol``              读该符号的完整实现（可 ``文件:行号``）；
        * ``file``                读该模块：符号概览（签名+行号）+ 话题命中的完整实现；
        * 只给 ``topic``          给候选模块清单（模块名 | 职责），由模型自己挑；
        * 都不给                  给全部模块清单。
        """
        if (symbol or "").strip():
            return self.where(symbol, file)
        if (file or "").strip():
            return self.read_module(file, topic)
        topic = (topic or "").strip()
        if not topic:
            return "【斗破模块清单】\n" + self.map_text()
        picked, fallback = self.candidates(topic)
        if not picked:
            return ("没在斗破源码里定位到「%s」，下面是全部模块，挑一个用 "
                    "file 参数读：\n%s" % (topic[:40], fallback))
        lines = ["【斗破候选模块】话题：%s" % topic[:60],
                 "（挑最相关的 1~2 个，用 file 参数读概览与实现；"
                 "想知道某个函数怎么写，再用 symbol 点名）"]
        for _sc, p, text in picked:
            name = p.name[:-3] if p.name.endswith(".py") else p.stem
            doc = self._desc(text, 60)
            lines.append("- %s | %s" % (name, doc or "（无模块说明）"))
        return "\n".join(lines)


#: 默认实例（root 由 __file__ 推导；热更新靠 mtime 自失效）
_default: CodeExplainer | None = None


def explain(topic: str = "", file: str = "", symbol: str = "") -> str:
    """模块级快捷入口（deps 注入用）。"""
    global _default
    if _default is None:
        _default = CodeExplainer()
    return _default.explain(topic, file, symbol)
