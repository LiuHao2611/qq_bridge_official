# -*- coding: utf-8 -*-
r"""斗技修习门槛守门（用户 2026-11-27 拍板 Q1②「分技与功法」）。

口径（不可自行更改）：
  * **功法**（任意阶）与**玄阶及以上**斗技 → 须斗者；
  * **黄阶斗技** → 斗之气可按段位修习，段位写进显式 unlock
    （碎石掌=3 / 劈山掌=5 / 铁山拳=7，取自本作图鉴固化的原著引文）；
  * 地阶低级「幼崽即修」是 [推断]，不能覆盖原著阶位 → 恢复地阶门槛。

门槛只有一个算法 ``skill_book_schema.learn_floor``，在 ``_s()`` 里折进
``unlock``；learnable / 卡面「LvN 解锁」/ learn_named「差几级」全部只读
``unlock``，所以本文件既验数据真源，也验玩家真实可学池。

运行：
    python src/plugins/doupo/test_skill_gate.py
    py -m pytest src/plugins/doupo/test_skill_gate.py
"""
from __future__ import annotations

import os
import sys
import types

_HERE = os.path.dirname(os.path.abspath(__file__))
_PLUGINS = os.path.dirname(_HERE)
_ROOT = os.path.dirname(os.path.dirname(_PLUGINS))
for _p in (_ROOT, _PLUGINS):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def _stub_nonebot():
    nb = types.ModuleType("nonebot")

    def on_regex(*a, **k):
        m = types.SimpleNamespace()
        m.handle = lambda *a, **k: (lambda f: f)
        m.finish = lambda *a, **k: None
        return m

    nb.on_regex = on_regex
    nb.get_driver = lambda: types.SimpleNamespace(on_startup=lambda f: f)
    sys.modules["nonebot"] = nb
    for name in ("nonebot.adapters", "nonebot.adapters.onebot",
                 "nonebot.adapters.onebot.v11"):
        sys.modules[name] = types.ModuleType(name)
    v11 = sys.modules["nonebot.adapters.onebot.v11"]
    v11.MessageEvent = object
    v11.MessageSegment = object
    v11.Message = object


_stub_nonebot()

from doupo import config as cfg, core, factions as fac, skill_book as book, skills as sk  # noqa: E402
from doupo import skill_book_schema as schema  # noqa: E402

_ADVANCED = ("玄", "地", "天")


def _p(lv: int, fid: str = "xiaojia", qq: str = "gate-probe") -> dict:
    """真玩家（create_player），覆盖等级/宗门以测门槛，不用手搓假存档。"""
    p = core.create_player(qq, "门槛探针", "g1")
    p["lv"] = int(lv)
    p["faction"] = fid
    p["faction_name"] = fac.FACTIONS.get(fid, {}).get("name", "")
    p["learned"] = []
    return p


def _pool_names(lv: int, fid: str = "xiaojia") -> set[str]:
    return {e["name"] for e in sk.learnable(_p(lv, fid))}


# ---------------------------------------------------------------------------
# 1. 数据真源
# ---------------------------------------------------------------------------
def test_douzhe_lv_is_single_source_with_config():
    """10 是斗者起始等级；schema 常量必须与 config.realm_lv(1) 恒等。"""
    assert schema.DOUZHE_LV == 10
    assert cfg.realm_lv(1) == schema.DOUZHE_LV, "斗者起始等级两处手抄漂移"
    assert cfg.realm_name(schema.DOUZHE_LV - 1) == "斗之气"
    assert cfg.realm_name(schema.DOUZHE_LV) == "斗者"


def test_novel_duan_gates_are_real():
    """图鉴自己引用的原著段位必须是真门槛，不是死文案。"""
    expect = {"碎石掌": 3, "劈山掌": 5, "铁山拳": 7}
    for name, lv in expect.items():
        rec = book.SKILL_BOOK[name]
        assert int(rec["unlock"]) == lv, (
            f"{name} 原著写「{lv} 段斗之气」，实际 unlock={rec['unlock']}")
        assert str(lv) in rec["novel"] or str("一二三四五六七八九"[lv - 1]) in rec["novel"], (
            f"{name} 的 novel 引文里找不到 {lv} 段")


def test_beast_faction_lv1_exception_removed():
    """两门地阶低级不再「入派即可施展」，恢复地阶门槛。"""
    for name in ("凶熊碎地拳", "狮寸劲"):
        rec = book.SKILL_BOOK[name]
        assert int(rec["unlock"]) >= schema.DOUZHE_LV, (
            f"{name} 地阶低级却 unlock={rec['unlock']}")


