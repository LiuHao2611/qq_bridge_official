# -*- coding: utf-8 -*-
"""带纹丹服用口径验收（2026-11-25 用户「我要服用有丹纹的丹药，应该怎么输入」）。

## 为什么需要这一层

背包**显示名**刻意剥掉了纹数后缀（用户「丹药名称不要变」，纹数交给右上角角标），
所以玩家看到的那格就叫「聚气散」。而带纹丹的真名是「聚气散·一纹/·二纹/·三纹」
（旧存档还有「·丹纹」）。玩家照卡面输入必然只吃到**不带纹**那枚，
或者打错分隔符直接被判「无此丹药」。

本文件守：
  ① 只有带纹丹时，`服丹 聚气散` 要自动吃到带纹那枚（并说明）；
  ② 纯丹与带纹丹都在时，`服丹 聚气散` 吃**纯丹**（稀有带纹丹留着）；
  ③ `·二纹 / 二纹 / 二纹在前 / 用点用空格用括号` 全部认；
  ④ 指定了纹数就**不许**换成别的纹（三纹很稀有，误吃就是坑）；
  ⑤ 旧档后缀 `·丹纹` 仍可服用。

运行：python src/plugins/doupo/test_use_danwen.py
"""
from __future__ import annotations

import asyncio
import os
import sys
import types

