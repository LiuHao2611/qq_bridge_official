# -*- coding: utf-8 -*-
"""守门（2026-11-26）：NPC 插话按战况档精选，不再生硬错配。

用户原话：「战斗播报里加的人物台词太生硬了，根本就不符合实时战斗情况，
重新调整一遍」。

改造：给台词按战况档（lose/narrow/crush）配贴合的逐字原文（NPC_TIER_FIT），
npc_line 按 tier 选；移除云棱「我是云岚宗的大长老」这种塞进战斗就违和的自我介绍。

守四件事：
  1. 药老三档（惨胜/越级/败局）出不同台词，各自贴合战况；
  2. 云棱自我介绍式违和句已移除，换成当场放话原文；
  3. 所有角色台词仍是逐字原文（有出处、无禁词、无引号、无「原著」等元信息）；
  4. 章白名单 / 险战才出 / 不与点睛重复 / 字数预算 全部不回退。
"""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLUGINS = os.path.join(ROOT, "src", "plugins")
for _p in (ROOT, PLUGINS):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import nonebot  # noqa: E402

if not getattr(nonebot, "_already_inited", False):
    try:
        nonebot.init()
    except Exception:  # noqa: BLE001 - 已被其它测试文件初始化过
        pass

from doupo import config as cfg  # noqa: E402
from doupo import doupo_text as txt  # noqa: E402

BAN = ("原著", "第", "章", "出处", "「", "」", "“", "”", "语料", "考据")


# ---------------------------------------------------------------------------
# 1 按战况档出不同台词
# ---------------------------------------------------------------------------
def test_yaolao_three_tiers_give_distinct_lines():
    lose = txt.npc_line(1, 0, "lose")
    narrow = txt.npc_line(1, 0, "narrow")
    crush = txt.npc_line(1, 0, "crush")
    for line in (lose, narrow, crush):
        assert line.startswith("🗣 药老："), line
    assert len({lose, narrow, crush}) == 3, "三档台词没分开：%s" % (lose, narrow, crush)


def test_tier_lines_match_situation():
    """败局先肯定那一招（留鼓励）、惨胜淡然宽慰、越级护短得意——语义贴战况。"""
    lose = txt.npc_line(1, 0, "lose")
    narrow = txt.npc_line(1, 0, "narrow")
    crush = txt.npc_line(1, 0, "crush")
    # 败局：药老先夸「不错」，给败局玩家一点正反馈
    assert "不错" in lose, lose
    # 惨胜：讲「斗技有什么了不起」的淡然宽慰
    assert "斗技" in narrow, narrow
    # 越级：护短式「功法独特」的得意
    assert "独特" in crush, crush
    assert len({lose, narrow, crush}) == 3


def test_tier_fit_is_deterministic_for_same_tier():
    a = txt.npc_line(1, 0, "narrow")
    b = txt.npc_line(1, 0, "narrow")
    assert a == b


# ---------------------------------------------------------------------------
# 2 违和句移除
# ---------------------------------------------------------------------------
def test_yunleng_self_intro_removed():
    for _text, _src in txt.NPC_SPEAKERS["云棱"]:
        assert "我是云岚宗的大长老" not in _text, _text
        assert "云棱。" != _text, _text
    line = txt.npc_line(8, 0, "lose")
    assert "得罪我云岚宗" in line, "云棱该说当场放话，不是自我介绍：%s" % line


# ---------------------------------------------------------------------------
# 3 台词仍是逐字原文（不新编）
# ---------------------------------------------------------------------------
def test_all_speaker_lines_are_verified_originals():
    for who, pool in txt.NPC_SPEAKERS.items():
        assert who.strip() and pool
        for item in pool:
            assert len(item) == 2, "台词条目必须是 (台词, 出处)：%s" % (item,)
            text, source = item
            assert text.strip() and source.strip(), (who, item)
            assert "章" in source, "出处没写章回：%s" % (source,)
            for w in BAN:
                assert w not in text, "角色台词混进元信息/引号：%s（%s）" % (text, w)


def test_tier_fit_indices_in_range():
    for who, fit in txt.NPC_TIER_FIT.items():
        assert who in txt.NPC_SPEAKERS, who
        n = len(txt.NPC_SPEAKERS[who])
        for tier, idx in fit.items():
            assert 0 <= idx < n, "%s/%s 索引越界：%d（池 %d）" % (who, tier, idx, n)


# ---------------------------------------------------------------------------
# 4 硬约束不回退
# ---------------------------------------------------------------------------
def test_no_line_in_easy_fights_and_no_duplicate_with_quote():
    for tier in ("sweep", "great", "", "unknown"):
        assert txt.npc_line(1, 3, tier) == "", "顺风档 %r 也插话了" % tier
    for tier in ("narrow", "crush", "lose"):
        assert txt.npc_line(1, 3, tier).startswith("🗣 药老："), tier
    assert txt.npc_line(99, 3, "lose") == ""
    for ch, who_list in txt.CHAPTER_NPC.items():
        quotes = {p[0] for p in txt.CHAPTER_QUOTE.get(ch, ())}
        for who in who_list:
            for text, _src in txt.NPC_SPEAKERS[who]:
                assert text not in quotes, "插话与点睛撞句：%s" % text


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-q"]))
