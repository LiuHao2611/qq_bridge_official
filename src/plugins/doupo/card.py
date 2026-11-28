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
import hashlib
import importlib.util
import json
import logging
import sys
from collections import OrderedDict
from pathlib import Path

from . import battle_review as br
from . import bagview
from . import card_theme
from . import config as cfg
from . import core
from . import commands
from . import avatar
from . import factions as fac
from . import realm_lore
from . import skill_effects as fx
from . import skill_rank as rank_mod
from . import danwen as dw
from . import flame_view as fview

log = logging.getLogger("qq_bridge.doupo.card")

#: 卡片路由用的业务卡名（命中卡片级主题绑定后，主题编辑器可按此卡单独改色）
CARD_NAME = "斗破人物"

#: 菜单卡的独立卡名（与人物卡共享 bg_overrides["doupo"] 配色，但可在编辑器里单独改）
CARD_MENU_NAME = "斗破菜单"

#: 卡片宽度（QQ 聊天窗内 720 显示效果最稳，过宽会被压缩导致字小）
CARD_WIDTH = 720

#: 分页卡并发渲染上限（真机实测 sweet spot：x2=1.7x、x4=1.1x、x8=0.5x）
#: 只用于战斗回看这类多页卡，封顶 2 防多用户同时触发时线程爆炸。
_PAGE_RENDER_CONCURRENCY = 2

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


# ---------------------------------------------------------------------------
# 双主题渲染注入（2026-11-23）
# ---------------------------------------------------------------------------
# 12 张斗破卡共用 doupo.py::_pal()（读 pic 包内的 contextvar）。这里在每次渲染
# 前按「个人偏好 > 全局默认」set_style，渲染后 reset —— 不改任何 matcher。
# 关键：style 必须注入 data，因为 pic.render 的磁盘缓存 key 由 params(data) 组成，
# 不含 contextvar；不注入会导致切主题后命中旧图（两套装在同一 key 上打架）。
def _style_for(data: dict | None) -> tuple[str, object, object]:
    """解析本次渲染主题并 set contextvar；返回 (style, token, doupo_tpl)。

    data 里的 ``__qq`` 由 build_*_data 注入（玩家 QQ）；公共卡（菜单/帮助/排行/
    战报）没有 qq → 走全局默认。调用方必须 :func:`_reset_style` 释放 token。
    """
    tpl = None
    token = None
    qq = ""
    try:
        if isinstance(data, dict):
            qq = str(data.get("__qq") or "")
        style = card_theme.resolve(qq or None)
        tpl = importlib.import_module(f"{_PKG_NAME}.templates.doupo")
        token = tpl.set_style(style)
        return style, token, tpl
    except Exception as e:  # noqa: BLE001 — 主题注入绝不能让卡片渲染失败
        log.debug("斗破主题注入失败，回退简约风：%s: %s", type(e).__name__, e)
        if token is not None and tpl is not None:
            try:
                tpl.reset_style(token)
            except Exception:  # noqa: BLE001
                pass
        return card_theme.LIGHT, None, None


def _reset_style(token, tpl) -> None:
    if token is not None and tpl is not None:
        try:
            tpl.reset_style(token)
        except Exception:  # noqa: BLE001
            pass


