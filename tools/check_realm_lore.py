# -*- coding: utf-8 -*-
"""tools/check_realm_lore.py —— 境界描述接入门禁（2026-11-27）。

守用户感受得到的行为：
  * 13 阶描述一一对应 config.REALM_NAMES，不漏档、不多档；
  * 玩家可见文案不出现「原著 / 百科 / 设定 / 考据 / 章号」等开发向元说明；
  * 人物卡图片、人物卡文本兜底都真的取 realm_lore.desc_of，不是各写一份；
  * 描述在卡上折行而不截断，且与「境界修为」标签、修为条之间不重叠不溢出；
  * 反例（删档 / 塞禁词 / 改回 `_fit` 截断 / 拆接线 / 压扁行高）必须被拦。

用法：
    python tools/check_realm_lore.py             # 报告
    python tools/check_realm_lore.py --check     # 违规 exit 1（交付前必跑）
    python tools/check_realm_lore.py --selftest  # 只跑反例注入
"""
from __future__ import annotations

import re
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOUPO_DIR = ROOT / "src" / "plugins" / "doupo"
PIC_DIR = ROOT / "src" / "plugins" / "qq_bridge"
for _p in (str(PIC_DIR), str(ROOT / "tools")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# 以轻量包方式加载 config / realm_lore：避开 doupo/__init__.py 的 nonebot 注册副作用。
if "doupo" not in sys.modules:
    _pkg = types.ModuleType("doupo")
    _pkg.__path__ = [str(DOUPO_DIR)]
    sys.modules["doupo"] = _pkg

from pic import theme                                  # noqa: E402
from pic.templates import doupo as T                   # noqa: E402
from doupo import config as cfg                        # noqa: E402
from doupo import realm_lore as lore                   # noqa: E402

LORE_SRC = DOUPO_DIR / "realm_lore.py"
CARD_SRC = DOUPO_DIR / "card.py"
CMD_SRC = DOUPO_DIR / "commands.py"
TPL_SRC = PIC_DIR / "pic" / "templates" / "doupo.py"
WIDTHS = (560, 720, 900)
MIN_GAP = 8


# --------------------------------------------------------------- check_boxes ----
def _overlap_2d(a, b) -> bool:
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    return ax0 < bx1 and bx0 < ax1 and ay0 < by1 and by0 < ay1


def check_boxes(bounds, boxes, min_gap: int = MIN_GAP) -> list[str]:
    """card-design 同款自检：溢出 / 两方向重叠 / 同行最小间距。"""
    bx0, by0, bx1, by1 = bounds
    out: list[str] = []
    for label, x0, y0, x1, y1 in boxes:
        if x0 < bx0 - 1 or y0 < by0 - 1 or x1 > bx1 + 1 or y1 > by1 + 1:
            out.append(f"[溢出] {label} ({x0},{y0},{x1},{y1}) bounds={bounds}")
    for i in range(len(boxes)):
        for j in range(i + 1, len(boxes)):
            la, a = boxes[i][0], boxes[i][1:]
            lb, b = boxes[j][0], boxes[j][1:]
            if _overlap_2d(a, b):
                out.append(f"[重叠] {la} <-> {lb}: {a} vs {b}")
            if a[1] < b[3] and b[1] < a[3]:
                gap_x = b[0] - a[2] if a[0] <= b[0] else a[0] - b[2]
                if 0 <= gap_x < min_gap:
                    out.append(f"[间距] {la} <-> {lb} 水平 {gap_x}px < {min_gap}px")
    return out


# ------------------------------------------------------------------ 数据契约 ----
def key_problems(desc: dict[str, str]) -> list[str]:
    names = list(cfg.REALM_NAMES)
    out: list[str] = []
    if len(desc) != len(names):
        out.append(f"[键集] 境界描述 {len(desc)} 档，应为 {len(names)} 档")
    missing = [n for n in names if n not in desc]
    extra = [n for n in desc if n not in names]
    if missing:
        out.append(f"[键集] 缺档: {missing}")
    if extra:
        out.append(f"[键集] 多档: {extra}")
    if list(desc) != names:
        out.append("[键集] 顺序与 REALM_NAMES 不一致（卡面/兜底可能取错档）")
    return out


FORBIDDEN = ("原著", "百科", "设定", "考据", "游戏自设", "自设", "章节", "注脚")


def desc_problems(desc: dict[str, str]) -> list[str]:
    out: list[str] = []
    for name, text in desc.items():
        s = str(text)
        for word in FORBIDDEN:
            if word in s:
                out.append(f"[文案] {name} 含开发向词「{word}」: {s}")
        if re.search(r"第.{0,8}章", s):
            out.append(f"[文案] {name} 写了章号: {s}")
        if not (24 <= len(s) <= 80):
            out.append(f"[文案] {name} 长度 {len(s)} 不在 24~80: {s}")
    return out


# ------------------------------------------------------------------ 接线契约 ----
def _func_block(src: str, name: str) -> str:
    m = re.search(r"^def " + re.escape(name) + r"\(.*?(?=^\S|\Z)", src,
                  re.M | re.S)
    return m.group(0) if m else ""


def source_problems(lore_src: str, card_src: str, cmd_src: str,
                    tpl_src: str) -> list[str]:
    out: list[str] = []
    if "REALM_DESC" not in lore_src or "def desc_of" not in lore_src:
        out.append("[真源] realm_lore.py 缺 REALM_DESC / desc_of")
    if '"realm_desc": realm_lore.desc_of(lv)' not in card_src:
        out.append("[图片] card.build_data 没把 realm_desc 传给模板")
    if "realm_lore.desc_of(lv)" not in cmd_src:
        out.append("[文本] commands.cmd_panel 没取 realm_lore.desc_of(lv)")
    block = _func_block(tpl_src, "_realm_desc_lines")
    if not block:
        out.append("[模板] _realm_desc_lines 不存在（测高与绘制会各算一套）")
    else:
        if '.get("realm_desc")' not in block:
            out.append("[模板] 描述折行函数没消费 data.realm_desc")
        if "S.wrap_cjk" not in block:
            out.append("[模板] 描述没走 S.wrap_cjk 折行")
        if "_fit(" in block:
            out.append("[模板] 描述又用 _fit 截断了（红线：不许吞字）")
    if "_realm_desc_extra_h(data, content_w)" not in tpl_src:
        out.append("[模板] _measure 没把描述高度算进去（会裁切）")
    if "_realm_desc_lines(data, content_w)" not in tpl_src:
        out.append("[模板] build 没走唯一折行入口（高度与绘制会漂移）")
    if "h = _measure(data, width)" not in tpl_src:
        out.append("[模板] build 测高没传 width（窄屏折行会算错高度）")
    return out


# ------------------------------------------------------------------ 排版自检 ----
def sample_data(lv: int, desc: str) -> dict:
    return {
        "name": "萧炎", "lv": lv, "avatar_caption": "持剑少年",
        "realm": cfg.full_realm(lv), "realm_desc": desc,
        "exp": 120, "need": 300, "realm_max": False, "can_break": False,
        "alch_name": "二品炼药师", "alch_exp": 40, "alch_need": 200,
        "weapon_name": "玄重尺", "weapon_level": "3",
        "weapon_stats": {"力量": 180, "敏捷": 60, "斗气": 90, "防御": 50},
        "stats": {"力量": 320, "敏捷": 140, "斗气": 260,
                  "防御": 180, "机缘": 75, "体力": 420},
        "hp": 420, "hp_max": 420, "mp": 260, "mp_max": 260,
        "gold": 12800, "stone": 6, "power": 980, "wins": 12, "losses": 3,
    }


def layout_problems(samples=None, widths=WIDTHS) -> list[str]:
    out: list[str] = []
    samples = samples or [
        (cfg.realm_lv(i), n, lore.REALM_DESC[n])
        for i, n in enumerate(cfg.REALM_NAMES)
    ]
    for lv, name, desc in samples:
        data = sample_data(lv, desc)
        for w in widths:
            tag = f"{name}@w{w}"
            lines = T._realm_desc_lines(data, w - T.PAD * 2)
            if "".join(lines) != desc:
                out.append(f"[吞字] {tag} 折行丢字: {lines}")
            if any("…" in ln for ln in lines):
                out.append(f"[截断] {tag} 出现省略号: {lines}")
            boxes: list[tuple] = []
            c = T.build(theme.get("light"), data=data, width=w, boxes=boxes)
            if c.height != T._measure(data, w):
                out.append(f"[测高] {tag} build={c.height} measure={T._measure(data, w)}")
            got = [b for b in boxes if b[0].startswith("realm_desc")]
            if len(got) != len(lines):
                out.append(f"[登记] {tag} 描述行 {len(lines)}，包围盒 {len(got)}")
            out += [f"[盒-{tag}] {p}" for p in check_boxes(
                (T.PAD, T.PAD, w - T.PAD, c.height - T.PAD), boxes)]
            label = next((b for b in boxes if b[0] == "realm.label"), None)
            bar = next((b for b in boxes if b[0] == "realm.bar"), None)
            if not label or not got or not bar:
                out.append(f"[登记] {tag} 缺 label/desc/bar 包围盒")
                continue
            gap_top = got[0][2] - label[4]
            gap_bottom = bar[2] - got[-1][4]
            if gap_top < MIN_GAP:
                out.append(f"[间距] {tag} 标签→描述 {gap_top}px < {MIN_GAP}px")
            if gap_bottom < MIN_GAP:
                out.append(f"[间距] {tag} 描述→修为条 {gap_bottom}px < {MIN_GAP}px")
    return out


# ------------------------------------------------------------------ 反例注入 ----
def selftest() -> list[tuple[str, bool]]:
    lore_src = LORE_SRC.read_text(encoding="utf-8")
    card_src = CARD_SRC.read_text(encoding="utf-8")
    cmd_src = CMD_SRC.read_text(encoding="utf-8")
    tpl_src = TPL_SRC.read_text(encoding="utf-8")
    res: list[tuple[str, bool]] = []

    bad_keys = dict(lore.REALM_DESC)
    bad_keys.pop("斗宗")
    res.append(("删掉一档" , bool(key_problems(bad_keys))))
    bad_text = dict(lore.REALM_DESC)
    bad_text["斗宗"] = "据百科所载，原著第 300 章有此设定。"
    res.append(("塞入原著/章号禁词", bool(desc_problems(bad_text))))

    mutated = tpl_src.replace(
        'return S.wrap_cjk(desc, REALM_DESC_FS, "regular", content_w)',
        'return [_fit(desc, content_w, REALM_DESC_FS, "regular")]')
    res.append(("改回 _fit 截断", bool(source_problems(
        lore_src, card_src, cmd_src, mutated))))
    res.append(("拆掉图片接线", bool(source_problems(
        lore_src, card_src.replace('"realm_desc": realm_lore.desc_of(lv),', ""),
        cmd_src, tpl_src))))
    res.append(("拆掉文本接线", bool(source_problems(
        lore_src, card_src, cmd_src.replace("realm_lore.desc_of(lv)", ""),
        tpl_src))))
    res.append(("模板不消费 realm_desc", bool(source_problems(
        lore_src, card_src, cmd_src,
        tpl_src.replace('.get("realm_desc")', '.get("wrong_key")')))))

    saved = T.REALM_DESC_ROW_H
    T.REALM_DESC_ROW_H = 8     # 压扁行距：描述会压到修为条
    try:
        sample = [(cfg.realm_lv(8), "斗尊",
                   "空间于掌心开合：撕裂、穿梭、绞杀，虫洞可建，风暴不惧。"
                   "斗气凝晶，肉身不灭。")]
        res.append(("压扁描述块行高", bool(layout_problems(sample, widths=(560,)))))
    finally:
        T.REALM_DESC_ROW_H = saved
    return res


# --------------------------------------------------------------------- main ----
def main() -> int:
    kp = key_problems(lore.REALM_DESC)
    dp = desc_problems(lore.REALM_DESC)
    sp = source_problems(
        LORE_SRC.read_text(encoding="utf-8"),
        CARD_SRC.read_text(encoding="utf-8"),
        CMD_SRC.read_text(encoding="utf-8"),
        TPL_SRC.read_text(encoding="utf-8"),
    )
    lp = layout_problems()
    st = selftest()
    problems = kp + dp + sp + lp + [
        f"[selftest] 反例没被拦住: {n}" for n, ok in st if not ok
    ]
    print(f"境界描述门禁：{'PASS' if not problems else 'FAIL（%d 项）' % len(problems)}")
    print(f"键集：{len(lore.REALM_DESC)}/{len(cfg.REALM_NAMES)} 阶；"
          f"玩家可见禁词：{len(dp)}；接线：{len(sp)}；"
          f"排版盒 {len(cfg.REALM_NAMES)} 阶 × {len(WIDTHS)} 宽：{len(lp)} 项违规")
    print(f"反例注入：{sum(1 for _, ok in st if ok)}/{len(st)} 被拦")
    for name, ok in st:
        print("  %s %s" % ("OK  " if ok else "MISS", name))
    for p in problems:
        print("  " + p)
    if "--selftest" in sys.argv:
        return 0 if all(ok for _, ok in st) else 1
    if "--check" in sys.argv and problems:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
