# -*- coding: utf-8 -*-
"""斗破「消息按钮点击」业务分发（仅官方副本，2026-11-16）。

## 为什么单独一层（而不是复用 qq_interaction 的 game_tools dispatch）

``qq_interaction.handle_interaction`` 原本把任何 button_data 都当 game_tools 口令
投递到 ``_handle_safe``。斗破按钮 data 是斗破中文口令（人物/修炼/异火…），
game_tools 不认识 → 点了没反应。本模块让斗破按钮点击复用**同一套 commands.cmd_***
（业务零分叉），并用 ``OfficialChannel.send`` 发出（event_id 被动回复，沿用
``qq_interaction`` 已踩坑验证的凭据方案）。

## 触发入口

由 ``qq_interaction.handle_interaction`` 在识别到 button_data ∈
``doupo_buttons.ALL_BUTTON_CMDS`` 时调用 :func:`dispatch_button`；返回 True 表示
已处理（qq_interaction 不再走 game_tools 分发）。

## 关键纪律

* **锁定斗破模式**：按钮来自斗破卡，点击必然走斗破（即使用户中途切了修仙）——
  入口先 ``set_mode(openid, "doupo")``，杜绝「修炼」这类共享口令串到修仙。
* **身份**：按钮回调只有 openid（群 member_openid），与斗破存档 key 一致，
  直接作 ``qq`` 传给 cmd_*；C2C 场景用 user_openid。
* **零编造**：分发表每个 key ∈ ``doupo_buttons.ALL_BUTTON_CMDS``，且每个 value
  都调用真实的 ``commands.cmd_*``；门禁用 AST 校验覆盖完整。
* **NapCat 无关**：本模块只在官方按钮回调链路被调用，NapCat 完全不触碰。
"""
from __future__ import annotations

from typing import Any

# 按钮口令 → (commands 函数名, 调用参数形态)。参数形态决定怎么调 cmd_*：
#   "qq"        → cmd(store, qq)
#   "qq_str"    → cmd(store, qq, "")         无参查询/用法提示
#   "gid"       → cmd(store, gid)            排行
#   panel/skills/flames/shop/bag/faction/rank → 出卡片（card.render_*）
# 出卡口令同时给出 card_key，供带按钮再下发；文本口令 card_key=""。
_PLAN: dict[str, tuple[str, str, str]] = {
    # cmd               : (commands 函数,   参数形态,  card_key 或 "")
    "入道": ("cmd_enter", "enter", ""),        # 已在群中→返回提示；不重复建号
    "人物": ("cmd_panel", "qq", "panel"),
    "修炼": ("cmd_cultivate", "qq", ""),
    "突破": ("cmd_break", "qq", ""),
    "战斗": ("cmd_fight", "qq", ""),
    "战斗评价": ("cmd_battle_comment", "qq", ""),
    "爬塔": ("cmd_tower", "qq_str", ""),
    "塔层": ("cmd_tower_status", "qq", ""),
    "装备": ("cmd_equip", "qq_str", ""),
    "强化": ("cmd_enhance", "qq", ""),
    "斗技": ("cmd_skills", "qq", "skills"),
    "斗技详情": ("cmd_skill_info", "qq_str", ""),
    "升级": ("cmd_skill_up", "qq_str", ""),
    "上阵": ("cmd_loadout", "qq_str", ""),
    "异火": ("cmd_flames", "qq", "flames"),
    "养火": ("cmd_feed", "qq_str", ""),
    "转宗": ("cmd_faction", "qq_str", ""),
    "宝库": ("cmd_shop", "qq", "shop"),
    "买": ("cmd_buy", "qq_str", ""),
    "炼药": ("cmd_recipes", "qq", "recipes"),
    "服丹": ("cmd_use", "qq_str", ""),
    "背包": ("cmd_bag", "qq", "bag"),
    # 2026-11-18 傀儡：按钮点击走同一套 cmd_*，不另写业务逻辑
    "炼制傀儡": ("cmd_craft_puppet", "qq", ""),
    # shape="ruin"：先跑战斗并把战况收进 report，再把战况并进同一张卡（只发一条）。
    "傀儡遗迹": ("cmd_puppet_ruin", "ruin", "ruin"),
    "傀儡": ("cmd_puppet", "qq", "puppet"),
}


def plan() -> dict[str, tuple[str, str, str]]:
    """返回按钮口令→调用计划（供守门测试核对，返回副本避免被篡改）。"""
    return dict(_PLAN)


#: 竞猜选项按钮 data 前缀（与 channel/doupo_buttons.py 同源）。
QUIZ_PREFIX = "doupo:quiz:"

#: 炼药卡翻页按钮 data 前缀（与 channel/doupo_buttons.py 同源）。
PAGE_PREFIX = "doupo:page:"

