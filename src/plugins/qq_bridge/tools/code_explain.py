# -*- coding: utf-8 -*-
"""tools.code_explain —— 斗破源码讲解用的**只读**检索模块（2026-11-17）。

用途：AI 模式下群友问「斗破某功能/口令是怎么设计的」，模型调本工具拿到
相关源码片段，再用自己的话把设计逻辑讲清楚。工具只喂素材，讲解由模型完成。

安全（默认拒绝，白名单制）：
  * 只放 doupo 业务源码 / 斗破卡片模板 / 斗破设计文档；
  * 排除 test_* / _gen_* / _verify_* / _throttle 等脚本与工具脚本；
  * 拒绝绝对路径、路径穿越、符号链接逃逸；
  * 单次结果文件数 / 行数封顶（tools.registry 另有 6000 字兜底截断）。

纪律：本模块零 nonebot / 零主包依赖，可裸单测；**只有读，没有任何写操作**；
返回给模型的文本里不出现「只读 / 不能改」之类字样（讲设计时不主动声明）。
"""
from __future__ import annotations

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

_MAX_FILES = 3          # 单次最多喂给模型几个文件
_MAX_LINES_PER_FILE = 50
_CONTEXT_BEFORE = 6
_CONTEXT_AFTER = 7
# 中文话题里这些 2~4 gram 无区分度，去掉免得刷屏
_STOP = {
    "怎么", "如何", "设计", "逻辑", "功能", "什么", "为什", "什么", "这个",
    "那个", "看看", "讲讲", "说说", "帮我", "一下", "的设", "设计逻", "计逻辑",
    "是怎么", "么设计", "介绍", "说明", "原理", "机制", "实现",
}
_CJK_RE = re.compile(r"[\u4e00-\u9fff]+")
_WORD_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{2,}")


class CodeExplainer:
    """斗破源码只读检索器。root 可注入（测试用 tmp 树），默认仓库根。"""

    def __init__(self, root: Path | None = None) -> None:
        # code_explain.py 在 <root>/src/plugins/qq_bridge/tools/ 下 → parents[4] = root
        self.root = Path(root).resolve() if root else Path(__file__).resolve().parents[4]
        self._cache: dict[str, str] = {}      # rel(posix) -> 文本
        self._mtime: dict[str, float] = {}

    # ---------------- 白名单与路径安全 ----------------
    def _iter_allowed(self) -> list[Path]:
        """返回白名单内全部文件（已 resolve，均已确认未逃逸）。"""
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

    # ---------------- 文件地图 ----------------
    def map_text(self) -> str:
        """文件地图：相对路径 + 一句话职责（取模块 docstring 首行）。"""
        lines: list[str] = []
        for p in self._iter_allowed():
            text = self._load(p)
            doc = ""
            m = re.search(r'^\s*"""\s*(.+?)[。.\n]', text, re.M)
            if m:
                doc = m.group(1).strip()[:46]
            lines.append("%s —— %s" % (self._rel(p), doc or p.stem))
        return "\n".join(lines)

    # ---------------- 话题 → 检索词 ----------------
    @staticmethod
    def _tokens(topic: str) -> list[str]:
        """从中文话题抽 2/3/4 gram + 英文标识符，长词优先（命中更精准）。"""
        toks: set[str] = set()
        for run in _CJK_RE.findall(topic or ""):
            for n in (2, 3, 4):
                for i in range(len(run) - n + 1):
                    g = run[i:i + n]
                    if g not in _STOP:
                        toks.add(g)
        for w in _WORD_RE.findall(topic or ""):
            toks.add(w.lower())
        # 长词优先；同长按字典序保证结果稳定
        return sorted(toks, key=lambda s: (-len(s), s))

    @staticmethod
    def _score(rel: str, text: str, toks: list[str]) -> int:
        name = rel.rsplit("/", 1)[-1].lower()
        head = "\n".join(text.splitlines()[:6]).lower()
        low = text.lower()
        s = 0
        for t in toks:
            if t in name:
                s += len(t) * 6
            if t in head:
                s += len(t) * 3
            s += min(low.count(t), 12) * len(t)
        return s

    @staticmethod
    def _snippets(text: str, toks: list[str]) -> tuple[str, bool]:
        """命中行 ±上下文的合并片段，带行号。返回 (片段, 是否截断)。"""
        lines = text.splitlines()
        low = [ln.lower() for ln in lines]
        hits = [i for i, ln in enumerate(low)
                if any(t in ln for t in toks)]
        if not hits:
            head = lines[:_MAX_LINES_PER_FILE]
            return "\n".join("%4d| %s" % (i + 1, ln)
                             for i, ln in enumerate(head)), len(lines) > len(head)
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
        truncated = False
        for a, b in ranges:
            room = _MAX_LINES_PER_FILE - used
            if room <= 0:
                truncated = True
                break
            end = min(b, a + room)
            out.extend("%4d| %s" % (i + 1, lines[i]) for i in range(a, end))
            used += end - a
            if end < b:
                truncated = True
            out.append("  ...")
        return "\n".join(out).rstrip("  ...\n"), truncated

    # ---------------- 主入口 ----------------
    def explain(self, topic: str) -> str:
        """topic = 用户想了解的功能/口令/问题。返回给模型当素材的源码文本。"""
        topic = (topic or "").strip()
        files = self._iter_allowed()
        if not topic:
            return "【斗破源码文件地图】\n" + self.map_text()
        toks = self._tokens(topic)
        if not toks:
            return "话题里没有可检索的关键词。\n【斗破源码文件地图】\n" + self.map_text()
        scored: list[tuple[int, Path, str]] = []
        for p in files:
            rel = self._rel(p)
            text = self._load(p)
            sc = self._score(rel, text, toks)
            if sc > 0:
                scored.append((sc, p, text))
        scored.sort(key=lambda x: (-x[0], self._rel(x[1])))
        if not scored:
            return ("没在斗破源码里定位到「%s」。\n【斗破源码文件地图】\n%s"
                    % (topic[:40], self.map_text()))
        parts = ["【斗破源码速查】话题：%s" % topic[:60]]
        for _sc, p, text in scored[:_MAX_FILES]:
            rel = self._rel(p)
            snip, cut = self._snippets(text, toks)
            parts.append("\n===== %s =====\n%s%s" % (
                rel, snip, "\n（此文件还有更多内容，仅摘相关片段）" if cut else ""))
        return "\n".join(parts)


#: 默认实例（root 由 __file__ 推导；热更新靠 mtime 自失效）
_default: CodeExplainer | None = None


def explain(topic: str) -> str:
    """模块级快捷入口（deps 注入用）。"""
    global _default
    if _default is None:
        _default = CodeExplainer()
    return _default.explain(topic)
