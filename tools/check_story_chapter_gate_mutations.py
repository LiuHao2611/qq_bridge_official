# -*- coding: utf-8 -*-
"""章节门禁守门测试的**变异反证**（mutation testing，2026-11-25）。

证明 `test_story_chapter_gate.py` 不是摆设：把每一条红线**真的改坏**，
守门必须变红；改回原样，守门必须变绿（baseline 已验）。

跑法：python tools/check_story_chapter_gate_mutations.py   （exit 0 = 11 组全部被抓住）

纪律：
  * 每轮只改一处，改完立刻恢复**原始字节**并校验 md5；
  * 任何一轮若「改坏了还全绿」→ 说明守门是空的，脚本 exit 1；
  * 恢复失败立刻中止（绝不留半坏的源码在工作区）。
  * 锚点按文件真实换行符拼接（仓库是 CRLF，用 `\n` 拼会命中 0 次）。
"""
from __future__ import annotations

import hashlib
import io
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CORE = os.path.join(ROOT, "src/plugins/doupo/core.py")
BTN = os.path.join(ROOT, "src/plugins/qq_bridge/channel/doupo_buttons.py")
TEST = os.path.join(ROOT, "src/plugins/doupo/test_story_chapter_gate.py")


def nl_of(path: str) -> str:
    raw = io.open(path, "rb").read().decode("utf-8")
    return "\r\n" if "\r\n" in raw else "\n"


MUTATIONS = [
    dict(
        mid="M1 章节闸改软锁",
        path=CORE,
        old=["        cand = list(chapter_pool(chapter))"],
        new=["        cand = list(cfg.MONSTERS)"],
        why="pick_encounter 不再受章节池约束，未开通章的怪照样抽得到",
    ),
    dict(
        mid="M2 章节闸误伤九星 BOSS",
        path=CORE,
        old=["    mon = monster_scaled(snap, species, puppet_bonus=_pu_bonus)"],
        new=["    mon = monster_scaled(snap, species, puppet_bonus=_pu_bonus,",
             "                             at_lv=cfg.chapter_base_lv(1))"],
        why="九星突破 BOSS 被钳到第 1 章基准 lv5（本该按玩家等级 lv9）",
    ),
    dict(
        mid="M3 按钮键与 battle 混用",
        path=BTN,
        old=['    "fight_text": ("战斗回看", "战斗评价", "任务"),'],
        new=['    "fight_text": ("战斗评价",),'],
        why="战斗纯文本回包丢「任务」「战斗回看」按钮，退化成回看卡",
    ),
    dict(
        mid="M4 story_chapter 用 or 兜底",
        path=CORE,
        old=['    v = p.get("story_chapter")',
             "    if v is None:",
             '        v = int(p.get("main_idx", 0) or 0) + 1',
             "        v = max(1, min(cfg.CHAPTER_COUNT, v))",
             '        p["story_chapter"] = v'],
        new=['    v = p.get("story_chapter") or 1',
             "    v = max(1, min(cfg.CHAPTER_COUNT, v))",
             '    p["story_chapter"] = v'],
        why="合法的 0 被吞成 1 并写回存档，且老档迁移分支变成死代码",
    ),
    dict(
        mid="M5 池空回落全表",
        path=CORE,
        old=["    if not out:", "        raise ValueError("],
        new=["    if not out:", "        return list(cfg.MONSTERS)",
             "        raise ValueError("],
        why="CHAPTER_SPECIES 配错时静默降级，第 1 章能遇到魂灭生",
    ),
    dict(
        mid="M6 章节与任务分两笔写",
        path=CORE,
        old=['    p["story_chapter"] = max(1, min(cfg.CHAPTER_COUNT,'
             ' int(p["main_idx"]) + 1))'],
        new=['    p["story_chapter"] = max(1, min(cfg.CHAPTER_COUNT,'
             ' int(p["main_idx"])))'],
        why="story_chapter 与 main_idx 口径错位一章（同一笔写被拆开）",
    ),
    dict(
        mid="M7 monster_scaled 忽略 at_lv",
        path=CORE,
        old=["    if at_lv is not None:"],
        new=["    if False:"],
        why="怪又跟着玩家等级反解，G2「怪等级按章节走」整个失效",
    ),
    dict(
        mid="M8 经验预算忽略 base_lv",
        path=CORE,
        old=['    lv = int(p.get("lv", 1)) if base_lv is None else int(base_lv)'],
        new=['    lv = int(p.get("lv", 1))'],
        why="lv40 回第 1 章仍拿 lv40 的经验（刷低章暴利）",
    ),
    dict(
        mid="M9 怪等级不托底（只剩章节基准）",
        path=CORE,
        old=['    return min(int(p.get("lv", 1)), cfg.chapter_base_lv(ch))'],
        new=["    return cfg.chapter_base_lv(ch)"],
        why="lv1 新号在 ch1 直接撞 lv5 怪（实测胜率 15%，劝退新手）",
    ),
    dict(
        mid="M10 require_lv 拦截失效",
        path=CORE,
        old=['    if _need_lv and int(p.get("lv", 1)) < _need_lv:'],
        new=["    if False:"],
        why="lv1 交完第 1 章就能进第 2 章打 lv10 的怪（必死）",
    ),
    dict(
        mid="M11 encounter 不把章节传下去",
        path=CORE,
        old=["    specs = pick_encounter(p, roll_encounter_count(p, rng), rng,"
             " chapter=chapter)"],
        new=["    specs = pick_encounter(p, roll_encounter_count(p, rng), rng)"],
        why="抽怪函数改好了但 encounter 没接线（章节闸形同虚设）",
    ),
]


def md5(data: bytes) -> str:
    return hashlib.md5(data).hexdigest()


def run_test():
    r = subprocess.run([sys.executable, TEST], cwd=ROOT,
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                       timeout=600)
    out = r.stdout.decode("utf-8", "replace")
    fails = [ln.strip()[6:] for ln in out.splitlines() if ln.startswith("FAIL  ")]
    total = ""
    for ln in out.splitlines():
        if ln.startswith("GATE_TOTAL="):
            total = ln.strip()
    return r.returncode, fails, total, out


def main():
    code, fails, total, out = run_test()
    print("BASELINE  exit=%d  %s" % (code, total))
    if code != 0:
        print("基线本身没全绿，先修基线再谈变异反证：")
        for f in fails:
            print("   FAIL " + f)
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
            io.open(path, "wb").write(
                text.replace(old, new, 1).encode("utf-8"))
            code, fails, total, out = run_test()
        finally:
            io.open(path, "wb").write(original)
            assert md5(io.open(path, "rb").read()) == sum0, \
                "恢复失败：%s 的 md5 与原始不一致，立即中止" % path
        caught = code != 0
        if fails:
            first = fails[0][:96]
        else:
            first = "崩溃（无 FAIL 行）: " + "; ".join(
                ln.strip() for ln in out.splitlines()
                if "Error" in ln or ln.strip().startswith("File ")
            )[:90]
        rows.append((m["mid"], caught, total, first, m["why"]))
        print("%-30s %s  %s" % (m["mid"], "变红 ✓" if caught else "仍全绿 ✗",
                                total or "(crash)"))
        print("    抓住它的断言: " + first)

    print("-" * 78)
    bad = [r for r in rows if not r[1]]
    for mid, caught, total, first, why in rows:
        print("%-30s %-8s %s" % (mid, "RED" if caught else "GREEN", first[:72]))
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
