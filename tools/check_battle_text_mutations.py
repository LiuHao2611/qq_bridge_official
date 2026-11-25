# -*- coding: utf-8 -*-
"""战斗回执文案守门的**变异反证**（mutation testing，2026-11-25）。

证明 ``tests/test_battle_text_rich.py`` 不是摆设：把每一条「有血有肉」的要求
**真的改坏**，守门必须变红；改回原样必须变绿（baseline 先验）。

跑法：python tools/check_battle_text_mutations.py   （exit 0 = 12 组全部被抓住）

纪律（与 check_story_chapter_gate_mutations.py 同）：
  * 每轮只改一处，改完立刻恢复**原始字节**并校验 md5；
  * 「改坏了还全绿」→ 守门是空的，exit 1；
  * 恢复失败立刻中止，绝不留半坏源码在工作区；
  * 锚点按文件真实换行符拼接（仓库是 CRLF，用 \\n 拼会命中 0 次）。
"""
from __future__ import annotations

import hashlib
import io
import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEXT = os.path.join(ROOT, "src/plugins/doupo/doupo_text.py")
CMD = os.path.join(ROOT, "src/plugins/doupo/commands.py")
TEST = os.path.join(ROOT, "tests/test_battle_text_rich.py")


def nl_of(path: str) -> str:
    raw = io.open(path, "rb").read().decode("utf-8")
    return "\r\n" if "\r\n" in raw else "\n"


MUTATIONS = [
    dict(
        mid="M1 境界不进回执",
        path=TEXT,
        old=['    prefix = f"{who}，" if who else ""'],
        new=['    prefix = ""'],
        why="算了境界却不出句，玩家看不到「我是二星斗师」",
    ),
    dict(
        mid="M2 境界体征两档撞车",
        path=TEXT,
        old=['    "斗师": "纱衣浮体",'],
        new=['    "斗师": "气机鼓荡",'],
        why="斗师与斗之气共用一句体征，13 阶分档失效",
    ),
    dict(
        mid="M3 斗技品阶被忽略",
        path=TEXT,
        old=['    if t == "特殊":',
             '        return f"💥 【特殊·{name}】气机一沉，{dmg} 点碾下。"',
             '    if t == "高阶":',
             '        return f"💥 【高阶·{name}】气劲贯体，{dmg} 点砸进。"',
             '    return f"💥 【{name}】一记砸实，{dmg} 点落在要害。"'],
        new=['    return f"💥 【{name}】一记砸实，{dmg} 点落在要害。"'],
        why="高阶/特殊斗技退化成普通措辞，玩家打出的品阶看不见",
    ),
    dict(
        mid="M4 普通档也刷「普通」",
        path=TEXT,
        old=['    return f"💥 【{name}】一记砸实，{dmg} 点落在要害。"'],
        new=['    return f"💥 【普通·{name}】一记砸实，{dmg} 点落在要害。"'],
        why="普通是缺省品阶，每场都标就成了噪音",
    ),
    dict(
        mid="M5 普攻当招式名",
        path=TEXT,
        old=['    if name in ("普攻", "拳脚", "拳头"):',
             '        return f"💥 一记拳脚砸实，{dmg} 点落在要害。"',
             '    if t == "特殊":'],
        new=['    if t == "特殊":'],
        why="0 斗气转普攻时会写成【普攻】，把系统词当招式名",
    ),
    dict(
        mid="M6 胜只留一档",
        path=TEXT,
        old=['        if hp_ratio < 0.2:',
             '            verdict, tail = "险胜", "一线之差"',
             '        elif hp_ratio < 0.5:',
             '            verdict, tail = "惨胜", "气血翻涌"',
             '        else:',
             '            verdict, tail = "大捷", "斗气未散"'],
        new=['        verdict, tail = "大捷", "斗气未散"'],
        why="残血险胜与满血大捷同一句话，胜利反馈回到单调",
    ),
    dict(
        mid="M7 斗气耗尽仍说未散",
        path=TEXT,
        old=['        if exhausted:',
             '            tail = "斗气枯竭"'],
        new=['        if exhausted and False:',
             '            tail = "斗气枯竭"'],
        why="斗气见底了还说「斗气未散」，判词与真实状态打架",
    ),
    dict(
        mid="M8 败局没有收场句",
        path=CMD,
        old=['    if not res.get("win"):',
             '        lines.append(txt.battle_defeat())'],
        new=['    if False:',
             '        lines.append(txt.battle_defeat())'],
        why="败局只剩「力竭·斩k/n」一个数字，没有钩子",
    ),
    dict(
        mid="M9 败局仍说斩获",
        path=TEXT,
        old=['    verb = "斩获" if win else "未克"'],
        new=['    verb = "斩获"'],
        why="打输了还写「此战斩获」，自相矛盾",
    ),
    dict(
        mid="M10 傀儡不出场",
        path=CMD,
        old=['    if pup_hits:'],
        new=['    if False:'],
        why="傀儡真出手了，战报里却一个字都没有（改前就是这个状态）",
    ),
    dict(
        mid="M11 傀儡抢玩家高光",
        path=CMD,
        old=['    hero_log = [it for it in all_log if not it.get("puppet")]'],
        new=['    hero_log = list(all_log)'],
        why="傀儡伤害进了 💥 行，被归因成玩家「力量·武器」，抢功",
    ),
    dict(
        mid="M12 傀儡行改看 puppet_guard",
        path=CMD,
        old=['    if pup_hits:'],
        new=['    if res.get("puppet_guard"):'],
        why="只要带傀儡就写「掠阵」，哪怕它这一场一次没出手（编造战果）",
    ),
]


