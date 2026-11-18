# -*- coding: utf-8 -*-
"""斗破傀儡系统自检（2026-11-18）。exit 0 = 全绿。

覆盖用户硬要求：
  * 🚨 最高声明：傀儡数据全在 SQLite **真实列**，players.data 的 JSON 里
    不许出现任何 puppet 字段；
  * 五道资格（境界 / 灵魂境界 / 控火 / 异火 / 斗气续航）逐道可独立报红；
  * 首炼恒人级铜（原著事实，非材料定档）；
  * 战斗同源：fight 注入与 damage_schedule/monster_scaled 反解共用
    puppet_expected_damage，带傀儡怪血同步变厚；
  * 雷淬升档、结阵、收服无主傀儡、北王式融魂；
  * 遗迹日副本掉落进真实列、传承竹简落资格表、当日进度单调不倒退。

运行（镜像内，挂宿主源码）：
  /opt/venv/bin/python src/plugins/doupo/test_doupo_puppet.py
"""
from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))
os.chdir(ROOT)
sys.path.insert(0, ROOT)

_TMP = tempfile.mkdtemp(prefix="doupo_puppet_")
os.environ["DOUPO_DATA_PATH"] = os.path.join(_TMP, "doupo.json")
os.environ["GAME_MODE_PATH"] = os.path.join(_TMP, "game_mode.json")
os.environ["QQ_LOG_DIR"] = os.path.join(_TMP, "logs")
os.environ["QQ_DATA_DIR"] = os.path.join(_TMP, "qdata")

import nonebot  # noqa: E402

nonebot.init(driver="~fastapi", onebot_access_token="e2e", log_level="CRITICAL")
from nonebot.adapters.onebot.v11 import Adapter  # noqa: E402

nonebot.get_driver().register_adapter(Adapter)
nonebot.load_plugin("src.plugins.doupo")

import importlib  # noqa: E402

from src.plugins.doupo import commands as cmds  # noqa: E402
from src.plugins.doupo import config as cfg  # noqa: E402
from src.plugins.doupo import core  # noqa: E402
from src.plugins.doupo import puppet_core as pc  # noqa: E402

# __init__.py 里 store / puppet_store 是**实例名**，直接 from-import 会拿到单例而非模块
ps = importlib.import_module("src.plugins.doupo.puppet_store")
store_mod = importlib.import_module("src.plugins.doupo.store")

QQ = "73001"
DAY = "2026-11-18"
_RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    _RESULTS.append((name, bool(ok), str(detail)))


def _qualified() -> dict:
    """一具合格人级傀儡的持有者画像：七品炼药 + 20 次炼制 + 异火 + 斗宗 + 斗气足。"""
    p = core.create_player(QQ, "test_puppet", 900000099)
    p["lv"] = 64                       # 一星斗宗（index 7）
    p["alch"] = cfg.PUPPET_ALCH_MIN    # 七品
    p["craft_count"] = cfg.PUPPET_CRAFT_COUNT_MIN
    p["high_grade_crafts"] = cfg.PUPPET_HIGH_GRADE_MIN
    p["flames"] = {"青莲地心火": 4}
    p["gold"] = 200000
    p["mp"] = core.mp_max(p) * 3
    return p


def _mats(ps_store: ps.PuppetStore) -> dict:
    return {n: 3 for n in cfg.PUPPET_MATERIALS}


