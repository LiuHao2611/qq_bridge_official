# -*- coding: utf-8 -*-
"""仿制「斗破老游戏点阵字」像素字体（doupo_pixel.ttf）。

为什么仿制而不搬游戏字库
------------------------
游戏解包字库 ``D:\\AI\\doupo\\u\\font.hy`` 只有 1090 个字模，斗技卡文案要 1356 个
汉字，直接搬会缺 266 字变豆腐块。按用户 2026-11-27 定案：**仿制**——用基础黑体
把每个字形栅格化成 16x16 点阵，再逐行合并矩形重建轮廓，得到方硬边、带网格感的
像素 TTF。全量覆盖、零缺字，且视觉对齐老游戏机（FC/GBA）中文点阵。

点阵参数
--------
* GRID=16：12x12 太疏（笔画断裂），16x16 笔画完整且仍明显像素化（肉眼验收）。
* 阈值 THRESH=95：保留细笔画（sRGB 抗锯齿灰阶到 95 即算笔画），不糊成一团。
* 每像素合并成水平矩形（rects），显著减少轮廓点数，TTF 更小、渲染更稳。

接入方式
--------
产物放 ``src/plugins/qq_bridge/pic/templates/assets/doupo_fonts/doupo_pixel.ttf``：
与 doupo_bold.ttf / doupo_kai.ttf 同目录，属**随源码部署的内置资产**。
不能放 ``data/fonts``——那是 phone_deploy.py 的 FORBIDDEN_PREFIXES，永远推不到手机。
字体族「DoupoPixel」由 ``fonts.load_family`` 经 ``_BUNDLED_PIXEL`` 兜底加载；
斗技卡 Theme 指定 ``font_family="DoupoPixel"`` 即可让卡上所有文字走像素字。
``PIC_FONT_DIR`` 环境变量可覆盖输出目录（本地换字体实验用）。

用法
----
    python tools/make_doupo_pixel.py
    python tools/make_doupo_pixel.py --check     # 只校验现有产物零缺字
"""
from __future__ import annotations

import argparse
import glob
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE = os.environ.get("DOUPO_PIXEL_BASE") or "C:/Windows/Fonts/simhei.ttf"
OUTDIR = os.environ.get("PIC_FONT_DIR") or os.path.join(
    ROOT, "src", "plugins", "qq_bridge", "pic", "templates", "assets", "doupo_fonts")
OUT = os.path.join(OUTDIR, "doupo_pixel.ttf")
FAMILY = "DoupoPixel"

GRID = 16       # 点阵分辨率
THRESH = 95     # 二值化阈值（0-255）
UPM = 1000      # TTF units-per-em
STEP = UPM // GRID


def collect_chars() -> set[str]:
    """字符集 = 斗破源码/数据出现的全部字符 ∪ GB2312 ∪ ASCII ∪ 中文标点。"""
    chars: set[str] = set()
    patterns = (
        "src/plugins/doupo/**/*.py",
        "src/plugins/qq_bridge/pic/templates/doupo*.py",
        "data/doupo/*.json",
        "src/plugins/doupo/data/*.json",
    )
    for pat in patterns:
        for path in glob.glob(os.path.join(ROOT, pat), recursive=True):
            try:
                chars.update(open(path, encoding="utf-8").read())
            except (OSError, UnicodeDecodeError):
                continue
    # GB2312 全集：保证玩家昵称/任意输入也不缺字
    for hi in range(0xA1, 0xF8):
        for lo in range(0xA1, 0xFF):
            try:
                chars.add(bytes((hi, lo)).decode("gb2312"))
            except UnicodeDecodeError:
                pass
    chars.update(chr(c) for c in range(0x20, 0x7F))
    chars.update("　·—…「」『』【】《》（）〈〉、。，；：？！～￥％＋－×÷≥≤•★☆")
    # 基础黑体个别 GB2312 符号缺失：￣ 用全角横线 — 顶替，避免豆腐块
    chars.discard("￣")
    for c in "\n\r\t":
        chars.discard(c)
    return chars


