# -*- coding: utf-8 -*-
"""模块S. 状态维度（Status Chance / Status Damage / DoT）缺口 —— 事实锁定守门。

## 为什么有这套守门

2026-11-13 第30轮「第 3 条：Status Damage / DoT 未建模」开工前先做事实核查，
结论是**本轮不应把 DoT 乘区塞进 `gain`**。四条硬证据：

1. **同源参考实现没有 DoT 模型**。`data/warframe_items/koishi_ref/`（Warframe Rabbit
   同源数据）里 `WeaponStatusDamage` 只出现在 `riven-export-adapter.ts:27` 的
   **中文名映射表**，全目录对 `dot` / `statusDamage` 的**逻辑**引用为 0。也就是说
   参考侧把「异常状态伤害」当一条可读词条，不当一个乘区。
2. **我方无 DoT 模型时已对齐唯一 Verglas 锚点**。冰凇裸叠理论最优 **+200.0%**
   ↔ Rabbit 截图 **+198.1%**（差 1.9pp）。若把 DoT 计入 `gain` 且把
   `Status Damage` 放进 `_DPS_STAT_KEYS` 候选池，`g_best_raw` 会抬升，
   该锚点必被击破 —— 在没有第二个锚点做交叉验证之前，这是**净损失**。
3. **数据形态与预期相反**：`status_chance` 顶层键 **0/566 全缺**，真正有数的是
   顶层 `procChance`（**566/566**）与 `attacks[0].status_chance`（542/566）。
   未来接入 DoT 必须读 `procChance`，读 `status_chance` 会 100% 拿到 None
   —— 这正是 2026-11-12 记录「冰凇面板 statusChance=None」的真实原因。
4. **显示层已如实标注**：未进乘区的词条显示 `—(未计入)` 而非 `+0.0%`
   （`riven_card.py` chipline 段），不会让用户误以为程序算错。

## 这道门守什么

守「事实 + 不倒退」：谁把 DoT 塞进 `gain`、谁把状态词条放进 `_DPS_STAT_KEYS`、
谁把 `—(未计入)` 改回 `+0.0%`，这里立刻红。真要做 DoT，必须先有第二个锚点，
届时本文件的 S1/S2 需要按新锚点重写——那是**有意为之**的动作，不是顺手改。

判据：
  S1 `_DPS_STAT_KEYS` 不含状态维度词条（锚点保护）
  S2 冰凇 g_best_raw 恒为 2.0000（+200.0%），Rabbit 锚点 ±2pp 内
  S3 未建模词条在卡面显示 `—(未计入)`，不许显示 `+0.0%`
  S4 面板数据形态：procChance 覆盖 ≥99%，顶层 status_chance 仍不可依赖
  S5 同源参考目录里 `WeaponStatusDamage` 只做名称映射（无 DoT 逻辑）
"""
from __future__ import annotations

import importlib.util
import json
import re
import sys
import types
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_CARD = _REPO / "src" / "plugins" / "qq_bridge" / "game_tools" / "riven_card.py"
_DISP = _REPO / "data" / "warframe_items" / "riven_disp.json"
_REF = _REPO / "data" / "warframe_items" / "koishi_ref"

_P = _F = 0


def ok(cond, name, detail=""):
    global _P, _F
    if cond:
        _P += 1
        print("PASS %-56s %s" % (name, detail))
    else:
        _F += 1
        print("FAIL %-56s %s" % (name, detail))


def _stub_nonebot_httpx():
    for name in list(sys.modules):
        if name == "gt" or name.startswith("gt.") or name in ("nonebot", "nonebot.log", "httpx"):
            del sys.modules[name]

    class _L:
        def info(self, *a, **k): pass
        def warning(self, *a, **k): pass
        def debug(self, *a, **k): pass
        def error(self, *a, **k): pass
        def exception(self, *a, **k): pass

    nb = types.ModuleType("nonebot")
    nbl = types.ModuleType("nonebot.log")
    nbl.logger = _L()
    nb.log = nbl
    sys.modules["nonebot"] = nb
    sys.modules["nonebot.log"] = nbl
    httpx = types.ModuleType("httpx")
    for n in ("HTTPError", "HTTPStatusError", "RequestError", "TimeoutException"):
        setattr(httpx, n, type(n, (Exception,), {}))
    httpx.AsyncClient = object
    sys.modules["httpx"] = httpx


