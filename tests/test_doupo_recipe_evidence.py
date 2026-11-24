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

# 宝库 = config.HERBS 基础 8 味 + NOVEL_HERBS。本测试保持零依赖（不 stub nonebot、
# 不 import doupo 包），用 ast 从 config.py 取第一处 HERBS 字面量即可，绝不漂移。
def _shop_herb_names():
    import ast

    src = open(os.path.join(ROOT, "src", "plugins", "doupo", "config.py"),
               encoding="utf-8").read()
    tree = ast.parse(src)
    for node in tree.body:
        if not (isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "HERBS" for t in node.targets)):
            continue
        if not isinstance(node.value, (ast.List, ast.Tuple)):
            continue
        names = set()
        for item in node.value.elts:
            if not isinstance(item, ast.Dict):
                continue
            for k, v in zip(item.keys, item.values):
                if (isinstance(k, ast.Constant) and k.value == "name"
                        and isinstance(v, ast.Constant) and isinstance(v.value, str)):
                    names.add(v.value)
        if names:
            return names
    return set()


SHOP_HERBS = _shop_herb_names() | {h["name"] for h in rd.NOVEL_HERBS}


def test_every_need_has_original_quote():
    """need 里每一味药材都要有原文引文，**除非**在 GAME_DESIGN_RECIPES 白名单里。

    2026-11-24 用户裁定：原著无成方的 43 枚补「游戏自设方」，材料合理即可、
    不冒充原著考据。白名单是**唯一**豁免通道；名单外任何无引文 need 仍然红。
    """
    bad = []
    for r in rd.NOVEL_RECIPES:
        name, need = r["name"], r.get("need") or {}
        if not need or name in ev.GAME_DESIGN_RECIPES:
            continue
        rows = ev.RECIPE_EVIDENCE.get(name) or []
        if not rows:
            bad.append("%s 有 need 但证据表没有引文（也不在游戏自设方白名单里）" % name)
            continue
        for herb in need:
            if not any(herb in row.get("materials", {}) for row in rows):
                bad.append("%s 的药材 %s 没有原文证据" % (name, herb))
    # 反向：白名单里的丹药不许混进证据表（不能既叫自设又冒充原著）
    both = sorted(ev.GAME_DESIGN_RECIPES & set(ev.RECIPE_EVIDENCE))
    assert not both, "游戏自设方不许同时进原文证据表: %s" % "、".join(both)
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
    """need={} 的丹药必须逐枚写明「为什么判无药方」，不许静略。

    游戏自设方的 43 枚虽然 now 有 need，但**原著仍无方**——审计记录保留，
    所以 stale 检查要排除白名单（它们不是「审计记录指向不存在的丹药」，
    而是「审计结论仍成立、只是游戏另配了方」）。
    """
    buyonly = [r["name"] for r in rd.NOVEL_RECIPES if not r.get("need")]
    missing = [n for n in buyonly if n not in ev.NO_RECIPE_AUDIT]
    assert not missing, "缺少无药方审计记录：%s" % "、".join(missing)

    both = [n for n in buyonly if n in ev.RECIPE_EVIDENCE]
    assert not both, "既判无药方又有证据：%s" % "、".join(both)

    stale = [n for n in ev.NO_RECIPE_AUDIT
             if n not in buyonly and n not in ev.GAME_DESIGN_RECIPES]
    assert not stale, "审计记录指向已不存在的无药方丹药：%s" % "、".join(stale)

    # 白名单必须逐枚有审计记录（证明「补方」是知情裁定，不是忘了考据就乱填）
    no_audit = sorted(ev.GAME_DESIGN_RECIPES - set(ev.NO_RECIPE_AUDIT))
    assert not no_audit, "游戏自设方没有「原著无方」审计记录: %s" % "、".join(no_audit)


def test_all_need_herbs_exist_in_shop():
    """need 的药材必须在**运行时宝库**里（config.HERBS = 基础 + 原著药材）。

    以前只查 NOVEL_HERBS，会误杀游戏基础药材（血灵草/紫叶兰/七叶花等）——
    它们是 `config.HERBS` 的前 8 味，玩家真的能在宝库买到。
    """
    bad = []
    names = SHOP_HERBS
    for r in rd.NOVEL_RECIPES:
        for herb in r.get("need") or {}:
            if herb not in names:
                bad.append("%s 需要 %s，但宝库里没有这味药材" % (r["name"], herb))
    assert not bad, "\n".join(bad)


def test_recipe_coverage_did_not_regress():
    """2026-11-24 起 NOVEL_RECIPES 67 枚里 65 枚有方（雏丹/玄丹按设定无方）。

    口径注意：cfg.RECIPES 是 75 枚（NOVEL_RECIPES 67 + 游戏自设丹 8 枚，
    后者本来就有 need）；本守门只审原著丹池 NOVEL_RECIPES。
    """
    with_need = [r["name"] for r in rd.NOVEL_RECIPES if r.get("need")]
    assert len(with_need) >= 65, "有药方的丹药只剩 %d 枚" % len(with_need)
    # 两枚按原著设定无方的丹药必须仍是纯金币购买
    for n in ("帝品雏丹", "九品玄丹"):
        rec = next(r for r in rd.NOVEL_RECIPES if r["name"] == n)
        assert not rec.get("need") and int(rec.get("buy", 0)) > 0, \
            "%s 按原著设定不该有方（洞府唯一 / 雏丹异变），应保持买价" % n


def test_ch900_misattribution_not_repeated():
    """第900章的 77 种材料方属于天魂融血丹，不能串给天火融雪丹。

    天火融雪丹 2026-11-24 有了自设方，所以判据从「need 必须为空」改成
    「不能含第900章专属材料」—— 原考据已判该章的丹名是转载笔误。
    """
    by_name = {r["name"]: r for r in rd.NOVEL_RECIPES}
    assert "龙血芝" in by_name["天魂融血丹"]["need"]
    ch900_only = {"龙血芝", "骨灵果", "玄天青藤", "魔兽精血"}
    assert not (ch900_only & set(by_name["天火融雪丹"]["need"])), \
        "天火融雪丹的自设方混进了第900章天魂融血丹的专属材料（考据已判笔误）"


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
