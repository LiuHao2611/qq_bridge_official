# -*- coding: utf-8 -*-
"""「斗技」合并卡 vs 「宗门斗技」本宗池 展示口径验收（2026-11-24）。

用户口径（第二次纠正）：
  * 原「斗技」= 已上阵、「斗技 全部」= 已学习；现在**合并为一张卡**；
  * 去掉「斗技 全部」子命令——「斗技」一张卡同时给出已上阵 + 已领悟；
  * 「宗门斗技」只单纯看当前宗门斗技（本宗池 + 上阵编号）。

守的行为：
  1. 合并卡有两个区块：已上阵（四槽，state=上阵）+ 已领悟（其余已学，state=已领悟）；
  2. 转宗保留的他宗技出现在已领悟区，并带〔他宗〕标记；
  3. 本宗尚未领悟的技**不**出现在合并卡（那是「宗门斗技」的职责）；
  4. 「宗门斗技」列本宗池（含未解锁），且仍带「上阵 1 3 5 7」的序号口径。

运行：
    PYTHONPATH="src/plugins;tmp;." pytest -q -p _pytest_stub_plugin src/plugins/doupo/test_skill_display.py
"""
from __future__ import annotations

import asyncio

from doupo import card, commands, core, skill_book as book


class MemStore:
    def __init__(self, p):
        self.p = p

    async def get_player(self, qq):
        return self.p

    async def put_player(self, p):
        self.p = p


def _cross_faction_player():
    p = core.create_player("95001", "展示探针", "g1")
    p["lv"] = 45
    p["gold"] = 500000
    # 塞一门确实属于别宗的已学技（转宗保留场景）
    extra = [n for n in ("黄泉掌", "碎石掌", "九幽魂手")
             if n in set(book.all_names())]
    assert extra, "探针需至少一个真实跨宗技"
    p["learned"] = list(dict.fromkeys((p.get("learned") or []) + extra))
    core.set_loadout(p, list(p["learned"])[:4])
    return p, extra


def test_merged_card_has_on_and_learned_groups():
    """合并卡必须同时有「已上阵」和「已领悟」两个区块。"""
    p, _ = _cross_faction_player()
    d = card.build_learned_data(p)
    labels = [str(g.get("label") or g.get("tier") or "") for g in d["groups"]]
    assert "已上阵" in labels, "合并卡缺「已上阵」区块：%s" % labels
    assert any(lb in ("普通", "高阶", "特殊") for lb in labels), \
        "合并卡缺按阶位分组的「已领悟」区块：%s" % labels
    # 已上阵区行数 == 四槽数，且状态全为「上阵」
    on = next(g for g in d["groups"] if g.get("label") == "已上阵")
    lo = core.loadout_entries(p)
    assert len(on["rows"]) == len(lo), "已上阵区行数与四槽不一致"
    assert all(r["state"] == "上阵" and r["state_kind"] == "on" for r in on["rows"])
    # 已领悟区状态必须是「已领悟」
    others = [r for g in d["groups"] if g.get("label") != "已上阵" for r in g["rows"]]
    assert others, "探针应有已上阵之外的已学技"
    assert all(r["state"] == "已领悟" and r["state_kind"] == "learn" for r in others)


def test_merged_card_shows_cross_faction_learned():
    """转宗保留的他宗技出现在「已领悟」区，且带〔他宗〕标记。"""
    p, extra = _cross_faction_player()
    d = card.build_learned_data(p)
    lo_names = {e["name"] for e in core.loadout_entries(p)}
    learned_rows = {r["name"]: r for g in d["groups"] if g.get("label") != "已上阵"
                    for r in g["rows"]}
    cross = [n for n in extra if n not in lo_names and n in learned_rows]
    assert cross, "他宗已学技应出现在已领悟区：extra=%s rows=%s" % (
        extra, sorted(learned_rows))
    for n in cross:
        assert "〔他宗〕" in learned_rows[n]["meta"], \
            "他宗技 %s 没打〔他宗〕标记：%s" % (n, learned_rows[n]["meta"])


def test_merged_card_excludes_unlearned_faction_skills():
    """本宗尚未领悟的技不进合并卡（那是「宗门斗技」的职责）。"""
    p, _ = _cross_faction_player()
    learned_names = set(core.sk_mod.learned_names(p))
    unlearned = [e["name"] for e in core.skill_pool(p)
                 if e["name"] not in learned_names and e.get("origin") != "异火"]
    if not unlearned:
        return  # 该宗门全学完了，本断言无意义
    d = card.build_learned_data(p)
    shown = {r["name"] for g in d["groups"] for r in g["rows"]}
    leaked = [n for n in unlearned if n in shown]
    assert not leaked, "合并卡漏出了未领悟的本宗技：%s" % leaked


def test_merged_card_cmd_text_has_both_sections():
    """纯文本兜底同样分「已上阵」「已领悟」，并提示上阵编号看「宗门斗技」。"""
    p, _ = _cross_faction_player()
    out = asyncio.run(commands.cmd_skills(MemStore(p), "95001"))
    assert "已上阵" in out and "已领悟" in out, out[:200]
    assert "宗门斗技" in out, "缺上阵编号指引"
    assert "斗技 全部" not in out, "「斗技 全部」子命令已取消，文案不该再出现"
    for e in core.loadout_entries(p):
        assert e["name"] in out, "已上阵技 %s 没列出来" % e["name"]


def test_faction_cmd_is_pure_faction_pool():
    """「宗门斗技」列本宗池（含未解锁），并保留上阵序号口径。"""
    p, _ = _cross_faction_player()
    out = asyncio.run(commands.cmd_faction_skills(MemStore(p), "95001"))
    assert "本门斗技" in out, "「宗门斗技」标题应是本门口径"
    pool = core.skill_pool(p)
    assert pool, "探针宗门应有可学技"
    assert any(e["name"] in out for e in pool), "「宗门斗技」没列出本宗技"
    assert "上阵 1 3 5 7" in out, "缺上阵序号指引"
    # 本宗池序号仍由 roster_no_map 提供（上阵 1 3 5 7 的唯一编号源）
    d = card.build_roster_data(p)
    assert [r["no"] for r in d["rows"]] == list(range(1, len(d["rows"]) + 1)), \
        "宗门斗技卡序号必须连续，供「上阵 1 3 5 7」使用"
