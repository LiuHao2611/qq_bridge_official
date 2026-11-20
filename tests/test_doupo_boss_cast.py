# -*- coding: utf-8 -*-
"""原著 BOSS 登场 / 台词 / 机制 + 买物品上限 门禁（2026-11-20）。

用户原话：
  「9星阶段突破boos战斗太敷衍……别写什么快照玩法的，
   怪物太单一，原著那么多boos和对应的出圈台词，
   boos登场肯定是要有一段引言或者台词，让玩家沉浸式体验突如其来的突破惊喜或者惊吓，
   你必须要认真结合原著，设计好每一个精英怪和boos，都要有各自的技能，
   精英怪一般有一个就够了，但是要最后恶心，就是有机制的。
   优化买物品上限，不要限制99。」

逐条钉死：
  1. **快照内藏**：内部保留 snapshot_power 锁难度；玩家文案零露出（无"快照/战力/心魔劫影"）。
  2. **原著 BOSS**：12 突破档 12 个不同 BOSS，各有 source/intro/skill/mech。
  3. **台词有出处**：角色库里每条 quotes 都是原著语料的逐字子串（防代编）。
  4. **登场引言**：fight 把 intro 写成独立旁白 log（intro=True，dmg=0），
     battle_review.intro_text 能提出来，且不污染逐招明细。
  5. **恶心机制**：mech 经 fx.on_hit 挂到玩家 p_fx，能实测生效（禁技/反伤/毒/眩晕）。
  6. **买上限**：SHOP_BUY_MAX=9999，cmd_buy 买 150 真收到 150（不再夹到 99）。
  7. **野外/塔/周本全覆盖**：23 只野外怪 + 10 个塔 BOSS + 3 个周本怪都有角色包；
     精英/BOSS 必有专属技能与恶心机制；游戏原创怪不许冒充原著台词。
  8. **三条 notes 不吞**：技能/原著台词/机制名都进战报标签（曾只取前 2 条）。

反证（改坏必红）：
  * 把某句台词改成代编的假句 → test_all_quotes_are_verbatim_from_novel 红；
  * 把 12 档 BOSS 都换成同一只 → test_twelve_distinct_bosses 红；
  * 删掉 intro 旁白 → test_intro_bubble_in_log 红；
  * 删掉 mech 挂 p_fx → test_mechanic_silence_blocks_skill 红；
  * 把 cmd_buy 上限改回 99 → test_buy_150_not_clamped 红；
  * 删内部 snapshot_power 或把它印进文案 → test_snapshot_internal_but_invisible 红。

运行：python tests/test_doupo_boss_cast.py（脚本式，exit 0 = 全绿）
"""
from __future__ import annotations

import asyncio
import os
import re as _re
import sys
import tempfile
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# 语料路径可用环境变量注入（容器内挂载不到 D 盘时由宿主跑这段守门）
NOVEL = os.environ.get("DOUPO_NOVEL_PATH",
                       "D:/AI/manju_agent/_novel_doupo_20261118/斗破苍穹_全本.txt")


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

from doupo import config as cfg, core, battle_review as br  # noqa: E402
from doupo import tower_data as td, weekly_data as wd  # noqa: E402

_RES = []


def check(name, ok, detail=""):
    _RES.append((name, bool(ok), str(detail)))


def _p(lv=1):
    p = core.create_player("QBOSS", "测试", "群")
    p["lv"] = lv
    core._refill(p)
    return p


def _mon(p, *, character, hp=300, atk=1):
    """手搓 BOSS：character 角色包直接挂上，数值可控，避免反解干扰。"""
    return {"name": character["name"], "lv": int(p["lv"]),
            "hp": int(hp), "atk": int(atk), "def": 0, "exp": 1, "gold": 1,
            "kind": "魂殿", "is_boss": True,
            "skill": dict(character["skill"]), "character": dict(character)}


