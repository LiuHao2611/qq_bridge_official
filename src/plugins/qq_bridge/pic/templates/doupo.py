# -*- coding: utf-8 -*-
"""pic/templates/doupo.py —— 斗破苍穹·双帝之战 · 人物状态卡。

用于「人物 / 属性 / 面板」的卡片化回复，替代原先的多行纯文本消息。

版式（浅色羊皮纸 + 赤金朱红玄幻观感，与 xiuxian.py 同一套 Rabbit 极简排版范式）：
  * 顶部居中标题「斗破大陆」+ sword 细描边图标 + 贯通分割线；
  * 左侧 96×96 圆角**静态头像**（用户 2026-11-07 要求：不再单独发动态小人），
    右侧角色名 + 境界 chip + 「LV.x · 形象」说明；资源缺失时画剑形占位；
  * 境界行（境界名 左 / 修为 x/y 右）+ 金→朱红渐变进度条；
    修为溢出（exp >= need，可突破）时：进度条转**暖金** + 右侧标注「· 可突破」；
  * 炼药师品阶 pill（药青，淡绿）；
  * 武器行：sword 图标 + 武器名 + 显示等级 + 四围加成（力量/敏捷/斗气/防御）；
  * 六维网格 3×2：力量 / 敏捷 / 斗气 / 防御 / 机缘 / 体力（数值大、标签小）；
  * 气血条 / 斗气条（红 / 蓝渐变，右侧标 n/n）；
  * 底部一行：金币 / 炎星石 / 战力 / 战绩（胜负数）。

设计约束：
  * 纯 Pillow 自绘，不依赖浏览器/渲染引擎（项目铁律）；
  * 全部文案用中文真字体绘制，**不使用 emoji**（图片里 emoji 易缺字形变方框）；
  * 颜色可经 ``Theme.bg_overrides["doupo"]`` 覆盖，字段见 :func:`_pal`。

输入（build 的 params）：
  name       角色名
  lv         等级（可选，头像下的 LV 标注）
  avatar_b64 96×96 透明头像 PNG 的纯 base64（可选，缺省画占位头像）
  avatar_caption 形象说明（可选，如「持剑少年」）
  realm      境界名（原著 13 阶，如「斗之气一段」「一转斗尊巅峰」）
  exp / need 当前修为 / 升级所需
  realm_max  是否已至巅峰（True 时修为显示 x(已至巅峰)，进度条置满）
  can_break  修为已满、可突破
  alch_name  炼药师品阶名（如「二品炼药师」）
  alch_exp / alch_need 炼药经验 / 升级所需（need<=0 时显示 —）
  weapon_name 武器名（如「玄重尺」）
  weapon_level 武器显示等级（如「3」）
  weapon_stats 武器四围加成 dict（力量/敏捷/斗气/防御）
  stats      六维 dict（力量/敏捷/斗气/防御/机缘/体力）
  hp / hp_max 当前/最大气血
  mp / mp_max 当前/最大斗气
  gold       金币
  stone      炎星石数量
  power      战力
  wins/losses 战绩
  tip        底部提示（已拼好，可留空）
"""
from __future__ import annotations

import base64
import contextvars
import io
from functools import lru_cache

from PIL import Image

from .. import icons
from ..canvas import Canvas, rounded_mask
from ..theme import Theme
from . import doupo_style as S
from . import doupo_ui as UI

# ---- 双主题：当前渲染风格（contextvar，并发安全）----
# 由 doupo/card.py 在每次渲染前 set（个人偏好 > 全局默认）；模板的 _pal() 读取。
# 2026-11-23 用户「默认为简约主题」：未设置时一律浅色现代风（STYLE_LIGHT）。
# 实际出图由 card.py 每次渲染前按个人偏好/全局默认 set；这里只是无人调用时的兜底。
_doupo_style: contextvars.ContextVar[str] = contextvars.ContextVar(
    "doupo_card_style", default=UI.STYLE_LIGHT)


def set_style(style: str | None) -> contextvars.Token:
    """设置本协程后续斗破卡渲染风格；返回 token 供 reset。"""
    s = (style or "").strip().lower()
    return _doupo_style.set(s if s in UI.STYLES else UI.STYLE_LIGHT)