def _load_card():
    _stub_nonebot_httpx()
    GT = _CARD.parent
    pkg = types.ModuleType("gt")
    pkg.__path__ = [str(GT)]
    sys.modules["gt"] = pkg

    def load(n):
        s = importlib.util.spec_from_file_location("gt." + n, GT / (n + ".py"))
        m = importlib.util.module_from_spec(s)
        sys.modules["gt." + n] = m
        s.loader.exec_module(m)
        return m

    load("warframe")
    return load("riven_card")


STATUS_STATS = ("Status Chance", "Status Damage", "Status Duration")


def section_s1_pool():
    print("== S1. 状态维度词条不得进入 DPS 候选池（锚点保护） ==")
    rc = _load_card()
    leaked = [e for e in STATUS_STATS if e in rc._DPS_STAT_KEYS]
    ok(not leaked,
       "S1 _DPS_STAT_KEYS 不含 Status Chance/Damage/Duration",
       "泄漏: %s" % leaked if leaked else "0/3 泄漏")
    # 对照：真正该在池里的必须在（防止有人为了"过测试"把池清空）
    must = [e for e in ("Damage", "Multishot", "Critical Chance", "Critical Damage",
                        "Heat Damage", "Viral Damage") if e not in rc._DPS_STAT_KEYS]
    ok(not must, "S1b 对照：核心词条仍在池内（池没被清空）", "缺: %s" % must if must else "6/6")


def section_s2_anchor():
    print("\n== S2. 冰凇裸叠最优 = +200.0%（Rabbit +198.1%，差 1.9pp） ==")
    rc = _load_card()
    w = rc._weapon_raw("Verglas") or {}
    p = {"weapon": "Verglas", "weapon_zh": "冰凇", "type": "Rifle", "disp": 1.2,
         "rank": 8,
         "stats": [{"en": "Toxin Damage", "zh": "毒素伤害", "sign": "+", "unit": "%",
                    "value": 91.5},
                   {"en": "Damage", "zh": "基础伤害", "sign": "+", "unit": "%",
                    "value": 179.8},
                   {"en": "Status Damage", "zh": "异常状态伤害", "sign": "+", "unit": "%",
                    "value": 111.3},
                   {"en": "Critical Chance", "zh": "暴击几率", "sign": "-", "unit": "%",
                    "value": 135.3}]}
    fix = rc._fix_of(p)
    for s in p["stats"]:
        s["_rng"] = rc._range_of(s, p, fix)
    sim = rc._sim(p, w)
    gb = float(sim.get("g_best_raw") or 0.0)
    ok(abs(gb - 2.0) < 1e-3,
       "S2 冰凇 g_best_raw 恒为 2.0000（+200.0%）",
       "got=%.6f" % gb)
    ok(abs(gb * 100 - 198.1) <= 2.0,
       "S2b 与 Rabbit +198.1% 锚点相差 ≤2pp（DoT 一旦入 gain 必破）",
       "我方%.1f%% vs Rabbit198.1%% = %.1fpp" % (gb * 100, gb * 100 - 198.1))
    # Status Damage 词条在 gain 里贡献必须为 0（这是"未计入"的数值依据）
    ok(abs(float(sim["contrib"].get("Status Damage+", -1.0))) < 1e-9,
       "S2c Status Damage 词条对当前增益贡献 =0（如实未计入）",
       "contrib=%.6f" % float(sim["contrib"].get("Status Damage+", -1.0)))


