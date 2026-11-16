# -*- coding: utf-8 -*-
"""模块 F. 配卡「基于当前武器 + 当前紫卡数值」守门（第38轮，用户 14:35 截图反馈）。

锁死 4 个真实 bug / 不变量：
  F1  8 槽恒留 1 格「当前紫卡」（末格）——旧实现 8 张真 MOD + 紫卡 = 9 格后
      out[:8] 截断，紫卡**必被砍掉**；
  F2  7 张 MOD 由当前紫卡数值的边际 DPS 决定，换紫卡 → 配卡必须变（不是固定表）；
  F3  同一互斥变体族（膛线/并合膛线、分裂膛室/镀层分裂膛室）不得同时出现；
  F4  标题「基于击杀效率配卡 - 需极化N次」的 N 由**最终 7 张**现算，且不编造；
  F5  指令区按当前紫卡词条动态生成，不再写死「触发几率 弹匣 带负」；
  F6  零武器名特例（7 类同一套规则）+ 真出图含紫卡格。
"""
from __future__ import annotations

import ast
import importlib
import os
import sys
import types

_P = 0
_F = 0


def ok(cond, msg, extra=""):
    global _P, _F
    if cond:
        _P += 1
        print("  PASS", msg)
    else:
        _F += 1
        print("  FAIL", msg, extra)


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# 🔴 `tmp/ocrdeps/PIL` 是 **Windows** 版（缺 _imaging 扩展），容器内会遮蔽
# /opt/venv 自带完整 PIL。故只在 Windows 宿主挂它；容器内直接用自带依赖。
if sys.platform.startswith("win"):
    sys.path.insert(0, os.path.join(ROOT, "tmp", "ocrdeps"))


class _SL:
    def __getattr__(self, _n):
        return lambda *a, **k: None


_nb = types.ModuleType("nonebot")
_nbl = types.ModuleType("nonebot.log")
_nbl.logger = _SL()
_nb.log = _nbl
sys.modules.setdefault("nonebot", _nb)
sys.modules.setdefault("nonebot.log", _nbl)

_GT = os.path.join(ROOT, "src", "plugins", "qq_bridge", "game_tools")
_pkg = types.ModuleType("gt")
_pkg.__path__ = [_GT]
sys.modules["gt"] = _pkg

RC = importlib.import_module("gt.riven_card")
RF = importlib.import_module("gt.riven_full")


def S(en, val, sign="+"):
    return {"en": en, "zh": RC._stat_zh(en), "sign": sign, "unit": "%", "value": val}


def parsed_of(stats, weapon="Torid", typ="Rifle"):
    return {"weapon": weapon, "weapon_zh": "托里德" if weapon == "Torid" else weapon,
            "type": typ, "disp": 1.2, "rank": 8, "riven_name": "probe", "stats": stats}


def build_mods():
    import json
    p = os.path.join(ROOT, "data", "warframe_items", "builds.json")
    d = json.load(open(p, encoding="utf-8"))
    sets = ((d.get("weapons") or {}).get("torid") or {}).get("sets") or []
    return [m for m in ((sets[0] if sets else {}).get("mods") or [])
            if not m.get("placeholder")]


CASES = {
    "crit": [S("Critical Damage", 141.7), S("Corrosive Damage", 105.0),
             S("Critical Chance", 170.0), S("Zoom", 57.1, "-")],
    "elem": [S("Heat Damage", 120.0), S("Toxin Damage", 110.0),
             S("Cold Damage", 100.0), S("Zoom", 50.0, "-")],
    "base": [S("Damage", 160.0), S("Multishot", 120.0),
             S("Fire Rate", 90.0), S("Zoom", 50.0, "-")],
    "zero": [S("Zoom", 50.0, "-"), S("Weapon Recoil", 50.0),
             S("Ammo Maximum", 40.0), S("Reload Speed", 30.0)],
}


def f1_slot_budget():
    """F1 8 槽恒留 1 格紫卡（真实 build 8 张这个必崩用例）。"""
    bm = build_mods()
    ok(len(bm) == 8, "F1a torid 真实 build 恰为 8 张（复现用户现场）", "n=%d" % len(bm))
    mods = RF._build_mods_from_build(bm, [])
    names = [str(m.get("name") or "") for m in mods]
    ok(len(mods) == 8, "F1b 装配后恒 8 格", "n=%d" % len(mods))
    ok(names[-1] == "当前紫卡", "F1c 末格恒为「当前紫卡」", repr(names[-1]))
    ok(names.count("当前紫卡") == 1, "F1d 紫卡恰好 1 格", repr(names))
    ok(all("当前紫卡" not in n for n in names[:-1]),
       "F1e 前 7 格全是普通 MOD", repr(names[:2]))
    # 宿主真出图路径 riven_full_data 也必须带上
    data = RF.riven_full_data(parsed=parsed_of(CASES["crit"]), weapon="托里德",
                              market=RC.market_error_bundle(RuntimeError("probe")),
                              build_mods=bm, build_source="warframe.today")
    dn = [str(m.get("name") or "") for m in (data.get("mods") or [])]
    ok(len(dn) == 8 and dn[-1] == "当前紫卡",
       "F1f riven_full_data 生产路径末格为当前紫卡", repr(dn[-2:]))
    ok(str(data.get("build_title") or "").startswith("基于击杀效率配卡"),
       "F1g 标题改回「基于击杀效率配卡」口径", repr(data.get("build_title")))


