# -*- coding: utf-8 -*-
r"""可直接修三条守门（D3 / D5 / E4，2026-11-27）。

* **D3**：``weekly_commit`` 没有幂等守卫 —— 同一会话连调两次会重复发金币/修为/物品
  （真跑 10000→11000→12000）。当前命令路径通关即 clear，正常玩家不可达，
  但未来加重试 / 按钮连点就会翻车。
* **D5**：新号 ``main_progress`` 是 ``None``（读侧有兜底暂不崩），一旦某处写成
  ``p["main_progress"] += n`` 就是 TypeError。``create_player`` 必须显式落 0。
* **E4**：文本「斗技 / 宗门斗技」页还拿内部数值档名（普通/高阶/特殊）当分组标题，
  卡面早已改原著阶级（黄/玄/地/天）。统一走 ``skill_rank.group_label``。

运行：
    python src/plugins/doupo/test_quick_fixes_20261127.py
"""
from __future__ import annotations

import inspect
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
    nb.get_driver = lambda: types.SimpleNamespace(on_startup=lambda f: f)
    nb.get_bot = lambda: types.SimpleNamespace(send=lambda *a, **k: None)

    def on_regex(*a, **k):
        return types.SimpleNamespace(handle=lambda *a, **k: (lambda f: f),
                                     finish=lambda *a, **k: None)
    nb.on_regex = on_regex
    sys.modules["nonebot"] = nb
    for mod in ("nonebot.adapters", "nonebot.adapters.onebot",
                "nonebot.adapters.onebot.v11"):
        sys.modules[mod] = types.ModuleType(mod)
    for attr in ("Message", "MessageEvent", "MessageSegment"):
        setattr(sys.modules["nonebot.adapters.onebot.v11"], attr, object)


_stub_nonebot()

from doupo import commands  # noqa: E402
from doupo import config as cfg  # noqa: E402
from doupo import core  # noqa: E402
from doupo import skill_rank as rank_mod  # noqa: E402


# ---------------------------------------------------------------------------
# D3：weekly_commit 幂等
# ---------------------------------------------------------------------------
def _weekly_fixture():
    p = core.create_player("d3-probe", "幂等探针", "g1")
    p["gold"], p["exp"], p["bag"], p["weapons"] = 10000, 0, {}, ["玄重尺"]
    st = {"staged": {"gold": 1000, "exp": 500, "items": {"炎星石": 3},
                     "weapons": []}, "cleared": False}
    return p, st


def test_weekly_commit_first_call_pays():
    p, st = _weekly_fixture()
    res = core.weekly_commit(p, st)
    assert res["ok"] and not res.get("already")
    assert p["gold"] == 11000 and p["exp"] == 500
    assert core.count_item(p, "炎星石") == 3
    assert st["cleared"] is True


def test_weekly_commit_second_call_is_noop():
    p, st = _weekly_fixture()
    core.weekly_commit(p, st)
    gold, exp = p["gold"], p["exp"]
    stones = core.count_item(p, "炎星石")
    again = core.weekly_commit(p, st)
    assert again.get("already") is True, "重复结算没被拦（D3 没生效）"
    assert p["gold"] == gold and p["exp"] == exp, "同一会话被发了两次金币/修为"
    assert core.count_item(p, "炎星石") == stones, "物品被重复发放"
    assert again["gold"] == 0 and again["exp"] == 0


def test_weekly_commit_does_not_drop_the_receipt():
    """幂等守卫不能顺手清掉 staged —— 回执还要给玩家看结算明细。"""
    p, st = _weekly_fixture()
    core.weekly_commit(p, st)
    core.weekly_commit(p, st)
    assert int(st["staged"]["gold"]) == 1000 and st["staged"]["items"]["炎星石"] == 3


# ---------------------------------------------------------------------------
# D5：create_player 显式 main_progress
# ---------------------------------------------------------------------------
def test_new_player_has_explicit_main_progress():
    p = core.create_player("d5-probe", "进度探针", "g1")
    assert "main_progress" in p, "新号缺 main_progress 字段"
    assert p["main_progress"] == 0 and isinstance(p["main_progress"], int)
    # 真实用途：可以直接自增（旧版 None 会 TypeError）
    p["main_progress"] += 1
    assert p["main_progress"] == 1


def test_main_progress_still_resets_on_submit():
    p = core.create_player("d5-probe2", "进度探针", "g1")
    m = core.current_main(p)
    core.main_progress(p, m["kind"], int(m["target"]), item=m.get("item"))
    p["lv"] = int(m.get("require_lv") or 1)
    res = core.main_submit(p)
    assert res["ok"] and p["main_progress"] == 0


# ---------------------------------------------------------------------------
# E4：分组标题走原著阶级
# ---------------------------------------------------------------------------
def test_group_label_speaks_original_ranks():
    assert rank_mod.group_label([{"rank": "黄阶中级"}]) == "黄阶"
    assert rank_mod.group_label([{"rank": "玄阶高级"}, {"rank": "黄阶低级"}]) == "黄阶·玄阶", (
        "同一组里的大阶没有按原著顺序排")
    assert rank_mod.group_label([{"rank": "秘法"}]) == "秘法"
    assert rank_mod.group_label([]) == rank_mod.UNKNOWN
    assert rank_mod.group_label([{"rank": ""}]) == rank_mod.UNKNOWN


def test_legacy_tier_names_never_reach_the_page():
    """文本页不许再把 普通/高阶/特殊 当分组标题印出来。"""
    src = inspect.getsource(commands.cmd_skills)
    assert "group_label" in src, "「斗技」页标题没走原著阶级"
    assert "〔{tier}〕" not in src, "「斗技」页还在印内部档名"
    assert "〔{tier}〕" not in inspect.getsource(commands.cmd_faction_skills), (
        "「宗门斗技」页还在印内部档名")
    for legacy in rank_mod.LEGACY_TIERS:
        assert f"〔{legacy}〕" not in src


def test_single_skill_tag_shows_real_rank():
    src = inspect.getsource(commands.cmd_skills)
    assert "e.get('rank')" in src, "已上阵那一行还印着数值档名"


def test_no_cross_tier_surprise_in_labels():
    """真实数据里 特殊档混了一门天阶（龙凰古甲）：标题必须跟着数据走，不许写死。"""
    from doupo import skill_book_data as bd
    special = [r for r in bd.BOOK_ROWS if r.get("tier") == "特殊"]
    assert special, "数据里没有特殊档，用例无效"
    label = rank_mod.group_label(special)
    fams = {rank_mod.fam_of(r.get("rank")) for r in special}
    for fam in fams:
        assert fam in label, "标题漏了实际存在的 %s" % fam
    assert cfg is not None


if __name__ == "__main__":
    fns = sorted(k for k in globals() if k.startswith("test_"))
    fails = 0
    for fn in fns:
        try:
            globals()[fn]()
            print("PASS", fn)
        except Exception as exc:  # noqa: BLE001
            fails += 1
            print("FAIL", fn, exc)
    print("== %d passed, %d failed ==" % (len(fns) - fails, fails))
    sys.exit(1 if fails else 0)
