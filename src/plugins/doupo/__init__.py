# -*- coding: utf-8 -*-
"""斗破苍穹·双帝之战 · NoneBot2 口令入口（QQ 群聊 / 私聊文字版）。

纯口令、零 LLM。业务逻辑全在 commands.py / core.py（无 nonebot，可单测）；
本文件只做三件事：
  1) 解析群消息里的口令（允许可选 # 前缀 / 前导空白 / @机器人）
  2) 从 event 取 qq / 群 / @对象 / 数字参数
  3) 调 commands.cmd_* 把结果文字回群

⚠️ 优先级：主插件聊天处理器是 on_message(priority=10, block=False)。
本插件全部 matcher 用 block=True —— 先于聊天处理器运行，命中即阻断，
避免"口令被 LLM 抢答/双重回复"。优先级绝大多数为 5；仅精确口令
「斗技详情」和「炼制傀儡 / 傀儡遗迹 / 傀儡」用 4（PRI-1）抢在
「斗技」列表 / 「炼制」丹方口令之前，避免被短口令吃掉。

⚠️ 模式隔离：斗破 / 修仙 现在都用纯中文口令，靠「按用户记录的当前模式」分流。
   谁发「斗破菜单」→ 他后续口令走斗破；发「修仙菜单」→ 走修仙（菜单是唯一切换口）。
   没有模式或打错游戏的口令，由 game_router 兜底给友好提示，两游戏互不抢答。
"""

from __future__ import annotations

import logging
import re
from datetime import date

from nonebot import on_regex, get_driver
from nonebot.adapters.onebot.v11 import Message, MessageSegment

# Event 从兼容层取（运行时有真 Event；纯业务单测的 _stub_nonebot 无此属性时回退 object）
# finish_card（2026-11-16）：官方通道出图时挂对应功能按钮，NapCat 行为不变。
from src.game_compat import (
    Event, finish_card, finish_text, image_seg, install_official_text_bridge,
    is_official, view,
)

from . import commands
from . import card
from . import avatar
from . import core
from . import store as store_mod
from . import puppet_store as puppet_store_mod

logger = logging.getLogger("qq_bridge.doupo")

# 2026-11-25：官方通道纯文本统一收口（「上阵 / 学习 / 锁定 / 主题」打字没反应的
# 根因修复——原生 matcher.finish(str) 发出的纯文本在官方通道被平台静默吞掉，
# 卡片/按钮走的 OfficialChannel.send 才到得了）。幂等；NapCat 单跑时自动跳过。
try:
    install_official_text_bridge()
except Exception as _e:  # noqa: BLE001 — 兜底补丁失败绝不能拖垮整个插件
    logger.warning("官方纯文本通道补丁安装失败（不影响其它功能）：%s: %s",
                   type(_e).__name__, _e)

# 进程级单例（store.STORE）：按钮点击回调与本插件必须共享同一实例，
# 否则官方按钮点「人物」读不到当前存档。见 store.py 末尾说明。
store = store_mod.STORE
# 傀儡独立 SQLite（真实列，绝不进 players.data 的 JSON blob）。
# ⚠️ 变量名**故意不叫 puppet_store**：叫它就会把包属性 `doupo.puppet_store`（子模块）
#    盖成实例，任何 `from . import puppet_store` 之后再取 `.PUPPET_STORE` 都会
#    AttributeError。2026-11-18 事故就是这么炸的（按钮点「炼制傀儡」全报错）。
#    需要模块请 `from .puppet_store import PUPPET_STORE`；本文件用 puppet_store_inst。
puppet_store_inst = puppet_store_mod.PUPPET_STORE


# priority=5（高于聊天处理器的 10） + block=True（命中即阻断 LLM 闲聊）
from src.game_mode import get_mode, set_mode, register_keywords


def _mode_is(game: str):
    # 官方副本 adapter 隔离（2026-11-11）：event 必须注解为 NapCat/OneBot V11
    # 具体类型。用户给机器人开通「获取群内全部消息」后，官方全量群消息
    # (GroupMessageCreateEvent，无 user_id 字段) 会进入本 rule；若 event 无注解，
    # NoneBot 依赖注入放行 → 执行 event.user_id → AttributeError → 每条官方消息
    # 刷一条 "Rule check failed" ERROR 噪音。注解具体 MessageEvent 后，官方事件
    # 在依赖注入阶段抛 TypeMisMatch(SkippedException)，被 Rule.__call__ 静默置
    # False，绝不再碰 event.user_id；NapCat 的 MessageEvent 实例照常通过，行为不变。
    async def _r(event: Event) -> bool:
        # 2026-11-16：注解改 nonebot.adapters.Event（基类），两种通道都能注入；
        # uid 走兼容层（官方=openid / NapCat=QQ 号）。原注解 OneBot 具体类型
        # 会让官方事件在 rule 阶段 TypeMisMatch→False，导致口令永不响应。
        return (await get_mode(str(view(event).user_id))) == game
    return _r

PRI = 5


def _pat(kw: str) -> str:
    """允许前导 CQ 码 / 空白任意交替 + 可选 # 前缀。

    ⚠️ nonebot 的 RegexRule 用 re.search(pattern, **str(event.get_message())**) 匹配，
    即匹配对象是 CQ 码字符串（如 '[CQ:at,qq=10002]抢'），不是纯文本。
    因此 ^ 之后必须先吃掉前置 CQ 码，否则所有「@某人 + 口令」的玩法全部失效。
    """
    return r"^(?:\[CQ:[^\]]*\]|<@[^>]*>|\s)*#?\s*" + kw


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------
def _gid(event):
    return getattr(event, "group_id", None)


def _extract_at(event):
    """取第一个"他人" @ 对象：跳过机器人自己与发送者本人。"""
    me = str(getattr(event, "self_id", ""))
    uid = str(event.user_id)
    for seg in event.message:
        if getattr(seg, "type", None) == "at":
            qq = str(seg.data.get("qq"))
            if qq and qq not in (me, "all") and qq != uid:
                return qq
    return None


def _plain(event) -> str:
    return event.get_plaintext()


def _arg(event, kw: str) -> str:
    """取口令后面的参数（先剥前导 CQ 残留/空白/#，再剥关键词）。"""
    t = re.sub(r"^\s*#?\s*", "", _plain(event))
    return t.replace(kw, "", 1).strip()


def _name(event) -> str:
    sender = getattr(event, "sender", None)
    if sender and getattr(sender, "card", None):
        return sender.card.strip()
    if sender and getattr(sender, "nickname", None):
        return sender.nickname.strip()
    return f"道友{str(event.user_id)[-4:]}"


# 私聊虚拟作用域前缀：每个用户一个「虚拟群」，隔离单人存档。
# 这样斗破既能在群聊玩（真实 group_id），也能在私聊单人玩（pv:<user_id>）。
# 已知限制：私聊里没有「其他成员」，故 排行/抢/送 在私聊只涉及自己。
PRIVATE_SCOPE_PREFIX = "pv:"


