# -*- coding: utf-8 -*-
"""九星突破 BOSS 战 + 火焰掉落自检（2026-11-19）。exit 0 = 全绿。

口径（用户原话）：
  * 「即将突破任意九星阶段时，触发战斗必定遇到 BOSS」；
  * 「九星突破必遇原著 BOSS，登场有引言台词，各有专属技能与恶心机制；别写快照玩法」；
  * 「挑战成功后概率获得异火或者兽火等其他火焰，兽火其他火焰概率更高」；
  * 「原著其他火焰全部补过来」（10 朵，考据 tmp/flames_other_research.md 20 条证据）。

运行：/opt/venv/bin/python src/plugins/doupo/test_nine_star_breakthrough.py
"""
from __future__ import annotations

import os
import random
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))
os.chdir(ROOT)
sys.path.insert(0, ROOT)

_TMP = tempfile.mkdtemp(prefix="doupo_ninebreak_")
os.environ["DOUPO_DATA_PATH"] = os.path.join(_TMP, "doupo.json")
os.environ["GAME_MODE_PATH"] = os.path.join(_TMP, "game_mode.json")
os.environ["QQ_LOG_DIR"] = os.path.join(_TMP, "logs")
os.environ["QQ_DATA_DIR"] = os.path.join(_TMP, "qdata")

import nonebot  # noqa: E402

nonebot.init(driver="~fastapi", onebot_access_token="e2e", log_level="CRITICAL")
from nonebot.adapters.onebot.v11 import Adapter  # noqa: E402

nonebot.get_driver().register_adapter(Adapter)
nonebot.load_plugin("src.plugins.doupo")

from src.plugins.doupo import config as cfg  # noqa: E402
from src.plugins.doupo import core  # noqa: E402
from src.plugins.doupo import flame_bonus as fb  # noqa: E402

_RES = []


def check(name, ok, detail=""):
    _RES.append((name, bool(ok), str(detail)))


def nine_player(qq="99001", lv=9):
    p = core.create_player(qq, "九星", 900000077)
    p["lv"] = lv
    p["exp"] = cfg.exp_need(lv)
    core._refill(p)
    return p


