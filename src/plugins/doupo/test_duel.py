# -*- coding: utf-8 -*-
"""无惩罚切磋验收（零依赖、exit-code 可验证，2026-11-16）。

守的是用户拍板的四条硬规则：
  1. **无惩罚**：结算只动 duel_wins/duel_losses，hp/mp/gold/exp/bag/wins/losses 一律不动；
  2. **仅本群**：跨群不能冒领挑战，私聊直接拒绝；
  3. **两段式**：发起 → 对方「应战」才打；「拒战」取消；超时自动作废；
  4. **属性强关联**：战报复用 _battle_line 内联属性归因，不退化成干巴巴的数字。

prove-it 反例注入（验收时执行，恢复后必须全绿）：
  * 把 commands.cmd_duel_accept 里 `await store.put_player(rival)` 之前插入
    `rival["gold"] -= 1` → 「切磋零惩罚·金币」必须 FAIL；
  * 把 core.duel 的 `res = core.duel(rival, p)` 改成 `res = core.fight(rival, ...)` →
    「应战后 hp 不变」必须 FAIL。

运行：python src/plugins/doupo/test_duel.py
"""
from __future__ import annotations

import asyncio
import os
import sys
import types

PLUGINS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO_ROOT = os.path.dirname(os.path.dirname(PLUGINS_DIR))
for _p in (REPO_ROOT, PLUGINS_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)
os.environ["DOUPO_CHANCE_RATE"] = "0"


def _stub_nonebot():
    nb = types.ModuleType("nonebot")

    def on_regex(*a, **k):
        m = types.SimpleNamespace()
        m.handle = lambda *a, **k: (lambda f: f)
        m.finish = lambda *a, **k: None
        return m

    nb.on_regex = on_regex
    nb.get_driver = lambda: types.SimpleNamespace(on_startup=lambda f: f)
    sys.modules["nonebot"] = nb
    for name in ("nonebot.adapters", "nonebot.adapters.onebot",
                 "nonebot.adapters.onebot.v11"):
        sys.modules[name] = types.ModuleType(name)
    v11 = sys.modules["nonebot.adapters.onebot.v11"]
    v11.MessageEvent = object
    v11.MessageSegment = object
    v11.Message = object


_stub_nonebot()

from doupo import commands, config as cfg, core, duel_challenge as dc  # noqa: E402

_PASSED = 0
_FAILED = 0


def check(name, cond, detail=""):
    global _PASSED, _FAILED
    if cond:
        _PASSED += 1
        print("PASS  " + name)
    else:
        _FAILED += 1
        print("FAIL  %s  ->  %s" % (name, detail))


class MemStore:
    def __init__(self, players):
        self.players = {str(k): v for k, v in players.items()}

    async def get_player(self, qq):
        return self.players.get(str(qq))

    async def put_player(self, p):
        self.players[str(p["qq"])] = p


def _p(qq, name, group="g1", lv=30):
    p = core.create_player(str(qq), name, group)
    p["lv"] = lv
    p["hp"] = core.stats(p)["体力"]
    p["mp"] = core.mp_max(p)
    p["learned"] = [e["name"] for e in core.skill_pool(p)]
    p["loadout"] = list(p["learned"])[:cfg.LOADOUT_SIZE]
    return p


def _fresh_duels():
    """每个用例重置进程级会话，互不污染。"""
    commands.duels = dc.ChallengeStore(cfg.DUEL_CHALLENGE_TTL)


def _snapshot(p):
    """无惩罚要守的全量字段快照。"""
    keys = ("hp", "mp", "gold", "exp", "lv", "wins", "losses",
            "duel_wins", "duel_losses")
    return {k: p.get(k) for k in keys} | {"bag": dict(p.get("bag") or {})}


# ---------------------------------------------------------------------------
# 1. 会话状态机（纯逻辑）
# ---------------------------------------------------------------------------
def test_store_ask_take_decline():
    _fresh_duels()
    s = commands.duels
    s.ask("10001", "10002", "g1", now=1000.0)
    check("发起后 pending 可见", s.pending_for("10002", "g1", 1000.0) is not None)
    c = s.take("10002", "g1", 1000.0)
    check("应战拿到挑战", c is not None and c.challenger == "10001", str(c))
    check("应战一次性消费", s.pending_for("10002", "g1", 1000.0) is None)
    check("重复应战为空", s.take("10002", "g1", 1000.0) is None)

    s.ask("10003", "10004", "g1", now=1000.0)
    check("拒战返回 True", s.decline("10004", "g1") is True)
    check("拒战后 pending 清空", s.pending_for("10004", "g1", 1000.0) is None)
    check("重复拒战返回 False", s.decline("10004", "g1") is False)


def test_store_timeout_and_group_isolation():
    _fresh_duels()
    s = commands.duels
    s.ask("10001", "10002", "g1", now=1000.0)
    check("超时未应战自动作废",
          s.take("10002", "g1", 1000.0 + cfg.DUEL_CHALLENGE_TTL + 1) is None)
    check("超时后会话被清理", len(s) == 0, str(len(s)))

    # 仅本群：A 群的挑战不能被 B 群同名 openid 冒领
    s.ask("10001", "10002", "g1", now=1000.0)
    check("跨群冒领失败", s.take("10002", "g2", 1000.0) is None)
    check("跨群冒领后原挑战仍在", s.pending_for("10002", "g1", 1000.0) is not None)

    # 同一目标被两人先后挑战：以最新一条为准
    s.ask("10003", "10002", "g1", now=1001.0)
    c = s.take("10002", "g1", 1001.0)
    check("重复挑战覆盖为最新", c is not None and c.challenger == "10003", str(c))