def _scope(event) -> str:
    """当前会话作用域：群聊=真实 group_id；私聊=pv:<user_id>。"""
    gid = _gid(event)
    if gid:
        return str(gid)
    return f"{PRIVATE_SCOPE_PREFIX}{event.user_id}"


async def _need_group(event, matcher):
    """取当前会话作用域。

    历史版本：私聊一律拒绝（"请在群聊中使用斗破功能～"）。
    现按需求放开私聊——私聊用 pv:<user_id> 作虚拟作用域，单人可玩；
    matcher 参数保留仅为兼容既有调用点，不再用于阻断。
    """
    return _scope(event)


def _num(event, default=None):
    m = re.search(r"(\d+)", _plain(event))
    return int(m.group(1)) if m else default


# ---------------------------------------------------------------------------
# 菜单 / 帮助
# ---------------------------------------------------------------------------
menu = on_regex(_pat(r"(?:斗破菜单|斗破口令)"), priority=PRI, block=True)


@menu.handle()
async def _(event: Event):
    event = view(event)  # 2026-11-16 双通道兼容（官方=openid/NapCat=QQ号）
    await set_mode(str(event.user_id), "doupo")
    await _need_group(event, menu)
    # 纯文本兜底（commands.cmd_menu 即菜单唯一源，与图片同源）
    msg = commands.cmd_menu()
    # 优先发自绘菜单卡；任何异常都降级为上面的纯文本（绝不丢消息）
    png = None
    try:
        png = await card.render_menu()
    except Exception as e:  # noqa: BLE001
        logger.warning("斗破菜单卡渲染异常，降级纯文本：%s: %s", type(e).__name__, e)
    if png:
        await finish_card(menu, event, png, "menu", "doupo_menu.png")
        return
    await menu.finish(msg)


helper = on_regex(_pat(r"(?:帮助|玩法|攻略|规则)"), priority=PRI, block=True, rule=_mode_is("doupo"))


@helper.handle()
async def _(event: Event):
    event = view(event)  # 2026-11-16 双通道兼容（官方=openid/NapCat=QQ号）
    await _need_group(event, helper)
    msg = commands.cmd_help()
    # 玩法细则是 44 行全静态内容 → 卡片化；任何异常降级为纯文本（绝不丢消息）
    png = None
    try:
        png = await card.render_help()
    except Exception as e:  # noqa: BLE001
        logger.warning("斗破玩法卡渲染异常，降级纯文本：%s: %s", type(e).__name__, e)
    if png:
        await finish_card(helper, event, png, "help", "doupo_help.png")
        return
    await helper.finish(msg)


# 卡片主题切换（2026-11-23 双主题）：个人口令 + 运维全局默认。
# 「主题默认 X」也要被这条正则吃到，handler 内按首词分流，避免多 matcher 互抢。
theme_cmd = on_regex(_pat(r"主题(?:默认|切换)?"), priority=PRI, block=True,
                     rule=_mode_is("doupo"))


@theme_cmd.handle()
async def _(event: Event):
    event = view(event)  # 2026-11-16 双通道兼容（官方=openid/NapCat=QQ号）
    await _need_group(event, theme_cmd)
    qq = str(event.user_id)
    raw = re.sub(r"^\s*#?\s*", "", _plain(event)).strip()
    # 分流：主题默认 <风格>（运维全局）/ 主题 <风格>（个人）
    if raw.startswith("主题默认") or raw.startswith("主题切换默认"):
        arg = re.sub(r"^主题(?:切换)?默认\s*", "", raw).strip()
        msg = commands.cmd_theme_default(arg)
    else:
        arg = re.sub(r"^主题(?:切换)?\s*", "", raw).strip()
        msg = commands.cmd_theme(qq, arg)
    await theme_cmd.finish(msg)


# ---------------------------------------------------------------------------
# 入道 / 人物
# ---------------------------------------------------------------------------
enter = on_regex(_pat(r"(?:入道|开始|创建角色)"), priority=PRI, block=True)


@enter.handle()
async def _(event: Event):
    event = view(event)  # 2026-11-16 双通道兼容（官方=openid/NapCat=QQ号）
    await set_mode(str(event.user_id), "doupo")
    gid = await _need_group(event, enter)
    msg = await commands.cmd_enter(store, str(event.user_id), _name(event), gid)
    # 建号欢迎动图（1 级 = 主角持剑站姿），失败不影响文字。
    # 注：这是全流程里唯一剩下的动态形象；「人物 / 属性 / 面板」已按用户要求
    # 改成只发一张内嵌静态头像的卡片，不再夹带动图（见下方 panel）。
    gif = None
    try:
        gif = avatar.avatar_bytes(1)
    except Exception as e:  # noqa: BLE001
        logger.warning("斗破入道动图读取异常，只发文字：%s: %s", type(e).__name__, e)
    if gif:
        if is_official(event):
            # 官方通道：文字与富媒体是互斥的 msg_type（0=文本 / 7=图片），
            # 不能像 OneBot 那样把图+文拼成一条。先发图，再发文字。
            await enter.send(image_seg(event, gif, file_name="doupo_enter.gif"))
            await enter.finish(msg)
        await enter.finish(Message([image_seg(event, gif, file_name='doupo_enter.gif'), MessageSegment.text(msg)]))
    await enter.finish(msg)


panel = on_regex(_pat(r"(?:人物|属性|面板)"), priority=PRI, block=True, rule=_mode_is("doupo"))


@panel.handle()
async def _(event: Event):
    event = view(event)  # 2026-11-16 双通道兼容（官方=openid/NapCat=QQ号）
    await _need_group(event, panel)
    qq = str(event.user_id)
    # 纯文本兜底（commands.cmd_panel 内部已结算离线修为并存档）
    msg = await commands.cmd_panel(store, qq)
    # 2026-11-07 用户要求：人物卡里不再放动态小人，改为卡片内嵌静态头像。
    # 因此这里**只发一张 PNG 卡片**，绝不额外拼 GIF 段；出图口令必须出图，
    # 但也不许用「卡片 + 动图」两张图刷屏。渲染异常才回落到上面的纯文本。
    png = None
    try:
        p = await store.get_player(qq)
        if p:
            png = await card.render_panel(p)
    except Exception as e:  # noqa: BLE001
        logger.warning("斗破人物卡渲染异常，降级纯文本：%s: %s", type(e).__name__, e)
    if png:
        await finish_card(panel, event, png, "panel", "doupo_panel.png")
        return
    await panel.finish(msg)


# ---------------------------------------------------------------------------
# 修炼 / 突破 / 战斗
# ---------------------------------------------------------------------------
cult = on_regex(_pat(r"(?:修炼|静修|打坐)"), priority=PRI, block=True, rule=_mode_is("doupo"))


@cult.handle()
async def _(event: Event):
    event = view(event)  # 2026-11-16 双通道兼容（官方=openid/NapCat=QQ号）
    await _need_group(event, cult)
    await cult.finish(await commands.cmd_cultivate(store, str(event.user_id)))


