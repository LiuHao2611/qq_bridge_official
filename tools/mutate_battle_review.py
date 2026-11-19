# -*- coding: utf-8 -*-
"""变异验证：把战斗回看卡的关键逻辑逐条改坏，确认对应门禁**真的会红**。

只信「测试原本就绿」= 假通过；必须证明这些断言能抓住回退。
跑法：python tmp/mutate_battle_review.py
"""
from __future__ import annotations

import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BR = os.path.join(ROOT, "src", "plugins", "doupo", "battle_review.py")
CORE = os.path.join(ROOT, "src", "plugins", "doupo", "core.py")
TPL = os.path.join(ROOT, "src", "plugins", "qq_bridge", "pic", "templates", "doupo_battle.py")

MUTATIONS = [
    ("阵营判据退回只看 taken", BR,
     '    mine = sum(_i(it.get("dmg")) for it in logs if _actor(it) == "我方")\n'
     '    foe = sum(_i(it.get("dmg")) for it in logs if _actor(it) != "我方")',
     '    mine = sum(_i(it.get("dmg")) for it in logs if not it.get("taken"))\n'
     '    foe = sum(_i(it.get("dmg")) for it in logs if it.get("taken"))'),
    ("关键节点不认心火共鸣", BR,
     '    if it.get("fortune") or _s(it.get("kind")) == "心火共鸣":\n        return "心火"',
     '    if False:\n        return "心火"'),
    ("关键节点不认踉跄", BR,
     '    if it.get("mishap"):\n        return "踉跄"',
     '    if False:\n        return "踉跄"'),
    ("数值去掉千分位", BR,
     '    return f"{_i(v):,}"',
     '    return str(_i(v))'),
    ("零伤害事件写成 0", BR,
     '            value, tone = "—", "dim"',
     '            value, tone = "0", "dim"'),
    ("逐招退回一条两行", BR,
     '        rows.append(f"{v[\'idx\']:02d}｜回合{v[\'round\']} {v[\'side\']} {v[\'move\']}{tag} {v[\'value\']}")',
     '        rows.append(f"{v[\'idx\']:02d}｜回合{v[\'round\']} {v[\'side\']} {v[\'move\']}{tag} {v[\'value\']}")\n'
     '        rows.append("　" + v["tags"])'),
    ("条形长度不编码数值", BR,
     '"ratio": round(t["foe"] / top, 4), "share": _pct(t["foe"], total),',
     '"ratio": 1.0, "share": _pct(t["foe"], total),'),
    ("模板对新旧数据不分流", TPL,
     '    return any(data.get(k) for k in\n'
     '               ("verdict", "kpis", "bars", "beats", "actions", "rounds"))',
     '    return True'),
    ("不落盘 exhausted", CORE,
     '            "exhausted": bool(res.get("exhausted")),',
     '            "exhausted": False,'),
    ("不落盘 auto_pills", CORE,
     '            "auto_pills": {"hp": int((res.get("auto_pills") or {}).get("hp") or 0),\n'
     '                           "mp": int((res.get("auto_pills") or {}).get("mp") or 0)},',
     '            "auto_pills": {"hp": 0, "mp": 0},'),
    ("气泡丢掉 rounds 字段", BR,
     '        "rounds": round_bubbles(battle),\n',
     '        "rounds": [],\n'),
    ("气泡左右列错边", BR,
     '        b["mine" if a.get("side") == "我方" else "foe"].append(a)',
     '        b["mine" if a.get("side") != "我方" else "foe"].append(a)'),
    ("气泡两列并成一列（几何碰撞）", TPL,
     '    col_w = (content_w - 2 * BUBBLE_PAD - BUBBLE_COL_GAP) // 2',
     '    col_w = content_w - 2 * BUBBLE_PAD'),
    ("气泡数值不右锚定（对齐漂移）", TPL,
     '            right = cx + col_w\n',
     '            right = cx + col_w - 24 if ri % 2 else cx + col_w\n'),
    # 2026-11-19 空白回合修复：闪避/踉跄/被控归敌方
    ("闪避踉跄被控误归我方（空白回合回归）", BR,
     '    if it.get("miss") or it.get("mishap"):\n        return "敌方"',
     '    if False:\n        return "敌方"'),
    # 闪避文案退回「闪避」→ 敌方列读成「敌人闪避了」
    ("闪避文案不点方向", BR,
     '    if it.get("miss") and _s(it.get("kind")) == "闪避":\n        return "被我方闪避"',
     '    if it.get("miss") and _s(it.get("kind")) == "闪避":\n        return "闪避"'),
    # 空列占位被删 → 纯空白
    ("删掉空列占位", TPL,
     '            if not entries:\n'
     '                # 空列（敌人已死 / 本回合真没动作）给一个淡色占位，\n'
     '                # 否则气泡半边纯空白，玩家以为卡坏了（2026-11-19 实拍反馈）。\n'
     '                entries = [{"move": "无动作", "value": "", "tags": "",\n'
     '                            "tone": "dim", "h": LINE_H + BUBBLE_ROW_GAP,\n'
     '                            "placeholder": True}]\n',
     '            if False:\n'
     '                entries = []\n'),
]


def run_tests() -> tuple[int, str]:
    r = subprocess.run([sys.executable, os.path.join(ROOT, "tests", "test_battle_review.py")],
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
