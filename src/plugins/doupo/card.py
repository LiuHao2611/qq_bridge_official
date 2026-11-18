# -*- coding: utf-8 -*-
"""斗破苍穹·双帝之战 · 人物卡渲染。

把 player dict 渲染成「斗破大陆」图片卡片，供群聊「人物 / 属性 / 面板」口令使用。
**渲染失败必须降级为纯文本**（铁律：不允许因为图片挂了就丢消息或静默失败）。

为什么用 importlib 独立加载 pic 包，而不是 `from ..qq_bridge import pic`
------------------------------------------------------------------------
`qq_bridge/__init__.py` 会注册全部 matcher、初始化主插件链路（含 LLM 相关），
而 doupo 的单测（test_core / test_commands / test_e2e）刻意**不加载主插件**，
以保证测试隔离、不触发模型调用。直接 import 上级包会把那套副作用统统带进来。

所以这里用 spec_from_file_location 把 `qq_bridge/pic` 目录直接挂成一个独立包，
只取它的绘制能力（Canvas / fonts / icons / Theme / render），零副作用。
pic 包自身约定「不 import nonebot、不 import 上级包」，因此可安全独立加载。
"""
from __future__ import annotations

import asyncio
import importlib.util
import logging
import sys
from pathlib import Path

from . import battle_review as br
from . import config as cfg
from . import core
from . import commands
from . import avatar
from . import factions as fac
from . import skill_effects as fx

log = logging.getLogger("qq_bridge.doupo.card")

#: 卡片路由用的业务卡名（命中卡片级主题绑定后，主题编辑器可按此卡单独改色）
CARD_NAME = "斗破人物"

#: 菜单卡的独立卡名（与人物卡共享 bg_overrides["doupo"] 配色，但可在编辑器里单独改）
CARD_MENU_NAME = "斗破菜单"

#: 卡片宽度（QQ 聊天窗内 720 显示效果最稳，过宽会被压缩导致字小）
CARD_WIDTH = 720

_PKG_NAME = "_qq_bridge_pic_standalone"
_PIC = None
_PIC_FAILED = False


def _load_pic():
    """把 qq_bridge/pic 作为独立包加载并缓存；失败返回 None（永久降级纯文本）。"""
    global _PIC, _PIC_FAILED
    if _PIC is not None:
        return _PIC
    if _PIC_FAILED:
        return None
    if _PKG_NAME in sys.modules:
        _PIC = sys.modules[_PKG_NAME]
        return _PIC
    try:
        pic_dir = Path(__file__).resolve().parents[1] / "qq_bridge" / "pic"
        init = pic_dir / "__init__.py"
        if not init.is_file():
            raise FileNotFoundError("pic 包不存在: %s" % init)
        spec = importlib.util.spec_from_file_location(
            _PKG_NAME, str(init), submodule_search_locations=[str(pic_dir)]
        )
        if spec is None or spec.loader is None:
            raise ImportError("无法为 pic 包创建 spec")
        mod = importlib.util.module_from_spec(spec)
        # 先注册再 exec：pic 内部有相对 import，需要包已在 sys.modules 中
        sys.modules[_PKG_NAME] = mod
        spec.loader.exec_module(mod)
        _PIC = mod
        return mod
    except Exception as e:  # noqa: BLE001
        _PIC_FAILED = True
        sys.modules.pop(_PKG_NAME, None)
        log.warning("斗破卡片不可用（已降级为纯文本）：%s: %s", type(e).__name__, e)
        return None


def available() -> bool:
    """卡片能力是否可用（供自检 / 测试用；不影响正常流程）。"""
    return _load_pic() is not None


def build_data(p: dict) -> dict:
    """player dict → doupo 模板参数。

    与 commands.cmd_panel 平行但信息等价：它生成纯文本兜底，这里生成图片数据，
    两者读同一个 player，因此「图与文永远不会说的不是一回事」。
    """
    core.settle(p)  # 结算离线修为（幂等），保证图片里的修为是最新的
    st = core.stats(p)
    lv = int(p.get("lv", 1))
    need = cfg.exp_need(lv)
    exp = int(p.get("exp", 0))
    realm_max = lv >= cfg.MAX_LV

    alch = cfg.ALCHEMIST[int(p.get("alch", 0))]
    alch_name = "%s炼药师" % alch[0]   # alch[0] 为品阶名（如「二品」）
    alch_need = int(alch[1])
    alch_exp = int(p.get("alch_exp", 0))

    wv = core.weapon_panel(p)
    wname, wlv, wp = wv["name"], wv["level"], wv["stats"]

    return {
        "name": p.get("name") or "无名",
        "lv": lv,
        # 静态头像：从对应档位 GIF 首帧裁出，base64 传给模板画进卡片左侧。
        # 用户 2026-11-07 明确要求「不用放动态人物小人了，截图一个头像放进卡片里」。
        # 资源缺失时给空串，模板会画占位头像，卡片照常出图。
        "avatar_b64": avatar.headshot_for(p) or "",
        "avatar_caption": avatar.headshot_caption(lv),
        "realm": cfg.full_realm(lv),
        "exp": exp, "need": need, "realm_max": realm_max,
        "can_break": bool(core.can_level(p)),
        "alch_name": alch_name, "alch_exp": alch_exp, "alch_need": alch_need,
        "weapon_name": wname, "weapon_level": wlv, "weapon_stats": wp,
        "stats": st,
        # 六维的战斗作用（2026-11-16 用户诉求：卡上属性与战斗强关联，不能只摆数字）。
        # 与 commands 战报内联归因、core 实际计算公式三者同源，杜绝图文说两回事。
        "stat_roles": dict(cfg.STAT_BATTLE_ROLE),
        "hp": int(p.get("hp", 0)), "hp_max": int(st["体力"]),
        "mp": int(p.get("mp", 0)), "mp_max": core.mp_max(p),
        "gold": int(p.get("gold", 0)),
        "stone": core.count_item(p, "炎星石"),
        "power": core.power(p),
        "wins": int(p.get("wins", 0)), "losses": int(p.get("losses", 0)),
    }