def reset_style(token: contextvars.Token) -> None:
    _doupo_style.reset(token)


def current_style() -> str:
    return _doupo_style.get()

# ---- 固定配色（深色玄幻「游戏本体 UI」风，2026-11-22 第三次重做）----
# 依据：用户看了解包素材 D:/AI/doupo 的斗破 HD 实机截图后明确要求「要游戏的效果、
# 别拿免费的简约图标糊弄」，并否掉了上一版「2026 前沿浅色信息卡」方向。
# 实机 UI 的视觉语言是「深蓝紫底 + 玫红回纹边 + 青蓝云纹牌匾 + 金色数值」，
# 不是浅底深字的现代信息卡，也不是金色雕花。
#
# 只改色值、不改键结构 → 全部 12 张斗破卡的 _pal() 调用方零改动即整体换肤；
# 新增的游戏专用键（fret/plaq_*/gold/panel/cloud）由 doupo_ui 消费。
C_PAGE = "#0B0912"      # 页面外底色（近黑紫）
C_CARD = "#2E294A"      # 卡片底色（深蓝紫中点，渐变由 doupo_ui.card_shell 画）
C_BORDER = "#623E7E"    # 卡片描边（暗紫，弱化用）
C_TEXT = "#EEE8FC"      # 主文字（近白淡紫，暗底对比 ≈13:1）
C_DIM = "#BAB0DA"       # 次文字（灰紫）
C_TITLE = "#FFD058"     # 标题：金（游戏数值/标题的高亮色）
C_ACCENT = "#FF60A2"    # 主强调：玫红（回纹同源色）
C_LINE = "#4A3D66"      # 分割线（暗紫）
C_TRACK = "#241E3C"     # 进度条轨道（深紫）
C_BAR_FROM = "#E82870"  # 进度条渐变起（玫红）
C_BAR_TO = "#FFD058"    # 进度条渐变止（金）
# 修为已满（可突破）：白金→亮金，一眼从常规条里跳出
C_BAR_READY_FROM = "#FFF0B0"
C_BAR_READY_TO = "#FFD058"

# 炼药师 pill（暗青底 + 薄荷字）
C_PILL_BG = "#1C3A34"
C_PILL_LINE = "#2E6A56"
C_PILL_TEXT = "#60E2A4"

# 境界 chip（暗紫底 + 金字）
C_CHIP_BG = "#2A2340"
C_CHIP_LINE = "#623E7E"
C_CHIP_TEXT = "#FFD058"

# 六维 / 武器网格底（游戏面板色）
C_CELL_BG = "#36305A"
C_CELL_LINE = "#623E7E"

# 气血 / 斗气条（珊瑚红 / 电光青，暗底上仍清晰）
C_HP_FROM = "#FF8A96"
C_HP_TO = "#DD3E5C"
C_MP_FROM = "#5EE0D2"
C_MP_TO = "#0C9FB0"

# ---- 浅色现代风专用 C_*（2026-11-23，取自 11-22 commit 0312077 的 2026 前沿配色）----
# 只换颜色不换键结构；名字前缀 L_ 与游戏风一一对应，_pal() 按当前 style 选一套。
L_PAGE = "#F6F3FB";        L_CARD = "#FFFFFF";    L_BORDER = "#E4DCF2"
L_TEXT = "#26213A";        L_DIM = "#56506E";     L_TITLE = "#5B3FA8"
L_ACCENT = "#C13584";      L_LINE = "#E9E3F3";    L_TRACK = "#ECE7F5"
L_BAR_FROM = "#6D5AE0";    L_BAR_TO = "#B84FA8"
L_BAR_READY_FROM = "#FF8A5B"; L_BAR_READY_TO = "#F5A524"
L_PILL_BG = "#E4F7EF";     L_PILL_LINE = "#B8E6D2"; L_PILL_TEXT = "#0F8A67"
L_CHIP_BG = "#F0EAFB";     L_CHIP_LINE = "#D0BEF0"; L_CHIP_TEXT = "#6B3FA8"
L_CELL_BG = "#F7F4FC";     L_CELL_LINE = "#E4DCF2"
L_HP_FROM = "#FF8A96";     L_HP_TO = "#DD3E5C"
L_MP_FROM = "#5EE0D2";     L_MP_TO = "#0C9FB0"


