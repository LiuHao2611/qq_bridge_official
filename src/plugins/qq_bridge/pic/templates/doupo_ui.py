# -*- coding: utf-8 -*-
"""pic/templates/doupo_ui.py —— 斗破全卡「游戏本体 UI」绘制内核（2026-11-22）。

背景
----
用户看了解包素材 ``D:\\AI\\doupo``（斗破 HD 实机截图）后明确要求：
「不行，没有游戏的效果」「别拿免费的简约图标糊弄我」「注意颜色搭配」
「加深细节展示，比如云，特效，游戏中特有的字体」「注意各个元素的内外边距」。
方向敲定为**忠于游戏本体 UI 的深色玄幻**，不是浅色信息卡：

  * 深蓝紫渐变底 + 暗红祥云带 / 龙鳞纹 + 斜纹 + 上下暗角 + 顶部天光
  * 玫红回纹（云雷纹）角花双层外框 + 边中金菱钉
  * 青蓝云纹牌匾 + 楷体白→金渐变艺术字（紫描边 + 紫辉光）
  * 异火火焰徽章（不规则火团：玫红/紫外焰 + 橙黄火心 + 白核）
  * 游戏面板（深底 + 玫红/暗金细边 + 面板内祥云水印 + 左侧状态色条）

为什么单独一层
--------------
12 张斗破卡（doupo / _menu / _list / _skills / _faction / _recipes / _puppet /
_roster / _battle / _ruin / _help / _craft）各写一套装饰必然漂移。这里把
「配色 / 字体 / 原语 / 组件」收敛成唯一实现，各模板只调用组件。

三个必须知道的坑（都踩过）
--------------------------
1. ``Canvas.text(role=...)`` **不选字体文件**，role 只推断 kind；真正选字体的是
   ``theme.font_family``。所以楷体标题不能走 ``c.text``，必须本模块
   :func:`kai_text` / :func:`grad_text` 自己 ``ImageDraw.text``。
2. ``Canvas.img`` 会被 ``paste_alpha`` 越界分支**整体替换**，因此禁止缓存
   ``c.draw`` / 旧 img 引用；每次绘制前重新取 :func:`img_of`。
3. 回纹角花是 **33px 级**的装饰占位。任何贴角文字必须缩进到 ``indent(角花)``
   之外，否则就是用户看到的「字和边框黏在一起」。本模块提供
   :data:`CORNER_RESERVE` 与 :func:`content_x` 统一这个缩进。

衡量口径
--------
全部度量走 :func:`tw`（真实字形宽度），高度一律内容驱动；:class:`Boxes`
收集包围盒做「溢出 / 重叠 / 贴边」自检，阈值 :data:`MIN_MARGIN`。
"""
from __future__ import annotations

import math

from PIL import Image, ImageChops, ImageDraw, ImageFilter

from .. import fonts
from ..canvas import Canvas, emoji_safe
from . import doupo_style as S

# ============================================================================
# 配色：深蓝紫 + 玫红 + 青 + 金（取自 D:/AI/doupo 实机截图）
# ============================================================================
G: dict[str, str] = {
    "page":      "#0B0912",   # 页面外底色（近黑紫）
    "card_top":  "#403A62",   # 卡片渐变顶（深蓝紫）
    "card_mid":  "#2E294A",
    "card_bot":  "#1E1A30",
    "fret":      "#E82870",   # 回纹玫红（主边框）
    "fret_hi":   "#FF60A2",
    "fret_dk":   "#8C1242",
    "plaq_top":  "#3874AA",   # 牌匾青蓝
    "plaq_mid":  "#224C80",
    "plaq_bot":  "#142E54",
    "plaq_cld":  "#6CACE2",
    "gold":      "#FFD058",
    "gold_hi":   "#FFF0B0",
    "gold_dk":   "#925C12",
    "text":      "#EEE8FC",
    "dim":       "#BAB0DA",
    "weak":      "#8E86AC",
    "red":       "#FF6A7A",
    "green":     "#60E2A4",
    "cyan":      "#5EE0D2",
    "panel":     "#36305A",
    "panel_hi":  "#443C6C",
    "panel_ln":  "#623E7E",
    "cloud":     "#C43460",   # 暗红祥云
    "cloud2":    "#9C3684",   # 暗紫祥云
    "shadow":    "#080610",
}

# ---------------------------------------------------------------------------
# 第二套主题：浅色现代风（2026-11-23 用户「两个主题可以自由切换」）。
# 取自 11-22 被否的「2026 前沿配色」（commit 0312077）但保留游戏本体 UI 的
# 结构语言（回纹/牌匾/火焰/楷体）——只换色不换骨架，12 张卡零改动即可跟随。
# 凡是内核里"深色专用"的硬编码值（牌匾字描边、天光、暗角、轨道），都提到
# 配色表里做键；浅色表给浅色值，游戏表给原值，组件只读 g[...]。
# ---------------------------------------------------------------------------
G_LIGHT: dict[str, str] = {
    "page":      "#EDEAF6",   # 页面外底（浅薰衣草灰）
    "card_top":  "#FFFFFF",   # 卡片渐变顶（冷白）
    "card_mid":  "#F6F3FB",
    "card_bot":  "#EDE8F7",
    "fret":      "#7B52C9",   # 回纹改靛紫（浅底上玫红不够沉稳）
    "fret_hi":   "#A17BE0",
    "fret_dk":   "#C6B2EC",
    "plaq_top":  "#6D5AE0",   # 牌匾靛紫渐变
    "plaq_mid":  "#5B3FA8",
    "plaq_bot":  "#3E2A82",
    "plaq_cld":  "#C9B8F5",
    "gold":      "#B07A16",   # 金色压深：浅底上 #FFD058 看不见
    "gold_hi":   "#D9A62E",
    "gold_dk":   "#7A5210",
    "text":      "#26213A",   # 深墨紫字（对比 ≥10:1）
    "dim":       "#6B6486",
    "weak":      "#948DA8",
    "red":       "#D2364E",
    "green":     "#0F8A67",   # 薄荷绿完成态（0312077 定稿）
    "cyan":      "#0C8F9E",
    "panel":     "#F4F0FB",
    "panel_hi":  "#EAE3F8",
    "panel_ln":  "#CDBCEC",
    "cloud":     "#D9C8F0",   # 浅紫祥云水印（浅底要淡）
    "cloud2":    "#E2D3F2",
    "shadow":    "#D6CFE6",

    # ---- 深色专用值参数化（浅色表给浅色值）----
    "title_hi":   "#FFFFFF",  # 牌匾字渐变顶
    "title_lo":   "#FFE8A8",  # 牌匾字渐变底
    "title_edge": "#8030B2",  # 牌匾紫描边
    "title_glow": "#BA6EF6",  # 牌匾外辉光
    "sky":        "#4A78D0",  # 顶部天光色（浅色表偏冷）
    "vignette":   "#2A1B4A",  # 暗角色（浅色表很淡）
    "chip_fill":  "#FFFFFF",  # 状态标签底
    "ord_num":    "#FFFFFF",  # done 序号徽数字
    "ord_lock":   "#3E3860",  # locked 序号徽底
    "bar_track":  "#E4DCF2",  # 进度条轨道
    "flame_done_outer": "#E82870",  # 火焰外焰（完成）
    "flame_done_mid":   "#F05228",
    "flame_lock_outer": "#8E86AC",  # 火焰外焰（未解锁）
    "flame_lock_mid":   "#B8B0CA",
}

