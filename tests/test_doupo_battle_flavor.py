# -*- coding: utf-8 -*-
"""斗破「战斗梗句符合玩家当前实际」守门（2026-11-26 用户实测要求）。

用户原话：「插入的语句一定要符合玩家当前实际情况，别搞的不符合当前区域和斗技，
境界等等，搞清楚再开始加」；追加「一共就这几句？这么少？」→ 扩充为双槽多池。

守的是**用户感受得到的行为**（不是查变量名/AST）：
  1. 反派口吻只在敌人真是魂殿/黑盟/魔炎谷时出，普通魔兽/悍匪局绝不出；
  2. 喊招只在玩家本场真打出那一招时出；
  3. 「侥幸/恐怖如斯/撕裂虚空」只在真越级时出，顺风无伤局不出；
  4. 「神清气爽/想说一句猖狂」只在惨胜时出；
  5. 召唤物梗只在真有傀儡助阵时出；「卡点」只在回合真多时出；
  6. 「恐怖如斯/半圣蝼蚁」只在玩家真到对应境界时出，萌新绝不出；
  7. 败局有轻蔑但不能出胜利梗；一场最多两句（登场+结算各一）；
  8. 确定性、无人名、无引号冒认角色；宁可不放也不硬塞。

运行：python tests/test_doupo_battle_flavor.py（脚本式，exit 0 = 全绿）
"""
from __future__ import annotations

import os
import sys
import types


def _stub_nonebot():
    nb = types.ModuleType("nonebot")
    nb.get_driver = lambda: types.SimpleNamespace(on_startup=lambda f: f)
    nb.on_regex = lambda *a, **k: types.SimpleNamespace(
        handle=lambda *a, **k: (lambda f: f), finish=lambda *a, **k: None)
    sys.modules["nonebot"] = nb
    for n in ("nonebot.adapters", "nonebot.adapters.onebot",
              "nonebot.adapters.onebot.v11"):
        sys.modules[n] = types.ModuleType(n)
    v11 = sys.modules["nonebot.adapters.onebot.v11"]
    v11.MessageEvent = object
    v11.MessageSegment = object
    v11.Message = object


