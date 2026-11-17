# -*- coding: utf-8 -*-
"""tools.code_explain —— 斗破源码讲解用的**只读**检索模块（2026-11-17）。

用途：AI 模式下群友问「斗破某功能/口令是怎么设计的」，模型调本工具拿到
模块清单与源码，再用自己的话把设计逻辑讲清楚。工具只喂素材，讲解由模型完成。

## 为什么不靠「关键词→文件名」的别名表（2026-11-17 返工教训）

初版维护了一张中文别名表（斗技→skill、宗门→faction…），每遇到一个新话题
答不对就加一条。用户原话：**「你这是打算一个模块改一次呢？我要是有一百个
模块你是不是打算让我测试一百次？」** —— 别名表本质是枚举，漏一个错一个，
新增模块还得再改代码，方向就是错的。

现方案把「判断该看哪个模块」交还给**模型自己**（它本来就懂中文语义）：
  1. ``explain(topic)`` 只返回**候选模块清单**（模块名 + 职责一句话），
     排序仅用于「把相关的排前面」，不要求排第一正确 —— 只要进候选，模型
     看职责就能挑对；
  2. ``explain(file="factions")`` 按模型点名的模块返回**设计说明 + 关键定义
     + 代码节选**；
  3. 话题完全匹配不上时返回**全部模块清单**（35 行，约 2K 字），模型照样能挑。
新增模块只要写了 docstring，自动出现在清单里，**不需要改本文件**。

## 安全（默认拒绝，白名单制）

  * 只放 doupo 业务源码 / 斗破卡片模板 / 斗破设计文档；
  * 排除 test_* / _gen_* / _verify_* / _throttle 等脚本；
  * 拒绝绝对路径、路径穿越、符号链接逃逸；
  * 单次结果行数封顶（tools.registry 另有 6000 字兜底截断）。

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

_MAX_CANDIDATES = 8        # 候选清单最多列几个模块（模型从中自选）
_MAX_DOC_CHARS = 1200      # 读模块时设计说明上限
_MAX_DEFS = 30             # 关键定义清单条数上限
_MAX_CODE_LINES = 60       # 代码节选行数上限
_MAX_DEF_DOC = 60          # 定义摘要字数
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
        for line in (doc or "").splitlines():
            s = line.strip().strip("：:·-—")
            if s and not set(s) <= set("-—=· "):
                return s[:limit]
        return ""

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

    # ---------------- 读指定模块 ----------------
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

    def _defs(self, text: str) -> list[str]:
        """关键定义清单：def/class + 首行 docstring 摘要。"""
        out: list[str] = []
        lines = text.splitlines()
        for i, line in enumerate(lines):
            m = re.match(r"^(def|class)\s+([A-Za-z_][A-Za-z0-9_]*)", line)
            if not m:
                continue
            sig = line.strip().rstrip(":")
            summary = ""
            for j in range(i + 1, min(i + 6, len(lines))):
                s = lines[j].strip()
                if s.startswith('"""') and len(s) > 3:
                    summary = s.strip('"')[:_MAX_DEF_DOC]
                    break
                if s.startswith('"""'):
                    for k in range(j + 1, min(j + 4, len(lines))):
                        t = lines[k].strip()
                        if t and not t.startswith('"""'):
                            summary = t[:_MAX_DEF_DOC]
                            break
                    break
                if s and not s.startswith(("#", "@")):
                    break
            out.append("- %s%s" % (sig, ("  // " + summary) if summary else ""))
            if len(out) >= _MAX_DEFS:
                out.append("- ……（更多定义见源码）")
                break
        return out

    def _excerpt(self, text: str, toks: list[str]) -> str:
        """代码节选：有话题词就给命中行附近，否则给文件头部。"""
        lines = text.splitlines()
        hits = [i for i, ln in enumerate(lines)
                if any(t in ln.lower() for t in toks)] if toks else []
        if not hits:
            head = lines[:_MAX_CODE_LINES]
            return "\n".join("%4d| %s" % (i + 1, ln)
                             for i, ln in enumerate(head))
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
            room = _MAX_CODE_LINES - used
            if room <= 0:
                out.append("  ...（节选到此，需要更多可用别的模块名或看源码）")
                break
            end = min(b, a + room)
            out.extend("%4d| %s" % (i + 1, lines[i]) for i in range(a, end))
            used += end - a
            if used >= _MAX_CODE_LINES:
                out.append("  ...（节选到此）")
                break
        return "\n".join(out)

    def read_module(self, name: str, topic: str = "") -> str:
        p, alts = self._resolve_module(name)
        if p is None:
            if alts:
                return ("「%s」匹配到多个模块，请用完整文件名再指定一次：\n%s"
                        % (name, "\n".join(alts[:12])))
            return ("没找到模块「%s」。可用模块：\n%s" % (name, self.map_text()))
        text = self._load(p)
        doc = self._docstring(text)
        if len(doc) > _MAX_DOC_CHARS:
            doc = doc[:_MAX_DOC_CHARS] + "……"
        toks = self._tokens(topic) if topic else []
        defs = self._defs(text)
        parts = ["【斗破模块】%s" % self._rel(p)]
        if doc:
            parts.append("【设计说明】\n" + doc)
        if defs:
            parts.append("【关键定义】\n" + "\n".join(defs))
        parts.append("【代码节选】\n" + self._excerpt(text, toks))
        return "\n\n".join(parts)

    # ---------------- 主入口 ----------------
    def explain(self, topic: str = "", file: str = "") -> str:
        """模型入口。

        * 传 ``file``：读该模块（设计说明 + 关键定义 + 代码节选）；
        * 只传 ``topic``：给候选模块清单（模块名 | 职责），由模型自己挑；
        * 都不传：给全部模块清单。
        """
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
                 "（挑最相关的 1~2 个，用 file 参数读详细设计与代码）"]
        for _sc, p, text in picked:
            name = p.name[:-3] if p.name.endswith(".py") else p.stem
            doc = self._desc(text, 60)
            lines.append("- %s | %s" % (name, doc or "（无模块说明）"))
        return "\n".join(lines)


#: 默认实例（root 由 __file__ 推导；热更新靠 mtime 自失效）
_default: CodeExplainer | None = None


def explain(topic: str = "", file: str = "") -> str:
    """模块级快捷入口（deps 注入用）。"""
    global _default
    if _default is None:
        _default = CodeExplainer()
    return _default.explain(topic, file)
