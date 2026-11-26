# -*- coding: utf-8 -*-
"""傀儡护主挡伤守门（2026-11-20 第三轮）。

用户原话（本轮指令）：
  「战斗中，任意回合内如果将要收到大额伤害，傀儡有较低概率帮忙抵挡伤害，
    傀儡要有血量和护甲，越高级的傀儡，帮忙抵挡伤害的概率越高，同时血量和
    护甲也越高。……优化傀儡协助战斗逻辑，既不能抢了玩家角色风头，
    傀儡也不能发挥不出作用」

原著依据（逐条见 docs/傀儡护主挡伤_原著考据与机制调研_2026-11-20.md）：
  * 第898处「连地妖傀都是被其一抓下，差点彻底报废……恐怕一捏下，就得爆成一堆血肉」
  * 第920处「抗一两道五色丹雷或许还行，可想要靠它度过丹雷，无疑是痴人说梦」
  * 第952处「被轰击得千疮百孔……频临报废」「已经不能够再用来抗敌」

本文件逐条守的四件事（缺一条这个机制就残）：
  1. 三档单调：概率↑、气血↑、护甲↑（用户明确要的三条）；
  2. 「大额」门槛真的起作用 —— 小擦伤一次都不许触发；
  3. 挡伤是**减伤不是免疫** —— 玩家必须仍吃到 ≥ leak 比例的伤害；
  4. 不带傀儡的战斗一次 rng 都不许多掷（否则 218 条逐招回看快照整片翻红）。

自带 runner，零依赖 pytest：python tests/test_doupo_puppet_guard.py
注入反证：tmp/mut_pup_guard.py 逐条改坏源码，对应断言必须 FAIL。
"""
from __future__ import annotations

import ast
import copy
import os
import re
import random
import sys
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

from doupo import config as cfg            # noqa: E402
from doupo import core                      # noqa: E402
from doupo import puppet_core as pc         # noqa: E402
from doupo import battle_review as br       # noqa: E402
from doupo import card                      # noqa: E402

_FAILS = []
_N = 0


def check(cond, msg):
    global _N
    _N += 1
    if not cond:
        _FAILS.append(msg)


PU_REN = {"name": "铜尸傀", "tier": 0, "core_tier": 7, "soul_strength": 900,
          "metal_grade": 2, "fire_tier": 7, "skin": "铜"}
PU_DI = {"name": "地妖傀", "tier": 1, "core_tier": 7, "soul_strength": 900,
         "metal_grade": 2, "fire_tier": 7, "skin": "银"}
PU_TIAN = {"name": "天妖傀", "tier": 2, "core_tier": 7, "soul_strength": 900,
           "metal_grade": 2, "fire_tier": 7, "skin": "金"}
TIERS = (PU_REN, PU_DI, PU_TIAN)

SPEC = {"name": "同阶怪", "hard": 1.0, "exp_mul": 1.0, "gold_mul": 1.0,
        "kind": "普通", "lv": 81}


def _p(lv=81):
    p = core.tower_ref_player(lv)
    p = dict(p)
    p["hp"] = core.stats(p)["体力"]
    return p


def _guarded_logs(log):
    return [x for x in (log or []) if (x or {}).get("puppet_guard")]


def _kinds(log):
    return [str((x or {}).get("kind") or "") for x in (log or [])]


def _hp_left(it):
    """读 guard_hp_left，**不能用 ``or -1``**：0 是 falsy，会被吞成 -1，
    「傀儡被打空」这一整条就永远匹配不到（本测试自己踩过一次）。"""
    v = (it or {}).get("guard_hp_left")
    return None if v is None else int(v)


# ---------------------------------------------------------------------------
# 1. 三档单调（用户明确要求的三条）
# ---------------------------------------------------------------------------
def test_tier_monotonic():
    """越高级 → 挡伤概率更高、气血更高、护甲更高。三条都要，缺一条就是漏需求。"""
    p = _p(81)
    profs = [pc.guard_profile(pu, p, 1) for pu in TIERS]
    for key in ("chance", "hp_max", "armor"):
        vals = [g[key] for g in profs]
        check(vals[0] < vals[1] < vals[2],
              "护主 %s 未随档位递增：%s（用户明确要求「越高级越高」）"
              % (key, vals))
    # 「较低概率」：人级不能高过两成，天级也不能接近必中
    check(profs[0]["chance"] <= 0.20,
          "人级挡伤概率 %.2f 已不算「较低概率」" % profs[0]["chance"])
    check(profs[2]["chance"] <= float(cfg.PUPPET_GUARD_CAP),
          "天级挡伤概率越过 CAP")
    # 气血/护甲必须比玩家本人薄 —— 否则血条是纯展示，挡伤变永动护盾
    st = core.stats(p)
    check(profs[2]["hp_max"] < int(st["体力"]),
          "天级傀儡气血 %d ≥ 玩家 %d，比主人还厚" % (profs[2]["hp_max"], int(st["体力"])))


