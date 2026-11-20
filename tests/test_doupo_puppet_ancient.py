# -*- coding: utf-8 -*-
"""P3 守门：远古人傀（斗圣主殿第七段·敌对单位）—— 原著三件事逐条落地（prove-it）。

原著依据（第一千两百三十五章「混乱」）：
  * 「这是远古人傀，以特殊手段将强者的灵魂抽出人体，然后封印于傀儡躯体内，
     如此这般制造而出的傀儡，不仅拥有着神智，而且还能施展斗技，远比寻常傀儡棘手。」
    ⇒ ① 有神智（登场旁白 + 台词）② 会施斗技（skill 走角色包，真实进 fight）
  * 「这些傀儡的身体表面上，似乎被缠绕上了一层层灰白色的奇异布条……即便是
     斗宗强者的全力一击，都是无法震断这种奇异布条。」
    ⇒ ③ 布条卸力（``cloth`` 额外防御）
  * 「无法控制，印记太强，现在的我，无法将之抹除。」
    ⇒ ④ 不可收服（D08）：它只是关卡敌人，永不进无主残傀池

守的重点（为什么这么测）：
  * 布条只加**防御**、不改 ``hard``：血条按同一 mitigation 换算，**回合数不变**
    （难度曲线不被暗改），玩家看到的只是每一击的数字变小 —— 这条要用
    「hp × mitigation(def) 恒定」来钉，不能只断言 def 变大。
  * 「不可收服」不能只看代码没写：要真跑一趟第七段，确认真打完之后
    无主残傀池里没有它。

自带 runner：python tests/test_doupo_puppet_ancient.py
"""
from __future__ import annotations

import asyncio
import importlib
import os
import sys
import tempfile
import types
from datetime import date

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLUGINS = os.path.join(ROOT, "src", "plugins")
for _p in (ROOT, PLUGINS):
    if _p not in sys.path:
        sys.path.insert(0, _p)
os.environ["DOUPO_CHANCE_RATE"] = "0"

NOVEL = "D:/AI/manju_agent/_novel_doupo_20261118/斗破苍穹_全本.txt"
#: 仓库内自带的 2.4KB 原文夹具（第 1235 章「混乱」逐字截取）。
#: 🚨 不许再回到「全本在就查、不在就跳过」：容器里没有 D:/…全本，跳过 = 白守
#:    （2026-11-21 变异 7「台词代编」在容器内没被抓住就是这么来的）。
FIXTURE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "fixtures", "doupo_1235_renkui.txt")


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
from doupo import card as card_mod          # noqa: E402

FAILS = []
N = 0


def check(cond, msg):
    global N
    N += 1
    if not cond:
        FAILS.append(msg)


def _stage():
    return next((s for s in cfg.PUPPET_RUIN_STAGES if s["name"] == "斗圣主殿"), None)


def _p(lv=90):
    p = core.create_player("ancient", "古迹测试", "20001")
    p["lv"] = lv
    return p


# ---------------------------------------------------------------------------
# 1. 数据接入
# ---------------------------------------------------------------------------
def test_stage_is_last_and_drops_are_real():
    stages = list(cfg.PUPPET_RUIN_STAGES)
    sg = _stage()
    check(sg is not None, "遗迹里没有「斗圣主殿」段 —— 远古人傀没接进来")
    if not sg:
        return
    check(stages[-1]["name"] == "斗圣主殿",
          "远古人傀必须是**最后一段**（高层敌对单位），实际末段 %r" % stages[-1]["name"])
    check(sg["monster"] == "远古人傀", "该段怪名不是远古人傀：%r" % sg["monster"])
    check(float(sg.get("cloth") or 0) > 0,
          "远古人傀没有布条（cloth）—— 「斗宗全力一击震不断」没落地")
    check(int(sg["lv"]) >= 90, "远古人傀等级过低：%r" % sg["lv"])
    check(float(sg["hard"]) < 0.72,
          "第七段难度应高于圣地残殿(0.72)，实际 %r" % sg["hard"])
    bad = [k for k in (sg.get("drops") or {}) if k not in cfg.PUPPET_MATERIALS]
    check(not bad, "掉落了不存在的傀儡材料（会进不了库）：%r" % bad)
    check("雷淬石" in (sg.get("drops") or {}),
          "高层遗迹没掉雷淬石 —— 石头仍是死瓶颈")
    # 段落文案是玩家可见文本，不能出现考据术语
    meta = ("未载", "代编", "游戏原创", "游戏命名", "游戏设定", "语料", "原著无")
    hit = [w for w in meta if w in str(sg.get("desc") or "")]
    check(not hit, "第七段描述出现考据术语：%r" % hit)