def test_every_gongfa_and_advanced_rank_needs_douzhe():
    """全量扫描：功法 + 玄阶以上无一门可在斗之气期间学。"""
    bad = []
    for name, rec in book.SKILL_BOOK.items():
        floor = schema.learn_floor(rec["rank"], rec["kind"])
        if int(rec["unlock"]) < floor:
            bad.append(f"{name}({rec['rank']}/{rec['kind']}) unlock={rec['unlock']}<{floor}")
    assert not bad, "门槛闸漏网：%s" % "；".join(bad)


def test_huangji_battle_skills_stay_open_to_douzhiqi():
    """门没关死：斗之气仍有黄阶斗技可学（否则新手引导断档）。"""
    open_low = [n for n, r in book.SKILL_BOOK.items()
                if str(r["rank"]).startswith("黄")
                and r["kind"] != "功法"
                and int(r["unlock"]) <= 9]
    assert len(open_low) >= 4, "斗之气可学的黄阶斗技不足，入道赠技会断档"


# ---------------------------------------------------------------------------
# 2. 玩家真实可学池
# ---------------------------------------------------------------------------
def test_lv1_pool_has_no_gongfa_or_advanced_rank():
    p = _p(1)
    pool = sk.learnable(p)
    assert pool, "lv1 可学池为空"
    bad = [e["name"] for e in pool
           if e["kind"] == "功法" or str(e["rank"])[:1] in _ADVANCED]
    assert not bad, "斗之气一段竟然能学：%s" % bad


def test_novel_duan_progresses_at_3_5_7():
    """lv2 学不了碎石掌、lv3 才行；劈山 5、铁山 7 同理。"""
    for name, lv in (("碎石掌", 3), ("劈山掌", 5), ("铁山拳", 7)):
        assert name not in _pool_names(lv - 1), f"{name} 在 lv{lv - 1} 提前解锁"
        assert name in _pool_names(lv), f"{name} 在 lv{lv} 未解锁"


def test_douzhe_can_learn_gongfa_and_xuan():
    p = _p(schema.DOUZHE_LV)
    pool = {(e["name"], e["kind"], str(e["rank"])[:1]) for e in sk.learnable(p)}
    assert any(k == "功法" for _, k, _ in pool), "斗者仍无功法可学"
    assert any(r == "玄" for _, _, r in pool), "斗者仍无玄阶斗技可学"


def test_every_faction_has_something_to_learn_at_lv1():
    """加闸后每个宗门在斗之气一段仍有可学技（含通用池）。"""
    bad = []
    for fid in fac.FACTIONS:
        if fid in book.FACTION_SKILLS:
            pool = sk.learnable(_p(1, fid, "gate-faction:" + fid))
            if not pool:
                bad.append(fid)
    assert not bad, "这些宗门 lv1 无可学斗技：%s" % bad


def test_starter_grant_is_huangji_battle_skill():
    """入道赠技不能是功法/玄阶以上（Q1 的核心玩家可见效果）。"""
    for i in range(20):
        p = core.create_player("gate-starter:%d" % i, "入道", "g1")
        p["lv"] = 1
        for name in p.get("learned", []):
            rec = book.SKILL_BOOK.get(name)
            if not rec:
                continue
            assert rec["kind"] != "功法", f"入道送了功法：{name}"
            assert str(rec["rank"])[:1] not in _ADVANCED, f"入道送了高阶技：{name}"


def test_learn_named_reports_real_gate():
    """学习者点名玄阶技时，报的 need_lv 必须是斗者而非 1。"""
    p = _p(1)
    target = next((e for e in sk.entries_for(p)
                   if str(e["rank"])[:1] == "玄" and e.get("origin") != "异火"), None)
    if target is None:
        return
    r = core.learn_named(p, target["name"])
    assert r.get("ok") is False
    assert r.get("reason") == "level"
    assert r.get("need_lv", 0) >= schema.DOUZHE_LV, r


if __name__ == "__main__":
    fails = 0
    for fn in sorted(k for k in globals() if k.startswith("test_")):
        try:
            globals()[fn]()
            print("PASS", fn)
        except Exception as exc:  # noqa: BLE001
            fails += 1
            print("FAIL", fn, exc)
    print("== %d passed, %d failed ==" % (
        len([k for k in globals() if k.startswith("test_")]) - fails, fails))
    sys.exit(1 if fails else 0)