brk = on_regex(_pat(r"(?:突破|晋升|升星)"), priority=PRI, block=True, rule=_mode_is("doupo"))


@brk.handle()
async def _(event: Event):
    event = view(event)  # 2026-11-16 双通道兼容（官方=openid/NapCat=QQ号）
    await _need_group(event, brk)
    await brk.finish(await commands.cmd_break(store, str(event.user_id)))


# 完整战斗回看 / 战斗评价（2026-11-17）
# 必须先于「战斗」命中：短口令「战斗」若在 PRI 相同情况下先注册，会把「战斗回看」
# 当成普通开打，用户就永远看不到完整战报。这里用 PRI-1 明示抢先，与「斗技详情」同口径。
battle_review_cmd = on_regex(
    _pat(r"(?:战斗回看|回看战斗|完整战斗|战斗详情)"),
    priority=PRI - 1, block=True, rule=_mode_is("doupo"),
)


@battle_review_cmd.handle()
async def _(event: Event):
    event = view(event)
    await _need_group(event, battle_review_cmd)
    uid = str(event.user_id)
    msg = await commands.cmd_battle_review(store, uid)
    p = await store.get_player(uid)
    last = p.get("last_battle") if p else None
    pages: list[bytes] = []
    if isinstance(last, dict) and last.get("log"):
        try:
            pages = await card.render_battle(last, qq=uid)
        except Exception as e:  # noqa: BLE001 — 出图异常必须有文本兜底，不许静默
            logger.warning("战斗回看卡渲染异常，降级纯文本：%s: %s", type(e).__name__, e)
    if pages:
        # card_key="battle" → 官方通道同条消息挂「战斗评价」按钮，绝不分离发送。
        # 超长战报（26 回合 ≈5,200px）由模板按回合切页，多页仍是**一条**消息
        # （官方 markdown 内嵌多图 / NapCat 多图片段），不是「文字+卡」两条。
        await finish_card(battle_review_cmd, event, pages, "battle", "doupo_battle.png")
        return
    await finish_text(battle_review_cmd, event, msg)


battle_comment_cmd = on_regex(_pat(r"战斗评价"), priority=PRI - 1, block=True,
                              rule=_mode_is("doupo"))


@battle_comment_cmd.handle()
async def _(event: Event):
    event = view(event)
    await _need_group(event, battle_comment_cmd)
    await finish_text(
        battle_comment_cmd, event,
        await commands.cmd_battle_comment(store, str(event.user_id)),
    )


fight = on_regex(_pat(r"(?:战斗|打怪|历练|出战)"), priority=PRI, block=True, rule=_mode_is("doupo"))


@fight.handle()
async def _(event: Event):
    event = view(event)  # 2026-11-16 双通道兼容（官方=openid/NapCat=QQ号）
    await _need_group(event, fight)
    await finish_text(fight, event, await commands.cmd_fight(store, str(event.user_id)),
                      card_key="fight_text")


# ---------------------------------------------------------------------------
# 试炼之塔（2026-11-17）：无限层冲榜 / 宝箱掉神兵 / 三选一
# 同一个 matcher 兼顾「爬塔」与「裸数字 1/2/3」：
#   * 用户要求战利品直接回 1/2/3，不要「爬塔 1」这种复杂口令；
#   * 不新增 matcher —— 菜单数 37 = matcher 数（test_e2e 等量守门），
#     单开一个数字 matcher 会破坏这条基准。
# ---------------------------------------------------------------------------
tower = on_regex(_pat(r"爬塔") + r"\s*(.*)" + r"|^#?\s*([1-9])\s*$",
                 priority=PRI, block=True, rule=_mode_is("doupo"))


@tower.handle()
async def _(event: Event):
    event = view(event)
    await _need_group(event, tower)
    raw = _plain(event).strip().lstrip("#").strip()
    if raw.isdigit():
        # 裸数字：塔战利品优先，其次机遇竞猜；都没有时静默（不吞群聊、不回复）
        msg = await commands.cmd_pick(store, str(event.user_id), int(raw))
        if msg:
            await finish_text(tower, event, msg)
        return
    arg = _arg(event, "爬塔")
    await finish_text(tower, event, await commands.cmd_tower(store, str(event.user_id), arg))


tower_status = on_regex(_pat(r"(?:塔层|塔状态)"), priority=PRI, block=True,
                        rule=_mode_is("doupo"))


@tower_status.handle()
async def _(event: Event):
    event = view(event)
    await _need_group(event, tower_status)
    await finish_text(tower_status, event,
                      await commands.cmd_tower_status(store, str(event.user_id)))


tower_settle = on_regex(_pat(r"(?:塔结算|收手)"), priority=PRI, block=True,
                        rule=_mode_is("doupo"))


@tower_settle.handle()
async def _(event: Event):
    event = view(event)
    await _need_group(event, tower_settle)
    await finish_text(tower_settle, event,
                      await commands.cmd_tower_settle(store, str(event.user_id)))


# 电梯（2026-11-17）：花金币直达上次止步层，满状态重打，旧层奖励不重复。
tower_elevator = on_regex(_pat(r"(?:爬塔电梯|电梯|坐电梯)"), priority=PRI,
                          block=True, rule=_mode_is("doupo"))


@tower_elevator.handle()
async def _(event: Event):
    event = view(event)
    await _need_group(event, tower_elevator)
    await finish_text(tower_elevator, event,
                      await commands.cmd_tower_elevator(store, str(event.user_id)))


# 周副本（2026-11-17）：7 层一命通关 / 周一快照难度 / 失败全清
weekly = on_regex(_pat(r"(?:周副本|周本|周塔)"), priority=PRI, block=True,
                  rule=_mode_is("doupo"))


@weekly.handle()
async def _(event: Event):
    event = view(event)
    await _need_group(event, weekly)
    await finish_text(weekly, event,
                      await commands.cmd_weekly(store, str(event.user_id)))


# PRI-1 抢在「周副本」之前命中：否则「周本状态」会被短口令「周本」先吃掉。
weekly_status = on_regex(_pat(r"(?:周本状态|周副本状态|周塔状态)"),
                         priority=PRI - 1, block=True, rule=_mode_is("doupo"))


@weekly_status.handle()
async def _(event: Event):
    event = view(event)
    await _need_group(event, weekly_status)
    await finish_text(weekly_status, event,
                      await commands.cmd_weekly_status(store, str(event.user_id)))


equip = on_regex(_pat(r"装备") + r"\s*(.*)", priority=PRI, block=True,
                 rule=_mode_is("doupo"))


@equip.handle()
async def _(event: Event):
    event = view(event)
    await _need_group(event, equip)
    arg = _arg(event, "装备")
    await finish_text(equip, event, await commands.cmd_equip(store, str(event.user_id), arg))


# ---------------------------------------------------------------------------
# 强化 / 斗技 / 异火
# ---------------------------------------------------------------------------
enh = on_regex(_pat(r"强化"), priority=PRI, block=True, rule=_mode_is("doupo"))


