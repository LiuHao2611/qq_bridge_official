# -*- coding: utf-8 -*-
"""沉浸式文案门禁 + 技能效果真落地门禁（2026-11-20）。

用户原话（连说两遍）：
  「你是不是又在玩家能看到的地方加了一堆声明？比如原著xxx，原著里没有xxx？
   禁止再出现这样的说明，你这个完全对玩家游戏体验没有提升，
   而是非常影响玩家沉浸式体验。懂吗，懂吗。
   还有，我让你加技能，你不会只加了技能描述，技能效果都没落地战斗系统吧？
   还有boos有台词，也应该有出厂描述。」

本文件把三件事钉死，改坏必红：

A. **玩家可见文案零元说明**
   玩家看得见的字段（角色包 intro/title、出场描写、火焰 desc、斗技列表/详情、突破文案）
   一律不许出现「未载 / 不代编 / 游戏原创 / 游戏命名 / 语料 / 原文只有 / 原著无」
   这类开发向声明。``source`` / ``novel`` 是内部考据字段，不参与。

B. **登场描写真渲染**
   fight() 的登场 log 必须带 appearance（先看见「什么东西来了」），
   再接 intro（听他说第一句）。battle_review 与 cmd_break 都必须把它显示出来。

C. **技能效果真落地**（四个曾被写成"只改文案"的假机制）
   C1 poison 真挂 DoT（旧版只写 notes，玩家根本不掉血）
   C2 mech 的 dot 基数取本次伤害（旧版 base 写死 1，每回合掉 1 点）
   C3 debuff_atk 真降玩家输出（旧版只挂状态没人读）
   C4 debuff_heal 真压自动服药回血（旧版只压吸血，玩家主要靠吃药 → 无感）

运行：python tests/test_doupo_immersion_gate.py（脚本式，exit 0 = 全绿）
"""
from __future__ import annotations

import asyncio
import os
import re
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

from doupo import config as cfg, core, battle_review as br, card  # noqa: E402
from doupo import commands, skill_book as sb  # noqa: E402

#: 只在**声明"我们没有这个"**时才用的词。引文（第X章「标题」）不算。
#: 2026-11-20 第三轮：用户原话是「比如原著xxx，原著里没有xxx」，
#: 所以「原著」这两个字本身也要拦 —— 玩家不该在游戏里被提醒自己在玩改编。
META_WORDS = ("原著", "未载", "不代编", "代编", "游戏原创", "游戏命名", "游戏设定",
              "语料", "考据", "原文", "非原著角色", "本作", "台账", "设定集")
#: 内部考据字段，玩家看不到，不参与扫描。
INTERNAL_KEYS = {"source", "novel", "note"}

_RES = []


def check(name, ok, detail=""):
    _RES.append((name, bool(ok), str(detail)))


def _p(lv=1):
    p = core.create_player("QIMM", "测试", "群")
    p["lv"] = lv
    core._refill(p)
    return p


def _scan_deep(obj, label="", out=None):
    """递归扫玩家可见字符串：禁词 + 考据标签（[C1]/[G1]/[S2]…）。"""
    out = [] if out is None else out
    if isinstance(obj, dict):
        for k, v in obj.items():
            if str(k) in INTERNAL_KEYS:
                continue
            _scan_deep(v, "%s/%s" % (label, k), out)
    elif isinstance(obj, (list, tuple)):
        for i, v in enumerate(obj):
            _scan_deep(v, "%s[%d]" % (label, i), out)
    elif isinstance(obj, str):
        hits = _hit_meta(obj) + (["[标签]"] if re.search(r"\[[A-Z]\d+\]", obj) else [])
        if hits:
            out.append((label, hits, obj))
    return out


def _hit_meta(text: str) -> list[str]:
    t = str(text or "")
    return [w for w in META_WORDS if w in t]


def _scan(obj, path="", out=None):
    """递归找玩家可见字符串里的元说明（跳过 INTERNAL_KEYS）。"""
    out = [] if out is None else out
    if isinstance(obj, dict):
        for k, v in obj.items():
            if str(k) in INTERNAL_KEYS:
                continue
            _scan(v, "%s/%s" % (path, k), out)
    elif isinstance(obj, (list, tuple)):
        for i, v in enumerate(obj):
            _scan(v, "%s[%d]" % (path, i), out)
    elif isinstance(obj, str):
        bad = _hit_meta(obj)
        if bad:
            out.append("%s %s <<%s>>" % (path, bad, obj[:60]))
    return out