async def main() -> None:
    db = ps.PuppetStore(os.path.join(_TMP, "puppet_data.sqlite"))
    store = store_mod.Store(os.path.join(_TMP, "doupo.json"))
    await store.load()
    p = _qualified()
    await store.put_player(p)

    # ---- 1. 真实列 ----
    with db._tx() as con:
        puppets_cols = [r[1] for r in con.execute("PRAGMA table_info(puppets)")]
        elig_cols = [r[1] for r in con.execute("PRAGMA table_info(puppet_eligibility)")]
        mat_cols = [r[1] for r in con.execute("PRAGMA table_info(puppet_materials)")]
        ruin_cols = [r[1] for r in con.execute("PRAGMA table_info(puppet_ruin_daily)")]
        all_cols = puppets_cols + elig_cols + mat_cols + ruin_cols
    banned = [c for c in all_cols if "json" in c.lower() or c.lower() in ("data", "blob", "payload")]
    check("傀儡表无 JSON/blob 列", not banned, str(banned))
    need_cols = ("tier", "skin", "core_tier", "soul_strength", "metal_name",
                 "fire_tier", "purity", "bonded", "awakened", "damaged")
    check("傀儡本体全为真实字段列", all(c in puppets_cols for c in need_cols),
          "缺=%s" % [c for c in need_cols if c not in puppets_cols])
    check("材料表为一行一名一数量", set(mat_cols) >= {"qq", "name", "qty"}, str(mat_cols))

    # ---- 2. 五道资格逐道可报红 ----
    full = pc.puppet_gate(p, 0)
    check("合格画像过五道", full["ok"], str(full))
    cases = []
    for key, mutate in (
        ("境界", lambda d: d.update(lv=1)),
        ("灵魂境界", lambda d: d.update(alch=0)),
        ("控火", lambda d: d.update(craft_count=0)),
        ("异火", lambda d: d.update(flames={})),
        ("斗气", lambda d: d.update(mp=0)),
    ):
        bad = dict(p)
        mutate(bad)
        cases.append((key, pc.puppet_gate(bad, 0)))
    for key, res in cases:
        check("五道资格-%s 独立报红" % key,
              (not res["ok"]) and key in res["missing"], str(res))
    big = pc.gate_realm(p, 1)
    check("大斗师以下不得炼地级", (not pc.gate_realm({"lv": 28}, 1)[0]) and big[0],
          "大斗师=%s 斗宗=%s" % (pc.gate_realm({"lv": 28}, 1), big))

    # ---- 3. 首炼恒人级铜（原著事实）----
    high_fire = pc.make_puppet(
        p, {"body_name": "斗尊尸躯"}, {"core_tier": 8}, {"soul_strength": 9999},
        {"metal_name": "金", "metal_grade": 3}, {"fire_tier": 9})
    check("首炼恒人级铜，不被高档材料顶上去",
          high_fire["tier"] == 0 and high_fire["tier_name"] == "人级傀儡"
          and high_fire["skin"] == "铜", str((high_fire["tier"], high_fire["skin"])))

    # ---- 4. 未过资格 / 无传承无材料必须拦 ----
    p0 = dict(p)
    p0["lv"] = 1
    await store.put_player(p0)
    blocked = await cmds.cmd_craft_puppet(store, QQ, db)
    check("未过资格开炉被拦", "未能开炉" in blocked, blocked[:80])
    await store.put_player(p)
    no_lore = await cmds.cmd_craft_puppet(store, QQ, db)
    check("无竹简不得炼制", "竹简" in no_lore, no_lore[:80])
    await db.set_lore(QQ, True)
    no_mat = await cmds.cmd_craft_puppet(store, QQ, db)
    check("材料不足不得炼制", "材料不足" in no_mat, no_mat[:80])

    # ---- 5. 端到端炼制：材料落真实列、玩家 JSON 无傀儡字段 ----
    for n, q in _mats(db).items():
        await db.add_material(QQ, n, q)
    mp_before = int(p["mp"])
    made = await cmds.cmd_craft_puppet(store, QQ, db)
    roster = await db.list_puppets(QQ)
    check("炼制成功且落库", "炼制成功" in made and len(roster) == 1, made[:80])
    check("资格表 ready 置位", (await db.eligibility(QQ))["ready"], "")
    left = [await db.material_qty(QQ, n) for n in _mats(db)]
    cost_names = {"强者躯体", "火属性魔核", "无主灵魂", "暗金玄铁"}
    spend_ok = all(
        (int(q) == 2 if n in cost_names else int(q) == 3)
        for n, q in zip(_mats(db), left))
    check("材料按真实列扣除（仅扣四主材）", spend_ok, str(list(zip(_mats(db), left))))
    p_after = await store.get_player(QQ)
    check("斗气续航消耗", int(p_after["mp"]) < mp_before,
          "%d -> %d" % (mp_before, p_after["mp"]))
    with store._tx() as con:
        raw = con.execute("SELECT data FROM players WHERE qq=?", (QQ,)).fetchone()[0]
    obj = json.loads(raw)
    bad_keys = [k for k, v in obj.items()
                if "puppet" in k.lower()
                or "傀儡" in json.dumps(v, ensure_ascii=False)]
    check("最高声明：玩家 JSON 无任何傀儡字段", not bad_keys, str(bad_keys))
    check("傀儡本体取自真实列", roster[0]["core_tier"] == 7 and roster[0]["bonded"],
          str(roster[0])[:160])

    # ---- 6. 战斗同源 ----
    pu = await db.active_puppet(QQ)
    n = pc.formation_count(await db.list_puppets(QQ))
    bonus = pc.puppet_expected_damage(pu, n)
    base_sch = core.damage_schedule(p_after, 10)
    with_sch = core.damage_schedule(p_after, 10, puppet_bonus=bonus)
    diffs = [round(b - a, 4) for a, b in zip(base_sch, with_sch)]
    check("damage_schedule 每回合恰加傀儡期望输出",
          len(set(diffs)) == 1 and abs(diffs[0] - bonus) < 0.01,
          "bonus=%.2f diffs=%s" % (bonus, diffs[:3]))
    mon = {"name": "试炼傀儡卫", "lv": 64, "hard": 0.9, "exp_mul": 1.0,
           "gold_mul": 1.0, "kind": "测试"}
    hp_plain = int(core.monster_scaled(p_after, mon)["hp"])
    hp_pu = int(core.monster_scaled(p_after, mon, puppet_bonus=bonus)["hp"])
    check("带傀儡怪血同步变厚（反解同源）", hp_pu > hp_plain,
          "%d -> %d" % (hp_plain, hp_pu))
    fp = dict(p_after)
    res = core.fight(fp, core.monster_scaled(fp, mon, puppet_bonus=bonus),
                     puppet=pu, formation_n=n)
    hits = [x for x in res.get("log", []) if x.get("puppet")]
    check("fight 有傀儡击条目且回包标注",
          bool(hits) and res.get("puppet") == pu["name"],
          "hits=%d puppet=%s" % (len(hits), res.get("puppet")))
    src = open(os.path.join(ROOT, "src", "plugins", "doupo", "core.py"),
               encoding="utf-8").read()
    check("战斗注入与反解都只调唯一输出函数",
          "pc.puppet_attack" in src and "puppet_bonus" in src
          and "def puppet_expected_damage" not in src, "")

    # ---- 7. 结阵 / 雷淬 ----
    roster2 = await db.list_puppets(QQ)
    n2 = pc.formation_count(roster2 + roster2)
    check("结阵需≥2具且加伤", n2 == 2
          and pc.puppet_expected_damage(pu, n2) > pc.puppet_expected_damage(pu, 1),
          "n=%d" % n2)
    cur = roster[0]
    for _ in range(12):
        new = pc.temper(cur)
        cur = dict(cur, **new)
    check("雷淬 人→地→天且天级封顶",
          cur["tier"] == 2 and cur["skin"] == "金"
          and pc.temper(cur).get("capped") is True,
          "tier=%s purity=%s" % (cur["tier"], cur["purity"]))

    # ---- 8. 收服 / 融魂 ----
    wild_id = await db.add_wild_puppet({"name": "无主残傀"})
    found = await db.wild_puppets()
    check("遗迹残傀进无主池", any(x["id"] == wild_id for x in found), str(found)[:100])
    claimed = await db.claim_puppet(QQ, wild_id)
    got = await db.get_puppet(QQ, wild_id)
    check("收服改主+认主+修复", claimed and got and got["origin"] == "other"
          and got["bonded"] and not got["damaged"], str(got)[:120])
    check("重复收服失败不静默改坏", not await db.claim_puppet(QQ, wild_id), "")
    await db.add_material(QQ, "无主灵魂", 2)
    p2 = await store.get_player(QQ)
    awaken_txt = await cmds.cmd_puppet(store, QQ, "融魂", db)
    awakened = await db.active_puppet(QQ)
    check("北王式融魂生灵智",
          "熔魂" in awaken_txt and awakened["awakened"], awaken_txt[:80])

    # ---- 9. 遗迹日副本 ----
    st = await db.ruin_state(QQ, DAY)
    await db.ruin_clear(QQ, DAY, 2)
    await db.ruin_clear(QQ, DAY, 1)
    st2 = await db.ruin_state(QQ, DAY)
    check("当日进度单调不倒退", st["stage"] == 0 and st2["stage"] == 2, str(st2))
    p3 = await store.get_player(QQ)
    p3["hp"] = int(core.stats(p3)["体力"])
    p3["mp"] = core.mp_max(p3)
    await store.put_player(p3)
    ruin = await cmds.cmd_puppet_ruin(store, QQ, db)
    check("日副本战报有段名与判定",
          "傀儡遗迹" in ruin and ("胜" in ruin or "力竭" in ruin), ruin[:100])
    with db._tx() as con:
        n_mats = con.execute("SELECT COUNT(*) FROM puppet_materials WHERE qq=?",
                             (QQ,)).fetchone()[0]
    check("副本掉落进真实列", n_mats > 0, "material rows=%d" % n_mats)

    # ---- 10. 失败显式抛错 ----
    try:
        await db.update_puppet(QQ, roster[0]["id"], injected=1)
        raised = False
    except ps.PuppetStoreError:
        raised = True
    check("非法字段写入显式抛错", raised, "")


def _run() -> int:
    asyncio.run(main())
    ok = 0
    for name, passed, detail in _RESULTS:
        print(("PASS  " if passed else "FAIL  ") + name +
              ("" if passed else "  -> " + detail[:200]))
        ok += 1 if passed else 0
    print("=" * 70)
    print("PUPPET_TOTAL=%d PASSED=%d FAILED=%d" %
          (len(_RESULTS), ok, len(_RESULTS) - ok))
    return 0 if ok == len(_RESULTS) else 1


if __name__ == "__main__":
    sys.exit(_run())
