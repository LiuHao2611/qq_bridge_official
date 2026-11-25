# -*- coding: utf-8 -*-
"""炼药师品阶 / 丹药品阶 / 功效标签守门（2026-11-18 prove-it）。

用户原话：「怎么好像没看到恢复斗气的丹药呢，而且我一个四品炼药师，怎么能
炼制四品以上的丹药，而且失败概率一样，这符合原著吗」。

守四件用户感受得到的事（每条都有原著证据，见 config.py 注释）：
  1. 够格丹药用基础率；越1档×0.35（勉强）、越2档以上×0.05（几无可能）；
  2. core.craft 返回 diff，供文案讲清「为什么四品炼七品老失败」；
  3. 回斗气等丹药在纯文本炼药卡与图片卡都挂〔回斗气〕功效标签；
  4. 单丹成功率按每位玩家的炼药师品阶独立计算，不再所有丹药一个值。

注入验证（把错误实现放回去，对应用例必须红）：
  * craft_rate_for 改成恒返回 craft_rate(alch_idx) → rate 三条全红；
  * craft 不返回 diff → test_craft_returns_diff 红；
  * effect_tag 恒返回 '' / 展示层不拼标签 → 标签两条红。

运行：python tests/test_doupo_alchemy_grade.py
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

from doupo import core, config as cfg, card, commands  # noqa: E402


def _p(lv=1, alch=3):
    p = core.create_player("10001", "炼药品阶测试", "20001")
    p["lv"] = lv
    p["exp"] = 0
    p["alch"] = alch
    # 2026-11-24 入道不送火：炼丹前须持有任意火（紫火=atk 型，不污染 alchemy 加成）
    p["flames"]["紫火"] = 1
    st = core.stats(p)
    p["hp"] = st["体力"]
    p["mp"] = core.mp_max(p)
    return p


# ---------------------------------------------------------------------------
# 1. 成功率随「炼药师品阶 vs 丹药品阶」变化
# ---------------------------------------------------------------------------
def test_rate_enough_uses_base():
    """刚好够格（diff==0）用基础率，不额外加成/惩罚。"""
    for alch, grade in [(3, "四品"), (5, "六品"), (8, "九品"), (0, "一品")]:
        rate, diff = cfg.craft_rate_for(alch, grade)
        assert diff == 0, (alch, grade, diff)
        assert abs(rate - cfg.craft_rate(alch)) < 1e-9, (alch, grade, rate)


def test_rate_overreach_one_is_35pct():
    """越1档「勉强」：基础率×0.35（第495章四品炼五品成功率太小）。"""
    for alch, grade in [(3, "五品"), (0, "二品"), (4, "六品")]:
        rate, diff = cfg.craft_rate_for(alch, grade)
        base = cfg.craft_rate(alch)
        assert diff == 1, (alch, grade, diff)
        assert abs(rate - base * 0.35) < 1e-9, (alch, grade, rate, base)


def test_rate_overreach_two_plus_is_5pct():
    """越≥2档「几无可能」：基础率×0.05，且严格低于越1档。"""
    rate3, diff3 = cfg.craft_rate_for(3, "六品")   # 四品炼六品 越2
    rate5, diff5 = cfg.craft_rate_for(3, "八品")   # 四品炼八品 越4
    assert diff3 == 2 and diff5 > 2, (diff3, diff5)
    base3 = cfg.craft_rate(3)
    assert abs(rate3 - base3 * 0.05) < 1e-9
    assert abs(rate5 - base3 * 0.05) < 1e-9
    one, _ = cfg.craft_rate_for(3, "五品")
    assert rate3 < one < base3, (rate3, one, base3)


def test_four_grade_alchemist_rates_distinct():
    """四品炼药师炼四/五/六品，成功率三者严格递减——不再是「一个值」。"""
    r4, _ = cfg.craft_rate_for(3, "四品")
    r5, _ = cfg.craft_rate_for(3, "五品")
    r6, _ = cfg.craft_rate_for(3, "六品")
    assert r4 > r5 > r6, (r4, r5, r6)


# ---------------------------------------------------------------------------
# 2. core.craft 回传 diff
# ---------------------------------------------------------------------------
def test_craft_returns_diff():
    """成功与失败分支都要带 diff，供 cmd_craft 给越阶提示。"""
    p = _p(lv=60, alch=3)
    for k, v in cfg.RECIPE_BY_NAME["凝血散"]["need"].items():
        core.add_item(p, k, v)
    p["last_craft"] = 0
    ok = core.craft(p, "凝血散", rng=lambda: 0.0, now_ts=100000)
    # 凝血散二品、四品炼药师 → diff=-2（够格）；关键是 diff 字段存在且<=0
    assert ok["ok"] is True and ok["diff"] <= 0, ok
    for k, v in cfg.RECIPE_BY_NAME["凝血散"]["need"].items():
        core.add_item(p, k, v)
    p["last_craft"] = 0
    fail = core.craft(p, "凝血散", rng=lambda: 0.999, now_ts=100000)
    assert fail["ok"] is False and "diff" in fail, fail


# ---------------------------------------------------------------------------
# 3. 功效标签（回斗气）在文本 + 卡片都可见
# ---------------------------------------------------------------------------
def test_effect_tag_map():
    assert cfg.effect_tag("mp") == "回斗气"
    assert cfg.effect_tag("hp") == "回血"
    assert cfg.effect_tag("unknown_xxx") == "", "未知 effect 不编造标签"


def test_mp_recipes_visible_in_text():
    """纯文本炼药卡：二品回气丹必须带〔回斗气〕。"""
    import asyncio
    from doupo import store as store_mod

    class _Mem:
        def __init__(self, p):
            self.p = p

        async def get_player(self, qq):
            return dict(self.p)

        async def put_player(self, p):
            self.p = dict(p)

    p = _p(lv=60, alch=3)
    store = _Mem(p)
    txt = asyncio.run(commands.cmd_recipes(store, "10001"))
    # 回气丹在所有页；lv60 可能分页，取含它的那页
    if "回气丹" not in txt:
        for pg in range(2, 20):
            txt = asyncio.run(commands.cmd_recipes(store, "10001", page=pg))
            if "回气丹" in txt:
                break
    line = next((ln for ln in txt.splitlines() if "回气丹" in ln), "")
    assert "〔回斗气〕" in line, "回气丹行应显式标〔回斗气〕：%r" % line
    assert "成" in txt and "把握" in txt, "文本卡须用成数表述：%s" % txt[:200]
    # 头两行（品阶 + 规则说明）不得出现百分号；每行成功率片段同（原功效描述的
    # 「+5%」是效果数值，合法保留，故不检查全文）。
    head = "\n".join(txt.splitlines()[:3])
    assert "%" not in head and "越阶骤降" in head, "规则头行须用成数：%s" % head
    assert "越" in txt, "文本卡须给出越阶规则说明"


def test_mp_recipes_visible_in_card():
    """图片卡：满级必须看得见回气丹（回斗气的那味）。

    2026-11-25 用户「炼药的卡片就只展示丹方和丹药本身效果就行，别写其他废话」——
    卡面只剩本效，〔回斗气〕功效标签与成数全部撤出卡面（详见
    ``card.build_recipes_data`` docstring 与 ``tests/test_view_item.py`` 的
    ``test_recipes_card_is_slim``）。所以这里守「丹在、本效写着回斗气」，
    不再要求 effect_tag 字段存在。
    """
    p = _p(lv=103, alch=3)
    found = None
    for pg in range(1, 20):
        d = card.build_recipes_data(p, pg)
        for g in d["groups"]:
            for r in g["rows"]:
                if r["name"] == "回气丹":
                    found = r
                    break
            if found:
                break
        if found:
            break
    assert found is not None, "满级应可见回气丹"
    # 卡面字段面收敛到「材料 / 本效 / 齐备」三件，成数与标签不许回流
    assert "effect_tag" not in found and "rate" not in found and "diff" not in found, found
    assert "斗气" in found["desc"], "回气丹本效应写着回斗气：%s" % found["desc"]
    assert "〔" not in found["desc"], "功效标签已撤出卡面：%s" % found["desc"]
    assert "把握" not in found["desc"], "成数已撤出卡面：%s" % found["desc"]


def test_card_rate_diff_by_alchemist():
    """同一枚丹药，四品与九品炼药师的**成丹率**不同 —— 2026-11-25 起看详情卡。

    卡片瘦身后成数不进炼药卡，但「炼药师品阶越高越稳」这件事玩家仍要看得见：
    改由「查看 <丹名>」详情卡的「从何而来」段承载（成丹率 + 越 N 档）。
    """
    def detail_for(alch):
        p = _p(lv=103, alch=alch)
        d = card.build_item_detail_data(p, "凝血散")
        assert d, "详情卡应查得到凝血散"
        rows = [r for g in d["groups"] for r in g["rows"]]
        rate_rows = [r for r in rows if "成丹率" in r]
        assert rate_rows, "详情卡缺成丹率：%s" % rows
        rate, diff = cfg.craft_rate_for(int(p.get("alch", 0)),
                                        str(cfg.RECIPE_BY_NAME["凝血散"]["grade"]))
        return rate, diff, rate_rows[0]

    low_rate, low_diff, low_row = detail_for(3)   # 四品炼二品 够格
    high_rate, high_diff, high_row = detail_for(8)  # 九品炼二品 够格
    assert low_diff <= 0 and high_diff <= 0
    assert high_rate >= low_rate, "炼药师品阶越高成功率越高：%s" % (
        (low_rate, high_rate),)
    # 详情卡上的成数必须与 cfg 同源（不许另算一套）
    assert cfg.rate_cn(high_rate) in high_row, (high_row, cfg.rate_cn(high_rate))
    assert "越" not in low_row, "高品阶炼低阶不该标越档：%s" % low_row


# ---------------------------------------------------------------------------
# 4. 高品阶炼低阶更稳（用户「四品炼一品还是70？」）
# ---------------------------------------------------------------------------
def test_low_grade_bonus_monotonic():
    """四品炼一品 > 炼二品 > 炼三品 > 炼四品，每低一档更稳（不是一律 70%）。"""
    rates = {g: cfg.craft_rate_for(3, g)[0] for g in
             ("一品", "二品", "三品", "四品")}
    assert rates["一品"] > rates["二品"] > rates["三品"] > rates["四品"], rates
    assert abs(rates["四品"] - 0.70) < 1e-9, rates       # 够格基础率
    assert abs(rates["一品"] - 0.94) < 1e-9, rates       # 0.70 + 3×0.08
    assert abs(rates["三品"] - 0.78) < 1e-9, rates       # 0.70 + 1×0.08


def test_low_grade_bonus_capped():
    """低阶加成封顶九成五（展示取整为九成），不超上限。"""
    rate, diff = cfg.craft_rate_for(8, "一品")   # 九品炼一品 diff=-8
    assert diff < 0 and rate <= cfg.CRAFT_RATE_CAP + 1e-9
    assert abs(rate - 0.95) < 1e-9, rate


def test_four_grade_full_ladder():
    """四品炼药师完整梯度：低阶越稳、越高阶越难，成数表述符合原著口语。"""
    ladder = [(g, cfg.craft_rate_for(3, g)[0]) for g in
              ("一品", "二品", "三品", "四品", "五品", "六品")]
    expect_cn = ["九成", "八成", "七成", "七成", "二成", "零成"]
    got_cn = [cfg.rate_cn(r) for _, r in ladder]
    assert got_cn == expect_cn, (got_cn, expect_cn)
    # 严格单调递减：品阶越高越难
    vals = [r for _, r in ladder]
    assert all(vals[i] > vals[i + 1] for i in range(len(vals) - 1)), vals


# ---------------------------------------------------------------------------
# 5. 成数表述 rate_cn（不用百分号）
# ---------------------------------------------------------------------------
def test_rate_cn_mapping():
    cases = {
        1.0: "十成", 0.94: "九成", 0.70: "七成", 0.86: "八成",
        0.78: "七成", 0.245: "二成", 0.035: "零成", 0.0: "零成",
    }
    for rate, want in cases.items():
        got = cfg.rate_cn(rate)
        assert got == want, (rate, got, want)
        assert "%" not in got and "." not in got, "成数不得含%%或小数点：%s" % ((rate, got),)
        assert not any(ch.isdigit() for ch in got), "成数须纯中文：%s" % ((rate, got),)
    # 越界夹取
    assert cfg.rate_cn(1.5) == "十成" and cfg.rate_cn(-0.2) == "零成"


def test_craft_reply_uses_cheng_no_percent():
    """丹成/失败回包用「N成把握」而非「N%」，且带真实成数。"""
    import asyncio
    from doupo import store as store_mod

    class _Mem:
        def __init__(self, p):
            self.p = p

        async def get_player(self, qq):
            return dict(self.p)

        async def put_player(self, p):
            self.p = dict(p)

    p = _p(lv=1, alch=3)          # 四品炼药师，炼一品聚气散 → 九成
    for k, v in cfg.RECIPE_BY_NAME["聚气散"]["need"].items():
        core.add_item(p, k, v)
    p["last_craft"] = 0
    store = _Mem(p)
    r = asyncio.run(commands.cmd_craft(store, "10001", "聚气散"))
    assert "成" in r and "把握" in r, r
    assert "%" not in r, r
    # 成功或失败都要带四品炼一品的真实成数「九成」（成功/失败回包同源 rate_cn）
    assert ("丹成" in r or "失败" in r), r
    assert "九成" in r, r
    # 纯中文：不得出现阿拉伯数字成数
    assert "9成" not in r and "7成" not in r, r


def test_card_desc_uses_cheng_no_percent():
    """成数仍是**纯中文成数、不带百分号** —— 2026-11-25 起落在详情卡上。

    卡片瘦身前这条守的是炼药卡 desc；瘦身后成数只出现在「查看 <丹名>」详情卡
    与纯文本「炼药」里，所以改守详情卡那行——口径（N成、不出现 %）一字不变。
    """
    import re
    p = _p(lv=103, alch=3)
    checked = 0
    for name in ("凝血散", "回气丹", "破厄丹"):
        d = card.build_item_detail_data(p, name)
        assert d, "详情卡应查得到 %s" % name
        rows = [r for g in d["groups"] for r in g["rows"]]
        rate_rows = [r for r in rows if "成丹率" in r]
        assert rate_rows, "%s 详情卡缺成丹率：%s" % (name, rows)
        for r in rate_rows:
            assert "成" in r, (name, r)
            assert not re.search(r"\d+\s*%", r.split("（")[0]), (name, r)
        checked += 1
    assert checked == 3, checked

    # 卡片侧：炼药卡 desc 不再印任何成数（同一信息不重复给）
    d = card.build_recipes_data(p, 1)
    for g in d["groups"]:
        for r in g["rows"]:
            assert "把握" not in r["desc"], (r["name"], r["desc"])


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
    print(f"ALCHEMY_GRADE_TOTAL={len(fns)} PASSED={ok} FAILED={len(fail)}")
    return 0 if not fail else 1


if __name__ == "__main__":
    sys.exit(_run_all())