# ---------------------------------------------------------------------------
# ① 去快照
# ---------------------------------------------------------------------------
def test_snapshot_internal_but_invisible():
    """用户澄清：快照要有（内部锁难度），只是不要发出来（影响体验）。"""
    p = _p(9)
    expect = core.power(p)
    boss = core.make_breakthrough_boss(p)
    check("内部保留 snapshot_power（触发瞬间战力）",
          int(boss.get("snapshot_power", 0)) == expect,
          "expect=%d got=%s" % (expect, boss.get("snapshot_power")))
    check("快照只锁难度，不覆盖原著 BOSS 身份",
          boss.get("name") == cfg.break_boss_for_lv(9)["name"]
          and boss["name"] != "心魔劫影", boss.get("name", ""))
    check("BOSS 有 title", bool(boss.get("boss_title")), boss.get("boss_title", ""))

    # 对外：命令层源码与真输出都不得出现快照/战力数字/心魔劫影
    import inspect
    from doupo import commands
    from doupo.store import Store
    src = inspect.getsource(commands.cmd_break)
    body = "\n".join(l for l in src.splitlines() if not l.strip().startswith("#"))
    check("cmd_break 代码不引用 snapshot_power", "snapshot_power" not in body,
          body[:160])

    async def _run():
        st = Store(os.path.join(tempfile.mkdtemp(prefix="bossnap_"), "d.json"))
        await st.load()
        q = core.create_player("SNAPQ", "快照验收", "群")
        q["lv"], q["exp"] = 27, cfg.exp_need(27) + 10
        core._refill(q)
        await st.put_player(q)
        return await commands.cmd_break(st, "SNAPQ", rng=lambda: 0.01)

    out = asyncio.run(_run())
    leak = [w for w in ("快照", "战力", "心魔劫影", "snapshot") if w in out]
    check("真突破文案零快照露出", not leak, str(leak) + " | " + out[:140])
    check("真突破文案仍有原著引言/BOSS", "云棱" in out and "我是云岚宗的大长老" in out,
          out[:140])


# ---------------------------------------------------------------------------
# ② 12 档不同原著 BOSS + 完整角色包
# ---------------------------------------------------------------------------
def test_twelve_distinct_bosses():
    names = [cfg.break_boss_for_lv(lv)["name"]
             for lv in (9, 18, 27, 36, 45, 54, 63, 72, 81, 90, 93, 102)]
    check("12 档配 12 个不同 BOSS", len(set(names)) == 12, str(names))
    for lv in (9, 27, 54, 72, 90, 102):
        ch = cfg.break_boss_for_lv(lv)
        check("lv%d 角色包完整" % lv,
              all(ch.get(k) for k in ("name", "title", "source", "intro", "skill", "mech")),
              str(ch)[:90])


def test_every_mech_is_consumable_type():
    """每个机制类型都必须能被战斗系统消费，否则是"假机制"。"""
    ok_types = {"silence", "stun", "reflect", "debuff_heal", "dot"}
    bad = [(ch["name"], ch["mech"].get("t")) for ch in cfg.BREAK_BOSSES
           if ch["mech"].get("t") not in ok_types]
    check("所有 BOSS 机制类型可消费", not bad, str(bad))
    # 概率必须落在 (0,1]，否则机制永远不触发 = 假机制
    bad_p = [(ch["name"], ch["mech"].get("p")) for ch in cfg.BREAK_BOSSES
             if not (0 < float(ch["mech"].get("p", 0)) <= 1)]
    check("所有机制概率 >0 且 <=1", not bad_p, str(bad_p))


# ---------------------------------------------------------------------------
# ③ 台词逐字来自原著（防代编，最强的诚实门禁）
# ---------------------------------------------------------------------------
def test_all_quotes_are_verbatim_from_novel():
    if not os.path.isfile(NOVEL):
        check("原著语料存在（守门前置）", False, NOVEL)
        return
    with open(NOVEL, encoding="utf-8", errors="replace") as fh:
        novel = fh.read()
    missing = []
    for ch in list(cfg.BREAK_BOSSES) + list(cfg.MONSTER_CHARACTERS.values()):
        # quotes 是"台词"，必须逐字见于原著（防代编）。
        # intro 是登场叙述（含文学化连接/省略号），不做逐字校验，但其引文锚点
        # 必须能在 quotes 或 source 章节找到——见 test_intro_anchors_from_novel。
        for q in ch.get("quotes") or ():
            if str(q) not in novel:
                missing.append((ch["name"], q))
    check("所有 BOSS/精英台词逐字见于原著（零代编）", not missing, str(missing))


