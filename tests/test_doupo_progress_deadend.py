# -*- coding: utf-8 -*-
"""「进度类任务」与「控火闸」两处死锁的门禁（2026-11-20 用户反馈）。

两个真 bug，症状都是**玩家永远做不到**，而不是数值不对：

1. **主线卡在「强化玄重尺至 5 级」**
   进度只由 ``advance_task('weapon')`` 事件累加，而该事件只在真的点强化时触发。
   玩家在轮到这条任务之前就把武器练满（weapon=9），之后满级再也强化不了 →
   进度恒 0 → 主线永久卡死。这是**状态要求**（到没到 5 级），不是次数要求。

2. **傀儡控火闸永远不过**
   ``gate_fire_ctrl`` 读 ``p['craft_count']`` / ``p['high_grade_crafts']``，
   但生产代码**从没写过这两个字段**（只有测试手塞）→ 所有玩家都卡在「火候不济」。

prove-it 反证（把任一处改回去，本文件必红）：
  * ``core.main_value`` 改回 ``p['main_progress']`` → test_main_weapon_* 红
  * ``craft()`` 去掉计数 → test_craft_counts_* 红
  * ``craft_floor`` 返回 0 → test_legacy_save_not_deadlocked 红

运行：python tests/test_doupo_progress_deadend.py
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
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

from doupo import commands, config as cfg, core, puppet_core as pc  # noqa: E402
from doupo.store import Store  # noqa: E402

os.environ["DOUPO_CHANCE_RATE"] = "0"


def _p(lv: int = 40) -> dict:
    p = core.create_player("Q_DEAD", "测试", "群")
    p["lv"] = lv
    # 2026-11-24 入道不送火：炼丹前须持有任意火（紫火=atk 型，不影响炼药率）
    p["flames"]["紫火"] = 1
    p["hp"] = core.stats(p)["体力"]
    p["mp"] = core.mp_max(p)
    return p


def _main_weapon_task() -> dict:
    return next(t for t in cfg.MAIN_TASKS if t.get("kind") == "weapon")


# ---------------------------------------------------------------------------
# 1. 主线：武器类按「当前等级」判定
# ---------------------------------------------------------------------------
def test_main_weapon_task_is_state_based():
    """武器已练满、但事件计数为 0 → 主线必须能交（用户的真实存档就是这个形状）。"""
    t = _main_weapon_task()
    p = _p(81)
    p["main_idx"] = cfg.MAIN_TASKS.index(t)
    p["main_progress"] = 0
    p["weapon"] = 9                      # 早已满级
    assert core.main_value(p) >= int(t["target"]), core.main_value(p)
    r = core.main_submit(p)
    assert r.get("ok"), r
    assert p["main_idx"] == cfg.MAIN_TASKS.index(t) + 1, p["main_idx"]


def test_main_weapon_task_still_blocks_when_not_reached():
    """没到目标级数就不能交（别把闸拆了）。"""
    t = _main_weapon_task()
    p = _p(20)
    p["main_idx"] = cfg.MAIN_TASKS.index(t)
    p["main_progress"] = 0
    p["weapon"] = 1
    r = core.main_submit(p)
    assert not r.get("ok") and r.get("reason") == "unfinished", r
    assert r["progress"] == 1, r


def test_main_progress_counter_still_works():
    """事件计数那条路不能坏（新玩家边做边推）。"""
    t = _main_weapon_task()
    p = _p(40)
    p["main_idx"] = cfg.MAIN_TASKS.index(t)
    p["weapon"] = 0
    core.main_progress(p, "weapon", 1)
    assert core.main_value(p) == 1, core.main_value(p)


# ---------------------------------------------------------------------------
# 2. 支线：同一口径 + 不再白发已完成的武器委托
# ---------------------------------------------------------------------------
def test_side_weapon_task_is_state_based():
    t = dict(next(x for x in cfg.SIDE_TASK_POOL if x.get("kind") == "weapon"))
    t["progress"] = 0
    p = _p(40)
    p["weapon"] = 5
    p["task"] = t
    assert core.task_value(p) == 5, core.task_value(p)
    assert core.task_submit(p).get("ok")


def test_accept_skips_weapon_task_already_done():
    """已满级的人不该再收到「强化玄重尺 1 次」这种白给委托（否则接一次交一次刷金币）。"""
    p = _p(40)
    p["weapon"] = 9
    done = {x["name"] for x in cfg.SIDE_TASK_POOL
            if x.get("kind") == "weapon" and int(x["target"]) <= 9}
    assert done, "测试前提：池里至少有一条武器类支线"
    for i in range(40):
        p["task"] = None
        core.task_accept(p, rng=lambda i=i: (i % 40) / 40.0)
        assert p["task"]["name"] not in done, (i, p["task"]["name"])


# ---------------------------------------------------------------------------
# 3. 控火闸：真实累加 + 老档不被卡死
# ---------------------------------------------------------------------------
def _starter(grade_max: str) -> dict:
    """挑一份品阶低于 grade_max、有药方的配方，给足材料。

    ⚠️ 2026-11-26 起「有药方的丹一律不卖」，但 ``buy`` 字段仍留在丹方表里
    做市场价/图鉴展示，所以这里仍不要求 ``buy==0``，只按 ``need`` 挑可炼制的丹。
    """
    lim = cfg.alch_grade_idx(grade_max)
    rec = next(r for r in cfg.RECIPES
               if r.get("need")
               and cfg.alch_grade_idx(r.get("grade", "")) < lim)
    p = _p(60)
    p["alch"] = cfg.ALCH_MAX
    for m in rec["need"]:
        core.add_item(p, m, 99)
    p["mp"] = core.mp_max(p) * 5
    return p, rec


def test_craft_counts_single():
    """单发炼药必须真实累加炉数；低阶丹不加 high_grade_crafts。"""
    p, rec = _starter(cfg.PUPPET_HIGH_GRADE_FROM)
    assert int(p.get("craft_count", 0) or 0) == 0
    core.craft(p, rec["name"], rng=lambda: 0.0, now_ts=1000.0)   # 必成
    assert int(p["craft_count"]) == 1, p.get("craft_count")
    assert int(p.get("high_grade_crafts", 0) or 0) == 0, p.get("high_grade_crafts")


def test_craft_counts_batch():
    """批量连炼每炉各计一次（整批 3 炉 → +3）。"""
    p, rec = _starter(cfg.PUPPET_HIGH_GRADE_FROM)
    p["last_craft"] = 0
    r = core.craft_batch(p, rec["name"], 3, rng=lambda: 0.0, now_ts=10 ** 9)
    assert int(r["n_done"]) == 3, r
    assert int(p["craft_count"]) == 3, p.get("craft_count")


def test_high_grade_counts_on_high_pill():
    """炼成够品阶的丹才 +high_grade_crafts（用五品丹验）。"""
    rec = next(r for r in cfg.RECIPES
               if r.get("grade") == cfg.PUPPET_HIGH_GRADE_FROM and r.get("need"))
    p = _p(60)
    p["alch"] = cfg.ALCH_MAX
    for m in rec["need"]:
        core.add_item(p, m, 99)
    core.craft(p, rec["name"], rng=lambda: 0.0, now_ts=1000.0)
    assert int(p.get("high_grade_crafts", 0) or 0) == 1, p.get("high_grade_crafts")


def test_legacy_save_not_deadlocked():
    """老档（craft_count 字段根本不存在）用品阶下限兜住，不能永久卡在火候。"""
    p = _p(81)
    p["alch"] = cfg.PUPPET_ALCH_MIN
    assert "craft_count" not in p and "high_grade_crafts" not in p
    ok, why = pc.gate_fire_ctrl(p)
    assert ok, why
    assert pc.craft_floor(p) >= cfg.PUPPET_CRAFT_COUNT_MIN, pc.craft_floor(p)


def test_newbie_still_blocked():
    """新号该拦还是拦（门槛不能一改就形同虚设）。"""
    p = _p(1)
    p["alch"] = 0
    ok, why = pc.gate_fire_ctrl(p)
    assert not ok and "火候不济" in why, why
    # 报错必须写出「还差多少」，否则玩家只知道错了不知道怎么办
    assert "/%d" % cfg.PUPPET_CRAFT_COUNT_MIN in why, why


# ---------------------------------------------------------------------------
# 4. 主线渲染：进度封顶 + 门槛可执行提示（2026-11-26 黑盒 P1/P7）
# ---------------------------------------------------------------------------
def test_main_progress_display_capped_at_target():
    """超额完成（main_progress=5、target=3）不许显示「5/3 进度条满格」。"""
    t = cfg.MAIN_TASKS[0]
    with tempfile.TemporaryDirectory(prefix="main_cap_") as d:
        s = Store(path=os.path.join(d, "doupo.json"))
        asyncio.run(s.load())
        p = core.create_player("QCAP", "测试", "群")
        p["main_idx"] = cfg.MAIN_TASKS.index(t)
        p["main_progress"] = int(t["target"]) + 2   # 超额 2
        p["weapon"] = 0
        asyncio.run(s.put_player(p))
        txt = asyncio.run(commands.cmd_tasks(s, "QCAP"))
        assert "5/3" not in txt and f"/{t['target']}" in txt, txt
        assert "进度 %s/%s" % (t["target"], t["target"]) in txt, txt


def test_exp_display_capped_when_overflow():
    """2026-11-26 黑盒 P3：修为远超升级所需时，文本/人物卡都不许显示超大分子。"""
    with tempfile.TemporaryDirectory(prefix="exp_cap_") as d:
        s = Store(path=os.path.join(d, "doupo.json"))
        asyncio.run(s.load())
        p = core.create_player("QEXP", "测试", "群")
        p["lv"] = 2
        p["exp"] = cfg.exp_need(2) * 20 + 86     # 故意造一个 8000+/400 的溢出值
        p["last_settle"] = 10 ** 9               # 别让 settle 把 exp 结算掉
        asyncio.run(s.put_player(p))
        cult = asyncio.run(commands.cmd_cultivate(s, "QEXP"))
        panel = asyncio.run(commands.cmd_panel(s, "QEXP"))
        huge = str(cfg.exp_need(2) * 20 + 86)
        assert huge not in cult, cult
        assert huge not in panel, panel
        assert "%s/%s" % (cfg.exp_need(2), cfg.exp_need(2)) in cult, cult


def test_exp_template_caps_shown_exp():
    """人物卡模板：可突破时分子封顶（源码级守门，防改回直接打印 exp）。"""
    import pathlib
    tpl = (pathlib.Path(ROOT) / "src" / "plugins" / "qq_bridge" / "pic"
           / "templates" / "doupo.py").read_text(encoding="utf-8")
    assert "shown_exp = min(exp, need)" in tpl, "模板必须把修为分子封顶到 need"


def test_newbie_flame_hint_matches_direct_claim():
    """2026-11-26 黑盒 N3：入道文案不许再说「异火不会凭空而来」，
    因为 B 线兽火（如火云水炎）发「收服 <火名>」就能直接拿到。"""
    import pathlib
    src = (pathlib.Path(ROOT) / "src" / "plugins" / "doupo"
           / "commands.py").read_text(encoding="utf-8")
    assert "不会凭空而来" not in src, "入道引导仍与「收服即得」冲突"
    assert "寻常兽火发「收服 <火名>」" in src, "入道引导要说明可直接收服寻常火"
    # 行为：B 线火真能直接收服（不是只改文案骗人）
    b_flame = "火云水炎"
    assert (getattr(cfg, "FLAME_TIER", {}) or {}).get(b_flame) != "S", \
        "测试前提：火云水炎须为非 S 线（可直接收服）"


def test_common_unimplemented_cmds_never_silent():
    """2026-11-26 黑盒 P8：8 条高频非菜单口令必须有回包，不许再静默。"""
    import pathlib
    init = (pathlib.Path(ROOT) / "src" / "plugins" / "doupo"
            / "__init__.py").read_text(encoding="utf-8")
    assert "_unimplemented = on_regex(" in init, "缺少兜底 matcher"
    for kw in ("主线任务", "战力", "闭关", "成就", "签到", "炼器", "驯兽", "擂台"):
        assert '"%s":' % kw in init, "%s 未登记引导文案" % kw


def test_main_lv_hint_tells_remaining_breakthroughs():
    """任务达标但境界不足：提示必须给出「还差 N 次突破」，不是只说「先战斗」。"""
    t = next(x for x in cfg.MAIN_TASKS if int(x.get("require_lv") or 0) > 1)
    need = int(t["require_lv"])
    with tempfile.TemporaryDirectory(prefix="main_hint_") as d:
        s = Store(path=os.path.join(d, "doupo.json"))
        asyncio.run(s.load())
        p = core.create_player("QHINT", "测试", "群")
        p["main_idx"] = cfg.MAIN_TASKS.index(t)
        p["main_progress"] = int(t["target"])       # 条件达标
        p["lv"] = 2                                  # 境界不足
        asyncio.run(s.put_player(p))
        txt = asyncio.run(commands.cmd_main_submit(s, "QHINT"))
        assert "还差 %d 次突破" % (need - 2) in txt, txt
        assert "先发「战斗」历练" not in txt, txt


def _main() -> int:
    fns = [(n, f) for n, f in globals().items()
           if n.startswith("test_") and callable(f)]
    failed = 0
    for n, f in sorted(fns):
        try:
            f()
            print("PASS", n)
        except AssertionError as e:
            failed += 1
            print("FAIL", n, "->", e)
        except Exception as e:  # noqa: BLE001
            failed += 1
            print("ERR ", n, "->", type(e).__name__, str(e)[:300])
    print("\n%d/%d 通过，失败 %d" % (len(fns) - failed, len(fns), failed))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(_main())
