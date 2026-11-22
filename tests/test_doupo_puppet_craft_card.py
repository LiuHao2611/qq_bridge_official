# -*- coding: utf-8 -*-
"""傀儡成卡 + 子命令回执守门（2026-11-20 第十一轮）。

用户两句原话：
  * 「炼制傀儡 自定 的回复不要纯文字，改成卡片」
  * 「再看看傀儡淬炼口令没触发」

第二句是**真 bug 的误报**：命令命中了、雷淬石与金币也真扣了，但 ``puppet_cmd``
handler 结尾无条件出傀儡卡，把 ``cmd_puppet`` 的文本回执整条丢弃 —— 玩家只收到
一张没变化的名录卡，看起来就像口令没触发。受害者不止淬炼：配方/结阵/收服/融魂
全都被吞。

要守住的不变量：
  * ``PUPPET_SUBCMDS`` 必须覆盖 ``cmd_puppet`` 里所有 ``arg.startswith(...)``
    分派 —— 加子命令忘了登记，回执立刻又会被卡吞掉；
  * handler 的「子命令走文本」分支必须**在** ``render_puppet_for`` 之前 return；
  * 成卡只在 ``report["ok"]`` 时渲染，失败/引导回复保持文本（互斥、一条消息）；
  * 卡面数字与文本回执**同源**：``build_craft_data`` 只读 report，不重算攻击力；
  * 降档提醒只在真降档时出现；自选材料要在卡上标出来。

真实 matcher 层的守门在 ``src/plugins/doupo/test_e2e.py``（「傀儡 淬炼（尚无傀儡
也须出声）」等 9 条）——那一层才是抓「回执被卡吞」的唯一位置，本文件跑的是命令层
与纯函数层。

自带 runner：python tests/test_doupo_puppet_craft_card.py
"""
from __future__ import annotations

import asyncio
import importlib
import os
import re
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
from doupo import card  # noqa: E402
from doupo import puppet_core as pc  # noqa: E402

ps_mod = importlib.import_module("doupo.puppet_store")
store_mod = importlib.import_module("doupo.store")

FAILED = []


def check(name, ok, detail=""):
    print(("PASS  " if ok else "FAIL  ") + name + ("  -> " + str(detail) if not ok else ""))
    if not ok:
        FAILED.append(name)
    return ok


def src(rel):
    import io
    with io.open(os.path.join(ROOT, rel), "r", encoding="utf-8", newline="") as f:
        return f.read().replace("\r\n", "\n")


# ---------------------------------------------------------------------------
# 1. 纯函数层
# ---------------------------------------------------------------------------
def test_subcmd_truth():
    """PUPPET_SUBCMDS 必须覆盖 cmd_puppet 里的每一条分派。"""
    body = src("src/plugins/doupo/commands.py")
    i0 = body.index("async def cmd_puppet(store, qq, arg=\"\", pstore=None) -> str:")
    i1 = body.index("def _puppet_recipe_text(", i0)
    seg = body[i0:i1]
    pref = re.findall(r'arg\.startswith\(\s*"([^"]+)"', seg)
    check("cmd_puppet 里确有 startswith 分派", len(pref) >= 5, pref)
    missing = [x for x in pref if x not in commands.PUPPET_SUBCMDS]
    check("每个分派前缀都登记进 PUPPET_SUBCMDS", not missing, missing)
    # 反向：常量里不许有分派层不认识的（否则 puppet_is_subcmd 会误判成文本）
    extra = [x for x in commands.PUPPET_SUBCMDS if x not in pref]
    check("PUPPET_SUBCMDS 无多余项", not extra, extra)


def test_is_subcmd():
    for yes in ("淬炼", "结阵", "收服 1", "融魂", "配方", "谱", "  淬炼  "):
        check("puppet_is_subcmd(%r)=True" % yes, commands.puppet_is_subcmd(yes) is True)
    for no in ("", "   ", None, "看看", "1"):
        check("puppet_is_subcmd(%r)=False" % no, commands.puppet_is_subcmd(no) is False)


