# -*- coding: utf-8 -*-
"""根基裁定守门（2026-11-18 prove-it）—— 隐形战力惩罚。

用户诉求：靠丹药堆修为 = 根基不稳/斗技虚浮，同级同斗技也远不敌；适量可以，
投机受罚；PVP/PVE 都生效；不展示到前台。原著证据见 docs/斗破根基裁定_*.md。

守四件事：
  1. root_factor 梯度：适量(≤15%)无罚、投机越重系数越低、下限 0.55；
  2. use_item 的丹药修为记账 pill_exp，苦修/战斗修为不记；
  3. PVE：同级同斗技，投机者打同一只怪更吃亏（输出/承伤双侧）；
  4. PVP：投机者 vs 同级正常修炼者，投机者败；stats/人物卡零新增字段（隐形）。

注入验证（错误实现塞回去，对应测试必须红）：
  * root_factor 恒返回 1.0 → 梯度/记账之外全部红；
  * _hit 去掉 `raw *= root` → PVE/PVP 失效红；
  * use_item 不记 pill_exp → 记账红。

运行：python tests/test_doupo_root_factor.py
"""
from __future__ import annotations

import os
import sys
import types

PLUGINS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "src", "plugins")
REPO_ROOT = os.path.dirname(os.path.dirname(PLUGINS_DIR))
for _p in (REPO_ROOT, PLUGINS_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)


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
    adapters = types.ModuleType("nonebot.adapters")
    onebot = types.ModuleType("nonebot.adapters.onebot")
    v11 = types.ModuleType("nonebot.adapters.onebot.v11")
    v11.MessageEvent = object
    v11.MessageSegment = object
    v11.Message = object
    sys.modules["nonebot.adapters"] = adapters
    sys.modules["nonebot.adapters.onebot"] = onebot
    sys.modules["nonebot.adapters.onebot.v11"] = v11


_stub_nonebot()

from doupo import core, config as cfg  # noqa: E402


def _p(lv=30, alch=3, pill=0):
    p = core.create_player("10001", "根基测试", "20001")
    p["lv"] = lv
    p["exp"] = 0
    p["alch"] = alch
    if pill:
        p["pill_exp"] = pill
    st = core.stats(p)
    p["hp"] = st["体力"]
    p["mp"] = core.mp_max(p)
    return p


def _fix_rng(seq):
    """循环取固定序列，保证 PVE/PVP 对比时随机流一致可比。"""
    it = iter(seq)
    import itertools
    cyc = itertools.cycle(seq)
    return lambda: next(cyc)


# ---------------------------------------------------------------------------
# 1. 系数梯度
# ---------------------------------------------------------------------------
def test_old_save_no_penalty():
    """老档无 pill_exp → 系数 1.0，零惩罚（天然兼容）。"""
    assert cfg.root_factor(_p(lv=30)) == 1.0
    assert cfg.root_factor({}) == 1.0
    assert cfg.root_factor({"lv": 30, "pill_exp": 0}) == 1.0


def test_tolerance_no_penalty():
    """适量丹药（占比≤15%）不罚——原著丹药只是辅助，萧炎常态。"""
    need = cfg.cum_exp_need(30)
    for ratio in (0.0, 0.05, 0.10, 0.15):
        p = _p(lv=30, pill=int(need * ratio))
        assert cfg.root_factor(p) == 1.0, (ratio, cfg.root_factor(p))


def test_speculation_penalty_monotonic():
    """投机度越高系数越低，100% 靠丹药 = 下限 0.55。"""
    need = cfg.cum_exp_need(30)
    vals = [cfg.root_factor(_p(lv=30, pill=int(need * r)))
            for r in (0.15, 0.3, 0.5, 0.8, 1.0, 1.5)]
    assert vals[0] == 1.0
    assert all(vals[i] >= vals[i + 1] for i in range(len(vals) - 1)), vals
    assert abs(vals[-1] - cfg.ROOT_MIN_FACTOR) < 1e-6, vals
    assert abs(vals[-2] - cfg.ROOT_MIN_FACTOR) < 1e-6, vals  # 1.0 与 1.5 都夹到下限
    assert cfg.ROOT_MIN_FACTOR < 1.0, "下限必须<1，否则惩罚无意义"


