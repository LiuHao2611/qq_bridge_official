# -*- coding: utf-8 -*-
"""生成《斗技与效果总表》Markdown（2026-11-26，用户「把所有斗技和效果列表给我」）。

数据源全部是运行时的唯一真源，**不手抄、不编造**：
  * ``skill_book.SKILL_BOOK``       —— 150 门斗技（阶 / 玄地阶 / 类型 / 伤害 / 冷却 /
                                      解锁 / 定位 / 效果原子 / 门控代价 / 原著出处）
  * ``skill_book.COMMON_SKILLS``    —— 4 门通用基础斗技（斗技堂货，任何宗门都能补槽）
  * ``skill_book.FACTION_SKILLS``   —— 宗门 → 斗技归属
  * ``flame_skills.FLAME_SKILLS``   —— 5 朵异火的主动 / 被动
  * ``skill_effects``               —— 16 种效果原子 / 5 种条件 / 8 种定位
  * ``config``                      —— 三阶基线、斗气占比（随境界递减）、倍率公式

跑法：python tools/gen_skill_matrix.py [输出路径]
"""
from __future__ import annotations

import sys
import types
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src" / "plugins"))

# doupo/__init__.py 顶层注册 nonebot matcher，本脚本只要纯数据，做最小桩。
_nb = types.ModuleType("nonebot")
_nb.get_driver = lambda: types.SimpleNamespace(on_startup=lambda f: f)


def _matcher(*a, **k):
    m = types.SimpleNamespace()
    m.handle = lambda *a, **k: (lambda f: f)
    m.finish = lambda *a, **k: None
    return m


_nb.on_regex = _matcher
_nb.on_message = _matcher
_nb.on_notice = _matcher
_nb.on_startup = _matcher
sys.modules["nonebot"] = _nb
for _n in ("nonebot.adapters", "nonebot.adapters.onebot", "nonebot.adapters.onebot.v11"):
    sys.modules[_n] = types.ModuleType(_n)
_v11 = sys.modules["nonebot.adapters.onebot.v11"]
_v11.MessageEvent = object
_v11.MessageSegment = object
_v11.Message = object

from doupo import config as cfg          # noqa: E402
from doupo import factions as fac        # noqa: E402
from doupo import flame_skills as fs     # noqa: E402
from doupo import skill_book as book     # noqa: E402
from doupo import skill_effects as se    # noqa: E402

TIERS = ("普通", "高阶", "特殊")
SAMPLE_LV = (1, 11, 21, 31, 41, 51, 61, 73, 85, 94, 103)


def effect_text(rec: dict) -> str:
    """效果原子 → 一行中文（与卡片 chip 同源 skill_effects.effect_chips）。"""
    try:
        chips = se.effect_chips(rec.get("effects") or [])
    except Exception:  # noqa: BLE001 — 老记录缺字段时退回原始码
        return "／".join(str(e.get("t")) for e in (rec.get("effects") or []))
    return "／".join(c.get("text") or c.get("label", "") for c in chips) or "—"


def esc(s) -> str:
    """表格里不许出现裸竖线（会截断列）。"""
    return str(s if s is not None else "").replace("|", "｜").replace("\n", " ").strip()


def skill_table(names, title) -> list[str]:
    out = [f"### {title}", ""]
    out.append("| 斗技 | 阶 | 原著阶别 | 类型 | 定位 | 基础倍率 | 冷却 | 解锁 | 效果 | 一句话 |")
    out.append("|---|---|---|---|---|---|---|---|---|---|")
    for n in names:
        r = book.SKILL_BOOK.get(n)
        if not r:
            continue
        out.append("| %s | %s | %s | %s | %s | %d%% | %d 回合 | lv%d | %s | %s |" % (
            esc(r["name"]), r["tier"], esc(r.get("rank", "")), esc(r.get("kind", "")),
            esc(r.get("role", "")) or "—", int(r["dmg"]), int(r.get("cd", 0) or 0),
            int(r.get("unlock", 1)), esc(effect_text(r)), esc(r.get("brief", "")),
        ))
    out.append("")
    return out