def test_handler_gate():
    """handler 必须在出卡之前把子命令分出去，否则回执又被吞。"""
    body = src("src/plugins/doupo/__init__.py")
    i0 = body.index("puppet_cmd = on_regex(")
    i1 = body.index("# 背包锁定", i0)
    seg = body[i0:i1]
    gate = seg.find("commands.puppet_is_subcmd(arg)")
    render = seg.find("card.render_puppet_for(")
    check("handler 有 puppet_is_subcmd 分流", gate >= 0, gate)
    check("分流在建卡之前（先出声再考虑出图）", 0 <= gate < render, (gate, render))
    # 只用 find 会被 `if is_subcmd(arg) and False:` 骗过（调用还在、条件已废）。
    # 这里钉整段结构：条件原样 → finish_text(txt) → return，一步都不许少。
    shape = re.search(
        r"if commands\.puppet_is_subcmd\(arg\):\n"
        r"(?:[ ]+[^\n]*\n)*?"
        r"[ ]+await finish_text\(puppet_cmd, event, txt\)\n"
        r"[ ]+return\n", seg)
    check("分流是「条件原样 + finish_text + return」的完整结构", bool(shape),
          seg[gate:gate + 260] if gate >= 0 else "")

    # 炼制成卡：只有 rep.ok 才渲染，失败保持文本
    j0 = seg.index('if "炼制傀儡" in raw:')
    j1 = seg.index("m = re.search(r\"傀儡\\s*(.*)\", raw)")
    craft = seg[j0:j1]
    k_ok = craft.index('if rep.get("ok"):')
    k_render = craft.index("card.render_craft_for(")
    check("炼制成卡被 rep.ok 挡住", 0 <= k_ok < k_render, (k_ok, k_render))
    check("成卡取数来自 report", "report=rep" in craft)
    check("成卡渲染失败时成卡内容不许消失",
          "炼制卡渲染失败" in craft and "+ txt" in craft)


def test_build_craft_data():
    rep = {
        "ok": True, "name": "人级·强", "recipe": "铜尸傀", "custom": False,
        "pid": 7, "tier": 0, "nominal_tier": 0, "tier_name": "人级傀儡",
        "skin": "铜皮", "atk": 1830, "realm_label": "斗师级",
        "cost": {"强者躯体": 1, "精铁": 1}, "need_mp": 620, "mp_left": 3400,
        "downgrade": "", "purity": 0, "bonded": False, "awakened": False,
        "body_name": "强者躯体", "body_realm": "斗皇", "body_star": 3,
    }
    d = card.build_craft_data(rep)
    flat = "\n".join(r for g in d["groups"] for r in g["rows"])
    check("title 是傀儡炼制", d["title"] == "傀儡炼制", d["title"])
    check("subtitle 含配方与 id", "铜尸傀" in d["subtitle"] and "id=7" in d["subtitle"],
          d["subtitle"])
    check("成傀行含档位与皮", "人级傀儡" in flat and "铜皮" in flat, flat)
    check("肉身攻击数字来自 report", "1830" in flat, flat)
    check("境界标签来自 report", "斗师级" in flat, flat)
    check("用料逐项列出", "强者躯体×1" in flat and "精铁×1" in flat, flat)
    check("斗气消耗来自 report", "斗气 -620" in flat and "余 3400" in flat, flat)
    check("躯体星数写进卡", "斗皇·3星" in flat, flat)
    check("未降档时没有降档分组",
          all(g["name"] != "降档提醒" for g in d["groups"]), [g["name"] for g in d["groups"]])
    check("自选材料标记缺省不出现", "自选材料" not in d["subtitle"], d["subtitle"])

    # 数据只读 report：改 report 就该改卡（不重算＝不猜）
    rep2 = dict(rep, atk=99999, downgrade="此配方本可出地级，火只到兽火", custom=True,
                tier=1, tier_name="地级傀儡")
    d2 = card.build_craft_data(rep2)
    flat2 = "\n".join(r for g in d2["groups"] for r in g["rows"])
    check("改 report 的 atk 会反映到卡", "99999" in flat2, flat2)
    check("降档提醒只在非空时成组",
          any(g["name"] == "降档提醒" for g in d2["groups"]), [g["name"] for g in d2["groups"]])
    check("自选材料要在 subtitle 标出", d2["subtitle"].startswith("自选材料"), d2["subtitle"])
    check("卡面不重复括号（label 自带说明）", d2["groups"][0]["rows"][1].count("（") == 0,
          d2["groups"][0]["rows"][1])

    # 非成傀：必须返回空（调用方此时发文本，不许拿 DEMO 糊）
    for bad in (None, {}, {"ok": False}, {"ok": 0}):
        check("build_craft_data(%r) 返回空" % (bad,), card.build_craft_data(bad) == {})