#: 玩家自定义按钮 data 前缀（2026-11-18）。data = ``doupo:cbtn:<真实口令>``，
#: 口令可带参（如 ``买 血灵草 2``），点击后解析首词查 _PLAN 走同一套 cmd_*。
CUSTOM_PREFIX = "doupo:cbtn:"


def parse_custom_cmd(cmd: str) -> str | None:
    """解析 ``doupo:cbtn:<口令>`` → 真实口令；非本前缀/空返 None。"""
    raw = (cmd or "").strip()
    if not raw.startswith(CUSTOM_PREFIX):
        return None
    real = raw[len(CUSTOM_PREFIX):].strip()
    return real or None


def parse_page_cmd(cmd: str) -> int | None:
    """解析 ``doupo:page:<page>`` → 目标页码（1-based）；非本前缀/非法返 None。"""
    raw = (cmd or "").strip()
    if not raw.startswith(PAGE_PREFIX):
        return None
    body = raw[len(PAGE_PREFIX):]
    if not body.isdigit() or int(body) < 1:
        return None
    return int(body)


async def dispatch_page(bot: Any, full_ctx: dict, cmd: str) -> bool:
    """炼药卡翻页点击回调：渲染目标页 → 带新页按钮再下发。返回 True=已处理。

    与 :func:`dispatch_quiz` 同构（event_id 被动回复、store 单例、模式锁定），
    业务走 ``card.render_recipes(p, page)``；页码越界由 core.recipes_page 夹取。
    翻页后需要重新 arm 分页状态（下一页的「上一页/下一页」目标不同）。
    """
    target = parse_page_cmd(cmd)
    if target is None:
        return False

    from . import commands as cmd_mod  # noqa: PLC0415
    from . import card as card_mod  # noqa: PLC0415
    from .store import STORE as _STORE  # noqa: PLC0415
    from src.game_mode import set_mode  # noqa: PLC0415

    store = _STORE
    try:
        await store.load()
    except Exception:  # noqa: BLE001 — load 幂等且失败不阻断本次回复
        pass

    user_key = str(full_ctx.get("user_key") or "")
    openid = user_key.split(":")[-1] if ":" in user_key else user_key
    try:
        await set_mode(openid, "doupo")
    except Exception:  # noqa: BLE001
        pass

    # 夹取页码到实际范围（与纯文本 cmd_recipes / card 同一口径）
    p = await store.get_player(openid)
    if not p:
        return True   # 未入道：静默（点击旧卡时角色可能已重置），不误发
    _chunk, page, total_pages = cmd_mod.core.recipes_page(p, target)

    png = None
    try:
        png = await card_mod.render_recipes(p, page)
    except Exception as e:  # noqa: BLE001 — 出图失败降级文本
        from nonebot.log import logger  # noqa: PLC0415

        logger.warning("doupo_page_render_failed: %s: %s" % (type(e).__name__, e))

    if png:
        from src.game_compat import send_card_via_channel  # noqa: PLC0415
        from src.plugins.qq_bridge.channel import doupo_buttons as db  # noqa: PLC0415

        # full_ctx 已由 qq_interaction 调用方 attach 到 ContextVar（与 quiz/button
        # 同构），本函数不重复 attach；只需 arm 本回合分页状态供按钮派生读取。
        page_ctx_token = None
        try:
            page_ctx_token = db.arm_page(page, total_pages)
            # send_card_via_channel 内部拼 "doupo:" + card_key：
            #   "page:N" → keyboard_cmd "doupo:page:N" → buttons_for 走翻页分支；
            # 多页才挂翻页按钮；单页退回普通炼药卡按钮（服丹/养火/宝库）。
            card_key = ("page:%d" % page) if total_pages > 1 else "recipes"
            ret = await send_card_via_channel(full_ctx, png, card_key)
            if isinstance(ret, dict) and ret.get("ok"):
                return True
        except Exception as e:  # noqa: BLE001
            from nonebot.log import logger  # noqa: PLC0415

            logger.warning("doupo_page_send_failed: %s: %s" % (type(e).__name__, e))
        finally:
            if page_ctx_token is not None:
                db.disarm_page(page_ctx_token)

    # 出图失败 → 发纯文本当页清单（绝不丢消息）
    try:
        from src.plugins.qq_bridge.channel.base import Outgoing  # noqa: PLC0415
        from src.plugins.qq_bridge.channel import OFFICIAL, get_channel  # noqa: PLC0415

        msg = await cmd_mod.cmd_recipes(store, openid, page)
        await get_channel(OFFICIAL).send(full_ctx, Outgoing(text=msg))
    except Exception as e:  # noqa: BLE001
        from nonebot.log import logger  # noqa: PLC0415

        logger.warning("doupo_page_text_failed: %s: %s" % (type(e).__name__, e))
    return True


