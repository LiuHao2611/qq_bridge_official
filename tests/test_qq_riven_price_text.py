# -*- coding: utf-8 -*-
"""守门：市场两盒失败态文案不像「系统故障」、成功态真出数（官方副本，2026-11-12）。

用户投诉「市场估价/词条差价没做好」：图1 两盒显示「暂无匹配样本 / 数据获取失败 /
接口样本不足」，读起来像机器人坏了。真根因是 WFM slug 连字符→400（由
test_qq_riven_slug.py 修复、使这把武器 status=ok 真出数）；本守门钉死剩下两件事：
  1. 仍真无数据的武器（status=error）走**诚实但不像故障**的降级文案；
  2. 成功态两盒真出数、口径独立（不编数、不写「暂无」冒充成功）。

锚定 official 副本根（parents[1]）。riven_price.py 只 import 标准库，宿主可直跑。
"""
from __future__ import annotations

import ast
import importlib.util
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_PRICE = _REPO / "src" / "plugins" / "qq_bridge" / "game_tools" / "riven_price.py"
_P = _F = 0
# 「像系统故障」的旧措辞：失败态不得再出现
_FAULTY = ("数据获取失败", "接口样本不足", "窗口样本不足", "获取失败")


def ok(cond, name, detail=""):
    global _P, _F
    if cond:
        _P += 1
        print("PASS %-52s %s" % (name, detail))
    else:
        _F += 1
        print("FAIL %-52s %s" % (name, detail))


