# -*- coding: utf-8 -*-
"""「等玩家回一句话」业务的统一时限门禁（2026-11-17 用户硬要求）。

用户原话：
> 战利品三选一不要复杂口令，只输出单个 1，2，3 就可以，凡是这种需要回答的业务，
> 都调整为 1 分钟内回复，超过 1 分钟失效并且失去奖励。

守什么
------
1. 三处业务的默认时限必须都等于 ``config.CHOICE_TTL``（唯一产品源），且为 60 秒；
2. 爬塔战利品：超时后 ``take_loot`` 必须拒绝并**清空待选**（奖励不补发）；
3. 机遇竞猜：超时后 ``pending_for`` 返回 None，且能报一次「刚超时」；
4. 裸数字入口 ``cmd_pick`` 在没有待选业务时必须返回空串（绝不吞群聊）。

运行：python tests/test_doupo_choice_ttl.py
"""
from __future__ import annotations

import importlib.util
import os
import sys
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DP = os.path.join(ROOT, "src", "plugins", "doupo")


def _stub_nonebot():
    nb = types.ModuleType("nonebot")
    nb.get_driver = lambda: types.SimpleNamespace(on_startup=lambda f: f)

    def on_regex(*a, **k):
        m = types.SimpleNamespace()
        m.handle = lambda *a, **k: (lambda f: f)
        m.finish = lambda *a, **k: None
        return m
    nb.on_regex = on_regex
    sys.modules["nonebot"] = nb
    for n in ("nonebot.adapters", "nonebot.adapters.onebot",
              "nonebot.adapters.onebot.v11"):
        sys.modules[n] = types.ModuleType(n)
    sys.modules["nonebot.adapters.onebot.v11"].MessageEvent = object
    sys.modules["nonebot.adapters.onebot.v11"].MessageSegment = object
    sys.modules["nonebot.adapters.onebot.v11"].Message = object


_stub_nonebot()
for _p in (ROOT, os.path.join(ROOT, "src", "plugins")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from doupo import config as cfg  # noqa: E402
from doupo import quiz_session as qs  # noqa: E402
from doupo import tower_session as ts  # noqa: E402


# ---------------------------------------------------------------------------
def test_ttl_single_source():
    """三处默认时限必须等于 config.CHOICE_TTL，且为 60 秒。"""
    assert cfg.CHOICE_TTL == 60, cfg.CHOICE_TTL
    assert cfg.DUEL_CHALLENGE_TTL == cfg.CHOICE_TTL, cfg.DUEL_CHALLENGE_TTL
    assert qs.DEFAULT_TTL == float(cfg.CHOICE_TTL), qs.DEFAULT_TTL
    assert ts.DEFAULT_LOOT_TTL == float(cfg.CHOICE_TTL), ts.DEFAULT_LOOT_TTL


def test_loot_ttl_countdown_and_expiry():
    """战利品：60 秒内可取、超时作废且待选被清空（奖励失去）。"""
    st = ts.new_state("q", seed="ttl")
    ts.set_loot(st, [{"key": "a"}, {"key": "b"}, {"key": "c"}], ttl=60.0, now=1000.0)
    assert ts.loot_left(st, now=1000.0) == 60
    assert ts.loot_left(st, now=1030.0) == 30
    # 60 秒内：正常取走
    loot, reason = ts.take_loot(st, 1, now=1059.0)
    assert reason == "ok" and loot["key"] == "b", (loot, reason)
    assert st["pending_loot"] is None and st["loot_deadline"] == 0.0


def test_loot_expired_loses_reward():
    """超过 60 秒：拒绝领取，并把待选清掉（不补发）。"""
    st = ts.new_state("q", seed="ttl2")
    ts.set_loot(st, [{"key": "a"}, {"key": "b"}, {"key": "c"}], ttl=60.0, now=1000.0)
    loot, reason = ts.take_loot(st, 0, now=1060.1)
    assert loot is None and reason == "expired", reason
    assert st["pending_loot"] is None, "超时后待选必须清空，否则可以补领"
    assert st["loot_expired"] is True
    # 再点一次也不能补领
    loot2, reason2 = ts.take_loot(st, 0, now=1061.0)
    assert loot2 is None and reason2 == "none", reason2


def test_expire_loot_reports_once():
    """expire_loot 只在「刚超时」返回 True，避免每次爬塔都刷提示。"""
    st = ts.new_state("q", seed="ttl3")
    ts.set_loot(st, [{"key": "a"}], ttl=60.0, now=1000.0)
    assert ts.expire_loot(st, now=1030.0) is False
    assert ts.expire_loot(st, now=1061.0) is True
    assert ts.expire_loot(st, now=1062.0) is False


def test_quiz_ttl_and_expired_marker():
    """机遇竞猜：60 秒内可答；超时返回 None 并留一次「刚超时」标记。"""
    store = qs.QuizStore(ttl=60.0)
    q = store.start("qq1", "题目", ["A", "B", "C"], 1, {"gold": 1}, now=2000.0)
    assert store.pending_for("qq1", now=2059.0) is not None
    assert store.pending_for("qq1", now=2061.0) is None
    assert store.take_expired("qq1") is True, "超时后必须能报一次"
    assert store.take_expired("qq1") is False, "标记只能消费一次"
    # 60 秒内作答：判对且一次性消费
    q2 = store.start("qq2", "题目", ["A", "B", "C"], 0, {"gold": 1}, now=3000.0)
    res = store.answer("qq2", q2.token, 0, now=3010.0)
    assert res.valid and res.correct
    assert store.pending_for("qq2", now=3011.0) is None


def test_quiz_default_store_uses_60():
    """进程级单例也必须是 60 秒（防止有人用默认 300 重新构造）。"""
    s = qs.STORE
    q = s.start("qq3", "题", ["A", "B"], 0, {}, now=4000.0)
    assert s.pending_for("qq3", now=4061.0) is None
    s.clear("qq3")
    assert s.take_expired("qq3") is False


def _string_literals(path):
    """取出文件里所有**玩家可见**字符串字面量（跳过模块/函数/类 docstring）。

    只对字面量做检查，注释与文档里写「60 秒」是叙述，不算文案。
    """
    import ast

    with open(path, encoding="utf-8") as f:
        tree = ast.parse(f.read())
    skip = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef)):
            body = getattr(node, "body", None) or []
            if (body and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                skip.add(id(body[0].value))
    return [(n.lineno, n.value) for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)
            and id(n) not in skip]