async def render_panel(p: dict, tip: str | None = None) -> bytes | None:
    """渲染人物卡，返回 PNG bytes；失败返回 None（调用方降级纯文本）。

    :param tip: 覆盖卡片底部提示；None = 不额外附加。
    """
    pic = _load_pic()
    if pic is None or not p:
        return None
    data = build_data(p)
    if tip is not None:
        data["tip"] = tip
    # 首选：走完整主题链路（卡片绑定 > 自定义主题 > PIC_THEME > 内置）+ 磁盘缓存
    try:
        return await pic.render("doupo", card=CARD_NAME, width=CARD_WIDTH, data=data)
    except Exception as e:  # noqa: BLE001
        log.debug("斗破卡片主题链路渲染失败，尝试内置 light 兜底：%s", e)
    # 兜底：显式内置 light 主题，绕开主题解析（防止主题配置损坏导致整卡不可用）
    try:
        return await asyncio.to_thread(
            pic.render_sync, "doupo", "light", CARD_NAME, width=CARD_WIDTH, data=data
        )
    except Exception as e:  # noqa: BLE001
        log.warning("斗破卡片渲染失败（已降级为纯文本）：%s: %s", type(e).__name__, e)
        return None


def build_menu_data() -> dict:
    """口令菜单 → doupo_menu 模板参数。

    数据源是 commands.MENU_GROUPS（唯一源），与纯文本兜底同源，
    因此图片与文字永远不会「说的不是一回事」。
    """
    return {
        "title": "斗破菜单",
        "groups": [
            {"name": name, "rows": [[cmd, desc] for cmd, desc in rows]}
            for name, rows in commands.MENU_GROUPS
        ],
        "tip": f"共 {commands.menu_total()} 条口令 · 纯中文，菜单是唯一切换口",
    }


async def render_menu() -> bytes | None:
    """渲染口令菜单卡，返回 PNG bytes；失败返回 None（调用方降级纯文本）。"""
    pic = _load_pic()
    if pic is None:
        return None
    data = build_menu_data()
    # 首选：走完整主题链路（卡片绑定 > 自定义主题 > PIC_THEME > 内置）+ 磁盘缓存
    try:
        return await pic.render("doupo_menu", card=CARD_MENU_NAME, width=CARD_WIDTH, data=data)
    except Exception as e:  # noqa: BLE001
        log.debug("斗破菜单卡主题链路渲染失败，尝试内置 light 兜底：%s", e)
    # 兜底：显式内置 light 主题，绕开主题解析（防止主题配置损坏导致整卡不可用）
    try:
        return await asyncio.to_thread(
            pic.render_sync, "doupo_menu", "light", CARD_MENU_NAME, width=CARD_WIDTH, data=data
        )
    except Exception as e:  # noqa: BLE001
        log.warning("斗破菜单卡渲染失败（已降级为纯文本）：%s: %s", type(e).__name__, e)
        return None


# ---------------------------------------------------------------------------
# 通用列表卡（异火 / 宝库 / 背包 / 排行）
# 斗技自 2026-11-14 起走 doupo_skills 专用模板（描述/伤害/五段条/重点大字），
# 不再与上面四张共用 doupo_list。
# ---------------------------------------------------------------------------
async def _render_list(template: str, data: dict, card_name: str) -> bytes | None:
    """渲染任意斗破列表卡：主题链路 → 内置 light 兜底 → None（调用方回纯文本）。"""
    pic = _load_pic()
    if pic is None:
        return None
    try:
        return await pic.render(template, card=card_name, width=CARD_WIDTH, data=data)
    except Exception as e:  # noqa: BLE001
        log.debug("%s 主题链路渲染失败，尝试内置 light 兜底：%s", card_name, e)
    try:
        return await asyncio.to_thread(
            pic.render_sync, template, "light", card_name, width=CARD_WIDTH, data=data
        )
    except Exception as e:  # noqa: BLE001
        log.warning("%s 渲染失败（已降级为纯文本）：%s: %s", card_name, type(e).__name__, e)
        return None


