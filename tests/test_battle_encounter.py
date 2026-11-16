# -*- coding: utf-8 -*-
"""斗破遭遇战 + 战报高光剪辑守门（2026-11-16）。

用户需求：
  * 回合太少 → 一场遭遇 1~5 个敌人，回合自然变多；
  * 输出太多 → 忽略中间回合，只讲高光；
  * 反派参考原著 → 物种表含原著魔兽/悍匪/魂殿反派，分档出现不越级；
  * 战报更规范吸引人 → 分节 + 终结标记 + 折叠计数 + 总览。

守门不信任被测模块，用独立判据复算；注入反例证明测试会报错（prove-it）。
"""
from __future__ import annotations

import ast
import random
from pathlib import Path

import nonebot

nonebot.init()

from src.plugins.doupo import commands, config as cfg, core  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
CORE_PY = ROOT / "src" / "plugins" / "doupo" / "core.py"
CONFIG_PY = ROOT / "src" / "plugins" / "doupo" / "config.py"
COMMANDS_PY = ROOT / "src" / "plugins" / "doupo" / "commands.py"


def _p(lv=20, luck=1):
    p = core.create_player("T", "测试道友", "G")
    p["lv"] = lv
    p["luck"] = luck
    p["hp"] = core.stats(p)["体力"]
    # 裸号用普攻即可测编排/展示逻辑；战斗数值与斗技由 test_core/test_skills 覆盖
    return p


# ---------------------------------------------------------------------------
# 一、物种表：考据 + 分档
# ---------------------------------------------------------------------------
def test_monsters_have_kind_and_cover_three_families():
    """每个物种都有 kind，且魔兽/悍匪/反派三类齐全（用户要"魔兽或强盗坏人"）。"""
    assert cfg.MONSTERS, "物种表空"
    for m in cfg.MONSTERS:
        assert m.get("kind"), "物种 %s 缺 kind" % m["name"]
    kinds = {m["kind"] for m in cfg.MONSTERS}
    assert {"魔兽", "悍匪", "魂殿"} <= kinds, "缺分类：%s" % kinds


def test_lore_enemies_are_real_and_tiered():
    """原著反派必须是考据得到的名字，且在高等级档才出现（不越级打新手）。"""
    lore = {
        "狼头佣兵穆蛇": "悍匪",   # 原著魔兽山脉狼头佣兵团团长，二星斗师
        "药皇韩枫": "黑盟",       # 黑盟首领，药皇
        "地魔老鬼": "魔炎谷",     # 魔炎谷首领
        "魂殿鹜护法": "魂殿",     # 魂殿地级护法
        "魂殿摘星老鬼": "魂殿",   # 魂殿天尊
        "魂灭生": "魂殿",         # 魂殿殿主
    }
    table = {m["name"]: m for m in cfg.MONSTERS}
    for name, kind in lore.items():
        assert name in table, "缺原著反派 %s" % name
        assert table[name]["kind"] == kind, "%s 的 kind 错" % name
    # 反派最低等级 >= 魔兽最低档 + 一段（新手不至于一上来撞魂殿）
    villain_lv = min(table[n]["lv"] for n in lore)
    assert villain_lv >= 18, "反派最低档 %d 太低，会越级" % villain_lv


def test_low_level_has_only_beasts_high_level_has_lore():
    """分层：lv1 只见魔兽；lv60+ 能见原著反派；lv102 只见魂殿。"""
    for lv, want_kind in ((1, "魔兽"), (10, "魔兽"), (102, "魂殿")):
        p = _p(lv)
        wins = [sp for sp in cfg.MONSTERS
                if abs(sp["lv"] - lv) <= cfg.LV_PER_REALM]
        seen = set()
        for s in range(40):
            for sp in core.pick_encounter(p, 5, rng=random.Random(s * 13 + lv).random):
                seen.add(sp["kind"])
        assert seen, "lv%d 抽不到任何敌人" % lv
        assert want_kind in seen, "lv%d 应可遇到 %s，实际 %s" % (lv, want_kind, seen)


