# -*- coding: utf-8 -*-
"""斗破苍穹·双帝之战（QQ 群聊文字版）· 数值表。

数值来源分两类，README 里有完整对照：
  [源码] = 直接来自反编译的 J2ME 原版（src/*.java），逐位 1:1 搬过来
  [调参] = 原版是单机节奏（单级 205000 经验），群里照搬会几周升不了一级，
           故按群聊活跃度重算；只调"速度"，不动"形状"

所有表集中在这里，方便调参。
"""

from __future__ import annotations

import os
from pathlib import Path

# ---------------------------------------------------------------------------
# 数据落盘（默认插件内 data/，可经环境变量改路径；不写 C 盘用户目录）
# ---------------------------------------------------------------------------
DATA_PATH = os.environ.get(
    "DOUPO_DATA_PATH",
    str(Path(__file__).resolve().parent / "data" / "doupo.json"),
)

# ---------------------------------------------------------------------------
# 爬塔会话落盘（2026-11-17 用户「爬塔层数被你重置掉了」事故后新增）
#
# 事故：TowerStore 原先只存进程内存，我为了改平衡连续 `docker compose up -d --build`
# 重建容器 3 次，正在爬的层数/气血/增益全部清零，玩家看到的就是「层数被重置」。
#
# 铁律（见 .workbuddy/memory/MEMORY.md）：容器运行态数据只许落挂载卷，且不许用
# `__file__` 当默认目录（会被 COPY src 烘进镜像，重建即回滚）。
# 因此这里**不写**"插件内 data/"这种默认，而是跟着 DATA_PATH 走：
#   容器：DOUPO_DATA_PATH=/app/data/doupo/doupo.json → /app/data/doupo/tower_session.json
#   宿主/单测：改了 DOUPO_DATA_PATH 就自动跟着改，不需要第二套路径逻辑。
# ---------------------------------------------------------------------------
TOWER_SESSION_PATH = os.environ.get(
    "DOUPO_TOWER_SESSION_PATH",
    str(Path(DATA_PATH).parent / "tower_session.json"),
)

#: 周副本会话（2026-11-17）：与爬塔会话同目录但**分开文件**，互不顶掉。
#: 同样只从 DATA_PATH 推导（单源），不新开环境变量；容器里落在 /app/data/doupo/。
WEEKLY_SESSION_PATH = os.environ.get(
    "DOUPO_WEEKLY_SESSION_PATH",
    str(Path(DATA_PATH).parent / "weekly_session.json"),
)

# ---------------------------------------------------------------------------
# 玩家存档库（SQLite，2026-11-17 从 doupo.json 迁来）
#
# 为什么换库（用户原话「玩家数据肯定是要存到库里，你存到 JSON 里安全吗」）：
#   * JSON 版每次 put_player 都重写**全服**存档（36 个写入点，1000 玩家时每次
#     ~700KB），写放大且没有事务；
#   * asyncio.Lock 只在单进程内有效 —— 第二个实例/测试脚本 = 后写覆盖，静默丢档；
#   * 落盘没有 fsync，宿主断电可能截断（只能靠 .corrupt- 备份人工恢复）。
#
# 路径**只由 DATA_PATH 推导**，不单独开 DOUPO_DB_PATH 环境变量：
#   容器：/app/data/doupo/doupo.json → /app/data/doupo/doupo.sqlite
#   宿主/单测：tmp/doupo.json → tmp/doupo.sqlite（每个用例目录独立）
#
# 🔴 2026-11-17 事故：一开始给 DB_PATH 单独留了 `DOUPO_DB_PATH` 覆盖，compose 里
# 设成 /app/data/doupo/doupo.sqlite。结果 test_e2e / test_menu_full 只改
# `DOUPO_DATA_PATH` 到临时目录，DB 却仍指向**生产库** —— 跑一次测试就往生产
# 库里塞进 4 个假玩家（10001/10002/10003/10021，群 900000001 / pv:*）。
# 教训：同一个东西只许有一个来源。两条路径必须同进同退，否则测试隔离必漏。
# 铁律：容器里绝不出现盘符路径（见 .workbuddy/memory/MEMORY.md）。
# ---------------------------------------------------------------------------
DB_PATH = str(Path(DATA_PATH).with_suffix(".sqlite"))

# ---------------------------------------------------------------------------
# 境界 / 星级  [原著小说]
# 原著《斗破苍穹》完整修为体系（据百度百科 / 360 百科 / 互动百科「修炼体系」表，
# 三处一致）：
#   斗之气 一至九段        （入门，滋润筋骨；萧炎开局就是「斗之气三段」）
#   斗者   一至九星        内视，聚气成气旋
#   斗师   一至九星        斗气纱衣，聚气化液态
#   大斗师 一至九星        斗气铠甲，斗气外放，聚气化固态
#   斗灵   一至九星        斗气凝物，固态，形似海胆
#   斗王   一至九星        斗气化翼，调动外界少量能量
#   斗皇   一至九星        斗气化翼，可大量调动外界同属性能量
#   斗宗   一至九星        不借外力停留虚空，可造空间锁
#   斗尊   一至九星        掌握并运用空间之力
#   斗尊巅峰 一至九转      斗尊之巅，面临入圣困境（每一转≈斗尊一至九星所需斗气）
#   半圣   低阶 / 中阶 / 高阶   初入圣境，远胜尊境
#   斗圣   一至九星        开辟空间；六星标志是空间挪位
#   斗帝                  改变血脉，纳天地入体
#
# 版本沿革（勿再走回头路）：
#   最初照搬 J2ME 手游 5 境界压缩模型 → 与原著不符；
#   2026-11-14 上午为「29 级总量不变」压成每境界 3 星 → 三星斗师突破成
#   「一星大斗师」，星数归零像掉级（用户实测否掉）；
#   同日下午改回九星制，但漏了 斗之气 / 斗尊巅峰 / 半圣 三段（用户质问
#   「原著是这样的吗」）→ 现按原著补全为 13 阶，见 REALM_TABLE。
#   斗帝原著不分星（个别百科写「一至九星」无出处），此处按单级处理。
# ---------------------------------------------------------------------------
STAR_NAMES = ["一星", "二星", "三星", "四星", "五星", "六星", "七星", "八星", "九星"]
DUAN_NAMES = ["一段", "二段", "三段", "四段", "五段", "六段", "七段", "八段", "九段"]
ZHUAN_NAMES = ["一转", "二转", "三转", "四转", "五转", "六转", "七转", "八转", "九转"]
BANSHENG_NAMES = ["低阶", "中阶", "高阶"]

#: (境界名, 该境界的小段数, 小段名表, 展示格式)
#:   sub_first   → 三星斗师 / 七转斗尊巅峰
#:   realm_first → 斗之气三段（原著就写成这样）
#:   dot         → 半圣·低阶
#:   solo        → 斗帝（无小段）
REALM_TABLE: list[tuple[str, int, list[str], str]] = [
    ("斗之气", 9, DUAN_NAMES, "realm_first"),
    ("斗者", 9, STAR_NAMES, "sub_first"),
    ("斗师", 9, STAR_NAMES, "sub_first"),
    ("大斗师", 9, STAR_NAMES, "sub_first"),
    ("斗灵", 9, STAR_NAMES, "sub_first"),
    ("斗王", 9, STAR_NAMES, "sub_first"),
    ("斗皇", 9, STAR_NAMES, "sub_first"),
    ("斗宗", 9, STAR_NAMES, "sub_first"),
    ("斗尊", 9, STAR_NAMES, "sub_first"),
    ("斗尊巅峰", 9, ZHUAN_NAMES, "sub_first"),
    ("半圣", 3, BANSHENG_NAMES, "dot"),
    ("斗圣", 9, STAR_NAMES, "sub_first"),
    ("斗帝", 1, [""], "solo"),
]

REALM_NAMES = [t[0] for t in REALM_TABLE]
LV_PER_REALM = 9                        # 除半圣(3)/斗帝(1)外的通例
_TOTAL = sum(t[1] for t in REALM_TABLE)
MAX_LV = _TOTAL                         # 103
TIANDI_LV = MAX_LV                      # 斗帝
PEAK_LV = MAX_LV - 1                    # 九星斗圣（冲击斗帝的起点）

#: 每个等级所属的 (境界下标, 段内下标)，启动时算一次
_LEVEL_INDEX: list[tuple[int, int]] = []
for _ri, (_name, _cnt, _subs, _fmt) in enumerate(REALM_TABLE):
    for _si in range(_cnt):
        _LEVEL_INDEX.append((_ri, _si))


def _clamp_lv(lv) -> int:
    try:
        n = int(lv)
    except (TypeError, ValueError):
        return 1
    return max(1, min(MAX_LV, n))


def realm_index(lv: int) -> int:
    """等级 -> 大境界下标（0=斗之气 … 12=斗帝）。属性成长的「阶」用它。"""
    ri, _ = _LEVEL_INDEX[_clamp_lv(lv) - 1]
    return ri


def realm_lv(realm_idx: int) -> int:
    """境界下标 -> 该境界起始等级（realm_index 的逆）。斗尊(8)=73、斗圣(11)=94。"""
    ri = max(0, min(len(REALM_TABLE) - 1, int(realm_idx)))
    return 1 + sum(t[1] for t in REALM_TABLE[:ri])


def realm_name(lv: int) -> str:
    """等级 -> 境界名（原著 13 阶）。"""
    return REALM_TABLE[realm_index(lv)][0]


def star_name(lv: int) -> str:
    """等级 -> 小段名（一段…九段 / 一星…九星 / 一转…九转 / 低中高阶 / 空）。"""
    ri, si = _LEVEL_INDEX[_clamp_lv(lv) - 1]
    return REALM_TABLE[ri][2][si]


def full_realm(lv: int) -> str:
    """完整称呼，按原著写法：「斗之气三段」「三星斗师」「七转斗尊巅峰」「半圣·低阶」「斗帝」。"""
    n = _clamp_lv(lv)
    ri, si = _LEVEL_INDEX[n - 1]
    name, _cnt, subs, fmt = REALM_TABLE[ri]
    sub = subs[si]
    if fmt == "solo" or not sub:
        return name
    if fmt == "realm_first":
        return f"{name}{sub}"
    if fmt == "dot":
        return f"{name}·{sub}"
    return f"{sub}{name}"