def build_skills_data(p: dict) -> dict:
    """斗技 → doupo_skills 专用模板参数（完整技能描述 + 详细伤害口径）。

    伤害数字与 core.damage_schedule() / core._player_hit() 同源：
        raw = (力量×0.5 + 斗气×1.5) × cfg.skill_dmg_mult(name, lv)
        期望单击 = raw × (1 + 暴击率/100 × 0.5)   # 暴击 1.5 倍的期望
    这里展示的是**期望单击（未计目标防御）**，与战斗内实际单次命中的差别只有
    mitigation(目标防御) 与暴击随机，故不会图文说两回事。
    """
    sk = p.get("skills") or {}
    st = core.stats(p)
    raw_base = st["力量"] * 0.5 + st["斗气"] * 1.5
    crit_mult = 1.0 + core.crit_rate(p) / 100.0 * 0.5
    item = cfg.SKILL_UP_ITEM
    have = core.count_item(p, item)
    frac = cfg.SKILL_TIER_MP_FRAC

    pool = core.skill_pool(p)
    own = sum(1 for e in pool if e.get("origin") == "本宗")
    common = sum(1 for e in pool if e.get("origin") == "通用")
    rows = []
    for e in core.loadout_entries(p):
        name = e["name"]
        tier = e["tier"]
        mx = 5
        lv = max(1, min(mx, int(sk.get(name, 1))))
        maxed = lv >= mx
        base_mult = float(e["dmg"])
        cur_mult = base_mult * (1 + 0.2 * (lv - 1))
        next_mult = cur_mult if maxed else base_mult * (1 + 0.2 * lv)
        # 2026-11-15 斗技多维度重构：用户明确要求「技能描述、技能数值都展示出来，
        # 一个位移+伤害的技能要有闪避和少量伤害，而不是一个单纯数字」。
        # 因此这里给出结构化多维字段，由 doupo_skills 模板分行呈现：
        #   role   主定位（续航/控场/爆发/异常/防御/位移/功能/输出）
        #   chips  效果原子 [{label,value}]（吸血 25% / 破防 30% / 血毒 5%·2回合…）
        #   gate   门控/代价（起手慢、需落脚点、被控即散…）
        #   desc   完整原著描述（不再用 brief 一句带过）
        # 伤害数值仍与 core.damage_schedule 同源（下方 base/cur/dmg 不变）。
        rows.append({
            "badge": tier[0],
            "name": name,
            "lv": lv, "max": mx, "maxed": maxed,
            "rank": e.get("rank") or tier,
            "origin_mark": _origin_mark(e),
            "role": fx.role_label(e.get("role", "")),
            "chips": fx.effect_chips(e.get("effects") or []),
            "gate": str(e.get("gate") or ""),
            # 完整描述去掉末尾「限制：…」段——代价由 gate 行单独展示，
            # 否则同一件事在图里说两遍（用户实测指出重复）。
            "desc": (fx.desc_without_gate(str(e.get("desc") or ""))
                     or str(e.get("desc") or e.get("brief") or "")),
            "base_mult": round(base_mult, 2),
            "cur_mult": round(cur_mult, 2),
            "next_mult": round(next_mult, 2),
            "dmg": int(round(raw_base * cur_mult * crit_mult)),
            "next_dmg": int(round(raw_base * next_mult * crit_mult)),
            "segments": mx, "lit": lv,
            "item": item,
            "item_need": 0 if maxed else cfg.skill_up_cost(lv),
        })

    fac_name = p.get("faction_name") or "无宗门"
    return {
        "icon": "bolt", "title": "斗技",
        "subtitle": (f"{fac_name} · 本宗 {own} 门 / 通用 {common} 门 · "
                     f"四槽上阵（{len(rows)}/{cfg.LOADOUT_SIZE}）"),
        "item": item, "item_count": have,
        "formula": "伤害口径：(力量×0.5 + 斗气×1.5) × 阶倍率 ×(1+0.2/级) × 暴击期望（未计目标防御）",
        "rows": rows,
        "tip": (f"通用=斗气大陆流通的基础斗技，非本宗传承 ｜ 上阵 <4技名> ｜ "
                f"现有 {item} ×{have}"),
    }


def _origin_mark(e: dict) -> str:
    """斗技来源标记：一眼分清本宗传承 / 通用基础 / 异火技 / 游戏自设。"""
    origin = e.get("origin") or "本宗"
    mark = {"本宗": "〔本宗〕", "通用": "〔通用〕", "异火": "〔异火〕"}.get(origin, f"〔{origin}〕")
    if e.get("core"):
        mark += "〔自设〕"
    return mark