def test_armor_is_reverse_solved_from_take():
    """护甲必须是「按设计穿透率反解」出来的，而不是随玩家防御线性放大。

    反解自洽：``mitigation(armor)`` 应等于 ``PUPPET_ARMOR_TAKE[tier]``（材料系数
    为 1 时精确相等，否则同向偏移）。这条抓住的坑很具体：2026-11-20 之前用
    ``玩家防御 × ratio``，玩家防御 15.7 倍跨度让减伤率从 19.9% 漂到 79.7%，
    lv81 天级要挡 33.6 次才报废 —— 血量与护甲整栏是死数据。
    """
    p = _p(81)
    for tier, pu in enumerate(TIERS):
        g = pc.guard_profile(pu, p, 1)
        take = core.mitigation(g["armor"])
        want = float(cfg.PUPPET_ARMOR_TAKE[tier])
        check(abs(take - want) < 0.02,
              "tier%d 护甲反解跑偏：mitigation(%d)=%.3f 应为 %.3f"
              % (tier, g["armor"], take, want))
    # 旧口径常量必须彻底消失，防止有人「修回去」
    check(not hasattr(cfg, "PUPPET_ARMOR_RATIO"),
          "cfg 里又出现了 PUPPET_ARMOR_RATIO —— 高等级护甲会再次爆表（见 config 注释）")


def test_durability_stable_across_levels():
    """同一档傀儡在各等级「挡几次报废」应大致一致（不随等级漂移）。

    这是「护甲反解」的存在理由：若按玩家防御比例放大，lv10 挡 2.5 次、
    lv81 挡 33.6 次，同一具傀儡的行为完全取决于主人等级，玩家无从预期。
    """
    for lv in (10, 50, 81):
        p = _p(lv)
        st = core.stats(p)
        mon = core.monster_scaled(p, dict(SPEC, lv=lv))
        raw = max(1, int(mon["atk"] * core.mitigation(st["防御"])))
        for tier, pu in enumerate(TIERS):
            g = pc.guard_profile(pu, p, 1)
            mig = core.mitigation(g["armor"])
            soak = min(max(0, int(raw * (1.0 - g["leak"]))),
                       int(g["hp_max"] / max(1e-6, mig)))
            taken = max(1, int(soak * mig))
            n = g["hp_max"] / max(1, taken)
            check(0.8 <= n <= 12.0,
                  "lv%d tier%d 要挡 %.1f 次才报废 —— 超出可感区间" % (lv, tier, n))