def parse_quiz_cmd(cmd: str) -> tuple[str, int] | None:
    """解析 ``doupo:quiz:<token>:<idx>`` → (token, idx)；非本前缀返 None。"""
    raw = (cmd or "").strip()
    if not raw.startswith(QUIZ_PREFIX):
        return None
    body = raw[len(QUIZ_PREFIX):]
    token, sep, idx_s = body.rpartition(":")
    if not sep or not token or not idx_s.isdigit():
        return None
    return token, int(idx_s)


async def dispatch_quiz(bot: Any, full_ctx: dict, cmd: str) -> bool:
    """有奖竞猜选项点击回调：判定 + 答对发奖。返回 True=已处理。

    与 :func:`dispatch_button` 同构（event_id 被动回复、store 单例、模式锁定），
    只是业务走 ``commands.cmd_answer_quiz`` 而非静态 _PLAN。
    """
    parsed = parse_quiz_cmd(cmd)
    if parsed is None:
        return False
    token, idx = parsed

    from . import commands as cmd_mod  # noqa: PLC0415
    from .store import STORE as _STORE  # noqa: PLC0415
    from src.plugins.qq_bridge.channel.base import Outgoing  # noqa: PLC0415
    from src.plugins.qq_bridge.channel import OFFICIAL, get_channel  # noqa: PLC0415

    store = _STORE
    try:
        await store.load()
    except Exception:  # noqa: BLE001
        pass

    user_key = str(full_ctx.get("user_key") or "")
    openid = user_key.split(":")[-1] if ":" in user_key else user_key

    try:
        msg = await cmd_mod.cmd_answer_quiz(store, openid, token, idx)
    except Exception as e:  # noqa: BLE001 — CLOSE-02 记全不静默吞
        from nonebot.log import logger  # noqa: PLC0415

        logger.warning("doupo_quiz_answer_failed: %s: %s" % (type(e).__name__, e))
        msg = "（答题处理出错，请重试）"

    try:
        await get_channel(OFFICIAL).send(full_ctx, Outgoing(text=msg))
    except Exception as e:  # noqa: BLE001
        from nonebot.log import logger  # noqa: PLC0415

        logger.warning("doupo_quiz_reply_failed: %s: %s" % (type(e).__name__, e))
    return True


