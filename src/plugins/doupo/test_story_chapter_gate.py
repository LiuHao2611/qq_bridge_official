# -*- coding: utf-8 -*-
"""剧情章节门禁守门测试（2026-11-25 · G1=A + G2=「怪等级按章节走」）。

用户原话（本次改造的起点，逐字引用）：

    「G1,用A方案，G2，取消玩家等级抬高怪等级，怪等级按章节走。」

拆成两条可验证的契约：

  * **G1=A**：`MAIN_TASKS` 一章一条（5 → 13）。因为 `story_chapter = main_idx + 1`，
    只有 5 条任务时 `CHAPTER_SPECIES` 里第 7–13 章永远开不出来（表里是死数据）。
  * **G2**：怪等级 = ``min(玩家等级, 该怪所属章的基准等级)``。玩家等级只能把怪
    压**低**（保护章初新手），永远抬不**高**（回低章刷怪零收益，逼推主线）。

## 三层职责，任何一层降级都必须让本文件变红

① ``ensure_story_chapter`` —— 读侧迁移：老档缺字段显式 ``is None`` 补
   ``main_idx + 1``；**禁止** ``p.get(...) or 1``（0 是合法值，会被吞成 1）。
② ``chapter_pool`` —— 章 → 怪池并集，**硬锁**：池空必抛 ``ValueError``，
   绝不因「池子太小」回落抽全表（那会让第 1 章遇到魂灭生）。
③ ``_monster_lv_for`` / ``monster_scaled(at_lv=)`` —— 怪实例等级与数值都由
   章节基准反解，与具体玩家强度无关。

## 为什么这些守门必须存在（踩过的坑）

`test_core` / `test_commands` 只覆盖「主线能交、能升级」，**不覆盖**「哪些怪该出现」。
改造前 `pick_encounter` 签名里根本没有章节参数，交完第 1 章再打 10 场，14 次遭遇
全是嗜血鼠（真跑证据见 `docs/新玩家战斗全流程模拟_2026-11-25.md`）——
主线与怪池脱节，「开通区域」只是文案。本文件把这条链路钉死在测试里。

跑法：``python src/plugins/doupo/test_story_chapter_gate.py``（exit 0 = 全绿）
"""
from __future__ import annotations

import asyncio
import importlib.util
import os
import random
import sys
import tempfile
import types

