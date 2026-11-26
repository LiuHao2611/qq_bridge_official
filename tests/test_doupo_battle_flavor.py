# -*- coding: utf-8 -*-
"""斗破「战斗梗句符合玩家当前实际」守门（2026-11-26 用户实测要求）。

用户原话：「插入的语句一定要符合玩家当前实际情况，别搞的不符合当前区域和斗技，
境界等等，搞清楚再开始加」。

本文件守的是**用户感受得到的行为**（不是查变量名/AST）：
  1. 反派口吻只在敌人真是魂殿/黑盟/魔炎谷时出，普通魔兽/悍匪局绝不出；
  2. 喊招只在玩家本场真打出那一招时出，没打出绝不喊；
  3. 「侥幸」只在真越级时出，顺风无伤局不出；
  4. 「神清气爽」只在惨胜时出；
  5. 「斗宗强者恐怖如斯」只在玩家真到斗宗档时出，低境界萌新绝不出；
  6. 所有败局 / 普通刷怪默认不出梗（宁可不放，不硬塞）；
  7. 确定性：同 ctx 必出同句；
  8. 不出现人名、不冒充角色（无引号、无说话人前缀）。

运行：python tests/test_doupo_battle_flavor.py（脚本式，exit 0 = 全绿）
"""
from __future__ import annotations

import os
import sys
import types


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
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (ROOT, os.path.join(ROOT, "src", "plugins")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from doupo import battle_flavor as bf  # noqa: E402
from doupo import config as cfg  # noqa: E402

_RES: list[tuple[str, bool, str]] = []


def check(name, ok, detail=""):
    _RES.append((name, bool(ok), str(detail)))


def ctx(**kw):
    base = dict(win=True, tier="great", realm_idx=2, foe_kind="魔兽",
                move="", move_tier="普通", seed=0)
    base.update(kw)
    return base


# ---- 1. 反派阵营门控 ----
for kind in ("魂殿", "黑盟", "魔炎谷"):
    got = bf.flavor_line(ctx(foe_kind=kind))
    check("反派阵营[%s]出反派口吻" % kind,
          got in ("桀桀桀。", "猖狂。"), got)
for kind in ("魔兽", "悍匪", "", "普通"):
    got = bf.flavor_line(ctx(foe_kind=kind, realm_idx=0, tier="sweep"))
    check("普通阵营[%s]不出反派口吻" % kind,
          got not in ("桀桀桀。", "猖狂。"), got)

# ---- 2. 喊招门控（招式名真实存在，且本场真打出）----
got = bf.flavor_line(ctx(move="焰分噬浪尺"))
check("真打出焰分噬浪尺才喊招", got == "焰分噬浪尺！", got)
for mv in ("", "八极崩", "吸掌", "天火三玄变"):
    got = bf.flavor_line(ctx(move=mv, realm_idx=0, tier="great"))
    check("未打出[%s]不喊焰分噬浪尺" % (mv or "空"),
          got != "焰分噬浪尺！", got)

# ---- 3. 侥幸门控：真越级才出 ----
got = bf.flavor_line(ctx(win=True, tier="crush", realm_idx=3))
check("真越级出侥幸", "侥幸" in got, got)
for tier in ("sweep", "great", "lose"):
    got = bf.flavor_line(ctx(win=True, tier=tier, realm_idx=3))
    check("非越级[%s]不出侥幸" % tier, "侥幸" not in got, got)
got = bf.flavor_line(ctx(win=False, tier="crush", realm_idx=3))
check("败局即使tier=crush也不出侥幸", "侥幸" not in got, got)

# ---- 4. 神清气爽门控：惨胜才出 ----
got = bf.flavor_line(ctx(win=True, tier="narrow", realm_idx=3))
check("惨胜出神清气爽", got == "神清气爽。", got)
got = bf.flavor_line(ctx(win=True, tier="great", realm_idx=3))
check("大捷不出神清气爽", got != "神清气爽。", got)

# ---- 5. 斗宗境界门控 ----
got = bf.flavor_line(ctx(tier="great", realm_idx=cfg.realm_index(64)))
check("斗宗境界出恐怖如斯", got == "斗宗强者，恐怖如斯。",
      "realm_index(64)=%s got=%s" % (cfg.realm_index(64), got))
for lv in (1, 10, 55, 63):  # 斗之气/斗者/斗皇/斗皇九星，都未到斗宗(64)
    got = bf.flavor_line(ctx(tier="great", realm_idx=cfg.realm_index(lv)))
    check("lv%d未到斗宗不出恐怖如斯" % lv, got != "斗宗强者，恐怖如斯。",
          "idx=%s got=%s" % (cfg.realm_index(lv), got))

# ---- 6. 默认不出（宁可不放）----
quiet = ctx(win=False, tier="lose", realm_idx=0, foe_kind="魔兽", seed=0)
check("败局+低境界+普通魔兽：不出梗", bf.flavor_line(quiet) == "",
      bf.flavor_line(quiet))
check("空ctx不出梗", bf.flavor_line({}) == "")

# ---- 7. 确定性 ----
c = ctx(foe_kind="魂殿", seed=3)
check("同ctx同句(确定性)", bf.flavor_line(c) == bf.flavor_line(dict(c)))

# ---- 8. 无人名 / 不冒充角色 ----
all_text = " ".join(
    bf.flavor_line(ctx(foe_kind=k, tier=t, realm_idx=r, move=m, seed=s))
    for k in ("魂殿", "魔兽") for t in ("crush", "narrow", "great")
    for r in (0, 7) for m in ("", "焰分噬浪尺") for s in (0, 1)
)
for bad in ("萧炎", "药老", "云岚宗", "纳兰", "魂天帝", "美杜莎"):
    check("不出现人名/地名[%s]" % bad, bad not in all_text)
check("无引号台词(不冒充角色)",
      "「" not in all_text and "：" not in all_text, all_text)


def main():
    for name, ok, detail in _RES:
        print(("PASS " if ok else "FAIL ") + name + ("  " + detail if detail else ""))
    bad = [n for n, ok, _d in _RES if not ok]
    print("\n%d passed, %d failed" % (len(_RES) - len(bad), len(bad)))
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