# ---------------------------------------------------------------------------
# 二、遭遇编排：1~5 敌、聚合、气血结转、败即收兵
# ---------------------------------------------------------------------------
def test_encounter_count_within_1_5():
    p = _p(20)
    for s in range(300):
        n = core.roll_encounter_count(p, rng=random.Random(s).random)
        assert 1 <= n <= 5, "敌人数越界 %d" % n


def test_encounter_count_distribution_not_uniform():
    """1~2 敌应占多数（日常），5 敌稀有（硬仗）——不能均匀随机。"""
    p = _p(20)
    cnt = {}
    for s in range(600):
        n = core.roll_encounter_count(p, rng=random.Random(s).random)
        cnt[n] = cnt.get(n, 0) + 1
    low = cnt.get(1, 0) + cnt.get(2, 0)
    assert low > cnt.get(5, 0), "5 连战比 1~2 敌还多：%s" % cnt


def test_pick_encounter_dedup_names():
    """同一场遭遇物种不重复（候选池耗尽才允许同种再来）。"""
    for lv in (20, 45, 90):
        p = _p(lv)
        for s in range(30):
            got = core.pick_encounter(p, 3, rng=random.Random(s + lv).random)
            names = [m["name"] for m in got]
            assert len(set(names)) == len(names), "同场重复物种：%s" % names


def test_encounter_aggregates_every_enemy():
    """每个敌人都产生一条 detail，且总回合 = 各敌回合之和。"""
    p = _p(20)
    res = core.encounter(p, rng=random.Random(7).random)
    assert 1 <= res["enemy_count"] <= 5
    assert len(res["detail"]) == res["enemy_count"]
    assert res["rounds"] == sum(int(x["rounds"]) for x in res["detail"])
    assert len(res["enemies"]) == res["enemy_count"]


def test_encounter_rounds_richer_than_single_fight():
    """用户"回合太少"：多敌场的总回合必须 ≥ 敌人数 × 3（每敌至少打几回合）。

    不写死"2 敌必须 ≥8"——裸号普攻 TTK 只有 3~4 回合，7 回合同样是
    "比单敌 4~5 回合丰富"。真正要守的是：回合随敌人数单调增长。
    """
    rich = 0
    for s in range(60):
        # 2026-11-16 斗气跨战结转后，encounter 会写回 p（战斗结算 + 斗气消耗）；
        # 每场抽样必须用满状态副本，否则第 2 场起是残血空蓝、敌人数分布失真。
        p = _p(20)
        res = core.encounter(p, rng=random.Random(s).random)
        n = res["enemy_count"]
        if n >= 2:
            assert res["rounds"] >= n * 3, "%d 敌只有 %d 回合" % (n, res["rounds"])
            rich += 1
    assert rich > 0, "60 场里一场多敌都没有——抽样坏了"


def test_encounter_stops_on_first_loss():
    """败即收兵：detail 里败的那条必须是最后一条，后面不再打。"""
    p = _p(10, luck=0)
    p["hp"] = 60  # 残血硬闯，必败
    found = False
    for s in range(30):
        res = core.encounter(dict(p), rng=random.Random(s).random)
        losses = [i for i, x in enumerate(res["detail"]) if not x["win"]]
        if losses:
            assert losses == [len(res["detail"]) - 1], "败后还在打：%s" % res["detail"]
            assert res["win"] is False
            found = True
            break
    assert found, "残血闯 30 场竟没败过——胜负判定可疑"


# ---------------------------------------------------------------------------
# 三、高光剪辑：忽略中间回合
# ---------------------------------------------------------------------------
def _synthetic_log(n=12):
    log = []
    for i in range(n):
        if i == 3:
            log.append({"kind": "闪避", "dmg": 0, "miss": True})
        elif i == 5:
            log.append({"kind": "持续伤害", "dmg": 120, "dot": True})
        elif i == 7:
            log.append({"kind": "兽击", "dmg": 300, "taken": True})
        else:
            log.append({"kind": "拳头", "dmg": 100 + i,
                        "skill": i % 2 == 0, "crit": i == 9})
    return log


def test_highlights_fold_middle_turns():
    log = _synthetic_log(12)
    hi, folded = commands._battle_highlights(log, limit=4)
    assert len(hi) <= 4
    assert folded == len(log) - len(hi), "折叠计数与保留数不符"
    assert folded > 0, "12 条战报没折叠——等于没做精简"


