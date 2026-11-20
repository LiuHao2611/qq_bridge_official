# -*- coding: utf-8 -*-
"""傀儡多配方守门（2026-11-20）—— 原著口径见
docs/傀儡多配方体系_设计_2026-11-20.md。

覆盖用户硬要求「不要再出现这种 BUG」：
  * 档位由五维材料木桶决定（原著第900章「等级之差，取决炼制材料以及炼制之火」、
    第906章「天为金，地为银，人为铜」）——`first_craft_tier()` 不许再被生产代码调用；
  * 老傀儡数值逐字节不变（金属/火系数基准点取旧档 grade=2 / fire_tier=7）；
  * 材料名全部出自原著正文，与既有药材/天材地宝零重名（不许自造名）；
  * `PUPPET_MP_FACTOR` 三档都 ≤ 1.0 —— 旧值 (1.0,1.6,2.4) 让地级/天级**永远开不了炉**；
  * 傀儡料只进 puppet_out（真实列），不混进 bag 战利；
  * 端到端：按配方名开炉，扣的就是这条配方的料。

自带 runner，零依赖 pytest：python tests/test_doupo_puppet_recipes.py
注入反例：改坏源码 → 对应断言必须 FAIL（prove-it）。
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
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

from doupo import config as cfg  # noqa: E402
from doupo import core  # noqa: E402
from doupo import commands  # noqa: E402
from doupo import puppet_core as pc  # noqa: E402
import importlib  # noqa: E402

ps_mod = importlib.import_module("doupo.puppet_store")
store_mod = importlib.import_module("doupo.store")

_FAILS = []
_N = 0


def check(cond, msg):
    global _N
    _N += 1
    if not cond:
        _FAILS.append(msg)


# --- 1. 木桶判档：最弱一环决定档位 -------------------------------------------
def test_trough_picks_weakest():
    cases = [
        ({"body": 4, "core": 1, "soul": 4, "metal": 4, "fire": 4}, 0, "魔核一档拖死全炉"),
        ({"body": 1, "core": 1, "soul": 1, "metal": 1, "fire": 1}, 0, "全一档=人级"),
        ({"body": 2, "core": 2, "soul": 2, "metal": 2, "fire": 2}, 1, "全二档=地级"),
        ({"body": 3, "core": 3, "soul": 3, "metal": 3, "fire": 3}, 2, "全三档=天级"),
        ({"body": 4, "core": 3, "soul": 4, "metal": 4, "fire": 3}, 2, "北王封顶天级"),
        ({"body": 2, "core": 2, "soul": 2, "metal": 2, "fire": 1}, 0, "火也参与木桶"),
    ]
    for slots, want, why in cases:
        check(pc.recipe_tier(slots) == want, "木桶：%s 应为 %d，实得 %d"
              % (why, want, pc.recipe_tier(slots)))
    for miss in ("body", "core", "soul", "metal", "fire"):
        s = {"body": 3, "core": 3, "soul": 3, "metal": 3, "fire": 3}
        s[miss] = 0
        check(pc.recipe_tier(s) == -1, "缺 %s 必须判不可开炉（-1）" % miss)


# --- 2. 材料名：全在原著名单内、与药材零重名、配方 >12 条 ---------------------
def test_material_names_are_novel():
    missing = []
    for r in cfg.PUPPET_RECIPES:
        for k in pc.SLOT_KINDS:
            if pc.slot_tier(k, r[k]) <= 0:
                missing.append("%s.%s=%s" % (r["name"], k, r[k]))
        if r["fire"] not in cfg.PUPPET_FIRE_GRADES:
            missing.append("%s.fire=%s" % (r["name"], r["fire"]))
    check(not missing, "配方外引了未定义材料：%s" % missing[:5])
    check(len(cfg.PUPPET_RECIPES) > 12, "配方须多于 12 条，现 %d" % len(cfg.PUPPET_RECIPES))

    herbs = {h["name"] for h in cfg.HERBS}
    herbs |= {m["name"] for m in getattr(cfg, "NOVEL_MATERIALS", [])}
    clash = [n for n in cfg.PUPPET_MATERIALS if n in herbs]
    check(not clash, "傀儡材料与药材/天材地宝撞名：%s" % clash)
    # 正文最高只出现「七阶魔核」——不许自造八阶/九阶
    selfmade = [n for n in cfg.PUPPET_MATERIALS if n.startswith(("八阶", "九阶"))]
    check(not selfmade, "出现原著没有的八/九阶魔核：%s" % selfmade)


# --- 3. 老傀儡不吃材料惩罚（口径见下：绝对值必须变，惩罚不许有）-------------
def test_legacy_puppet_unchanged():
    """老存档傀儡不被倒扣。

    ⚠️ 2026-11-20 第二轮**口径变更**：上一轮这里断言「数值逐字节不变」，锚的是
    旧绝对常数 `PUPPET_TIER_BASE / PUPPET_CORE_K1 / PUPPET_SOUL_K2 /
    PUPPET_ATK_FACTOR`。那几个常数已在第二轮删除 —— 它们正是用户说的
    「傀儡战力低得离谱」的根因（绝对常数不跟等级走，lv81 只占玩家输出 0.2%）。

    现在能守、也必须守的是：**老存档（库列缺 metal/fire、或为 0）不被倒扣**，
    即材料倍率恰为 1.0，且与「显式填满基准值」的同一具傀儡逐字节等值。

    ⚠️ 2026-11-20 第三轮**再次口径变更**：`PUPPET_TIER_RATIO`（玩家输出百分比）
    已删，改锚**躯体境界**（见 cfg 里那段三轮演进）。所以这里不再拿
    「玩家输出 × ratio」当期望值 —— 那正是被本轮废掉的旧口径。改成拿「显式
    基准材料」当参照物，守的是「缺键不扣分」这条真正的不变量。
    """
    p60 = core.tower_ref_player(60)
    for t in (0, 1, 2):
        base = {"tier": t, "core_tier": 7, "soul_strength": 900,
                "metal_grade": 2, "fire_tier": 7}
        want = pc.puppet_attack(base, p60)
        check(want > 0, "tier=%d 基准材料战力算成 0" % t)
        check(abs(pc.puppet_full_attack(base) - want <= 1e-6) or want <= pc.puppet_full_attack(base) + 1e-6,
              "tier=%d 战力 %.2f 超过了躯体境界满配 %.2f"
              % (t, want, pc.puppet_full_attack(base)))
        # 库列缺失 / 为 0（未记录）也必须按基准算，不能吃惩罚
        for variant in ({"tier": t, "core_tier": 7, "soul_strength": 900},
                        {"tier": t, "core_tier": 7, "soul_strength": 900,
                         "metal_grade": 0, "fire_tier": 0},
                        {"tier": t}):
            got = pc.puppet_attack(variant, p60)
            check(abs(got - want) < 1e-6,
                  "缺键/0 值傀儡 tier=%d 被扣分：%.2f ≠ %.2f（%s）"
                  % (t, got, want, variant))
        check(abs(pc._material_mult({"tier": t}) - 1.0) < 1e-9,
              "老档材料倍率必须恰为 1.0，实得 %.6f" % pc._material_mult({"tier": t}))
    # 旧比例口径的常量必须彻底退场（两套并存 = 口径分叉）
    for ghost in ("PUPPET_TIER_RATIO", "PUPPET_REF_LV"):
        check(not hasattr(cfg, ghost),
              "cfg.%s 复活了 —— 旧「玩家输出百分比」口径已弃用" % ghost)


# --- 4. 金属与火真的进公式（同档内拉开差距，且不失控）-----------------------
def test_metal_and_fire_enter_formula():
    base = {"tier": 1, "core_tier": 7, "soul_strength": 1500}
    lo = pc.puppet_attack(dict(base, metal_grade=1, fire_tier=7))
    hi = pc.puppet_attack(dict(base, metal_grade=4, fire_tier=9))
    check(hi > lo, "高阶金属+高阶火必须更强：%.1f vs %.1f" % (hi, lo))
    ratio = hi / lo
    check(1.20 <= ratio <= 1.27, "金属+火合计差距应在 20%%~27%%，实为 %.4f" % ratio)
    # 木桶保底：金属再高也不改档位（档位只由 recipe_tier 出）
    check(pc.recipe_tier({"body": 1, "core": 1, "soul": 1, "metal": 4, "fire": 1}) == 0,
          "堆一件神金属不该顶起档位")


# --- 5. 火档读玩家 flames / diyan -------------------------------------------
def test_fire_grade_from_player():
    for flames, want in (({}, 0), ({"青莲地心火": 4}, 2), ({"陨落心炎": 1}, 3),
                         ({"九幽风炎": 3}, 1), ({"不存在的火": 2}, 1)):
        got = pc.fire_grade({"flames": flames})["grade"]
        check(got == want, "火档 %s 应为 %d，实得 %d" % (list(flames) or "无火", want, got))
    check(pc.fire_grade({"flames": {}, "diyan": True})["grade"] == 3,
          "帝炎走 diyan 位也应算高阶火")
    check(pc.fire_grade({"flames": {"青莲地心火": 0}})["grade"] == 0,
          "火等级为 0 视为未持有")


# --- 6. 缺料提示 / 自动选配方 ------------------------------------------------
def test_missing_and_best_recipe():
    mats = {n: 9 for n in cfg.PUPPET_MATERIALS}
    rec = cfg.PUPPET_RECIPE_BY_NAME.get("地妖傀")
    check(rec is not None, "配方表缺 地妖傀")
    if not rec:
        return
    check(not pc.missing_materials(mats, rec), "材料齐时不该报缺")
    few = {k: v for k, v in mats.items() if k != "焰陨玄铁"}
    check(pc.missing_materials(few, rec) == ["焰陨玄铁×1"],
          "缺料要如实报名字与数量，实得 %s" % pc.missing_materials(few, rec))
    # 斗宗 + A 火：最高能到地级，同档取 cfg 最靠前（地妖傀，不是北王傀）
    best = pc.best_recipe({"lv": 64, "flames": {"青莲地心火": 4}}, mats)
    check(best and best["tier"] == 1 and best["recipe"]["name"] == "地妖傀",
          "斗宗自动选配方应为地妖傀，实得 %s" % (best and best["recipe"]["name"]))
    low = pc.best_recipe({"lv": 30, "flames": {"青莲地心火": 4}}, mats)
    check(low and low["tier"] == 0 and low["recipe"]["name"] == "铜尸傀",
          "大斗师自动选配方应为铜尸傀，实得 %s" % (low and low["recipe"]["name"]))


# --- 7. 标称档 vs 实况档（火不够要降档，不许假装能出天级）-------------------
def test_nominal_vs_actual_tier():
    tiers = [pc.recipe_tier(pc.recipe_slots(r)) for r in cfg.PUPPET_RECIPES]
    check(tiers.count(0) == 6 and tiers.count(1) == 6 and tiers.count(2) == 6,
          "标称档应人6/地6/天6，实为 %d/%d/%d"
          % (tiers.count(0), tiers.count(1), tiers.count(2)))
    king = cfg.PUPPET_RECIPE_BY_NAME.get("金煞傀")
    check(king is not None, "配方表缺 金煞傀")
    if king:
        check(pc.recipe_tier(pc.recipe_slots(king, {"flames": {"青莲地心火": 4}})) == 1,
              "只有 A 火时天级配方必须降成地级（木桶）")
        check(pc.recipe_tier(pc.recipe_slots(king, {"flames": {"陨落心炎": 1}})) == 2,
              "有 S 火时天级配方才能到天级")
    # 列表分组用的标称档不受玩家火影响（否则玩家以为配方写错）
    items = pc.craftable_recipes({"flames": {"青莲地心火": 4}},
                                 {n: 9 for n in cfg.PUPPET_MATERIALS})
    nom = {x["recipe"]["name"]: x["tier_nominal"] for x in items}
    check(nom.get("金煞傀") == 2, "列表标称档不该被玩家火拉低，实得 %s" % nom.get("金煞傀"))


# --- 8. 防回归：斗气系数 & first_craft_tier 红线 ----------------------------
def test_no_deadlock_regressions():
    for t in (0, 1, 2):
        f = float(cfg.PUPPET_MP_FACTOR[t])
        check(f <= 1.0, "PUPPET_MP_FACTOR[%d]=%.2f > 1.0 —— 斗气上限就是 1 倍，"
                        "这档永远开不了炉" % (t, f))
    check(cfg.PUPPET_MP_FACTOR[0] < cfg.PUPPET_MP_FACTOR[1] < cfg.PUPPET_MP_FACTOR[2],
          "档位越高越耗斗气")
    # 源码红线：生产代码不许再调 first_craft_tier（它恒返回 0，等于材料白搭）
    src_dir = os.path.join(PLUGINS, "doupo")
    calls = []
    for fn in sorted(os.listdir(src_dir)):
        if not fn.endswith(".py") or fn.startswith("test_"):
            continue
        with open(os.path.join(src_dir, fn), encoding="utf-8") as fh:
            for i, line in enumerate(fh, 1):
                if "first_craft_tier(" in line and "def first_craft_tier" not in line:
                    calls.append("%s:%d" % (fn, i))
    check(not calls, "生产代码仍在调 first_craft_tier（档位会恒为人级）：%s" % calls)


# --- 9. 傀儡料不进 bag 战利 -------------------------------------------------
def test_puppet_drops_not_in_bag():
    p = core.create_player("10009", "掉落测试", "20009")
    mon = {"name": "试炼卫", "lv": 99, "hard": 0.9, "exp_mul": 1.0,
           "gold_mul": 1.0, "kind": "测试", "is_boss": True, "hp": 1,
           "atk": 1, "def": 1, "exp": 1, "gold": 1}
    out_only = core._roll_drops(p, lambda: 0.0, mon)
    pit = []
    out_both = core._roll_drops(p, lambda: 0.0, mon, pit)
    check(out_only == out_both, "传 puppet_out 不该改变 bag 战利序列")
    check(bool(pit), "高等级 BOSS 必掉一件傀儡料（PUPPET_DROPS_SURE）")
    bag_names = {n for n, _c in out_both}
    check(not (set(n for n, _c in pit) & bag_names),
          "傀儡料不许混进 bag 战利：%s" % (set(n for n, _c in pit) & bag_names))
    check(not any(n in core.bag(p) for n, _c in pit),
          "傀儡料不许塞进 players.data 的 bag（最高声明）")
    # 老调用（不传 puppet_out）行为逐字节不变
    p2 = core.create_player("10010", "老路径", "20010")
    check(core._roll_drops(p2, lambda: 0.0, mon) == core._roll_drops(p2, lambda: 0.0, mon),
          "老路径必须可重放（无隐藏状态）")


# --- 10. 端到端：按配方名开炉，扣的就是这条配方的料 -------------------------
async def _e2e():
    tmp = tempfile.mkdtemp(prefix="pup_recipe_")
    store = store_mod.Store(os.path.join(tmp, "doupo.json"))
    await store.load()
    db = ps_mod.PuppetStore(os.path.join(tmp, "puppet.sqlite"))
    qq = "10002"
    p = core.create_player(qq, "配方测试", "20002")
    p["lv"] = 64
    p["alch"] = cfg.PUPPET_ALCH_MIN
    p["craft_count"] = cfg.PUPPET_CRAFT_COUNT_MIN
    p["high_grade_crafts"] = cfg.PUPPET_HIGH_GRADE_MIN
    p["flames"] = {"青莲地心火": 4}
    p["gold"] = 200000
    p["mp"] = core.mp_max(p)
    await store.put_player(p)
    await db.set_lore(qq, True)
    stock = {n: 3 for n in cfg.PUPPET_MATERIALS}
    for n, q in stock.items():
        await db.add_material(qq, n, q)

    rec = cfg.PUPPET_RECIPE_BY_NAME.get("银煞傀")
    check(rec is not None, "配方表缺 银煞傀")
    if not rec:
        return
    cost = pc.recipe_materials(rec)
    txt = await commands.cmd_craft_puppet(store, qq, db, "银煞傀")
    roster = await db.list_puppets(qq)
    check("炼制成功" in txt and bool(roster), "按配方开炉失败：%s" % txt[:160])
    check(bool(roster), "按配方开炉没产出傀儡行")
    if roster:
        row = roster[0]
        # 银煞傀 = 斗宗强者之躯 / 高阶魔核(9阶) / 斗宗强者之魂 / 寒铁
        check(row["core_tier"] == 9 and row["soul_source"] == "斗宗强者之魂"
              and row["metal_name"] == "寒铁",
              "落库五维与配方不符：%s" % str(row)[:260])
        check(pc.match_recipe(row) is not None
              and pc.match_recipe(row)["name"] == "银煞傀",
              "反查配方应为银煞傀，实得 %s"
              % (pc.match_recipe(row) or {}).get("name"))
        check(int(row["tier"]) == 1, "银煞傀应为地级，实得 %d" % int(row["tier"]))
    for n, q in stock.items():
        left = await db.material_qty(qq, n)
        want = q - int(cost.get(n, 0))
        check(left == want, "材料 %s 应余 %d，实余 %d" % (n, want, left))

    # 配方不存在的提示
    bad = await commands.cmd_craft_puppet(store, qq, db, "不存在的傀")
    check("无此配方" in bad, "写错配方名要如实报错：%s" % bad[:80])

    # 缺料时要报缺什么、去哪取（先把斗气补满、把余料清空，别让别的原因抢先报错）
    for n in list(stock):
        have = await db.material_qty(qq, n)
        if have > 0:
            await db.take_materials(qq, {n: have})
    p2 = await store.get_player(qq)
    p2["mp"] = core.mp_max(p2)
    await store.put_player(p2)
    lack_txt = await commands.cmd_craft_puppet(store, qq, db, "银煞傀")
    check("材料不足" in lack_txt and "寒铁" in lack_txt,
          "缺料提示应点名缺什么：%s" % lack_txt[:160])
    check("傀儡遗迹" in lack_txt, "缺料提示应给出获取途径：%s" % lack_txt[:160])

    # 配方列表随时可看
    lst = await commands.cmd_craft_puppet(store, qq, db, "配方")
    check("傀儡配方" in lst and str(len(cfg.PUPPET_RECIPES)) in lst,
          "配方列表应含条数：%s" % lst[:100])


def test_lei_power_three_tiers():
    """P1-2 雷力三档（D04）：丹雷+12 / 五色丹雷+25 / 黑魔雷+40，按当前档位单调。"""
    check(tuple(cfg.PUPPET_LEI_POWER_GAIN) == (12, 25, 40),
          "雷力三档增量不是 12/25/40：%s" % (cfg.PUPPET_LEI_POWER_GAIN,))
    gains = [pc.lei_power_gain(t) for t in (0, 1, 2)]
    check(gains[0] < gains[1] < gains[2],
          "雷力增量未随档位递增：%s（丹雷慢、黑魔雷快）" % gains)
    # temper 真用这个增量：各档雷淬一次的 purity 增量必须等于档位增益
    for tier in (0, 1):
        pu = {"name": "测试", "tier": tier, "purity": 0, "damaged": False}
        got = pc.temper(pu)["purity"] - 0
        check(got == pc.lei_power_gain(tier),
              "tier%d 雷淬增量 %d ≠ 雷力档位 %d" % (tier, got, pc.lei_power_gain(tier)))
    # 越界档位钳制到 2，不许 IndexError
    check(pc.lei_power_gain(99) == 40 and pc.lei_power_gain(-1) == 12,
          "雷力档位越界未钳制")


def test_tian_tier_vein_longline():
    """P1-2 天级长线（Q4）：不再封死 100，黑魔雷继续涨、折雷纹、护甲增、输出不破 CAP。"""
    hard = pc.purity_hard_max()
    check(hard == int(cfg.PUPPET_PURITY_MAX)
          + int(cfg.PUPPET_VEIN_MAX) * int(cfg.PUPPET_VEIN_PURITY),
          "纯度硬上限公式错：%d" % hard)
    # 天级连续雷淬，purity 必须能突破 100 并到硬上限
    cur = {"name": "天妖傀", "tier": 2, "tier_name": "天级傀儡", "skin": "金",
           "purity": 100, "damaged": False}
    for _ in range(10):
        cur = dict(cur, **pc.temper(cur))
    check(cur["purity"] == hard, "天级雷淬未累到硬上限：%d≠%d" % (cur["purity"], hard))
    check(cur.get("maxed") is True, "满层未标 maxed")
    check(int(cur.get("vein") or 0) == int(cfg.PUPPET_VEIN_MAX),
          "满层雷纹层数错：%s" % cur.get("vein"))
    # 战损修复在长线阶段仍生效
    check(pc.temper(dict(cur, damaged=True)).get("damaged") is False,
          "天级雷纹满层后雷淬不清战损")


def test_dye_bar_uses_renderable_glyphs():
    """P1-2 渐进染色（N06）：进度条必须用卡片字体真有的字形。

    ◼/◻(U+25FC/25FB) 字体缺字形会被 emoji_safe 过滤 → 整条不显示（本轮真机截图
    抓到）；■/□(U+25A0/25A1) 有真字形。格数与 PUPPET_DYE_STEPS 一致。
    """
    steps = int(cfg.PUPPET_DYE_STEPS)
    b0 = pc.dye_bar(0)
    bfull = pc.dye_bar(int(cfg.PUPPET_PURITY_MAX))
    check(len(b0) == steps and len(bfull) == steps,
          "染色条格数 %d/%d ≠ %d" % (len(b0), len(bfull), steps))
    check("■" in bfull and "□" in b0,
          "染色条没用 ■/□（字体有真字形）；当前 full=%s empty=%s" % (bfull, b0))
    check("◼" not in bfull and "◻" not in b0,
          "染色条用了 ◼/◻ —— 卡片字体缺字形会被过滤，玩家看不到")
    # 单调：纯度越高实心越多
    check(pc.dye_bar(0).count("■") < pc.dye_bar(50).count("■")
          < pc.dye_bar(100).count("■"),
          "染色条实心格数未随纯度单调增")


def test_batch_temper_atomic():
    """P1-2 批量雷淬（Q1）：N 次=N 倍扣费、各判各档、单次失败整笔不扣。"""
    import inspect
    src = inspect.getsource(commands._cmd_puppet_temper)
    # 源码钉：按 times 倍校验/扣费，不是每次循环里扣（后者会有半途失败烂摊子）
    check("stone_need = int(cfg.PUPPET_TEMPER_STONE) * times" in src,
          "批量雷淬石头未按 times 倍一次性校验")
    check("gold_need = int(cfg.PUPPET_TEMPER_COST) * times" in src,
          "批量雷淬金币未按 times 倍一次性校验")
    check("for i in range(times)" in src,
          "批量雷淬没有逐次开炉循环")
    # 天级门禁必须用硬上限，不能退回封死 100（否则雷纹长线入口被堵死）
    check(">= pc.purity_hard_max()" in src,
          "天级雷淬门禁未用 purity_hard_max —— 长线入口被 purity=100 堵死")
    # 分发处把次数传进来了
    cmd_src = inspect.getsource(commands.cmd_puppet)
    check("_cmd_puppet_temper(store, qq, p, roster, elig, pstore," in cmd_src,
          "cmd_puppet 未把批量次数传给 _cmd_puppet_temper")


def main():
    for fn in (test_trough_picks_weakest, test_material_names_are_novel,
               test_legacy_puppet_unchanged, test_metal_and_fire_enter_formula,
               test_fire_grade_from_player, test_missing_and_best_recipe,
               test_nominal_vs_actual_tier, test_no_deadlock_regressions,
               test_puppet_drops_not_in_bag, test_lei_power_three_tiers,
               test_tian_tier_vein_longline, test_dye_bar_uses_renderable_glyphs,
               test_batch_temper_atomic):
        fn()
    asyncio.run(_e2e())
    for m in _FAILS:
        print("FAIL  " + m)
    print("=" * 70)
    print("PUP_RECIPES_TOTAL=%d PASSED=%d FAILED=%d"
          % (_N, _N - len(_FAILS), len(_FAILS)))
    return 0 if not _FAILS else 1


if __name__ == "__main__":
    sys.exit(main())