def test_character_pack_wires_into_fight():
    ch = cfg.monster_character("远古人傀")
    check(bool(ch), "远古人傀没有角色包 —— 有神智、会施斗技都无从表达")
    if not ch:
        return
    check(ch.get("skill", {}).get("name") == "裂山枪",
          "技能不是原著真名「裂山枪」：%r" % (ch.get("skill") or {}))
    check(str(ch.get("skill", {}).get("effect")) == "burst",
          "裂山枪应是爆发型（重枪一击）：%r" % (ch.get("skill") or {}))
    check((ch.get("mech") or {}).get("kind"), "远古人傀没有机制（mech.kind）")
    check(len(str(cfg.character_appearance("远古人傀"))) >= 12,
          "远古人傀缺出场描写（玩家第一眼看到的东西）")
    # 真进 fight 的路径：monster_scaled 产出的 mon 必须带上这个技能
    sg = _stage()
    mon = core.monster_scaled(_p(), dict(sg))
    check((mon.get("skill") or {}).get("name") == "裂山枪",
          "monster_scaled 没把裂山枪传给战斗：%r" % mon.get("skill"))
    check((mon.get("character") or {}).get("name") == "远古人傀",
          "monster_scaled 没带上角色包（登场旁白会缺）")
    # 难度必须真的高于第六段：hard 更狠（0.70 < 0.72）、布条让防御更厚。
    # ⚠️ 不比 atk：``monster_scaled`` 的 atk 里含「等级差 → 回合数」的压缩项
    #    （lv95 打 lv90 的怪，delta 会把战斗压短、单击变重），跨段比 atk 会得出
    #    「低级怪更疼」的假结论。胜率对照见 tmp/probe_ancient_balance2.py。
    s6 = next(s for s in cfg.PUPPET_RUIN_STAGES if s["name"] == "圣地残殿")
    m6 = core.monster_scaled(_p(), dict(s6, name=s6["monster"]))
    check(mon["def"] > m6["def"], "第七段不比第六段硬（布条没生效）：%r vs %r"
          % (mon["def"], m6["def"]))
    # 台词必须逐字见于原著（防代编）。夹具**必须存在**：缺了就是「无法验证」，
    # 必须报红而不是静默跳过（容器里没有全本语料，跳过等于这条门禁不存在）。
    check(os.path.isfile(FIXTURE), "原文夹具缺失，台词/布条/不可收服都无从核对：%s" % FIXTURE)
    if os.path.isfile(FIXTURE):
        with open(FIXTURE, encoding="utf-8") as fh:
            body = fh.read()
        src = [body]
        if os.path.isfile(NOVEL):
            with open(NOVEL, encoding="utf-8", errors="replace") as fh:
                src.append(fh.read())
        miss = [q for q in (ch.get("quotes") or ()) if not any(str(q) in s for s in src)]
        check(not miss, "台词在原著里查无此句（代编）：%r" % miss)
        check(len(ch.get("quotes") or ()) >= 2, "远古人傀台词太少（有神智却没话说）")
        # 夹具自身也要覆盖三个「原著依据」的锚，防止有人删夹具让代编蒙混过关
        check("无法震断" in body, "夹具里没有布条卸力的原文依据（斗宗全力一击无法震断）")
        check("无法将之抹除" in body, "夹具里没有「不可收服」的原文依据")
        check("裂山枪" in body, "夹具里没有「裂山枪」的原文依据（技能名不许自造）")


# ---------------------------------------------------------------------------
# 2. 布条：只加防御、不改回合数
# ---------------------------------------------------------------------------
def test_cloth_raises_def_but_keeps_rounds():
    p = _p()
    sg = dict(_stage())
    plain = dict(sg)
    plain.pop("cloth", None)
    with_cloth = core.monster_scaled(p, dict(sg))
    without = core.monster_scaled(p, dict(plain))
    check(with_cloth["def"] > without["def"],
          "布条没提高防御：%r vs %r" % (with_cloth["def"], without["def"]))
    check(with_cloth["atk"] == without["atk"],
          "布条不该改攻击：%r vs %r" % (with_cloth["atk"], without["atk"]))
    # 回合数不变：``m_hp = 累计伤害 × mitigation(def)``，而每击 = raw × mitigation(def)，
    # 于是「打几回合」= hp / mitigation(def) 除以 raw —— 布条不该动这个比值的分母之外
    # 的任何东西。所以判据是 hp / mitigation(def) 恒定（写成 hp×mit 就会误判）。
    eff_w = with_cloth["hp"] / core.mitigation(with_cloth["def"])
    eff_p = without["hp"] / core.mitigation(without["def"])
    check(abs(eff_w - eff_p) <= max(2.0, eff_p * 1e-3),
          "布条暗改了回合数：有效血量 %r vs %r（应相等）" % (eff_w, eff_p))
    # 每击数字确实变小（玩家能看见「布条卸力」）
    check(core.mitigation(with_cloth["def"]) < core.mitigation(without["def"]),
          "布条没让每击伤害变小，玩家看不出布条存在")


def test_no_cloth_monsters_are_byte_identical():
    """没写 cloth 的怪必须走旧公式（零回归），别把 0 当默认倍率。"""
    p = _p()
    st = core.stats(p)
    sg = {"name": "守库尸卫", "lv": 30, "hard": 0.90, "kind": "魔炎谷",
          "drops": {}, "desc": ""}
    mon = core.monster_scaled(p, sg)
    want = max(20, int(st["防御"] * cfg.MON_DEF_RATIO))
    check(mon["def"] == want, "无布条的怪防御口径变了：%r ≠ %r" % (mon["def"], want))


