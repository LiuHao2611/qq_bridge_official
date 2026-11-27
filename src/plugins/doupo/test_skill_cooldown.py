# -*- coding: utf-8 -*-
r"""斗技冷却真实现守门（Q6，2026-11-27）。

旧行为：斗技详情卡写「冷却 N 回合」，但 `_pick_skill` 只按斗气过滤，同一技
每回合连放 —— 卡面数值是死字段。现在每场战斗自带冷却表（技名→上次释放回合），
``round_no - used < cd`` 的技本回合不可用；跨场不结转。

运行：
    python src/plugins/doupo/test_skill_cooldown.py
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

from doupo import core  # noqa: E402


def _p() -> dict:
    p = core.create_player("cd-probe", "冷却", "g1")
    p["faction"] = "xiaojia"
    p["faction_name"] = "萧家"
    p["learned"] = ["碎石掌", "裂爪击"]
    p["loadout"] = ["碎石掌", "裂爪击"]
    return p


def _pick(p, cd_state, round_no):
    return core._pick_skill(p, core.mp_max(p), rng=lambda: 0,
                            round_no=round_no, cd_state=cd_state)


def test_cooldown_blocks_repeat_inside_window():
    p = _p()
    cd = {}
    first, _ = _pick(p, cd, 1)
    assert first is not None and int(first.get("cd") or 0) > 0, "样本技没有冷却"
    name, span = first["name"], int(first["cd"])
    assert cd.get(name) == 1, "释放后没记录冷却起点"
    second, _ = _pick(p, cd, 1 + span - 1)
    assert second is None or second["name"] != name, (
        "%s 在冷却第 %d 回合又被放出" % (name, span - 1))


def test_cooldown_expires_and_allows_again():
    p = _p()
    cd = {}
    first, _ = _pick(p, cd, 1)
    name, span = first["name"], int(first["cd"])
    again, _ = _pick(p, cd, 1 + span)
    assert again is not None and again["name"] == name, "冷却到期后该技没恢复"


def test_none_state_keeps_old_behavior():
    """纯计算调用方不传 cd_state 时逐字节沿用旧口径。"""
    p = _p()
    a = core._pick_skill(p, core.mp_max(p), rng=lambda: 0)
    b = core._pick_skill(p, core.mp_max(p), rng=lambda: 0, round_no=99)
    assert a == b, "不传 cd_state 时行为被改变"


def test_battle_and_duel_pass_their_own_cd_table():
    """每场各自一份冷却表：fight / duel 都要接线，且不能是同一份。"""
    bs = inspect.getsource(core._pick_skill)
    assert "cd_state is not None" in bs and "round_no" in bs, "选招内核没接冷却"
    fsrc = inspect.getsource(
        __import__("doupo.battle_core", fromlist=["fight"]))
    assert "cd_state" in fsrc and "round_no=rounds" in fsrc, "fight 没传冷却表"
    dsrc = inspect.getsource(core.duel)
    assert "cd_p" in dsrc and "cd_v" in dsrc and "round_no=rounds" in dsrc, (
        "duel 没给双方各自传冷却表")


def test_card_cd_and_battle_read_same_field():
    """卡面「冷却 N 回合」与战斗过滤必须同源 entry['cd']。"""
    p = _p()
    e = core._loadout_entries(p)[0]
    assert int(e.get("cd") or 0) > 0
    assert "hit.get('cd'" in inspect.getsource(
        __import__("doupo.commands", fromlist=["cmd_skill"]))
    assert 'e.get("cd")' in inspect.getsource(core._pick_skill)


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
