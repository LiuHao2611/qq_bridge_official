# -*- coding: utf-8 -*-
"""丹方门禁：need 不许凭空写，「无药方」不许随口判。

2026-11-16 用户纠正「不可能只有这几个丹药有配药」后的防重犯闸门。
下一轮 agent 若只照着 recipes_data.py 的旧写法补 need={}，或看到
need 为空就写「原著未载药方」，这里会直接红。

运行：python tests/test_doupo_recipe_evidence.py
"""
from __future__ import annotations

import importlib.util
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DATA = os.path.join(ROOT, "src", "plugins", "doupo", "recipes_data.py")
EVID = os.path.join(ROOT, "src", "plugins", "doupo", "recipe_evidence.py")


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


rd = _load(DATA, "_rd_for_gate")
ev = _load(EVID, "_ev_for_gate")


def test_every_need_has_original_quote():
    """need 里每一味药材都要有原文引文，且引文里真能找到对应字样。"""
    bad = []
    for r in rd.NOVEL_RECIPES:
        name, need = r["name"], r.get("need") or {}
        if not need:
            continue
        rows = ev.RECIPE_EVIDENCE.get(name) or []
        if not rows:
            bad.append("%s 有 need 但证据表没有引文" % name)
            continue
        for herb in need:
            if not any(herb in row.get("materials", {}) for row in rows):
                bad.append("%s 的药材 %s 没有原文证据" % (name, herb))
    assert not bad, "\n".join(bad)


def test_every_quote_token_really_in_quote():
    """证据表自身也要自洽：materials 的 token 必须是 quote 的子串。"""
    bad = []
    for name, rows in ev.RECIPE_EVIDENCE.items():
        for row in rows:
            for herb, token in row.get("materials", {}).items():
                if token not in row["quote"]:
                    bad.append("%s/%s：%s 不在引文中" % (name, row["cite"], herb))
    assert not bad, "\n".join(bad)


def test_buyonly_must_have_audit_record():
    """need={} 的丹药必须逐枚写明「为什么判无药方」，不许静略。"""
    buyonly = [r["name"] for r in rd.NOVEL_RECIPES if not r.get("need")]
    missing = [n for n in buyonly if n not in ev.NO_RECIPE_AUDIT]
    assert not missing, "缺少无药方审计记录：%s" % "、".join(missing)

    both = [n for n in buyonly if n in ev.RECIPE_EVIDENCE]
    assert not both, "既判无药方又有证据：%s" % "、".join(both)

    stale = [n for n in ev.NO_RECIPE_AUDIT if n not in buyonly]
    assert not stale, "审计记录指向已不存在的无药方丹药：%s" % "、".join(stale)


def test_all_need_herbs_exist_in_shop():
    bad = []
    names = {h["name"] for h in rd.NOVEL_HERBS}
    for r in rd.NOVEL_RECIPES:
        for herb in r.get("need") or {}:
            if herb not in names:
                bad.append("%s 需要 %s，但宝库里没有这味药材" % (r["name"], herb))
    assert not bad, "\n".join(bad)


def test_recipe_coverage_did_not_regress():
    """上次被用户指出只有 10 枚有方；补录后至少 20 枚，防整批回退。"""
    with_need = [r["name"] for r in rd.NOVEL_RECIPES if r.get("need")]
    assert len(with_need) >= 20, "有药方的丹药只剩 %d 枚" % len(with_need)


def test_ch900_misattribution_not_repeated():
    """第900章的 77 种材料方属于天魂融血丹，不能给天火融雪丹。"""
    by_name = {r["name"]: r for r in rd.NOVEL_RECIPES}
    assert "龙血芝" in by_name["天魂融血丹"]["need"]
    assert not by_name["天火融雪丹"]["need"]


def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in tests:
        try:
            fn()
            print("PASS %s" % fn.__name__)
        except AssertionError as e:
            failed += 1
            print("FAIL %s: %s" % (fn.__name__, e))
    print("通过 %d，失败 %d" % (len(tests) - failed, failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
