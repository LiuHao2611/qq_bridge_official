# -*- coding: utf-8 -*-
"""斗技三阶 · 四槽上阵内核（纯函数，无副作用，不依赖 nonebot）。

设计（用户 2026-11-14 定）：
  * 入道随机宗门 → 该宗门斗技分 **普通 / 高阶 / 特殊** 三阶，随修为解锁。
  * 上阵只允许 **四槽**，三阶可任意搭配（后期可 4 特殊）。
  * 战斗/掠夺每回合在「已装备且斗气足够」的技里**随机释放**；都放不出 → 武器普攻。
  * 每阶斗气消耗不同（占 mp_max 比例）。

本模块只做「技能目录 / 解锁 / 四槽 / 斗气单价」的纯计算；
实际扣血与随机释放由 core.fight / core.duel 调用本模块完成。
"""
from __future__ import annotations

from . import config as cfg
from . import factions as fac
from . import flame_skills as fs
from . import skill_book as book
from .skill_book_schema import learn_floor


# ---------------------------------------------------------------------------
# 目录派生
# ---------------------------------------------------------------------------
def _skill_name(raw: str) -> str:
    """从一条斗技描述里取展示名（仅在 skill_book 无该宗门时的兜底路径使用）。

    例："六合游身尺（地阶中，萧厉）[C1]" -> "六合游身尺"
    """
    s = str(raw).split("[")[0].strip()
    for sep in ("（", "("):
        s = s.split(sep)[0]
    s = s.split("/")[0].strip()
    return s


def _rank_tier(raw: str) -> str:
    """地/天阶 -> 高阶；其余（黄/玄/未标）-> 普通。"""
    if any(t in raw for t in ("天阶", "地阶")):
        return "高阶"
    return "普通"


def _rank_hint(raw: str) -> str:
    """兜底路径：从宗门资料的括号描述里取阶位首字（玄/地/天），供境界闸判定。"""
    s = str(raw or "")
    for head in ("玄阶", "地阶", "天阶"):
        if head in s:
            return head
    return ""


def _to_entry(rec: dict, fid: str, origin: str) -> dict:
    """skill_book 记录 → 运行时斗技条目（唯一转换入口，本宗/通用共用）。"""
    tier = rec["tier"]
    novel = str(rec.get("novel") or "")
    source = str(rec.get("source") or "")
    return {
        "name": rec["name"],
        "tier": tier,
        "faction": fid,
        "origin": origin,                 # "本宗" / "通用" / "异火"
        "mp": round(rec["mp"] / 100.0, 4),
        "dmg": rec["dmg"] / 100.0,
        "unlock": int(rec.get("unlock") or cfg.SKILL_TIER_UNLOCK[tier]),
        "desc": rec["desc"],
        "brief": rec.get("brief", ""),
        "rank": rec["rank"],
        "kind": rec["kind"],
        "scope": rec["scope"],
        "source": source,
        "novel": novel,
        "cd": rec["cd"],
        # 2026-11-15 多维度：主定位 / 效果原子 / 门控代价，供卡片与战斗共用。
        # 用 .get 兜底，兼容 skill_book 里尚未填这三门的老记录（其余 160+ 门）。
        "role": rec.get("role", ""),
        "effects": list(rec.get("effects") or []),
        "gate": rec.get("gate", ""),
        # 「游戏自设」= 原著无据的战斗技，卡片要显式标注，不许冒充原著斗技
        "core": ("游戏自设" in source) or ("原著无载" in novel),
    }


def _from_book(fid: str) -> list[dict]:
    """从 skill_book（唯一权威表）派生某个宗门的**本宗专属**斗技。"""
    return [_to_entry(rec, fid, "本宗") for rec in book.skills_of(fid)]