def _styled(data: dict | None, style: str) -> dict:
    """把 style 与去内部键后的 data 拷一份，供 pic.render 参与缓存 key。"""
    out = dict(data or {})
    out.pop("__qq", None)      # 内部键不进模板
    out["__doupo_style"] = style  # 只用于缓存 key 区分；模板忽略未知键
    return out


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

    _alch_idx = int(p.get("alch", 0))
    alch_name = cfg.alch_label(_alch_idx)   # 未入品→「未入品」，其余→「二品炼药师」
    alch_need = cfg.alch_tier(_alch_idx)[1]
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
        # 2026-11-27：境界描述唯一源在 realm_lore，图/文两条兜底都从它取。
        # 模板在境界 chip 与修为条之间画这一行；空串时不撑高，老存档零影响。
        "realm_desc": realm_lore.desc_of(lv),
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

    2026-11-26 用户「今天卡片发送又变慢了」：本函数原先**自己实现了一遍**
    主题注入 + pic.render，绕过了 ``_render_list`` 里的 ``_RENDER_CACHE``
    （既不记 stats，也没有「命中即字节稳定」的保证）。改为一律走
    ``_render_list``，与背包/斗技/物品卡同一条路径。
    """
    if not p:
        return None
    data = build_data(p)
    if tip is not None:
        data["tip"] = tip
    return await _render_list("doupo", data, CARD_NAME, qq=p.get("qq"))


def menu_cmd_label(cmd: str) -> str:
    """菜单卡口令标签：委托 pic.templates.doupo_menu（与门禁共用单一真值）。

    先确保 pic 包已由 _load_pic() 注册到 sys.modules（importlib 独立加载，
    不能用普通 import），再取其子模块；pic 不可用时降级为本地首词截取。
    """
    if _load_pic() is None:
        return str(cmd).strip().split(" ", 1)[0]
    mod = importlib.import_module(f"{_PKG_NAME}.templates.doupo_menu")
    return mod.menu_cmd_label(cmd)


def build_menu_data() -> dict:
    """口令菜单 → doupo_menu 模板参数。

    数据源是 commands.MENU_GROUPS（唯一源），与纯文本兜底同源，
    因此图片与文字永远不会「说的不是一回事」。
    2026-11-20：口令列在卡片层用 menu_cmd_label 精简示例参数（两列并排省版面）。
    """
    return {
        "title": "斗破菜单",
        "groups": [
            {"name": name, "rows": [[menu_cmd_label(cmd), desc] for cmd, desc in rows]}
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
    style, token, tpl = _style_for(data)
    render_data = _styled(data, style)
    try:
        # 首选：走完整主题链路（卡片绑定 > 自定义主题 > PIC_THEME > 内置）+ 磁盘缓存
        try:
            return await pic.render("doupo_menu", card=CARD_MENU_NAME, width=CARD_WIDTH, data=render_data)
        except Exception as e:  # noqa: BLE001
            log.debug("斗破菜单卡主题链路渲染失败，尝试内置 light 兜底：%s", e)
        # 兜底：显式内置 light 主题，绕开主题解析（防止主题配置损坏导致整卡不可用）
        try:
            return await asyncio.to_thread(
                pic.render_sync, "doupo_menu", "light", CARD_MENU_NAME, width=CARD_WIDTH, data=render_data
            )
        except Exception as e:  # noqa: BLE001
            log.warning("斗破菜单卡渲染失败（已降级为纯文本）：%s: %s", type(e).__name__, e)
            return None
    finally:
        _reset_style(token, tpl)


# ---------------------------------------------------------------------------
# 通用列表卡（异火 / 宝库 / 背包 / 排行）
# 斗技自 2026-11-14 起走 doupo_skills 专用模板（描述/伤害/五段条/重点大字），
# 不再与上面四张共用 doupo_list。
# ---------------------------------------------------------------------------
async def _render_pages(template: str, data: dict, card_name: str,
                        qq: str | None = None) -> list[bytes]:
    """渲染可分成多页的卡（战斗回看）：返回每页 PNG；失败返回 ``[]`` 走纯文本。

    切页是模板的事（``pic.split_pages`` 调模板自己的测量函数），本层只负责
    逐页渲染。**任一一页失败就整份降级**——只发前两页会把后排回合吞掉，
    比回到纯文本更糟（纯文本至少有完整逐招）。
    """
    pic = _load_pic()
    if pic is None:
        return []
    if qq:
        data = dict(data)
        data["__qq"] = str(qq)
    try:
        pages = pic.split_pages(template, data, width=CARD_WIDTH)
    except Exception as e:  # noqa: BLE001
        log.warning("%s 分页失败（整份降级纯文本）：%s: %s", card_name, type(e).__name__, e)
        return []
    # 主题按整份解析一次（分页共享同一风格），qq 来自 data 内部键（若有）。
    style, token, tpl = _style_for(data)
    render_pages = [_styled(pg, style) for pg in pages]

    async def _one(idx: int, page_data: dict) -> bytes | None:
        """渲染单页：主题链路 → 内置 light 兜底；整页失败返回 None。"""
        try:
            return await pic.render(template, card=card_name,
                                    width=CARD_WIDTH, data=page_data)
        except Exception as e:  # noqa: BLE001
            log.debug("%s 第 %d 页主题链路失败，试内置 light：%s", card_name, idx, e)
        try:
            return await asyncio.to_thread(
                pic.render_sync, template, "light", card_name,
                width=CARD_WIDTH, data=page_data)
        except Exception as e:  # noqa: BLE001
            log.warning("%s 第 %d/%d 页渲染失败（整份降级纯文本）：%s: %s",
                        card_name, idx, len(pages), type(e).__name__, e)
            return None

    # 分页并发上限 2（2026-11-24 真机实测）：单卡渲染受 GIL 保护，线程>2 反而
    # 比串行慢（x2=1.7x / x4=1.1x / x8=0.5x），但同模板多页缓存友好、PNG 编码
    # 释放 GIL，x2 实测战斗回看 3 页 2777ms→738ms(3.8x)。gather 保序，Semaphore
    # 封顶 2 防止多用户同时触发时线程爆炸。contextvar 主题由 create_task/to_thread
    # 各自复制，缓存 PicCache 有 RLock，均无竞态。
    gate = asyncio.Semaphore(_PAGE_RENDER_CONCURRENCY)

    async def _bounded(idx: int, page_data: dict) -> bytes | None:
        async with gate:
            return await _one(idx, page_data)

    try:
        got = await asyncio.gather(*[
            _bounded(i, pd) for i, pd in enumerate(render_pages, 1)
        ])
    finally:
        _reset_style(token, tpl)
    if any(p is None for p in got):
        return []  # 任一页失败整份降级
    out = list(got)
    log.info("%s 出图 %d 页", card_name, len(out))
    return out


# ---------------------------------------------------------------------------
# 渲染结果缓存（2026-11-25 用户「查看物品卡输出很慢，99% 固定内容，你忘了加缓存」）
#
# PNG 字节**完全**由 (template, card_name, render_data) 决定：主题色/布局在
# _styled() 里已烘成具体值，所以参与哈希的数据里天然带着主题指纹——改主题自然
# 换 key，不会串色。命中时直接返回字节，省掉取数与整张重绘。
#
# 比「只靠 pic 内部缓存」强的地方：pic 的缓存键由它自己定（某些模板不缓存），
# 而且我们这层命中连 build_*_data 之后的 hash 计算都省不了、但能省渲染；
# 更关键的是**字节稳定 → 上层素材上传按 md5 命中**，不会每次重传一张新图。
# ---------------------------------------------------------------------------
_RENDER_CACHE: "OrderedDict[str, bytes]" = OrderedDict()
_RENDER_CACHE_MAX = 48          # 单张 100~250KB，48 张 ≈ 10MB 上限
_RENDER_STATS = {"hit": 0, "miss": 0, "put": 0}


def _render_cache_key(template: str, card_name: str, data: dict) -> str:
    blob = json.dumps(data, ensure_ascii=False, sort_keys=True, default=str)
    return "%s|%s|%s" % (template, card_name,
                         hashlib.sha1(blob.encode("utf-8")).hexdigest())


def render_cache_stats() -> dict:
    """缓存命中计数（守门/排障用，不对外输出给玩家）。"""
    return dict(_RENDER_STATS)


def render_cache_clear() -> None:
    """清空渲染缓存（测试隔离用）。"""
    _RENDER_CACHE.clear()
    for k in _RENDER_STATS:
        _RENDER_STATS[k] = 0


def _render_cache_get(key: str) -> bytes | None:
    hit = _RENDER_CACHE.get(key)
    if hit is None:
        _RENDER_STATS["miss"] += 1
        return None
    _RENDER_CACHE.move_to_end(key)
    _RENDER_STATS["hit"] += 1
    return hit


def _render_cache_put(key: str, png: bytes) -> None:
    if not png:
        return
    _RENDER_CACHE[key] = png
    _RENDER_STATS["put"] += 1
    _RENDER_CACHE.move_to_end(key)
    while len(_RENDER_CACHE) > _RENDER_CACHE_MAX:
        _RENDER_CACHE.popitem(last=False)


async def _render_list(template: str, data: dict, card_name: str,
                       qq: str | None = None) -> bytes | None:
    """渲染任意斗破列表卡：主题链路 → 内置 light 兜底 → None（调用方回纯文本）。"""
    pic = _load_pic()
    if pic is None:
        return None
    if qq:
        data = dict(data)
        data["__qq"] = str(qq)
    style, token, tpl = _style_for(data)
    render_data = _styled(data, style)
    key = _render_cache_key(template, card_name, render_data)
    cached = _render_cache_get(key)
    if cached is not None:
        return cached
    png: bytes | None = None
    try:
        try:
            png = await pic.render(template, card=card_name, width=CARD_WIDTH, data=render_data)
        except Exception as e:  # noqa: BLE001
            log.debug("%s 主题链路渲染失败，尝试内置 light 兜底：%s", card_name, e)
        if not png:
            try:
                png = await asyncio.to_thread(
                    pic.render_sync, template, "light", card_name, width=CARD_WIDTH,
                    data=render_data
                )
            except Exception as e:  # noqa: BLE001
                log.warning("%s 渲染失败（已降级为纯文本）：%s: %s",
                            card_name, type(e).__name__, e)
                png = None
    finally:
        _reset_style(token, tpl)
    if png:
        _render_cache_put(key, png)
    return png


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
        mx = core.skill_max(e)
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
            "item_need": 0 if maxed else core.skill_up_cost(lv, e),
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
        "__qq": str(p.get("qq") or ""),
    }


#: 原著大阶 → 分区图标（几何递进：圆 → 三角 → 菱形 → 星 → 法杖，
#: 一眼能看出「越往后越稀有」，与模板 doupo_roster.RANK_STYLE 同一套顺序）。
_FAM_ICON = {
    "黄阶": "circle",
    "玄阶": "triangle",
    "地阶": "diamond",
    "天阶": "star",
    "秘法": "wand",
}


def _rank_groups(by_fam: dict[str, list[dict]]) -> list[dict]:
    """把「按大阶分组」重排成「按完整阶位分组」（2026-11-27）。

    用户：「阶级要标注完整，地阶高级，别只写个地阶」。所以分区标题用完整 rank
    （黄阶低级/黄阶中级/…/天阶高级/秘法），组内 rank 一致。

    * ``tier`` = 完整 rank（模板拿它当分区标题）；
    * ``family`` = 大阶（模板拿它取色/取图标，保证同一大阶各小阶同色同图标）；
    * 排序走 ``rank_mod.sort_key``（黄→天→秘法，各阶内低→中→高），与卡内同源。
    """
    buckets: dict[str, list[dict]] = {}
    for rs in by_fam.values():
        for row in rs:
            rank = str(row.get("rank") or row.get("family") or "未标阶")
            buckets.setdefault(rank, []).append(row)
    out: list[dict] = []
    for rank in sorted(buckets, key=rank_mod.sort_key):
        rs = buckets[rank]
        fam = rank_mod.fam_of(rank)
        out.append({
            "tier": rank, "label": rank,
            "family": fam,
            "count": len(rs),
            "icon": _FAM_ICON.get(fam, "sword"),
            "rows": rs,
        })
    return out


def _origin_mark(e: dict) -> str:
    """斗技来源标记：一眼分清本宗传承 / 通用基础 / 异火技。

    2026-11-20 沉浸式铁律：不再追加「〔自设〕」。玩家不需要在卡面上被提醒
    「这门斗技是补出来的」——那属于考据台账（skill_book.novel），不进玩家视野。
    """
    origin = e.get("origin") or "本宗"
    return {"本宗": "〔本宗〕", "通用": "〔通用〕", "异火": "〔异火〕"}.get(
        origin, f"〔{origin}〕")


def _roster_row(e: dict, no, sk: dict, state: str, state_kind: str,
                show_lv: bool = False, dmg_now: int | None = None,
                plv: int = 1) -> dict:
    """把一条斗技 entry 转成 doupo_roster 的行数据（唯一定义，两卡共用）。

    ``build_roster_data``（宗门斗技·本宗池）与 ``build_learned_data``（斗技·我的）
    必须同构，否则同一门技在两张卡上会长得不一样（对齐/字段漂移）。
    ``show_lv=True`` 时副行追加 Lv，用于「我的斗技」——玩家关心自己练到几级；
    宗门卡列的是"宗门有什么"，等级无意义，保持原样不动。
    """
    name = e["name"]
    unlocked = bool(e.get("unlocked"))
    # 斗气占比按玩家境界取（2026-11-26 与实算 skills.tier_cost_by_lv 同源）。
    frac = int(round(cfg.skill_mp_frac(e["tier"], plv) * 100))
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
    if show_lv:
        parts.append("Lv%d" % max(1, min(5, int(sk.get(name, 1)))))
    if not unlocked:
        parts.append("lv%s 解锁" % e.get("unlock", "?"))
    meta = " · ".join(x for x in parts if x)
    if e.get("flame"):
        meta = f"异火技〔{e['flame']}〕 · " + meta

    # chip 序列：伤害倍率打头（数值可比），其后每个维度一枚胶囊
    chips = [{"label": "伤害", "value": "×%.2f" % float(e.get("dmg") or 0)}]
    if dmg_now is not None:
        # 「斗技」合并卡专用：当前等级的期望单击（未计目标防御），
        # 与 core.damage_schedule / _player_hit 同源，避免只有倍率没有实数。
        chips.append({"label": "当前", "value": str(int(dmg_now))})
    chips += fx.effect_chips(e.get("effects") or [])

    # 2026-11-15 用户纠正：「不要写原著多少章这种描述，你见过哪个游戏的
    # 技能描述这样写的？」——来源只取 skill_book.source（谁所使 / 怎么得到），
    # 由 desc_with_source 用逗号并进描述第一句，像游戏技能说明而不是小说注脚；
    # novel 章节引文只用于离线核验，绝不进卡片。有来源就加、没有就不加。
    phrase = fx.source_phrase(e)
    lv = max(1, min(5, int(sk.get(name, 1))))
    #: 2026-11-25 用户「等级展示改成解包游戏里发光/不发光的小元素」：
    #: 合并卡模板把 meta 末尾的 ``LvN`` 换成 5 段等级条（亮 N 段 + 暗 5-N 段）。
    #: 结构化字段给模板和测试用；纯文本兜底仍保留 ``LvN`` 字样。
    return {
        # 全局序号（1-based）= core.learned_no_map 的编号（**只含已领悟技**，
        # 「斗技」卡自己的连续顺序）。玩家发「上阵 1 3 5 7」按此号上阵，
        # 序号不再挂在宗门卡上（2026-11-25 用户：宗门卡序号导致上阵错位）。
        "no": no,
        "name": name,
        # 2026-11-26 用户：「不要搞普通 高阶 特殊的说法了，严格按照原著的阶级表达」。
        # badge/family 一律走原著阶级（黄/玄/地/天/秘法）；`tier` 仍保留，
        # 但它只是**数值档**（伤害倍率 / 斗气消耗 / 解锁等级），卡面不再显示它。
        "badge": (rank_mod.fam_of(rank) or "未")[:1],
        "family": rank_mod.fam_of(rank),
        # 完整原著阶位（"地阶高级"）：2026-11-27 分区标题要完整阶位，供分组用。
        "rank": rank,
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
        # 技能等级（仅 show_lv=True 的合并卡有意义；宗门池卡恒 None 不画条）
        "lv": lv if show_lv else None,
        # ---- 兼容通用列表卡 / 既有测试的扁平字段 ----
        "title": name,
        "right": state,
        "sub": ((f"异火技〔{e['flame']}〕 · " if e.get("flame") else "")
                + (f"〔{role}〕" if role else "") + meta),
        "note": " · ".join(f"{c['label']}{c['value']}" for c in chips[1:])
                + ((" ｜ 限：" + str(e.get("gate"))) if e.get("gate") else ""),
        "bar": {"cur": lv, "max": 5},
    }


def build_roster_data(p: dict) -> dict:
    """本宗斗技名录 → doupo_roster 专用名录卡参数。

    与 build_learned_data（「斗技」合并卡）的区别：这张画**本宗全部斗技**
    （含未解锁、已收服异火的主动技），用于「宗门斗技」口令；合并卡只画已上阵
    + 已领悟，不含"本宗还没学的"。
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
    # 2026-11-25 用户：去掉宗门斗技卡序号——本宗全池含未解锁技，序号跳号会让
    # 玩家照着上阵时错位。序号改由「斗技」（我的斗技）卡给出（learned_no_map，
    # 只含已领悟技、连续无空洞）。本卡不再传 no，_roster_row 收到 None 不画序号圆。
    pool_sorted = core.skill_pool_sorted(p)

    rows: list[dict] = []
    by_fam: dict[str, list[dict]] = {}
    for e in pool_sorted:
        unlocked = bool(e.get("unlocked"))
        equipped = bool(e.get("equipped"))
        # 两列版的状态 pill 只有 ~52px 宽，「未解锁 lv50」会撑爆块宽，
        # 因此状态只写短词，解锁等级挪到副行末尾（信息不丢、胶囊不挤）。
        if equipped:
            state, state_kind = "上阵", "on"
        elif unlocked:
            # 2026-11-26：旧文案「可学」被玩家读成「已学」，于是照着点名上阵
            # 却报未领悟。本卡是本宗名录，状态必须写明这些技还没领悟。
            state, state_kind = "未领悟", "learn"
        else:
            state, state_kind = "未解锁", "lock"
        row = _roster_row(e, None, sk, state, state_kind, show_lv=False,
                          plv=int(p.get("lv", 1)))
        rows.append(row)
        by_fam.setdefault(row["family"], []).append(row)

    # 2026-11-27 用户「阶级要标注完整，地阶高级，别只写个地阶」：
    # 分区标题按完整 rank（黄阶低级/…/天阶高级/秘法）分组，不再并到大阶。
    groups = _rank_groups(by_fam)

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
        # 卡脚注单行，长度与原 tip 持平（36 字），长了会被模板截断（真截图验过）。
        "tip": "未领悟先发「学习 <技名>」 ｜ 序号见「斗技」 ｜ 斗技详情 <技名>",
    }


