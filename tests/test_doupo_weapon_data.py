# -*- coding: utf-8 -*-
"""武器数值 + 搭配表门禁。

守的行为（用户感受得到的）：
  * 14 件考据出的实体兵器每件都有四词条数值和专属效果；
  * 专属效果维度 / effect 映射只能取已对接的合法值，不许随手编；
  * 推荐搭配的斗技必须在 skill_book 里真实存在（否则玩家「学习」不到）；
  * 数值表与考据证据表一一对应，不多不少。

运行：python tests/test_doupo_weapon_data.py
"""
from __future__ import annotations

import importlib.util
import os

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DP = os.path.join(ROOT, "src", "plugins", "doupo")


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


wd = _load("_wd_for_gate", os.path.join(DP, "weapon_data.py"))
we = _load("_we_for_gate", os.path.join(DP, "weapon_evidence.py"))
sb = _load("_sb_for_gate", os.path.join(DP, "skill_book.py"))

VALID_DIM = {"damage", "speed", "hits", "evade", "defense"}
VALID_FX = {v["fx"] for v in wd.DIM_MAP.values()} - {None}
STATS = {"力量", "敏捷", "斗气", "防御"}
# skill_book 暴露的真实斗技名集合
BOOK_NAMES = {r["name"] for r in sb._BOOK_ROWS}


def test_all_14_weapons_have_stats():
    names = set(wd.all_names())
    assert len(names) == 14, "武器数不是 14：%d" % len(names)
    bad = []
    for n in names:
        w = wd.WEAPONS[n]
        if set(w.get("stats") or {}) != STATS:
            bad.append("%s 四词条不全：%s" % (n, w.get("stats")))
        if any(int(v) <= 0 for v in w["stats"].values()):
            bad.append("%s 有非正数值" % n)
        if w.get("affinity") not in STATS:
            bad.append("%s 主属性非法：%s" % (n, w.get("affinity")))
    assert not bad, "\n".join(bad)


def test_special_dim_and_fx_legal():
    bad = []
    for n, w in wd.WEAPONS.items():
        sp = w.get("special") or {}
        if sp.get("dim") not in VALID_DIM:
            bad.append("%s 影响维度非法：%s" % (n, sp.get("dim")))
        fx = sp.get("fx")
        if fx is not None and fx not in VALID_FX:
            bad.append("%s effect 映射非法：%s" % (n, fx))
        # dim 与 fx 必须与 DIM_MAP 对齐（不许写 damage 却挂 chain）
        expect = wd.DIM_MAP.get(sp.get("dim"), {}).get("fx")
        if fx != expect:
            bad.append("%s：dim=%s 应映射 %s，实际 %s" % (n, sp.get("dim"), expect, fx))
        if not (0 < float(sp.get("v", 0)) < 0.5):
            bad.append("%s 影响幅度越界：%s" % (n, sp.get("v")))
        if not sp.get("basis"):
            bad.append("%s 专属效果缺原著依据" % n)
    assert not bad, "\n".join(bad)


def test_recommend_skills_exist():
    bad = []
    for n, w in wd.WEAPONS.items():
        rec = w.get("recommend") or []
        if not rec:
            bad.append("%s 没有推荐斗技" % n)
        for s in rec:
            if s not in BOOK_NAMES:
                bad.append("%s 推荐的「%s」在斗技库不存在" % (n, s))
    assert not bad, "\n".join(bad)


def test_evidence_data_one_to_one():
    ev_names = set(we.entity_weapons())
    data_names = set(wd.all_names())
    assert ev_names == data_names, (
        "证据表与数值表不一致：仅在证据 %s；仅在数值 %s"
        % (sorted(ev_names - data_names), sorted(data_names - ev_names))
    )


def test_fengling_pairs_with_swords():
    """用户原话举例：剑搭配风灵分形剑造成更多伤害。必须落地。"""
    sword_names = [n for n, w in wd.WEAPONS.items() if "剑" in n]
    assert sword_names, "没有任何剑类武器"
    hit = [n for n in sword_names if "风灵分形剑" in wd.WEAPONS[n]["recommend"]]
    assert hit, "没有任何剑类武器推荐风灵分形剑：%s" % sword_names
    # 其中至少一把的联动确实加伤害/连击（不是摆设）
    assert any(wd.WEAPONS[n]["special"]["dim"] in ("damage", "hits") for n in hit)


def test_all_five_dims_covered():
    """用户列的五个维度都要有武器覆盖：伤害/速度/次数/闪避/防御。"""
    covered = {w["special"]["dim"] for w in wd.WEAPONS.values()}
    assert covered == VALID_DIM, "维度覆盖不全：缺 %s" % (VALID_DIM - covered)


def test_synergy_not_empty():
    bad = [n for n, w in wd.WEAPONS.items() if not w.get("synergy")]
    assert not bad, "缺联动文案：%s" % bad


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
