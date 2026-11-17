# -*- coding: utf-8 -*-
"""各派武器门禁：不许把斗技/人名/绰号/斗气凝聚物当「可存背包的兵器」。

2026-11-17 用户要求「严格参考原著整理各宗门武器」后的防重犯闸门：
  * 下一轮 agent 若把「游身尺 / 邙天尺 / 血剑吴昊 / 屠龙剑」塞进武器池，
    这里会直接红；
  * 若给 25 派里原著无具名兵器的宗门硬凑一把，这里也会红。

运行：python tests/test_doupo_weapons_evidence.py
"""
from __future__ import annotations

import importlib.util
import os

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
EVID = os.path.join(ROOT, "src", "plugins", "doupo", "weapon_evidence.py")
CORPUS = os.environ.get("DOUPO_NOVEL_DIR", "C:/Users/L/AppData/Local/Temp/chaps")


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ev = _load(EVID, "_wev_for_gate")

VALID_KIND = {"entity", "condensed", "skill", "nickname", "person", "metaphor", "tool"}


def test_entity_quote_contains_key_token():
    """每条实体兵器的引文里必须真能找到 key_token（防张冠李戴）。"""
    bad = []
    for name, row in ev.WEAPON_EVIDENCE.items():
        if row["kind"] != "entity":
            continue
        if row["key_token"] not in row["quote"]:
            bad.append("%s：key_token「%s」不在引文中" % (name, row["key_token"]))
    assert not bad, "\n".join(bad)


def test_excluded_quote_contains_key_phrase():
    """剔除区引文要能自证其类别（如斗技引文里得有「斗技」字样）。"""
    bad = []
    for name, row in ev.EXCLUDED.items():
        if row["kind"] not in VALID_KIND:
            bad.append("%s：未知 kind=%s" % (name, row["kind"]))
        token = row.get("key_token", name)
        if token not in row["quote"]:
            bad.append("%s：剔除词「%s」不在引文中" % (name, token))
    assert not bad, "\n".join(bad)


def test_fake_weapons_are_excluded():
    """真实踩过的坑：这些词看着像武器，必须待在剔除区、且不是 entity。"""
    fake = ["游身尺", "邙天尺", "屠龙剑", "血矛", "圣像妖凰钟",
            "开天利剑", "血剑吴昊", "天泉剑唐鹰", "翡翠尺"]
    bad = []
    for w in fake:
        if w in ev.WEAPON_EVIDENCE:
            bad.append("%s 混进了武器池" % w)
        if w not in ev.EXCLUDED:
            bad.append("%s 没有剔除记录" % w)
    assert not bad, "\n".join(bad)


def test_excluded_never_entity():
    """剔除区任何一条都不许标成 entity。"""
    bad = [n for n, r in ev.EXCLUDED.items() if r["kind"] == "entity"]
    assert not bad, "剔除区被标成实体兵器：%s" % "、".join(bad)


def test_no_overlap_between_pool_and_excluded():
    bad = set(ev.WEAPON_EVIDENCE) & set(ev.EXCLUDED)
    assert not bad, "武器池与剔除区重叠：%s" % "、".join(sorted(bad))


def test_entity_count_floor():
    """实体兵器不少于 14 把（防再次「只有玄重尺」）。"""
    n = len(ev.entity_weapons())
    assert n >= 14, "实体兵器只剩 %d 把" % n


def test_hao_lei_is_fenglei_treasure():
    """唯一明写「门派之宝」的兵器归属不能被改错。"""
    r = ev.WEAPON_EVIDENCE.get("昊雷锤")
    assert r and r["kind"] == "entity"
    assert "风雷" in r["faction"] and "之宝" in r["quote"]


def test_25_factions_covered():
    """游戏宗门：有兵器的进 WEAPON_EVIDENCE，没兵器的必须有审计记录，不许静略。"""
    from importlib.util import spec_from_file_location
    fp = os.path.join(ROOT, "src", "plugins", "doupo", "factions.py")
    spec = spec_from_file_location("_fac_for_gate", fp)
    fac = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fac)

    covered = {r["faction"] for r in ev.WEAPON_EVIDENCE.values()}
    audited = set(ev.NO_NAMED_WEAPON_AUDIT)
    bad = []
    for fid, f in fac.FACTIONS.items():
        nm = f["name"]
        hit = [c for c in covered if c == nm or c.startswith(nm) or nm in c]
        if not hit and nm not in audited:
            bad.append("%s（%s）既无兵器又无审计" % (fid, nm))
    assert not bad, "\n".join(bad)


def _main():
    fns = [(n, f) for n, f in globals().items() if n.startswith("test_") and callable(f)]
    failed = 0
    for n, f in sorted(fns):
        try:
            f()
            print("PASS", n)
        except AssertionError as e:
            failed += 1
            print("FAIL", n, "->", e)
    print("\n%d/%d 通过，失败 %d" % (len(fns) - failed, len(fns), failed))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(_main())
