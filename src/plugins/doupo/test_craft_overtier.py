# -*- coding: utf-8 -*-
"""炼药「越档」提示口径验收（2026-11-25 玩家实测 BUG）。

## 用户原话

「还有我练药的结果播报里，我已经炼制成功了，怎么还会有 警告越-3挡 等等提示，
这是BUG吧，处理掉」

## 坑在哪

`core.craft_rate_for()` 返回的 `diff` 语义是 **丹药所需品阶 − 炼药师品阶**：

    diff > 0  → 越阶（四品炼五品，越 1 档勉强、越 2 档几无可能）→ 该警告；
    diff = 0  → 刚好够格；
    diff < 0  → **高品阶炼低阶的加成**（四品炼一品，每低 1 档更稳）→ 绝不能警告。

旧代码写的是 `if over:`（`over = int(res["diff"])`），负数在 Python 里为真，
于是四品炼药师炼一品丹、丹药成了，回执还跟着一句「⚠ 越-3档——几无可能」。

## 本文件守

  ① 成功回执**永不**出现越档字样（真越阶成功也不许泼冷水）；
  ② `diff < 0` 时，成败两条路径都印不出「越」；
  ③ 真越阶（diff > 0）**失败**时，警告仍在——修 BUG 不等于把有用的提示删了；
  ④ `txt.craft_over()` 本体对非正数返回空串（纵深防御，传错也印不出来）；
  ⑤ 源码级：`over` 必须过 `max(0, …)` 这道闸。

运行：python src/plugins/doupo/test_craft_overtier.py
"""
from __future__ import annotations

import asyncio
import os
import pathlib
import sys
import types