@enh.handle()
async def _(event: Event):
    event = view(event)  # 2026-11-16 双通道兼容（官方=openid/NapCat=QQ号）
    await _need_group(event, enh)
    await finish_text(enh, event, await commands.cmd_enhance(store, str(event.user_id)))


# 学斗技必须注册在 skills_cmd 之前（同 PRI 下先注册者先拦），否则「学斗技」
# 里含「斗技」会被列表口令 skills_cmd 吃掉。与「斗技详情」同属需要抢先的精确
# 口令，但保持 priority=PRI（不额外降优先级），与全部 matcher 一致。
# 2026-11-16 扩展：同一 matcher 兼收「学习 <技名>」定向领悟（用户截图明确诉求，
# 不新增 matcher 以免破坏菜单/matcher 数守门）。「学习」不含「斗技」二字，
# 不会被 skills_cmd 吃；也不同于「学斗技」（无「习」字）。
insight_cmd = on_regex(_pat(r"(?:学斗技|领悟斗技|悟技|学习\s*\S+)"),
                       priority=PRI, block=True, rule=_mode_is("doupo"))


@insight_cmd.handle()
async def _(event: Event):
    event = view(event)  # 2026-11-16 双通道兼容（官方=openid/NapCat=QQ号）
    await _need_group(event, insight_cmd)
    txt = _plain(event)
    qq = str(event.user_id)
    # 「学习 X」→ 定向领悟 X；「学斗技/领悟斗技/悟技」→ 随机领悟。
    if "学习" in txt:
        await insight_cmd.finish(await commands.cmd_learn_named(store, qq, _arg(event, "学习")))
    await insight_cmd.finish(await commands.cmd_insight(store, qq))


# 斗技详情必须排在 skills_cmd 之前（同一 PRI 下先注册者先拦），否则「斗技详情 X」
# 会被「斗技」列表口令吃掉。这里再用 PRI-1 明示抢在列表前。
skinfo = on_regex(_pat(r"(?:斗技|技能|功法)详情") + r"\s*(.*)",
                  priority=PRI - 1, block=True, rule=_mode_is("doupo"))


@skinfo.handle()
async def _(event: Event):
    event = view(event)  # 2026-11-16 双通道兼容（官方=openid/NapCat=QQ号）
    await _need_group(event, skinfo)
    txt = _plain(event)
    kw = next((k for k in ("斗技详情", "技能详情", "功法详情") if k in txt), "斗技详情")
    await skinfo.finish(await commands.cmd_skill_info(store, str(event.user_id), _arg(event, kw)))


# 2026-11-24 新增「宗门斗技」= 本门技能池（与「斗技」= 已学全部互补）。
# 必须 PRI-1 + 注册在 sect（宗门/门派）与 skills_cmd（斗技/技能）之前：
# 该口令同时含「宗门」「斗技」二字，若同 PRI 会被这两个 matcher 抢答成宗门卡/
# 已学列表。PRI-1 保证先拦，block=True 阻止后续 matcher 重复回包。
faction_skills_cmd = on_regex(_pat(r"宗门斗技"),
                              priority=PRI - 1, block=True, rule=_mode_is("doupo"))


@faction_skills_cmd.handle()
async def _(event: Event):
    event = view(event)  # 2026-11-16 双通道兼容（官方=openid/NapCat=QQ号）
    await _need_group(event, faction_skills_cmd)
    qq = str(event.user_id)
    msg = await commands.cmd_faction_skills(store, qq)
    if msg == commands.NOT_ENTERED:
        await faction_skills_cmd.finish(msg)
    # 本门名录基本固定，出 roster 卡（与「斗技」合并卡同模板，序号口径同源
    # core.roster_no_map —— 「上阵 1 3 5 7」的编号只认这张卡的序号圆）
    png = None
    try:
        p = await store.get_player(qq)
        if p:
            png = await card.render_roster(p)
    except Exception as e:  # noqa: BLE001
        logger.warning("斗破宗门斗技卡渲染异常，降级纯文本：%s: %s", type(e).__name__, e)
    if png:
        await finish_card(faction_skills_cmd, event, png, "roster", "doupo_faction_skills.png")
        return
    await faction_skills_cmd.finish(msg)


# 注意：变量名不能叫 skills，否则会遮蔽 doupo.skills 子模块（core 依赖它）
skills_cmd = on_regex(_pat(r"(?:斗技|技能)"), priority=PRI, block=True, rule=_mode_is("doupo"))


@skills_cmd.handle()
async def _(event: Event):
    event = view(event)  # 2026-11-16 双通道兼容（官方=openid/NapCat=QQ号）
    await _need_group(event, skills_cmd)
    qq = str(event.user_id)
    # 纯文本兜底（commands.cmd_skills 即数据源，与图片同源）
    msg = await commands.cmd_skills(store, qq)
    # 2026-11-24 用户口径：原「斗技」（四槽上阵）与「斗技 全部」（已学习名录）
    # 合并为**一张卡**——固定出合并名录卡，「斗技 全部」子命令已取消。
    # 卡片画「已上阵」+「已领悟」两区；任何渲染异常才降级纯文本（绝不丢消息）。
    png = None
    try:
        p = await store.get_player(qq)
        if p:
            png = await card.render_learned(p)
    except Exception as e:  # noqa: BLE001
        logger.warning("斗破斗技合并卡渲染异常，降级纯文本：%s: %s", type(e).__name__, e)
    if png:
        await finish_card(skills_cmd, event, png, "skills", "doupo_skills.png")
        return
    await skills_cmd.finish(msg)


sup = on_regex(_pat(r"(?:升级|升技)") + r"\s*(.*)", priority=PRI, block=True, rule=_mode_is("doupo"))


@sup.handle()
async def _(event: Event):
    event = view(event)  # 2026-11-16 双通道兼容（官方=openid/NapCat=QQ号）
    await _need_group(event, sup)
    arg = _arg(event, "升级") if "升级" in _plain(event) else _arg(event, "升技")
    await sup.finish(await commands.cmd_skill_up(store, str(event.user_id), arg))


loadout = on_regex(_pat(r"(?:上阵|布阵|配置斗技)") + r"\s*(.*)", priority=PRI, block=True, rule=_mode_is("doupo"))


@loadout.handle()
async def _(event: Event):
    event = view(event)  # 2026-11-16 双通道兼容（官方=openid/NapCat=QQ号）
    await _need_group(event, loadout)
    txt = _plain(event)
    kw = "配置斗技" if "配置斗技" in txt else ("布阵" if "布阵" in txt else "上阵")
    await loadout.finish(await commands.cmd_loadout(store, str(event.user_id), _arg(event, kw)))


# 「宗门」看本门 + 可转列表；「转宗」执行改换门庭。
# 拆成两个 matcher（而不是一个吃四个词）是为了让 matcher 数 == 菜单条数，
# test_e2e 会断言这两个数相等（菜单是口令唯一源）。
sect = on_regex(_pat(r"(?:宗门|门派)") + r"\s*(.*)",
                priority=PRI, block=True, rule=_mode_is("doupo"))