PLUGINS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO_ROOT = os.path.dirname(os.path.dirname(PLUGINS_DIR))
for _p in (REPO_ROOT, PLUGINS_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def _stub_nonebot():
    nb = types.ModuleType("nonebot")

    def on_regex(*a, **k):
        m = types.SimpleNamespace()
        m.handle = lambda *a, **k: (lambda f: f)
        m.finish = lambda *a, **k: None
        return m

    nb.on_regex = on_regex
    nb.get_driver = lambda: types.SimpleNamespace(on_startup=lambda f: f)
    sys.modules["nonebot"] = nb
    for name in ("nonebot.adapters", "nonebot.adapters.onebot",
                 "nonebot.adapters.onebot.v11"):
        sys.modules[name] = types.ModuleType(name)
    v11 = sys.modules["nonebot.adapters.onebot.v11"]
    v11.MessageEvent = object
    v11.MessageSegment = object
    v11.Message = object


_stub_nonebot()

from doupo import commands, core, config as cfg  # noqa: E402

_PASSED = 0
_FAILED = 0


def check(name, cond, detail=""):
    global _PASSED, _FAILED
    if cond:
        _PASSED += 1
        print("PASS  %s" % name)
    else:
        _FAILED += 1
        print("FAIL  %s  ->  %s" % (name, detail))


class MemStore:
    """真存档替身：只做内存读写，丹药限次/锁定给空实现。"""

    def __init__(self, p):
        self.p = p

    async def get_player(self, qq):
        return self.p

    async def put_player(self, p):
        self.p = p

    async def pill_used_counts(self, qq, name):
        return 0

    async def mark_pill_used(self, qq, name, n):
        return None

    async def item_locks(self, qq):
        return set()

    async def set_item_lock(self, qq, name, on):
        return True


#: 挑一门「气血类」且注册了带纹版的丹当样本（回灵丹/回春散谁在就用谁）
BASE = next(
    (n for n, r in cfg.RECIPE_BY_NAME.items()
     if r.get("effect") == "hp" and not cfg.is_danwen_name(n)
     and cfg.danwen_name(n, 1) in cfg.RECIPE_BY_NAME),
    "",
)


def _fresh(items: dict):
    """造一个「气血见底」的档，背包**清空**后只放 items 里指定的东西。

    ⚠️ `create_player` 会发新手物资（回灵丹×4、炎星石×2）。不清空的话
    「只有带纹丹」这种前提根本不存在，用例会静默测成「纯丹优先」——首版就踩了。
    """
    p = core.create_player("danwen-use", "试丹", "g1")
    core.bag(p).clear()
    p["hp"] = 1
    p["mp"] = 0
    for k, v in items.items():
        core.add_item(p, k, v)
    return p


def test_sample_pill_exists():
    check("存在气血类带纹丹样本", bool(BASE), "RECIPES 里没找到 hp 类丹")
    if BASE:
        check("样本注册了三档带纹版",
              all(cfg.danwen_name(BASE, i) in cfg.RECIPE_BY_NAME for i in (1, 2, 3)), BASE)


def test_only_danwen_falls_back_to_it():
    """只有带纹丹时：打纯名也要吃到它，而不是回「你没有」。"""
    if not BASE:
        return
    two = cfg.danwen_name(BASE, 2)
    p = _fresh({two: 1})
    out = asyncio.run(commands.cmd_use(MemStore(p), "x", BASE))
    check("只有带纹丹→自动服用", "服下" in out, out[:120])
    check("只有带纹丹→文案点名二纹", "二纹" in out, out[:120])
    check("只有带纹丹→确实扣掉", core.count_item(p, two) == 0, str(core.bag(p)))


def test_plain_preferred_over_danwen():
    """纯丹与带纹丹都在：先吃纯丹，稀有带纹丹留着。"""
    if not BASE:
        return
    two = cfg.danwen_name(BASE, 2)
    p = _fresh({BASE: 1, two: 1})
    out = asyncio.run(commands.cmd_use(MemStore(p), "x", BASE))
    check("纯丹优先→服用成功", "服下" in out, out[:120])
    check("纯丹优先→纯丹被扣", core.count_item(p, BASE) == 0, str(core.bag(p)))
    check("纯丹优先→带纹丹还在", core.count_item(p, two) == 1, str(core.bag(p)))
    check("纯丹优先→没有「带纹丹直接服下」备注", "带纹丹直接服下" not in out, out[:120])


def test_input_variants_all_resolve():
    """`·二纹 / 二纹 / 二纹在前 / 点 / 空格 / 括号` 都要认。"""
    if not BASE:
        return
    two = cfg.danwen_name(BASE, 2)
    variants = [
        cfg.danwen_name(BASE, 2),
        BASE + "二纹",
        "二纹" + BASE,
        BASE + " 二纹",
        BASE + ".二纹",
        BASE + "．二纹",
        BASE + "（二纹）",
        BASE + "·2纹",
        BASE + "-二纹",
    ]
    for v in variants:
        p = _fresh({two: 1})
        got, _auto = commands.resolve_owned_item(p, v)
        check("写法 %r 解析到二纹" % v, got == two, "got=%r" % (got,))
        out = asyncio.run(commands.cmd_use(MemStore(p), "x", v))
        check("写法 %r 能服下" % v, "服下" in out and "二纹" in out, out[:100])


def test_explicit_tier_never_substitutes():
    """指定三纹但包里没有 → 不许拿一纹顶替（会白吃错丹）。"""
    if not BASE:
        return
    one = cfg.danwen_name(BASE, 1)
    p = _fresh({one: 1})
    got, _auto = commands.resolve_owned_item(p, cfg.danwen_name(BASE, 3))
    check("缺指定纹→不返回替代品", got == "", "got=%r" % (got,))
    out = asyncio.run(commands.cmd_use(MemStore(p), "x", cfg.danwen_name(BASE, 3)))
    check("缺指定纹→回「你没有」", "没有" in out, out[:120])
    check("缺指定纹→一纹丹没被吃掉", core.count_item(p, one) == 1, str(core.bag(p)))


def test_legacy_suffix_still_works():
    """2026-11-24 首版无档位后缀「·丹纹」的存量存档必须还能吃。"""
    if not BASE:
        return
    legacy = BASE + cfg.DANWEN_LEGACY
    p = _fresh({legacy: 1})
    out = asyncio.run(commands.cmd_use(MemStore(p), "x", BASE + " 丹纹"))
    check("旧后缀→自动服用成功", "服下" in out, out[:120])
    check("旧后缀→确实扣掉", core.count_item(p, legacy) == 0, str(core.bag(p)))


def test_skip_effect_pills_stay_not_eatable():
    """焚血散是升技材料：带纹版既没注册，也不许被「服丹」当普通丹吃掉。"""
    # 焚血散不在带纹注册表里（升技材料），所以它的「一纹」版只能靠迁移/脏数据出现；
    # 这里直接摆一枚新前缀名，验证服丹既不许吃、也不会把它当成普通丹。
    p = _fresh({"一纹焚血散": 1})
    out = asyncio.run(commands.cmd_use(MemStore(p), "x", "一纹焚血散"))
    check("焚血散带纹版不能被口服掉", core.count_item(p, "一纹焚血散") == 1,
          str(core.bag(p)))
    check("焚血散给出材料去向", "升级" in out or "不是口服" in out, out[:120])


def test_usage_mentions_danwen_syntax():
    """空参时的用法必须写明带纹丹怎么打（否则玩家只能猜）。"""
    p = _fresh({})
    out = asyncio.run(commands.cmd_use(MemStore(p), "x", ""))
    check("用法提到带纹丹写法", "二纹" in out and "服丹" in out, out[:160])


def test_legacy_suffix_is_tier_one_only():
    """🔴 2026-11-25 线上实测抓到的坑：旧后缀「·丹纹」只等价**一纹**。

    包里只有「聚气散·丹纹」时，玩家要「二纹」不能被顶替成这枚一纹丹
    （等于骗玩家吃掉另一枚丹）；要「一纹」则应当能吃到它。
    """
    if not BASE:
        return
    legacy = BASE + cfg.DANWEN_LEGACY
    p = _fresh({legacy: 1})
    got, _a = commands.resolve_owned_item(p, cfg.danwen_name(BASE, 2))
    check("只有·丹纹时，要二纹不得顶替", got == "", "got=%r" % (got,))
    got2, _a2 = commands.resolve_owned_item(p, cfg.danwen_name(BASE, 1))
    check("只有·丹纹时，要一纹能吃到", got2 == legacy, "got=%r" % (got2,))
    got3, _a3 = commands.resolve_owned_item(p, BASE + "二纹")
    check("只有·丹纹时，裸写二纹也不得顶替", got3 == "", "got=%r" % (got3,))


def test_craft_never_rolls_danwen_for_skip_effects():
    """🔴 2026-11-25 线上实测抓到的死物品洞：焚血散也能掷出带纹版。

    焚血散是升技材料（``effect="skill"``），注册表按 ``DANWEN_SKIP_EFFECTS``
    不给它建带纹版；但旧版 ``core.craft`` / ``craft_batch`` **不看这道闸**，
    掷中就 ``add_item("一纹焚血散")`` ⇒ 一枚既不能服、又不能锁、卖不掉的死物。
    """
    check("闸：焚血散不许出丹纹", not cfg.danwen_allowed({"effect": "skill"}))
    check("闸：普通丹药许出丹纹", cfg.danwen_allowed({"effect": "hp"}))
    # 结构门禁：两条出货路径都必须先过闸（漏一条就够再放一枚死物品出去）
    import pathlib
    src = pathlib.Path(__file__).with_name("core.py").read_text(encoding="utf-8")
    rolled = src.count("_t := dw.roll(")
    gated = src.count("cfg.danwen_allowed(rec) and (_t := dw.roll(")
    check("两条出货路径都过了闸", rolled == 2 and gated == rolled,
          "roll=%d gated=%d" % (rolled, gated))

    # 行为门禁：真炼一炉焚血散（必成功 rng），背包里不许出现带纹名
    p = core.create_player("danwen-craft", "试炼", "g1")
    core.bag(p).clear()
    p["alch"] = 8
    p["flames"] = {"紫火": 1}          # 空火禁炼药（2026-11-24 规则）
    for _ in range(30):
        core.add_item(p, "赤焰果", 3)
        core.add_item(p, "冰晶石", 2)
        p["last_craft"] = -10 ** 9          # 绕开 60s 开炉冷却（一炉一炉地炼）
        core.craft(p, "焚血散", rng=lambda: 0.0, now_ts=0)
    bad = [n for n in core.bag(p) if n.startswith(cfg.DANWEN_TIERS)]
    check("炼焚血散绝不产带纹版", not bad, str(bad))
    check("焚血散本身照常入包", core.count_item(p, "焚血散") > 0, str(core.bag(p)))


def test_orphan_danwen_migrates_to_base():
    """旧存档里的孤儿带纹版（基础丹根本没带纹注册）→ 退回基础丹，不搬死名。"""
    check("迁移：焚血散·一纹 退回焚血散",
          cfg.migrate_danwen_name("焚血散·一纹") == "焚血散",
          cfg.migrate_danwen_name("焚血散·一纹"))
    check("迁移：正常带纹丹照常改前缀",
          cfg.migrate_danwen_name("破厄丹·丹纹") == "一纹破厄丹",
          cfg.migrate_danwen_name("破厄丹·丹纹"))
    p = _fresh({"焚血散·一纹": 2, "焚血散": 1})
    core.migrate_danwen_bag(p)
    check("迁移后并回基础丹数量", core.count_item(p, "焚血散") == 3, str(core.bag(p)))
    check("迁移后不留死名", "焚血散·一纹" not in core.bag(p), str(core.bag(p)))


def test_cmd_use_wired_to_resolver():
    """结构门禁：`cmd_use` 必须真的走 resolve_owned_item（否则一切回退到旧全字匹配）。"""
    import pathlib
    src = pathlib.Path(__file__).with_name("commands.py").read_text(encoding="utf-8")
    check("cmd_use 已接入 resolve_owned_item",
          "owned, auto_danwen = resolve_owned_item(p, name)" in src)
    check("cmd_lock 也接了归一（锁定同一个坑）",
          "owned, _auto = resolve_owned_item(p, name)" in src)


def test_naming_prefix_migration_and_no_collision():
    """2026-11-25 用户「改成一纹破厄丹这种，几纹直接写在名称前面」。

    守三件事：① 生成名是**档位在前**；② 不能撞上原著丹名（三纹/二纹青灵丹）；
    ③ 旧后缀存档要能一次性改名并合并同名。
    """
    check("命名：破厄丹一纹 → 一纹破厄丹",
          cfg.danwen_name("破厄丹", 1) == "一纹破厄丹", cfg.danwen_name("破厄丹", 1))
    check("命名：三纹在前", cfg.danwen_name("破厄丹", 3).startswith("三纹"),
          cfg.danwen_name("破厄丹", 3))
    # 青灵丹的三纹版不能叫「三纹青灵丹」（那是另一枚原著丹）
    got = cfg.danwen_name("青灵丹", 3)
    check("命名：青灵丹三纹不与原著撞车", got != "三纹青灵丹", got)
    check("原著三纹青灵丹仍是普通丹（不能被注册成带纹丹）",
          not cfg.is_danwen_name("三纹青灵丹") and "三纹青灵丹" in cfg.RECIPE_NAMES)
    # 基名本身以纹数开头 → 加隔点
    check("命名：三纹青灵丹的一纹版加隔点",
          cfg.danwen_name("三纹青灵丹", 1) == "一纹·三纹青灵丹",
          cfg.danwen_name("三纹青灵丹", 1))

    p = _fresh({"破厄丹·丹纹": 2, "一纹破厄丹": 1})
    moved = core.migrate_danwen_bag(p)
    check("迁移：旧后缀改名为前缀",
          moved == [("破厄丹·丹纹", "一纹破厄丹")], str(moved))
    check("迁移：同名合并数量", core.count_item(p, "一纹破厄丹") == 3, str(core.bag(p)))
    check("迁移：幂等（第二次无改动）", core.migrate_danwen_bag(p) == [], str(core.bag(p)))


def _run():
    fns = [(k, v) for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    for name, fn in fns:
        fn()
    print("=" * 70)
    print("USEDANWEN_TOTAL=%d PASSED=%d FAILED=%d"
          % (_PASSED + _FAILED, _PASSED, _FAILED))
    return 0 if _FAILED == 0 else 1


if __name__ == "__main__":
    sys.exit(_run())