def test_low_level_no_div_zero():
    """1 级累计修为 0 时不除零，安全返回 1.0。"""
    assert cfg.root_factor(_p(lv=1, pill=9999)) == 1.0


# ---------------------------------------------------------------------------
# 2. 记账：只有丹药修为计入 pill_exp
# ---------------------------------------------------------------------------
def test_use_item_exp_records_pill_exp():
    """服用增修为丹药 → exp 与 pill_exp 同增；这是唯一记账入口。"""
    p = _p(lv=1)
    rec = next(r for r in cfg.RECIPES if r["effect"] == "exp" and r.get("value", 0) > 0)
    core.add_item(p, rec["name"], 1)
    before_exp = int(p.get("exp", 0))
    before_pill = int(p.get("pill_exp", 0))
    val = int(rec["value"])
    r = core.use_item(p, rec["name"])
    assert r["ok"], r
    assert int(p["exp"]) == before_exp + val
    assert int(p["pill_exp"]) == before_pill + val, "丹药修为必须记账"


def test_cultivate_does_not_record_pill_exp():
    """苦修/战斗等丹药以外的修为不记 pill_exp（因果链的另一侧）。"""
    p = _p(lv=1)
    p["last_settle"] = 0           # 从 0 计时，确保有离线收益
    r = core.settle(p, now_ts=100000)
    assert r["gain"] > 0
    assert int(p.get("pill_exp", 0)) == 0, "苦修修为不算外力"
    assert int(p["exp"]) > 0


# ---------------------------------------------------------------------------
# 3. PVE：同级同斗技，投机者更吃亏
# ---------------------------------------------------------------------------
def test_hit_output_damage_penalized():
    """直击 _hit 输出侧：同级同技同 rng，投机者的单击伤害必须更低。

    这条专门守 `raw *= root` 注入点——PVE/PVP 整体测试可能被防御侧掩盖，
    这里单独锁定输出伤害，去掉输出惩罚时必红。
    """
    lv = 30
    need = cfg.cum_exp_need(lv)
    normal = _p(lv=lv, pill=0)
    spec = _p(lv=lv, pill=need)
    entry = None   # 武器普攻，技能随机性最小，纯看根基系数
    dmg_solid = core._hit(normal, 400, entry, _fix_rng([0.5] * 50))["dmg"]
    dmg_spec = core._hit(spec, 400, entry, _fix_rng([0.5] * 50))["dmg"]
    root = cfg.root_factor(spec)
    assert root < 1.0
    assert dmg_spec < dmg_solid, (dmg_spec, dmg_solid, root)
    # 伤害应约按系数缩放（取整误差内）
    assert abs(dmg_spec - dmg_solid * root) <= 2, (dmg_spec, dmg_solid, root)


def test_pve_speculator_takes_more_damage():
    """同一只怪、同一 rng：投机者的出手伤害更低、承伤更高、打完剩血更少。

    ⚠️ 2026-11-19 取消 15 回合上限后，怪物血量必须选「两边都能打赢」的档位
    （这里 30000，实测正常 11 回合、投机 19 回合，都胜）。原来写 100000 时
    投机者会被打死，而**战败盘 hp 会被重置成「上限 × (1-LOSE_HP_ON_DEFEAT)」**
    （10400 × 0.7 = 7279），比胜者的残血还高 —— 跨胜负比 hp 是在比两种不同的
    口径，必然假红。承伤用 ``taken`` 比、剩余血只在「同胜负」前提下比。
    """
    lv = 30
    need = cfg.cum_exp_need(lv)
    normal = _p(lv=lv, pill=0)
    spec = _p(lv=lv, pill=need)          # 100% 靠丹药堆到同级
    # 同等级同斗技（都用默认已领悟的技能），只差根基
    mon = {"name": "测试魔兽", "lv": lv, "hp": 30000, "atk": 800,
           "def": 400, "exp": 0, "gold": 0}
    rn = core.fight(normal, dict(mon), rng=_fix_rng([0.5] * 200), roll_aid=False)
    rs = core.fight(spec, dict(mon), rng=_fix_rng([0.5] * 200), roll_aid=False)
    # 前提：两边都打赢了，剩余血量才可比
    assert rn["win"] and rs["win"], ("两边没都赢，hp 口径不可比",
                                     rn["win"], rs["win"])
    # 投机者对同一怪造成伤害更低（打掉的血更少）
    assert rn["mon_hp_left"] <= rs["mon_hp_left"], (rn["mon_hp_left"], rs["mon_hp_left"])
    # 承伤更高（总承伤 taken 与剩余血两个角度都守）
    assert rs["taken"] > rn["taken"], \
        "投机者防御打折应承受更多伤害——守 PVE 承伤侧注入：%s" % ((rn["taken"], rs["taken"]),)
    assert normal["hp"] > spec["hp"], (normal["hp"], spec["hp"])