@sect.handle()
async def _(event: Event):
    event = view(event)  # 2026-11-16 双通道兼容（官方=openid/NapCat=QQ号）
    await _need_group(event, sect)
    qq = str(event.user_id)
    # 纯文本兜底（与图片同源）
    msg = await commands.cmd_faction(store, qq, None)
    if msg == commands.NOT_ENTERED:
        await sect.finish(msg)
    # 宗门内容基本固定（概览/斗技统计/代表人物/异火/可转宗门），必须出卡片
    png = None
    try:
        p = await store.get_player(qq)
        if p:
            png = await card.render_faction(p)
    except Exception as e:  # noqa: BLE001
        logger.warning("斗破宗门卡渲染异常，降级纯文本：%s: %s", type(e).__name__, e)
    if png:
        await finish_card(sect, event, png, "faction", "doupo_faction.png")
        return
    await sect.finish(msg)


switch = on_regex(_pat(r"(?:转宗|换宗|改换门庭)") + r"\s*(.*)",
                  priority=PRI, block=True, rule=_mode_is("doupo"))


@switch.handle()
async def _(event: Event):
    event = view(event)  # 2026-11-16 双通道兼容（官方=openid/NapCat=QQ号）
    await _need_group(event, switch)
    txt = _plain(event)
    kw = next((k for k in ("改换门庭", "转宗", "换宗") if k in txt), "转宗")
    await switch.finish(await commands.cmd_faction(store, str(event.user_id), _arg(event, kw)))


flames = on_regex(_pat(r"异火"), priority=PRI, block=True, rule=_mode_is("doupo"))


@flames.handle()
async def _(event: Event):
    event = view(event)  # 2026-11-16 双通道兼容（官方=openid/NapCat=QQ号）
    await _need_group(event, flames)
    qq = str(event.user_id)
    p = await store.get_player(qq)
    # 2026-11-16 用户硬要求：本宗无传承异火 → 纯文本一句提示，
    # 绝不渲染卡片 / 挂按钮（避免本宗没异火却发一张异火图 + 分离的按钮条）。
    if not p or not commands.has_tradition_flame(p):
        await flames.finish(await commands.cmd_flames(store, qq))
        return
    # 有传承：先渲染卡片；渲染失败才降级纯文本（与纯文本同源）
    msg = await commands.cmd_flames(store, qq)
    png = None
    try:
        png = await card.render_flames(p)
    except Exception as e:  # noqa: BLE001
        logger.warning("斗破异火卡渲染异常，降级纯文本：%s: %s", type(e).__name__, e)
    if png:
        await finish_card(flames, event, png, "flames", "doupo_flames.png")
        return
    await flames.finish(msg)


feed = on_regex(_pat(r"(?:养火|喂火)") + r"\s*(.*)", priority=PRI, block=True, rule=_mode_is("doupo"))


@feed.handle()
async def _(event: Event):
    event = view(event)  # 2026-11-16 双通道兼容（官方=openid/NapCat=QQ号）
    await _need_group(event, feed)
    arg = _arg(event, "养火") if "养火" in _plain(event) else _arg(event, "喂火")
    await finish_text(feed, event, await commands.cmd_feed(store, str(event.user_id), arg))


# 2026-11-19 三层投放补齐：玩家主动收服入口（S 线门槛 / B 线直接 / A 线拒绝）。
# 正则刻意用「收服异火|收服」且不吃「傀儡 收服」（后者以「傀儡」起头，走傀儡 matcher）。
claim_flame = on_regex(_pat(r"(?:收服异火|收服)") + r"\s*(.*)", priority=PRI, block=True,
                       rule=_mode_is("doupo"))


@claim_flame.handle()
async def _(event: Event):
    event = view(event)
    await _need_group(event, claim_flame)
    txt = _plain(event)
    kw = "收服异火" if "收服异火" in txt else "收服"
    arg = _arg(event, kw)
    await finish_text(claim_flame, event,
                      await commands.cmd_claim_flame(store, str(event.user_id), arg))


# ---------------------------------------------------------------------------
# 宝库 / 炼药 / 背包
# ---------------------------------------------------------------------------
shop = on_regex(_pat(r"(?:宝库|商店|坊市)"), priority=PRI, block=True, rule=_mode_is("doupo"))


@shop.handle()
async def _(event: Event):
    event = view(event)  # 2026-11-16 双通道兼容（官方=openid/NapCat=QQ号）
    await _need_group(event, shop)
    qq = str(event.user_id)
    # 2026-11-24 兽火材料 28 种单独一卡：「宝库 兽火」切到兽火材料页
    _beast = "兽火" in (event.get_plaintext() or "")
    # 纯文本兜底（与图片同源）
    msg = await commands.cmd_shop(store, qq, beast=_beast)
    png = None
    try:
        p = await store.get_player(qq)
        if p:
            png = await card.render_shop(p, beast=_beast)
    except Exception as e:  # noqa: BLE001
        logger.warning("斗破宝库卡渲染异常，降级纯文本：%s: %s", type(e).__name__, e)
    if png:
        await finish_card(shop, event, png, "shop", "doupo_shop.png")
        return
    await shop.finish(msg)


# 2026-11-18 玩家自定义每张卡片底部按钮：配置入口（隐藏口令，不进菜单）。
# PRI-1 优先于普通口令，避免被「买」等抢；rule 限斗破模式。
# 参数从纯文本里剥掉口令首词取得（不用 _arg，它会 replace 掉所有匹配）。
btn_setup = on_regex(_pat(r"(?:按钮设置|自定义按钮)") + r"\s*(.*)",
                     priority=PRI - 1, block=True, rule=_mode_is("doupo"))


@btn_setup.handle()
async def _(event: Event):
    from . import custom_buttons as _cb  # noqa: PLC0415

    raw_event = event
    event = view(event)
    qq = str(event.user_id)
    plain = re.sub(r"^\s*#?\s*", "", _plain(event))
    m = re.match(r"(?:按钮设置|自定义按钮)\s*(.*)", plain, re.S)
    arg = (m.group(1) if m else "").strip()

    # 引用卡片识别：官方群消息 msg_type=103 时 event.reply 是被引用消息，
    # 其 content 里带发卡时嵌入的 dpq:<card_key> 标记。识别不出就传 None，
    # handle_config 会要求显式写卡片名——绝不猜（用户明确「识别不了就不理」）。
    quote_card = None
    try:
        reply = getattr(raw_event, "reply", None)
        if reply is not None:
            quote_card = _cb.card_key_from_content(str(getattr(reply, "content", "") or ""))
    except Exception:  # noqa: BLE001 — 引用解析是增强项，失败当没引用
        quote_card = None
    logger.info("btn_setup quote_card=%s arg=%r", quote_card, arg)

    await finish_text(btn_setup, event, await _cb.handle_config(store, qq, arg, quote_card=quote_card))


