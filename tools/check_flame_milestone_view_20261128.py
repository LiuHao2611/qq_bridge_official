# -*- coding: utf-8 -*-
"""火焰里程碑 / 重复升级 / 火焰查看 交付守门（2026-11-28）。

用户三项要求：
  A 里程碑 = 所有持有火焰等级之和；达档给永久全属性加成，后期给多点。
     用户例子：1异火+3兽火各1级=4；任一升级=5；再收1个1级火=6。
  B 重复获得火焰→自动升一级；满级后不再掉落该火。
  C 火焰查看：主动+被动+获取途径，禁止遗漏（全集 33 朵）。

用法：python tools/check_flame_milestone_view_20261128.py [--check] [--selftest]
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
_nb.get_driver = lambda: types.SimpleNamespace(on_startup=lambda f: f)
_nb.get_bot = lambda: types.SimpleNamespace(send=lambda *a, **k: None)
_nb.on_regex = lambda *a, **k: types.SimpleNamespace(
    handle=lambda *a, **k: (lambda f: f), finish=lambda *a, **k: None)
sys.modules["nonebot"] = _nb
for _m in ("nonebot.adapters", "nonebot.adapters.onebot", "nonebot.adapters.onebot.v11"):
    sys.modules[_m] = types.ModuleType(_m)
for _a in ("Message", "MessageEvent", "MessageSegment"):
    setattr(sys.modules["nonebot.adapters.onebot.v11"], _a, object)

from doupo import commands as commands  # noqa: E402
from doupo import config as cfg  # noqa: E402
from doupo import core  # noqa: E402
from doupo import doupo_text as txt  # noqa: E402
from doupo import flame_drop as fd  # noqa: E402
from doupo import flame_milestone as ms  # noqa: E402
from doupo import flame_view as fv  # noqa: E402

FAILS: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print("  %s %s%s" % ("OK  " if ok else "FAIL", name, ("  ← " + detail) if detail and not ok else ""))
    if not ok:
        FAILS.append(name)


def sec_a() -> None:
    print("[A] 火焰等级里程碑 → 永久全属性")
    p = {"flames": {"青莲地心火": 1, "紫火": 1, "桃花火": 1, "海心焰": 1}}
    check("A1 用户例子：1异火+3兽火各1级=4", ms.milestone_level(p) == 4, str(ms.milestone_level(p)))
    p["flames"]["青莲地心火"] = 2
    check("A2 任一火焰升级=5", ms.milestone_level(p) == 5, str(ms.milestone_level(p)))
    p["flames"]["火云水炎"] = 1
    check("A3 再收1个1级火=6", ms.milestone_level(p) == 6, str(ms.milestone_level(p)))
    check("A4 帝炎成就位计1级", ms.milestone_level({"flames": {}, "diyan": True}) == 1)
    check("A5 空火=0档、0加成", ms.bonus_pct({"flames": {}}) == 0.0 and ms.bonus_pct({}) == 0.0)
    # 档位递增 + 后期给多点
    pcts = [v for _g, v in ms.MILESTONE_TIERS]
    check("A6 档位加成单调递增", all(pcts[i] < pcts[i + 1] for i in range(len(pcts) - 1)), str(pcts))
    check("A7 后期加成给足（顶档 ≥ +50%）", pcts[-1] >= 0.50, str(pcts[-1]))
    check("A8 取已达成最高档不累加", ms.tier_of(100)[1] == pcts[-1])
    # stats 接线
    p0 = core.create_player("ms-probe", "探针", "g1")
    base = core.stats(p0)["力量"]
    p0["flames"] = {"青莲地心火": 1, "紫火": 1, "桃花火": 1, "海心焰": 1}  # 里程碑4，无 atk
    check("A9 stats 中里程碑加成生效（里程碑4力量↑）", core.stats(p0)["力量"] > base)
    csrc = inspect.getsource(core.stats)
    check("A10 core.stats 调用 flame_milestone.bonus_pct", "_milestone.bonus_pct" in csrc)


def sec_b() -> None:
    print("[B] 重复火焰自动升级 + 满级不掉落")
    p = core.create_player("dup-probe", "探针", "g1")
    r1 = core.grant_flame(p, "紫火", kind="beast")
    check("B1 新得=1级", r1["duplicate"] is False and r1["leveled"] == 1)
    r2 = core.grant_flame(p, "紫火", kind="beast")
    check("B2 重复=自动升1级", r2["duplicate"] is True and r2["leveled"] == 2,
          str(r2))
    p["flames"]["紫火"] = cfg.FLAME_MAX_LV
    r3 = core.grant_flame(p, "紫火", kind="beast")
    check("B3 满级不再升、折算药渣兜底", r3.get("maxed") is True and "leveled" not in r3,
          str(r3))
    check("B4 掉落决策满级火不入池",
          "紫火" not in fd._exclude_maxed(fd.beast_pool_for_lv(1), {"flames": {"紫火": 4}}))
    # 三处入口接入
    check("B5 突破掉落传 p 并走 grant_flame",
          "grant_flame(p, name, kind=kind)" in inspect.getsource(core.roll_breakthrough_flame)
          and "p=p" in inspect.getsource(core.roll_breakthrough_flame))
    check("B6 野外掉火重复升级不折算药渣",
          "fl[flame] = cur + 1" in inspect.getsource(fd.wild_flame_drops))
    check("B7 兽火丹重复升级+满级拒绝",
          '"flame_max"' in inspect.getsource(fd.beast_pill_result)
          and '"leveled"' in inspect.getsource(fd.beast_pill_result))
    # 失败分支在 _use_one（cmd_use 只是 wrapper）
    check("B8 服丹满级不吞（_use_one 拦在扣物前）",
          "flame_max" in inspect.getsource(commands._use_one))
    check("B9 播报文案区分升级/药渣",
          "leveled" in inspect.getsource(txt.flame_drop) and "升至" in inspect.getsource(txt.flame_drop))


def sec_c() -> None:
    print("[C] 火焰查看（主动+被动+获取途径，禁止遗漏）")
    names = fv.all_flame_names()
    check("C1 全集 33 朵", len(names) == 33, str(len(names)))
    check("C2 每朵都有获取途径（无待补）", all("待补" not in fv.how_of(n) for n in names),
          str([n for n in names if "待补" in fv.how_of(n)]))
    check("C3 每朵查看文本含主动/被动/获取",
          all(("主动" in fv.view_text(n) and "被动" in fv.view_text(n)
               and "获取" in fv.view_text(n)) for n in names))
    check("C4 可养五火主动技来自权威表（非重写）",
          fv.view_text("帝炎").find("帝炎破世") >= 0
          and fv.view_text("净莲妖火").find("净化万物") >= 0)
    check("C5 玩家文案无内部实现注记", "已实现" not in fv.view_text("帝炎"))
    csrc = inspect.getsource(commands.cmd_view)
    check("C6 查看命令接入火焰查询", "_fview.all_flame_names" in csrc and "_fview.view_text" in csrc)


def selftest() -> None:
    print("[selftest] 反例注入（旧错必须被拦）")
    cases = []
    # A: 里程碑改成恒定档
    orig = ms.milestone_level
    ms.milestone_level = lambda p: 0
    try:
        cases.append(("A 里程碑恒0", not _probe(sec_a)))
    finally:
        ms.milestone_level = orig
    # B: 重复改回折算药渣
    src_grant = core.grant_flame
    core.grant_flame = lambda p, name, kind="": {"ok": True, "duplicate": True, "dross": 30}
    try:
        cases.append(("B 重复折算药渣", not _probe(sec_b)))
    finally:
        core.grant_flame = src_grant
    # C: 抽掉一朵火的获取途径
    backup = dict(fv.HOW)
    fv.HOW.pop("紫火", None)
    try:
        cases.append(("C 漏一朵火获取途径", not _probe(sec_c)))
    finally:
        fv.HOW.clear()
        fv.HOW.update(backup)
    for name, caught in cases:
        print("  %s %s" % ("OK  " if caught else "FAIL", "拦住 " + name))
        if not caught:
            FAILS.append("selftest:" + name)


def _probe(fn):
    global FAILS
    saved, buf = FAILS, []
    FAILS = buf
    import contextlib, io
    with contextlib.redirect_stdout(io.StringIO()):
        try:
            fn()
        except Exception:
            FAILS.append("exception")
    ok = not buf
    FAILS = saved
    return ok


if __name__ == "__main__":
    sec_a(); sec_b(); sec_c()
    if "--selftest" in sys.argv:
        selftest()
    print("\n== %s ==" % ("FAIL %d" % len(FAILS) if FAILS else "PASS 全部通过"))
    for f in FAILS:
        print("  -", f)
    if "--check" in sys.argv or "--selftest" in sys.argv:
        sys.exit(1 if FAILS else 0)