class _StubStore:
    """最小存档桩（cmd_* 只用 get_player/put_player/item_locks）。"""

    def __init__(self, p):
        self.p = p
        self.locks = set()

    async def get_player(self, qq):
        return self.p

    async def put_player(self, p):
        self.p = p

    async def item_locks(self, qq):
        return set(self.locks)


# ---------------------------------------------------------------------------
# A. 玩家可见文案零元说明
# ---------------------------------------------------------------------------
def test_config_player_text_has_no_meta():
    from doupo import factions as fac
    for label, obj in (("角色包", cfg.MONSTER_CHARACTERS),
                       ("突破BOSS", cfg.BREAK_BOSSES),
                       ("出场描写", cfg.CHARACTER_APPEARANCE),
                       ("异火", cfg.FLAMES),
                       ("数值异火", cfg.BONUS_FLAMES),
                       ("其他火焰", getattr(cfg, "OTHER_FLAMES", [])),
                       ("宗门传承异火/特殊斗技", fac.FACTIONS)):
        bad = _scan(obj)
        check("%s 玩家可见字段零元说明" % label, not bad,
              "%d 处：%s" % (len(bad), bad[:3]))


def test_appearance_quality():
    """出场描写不能是"一句话交代机制"，要有可看见的东西。"""
    short = [(k, v) for k, v in cfg.CHARACTER_APPEARANCE.items() if len(str(v)) < 12]
    check("出场描写都有实质内容（≥12字）", not short, str(short[:3]))
    no_period = [k for k, v in cfg.CHARACTER_APPEARANCE.items()
                 if not str(v).rstrip().endswith(("。", "！", "？"))]
    check("出场描写句读完整（句末有标点）", not no_period, str(no_period[:5]))
    # 所有"有机制的精英/BOSS"都必须有出场描写（玩家第一眼看到的东西）
    key_roles = [ch["name"] for ch in cfg.MONSTER_CHARACTERS.values()
                 if ch.get("elite")]
    missing = [n for n in key_roles if not cfg.character_appearance(n)]
    check("所有精英/BOSS 都有出场描写", not missing, str(missing))
    # 12 档突破 BOSS 一个都不能漏
    miss_break = [b["name"] for b in cfg.BREAK_BOSSES
                  if not cfg.character_appearance(b["name"])]
    check("12 档突破 BOSS 出场描写全覆盖", not miss_break, str(miss_break))


def test_cmd_outputs_has_no_meta():
    p = _p(30)
    p["faction"] = p.get("faction") or "tianming"
    store = _StubStore(p)
    out_skills = asyncio.run(commands.cmd_skills(store, "QIMM"))
    check("斗技列表零元说明", not _hit_meta(out_skills), _hit_meta(out_skills))
    check("斗技列表不再有〔自设〕标记", "〔自设〕" not in out_skills,
          out_skills[-160:])

    # 只测「玩家真能打开的那些斗技」：详情口令只对本宗/通用池里的技生效。
    pool = core.skill_pool(p)

    def _nov(n):
        return str((sb.SKILL_BOOK.get(n) or {}).get("novel") or "")

    absent = [e["name"] for e in pool if _nov(e["name"]).startswith("原著无载")]
    check("本宗池里确有取不到原文的斗技（反向验证样本）", bool(absent), str(absent))
    for nm in absent[:3]:
        out_info = asyncio.run(commands.cmd_skill_info(store, "QIMM", nm))
        check("斗技详情不印「原著无载」：%s" % nm, not _hit_meta(out_info),
              str(_hit_meta(out_info)))
        check("斗技详情不出现「原著：」空引文：%s" % nm,
              "原著：原著无载" not in out_info, out_info[-80:])

    cited = [e["name"] for e in pool
             if _nov(e["name"]).strip() and not _nov(e["name"]).startswith("原著无载")]
    check("本宗池里确有真引文斗技（反向验证样本）", bool(cited), str(cited[:3]))
    if cited:
        nm = cited[0]
        out_ok = asyncio.run(commands.cmd_skill_info(store, "QIMM", nm))
        # 2026-11-20 第三轮改向：章节引文（novel）是内部考据台账，玩家不需要在
        # 斗技详情里读「第X章「标题」」。所以这里反过来钉死：**不许印**。
        check("斗技详情不印章节引文：%s" % nm,
              not re.search(r"第[零〇一壹二三四五六七八九十百千\d]+章「", out_ok),
              out_ok[-120:])
        check("斗技详情不印「原著」字样：%s" % nm,
              "原著" not in out_ok, out_ok[-120:])
        # 但内容不能被"去元说明"顺手删空 —— 描述正文必须还在。
        desc = str((sb.SKILL_BOOK.get(nm) or {}).get("desc") or "")
        check("斗技详情仍完整保留效果描述：%s" % nm,
              bool(desc) and desc[:10] in out_ok, "%s||%s" % (desc[:20], out_ok[:80]))