def migrate_lv(old_lv: int, src_ver: int = 1) -> int:
    """旧存档等级 → 当前（13 阶九星制）等级。

    src_ver=1：最初「每境界 3 星 / 满级 29」；
    src_ver=2：上午的九星制但缺 斗之气/斗尊巅峰/半圣（满级 83）。

    两种都按「境界 + 境界内第几星」换算，玩家看到的称呼一字不变，
    例如旧 3 星制 6 级「三星斗师」→ 2 版 12 级 → 现版 21 级「三星斗师」。
    """
    try:
        old = int(old_lv)
    except (TypeError, ValueError):
        return 1
    if int(src_ver) <= 1:
        # 1 版：斗者=1-3 … 斗圣=25-27，28 斗圣巅峰，29 斗帝
        if old >= 29:
            v2 = 83
        elif old >= 28:
            v2 = 82
        elif old <= 1:
            v2 = 1
        else:
            v2 = ((old - 1) // 3) * LV_PER_REALM + (old - 1) % 3 + 1
    else:
        v2 = old
    # 2 版：斗者 1-9 … 斗圣 73-81，82 斗圣巅峰，83 斗帝
    if v2 >= 83:
        return TIANDI_LV
    if v2 >= 82:
        return PEAK_LV
    if v2 <= 0:
        return 1
    # 2 版第 1-9 级是斗者；现版前面多了 9 级斗之气 → 整体后移 9 级
    return min(MAX_LV, v2 + LV_PER_REALM)


# ---------------------------------------------------------------------------
# 八维属性基础值  [源码]
# d/hero.hy 形象 0：ai.g[0] = [200, 0, 6, 5, 10, 80, 25, 15, 20, 12]
#   [3]=闪避 5  [4]=暴击 10  [5]=体力 80  [6]=力量 25  [7]=斗气 15
#   [8]=敏捷 20 [9]=防御 12
# 面板只显示 6 格：力量 / 敏捷 / 斗气 / 防御 / 机缘 / 体力（实机逐格核对）
# ---------------------------------------------------------------------------
HERO_BASE = {
    "体力": 80,
    "力量": 25,
    "斗气": 15,
    "敏捷": 20,
    "防御": 12,
}
HERO_DODGE = 5      # 闪避率基础 5%
HERO_CRIT = 10      # 暴击率基础 10%

#: 面板 6 格的展示顺序（实机：力量 敏捷 斗气 / 防御 机缘 体力）
PANEL_ORDER = ["力量", "敏捷", "斗气", "防御", "机缘", "体力"]

#: 六维属性在战斗中的实际作用（2026-11-16 用户诉求：属性与战斗强关联）。
#: 人物卡/战报归因/帮助文案共用这一份口径，改这里即可三处同步。
STAT_BATTLE_ROLE = {
    "力量": "伤害",
    "敏捷": "闪避·暴击",
    "斗气": "斗气·伤害",
    "防御": "减伤",
    "机缘": "气运·贵人",
    "体力": "气血",
}

# ---------------------------------------------------------------------------
# 玄重尺  [源码]
# d/i.hy 表 1 第 0 行：[0, 1, 1, 3, 0, 4, 4,300, 6,260, 5,150, 7,210]
#   -> 需求 1 级、品质 3(金)、4 条词条：力量+300 敏捷+260 斗气+150 防御+210
# 强化上限：l.java:304 `g.c[0][3] > 8` -> 已达最高级别，即内部 L 最大 9
# 界面显示等级 = L + 1（实机强化框：L=0 显示「1 级 ↗ 2 级」）
# 满级硬编码：h.java:81-89  L==9 时 力量/敏捷 2000、斗气 1000、防御 1200
# ---------------------------------------------------------------------------
WEAPON = {
    "name": "玄重尺",
    "name_max": "异火恒古尺",
    "req_lv": 1,
    "quality": 3,
    "stats": {"力量": 300, "敏捷": 260, "斗气": 150, "防御": 210},
    "max_internal": 9,          # 内部 L 上限
    "max_display": 10,          # 界面显示上限
    "max_stats": {"力量": 2000, "敏捷": 2000, "斗气": 1000, "防御": 1200},
}


def weapon_display_level(internal: int) -> int:
    return int(internal) + 1


def weapon_name(internal: int) -> str:
    """l.java:1604  h.b[1][g.c[0][3] > 8 ? 2 : 0]"""
    return WEAPON["name_max"] if internal > 8 else WEAPON["name"]


def weapon_stat(base: int, internal: int) -> int:
    """h.java:80-102  收益 = base × (1 + 0.1·L(L+1)/2)；L=9 走硬编码。"""
    if internal >= 9:
        return 0  # 由调用方查 max_stats
    total = base
    for k in range(internal + 1):
        total += base * (k * 10) // 100
    return total


# ---------------------------------------------------------------------------
# 斗技（4 个）  [源码 + 实机]
# 名称与第 1 条说明取自实机「斗技」面板；图标色对应技能栏快捷键 1/3/7/9
# 升级消耗「焚血散」——实机点升级提示「焚血散不足，请炼制焚血散」
# ---------------------------------------------------------------------------
SKILLS = [
    {
        "name": "龙吟碎", "key": "1", "icon": "紫",
        "desc": "凝火化龙，攻击周围目标，技能每提升一级，技能伤害增加 20%，"
                "将目标击晕几率增加 20%",
        "max": 5, "dmg_mult": 1.6,
    },
    {
        "name": "天火三玄变", "key": "3", "icon": "红",
        "desc": "焚炎附体，连续三段火劲贯体而出，技能每提升一级伤害增加 20%",
        "max": 5, "dmg_mult": 1.4,
    },
    {
        "name": "八极崩", "key": "7", "icon": "绿",
        "desc": "拳意崩山，一击叠加八重暗劲，技能每提升一级伤害增加 20%",
        "max": 5, "dmg_mult": 1.5,
    },
    {
        "name": "五轮离火法", "key": "9", "icon": "橙",
        "desc": "五轮离火环身，焚尽周身之敌，技能每提升一级伤害增加 20%",
        "max": 5, "dmg_mult": 1.8,
    },
]
SKILL_NAMES = [s["name"] for s in SKILLS]
SKILL_BY_NAME = {s["name"]: s for s in SKILLS}

#: 技能升级消耗（实机：焚血散不足）
SKILL_UP_ITEM = "焚血散"


def skill_up_cost(cur_lv: int) -> int:
    """从 cur_lv 升到 cur_lv+1 需要几份焚血散。"""
    return max(1, int(cur_lv))


def skill_dmg_mult(name: str, lv: int) -> float:
    """技能每提升一级伤害 +20%。"""
    base = SKILL_BY_NAME[name]["dmg_mult"]
    return base * (1 + 0.2 * (int(lv) - 1))


# ---------------------------------------------------------------------------
# 斗技三阶 · 四槽上阵  [调参]
# 普通 = 黄阶/玄阶（新手，随手可学）；高阶 = 地阶/天阶（需修为）；
# 特殊 = 秘法·瞳术·血脉·宗门绝学（需高修为，后期可四槽全特殊）。
#
# 斗气消耗按 mp_max 的**比例**，随等级/异火放大，保证"每战可释放次数"稳定：
#   lv1≈330 / lv20≈2100 / lv50≈9300 / lv90≈27300（无火）
#   普通 0.20 → ~5 次；高阶 0.34 → ~3 次；特殊 0.50 → ~2 次
# 斗气不足以释放任一已装备斗技时，强制武器普攻（伤害大减、持续掉血）。
# ---------------------------------------------------------------------------
SKILL_TIERS = ("普通", "高阶", "特殊")
SKILL_TIER_MP_FRAC = {"普通": 0.20, "高阶": 0.34, "特殊": 0.50}
SKILL_TIER_DMG = {"普通": 1.0, "高阶": 1.4, "特殊": 1.8}
SKILL_TIER_UNLOCK = {"普通": 1, "高阶": 20, "特殊": 40}
LOADOUT_SIZE = 4
#: 一次「服丹 <丹名> N」连服的数量硬顶（2026-11-25 用户「不限制数量」）。
#: 只是防手抖把 100000 打成一笔刷屏的兜底，正常玩家背包远到不了这个数；
#: 真正能吃几枚由背包实际持有数决定——这里不设玩法上限。
PILL_USE_BATCH_CAP = 99
#: 武器普攻倍率（相对力量）——斗气耗尽后的兜底输出，故意压低
WEAPON_BASE_MULT = 1.0

#: 转宗（改换门庭）
#: 为什么需要：入道只能进「新手池」8 派，而 25 派里 17 派是进阶/隐世池
#: （unlock_lv 40 / 90）。没有转宗，这 17 派的 120+ 门斗技玩家一辈子见不到。
#: 转宗只换师门、重配四槽，**不重新发该派的入道物品**（否则等于刷物品）；
#: 已升的技能等级按技名保留，回到原派仍是原等级。
FACTION_SWITCH_COST_BASE = 3000
FACTION_SWITCH_COST_PER_LV = 200


def faction_switch_cost(lv: int) -> int:
    """转宗要多少金币：境界越高，越难改换门庭。"""
    return int(FACTION_SWITCH_COST_BASE + FACTION_SWITCH_COST_PER_LV * max(1, int(lv)))


# ---------------------------------------------------------------------------
# 异火（5 种）  [实机]
# [源码] 实机异火面板是「进度条 0/5000」，升级靠「药渣」喂养（m.java 原文）
# [调参] 5000 → 60：群里药渣只有一个来源（炼药失败 +3），5000 要约 1600 次失败炼药，
#        异火等于永远点不亮。60 的口径：第 1 级约 20 次失败炼药（≈1 小时），
#        练满 4 级累计 600 药渣，作为长期追求线。
#        第 N 级所需 = need × N（线性递增 60 / 120 / 180 / 240）
# ---------------------------------------------------------------------------
FLAME_MAX_LV = 4
FLAMES = [
    {"name": "青莲地心火", "icon": "紫", "need": 60,
     "desc": "莲心青火经大地之火千年锤炼，精纯能量免去提炼；斗气槽上限每级 +25%"},
    {"name": "陨落心炎", "icon": "绿", "need": 60,
     "desc": "火由心生、淬气炼骨，外号修炼作弊器；获得经验每级 +50%"},
    {"name": "三千焱炎火", "icon": "红", "need": 60,
     "desc": "别名三千星空焱炎火，成形于遥遥星空并长年吸收星空之力；吸血比例每级 +2%"},
    {"name": "净莲妖火", "icon": "蓝", "need": 60,
     "desc": "有净化万物之效，沾上丁点便净化成虚无；暴击与闪避每级 +10%"},
    {"name": "帝炎", "icon": "金", "need": 0,
     "desc": "异火榜第一并非特定火焰，由二十二种异火融合凝聚、自命帝炎；全属性 +20%"},
]
FLAME_NAMES = [f["name"] for f in FLAMES]

#: 入道送火已取消（2026-11-24 用户「异火极其稀有，直接送不符合原著」）。
#: 新号空火开局，火焰只能靠九星 BOSS 掉落或炼制兽火单方获得；无火禁炼药/养火（有提示）。
#: 保留空串常量兼容旧读取点（faction_flame / 卡面"本宗异火"提示位），恒不发火。
STARTER_FLAME = ""
FACTION_FLAME_GRANT = {}
FLAME_BY_NAME = {f["name"]: f for f in FLAMES}
#: 帝炎不靠喂养，靠斗帝之路解锁（原版是内购）
FLAME_DIYAN = "帝炎"

# ---------------------------------------------------------------------------
# 异火三层投放（2026-11-19 设计文档 docs/异火投放分层设计_2026-11-19.md）
# ---------------------------------------------------------------------------
# 23 朵 = S 大后期/剧情锁 5 + A 隐藏支线彩蛋 7 + B 数值填充 11。
# 「解锁」= 玩家收服（p["flames"][name] > 0），单一真值；S 的境界/剧情门槛、A 的支线
# 由发放入口校验，不另建解锁字段（最高声明：不塞 JSON、不建重复真值源）。
FLAME_TIER = {
    "帝炎": "S", "虚无吞炎": "S", "净莲妖火": "S", "三千焱炎火": "S", "陨落心炎": "S",
    "金帝焚天炎": "A", "生灵之焱": "A", "九幽金祖火": "A", "火山石焰": "A",
    "骨灵冷火": "A", "九龙雷罡火": "A", "青莲地心火": "A",
    "九幽风炎": "B", "海心焰": "B", "火云水炎": "B", "风怒龙炎": "B",
    "幽冥毒火": "B", "阴阳双炎": "B", "万兽灵火": "B", "荒破灭焱": "B",
    "红莲业火": "B", "龟灵地火": "B", "玄黄炎": "B",
}

#: S 线解锁门槛：境界索引（realm_index）下限 / 主线进度 main_idx 下限。
#: 两者取「且」——既要熬到境界，也要推到对应剧情。数值为 [调参]，原著只给榜位。
#: 境界索引来自 REALM_TABLE：斗宗=7(64-72)、斗尊=8(73-81)、斗圣=11(94-102)、斗帝=103。
S_FLAME_GATE = {
    "虚无吞炎":   {"realm": 11, "main": 4},   # 斗圣 + 魂族主线
    "净莲妖火":   {"realm": 11, "main": 3},   # 斗圣 + 天墓/妖火空间
    "三千焱炎火": {"realm": 8,  "main": 3},   # 斗尊 + 丹塔星域
    "陨落心炎":   {"realm": 8,  "main": 2},   # 斗尊 + 天焚炼气塔
    # 帝炎走 p["diyan"] 斗帝成就位（tiandi_rate），不在此表。
}

#: A 线隐藏支线：完成该支线时**静默**收服异火（任务 desc/reward 不写火名）。
#: key=支线名，value=火名。支线本体在 SIDE_TASK_POOL，disguise=True 表示隐藏奖励。
A_FLAME_SIDE = {
    "神农谷的灵田":   "生灵之焱",
    "药老的旧友":     "骨灵冷火",
    "焚炎谷的试炼":   "九龙雷罡火",
    "古族来客":       "金帝焚天炎",
    "沙漠深处的蛇影": "青莲地心火",
    "炎族的窖藏":     "九幽金祖火",
    "星域的余烬":     "火山石焰",
}

# ---------------------------------------------------------------------------
# B 线数值填充异火（11 朵）：可持有 / 可喂养 / 只给数值，无主动技。
# ---------------------------------------------------------------------------
# * kind = alchemy → 炼药成功率加成（每级 +value，封顶 CRAFT_RATE_CAP）
# * kind = puppet  → 傀儡伤害/防御加成（每级 +value）
# * need = 每级药渣（与可养五火同口径 need×N）
# * novel = 原著依据；正文 0 命中的三朵显式标 game_only，不编造引用（原著红线）。
BONUS_FLAMES = [
    {"name": "九幽风炎", "icon": "黑", "rank": 10, "kind": "alchemy", "value": 0.03,
     "need": 60, "desc": "成形于极阴深渊风罡最烈处，可凝品质最好的药鼎；炼药成功率每级 +3%。"},
    {"name": "海心焰", "icon": "蓝", "rank": 15, "kind": "alchemy", "value": 0.02,
     "need": 60, "desc": "深蓝水相，升腾如海水涟漪；炼药成功率每级 +2%。"},
    {"name": "火云水炎", "icon": "紫", "rank": 16, "kind": "alchemy", "value": 0.02,
     "need": 60, "desc": "炎族火耀长老所掌；炼药成功率每级 +2%。"},
    {"name": "风怒龙炎", "icon": "黄", "rank": 18, "kind": "alchemy", "value": 0.03,
     "need": 60, "desc": "生于古老沙漠龙卷风风眼，风火相合；炼药成功率每级 +3%。"},
    {"name": "幽冥毒火", "icon": "绿", "rank": 20, "kind": "alchemy", "value": 0.04,
     "need": 60, "desc": "幽冥毒泽瘴气所养，剧毒；毒系炼药成功率每级 +4%。"},
    {"name": "阴阳双炎", "icon": "白", "rank": 21, "kind": "alchemy", "value": 0.03,
     "need": 60, "desc": "黑白双色，兼具生灭之力；炼药成功率每级 +3%。"},
    {"name": "万兽灵火", "icon": "赤", "rank": 22, "kind": "alchemy", "value": 0.02,
     "need": 60, "desc": "万兽之魂血脉凝结，常现万兽模样；炼药成功率每级 +2%。"},
    {"name": "荒破灭焱", "icon": "墨", "rank": 6, "kind": "puppet", "value": 0.04,
     "need": 60, "desc": "霸道之火，炎族火炫所降；傀儡攻击每级 +4%。"},
    {"name": "红莲业火", "icon": "红", "rank": 8, "kind": "puppet", "value": 0.03,
     "need": 60, "desc": "深红妖艳、火苗化莲，压制低位异火；傀儡攻击每级 +3%。"},
    {"name": "龟灵地火", "icon": "褐", "rank": 13, "kind": "puppet", "value": 0.04,
     "need": 60, "desc": "形如巨龟的褐色异火；傀儡防御每级 +4%。"},
    {"name": "玄黄炎", "icon": "深黄", "rank": 23, "kind": "puppet", "value": 0.02,
     "need": 60, "desc": "异火榜末位、深黄之火；傀儡攻防每级 +2%（入门档）。"},
]
BONUS_FLAME_NAMES = [f["name"] for f in BONUS_FLAMES]
BONUS_FLAME_BY_NAME = {f["name"]: f for f in BONUS_FLAMES}

# ---------------------------------------------------------------------------
# 兽火（2026-11-24 用户「只保留异火和兽火的说法」，附件 docs/兽火单方.txt 全量加入）。
# ---------------------------------------------------------------------------
# 火焰身份只有两类：异火（异火榜 + B 线数值火 + 新生异火）、兽火（本表 8 朵）。
# 已取消"天然火/人造火/融合火"作为独立身份——详见 docs/兽火系统改造_设计文档_2026-11-24.md。
#   * 6 朵原著有据（紫火等），2 朵附件新造（幻金火/天阴魔火的魔兽 0 命中语料，标 game_only）。
#   * bonus: alchemy=炼药成功率 / atk=面板攻击；走与 B 线同一套 p["flames"][name] 单一真值。
#   * 丹方在 recipes_data.BEAST_PILLS，服后获得对应兽火（与九星掉落并列第二条途径）。
BEAST_FLAMES = [
    {"name": "紫火", "icon": "紫", "beast": "紫晶翼狮王", "bonus": "atk", "value": 0.03,
     "need": 55, "pill": "紫火丹",
     "desc": "紫晶翼狮王体内凝结的兽火，药老明言「不算异火，叫它兽火」；"
             "可作炼丹火种，亦可融入佛怒火莲。攻击每级 +3%。"},
    {"name": "桃花火", "icon": "粉", "beast": "奎木兽", "bonus": "alchemy", "value": 0.02,
     "need": 55, "pill": "桃花丹",
     "desc": "五阶木系魔兽奎木兽体内的粉色兽火，比紫火温顺易驯，"
             "适合新手练习控火、温养药材。炼药成功率每级 +2%。"},
    {"name": "乾蓝水炎", "icon": "蓝", "beast": "惊涛龙兽", "bonus": "alchemy", "value": 0.03,
     "need": 60, "pill": "乾蓝丹",
     "desc": "惊涛龙兽体内的兽火，偏阴柔绵长；适合炼制水属性丹药，也能压制火毒。"
             "炼药成功率每级 +3%。"},
    {"name": "天寒火", "icon": "白", "beast": "天骨蟒", "bonus": "alchemy", "value": 0.03,
     "need": 60, "pill": "天寒丹",
     "desc": "天骨蟒体内的白色兽火，外冷内热，可处理需「冷火」的特殊药材、压制火毒。"
             "炼药成功率每级 +3%。"},
    {"name": "海魔炎", "icon": "靛", "beast": "海魔兽", "bonus": "atk", "value": 0.04,
     "need": 65, "pill": "海魔丹",
     "desc": "七阶魔兽海魔兽体内的深蓝兽火，韧性极强，适合长时间炼药，"
             "是施展五轮离火法的备用火种。攻击每级 +4%。"},
    {"name": "天阴魔火", "icon": "墨", "beast": "天阴三头狮", "bonus": "atk", "value": 0.04,
     "need": 65, "pill": "天阴丹",
     "desc": "天阴三头狮的淡黑色兽火，附带侵蚀之力，可破解斗气护甲；"
             "炼药需谨慎以免侵蚀药材。攻击每级 +4%。"},
    {"name": "血妖焰火", "icon": "赤", "beast": "近千火属性魔兽", "bonus": "atk", "value": 0.05,
     "need": 70, "pill": "血妖丹",
     "desc": "近千头火属性魔兽血液融合而成的血红兽火，威力接近低阶异火，"
             "可作高阶炼药师的替代火种。攻击每级 +5%。"},
    {"name": "幻金火", "icon": "金", "beast": "幻火蝎龙兽", "bonus": "alchemy", "value": 0.04,
     "need": 65, "pill": "幻金火丹", "game_only": True,
     "desc": "幻火蝎龙兽体内的金黄色兽火，带轻微幻术干扰，"
             "炼药时可提升剥离药材杂质的精度。炼药成功率每级 +4%。"},
]
BEAST_FLAME_NAMES = [f["name"] for f in BEAST_FLAMES]
BEAST_FLAME_BY_NAME = {f["name"]: f for f in BEAST_FLAMES}

#: 新生异火（萧炎融合而成，非异火榜但在原著有据）——归入**异火**身份，不再是独立"融合火"类。
#: 从旧 OTHER_FLAMES 迁入；阳火/化生火已删（考据台账保留在 BEAST_FLAME_CITES 之外的历史文档）。
FUSION_FLAMES = [
    {"name": "琉璃莲心火", "icon": "青", "bonus": "atk", "value": 0.05, "need": 80,
     "desc": "萧炎融合青莲地心火＋陨落心炎生成、亲自命名的新生异火，未列异火榜，"
             "威力不低于骨灵冷火。攻击每级 +5%。"},
    {"name": "三千莲心火", "icon": "玄", "bonus": "atk", "value": 0.06, "need": 85,
     "desc": "萧炎融合三种异火生成、亲自命名的新生异火，自测「有排进前六的资格」。"
             "攻击每级 +6%。"},
]
FUSION_FLAME_NAMES = [f["name"] for f in FUSION_FLAMES]

#: 兽火出处 —— **内部考据台账，禁止渲染**。game_only 的 2 朵语料 0 命中，显式留空。
BEAST_FLAME_CITES = {
    "紫火": "第一百三十二章「斗皇级别的战斗」",
    "桃花火": "第两百十六章「淘宝」",
    "乾蓝水炎": "第三百一十七章「各显神通」",
    "天寒火": "第一千零二十五章「异火测试」",
    "海魔炎": "第一千零七十五章「离火焚天」",
    "天阴魔火": "第一千一百五十章「血妖焰火」",
    "血妖焰火": "第一千一百五十章「血妖焰火」",
    "幻金火": "",               # 附件新造，语料 0 命中，不编章节
}

#: 可持有/可喂养的全部火焰名（可养五火 + B 线十一火 + 新生异火二朵 + 兽火八朵；帝炎走 diyan 位）
OWNED_FLAME_NAMES = FLAME_NAMES + BONUS_FLAME_NAMES + FUSION_FLAME_NAMES + BEAST_FLAME_NAMES

#: 兼容旧代码：OTHER_FLAME_NAMES 现指向"新生异火+兽火"（都是玩家可持有的非榜火）。
OTHER_FLAMES = FUSION_FLAMES + BEAST_FLAMES
OTHER_FLAME_NAMES = [f["name"] for f in OTHER_FLAMES]
OTHER_FLAME_BY_NAME = {f["name"]: f for f in OTHER_FLAMES}
OTHER_FLAME_CITES = dict(BEAST_FLAME_CITES)

# ---------------------------------------------------------------------------
# 九星突破 BOSS 战（2026-11-20 重做：用户「别写快照玩法」「BOSS 登场要有引言台词」，
# 改为原著有名有姓的 BOSS + 台词 + 专属技能 + 恶心机制；数值仍按等级反解保平衡。
# 成功后概率掉异火/兽火，兽火概率更高的口径不变）。
# ---------------------------------------------------------------------------
#: 触发：当前 lv 处于「九星」且经验已满可突破（跨大境界那一级）。
#: 判定用 REALM_TABLE 的星位——某大境界的最后一颗星（第 9 星 / 半圣第 3 阶）。
#: 快照 = 触发瞬间 core.power(p) 与深拷贝的 p；BOSS 用放大后的快照反解，
#: 保证「比快照强一点」。倍数越大越难，须 >1。
#: 2026-11-20 命中面板上线后重标定：BOSS 原来只有「血攻倍数」，现在多了
#: 隐藏面板（暴击 ~16% / 身法 ~30 / 眼力 ~30），强度已经从面板那边补上了。
#: 若还留 1.60/1.15，九星突破会从「必赢」变成「必输」（实测 92%→22%）。
#: 现标定 1.15/1.10：裸装胜率见 docs，且「战败」这一路径仍然打得出来
#: （旧倍数 + 无面板时低阶档位只有 3%~23%，那条必败是另一种坏法）。
BOSS_BREAK_HP_MULT = 1.15      # BOSS 血量 = 快照基准 ×1.15（更耐打，拖长战斗消耗斗气）
BOSS_BREAK_ATK_MULT = 1.10     # BOSS 攻击 = 快照基准 ×1.10（略疼；强度大头已移到隐藏面板）
BOSS_BREAK_KIND = "魂殿"        # 走 Boss kind → 已自带 tier4 天材地宝 + 补血丹掉落
#: 九星突破 BOSS 掉火（2026-11-24 重做，用户拍板「按难度从兽火到异火，兽火50/异火100」）。
#: 按玩家突破后的境界分档：低境界只掉兽火，高境界必掉异火（异火稀有，见设计文档 §7/§9）。
#: 千分率口径：兽火 500‰=50%，异火 1000‰=100%（必得）。
BOSS_FLAME_DROP_BEAST = 500     # 兽火档：50% 掉一朵兽火
BOSS_FLAME_DROP_YIHUO = 1000    # 异火档：100% 掉一朵异火（池限 4 朵，见下）
#: 分档境界下标（cfg.realm_index）：低于 BEAST_MAX_REALM 走兽火档，达到则走异火档。
#: 用户「按难度从兽火到异火」+ 调研（异火榜122命中/可遇不可求）⇒ 斗宗(realm 7)为界。
YIHUO_DROP_MIN_REALM = 7        # 斗宗及以上：异火档 100%；斗宗以下：兽火档 50%
#: 异火掉落池（4 朵 B 线数值异火，按 rank 从低到高随境界进池，保留稀有度）。
#: 低档(realm7-8)：海心焰/火云水炎；高档(realm9+)：万兽灵火/玄黄炎。
YIHUO_DROP_POOL = (
    (7, "海心焰"), (7, "火云水炎"), (9, "万兽灵火"), (9, "玄黄炎"),
)
#: 兽火掉落池（九星 BOSS 兽火档）：同样按境界梯度进池，避免 1 级新手直接掉血妖焰火。
#: 门槛 = realm_index（cfg.realm_index）：斗者0/斗师1/大斗师2/斗灵3/斗王4/斗皇5/斗宗6…
#: 弱火（紫火/桃花火）realm0 起；中档（乾蓝水炎/天寒火）realm2 起；强档 realm4 起。
BEAST_DROP_POOL = (
    (0, "紫火"), (0, "桃花火"),
    (2, "乾蓝水炎"), (2, "天寒火"),
    (4, "海魔炎"), (4, "幻金火"),
    (6, "天阴魔火"), (6, "血妖焰火"),
)
#: 兼容旧读取点（旧测试/卡片可能引用）→ 兽火与新口径对齐
BOSS_FLAME_DROP_OTHER = BOSS_FLAME_DROP_BEAST
BOSS_FLAME_DROP_BONUS = 0

#: 野外「对应魔兽掉火」（2026-11-24 用户「改成对应魔兽掉落火焰」）：千分率 500=50%。
#: 只有 MONSTERS 里带 ``flame`` 字段的魔兽才掉（紫晶翼狮王/奎木兽/惊涛龙兽/天骨蟒/
#: 海魔兽/幻火蝎龙兽/天阴三头狮），掉的就是该魔兽对应的那一朵，不是随机池。
#: 血妖焰火无单一魔兽（原著为近千头火属性魔兽血液凝聚）→ 只能靠血妖丹或九星池。
BEAST_MONSTER_DROP_RATE = 500

# ---------------------------------------------------------------------------
# 药材（宝库·随身商店）  [调参，命名取自原著]
# 实机商店 4×6=24 格、8 种图标；此处取 8 种，价格梯度对齐实机「血灵草 120」
# ---------------------------------------------------------------------------
HERBS = [
    {"name": "黑岩根", "price": 60, "desc": "生于火山岩缝，药性沉稳，炼药打底之用"},
    {"name": "紫叶兰", "price": 90, "desc": "叶脉泛紫，清心明目，四品丹药常用"},
    {"name": "血灵草", "price": 120, "desc": "叶片呈褐红色，茎部呈暗灰色，炼药的低阶材料，盛产于天山血潭"},
    {"name": "七叶花", "price": 140, "desc": "七叶一花，花心凝露，最宜温养斗气"},
    {"name": "赤焰果", "price": 170, "desc": "果皮赤红如火，入口如吞炭，炼焚血散的主药"},
    {"name": "冰晶石", "price": 210, "desc": "极北寒潭所结，可压火毒，与赤焰果相济"},
    {"name": "白参", "price": 260, "desc": "千年白参，补气生血，复伤丹之君药"},
    {"name": "地心芝", "price": 320, "desc": "生于地心熔岩之侧，一株千百年，帝品丹方的引子"},
]
# 2026-11-16 原著考据扩充：32 种原著药材（有明确药方的丹药所需）并入宝库
from .recipes_data import NOVEL_HERBS, NOVEL_RECIPES, GRADE_MIN_LV, RECIPES_PER_PAGE  # noqa: E402
from .recipes_data import NOVEL_MATERIALS, MATERIAL_TIER_LV, MATERIAL_DROP_RATE, BOSS_KIND_TOKENS  # noqa: E402
# 描写放 recipes_data：config 已贴 200KB 红线（2026-11-26）
from .recipes_data import BEAST_ELITE_APPEARANCE  # noqa: E402
# 天材地宝索引（服丹/炼化入口 + 分级掉落共用唯一数据源）
MATERIAL_NAMES = [m["name"] for m in NOVEL_MATERIALS]
MATERIAL_BY_NAME = {m["name"]: m for m in NOVEL_MATERIALS}
assert len(MATERIAL_BY_NAME) == len(MATERIAL_NAMES), "天材地宝重名，检查 NOVEL_MATERIALS"

HERBS = HERBS + list(NOVEL_HERBS)
HERB_NAMES = [h["name"] for h in HERBS]
HERB_BY_NAME = {h["name"]: h for h in HERBS}

# ---------------------------------------------------------------------------
# 丹方  [实机名单 + 调参配方]
# 实机「炼药」面板：帝品(灭神丹/九幽散) 四品(回灵丹/破障丹/醒神丹) 五品(复伤丹/风行丹/焚血散)
# ---------------------------------------------------------------------------
RECIPES = [
    {"name": "回灵丹", "grade": "四品", "need": {"血灵草": 2, "紫叶兰": 1},
     "price": 90, "effect": "hp", "value": 1200, "pct": 0.03,
     "desc": "快速恢复 1200 生命"},
    {"name": "破障丹", "grade": "四品", "need": {"赤焰果": 2, "黑岩根": 2},
     "price": 600, "effect": "exp", "value": 3000, "min_lv": 15,
     "desc": "服之冲关，另得 3000 修为"},
    {"name": "醒神丹", "grade": "四品", "need": {"白参": 1, "七叶花": 2},
     "price": 160, "effect": "crit", "value": 3,
     "desc": "醒神凝识，永久暴击 +3%"},
    # 2026-11-20 与风行丹（永久闪避）成对的命中丹：闪避有了对冲词条，
    # 玩家才有一条「打高身法目标」的实路，而不是只能看着怪闪。
    {"name": "凝识丹", "grade": "五品", "need": {"七叶花": 2, "紫叶兰": 1},
     "price": 300, "effect": "hit", "value": 5,
     "desc": "气机凝而不散，永久命中 +5%"},
    {"name": "复伤丹", "grade": "五品", "need": {"白参": 2, "地心芝": 1},
     "price": 280, "effect": "hp", "value": 5000, "pct": 0.05,
     "desc": "大回气血 5000"},
    {"name": "风行丹", "grade": "五品", "need": {"紫叶兰": 3, "冰晶石": 1},
     "price": 300, "effect": "dodge", "value": 5,
     "desc": "身法如风，永久闪避 +5%"},
    {"name": "焚血散", "grade": "五品", "need": {"赤焰果": 3, "冰晶石": 2},
     "price": 320, "effect": "skill", "value": 0,
     "desc": "焚血淬体，升斗技必备之药"},
    {"name": "灭神丹", "grade": "帝品", "need": {"地心芝": 3, "冰晶石": 3, "七叶花": 3},
     "price": 900, "effect": "stat", "value": 60,
     "desc": "帝品丹药，威能无限，取日月之精华，夺天地之造化！永久全属性 +60"},
    {"name": "九幽散", "grade": "帝品", "need": {"黑岩根": 5, "血灵草": 5},
     "price": 800, "effect": "luck", "value": 1200,
     "desc": "九幽所凝，服之机缘 +1200"},
]
# 2026-11-16 原著考据扩充：55 枚原著丹药并入（有药方者走炼制，无药方者纯金币购买）
# 原有 8 枚游戏自设丹方（回灵丹/破障丹…）保留不动，避免破坏既有存档与平衡。
# 每枚补 min_lv（品阶→境界门槛）、buy（金币售价）、src（原著出处）、need 缺省为 {}。
for _nr in NOVEL_RECIPES:
    _nr.setdefault("price", _nr["buy"])   # 市场价沿用金币售价，供商店/备注展示
# 同名冲突必须显式处理，否则炼药卡会出现两张同名丹方。
# 已知唯一冲突：醒神丹 —— 游戏自设四品暴击版被原著第290章三品醒神丹覆盖。
_GAME_RECIPE_NAMES = {r["name"] for r in RECIPES}
_OVERRIDDEN = sorted({r["name"] for r in NOVEL_RECIPES if r["name"] in _GAME_RECIPE_NAMES})
assert _OVERRIDDEN == ["醒神丹"], (
    "原著丹药与游戏自设丹方撞名：%s。请在 config.py 里显式决定保留哪一条，"
    "不要 silent 合并（会出现两张同名卡）。" % "、".join(_OVERRIDDEN or ["<无>"])
)
_MERGED = {r["name"]: r for r in RECIPES}
_MERGED.update({r["name"]: r for r in NOVEL_RECIPES})
RECIPES = list(_MERGED.values())
RECIPE_NAMES = [r["name"] for r in RECIPES]
RECIPE_BY_NAME = {r["name"]: r for r in RECIPES}
#: 别名 → 正名（2026-11-23 去重）。原著「复灵紫丹（复紫灵丹）」是同一枚丹的两种
#: 写法（百度百科·药尘条并列），旧版当两条配方收，炼药名录与图鉴各出现一次。
#: 别名只进索引、不进 RECIPES：老存档包裹里的「复紫灵丹」照样能服、有说明，
#: 但不再单独占一格名录。新增别名只改这张表，别去 RECIPES 里再抄一条。
RECIPE_ALIAS = {"复紫灵丹": "复灵紫丹"}
RECIPE_BY_NAME.update({a: RECIPE_BY_NAME[n] for a, n in RECIPE_ALIAS.items()
                       if n in RECIPE_BY_NAME})
from .recipes_data import GRADE_ORDER  # noqa: E402  高→低全品阶

# 缺字段补齐：游戏自设的 8 枚原本没有 min_lv/buy/src，给默认值让下游统一 .get。
# 2026-11-16：min_lv 兜底不能一律 1——否则帝品「灭神丹/九幽散」、五品「复伤丹」
# 等会在 lv1 卡上出现，违反「等级不够不展示」。按品阶套与原创丹药同一张门槛表；
# 一~四品仍为 1（新手开局四品炼药师，常用回灵丹/破障丹照常可见）。
for _r in RECIPES:
    if "min_lv" not in _r:
        _r["min_lv"] = GRADE_MIN_LV.get(_r.get("grade", ""), 1)
    _r.setdefault("buy", int(_r.get("price", 0)))
    _r.setdefault("src", "游戏自设丹方")
    _r.setdefault("need", {})

# ============================================================================
# 丹纹（2026-11-24）—— 开炉成功时按丹药品阶概率出纹，出纹产「丹名·丹纹」带纹丹。
# 设计文档：docs/炼药模块优化_原著考据与设计_2026-11-24.md §10/§10.5。
# 真源唯一：丹纹率只在这里定义；服丹赐福池在 danwen.py::BLESSINGS。
# ============================================================================
#: 丹纹触发概率（成功之后才掷）。低品高、高品低——原著「品阶越高越稀有」。
DANWEN_BY_GRADE: dict[str, float] = {
    "一品": 0.20, "二品": 0.18, "三品": 0.15, "四品": 0.12, "五品": 0.10,
    "六品": 0.08, "七品": 0.06, "八品": 0.04, "九品": 0.02, "帝品": 0.01,
}
#: 丹纹档位（2026-11-25 用户「改成一纹破厄丹这种，几纹就直接写在丹药名称前面」）。
#: 物品名**自带档位**，存档不新增字段：丹被吃/卖光自然消失，
#: 不会像 ``{物品名: 纹数}`` 那样留下跟背包对不上的脏数据。
DANWEN_TIERS = ("一纹", "二纹", "三纹")
#: 出纹后的档位分布（按顺序累计）：一纹 70% / 二纹 25% / 三纹 5%。
#: 锚原著「二纹、三纹青灵丹」——纹数越高越罕见，但三纹不是必得（避免经济失控）。
DANWEN_TIER_W = (0.70, 0.25, 0.05)
#: 2026-11-24 首版用的是**后缀**（``破厄丹·丹纹`` / ``破厄丹·一纹``），线上存档里还有。
#: 这些只作为「旧名识别 + 迁移源」，不再作为新产出。
DANWEN_LEGACY_SUFFIXES = (("·丹纹", 1), ("·一纹", 1), ("·二纹", 2), ("·三纹", 3))
DANWEN_LEGACY = "·丹纹"           # 旧引用兼容（等价一纹后缀）
DANWEN_SUFFIX = DANWEN_LEGACY     # 旧引用兼容
DANWEN_CAP = 0.20                 # 任何品阶丹纹率硬顶


def danwen_rate(grade: str) -> float:
    """丹纹率（成功之后掷）。未列品阶显式 0.0，不用 or 兜底。"""
    try:
        v = float(DANWEN_BY_GRADE.get(str(grade), 0.0))
    except (TypeError, ValueError):
        return 0.0
    return min(DANWEN_CAP, max(0.0, v))


def danwen_prefix(tier) -> str:
    """档位 → 前缀。越界夹到 1..3，绝不抛异常（结算路径不许因数据脏而炸）。"""
    try:
        i = int(tier)
    except (TypeError, ValueError):
        i = 1
    return DANWEN_TIERS[max(1, min(len(DANWEN_TIERS), i)) - 1]


def danwen_name(base: str, tier) -> str:
    """丹名 + 档位 → 带纹丹名（**档位写在最前面**）。

    两种必须插 ``·`` 隔开的情况（否则会撞上原著丹名，读起来也像另一枚丹）：
      * 基名本身以纹数开头：``三纹青灵丹`` 的一纹版 → ``一纹·三纹青灵丹``；
      * 生成名正好是另一枚原著丹：``青灵丹`` 的三纹版 → ``三纹·青灵丹``
        （不隔开就变成原著那枚「三纹青灵丹」，会把真丹覆盖成带纹丹）。
    """
    p = danwen_prefix(tier)
    b = str(base or "")
    if b.startswith(DANWEN_TIERS):
        return p + "·" + b
    cand = p + b
    other = RECIPE_BY_NAME.get(cand)
    if other is not None and not other.get("danwen"):
        return p + "·" + b
    return cand


def migrate_danwen_name(name: str) -> str:
    """旧后缀带纹丹名 → 新前缀名；不是旧名就原样返回（幂等）。

    2026-11-25 补洞：**抽到孤儿带纹名时退回基础丹**。线上实测抓到
    ``焚血散·一纹`` —— 焚血散是升技材料（``effect="skill"``），注册表按
    ``_DANWEN_SKIP_EFFECTS`` 不给它建带纹版，但旧版炼药出货**不看这个闸**，
    照样掷出了这枚带纹丹 ⇒ 一个既不能服、又不能锁、卖不掉的死物品。
    改名之后若新名同样不在注册表里，就说明它是这类孤儿：
    退回基础丹（数量由 ``core.migrate_danwen_bag`` 累加），别把死名搬过去。
    """
    s = str(name or "")
    for suf, t in DANWEN_LEGACY_SUFFIXES:
        if s.endswith(suf):
            base = s[: -len(suf)]
            new = danwen_name(base, t)
            if new in DANWEN_INDEX:
                return new
            # 孤儿带纹版：基础丹在册就退回基础丹，否则原样（不猜、不吞）
            return base if base in RECIPE_BY_NAME else s
    return s


# 带纹丹：不进 RECIPES / RECIPE_NAMES（丹方卡、宝库、名录都不出现），
# 只注册到 RECIPE_BY_NAME（供 use_item 解析基础药效）与 DANWEN_RECIPE_NAMES
# （供服丹口令匹配）。配方浅拷贝基础丹 + danwen 标记，服下时先走基础药效再挂赐福。
# 🚨 只给「能正常服下去」的丹注册带纹版。这三类若出带纹版，服下**必然**在
#    基础药效那一步失败（兽火单方要 take_item 基础丹、焚血散是升技材料、
#    养魂涎要渡进傀儡），旧版先扣后跑会白吃掉玩家的稀有带纹丹
#    —— 2026-11-24 实测抓出的吞丹洞，别再把它们放开。
_DANWEN_SKIP_EFFECTS = frozenset({"beast_flame", "skill", "puppet_soul"})
#: 公开别名：炼药出货路径（``core.craft`` / ``core.craft_batch``）必须用
#: ``danwen_allowed`` 先过这道闸。2026-11-25 前出货路径不看它，于是焚血散
#: 也能掷出带纹版 —— 而带纹版没注册 ⇒ 线上躺着一枚死物品（实测抓到）。
DANWEN_SKIP_EFFECTS = _DANWEN_SKIP_EFFECTS


def danwen_allowed(rec) -> bool:
    """这枚丹能不能出丹纹？三类「不是吃掉而是另有用途」的丹出纹就是死物品。"""
    return str((rec or {}).get("effect") or "") not in _DANWEN_SKIP_EFFECTS


DANWEN_RECIPE_NAMES: list[str] = []
#: 带纹丹名 → (基础丹名, 纹数)。**唯一真源**：`is_danwen_name` 只查这张表，
#: 不做前缀猜测——否则原著丹名「三纹青灵丹/二纹青灵丹」会被误判成带纹丹。
DANWEN_INDEX: dict[str, tuple[str, int]] = {}


def _register_danwen(base: dict, dn: str, base_name: str, tier: int) -> None:
    r = dict(base)                       # 浅拷贝 need/buy/src 等值；不修改原丹
    r["name"] = dn
    r["danwen"] = True
    r["base_name"] = base_name
    r["danwen_tier"] = tier              # 供 use_item 的赐福文案说清「几纹」
    RECIPE_BY_NAME[dn] = r
    DANWEN_RECIPE_NAMES.append(dn)
    DANWEN_INDEX[dn] = (base_name, tier)


for _base in list(RECIPES):
    if str(_base.get("effect") or "") in _DANWEN_SKIP_EFFECTS:
        continue
    _bn = str(_base["name"])
    # 新名（档位在前）：一纹破厄丹 / 二纹破厄丹 / 三纹破厄丹
    for _t in (1, 2, 3):
        _register_danwen(_base, danwen_name(_bn, _t), _bn, _t)
    # 旧后缀名只为「未迁移存档照样能服」留着（不产出、可迁移）
    for _suf, _t in DANWEN_LEGACY_SUFFIXES:
        _old = _bn + _suf
        if _old in RECIPE_BY_NAME:
            continue
        _register_danwen(_base, _old, _bn, _t)


def is_danwen_name(name: str) -> bool:
    return str(name) in DANWEN_INDEX


def base_of_danwen(name: str) -> str:
    hit = DANWEN_INDEX.get(str(name))
    return hit[0] if hit else str(name)


def danwen_tier(name: str) -> int:
    """物品名 → 纹数（1/2/3）。非带纹丹返回 0；旧后缀按各自纹数。"""
    hit = DANWEN_INDEX.get(str(name))
    return hit[1] if hit else 0


#: 炼药师品阶。下标 0..8 = 一品..九品（**老存档语义不变，绝不整体挪位**）。
#: 2026-11-23 起新号从「未入品」起步（原著：萧炎开局连一品炼药师都不是）：
#: 未入品用独立索引 ALCH_NONE=-1，绝不塞进 ALCHEMIST 表 —— 一塞就得把所有老存档
#: alch 值整体 +1，牵连数据库/测试/傀儡门槛，风险不可控。
ALCHEMIST = [
    ("一品", 400), ("二品", 800), ("三品", 1400), ("四品", 2080),
    ("五品", 3600), ("六品", 6000), ("七品", 9000), ("八品", 13000),
    ("九品", 0),
]
#: 未入品专用索引。⚠️ -1 直接拿去 ALCHEMIST[-1] 会静默取到九品（门槛 0），
#: 导致新号首炉白跳一品、面板显示「九品炼药师」——所有下标访问必须走 alch_tier()。
ALCH_NONE = -1
ALCH_NONE_NAME = "未入品"
#: 新号起步品阶。老存档已有 alch=0..8，不受此常量影响。
ALCH_START = ALCH_NONE
ALCH_MAX = len(ALCHEMIST) - 1


def alch_tier(idx: int) -> tuple[str, int]:
    """炼药师索引 → (品阶名, 升下一阶所需炼药经验)。**所有 alch 下标访问的唯一入口**。

    - ``ALCH_NONE(-1)`` → ``("未入品", 400)``：门槛直接取一品的 ALCHEMIST[0][1]，
      不新造魔数（未入品攒够一品的经验即晋一品）。
    - ``0..ALCH_MAX`` → ALCHEMIST[idx] 原样返回。
    - 其它越界 → 显式 ValueError（最高纪律：不降级、不静默兜底成九品）。
    """
    i = int(idx)
    if i == ALCH_NONE:
        return (ALCH_NONE_NAME, int(ALCHEMIST[0][1]))
    if 0 <= i <= ALCH_MAX:
        name, need = ALCHEMIST[i]
        return (str(name), int(need))
    raise ValueError("非法炼药师品阶索引 %r（合法范围 %d..%d 或 %d=未入品）"
                     % (idx, 0, ALCH_MAX, ALCH_NONE))


def alch_label(idx: int) -> str:
    """品阶展示串。未入品不加「炼药师」后缀（原著里此时尚未入品）。"""
    name, _ = alch_tier(idx)
    return name if int(idx) == ALCH_NONE else ("%s炼药师" % name)

# 炼药成功率：品阶越高越稳（m.java：炼药师等级会影响炼制丹药时的成功几率）
def craft_rate(alch_idx: int) -> float:
    return min(0.95, 0.55 + 0.05 * int(alch_idx))


# ---------------------------------------------------------------------------
# 丹药品阶 ↔ 炼药师品阶（2026-11-18 用户「四品炼药师怎么能炼四品以上，失败
# 概率还一样，符合原著吗」）。原著证据：
#   * 第702章：六品炼药师才「足以炼制」五品丹药 → 品阶决定可炼级别；
#   * 第495章：四品炼药师「偶尔状态巅峰能勉强炼五品，成功率太小」 → 越 1 档
#     可勉强、概率骤降，不是绝对禁止；
#   * 第6章：材料/火种/灵魂感知力三要素，异火提升成功率 → 概率不该是定值。
# 口径：够格用基础成功率；越 1 档 ×0.35（勉强）；越 ≥2 档 ×0.05（几无可能，
# 但仍走同一失败路径给药渣——原著「勉强尝试」而非「系统直接拒绝开炉」）。
# ---------------------------------------------------------------------------
#: 丹药品阶 → 所需炼药师品阶索引（与 ALCHEMIST 下标对齐：一品=0 … 九品=8）
GRADE_ALCH_IDX = {
    "一品": 0, "二品": 1, "三品": 2, "四品": 3, "五品": 4,
    "六品": 5, "七品": 6, "八品": 7, "九品": 8,
    "帝品": 8,   # 帝品在炼药师之上，九品也只能勉强（×0.05）
}
#: 越阶成功率衰减系数：越 1 档「勉强」、越 ≥2 档「几无可能」
OVERREACH_PENALTY = {1: 0.35}
#: 炼低于自身品阶的丹药：每低 1 档成功率加成（高品阶炼低阶十拿九稳）。
#: 用户 2026-11-18「我四品炼药师炼一品丹药，概率还是 70？」——旧版 diff<=0
#: 一律回基础率，导致四品炼一品与炼四品同为 70%，不合原著品阶压制。
LOW_GRADE_BONUS = 0.08
#: 低阶加成后的成功率上限（与 craft_rate 的 0.95 一致）
CRAFT_RATE_CAP = 0.95


def alch_grade_idx(grade: str) -> int:
    """丹药品阶 → 所需炼药师品阶索引；未知品阶按最低（0）兜底，不静默报错。"""
    return int(GRADE_ALCH_IDX.get(str(grade), 0))


#: 丹药功效 → 短标签（唯一源：炼药纯文本 / 卡片共用，杜绝两处口径分叉）。
#: 用户 2026-11-18「怎么没看到恢复斗气的丹药」——旧版只写 desc 文字，玩家认不出
#: 哪枚回斗气，必须在丹名前显式挂一枚 2~3 字短标签。
EFFECT_TAG = {
    "hp": "回血", "mp": "回斗气", "exp": "增修为", "stat": "增属性",
    "luck": "增机缘", "crit": "增暴击", "dodge": "增闪避", "hit": "增命中",
    # 2026-11-23：暴击率 80% 硬顶之后的「增幅战力」词条（炎魔清玄丹）。
    "critdmg": "增暴伤",
    # 2026-11-24 限时增益（原著「短时间内」类：蓄力丹/龙力丹/速灵风丹）。
    "temp_buff": "限时增益",
    # 2026-11-23：帝品雏丹 = 原著「可助人成为斗帝」，不是五维丹。
    "ascend": "成帝",
    # 2026-11-24 兽火单方：承诺是「收服一朵兽火」，不是数值丹。
    "beast_flame": "得兽火",
    "skill": "升级技",
    # 2026-11-21 P3：养魂涎不再按「机缘」糊弄（N08 原著唯一给出的养魂手段，
    # 而旧文案自己写着「无独立灵魂值」—— 玩家看着名字买了个加机缘的丹）。
    "puppet_soul": "养魂",
    # 2026-11-18：三纹青灵丹「助斗师破入大斗师」的专属功效。此前它被写成
    # effect="exp" value=1000，desc 却照抄原著承诺破境 —— 玩家吃八枚只涨修为、
    # 永远破不了大斗师（实机报障）。现在有独立 effect，标签不能再显示「增修为」。
    "breakthrough": "破境",
}

#: 永久百分比加成丹 → (``p["bonus"]`` 键名, 卡面量纲)。
#:
#: 2026-11-23 合并：闪避/暴击/命中曾各写一段一模一样的 use_item 分支，新增「暴伤」
#: 时又要抄第四段。改成表驱动后，加一种永久百分比只改这张表。
#: 读取方各自唯一：``dodge_rate`` / ``crit_rate`` / ``hit_rate`` / ``crit_mul`` ——
#: 卡面、战斗、人物卡都走它们，不许再各算一套（2026-11-20 命中那条的原话）。
PCT_BONUS = {
    "dodge": ("闪避", "永久闪避"),
    "crit": ("暴击", "永久暴击"),
    "hit": ("命中", "永久命中"),
    "critdmg": ("暴伤", "永久暴击伤害"),
}


def effect_tag(effect: str) -> str:
    """丹药 effect → 展示短标签；未知 effect 返回空串（不编造、不污染卡面）。"""
    return str(EFFECT_TAG.get(str(effect) or "", ""))


#: effect → 「本作实际发生什么」的默认模板（``recipe_impl`` 未手写时用它）。
#: 纪律：这里的每一句都必须由结算代码兑现 —— 写进来就等于对外承诺。
IMPL_BY_EFFECT = {
    "hp": "气血 +%d",
    "mp": "斗气 +%d",
    "exp": "修为 +%d",
    "luck": "机缘 +%d",
    "dodge": "永久闪避 +%d%%（自然闪避封顶 80%%，丹药可越顶）",
    "crit": "永久暴击 +%d%%",
    "critdmg": "永久暴击伤害 +%d%%",
    # 无 %d（一次性剧情道具，不是数值丹）—— recipe_impl 对无 % 模板原样返回。
    "ascend": "斗帝突破必成（102→103 不再掷机缘，用掉即止）",
    "hit": "永久命中 +%d%%",
    # 基础模板：真正的 impl 由 recipe_impl 里的 temp_buff 分支按 buff_key/buff_min
    # 拼出「力量 +20%（20 分钟）」——时长必须进承诺，否则又是「短时」不写时长。
    "temp_buff": "限时增益 +%d%%",
    "stat": "全属性 +%d",
    # 默认模板只给量纲；上限写不进单据（模板只吃一个 %d），所以养魂涎在
    # RECIPE_IMPL_OVERRIDE 里手写了「傀儡灵魂 +10（上限 200）」这句承诺。
    "puppet_soul": "傀儡灵魂 +%d",
    "skill": "用于升级斗技",
}


#: 2026-11-23 P1-5：五维丹的定位尾注（唯一源，16 枚 stat 丹全部带上）。
#:
#: 实测（探针对比：裸面板 vs 全属性 +30000）：
#:   * 野外遭遇战 ``monster_scaled(p)``、九星突破 BOSS ``make_breakthrough_boss``
#:     都按**真人面板**反解 —— 吃丹后怪血/怪攻同步变厚（突破 BOSS 血 28002→30954），
#:     相对难度不变，等于白吃；
#:   * 爬塔 ``tower_ref_player(层)``、傀儡遗迹 ``ruin_ref_lv(power_rec)`` 锚标尺，
#:     周本锚周一切片 —— 三处怪数值与当前面板无关，加的全属性是**真赚**。
#:
#: 不写这一行，玩家吃灭神丹去打突破 BOSS 就是隐性假药（2026-11-18 契约）。
_STAT_SCOPE = "爬塔·傀儡遗迹·周本真生效；野外与破境BOSS随你面板变强"


def dodge_final(base: int, p: dict) -> float:
    """闪避收口（%）。``base`` = 自然闪避（等级/异火）+ 丹药闪避。

    2026-11-23 修风行丹死区：旧实现把 ``bonus["闪避"]`` 先加进 base 再 ``min(80,…)``，
    而敏捷自然闪避 **lv82 就顶到 80**（实测 lv78 服丹 80/80、lv82 起恒 80）—— 五品
    风行丹从此完全白吃。现在自然那部分仍封 80，丹药那部分在封顶**之后**另计，
    最多 +8（风行丹只有 +5）。

    🚨 只放开到这里：自然闪避的 80 硬顶是战斗平衡锚（``MON_HIT_RESIDUAL`` 用
    ``level_dodge`` 抵消等级送的身法），不再是「丹药也别想越」。
    """
    pill = int((p.get("bonus") or {}).get("闪避", 0))
    return float(min(80, base - pill) + min(8, pill))


def recipe_min_lv(name: str) -> int:
    """丹方品阶门槛（无丹方 / 药材 → 1）。

    2026-11-23：商店此前只认金币不认门槛，lv1 能买 66 枚破障丹跳过 23 级；
    「炼药名录」早就按它过滤，两处口径必须同源。
    """
    return int((RECIPE_BY_NAME.get(name) or {}).get("min_lv", 1) or 1)


def _with_stat_scope(text: str) -> str:
    """把「全属性 +N」与定位尾注拼成一句。

    多数 stat 丹的 impl 自带括号（「全属性 +5（永久生效）」），直接再挂一对就成了
    「（永久生效）（爬塔…）」；这里并入同一对括号，读起来是一句话。
    """
    t = str(text).rstrip()
    if t.endswith("）"):
        return t[:-1] + "；" + _STAT_SCOPE + "）"
    return t + "（" + _STAT_SCOPE + "）"


def recipe_impl(rec: dict) -> str:
    """这枚丹**在本作里真实会发生什么**（唯一源：卡面 / 「炼药」文本 / 背包共用）。

    2026-11-18 假药事故后的契约：``desc`` 是**原著功效**，不是承诺；``impl`` 才是
    承诺。两者可能不一致（原著有封印/寿命/假死，本作没有这些系统），所以卡面只印
    ``impl``，``desc`` 归到文本层标「原著：」。

    生成顺序：
      1. 配方里手写的 ``impl`` 优先（表示「这条有人审过、与自动口径不同」）；
      2. ``breakthrough`` 按 to_realm / break_boundary / star_chance / max_per_realm 拼；
      3. 其余按 ``IMPL_BY_EFFECT`` 套 value。

    守门 ``tests/test_doupo_recipe_honesty.py`` 会逐条比对：impl 必须非空、且与
    effect 的量纲一致（增修为的不能说气血）。
    """
    hand = str(rec.get("impl") or "").strip()
    eff = str(rec.get("effect") or "")
    if hand:
        return _with_stat_scope(hand) if eff == "stat" else hand
    val = int(rec.get("value") or 0)
    if eff == "breakthrough":
        to = int(rec.get("to_realm") or 1)
        to_name = REALM_NAMES[min(to, len(REALM_NAMES) - 1)]
        parts = []
        if rec.get("break_boundary") and to - 1 >= 0:
            parts.append("%s九星直破%s" % (REALM_NAMES[to - 1], to_name))
        chance = float(rec.get("star_chance") or 0.0)
        if chance >= 0.999:
            parts.append("%s必升一星" % to_name)
        elif chance > 0:
            parts.append("%s%d%%升一星" % (to_name, int(round(chance * 100))))
        max_per = int(rec.get("max_per_realm") or 0)
        text = "，".join(parts) or "破境"
        # 限制用括号附在末尾（不用逗号）：卡面自动折行时不会以「，」开头成孤字。
        if max_per:
            text += "（限 %d 枚）" % max_per
        return text
    # 气血/斗气类：``pct`` 存在时必须把它写进承诺，否则玩家在高境界服下
    # 「回春散 +200」会以为是假药（2026-11-19 掉落改造，实际按上限百分比给）。
    if eff in ("hp", "mp") and float(rec.get("pct") or 0) > 0:
        # 卡面按字符折行，"气血 +200 或上限 12%（取高）" 会被拆成「上限 1 / 2%」，
        # 数字与百分号分家。压到最短：去空格 + 省「（取高）」（「或」已表达取高）。
        base = (IMPL_BY_EFFECT[eff] % val).replace(" ", "")
        return "%s/上限%d%%" % (base, int(round(float(rec["pct"]) * 100)))
    tpl = IMPL_BY_EFFECT.get(eff)
    if tpl:
        # skill 等模板没有 %d（「用于升级斗技」），不能盲目套 %
        out = (tpl % val) if "%" in tpl else tpl
        if eff == "temp_buff":
            # 时长是承诺的一部分（原著「短时间」），必须由配方字段拼出来。
            mins = int(float(rec.get("buff_min") or 0))
            out = "%s +%d%%（%d 分钟）" % (rec.get("buff_key") or "力量", val, mins)
            pen = rec.get("buff_penalty") or {}
            if pen and float(pen.get("min") or 0) > 0:
                out += "；到期后 %d 分钟 %s %d%%" % (
                    int(float(pen["min"])), rec.get("buff_key") or "力量",
                    int(round(float(pen.get("mul") or 0) * 100)))
        return _with_stat_scope(out) if eff == "stat" else out
    return ""


#: 描述里出现这些词，就等于**承诺改变了境界/星级**——必须 effect="breakthrough"，
#: 或者配方里显式手写 impl（表示作者已确认本作无法兑现、并如实标注）。
#: 门禁用它扫全表，杜绝「文案写破境、代码只加修为」的假药复发。
IMPL_PROMISE_WORDS = (
    "提升一星", "提升两星", "提升三星", "提升数星", "升一星", "提升一至两星",
    "提升至", "提升服用者", "提升实力", "提升等级", "突破晋入", "踏入",
    "百分之百凝聚", "凝聚斗之气旋", "可突破", "助人突破", "成功率",
    "短时间", "短时", "萎靡", "透支",
    "寿命", "假死", "封印", "逃生", "火毒", "异火", "紫火", "灵魂",
    "救活", "活命", "还命", "濒死", "化形", "变化形态", "毒物", "变异",
    "丹雷", "潜力", "倍增", "魔兽", "冲击段位",
)


def craft_rate_for(alch_idx: int, grade: str, flame_bonus: float = 0.0) -> tuple[float, int]:
    """某炼药师炼某品阶丹药的真实成功率与品阶差。

    ``flame_bonus``（2026-11-19）：B 线炼药异火的合成加成（小数），**只在最后加一次**，
    全部支线共用 CRAFT_RATE_CAP 封顶；默认 0 → 旧调用逐字节不变。

    返回 ``(rate, diff)``：``diff`` = 丹药所需品阶 − 炼药师品阶。
      diff <  0  高品阶炼低阶：每低 1 档 +LOW_GRADE_BONUS，封顶 CRAFT_RATE_CAP
                 （四品炼一品 = 0.70 + 3×0.08 = 0.94，十拿九稳）
      diff == 0  刚好够格：rate = craft_rate(alch_idx)
      diff == 1  勉强越阶：rate = 基础 ×0.35
      diff >= 2  几无可能：rate = 基础 ×0.05
    """
    base = craft_rate(alch_idx) + max(0.0, float(flame_bonus))
    diff = alch_grade_idx(grade) - int(alch_idx)
    if diff < 0:
        return min(CRAFT_RATE_CAP, base + (-diff) * LOW_GRADE_BONUS), diff
    if diff == 0:
        return min(CRAFT_RATE_CAP, base), diff
    penalty = OVERREACH_PENALTY.get(1 if diff == 1 else 2, 0.05)
    return min(CRAFT_RATE_CAP, base * penalty), diff


#: 兽火单方（引火丹）成功率。**引火不是炼药**：原著萧炎吸收紫火不需要炼药师资格，
#: 若走 craft_rate_for 的越阶惩罚（未入品炼四品 = 0.5×0.05），新号永远拿不到第一朵火，
#: 「入道不送火 + 无火禁炼药」会让业务流程死锁。故单方走独立通道，只受 min_lv 境界门槛。
BEAST_SEAL_RATE = 0.70


def rate_for_recipe(p, rec: dict, flame_bonus: float = 0.0) -> tuple[float, int]:
    """炼丹成功率**唯一入口**：兽火单方走引火通道，其余走炼药师品阶判定。

    两个炼制入口（craft / craft_batch）必须共用本函数——历史上丹塔加成
    只在单发接、批量漏接，就是这个双真源坑（设计文档 §11）。
    """
    if str(rec.get("effect") or "") == "beast_flame":
        return float(BEAST_SEAL_RATE), 0
    return craft_rate_for(p.get("alch", 0), rec.get("grade", ""), flame_bonus)


#: 中文数字（成数表述唯一表，用户 2026-11-18「全部纯中文，不用阿拉伯数字」）
_CN_NUM = ("零", "一", "二", "三", "四", "五", "六", "七", "八", "九", "十")


def rate_cn(rate: float) -> str:
    """成功率 → 纯中文「几成」表述（用户 2026-11-18「不要九成4这种几成几的说法，
    九成四就叫做九成，不用阿拉伯数字，全部纯中文」）。

    一成 = 10%，截断取整到整数成（去掉尾数），像原著口语「几成把握」：
      0.94 → "九成"     0.86 → "八成"     0.78 → "七成"     0.70 → "七成"
      0.245 → "二成"（越1档）   0.035 → "零成"（越2档以上，几无可能）
      1.0 → "十成"      0.0 → "零成"
    数值本身仍精确到 rate 字段，只有展示取整。供丹成回包 / 文本炼药 / 卡片共用。
    """
    r = max(0.0, min(1.0, float(rate)))
    cheng = min(10, int(r * 10 + 1e-9))   # 截断取整：0.94→9、1.0→10；+1e-9 抗浮点误差
    return f"{_CN_NUM[cheng]}成"


#: 炼制耗时（秒）——群里用「冷却」形式表现，不做异步计时器
CRAFT_CD_SEC = 60
#: 炼制失败给多少药渣
CRAFT_FAIL_DROSS = 3
#: 炼制成功给多少炼药经验
CRAFT_EXP = 60

# ---------------------------------------------------------------------------
# 批量连炼（2026-11-19 用户「一次炼制一个太慢，晋升炼药师等级太慢」）
# 原著依据见 docs/炼药加速与快速炼丹_原著考据_2026-11-19.md：
#   * 第70章「炼药师初期，就是靠烧药来得经验」+ 一次囤数百株药材 → 连炼有据；
#   * 第111章一炉一丹、失败报废 → 批量=连续开炉 N 炉，各判成败，非一炉多丹；
#   * 第6章「极其消耗斗气」→ 每炉扣斗气，耗尽提前收炉；
#   * 第1103章「很多炼药师一辈子停留在七品，正是灵魂境界缘故」→ 七品以上
#     晋阶需实战场次（wins+losses）达标，防「刷低级丹冲满级」。
# ---------------------------------------------------------------------------
#: 单次批量连炼炉数上限（防一次消息塞爆与数值溢出）
CRAFT_BATCH_MAX = 10
#: 每炉斗气消耗 = mp_max × 此系数（原著「极其消耗斗气」）
CRAFT_MP_PER = 0.08
#: 低阶经验递减：丹药品阶低于炼药师 N 档时经验乘此系数（N=0/1 不减，N>=2 逐档递减）
#: 四品炼一品（低3档）0.35、低2档 0.6——逼玩家炼接近自身品阶的丹才高效升级。
CRAFT_LOW_EXP_FACTOR = (1.0, 1.0, 0.6, 0.35, 0.2)
#: 七品及以上晋阶的「灵魂境界」门槛（以实战场次 wins+losses 代理）。
#: idx（ALCHEMIST 下标）→ 需要的最低实战场次；未列档不设门槛。
SOUL_REALM_BATTLES = {6: 10, 7: 30, 8: 60}


def low_grade_exp_factor(alch_idx: int, grade_idx: int) -> float:
    """低阶丹经验递减系数。``grade_idx`` = 丹药品阶下标，``alch_idx`` = 炼药师下标。

    diff = grade_idx - alch_idx；diff>=0（本阶/越阶）不减，diff<0 低得越多系数越小，
    最低封到 CRAFT_LOW_EXP_FACTOR 末位。原著：初期烧低阶药得经验，高阶靠灵魂境界，
    狂炼低阶丹不该等同高阶实践。
    """
    diff = int(grade_idx) - int(alch_idx)
    if diff >= 0:
        return 1.0
    k = min(-diff, len(CRAFT_LOW_EXP_FACTOR) - 1)
    return float(CRAFT_LOW_EXP_FACTOR[k])


def soul_realm_ok(p: dict, target_idx: int) -> tuple[bool, int]:
    """晋入 ``target_idx`` 品阶的「灵魂境界」闸：实战场次须达标。

    以 ``wins + losses`` 作灵魂境界代理（原著第1103章灵魂境界、根基裁定同源），
    返回 ``(是否达标, 需要场次)``。未列门槛的档位恒达标。
    """
    need = int(SOUL_REALM_BATTLES.get(int(target_idx), 0))
    have = int(p.get("wins", 0)) + int(p.get("losses", 0))
    return (have >= need), need

# ---------------------------------------------------------------------------
# 怪物  [名字取自 d/enemy.hy 明文；数值调参]
# ---------------------------------------------------------------------------
#: 怪物"种族"——数值按玩家等级动态缩放（见 core.monster_scaled），
#: 表里只保留物种难度，避免"固定数值 + 玩家每 9 级平方跃升"造成的断崖。
#:   hard = 物种难度，直接等于「怪斩杀玩家所需回合 ÷ 玩家斩杀怪所需回合」，越小越难。
#:   exp_mul / gold_mul 只影响产出，越凶的怪给得越多。
#: lv 是「这个物种出现在哪一档玩家面前」，2026-11-14 补全 13 阶后按新等级轴重排
#: （对应 斗之气九段 / 九星斗者 / 九星斗师 / 九星斗灵 / 九星斗皇 / 九星斗尊 / 九星斗圣）。
#: 2026-11-16 扩充：每场遭遇 1~5 个敌人，物种分三类（魔兽 / 强盗 / 原著反派）。
#:   * 名字全部考据原著（百度百科「斗破苍穹势力/魂殿」、B 站「全本资料·魔兽」、
#:     头条「魔兽介绍」），不编造：魂殿等级 = 殿主>天尊>尊老>护法>魂使；
#:     魔兽按一~九阶；穆蛇=狼头佣兵团团长二星斗师；韩枫=黑盟药皇；
#:     地魔老鬼=魔炎谷首领；鹜护法/摘星老鬼=魂殿护法/天尊；魂灭生=魂殿殿主。
#:   * kind 只影响战报文案（"魔兽/悍匪/魂殿"），不参与数值；hard/exp_mul/gold_mul
#:     沿用原 7 只的档位规律（等级越高越难、产出越多），保证既有平衡测试不破。
#:   * 数值仍由 monster_scaled 按玩家等级反解，表内 lv 只决定"在哪一档出现"。
MONSTERS = [
    # ---- 魔兽（按原著阶位，低→高）----
    {"name": "嗜血鼠", "lv": 9, "hard": 0.93, "exp_mul": 1.00, "gold_mul": 0.80, "kind": "魔兽"},
    {"name": "岩蛇", "lv": 14, "hard": 0.92, "exp_mul": 1.00, "gold_mul": 0.85, "kind": "魔兽"},
    {"name": "巡山妖", "lv": 18, "hard": 0.90, "exp_mul": 1.00, "gold_mul": 1.00, "kind": "魔兽"},
    {"name": "暴雪魔猿", "lv": 24, "hard": 0.90, "exp_mul": 1.00, "gold_mul": 1.05, "kind": "魔兽"},
    {"name": "狮翼兽", "lv": 30, "hard": 0.89, "exp_mul": 1.00, "gold_mul": 1.10, "kind": "魔兽"},
    {"name": "赤炎狼", "lv": 33, "hard": 0.89, "exp_mul": 1.00, "gold_mul": 1.10, "kind": "魔兽"},
    {"name": "双头火灵蛇", "lv": 42, "hard": 0.88, "exp_mul": 1.00, "gold_mul": 1.15, "kind": "魔兽"},
    {"name": "紫晶翼狮王", "lv": 45, "hard": 0.87, "exp_mul": 1.00, "gold_mul": 1.20, "kind": "魔兽",
     "flame": "紫火"},
    # ---- 兽火魔兽（2026-11-24 用户「改成对应魔兽掉落火焰」，附件 docs/兽火单方.txt）----
    # 阶位依原著：奎木兽五阶（第216章「淘宝」）、紫晶翼狮王六阶（第131章）、
    # 海魔兽七阶（第1075章「离火焚天」）；惊涛龙兽/天骨蟒原文未明示阶位，按兽火强度插值。
    # 幻火蝎龙兽语料 0 命中（附件新造），按高阶魔兽插值，非原著考据。
    {"name": "奎木兽", "lv": 40, "hard": 0.88, "exp_mul": 1.00, "gold_mul": 1.15, "kind": "魔兽",
     "flame": "桃花火"},
    {"name": "惊涛龙兽", "lv": 58, "hard": 0.86, "exp_mul": 1.05, "gold_mul": 1.30, "kind": "魔兽",
     "flame": "乾蓝水炎"},
    {"name": "天骨蟒", "lv": 66, "hard": 0.86, "exp_mul": 1.05, "gold_mul": 1.32, "kind": "魔兽",
     "flame": "天寒火"},
    {"name": "海魔兽", "lv": 74, "hard": 0.85, "exp_mul": 1.10, "gold_mul": 1.36, "kind": "魔兽",
     "flame": "海魔炎"},
    {"name": "幻火蝎龙兽", "lv": 82, "hard": 0.84, "exp_mul": 1.15, "gold_mul": 1.42, "kind": "魔兽",
     "flame": "幻金火"},
    {"name": "天阴三头狮", "lv": 90, "hard": 0.84, "exp_mul": 1.20, "gold_mul": 1.48, "kind": "魔兽",
     "flame": "天阴魔火"},
    {"name": "黑焰紫云雕", "lv": 57, "hard": 0.87, "exp_mul": 1.00, "gold_mul": 1.25, "kind": "魔兽"},
    {"name": "天山雪蟒", "lv": 63, "hard": 0.86, "exp_mul": 1.00, "gold_mul": 1.30, "kind": "魔兽"},
    # 原著只写「翼黑蛇皇」，无「八翼」二字（语料逐字核对 2026-11-20）
    {"name": "翼黑蛇皇", "lv": 74, "hard": 0.85, "exp_mul": 1.00, "gold_mul": 1.35, "kind": "魔兽"},
    {"name": "云岚山守山兽", "lv": 81, "hard": 0.85, "exp_mul": 1.00, "gold_mul": 1.40, "kind": "魔兽"},
    {"name": "天毒蝎龙兽", "lv": 92, "hard": 0.84, "exp_mul": 1.00, "gold_mul": 1.50, "kind": "魔兽"},
    # ---- 强盗 / 佣兵（原著早期反派，黑角域与魔兽山脉）----
    {"name": "狼头佣兵穆蛇", "lv": 20, "hard": 0.90, "exp_mul": 1.05, "gold_mul": 1.20, "kind": "悍匪"},
    {"name": "血战佣兵", "lv": 36, "hard": 0.89, "exp_mul": 1.05, "gold_mul": 1.25, "kind": "悍匪"},
    {"name": "黑角域散修", "lv": 55, "hard": 0.87, "exp_mul": 1.05, "gold_mul": 1.35, "kind": "悍匪"},
    {"name": "黑盟死士", "lv": 78, "hard": 0.85, "exp_mul": 1.05, "gold_mul": 1.45, "kind": "悍匪"},
    # ---- 原著反派人物（魂殿体系 + 黑盟/魔炎谷）----
    {"name": "魂殿鹜护法", "lv": 60, "hard": 0.86, "exp_mul": 1.20, "gold_mul": 1.40, "kind": "魂殿"},
    {"name": "药皇韩枫", "lv": 69, "hard": 0.85, "exp_mul": 1.25, "gold_mul": 1.45, "kind": "黑盟"},
    {"name": "地魔老鬼", "lv": 76, "hard": 0.85, "exp_mul": 1.25, "gold_mul": 1.50, "kind": "魔炎谷"},
    {"name": "魂殿摘星老鬼", "lv": 90, "hard": 0.84, "exp_mul": 1.35, "gold_mul": 1.60, "kind": "魂殿"},
    {"name": "魂殿执事", "lv": 102, "hard": 0.83, "exp_mul": 1.00, "gold_mul": 1.60, "kind": "魂殿"},
    {"name": "魂灭生", "lv": 103, "hard": 0.83, "exp_mul": 1.50, "gold_mul": 1.80, "kind": "魂殿"},
]

#: 魔兽 → 对应兽火（只有带 ``flame`` 字段的魔兽在表里；野外击杀按它掉火）。
MONSTER_FLAME = {m["name"]: m["flame"] for m in MONSTERS if m.get("flame")}

#: 遭遇战：每场随机遇敌数量 1~5（用户 2026-11-16 需求）。
ENCOUNTER_MIN = 1
ENCOUNTER_MAX = 5
#: 连战中每放倒一个敌人，玩家回复一点气血（避免 5 连战必死，但不回满）。
#: 比例是"最大气血"的百分比；斗气 2026-11-16 起跨战结转、不再回满，此处不动。
ENCOUNTER_REST_HEAL = 0.18
#: 连战中非首敌的属性削弱（每个敌人比单挑略弱一点，连战总难度可控）。
#: 第 i 个敌人（0-based）的难度缩放 = 1 - i * ENCOUNTER_FADE，最多削弱到 80%。
ENCOUNTER_FADE = 0.05

#: 玩家斩杀怪物所需回合。血量按此反解，"几回合打完"是设计值而不是副产品。
TTK_PLAYER = 4.5
#: 怪物斩杀玩家所需回合 = 玩家 TTK × MON_ROUND_RATIO × 物种 hard。
#: 玩家先手，故「怪的斩杀回合 > 玩家回合−1」时玩家取胜 —— 胜负就卡在这个比值上。
#: 全局微调只动 MON_ROUND_RATIO，不要动各物种 hard 的相对差。
MON_ROUND_RATIO = 1.10
#: 怪物每击 ±25% 浮动。没有这个，胜负是整数回合比大小，
#: 胜率只会在 0% / 50% / 100% 之间跳，出不来 70%~99% 这种中间态。
#: 0.25 是扫描出来的值（_balance.py 口径：28 级全部落在 73%~99.5%）。
MON_DMG_VAR = 0.25
#: 怪物防御 = 玩家防御 × 该系数
MON_DEF_RATIO = 0.35
#: 等级差修正：怪比玩家高 1 级，"打完所需回合" +3%（只改时长，不改难度比值）
LV_DELTA_STEP = 0.03
#: 减伤曲线的半值点：``mitigation(d) = MITIGATION_W / (MITIGATION_W + d)``，
#: 防御 = 此值时减伤 50%。提成常量是为了让需要「反解护甲」的地方（傀儡护主）
#: 有一份可引用的真源，而不是在别处再写一个 800 —— 两份常数迟早会漂。
MITIGATION_W = 800.0

# ---------------------------------------------------------------------------
# 命中 · 与闪避对冲的双向词条（2026-11-20 用户）
#   「我觉得应该有一个和闪避对应的词条，不然闪避太超标了」
#   「按照等级来，然后无论是小怪还是精英怪，还是 boos，都应该拥有和玩家
#     一样的隐藏面板，这样战斗逻辑进行时才不会出现个别词条一边倒的情况」
#
# 唯一口径（PVE 双侧同一条，别在 fight/_hit 里各写一份）：
#     实际闪避% = max(0, 目标闪避% − 攻击方命中%)
# 只做减法不做乘法：闪避堆到 80 也还是「80 − 命中」，算得出来的数才解释得清。
#
# 为什么命中按大境界、不按敏捷：敏捷已经同时给闪避与暴击（STAT_BATTLE_ROLE），
# 再让它给命中，两边同步成长 → 命中永远把闪避抵平，"词条"等于没加（假属性）。
# 命中走独立轴：怪物侧只由等级决定，玩家侧靠丹药/塔增益往上堆。
# ---------------------------------------------------------------------------
HERO_HIT = 6.0              #: 玩家命中基础 6%
HERO_HIT_PER_REALM = 1.5    #: 每大境界 +1.5%（刻意慢于怪物身法，留给后天投入）
HIT_CAP = 90.0              #: 命中封顶，与闪避 80 对称留 10 点余量防「必中」

#: 怪物眼力的残值：同等级怪物的命中**正好抵掉玩家靠等级白拿的身法**，只留这么多。
#: 于是「敏捷涨上去自然就能闪」这条路被封死（那是闪避超标的直接原因），
#: 剩下的闪避必须靠异火/丹药真投入 —— 玩家看得懂，也能自己决定要不要堆。
#: 60 是量出来的：再往下压（55）主线胜率就跌破 70% 下限，往上（70）等于没改。
#: 现在定 50 —— 高层实际闪避 80 → 50，主线胜率 96% → 87%（仍在设计区间）；
#: 压到 45 时最低胜率掉到 63%，破了 70% 下限，所以停在这里。
MON_HIT_RESIDUAL = 50.0

#: 怪物隐藏面板（暴击/闪避两项）。只由等级（→ 物种/塔层/周本层）决定，
#: **不进 hp/atk 反解** —— 红线「难度只由层号/战力决定」不动。
#: 数值刻意压在「和玩家早期面板同量级」：这是补对称，不是给怪加难度。
MON_CRIT_BASE = 2.0
MON_CRIT_PER_REALM = 0.8
MON_CRIT_CAP = 25.0
MON_DODGE_BASE = 3.0
MON_DODGE_PER_REALM = 1.6
MON_DODGE_CAP = 32.0
#: 精英 / BOSS 的加成只落在**暴击与身法**上：眼力只由等级决定（同等级同眼力），
#: 否则 BOSS 会连"底子闪避"都扒干净，九星突破这种必打关直接变成必输。
MON_PANEL_ELITE = 1.20
MON_PANEL_BOSS = 1.45

#: 怪物「战斗档位」判定用的 **elite** 关键字。
#: 2026-11-20 修：塔/周本传给 ``monster_scaled`` 的 kind 是 ``"塔·elite"`` /
#: ``"周本·elite"`` —— 只有 ``elite``，没有「精英」两个字（那两个字只在怪名
#: 「精英·嗜血鼠」里）。旧版面板/失误率/技能概率/保底掉落四处都写
#: ``"精英" in kind``，于是精英层全程按**小怪**结算，``MON_PANEL_ELITE`` 成了
#: 死常量。必须同时认 ``elite`` 与 ``精英``；统一走 ``core.mon_tier()``，别再散写。
MON_TIER_ELITE_TOKENS = ("elite", "精英")

#: ⚠️ 「反解怪攻时按基准闪避还原」这条路**试过并否决了**（2026-11-20）：
#: 给 ``m_atk`` 除以 ``(1 - 0.30)`` 对「本来就没被削闪避的低等级」同样放大
#: 1.43 倍 → ``_balance.py`` 全等级越界。闪避超标最终的解法是把「等级白送的
#: 身法」用眼力抵掉（见 ``MON_HIT_RESIDUAL``），**不动 hp/atk 反解**。
#: 别再加 ``DODGE_DESIGN`` 之类的基准补偿常量 —— 难度只由等级/战力/层号决定。

#: 单敌战斗经验除数（exp = exp_need(lv) / EXP_FIGHTS_PER_LV × exp_mul）。
#: 九星制把等级从 29 拉长到 83，若仍是 6 场一级，全局要打 480+ 场（原来约 170 场）。
#: 改成 2 场一级，总场次与改版前基本持平，同时每一星都还有「打两把就升」的节奏。
#:
#: ⚠ 2026-11-22 经验平衡 v0.2 后：本常量**只服务单敌 fight**（遗迹连段 / PVP /
#: 直接调用 monster_scaled 的场合）。野外遭遇战（1~5 敌 encounter）改走下方
#: ENCOUNTER_EXP_BUDGET_DIVISOR 的「整场经验池」，不再逐敌按本常量累加——
#: 否则 5 敌场仍会线性膨胀 2.5 倍（用户点名的直接病灶）。两处真源互不覆写。
EXP_FIGHTS_PER_LV = 2

# ---------------------------------------------------------------------------
# 战斗经验池 / 副本经验锚（2026-11-22 经验产出平衡 v0.2，用户拍板「开始改」）
# 设计见 docs/经验产出平衡评审_2026-11-22.md，数字见 tmp/probe_exp_balance_v2.out.json。
# 铁律：这些数值只有本文件一处定义；core / tower_data / weekly_data 只 import，禁止手抄。
# ---------------------------------------------------------------------------
#: 整场遭遇战修为预算除数 K 的**基础值**：整场总经验 = exp_need(玩家lv)/K(lv) × 异火 × (1+多敌补偿)。
#: 一场战斗的回报由「这场打几只」改成「这场战斗本身」：整场固定预算，按击败份额发。
#:
#: ⚠ 2026-11-23 前快后慢曲线（用户「10天斗帝太快」二轮反馈，拍板 40-60 天）：
#:   K 不再是常数，而是随玩家等级线性增长 —— K(lv)=K_BASE×(1+K_RAMP×(lv-1)/(MAX_LV-1))。
#:   低等级 K=4（≈异火1 时 2.67 场/级，前期不卡）；高等级 K 逐步涨到 ≈48（后期慢）。
#:   K_BASE/K_RAMP 是唯一真源；`core.encounter_exp_divisor(lv)` 是唯一计算函数，
#:   反解/实战/测试都走它，禁止在别处手抄公式。
#:   真跑标定（全功能模拟：每天40场+塔25层+遗迹+周本，即用户看到的那套口径）：
#:   RAMP=6→29天 / 9→37天 / 11→41天 / 12→42天；副本经验（锚exp_need不受RAMP影响）
#:   构成下限，RAMP>11 收益递减，故取 11。
ENCOUNTER_EXP_BUDGET_DIVISOR = 4   # K_BASE：低等级除数（保留原名兼容，语义=基础值）
ENCOUNTER_EXP_K_RAMP = 11          # 高等级除数相对基础值的增量倍数（0=退回恒定K=旧行为）

#: 多敌经验补偿：每多 1 个敌人整场预算 +10%，封顶 +40%（hard 仗不白打，但方差收敛）。
#: 5 敌预算 = 基数×1.4，仍远低于「5×单敌」的旧口径 2.5 倍线性膨胀。
ENCOUNTER_EXP_HARD_STEP = 0.10
ENCOUNTER_EXP_HARD_CAP = 0.40

#: 塔每层修为锚点 = exp_need(min(floor_ref_lv(i), PEAK_LV)) × 本比例，按首通增量发。
#: 0.05 对应探针 tower 19层 20,302 / 60层 601,063；必须封顶 PEAK_LV，
#: 否则 52 层起 floor_ref_lv=103 而 exp_need(103)=0，新增层奖励会归零（假机制）。
TOWER_EXP_REF_RATIO = 0.05

#: 周本每层修为锚点 = exp_need(min(snapshot_lv, PEAK_LV)) × 本比例 × WEEKLY_REWARD_MULT。
#: 0.05 对应探针 lv88 180,691/周；同样必须封顶 PEAK_LV，避免快照 lv103 时归零。
WEEKLY_EXP_SNAPSHOT_RATIO = 0.05

# ---------------------------------------------------------------------------
# 战斗过程丰富化（2026-11-19 用户「不要控制回合」「自动服药」「怪物技能」「意外」）
# ---------------------------------------------------------------------------
#: 异常熔断，**不是玩法回合上限**。双方每回合至少造成 1 点伤害，数学上必分胜负；
#: 这个数只防「闪避率被叠到 100% / 伤害被写成 0」这类脏数据导致的死循环。
#: 触发时按剩余血量比例判定胜负，绝不静默判负。
HARD_ROUND_CAP = 300

#: 自动服药阈值（占自身上限比例）。低于阈值且背包里有未锁定的疗伤丹时服用。
AUTO_PILL_HP_RATIO = 0.35
AUTO_PILL_MP_RATIO = 0.20
#: 每场战斗各自最多自动服用几枚（防止一场嗑光家底）。
AUTO_PILL_MAX_PER_FIGHT = 2

#: 怪物回合失误概率（原著「脚掌一崴，一屁股坐在地上」· 第191章）。
#: 命中则本回合怪不攻击，战报按 kind 选文案。精英/Boss 减半（越强的怪越少犯低级错）。
MISHAP_CHANCE = 0.08
MISHAP_BOSS_FACTOR = 0.5

#: 玩家「心火共鸣」福利（原著「短暂晕眩足以令战局顷刻间转变」· 第1155章）。
#: 每场最多触发一次，持续 1~2 回合；伤害 ×1.3、承伤 ×0.7。
FORTUNE_CHANCE = 0.04
FORTUNE_ROUNDS = (1, 2)
FORTUNE_DMG_MULT = 1.3
FORTUNE_TAKEN_MULT = 0.7

#: 怪物技能基础施放概率（精英 ×1.5、Boss ×2，见 fight 内口径）。
#: 怪物技能属于「回合中追加效果」，不改怪物面板反解（红线：难度仍由层号/战力决定）。
MONSTER_SKILL_BASE_CHANCE = 0.15

# 2026-11-23 BOSS 四技能选择器：每回合至多一条，大招蓄势 + 冷却。
# 三档基础概率见 BREAK_BOSSES[].skills；BOSS ×2 后总和仍受 cap 约束。
BOSS_SKILL_MAX_CHANCE = 0.70
BOSS_ULTIMATE_MIN_ROUND = 3
BOSS_ULTIMATE_COOLDOWN = 4

#: 每种效果结算口径：
#:   burst 本次怪击伤害 ×mult；poison 挂 2 回合 DoT；drain 伤害同时抽走玩家斗气。
MONSTER_SKILLS: dict[str, dict] = {
    # ---- 魔兽 ----
    "嗜血鼠":       {"name": "噬咬",     "chance": 0.15, "effect": "burst",  "mult": 1.4},
    "岩蛇":         {"name": "毒液喷射", "chance": 0.16, "effect": "poison", "mult": 0.08},
    "巡山妖":       {"name": "乱石砸",   "chance": 0.15, "effect": "burst",  "mult": 1.5},
    "暴雪魔猿":     {"name": "狂暴捶击", "chance": 0.20, "effect": "burst",  "mult": 1.6},
    "狮翼兽":       {"name": "狮吼震气", "chance": 0.16, "effect": "drain",  "mult": 0.08},
    "赤炎狼":       {"name": "炎牙撕咬", "chance": 0.17, "effect": "burst",  "mult": 1.5},
    "双头火灵蛇":   {"name": "双头毒噬", "chance": 0.20, "effect": "poison", "mult": 0.10},
    "紫晶翼狮王":   {"name": "紫晶幻芒", "chance": 0.18, "effect": "drain",  "mult": 0.10},
    "黑焰紫云雕":   {"name": "黑焰俯冲", "chance": 0.18, "effect": "burst",  "mult": 1.6},
    "天山雪蟒":     {"name": "寒毒缠身", "chance": 0.19, "effect": "poison", "mult": 0.10},
    "翼黑蛇皇":     {"name": "毒瘴锁空", "chance": 0.21, "effect": "poison", "mult": 0.12},
    "云岚山守山兽": {"name": "撼山撞",   "chance": 0.18, "effect": "burst",  "mult": 1.7},
    "天毒蝎龙兽":   {"name": "蝎龙剧毒", "chance": 0.22, "effect": "poison", "mult": 0.13},
    # ---- 悍匪 / 黑盟 / 魂殿 ----
    "狼头佣兵穆蛇": {"name": "老练突刺", "chance": 0.16, "effect": "burst",  "mult": 1.4},
    "血战佣兵":     {"name": "血战狂砍", "chance": 0.17, "effect": "burst",  "mult": 1.5},
    "黑角域散修":   {"name": "阴邪夺气", "chance": 0.17, "effect": "drain",  "mult": 0.09},
    "黑盟死士":     {"name": "死士搏命", "chance": 0.20, "effect": "burst",  "mult": 1.7},
    "魂殿鹜护法":   {"name": "摄魂术",   "chance": 0.19, "effect": "drain",  "mult": 0.10},
    "药皇韩枫":     {"name": "毒蛊",     "chance": 0.20, "effect": "poison", "mult": 0.12},
    "地魔老鬼":     {"name": "魔焰吞噬", "chance": 0.21, "effect": "drain",  "mult": 0.12},
    "魂殿摘星老鬼": {"name": "摘星手",   "chance": 0.22, "effect": "burst",  "mult": 1.7},
    "魂殿执事":     {"name": "锁魂链",   "chance": 0.20, "effect": "drain",  "mult": 0.10},
    "魂灭生":       {"name": "灭世魂掌", "chance": 0.25, "effect": "burst",  "mult": 1.8},
}

#: 塔/周本虚拟 boss 与精英的技能（species 没有 MONSTERS 条目时按 kind 取）。
SKILL_BY_KIND = {
    "boss": {"name": "魂殿秘技", "chance": 0.28, "effect": "drain", "mult": 0.12},
    "elite": {"name": "凶兽扑杀", "chance": 0.22, "effect": "burst", "mult": 1.5},
}

# ---------------------------------------------------------------------------
# 九星突破 BOSS 角色库（2026-11-20 用户「别写快照玩法」「BOSS 登场要有引言台词」）
# ---------------------------------------------------------------------------
#: 12 个跨境界突破档各配一名原著 BOSS。台词/招式名全部有原文出处，见
#: docs/战斗BOSS体系重做_原著考据_2026-11-20.md（含章节号与引文）。
#: 魔兽无本人台词者用"威压旁白"，**不代编**；语料无据的「八翼」「帝印诀」
#: 「魂殿锁魂」「魔音」一律不用。
#: 字段：
#:   intro   —— 登场引言（沉浸式"突如其来的惊喜/惊吓"由它承载）
#:   quotes  —— 技能/战败时滚动播出的原著台词
#:   skill   —— 专属技能（沿用 effect: burst/poison/drain，不改数值反解）
#:   mech    —— "恶心机制"：复用 skill_effects 的 t/v/d/p；skill 命中时挂到玩家
#:   source  —— 原著章节出处（守门测试断言非空）
BREAK_BOSSES: tuple[dict, ...] = (
    {"name": "药老·药尘", "title": "黑袍炼药师", "source": "第九章 药老！",
     "intro": "「就是因为没有，才能显出我这功法的独特啊！」黑袍人负手而立，笑意深不可测。",
     "quotes": ("就是因为没有，才能显出我这功法的独特啊！",
                "你体内的基础，早在三年前便已牢固可靠"),
     "skill": {"name": "焚决", "chance": 0.22, "effect": "drain", "mult": 0.08},
     "mech": {"t": "reflect", "v": 0.18, "d": 2, "p": 0.30, "kind": "焚决·焚脉"},
     "skills": [
         {"name": "焚决", "chance": 0.065, "effect": "drain", "mult": 0.08,
          "mech": {"t": "reflect", "v": 0.18, "d": 2, "p": 0.30, "kind": "焚决·焚脉"}},
         {"name": "骨灵冷火", "chance": 0.060, "effect": "poison", "mult": 0.04, "mech": None},
         {"name": "丹雷·火印", "chance": 0.055, "effect": "burst", "mult": 1.20,
          "mech": {"t": "debuff_atk", "v": 0.12, "d": 2, "p": 0.15, "kind": "丹雷·压息"}},
         {"name": "骨灵冷火·丹雷", "chance": 0.040, "effect": "burst", "mult": 1.60,
          "mech": {"t": "silence", "v": 1, "d": 2, "p": 0.15, "kind": "冷火封脉"},
          "ultimate": True, "cooldown": BOSS_ULTIMATE_COOLDOWN},
     ]},
    {"name": "狼头佣兵·穆蛇", "title": "狼头佣兵团团长", "source": "原著早期·魔兽山脉",
     "intro": "三道身影拦住去路，为首者气息沉凝，腰间弯刀随呼吸轻轻起伏。",
     "quotes": (),
     "skill": {"name": "老练突刺", "chance": 0.20, "effect": "burst", "mult": 1.5},
     "mech": {"t": "debuff_heal", "v": 0.25, "d": 2, "p": 0.28, "kind": "断筋·封脉"},
     "skills": [
         {"name": "狼头佣兵团·围杀", "chance": 0.060, "effect": "burst", "mult": 1.50,
          "mech": {"t": "debuff_heal", "v": 0.25, "d": 2, "p": 0.28, "kind": "断筋·封脉"}},
         {"name": "佣兵·血战", "chance": 0.055, "effect": "burst", "mult": 1.40, "mech": None},
         {"name": "穆蛇·刀锋", "chance": 0.050, "effect": "drain", "mult": 0.08,
          "mech": {"t": "debuff_atk", "v": 0.15, "d": 2, "p": 0.20, "kind": "刀锋·压势"}},
         {"name": "狼头佣兵团·血刃", "chance": 0.035, "effect": "burst", "mult": 2.00,
          "mech": {"t": "stun", "v": 1, "d": 2, "p": 0.20, "kind": "狼头·血刃"},
          "ultimate": True, "cooldown": BOSS_ULTIMATE_COOLDOWN},
     ]},
    {"name": "云岚宗·云棱", "title": "云岚宗大长老", "source": "第三百三十四章 三年之约！",
     "intro": "「我是云岚宗的大长老，云棱。」话音未落，剑气已至。",
     "quotes": ("我是云岚宗的大长老，云棱。", "生死，各安天命。"),
     "skill": {"name": "青木剑诀", "chance": 0.24, "effect": "burst", "mult": 1.6},
     "mech": {"t": "silence", "v": 1, "d": 2, "p": 0.25, "kind": "青木·封剑势"},
     "skills": [
         {"name": "青木剑诀", "chance": 0.070, "effect": "burst", "mult": 1.60,
          "mech": {"t": "silence", "v": 1, "d": 2, "p": 0.25, "kind": "青木·封剑势"}},
         {"name": "剑气·青木", "chance": 0.065, "effect": "burst", "mult": 1.45, "mech": None},
         {"name": "云岚·剑气", "chance": 0.060, "effect": "drain", "mult": 0.09,
          "mech": {"t": "debuff_atk", "v": 0.18, "d": 2, "p": 0.20, "kind": "云岚·压剑"}},
         {"name": "云岚·剑诀", "chance": 0.045, "effect": "burst", "mult": 2.10,
          "mech": {"t": "silence", "v": 1, "d": 2, "p": 0.24, "kind": "云岚·封剑阵"},
          "ultimate": True, "cooldown": BOSS_ULTIMATE_COOLDOWN},
     ]},
    {"name": "黑盟死士", "title": "黑盟·死士", "source": "第五百九十三章 破封！",
     "intro": "黑袍人吐出一口血，厉喝连黑盟的人也敢动、找死不成，反手已欺身至前。",
     "quotes": ("黑盟不会放过你们的！",),  # 前一句原文用直角引号，此处只放逐字命中句
     "skill": {"name": "死士搏命", "chance": 0.26, "effect": "burst", "mult": 1.8},
     "mech": {"t": "reflect", "v": 0.30, "d": 2, "p": 0.35, "kind": "搏命·反噬"},
     "skills": [
         {"name": "黑盟·破封", "chance": 0.075, "effect": "burst", "mult": 1.80,
          "mech": {"t": "reflect", "v": 0.30, "d": 2, "p": 0.35, "kind": "搏命·反噬"}},
         {"name": "死士·血祭", "chance": 0.070, "effect": "burst", "mult": 1.50, "mech": None},
         {"name": "黑盟·杀伐", "chance": 0.065, "effect": "poison", "mult": 0.10,
          "mech": {"t": "dot", "v": 0.15, "d": 2, "p": 0.22, "kind": "黑盟·血毒"}},
         {"name": "黑盟·血刃", "chance": 0.050, "effect": "burst", "mult": 2.20,
          "mech": {"t": "reflect", "v": 0.25, "d": 2, "p": 0.25, "kind": "血刃·反噬"},
          "ultimate": True, "cooldown": BOSS_ULTIMATE_COOLDOWN},
     ]},
    {"name": "紫晶翼狮王", "title": "六阶魔兽", "source": "第一百三十一章 神秘女人与六阶魔兽",
     "intro": "虚空为之一颤，紫晶覆身的巨兽踏空而来，四爪下山石寸寸龟裂。",
     "quotes": (),  # 魔兽无台词
     "skill": {"name": "紫晶幻芒", "chance": 0.24, "effect": "drain", "mult": 0.10},
     "mech": {"t": "reflect", "v": 0.25, "d": 2, "p": 0.30, "kind": "紫晶·反震"},
     "skills": [
         {"name": "紫晶翼狮王", "chance": 0.070, "effect": "drain", "mult": 0.10,
          "mech": {"t": "reflect", "v": 0.22, "d": 2, "p": 0.22, "kind": "紫晶·反震"}},
         {"name": "紫晶·狮吟", "chance": 0.065, "effect": "burst", "mult": 1.10, "mech": None},
         {"name": "狮吼·紫晶", "chance": 0.060, "effect": "burst", "mult": 1.20,
          "mech": {"t": "debuff_atk", "v": 0.10, "d": 2, "p": 0.10, "kind": "狮吼·压息"}},
         {"name": "紫晶翼狮·狮吼", "chance": 0.045, "effect": "burst", "mult": 1.50,
          "mech": {"t": "stun", "v": 1, "d": 2, "p": 0.10, "kind": "紫晶·震魄"},
          "ultimate": True, "cooldown": BOSS_ULTIMATE_COOLDOWN},
     ]},
    {"name": "魂殿鹜护法", "title": "魂殿护法", "source": "第六百十四章 鹜护法 / 第六百九十二章",
     "intro": "「桀桀，我魂殿可有的是手段收拾你。」黑雾无声漫过，笑声从四面八方响起。",
     "quotes": ("桀桀，我魂殿可有的是手段收拾你",
                "本护法便等着看一场好戏吧",
                "药尘，难道你以为以我现在的实力，你还能是我对手不成？"),
     "skill": {"name": "摄魂术", "chance": 0.24, "effect": "drain", "mult": 0.11},
     "mech": {"t": "silence", "v": 1, "d": 2, "p": 0.28, "kind": "摄魂·禁技"},
     "skills": [
         {"name": "黑雾·魂殿", "chance": 0.070, "effect": "drain", "mult": 0.11,
          "mech": {"t": "silence", "v": 1, "d": 2, "p": 0.28, "kind": "黑雾·锁魂"}},
         {"name": "锁链·黑雾", "chance": 0.065, "effect": "burst", "mult": 1.35, "mech": None},
         {"name": "鹜护法·黑雾", "chance": 0.060, "effect": "poison", "mult": 0.10,
          "mech": {"t": "debuff_heal", "v": 0.25, "d": 2, "p": 0.22, "kind": "魂殿·蚀脉"}},
         {"name": "万魂噬灵", "chance": 0.045, "effect": "burst", "mult": 1.90,
          "mech": {"t": "silence", "v": 1, "d": 2, "p": 0.20, "kind": "万魂·禁技"},
          "ultimate": True, "cooldown": BOSS_ULTIMATE_COOLDOWN},
     ]},
    {"name": "地魔老鬼", "title": "魔炎谷首领·斗宗", "source": "第百六十章 地魔老鬼",
     "intro": "「他杀了我魔炎谷三大长老，这笔账，不得不算。」老鬼踏火而来，声如闷雷。",
     "quotes": ("这笔账，不得不算", "今日老夫前来的目的，你应该也清楚"),
     "skill": {"name": "魔焰吞噬", "chance": 0.26, "effect": "drain", "mult": 0.12},
     "mech": {"t": "debuff_heal", "v": 0.35, "d": 2, "p": 0.32, "kind": "魔焰·灼脉"},
     "skills": [
         {"name": "魔炎谷·魔焰", "chance": 0.075, "effect": "drain", "mult": 0.12,
          "mech": {"t": "debuff_heal", "v": 0.35, "d": 2, "p": 0.32, "kind": "魔焰·灼脉"}},
         {"name": "地魔老鬼·魔炎", "chance": 0.070, "effect": "burst", "mult": 1.70, "mech": None},
         {"name": "老鬼·血祭", "chance": 0.065, "effect": "poison", "mult": 0.11,
          "mech": {"t": "debuff_atk", "v": 0.20, "d": 2, "p": 0.22, "kind": "血祭·焚息"}},
         {"name": "魔炎谷·血祭", "chance": 0.050, "effect": "burst", "mult": 2.20,
          "mech": {"t": "debuff_heal", "v": 0.30, "d": 2, "p": 0.26, "kind": "魔炎·封疗"},
          "ultimate": True, "cooldown": BOSS_ULTIMATE_COOLDOWN},
     ]},
    {"name": "魂殿摘星老鬼", "title": "魂殿天尊·五星斗尊巅峰", "source": "第一千一百九十九章 摘星老鬼",
     "intro": "「你才是大鱼……既然老夫已经露面，怎还能让你们安然离去？」笑面老者踏锁链而来。",
     "quotes": ("你才是大鱼", "既然老夫已经露面，怎还能让你们安然离去？",
                "在绝对的实力面前，这些都没有用", "九幽魂手！"),
     "skill": {"name": "九幽魂手", "chance": 0.28, "effect": "drain", "mult": 0.12},
     "mech": {"t": "stun", "v": 1, "d": 2, "p": 0.25, "kind": "九幽·锁魂（空间崩塌）"},
     "skills": [
         {"name": "摘星手", "chance": 0.080, "effect": "burst", "mult": 1.50,
          "mech": {"t": "stun", "v": 1, "d": 2, "p": 0.20, "kind": "摘星·锁空"}},
         {"name": "九幽魂手", "chance": 0.075, "effect": "drain", "mult": 0.11,
          "mech": {"t": "silence", "v": 1, "d": 2, "p": 0.18, "kind": "九幽·禁技"}},
         {"name": "空间崩塌·锁链", "chance": 0.070, "effect": "burst", "mult": 1.35, "mech": None},
         {"name": "九幽魂手·空间崩塌", "chance": 0.050, "effect": "drain", "mult": 0.12,
          "mech": {"t": "stun", "v": 1, "d": 2, "p": 0.20, "kind": "九幽·锁魂（空间崩塌）"},
          "ultimate": True, "cooldown": BOSS_ULTIMATE_COOLDOWN},
     ]},
    {"name": "魂殿殿主·魂灭生", "title": "魂殿殿主", "source": "第一千五百一十二章 萧炎vs魂殿殿主",
     "intro": "「三局两平，有些出乎本殿的意料，不过这应该无法改变最后的结局。」殿主端坐黑雾之上。",
     "quotes": ("三局两平，有些出乎本殿的意料", "成大事，若是在乎这些，终究是小家子气"),
     "skill": {"name": "大天造化掌", "chance": 0.30, "effect": "burst", "mult": 1.7},
     "mech": {"t": "stun", "v": 1, "d": 2, "p": 0.32, "kind": "造化掌·镇身"},
     "skills": [
         {"name": "魂灭生·造化掌", "chance": 0.085, "effect": "burst", "mult": 1.80,
          "mech": {"t": "stun", "v": 1, "d": 2, "p": 0.32, "kind": "造化掌·镇身"}},
         {"name": "殿主·魂殿", "chance": 0.080, "effect": "drain", "mult": 0.11, "mech": None},
         {"name": "黑雾·杀伐", "chance": 0.075, "effect": "burst", "mult": 1.60,
          "mech": {"t": "silence", "v": 1, "d": 2, "p": 0.20, "kind": "殿主·封技"}},
         {"name": "大天造化掌", "chance": 0.060, "effect": "burst", "mult": 2.30,
          "mech": {"t": "stun", "v": 1, "d": 2, "p": 0.28, "kind": "造化掌·镇魂"},
          "ultimate": True, "cooldown": BOSS_ULTIMATE_COOLDOWN},
     ]},
    {"name": "魂灭生·真身", "title": "魂殿殿主·全力", "source": "第一千四百七十一章 魂殿殿主！",
     "intro": "「你的灵魂力量很强，但可惜，本体太弱。」黑雾骤然倒卷，殿主的真身终于降临。",
     "quotes": ("你的灵魂力量很强，但可惜，本体太弱", "成大事，若是在乎这些，终究是小家子气"),
     "skill": {"name": "大天造化掌", "chance": 0.32, "effect": "burst", "mult": 1.8},
     "mech": {"t": "silence", "v": 1, "d": 2, "p": 0.30, "kind": "殿主·镇魂印"},
     "skills": [
         {"name": "灵魂力量·镇魂", "chance": 0.090, "effect": "drain", "mult": 0.13,
          "mech": {"t": "silence", "v": 1, "d": 2, "p": 0.30, "kind": "殿主·镇魂印"}},
         {"name": "本体·黑雾", "chance": 0.085, "effect": "burst", "mult": 1.90, "mech": None},
         {"name": "万魂噬灵", "chance": 0.080, "effect": "poison", "mult": 0.12,
          "mech": {"t": "debuff_heal", "v": 0.30, "d": 2, "p": 0.24, "kind": "万魂·蚀脉"}},
         {"name": "大天造化掌·殿主", "chance": 0.055, "effect": "burst", "mult": 2.40,
          "mech": {"t": "stun", "v": 1, "d": 2, "p": 0.30, "kind": "造化掌·真身"},
          "ultimate": True, "cooldown": BOSS_ULTIMATE_COOLDOWN},
     ]},
    {"name": "美杜莎女王", "title": "蛇人族女王", "source": "第两百一十五章 进化开始",
     "intro": "「若是进化成功，今日此处的所有人类，都得永留沙漠！」七彩光芒自蛇人族女王身上冲天而起。",
     "quotes": ("若是进化成功，今日此处的所有人类，都得永留沙漠！", "抱歉，不能。",
                "没想到你竟然还能知道这些东西"),
     "skill": {"name": "七彩吞天蟒", "chance": 0.30, "effect": "poison", "mult": 0.14},
     "mech": {"t": "dot", "v": 0.18, "d": 3, "p": 0.35, "kind": "七彩毒瘴", "base": 1},
     "skills": [
         {"name": "七彩吞天蟒", "chance": 0.085, "effect": "poison", "mult": 0.14,
          "mech": {"t": "dot", "v": 0.18, "d": 3, "p": 0.35, "kind": "七彩毒瘴"}},
         {"name": "美杜莎女王·蛇尾", "chance": 0.080, "effect": "burst", "mult": 1.60, "mech": None},
         {"name": "蛇人族·七彩", "chance": 0.075, "effect": "poison", "mult": 0.11,
          "mech": {"t": "debuff_atk", "v": 0.20, "d": 2, "p": 0.22, "kind": "七彩·压息"}},
         {"name": "七彩吞天蟒·蛇尾", "chance": 0.060, "effect": "burst", "mult": 2.00,
          "mech": {"t": "stun", "v": 1, "d": 2, "p": 0.26, "kind": "七彩·锁身"},
          "ultimate": True, "cooldown": BOSS_ULTIMATE_COOLDOWN},
     ]},
    {"name": "魂天帝", "title": "魂族族长·最终 BOSS", "source": "第两百一十六章 进化成功？（天地异象）",
     "intro": "天地能量骤然暴动，乌云压顶，银蛇在云层间乱窜，整片空间都在低鸣。",
     "quotes": (),
     "skill": {"name": "大天造化掌", "chance": 0.34, "effect": "burst", "mult": 1.9},
     "mech": {"t": "silence", "v": 1, "d": 2, "p": 0.35, "kind": "帝威·封技"},
     "skills": [
         {"name": "魂天帝·魂族", "chance": 0.095, "effect": "burst", "mult": 1.80,
          "mech": {"t": "silence", "v": 1, "d": 2, "p": 0.35, "kind": "帝威·封技"}},
         {"name": "万魂噬灵", "chance": 0.090, "effect": "drain", "mult": 0.12, "mech": None},
         {"name": "魂族·杀伐", "chance": 0.085, "effect": "burst", "mult": 1.70,
          "mech": {"t": "debuff_atk", "v": 0.22, "d": 2, "p": 0.24, "kind": "魂族·压势"}},
         {"name": "斩帝鬼血刃", "chance": 0.060, "effect": "burst", "mult": 2.50,
          "mech": {"t": "stun", "v": 1, "d": 2, "p": 0.30, "kind": "鬼血刃·镇身"},
          "ultimate": True, "cooldown": BOSS_ULTIMATE_COOLDOWN},
     ]},
)

#: 12 档突破对应的 BOSS 序号 = realm_index(lv)（0=斗之气 … 11=斗圣，共 12 档）。
#: 索引越界时钳到最后一档，保证 lv 边界不崩。
def break_boss_for_lv(lv: int) -> dict:
    idx = max(0, min(len(BREAK_BOSSES) - 1, realm_index(int(lv))))
    return BREAK_BOSSES[idx]


# ---------------------------------------------------------------------------
# 野外 / 塔层 / 周本 精英与 BOSS 角色包（2026-11-20 用户
# 「每一个精英怪和 BOOS 都要有各自的技能……有机制的」）
# ---------------------------------------------------------------------------
#: 九星突破的 12 档已由 BREAK_BOSSES 覆盖；这里补齐**其余所有会遇到的怪**。
#: 铁律同 BREAK_BOSSES：
#:   * quotes 只放逐字核对过原文的连续短句，语料没有的一律空元组，不代编；
#:     「游戏原创」怪（魂殿执事/黑盟死士/塔灵等）明确标 source，不冒充原著角色。
#:   * intro 是登场旁白；普通怪 intro 留空，避免一场 5 连战刷 5 条旁白。
#:   * mech 复用 skill_effects 的 t/v/d/p/kind；silence/stun 的 d≥2 保证下回合真生效。
#:   * skill 只改名字/表现，数值口径与既有 MONSTER_SKILLS 一致，不重算面板。
_CHAR_ZHAIXING = {
    "name": "摘星老鬼", "title": "魂殿天尊·五星斗尊巅峰",
    "source": "第一千一百九十九章 摘星老鬼", "elite": True,
    "intro": "「呵呵，终于打算拼命了么？」黑雾漫过锁魂殿，笑面老者缓步而来。",
    "quotes": ("呵呵，终于打算拼命了么？", "摘星手！",
               "在绝对的实力面前，这些都没有用"),
    "skill": {"name": "摘星手", "chance": 0.22, "effect": "burst", "mult": 1.7},
    "mech": {"t": "stun", "v": 1, "d": 2, "p": 0.25, "kind": "摘星·空间崩塌"},
}
_CHAR_DIMO = {
    "name": "地魔老鬼", "title": "魔炎谷首领·斗宗",
    "source": "第百六十章 地魔老鬼", "elite": True,
    "intro": "「这笔账，不得不算。」老鬼踏火而来，声如闷雷。",
    "quotes": ("这笔账，不得不算", "今日老夫前来的目的，你应该也清楚"),
    "skill": {"name": "魔焰吞噬", "chance": 0.21, "effect": "drain", "mult": 0.12},
    "mech": {"t": "debuff_heal", "v": 0.35, "d": 2, "p": 0.32, "kind": "魔焰·灼脉"},
}
_CHAR_HANFENG = {
    "name": "韩枫", "title": "药皇",
    "source": "原著·黑盟篇（语料逐字核对：韩枫其名）", "elite": True,
    "intro": "药皇袍角翻飞，袖中火影一闪即逝，周身浮起一层灼人的血色斗气。",
    "quotes": (),
    "skill": {"name": "毒蛊", "chance": 0.20, "effect": "poison", "mult": 0.12},
    "mech": {"t": "dot", "v": 0.15, "d": 3, "p": 0.30, "kind": "药皇·蛊毒", "base": 1},
}
_CHAR_MUSHE = {
    "name": "穆蛇", "title": "狼头佣兵团团长",
    "source": "原著早期·魔兽山脉", "elite": True,
    "intro": "三道身影拦住去路，为首者气息沉凝，腰间弯刀随呼吸轻轻起伏。",
    "quotes": (),
    "skill": {"name": "老练突刺", "chance": 0.16, "effect": "burst", "mult": 1.4},
    "mech": {"t": "debuff_heal", "v": 0.25, "d": 2, "p": 0.28, "kind": "断筋·封脉"},
}

MONSTER_CHARACTERS: dict[str, dict] = {
    # ---- 野外魔兽：低阶无旁白，高阶精英各有恶心机制 ----
    "嗜血鼠": {"name": "嗜血鼠", "title": "魔兽山脉·低阶魔兽",
               "source": "原著世界观（游戏命名）", "intro": "", "quotes": (),
               "skill": {"name": "噬咬", "chance": 0.15, "effect": "burst", "mult": 1.4},
               "mech": None},
    "岩蛇": {"name": "岩蛇", "title": "魔兽山脉·低阶魔兽",
             "source": "原著世界观（游戏命名）", "intro": "", "quotes": (),
             "skill": {"name": "毒液喷射", "chance": 0.16, "effect": "poison", "mult": 0.08},
             "mech": None},
    "巡山妖": {"name": "巡山妖", "title": "山林巡守",
               "source": "原著世界观（游戏命名）", "intro": "", "quotes": (),
               "skill": {"name": "乱石砸", "chance": 0.15, "effect": "burst", "mult": 1.5},
               "mech": None},
    "暴雪魔猿": {"name": "暴雪魔猿", "title": "魔兽山脉·中阶魔兽",
                 "source": "原著世界观（游戏命名）", "intro": "", "quotes": (),
                 "skill": {"name": "狂暴捶击", "chance": 0.20, "effect": "burst", "mult": 1.6},
                 "mech": None},
    "狮翼兽": {"name": "狮翼兽", "title": "魔兽山脉·中阶魔兽",
               "source": "原著世界观（游戏命名）", "intro": "", "quotes": (),
               "skill": {"name": "狮吼震气", "chance": 0.16, "effect": "drain", "mult": 0.08},
               "mech": None},
    "赤炎狼": {"name": "赤炎狼", "title": "魔兽山脉·中阶魔兽",
               "source": "原著世界观（游戏命名）", "intro": "", "quotes": (),
               "skill": {"name": "炎牙撕咬", "chance": 0.17, "effect": "burst", "mult": 1.5},
               "mech": None},
    "双头火灵蛇": {"name": "双头火灵蛇", "title": "异火伴生魔兽", "elite": True,
                   "source": "原著世界观（游戏命名，语料未见同名魔兽）",
                   "intro": "两颗蛇首自火中探出，吐息间炎浪翻卷，岩壁被烧得通红。",
                   "quotes": (),
                   "skill": {"name": "双头毒噬", "chance": 0.20, "effect": "poison", "mult": 0.10},
                   "mech": {"t": "dot", "v": 0.12, "d": 2, "p": 0.24,
                            "kind": "双头·炎毒", "base": 1}},
    "紫晶翼狮王": {"name": "紫晶翼狮王", "title": "六阶魔兽", "elite": True,
                   "source": "第一百三十一章 神秘女人与六阶魔兽",
                   "intro": "虚空为之一颤，紫晶覆身的巨兽踏空而来，四爪下山石寸寸龟裂。",
                   "quotes": (),
                   "skill": {"name": "紫晶幻芒", "chance": 0.18, "effect": "drain", "mult": 0.10},
                   "mech": {"t": "reflect", "v": 0.25, "d": 2, "p": 0.30, "kind": "紫晶·反震"}},
    # ---- 兽火魔兽角色包（2026-11-24 对应魔兽掉火；台词/机制按原著调性）----
    "奎木兽": {"name": "奎木兽", "title": "五阶木系魔兽", "elite": True,
               "source": "第两百十六章「淘宝」（五阶木系魔兽，桃花火寄于体内）",
               "intro": "林中藤影骤然收束，一头覆着青木鳞甲的巨兽自树影里探出头来，"
                        "周身草木气息温顺却厚重。",
               "quotes": (),
               "skill": {"name": "万木缠身", "chance": 0.18, "effect": "burst", "mult": 1.5},
               "mech": {"t": "debuff_heal", "v": 0.30, "d": 2, "p": 0.25, "kind": "木气·回春"}},
    "惊涛龙兽": {"name": "惊涛龙兽", "title": "水属魔兽", "elite": True,
                 "source": "第三百一十七章「各显神通」（乾蓝水炎出自其体）",
                 "intro": "水面轰然炸开，一头蓝鳞蛟龙破浪而出，鳞隙间流转着幽蓝水炎。",
                 "quotes": (),
                 "skill": {"name": "惊涛裂岸", "chance": 0.19, "effect": "burst", "mult": 1.7},
                 "mech": {"t": "debuff_atk", "v": 0.16, "d": 2, "p": 0.26, "kind": "水压·滞脉"}},
    "天骨蟒": {"name": "天骨蟒", "title": "骨灵冷火之地·寒蟒", "elite": True,
               "source": "第一千零二十五章「异火测试」（天寒火出自其体，出没于骨灵冷火区域）",
               "intro": "寒气无端自地底漫出，一条白骨般的巨蟒缓缓盘起，吐信间霜花凝结。",
               "quotes": (),
               "skill": {"name": "天寒蚀骨", "chance": 0.20, "effect": "poison", "mult": 0.12},
               "mech": {"t": "dot", "v": 0.15, "d": 2, "p": 0.27, "kind": "寒毒·蚀骨", "base": 1}},
    "海魔兽": {"name": "海魔兽", "title": "七阶魔兽", "elite": True,
               "source": "第一千零七十五章「离火焚天」（海魔炎取自七阶魔兽海魔兽之体）",
               "intro": "深海翻涌，一头庞大到遮蔽半片海面的黑影浮起，靛蓝火焰在浪尖燃起。",
               "quotes": (),
               "skill": {"name": "离火翻涛", "chance": 0.20, "effect": "burst", "mult": 1.8},
               "mech": {"t": "reflect", "v": 0.22, "d": 2, "p": 0.28, "kind": "海魔·反噬"}},
    # title 会印在玩家看得见的卡面上，绝不能带「游戏原创」这类元说明（沉浸感红线）；
    # 出处考据只写在 source 里，留给内部核对用（2026-11-26 沉浸门禁抓到后清掉）。
    "幻火蝎龙兽": {"name": "幻火蝎龙兽", "title": "异种魔兽", "elite": True,
                   "source": "游戏原创（附件 docs/兽火单方.txt 新造，语料 0 命中，不编章节）",
                   "intro": "沙砾下骤然拱起一道金影，蝎尾与龙翼并生的凶兽现形，"
                            "周身金焰扭曲了四周的光。",
                   "quotes": (),
                   "skill": {"name": "幻金迷瞳", "chance": 0.19, "effect": "drain", "mult": 0.11},
                   "mech": {"t": "silence", "v": 0.0, "d": 1, "p": 0.22, "kind": "幻金·乱神"}},
    "天阴三头狮": {"name": "天阴三头狮", "title": "阴属魔兽", "elite": True,
                   "source": "第一千一百五十章「血妖焰火」（天阴魔火取自其体）",
                   "intro": "黑雾凝聚，三颗狮首同时睁眼，淡黑色的火焰在鬃毛间无声燃烧。",
                   "quotes": (),
                   "skill": {"name": "天阴蚀甲", "chance": 0.20, "effect": "poison", "mult": 0.13},
                   "mech": {"t": "debuff_atk", "v": 0.20, "d": 2, "p": 0.28, "kind": "阴火·侵甲"}},
    "黑焰紫云雕": {"name": "黑焰紫云雕", "title": "魔兽山脉·高阶魔兽", "elite": True,
                   "source": "原著世界观（游戏命名，语料未见同名魔兽）",
                   "intro": "黑焰自云层压落，雕影遮天，翅尖划过之处留下一道焦痕。",
                   "quotes": (),
                   "skill": {"name": "黑焰俯冲", "chance": 0.18, "effect": "burst", "mult": 1.6},
                   "mech": {"t": "dot", "v": 0.14, "d": 2, "p": 0.26,
                            "kind": "黑焰·灼羽", "base": 1}},
    "天山雪蟒": {"name": "天山雪蟒", "title": "天山·寒蟒", "elite": True,
                 "source": "原著世界观（游戏命名，非原著角色）",
                 "intro": "",
                 "quotes": (),
                 "skill": {"name": "寒毒缠身", "chance": 0.19, "effect": "poison", "mult": 0.10},
                 "mech": {"t": "debuff_atk", "v": 0.18, "d": 2, "p": 0.26, "kind": "寒毒·僵脉"}},
    "翼黑蛇皇": {"name": "翼黑蛇皇", "title": "蛇人族·翼黑蛇皇", "elite": True,
                 "source": "原著·小医仙异兽（语料第4517行作「翼黑蛇皇」，无「八翼」）",
                 "intro": "",
                 "quotes": (),
                 "skill": {"name": "毒瘴锁空", "chance": 0.21, "effect": "poison", "mult": 0.12},
                 "mech": {"t": "dot", "v": 0.16, "d": 3, "p": 0.30,
                          "kind": "黑蛇·毒瘴", "base": 1}},
    "云岚山守山兽": {"name": "云岚山守山兽", "title": "云岚宗·守山兽",
                     "elite": True, "source": "原著世界观（游戏命名，非原著角色）",
                     "intro": "",
                     "quotes": (),
                     "skill": {"name": "撼山撞", "chance": 0.18, "effect": "burst", "mult": 1.7},
                     "mech": {"t": "stun", "v": 1, "d": 2, "p": 0.22, "kind": "撼山·震荡"}},
    "天毒蝎龙兽": {"name": "天毒蝎龙兽", "title": "蝎龙族", "elite": True,
                   "source": "原著·天墓篇（语料第4171行）",
                   "intro": "「混蛋，本王定要将你们碎尸万段！」庞大身躯砸落，大地如地震般开裂。",
                   "quotes": ("混蛋，本王定要将你们碎尸万段！",
                              "我摩蝎在此以蝎龙族血脉发誓，你定会在我蝎龙族的追杀下，逃亡一生！"),
                   "skill": {"name": "蝎龙剧毒", "chance": 0.22, "effect": "poison", "mult": 0.13},
                   "mech": {"t": "dot", "v": 0.18, "d": 3, "p": 0.34,
                            "kind": "蝎龙·腐蚀血雾", "base": 1}},
    # ---- 野外人族反派 ----
    "狼头佣兵穆蛇": dict(_CHAR_MUSHE, name="狼头佣兵穆蛇"),
    "血战佣兵": {"name": "血战佣兵", "title": "雇佣兵",
                 "source": "原著世界观（游戏命名）", "intro": "", "quotes": (),
                 "skill": {"name": "血战狂砍", "chance": 0.17, "effect": "burst", "mult": 1.5},
                 "mech": None},
    "黑角域散修": {"name": "黑角域散修", "title": "黑角域·散修",
                   "source": "原著世界观（游戏命名）", "intro": "", "quotes": (),
                   "skill": {"name": "阴邪夺气", "chance": 0.17, "effect": "drain", "mult": 0.09},
                   "mech": None},
    "黑盟死士": {"name": "黑盟死士", "title": "黑盟·死士", "elite": True,
                 "source": "原著世界观（游戏命名，非原著角色）",
                 "intro": "「黑盟不会放过你们的！」喝声未落，人已欺身至前。",
                 "quotes": ("黑盟不会放过你们的！",),
                 "skill": {"name": "死士搏命", "chance": 0.20, "effect": "burst", "mult": 1.7},
                 "mech": {"t": "reflect", "v": 0.30, "d": 2, "p": 0.33, "kind": "搏命·反噬"}},
    "魂殿鹜护法": {"name": "魂殿鹜护法", "title": "魂殿护法", "elite": True,
                   "source": "第六百十四章 鹜护法 / 第六百九十二章",
                   "intro": "「桀桀，我魂殿可有的是手段收拾你。」黑雾无声漫过，笑声从四面响起。",
                   "quotes": ("桀桀，我魂殿可有的是手段收拾你",
                              "本护法便等着看一场好戏吧",
                              "药尘，难道你以为以我现在的实力，你还能是我对手不成？"),
                   "skill": {"name": "摄魂术", "chance": 0.19, "effect": "drain", "mult": 0.10},
                   "mech": {"t": "silence", "v": 1, "d": 2, "p": 0.28, "kind": "摄魂·禁技"}},
    "药皇韩枫": dict(_CHAR_HANFENG, name="药皇韩枫"),
    "地魔老鬼": dict(_CHAR_DIMO),
    "魂殿摘星老鬼": dict(_CHAR_ZHAIXING),
    "魂殿执事": {"name": "魂殿执事", "title": "魂殿·执事", "elite": True,
                 "source": "原著世界观（游戏命名，非原著角色）",
                 "intro": "",
                 "quotes": (),
                 "skill": {"name": "锁魂链", "chance": 0.20, "effect": "drain", "mult": 0.10},
                 "mech": {"t": "silence", "v": 1, "d": 2, "p": 0.26, "kind": "魂殿·锁技"}},
    "魂灭生": {"name": "魂灭生", "title": "魂殿殿主", "elite": True,
               "source": "第一千五百一十二章 萧炎vs魂殿殿主",
               "intro": "「成大事，若是在乎这些，终究是小家子气。」殿主端坐黑雾之上。",
               "quotes": ("三局两平，有些出乎本殿的意料", "成大事，若是在乎这些，终究是小家子气"),
               "skill": {"name": "灭世魂掌", "chance": 0.25, "effect": "burst", "mult": 1.8},
               "mech": {"t": "stun", "v": 1, "d": 2, "p": 0.32, "kind": "造化掌·镇身"}},
    # ---- 爬塔 BOSS 层（tower_data.BOSS_NAMES 十个名字全覆盖）----
    "穆蛇": dict(_CHAR_MUSHE),
    "摘星老鬼": dict(_CHAR_ZHAIXING),
    "韩枫": dict(_CHAR_HANFENG),
    "血刀圣者": {"name": "血刀圣者", "title": "血刀圣者", "elite": True,
                 "source": "原著·天墓/魂殿篇（语料逐字核对：血刀圣者）",
                 "intro": "",
                 "quotes": (),
                 "skill": {"name": "血刀", "chance": 0.26, "effect": "burst", "mult": 1.7},
                 "mech": {"t": "debuff_heal", "v": 0.30, "d": 2, "p": 0.30, "kind": "血刀·封脉"}},
    "魂魔老人": {"name": "魂魔老人", "title": "魂殿·魂魔老人", "elite": True,
                 "source": "原著·魂殿篇（语料逐字核对：魂魔老人）",
                 "intro": "",
                 "quotes": (),
                 "skill": {"name": "魂魔手", "chance": 0.26, "effect": "drain", "mult": 0.11},
                 "mech": {"t": "silence", "v": 1, "d": 2, "p": 0.29, "kind": "魂魔·禁技"}},
    "狮天": {"name": "狮天", "title": "狮天", "elite": True,
             "source": "原著·天墓篇（语料逐字核对：狮天）",
             "intro": "",
             "quotes": (),
             "skill": {"name": "狮王裂空", "chance": 0.27, "effect": "burst", "mult": 1.7},
             "mech": {"t": "stun", "v": 1, "d": 2, "p": 0.27, "kind": "狮天·裂空"}},
    "天蛇老怪": {"name": "天蛇老怪", "title": "天蛇府·天蛇", "elite": True,
                 "source": "原著·天蛇府篇（语料逐字核对：天蛇老怪）",
                 "intro": "",
                 "quotes": (),
                 "skill": {"name": "蛇杖毒瘴", "chance": 0.26, "effect": "poison", "mult": 0.12},
                 "mech": {"t": "dot", "v": 0.16, "d": 3, "p": 0.30,
                          "kind": "天蛇·毒瘴", "base": 1}},
    "蝎山": {"name": "蝎山", "title": "万蝎门·蝎山", "elite": True,
             "source": "原著·毒宗篇（语料逐字核对：蝎山）",
             "intro": "",
             "quotes": (),
             "skill": {"name": "万蝎毒雾", "chance": 0.26, "effect": "poison", "mult": 0.12},
             "mech": {"t": "debuff_atk", "v": 0.20, "d": 2, "p": 0.30, "kind": "蝎山·蚀脉"}},
    "范痨": {"name": "范痨", "title": "范痨", "elite": True,
             "source": "第三百九十九章 大路激战",
             "intro": "",
             "quotes": (),
             "skill": {"name": "阴森血掌", "chance": 0.26, "effect": "drain", "mult": 0.11},
             "mech": {"t": "reflect", "v": 0.22, "d": 2, "p": 0.28, "kind": "范痨·血煞"}},
    # ---- 周本（游戏原创意象，明确标注，不冒充原著角色）----
    "天焚塔灵": {"name": "天焚塔灵", "title": "天焚炼气塔·守塔灵", "elite": True,
                 "source": "游戏原创（原著无「塔灵」角色）",
                 "intro": "",
                 "quotes": (),
                 "skill": {"name": "焚塔火域", "chance": 0.28, "effect": "burst", "mult": 1.8},
                 "mech": {"t": "dot", "v": 0.18, "d": 3, "p": 0.32,
                          "kind": "塔灵·火域", "base": 1}},
    "塔卫统领": {"name": "塔卫统领", "title": "天焚塔·塔卫统领", "elite": True,
                 "source": "游戏原创（原著无「塔卫」角色）",
                 "intro": "",
                 "quotes": (),
                 "skill": {"name": "火晶斩", "chance": 0.24, "effect": "burst", "mult": 1.6},
                 "mech": {"t": "stun", "v": 1, "d": 2, "p": 0.24, "kind": "塔卫·镇门"}},
    "塔卫": {"name": "塔卫", "title": "天焚塔·塔卫",
             "source": "游戏原创（原著无「塔卫」角色）", "intro": "", "quotes": (),
             "skill": {"name": "火晶刺", "chance": 0.20, "effect": "burst", "mult": 1.5},
             "mech": None},
    # ---- P3 远古人傀（第1235章 混乱）：斗圣主殿守卫 ----
    # 原著三件事逐条兑现：① 有神智（intro 说「睁眼」，台词是原文里描述它的句子）；
    # ② 能施展斗技（skill「裂山枪」——原著真名，霸枪柳擎的枪法，正合它手里那杆血枪）；
    # ③ 灰白布条卸力（cloth=0.60 走 monster_scaled 的额外防御，血条同步换算，
    #    回合数不变 ⇒ 玩家看得见「伤害被布条吃掉」，难度曲线不被暗改）。
    "远古人傀": {"name": "远古人傀", "title": "斗圣主殿·守殿人傀", "elite": True,
                 "source": "第一千两百三十五章 混乱",
                 "intro": "石座四畔的石雕忽然睁眼，瞳中杀伐之意烧得正旺；"
                          "灰白布条层层缠身，一杆血枪自布条间探出，枪尖的血千年未干。",
                 "quotes": ("这不是普通的傀儡，它们的眼中，依旧有生气残留",
                            "不仅拥有着神智，而且还能施展斗技，远比寻常傀儡棘手",
                            "无法控制，印记太强，现在的我，无法将之抹除"),
                 "skill": {"name": "裂山枪", "chance": 0.26, "effect": "burst", "mult": 1.9},
                 "mech": {"t": "debuff_atk", "v": 0.22, "d": 2, "p": 0.30,
                          "kind": "杀伐·枪势"}},
}
#: 塔的精英层名字形如「精英·穆蛇」，取角色包时剥掉前缀即可复用同一只怪的设定。
def monster_character(name: str) -> dict | None:
    raw = str(name or "")
    ch = MONSTER_CHARACTERS.get(raw)
    if ch is None and raw.startswith("精英·"):
        ch = MONSTER_CHARACTERS.get(raw[3:])
    if ch is None:
        return None
    # 出场描写集中在本表，避免每个角色包各写一份、改一处漏一处。
    ch = dict(ch)
    ch["appearance"] = ch.get("appearance") or CHARACTER_APPEARANCE.get(ch["name"], "")
    return ch


#: 出场描写 = 玩家第一眼看到的外貌 / 气势 / 环境，**纯沉浸式旁白**。
#: 铁律：这里禁止出现「原著」「语料」「未载」「不代编」「游戏原创」等
#: 开发向声明——玩家是来玩游戏的，不是来看考据报告的（2026-11-20 用户两次强调）。
#: 描写取材自原著对应段落的外貌/场景句，提炼成一句；魔兽只写威压与形体。
CHARACTER_APPEARANCE: dict[str, str] = {
    # ---- 九星突破 12 BOSS ----
    "药老·药尘": "黑袍裹得严严实实，干涩苍老的嗓音自袍中飘出，一双眼睛在阴影里亮着。",
    "狼头佣兵·穆蛇": "面色阴沉的中年男子端坐首位，指尖轻叩桌面，一室沉默被他缓缓打破。",
    "云岚宗·云棱": "白袍老者居中而立，众长老分列两侧，一道目光缓缓落在你身上。",
    "黑盟死士": "黑袍人吐出一口血，反手撕开衣襟，露出一身纵横交错的旧疤。",
    "紫晶翼狮王": "通体裹覆紫晶的巨兽仰天长啸，四爪落处山石崩裂，紫色火焰自翼下喷薄。",
    "魂殿鹜护法": "大殿中涌出铺天盖地的黑雾，于天际聚成一团丈许雾团，阴冷无声压境。",
    "地魔老鬼": "血发老者身披骷髅黄袍，脸如枯骨，深陷双目幽亮如鬼火，凌空而立空间轻颤。",
    "魂殿摘星老鬼": "漫天黑色锁链之间，灰白衫老者脚踏一链笑吟吟俯视，杀意尽藏在笑纹里。",
    "魂殿殿主·魂灭生": "天地骤然禁锢，一只数百丈黑暗大手自虚空凝成，当头罩下。",
    "魂灭生·真身": "黑雾倒卷成柱，殿主真身自其中踏出，每一步都让空间泛起涟漪。",
    "美杜莎女王": "七彩光芒自蛇身暴涨，纤细身形瞬息化为十丈庞然巨物，鳞光流转如神龙临世。",
    "魂天帝": "乌云压顶，银蛇在云层间乱窜，天地能量暴动如潮，整片空间都在低鸣。",
    # ---- 野外精英 ----
    "双头火灵蛇": "两颗蛇首自岩浆中抬起，菱形眼瞳泛着嗜血的光，炎浪顺鳞片翻卷。",
    "黑焰紫云雕": "黑焰自云层压落，雕影遮天，翅尖划过之处留下一道焦痕。",
    "天山雪蟒": "寒气自地缝倒卷，鳞片摩擦声里毒雾漫开，脚下石面结出白霜。",
    "翼黑蛇皇": "巨蛇通体黝黑斑斓，紫翼振于身侧，螺旋尖角泛着剧毒紫芒。",
    "云岚山守山兽": "山门石兽睁眼，云岚剑气扑面，青石地面被削出一道沟壑。",
    "天毒蝎龙兽": "血红眼瞳锁敌，几十米长的巨大蝎尾刺破长空，毒云翻涌，地动山摇。",
    "狼头佣兵穆蛇": "面色阴沉的中年男子端坐首位，指尖轻叩桌面，一室沉默被他缓缓打破。",
    "药皇韩枫": "药皇袍角翻飞，袖中火影一闪即逝，周身浮起一层灼人的血色斗气。",
    "魂殿执事": "黑袍执事合上锁魂册，指尖黑雾凝成锁链，锁链落地无声。",
    "魂灭生": "天地骤然禁锢，一只数百丈黑暗大手自虚空凝成，当头罩下。",
    # ---- 爬塔 BOSS ----
    "穆蛇": "面色阴沉的中年男子端坐首位，指尖轻叩桌面，一室沉默被他缓缓打破。",
    "摘星老鬼": "漫天黑色锁链之间，灰白衫老者脚踏一链笑吟吟俯视，杀意尽藏在笑纹里。",
    "韩枫": "药皇袍角翻飞，袖中火影一闪即逝，周身浮起一层灼人的血色斗气。",
    "血刀圣者": "暗红袍服，鬼头大刀，脸横数道刀疤，凶煞凝如一头浴血怒狮。",
    "魂魔老人": "灰衣老人摇头走出，一对长黑眉垂落脸侧，其貌不扬却深藏不露。",
    "狮天": "金发男子踏碎虚空而来，身后大军压境，连地面都在震颤。",
    "天蛇老怪": "蛇杖点地，老怪阴冷一笑，杖头白雾里藏着毒牙的影子。",
    "蝎山": "万蝎门主一身低调装束，出手却带腐蚀毒雾，草木沾上即刻枯黄。",
    "范痨": "阴森喝声在道路上空徘徊，血色双翼卷起腥风。",
    # ---- 周本 ----
    "天焚塔灵": "塔底岩浆翻涌，异火凝成的守塔之灵睁开双目，整座塔身随之震鸣。",
    "塔卫统领": "火晶甲叶相撞，塔卫统领横戟挡在塔阶尽头，火光把影子拉得很长。",
    # ---- P3 远古人傀 ----
    "远古人傀": "灰白布条缠满的躯体自石座旁立起，软得像旧布，落上去却比玄铁还硬，"
                "枪尖一点血色在殿中格外刺眼。",
}
# 兽火精英 6 只（2026-11-26）：描写在 recipes_data，config 已贴 200KB 红线
CHARACTER_APPEARANCE.update(BEAST_ELITE_APPEARANCE)


def character_appearance(name: str) -> str:
    """取某只怪的出场描写；没有就返回空串（普通怪不刷登场旁白）。"""
    return CHARACTER_APPEARANCE.get(str(name or ""), "")

# ---------------------------------------------------------------------------
# 后期天焚炼气塔 · 聚火壶修炼（2026-11-19 大改）
# ---------------------------------------------------------------------------
#: 五档火晶卡（黑/蓝/青/赤/紫），对应原著「黑蓝青赤紫，越往上可入层数越深」。
FACTION_CARD_NAMES = ("黑火晶卡", "蓝火晶卡", "青火晶卡", "赤火晶卡", "紫火晶卡")
#: 每档可进入的最高塔层（第1~10层由浅入深，塔底岩浆世界另算）。
FACTION_CARD_MAX_FLOOR = (2, 4, 6, 8, 10)
#: 心火倍率 —— 原著「有了心火淬炼体内斗气，修炼速度起码成倍翻长」。
FACTION_CARD_MULT = (1.5, 2.0, 2.8, 4.0, 6.0)
#: 换卡所需火能（原著「从黑换蓝需一百天火能，蓝换青需两百天」）。
FACTION_CARD_UPGRADE_COST = (100, 200, 400, 800)

#: 每日免费火能（原著「内院每月组织免费入塔」→ 游戏化每日）。
TOWER_FREE_ENERGY = 20
#: 1 点火能可换多少分钟塔修。原著火能是「天」为单位，本作按 10 分钟压细，
#: 让每日 20 点免费火能 ≈ 3 小时塔修（不至于领了不够用一分钟）。
TOWER_MINUTES_PER_ENERGY = 10
#: 战斗胜利获得火能 = mon.lv // TOWER_ENERGY_PER_LV，至少 1。
TOWER_ENERGY_PER_LV = 5
#: 每次入塔最多结算多少分钟（防止挂机几年一次结算爆表）。
TOWER_MAX_MINUTES = 12 * 60
#: 火毒累积（每分钟 · 卡档）。原著「修炼半天、休息一夜」「斗灵可连修四五天」。
TOWER_POISON_PER_MIN = (1.2, 1.6, 2.0, 2.6, 3.2)
TOWER_POISON_CAP = 100
#: 斗灵（lv37）火毒增速减半，且可进塔底岩浆世界。
TOWER_DOU_LING_LV = 37
#: 塔底岩浆世界：额外收益倍率与额外火毒倍率。
TOWER_MAGMA_MULT = 1.5
TOWER_MAGMA_POISON_MULT = 1.5
#: 出塔后火毒自然消解（每分钟）。
TOWER_POISON_COOL_PER_MIN = 2.0

# ---------------------------------------------------------------------------
# 掉落  [源码 h.java:57-78 形状：千分率 + 基础值到 2 倍之间随机]
# ---------------------------------------------------------------------------
DROP_RATE_STONE = 260       # 炎星石掉落千分率（26%）
DROP_RATE_HERB = 520        # 药材掉落千分率（52%）
DROP_GOLD_MULT = (1, 2)     # 金币 = 基础值的 1~2 倍

# ---------------------------------------------------------------------------
# 战斗疗伤丹掉落（2026-11-19 用户「任何战斗掉落增加补血补斗气丹药」）
# ---------------------------------------------------------------------------
#: 普通战斗胜利后掉 1 件疗伤丹的概率（气血/斗气各 50% 二选一）。
#: 精英层与 Boss 层不看概率——两件必掉，因为那本来就是该喝药的硬仗。
HEAL_DROP_RATE = 0.55
#: 掉落用的疗伤丹名（必须是 RECIPES 里 effect=hp / effect=mp 的既有配方）。
#: 不新增丹药：靠配方上的 ``pct``（上限百分比）让同一枚药在高境界也不至于无感。
HEAL_PILL_HP = "回春散"
HEAL_PILL_MP = "回气丹"

# ---------------------------------------------------------------------------
# 自动调息（2026-11-19 用户「给斗气和血量加上自动恢复」）
# ---------------------------------------------------------------------------
#: 结算间隔（秒）。每过一个间隔，气血/斗气各按上限百分比回一次。
#: 离线同样累计（时间基准落 ``regen_clock`` 表，不写 players.data）。
#: 与「突破/升级回满」不冲突：调息是慢速保底，破境仍然是唯一的瞬间满状态。
REGEN_TICK_SEC = 180
#: 每个间隔恢复的最大气血比例（4% → 满血约需 75 分钟，一夜回满）。
REGEN_HP_PCT = 0.04
#: 每个间隔恢复的最大斗气比例（6% → 空蓝约需 50 分钟）。
#: 比气血快：斗气是跨战结转的资源，战斗续航主要卡在它上面。
REGEN_MP_PCT = 0.06

# ---------------------------------------------------------------------------
# 剧情章节门禁（2026-11-25 用户拍板 Q1=A / G2=「怪等级按章节走」）
#
# 为什么需要这一层：旧 `pick_encounter` 只吃 `p["lv"]`，主线推到第几章对怪池
# **零影响**（真跑证据：交完第 1 章主线再打 10 场，14 次遭遇全是嗜血鼠）。
# 本章节表把「哪一章能遇到哪些怪」变成显式数据，主线推进才真正改变世界。
#
# 纪律：
#   * 怪名**全部取自** `MONSTERS`，不新增怪物实体（开通的是「能遇到哪些已有怪」）；
#   * 章节等级区间由 `REALM_TABLE` 推导（`chapter_lv_range`），**不手抄**；
#   * `story_chapter` 由 `main_idx` 推导，唯一写入点 `core.main_submit`。
# ---------------------------------------------------------------------------

#: 章 → 该章可遇的怪种（唯一真源）。
#: 前 8 章区域/怪来自解包任务序列与地点清单，后 5 章来自原著章节推进。
CHAPTER_SPECIES: dict[int, tuple[str, ...]] = {
    1: ("嗜血鼠", "岩蛇"),
    2: ("巡山妖", "暴雪魔猿", "狼头佣兵穆蛇"),
    3: ("狮翼兽", "赤炎狼", "血战佣兵"),
    4: ("双头火灵蛇", "紫晶翼狮王", "奎木兽"),
    5: ("惊涛龙兽", "黑角域散修"),
    6: ("黑焰紫云雕", "魂殿鹜护法"),
    7: ("天山雪蟒", "药皇韩枫", "天骨蟒"),
    8: ("云岚山守山兽", "地魔老鬼", "翼黑蛇皇"),
    9: ("黑盟死士", "海魔兽", "幻火蝎龙兽"),
    10: ("天阴三头狮", "魂殿摘星老鬼"),
    11: ("天毒蝎龙兽",),
    12: ("魂殿执事",),
    13: ("魂灭生",),
}

#: 章 → 开通区域名（回执/任务面板展示用，纯文案，不参与数值）。
CHAPTER_REGION: dict[int, str] = {
    1: "乌坦城·萧家后山", 2: "魔兽山脉外围", 3: "魔兽山脉深处", 4: "魔兽山脉核心",
    5: "塔戈尔大沙漠", 6: "蛇人禁地·石漠城", 7: "帝都·墨家", 8: "云岚宗",
    9: "兽族遗迹", 10: "中州", 11: "天墓", 12: "兽域", 13: "斗帝之路",
}

#: 章节总数 = 主线任务数（`main_idx` 的值域 0..CHAPTER_COUNT-1）。
#: 两者必须相等：`story_chapter = main_idx + 1`，少一个任务就少一章开不出来。
CHAPTER_COUNT = len(CHAPTER_SPECIES)


def chapter_lv_range(ch: int) -> tuple[int, int]:
    """第 ``ch`` 章对应的等级区间 ``[起, 止]``，由 REALM_TABLE 推导（不手抄）。

    章 1..13 与 REALM_TABLE 的 13 个境界一一对应：
    ch1=斗之气[1,9]、ch2=斗者[10,18] … ch12=斗圣[94,102]、ch13=斗帝[103,103]。

    🚨 单一真源：区间只由 `realm_lv` / `REALM_TABLE` 决定，禁止在别处手写
    `(1,9)` 这类字面量——境界表一改，章节区间必须跟着改。
    """
    idx = max(0, min(len(REALM_TABLE) - 1, int(ch) - 1))
    start = realm_lv(idx)
    return start, start + int(REALM_TABLE[idx][1]) - 1


def chapter_of_lv(lv: int) -> int:
    """等级 → 它所属的章节（`chapter_lv_range` 的逆，展示/兜底用）。"""
    return realm_index(lv) + 1


def chapter_base_lv(ch: int) -> int:
    """第 ``ch`` 章怪物的**固定等级** = 该章区间中点（2026-11-25 用户拍板 G2）。

    「怪等级按章节走」的落地值：玩家练到多少级，第 N 章的怪都还是这个等级，
    不再被玩家等级抬高（旧口径 `monster_scaled(p, mon)` 按玩家等级反解）。

    为什么取中点而不是起点/终点（ch1、1~5 敌随机、无丹、每格 120 场）：
      * 起点（ch1→lv1）：lv9 玩家 100% 胜、均修为只剩 **12/场** —— 整章碾压，
        且经验预算被压在 lv1 上（lv9 升级要 3600），等于「开通了但练不动」；
      * 中点（ch1→lv5，现行）：章内前段略吃力、后段顺畅（lv5 40% → lv9 73%），
        均修为 133 → 188，成长张力与收益都正当；
      * 终点（ch1→lv9）：lv9 打本章怪只有 **37%** —— 章初就是硬仗，劝退。
      * 同时保留惩罚：lv40 回第 1 章，怪仍是 lv5，经验 **215/场**，而 lv40 升级
        要 71111 —— 刷低章怪收益 **0.30%**，逼玩家推主线。
    """
    lo, hi = chapter_lv_range(ch)
    return (lo + hi) // 2


# ---------------------------------------------------------------------------
# 任务  [调参，扣原著主线]
#
# 2026-11-25 用户拍板 G1=A：主线从 5 条扩到 13 条，**一章一条**。
# 为什么必须扩：`story_chapter = main_idx + 1`，只有 5 条任务时章节最多到 6，
# 13 章表里第 7–13 章永远开不出来（真跑核对 main_submit 只做 main_idx+=1）。
#
# `require_lv`：交任务前必须达到的等级 = 该章等级区间上限（末尾统一注入并断言）。
# 作用：玩家不能在 lv1 交完第 1 章就跳进第 2 章打 lv10 的怪（会必死），
# 也不能靠刷低章怪无限升级——低章怪等级被钳在章末，经验按怪等级给。
# ---------------------------------------------------------------------------
MAIN_TASKS = [
    # ---- 前 5 章沿用既有任务名（老档 main_idx 语义不变）----
    {"name": "云岚山初战", "desc": "斩杀 3 只妖兽", "kind": "kill", "target": 3,
     "reward": {"gold": 500, "exp": 800, "luck": 5}},
    {"name": "寻药天山", "desc": "采集 5 株血灵草", "kind": "herb", "target": 5,
     "reward": {"gold": 800, "exp": 1500, "luck": 10}},
    {"name": "焚血淬体", "desc": "炼成 2 份焚血散", "kind": "craft", "target": 2,
     "reward": {"gold": 1500, "exp": 4000, "luck": 20}},
    {"name": "斗尊之路", "desc": "强化玄重尺至 5 级", "kind": "weapon", "target": 5,
     "reward": {"gold": 3000, "exp": 12000, "luck": 40}},
    {"name": "异火初燃", "desc": "将任意异火升到 1 级", "kind": "flame", "target": 1,
     "reward": {"gold": 6000, "exp": 30000, "luck": 80}},
    # ---- 第 6–13 章（2026-11-25 新增，扣原著区域推进）----
    {"name": "蛇人禁地", "desc": "斩杀 8 只魔兽", "kind": "kill", "target": 8,
     "reward": {"gold": 9000, "exp": 45000, "luck": 100}},
    {"name": "帝都风云", "desc": "采集 8 株七叶花", "kind": "herb", "target": 8,
     "reward": {"gold": 12000, "exp": 60000, "luck": 120}},
    {"name": "三年之约", "desc": "斩杀 12 只妖兽", "kind": "kill", "target": 12,
     "reward": {"gold": 16000, "exp": 80000, "luck": 160}},
    {"name": "兽族遗迹", "desc": "炼成 3 份丹药", "kind": "craft", "target": 3,
     "reward": {"gold": 20000, "exp": 100000, "luck": 200}},
    {"name": "中域争锋", "desc": "强化任意武器至 8 级", "kind": "weapon", "target": 8,
     "reward": {"gold": 26000, "exp": 130000, "luck": 240}},
    {"name": "天墓试炼", "desc": "将任意异火升到 3 级", "kind": "flame", "target": 3,
     "reward": {"gold": 32000, "exp": 160000, "luck": 280}},
    {"name": "兽域决战", "desc": "斩杀 15 只强敌", "kind": "kill", "target": 15,
     "reward": {"gold": 44000, "exp": 220000, "luck": 320}},
    {"name": "斗帝之路", "desc": "斩杀 20 只强敌", "kind": "kill", "target": 20,
     "reward": {"gold": 60000, "exp": 300000, "luck": 400}},
]

# `require_lv` 一律取该章区间上限：ch1→9、ch2→18 … ch13→103。
# 末尾注入而不是手写，是为了「境界表一改，门槛自动跟着改」；下面 immediately
# 断言注入结果与 chapter_lv_range 一致，写错会 import 期直接炸，不会静默错位。
for _gi, _gt in enumerate(MAIN_TASKS, 1):
    _gt["require_lv"] = chapter_lv_range(_gi)[1]
del _gi, _gt

assert len(MAIN_TASKS) == CHAPTER_COUNT, (
    "MAIN_TASKS 条数(%d) 必须等于 CHAPTER_COUNT(%d)：story_chapter=main_idx+1，"
    "少一条就少一章永远开不出来" % (len(MAIN_TASKS), CHAPTER_COUNT)
)

SIDE_TASK_POOL = [
    {"name": "药农之托", "desc": "采 3 株紫叶兰", "kind": "herb", "target": 3,
     "reward": {"gold": 300, "exp": 600, "luck": 8}},
    {"name": "守山兽患", "desc": "斩杀 2 只妖兽", "kind": "kill", "target": 2,
     "reward": {"gold": 400, "exp": 900, "luck": 10}},
    {"name": "炎星石之需", "desc": "强化玄重尺 1 次", "kind": "weapon", "target": 1,
     "reward": {"gold": 600, "exp": 1200, "luck": 12}},
    {"name": "丹房杂役", "desc": "炼制 1 次丹药", "kind": "craft", "target": 1,
     "reward": {"gold": 500, "exp": 1000, "luck": 10}},
    # ---- A 线隐藏彩蛋支线（2026-11-19）：desc/reward 不写异火名，完成后静默收服 ----
    # 惊喜感：接任务时只看到普通委托；提交时才在回包里揭晓「意外引出一缕奇火」。
    {"name": "神农谷的灵田", "desc": "替神农老人照看 5 株灵药", "kind": "herb", "target": 5,
     "reward": {"gold": 800, "exp": 2000, "luck": 20}},
    {"name": "药老的旧友", "desc": "炼制 3 份丹药送去旧友处", "kind": "craft", "target": 3,
     "reward": {"gold": 900, "exp": 2200, "luck": 20}},
    {"name": "焚炎谷的试炼", "desc": "斩杀 5 只妖兽", "kind": "kill", "target": 5,
     "reward": {"gold": 1000, "exp": 2500, "luck": 25}},
    {"name": "古族来客", "desc": "采集 6 株七叶花", "kind": "herb", "target": 6,
     "reward": {"gold": 1000, "exp": 2500, "luck": 25}},
    {"name": "沙漠深处的蛇影", "desc": "斩杀 4 只妖兽", "kind": "kill", "target": 4,
     "reward": {"gold": 900, "exp": 2200, "luck": 22}},
    {"name": "炎族的窖藏", "desc": "强化玄重尺 2 次", "kind": "weapon", "target": 2,
     "reward": {"gold": 1100, "exp": 2600, "luck": 25}},
    {"name": "星域的余烬", "desc": "炼制 4 份丹药", "kind": "craft", "target": 4,
     "reward": {"gold": 1200, "exp": 2800, "luck": 28}},
]

# ---------------------------------------------------------------------------
# 任务途中的「机遇」（原版随机事件）  [源码文案 + 调参数值]
# ---------------------------------------------------------------------------
# 文案逐字整理自原版《斗破苍穹2-双帝之战》HD 的静态文本资源：
#   D:\AI\doupo\hy_text\主线剧情\任务脚本\s__d0.hy.txt   （世界地图随机事件表，37~146 行）
#   D:\AI\doupo\hy_text\任务名称表\s__t.hy.txt           （隐藏发现：散财童子 / 魂淡 / 藏宝图）
#   D:\AI\doupo\hy_text\主线剧情\任务脚本\s__d14.hy.txt  （试炼之塔稀有材料）
# 提取时去掉了每行行首的 VM 控制字节与文案中的 \1…\0 颜色标记。
# 原版里这些事件是**在世界地图移动/做任务途中随机触发**的，正对应本次需求；
# 本插件把它们接进任务进度推进链路（core.advance_task / core.roll_chance）。
#
# 形状照抄原版：有白捡（炎星石/竞猜）、有破财（劫匪/深渊/女大王）、
# 有交换（神秘老者索丹）、也有骗局（生骨丹）和穷分支（匪头嫌你穷直接踹走）。
# 数值标 [源码] = 原版明文；[调参] = 原版未写数值或丹名在本项目丹方表里不存在、
# 按群聊经济缩放/映射（只调数值，不动事件形状与文案口径）。
#
# 字段：
#   name/text/tone/src  事件名、原文、语气(good/bad/neutral)、原版出处
#   weight              抽取权重（[调参]，越大越常见）
#   reward              {"gold","exp","luck","item":{name:n}}，负数=损失
#   need                可选：需要交出的物品；不足时走 missing_text，不扣不奖
#   guard_gold/poor_text 可选：金币不足以支付损失时改走原版的「嫌你穷」分支
#   quiz                可选：有奖竞猜的题目池，抽一题展示（答案见原版脚本）
# ---------------------------------------------------------------------------
#: [调参] 每次任务进度推进触发机遇的概率。
#: 验收脚本在 import 前置 DOUPO_CHANCE_RATE=0 关掉随机，避免概率让既有断言 flaky；
#: 机遇专项测试则临时把它设成 1.0 并注入 core.set_chance_rng 做确定性验证。
try:
    CHANCE_RATE = float(os.environ.get("DOUPO_CHANCE_RATE") or 0.22)
except (TypeError, ValueError):
    CHANCE_RATE = 0.22

CHANCE_EVENTS: list[dict] = [
    {
        "name": "天降炎星石", "tone": "good", "weight": 10,
        "text": "萧炎正在赶路，突然风云突变，天地混沌，惊雷不断，一场离奇的天象过后，"
                "萧炎在地上找到了一颗炎星石。",
        "reward": {"item": {"炎星石": 1}},
        "src": "s__d0.hy.txt:37（奖励 [源码]）",
    },
    {
        "name": "紫薇北斗", "tone": "good", "weight": 7,
        "text": "夜观天象，只见紫薇北斗光芒逼月，料想萧炎会有大机缘！",
        "reward": {"luck": 5},
        "src": "s__d0.hy.txt:39（[调参] 大机缘 → 机缘 +5）",
    },
    {
        "name": "偶遇张大善人", "tone": "good", "weight": 9,
        "text": "偶遇“张大善人”沿路施舍穷人，萧炎化妆后前去骗了一些钱财。",
        "reward": {"gold": 800},
        "src": "s__d0.hy.txt:40（[调参] 原版未写数额）",
    },
    {
        "name": "有奖竞猜", "tone": "good", "weight": 12,
        "text": "《斗破苍穹》有奖竞猜！答案正确，获得1000金币奖励。",
        # quiz 保留原版逐字文案（含「（答案：X）」），供 test_chance 逐字考据守门；
        # 也作为 question/answer 的提取源，避免两处文案漂移。真正展示给玩家的是
        # quiz_items 里拆好的题目（去掉答案） + 选项按钮，答案只留服务端判定。
        "quiz": [
            "玩家在本游戏中可拥有几种火？（答案：4种）",
            "萧炎为何要去云岚宗？（答案：三年之约）",
            "薰儿与萧炎的关系是？（答案：青梅竹马）",
            "斗破苍穹故事的主角是谁？（答案：萧炎）",
            "斗破苍穹里谁是萧炎的老师？（答案：药老）",
        ],
        # 2026-11-16 用户「机遇问答应该增加选项按钮，而不是默认正确」：
        # question 从 quiz 去掉「（答案：X）」得到；options 是 4 选 1 的干扰项；
        # answer 是正确选项在 options 里的下标。答案绝不进按钮 data。
        # 干扰项均取自原著人物/设定，不编造无关词。
        "quiz_items": [
            {"options": ["1 种", "4 种", "3 种", "5 种"], "answer": 1},
            {"options": ["三年之约", "复仇雪恨", "拜师学艺", "寻找异火"], "answer": 0},
            {"options": ["青梅竹马", "同门师姐", "救命恩人", "宿世仇敌"], "answer": 0},
            {"options": ["萧炎", "萧战", "萧厉", "萧薰儿"], "answer": 0},
            {"options": ["药老", "韩枫", "云山", "魂灭生"], "answer": 0},
        ],
        "reward": {"gold": 1000},
        "src": "s__d0.hy.txt:87-139（5 组竞猜，奖励 [源码] 1000）",
    },
    {
        "name": "散财童子", "tone": "good", "weight": 6,
        "text": "能在茫茫人海中寻找到散财童子，说明你机缘不错哟亲。",
        "reward": {"gold": 1500, "luck": 2},
        "src": "s__t.hy.txt:68（[调参] 原版未写数额）",
    },
    {
        "name": "魂淡赠丹方", "tone": "good", "weight": 5,
        "text": "竟然找到了魂淡……大机缘啊！为了鼓励你的探索精神，魂淡赠予你“丹方”。",
        "reward": {"item": {"焚血散": 1}, "luck": 3},
        "src": "s__t.hy.txt:75（[调参] 丹方 → 本项目焚血散）",
    },
    {
        "name": "真正的藏宝图", "tone": "good", "weight": 5,
        "text": "在天北城找到了骗子韩坑，从他手中抢来了真正的藏宝图，按宝图所写宝藏就在“天山血潭”。",
        "reward": {"gold": 2000, "item": {"炎星石": 1}},
        "src": "s__t.hy.txt:76（[调参] 天山血潭 → 金币 + 炎星石）",
    },
    {
        "name": "试炼之塔", "tone": "good", "weight": 6,
        "text": "这石漠城有一处名为试炼之塔的地方，那里不但能提升实力，运气好的话还能获得稀有材料。",
        "reward": {"item": {"炎星石": 1, "血灵草": 2}, "exp": 300},
        "src": "s__d14.hy.txt:58（[调参] 稀有材料 → 炎星石 + 药材）",
    },
    {
        "name": "兽人宝藏", "tone": "good", "weight": 3,
        "text": "发现兽人藏匿宝藏的洞穴",
        "reward": {"gold": 3000, "luck": 5},
        "src": "s__d0.hy.txt:1 / s__t.hy.txt:66（[调参] 富可敌国 → 大额金币）",
    },
    {
        "name": "时空风暴", "tone": "good", "weight": 3,
        "text": "路遇时空风暴，危险同时伴随着机遇，是否进入风暴一探究竟？富贵险中求！走起！！",
        "reward": {"gold": 2000, "luck": 10, "item": {"炎星石": 2}},
        "src": "s__d0.hy.txt:142-144（[调参] 原版只给选择、未写结果）",
    },
    {
        "name": "神秘老者索丹", "tone": "good", "weight": 8,
        "text": "路遇神秘老者向你索要3枚破障丹，神秘老者非常高兴，赠送你一些奖励。",
        "need": {"破障丹": 3},
        "missing_text": "你身上的破障丹不够，神秘老者悻悻的走开了，有些事，一旦错过就不再。",
        "reward": {"gold": 1500, "luck": 3},
        "src": "s__d0.hy.txt:44-50（丹名/奖励 [源码] / [调参]）",
    },
    {
        "name": "神秘老者索丹", "tone": "good", "weight": 6,
        "text": "路遇神秘老者向你索要3枚风行丹，神秘老者非常高兴，赠送你一些奖励。",
        "need": {"风行丹": 3},
        "missing_text": "你身上的风行丹不够，神秘老者悻悻的走开了，有些事，一旦错过就不再。",
        "reward": {"exp": 1200, "luck": 3},
        "src": "s__d0.hy.txt:65-71（丹名/奖励 [源码] / [调参]）",
    },
    {
        "name": "生骨丹骗局", "tone": "bad", "weight": 4,
        "text": "路遇神秘老者向你索要4枚回灵丹……神秘老者狡诈的一笑，带着丹药转身不见了。"
                "远处传来“骗骗更健康”的笑语…",
        "need": {"回灵丹": 4},
        "missing_text": "你身上的回灵丹不够，神秘老者悻悻的走开了。",
        "reward": {},
        "src": "s__d0.hy.txt:79-86（[调参] 原版生骨丹 → 本项目回灵丹）",
    },
    {
        "name": "路遇劫匪", "tone": "bad", "weight": 8,
        "text": "路遇劫匪，实力非常彪悍，萧炎不想横生事端，果断扔给他们3000金钱，保命要紧！损失3000金钱。",
        "reward": {"gold": -3000},
        "guard_gold": 3000,
        "poor_text": "路遇劫匪，匪头圣哥嫌弃萧炎太穷，将其踹走！",
        "src": "s__d0.hy.txt:41-43（数额与穷分支均 [源码]）",
    },
    {
        "name": "女大王招亲", "tone": "bad", "weight": 6,
        "text": "路遇劫匪，被女大王抢上山做压寨丈夫，萧炎急中生智，拿出1000金钱，散财消灾。损失1000金钱。",
        "reward": {"gold": -1000},
        "guard_gold": 1000,
        "poor_text": "路遇劫匪，匪头圣哥嫌弃萧炎太穷，将其踹走！",
        "src": "s__d0.hy.txt:140-143（数额 [源码]）",
    },
    {
        "name": "跌入无尽深渊", "tone": "bad", "weight": 6,
        "text": "出门没看黄历，萧炎跌入无尽深渊，幸好被一队佣兵所救！损失2000金钱。",
        "reward": {"gold": -2000},
        "guard_gold": 2000,
        "poor_text": "路遇劫匪，匪头圣哥嫌弃萧炎太穷，将其踹走！",
        "src": "s__d0.hy.txt:102-103（数额 [源码]）",
    },
    {
        "name": "卖消息的神秘男子", "tone": "neutral", "weight": 6,
        "text": "遇见了卖你消息的神秘男子，他偷偷的告诉你，斗气大陆上的很多秘密只有喜爱探索地图的人方可发现。",
        "reward": {"luck": 1},
        "src": "s__d0.hy.txt:96（[调参] 线索 → 机缘 +1）",
    },
]

CHANCE_BY_NAME = {e["name"]: e for e in CHANCE_EVENTS}

# ---------------------------------------------------------------------------
# 成长曲线  [调参]
# 原版单级 205000 经验（实机 一星斗宗 0/205000）。群里照搬会让升级变成苦役，
# 故改为 need(lv) = 400 × lv²，配合战斗经验，约 5~8 战一级。
# 2026-11-14 九星制：等级轴拉长 3 倍，单级成本按 /9 缩放，
# 使「同一境界内的一级」大致还是改版前的三分之一 —— 每星便宜、总时长不变。
# ---------------------------------------------------------------------------
def exp_need(lv: int) -> int:
    if lv >= MAX_LV:
        return 0
    return 400 * lv * lv // 9


def cum_exp_need(lv: int) -> int:
    """从 1 级升到 lv 级累计消耗的修为（sum of exp_need(1..lv-1)）。

    exp_need(i)=400·i²/9 ⇒ 累计 ≈ 400/9 · (lv-1)·lv·(2lv-1)/6。
    根基裁定用它把「丹药修为」换算成占比，闭式避免逐等级循环。
    注：逐项 exp_need(i) 各自向下取整，闭式误差 ≤ n/2（lv50 差 14/179万 ≈0.0008%），
    对占比与隐藏系数无实际影响，测试按容差比对。
    """
    n = max(0, min(int(lv), MAX_LV) - 1)
    if n <= 0:
        return 0
    return 400 * n * (n + 1) * (2 * n + 1) // 54


# ---------------------------------------------------------------------------
# 根基裁定（2026-11-18 用户「靠丹药堆修为=根基不稳，同级同斗技也远不敌，
# 适量可以，投机受罚，PVP/PVE 都生效」）。
# 原著证据：第9章「根基是重中之重，修炼不可采半点外力」、第499章「依赖外力
# →斗气难凝实→对战吃亏」、第513章「十足药罐子，斗气虚浮不足为惧」、第561
# 章「取巧提升→虚浮→实战顶多在大斗师巅峰（打折非归零）」、第453章「虚浮
# 者不如凝实者，三年苦修=磐石地基」。详见 docs/斗破根基裁定_原著考据与实现_2026-11-18.md。
# 隐形：本函数只读 p['pill_exp']（丹药来源累计修为），不进 stats/人物卡/战报。
# ---------------------------------------------------------------------------
#: 丹药修为占比容忍阈值：不超过视为「适量」（丹药只是辅助，原著萧炎常态）
ROOT_TOLERANCE = 0.15
#: 极端投机（100% 靠丹药）的战力系数下限——远远不敌但非废人（第561章口径）
ROOT_MIN_FACTOR = 0.55
#: 占比从容忍阈值到 1.0 之间线性压到下限

# ---------------------------------------------------------------------------
# 魂殿邪修 · 吞噬炼化被动（2026-11-24 用户定案，设计文档 tmp/sim_output/魂殿邪修玩法设计）
# ---------------------------------------------------------------------------
# 玩家确认加入魂殿（faction == "hundian"）后锁死转宗，战斗胜利斩杀
# boss/elite 档怪时按被斩怪 exp × DEVOUR_RATE 额外吞噬修为，并同步记入
# pill_exp 受 root_factor 根基惩罚（用户拍板②：邪修快而虚，不击穿 PVP 平衡）。
# 原著依据：第690章「强势吞噬灵魂来壮大自己的实力」、第770章魂袋增幅、
# 第1460章「吸收灵魂本源，能够提升灵魂力量」。
#: 吞噬修为系数（U02，实现阶段定标）。0 为合法值=关闭吞噬，显式判空不用 or。
DEVOUR_RATE = 0.5
#: 魂殿 faction key —— 入此宗即锁死转宗（不可逆邪道）。
DEVOUR_SECT = "hundian"


def root_factor(p: dict) -> float:
    """隐藏根基系数 ∈ [ROOT_MIN_FACTOR, 1.0]：投机堆丹药者实战打折。

    pill_exp = 累计由丹药获得的修为（use_item 的 effect=='exp' 分支唯一记账）。
    占比 = pill_exp / 当前等级累计所需修为；≤容忍阈值不罚，越接近 1 罚越重。
    老档无 pill_exp 视为 0（无惩罚），天然兼容。
    """
    pill = max(0, int((p or {}).get("pill_exp", 0)))
    if pill <= 0:
        return 1.0
    need = cum_exp_need(int((p or {}).get("lv", 1)))
    if need <= 0:
        return 1.0
    ratio = pill / float(need)
    if ratio <= ROOT_TOLERANCE:
        return 1.0
    # 从 (tolerance, 1.0) 线性压到 (1.0, ROOT_MIN_FACTOR)
    t = min(1.0, (ratio - ROOT_TOLERANCE) / (1.0 - ROOT_TOLERANCE))
    return round(1.0 - t * (1.0 - ROOT_MIN_FACTOR), 4)


#: 暴击基础倍率（无「暴伤」词条时的倍率）。原写死在 core._hit 的 1.5。
CRIT_BASE_MUL = 1.5


def crit_mul(p: dict) -> float:
    """暴击伤害倍率 = ``CRIT_BASE_MUL`` + ``bonus["暴伤"]``（百分点）。

    2026-11-23 审计：暴击**率**在 lv55 就被敏捷顶到 80% 硬顶，于是「八品·炎魔
    清玄丹 +30% 暴击」从可服用等级（73）起恒为 0 收益。原著第1449章侯老怪那枚
    丹的真实功效是「促使斗气变异、**增幅战斗力**」—— 落成暴击**伤害**才对得上，
    且它是唯一不会被上限吃掉的战力词条。

    帝炎 ×1.2 与 ``stats()`` 同口径（那句「全属性 ×1.2」不能只在面板生效）。
    """
    v = int((p or {}).get("bonus", {}).get("暴伤", 0) or 0)
    if p and p.get("diyan"):
        v = int(v * 1.2)
    return CRIT_BASE_MUL + v / 100.0


#: 被动修炼：每分钟获得的修为（离线也算，不强制打卡）
CULTIVATE_PER_MIN = 6.0
#: 战斗冷却（秒）——防刷
FIGHT_CD_SEC = 20
#: 战斗失败损失当前体力比例
#: ⚠️ 2026-11-19 起**只用于斗帝进阶失败的经验惩罚**（`level_up`），
#: 战败后的气血不再走这个系数 —— 见下面的 LOSE_HP_FLOOR。
LOSE_HP_ON_DEFEAT = 0.30
#: 战败结算保留的气血比例（2026-11-19 用户：「不要 0 血量，留百分之五就行」）。
#: 战败**不归零**：保住上限的这一成比例，且这就是回看卡「终局」与人物卡同时
#: 显示的**同一个数**（旧版回看写 0、人物卡写 70%，看着像两个游戏）。
#: 叙事上对应「落荒而逃 / 被小医仙所救」——被打到 0 以下只是差点死，不是死。
LOSE_HP_FLOOR = 0.05

# ---------------------------------------------------------------------------
# 交互时限（2026-11-17 用户硬要求）
# 凡是「需要玩家回一句话」的业务，统一 1 分钟内回复；超时即失效并失去奖励。
# 覆盖：爬塔战利品三选一 / 切磋应战 / 机遇有奖竞猜作答。
# 三处各自的默认常量必须与此一致（tests/test_doupo_choice_ttl.py 守门）。
# ---------------------------------------------------------------------------
CHOICE_TTL = 60

# ---------------------------------------------------------------------------
# 无惩罚切磋（2026-11-16 用户拍板：先做无惩罚切磋，仅本群，自动随机放技）
# ---------------------------------------------------------------------------
#: 切磋邀请有效期（秒）：超时未应战自动作废，避免半开会话堆积
DUEL_CHALLENGE_TTL = CHOICE_TTL
#: 切磋只更新荣誉战绩，不扣 hp / gold / exp（用户明确「只记荣誉战绩」）
DUEL_HONOR_ONLY = True

# ---------------------------------------------------------------------------
# 机缘·贵人相助（2026-11-16 用户设计：让机缘与战斗强关联）
# 用户原话：「机缘有结算随机奖惩就够了，战斗时也可以加一个极低概率遇到
# 无名斗皇强者帮助，一招制敌，赢得战斗」。
# ---------------------------------------------------------------------------
#: 贵人相助基础概率（每次战斗开场掷一次，极低——默认约 0.3%）。
#: 刻意压低，保证不改变 TTK_PLAYER 平衡；纯属稀有的机缘彩蛋。
HERO_AID_BASE_RATE = 0.003
#: 机缘对概率的加成上限：满机缘(9999)时总概率不超过此值（硬顶 2%）。
HERO_AID_MAX_RATE = 0.02
#: 触发概率 = min(MAX, BASE + luck/LUCK_SCALE)。LUCK_SCALE 决定成长斜率：
#: 机缘 100 约 +0.5%、机缘 1000 约 +5%(触及硬顶)，普通玩家(<50)几乎无感。
HERO_AID_LUCK_SCALE = 20000
#: 贵人称号池（斗破原著气质，随机取一；rng 注入保证可复现）。
HERO_AID_NAMES = (
    "无名斗皇", "游历老者", "黑袍强者", "隐世斗尊", "路过高人",
)

# ---------------------------------------------------------------------------
# 进阶斗帝  [源码 y.java:128 硬编码 + m.java 提示语]
# 「机缘值影响到您最后进阶斗帝成功的几率」
# ---------------------------------------------------------------------------
def tiandi_rate(luck: int, has_diyan: bool) -> float:
    if has_diyan:
        return 1.0
    return min(0.90, 0.30 + int(luck) / 2000.0)


# ---------------------------------------------------------------------------
# 经济
# ---------------------------------------------------------------------------
START_GOLD = 10000          # 实机底部金币 10000
START_LUCK = 1              # 实机第 5 格「机缘 1」
START_STONES = 2            # 新手礼：够强化一次（原版初始没有，实机提示炎星石不足）
STONE_PRICE = 600           # 宝库卖炎星石
START_HP_POTION = 5         # 实机包裹：回灵丹 ×5
#: 宝库单次购买上限（2026-11-20 用户「买东西别限制 99」）。
#: 真正的约束是金币（core.shop_buy 里 total > gold 直接挡），这个数只防
#: 「买 999999999999」这类手滑把显示/存档撑爆；9999 已远超正常采买量。
SHOP_BUY_MAX = 9999

#: 商店里除药材外的固定商品
SHOP_EXTRA = [
    {"name": "炎星石", "price": STONE_PRICE, "desc": "强化玄重尺的唯一材料，每次强化消耗 1 块起"},
]


# ---------------------------------------------------------------------------
# 傀儡系统（2026-11-18 从零考据，见 docs/斗破傀儡_原文证据台账_2026-11-18.md）
# 数字均 [调参]，非原著定值；考据硬事实见台账 E01~E45。
# ---------------------------------------------------------------------------
#: 三档（E05/E06/E07/E08）：首炼恒人级铜，雷淬升级 人→地→天。
PUPPET_TIERS = ("人级傀儡", "地级傀儡", "天级傀儡")
PUPPET_SKINS = ("铜", "银", "金")
PUPPET_TIER_BY_IDX = {0: ("人级傀儡", "铜"), 1: ("地级傀儡", "银"), 2: ("天级傀儡", "金")}

#: 境界闸（E31，原著未写死星级，此处为 [调参]）；realm_index: 斗之气0 斗者1 斗师2 大斗师3 斗灵4 斗王5 斗皇6 斗宗7 斗尊8 …
PUPPET_REALM_MIN = {0: 2, 1: 7, 2: 8}   # 人级≥斗师(2)、地级≥斗宗(7)、天级≥斗尊(8)
#: 灵魂境界闸（E22/E23/E24）：炼药师品阶下标（ALCHEMIST 索引，七品为门槛 [调参]）
PUPPET_ALCH_MIN = 6                      # ALCHEMIST 索引 6 = 七品（E22 触灵境门槛）
#: 控火闸（E16/E25）：累计炼药次数 / 高阶丹药炼成数门槛 [调参]
PUPPET_CRAFT_COUNT_MIN = 20
PUPPET_HIGH_GRADE_MIN = 1
#: 多高阶算「高阶丹药」：成品品阶 ≥ 此档才计入 ``high_grade_crafts``。
#: 2026-11-20 修：这两个字段以前**从没被写过**（只有测试手塞），控火闸永远不过 ——
#: 现在 ``core.craft()/craft_batch()`` 每次开炉都真实累加。
PUPPET_HIGH_GRADE_FROM = "五品"
#: 斗气续航（E29/E30/E45）：全程消耗 = ``mp_max × 系数``，[调参]
#: 2026-11-20 修 bug：旧值 ``(1.0, 1.6, 2.4)`` —— 斗气上限就是 ``mp_max``，
#: 系数 >1 意味着地级/天级**永远开不了炉**（旧代码只走 tier0 所以从没暴露）。
#: 现改成「占上限的比例」：人级 6 成、地级 8 成、天级须满。
PUPPET_MP_FACTOR = {0: 0.60, 1: 0.80, 2: 1.00}

#: 雷淬阈值（E07/E08/E41/E42）：purity 达阈值升档；天级封顶。
#: **索引 = 当前档**，值 = 升到下一档所需 purity。
#: 2026-11-20 修 bug：旧表 ``(0, 60, 100)`` 被按 ``[tier]`` 读，tier=0 时门槛
#: 取到 0 → 第一次雷淬（purity 12）就直接跳地级，5 次封顶天级。
#: 现改为 ``(60, 100)``：0→1 需 60（5 次）、1→2 需 100（再 9 次，共 14 次）。
PUPPET_PURITY_MAX = 100
PUPPET_TIER_UP_PURITY = (60, 100)
PUPPET_TEMPER_COST = 800               # 每次雷淬金币 [调参]
PUPPET_TEMPER_STONE = 1                # 每次雷淬消耗「雷淬石」枚数 [调参]
PUPPET_TEMPER_PURITY = 12              # 每次雷淬 purity 增量 [调参]

#: P1-1 战损（D05/N02/E42）：本场护甲被打空 → damaged=True 写库（跨场状态），
#: 战损时攻击 ×0.8、护甲减半，直到一次雷淬令 temper() 清零修复。
#: 乘在基数上、CAP 仍在 puppet_expected_damage 末端落一次 —— 战损只会更低，
#: 不会顶破「不抢风头」的输出封顶。单场护甲 _pup_hp 每场重置，与此字段分离。
PUPPET_DAMAGED_ATK_MULT = 0.80         # 战损攻击倍率 [调参]
PUPPET_DAMAGED_ARMOR_MULT = 0.50       # 战损护甲倍率 [调参]

#: P1-2 雷力三档（D04/N06，第1531章「黑魔雷半月一星」）：按**当前档位**决定
#: 每次雷淬的纯度增量，索引=tier。人级丹雷 +12（沿用现状）、地级五色丹雷 +25、
#: 天级黑魔雷 +40。确定性分档，不掷骰、不新增材料 —— 忠于「养成链全确定」契约
#: 与 D04「+12/+25/+40 单调扩展」。tier=2 的 +40 喂给天级雷纹长线（见下）。
PUPPET_LEI_POWER_GAIN = (12, 25, 40)
#: 天级雷纹长线（D03/Q4/N07）：天级 purity 不再封死 100，继续按黑魔雷累加；
#: 每 PUPPET_VEIN_PURITY 点纯度折 1 层雷纹，每层护甲 +PUPPET_VEIN_ARMOR_STEP
#: （相对值，喂给 guard_profile 反解前的护甲），上限 PUPPET_VEIN_MAX 层。
#: 🚨 雷纹只加护主护甲/耐久（有落点）；「命中」在本作傀儡击是必中固定伤害、
#:    没有命中判定，加了就是死字段 —— 如实不实现，不伪造机制。
PUPPET_VEIN_PURITY = 40                # 每层雷纹所需超量 purity [调参]
PUPPET_VEIN_MAX = 5                    # 雷纹层数上限 [调参]
PUPPET_VEIN_ARMOR_STEP = 0.12          # 每层护甲相对增量 [调参]
#: 渐进染色（N06 第1160章「最后一只银掌转金」）：名录卡按 purity 画银→金进度格数。
PUPPET_DYE_STEPS = 10                  # 染色进度格数（0..10）[调参]

#: P2 收服印记强度（D06/E03/N03，「若非亲手炼制过无法探知」「印记太强无法抹除」）：
#: 确定性不等式，不掷骰。印记强度=残傀档位+1；抹除所需炼制经验=强度×每点系数。
#: 探知前提=亲手炼过至少 PUPPET_MARK_DISCOVER_MIN 具（craft_count）。
PUPPET_MARK_DISCOVER_MIN = 1           # 探知印记的炼制次数前提 [调参]
PUPPET_MARK_EXP_PER = 5                # 每点印记强度所需炼制经验 [调参]
#: P2 融魂反噬（D07/Q3/E04）：100% 发生，由「设陷阱」步骤拦下，不掷骰。
#: 未设陷阱直接融魂 → 如实报错退材料，绝不静默扣钱。
PUPPET_AWAKEN_TRAP_SOUL = 1            # 设陷阱额外消耗的「无主灵魂」[调参]
#: 进阶玩法（E37/E43/E44）：收服无主残傀 / 熔入残魂生灵智（北王式）。
PUPPET_CLAIM_COST = 600                # 收服金币 [调参]
PUPPET_AWAKEN_COST = 1000              # 融魂金币 [调参]
PUPPET_AWAKEN_SOUL = 1                 # 融魂消耗「无主灵魂」枚数 [调参]

#: P3 养魂（E09/N08，第757章「养魂涎」——原著唯一给出的「灵魂可养」手段）。
#:
#: 口径（**别改回去**）：材料决定的 ``soul_strength`` 是**基础值、永不改写**，
#: 它同时是木桶判档与 ``_material_mult`` 的锚；养魂的收益单独记在 ``soul_fed``
#: （0..上限）。有效灵魂 = ``soul_strength + soul_fed``，由
#: ``puppet_core.soul_total()`` 唯一读取。
#:
#: 为什么不能直接加到 ``soul_strength`` 上：那样「已喂多少」无从还原，
#: 上限就失去定义（900 底和 3000 底都能喂到同一列），迟早被喂穿。
#: ``soul_fed=0`` 时数值与改造前逐字节相同 ⇒ 老存档零回归。
#:
#: 每枚 +10、上限 200 —— 设计文档 §1.2 U08 拍板值；确定性、不掷骰。
PUPPET_SOUL_FEED_GAIN = 10             # 每枚养魂涎的灵魂增量 [调参]
PUPPET_SOUL_FEED_MAX = 200             # 养魂累计上限 [调参]
PUPPET_SOUL_PILL = "养魂涎"            # 养魂消耗的丹药名（唯一源）

#: 战力口径（E09/E32/E33 / 第900章「等级之差取决炼制材料以及炼制之火」）：
#: **战力由躯体境界决定，档位只负责封顶**。
#:
#: 三轮演进，别再改回去：
#:   ① 初版 `PUPPET_TIER_BASE=(120,900,4200)` 是绝对常数 → lv81 人级只占玩家输出
#:      0.2%，用户原话「低得离谱」，实测确认属实。
#:   ② 第二轮改成「玩家每回合输出 × (0.15/0.30/0.50)」→ 落到纯比例：lv81 人级
#:      5.5 千、地级 1.1 万，与原著「地妖傀 = 五星六星斗宗」完全对不上；而若按
#:      同境界给满额，lv20 会到 1134%（实测），低等级直接崩。
#:   ③ 第三轮（用户「我觉得傀儡应该有原著中说的，有对应斗宗或者斗尊级别的
#:      战力」）：战力锚**躯体境界**，档位只做「不抢风头」的上限。
#:
#: 傀儡每回合原始伤害
#:   = min(躯体境界标尺输出, 玩家每回合输出 × CAP[档]) × 材料系数
#:   * 躯体境界标尺输出：`puppet_core.puppet_realm_lv()` 把「七星斗宗之躯」折成
#:     「五星斗宗」（第757处），再取该等级的标尺玩家输出 —— 于是「人级地妖傀
#:     ≈ 五星斗宗」在数值上成立，而不是一句文案。
#:   * CAP：玩家比傀儡弱时往下压，防止「一具高级傀儡碾穿全场」（抢风头红线）。
#: ⚠️ 别叫 ``PUPPET_TIER_CAP`` —— 那是「材料档位封顶天级」的旧常量（值 2），
#:    同名会被后面那段覆盖，战力封顶静默失效（本轮踩过一次）。
PUPPET_ATK_CAP = (0.55, 0.80, 0.95)
#: 躯体小星折扣（第757处）：用「一名七星斗宗强者的躯体」（第906章）炼出的地妖傀，
#: 「恐怕不会比一名五星乃至六星的斗宗强者弱」⇒ 战力 = 躯体境界 − 2 星。
#: 躯体未标小星（``body_star=0``，如「强者躯体」「斗尊强者之躯」）时按一星算。
PUPPET_BODY_STAR_PENALTY = 2
#: ``body_realm`` 缺失/不识别的老存档回落值：该档**标准躯体**折算出的等级。
#:   人级=强者躯体(斗皇·未标星) → 斗皇一星 lv55
#:   地级=斗宗强者之躯(七星)    → 五星斗宗 lv68
#:   天级=斗尊强者之躯(未标星)  → 斗尊一星 lv73
#: 走 ``core.tower_ref_player(lv)``，与爬塔同一套标尺，不另造一份假玩家。
PUPPET_TIER_REALM_LV = (55, 68, 73)
#: 材料系数（同一档**之内**的差异；基准 core7 / soul900 / metal2 / fire7 = 1.00）
PUPPET_CORE_STEP = 0.02        # 魔核每高 1 阶 +2%
PUPPET_SOUL_STEP = 0.00006     # 灵魂每高 1 点 +0.006%（3000 点 = +12.6%）
#: 怪血反解只按傀儡输出的这个比例变厚。
#: 反解若全额计入（旧口径 1.0），「多一个打手」与「怪血同步变厚」在数学上
#: 完全对消 —— 带傀儡零收益，玩家当然觉得没意义。0.55 → 带傀儡净赚 45%。
PUPPET_ENEMY_COMP = 0.55

#: 结阵加成上限（2026-11-20 第二轮新增，堵刷分后门）。
#: 原实现 `formation_count()` 数的是**玩家拥有的全部傀儡**、无上限，每具 +15%。
#: 人级傀儡材料极便宜（精铁 + 火属性魔核 + 无主灵魂），凑 5 具就能白拿 +60%，
#: 叠上本轮「锚定玩家输出」的新口径会变成天级 65.5%×1.6 = 104% —— 傀儡比人还强。
#: 现封顶 3 具（+30%），既保住「结阵有用」，又不让堆垃圾傀儡当外挂。
PUPPET_FORMATION_MAX = 3
#: 结阵每多一具的加成（与 PUPPET_FORMATION_MAX 配套）。
PUPPET_FORMATION_STEP = 0.15
#: P0-1（2026-11-20 设计文档）：成阵不是加百分比，是**临时升档**。
#: 原著「收服」章：十具地妖傀成阵 → 领头暂化金色天妖傀、可抗二星斗尊，
#: 战后「金光消散，再度化为地妖傀」。本作封顶 3 具，故门槛取 3。
#: 只对人/地级生效；天级无更高档，走紫皮变体（护主增强，输出不叠倍率）。
PUPPET_FORMATION_BURST_N = 3
PUPPET_FORMATION_BURST_TIERS = (0, 1)

# ---------------------------------------------------------------------------
# 傀儡护主（2026-11-20 第三轮）：大额伤害时有较低概率替主人挡下
#
# 原著依据（原文见 docs/傀儡护主挡伤_原著考据与机制调研_2026-11-20.md）：
#   * 第898处「连**地妖傀**都是被其一抓下，**差点彻底报废**，以他的身体强度，
#     恐怕一捏下，就得**爆成一堆血肉**」—— 傀儡替主承受致命一击，代价是自身近废。
#   * 第920处「用**傀儡来抗住**丹雷么」「**抗一两道**五色丹雷或许还行，可想要靠它
#     **度过丹雷，无疑是痴人说梦**」—— 能挡，但挡不住全部、也挡不了整场。
#   * 第952处「被轰击得**千疮百孔**…**频临报废**」「已经**不能够再用来抗敌**」
#     —— 有耐久，坏到极限就退出战斗。
#
# 设计三条硬约束（用户原话「既不能抢了玩家角色风头，傀儡也不能发挥不出作用」）：
#   1. 只挡**大额**伤害（占气血上限 12% 以上），小招不触发 —— 免得太勤快像主角；
#   2. 挡下后**玩家仍承受 35%**（`PUPPET_GUARD_LEAK`）—— 挡伤是减伤，不是免疫；
#   3. 概率封顶 40% —— 满配也不能稳定护，玩家永远得自己扛。
# ---------------------------------------------------------------------------
#: 「大额」判定线：单次伤害 ≥ 玩家气血上限 × 此比例才掷挡伤骰。
#: ⚠️ 必须是**怪单回合伤害同一量级**的值：实测同阶怪一击约占玩家气血 7%，
#: 阈值定 12% 时永远过不了线（护主 0.2 次/场，等于没有）。6% = 「这一下打掉你
#: 一成不到的血就算疼了」，普通重击与暴击都能触发，小擦伤不触发。
PUPPET_GUARD_THRESHOLD = 0.06
#: 人/地/天三档基础挡伤概率（用户「较低概率」→ 人级只有一成半）。
#: 2026-11-20 调参：实测一场战斗只有 ~1.8 次「落账的挨打」（4~5 回合内怪出手
#: 4 次、玩家闪掉 1.5 次），所以「单场至少护主一次」≈ 1−(1−p)^1.8。
#: 旧值 (0.12,0.20,0.28) 实测人级仅 19%、天级 34% —— 平均 5 场才见一次，
#: 用户会判定「发挥不出作用」。现提到 (0.18,0.25,0.32)：真跑 D 段人级约 30%、
#: 天级约 50%，仍是「较低概率」（人级不到两成），没有变成稳定护盾。
#: ⚠️ 不能靠再提概率来加强体感：分摊比 = 傀儡挡下量/玩家总承伤，它的理论上限
#: 就是 (1−PUPPET_GUARD_LEAK)=65%。实测天级已到 44%，再提就摸 50% —— 那才是
#: 真正的「抢了玩家风头」。想更明显请动 leak 或阈值，别再动这三个数。
PUPPET_GUARD_CHANCE = (0.18, 0.25, 0.32)
#: 概率上限：满配 + 结阵也不能稳定护主。
PUPPET_GUARD_CAP = 0.40
#: 挡下之后**玩家自己仍要承受**的比例。0 = 全额免伤（抢风头，禁用）。
PUPPET_GUARD_LEAK = 0.35
#: 傀儡气血 = 玩家气血上限 × 此值。
#: ⚠️ 刻意比玩家**薄**：傀儡是消耗品，原著里挡几下就「千疮百孔、频临报废」
#: （第952处）。若做成比玩家还厚，血条就成了纯展示数字，挡伤变成永动护盾。
PUPPET_HP_RATIO = (0.25, 0.40, 0.55)
#: 傀儡受击的**穿透率**（taken = 挡下量 × 此值，越低越硬）。
#:
#: 🚨 2026-11-20 调参踩坑，别再改回「玩家防御 × 比例」：
#: 旧口径 `PUPPET_ARMOR_RATIO = (0.35, 0.55, 0.75)` 按玩家防御线性放大护甲，
#: 但 `core.mitigation()` 是双曲线 —— 玩家防御从 lv10 的 570 涨到 lv81 的
#: 8982（15.7 倍），护甲同步放大后**减伤率从 19.9% 飙到 79.7%**。真跑扫描
#: （tmp/scan_guard_armor.py）量出：lv81 天级要挡 **33.6 次**才报废，一场
#: 最多挨 3 次 —— 血量与护甲整栏是死数据，玩家永远看不到傀儡掉血。
#: 低等级需要 ratio≈0.98、高等级需要 ratio≈0.03，**固定比例在数学上无解**。
#: 现改为锚定「穿透率」再用 `armor = MITIGATION_W × (1/take − 1)` 反解护甲：
#: 无论玩家多少级，挡一次都稳定吃掉挡下量的 78%/68%/58%（报废节奏 3/4/5 次）。
PUPPET_ARMOR_TAKE = (0.78, 0.68, 0.58)
#: 结阵每多一具，挡伤概率 +6 个百分点（直接加到概率上，不乘）。
PUPPET_FORMATION_GUARD_STEP = 0.06
#: 傀儡自身残血时反而更容易护不住（它自己快废了）：概率 × 此系数。
PUPPET_GUARD_HURT_MULT = 0.5
PUPPET_GUARD_HURT_AT = 0.30
#: P0-2 有灵（awakened）：原著「北王」只需心念一动便主动出手、悍不畏死拖住强敌。
#: 落地为两条可观测行为：① 小额伤害也进入挡伤判定（大额阈值乘此系数）；
#: ② 傀儡自身残血时不再乘 PUPPET_GUARD_HURT_MULT 惩罚。
#: 🚨 概率上限 GUARD_CAP=0.40、泄漏 GUARD_LEAK=0.35 不因有灵放宽 —— 不抢风头。
PUPPET_AWAKEN_GUARD_THRESHOLD_MULT = 0.5

#: 战斗集成唯一函数口径：傀儡每回合期望输出，fight 与 damage_schedule 都只调它。
#: 这条是红线——两处同源，谁只改一处守门必红。

#: 傀儡专用材料名（真实列存 puppet_materials 表，不进 bag JSON）
#: 2026-11-20 多配方：追加五维分档材料，全部原著真名（正文最高只到七阶魔核）。
#: 唯一「既是材料、又代表传承」的那一件：拿到它必须同时点亮 eligibility.lore。
#: 遗迹掉落本来就在同一处 set_lore（commands.cmd_puppet_ruin），赠送也必须一样，
#: 否则受赠者会拿到一张永远用不掉的竹简（2026-11-20 第十轮实测发现）。
PUPPET_LORE_MATERIAL = "天妖傀竹简"

PUPPET_MATERIALS = (
    "天妖傀竹简", "强者躯体", "火属性魔核", "无主灵魂", "暗金玄铁", "雷淬石",
    "斗宗强者之躯", "斗尊强者之躯", "北龙王之躯",
    "七阶魔核", "高阶魔核",
    "斗宗强者之魂", "斗尊强者之魂", "北龙王残魂",
    "精铁", "寒铁", "焰陨玄铁",
)

#: 属性→战斗作用短标（另建表，不污染 STAT_BATTLE_ROLE，守门 test_battle_depth）
PUPPET_STAT_ROLE = {
    "力量": "肉身", "防御": "躯体", "体力": "耐久", "斗气": "无",
}

#: 遗迹自动连打一次最多替玩家自动服几颗丹（气血/斗气共用一份预算）。
#: 2026-11-20 第十轮：六段连打若按 `auto_pill=True` 的默认（每场 2 颗）走，
#: 一趟能吞掉十几颗回灵丹且玩家毫不知情；而「丹药管够」时力竭永远不会到来，
#: 连打就成了无限续杯。与 core 里深副本 `auto_pill=False` 同一考虑，这里给一个
#: 更宽松但**有硬顶**的口径：整趟最多 2 颗，够救一次急，不会洗空包裹。
PUPPET_RUIN_PILL_CAP = 2

#: 傀儡遗迹日副本（2026-11-18 三段 + 11-20 三段 + P3 第七段）
#:
#: 🚨 难度口径（2026-11-22 用户选定「标尺固定难度」，与爬塔同一机制）：
#:   怪的全部数值由 ``power_rec`` 反查出的**标尺玩家**反解，与打它的真人战力无关。
#:   玩家变强 ⇒ 同一段打得更快、更扛打（真碾压），而不是怪同步变厚原地踏步。
#:   ``power_rec`` 是**后台字段**——只喂 core.ruin_ref_lv 定难度，绝不进卡面/文案；
#:   守门 tests/test_doupo_ruin_fixed.py::test_power_rec_never_shown_to_frontend。
#:   数值不是手拍常数：每段 power_rec == power(tower_ref_player(该段 lv))，
#:   即现成参考战力曲线上该段的点（守门 test_power_rec_matches_ref_curve）。
#: 掉落只进 puppet_materials 真实列。
PUPPET_RUIN_STAGES = (
    {"name": "魔炎谷库房", "monster": "守库尸卫", "lv": 30, "hard": 0.90,
     "power_rec": 18645,
     "exp_mul": 1.10, "gold_mul": 1.10, "kind": "魔炎谷",
     "drops": {"强者躯体": 1, "暗金玄铁": 1},
     "desc": "竹简原藏之处，残存尸卫守着未及带走的躯体与玄铁。"},
    {"name": "天墓·能量之层", "monster": "远古凶魂", "lv": 55, "hard": 0.86,
     "power_rec": 57585,
     "exp_mul": 1.25, "gold_mul": 1.20, "kind": "天墓",
     "drops": {"无主灵魂": 1, "火属性魔核": 1},
     "desc": "能量之层游荡的无主凶魂，需炼尽神智才能取作傀儡之魂。"},
    {"name": "虚空雷池", "monster": "雷池守护者", "lv": 75, "hard": 0.80,
     "power_rec": 97185,
     "exp_mul": 1.50, "gold_mul": 1.40, "kind": "虚空雷池", "is_boss": True,
     "drops": {"雷淬石": 2, "天妖傀竹简": 1},
     "desc": "雷霆淬体之地，守者掌天妖傀竹简，胜之方得传承。"},
    # 2026-11-20 多配方落地：追加三段（严格接在旧三段之后，旧段号/掉落不动）
    {"name": "宗骸祭坛", "monster": "守坛宗魂", "lv": 60, "hard": 0.84,
     "power_rec": 58245,
     "exp_mul": 1.30, "gold_mul": 1.25, "kind": "宗骸祭坛",
     "drops": {"斗宗强者之躯": 1, "寒铁": 1},
     "desc": "祭坛上还留着斗宗强者的躯身，与一块未熔的寒铁同被封在石中。"},
    {"name": "尊骨陵寝", "monster": "陵寝尊者", "lv": 78, "hard": 0.78,
     "power_rec": 97581,
     "exp_mul": 1.55, "gold_mul": 1.45, "kind": "陵寝尊者·elite",
     "drops": {"斗尊强者之躯": 1, "焰陨玄铁": 1},
     "desc": "陵寝深处的斗尊骨架尚未朽尽，陪葬的焰陨玄铁仍有余温。"},
    {"name": "圣地残殿", "monster": "残殿圣卫", "lv": 90, "hard": 0.72,
     "power_rec": 121605,
     "exp_mul": 1.80, "gold_mul": 1.70, "kind": "圣地残殿", "is_boss": True,
     "drops": {"北龙王之躯": 1, "北龙王残魂": 1, "高阶魔核": 1},
     "desc": "圣卫守着一具斗圣躯身与它的残魂，魔核在胸腔里跳了千年。"},
    # P3 远古人傀（E12/N09，第1235章「混乱」）。原著原文：
    #   「以特殊手段将强者的灵魂抽出人体，然后封印于傀儡躯体内……不仅拥有着
    #     神智，而且还能施展斗技，远比寻常傀儡棘手」
    #   「这些傀儡的身体表面上，似乎被缠绕上了一层层灰白色的奇异布条……
    #     即便是斗宗强者的全力一击，都是无法震断这种奇异布条」
    # ⇒ 三件事全部落地：会施斗技（角色包 skill「裂山枪」，原著真名）、
    #   布条卸力（``cloth`` 额外防御，见 core.monster_scaled）、
    #   有神智（登场旁白 + 台词，走 MONSTER_CHARACTERS 同一套）。
    # ``kind`` 不带 boss 字样：面板按普通怪算（与「塔·boss」那套区分开）。
    # 标定依据 tmp/probe_ancient_balance2.py（lv75 裸玩家 60 场）：圣地残殿 93%，
    # 本段普通面板 82%、boss 面板仅 58~65%（技能概率还会翻倍到 52%，变成每两回合
    # 一枪）。取普通面板 + 全表最狠的 hard=0.70：攻/防都高于第六段，胜率低一档，
    # 打完不至于让人不想再来（每天还要靠它掉雷淬石）。
    {"name": "斗圣主殿", "monster": "远古人傀", "lv": 95, "hard": 0.70,
     "power_rec": 175065,
     "exp_mul": 1.90, "gold_mul": 1.80, "kind": "斗圣主殿", "is_boss": True,
     "cloth": 0.60,
     "drops": {"北龙王残魂": 1, "高阶魔核": 1, "雷淬石": 3},
     "desc": "主殿石座四畔的石雕尽数睁眼，灰白布条缠身、血枪犹在滴血——"
             "它们记得自己曾是谁，也记得主上临去前那道命令。"},
)


# ---------------------------------------------------------------------------
# 傀儡多配方体系（2026-11-20）
#
# 原著第900章：「妖傀分三等，天地人，等级之差，取决炼制材料以及炼制之火」
# 原著第906章：「光是为了炼制这地妖傀，便是消耗了一名七星斗宗强者的躯体，
#               一枚七阶魔核，以及一道斗宗强者的灵魂」
# 原著第906章：「天为金，地为银，人为铜」
#
# 结论：档位由「五维材料里最弱的那一环」决定（木桶），不是首炼恒人级。
# 全部材料名取自原著正文——正文最高只出现「七阶魔核」，故不设八/九阶。
# ---------------------------------------------------------------------------
#: 躯体档：材料名 → (档位, 躯体境界, 星级)
PUPPET_BODY_TIERS = {
    "强者躯体":     (1, "斗皇", 0),
    "斗宗强者之躯": (2, "斗宗", 7),   # 第906章「一名七星斗宗强者的躯体」
    "斗尊强者之躯": (3, "斗尊", 0),   # 第906章「斗尊强者的躯体」
    "北龙王之躯":   (4, "斗圣", 5),   # 第1530章 北王，五星斗圣之躯
}
#: 魔核档：材料名 → (档位, 魔核阶, 属性)
#: 正文最高只到「七阶魔核」（第906章），「高阶魔核」对应同章「阶魔核」脱字处。
PUPPET_CORE_TIERS = {
    "火属性魔核": (1, 4, "火"),
    "七阶魔核":   (2, 7, "火"),
    "高阶魔核":   (3, 9, "火"),
}
#: 灵魂档：材料名 → (档位, 灵魂强度)
PUPPET_SOUL_TIERS = {
    "无主灵魂":     (1, 900),
    "斗宗强者之魂": (2, 1500),   # 第906章「一道斗宗强者的灵魂」
    "斗尊强者之魂": (3, 2400),   # 第906章「斗尊灵魂」
    "北龙王残魂":   (4, 3000),   # 第1530章 北王残魂
}
#: 金属档：材料名 → (档位, metal_grade)。暗金玄铁 grade=2 是旧档基准，
#: 老傀儡全存 2 → 数值系数恰为 1.0，不会因新表变动。
PUPPET_METAL_TIERS = {
    "精铁":     (1, 1),   # 第38章
    "暗金玄铁": (2, 2),   # 旧档（黑角域）
    "寒铁":     (3, 3),   # 第391章
    "焰陨玄铁": (4, 4),   # 第100章
}
#: 炼制之火三档（不消耗，只判档）：B 兽火 → A 异火 → S 异火。
#: 值即写库的 fire_tier；7 是旧档基准，老傀儡全存 7 → 系数恰为 1.0。
PUPPET_FIRE_GRADES = {"B": (1, 7), "A": (2, 8), "S": (3, 9)}
#: 材料档位上限（超过即封顶天级）与档位→傀儡 tier 的换算：tier = min(五档) − 1，封顶 2
PUPPET_SLOT_MAX = 4
PUPPET_TIER_CAP = 2
#: 金属 / 火 的微系数（同档内拉开差距，不动档位）：
#:   metal_mult = 1 + (metal_grade − 2) × 0.05
#:   fire_mult  = 1 + (fire_tier − 7) × 0.04
#: 基准点取旧档值，故老傀儡（grade2 / fire7）倍率恒为 1.0，逐字节不变。
PUPPET_METAL_MULT_STEP = 0.05
PUPPET_FIRE_MULT_STEP = 0.04

#: 推荐配方（18 条，> 设计文档要求的 12 条）。
#: 每条固定五维材料，数值全部由材料决定（不用手写倍率，避免卡面与数值两张皮）。
PUPPET_RECIPES = (
    # --- 人级（铜）：五维最弱一环=1 ---
    {"name": "铜尸傀", "body": "强者躯体", "core": "火属性魔核",
     "soul": "无主灵魂", "metal": "精铁", "fire": "B",
     "note": "最省的一具，能站起来挡刀就算成"},
    {"name": "铁卫傀", "body": "强者躯体", "core": "火属性魔核",
     "soul": "无主灵魂", "metal": "暗金玄铁", "fire": "B",
     "note": "玄铁淬体，比铜尸傀硬一分"},
    {"name": "重击傀", "body": "强者躯体", "core": "火属性魔核",
     "soul": "无主灵魂", "metal": "寒铁", "fire": "B",
     "note": "寒铁沉，出手慢却重"},
    {"name": "疾风傀", "body": "强者躯体", "core": "七阶魔核",
     "soul": "无主灵魂", "metal": "精铁", "fire": "B",
     "note": "七阶魔核驱动轻躯，走的是快路"},
    {"name": "残躯傀", "body": "强者躯体", "core": "七阶魔核",
     "soul": "无主灵魂", "metal": "寒铁", "fire": "B",
     "note": "残躯配好核，人级里的顶格"},
    {"name": "旧制傀", "body": "强者躯体", "core": "七阶魔核",
     "soul": "无主灵魂", "metal": "暗金玄铁", "fire": "B",
     "note": "老法子：强者之躯 + 七阶魔核 + 玄铁"},
    # --- 地级（银）：五维最弱一环=2 ---
    {"name": "地妖傀", "body": "斗宗强者之躯", "core": "七阶魔核",
     "soul": "斗宗强者之魂", "metal": "焰陨玄铁", "fire": "A",
     "note": "原著正法：七星斗宗之躯、七阶魔核、斗宗之魂"},
    {"name": "银甲傀", "body": "斗宗强者之躯", "core": "七阶魔核",
     "soul": "斗宗强者之魂", "metal": "暗金玄铁", "fire": "A",
     "note": "玄铁版地妖傀，省一块焰陨玄铁"},
    {"name": "银锋傀", "body": "斗宗强者之躯", "core": "七阶魔核",
     "soul": "斗宗强者之魂", "metal": "寒铁", "fire": "A",
     "note": "寒铁淬锋，银档里最利的一具"},
    {"name": "银煞傀", "body": "斗宗强者之躯", "core": "高阶魔核",
     "soul": "斗宗强者之魂", "metal": "寒铁", "fire": "A",
     "note": "魔核越阶，躯与魂压在银档"},
    {"name": "银骨傀", "body": "斗尊强者之躯", "core": "七阶魔核",
     "soul": "斗宗强者之魂", "metal": "暗金玄铁", "fire": "A",
     "note": "斗尊骨架配银档魔核，根骨最厚"},
    {"name": "银魂傀", "body": "斗宗强者之躯", "core": "七阶魔核",
     "soul": "斗尊强者之魂", "metal": "暗金玄铁", "fire": "A",
     "note": "魂强躯弱，认主后最听话"},
    # --- 天级（金）：五维最弱一环=3 ---
    {"name": "金身傀", "body": "斗尊强者之躯", "core": "高阶魔核",
     "soul": "斗尊强者之魂", "metal": "寒铁", "fire": "S",
     "note": "斗尊三件套齐备，寒铁淬成金身"},
    {"name": "天妖傀", "body": "斗尊强者之躯", "core": "高阶魔核",
     "soul": "斗尊强者之魂", "metal": "焰陨玄铁", "fire": "S",
     "note": "原著所称天妖傀，焰陨玄铁淬体"},
    {"name": "金魂傀", "body": "斗尊强者之躯", "core": "高阶魔核",
     "soul": "北龙王残魂", "metal": "寒铁", "fire": "S",
     "note": "北王残魂入主，金档里灵性最足"},
    {"name": "金煞傀", "body": "斗尊强者之躯", "core": "高阶魔核",
     "soul": "北龙王残魂", "metal": "焰陨玄铁", "fire": "S",
     "note": "五维全顶，天级上限"},
    # --- 特殊（圣躯）---
    {"name": "北王傀", "body": "北龙王之躯", "core": "高阶魔核",
     "soul": "北龙王残魂", "metal": "焰陨玄铁", "fire": "S",
     "note": "斗圣之躯入炉，北王本相"},
    {"name": "雷池傀", "body": "北龙王之躯", "core": "高阶魔核",
     "soul": "斗尊强者之魂", "metal": "寒铁", "fire": "S",
     "note": "寒铁引雷，专为雷淬而生"},
)
PUPPET_RECIPE_BY_NAME = {r["name"]: r for r in PUPPET_RECIPES}

#: 材料获取途径（玩家可见文案；缺料提示直接引用，不再让玩家自己猜去哪打）
PUPPET_MATERIAL_SRC = {
    "强者躯体": "傀儡遗迹·一段「魔炎谷库房」",
    "火属性魔核": "傀儡遗迹·二段「天墓·能量之层」",
    "无主灵魂": "傀儡遗迹·二段「天墓·能量之层」",
    "暗金玄铁": "傀儡遗迹·一段「魔炎谷库房」",
    "雷淬石": "傀儡遗迹·三段「虚空雷池」",
    "天妖傀竹简": "傀儡遗迹·三段「虚空雷池」",
    "斗宗强者之躯": "傀儡遗迹·四段「宗骸祭坛」",
    "寒铁": "傀儡遗迹·四段「宗骸祭坛」／野外 lv55+ 精英",
    "斗尊强者之躯": "傀儡遗迹·五段「尊骨陵寝」",
    "焰陨玄铁": "傀儡遗迹·五段「尊骨陵寝」／野外 lv75+ 精英",
    "北龙王之躯": "傀儡遗迹·六段「圣地残殿」",
    "北龙王残魂": "傀儡遗迹·六段「圣地残殿」",
    "七阶魔核": "野外 lv60+ 精英／周本深层",
    "高阶魔核": "野外 lv80+ BOSS／傀儡遗迹·六段",
    "斗宗强者之魂": "野外 lv60+ 精英／周本深层",
    "斗尊强者之魂": "野外 lv80+ BOSS／周本 BOSS",
    "精铁": "野外 lv30+ 任意怪",
}

#: 野外/周本补料（追加在 _roll_drops 既有判定之后，保老 rng 序列）：
#: 材料名 → (最低怪物 lv, 千分率, 只允许精英/BOSS)
PUPPET_DROPS = {
    "精铁":         (30, 40, False),
    "寒铁":         (55, 22, False),
    "焰陨玄铁":     (75, 14, False),
    "七阶魔核":     (60, 16, True),
    "高阶魔核":     (80, 12, True),
    "斗宗强者之魂": (60, 14, True),
    "斗尊强者之魂": (80, 10, True),
}
#: 精英/BOSS 额外必掉（按 lv 门槛从上往下取第一件，每次战斗最多一件）
PUPPET_DROPS_SURE = (
    (90, "高阶魔核"),
    (78, "斗尊强者之魂"),
    (60, "七阶魔核"),
)

# ---------------------------------------------------------------------------
# 宗门特色机制 · P0 三条（2026-11-24 用户定案，设计文档 docs/斗破宗门特色机制_设计文档_2026-11-24.md）
# 全部锚定现有量、走现有单一真源通道；系数为 0 是合法的"关闭该机制"，显式判 0 不用 or。
# ---------------------------------------------------------------------------
#: 古族 · 族纹：血脉品级由等级派生，每档给六维比例加成（设计文档 U01/U07）。
#: 档位门槛起点升序；加成是比例（0.03=+3%），乘在六维上再走 core.power 现有加权。
GUZU_SECT = "guzu"
CLAN_TIER_PCT = (
    (1, 0.00),    # 一品·金纹之前：无加成
    (20, 0.03),   # 二品
    (40, 0.06),   # 三品
    (60, 0.09),   # 四品
    (80, 0.12),   # 五品·七彩（封顶）
)

#: 丹塔 · 丹会：炼丹成功率加成 = 该系数 × (炼药师品阶序号+1)，即一品 +0.03、
#: 四品 +0.12、七品 +0.21（后者被 CRAFT_RATE_CAP=0.95 截断）。走 craft_rate_for
#: 的 flame_bonus 入参，与 B 线炼药异火共用封顶（设计文档 U02 / §10 三档表）。
DANTA_SECT = "danta"
DANHUI_BONUS_PER_TIER = 0.03

#: 蛇人族 · 封印咒：对怪物降攻比例（0.15=-15%），走 skill_effects 的 debuff_atk
#: 原子 + target_atk_mult 现有通道（设计文档 U03）。整场常驻，持续回合覆盖熔断上限。
SHEREN_SECT = "sherenzu"
SEAL_ATK_RATE = 0.15

# ---------------------------------------------------------------------------
# 宗门特色机制 · P1 三条（2026-11-24 设计文档 §15.2 推荐口径已定，用户「把剩下的做完」）
# 全部锚定现有量、走现有单一真源通道；系数为 0 是合法的"关闭该机制"，显式判 0 不用 or。
# ---------------------------------------------------------------------------
#: 太虚古龙 · 空间穿梭：等级→自然闪避加成百分点（走 base 通道，非 bonus["闪避"]，
#: 避免被丹药 +8 通道限制）。lv100 自然闪避已顶 80，溢出点数转命中（设计文档 §10 P04）。
TAIXU_SECT = "taixugulong"
TAIXU_EVADE_TIERS = (
    (1, 0),    # 未到门槛
    (40, 3),
    (70, 6),
    (90, 10),
)

#: 毒宗 · 仿毒体（明示非天生厄难毒体，守「个人所有不写成门派传承」红线）：
#: 吸血比例 × 根基系数 root_factor（丹药修为占比越高吸血越低，12%→6.6%）。
VENOM_SECT = "duzong"
VENOM_LIFESTEAL = 0.12

#: 天府联盟 · 全属性比例加成 +4%（不锁宗，与魂殿锁死形成对照）。走 stats 六维。
TIANFU_SECT = "tianfu"
TIANFU_ALLSTAT = 0.04


# ============================================================================
# 物品分类（「查看 <物品名>」唯一真源，2026-11-25）
# ============================================================================
# 卡片层（card.build_item_detail_data）与文本层（commands.cmd_view）必须给出
# **同一套分类**，否则会出现「卡里有丹纹段、文本说不认识」这种自相矛盾。
# 所以分类表放在数据层（本文件），两边都只读它。
#
# 用户原话：「加一个查看物品的功能，无论是背包还是宝库还是丹药，这些物品，
# 都加一个查看物品详情的功能……从何而来。其他物品都一样……以药老或者普通商家
# 的口吻描述就行。」⇒ 系统物（药渣/炎星石/玄重尺）没有原著出处，文案只写
# 「它到底干什么」，不编设定、不写章节号。

#: 系统杂项：既不是丹、也不是药材/天材地宝。``kind``=类别行，``rows``=本效段，
#: ``tip``=底部用法。数值引用本文件常量，不写死。
MISC_ITEMS: dict[str, dict] = {
    "药渣": {
        "kind": "炼药残渣",
        "rows": ["炼药失败的残留，每次失败得 %d 份" % CRAFT_FAIL_DROSS,
                 "养火的唯一燃料：发「养火 <火名>」把现有药渣一次喂光"],
        "tip": "发「养火 青莲地心火」喂火升级 · 发「异火」看现有火种",
    },
    "炎星石": {
        "kind": "强化石",
        "rows": ["玄重尺强化的耗材，强化越高越吃石",
                 "战斗、竞技场、天降炎星石都会掉（约 %d%% 一场）"
                 % int(DROP_RATE_STONE / 10)],
        "tip": "发「强化」消耗炎星石强化玄重尺 · 发「人物」看当前武器",
    },
    "玄重尺": {
        "kind": "武器",
        "rows": ["入道时的那柄黑尺，越强化越沉",
                 "强化靠炎星石：发「强化」一次一颗往上叠"],
        "tip": "发「强化」升武器 · 发「人物」看攻击与强化等级",
    },
}


def item_kind(name: str) -> str:
    """物品名 → 类别：``pill`` / ``danwen`` / ``herb`` / ``material`` /
    ``puppet`` / ``misc`` / ``""``（查无此物）。

    「带纹丹」单独一类：它既是丹（本效照旧）又多了赐福，卡面要分别处理。
    ``puppet`` 收两类：``PUPPET_MATERIALS`` 名单 + 按阶命名的``X魔核``
    （魔核是动态生成的，正文最高只到七阶，故按后缀判、限长防误吞）。
    """
    s = str(name or "").strip()
    if not s:
        return ""
    if s in DANWEN_INDEX:
        return "danwen"
    if s in RECIPE_BY_NAME:
        return "pill"
    if s in HERB_BY_NAME:
        return "herb"
    if s in MATERIAL_BY_NAME:
        return "material"
    if s in MISC_ITEMS:
        return "misc"
    if s in PUPPET_MATERIALS or (s.endswith("魔核") and len(s) <= 12):
        return "puppet"
    return ""
