# -*- coding: utf-8 -*-
"""P3 守门：养魂（养魂涎 → 傀儡灵魂强度）—— 标准玩法、零漏洞（prove-it）。

原著依据 N08（第900章「养魂涎」）：
  * 「好五品左右，炼制起来也并不繁琐，但唯一的缺陷便是所需要的药材太过偏门」
    ⇒ 未列药方 ⇒ 按既有铁律**只能购得、不可开炉**（core.craft 返回 buyonly）；
  * 「其中那道沉睡的灵魂，正在缓缓的苏醒」
    ⇒ 灵魂是**可养**的，这是原著唯一给出的灵魂成长手段。

本作规则（设计文档 §1.2 U08 拍板 + 本轮落地口径）：
  * 材料决定的 soul_strength 是**基准值、永不改写**（判档锚）；
    养魂量单独记在 soul_fed（0..200），有效灵魂 = 基准 + 养魂；
  * 每枚 +10，上限 200，确定性、不掷骰；
  * 只扣真正用得上的丹（喂到 190 再喂 5 枚 → 只吃 1 枚）；
  * 已满 / 没丹 / 没傀儡三种分支先报错、**一枚都不扣**；
  * 「服丹 养魂涎」必须被拒且不消耗（拦在 take_item 之前）。

自带 runner：python tests/test_doupo_puppet_soul.py
"""
from __future__ import annotations

import asyncio
import importlib
import os
import sqlite3
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
from doupo import core                      # noqa: E402
from doupo import puppet_core as pc         # noqa: E402
from doupo import card as card_mod          # noqa: E402

FAILS = []
N = 0


def check(cond, msg):
    global N
    N += 1
    if not cond:
        FAILS.append(msg)


def _pu(**kw):
    """一具「全部基准料」的傀儡：材料系数恰为 1.0（便于用绝对锚断言）。"""
    base = {
        "id": 1, "name": "铜尸傀", "tier": 0, "tier_name": "人级傀儡", "skin": "铜",
        "body_name": "强者躯体", "body_realm": "斗皇", "body_star": 0,
        "core_tier": 7, "core_attr": "火", "soul_source": "无主灵魂",
        "soul_strength": 900, "soul_fed": 0,
        "metal_name": "暗金玄铁", "metal_grade": 2, "fire_tier": 7,
        "purity": 0, "bonded": True, "awakened": False, "damaged": False,
    }
    base.update(kw)
    return base


# ---------------------------------------------------------------------------
# 1. 数值口径：基准 + 养魂，老档零回归
# ---------------------------------------------------------------------------
def test_soul_total_and_old_save_compat():
    """老档没有 soul_fed ⇒ 有效灵魂 == 材料基准，材料系数逐字节不变。"""
    old = _pu()
    old.pop("soul_fed")                      # 真·老存档：库里还没这一列
    check(pc.soul_fed_of(old) == 0, "老档 soul_fed 应为 0，实际 %r" % pc.soul_fed_of(old))
    check(pc.soul_total(old) == 900, "老档有效灵魂应等于基准 900，实际 %d"
          % pc.soul_total(old))
    # 绝对锚：core7/soul900/metal2/fire7 的材料系数必须正好 1.0
    check(abs(pc._material_mult(old) - 1.0) < 1e-9,
          "老档材料系数不是 1.0：%r" % pc._material_mult(old))
    # 养满 200：1.0 + 200×0.00006 = 1.012（绝对数，不读 cfg 期望值）
    _cfg_ok = (int(cfg.PUPPET_SOUL_FEED_MAX) == 200
               and int(cfg.PUPPET_SOUL_FEED_GAIN) == 10)
    check(_cfg_ok, "养魂常量被改：MAX=%r GAIN=%r"
          % (cfg.PUPPET_SOUL_FEED_MAX, cfg.PUPPET_SOUL_FEED_GAIN))
    fed = _pu(soul_fed=200)
    check(pc.soul_total(fed) == 1100, "养满后有效灵魂应为 1100，实际 %d"
          % pc.soul_total(fed))
    check(abs(pc._material_mult(fed) - 1.012) < 1e-9,
          "养满 200 的材料系数应为 1.012，实际 %r" % pc._material_mult(fed))


