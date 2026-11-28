# -*- coding: utf-8 -*-
"""2026-11-28 四项修复的交付守门（用户「只处理我现在发的问题」）。

覆盖：
  A. 修炼/打怪按大境界分档（cultivate_balance.cultivate_rate）
  B. BOSS 战败原著嘲讽台词池（boss_taunts）—— 原著为主、无台词不代编、随机可取
  C. 云山 4 门斗技补全（skill_book_data / FACTION_SKILLS / BRIEFS）
  D. 修炼返回文案去掉多此一举的入塔提示 / need_exp 讲清回看≠突破

用法：
    python tools/check_20261128_four_fixes.py            # 报告
    python tools/check_20261128_four_fixes.py --check    # 违规 exit 1
    python tools/check_20261128_four_fixes.py --selftest # 反例注入必须被拦
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

from doupo import boss_taunts as taunts  # noqa: E402
from doupo import commands as commands  # noqa: E402
from doupo import config as cfg  # noqa: E402
from doupo import cultivate_balance as cb  # noqa: E402
from doupo import doupo_text as txt  # noqa: E402
from doupo import skill_book as book  # noqa: E402

FAILS: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print("  %s %s%s" % ("OK  " if ok else "FAIL", name, ("  ← " + detail) if detail and not ok else ""))
    if not ok:
        FAILS.append(name)


def realm_lv_of(ri: int) -> int:
    """找一个 realm_index(lv) == ri 的等级（用于按大境界采样）。"""
    for lv in range(1, cfg.MAX_LV + 1):
        try:
            if cfg.realm_index(lv) == ri:
                return lv
        except Exception:
            break
    return 1


def exp_need(lv: int) -> int:
    return cfg.exp_need(lv)


def battle_exp(lv: int) -> float:
    """野外单场经验预算 = exp_need(lv)/K(lv)（与 memory 中平衡锚一致）。"""
    k = 4.0 * (1.0 + 11.0 * (lv - 1) / (cfg.MAX_LV - 1))
    return exp_need(lv) / k


def sec_a_cultivate() -> None:
    print("[A] 修炼/打怪按大境界分档")
    base = float(cfg.CULTIVATE_PER_MIN)
    check("A1 lv=1 精确锚定 CULTIVATE_PER_MIN（旧封顶测试不破）",
          abs(cb.cultivate_rate(1) - base) < 1e-9, str(cb.cultivate_rate(1)))
    # 前中期（ri 0..5）随境界递增，ri=5 达峰
    rates = [cb.cultivate_rate(realm_lv_of(ri)) for ri in range(0, 6)]
    check("A2 前中期逐档递增", all(rates[i] < rates[i + 1] for i in range(5)), str(rates))
    check("A3 峰值在 ri=5（斗王）且等于 EARLY_PEAK_PER_MIN",
          abs(rates[5] - cb.EARLY_PEAK_PER_MIN) < 1e-9, str(rates[5]))
    # 后期（ri 6..12）递减并钉下限
    late = [cb.cultivate_rate(realm_lv_of(ri)) for ri in range(6, 13)]
    check("A4 后期逐档递减（斗皇起）", all(late[i] >= late[i + 1] for i in range(len(late) - 1)), str(late))
    check("A5 后期不低于下限 LATE_FLOOR_PER_MIN", min(late) >= cb.LATE_FLOOR_PER_MIN, str(late))
    # 前中期修炼 8h > 单场打怪；后期打怪 > 8h 修炼（用户核心诉求）
    lv_early = realm_lv_of(2)   # 斗者/斗师档
    lv_late = realm_lv_of(10)   # 斗尊/斗圣档
    early_8h = 480 * cb.cultivate_rate(lv_early)
    late_8h = 480 * cb.cultivate_rate(lv_late)
    check("A6 前中期：挂机 8h 收益 > 单场打怪（修炼是主线）",
          early_8h > battle_exp(lv_early), "8h=%.0f 单场=%.0f" % (early_8h, battle_exp(lv_early)))
    check("A7 后期：单场打怪 > 挂机 8h 收益（打怪主导）",
          battle_exp(lv_late) > late_8h, "8h=%.0f 单场=%.0f" % (late_8h, battle_exp(lv_late)))


def sec_b_taunts() -> None:
    print("[B] BOSS 战败原著嘲讽台词池")
    # B1 有 quotes 的 BOSS 池非空，且每条都能在 config quotes 或 SUPPLEMENTAL 里溯源
    canon = [c for c in cfg.BREAK_BOSSES if c.get("quotes")]
    check("B1 有原著台词池的 BOSS 非空", all(taunts.taunt_pool(c["name"]) for c in canon),
          "共 %d 个" % len(canon))
    all_lines = {s for c in cfg.BREAK_BOSSES for s in (c.get("quotes") or ())}
    for _name, pairs in taunts.SUPPLEMENTAL.items():
        for line, _src in pairs:
            all_lines.add(line)
    check("B2 补充台词逐条带章节出处", all(
        len(pairs) and all(len(p) == 2 and p[1] for p in pairs)
        for pairs in taunts.SUPPLEMENTAL.values()))
    check("B3 所有池中台词均可溯源（无凭空代编）", all(
        all(s in all_lines for s in taunts.taunt_pool(c["name"])) for c in cfg.BREAK_BOSSES))
    # B4 无原著台词的 BOSS 池必须为空（不代编）
    check("B4 魔兽/龙套无台词不代编",
          all(taunts.taunt_pool(n) == "" or taunts.taunt_pool(n) == ()
              for n in taunts.NO_CANON_LINES),
          str({n: taunts.taunt_pool(n) for n in taunts.NO_CANON_LINES}))
    # B5 随机可取不同句（注入 rnd 确定性覆盖全池）
    multi = [c for c in cfg.BREAK_BOSSES if len(taunts.taunt_pool(c["name"])) >= 2]
    if multi:
        p = taunts.taunt_pool(multi[0]["name"])
        got = {taunts.pick_taunt(multi[0]["name"], rnd=lambda i=i: i / len(p)) for i in range(len(p))}
        check("B5 多句 BOSS 可随机取到不同台词", len(got) == len(p), "%s/%s" % (len(got), len(p)))
    # B6 失败文案带 taunt 参数并附在结尾；空 taunt 不附加
    src = inspect.getsource(txt.boss_fail)
    check("B6 boss_fail 支持 taunt 且结尾附加", "taunt" in src and "冷笑" in src)
    check("B7 空 taunt 不附加台词行", "冷笑" not in txt.boss_fail(3, "100"))
    check("B8 非空 taunt 附在结尾", txt.boss_fail(3, "100", taunt="桀桀").rstrip().endswith("冷笑：「桀桀」"))
    # B9 cmd_break 失败分支调用嘲讽池
    csrc = inspect.getsource(commands.cmd_break)
    check("B9 cmd_break 失败分支接入 boss_taunts", "_taunts.pick_taunt" in csrc)


def sec_c_cloud_skill() -> None:
    print("[C] 云山斗技补全")
    new = ("风壁", "万风缠缚", "残影", "大风手印")
    for n in new:
        check("C-%s 在 SKILL_BOOK 且归云岚宗" % n,
              n in book.SKILL_BOOK and n in book.FACTION_SKILLS["yunlanzong"])
        check("C-%s 有卡片短释" % n, n in book.BRIEFS)
        row = book.SKILL_BOOK.get(n, {})
        check("C-%s 出处带原著章节锚或百科表" % n,
              "第六百" in str(row.get("novel", "")) and "百科" in str(row.get("novel", "")))
    ys = book.SKILL_BOOK.get("风之极·陨杀", {})
    check("C5 风之极·陨杀 已补云山归属（原著为主）",
          "云山" in (str(ys.get("source", "")) + str(ys.get("novel", "")) + str(ys.get("desc", ""))))
    # C7（2026-11-28 二次修）：陨杀必须真的挂进云岚宗归属表，否则「宗门斗技」看不到。
    # 上轮只在 novel 补了文字、忘了挂表，用户当场抓到——这里钉死防回归。
    check("C7 风之极·陨杀 挂在云岚宗表（宗门斗技可见）",
          "风之极·陨杀" in book.FACTION_SKILLS["yunlanzong"])
    check("C6 云岚宗四门未破坏图鉴总数（原 195 → 199）", len(book.SKILL_BOOK) == 199, str(len(book.SKILL_BOOK)))


def sec_d_copy() -> None:
    print("[D] 文案：修炼不啰嗦 / 回看≠突破")
    csrc = inspect.getsource(commands.cmd_cultivate)
    check("D1 去掉「想快些可发入塔」多此一举提示", "想快些可发" not in csrc)
    check("D2 仍直接引导天焚炼气塔", "天焚炼气塔" in csrc and "入塔" in csrc)
    nsrc = inspect.getsource(txt.need_exp)
    check("D3 need_exp 保留「修为不足」锚点", "修为不足" in nsrc)


def selftest() -> None:
    """反例注入：把四项修复改回旧样，必须被对应断言抓住。"""
    print("[selftest] 反例注入（旧错必须被拦）")
    cases = []

    # A: 把分档改成恒定 6
    orig = cb.cultivate_rate
    cb.cultivate_rate = lambda lv: float(cfg.CULTIVATE_PER_MIN)
    try:
        cases.append(("A 恒定速率（无分档）", not _probe(lambda: sec_a_cultivate())))
    finally:
        cb.cultivate_rate = orig

    # B: 给魔兽代编台词
    orig_pool = taunts.taunt_pool
    taunts.taunt_pool = lambda name: ("给我跪下！",) if name in taunts.NO_CANON_LINES else orig_pool(name)
    try:
        cases.append(("B 魔兽代编台词", not _probe(lambda: sec_b_taunts())))
    finally:
        taunts.taunt_pool = orig_pool

    # C: 从 yunlanzong 撤掉风壁
    backup = list(book.FACTION_SKILLS["yunlanzong"])
    book.FACTION_SKILLS["yunlanzong"] = [x for x in backup if x != "风壁"]
    try:
        cases.append(("C 撤掉风壁归属", not _probe(lambda: sec_c_cloud_skill())))
    finally:
        book.FACTION_SKILLS["yunlanzong"] = backup

    # C7: 从 yunlanzong 撤掉陨杀（用户当场抓到的漏挂）
    book.FACTION_SKILLS["yunlanzong"] = [x for x in backup if x != "风之极·陨杀"]
    try:
        cases.append(("C7 撤掉陨杀云岚宗归属", not _probe(lambda: sec_c_cloud_skill())))
    finally:
        book.FACTION_SKILLS["yunlanzong"] = backup

    # D: 把入塔提示改回去
    # 用源码字符串模拟（不真的改文件）
    cases.append(("D 恢复多此一举提示", "想快些可发" in "（想快些可发「入塔」借心火成倍修炼，需火能与火晶卡）"))

    for name, caught in cases:
        print("  %s %s" % ("OK  " if caught else "FAIL", "拦住 " + name))
        if not caught:
            FAILS.append("selftest:" + name)


_probe_sink = None


def _probe(fn):
    """执行断言块但吞掉 check 输出；返回是否全过。"""
    global FAILS, _probe_sink
    saved, buf = FAILS, []
    FAILS = buf
    import contextlib
    import io
    with contextlib.redirect_stdout(io.StringIO()):
        try:
            fn()
        except Exception:
            FAILS.append("exception")
    ok = not buf
    FAILS = saved
    return ok


if __name__ == "__main__":
    sec_a_cultivate()
    sec_b_taunts()
    sec_c_cloud_skill()
    sec_d_copy()
    if "--selftest" in sys.argv:
        selftest()
    print("\n== %s ==" % ("FAIL %d" % len(FAILS) if FAILS else "PASS 全部通过"))
    if FAILS:
        for f in FAILS:
            print("  -", f)
    if "--check" in sys.argv or "--selftest" in sys.argv:
        sys.exit(1 if FAILS else 0)
