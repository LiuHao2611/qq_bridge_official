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
FLAVOR_TEST = os.path.join(ROOT, "tests/test_doupo_battle_flavor.py")
VERDICT = os.path.join(ROOT, "src/plugins/doupo/battle_verdict.py")


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
        old=['        elif t == "特殊":',
             '            body = f"💥 【特殊·{name}】气机一沉，{dmg} 点碾下。"',
             '        elif t == "高阶":',
             '            body = f"💥 【高阶·{name}】气劲贯体，{dmg} 点砸进。"',
             '        else:',
             '            body = f"💥 【{name}】一记砸实，{dmg} 点落在要害。"'],
        new=['        else:',
             '            body = f"💥 【{name}】一记砸实，{dmg} 点落在要害。"'],
        why="高阶/特殊斗技退化成普通措辞，玩家打出的品阶看不见",
    ),
    dict(
        mid="M4 普通档也刷「普通」",
        path=TEXT,
        old=['            body = f"💥 【{name}】一记砸实，{dmg} 点落在要害。"'],
        new=['            body = f"💥 【普通·{name}】一记砸实，{dmg} 点落在要害。"'],
        why="普通是缺省品阶，每场都标就成了噪音",
    ),
    dict(
        mid="M5 普攻当招式名",
        path=TEXT,
        old=['        if name in ("普攻", "拳脚", "拳头"):',
             '            body = f"💥 一记拳脚砸实，{dmg} 点落在要害。"',
             '        elif t == "特殊":'],
        new=['        if t == "特殊":'],
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
        mid="M8 败局没有鼓励行",
        path=CMD,
        old=['    if not res.get("win"):',
             '        lines.append(txt.battle_defeat(_seed))'],
        new=['    if False:',
             '        lines.append(txt.battle_defeat(_seed))'],
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
    # ---- 第二轮：小说式战报（铺垫 / 高潮 / 吹捧 / 嘲讽 / 点睛）----
    dict(
        mid="M13 铺垫不出场",
        path=CMD,
        old=['        txt.scene_line(_ch, _seed),',
             '        txt.encounter_head(n_total, foes, res["rounds"], bool(res["win"]), killed,'],
        new=['        txt.encounter_head(n_total, foes, res["rounds"], bool(res["win"]), killed,'],
        why="战报只剩数据，没有「在哪打的」——回到改前的电报体",
    ),
    dict(
        mid="M14 铺垫与章节无关",
        path=CMD,
        old=['        txt.scene_line(_ch, _seed),'],
        new=['        txt.scene_line(1, _seed),'],
        why="13 章共用第 1 章的风景，玩家推到第 8 章还在萧家后山",
    ),
    dict(
        mid="M15 胜局也出嘲讽",
        path=CMD,
        old=['    if res.get("win"):',
             '        _gl = txt.drop_glory_line([n for n, _c in drops], _seed) \\'],
        new=['    if False:',
             '        _gl = txt.drop_glory_line([n for n, _c in drops], _seed) \\'],
        why="赢了还被笑，情绪反向",
    ),
    dict(
        mid="M16 吹捧不看掉落成色",
        path=TEXT,
        old=['    return "🏅 " + _pick(DROP_GLORY[drop_tier(names)], seed)'],
        new=['    return "🏅 " + _pick(DROP_GLORY["common"], seed)'],
        why="垃圾料与珍稀料同一句，用户点名的「垃圾也要吹」落空",
    ),
    dict(
        mid="M17 败局没有嘲讽",
        path=CMD,
        old=['        _taunt = _verdict.taunt(_vd["case"], _vd["seed"], foe=_vd["foe"],',
             '                                skill=_vd["skill"], beast=_vd["foe_beast"])'],
        new=['        _taunt = ""'],
        why="败局只有鼓励没有嘲讽，情绪只扬不抑（2026-11-28 起嘲讽由活着的怪开口）",
    ),
    dict(
        mid="M20 胜局不赞叹",
        path=CMD,
        old=['        if _praise:',
             '            lines.append("\u3000" + _praise)'],
        new=['        if False:',
             '            lines.append("\u3000" + _praise)'],
        why="判词之后没有赞叹，赢下来干巴巴（用户指定这句要跟在判词后）",
    ),
    dict(
        mid="M21 赞叹位置错（塞到战报最前）",
        path=CMD,
        old=['        if _praise:',
             '            lines.append("\u3000" + _praise)'],
        new=['        if _praise:',
             '            lines.insert(0, "\u3000" + _praise)'],
        why="赞叹不跟判词（用户指定必须紧跟「二星大斗师斗晶映体」那句）",
    ),
    dict(
        mid="M18 点睛台词现编",
        path=TEXT,
        old=['        return f"　「{text}」"'],
        new=['        return "　「我命由我不由天」"'],
        why="台词池外的自造句，冒充原文",
    ),
    dict(
        mid="M19 点睛忽略章节",
        path=TEXT,
        old=['    pool = CHAPTER_QUOTE.get(int(ch or 0), ())'],
        new=['    pool = CHAPTER_QUOTE.get(1, ())'],
        why="第 13 章还在喊第一章那句，章节感归零",
    ),
    dict(
        mid="M20 池内长短失控",
        path=TEXT,
        old=['    "great": ("林子里静了一瞬，随后是压不住的哗然。",'],
        new=['    "great": ("林子里静了一瞬，随后是压不住的哗然；那种安静让人后脊发凉，'
             '连风都停在了半空，远处的佣兵不由自主地后退了半步。",'],
        why="单池极差 >4 会把「1 敌 vs 5 敌 ≤24 字」的预算吃掉，未来加句必炸",
    ),
    dict(
        mid="M21 越级不优先",
        path=TEXT,
        old=['    if int(foe_max_lv or 0) >= int(my_lv or 0) + 2:',
             '        return "crush"'],
        new=['    if False:',
             '        return "crush"'],
        why="越级斩杀被当成普通大捷吹，最该吹的时刻哑了",
    ),
    # ---- 第三轮：多维度诊断 + 大句池 + 随机（2026-11-28 用户二次纠正）----
    dict(
        mid="M22 斗气枯竭判成零暴击",
        path=VERDICT,
        old=['            out["case"] = "no_mp"'],
        new=['            out["case"] = "crit_dry"'],
        why="用户点名的「没斗气多去炼药」被泛化的零暴击嘲讽顶掉",
    ),
    dict(
        mid="M23 暴击串维度消失",
        path=VERDICT,
        old=['            out["case"] = "crit_storm"'],
        new=['            out["case"] = "plain"'],
        why="三连暴击的爽点被平胜句抹掉，赢了也说不出赢在哪",
    ),
    dict(
        mid="M24 句池缩到一句",
        path=VERDICT,
        old=['    return pool[rng.randrange(len(pool))]'],
        new=['    return pool[0]'],
        why="每档只剩一句，打十场复读十遍（用户「语句一定要多」落空）",
    ),
    dict(
        mid="M25 随机退化成取模",
        path=VERDICT,
        old=['    return pool[rng.randrange(len(pool))]'],
        new=['    return pool[int(seed) % len(pool)]'],
        why="等间隔种子全落同一句，看起来随机其实固定轮转",
    ),
    dict(
        mid="M26 嘲讽冒认说话人",
        path=VERDICT,
        old=['    return f"{foe}{action}：「{spoken}」"'],
        new=['    return f"它{action}：「{spoken}」"'],
        why="怪名被替换成「它」，最后一行变成无主旁白",
    ),
    dict(
        mid="M27 差一口气抢走没气嘲讽",
        path=VERDICT,
        old=['        elif plain_ratio >= _PLAIN_RATIO_DRY and low_mp:'],
        new=['        elif False:'],
        why="「没斗气多炼药」被泛化的「就差一点」顶掉，玩家不知道该改什么",
    ),
    # ---- 第四轮：小说口吻（2026-11-28 用户「太 AI 了，没有厮杀的氛围」）----
    dict(
        mid="M28 骂阵塞回战后点评",
        path=VERDICT,
        old=['        "你那手{skill}，火候还差得远！",'],
        new=['        "练满是好事，可惜练的是一门天生的下风招。",'],
        why="用户点名「完全不能用」的点评味台词回到敌人嘴里",
    ),
    dict(
        mid="M29 旁白退回战报评语",
        path=VERDICT,
        old=['        "血顺着指尖往下滴，人却没退。",'],
        new=['        "他不像只会一招的人——每一手都有后手。",'],
        why="判词后的赞叹变成旁观者总结，不是厮杀现场的画面",
    ),
    dict(
        mid="M30 魔兽套人型口吻",
        path=VERDICT,
        old=['    "龇牙低吼", "喉咙里滚出一声闷哼", "吐着信子", "甩了甩尾巴",'],
        new=['    "桀桀怪笑", "喉咙里滚出一声闷哼", "吐着信子", "甩了甩尾巴",'],
        why="让岩蛇桀桀怪笑——魔兽不会像魂殿那样说话，破坏原著感",
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
    code = r.returncode

    # 第二道：脚本式守门（手机无 pytest 也能跑），覆盖 15+15 个诊断维度、
    # 每档句池与随机轮换。只跑 pytest 的话 battle_verdict.py 的变异根本没人拦。
    s = subprocess.run([sys.executable, FLAVOR_TEST], cwd=ROOT,
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                       timeout=600)
    sout = s.stdout.decode("utf-8", "replace")
    out += "\n--- script: test_doupo_battle_flavor ---\n" + sout
    if s.returncode != 0:
        code = 1
        fails += [ln.strip() for ln in sout.splitlines() if ln.startswith("FAIL ")]
    return code, fails, total, out


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
    # 2026-11-28 事故：早退路径绕过 finally，把源码留在改坏状态还以为全绿。
    # 跑完必须自己核一次工作区，不能只信 MUTATION_TOTAL。
    import subprocess as _sp
    st = _sp.run(["git", "status", "--porcelain"], cwd=ROOT,
                 stdout=_sp.PIPE, stderr=_sp.STDOUT, text=True).stdout.strip()
    print("--- git status（应只剩本轮预期改动）---")
    print(st or "(clean)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
