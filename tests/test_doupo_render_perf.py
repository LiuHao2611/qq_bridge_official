# -*- coding: utf-8 -*-
"""P0 渲染性能改动的守门测试（docs/斗破卡片渲染性能_深度调研与优化方案_2026-11-23.md §8）。

守四条：
  1. PNG 用 compress_level=6 编码，与 optimize=True **逐像素一致**（无损）；
  2. 外壳缓存命中/未命中/再冷渲染，三次输出 **字节完全相同**；
  3. 换主题配色 -> key 变 -> 必须输出不同图（防缓存穿透）；
  4. 12 张斗破模板全部渲染通过，且缓存不改变任何一张的字节。

运行：
    python -m pytest tests/test_doupo_render_perf.py -q
"""
from __future__ import annotations

import io
from dataclasses import replace

from PIL import Image

from src.plugins.qq_bridge.pic import Canvas, list_templates, theme
from src.plugins.qq_bridge.pic.canvas import _PNG_COMPRESS_LEVEL, rgb
from src.plugins.qq_bridge.pic.templates import doupo as doupo_tpl
from src.plugins.qq_bridge.pic.templates import doupo_ui as UI


def _canvas() -> Canvas:
    c = Canvas(320, 180, "#0B0912")
    c.rounded_rect((10, 10, 300, 170), radius=12, fill="#403A62", outline="#E82870")
    c.text((24, 40), "斗破大陆", size=28, color="#FFD058", anchor="lm")
    return c


def _rgb_bytes(data: bytes) -> bytes:
    return Image.open(io.BytesIO(data)).convert("RGB").tobytes()


# ---------------------------------------------------------------- P0-1 编码
def test_png_compress_level_6_is_lossless_and_faster_param():
    assert _PNG_COMPRESS_LEVEL == 6
    c = _canvas()
    got = c.to_bytes()
    flat = Image.alpha_composite(
        Image.new("RGBA", c.img.size, rgb(c.bg) + (255,)), c.img).convert("RGB")
    b6 = io.BytesIO()
    flat.save(b6, format="PNG", compress_level=6)
    bo = io.BytesIO()
    flat.save(bo, format="PNG", optimize=True)
    # 与手写 c6 完全一致 -> 参数确实生效；与 optimize 像素一致 -> 无损
    assert got == b6.getvalue()
    assert got != bo.getvalue()
    assert _rgb_bytes(got) == _rgb_bytes(bo.getvalue())


def test_png_param_does_not_break_other_formats():
    # JPEG 不认 compress_level：非 PNG 仍走 optimize，确保不因本次改动崩掉
    c = _canvas()
    jpg = c.to_bytes("JPEG")
    assert Image.open(io.BytesIO(jpg)).format == "JPEG"


# ---------------------------------------------------------------- P0-2 外壳
def test_shell_cache_cold_hot_cold_are_byte_identical():
    t = theme.get(theme.LIGHT)
    data = {"title": "斗破大陆", "name": "萧炎", "lv": 50, "exp": 1200, "need": 3000}
    UI.clear_shell_cache()
    cold = doupo_tpl.build(t, data).to_bytes()
    hot = doupo_tpl.build(t, data).to_bytes()
    UI.clear_shell_cache()
    cold2 = doupo_tpl.build(t, data).to_bytes()
    assert cold == hot == cold2


def test_shell_cache_key_includes_theme_palette():
    t = theme.get(theme.LIGHT)
    data = {"title": "斗破大陆", "name": "萧炎"}
    changed = replace(t, bg_overrides={"doupo": {"card_top": "#123456"}})
    UI.clear_shell_cache()
    a = doupo_tpl.build(t, data).to_bytes()
    b = doupo_tpl.build(changed, data).to_bytes()
    assert a != b, "换主题配色后仍吐旧外壳 —— 缓存 key 漏了配色"


def test_shell_hit_uses_paste_not_alpha_composite():
    """命中必须覆盖式 paste；alpha_composite 在半透明画布上会与旧像素重影。

    当前 Canvas 初始 alpha=255，字节等价测试抓不到这条；故用源码契约锁住，
    防止以后有人"顺手改成更安全的 alpha_composite"引入重影。
    """
    import inspect
    src = inspect.getsource(UI.card_shell)
    assert "img.paste(shell, (0, 0))" in src
    assert "alpha_composite(shell" not in src


def test_shell_cache_content_y_matches_uncached():
    """牌匾高度必须随缓存一致，否则所有内容会整体上/下错位。"""
    t = theme.get(theme.LIGHT)
    UI.clear_shell_cache()
    c1 = Canvas(720, 810, "#0B0912")
    y1 = UI.card_shell(c1, doupo_tpl._pal(t), "斗破大陆", width=720)
    y2 = UI.card_shell(Canvas(720, 810, "#0B0912"),
                       doupo_tpl._pal(t), "斗破大陆", width=720)
    assert y1 == y2


# ---------------------------------------------------------------- 12 卡全覆盖
def test_all_doupo_templates_render_and_cache_is_invisible():
    names = [n for n in list_templates() if n.startswith("doupo")]
    assert len(names) >= 12
    t = theme.get(theme.LIGHT)
    for name in names:
        fn = __import__("src.plugins.qq_bridge.pic", fromlist=["_TEMPLATES"])._TEMPLATES[name]
        UI.clear_shell_cache()
        a = fn(t, width=720).to_bytes()
        b = fn(t, width=720).to_bytes()   # 命中缓存
        assert a == b, "%s 外壳缓存改变了输出" % name
        assert Image.open(io.BytesIO(a)).height > 0
