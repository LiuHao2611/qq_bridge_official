# -*- coding: utf-8 -*-
"""丹药「承诺 ↔ 实现」一致性守门（2026-11-18 假药普查）。

事故起点：三纹青灵丹 desc 写「助斗师突破大斗师、大斗师服下升一星」，代码却是
``effect="exp" value=1000``——玩家吃八枚只涨修为。普查发现这不是孤例：全表 75 枚
里有一批丹药在承诺本作根本没有的系统（封印 / 寿命 / 假死 / 魔兽化形 / 独立灵魂值 /
升级失败率 / 吞火护体…）。

因此确立契约：
  * ``desc`` = **原著功效**（考据用，不是承诺）；
  * ``impl`` = **本作真会发生什么**（承诺），卡面 / 包裹 / 「炼药」文本只印它；
  * desc 里出现承诺词（升星 / 突破 / 成功率 / 寿命 / 化形…）而 effect 不是
    ``breakthrough`` 时，**必须**在配方里手写 ``impl``——等于强制有人逐条审过、
    并把落差如实写出来。

守门：
  1. 每条配方都有非空 impl；
  2. impl 与 effect 量纲一致（增修为的不能说气血）；
  3. 承诺词必须被 breakthrough 或手写 impl 覆盖；
  4. 每个 effect 都有展示标签；breakthrough 参数合法；
  5. 卡面与「炼药」文本都走 cfg.recipe_impl，不直接用 desc。

注入验证（错误实现塞回去必须红）：
  * 三纹青灵丹 effect 改回 "exp" 且删 impl → 第 3 条红；
  * recipe_impl 返回 "" → 第 1 条红；
  * card 里改回 r["desc"] → 第 5 条红。

运行：python tests/test_doupo_recipe_honesty.py
"""
from __future__ import annotations

import ast
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


def _all():
    return list(cfg.RECIPES)


# ---------------------------------------------------------------------------
# 1. 每条都有 impl
# ---------------------------------------------------------------------------
def test_every_recipe_has_impl():
    bad = [r["name"] for r in _all() if not str(cfg.recipe_impl(r)).strip()]
    assert not bad, "这些丹药没有「本作效果」文案（卡面会空）: %s" % bad


def test_impl_has_no_markdown():
    """群聊发不出 markdown，impl 会进卡片与纯文本，必须禁 # ** | 与反引号。"""
    bad = []
    for r in _all():
        s = cfg.recipe_impl(r)
        for ch in ("#", "**", "|", "`"):
            if ch in s:
                bad.append("%s: %r" % (r["name"], s))
    assert not bad, bad


# ---------------------------------------------------------------------------
# 2. impl 与 effect 量纲一致
# ---------------------------------------------------------------------------
DIM = {
    "hp": ("气血",), "mp": ("斗气",), "exp": ("修为",), "luck": ("机缘",),
    "dodge": ("闪避",), "crit": ("暴击",), "hit": ("命中",),
    "stat": ("全属性",), "skill": ("斗技",),
    # 2026-11-21 P3 养魂：养魂涎有独立的灵魂量纲（傀儡 soul_fed）。
    # 加进来不是「放过它」，而是让这条门禁也能抓「写着养魂、实际加机缘」的复发。
    "puppet_soul": ("傀儡灵魂",),
    # 2026-11-23：暴击率 80% 硬顶后新增「暴伤」量纲（炎魔清玄丹·原著增幅战力）。
    # 同样不是放过它 —— 写成「气血」会立刻被本表抓住。
    "critdmg": ("暴伤", "暴击伤害"),
    # 2026-11-23：帝品雏丹 = 原著「可助人成为斗帝」，量纲是「斗帝/成帝」。
    "ascend": ("斗帝", "成帝"),
    # 2026-11-24 限时增益：原著「短时间」是承诺的一部分，impl 必须写出时长。
    # 写成「永久」「气血」都会被本表抓住 —— 不是放过它，是给它划量纲。
    "temp_buff": ("分钟", "限时"),
    # 2026-11-24 兽火单方：承诺是「收服一朵兽火」，量纲只看兽火。
    # impl 写成「加气血/涨修为」会被本表抓住。
    "beast_flame": ("兽火",),
}


def test_impl_matches_effect_dimension():
    bad = []
    for r in _all():
        eff = r.get("effect")
        impl = cfg.recipe_impl(r)
        if eff == "breakthrough":
            if not any(k in impl for k in ("破", "升一星", "升星")):
                bad.append("%s: breakthrough 但 impl=%r" % (r["name"], impl))
            continue
        need = DIM.get(str(eff))
        if need is None:
            bad.append("%s: effect=%r 没有量纲定义" % (r["name"], eff))
            continue
        if not any(k in impl for k in need):
            bad.append("%s: effect=%s 但 impl=%r" % (r["name"], eff, impl))
    assert not bad, "\n".join(bad)


def test_impl_value_matches_number():
    """impl 里出现的数字必须包含配方的 value（防止改 value 忘了改文案）。"""
    bad = []
    for r in _all():
        if r.get("effect") == "breakthrough":
            continue
        v = int(r.get("value") or 0)
        impl = cfg.recipe_impl(r)
        if v and str(v) not in impl:
            bad.append("%s: value=%d 但 impl=%r" % (r["name"], v, impl))
    assert not bad, "\n".join(bad)


