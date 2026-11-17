# -*- coding: utf-8 -*-
"""斗破源码讲解（read-only FC 工具）验收测试（机器判定，2026-11-17）。

覆盖：
  - 路径安全：绝对/盘符/../ 反斜杠一律拒绝（默认拒绝）
  - 白名单：只 doupo 业务源码 / 斗破模板 / 斗破文档；.env、config.py
    （含密钥读取）等敏感文件绝不会被检索到
  - 检索功能：文件地图、话题定位、带行号、空话题兜底
  - 工具注册：safe 权限（群聊可用）、topic 参数、描述/提示不含「只读/不能改」
  - 只读纪律：模块源码无任何写操作（open('w')/write/unlink 等）

测试全部用 tmp 树注入 root，绝不读生产数据、更不写生产数据。
"""
from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_CE_PATH = _REPO / "src" / "plugins" / "qq_bridge" / "tools" / "code_explain.py"


def _load_ce():
    spec = importlib.util.spec_from_file_location("ce_under_test", _CE_PATH)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _make_tree(tmp_path: Path) -> Path:
    """造一个最小白名单树：含业务源码/模板/文档 + 白名单外的敏感文件。"""
    (tmp_path / "src/plugins/doupo").mkdir(parents=True)
    (tmp_path / "src/plugins/qq_bridge/pic/templates").mkdir(parents=True)
    (tmp_path / "docs").mkdir(parents=True)
    (tmp_path / "src/plugins/doupo/core.py").write_text(
        '"""斗破 · 核心逻辑。"""\n'
        "def tower_elevator(floor):\n"
        "    # 电梯直达失败层，消耗金币，满状态挑战\n"
        "    return floor - 1\n", encoding="utf-8")
    (tmp_path / "src/plugins/doupo/test_core.py").write_text(
        "def test_x():\n    assert True\n", encoding="utf-8")
    (tmp_path / "src/plugins/qq_bridge/pic/templates/doupo_battle.py").write_text(
        '"""斗破战斗回看卡模板。"""\nFS_BODY = 18\n', encoding="utf-8")
    (tmp_path / "src/plugins/qq_bridge/pic/templates/warframe.py").write_text(
        "SECRET_IN_TEMPLATE = 1\n", encoding="utf-8")
    (tmp_path / "docs/斗破无限塔_设计方案.md").write_text(
        "# 斗破无限塔电梯 · 周副本设计\n周一快照锁定个人难度。\n", encoding="utf-8")
    (tmp_path / "docs/其他项目.md").write_text("不相关\n", encoding="utf-8")
    # 白名单外敏感文件
    (tmp_path / ".env").write_text("AI_API_KEY=sk-SHOULD-NOT-LEAK\n", encoding="utf-8")
    (tmp_path / "src/plugins/qq_bridge/config.py").write_text(
        "BOT_OWNER_QQ = {1}\n", encoding="utf-8")
    return tmp_path


# ---------------- 路径安全（默认拒绝）----------------
def test_safe_rel_rejects_evil_paths():
    ce = _load_ce()
    for bad in ("/etc/passwd", "D:/x", "D:\\x", "..\\.env", "../../.env",
                "src/../.env", "", None):
        assert ce.CodeExplainer._is_safe_rel(bad) is False, bad
    assert ce.CodeExplainer._is_safe_rel("src/plugins/doupo/core.py") is True


def test_whitelist_excludes_secrets_and_tests(tmp_path):
    ce = _load_ce()
    root = _make_tree(tmp_path)
    ex = ce.CodeExplainer(root=root)
    rels = [ex._rel(p) for p in ex._iter_allowed()]
    assert "src/plugins/doupo/core.py" in rels
    assert "docs/斗破无限塔_设计方案.md" in rels
    assert "src/plugins/qq_bridge/pic/templates/doupo_battle.py" in rels
    # 测试文件 / 无关模板 / 无关文档 / 密钥文件 / 主配置 —— 全部排除
    for banned in ("test_core.py", "warframe.py", "其他项目.md",
                   ".env", "qq_bridge/config.py"):
        assert not any(banned in r for r in rels), banned


