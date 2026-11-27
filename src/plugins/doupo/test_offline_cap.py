# -*- coding: utf-8 -*-
r"""C3 离线收益封顶守门（2026-11-27）。

旧行为：被动修炼 ``settle`` 按真实时间全额结算 —— 挂机三天回来一次性领三天修为，
「手离键盘也有全额产出」，与在线按分钟修的节奏割裂，连续打卡失去意义。

现在：**最多只结算 8 小时**（``CULTIVATE_OFFLINE_CAP_SEC``），超出部分直接丢弃
（不攒着下次领，否则等于把封顶改成延迟发放）。文案层会明说「只按八小时结算」。

运行：
    python src/plugins/doupo/test_offline_cap.py
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

from doupo import config as cfg  # noqa: E402
from doupo import core  # noqa: E402

T0 = 1_700_000_000.0
HOUR = 3600.0


def _player(last_settle=T0):
    p = core.create_player("c3-probe", "封顶探针", "g1")
    p["last_settle"] = last_settle
    return p


def test_cap_constant_is_eight_hours():
    assert core.CULTIVATE_OFFLINE_CAP_SEC == 8 * 3600, "封顶不是 8 小时"
    assert core.CULTIVATE_OFFLINE_CAP_SEC < 24 * 3600, "封顶宽到等于没封"


def test_within_cap_still_pays_full():
    p = _player()
    res = core.settle(p, T0 + 3 * HOUR)
    expect = int(3 * 60 * cfg.CULTIVATE_PER_MIN * core.flame_exp_mult(p))
    assert res["gain"] == expect, "八小时内被削了收益"
    assert res["minutes"] == 180
    assert res["offline_capped"] is False


def test_beyond_cap_pays_only_eight_hours():
    p = _player()
    res = core.settle(p, T0 + 72 * HOUR)   # 挂机三天
    eight = int(8 * 60 * cfg.CULTIVATE_PER_MIN * core.flame_exp_mult(p))
    full = int(72 * 60 * cfg.CULTIVATE_PER_MIN * core.flame_exp_mult(p))
    assert res["gain"] == eight, "挂机三天仍按三天结算（C3 未生效）"
    assert res["gain"] < full
    assert res["minutes"] == 480
    assert res["offline_capped"] is True


def test_cap_is_not_deferred():
    """超出部分必须丢弃：连领两次不能把三天的量补回来。"""
    p = _player()
    a = core.settle(p, T0 + 72 * HOUR)
    b = core.settle(p, T0 + 72 * HOUR + 60)
    assert a["gain"] == int(8 * 60 * cfg.CULTIVATE_PER_MIN * core.flame_exp_mult(p))
    assert b["gain"] == int(1 * cfg.CULTIVATE_PER_MIN * core.flame_exp_mult(p)), (
        "第二次结算补发了封顶外的时间（等于没封顶）")


def test_long_gap_keeps_exp_off_the_moon():
    p = _player()
    before = int(p.get("exp", 0))
    core.settle(p, T0 + 30 * 24 * HOUR)     # 挂机一个月
    gained = int(p["exp"]) - before
    eight = int(8 * 60 * cfg.CULTIVATE_PER_MIN * core.flame_exp_mult(p))
    assert gained <= eight + 1, "挂机一个月拿到的修为超过八小时上限"


def test_command_text_says_it_was_capped():
    from doupo import commands
    src = inspect.getsource(commands.cmd_cultivate)
    assert "offline_capped" in src, "封顶了却不告诉玩家，会被当吞修为"
    assert "八小时" in src


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