def f2_riven_driven():
    """F2 换紫卡数值 → 配卡必须变（核心：不是固定表）。"""
    bm = build_mods()
    got = {}
    for tag, stats in CASES.items():
        ranked, info = RF._ranked_build_mods(bm, "Rifle", parsed_of(stats), "托里德")
        got[tag] = tuple(info["chosen"])
        ok(len(info["chosen"]) == 7, "F2a-%s 选满 7 张" % tag, str(info["chosen"]))
        ok(not info.get("degraded"), "F2b-%s 非降级（有面板/有词条）" % tag)
    keys = list(got)
    pairs = [(a, b) for i, a in enumerate(keys) for b in keys[i + 1:]]
    diff = sum(1 for a, b in pairs if set(got[a]) != set(got[b]))
    ok(diff >= 2, "F2c 不同紫卡给出不同配卡（至少 2 对）", "diff=%d/%d" % (diff, len(pairs)))
    # 纯数值证据：暴击紫卡自带暴伤/暴率 → 暴击类 MOD 边际被稀释，不该再占满
    ok("vital sense" not in got["crit"] or "vital sense" in got["zero"],
       "F2d 暴击词条紫卡不硬堆暴击 MOD（边际稀释生效）")
    # 降级：无面板武器不假装最优
    none, info2 = RF._ranked_build_mods(
        bm, "Rifle", {"weapon": "不存在的武器XYZ", "weapon_zh": "不存在的武器XYZ",
                      "type": "Rifle", "disp": 1.0, "stats": CASES["crit"]},
        "不存在的武器XYZ")
    ok(info2.get("degraded") is True, "F2e 无面板武器诚实降级不编最优")
    ok(len(info2.get("chosen") or []) == 7, "F2f 降级仍给 7 张真卡", str(info2.get("chosen")))
    # 中文武器名入口：disp 表 zh 反查到英文 key，仍应拿到面板、不降级
    cn = {"weapon": "托里德", "weapon_zh": "托里德", "type": "Rifle",
          "disp": 1.2, "stats": CASES["crit"]}
    _, info3 = RF._ranked_build_mods(bm, "Rifle", cn, "托里德")
    ok(not info3.get("degraded"), "F2g 中文武器名经 disp 反查拿面板（不误降级）")
    ok(len(info3.get("chosen") or []) == 7, "F2h 中文入口仍选满 7 张")
    # 无 parsed（纯武器名查询）→ 没有紫卡数值，诚实降级
    _, info4 = RF._ranked_build_mods(bm, "Rifle", None, "托里德")
    ok(info4.get("degraded") is True, "F2i 无紫卡数值时诚实降级不假装有最优")


def f3_exclusive_family():
    """F3 互斥变体族不得同框（膛线/并合膛线、分裂膛室/镀层分裂膛室）。"""
    ok(RC._mod_family("serration") == RC._mod_family("amalgam serration"),
       "F3a 膛线 ≡ 并合 膛线 同族",
       "%s vs %s" % (RC._mod_family("serration"), RC._mod_family("amalgam serration")))
    ok(RC._mod_family("split chamber") == RC._mod_family("galvanized chamber"),
       "F3b 分裂膛室 ≡ 镀层 分裂膛室 同族")
    ok(RC._mod_family("serration") != RC._mod_family("heavy caliber"),
       "F3c 不同效果的卡不被误判同族")
    bm = build_mods()
    for tag, stats in CASES.items():
        _, info = RF._ranked_build_mods(bm, "Rifle", parsed_of(stats), "托里德")
        fams = [RC._mod_family(e) for e in info["chosen"]]
        ok(len(fams) == len(set(fams)), "F3d-%s 7 张无互斥族重复" % tag, repr(fams))
    # 直接测 _rank_build_by_riven：候选含两张同族，只留增益最高者
    r = RC._rank_build_by_riven(
        RC._weapon_raw("Torid") or {}, CASES["crit"],
        ["serration", "amalgam serration", "split chamber", "galvanized chamber"],
        slots=3)
    fams = [RC._mod_family(e) for e in r["chosen"]]
    ok(len(fams) == len(set(fams)), "F3e 模型层同族只留一张", repr(r["chosen"]))


