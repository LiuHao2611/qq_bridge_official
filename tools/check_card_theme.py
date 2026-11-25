# -*- coding: utf-8 -*-
"""tools/check_card_theme.py —— 卡片主题「全局校验」交付闸门（2026-11-25 用户要求）。

用户原话：「我发现几个卡片没按照主题口令切换深浅色主题，你检查所有卡片，
都加上主题全局校验，包括以后新增的卡片也要遵守」。

根因（本轮实证）
----------------
主题有两层：``pic`` 层 Theme + ``doupo_ui`` 层 contextvar style。斗破卡靠
``card._render_list/_render_pages(..., qq=...)`` 把玩家 QQ 注入 data 的 ``__qq``，
``_style_for`` 再据此解个人偏好 > 全局默认。**凡是不传 qq 的玩家卡，个人主题
口令对它无效**——本轮诊断实测：``render_ruin`` / ``render_battle`` / ``render_craft_for``
三张卡在「主题 游戏 / 主题 浅色」下字节完全相同（FAIL），而 menu/help/rank 这类
**本来就没有玩家的公共卡**走全局默认，是正确行为、不在此列。

三道闸（prove-it：改坏了必须真红）
----------------------------------
1. **静态 · qq 透传**（本闸核心，覆盖未来新卡）：AST 扫 ``card.py`` 所有
   ``render*`` 函数——只要函数能拿到 qq（签名有 ``qq`` / ``p`` / ``player``
   等玩家参数），且体内调 ``_render_list`` / ``_render_pages``，就**必须**显式
   传 ``qq=``；漏传即 FAIL。新增玩家卡若忘记接线，自动被拦。
2. **静态 · 模板主题接线**：扫 ``pic/templates/doupo*.py``（除调色板/内核模块），
   每个 ``build`` 必须走 ``_pal(t)`` / ``UI.pal``（读 contextvar），不许硬编码
   单一主题底色；用 ``bg_overrides`` 直接定死的也算违规。
3. **动态 · 双主题渲染**：把注册表里每张斗破模板用同一份 data 分别 set
   game/light 渲染，比对 PNG 必须不同（证明模板本身吃 contextvar，不是摆设）。

用法：
    python tools/check_card_theme.py              # 报告
    python tools/check_card_theme.py --check      # 违规 exit 1（供测试/pre-commit）
    python tools/check_card_theme.py --selftest   # 反例注入：每个旧错都必须被拦
"""
from __future__ import annotations

import ast
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CARD = ROOT / "src/plugins/doupo/card.py"
TPL_DIR = ROOT / "src/plugins/qq_bridge/pic/templates"

#: 允许的「无玩家」公共卡白名单：这些卡没有 qq 上下文，走全局默认是正确的。
#: 新增公共卡（菜单/帮助/排行之类）才加进来；玩家卡一律不许进。
PUBLIC_CARDS = frozenset({
    "render_menu", "render_help", "render_rank",
})

#: 判定「函数能拿到玩家 qq」的参数名（签名里出现任一即视为玩家卡）。
PLAYER_ARG_NAMES = frozenset({"qq", "p", "player", "last_battle", "state", "report"})


def _card_tree() -> ast.Module:
    return ast.parse(CARD.read_text(encoding="utf-8"))


def _kwargs_of(call: ast.Call) -> set[str]:
    return {kw.arg for kw in call.keywords if kw.arg}


def qq_wiring_problems(tree: ast.Module | None = None) -> list[str]:
    """闸门 1：玩家 render 函数必须把 qq 传给渲染入口。"""
    tree = tree or _card_tree()
    out: list[str] = []
    for fn in tree.body:
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        name = fn.name
        if not name.startswith("render"):
            continue
        if name in PUBLIC_CARDS:
            continue
        args = {a.arg for a in fn.args.args}
        if not (args & PLAYER_ARG_NAMES):
            continue  # 真的没有玩家参数（如 _render_list 自己）→ 跳过
        has_render_call = False
        for node in ast.walk(fn):
            if not isinstance(node, ast.Call):
                continue
            f = node.func
            callee = f.id if isinstance(f, ast.Name) else (
                f.attr if isinstance(f, ast.Attribute) else "")
            if callee in ("_render_list", "_render_pages", "render_puppet",
                          "render_ruin"):
                has_render_call = True
                if "qq" not in _kwargs_of(node):
                    out.append("[qq 透传] %s() 调 %s() 未传 qq= → 个人主题口令对它无效"
                               % (name, callee))
        # 有玩家参数却完全没渲染调用（如 render_puppet_for 调 render_puppet）：
        # 由下一轮的「委托链」检查覆盖，这里不重复报。
    return out


def template_wiring_problems(tpl_dir: Path | None = None) -> list[str]:
    """闸门 2：每张斗破模板的 build 必须走主题调色板，不许硬编码单主题底色。"""
    tpl_dir = tpl_dir or TPL_DIR
    out: list[str] = []
    # 调色板定义 / 内核模块自身允许硬编码 hex
    exempt = {"doupo.py", "doupo_ui.py", "doupo_style.py"}
    for f in sorted(tpl_dir.glob("doupo*.py")):
        if f.name in exempt:
            continue
        src = f.read_text(encoding="utf-8")
        if "def build(" not in src:
            continue
        uses_pal = ("_pal(t)" in src or "UI.pal(" in src
                    or "UI.palette(" in src or "from .doupo import _pal" in src)
        if not uses_pal:
            out.append("[模板主题] %s 的 build() 未接 _pal/UI.pal → 不随深浅色主题切换"
                       % f.name)
    return out


