# -*- coding: utf-8 -*-
"""战斗回看卡 · BOSS 登场引言块排版守门（2026-11-23）。

用户点名：「战斗回看卡片，顶部的深色背景，我看内外边距没调整好，还有字体颜色」。

根因：引言面板用 state="locked" 画深紫底（ord_lock：深#2C2644 / 浅#3E3860），
但正文取 p["text"]——浅色主题里那是深墨紫 #26213A，深底深字对比仅 1.1~1.4:1；
折行宽度 content_w-16 又没扣左缩进 26px，正文右端越过面板右缘。

守四条：① 深底亮字对比度双主题都达标；② 右边距不贴边；③ 标题与正文不重叠；
④ 块内上下留白存在。变异反证注入回退写法必须变红。
"""
from __future__ import annotations

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (ROOT, os.path.join(ROOT, "src", "plugins")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from qq_bridge.pic.templates import doupo_battle as DB  # noqa: E402
from qq_bridge.pic.templates import doupo_ui as UI  # noqa: E402

_INTRO_BG = (UI.G["ord_lock"], UI.G_LIGHT["ord_lock"])


def _lum(h: str) -> float:
    h = h.lstrip("#")
    ch = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    def f(c):
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    return 0.2126 * f(ch[0]) + 0.7152 * f(ch[1]) + 0.0722 * f(ch[2])


def _contrast(a: str, b: str) -> float:
    l1, l2 = sorted((_lum(a), _lum(b)), reverse=True)
    return (l1 + 0.05) / (l2 + 0.05)


def test_intro_text_contrast_on_both_dark_backgrounds():
    """正文/标题在两套主题深色底上对比度都 >= 4.5:1（WCAG AA）。"""
    for bg in _INTRO_BG:
        assert _contrast(DB.INTRO_TEXT, bg) >= 4.5, f"正文在 {bg} 对比不足"
        assert _contrast(DB.INTRO_HEAD, bg) >= 4.5, f"标题在 {bg} 对比不足"
    # 回退到 p["text"] 的浅色主题深墨紫必须被判不合格（变异反证锚点）
    assert _contrast("#26213A", UI.G_LIGHT["ord_lock"]) < 2.0


def test_intro_uses_fixed_bright_colors_not_palette_text():
    """绘制必须用固定亮字常量，不能跟随 p["text"]/p["fret_hi"] 回到深底深字。"""
    import inspect
    src = inspect.getsource(DB._draw_intro)
    assert "INTRO_TEXT" in src and "INTRO_HEAD" in src
    assert 'color=INTRO_TEXT' in src
    assert "color=p[\"text\"]" not in src, "正文回退到 p['text'] 会深底深字"
    assert "p[\"fret_hi\"]" not in src, "标题回退到 fret_hi 浅主题只有 3.3:1"


def test_intro_layout_constants_symmetric_and_padded():
    """左右缩进对称留白、上下留白、标题间隙常量都存在且为正。"""
    assert DB.INTRO_INSET_L > DB.INTRO_PAD_R >= 10
    assert DB.INTRO_PAD_T >= 8 and DB.INTRO_PAD_B >= 8
    assert DB.INTRO_HEAD_GAP >= 4
    assert DB.INTRO_ROW > DB.FS_BODY  # 行高大于字号，行距不挤


def test_wrap_width_deducts_inset_and_right_pad():
    """_rich_layout 折行宽度必须扣掉左缩进 + 右内边距，否则正文贴右边。"""
    import inspect
    src = inspect.getsource(DB._rich_layout)
    assert "content_w - INTRO_INSET_L - INTRO_PAD_R" in src, \
        "折行没扣左右留白，正文会越过面板右缘"
    assert "content_w - 16" not in src, "回退到旧的 content_w-16 会贴边"


def test_rec_no_extra_half_row():
    """boxes 记录不能再减半行高，否则标题/正文在门禁里假重叠。"""
    import inspect
    src = inspect.getsource(DB._draw_intro)
    assert 'rec("intro_%d" % i, line, x0 + INTRO_INSET_L, ly, FS_BODY)' in src, \
        "rec 传了 ly-半行高会让 boxes 记录偏上 13px"


def test_mutation_guard_contrast_detector_works():
    """变异反证：注入旧回退色，对比度检测器必须判红。"""
    assert _contrast("#26213A", UI.G_LIGHT["ord_lock"]) < 2.0, \
        "若这条红了，说明对比度检测器失效，守不住深底深字回退"
