# -*- coding: utf-8 -*-
"""战斗深度验收（零依赖、exit-code 可验证）——属性与战斗强关联（2026-11-16）。

覆盖用户三条硬要求：
  1. 机缘与战斗强关联：极低概率「贵人相助」一招制敌（可注入 rng、有硬顶）。
  2. 战报内联属性因果：每条行动都点明起作用的力量/敏捷/斗气/防御/机缘。
  3. 人物卡六维标注战斗作用，且与 core/config 同源（图文战斗口径一致）。

prove-it：反例注入（把 aid 从 fight 移除 / 战报去掉归因）必须让对应断言 FAIL。
"""
from __future__ import annotations

import asyncio
import os
import sys
import types

PLUGINS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO_ROOT = os.path.dirname(os.path.dirname(PLUGINS_DIR))
for _p in (REPO_ROOT, PLUGINS_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)
os.environ["DOUPO_CHANCE_RATE"] = "0"


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

from doupo import card, commands, config as cfg, core  # noqa: E402

_PASSED = 0
_FAILED = 0


def check(name, cond, detail=""):
    global _PASSED, _FAILED
    if cond:
        _PASSED += 1
        print("PASS  " + name)
    else:
        _FAILED += 1
        print("FAIL  %s  ->  %s" % (name, detail))


class MemStore:
    def __init__(self, p):
        self.p = p

    async def get_player(self, qq):
        return self.p

    async def put_player(self, p):
        self.p = p


def _p(lv=10, luck=1):
    p = core.create_player("depth-test", "测试", "g1")
    p["lv"] = lv
    p["luck"] = luck
    p["hp"] = core.stats(p)["体力"]
    p["learned"] = [e["name"] for e in core.skill_pool(p)]
    p["loadout"] = list(p["learned"])[:cfg.LOADOUT_SIZE]
    return p


def _mon(p):
    return core.monster_scaled(p, cfg.MONSTERS[1])


# ---------------------------------------------------------------------------
# 1. 机缘 → 贵人相助概率
# ---------------------------------------------------------------------------
def test_aid_rate_formula():
    check("机缘0=基础概率", abs(core.hero_aid_rate({"luck": 0}) - cfg.HERO_AID_BASE_RATE) < 1e-9,
          str(core.hero_aid_rate({"luck": 0})))
    r100 = core.hero_aid_rate({"luck": 100})
    check("机缘100略高于基础", cfg.HERO_AID_BASE_RATE < r100 < cfg.HERO_AID_MAX_RATE, str(r100))
    rmax = core.hero_aid_rate({"luck": 999999})
    check("概率硬顶=MAX_RATE", abs(rmax - cfg.HERO_AID_MAX_RATE) < 1e-9, str(rmax))
    check("概率随机缘单调递增",
          core.hero_aid_rate({"luck": 1}) < core.hero_aid_rate({"luck": 50})
          < core.hero_aid_rate({"luck": 500}), "monotonic")


def test_aid_not_triggered_at_default():
    # rng 恒返回 0.5 >> 硬顶 2%，绝不该触发——保护 TTK 平衡与既有测试序列
    p = _p(luck=9999)
    aid = core.roll_hero_aid(p, rng=lambda: 0.5)
    check("rng=0.5 不触发贵人", aid is None, str(aid))
    res = core.fight(p, _mon(p), rng=lambda: 0.5)
    check("常规战斗无 aid 标记", res.get("hero_aid") is None, str(res.get("hero_aid")))
    check("常规战斗 log 无贵人相助条目",
          not any(it.get("aid") for it in res["log"]), str(res["log"][:2]))


def test_aid_trigger_one_hit_win():
    # rng 恒 0 → 必触发：一招制敌、毫发无伤、正常结算、带机缘标记
    p = _p(luck=500)
    hp_before = int(p["hp"])
    gold_before = int(p["gold"])
    res = core.fight(p, _mon(p), rng=lambda: 0.0)
    check("贵人相助必触发", res.get("hero_aid") is not None, str(res.get("hero_aid")))
    check("贵人一招制敌(胜)", res["win"] is True, str(res["win"]))
    check("贵人回合数=1", res["rounds"] == 1, str(res["rounds"]))
    check("贵人毫发无伤", res["taken"] == 0, str(res["taken"]))
    check("贵人后 hp 不减", p["hp"] >= hp_before, f"{p['hp']} vs {hp_before}")
    check("贵人仍正常结算金币", p["gold"] > gold_before, f"{p['gold']} vs {gold_before}")
    check("贵人战报带 aid 条目",
          any(it.get("aid") and it.get("source") == "机缘" for it in res["log"]),
          str(res["log"]))
    check("贵人名字来自称号池",
          res["hero_aid"]["name"] in cfg.HERO_AID_NAMES, str(res["hero_aid"]))


# ---------------------------------------------------------------------------
# 2. 战报内联属性因果
# ---------------------------------------------------------------------------
def _line(*a, **kw):
    return commands._battle_line(*a, **kw)