def _from_faction_strings(fid: str) -> list[dict]:
    """兜底：skill_book 尚未收录该宗门时，退回 factions.py 的字符串解析。"""
    f = fac.FACTIONS.get(fid)
    if not f:
        return []
    out: list[dict] = []
    seen: set[str] = set()
    for raw in f.get("skills", []):
        name = _skill_name(raw)
        # 兜底路径也必须拦住典故：factions.py 的 skills 是「宗门资料」，
        # 里面仍写着炼药/考核/丹会，绝不能因为某天 FACTION_SKILLS 缺键就把它们放回斗技池。
        if name in book.NON_SKILL_NAMES:
            continue
        if not name or name in seen:
            continue
        core = "[游戏核心]" in raw
        tier = "普通" if core else _rank_tier(raw)
        dmg = cfg.SKILL_BY_NAME.get(name, {}).get("dmg_mult", cfg.SKILL_TIER_DMG[tier])
        out.append({
            "name": name, "tier": tier, "faction": fid, "origin": "本宗",
            "mp": cfg.SKILL_TIER_MP_FRAC[tier],
            "dmg": dmg,
            # 兜底路径也要过境界闸：功法/玄阶以上须斗者，黄阶按段位（默认 1）。
            # 阶位从宗门资料的括号描述里解析（"六合游身尺（地阶中，萧厉）[C1]"）。
            "unlock": max(cfg.SKILL_TIER_UNLOCK[tier],
                          learn_floor(_rank_hint(raw), "")),
            "desc": raw, "brief": _skill_name(raw), "core": core,
        })
        seen.add(name)
    for sp in f.get("special_skills", []):
        name = sp.get("name")
        if name in book.NON_SKILL_NAMES:
            continue
        if not name or name in seen:
            continue
        out.append({
            "name": name, "tier": "特殊", "faction": fid, "origin": "本宗",
            "mp": cfg.SKILL_TIER_MP_FRAC["特殊"],
            "dmg": cfg.SKILL_TIER_DMG["特殊"],
            "unlock": max(int(sp.get("unlock_lv", cfg.SKILL_TIER_UNLOCK["特殊"])),
                          learn_floor(sp.get("rank", ""), "")),
            "desc": sp.get("note", ""), "brief": name,
        })
        seen.add(name)
    return out


def catalog(fid: str) -> list[dict]:
    """某个宗门全部可学斗技 = 本宗专属斗技 + 通用基础斗技。

    2026-11-15 梳理：炼药师公会这类宗门原著里只有 1 门本宗斗技（千焰剑罡，
    古河在「战古河」施展），剥离炼药/考核/丹会后不能靠塞假斗技凑四槽；
    改用 ``skill_book.COMMON_SKILLS`` 补齐，并标 ``origin="通用"``，卡片与文本
    都明说是通用而非本宗传承。

    ⚠️ 2026-11-15 修正：通用池**只准放斗技堂的黄阶基础技**。旧版把 8 门有明确
    授受对象的专属斗技（药尘授萧炎的焰分噬浪尺、云韵赠的爆步、萧薰儿的燕返击…）
    也塞了进来，于是全量注入后 25 派人人会萧炎的尺法。这些技仍留在各自宗门的
    ``FACTION_SKILLS`` 里，数据不丢，只是不再跨宗门泛滥。
    """
    fid = str(fid or "")
    base = _from_book(fid) if fid in book.FACTION_SKILLS else _from_faction_strings(fid)
    seen = {e["name"] for e in base}
    common = [_to_entry(rec, "通用", "通用")
              for rec in book.common_entries() if rec["name"] not in seen]
    return base + common



def entries_for(p) -> list[dict]:
    """玩家技能目录：本宗斗技 + 已收服异火的专属主动技。

    2026-11-15：异火技从这里进池，四槽 / 斗技列表 / 斗技详情 / 战斗选招
    全部自动支持，不必改 skill_book 的 185 条大表。
    """
    base = catalog(str(p.get("faction") or ""))
    extra = fs.entries_for(p)
    if not extra:
        return base
    seen = {e["name"] for e in base}
    return base + [e for e in extra if e["name"] not in seen]


def learnable(p) -> list[dict]:
    """当前修为已解锁的斗技（unlock_lv <= 玩家等级）。

    注意：这是「可领悟的候选池」，不是「已会的技」。入道玩家只从中学 1 门，
    其余靠「学斗技」逐个领悟（2026-11-16）。异火主动技收服即会，不算领悟池。
    """
    lv = int(p.get("lv", 1))
    return [e for e in entries_for(p) if int(e["unlock"]) <= lv]