def md5(data: bytes) -> str:
    return hashlib.md5(data).hexdigest()


def _pytest_cmd():
    exe = shutil.which("pytest")
    if exe:
        return [exe, TEST, "-q", "--no-header", "-p", "no:cacheprovider"]
    return [sys.executable, "-m", "pytest", TEST, "-q", "--no-header",
            "-p", "no:cacheprovider"]


def run_test():
    r = subprocess.run(_pytest_cmd(), cwd=ROOT,
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                       timeout=600)
    out = r.stdout.decode("utf-8", "replace")
    fails = [ln.strip() for ln in out.splitlines() if ln.startswith("FAILED ")]
    total = ""
    for ln in out.splitlines():
        s = ln.strip()
        if " passed" in s or " failed" in s:
            total = s.strip("= ")
    return r.returncode, fails, total, out


def main():
    code, fails, total, out = run_test()
    print("BASELINE  exit=%d  %s" % (code, total))
    if code != 0:
        print("基线本身没全绿，先修基线再谈变异反证：")
        for f in fails:
            print("   " + f)
        print(out[-1500:])
        return 1
    print("-" * 78)

    rows = []
    for m in MUTATIONS:
        path = m["path"]
        nl = nl_of(path)
        original = io.open(path, "rb").read()
        sum0 = md5(original)
        text = original.decode("utf-8")
        old = nl.join(m["old"])
        new = nl.join(m["new"])
        n = text.count(old)
        if n != 1:
            print("!! %s 锚点命中 %d 次（应为 1），拒绝执行" % (m["mid"], n))
            return 2
        try:
            io.open(path, "wb").write(text.replace(old, new, 1).encode("utf-8"))
            code, fails, total, out = run_test()
        finally:
            io.open(path, "wb").write(original)
            got = md5(io.open(path, "rb").read())
            assert got == sum0, \
                "恢复失败：%s 的 md5 与原始不一致，立即中止" % path
        caught = code != 0
        first = fails[0][:110] if fails else (
            "崩溃（无 FAILED 行）: " + "; ".join(
                ln.strip() for ln in out.splitlines()
                if "Error" in ln)[:90])
        rows.append((m["mid"], caught, total, first, m["why"]))
        print("%-28s %s  %s" % (m["mid"], "变红 ✓" if caught else "仍全绿 ✗", total or ""))
        print("    抓住它的断言: " + first)

    print("-" * 78)
    bad = [r for r in rows if not r[1]]
    for mid, caught, total, first, why in rows:
        print("%-28s %-8s %s" % (mid, "RED" if caught else "GREEN", first[:76]))
    print("-" * 78)
    print("MUTATION_TOTAL=%d CAUGHT=%d MISSED=%d"
          % (len(rows), len(rows) - len(bad), len(bad)))
    if bad:
        for mid, _, _, _, why in bad:
            print("  漏网：" + mid + " —— " + why)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