# 游戏本体风补齐同批参数键（值 = 原来的硬编码，保证老视觉零变化）
G.update({
    "title_hi": "#FFFFFF", "title_lo": "#FFE8A8",
    "title_edge": "#8030B2", "title_glow": "#BA6EF6",
    "sky": "#96C8FF", "vignette": "#000000",
    "chip_fill": "#1C1830", "ord_num": "#3A2006", "ord_lock": "#2C2644",
    "bar_track": "#241E3C",
    "flame_done_outer": "#CC1E5C", "flame_done_mid": "#F05228",
    "flame_lock_outer": "#3E3860", "flame_lock_mid": "#544E72",
})

#: 两套主题名（对外）。游戏本体风为默认。
STYLE_GAME = "game"
STYLE_LIGHT = "light"
STYLES = (STYLE_GAME, STYLE_LIGHT)


def palette(style: str | None) -> dict[str, str]:
    """按主题名取内核配色表；未知/空一律游戏本体风（默认）。"""
    return G_LIGHT if (style or "").strip().lower() == STYLE_LIGHT else G

#: 回纹角花占位（角内 9px 起、向外 6*unit）。文案缩进据此计算。
FRET_UNIT = 4
CORNER_RESERVE = 9 + 6 * FRET_UNIT          # = 33

#: 文本到面板边的最小允许间距（boxes 门禁阈值）
MIN_MARGIN = 10

#: 内容内边距。左右 36 → 距外框内线（17）19px；底部 54 → 距内线 37px。
#: 底部比左右再宽 18 的原因：底部两角只有「短 L 线 + 角钉」（见 :func:`frame`），
#: 视觉上比顶部牌匾区空得多，最后一行文字照 36 收尾会明显贴下边框
#: （2026-11-22 斗技卡实测 tip 下缘距内线仅 8px，用户点名「字和边框黏在一起」）。
PAD_X = 36
PAD_B = 54


def pal(p: dict | None = None) -> dict[str, str]:
    """模板 ``_pal(t)`` 的输出 → 本模块配色；缺失键回退 :data:`G`。

    允许 ``Theme.bg_overrides["doupo"]`` 里用同名键逐项覆盖（例如 ``fret``）。
    """
    out = dict(G)
    if isinstance(p, dict):
        for k in G:
            v = p.get(k)
            if isinstance(v, str) and v:
                out[k] = v
    return out


def content_x(x0: float) -> int:
    """回纹面板内的文字左起点：``x0 + CORNER_RESERVE + 11``。"""
    return int(x0 + CORNER_RESERVE + 11)


# ============================================================================
# 字体
# ============================================================================
def font(size: int, role: str = "regular"):
    """取字体实例。role: regular / bold / light / kai（楷体子集，游戏牌匾字）。"""
    return fonts.load(role, int(size))


def tw(s: str, size: int, role: str = "regular") -> int:
    """真实字形宽度（与绘制同 role 同 size，禁止估算）。"""
    return int(font(size, role).getlength(emoji_safe(str(s))))


def wrap(s: str, size: int, role: str, max_w: int) -> list[str]:
    """中文避头折行（复用 doupo_style.wrap_cjk）。"""
    return S.wrap_cjk(str(s), int(size), role, int(max_w))


def fit(s: str, size: int, role: str, max_w: int) -> str:
    """按最大宽度截断加省略号。"""
    s = str(s)
    if max_w <= 0:
        return ""
    if tw(s, size, role) <= max_w:
        return s
    while s and tw(s + "…", size, role) > max_w:
        s = s[:-1]
    return (s + "…") if s else ""


# ============================================================================
# 基础图元
# ============================================================================
def img_of(c: Canvas) -> Image.Image:
    """当前画布 RGBA 图（禁止缓存：paste_alpha 可能替换 Canvas.img）。"""
    return c.img


def rgb(hexcol: str) -> tuple[int, int, int]:
    from ..canvas import rgb as _rgb
    return _rgb(hexcol)


def vgrad(size: tuple[int, int], top: str, bot: str) -> Image.Image:
    """垂直两色渐变 RGB 图。"""
    return _vgrad(list(size), [top, bot])


def vgrad3(size: tuple[int, int], top: str, mid: str, bot: str) -> Image.Image:
    """垂直三色渐变（顶/中/底），中点在 50%。"""
    return _vgrad(list(size), [top, mid, bot])


def _vgrad(size: list[int], stops: list[str]) -> Image.Image:
    w, h = max(1, int(size[0])), max(1, int(size[1]))
    cols = [rgb(s) for s in stops]
    strip = Image.new("RGB", (1, h))
    px = strip.load()
    n = len(cols) - 1
    for y in range(h):
        t = (y / max(1, h - 1)) * n
        i = min(n - 1, int(t))
        u = t - i
        px[0, y] = tuple(int(cols[i][k] + (cols[i + 1][k] - cols[i][k]) * u) for k in range(3))
    return strip.resize((w, h))