def test_feed_soul_steps_caps_and_no_waste():
    """每次 +10、上限 200、超出部分不吞丹。"""
    r = pc.feed_soul(_pu(), 3)
    check(r["ok"] and r["used"] == 3 and r["fed"] == 30,
          "喂 3 枚应 used=3/fed=30，实际 %r" % r)
    check(r["left"] == 170, "剩余可养应为 170，实际 %r" % r["left"])
    # 喂到 190 再要 5 枚：只该吃 1 枚，其余原样留在包里
    r2 = pc.feed_soul(_pu(soul_fed=190), 5)
    check(r2["used"] == 1 and r2["fed"] == 200 and r2["maxed"],
          "190 喂 5 枚应只吃 1 枚到 200，实际 %r" % r2)
    # 已满：不 ok、不消耗
    r3 = pc.feed_soul(_pu(soul_fed=200), 10)
    check((not r3["ok"]) and r3["used"] == 0 and r3["reason"] == "maxed",
          "已满仍允许喂：%r" % r3)
    # 无傀儡
    r4 = pc.feed_soul(None, 1)
    check((not r4["ok"]) and r4["used"] == 0, "无傀儡仍返回成功：%r" % r4)
    # 0 枚
    r5 = pc.feed_soul(_pu(), 0)
    check((not r5["ok"]) and r5["used"] == 0, "0 枚仍返回成功：%r" % r5)


def test_soul_feed_reaches_attack_hp_armor():
    """养魂的进益必须真的落到攻击/气血/护甲三个出口（不是一串孤立数字）。"""
    plain = _pu()
    fed = _pu(soul_fed=200)
    a0 = pc.puppet_attack(plain)             # p=None → 境界标尺，不吃 CAP
    a1 = pc.puppet_attack(fed)
    check(a1 > a0, "养满后肉身攻击没涨：%r→%r" % (a0, a1))
    check(abs(a1 / a0 - 1.012) < 1e-6,
          "攻击涨幅应等于材料系数 1.2%%：%r" % (a1 / a0 - 1))
    g0 = pc.guard_profile(plain)
    g1 = pc.guard_profile(fed)
    check(g1["hp_max"] > g0["hp_max"], "养满后护主气血没涨：%r→%r"
          % (g0["hp_max"], g1["hp_max"]))
    check(g1["armor"] > g0["armor"], "养满后护甲没涨：%r→%r" % (g0["armor"], g1["armor"]))


# ---------------------------------------------------------------------------
# 2. 丹药契约：只能买、不可开炉、服丹被拒且不消耗
# ---------------------------------------------------------------------------
def test_soul_pill_contract_is_honest():
    rec = cfg.RECIPE_BY_NAME["养魂涎"]
    check(rec["effect"] == "puppet_soul", "养魂涎 effect 不是 puppet_soul：%r"
          % rec["effect"])
    check(int(rec["value"]) == 10, "养魂涎 value 应为 10，实际 %r" % rec["value"])
    check(not rec.get("need"), "养魂涎被编了药方（原著未列药材，只可购得）")
    check(int(rec["buy"]) > 0, "养魂涎没有售价 —— 玩家将无处可得")
    check(cfg.effect_tag("puppet_soul") == "养魂", "缺少养魂效果标签")
    impl = cfg.recipe_impl(rec)
    check("傀儡灵魂" in impl and "200" in impl,
          "卡面承诺必须写清灵魂量纲与上限，实际 %r" % impl)
    # 未列药方 ⇒ craft 必须诚实拒绝（buyonly），而不是静默炼不出
    p = core.create_player("soulcraft", "炼丹测试", "20001")
    p["alch"] = 9
    res = core.craft(p, "养魂涎")
    check(res.get("reason") == "buyonly",
          "养魂涎应只能购买（buyonly），实际 %r" % res)


def test_use_pill_refuses_without_consuming():
    """「服丹 养魂涎」必须被拒且**一枚都不扣**（这是最容易被吞的洞）。"""
    async def go():
        smod = importlib.import_module("doupo.store")
        tmp = tempfile.mkdtemp(prefix="soul_use_")
        store = smod.Store(os.path.join(tmp, "d.json"))
        await store.load()
        p = core.create_player("souluse", "服丹测试", "20001")
        core.add_item(p, "养魂涎", 2)
        await store.put_player(p)
        txt = await cmd.cmd_use(store, "souluse", "养魂涎")
        p2 = await store.get_player("souluse")
        check("傀儡 养魂" in txt, "服丹回执没把玩家指到「傀儡 养魂」：%s" % txt[:80])
        check("还在你包里" in txt, "服丹回执没说清丹没被扣：%s" % txt[:80])
        check(core.count_item(p2, "养魂涎") == 2,
              "服丹吞掉了养魂涎：余 %d" % core.count_item(p2, "养魂涎"))
        # 底层 use_item 也必须拒绝（防止别的入口绕过命令层）
        r = core.use_item(p2, "养魂涎")
        check(r.get("reason") == "need_puppet", "use_item 未返回 need_puppet：%r" % r)
        check(core.count_item(p2, "养魂涎") == 2,
              "use_item 直调也吞了丹：余 %d" % core.count_item(p2, "养魂涎"))
    asyncio.run(go())


