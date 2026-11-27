# -*- coding: utf-8 -*-
"""斗破卡片「对应功能操作按钮」派生（仅官方副本，2026-11-16）。

## 为什么单独一层

game_tools 的按钮基准（``command_inventory`` / ``card_buttons``）完全建立在
warframe 的 ``_handle`` 上，斗破口令一条都不在它的 inventory 里；直接复用会让
斗破卡永远零按钮。而 ``qq_keyboard.buttons_for`` 是官方发送链路唯一的按钮入口，
所以本模块提供「斗破派生」并挂到它的 fallback 上——**不动** game_tools 的档位族
纪律，也不改 official.py 任何一行（`has_buttons` / `make_keyboard` 自动生效）。

放在 channel 层（与 game_tools 的 card_buttons.py / command_inventory.py 同层）
而非 doupo 插件包内：本模块零 nonebot 依赖，宿主可直接单测；若放插件包内，
import 会触发 ``doupo/__init__.py`` 顶层的 on_regex 注册（nonebot 未初始化时报错）。

## 事实源与零编造

* 事实源 1（AST，:func:`real_commands`）：从 ``commands.MENU_GROUPS`` 提取 29 个
  canonical 首词口令（菜单唯一源，与 ``register_keywords`` / ``on_regex(_pat)`` 同步）。
* 事实源 2（声明，:data:`CARD_ACTIONS`）：每张出图卡 → 该卡语境下的下钻按钮。

守门铁律（tests/test_doupo_buttons.py 用**第二套独立 AST 遍历**反向设防）：
  1. 每个按钮值 ∈ 事实源 1（零编造，data 点击后必为真实口令）；
  2. 按钮 ≠ 当前卡触发口令，≠ 主菜单（斗破菜单/修仙菜单/帮助）；
  3. 按钮不与其功能族冲突（跨卡组合两两不同，杜绝「每张卡都一样」的全局导航）；
  4. 平台结构：每卡 1~5 个、label ≤10、primary 恒 False。

## 卡片 / 按钮映射（用户「每个卡片配置合理按钮、功能不要重叠」）

按钮只做「这张卡内容的下一步」——人物卡给养成动作、名录卡给斗技下钻、商店卡
给购买/炼药……不给全局导航，不在每张卡塞「帮助/菜单」。按钮 data 是 canonical
首词（如 ``修炼``），点击后由 ``qq_interaction`` 识别并用 ``commands.cmd_*`` 分发。

本模块零 nonebot 依赖（宿主可单测）；keyboard 模型仍由 qq_keyboard.make_keyboard
用本模块返回的 :class:`Btn` 构造，不重复实现。
"""
from __future__ import annotations

import ast
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path

_COMMANDS_PY = Path(__file__).resolve().parents[2] / "doupo" / "commands.py"

#: 主菜单 / 切换口口令：任何卡片按钮都不得出现（用户「去掉主菜单按钮」）。
MENU_CMDS = frozenset({"斗破菜单", "斗破口令", "修仙菜单", "帮助"})

#: 有奖竞猜选项按钮的哨兵前缀（2026-11-16）。命令层注册答题后把
#: ``keyboard_cmd = DOUPO_QUIZ_PREFIX + token`` 交给发送层；按钮 data 形如
#: ``doupo:quiz:<token>:<idx>``，与卡片口令空间（ALL_BUTTON_CMDS）完全隔离，
#: 不污染 game_tools 档位族守门。选项文字随 per-task 上下文传进来（不写进 data）。
DOUPO_QUIZ_PREFIX = "doupo:quiz:"

#: 炼药丹方卡「翻页」按钮前缀（2026-11-16 用户「丹药多了分页 / 按钮切换」）。
#: 与 quiz 同理：独立前缀，不进 CARD_ACTIONS / ALL_BUTTON_CMDS，不污染 canonical
#: 口令守门。data = ``doupo:page:<page>``（page 1-based，由发送层按当前页派生
#: 「上一页/下一页」）；点击后由 button_dispatch.dispatch_page 渲染对应页。
DOUPO_PAGE_PREFIX = "doupo:page:"
#: per-task 的炼药分页信息：{"page":cur, "total_pages":N}。发送层 arm，按钮派生读取。
_page_state: ContextVar[dict] = ContextVar("doupo_page_state", default={})

#: per-task 的竞猜选项表：token -> (label...)。NoneBot 每事件独立 task，天然隔离。
_quiz_options: ContextVar[dict] = ContextVar("doupo_quiz_options", default={})

#: per-task 的「玩家自定义按钮」覆盖表：card_key -> ((label, cmd), ...)。
#: 2026-11-18 用户「每个玩家自定义每张卡片底部按钮」。玩家配置存在存档里，
#: 发送层读出来后 arm 进来；:func:`buttons_for_card` 命中本卡时优先用它，
#: 彻底替换该卡固定按钮（玩家对每个按钮有增删改权，菜单卡除外）。
#: 与 quiz/page 同构用 ContextVar：NoneBot 每事件独立 task，天然并发隔离不串号。
DOUPO_CUSTOM_PREFIX = "doupo:cbtn:"
_custom_buttons: ContextVar[dict] = ContextVar("doupo_custom_buttons", default={})

#: 2026-11-26 per-task 的**动态按钮**表：card_key -> (Btn, ...)。
#:
#: 与上面 `_custom_buttons`（玩家在存档里自定义的按钮）分开：这一层是**业务按
#: 玩家实时数据现算**的按钮，如「傀儡出战」回执要按名录给每具一颗直接出战按钮。
#: 固定 CARD_ACTIONS 无法表达（按钮数量/文字都随存档变），所以走 ContextVar 由
#: 分发层 arm，:func:`buttons_for_card` 命中本卡时优先返回。
_dynamic_buttons: ContextVar[dict] = ContextVar("doupo_dynamic_buttons", default={})