def glow(c: Canvas, painter, blur: int = 10) -> None:
    """通用辉光：画到透明层 → 高斯模糊 → 叠加。"""
    img = img_of(c)
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    painter(ImageDraw.Draw(layer, "RGBA"))
    img.alpha_composite(layer.filter(ImageFilter.GaussianBlur(blur)))


def pattern_into(c: Canvas, box, radius: int, painter) -> None:
    """在面板内部铺纹样（圆角裁剪）。**必须在写字之前调用**，云纹才可见又不挡字。

    painter 签名 ``(layer, draw, w, h)``：需要画 ``d.arc`` 之类可用 draw，
    需要 ``cloud_band`` 这类整层操作则用 layer。
    """
    img = img_of(c)
    x0, y0, x1, y1 = [int(round(v)) for v in box]
    w, h = max(1, x1 - x0), max(1, y1 - y0)
    layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    painter(layer, ImageDraw.Draw(layer, "RGBA"), w, h)
    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, w - 1, h - 1), radius=radius, fill=255)
    layer.putalpha(ImageChops.multiply(layer.getchannel("A"), mask))
    img.alpha_composite(layer, (x0, y0))


# ---- 回纹 / 外框 -----------------------------------------------------------
def fret_corner(d: ImageDraw.ImageDraw, x: float, y: float, sx: int, sy: int,
                color: str, unit: int = 7, width: int = 3, pin: str | None = None) -> None:
    """回纹（云雷纹）角花：从角点向内的三组阶梯 L 形折线 + 角钉。"""
    segs = [
        [(0, 0), (6 * unit, 0)], [(0, 0), (0, 6 * unit)],
        [(unit, unit), (5 * unit, unit)], [(unit, unit), (unit, 5 * unit)],
        [(2 * unit, 2 * unit), (4 * unit, 2 * unit)], [(2 * unit, 2 * unit), (2 * unit, 4 * unit)],
    ]
    col = rgb(color) + (255,)
    for seg in segs:
        d.line([(x + sx * a, y + sy * b) for a, b in seg], fill=col, width=width, joint="curve")
    if pin:
        d.rectangle((x + sx * 2 - 2, y + sy * 2 - 2, x + sx * 2 + 2, y + sy * 2 + 2),
                    fill=rgb(pin) + (255,))


def frame(d: ImageDraw.ImageDraw, box, g: dict, unit: int = 10) -> None:
    """卡片外框：玫红粗线 + 暗玫红内线 + 四角回纹 + 上下边中金菱钉。"""
    x0, y0, x1, y1 = [int(round(v)) for v in box]
    d.rectangle((x0, y0, x1, y1), outline=rgb(g["fret"]) + (255,), width=4)
    d.rectangle((x0 + 7, y0 + 7, x1 - 7, y1 - 7), outline=rgb(g["fret_dk"]) + (255,), width=2)
    m = 9
    # 顶部两角：完整回纹角花（该区域被牌匾占据，不会压到正文）
    fret_corner(d, x0 + m, y0 + m, 1, 1, g["fret"], unit, 4)
    fret_corner(d, x1 - m, y0 + m, -1, 1, g["fret"], unit, 4)
    # 底部两角：只画一道 20px 短 L 线 + 角钉，**不画回纹**——底部是最后一行文字
    # 区，完整角花会占满 60×60 顶到正文（2026-11-22 斗技卡实测：tip 起点 44、
    # 角花止于 79，直接压住「通」字左半）；而 unit 缩小到 3 又会碎成粉红色块，
    # 故改为干净的 L 线（横向止于 x0+29，44 起的内容彻底让开）。
    for sx, bx in ((1, x0 + m), (-1, x1 - m)):
        d.line([(bx + sx * 20, y1 - m), (bx, y1 - m), (bx, y1 - m - 20)],
               fill=rgb(g["fret"]) + (255,), width=3, joint="curve")
        d.rectangle((bx + sx * 3 - 2, y1 - m + 1, bx + sx * 3 + 2, y1 - m + 5),
                    fill=rgb(g["fret_hi"]) + (255,))
    cx = (x0 + x1) // 2
    d.polygon([(cx, y0 - 2), (cx + 7, y0 + 6), (cx, y0 + 14), (cx - 7, y0 + 6)],
              fill=rgb(g["gold"]) + (255,))
    d.polygon([(cx, y1 - 14), (cx + 7, y1 - 6), (cx, y1 + 2), (cx - 7, y1 - 6)],
              fill=rgb(g["gold"]) + (255,))


def fret_band(d: ImageDraw.ImageDraw, x0: float, x1: float, y: float,
              color: str, unit: int = 9, width: int = 2) -> None:
    """一排小回纹横带（分隔装饰）。"""
    col = rgb(color) + (255,)
    x = x0
    while x + unit * 2.8 <= x1:
        d.line([(x, y + unit * 1.15), (x, y), (x + unit * 1.7, y),
                (x + unit * 1.7, y + unit * 0.62), (x + unit * 0.75, y + unit * 0.62),
                (x + unit * 0.75, y + unit * 0.32)],
               fill=col, width=width, joint="curve")
        x += unit * 2.45


# ---- 祥云 / 龙鳞 / 底纹 ----------------------------------------------------
def cloud_head(d: ImageDraw.ImageDraw, cx: float, cy: float, r: float,
               col: tuple, lw: int = 3) -> None:
    """如意云头：四瓣波浪上沿（外侧小、中间大）+ 瓣下褶 + 云脚。

    纯 PIL 无贝塞尔，用多段圆弧拼：相邻瓣圆心距 < 直径 → 自然连成一朵团云，
    不会散成几个圆（早期版本「三瓣独立圆 + 同心圆卷尾」实测像靶心）。
    """
    for dx, q in ((-0.72, 0.34), (-0.24, 0.46), (0.26, 0.46), (0.74, 0.34)):
        ox, qq = cx + dx * r, q * r
        d.arc((ox - qq, cy - qq, ox + qq, cy + qq), 180, 360, fill=col, width=lw)
    for dx, q in ((-0.48, 0.24), (0.02, 0.30), (0.50, 0.24)):
        ox, qq = cx + dx * r, q * r
        d.arc((ox - qq, cy + 0.06 * r, ox + qq, cy + 0.06 * r + 1.5 * qq), 205, 335,
              fill=col, width=max(2, lw - 1))
    d.arc((cx - 0.86 * r, cy + 0.16 * r, cx + 0.02 * r, cy + 0.56 * r), 6, 174, fill=col, width=lw)
    d.arc((cx - 0.02 * r, cy + 0.16 * r, cx + 0.86 * r, cy + 0.56 * r), 6, 174, fill=col, width=lw)


