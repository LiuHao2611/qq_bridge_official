# -*- coding: utf-8 -*-
"""丹雷机制行为测试（2026-11-27）。守玩家感受得到的行为，不守内部变量名。

跑法：作为自跑脚本 `python test_danlei.py`，或直接 pytest（本文件用 assert，
不依赖 nonebot）。覆盖：七品起引雷、50% 抵挡、淬炼落库、批量封顶、血量地板、
低阶零回归、文案无章号。
"""
import sys
import types
from pathlib import Path

_DOUPO = Path(__file__).resolve().parent
_pkg = types.ModuleType("doupo"); _pkg.__path__ = [str(_DOUPO)]
sys.modules.setdefault("doupo", _pkg)

from doupo import danlei as dl
from doupo import config as cfg


def t_low_grade_no_thunder():
    """六品及以下不引雷，行为与改造前逐字节相同（plan 返回 None）。"""
    for g in ("一品", "三品", "五品", "六品"):
        assert dl.plan(g, 3000, False, lambda: 0.0) is None, g
    assert dl.grade_triggers("六品") is False
    assert dl.grade_triggers("七品") is True


def t_damage_escalates():
    """掉血按品阶递增：七品 < 八品 < 九品。"""
    hm = 3000
    assert dl.hurt_of("七品", hm) < dl.hurt_of("八品", hm) < dl.hurt_of("九品", hm)
    assert dl.hurt_of("九品", hm) == dl.hurt_of("帝品", hm)  # 最高档同夹


def t_guard_50_full_immune():
    """有傀儡掷中(<0.5)→全额免伤；掷不中(≥0.5)→玩家全吃。"""
    a = dl.plan("八品", 3000, True, lambda: 0.49)
    b = dl.plan("八品", 3000, True, lambda: 0.50)
    assert a["blocked"] == 1 and a["hurt_total"] == 0
    assert b["blocked"] == 0 and b["hurt_total"] == a["hurt"]
    # 无傀儡不掷骰，恒全吃
    c = dl.plan("八品", 3000, False, lambda: 0.0)
    assert c["blocked"] == 0 and c["hurt_total"] == c["hurt"]


def t_no_puppet_no_rng_call():
    """无傀儡时绝不消耗 rng（保证低阶/无傀儡的随机序列不变）。"""
    calls = []
    def rng():
        calls.append(1); return 0.0
    dl.plan("九品", 3000, False, rng)
    assert calls == [], "无傀儡不应掷抵挡骰"
    dl.plan("九品", 3000, True, rng)
    assert len(calls) == 1, "有傀儡应恰好掷一次"


def t_batch_cap():
    """批量封顶：10 炉只结算 3 道，挡下也只挡这 3 道。"""
    r = dl.plan("八品", 3000, True, lambda: 0.1, n=10)
    assert r["n"] == dl.DANLEI_BATCH_CAP == 3
    assert r["blocked"] == 3 and r["hurt_total"] == 0
    r2 = dl.plan("八品", 3000, False, lambda: 0.9, n=50)
    assert r2["n"] == 3 and r2["hurt_total"] == 3 * r2["hurt"]


def t_hp_floor_not_resurrect():
    """血量地板：不把玩家劈死，也绝不反过来给残血玩家加血。"""
    hm = 3000
    floor = dl.hp_floor(hm)
    assert floor == max(1, int(hm * cfg.LOSE_HP_FLOOR))
    p = {"hp": 100}
    done = dl.apply_hp(p, 5000, hm)
    assert p["hp"] == 100 and done == 0, "残血低于地板时不应加血"
    p2 = {"hp": 2000}
    done2 = dl.apply_hp(p2, 5000, hm)
    assert p2["hp"] == floor and done2 == 2000 - floor, "应扣到地板为止"


def t_settle_wires_hurt_done():
    """settle 一步到位的端到端：低阶不碰 p，高阶扣血+挂 hurt_done。"""
    p_low = {"hp": 1000}
    assert dl.settle(p_low, "回灵丹-一品", 1000, False, lambda: 0.0) is None
    assert p_low["hp"] == 1000
    p_high = {"hp": 3000}
    r = dl.settle(p_high, "九品", 3000, False, lambda: 0.9)
    assert r["hurt_done"] > 0 and p_high["hp"] < 3000


def t_line_no_chapter_no_meta():
    """玩家可见文案无章节号、无工程口吻。"""
    import re
    r = dl.plan("八品", 3000, True, lambda: 0.1)
    for txt in (dl.line(r, 0, "地妖傀", purity=84),
                dl.line(dl.plan("七品", 3000, False, lambda: 0.9), 240)):
        assert not re.search(r"第\d+章|无.{0,4}系统|未上线|语料|游戏设定", txt), txt


def t_core_craft_integration():
    """core.craft 真接入：炼七品高阶丹会掉血并挂 danlei（低阶不挂）。"""
    from doupo import core
    p = {
        "hp": 5000, "mp": 5000, "lv": 80, "exp": 0, "gold": 0,
        "alch": 8, "flames": {"骨灵冷火": 7}, "alch_exp": 0,
        "bag": {}, "craft_count": 0, "last_craft": 0,
    }
    # 找一枚七品丹方，塞够材料
    rec = next((r for r in cfg.RECIPES if r.get("grade") == "七品" and r.get("need")), None)
    if rec is None:
        return  # 无七品丹方则跳过（不影响其他断言）
    for mat, n in rec["need"].items():
        p["bag"][mat] = n + 5
    before = p["hp"]
    res = core.craft(p, rec["name"], rng=lambda: 0.0)  # 必成功
    assert res.get("ok"), res
    assert "danlei" in res, res
    assert p["hp"] < before, "七品丹成丹应掉血"


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("t_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn()
            print("PASS", fn.__name__)
        except Exception as e:
            failed += 1
            print("FAIL", fn.__name__, repr(e))
    print(f"DANLEI_TOTAL={len(fns)} FAILED={failed}")
    sys.exit(1 if failed else 0)