def test_all_factions_render_clean():
    """25 个宗门逐一过：宗门卡数据 + 宗门/异火文本 + 每个特殊斗技详情。

    2026-11-20 第三轮补：玩家不只在"自己的宗门"看文案，转宗后能看到别派的
    传承异火与特殊斗技，所以必须**全派遍历**，不能只测一个天冥宗。
    """
    from doupo import factions as fac
    bad = []
    for fid, fdef in fac.FACTIONS.items():
        p = _p(60)
        p["faction"] = fid
        p["faction_name"] = fdef.get("name") or fid
        for label, obj in (("宗门卡", card.build_faction_data(p)),
                           ("宗门文本", asyncio.run(commands.cmd_faction(_StubStore(p), "QIMM", ""))),
                           ("异火文本", asyncio.run(commands.cmd_flames(_StubStore(p), "QIMM")))):
            for path, hits, txt in _scan_deep(obj, label):
                bad.append("%s/%s %s <<%s>>" % (fid, path, hits, txt[:40]))
    check("25 宗门逐一渲染零元说明", not bad, "；".join(bad[:3]))


def test_skill_source_cleaned():
    """斗技「出处」不许印考据来源（游戏自设 / 原著无载 / [C1] 这类）。"""
    samples = ["游戏自设，云岚宗宗门合击（原著无同名设定）。",
               "原著无载 · 游戏自设",
               "某派秘传（第一千三百章）[C1]",
               "迦南学院内院天焚炼气塔下封印，萧炎收服"]
    cleaned = [commands._clean_source(x) for x in samples]
    check("考据来源整行不印（返回空串）", cleaned[0] == "" and cleaned[1] == "",
          str(cleaned[:2]))
    check("考据标签被剥掉", "[C1]" not in cleaned[2], cleaned[2])
    check("正常出处照旧保留", cleaned[3].startswith("迦南学院"), cleaned[3])


def test_card_marks_has_no_meta():
    """卡面来源标记不许带〔自设〕（玩家不需要被告知"这招是补出来的"）。"""
    from doupo import card
    marks = [card._origin_mark({"origin": o, "core": True})
             for o in ("本宗", "通用", "异火")]
    check("卡面来源标记零元说明", not _hit_meta(" ".join(marks)), str(marks))
    check("卡面来源标记不含〔自设〕",
          all("〔自设〕" not in m for m in marks), str(marks))


def test_break_text_has_no_meta():
    """真跑一次突破，检查玩家实际看到的那段文案。"""
    p = _p(9)
    p["exp"] = cfg.exp_need(9)   # 修为拉满才走「九星突破 BOSS」分支
    p["faction"] = p.get("faction") or "tianming"
    store = _StubStore(p)
    out = asyncio.run(commands.cmd_break(store, "QIMM", rng=None))
    out = out if isinstance(out, str) else str(out)
    check("突破文案零元说明", not _hit_meta(out), _hit_meta(out))
    check("突破文案仍报出 BOSS 名号（没有为了去元说明删掉内容）",
          any(b["name"] in out for b in cfg.BREAK_BOSSES), out[:160])


