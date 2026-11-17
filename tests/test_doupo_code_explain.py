# -*- coding: utf-8 -*-
"""斗破源码讲解（read-only FC 工具）验收测试（机器判定，2026-11-17）。

覆盖：
  - 路径安全：绝对/盘符/../ 反斜杠一律拒绝（默认拒绝）
  - 白名单：只 doupo 业务源码 / 斗破模板 / 斗破文档；.env、config.py
    （含密钥读取）等敏感文件绝不会被检索到
  - 两段式：topic→候选清单（带职责）；file→设计说明+关键定义+代码节选
  - **通用性（回应用户「一百个模块」批评）**：
      * 白名单里每个模块都必须出现在 map_text（新增模块自动被覆盖）
      * 每个模块名都必须能被 read_module 读到（不靠手工别名表）
  - 工具注册：safe 权限（群聊可用）、topic+file 参数、描述不含「只读/不能改」
  - 只读纪律：模块源码无任何写操作

测试全部用 tmp 树注入 root 或只读真实仓库，绝不写生产数据。
"""
from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_CE_PATH = _REPO / "src" / "plugins" / "qq_bridge" / "tools" / "code_explain.py"


def _load_ce(name: str = "ce_under_test"):
    spec = importlib.util.spec_from_file_location(name, _CE_PATH)
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
    (tmp_path / "src/plugins/doupo/factions.py").write_text(
        '"""斗破门派系统 · 纯数据层。\n\n'
        "用途：新手入道随机分配宗门。\n"
        '"""\nFACTIONS = ["云岚宗"]\n\n\n'
        "def change_faction(p, fid):\n"
        '    """转宗：保留通用技，赠一门新宗门入门技。"""\n'
        "    return p\n", encoding="utf-8")
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
    for banned in ("test_core.py", "warframe.py", "其他项目.md",
                   ".env", "qq_bridge/config.py"):
        assert not any(banned in r for r in rels), banned


def test_never_leaks_secret(tmp_path):
    ce = _load_ce()
    ex = ce.CodeExplainer(root=_make_tree(tmp_path))
    for out in (ex.explain("API Key"), ex.explain("BOT_OWNER"), ex.map_text()):
        assert "sk-SHOULD-NOT-LEAK" not in out
        assert "BOT_OWNER_QQ" not in out


# ---------------- 两段式：候选清单 → 读模块 ----------------
def test_topic_returns_candidate_list_with_responsibility(tmp_path):
    ce = _load_ce()
    ex = ce.CodeExplainer(root=_make_tree(tmp_path))
    out = ex.explain("宗门系统是怎么设计的")
    assert "【斗破候选模块】" in out
    assert "factions" in out
    assert "门派系统" in out          # 带职责，模型据此判断


def test_read_module_gives_design_doc_defs_and_code(tmp_path):
    ce = _load_ce()
    ex = ce.CodeExplainer(root=_make_tree(tmp_path))
    out = ex.read_module("factions")
    assert "【斗破模块】" in out
    assert "【设计说明】" in out and "用途：新手入道随机分配宗门" in out
    assert "【关键定义】" in out
    assert "【代码节选】" in out
    # 不需要 topic 也能读
    assert "门派系统" in ex.explain(file="factions")


def test_read_module_accepts_full_path_and_reports_unknown(tmp_path):
    ce = _load_ce()
    ex = ce.CodeExplainer(root=_make_tree(tmp_path))
    assert "core" in ex.read_module("src/plugins/doupo/core.py")
    miss = ex.read_module("不存在的模块")
    assert "没找到模块" in miss and "factions" in miss   # 兜底给全部清单


def test_ambiguous_module_name_asks_to_disambiguate(tmp_path):
    ce = _load_ce()
    ex = ce.CodeExplainer(root=_make_tree(tmp_path))
    (tmp_path / "src/plugins/doupo/skills.py").write_text('"""斗技内核。"""\n',
                                                         encoding="utf-8")
    (tmp_path / "src/plugins/doupo/doupo_skills.py").write_text('"""斗技卡。"""\n',
                                                              encoding="utf-8")
    ex2 = ce.CodeExplainer(root=tmp_path)
    out = ex2.read_module("skill")
    assert "匹配到多个模块" in out or "没找到模块" in out