def test_manifest():
    pic = src("src/plugins/qq_bridge/pic/__init__.py")
    check("模板已 import", "doupo_craft as _doupo_craft" in pic)
    check("模板已注册", '"doupo_craft": _doupo_craft.build' in pic)
    check("卡名已登记", '"doupo_craft": "斗破傀儡炼制卡"' in pic)
    tpl = src("src/plugins/qq_bridge/pic/templates/doupo_craft.py")
    # 2026-11-22 气泡版：不再走 battle legacy 纯文本骨架，改为独立分组气泡 + 序号圆标。
    # 反证：一旦有人退回 "_battle.build(...)" 或丢掉气泡/序号，这条立刻红。
    check("模板走独立气泡骨架（非 battle legacy）",
          "def _plan(" in tpl and "def card_height(" in tpl
          and "_pal(t)" in tpl and "c.ellipse(" in tpl
          and "c.rounded_rect(" in tpl and "ORD_D" in tpl)
    check("模板空数据回退 DEMO（不许空图）",
          "_DEMO" in tpl and "if not _groups_of(data)" in tpl)
    check("card 侧常量存在", card.CARD_CRAFT_NAME == "斗破傀儡炼制")
    # render_craft_for 必须自己挡住「没成傀数据」——否则 _render_list 会拿模板
    # 里的 _DEMO 占位图糊过去，玩家看到一张写着「铜尸傀 人级」的假成卡。
    body = src("src/plugins/doupo/card.py")
    i0 = body.index("async def render_craft_for(")
    rf = body[i0:i0 + 600]
    check("render_craft_for 无数据时返回 None",
          re.search(r"if not data:\n\s+return None", rf) is not None, rf[:200])


