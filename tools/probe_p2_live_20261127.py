# -*- coding: utf-8 -*-
r"""P2 十条 + 可直接修三条 · 生产真回包探针（2026-11-27）。

用途：在手机上用**生产 venv + 生产源码**跑一遍本轮所有改动，输出固定文本，
与本地逐字节 diff —— 证明线上跑的就是本地这套，而不是「push 报 PASS 但线上还是旧码」。

约束：
* 只读生产源码，不碰 ``data/`` 里的任何存档（玩家对象在内存里现造）；
* 不走网络、不启 nonebot driver；
* 每行固定格式，行序固定。

运行：
    本地：``python tools/probe_p2_live_20261127.py``
    手机：``PROBE_ROOT=/home/pmos/qq_bridge ~/venv/bin/python /tmp/probe_p2_live.py``
"""
from __future__ import annotations

import os
import random
import sys
import types

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.environ.get("PROBE_ROOT") or _HERE
if not os.path.isfile(os.path.join(_ROOT, "src", "plugins", "doupo", "core.py")):
    for _ in range(6):
        _ROOT = os.path.dirname(_ROOT)
        if os.path.isfile(os.path.join(_ROOT, "src", "plugins", "doupo", "core.py")):
            break
_PLUGINS = os.path.join(_ROOT, "src", "plugins")
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

from doupo import battle_core as bc  # noqa: E402
from doupo import config as cfg  # noqa: E402
from doupo import core  # noqa: E402
from doupo import skill_rank as rank_mod  # noqa: E402

OUT: list[str] = []


def out(tag: str, text: str) -> None:
    OUT.append("%-3s %s" % (tag, text))


def _player(lv=30, skills=(), mp=None):
    p = core.create_player("probe-p2", "探针", "g1")
    p["lv"] = lv
    p["learned"] = list(skills)
    p["loadout"] = list(skills)
    p["skills"] = {}
    p["bag"] = {}
    p["hp"] = core.stats(p)["体力"]
    p["mp"] = core.mp_max(p) if mp is None else mp
    return p


def _mon(lv=30):
    mon = core.monster_scaled(_player(lv), cfg.MONSTERS[0], at_lv=lv)
    mon["hp"] = 10 ** 9
    mon["atk"] = 50
    mon["def"] = 0
    mon["dodge"] = 0.0
    mon.pop("skill", None)
    return mon


def _fight(p, seed=11):
    return bc.fight(p, _mon(int(p["lv"])), rng=random.Random(seed).random,
                    roll_aid=False, auto_pill=False)


def _rng(vals):
    it = iter(vals)
    return lambda: next(it, 0.99)


# ---------------------------------------------------------------------------
# A7 境界标志能力
# ---------------------------------------------------------------------------
def probe_a7():
    for lv in (1, 45, 46, 54, 63, 64, 72, 99):
        out("A7", "lv=%-2d 普攻承伤=%.2f 怪技承伤=%.2f"
            % (lv, core.realm_flag_taken_mult(lv), core.realm_flag_taken_mult(lv, skill=True)))
    for lv, key in ((45, "化翼离地"), (46, "化翼离地")):
        p = _player(lv)
        res = _fight(p)
        hit_txt = sum(1 for it in res["log"] if key in str(it.get("notes") or ""))
        out("A7", "真战斗 lv=%d 「%s」提示=%d 次" % (lv, key, hit_txt))


# ---------------------------------------------------------------------------
# B6 普攻回气
# ---------------------------------------------------------------------------
def probe_b6():
    for lv in (1, 30, 90):
        p = _player(lv)
        cap = core.mp_max(p)
        out("B6", "lv=%-2d 上限=%-4d 单次回气=%-3d 满槽=%d 差1=%d"
            % (lv, cap, core.mp_regen_on_plain(p),
               core.mp_regen_on_plain(p, cap), core.mp_regen_on_plain(p, cap - 1)))
    p = _player(30, mp=0)
    res = _fight(p)
    plain = [it for it in res["log"] if int(it.get("mp_cost") or 0) == 0]
    casts = [it for it in res["log"] if int(it.get("mp_cost") or 0) > 0]
    out("B6", "真战斗 普攻回合=%d 其中回气>0=%d 放技回合=%d 放技回气=%d 末斗气=%d"
        % (len(plain), sum(1 for it in plain if int(it.get("mp_regen") or 0) > 0),
           len(casts), sum(int(it.get("mp_regen") or 0) for it in casts), p["mp"]))


