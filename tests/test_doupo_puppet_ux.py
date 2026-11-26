# -*- coding: utf-8 -*-
"""守门（2026-11-26）：傀儡图鉴卡 + 出战切换 + 三卡按钮差异化。

用户原话：「优化傀儡模块的各个卡片对应按钮，别都搞一样，还有加一个傀儡图鉴，
展示所有傀儡的配方，如果玩家已有对应材料，材料要高亮而且显示数量。还有上阵
傀儡没有提示，能做成按钮就做成按钮，目的都是方便操作。」

守四件事：
  1. build_codex_data 输出全部 18 条配方、按档分组、已备材料进 note 且带持有/所需数；
  2. cmd_puppet_deploy 真写 puppet_store.active_id（战斗同源），序号/名字解析正确；
  3. 三张傀儡出图卡按钮组合两两不同、不再都挂「战斗」（差异化）；
  4. 新口令「傀儡图鉴」「傀儡出战」贯穿 MENU_GROUPS / _PLAN / REPLY_CARD_KEY。
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLUGINS = os.path.join(ROOT, "src", "plugins")
for _p in (ROOT, PLUGINS):
    if _p not in sys.path:
        sys.path.insert(0, _p)
os.environ.setdefault("DOUPO_CHANCE_RATE", "0")


def _stub():
    nb = types.ModuleType("nonebot")
    nb.get_driver = lambda: types.SimpleNamespace(on_startup=lambda f: f)

    def on_x(*a, **k):
        m = types.SimpleNamespace()
        m.handle = lambda *a, **k: (lambda f: f)
        m.finish = lambda *a, **k: None
        m.on_command = lambda *a, **k: m
        return m
    nb.on_regex = nb.on_command = nb.on_notice = nb.on_message = on_x
    nb.require = lambda *a, **k: (lambda f: f)
    nb.logger = types.SimpleNamespace(error=lambda *a, **k: None,
                                      warning=lambda *a, **k: None,
                                      info=lambda *a, **k: None)
    sys.modules["nonebot"] = nb
    for n in ("nonebot.adapters", "nonebot.adapters.onebot"):
        sys.modules[n] = types.ModuleType(n)
    v = types.ModuleType("nonebot.adapters.onebot.v11")
    v.MessageEvent = v.MessageSegment = v.Message = v.Bot = object
    sys.modules["nonebot.adapters.onebot.v11"] = v


_stub()

from doupo import card  # noqa: E402
from doupo import commands as cmd  # noqa: E402
from doupo import config as cfg  # noqa: E402
from doupo import puppet_core as pc  # noqa: E402
from doupo import puppet_store as psm  # noqa: E402

CH = os.path.join(ROOT, "src", "plugins", "qq_bridge", "channel")
if CH not in sys.path:
    sys.path.insert(0, CH)
import doupo_buttons as db  # noqa: E402


# ---------------------------------------------------------------------------
# 1 图鉴数据
# ---------------------------------------------------------------------------
def test_codex_covers_all_recipes():
    data = card.build_codex_data({}, None)
    recipe_rows = [r for r in data["rows"] if not r.get("head")]
    assert len(recipe_rows) == len(cfg.PUPPET_RECIPES) == 18, (
        len(recipe_rows), len(cfg.PUPPET_RECIPES))
    names = {r["title"] for r in recipe_rows}
    assert names == {r["name"] for r in cfg.PUPPET_RECIPES}, "图鉴漏配方"


def test_codex_highlights_owned_materials_with_counts():
    # 造一份「铜尸傀」齐料：四维材料各 1
    rec = cfg.PUPPET_RECIPE_BY_NAME["铜尸傀"]
    need = pc.recipe_materials(rec)
    mats = {n: q for n, q in need.items()}
    data = card.build_codex_data(mats, None)
    row = next(r for r in data["rows"] if r.get("title") == "铜尸傀")
    assert row["accent"] is True, "齐料配方应高亮"
    assert "已备：" in row["note"], row
    for n, q in need.items():
        assert "%s %d/%d" % (n, q, q) in row["note"], (n, row["note"])
    # 缺料的配方不该高亮
    other = next(r for r in data["rows"]
                 if r.get("title") and r["title"] != "铜尸傀" and not r.get("head"))
    assert other["accent"] is False, other


def test_codex_count_text_is_honest():
    """持有数量必须是 mats 真值，不是写死满格。"""
    rec = cfg.PUPPET_RECIPE_BY_NAME["铜尸傀"]
    need = pc.recipe_materials(rec)
    first = next(iter(need))
    mats = {first: 0}  # 一味都没有
    data = card.build_codex_data(mats, None)
    row = next(r for r in data["rows"] if r.get("title") == "铜尸傀")
    assert row["accent"] is False
    assert first not in row["note"], "缺料不该进已备高亮"


# ---------------------------------------------------------------------------
# 2 出战切换真写列
# ---------------------------------------------------------------------------
def _p():
    from doupo import core
    pl = core.create_player("deploy-test", "出战", "g")
    pl["flames"] = {"紫火": 1}
    return pl


def _three_puppets(db):
    """塞三具傀儡，返回 id 列表。"""
    ids = []
    for name in ("铜尸傀", "铁卫傀", "重击傀"):
        ids.append(asyncio.run(db.add_puppet("10001", {
            "name": name, "tier": 0, "tier_name": "人级傀儡",
            "body_name": "强者躯体", "body_realm": "斗师", "body_star": 0,
            "core_tier": 7, "core_attr": "火", "soul_source": "无主灵魂",
            "metal_name": "精铁", "purity": 10, "bonded": True,
        })))
    return ids


def test_deploy_switch_writes_active_id_real_column():
    with tempfile.TemporaryDirectory() as tmp:
        db = psm.PuppetStore(os.path.join(tmp, "p.sqlite"))
        ids = _three_puppets(db)
        asyncio.run(db.set_active("10001", ids[0]))
        p = _p()
        roster = asyncio.run(db.list_puppets("10001"))
        elig = asyncio.run(db.eligibility("10001"))
        # 切到第 2 具
        msg = asyncio.run(cmd._cmd_puppet_deploy(None, "10001", p, roster, elig, db, "出战 2"))
        assert "切换" in msg and "铁卫傀" in msg, msg
        assert asyncio.run(db.eligibility("10001"))["active_id"] == ids[1], "active_id 没真写"
        # 战斗读的就是这具（同源）
        active = asyncio.run(db.active_puppet("10001"))
        assert active["name"] == "铁卫傀", active


def test_deploy_lists_without_arg_and_rejects_bad_index():
    with tempfile.TemporaryDirectory() as tmp:
        db = psm.PuppetStore(os.path.join(tmp, "p.sqlite"))
        ids = _three_puppets(db)
        p = _p()
        roster = asyncio.run(db.list_puppets("10001"))
        elig = asyncio.run(db.eligibility("10001"))
        listing = asyncio.run(cmd._cmd_puppet_deploy(None, "10001", p, roster, elig, db, "出战"))
        assert "1." in listing and "2." in listing and "3." in listing, listing
        assert "铜尸傀" in listing, listing
        bad = asyncio.run(cmd._cmd_puppet_deploy(None, "10001", p, roster, elig, db, "出战 99"))
        assert "超出范围" in bad, bad
        assert asyncio.run(db.eligibility("10001"))["active_id"] == ids[0]  # 没被误改（首具自动出战）


def test_deploy_by_name_keyword():
    with tempfile.TemporaryDirectory() as tmp:
        db = psm.PuppetStore(os.path.join(tmp, "p.sqlite"))
        ids = _three_puppets(db)
        p = _p()
        roster = asyncio.run(db.list_puppets("10001"))
        elig = asyncio.run(db.eligibility("10001"))
        msg = asyncio.run(cmd._cmd_puppet_deploy(None, "10001", p, roster, elig, db, "出战 重击"))
        assert "重击傀" in msg and "切换" in msg, msg
        assert asyncio.run(db.eligibility("10001"))["active_id"] == ids[2]


def test_puppet_deploy_entry_uses_real_store():
    """cmd_puppet_deploy 按钮入口：无参返回清单而非名录卡（不空）。"""
    with tempfile.TemporaryDirectory() as tmp:
        db = psm.PuppetStore(os.path.join(tmp, "p.sqlite"))
        _three_puppets(db)

        class _Store:
            async def get_player(self, qq):
                return _p()

        msg = asyncio.run(cmd.cmd_puppet_deploy(_Store(), "10001", db, ""))
        assert "傀儡出战" in msg and "1." in msg, msg


# ---------------------------------------------------------------------------
# 3 按钮差异化 + 新口令接线
# ---------------------------------------------------------------------------
def test_puppet_cards_buttons_are_differentiated():
    acts = db.CARD_ACTIONS
    trio = (acts["puppet"], acts["ruin"], acts["craft"], acts["codex"])
    assert len({tuple(sorted(x)) for x in trio}) == 4, trio
    # 不再三张都挂「战斗」
    with_fight = sum(1 for x in trio if "战斗" in x)
    assert with_fight <= 1, "傀儡卡不该都挂战斗：%s" % (trio,)
    # 每张都含傀儡功能族口令
    family = {"炼制傀儡", "傀儡", "傀儡遗迹", "傀儡图鉴", "傀儡出战"}
    for x in trio:
        assert set(x) & family, x


def test_new_commands_wired_through_all_layers():
    plan_path = os.path.join(ROOT, "src", "plugins", "doupo", "button_dispatch.py")
    src = open(plan_path, encoding="utf-8").read()
    for c in ("傀儡图鉴", "傀儡出战"):
        assert ('"%s"' % c) in src, "dispatch 缺 %s" % c
        assert db.reply_card_key_for(c), "reply_card_key_for 缺 %s" % c
        assert c in db.real_commands(), "MENU_GROUPS 缺 %s" % c
    assert "codex" in db.CARD_ACTIONS, "图鉴卡未配按钮"
    assert db.CARD_SELF.get("codex") == "傀儡图鉴"


def test_codex_render_entry_exists():
    assert hasattr(card, "build_codex_data")
    assert hasattr(card, "render_codex")
    assert hasattr(card, "render_codex_for"), "按钮点击需要 render_codex_for"


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-q"]))