def _legacy_palette(style: str) -> dict[str, str]:
    """12 张卡共用的「旧键」配色（page_color/card_color/...），按风格取一套。"""
    if style == UI.STYLE_LIGHT:
        return {
            "page_color": L_PAGE, "card_color": L_CARD, "border_color": L_BORDER,
            "text_color": L_TEXT, "dim_color": L_DIM, "title_color": L_TITLE,
            "accent_color": L_ACCENT, "line_color": L_LINE, "track_color": L_TRACK,
            "bar_from": L_BAR_FROM, "bar_to": L_BAR_TO,
            "bar_ready_from": L_BAR_READY_FROM, "bar_ready_to": L_BAR_READY_TO,
            "pill_bg": L_PILL_BG, "pill_line": L_PILL_LINE, "pill_text": L_PILL_TEXT,
            "chip_bg": L_CHIP_BG, "chip_line": L_CHIP_LINE, "chip_text": L_CHIP_TEXT,
            "cell_bg": L_CELL_BG, "cell_line": L_CELL_LINE,
            "hp_from": L_HP_FROM, "hp_to": L_HP_TO, "mp_from": L_MP_FROM, "mp_to": L_MP_TO,
        }
    return {
        "page_color": C_PAGE, "card_color": C_CARD, "border_color": C_BORDER,
        "text_color": C_TEXT, "dim_color": C_DIM, "title_color": C_TITLE,
        "accent_color": C_ACCENT, "line_color": C_LINE, "track_color": C_TRACK,
        "bar_from": C_BAR_FROM, "bar_to": C_BAR_TO,
        "bar_ready_from": C_BAR_READY_FROM, "bar_ready_to": C_BAR_READY_TO,
        "pill_bg": C_PILL_BG, "pill_line": C_PILL_LINE, "pill_text": C_PILL_TEXT,
        "chip_bg": C_CHIP_BG, "chip_line": C_CHIP_LINE, "chip_text": C_CHIP_TEXT,
        "cell_bg": C_CELL_BG, "cell_line": C_CELL_LINE,
        "hp_from": C_HP_FROM, "hp_to": C_HP_TO, "mp_from": C_MP_FROM, "mp_to": C_MP_TO,
    }


# ---- 排版 ----
PAD = 36              # 内容左右内边距（2026-11-22 由 28 加到 36：游戏风回纹外框
                      # 画在 10、内线 17，PAD=28 时内容距框线只剩 11px，
                      # 用户明确反馈「字和边框黏在一起」，故统一到 36 → 距框线 26px）
FS_TITLE = 34          # 标题保持 34：改它会动 H_TITLE → 头像框 y 漂移（像素断言依赖）
# 其余字号全部对齐 doupo_style 规范：重点放大加粗、说明清晰不糊
FS_NAME = S.FS_HERO + 2    # 32 角色名
FS_BODY = S.FS_BODY + 2    # 22 武器 / 炼药师
FS_SMALL = S.FS_SUB + 1    # 18 次级信息
FS_TINY = S.FS_LABEL       # 16 小标签
FS_STAT = S.FS_HERO + 2    # 32 六维大数值
ICON = 22
BAR_H = 14
CELL_H = 80
GAP = 12
PILL_H = 36

# 头像框（与 doupo/avatar.py::HEADSHOT_SIZE 必须一致）
AVATAR_SIZE = 96
AVATAR_GAP = 20

ICON_STYLE = "line"


def _w(s: str, size: int, role: str = "regular") -> int:
    return Canvas.text_width(s, size, role)


def _fit(s: str, max_w: int, size: int, role: str = "regular") -> str:
    """超宽截断加省略号（保证不溢出面板）。"""
    if max_w <= 0:
        return ""
    if _w(s, size, role) <= max_w:
        return s
    while s and _w(s + "…", size, role) > max_w:
        s = s[:-1]
    return (s + "…") if s else ""


