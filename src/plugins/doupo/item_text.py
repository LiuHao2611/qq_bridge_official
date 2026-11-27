# -*- coding: utf-8 -*-
"""物品名解析：输入归一化 + 卖出目标定位（纯函数，不碰存档）。

为什么单独成模块
----------------
``commands.py`` 已 206479 B，顶破 pre-commit 的 200KB 红线（204800 B）。
这两段是纯字符串逻辑、零副作用，搬出来最安全（``bagview.py`` 是同样的先例）。

归一化为什么要统一口径
----------------------
玩家手打「聚气散.一纹」「聚气散 ・ 一纹」「聚气散·一纹」都是同一件东西。
``norm_item_text`` 把各种「点」统一成「·」、去掉空格/横线/括号、压掉重复的 ·。
**服丹 / 查看 / 卖出必须共用这一份**，否则同一个名字在一处认得、另一处认不得。

为什么卖出要单独一个定位函数
----------------------------
背包**显示名**剥掉了纹数后缀（用户 2026-11-25「丹药名称不要变」），玩家看到
「聚气散」时那一格可能其实是「聚气散·三纹」。买东西点错只是花错钱，卖东西
点错**不可回滚**——所以只写基础名、手上又只有带纹版时明确拒绝，绝不替玩家
挑一枚卖（三纹很稀有，误卖就是坑）。
"""
from __future__ import annotations

import re

from . import bagview
from . import config as cfg

#: 数量写法：丹名 + 可选分隔 + 阿拉伯数字 + 可选量词。
#: 与 ``commands._USE_COUNT_RE`` 同一条规则，搬过来供卖出解析复用。
COUNT_RE = re.compile(r"^(.*?)[\s×xX*＊]*(?:×|x|X|\*)?\s*(\d+)\s*(?:枚|颗|粒|个|份)?$")


def norm_item_text(s: str) -> str:
    """物品名归一化：各种「点」→ ·，去空格/横线/括号，压掉重复的 ·。"""
    t = str(s or "").strip()
    for ch in ("．", ".", "・", "•", "‧", "｡", "*", "＊"):
        t = t.replace(ch, "·")
    for ch in (" ", "\t", "-", "—", "_", "(", ")", "（", "）",
               "【", "】", "《", "》", "、", "，", ","):
        t = t.replace(ch, "")
    while "··" in t:
        t = t.replace("··", "·")
    return t.strip("·")


def resolve_sell_target(p, raw: str) -> tuple[str, str]:
    """把玩家输入对到**背包里真实存在**的那一格；返回 ``(真实名, 错误文案)``。

    三条匹配顺序不可换：
      ① 归一化后精确命中——写全名（含「聚气散·一纹」）走这里；
      ② 只写基础名、而背包里那格是带纹的 → **拒绝**并报出全名；
      ③ 包含匹配只在**非带纹**里找，避免「回灵丹」顺手卖掉「回灵丹·三纹」。
    """
    bag = [name for name, _n in bagview.sorted_items(p)]
    if not bag:
        return "", "🎒 背包空空，没东西可卖。"
    want = norm_item_text(raw)
    if not want:
        return "", "用法：卖 血灵草 5　（发「背包」看货）"

    # ① 精确命中
    for name in bag:
        if norm_item_text(name) == want:
            return name, ""

    # ② 只有带纹版 → 拒绝，报出全名让玩家自己决定卖哪一枚
    tier_hits = [x for x in bag
                 if cfg.is_danwen_name(x) and cfg.base_of_danwen(x) == want]
    if tier_hits:
        shown = "、".join(sorted(tier_hits, key=len))
        return "", (f"【{want}】你手上只有带纹的：{shown}。\n"
                    f"　带纹丹比普丹值钱，请写全名再卖 —— 卖错了没法回滚。")

    # ③ 包含匹配（排除带纹，防止顺手卖掉稀有纹丹）
    plain = [x for x in bag if want in norm_item_text(x) and not cfg.is_danwen_name(x)]
    if len(plain) == 1:
        return plain[0], ""
    if len(plain) > 1:
        return "", "「%s」对上了好几样：%s。请写全名。" % (raw, "、".join(plain[:5]))

    return "", f"🎒 背包里没有【{raw}】。发「背包」看货。"
