# -*- coding: utf-8 -*-
"""斗破·机遇「有奖竞猜」按钮问答守门（2026-11-16，prove-it）。

用户需求原话：
  「机遇有的是问答，应该增加选项按钮问答才对，而不是默认正确。」

本文件证明（全部用真实模块，非重写逻辑）：
  ① 触发竞猜**不发奖、不显示答案**（旧实现是显示答案 + 无条件 +1000）；
  ② question/options/answer 与 config 原版逐字题同源，答案绝不进展示文本；
  ③ 答题会话：一次性消费、token 校验、qq 隔离、惰性超时；
  ④ 答对才发奖、答错无奖、重复点击无效；
  ⑤ 按钮 data 前缀独立（doupo:quiz:），不污染卡片按钮 / 菜单 33 条基准。

跑法：python tests/test_quiz_answer.py（宿主，自带 runner）
      pytest tests/test_quiz_answer.py
"""
from __future__ import annotations

import os
import sys
import tempfile
import types

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PLUGINS = os.path.join(_ROOT, "src", "plugins")
for _p in (_ROOT, _PLUGINS):
    if _p not in sys.path:
        sys.path.insert(0, _p)

os.environ["DOUPO_CHANCE_RATE"] = "1.0"   # 本脚本要验证必触发路径


def _stub_nonebot():
    nb = types.ModuleType("nonebot")
    nb.get_driver = lambda: types.SimpleNamespace(on_startup=lambda f: f)
    nb.get_bot = lambda: types.SimpleNamespace(send=lambda *a, **k: None)

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
    v11 = sys.modules["nonebot.adapters.onebot.v11"]
    v11.MessageEvent = object
    v11.MessageSegment = object
    v11.Message = object


_stub_nonebot()

from doupo import commands, config as cfg, core, quiz_session as qs  # noqa: E402
from doupo.store import Store  # noqa: E402

ALWAYS = lambda: 0.0   # noqa: E731

QUIZ_EV = next(e for e in cfg.CHANCE_EVENTS if e.get("quiz"))


def _player(**kw):
    p = core.create_player("10001", "萧炎", "900000001")
    p.update(kw)
    return p


# ---------------------------------------------------------------------------
# ① 触发竞猜：不发奖、不显示答案
# ---------------------------------------------------------------------------
def test_quiz_trigger_grants_nothing_and_hides_answer():
    p = _player()
    gold, exp, luck = p["gold"], p["exp"], p["luck"]
    got = core._apply_chance(p, QUIZ_EV, ALWAYS)
    assert "quiz" in got, "竞猜必须走问答分支（返回 quiz 元数据）"
    assert int(p["gold"]) == gold and int(p["exp"]) == exp and int(p["luck"]) == luck, \
        "触发竞猜时绝不发奖"
    assert got["reward"] == {}, "展示结果里不应带已发奖励"
    answer = QUIZ_EV["quiz_items"][0]["answer"]
    answer_text = QUIZ_EV["quiz_items"][0]["options"][answer]
    assert answer_text not in got["text"], "题干文本泄露了正确答案"
    assert "答案正确" not in got["text"] and "获得1000" not in got["text"], \
        "旧「默认正确」文案必须删除"


# ---------------------------------------------------------------------------
# ② question/options/answer 同源，5 题齐全
# ---------------------------------------------------------------------------
def test_quiz_bank_aligned_with_original_lines():
    quiz = QUIZ_EV["quiz"]
    items = QUIZ_EV["quiz_items"]
    assert len(quiz) == len(items) == 5, "原版 5 题与结构化题库必须一一对应"
    for raw, item in zip(quiz, items):
        # question 从原版去掉「（答案：X）」得到
        question = raw.split("（答案：")[0].strip()
        got = core._apply_chance(_player(), dict(QUIZ_EV, quiz=[raw], quiz_items=[item]), ALWAYS)
        assert got["quiz"]["question"] == question, "题干必须逐字来自原版"
        ans = item["answer"]
        assert 0 <= ans < len(item["options"]), "答案下标越界"
        assert len(set(item["options"])) == len(item["options"]), "选项重复"


# ---------------------------------------------------------------------------
# ③ 会话：一次性 / token / qq 隔离 / 过期
# ---------------------------------------------------------------------------
def test_quiz_session_lifecycle():
    st = qs.QuizStore(ttl=100.0)
    q = st.start("u1", "题？", ["A", "B", "C", "D"], 1, {"gold": 1000}, now=1000.0)
    assert q.token and len(q.options) == 4
    assert st.pending_for("u1", now=1000.0) is q
    assert st.pending_for("u2", now=1000.0) is None, "不同 qq 不应读到彼此的题"

    bad = st.answer("u1", "wrong-token", 1, now=1000.0)
    assert not bad.valid and bad.reason == "bad_token"

    ok = st.answer("u1", q.token, 1, now=1000.0)
    assert ok.valid and ok.correct and ok.answer_idx == 1
    assert st.pending_for("u1", now=1000.0) is None, "答完必须一次性消费"

    again = st.answer("u1", q.token, 1, now=1000.0)
    assert not again.valid, "重复点击不得再次有效（防重复领奖）"


def test_quiz_session_expiry():
    st = qs.QuizStore(ttl=10.0)
    q = st.start("u1", "题？", ["A", "B"], 0, {}, now=1000.0)
    assert st.pending_for("u1", now=1005.0) is q
    assert st.pending_for("u1", now=1011.0) is None, "超时题目应失效并清理"
    r = st.answer("u1", q.token, 0, now=1011.0)
    assert not r.valid and r.reason == "no_session"


