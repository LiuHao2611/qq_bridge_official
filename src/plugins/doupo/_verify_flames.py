# -*- coding: utf-8 -*-
"""门派异火归属门禁：展示串必须干净，考据台账必须可核验。

为什么需要（2026-11-15）
------------------------
原著里异火极其稀有，全大陆二十三种、多半被少数远古种族/强者持有。
但 ``factions.py`` 初版给 25 个门派里的 19 个都挂了 ``flames``，其中
8 朵是「云岚宗·风灵青焰」这类自造镇派灵焰，另有「幽冥毒火」「阴阳双炎」
「八荒破灭焱」三个名字原著正文全文 0 命中，丹塔那条还把原著明确写的
「无主之物」误写成「丹塔所有」。这违背「必须参考原著」的红线。

2026-11-20 第二轮（用户第二次投诉）
-----------------------------------
上一版把「第X章「标题」+ 原句 + [原著]」直接写在 ``flames`` 里，而 ``flames``
会渲染到宗门卡的「传承异火」区 —— 玩家看到的是台账，不是世界观。
现在职责拆开：

  * ``flames``            = **只给玩家看的一句话**（禁章节引文、禁标记、禁出戏词）；
  * ``FACTION_FLAME_EVIDENCE`` = 内部考据台账（带章节 + 原句），**禁止渲染**。

所以门禁的四条硬规则（任何一条不过就 exit 1）：
  1. 展示串里**不许**出现 ``第X章「…」`` 引用、``[C1]``/``[原著]``/``[游戏原创]`` 标记、
     ``原著``/``语料``/``考据`` 等出戏词；
  2. 每个有 ``flames`` 的门派必须有台账，且台账条数 >= 展示条数（不许少记）；
  3. 台账每条至少带一个 ``第X章「标题」``，且必须能在原著语料的章节表里逐字找到；
  4. 展示串与台账里出现的异火名必须在原著正文真实出现（防二手资料错名），
     且不得出现已知错名（八荒破灭焱 / 风灵青焰 …）。

正文语料沿用 ``_verify_novel.CORPUS``（默认 2133 章 html，去重后 1608 章）。
语料缺失会**显式报错**，绝不静默通过 —— 没有原著就没法验收。

用法
----
    python src/plugins/doupo/_verify_flames.py              # 校验真实数据
    python src/plugins/doupo/_verify_flames.py --selftest   # 反例注入自测
"""
from __future__ import annotations

import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import factions  # noqa: E402
import _verify_novel as vn  # noqa: E402

#: 引用串：第X章「标题」
_CITE_RE = re.compile(r"第[零〇一壹二两贰三叁四肆五伍六陆七柒八捌九玖十百千\d]+章「[^」]+」")
#: 自造标记 —— 出现即视为把游戏设定冒充原著
_FAKE_MARKS = ("[游戏原创]", "游戏自设", "镇派灵焰", "游戏原创", "本源焰", "本源火")
#: 玩家可见处的出戏词（出现即报错，不管在哪个字段）
_META_WORDS = ("原著", "语料", "考据", "原文", "未载", "代编", "游戏自设",
               "游戏原创", "游戏设定", "本作")
#: 也需要在正文里核实存在的异火名（含原著正确写法，不含二手错名）
_FLAME_WORDS = (
    "青莲地心火", "陨落心炎", "三千焱炎火", "骨灵冷火", "九龙雷罡火",
    "荒破灭焱", "九幽金祖火", "红莲业火", "火云水炎", "火山石焰",
    "金帝焚天炎", "虚无吞炎", "化生火", "净莲妖火", "生灵之焱",
    "九幽风炎", "海心焰", "龟灵地火", "帝炎",
)
#: 已知的二手错名 —— 正文 0 命中，出现即报错（这次真实犯过的错）
_WRONG_NAMES = ("八荒破灭焱", "幽冥毒火", "阴阳双炎", "风灵青焰", "丹火灵焰",
                "地火炉焰", "雷霄灵焰", "玄冰灵焰", "陨星焰", "虚空龙焰", "冥火")


def valid_cites(idx: dict) -> set[str]:
    """原著语料里所有合法的标准引用串。"""
    return {vn.cite(ch["title"], ch.get("vol_no", 0)) for ch in idx["chapters"]}


def problems(idx: dict | None = None,
             factions_mod=None) -> list[str]:
    """返回全部违规说明；空列表 = 通过。idx 可注入以便 selftest。"""
    idx = idx if idx is not None else vn.build_index()
    F = factions_mod or factions
    cites = valid_cites(idx)
    text = idx["text"]
    ledger = getattr(F, "FACTION_FLAME_EVIDENCE", {}) or {}
    bad: list[str] = []

    def _name_check(s: str, tag: str) -> None:
        for w in _WRONG_NAMES:
            if w in s:
                bad.append(f"原著正文 0 命中的错名: {w} | {tag}")
        for w in _FLAME_WORDS:
            if w in s and w not in text:
                bad.append(f"异火名未在原著正文出现: {w} | {tag}")

    for key, d in F.FACTIONS.items():
        name = d.get("name", key)
        fl = list(d.get("flames") or [])
        if not fl:
            continue

        # 规则 1：展示串必须干净
        for e in fl:
            tag = f"{name}: {e[:38]}…"
            if _CITE_RE.search(e):
                bad.append(f"展示串混进章节引文（台账该进 EVIDENCE）| {tag}")
            for m in _FAKE_MARKS:
                if m in e:
                    bad.append(f"展示串混进自造标记: {m} | {tag}")
            for w in _META_WORDS:
                if w in e:
                    bad.append(f"展示串出现出戏词「{w}」| {tag}")
            _name_check(e, tag)

        # 规则 2：台账不许缺、不许少记
        ev = tuple(ledger.get(key) or ())
        if not ev:
            bad.append(f"考据台账缺失（展示 {len(fl)} 条，台账 0 条）| {name}")
            continue
        if len(ev) < len(fl):
            bad.append(f"考据台账少于展示条数（展 {len(fl)} / 台 {len(ev)}）| {name}")

        # 规则 3：台账每条都要有可核验的章节引用
        for e in ev:
            tag = f"{name}·台账: {e[:38]}…"
            refs = _CITE_RE.findall(e)
            if not refs:
                bad.append(f"台账缺少原著章节引用 | {tag}")
            for r in refs:
                if r not in cites:
                    bad.append(f"引用章号/标题在原著语料中不存在: {r} | {tag}")
            _name_check(e, tag)
    return bad