async def render_roster(p: dict) -> bytes | None:
    """「宗门斗技」出图：本宗全部斗技名录卡（按阶位分组；2026-11-25 起不再画上阵序号）。"""
    return await _render_list("doupo_roster", build_roster_data(p), "斗破本宗斗技", qq=p.get("qq"))


def build_learned_data(p: dict) -> dict:
    """「斗技」合并卡 → doupo_roster 参数：已上阵 + 已领悟（跨宗）。

    2026-11-24 用户口径：原来的「斗技」（四槽上阵卡）与「斗技 全部」（已学习名录）
    合并为**一张卡**——玩家要看的是"我现在用什么 / 我学到了什么"，分两张卡反而
    要发两次口令。故本卡两个区块：
      ① 已上阵（state=上阵/on）：四槽真实出战技，含等级与完整数值；
      ② 已领悟（state=已领悟/learn）：其余已学技，按阶位分组，含转宗保留的他宗技。
    「宗门斗技」仍单独一张（只列本宗池，**不带上阵序号**），两者职责不重叠。
    """
    sk = p.get("skills") or {}
    # 2026-11-25 起「上阵 N」序号 = 本卡连续序号（只含已领悟技，无跳号）。
    # 与 cmd_loadout 共用 core.learned_no_map 唯一真值源，改动任一侧守门会抓。
    no_by_name = {name: no for no, name in core.learned_no_map(p).items()}
    # 四槽 entry 不带 unlocked 字段（loadout_entries 只保证是能上阵的技）；
    # 不补的话 _roster_row 会误判"未解锁"，副行印出「Lv1 lv1 解锁」重复信息。
    lo = [dict(e, unlocked=True) for e in core.loadout_entries(p)]
    lo_names = {e["name"] for e in lo}

    # 当前等级期望单击（与 build_skills_data 同一公式，两卡数字不会打架）：
    #   伤害 = (力量×0.5 + 斗气×1.5) × 阶倍率 × (1+0.2/级) × 暴击期望
    st = core.stats(p)
    _raw_base = st["力量"] * 0.5 + st["斗气"] * 1.5
    _crit = 1.0 + core.crit_rate(p) / 100.0 * 0.5

    def _dmg_now(e: dict) -> int:
        lv = max(1, min(5, int(sk.get(e["name"], 1))))
        mult = float(e.get("dmg") or 0) * (1 + 0.2 * (lv - 1))
        return int(round(_raw_base * mult * _crit))

    groups: list[dict] = []
    rows: list[dict] = []

    # ① 已上阵（四槽顺序即出战顺序，不再排序）
    on_rows = [_roster_row(e, no_by_name.get(e["name"]), sk,
                           "上阵", "on", show_lv=True, dmg_now=_dmg_now(e),
                           plv=int(p.get("lv", 1))) for e in lo]
    if on_rows:
        groups.append({"tier": "已上阵", "label": "已上阵",
                       "count": len(on_rows), "icon": "bolt", "rows": on_rows})
        rows.extend(on_rows)

    # ② 其余已领悟，按原著阶级稳定排序（与「宗门斗技」同序，便于对照）
    by_fam: dict[str, list[dict]] = {}
    for e in core.equippable_entries(p):
        if e["name"] in lo_names:
            continue
        row = _roster_row(e, no_by_name.get(e["name"]), sk,
                          "已领悟", "learn", show_lv=True, plv=int(p.get("lv", 1)))
        by_fam.setdefault(row["family"], []).append(row)
        rows.append(row)
    # 2026-11-27 完整阶位分组（地阶高级而非只写地阶），与宗门卡同一口径
    groups.extend(_rank_groups(by_fam))

    learned = core.learned_skill_pool(p)
    n_ben = sum(1 for e in learned if e.get("origin") == "本宗")
    n_other = sum(1 for e in learned if e.get("origin") == "他宗")
    n_common = sum(1 for e in learned if e.get("origin") == "通用")
    return {
        "icon": "bolt", "title": "我的斗技",
        "subtitle": (f"已上阵 {len(on_rows)}/{cfg.LOADOUT_SIZE} · 已领悟 {len(learned)} 门"
                     f"（本宗 {n_ben} / 他宗 {n_other} / 通用 {n_common}）"),
        "groups": groups,
        "rows": rows,
        "tip": "序号即上阵编号：发「上阵 1 3 5 7」按号选技 ｜ 斗技详情 <技名> 看招式来历",
    }


async def render_learned(p: dict) -> bytes | None:
    """「斗技」出图：已上阵 + 已领悟合并卡（一张卡看完我的斗技全貌）。"""
    return await _render_list("doupo_roster", build_learned_data(p), "斗破我的斗技", qq=p.get("qq"))


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

    # 本门斗技统计（按原著大阶：黄/玄/地/天/秘法 门数）
    # 2026-11-26 用户：「不要搞普通 高阶 特殊的说法了」——这三格数字原先写的是
    # 内部数值档，玩家看不懂也不对应原著，改成五个大阶。
    pool_sorted = core.skill_pool_sorted(p)
    fam_counts = {f: sum(1 for e in pool_sorted if rank_mod.fam_of(e.get("rank")) == f)
                  for f in rank_mod.FAMILIES}
    fam_order = list(rank_mod.FAMILIES)

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
        "tier_counts": fam_counts,      # 键换成原著大阶，模板同步按 5 格画
        "fam_order": fam_order,
        "total_skills": len(pool_sorted),
        "open_pools": open_pools,
        "lv": lv,
        "groups": groups,
        "switch_cost": cost,
        "tip": "用法：转宗 <宗门名>（只换师门并重配四槽，已升技能等级按技名保留）",
    }


async def render_faction(p: dict) -> bytes | None:
    """「宗门」出图：宗门概览 / 斗技统计 / 代表人物 / 传承异火 / 可转宗门。"""
    return await _render_list("doupo_faction", build_faction_data(p), "斗破宗门", qq=p.get("qq"))

