# -*- coding: utf-8 -*-
"""P2 守门：换躯重炼 + 收服印记强度 + 融魂反噬设陷阱（prove-it）。

设计依据 docs/傀儡养成系统_设计文档_2026-11-20.md §P2：
  * 换躯：五维木桶重算档位，purity/bonded/awakened/soul_strength 全继承，旧躯不返还；
  * 印记：mark_strength ≤ 玩家炼制经验 的确定性不等式，失败退金币、不静默扣；
  * 融魂：反噬 100% 发生，须「设陷阱」一步拦下，不掷骰。

自带 runner：python tests/test_doupo_puppet_p2.py
"""
from __future__ import annotations

import os
import re
import sys
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
    nb.on_regex = nb.on_command = nb.on_notice = nb.on_message = on_x
    nb.require = lambda *a, **k: (lambda f: f)
    nb.logger = types.SimpleNamespace(error=lambda *a, **k: None,
                                      warning=lambda *a, **k: None,
                                      info=lambda *a, **k: None)
    sys.modules["nonebot"] = nb
    for n in ("nonebot.adapters", "nonebot.adapters.onebot"):
        sys.modules[n] = types.ModuleType(n)
    v = types.ModuleType("nonebot.adapters.onebot.v11")
    v.MessageEvent = v.MessageSegment = v.Message = v.Bot = object
    sys.modules["nonebot.adapters.onebot.v11"] = v


_stub()
from doupo import config as cfg            # noqa: E402
from doupo import commands as cmd           # noqa: E402
from doupo import puppet_core as pc         # noqa: E402

FAILS = []
N = 0


def check(cond, msg):
    global N
    N += 1
    if not cond:
        FAILS.append(msg)


# --- P2 换躯 ---
def test_reforge_recomputes_tier_and_keeps_progress():
    """换躯：tier 按新躯体木桶重算，purity/bonded/awakened/soul 全继承。"""
    # 其他四维都高于旧躯档(1)，让躯体成为木桶唯一瓶颈 —— 换高躯必须升档
    pu = {"name": "铜尸傀", "tier": 0, "body_name": "强者躯体",
          "body_realm": "斗皇", "body_star": 0,
          "core_tier": 9, "soul_source": "斗尊强者之魂", "metal_name": "暗金玄铁",
          "fire_tier": 8, "purity": 55, "bonded": True, "awakened": True,
          "soul_strength": 2400, "damaged": True}
    out = pc.reforge_body(pu, "斗宗强者之躯")
    check(bool(out), "换躯返回空 —— 真名躯体应可换")
    check(out["body_name"] == "斗宗强者之躯" and out["body_realm"] == "斗宗",
          "躯体境界未更新：%s/%s" % (out["body_name"], out["body_realm"]))
    check(out["body_star"] == 7, "斗宗强者是七星，实际 %s" % out["body_star"])
    # 存量继承（U11）
    for k in ("purity", "bonded", "awakened", "soul_strength", "damaged",
              "core_tier", "soul_source", "metal_name", "fire_tier"):
        check(out[k] == pu[k], "换躯丢字段 %s：%s≠%s" % (k, out[k], pu[k]))
    # 档位按木桶重算：躯体是唯一瓶颈，换斗宗躯后必须实打实升一档
    check(out["tier"] > pu["tier"],
          "换更高躯后档位没升（木桶未重算）：%d→%d" % (pu["tier"], out["tier"]))
    check(out.get("tier_name") == cfg.PUPPET_TIER_BY_IDX[out["tier"]][0],
          "tier_name 未随新档同步")
    # 旧躯不返还：原 dict 未被就地改
    check(pu["body_name"] == "强者躯体", "reforge_body 就地改了传入 dict")


def test_reforge_rejects_illegal_body():
    """非法/空躯体名返回空，命令层据此报错，绝不静默换失败。"""
    pu = {"tier": 0, "body_name": "强者躯体"}
    check(pc.reforge_body(pu, "不存在之躯") == {}, "非法躯体名未拒绝")
    check(pc.reforge_body(pu, "") == {}, "空躯体名未拒绝")


def test_reforge_command_deducts_material():
    """命令层换躯真扣材料、真入库（换躯是唯一消耗点）。"""
    import asyncio
    import tempfile
    import importlib
    psm = importlib.import_module("doupo.puppet_store")
    smod = importlib.import_module("doupo.store")
    from doupo import core

    async def go():
        tmp = tempfile.mkdtemp(prefix="p2_reforge_")
        store = smod.Store(os.path.join(tmp, "d.json"))
        await store.load()
        db = psm.PuppetStore(os.path.join(tmp, "p.sqlite"))
        qq = "p2ref"
        p = core.create_player(qq, "换躯测试", "20001")
        await store.put_player(p)
        await db.set_lore(qq, True)
        pid = await db.add_puppet(qq, {
            "name": "铜尸傀", "tier": 0, "body_name": "强者躯体",
            "body_realm": "斗皇", "body_star": 0, "core_tier": 7,
            "soul_source": "斗宗强者之魂", "metal_name": "精铁", "fire_tier": 7,
            "purity": 55, "bonded": True,
        })
        await db.set_active(qq, pid)
        # 缺料：不扣不换
        txt0 = await cmd._cmd_puppet_reforge(store, qq, p, await db.list_puppets(qq),
                                             await db.eligibility(qq), db, "换躯 斗宗强者之躯")
        check("缺少" in txt0, "缺躯体应报错：%s" % txt0[:60])
        check((await db.get_puppet(qq, pid))["body_name"] == "强者躯体",
              "缺料却换了躯")
        # 给料：真扣真换
        await db.add_material(qq, "斗宗强者之躯", 1)
        txt1 = await cmd._cmd_puppet_reforge(store, qq, p, await db.list_puppets(qq),
                                             await db.eligibility(qq), db, "换躯 斗宗强者之躯")
        got = await db.get_puppet(qq, pid)
        check("换躯重炼" in txt1 and got["body_name"] == "斗宗强者之躯",
              "换躯未生效：%s" % txt1[:80])
        check(int(got["purity"]) == 55, "换躯后 purity 未保留")
        check(int((await db.materials(qq)).get("斗宗强者之躯", 0)) == 0,
              "换躯未真扣躯体材料")
    asyncio.run(go())