def test_no_hardcoded_seconds_in_player_text():
    """玩家可见文案的秒数必须来自 cfg.CHOICE_TTL，禁止再写死「60 秒」。

    真实坑：时限从 300 改 60 时，菜单里还留着旧秒数，文案与实现自相矛盾。
    菜单例外：``MENU_GROUPS`` 必须保持纯字面量（按钮门禁用 ast.literal_eval 静态
    解析，写成 ``"…%d 秒" % cfg.CHOICE_TTL`` 会解析失败），因此允许写「N 分钟」，
    但必须与 ``cfg.CHOICE_TTL`` 的分钟数一致。

    🚨 2026-11-24：本函数以前把**所有**「N 分钟」都当成选项时限，于是限时增益丹药
    的「力量 +45%（15 分钟）」被误判。药效时长与回复时限是两个语义域，不能共用
    一条判据 —— 所以「分钟」改成只在**选项语境**里审判（``CHOICE_CTX``），
    药效时长由 ``test_doupo_temp_buff.py`` 的 ``buff_min`` 一致性守门覆盖。
    秒数检查保持全量（药效从不按秒写）。
    """
    import re

    #: 出现这些词才说明这句在讲「回 1/2/3 的时限」，而不是丹药药效时长。
    CHOICE_CTX = ("回", "选择", "超时", "失效", "邀请", "战利品", "入塔", "内")
    bad = []
    for fn in sorted(os.listdir(DP)):
        if not fn.endswith(".py") or fn.startswith("test_"):
            continue
        for lineno, s in _string_literals(os.path.join(DP, fn)):
            if fn == "config.py":
                continue
            where = "%s:%d" % (fn, lineno)
            sec = re.search(r"(\d+)\s*秒", s)
            if sec and int(sec.group(1)) != int(cfg.CHOICE_TTL):
                bad.append("%s 写死 %s 秒（实际 %d）" % (where, sec.group(1), cfg.CHOICE_TTL))
            if not any(w in s for w in CHOICE_CTX):
                continue
            for mn in re.finditer(r"(\d+)\s*分钟", s):
                if int(mn.group(1)) * 60 != int(cfg.CHOICE_TTL):
                    bad.append("%s 写「%s 分钟」与 %d 秒不一致"
                               % (where, mn.group(1), cfg.CHOICE_TTL))
    assert not bad, "文案时限与 config.CHOICE_TTL 不一致：%s" % bad


def test_help_teaches_bare_number_and_loss():
    """帮助必须讲清「一律回 1、2、3」且「超时会失去奖励」（用户要求的口径）。"""
    txt = "\n".join(s for _, s in _string_literals(os.path.join(DP, "commands.py")))
    assert "直接回 1、2、3" in txt, "帮助里缺少「直接回 1、2、3」"
    assert "失去" in txt, "帮助里没写明超时会失去奖励"


def _main():
    fns = [(n, f) for n, f in globals().items() if n.startswith("test_") and callable(f)]
    failed = 0
    for n, f in sorted(fns):
        try:
            f()
            print("PASS", n)
        except AssertionError as e:
            failed += 1
            print("FAIL", n, "->", e)
    print("\n%d/%d 通过，失败 %d" % (len(fns) - failed, len(fns), failed))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(_main())