# ---------------------------------------------------------------------------
# 3. 命令层：真扣丹、真入库、满上限不多吃、卡面可见
# ---------------------------------------------------------------------------
async def _mk_env(qq: str, pills: int = 0, soul_fed: int = 0, make_puppet: bool = True):
    psm = importlib.import_module("doupo.puppet_store")
    smod = importlib.import_module("doupo.store")
    tmp = tempfile.mkdtemp(prefix="soul_cmd_")
    store = smod.Store(os.path.join(tmp, "d.json"))
    await store.load()
    db = psm.PuppetStore(os.path.join(tmp, "p.sqlite"))
    p = core.create_player(qq, "养魂测试", "20001")
    p["lv"] = 90
    p["alch"] = cfg.PUPPET_ALCH_MIN
    if pills:
        core.add_item(p, cfg.PUPPET_SOUL_PILL, pills)
    await store.put_player(p)
    await db.set_lore(qq, True)
    if not make_puppet:
        return store, db, p, 0
    pid = await db.add_puppet(qq, {
        "name": "铜尸傀", "tier": 0, "body_name": "强者躯体",
        "body_realm": "斗皇", "body_star": 0, "core_tier": 7,
        "soul_source": "无主灵魂", "soul_strength": 900, "soul_fed": soul_fed,
        "metal_name": "暗金玄铁", "metal_grade": 2, "fire_tier": 7,
        "purity": 0, "bonded": True,
    })
    await db.set_active(qq, pid)
    return store, db, p, pid


async def _feed(store, db, qq, arg):
    p = await store.get_player(qq)
    return await cmd._cmd_puppet_soul_feed(
        store, qq, p, await db.list_puppets(qq),
        await db.eligibility(qq), db, arg)


def test_command_feeds_and_persists():
    async def go():
        qq = "soul1"
        store, db, p, pid = await _mk_env(qq, pills=3)
        txt = await _feed(store, db, qq, "养魂 2")
        row = await db.get_puppet(qq, pid)
        p2 = await store.get_player(qq)
        check(row["soul_fed"] == 20, "库中 soul_fed 应 20，实际 %r" % row["soul_fed"])
        check(row["soul_strength"] == 900, "基准 soul_strength 被改写：%r"
              % row["soul_strength"])
        check(core.count_item(p2, "养魂涎") == 1, "背包没扣丹：余 %d"
              % core.count_item(p2, "养魂涎"))
        check("灵魂强度 +20" in txt and "20/200" in txt,
              "回执没说清本次进益与进度：%s" % txt[:120])
        # 再喂 5 枚（只剩 1 枚）：如实按持有量吃，不报「成功 5 枚」
        txt2 = await _feed(store, db, qq, "养魂 5")
        row2 = await db.get_puppet(qq, pid)
        check(row2["soul_fed"] == 30, "补喂后应 30，实际 %r" % row2["soul_fed"])
        check("×1" in txt2, "回执应按实吃 1 枚：%s" % txt2[:120])
    asyncio.run(go())


def test_command_caps_and_never_eats_extra():
    async def go():
        qq = "soul2"
        store, db, p, pid = await _mk_env(qq, pills=5, soul_fed=190)
        txt = await _feed(store, db, qq, "养魂 5")
        row = await db.get_puppet(qq, pid)
        p2 = await store.get_player(qq)
        check(row["soul_fed"] == 200, "应停在 200，实际 %r" % row["soul_fed"])
        check(core.count_item(p2, "养魂涎") == 4,
              "超出上限的丹被吞了：余 %d（应 4）" % core.count_item(p2, "养魂涎"))
        check("已养满" in txt, "养满没给出明确反馈：%s" % txt[:120])
        # 已满再喂：不扣丹、不返回成功
        txt2 = await _feed(store, db, qq, "养魂 3")
        p3 = await store.get_player(qq)
        check("已至养魂上限" in txt2, "已满时回执不对：%s" % txt2[:120])
        check(core.count_item(p3, "养魂涎") == 4,
              "已满仍扣丹：余 %d" % core.count_item(p3, "养魂涎"))
    asyncio.run(go())


def test_command_reports_when_no_pill_or_no_puppet():
    async def go():
        qq = "soul3"
        store, db, p, pid = await _mk_env(qq, pills=0)
        txt = await _feed(store, db, qq, "养魂")
        check("没有【养魂涎】" in txt, "没丹时没说清缺什么：%s" % txt[:120])
        check("还差" in txt, "没丹时没给出还差多少：%s" % txt[:120])
        # 没有傀儡：cmd_puppet 走五道资格引导，必须出声且指向炼制
        store2, db2, _p2, _ = await _mk_env("soul3b", pills=1, make_puppet=False)
        txt2 = await cmd.cmd_puppet(store2, "soul3b", "养魂", db2)
        check("炼制傀儡" in txt2 or "尚未拥有傀儡" in txt2,
              "无傀儡时养魂没有指路：%s" % txt2[:120])
        check(core.count_item(await store2.get_player("soul3b"), "养魂涎") == 1,
              "无傀儡时养魂扣了丹")
    asyncio.run(go())