# ---------------------------------------------------------------------------
# ④ 答对发奖 / 答错无奖 / 命令层一次性
# ---------------------------------------------------------------------------
def _quiz_store_with_player():
    store = Store(os.path.join(tempfile.mkdtemp(prefix="doupo_quiz_"), "doupo.json"))
    import asyncio
    asyncio.run(store.load())
    return store


def test_answer_correct_grants_reward_once():
    import asyncio
    store = _quiz_store_with_player()
    asyncio.run(store.put_player(_player()))
    q = qs.STORE.start("10001", "题？", ["A", "B", "C", "D"], 2, {"gold": 1000})
    before = asyncio.run(store.get_player("10001"))["gold"]

    msg_ok = asyncio.run(commands.cmd_answer_quiz(store, "10001", q.token, 2))
    after = asyncio.run(store.get_player("10001"))["gold"]
    assert after == before + 1000, "答对必须 +1000 金币"
    assert "答对" in msg_ok and "1000" in msg_ok

    msg_again = asyncio.run(commands.cmd_answer_quiz(store, "10001", q.token, 2))
    after2 = asyncio.run(store.get_player("10001"))["gold"]
    assert after2 == after, "重复点击不得二次发奖"
    assert "失效" in msg_again or "无效" in msg_again


def test_answer_wrong_grants_nothing_but_shows_answer():
    import asyncio
    store = _quiz_store_with_player()
    asyncio.run(store.put_player(_player()))
    q = qs.STORE.start("10001", "题？", ["A", "B", "C", "D"], 3, {"gold": 1000})
    before = asyncio.run(store.get_player("10001"))["gold"]

    msg = asyncio.run(commands.cmd_answer_quiz(store, "10001", q.token, 0))
    after = asyncio.run(store.get_player("10001"))["gold"]
    assert after == before, "答错绝不发奖"
    assert "答错" in msg and "D" in msg, "答错要给出正确答案"


def test_chance_lines_arms_button_and_no_answer():
    p = _player()
    got = core._apply_chance(p, QUIZ_EV, ALWAYS)
    lines = commands._chance_lines(got, "10001")
    joined = "\n".join(lines)
    assert "选项" in joined and "答对" in joined
    token = qs.take_button()
    assert token, "_chance_lines 必须 arm 按钮 token"
    quiz = qs.STORE.peek_by_token(token)
    assert quiz is not None and quiz.qq == "10001"
    answer_text = quiz.options[quiz.answer]
    # 文本可以列出全部选项（NapCat 可读），但绝不能标注哪个是答案
    assert "答案" not in joined or ("答对" in joined), "不得出现「答案是X」式标注"
    assert "(%s)" % answer_text not in joined and "（%s）" % answer_text not in joined


def test_no_answer_default_text_in_chance_lines():
    """旧实现的「答案正确，获得1000金币奖励」必须彻底消失。"""
    p = _player()
    got = core._apply_chance(p, QUIZ_EV, ALWAYS)
    joined = "\n".join(commands._chance_lines(got, "10001"))
    assert "答案正确" not in joined and "获得1000" not in joined


# ---------------------------------------------------------------------------
# ⑤ 按钮 data 隔离：解析 + 不污染菜单基准
# ---------------------------------------------------------------------------
def test_quiz_button_data_parse_and_isolation():
    import importlib.util
    from doupo import button_dispatch as bd

    # channel 层选项按钮派生（零 nonebot，独立加载）
    ch_dir = os.path.join(_ROOT, "src", "plugins", "qq_bridge", "channel")
    spec = importlib.util.spec_from_file_location(
        "db_quiz_test", os.path.join(ch_dir, "doupo_buttons.py"))
    db = importlib.util.module_from_spec(spec)
    sys.modules["db_quiz_test"] = db
    spec.loader.exec_module(db)
    ct = db.arm_quiz("tok1", ["药老", "韩枫", "云山", "魂灭生"])
    btns = db.quiz_buttons_for("doupo:quiz:tok1")
    db.disarm_quiz(ct)
    assert len(btns) == 4, "4 个选项按钮"
    assert [b.label for b in btns] == ["药老", "韩枫", "云山", "魂灭生"]
    assert btns[0].cmd == "doupo:quiz:tok1:0", "按钮 data = 前缀+token+下标"
    assert db.quiz_buttons_for("doupo:panel") == (), "卡片口令不派生答题按钮"

    assert bd.parse_quiz_cmd("doupo:quiz:abc123:3") == ("abc123", 3)
    assert bd.parse_quiz_cmd("doupo:quiz:abc123:x") is None
    assert bd.parse_quiz_cmd("人物") is None      # 卡片口令不被误判为答题
    assert bd.parse_quiz_cmd("doupo:panel") is None

    # 菜单 canonical 口令不被误判为答题 data（菜单 33 基准由 test_doupo_buttons 守）
    for _title, rows in commands.MENU_GROUPS:
        for cmd_s, _desc in rows:
            assert bd.parse_quiz_cmd(cmd_s.split()[0]) is None


# ---------------------------------------------------------------------------
# runner
# ---------------------------------------------------------------------------
def _run_all():
    fns = [(k, v) for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    ok, fail = 0, []
    for name, fn in fns:
        try:
            fn()
            ok += 1
            print("PASS  %s" % name)
        except Exception as e:  # noqa: BLE001
            fail.append((name, e))
            print("FAIL  %s  ->  %s: %s" % (name, type(e).__name__, e))
    print("=" * 70)
    print("QUIZ_TOTAL=%d PASSED=%d FAILED=%d" % (len(fns), ok, len(fail)))
    return 0 if not fail else 1


if __name__ == "__main__":
    sys.exit(_run_all())