# ---------------------------------------------------------------------------
# 3. 承诺词必须被兑现（breakthrough）或如实标注（手写 impl）
# ---------------------------------------------------------------------------
def test_promise_words_are_honored():
    bad = []
    for r in _all():
        if r.get("effect") == "breakthrough":
            continue
        desc = str(r.get("desc") or "")
        hit = [w for w in cfg.IMPL_PROMISE_WORDS if w in desc]
        if hit and "impl" not in r:
            bad.append("%s: desc 承诺 %s，但 effect=%r 且没手写 impl"
                       % (r["name"], hit, r.get("effect")))
    assert not bad, (
        "以下丹药在承诺本作做不到的事（要么改 breakthrough，要么在 "
        "RECIPE_IMPL_OVERRIDE 里如实写 impl）：\n" + "\n".join(bad))


def test_overridden_recipes_keep_original_desc():
    """手写 impl 的丹药必须保留原著 desc（考据不能因为改文案就丢了）。"""
    bad = [r["name"] for r in _all()
           if r.get("impl") and not str(r.get("desc") or "").strip()]
    assert not bad, "手写了 impl 却丢了原著 desc: %s" % bad


# ---------------------------------------------------------------------------
# 4. effect 标签与 breakthrough 参数
# ---------------------------------------------------------------------------
def test_every_effect_has_tag():
    bad = sorted({str(r.get("effect")) for r in _all()
                  if not cfg.effect_tag(str(r.get("effect")))})
    assert not bad, "这些 effect 没有展示标签（卡面会缺角标）: %s" % bad


def test_breakthrough_params_sane():
    bad = []
    for r in _all():
        if r.get("effect") != "breakthrough":
            continue
        to = int(r.get("to_realm") or 0)
        if not (0 <= to < len(cfg.REALM_NAMES)):
            bad.append("%s: to_realm=%r 越界" % (r["name"], r.get("to_realm")))
        ch = float(r.get("star_chance") or 0.0)
        if not (0.0 <= ch <= 1.0):
            bad.append("%s: star_chance=%r 越界" % (r["name"], ch))
        if int(r.get("max_per_realm") or 0) < 1:
            bad.append("%s: max_per_realm 必须 >=1" % r["name"])
        if r.get("break_boundary") and to == 0:
            bad.append("%s: 斗之气是起点，没有「前一境界」可破" % r["name"])
    assert not bad, "\n".join(bad)


# ---------------------------------------------------------------------------
# 5. 展示层只印 impl
# ---------------------------------------------------------------------------
def _src(rel):
    with open(os.path.join(REPO_ROOT, rel), encoding="utf-8") as fh:
        return fh.read()


def test_card_renders_impl_not_desc():
    src = _src("src/plugins/doupo/card.py")
    tree = ast.parse(src)
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "build_recipes_data")
    body = ast.get_source_segment(src, fn) or ""
    assert "cfg.recipe_impl(" in body, "炼药卡必须用 cfg.recipe_impl 生成描述"
    assert 'r.get("desc")' not in body and "r['desc']" not in body, \
        "炼药卡不能再直接印原著 desc（会承诺做不到的事）"


def test_text_recipes_renders_impl():
    src = _src("src/plugins/doupo/commands.py")
    tree = ast.parse(src)
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
              and n.name == "cmd_recipes")
    body = ast.get_source_segment(src, fn) or ""
    assert "cfg.recipe_impl(" in body, "「炼药」文本必须用 cfg.recipe_impl"
    # 2026-11-20 第三轮：玩家可见处不许有「原著：…」这种对照行（用户两次投诉）。
    # 手写 impl 的丹药改为只印一行「效果：…」，考据留在 recipe_impl 的内部字段里。
    assert "效果：" in body, "文本要先说这枚丹在本作里干什么（效果：…）"
    assert "原著：" not in body, "丹方文本又把原著对照行印给玩家了"
    assert "本作：" not in body, "「本作：」是开发向措辞，玩家看不懂"


# ---------------------------------------------------------------------------
# 6. 新接的两枚破境丹行为正确
# ---------------------------------------------------------------------------
def test_juqi_san_breaks_to_douzhe():
    p = core.create_player("30001", "聚气", "20001")
    p["lv"] = 9                     # 斗之气九段
    core.add_item(p, "聚气散", 1)
    res = core.use_item(p, "聚气散", used_counts={}, rng=lambda: 0.99)
    assert res.get("ok") and res.get("breakthrough"), res
    assert p["lv"] == 10, p["lv"]
    assert cfg.realm_name(10) == "斗者", cfg.realm_name(10)


def test_douling_dan_star_up_at_douwang():
    wang = next(lv for lv in range(1, cfg.MAX_LV + 1)
                if cfg.realm_name(lv) == "斗王")
    p = core.create_player("30002", "斗王", "20001")
    p["lv"] = wang
    core.add_item(p, "斗灵丹", 1)
    res = core.use_item(p, "斗灵丹", used_counts={}, rng=lambda: 0.99)
    assert res.get("ok") and res.get("star_up"), res
    assert p["lv"] == wang + 1, p["lv"]
    # 不是斗王时只转修为，不升星
    p2 = core.create_player("30003", "斗师", "20001")
    p2["lv"] = 20
    core.add_item(p2, "斗灵丹", 1)
    r2 = core.use_item(p2, "斗灵丹", used_counts={}, rng=lambda: 0.0)
    assert r2.get("ok") and not r2.get("star_up"), r2
    assert p2["lv"] == 20


def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in tests:
        try:
            fn()
            print("  PASS %s" % fn.__name__)
        except AssertionError as e:
            failed += 1
            print("  FAIL %s:\n%s" % (fn.__name__, e))
        except Exception as e:  # noqa: BLE001
            failed += 1
            print("  ERROR %s: %s: %s" % (fn.__name__, type(e).__name__, e))
    print("\n%d/%d passed" % (len(tests) - failed, len(tests)))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