def cloud_band(layer: Image.Image, x: float, y: float, count: int, unit: float,
               alpha: int, color: str, step: float = 1.85, rise: float = 0.18,
               lw: int = 3) -> None:
    """云带：清晰层 + 柔光层各叠一次，暗底上才看得见。"""
    tmp = Image.new("RGBA", layer.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(tmp, "RGBA")
    base = rgb(color)
    for i in range(count):
        cx = x + i * unit * step
        cy = y - i * unit * rise + (unit * 0.34 if i % 3 == 1 else 0)
        a = int(alpha * (1 - i * 0.10))
        if a < 10:
            break
        cloud_head(d, cx, cy, unit * (1 - i * 0.02), base + (a,), lw)
    layer.alpha_composite(tmp.filter(ImageFilter.GaussianBlur(7)))
    layer.alpha_composite(tmp.filter(ImageFilter.GaussianBlur(1)))


def scale_band(layer: Image.Image, x: float, y: float, cols: int, rows: int,
               unit: float, alpha: int, color: str) -> None:
    """龙鳞 / 水波纹：错行叠瓦的半圆。"""
    d = ImageDraw.Draw(layer, "RGBA")
    col = rgb(color) + (alpha,)
    for r in range(rows):
        for c in range(cols):
            cx = x + c * unit + (unit * 0.5 if r % 2 else 0)
            cy = y + r * unit * 0.60
            d.arc((cx - unit * 0.52, cy - unit * 0.52, cx + unit * 0.52, cy + unit * 0.52),
                  198, 342, fill=col, width=2)


def diag_texture(layer: Image.Image, alpha: int = 10, step: int = 13,
                 color: str = "#C8D2FF") -> None:
    """极淡斜纹底纹，让大片深色区不空。"""
    d = ImageDraw.Draw(layer, "RGBA")
    w, h = layer.size
    col = rgb(color) + (alpha,)
    for k in range(-h, w + h, step):
        d.line((k, 0, k + h, h), fill=col, width=1)


# ============================================================================
# 文字（楷体 / 渐变 / 描边 / 辉光 / 伪粗）
# ============================================================================
def grad_text(c: Canvas, xy, s: str, size: int, top: str, bot: str, *,
              role: str = "kai", anchor: str = "mm", sw: int = 0,
              stroke: str | None = None, glow_col: tuple | None = None,
              fake_bold: int = 0) -> None:
    """描边 + 外发光 + 垂直渐变填充的文字，直接合成到画布。

    楷体本身笔画细，撑不起游戏牌匾，用 ``fake_bold``（四向偏移各画一次）
    让字形外扩 1px 加粗。
    """
    img = img_of(c)
    s = emoji_safe(str(s))
    f = font(size, role)
    bb = f.getbbox(s, anchor=anchor, stroke_width=sw)
    w, h = max(1, bb[2] - bb[0]), max(1, bb[3] - bb[1])
    ox, oy = int(round(xy[0] + bb[0])), int(round(xy[1] + bb[1]))
    if glow_col is not None:
        gcol, gr, gal = glow_col
        pad = gr * 2
        gl = Image.new("RGBA", (w + 2 * pad, h + 2 * pad), (0, 0, 0, 0))
        ImageDraw.Draw(gl).text((pad - bb[0], pad - bb[1]), s, font=f, fill=gcol + (gal,),
                                anchor=anchor, stroke_width=sw + 5, stroke_fill=gcol + (gal,))
        img.alpha_composite(gl.filter(ImageFilter.GaussianBlur(gr)), (ox - pad, oy - pad))
    if stroke:
        dd = ImageDraw.Draw(img)
        scol = rgb(stroke) + (255,)
        dd.text(xy, s, font=f, fill=scol, anchor=anchor, stroke_width=sw, stroke_fill=scol)
        if fake_bold:
            for dx, dy in ((-fake_bold, 0), (fake_bold, 0), (0, -fake_bold), (0, fake_bold)):
                dd.text((xy[0] + dx, xy[1] + dy), s, font=f, fill=scol, anchor=anchor,
                        stroke_width=sw, stroke_fill=scol)
    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).text((-bb[0], -bb[1]), s, font=f, fill=255, anchor=anchor)
    grad = vgrad((w, h), top, bot).convert("RGBA")
    grad.putalpha(mask)
    img.alpha_composite(grad, (ox, oy))


def kai_text(c: Canvas, xy, s: str, size: int, color: str, *, anchor: str = "lm",
             fake_bold: int = 1, sw: int = 0, stroke: str | None = None) -> None:
    """楷体实色字（段名 / 小标题）。默认伪粗 1px，楷体偏细需要。"""
    img = img_of(c)
    s = emoji_safe(str(s))
    f = font(size, "kai")
    dd = ImageDraw.Draw(img)
    col = rgb(color) + (255,)
    if stroke:
        dd.text(xy, s, font=f, fill=col, anchor=anchor, stroke_width=sw,
                stroke_fill=rgb(stroke) + (255,))
    if fake_bold:
        for dx, dy in ((-fake_bold, 0), (fake_bold, 0), (0, -fake_bold), (0, fake_bold)):
            dd.text((xy[0] + dx, xy[1] + dy), s, font=f, fill=col, anchor=anchor,
                    stroke_width=sw, stroke_fill=rgb(stroke) + (255,) if stroke else None)
    dd.text(xy, s, font=f, fill=col, anchor=anchor, stroke_width=sw,
            stroke_fill=rgb(stroke) + (255,) if stroke else None)


# ============================================================================
# 组件
# ============================================================================
#: 牌匾高度与标题区总高（各模板 _measure 必须用这个，禁止自己写数字）
PLAQUE_H = 62
TITLE_H = 30 + PLAQUE_H + 20        # 顶部留白 + 牌匾 + 与内容间距