# ---------------------------------------------------------------------------
# B. 登场描写真渲染
# ---------------------------------------------------------------------------
def test_appearance_wired_into_fight():
    ch = dict(cfg.BREAK_BOSSES[0])
    p = _p(9)
    mon = {"name": ch["name"], "lv": 9, "hp": 100000, "atk": 1, "def": 0,
           "exp": 1, "gold": 1, "kind": "魂殿", "is_boss": True,
           "skill": dict(ch["skill"]), "character": dict(ch)}
    r = core.fight(p, mon, rng=lambda: 0.99, roll_aid=False, auto_pill=False)
    intros = [it for it in r["log"] if isinstance(it, dict) and it.get("intro")]
    check("登场旁白进 log（intro=True）", bool(intros), "条数 %d" % len(intros))
    if intros:
        notes = [str(n) for n in (intros[0].get("notes") or [])]
        check("登场首条是出场描写（可见的形体/气势）",
              bool(notes) and notes[0] == cfg.character_appearance(ch["name"]),
              notes[:1])
        check("登场次条是台词（先看见，再听他说）",
              len(notes) >= 2 and ch["intro"][:6] in notes[1], notes[1:2])
        check("登场旁白不进伤害统计",
              int(intros[0].get("dmg") or 0) == 0, intros[0].get("dmg"))

    tpl = br.build_data({"log": intros})["intro"]
    check("回看卡引言块含出场描写",
          cfg.character_appearance(ch["name"])[:8] in str(tpl.get("quote") or ""),
          str(tpl.get("quote"))[:80])
    check("回看卡引言块含台词",
          ch["intro"][:6] in str(tpl.get("quote") or ""), str(tpl.get("quote"))[:80])


def test_appearance_applies_to_elite_prefix():
    """塔里叫「精英·穆蛇」，剥前缀后同样要挂上出场描写。"""
    ch = cfg.monster_character("精英·穆蛇")
    check("精英·前缀能取到角色包", bool(ch), str(ch)[:60])
    check("精英·前缀角色包带出场描写",
          bool(ch and ch.get("appearance")), str((ch or {}).get("appearance"))[:60])


# ---------------------------------------------------------------------------
# C. 技能效果真落地
# ---------------------------------------------------------------------------
def _mon(character, *, hp=100000, atk=1, kind="魂殿"):
    return {"name": character["name"], "lv": 9, "hp": int(hp), "atk": int(atk),
            "def": 0, "exp": 1, "gold": 1, "kind": kind, "is_boss": True,
            "skill": dict(character["skill"]), "character": dict(character)}


def _force(character, *, chance=1.0, mech_p=1.0):
    ch = dict(character)
    ch["skill"] = dict(ch["skill"])
    ch["skill"]["chance"] = chance
    if ch.get("mech"):
        ch["mech"] = dict(ch["mech"])
        ch["mech"]["p"] = mech_p
    return ch


def _player_dots(r):
    return [it for it in r["log"]
            if isinstance(it, dict) and it.get("dot") and it.get("taken")]


def test_poison_really_ticks():
    """C1：poison 技能要真挂 DoT（旧版只往 notes 写一行字，玩家血条不动）。"""
    # 选「技能=毒、机制≠毒」的怪：否则毒 DoT 会由机制那条路兜住，测不出技能真落地。
    ch = _force(cfg.MONSTER_CHARACTERS["天山雪蟒"])   # skill=poison, mech=debuff_atk
    r = core.fight(_p(9), _mon(ch, atk=200), rng=lambda: 0.99,
                   roll_aid=False, auto_pill=False)
    dots = _player_dots(r)
    check("中毒在玩家身上真实结算（dot 条目）", bool(dots), "条数 %d" % len(dots))
    check("中毒扣的是真实血量（dmg>0）",
          bool(dots) and all(int(d.get("dmg") or 0) > 0 for d in dots),
          str([d.get("dmg") for d in dots])[:60])


def test_mech_dot_uses_real_damage_base():
    """C2：mech 的 dot 基数取本次命中伤害（旧版 base=1 → 每回合掉 1 点 = 假机制）。"""
    # 选「机制=毒、技能≠毒」的怪：否则扣血的 DoT 来自技能，测不出机制基数。
    ch = _force(cfg.MONSTER_CHARACTERS["黑焰紫云雕"])   # skill=burst, mech=dot
    r = core.fight(_p(9), _mon(ch, atk=200), rng=lambda: 0.99,
                   roll_aid=False, auto_pill=False)
    dots = _player_dots(r)
    check("机制 DoT 真挂上玩家", bool(dots), "条数 %d" % len(dots))
    # 怪 atk=200，dealt 远大于 1；基数若仍写死 1，这里必红。
    # 只看**第一次**结算：每次命中都会再叠一层毒，取 max 会被"两层毒各 1 点"
    # 蒙混过去（合计 2 > 1 就绿了），故必须验单次 tick。
    check("机制 DoT 基数不是 1（按本次伤害算）",
          bool(dots) and int(dots[0].get("dmg") or 0) > 1,
          "首次 tick=%s 全部=%s" % (dots[0].get("dmg") if dots else None,
                                     [d.get("dmg") for d in dots]))


