# -*- coding: utf-8 -*-
"""丹雷机制结构门禁（2026-11-27）。

``--check``  锁源码契约：品阶下限/概率/封顶/地板同源/低阶零回归/接线/文案无章号。
``--selftest`` 变异反证：把关键规则故意改坏，契约判据必须报问题（≥6 类）。

判据尽量读源码常量与结构，不依赖导入 nonebot；所有反例在本地字符串上做。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOUPO = ROOT / "src/plugins/doupo"
DANLEI = (DOUPO / "danlei.py").read_text(encoding="utf-8")
CORE = (DOUPO / "core.py").read_text(encoding="utf-8")
CCRAFT = (DOUPO / "commands_craft.py").read_text(encoding="utf-8")
DCMD = (DOUPO / "danlei_cmd.py").read_text(encoding="utf-8")


def _grab(src: str, name: str) -> str:
    m = re.search(rf"^{name}\s*=\s*([^\n]+)", src, re.M)
    return m.group(1).strip() if m else ""


def check_problems(danlei=DANLEI, core=CORE, ccraft=CCRAFT, dcmd=DCMD) -> list[str]:
    """返回问题描述列表；空=通过。"""
    p: list[str] = []

    # 1. 品阶下限 = 6（七品）
    if _grab(danlei, "DANLEI_MIN_IDX") != "6":
        p.append("[品阶] DANLEI_MIN_IDX 必须=6（七品起）")

    # 2. 抵挡概率 = 0.50
    if _grab(danlei, "DANLEI_GUARD_CHANCE") != "0.50":
        p.append("[概率] DANLEI_GUARD_CHANCE 必须=0.50（用户拍板）")

    # 3. 批量封顶存在且被 cap_n 调用
    if "DANLEI_BATCH_CAP" not in danlei or "cap_n" not in danlei:
        p.append("[封顶] 批量封顶常量/cap_n 缺失")
    if "min(int(n), DANLEI_BATCH_CAP)" not in danlei.replace(" ", "") and \
       "min(int(n),DANLEI_BATCH_CAP)" not in danlei.replace(" ", ""):
        p.append("[封顶] cap_n 未真正用 DANLEI_BATCH_CAP 截断")

    # 4. 地板同源 cfg.LOSE_HP_FLOOR，禁另造 0.05
    if "cfg.LOSE_HP_FLOOR" not in danlei:
        p.append("[地板] hp_floor 未复用 cfg.LOSE_HP_FLOOR")
    # apply_hp 地板不得反向加血（必须先 min(before, floor)）
    if "max(min(before,hp_floor(hp_max))" not in danlei.replace(" ", ""):
        p.append("[地板] apply_hp 缺『不超过 before』守卫（残血会被加血）")

    # 5. 低阶零回归：plan 开头判 grade_triggers 并 return None
    if not re.search(r"if not grade_triggers\(grade\):\s*\n\s*return None", danlei):
        p.append("[零回归] plan 缺低阶提前 return None")

    # 6. core 两处接线 + has_puppet 参数
    if "def craft(p, name, rng=random.random, now_ts=None, has_puppet=False)" not in core:
        p.append("[core] craft 缺 has_puppet 参数")
    if "def craft_batch(p, name, n, rng=random.random, now_ts=None, has_puppet=False)" not in core:
        p.append("[core] craft_batch 缺 has_puppet 参数")
    if core.count("_danlei.settle(") < 2:
        p.append("[core] craft/craft_batch 未都接 _danlei.settle")

    # 7. 命令层接线 + 淬炼走 temper
    if "danlei_cmd.has_puppet(qq)" not in ccraft:
        p.append("[命令] 未读 has_puppet")
    if "finish(qq, res)" not in ccraft:
        p.append("[命令] 未接丹雷文案/落库")
    if "pc.temper(" not in dcmd:
        p.append("[落库] 淬炼未走 puppet_core.temper 同源")

    # 8. 玩家可见文案无章节号与工程口吻（line 函数体内）
    body = danlei.split("def line(", 1)[-1] if "def line(" in danlei else ""
    if "第" in body and "章" in body:
        p.append("[文案] line 疑似含章节号")
    for kw in ("语料", "游戏设定", "未上线", "无封印系统"):
        if kw in body:
            p.append(f"[文案] line 含工程口吻：{kw}")

    return p


def selftest() -> list[tuple[str, bool]]:
    """把关键规则改坏，check_problems 必须报问题。"""
    res: list[tuple[str, bool]] = []

    m = DANLEI.replace("DANLEI_MIN_IDX = 6", "DANLEI_MIN_IDX = 4")
    res.append(("品阶下限改成五品", bool(check_problems(danlei=m))))

    m = DANLEI.replace("DANLEI_GUARD_CHANCE = 0.50", "DANLEI_GUARD_CHANCE = 0.90")
    res.append(("抵挡概率改成0.90", bool(check_problems(danlei=m))))

    m = DANLEI.replace("min(int(n), DANLEI_BATCH_CAP)", "min(int(n), 99)")
    res.append(("封顶改成99", bool(check_problems(danlei=m))))

    m = DANLEI.replace("cfg.LOSE_HP_FLOOR", "0.05")
    res.append(("地板改回硬编码0.05", bool(check_problems(danlei=m))))

    m = DANLEI.replace("max(min(before, hp_floor(hp_max)), before - max(0, int(hurt_total)))",
                       "max(hp_floor(hp_max), before - max(0, int(hurt_total)))")
    res.append(("地板反向加血", bool(check_problems(danlei=m))))

    m = DANLEI.replace("if not grade_triggers(grade):\n        return None", "if False:\n        return None")
    res.append(("拆掉低阶零回归闸", bool(check_problems(danlei=m))))

    m = CORE.replace("_danlei.settle(p, rec.get(\"grade\", \"\"), int(stats(p)[\"体力\"]),\n                                  has_puppet, rng))",
                     "None)")
    res.append(("拆掉 core 丹雷接线", bool(check_problems(core=m))))

    m = DCMD.replace("pc.temper(new)", "pc.vein_layers(new)")
    res.append(("淬炼不走 temper", bool(check_problems(dcmd=m))))

    return res


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        cases = selftest()
        bad = [n for n, ok in cases if not ok]
        for n, ok in cases:
            print(("PASS " if ok else "FAIL ") + n)
        print(f"SELFTEST={len(cases)} CAUGHT={len(cases) - len(bad)} MISSED={len(bad)}")
        sys.exit(1 if bad else 0)
    probs = check_problems()
    if probs:
        for x in probs:
            print("FAIL", x)
        print(f"CHECK FAILED: {len(probs)} problems")
        sys.exit(1)
    print("CHECK OK: 丹雷契约全部通过")