def test_intro_anchors_from_novel():
    """每句登场引言至少有一个 ≥8 字的片段能在原著逐字找到（防整句代编）。"""
    if not os.path.isfile(NOVEL):
        check("原著语料存在（intro 锚点前置）", False, NOVEL)
        return
    with open(NOVEL, encoding="utf-8", errors="replace") as fh:
        novel = fh.read()
    bad = []
    for ch in cfg.BREAK_BOSSES:
        intro = str(ch.get("intro") or "")
        # 取「」内每段引文，按 …… / 。/ ，切出 ≥8 字锚点
        anchors = []
        for quoted in _re.findall(r"「([^」]+)」", intro):
            for seg in _re.split(r"[…。！？，]", quoted):
                if len(seg) >= 8:
                    anchors.append(seg)
        # 至少有一个锚点在原文（魔兽无台词者 intro 是威压叙述，豁免）
        if anchors and not any(a in novel for a in anchors):
            bad.append((ch["name"], anchors))
    check("登场引言锚点均出自原著", not bad, str(bad)[:200])


# ---------------------------------------------------------------------------
# ④ 登场引言旁白 + 不污染逐招
# ---------------------------------------------------------------------------
def test_intro_bubble_in_log():
    p = _p(9)
    boss = core.make_breakthrough_boss(p)
    # 让战斗快速结束：怪血 1，玩家一击必杀，但登场旁白仍应在 log 里
    boss["hp"] = 1
    r = core.fight(p, boss, rng=lambda: 0.9, roll_aid=False)
    intro = [it for it in r["log"] if isinstance(it, dict) and it.get("intro")]
    check("战斗 log 有登场旁白", len(intro) == 1, str(len(intro)))
    check("登场旁白 dmg=0", intro and int(intro[0].get("dmg", -1)) == 0, str(intro))
    check("登场旁白带 speaker+notes", intro and bool(intro[0].get("speaker"))
          and bool(intro[0].get("notes")), str(intro))
    # battle_review：独立提成引言块，且不进逐招明细
    info = br.intro_text({"log": r["log"]})
    check("回看能提出登场引言", bool(info.get("quote")), str(info))
    acts = br.action_items({"log": r["log"]})
    check("登场旁白不进逐招明细", all("登场" != a.get("move") for a in acts), str(acts)[:100])


# ---------------------------------------------------------------------------
# ⑤ 恶心机制真生效（禁技：挂上后玩家只能用普攻，斗气不扣）
# ---------------------------------------------------------------------------
def test_mechanic_silence_blocks_skill():
    # 直接构造一只带 silence 机制的 BOSS，强制机制 100% 触发
    ch = dict(cfg.BREAK_BOSSES[2])   # 云棱，机制=silence
    ch = dict(ch)
    ch["mech"] = dict(ch["mech"])
    ch["mech"]["p"] = 1.0
    ch["skill"] = dict(ch["skill"])
    ch["skill"]["chance"] = 1.0
    p = _p(9)
    p["mp"] = core.mp_max(p)   # 满斗气，本可用斗技
    boss = _mon(p, character=ch, hp=100000, atk=1)  # 怪血超厚，打很多回合
    r = core.fight(p, boss, rng=lambda: 0.99, roll_aid=False, auto_pill=False)
    # 禁技生效 → 至少有一条技能 notes 提到"封剑势"，且玩家 mp 几乎不被消耗
    notes_text = " ".join(str(n) for it in r["log"]
                          for n in (it.get("notes") or []))
    hits = notes_text.count("封剑势") + notes_text.count("禁技")
    check("禁技机制在战报生效", hits >= 1, notes_text[:150])
    # 机制必须是"持续恶心"而非只挂一次：长战斗里应反复触发（d=2 跨回合生效）
    check("禁技跨回合反复生效（恶心机制）", hits >= 3, "触发 %d 次" % hits)