# PLUGINS_DIR 供 `from doupo import ...`；REPO_ROOT 供插件 __init__.py 里的
# `from src.game_mode import ...`。裸跑时 CWD 不保证是仓库根，两者缺一即 ImportError。
PLUGINS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO_ROOT = os.path.dirname(os.path.dirname(PLUGINS_DIR))
for _p in (REPO_ROOT, PLUGINS_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# 全部落盘路径隔离到临时目录：绝不碰插件包 data/ 里的生产存档。
_TMP = tempfile.mkdtemp(prefix="doupo_chgate_")
os.environ["DOUPO_DATA_PATH"] = os.path.join(_TMP, "doupo.json")
os.environ["QQ_LOG_DIR"] = os.path.join(_TMP, "logs")
os.environ["QQ_DATA_DIR"] = os.path.join(_TMP, "qdata")
# 机遇是概率事件，会在战斗回执里插话、影响首行断言；本文件不验机遇。
os.environ["DOUPO_CHANCE_RATE"] = "0"
os.environ.setdefault("DOUPO_TOWER_SESSION_PATH",
                      os.path.join(_TMP, "tower_session.json"))


def _stub_nonebot():
    """不启动 nonebot：本文件只测业务层（与 test_commands 同一套桩）。"""
    nb = types.ModuleType("nonebot")
    nb.get_driver = lambda: types.SimpleNamespace(on_startup=lambda f: f)
    nb.get_bot = lambda: types.SimpleNamespace(send=lambda *a, **k: None)

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
    sys.modules["nonebot.adapters.onebot.v11"].MessageEvent = object
    sys.modules["nonebot.adapters.onebot.v11"].MessageSegment = \
        types.SimpleNamespace(image=lambda data: ("[image]", len(data or b"")))
    sys.modules["nonebot.adapters.onebot.v11"].Message = object


_stub_nonebot()

from doupo import commands, core, config as cfg  # noqa: E402
from doupo.store import Store  # noqa: E402

RES = []


def check(name, ok, detail=""):
    RES.append((name, bool(ok), str(detail)))


G = "900000009"
_MON = {str(m["name"]): m for m in cfg.MONSTERS}
_SEQ = [0]


def newp(lv=1, qq=None):
    """裸装新号（无火/无宗门/默认玄重尺），只调等级 —— 与 _baseline_at 同源。"""
    _SEQ[0] += 1
    p = core.create_player(qq or ("8%05d" % _SEQ[0]), "测%d" % _SEQ[0], G)
    p["lv"] = int(lv)
    p["exp"] = 0
    core._refill(p)
    return p


def spec(name):
    return _MON[str(name)]


def names_of(specs):
    return [str(s["name"]) for s in specs]


def ch_names(ch):
    """前 ch 章的怪名并集（本文件自算，不调 chapter_pool —— 避免同源同错）。"""
    out = []
    for c in range(1, int(ch) + 1):
        for nm in cfg.CHAPTER_SPECIES.get(c, ()):
            if nm not in out:
                out.append(nm)
    return out


def sample(ch, n, draws=600, seed=11):
    """用 pick_encounter 真抽 draws 次，返回出现过的怪名集合。"""
    p = newp(lv=cfg.chapter_base_lv(ch))
    rng = random.Random(seed).random
    seen = set()
    for _ in range(draws):
        for s in core.pick_encounter(p, n, rng, chapter=ch):
            seen.add(str(s["name"]))
    return seen


def _load_module(path, name):
    s = importlib.util.spec_from_file_location(name, str(path))
    mod = importlib.util.module_from_spec(s)
    sys.modules[name] = mod
    s.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# A. 章节数据自洽（config 是唯一真源，不允许手抄错位）
# ---------------------------------------------------------------------------
def group_a_data():
    check("CHAPTER_COUNT == 13", cfg.CHAPTER_COUNT == 13, str(cfg.CHAPTER_COUNT))
    check("MAIN_TASKS 条数 == CHAPTER_COUNT（少一条就少一章永远开不出来）",
          len(cfg.MAIN_TASKS) == cfg.CHAPTER_COUNT,
          "tasks=%d chapters=%d" % (len(cfg.MAIN_TASKS), cfg.CHAPTER_COUNT))

    empty = [c for c in range(1, cfg.CHAPTER_COUNT + 1)
             if not cfg.CHAPTER_SPECIES.get(c)]
    check("每章至少 1 种怪（无空章）", not empty, "空章=%s" % empty)

    orphan = [(c, nm) for c in cfg.CHAPTER_SPECIES
              for nm in cfg.CHAPTER_SPECIES[c] if nm not in _MON]
    check("章节怪名全部命中 MONSTERS（零编造实体）", not orphan, str(orphan[:5]))

    missing = [nm for nm in _MON if nm not in ch_names(cfg.CHAPTER_COUNT)]
    check("MONSTERS 全部有归属章（%d 只）" % len(_MON), not missing,
          "无章怪=%s" % missing[:5])
    check("怪名不跨章重复（章归属唯一）",
          len(ch_names(cfg.CHAPTER_COUNT)) == sum(
              len(cfg.CHAPTER_SPECIES[c]) for c in cfg.CHAPTER_SPECIES),
          str(len(ch_names(cfg.CHAPTER_COUNT))))

    # 区间由 REALM_TABLE 推导，不手抄：逐章对一遍
    bad = []
    for ch in range(1, cfg.CHAPTER_COUNT + 1):
        lo, hi = cfg.chapter_lv_range(ch)
        want_lo = cfg.realm_lv(ch - 1)
        want_hi = (cfg.realm_lv(ch) - 1 if ch < cfg.CHAPTER_COUNT else cfg.MAX_LV)
        if (lo, hi) != (want_lo, want_hi):
            bad.append((ch, (lo, hi), (want_lo, want_hi)))
    check("chapter_lv_range 逐章 == REALM_TABLE 推导值", not bad, str(bad[:3]))

    rt = [ch for ch in range(1, cfg.CHAPTER_COUNT + 1)
          for v in cfg.chapter_lv_range(ch) if cfg.chapter_of_lv(v) != ch]
    check("chapter_of_lv 对区间两端往返一致", not rt, str(rt[:5]))

    base_bad = []
    for ch in range(1, cfg.CHAPTER_COUNT + 1):
        b = cfg.chapter_base_lv(ch)
        lo, hi = cfg.chapter_lv_range(ch)
        if not (lo <= b <= hi) or cfg.chapter_of_lv(b) != ch:
            base_bad.append((ch, b, (lo, hi)))
    check("chapter_base_lv 落在本章境界区间内", not base_bad, str(base_bad[:3]))

    req_bad = [(i, t.get("require_lv"), cfg.chapter_lv_range(i)[1])
               for i, t in enumerate(cfg.MAIN_TASKS, 1)
               if int(t.get("require_lv") or 0) != cfg.chapter_lv_range(i)[1]]
    check("require_lv == 本章区间上限（逐条）", not req_bad, str(req_bad[:3]))


# ---------------------------------------------------------------------------
# B. ensure_story_chapter：读侧迁移，禁 `or` 兜底
# ---------------------------------------------------------------------------
def group_b_migrate():
    p = newp()
    check("新档自带 story_chapter=1", int(p.get("story_chapter", 0)) == 1,
          str(p.get("story_chapter")))

    old = {"main_idx": 3, "lv": 30}
    got = core.ensure_story_chapter(old)
    check("老档缺字段：main_idx=3 → 补 4 并写回",
          got == 4 and int(old.get("story_chapter") or 0) == 4,
          "ret=%s stored=%s" % (got, old.get("story_chapter")))

    old0 = {"main_idx": 0}
    check("老档缺字段：main_idx=0 → 补 1",
          core.ensure_story_chapter(old0) == 1
          and int(old0.get("story_chapter") or 0) == 1,
          str(old0))

    over = {"main_idx": 99}
    check("老档 main_idx 越界 → 夹到 CHAPTER_COUNT",
          core.ensure_story_chapter(over) == cfg.CHAPTER_COUNT,
          str(over))

    # 🚨 `or` 兜底会把两项一起搞坏：合法的 0 被吞成 1 并**写回**（改写存档），
    # 且「缺字段 → main_idx+1」的迁移分支变成永不可达的死代码。
    zero = {"story_chapter": 0, "main_idx": 4}
    ret0 = core.ensure_story_chapter(zero)
    check("显式 story_chapter=0 不被 `or` 吞（原值不得被改写）",
          int(zero["story_chapter"]) == 0,
          "stored=%s（被写成 1 或 main_idx+1 即为 `or` 兜底）" % zero["story_chapter"])
    check("显式 0 时返回仍夹到合法章号",
          1 <= ret0 <= cfg.CHAPTER_COUNT, "ret=%s" % ret0)

    dirty = [{"story_chapter": v} for v in (-5, 0, 1, 13, 999)]
    outs = [core.ensure_story_chapter(d) for d in dirty]
    check("返回值恒在 [1, CHAPTER_COUNT]",
          all(1 <= o <= cfg.CHAPTER_COUNT for o in outs), str(outs))


# ---------------------------------------------------------------------------
# C. chapter_pool：硬锁 + 池空必抛
# ---------------------------------------------------------------------------
def group_c_pool():
    check("chapter_pool(1) 恰为第 1 章 2 种",
          sorted(names_of(core.chapter_pool(1))) == sorted(ch_names(1)),
          str(names_of(core.chapter_pool(1))))
    check("chapter_pool(1) == {嗜血鼠, 岩蛇}（真值锚）",
          set(names_of(core.chapter_pool(1))) == {"嗜血鼠", "岩蛇"},
          str(names_of(core.chapter_pool(1))))

    mono = [ch for ch in range(1, cfg.CHAPTER_COUNT)
            if not set(names_of(core.chapter_pool(ch)))
            <= set(names_of(core.chapter_pool(ch + 1)))]
    check("池并集逐章单调（开通只增不减）", not mono, str(mono))

    check("chapter_pool(13) == 全部 %d 只" % len(_MON),
          sorted(names_of(core.chapter_pool(cfg.CHAPTER_COUNT))) == sorted(_MON),
          str(len(core.chapter_pool(cfg.CHAPTER_COUNT))))

    # 池空必须抛错：静默降级会让第 1 章出现魂灭生，比报错难查一万倍。
    orig = cfg.CHAPTER_SPECIES
    raised, msg, got = False, "", None
    try:
        cfg.CHAPTER_SPECIES = {1: ("查无此怪甲", "查无此怪乙")}
        try:
            got = core.chapter_pool(1)
        except ValueError as e:
            raised, msg = True, str(e)
    finally:
        cfg.CHAPTER_SPECIES = orig
    check("池空必抛 ValueError（绝不回落全表）",
          raised and got is None, "raised=%s got=%s" % (raised, got))
    check("池空错误信息含章号（可排障）",
          raised and ("1" in msg and "CHAPTER_SPECIES" in msg), msg[:80])


# ---------------------------------------------------------------------------
# D. pick_encounter(chapter=) —— 未开通章的怪一个都不许出现
# ---------------------------------------------------------------------------
def group_d_hardlock():
    seen1 = sample(1, 3, draws=600)
    check("第 1 章抽样 600 次只出现本章 2 种",
          seen1 <= set(ch_names(1)), "越界=%s" % sorted(seen1 - set(ch_names(1)))[:5])
    check("第 1 章抽样确实覆盖到两种（非恒等/非空抽）",
          seen1 == set(ch_names(1)), str(sorted(seen1)))

    seen3 = sample(3, 3, draws=400, seed=23)
    check("第 3 章抽样只出现前 3 章并集",
          seen3 <= set(ch_names(3)), "越界=%s" % sorted(seen3 - set(ch_names(3)))[:5])

    # 对照组：不传 chapter 时旧口径仍在（等级窗口），能抽到 ch1 之外的怪。
    # 没有这条，上面两条就有可能是「恒真」——因为池子本来就只有那两只。
    p40 = newp(lv=40)
    rng = random.Random(5).random
    legacy = set()
    for _ in range(200):
        legacy |= {str(s["name"]) for s in core.pick_encounter(p40, 6, rng)}
    check("对照：chapter=None 走旧等级窗口，能抽到第 1 章之外的怪",
          bool(legacy - set(ch_names(1))), str(len(legacy)))


# ---------------------------------------------------------------------------
# E. _monster_lv_for：min 封顶（只能压低，不能抬高）
# ---------------------------------------------------------------------------
def group_e_lv_for():
    mouse = spec("嗜血鼠")          # 物种表 lv=9，但归属第 1 章（基准 5）
    patrol = spec("巡山妖")         # 归属第 2 章（基准 14）

    check("归属章反查：嗜血鼠→ch1 / 巡山妖→ch2",
          core.chapter_of_species("嗜血鼠") == 1
          and core.chapter_of_species("巡山妖") == 2,
          "%s/%s" % (core.chapter_of_species("嗜血鼠"),
                     core.chapter_of_species("巡山妖")))
    check("章外物种 → None（不瞎猜）",
          core.chapter_of_species("查无此怪") is None, "")

    check("lv1 打 ch1 → 怪 lv1（不托底，否则新手必被碾压）",
          core._monster_lv_for(newp(lv=1), mouse, 1) == 1,
          str(core._monster_lv_for(newp(lv=1), mouse, 1)))
    check("lv5 打 ch1 → 怪 lv5（等于基准）",
          core._monster_lv_for(newp(lv=5), mouse, 1) == 5, "")
    check("lv9 打 ch1 → 怪仍 lv5（玩家等级抬不高它）",
          core._monster_lv_for(newp(lv=9), mouse, 1) == 5,
          str(core._monster_lv_for(newp(lv=9), mouse, 1)))
    check("lv40 回 ch1 → 怪仍 lv5（刷低章零收益的数值根基）",
          core._monster_lv_for(newp(lv=40), mouse, 1) == 5,
          str(core._monster_lv_for(newp(lv=40), mouse, 1)))
    check("lv9 打 ch2 的巡山妖 → 怪 lv9（低于基准取玩家）",
          core._monster_lv_for(newp(lv=9), patrol, 2) == 9,
          str(core._monster_lv_for(newp(lv=9), patrol, 2)))
    check("lv40 打 ch2 的巡山妖 → 怪封顶 lv14",
          core._monster_lv_for(newp(lv=40), patrol, 2) == 14,
          str(core._monster_lv_for(newp(lv=40), patrol, 2)))
    check("chapter=None → None（旧口径逐字节不变）",
          core._monster_lv_for(newp(lv=40), mouse, None) is None, "")


# ---------------------------------------------------------------------------
# F. monster_scaled(at_lv=)：数值由章节基准反解，且不改真源
# ---------------------------------------------------------------------------
def group_f_scale():
    mouse = spec("嗜血鼠")
    m_lo = core.monster_scaled(newp(lv=1), mouse, at_lv=5)
    m_hi = core.monster_scaled(newp(lv=40), mouse, at_lv=5)
    check("同一 at_lv：lv1 与 lv40 玩家看到的怪 hp 完全相同",
          int(m_lo["hp"]) == int(m_hi["hp"]),
          "%d vs %d" % (m_lo["hp"], m_hi["hp"]))
    check("同一 at_lv：atk 也完全相同",
          int(m_lo["atk"]) == int(m_hi["atk"]),
          "%d vs %d" % (m_lo["atk"], m_hi["atk"]))
    check("实例 lv 就地改成 at_lv",
          int(m_lo["lv"]) == 5 and int(m_hi["lv"]) == 5,
          "%s/%s" % (m_lo["lv"], m_hi["lv"]))

    # 对照：不传 at_lv 的旧口径，怪必须跟着玩家涨（否则上一条可能是恒真）
    l_lo = core.monster_scaled(newp(lv=1), mouse)
    l_hi = core.monster_scaled(newp(lv=40), mouse)
    check("对照：at_lv=None 旧口径下 lv40 的怪明显比 lv1 的厚",
          int(l_hi["hp"]) > int(l_lo["hp"]) * 2,
          "%d vs %d" % (l_hi["hp"], l_lo["hp"]))

    check("真源不被改写：cfg.MONSTERS 里嗜血鼠 lv 仍是 9",
          int(spec("嗜血鼠")["lv"]) == 9, str(spec("嗜血鼠")["lv"]))
    check("真源不被改写：全部 MONSTERS 仍是创建时那份 lv 序列",
          [int(m["lv"]) for m in cfg.MONSTERS] == [int(m["lv"]) for m in _MON.values()],
          "")

    b1, b2 = core._baseline_at(5), core._baseline_at(5)
    check("_baseline_at 命中缓存（同对象）", b1 is b2, "")
    check("_baseline_at 的基准号等级正确", int(core._baseline_at(5)["lv"]) == 5, "")


# ---------------------------------------------------------------------------
# G. 经验预算：按怪等级算（回低章经验被钳制）
# ---------------------------------------------------------------------------
def group_g_budget():
    p40 = newp(lv=40)
    b5 = core.encounter_exp_budget(p40, 3, base_lv=5)
    b14 = core.encounter_exp_budget(p40, 3, base_lv=14)
    b40 = core.encounter_exp_budget(p40, 3)
    check("base_lv=5 的预算不到玩家等级预算的 1/10",
          b5 * 10 < b40, "%d vs %d（比值 %.1f×）" % (b5, b40, b40 / max(1, b5)))
    check("预算随 base_lv 单调（5 < 14 < 40）", b5 < b14 < b40,
          "%d / %d / %d" % (b5, b14, b40))
    check("base_lv=None 与旧公式逐字节一致",
          b40 == int(cfg.exp_need(40) / core.encounter_exp_divisor(40)
                     * core.flame_exp_mult(p40)
                     * (1.0 + min(float(cfg.ENCOUNTER_EXP_HARD_CAP),
                                  float(cfg.ENCOUNTER_EXP_HARD_STEP) * 2))),
          str(b40))
    check("预算份额整除性质仍成立：Σ份额 == 预算",
          core.encounter_exp_share(b5, 3, 3) == b5, str(b5))


# ---------------------------------------------------------------------------
# H. encounter(chapter=) 端到端：怪种、怪等级、经验三者同源
# ---------------------------------------------------------------------------
def group_h_encounter():
    p40 = newp(lv=40)
    r = core.encounter(p40, rng=random.Random(7).random, chapter=1, auto_pill=False)
    det = r["detail"]
    check("lv40 打 ch1：抽到的怪全在第 1 章池内",
          {str(d["name"]) for d in det} <= set(ch_names(1)),
          str(sorted({str(d["name"]) for d in det})))
    check("lv40 打 ch1：每只怪实例 lv 都是 5（不随玩家涨）",
          all(int(d["lv"]) == 5 for d in det), str([d["lv"] for d in det]))
    budget = core.encounter_exp_budget(p40, len(det), base_lv=5)
    wins = sum(1 for d in det if d["win"])
    check("lv40 打 ch1：经验 == 按怪等级算的预算份额（不是 lv40 的巨额）",
          int(r["exp"]) == core.encounter_exp_share(budget, len(det), wins),
          "exp=%s budget=%s wins=%s" % (r["exp"], budget, wins))
    check("lv40 打 ch1：单场经验 < 500（真值锚，防公式漂移）",
          0 <= int(r["exp"]) < 500, str(r["exp"]))

    p1 = newp(lv=1)
    r1 = core.encounter(p1, rng=random.Random(7).random, chapter=1, auto_pill=False)
    check("lv1 打 ch1：怪跟着降到 lv1（min 生效，不是固定 lv5）",
          all(int(d["lv"]) == 1 for d in r1["detail"]),
          str([d["lv"] for d in r1["detail"]]))

    p9 = newp(lv=9)
    r9 = core.encounter(p9, rng=random.Random(3).random, chapter=2, auto_pill=False)
    want = {d["name"]: min(9, cfg.chapter_base_lv(core.chapter_of_species(d["name"])))
            for d in r9["detail"]}
    check("lv9 打 ch2：混章时按各自所属章封顶（ch1 怪 lv5 / ch2 怪 lv9）",
          all(int(d["lv"]) == want[d["name"]] for d in r9["detail"]),
          str([(d["name"], d["lv"]) for d in r9["detail"]]))

    p40b = newp(lv=40)
    rl = core.encounter(p40b, rng=random.Random(7).random, auto_pill=False)
    check("对照：不传 chapter 时怪不再是清一色 lv5（旧口径仍在用）",
          any(int(d["lv"]) != 5 for d in rl["detail"]),
          str([d["lv"] for d in rl["detail"]]))


# ---------------------------------------------------------------------------
# I. main_submit：require_lv 拦截 + 章节与任务同一笔写
# ---------------------------------------------------------------------------
def group_i_submit():
    p = newp(lv=1)
    snap = (int(p["main_idx"]), int(p["gold"]), int(p["exp"]), int(p["story_chapter"]))
    r = core.main_submit(p)
    check("主线未达成 → reason=unfinished", r.get("reason") == "unfinished",
          str(r.get("reason")))
    check("主线未达成：四项全不变（无半截写入）",
          snap == (int(p["main_idx"]), int(p["gold"]), int(p["exp"]),
                   int(p["story_chapter"])), str(snap))

    p2 = newp(lv=1)
    p2["main_progress"] = int(cfg.MAIN_TASKS[0]["target"])
    snap2 = (int(p2["main_idx"]), int(p2["gold"]), int(p2["exp"]),
             int(p2["story_chapter"]))
    r2 = core.main_submit(p2)
    check("等级不够 → reason=lv（G1 门槛真的拦）", r2.get("reason") == "lv",
          str(r2.get("reason")))
    check("等级不够：main_idx/gold/exp/story_chapter 四项全不变",
          snap2 == (int(p2["main_idx"]), int(p2["gold"]), int(p2["exp"]),
                    int(p2["story_chapter"])), str(snap2))
    check("等级不够：回报里明确给出 need_lv 与当前 lv",
          int(r2.get("need_lv") or 0) == cfg.chapter_lv_range(1)[1]
          and int(r2.get("lv") or 0) == 1, str(r2.get("need_lv")))

    walker = newp(lv=1)
    bad = []
    for i, t in enumerate(cfg.MAIN_TASKS, 1):
        walker["lv"] = int(t["require_lv"])
        walker["main_progress"] = int(t["target"])
        rr = core.main_submit(walker)
        want_ch = min(cfg.CHAPTER_COUNT, i + 1)
        if not rr.get("ok") or int(walker["main_idx"]) != i \
                or int(walker["story_chapter"]) != want_ch:
            bad.append("第%d章 ok=%s idx=%s ch=%s(要 %d)"
                       % (i, rr.get("ok"), walker["main_idx"],
                          walker["story_chapter"], want_ch))
    check("13 章逐章推进：story_chapter == main_idx + 1（同一笔写）",
          not bad, "; ".join(bad[:3]))
    check("走完 13 章：story_chapter 停在 CHAPTER_COUNT",
          int(walker["story_chapter"]) == cfg.CHAPTER_COUNT,
          str(walker["story_chapter"]))
    check("走完 13 章：next 为 None（无 14 章可开）",
          core.current_main(walker) is None, "")
    check("走完 13 章：再交 → reason=done",
          core.main_submit(walker).get("reason") == "done", "")


# ---------------------------------------------------------------------------
# J. 文案层：地点坐标露出、章号不露出（2026-11-25 用户「去掉第几章节的声明」）
# ---------------------------------------------------------------------------
async def group_j_text(store):
    check("chapter_line 单值形态（区域 + lv，无章号）",
          "乌坦城·萧家后山" in commands.txt.chapter_line(1, "乌坦城·萧家后山", 5)
          and "lv5" in commands.txt.chapter_line(1, "乌坦城·萧家后山", 5)
          and "章" not in commands.txt.chapter_line(1, "乌坦城·萧家后山", 5),
          commands.txt.chapter_line(1, "乌坦城·萧家后山", 5))
    line = commands.txt.chapter_line(2, "魔兽山脉外围", 5, 14)
    check("chapter_line 区间形态（混章）含 lv5-14 且无章号",
          "lv5-14" in line and "章" not in line, line)

    a = newp(lv=1, qq="710001")
    await store.put_player(a)
    t1 = await commands.cmd_fight(store, "710001")
    check("cmd_fight lv1：首行露区域、不露「第 N 章」",
          t1.splitlines()[0].startswith("📍 乌坦城·萧家后山")
          and "章" not in t1.splitlines()[0], t1.splitlines()[0][:70])
    check("cmd_fight lv1：首行露「妖物 lv1」",
          "妖物 lv1" in t1.splitlines()[0], t1.splitlines()[0][:70])

    b = newp(lv=40, qq="710002")
    await store.put_player(b)
    t2 = await commands.cmd_fight(store, "710002")
    check("cmd_fight lv40 回 ch1：首行仍写「妖物 lv5」",
          "妖物 lv5" in t2.splitlines()[0], t2.splitlines()[0][:70])

    c = newp(lv=9, qq="710003")
    c["main_progress"] = int(cfg.MAIN_TASKS[0]["target"])
    await store.put_player(c)
    t3 = await commands.cmd_main_submit(store, "710003")
    check("交第 1 章回执：露「第 2 章开通」+「此地妖物 lv14」",
          "第 2 章开通" in t3 and "此地妖物 lv14" in t3, t3[:120])
    check("交第 1 章回执：给出下一章与境界要求",
          "下一章" in t3 and cfg.full_realm(cfg.chapter_lv_range(2)[1]) in t3,
          t3[:200])

    d = newp(lv=9, qq="710004")
    await store.put_player(d)
    t4 = await commands.cmd_tasks(store, "710004")
    check("任务面板首行露「📍 第 1 章」", "📍 第 1 章" in t4.splitlines()[0],
          t4.splitlines()[0][:70])
    check("任务面板露「境界要求」并标记是否已达",
          "境界要求" in t4 and "已达" in t4, t4[:200])

    e = newp(lv=1, qq="710005")
    await store.put_player(e)
    t5 = await commands.cmd_tasks(store, "710005")
    check("任务面板 lv 不够时写「当前」而不是「已达」",
          "境界要求" in t5 and "已达" not in t5, t5[:200])


# ---------------------------------------------------------------------------
# K. 按钮：fight_text 与 battle 绝不混用
# ---------------------------------------------------------------------------
def group_k_buttons():
    path = os.path.join(REPO_ROOT, "src/plugins/qq_bridge/channel/doupo_buttons.py")
    db = _load_module(path, "chgate_doupo_buttons")
    ft = tuple(db.CARD_ACTIONS.get("fight_text") or ())
    bt = tuple(db.CARD_ACTIONS.get("battle") or ())
    check("CARD_ACTIONS 登记 fight_text", bool(ft), str(ft))
    check("fight_text 含「任务」「战斗回看」「战斗评价」",
          {"任务", "战斗回看", "战斗评价"} <= set(ft), str(ft))
    check("battle（回看卡）不含「任务」（不混用）", "任务" not in bt, str(bt))
    check("两组按钮不完全相同", ft != bt, "%s vs %s" % (ft, bt))
    check("CARD_SELF 区分：fight_text=战斗 / battle=战斗回看",
          db.CARD_SELF.get("fight_text") == "战斗"
          and db.CARD_SELF.get("battle") == "战斗回看",
          "%s / %s" % (db.CARD_SELF.get("fight_text"),
                       db.CARD_SELF.get("battle")))


# ---------------------------------------------------------------------------
# L. 九星突破 BOSS 必须**豁免**章节闸（它按玩家境界走，不按区域走）
#
# 为什么单列一组：章节闸写在 `encounter` / `pick_encounter` 上，九星突破走的是
# `make_breakthrough_boss → monster_scaled(snap, species)`（不传 at_lv）。两条路
# 物理上分开，但「以后谁图省事给 monster_scaled 加个默认 at_lv」就会把 12 档
# 原著 BOSS 全部钳成第 1 章 lv5 —— 用户明确要求过「九星必遇原著 BOSS，别写快照玩法」。
# ---------------------------------------------------------------------------
def group_l_nine_star():
    p9 = newp(lv=9)
    p9["exp"] = cfg.exp_need(9)
    check("lv9 满经验 = 九星突破点", core.is_nine_star_breakthrough(p9),
          str(p9["lv"]))

    boss = core.make_breakthrough_boss(p9)
    check("九星 BOSS 豁免章节闸：lv 跟玩家 lv9，不被钳到 ch1 基准 lv5",
          int(boss["lv"]) == 9,
          "boss.lv=%s（ch1 基准 lv%s）" % (boss["lv"], cfg.chapter_base_lv(1)))
    p9_tail = newp(lv=9)
    p9_tail["story_chapter"] = 13
    check("九星 BOSS 与章节字段完全解耦：story_chapter=13 也不改 BOSS 等级",
          int(core.make_breakthrough_boss(p9_tail)["lv"]) == 9,
          str(core.make_breakthrough_boss(p9_tail)["lv"]))
    check("九星 BOSS 仍是玩家档强度（血量高于同 at_lv 的普通怪）",
          int(boss["hp"]) > int(core.monster_scaled(p9, spec("嗜血鼠"), at_lv=9)["hp"]),
          "%d vs %d" % (boss["hp"],
                        core.monster_scaled(p9, spec("嗜血鼠"), at_lv=9)["hp"]))
    check("九星 BOSS 带原著角色包（不是通用心魔）",
          bool((boss.get("character") or {}).get("intro"))
          and bool((boss.get("character") or {}).get("skill")),
          str((boss.get("character") or {}).get("name")))


async def main():
    tmp = tempfile.mkdtemp(prefix="doupo_chgate_store_")
    store = Store(os.path.join(tmp, "doupo.json"))
    await store.load()

    group_a_data()
    group_b_migrate()
    group_c_pool()
    group_d_hardlock()
    group_e_lv_for()
    group_f_scale()
    group_g_budget()
    group_h_encounter()
    group_i_submit()
    await group_j_text(store)
    group_k_buttons()
    group_l_nine_star()

    ok = sum(1 for _, passed, _ in RES if passed)
    for name, passed, detail in RES:
        print(("PASS  " if passed else "FAIL  ") + name
              + ("" if passed else "  -> " + detail[:180]))
    print("=" * 70)
    print("GATE_TOTAL=%d PASSED=%d FAILED=%d" %
          (len(RES), ok, len(RES) - ok))
    return 0 if ok == len(RES) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