def build_roster_data(p: dict) -> dict:
    """本宗斗技名录 → doupo_roster 专用名录卡参数。

    与 render_skills 的区别：那张是「四槽上阵专用卡」，只画已上阵项；
    这张画**本宗全部斗技**（含未解锁、已收服异火的主动技），用于「斗技 全部」。
    基本固定的名录内容必须出卡片，不能拿纯文本糊弄。

    2026-11-15 第二次视觉重做（用户：「优化这个卡片，怎么没优化」）
    ------------------------------------------------------------
    上一版只是把多维信息塞进通用列表卡的行里，用户仍觉得没优化 —— 因为
    「上阵/可学」用了和技能名一样的 24px 粗体、23 行无分组无分隔、效果与代价
    挤成 30 多字一行。这版按阶位**分组**输出，每门给出结构化字段，由
    ``doupo_roster`` 模板画成独立卡片块：

        name/role/state/state_kind/meta/chips/gate

    ``chips`` 第一项固定是伤害倍率（让数值可比），其后才是各维度效果 ——
    这样玩家看到的是「一个数值 + 多个维度」，而不是「一个数字比大小」。
    ``rows`` 仍保留扁平结构，供纯文本兜底与既有测试消费。
    """
    sk = p.get("skills") or {}
    # 序号必须与「上阵 1 3 5」的数字口径严格同源——统一走 core.skill_pool_sorted
    # （三阶稳定排序），不再在此另写一份 sorted，避免卡片序号与上阵序号错位。
    pool_sorted = core.skill_pool_sorted(p)

    rows: list[dict] = []
    by_tier: dict[str, list[dict]] = {}
    for no, e in enumerate(pool_sorted, 1):
        name = e["name"]
        unlocked = bool(e.get("unlocked"))
        equipped = bool(e.get("equipped"))
        # 两列版的状态 pill 只有 ~52px 宽，「未解锁 lv50」会撑爆块宽，
        # 因此状态只写短词，解锁等级挪到副行末尾（信息不丢、胶囊不挤）。
        if equipped:
            state, state_kind = "上阵", "on"
        elif unlocked:
            state, state_kind = "可学", "learn"
        else:
            state, state_kind = "未解锁", "lock"
        lv = max(1, min(5, int(sk.get(name, 1))))
        frac = int(cfg.SKILL_TIER_MP_FRAC.get(e["tier"], 0.2) * 100)
        role = fx.role_label(e.get("role", ""))

        # 副行：来源 + 阶位 + 类型 + 斗气消耗（不再把主定位塞进这行，它单独成胶囊）
        # 注意 rank 与 kind 可能同名（如「秘法」阶 + 「秘法」类型），去重后再拼，
        # 否则会画出「秘法 · 秘法」这种重复。
        rank = str(e.get("rank") or "")
        kind = str(e.get("kind") or "")
        parts = [f"{_origin_mark(e)}{rank}"]
        if kind and kind != rank:
            parts.append(kind)
        parts.append(f"斗气 {frac}%")
        if not unlocked:
            parts.append("lv%s 解锁" % e.get("unlock", "?"))
        meta = " · ".join(x for x in parts if x)
        if e.get("flame"):
            meta = f"异火技〔{e['flame']}〕 · " + meta

        # chip 序列：伤害倍率打头（数值可比），其后每个维度一枚胶囊
        chips = [{"label": "伤害", "value": "×%.2f" % float(e.get("dmg") or 0)}]
        chips += fx.effect_chips(e.get("effects") or [])

        # 2026-11-15 用户纠正：「不要写原著多少章这种描述，你见过哪个游戏的
        # 技能描述这样写的？」——来源只取 skill_book.source（谁所使 / 怎么得到），
        # 由 desc_with_source 用逗号并进描述第一句，像游戏技能说明而不是小说注脚；
        # novel 章节引文只用于离线核验，绝不进卡片。有来源就加、没有就不加。
        phrase = fx.source_phrase(e)

        row = {
            # 全局序号（1-based，按卡片显示顺序 = core.skill_pool_sorted）。
            # 玩家可直接发「上阵 1 3 5 7」按序号上阵，无需手打技名。
            "no": no,
            "name": name,
            "badge": e["tier"][0],
            "tier": e["tier"],
            "role": role,
            "state": state,
            "state_kind": state_kind,
            "meta": meta,
            # 完整技能描述（用户 2026-11-15：「技能描述加上，别偷懒」）：
            # 名片卡只放 brief 是偷懒，玩家看不到机制就会退回比数字。
            # 完整描述去掉末尾「限制：…」段——代价由 gate 行单独展示，
            # 否则同一件事在图里说两遍（用户实测指出重复）。
            # 首句并入技能来源状语（``desc_with_source``），无来源时原样不动。
            "desc": fx.desc_with_source(
                e, (fx.desc_without_gate(str(e.get("desc") or ""))
                    or str(e.get("desc") or e.get("brief") or ""))),
            # 结构化透出来源，供交付闸门断言「有来源必加、无来源不编、不写章节号」
            "source_phrase": phrase,
            "has_source": bool(phrase),
            "chips": chips,
            "gate": str(e.get("gate") or ""),
            # ---- 兼容通用列表卡 / 既有测试的扁平字段 ----
            "title": name,
            "right": state,
            "sub": ((f"异火技〔{e['flame']}〕 · " if e.get("flame") else "")
                    + (f"〔{role}〕" if role else "") + meta),
            "note": " · ".join(f"{c['label']}{c['value']}" for c in chips[1:])
                    + ((" ｜ 限：" + str(e.get("gate"))) if e.get("gate") else ""),
            "bar": {"cur": lv, "max": 5},
        }
        rows.append(row)
        by_tier.setdefault(e["tier"], []).append(row)

    groups = []
    for tier in cfg.SKILL_TIERS:
        rs = by_tier.get(tier) or []
        if not rs:
            continue
        groups.append({
            "tier": tier,
            "count": len(rs),
            "icon": {"普通": "sword", "高阶": "bolt", "特殊": "star"}.get(tier, "sword"),
            "rows": rs,
        })

    fac_name = p.get("faction_name") or "无宗门"
    on = sum(1 for e in pool_sorted if e.get("equipped"))
    own = sum(1 for e in pool_sorted if e.get("origin") == "本宗")
    common = sum(1 for e in pool_sorted if e.get("origin") == "通用")
    return {
        "icon": "bolt", "title": "本宗斗技",
        "subtitle": (f"{fac_name} · 共 {len(rows)} 门 · 本宗 {own} / 通用 {common} · "
                     f"上阵 {on}/{cfg.LOADOUT_SIZE}"),
        "groups": groups,
        "rows": rows,
        "tip": "序号即上阵编号：发「上阵 1 3 5 7」按号选技 ｜ 斗技详情 <技名> 看原著出处",
    }