# ---------------------------------------------------------------------------
# 4. PVP：投机者败给同级正常修炼者
# ---------------------------------------------------------------------------
def test_pvp_speculator_loses_to_solid():
    """同级同斗技，投机者（p）与正常修炼者（vic）切磋，投机者败。"""
    lv = 30
    need = cfg.cum_exp_need(lv)
    spec = _p(lv=lv, pill=need)
    solid = _p(lv=lv, pill=0)
    solid["qq"] = "20002"
    res = core.duel(spec, solid, rng=_fix_rng([0.5] * 200))
    assert res["win"] is False, "投机堆境界者同级切磋应败给扎实修炼者"
    assert float(cfg.root_factor(spec)) < cfg.root_factor(solid)


def test_pvp_moderate_pill_not_crushed():
    """适量丹药（≤15%）不应明显吃亏——避免误伤正常玩家。"""
    lv = 30
    need = cfg.cum_exp_need(lv)
    moderate = _p(lv=lv, pill=int(need * 0.10))
    solid = _p(lv=lv, pill=0)
    assert cfg.root_factor(moderate) == 1.0
    assert cfg.root_factor(solid) == 1.0
    # 系数相同 → 同级切磋不再有根基差距（随机流一致时胜负可任一，但系数必等）
    assert cfg.root_factor(moderate) == cfg.root_factor(solid)


# ---------------------------------------------------------------------------
# 5. 隐形：不进 stats / 不进人物卡数据
# ---------------------------------------------------------------------------
def test_invisible_not_in_stats_or_card():
    """root_factor 不进 stats 六维，也不进人物卡参数（前台零感知）。"""
    need = cfg.cum_exp_need(30)
    spec = _p(lv=30, pill=need)
    solid = _p(lv=30, pill=0)
    # stats 完全相同（系数不在面板里）
    assert core.stats(spec) == core.stats(solid), "六维面板不得暴露根基惩罚"
    assert core.power(spec) == core.power(solid), "战力数值（排行榜展示）不打折"
    # 人物卡数据也不含 root/pill 相关字段
    from doupo import card
    d = card.build_panel_data(spec) if hasattr(card, "build_panel_data") else None
    if d is not None:
        blob = repr(d)
        assert "root_factor" not in blob and "根基" not in blob and "pill_exp" not in blob


# ---------------------------------------------------------------------------
# runner
# ---------------------------------------------------------------------------
def _run_all():
    fns = [(k, v) for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    ok = 0
    fail = []
    for name, fn in fns:
        try:
            fn()
            ok += 1
            print(f"PASS  {name}")
        except Exception as e:  # noqa: BLE001
            fail.append((name, e))
            print(f"FAIL  {name}  ->  {type(e).__name__}: {e}")
    print("=" * 70)
    print(f"ROOT_FACTOR_TOTAL={len(fns)} PASSED={ok} FAILED={len(fail)}")
    return 0 if not fail else 1


if __name__ == "__main__":
    sys.exit(_run_all())