def _avg_dealt_after_round1(r):
    """玩家第 2 回合起的**单次平均**伤害。

    为什么不比总伤害：怪血固定，两组都会在打死怪那一刻停手，总额≈怪血量，
    差异被"最后一击溢出"抹平（实测 99848 vs 99750，方向还反了）。
    """
    hits = [int(it.get("dmg") or 0) for it in r["log"]
            if isinstance(it, dict) and not it.get("taken")
            and not it.get("dot") and not it.get("intro")
            and not it.get("aid") and int(it.get("round") or 0) >= 2]
    return (sum(hits) / len(hits)) if hits else 0.0


def test_debuff_atk_really_lowers_output():
    """C3：BOSS 的 debuff_atk 要真降玩家输出（旧版挂上了但没人读）。"""
    base = dict(cfg.MONSTER_CHARACTERS["蝎山"])       # mech=debuff_atk
    plain = dict(base)
    plain["mech"] = None
    a = _avg_dealt_after_round1(core.fight(_p(9), _mon(_force(base), atk=1),
                                           rng=lambda: 0.99, roll_aid=False,
                                           auto_pill=False))
    b = _avg_dealt_after_round1(core.fight(_p(9), _mon(dict(plain), atk=1),
                                           rng=lambda: 0.99, roll_aid=False,
                                           auto_pill=False))
    # 18% 降攻 → 单次平均应明显低一截，只差个位数说明机制没生效。
    check("降攻真降玩家输出（同 rng 对比单次均伤）", a < b * 0.95,
          "带降攻 %.1f vs 无 %.1f" % (a, b))


def test_debuff_heal_really_cuts_pills():
    """C4：BOSS 的 debuff_heal 要真压自动服药（旧版只压吸血 → 减疗无感）。"""
    pill = next((r["name"] for r in cfg.RECIPES if str(r.get("effect")) == "hp"), None)
    check("存在可自动服用的回血丹（测试前提）", bool(pill), str(pill))

    def run(with_mech: bool) -> int:
        ch = dict(cfg.MONSTER_CHARACTERS["血刀圣者"])   # mech=debuff_heal
        if not with_mech:
            ch["mech"] = None
        p = _p(9)
        p["bag"] = {pill: 20}
        r = core.fight(p, _mon(_force(ch), atk=120), rng=lambda: 0.99,
                       roll_aid=False, auto_pill=True)
        gains = [int(it.get("amount") or 0) for it in r["log"]
                 if isinstance(it, dict) and it.get("kind") == "服药"]
        return max(gains) if gains else 0

    a, b = run(True), run(False)
    check("减疗真压低自动服药回血", 0 < a < b, "带减疗 %d vs 无 %d" % (a, b))


# ---------------------------------------------------------------------------
def main() -> int:
    for fn in (test_config_player_text_has_no_meta,
               test_appearance_quality,
               test_cmd_outputs_has_no_meta,
               test_all_factions_render_clean,
               test_skill_source_cleaned,
               test_card_marks_has_no_meta,
               test_break_text_has_no_meta,
               test_appearance_wired_into_fight,
               test_appearance_applies_to_elite_prefix,
               test_poison_really_ticks,
               test_mech_dot_uses_real_damage_base,
               test_debuff_atk_really_lowers_output,
               test_debuff_heal_really_cuts_pills):
        try:
            fn()
        except Exception as e:  # 单条炸掉也要出报告，不能静默
            import traceback
            check("%s 未抛异常" % fn.__name__, False,
                  "%s: %s" % (type(e).__name__, e))
            traceback.print_exc()

    failed = [r for r in _RES if not r[1]]
    for name, ok, detail in _RES:
        mark = "PASS" if ok else "FAIL"
        extra = ("  -> %s" % detail) if (detail and not ok) else ""
        print("%s  %s%s" % (mark, name, extra))
    print("=" * 70)
    print("IMMERSION_TOTAL=%d PASSED=%d FAILED=%d"
          % (len(_RES), len(_RES) - len(failed), len(failed)))
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