def _pal(t: Theme) -> dict[str, str]:
    """本卡生效配色：专属覆盖 > 当前主题的默认值。

    双主题（2026-11-23）：按 contextvar 里的 style 选「旧键」配色 + 内核 G 表；
    主题编辑器的 bg_overrides["doupo"] 仍可逐键覆盖，优先级最高（向后兼容）。
    """
    style = current_style()
    ov = getattr(t, "bg_overrides", None) or {}
    v = ov.get("doupo") if isinstance(ov.get("doupo"), dict) else {}
    defaults = _legacy_palette(style)
    # 内核专用键（doupo_ui 消费）：默认值取对应主题的内核配色，避免两处定义漂移。
    for _k, _v in UI.palette(style).items():
        if _k not in defaults:
            defaults[_k] = _v
    out: dict[str, str] = {}
    for k, d in defaults.items():
        s = v.get(k)
        out[k] = s if (isinstance(s, str) and s) else d
    return out


def _card_alpha(t: Theme) -> int:
    ov = getattr(t, "bg_overrides", None) or {}
    v = ov.get("doupo") if isinstance(ov.get("doupo"), dict) else None
    try:
        return max(0, min(100, int(v.get("card_alpha", 100)))) if v else 100
    except (TypeError, ValueError):
        return 100


# ---- 区块高度（_measure 与 build 必须同步）----
# 标题区高度：由 doupo_ui 牌匾统一（顶部留白 + 牌匾 + 与内容间距）。
# 改这里必须同步 doupo_ui.TITLE_H —— build 与 _measure 共用同一个真值。
H_TITLE = UI.TITLE_H
# 头像 + 名字行：96 头像上下各留 8px（2026-11-07 由 44px 的纯名字行改造而来）
H_HEADER = AVATAR_SIZE + 16
H_REALM = (FS_SMALL + 12) + (BAR_H + 18)   # 境界行 + 进度条
H_ALCH = PILL_H + 12             # 炼药师 pill
H_WEAPON = (FS_BODY + 12) + (FS_SMALL + 10)  # 武器名 + 武器四围
H_GRID = CELL_H * 2 + GAP + 16   # 六维 3×2 网格
H_HP = (FS_SMALL + 10) + (BAR_H + 10)       # 气血条
H_MP = (FS_SMALL + 10) + (BAR_H + 10)       # 斗气条
H_FOOT = FS_SMALL + 14           # 底部信息行（底部呼吸由 PAD=36 提供）


def _measure(_data: dict | None = None) -> int:
    # 底部用 UI.PAD_B（54）而非 PAD（36）：底部两角只有短 L 线，36 收尾会贴框。
    return (PAD + UI.PAD_B + H_TITLE + H_HEADER + H_REALM + H_ALCH + H_WEAPON
            + H_GRID + H_HP + H_MP + H_FOOT)


@lru_cache(maxsize=8)
def _headshot(b64: str):
    """base64 → 96×96 RGBA 头像；空串 / 损坏 / 缺依赖一律返回 None（画占位）。"""
    if not b64:
        return None
    try:
        im = Image.open(io.BytesIO(base64.b64decode(b64))).convert("RGBA")
        if im.size != (AVATAR_SIZE, AVATAR_SIZE):
            im = im.resize((AVATAR_SIZE, AVATAR_SIZE), Image.LANCZOS)
        return im
    except Exception:  # noqa: BLE001
        return None