buy = on_regex(_pat(r"(?:买|购买)") + r"\s*(.*)", priority=PRI, block=True, rule=_mode_is("doupo"))


@buy.handle()
async def _(event: Event):
    event = view(event)  # 2026-11-16 双通道兼容（官方=openid/NapCat=QQ号）
    await _need_group(event, buy)
    arg = _arg(event, "购买") if "购买" in _plain(event) else _arg(event, "买")
    await finish_text(buy, event, await commands.cmd_buy(store, str(event.user_id), arg))


recipes = on_regex(_pat(r"(?:炼药|丹方)"), priority=PRI, block=True, rule=_mode_is("doupo"))


@recipes.handle()
async def _(event: Event):
    event = view(event)  # 2026-11-16 双通道兼容（官方=openid/NapCat=QQ号）
    await _need_group(event, recipes)
    qq = str(event.user_id)
    # 2026-11-16 分页：手打「炼药2/丹方3」翻页，无数字默认第 1 页。
    # 页码夹取与卡片/文本共用 core.recipes_page（越界落末页，不吐空页）。
    page = _num(event, 1)
    msg = await commands.cmd_recipes(store, qq, page)
    # 炼药丹方是近乎固定的清单 → 卡片化（等级过滤 + 两列 + 分页）；任何异常降级文本
    png = None
    total_pages = 1
    try:
        p = await store.get_player(qq)
        if p:
            _chunk, page, total_pages = core.recipes_page(p, page)
            png = await card.render_recipes(p, page)
    except Exception as e:  # noqa: BLE001
        logger.warning("斗破炼药卡渲染异常，降级纯文本：%s: %s", type(e).__name__, e)
    if png:
        await finish_card(recipes, event, png, "recipes", "doupo_recipes.png",
                          page=page, total_pages=total_pages)
        return
    await recipes.finish(msg)


# ---------------------------------------------------------------------------
# 「查看 <物品名>」物品详情卡（2026-11-25 用户要求）
# ---------------------------------------------------------------------------
# 用户原话：「加一个查看物品的功能，无论是背包还是宝库还是丹药，这些物品，都加
# 一个查看物品详情的功能，比如我输入查看 破厄丹，他就给我展示这个物品的卡片」。
# 与「服丹/锁定」的区别：那两条**要求背包里真有**（会扣物品），
# 查看只查图鉴 —— 宝库里没买的药材、还没炼过的丹，也得看得到。
# 关键词只认「查看」：与「斗技详情」这类「详情」口令不冲突，不会互相抢。
view_cmd = on_regex(_pat(r"查看") + r"\s*(.*)", priority=PRI, block=True,
                    rule=_mode_is("doupo"))


@view_cmd.handle()
async def _(event: Event):
    event = view(event)  # 2026-11-16 双通道兼容（官方=openid/NapCat=QQ号）
    await _need_group(event, view_cmd)
    qq = str(event.user_id)
    arg = _arg(event, "查看")
    name = commands.item_lookup(arg) if arg else ""
    txt = await commands.cmd_view(store, qq, arg)
    png = None
    if name:
        try:
            p = await store.get_player(qq)
            if p:
                png = await card.render_item_detail(p, name)
        except Exception as e:  # noqa: BLE001
            logger.warning("斗破物品详情卡渲染异常，降级纯文本：%s: %s",
                           type(e).__name__, e)
    if png:
        await finish_card(view_cmd, event, png, "item", "doupo_item.png")
        return
    await view_cmd.finish(txt)


craft = on_regex(_pat(r"(?:炼制|炼丹)") + r"\s*(.*)", priority=PRI, block=True, rule=_mode_is("doupo"))


@craft.handle()
async def _(event: Event):
    event = view(event)  # 2026-11-16 双通道兼容（官方=openid/NapCat=QQ号）
    await _need_group(event, craft)
    arg = _arg(event, "炼制") if "炼制" in _plain(event) else _arg(event, "炼丹")
    await finish_text(craft, event, await commands.cmd_craft(store, str(event.user_id), arg))


bag = on_regex(_pat(r"(?:背包|包裹|行囊)"), priority=PRI, block=True, rule=_mode_is("doupo"))


@bag.handle()
async def _(event: Event):
    event = view(event)  # 2026-11-16 双通道兼容（官方=openid/NapCat=QQ号）
    await _need_group(event, bag)
    qq = str(event.user_id)
    # 2026-11-24 不再翻页：背包全量一屏铺开（丹药优先，卡片随行数自动加高）。
    # 纯文本兜底（与图片同源）
    msg = await commands.cmd_bag(store, qq)
    png = None
    try:
        p = await store.get_player(qq)
        if p:
            # 锁定集合进卡面（左上「锁」），与 cmd_bag 同一数据源
            png = await card.render_bag(p, await store.item_locks(qq))
    except Exception as e:  # noqa: BLE001
        logger.warning("斗破背包卡渲染异常，降级纯文本：%s: %s", type(e).__name__, e)
    if png:
        await finish_card(bag, event, png, "bag", "doupo_bag.png")
        return
    await bag.finish(msg)


use = on_regex(_pat(r"(?:服丹|用丹|吃药)") + r"\s*(.*)", priority=PRI, block=True, rule=_mode_is("doupo"))


@use.handle()
async def _(event: Event):
    event = view(event)  # 2026-11-16 双通道兼容（官方=openid/NapCat=QQ号）
    await _need_group(event, use)
    txt = _plain(event)
    kw = "服丹" if "服丹" in txt else ("用丹" if "用丹" in txt else "吃药")
    await use.finish(await commands.cmd_use(store, str(event.user_id), _arg(event, kw)))


# ---------------------------------------------------------------------------
# 任务
# ---------------------------------------------------------------------------
tasks = on_regex(_pat(r"任务") + r"\s*$", priority=PRI, block=True, rule=_mode_is("doupo"))


@tasks.handle()
async def _(event: Event):
    event = view(event)  # 2026-11-16 双通道兼容（官方=openid/NapCat=QQ号）
    await _need_group(event, tasks)
    await tasks.finish(await commands.cmd_tasks(store, str(event.user_id)))


taccept = on_regex(_pat(r"接任务"), priority=PRI, block=True, rule=_mode_is("doupo"))


@taccept.handle()
async def _(event: Event):
    event = view(event)  # 2026-11-16 双通道兼容（官方=openid/NapCat=QQ号）
    await _need_group(event, taccept)
    await taccept.finish(await commands.cmd_task_accept(store, str(event.user_id)))


tsubmit = on_regex(_pat(r"交任务"), priority=PRI, block=True, rule=_mode_is("doupo"))


@tsubmit.handle()
async def _(event: Event):
    event = view(event)  # 2026-11-16 双通道兼容（官方=openid/NapCat=QQ号）
    await _need_group(event, tsubmit)
    await tsubmit.finish(await commands.cmd_task_submit(store, str(event.user_id)))


msubmit = on_regex(_pat(r"(?:领主线|交主线)"), priority=PRI, block=True, rule=_mode_is("doupo"))