async def dispatch_button(bot: Any, full_ctx: dict, cmd: str,
                          arg_override: str | None = None) -> bool:
    """把一个斗破按钮口令分发出去。返回 True=已处理；False=非斗破口令（放行）。

    ``full_ctx`` 是 ``qq_interaction`` 构造的完整官方 ctx（含 user_key/group_key/
    reply_ref.event_id），由调用方 attach 到 ContextVar；本函数不 attach（避免
    与调用方重复），只复用已 attach 的 ctx 让 OfficialChannel.send 拿到凭据。

    ``arg_override``（2026-11-18 玩家自定义按钮）：带参口令如 ``买 血灵草 2``
    拆出首词 ``买`` 查表 + 参数 ``血灵草 2``，用于 ``qq_str`` 形态函数真传参。
    """
    from src.plugins.qq_bridge.channel.base import Outgoing  # noqa: PLC0415
    from src.plugins.qq_bridge.channel import OFFICIAL, get_channel  # noqa: PLC0415

    spec = _PLAN.get((cmd or "").strip())
    if spec is None:
        return False
    fn_name, shape, card_key = spec

    # 延迟 import：避免与 doupo/__init__ 顶层 matcher 注册循环依赖；
    # qq_interaction 调用时 nonebot 已初始化、doupo 已加载。
    from . import commands as cmd_mod  # noqa: PLC0415
    from . import card as card_mod  # noqa: PLC0415
    # ⚠️ 必须 from .store import STORE，不能 from . import store：
    # doupo/__init__.py 里有模块级 `store = store_mod.STORE`，包属性 `store`
    # 已被 Store 实例遮蔽，`from . import store` 会拿到实例而非子模块。
    from .store import STORE as _STORE  # noqa: PLC0415
    from src.game_mode import set_mode  # noqa: PLC0415
    from src.game_compat import send_card_via_channel  # noqa: PLC0415

    # store 单例：与 doupo/__init__.py 共享 store.STORE（同一进程同一实例），
    # 保证按钮点击读到的是玩家真实存档（而不是新建的空库）。
    # 若插件尚未 load()（极早期点击），这里补一次 load（幂等）。
    store = _STORE
    try:
        await store.load()
    except Exception:  # noqa: BLE001 — load 幂等且失败不阻断本次回复
        pass

    user_key = str(full_ctx.get("user_key") or "")
    openid = user_key.split(":")[-1] if ":" in user_key else user_key
    gid = str(full_ctx.get("group_key") or "").split(":")[-1] or openid

    # 锁定斗破模式（按钮来自斗破卡，共享口令不许串到修仙）
    try:
        await set_mode(openid, "doupo")
    except Exception:  # noqa: BLE001 — 模式落在 store 之外，失败也不阻断本条回复
        pass

    # shape="ruin" 的战斗会把战况写进这个 dict，随卡一起出（只发一条）。
    # 必须在调用之前初始化，否则会被下面的卡片分支重置成 None。
    ruin_report = None
    fn = getattr(cmd_mod, fn_name)
    try:
        if shape == "enter":
            # 按钮点击无昵称上下文；与 __init__._name 的兜底一致（道友<openid 后4位>）
            name = str(full_ctx.get("sender_name", "") or "") or ("道友" + openid[-4:])
            msg = await fn(store, openid, name, gid)
        elif shape == "gid":
            msg = await fn(store, gid)
        elif shape == "ruin":
            # 战况写进 ruin_report，随卡一起发；不再另发一条文字战报。
            ruin_report = {}
            msg = await fn(store, openid, None, report=ruin_report)
        elif shape == "qq_str":
            msg = await fn(store, openid, (arg_override or "").strip())
        else:  # "qq"
            msg = await fn(store, openid)
    except Exception as e:  # noqa: BLE001 — CLOSE-02 记全不静默吞
        from nonebot.log import logger  # noqa: PLC0415

        logger.warning("doupo_button_dispatch_failed cmd=%s: %s: %s" % (cmd, type(e).__name__, e))
        msg = "（按钮处理出错，请重试或手打口令）"

    # 出卡口令：card.render_* + 带按钮再下发；渲染失败降级为纯文本
    if card_key:
        png = None
        try:
            p = await store.get_player(openid)
            # ⚠️ 每张卡的入参形状不同，**不许**统一 `renderer(p)` 糊过去。
            #    2026-11-18 事故：傀儡卡签名是 render_puppet(roster, elig, p)、
            #    遗迹卡是 render_ruin(state, day)，被 `renderer(p)` 调用后把玩家 dict
            #    当成名录/当日状态，渲出「持有 0 具 / 今日进度 0/3」的假卡（还不报错）。
            #    所以非 (p) 形状的卡必须在 card 里提供「按 qq 取数」的唯一入口。
            if card_key == "puppet":
                if p:
                    png = await card_mod.render_puppet_for(openid, p)
            elif card_key == "ruin":
                if p:
                    png = await card_mod.render_ruin_for(openid, "", None, ruin_report)
            else:
                renderer = getattr(card_mod, "render_" + card_key, None)
                if p and renderer is not None:
                    if card_key == "rank":
                        # render_rank(top: list) 与其它 render_*(p) 签名不同
                        top = (await store.all_players(gid))[:10]
                        png = await renderer(top)
                    else:
                        png = await renderer(p)
        except Exception:  # noqa: BLE001 — 出图失败降级文本，绝不静默吞消息
            png = None
        if png:
            try:
                ret = await send_card_via_channel(full_ctx, png, card_key)
                if isinstance(ret, dict) and ret.get("ok"):
                    return True
            except Exception:  # noqa: BLE001 — 降级文本
                pass

    # 文本回复（无图 / 出图失败 / 纯操作口令）
    try:
        await get_channel(OFFICIAL).send(full_ctx, Outgoing(text=msg))
    except Exception as e:  # noqa: BLE001
        from nonebot.log import logger  # noqa: PLC0415

        logger.warning("doupo_button_text_send_failed cmd=%s: %s: %s" % (cmd, type(e).__name__, e))
    return True


async def dispatch_custom(bot: Any, full_ctx: dict, cmd: str) -> bool:
    """玩家自定义按钮点击（2026-11-18）：``doupo:cbtn:<口令>`` → 真口令分发。

    解析哨兵 → 拆「首词 + 参数」→ 首词查 :data:`_PLAN` 走同一套 ``commands.cmd_*``
    （业务零分叉），带参口令（如 ``买 血灵草 2``）把参数传给 ``qq_str`` 形态函数。
    首词不在 _PLAN（如自定义了不被按钮系统支持的口令）→ 返 False 放行，
    由 qq_interaction 记日志，绝不静默吞。
    """
    real = parse_custom_cmd(cmd)
    if not real:
        return False
    parts = real.split()
    head, arg = parts[0], " ".join(parts[1:])
    if head not in _PLAN:
        from nonebot.log import logger  # noqa: PLC0415

        logger.warning("doupo_custom_button_unsupported head=%s raw=%s" % (head, real))
        return False
    return await dispatch_button(bot, full_ctx, head, arg_override=arg)
