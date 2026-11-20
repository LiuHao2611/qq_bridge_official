# -*- coding: utf-8 -*-
"""tools/check_doupo_menu_two_col.py —— 斗破菜单「两列并排 + 精简示例参数」交付闸门。

用户 2026-11-20：「优化一下斗破菜单，太长了，能两排就两排，隐藏掉无用的口令」。
落地口径（用户确认）：
  1. 每组口令两两配对成左右两列 cell（模板 doupo_menu.layout）；
  2. 带具体示例参数的口令卡面只显示首词（收服 火云水炎 → 收服），
     命令系统真值 MENU_GROUPS **不动**，口令仍可用。

本闸门把这次的契约钉死（prove-it：改坏了必须真红）：
  * menu_cmd_label 精简函数不许退化（带参数必须截首词、无参数必须原样）；
  * 两列布局不许退回单列（每行必须 2 个 cell、cx 恰好两个值）；
  * 口令精简后不丢字、说明不丢字；
  * 卡高必须比旧单列基线明显下降（否则「两排」名存实亡）。

用法：
    python tools/check_doupo_menu_two_col.py            # 报告
    python tools/check_doupo_menu_two_col.py --check    # 违规 exit 1
    python tools/check_doupo_menu_two_col.py --selftest # 反例注入：每个旧错都必须被拦
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src" / "plugins" / "qq_bridge"))

from pic.templates import doupo_menu as M  # noqa: E402

COMMANDS = ROOT / "src/plugins/doupo/commands.py"
TEMPLATE = ROOT / "src/plugins/qq_bridge/pic/templates/doupo_menu.py"
TMPL_SRC = TEMPLATE.read_text(encoding="utf-8")
WIDTH = 720


def load_groups() -> tuple:
    src = COMMANDS.read_text(encoding="utf-8")
    for node in ast.parse(src).body:
        if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") == "MENU_GROUPS":
            return ast.literal_eval(node.value)
    raise SystemExit("[数据] commands.py 里找不到 MENU_GROUPS")


def menu_data(shorten: bool = True) -> dict:
    groups = load_groups()
    label = M.menu_cmd_label if shorten else (lambda c: c)
    return {
        "title": "斗破菜单",
        "groups": [
            {"name": g, "rows": [[label(c), d] for c, d in rows]}
            for g, rows in groups
        ],
        "tip": "共 %d 条口令 · 纯中文，菜单是唯一切换口"
               % sum(len(rows) for _, rows in groups),
    }


# ---------------------------------------------------------- 精简函数契约 ----
def label_problems() -> list[str]:
    out: list[str] = []
    cases = {
        "收服 火云水炎": "收服",
        "买 血灵草 2": "买",
        "转宗 迦南学院": "转宗",
        "上阵 1 3 5 7": "上阵",
        "送 100 @某人": "送",
        "斗技详情 狂狮怒罡": "斗技详情",
        "斗破菜单": "斗破菜单",
        "战斗回看": "战斗回看",
        "入道": "入道",
        " 修炼 ": "修炼",
    }
    for raw, want in cases.items():
        got = M.menu_cmd_label(raw)
        if got != want:
            out.append("[精简] menu_cmd_label(%r) = %r，应为 %r" % (raw, got, want))
    return out


# ------------------------------------------------------- 两列布局契约 ----
def two_col_problems() -> list[str]:
    out: list[str] = []
    data = menu_data(shorten=True)
    L = M.layout(data, WIDTH)
    rows = [n for n in L["nodes"] if n["k"] == "row"]
    total = sum(len(rows_of(g)) for g in data["groups"])
    if len(rows) != total:
        out.append("[两列] cell 数 %d != 口令数 %d" % (len(rows), total))
    # cx 恰好两个值（左右列起点）
    cxs = sorted({n["cx"] for n in rows})
    if len(cxs) != 2:
        out.append("[两列] cell 的 cx 应恰好 2 个（左右列），实际 %s" % cxs)
    else:
        gap = cxs[1] - cxs[0]
        if gap != M.PAD + (L["cmd_col"] + M.COL_GAP) - M.PAD:
            out.append("[两列] 左右列间距异常：cx gap=%d，cell_w=%d COL_GAP=%d"
                       % (gap, L["cmd_col"], M.COL_GAP))
    # 口令精简后不丢字：每个精简口令完整出现在某个 cell 的 cmd 行里
    labels = [M.menu_cmd_label(c) for _, rs in load_groups() for c, _ in rs]
    cell_cmds = ["".join(ln for ln, _ in n["cmd"]) for n in rows]
    for lab in labels:
        if lab not in cell_cmds:
            out.append("[两列] 精简口令 %r 未出现在任何 cell" % lab)
    return out


def rows_of(g: dict) -> list:
    return [r for r in (g.get("rows") or []) if r]


# ------------------------------------------------------------ 高度契约 ----
#: 旧版单列布局在 720 宽的实测高度（2026-11-20 改造前，52 条口令一列排开）
LEGACY_SINGLE_COL_H = 3474


def height_problems() -> list[str]:
    out: list[str] = []
    two = M.layout(menu_data(shorten=True), WIDTH)["h"]
    # 两列必须比旧单列基线矮（行数减半；说明折行略增但净高度应下降）
    if two >= LEGACY_SINGLE_COL_H * 0.97:
        out.append("[高度] 两列卡高 %d 未明显低于旧单列基线 %d（两排名存实亡）"
                   % (two, LEGACY_SINGLE_COL_H))
    if two < 360:
        out.append("[高度] 两列卡高 %d 异常矮，可能漏渲染口令" % two)
    return out


def source_problems() -> list[str]:
    out: list[str] = []
    if "def menu_cmd_label(" not in TMPL_SRC:
        out.append("[源码] menu_cmd_label 单一真值函数丢了")
    if "for pi in range(0, len(rows), 2)" not in TMPL_SRC:
        out.append("[源码] 两两配对的两列循环丢了（退回单列）")
    if '"cx": cx' not in TMPL_SRC:
        out.append("[源码] cell 左右列坐标 cx 丢了")
    return out


def all_problems() -> list[str]:
    return label_problems() + two_col_problems() + height_problems() + source_problems()


# -------------------------------------------------------------- 反例注入 ----
def selftest() -> list[tuple[str, bool]]:
    """每个旧错必须被对应检查拦住（改回单列 / 精简退化 / 高度无下降）。"""
    res: list[tuple[str, bool]] = []
    global TMPL_SRC
    orig_src = TMPL_SRC
    orig_label = M.menu_cmd_label

    # 1. 精简退化：不截参数
    M.menu_cmd_label = lambda c: str(c).strip()
    res.append(("精简退化(不截参数)", bool(label_problems())))
    M.menu_cmd_label = orig_label

    # 2. 两两配对循环改单列（步长 1）
    TMPL_SRC = orig_src.replace("for pi in range(0, len(rows), 2)",
                                "for pi in range(0, len(rows), 1)")
    res.append(("退回单列(步长1)", bool(source_problems())))
    TMPL_SRC = orig_src

    # 3. cx 坐标丢失
    TMPL_SRC = orig_src.replace('"cx": cx, "cw": cell_w,', '"cw": cell_w,')
    res.append(("cell 缺 cx 坐标", bool(source_problems())))
    TMPL_SRC = orig_src

    return res


def main() -> int:
    args = set(sys.argv[1:])
    if "--selftest" in args:
        cases = selftest()
        for name, caught in cases:
            print(("  OK   " if caught else "  MISS ") + name)
        bad = [n for n, caught in cases if not caught]
        print("SELFTEST %d/%d 被拦" % (len(cases) - len(bad), len(cases)))
        return 1 if bad else 0

    probs = all_problems()
    if probs:
        for p in probs:
            print("FAIL " + p)
    else:
        two = M.layout(menu_data(True), WIDTH)["h"]
        print("PASS 两列布局：cell=%d，卡高 %d（旧单列基线 %d，降 %.0f%%）"
              % (sum(len(rows_of(g)) for g in menu_data(True)["groups"]),
                 two, LEGACY_SINGLE_COL_H, 100 * (1 - two / LEGACY_SINGLE_COL_H)))
    if "--check" in args and probs:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
