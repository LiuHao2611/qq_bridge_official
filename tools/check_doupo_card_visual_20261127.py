# -*- coding: utf-8 -*-
"""斗技名录卡视觉契约守门（2026-11-27 改造）。

用户当场提的四条，逐条固化成「源码契约 + 反例注入」：
  1) 斗技名称不许省略：模板必须用 ``_name_lines`` 折行，不许再 ``_fit(..., …)`` 截断；
     数据层 ``card.py`` 把完整 rank 带进 row（``"rank": rank``）。
  2) 分区标题要完整阶位（地阶高级，不是只写地阶）：``card.py`` 用 ``_rank_groups``
     按完整 rank 分组，模板 section 标题取 ``g["label"]``（= 完整阶位）。
  3) 图标不要简单几何：section 优先 ``_draw_rank_icon``（像素徽章），
     几何 ``icons.draw`` 只能作回退；5 枚徽章素材必须齐全。
  4) 技能字体不变（2026-11-27 用户看到像素字后纠正「技能的字体不要变，改回去」）：
     模板 ``build`` 不得强制 ``PIXEL_FAMILY``、不得设像素度量族——技能名/描述/chip
     走 theme 原本字体。像素化只保留在图标（契约 3）。反例把强制度量族放回去必须报错。

反例注入把每个修复点改回旧写法，闸门必须报错；全拦才算守门有效。
不 import nonebot / Pillow，纯读源码，可作轻量 CI 闸门。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CARD = ROOT / "src" / "plugins" / "doupo" / "card.py"
ROSTER = ROOT / "src" / "plugins" / "qq_bridge" / "pic" / "templates" / "doupo_roster.py"
FONTS = ROOT / "src" / "plugins" / "qq_bridge" / "pic" / "fonts.py"
CANVAS = ROOT / "src" / "plugins" / "qq_bridge" / "pic" / "canvas.py"
ICON_DIR = (ROOT / "src" / "plugins" / "qq_bridge" / "pic" / "templates" /
            "assets" / "doupo_rank_icons")
FONT_FILE = (ROOT / "src" / "plugins" / "qq_bridge" / "pic" / "templates" /
             "assets" / "doupo_fonts" / "doupo_pixel.ttf")
GEN = ROOT / "tools" / "make_doupo_pixel.py"

# 可注入的反例源：默认读真实源码，selftest 传改坏的版本进来。
_SRC = {p.name: p.read_text(encoding="utf-8") for p in (CARD, ROSTER, FONTS, CANVAS, GEN)}


def visual_problems(src: dict[str, str] | None = None) -> list[str]:
    src = _SRC if src is None else src
    card_s, ros_s = src[CARD.name], src[ROSTER.name]
    font_s, can_s, gen_s = src[FONTS.name], src[CANVAS.name], src[GEN.name]
    out: list[str] = []

    # --- 契约 1：名称不省略 ---
    if "def _name_lines" not in ros_s:
        out.append("[名称] 模板丢了 _name_lines（斗技名又被 _fit 截断成「…」）")
    if "S.wrap_cjk" not in ros_s:
        out.append("[名称] 折行不再走 wrap_cjk（中文长名会被按半宽拆断）")
    if '"name_lines": _name_lines' not in ros_s:
        out.append("[名称] _row_texts 没把 name_lines 交给绘制层")
    if '"rank": rank' not in card_s:
        out.append("[名称] card.py 的 _roster_row 没带完整 rank，分组拿不到阶位")
    if any(ln.strip().startswith("ktext(") and "max_w=" in ln
           and "name" in ln.lower() and "name_lines" not in ln
           for ln in ros_s.splitlines()):
        out.append("[名称] 名字绘制又用回 ktext(max_w=...)（省略号回归）")

    # --- 契约 2：完整阶位标题 ---
    if "def _rank_groups" not in card_s:
        out.append("[阶位] card.py 丢了 _rank_groups（分区退回黄/玄/地/天大阶）")
    if '"tier": rank' not in card_s or '"label": rank' not in card_s:
        out.append("[阶位] _rank_groups 的 tier/label 不是完整 rank")
    if "rank_mod.sort_key" not in card_s:
        out.append("[阶位] 完整阶位不再按 sort_key 排序（黄阶高级会排到玄阶后）")
    if 'g.get("label")' not in ros_s:
        out.append("[阶位] 模板分区标题不读 g[label]，完整阶位显示不出来")

    # --- 契约 3：像素阶级图标 ---
    if "def _draw_rank_icon" not in ros_s:
        out.append("[图标] 模板丢了 _draw_rank_icon（分区又画几何圆/三角）")
    if "if not _draw_rank_icon" not in ros_s:
        out.append("[图标] section 未优先调用 _draw_rank_icon")
    if "paste_alpha" not in ros_s:
        out.append("[图标] 像素徽章不用 paste_alpha 合成（c.image 已不存在会崩）")
    need = {"rank_huang.png", "rank_xuan.png", "rank_di.png",
            "rank_tian.png", "rank_mifa.png"}
    have = {p.name for p in ICON_DIR.glob("*.png")} if ICON_DIR.is_dir() else set()
    miss = need - have
    if miss:
        out.append("[图标] 徽章素材缺失：%s" % ", ".join(sorted(miss)))

    # --- 契约 4：技能字体不变（不得强制像素字） ---
    # 用户看到全卡方块像素字后明确「技能的字体不要变，改回去」。像素化只留图标。
    if "set_measure_family(PIXEL_FAMILY)" in ros_s:
        out.append("[字体] 模板 build 又强制像素度量族（技能名/描述会变方块字，用户不要）")
    if "font_family=PIXEL_FAMILY" in ros_s:
        out.append("[字体] 模板 build 又把主题字体换成 PIXEL_FAMILY（技能字体被改）")
    if "from dataclasses import replace as _dc_replace" in ros_s:
        out.append("[字体] 模板 build 又在浅拷贝 Theme 替换 font_family")
    return out


def selftest() -> list[tuple[str, bool]]:
    cases: list[tuple[str, dict[str, str]]] = []

    def mut(name: str, old: str, new: str) -> dict[str, str]:
        s = dict(_SRC)
        # 全量替换，且 new 不得含 old 子串（否则「改坏」后标志串仍在 → 假 MISS）。
        s[name] = s[name].replace(old, new)
        return s

    cases.append(("名称折行改回 _fit 截断",
                  mut(ROSTER.name, "def _name_lines", "def _old_name_lines")))
    cases.append(("名称不再带 rank",
                  mut(CARD.name, '"rank": rank', '"rank": None')))
    cases.append(("分组退回大阶（删 _rank_groups）",
                  mut(CARD.name, "def _rank_groups", "def _old_rank_groups")))
    cases.append(("分区标题写死大阶而非 label",
                  mut(ROSTER.name, 'g.get("label")', '"%s斗技" % tier')))
    cases.append(("section 不画像素徽章",
                  mut(ROSTER.name, "if not _draw_rank_icon", "if False and not _draw_rank_icon")))
    cases.append(("徽章改回 c.image.paste（会崩）",
                  mut(ROSTER.name, "paste_alpha", "raw_image_paste")))
    # 字体反例：把「强制像素字体」的旧写法放回模板，闸门必须报错。
    cases.append(("模板又强制像素度量族",
                  mut(ROSTER.name, "    data = data or {}\n",
                      "    from ..canvas import set_measure_family\n"
                      "    set_measure_family(PIXEL_FAMILY)\n"
                      "    data = data or {}\n", )))
    cases.append(("模板又把主题换成像素字体",
                  mut(ROSTER.name, "    data = data or {}\n",
                      "    from dataclasses import replace as _dc_replace\n"
                      "    t = _dc_replace(t, font_family=PIXEL_FAMILY)\n"
                      "    data = data or {}\n", )))
    return [(label, bool(visual_problems(m))) for label, m in cases]


def main() -> int:
    probs = visual_problems()
    if probs:
        print("斗技名录卡视觉契约：FAIL（%d 项）" % len(probs))
        for p in probs:
            print("  " + p)
        return 1
    print("斗技名录卡视觉契约：PASS")
    print("  名称折行 / 完整阶位 / 像素徽章 / 技能字体不强制像素 四组契约齐备")
    checks = selftest()
    bad = [label for label, caught in checks if not caught]
    print("反例注入：%d/%d 被拦" % (len(checks) - len(bad), len(checks)))
    for label, caught in checks:
        print("  %s %s" % ("OK  " if caught else "MISS", label))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