def selftest() -> int:
    """拿这次真实犯过的错去试：门禁必须报错，改回来必须通过。"""
    idx = vn.build_index()
    real = problems(idx)
    if real:
        print("SELFTEST FAIL: 真实数据本身就违规，先修数据")
        for p in real[:5]:
            print("   ", p)
        return 1

    orig = {k: list(v.get("flames") or []) for k, v in factions.FACTIONS.items()}
    orig_ev = dict(getattr(factions, "FACTION_FLAME_EVIDENCE", {}) or {})
    try:
        # 反例 1：把考据（章节引文）塞回展示串 —— 2026-11-20 玩家投诉的原始形态
        factions.FACTIONS["danta"]["flames"] = [
            "三千焱炎火(9)：丹塔所有（第一百五十四章「三千焱炎火」『虽然与丹塔有关』）[原著]"]
        p = problems(idx)
        ok1 = any("展示串混进章节引文" in x for x in p)
        print(("  PASS" if ok1 else "  FAIL"), "反例1 考据塞回展示串 → 必须报错")
        factions.FACTIONS["danta"]["flames"] = orig["danta"]

        # 反例 2：自造镇派灵焰（这次真实犯过的错）
        factions.FACTIONS["yunlanzong"]["flames"] = [
            "云岚宗·风灵青焰（游戏自设镇派灵焰）"]
        p = problems(idx)
        ok2 = any("自造标记" in x or "出戏词" in x or "错名" in x for x in p)
        print(("  PASS" if ok2 else "  FAIL"), "反例2 自造镇派灵焰 → 必须报错")
        factions.FACTIONS["yunlanzong"]["flames"] = orig["yunlanzong"]

        # 反例 3：台账被删掉（不许只留展示串、丢掉证据）
        ev = dict(orig_ev)
        ev.pop("guzu", None)
        factions.FACTION_FLAME_EVIDENCE = ev
        p = problems(idx)
        ok3 = any("考据台账缺失" in x for x in p)
        print(("  PASS" if ok3 else "  FAIL"), "反例3 台账缺失 → 必须报错")
        factions.FACTION_FLAME_EVIDENCE = orig_ev

        # 反例 4：编造章号（台账里写一个不存在的章）
        ev = dict(orig_ev)
        ev["guzu"] = ("金帝焚天炎(4)：古族传承（第九千九百九十九章「不存在的章」）[原著]",)
        factions.FACTION_FLAME_EVIDENCE = ev
        p = problems(idx)
        ok4 = any("不存在" in x for x in p)
        print(("  PASS" if ok4 else "  FAIL"), "反例4 编造章号 → 必须报错")
        factions.FACTION_FLAME_EVIDENCE = orig_ev

        # 反例 5：二手错名（原名八荒破灭焱，原著作荒破灭焱）
        factions.FACTIONS["yanzu"]["flames"] = ["八荒破灭焱（榜列六）：炎族镇族之火。"]
        p = problems(idx)
        ok5 = any("错名" in x for x in p)
        print(("  PASS" if ok5 else "  FAIL"), "反例5 二手错名八荒破灭焱 → 必须报错")
        factions.FACTIONS["yanzu"]["flames"] = orig["yanzu"]

        ok = ok1 and ok2 and ok3 and ok4 and ok5
        print("\nSELFTEST:", "PASS (5/5 反例全被拦)" if ok else "FAIL")
        return 0 if ok else 1
    finally:
        for k, v in orig.items():
            factions.FACTIONS[k]["flames"] = v
        factions.FACTION_FLAME_EVIDENCE = orig_ev


def main() -> int:
    if not os.path.isdir(vn.CORPUS):
        print("原著语料不存在：%s" % vn.CORPUS)
        print("设 DOUPO_NOVEL_DIR 指到章节目录；没有原著不允许验收通过。")
        return 1
    if "--selftest" in sys.argv:
        return selftest()
    idx = vn.build_index()
    print("原著正文: %d 章 / %d 字" % (len(idx["chapters"]), len(idx["text"])))
    print("门派数: %d, 有异火归属: %d, 条目数: %d, 台账门派: %d" % (
        len(factions.FACTIONS),
        sum(1 for d in factions.FACTIONS.values() if d.get("flames")),
        sum(len(d.get("flames") or []) for d in factions.FACTIONS.values()),
        len(getattr(factions, "FACTION_FLAME_EVIDENCE", {}) or {})))
    bad = problems(idx)
    if bad:
        print("\nFLAMES_GATE FAIL (%d):" % len(bad))
        for p in bad:
            print("  -", p)
        return 1
    print("\nFLAMES_GATE PASS: 展示串零考据 / 台账每条有可核验章节证据")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