# --- P2 印记强度 ---
def test_mark_strength_deterministic():
    """印记强度=档位+1 单调；所需经验=强度×系数；纯函数、不掷骰。"""
    vals = [pc.mark_strength({"tier": t}) for t in (0, 1, 2)]
    check(vals == [1, 2, 3], "印记强度不是 1/2/3：%s" % vals)
    check(pc.mark_exp_needed({"tier": 2}) > pc.mark_exp_needed({"tier": 0}),
          "高档残傀所需经验未更高")


def test_mark_fail_refunds_and_claim_gate():
    """命令层：印记不足失败不扣金；探知前提不足明确拒绝；成功才扣金。"""
    import asyncio
    import tempfile
    import importlib
    psm = importlib.import_module("doupo.puppet_store")
    smod = importlib.import_module("doupo.store")
    from doupo import core

    async def go():
        tmp = tempfile.mkdtemp(prefix="p2_mark_")
        store = smod.Store(os.path.join(tmp, "d.json"))
        await store.load()
        db = psm.PuppetStore(os.path.join(tmp, "p.sqlite"))
        qq = "p2mark"
        p = core.create_player(qq, "印记测试", "20001")
        p["gold"] = 10000
        p["craft_count"] = 0
        p["alch"] = cfg.PUPPET_ALCH_MIN  # 灵魂境界闸（收服前置）
        await store.put_player(p)
        wild_id = await db.add_wild_puppet({"name": "残傀", "tier": 2})

        # 未炼过 → 探不破
        t0 = await cmd._cmd_puppet_claim(store, qq, p, "收服 %d" % wild_id, db)
        check("探不破" in t0 or "亲手炼制" in t0, "未炼过却探破印记：%s" % t0[:60])
        check(int((await store.get_player(qq))["gold"]) == 10000, "探知失败却扣金")

        # 炼过但经验不足 → 抹除失败退金币
        p["craft_count"] = 1
        await store.put_player(p)
        t1 = await cmd._cmd_puppet_claim(store, qq, p, "收服 %d" % wild_id, db)
        check("抹除失败" in t1 and "未扣金币" in t1, "印记不足未如实反馈：%s" % t1[:80])
        check(int((await store.get_player(qq))["gold"]) == 10000, "抹除失败却扣金")
        # 失败时残傀仍在 wild 池（未改主）
        w = await db.get_puppet("wild", wild_id)
        check(w is not None and not w["bonded"], "失败却认主/改主了")

        # 经验足够 → 成功扣金认主
        p["craft_count"] = pc.mark_exp_needed({"tier": 2})
        await store.put_player(p)
        t2 = await cmd._cmd_puppet_claim(store, qq, p, "收服 %d" % wild_id, db)
        check("收服成功" in t2, "印记够却收服失败：%s" % t2[:80])
        check(int((await store.get_player(qq))["gold"]) == 10000 - cfg.PUPPET_CLAIM_COST,
              "收服成功未按单次扣金")
        check(bool((await db.get_puppet(qq, wild_id))["bonded"]), "成功却未认主")
    asyncio.run(go())


# --- P2 融魂反噬 ---
def test_awaken_requires_trap_no_dice():
    """融魂反噬 100% 发生，须设陷阱拦下；源码无掷骰、失败不扣料。"""
    import inspect
    src = inspect.getsource(cmd._cmd_puppet_awaken)
    check("设陷阱" in src, "融魂命令没有设陷阱步骤")
    check("random" not in src and "rng" not in src,
          "融魂引入掷骰 —— 违反「养成链全确定」")
    check("未布陷阱，未扣任何材料" in src, "未设陷阱的失败分支没说清不扣料")
    # 陷阱额外耗灵魂，且并入一次 take_materials（原子）
    check("PUPPET_AWAKEN_TRAP_SOUL" in src, "陷阱未消耗额外灵魂")


def _run():
    for fn in (test_reforge_recomputes_tier_and_keeps_progress,
               test_reforge_rejects_illegal_body, test_reforge_command_deducts_material,
               test_mark_strength_deterministic, test_mark_fail_refunds_and_claim_gate,
               test_awaken_requires_trap_no_dice):
        try:
            fn()
        except Exception as e:  # noqa: BLE001
            import traceback
            FAILS.append("%s 抛异常：%s" % (fn.__name__, e))
            traceback.print_exc()
    for m in FAILS:
        print("FAIL  " + m)
    print("=" * 70)
    print("P2_TOTAL=%d FAILED=%d" % (N, len(FAILS)))
    return 0 if not FAILS else 1


if __name__ == "__main__":
    sys.exit(_run())