def _load():
    spec = importlib.util.spec_from_file_location("_rp_under_test", _PRICE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main():
    rp = _load()

    # A 失败态：诚实降级、零故障措辞、零异常串
    print("== A. 失败态降级文案 ==")
    for mkt in [
        {"status": "error", "error": "RivenMarketError: WFM 拍卖返回 NoneType 对象"},
        {"status": "error", "error": "连接超时"},
        {"status": "ok", "med": None, "n": 0},  # ok 但无有效中位价 → 同走降级
        {}, None,
    ]:
        left, right = rp.price_boxes(mkt)
        blob = str(left) + str(right)
        ok(not any(t in blob for t in _FAULTY),
           "A 失败态无故障措辞", "mkt=%r" % (mkt,))
        ok(not any(t in blob for t in ("RivenMarketError", "NoneType", "Traceback")),
           "A 失败态无异常串")
        ok("暂无紫卡挂牌样本" in str(left.get("foot")),
           "A 失败态诚实说明无挂牌样本", "foot=%r" % left.get("foot"))
        ok(left.get("big") == "暂无匹配样本" and right.get("big") == "暂无实盘样本",
           "A 失败态 big 不编数")

    # B 成功态（图2 真出数快照）：两盒真出、口径独立
    print("== B. 成功态真出数 ==")
    mkt = {"status": "ok", "n": 5, "med": 70.0, "zero": 1.0,
           "match_tier": "同负词条", "rec_neg": {"delta": 230.0},
           "exact": {"status": "unknown", "n": 0}}
    left, right = rp.price_boxes(mkt)
    ok(left.get("big") == "70P", "B1 左盒出真中位价", "big=%r" % left.get("big"))
    ok(left.get("sub_val") == "1P", "B2 零洗中位价取 rank0", "sub=%r" % left.get("sub_val"))
    ok("匹配层级" in str(left.get("foot")), "B3 左盒标注匹配层级")
    ok(right.get("big") not in ("暂无匹配样本", "暂无实盘样本", None),
       "B4 右盒出模型估值", "big=%r" % right.get("big"))
    ok(left.get("sub_val") != right.get("big"), "B5 两盒口径独立（非同值）")
    ok("置信度%d%%" % rp.confidence(5) in str(right.get("sub")),
       "B6 右盒置信度由样本数推导")

    # C 注入反证：把失败态改回旧故障措辞 → price_boxes 真输出必须被 A 判据抓到
    print("== C. 注入反证 ==")
    src = _PRICE.read_text(encoding="utf-8")
    ok("该武器 WFM 暂无紫卡挂牌样本" in src,
       "C1 当前真源码含诚实降级文案")
    # C2 用 AST 取 price_boxes 执行代码里的字符串常量（排除注释/docstring，
    #     否则本文件第 93 行「不用『数据获取失败』」的注释会被子串误命中）
    tree = ast.parse(src)
    fn = next((n for n in ast.walk(tree)
               if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
               and n.name == "price_boxes"), None)
    doc_ids = set()
    for n in ast.walk(tree):
        if isinstance(n, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            b = getattr(n, "body", None)
            if b and isinstance(b[0], ast.Expr) and isinstance(b[0].value, ast.Constant) \
                    and isinstance(b[0].value.value, str):
                doc_ids.add(id(b[0].value))
    code_strs = {n.value for n in ast.walk(fn)
                 if isinstance(n, ast.Constant) and isinstance(n.value, str)
                 and id(n) not in doc_ids} if fn else set()
    ok(not any(t in "".join(code_strs) for t in _FAULTY),
       "C2 price_boxes 执行代码不含旧故障措辞",
       "hit=%r" % [t for t in _FAULTY if t in "".join(code_strs)])
    inj = src.replace("该武器 WFM 暂无紫卡挂牌样本", "数据获取失败")
    ok("数据获取失败" in inj and inj != src,
       "C3 注入旧故障措辞被 A 判据抓到（反证样本构造）")

    # D 2026-11-13 第31轮：价格口径按 WFM 实测重定（Docker 恢复后真网络）
    print("== D. 口径实测修正（零洗中位 / 卡面词条溢价 / 映射覆盖）==")
    card_src = (_REPO / "src" / "plugins" / "qq_bridge" / "game_tools"
                / "riven_card.py").read_text(encoding="utf-8")
    ctree = ast.parse(card_src)

    # D1 零洗价必须是**中位**：旧实现 `zero_ps[0]` = 最低价，标签却写「零洗中位价」。
    #    实测对照 Robot：阿利乌 最低 120 / 中位 399（Robot 399 逐位命中）。
    fb = next((n for n in ast.walk(ctree)
               if isinstance(n, ast.AsyncFunctionDef) and n.name == "fetch_market_bundle"),
              None)
    fb_src = ast.get_source_segment(card_src, fb) or "" if fb else ""
    # 只看 AST（注释里引用旧写法 `zero_ps[0]` 不算执行代码——V10d 踩过的同一个坑）
    _subscript_zero = [n for n in (ast.walk(fb) if fb else [])
                       if isinstance(n, ast.Subscript)
                       and isinstance(n.value, ast.Name) and n.value.id == "zero_ps"]
    ok(not _subscript_zero and "_robust_center(zero_ps)" in fb_src,
       "D1 零洗中位价取中位（不是最低价）",
       "发现下标取值 %d 处" % len(_subscript_zero) if _subscript_zero else "")
    ok("_robust_center(zero_ps) if zero_ps else None" in fb_src.replace("\n", " ")
       or "_robust_center(zero_ps)" in fb_src,
       "D2 零洗无样本时留 None（不编数）")
    ok('"re_rolls"' in fb_src, "D3 零洗优先取 re_rolls==0 的真·未洗子集")

    # D4 右盒优先用**卡面自身**词条溢价，而不是「推荐负面」溢价
    prem_mkt = {"status": "ok", "n": 40, "med": 400.0, "zero": 400.0,
                "match_tier": "全武器地板价视图", "rec_neg": {"delta": 17.0},
                "exact": {"status": "few", "n": 0},
                "premium": {"rows": [{"en": "Damage", "delta": 250.0}], "delta": 250.0,
                            "skipped": [], "incomplete": False}}
    l2, r2 = rp.price_boxes(prem_mkt)
    ok(r2.get("big") == "650P", "D4 右盒 = 地板价 + 卡面词条溢价（400+250）",
       "big=%r" % r2.get("big"))
    ok("卡面" in str(r2.get("foot")) and "词条" in str(r2.get("foot")),
       "D5 右盒脚注标明溢价来源是卡面词条", "foot=%r" % r2.get("foot"))
    # D6 有词条因限流未计入 → 必须显式标注，不许静默少算
    inc = dict(prem_mkt, premium={"rows": [{"en": "Damage", "delta": 250.0}],
                                  "delta": 250.0, "skipped": ["X(限流/请求失败)"],
                                  "incomplete": True})
    _, r3 = rp.price_boxes(inc)
    ok("限流" in str(r3.get("foot")), "D6 限流未计入必须写在脚注",
       "foot=%r" % r3.get("foot"))
    # D7 premium 为空（词条全不可测）→ 退回旧口径，不得把 0 当溢价
    noP = dict(prem_mkt, premium={"rows": [], "delta": 0.0, "skipped": [], "incomplete": False})
    _, r4 = rp.price_boxes(noP)
    ok(r4.get("big") == "417P", "D7 无词条可测时退回推荐负面溢价（400+17）",
       "big=%r" % r4.get("big"))

    # D8 base 池真实语义 = 全武器地板价视图（asc 无词条过滤 + page=2 实测完全重叠）
    ok("全武器地板价视图" in fb_src,
       "D8 base 池标注为「全武器地板价视图」（不是「同负词条」）")

    # D9 `_DE_TO_WFM` 覆盖：17 条实测映射必须在，且「Ammo Efficiency 不存在」的旧断言被推翻
    de_map = None
    for n in ctree.body:
        if isinstance(n, ast.Assign) and any(
                getattr(t, "id", "") == "_DE_TO_WFM" for t in n.targets):
            de_map = ast.literal_eval(n.value)
    need = {"Damage": "base_damage_/_melee_damage", "Ammo Efficiency": "ammo_efficiency",
            "Blast Damage": "blast", "Gas Damage": "gas", "Magnetic Damage": "magnetic",
            "Radiation Damage": "radiation", "Damage to Orokin": "damage_to_orokin",
            "Status Damage": "status_damage", "Status Duration": "status_duration",
            "Weak Point Critical Chance": "weak_point_critical_chance",
            "Fire Rate / Attack Speed": "fire_rate_/_attack_speed", "Range": "range",
            "Finisher Damage": "finisher_damage", "Slam Attack Damage": "slam_attack_damage",
            "Melee Damage on Heavy Attack": "melee_damage_on_heavy_attack",
            "Heavy Attack Wind Up Speed": "heavy_attack_wind_up_speed",
            "Combo Duration": "combo_duration"}
    missing = {k: v for k, v in need.items() if (de_map or {}).get(k) != v}
    ok(not missing, "D9 17 条实测 slug 映射齐备", "缺/错: %s" % missing if missing else "17/17")
    # D10 实测 400 的 6 条必须**不**映射（宁可 exact 报 unknown，也不猜 slug）
    bad = [k for k in ("Initial Combo", "Additional Combo Count Chance",
                       "Chance to Gain Combo Count", "Damage to Scaldra",
                       "Damage to Techrot") if (de_map or {}).get(k)]
    ok(not bad, "D10 实测不存在的 slug 不得映射", "误映射: %s" % bad if bad else "5/5")

    # D11 exact 查询只用**卡面自身**负面（旧实现拼的是推荐负面 → 3正0负的卡必 0 样本）
    ok('neg_extra + "&positive_stats="' not in fb_src,
       "D11 exact 不再拼「推荐负面」（3正0负的卡可正常精确匹配）")
    ok("neg_slugs_card" in fb_src, "D12 exact 用卡面自身负面构造 negative_stats")

    print("\n%d PASS / %d FAIL" % (_P, _F))
    raise SystemExit(1 if _F else 0)


if __name__ == "__main__":
    main()