@msubmit.handle()
async def _(event: Event):
    event = view(event)  # 2026-11-16 双通道兼容（官方=openid/NapCat=QQ号）
    await _need_group(event, msubmit)
    await msubmit.finish(await commands.cmd_main_submit(store, str(event.user_id)))


# ---------------------------------------------------------------------------
# 排行 / 抢 / 送
# ---------------------------------------------------------------------------
rank = on_regex(_pat(r"(?:排行|排行榜|战力榜)"), priority=PRI, block=True, rule=_mode_is("doupo"))


@rank.handle()
async def _(event: Event):
    event = view(event)  # 2026-11-16 双通道兼容（官方=openid/NapCat=QQ号）
    gid = await _need_group(event, rank)
    # 纯文本兜底（与图片同源）
    msg = await commands.cmd_rank(store, gid)
    png = None
    try:
        ps = await store.all_players(gid)
        top = core.rank(ps)[:10]
        png = await card.render_rank(top)
    except Exception as e:  # noqa: BLE001
        logger.warning("斗破排行卡渲染异常，降级纯文本：%s: %s", type(e).__name__, e)
    if png:
        await finish_card(rank, event, png, "rank", "doupo_rank.png")
        return
    await rank.finish(msg)


rob = on_regex(_pat(r"(?:抢|抢夺|掠夺)"), priority=PRI, block=True, rule=_mode_is("doupo"))


@rob.handle()
async def _(event: Event):
    event = view(event)  # 2026-11-16 双通道兼容（官方=openid/NapCat=QQ号）
    await _need_group(event, rob)
    await rob.finish(await commands.cmd_rob(store, str(event.user_id), _extract_at(event)))


# ---------------------------------------------------------------------------
# 无惩罚切磋（2026-11-16）：发起 → 应战/拒战，两段式，仅本群
# ---------------------------------------------------------------------------
# 变量名不要叫 duel_challenge：那会遮住同名的纯逻辑子模块
# （`from doupo import duel_challenge` 会拿到 matcher 而不是模块）。
duel_ask_cmd = on_regex(_pat(r"切磋"), priority=PRI, block=True,
                        rule=_mode_is("doupo"))


@duel_ask_cmd.handle()
async def _(event: Event):
    event = view(event)  # 双通道兼容（官方=openid / NapCat=QQ号）
    gid = await _need_group(event, duel_ask_cmd)
    await duel_ask_cmd.finish(await commands.cmd_duel_challenge(
        store, str(event.user_id), _extract_at(event), gid, _name(event)))


duel_accept = on_regex(_pat(r"(?:应战|接受切磋)"), priority=PRI, block=True,
                       rule=_mode_is("doupo"))


@duel_accept.handle()
async def _(event: Event):
    event = view(event)
    gid = await _need_group(event, duel_accept)
    await duel_accept.finish(await commands.cmd_duel_accept(
        store, str(event.user_id), gid))


duel_decline = on_regex(_pat(r"(?:拒战|拒绝切磋)"), priority=PRI, block=True,
                        rule=_mode_is("doupo"))


@duel_decline.handle()
async def _(event: Event):
    event = view(event)
    gid = await _need_group(event, duel_decline)
    await duel_decline.finish(await commands.cmd_duel_decline(
        str(event.user_id), gid))


# ---------------------------------------------------------------------------
# 傀儡（2026-11-18）：一个 matcher 同时吃「炼制傀儡」和「傀儡[子命令]」。
# 故意不拆成两条同优先级 matcher——否则「炼制傀儡」会被「傀儡」抢答。
# 「傀儡」是出图口令：必须真出图，渲染失败只显式报错，绝不静默降级为纯文字。
# ---------------------------------------------------------------------------
puppet_cmd = on_regex(
    _pat(r"(?:炼制傀儡|傀儡)") + r"\s*(.*)", priority=PRI - 1, block=True,
    rule=_mode_is("doupo"))


@puppet_cmd.handle()
async def _(event: Event):
    event = view(event)  # 2026-11-16 双通道兼容（官方=openid/NapCat=QQ号）
    await _need_group(event, puppet_cmd)
    qq = str(event.user_id)
    raw = _plain(event)
    if "傀儡遗迹" in raw:
        # 一次点击只出一条卡片：战况（段位/胜负/回合/掉落）由 cmd_puppet_ruin 写进
        # report，再并进同一张进度卡。2026-11-18 用户明确否掉了「战报一条 + 卡一条」。
        rep: dict = {}
        txt = await commands.cmd_puppet_ruin(store, qq, puppet_store_inst, report=rep)
        png = None
        try:
            if not await store.get_player(qq):
                await puppet_cmd.finish(txt)  # 未入道者无卡可出，不是降级
                return
            day = date.today().isoformat()
            png = await card.render_ruin_for(qq, day, puppet_store_inst, rep)
        except Exception as e:  # noqa: BLE001
            logger.error("傀儡遗迹卡渲染失败（拒绝静默降级）：%s: %s",
                         type(e).__name__, e)
        if png:
            await finish_card(puppet_cmd, event, png, "ruin", "doupo_ruin.png")
            return
        await puppet_cmd.finish("⚠️ 傀儡遗迹卡渲染失败，请稍后重试（未用纯文字代替图片）。\n" + txt)
        return
    if "炼制傀儡" in raw:
        # 2026-11-20 多配方：「炼制傀儡 <配方名>」按配方开炉；留空自动选最高档。
        # 2026-11-20 第十一轮：成傀改出卡（用户明确要求）。
        # 失败/引导仍是文本——那些回复要逐条说清缺什么料、卡在哪一道资格，
        # 卡片塞不下也不该塞；成卡与失败文本互斥（一处输出=一条消息）。
        _m = re.search(r"炼制傀儡\s*(.*)", raw)
        _arg = (_m.group(1) if _m else "").strip()
        rep: dict = {}
        txt = await commands.cmd_craft_puppet(
            store, qq, puppet_store_inst, _arg, report=rep)
        png = None
        if rep.get("ok"):
            try:
                png = await card.render_craft_for(rep, qq=qq)
            except Exception as e:  # noqa: BLE001
                logger.error("傀儡炼制卡渲染失败（拒绝静默降级）：%s: %s",
                             type(e).__name__, e)
        if png:
            await finish_card(puppet_cmd, event, png, "craft", "doupo_craft.png")
            return
        if rep.get("ok"):
            # 真炼成了但图没出：成卡内容必须仍然让玩家看到（炼一炉不可逆、
            # 材料与斗气已经扣掉），绝不能让「成了什么」静默消失。
            await puppet_cmd.finish(
                "⚠️ 炼制卡渲染失败，请稍后重试（未用纯文字代替图片）。\n" + txt)
            return
        await finish_text(puppet_cmd, event, txt)
        return
    m = re.search(r"傀儡\s*(.*)", raw)
    arg = (m.group(1) if m else "").strip()
    txt = await commands.cmd_puppet(store, qq, arg, puppet_store_inst)
    if commands.puppet_is_subcmd(arg):
        # 子命令是**动作回执**，必须原样发出：淬炼当场扣雷淬石与金币，收服/融魂
        # 改的是真实列。此前这里无条件出傀儡卡，把这些回执整条吞掉——玩家花了
        # 材料只收到一张没有变化的名录卡，于是「傀儡淬炼」看起来像没触发
        # （2026-11-20 第十一轮实锤：命令真跑了、料真扣了、回执被卡吞了）。
        await finish_text(puppet_cmd, event, txt)
        return
    try:
        p = await store.get_player(qq)
    except Exception as e:  # noqa: BLE001
        logger.warning("傀儡卡取玩家失败：%s: %s", type(e).__name__, e)
        p = None
    if not p:
        # 未入道者本来就没有卡可出，按文本引导（不是出图口令降级）
        await puppet_cmd.finish(txt)
        return
    png = None
    try:
        png = await card.render_puppet_for(qq, p, puppet_store_inst)
    except Exception as e:  # noqa: BLE001
        logger.error("斗破傀儡卡渲染失败（拒绝静默降级为纯文字）：%s: %s",
                     type(e).__name__, e)
    if png:
        await finish_card(puppet_cmd, event, png, "puppet", "doupo_puppet.png")
        return
    await puppet_cmd.finish("⚠️ 傀儡卡渲染失败，请稍后重试（未用纯文字代替图片）。")


