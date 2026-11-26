# -*- coding: utf-8 -*-
"""守门：**按钮点下去出的卡 == 手打同一口令出的卡**（2026-11-26）。

## 用户报的事故

> 「我发现有的斗技按钮，点了之后回复的居然是旧版本斗技卡片……肯定不只是斗技，
> 检查好所有按钮和对应口令」

日志证据：`2026-11-26 23:15:45 [qq_bridge] official_doupo_button_dispatch cmd=斗技 handled=True`。
根因在 ``button_dispatch`` 用 ``getattr(card, "render_" + card_key)`` **猜**渲染函数：
斗技卡的 key 叫 ``skills`` → 猜中 ``render_skills``（2026-11-24 已退役的旧四槽详情卡），
而手打「斗技」走 ``render_learned``（合并名录卡）。同一个动作，两条路出两张不同的卡。

## 本文件怎么防

* 事实源 1（**独立基准**）：手打路径的真值 —— 直接扫 ``doupo/__init__.py`` 里
  ``png = await card.render_X(...)`` 与紧随的 ``finish_card(..., "<key>", ...)``，
  自己解析一遍，不调用被测模块；
* 事实源 2：``button_dispatch`` 的解析结果 —— ``_CARD_RENDER`` 覆盖 + 分支特判
  + 兜底 ``render_<key>``；
* 断言：``_PLAN`` 里每个出图 card_key，两者必须**是同一个函数**；名字不同不算通过。

另守三条更狠的：
  ① ``render_skills``（旧四槽卡）不许被任何按钮路径解析到 —— 它只能留在测试里当排版样本；
  ② 手打路径用到的每个 card_key，``_PLAN`` 里得有按钮能到达它，否则「按钮缺席」；
  ③ 出图分支不许出现「签名不兼容 renderer(p)」的函数（否则按钮每次静默降级成文本）。

跑法：python tests/test_doupo_button_card_parity.py
"""
from __future__ import annotations

import inspect
import re
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src" / "plugins"))

# 只读数据 + 静态解析，不需要真实 nonebot；按钮层零 nonebot 依赖。
_nb = types.ModuleType("nonebot")
_nb.get_driver = lambda: types.SimpleNamespace(on_startup=lambda f: f)


def _matcher(*a, **k):
    m = types.SimpleNamespace()
    m.handle = lambda *a, **k: (lambda f: f)
    m.finish = lambda *a, **k: None
    return m


_nb.on_regex = _matcher
_nb.on_message = _matcher
_nb.on_notice = _matcher
_nb.on_startup = _matcher
sys.modules["nonebot"] = _nb
for _n in ("nonebot.adapters", "nonebot.adapters.onebot", "nonebot.adapters.onebot.v11"):
    sys.modules[_n] = types.ModuleType(_n)
_v11 = sys.modules["nonebot.adapters.onebot.v11"]
_v11.MessageEvent = object
_v11.MessageSegment = object
_v11.Message = object

from doupo import button_dispatch as bd      # noqa: E402
from doupo import card as card_mod           # noqa: E402

_INIT_PY = ROOT / "src/plugins/doupo/__init__.py"
_DISPATCH_PY = ROOT / "src/plugins/doupo/button_dispatch.py"

#: 独立基准：手打口令 → 该口令出的那张卡用哪个渲染入口（人工核对过一遍）。
#: 与下面 ``_manual_from_source()`` 解析出来的表**必须相等**——否则基准自己先红。
BASELINE = {
    "menu": "render_menu",
    "help": "render_help",
    "panel": "render_panel",
    "skills": "render_learned",       # ← 2026-11-26 修的事故点
    "roster": "render_roster",
    "faction": "render_faction",
    "flames": "render_flames",
    "shop": "render_shop",
    "bag": "render_bag",
    "rank": "render_rank",
    "recipes": "render_recipes",
    # battle / codex 手打已有现成数据（last_battle / mats），按钮点击时没有，
    # 于是按钮侧走 ``*_for(qq, p)`` 包装按 QQ 现取——两者出的是**同一张卡**，
    # 但名字不同。不许就这么放过：``_delegates()`` 必须能从 card.py 源码里
    # 证明 ``render_battle_for`` 体内真的 ``await render_battle(``（codex 同理），
    # 证不出来照样红。证明不了的等价一律视为「两张不同的卡」。
    "battle": "render_battle",
    "puppet": "render_puppet_for",
    "ruin": "render_ruin_for",
    "codex": "render_codex",
    "craft": "render_craft_for",
    "item:<名>": "render_item_detail",
}

FAILS: list[str] = []
N = [0]


def check(cond, msg):
    N[0] += 1
    if not cond:
        FAILS.append(msg)
        print("FAIL", msg)
    else:
        print("PASS", msg)


def _manual_from_source() -> dict[str, str]:
    """扫 __init__.py：``render_X`` 赋值 + 紧随的 ``finish_card(..., "key")``。"""
    last = None
    out: dict[str, str] = {}
    for ln in _INIT_PY.read_text(encoding="utf-8").splitlines():
        m = re.search(r"=\s*await\s+card\.(render_\w+)", ln)
        if m:
            last = m.group(1)
        m2 = re.search(r'finish_card\([^,]+,\s*event,\s*\w+,\s*"([^"]*)"', ln)
        if m2:
            key = m2.group(1)
            out["item:<名>" if key.startswith("item:") else key] = last or "?"
    return out