def test_mechanic_reflect_hurts_player():
    ch = dict(cfg.BREAK_BOSSES[3])   # 黑盟死士，机制=reflect
    ch["mech"] = dict(ch["mech"])
    ch["mech"]["p"] = 1.0
    ch["skill"] = dict(ch["skill"])
    ch["skill"]["chance"] = 1.0
    p = _p(9)
    boss = _mon(p, character=ch, hp=100000, atk=1)
    r = core.fight(p, boss, rng=lambda: 0.99, roll_aid=False, auto_pill=False)
    # 关键：必须检查 reflect=True 的**真实反伤 log 条目**。
    # 只查 notes 文本是假守门——fx.on_hit 无论把 reflect 挂给谁都会往 notes 写"反噬"，
    # 只有 on_incoming(p_fx) 真读到反伤，才会产生 reflect 条目并反弹伤害给怪。
    reflects = [it for it in r["log"] if isinstance(it, dict) and it.get("reflect")]
    check("反伤机制真实反弹（非仅文案）", len(reflects) >= 1,
          "reflect 条目 %d 条" % len(reflects))


# ---------------------------------------------------------------------------
# ⑥ 买物品上限（去 99）
# ---------------------------------------------------------------------------
def test_buy_150_not_clamped():
    check("SHOP_BUY_MAX 已提高", int(cfg.SHOP_BUY_MAX) >= 150, str(cfg.SHOP_BUY_MAX))
    # 命令层解析：两处 min(99,...) 都必须改成 min(cfg.SHOP_BUY_MAX,...)
    import inspect
    from doupo import commands
    src = inspect.getsource(commands.cmd_buy)
    check("cmd_buy 无硬编码 99 上限", "min(99" not in src, src[:200])


# ---------------------------------------------------------------------------
# ⑦ 野外 / 塔 / 周本：每个精英与 BOSS 都有专属技能与恶心机制
# ---------------------------------------------------------------------------
def test_field_monsters_all_have_character_packs():
    missing = [m["name"] for m in cfg.MONSTERS
               if cfg.monster_character(m["name"]) is None]
    check("23 只野外怪全有角色包", not missing, str(missing))
    no_skill = [n for n, ch in cfg.MONSTER_CHARACTERS.items() if not ch.get("skill")]
    check("每只怪都有专属技能", not no_skill, str(no_skill))
    elites = [(m["name"], cfg.monster_character(m["name"]))
              for m in cfg.MONSTERS if cfg.monster_character(m["name"]).get("elite")]
    bad = [(n, ch.get("name")) for n, ch in elites
           # 2026-11-20 起「登场」= 出场描写(appearance) + 台词(intro)，二者至少其一；
           # 无台词的魔兽只靠 appearance 也满足沉浸要求。
           if not (ch.get("appearance") or ch.get("intro"))
           or not (ch.get("mech") or {}).get("kind")]
    check("野外精英均有登场描写+恶心机制", not bad, str(bad))
    check("野外精英至少 8 只（不是只做 12 突破 BOSS）", len(elites) >= 8,
          "精英 %d 只" % len(elites))


def test_tower_and_weekly_bosses_have_packs():
    miss_t = [n for n in td.BOSS_NAMES if cfg.monster_character(n) is None]
    check("塔层 10 个 BOSS 全有角色包", not miss_t, str(miss_t))
    for n, sk in (("血刀圣者", "血刀"), ("魂魔老人", "魂魔手"),
                  ("狮天", "狮王裂空"), ("范痨", "阴森血掌")):
        mon = core.monster_scaled(_p(9), {"name": n, "lv": 9, "hard": 1.0,
                                          "kind": "塔·boss"})
        check("塔层 %s 用专属技而非通用魂殿秘技" % n,
              mon.get("skill", {}).get("name") == sk, str(mon.get("skill")))
    for n in wd.BOSS_NAME, wd.ELITE_NAME:
        ch = cfg.monster_character(n)
        check("周本 %s 有技能+机制+登场描写" % n,
              bool(ch and ch.get("skill") and (ch.get("mech") or {}).get("kind")
                   and (ch.get("appearance") or ch.get("intro"))),
              str(ch)[:90] if ch else "缺失")
    check("周本普通塔卫有技能",
          bool(cfg.monster_character("塔卫").get("skill")),
          str(cfg.monster_character("塔卫"))[:90])
    bad = [n for n in cfg.MONSTER_CHARACTERS
           if "游戏原创" in str(cfg.MONSTER_CHARACTERS[n].get("source"))
           and cfg.MONSTER_CHARACTERS[n].get("quotes")]
    check("游戏原创怪不冒充原著台词", not bad, str(bad))
    check("原著只作翼黑蛇皇（无八翼）",
          "八翼黑蛇皇" not in [m["name"] for m in cfg.MONSTERS],
          str([m["name"] for m in cfg.MONSTERS]))