# ---------------------------------------------------------------------------
# 2. 命令层：发起 / 边界
# ---------------------------------------------------------------------------
def test_challenge_guards():
    _fresh_duels()
    a, b = _p("10001", "甲"), _p("10002", "乙")
    store = MemStore({"10001": a, "10002": b})

    out = asyncio.run(commands.cmd_duel_challenge(store, "99999", "10002", "g1"))
    check("未入道不能发起", "尚未踏入" in out, out)

    out = asyncio.run(commands.cmd_duel_challenge(store, "10001", "10002", "pv:10001"))
    check("私聊不能发起", "群聊" in out, out)

    out = asyncio.run(commands.cmd_duel_challenge(store, "10001", None, "g1"))
    check("无目标给用法", "用法" in out, out)

    out = asyncio.run(commands.cmd_duel_challenge(store, "10001", "10001", "g1"))
    check("不能切磋自己", "自己" in out, out)

    out = asyncio.run(commands.cmd_duel_challenge(store, "10001", "88888", "g1"))
    check("对方未入道", "尚未入道" in out, out)

    c = _p("10003", "丙", group="g2")
    store.players["10003"] = c
    out = asyncio.run(commands.cmd_duel_challenge(store, "10001", "10003", "g1"))
    check("跨群不能发起", "不在本群" in out, out)


def test_challenge_success_prompt():
    _fresh_duels()
    a, b = _p("10001", "甲"), _p("10002", "乙")
    store = MemStore({"10001": a, "10002": b})
    out = asyncio.run(commands.cmd_duel_challenge(
        store, "10001", "10002", "g1", name="甲道友"))
    check("发起回包含双方昵称", "甲道友" in out and "乙" in out, out[:120])
    check("发起回包明示无惩罚", "不扣血" in out and "不掉钱" in out, out[:120])
    check("发起回包告知应战口令", "应战" in out, out[:120])
    check("会话确实登记", len(commands.duels) == 1, str(len(commands.duels)))


# ---------------------------------------------------------------------------
# 3. 结算：无惩罚 + 荣誉战绩 + 属性归因
# ---------------------------------------------------------------------------
def test_accept_honor_only():
    _fresh_duels()
    a, b = _p("10001", "甲"), _p("10002", "乙")
    store = MemStore({"10001": a, "10002": b})
    commands.duels.ask("10001", "10002", "g1")
    before_a, before_b = _snapshot(a), _snapshot(b)

    out = asyncio.run(commands.cmd_duel_accept(store, "10002", "g1"))
    check("应战产出战报", "切磋" in out and "回合" in out, out[:120])

    # 无惩罚核心：除荣誉字段外，一切数值原封不动
    for k in ("hp", "mp", "gold", "exp", "lv", "wins", "losses", "bag"):
        check("切磋零惩罚·发起方 %s" % k, a.get(k) == before_a[k],
              f"{a.get(k)} vs {before_a[k]}")
        check("切磋零惩罚·应战方 %s" % k, b.get(k) == before_b[k],
              f"{b.get(k)} vs {before_b[k]}")

    wa = int(a.get("duel_wins", 0)) + int(a.get("duel_losses", 0))
    wb = int(b.get("duel_wins", 0)) + int(b.get("duel_losses", 0))
    check("双方荣誉各记一场", wa == 1 and wb == 1, f"a={wa} b={wb}")
    check("胜者+胜 / 败者+负", (a["duel_wins"], b["duel_wins"]) in ((1, 0), (0, 1)),
          f"a={a['duel_wins']} b={b['duel_wins']}")
    check("战报标无惩罚", "未扣血" in out and "未掉钱" in out, out[-160:])
    check("战报带双方昵称", "甲" in out and "乙" in out, out[:120])
    check("战报带属性归因",
          any(k in out for k in ("力量", "敏捷", "斗气", "防御")), out[:200])
    check("应战后会话清空", len(commands.duels) == 0, str(len(commands.duels)))


def test_accept_and_decline_guards():
    _fresh_duels()
    a, b = _p("10001", "甲"), _p("10002", "乙")
    store = MemStore({"10001": a, "10002": b})

    out = asyncio.run(commands.cmd_duel_accept(store, "10002", "g1"))
    check("无挑战时应战有提示", "没有待应" in out, out)
    out = asyncio.run(commands.cmd_duel_decline("10002", "g1"))
    check("无挑战时拒战有提示", "没有待应" in out, out)

    out = asyncio.run(commands.cmd_duel_accept(store, "10002", "pv:10002"))
    check("私聊不能应战", "群聊" in out, out)

    commands.duels.ask("10001", "10002", "g1")
    out = asyncio.run(commands.cmd_duel_decline("10002", "g1"))
    check("拒战回包", "婉拒" in out, out)
    check("拒战后无挑战", len(commands.duels) == 0)
    check("拒战不改荣誉", a.get("duel_wins", 0) == 0 and b.get("duel_wins", 0) == 0)


def test_panel_and_menu_expose_duel():
    a = _p("10001", "甲")
    panel = asyncio.run(commands.cmd_panel(MemStore({"10001": a}), "10001"))
    check("人物卡显示切磋荣誉", "切磋荣誉" in panel and "无惩罚" in panel, panel[-160:])
    check("菜单列出切磋", "切磋 @某人" in commands.menu_text())
    check("菜单列出应战", "应战" in commands.menu_text())
    check("菜单列出拒战", "拒战" in commands.menu_text())
    check("菜单条数 51", commands.menu_total() == 51, str(commands.menu_total()))


def _run():
    fns = [(k, v) for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    for _name, fn in fns:
        fn()
    print("=" * 70)
    print("DUEL_TOTAL=%d PASSED=%d FAILED=%d" % (_PASSED + _FAILED, _PASSED, _FAILED))
    return 0 if _FAILED == 0 else 1


if __name__ == "__main__":
    sys.exit(_run())