async def render_roster(p: dict) -> bytes | None:
    """「斗技 全部」出图：本宗全部斗技名录卡（按阶位分组 + 卡片块）。"""
    return await _render_list("doupo_roster", build_roster_data(p), "斗破本宗斗技")


def build_faction_data(p: dict) -> dict:
    """宗门 → doupo_faction 模板参数（卡片化展示，设计美化）。

    与 commands.cmd_faction 纯文本兜底同源：读同一个 player / FACTIONS，
    因此「图片里的宗门」和「文字里的宗门」永远不会说两回事。
    """
    core.settle(p)
    name = p.get("faction_name") or "无宗门"
    fid = p.get("faction") or ""
    fac_def = fac.FACTIONS.get(fid, {})
    POOL_CN = {"starter": "新手池", "advanced": "进阶池", "hidden": "隐世池"}
    pool = str(fac_def.get("pool", ""))
    base = str(fac_def.get("base", ""))
    npcs = list(fac_def.get("npcs", []) or [])
    flames = list(fac_def.get("flames", []) or [])
    lv = int(p.get("lv", 1))

    # 本门斗技统计（普通/高阶/特殊 门数）
    pool_sorted = core.skill_pool_sorted(p)
    tier_counts = {t: sum(1 for e in pool_sorted if e["tier"] == t)
                   for t in cfg.SKILL_TIERS}

    # 可转宗门（按卡池分组，只列当前境界已开放的）
    cands = core.faction_candidates(p)
    groups = []
    for pool_key in ("starter", "advanced", "hidden"):
        rows = [c for c in cands if c["pool"] == pool_key]
        if not rows:
            continue
        names = ["%s %d门" % (c["name"], c["skills"]) for c in rows]
        groups.append({"pool": POOL_CN.get(pool_key, pool_key), "names": names})

    cost = core.faction_switch_cost(p)
    open_pools = [POOL_CN[x] for x in core.faction_pools_open(p)]
    return {
        "name": name,
        "pool": POOL_CN.get(pool, pool),
        "base": base,
        "npcs": npcs,
        "flames": flames,
        "tier_counts": tier_counts,
        "total_skills": len(pool_sorted),
        "open_pools": open_pools,
        "lv": lv,
        "groups": groups,
        "switch_cost": cost,
        "tip": "用法：转宗 <宗门名>（只换师门并重配四槽，已升技能等级按技名保留）",
    }


async def render_faction(p: dict) -> bytes | None:
    """「宗门」出图：宗门概览 / 斗技统计 / 代表人物 / 传承异火 / 可转宗门。"""
    return await _render_list("doupo_faction", build_faction_data(p), "斗破宗门")