# ---------------------------------------------------------------------------
# 2. 「大额」门槛真的起作用
# ---------------------------------------------------------------------------
def test_only_big_hit_triggers():
    """小擦伤一次都不许触发。门槛失守 = 护主变成每回合都在挡 = 抢风头。"""
    p = _p(81)
    mon = core.monster_scaled(p, dict(SPEC, lv=81))
    # 把攻击压到 1/20：单次伤害远低于 threshold（气血上限 × 6%）
    mon["atk"] = max(1, int(mon["atk"]) // 20)
    g = pc.guard_profile(PU_TIAN, p, 1)
    hit_raw = max(1, int(mon["atk"] * core.mitigation(core.stats(p)["防御"])))
    check(hit_raw < g["threshold"],
          "构造失败：这一下 raw=%d 并没低于门槛 %d" % (hit_raw, g["threshold"]))
    fired = 0
    for seed in range(25):
        r = core.fight(_p(81), mon, rng=random.Random(seed).random,
                       puppet=PU_TIAN, formation_n=1, auto_pill=True)
        fired += int((r.get("puppet_guard") or {}).get("count") or 0)
    check(fired == 0, "小擦伤也触发了护主 %d 次 —— 门槛形同虚设" % fired)


def test_threshold_is_configured_not_zero():
    g = pc.guard_profile(PU_REN, _p(81), 1)
    check(g["threshold"] > 0, "大额门槛为 0 —— 任何伤害都会掷骰")
    check(0.0 < float(cfg.PUPPET_GUARD_THRESHOLD) < 1.0,
          "PUPPET_GUARD_THRESHOLD 越界：%s" % cfg.PUPPET_GUARD_THRESHOLD)


# ---------------------------------------------------------------------------
# 3. 挡伤是减伤，不是免疫
# ---------------------------------------------------------------------------
def test_guard_is_not_immunity():
    """挡下后玩家仍须吃到 ≥ leak 比例的伤害（否则就是抢风头）。"""
    leak = float(cfg.PUPPET_GUARD_LEAK)
    check(0.0 < leak < 1.0, "LEAK=%s 越界（0=免疫，1=等于没挡）" % leak)
    seen = 0
    for seed in range(60):
        r = core.fight(_p(81), core.monster_scaled(_p(81), dict(SPEC, lv=81)),
                       rng=random.Random(seed).random,
                       puppet=PU_REN, formation_n=1, auto_pill=True)
        for e in _guarded_logs(r.get("log")):
            seen += 1
            raw = int(e.get("guard_raw") or 0)
            dmg = int(e.get("dmg") or 0)
            check(dmg >= max(1, int(raw * leak)),
                  "护主把伤害挡成 %d（原始 %d），低于 leak=%.2f 的下限"
                  % (dmg, raw, leak))
            check(dmg > 0, "护主后玩家实收 0 —— 变成免疫了")
        if seen >= 3:
            break
    check(seen > 0, "60 场都没抓到一次护主 —— 概率或门槛有问题")


def test_puppet_actually_loses_hp():
    """傀儡必须真的掉血。掉不动血 = 「血量和护甲」是死数据。"""
    dropped = 0
    for seed in range(60):
        r = core.fight(_p(81), core.monster_scaled(_p(81), dict(SPEC, lv=81)),
                       rng=random.Random(seed).random,
                       puppet=PU_REN, formation_n=1, auto_pill=True)
        pg = r.get("puppet_guard") or {}
        if int(pg.get("count") or 0) > 0:
            dropped += int(pg.get("taken") or 0)
            check(int(pg.get("hp_left")) < int(pg.get("hp_max")),
                  "护主后傀儡余血 == 上限 —— 根本没掉血")
            check(int(pg.get("taken")) > 0, "护主记录自损为 0")
        if dropped > 0:
            break
    check(dropped > 0, "跑不到一次带自损的护主")


# ---------------------------------------------------------------------------
# 4. 傀儡被打废后不再护、也不再打（原著第952处「不能够再用来抗敌」）
# ---------------------------------------------------------------------------
def test_broken_puppet_stops_working():
    p = _p(60)
    st = core.stats(p)
    # 一击足以把 tier0 傀儡打空：raw 取到 hp_max/take/leak 之上
    g = pc.guard_profile(PU_REN, p, 1)
    mig = core.mitigation(g["armor"])
    need = int(g["hp_max"] / max(1e-6, mig) / max(1e-6, 1.0 - g["leak"])) + 1
    mon = core.monster_scaled(p, dict(SPEC, lv=60))
    mon["atk"] = max(1, int(need / max(1e-6, core.mitigation(st["防御"]))) + 1)
    hit = False
    for seed in range(60):
        r = core.fight(_p(60), copy.deepcopy(mon), rng=random.Random(seed).random,
                       puppet=PU_REN, formation_n=1, auto_pill=True)
        log = r.get("log") or []
        if not any(_hp_left(x) == 0 for x in log):
            continue
        hit = True
        # ⚠️ 每一场都要查，不能「查到第一场就 break」：傀儡被打空往往正好发生在
        #    最后一个回合，那种场次之后本来就没有傀儡击，break 掉会漏掉真正
        #    「打空了还在打」的场次（注入反证 #8 白守就是这么来的）。
        idx = max(i for i, x in enumerate(log) if _hp_left(x) == 0)
        later = [x for x in log[idx + 1:] if (x or {}).get("kind") == "傀儡击"]
        check(not later,
              "seed%d 傀儡已被打废，之后却还打出 %d 次「傀儡击」" % (seed, len(later)))
        pg = r.get("puppet_guard") or {}
        check(pg.get("broken") is True,
              "seed%d 傀儡血已归零，res.puppet_guard.broken 却是 %s"
              % (seed, pg.get("broken")))
        check(int(pg.get("hp_left") or 0) == 0, "seed%d 打空后 hp_left 未归零" % seed)
    check(hit, "60 场都没把傀儡打空 —— BOSS 级一击的 cap 分支没被覆盖")
    # 顺带钉死「余 1 血僵尸态」：任何一场都不许停在 hp_left==1 还继续护主
    for seed in range(20):
        r = core.fight(_p(60), copy.deepcopy(mon), rng=random.Random(seed).random,
                       puppet=PU_REN, formation_n=1, auto_pill=True)
        pg = r.get("puppet_guard") or {}
        check(int(pg.get("hp_max") or 0) > 0, "puppet_guard 缺 hp_max")
        if int(pg.get("hp_left") or 0) == 1:
            check(False, "傀儡停在 1 点血 —— int() 截断导致「刚好吸满」永远差 1，"
                         "报废门失效（seed=%d）" % seed)


# ---------------------------------------------------------------------------
# 5. 不带傀儡：res / log / rng 全部零变化（红线）
# ---------------------------------------------------------------------------
def test_no_puppet_adds_nothing():
    p = _p(50)
    r = core.fight(_p(50), core.monster_scaled(p, dict(SPEC, lv=50)),
                   rng=random.Random(1234).random, auto_pill=False)
    check("puppet_guard" not in r,
          "没带傀儡却多出 res['puppet_guard'] 键 —— 会打破所有 res 结构断言")
    check(not _guarded_logs(r.get("log")),
          "没带傀儡却打出护主标记")
    check("傀儡击" not in _kinds(r.get("log")),
          "没带傀儡却打出「傀儡击」")


def test_no_puppet_fixed_scenario_baseline():
    """固定场景快照：不带傀儡的战斗结果必须与护主上线前逐字节一致。

    ⚠️ 这三个数是**故意写死**的：``taken`` / ``rounds`` / ``log`` 条数对 rng
    序列极度敏感，只要有人在「无傀儡路径」上多掷一次骰子，这里立刻变红 ——
    这正是我们要的（``test_battle_review`` 的 218 条逐招快照守的是同一件事，
    但那些用例不会主动跑「护主」这条分支）。

    若确属**有意**改动了战斗数值/回合结算，请同步更新这三个数并在提交说明里
    写清原因；不要为了让它变绿就把断言删掉。
    """
    p = _p(50)
    r = core.fight(_p(50), core.monster_scaled(p, dict(SPEC, lv=50)),
                   rng=random.Random(1234).random, auto_pill=False)
    check(int(r["rounds"]) == 4, "固定场景回合数变了：%s（疑似无傀儡路径多掷了 rng）"
          % r["rounds"])
    check(int(r["taken"]) == 5439, "固定场景承伤变了：%s" % r["taken"])
    check(len(r["log"]) == 10, "固定场景日志条数变了：%d" % len(r["log"]))


# ---------------------------------------------------------------------------
# 6. 卡面与实战同源（唯一口径）
# ---------------------------------------------------------------------------
def test_card_shows_guard_panel():
    p = _p(81)
    data = card.build_puppet_data([dict(PU_REN, id=1), dict(PU_TIAN, id=2)],
                                  {"active_id": 1}, p)
    blob = "\n".join("\n".join(x["lines"]) for x in data["puppets"])
    check("护主" in blob, "傀儡卡没有护主面板")
    check("气血" in blob and "护甲" in blob and "挡伤" in blob,
          "傀儡卡缺气血/护甲/挡伤中的某一项：%s" % blob)
    # 卡面数字必须等于 guard_profile 的返回值（不是渲染层另算一遍）
    g = pc.guard_profile(PU_REN, p, 2)   # 两只 → formation_count=2
    check(str(g["hp_max"]) in blob, "卡面气血与 guard_profile 不一致")
    check("%.0f%%" % (g["chance"] * 100) in blob, "卡面挡伤概率与 guard_profile 不一致")
    # 攻击也必须传 p：旧版 puppet_attack(cur) 走标尺玩家，与实战两张皮
    check(data["puppets"][0]["atk"] == str(int(pc.puppet_attack(PU_REN, p))),
          "卡面攻击不是用玩家 p 算的（会跌破实战口径）")
    # P1-1：战损必须在 PNG 名录卡上可见（玩家真正看的卡），且护甲是减半后的值
    hd = card.build_puppet_data([dict(PU_REN, id=1, damaged=True)],
                                {"active_id": 1}, p)
    dblob = "\n".join("\n".join(x["lines"]) for x in hd["puppets"])
    check("受损" in dblob and "雷淬修复" in dblob,
          "战损傀儡的 PNG 名录卡没写「待雷淬修复」——玩家看不到战损")
    gd = pc.guard_profile(dict(PU_REN, damaged=True), p, 1)
    check(str(gd["armor"]) in dblob,
          "战损卡的护甲数字未用减半后的 guard_profile 值")


def test_command_layer_uses_same_profile():
    """命令层必须调 guard_profile，不许自己拼一套数字。"""
    # 2026-11-26：cmd_puppet 已从 commands.py 拆到 commands_puppet.py，扫描路径跟着搬家。
    src = open(os.path.join(PLUGINS, "doupo", "commands_puppet.py"),
               encoding="utf-8").read()
    fn = None
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "cmd_puppet":
            fn = node
            break
    check(fn is not None, "找不到 cmd_puppet")
    if fn is None:
        return
    seg = ast.get_source_segment(src, fn) or ""
    check("guard_profile(" in seg, "cmd_puppet 没有调 guard_profile")
    check("puppet_attack(cur, p)" in seg,
          "cmd_puppet 的攻击没传 p —— 卡面会与实战两张皮")


# ---------------------------------------------------------------------------
# 7. 玩家真的能在战报里看见（否则等于没做）
# ---------------------------------------------------------------------------
def _find_guard_battle():
    """找一场**怪放了技能**的护主场次。

    ⚠️ 必须让怪放技能：`sk_note` 为空时 `notes` 只有护主一条，`append` 和
    `insert(0)` 结果完全一样 —— 那样的场次抓不到「文案被 TAG_LIMIT 截掉」，
    注入反证 #13 会白守（踩过一次）。chance=1.0 保证每回合都放。
    """
    spec = dict(SPEC, lv=81,
                skill={"name": "裂爪击", "effect": "burst",
                       "mult": 1.5, "chance": 1.0})
    for seed in range(400):
        p = _p(81)
        r = core.fight(p, core.monster_scaled(p, spec),
                       rng=random.Random(seed).random,
                       puppet=PU_TIAN, formation_n=1, auto_pill=True)
        hit = _guarded_logs(r.get("log"))
        if not hit:
            continue
        # 必须真的带上技能 note，否则这场证明不了「谁排在第一位」
        if any(len([x for x in (e.get("notes") or []) if x]) >= 2 for e in hit):
            return r
    return None


def test_guard_visible_in_review():
    r = _find_guard_battle()
    check(r is not None, "300 场抓不到护主，无法验证战报可见性")
    if r is None:
        return
    items = br.action_items(r)
    hit = [it for it in items
           if "抢身挡下" in str(it.get("tags") or "")]
    check(hit, "逐招明细里看不到护主文案 —— 玩家永远不知道傀儡挡了")
    # 🚨 结构断言（比「能不能看见」更强）：文案必须是 notes[0]。
    #    逐招明细只取 notes[:TAG_LIMIT]（4 条），而兽击条目上可能已经挂了
    #    「技能名 + 原著台词 + 机制」三条 —— append 到末尾会静默消失，
    #    而且只在 notes 恰好塞满的场次才暴露，靠跑战斗抓不稳（反证 #13 白守）。
    for it in _guarded_logs(r.get("log")):
        notes = [x for x in (it.get("notes") or []) if isinstance(x, str) and x]
        check(notes and "抢身挡下" in notes[0],
              "护主文案不在 notes 第一位，会被 TAG_LIMIT 截掉：%s" % notes)
    beats = br.beats(r)
    check(any(b.get("tag") == "护主" for b in beats),
          "关键节点里没有「护主」条目：%s" % [b.get("tag") for b in beats])
    for b in beats:
        if b.get("tag") == "护主":
            check("抢身挡下" in str(b.get("text") or ""),
                  "护主关键节点文案没带挡伤数")
            check(b.get("tone") == "mine",
                  "护主关键节点用了敌方色调（会让玩家以为是被打）")


def test_guard_note_no_nested_bracket():
    """文案内不许出现括号：它会整句塞进「招式（tags）」，再套一层就成嵌套。"""
    for pu, raw, soak, taken, left, mx in (
            (PU_REN, 10000, 6500, 5000, 1200, 14620),
            (PU_TIAN, 88888, 55555, 22222, 0, 32164)):
        note = pc.guard_note(pu, raw, soak, taken, left, mx)
        check("（" not in note and "(" not in note,
              "护主文案里出现了括号，外层会套成两层：%s" % note)
        for num in (soak, taken, left, mx):
            check(str(num) in note, "护主文案缺数字 %d：%s" % (num, note))
        check(str(pu["name"]) in note, "护主文案没写傀儡名：%s" % note)


# ---------------------------------------------------------------------------
# 8. 概率配置本身
# ---------------------------------------------------------------------------
def test_probability_config_sane():
    ch = tuple(cfg.PUPPET_GUARD_CHANCE)
    cap = float(cfg.PUPPET_GUARD_CAP)
    step = float(cfg.PUPPET_FORMATION_GUARD_STEP)
    check(len(ch) == 3, "PUPPET_GUARD_CHANCE 必须三档")
    check(ch[0] < ch[1] < ch[2], "挡伤概率未随档位递增：%s" % (ch,))
    check(all(0.0 < c <= cap for c in ch), "挡伤概率越界：%s" % (ch,))
    # 结阵加成必须被 CAP 吃掉，而不是叠成必中（真跑 guard_profile，别手算）
    p = _p(81)
    g1 = pc.guard_profile(PU_TIAN, p, 1)["chance"]
    g3 = pc.guard_profile(PU_TIAN, p, 9)["chance"]
    check(g3 <= cap + 1e-9, "结阵 9 具挡伤概率 %.3f 顶破 CAP %.2f" % (g3, cap))
    check(g3 == cap, "三具以上结阵应正好封在 CAP，实际 %.3f" % g3)
    check(g1 < g3, "结阵没有提升挡伤概率（%.3f → %.3f）" % (g1, g3))
    check(ch[2] + 2 * step >= cap,
          "三具结阵都到不了 CAP —— 结阵这条加成等于没写")
    # 「较低概率」：单具的人级概率必须明显低于一成半以上
    check(ch[0] <= 0.20, "人级 %.0f%% 已不算「较低概率」" % (ch[0] * 100))


def test_guard_share_stays_below_half():
    """护主挡下的量占「本应承受」的比例必须 < 50% —— 过了一半就是抢风头。

    ⚠️ 别用「带/不带傀儡的承伤差」当分子：那个差里混了**傀儡输出让回合变少**
    的贡献（实测 lv81 天级差 66%），全记到护主头上会得出「护主吃了三分之二」
    这种假结论。只统计 ``res.puppet_guard.soak``（护主真挡下的原始伤害）。
    """
    lv = 81
    mon = core.monster_scaled(_p(lv), dict(SPEC, lv=lv))
    soak_sum = take_sum = 0
    for seed in range(25):
        r = core.fight(_p(lv), copy.deepcopy(mon),
                       rng=random.Random(seed).random,
                       puppet=PU_TIAN, formation_n=1, auto_pill=True)
        pg = r.get("puppet_guard") or {}
        soak_sum += int(pg.get("soak") or 0)
        take_sum += int(r["taken"])
    check(soak_sum > 0, "25 场天级护主一次没触发，无法评估分摊比")
    total = soak_sum + take_sum
    share = soak_sum / max(1, total)
    check(share < 0.50,
          "护主替玩家吃掉 %.0f%% 的本应承受伤害 —— 超过一半就是抢风头" % (share * 100))
    check(share > 0.03,
          "护主只替玩家吃掉 %.1f%% —— 几乎没作用" % (share * 100))


# ---------------------------------------------------------------------------
# P0-2 有灵主动拦截（2026-11-20 设计文档）：小额也挡、残血不打折
# ---------------------------------------------------------------------------
def test_awakened_intercepts_small_hits():
    """同种子 A/B：伤害落在「有灵门槛~无灵门槛」之间时，只有有灵会拦。"""
    p = _p(81)
    st = core.stats(p)
    base_th = max(1, int(int(st["体力"]) * float(cfg.PUPPET_GUARD_THRESHOLD)))
    aw = dict(PU_REN, awakened=True)
    g = pc.guard_profile(aw, p, 1)
    check(bool(g.get("awakened")), "guard_profile 未透出 awakened —— 卡面与战斗会两张皮")
    aw_th = g["threshold"]
    check(aw_th < base_th,
          "有灵门槛 %d 未低于无灵门槛 %d —— 主动拦截没有扩大触发范围"
          % (aw_th, base_th))
    mit = core.mitigation(st["防御"])
    # 取两道门槛的中点：无灵绝不触发，有灵才有机会触发。
    mon = core.monster_scaled(p, dict(SPEC, lv=81))
    mon["atk"] = max(1, int(((aw_th + base_th) // 2) / max(0.01, mit)))
    raw = max(1, int(mon["atk"] * mit))
    check(aw_th <= raw < base_th,
          "构造失败：实收 %d 未落在有灵门槛 %d ~ 无灵门槛 %d 之间"
          % (raw, aw_th, base_th))
    fired_aw = fired_plain = 0
    for seed in range(25):
        r = core.fight(_p(81), copy.deepcopy(mon), rng=random.Random(seed).random,
                       puppet=dict(aw), formation_n=1, auto_pill=True)
        fired_aw += int((r.get("puppet_guard") or {}).get("count") or 0)
        r0 = core.fight(_p(81), copy.deepcopy(mon), rng=random.Random(seed).random,
                        puppet=dict(PU_REN), formation_n=1, auto_pill=True)
        fired_plain += int((r0.get("puppet_guard") or {}).get("count") or 0)
    check(fired_aw > 0, "有灵后小伤害仍一次都不拦 —— 主动拦截没落地")
    check(fired_plain == 0, "无灵也拦了 %d 次 —— 大额门槛被破坏" % fired_plain)


def test_awakened_skips_hurt_penalty():
    """有灵＝悍不畏死：自身残血不再乘 0.5 惩罚（源码级钉死整段条件）。"""
    # 2026-11-26：fight 内核已从 core.py 搬到 battle_core.py，源码断言必须跟着搬家，
    # 否则扫 core.py 永远找不到 → 假红（业务实现本身一直在，见 battle_core.py 有灵分支）。
    src = open(os.path.join(ROOT, "src", "plugins", "doupo", "battle_core.py"),
               encoding="utf-8").read()
    # 🚨 必须钉整段结构：只搜子串会被 `if (True or not ...)` 这种注入骗过（实测白守）。
    check(bool(re.search(r'if \(not _pup_prof\.get\("awakened"\)', src)),
          "残血惩罚未按有灵分支跳过 —— 北王式主动拦截只剩卡面文案")
    check(0.0 < float(cfg.PUPPET_AWAKEN_GUARD_THRESHOLD_MULT) < 1.0,
          "有灵门槛系数越界：%s" % cfg.PUPPET_AWAKEN_GUARD_THRESHOLD_MULT)
    note = pc.guard_note(PU_REN, 100, 60, 30, 10, 100, awakened=True)
    check("心念一动" in note,
          "有灵拦下时战报无「心念一动」提示：%s" % note)
    plain = pc.guard_note(PU_REN, 100, 60, 30, 10, 100)
    check("心念一动" not in plain, "无灵傀儡的战报被误加了有灵文案")


def test_awakened_still_not_stealing_show():
    """有灵只改行为，不放宽概率上限与泄漏率（不抢风头红线）。"""
    g = pc.guard_profile(dict(PU_TIAN, awakened=True), _p(81), 3)
    check(float(g["chance"]) <= float(cfg.PUPPET_GUARD_CAP) + 1e-9,
          "有灵后挡伤概率 %.3f 越过 CAP %.3f" % (g["chance"], cfg.PUPPET_GUARD_CAP))
    check(abs(float(g["leak"]) - float(cfg.PUPPET_GUARD_LEAK)) < 1e-9,
          "有灵后泄漏率 %.3f ≠ %.3f（变成免伤了）" % (g["leak"], cfg.PUPPET_GUARD_LEAK))


# ---------------------------------------------------------------------------
# P1-1 战损（D05/N02/E42）：护甲打空 → damaged 写库 → 攻击×0.8/护甲减半 → 雷淬复原
# ---------------------------------------------------------------------------
def test_damaged_cuts_attack_and_armor():
    """战损必须同时降攻击与护甲，且倍率就是设计文档 D05 的 0.8 / 0.5。"""
    # config 值本身钉死为绝对锚（否则 config 被改、期望跟着改 = 自己考自己）
    check(abs(float(cfg.PUPPET_DAMAGED_ATK_MULT) - 0.80) < 1e-9,
          "PUPPET_DAMAGED_ATK_MULT 被改成 %s（设计 D05 要求 0.80）"
          % cfg.PUPPET_DAMAGED_ATK_MULT)
    check(abs(float(cfg.PUPPET_DAMAGED_ARMOR_MULT) - 0.50) < 1e-9,
          "PUPPET_DAMAGED_ARMOR_MULT 被改成 %s（设计 D05 要求 0.50）"
          % cfg.PUPPET_DAMAGED_ARMOR_MULT)
    p = _p(81)
    for tier, pu in enumerate(TIERS):
        whole = pc.puppet_full_attack(pu)
        hurt = pc.puppet_full_attack(dict(pu, damaged=True))
        check(abs(hurt - whole * 0.80) < 1e-6,
              "tier%d 战损攻击 %.2f ≠ 满状态 %.2f × 0.80"
              % (tier, hurt, whole))
        # 卡面 puppet_attack 与实战 expected_damage 都要降（别只降一处）。
        # ⚠️ 不能用 `h < w + 容差`：相等时也成立 → 变异白守（本轮踩过）。
        #    卡面有 CAP 截断，比值约 0.80~0.86，只断言「至少降 1 点」。
        atk_w = pc.puppet_attack(pu, p)
        atk_h = pc.puppet_attack(dict(pu, damaged=True), p)
        check(atk_h < atk_w - 1.0,
              "tier%d 卡面攻击未随战损下降：满 %d 战损 %d"
              % (tier, atk_w, atk_h))
        exp_w = pc.puppet_expected_damage(pu, 1, p=p)
        exp_h = pc.puppet_expected_damage(dict(pu, damaged=True), 1, p=p)
        check(exp_h < exp_w - 1.0, "tier%d 实战 expected_damage 未随战损下降" % tier)
        g_w = pc.guard_profile(pu, p, 1)
        g_h = pc.guard_profile(dict(pu, damaged=True), p, 1)
        # ⚠️ 绝对锚 0.5 而非读 config：config 被改成 1.0 时，若期望也读 config
        #    就会「自己考自己」永远绿（变异 27 白守的根因）。config 值另单独钉。
        check(g_h["armor"] == int(g_w["armor"] * 0.5),
              "tier%d 战损护甲 %d ≠ 满状态 %d 的一半"
              % (tier, g_h["armor"], g_w["armor"]))
        check(g_h["damaged"] is True and g_w["damaged"] is False,
              "tier%d guard_profile 未透出 damaged 字段" % tier)
        # 战损只降攻击/护甲，不碰挡伤概率与泄漏（不抢风头红线不能借战损放宽）
        check(abs(g_h["chance"] - g_w["chance"]) < 1e-9
              and abs(g_h["leak"] - g_w["leak"]) < 1e-9,
              "tier%d 战损改动了挡伤概率/泄漏率" % tier)


def test_damaged_never_breaks_cap():
    """战损降效后仍守 CAP：满状态贴 CAP 的傀儡战损后只会更低。"""
    p = _p(90)
    for pu in TIERS:
        full = pc.puppet_expected_damage(pu, 3, p=p)
        hurt = pc.puppet_expected_damage(dict(pu, damaged=True), 3, p=p)
        cap = pc._player_round_output(p) * float(cfg.PUPPET_ATK_CAP[int(pu["tier"])]) \
            if hasattr(pc, "_player_round_output") else None
        # 不依赖私有函数的具体名字：战损输出必然 ≤ 满状态输出即可
        check(0.0 <= hurt <= full + 1e-6,
              "%s 战损后输出 %.2f 反超满状态 %.2f" % (pu["name"], hurt, full))


def test_temper_repairs_damage():
    """一次雷淬令 damaged=False —— 「待雷淬修复」这句卡面文案第一次成真。"""
    for tier, pu in enumerate(TIERS):
        rep = pc.temper(dict(pu, damaged=True, purity=0))
        check(rep.get("damaged") is False,
              "tier%d 雷淬后 damaged 仍为 %s" % (tier, rep.get("damaged")))
    # 天级 capped 分支也必须清零（这是死代码高发区）
    capped = pc.temper(dict(PU_TIAN, damaged=True, tier=2))
    check(capped.get("damaged") is False, "天级封顶分支雷淬未清战损")


def test_fight_guard_signal_carries_real_id():
    """真打一场：broken 信号必须自带真实库行 id，供命令层写回。"""
    p = _p(60)
    st = core.stats(p)
    g = pc.guard_profile(PU_REN, p, 1)
    mig = core.mitigation(g["armor"])
    need = int(g["hp_max"] / max(1e-6, mig) / max(1e-6, 1.0 - g["leak"])) + 1
    mon = core.monster_scaled(p, dict(SPEC, lv=60))
    mon["atk"] = max(1, int(need / max(1e-6, core.mitigation(st["防御"]))) + 1)
    pu = dict(PU_REN, id=4242)
    hit = False
    for seed in range(30):
        r = core.fight(p, copy.deepcopy(mon), rng=random.Random(seed).random,
                       puppet=pu, formation_n=1, auto_pill=True)
        pg = r.get("puppet_guard") or {}
        check(int(pg.get("id") or 0) == 4242,
              "seed%d 护主信号丢真实 id：%s" % (seed, pg))
        if pg.get("broken"):
            # ⚠️ res 的键是 hp_left（不是 log 条目的 guard_hp_left）；且 0 是
            #    falsy，不能用 `or -1` 兜底，直接下标断言。
            check(int(pg.get("id") or 0) == 4242 and int(pg["hp_left"]) == 0,
                  "seed%d broken 但 id/hp_left 不对：%s" % (seed, pg))
            hit = True
    check(hit, "30 场没打空傀儡，无法验证 broken 信号带 id")


def test_wrappers_forward_puppet_guard():
    """4 个 wrapper 必须透传 puppet_guard，否则 6/7 入口拿不到战损信号。

    duel 例外：它没有护主机制，res 里本就不该有这个键（PVP 不产生战损）。
    """
    import inspect
    names = ("encounter", "tower_floor", "weekly_floor", "nine_star_breakthrough")
    for name in names:
        src = inspect.getsource(getattr(core, name))
        check("puppet_guard" in src,
              "core.%s 未透传 puppet_guard —— 该入口战损永远写不回" % name)
    # 信号源 fight 必须把 id 塞进 guard
    fsrc = inspect.getsource(core.fight)
    check('"id": int(puppet.get("id") or 0)' in fsrc,
          "fight 的 puppet_guard 未携带真实 id")


def test_vein_layers_only_add_armor_not_damage():
    """P1-2 天级雷纹：只加护主护甲（有落点），不碰输出/概率/CAP（禁死字段）。"""
    p = _p(81)
    base = dict(PU_TIAN, purity=100)
    # 雷纹层数由 purity 纯函数推导；0 层=100，满层=100+5×40=300
    check(pc.vein_layers(100) == 0, "purity=100 不该有雷纹")
    check(pc.vein_layers(100 + int(cfg.PUPPET_VEIN_PURITY)) == 1,
          "超量 40 纯度应为 1 层雷纹")
    check(pc.vein_layers(pc.purity_hard_max()) == int(cfg.PUPPET_VEIN_MAX),
          "硬上限应为满层雷纹")
    check(pc.vein_layers(pc.purity_hard_max() + 999) == int(cfg.PUPPET_VEIN_MAX),
          "超过硬上限雷纹不该继续涨")
    g0 = pc.guard_profile(base, p, 1)
    g5 = pc.guard_profile(dict(base, purity=pc.purity_hard_max()), p, 1)
    check(g5["armor"] > g0["armor"],
          "满层雷纹护甲 %d 未超过 0 层 %d —— 紫皮长线没有落点"
          % (g5["armor"], g0["armor"]))
    check(g5["vein"] == int(cfg.PUPPET_VEIN_MAX), "guard_profile 未透出雷纹层数")
    # 输出不因雷纹上涨（D03：不动伤害倍率，不破 CAP）
    d0 = pc.puppet_expected_damage(base, 1, p=p)
    d5 = pc.puppet_expected_damage(dict(base, purity=pc.purity_hard_max()), 1, p=p)
    check(abs(d5 - d0) < 1e-6,
          "雷纹改变了输出 %.2f→%.2f —— 违反「不动伤害倍率」" % (d0, d5))
    # 概率/泄漏也不变（不抢风头）
    check(abs(g5["chance"] - g0["chance"]) < 1e-9
          and abs(g5["leak"] - g0["leak"]) < 1e-9,
          "雷纹改动了挡伤概率/泄漏率")


def test_writeback_failures_propagate():
    """源码级：写回失败必须上抛，不许 try/except 吞（用户最高声明禁止兜底）。"""
    import inspect
    from doupo import commands as cmd
    src = inspect.getsource(cmd._writeback_damage)
    check("try:" not in src and "except" not in src,
          "_writeback_damage 里出现 try/except —— 写回失败被静默吞掉了")
    check("update_puppet" in src and "damaged=True" in src,
          "_writeback_damage 未真正调用 update_puppet(damaged=True)")
    check("broken" in src and 'guard.get("id")' in src,
          "_writeback_damage 未按 broken + 信号 id 写回")
    # 漏斗必须真正「调用」写回口径，不能只写在注释里骗过子串搜索（白守教训）。
    fsrc = inspect.getsource(cmd._finalize_battle)
    check(bool(re.search(r'await\s+_writeback_damage\s*\(', fsrc)),
          "_finalize_battle 未调用 _writeback_damage（逻辑复制/注释骗过会漂移）")


# ---------------------------------------------------------------------------
def _run():
    for fn in (test_tier_monotonic,
               test_armor_is_reverse_solved_from_take,
               test_durability_stable_across_levels,
               test_only_big_hit_triggers,
               test_threshold_is_configured_not_zero,
               test_guard_is_not_immunity,
               test_puppet_actually_loses_hp,
               test_broken_puppet_stops_working,
               test_no_puppet_adds_nothing,
               test_no_puppet_fixed_scenario_baseline,
               test_card_shows_guard_panel,
               test_command_layer_uses_same_profile,
               test_guard_visible_in_review,
               test_guard_note_no_nested_bracket,
               test_probability_config_sane,
               test_guard_share_stays_below_half,
               test_awakened_intercepts_small_hits,
               test_awakened_skips_hurt_penalty,
               test_awakened_still_not_stealing_show,
               test_damaged_cuts_attack_and_armor,
               test_damaged_never_breaks_cap,
               test_temper_repairs_damage,
               test_fight_guard_signal_carries_real_id,
               test_wrappers_forward_puppet_guard,
               test_writeback_failures_propagate,
               test_vein_layers_only_add_armor_not_damage):
        try:
            fn()
        except Exception as e:  # noqa: BLE001
            import traceback
            _FAILS.append("%s 抛异常：%s" % (fn.__name__, e))
            traceback.print_exc()
    for m in _FAILS:
        print("FAIL  " + m)
    print("=" * 70)
    print("GUARD_TOTAL=%d FAILED=%d" % (_N, len(_FAILS)))
    return 0 if not _FAILS else 1


if __name__ == "__main__":
    sys.exit(_run())
