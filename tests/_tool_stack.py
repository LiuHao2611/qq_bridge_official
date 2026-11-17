# -*- coding: utf-8 -*-
"""按文件加载 tools 栈（config/registry/scheduler/builtins），绕开包 ``__init__``。

直接 ``import plugins.qq_bridge.tools.builtins`` 会先执行 ``qq_bridge/__init__``，
连带 import psutil / httpx / rapidocr 等一堆重依赖 —— 只是验「工具怎么注册的」
不该为此付出装全套运行环境的代价。这里用影子包（只有 ``__path__``、不执行
``__init__``）把相对导入 ``..config`` / ``.registry`` 接上即可。

放在 ``_`` 开头文件里：pytest 只收 ``test_*.py``，不会被当成用例。
"""
from __future__ import annotations

import importlib.util
import sys as _sys
import types as _types
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BASE = REPO / "src" / "plugins" / "qq_bridge"


def load_tool_stack(prefix: str = "_tstack"):
    """返回 (registry 模块, builtins 模块)；每调用一次用新 prefix 隔离。"""
    for nm, path in ((prefix, BASE), (prefix + ".tools", BASE / "tools")):
        pkg = _types.ModuleType(nm)
        pkg.__path__ = [str(path)]
        _sys.modules[nm] = pkg

    def _load(rel: str, modname: str):
        spec = importlib.util.spec_from_file_location(modname, BASE / rel)
        m = importlib.util.module_from_spec(spec)
        _sys.modules[modname] = m
        spec.loader.exec_module(m)
        return m

    _load("config.py", prefix + ".config")
    registry = _load("tools/registry.py", prefix + ".tools.registry")
    _load("tools/scheduler.py", prefix + ".tools.scheduler")
    builtins = _load("tools/builtins.py", prefix + ".tools.builtins")
    return registry, builtins


def load_module(rel: str, name: str):
    """按文件路径加载 qq_bridge 下的单个模块（同样绕开包 __init__）。"""
    spec = importlib.util.spec_from_file_location(name, BASE / rel)
    m = importlib.util.module_from_spec(spec)
    _sys.modules[name] = m
    spec.loader.exec_module(m)
    return m
