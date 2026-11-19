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
GC = os.path.join(ROOT, "src", "game_compat.py")

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
     '               ("verdict", "end", "kpis", "bars", "beats", "actions", "rounds"))',
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
     '        side = "mine" if a.get("side") == "我方" else "foe"',
     '        side = "foe" if a.get("side") == "我方" else "mine"'),
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
     '                            "comp": [], "placeholder": True}]\n',
     '            if False:\n'
     '                entries = []\n'),
    # ---- 2026-11-19 补料：终局真值 / 败因 / 每回合战损 / 伤害构成 ----
    # 终局退回读存档血量（战败已被重置成上限×0.7）= 用户质疑的那个假象
    ("终局退回读战后重置血量", CORE,
     '    hp_show = int(_hp_end) if _hp_end is not None else int(p.get("hp") or 0)',
     '    hp_show = int(p.get("hp") or 0)'),
    # 败因不判 downed → 被打到 0 以下也写成「落荒而逃」（少了被救的收场）
    ("败因不判 downed", CORE,
     '    elif downed:\n'
     '        end_reason, end_text = "rescued", "被小医仙所救"',
     '    elif False:\n'
     '        end_reason, end_text = "rescued", "被小医仙所救"'),
    # 战败气血退回归零（用户 2026-11-19 明确否掉的那个显示）
    ("战败气血归零", CORE,
     '    hp_end = max(1, int(hp)) if win else floor_hp',
     '    hp_end = max(0, int(hp))'),
    # 战败后又用「上限×0.7」回血 → 回看与人物卡两个数（用户报的正是这个）
    ("战败偷偷回血到 70%", CORE,
     '    p["hp"] = hp_end\n    p["mp"] = mp',
     '    p["hp"] = int(st["体力"] * 0.7)\n    p["mp"] = mp'),
    # 不回填致死那几条 log 的气血快照 → 逐招末行写 0、终局写 5%（又两处对不上）
    ("逐招末行余血不回填", CORE,
     '            e["hp_after"] = hp_end',
     '            e["hp_after"] = 0'),
    # 旧档（气血 0 + hp_zero）不做新口径换算 → 点开旧战报又看到「0 血」
    ("旧档 0 血不换算", BR,
     '    if reason == "rescued" and hp_val <= 0 and hp_max > 0:',
     '    if False:'),
    # 保底常量与 config 脱钩（镜像常量漂移）
    ("保底常量漂移", BR,
     'LEGACY_HP_FLOOR = 0.05',
     'LEGACY_HP_FLOOR = 0.06'),
    # 每回合不统计「我受」→ 抬头只剩敌受，看不出自己掉了多少
    ("每回合不算我受伤害", BR,
     '        if side == "foe":\n'
     '            b["hurt"] += _i(a.get("dmg"))',
     '        if side == "foe":\n'
     '            b["hurt"] += 0'),
    # 余血不钳 0 → 致死那击印出 -287
    ("余血不钳到 0（印负数）", BR,
     '            b["hp"] = max(0, _i(a.get("hp_after")))',
     '            b["hp"] = _i(a.get("hp_after"))'),
    # 伤害构成永远为空 → 「多少被稀释」又看不见了
    ("伤害构成永远为空", BR,
     '    if raw <= 0 or (cut <= 0 and absorbed <= 0):\n        return ""',
     '    if True:\n        return ""'),
    # 模板不画终局资源条（数据有了但用户看不到）
    ("模板不画终局条", TPL,
     '    if end_rows:\n'
     '        blocks.append(_cap("终局"))',
     '    if False:\n'
     '        blocks.append(_cap("终局"))'),
    # 终局条恒定满条 → 「气血归零」看起来还是满血
    ("终局条不按比例（恒定满条）", TPL,
     '            fw = int(track_w * ratio)',
     '            fw = track_w'),
    # 气泡抬头不写回合战损
    ("气泡抬头不写战损", TPL,
     '        if b["sum_text"]:',
     '        if False:'),
    # 纯文本兜底用败因顶替玩法（「遭遇」被吃掉，用户看不出打的是什么）
    ("文本兜底吃掉玩法", BR,
     '           f"{\'·\' + v[\'end_text\'] if v[\'end_text\'] else \'\'}"\n'
     '           f"｜{v[\'kind\']}｜{v[\'rounds\']}回合｜对手 {v[\'opponent\']}"]',
     '           f"｜{v[\'end_text\'] or v[\'kind\']}"\n'
     '           f"｜{v[\'rounds\']}回合｜对手 {v[\'opponent\']}"]'),
    # ---- 分页（2026-11-19 用户「超长就分页」）----
    # 完全不分页 → 26 回合又回到 4,964px 的长图
    ("超长战报不分页", TPL,
     '    if card_height(data, content_w) <= max_h:\n        return [data]',
     '    if True:\n        return [data]'),
    # 末页被丢掉 → 后面的回合凭空消失
    ("分页丢最后一页", TPL,
     '        out.append(page)\n    return out',
     '        if n < total:\n            out.append(page)\n    return out'),
    # 页码从 0 起 → 「第 0/3 页」/ 页数对不上
    ("页码写错（从 0 起）", TPL,
     '        page["page"] = {"index": n, "total": total,',
     '        page["page"] = {"index": n - 1, "total": total,'),
    # 续页也带结算区 → 每页都重复终局/输出/节点（越翻越长）
    ("续页重复结算区", TPL,
     '            for k in ("end", "kpis", "bars", "beats", "groups", "actions"):\n'
     '                page.pop(k, None)',
     '            for k in ("groups",):\n'
     '                page.pop(k, None)'),
    # 页数封顶时把尾巴全塞末页（实拍会变成 39,937px 的巨图）
    ("封顶塞末页（巨图回归）", TPL,
     '        per = (len(plan) + max_pages - 1) // max_pages\n'
     '        slices = [(i, min(i + per, len(plan))) for i in range(0, len(plan), per)]',
     '        slices = slices[:max_pages - 1] + [(slices[max_pages - 1][0], len(plan))]'),
    # NapCat 只发第一页（分页等于白做）
    ("NapCat 只发第一页", GC,
     '        await matcher.finish(*segs)',
     '        await matcher.finish(segs[0])'),
    # 官方退回单图发送（其余页静默丢失）
    ("官方只发第一张图", GC,
     'Outgoing(images=images, keyboard_cmd=kcmd)',
     'Outgoing(images=images[:1], keyboard_cmd=kcmd)'),
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
