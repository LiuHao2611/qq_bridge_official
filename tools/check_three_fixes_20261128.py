#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""2026-11-28 三项交互修复的交付守门。

覆盖用户本轮三个问题：
  A. 突破战写入战斗回看——九星 BOSS 战胜利/失败后落 ``p["last_battle"]``，
     玩家发「战斗回看」能看到这一场（kind=突破 + 逐招 log + 对手=BOSS）。
  B. 查看火焰出图卡（不是纯文字）——火名不在物品表也要出 ``doupo_item`` 卡，
     火图放大 ``icon_hero=160``，主动/被动/获取三段齐，33 朵无遗漏。
  C. 异火去宗门传承门禁——任何宗门都出异火卡/列养成面板，不再因「本宗无传承」
     挡成一句纯文本，卡面/文本都不再印【本宗传承】块（误导玩家）。

用法：
    python tools/check_three_fixes_20261128.py            # 报告
    python tools/check_three_fixes_20261128.py --check    # 违规 exit 1
    python tools/check_three_fixes_20261128.py --selftest # 反例注入必须被拦
"""
from __future__ import annotations

import asyncio
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
from doupo import flame_view as fview  # noqa: E402

FAILS: list[str] = []
SRC = ROOT / "src" / "plugins" / "doupo"


def check(name: str, ok: bool, detail: str = "") -> None:
    print("  %s %s%s" % ("OK  " if ok else "FAIL", name, ("  ← " + detail) if detail and not ok else ""))
    if not ok:
        FAILS.append(name)


def group_a() -> None:
    print("[A] 突破战写入战斗回看")
    cmd_src = (SRC / "commands.py").read_text(encoding="utf-8")
    check("A1 cmd_break 调 record_last_battle 写突破",
          'record_last_battle(p, "突破"' in cmd_src)
    check("A2 突破回看对手取 boss_name",
          'opponent=br.get("boss_name")' in cmd_src)
    # 动态：九星突破胜利后 last_battle.kind=突破（与 test_commands 同口径）
    p = core.create_player("gate-break", "破客", 900000088)
    p["lv"] = 27
    p["exp"] = core.cfg.exp_need(27) + 10
    core._refill(p)
    br = core.nine_star_breakthrough(p, rng=lambda: 0.01)
    core.record_last_battle(p, "突破", br, opponent=br.get("boss_name") or "强敌")
    lb = p.get("last_battle") or {}
    check("A3 写入后 kind=突破", lb.get("kind") == "突破", str(lb.get("kind")))
    check("A4 有逐招 log", bool(lb.get("log")), str(len(lb.get("log") or [])))
    check("A5 win 标记正确", bool(lb.get("win")) is bool(br.get("win")))


def group_b() -> None:
    print("[B] 查看火焰出图卡（火图放大 icon_hero=160）")
    init_src = (SRC / "__init__.py").read_text(encoding="utf-8")
    card_src = (ROOT / "src" / "plugins" / "qq_bridge" / "pic" / "templates"
                / "doupo_item.py").read_text(encoding="utf-8")
    check("B1 view handler 识别火焰名", "all_flame_names()" in init_src)
    check("B2 view handler 调 render_flame_detail", "render_flame_detail" in init_src)
    check("B3 模板支持 icon_hero 覆盖", "data.get(\"icon_hero\")" in card_src)
    p = core.create_player("gate-flame", "看客", 900000089)
    names = fview.all_flame_names()
    check("B4 火焰全集 33 朵", len(names) == 33, str(len(names)))
    d = card.build_flame_detail_data(p, "陨落心炎")
    check("B5 详情 data 带图", bool(d) and d.get("image") == "陨落心炎")
    check("B6 大图 icon_hero=160", bool(d) and int(d.get("icon_hero") or 0) == 160,
          str(d and d.get("icon_hero")))
    gnames = [str(g.get("name")) for g in (d or {}).get("groups", [])]
    check("B7 三段齐", gnames == ["主动", "被动", "获取途径"], str(gnames))
    check("B8 非火名返回 None", card.build_flame_detail_data(p, "不存在的火") is None)
    png = asyncio.run(card.render_flame_detail(p, "陨落心炎"))
    check("B9 真出图（PNG 魔数）", bool(png) and png[:8] == b"\x89PNG\r\n\x1a\n")
    bad = []
    for n in names:
        one = asyncio.run(card.render_flame_detail(p, n))
        if not one or one[:8] != b"\x89PNG\r\n\x1a\n":
            bad.append(n)
    check("B10 33 朵全可出图", not bad, ",".join(bad[:5]))


def group_c() -> None:
    print("[C] 异火去宗门传承门禁")
    init_src = (SRC / "__init__.py").read_text(encoding="utf-8")
    cmd_src = (SRC / "commands.py").read_text(encoding="utf-8")
    card_src = (SRC / "card.py").read_text(encoding="utf-8")
    check("C1 handler 不再按 has_tradition_flame 挡回",
          "has_tradition_flame" not in init_src)
    check("C2 handler 一律 render_flames", "render_flames" in init_src)
    p = core.create_player("gate-none", "云", 900000090)
    p["faction"] = "yunlanzong"
    p["faction_name"] = "云岚宗"
    out = asyncio.run(commands.cmd_flames(_MemStore(p), "x"))
    check("C3 无传承宗门仍列养成面板", "须先收服才可喂养" in out, out[:160])
    check("C4 无传承不再纯文本提示", "本宗无传承异火" not in out, out[:160])
    check("C5 文本不印【本宗传承】块", "本宗传承" not in out, out[:160])
    d = card.build_flames_data(p)
    rows_txt = str(d.get("rows") or [])
    check("C6 卡片无【本宗传承】行", "本宗传承" not in rows_txt, rows_txt[:160])
    check("C7 cmd_flames 去传承早退", "if not has_tradition_flame(p):" not in cmd_src)
    check("C8 card 去 trad 区块", "fdef.get(\"flames\"" not in card_src)


class _MemStore:
    """最小 store：cmd_flames 只用 get_player。"""

    def __init__(self, p):
        self.p = p

    async def get_player(self, qq):
        return self.p

    async def put_player(self, p):
        self.p = p


def main() -> int:
    group_a()
    group_b()
    group_c()
    print("=" * 70)
    if FAILS:
        print("== FAIL %d 项：%s ==" % (len(FAILS), "、".join(FAILS)))
        return 1
    print("== PASS 全部通过 ==")
    return 0


if __name__ == "__main__":
    args = set(sys.argv[1:])
    if "--selftest" in args:
        # 反例注入：对「注入了旧代码的假源码」跑守门表达式，必须为 False（被拦）。
        cmd_src = (SRC / "commands.py").read_text(encoding="utf-8")
        init_src = (SRC / "__init__.py").read_text(encoding="utf-8")
        tpl_src = (ROOT / "src" / "plugins" / "qq_bridge" / "pic" / "templates"
                   / "doupo_item.py").read_text(encoding="utf-8")
        # 每项是 (名字, 真实源码为真, 注入旧代码后守门表达式为假)
        checks = [
            ("A 撤突破回看写入",
             'record_last_battle(p, "突破"' in cmd_src,
             'record_last_battle(p, "突破"' not in
             cmd_src.replace('record_last_battle(p, "突破"', '# removed')),
            ("B 撤 icon_hero 覆盖",
             'data.get("icon_hero")' in tpl_src,
             'data.get("icon_hero")' not in
             tpl_src.replace('data.get("icon_hero")', "ICON_HERO")),
            ("C 恢复有传承门禁",
             "has_tradition_flame" not in init_src,
             "has_tradition_flame" in
             init_src.replace("# 不再因本宗无传承而挡成一句纯文本。渲染失败才降级纯文本（与纯文本同源）。",
                              "if not commands.has_tradition_flame(p):\n        pass")),
        ]
        bad = 0
        for name, real_ok, injected_caught in checks:
            ok = real_ok and injected_caught
            print("%s  拦住 %s" % ("OK  " if ok else "FAIL", name))
            if not ok:
                bad += 1
        print("SELFTEST %d/%d 反例被拦" % (len(checks) - bad, len(checks)))
        sys.exit(0 if bad == 0 else 1)
    sys.exit(main())