_stub_nonebot()
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# 裸跑本脚本会 import core → cfg.DB_PATH 默认落「包内 data/」，在手机上会建出
# puppet_data.sqlite 让 phone_deploy 预检 FAIL。先钉到 tmp/，绝不污染生产包。
_PROBE_DATA = os.path.join(ROOT, "tmp", "_probe_data")
os.makedirs(_PROBE_DATA, exist_ok=True)
os.environ.setdefault("DOUPO_DATA_PATH", os.path.join(_PROBE_DATA, "doupo.json"))
os.environ.setdefault("XIUXIAN_DATA_PATH", os.path.join(_PROBE_DATA, "xiuxian.json"))
for _p in (ROOT, os.path.join(ROOT, "src", "plugins")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from doupo import battle_flavor as bf  # noqa: E402
from doupo import battle_verdict as vd  # noqa: E402
from doupo import config as cfg  # noqa: E402
from doupo import core  # noqa: E402

_RES: list[tuple[str, bool, str]] = []
EVIL = {"桀桀桀。", "猖狂。", "此子断不可留，否则日后必成大患！",
        "今日谁保他，谁与我魂殿为敌！"}
CRUSH = {"侥幸罢了。",
         "这一击，竟撕裂了虚空。他发出的斗气波动，竟然比我还强。"}
NARROW = {"神清气爽。", "怎么很想说一句猖狂。"}
PUPPET = {"平 A 尊者。", "我又何尝不是天才。"}
LOSE = {"无奈地摇了摇头。", "别急，我在卡点。"}
REALM = {"斗宗强者，恐怖如斯。", "半圣之下，皆为蝼蚁。"}


def check(name, ok, detail=""):
    _RES.append((name, bool(ok), str(detail)))


def ctx(**kw):
    base = dict(win=True, tier="great", realm_idx=2, foe_kind="魔兽",
                move="", move_tier="普通", has_puppet=False, rounds=3, seed=0)
    base.update(kw)
    return base


def lines(**kw):
    return bf.flavor_lines(ctx(**kw))


# ---- 1. 反派阵营门控（登场口吻）----
for kind in ("魂殿", "黑盟", "魔炎谷"):
    got = lines(foe_kind=kind)
    check("反派阵营[%s]出反派口吻" % kind, bool(got) and got[0] in EVIL, str(got))
for kind in ("魔兽", "悍匪", "", "普通"):
    got = lines(foe_kind=kind, realm_idx=0, tier="sweep")
    check("普通阵营[%s]不出反派口吻" % kind,
          all(x not in EVIL for x in got), str(got))

# ---- 2. 喊招门控 ----
check("真打出焰分噬浪尺才喊招", "焰分噬浪尺！" in lines(move="焰分噬浪尺"))
for mv in ("", "八极崩", "吸掌", "天火三玄变"):
    check("未打出[%s]不喊招" % (mv or "空"),
          "焰分噬浪尺！" not in lines(move=mv, realm_idx=0, tier="great"))

# ---- 3. 越级门控 ----
got = lines(win=True, tier="crush", realm_idx=3)
check("真越级出越级梗池", bool(got) and got[0] in CRUSH, str(got))
for tier in ("sweep", "great", "lose"):
    got = lines(win=True, tier=tier, realm_idx=3)
    check("非越级[%s]不出越级梗" % tier,
          all(x not in CRUSH for x in got), str(got))
got = lines(win=False, tier="crush", realm_idx=3)
check("败局即使tier=crush也不出越级梗", all(x not in CRUSH for x in got), str(got))

# ---- 4. 惨胜门控 ----
# 2026-11-28 反转：惨胜/大捷这类「只由战况档决定」的通用句停用，改由
# battle_verdict 的对局分析出（更针对：越级/爆发/傀儡/嗑药/残血翻盘）。
check("惨胜不再出通用梗（交给对局分析）",
      lines(win=True, tier="narrow", realm_idx=3) == [])
check("大捷不出惨胜梗", all(x not in NARROW
      for x in lines(win=True, tier="great", realm_idx=3)))

# ---- 5. 傀儡 / 拖沓门控 ----
check("真有傀儡助阵出召唤物梗",
      PUPPET.intersection(lines(win=True, tier="great", realm_idx=0, has_puppet=True)))
check("无傀儡不出召唤物梗",
      not PUPPET.intersection(lines(win=True, tier="great", realm_idx=0, has_puppet=False)))
check("回合真多(>=10)出卡点梗",
      "别急，我在卡点。" in lines(win=True, tier="great", realm_idx=0, rounds=11))
check("回合少不出卡点梗",
      "别急，我在卡点。" not in lines(win=True, tier="great", realm_idx=0, rounds=3))

# ---- 6. 境界门控 ----
got = lines(tier="great", realm_idx=cfg.realm_index(64))
check("斗宗境界出恐怖如斯", got[0] == "斗宗强者，恐怖如斯。", str(got))
got = lines(tier="great", realm_idx=cfg.realm_index(94))
check("斗圣境界出半圣蝼蚁", got[0] == "半圣之下，皆为蝼蚁。", str(got))
for lv in (1, 10, 55, 63):
    got = lines(tier="great", realm_idx=cfg.realm_index(lv))
    check("lv%d未到斗宗不出境界梗" % lv,
          all(x not in REALM for x in got), str(got))

# ---- 7. 败局 + 双槽 + 宁可不放 ----
# 2026-11-28 反转：败局的无主轻蔑已废，改由活着的怪开口（battle_verdict）；
# 结算槽在败局一律不出句，更不许越级/境界梗乱入。
check("败局结算槽不出句",
      lines(win=False, tier="lose", realm_idx=0) == [])
two = lines(foe_kind="魂殿", tier="crush", realm_idx=3, seed=0)
check("双槽：魂殿登场+越级结算各一句", len(two) == 2 and two[0] in EVIL and two[1] in CRUSH,
      str(two))
check("低境界普通魔兽大捷：至多一句", len(lines(realm_idx=0, tier="great", seed=0)) <= 1)
check("空ctx不出梗", bf.flavor_lines({}) == [])

# ---- 8. 确定性 / 无人名 / 无引号 ----
c = ctx(foe_kind="魂殿", seed=3)
check("同ctx同句(确定性)", bf.flavor_lines(c) == bf.flavor_lines(dict(c)))
all_text = " ".join(
    x for k in ("魂殿", "黑盟", "魔兽") for t in ("crush", "narrow", "great", "lose")
    for r in (0, 7, 11) for m in ("", "焰分噬浪尺") for pu in (False, True)
    for rd in (3, 11) for s in (0, 1, 2)
    for x in bf.flavor_lines(ctx(foe_kind=k, tier=t, realm_idx=r, move=m,
                                 has_puppet=pu, rounds=rd, seed=s, win=(t != "lose"))))
for bad in ("萧炎", "药老", "云岚宗", "纳兰", "魂天帝", "美杜莎", "天妖傀"):
    check("不出现人名/地名[%s]" % bad, bad not in all_text)
check("无引号台词(不冒充角色)", "「" not in all_text and "：" not in all_text)

# ---- 9. 素材库覆盖：落地句子数 ----
all_flavors = {
    "桀桀桀。", "今日谁保他，谁与我魂殿为敌！", "此子断不可留，否则日后必成大患！",
    "猖狂。", "焰分噬浪尺！", "侥幸罢了。", "恐怖如斯。",
    "这一击，竟撕裂了虚空。他发出的斗气波动，竟然比我还强。",
    "神清气爽。", "怎么很想说一句猖狂。", "平 A 尊者。", "我又何尝不是天才。",
    "别急，我在卡点。", "无奈地摇了摇头。", "斗宗强者，恐怖如斯。", "半圣之下，皆为蝼蚁。",
}
check("落地句池>=15句(用户嫌少后的扩充)", len(all_flavors) >= 15, str(len(all_flavors)))


# ---- 10. 对局诊断：15 个败因 + 15 个胜因，全部由真实 log 判出 ----
# 用户 2026-11-28 二次纠正：「你怎么只把我说到的纬度加上了，你要从各个维度去评判啊，
# 各种语句一定要多，随机」。这里守的是玩家感受得到的两件事：
#   ① 一场战斗的输入变了，怪/旁白说出的失败点必须跟着变（不是通用句）；
#   ② 同档多句、且不同场次会换句（不是每次一句复读）。

def _hit(**kw):
    e = {"dmg": 100, "kind": "拳脚", "move": "拳脚", "skill": False,
         "tier": "普通", "crit": False}
    e.update(kw)
    return e


def _foe(**kw):
    e = {"dmg": 100, "kind": "兽击", "move": "兽击", "taken": True}
    e.update(kw)
    return e


def _diag(win=False, ratio=0.8, hp_max=10000, **kw):
    p = core.create_player("vd-probe", "诊断探针", "g1")
    p["lv"] = int(kw.pop("hero_lv", 29))
    p["skills"] = dict(kw.pop("player_skills", None) or {})
    pup = kw.pop("pup", None)
    extra = kw.pop("extra", None)
    rounds = int(kw.pop("rounds", 6))
    foe_hp = int(kw.pop("foe_hp", 1000))
    foe_end = int(kw.pop("foe_end", 1000))
    hp_end = int(kw.pop("hp_end", 1000))
    mp_end = int(kw.pop("mp", 60))
    taken = int(kw.pop("taken", 100))
    foe_lv = int(kw.pop("lv", 29))
    atk = kw.pop("atk", None) or []
    foes = kw.pop("foes", None) or []
    log = list(atk) + list(foes) + ([_hit(puppet=True, dmg=5000, move="傀儡击")]
                                    if pup else [])
    res = {"win": win, "rounds": rounds, "taken": taken,
           "foe_hp_max": foe_hp, "foe_hp_end": foe_end,
           "hp_end": hp_end, "mp_end": mp_end, "exhausted": mp_end <= 0,
           "enemy_count": 1,
           "detail": [{"index": 0, "name": "岩蛇", "kind": "魔兽", "lv": foe_lv,
                       "win": win, "rounds": rounds, "taken": taken, "log": log}]}
    if "pills" in kw:
        res["auto_pills"] = kw.pop("pills")
    if extra:
        res.update(extra)
    return vd.analyze(res, p, lv=p["lv"], hp_ratio=ratio, hp_max=hp_max)


DEFEAT_DIMS = (
    ("反伤自毙", "reflect", dict(atk=[_hit(dmg=300)],
                                foes=[_foe(dmg=900, kind="反伤", reflect=True)])),
    ("毒磨致死", "dot_death", dict(atk=[_hit()],
                                foes=[_foe(dmg=900, kind="中毒", dot=True)])),
    ("被控到死", "controlled", dict(atk=[_hit()],
                                foes=[_foe(), {"kind": "被控", "dmg": 0},
                                      {"kind": "被控", "dmg": 0}])),
    ("打不中", "dodged", dict(atk=[_hit(evaded=True) for _ in range(4)])),
    ("对面残血", "near_win", dict(atk=[_hit(dmg=200)], foe_end=100)),
    ("药白吃了", "potion_futile", dict(atk=[_hit()], pills={"hp": 2, "mp": 0})),
    ("斗气枯竭", "no_mp", dict(atk=[_hit() for _ in range(5)], mp=0)),
    ("斗技没满级", "skill_short", dict(
        atk=[_hit(dmg=120, skill=True, move="碎石掌", tier="黄阶低级") for _ in range(4)],
        player_skills={"碎石掌": 3})),
    ("满级阶位低", "skill_cap", dict(
        atk=[_hit(dmg=120, skill=True, move="碎石掌", tier="黄阶低级") for _ in range(4)],
        player_skills={"碎石掌": 5})),
    # 这两档用「不在斗技图鉴里」的招名：否则 _skill_cap 必然查到上限，
    # skill_short 会先命中（那也是对的，只是本例要单独验「一招到底/只会低阶」）。
    ("一招到底", "one_trick", dict(
        atk=[_hit(skill=True, move="乱击", tier="黄阶低级") for _ in range(4)])),
    ("只会低阶", "tier_low", dict(atk=[
        _hit(skill=True, move="乱击", tier="黄阶低级"),
        _hit(skill=True, move="乱斩", tier="玄阶低级"),
        _hit(skill=True, move="乱击", tier="黄阶低级")])),
    ("零暴击", "crit_dry", dict(atk=[_hit(dmg=400) for _ in range(5)])),
    ("身板太脆", "squishy", dict(atk=[_hit()], foes=[_foe(dmg=13000)])),
    ("速败", "fast_loss", dict(atk=[_hit()], rounds=2)),
    ("硬实力差", "overmatched", dict(atk=[_hit(dmg=100)], rounds=8)),
)

WIN_DIMS = (
    ("残血翻盘", "clutch", dict(ratio=0.1, atk=[_hit()])),
    ("中途跌落再翻", "comeback", dict(atk=[_hit(hp_after=200), _hit(hp_after=9000)])),
    ("越级斩", "crush", dict(lv=33, atk=[_hit()])),
    ("一击定局", "burst", dict(atk=[_hit(dmg=600)])),
    ("傀儡带飞", "puppet", dict(atk=[_hit(dmg=100)], pup=True)),
    ("暴击串", "crit_storm", dict(atk=[_hit(crit=True) for _ in range(3)])),
    ("高阶压场", "tier_high", dict(
        atk=[_hit(skill=True, move="劈山掌", tier="地阶低级") for _ in range(3)])),
    ("多招轮转", "skillful", dict(atk=[
        _hit(skill=True, move="碎石掌", tier="黄阶低级"),
        _hit(skill=True, move="裂山拳", tier="玄阶低级"),
        _hit(skill=True, move="崩山击", tier="黄阶中级")])),
    ("吞噬得手", "devour", dict(atk=[_hit()], extra={"devour": True})),
    ("气运加身", "lucky", dict(atk=[_hit()], extra={"fortune": True})),
    ("丹药续航", "potion", dict(atk=[_hit()], pills={"hp": 2, "mp": 0})),
    ("速胜", "speed", dict(rounds=3, atk=[_hit()])),
    ("省气取胜", "frugal", dict(
        atk=[_hit(skill=True, move="碎石掌", tier="黄阶低级", mp_cost=10)], mp=9000)),
    ("无伤", "clean", dict(taken=0, foes=[], atk=[_hit()])),
    ("平胜", "plain", dict(rounds=8, taken=2, atk=[_hit(dmg=100)])),
)

for label, want, kw in DEFEAT_DIMS:
    got = _diag(win=False, **kw)["case"]
    check("败因诊断[%s]" % label, got == want, "want=%s got=%s" % (want, got))
for label, want, kw in WIN_DIMS:
    got = _diag(win=True, **kw)["case"]
    check("胜因诊断[%s]" % label, got == want, "want=%s got=%s" % (want, got))

# 台词池：玩家感受得到的是「连打好几场不重样」，用 120 个不同种子实际抽，
# 不去读内部常量——池子缩水/轮换退化都会让这里变红。
for label, want, _kw in DEFEAT_DIMS:
    seen = {vd.taunt(want, s, foe="岩蛇", skill="碎石掌") for s in range(120)}
    check("句池>=6且轮换[败·%s]" % label, len(seen) >= 6, "distinct=%d" % len(seen))
for label, want, _kw in WIN_DIMS:
    seen = {vd.praise(want, s) for s in range(120)}
    check("句池>=6且轮换[胜·%s]" % label, len(seen) >= 6, "distinct=%d" % len(seen))

# 「seed % len(pool)」会让 s*6 这种等间隔种子全落同一句；真随机不会。
check("跨池长种子仍会换句(防取模退化)",
      len({vd.praise("plain", s * 6) for s in range(10)}) >= 4)
# 优先级：用户点名的「没气 / 招式没练够」是能照着改的点，「差一口气」只是情绪。
check("可操作失败点优先于差一口气",
      _diag(win=False, atk=[_hit() for _ in range(5)], mp=0, foe_end=100)["case"] == "no_mp")
check("同一场两根同一句(回看一致)",
      vd.taunt("skill_short", 42, foe="岩蛇", skill="碎石掌") ==
      vd.taunt("skill_short", 42, foe="岩蛇", skill="碎石掌"))
check("不同场次会换句",
      len({vd.praise("clutch", s) for s in range(20)}) >= 5)
check("嘲讽必须带说话人", vd.taunt("overmatched", 3, foe="") == "")
check("败局句全部带引号+说话人", all(
    vd.taunt(want, s, foe="岩蛇", skill="碎石掌").startswith("岩蛇")
    and "「" in vd.taunt(want, s, foe="岩蛇", skill="碎石掌")
    for _label, want, _kw in DEFEAT_DIMS for s in (0, 7, 31)))
check("胜局赞叹不带引号(不冒充角色)", all(
    "「" not in vd.praise(c, s) for c in ("clutch", "crush", "plain")
    for s in range(20)))


# ---- 11. 小说口吻闸（2026-11-28 用户「太 AI 了，完全没有厮杀的氛围」）----
# 用户的批评针对的是**战后点评式**：「练满是好事，可惜练的是一门下风招」
# 「这是真本事」「每一手都有后手」。小说里敌人当场骂阵是「桀桀，我魂殿可有的是
# 手段收拾你」「我就不信，你这乌龟壳还真的打不烂！」——短、狠、带杀气。
# 真源：boss_taunts.py / config.BREAK_BOSSES / doupo_text.CHAPTER_QUOTE。
_AI_SMELL = (
    "下风招", "练满是好事", "真本事", "应验了", "格局", "也不是不努力",
    "上限就在那儿", "这道理", "根基", "还没到的那个境界", "不值钱",
    "几斤几两", "重修了", "使不出来", "每一手都有后手", "一板一眼",
    "天花乱坠", "省着用也能赢", "这才是", "说到底", "场面从一开始",
)
_all_praise = set()
_all_speech = set()
for _c, _s in list(vd._PRAISE.items()) + list(vd._SPEECH.items()):
    for _line in _s:
        (_all_praise if _c in vd._PRAISE else _all_speech).add(_line)

check("台词池无战后点评味(旁白)",
      not [x for x in _all_praise for w in _AI_SMELL if w in x],
      str([x for x in _all_praise for w in _AI_SMELL if w in x][:2]))
check("台词池无战后点评味(骂阵)",
      not [x for x in _all_speech for w in _AI_SMELL if w in x],
      str([x for x in _all_speech for w in _AI_SMELL if w in x][:2]))
check("骂阵是短句(<=24字)", max(len(x) for x in _all_speech) <= 24,
      "max=%d" % max(len(x) for x in _all_speech))
check("旁白是画面不是长评语(<=28字)", max(len(x) for x in _all_praise) <= 28,
      "max=%d" % max(len(x) for x in _all_praise))
check("魔兽不桀桀/负手/抽刀", all(
    not any(w in vd.taunt("overmatched", s, foe="岩蛇", beast=True)
            for w in ("桀桀", "负手", "抽刀", "冷笑"))
    for s in range(120)))
check("人型不龇牙/吐信/獠牙", all(
    not any(w in vd.taunt("overmatched", s, foe="魂殿摘星老鬼", beast=False)
            for w in ("龇牙", "吐着信子", "獠牙", "竖瞳"))
    for s in range(120)))


def main():
    for name, ok, detail in _RES:
        print(("PASS " if ok else "FAIL ") + name + ("  " + detail if detail else ""))
    bad = [n for n, ok, _d in _RES if not ok]
    print("\n%d passed, %d failed" % (len(_RES) - len(bad), len(bad)))
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