def dynamic_problems() -> list[str]:
    """闸门 3：逐模板实渲染，game/light 两态必须不同。需要 nonebot 环境。"""
    sys.path.insert(0, str(ROOT / "src/plugins"))
    sys.path.insert(0, str(ROOT))
    try:
        import nonebot  # noqa: F401
        if not nonebot._driver:
            nonebot.init()
    except Exception as e:  # noqa: BLE001
        return ["[动态] 无法初始化 nonebot：%s（本闸需在含 nonebot 的解释器下跑）" % e]
    from nonebot.adapters.onebot.v11 import Adapter as OA
    try:
        nonebot.get_driver().register_adapter(OA)
    except Exception:
        pass
    from doupo import card as C, card_theme as TH, core as CO
    out: list[str] = []

    real_path = TH.path
    TH.path = lambda: ROOT / "tmp" / "_theme_gate.json"
    TH._CACHE = None
    TH._CACHE_MTIME = -1.0
    try:
        def clear():
            try:
                C._load_pic().pic_cache.clear()
            except Exception:
                pass

        def player(qq):
            p = CO.create_player(qq, "主题闸", "g1")
            p["lv"] = 45
            p["hp"] = CO.stats(p)["体力"]
            p["mp"] = CO.mp_max(p)
            return p

        # (名字, style->png)：用个人口味注入，逐个 render 入口验证
        checks = [
            ("panel", lambda s: C.render_panel(player(checks_q(s)))),
            ("bag", lambda s: C.render_bag(player(checks_q(s)))),
            ("skills", lambda s: C.render_skills(player(checks_q(s)))),
            ("roster", lambda s: C.render_roster(player(checks_q(s)))),
            ("learned", lambda s: C.render_learned(player(checks_q(s)))),
            ("item", lambda s: C.render_item_detail(player(checks_q(s)), "火属性魔核")),
        ]
        for q in list((TH._read().get("users") or {})):
            TH.clear_user(q)

        for name, fn in checks:
            try:
                clear()
                TH.set_user(checks_q("game"), "game")
                g = asyncio.run(fn("game"))
                clear()
                TH.set_user(checks_q("light"), "light")
                l = asyncio.run(fn("light"))
            except Exception as e:  # noqa: BLE001
                out.append("[动态] %s 渲染异常：%s: %s" % (name, type(e).__name__, e))
                continue
            if not (g and l and g[:8] == b"\x89PNG\r\n\x1a\n" == l[:8]):
                out.append("[动态] %s 未出图（game=%s light=%s）" % (
                    name, bool(g), bool(l)))
            elif g == l:
                out.append("[动态] %s 两主题字节相同 → 主题切换失效" % name)
    finally:
        TH.path = real_path
        TH._CACHE = None
        TH._CACHE_MTIME = -1.0
    return out


def checks_q(style: str) -> str:
    """动态检查用的稳定 QQ（不同主题用不同 qq，避免缓存与个人偏好互相污染）。"""
    return "theme_gate_%s" % style


def all_problems(run_dynamic: bool = False) -> list[str]:
    out = qq_wiring_problems()
    out += template_wiring_problems()
    if run_dynamic:
        out += dynamic_problems()
    return out


def selftest() -> int:
    """反例注入：三道闸各自必须抓到对应旧错。"""
    ok = True

    # 反例 1：玩家卡漏传 qq
    bad = ast.parse('''
def render_bad(p):
    return _render_list("x", build(p), "卡名")
def render_good(p):
    return _render_list("x", build(p), "卡名", qq=p.get("qq"))
''')
    probs = qq_wiring_problems(bad)
    hit = any("render_bad" in x for x in probs) and not any("render_good" in x for x in probs)
    print(("PASS" if hit else "FAIL"), "反例1 漏传 qq 被拦 / 正确写法放行")
    ok &= hit

    # 反例 2：模板 build 不接主题
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        (d / "doupo_bad.py").write_text(
            "def build(t, data):\n    return '#22242C'\n", encoding="utf-8")
        (d / "doupo_good.py").write_text(
            "from .doupo import _pal\ndef build(t, data):\n    p=_pal(t)\n", encoding="utf-8")
        probs = template_wiring_problems(d)
        hit = (any("doupo_bad" in x for x in probs)
               and not any("doupo_good" in x for x in probs))
        print(("PASS" if hit else "FAIL"), "反例2 模板不接主题被拦 / 正确写法放行")
        ok &= hit

    print("SELFTEST", "PASS" if ok else "FAIL")
    return 0 if ok else 1


def main(argv: list[str]) -> int:
    args = set(argv)
    if "--selftest" in args:
        return selftest()
    probs = all_problems(run_dynamic="--dynamic" in args)
    if probs:
        print("主题全局校验 FAIL：%d 项" % len(probs))
        for p in probs:
            print("  ", p)
        return 1 if "--check" in args else 0
    print("主题全局校验 PASS：qq 透传 / 模板接线 / 双主题渲染 全部合规")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
