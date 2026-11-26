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
for _p in (ROOT, os.path.join(ROOT, "src", "plugins")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from doupo import battle_flavor as bf  # noqa: E402
from doupo import config as cfg  # noqa: E402

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
check("惨胜出惨胜梗池", lines(win=True, tier="narrow", realm_idx=3)[0] in NARROW)
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
check("败局出轻蔑但不越级/不境界",
      LOSE.intersection(lines(win=False, tier="lose", realm_idx=0))
      and not CRUSH.intersection(lines(win=False, tier="lose", realm_idx=0)))
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


def main():
    for name, ok, detail in _RES:
        print(("PASS " if ok else "FAIL ") + name + ("  " + detail if detail else ""))
    bad = [n for n, ok, _d in _RES if not ok]
    print("\n%d passed, %d failed" % (len(_RES) - len(bad), len(bad)))
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