# ---------------------------------------------------------------------------
# 2. 命令层：卡面数字与文本回执必须同源
# ---------------------------------------------------------------------------
async def e2e():
    tmp = tempfile.mkdtemp(prefix="pup_craft_card_")
    store = store_mod.Store(os.path.join(tmp, "doupo.json"))
    await store.load()
    db = ps_mod.PuppetStore(os.path.join(tmp, "puppet.sqlite"))
    qq = "10088"
    p = core.create_player(qq, "成卡测试", "20088")
    p["lv"] = 64
    await store.put_player(p)
    p = await store.get_player(qq)      # realm 由 put 同步，mp_max 才有值
    p["alch"] = cfg.PUPPET_ALCH_MIN
    p["craft_count"] = cfg.PUPPET_CRAFT_COUNT_MIN
    p["high_grade_crafts"] = cfg.PUPPET_HIGH_GRADE_MIN
    p["flames"] = {"青莲地心火": 4}
    p["gold"] = 200000
    p["mp"] = 10 ** 9                   # get_player 会 clamp 到 mp_max
    await store.put_player(p)
    await db.set_lore(qq, True)
    for n in cfg.PUPPET_MATERIALS:
        await db.add_material(qq, n, 3)

    rep: dict = {}
    txt = await commands.cmd_craft_puppet(store, qq, db, "铜尸傀", report=rep)
    check("成傀 rep 写了 ok", rep.get("ok") is True, rep)
    check("成傀文本仍是旧的「炼制成功」", "炼制成功" in txt, txt[:60])
    matk = re.search(r"肉身攻击 (\d+)", txt)
    check("文本里有肉身攻击", bool(matk), txt[:120])
    if matk:
        # 用 .get：注入「命令层漏写 atk」时应当断言失败，而不是把整个测试崩掉
        # （崩掉只会得到 FAILED=-1，判据不可信，等于白守）。
        check("卡面 atk == 文本 atk（同源，不重算）",
              int(matk.group(1)) == int(rep.get("atk") or -1),
              (matk.group(1), rep.get("atk")))
    check("rep 带 pid", int(rep.get("pid") or 0) > 0, rep.get("pid"))
    check("rep 记了肉身攻击（卡面唯一数值源）",
          int(rep.get("atk") or 0) > 0, rep.get("atk"))
    _want = set(pc.recipe_materials(cfg.PUPPET_RECIPE_BY_NAME["铜尸傀"]))
    check("rep 记了全部用料", set(rep.get("cost") or {}) == _want,
          (sorted(rep.get("cost") or {}), sorted(_want)))
    data = card.build_craft_data(rep)
    check("成傀能构出卡数据", bool(data.get("groups")), data)
    check("卡 subtitle 写的是这条配方",
          "铜尸傀" in data.get("subtitle", ""), data.get("subtitle"))

    # 失败路径：rep 不许被写 ok（否则会拿 DEMO 出卡糊玩家）
    rep2: dict = {}
    txt2 = await commands.cmd_craft_puppet(store, qq, db, "不存在的配方", report=rep2)
    check("未知配方报错是文本", "无此配方" in txt2, txt2[:60])
    check("失败不写 rep.ok", not rep2.get("ok"), rep2)
    check("失败构不出卡", card.build_craft_data(rep2) == {})

    # 自选材料：卡上要能看出这是自定组合
    p = await store.get_player(qq)
    p["mp"] = 10 ** 9
    await store.put_player(p)
    rep3: dict = {}
    txt3 = await commands.cmd_craft_puppet(
        store, qq, db, "自定 强者躯体 火属性魔核 无主灵魂 精铁", report=rep3)
    check("自定炼制成功", rep3.get("ok") is True, txt3[:80])
    d3 = card.build_craft_data(rep3)
    check("自定卡的 subtitle 标了「自选材料」",
          d3.get("subtitle", "").startswith("自选材料"), d3.get("subtitle"))

    # 子命令回执：命令层返回的文本必须是非空、且带关键信息（被吞的是 handler 那一层）
    roster = await db.list_puppets(qq)
    elig = await db.eligibility(qq)
    t_temper = await commands.cmd_puppet(store, qq, "淬炼", db)
    check("淬炼回执非空", bool(t_temper.strip()), t_temper[:60])
    check("淬炼回执写明结果或原因",
          ("雷霆淬炼" in t_temper) or ("不足" in t_temper) or ("无需再淬" in t_temper),
          t_temper[:60])
    check("淬炼真扣了雷淬石（回执不是空话）",
          int((await db.materials(qq)).get("雷淬石", 0)) == 3 - cfg.PUPPET_TEMPER_STONE,
          (await db.materials(qq)).get("雷淬石"))
    t_form = await commands.cmd_puppet(store, qq, "结阵", db)
    check("结阵回执非空", bool(t_form.strip()), t_form[:60])
    t_awaken = await commands.cmd_puppet(store, qq, "融魂", db)
    check("融魂回执非空", bool(t_awaken.strip()), t_awaken[:60])
    t_recipe = await commands.cmd_puppet(store, qq, "配方", db)
    check("配方回执列出全部条目", "共 %d 条" % len(cfg.PUPPET_RECIPES) in t_recipe,
          t_recipe[:60])


async def main():
    for fn in (test_subcmd_truth, test_is_subcmd, test_handler_gate,
               test_build_craft_data, test_manifest):
        fn()
    await e2e()
    print("=" * 70)
    print("TOTAL FAILED=%d" % len(FAILED))
    for x in FAILED:
        print("  - " + x)
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
