#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""2026-11-28 两项修复守门：修炼=入塔 + 异火卡显示里程碑进度。

覆盖用户本轮：
  A. 「修炼/静修/打坐」口令直接进天焚炼气塔（=入塔），不再走静修+一句入塔提示；
     按塔规矩来，没火能就提示，不回落静修；按钮「修炼」同样改道。
  B. 异火卡顶部显示火焰里程碑进度：当前等级总和、当前永久加成、下一档门槛与差距，
     只显示当前档+下一档（不铺全部 8 档）。数值口径走 flame_milestone 唯一真源。

用法：
    python tools/check_cultivate_tower_and_milestone_20261128.py
    python tools/check_cultivate_tower_and_milestone_20261128.py --selftest
"""
from __future__ import annotations

import inspect
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for _p in (str(ROOT), str(ROOT / "src" / "plugins"), str(ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

_nb = types.ModuleType("nonebot")
_nb.on_regex = lambda *a, **k: types.SimpleNamespace(
    handle=lambda *a, **k: (lambda f: f), finish=lambda *a, **k: None)
_nb.get_driver = lambda: types.SimpleNamespace(on_startup=lambda f: f)
sys.modules["nonebot"] = _nb
for _m in ("nonebot.adapters", "nonebot.adapters.onebot", "nonebot.adapters.onebot.v11"):
    sys.modules[_m] = types.ModuleType(_m)
for _a in ("Message", "MessageEvent", "MessageSegment"):
    setattr(sys.modules["nonebot.adapters.onebot.v11"], _a, object)

from doupo import card  # noqa: E402
from doupo import commands  # noqa: E402
from doupo import core  # noqa: E402
from doupo import flame_milestone as ms  # noqa: E402

FAILS: list[str] = []
SRC = ROOT / "src" / "plugins" / "doupo"


def check(name: str, ok: bool, detail: str = "") -> None:
    print("  %s %s%s" % ("OK  " if ok else "FAIL", name, ("  ← " + detail) if detail and not ok else ""))
    if not ok:
        FAILS.append(name)


def group_a() -> None:
    print("[A] 修炼=入塔（直接进天焚炼气塔，不再静修+提示）")
    init_src = (SRC / "__init__.py").read_text(encoding="utf-8")
    # 找 cult handler（含「修炼=入塔」注释的那个 async 函数）
    import ast as _ast
    tree = _ast.parse(init_src)
    cult_fn = None
    for node in _ast.walk(tree):
        if isinstance(node, _ast.AsyncFunctionDef):
            blob = _ast.unparse(node)
            if "cmd_furnace_enter" in blob:
                cult_fn = blob
                break
    check("A1 修炼 handler 调 cmd_furnace_enter", bool(cult_fn))
    check("A2 修炼 handler 不再调 cmd_cultivate",
          bool(cult_fn) and "cmd_cultivate" not in cult_fn, (cult_fn or "")[:200])
    # 按钮分发同样改道
    bd_src = (SRC / "button_dispatch.py").read_text(encoding="utf-8")
    check("A3 按钮「修炼」改道 cmd_furnace_enter",
          '"修炼": ("cmd_furnace_enter"' in bd_src)
    check("A4 按钮「修炼」不再指向 cmd_cultivate",
          '"修炼": ("cmd_cultivate"' not in bd_src)
    # 塔照规矩：cmd_furnace_enter 含 no_energy 分支（没火能提示，不回落静修）
    fsrc = inspect.getsource(commands.cmd_furnace_enter)
    check("A5 入塔保留没火能提示（no_energy）", "no_energy" in fsrc)
    check("A6 入塔不回落 cmd_cultivate", "cmd_cultivate" not in fsrc)


def group_b() -> None:
    print("[B] 异火卡显示里程碑进度（当前档+下一档）")
    p = core.create_player("gate-ms", "看客", 900000200)
    p["flames"] = {"紫火": 2, "青莲地心火": 1, "陨落心炎": 3, "骨灵冷火": 1}
    d = card.build_flames_data(p)
    rows = d.get("rows") or []
    ms_rows = [r for r in rows if isinstance(r, dict) and "里程碑" in str(r.get("title") or "")]
    check("B1 异火卡有里程碑行", bool(ms_rows), str([r.get("title") for r in rows[:2]]))
    if ms_rows:
        r = ms_rows[0]
        # 当前等级总和 = 2+1+3+1 = 7；当前档 6 → +7%；下一档 10 还差 3
        want_lv = ms.milestone_level(p)
        check("B2 显示等级总和", want_lv == 7 and str(want_lv) in str(r.get("right")),
              str(r.get("right")))
        check("B3 显示当前加成 %", "+7%" in str(r.get("right")), str(r.get("right")))
        check("B4 显示下一档与差距", "下一档" in str(r.get("sub")) and "3 级" in str(r.get("sub")),
              str(r.get("sub")))
        check("B5 不铺全部 8 档（只当前+下一档）",
              str(r.get("sub")).count("级 →") <= 2, str(r.get("sub")))
    # 加成口径与唯一真源一致
    check("B6 加成走 flame_milestone 真源",
          abs(ms.bonus_pct(p) - 0.07) < 1e-9, str(ms.bonus_pct(p)))
    # 真出图不崩
    import asyncio
    png = asyncio.run(card.render_flames(p))
    check("B7 异火卡真出图", bool(png) and png[:8] == b"\x89PNG\r\n\x1a\n")


def main() -> int:
    group_a()
    group_b()
    print("=" * 70)
    if FAILS:
        print("== FAIL %d 项：%s ==" % (len(FAILS), "、".join(FAILS)))
        return 1
    print("== PASS 全部通过 ==")
    return 0


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        init_src = (SRC / "__init__.py").read_text(encoding="utf-8")
        card_src = (SRC / "card.py").read_text(encoding="utf-8")
        checks = [
            ("A 修炼改回静修",
             "cmd_furnace_enter" in init_src,
             "cmd_furnace_enter" not in init_src.replace("cmd_furnace_enter", "cmd_cultivate")),
            ("B 撤掉里程碑行",
             "火焰里程碑" in card_src,
             "火焰里程碑" not in card_src.replace("火焰里程碑", "进度")),
        ]
        bad = 0
        for name, real, injected in checks:
            ok = real and injected
            print("%s  拦住 %s" % ("OK  " if ok else "FAIL", name))
            if not ok:
                bad += 1
        print("SELFTEST %d/%d 反例被拦" % (len(checks) - bad, len(checks)))
        sys.exit(0 if bad == 0 else 1)
    sys.exit(main())