# ---------------------------------------------------------------------------
# 背包锁定（2026-11-19）：锁定后战斗不自动服用，手动服丹照旧。
# ---------------------------------------------------------------------------
item_lock = on_regex(_pat(r"(?:锁定|解锁)\s*\S"), priority=PRI, block=True,
                     rule=_mode_is("doupo"))


@item_lock.handle()
async def _(event: Event):
    event = view(event)  # 2026-11-16 双通道兼容（官方=openid/NapCat=QQ号）
    await _need_group(event, item_lock)
    raw = _plain(event)
    m = re.search(r"(锁定|解锁)\s*(.+)$", raw)
    on = bool(m) and m.group(1) == "锁定"
    name = (m.group(2) if m else "").strip()
    await item_lock.finish(
        await commands.cmd_lock(store, str(event.user_id), name, on))


# ---------------------------------------------------------------------------
# 后期天焚炼气塔（聚火壶）——2026-11-19 修炼大改
# 「入塔」进塔并结算塔修；「出塔」离开并开始冷却；「火能」看状态；「换卡」升火晶卡。
# ---------------------------------------------------------------------------
tower_in = on_regex(_pat(r"入塔"), priority=PRI, block=True, rule=_mode_is("doupo"))


@tower_in.handle()
async def _(event: Event):
    event = view(event)
    await _need_group(event, tower_in)
    await tower_in.finish(await commands.cmd_furnace_enter(store, str(event.user_id)))


tower_out = on_regex(_pat(r"出塔"), priority=PRI, block=True, rule=_mode_is("doupo"))


@tower_out.handle()
async def _(event: Event):
    event = view(event)
    await _need_group(event, tower_out)
    await tower_out.finish(await commands.cmd_furnace_exit(store, str(event.user_id)))


tower_fire = on_regex(_pat(r"(?:火能|聚火壶)"), priority=PRI, block=True,
                      rule=_mode_is("doupo"))


@tower_fire.handle()
async def _(event: Event):
    event = view(event)
    await _need_group(event, tower_fire)
    await tower_fire.finish(await commands.cmd_furnace_status(store, str(event.user_id)))


tower_card = on_regex(_pat(r"换卡"), priority=PRI, block=True, rule=_mode_is("doupo"))


@tower_card.handle()
async def _(event: Event):
    event = view(event)
    await _need_group(event, tower_card)
    await tower_card.finish(await commands.cmd_furnace_upgrade(store, str(event.user_id)))


give = on_regex(_pat(r"(?:送|赠送)"), priority=PRI, block=True, rule=_mode_is("doupo"))


@give.handle()
async def _(event: Event):
    event = view(event)  # 2026-11-16 双通道兼容（官方=openid/NapCat=QQ号）
    await _need_group(event, give)
    # 2026-11-20 赠送扩到任意背包物品：`送 回灵丹 5 @某人` 走 cmd_gift，
    # `送 100 @某人` 仍是金币（parse_gift 只认「整串是数字」）。
    # 数字不再用 `_num(event)`：那条取全文第一个数字，官方通道把 @ 解析成
    # openid 后容易把一串数字当成金额，这里统一从纯文本里解析。
    item, n = commands.parse_gift(_plain(event))
    target = _extract_at(event)
    if item:
        await give.finish(await commands.cmd_gift(
            store, str(event.user_id), target, item, n))
    await give.finish(await commands.cmd_give(
        store, str(event.user_id), target, n or None))


# ---------------------------------------------------------------------------
# 启动：载入存档
# ---------------------------------------------------------------------------
@get_driver().on_startup
async def _startup():
    await store.load()


# 口令关键字登记（供 game_router 兜底探测）
register_keywords("doupo", [
    "斗破菜单",
    "斗破口令",
    "帮助",
    "玩法",
    "攻略",
    "规则",
    "入道",
    "开始",
    "创建角色",
    "人物",
    "属性",
    "面板",
    "修炼",
    "静修",
    "打坐",
    "突破",
    "晋升",
    "升星",
    "战斗",
    "战斗回看",
    "回看战斗",
    "完整战斗",
    "战斗详情",
    "战斗评价",
    "打怪",
    "历练",
    "出战",
    "爬塔",
    "塔层",
    "塔状态",
    "塔结算",
    "收手",
    "电梯",
    "爬塔电梯",
    "坐电梯",
    "周副本",
    "周本",
    "周塔",
    "周本状态",
    "周副本状态",
    "装备",
    "强化",
    "斗技",
    "技能",
    "宗门",
    "门派",
    "转宗",
    "换宗",
    "斗技详情",
    "技能详情",
    "功法详情",
    "升级",
    "升技",
    "上阵",
    "布阵",
    "配置斗技",
    "异火",
    "收服",
    "收服异火",
    "养火",
    "喂火",
    "宝库",
    "商店",
    "坊市",
    "买",
    "购买",
    "炼药",
    "丹方",
    "炼制",
    "炼丹",
    "背包",
    "包裹",
    "行囊",
    "服丹",
    "用丹",
    "吃药",
    "任务",
    "接任务",
    "交任务",
    "领主线",
    "交主线",
    "排行",
    "排行榜",
    "战力榜",
    "抢",
    "抢夺",
    "掠夺",
    "切磋",
    "应战",
    "接受切磋",
    "拒战",
    "拒绝切磋",
    "送",
    "赠送",
    "炼制傀儡",
    "傀儡",
    "傀儡遗迹",
    "锁定",
    "解锁",
    "入塔",
    "出塔",
    "火能",
    "聚火壶",
    "换卡",
])