def test_card_shows_soul_progress():
    async def go():
        qq = "soul4"
        store, db, p, pid = await _mk_env(qq, pills=1, soul_fed=30)
        # 文本卡
        txt = await cmd.cmd_puppet(store, qq, "", db)
        check("养魂 30/200" in txt, "文本傀儡卡没有养魂进度：%s" % txt[:200])
        check("有效灵魂 930" in txt, "文本卡没给有效灵魂：%s" % txt[:200])
        # PNG 名录卡的数据层（与文本卡同一份 soul_feed_info）
        roster = await db.list_puppets(qq)
        data = card_mod.build_puppet_data(roster, await db.eligibility(qq), p)
        lines = " ".join(data["puppets"][0]["lines"])
        check("养魂 30/200" in lines, "PNG 名录卡没有养魂进度：%s" % lines[:200])
    asyncio.run(go())


# ---------------------------------------------------------------------------
# 4. 老库迁移：缺列不能让老玩家直接崩
# ---------------------------------------------------------------------------
def test_store_migrates_old_db_without_soul_fed():
    psm = importlib.import_module("doupo.puppet_store")
    tmp = tempfile.mkdtemp(prefix="soul_mig_")
    path = os.path.join(tmp, "old.sqlite")
    # 用当前 schema 去掉 soul_fed 那一行，造一个「上一版」的库
    old_schema = "\n".join(l for l in psm._SCHEMA.splitlines() if "soul_fed" not in l)
    con = sqlite3.connect(path)
    con.executescript(old_schema)
    con.execute("INSERT INTO puppets(qq, name, soul_strength) VALUES('m1','老傀',900)")
    con.commit()
    con.close()

    async def go():
        db = psm.PuppetStore(path)
        rows = await db.list_puppets("m1")
        check(len(rows) == 1, "老库读不出傀儡：%r" % rows)
        check(int(rows[0].get("soul_fed") or 0) == 0,
              "迁移后 soul_fed 应为 0，实际 %r" % rows[0].get("soul_fed"))
        check(pc.soul_total(rows[0]) == 900, "老傀有效灵魂被改：%d"
              % pc.soul_total(rows[0]))
        # 迁移后能正常写
        await db.update_puppet("m1", int(rows[0]["id"]), soul_fed=30)
        again = await db.get_puppet("m1", int(rows[0]["id"]))
        check(int(again["soul_fed"]) == 30, "迁移后写入失败：%r" % again["soul_fed"])
        # 幂等：再开一次不会因「列已存在」炸掉
        db2 = psm.PuppetStore(path)
        rows2 = await db2.list_puppets("m1")
        check(int(rows2[0]["soul_fed"]) == 30, "二次打开把数据搞坏了：%r"
              % rows2[0]["soul_fed"])
    asyncio.run(go())


def test_subcmd_registered():
    """「养魂」必须登记在 PUPPET_SUBCMDS，否则回执会被傀儡卡吞掉。"""
    check("养魂" in cmd.PUPPET_SUBCMDS, "养魂未登记为傀儡子命令")
    check(cmd.puppet_is_subcmd("养魂"), "puppet_is_subcmd('养魂') 为假 —— 会去出图")
    check(cmd.puppet_is_subcmd("养魂 3"), "带次数的养魂没被识别为子命令")


def _run():
    for fn in (test_soul_total_and_old_save_compat,
               test_feed_soul_steps_caps_and_no_waste,
               test_soul_feed_reaches_attack_hp_armor,
               test_soul_pill_contract_is_honest,
               test_use_pill_refuses_without_consuming,
               test_command_feeds_and_persists,
               test_command_caps_and_never_eats_extra,
               test_command_reports_when_no_pill_or_no_puppet,
               test_card_shows_soul_progress,
               test_store_migrates_old_db_without_soul_fed,
               test_subcmd_registered):
        try:
            fn()
        except Exception as e:  # noqa: BLE001
            import traceback
            FAILS.append("%s 抛异常：%s" % (fn.__name__, e))
            traceback.print_exc()
    for m in FAILS:
        print("FAIL  " + m)
    print("=" * 70)
    print("SOUL_TOTAL=%d FAILED=%d" % (N, len(FAILS)))
    return 0 if not FAILS else 1


if __name__ == "__main__":
    sys.exit(_run())