def _statcell(c: Canvas, x: int, y: int, w: int, p: dict[str, str],
              value: str, label: str, size: int = FS_STAT,
              role: str = "") -> None:
    """六维单元：圆角底 + 大数值 + 小标签（数值按可用宽度自动降字号）。

    role 为战斗作用短标（如「伤害」「闪避·暴击」），渲染在标签行右侧，
    让每个属性一眼可见它换来了什么（2026-11-16 用户诉求）。
    """
    c.rounded_rect((x, y, x + w, y + CELL_H), radius=12,
                   fill=p["cell_bg"], outline=p["gold_dk"], width=1)
    # 顶部一道内高光：深底格子在没有亮边时会糊成一片（暗底换肤后实测）
    c.line((x + 12, y + 1, x + w - 12, y + 1), fill=p["panel_ln"], width=1)
    # 数值（上方，左对齐）：大字加粗；度量与绘制同 role，避免右对齐漂移
    avail = x + w - 20 - (x + 20)
    txt, sz = value, size

    def _role(s: int) -> str:
        return "bold" if s >= S.MIN_BOLD_FS else "regular"

    while avail > 0 and _w(txt, sz, _role(sz)) > avail and sz > 14:
        sz -= 2
        if _w(txt, sz, _role(sz)) <= avail:
            break
    if _w(txt, sz, _role(sz)) > avail:
        txt = _fit(txt, avail, sz, _role(sz))
    S.key(c, (x + 20, y + 22), txt, size=sz, color=p["text_color"])
    # 标签（下方）：小字明确不加粗；右端附战斗作用短标（属性↔战斗强关联）
    S.small(c, (x + 20, y + CELL_H - 20), label, size=FS_TINY, color=p["dim_color"])
    if role:
        S.small(c, (x + w - 20, y + CELL_H - 20), "→" + role,
                size=FS_TINY, color=p["dim_color"], anchor="rm")


