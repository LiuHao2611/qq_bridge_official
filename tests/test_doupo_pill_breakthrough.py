# -*- coding: utf-8 -*-
"""三纹青灵丹「真破境」守门（2026-11-18 prove-it）。

玩家报障原话：「确认下三纹青灵丹的效果是不是真的，我吃了那么多，没见突破到大斗师，
每次只加 1000 修为」——属实。旧实现是 ``effect="exp" value=1000``，desc 却照抄
原著承诺破境，等于卖假药：吃一百枚也破不了大斗师。

原著第 315 章原话（``docs`` 台账与原文一致）：
  「普通三纹青灵丹能够助斗师强者突破晋入大斗师的界限，若是大斗师强者服用，
    则能有少许几率提升服用者一星的等级，并无药效反噬。」
  「大斗师强者，在此阶之中，唯有一次服用三纹青灵丹的机会，若是再服用，
    则将会因为药体的抗性，最后做了平白功夫。」

守这几件事：
  1. 元数据：effect=breakthrough / to_realm=3 / star_chance∈(0,1) / 标签「破境」；
  2. 斗师九星服下 → **真的**晋入一星大斗师（lv+1、气血斗气回满、不扣修为）；
  3. 大斗师服下 → 命中升一星、未命中无惩罚（只补修为），不倒退；
  4. 同阶第二次 → 药体抗性：丹药照扣、星级不动（原著「白白浪费」）；
  5. 其他境界 → 不升级，按 value 转修为（不空手）；
  6. ``pill_use`` 真实表：往返/幂等/按人隔离，且不写进 players.data 的 JSON。

注入验证（把错误实现塞回去，对应测试必须红）：
  * effect 改回 "exp" → test_recipe_metadata / test_nine_star_breaks_through 红；
  * 九星分支删掉 → test_nine_star_breaks_through 红；
  * 抗性分支去掉 used_realms 判断 → test_resisted_second_dose 红；
  * store 的 mark_pill_used 改成 pass → test_store_roundtrip 红。

运行：python tests/test_doupo_pill_breakthrough.py
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
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
import importlib  # noqa: E402

# ⚠️ 不能 `from doupo import store`：包 __init__ 里 `store = store_mod.STORE`
# 把包属性盖成了**实例**，拿到的是对象不是模块（同一类雷已记在 puppet_store 上）。
store_mod = importlib.import_module("doupo.store")

PILL = "三纹青灵丹"
#: 大斗师起止（REALM_NAMES 下标 3）；九星斗师 = 大斗师前一级
DA_DOUSHI = cfg.REALM_NAMES.index("大斗师")
NINE_STAR_DOUSHI = 1
for _lv in range(1, cfg.MAX_LV + 1):
    if cfg.realm_index(_lv) == DA_DOUSHI:
        NINE_STAR_DOUSHI = _lv - 1
        break


def _p(lv=1, pills=1, name=None):
    p = core.create_player("10001", "破境测试", "20001")
    p["lv"] = int(lv)
    p["exp"] = 0
    p["pill_exp"] = 0
    st = core.stats(p)
    p["hp"] = st["体力"]
    p["mp"] = core.mp_max(p)
    if pills:
        core.add_item(p, name or PILL, int(pills))
    return p


def _yes():
    return 0.0


def _no():
    return 0.999


# ---------------------------------------------------------------------------
# 1. 元数据
# ---------------------------------------------------------------------------
def test_recipe_metadata():
    rec = cfg.RECIPE_BY_NAME[PILL]
    assert rec["effect"] == "breakthrough", "效果必须真破境，不能只给修为"
    assert int(rec["to_realm"]) == DA_DOUSHI, (rec["to_realm"], DA_DOUSHI)
    assert 0 < float(rec["star_chance"]) < 1, rec["star_chance"]
    assert cfg.effect_tag("breakthrough") == "破境", cfg.effect_tag("breakthrough")
    # 文案不许再承诺「只加修为」，也不许出现 markdown（群聊发不出）
    for bad in ("#", "**", "|"):
        assert bad not in rec["desc"]


def test_realm_boundary_sanity():
    """先钉死境界边界，下面几条都靠它。"""
    assert cfg.realm_index(NINE_STAR_DOUSHI) == DA_DOUSHI - 1
    assert cfg.realm_index(NINE_STAR_DOUSHI + 1) == DA_DOUSHI
    assert cfg.full_realm(NINE_STAR_DOUSHI).endswith("斗师")
    assert cfg.full_realm(NINE_STAR_DOUSHI + 1).endswith("大斗师")


# ---------------------------------------------------------------------------
# 2. 斗师九星 → 真的破入大斗师
# ---------------------------------------------------------------------------
def test_nine_star_breaks_through():
    p = _p(NINE_STAR_DOUSHI)
    exp_before = int(p["exp"])
    st_before = core.stats(p)
    res = core.use_item(p, PILL, used_counts={}, rng=_yes)
    assert res.get("ok"), res
    assert res.get("breakthrough") is True, res
    assert int(p["lv"]) == NINE_STAR_DOUSHI + 1, p["lv"]
    assert cfg.full_realm(int(p["lv"])).endswith("大斗师"), cfg.full_realm(p["lv"])
    # 破境不该倒扣修为
    assert int(p["exp"]) >= exp_before, (exp_before, p["exp"])
    # 回满气血斗气（原著破境后状态拉满）
    assert int(p["mp"]) == core.mp_max(p)
    assert int(p["hp"]) == int(core.stats(p)["体力"])
    assert st_before  # 面板可用
    # 丹药被消耗
    assert int(core.count_item(p, PILL)) == 0


def test_break_through_does_not_consume_star_slot():
    """在斗师阶破境不该占用「大斗师那一阶的一次机会」——所以不可返回 mark_used。"""
    p = _p(NINE_STAR_DOUSHI)
    res = core.use_item(p, PILL, used_counts={}, rng=_yes)
    assert res.get("mark_used") is None, res


# ---------------------------------------------------------------------------
# 3. 大斗师：命中升星 / 未命中无惩罚
# ---------------------------------------------------------------------------
def test_da_doushi_star_up_on_hit():
    lv = NINE_STAR_DOUSHI + 1
    p = _p(lv)
    res = core.use_item(p, PILL, used_counts={}, rng=_yes)
    assert res.get("ok") and res.get("star_up") is True, res
    assert int(p["lv"]) == lv + 1, p["lv"]
    assert res.get("mark_used") == DA_DOUSHI, res


def test_da_doushi_no_penalty_on_miss():
    """原著「并无药效反噬」：未命中不降星、不扣血，只补修为。"""
    lv = NINE_STAR_DOUSHI + 1
    p = _p(lv)
    hp, mp = int(p["hp"]), int(p["mp"])
    exp_before = int(p["exp"])
    res = core.use_item(p, PILL, used_counts={}, rng=_no)
    assert res.get("ok") and not res.get("star_up"), res
    assert int(p["lv"]) == lv, p["lv"]
    assert int(p["exp"]) == exp_before + int(cfg.RECIPE_BY_NAME[PILL]["value"])
    assert int(p["hp"]) >= hp and int(p["mp"]) >= mp, "不许反噬掉血"
    assert res.get("mark_used") == DA_DOUSHI, res


# ---------------------------------------------------------------------------
# 4. 同阶第二次：药体抗性，丹药照扣
# ---------------------------------------------------------------------------
def test_resisted_second_dose():
    lv = NINE_STAR_DOUSHI + 1
    p = _p(lv, pills=2)
    res = core.use_item(p, PILL, used_counts={DA_DOUSHI: 1}, rng=_yes)
    assert res.get("ok") is False, res
    assert res.get("reason") == "resisted", res
    assert int(p["lv"]) == lv, "抗性状态绝对不能升星"
    assert int(core.count_item(p, PILL)) == 1, "原著「白白浪费」——丹药必须扣掉"
    assert "抗性" in res["msg"]


def test_no_limit_when_used_counts_absent():
    """默认 used_counts=None → 不做限制（既有脚本/单测调用语义不变）。"""
    lv = NINE_STAR_DOUSHI + 1
    p = _p(lv, pills=2)
    r1 = core.use_item(p, PILL, rng=_yes)
    r2 = core.use_item(p, PILL, rng=_yes)
    assert r1.get("ok") and r2.get("ok"), (r1, r2)
    assert int(p["lv"]) == lv + 2, p["lv"]


# ---------------------------------------------------------------------------
# 5. 其他境界：不升级，转修为，不空手
# ---------------------------------------------------------------------------
def test_other_realms_convert_to_exp():
    for lv in (1, cfg.realm_index(1) and 5, NINE_STAR_DOUSHI - 5,
               NINE_STAR_DOUSHI + 12):
        if not (1 <= lv <= cfg.MAX_LV):
            continue
        if cfg.realm_index(lv) == DA_DOUSHI - 1:
            continue          # 上一级就可能踩到界限，跳过
        p = _p(lv)
        exp_before = int(p["exp"])
        res = core.use_item(p, PILL, used_counts={}, rng=_yes)
        assert res.get("ok"), (lv, res)
        assert int(p["lv"]) == lv, (lv, p["lv"])
        assert int(p["exp"]) == exp_before + int(cfg.RECIPE_BY_NAME[PILL]["value"]), lv


def test_plain_exp_pill_unchanged():
    """普通增修为丹（回灵丹）行为必须逐字节不变，别被破境分支带偏。"""
    name = next(r["name"] for r in cfg.RECIPES
                if r["effect"] == "exp" and int(r.get("value") or 0) > 0)
    p = core.create_player("10002", "常规", "20001")
    p["lv"] = 30
    core.add_item(p, name, 1)
    before = int(p["exp"])
    res = core.use_item(p, name)
    assert res.get("ok"), res
    assert int(p["exp"]) == before + int(cfg.RECIPE_BY_NAME[name]["value"])


# ---------------------------------------------------------------------------
# 6. pill_use 真实表
# ---------------------------------------------------------------------------
def test_store_roundtrip():
    tmp = tempfile.mkdtemp(prefix="pill_use_")
    s = store_mod.Store(path=os.path.join(tmp, "doupo.json"))

    async def run():
        assert await s.pill_used_counts("1", PILL) == {}
        assert await s.mark_pill_used("1", PILL, DA_DOUSHI) == 1
        assert await s.pill_used_counts("1", PILL) == {DA_DOUSHI: 1}
        # 同阶第二枚必须真的记成两行（紫心破障丹靠这个判上限）
        assert await s.mark_pill_used("1", PILL, DA_DOUSHI) == 2
        assert await s.pill_used_counts("1", PILL) == {DA_DOUSHI: 2}
        # 按人隔离
        assert await s.pill_used_counts("2", PILL) == {}
        # 按丹药隔离
        assert await s.pill_used_counts("1", "回灵丹") == {}
        # 真实表，不是 JSON；主键必须是自增 id（否则第二枚记不进去）
        with s._tx() as con:
            cols = [r[1] for r in con.execute("PRAGMA table_info(pill_use)")]
        assert cols == ["id", "qq", "name", "realm_idx", "used_at"], cols

    asyncio.run(run())


def test_legacy_pill_use_table_migrates():
    """旧形状（复合主键、无 id）必须无损迁移，不能因为改结构把记录清了。"""
    tmp = tempfile.mkdtemp(prefix="pill_mig_")
    path = os.path.join(tmp, "doupo.json")
    s0 = store_mod.Store(path=path)
    with s0._tx() as con:
        con.execute("DROP TABLE pill_use")
        con.execute("CREATE TABLE pill_use (qq TEXT NOT NULL, name TEXT NOT NULL,"
                    " realm_idx INTEGER NOT NULL, used_at REAL NOT NULL DEFAULT 0,"
                    " PRIMARY KEY(qq, name, realm_idx))")
        con.execute("INSERT INTO pill_use(qq,name,realm_idx,used_at)"
                    " VALUES('9',? ,3,1.0)", (PILL,))
    s1 = store_mod.Store(path=path)      # 重新 init → 触发迁移
    assert asyncio.run(s1.pill_used_counts("9", PILL)) == {DA_DOUSHI: 1}
    with s1._tx() as con:
        cols = [r[1] for r in con.execute("PRAGMA table_info(pill_use)")]
        legacy = con.execute("SELECT name FROM sqlite_master WHERE type='table'"
                             " AND name LIKE 'pill_use_legacy%'").fetchall()
    assert cols == ["id", "qq", "name", "realm_idx", "used_at"], cols
    assert not legacy, "迁完必须把旧表 drop 掉"


# ---------------------------------------------------------------------------
# 7. 紫心破障丹（同一类假药，原著第 321 章）：必升一星、同阶两枚
# ---------------------------------------------------------------------------
ZI = "紫心破障丹"


def test_zi_broken_metadata():
    rec = cfg.RECIPE_BY_NAME[ZI]
    assert rec["effect"] == "breakthrough", rec
    assert int(rec["to_realm"]) == DA_DOUSHI
    assert float(rec["star_chance"]) == 1.0, "原著是「稳步提升」，不是几率"
    assert int(rec["max_per_realm"]) == 2, "原著「同一级别能接连服两枚」"
    assert not rec.get("break_boundary"), "紫心只作用于大斗师，不能用来破师级界限"


def test_zi_two_stars_then_resist():
    p = _p(NINE_STAR_DOUSHI + 1, pills=3, name=ZI)
    base = NINE_STAR_DOUSHI + 1
    used = {}
    for i in (1, 2):
        res = core.use_item(p, ZI, used_counts=dict(used), rng=_no)  # rng 全 1 也必须成
        assert res.get("ok") and res.get("star_up") is True, (i, res)
        assert int(p["lv"]) == base + i, (i, p["lv"])
        used[DA_DOUSHI] = used.get(DA_DOUSHI, 0) + 1
    res = core.use_item(p, ZI, used_counts=dict(used), rng=_no)
    assert res.get("ok") is False and res.get("reason") == "resisted", res
    assert int(p["lv"]) == base + 2, "第三枚不许再升"
    assert int(core.count_item(p, ZI)) == 0, "抗性那枚也要扣掉"


def test_zi_does_not_break_doushi_boundary():
    """紫心破障丹「只能作用于大斗师」——斗师九星吃它不破境，只转修为。"""
    p = _p(NINE_STAR_DOUSHI, name=ZI)
    exp_before = int(p["exp"])
    res = core.use_item(p, ZI, used_counts={}, rng=_yes)
    assert res.get("ok") and not res.get("breakthrough"), res
    assert int(p["lv"]) == NINE_STAR_DOUSHI, p["lv"]
    assert int(p["exp"]) == exp_before + int(cfg.RECIPE_BY_NAME[ZI]["value"])



def test_pill_use_is_not_in_player_json():
    """最高声明：玩家数据进库表，不许再往 players.data 塞新字段。"""
    p = _p(NINE_STAR_DOUSHI)
    core.use_item(p, PILL, used_counts={}, rng=_yes)
    blob = json.dumps(p, ensure_ascii=False)
    for bad in ("pill_use", "pill_realms", "used_realms"):
        assert bad not in blob, "不许把服药记录写进玩家 JSON：%s" % bad


def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in tests:
        try:
            fn()
            print("  PASS %s" % fn.__name__)
        except AssertionError as e:
            failed += 1
            print("  FAIL %s: %s" % (fn.__name__, e))
        except Exception as e:  # noqa: BLE001
            failed += 1
            print("  ERROR %s: %s: %s" % (fn.__name__, type(e).__name__, e))
    print("\n%d/%d passed" % (len(tests) - failed, len(tests)))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