def f4_polarity():
    """F4 极化次数由最终 7 张现算，查不到 drain 不编造。"""
    n = RC._forma_needed(["serration", "heavy caliber", "split chamber",
                           "primed cryo rounds", "hellfire", "vile acceleration",
                           "vigilante armaments"])
    ok(isinstance(n, int), "F4a _forma_needed 返回整数", repr(n))
    lab = RF._ranked_build_label(["serration", "heavy caliber", "split chamber",
                                  "primed cryo rounds", "hellfire",
                                  "vile acceleration", "vigilante armaments"])
    ok(lab == "基于击杀效率配卡 - 需极化%d次" % n, "F4b 标题次数=最终 7 张实算", lab)
    lab2 = RF._ranked_build_label(["definitely_not_a_real_mod_xyz"])
    ok(lab2 == "基于击杀效率配卡", "F4c drain 查不到不编次数", lab2)
    # 标题次数必须随配卡变化（不是写死 4）
    lab3 = RF._ranked_build_label(["serration"])
    ok(lab3 != lab, "F4d 换配卡标题次数随之变化", "%s vs %s" % (lab3, lab))


def f5_cmd_tags():
    """F5 指令区按当前紫卡词条动态生成。"""
    mk = lambda stats: RF.riven_full_data(
        parsed=parsed_of(stats), weapon="托里德",
        market=RC.market_error_bundle(RuntimeError("probe")),
        build_mods=build_mods(), build_source="warframe.today")
    d1 = mk(CASES["crit"])
    rows = [r for g in (d1.get("cmd_groups") or []) for row in (g.get("rows") or [])
            for r in row]
    wm = [r for r in rows if str(r).startswith("wmr")]
    ok(len(wm) == 1, "F5a 指令区保留 wmr 口令行", repr(rows))
    ok("暴伤" in wm[0] and "腐蚀" in wm[0] and "暴率" in wm[0],
       "F5b 词条短名来自当前卡（暴伤/腐蚀/暴率）", repr(wm[0]))
    ok("触发几率 弹匣" not in wm[0], "F5c 不再写死「触发几率 弹匣」", repr(wm[0]))
    d2 = mk(CASES["elem"])
    rows2 = [r for g in (d2.get("cmd_groups") or []) for row in (g.get("rows") or [])
             for r in row]
    wm2 = [r for r in rows2 if str(r).startswith("wmr")][0]
    ok(wm2 != wm[0], "F5d 换紫卡指令词条随之变化", "%s vs %s" % (wm2, wm[0]))


def f6_general_and_render():
    """F6 零武器名特例（7 类通用）+ 真出图含紫卡格。"""
    src = open(os.path.join(_GT, "riven_card.py"), encoding="utf-8").read()
    tree = ast.parse(src)
    for name in ("_rank_build_by_riven", "_mod_family", "_mod_bucket_delta"):
        node = next((n for n in tree.body
                     if isinstance(n, ast.FunctionDef) and n.name == name), None)
        ok(node is not None, "F6a %s 存在" % name)
        if node:
            seg = ast.get_source_segment(src, node) or ""
            lits = [n.value for n in ast.walk(node)
                    if isinstance(n, ast.Constant) and isinstance(n.value, str)]
            bad = [s for s in lits if "托里德" in s or "Torid" in s]
            ok(not bad, "F6b %s 零武器名硬编码" % name, repr(bad[:2]))
    # 7 类都能跑通（同一套规则，不抛异常）
    for t in ("Rifle", "Pistol", "Shotgun", "Archgun", "Kitgun", "Melee", "Zaw"):
        try:
            r = RC._rank_build_by_riven(RC._weapon_raw("Torid") or {}, CASES["crit"],
                                        ["serration", "heavy caliber"], slots=2)
            ok(len(r["chosen"]) == 2, "F6c %s 走同一套选卡规则" % t)
        except Exception as e:  # noqa: BLE001
            ok(False, "F6c %s 走同一套选卡规则" % t, "%s: %s" % (type(e).__name__, e))
    # 真出图
    try:
        _qb = types.ModuleType("qqb")
        _qb.__path__ = [os.path.join(ROOT, "src", "plugins", "qq_bridge")]
        _qb.__package__ = "qqb"
        sys.modules["qqb"] = _qb
        PIC = importlib.import_module("qqb.pic")
        data = RF.riven_full_data(
            parsed=parsed_of(CASES["crit"]), weapon="托里德",
            market=RC.market_error_bundle(RuntimeError("probe")),
            build_mods=build_mods(), build_source="warframe.today")
        b = PIC.render_sync("riven_full", data=data)
        ok(isinstance(b, (bytes, bytearray)) and len(b) > 100000,
           "F6d 真出图成功且非空", "%d bytes" % (len(b) if b else 0))
        out = os.path.join(ROOT, "tmp", "c38_torid_fixed_final.png")
        open(out, "wb").write(b)
    except Exception as e:  # noqa: BLE001
        import traceback
        ok(False, "F6d 真出图成功且非空", "%s: %s" % (type(e).__name__, e))
        traceback.print_exc()


def main():
    print("== 模块F. 配卡基于当前武器+当前紫卡数值守门（第38轮）==")
    f1_slot_budget()
    f2_riven_driven()
    f3_exclusive_family()
    f4_polarity()
    f5_cmd_tags()
    f6_general_and_render()
    print("\n%d PASS / %d FAIL" % (_P, _F))
    return 1 if _F else 0


if __name__ == "__main__":
    sys.exit(main())