# ---------------- 通用性：不靠枚举，新增模块自动覆盖 ----------------
def test_every_allowed_module_listed_in_map():
    """白名单里**每一个**文件都必须出现在模块清单里。

    这是对「一百个模块难道测一百次」的回答：不逐个枚举模块名，
    而是遍历白名单验证覆盖 —— 新增模块只要写了 docstring 就自动出现。
    """
    ce = _load_ce("ce_cov")
    ex = ce.CodeExplainer()          # 真实仓库
    listed = ex.map_text()
    missing = []
    for p in ex._iter_allowed():
        name = p.name[:-3] if p.name.endswith(".py") else p.stem
        if name not in listed:
            missing.append(name)
    assert not missing, missing


def test_every_module_name_is_readable():
    """每个模块名都必须能被 read_module 读到（含唯一化/消歧路径）。"""
    ce = _load_ce("ce_read")
    ex = ce.CodeExplainer()
    bad = []
    for p in ex._iter_allowed():
        name = p.name[:-3] if p.name.endswith(".py") else p.stem
        out = ex.read_module(name)
        if "【斗破模块】" not in out:
            bad.append((name, out[:60]))
    assert not bad, bad


def test_common_topics_put_right_module_in_candidates():
    """常见话题的**正确模块必须进候选**（不要求排第一 —— 模型会看职责自选）。"""
    ce = _load_ce("ce_topics")
    ex = ce.CodeExplainer()
    cases = {
        "宗门系统是怎么设计的": "factions",
        "斗技怎么学": "skills",
        "爬塔电梯怎么设计的": "tower_session",
        "周副本难度怎么定的": "weekly_data",
        "异火怎么养成": "flame_skills",
    }
    failed = {}
    for topic, expect in cases.items():
        out = ex.explain(topic)
        if expect not in out:
            failed[topic] = out[:300]
    assert not failed, failed


# ---------------- 工具注册口径 ----------------
def test_tool_registered_safe_with_two_params_and_no_readonly_wording():
    import sys as _sys
    _sys.path.insert(0, str(_REPO / "src"))
    from plugins.qq_bridge.tools.registry import ToolRegistry, RISK_SAFE
    from plugins.qq_bridge.tools.builtins import register_builtin_tools

    reg = ToolRegistry()

    async def cb(topic, file, ctx):
        return "stub"

    names = register_builtin_tools(reg, {"doupo_code_explain": cb})
    assert "doupo_code_explain" in names
    t = reg.get("doupo_code_explain")
    assert t.risk == RISK_SAFE          # 群聊可用
    assert {"topic", "file"} <= set(t.parameters["properties"])
    assert t.parameters["required"] == []   # 都可选：能只传 topic 或只传 file
    for w in ("只读", "不能改", "readonly", "不可编辑"):
        assert w not in t.description


def test_no_write_operations_in_module_source():
    """只读纪律：模块源码不得出现任何写文件操作。"""
    src = _CE_PATH.read_text(encoding="utf-8")
    for pattern in (r"open\([^)]*['\"][wa]", r"\.write_text\(", r"\.write\(",
                    r"unlink\(", r"rmtree\(", r"os\.remove\("):
        assert re.search(pattern, src) is None, pattern


def test_no_hardcoded_alias_table():
    """禁止退回「中文关键词→文件名」手工别名表。

    2026-11-17 返工教训：别名表是枚举，漏一个错一个，新增模块还得改代码。
    正确的做法是让模型自己看模块清单的职责来选。
    """
    src = _CE_PATH.read_text(encoding="utf-8")
    assert "_ALIAS" not in src, "别名表已废弃，不要再加回来"