def _bar(c: Canvas, x: int, y: int, w: int, pct: int,
         c1: str, c2: str, track: str, radius: int = BAR_H // 2) -> None:
    """进度条：轨道 + 渐变填充（渐变左=c2 右=c1，见 xiuxian.py 方向坑注释）。"""
    pct = max(0, min(100, int(pct)))
    c.rounded_rect((x, y, x + w, y + BAR_H), radius=radius, fill=track,
                   outline="#4A3D66", width=1)
    fill_w = int(w * pct / 100)
    # 比例极小时（如 880/24800）会缩成一个圆点看不出是条 → 给最小可见胶囊宽
    if 0 < pct < 100:
        fill_w = max(fill_w, BAR_H * 2)
    if fill_w > 4:
        # ⚠️ 方向坑：gradient_rect(horizontal=True) 实测「左=c2、右=c1」，
        # 所以想要「左浅右深」必须交换实参（与 xiuxian.py 一致）。
        c.gradient_rect((x, y, x + fill_w, y + BAR_H),
                        c2, c1, radius=BAR_H // 2, horizontal=True)


def build(t: Theme, data: dict | None = None, width: int | None = None,
          source: str | None = None, **kw) -> Canvas:
    data = data or {}
    width = int(width or getattr(t, "width", 720) or 720)
    width = max(560, min(width, 900))
    p = _pal(t)
    content_w = width - PAD * 2
    h = _measure(data)

    c = Canvas(width, h, p["page_color"], theme=t)
    # 游戏风外壳：深蓝紫渐变底 + 斜纹/祥云/龙鳞 + 暗角 + 回纹外框 + 青蓝云纹牌匾。
    # 2026-11-22 用户否掉浅色信息卡方向，要求「游戏的效果」，故整卡外壳换内核统一画。
    x0 = PAD
    y = UI.card_shell(c, p, str(data.get("title") or "斗破大陆"), width=width)

    # ---- 左侧静态头像 + 右侧角色名 / 境界 chip / 形象说明 ----
    # 用户 2026-11-07：「人物卡片里不用放动态人物小人了，截图一个头像就行，放在卡片里」。
    # 头像由 avatar.py 从对应档位 GIF 首帧裁出，base64 经 data 传入，不再发独立动图。
    ax, ay = x0, y + 8
    av = _headshot(str(data.get("avatar_b64") or ""))
    c.rounded_rect((ax, ay, ax + AVATAR_SIZE, ay + AVATAR_SIZE), radius=16,
                   fill=p["cell_bg"], outline=p["gold_dk"], width=2)
    if av is not None:
        # paste_alpha（而非 paste）：把圆角 mask 乘进 alpha 再合成，
        # 透明区不会带出黑边（见 canvas.py::paste_alpha 的说明）。
        c.paste_alpha(av, (ax, ay), rounded_mask((AVATAR_SIZE, AVATAR_SIZE), 16))
    else:
        # 资源缺失也不留空框：画剑形占位，卡片照常出图
        icons.draw(c, "sword", ax + AVATAR_SIZE // 2, ay + AVATAR_SIZE // 2,
                   40, p["dim_color"], style=ICON_STYLE)
    c.rounded_rect((ax, ay, ax + AVATAR_SIZE, ay + AVATAR_SIZE), radius=16,
                   outline=p["gold"], width=1)

    rx = ax + AVATAR_SIZE + AVATAR_GAP
    cx1 = x0 + content_w
    chip = str(data.get("realm") or "凡人")
    chip_fs = S.FS_KEY                  # 境界是重点：24px 加粗
    ch = chip_fs + 16
    cw = _w(chip, chip_fs, "bold") + 28
    name_y = y + 34
    name = str(data.get("name") or "无名")
    S.key(c, (rx, name_y), name, size=FS_NAME, color=p["text_color"],
          max_w=max(0, cx1 - cw - 16 - rx))
    c.rounded_rect((cx1 - cw, name_y - ch // 2, cx1, name_y + ch // 2),
                   radius=ch // 2, fill=p["chip_bg"], outline=p["chip_line"], width=1)
    S.key(c, (cx1 - cw // 2, name_y), chip, size=chip_fs,
          color=p["chip_text"], anchor="mm")

    lv = data.get("lv")
    cap = str(data.get("avatar_caption") or "斗气修行者")
    cap_txt = f"LV.{int(lv)} · {cap}" if str(lv or "").isdigit() else cap
    S.key(c, (rx, y + 76), cap_txt, size=S.FS_KEY,
          color=p["chip_text"], max_w=cx1 - rx - 8)
    y += H_HEADER

    # ---- 境界 + 修为 + 进度条 ----
    exp = int(data.get("exp") or 0)
    need = int(data.get("need") or 0)
    can_break = bool(data.get("can_break"))
    if data.get("realm_max") or need <= 0:
        exp_txt = f"{exp} 修为（已至巅峰）"
        pct = 100
    else:
        # 2026-11-26 黑盒 P3：可突破时 exp 可能是 need 的几十倍（84486/400），
        # 分子封顶到 need —— 玩家只需知道「满了能突破」，超额数字没有意义还像 bug。
        shown_exp = min(exp, need)
        if can_break:
            exp_txt = f"{shown_exp} / {need} 修为 · 可突破"
        else:
            exp_txt = f"{shown_exp} / {need} 修为"
        pct = min(100, int(shown_exp * 100 / need)) if need else 0
    # 左右同一基线 y+11：字号一大一小但视觉不漂移（原 y+9 / y+11 会错开）
    S.small(c, (x0, y + 11), "境界修为", size=S.FS_BODY,
            color=p["dim_color"])
    # 修为数值放大加粗（可突破时转朱红），一眼看得出「满没满」
    S.key(c, (x0 + content_w, y + 11), exp_txt, size=S.MIN_BOLD_FS,
          color=p["chip_text"] if can_break else p["text_color"], anchor="rm")
    y += FS_SMALL + 12
    bar_c1, bar_c2 = ((p["bar_ready_from"], p["bar_ready_to"]) if can_break
                      else (p["bar_from"], p["bar_to"]))
    _bar(c, x0, y, content_w, pct, bar_c1, bar_c2, p["track_color"])
    y += BAR_H + 18  # 余量并入 H_REALM

    # ---- 炼药师 pill ----
    alch = str(data.get("alch_name") or "凡品炼药师")
    alch_exp = int(data.get("alch_exp") or 0)
    alch_need = int(data.get("alch_need") or 0)
    alch_txt = f"炼药 · {alch}　{alch_exp}/{alch_need}" if alch_need > 0 else f"炼药 · {alch}"
    pw = min(content_w, _w(alch_txt, FS_BODY, "bold") + 44)
    ph = PILL_H
    c.rounded_rect((x0, y, x0 + pw, y + ph), radius=ph // 2,
                   fill=p["pill_bg"], outline=p["pill_line"], width=1)
    icons.draw(c, "diamond", x0 + 18, y + ph // 2, ICON, p["pill_text"], style=ICON_STYLE)
    S.key(c, (x0 + 18 + ICON + 8, y + ph // 2), alch_txt,
          size=FS_BODY, color=p["pill_text"])
    y += PILL_H + 12  # = H_ALCH

    # ---- 武器 ----
    wname = str(data.get("weapon_name") or "徒手")
    wlv = str(data.get("weapon_level") or "0")
    icons.draw(c, "sword", x0, y + FS_BODY // 2, FS_BODY, p["accent_color"], style=ICON_STYLE)
    S.key(c, (x0 + ICON + 10, y + FS_BODY // 2), f"{wname}　{wlv} 级",
          size=FS_BODY, color=p["text_color"])
    y += FS_BODY + 12
    ws = data.get("weapon_stats") or {}
    wstat = "　".join(f"{k} {int(ws.get(k, 0))}" for k in ("力量", "敏捷", "斗气", "防御"))
    S.small(c, (x0, y + FS_SMALL // 2), wstat, size=FS_SMALL,
            color=p["dim_color"])
    y += FS_SMALL + 10  # = H_WEAPON

    # ---- 六维 3×2 网格 ----
    st = data.get("stats") or {}
    roles = data.get("stat_roles") or {}
    cells = [
        ("力量", str(int(st.get("力量", 0)))),
        ("敏捷", str(int(st.get("敏捷", 0)))),
        ("斗气", str(int(st.get("斗气", 0)))),
        ("防御", str(int(st.get("防御", 0)))),
        ("机缘", str(int(st.get("机缘", 0)))),
        ("体力", str(int(st.get("体力", 0)))),
    ]
    cell_gap = GAP
    cell_w = (content_w - cell_gap * 2) // 3
    grid_y = y
    for i, (lab, val) in enumerate(cells):
        col = i % 3
        row = i // 3
        cx = x0 + col * (cell_w + cell_gap)
        cy = grid_y + row * (CELL_H + cell_gap)
        _statcell(c, cx, cy, cell_w, p, val, lab, size=FS_STAT,
                  role=str(roles.get(lab) or ""))
    y += H_GRID

    # ---- 气血 / 斗气 条 ----
    hp = int(data.get("hp") or 0)
    hp_max = int(data.get("hp_max") or 0) or 1
    mp = int(data.get("mp") or 0)
    mp_max = int(data.get("mp_max") or 0) or 1

    S.small(c, (x0, y + FS_SMALL // 2), "气血", size=FS_SMALL, color=p["dim_color"])
    S.key(c, (x0 + content_w, y + 11), f"{hp}/{hp_max}", size=S.MIN_BOLD_FS,
          color=p["chip_text"], anchor="rm")
    y += FS_SMALL + 10
    _bar(c, x0, y, content_w, int(hp * 100 / hp_max), p["hp_from"], p["hp_to"], p["track_color"])
    y += BAR_H + 10  # = H_HP

    S.small(c, (x0, y + FS_SMALL // 2), "斗气", size=FS_SMALL, color=p["dim_color"])
    S.key(c, (x0 + content_w, y + 11), f"{mp}/{mp_max}", size=S.MIN_BOLD_FS,
          color=p["chip_text"], anchor="rm")
    y += FS_SMALL + 10
    _bar(c, x0, y, content_w, int(mp * 100 / mp_max), p["mp_from"], p["mp_to"], p["track_color"])
    y += BAR_H + 10  # = H_MP

    # ---- 底部信息行 ----
    gold = int(data.get("gold") or 0)
    stone = int(data.get("stone") or 0)
    power = int(data.get("power") or 0)
    wins = int(data.get("wins") or 0)
    losses = int(data.get("losses") or 0)
    foot = f"金币 {gold}　炎星石 {stone}　战力 {power}　战绩 {wins}胜{losses}负"
    # 底部是玩家高频扫读项，放大到 20px；不加粗（避免整行横画糊在一起）
    S.small(c, (x0, y + FS_SMALL // 2), foot, size=S.FS_BODY,
            color=p["text_color"], max_w=content_w)
    y += H_FOOT

    if source:
        S.small(c, (width - PAD, h - PAD + 6), source, size=FS_TINY,
                color=p["dim_color"], anchor="rb")

    return c