def arm_dynamic(card_key: str, buttons: tuple[Btn, ...]) -> object:
    """登记本回合某卡的动态按钮，返回 ContextVar token 供 detach。

    与 :func:`arm_custom` 的区别：这里收的是**已经构好的 Btn**（业务层自己决定
    label 与 data，含 ``doupo:cbtn:`` 带参哨兵），本函数不做任何编码/截断。
    同名 card_key 后 arm 覆盖先 arm（同一次回复只可能有一组）。
    """
    k = (card_key or "").strip()
    table = dict(_dynamic_buttons.get() or {})
    table[k] = tuple(buttons or ())
    return _dynamic_buttons.set(table)


def disarm_dynamic(token) -> None:
    try:
        _dynamic_buttons.reset(token)
    except (ValueError, TypeError):
        pass



def arm_custom(buttons: tuple[tuple[str, str], ...], card_key: str) -> object:
    """登记本回合某卡的玩家自定义按钮，返回 ContextVar token 供 detach。

    ``buttons`` 是 ``((label, real_cmd), ...)``；编码成哨兵 data 在这里做，
    让按钮派生层之外的调用方不必知道 :mod:`doupo.custom_buttons` 的细节。
    """
    encoded = tuple(
        Btn(label[:10], DOUPO_CUSTOM_PREFIX + cmd.strip(), False)
        for label, cmd in (buttons or [])
        if label and cmd
    )
    return _custom_buttons.set({(card_key or "").strip(): encoded})


def disarm_custom(token) -> None:
    try:
        _custom_buttons.reset(token)
    except (ValueError, TypeError):
        pass


def arm_quiz(token: str, options) -> object:
    """登记本回合竞猜选项，返回 ContextVar token 供 handler detach。"""
    return _quiz_options.set({str(token): [str(o)[:10] for o in options]})


def disarm_quiz(token) -> None:
    try:
        _quiz_options.reset(token)
    except (ValueError, TypeError):
        pass


def arm_page(page: int, total_pages: int) -> object:
    """登记本回合炼药卡分页状态，返回 ContextVar token 供 handler detach。"""
    return _page_state.set({"page": int(page), "total_pages": int(total_pages)})


def disarm_page(token) -> None:
    try:
        _page_state.reset(token)
    except (ValueError, TypeError):
        pass


def page_buttons_for(cmd: str) -> tuple[Btn, ...]:
    """从 ``doupo:page:<page>`` 派生「上一页/下一页」按钮；非本前缀返 ``()``。

    页码来自 ``_page_state``（发送层 arm 的当前页）；按钮 data 写字面目标页码，
    如当前第 2/4 页 → [上一页 → doupo:page:1, 下一页 → doupo:page:3]。
    单页（total_pages<=1）不挂翻页按钮（发送层此时也不设 page keyboard_cmd）。
    """
    raw = (cmd or "").strip()
    if not raw.startswith(DOUPO_PAGE_PREFIX):
        return ()
    st = _page_state.get() or {}
    page = int(st.get("page", 0) or 0)
    total = int(st.get("total_pages", 0) or 0)
    if page < 1 or total < 2:
        return ()
    out: list[Btn] = []
    if page > 1:
        out.append(Btn("上一页", "%s%d" % (DOUPO_PAGE_PREFIX, page - 1), False))
    if page < total:
        out.append(Btn("下一页", "%s%d" % (DOUPO_PAGE_PREFIX, page + 1), False))
    return tuple(out)


def quiz_buttons_for(cmd: str) -> tuple[Btn, ...]:
    """从 ``doupo:quiz:<token>`` 派生 4 个选项按钮；非本前缀返 ``()``。

    按钮 data = ``doupo:quiz:<token>:<idx>``；label = 选项文字。答案不在 data。
    """
    raw = (cmd or "").strip()
    if not raw.startswith(DOUPO_QUIZ_PREFIX):
        return ()
    token = raw[len(DOUPO_QUIZ_PREFIX):].strip()
    if not token:
        return ()
    opts = (_quiz_options.get() or {}).get(token) or []
    out: list[Btn] = []
    for i, label in enumerate(opts[:5]):
        out.append(Btn(label=label[:10], cmd="%s%s:%d" % (DOUPO_QUIZ_PREFIX, token, i),
                       primary=False))
    return tuple(out)


@dataclass(frozen=True)
class Btn:
    """与 qq_keyboard.Btn 同构（label / cmd / primary / kind），供 make_keyboard 直接消费。"""

    label: str
    cmd: str
    primary: bool = False
    #: 官方按钮的 ``action.type``（2026-11-26 补全）。平台一共只支持三种：
    #:
    #:   * ``callback``（type=1）—— 点击**回传事件**，客户端不发消息。本项目
    #:     所有既有按钮都是这一类（靠 ``INTERACTION_CREATE`` 走 qq_interaction）。
    #:     好处：能带任意 ``data``（如 ``doupo:bag``、``doupo:cbtn:炼制 破厄丹``），
    #:     不必是玩家真打得出来的口令。
    #:   * ``command``（type=2）—— 指令按钮：客户端把 ``data`` 当成玩家要发的消息。
    #:     官方文档：群聊里是「自动在输入框插入 @bot data」（``enter=True`` 直接发送
    #:     **仅单聊可用**），所以群聊点完还要玩家按一下发送。``data`` 必须是真人
    #:     可输入的完整口令。
    #:   * ``link``（type=0）—— 纯跳转（网页/小程序），``data`` 是 https 地址，
    #:     需要 ``unsupport_tips`` 兜底文案。不产生任何游戏事件。
    #:
    #: 默认 ``callback``：既有按钮行为一个字节都不变。
    kind: str = "callback"