def title_plaque(c: Canvas, g: dict, y: float, text: str, *, width: int | None = None,
                 font_size: int = 34) -> int:
    """青蓝云纹牌匾 + 楷体渐变标题。``y`` = 牌匾上沿；返回牌匾下沿。"""
    img = img_of(c)
    W = width or c.width
    tw_ = tw(text, font_size, "kai")
    pw = max(300, min(int(tw_ + 128), W - 96))
    ph = PLAQUE_H
    x0 = (W - pw) // 2
    M = 18
    tile = Image.new("RGBA", (pw + M * 2, ph + M * 2), (0, 0, 0, 0))
    ox, oy = M, M
    tile.alpha_composite(vgrad3((pw, ph), g["plaq_top"], g["plaq_mid"], g["plaq_bot"]).convert("RGBA"),
                         (ox, oy))
    # 云纹
    cl = Image.new("RGBA", tile.size, (0, 0, 0, 0))
    cd = ImageDraw.Draw(cl, "RGBA")
    ccol = rgb(g["plaq_cld"])
    cloud_head(cd, ox + 44, oy + ph * 0.36, 16, ccol + (185,), 2)
    cloud_head(cd, ox + pw - 44, oy + ph * 0.36, 16, ccol + (185,), 2)
    cloud_head(cd, ox + pw * 0.5, oy + ph * 0.80, 10, ccol + (120,), 2)
    cd.line((ox + 12, oy + ph - 12, ox + pw - 12, oy + ph - 12), fill=ccol + (90,), width=1)
    tile.alpha_composite(cl)
    # 顶部高光
    hi = Image.new("RGBA", (pw, max(2, ph // 3)), (255, 255, 255, 0))
    hp = hi.load()
    for yy in range(hi.size[1]):
        a = int(52 * (1 - yy / hi.size[1]) ** 1.6)
        for xx in range(pw):
            hp[xx, yy] = (255, 255, 255, a)
    tile.alpha_composite(hi, (ox, oy))
    td = ImageDraw.Draw(tile)
    td.rounded_rectangle((ox, oy, ox + pw - 1, oy + ph - 1), radius=15,
                         outline=rgb(g["fret"]) + (255,), width=4)
    td.rounded_rectangle((ox + 6, oy + 6, ox + pw - 7, oy + ph - 7), radius=11,
                         outline=rgb(g["fret_hi"]) + (255,), width=1)
    for sx, sy, px, py in ((1, 1, ox + 9, oy + 9), (-1, 1, ox + pw - 9, oy + 9),
                           (1, -1, ox + 9, oy + ph - 9), (-1, -1, ox + pw - 9, oy + ph - 9)):
        fret_corner(td, px, py, sx, sy, g["fret_hi"], 5, 2)
    # 外双辉光
    g1 = Image.new("RGBA", tile.size, (0, 0, 0, 0))
    ImageDraw.Draw(g1).rounded_rectangle((ox - 4, oy - 4, ox + pw + 4, oy + ph + 4), radius=18,
                                         outline=ccol + (170,), width=5)
    tile.alpha_composite(g1.filter(ImageFilter.GaussianBlur(9)))
    g2 = Image.new("RGBA", tile.size, (0, 0, 0, 0))
    ImageDraw.Draw(g2).rounded_rectangle((ox - 2, oy - 2, ox + pw + 2, oy + ph + 2), radius=17,
                                         outline=rgb(g["fret_hi"]) + (150,), width=3)
    tile.alpha_composite(g2.filter(ImageFilter.GaussianBlur(3)))
    # 标题（双主题：牌匾字色/描边/辉光读 g）
    tcx, tcy = ox + pw / 2, oy + ph / 2 + 1
    _grad_on(tile, (tcx, tcy), text, font_size, g["title_hi"], g["title_lo"],
             sw=3, stroke=g["title_edge"], glow_col=(rgb(g["title_glow"]), 7, 150), fake_bold=1)
    img.alpha_composite(tile, (x0 - M, int(y) - M))
    # 匾额挂钉
    dd = ImageDraw.Draw(img)
    pcy = y + ph / 2
    for sx in (-1, 1):
        gx = W // 2 + sx * (pw // 2 + 15)
        dd.polygon([(gx, pcy - 9), (gx + 7, pcy), (gx, pcy + 9), (gx - 7, pcy)],
                   fill=rgb(g["gold"]) + (255,))
        dd.polygon([(gx, pcy - 4), (gx + 3, pcy), (gx, pcy + 4), (gx - 3, pcy)],
                   fill=rgb(g["gold_dk"]) + (255,))
    return int(y + ph)


def _grad_on(base: Image.Image, xy, s, size, top, bot, *, role="kai", anchor="mm",
             sw=0, stroke=None, glow_col=None, fake_bold=0):
    """grad_text 的底层实现，直接作用于 Image（供牌匾 tile 用）。"""
    s = emoji_safe(str(s))
    f = font(size, role)
    bb = f.getbbox(s, anchor=anchor, stroke_width=sw)
    w, h = max(1, bb[2] - bb[0]), max(1, bb[3] - bb[1])
    ox, oy = int(round(xy[0] + bb[0])), int(round(xy[1] + bb[1]))
    if glow_col is not None:
        gcol, gr, gal = glow_col
        pad = gr * 2
        gl = Image.new("RGBA", (w + 2 * pad, h + 2 * pad), (0, 0, 0, 0))
        ImageDraw.Draw(gl).text((pad - bb[0], pad - bb[1]), s, font=f, fill=gcol + (gal,),
                                anchor=anchor, stroke_width=sw + 5, stroke_fill=gcol + (gal,))
        base.alpha_composite(gl.filter(ImageFilter.GaussianBlur(gr)), (ox - pad, oy - pad))
    if stroke:
        dd = ImageDraw.Draw(base)
        scol = rgb(stroke) + (255,)
        dd.text(xy, s, font=f, fill=scol, anchor=anchor, stroke_width=sw, stroke_fill=scol)
        if fake_bold:
            for dx, dy in ((-fake_bold, 0), (fake_bold, 0), (0, -fake_bold), (0, fake_bold)):
                dd.text((xy[0] + dx, xy[1] + dy), s, font=f, fill=scol, anchor=anchor,
                        stroke_width=sw, stroke_fill=scol)
    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).text((-bb[0], -bb[1]), s, font=f, fill=255, anchor=anchor)
    grad = vgrad((w, h), top, bot).convert("RGBA")
    grad.putalpha(mask)
    base.alpha_composite(grad, (ox, oy))


def card_shell(c: Canvas, g: dict, title: str, *, top: int = 0,
               width: int | None = None) -> int:
    """卡片外壳一笔画完：底渐变 + 斜纹 + 云纹 + 暗角 + 天光 + 回纹外框 + 牌匾。

    调用方必须在算 ``h`` 之后、画内容之前调用；返回值即内容起始 y。
    注意：模板**不要再自己画卡片圆角底**，本函数已覆盖整幅。
    """
    img = img_of(c)
    W, H = img.size
    inset = 8
    img.alpha_composite(vgrad3((W - 2 * inset, H - 2 * inset), g["card_top"], g["card_mid"],
                              g["card_bot"]).convert("RGBA"), (inset, inset))
    # 斜纹
    tex = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    diag_texture(tex, 10, 13)
    img.alpha_composite(tex)
    # 云纹 / 龙鳞
    wm = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    cloud_band(wm, W - 268, 104, 4, 34, 96, g["cloud"], 1.70, 0.22, 3)
    cloud_band(wm, W - 230, H - 156, 3, 28, 82, g["cloud2"], 1.78, 0.24, 3)
    cloud_band(wm, 56, H - 84, 3, 24, 62, g["cloud2"], 1.86, 0.20, 2)
    scale_band(wm, W - 200, 210, 9, 28, 23, 46, g["cloud"])
    img.alpha_composite(wm)
    # 暗角（双主题：暗角色读 g；浅色风是很淡的紫，不压字）
    vig = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    vd = ImageDraw.Draw(vig, "RGBA")
    vc = rgb(g["vignette"])
    if g is G_LIGHT:
        vd.rectangle((0, 0, W, 88), fill=vc + (18,))
        vd.rectangle((0, H - 108, W, H), fill=vc + (26,))
    else:
        vd.rectangle((0, 0, W, 88), fill=vc + (52,))
        vd.rectangle((0, H - 108, W, H), fill=vc + (72,))
    img.alpha_composite(vig.filter(ImageFilter.GaussianBlur(40)))
    # 顶部天光（双主题：色读 g）
    sc = rgb(g["sky"])
    def sky(d, _c=sc, _a=30 if g is G_LIGHT else 46):
        d.ellipse((W // 2 - 240, -130, W // 2 + 240, 140), fill=_c + (_a,))
    glow(c, sky, 60)
    # 外框
    frame(ImageDraw.Draw(img), (10, 10, W - 11, H - 11), g)
    # 牌匾
    y = 30 if not top else top
    y2 = title_plaque(c, g, y, title, width=width or W)
    return y2 + 20


def sub_line(c: Canvas, g: dict, y: float, text: str, *, size: int = 17) -> int:
    """副标题：居中小字 + 两侧向外淡出的金色饰线。返回下一行 y。"""
    img = img_of(c)
    W = img.size[0]
    s = emoji_safe(str(text))
    if not s:
        return int(y)
    tw_ = tw(s, size, "regular")
    x0 = (W - tw_) / 2
    c.text((x0, y), s, size=size, color=g["dim"], role="regular", anchor="lm")
    amb = Image.new("RGBA", (W, 4), (0, 0, 0, 0))
    ad = ImageDraw.Draw(amb)
    gold = rgb(g["gold"])
    for side in (-1, 1):
        sx = (x0 - 16) if side < 0 else (x0 + tw_ + 16)
        for i in range(78):
            a = int(190 * (1 - i / 78.0) ** 1.5)
            if a:
                ad.point((int(sx + side * i), 1), fill=gold + (a,))
    img.alpha_composite(amb, (0, int(y) - 1))
    return int(y + size + 12)


def panel(c: Canvas, g: dict, box, *, state: str = "done", radius: int = 13,
          watermark: bool = True, outline: str | None = None, fill: str | None = None,
          width: int = 1) -> None:
    """游戏面板：填充 → 面板内祥云/龙鳞水印 → 边框（顺序不能反，否则云纹被盖掉）。"""
    x0, y0, x1, y1 = [int(round(v)) for v in box]
    base = fill or (g["panel_hi"] if state == "current" else g["panel"])
    if state == "locked" and fill is None:
        base = g["ord_lock"]
    c.rounded_rect((x0, y0, x1, y1), radius=radius, fill=base)
    if watermark:
        a = 112 if state == "current" else (84 if state == "done" else 0)
        if a:
            right = (x1 - x0) % 2 == 0

            def paint(layer, dd, w, h, _a=a, _r=right):
                cxx = w - 46 if _r else 46
                cloud_head(dd, cxx, h - 44, 20, rgb(g["cloud"]) + (_a,), 2)
                cloud_head(dd, cxx + (-46 if _r else 46), h - 22, 13,
                           rgb(g["cloud"]) + (int(_a * 0.7),), 2)
                scale_band(layer, w - 176 if _r else 30, 9, 7, 2, 18,
                           int(_a * 0.6), g["cloud"])
            pattern_into(c, (x0 + 1, y0 + 1, x1 - 1, y1 - 1), max(0, radius - 1), paint)
    edge = outline or (g["fret"] if state == "current" else
                       (g["gold_dk"] if state == "done" else g["panel_ln"]))
    c.rounded_rect((x0, y0, x1, y1), radius=radius, outline=edge,
                   width=2 if state == "current" else width)


def ord_badge(c: Canvas, g: dict, cx: float, cy: float, n, state: str = "done",
              r: int = 18) -> None:
    """段序号徽：金（已通关）/ 玫红（可挑战）/ 暗紫（未解锁），带内圈与数字。"""
    if state == "done":
        c.ellipse((cx - r, cy - r, cx + r, cy + r), fill=g["gold"], outline=g["gold_hi"], width=2)
        c.ellipse((cx - r * 0.72, cy - r * 0.72, cx + r * 0.72, cy + r * 0.72),
                  outline=g["gold_dk"], width=1)
        num = g["ord_num"]
    elif state == "current":
        c.ellipse((cx - r, cy - r, cx + r, cy + r), fill=g["fret"], outline=g["fret_hi"], width=2)
        num = "#FFFFFF"
    else:
        c.ellipse((cx - r, cy - r, cx + r, cy + r), fill=g["ord_lock"], outline=g["panel_ln"], width=2)
        num = g["weak"]
    c.text((cx, cy), str(n), size=int(r * 1.05), color=num, role="bold", bold=True, anchor="mm")


def chip(c: Canvas, g: dict, x_right: float, cy: float, text: str, state: str = "done",
         size: int = 15) -> float:
    """状态标签：对勾 / 三角 / 锁点 + 文案。返回标签宽度。"""
    col = g["green"] if state == "done" else (g["fret_hi"] if state == "current" else g["dim"])
    sw_ = tw(text, size, "bold") + 40 if tw(text, size, "bold") + 40 > 76 else 76
    x0 = x_right - sw_
    c.rounded_rect((x0, cy - 13, x_right, cy + 13), radius=9, fill=g["chip_fill"], outline=col, width=1)
    d = ImageDraw.Draw(img_of(c))
    ix = x0 + 11
    icol = rgb(col) + (255,)
    if state == "done":
        d.line([(ix - 1, cy + 1), (ix + 3, cy + 5), (ix + 9, cy - 5)], fill=icol, width=2, joint="curve")
    elif state == "current":
        d.polygon([(ix, cy - 5), (ix + 8, cy), (ix, cy + 5)], fill=icol)
    else:
        d.ellipse((ix + 1, cy - 4, ix + 7, cy + 4), outline=icol, width=1)
    c.text((x0 + 20 + (sw_ - 20) / 2, cy), text, size=size, color=col, role="bold",
           bold=True, anchor="mm")
    return sw_


def bar(c: Canvas, g: dict, box, pct: float, *, kind: str = "xp", mark: bool = False) -> None:
    """游戏风进度条：暗轨 + 渐变填充 + 顶部高光 + 右端亮头。"""
    x0, y0, x1, y1 = [int(round(v)) for v in box]
    h = max(4, y1 - y0)
    c.rounded_rect((x0, y0, x1, y1), radius=h // 2, fill=g["bar_track"], outline=g["panel_ln"], width=1)
    pct = max(0.0, min(1.0, float(pct)))
    fw = int((x1 - x0 - 2) * pct)
    if fw > 1:
        cf, ct = {
            "xp": (g["fret"], g["gold"]),
            "hp": ("#FF8A96", "#DD3E5C"),
            "mp": ("#5EE0D2", "#0C9FB0"),
            "ready": (g["gold_hi"], g["gold"]),
        }.get(kind, (g["fret"], g["gold"]))
        c.gradient_rect((x0 + 1, y0 + 1, x0 + 1 + fw, y1 - 1), cf, ct,
                        radius=max(0, h // 2 - 1), horizontal=True)
        # 顶部高光
        hl = Image.new("RGBA", (max(1, fw), max(1, h // 2)), (0, 0, 0, 0))
        hp = hl.load()
        for yy in range(hl.size[1]):
            a = int(56 * (1 - yy / max(1, hl.size[1])))
            for xx in range(hl.size[0]):
                hp[xx, yy] = (255, 255, 255, a)
        img_of(c).alpha_composite(hl, (x0 + 1, y0 + 1))
    if mark:
        d = ImageDraw.Draw(img_of(c))
        d.polygon([(x1 + 1, y0 - 3), (x1 + 7, y0 + h // 2), (x1 + 1, y1 + 3),
                   (x1 - 5, y0 + h // 2)], fill=rgb(g["fret_hi"]) + (255,))


# ---- 异火火焰徽章 ----------------------------------------------------------
#: 不规则火团轮廓（相对 r）：宽底 + 左右鼓包 + 细长主火舌 + 右小火舌。
#: 参考 D:/AI/doupo 异火图标——不是对称尖三角，而是有机火团。
FLAME_BLOBS = [
    (0.00, 0.52, 1.00, 0.52),
    (-0.36, 0.08, 0.50, 0.46),
    (0.36, 0.08, 0.50, 0.46),
    (0.00, -0.62, 0.34, 0.72),
    (0.34, -1.10, 0.13, 0.30),
]


def flame_badge(c: Canvas, g: dict, cx: float, cy: float, r: float, state: str = "done") -> None:
    """异火徽章：外焰独占火舌轮廓（纯色不叠层），火心为下部圆团。

    关键：火心只取前 3 个 blob 并缩小下移；整轮廓等比缩放会在火舌尖形成
    难看的阶梯（早期版本实测像「岩浆蛋糕」）。
    """
    ring, outer, mid, inner, core = {
        "done":    (g["fret_hi"], g["flame_done_outer"], g["flame_done_mid"], "#FFC234", "#FFFCD6"),
        "current": (g["fret_hi"], "#7E28D6", "#C63AB2", "#FA7A5C", "#FFF7CC"),
        "locked":  (g["panel_ln"], g["flame_lock_outer"], g["flame_lock_mid"], "#6E6892", "#928CB4"),
    }[state]
    d = ImageDraw.Draw(img_of(c))
    for bx, by, bw, bh in FLAME_BLOBS:
        k = 1.10
        ex, ey = cx + bx * r * k, cy + by * r * k + r * 0.02
        d.ellipse((ex - bw * r * k, ey - bh * r * k, ex + bw * r * k, ey + bh * r * k),
                  fill=rgb(ring) + (255,))
    for bx, by, bw, bh in FLAME_BLOBS:
        ex, ey = cx + bx * r, cy + by * r
        d.ellipse((ex - bw * r, ey - bh * r, ex + bw * r, ey + bh * r), fill=rgb(outer) + (255,))
    for col, k, oy in ((mid, 0.64, 0.11), (inner, 0.38, 0.20)):
        for bx, by, bw, bh in FLAME_BLOBS[:3]:
            ex, ey = cx + bx * r * k, cy + by * r * k + oy * r
            d.ellipse((ex - bw * r * k, ey - bh * r * k, ex + bw * r * k, ey + bh * r * k),
                      fill=rgb(col) + (255,))
    d.ellipse((cx - r * 0.25, cy + r * 0.44, cx + r * 0.25, cy + r * 0.88), fill=rgb(core) + (255,))


def flame_halo(c: Canvas, g: dict, cx: float, cy: float, r: float, state: str = "done") -> None:
    """火焰底光斑（先于徽章绘制）。"""
    col = {"done": "#FF922C", "current": "#BA56F4", "locked": "#58527E"}[state]
    a = 118 if state != "locked" else 22
    cc = rgb(col)

    def paint(d):
        d.ellipse((cx - r * 2.1, cy - r * 1.5, cx + r * 2.1, cy + r * 1.9), fill=cc + (a,))
        d.ellipse((cx - r * 1.3, cy - r * 1.0, cx + r * 1.3, cy + r * 1.4), fill=cc + (a,))
    glow(c, paint, 15)


def flame_row(c: Canvas, g: dict, y_flame: float, done: int, total: int,
              *, x0: float | None = None, x1: float | None = None,
              r: float = 20) -> int:
    """进度火焰条 + 底部序号 + 金色短横。返回数字行下沿。"""
    W = img_of(c).size[0]
    x0 = 36 if x0 is None else x0
    x1 = (W - 36) if x1 is None else x1
    cell = (x1 - x0 - 24) / max(1, total)
    xs = [x0 + 12 + cell * (i + 0.5) for i in range(total)]
    for i in range(total):
        st = "done" if i < done else ("current" if i == done else "locked")
        flame_halo(c, g, xs[i], y_flame, r, st)
    d = ImageDraw.Draw(img_of(c))
    y_digit = y_flame + r * 1.18 + 16
    for i in range(total):
        st = "done" if i < done else ("current" if i == done else "locked")
        flame_badge(c, g, xs[i], y_flame, r, st)
        d = ImageDraw.Draw(img_of(c))
        col = g["gold"] if st == "done" else (g["fret_hi"] if st == "current" else g["weak"])
        c.text((xs[i], y_digit), str(i + 1), size=15, color=col, role="bold", bold=True, anchor="lm")
        if st == "done":
            d.line((xs[i] - 9, y_digit + 11, xs[i] + 9, y_digit + 11),
                   fill=rgb(g["gold_hi"]) + (255,), width=2)
        elif st == "current":
            d.line((xs[i] - 9, y_digit + 11, xs[i] + 9, y_digit + 11),
                   fill=rgb(g["fret_hi"]) + (255,), width=1)
    return int(y_digit + 16)


def divider(c: Canvas, g: dict, y: float, x0: float, x1: float,
            style: str = "gold") -> None:
    """分隔线：gold=金菱在中间 / band=回纹横带 / thin=细暗线。"""
    d = ImageDraw.Draw(img_of(c))
    if style == "band":
        half = (x1 - x0 - 90) / 2
        fret_band(d, x0 + 8, x0 + 8 + half, y - 9, g["fret_dk"], 9, 2)
        fret_band(d, x1 - 8 - half, x1 - 8, y - 9, g["fret_dk"], 9, 2)
        cx = (x0 + x1) / 2
        d.polygon([(cx, y - 7), (cx + 8, y), (cx, y + 7), (cx - 8, y)],
                  fill=rgb(g["fret"]) + (255,))
        d.polygon([(cx, y - 3), (cx + 3, y), (cx, y + 3), (cx - 3, y)],
                  fill=rgb(g["gold_hi"]) + (255,))
    elif style == "thin":
        d.line((x0, y, x1, y), fill=rgb(g["panel_ln"]) + (255,), width=1)
    else:
        d.line((x0, y, x1, y), fill=rgb(g["fret_dk"]) + (255,), width=1)
        cx = (x0 + x1) / 2
        d.polygon([(cx, y - 4), (cx + 5, y), (cx, y + 4), (cx - 5, y)],
                  fill=rgb(g["gold"]) + (255,))


# ============================================================================
# boxes 自检
# ============================================================================
class Boxes:
    """包围盒收集器：溢出 / 重叠 / 贴边三类自检。

    用法::

        bx = Boxes()
        bx.text("title", (x, y), "文案", 30, "kai")
        bx.panel("seg0", (x0, y0, x1, y1))
        bx.check()      # 打印/返回问题列表
    """

    def __init__(self) -> None:
        self.texts: list[dict] = []
        self.panels: list[dict] = []

    def text(self, label: str, pos, s: str, size: int, role: str = "regular",
             anchor: str = "lm") -> None:
        s = str(s or "")
        w = tw(s, size, role)
        h = int(size)
        x, y = pos
        if anchor == "lm":
            box = (x, y - h / 2, x + w, y + h / 2)
        elif anchor == "rm":
            box = (x - w, y - h / 2, x, y + h / 2)
        elif anchor == "mm":
            box = (x - w / 2, y - h / 2, x + w / 2, y + h / 2)
        elif anchor == "ra":
            box = (x - w, y, x, y + h)
        else:
            box = (x, y, x + w, y + h)
        self.texts.append({"label": label, "s": s, "box": box})

    def panel(self, label: str, box) -> None:
        self.panels.append({"label": label, "box": tuple(box)})

    def check(self, min_margin: int = MIN_MARGIN, width: int | None = None,
              height: int | None = None) -> list[str]:
        probs: list[str] = []
        margins: list[tuple[float, str]] = []
        for t in self.texts:
            x0, y0, x1, y1 = t["box"]
            if width is not None and (x0 < 4 or x1 > width - 4):
                probs.append("越界X %s %.0f..%.0f" % (t["label"], x0, x1))
            if height is not None and (y0 < 4 or y1 > height - 4):
                probs.append("越界Y %s %.0f..%.0f" % (t["label"], y0, y1))
            for pn in self.panels:
                px0, py0, px1, py1 = pn["box"]
                if px0 - 1 <= x0 and x1 <= px1 + 1 and py0 - 1 <= y0 and y1 <= py1 + 1:
                    m = min(y0 - py0, py1 - y1, x0 - px0, px1 - x1)
                    margins.append((round(m, 1), t["label"]))
                    if m < min_margin:
                        probs.append("贴边 %.1fpx %s in %s" % (m, t["label"], pn["label"]))
        for i in range(len(self.texts)):
            for j in range(i + 1, len(self.texts)):
                a, b = self.texts[i]["box"], self.texts[j]["box"]
                ox = min(a[2], b[2]) - max(a[0], b[0])
                oy = min(a[3], b[3]) - max(a[1], b[1])
                if ox > 1 and oy > 1:
                    probs.append("重叠 %s × %s" % (self.texts[i]["label"], self.texts[j]["label"]))
        margins.sort()
        self.min_margin = margins[0][0] if margins else None
        return probs
