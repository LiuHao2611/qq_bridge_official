# -*- coding: utf-8 -*-
"""遗迹自动连打守门（2026-11-20 第九轮）。

用户诉求：「傀儡遗迹，一次一次打太慢了，改成自动往下打，力竭为止」。

要守住的不变量：
  * 一次调用能连过多段（旧实现一次只打一段，水位线只 +1）；
  * 战败即停、停在原段、不继续往下打；
  * 每段材料只发一次，累计 == 各段 drops 之和（连打不能重复领奖）；
  * 战斗消耗 hp/mp 跨场结转，最后一次性落库；
  * report.runs 一段一条，卡片并进同一张，一处输出一条消息；
  * 已全通再调用不发料、不再打。

自带 runner：python tests/test_doupo_puppet_ruin_auto.py
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

from datetime import date  # noqa: E402
from doupo import config as cfg  # noqa: E402
from doupo import core  # noqa: E402
from doupo import commands  # noqa: E402
from doupo import card  # noqa: E402
from doupo import puppet_core as pc  # noqa: E402
import importlib  # noqa: E402

ps_mod = importlib.import_module("doupo.puppet_store")
store_mod = importlib.import_module("doupo.store")

_FAILS = []
_N = 0
DAY = date.today().isoformat()


def check(cond, msg):
    global _N
    _N += 1
    if not cond:
        _FAILS.append(msg)


async def _make_strong(store, db, qq, lv=96):
    p = core.create_player(qq, "连打测试", "3" + str(qq))
    p["lv"] = lv
    p["gold"] = 999999
    p["flames"] = {"帝炎": 10}
    p["diyan"] = 1
    core._refill(p)
    await store.put_player(p)
    pu = pc.make_puppet(
        p, {"body_name": "北龙王之躯", "body_realm": "斗圣", "body_star": 5,
            "body_fresh": True},
        {"core_tier": 9, "core_attr": "火"},
        {"soul_source": "北龙王残魂", "soul_strength": 3000},
        {"metal_name": "焰陨玄铁", "metal_grade": 4},
        {"fire_tier": 9, "fire_attr": "火"})
    pid = await db.add_puppet(qq, pu)
    await db.set_active(qq, pid)
    return p


async def e2e():
    tmp = tempfile.mkdtemp(prefix="ruin_auto_")
    store = store_mod.Store(os.path.join(tmp, "doupo.json"))
    await store.load()
    db = ps_mod.PuppetStore(os.path.join(tmp, "puppet.sqlite"))
    qq = "10088"
    await _make_strong(store, db, qq)

    rep: dict = {}
    txt = await commands.cmd_puppet_ruin(store, qq, db, report=rep)
    runs = list(rep.get("runs") or [])
    st = await db.ruin_state(qq, DAY)
    check(len(runs) >= 2,
          "一次调用应连过多段（旧实现只 1 段），实得 %d 段：%s"
          % (len(runs), txt[:160]))
    wins = sum(1 for r in runs if r.get("win"))
    check(int(st["stage"]) == wins,
          "水位线应等于实际胜场数（失败段不推进）：stage=%d wins=%d runs=%d"
          % (int(st["stage"]), wins, len(runs)))
    check(all(r.get("win") for r in runs[:-1]),
          "最后一段之前必须全胜，不能跳过失败段继续打")
    check("自动连打" in txt, "文本战报应标明自动连打：%s" % txt[:80])

    # 连打跨场结转的 hp/mp 必须真落库（不是只在内存里改了活字典）
    live = await store.get_player(qq)
    fresh = store_mod.Store(os.path.join(tmp, "doupo.json"))
    await fresh.load()
    disk = await fresh.get_player(qq)
    check(int(disk["hp"]) == int(live["hp"]) and int(disk["mp"]) == int(live["mp"]),
          "连打结束必须写回一次存档：内存 hp/mp=%d/%d，磁盘=%d/%d"
          % (int(live["hp"]), int(live["mp"]), int(disk["hp"]), int(disk["mp"])))

    # 材料累计 = 各段 drops 之和，且不多发
    agg: dict = {}
    for r in runs:
        for k, v in (r.get("drops") or {}).items():
            agg[k] = agg.get(k, 0) + int(v)
    for name, want in agg.items():
        got = await db.material_qty(qq, name)
        check(got == want,
              "材料 %s 应累计 %d（各段 drops 之和），实得 %d（重发或漏发）"
              % (name, want, got))

    # 顶层旧字段仍可读（兼容既有卡片/外部调用）
    check("win" in rep and "rounds" in rep and "stage_name" in rep,
          "report 顶层要保留旧单段字段：%s" % str(rep)[:160])
    check("cleared" in rep and "total" in rep,
          "report 要有 cleared/total：%s" % str(rep)[:160])

    # 卡片：多段并进同一张，段数逐条可见
    data = card.build_ruin_data(await db.ruin_state(qq, DAY), DAY, rep)
    first = (data.get("groups") or [{}])[0]
    check(first.get("name") == "本次挑战", "遗迹卡首段应为「本次挑战」")
    rows = list(first.get("rows") or [])
    check(any("自动挑战" in r for r in rows), "卡片应标明本次自动挑战：%s" % rows[:2])
    # 只数「第 N 段 … ｜」的逐段行；head 里的「第 6 段力竭」不算一段
    n_rows = sum(1 for r in rows if "第 " in r and "段" in r and "｜" in r)
    check(n_rows == len(runs),
          "卡片段行数应等于实际段数：%d vs %d（%s）" % (n_rows, len(runs), rows[:3]))
    png = await card.render_ruin_for(qq, DAY, db, rep)
    check(bool(png) and png[:8] == b"\x89PNG\r\n\x1a\n",
          "多段战报仍须真出 PNG（一处输出一条消息）")

    # 已全通：再调用不重复发料、不再打
    if rep.get("cleared"):
        before = {n: await db.material_qty(qq, n) for n in agg}
        rep2: dict = {}
        await commands.cmd_puppet_ruin(store, qq, db, report=rep2)
        check(rep2.get("cleared") and not rep2.get("runs"),
              "全通后再调用应直接返回全通：%s" % str(rep2)[:120])
        for n, want in before.items():
            check(await db.material_qty(qq, n) == want,
                  "全通后重复调用不应再发 %s" % n)

    # 自动服药：整趟共用一份预算，最多吃 cfg.PUPPET_RUIN_PILL_CAP 颗
    async def _pills_used(qq, cap):
        old = cfg.PUPPET_RUIN_PILL_CAP
        cfg.PUPPET_RUIN_PILL_CAP = cap
        try:
            pp = await _make_strong(store, db, qq)
            core._refill(pp)
            # 起手残血 → 第一回合必然触发自动服药，不能靠「恰好没触发」空过
            pp["hp"] = max(1, int(core.stats(pp)["体力"] * 0.05))
            core.add_item(pp, "回灵丹", 10)
            await store.put_player(pp)
            before = core.count_item(await store.get_player(qq), "回灵丹")
            await commands.cmd_puppet_ruin(store, qq, db)
            after = core.count_item(await store.get_player(qq), "回灵丹")
            return before - after
        finally:
            cfg.PUPPET_RUIN_PILL_CAP = old

    used = await _pills_used("10090", 2)
    check(1 <= used <= 2,
          "残血开局应吃药且整趟不超过 2 颗，实得 %d 颗" % used)
    used0 = await _pills_used("10091", 0)
    check(used0 == 0,
          "预算封 0 时一颗都不许吃（说明 pill_budget 真的传进去了），实得 %d" % used0)

    # 弱玩家：第 1 段就力竭，停住、不推进水位
    weak = "10089"
    pw = core.create_player(weak, "弱号", "30089")
    pw["lv"] = 5
    # 怪血按玩家反解，单靠低等级打不过不成立 —— 必须真的没药且残血。
    pw["hp"] = 1
    pw["bag"] = {}
    await store.put_player(pw)
    rep_w: dict = {}
    await commands.cmd_puppet_ruin(store, weak, db, report=rep_w)
    runs_w = list(rep_w.get("runs") or [])
    st_w = await db.ruin_state(weak, DAY)
    check(len(runs_w) == 1 and not runs_w[0]["win"],
          "弱玩家应在第 1 段力竭即停：%s" % str(runs_w)[:160])
    check(int(st_w["stage"]) == 0,
          "力竭不应推进水位：stage=%d" % int(st_w["stage"]))
    # 输了不许发料（否则可以靠「故意战败」白刷第一段的掉落）
    for n in cfg.PUPPET_MATERIALS:
        check(await db.material_qty(weak, n) == 0,
              "力竭那一段不该发 %s，实得 %d" % (n, await db.material_qty(weak, n)))


def test_source_gates():
    """源码级红线：连打是循环、反解与实战都传真实 p、战败不发掉落。

    这几条行为测试只能覆盖「当前数值恰好撞上」的场景（例如 lv96 的强号
    用标尺玩家反解也不会输），所以必须另有一条钉子直接钉在源码上。
    """
    path = os.path.join(ROOT, "src", "plugins", "doupo", "commands.py")
    with open(path, encoding="utf-8") as f:
        src = f.read()
    i0 = src.index("async def cmd_puppet_ruin(")
    body = src[i0:src.index("\n_PUPPET_COLUMNS", i0)]
    check("while idx < total:" in body,
          "遗迹必须一次调用连打多段（退回 if = 玩家还得点六次）")
    # 2026-11-22 用户改选「标尺固定难度」（与爬塔同款）：怪数值按 power_rec
    # 反查的标尺玩家反解，与真人战力无关 —— 斗尊打斗宗段是真碾压。旧口径
    # 「怪血反解必须传真实 p」已随这条设计决策作废，改为钉住新的唯一入口。
    check("core.ruin_scaled_monster(sg, pu, n, puppet_flame_mult=flame_mult)" in body,
          "怪血必须走标尺固定难度入口 ruin_scaled_monster（传真实 p = 退回自适应，"
          "玩家越强怪越厚、永远没有碾压感）")
    check("p=p" not in body or "monster_scaled" not in body,
          "怪血反解不许直接读真实 p（应只在 ruin_scaled_monster 里用标尺）")
    check("core.fight(p, mon, puppet=pu, formation_n=n," in body and
          "puppet_flame_mult=flame_mult, pill_budget=pill_budget)" in body,
          "实战必须带上真人的傀儡与同一个 flame_mult（怪按标尺、出手按真人，净收益全落袋）")
    # 六段连打 = 深副本口径：不许替玩家自动嗑药（否则「力竭」永远不来，
    # 且会在玩家不知情下吞掉几十颗丹药）。core 里深副本两处也是 False。
    # 服药预算：必须是**循环外**的一份 dict，跨段累计封顶；建在循环里等于每段
    # 重置，六段照样能吞十几颗。
    check("pill_budget = {\"hp\": 0, \"mp\": 0, \"cap\": int(cfg.PUPPET_RUIN_PILL_CAP)}" in body,
          "连打必须整趟共用一份服药预算（建在循环里 = 每段重置，等于没封顶）")
    check("pill_budget=pill_budget)" in body,
          "fight 必须收到这份共享预算")
    check(body.index("pill_budget = {") < body.index("while idx < total:"),
          "预算 dict 必须在 while 之前创建（循环内创建 = 每段满血预算）")
    check("drops = dict(sg[\"drops\"]) if win else {}" in body,
          "战败段不许发掉落（否则故意输第一段就能刷料）")
    check("await pstore.ruin_clear(qq, day, idx + 1)" in body,
          "水位线必须只在胜场后 +1")


def main():
    asyncio.run(e2e())
    test_source_gates()
    for m in _FAILS:
        print("FAIL  " + m)
    print("=" * 66)
    print("RUIN_AUTO_TOTAL=%d FAILED=%d" % (_N, len(_FAILS)))
    return 0 if not _FAILS else 1


if __name__ == "__main__":
    sys.exit(main())
