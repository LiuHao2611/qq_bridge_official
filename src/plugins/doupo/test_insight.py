# -*- coding: utf-8 -*-
"""学斗技 / 入道1技 / 异火传承 验收（零依赖、exit-code 可验证）。

运行：
    python src/plugins/doupo/test_insight.py

覆盖（2026-11-16 用户三条需求）：
  1. 入道只领悟 1 门低阶（普通阶）斗技，且已上阵；同一 qq 稳定不重掷；
  2. 「学斗技」随机领悟：达解锁等级 + 战力门槛 + 金币足够才成功，失败各有明确 reason；
  3. 已领悟技才能升级/上阵；老存档（无 learned）视为全会，不削弱；
  4. 异火面板先列本宗传承（本宗无传承则明确提示），养成列表仍在。
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
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

from doupo import card, commands, core, factions as fac, skills as sk  # noqa: E402

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
    def __init__(self, p):
        self.p = p

    async def get_player(self, qq):
        return self.p

    async def put_player(self, p):
        self.p = p


# ---------------------------------------------------------------------------
# 1. 入道只有 1 门低阶技
# ---------------------------------------------------------------------------
def test_enter_one_low_skill():
    for qq in ("9001", "777", "778", "10001"):
        p = core.create_player(qq, "测试", "g1")
        learned = p.get("learned") or []
        check("入道领悟恰好一门(%s)" % qq, len(learned) == 1, f"learned={learned}")
        learned_tier = next((e["tier"] for e in sk.catalog(p["faction"])
                             if e["name"] == learned[0]), None)
        check("入门技是低阶普通(%s)" % qq, learned_tier == "普通",
              f"{learned[0]} tier={learned_tier}")
        check("入门技已上阵(%s)" % qq, (p.get("loadout") or [])[:1] == learned,
              f"loadout={p.get('loadout')}")
    a = core.create_player("stable-one", "甲", "g1")
    b = core.create_player("stable-one", "甲", "g1")
    check("入门技按 qq 稳定", a["learned"] == b["learned"],
          f"{a['learned']} vs {b['learned']}")


# ---------------------------------------------------------------------------
# 2. 学斗技门槛与随机领悟
# ---------------------------------------------------------------------------
def _rich_player():
    p = core.create_player("insight-rich", "富", "g1")
    p["lv"] = 50
    p["gold"] = 100000
    p["loadout"] = list(p["learned"])
    return p


def test_insight_gates_and_success():
    p = _rich_player()
    n0 = len(p["learned"])
    before = p["gold"]
    res = core.insight(p, rng=lambda: 0.0)
    check("领悟成功", res.get("ok") is True, str(res))
    check("领悟后 learned+1", len(p["learned"]) == n0 + 1, str(p["learned"]))
    check("领悟扣金币", p["gold"] == before - res["cost"], f"{before}->{p['gold']}")
    check("领悟的是未学新技", res["name"] not in p["learned"][:-1], str(res["name"]))

    # 金币不足
    poor = _rich_player()
    poor["gold"] = 0
    r = core.insight(poor, rng=lambda: 0.0)
    check("金币不足被挡", r.get("reason") == "gold", str(r))

    # 战力不足：高门槛（已领悟很多门）且给低等级
    high = _rich_player()
    high["learned"] = [e["name"] for e in sk.learnable(high)
                       if e.get("origin") != "异火"]
    high["lv"] = 1
    r = core.insight(high, rng=lambda: 0.0)
    check("战力不足被挡", r.get("reason") in ("power", "level", "all"), str(r))

    # 老档无 learned → legacy，不破坏
    legacy = core.create_player("legacy-x", "老", "g1")
    legacy.pop("learned", None)
    r = core.insight(legacy, rng=lambda: 0.0)
    check("老档领悟被挡(legacy)", r.get("reason") == "legacy", str(r))


def test_insight_random_candidate():
    seen = set()
    for i in range(12):
        p = _rich_player()
        r = core.insight(p, rng=lambda k=i: (k * 0.079) % 1)
        if r.get("ok"):
            seen.add(r["name"])
    check("领悟结果可随机(≥2种)", len(seen) >= 2, f"seen={seen}")


# ---------------------------------------------------------------------------
# 3. 已领悟才升/才上阵；老档兼容
# ---------------------------------------------------------------------------
def test_learned_gates_upgrade_and_loadout():
    p = core.create_player("gate-x", "关", "g1")
    unlearned = [e["name"] for e in sk.learnable(p)
                 if e["name"] not in p["learned"]][0]
    p["bag"] = {"焚血散": 10}
    r = core.skill_up(p, unlearned)
    check("未领悟不能升级", r.get("reason") == "noskill", str(r))
    r2 = core.skill_up(p, p["learned"][0])
    check("已领悟能升级", r2.get("ok") is True, str(r2))

    # 老档：全会可升级、四槽自动补满
    legacy = core.create_player("legacy-y", "老", "g1")
    legacy.pop("learned", None)
    legacy["loadout"] = []
    sk.ensure_loadout(legacy)
    check("老档四槽自动补满", 1 <= len(legacy["loadout"]) <= 4,
          str(legacy["loadout"]))


# ---------------------------------------------------------------------------
# 4. cmd_insight / 异火传承提示
# ---------------------------------------------------------------------------
def test_cmd_insight_text():
    p = _rich_player()
    out = asyncio.run(commands.cmd_insight(MemStore(p), "x"))
    check("学斗技命令回领悟文案", "领悟" in out and "已领悟" in out, out[:80])
    poor = _rich_player()
    poor["gold"] = 0
    out2 = asyncio.run(commands.cmd_insight(MemStore(poor), "x"))
    check("学斗技金币不足提示", "金币不足" in out2, out2[:60])


def test_flames_shows_faction_tradition():
    # 云岚宗：无传承异火 → 纯文本一句提示，无图无按钮、无养成列表
    # （2026-11-16 用户硬要求：没异火传承的宗门不要展示任何异火）
    p = core.create_player("flame-none", "云", "g1")
    p["faction"] = "yunlanzong"
    p["faction_name"] = fac.FACTIONS["yunlanzong"]["name"]
    check("无传承判定为 False", commands.has_tradition_flame(p) is False, str(p.get("faction")))
    out = asyncio.run(commands.cmd_flames(MemStore(p), "x"))
    check("本宗无传承异火提示", "本宗无传承异火" in out, out[:100])
    check("无传承不列养成列表（纯文本一句）", "可自行收服" not in out and "青莲地心火" not in out,
          out[:160])
    check("无传承提示不含按钮/图片引导", "上阵" not in out and "斗技详情" not in out, out[:160])

    # 萧家：有传承异火 → 列出来（完整养成面板）
    p2 = core.create_player("flame-has", "萧", "g1")
    p2["faction"] = "xiaojia"
    p2["faction_name"] = fac.FACTIONS["xiaojia"]["name"]
    check("有传承判定为 True", commands.has_tradition_flame(p2) is True, str(p2.get("faction")))
    out2 = asyncio.run(commands.cmd_flames(MemStore(p2), "x"))
    check("本宗有传承异火列出", "萧玄" in out2 and "本宗传承" in out2, out2[:120])
    check("有传承仍列养成列表", "可自行收服" in out2, out2[:120])


def test_flames_handler_no_card_without_tradition():
    """守门：异火 handler 必须先判 has_tradition_flame，无传承直接纯文本 finish，
    绝不走 render_flames/finish_card（2026-11-16「没传承不要展示任何异火/无图无按钮」）。

    用 AST 检查，防止有人把『无传承也出图』的旧逻辑接回来。
    """
    import ast as _ast
    from pathlib import Path as _Path

    init_py = _Path(__file__).with_name("__init__.py").read_text(encoding="utf-8")
    tree = _ast.parse(init_py)
    flames_fn = None
    # flames handler 是注册在 flames matcher 上的装饰器函数体：找含
    # has_tradition_flame 调用的那个 async 函数
    for node in _ast.walk(tree):
        if isinstance(node, _ast.AsyncFunctionDef):
            blob = _ast.unparse(node)
            if "cmd_flames" in blob and "get_player" in blob:
                flames_fn = node
                break
    check("找到异火 handler", flames_fn is not None, "AST 未定位 cmd_flames handler")
    if flames_fn is None:
        return
    blob = _ast.unparse(flames_fn)
    check("handler 调用 has_tradition_flame", "has_tradition_flame" in blob, blob[:200])
    # 关键：render_flames 必须在 has_tradition_flame 的『有传承』分支之后，
    # 即 handler 里存在 if not ...has_tradition_flame ... finish / return 的早退守卫。
    early_return = any(
        isinstance(n, _ast.If)
        and "has_tradition_flame" in _ast.unparse(n.test)
        and "return" in _ast.unparse(n)
        for n in _ast.walk(flames_fn)
    )
    check("无传承早退守卫（不渲染卡片）", early_return,
          "handler 缺『无传承→纯文本早退』守卫，可能又发异火图+按钮")


def _run():
    fns = [(k, v) for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    for name, fn in fns:
        fn()
    print("=" * 70)
    print("INSIGHT_TOTAL=%d PASSED=%d FAILED=%d"
          % (_PASSED + _FAILED, _PASSED, _FAILED))
    return 0 if _FAILED == 0 else 1


if __name__ == "__main__":
    sys.exit(_run())
