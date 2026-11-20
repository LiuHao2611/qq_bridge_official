# -*- coding: utf-8 -*-
"""自选材料炼制守门（2026-11-20 第九轮）。

用户诉求：「手上有很多材料时，需要选择材料进行炼制」。

要守住的不变量：
  * 自定只是「造一条临时配方」，判档仍走 recipe_tier、耗材仍走 recipe_materials、
    落库仍走 make_puppet —— 重开分支最容易把这三条写漏；
  * 自选四维全在分档表内，错名要如实报错，不许静默回落人级；
  * 火不耗材、不自选（按玩家实况取），否则会出现「占位 B 火误报降档」；
  * 门禁先于耗材：境界不足时先报境界，不能被「材料不足」盖住；
  * 材料不足点名缺什么、去哪取；固定配方的旧路径逐字节不变。

自带 runner：python tests/test_doupo_puppet_custom.py
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLUGINS = os.path.join(ROOT, "src", "plugins")
for _p in (ROOT, PLUGINS):
    if _p not in sys.path:
        sys.path.insert(0, _p)

os.environ["DOUPO_CHANCE_RATE"] = "0"


def _stub():
    nb = types.ModuleType("nonebot")
    nb.get_driver = lambda: types.SimpleNamespace(on_startup=lambda f: f)

    def on_x(*a, **k):
        m = types.SimpleNamespace()
        m.handle = lambda *a, **k: (lambda f: f)
        m.finish = lambda *a, **k: None
        return m
    nb.on_regex = on_x
    nb.on_command = on_x
    nb.on_notice = on_x
    nb.on_message = on_x
    nb.require = lambda *a, **k: (lambda f: f)
    nb.logger = types.SimpleNamespace(error=lambda *a, **k: None,
                                      warning=lambda *a, **k: None,
                                      info=lambda *a, **k: None)
    sys.modules["nonebot"] = nb
    for name in ("nonebot.adapters", "nonebot.adapters.onebot"):
        sys.modules[name] = types.ModuleType(name)
    v11 = types.ModuleType("nonebot.adapters.onebot.v11")
    v11.MessageEvent = object
    v11.MessageSegment = object
    v11.Message = object
    v11.Bot = object
    sys.modules["nonebot.adapters.onebot.v11"] = v11


_stub()

from doupo import config as cfg  # noqa: E402
from doupo import core  # noqa: E402
from doupo import commands  # noqa: E402
from doupo import puppet_core as pc  # noqa: E402
import importlib  # noqa: E402

ps_mod = importlib.import_module("doupo.puppet_store")
store_mod = importlib.import_module("doupo.store")

_FAILS = []
_N = 0


def check(cond, msg):
    global _N
    _N += 1
    if not cond:
        _FAILS.append(msg)


def test_parser():
    # 帮助分支
    rec, err = pc.custom_recipe_from_arg("自定")
    check(rec is None and "自定炼制：" in err, "「自定」应给帮助：%s" % err[:60])
    for kind in ("躯体", "魔核", "灵魂", "金属"):
        check(kind in err, "帮助里缺 %s 的候选清单" % kind)
    rec, err = pc.custom_recipe_from_arg("自定?")
    check(rec is None and "自定炼制：" in err, "「自定?」也应给帮助")
    # 带空格的问号走的是另一条分支（parts[0] in ("?", "？", "帮助")），
    # 删掉那条判断后这里会退化成「参数个数错」，所以必须单独钉。
    for q in ("自定 ?", "自定 ？", "自定 帮助"):
        rec, err = pc.custom_recipe_from_arg(q)
        check(rec is None and "自定炼制：" in err,
              "「%s」应给帮助而不是报错：%s" % (q, err[:40]))

    # 四维正确
    rec, err = pc.custom_recipe_from_arg(
        "自定 斗宗强者之躯 七阶魔核 斗宗强者之魂 寒铁",
        {"flames": {"青莲地心火": 4}})       # A 火，与实战一致
    check(rec is not None and not err, "正确四维应解析成功：%s" % err)
    if rec:
        check(rec["custom"] is True, "自定配方要打 custom 标记（命令层据此跳过标称火）")
        check(pc.recipe_tier(pc.recipe_slots(rec)) == 1,
              "斗宗躯+七阶核+斗宗魂+寒铁应判地级（木桶 min−1）")
        cost = pc.recipe_materials(rec)
        check(len(cost) == 4 and "火" not in str(cost),
              "四维耗材应恰好 4 样、火不耗材：%s" % cost)

    # 错名/数量不对要报错，不许静默回落
    for bad in ("自定 不存在躯 七阶魔核 斗宗强者之魂 寒铁",
                "自定 强者躯体 七阶魔核 寒铁",
                "自定 强者躯体 七阶魔核 无主灵魂 寒铁 多余"):
        r, e = pc.custom_recipe_from_arg(bad)
        check(r is None and bool(e), "非法自定参数应报错，实得 %s / %s" % (r, e))
    # 错名要点出问题材料，玩家才知道改哪个
    r, e = pc.custom_recipe_from_arg("自定 不存在躯 七阶魔核 无主灵魂 精铁")
    check("不存在躯" in e and "躯体" in e, "错名提示应点出材料与维度：%s" % e)


def test_no_shortcut_through_tier():
    """自定不能绕过木桶判档：任一维低就降档，缺维就不能开炉。"""
    low = pc.custom_recipe_from_arg("自定 斗尊强者之躯 火属性魔核 斗尊强者之魂 精铁")[0]
    check(low is not None and pc.recipe_tier(pc.recipe_slots(low)) == 0,
          "魔核只有 4 阶时应把斗尊躯压成人级（木桶），实得 %s"
          % (pc.recipe_tier(pc.recipe_slots(low)) if low else None))
    # 复用固定配方的四维，判档必须与那条配方一致
    fixed = cfg.PUPPET_RECIPE_BY_NAME["银煞傀"]
    a_fire_p = {"flames": {"青莲地心火": 4}}
    custom = pc.custom_recipe_from_arg("自定 %s %s %s %s" % (
        fixed["body"], fixed["core"], fixed["soul"], fixed["metal"]), a_fire_p)[0]
    check(custom is not None and
          pc.recipe_tier(pc.recipe_slots(custom)) ==
          pc.recipe_tier(pc.recipe_slots(fixed, a_fire_p)),
          "同料自定与固定配方判档必须一致（唯一 recipe_tier 口径）：%s vs %s"
          % (pc.recipe_tier(pc.recipe_slots(custom)) if custom else None,
             pc.recipe_tier(pc.recipe_slots(fixed, a_fire_p))))


async def e2e():
    tmp = tempfile.mkdtemp(prefix="pup_custom_")
    store = store_mod.Store(os.path.join(tmp, "doupo.json"))
    await store.load()
    db = ps_mod.PuppetStore(os.path.join(tmp, "puppet.sqlite"))
    qq = "10077"
    p = core.create_player(qq, "自选炼制测试", "20077")
    p["lv"] = 64                       # 斗宗，够地级
    p["alch"] = cfg.PUPPET_ALCH_MIN
    p["craft_count"] = cfg.PUPPET_CRAFT_COUNT_MIN
    p["high_grade_crafts"] = cfg.PUPPET_HIGH_GRADE_MIN
    p["flames"] = {"青莲地心火": 4}     # 异火闸 + A 火
    p["gold"] = 200000
    p["mp"] = core.mp_max(p)
    await store.put_player(p)
    await db.set_lore(qq, True)
    for n in cfg.PUPPET_MATERIALS:
        await db.add_material(qq, n, 3)

    picks = ("斗宗强者之躯", "七阶魔核", "斗宗强者之魂", "寒铁")
    txt = await commands.cmd_craft_puppet(
        store, qq, db, "自定 %s %s %s %s" % picks)
    check("炼制成功" in txt, "自定四维开炉失败：%s" % txt[:180])
    check("降为" not in txt,
          "自定配方不该拿占位 B 火误报降档：%s" % txt[:180])
    roster = await db.list_puppets(qq)
    check(len(roster) == 1, "自定开炉应产出一具傀儡，实得 %d" % len(roster))
    if roster:
        row = roster[0]
        check(row["body_name"] == picks[0] and row["soul_source"] == picks[2]
              and row["metal_name"] == picks[3],
              "落库材料与自选不符：%s" % str(row)[:220])
        check(int(row["tier"]) == 1, "自选四维应判地级，实得 %d" % int(row["tier"]))
        # 自选四维恰好等于「银煞傀」的用料时，反查命中它是**正确**行为
        # （原著逻辑：料相同就是同一具傀儡）。这里只要求反查能给出名字，
        # 不是固定配方表里没有的组合。
        check(pc.match_recipe(row) is not None,
              "自选组合的用料反查不该失败（卡面_output 需要能显示来源）")

    for n in picks:
        check(await db.material_qty(qq, n) == 2,
              "自选材料 %s 应扣 1 剩 2" % n)
    # 火不耗材
    check(await db.material_qty(qq, "焰陨玄铁") == 3,
          "没选的金属不该被扣")

    # 缺料：点名 + 获取途径
    for n in cfg.PUPPET_MATERIALS:
        have = await db.material_qty(qq, n)
        if have:
            await db.take_materials(qq, {n: have})
    p2 = await store.get_player(qq)
    p2["mp"] = core.mp_max(p2)
    await store.put_player(p2)
    lack = await commands.cmd_craft_puppet(
        store, qq, db, "自定 %s %s %s %s" % picks)
    check("材料不足" in lack and "寒铁" in lack and "傀儡遗迹" in lack,
          "自定缺料也应点名+给获取途径：%s" % lack[:180])

    # 门禁先于耗材：境界不够时先报境界，不能被材料不足盖住
    p3 = await store.get_player(qq)
    p3["lv"] = 10
    await store.put_player(p3)
    gated = await commands.cmd_craft_puppet(
        store, qq, db, "自定 %s %s %s %s" % picks)
    check("境界" in gated, "境界不足应优先报境界：%s" % gated[:180])

    # 固定配方路径没被自定分支碰坏
    p4 = await store.get_player(qq)
    p4["lv"] = 64
    p4["mp"] = core.mp_max(p4)
    await store.put_player(p4)
    for n in cfg.PUPPET_MATERIALS:
        await db.add_material(qq, n, 3)
    old = await commands.cmd_craft_puppet(store, qq, db, "银煞傀")
    check("炼制成功" in old, "固定配方旧路径被自定分支弄坏了：%s" % old[:180])


def main():
    test_parser()
    test_no_shortcut_through_tier()
    asyncio.run(e2e())
    for m in _FAILS:
        print("FAIL  " + m)
    print("=" * 66)
    print("CUSTOM_TOTAL=%d FAILED=%d" % (_N, len(_FAILS)))
    return 0 if not _FAILS else 1


if __name__ == "__main__":
    sys.exit(main())