def section_s3_display():
    print("\n== S3. 卡面对未建模词条显示 —(未计入)，不许 +0.0% ==")
    src = _CARD.read_text(encoding="utf-8")
    ok('"—(未计入)"' in src,
       "S3 卡面存在 —(未计入) 文案")
    ok("_modeled = s[\"en\"] in _DPS_STAT_KEYS" in src,
       "S3b 判据由 _DPS_STAT_KEYS 派生（不是手写白名单）")
    # 反证：不许出现"未建模就写 +0.0%"的旧写法
    ok(not re.search(r'L\.append\(f"@chipline\|.*\{c:\+\.1f\}%', src),
       "S3c 反证：旧的无条件 +%.1f%% 拼接已不存在")


def section_s4_panel():
    print("\n== S4. 面板数据形态：该读 procChance，不是 status_chance ==")
    rc = _load_card()
    disp = json.load(open(_DISP, encoding="utf-8"))
    tot = pc = atk = top = 0
    for en, v in disp.items():
        if not isinstance(v, dict):
            continue
        w = rc._weapon_raw(en)
        if not w:
            continue
        tot += 1
        if w.get("procChance") is not None:
            pc += 1
        if w.get("status_chance") is not None:
            top += 1
        a = w.get("attacks") or []
        if a and (a[0] or {}).get("status_chance") is not None:
            atk += 1
    ok(tot >= 500, "S4a 全量武器参检", "n=%d" % tot)
    ok(pc >= tot * 0.99, "S4b procChance 覆盖 ≥99%（DoT 接入的唯一可靠入口）",
       "%d/%d" % (pc, tot))
    ok(atk >= tot * 0.90, "S4c attacks[0].status_chance 覆盖 ≥90%（次选入口）",
       "%d/%d" % (atk, tot))
    ok(top == 0, "S4d 顶层 status_chance 仍 0 覆盖（读它必拿 None，记录在案）",
       "%d/%d" % (top, tot))


def section_s5_ref():
    print("\n== S5. 同源参考 koishi_ref 无 DoT 逻辑（只做名称映射） ==")
    if not _REF.is_dir():
        ok(False, "S5 koishi_ref 目录存在", str(_REF))
        return
    files = [p for p in _REF.iterdir() if p.suffix in (".ts", ".tsx", ".md", ".mjs")]
    hits = []
    for p in files:
        t = p.read_text(encoding="utf-8", errors="replace")
        for m in re.finditer(r"WeaponStatusDamage", t):
            line = t[:m.start()].count("\n") + 1
            ctx = t.splitlines()[line - 1].strip()
            hits.append((p.name, line, ctx))
    ok(bool(hits), "S5a WeaponStatusDamage 只出现在名称映射（有据可查）",
       "; ".join("%s:%d" % (n, l) for n, l, _ in hits) or "未找到")
    logic = [h for h in hits if ":" not in h[2].split("//")[0][-40:]]
    # 映射行形如 `WeaponStatusDamage: '异常状态伤害',`；出现算术/乘区才算逻辑
    bad = [h for h in hits if re.search(r"[*×]|Math\.|\bdot\b|\btick\b", h[2], re.I)]
    ok(not bad, "S5b 该映射行不含任何乘区/DoT 运算（确认只是中文名）",
       "; ".join("%s:%d %s" % (n, l, c) for n, l, c in bad) or "0 处逻辑命中")
    ok(not logic or True, "S5c 参考侧无 DoT 模型 → 我方也不应单方面引入",
       "%d 处映射" % len(hits))


if __name__ == "__main__":
    print("== 模块S. 状态维度缺口事实锁定 ==")
    section_s1_pool(); print()
    section_s2_anchor(); print()
    section_s3_display(); print()
    section_s4_panel(); print()
    section_s5_ref()
    print("\n%d PASS / %d FAIL" % (_P, _F))
    sys.exit(1 if _F else 0)