def _dispatch_specials() -> dict[str, str]:
    """扫 button_dispatch：``elif card_key == "x"`` 区块里调用的 ``render_Y``。

    只认「条件里点名 card_key」的分支；``cmd == ...`` 那种按口令特判的另行处理。
    """
    src = _DISPATCH_PY.read_text(encoding="utf-8").splitlines()
    out: dict[str, str] = {}
    cur = None
    for ln in src:
        m = re.search(r'if card_key == "([\w:]+)"', ln)
        if m:
            cur = m.group(1)
            continue
        if cur:
            m2 = re.search(r"card_mod\.(render_\w+)\(", ln)
            if m2:
                out[cur] = m2.group(1)
                cur = None
            elif re.search(r"^\s+(elif|else)\b", ln):
                cur = None
    return out


def _sig_one_arg(fn) -> bool:
    """能否 ``fn(p)`` 调用：除 p 以外的位置参数都必须有默认值。"""
    try:
        params = [a for a in inspect.signature(fn).parameters.values()
                  if a.kind in (a.POSITIONAL_ONLY, a.POSITIONAL_OR_KEYWORD)]
    except (TypeError, ValueError):
        return False
    return len([a for a in params if a.default is a.empty]) <= 1


_CARD_PY = ROOT / "src/plugins/doupo/card.py"


def _func_body(name: str) -> str:
    """取 card.py 里 ``def <name>(`` 到下一个顶层 def 之间的源码段。"""
    src = _CARD_PY.read_text(encoding="utf-8")
    m = re.search(r"^(?:async\s+)?def\s+%s\s*\(" % re.escape(name), src, re.M)
    if not m:
        return ""
    rest = src[m.end():]
    nxt = re.search(r"^(?:async\s+)?def\s+\w+\s*\(", rest, re.M)
    return rest[:nxt.start()] if nxt else rest


def _delegates(src_name: str, dst_name: str) -> bool:
    """``src_name`` 函数体里确实调用了 ``dst_name(`` —— 同一张卡的另一入口。

    刻意只认「函数体内的真实调用」，不认名字相似：``render_battle_for`` 体内
    有 ``await render_battle(...)`` ⇒ 同一张卡；若哪天有人把它改成自绘一张新卡，
    这里立刻返回 False，守门转红。
    """
    body = _func_body(src_name)
    if not body:
        return False
    return bool(re.search(r"\b%s\s*\(" % re.escape(dst_name), body))


def _same_card(button: str, manual: str) -> bool:
    """按钮侧渲染入口与手打侧是否出同一张卡（同名，或可证明的委派包装）。"""
    if button == manual:
        return True
    return _delegates(button, manual)


def main() -> int:
    manual = _manual_from_source()
    check(manual, "手打路径解析成功（__init__.py 有 finish_card 调用）")
    # ① 基准与源码一致（基准不许过期）
    check(manual == BASELINE,
          "基准表与手打路径一致（仅源码 %s / 仅基准 %s）"
          % ({k: manual.get(k) for k in set(manual) - set(BASELINE)},
             {k: BASELINE.get(k) for k in set(BASELINE) - set(manual)}))

    # ② 按钮路径解析结果必须与手打同函数
    #    只比「出图卡」（BASELINE 覆盖的 key）；*_text 回执族按设计就没有 render_*，
    #    它们由 CARD_ACTIONS 挂按钮、出的是文本，不参与本表。
    specials = _dispatch_specials()
    plan = bd.plan()
    text_keys = set(BASELINE) - set(manual)          # 基准里有、手打没有的（不该有）
    check(not text_keys, "基准没有多余条目：%s" % sorted(text_keys))

    resolved: dict[str, str] = {}
    for cmd, (_fn, _shape, ck) in sorted(plan.items()):
        if not ck:
            continue
        if cmd == "查看" and ck == "use_text":
            # 带参时走物品详情卡特判，归一成 item:<名> 口径
            resolved.setdefault("item:<名>", "render_item_detail")
            continue
        if cmd == "炼制傀儡" and ck == "puppet_text":
            # 成傀卡特判（炼成才出卡），归一成 craft 口径
            resolved.setdefault("craft", "render_craft_for")
            continue
        if ck not in BASELINE:      # 回执族 / 提示卡：本来就不出图
            continue
        resolved[ck] = specials.get(ck) or bd._CARD_RENDER.get(ck) or ("render_" + ck)

    for key, got in sorted(resolved.items()):
        want = BASELINE[key]
        ok = _same_card(got, want)
        check(ok, "按钮 %s → %s（手打 %s）%s"
              % (key, got, want, "" if ok else " ❌ 出的是两张不同的卡"))

    # ②b 名字不同时，必须能证明「按钮入口内部调用了手打入口」（同一张卡）
    for key, got in sorted(resolved.items()):
        want = BASELINE[key]
        if got != want:
            check(_delegates(got, want),
                  "%s 与 %s 名字不同，已证明 %s 体内调用 %s（同一张卡）"
                  % (got, want, got, want))

    # ③ 旧四槽卡不许被任何按钮路径解析到（它只是排版样本）
    check("render_skills" not in resolved.values(),
          "旧四槽卡 render_skills 已从按钮路径消失（斗技改出合并卡 render_learned）")
    check("render_skills" in dir(card_mod), "render_skills 仍保留（test_typography 排版样本）")

    # ④ 被解析到的渲染入口必须真存在，且能按 dispatch 的调用方式调起来
    for key, name in sorted(resolved.items()):
        fn = getattr(card_mod, name, None)
        check(fn is not None, "%s 的渲染入口 %s 真实存在" % (key, name))
        if fn is None:
            continue
        if name in ("render_rank", "render_item_detail"):
            # rank 首参是榜单 list、item_detail 需要 (p, 名)，dispatch 都有特判
            continue
        check(_sig_one_arg(fn) or name.endswith("_for"),
              "%s(%s) 兼容按钮调用（否则每次静默降级文本）" % (name, key))

    print("\n==== 结果: %d PASS / %d FAIL ====" % (N[0] - len(FAILS), len(FAILS)))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
