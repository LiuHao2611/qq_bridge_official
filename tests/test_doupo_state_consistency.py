# -*- coding: utf-8 -*-
"""斗破「卡片展示 ↔ 命令判定」口径一致门禁（2026-11-26 用户实测）。

用户原话：「上阵斗技 帝印决，提示未学习，可是我的斗技卡片明明有这个斗技，
是已学习……全量排查整个斗破模块，不要有这种前后矛盾的情况」。

复现探针 tmp/probe_cross_faction_loadout.py 证明：卡片序号走
``learned_no_map``（含转宗保留的他宗技），上阵判定却走 ``skill_pool``（只看本宗），
于是同一门技「卡上有序号 / 可上阵名单里有 / 点名上阵却说未学习」。

本文件守用户感受到的六件事（拒绝只查 AST / 变量名）：
  1. 卡上「帝印决」的序号能直接上阵，回包不再自相矛盾；
  2. 跨宗已学技的详情不再说「不属于你的宗门」；
  3. 上阵结果跨读档（ensure_loadout）仍在，战斗取招仍取得到；
  4. 无火玩家材料齐备的丹方在卡/文本上不再亮 ✅（兽火单方是唯一例外）；
  5. 宗门名录「可学」不再被读成「已学」，傀儡卡五道与文本同源；
  6. 转宗不再把已上阵的异火技静默踢出四槽。

运行：python tests/test_doupo_state_consistency.py（脚本式，exit 0 = 全绿）
"""
from __future__ import annotations

import asyncio
import os
import sys
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _stub_nonebot():
    nb = types.ModuleType("nonebot")
    nb.get_driver = lambda: types.SimpleNamespace(on_startup=lambda f: f)

    def on_regex(*a, **k):
        m = types.SimpleNamespace()
        m.handle = lambda *a, **k: (lambda f: f)
        m.finish = lambda *a, **k: None
        return m

    nb.on_regex = on_regex
    sys.modules["nonebot"] = nb
    for n in ("nonebot.adapters", "nonebot.adapters.onebot",
              "nonebot.adapters.onebot.v11"):
        sys.modules[n] = types.ModuleType(n)
    v11 = sys.modules["nonebot.adapters.onebot.v11"]
    v11.MessageEvent = object
    v11.MessageSegment = object
    v11.Message = object