def test_battle_line_attribute_causality():
    check("技能出手→力量斗气",
          "力量" in _line({"kind": "裂爪击", "dmg": 100, "skill": True})
          and "斗气" in _line({"kind": "裂爪击", "dmg": 100, "skill": True}),
          _line({"kind": "裂爪击", "dmg": 100, "skill": True}))
    check("普攻→力量武器",
          "武器" in _line({"kind": "普攻", "dmg": 50, "skill": False}),
          _line({"kind": "普攻", "dmg": 50, "skill": False}))
    check("暴击→敏捷",
          "敏捷" in _line({"kind": "裂爪击", "dmg": 200, "skill": True, "crit": True}),
          _line({"kind": "裂爪击", "dmg": 200, "skill": True, "crit": True}))
    check("闪避→敏捷",
          "敏捷" in _line({"kind": "闪避", "dmg": 0, "miss": True}),
          _line({"kind": "闪避", "dmg": 0, "miss": True}))
    check("兽击→防御减伤",
          "防御" in _line({"kind": "兽击", "dmg": 30, "taken": True}),
          _line({"kind": "兽击", "dmg": 30, "taken": True}))
    check("持续伤害标斗技",
          "持续" in _line({"kind": "持续伤害", "dmg": 20, "dot": True}),
          _line({"kind": "持续伤害", "dmg": 20, "dot": True}))
    check("反伤标斗技",
          "反伤" in _line({"kind": "反伤", "dmg": 15, "reflect": True}),
          _line({"kind": "反伤", "dmg": 15, "reflect": True}))
    check("贵人相助标机缘",
          "机缘" in _line({"kind": "贵人相助", "dmg": 999, "aid": True,
                         "helper": "无名斗皇"}),
          _line({"kind": "贵人相助", "dmg": 999, "aid": True, "helper": "无名斗皇"}))
    # 特殊条目字段不全不得 KeyError（回归 2026-11-16 战斗回包崩溃）
    try:
        _line({"kind": "持续伤害", "dmg": 5, "dot": True})
        _line({"kind": "反伤", "dmg": 5, "reflect": True})
        check("特殊条目无 KeyError", True)
    except Exception as e:  # noqa: BLE001
        check("特殊条目无 KeyError", False, repr(e))


def test_cmd_fight_shows_causality():
    p = _p(luck=1)
    core.fight(p, _mon(p), rng=lambda: 0.5)  # 预热确保存档结构
    out = asyncio.run(commands.cmd_fight(MemStore(p), "depth-test"))
    check("实战战报含属性归因(力量/敏捷/防御任一)",
          any(k in out for k in ("力量", "敏捷", "防御", "斗气")), out[:200])
    check("实战战报含遭遇", "遭遇" in out, out[:80])


def test_cmd_fight_aid_text():
    p = _p(luck=500)
    # encounter 对每个敌人各掷一次机缘，luck=500 在多敌场有累积触发概率；
    # 本用例只验「未触发时文案层不出现贵人字样」，故注入确定性关闭机缘，
    # 消除随机抖动（概率/硬顶由 test_aid_* 系列覆盖，不在这里重复）。
    saved = core.roll_hero_aid
    try:
        core.roll_hero_aid = lambda *_a, **_kw: None
        out = asyncio.run(commands.cmd_fight(MemStore(p), "depth-test"))
    finally:
        core.roll_hero_aid = saved
    check("普通战斗文案无贵人误导", "贵人相助" not in out, out[:160])


# ---------------------------------------------------------------------------
# 3. 人物卡与战斗口径同源
# ---------------------------------------------------------------------------
def test_panel_and_battle_same_source():
    d = card.build_data(_p())
    check("人物卡带 stat_roles", d.get("stat_roles") == dict(cfg.STAT_BATTLE_ROLE),
          str(d.get("stat_roles")))
    check("六维都有战斗作用标注",
          all(cfg.STAT_BATTLE_ROLE.get(k) for k in cfg.PANEL_ORDER), str(cfg.STAT_BATTLE_ROLE))
    check("机缘战斗作用含贵人",
          "贵人" in cfg.STAT_BATTLE_ROLE["机缘"], cfg.STAT_BATTLE_ROLE["机缘"])


def test_statcell_label_role_no_overlap():
    """几何守门：属性名与「→战斗作用」同排且不得重叠/越出格子。

    _statcell 左侧标签 x+20、右侧短标 x+w-20 右锚，可用宽 = w-40。
    用模板同一套 text_width 度量，避免以后改战斗作用文案把格子撑破。
    """
    import importlib
    pic = card._load_pic()
    check("pic 绘制包可加载", pic is not None, str(pic))
    if pic is None:
        return
    tpl = importlib.import_module(card._PKG_NAME + ".templates.doupo")
    content_w = card.CARD_WIDTH - tpl.PAD * 2
    cell_w = (content_w - tpl.GAP * 2) // 3
    inner = cell_w - 20 - 20
    for lab in cfg.PANEL_ORDER:
        role = "→" + str(cfg.STAT_BATTLE_ROLE.get(lab) or "")
        lw = tpl._w(lab, tpl.FS_TINY, "regular")
        rw = tpl._w(role, tpl.FS_TINY, "regular")
        check("六维排版不重叠:%s" % lab, lw + rw <= inner,
              "label=%d role=%d inner=%d" % (lw, rw, inner))


def _run():
    fns = [(k, v) for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    for _name, fn in fns:
        fn()
    print("=" * 70)
    print("BATTLE_DEPTH_TOTAL=%d PASSED=%d FAILED=%d"
          % (_PASSED + _FAILED, _PASSED, _FAILED))
    return 0 if _FAILED == 0 else 1


if __name__ == "__main__":
    sys.exit(_run())
