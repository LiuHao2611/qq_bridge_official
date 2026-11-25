# -*- coding: utf-8 -*-
"""官方通道「按钮重复发送」回归守门（2026-11-25，prove-it）。

## 真实故障（用户截图 + 线上日志铁证）

用户原话：「处理下BUG，机器人重复发送按钮问题，不要再出现」。
截图：一条战斗文本下方挂着「战斗回看 / 战斗评价 / 任务」，紧接着又冒出一条
标题为「## 快捷指令」的消息，挂着**同一组**按钮 —— 同一次口令，按钮出现两遍。

线上日志（``logs/service.err.log``，同一 msg_id 下 seq 连号，同一次「战斗」口令）：

    event=official_sent kind=text_card msg_seq=1 buttons=战斗回看,战斗评价,任务
    event=official_sent kind=keyboard  msg_seq=2 buttons=战斗回看,战斗评价,任务

## 根因

2026-11-25 第四轮为修「纯文本发出去客户端空白」，把纯文本改成
``markdown + keyboard`` 同段（``seg_kind="text_card"``），键盘已经挂在文本段上。
但下面还留着旧降级条件 ``has_buttons and (not wanted_card or ...)``：
``wanted_card`` 只在「有图 + 有按钮口令 + 有按钮」时为真，**纯文本恰好为假**
→ 条件成立 → 又补发一条独立按钮段（``## 快捷指令`` + 同一组按钮）。

## 守门设计

① 行为：从源文件 AST 抽出真函数 ``needs_standalone_keyboard`` 执行，
   跑 16 组真值表 + 生产实例（文本已带键盘 → 必须不发）；
② 接线：``send()`` 内「发独立按钮段」必须且只能由该函数守卫；``text_has_kb``
   必须与 ``seg_kind="text_card"`` 同一分支置位；
③ 变异反证（三处，全部必须在源字符串上改再编译，真跑）：
   - 判定忽略 ``text_has_kb`` → 生产实例立刻复现事故；
   - 删掉 ``text_has_kb = True`` 置位 → 接线门禁打红；
   - 把守卫换回裸条件 → 接线门禁打红。

跑法：``python tests/test_official_no_dup_keyboard.py``（宿主纯标准库）
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_OFFICIAL = _REPO / "src/plugins/qq_bridge/channel/official.py"
_SRC = _OFFICIAL.read_text(encoding="utf-8")

_PLANNER = "needs_standalone_keyboard"

#: 生产实例（事故当次）：纯文本 + 有按钮 + 文本段已挂键盘 + image_only 默认开
_INCIDENT = dict(has_buttons=True, wanted_card=False,
                 image_only_fallback=True, text_has_kb=True)

#: 真值表：has_buttons, wanted_card, image_only, text_has_kb → 是否还需独立按钮段
_TABLE = [
    # 无按钮：任何模式都不发（零按钮卡绝不发空 keyboard）
    ((False, False, True, False), False),
    ((False, True, True, False), False),
    ((False, True, False, False), False),
    ((False, False, True, True), False),
    # 🚨 本次事故：文本段已带键盘 → 一律不发（按钮只许出现一次）
    ((True, False, True, True), False),
    ((True, True, True, True), False),
    ((True, True, False, True), False),
    # 纯按钮（无文本、无图）：必须发，否则用户看不到按钮
    ((True, False, True, False), True),
    ((True, False, False, False), True),
    # 图+按钮卡：image_only 降级只发图（用户硬要求「图与按钮不分开发」）
    ((True, True, True, False), False),
    # 图+按钮卡：显式 two_messages 回退模式才允许两条
    ((True, True, False, False), True),
]


def _extract_fn(src: str, fn_name: str):
    """从源码 AST 抽出单个函数并 exec（真函数体，不是复刻逻辑）。"""
    tree = ast.parse(src)
    node = next((n for n in tree.body
                 if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                 and n.name == fn_name), None)
    assert node is not None, "源码找不到函数 %s" % fn_name
    module = ast.Module(body=[node], type_ignores=[])
    ast.fix_missing_locations(module)
    ns: dict = {}
    exec(compile(module, "<%s>" % fn_name, "exec"), ns)  # noqa: S102 — 只编译自己的源码
    return ns[fn_name]


def _send_node(src: str) -> ast.AsyncFunctionDef:
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "send":
            return node
    raise AssertionError("official.py 找不到 async def send")


def _is_segments_keyboard_append(node: ast.AST) -> bool:
    """``segments.append((btn_seg, "keyboard"))`` 形态。"""
    if not isinstance(node, ast.Call):
        return False
    fn = node.func
    if not (isinstance(fn, ast.Attribute) and fn.attr == "append"
            and isinstance(fn.value, ast.Name) and fn.value.id == "segments"):
        return False
    for arg in node.args:
        if isinstance(arg, ast.Tuple):
            for elt in arg.elts:
                if isinstance(elt, ast.Constant) and elt.value == "keyboard":
                    return True
    return False


def _is_planner_if(node: ast.AST) -> bool:
    return (isinstance(node, ast.If) and isinstance(node.test, ast.Call)
            and isinstance(node.test.func, ast.Name)
            and node.test.func.id == _PLANNER)


def guard_keyboard_once(src: str) -> None:
    """门禁①：独立按钮段有且只有一处，且整段都在 needs_standalone_keyboard 守卫下。"""
    send = _send_node(src)
    kb_lines = sorted(n.lineno for n in ast.walk(send)
                      if _is_segments_keyboard_append(n))
    assert kb_lines, "send() 里找不到「发独立按钮段」，按钮会整体消失"
    assert len(kb_lines) == 1, (
        "send() 里出现 %d 处独立按钮段（行 %s）——按钮可能被发多遍"
        % (len(kb_lines), kb_lines)
    )
    guarded: set[int] = set()
    for node in ast.walk(send):
        if _is_planner_if(node):
            for sub in node.body:
                for d in ast.walk(sub):
                    ln = getattr(d, "lineno", None)
                    if ln:
                        guarded.add(ln)
    assert kb_lines[0] in guarded, (
        "第 %d 行「发独立按钮段」不在 %s() 守卫下（裸条件会让文本已带键盘时再发一遍）"
        % (kb_lines[0], _PLANNER)
    )


def guard_text_kb_wired(src: str) -> None:
    """门禁②：text_has_kb 必须与 seg_kind="text_card" 在同一分支里一起置位。"""
    send = _send_node(src)
    wired = False
    for node in ast.walk(send):
        if not isinstance(node, ast.If):
            continue
        names, consts = set(), set()
        for stmt in node.body:
            if (isinstance(stmt, ast.Assign) and len(stmt.targets) == 1
                    and isinstance(stmt.targets[0], ast.Name)
                    and isinstance(stmt.value, ast.Constant)):
                names.add(stmt.targets[0].id)
                consts.add(stmt.value.value)
        if "text_card" in consts:
            assert "text_has_kb" in names, (
                "seg_kind=\"text_card\" 的分支没有置 text_has_kb=True"
                " —— 重复按钮回归（第 %d 行）" % node.lineno
            )
            wired = True
    assert wired, 'send() 里找不到 seg_kind="text_card" 分支'
    assert re.search(r"^\s*text_has_kb = False\s*$", src, re.M), (
        "send() 缺少 text_has_kb 初值 False"
    )


# ---------------------------------------------------------------- 行为：真值表

def test_planner_truth_table():
    planner = _extract_fn(_SRC, _PLANNER)
    bad = []
    for (hb, wc, io, tk), want in _TABLE:
        got = planner(hb, wc, io, tk)
        if got is not want:
            bad.append("has_buttons=%s wanted_card=%s image_only=%s text_has_kb=%s "
                       "→ %s（期望 %s）" % (hb, wc, io, tk, got, want))
    assert not bad, "真值表不符：\n" + "\n".join(bad)


def test_production_incident_does_not_duplicate():
    """用户截图那一次：纯文本 + 按钮 + 文本已挂键盘 → 绝不能再发独立按钮段。"""
    planner = _extract_fn(_SRC, _PLANNER)
    assert planner(**_INCIDENT) is False, (
        "文本已带键盘仍要补发独立按钮段 —— 用户端必然出现两条消息、同一组按钮"
    )


def test_button_only_reply_still_sent():
    """无文本的纯按钮回复不能被误关（否则按钮消失，比重复更糟）。"""
    planner = _extract_fn(_SRC, _PLANNER)
    assert planner(True, False, True, False) is True


# ---------------------------------------------------------------- 接线：AST

def test_send_wires_planner_and_flags():
    send_src = ast.get_source_segment(_SRC, _send_node(_SRC)) or ""
    assert _PLANNER + "(" in send_src, "send() 没有调用 %s()" % _PLANNER
    guard_keyboard_once(_SRC)
    guard_text_kb_wired(_SRC)


# ---------------------------------------------------------------- 变异反证

def test_mutation_planner_ignoring_text_kb_reproduces_bug():
    """把判定里的 text_has_kb 摘掉（= 旧逻辑）→ 事故必须复现，证明守门有效。"""
    mutated, n = re.subn(
        r"    if not has_buttons or text_has_kb:\n        return False\n"
        r"    return not wanted_card or not image_only_fallback",
        "    return has_buttons and (not wanted_card or not image_only_fallback)",
        _SRC,
    )
    assert n == 1, "变异注入失败：没匹配到判定函数体（源码已改，请更新本测试）"
    buggy = _extract_fn(mutated, _PLANNER)
    assert buggy(**_INCIDENT) is True, (
        "变异版本竟然不复现重复按钮 —— 说明生产实例本身已被绕过，守门是假绿的"
    )
    assert _extract_fn(_SRC, _PLANNER)(**_INCIDENT) is False


def test_mutation_drop_text_kb_flag_turns_red():
    """删掉 text_has_kb = True 置位 → 接线门禁必须打红。"""
    mutated, n = re.subn(r"^\s*text_has_kb = True\s*$\n?", "", _SRC, flags=re.M)
    assert n == 1, "变异注入失败：找不到 text_has_kb = True"
    try:
        guard_text_kb_wired(mutated)
    except AssertionError as e:
        assert "text_has_kb" in str(e), "报错信息没指向 text_has_kb：%s" % e
    else:
        raise AssertionError("删掉 text_has_kb=True 没被打红 —— 门禁是假的")


def test_mutation_bare_condition_turns_red():
    """把守卫换回裸条件（事故版本）→ 接线门禁必须打红。"""
    mutated, n = re.subn(
        _PLANNER + r"\(\s*has_buttons,\s*wanted_card,\s*"
        r"_MERGE_FALLBACK_IMAGE_ONLY,\s*text_has_kb\s*\)",
        "has_buttons and (not wanted_card or not _MERGE_FALLBACK_IMAGE_ONLY)",
        _SRC,
    )
    assert n == 1, "变异注入失败：找不到 %s 的四处实参调用（源码已改，请更新本测试）" % _PLANNER
    try:
        guard_keyboard_once(mutated)
    except AssertionError as e:
        assert _PLANNER in str(e), "报错信息没指向 %s：%s" % (_PLANNER, e)
    else:
        raise AssertionError("换回裸条件没被打红 —— 门禁是假的")


def main() -> int:
    tests = [(k, v) for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
        except Exception as e:  # noqa: BLE001
            failed += 1
            print("FAIL %s: %s" % (name, e))
        else:
            print("ok   %s" % name)
    print("\n%d/%d passed" % (len(tests) - failed, len(tests)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