def is_legacy(p) -> bool:
    """老存档（无 learned 字段）：视为全部已学，保持旧玩法不削弱老玩家。"""
    return not isinstance(p.get("learned"), list)


def learned_names(p) -> list[str]:
    """已领悟技名。新档读 learned 字段；老档返回全部可学技（兼容）。"""
    raw = p.get("learned")
    if isinstance(raw, list):
        return [str(x) for x in raw if str(x).strip()]
    return [e["name"] for e in learnable(p)]


def flame_skill_names(p) -> set[str]:
    """已收服异火的主动技名（收服即会，不占领悟、不随转宗丢失）。"""
    return {e["name"] for e in fs.entries_for(p)}


def equippable_names(p) -> set[str]:
    """允许上阵的技名 = 已领悟 ∪ 已收服异火的主动技（收服即会，不占领悟）。"""
    out = set(learned_names(p))
    out |= flame_skill_names(p)
    return out


def _origin_of(name: str, fid: str) -> str:
    """已学技的来源标记：本宗 / 他宗 / 通用 / 异火。

    转宗保留全部已学技（2026-11-24 BUG 修复）后，玩家的 learned 里可能混着
    多个宗门的专属技——这里按 skill_book 归属判定，供「斗技」口令区分展示。
    """
    if any(str((rec.get("active") or {}).get("name") or "") == name
           for rec in fs.FLAME_SKILLS.values()):
        return "异火"
    if name in set(book.COMMON_SKILLS):
        return "通用"
    for f, names in book.FACTION_SKILLS.items():
        if name in names:
            return "本宗" if f == fid else "他宗"
    return "本宗"


def learned_entries(p) -> list[dict]:
    """玩家**所有已领悟斗技**的完整条目（跨宗），供「斗技」口令展示。

    与 ``entries_for(p)``（本宗池 + 异火技）不同：本函数严格按 ``p["learned"]``
    逐名还原，因此转宗后保留的老宗门专属技也能看到——这是 2026-11-24 用户
    「学习的斗技转宗不该被舍弃」的展示侧配套。每条带 origin（本宗/他宗/
    通用/异火）、unlocked/equipped 标记，字段与 skill_pool 的条目同形，
    卡片/文本共用同一口径。老档（无 learned）= 全部可学技，保持兼容。
    """
    lo = set(ensure_loadout(p))
    fid = str(p.get("faction") or "")
    out: list[dict] = []
    seen: set[str] = set()
    for name in learned_names(p):
        if name in seen:
            continue
        seen.add(name)
        rec = book.get(name)
        if rec is not None:
            e = _to_entry(rec, fid, _origin_of(name, fid))
            e["unlocked"] = True          # 已领悟必然已解锁
            e["equipped"] = name in lo
            out.append(e)
            continue
        # 不在 SKILL_BOOK：可能是异火主动技或老档派生技，从异火池找同形条目
        hit = next((x for x in fs.entries_for(p) if x["name"] == name), None)
        if hit:
            e = dict(hit)
            e["origin"] = "异火"      # 同口径：展示层按 origin 分组，缺了会重复
            e["unlocked"] = True
            e["equipped"] = name in lo
            out.append(e)
    # 已收服异火的主动技：收服即会，即使不在 learned 列表也要展示（与旧 cmd_skills 一致）
    for e in fs.entries_for(p):
        if e["name"] not in seen:
            d = dict(e)
            # 统一来源口径：flame_skills.entries_for 的 entry 只带 faction="异火"，
            # 没有 origin 字段。不补的话展示层按 origin 分组会漏判，同一门异火技
            # 既进「特殊」又进「异火技」两次（test_flame_skills 抓到的重复）。
            d["origin"] = "异火"
            d["unlocked"] = True
            d["equipped"] = e["name"] in lo
            out.append(d)
            seen.add(e["name"])
    return out