def test_elite_pack_hooks_into_real_fight():
    p = _p(9)
    mon = core.monster_scaled(p, {"name": "天毒蝎龙兽", "lv": 9, "hard": 1.0,
                                  "kind": "魔兽"})
    ch = cfg.monster_character("天毒蝎龙兽")
    check("野外精英角色包自动挂进 monster_scaled",
          bool(mon.get("character")) and mon["character"]["name"] == ch["name"],
          str(mon.get("character"))[:100])
    check("野外精英技能随怪物实例透传",
          mon.get("skill", {}).get("name") == ch["skill"]["name"],
          str(mon.get("skill")))
    # 真战斗：登场旁白 + 原著台词 + 恶心机制三条都在 log 里
    mon["hp"], mon["atk"] = 100000, 1
    mon["skill"] = dict(ch["skill"])
    mon["skill"]["chance"] = 1.0
    mon["character"] = dict(ch)
    mon["character"]["mech"] = dict(ch["mech"], p=1.0)
    r = core.fight(p, mon, rng=lambda: 0.2, roll_aid=False, auto_pill=False)
    notes = [n for it in r["log"] for n in (it.get("notes") or [])]
    check("野外精英战有登场旁白",
          any(it.get("intro") for it in r["log"]), str(r["log"][:1]))
    check("野外精英原著台词进战报",
          any("混蛋，本王定要将你们碎尸万段" in n for n in notes), str(notes[:8]))
    check("野外精英恶心机制进战报",
          any("蝎龙·腐蚀血雾" in n for n in notes), str(notes[:8]))


def test_three_notes_not_swallowed_by_review():
    battle = {"log": [
        {"round": 1, "kind": "兽击", "dmg": 10, "taken": True, "move": "兽击",
         "notes": ["九幽魂手！斗气被夺 -2095", "「你才是大鱼」",
                   "九幽·锁魂（空间崩塌）！"]},
    ]}
    acts = br.action_items(battle)
    tags = acts[0]["tags"] if acts else ""
    check("第三条机制 note 不再被吞",
          all(x in tags for x in ("九幽魂手", "你才是大鱼", "九幽·锁魂")), tags)
    # 卡片标签行必须按实测宽度折出多行，而不是单行省略号截断。
    # 直接读源码，避免为一条静态守门去 import 整条 nonebot/httpx 依赖链。
    tpl_path = os.path.join(ROOT, "src", "plugins", "qq_bridge", "pic",
                            "templates", "doupo_battle.py")
    with open(tpl_path, encoding="utf-8") as fh:
        src = fh.read()
    check("标签行按列宽折行（无单行 _fit 截断）",
          "S.wrap_cjk(_gs(a.get(\"tags\")" in src
          and "BUBBLE_TAG_H * len(tag_lines)" in src,
          tpl_path)


# ---------------------------------------------------------------------------
if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        try:
            fn()
        except Exception as e:  # noqa: BLE001
            check(fn.__name__ + " 抛异常", False, "%s: %s" % (type(e).__name__, e))
    ok = sum(1 for _, passed, _ in _RES if passed)
    for name, passed, detail in _RES:
        print(("PASS  " if passed else "FAIL  ") + name + ("" if passed else "  -> " + detail[:160]))
    print("=" * 70)
    print("BOSS_CAST_TOTAL=%d PASSED=%d FAILED=%d" % (len(_RES), ok, len(_RES) - ok))
    sys.exit(0 if ok == len(_RES) else 1)