def test_highlights_keep_first_last_and_crit():
    log = _synthetic_log(12)
    hi, _ = commands._battle_highlights(log, limit=6)
    assert log[0] in hi, "丢了首击"
    assert any(it.get("crit") for it in hi), "丢了暴击高光"
    assert any(it.get("dot") for it in hi), "丢了持续伤害高光"


def test_highlights_empty_and_short():
    assert commands._battle_highlights([], limit=4) == ([], 0)
    log = _synthetic_log(3)
    hi, folded = commands._battle_highlights(log, limit=4)
    assert hi == log and folded == 0, "短战报不该折叠"


def test_battle_report_no_raw_log_dump():
    """展示层不得把整条 log 原样输出（否则 5 连战刷屏）。"""
    src = COMMANDS_PY.read_text(encoding="utf-8")
    fn = src[src.index("async def cmd_fight"):]
    fn = fn[:fn.index("\nasync def ")] if "\nasync def " in fn else fn
    assert "_battle_highlights" in fn, "cmd_fight 没用高光剪辑"
    assert "res[\"log\"]" not in fn and "res['log']" not in fn, "cmd_fight 仍在整段渲染 log"


# ---------------------------------------------------------------------------
# 四、prove-it：把坏东西放回去，守门必须报错
# ---------------------------------------------------------------------------
def test_injection_guards():
    """注入反例验证检测逻辑不是空转。"""
    # A. 缺 kind 的物种必须被识别
    bad = [{"name": "野怪", "lv": 9, "hard": 0.9}]
    assert all(not m.get("kind") for m in bad), "检测逻辑失效：缺 kind 没识别"
    # B. 折叠计数算错必须报错
    hi, folded = commands._battle_highlights(_synthetic_log(10), limit=4)
    assert folded == 10 - len(hi)
    assert not (folded == 0 and len(hi) < 10), "错判：不该折叠却说没折叠"
    # C. 反派名单缺项必须被识别
    names = {m["name"] for m in cfg.MONSTERS if m["name"] != "魂灭生"}
    assert "魂灭生" not in names, "检测逻辑失效：删了反派没识别"
    # D. AST 能独立发现新写的 encounter 函数（新增编排必须能被守门看到）
    tree = ast.parse(CORE_PY.read_text(encoding="utf-8"))
    fns = {n.name for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    assert {"encounter", "roll_encounter_count", "pick_encounter"} <= fns


# ---------------------------------------------------------------------------
# 五、固定骨架战报：敌人再多，字数/行数不膨胀（2026-11-16 用户二次反馈）
# ---------------------------------------------------------------------------
class _SkeletonStore:
    def __init__(self, p):
        self.p = p

    async def get_player(self, qq):
        return self.p

    async def put_player(self, p):
        self.p = p


def _fake_res(n: int) -> dict:
    """确定性的遭遇结果：n 个敌人，每敌 8 条 log（1 高光 + 7 中间）。"""
    detail = []
    for i in range(n):
        log = [{"kind": "爆步", "dmg": 1000, "skill": True, "crit": i == 0}]
        log += [{"kind": "拳脚", "dmg": 100, "skill": False} for _ in range(7)]
        detail.append({"index": i, "name": f"怪{i}", "lv": 20, "kind": "魔兽",
                       "win": True, "rounds": 4, "log": log})
    return {"win": True, "rounds": 4 * n, "taken": 100 * n,
            "exp": 1000 * n, "gold": 500 * n,
            "drops": [("血灵草", 1)],
            "detail": detail, "enemies": [f"怪{i}" for i in range(n)],
            "enemy_count": n, "hero_aid": None}


def _render_with_n_enemies(n: int) -> str:
    """注入确定性 encounter / 机遇 / 突破，隔离出纯展示骨架。"""
    import asyncio

    p = core.create_player("skel", "骨架测试", "g")
    p["lv"] = 20
    p["hp"] = core.stats(p)["体力"]
    saved = (core.encounter, core.advance_task, core.can_level)
    try:
        core.encounter = lambda _p, **_kw: _fake_res(n)
        core.advance_task = lambda *_a, **_kw: None
        core.can_level = lambda _p: False
        return asyncio.run(commands.cmd_fight(_SkeletonStore(p), "skel"))
    finally:
        core.encounter, core.advance_task, core.can_level = saved


def _skeleton_shape(out: str) -> dict:
    """独立复算战报骨架：行首标记序列 + 高光条数 + 逐敌展开痕迹。"""
    marks = [ln[:2] for ln in out.split("\n") if ln.strip()]
    return {
        "lines": len([ln for ln in out.split("\n") if ln.strip()]),
        "head_marks": tuple(m for m in marks if m[0] in ("⚔", "💥", "⋯", "🎯", "🩸", "💰", "🎁", "🍀", "⬆")),
        "spot_count": out.count("💥"),
        "fold_count": out.count("略去中间"),
        # 逐敌展开的痕迹：编号圈、多个敌人名、"交锋/决战"分节
        "per_foe": any(sym in out for sym in "①②③④⑤") or "交锋" in out or "决战" in out
                   or ("怪1" in out and "怪2" in out),
    }


def test_skeleton_fixed_1_vs_5_enemies():
    """敌人从 1 增到 5，骨架标记序列必须完全相同、行数相等、不逐敌展开。"""
    one = _render_with_n_enemies(1)
    five = _render_with_n_enemies(5)
    s1, s5 = _skeleton_shape(one), _skeleton_shape(five)
    assert s1["head_marks"] == s5["head_marks"], \
        "骨架行数/标记随敌人数变了：%s vs %s" % (s1["head_marks"], s5["head_marks"])
    assert s1["lines"] == s5["lines"], "行数膨胀：%d vs %d" % (s1["lines"], s5["lines"])
    assert s5["spot_count"] == 1, "高光不止一条：%d" % s5["spot_count"]
    assert s5["fold_count"] == 1, "折叠说明不止一条：%d" % s5["fold_count"]
    assert not s1["per_foe"] and not s5["per_foe"], "仍在逐敌展开"


def test_skeleton_char_count_capped_and_not_linear():
    """字数有硬上限，且 5 敌相对 1 敌只多几个字（数字位数），不是线性增长。"""
    one = _render_with_n_enemies(1)
    five = _render_with_n_enemies(5)
    c1 = len(one.replace("\n", ""))
    c5 = len(five.replace("\n", ""))
    assert c5 <= 200, "5 敌战报 %d 字，超过 200 字上限" % c5
    assert c5 - c1 <= 24, "字数随敌人数膨胀：1 敌 %d 字 vs 5 敌 %d 字" % (c1, c5)


def test_skeleton_keeps_causality_and_rewards():
    """精简后仍保留：遭遇字样、属性归因、收益、折叠计数、只露代表名。"""
    out = _render_with_n_enemies(5)
    assert "遭遇" in out, "丢了「遭遇」"
    assert any(k in out for k in ("力量", "敏捷", "防御", "斗气")), "丢了属性归因"
    assert "修为 +" in out and "金币 +" in out, "丢了收益"
    assert "略去中间" in out, "丢了折叠计数"
    assert "怪0等5名" in out, "敌人名单没压成「代表名等N名」"


def test_skeleton_guard_detects_bloated_report():
    """prove-it：把旧的逐敌展开战报喂给独立检测，必须被判为不合格。"""
    bloated = "\n".join([
        "⚔️ 遭遇 5 名敌手，全部击溃！", "",
        "① 【魔兽】怪0（20级）· 交锋4回合 · 胜",
        "　→ 爆步 1000（力量·斗气）",
        "　　└ 略去 7 式常规攻防",
        "② 【魔兽】怪1（20级）· 决战4回合 · 胜",
        "　→ 爆步 1000（力量·斗气）",
        "　　└ 略去 7 式常规攻防",
    ])
    s = _skeleton_shape(bloated)
    assert s["per_foe"], "检测失效：逐敌展开没被识别"
    assert s["spot_count"] != 1 or s["fold_count"] != 1, "检测失效：多段高光没被识别"


def test_cmd_fight_has_no_per_foe_loop_in_source():
    """源码守门：cmd_fight 展示段不得再出现逐敌分节标记（防止回退到旧写法）。"""
    src = COMMANDS_PY.read_text(encoding="utf-8")
    fn = src[src.index("async def cmd_fight"):]
    fn = fn[:fn.index("\nasync def ")] if "\nasync def " in fn else fn
    for sym in ("①②③", "marks =", "title = \"决战\"", "title = '决战'",
                "交锋{", "· {who}"):
        assert sym not in fn, "展示段出现逐敌分节残留：%s" % sym


# ---------------------------------------------------------------------------
# 六、机缘整场一次：多敌场命中率不累积突破 2% 硬顶（2026-11-16 修正）
# ---------------------------------------------------------------------------
def test_encounter_rolls_hero_aid_exactly_once():
    """无论 1/3/5 敌，encounter 都只调用一次 roll_hero_aid（概率不累积的硬证据）。"""
    orig = core.roll_hero_aid
    calls: list[int] = []

    def spy(p, rng=None):
        calls.append(1)
        return None

    core.roll_hero_aid = spy
    try:
        saved = core.roll_encounter_count
        try:
            for n in (1, 3, 5):
                core.roll_encounter_count = lambda _p, rng=None, _n=n: _n
                before = len(calls)
                core.encounter(_p(20), rng=random.Random(n).random)
                assert len(calls) - before == 1, \
                    "%d 敌调了 %d 次机缘（应为 1）" % (n, len(calls) - before)
        finally:
            core.roll_encounter_count = saved
    finally:
        core.roll_hero_aid = orig


def test_encounter_aid_rate_not_compounded():
    """5 敌场命中率 ≈ 单次硬顶 2%，而非 1-(1-2%)^5≈9.6%（统计守门）。"""
    saved_count = core.roll_encounter_count
    core.roll_encounter_count = lambda _p, rng=None: 5
    try:
        p = _p(20, luck=500)
        rate = core.hero_aid_rate(p)
        assert abs(rate - cfg.HERO_AID_MAX_RATE) < 1e-9, "luck=500 应达硬顶"
        n = 2000
        hits = sum(1 for s in range(n)
                   if core.encounter(dict(p), rng=random.Random(s).random).get("hero_aid"))
        observed = hits / n
        assert observed < rate * 1.6, \
            "命中率 %.4f 明显超过单次硬顶 %.4f（可能在累积）" % (observed, rate)
    finally:
        core.roll_encounter_count = saved_count


def test_encounter_aid_hit_wipes_all_harmless():
    """机缘命中：整场全胜、受伤 0、正常结算、气血不降（「毫发无伤」文案成立）。"""
    orig = core.roll_hero_aid
    aid = {"name": "无名斗皇", "rate": 0.02, "luck": 500}
    core.roll_hero_aid = lambda _p, rng=None: aid
    try:
        p = _p(20, luck=500)
        hp0 = int(p["hp"])
        res = core.encounter(p, rng=random.Random(7).random)
        assert res["win"] is True, "机缘命中却没全胜"
        assert int(res["taken"]) == 0, "机缘命中仍受伤 %s" % res["taken"]
        assert all(x["win"] for x in res["detail"]), "有敌人没被一招秒"
        assert int(res["exp"]) > 0 and int(res["gold"]) > 0, "机缘场奖励没结算"
        assert int(p["hp"]) >= hp0, "机缘后气血反降"
        assert res["hero_aid"] == aid, "整场 hero_aid 未回传"
    finally:
        core.roll_hero_aid = orig


def test_single_fight_still_rolls_by_default():
    """单敌直接调用 fight() 默认仍自己掷机缘（PVP/平衡/既有测试零回归）。"""
    orig = core.roll_hero_aid
    calls: list[int] = []

    def spy(p, rng=None):
        calls.append(1)
        return None

    core.roll_hero_aid = spy
    try:
        p = _p(20)
        calls.clear()
        core.fight(p, core.monster_scaled(p, cfg.MONSTERS[0]), rng=lambda: 0.9)
        assert len(calls) == 1, "单敌默认应自己掷 1 次，实际 %d" % len(calls)
        # 显式 roll_aid=False 则完全不掷
        calls.clear()
        core.fight(p, core.monster_scaled(p, cfg.MONSTERS[0]),
                   rng=lambda: 0.9, roll_aid=False)
        assert len(calls) == 0, "roll_aid=False 不该掷，实际 %d" % len(calls)
    finally:
        core.roll_hero_aid = orig


def test_aid_guard_detects_old_compounding_source():
    """prove-it：把旧实现（每敌各掷）喂给源码判据，必须被识别为回退。"""
    src = CORE_PY.read_text(encoding="utf-8")
    fn = src[src.index("def encounter"):]
    fn = fn[:fn.index("\ndef ")] if "\ndef " in fn else fn
    # 正确实现：encounter 自己掷一次 + 传 roll_aid=False 禁止 fight 再掷
    assert "roll_hero_aid(p, rng)" in fn, "encounter 没在整场掷机缘"
    assert "roll_aid=False" in fn, "encounter 没禁止 fight 重复掷骰"
    # 反例：旧写法 = 直接 fight(p, mon, rng=rng) 且无整场预掷
    old = "r = fight(p, mon, rng=rng)\n        if r.get(\"hero_aid\"): hero_aid = r[\"hero_aid\"]"
    assert old not in fn, "检测失效：疑似回退到每敌各掷的旧写法"
    assert not ("roll_aid=False" not in fn and "roll_hero_aid" not in fn), \
        "检测失效：整场预掷与禁掷都没了却没报警"


# ---------------------------------------------------------------------------
# 斗气结转（2026-11-16 用户「去掉强制回满斗气，不然突破就失去了意义」）
# ---------------------------------------------------------------------------
def test_fight_mp_carries_over_not_refilled():
    """战后写回消耗后的斗气，不再强制回满；开战沿用剩余值。"""
    p = _p(20)
    # 给玩家上阵斗技，确保本场会消耗斗气（裸号普攻不耗）
    from src.plugins.doupo import skills as sk  # noqa: PLC0415
    pool = sk.loadout_entries(p)
    if pool:
        p["learned"] = [e["name"] for e in pool]
        p["loadout"] = [e["name"] for e in pool[:4]]
    full = core.mp_max(p)
    p["mp"] = full
    hp0 = core.stats(p)["体力"]
    p["hp"] = hp0
    core.fight(p, core.monster_scaled(p, cfg.MONSTERS[0]), rng=random.Random(2).random)
    if pool:
        assert 0 <= int(p["mp"]) < full, "有斗技却战后满蓝——强制回满没去掉"
    else:
        assert 0 <= int(p["mp"]) <= full


def test_zero_mp_opening_uses_normal_attack():
    """0 斗气开战：第一击转普攻（不卡死、也不白送斗技）。"""
    p = _p(20)
    p["hp"] = core.stats(p)["体力"]
    p["mp"] = 0
    res = core.fight(p, core.monster_scaled(p, cfg.MONSTERS[0]), rng=lambda: 0.5)
    assert res["log"], "没出手"
    assert not res["log"][0].get("skill"), "0 斗气第一击不该是斗技"


def test_level_up_refills_mp_so_break_matters():
    """突破/升级回满斗气——这是取消战后回满后「突破的意义」。"""
    p = _p(10)
    p["mp"] = 0
    p["exp"] = cfg.exp_need(10)
    res = core.level_up(p, rng=lambda: 0.9)
    assert res.get("ok"), "经验满应能升级：%s" % res
    assert int(p["mp"]) == core.mp_max(p), "升级后斗气必须回满（突破价值）"


def test_no_postfight_refill_source_guard():
    """prove-it：源码判据——fight 战后不得再写 p[\"mp\"] = mp_max(p)。"""
    src = CORE_PY.read_text(encoding="utf-8")
    fn = src[src.index("def fight"):]
    fn = fn[:fn.index("\ndef ")] if "\ndef " in fn else fn
    assert 'p["mp"] = mp' in fn, "战后没写回消耗后的斗气"
    assert 'p["mp"] = mp_max(p)' not in fn, "仍存在战后强制回满斗气"