def main():
    # ---- 1. 其他火焰数据完整、原著有据 ----
    check("其他火焰 10 朵", len(cfg.OTHER_FLAMES) == 10, str(len(cfg.OTHER_FLAMES)))
    check("其他火焰名无重复且进存档表",
          len(cfg.OTHER_FLAME_NAMES) == 10
          and set(cfg.OTHER_FLAME_NAMES).issubset(set(cfg.OWNED_FLAME_NAMES)),
          str(cfg.OTHER_FLAME_NAMES))
    check("兽火 5 朵", sum(1 for f in cfg.OTHER_FLAMES if f["kind"] == "beast") == 5, "")
    check("每朵都有原著出处（章）——存在内部台账，不印给玩家",
          set(cfg.OTHER_FLAME_CITES) == set(cfg.OTHER_FLAME_NAMES)
          and all("章「" in v for v in cfg.OTHER_FLAME_CITES.values()),
          str([k for k, v in cfg.OTHER_FLAME_CITES.items() if "章「" not in v]))
    check("其他火焰 desc 不印章节（玩家可见处零考据）",
          not any("章「" in f["desc"] or "原著" in f["desc"] for f in cfg.OTHER_FLAMES),
          str([f["name"] for f in cfg.OTHER_FLAMES
               if "章「" in f["desc"] or "原著" in f["desc"]]))
    check("已知 23 异火不混进其他火焰",
          not (set(cfg.OTHER_FLAME_NAMES) & set(cfg.FLAME_TIER.keys())), "")

    # ---- 2. 九星突破判定 ----
    p9 = nine_player()
    check("lv9 经验满 = 九星突破点", core.is_nine_star_breakthrough(p9), str(p9["lv"]))
    p10 = nine_player(lv=10)   # 斗者一星（非九星）
    check("普通 upgrade 点不算九星", not core.is_nine_star_breakthrough(p10), "")
    p18 = nine_player(lv=18)   # 斗者九星
    check("lv18 也是九星突破点", core.is_nine_star_breakthrough(p18), "")
    poor = nine_player()
    poor["exp"] = 0
    check("经验不足不算（前置门槛）", not core.is_nine_star_breakthrough(poor), "")
    p93 = nine_player(lv=93)   # 半圣高阶→斗圣
    check("半圣 lv93 跨境界也算", core.is_nine_star_breakthrough(p93), "")

    # ---- 3. 原著 BOSS（2026-11-20）：内部快照锁难度 + 有名有姓 + 引言/台词/技能/机制 ----
    boss = core.make_breakthrough_boss(p9)
    species = {"name": "测试基准", "lv": 9, "hard": 1.0,
               "exp_mul": 1.0, "gold_mul": 1.0, "kind": cfg.BOSS_BREAK_KIND}
    baseline = core.monster_scaled(p9, species)
    check("BOSS 血量高于同阶基准", boss["hp"] > baseline["hp"],
          "%d > %d" % (boss["hp"], baseline["hp"]))
    check("BOSS 攻击高于同阶基准", boss["atk"] > baseline["atk"],
          "%d > %d" % (boss["atk"], baseline["atk"]))
    check("BOSS 标记 is_boss", boss["is_boss"] is True, "")
    # 用户澄清：快照要有（内部锁难度），只是不要发出来（对外文案零露出）
    check("内部保留 snapshot_power", int(boss.get("snapshot_power", 0)) == core.power(p9),
          str(boss.get("snapshot_power")))
    check("BOSS 走魂殿 kind（复用 tier4 掉落）",
          boss["kind"] == cfg.BOSS_BREAK_KIND, boss["kind"])
    ch = boss.get("character") or {}
    check("BOSS 带原著角色包", bool(ch) and bool(ch.get("source")) and bool(ch.get("intro")),
          str(ch)[:100])
    check("BOSS 带专属技能", bool(ch.get("skill")) and
          ch["skill"].get("name") == boss["skill"]["name"], str(ch.get("skill")))
    check("BOSS 带恶心机制", bool(ch.get("mech")) and
          ch["mech"].get("t") in ("silence", "stun", "reflect", "debuff_heal", "dot"),
          str(ch.get("mech")))
    # 12 档逐一可取，且不同境界不同 BOSS（不是一只心魔劫影打天下）
    names = [cfg.break_boss_for_lv(lv)["name"] for lv in (9, 18, 27, 36, 45, 54, 63, 72, 81, 90, 93, 102)]
    check("12 突破档配 12 个不同原著 BOSS", len(set(names)) == 12, str(names))

    # ---- 4. BOSS 战胜利 → level_up + 掉落落库 ----
    win = nine_player("99002")
    r = core.nine_star_breakthrough(win, rng=lambda: 0.01, locked=set())
    check("可战胜（rng 有利时 win）", r["win"] is True, str(r)[:120])
    check("胜利后突破到下一境界", r["leveled"] is True and r["new_lv"] == 10,
          str(r.get("new_lv")))
    check("胜利后确实掉落火焰", (r.get("flame") or {}).get("ok") is True, str(r.get("flame")))

    # ---- 5. 失败 → 不突破、经验不扣、不掉落 ----
    # lv9 BOSS 偏弱（新手不卡关），用 lv45（难度更高）找败局；清空背包排除服药续命。
    rl = None
    lose, exp0 = None, 0
    for seed in range(200):
        lose = nine_player("99003", lv=45)
        lose["bag"] = {}          # 无药，纯度数值
        exp0 = int(lose["exp"])
        rl = core.nine_star_breakthrough(lose, rng=random.Random(seed).random,
                                         locked=set())
        if not rl["win"]:
            break
    check("可战败（存在败局）", rl is not None and rl["win"] is False,
          str(rl)[:120] if rl else "no result")
    check("战败不突破", rl is not None and rl["leveled"] is False, "")
    check("战败经验不扣", lose is not None and int(lose["exp"]) == exp0,
          "%s vs %s" % (lose["exp"] if lose else None, exp0))
    check("战败不掉落", rl is not None and rl.get("flame") is None, "")

    # ---- 6. 掉落概率分层：兽火/其他 > 异火 ----
    from collections import Counter
    cnt = Counter()
    for i in range(1000):
        q = nine_player("98999")
        fr = core.roll_breakthrough_flame(q, rng=lambda i=i: ((i * 0.6180339) % 1.0))
        cnt[fr["kind"] or "none"] += 1
    check("其他火焰掉率 > 异火（用户口径）", cnt["other"] > cnt["bonus"],
          "other=%d bonus=%d none=%d" % (cnt["other"], cnt["bonus"], cnt["none"]))
    check("其他火焰掉率约 45%", 400 <= cnt["other"] <= 500, str(cnt["other"]))
    check("异火掉率约 18%", 130 <= cnt["bonus"] <= 230, str(cnt["bonus"]))

    # 融合新生火更稀有：在 other 池里占比低
    fusion_names = {f["name"] for f in cfg.OTHER_FLAMES if f["kind"] == "fusion"}
    got = Counter()
    for i in range(400):
        q = nine_player("98998")
        fr = core.roll_breakthrough_flame(q, rng=lambda i=i: ((i * 0.314159) % 1.0))
        if fr.get("name") in fusion_names:
            got[fr["name"]] += 1
    check("融合火在 other 池里更稀有", sum(got.values()) < 120, str(got))

    # ---- 7. 掉落写单一真值 p["flames"][name]，不新增字段 ----
    q = nine_player("98997")
    q["flames"] = {n: 0 for n in cfg.OWNED_FLAME_NAMES}
    fr = core.roll_breakthrough_flame(q, rng=lambda: 0.0)  # r=0 → other池首个
    check("掉落写 flames 单一真值", q["flames"].get(fr["name"]) == 1, str(fr))
    keys = set(core.create_player("98996", "键", 900000077).keys())
    check("不新增突破/掉落字段",
          not any(("break" in k or "boss" in k or "flame" in k and k != "flames")
                  and k not in ("flames",) for k in keys),
          str(sorted(k for k in keys if "break" in k or "boss" in k)))

    # 重复掉落折算药渣（不刷数值）
    q2 = nine_player("98995")
    q2["flames"] = {n: 0 for n in cfg.OWNED_FLAME_NAMES}
    core.add_item(q2, "药渣", 0)
    f1 = core.roll_breakthrough_flame(q2, rng=lambda: 0.0)
    dross0 = core.count_item(q2, "药渣")
    f2 = core.roll_breakthrough_flame(q2, rng=lambda: 0.0)
    check("重复掉落折算药渣不刷数值",
          f2.get("duplicate") is True and core.count_item(q2, "药渣") > dross0
          and q2["flames"][f1["name"]] == 1, str(f2))

    # ---- 8. 其他火焰加成进 stats / 炼药（单一源）----
    q3 = nine_player("98994")
    q3["flames"] = {n: 0 for n in cfg.OWNED_FLAME_NAMES}
    base_atk = core.stats(q3)["力量"]
    q3["flames"]["海魔炎"] = 1   # atk +4%
    boosted = core.stats(q3)["力量"]
    check("兽火 atk 加成进 stats", boosted > base_atk, "%d -> %d" % (base_atk, boosted))
    q4 = nine_player("98993")
    q4["flames"] = {n: 0 for n in cfg.OWNED_FLAME_NAMES}
    r0 = fb.alchemy_rate_bonus(q4)
    q4["flames"]["桃花火"] = 1  # alchemy +2%
    check("兽火 alchemy 加成进炼药", fb.alchemy_rate_bonus(q4) > r0,
          "%s -> %s" % (r0, fb.alchemy_rate_bonus(q4)))

    ok = sum(1 for _, passed, _ in _RES if passed)
    for name, passed, detail in _RES:
        print(("PASS  " if passed else "FAIL  ") + name + ("" if passed else "  -> " + detail[:160]))
    print("=" * 70)
    print("NINE_BREAK_TOTAL=%d PASSED=%d FAILED=%d" %
          (len(_RES), ok, len(_RES) - ok))
    return 0 if ok == len(_RES) else 1


if __name__ == "__main__":
    sys.exit(main())