def build_flames_data(p: dict) -> dict:
    """异火列表 → doupo_list 模板参数。

    2026-11-16 修复：首行先列「本宗传承异火」（原著考证文字），本宗无传承则
    明确提示；其后才是可自行收服/喂养的 5 种异火，并标明与本宗传承无关，
    消除「本宗没异火却列一堆异火」的矛盾感。养成列表本身不变。
    """
    fl = p.get("flames") or {}
    dross = core.count_item(p, "药渣")
    fid = str(p.get("faction") or "")
    fdef = fac.FACTIONS.get(fid, {})
    fname = str(p.get("faction_name") or fdef.get("name") or "本宗")
    trad = list(fdef.get("flames", []) or [])
    rows = []
    if trad:
        for i, t in enumerate(trad):
            rows.append({"title": (f"{fname}·本宗传承" if i == 0 else "　"),
                         "right": "", "sub": str(t), "note": ""})
    else:
        rows.append({"title": f"{fname}·本宗传承", "right": "无",
                     "sub": "本宗无传承异火。以下异火皆为游历中所遇，与师门无关。", "note": ""})
    rows.append({"title": "可收服的异火", "right": "",
                 "sub": "须先收服才可喂养；未收服不显示下级需求。", "note": ""})
    own = core.faction_flame(p.get("faction"))
    for f in cfg.FLAMES:
        name = f["name"]
        lv = int(fl.get(name, 0))
        note = ""
        if name == cfg.FLAME_DIYAN:
            state = "已收服" if p.get("diyan") else "未得（成就斗帝自燃）"
            sub = f["desc"]
        elif lv <= 0:
            # 2026-11-18 A 方案：未收服不得喂养，卡面也不许再写「下级需 N 药渣」
            state = "未收服"
            sub = f["desc"]
            note = "本宗异火，入道/转宗即得" if own == name else "须另寻机缘收服"
        elif lv >= cfg.FLAME_MAX_LV:
            state = f"{lv} 级（圆满）"
            sub = f["desc"]
        else:
            need = int(f["need"]) * (lv + 1)
            state = f"{lv}/{cfg.FLAME_MAX_LV} 级"
            sub = f["desc"]
            # 2026-11-15 card-design：「下级需 N 药渣」独立成 note 行，用显式
            # NOTE_GAP 与描述拉开；旧版用全角空格拼进 sub，长描述折行后会连读。
            note = f"下级需 {commands._fmt(need)} 药渣"
        rows.append({"title": name, "right": state, "sub": sub, "note": note})
    return {
        "icon": "star", "title": "异火",
        "subtitle": f"药渣 ×{dross}",
        "rows": rows,
        "tip": "用法：养火 青莲地心火（一次喂光现有药渣）",
    }


def build_shop_data(p: dict) -> dict:
    """宝库列表 → doupo_list 模板参数。"""
    rows = []
    for it in core.shop_list():
        rows.append({"title": it["name"], "right": f"{it['price']} 金", "sub": it["desc"]})
    return {
        "icon": "cart", "title": "随身商店",
        "subtitle": f"金币 {int(p.get('gold', 0))} · 药材 {len(rows)} 种",
        # 2026-11-16：原著药材补录后 50+ 种，单列会到 5000px 不可读；双列排版。
        "columns": 2,
        "rows": rows,
        "tip": "用法：买 血灵草 2",
    }


def build_bag_data(p: dict) -> dict:
    """背包列表 → doupo_list 模板参数。"""
    items = core.bag_slots(p)
    known = {r["name"]: r["desc"] for r in cfg.RECIPES}
    known.update({h["name"]: h["desc"] for h in cfg.HERBS})
    rows = []
    for name, n in items[:24]:
        rows.append({"title": f"{name} ×{n}", "sub": known.get(name, "")})
    return {
        "icon": "diamond", "title": "包裹",
        "subtitle": f"{len(items)}/24 格 · 金币 {int(p.get('gold', 0))}",
        "rows": rows,
        "tip": "用法：服丹 回灵丹",
    }


def build_rank_data(top: list) -> dict:
    """战力榜（已排序前 N）→ doupo_list 模板参数。"""
    rows = []
    for i, q in enumerate(top, 1):
        rows.append({
            "badge": f"#{i}",
            "title": q.get("name") or "无名",
            "right": f"{cfg.full_realm(int(q.get('lv', 1)))} · 战力 {core.power(q)}",
            "accent": i == 1,
        })
    return {
        "icon": "arrow_up", "title": "本群战力榜",
        "subtitle": f"前 {len(rows)} 名",
        "rows": rows,
        "tip": "发 抢 @某人 掠夺 10% 金币",
    }


async def render_skills(p: dict) -> bytes | None:
    # 专用模板：技能描述 + 详细伤害 + 五段斗气条 + 重点大字粗体。
    # 不再复用 doupo_list，避免把异火/宝库/背包/排行一起改坏。
    return await _render_list("doupo_skills", build_skills_data(p), "斗破斗技")


async def render_flames(p: dict) -> bytes | None:
    return await _render_list("doupo_list", build_flames_data(p), "斗破异火")


async def render_shop(p: dict) -> bytes | None:
    return await _render_list("doupo_list", build_shop_data(p), "斗破宝库")


async def render_bag(p: dict) -> bytes | None:
    return await _render_list("doupo_list", build_bag_data(p), "斗破背包")


async def render_rank(top: list) -> bytes | None:
    return await _render_list("doupo_list", build_rank_data(top), "斗破排行")


# ---------------------------------------------------------------------------
# 炼药丹方卡 / 玩法细则卡（2026-11-16：内容多且近乎固定的纯文本 → 卡片）
# ---------------------------------------------------------------------------
CARD_RECIPES_NAME = "斗破炼药"
CARD_HELP_NAME = "斗破玩法"
CARD_BATTLE_NAME = "斗破战斗回看"


