# -*- coding: utf-8 -*-
"""变异验证：把「引用卡片时口令优先于卡片名」的判据逐条改坏，确认门禁真的会红。

背景（2026-11-19 用户方案②）：引用宗门卡发「斗技 全部」失败，根因是
``斗技`` 既是口令首词又是卡片别名，旧判据「首词是卡片名就排除」把它挡掉。
修成口令优先后，必须证明：改回旧判据 / 去掉子命令保护，测试都会红。

跑法：python tools/mutate_custom_buttons.py
"""
from __future__ import annotations

import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CB = os.path.join(ROOT, "src", "plugins", "doupo", "custom_buttons.py")

MUTATIONS = [
    # 回退到旧判据：首词是卡片名一律排除 → 「斗技 全部」被挡，报不认识
    ("回退旧判据：卡片名一律排除",
     '    if len(parts) == 1 and resolve_card(first) is not None:\n'
     '        return False  # 单发卡片名 = 查看该卡\n'
     '    return first in real_commands()',
     '    if resolve_card(first) is not None:\n'
     '        return False\n'
     '    return first in real_commands()'),
    # 去掉「第二词 - 开头」保护 → 「宝库 -血灵草」被当口令新增，旧 bug 复现
    ("去掉第二词减号保护",
     '        if second.startswith("-") or second in _QUOTE_SUBCOMMAND_WORDS:\n'
     '            return False',
     '        if second in _QUOTE_SUBCOMMAND_WORDS:\n'
     '            return False'),
    # 去掉子命令词表 → 「宝库 清空」被当口令新增
    ("去掉子命令词表",
     '        if second.startswith("-") or second in _QUOTE_SUBCOMMAND_WORDS:\n'
     '            return False',
     '        if second.startswith("-"):\n'
     '            return False'),
    # 去掉单词卡片名查看保护 → 「宝库」单发被当成加「宝库」按钮
    ("去掉单词卡片名查看保护",
     '    if len(parts) == 1 and resolve_card(first) is not None:\n'
     '        return False  # 单发卡片名 = 查看该卡\n',
     '    if False:\n'
     '        return False\n'),
    # 口令基准换成「永远假」 → 所有快捷新增失效
    ("口令基准永远假",
     '    return first in real_commands()',
     '    return False'),
]


def run_tests() -> tuple[int, str]:
    r = subprocess.run([sys.executable, os.path.join(ROOT, "tests", "test_doupo_custom_buttons.py")],
                       cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
                       errors="replace")
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def main() -> int:
    base_code, base_out = run_tests()
    print("基线：exit=%d %s" % (base_code, base_out.strip().splitlines()[-1]))
    if base_code != 0:
        print("基线就是红的，先修基线")
        return 2
    ok = 0
    for name, old, new in MUTATIONS:
        with open(CB, encoding="utf-8", newline="") as f:
            src = f.read()
        nl = "\r\n" if "\r\n" in src else "\n"
        old, new = old.replace("\n", nl), new.replace("\n", nl)
        if old not in src:
            print("[SKIP] %s :: 替换串没找到（源码已改？）" % name)
            continue
        try:
            with open(CB, "w", encoding="utf-8", newline="") as f:
                f.write(src.replace(old, new, 1))
            code, out = run_tests()
            fails = [ln for ln in out.splitlines() if ln.startswith("FAIL")]
            if code != 0:
                ok += 1
                print("[RED ] %s → 抓到 %d 条失败，例：%s"
                      % (name, len(fails), (fails[0][:90] if fails else out.strip().splitlines()[-1])))
            else:
                print("[MISS] %s → 测试仍然全绿，这条门禁是假的！" % name)
        finally:
            with open(CB, "w", encoding="utf-8", newline="") as f:
                f.write(src)
    print("\n变异 %d 组，抓到 %d 组" % (len(MUTATIONS), ok))
    return 0 if ok == len(MUTATIONS) else 1


if __name__ == "__main__":
    raise SystemExit(main())