def glyph_rects(ch: str, base_font) -> list[tuple[int, int, int, int]]:
    """把一个字形栅格化成点阵，再把每行连续像素合成矩形。

    中文/全角 = GRID×GRID 方格；ASCII 半角 = (GRID//2)×GRID，宽度只占半格——
    否则等宽像素字会把「斗气 17%」排成「斗气 1 7 ％」过于松散。
    """
    from PIL import Image, ImageDraw

    half = " " <= ch <= "~"
    w = GRID // 2 if half else GRID
    im = Image.new("L", (w, GRID), 0)
    ImageDraw.Draw(im).text((0, -1), ch, font=base_font, fill=255)
    px = im.load()
    rects: list[tuple[int, int, int, int]] = []
    for y in range(GRID):
        x = 0
        while x < w:
            if px[x, y] >= THRESH:
                x2 = x
                while x2 + 1 < w and px[x2 + 1, y] >= THRESH:
                    x2 += 1
                rects.append((x, y, x2 + 1, y + 1))
                x = x2 + 1
            else:
                x += 1
    return rects, w


def build(chars: set[str]) -> str:
    from PIL import ImageFont
    from fontTools.fontBuilder import FontBuilder
    from fontTools.pens.ttGlyphPen import TTGlyphPen

    base_font = ImageFont.truetype(BASE, GRID)
    ordered = sorted(chars)
    names = [".notdef"] + ["g%05d" % i for i in range(1, len(ordered) + 1)]

    fb = FontBuilder(UPM, isTTF=True)
    fb.setupGlyphOrder(names)
    cmap: dict[int, str] = {0: ".notdef"}
    for i, ch in enumerate(ordered):
        cmap[ord(ch)] = names[i + 1]
    fb.setupCharacterMap(cmap)

    glyfs: dict[str, object] = {}
    advances: dict[str, int] = {".notdef": UPM}
    pen = TTGlyphPen(None)
    glyfs[".notdef"] = pen.glyph()
    for i, ch in enumerate(ordered):
        pen = TTGlyphPen(None)
        rects, cw = glyph_rects(ch, base_font)
        for (x0, y0, x1, y1) in rects:
            X0, Y0 = x0 * STEP, (GRID - y1) * STEP
            X1, Y1 = x1 * STEP, (GRID - y0) * STEP
            pen.moveTo((X0, Y0))
            pen.lineTo((X1, Y0))
            pen.lineTo((X1, Y1))
            pen.lineTo((X0, Y1))
            pen.closePath()
        glyfs[names[i + 1]] = pen.glyph()
        advances[names[i + 1]] = cw * STEP

    fb.setupGlyf(glyfs)
    fb.setupHorizontalMetrics({n: (advances[n], 0) for n in names})
    fb.setupHorizontalHeader(ascent=UPM, descent=0)
    fb.setupNameTable({"familyName": FAMILY, "styleName": "Regular"})
    fb.setupOS2(sTypoAscender=UPM, sTypoDescender=0)
    fb.setupPost(formatType=3)  # 不存字形名 → 避免中文名 latin-1 编码失败
    fb.setupMaxp()

    os.makedirs(OUTDIR, exist_ok=True)
    fb.save(OUT)
    return OUT


def check(path: str, chars: set[str]) -> bool:
    """零缺字验证：字形位图不等于 .notdef 即视为有字形。"""
    from PIL import Image, ImageFont

    font = ImageFont.truetype(path, 20)

    def mask(ch: str) -> bytes:
        # ImagingCore 无 tobytes()：paste 进固定 L 图再取字节（项目既有做法）
        core = font.getmask(ch)
        im = Image.new("L", (32, 32), 0)
        im.paste(core, (0, 0, core.size[0], core.size[1]))
        return im.tobytes()

    notdef = mask("\u0378")
    # 空白类字符（全角空格等）本就无可见字形，不计缺字
    missing = [c for c in sorted(chars)
               if ord(c) > 0x2000 and not c.isspace() and mask(c) == notdef]
    print("MISSING", len(missing), "".join(missing[:80]))
    if missing:
        print("FAIL")
        return False
    print("OK")
    return True


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="只校验现有产物")
    args = ap.parse_args()
    chars = collect_chars()
    print("BASE", BASE)
    print("CHARS", len(chars))
    if args.check:
        if not os.path.isfile(OUT):
            print("NO FONT", OUT)
            return 1
        return 0 if check(OUT, chars) else 1
    out = build(chars)
    print("OUT", out, os.path.getsize(out), "bytes")
    return 0 if check(out, chars) else 1


if __name__ == "__main__":
    sys.exit(main())