def build_recipes_data(p: dict, page: int = 1) -> dict:
    """炼药丹方 → doupo_recipes 模板参数（等级过滤 + 分页 + 两列数据）。

    与 ``commands.cmd_recipes`` 严格同源：
      * 可见丹方 = ``core.recipes_page(p, page)``（内含等级过滤 + 分页夹取）；
      * 分组顺序 = ``cfg.GRADE_ORDER``（与纯文本一致）；
      * 齐备标记 = ``core.count_item`` 逐味判（与纯文本 `ok` 判据逐字一致）；
      * 功效标签 = ``cfg.effect_tag``，单丹成功率 = ``cfg.craft_rate_for``
        （2026-11-18 原著口径：够格用基础率、越 1 档×0.35、越≥2 档×0.05）。
        标签与成功率都拼进 ``desc``，由模板自适应折行，不动坐标骨架。

    2026-11-16 用户要求「等级不够不展示 / 丹药多了分页 / 两列展示」：
    低阶丹药由 ``available_recipes`` 直接过滤掉，卡上只出现当前境界可用者；
    每页固定 ``cfg.RECIPES_PER_PAGE`` 枚，模板按两列网格排布，页码信息由
    ``page`` / ``total_pages`` / ``total_avail`` 透出（供卡面与翻页按钮共用）。
    """
    alch_idx = int(p.get("alch", 0))
    alch = cfg.ALCHEMIST[alch_idx]
    chunk, page, total_pages = core.recipes_page(p, page)
    total_avail = len(core.available_recipes(p))

    groups = []
    for grade in cfg.GRADE_ORDER:
        rows = []
        for r in chunk:
            if r["grade"] != grade:
                continue
            need = r.get("need") or {}
            # 2026-11-18 原著口径：单丹成功率随品阶差变化，功效显式挂标签。
            # 信息拼进 desc（模板 Canvas.wrap 折行 + _block_h 同源度量），
            # 不改模板坐标骨架 —— 越1档「勉强」、越≥2档「几无可能」。
            rate, diff = cfg.craft_rate_for(alch_idx, r.get("grade", ""))
            tag = cfg.effect_tag(r.get("effect", ""))
            rate_bit = "%s把握" % cfg.rate_cn(rate)
            if diff > 0:
                rate_bit += " 越%d档·勉强" % diff if diff == 1 else " 越%d档·几无" % diff
            desc_bits = []
            if tag:
                desc_bits.append("〔%s〕" % tag)
            desc_bits.append(rate_bit)
            orig = str(r.get("desc") or "")
            if orig:
                desc_bits.append(orig)
            rows.append({
                "name": r["name"],
                "mats": [[k, int(v)] for k, v in need.items()],
                "desc": " · ".join(desc_bits),
                "effect_tag": tag,
                "rate": int(rate * 100),
                "diff": diff,
                "ready": all(core.count_item(p, k) >= int(v) for k, v in need.items()),
                # 无药方丹药：卡面改为「N 金购买」而非空材料行（与原著考据口径一致）
                "buyonly": not bool(need),
                "buy": int(r.get("buy", 0)),
            })
        if rows:
            groups.append({"grade": grade, "rows": rows})

    # 每页丹药数 = 当前页实际条数；卡面与脚注都读它，杜绝「算的画得下」三处不一致
    shown = sum(len(g["rows"]) for g in groups)
    return {
        "icon": "wand",
        "title": "炼药",
        "subtitle": ("%s炼药师 · 低阶更稳/越阶骤降 · 药渣 ×%d" %
                     (alch[0], core.count_item(p, "药渣"))),
        "page": page,
        "total_pages": total_pages,
        "total_avail": total_avail,
        "shown": shown,
        "groups": groups,
        "tip": ("第 %d/%d 页 · 本页 %d 枚 · 可用丹方 %d 枚" %
                (page, total_pages, shown, total_avail)) if total_pages > 1 else
               ("可用丹方 %d 枚 · 炼制 <丹名> / 买 <丹名>" % total_avail),
    }


async def render_recipes(p: dict, page: int = 1) -> bytes | None:
    """「炼药/丹方」出图：等级过滤 + 分页 + 两列丹方卡。失败降级纯文本由调用方保证。"""
    return await _render_list("doupo_recipes", build_recipes_data(p, page), CARD_RECIPES_NAME)


def build_help_data() -> dict:
    """玩法细则 → doupo_help 模板参数，数据源 commands.HELP_GROUPS（唯一源）。"""
    return {
        "title": "斗破玩法",
        "groups": [
            {"name": name, "rows": [str(x) for x in rows]}
            for name, rows in commands.HELP_GROUPS
        ],
        "tip": "发「斗破菜单」看全部口令 · 数值改动以本卡为准",
    }


async def render_help() -> bytes | None:
    """「帮助/玩法/攻略/规则」出图：10 分组玩法细则卡。"""
    return await _render_list("doupo_help", build_help_data(), CARD_HELP_NAME)


def build_battle_data(last_battle: dict) -> dict:
    """最后一场战斗 → doupo_battle 模板参数（与纯文本兜底同源）。"""
    return br.build_data(last_battle or {})