_stub_nonebot()
for _p in (ROOT, os.path.join(ROOT, "src", "plugins")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from doupo import card, commands, config as cfg, core, skills  # noqa: E402

_RES: list[tuple[str, bool, str]] = []


def check(name, ok, detail=""):
    _RES.append((name, bool(ok), str(detail)))


class MemStore:
    def __init__(self, p):
        self.p = p

    async def get_player(self, qq):
        return self.p

    async def put_player(self, p):
        self.p = p


def _cross_faction_p():
    """云岚宗玩家，转宗前学过帝印决（他宗技）——用户报告的最小复现。"""
    p = core.create_player("DCONS", "一致性", "群")
    p["lv"] = 70
    p["faction"] = "yunlanzong"
    p["faction_name"] = "云岚宗"
    p["learned"] = ["帝印决", "玄岩盾"]
    p["loadout"] = []
    core._refill(p)
    return p


def test_card_number_loads_cross_faction_skill():
    p = _cross_faction_p()
    store = MemStore(p)
    no_map = core.learned_no_map(p)
    no = core.learned_no_of(p, "帝印决")
    check("跨宗已学技在斗技卡有连续序号", no is not None and no in no_map,
          "no=%s map=%s" % (no, no_map))
    rows = card.build_learned_data(p)["rows"]
    check("斗技卡确实列出帝印决", any(r["name"] == "帝印决" and r.get("no") == no
                                    for r in rows), str([r["name"] for r in rows]))

    out = asyncio.run(commands.cmd_loadout(store, "DCONS", str(no)))
    got = [e["name"] for e in core.loadout_entries(store.p)]
    check("按卡上序号上阵成功", "帝印决" in got, "回包=%s 实得=%s" % (out[:120], got))
    check("回包不再自相矛盾", "不可上阵" not in out, out[:160])

    # 按技名（含用户写的「诀/决」别名）也要能上
    out2 = asyncio.run(commands.cmd_loadout(MemStore(_cross_faction_p()),
                                            "DCONS", "帝印诀"))
    check("按技名上阵成功", "已上阵" in out2 and "帝印决" in out2, out2[:160])


def test_cross_faction_detail_not_rejected():
    p = _cross_faction_p()
    out = asyncio.run(commands.cmd_skill_info(MemStore(p), "DCONS", "帝印决"))
    check("跨宗已学技能看详情", "帝印决" in out and "出处：" in out, out[:160])
    check("详情不再说不属于本宗", "不属于你的宗门" not in out, out[:160])
    check("详情不再把已领悟写成可学", "可学" not in out, out[:160])


def test_loadout_survives_reload_and_battle_pick():
    p = _cross_faction_p()
    store = MemStore(p)
    asyncio.run(commands.cmd_loadout(store, "DCONS", "帝印决 玄岩盾"))
    # 模拟读档（读出来的就是同一份 dict，经 ensure_loadout 规范化）
    after = skills.ensure_loadout(store.p)
    check("读档后四槽仍保留跨宗技", "帝印决" in after, str(after))
    picked = [e["name"] for e in core._loadout_entries(store.p)]
    check("战斗取招仍取得到跨宗技", "帝印决" in picked, str(picked))


def test_no_flame_card_and_text_do_not_claim_ready():
    p = core.create_player("DNOFIRE", "无火", "群")
    p["lv"] = 70
    p["flames"] = {}
    p["alch"] = len(cfg.ALCHEMIST) - 1          # 所有丹方都可见
    materials = set()
    for r in cfg.RECIPES:
        materials.update((r.get("need") or {}).keys())
    p["bag"] = {name: 99 for name in materials}
    normal = next((r for r in cfg.RECIPES
                   if r.get("need") and r.get("effect") != "beast_flame"), None)
    beast = next((r for r in cfg.RECIPES
                  if str(r.get("effect") or "") == "beast_flame"), None)
    assert normal and beast, "夹具需要普通丹方与兽火单方各一枚"

    check("普通丹无火闸命中", core.craft_flame_gate(p, normal) == "nofire",
          str(core.craft_flame_gate(p, normal)))
    check("兽火单方是引火例外", core.craft_flame_gate(p, beast) == "",
          str(core.craft_flame_gate(p, beast)))
    check("兽火单方成功率走引火通道", core.recipe_rate(p, beast)[0] == cfg.BEAST_SEAL_RATE,
          str(core.recipe_rate(p, beast)))

    # 卡面：普通丹不能亮「可炼」，兽火单方仍可炼
    found = {}
    for pg in range(1, 40):
        d = card.build_recipes_data(p, pg)
        for g in d["groups"]:
            for row in g["rows"]:
                if row["name"] in (normal["name"], beast["name"]):
                    found[row["name"]] = row
        if len(found) == 2:
            break
    check("卡面普通丹不亮可炼", found.get(normal["name"], {}).get("ready") is False,
          str(found.get(normal["name"])))
    check("卡面普通丹标记无火", found.get(normal["name"], {}).get("gate") == "nofire",
          str(found.get(normal["name"])))
    check("卡面兽火单方仍可炼", found.get(beast["name"], {}).get("ready") is True,
          str(found.get(beast["name"])))

    # 文本：同一闸口，不能只给一个光秃秃的 ✅
    out = asyncio.run(commands.cmd_recipes(MemStore(p), "DNOFIRE", 1))
    check("文本仍展示丹方清单", "炼药" in out, out[:100])

    # 实炼：卡说不可炼，命令也必须真拒绝（同一真源）
    res = core.craft(p, normal["name"], rng=lambda: 0.0, now_ts=0)
    check("实炼同样报 nofire", res.get("reason") == "nofire", str(res))


def test_roster_and_puppet_card_do_not_claim_learned():
    p = _cross_faction_p()
    roster = card.build_roster_data(p)
    states = {r["state"] for g in roster["groups"] for r in g["rows"]}
    check("宗门名录不再用会被读成已学的「可学」", "可学" not in states, str(states))
    check("宗门名录明确写未领悟", "未领悟" in states, str(states))
    check("宗门名录提示怎么领悟", "学习" in roster.get("tip", ""),
          str(roster.get("tip")))

    pup = card.build_puppet_data([], {}, p)
    names = [g["name"] for g in pup["gates"]]
    check("傀儡卡补齐第五道斗气", names == ["境界", "灵魂境界", "控火", "异火", "斗气"],
          str(names))
    from doupo import puppet_core as pc
    missing = set(pc.puppet_gate(p, 0)["missing"])
    shown_missing = {g["name"] for g in pup["gates"] if not g["ok"]}
    check("傀儡卡未过资格与判定同源", missing == shown_missing,
          "判定=%s 卡面=%s" % (sorted(missing), sorted(shown_missing)))


def test_switch_keeps_flame_skill_in_loadout():
    p = core.create_player("DSWITCH", "转宗", "群")
    p["lv"] = 45
    p["gold"] = 500000
    p["flames"] = {"青莲地心火": 10}
    flame_names = skills.flame_skill_names(p)
    assert flame_names, "夹具需要一朵带主动技的异火"
    flame_skill = next(iter(flame_names))
    p["loadout"] = [flame_skill]
    res = core.change_faction(p, "迦南学院")
    check("转宗成功", res.get("ok") is True, str(res))
    check("异火技不随转宗被踢出四槽", flame_skill in (p.get("loadout") or []),
          "flame=%s loadout=%s" % (flame_skill, p.get("loadout")))
    check("转宗回执与读档一致", res.get("loadout") == list(p.get("loadout") or []),
          "回执=%s 读档=%s" % (res.get("loadout"), p.get("loadout")))


def main():
    for fn in (test_card_number_loads_cross_faction_skill,
               test_cross_faction_detail_not_rejected,
               test_loadout_survives_reload_and_battle_pick,
               test_no_flame_card_and_text_do_not_claim_ready,
               test_roster_and_puppet_card_do_not_claim_learned,
               test_switch_keeps_flame_skill_in_loadout):
        try:
            fn()
        except Exception as exc:                      # 测试自身异常也算红
            check(fn.__name__ + " 执行", False, repr(exc))
    bad = [x for x in _RES if not x[1]]
    for name, ok, detail in _RES:
        print(("  OK  " if ok else "  FAIL") + " " + name + (("  | " + detail) if detail and not ok else ""))
    print("\n通过 %d，失败 %d" % (len(_RES) - len(bad), len(bad)))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
