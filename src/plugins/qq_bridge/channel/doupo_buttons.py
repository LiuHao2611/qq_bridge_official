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
from dataclasses import dataclass
from pathlib import Path

_COMMANDS_PY = Path(__file__).resolve().parents[2] / "doupo" / "commands.py"

#: 主菜单 / 切换口口令：任何卡片按钮都不得出现（用户「去掉主菜单按钮」）。
MENU_CMDS = frozenset({"斗破菜单", "斗破口令", "修仙菜单", "帮助"})


@dataclass(frozen=True)
class Btn:
    """与 qq_keyboard.Btn 同构（label / cmd / primary），供 make_keyboard 直接消费。"""

    label: str
    cmd: str
    primary: bool = False


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
    "bag": ("服丹", "炼药", "宝库"),
    "rank": ("战斗", "修炼", "人物"),
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
}

#: 所有按钮 canonical 的并集 —— qq_interaction 据此识别「斗破按钮点击」。
ALL_BUTTON_CMDS = frozenset(c for acts in CARD_ACTIONS.values() for c in acts)


def buttons_for_card(card_key: str) -> tuple[Btn, ...]:
    """取某张卡的按钮；未知卡/空输入返回 ``()``（零按钮，不构造空 keyboard）。

    只在 ``card_key`` 精确命中时返回——绝不把斗破按钮塞进 game_tools 口令
    （那会污染档位族守门），也不对闲聊/斜杠命令给按钮。
    """
    acts = CARD_ACTIONS.get((card_key or "").strip())
    if not acts:
        return ()
    return tuple(Btn(c, c, False) for c in acts)


def validate() -> None:
    """实现侧自检（守门测试另有独立基准，不信任本函数）：任一违例立即抛 AssertionError。"""
    real = set(real_commands())
    assert real, "MENU_GROUPS 派生为空（事实源退化）"
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