# ---------------------------------------------------------------------------
# B7 熔断判胜
# ---------------------------------------------------------------------------
def probe_b7():
    for m_hp, mon_hp in ((30, 100), (50, 100), (49, 100), (60, 100), (0, 0)):
        out("B7", "我%-3d/怪%-3d → 判胜=%s" % (m_hp, mon_hp, bc.hard_round_verdict(m_hp, mon_hp)))
    p = _player(1)
    p["hp"] = 10 ** 6
    mon = _mon(1)
    mon["hp"] = 10 ** 7
    mon["atk"] = 1
    res = bc.fight(p, mon, rng=random.Random(7).random, roll_aid=False, auto_pill=False)
    dealt = 10 ** 7 - int(res["foe_hp_end"])
    out("B7", "真战斗(怪血1e7) 力竭=%s 判胜=%s 累计伤害=%d 剩余=%.1f%%"
        % (bool(res.get("exhausted")), bool(res.get("win")), dealt,
           100.0 * int(res["foe_hp_end"]) / 10 ** 7))
    hp2 = max(2, int(dealt / 0.8))
    p2 = _player(1)
    p2["hp"] = 10 ** 6
    mon2 = _mon(1)
    mon2["hp"] = hp2
    mon2["atk"] = 1
    res2 = bc.fight(p2, mon2, rng=random.Random(7).random, roll_aid=False, auto_pill=False)
    out("B7", "真战斗(怪血%d) 力竭=%s 判胜=%s 剩余=%.1f%%"
        % (hp2, bool(res2.get("exhausted")), bool(res2.get("win")),
           100.0 * int(res2["foe_hp_end"]) / hp2))


# ---------------------------------------------------------------------------
# C3 离线封顶
# ---------------------------------------------------------------------------
def probe_c3():
    now = 1800000000.0
    for hours in (7, 9):
        p = _player(30)
        p["last_settle"] = now - hours * 3600
        r = core.settle(p, now)
        out("C3", "离线%dh 计费=%d分 修为=%d 封顶=%s"
            % (hours, r["minutes"], r["gain"], bool(r.get("offline_capped"))))


# ---------------------------------------------------------------------------
# C4 炼药封顶
# ---------------------------------------------------------------------------
def probe_c4():
    top = cfg.ALCH_MAX
    cases = [
        ("九品无火", top, cfg.alch_tier(top)[0], 0.0),
        ("九品有火", top, cfg.alch_tier(top)[0], 0.08),
        ("八品有火", top - 1, cfg.alch_tier(top - 1)[0], 0.08),
        ("四品炼四品", 3, "四品", 0.0),
        ("四品炼一品", 3, "一品", 0.0),
        ("未入品炼四品", cfg.ALCH_NONE, "四品", 0.0),
    ]
    for name, idx, grade, flame in cases:
        rate, diff = cfg.craft_rate_for(idx, grade, flame)
        out("C4", "%s 成功率=%.3f 越阶=%d" % (name, rate, diff))


# ---------------------------------------------------------------------------
# D2 任务物品硬校验
# ---------------------------------------------------------------------------
def probe_d2():
    p = _player(60)
    p["main_idx"] = 1
    p["task"] = None
    m = core.current_main(p)
    core.main_progress(p, "herb", 3, item="紫叶兰")
    wrong = int(p.get("main_progress") or 0)
    core.main_progress(p, "herb", 3, item="血灵草")
    right = int(p.get("main_progress") or 0)
    core.main_progress(p, "herb", 1)
    none_item = int(p.get("main_progress") or 0)
    out("D2", "主线「%s」校验物品=%s 错物品=%d 对物品=%d 不报名=%d"
        % (m["name"], m.get("item"), wrong, right, none_item))


# ---------------------------------------------------------------------------
# D4 Boss 层宝箱补偿
# ---------------------------------------------------------------------------
def probe_d4():
    for floor, bonus, label in ((10, 0.6, "Boss层加成0.6"), (10, 0.0, "Boss层无加成"),
                                (1, 0.6, "普通层加成0.6")):
        p = _player(30)
        p["tower_paid_floor"] = 0
        p["weapons"] = {}
        out_list = core.tower_open_chest(p, {}, _rng([0.0, 0.5, 0.0]),
                                         floor=floor, chest_bonus=bonus)
        comp = [c for c in out_list if c.get("comp")]
        out("D4", "%s 掉落=%d 补偿=%s"
            % (label, len(out_list),
               ("%s×%d" % (comp[0]["name"], comp[0]["n"])) if comp else "无"))


# ---------------------------------------------------------------------------
# D3 / D5 / E4
# ---------------------------------------------------------------------------
def probe_quick():
    p = _player(30)
    p["gold"], p["exp"], p["bag"], p["weapons"] = 10000, 0, {}, ["玄重尺"]
    st = {"staged": {"gold": 1000, "exp": 500, "items": {"炎星石": 3}, "weapons": []},
          "cleared": False}
    core.weekly_commit(p, st)
    g1 = p["gold"]
    r2 = core.weekly_commit(p, st)
    out("D3", "首结算金币=%d 二次already=%s 二次金币=%s 现金币=%d"
        % (g1, bool(r2.get("already")), r2.get("gold"), p["gold"]))
    fresh = core.create_player("probe-d5", "新号", "g1")
    out("D5", "新号main_progress=%r" % (fresh.get("main_progress"),))
    rows = [{"rank": "黄阶中级", "tier": "黄阶"}, {"rank": "玄阶初级", "tier": "玄阶"}]
    out("E4", "分组标题=%s 单行标签=%s"
        % (rank_mod.group_label(rows),
           (rows[0].get("rank") or rows[0]["tier"])))


def main() -> int:
    for fn in (probe_a7, probe_b6, probe_b7, probe_c3, probe_c4, probe_d2,
               probe_d4, probe_quick):
        fn()
    print("\n".join(OUT))
    print("-- 共 %d 行 / 源码根 %s" % (len(OUT), _ROOT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