# ---------------------------------------------------------------------------
# 3. 不可收服：真跑一趟第七段，残傀池里不能有它
# ---------------------------------------------------------------------------
def test_ancient_never_enters_wild_pool():
    psm = importlib.import_module("doupo.puppet_store")
    smod = importlib.import_module("doupo.store")

    async def go():
        tmp = tempfile.mkdtemp(prefix="ancient_")
        store = smod.Store(os.path.join(tmp, "d.json"))
        await store.load()
        db = psm.PuppetStore(os.path.join(tmp, "p.sqlite"))
        qq = "anc1"
        p = core.create_player(qq, "古迹测试", "20001")
        p["lv"] = 95
        p["alch"] = cfg.PUPPET_ALCH_MIN
        await store.put_player(p)
        await db.set_lore(qq, True)
        # 直接把水位线推到第六段之后：本次只打第七段（真跑，不 mock fight）
        await db.ruin_clear(qq, date.today().isoformat(), len(cfg.PUPPET_RUIN_STAGES) - 1)
        rep: dict = {}
        txt = await cmd.cmd_puppet_ruin(store, qq, db, report=rep)
        runs = rep.get("runs") or []
        check(len(runs) == 1, "第七段没真打起来：runs=%r txt=%s" % (runs, txt[:120]))
        if runs:
            check(runs[0]["stage_name"] == "斗圣主殿",
                  "打的不是第七段：%r" % runs[0]["stage_name"])
            check(runs[0]["monster"] == "远古人傀",
                  "对手不是远古人傀：%r" % runs[0]["monster"])
            check(int(runs[0]["rounds"]) > 0, "战斗没回合数（异常熔断？）")
            check(int(runs[0]["rounds"]) <= int(cfg.HARD_ROUND_CAP),
                  "回合数越界：%r" % runs[0]["rounds"])
        wild = await db.wild_puppets()
        check(not [w for w in wild if "远古" in str(w.get("name"))],
              "远古人傀进了无主残傀池 —— 它本该不可收服：%r"
              % [w.get("name") for w in wild])
        # 战报里的「敌方」必须是**物种名**，不能是段名（斗圣主殿是地点，不是对手）
        lb = (await store.get_player(qq)).get("last_battle") or {}
        foes = {str(r.get("foe") or "") for r in (lb.get("log") or [])}
        check("远古人傀" in foes,
              "战报敌方名不是远古人傀（段名/物种名没分清）：%r" % list(foes)[:4])
        # 材料表里也不能有它（能被当材料等于能被交易/收服）
        check("远古人傀" not in cfg.PUPPET_MATERIALS,
              "远古人傀被登记成傀儡材料 —— 违反不可收服")
    asyncio.run(go())


# ---------------------------------------------------------------------------
# 4. 卡片：七段全列、第七段写明远古人傀
# ---------------------------------------------------------------------------
def test_ruin_card_lists_ancient_stage():
    data = card_mod.build_ruin_data({"stage": 6}, "2026-11-21")
    check(len(cfg.PUPPET_RUIN_STAGES) == 7, "遗迹段数应为 7，实际 %d"
          % len(cfg.PUPPET_RUIN_STAGES))
    check("6/7" in data["subtitle"], "遗迹卡副标题没跟上段数：%r" % data["subtitle"])
    joined = " ".join(" ".join(g["rows"]) + g["name"] for g in data["groups"])
    check("远古人傀" in joined, "遗迹卡里看不到远古人傀")
    check("斗圣主殿" in joined, "遗迹卡里看不到斗圣主殿")


def test_menu_help_mentions_ancient_flow():
    """帮助/菜单里遗迹一行要跟上七段（玩家靠这句知道有第七段）。"""
    rows = [r for _, rs in cmd.MENU_GROUPS for r in rs]
    line = next((r[1] for r in rows if r[0] == "傀儡遗迹"), "")
    check("斗圣主殿" in line, "「傀儡遗迹」菜单说明没写第七段：%r" % line)


def _run():
    for fn in (test_stage_is_last_and_drops_are_real,
               test_character_pack_wires_into_fight,
               test_cloth_raises_def_but_keeps_rounds,
               test_no_cloth_monsters_are_byte_identical,
               test_ancient_never_enters_wild_pool,
               test_ruin_card_lists_ancient_stage,
               test_menu_help_mentions_ancient_flow):
        try:
            fn()
        except Exception as e:  # noqa: BLE001
            import traceback
            FAILS.append("%s 抛异常：%s" % (fn.__name__, e))
            traceback.print_exc()
    for m in FAILS:
        print("FAIL  " + m)
    print("=" * 70)
    print("ANCIENT_TOTAL=%d FAILED=%d" % (N, len(FAILS)))
    return 0 if not FAILS else 1


if __name__ == "__main__":
    sys.exit(_run())
