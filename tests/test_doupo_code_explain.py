# -*- coding: utf-8 -*-
"""斗破源码讲解（read-only FC 工具）验收测试（机器判定，2026-11-17 重构后）。

覆盖：
  - 路径安全：绝对/盘符/../ 反斜杠一律拒绝（默认拒绝）
  - 白名单：只 doupo 业务源码 / 斗破模板 / 斗破文档；.env、config.py
    （含密钥读取）等敏感文件绝不会被检索到
  - 三段式：topic→候选清单；file→符号概览（签名+行号+说明）；symbol→完整实现
  - **细致问题回归（本次重构的核心目的）**：问「宗门权重怎么算」必须拿到
    ``random_faction`` 的**完整函数体**，而不只是签名
  - **通用性（回应用户「一百个模块」批评）**：
      * 白名单里每个模块都必须出现在 map_text（新增模块自动被覆盖）
      * 每个模块名都必须能被 read_module 读到（不靠手工别名表）
      * 每个模块的**每一个符号**都必须能按名取到完整实现
  - 工具注册：safe 权限（群聊可用）、topic/file/symbol 三参数、描述不含「只读/不能改」
  - 输出有界：任何调用都不得超过 registry 的 6000 字硬截断线
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

#: tools.registry 的硬截断线（AI_TOOL_RESULT_MAX_CHARS）。本模块必须自己先算账，
#: 永远不把「半截代码」留给上层裸截。
_HARD_CAP = 6000


def _load_ce(name: str = "ce_under_test"):
    spec = importlib.util.spec_from_file_location(name, _CE_PATH)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _load_tool_stack(prefix: str = "_tstack_ce"):
    """按文件加载 tools 栈（config/registry/scheduler/builtins），绕开包 __init__。

    直接 ``import plugins.qq_bridge.tools.builtins`` 会先执行 ``qq_bridge/__init__``，
    连带 import psutil / httpx / rapidocr 等一堆重依赖 —— 只是验「工具怎么注册的」
    不该为此付出装全套运行环境的代价。这里用影子包（只有 ``__path__``、不执行
    ``__init__``）把相对导入 ``..config`` / ``.registry`` 接上即可。
    """
    import sys as _sys
    import types as _types

    base = _REPO / "src" / "plugins" / "qq_bridge"
    for nm, path in ((prefix, base), (prefix + ".tools", base / "tools")):
        pkg = _types.ModuleType(nm)
        pkg.__path__ = [str(path)]
        _sys.modules[nm] = pkg

    def _load(rel: str, modname: str):
        spec = importlib.util.spec_from_file_location(modname, base / rel)
        m = importlib.util.module_from_spec(spec)
        _sys.modules[modname] = m
        spec.loader.exec_module(m)
        return m

    _load("config.py", prefix + ".config")
    registry = _load("tools/registry.py", prefix + ".tools.registry")
    _load("tools/scheduler.py", prefix + ".tools.scheduler")
    builtins = _load("tools/builtins.py", prefix + ".tools.builtins")
    return registry, builtins


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


# ---------------- 三段式：候选清单 → 概览 → 完整实现 ----------------
def test_topic_returns_candidate_list_with_responsibility(tmp_path):
    ce = _load_ce()
    ex = ce.CodeExplainer(root=_make_tree(tmp_path))
    out = ex.explain("宗门系统是怎么设计的")
    assert "【斗破候选模块】" in out
    assert "factions" in out
    assert "门派系统" in out          # 带职责，模型据此判断


def test_read_module_gives_doc_outline_and_symbol_hint(tmp_path):
    ce = _load_ce()
    ex = ce.CodeExplainer(root=_make_tree(tmp_path))
    out = ex.read_module("factions")
    assert "【斗破模块】" in out
    assert "【设计说明】" in out and "用途：新手入道随机分配宗门" in out
    assert "【符号概览】" in out
    assert "change_faction" in out and "L" in out      # 签名 + 行号
    assert "转宗" in out                                # 一行说明
    # 没给 topic 时不该硬塞代码，而是提示用 symbol 点名
    assert 'symbol="change_faction"' in out
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


# ---------------- ★ 细致问题回归：必须给函数体，不能只给签名 ----------------
def test_detailed_question_gets_real_body():
    """用户原话：「问细致点他就又不知道了」。

    实测缺陷（重构前）：`read_module('factions', '宗门的权重是怎么算的')` 输出 4549
    字符里 ``acc +=`` 出现 **0** 次、``pool_map`` **0** 次 —— 18 行的 ``random_faction``
    一行都没给，模型只能编。本用例把「必须给到实现」钉死。
    """
    ce = _load_ce("ce_detail")
    ex = ce.CodeExplainer()                       # 真实仓库
    out = ex.explain(topic="宗门的权重是怎么算的", file="factions")
    assert "random_faction" in out
    for needle in ("pool_map", "weights = [max(0, int(", "pick = r() * total",
                   "acc += w", "return ids[-1]"):
        assert needle in out, "细致问题必须拿到函数体，缺 %r" % needle


def test_symbol_mode_returns_complete_body():
    """``symbol=`` 必须给**完整**函数体（首行到末行，不省略）。"""
    ce = _load_ce("ce_symbol")
    ex = ce.CodeExplainer()
    out = ex.explain(symbol="random_faction")
    assert "【完整实现】" in out
    assert "def random_faction(" in out
    assert "return ids[-1]" in out               # 末行必须在
    assert "⋮" not in out                         # 18 行的函数不该折叠
    assert "L1340-1357" in out                    # 行号区间（真实仓库定位）


def test_line_number_lookup_returns_innermost_symbol(tmp_path):
    """``文件:行号`` → 最内层符号（code-index MCP 的 where_am_i 口径）。"""
    ce = _load_ce()
    ex = ce.CodeExplainer(root=_make_tree(tmp_path))
    out = ex.explain(symbol="core.py:2")
    assert "tower_elevator" in out
    assert "【完整实现】" in out and "return floor - 1" in out
    # 不在符号内的行不能死胡同：要给附近代码 + 概览，而不是一句「没找到」
    out2 = ex.explain(symbol="core.py:1")
    assert "不在任何符号范围内" in out2
    assert "【附近代码】" in out2 and "【该模块符号概览】" in out2


def test_module_level_data_constants_are_indexed():
    """模块级**数据常量**必须能按名取到 —— 斗破是数据密集仓库，答案常在常量里。

    2026-11-17 真机暴露：模型问「各宗门权重是多少」时 ``symbol=POOL_UNLOCK``
    直接「没找到符号」（它只索引 def/class），而 ``POOL_UNLOCK`` 正是 1/40/90
    的池子门槛、``FACTIONS`` 里才写着每条 ``weight``。
    """
    ce = _load_ce("ce_data")
    ex = ce.CodeExplainer()

    gate = ex.explain(symbol="POOL_UNLOCK")
    assert "starter" in gate and "advanced" in gate and "hidden" in gate
    assert "40" in gate and "90" in gate

    big = ex.explain(symbol="FACTIONS")
    assert "共 1209 行" in big or "FACTIONS" in big
    assert "weight" in big, "数据常量要给出真实条目（含 weight 数值）"
    assert len(big) <= _HARD_CAP

    # 概览里必须能看见这些常量（否则模型不知道有它们可问）
    ov = ex.explain(file="factions")
    for name in ("POOL_UNLOCK", "SPECIAL_UNLOCK_STEPS", "FACTIONS"):
        assert name in ov, name


def test_pure_data_module_is_not_useless():
    """纯数据模块（没有 def/class）必须仍能被讲清楚，不能说「没有 def/class」。"""
    ce = _load_ce("ce_pure")
    ex = ce.CodeExplainer()
    out = ex.explain(file="tower_data")
    assert "【符号概览】" in out
    assert "没有 def/class" not in out
    assert "HARD_BASE" in out          # 常量名要出现


def test_nested_and_decorated_symbol(tmp_path):
    """嵌套类方法 + 装饰器 + 多行签名：签名必须完整，不能断在半截。"""
    ce = _load_ce()
    root = _make_tree(tmp_path)
    (root / "src/plugins/doupo/nested.py").write_text(
        "def deco(f):\n    return f\n\n\n"
        "class Outer:\n"
        '    """外层类。"""\n\n'
        "    @deco\n"
        "    def method(\n"
        "        self,\n"
        "        x: int = 1,\n"
        "    ) -> int:\n"
        '        """方法说明。"""\n'
        "        return x\n", encoding="utf-8")
    ex = ce.CodeExplainer(root=root)
    outline = ex.read_module("nested")
    assert "Outer" in outline and "method" in outline
    assert "x: int = 1" in outline, "多行签名的参数不能被丢掉"
    body = ex.explain(symbol="Outer.method")
    assert "@deco" in body and "-> int:" in body and "return x" in body
    # 行号定位要给出外层链
    where = ex.explain(symbol="nested.py:10")
    assert "Outer.method" in where and "外层作用域" in where


def test_broken_syntax_falls_back_without_raising(tmp_path):
    """语法错误文件必须静默回退（repomix 同款），不能抛错。"""
    ce = _load_ce()
    root = _make_tree(tmp_path)
    (root / "src/plugins/doupo/broken.py").write_text(
        "def ok_one(a):\n    return a\n\n\ndef broken(:\n  pass\n", encoding="utf-8")
    ex = ce.CodeExplainer(root=root)
    out = ex.read_module("broken")               # 不抛异常
    assert "ok_one" in out
    assert "语法不完整" in out                    # 明确告知是回退结果


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
        if "【斗破模块】" not in out and "【斗破文档】" not in out:
            bad.append((name, out[:60]))
    assert not bad, bad


def test_every_symbol_in_every_module_is_retrievable():
    """**遍历式**门禁：每个模块的每一个符号都必须能按名取到完整实现。

    这是"问细致必须答得出"的结构性保证 —— 不挑几个热门符号测，
    而是把 40 个模块的 559 个符号全跑一遍。
    """
    ce = _load_ce("ce_syms")
    ex = ce.CodeExplainer()
    bad = []
    total = 0
    for p in ex._iter_allowed():
        if p.suffix != ".py":
            continue
        for s in ex._index(p).syms:
            total += 1
            out = ex.where(s.name, p.stem)
            if "【完整实现】" not in out or s.name not in out:
                bad.append("%s.%s" % (p.stem, s.name))
    assert total >= 500, "真实仓库符号数异常：%d" % total
    assert not bad, bad[:20]


def test_every_symbol_name_appears_in_module_outline():
    """大模块（如 core.py 上百个符号）的概览必须让模型看得到**每一个名字**。

    否则模型连点名都点不出来 —— 又会退回"问细致只能猜"。
    """
    ce = _load_ce("ce_outline")
    ex = ce.CodeExplainer()
    bad = {}
    for p in ex._iter_allowed():
        if p.suffix != ".py":
            continue
        out = ex.explain(file=p.stem)
        outline = out.split("【符号概览】")[1].split("【")[0] if "【符号概览】" in out else ""
        miss = [s.name for s in ex._index(p).syms
                if not re.search(r"\b%s\b" % re.escape(s.name), outline)]
        if miss:
            bad[p.stem] = miss[:6]
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


# ---------------- 输出有界（不把半截代码留给上层裸截）----------------
def test_all_outputs_bounded():
    """任何调用都不得超过 _HARD_CAP —— 超过就会被 registry 裸截在函数中间。"""
    ce = _load_ce("ce_bound")
    ex = ce.CodeExplainer()
    topic = "宗门 斗技 异火 爬塔 丹药 武器 心法 属性 战斗 世界 等级 经验 金币"
    over = []
    for p in ex._iter_allowed():
        for label, out in (("notopic", ex.read_module(p.stem)),
                           ("topic", ex.read_module(p.stem, topic))):
            if len(out) > _HARD_CAP:
                over.append((p.stem, label, len(out)))
        if p.suffix == ".py":
            for s in ex._index(p).syms[:20]:
                out = ex.where(s.name, p.stem)
                if len(out) > _HARD_CAP:
                    over.append((p.stem, s.name, len(out)))
    assert not over, over[:10]


# ---------------- 工具注册口径 ----------------
def test_tool_registered_safe_with_three_params_and_no_readonly_wording():
    registry_mod, builtins_mod = _load_tool_stack("_tstack_reg")
    reg = registry_mod.ToolRegistry()

    async def cb(topic, file, symbol, ctx):
        return "stub"

    names = builtins_mod.register_builtin_tools(reg, {"doupo_code_explain": cb})
    assert "doupo_code_explain" in names
    t = reg.get("doupo_code_explain")
    assert t.risk == registry_mod.RISK_SAFE          # 群聊可用
    assert {"topic", "file", "symbol"} <= set(t.parameters["properties"])
    assert t.parameters["required"] == []   # 都可选，按组合决定返回什么
    for w in ("只读", "不能改", "readonly", "不可编辑"):
        assert w not in t.description
    # 描述里必须把「用 symbol 拿完整实现」教给模型，否则它只会看签名就下结论
    assert "symbol" in t.description
    assert "完整实现" in t.description


def test_builtin_callback_passes_symbol_through():
    """builtins 的 args→callback 转发必须带上 symbol（漏了就等于没这功能）。"""
    import asyncio

    registry_mod, builtins_mod = _load_tool_stack("_tstack_fwd")
    reg = registry_mod.ToolRegistry()
    got = {}

    async def cb(topic, file, symbol, ctx):
        got.update(topic=topic, file=file, symbol=symbol)
        return "stub"

    builtins_mod.register_builtin_tools(reg, {"doupo_code_explain": cb})
    tool = reg.get("doupo_code_explain")
    asyncio.new_event_loop().run_until_complete(
        tool.handler({"topic": "宗门", "file": "factions",
                      "symbol": "random_faction"}, object()))
    assert got == {"topic": "宗门", "file": "factions", "symbol": "random_faction"}


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