def real_commands() -> tuple[str, ...]:
    """AST 从 ``commands.MENU_GROUPS`` 派生真实 canonical 口令全集（零编造基准）。

    每条 ``(口令, 说明)`` 取口令的**首词**作 canonical（``斗技详情 狂狮怒罡``→
    ``斗技详情``、``买 血灵草 2``→``买``、``送 100 @某人``→``送``）。这些首词
    与 ``on_regex(_pat(...))`` 的触发词同源（菜单是口令唯一源），点击后必命中。
    """
    tree = ast.parse(_COMMANDS_PY.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if any(isinstance(t, ast.Name) and t.id == "MENU_GROUPS" for t in targets):
                groups = ast.literal_eval(node.value)
                out: list[str] = []
                for _title, rows in groups:
                    for cmd, _desc in rows:
                        head = str(cmd).strip().split()[0]
                        if head and head not in out:
                            out.append(head)
                return tuple(out)
    return ()


#: 每张出图卡 → 该卡功能族的下钻按钮（canonical 口令，保序）。
#: key 同时是 ``Outgoing.keyboard_cmd`` 传入值（见 __init__.py 出图 handler）。
CARD_ACTIONS = {
    # 卡: (按钮 canonical...) —— 卡自身触发口令见 CARD_SELF，按钮绝不含它
    "menu": ("入道", "人物", "修炼", "战斗", "宝库"),
    "panel": ("修炼", "突破", "战斗", "强化", "斗技"),
    "roster": ("斗技详情", "升级", "上阵"),
    "skills": ("上阵", "升级", "异火", "斗技详情"),
    "faction": ("转宗", "斗技", "异火"),
    "flames": ("养火", "上阵", "斗技详情"),
    "shop": ("买", "炼药", "背包"),
    "bag": ("服丹", "炼药", "宝库", "装备"),
    "rank": ("战斗", "修炼", "爬塔", "塔层"),
    "recipes": ("服丹", "养火", "宝库"),
    "help": ("人物", "炼药", "斗技"),
    # 2026-11-17 完整战斗回看卡：用户要求卡片同时带「战斗评价」按钮。
    "battle": ("战斗评价",),
    # 2026-11-26 傀儡三卡按钮差异化（用户「别都搞一样」）：每张聚焦该场景的
    # 下一步，不再千篇一律挂「战斗」。真人操作路径：
    #   名录 → 炼新/看图鉴/换出战；遗迹 → 去炼/看名录/查图鉴；
    #   成傀 → 立刻上阵/看名录/打一场试刀；图鉴 → 照方去炼/打料/看名录。
    # ⚠️ 按钮只许用菜单里真实存在的口令（「傀儡 淬炼」是子命令、不在菜单 → 判编造）。
    "puppet": ("炼制傀儡", "傀儡图鉴", "傀儡出战"),
    "ruin": ("炼制傀儡", "傀儡", "傀儡图鉴"),
    "craft": ("傀儡出战", "傀儡", "战斗"),
    # 2026-11-26 傀儡图鉴卡：看完配方，下一步照方炼 / 换上阵 / 回名录看已有傀儡
    "codex": ("炼制傀儡", "傀儡出战", "傀儡"),
    # 2026-11-25 物品详情卡：看完详情最自然的下一步是「吃掉/去炼/再逛逛」
    "item": ("服丹", "炼药", "宝库", "背包"),
    # 2026-11-25 战斗**纯文本**回包（用户 Q2 拍板：文本+按钮同一条发）。
    # ⚠️ 与 "battle" 语义不同、绝不共用键："battle" 是「战斗回看卡」自己的
    # 下钻按钮（只挂战斗评价），这里是「刚打完这一场」的功能组
    # （回看 / 评价 / 任务）。混用会让回看卡多出不相关的「任务」按钮。
    "fight_text": ("战斗回看", "战斗评价", "战斗", "任务"),
    # 2026-11-25 晚：按回复类型精确配按钮（用户「对应卡片只放对应的功能按钮，
    # 别都批量放一堆主菜单」「战报加继续战斗」「处理好每一个回复的按钮」）。
    # 这些 key 由 game_compat.reply_card_key 按**入站口令**派生，专供纯文字回复。
    "task_panel": ("领主线", "交任务", "接任务", "战斗"),      # 📜 任务面板
    "main_submit": ("任务", "战斗", "修炼"),                    # 🏅 主线完成回执
    "task_submit": ("任务", "领主线", "战斗"),                  # 支线交任务回执
    "battle_text": ("战斗回看", "战斗", "任务"),                # 战斗评价纯文字
    "review_text": ("战斗评价", "战斗", "任务"),                # 战斗回看纯文字
    "tower_text": ("塔层", "塔结算", "自动爬塔", "战斗"),        # 爬塔回执
    "weekly_text": ("周本状态", "战斗", "人物"),                # 周副本回执
    "cult_text": ("战斗", "突破", "人物"),                      # 修炼/突破回执
    "equip_text": ("强化", "人物", "战斗"),                     # 装备/强化回执
    "skill_text": ("斗技", "上阵", "人物"),                     # 学斗技/升级/上阵回执
    "flame_text": ("养火", "异火", "人物"),                      # 收服/养火回执
    "buy_text": ("买", "炼药", "宝库"),                         # 购买回执（区别于 shop）
    # 2026-11-27「卖 <物品> [数量]」回执：下一步继续卖（背包看存货）/ 查买价（宝库）/ 去炼（炼药）。
    # 按钮绝不含「卖」自己（self 检查）；组合也不复用 shop 的 (买,炼药,背包)。
    "sell_text": ("背包", "宝库", "炼药"),                      # 卖出回执
    "craft_text": ("炼药", "服丹", "背包"),                     # 炼丹回执
    "use_text": ("背包", "炼药", "查看"),                       # 服丹回执
    "shop_text": ("买", "宝库", "背包"),                        # 商店文字回执
    "enter_text": ("人物", "修炼", "战斗"),                     # 入道/主题回执
    "puppet_text": ("傀儡", "炼制傀儡", "傀儡遗迹"),             # 傀儡子命令回执（区别于其他傀儡卡）
    # 2026-11-25 晚：切磋/应战/拒战回执（不惩罚切磋，最自然下一步是打一场或看人物）。
    "duel_text": ("战斗", "人物"),
    # 按钮设置/自定义按钮的回执（隐藏口令，不进菜单）。配完就该回去玩，
    # 给三个不重叠的常用入口；绝不给「斗破菜单」那一套主菜单按钮。
    "setup_text": ("人物", "背包", "战斗"),
    # 2026-11-26「尚未开放」提示回包（签到/炼器/驯兽/擂台/成就/战力/闭关/主线任务
    # 这 8 个词在 __init__.py 里有正则、点了只会回一句「暂未开放」）。此前它们
    # 一条按钮都没登记 → 官方群兜底成主菜单按钮（入道/人物/修炼/战斗/宝库），
    # 玩家点完还是原地。这里给「去做点正经事」的三键，与守门用例一一对齐。
    "tip_text": ("战斗", "修炼", "宝库"),
}

#: 按钮 label 覆盖（card_key → ((label, cmd), ...)）：cmd 仍是 canonical 真口令，
#: 只改玩家看到的字。用于「继续战斗」这类更友好的措辞（cmd=战斗）。
#: 未登记的 card_key 走 CARD_ACTIONS 原样（label=cmd）。
CARD_ACTIONS_PAIRS: dict[str, tuple[tuple[str, str], ...]] = {
    "fight_text": (
        ("战斗回看", "战斗回看"), ("战斗评价", "战斗评价"),
        ("继续战斗", "战斗"), ("任务", "任务"),
    ),
    "battle_text": (
        ("战斗回看", "战斗回看"), ("继续战斗", "战斗"), ("任务", "任务"),
    ),
    "review_text": (
        ("战斗评价", "战斗评价"), ("继续战斗", "战斗"), ("任务", "任务"),
    ),
    "task_panel": (
        ("领主线", "领主线"), ("交任务", "交任务"),
        ("接任务", "接任务"), ("继续战斗", "战斗"),
    ),
    "main_submit": (
        ("看任务", "任务"), ("继续战斗", "战斗"), ("修炼", "修炼"),
    ),
    "tower_text": (
        ("看塔层", "塔层"), ("塔结算", "塔结算"),
        ("自动爬塔", "自动爬塔"), ("继续战斗", "战斗"),
    ),
}

#: 入站口令首词 → 纯文字回复该挂的 card_key（2026-11-25 晚）。
#: 兜底桥 ``matcher.finish(str)`` 与未显式传 card_key 的 finish_text 都用它派生，
#: 一处收口覆盖全部历史写法，不用逐个改 47 处 handler。
#: 只对「玩家明确发起了某个功能」的口令映射；闲聊/未知口令不映射（保持零按钮兜底）。
REPLY_CARD_KEY: dict[str, str] = {
    "任务": "task_panel", "领主线": "main_submit", "交主线": "main_submit",
    "交任务": "task_submit", "接任务": "task_submit",
    "战斗评价": "battle_text", "战斗回看": "review_text",
    "爬塔": "tower_text", "塔层": "tower_text", "塔结算": "tower_text",
    # 2026-11-26 隐藏样例口令：不出图，只挂「三种 action.type」的样例键盘。
    "按钮样例": "btn_sample", "按钮测试": "btn_sample",
    "周副本": "weekly_text", "周本状态": "weekly_text",
    "修炼": "cult_text", "突破": "cult_text", "入塔": "cult_text",
    "出塔": "cult_text", "火能": "cult_text",
    "装备": "equip_text", "强化": "equip_text",
    "学斗技": "skill_text", "学习": "skill_text", "升级": "skill_text",
    "上阵": "skill_text", "斗技详情": "skill_text",
    "收服": "flame_text", "养火": "flame_text",
    "买": "buy_text", "购买": "buy_text",
    "卖": "sell_text", "出售": "sell_text", "变卖": "sell_text",
    "炼制": "craft_text", "炼丹": "craft_text",
    "服丹": "use_text", "锁定": "use_text", "解锁": "use_text",
    "入道": "enter_text", "主题": "enter_text",
    "傀儡": "puppet_text", "炼制傀儡": "puppet_text", "傀儡遗迹": "puppet_text",
    "傀儡图鉴": "codex", "傀儡出战": "puppet_text",
    # 2026-11-25 第五轮（用户「买材料炼药的回复自动携带默认按钮，检查所有口令」）：
    # 这里补齐全量菜单口令——**任何一条命令的回执都不许回落到主菜单按钮**。
    # 数值键必须指向真实存在的 CARD_ACTIONS 卡（validate 会查）。
    "斗破菜单": "menu", "斗破口令": "menu", "人物": "panel", "帮助": "help",
    "战斗": "fight_text",                      # ★「继续战斗」按钮点完必须还带战斗按钮
    "斗技": "skills", "宗门斗技": "faction", "宗门": "faction",
    "转宗": "skill_text", "异火": "flames",
    "宝库": "shop", "炼药": "recipes", "查看": "use_text", "背包": "bag",
    "排行": "rank", "抢": "rank",
    "切磋": "duel_text", "应战": "duel_text", "拒战": "duel_text",
    "送": "bag", "换卡": "cult_text",
    "自动爬塔": "auto_tower_prompt",
    # 按钮设置（隐藏口令，不进菜单）——回执也要有自己的按钮组，
    # 否则又是一排「入道/人物/修炼/战斗/宝库」。
    "按钮设置": "setup_text", "自定义按钮": "setup_text",
    # 2026-11-26 「尚未开放」的 8 个词（__init__.py 有一条兜底正则，回一句
    # 「暂未开放」）。没有这一组，它们的回执会回落主菜单按钮 —— 玩家点了原地。
    "签到": "tip_text", "炼器": "tip_text", "驯兽": "tip_text",
    "擂台": "tip_text", "成就": "tip_text", "战力": "tip_text",
    "闭关": "tip_text", "主线任务": "tip_text",
}

#: 入站**别名** → 主口令（主口令必须已在 ``REPLY_CARD_KEY`` 里）。
#:
#: 为什么要这一层：斗破的同一张卡常有 2~4 个触发词（「战斗 / 打怪 / 历练 / 出战」
#: 「背包 / 包裹 / 行囊」「宝库 / 商店 / 坊市」）。以前只登记主词，别的说法一律
#: 落空 → 官方群纯文本兜底到 ``doupo:menu``，回执底下就是默认主菜单按钮
#: （2026-11-25 用户实测「买材料炼药的回复自动携带默认按钮」的同类缺口）。
#: 别名只是**指向**主词，按钮族仍然只有一份，不会出现两套漂移。
REPLY_ALIAS: dict[str, str] = {
    # 战斗族
    "打怪": "战斗", "历练": "战斗", "出战": "战斗",
    "回看战斗": "战斗回看", "完整战斗": "战斗回看", "战斗详情": "战斗回看",
    # 塔族
    "开始": "爬塔", "聚火壶": "爬塔", "塔状态": "塔层", "收手": "塔结算",
    # 周本族（别名最多的一个 matcher）
    "周本": "周本状态", "周塔": "周本状态",
    "周塔状态": "周本状态", "周副本状态": "周本状态",
    # 修炼族
    "打坐": "修炼", "静修": "修炼", "晋升": "突破",
    # 装备 / 斗技族
    "升星": "强化", "升技": "升级", "功法": "斗技", "技能": "斗技",
    "悟技": "学斗技", "领悟斗技": "学斗技",
    "布阵": "上阵", "配置斗技": "上阵", "详情": "斗技详情",
    # 物品族
    "吃药": "服丹", "用丹": "服丹", "丹方": "炼药",
    "包裹": "背包", "行囊": "背包", "赠送": "送",
    # 商店 / 排行族
    "商店": "宝库", "坊市": "宝库",
    "排行榜": "排行", "战力榜": "排行", "抢夺": "抢", "掠夺": "抢",
    # 异火 / 宗门 / 切磋族
    "喂火": "养火", "收服异火": "收服",
    "门派": "宗门", "换宗": "转宗", "改换门庭": "转宗",
    "接受切磋": "应战", "拒绝切磋": "拒战",
    # 其它
    "创建角色": "入道", "切换": "换卡", "属性": "人物", "面板": "人物",
    "攻略": "帮助", "规则": "帮助", "玩法": "帮助",
    "默认": "按钮设置",
}

#: 卡片自身 canonical 触发口令（用于「按钮不含当前卡自己」守门）。
CARD_SELF = {
    "menu": "斗破菜单",
    "panel": "人物",
    "roster": "斗技",
    "skills": "斗技",
    "faction": "宗门",
    "flames": "异火",
    "shop": "宝库",
    "bag": "背包",
    "rank": "排行",
    "recipes": "炼药",
    "help": "帮助",
    "battle": "战斗回看",
    "puppet": "傀儡",
    "ruin": "傀儡遗迹",
    "craft": "炼制傀儡",
    "codex": "傀儡图鉴",
    "item": "查看",
    # 2026-11-25 战斗纯文本按钮组：自身触发口令就是「战斗」（这条回执的来源）。
    # 它不是一个「可触发的卡」，只是纯文本回执挂的按钮组，但在守门②眼里
    # 必须与「回看卡 battle」区分开——两者的按钮族不同（见 CARD_ACTIONS 注释）。
    "fight_text": "战斗回执",
    # 2026-11-25 晚：纯文字回执 key 不是「可点卡」，CARD_SELF 只用于守门的
    # 「按钮不含自身」检查；设成不与本族按钮冲突的虚拟词即可。
    "task_panel": "任务面板", "main_submit": "主线结算",
    "task_submit": "支线结算", "battle_text": "战斗评语", "review_text": "回看文本",
    "tower_text": "爬塔回执", "weekly_text": "周本回执", "cult_text": "修炼回执",
    "equip_text": "装备回执", "skill_text": "斗技回执", "flame_text": "异火回执",
    "buy_text": "购买回执", "sell_text": "卖出回执", "craft_text": "炼丹回执", "use_text": "服丹回执",
    "shop_text": "商店回执", "enter_text": "入道回执", "puppet_text": "傀儡回执",
    "duel_text": "切磋回执",
    "setup_text": "按钮设置", "tip_text": "未开放提示",
}

#: 2026-11-25：**不出图、只挂按钮**的提示型 card_key（buttons_for_card 里特判返回）。
#: 它们不是可渲染的卡（没有 render_*），但可以作为 keyboard_cmd 传给通道；
#: validate 与守门都认这一族，否则「提示卡」会被当成未知卡拒掉。
PROMPT_CARD_KEYS = frozenset({"auto_tower_prompt", "btn_sample"})

#: 2026-11-26「按钮样例」：把官方**三种 action.type** 各做一颗，供真机点验。
#:
#: 平台只支持三种按钮动作（见 :class:`Btn.kind`），本项目此前只用过 callback
#: （type=1）。这一组把三种混在一张键盘里，玩家点一遍就能看清区别：
#:
#:   ① ``人物``  callback/灰     —— 点它回传事件，出人物卡（现有全部按钮的样子）
#:   ② ``背包``  callback/蓝     —— 同上，但 ``style=1`` 是主色（primary）
#:   ③ ``爬塔``  command         —— 指令按钮：把「@我 爬塔」填进输入框（群聊）
#:   ④ ``跳转·文档`` link        —— 只打开网页，不产生任何游戏事件
#:   ⑤ ``自动爬塔·是`` callback 带参 —— data 是 ``doupo:cbtn:…``，点一下直接开自动爬塔
#:
#: 五行刚好用满平台上限（每行≤5 按钮、≤5 行）。
SAMPLE_BUTTONS: tuple[Btn, ...] = (
    Btn("人物", "doupo:panel", False, kind="callback"),
    Btn("背包", "doupo:bag", True, kind="callback"),
    Btn("爬塔", "爬塔", False, kind="command"),
    Btn("跳转·文档", "https://bot.q.qq.com/wiki/", False, kind="link"),
    Btn("自动爬塔·是", DOUPO_CUSTOM_PREFIX + "自动爬塔 是", False, kind="callback"),
)

#: 2026-11-25 晚：**纯文字回执**族 —— 回复本身是一段文本，卡片另有 render_* 负责
#: （或压根不该出图），这些 key 只用来给那段文本挂一组「下一步做什么」的按钮。
#:
#: 为什么必须单独列出来：守门要区分两种 card_key ——
#:   * 出图卡的 key（bag / shop / tower …）→ 必须有 ``render_<key>``，否则渲出假卡；
#:   * 回执卡的 key（fight_text / craft_text …）→ **本来就不出图**，逼它写渲染函数
#:     只会催生一堆永远不用的死代码。
#: 新增纯文字回执时，往这里补一行即可；漏补的话守门会红（不会静默放过）。
TEXT_CARD_KEYS = frozenset({
    "fight_text", "battle_text", "review_text",              # 战斗族回执
    "task_panel", "main_submit", "task_submit",              # 任务族回执
    "tower_text", "weekly_text",                             # 塔 / 周本回执
    "cult_text", "equip_text", "skill_text", "flame_text",   # 修炼 / 装备 / 斗技 / 异火
    "buy_text", "sell_text", "craft_text", "use_text", "shop_text",  # 买 / 卖 / 炼 / 服 / 商店
    "enter_text", "puppet_text", "duel_text",                # 入道 / 傀儡 / 切磋
    "setup_text",                                            # 按钮设置回执
    "tip_text",                                              # 「尚未开放」提示回执
})

#: 以上两族的并集：**没有 render_***、但可以当 keyboard_cmd 用。
NO_RENDER_CARD_KEYS = TEXT_CARD_KEYS | PROMPT_CARD_KEYS

#: 所有按钮 canonical 的并集 —— qq_interaction 据此识别「斗破按钮点击」。
#: 2026-11-26：并入隐藏样例口令「按钮样例」——它不挂任何游戏卡（所以不在
#: CARD_ACTIONS 里），但确是一颗真会被点的按钮（button_dispatch._PLAN 有登记）；
#: 不并进来的话，守门会把它判成「dispatch 混入非按钮口令」。
ALL_BUTTON_CMDS = (frozenset(c for acts in CARD_ACTIONS.values() for c in acts)
                   | {"按钮样例"})


#: 官方按钮 label 的显示上限（项目自定，超了会被客户端截断成看不懂的残字）。
LABEL_MAX = 10


def _fit_label(prefix: str, name: str, tail: str = "") -> str:
    """``prefix + name + tail`` 压进 :data:`LABEL_MAX`：超长只截 name，前缀后缀不丢。

    为什么截名字而不是截数量：玩家看按钮时**数量必须一眼可见**（「一次买够」是
    本功能的全部意义），名字在回执正文里本来就有一行完整版，截短还能认出来。
    """
    room = LABEL_MAX - len(prefix) - len(tail)
    if room < 2:
        return (prefix + name + tail)[:LABEL_MAX]
    return prefix + (name if len(name) <= room else name[:room]) + tail


def mat_buy_button(dan: str, mat: str, qty: int, furnace: int = 1) -> Btn:
    """一颗「一次买够某味药材」按钮（2026-11-26 用户要求）。

    ``data = doupo:cbtn:买 <药材> <数量> @<丹名> <炉数>``：前半段是玩家手打也认的
    真口令，``@`` 之后是本作的**上下文尾巴**（``craft_ctx.split_ctx`` 解析），
    买完据此重算剩余缺口并把「炼制此丹」挂回回执——点完这味，下一味就在手边。
    """
    q = max(1, int(qty))
    tail = "×%d" % q
    cmd = "%s买 %s %d @%s %d" % (DOUPO_CUSTOM_PREFIX, mat, q, dan, max(1, int(furnace)))
    return Btn(_fit_label("买", mat, tail), cmd, False)


def craft_mat_buttons(dan: str, pairs, furnace: int = 1) -> tuple[Btn, ...]:
    """按 ``((药材, 数量), ...)`` 派生「每味一颗买齐按钮 + 一颗炼制此丹」。

    ``pairs`` 的数量由调用方按语义算好：
      * 查看丹药卡 —— 单炉用量（玩家只是在看图鉴，没指定炉数）；
      * 缺料回执   —— **本次炉数 × 单炉用量 − 已有** 的缺口（见 craft_ctx）。
    炉数 >1 时炼制按钮带炉数（``炼制 <丹> 10``），买完点一下就是整批，不用重打。
    """
    n = max(1, int(furnace))
    acts: list[Btn] = [mat_buy_button(dan, m, q, n) for m, q in (pairs or ()) if int(q) > 0]
    if dan:
        cmd = DOUPO_CUSTOM_PREFIX + "炼制 " + dan + ("" if n == 1 else " %d" % n)
        acts.append(Btn(_fit_label("炼制", dan), cmd, False))
    return tuple(acts)


def _item_detail_buttons(name: str) -> tuple[Btn, ...]:
    """查看物品详情卡：丹药给「每味材料买齐 + 炼制此丹 + 炼药/背包」，非丹药给通用按钮。

    按钮 data 用 custom 前缀带参（``doupo:cbtn:炼制 <丹名>``），点击走
    ``dispatch_custom`` → ``commands.cmd_craft(arg)``，直接开炉这味丹，不用玩家
    再手打口令。零 nonebot 依赖：只读 config 的纯数据表。

    2026-11-26 用户原话：「查看丹药时，底部的按钮应该有全部材料的购买按钮，
    注意有的材料需要不止一个，必须要保证一次买够」——此前只挂第一味材料且不带
    数量（点一下买 1 份，缺 3 份就得点三次、还得自己记数），现在**每味一颗**、
    数量就是该丹单炉用量，一键买齐。
    """
    base = CARD_ACTIONS.get("item", ("服丹", "炼药", "宝库", "背包"))
    if not name:
        return tuple(Btn(c, c, False) for c in base)
    try:
        # 直接按文件加载 config（绕开 doupo/__init__.py 的 nonebot import），
        # 宿主纯标准库单测可跑、生产零副作用。config 顶部相对 import recipes_data，
        # 用假包 dpkg 承载即可（同 test_official_text_bridge 的桩法）。
        import importlib.util as _ilu
        import sys as _sys
        import types as _types
        from pathlib import Path as _Path

        _dir = _Path(__file__).resolve().parents[2] / "doupo"  # src/plugins/doupo
        _pkg = _types.ModuleType("_doupo_data")
        _pkg.__path__ = [str(_dir)]  # type: ignore[attr-defined]
        _sys.modules.setdefault("_doupo_data", _pkg)
        for _sub in ("recipes_data", "config"):
            _key = "_doupo_data." + _sub
            if _key not in _sys.modules:
                _sp = _ilu.spec_from_file_location(_key, _dir / (_sub + ".py"))
                if _sp is None or _sp.loader is None:
                    raise ImportError(_sub)
                _mod = _ilu.module_from_spec(_sp)
                _sys.modules[_key] = _mod
                _sp.loader.exec_module(_mod)
        _cfg = _sys.modules["_doupo_data.config"]
        rec = _cfg.RECIPE_BY_NAME.get(name) or {}
        need = rec.get("need") or {}
    except Exception:  # noqa: BLE001 — 派生失败退回通用按钮，绝不阻断出图
        return tuple(Btn(c, c, False) for c in base)
    if not need:
        # 非丹药（药材/天材地宝/傀儡料）：给通用「炼药/宝库/背包」，不加炼制按钮。
        return tuple(Btn(c, c, False) for c in base)
    # 每味材料一颗「买X×N」（N=单炉用量），再挂「炼制此丹」+「炼药」「背包」。
    # 药材最多 6 味 → 最多 9 颗；qq_keyboard.wrap_button_rows 自动切成多行（≤5 行）。
    acts: list[Btn] = list(craft_mat_buttons(name, tuple(need.items()), 1))
    acts.append(Btn("炼药", "炼药", False))
    acts.append(Btn("背包", "背包", False))
    return tuple(acts)


def buttons_for_card(card_key: str) -> tuple[Btn, ...]:
    """取某张卡的按钮；未知卡/空输入返回 ``()``（零按钮，不构造空 keyboard）。

    只在 ``card_key`` 精确命中时返回——绝不把斗破按钮塞进 game_tools 口令
    （那会污染档位族守门），也不对闲聊/斜杠命令给按钮。
    """
    k = (card_key or "").strip()
    # 2026-11-26 动态按钮优先（业务现算，如傀儡出战按名录逐具给一颗）：本回合
    # arm 了该 card_key 就先返回它，压过固定 CARD_ACTIONS 与该卡的玩家自定义按钮
    # ——「点一下就换这具」比任何固定按钮族都更具体，不能被通用族盖掉。
    dyn = (_dynamic_buttons.get() or {}).get(k)
    if dyn:
        return tuple(dyn)
    # 2026-11-25 晚：查看丹药卡动态按钮（用户「丹药就加炼制+买材料按钮」）。
    # card_key = "item:<丹名>" → 按该丹配方生成带参按钮；非丹药走普通 item。
    if k.startswith("item:"):
        return _item_detail_buttons(k[len("item:"):].strip())
    # 2026-11-25 自动爬塔确认卡：两个带参按钮（随机加成/无加成）。
    # 不经 CARD_ACTIONS 的零编造守门（data 是 custom 哨兵，非 canonical 口令）；
    # head「自动爬塔」在 button_dispatch._PLAN 登记，点击走 dispatch_custom 带参。
    if k == "auto_tower_prompt":
        return (
            Btn("随机加成", DOUPO_CUSTOM_PREFIX + "自动爬塔 是", False),
            Btn("无加成·掉落高", DOUPO_CUSTOM_PREFIX + "自动爬塔 否", False),
        )
    # 2026-11-26 按钮样例（隐藏口令「按钮样例」）：三种 action.type 全家桶。
    if k == "btn_sample":
        return SAMPLE_BUTTONS

    acts = CARD_ACTIONS.get(k)
    if not acts:
        return ()
    # 2026-11-18 玩家自定义按钮优先：本回合发送层 arm 了该卡的自定义按钮时，
    # 完全替换固定按钮（用户对每个按钮有增删改权）。菜单卡的 arm 由发送层禁止。
    custom = _custom_buttons.get().get(k)
    if custom:
        return tuple(custom)
    # 2026-11-25 晚：label 覆盖优先（如「继续战斗」cmd 仍是「战斗」）。
    pairs = CARD_ACTIONS_PAIRS.get(k)
    if pairs:
        return tuple(Btn(label, cmd, False) for label, cmd in pairs)
    return tuple(Btn(c, c, False) for c in acts)


def reply_card_key_for(inbound_text: str) -> str:
    """入站口令文本 → 纯文字回复该挂的 card_key（零编造：只认两张表）。

    取首个非空词；「上阵 1 3」「炼制 回灵丹」这类带参口令取首词。
    先查 ``REPLY_CARD_KEY``（主口令），查不到再经 ``REPLY_ALIAS`` 归到主口令上——
    同一张卡的各种说法（「打怪/历练」「包裹/行囊/背包」）不能一半有按钮一半没有。
    """
    s = str(inbound_text or "").strip()
    if not s:
        return ""
    head = s.split()[0]
    key = REPLY_CARD_KEY.get(head)
    if key:
        return key
    canon = REPLY_ALIAS.get(head)
    if canon:
        return REPLY_CARD_KEY.get(canon, "")
    return ""


def validate() -> None:
    """实现侧自检（守门测试另有独立基准，不信任本函数）：任一违例立即抛 AssertionError。"""
    real = set(real_commands())
    assert real, "MENU_GROUPS 派生为空（事实源退化）"
    # label 覆盖表：cmd 必须是真口令，label 长度合法，覆盖的 card_key 必须存在
    for card, pairs in CARD_ACTIONS_PAIRS.items():
        assert card in CARD_ACTIONS, "label 覆盖指向不存在的卡：%s" % card
        assert 1 <= len(pairs) <= 5, "卡 %s label 覆盖按钮数越界" % card
        for label, cmd in pairs:
            assert cmd in real, "卡 %s 覆盖 cmd 编造：%s" % (card, cmd)
            assert 0 < len(label) <= 10, "卡 %s label 越界：%s" % (card, label)
    # 入站映射表：值必须指向已知 card_key，键必须首词规范
    for head, ck in REPLY_CARD_KEY.items():
        assert ck in CARD_ACTIONS or ck in NO_RENDER_CARD_KEYS, \
            "入站口令 %s 指向未知卡 %s" % (head, ck)
        assert head == head.strip() and head, "入站口令键不规范：%r" % head
    # 别名表：只能指向**已有主口令**，且不许和主表重名（重名会让别名静默失效）
    for alias, canon in REPLY_ALIAS.items():
        assert canon in REPLY_CARD_KEY, \
            "别名 %s 指向不存在的主口令 %s" % (alias, canon)
        assert alias not in REPLY_CARD_KEY, \
            "别名 %s 与主表重名（主表优先，别名会被无视）" % alias
        assert alias == alias.strip() and alias, "别名不规范：%r" % alias
    # 回执族自洽：TEXT_CARD_KEYS 里每个 key 都得真在 CARD_ACTIONS 里（否则挂了空按钮），
    # 且不许和提示卡族重叠（语义不同：一个发文本、一个发提示卡）。
    assert TEXT_CARD_KEYS <= set(CARD_ACTIONS), \
        "TEXT_CARD_KEYS 有 CARD_ACTIONS 里不存在的卡：%s" % sorted(
            TEXT_CARD_KEYS - set(CARD_ACTIONS))
    assert not (TEXT_CARD_KEYS & PROMPT_CARD_KEYS), "回执族与提示卡族重叠"
    combos: list[tuple[str, ...]] = []
    for card, acts in CARD_ACTIONS.items():
        assert 1 <= len(acts) <= 5, "卡 %s 按钮数越界：%d" % (card, len(acts))
        assert len(set(acts)) == len(acts), "卡 %s 按钮重复：%s" % (card, acts)
        combos.append(tuple(sorted(acts)))
        self_cmd = CARD_SELF.get(card)
        for c in acts:
            assert c in real, "卡 %s 按钮编造（非真实口令）：%s" % (card, c)
            assert c not in MENU_CMDS, "卡 %s 含主菜单按钮：%s" % (card, c)
            assert c != self_cmd, "卡 %s 含当前卡自己按钮：%s" % (card, c)
            assert 0 < len(c) <= 10, "卡 %s label 越界：%s" % (card, c)
    # 跨卡组合两两不同（杜绝「每张卡都一样」的全局导航）
    assert len(set(combos)) == len(combos), "存在按钮组合完全相同的两张卡（功能重叠）"