PLUGINS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO_ROOT = os.path.dirname(os.path.dirname(PLUGINS_DIR))
for _p in (REPO_ROOT, PLUGINS_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def _stub_nonebot():
    nb = types.ModuleType("nonebot")

    def on_regex(*a, **k):
        m = types.SimpleNamespace()
        m.handle = lambda *a, **k: (lambda f: f)
        m.finish = lambda *a, **k: None
        return m

    nb.on_regex = on_regex
    nb.get_driver = lambda: types.SimpleNamespace(on_startup=lambda f: f)
    sys.modules["nonebot"] = nb
    for name in ("nonebot.adapters", "nonebot.adapters.onebot",
                 "nonebot.adapters.onebot.v11"):
        sys.modules[name] = types.ModuleType(name)
    v11 = sys.modules["nonebot.adapters.onebot.v11"]
    v11.MessageEvent = object
    v11.MessageSegment = object
    v11.Message = object


_stub_nonebot()

from doupo import commands, core, config as cfg, doupo_text as txt  # noqa: E402

_PASSED = 0
_FAILED = 0


def check(name, cond, detail=""):
    global _PASSED, _FAILED
    if cond:
        _PASSED += 1
        print("PASS  %s" % name)
    else:
        _FAILED += 1
        print("FAIL  %s  ->  %s" % (name, detail))


class MemStore:
    """只做内存读写的存档替身。"""

    def __init__(self, p):
        self.p = p

    async def get_player(self, qq):
        return self.p

    async def put_player(self, p):
        self.p = p

    async def item_locks(self, qq):
        return set()


def _player(alch: int = 8):
    """造一个高品阶炼药师的档：alch=8 时炼一品丹必然 diff<0。"""
    p = core.create_player("overtier", "炼丹人", "g1")
    p["alch"] = alch
    p["flames"] = {"紫火": 1}
    return p


def _run_craft(res: dict, name: str = "") -> str:
    """把 `core.craft` 换成固定结果，跑真正的 `cmd_craft` 回执链路。

    必须走真 `cmd_craft`：这个 BUG 的现场就在它的组装行里，
    只测 `txt.craft_over()` 本体是抓不到的（变异反证已确认）。
    """
    p = _player()
    store = MemStore(p)
    hit = name or next(n for n in cfg.RECIPE_NAMES
                       if not cfg.is_danwen_name(n))
    orig = commands.core.craft
    commands.core.craft = lambda _p, _n: dict(res, name=hit)
    try:
        return asyncio.run(commands.cmd_craft(store, "u1", hit))
    finally:
        commands.core.craft = orig


# ---------------------------------------------------------------------------
# ① 成功回执永远不带越档
# ---------------------------------------------------------------------------
def test_success_never_prints_overtier():
    msg = _run_craft({"ok": True, "rate": 1.0, "diff": -3, "alch_up": 0})
    check("用户现场：四品炼一品成功 → 不印「越-3档」", "越" not in msg, msg)
    check("成功回执仍是「丹成」", "丹成" in msg, msg)

    msg = _run_craft({"ok": True, "rate": 1.0, "diff": 2, "alch_up": 0})
    check("真越阶但炼成了 → 也不许泼冷水", "越" not in msg and "几无可能" not in msg, msg)

    msg = _run_craft({"ok": True, "rate": 1.0, "diff": 1, "alch_up": 0})
    check("越1档炼成 → 同样不印警告", "越" not in msg, msg)


# ---------------------------------------------------------------------------
# ②③ 失败路径：负数静默，正数照旧
# ---------------------------------------------------------------------------
def test_fail_paths():
    msg = _run_craft({"ok": False, "reason": "fail", "rate": 0.9,
                      "diff": -3, "dross": 2})
    check("高品阶炼低阶失败 → 不印「越-3档」", "越" not in msg, msg)
    check("失败回执本身还在", "失败" in msg, msg)

    msg = _run_craft({"ok": False, "reason": "fail", "rate": 0.2,
                      "diff": 2, "dross": 1})
    check("真越2档失败 → 仍提示「越2档」", "越2档" in msg, msg)

    msg = _run_craft({"ok": False, "reason": "fail", "rate": 0.35,
                      "diff": 1, "dross": 1})
    check("真越1档失败 → 仍提示「越1档勉强」", "越1档" in msg, msg)


# ---------------------------------------------------------------------------
# ④ 纵深防御：craft_over 本体
# ---------------------------------------------------------------------------
def test_craft_over_guards_non_positive():
    bad = {d: txt.craft_over(d) for d in (0, -1, -3, -99)
           if txt.craft_over(d) != ""}
    check("craft_over 对 0/负数一律返回空串", not bad, bad)
    check("craft_over(1) 说「勉强」", "越1档" in txt.craft_over(1), txt.craft_over(1))
    check("craft_over(2) 说「几无可能」", "越2档" in txt.craft_over(2)
          and "几无可能" in txt.craft_over(2), txt.craft_over(2))
    check("craft_over 容错非数字", txt.craft_over("x") == "", txt.craft_over("x"))


# ---------------------------------------------------------------------------
# ⑤ 源码级：负数不许漏进提示
# ---------------------------------------------------------------------------
def test_source_has_positive_gate():
    src = pathlib.Path(__file__).with_name("commands.py").read_text(encoding="utf-8")
    check("源码：over 过了 max(0, …) 这道闸",
          "over = max(0, int(res.get(\"diff\") or 0))" in src,
          [ln.strip() for ln in src.splitlines() if "over = " in ln][:3])
    # over_hint 只允许出现两次：赋值 + 失败分支的 craft_fail 实参。
    # 第三次出现就意味着又被拼进成功回执了（这个 BUG 的原始写法）。
    n = src.count("over_hint")
    check("源码：over_hint 只在失败分支用（出现 %d 次）" % n, n == 2, n)


def _run():
    fns = [(k, v) for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    for name, fn in fns:
        fn()
    print("=" * 70)
    print("CRAFTOVER_TOTAL=%d PASSED=%d FAILED=%d"
          % (_PASSED + _FAILED, _PASSED, _FAILED))
    return 0 if _FAILED == 0 else 1


if __name__ == "__main__":
    sys.exit(_run())