def build() -> str:
    total = len(book.SKILL_BOOK)
    by_tier = {t: sum(1 for r in book.SKILL_BOOK.values() if r["tier"] == t) for t in TIERS}
    md: list[str] = []
    md.append("# 斗破苍穹 · 斗技与效果总表")
    md.append("")
    md.append("> 生成时间：%s　｜　数据源：`skill_book.SKILL_BOOK` / `flame_skills` / "
              "`skill_effects` / `config`（运行时真值，非手抄）" % date.today().isoformat())
    md.append(">")
    md.append("> 复现：`python tools/gen_skill_matrix.py`")
    md.append("")
    md.append("## 一、总览")
    md.append("")
    md.append("| 项 | 数量 |")
    md.append("|---|---|")
    md.append("| 斗技总数（不含异火技） | %d |" % total)
    md.append("| 　普通阶 | %d |" % by_tier["普通"])
    md.append("| 　高阶 | %d |" % by_tier["高阶"])
    md.append("| 　特殊阶 | %d |" % by_tier["特殊"])
    md.append("| 通用基础斗技（斗技堂货） | %d |" % len(book.COMMON_SKILLS))
    md.append("| 异火主动技 | %d |" % len(fs.FLAME_SKILLS))
    md.append("| 异火被动 | %d |" % len(fs.FLAME_SKILLS))
    md.append("| 宗门 / 势力 | %d |" % len(fac.FACTIONS))
    md.append("| 效果原子（effect） | %d |" % len(se.EFFECT_LABELS))
    md.append("| 上阵槽位 | %d（三阶任意搭配） |" % int(cfg.LOADOUT_SIZE))
    md.append("")
    md.append("## 二、规则口径")
    md.append("")
    md.append("### 2.1 三阶基线")
    md.append("")
    md.append("| 阶 | 基础倍率 | 斗气占比（斗之气档） | 解锁 | 说明 |")
    md.append("|---|---|---|---|---|")
    md.append("| 普通 | %d%% | %d%% · 气海 | lv%d | 黄/玄阶，斗技堂与各宗基础技 |"
              % (int(cfg.SKILL_TIER_DMG["普通"] * 100),
                 int(cfg.SKILL_TIER_MP_FRAC["普通"] * 100), cfg.SKILL_TIER_UNLOCK["普通"]))
    md.append("| 高阶 | %d%% | %d%% · 气海 | lv%d | 地/天阶，宗门核心传承 |"
              % (int(cfg.SKILL_TIER_DMG["高阶"] * 100),
                 int(cfg.SKILL_TIER_MP_FRAC["高阶"] * 100), cfg.SKILL_TIER_UNLOCK["高阶"]))
    md.append("| 特殊 | %d%% | %d%% · 气海 | lv%d | 秘法 / 异火技 |"
              % (int(cfg.SKILL_TIER_DMG["特殊"] * 100),
                 int(cfg.SKILL_TIER_MP_FRAC["特殊"] * 100), cfg.SKILL_TIER_UNLOCK["特殊"]))
    md.append("")
    md.append("实际倍率 = **阶基础倍率 ×（1 + 0.2 ×（技能等级 − 1））**，"
              "升级每级 +20%%，最高 5 级（材料：%s）。" % cfg.SKILL_UP_ITEM)
    md.append("")
    md.append("### 2.2 斗气消耗随境界递减（2026-11-26 起）")
    md.append("")
    md.append("同一门斗技，境界越高占比越低 —— 练级涨的斗气上限才有实感"
              "（每战能放的次数随修为增长，战斗回合数不变）。")
    md.append("")
    md.append("| 等级 | 境界 | 普通 | 高阶 | 特殊 |")
    md.append("|---|---|---|---|---|")
    for lv in SAMPLE_LV:
        md.append("| lv%d | %s | %d%% | %d%% | %d%% |" % (
            lv, esc(cfg.full_realm(lv)),
            int(round(cfg.skill_mp_frac("普通", lv) * 100)),
            int(round(cfg.skill_mp_frac("高阶", lv) * 100)),
            int(round(cfg.skill_mp_frac("特殊", lv) * 100)),
        ))
    md.append("")
    md.append("### 2.3 释放规则")
    md.append("")
    md.append("* 上阵 **%d 槽**，三阶任意搭配（后期可以 4 特殊）；" % int(cfg.LOADOUT_SIZE))
    md.append("* 每回合在「已上阵 + 斗气够 + 冷却已好」的技里**随机释放**一门；")
    md.append("* 一个都放不出 → 武器普攻（不空过回合）；")
    md.append("* 斗气跨战结转、不回满，所以高境界能连打更多场。")
    md.append("")

    md.append("## 三、效果一览")
    md.append("")
    md.append("### 3.1 效果原子（每门斗技的 `effects` 由这些代号组成）")
    md.append("")
    md.append("| 代号 | 名称 | 作用 |")
    md.append("|---|---|---|")
    eff_desc = {
        "dot": "每回合按伤害比例持续掉血（子类：血毒 / 流血 / 灼烧 / 中毒）",
        "lifesteal": "打出伤害的一部分回补自身气血",
        "pierce": "无视目标一部分防御",
        "debuff_atk": "降低目标攻击，持续若干回合",
        "debuff_evade": "压低目标闪避，越打越躲不开",
        "debuff_heal": "削减目标受疗效果（克回血）",
        "stun": "目标该回合无法出手",
        "silence": "只能普攻，放不出斗技",
        "root": "无法位移 / 脱离",
        "knockback": "打断对方节奏，拉开距离",
        "reflect": "把受到的一部分伤害弹回对手",
        "reduce": "自身受到的伤害按比例降低",
        "evade": "提高躲避判定",
        "chain": "同一回合追加段数",
        "cond": "满足条件（恐惧/守势/火势/位移后/残血）时额外增伤",
        "empower": "自身攻击等属性按比例提升",
    }
    for code, label in se.EFFECT_LABELS.items():
        md.append("| `%s` | %s | %s |" % (code, label, eff_desc.get(code, "")))
    md.append("")
    md.append("### 3.2 条件加伤（`cond` 的触发条件）")
    md.append("")
    md.append("| 代号 | 中文 |")
    md.append("|---|---|")
    for k, v in se.COND_LABELS.items():
        md.append("| `%s` | %s |" % (k, v))
    md.append("")
    md.append("### 3.3 主定位（`role`）")
    md.append("")
    md.append("　" + "　·　".join(se.ROLES))
    md.append("")
    md.append("### 3.4 持续伤害子类")
    md.append("")
    md.append("　" + "　·　".join(se.DOT_KINDS))
    md.append("")

    md.append("## 四、宗门斗技全表")
    md.append("")
    for fid, f in fac.FACTIONS.items():
        names = [n for n in book.FACTION_SKILLS.get(fid, ()) if n in book.SKILL_BOOK]
        if not names:
            continue
        md.extend(skill_table(names, "%s（%d 门）" % (f.get("name", fid), len(names))))

    md.append("## 五、通用基础斗技（任何宗门都能补满四槽）")
    md.append("")
    md.extend(skill_table([n for n in book.COMMON_SKILLS if n in book.SKILL_BOOK], "通用池"))

    unattached = [n for n in book.UNATTACHED_SKILLS if n in book.SKILL_BOOK]
    if unattached:
        md.append("## 六、未归属宗门的可考斗技")
        md.append("")
        md.append("> 原著里属个人 / 世家，25 派中无对应宗门；保留在权威表并单列，"
                  "绝不硬塞给别派冒充传承。")
        md.append("")
        md.extend(skill_table(unattached, "未归属"))

    md.append("## 七、异火技（收服即解锁，占「特殊」阶）")
    md.append("")
    md.append("| 异火 | 榜位 | 主动技 | 类型 / 范围 | 伤害 | 被动 | 被动效果 |")
    md.append("|---|---|---|---|---|---|---|")
    for name, d in fs.FLAME_SKILLS.items():
        a, p = d["active"], d["passive"]
        md.append("| %s | 第 %d | %s | %s / %s | %d%% | %s | %s |" % (
            esc(name), fs.RANK_NO.get(name, 0), esc(a["name"]),
            esc(a.get("kind", "")), esc(a.get("scope", "")),
            int(round(float(a["dmg"]) * 100)), esc(p["name"]), esc(p.get("effect", "")),
        ))
    md.append("")
    md.append("异火主动技斗气占比 %d%%·气海（同「特殊」阶），冷却 %d 回合。"
              % (int(fs.ACTIVE_MP * 100), fs.ACTIVE_CD))
    md.append("")

    no_eff = [n for n in book.NO_EFFECT_SKILLS if n in book.SKILL_BOOK]
    if no_eff:
        md.append("## 八、无独立效果原子的斗技（只有伤害 / 分段 / 定性描述）")
        md.append("")
        md.append("> 纪律：**禁止编造**——原著没有可量化机制就不硬塞效果，卡片照常展示"
                  "定位 / 代价 / 来源。")
        md.append("")
        for n in no_eff:
            md.append("* **%s**：%s" % (n, book.NO_EFFECT_REASONS.get(n, "")))
        md.append("")
    return "\n".join(md)


def main() -> int:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else (
        ROOT / "docs" / ("斗技与效果总表_%s.md" % date.today().isoformat()))
    out.parent.mkdir(parents=True, exist_ok=True)
    text = build()
    out.write_text(text, encoding="utf-8")
    print("已生成：%s（%d 行 / %d 字节）" % (out, text.count("\n") + 1, len(text.encode())))
    return 0


if __name__ == "__main__":
    sys.exit(main())