def test_explain_never_leaks_secret(tmp_path):
    ce = _load_ce()
    root = _make_tree(tmp_path)
    ex = ce.CodeExplainer(root=root)
    for topic in ("电梯", "API Key", "BOT_OWNER", "core", "周副本"):
        out = ex.explain(topic)
        assert "sk-SHOULD-NOT-LEAK" not in out
        assert "BOT_OWNER_QQ" not in out


# ---------------- 检索功能 ----------------
def test_map_text_lists_allowed_files_with_doc(tmp_path):
    ce = _load_ce()
    ex = ce.CodeExplainer(root=_make_tree(tmp_path))
    m = ex.map_text()
    assert "src/plugins/doupo/core.py" in m
    assert "核心逻辑" in m
    assert "test_core.py" not in m


def test_explain_topic_finds_source_with_line_numbers(tmp_path):
    ce = _load_ce()
    ex = ce.CodeExplainer(root=_make_tree(tmp_path))
    out = ex.explain("爬塔电梯怎么设计的")
    assert "斗破源码速查" in out
    assert "core.py" in out or "斗破无限塔" in out
    # 带行号
    assert re.search(r"^\s*\d+\|", out, re.M) is not None


def test_relevance_ranks_topic_doc_above_long_unrelated_file(tmp_path):
    """相关度排序：主题命中的文档/源码应排在「靠篇幅刷分」的长文件之前。

    真机教训：正文按 count 累加会让 commands.py 这类长文件虚高，把真正讲
    「电梯」的设计文档挤出前三（随后被 6000 字截断，模型拿不到关键内容）。
    """
    import importlib.util as _ilu
    spec = _ilu.spec_from_file_location("ce_prod", _CE_PATH)
    ce = _ilu.module_from_spec(spec)
    spec.loader.exec_module(ce)
    ex = ce.CodeExplainer()                  # 用真实仓库源码树
    toks = ex._tokens("爬塔失败后电梯直达上次失败层怎么设计的")
    scored = []
    for p in ex._iter_allowed():
        rel = ex._rel(p)
        sc = ex._score(rel, ex._load(p), toks)
        if sc > 0:
            scored.append((sc, rel))
    scored.sort(key=lambda x: (-x[0], x[1]))
    top = [rel for _sc, rel in scored[:3]]
    # 电梯主题前三必须出现真正讲它的文件，而不是只有长文件 commands.py
    assert any(("tower" in r) or ("无限塔" in r) or ("core.py" in r) for r in top), top


def test_explain_empty_topic_falls_back_to_map(tmp_path):
    ce = _load_ce()
    ex = ce.CodeExplainer(root=_make_tree(tmp_path))
    out = ex.explain("")
    assert "文件地图" in out
    assert "src/plugins/doupo/core.py" in out


# ---------------- 工具注册口径 ----------------
def test_tool_registered_safe_with_topic_and_no_readonly_wording():
    import sys as _sys
    _sys.path.insert(0, str(_REPO / "src"))
    from plugins.qq_bridge.tools.registry import ToolRegistry, RISK_SAFE
    from plugins.qq_bridge.tools.builtins import register_builtin_tools

    reg = ToolRegistry()

    async def cb(topic, ctx):
        return "stub"

    names = register_builtin_tools(reg, {"doupo_code_explain": cb})
    assert "doupo_code_explain" in names
    t = reg.get("doupo_code_explain")
    assert t.risk == RISK_SAFE          # 群聊可用
    assert "topic" in t.parameters["properties"]
    assert "topic" in t.parameters["required"]
    # 工具描述不主动声明「只读/不能改」
    for w in ("只读", "不能改", "readonly", "不可编辑"):
        assert w not in t.description


def test_no_write_operations_in_module_source():
    """只读纪律：模块源码不得出现任何写文件操作。"""
    src = _CE_PATH.read_text(encoding="utf-8")
    for pattern in (r"open\([^)]*['\"][wa]", r"\.write_text\(", r"\.write\(",
                    r"unlink\(", r"rmtree\(", r"os\.remove\("):
        assert re.search(pattern, src) is None, pattern