async def render_battle(last_battle: dict) -> bytes | None:
    """「战斗回看」出图：完整逐招过程卡，按钮「战斗评价」由 card_key 派生。"""
    return await _render_list("doupo_battle", build_battle_data(last_battle),
                              CARD_BATTLE_NAME)


# ---------------------------------------------------------------------------
# 傀儡卡（2026-11-18）
# 数据来自 puppet_store 的独立 SQLite 真实列；这里只做展示映射，不重算数值。
# ---------------------------------------------------------------------------
CARD_PUPPET_NAME = "斗破傀儡"


def build_puppet_data(roster: list, elig: dict, p: dict | None = None) -> dict:
    """傀儡名录 → doupo_puppet 模板参数。

    与 ``commands.cmd_puppet`` 纯文本兜底同源：攻击走 ``puppet_attack`` 唯一口径，
    资格走 ``puppet_gate`` 同一套五道，不在渲染层另写判定。
    """
    from . import puppet_core as pc

    roster = [x for x in (roster or []) if isinstance(x, dict)]
    elig = elig or {}
    out: dict = {
        "title": "傀儡",
        "subtitle": "持有 %d 具 · 出战 %d 具" % (
            len(roster), 1 if any(x.get("id") == elig.get("active_id") for x in roster) else 0),
        "puppets": [], "gates": [],
        "tip": "发「傀儡 淬炼」雷池提纯 · 「傀儡 结阵」多人合击 · 「炼制傀儡」再炼一具",
    }
    for pu in roster:
        tier_name, skin = cfg.PUPPET_TIER_BY_IDX.get(
            int(pu.get("tier", 0)), ("人级傀儡", "铜"))
        body = "%s（%s%s）" % (
            pu.get("body_name") or "—", pu.get("body_realm") or "凡躯",
            ("·%d星" % pu["body_star"]) if pu.get("body_star") else "")
        out["puppets"].append({
            "name": str(pu.get("name") or "傀儡"),
            "badge": "%s · %s皮%s" % (tier_name, skin, " · 出战" if pu.get("id") == elig.get("active_id") else ""),
            "atk": str(int(pc.puppet_attack(pu))),
            "lines": [
                "躯体 " + body,
                "魔核 %d阶%s系 · 灵魂 %s" % (int(pu.get("core_tier", 0)),
                                              pu.get("core_attr") or "无",
                                              pu.get("soul_source") or "—"),
                "雷淬纯度 %d%% · %s%s" % (
                    int(pu.get("purity", 0)),
                    "已认主·永不背叛" if pu.get("bonded") else "未认主",
                    " · 有灵" if pu.get("awakened") else ""),
            ],
            "active": bool(pu.get("id") == elig.get("active_id")),
        })
    if not roster and p is not None:
        checks = [
            ("境界", pc.gate_realm(p, 0)), ("灵魂境界", pc.gate_soul(p)),
            ("控火", pc.gate_fire_ctrl(p)), ("异火", pc.gate_flame(p)),
        ]
        out["gates"] = [{"name": name, "ok": ok, "why": reason}
                        for name, (ok, reason) in checks]
        out["tip"] = "五道资格齐备后发「炼制傀儡」· 材料去「傀儡遗迹」攒"
    return out


async def render_puppet(roster: list, elig: dict, p: dict | None = None) -> bytes | None:
    """「傀儡」出图：名录卡；未拥有时渲染五道资格，绝不降级为纯文字空回。"""
    return await _render_list("doupo_puppet",
                              build_puppet_data(roster, elig, p), CARD_PUPPET_NAME)


CARD_RUIN_NAME = "斗破傀儡遗迹"


def build_ruin_data(state: dict, day: str = "") -> dict:
    """遗迹当日状态 → doupo_ruin 模板参数；段落与掉落只读 cfg 唯一源。"""
    stages = cfg.PUPPET_RUIN_STAGES
    done = int((state or {}).get("stage", 0))
    groups = []
    for i, sg in enumerate(stages):
        status = "已通关" if i < done else ("可挑战" if i == done else "未解锁")
        drops = "、".join("%s×%d" % (k, int(v))
                         for k, v in (sg.get("drops") or {}).items())
        groups.append({
            "name": "%s · %s" % ("一二三"[i] if i < 3 else str(i + 1), sg["name"]),
            "rows": [str(sg.get("desc") or ""),
                     "%s ｜ 掉 %s ｜ %s" % (sg["monster"], drops, status)],
        })
    return {
        "title": "傀儡遗迹",
        "subtitle": "今日进度 %d/%d%s" % (done, len(stages), (" · " + day) if day else ""),
        "groups": groups,
        "tip": "发「傀儡遗迹」逐段挑战 · 发「傀儡」看名录 · 发「炼制傀儡」开炉",
    }


async def render_ruin(state: dict, day: str = "") -> bytes | None:
    """「傀儡遗迹」出图：三段进度卡，与战报文字同源（同一 stage 水位线）。"""
    return await _render_list("doupo_ruin", build_ruin_data(state, day), CARD_RUIN_NAME)
