# -*- coding: utf-8 -*-
"""变异验证：把「命中 ↔ 闪避」对冲的关键逻辑逐条改坏，确认门禁**真的会红**。

只信「测试原本就绿」= 假通过；必须证明这些断言抓得住回退。
跑法：python tools/mutate_hit_rate.py
"""
from __future__ import annotations

import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CORE = os.path.join(ROOT, "src", "plugins", "doupo", "core.py")
FX = os.path.join(ROOT, "src", "plugins", "doupo", "skill_effects.py")

MUTATIONS = [
    # 玩家防守侧：怪的命中不减 → 「闪避太超标」原地回归
    ("怪命中不削玩家闪避", CORE,
     '    dodge = max(0.0, min(95.0, dodge_rate(p) + float(tm.get("evade_add", 0))) - m_hit)',
     '    dodge = max(0.0, min(95.0, dodge_rate(p) + float(tm.get("evade_add", 0))))'),
    # 玩家进攻侧：命中不压怪闪避
    ("玩家命中不压怪闪避", CORE,
     '                   + fx.target_evade_delta(fx_target)\n'
     '                   - max(0.0, float(atk_hit)) / 100.0)',
     '                   + fx.target_evade_delta(fx_target))'),
    # 临时身法（技能给的）没被命中对冲 → 留一个白嫖洞
    ("临时身法不吃命中", FX,
     '    evade = (float(st.get("evade", 0)) + target_evade_delta(st)\n'
     '             - max(0.0, float(hit)))',
     '    evade = float(st.get("evade", 0)) + target_evade_delta(st)'),
    # 眼力写死 0 → 怪物又变回「永远打不中闪避流」
    ("怪物眼力写死 0", CORE,
     '    hit = max(0.0, level_dodge(lv) - float(cfg.MON_HIT_RESIDUAL))',
     '    hit = 0.0'),
    # 身法不随等级涨 → 面板退化成常量
    ("怪物身法不随等级涨", CORE,
     '        "dodge": round(min(cfg.MON_DODGE_CAP, (cfg.MON_DODGE_BASE\n'
     '                                              + cfg.MON_DODGE_PER_REALM * k) * mul), 2),',
     '        "dodge": round(float(cfg.MON_DODGE_BASE), 2),'),
    # 面板混进血量反解 → 破坏「难度只由等级/战力决定」的红线
    ("面板混进血量反解", CORE,
     '        "hp": max(1, m_hp), "atk": max(1, m_atk), "def": m_def,',
     '        "hp": max(1, int(m_hp * (1 + _panel["hit"] / 100.0))), '
     '"atk": max(1, m_atk), "def": m_def,'),
    # 手搓怪被强行套面板 → 既有逐招快照全线漂移
    ("手搓怪强行套面板", CORE,
     '    _panel = mon.get("panel") or {}',
     '    _panel = mon.get("panel") or monster_panel(int(mon.get("lv", 1) or 1), "", False)'),
    # 命中从敏捷来 → 两边同步成长，词条等于没加
    ("命中改由敏捷白送", CORE,
     '    base = cfg.HERO_HIT + cfg.HERO_HIT_PER_REALM * cfg.realm_index(lv)',
     '    base = (cfg.HERO_HIT + cfg.HERO_HIT_PER_REALM * cfg.realm_index(lv)\n'
     '            + stats(p)["敏捷"] // 400)'),
    # 凝识丹不发货 → 又一张「只加描述」的假丹
    ("凝识丹不发货", CORE,
     '    if eff == "hit":',
     '    if False:'),
]


def run_tests() -> tuple[int, str]:
    r = subprocess.run([sys.executable, os.path.join(ROOT, "tests", "test_doupo_hit_rate.py")],
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
    for name, path, old, new in MUTATIONS:
        with open(path, encoding="utf-8", newline="") as f:
            src = f.read()
        nl = "\r\n" if "\r\n" in src else "\n"
        old = old.replace("\n", nl)
        new = new.replace("\n", nl)
        if old not in src:
            print("[SKIP] %s :: 替换串没找到（源码已改？）" % name)
            continue
        try:
            with open(path, "w", encoding="utf-8", newline="") as f:
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
            with open(path, "w", encoding="utf-8", newline="") as f:
                f.write(src)
    print("\n变异 %d 组，抓到 %d 组" % (len(MUTATIONS), ok))
    return 0 if ok == len(MUTATIONS) else 1


if __name__ == "__main__":
    raise SystemExit(main())