def build_flames_data(p: dict) -> dict:
    """异火列表 → doupo_list 模板参数（2026-11-26 整体重构：只列已收服）。

    用户「现在看着很乱，没获得的就不要显示出来了」。旧版把 11 朵没到手的火连同
    「未收服」状态、获取提示、三个分类标题全铺在卡上，玩家真正拥有的火被挤到
    看不见的位置。现在只列 **已收服**（只列 lv>0 的火，按 招式异火 → 数值异火 →
    兽火/新生 排序）；一朵都没有时给一条「尚未收服」+ 获取路径，不铺 11 行「未收服」。

    🚨 2026-11-28 反转：异火不走宗门传承，**任何宗门都出卡**，卡面不再有
    【本宗传承】行（用户「异火都不走宗门传承了就不要展示传承，容易误导玩家」）。
    数值口径仍只做展示，加成聚合唯一源是 :mod:`flame_bonus`，这里不重算数值。
    """
    fl = p.get("flames") or {}
    dross = core.count_item(p, "药渣")

    rows: list[dict] = []
    owned: list[dict] = []

    def _lv_right(lv: int) -> str:
        return f"{lv}/{cfg.FLAME_MAX_LV} 级" + ("（圆满）" if lv >= cfg.FLAME_MAX_LV else "")

    def _need_note(lv: int, need: int) -> str:
        return "" if lv >= cfg.FLAME_MAX_LV else \
            f"下级需 {commands._fmt(int(need) * (lv + 1))} 药渣"

    # ① 招式异火（S/A/B 榜位火，走招式与剧情特效）
    for f in cfg.FLAMES:
        name = f["name"]
        if name == cfg.FLAME_DIYAN:
            if not p.get("diyan"):
                continue
            owned.append({"title": name, "right": "已得",
                          "sub": f["desc"], "note": "", "image": name})
            continue
        lv = int(fl.get(name, 0))
        if lv <= 0:
            continue
        owned.append({"title": name, "right": _lv_right(lv), "sub": f["desc"],
                      "note": _need_note(lv, int(f["need"])), "image": name})

    # ② 数值异火（B 线） / ③ 兽火+新生异火：desc 本身已写明「炼药成功率每级 +N%」，
    #    不再额外拼一行加成前缀（拼了就是同一个数字说两遍，卡面更乱）。
    for f in list(getattr(cfg, "BONUS_FLAMES", [])) + list(getattr(cfg, "OTHER_FLAMES", [])):
        lv = int(fl.get(f["name"], 0))
        if lv <= 0:
            continue
        owned.append({"title": f["name"], "right": _lv_right(lv),
                      "sub": f["desc"], "note": _need_note(lv, int(f["need"])),
                      "image": f["name"]})

    if owned:
        rows.append({"title": "已收服", "right": "", "head": True,
                     "sub": "", "note": ""})
        rows.extend(owned)
    else:
        rows.append({"title": "尚未收服异火", "right": "无",
                     "sub": "发「收服 <火名>」认主，或九星突破、炼制兽火单方可得火种。",
                     "note": ""})

    return {
        "icon": "star", "title": "异火",
        "subtitle": f"已收服 {len(owned)} 朵 · 药渣 ×{dross}",
        # 2026-11-27 接图：整卡左侧让一条图槽（分类标题行一起让，否则标题起点
        # 参差），每行带自己的火图。没图的行（分类标题）就是空槽。
        "row_icon": True,
        "rows": rows,
        "tip": commands.flame_usage_hint(p),
    }



def _shop_is_beast(name: str) -> bool:
    """该商品是否「兽火单方材料」（2026-11-24 新增 28 种，单独一卡防爆高）。"""
    from . import config as _cfg
    return str((_cfg.HERB_BY_NAME.get(name) or {}).get("src") or "").startswith("兽火单方")


def build_shop_data(p: dict, beast: bool = False) -> dict:
    """宝库列表 → doupo_list 模板参数。

    ``beast``（2026-11-24）：兽火单方材料 28 种单独成卡。93 种挤一卡时
    w=560 高度 4744px 远超可读上限（3600），且玩家一次看不完。
    """
    rows = []
    for it in core.shop_list():
        if _shop_is_beast(it["name"]) != bool(beast):
            continue
        rows.append({"title": it["name"], "right": f"{it['price']} 金", "sub": it["desc"]})
    n_all = len(core.shop_list())
    n_this = len(rows)
    other = n_all - n_this
    return {
        "icon": "cart", "title": "随身商店·兽火材料" if beast else "随身商店",
        "subtitle": (f"金币 {int(p.get('gold', 0))} · 兽火材料 {n_this} 种"
                     if beast else
                     f"金币 {int(p.get('gold', 0))} · 药材 {n_this} 种"),
        # 2026-11-16：原著药材补录后 50+ 种，单列会到 5000px 不可读；双列排版。
        "columns": 2,
        "rows": rows,
        "tip": ("用法：买 蓝晶草 2　｜　发「宝库」看基础药材" if beast
                else f"用法：买 血灵草 2　｜　兽火材料 {other} 种发「宝库 兽火」"),
    }


def build_bag_data(p: dict, locked=None) -> dict:
    """背包列表 → doupo_list 模板参数（2026-11-24 用户口径：**不翻页、一卡到底**）。

    ``locked``：被锁定的物品名集合（2026-11-19）。

    用户明确的两条视觉规则（照多数游戏的做法）：
      * **带纹丹不改名**：格子里仍显示基础丹名，只在右上角用标题同族金丝小字
        标「一纹/二纹/三纹」，几纹就写几（``corner_tr``）；
      * **锁定不写前缀**：左上角一个「锁」字（``corner_tl``），别把名字挤掉。

    🚨 2026-11-25 用户明确：「别改我背包的丹药展示效果，那是我的独特设计，只是让你
    改口令和命名，不要改其他的。」⇒ **物品真名**（存档键 / 口令输入）是前缀式
    ``一纹破厄丹``；但**背包格子里的展示**仍是他定的那一套 —— 基础丹名 + 右上角
    金色光晕纹字角标。两者不是一回事，别再"统一"成一种（会把他的设计删掉）。

    丹药说明与 ``cmd_bag`` 同源，走 ``cfg.recipe_impl`` —— 卡面只印「本作真会
    发生的事」；直接用 ``desc``（原著承诺）会违反 2026-11-18 的诚实契约。
    """
    locked = set(locked or ())
    items = bagview.sorted_items(p)
    # 2026-11-23：按 RECIPE_BY_NAME 遍历，别名（复紫灵丹）也有说明，不会在包裹里变哑巴。
    known = {n: (cfg.recipe_impl(r) or r.get("desc", ""))
             for n, r in cfg.RECIPE_BY_NAME.items()}
    known.update({h["name"]: h["desc"] for h in cfg.HERBS})
    rows = []
    for name, n in items:
        tier = cfg.danwen_tier(name) if cfg.is_danwen_name(name) else 0
        # 显示名一律剥掉丹纹后缀（用户：丹药名称不要变），纹数交给右上角角标
        shown = cfg.base_of_danwen(name) if tier else name
        row = {"title": f"{shown} ×{n}", "sub": known.get(name) or known.get(shown, "")}
        # 2026-11-27 接图：格子里的物品图（带纹丹用基础丹名的美术；药材/材料/杂项
        # 没有图，模板查不到就整块不画）。名称与右上角纹字角标仍是用户定的那一套。
        row["image"] = shown
        if tier:
            row["corner_tr"] = cfg.DANWEN_TIERS[min(3, tier) - 1]
            row["sub"] = "丹纹丹：本效之外，服下另得随机限时/限次赐福"
        # 锁定：只认真正被锁的那个名字（普通丹与带纹丹是两件独立的物品）
        if name in locked or shown in locked:
            row["corner_tl"] = "锁"
        rows.append(row)
    return {
        "icon": "diamond", "title": "包裹",
        "subtitle": (f"{len(items)} 种物品 · 丹药优先 · 金币 {int(p.get('gold', 0))}"),
        # 用户「背包别搞翻页，延长」：4 列网格一卡到底，行数随物品数自然增长。
        "columns": 4,
        "grid": True,
        "rows": rows,
        "tip": "服丹 <丹名> 使用 · 锁定 <丹名> 后战斗不自动服用 · 左上「锁」=已锁定",
    }


async def render_bag(p: dict, locked=None) -> bytes | None:
    return await _render_list("doupo_list", build_bag_data(p, locked), "斗破背包",
                              qq=p.get("qq"))


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
    """四槽上阵**详情卡**（doupo_skills：完整描述 + 五段斗气条 + 伤害口径）。

    ⚠️ 2026-11-24 起「斗技」口令改出合并卡（``render_learned``），本函数已无
    口令入口；保留是因为 doupo_skills 模板仍被排版验收（test_typography）当作
    "五段等级条 + 大字粗体"样本，且将来若要开"上阵详情卡"可直接复用。
    改这里不影响玩家可见的「斗技」——那张卡走 build_learned_data。
    """
    # 专用模板：技能描述 + 详细伤害 + 五段斗气条 + 重点大字粗体。
    # 不再复用 doupo_list，避免把异火/宝库/背包/排行一起改坏。
    return await _render_list("doupo_skills", build_skills_data(p), "斗破斗技", qq=p.get("qq"))


async def render_flames(p: dict) -> bytes | None:
    return await _render_list("doupo_list", build_flames_data(p), "斗破异火", qq=p.get("qq"))


