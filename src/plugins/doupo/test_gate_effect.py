# -*- coding: utf-8 -*-
r"""斗技 `gate` 真落地守门（E3/Q5，2026-11-27）。

报告 E3：图鉴里大量 `gate=` 限制文案在战斗里一律不判定。按拍板「核心技
落地、考据型降级 flavor」，只把木灵缚这类藤蔓控制的明文限制做成 effect
条件：石板地 / 沙丘 / 水面抽不出藤，遇火属性斗气被烧断。其余门控文案保持
卡面 flavor，不改战斗。

运行：
    python src/plugins/doupo/test_gate_effect.py
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

from doupo import skill_effects as fx  # noqa: E402


VINE = [{"t": "root", "d": 1, "gate": "vine"}]


def _hit(effects, ctx, rng_value=0.0):
    target = fx.new_state()
    r = fx.on_hit(effects, fx.new_state(), target, 100,
                  lambda: rng_value, ctx or {})
    return target, r


def test_region_to_terrain_mapping_follows_card_text():
    """死地关键词只取卡面明文三类，且不误伤有土的后山 / 山脉。"""
    assert fx.terrain_of_region("塔戈尔大沙漠") == "沙丘"
    assert fx.terrain_of_region("蛇人禁地·石漠城") == "沙丘"
    assert fx.terrain_of_region("兽族遗迹") == "石板地"
    assert fx.terrain_of_region("天墓") == "石板地"
    assert fx.terrain_of_region("帝都·墨家") == "石板地"
    assert fx.terrain_of_region("乌坦城·萧家后山") == "沃土"
    assert fx.terrain_of_region("魔兽山脉深处") == "沃土"
    assert fx.terrain_of_region("") == "沃土"


def test_vine_cannot_root_on_dead_terrain():
    for terrain in sorted(fx.VINE_DEAD_TERRAIN):
        target, r = _hit(VINE, {"terrain": terrain})
        assert int(target.get("root") or 0) == 0, "%s 上藤还生出来了" % terrain
        assert "藤无从生" in r["notes"], "%s 没播报门控原因" % terrain


def test_vine_burns_on_fire_target():
    target, r = _hit(VINE, {"terrain": "沃土", "target_fire": True})
    assert int(target.get("root") or 0) == 0, "遇火属性没烧断藤"
    assert "藤被烧断" in r["notes"]


def test_vine_works_on_fertile_ground():
    target, r = _hit(VINE, {"terrain": "沃土"}, rng_value=fx.ROOT_BASE_P - 0.01)
    assert int(target.get("root") or 0) >= 1, "普通地面藤反而不能定身"
    assert "定身" in r["notes"] and "藤无从生" not in r["notes"]


def test_other_root_skills_ignore_terrain():
    """没写 gate 的定身技口径完全不变——只约束藤蔓类。"""
    plain = [{"t": "root", "d": 1}]
    target, r = _hit(plain, {"terrain": "沙丘", "target_fire": True},
                     rng_value=fx.ROOT_BASE_P - 0.01)
    assert int(target.get("root") or 0) >= 1, "地形门控误伤了普通定身技"
    assert "定身" in r["notes"]


def test_book_data_drives_gate_from_one_source():
    """卡面 gate 文案与 effect 条件同源：木灵缚数据必须带 gate='vine'。"""
    from doupo import skill_book_data as d

    rows = [r for r in getattr(d, "BOOK_ROWS", ()) if r.get("name") == "木灵缚"]
    assert len(rows) == 1, "木灵缚图鉴条目不唯一"
    eff = rows[0].get("effects") or []
    assert any(e.get("t") == "root" and e.get("gate") == "vine" for e in eff), (
        "图鉴数据没给木灵缚的 root 挂 vine 门控")
    assert "石板地" in str(rows[0].get("gate") or "") and "火属性" in str(rows[0].get("gate") or "")


def test_fight_passes_terrain_and_fire_into_ctx():
    import doupo.battle_core as bc

    src = inspect.getsource(bc.fight)
    assert '"terrain": str(mon.get("terrain") or "")' in src, "fight 没传地形"
    assert '"target_fire": fx.monster_is_fire(mon)' in src, "fight 没传目标属性"


def test_encounter_attaches_terrain_from_chapter_region():
    from doupo import core

    src = inspect.getsource(core.encounter)
    assert "fx.terrain_of_region" in src and 'mon["terrain"] = _terrain' in src, (
        "章节遭遇战没按地域给怪挂地形")
    # 野外火属性怪要在 ctx 里被识别（名字命中即可，不依赖额外数据字段）
    assert fx.monster_is_fire({"name": "赤炎狼"})
    assert fx.monster_is_fire({"name": "双头火灵蛇"})
    assert not fx.monster_is_fire({"name": "嗜血鼠"})


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
