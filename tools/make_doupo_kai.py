# -*- coding: utf-8 -*-
"""把系统楷体子集化为「斗破牌匾字」（doupo_kai.ttf）。

为什么需要
----------
12 张斗破卡要用**楷体**做牌匾标题 / 段名（游戏本体 UI 的字），但
``Canvas.text(role=...)`` 只会按 role 推断字重、真选字体的是 ``theme.font_family``，
而生产机（postmarketOS / Alpine）**没有中文字体**。所以必须自带一个子集。

做法
----
字符集 = GB2312 全集 ∪ 仓库里 doupo 相关源码/数据出现的全部非 ASCII ∪ ASCII ∪
中文标点，共 7445 字 → **汉字零缺字**（缺的 76 个是 emoji，simkai 本来就没有）。
验证用 ``.notdef`` 参照法：拿一个必然不存在的码位（U+0378）的位图当凭证，
逐字比对，命中即为缺字。

产物大小
--------
约 2.44MB，**超过仓库 200KB 单文件上传红线**（2026-11-16 用户要求），因此
``doupo_kai.ttf`` 不进 git：本机/生产机各放一份即可。缺文件时
``fonts.font_path("kai")`` 自动回退等线 Bold（卡片不会崩，只是标题没楷体味）。

用法
----
    python tools/make_doupo_kai.py          # Windows：读 C:/Windows/Fonts/simkai.ttf
    SIMKAI=/path/to/simkai.ttf python tools/make_doupo_kai.py

拷到生产机：
    scp src/plugins/qq_bridge/pic/templates/assets/doupo_fonts/doupo_kai.ttf \
        pmos@192.168.0.50:~/qq_bridge/src/plugins/qq_bridge/pic/templates/assets/doupo_fonts/
"""
import io, os, sys, json, glob

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.environ.get("SIMKAI") or "C:/Windows/Fonts/simkai.ttf"
OUTDIR = os.path.join(ROOT, "src/plugins/qq_bridge/pic/templates/assets/doupo_fonts")
OUT = os.path.join(OUTDIR, "doupo_kai.ttf")

# ---- 1. GB2312 全集 ----
chars = set()
for hi in range(0xA1, 0xF8):
    for lo in range(0xA1, 0xFF):
        try:
            chars.add(bytes([hi, lo]).decode("gb2312"))
        except Exception:
            pass
print("GB2312:", len(chars))

# ---- 2. 仓库中斗破相关文本 ----
scan = []
for pat in ("src/plugins/doupo/**/*.py",
            "src/plugins/qq_bridge/pic/templates/doupo*.py",
            "src/plugins/qq_bridge/pic/templates/_shared.py",
            "src/plugins/qq_bridge/pic/*.py"):
    scan += glob.glob(os.path.join(ROOT, pat), recursive=True)
for f in scan:
    try:
        t = open(f, encoding="utf-8").read()
    except Exception:
        continue
    chars.update(ch for ch in t if not ch.isascii())

for pat in ("data/doupo/*.json", "src/plugins/doupo/data/*.json"):
    for f in glob.glob(os.path.join(ROOT, pat)):
        try:
            t = open(f, encoding="utf-8").read()
        except Exception:
            continue
        chars.update(ch for ch in t if not ch.isascii())
print("with repo:", len(chars))

# ---- 3. ASCII + 标点 ----
chars.update(chr(c) for c in range(0x20, 0x7F))
chars.update("　·—…「」『』【】《》（）〈〉、。，；：？！～￥％＋－×÷≥≤·•　")
chars.discard("\n"); chars.discard("\r"); chars.discard("\t")
print("total chars:", len(chars))

# ---- 4. 子集化 ----
from fontTools import subset
opts = subset.Options()
opts.layout_features = ["*"]
opts.name_IDs = ["*"]
opts.notdef_outline = True
opts.recalc_bounds = True
opts.drop_tables += ["EBDT", "EBLC", "SVG "]
font = subset.load_font(SRC, opts)
sub = subset.Subsetter(options=opts)
sub.populate(text="".join(sorted(chars)))
sub.subset(font)
os.makedirs(OUTDIR, exist_ok=True)
subset.save_font(font, OUT, opts)
print("OUT", OUT, os.path.getsize(OUT), "bytes")

# ---- 5. 零缺字验证（.notdef 参照）----
from PIL import ImageFont
f = ImageFont.truetype(OUT, 40)
probe = bytes(f.getmask("\u0378"))
miss = [ch for ch in sorted(chars) if ord(ch) > 0x2000 and bytes(f.getmask(ch)) == probe]
print("MISSING", len(miss), "".join(miss[:60]))
print("OK" if not miss else "FAIL")
