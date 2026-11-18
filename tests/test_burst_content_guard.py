# -*- coding: utf-8 -*-
"""连发拆条的「内容不许丢」守门（2026-11-18）。

背景（真机）：AI 模式回答「斗破源码细节」时答案天然是长文本（几十到几百字，
带数字与 `文件:行号`）。连发模块 ``process_llm_response`` 是 MaiMBot 原版搬运，
它有两处**拿内容换防刷屏**的守卫：

  * 文本 > 300 字 → 直接返回 ``['懒得说']``；
  * 拆出来的句子数 > AI_BURST_MAX_PARTS → 返回 ``['本喵不知道哦']``。

在原版（闲聊搭子）里这叫「摆烂」，在本机就是**把模型刚查出来的答案连数字一起
扔了**——用户问「37 层爆装备概率是多少」，可能收到一句「懒得说」。这违反项目
铁律「禁止降级 / 禁止兜底」，也违反用户反复强调的「不许把答案降级成别的」。

现在的口径：**长度只决定拆不拆条，不决定说不说**。本测试锁死三件事：
  1. 超长文本原样返回（一个字不少，单条气泡）；
  2. 拆条数超限时也原样返回，不许换成「本喵不知道哦」；
  3. 正常短句仍按原版拆条（别把拟人连发一起改没了）。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _tool_stack import REPO, load_ai_mode  # noqa: E402

ai_mode = load_ai_mode("_burst_guard")

#: 拆条器会按概率吃掉标点（原版行为），比对内容时先剔掉标点与空白
_PUNCT = str.maketrans("", "", "，,。.？！!?… \n")


def _plain(text: str) -> str:
    return text.translate(_PUNCT)


#: 真机答案的样子：数字 + 百分号 + 文件行号 + 一串句子（>300 字才测得到守卫）
LONG_ANSWER = (
    "爬塔第 37 层的爆装备概率是 3.75%，代码在 src/plugins/doupo/core.py:412 "
    "的 drop_rate 里算的。基础值按层数线性衰减，每层减 0.05 个百分点。"
    "另外还要乘一个境界加成系数，链体境是 1.0，筑基境是 1.2，斗王境是 1.45。"
    "宝箱和装备是两次独立判定，宝箱先判，装备后判，互不影响。"
    "所以你看到「出宝箱没出装备」是正常的，不是 bug，别当成概率算错。"
    "如果带了寻宝类异火，还会再多一次 0.5 倍的加成判定，但那次只加装备不加宝箱。"
    "最后还有一层保底：连续 12 次没出装备，第 13 次必定给一件，保底计数器存在"
    "存档的 tower_no_drop 字段里。"
)


def test_long_answer_is_never_replaced_by_lazy_line():
    assert len(LONG_ANSWER) > 300, "样本必须真的超过 300 字，否则测不到守卫"
    parts = ai_mode.process_llm_response(LONG_ANSWER)
    assert parts == [LONG_ANSWER], "超长答案不许被换成『懒得说』之类"
    assert "懒得说" not in "".join(parts)


def test_too_many_parts_keeps_full_text():
    # 造 20 个逗号短句（远超 AI_BURST_MAX_PARTS 默认 4），长度仍 < 300
    # 逗号处按 split_strength=0.9 拆条，拆出 ≤4 片的概率可忽略 → 必然撞守卫
    text = "，".join(["一二三四五"] * 20) + "。"
    assert len(text) <= 300
    parts = ai_mode.process_llm_response(text, typo=False)
    assert len(parts) == 1, "拆太多时应当整段一条发，而不是换句摆烂话"
    assert "不知道" not in parts[0]
    assert _plain(parts[0]) == _plain(text)  # 字一个不少


def test_short_chat_passes_through_unchanged():
    """短句仍按原版处理（拟人连发不能被这次改动误伤）。"""
    text = "今天天气不错，要不要出去走走？"
    parts = ai_mode.process_llm_response(text, typo=False)
    assert parts, "短句不能返回空"
    assert _plain(text) in _plain("".join(parts))


def test_empty_text_returns_empty_list():
    assert ai_mode.process_llm_response("") == []
    assert ai_mode.process_llm_response(None) == []


@pytest.mark.parametrize("marker", ["懒得说", "本喵不知道哦"])
def test_lazy_guard_not_in_code(marker):
    """守卫的**返回语句**不许回来（注释里为解释原因提到这两个词是允许的）。"""
    src = (REPO / "src/plugins/qq_bridge/ai_mode.py").read_text(encoding="utf-8")
    for quote in ("'", '"'):
        assert ("return [%s%s%s]" % (quote, marker, quote)) not in src, (
            "别把 MaiMBot 的摆烂守卫改回来（会丢答案）")