# ---------------------------------------------------------------------------
# 四槽
# ---------------------------------------------------------------------------
def _default_pick(pool: list[dict]) -> list[str]:
    """默认四槽：优先 2 普通 + 2 高阶（贴合「前期普通+高阶搭配」），不足再补特殊/其余。"""
    common = [e for e in pool if e["tier"] == "普通"]
    high = [e for e in pool if e["tier"] == "高阶"]
    spec = [e for e in pool if e["tier"] == "特殊"]
    pick = common[:2] + high[:2]
    if len(pick) < cfg.LOADOUT_SIZE:
        rest = [e for e in (common[2:] + high[2:] + spec) if e not in pick]
        pick += rest
    return [e["name"] for e in pick[:cfg.LOADOUT_SIZE]]


def ensure_loadout(p) -> list[str]:
    """把 p["loadout"] 规范成 ≤4 个「已领悟且已解锁」的技能名。

    2026-11-16 改（用户「入道只给一个低阶技，后面逐一学习」）：
      * 新档（有 learned 字段）：只允许**已领悟**的技留在四槽，且**不自动补满**——
        没领悟的技不能凭空上阵，必须玩家「学斗技」领悟后再「上阵」。
      * 老档（无 learned 字段）：保持原行为（全部可学技都可上阵、不足自动补满），
        不削弱老玩家已有进度。
    """
    pool = learnable(p)
    names = [e["name"] for e in pool]
    if is_legacy(p):
        cur = list(dict.fromkeys(n for n in (p.get("loadout") or []) if n in names))
        if len(cur) < cfg.LOADOUT_SIZE and names:
            cur += [n for n in names if n not in cur][:cfg.LOADOUT_SIZE - len(cur)]
        p["loadout"] = cur[:cfg.LOADOUT_SIZE]
        return p["loadout"]

    # 2026-11-26 用户实测矛盾：斗技卡列得出他宗已学技、还给了上阵序号，
    # 但四槽这里旧代码还要求 `n in names`（本宗 learnable）→ 上阵后被静默丢弃。
    # 允许集合与卡片/equippable_names 同源：已领悟（含转宗保留的他宗技）∪ 异火技。
    # 不能调 learned_entries() 取名字——那个函数会调本函数取 equipped，会递归。
    allowed = equippable_names(p)
    cur = list(dict.fromkeys(n for n in (p.get("loadout") or []) if n in allowed))
    p["loadout"] = cur[:cfg.LOADOUT_SIZE]
    return p["loadout"]


def loadout_entries(p) -> list[dict]:
    """已上阵四槽（或不足四个）的完整技能对象。

    2026-11-26：条目来源改为 ``learned_entries``（跨宗已领悟 ∪ 收服即会异火技）。
    旧版只从 ``entries_for``（本宗池 + 异火）取，转宗保留的他宗技即便已在四槽
    也会在战斗选招时**静默消失**（卡片却显示已上阵）——展示/战斗前后矛盾。
    """
    by = {e["name"]: e for e in learned_entries(p)}
    out = []
    for n in ensure_loadout(p):
        if n in by:
            out.append(by[n])
    return out


# ---------------------------------------------------------------------------
# 斗气单价 / 伤害倍率
# ---------------------------------------------------------------------------
def tier_cost(tier: str, mp_max_val: int) -> int:
    """某一阶斗技的斗气消耗（占 mp_max 比例的绝对值，至少 1）。

    兼容旧签名：按恒定基础占比算（= 斗之气档）。战斗/卡面请用
    :func:`tier_cost_by_lv`，它会按玩家境界递减消耗。
    """
    frac = cfg.SKILL_TIER_MP_FRAC.get(tier, 0.20)
    return max(1, int(round(int(mp_max_val) * frac)))


def tier_cost_by_lv(tier: str, mp_max_val: int, lv: int) -> int:
    """按玩家境界计算的斗技消耗（2026-11-26 唯一战斗/卡面真源）。

    境界越高，同阶斗技消耗占比越低——练级涨的斗气上限才有实感
    （每战可放次数随修为增长）。占比来自 ``cfg.skill_mp_frac``。
    """
    frac = cfg.skill_mp_frac(tier, lv)
    return max(1, int(round(int(mp_max_val) * frac)))


def skill_mult(entry: dict, lv: int) -> float:
    """技能倍率：阶基础倍率 ×（每级 +20%）。"""
    return float(entry["dmg"]) * (1 + 0.2 * (int(lv) - 1))