async def render_shop(p: dict, beast: bool = False) -> bytes | None:
    return await _render_list("doupo_list", build_shop_data(p, beast), "斗破宝库", qq=p.get("qq"))



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
      * 齐备标记 = ``core.craft_flame_gate`` + ``core.count_item`` 逐味判
        （与纯文本 / 单发 / 批量 `craft` 判据逐字一致，2026-11-26 起含火焰闸）；
      * 功效标签 = ``cfg.effect_tag``，单丹成功率 = ``cfg.craft_rate_for``
        （2026-11-18 原著口径：够格用基础率、越 1 档×0.35、越≥2 档×0.05）。
        标签与成功率都拼进 ``desc``，由模板自适应折行，不动坐标骨架。

    2026-11-16 用户要求「等级不够不展示 / 丹药多了分页 / 两列展示」：
    低阶丹药由 ``available_recipes`` 直接过滤掉，卡上只出现当前境界可用者；
    每页固定 ``cfg.RECIPES_PER_PAGE`` 枚，模板按两列网格排布，页码信息由
    ``page`` / ``total_pages`` / ``total_avail`` 透出（供卡面与翻页按钮共用）。

    2026-11-25 用户「炼药的卡片就只展示丹方和丹药本身效果就行，别写其他废话，
    丹药卡片收紧，布局紧凑点，别搞得翻页太多了」——
    卡面 ``desc`` **只印** ``cfg.recipe_impl``（本作真会发生的事），
    功效标签 / 把握 / 越档 / 丹纹率全部撤出卡面：那些是「这一炉风险多大」的
    决策信息，看一次就够，印在每一格上只会把两列挤成四行、翻页翻到烦。
    它们仍在「炼药」纯文本与「查看 <丹名>」详情卡里（分场景给，不重复给）。
    """
    alch_idx = int(p.get("alch", 0))
    alch_label = cfg.alch_label(alch_idx)
    chunk, page, total_pages = core.recipes_page(p, page)
    total_avail = len(core.available_recipes(p))

    groups = []
    for grade in cfg.GRADE_ORDER:
        rows = []
        for r in chunk:
            if r["grade"] != grade:
                continue
            need = r.get("need") or {}
            # 2026-11-26 矛盾修复：空火玩家材料齐备时旧代码亮「可炼✅」，真炼却报
            # nofire。闸口统一走 core.craft_flame_gate，与单发/批量完全同源。
            gate = core.craft_flame_gate(p, r)
            rows.append({
                "name": r["name"],
                "mats": [[k, int(v)] for k, v in need.items()],
                # 只印本效（唯一源 cfg.recipe_impl，2026-11-18 诚实契约不变）
                "desc": cfg.recipe_impl(r),
                "ready": (not gate
                          and all(core.count_item(p, k) >= int(v) for k, v in need.items())),
                "gate": gate,
                # 无药方丹药：卡面改为「N 金购买」而非空材料行（与原著考据口径一致）
                "buyonly": not bool(need),
                "buy": int(r.get("buy", 0)),
            })
        if rows:
            groups.append({"grade": grade, "rows": rows})

    # 每页丹药数 = 当前页实际条数；卡面与脚注都读它，杜绝「算的画得下」三处不一致
    shown = sum(len(g["rows"]) for g in groups)
    base_tip = (("第 %d/%d 页 · 本页 %d 枚 · 可用丹方 %d 枚" %
                 (page, total_pages, shown, total_avail)) if total_pages > 1 else
                ("可用丹方 %d 枚 · 炼制 <丹名> / 买 <丹名>" % total_avail))
    # 2026-11-26 黑盒 P5：新手第一眼全是「缺料」却不知火从哪来、药去哪买。
    # 只在第一页且确实没有可炼丹方时补一句可执行引导（老玩家/可炼时不打扰）。
    if page == 1 and not any(r["ready"] for g in groups for r in g["rows"]):
        base_tip += " · 火：九星胜敌/兽火单方；药材：宝库买"
    return {
        "icon": "wand",
        "title": "炼药",
        "subtitle": ("%s · 低阶更稳/越阶骤降 · 药渣 ×%d" %
                     (alch_label, core.count_item(p, "药渣"))),
        "page": page,
        "total_pages": total_pages,
        "total_avail": total_avail,
        "shown": shown,
        "groups": groups,
        "tip": base_tip,
    }


async def render_recipes(p: dict, page: int = 1) -> bytes | None:
    """「炼药/丹方」出图：等级过滤 + 分页 + 两列丹方卡。失败降级纯文本由调用方保证。"""
    return await _render_list("doupo_recipes", build_recipes_data(p, page), CARD_RECIPES_NAME, qq=p.get("qq"))


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


async def render_battle(last_battle: dict, qq: str | None = None) -> list[bytes]:
    """「战斗回看」出图：返回**每页** PNG（短战报就一页，长战报按回合分页）。

    分页口径见 ``pic.templates.doupo_battle.split_pages``：切点只在回合边界，
    首页保留结算区，续页只留胜负徽标 + 逐招明细（顶部印「第 x/N 页 · 回合 a-b」）。
    出图失败返回 ``[]``，调用方回纯文本兜底（完整逐招不丢）。

    2026-11-25 主题修复：``qq`` 必须透传给 ``_render_pages``（否则个人主题
    口令对战斗回看卡无效——只跟随全局默认），由 ``tools/check_card_theme.py``
    静态闸门守。
    """
    return await _render_pages("doupo_battle", build_battle_data(last_battle),
                              CARD_BATTLE_NAME, qq=qq)


async def render_battle_for(qq: str, p: dict | None) -> list[bytes]:
    """战斗回看「按玩家取数」的唯一出卡入口（按钮点击用）。

    ``render_battle`` 首参是 ``last_battle`` 而不是玩家 dict —— 按钮分发
    **不许**统一 ``renderer(p)`` 糊过去（会把玩家当战报渲出假卡，且不报错）。
    无战报返回 ``[]``，调用方回纯文本兜底，绝不编造卡面。
    """
    last = (p or {}).get("last_battle")
    if not isinstance(last, dict) or not last.get("log"):
        return []
    return await render_battle(last, qq=qq)


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
            int(pu.get("tier", 0)), cfg.PUPPET_TIER_BY_IDX[0])
        body = "%s（%s%s）" % (
            pu.get("body_name") or "—", pu.get("body_realm") or "凡躯",
            ("·%d星" % pu["body_star"]) if pu.get("body_star") else "")
        # atk / 护主都传 p：与 ``core.fight`` 同一个 p、同一个 guard_profile，
        # 卡面数字与实战掷的骰子是同一份（红线：只改一处必红）。
        g = pc.guard_profile(pu, p, pc.formation_count(roster))
        _si = pc.soul_feed_info(pu)
        out["puppets"].append({
            "name": pc.display_name(pu),
            "badge": "%s · %s皮%s" % (tier_name, skin, " · 出战" if pu.get("id") == elig.get("active_id") else ""),
            "atk": str(int(pc.puppet_attack(pu, p))),
            "lines": [
                # 战力对标（2026-11-20 第三轮）：数字得有个「几品战力」的量纲，
                # 否则「肉身攻击 28357」对玩家只是一个无量纲的数。标签走**实际
                # 发挥**（puppet_realm_label 会按玩家境界压制同步降档）。
                "战力 " + pc.puppet_realm_label(pu, p),
                "躯体 " + body,
                "魔核 %d阶%s系 · 灵魂 %s" % (int(pu.get("core_tier", 0)),
                                              pu.get("core_attr") or "无",
                                              pu.get("soul_source") or "—"),
                # 护主一行（2026-11-20 第三轮）：气血/护甲/挡伤率缺一个都是隐式数值
                "护主 气血%d · 护甲%d · 挡伤%.0f%%" % (
                    g["hp_max"], g["armor"], g["chance"] * 100),
                "雷淬纯度 %d%% %s%s · %s%s%s" % (
                    int(pu.get("purity", 0)),
                    # P1-2 渐进染色（N06）：按纯度画银→金进度格
                    pc.dye_bar(int(pu.get("purity", 0))),
                    (" 紫纹%d层" % int(g["vein"])) if int(g.get("vein") or 0) else "",
                    "已认主·永不背叛" if pu.get("bonded") else "未认主",
                    " · 有灵" if pu.get("awakened") else "",
                    # P1-1：战损在 PNG 名录卡上可见（此前只有文本名录卡有）
                    " · ⚠受损待雷淬修复" if pu.get("damaged") else ""),
                # P3 养魂：文本卡与 PNG 卡同一份 soul_feed_info，卡面不再各算一套
                "养魂 %d/%d%s · 有效灵魂 %d" % (
                    _si["fed"], _si["cap"], "（已满）" if _si["maxed"] else "",
                    _si["total"]),
                # 2026-11-27 用户「我都不知道养魂是干啥的怎么操作」：数值行旁补一条
                # 口径说明。常量取真源，不硬编码；说明走 lines（_wrap 折行 + 内容驱动高度），
                # 不塞进单行 tip（_fit 会截断）。告知「是什么/用什么/怎么发/涨多少」。
                "养魂：发「傀儡 养魂」渡【%s】，+%d灵魂/枚（上限%d，攻血甲同涨）" % (
                    cfg.PUPPET_SOUL_PILL, int(cfg.PUPPET_SOUL_FEED_GAIN),
                    int(cfg.PUPPET_SOUL_FEED_MAX)),
            ],
            "active": bool(pu.get("id") == elig.get("active_id")),
        })
    if not roster and p is not None:
        # 2026-11-26：文本版列五道、卡片只列四道，卡上却写「五道资格齐备」。
        # 与 commands_puppet.cmd_puppet / puppet_gate 同源补齐第五道「斗气」。
        checks = [
            ("境界", pc.gate_realm(p, 0)), ("灵魂境界", pc.gate_soul(p)),
            ("控火", pc.gate_fire_ctrl(p)), ("异火", pc.gate_flame(p)),
            ("斗气", pc.mp_endurance_ok(p, 0)),
        ]
        out["gates"] = [{"name": name, "ok": ok, "why": reason}
                        for name, (ok, reason) in checks]
        out["tip"] = "五道资格齐备后发「炼制傀儡」· 材料去「傀儡遗迹」攒"
    return out


async def render_puppet(roster: list, elig: dict, p: dict | None = None,
                        qq: str | None = None) -> bytes | None:
    """「傀儡」出图：名录卡；未拥有时渲染五道资格，绝不降级为纯文字空回。

    2026-11-25 主题修复：新增 ``qq`` 显式参数（兼容旧调用从 ``p`` 里取）。
    按钮分发没有 ``p`` 时，靠 ``render_puppet_for(qq)`` 把 qq 传进来。
    """
    return await _render_list("doupo_puppet",
                              build_puppet_data(roster, elig, p), CARD_PUPPET_NAME,
                              qq=qq or (p or {}).get("qq"))


CARD_CODEX_NAME = "斗破傀儡图鉴"


def build_codex_data(mats: dict | None = None, p: dict | None = None) -> dict:
    """傀儡图鉴 → doupo_list 模板参数（2026-11-26 重做）。

    用户对上一版的两条意见：**「做的是个什么垃圾设计，搞这么长」** +
    **「已拥有材料名称还没高亮」**。所以这一版：

      * **双列**（``columns=2``）——18 条单列铺 2000+px，双列 9 行收进一屏；
      * **每条两行**：标题行（配方名 + 右侧档位／可炼）+ 一行材料。删掉原来那条
        独立的「已备：…」——它既多占一行，颜色又是与正文同档的暗红，跟没高亮一样；
      * 材料**名称行内分色**（``sub_segs``）：够数的 = 金色且写成「名 持有/需」，
        没有的 = 灰且只写「名×需」。扫一眼就知道哪条能开炉。

    材料数量口径与 ``commands_puppet.cmd_puppet_codex`` / ``puppet_core`` 同源，
    不在渲染层另算。
    """
    from . import puppet_core as pc

    mats = {str(k): int(v or 0) for k, v in (mats or {}).items()}
    rows = []
    ready_n = 0
    for r in cfg.PUPPET_RECIPES:
        slots = pc.recipe_slots(r)
        tier = pc.recipe_tier(slots)
        tier_label = (cfg.PUPPET_TIER_BY_IDX[max(0, tier)][0]
                      if tier >= 0 else "五维不全")
        need = pc.recipe_materials(r)
        segs: list[tuple[str, str]] = []
        ready = True
        for n, q in need.items():
            have = int(mats.get(n, 0) or 0)
            ok = have >= q
            ready = ready and ok
            if segs:
                segs.append(("　", "dim"))
            segs.append((("%s %d/%d" % (n, have, q)) if have > 0 else ("%s×%d" % (n, q)),
                         "gold" if ok else "dim"))
        ready_n += 1 if ready else 0
        rows.append({
            "title": r["name"],
            "right": tier_label.split("傀儡")[0] + ("·可炼" if ready else ""),
            "sub_segs": segs,
            "accent": ready,
        })

    return {
        "icon": "shield",
        "title": "傀儡图鉴",
        "columns": 2,
        "subtitle": "共 %d 条配方 · 已备齐 %d 条（金色＝已拥有的料）" % (
            len(cfg.PUPPET_RECIPES), ready_n),
        "rows": rows,
        "tip": "发「炼制傀儡 <配方名>」开炉 · 金色材料＝已拥有且够数",
    }


async def render_codex(mats: dict | None = None, p: dict | None = None,
                       qq: str | None = None) -> bytes | None:
    """「傀儡图鉴」出图：双列配方卡（2026-11-26 重做，两行一条）；失败由调用方回文本。"""
    data = build_codex_data(mats, p)
    return await _render_list("doupo_list", data, CARD_CODEX_NAME, qq=qq)


async def render_codex_for(qq: str, p: dict | None = None,
                           pstore=None) -> bytes | None:
    """按 qq 取材料再出图（按钮点击走 button_dispatch 的 codex 特判）。"""
    from .puppet_store import PUPPET_STORE
    store = pstore or PUPPET_STORE
    mats = await store.materials(str(qq)) or {}
    return await render_codex(mats, p, qq=qq)


CARD_RUIN_NAME = "斗破傀儡遗迹"


def build_ruin_data(state: dict, day: str = "", report: dict | None = None) -> dict:
    """遗迹当日状态 → doupo_ruin 模板参数；段落与掉落只读 cfg 唯一源。

    ``report``（2026-11-18）：本次挑战的战况（``cmd_puppet_ruin(report=...)`` 写入）。
    有它就并成首段「本次挑战」——用户要求**点一次只出一条内容**，战报不许另发一条文字。
    """
    stages = cfg.PUPPET_RUIN_STAGES
    done = int((state or {}).get("stage", 0))
    total = len(stages)
    groups = []
    if report:
        groups.append({"name": "本次挑战", "rows": _ruin_report_rows(report, done=done)})
    # 结构化段列表（遗迹专用富版式用）：气泡渲染读这里，不再靠解析 rows 文本。
    # 每段 status: done=已通关 / current=可挑战（本次要打的）/ locked=未解锁。
    stage_cards = []
    for i, sg in enumerate(stages):
        status = "done" if i < done else ("current" if i == done else "locked")
        status_text = "已通关" if status == "done" else ("可挑战" if status == "current" else "未解锁")
        drops = [(str(k), int(v)) for k, v in (sg.get("drops") or {}).items()]
        stage_cards.append({
            "no": i + 1,   # 序号圆标用阿拉伯数字（汉字「一」在圆标里像减号，视觉歧义）
            "ord": "一二三四五六七八九十"[i] if i < 10 else str(i + 1),
            "name": str(sg["name"]),
            "desc": str(sg.get("desc") or ""),
            "monster": str(sg["monster"]),
            "drops": drops,
            "status": status,
            "status_text": status_text,
        })
        drop_txt = "、".join("%s×%d" % (k, v) for k, v in drops)
        groups.append({
            "name": "%s · %s" % (stage_cards[-1]["ord"], sg["name"]),
            "rows": [stage_cards[-1]["desc"],
                     "%s ｜ 掉 %s ｜ %s" % (sg["monster"], drop_txt, status_text)],
        })
    return {
        "title": "傀儡遗迹",
        "subtitle": "今日进度 %d/%d%s" % (done, total, (" · " + day) if day else ""),
        # 富版式字段：进度气泡条 + 分段气泡。groups 保留供兼容与外部调用。
        "progress": {"done": done, "total": total, "day": str(day or "")},
        "stage_cards": stage_cards,
        "report": report or {},
        "groups": groups,
        "tip": "发「傀儡遗迹」自动连打至力竭 · 发「傀儡」看名录 · 发「炼制傀儡」开炉",
    }


def _ruin_report_rows(report: dict, done: int | None = None) -> list:
    """本次挑战 → 卡片行（胜负/回合/承伤/掉落），与文本战报同一批字段。

    2026-11-20 第九轮：遗迹改为自动连打，``report["runs"]`` 一段一条。
    老的单段 report（只有 stage_idx/win/rounds）仍照旧解析，兼容既有测试与外部调用。

    2026-11-22 用户反馈「连过 1/7 展示不对，应该 6/7」：分母是**今日累计通关段数**
    （state.stage），不是本次 runs 里赢了几段。玩家从第 6 段水位线接着打，本次只赢
    1 段，但今日已通关 6 段——那句应读作「今日累计 6/7」，本次连胜另起一句说明。
    ``done`` 由 build_ruin_data 传入；缺省时回退旧口径，兼容直接调本函数的测试。
    """
    rep = report or {}
    total = int(rep.get("total") or len(cfg.PUPPET_RUIN_STAGES))
    runs = list(rep.get("runs") or [])
    if runs:
        wins = [r for r in runs if r.get("win")]
        loss = next((r for r in runs if not r.get("win")), None)
        if done is None:
            head = "本次自动挑战 · 连过 %d/%d 段" % (len(wins), total)
        else:
            # 主数字 = 今日累计通关（用户要的 6/7）；本次连胜作补充，不再当主计数。
            head = "本次自动挑战 · 今日累计 %d/%d 段" % (int(done), total)
            if len(wins) != int(done):
                head += "（本次再连胜 %d 段）" % len(wins)
        if loss is not None:
            head += " · 第 %d 段力竭" % (int(loss.get("stage_idx") or 0) + 1)
        elif len(wins) >= total:
            head += " · 今日全通"
        rows = [head]
        for r in runs:
            rows.append("第 %d 段 %s ｜ %s ｜ %d回合%s" % (
                int(r.get("stage_idx") or 0) + 1,
                str(r.get("stage_name") or "傀儡遗迹"),
                "✅ 胜" if r.get("win") else "❌ 力竭",
                int(r.get("rounds") or 0),
                "" if r.get("win") else " · 敌余血 %s" % _fmt_hp(r.get("mon_hp_left"))))
        agg: dict = {}
        for r in runs:
            for k, v in (r.get("drops") or {}).items():
                agg[str(k)] = agg.get(str(k), 0) + int(v)
        if agg:
            rows.append("累计掉落：" + "、".join("%s×%d" % (k, v) for k, v in agg.items()))
        if rep.get("lore"):
            rows.append("📜 得《天妖傀》竹简 · 发「傀儡 收服」降服残傀")
        return rows

    idx = int(rep.get("stage_idx") or 0)
    name = str(rep.get("stage_name") or "")
    mon = str(rep.get("monster") or "")
    if rep.get("cleared") and not rep.get("rounds"):
        return ["今日遗迹已全通（%d/%d）· 明日再来" % (total, total),
                "已通关的段落不再掉材料，明天刷新后继续"]
    head = "第 %d 段 · %s ｜ %s" % (idx + 1, name or "傀儡遗迹", mon or "守关者")
    if not rep.get("win"):
        return [head + " ｜ ❌ 力竭",
                "%d 回合 · 敌余血 %s · 今日仍在第 %d 段，调息后可再来"
                % (int(rep.get("rounds") or 0), _fmt_hp(rep.get("mon_hp_left")), idx + 1)]
    drops = rep.get("drops") or {}
    drop_txt = "、".join("%s×%d" % (k, int(v)) for k, v in drops.items()) or "无"
    rows = [head + " ｜ ✅ 胜",
            "%d 回合 · 承伤 %s · 今日进度 %d/%d"
            % (int(rep.get("rounds") or 0), _fmt_hp(rep.get("taken")), idx + 1, total),
            "实际掉落：" + drop_txt]
    if rep.get("lore"):
        rows.append("📜 得《天妖傀》竹简 · 遗迹残傀可发「傀儡 收服」降服")
    return rows


def _fmt_hp(v) -> str:
    try:
        return "{:,}".format(int(v or 0))
    except (TypeError, ValueError):
        return "0"


async def render_ruin(state: dict, day: str = "", report: dict | None = None,
                      qq: str | None = None) -> bytes | None:
    """「傀儡遗迹」出图：战况 + 三段进度卡，与战报文字同源（同一 stage 水位线）。

    2026-11-25 主题修复：``qq`` 必须透传，否则个人主题口令对遗迹卡无效。
    """
    return await _render_list("doupo_ruin", build_ruin_data(state, day, report),
                              CARD_RUIN_NAME, qq=qq)


CARD_CRAFT_NAME = "斗破傀儡炼制"


def build_craft_data(report: dict | None) -> dict:
    """成傀结果 → doupo_craft 模板参数（2026-11-20 第十一轮）。

    取数唯一源是 ``commands.cmd_craft_puppet(report=...)`` 写下的那份 dict——
    卡片**不重算**攻击力 / 用料 / 斗气消耗。重算就是第二套口径（第三轮已经吃过
    「卡面数字与实战两张皮」的亏），也等于把「这一炉到底扣了什么」变成猜测。

    没有 ``ok`` 标记时返回空 dict：失败 / 引导类回复本来就是逐条文本，不该出卡。
    """
    from . import puppet_core as pc  # 局部导入：本函数作用域没有 pc（勿依赖别处的 import）
    rep = dict(report or {})
    if not rep.get("ok"):
        return {}
    tier_name = str(rep.get("tier_name") or "")
    skin = str(rep.get("skin") or "")
    label = str(rep.get("realm_label") or "")
    head = ["【%s】%s档 · %s皮" % (rep.get("name") or "傀儡", tier_name, skin),
            ("肉身攻击 %d　%s" % (int(rep.get("atk") or 0), label)) if label
            else ("肉身攻击 %d" % int(rep.get("atk") or 0)),
            "不吃斗气、不施斗技 —— 傀儡只以肉身搏杀"]
    body = str(rep.get("body_name") or "")
    if body:
        star = int(rep.get("body_star") or 0)
        head.append("躯体：%s（%s%s）" % (
            body, rep.get("body_realm") or "凡躯",
            ("·%d星" % star) if star else ""))
    state = "已认主·永不背叛" if rep.get("bonded") else "未认主"
    if rep.get("awakened"):
        state += "　有灵（北王式）"
    head.append("雷淬纯度 %d%% %s　%s" % (
        int(rep.get("purity") or 0), pc.dye_bar(int(rep.get("purity") or 0)), state))

    cost = rep.get("cost") or {}
    groups = [
        {"name": "成傀", "rows": head},
        {"name": "用料", "rows": [
            "、".join("%s×%d" % (k, int(v)) for k, v in cost.items()) or "—",
            "斗气 -%d（余 %d）" % (int(rep.get("need_mp") or 0),
                                  int(rep.get("mp_left") or 0)),
        ]},
    ]
    down = str(rep.get("downgrade") or "")
    if down:
        groups.append({"name": "降档提醒", "rows": [down]})
    groups.append({"name": "下一步", "rows": [
        "发「傀儡 淬炼」以雷淬石提纯升档（每淬扣 %d 枚）" % int(cfg.PUPPET_TEMPER_STONE),
        "发「傀儡」看名录与护主面板；发「傀儡 结阵」看合击",
    ]})
    sub = "%s%s · %s·%s · id=%d" % (
        "自选材料 · " if rep.get("custom") else "",
        rep.get("recipe") or "", tier_name, skin, int(rep.get("pid") or 0))
    return {
        "title": "傀儡炼制",
        "subtitle": sub,
        "groups": groups,
        "tip": "发「傀儡」看名录 · 发「傀儡 淬炼」雷淬升档 · 发「傀儡 配方」看全部配方",
    }


# ---------------------------------------------------------------------------
# 「查看 <物品名>」物品详情卡（2026-11-25 用户要求）
# ---------------------------------------------------------------------------
#: 用户原话：「加一个查看物品的功能，无论是背包还是宝库还是丹药，这些物品，
#: 都加一个查看物品详情的功能……有什么效果，几纹有什么概率出什么加成。从何而来。
#: 其他物品都一样……尽量展示详细，必须结合原著，但是不要写明原著多少章什么的，
#: 就以药老或者普通商家的口吻描述就行。」
#:
#: 由此定下三条硬口径：
#:   1. **药老口吻**：原著味放在「本效」段的一句话里（`药老言：<desc>`），
#:      不写章节号 —— config 里的 ``src`` 字段（「原著第230章」）**绝不上卡**；
#:   2. **数据不重算**：丹纹率读 ``cfg.danwen_rate``、档位权重读 ``cfg.DANWEN_TIER_W``、
#:      赐福数值读 ``danwen.tier_table`` —— 改数值只改一处，卡面自动跟；
#:   3. **查得到就能看**：不要求持有（宝库里没买的也能看详情），只有持有量那行
#:      才随背包变化。
CARD_ITEM_NAME = "斗破物品详情"

#: 千分率 → 百分比文案（掉落表都按千分记）
def _per_mille(v) -> str:
    try:
        n = int(v)
    except (TypeError, ValueError):
        return "极低"
    return ("%d%%" % (n // 10)) if n % 10 == 0 else ("%.1f%%" % (n / 10.0))


def _danwen_rows(base: dict) -> list[str]:
    """丹药的「丹纹」段：出纹率 → 档位权重 → 三档规则 → 赐福池逐条实数值。"""
    grade = str(base.get("grade") or "")
    rate = cfg.danwen_rate(grade)
    if rate <= 0:
        return []
    w = cfg.DANWEN_TIER_W
    rows = ["开炉成功后有 %d%% 几率出纹（%s丹药，品阶越高越难）"
            % (int(round(rate * 100)), grade),
            "出纹后再掷档位：一纹 %d%%　二纹 %d%%　三纹 %d%%"
            % (int(round(w[0] * 100)), int(round(w[1] * 100)), int(round(w[2] * 100)))]
    rows += [dw.tier_rule(1), dw.tier_rule(2), dw.tier_rule(3)]
    rows.append("赐福池（六选一；括号里是这个丹药的档位多给的部分）")
    for b in dw.BLESSINGS:
        rows.append("【%s】%s" % (b["name"], dw.tier_table(b)))
    return rows


def _pills_using(item_name: str) -> list[str]:
    """哪些丹方的药方里用到了这味材料（反向索引，卡面「用途」段用）。"""
    return [n for n, r in cfg.RECIPE_BY_NAME.items()
            if not r.get("danwen") and item_name in (r.get("need") or {})]


def build_item_detail_data(p: dict, name: str) -> dict | None:
    """物品名 → 详情卡参数（``doupo_item`` / 与成傀卡共用分段内核）。

    查找顺序：带纹丹 → 丹药 → 药材 → 天材地宝 → 系统杂项 → None。
    返回 None 表示「查无此物」，调用方应给文本引导而不是拿占位卡糊过去。
    """
    raw = str(name or "").strip()
    kind = cfg.item_kind(raw)
    if not kind:
        return None
    held = core.count_item(p, raw) if raw else 0
    tier = cfg.danwen_tier(raw) if kind == "danwen" else 0
    base_name = cfg.base_of_danwen(raw) if tier else raw
    rec = cfg.RECIPE_BY_NAME.get(raw) or cfg.RECIPE_BY_NAME.get(base_name)

    groups: list[dict] = []

    # ---- 一、丹药（含带纹丹） ----------------------------------------------
    if rec:
        grade = str(rec.get("grade") or "")
        impl = cfg.recipe_impl(rec) or str(rec.get("desc") or "")
        sub = "丹药 · %s" % (grade or "无品阶")
        if tier:
            sub += " · %s" % cfg.danwen_prefix(tier)
        if held:
            sub += " · 背包 ×%d" % held
        else:
            sub += " · 背包暂无"

        eff = [impl] if impl else []
        lore = str(rec.get("desc") or "").strip()
        if lore and lore != impl:
            eff.append("药老言：%s" % lore)
        if tier:
            eff.append("这枚是带纹丹，服下本效之外另得赐福（见下段）")
        groups.append({"name": "本效", "rows": eff})

        # 升技材料/引火/养魂这三类根本没有带纹版（cfg.DANWEN_SKIP_EFFECTS），
        # 卡面就不能给玩家画一个「打不出来的丹纹池」
        dw_rows = _danwen_rows(rec) if cfg.danwen_allowed(rec) else []
        if dw_rows:
            groups.append({"name": "丹纹", "rows": dw_rows})

        src_rows = []
        need = rec.get("need") or {}
        if need:
            src_rows.append("炼制｜" + "、".join("%s×%d" % (k, int(v)) for k, v in need.items()))
            rate, diff = cfg.craft_rate_for(int(p.get("alch", 0)), grade)
            bit = "成丹率 %s（%s）" % (cfg.rate_cn(rate), cfg.alch_label(int(p.get("alch", 0))))
            if diff > 0:
                bit += "·越%d档" % diff
            src_rows.append(bit)
        # 2026-11-26：能炼制的丹药一律不卖，详情卡不再给「宝库 X 金一枚」这条
        # 会被玩家当成「可直接买」的来源；只有无药方丹（buyonly）才显示购买价。
        if not need and int(rec.get("buy", 0) or 0) > 0:
            src_rows.append("宝库｜%d 金一枚" % int(rec["buy"]))
            src_rows.append("此丹原著未载药方，本作只能金币购入")
        groups.append({"name": "从何而来", "rows": src_rows or ["来历不详"]})
        tip = ("服丹 %s 用掉一枚" % raw) if not tier else ("服丹 %s 本效+赐福一起生效" % raw)
        tip += " · 发「查看 <物品名>」接着看别的"
        # 2026-11-27 接图：带上物品图名，模板自己查 ``pic/templates/assets/
        # doupo_item_icons`` 决定画不画（缺图自动回退，数据层不判断有没有图）。
        # 带纹丹（一纹破厄丹…）没有独立画，用**基础丹名**那件美术。
        return {"title": raw, "subtitle": sub, "image": base_name,
                "groups": groups, "tip": tip}

    # ---- 二、药材（宝库随身商店） ------------------------------------------
    herb = cfg.HERB_BY_NAME.get(raw)
    if herb:
        desc = str(herb.get("desc") or "").strip()
        # 药材的 desc 本身就是「它是什么」，不是另一套药效承诺 —— 只写一行，
        # 前面挂商家口吻。早先既写 desc 又写「药老言：desc」，同一句话连印两遍
        # （2026-11-25 截图自查抓到）。
        groups.append({"name": "本效", "rows": [
            "老药师言：%s" % desc] if desc else ["入药之物，无直接药效"]})
        uses = _pills_using(raw)
        src = ["宝库｜随身商店 %d 金一味" % int(herb.get("price", 0) or 0)]
        if uses:
            src.append("可用于 %d 味丹方：%s" % (len(uses), "、".join(uses[:6])
                                              + ("…" if len(uses) > 6 else "")))
        else:
            src.append("暂未列入任何丹方（先囤着）")
        groups.append({"name": "从何而来", "rows": src})
        sub = "药材 · 宝库 %d 金" % int(herb.get("price", 0) or 0)
        if held:
            sub += " · 背包 ×%d" % held
        return {"title": raw, "subtitle": sub, "groups": groups,
                "tip": "买 <药材> <数量> 补货 · 炼药时按丹方自动扣"}

    # ---- 三、天材地宝（可直接服用） ----------------------------------------
    mat = cfg.MATERIAL_BY_NAME.get(raw)
    if mat:
        desc = str(mat.get("desc") or "").strip()
        mtier = int(mat.get("tier", 0) or 0)
        eff = []
        val = int(mat.get("value") or 0)
        if val > 0:
            eff.append("服之：%s" % (cfg.effect_tag(mat.get("effect", ""))
                                     or mat.get("effect") or "增益"))
            eff.append("%s +%d（发「服丹 %s」服用）" % (
                cfg.effect_tag(mat.get("effect", "")) or "增益", val, raw))
        if desc:
            eff.append("药老言：%s" % desc)
        groups.append({"name": "本效", "rows": eff or ["天材地宝，入药为引"]})
        src = []
        if int(mat.get("price", 0) or 0) > 0:
            src.append("宝库｜%d 金一份" % int(mat["price"]))
        drop = cfg.MATERIAL_DROP_RATE.get(mtier)
        if drop:
            src.append("掉落｜%d 阶起魔兽有得掉（千分率 %d，约 %s）"
                       % (mtier, int(drop), _per_mille(drop)))
        uses = _pills_using(raw)
        if uses:
            src.append("可用于 %d 味丹方：%s" % (len(uses), "、".join(uses[:4])))
        if raw in cfg.PUPPET_MATERIALS:
            src.append("傀儡｜炼制傀儡要它（发「傀儡 配方」看配方）")
        groups.append({"name": "从何而来", "rows": src or ["来历不详"]})
        sub = "天材地宝 · %s阶" % (mtier or "?")
        if held:
            sub += " · 背包 ×%d" % held
        return {"title": raw, "subtitle": sub, "groups": groups,
                "tip": "服丹 %s 直接炼化 · 也可留着当丹方引子" % raw}

    # ---- 四、系统杂项 -------------------------------------------------------
    misc = cfg.MISC_ITEMS.get(raw)
    if misc:
        groups.append({"name": "本效", "rows": list(misc["rows"])})
        groups.append({"name": "从何而来", "rows": [misc["kind"]]})
        sub = misc["kind"] + ((" · 背包 ×%d" % held) if held else "")
        return {"title": raw, "subtitle": sub, "groups": groups, "tip": misc["tip"]}

    # 傀儡材料里有名字不在天材地宝表的（魔核按阶命名、躯体/残魂等）
    if raw in cfg.PUPPET_MATERIALS or (raw.endswith("魔核") and len(raw) <= 12):
        sub = "傀儡材料" + ((" · 背包 ×%d" % held) if held else "")
        return {"title": raw, "subtitle": sub,
                "groups": [{"name": "本效",
                            "rows": ["傀儡的一件料，不入口；凑齐配方即开炉",
                                     "品阶越高，成傀档位与魔核加成越好"]},
                           {"name": "从何而来",
                            "rows": ["傀儡遗迹 / 魔兽掉落（发「傀儡 遗迹」探）"]}],
                "tip": "发「傀儡 配方」看整套配方 · 发「傀儡」看名录"}

    return None


async def render_item_detail(p: dict, name: str) -> bytes | None:
    """「查看 <物品名>」出图入口；查无此物返回 None（调用方给文本引导）。"""
    data = build_item_detail_data(p, name)
    if not data:
        return None
    return await _render_list("doupo_item", data, CARD_ITEM_NAME, qq=p.get("qq"))


CARD_FLAME_NAME = "斗破异火详情"


def build_flame_detail_data(p: dict, name: str) -> dict | None:
    """「查看 <火名>」详情卡参数（2026-11-28 用户「火焰查看怎么能是纯文字，有图的，图要大点」）。

    与丹药详情卡共用 ``doupo_item`` 模板（同一套分段/头像格语言），差异只有三点：
      ① groups 走 flame_view 的主动/被动/获取途径（唯一真源，不重写）；
      ② ``image=火名`` 接 doupo_item_icons 里的真火图；
      ③ ``icon_hero=160`` 把火图放大（普通物品默认 88）。
    火名必须在 33 朵全集中，否则返回 None（调用方回文本引导），绝不拿占位图糊。
    """
    fn = name.strip()
    if fn not in set(fview.all_flame_names()):
        return None
    active, passive = fview._effect_of(fn)
    tier = (getattr(cfg, "FLAME_TIER", {}) or {}).get(fn, "")
    ident = "兽火" if fn in getattr(cfg, "BEAST_FLAME_NAMES", []) else "异火"
    fl = p.get("flames") or {}
    lv = int(fl.get(fn, 0) or 0)
    diyan = bool(p.get("diyan")) if fn == cfg.FLAME_DIYAN else False
    owned = diyan if fn == cfg.FLAME_DIYAN else lv > 0
    if fn == cfg.FLAME_DIYAN:
        state = "已得" if diyan else "未得（成就斗帝自燃）"
    elif lv >= cfg.FLAME_MAX_LV:
        state = "%d 级（圆满）" % lv
    elif lv > 0:
        state = "%d/%d 级" % (lv, cfg.FLAME_MAX_LV)
    else:
        state = "未收服"
    sub = "%s · %s%s · %s" % (
        ident, (tier + " 线 · " if tier else ""), state,
        ("已拥有" if owned else "尚未拥有"))
    return {
        "title": fn, "subtitle": sub,
        "image": fn, "icon_hero": 160,
        "groups": [
            {"name": "主动", "rows": [active]},
            {"name": "被动", "rows": [passive]},
            {"name": "获取途径", "rows": [fview.how_of(fn)]},
        ],
        "tip": ("发「异火」看火种总览 · 发「养火 %s」喂养升阶" % fn) if owned
               else ("发「收服 %s」认主后即可喂养" % fn),
    }


async def render_flame_detail(p: dict, name: str) -> bytes | None:
    """「查看 <火名>」出图入口；非火焰或渲染失败返回 None（调用方给文本引导）。"""
    data = build_flame_detail_data(p, name)
    if not data:
        return None
    return await _render_list("doupo_item", data, CARD_FLAME_NAME, qq=p.get("qq"))


async def render_craft_for(report: dict | None, qq: str | None = None) -> bytes | None:
    """「炼制傀儡」出图入口：只吃命令层写下的 report，不另查库。

    返回 None 表示「这一炉没有成傀数据」——调用方此时应发文本回执，不要拿
    DEMO 占位卡糊过去（那会让玩家以为炼成了）。

    2026-11-25 主题修复：``qq`` 必须透传，否则个人主题口令对成傀卡无效。
    """
    data = build_craft_data(report)
    if not data:
        return None
    return await _render_list("doupo_craft", data, CARD_CRAFT_NAME, qq=qq)


# ---------------------------------------------------------------------------
# 按 QQ 出图的两个唯一入口（2026-11-18 事故后补）
#
# 背景：``render_puppet(roster, elig, p)`` / ``render_ruin(state, day)`` 的签名
# 与其它卡 ``render_*(p)`` 不同。按钮分发原本统一用 ``renderer(p)`` 调用，
# 于是傀儡卡把**玩家 dict 当成名录列表**、遗迹卡把**玩家 dict 当成当日状态**，
# 渲染出「持有 0 具 / 今日进度 0/3」的假卡（不报错，默默错）。
#
# 现在出图取数只在这两个函数里做一次，文本 matcher 与按钮分发都调它，
# 谁再想「自己拼参数」就会因为签名不匹配在测试里立刻红。
# ---------------------------------------------------------------------------
def _puppet_store(pstore=None):
    """傀儡库单例。必须从子模块取名（``from . import puppet_store`` 拿到的是实例）。"""
    if pstore is not None:
        return pstore
    from .puppet_store import PUPPET_STORE  # noqa: PLC0415
    return PUPPET_STORE


async def render_puppet_for(qq: str, p: dict | None = None, pstore=None) -> bytes | None:
    """按 QQ 渲染傀儡名录卡（数据与 ``cmd_puppet`` 同源，不另算一遍）。"""
    store = _puppet_store(pstore)
    roster = await store.list_puppets(str(qq))
    elig = await store.eligibility(str(qq))
    return await render_puppet(roster, elig, p, qq=str(qq))


async def render_ruin_for(qq: str, day: str = "", pstore=None,
                          report: dict | None = None) -> bytes | None:
    """按 QQ 渲染傀儡遗迹卡（today 缺省取本地当天，与 ``cmd_puppet_ruin`` 同源）。

    ``report`` 传 ``cmd_puppet_ruin(report=...)`` 收下的战况 → 并进同一张卡。
    """
    store = _puppet_store(pstore)
    if not day:
        from datetime import date  # noqa: PLC0415
        day = date.today().isoformat()
    state = await store.ruin_state(str(qq), day)
    return await render_ruin(state, day, report, qq=str(qq))
