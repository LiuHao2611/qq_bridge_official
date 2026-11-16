# -*- coding: utf-8 -*-
"""game_tools/riven_card.py —— 紫卡分析卡（逐字对齐 Warframe Rabbit「紫卡分析」版式）。

公式（与 koishi-plugin-warframe ``services/riven/analysis.ts`` 完全同源）：

    standard = |base| × disposition × factor × (rank+1)/9      # 截图一律按满级 rank=8
    区间      = [standard×0.9, standard×1.1]，两端都 round(…,1)
    偏差      = |value| / standard − 1     （负词条取惩罚量绝对值）
    综合评分  = 各词条 |偏差| 的均值；字母档用**方向敏感**均值（见 _grade）

校准证据（2026-11-02，用户提供的伯斯顿卡「+192.2/+105.2/+143.8/−54.9」对照
参考机器人截图，逐位吻合）：

    区间   167.1%-204.2% / 91.1%-111.4% / 121.5%-148.5% / -59.4%~-48.6%
    偏差   +3.54% / +3.90% / +6.52% / +1.68%
    综合   3.910(A-)

⚠️ 两条历史错误已纠正（2026-11-02）：
  1. 下限曾用 floor(…,1)，会输出 167.0/121.4/48.5 —— 参考卡是 round，差 0.1；
  2. 偏差基准曾是 ``mid / 0.99``（误以为以「2 正布局」为基准），系统性抬高所有偏差。

倾向口径：**官方 Hotfix 44.1.3 后全部紫卡统一按 1.2 计算**，导出里的
``omegaAttenuation`` 只作历史兼容。截图分析先用 1.2 试所有武器版本，全不匹配才
由 ``_fit_disp`` 依卡面词条反推（反推时卡面会显式提示，不静默改数）。

伤害模型为本项目实现（Rabbit 闭源，不冒充同值）：武器官方面板 + 标准配卡 DPS 模拟，
靶子口径照抄参考卡脚注（Lv170 钢铁之路凶魂百夫长 / 护甲 2700 / 弱点命中 50% / 病毒 /
三硬度节点 1.4s/4.5s/14.3s / 击杀效率取几何平均）。绝对值与 Rabbit 不同，
但同一模型内横向可比——参考卡脚注亦声明「仅适合在相同靶子和计算条件下比较」。

市场数据（REDUN-01 降级链）：Warframe.Market 拍卖（可达）→ 样本 <3 或接口失败
按模板填「暂无」，绝不编造价格（CLOSE-02 精神；DE 拍卖/riven.market 本机代理不可达，
预留 _auction_de 扩展位）。
"""
from __future__ import annotations

import asyncio
import itertools
import json
import math
import re
import statistics
import time
from collections import defaultdict
from pathlib import Path
from urllib.parse import quote as _quote

from nonebot.log import logger

from . import warframe

_DATA_DIR = Path(__file__).resolve().parents[4] / "data" / "warframe_items"

#: 词条组合修正（同 riven.py/_FIX：koishi rivenStatFixFactor）
FIX = {
    "2": {"buff": 0.99, "curse": 0.0},
    "21": {"buff": 1.2375, "curse": -0.495},
    "3": {"buff": 0.75, "curse": 0.0},
    "31": {"buff": 0.9375, "curse": -0.75},
}

#: 增益天花板标定（反推 Rabbit，**按武器类别**，与当前卡词条无关）：Rabbit「最强紫卡」=
#: 该武器理论最优词条组合的裸叠增益 × 归一化系数（武器潜力天花板）。
#: 第30轮重标定（**配卡桶换真实终局配卡后，裸叠基数整体下降，必须重算**）：
#:   * 阿利乌（枪）  裸叠理论最优 +432.3%，伤害目标 62.5，实测增益 +89.9%
#:                    → 0.333 → 天花板 143.9%，实测伤害 62.4（目标 62.5）
#:   * Coda （近战）裸叠理论最优 +490.9%，Rabbit 最强 +138.4%，伤害 11.0
#:                    → 0.28 → 天花板 137.4%，实测伤害 11.0（目标 11.0）
#: 旧值 0.30 是「旧弱配卡桶」下反推的（当时裸叠 +595%），桶一换就失真，故随桶重标。
#: 交叉印证：冰凇 Verglas 裸叠理论最优 **+200.0%** ↔ Rabbit「最强紫卡 +198.1%」，
#: 差 1.9pp —— 说明裸叠侧（不含 calib）与 Rabbit 同源，calib 只是刻度缩放。
#: ⚠️ 每类仅 1 张参考卡标定，待更多 Rabbit 卡收敛。
_OPTIMAL_CALIB_GUN = 0.333     # 枪械（Pistol/Rifle/Shotgun/Kitgun/Archgun）
_OPTIMAL_CALIB_MELEE = 0.28    # 近战（Melee/Zaw）

#: 近战 std **线性等效暴击基线**（不是字面暴击率）。
#: ``_crit_mult`` 是线性近似 ``1+cc×(cm−1)``，而近战毕业 build 走血冲（Blood Rush）持续
#: 高连击，真实 tier 暴击已是红暴击（平均倍率 ~26×）；把它折成线性模型需要 cc≈7.6
#: （= 0.25 基暴率 ×(1+29.5)）。此基线让紫卡「+暴击几率」词条被正确稀释——实测 Coda
#: 卡 +222% 暴击的边际从 +18.2% 降到 +7.1%，整卡当前增益 15.2% 精确命中 Rabbit。
#: 旧值 10.0 是在 phys_neg 浅拷贝污染下凑出来的，污染修掉后失效（会变 +27.2%）。
_MELEE_STD_CC = 29.5

#: 市场分中「不伤市场价值」的 QoL 负词条：这些负词条（更多后坐/变焦/少弹药等）玩家基本
#: 无视，不按 NEG_MARKET 扣分（否则阿利乌 recoil 负会被重复罚，市场分从 85.7 掉到 75.8）。
_MARKET_QOL_NEG = frozenset({
    "Weapon Recoil", "Zoom", "Ammo Maximum", "Magazine Capacity",
    "Reload Speed", "Projectile Speed", "Punch Through", "Status Duration",
})

#: 偏差口径说明（历史遗留常量已移除）：
#: 旧实现把偏差基准归一为 ``mid / 0.99``（误以为参考卡以「2 正布局」为基准），
#: 导致所有偏差被系统性抬高。参考卡实际口径是 dev = |value| / mid − 1，
#: mid 由本卡的**真实布局系数**决定（3 正 1 负 = 0.9375 / 0.75），见 _dev_text。

#: 负词条市场友好度（0~100，越大越不伤价；「推荐负面-伤害计算」排序也用它）
NEG_MARKET = {
    "Zoom": 100, "Ammo Maximum": 100, "Weapon Recoil": 55,
    "Damage to Grineer": 92, "Damage vs Grineer": 92,
    "Damage to Corpus": 92, "Damage vs Corpus": 92,
    "Damage to Infested": 92, "Damage vs Infested": 92,
    "Projectile Speed": 70, "Status Duration": 66,
    "Magazine Capacity": 60, "Combo Duration": 60, "Initial Combo": 60,
    "Fire Rate / Attack Speed": 30, "Fire Rate": 30, "Attack Speed": 30,
    "Reload Speed": 30, "Punch Through": 40, "Impact Damage": 26,
    "Puncture Damage": 26, "Slash Damage": 26, "Status Chance": 20,
    "Critical Chance": 8, "Critical Damage": 8, "Damage": 8, "Multishot": 8,
}

#: 词条市场权重（0~100，「市场 X」分类评分与市场推荐排序用；派系卡溢价高）
STAT_MARKET = {
    "Critical Chance": 88, "Critical Damage": 90, "Damage": 86, "Multishot": 92,
    "Status Chance": 55,
    "Cold Damage": 40, "Electricity Damage": 42, "Heat Damage": 45, "Toxin Damage": 44,
    "Fire Rate / Attack Speed": 48, "Fire Rate": 48, "Attack Speed": 48,
    "Reload Speed": 30, "Magazine Capacity": 24, "Punch Through": 26,
    "Projectile Speed": 18, "Status Duration": 20, "Combo Duration": 26,
    "Viral Damage": 95, "Corrosive Damage": 96, "Radiation Damage": 50,
    "Magnetic Damage": 45, "Blast Damage": 40, "Gas Damage": 35,
    "Range": 60, "Initial Combo": 40, "Heavy Attack Efficiency": 40,
    "Heavy Attack Damage": 40, "Slide Attack Critical Chance": 16,
    "Slide Attack Critical Damage": 16, "Impact Damage": 10, "Puncture Damage": 10,
    "Slash Damage": 10, "Zoom": 6, "Ammo Maximum": 8, "Weapon Recoil": 6,
    "Finisher Damage": 8, "Damage to Grineer": 96, "Damage vs Grineer": 96,
    "Damage to Corpus": 96, "Damage vs Corpus": 96,
    "Damage to Infested": 96, "Damage vs Infested": 96,
}

#: 负词条 DPS 影响度（「推荐负面/致命负」伤害维度；0 = 对 DPS 无影响）
NEG_DPS_IMPACT = {
    "Zoom": 0.0, "Ammo Maximum": 0.0, "Weapon Recoil": 0.0,
    "Damage to Grineer": 0.0, "Damage vs Grineer": 0.0,
    "Damage to Corpus": 0.0, "Damage vs Corpus": 0.0,
    "Damage to Infested": 0.0, "Damage vs Infested": 0.0,
    "Status Duration": 0.0, "Combo Duration": 0.0, "Initial Combo": 0.0,
    "Projectile Speed": 0.5, "Magazine Capacity": 1.5, "Punch Through": 1.5,
    "Reload Speed": 3.0, "Fire Rate": 5.0, "Attack Speed": 5.0,
    "Fire Rate / Attack Speed": 5.0, "Impact Damage": 6.0, "Puncture Damage": 6.0,
    "Slash Damage": 6.0, "Status Chance": 9.0, "Critical Chance": 14.0,
    "Critical Damage": 14.0, "Damage": 16.0, "Multishot": 18.0,
}

#: 靶子口径（照抄参考卡脚注，不改动数字）
_TARGET_TTK = (1.4, 4.5, 14.3)     # 三硬度节点（秒）
_ARMOR = 2700                       # 护甲参与减伤
_HEADSHOT_RATIO = 0.5               # 弱点命中占比（50% @2x → 平均 ×1.5）
_ELEMENT_PCT = 1.20                 # 近战元素桶：两个 60/60 合并 = +120% 基伤

# ---------------------------------------------------------------------------
# 终局标准配卡桶（2026-11-13 第30轮根因修复）
# ---------------------------------------------------------------------------
# 旧实现是**单一常数**：基伤 `1.65 if 主武器 else 2.20`、元素恒 `1.20`、多重
# `1.00 + (1.10 if 副武器 else 0.60)`、射速恒 `0.30`。真卡实测（冰凇 Verglas，
# Rabbit 截图 8 Mod：紫卡/低温弹头Prime/地狱火/重口径/分裂膛室/卑劣加速/膛线/私法军备）
# 暴露它把「紫卡增益」系统性高估：同一张卡、同一套 `_dps` 公式，旧桶给 **+120.1%**，
# 换成真实配卡桶给 **+66.9%**，而**独立手算**（基伤×元素×多重×暴击×射速）也是 **+66.9%**
# —— 引擎没错，错的是桶。差 53pp 的根因是基伤桶只有一张模组（膛线 165%）：
# 真实终局必带重口径（再 +165%），桶越弱，紫卡那条 +179.8% 基伤就越显得值钱。
#
# 每个数都能在 `data/warframe_items/Mods.json` 的 canonical 条目（满级）里查到，
# `tests/test_qq_riven_std_build.py` 逐项回查源码常数，禁止手改不查表。
# 结构：紫卡类型 → (基伤和, 元素和, 多重和, 射速和, 暴击几率, 暴击伤害)
# 负作用照实折进基伤（卑劣加速/贫血敏捷各 −15%），不带下划线的负面（−精准）不进 DPS。
_STD_BUILD_GUN = {
    # 膛线 165 + 重口径 165 − 卑劣加速 15 ；低温弹头Prime 165 + 地狱火 90 ；
    # 分裂膛室 90 + 私法军备 60 ；卑劣加速 90（7 张真卡 + 紫卡 = 8 槽）
    "Rifle": (3.15, 2.55, 1.50, 0.90, 0.00, 0.00),
    # 黄蜂蜇刺 220 + Augur Pact 90 − 贫血敏捷 15 ；深冻 90 + 烈焰 90 ；
    # 弹头扩散 120 + 致命洪流 60 ；贫血敏捷 90（7 张）
    "Pistol": (2.95, 1.80, 1.80, 0.90, 0.00, 0.00),
    # 抵近射击 90 + 燃烧弹 60 ；冰霜掌握 90 + 燃烧外套 90 ；地狱弹幕 120 ；
    # 霰弹弹幕 90（6 张，见下方 _STD_SLOT_NOTE）
    "Shotgun": (1.50, 1.80, 1.20, 0.90, 0.00, 0.00),
    # 赤晶枪管 100 + 破坏弹 60 ；极地弹匣 120 + 燃烧弹 120 ；Primed Dual Rounds 110 ；
    # 自动扳机 60（6 张）
    "Archgun": (1.60, 2.40, 1.10, 0.60, 0.00, 0.00),
}
#: 暴击武器桶：**暴击双卡占用「射速 + 第二张多重」两个位置**，总数仍是 7 张。
#: 依据（2026-11-13 第30轮，槽位预算硬约束）：Warframe 只有 8 个 Mod 槽，紫卡占 1 ⇒
#: 真卡最多 **7 张**。旧实现把暴击双卡**追加**在 7 张之上（Rifle 9 / Pistol 9 /
#: Shotgun 8 / Archgun 8 张 + 紫卡 = 10/10/9/9 槽），物理上不可能装备，且会虚高基数。
#: 非暴击桶（上表）已由 Robot 冰凇真实 8 槽配卡逐张验证：紫卡 + 膛线/重口径/卑劣加速/
#: 低温弹头Prime/地狱火/分裂膛室/私法军备 = 正好 7 张，与 `_STD_BUILD_GUN["Rifle"]` 等价。
#: 暴击桶＝把「卑劣加速」和「私法军备（第二张多重）」换成「死眼 + 敏感」类暴击双卡。
_STD_BUILD_GUN_CRIT = {
    "Rifle": (3.30, 2.55, 0.90, 0.00, 1.50, 1.20),    # 膛线+重口径 / 分裂膛室 / 死眼+敏感
    "Pistol": (3.10, 1.80, 1.20, 0.00, 1.20, 0.60),   # 黄蜂+Augur / 弹头扩散 / 手枪精通+目标标定
    "Shotgun": (1.50, 1.80, 1.20, 0.00, 0.90, 0.60),  # 抵近+燃烧弹 / 地狱弹幕 / 团块+破坏
    "Archgun": (1.60, 2.40, 1.10, 0.00, 1.00, 0.80),  # 赤晶+破坏弹 / Primed Dual Rounds / 视差+空心
}
#: 槽位预算说明（Shotgun/Archgun 非暴击为 6 张）：两者真表里没有第二张多重/射速以外的
#: 常用填充卡，宁可**少装一张**（基数偏低 ⇒ 紫卡增益偏保守）也不凭空编一张不存在的卡。
_STD_SLOT_NOTE = "Rifle/Pistol 非暴击 7 张；暴击 7 张；Shotgun/Archgun 非暴击 6 张、暴击 7 张"
#: 紫卡类型 → 配卡桶键。Kitgun 是真·副手（自制手枪），Zaw 归近战（走 _melee_std_bucket）。
#: 认不出的类型回落到面板 `_derive_cat` + `w["type"]`，**不做武器特例**。
_STD_CAT_BY_RIVEN = {"Rifle": "Rifle", "Pistol": "Pistol", "Kitgun": "Pistol",
                     "Shotgun": "Shotgun", "Archgun": "Archgun"}
#: 暴击模组只在「真暴击武器」上装（cc < 15% 的武器装暴击模组是假的，会稀释紫卡暴击词条）
_STD_CRIT_CC_MIN = 0.15

# ---------------------------------------------------------------------------
# 数据懒加载
# ---------------------------------------------------------------------------
_RAW: dict = {"weapons": None, "disp": None, "base": None, "zh": None}

# ---------------------------------------------------------------------------
# 市场数据（2026-11-04 22:25 重写，代码位置：本文件「市场」段）
# ---------------------------------------------------------------------------
class RivenMarketError(RuntimeError):
    """WFM 拍卖数据获取/校验失败。

    用户纪律：禁止降级、禁止兜底。失败必须显式上抛，绝不用静态权重表、
    热度分或「暂无」冒充真实市场数据。调用方负责把**错误原因**显示在卡面上。
    """

#: ⚠️ WFM /v1/auctions/search 必须用 API 型 UA。实测 2026-11-04 22:2x（生产容器
#: qq-bridge 内直连验证）：带 Chrome-like UA 会返回 200 但 ``auctions: []``（29 字节），
#: 带 curl UA + ``Platform: pc`` 返回完整 100 条（108247 字节）。旧代码走
#: ``warframe._market_get``（MARKET_UA=Chrome）＋ 全局 ``/v1/auctions``（100 条全武器），
#: 按武器过滤后经常 <3 条 → 市场行永远「暂无」。这里是该问题的根因修复。
_WFM_AUCTION_HEADERS = {
    "User-Agent": "curl/8.4.0",
    "accept": "application/json",
    "Platform": "pc",
    "Language": "en",
}
_WFM_PAGE_TTL = 600.0
_WFM_MIN_SAMPLES = 3
_AUCTION_CACHE: dict = {}   # (slug, sort_by) -> (ts, auctions)

#: WFM attributes 的 url_name → 中文短名（卡面用；未收录时显示原 slug，绝不编造译名）。
_WFM_ATTR_SHORT = {
    "ammo_maximum": "弹药", "base_damage_/_melee_damage": "基伤",
    "blast": "爆炸", "cold_damage": "冰伤", "corrosive": "腐蚀",
    "critical_chance": "暴率", "critical_damage": "暴伤",
    "damage_to_orokin": "对奥罗金", "damage_vs_corpus": "对C佬",
    "damage_vs_grineer": "对G佬", "damage_vs_infested": "对I佬",
    "electric_damage": "电伤", "fire_rate_/_attack_speed": "射速",
    "gas": "毒气", "heat_damage": "火伤", "impact_damage": "冲击",
    "magazine_capacity": "弹匣", "multishot": "多重",
    "projectile_speed": "弹速", "punch_through": "穿透",
    "recoil": "后坐", "reload_speed": "装填", "slash_damage": "切割",
    "status_chance": "触发", "status_damage": "异常伤",
    "status_duration": "异常持续", "toxin_damage": "毒伤", "viral": "病毒",
    "weak_point_critical_chance": "弱点暴率", "weak_point_damage": "弱点伤",
    "zoom": "变焦",
    # 2026-11-13 第31轮：随 17 条新映射同步补中文短名（否则卡面显示裸 slug）。
    # 只补**已实测存在**的 slug；`_short` 查不到仍回落原 slug，不编译名。
    "ammo_efficiency": "弹药效率", "magnetic": "磁力", "radiation": "辐射",
    "range": "范围", "finisher_damage": "处决伤", "slam_attack_damage": "震地伤",
    "melee_damage_on_heavy_attack": "重击伤", "heavy_attack_wind_up_speed": "重击蓄力",
    "combo_duration": "连击持续",
}

#: DE 官方词条名 → WFM ``attributes.url_name``（2026-11-04 23:0x 新增）。
#: WFM 的 ``positive_stats`` / ``negative_stats`` 只认 url_name，不认 DE 名。
#: 只列已核对过的映射；**映射不到的词条显式标注「无法精确匹配」**，绝不猜 slug。
_DE_TO_WFM = {
    "Ammo Maximum": "ammo_maximum",
    "Base Damage": "base_damage_/_melee_damage",
    "Melee Damage": "base_damage_/_melee_damage",
    "Cold Damage": "cold_damage",
    "Corrosive Damage": "corrosive",
    "Critical Chance": "critical_chance",
    "Critical Damage": "critical_damage",
    # 滑砍暴击：WFM url_name 实地核对（2026-11-12 dump 真实拍卖 attributes 得到）
    "Critical Chance for Slide Attack": "critical_chance_on_slide_attack",
    # 重击效率：近战 3.0 把「引导效率(Channeling)」改名而来，WFM 仍沿用旧 slug
    "Heavy Attack Efficiency": "channeling_efficiency",
    # 🔴 2026-11-13 第31轮：以 WFM 为判定器**逐条实测**（容器内真网络）后补齐 17 条。
    # 判据：`GET /v1/auctions/search?...&positive_stats=<slug>` —— 返回 400
    # ``{"positive_stats": ["app.form.invalid"]}`` = slug 不存在；返回 200 = slug 存在
    # （n=0 只表示该武器没人挂该词条，不代表参数非法）。**不靠译名猜。**
    # 同时**更正一条长期错误注释**：旧注释称「Ammo Efficiency 在 WFM attributes 里不存在」，
    # 实测 ``ammo_efficiency`` 返回 **200 / n=2**（rubico），确实存在，故补映射。
    "Damage": "base_damage_/_melee_damage",
    "Ammo Efficiency": "ammo_efficiency",
    "Blast Damage": "blast",
    "Gas Damage": "gas",
    "Magnetic Damage": "magnetic",
    "Radiation Damage": "radiation",
    "Damage to Orokin": "damage_to_orokin",
    "Status Damage": "status_damage",
    "Status Duration": "status_duration",
    "Weak Point Critical Chance": "weak_point_critical_chance",
    "Fire Rate / Attack Speed": "fire_rate_/_attack_speed",
    "Range": "range",
    "Finisher Damage": "finisher_damage",
    "Slam Attack Damage": "slam_attack_damage",
    "Melee Damage on Heavy Attack": "melee_damage_on_heavy_attack",
    "Heavy Attack Wind Up Speed": "heavy_attack_wind_up_speed",
    "Combo Duration": "combo_duration",
    # ⚠️ 以下 6 条实测返回 400 app.form.invalid（WFM 确无此 slug），**保持不映射**：
    # Initial Combo / Additional Combo Count Chance / Chance to Gain Combo Count /
    # Magazine Reloaded/s when Holstered / Damage to Scaldra / Damage to Techrot。
    # Puncture Damage 因 429 未取到判定，同样不映射（宁可 exact 报 unknown，也不猜）。
    "Damage to Corpus": "damage_vs_corpus",
    "Damage to Grineer": "damage_vs_grineer",
    "Damage to Infested": "damage_vs_infested",
    "Electricity Damage": "electric_damage",
    "Fire Rate": "fire_rate_/_attack_speed",
    "Attack Speed": "fire_rate_/_attack_speed",
    "Heat Damage": "heat_damage",
    "Impact Damage": "impact_damage",
    "Magazine Capacity": "magazine_capacity",
    "Multishot": "multishot",
    "Projectile Speed": "projectile_speed",
    "Punch Through": "punch_through",
    "Weapon Recoil": "recoil",
    "Reload Speed": "reload_speed",
    "Slash Damage": "slash_damage",
    "Status Chance": "status_chance",
    "Toxin Damage": "toxin_damage",
    "Viral Damage": "viral",
    "Weak Point Damage": "weak_point_damage",
    "Zoom": "zoom",
}

#: 同实现节流：4 个请求/卡，避免把 WFM 打到 429（不是降级，不改变口径）。
_WFM_MIN_INTERVAL = 0.35
_WFM_LAST_REQ = [0.0]


def _lift_attack_panel(w: dict) -> dict:
    """顶层面板不可用时，用 ``attacks[0]`` 回填一份等价面板（原地改并返回同一对象）。

    🔴 2026-11-13 第31轮根因（**读错键，不是缺数据**）：WFCD 把「单件」武器记成
    ``type="<X> Component"``（如 ``Zaw Component``），这类记录的**顶层字段是占位值**——
    ``damage=null``、``criticalChance/criticalMultiplier/procChance/fireRate`` 全为
    **0**，真数值只存在于 ``attacks[0]``：

        attacks[0] = {name: "Dokrahm Strike",
                      damage: {impact: 46.4, slash: 154.5, puncture: 108.1},
                      crit_chance: .., crit_mult: .., status_chance: .., speed: ..}

    旧实现只读顶层 ⇒ Zaw 11 把 ``_dps`` 恒 0 ⇒ ``panel=False`` ⇒ 伤害分给不出。
    这与「``statusChance`` 实际叫 ``procChance``」是同一类 bug：**按武器名打补丁会
    漏掉下一个模块化武器**，所以这里按**字段形状**判定，不含任何武器名特例。

    触发条件刻意收窄到**顶层 ``damage.total`` 缺失/≤0 且有 ``attacks[0].damage``**：
    有正常面板的 606 把武器顶层 total 均 > 0，构造上不可能被本条改动
    （实测 dps_base / dmg / mkt / total 逐位不变）。

    只在字段为空/0 时回填，不用 ``attacks[0]`` 覆盖已有真值；``speed`` 映射到
    ``fireRate``、``status_chance`` 映射到 ``procChance``（键名不同，语义相同）。
    """
    dmg = w.get("damage") or {}
    if float(dmg.get("total") or 0) > 0:
        return w
    atk = w.get("attacks") or []
    a0 = atk[0] if atk and isinstance(atk[0], dict) else None
    ad = (a0 or {}).get("damage") or {}
    parts = {k: float(v or 0) for k, v in ad.items()
             if isinstance(v, (int, float)) and not isinstance(v, bool)}
    total = sum(parts.values())
    if total <= 0:
        return w
    # 保留原 damage 里的零值键（元素键用于 `elem_innate = total - phys_total`），
    # 缺的键补 0，避免 `_dps` 的 `dmg.get(k) or 0` 之外的读取路径 KeyError。
    new_dmg = dict(dmg)
    new_dmg.update(parts)
    for k in ("impact", "puncture", "slash", "heat", "cold", "electricity", "toxin",
              "blast", "radiation", "gas", "magnetic", "viral", "corrosive", "void",
              "tau", "true"):
        new_dmg.setdefault(k, 0.0)
    new_dmg["total"] = total
    w["damage"] = new_dmg
    for src_k, dst_k in (("crit_chance", "criticalChance"),
                         ("crit_mult", "criticalMultiplier"),
                         ("status_chance", "procChance"),
                         ("speed", "fireRate")):
        v = a0.get(src_k)
        if v is None:
            continue
        try:
            v = float(v)
        except (TypeError, ValueError):
            continue
        if not float(w.get(dst_k) or 0) > 0 and v > 0:
            w[dst_k] = v
    w["_panel_from_attacks"] = True
    return w


#: **确证无固定伤害面板**的武器类型（模块化，膛室/握把/装填器自由组合，DE 不发布组装后面板）。
#: 上游 WFCD `Misc.json` 对这 6 个膛室只给占位零值（`damage.total=0`、`cc=cm=proc=fr=0`、
#: `attacks[].damage={}`，唯 `omegaAttenuation=1.2`/`disposition=4` 为真），故**不是缺数据**，
#: 展示层应写「模块化武器，需按你的组装实测」，不能写「面板缺失」。
#: 🔴 Zaw **已于 2026-11-13 第31轮移出本集合**：其 `attacks[0]` 有官方 strike 基础伤害
#: （Balla 224 / Dokrahm 309 …），靠 `_lift_attack_panel` 回填后 11/11 可算伤害分。
_MODULAR_NO_PANEL = frozenset({"Kitgun"})


def _raw_has_panel(it: dict) -> bool:
    """记录是否带**可用伤害面板**：顶层 ``damage.total`` 或 ``attacks[0].damage`` 之和。

    🔴 2026-11-13 第31轮根因（与 `_lift_attack_panel` 同一族的第二个 bug）：同名去重
    原先只用 ``bool((it.get("damage") or {}).get("total"))`` 判优劣，**看不见
    ``attacks[0]``**。Zaw 的「单件」记录正好是顶层 ``damage=null`` + 真值在 ``attacks[0]``，
    于是 `Melee.json` 里的 22 键占位记录因为「先遇到」被留下，`ExtraWeapons.json` 里
    带 attacks 的完整记录被当成「不比它好」而丢弃 —— **6 把 Zaw 无面板的真因**，
    不是缺数据。修好后 11/11 Zaw 全部有面板。
    """
    if float(((it.get("damage") or {}).get("total")) or 0) > 0:
        return True
    atk = it.get("attacks") or []
    a0 = atk[0] if atk and isinstance(atk[0], dict) else {}
    dmg = a0.get("damage") or {}
    return any(float(v or 0) > 0 for v in dmg.values()
               if isinstance(v, (int, float)) and not isinstance(v, bool))


def _load_raw_weapons() -> dict:
    """懒加载武器原始记录（含 damage 伤害类型分解，DPS 模型用）。

    ``SentinelWeapons.json`` 必须一起并入：DE 的 ``LotusRifleRandomModRare``
    ``compatibleItems`` 里确实收录了哨兵步枪（Verglas / 冰凇 就是典型案例，
    官方中文名 = 冰凇、type = Rifle），只读三件套会让这类卡整块 DPS 面板
    变成「武器面板数据缺失」（实测 2026-11-02）。

    ``Arch-Gun.json``（2026-11-13 第31轮补齐，WFCD/warframe-items 同源同 schema）：
    空战枪 20 把与 ``riven_disp.json`` 的 Archgun **20/20 逐名对齐**，此前无此文件
    ⇒ 这 20 把紫卡全落「武器面板数据缺失」、伤害分给不出，属真实缺数据而非算法限制。

    🔴 **Zaw 11 把（2026-11-13 第31轮第三修）**：本以为是缺数据，**实为两个读键 bug**——
    ① 顶层 ``damage=null`` / ``cc=cm=proc=fr=0`` 是**占位值**，真值在 ``attacks[0]``
    （见 `_lift_attack_panel`）；② 同名去重只认顶层 ``damage.total``，把 ``Melee.json``
    的 22 键占位记录判成「更好」，丢弃了 ``ExtraWeapons.json`` 里带 attacks 的完整记录
    （见 `_raw_has_panel`）。两处修好后 Zaw **11/11 有面板**。

    ``ExtraWeapons.json``（2026-11-13 第31轮新增，增量补充包）：本地四件套停在
    2026-09-16，缺 31 把较新武器（War Prime / Venato Prime / Perigale Prime 等）。
    从 WFCD 同源下载后**只追加缺失条目**，不覆盖任何现有文件（本地条目字段更全，
    直接覆盖会丢 ``patchlogs``）。后又补 2 条 Zaw strike（Plague Keewar / Plague Kripath，
    本地 ``Melee.json`` 完全无 attacks）。实测补齐 + 上两项修键后 **623 把里 617 有面板**。

    🔴 **仅剩 6 把 Kitgun 无面板，且已硬证「非缺数据、非读错键」**：上游 ``Misc.json``
    确有 6 条 ``type="Kitgun Component"`` 记录（Catchmoon/Gaze/Rattleguts/Tombfinger/
    Sporelacer/Vermisplicer），但**全部是占位零值**——``damage.total=0``、
    ``cc=cm=proc=fr=0``、``attacks[].damage={}``，唯 ``omegaAttenuation=1.2`` /
    ``disposition=4`` 是真值。即 DE 不对外发布「组装后」的膛室面板（膛室+握把+装填器
    自由组合，无唯一解）。展示层必须写「模块化武器，需按你的组装实测」，
    **不许写「面板缺失」**（那是把数据源的限制说成自家 bug）。
    """
    if _RAW["weapons"] is not None:
        return _RAW["weapons"]
    out: dict[str, dict] = {}
    for fname in ("Primary.json", "Secondary.json", "Melee.json",
                  "SentinelWeapons.json", "Arch-Gun.json", "ExtraWeapons.json"):
        try:
            items = json.loads((_DATA_DIR / fname).read_text(encoding="utf-8"))
        except Exception as e:  # noqa: BLE001
            logger.warning(f"riven_card_raw_load_failed {fname}: {type(e).__name__}: {e}")
            continue
        # `ExtraWeapons.json` 是**增量补充包**（`{"items": [...], "__note__": ...}`），
        # 只装本地四件套未收录的新武器面板，删除即可回退；其余文件是裸列表。
        if isinstance(items, dict):
            items = items.get("items") or []
        for it in items:
            en = (it.get("name") or "").strip()
            if not en:
                continue
            key = en.lower()
            prev = out.get(key)
            # 同名重复（如 Zaw 部件 vs 成品、Prisma 变体）：优先保留带伤害面板的成品记录，
            # 避免部件/占位条目（顶层 total=None）覆盖成品导致 DPS 面板缺失。
            # `_raw_has_panel` 同时认 `attacks[0].damage`（Zaw Component 的真值所在）。
            has_dmg = _raw_has_panel(it)
            prev_dmg = bool(prev) and _raw_has_panel(prev)
            if prev is None or (has_dmg and not prev_dmg):
                out[key] = it
    # 顶层占位面板 → 用 `attacks[0]` 回填（Zaw 等「单件」模块化武器，见函数 docstring）。
    # 放在去重之后：先保证拿到的是带 `attacks` 的那条完整记录，再回填。
    for it in out.values():
        _lift_attack_panel(it)
    _RAW["weapons"] = out
    return out


def _load_riven_tables() -> tuple[dict, dict, dict]:
    """懒加载倾向表 / 词条基础值表 / 词条中文名（与 riven.py 同源文件）。"""
    if _RAW["disp"] is None:
        for key, fn in (("disp", "riven_disp.json"), ("base", "riven_stat_base.json"),
                        ("zh", "riven_stat_zh.json")):
            try:
                _RAW[key] = json.loads((_DATA_DIR / fn).read_text(encoding="utf-8"))
            except Exception as e:  # noqa: BLE001
                logger.warning(f"riven_card_tbl_load_failed {fn}: {type(e).__name__}: {e}")
                _RAW[key] = {}
    return _RAW["disp"] or {}, _RAW["base"] or {}, _RAW["zh"] or {}


def _stat_zh(en: str) -> str:
    _, _, zh = _load_riven_tables()
    return (zh.get(en) or {}).get("zh") or en


def _fmt_val(s: dict) -> str:
    """词条数值显示：% 一位小数（参考卡口径 +148.0%）、x 两位小数、其余原样。"""
    if s["unit"] == "%":
        return f"{s['value']:.1f}%"
    if s["unit"] == "x":
        return f"{s['value']:.2f}"
    return f"{s['value']:g}"


def _neg_zh(en: str) -> str:
    """负词条显示名（推荐负面行，用词对齐参考卡：变焦/弹药最大值/武器后坐力）。"""
    override = {
        "Zoom": "变焦", "Ammo Maximum": "弹药最大值", "Weapon Recoil": "武器后坐力",
        "Projectile Speed": "弹道速度", "Status Duration": "异常持续时间",
        "Magazine Capacity": "弹匣容量", "Reload Speed": "装填速度",
        "Fire Rate": "射速", "Attack Speed": "攻速",
        "Fire Rate / Attack Speed": "射速", "Slash Damage": "切割",
        "Impact Damage": "冲击", "Puncture Damage": "穿刺",
        "Critical Chance": "暴击几率", "Critical Damage": "暴击伤害",
        "Damage": "基础伤害", "Multishot": "多重射击", "Status Chance": "触发几率",
    }
    return override.get(en) or _stat_zh(en)


def _stat_icon_color(en: str) -> str:
    """词条左侧彩色圆形图标配色（对齐 Warframe 词条图标色系，参考卡每条一个色圆）。

    无现成图标库时，用「按词条类别取色」的圆形图标还原参考卡观感
    （暴击=青、病毒=紫、多重=橙、物理切割=红、射速=黄、触发=绿、弹匣=蓝…）。
    取不到类别时回落中性灰，绝不抛异常。
    """
    s = en.lower()
    if "critical damage" in s:
        return "#27C4D9"          # 暴击伤害：青
    if "critical chance" in s:
        return "#3FD0E0"          # 暴击几率：青亮
    if "multishot" in s:
        return "#E8852B"          # 多重射击：橙
    if "viral" in s:
        return "#9B59B6"          # 病毒：紫
    if "corrosive" in s:
        return "#B7D33B"          # 腐蚀：黄绿
    if "radiation" in s:
        return "#E84393"          # 辐射：粉
    if "magnetic" in s:
        return "#3498DB"          # 磁力：蓝
    if "heat" in s or "fire" in s:
        return "#E74C3C"          # 火/热：红橙
    if "cold" in s or "ice" in s:
        return "#5DADE2"          # 冰：浅蓝
    if "electric" in s or "toxin" in s:
        return "#9B59B6"          # 电/毒：紫
    if "blast" in s:
        return "#E67E22"          # 爆炸：橙
    if "gas" in s:
        return "#58D68D"          # 毒气：绿
    if "slash" in s:
        return "#E74C3C"          # 切割：红
    if "impact" in s:
        return "#C0392B"          # 冲击：暗红
    if "puncture" in s:
        return "#A93226"          # 穿刺：暗红
    if "damage" in s:
        return "#D64545"          # 基础伤害：红
    if "fire rate" in s or "attack speed" in s:
        return "#F1C40F"          # 射速/攻速：黄
    if "status chance" in s or "status" in s:
        return "#2ECC71"          # 触发：绿
    if "reload" in s or "magazine" in s or "ammo" in s:
        return "#3498DB"          # 装填/弹匣/弹药：蓝
    if "zoom" in s or "recoil" in s or "projectile" in s:
        return "#95A5A6"          # 变焦/后坐/弹速：灰
    if "grineer" in s:
        return "#C0392B"          # g系：红
    if "corpus" in s:
        return "#2980B9"          # c系：蓝
    if "infested" in s:
        return "#8E44AD"          # i系：紫
    if "combo" in s:
        return "#16A085"          # 连击：青绿
    if "range" in s:
        return "#E67E22"          # 范围：橙
    if "punch" in s:
        return "#7F8C8D"          # 穿透：灰
    if "duration" in s:
        return "#BDC3C7"          # 持续：浅灰
    return "#9A9898"              # 兜底中性灰


def _short_stat(en: str) -> str:
    """词条短名（市场价差/推荐行用，对齐参考卡：暴击伤害→暴伤、多重射击→多重、触发→异常）。"""
    m = {
        "Critical Damage": "暴伤", "Multishot": "多重", "Damage": "基伤",
        "Critical Chance": "暴率", "Status Chance": "异常",
        "Electricity Damage": "电伤", "Heat Damage": "火伤", "Cold Damage": "冷伤",
        "Toxin Damage": "毒伤", "Fire Rate": "射速", "Attack Speed": "攻速",
        "Fire Rate / Attack Speed": "射速", "Reload Speed": "装填",
        "Slash Damage": "切割", "Impact Damage": "冲击", "Puncture Damage": "穿刺",
        "Weapon Recoil": "后坐", "Zoom": "变焦", "Ammo Maximum": "弹药",
        "Magazine Capacity": "弹匣", "Projectile Speed": "弹速",
        "Status Duration": "异常持续", "Range": "范围", "Punch Through": "穿透",
        "Viral Damage": "病毒", "Corrosive Damage": "腐蚀", "Radiation Damage": "辐射",
        "Magnetic Damage": "磁力", "Blast Damage": "爆炸", "Gas Damage": "毒气",
        "Damage to Grineer": "g系伤", "Damage vs Grineer": "g系伤",
        "Damage to Corpus": "c系伤", "Damage vs Corpus": "c系伤",
        "Damage to Infested": "i系伤", "Damage vs Infested": "i系伤",
    }
    if en in m:
        return m[en]
    z = _stat_zh(en)
    return z.replace("伤害", "伤") if z.endswith("伤害") else z


# ---------------------------------------------------------------------------
# 词条理论范围 / 偏差 / 百分位（公式见模块 docstring，全部经参考卡核验）
# ---------------------------------------------------------------------------
#: 布局负面位属性集合（"效果方向为负"——越低越好，卡面显示 + 号也意味着负面效果）。
#: 2026-11-12 第26轮对齐 Warframe Rabbit 反推（koishi_ref/riven-data.md:69、
#: riven-ocr.md:155）：Rabbit 判「这张卡是 2正1负」时，把「+58.1% 武器后坐力」
#: 计为 1 个负面位（后坐力越低越好，+58.1% = 后坐力变差），于是选 2正1负的
#: buff=1.2375 / curse=-0.495，逐位复现参考卡 60.1/66.8/73.5、293.5/326.1/358.7、
#: 48.1/53.5/58.8。我方旧实现只认 OCR 卡面 +/- 号，三条全 + → 误判为 3正(0.75)，
#: 得到 40.5/197.6/81.0，与参考卡差远——第26轮真群截图根因。
#:
#: 事实源（通用、非按武器硬编码）：riven_stat_base.json 中 baseValue<0 的属性即
#: "效果方向为负"候选；其中 Ammo Efficiency 带官方 reverseValueSymbol 保护
#: （riven-data.md:69「弹药效率使用官方 reverseValueSymbol，不会把原始负号误认成
#: 负面属性」），须从负面位排除。展开后当前仅 Weapon Recoil 落入此集合。
#: 该常量由守门测试 E2 从 base 表实时派生校验，防止 base 表新增负向属性时漏同步。
_REVERSE_VALUE_SYMBOL_STATS = frozenset({"Ammo Efficiency"})
_LAYOUT_CURSE_STATS: frozenset[str] = frozenset({"Weapon Recoil"})


def _layout_curse(s: dict) -> bool:
    """该词条在**布局计数**里是否算负面位。

    与卡面显示符号解耦：卡面真负号（s['sign']=='-'，OCR 读到的诅咒）算负面位；
    此外"效果方向为负"的属性（如 Weapon Recoil，卡面 +58.1% 但越低越好）也
    算负面位——这正是 Rabbit/koishi 2正1负布局判定、我方第26轮漏掉的一环。
    **仅用于布局/系数选择**；卡面显示、区间正负、偏差方向仍走 s['sign']。
    """
    return s.get("sign") == "-" or s.get("en") in _LAYOUT_CURSE_STATS


def _fix_of(parsed: dict) -> dict:
    pos = [s for s in parsed["stats"] if not _layout_curse(s)]
    neg = [s for s in parsed["stats"] if _layout_curse(s)]
    sus = len(pos) >= 4 and not neg
    key = str(min(len(pos), 3)) + ("1" if (neg or sus) else "")
    return FIX.get(key, FIX["3"])


def _eff_rank(parsed: dict) -> int:
    """紫卡等级（0~8）：在 OCR 读数基础上**只升不降**地校正。

    为什么需要校正（2026-11-12 真截图实测）：``verify_real`` 跑用户参考图时 OCR 把
    等级读成 1（Rabbit 自己也显示「Lv.未知」），而 ``_mid`` 的等级系数 (rank+1)/9
    让区间缩到 2/9 → 所有词条值都「超上限」→ 市场分顶 100、偏差全错。实际卡是满级 8
    （Damage 355.3 恰落在 rank8 区间 293.5~358.6 内）。

    判据只认**物理不可能**：某条词条值超出「该等级理论上限 × 1.1 × 1.1」时，等级必然
    被读低了，取能让所有词条都装得下的最小等级。**绝不因数值偏低而降级**——低 roll 的
    满级卡（如阿利乌低 roll：Damage 50% vs 满级区间 293.5~358.6）本来就该判低分，
    若按「与区间最吻合」反推会被误判成 rank1 中 roll，把 Rabbit 的低 roll 10.1 抬成 50。
    """
    cached = parsed.get("_rank_eff")
    if isinstance(cached, int):
        return cached
    _, base_tbl, _ = _load_riven_tables()
    t = base_tbl.get(parsed.get("type")) or {}
    fix = _fix_of(parsed)
    omega = float(parsed.get("disp") or 1.0)
    rk = parsed.get("rank")
    rk = 8 if not isinstance(rk, int) else max(0, min(8, rk))
    need = rk
    for s in parsed.get("stats") or []:
        base = t.get(s["en"])
        if base is None and " vs " in s["en"]:
            base = t.get(s["en"].replace(" vs ", " to "))
        if base is None and " to " in s["en"]:
            base = t.get(s["en"].replace(" to ", " vs "))
        if not base:
            continue
        f = abs(fix["curse"]) if _layout_curse(s) else fix["buff"]
        full = abs(base) * omega * f              # 满级(rank8)理论中值
        v = (float(s["value"]) - 1.0) if s["unit"] == "x" else abs(float(s["value"]))
        if full <= 0 or v <= 0:
            continue
        # v ≤ full×(r+1)/9×1.1 ⟺ r ≥ 9v/(full×1.1) − 1；取能装下的最小等级
        r = max(0, min(8, math.ceil(9.0 * v / (full * 1.1) - 1.0)))
        need = max(need, r)
    if need != rk:
        logger.info(f"wf_riven_rank_fit: {parsed.get('weapon')!r} ocr_rank={rk} -> {need} "
                    f"(词条值超出 rank{rk} 理论上限，物理不可能)")
    parsed["_rank_eff"] = need
    return need


def _mid(s: dict, parsed: dict, fix: dict) -> float | None:
    """词条中值 = |base| × omega × fix（正词条 buff，负词条 |curse|）。
    base 表对某些词条（如 Weapon Recoil）记为负值，取绝对值统一按"惩罚/加成量"处理。"""
    _, base_tbl, _ = _load_riven_tables()
    t = base_tbl.get(parsed["type"]) or {}
    base = t.get(s["en"])
    if base is None and " vs " in s["en"]:
        base = t.get(s["en"].replace(" vs ", " to "))
    if base is None and " to " in s["en"]:
        base = t.get(s["en"].replace(" to ", " vs "))
    if not base:
        return None
    omega = float(parsed.get("disp") or 1.0)
    # 第26轮对齐 Rabbit：布局负面位（含"效果方向为负但卡面+"的 Weapon Recoil）
    # 用 |curse|，其余用 buff。区间/均值仍按加成量取正显示，与参考卡一致。
    f = abs(fix["curse"]) if _layout_curse(s) else fix["buff"]
    # 等级系数 (rank+1)/9 —— koishi analysis.ts 的 standard 公式里就有，旧实现漏了它，
    # 等于**假设所有卡都满级**。满级卡 rank=8 → ×1 无影响；而未满级卡（语料实测 6/12
    # 张是 Rank 0~3）区间会被放大 2~9 倍，导致怎么都对不上、被误判成"旧倾向"。
    # ⚠️ 等级取 ``_rank_of``（反推值）：OCR 徽章常读错（8→1），直接信会让区间缩到 2/9。
    return abs(base) * omega * f * (_eff_rank(parsed) + 1) / 9.0


def _range_of(s: dict, parsed: dict, fix: dict) -> tuple[float, float] | None:
    mid = _mid(s, parsed, fix)
    if mid is None:
        return None
    return mid * 0.9, mid * 1.1


def _fmt_range(lo: float, hi: float, unit: str, is_neg: bool = False) -> str:
    """范围显示：两端都按 round(…,1)（经参考卡逐位核验）。

    伯斯顿样例（disp=1.2，3正1负）：
      Damage  185.6247×[0.9,1.1] = 167.062~204.187 → 167.1%~204.2%
      Crit    134.9966×[0.9,1.1] = 121.497~148.496 → 121.5%~148.5%
      Zoom     53.9946×[0.9,1.1] =  48.595~ 59.394 → -59.4%~-48.6%
    旧实现下限用 floor 会输出 167.0 / 121.4 / 48.5，与参考卡差 0.1——已修正。
    负词条：区间整体取负、更负（更大惩罚）在前，如 -59.4%~-48.6%。
    """
    lo_d = round(lo, 1)
    hi_d = round(hi, 1)
    if unit == "x":
        return f"x{1 + lo_d:.2f}-x{1 + hi_d:.2f}"
    if is_neg:
        return f"-{hi_d:.1f}%~-{lo_d:.1f}%"
    return f"{lo_d:.1f}%-{hi_d:.1f}%"


def _dev_text(v: float, mid: float) -> str:
    """「偏差 ±X.XX%」：词条 roll 相对理论中值(mid)的偏离（两位小数，参考卡口径）。

    dev = |v| / mid - 1：正 = 高于中值 / 惩罚更大（roll 更极端），
    负 = 低于中值 / 惩罚更小。正负词条统一用加成量绝对值比较，
    与参考卡「(落点-中值)/中值」一致。"""
    if not mid:
        return "—"
    dev = (abs(v) / mid - 1.0) * 100.0
    return f"{dev:+.2f}%"


def _percentile(v: float, lo: float, hi: float, is_neg: bool) -> float:
    """「数值X.X%」：距好端百分位（正词条=高 roll 好；负词条=惩罚越小越好）。
    负词条用惩罚量绝对值参与；结果钳制 [0,100]，避免低于范围最小惩罚时溢出。"""
    if hi <= lo:
        return 0.0
    if is_neg:
        p = (hi - abs(v)) / (hi - lo) * 100.0
    else:
        p = (v - lo) / (hi - lo) * 100.0
    return max(0.0, min(100.0, p))


# ---------------------------------------------------------------------------
# DPS 模拟（本项目实现；靶子口径与参考卡脚注一致）
# ---------------------------------------------------------------------------
#: 标准配卡（显示名；EN 名仅注释）。近战走通用表，DPS 对近战为简化口径。
#: 🔴 **配卡单一事实源**（2026-11-13 第30轮）：卡面画出来的 7 张卡 ≡ 算伤害用的桶。
#: 旧实现有三套并存的配卡 —— ①`_BUILD`（卡面显示：镀层/猎者军火/速度触发那条现代
#: meta，「镀层」是 Galvanized 系列、**没有膛线**）；②`_STD_BUILD_GUN`（DPS 用：
#: 膛线/重口径/元素）；③Robot 实测（= ②）。卡面与数字对不上，用户会直接判「配卡不符」。
#: 现统一为：本表（en 键）→ 数字由桶表给出 → zh 由 `mod_full.json` 反查。三处同源。
#: 非暴击变体已由 Robot 冰凇真实 8 槽配卡逐张验证（紫卡 + 膛线/重口径/卑劣加速/
#: 低温弹头Prime/地狱火/分裂膛室/私法军备），暴击变体把「卑劣加速 + 私法军备」
#: 换成「死眼 + 敏感」类暴击双卡，两者都是 7 张（8 槽 − 紫卡 1 槽）。
_BUILD_EN = {
    "noncrit": {
        "Rifle": ["serration", "heavy caliber", "vile acceleration",
                  "primed cryo rounds", "hellfire", "split chamber",
                  "vigilante armaments"],
        "Pistol": ["hornet strike", "augur pact", "anemic agility",
                   "deep freeze", "heated charge", "barrel diffusion",
                   "lethal torrent"],
        "Shotgun": ["point blank", "blaze", "chilling grasp", "incendiary coat",
                    "hell's chamber", "shotgun barrage"],
    },
    "crit": {
        "Rifle": ["serration", "heavy caliber", "primed cryo rounds", "hellfire",
                  "split chamber", "point strike", "vital sense"],
        "Pistol": ["hornet strike", "augur pact", "deep freeze", "heated charge",
                   "barrel diffusion", "pistol gambit", "target cracker"],
        "Shotgun": ["point blank", "blaze", "chilling grasp", "incendiary coat",
                    "hell's chamber", "blunderbuss", "ravage"],
    },
}
_MOD_FULL_CACHE: dict | None = None


def _mod_full_map() -> dict:
    """`mod_full.json["map"]`：``{en_lower: {zh, polarity, drain, type, ...}}``（懒加载）。"""
    global _MOD_FULL_CACHE
    if _MOD_FULL_CACHE is None:
        try:
            raw = json.loads((_DATA_DIR / "mod_full.json").read_text(encoding="utf-8"))
            _MOD_FULL_CACHE = raw.get("map") or {}
        except Exception as e:  # noqa: BLE001
            logger.warning(f"riven_mod_full_load_failed: {type(e).__name__}: {e}")
            _MOD_FULL_CACHE = {}
    return _MOD_FULL_CACHE


def _mod_zh(en: str) -> str:
    """en 键 → 中文卡名；查不到**回落 en 本身**（绝不编造译名）。"""
    return ((_mod_full_map().get((en or "").strip().lower()) or {}).get("zh")
            or (en or ""))


def _build_zh(cat: str, crit: bool = False) -> list[str]:
    """该类别（+暴击变体）的 7 张真卡中文名，顺序即卡面顺序。"""
    keys = (_BUILD_EN["crit" if crit else "noncrit"].get(cat) or [])
    return [_mod_zh(k) for k in keys]


def _std_crit(w: dict) -> bool:
    """该武器是否按暴击变体配卡（与 `_gun_std_bucket` 共用同一阈值，单一判据）。"""
    return float((w or {}).get("criticalChance") or 0) >= _STD_CRIT_CC_MIN


#: 卡面显示表（非暴击变体；暴击变体见 `_BUILD_CRIT`）。由 `_BUILD_EN` 派生，
#: **不再手抄卡名** —— 旧实现手抄的是一套与 DPS 桶无关的 meta build。
_BUILD = {cat: _build_zh(cat, False) for cat in _BUILD_EN["noncrit"]}
_BUILD_CRIT = {cat: _build_zh(cat, True) for cat in _BUILD_EN["crit"]}

#: riven_disp.json 的 **7 类** type → 配卡表归属（`melee` / 4 类枪械）。
#: 🔴 单一事实源：文本卡 `build_card` 与图片卡 `riven_full._build_key` 必须用同一张表，
#: 由守门 V10 交叉锁死（两处漂移立刻红）。旧文本卡写
#: `_BUILD.get(parsed["type"] or "Rifle", _BUILD["Rifle"])` —— **Zaw 与 Kitgun 都会落到
#: 步枪卡**（Zaw 是近战、Kitgun 是手枪），未知类型也默认套步枪，直接违反
#: 「没真实类型就不假装给配卡」铁律。
_TYPE_TO_BUILD_KEY = {
    "Melee": "melee", "Zaw": "melee",
    "Pistol": "Pistol", "Kitgun": "Pistol",
    "Rifle": "Rifle", "Archgun": "Rifle",
    "Shotgun": "Shotgun",
}


def _build_key_for_type(rtype: str | None) -> str:
    """7 类 type → 配卡表键；未知/缺失返回空串（调用方须走诚实占位，禁默认套枪卡）。"""
    return _TYPE_TO_BUILD_KEY.get((rtype or "").strip(), "")

#: 极化次数口径（2026-11-13 第30轮）：**最小化**，不是「8 槽全极化」。
#: Warframe：rank30 容量 30，装 Orokin 反应堆 ×2 = **60**；Mod drain = baseDrain + 满级 rank；
#: 槽位极性匹配则 drain 减半（向上取整）。故「需极化 N 次」= 让总量 ≤60 的最少槽数。
#: Robot 冰凇卡面写 **8 次**（=8 槽全极化，非最小化）；本地实算这套 7 卡最小 **4 次**
#: （重口径 16→8 / 低温弹头Prime 16→8 / 膛线 14→7 / 分裂膛室 15→8，90→59）。
#: 两个口径都自洽，卡面采用**可省钱的真实最小值**，并在标题里只写次数，不做夸张。
_MOD_CAPACITY = 60
_MODS_CANON_CACHE: dict | None = None


def _mods_canon() -> dict:
    """``{en_lower: (polarity, max_drain)}``，canonical 条目（跳过 Beginner/Intermediate，
    `/Expert/` 只认 Primed 前缀，同名取 drain 最大者）。"""
    global _MODS_CANON_CACHE
    if _MODS_CANON_CACHE is not None:
        return _MODS_CANON_CACHE
    out: dict[str, tuple[str, int]] = {}
    try:
        items = json.loads((_DATA_DIR / "Mods.json").read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        logger.warning(f"riven_mods_canon_failed: {type(e).__name__}: {e}")
        _MODS_CANON_CACHE = {}
        return _MODS_CANON_CACHE
    for m in items:
        u = m.get("uniqueName") or ""
        n = (m.get("name") or "").strip().lower()
        if not n:
            continue
        if "/Beginner/" in u or "/Intermediate/" in u:
            continue
        if "/Expert/" in u and not (m.get("name") or "").startswith("Primed"):
            continue
        drain = int(m.get("baseDrain") or 0) + int(m.get("fusionLimit") or 0)
        pol = str(m.get("polarity") or "")
        if n not in out or drain > out[n][1]:
            out[n] = (pol, drain)
    _MODS_CANON_CACHE = out
    return out


def _forma_needed(mods_en: list[str], capacity: int = _MOD_CAPACITY) -> int | None:
    """装满这些 Mod 所需**最少极化次数**（容量 60 = rank30 + Orokin 反应堆）。

    口径（2026-11-13 第30轮，逐张对账后定稿）：
      * 满级 drain = ``baseDrain + fusionLimit``（`_mods_canon` 从 Mods.json 取）；
      * 槽位极化匹配 → drain **减半向下取整**（15→7），故**节省量 = ``d - d // 2``**；
      * 贪心：每次极化当前 drain 最大的一张，收益最大且单调，贪心即最优。

    🔴 旧实现写 ``cur -= d // 2`` —— 那是「极化后的新 drain」，不是「节省量」，
    只有偶数 drain 时两者恰好相等；奇数（15/11/9）会**少算节省、高估次数**
    （暴击霰弹 73 点实需 2 次，旧算法给 3 次）。已修正为 ``cur -= d - d // 2``。

    返回 ``None`` = 数据缺失（有 Mod 查不到 drain），调用层据此不写次数，绝不编数。
    """
    tbl = _mods_canon()
    drains = []
    for en in mods_en:
        row = tbl.get((en or "").strip().lower())
        if not row:
            return None
        drains.append(row[1])
    cur = sum(drains)
    n = 0
    for d in sorted(drains, reverse=True):     # 极化大卡收益最大，贪心即最优（减半单调）
        if cur <= capacity:
            break
        cur -= d - d // 2                      # 节省量 = 原 drain − 极化后 drain
        n += 1
    return n if cur <= capacity else None
#: 近战通用「病毒 + 切割 + 重击」配卡（2026-11-11 全量对齐图2 Rabbit 参考卡，
#: 2026-11-12 修正两处卡名——旧名按译名猜、mod_full.json 事实源里根本不存在）。
#: 7 张主卡（末格「当前紫卡」由 riven_full._build_mods 追加），顺序逐格对齐图2 原图，
#: 每张卡名都可在 data/warframe_items/mod_full.json 的 zh 字段精确命中：
#:   热病打击 Prime(primed fever strike) / 北风(north wind) → 冰+毒=病毒，放大生命伤害
#:   邪恶蓄力(corrupt charge)            → 连击持续时间 + 初始连击（重击启动）
#:   震波(seismic wave)                  → Madurai 技法（近战范围清怪）
#:   肉碾虫 巨颚(carnis mandible)        → 切割伤害 + 触发几率（流血绕过护甲）
#:   牺牲 斩铁(sacrificial steel)        → 暴击几率 + 派系伤害（重击暴击核心）
#:   肢解(organ shatter)                 → 暴击伤害
#: 卡名真值事实源 = mod_full.json；配图 = riven_full_assets/mods/<en_slug>.png（离线真卡）。
#: 旧表「压迫 Prime/热毒 Prime/渴血/泣血/角斗 威力/粉碎者/触手 Prime」属纯状态流，
#: 与图2 的病毒切割重击流不符，整表替换。仍为「近战通用」表：不同近战武器按此
#: 套路出卡，不对单武器硬编码（避免又制造「一类武器写死一张表」的新错配）。
_MELEE_BUILD = ["热病打击 Prime", "北风", "邪恶蓄力", "震波",
                "肉碾虫 巨颚", "牺牲 斩铁", "肢解"]


def _weapon_raw(name: str) -> dict | None:
    return _load_raw_weapons().get((name or "").strip().lower())


def _base_bucket() -> dict:
    return {"base": 0.0, "cc": 0.0, "cm": 0.0, "ms": 0.0, "fr": 0.0,
            "elem": 0.0, "sc": 0.0, "mag": 0.0, "reload": 0.0,
            "phys_neg": {"impact": 0.0, "puncture": 0.0, "slash": 0.0}}


def _clone_bucket(b: dict) -> dict:
    """配卡桶**深拷贝**（``phys_neg`` 是嵌套字典，``dict(b)`` 浅拷贝会共享子字典）。

    第27轮根因（用户「伤害分乱写 100」）：``_sim`` 内 ``cur_b/cur_max_b/best_b/b2``
    原先一律 ``dict(std)`` 浅拷贝 → 四个桶共用同一个 ``phys_neg``。而 ``_apply_riven``
    写的是 ``bucket["phys_neg"][k] += ...``（原地改），于是**每份含物理词条的卡/最优组合
    都会反向污染源桶 ``std``**：Braton 实测 ``dps_base`` 从 1200.77 被抬到 1718.28
    （最优组合含 ``Slash Damage`` → 切割段被乘 2.188），``gain_max`` 反而被压到 35.1%，
    低于当前增益 123% → ``dmg = gain/gain_max`` 被 ``min(100)`` 顶到 100。
    物理词条越多的武器失真越重（步枪/霰弹/近战几乎必带），故必须逐层拷贝。
    """
    out = dict(b)
    out["phys_neg"] = dict(b.get("phys_neg") or {})
    return out


def _derive_cat(w: dict) -> str:
    """从武器原始记录推导「主武器/副武器/近战」类别（DE 导出无 `cat` 字段，原实现
    读 w.get('cat') 恒为 None，导致步枪被错误套用手枪基线——第26轮后暴露的泛化 bug）。"""
    c = (w.get("category") or "").strip()
    if c == "Primary":
        return "主武器"
    if c == "Secondary":
        return "副武器"
    if c == "Melee":
        return "近战"
    t = (w.get("type") or "").strip()
    # 🔴 2026-11-13 第31轮：Arch-Gun 面板（`Arch-Gun.json`，20 条）本轮才随网络补齐。
    # 空战枪既不是 Primary 也不是 Secondary，`category` 为 "Arch-Gun"，旧实现返回空串
    # → `_std_gun_cat` 落 "Rifle"（巧合正确）但 `_derive_cat` 的调用方拿到空类别。
    # 这里显式归「主武器」（与 riven_disp 的 Archgun→Rifle 桶一致），零武器名特例。
    if t in ("Arch-Gun", "Archgun", "Arch-Gun (Atmosphere)") \
            or c in ("Arch-Gun", "Archgun"):
        return "主武器"
    if t in ("Rifle", "Bow", "Launcher", "Shotgun", "Cannon", "Crossbow"):
        return "主武器"
    if t in ("Pistol",):
        return "副武器"
    if t in ("Melee", "Zaw", "Gunblade"):
        return "近战"
    return ""


def _std_gun_cat(w: dict, rtype: str | None = None) -> str:
    """紫卡武器 → 配卡桶键（Rifle/Pistol/Shotgun/Archgun）。

    优先用 `riven_disp` 的权威类型 `parsed["type"]`（Rifle/Pistol/Shotgun/Archgun/
    Kitgun），缺失时才回落面板 `_derive_cat` + `w["type"]`。
    `_derive_cat` 把 Shotgun 也归成「主武器」，所以面板回落分支必须再按 `w["type"]`
    细分一次，否则霰弹会套步枪配卡（基伤 3.15 vs 1.50，差一倍）。
    **零武器特例**：只按类型分桶，不看武器名。
    """
    t = (rtype or "").strip()
    if t in _STD_CAT_BY_RIVEN:
        return _STD_CAT_BY_RIVEN[t]
    if (w.get("type") or "").strip() == "Shotgun":
        return "Shotgun"
    if (w.get("type") or "").strip() in ("Arch-Gun", "Archgun", "Arch-Gun (Atmosphere)"):
        return "Archgun"
    return "Pistol" if _derive_cat(w) == "副武器" else "Rifle"


def _gun_std_bucket(w: dict, rtype: str | None = None) -> dict:
    """枪械标准配卡桶：按**类型**取真实终局配卡（见 `_STD_BUILD_GUN`）。

    与旧实现的区别（2026-11-13 第30轮）：
      * 基伤从「一张模组」改为「两张」——主武器 膛线+重口径 330%、副武器 黄蜂蜇刺+
        Augur Pact 310%、霰弹 抵近射击+燃烧弹 150%、Archgun 赤晶枪管+破坏弹 160%；
      * 元素从固定 120% 改为各类别的真实双元素（步枪 165+90、副手 90+90、
        霰弹 90+90、Archgun 120+120）；
      * 多重/射速同样按类别取真值（副手 弹头扩散+致命洪流 180% 等）；
      * 暴击模组按类别取真值（手枪 手枪精通120/目标标定60，不再套步枪的 150/120）。
      * **槽位预算**（2026-11-13 第30轮补）：8 槽里紫卡占 1 ⇒ 真卡恒 ≤7 张。暴击双卡
        占用「射速 + 第二张多重」两个位置（见 `_STD_BUILD_GUN_CRIT`），不再追加到 7 张之上。
    """
    b = _base_bucket()
    cat = _std_gun_cat(w, rtype)
    crit = float(w.get("criticalChance") or 0) >= _STD_CRIT_CC_MIN
    base, elem, ms, fr, cc, cm = (_STD_BUILD_GUN_CRIT if crit else _STD_BUILD_GUN)[cat]
    b["base"] += base
    b["elem"] += elem
    b["ms"] += ms
    b["fr"] += fr
    if crit:
        b["cc"] += cc
        b["cm"] += cm
    return b


def _melee_std_bucket(w: dict) -> dict:
    """近战标准配卡桶（2026-11-12 新增，真实近战机制）。

    近战与枪械的 std build 完全不同：无多重射击、无弹匣/装填（持续=面板，占空比 1.0）、
    攻击速度走 ``w.fireRate``（DE 把近战攻速存 fireRate 字段）。配卡桶：
      * 基伤：压迫点 +120%；
      * 暴击（仅暴击武器 cc≥10%）：牺牲斩铁/暴击近似 +150%，肢解暴伤 +90%；
      * 元素：90% 元素；
      * 攻速：狂怒 +30%。
    词条名/数值全部来自真表，武器无关、零写死卡名。
    """
    b = _base_bucket()
    cc = float(w.get("criticalChance") or 0)
    b["base"] += 1.20          # 压迫点 +120% 基伤
    if cc >= 0.10:
        # 近战毕业 build 走血冲（Blood Rush）流，持续高连击下已是红暴击，故 std 暴击基线
        # 取**线性等效**高值（`_MELEE_STD_CC` ≈ +2950%，把红暴击的真实 tier 期望倍率折成
        # 线性模型的等效 cc，不是字面暴击率）——紫卡暴击词条边际被正确稀释（Coda 实测
        # +222% 暴击边际 18.2%→7.1%），整卡当前增益精确命中 Rabbit +15.2%；
        # 低暴击武器（cc<10%）不套此基线。
        b["cc"] += _MELEE_STD_CC
        b["cm"] += 0.90        # 肢解 +90% 暴伤
    b["elem"] += _ELEMENT_PCT
    b["fr"] += 0.30            # 狂怒 +30% 攻速
    return b


def _is_melee(parsed: dict, w: dict) -> bool:
    """该紫卡是否近战类（Melee/Zaw）。类型以 riven_disp 的 ``parsed["type"]`` 为准
    （权威），缺失时回落面板 ``_derive_cat``。"""
    t = (parsed.get("type") or "").strip()
    if t in ("Melee", "Zaw"):
        return True
    if t in ("Pistol", "Rifle", "Shotgun", "Kitgun", "Archgun"):
        return False
    return _derive_cat(w) == "近战"


def _std_bucket(parsed: dict, w: dict) -> dict:
    """按紫卡武器**类型**选择标准配卡桶（2026-11-12 泛化修复）。

    类型以 `riven_disp` 的 ``parsed["type"]`` 为准（权威：Pistol/Rifle/Shotgun/
    Kitgun/Archgun = 枪械；Melee/Zaw = 近战），缺失时回落面板 ``_derive_cat``。
    旧实现无条件 `_gun_std_bucket(w)`，把近战/Zaw 套上枪械配卡（副武器基伤 2.20 +
    多重+枪械暴击），是「近战伤害分虚高」的根因。

    2026-11-13：枪械桶再按类型细分（步枪/副手/霰弹/Archgun 四套真实终局配卡），
    权威类型从 `parsed["type"]` 透传给 `_gun_std_bucket`——`_derive_cat` 分不出霰弹。
    """
    if _is_melee(parsed, w):
        return _melee_std_bucket(w)
    return _gun_std_bucket(w, parsed.get("type"))


#: 元素伤词条 = 4 基础元素 + 6 复合元素。事实源 `riven_stat_base.json` 的 **7 类武器表
#: 全部**都登记了这 10 项（复合元素各 89.999998），即游戏真能洗出复合元素紫卡词条。
#: 2026-11-12 根因修复：旧实现只列 4 基础元素，`_apply_riven` 里 `k in _ELEM_KEYS`
#: 把复合元素**静默丢弃**——真卡实测（Ocucor 视使之触：火焰+110.7 / **病毒+104.2** /
#: 多重+132.7 / -切割111.4）病毒词条贡献恒为 0.0%，当前增益被低估 58pp
#: （79.2% → 137.2%）；且 `_DPS_STAT_KEYS` 同源受限，「理论最优组合」永远选不到复合
#: 元素。Warframe 里复合元素与基础元素同机制（按**基伤百分比**追加进同一元素乘区），
#: 故此处同桶处理，零武器特例、7 类通用。
_ELEM_KEYS = ("Electricity Damage", "Heat Damage", "Cold Damage", "Toxin Damage",
              "Viral Damage", "Gas Damage", "Corrosive Damage",
              "Radiation Damage", "Blast Damage", "Magnetic Damage")
_PHYS_KEYS = {"Impact Damage": "impact", "Puncture Damage": "puncture", "Slash Damage": "slash"}


def _riven_mag(s: dict, maxed: bool) -> float:
    """单词条带符号加成量（正 + / 负 −）。maxed=True 取最强 roll（正=上限，负=下限）。"""
    sign = 1.0 if s["sign"] != "-" else -1.0
    if maxed:
        rng = s.get("_rng")
        if rng:
            return sign * (rng[1] if sign > 0 else rng[0])
    v = s["value"] if s["unit"] != "x" else (s["value"] - 1.0)
    return sign * v


def _apply_riven(bucket: dict, stats: list[dict], maxed: bool = False) -> None:
    """把紫卡词条折算进配卡桶。"""
    for s in stats:
        mag = _riven_mag(s, maxed)
        k = s["en"]
        if k in ("Critical Chance",):
            bucket["cc"] += mag / 100.0
        elif k in ("Critical Damage",):
            bucket["cm"] += mag / 100.0
        elif k == "Damage":
            bucket["base"] += mag / 100.0
        elif k == "Multishot":
            bucket["ms"] += mag / 100.0
        elif k in ("Fire Rate", "Attack Speed", "Fire Rate / Attack Speed"):
            bucket["fr"] += mag / 100.0
        elif k in _ELEM_KEYS:
            bucket["elem"] += mag / 100.0
        elif k in _PHYS_KEYS:
            bucket["phys_neg"][ _PHYS_KEYS[k] ] += (-mag) / 100.0  # 负物理 → 减配比例
        elif k in ("Magazine Capacity", "Ammo Maximum"):
            bucket["mag"] += mag / 100.0          # 弹匣容量 → 持续 DPS 占空比
        elif k == "Reload Speed":
            bucket["reload"] += mag / 100.0       # 装填速度：正=更快（reload_mult 取倒数）
        # 其余（变焦/后坐/派系/触发等）不进 DPS（对齐参考卡「伤害+0.0%」）


# ---------------------------------------------------------------------------
# 配卡推荐：按「当前武器面板 + 当前紫卡数值」的边际 DPS 选卡（2026-11-13 第38轮）
# ---------------------------------------------------------------------------
#: MOD 满级效果行 → DPS 桶增量。按**最具体关键词优先**排列（「暴击伤害」必须先于
#: 「伤害」，否则会被误折成基伤）。唯一事实源 = ``mod_full.json["map"][en]["max"]``，
#: **零卡名/武器名表**：1540 张 MOD、623 把武器 × 7 类共用这一套规则。
#: 无法折算的效果（精准度、爆炸半径、条件触发、派系伤害…）一律跳过 —— 它们在本
#: 模型里本就不进 DPS，硬塞一个非 0 数就是编造。
_MOD_EFFECT_RULES: tuple[tuple[str, str], ...] = (
    ("暴击伤害", "cm"), ("暴击几率", "cc"), ("暴击率", "cc"),
    ("多重射击", "ms"), ("攻击速度", "fr"), ("射速", "fr"),
    ("触发几率", "sc"), ("装填速度", "reload"), ("弹匣容量", "mag"),
    ("冰冻伤害", "elem"), ("火焰伤害", "elem"), ("毒素伤害", "elem"),
    ("电流伤害", "elem"), ("电击伤害", "elem"),
    ("伤害", "base"),
)
_MOD_EFFECT_RE = re.compile(r"^\s*([+-]?\d+(?:\.\d+)?)\s*%\s*(.+)$")


def _mod_bucket_delta(en: str) -> dict:
    """一张 MOD 的满级**常驻**效果 → DPS 桶增量（与 ``_apply_riven`` 同口径）。

    只认 ``+N% <属性>`` 这种行首；条件触发（「击杀时…」）、范围/精准度、
    派系伤害等一律忽略。返回 ``{}`` = 该卡在本模型内无可折算增益，不编造。
    """
    row = _mod_full_map().get((en or "").strip().lower()) or {}
    out: dict[str, float] = {}
    for line in (row.get("max") or []):
        if not isinstance(line, str):
            continue
        m = _MOD_EFFECT_RE.match(line)
        if not m:
            continue
        val = float(m.group(1)) / 100.0
        label = m.group(2)
        for kw, key in _MOD_EFFECT_RULES:
            if kw in label:
                out[key] = out.get(key, 0.0) + val
                break
    return out


def _apply_mod_delta(b: dict, delta: dict) -> None:
    """把 ``_mod_bucket_delta`` 的增量叠加进桶（不含嵌套 phys_neg）。"""
    for k, v in (delta or {}).items():
        if k in b and not isinstance(b[k], dict):
            b[k] = float(b[k]) + float(v)


#: MOD「变体族」前缀/后缀（社区通用命名，不是按武器或按单卡硬编码）。
#: 同一族的卡在 Warframe 里**互斥不能同装**：并合 膛线 / 膛线、
#: 镀层 分裂膛室 / 分裂膛室、低温弹头 Prime / 低温弹头 等。
#: 只做**词形归一**，不列举任何具体卡名，7 类 / 1540 张 MOD 通用。
_MOD_VARIANT_PREFIXES = ("并合 ", "镀层 ", "增幅 ", "孪生 ", "Prime ")
_MOD_VARIANT_SUFFIXES = (" Prime", "  Prime", " 增幅")


def _mod_family(en: str) -> str:
    """MOD 的互斥变体族键：中文名去掉变体前后缀后归一。

    ``并合 膛线`` / ``膛线`` → ``膛线``；``镀层 分裂膛室`` / ``分裂膛室`` →
    ``分裂膛室``。事实源 ``mod_full.json`` 的 zh；查不到回落 en 本身。
    """
    zh = ((_mod_full_map().get((en or "").strip().lower()) or {}).get("zh")
          or en or "").strip()
    for p in _MOD_VARIANT_PREFIXES:
        if zh.startswith(p):
            zh = zh[len(p):]
            break
    for s in _MOD_VARIANT_SUFFIXES:
        if zh.endswith(s):
            zh = zh[:-len(s)]
            break
    return zh.strip().lower() or (en or "").strip().lower()


def _rank_build_by_riven(w: dict, stats: list[dict], candidates: list[str],
                         slots: int = 7) -> dict:
    """按「已装备当前紫卡」的边际 DPS 给候选 MOD 排序，返回最优 ``slots`` 张。

    纯数值、零武器名特例：基线桶 = 空桶 + **当前紫卡词条**（不是标准配卡桶），
    逐张算 ``_dps(w, base + 该卡) − _dps(w, base)``。紫卡自带大量暴击/元素时
    对应类别 MOD 的边际会真实变化 ⇒ 配卡随当前紫卡数值变，而不是贴一张与紫卡
    无关的通用 build。7 类（Rifle/Pistol/Shotgun/Archgun/Kitgun/Melee/Zaw）
    走同一套规则；候选顺序仅作同分 tiebreak（真实 build 的卡优先）。

    :return: ``{"chosen", "dropped", "gains", "degraded"}``；``degraded=True``
        表示武器面板不可用（``_dps`` 恒 0），此时只按候选原顺序取前 slots 张，
        **不假装有数值依据**（诚实降级，绝不编造最优）。
    """
    base = _base_bucket()
    try:
        _apply_riven(base, list(stats or []))
    except Exception:  # noqa: BLE001 — 词条异常不该让配卡整条链路崩掉
        pass
    d0 = _dps(w or {}, base)
    degraded = d0 <= 0
    gains: dict[str, float] = {}
    for en in candidates:
        d = _mod_bucket_delta(en) if not degraded else {}
        if not d:
            gains[en] = 0.0
            continue
        b = _clone_bucket(base)
        _apply_mod_delta(b, d)
        gains[en] = max(0.0, _dps(w, b) - d0)
    order = {en: i for i, en in enumerate(candidates)}
    ranked = sorted(candidates,
                     key=lambda e: (-gains.get(e, 0.0), order.get(e, 1 << 30)))
    # 🔴 第38轮：互斥变体族去重 —— 同族只留边际收益最高的一张。否则会同时推荐
    # 「膛线 + 并合 膛线」「分裂膛室 + 镀层 分裂膛室」这种**游戏里根本装不上**的
    # 组合（真实 build 与标准表并池后必然出现）。按词形归一，零卡名/武器名特例。
    chosen: list[str] = []
    seen: set[str] = set()
    for e in ranked:
        fam = _mod_family(e)
        if fam in seen:
            continue
        seen.add(fam)
        chosen.append(e)
        if len(chosen) >= slots:
            break
    return {"chosen": chosen, "dropped": [e for e in ranked if e not in chosen],
            "gains": gains, "degraded": degraded}


#: 能进 DPS 桶、可被「理论最优组合」选为正词条的词条键（_apply_riven 认识的键 ∩ 有意义基线）。
#: 只做集合筛选，词条名/数值**全部**来自 riven_stat_base.json，零写死卡名/数值。
_DPS_STAT_KEYS = frozenset({
    "Critical Chance", "Critical Damage", "Damage", "Multishot",
    "Fire Rate", "Attack Speed", "Fire Rate / Attack Speed",
    "Electricity Damage", "Heat Damage", "Cold Damage", "Toxin Damage",
    # 复合元素：与 `_ELEM_KEYS` 必须同步（`_apply_riven` 只认 _ELEM_KEYS，漏一边就会出现
    # 「最优组合选中了它、折算却当 0」的静默错配）。2026-11-12 与 _ELEM_KEYS 一并补齐。
    "Viral Damage", "Gas Damage", "Corrosive Damage",
    "Radiation Damage", "Blast Damage", "Magnetic Damage",
    "Impact Damage", "Puncture Damage", "Slash Damage",
    "Magazine Capacity", "Ammo Maximum", "Reload Speed",
})
#: 对 DPS 无影响、可作「理论最优组合」负槽的词条（进不了 _apply_riven 的乘区）。
#: 顺序即优先级；取该武器类型 base 表里存在的第一个。排除 Ammo Maximum（有 mag 占空比影响）。
_ZERO_IMPACT_NEG = ("Zoom", "Weapon Recoil", "Status Duration", "Combo Duration",
                    "Initial Combo", "Damage to Grineer", "Damage to Corpus",
                    "Damage to Infested")


def _theoretical_best_stats(parsed: dict, w: dict) -> list[dict]:
    """该武器类型的**理论最优紫卡组合**（候选参考；2026-11-12 已确认非 Rabbit 的 gain_max 口径）。

    反推证据（两张参考卡铁证）：Rabbit 的「参考最大增益」= **当前卡词条类型 roll 到满值**
    （武器×词条组合级常数），既不是「另选最优 3 词条」，也不是「当前卡同样 4 条洗满」。
    故本函数不再作为 `_sim` 的 gain_max 来源（见 `_sim` 内 `maxed=True` 实现），仅保留为
    「洗卡推荐」候选参考。原依据（图2 Rabbit 参考卡 + `reference_sample()` 阿利乌双枪卡两点）：
      * 图2 Coda Motovore `紫卡增益 当前+15.2% → 最强+138.4%`、击杀效率 `8.6→9.9→20.5`；
      * 阿利乌参考卡 `gain=16.3% / gain_max=161.1%`（`riven_full.reference_sample._gain_parts`）。
      两张卡的「最强」都**远超**「当前同样 4 词条洗到上限」能达到的水平，故 Rabbit 的
      `gain_max` 是**该武器能洗出的理论最优词条组合天花板**（武器潜力基准，与当前卡词条
      无关），而不是「这张卡重洗到最好」。

    旧实现 `_sim` 的 `best_b = _apply_riven(std, stats, maxed=True)` 只把**当前**同样
    4 条洗满（组合锁定）——当前卡 roll 越接近上限，`gain≈gain_max` 越严重，
    `dmg=gain/gain_max×100` 越被顶到 100（用户图1「伤害分乱写 100(S)」根因）。

    本函数从 ``riven_stat_base.json[类型]`` 真表派生最优组合，遵守：
      * 词条名/基线**全部取自真表**，零写死卡名/数值（守门 D 静态断言）；
      * 正槽数量与当前卡一致（3 正 1 负→3 正），只从 ``_DPS_STAT_KEYS ∩ 真表`` 取满 roll
        **边际 DPS 最高**者（边际用 ``_dps`` 真函数测，不用静态热度表）；
      * 负槽取 ``_ZERO_IMPACT_NEG ∩ 真表`` 第一个、惩罚最轻端（不影响 DPS，只还原布局）；
      * 满级中性基准 ``mid = |base| × disp × factor × (rank+1)/9``（与 `_mid` 同源，满 roll=mid×1.1）。

    只服务于「最强紫卡」基准；当前卡贡献（`contrib`）仍按真实 ``stats`` 算，不受影响。
    真表缺失/无武器面板时返回 ``[]``，`_sim` 回退到旧语义（诚实降级，不编造）。
    """
    _, base_tbl, _ = _load_riven_tables()
    table = base_tbl.get(parsed.get("type")) or {}
    if not table or not w:
        return []
    fix = _fix_of(parsed)
    std = _std_bucket(parsed, w)
    # 边际口径 = **持续 DPS**（_dps × 占空比），必须与 `_sim.gain` 的口径一致。
    # 旧实现用裸 DPS 边际挑最优组合，而比较对象是持续增益 → 弹匣/装填类词条只抬持续
    # 增益、不进裸边际，天花板被系统性低估（全量扫描实测 56/623 把武器的中位卡因此
    # 顶到 100：Aegrit 卡增益 156.8% > 天花板 117.5%、Vulcax 163.8% > 101.1%）。
    base_sdps = _dps(w, std) * _sustain_ratio(w, std)
    disp = float(parsed.get("disp") or 1.0)
    # 「最强紫卡」= 该武器**满级（rank 8）**的理论最优组合，是**武器常数**，与当前卡等级无关。
    # 旧实现取 parsed["rank"]：真截图链路 OCR 把 8 读成 1 时，最优组合基值按 (1+1)/9 缩到
    # 2/9 → 天花板塌到 23%，低于当前卡自身增益 → 兜底把 gain_max 抬成当前增益 → 伤害分
    # 顶 100（用户最痛恨的「乱写 100」，2026-11-12 真截图 verify_real 实测复现）。
    # Rabbit 口径同样把「最强 +161.1%/+138.4%」当作武器级恒定值（同武器高低 roll 卡相同），
    # 故这里恒定 rank 8：卡等级只影响「当前增益」（卡面真实数值），不该影响武器潜力上限。
    rk = 8

    def mid_of(en: str, f: float) -> float:
        base = abs(float(table.get(en) or 0))
        return base * disp * f * (rk + 1) / 9.0 if base else 0.0

    pool = [en for en in table if en in _DPS_STAT_KEYS
            and abs(float(table.get(en) or 0)) > 0]

    def marginal(en: str) -> float:
        hi = mid_of(en, fix["buff"]) * 1.1
        b = _clone_bucket(std)
        _apply_riven(b, [{"en": en, "value": hi, "unit": "%", "sign": "+", "_rng": None}])
        return _dps(w, b) * _sustain_ratio(w, b) - base_sdps

    pool.sort(key=marginal, reverse=True)
    n_pos = max(1, min(sum(1 for s in parsed["stats"] if s["sign"] != "-"), 3))
    out: list[dict] = []
    for en in pool[:n_pos]:
        m = mid_of(en, fix["buff"])
        out.append({"en": en, "value": round(m * 1.1, 1), "unit": "%", "sign": "+",
                    "_rng": (m * 0.9, m * 1.1)})
    if sum(1 for s in parsed["stats"] if s["sign"] == "-"):
        for en in _ZERO_IMPACT_NEG:
            m = mid_of(en, abs(fix["curse"]))
            if m:
                out.append({"en": en, "value": round(m * 0.9, 1), "unit": "%", "sign": "-",
                            "_rng": (m * 0.9, m * 1.1)})
                break
    return out


def _crit_mult(cc: float, cm: float) -> float:
    """暴击期望倍率 ``1 + cc×(cm−1)``（cc **不封顶**，线性近似）。

    旧实现 ``min(1.0, cc)`` 把 >100% 的暴击词条整段丢弃（高暴击武器伤害分失真根因）。
    这里不封顶、用线性期望：对 cc>100% 的严格 tier 公式 (``cm^⌊cc⌋ ×(1+frac×(cm−1))``)
    会在 tier 边界产生跳变，使 riven 评分的「当前/最强」比值不连续；线性近似平滑、单调、
    且与 Rabbit 的平滑增益数字一致。真实暴击提层差异由 std build 的高暴击基线吸收。
    """
    return 1.0 + cc * (cm - 1.0)


def _dps(w: dict, bucket: dict) -> float:
    """面板 DPS（对 Lv170 护甲 2700 靶，病毒，弱点 50%——口径照抄参考卡脚注）。"""
    dmg = w.get("damage") or {}
    total = float(dmg.get("total") or 0)
    if total <= 0:
        return 0.0
    bm = 1.0 + bucket["base"]
    cc = max(0.0, float(w.get("criticalChance") or 0) * (1 + bucket["cc"]))
    cm = max(1.0, float(w.get("criticalMultiplier") or 1) * (1 + bucket["cm"]))
    ms = max(1.0, 1.0 + bucket["ms"])
    fr = max(0.1, float(w.get("fireRate") or 1) * (1 + bucket["fr"]))
    crit_exp = _crit_mult(cc, cm)
    dr = _ARMOR / (_ARMOR + 300.0)
    hs = 1.0 + _HEADSHOT_RATIO
    phys_total = sum(float(dmg.get(k) or 0) for k in ("impact", "puncture", "slash"))
    elem_innate = max(0.0, total - phys_total)   # 武器自带元素伤（blast 等），不再被丢弃
    per_hit = 0.0
    # 物理伤：各部位独立算，吃 phys_neg 减幅；切割流血无视护甲（dr）
    for k in ("impact", "puncture", "slash"):
        part = float(dmg.get(k) or 0) * bm
        part *= max(0.0, 1.0 - bucket["phys_neg"].get(k, 0.0))
        per_hit += part * (1.0 if k == "slash" else (1.0 - dr))
    # 元素伤 = 自带元素 + 模组追加元素。Warframe 真实机制：元素模组按「**基伤 × 百分比**」
    # **追加**元素伤（+90% 电 = +0.9×基伤 的电伤），而不是放大已有的那点元素伤。
    # 旧实现 `elem_total*(1+elem)` 只在武器自带元素时才起作用 → 纯 IPS 武器（Braton/
    # 多数步枪/霰弹/近战）的元素词条边际恒为 0（第27轮诊断实测 Electricity/Heat/Cold/
    # Toxin 边际 DPS 全 0.00），「元素类紫卡词条评分全 0」的根因，也是理论最优组合
    # 永远选不到元素词条的原因。负元素词条同理按基伤扣减，整体下限 0（不能扣成负数）。
    elem_eff = max(0.0, elem_innate + bucket["elem"] * total) * bm
    per_hit += elem_eff * (1.0 - dr) * 2.0
    return max(0.0, per_hit) * hs * crit_exp * ms * fr


def _sustain_ratio(w: dict, bucket: dict) -> float:
    """持续 DPS 占空比：弹匣打空时间 /（打空时间 + 装填时间）。

    突袭/持续输出口径：弹匣越大、装填越快，实际秒伤越接近面板；反之（如 Akarius
    弹匣仅 10、装填 3.4s）持续秒伤远低于面板。紫卡「弹匣容量 +X%」放大弹匣、
    「装填速度 ±Y%」改变装填耗时，二者都直接改变真实 TTK，必须进模型，否则
    这类紫卡会出现「伤害分 0.0 / 击杀效率被压平」的伪失败（实测 2026-11-06
    阿利乌双枪 Armadex 卡：三条词条全是触发/弹匣/装填，旧模型三者贡献全 0）。
    缺弹匣或装填字段（近战/无装填武器）时回落 1.0（即面板=持续）。
    """
    mag = float(w.get("magazineSize") or 0)
    reload = float(w.get("reloadTime") or 0)
    fr = max(0.1, float(w.get("fireRate") or 1))
    if mag <= 0 or reload <= 0:
        return 1.0
    mag_mult = max(0.1, 1.0 + bucket.get("mag", 0.0))
    reload_mult = 1.0 / max(0.1, 1.0 + bucket.get("reload", 0.0))  # 装填速度正→更快
    tpm = (mag * mag_mult) / fr
    rt = reload * reload_mult
    return tpm / (tpm + rt)


def _kill_eff(dps: float, dps_base: float) -> float:
    """击杀效率 = 三硬度节点击杀/分钟的几何平均（血量按无紫卡基线校准）。"""
    if dps_base <= 0 or dps <= 0:
        return 0.0
    kills = [60.0 * dps / (dps_base * t) for t in _TARGET_TTK]
    return math.exp(sum(math.log(k) for k in kills) / len(kills))


def _sim(parsed: dict, w: dict) -> dict:
    """完整模拟：无紫卡 / 当前紫卡 / 最强紫卡 + 每词条贡献。

    伤害分与击杀效率均基于**持续 DPS**（面板 × 占空比），占空比由弹匣容量 + 装填速度
    决定。这样「触发/弹匣/装填」类紫卡也能得到非零伤害分与不被压平的击杀效率，
    而不是旧的「三者贡献全 0 → 伤害分 0.0 / 击杀效率 13.4→13.4→13.4」伪失败。
    """
    stats = parsed["stats"]
    for s in stats:
        s.setdefault("_rng", _range_of(s, parsed, _fix_of(parsed)))
    std = _std_bucket(parsed, w)
    cur_b = _clone_bucket(std)
    _apply_riven(cur_b, stats)
    # 当前组合「满 roll」基线（combo 优势比分母）：把当前卡同样词条洗到各自的 +10% 上限。
    cur_max_b = _clone_bucket(std)
    _apply_riven(cur_max_b, stats, maxed=True)
    # 「最强紫卡」= 该武器**理论最优词条组合**裸叠增益 × `_OPTIMAL_CALIB`（武器潜力天花板，
    # 与当前卡词条无关）——Rabbit 闭源口径，两张参考卡铁证（阿利乌 161.1% / Coda 138.4%，
    # 裸叠 545%/511% 均约 3.4×，系数高度一致）。真表缺失/无面板时诚实降级：回退当前组合满 roll。
    best_stats = _theoretical_best_stats(parsed, w)
    if best_stats:
        best_b = _clone_bucket(std)
        for s in best_stats:
            s.setdefault("_rng", _range_of(s, parsed, _fix_of(parsed)))
        _apply_riven(best_b, best_stats, maxed=True)
    else:
        best_b = cur_max_b
    dps_base = _dps(w, std)
    dps_cur = _dps(w, cur_b)
    dps_cur_max = _dps(w, cur_max_b)
    dps_best = _dps(w, best_b)
    # 持续 DPS（占空比）
    rb = _sustain_ratio(w, std)
    rc_ = _sustain_ratio(w, cur_b)
    rcm = _sustain_ratio(w, cur_max_b)
    rbest = _sustain_ratio(w, best_b)
    sdps_base = dps_base * rb
    sdps_cur = dps_cur * rc_
    sdps_cur_max = dps_cur_max * rcm
    sdps_best = dps_best * rbest
    g_cur = sdps_cur / sdps_base - 1.0 if sdps_base > 0 else 0.0
    g_cur_raw = dps_cur / dps_base - 1.0 if dps_base > 0 else 0.0
    # 2026-11-12 口径统一：g_cur_max / g_best 全部改用**持续口径**，与 g_cur（参与
    # dmg=gain/gain_max）同度量。旧实现这三者混用裸 DPS 比值，弹匣/装填类词条只抬
    # 持续增益不进裸比值 → 天花板系统性低估 → 全量扫描 56/623 把武器中位卡顶 100。
    g_cur_max = sdps_cur_max / sdps_base - 1.0 if sdps_base > 0 else 0.0
    g_best_raw = sdps_best / sdps_base - 1.0 if sdps_base > 0 else 0.0
    # 天花板 = 理论最优组合裸叠增益 × 类别标定系数（组合无关，与当前卡词条无关）。
    # 真表缺失走兜底 best_b=cur_max_b 时，退化为「当前组合满 roll」天花板（诚实降级）。
    calib = _OPTIMAL_CALIB_MELEE if _is_melee(parsed, w) else _OPTIMAL_CALIB_GUN
    gain_max = g_best_raw * calib if g_best_raw > 0 else 0.0
    # 不变式：天花板必须 ≥ 当前卡增益，否则 dmg=gain/gain_max 会 >100 被 min(100) 顶满
    # （用户明确反对的「伤害分乱写 100」）。比较对象必须同时含**持续口径 g_cur**：
    # 弹匣/装填类词条只抬持续增益、不进裸增益，只比 g_cur_max 会漏（Aegrit 实测
    # g_cur_max=117.5% 但持续 gain=176.2% → 仍被顶满）。
    _ceil_before = gain_max
    # 记录天花板最终由谁决定，供展示层揭示「刻度被压平」：
    #   calib      → 正常：理论最优 × 标定系数（武器常数，与当前卡无关，可跨卡比较）
    #   cur_max    → 该卡词条洗满已超刻度 → 分数退化为**离满 roll 多近**（同武器不可比）
    #   cur        → 当前卡增益本身已超刻度 → 分数被顶 100
    # 旧实现静默抬升，用户只看到一个 90+ 却不知口径已变（2026-11-12 全量实测：词条类型
    # 正确的典型卡 555/555 都会走到 cur_max 分支），故必须可观测。
    _ceil_tag, gain_max = max(
        (("calib", _ceil_before), ("cur_max", g_cur_max), ("cur", g_cur)),
        key=lambda kv: kv[1])
    if gain_max > _ceil_before + 1e-12:
        # 正常情形（非异常）：标定系数把「理论最优」压到 ~30%（对齐 Rabbit 刻度），故当前卡
        # 自身满 roll 已超过 30% 时会被兜底抬到实测值。DEBUG 级，避免刷生产日志。
        logger.debug(
            f"riven_gain_max_raised_to_current: weapon={parsed.get('weapon')!r} "
            f"calib_ceiling={_ceil_before:.4f} < max(g_cur_max={g_cur_max:.4f}, "
            f"g_cur={g_cur:.4f}) → 取当前实测增益")
    combo_sup = g_best_raw / g_cur_max if g_cur_max > 0 else 1.0
    contrib = {}
    for s in stats:
        b2 = _clone_bucket(std)
        _apply_riven(b2, [x for x in stats if x is not s])
        d2 = _dps(w, b2) * _sustain_ratio(w, b2)
        c = (sdps_cur - d2) / d2 * 100.0 if d2 > 0 else 0.0
        # 负词条**保留符号**：c = 只去掉该词条时相对增益，负值即该词条造成的真实损失。
        # 旧实现 `max(0.0, c)` 把有害负面钳成 `+0.0%`，与脚注「只去掉该词条时击杀效率的
        # 下降」自相矛盾——Ocucor `-111.4% 切割` 在本模型里实际代价 -18.4%（切割段被打到 0），
        # 却显示 +0.0%，用户无法判断负面有多毒。QoL 负面（-变焦/-后坐）本身 c≈0，无需钳。
        contrib[s["en"] + ("-" if s["sign"] == "-" else "+")] = c
    return {"dps_base": sdps_base, "dps_cur": sdps_cur, "dps_best": sdps_best,
            "gain": g_cur, "gain_raw": g_cur_raw, "gain_max": gain_max, "g_cur_max": g_cur_max,
            "g_best_raw": g_best_raw, "combo_sup": combo_sup, "ceil_src": _ceil_tag,
            "contrib": contrib,
            "ke_base": _kill_eff(sdps_base, sdps_base),
            "ke_cur": _kill_eff(sdps_cur, sdps_base),
            "ke_best": _kill_eff(sdps_base * (1.0 + gain_max), sdps_base)}


# ---------------------------------------------------------------------------
# 市场（2026-11-04 22:25 重写：按武器真实拍卖聚合 + 按词条分组推荐）
# ---------------------------------------------------------------------------
def _weapon_slug(parsed: dict) -> str:
    """紫卡武器名 → WFM **拍卖接口**用的 riven slug（下划线族名，如 ``coda_motovore``）。

    2026-11-12 根因修复（用户图2 Coda Motovore 市场两盒空）：
    旧实现走 ``warframe._resolve_riven_weapon`` 拼出**连字符** slug（``coda-motovore``），
    那是普通物品库 / disp 表的 key 体系；WFM ``/v1/auctions/search`` 的
    ``weapon_url_name`` 是**另一套下划线族名**（``coda_motovore``），连字符形态恒定
    返回 ``400 app.auctions.errors.item_not_exist``（真网络矩阵坐实：同参数 ``rubico``
    200/106KB、``coda_motovore`` 200/107KB、``coda-motovore`` 400）。

    修法（**通用、零特例**）：唯一权威事实源 = ``riven_trend.resolve_weapon``，它读
    ``data/warframe_items/riven_wfm_slugs.json``（414 条 en→下划线 slug），中文名先经
    ``warframe._resolve_weapon`` 拿官方英文名、再逐段去 Prime/Wraith 等后缀匹配族名
    （WFM 紫卡只挂基础族名，如 ``Rubico Prime``→``rubico``）。该函数已被 trend 模块
    生产验证。仅当它解析不出（别称/新武器未收录）时，才退回旧逻辑并把连字符归一为
    下划线兜底——绝不硬编码任何单把武器。解析不出 ASCII slug 显式报错，不拿中文查。
    """
    en = (parsed.get("weapon") or "").strip()
    zh = (parsed.get("weapon_zh") or "").strip()
    tried = []
    # 1) 权威源：WFM riven slug 静态表（下划线族名，中英文通用）
    try:
        from . import riven_trend as _rt
        for q in (en, zh):
            if not q:
                continue
            info = _rt.resolve_weapon(q)
            tried.append((q, (info or {}).get("slug")))
            if info and info.get("slug") and re.fullmatch(r"[a-z0-9_]+", info["slug"]):
                return str(info["slug"])
    except Exception as e:  # noqa: BLE001 — 表缺失/异常 → 退回兜底，不让市场链路整体崩
        logger.warning(f"riven_slug_trend_failed: {type(e).__name__}: {e}")
    # 2) 兜底：旧 disp/物品库 slug，归一化连字符→下划线（WFM 拍卖只认下划线族名）
    for q in (en, zh):
        if not q:
            continue
        slug = (warframe._resolve_riven_weapon(q) or "").strip().lower()
        slug = slug.replace("-", "_") if re.fullmatch(r"[a-z0-9_\-]+", slug) else slug
        tried.append((q, slug))
        if slug and re.fullmatch(r"[a-z0-9_]+", slug):
            return slug
    raise RivenMarketError(
        "无法把武器名解析成 WFM riven slug：en=%r zh=%r tried=%r" % (en, zh, tried))


def _auction_price(a: dict) -> float | None:
    """挂牌价：一口价优先，缺失用起拍价；无价返回 None（不编 0）。"""
    v = a.get("buyout_price")
    if v is None:
        v = a.get("starting_price")
    if v is None:
        return None
    try:
        p = float(v)
    except (TypeError, ValueError):
        return None
    return p if p > 0 else None


def _auction_attrs(a: dict) -> list[dict]:
    return ((a.get("item") or {}).get("attributes") or [])


#: 429 限流处理（2026-11-12）：Cloudflare 对连续请求返回 429 + retry-after。
#: ``warframe._fetch`` 失败时统一返回 None，把状态码吞掉 → 卡面只能报
#: 「WFM 拍卖返回 NoneType（不是 JSON 对象）」，用户完全无法判断是限流还是接口坏了。
#: 这里在失败路径上**额外探测一次**状态码：是 429 就按 retry-after 等（有上限）重试一次，
#: 仍限流则抛出带秒数的明确错误，让用户知道「等 43 秒再试」而不是对着 NoneType 发愣。
_WFM_429_RETRY = 1          # 最多重试 1 次（再多会把用户等穿）
_WFM_429_MAX_WAIT = 12.0    # 单次最多等 12s；retry-after 更大就直接报错让用户稍后重试


async def _probe_429(url: str) -> float | None:
    """探测该 URL 是否被限流：是 429 → 返回 retry-after 秒数；否则 None。"""
    import httpx
    try:
        async with httpx.AsyncClient(headers=dict(_WFM_AUCTION_HEADERS),
                                     follow_redirects=True, timeout=8.0,
                                     verify=False) as c:
            r = await c.get(url)
    except Exception as e:  # noqa: BLE001 — 探测失败不改变主错误语义
        logger.warning(f"wf_riven_429_probe_failed: {type(e).__name__}: {e}")
        return None
    if r.status_code != 429:
        return None
    try:
        return float(r.headers.get("retry-after") or 0)
    except (TypeError, ValueError):
        return 0.0


async def _fetch_auction_page(slug: str, sort_by: str, extra: str = "",
                              allow_empty: bool = False) -> list[dict]:
    """抓一页 WFM 紫卡拍卖（按武器 + 可选词条过滤）。

    ``extra`` 是已拼好的 query 片段（``negative_stats=`` / ``positive_stats=``）。
    ``allow_empty=True`` **只**用于「精确匹配查无同组合卡」这种合法空结果；
    其余场景空样本一律显式 :class:`RivenMarketError`，绝不静默返回空冒充成功。
    """
    key = (slug, sort_by, extra)
    hit = _AUCTION_CACHE.get(key)
    if hit and time.time() - hit[0] < _WFM_PAGE_TTL:
        return hit[1]
    url = (f"{warframe.MARKET_BASE}/v1/auctions/search"
           f"?type=riven&sort_by={sort_by}&buyout_policy=direct"
           f"&weapon_url_name={slug}{extra}")

    async def _call():
        wait = _WFM_MIN_INTERVAL - (time.time() - _WFM_LAST_REQ[0])
        if wait > 0:
            await asyncio.sleep(wait)
        _WFM_LAST_REQ[0] = time.time()
        try:
            return await asyncio.wait_for(
                warframe._fetch(url, headers=dict(_WFM_AUCTION_HEADERS),
                                verify_ssl=False, timeout=10.0, retry=2),
                timeout=14.0)
        except Exception as e:  # noqa: BLE001 — 上抛为领域错误，不吞
            raise RivenMarketError(
                "WFM 拍卖请求失败（%s / %s%s）：%s: %s"
                % (slug, sort_by, extra[:60], type(e).__name__, e)) from e

    d = await _call()
    for _try in range(_WFM_429_RETRY + 1):
        if d is not None:
            break
        ra = await _probe_429(url)
        if ra is None:      # 不是限流 → 保持原有「返回 None」语义，不改变判断
            break
        if _try >= _WFM_429_RETRY:
            raise RivenMarketError(
                "WFM 限流 429（retry-after %ss）：%s / %s%s；已重试 %d 次仍限流，"
                "请等待 %s 秒后重试" % (ra, slug, sort_by, extra[:40],
                                       _WFM_429_RETRY, int(max(ra, 1))))
        _wait = min(max(ra, 1.0), _WFM_429_MAX_WAIT)
        logger.warning(f"wf_riven_429 slug={slug} retry_after={ra} wait={_wait:.0f}s")
        await asyncio.sleep(_wait)
        d = await _call()

    if not isinstance(d, dict):
        raise RivenMarketError(
            "WFM 拍卖返回 %s（不是 JSON 对象）：%s / %s" % (type(d).__name__, slug, sort_by))
    if d.get("error"):
        raise RivenMarketError("WFM 拍卖接口报错：%r / %s" % (d["error"], sort_by))
    auc = (d.get("payload") or {}).get("auctions")
    if not isinstance(auc, list):
        raise RivenMarketError(
            "WFM 拍卖返回非列表（%s）：%s / %s" % (type(auc).__name__, slug, sort_by))
    if not auc and not allow_empty:
        raise RivenMarketError(
            "WFM 拍卖返回 0 条样本（武器 %s / %s%s）；检查 slug 与 Platform 头"
            % (slug, sort_by, extra[:60]))
    _AUCTION_CACHE[key] = (time.time(), auc)
    return auc


def _dedup(pages: list[list[dict]]) -> list[dict]:
    seen, out = set(), []
    for page in pages:
        for a in page:
            k = a.get("id") or id(a)
            if k in seen:
                continue
            seen.add(k)
            out.append(a)
    return out


def _rank_of(a: dict) -> int | None:
    """WFM 紫卡等级；缺失/异常返回 None（绝不猜 0 或 8）。"""
    v = (a.get("item") or {}).get("mod_rank")
    return v if isinstance(v, int) else None


def _same_rank(auctions: list[dict], rank: int) -> list[dict]:
    """只保留同等级挂牌：rank0 未洗卡与 rank8 满级卡的价格不可直接比较。"""
    return [a for a in auctions if _rank_of(a) == rank]


def _prices_of(auctions: list[dict]) -> list[float]:
    return sorted(p for p in (_auction_price(a) for a in auctions) if p is not None)


def _robust_center(prices: list[float]) -> float:
    """抗离群中心值 = **中位数**（唯一价格聚合入口）。

    2026-11-04 23:0x：旧版用算术均值，被 66666p 挂单拉到 +3949%。
    WFM 是「卖家要价」，离群挂单极多，均值没有参考价值。
    单独抽成函数是为了让联调门禁能注入离群反例（改回 mean 必须报错）。
    """
    if not prices:
        raise RivenMarketError("价格池为空，无法计算中心值")
    return statistics.median(prices)


def _hot_counts(auctions: list[dict], positive: bool) -> dict[str, int]:
    """词条**出现频次**（只看有没有，不看价格）。

    ``price_desc``（全站最贵 100 条）**只允许**通过本函数参与计算，
    用于判断「哪些词条出现在高价卡上」；它的价格绝不进入任何溢价/基线。
    """
    g: dict[str, int] = defaultdict(int)
    for a in auctions:
        for at in _auction_attrs(a):
            if bool(at.get("positive")) == positive and at.get("url_name"):
                g[at["url_name"]] += 1
    return dict(g)


def _short(slug: str) -> str:
    return _WFM_ATTR_SHORT.get(slug, slug)


async def fetch_market_bundle(parsed: dict) -> dict:
    """按武器抓真实 WFM 挂牌 → 基线 / 推荐词条 / 类似紫卡价。

    2026-11-04 23:0x 口径修订（用户质疑「和别人数据源一样吗」之后）：

      * **价格只取普通挂牌**：``price_asc`` 低价视图 +
        ``negative_stats=<推荐负>`` 的同负词条普通挂牌，都按
        ``mod_rank == parsed.rank``（缺省 8）过滤，保证同等级可比。
      * ``price_desc``（全站最贵 100 条，实测含 66666p 挂单）**只用于词条
        热度频次**，绝不参与任何价格计算。旧版拿它算均价得到 +3949%，
        是被离群挂单拉爆的伪溢价，不是真实溢价。
      * **同分位对比**（2026-11-04 23:25 第二次修订）：WFM 公开检索实测只暴露
        「最便宜 100 / 最贵 100」两个**截断视图**——``page`` / ``mod_rank_min`` /
        ``created_at_desc`` 等参数被忽略或直接 400，拿不到代表性中间样本。
        Glaxion 未过滤页的 rank8 只有 13 条（全部 90–100P），而 ``-zoom``
        过滤页的 rank8 有 84 条（150–1700P）：拿「全量负词条分布的中位数」
        比「通用池最便宜 13 条」，会得到 +700% 的**伪溢价**。
        修法：两池各取自身 ``price_asc`` 视图里**同样条数的最低价样本**
        （``pair_k = min(n_base, n_neg)``，同一低价分位），再用中位数对比。
        实测三武器由 +700/+100/+71% 变为 +100/+50/+29%。
      * **聚合统一用中位数**（:func:`_robust_center`），不用算术均值。
      * **匹配层级显式**：``positive_stats`` + ``negative_stats`` 精确匹配
        「正负面相同」；样本 >=3 才给「类似紫卡价」，否则明确写
        「无同组合样本」，不拿别的池冒充精确匹配。
      * 价格性质 = **WM 挂牌要价，非成交价**（WFM 公开 API 只有挂单：
        实测字段 buyout_price/starting_price/top_bid/closed/winner，无成交价）。

    任何失败一律 :class:`RivenMarketError`；不回退静态权重表、不写「暂无」。
    返回 dict 兼容旧 ``auc`` 字段（n/lo/hi/med/zero）。
    """
    slug = _weapon_slug(parsed)
    # 同等级挂牌过滤用**反推等级**（OCR 徽章常读错 8→1，用错等级会拉到 rank1 的价）
    rank = _eff_rank(parsed)

    asc = await _fetch_auction_page(slug, "price_asc")
    desc = await _fetch_auction_page(slug, "price_desc")   # 仅词条热度

    base_prices = _prices_of(_same_rank(asc, rank))
    if len(base_prices) < _WFM_MIN_SAMPLES:
        raise RivenMarketError(
            "WFM 同等级(rank=%d)低价挂牌有效样本 %d 条 < %d（%s）"
            % (rank, len(base_prices), _WFM_MIN_SAMPLES, slug))
    n = len(base_prices)
    med = _robust_center(base_prices)
    lo = base_prices[max(0, int(n * 0.1))]
    hi = base_prices[min(n - 1, int(n * 0.9))]
    # rank0 未洗挂牌：没有样本就写 None（卡面显示「不可估价」），
    # 绝不拿 rank8 的最低价冒充 rank0——「拿别的池冒充」属于明令禁止的兜底。
    # 🔴 2026-11-13 第31轮修口径：卡面标签写的是「零洗**中位**价」，旧实现取
    # ``zero_ps[0]`` = **最低价**，名实不符。实测（容器内 WFM 真网络）对照 Robot
    # 参考卡：阿利乌 Robot 399P，旧实现 120P（rank0 最低）/ 中位 **399P**（逐位命中）；
    # 冰凇 Robot 500P，旧实现 400P / 中位 480P。⇒ 改为中位，并优先取
    # ``re_rolls == 0`` 的真·零洗子集（标签语义），样本不足才退回 rank0 池（同为未升级）。
    zero_ps = _prices_of([a for a in asc
                          if ((a.get("item") or {}).get("re_rolls")) == 0])
    if len(zero_ps) < _WFM_MIN_SAMPLES:
        zero_ps = _prices_of(_same_rank(asc, 0))
    zero = _robust_center(zero_ps) if zero_ps else None

    # ---- 词条热度：只用 desc 的频次，不用它的价格 ----
    neg_hot = _hot_counts(desc, False)
    pos_hot = _hot_counts(desc, True)
    if not neg_hot:
        raise RivenMarketError("WFM 高价视图没有可用的负词条样本（%s）" % slug)
    neg_slug, neg_hot_n = max(
        ((k, v) for k, v in neg_hot.items() if v >= _WFM_MIN_SAMPLES),
        key=lambda kv: kv[1], default=(None, 0))
    if neg_slug is None:
        raise RivenMarketError(
            "WFM 样本不足：没有任何负词条达到 %d 条（%s）" % (_WFM_MIN_SAMPLES, slug))
    rec_pos = [k for k, _ in sorted(pos_hot.items(), key=lambda kv: -kv[1])[:3]]

    # ---- 溢价：同负词条 + 同等级普通挂牌的**同分位中位数对比** ----
    neg_extra = "&negative_stats=" + _quote(neg_slug, safe="")
    neg_pool = await _fetch_auction_page(slug, "price_asc", neg_extra)
    neg_prices = _prices_of(_same_rank(neg_pool, rank))
    if len(neg_prices) < _WFM_MIN_SAMPLES:
        raise RivenMarketError(
            "WFM 同负词条(%s)同等级挂牌仅 %d 条 < %d（%s），不编造溢价"
            % (neg_slug, len(neg_prices), _WFM_MIN_SAMPLES, slug))
    # ⚠️ 两池都来自 price_asc 截断视图，但样本量可能差 6 倍（Glaxion 13 vs 84）。
    # 直接用 neg 全量中位数比 base 全量中位数，等于拿「整个分布」比「最便宜 13 条」，
    # 2026-11-04 23:22 实测得到 +700% 伪溢价。统一取各自最低价的 k 条再比中位数。
    pair_k = min(len(base_prices), len(neg_prices))
    # 2026-11-04 23:29 真实事故：下游「DE 词条 → WFM slug」循环曾用变量名 ``k``，
    # 把这里的 int 覆盖成 str，source 的 %d 直接 TypeError，又被失败态吞掉。
    # 变量名改唯一 + 显式类型断言，杜绝同名覆盖复发。
    if not isinstance(pair_k, int) or pair_k < _WFM_MIN_SAMPLES:
        raise RivenMarketError(
            "同分位对比样本不足或类型异常：k=%r（base=%d / neg=%d，%s），"
            "不退回不对称口径"
            % (pair_k, len(base_prices), len(neg_prices), slug))
    base_med = _robust_center(base_prices[:pair_k])
    neg_median = _robust_center(neg_prices[:pair_k])
    delta = neg_median - base_med
    pct = delta / base_med * 100.0 if base_med > 0 else 0.0

    # ---- 「正负面相同」精确匹配（DE 词条名 → WFM url_name） ----
    # 🔴 2026-11-13 第31轮修正：旧实现把 ``neg_extra``（**推荐洗卡该选的负面**）拼进
    # 精确匹配查询 → 查的是「卡面正词条 + 别人推荐的负面」，根本不是这张卡。3正0负的
    # 卡（如阿利乌）被强行要求带一个负面，必然 0 样本。改为只用**卡面自身**的负面；
    # 卡面没有负面就不加 negative_stats 过滤。
    stats = parsed.get("stats") or []
    pos_slugs, neg_slugs_card, unknown = [], [], []
    for s in stats:
        wfm_slug = _DE_TO_WFM.get(s.get("en"))
        if s.get("sign") == "-":
            if wfm_slug:
                neg_slugs_card.append(wfm_slug)
            else:
                unknown.append(s.get("en"))
            continue
        if wfm_slug:
            pos_slugs.append(wfm_slug)
        else:
            unknown.append(s.get("en"))
    exact = {"status": "unknown", "n": 0, "median": None, "reason": ""}
    if not stats:
        exact["reason"] = "未提供当前卡词条，无法精确匹配"
    elif unknown:
        exact["reason"] = "词条未收录 WFM 映射：" + "/".join(map(str, unknown))
    elif not pos_slugs:
        exact["reason"] = "当前卡无正向词条"
    else:
        p = _quote(",".join(sorted(set(pos_slugs))), safe=",")
        q_exact = "&positive_stats=" + p
        if neg_slugs_card:
            q_exact += "&negative_stats=" + _quote(
                ",".join(sorted(set(neg_slugs_card))), safe=",")
        pool = await _fetch_auction_page(slug, "price_asc", q_exact,
                                         allow_empty=True)
        ps = _prices_of(_same_rank(pool, rank))
        if len(ps) >= _WFM_MIN_SAMPLES:
            exact = {"status": "ok", "n": len(ps),
                     "median": _robust_center(ps), "reason": ""}
        else:
            exact = {"status": "few", "n": len(ps), "median": None,
                     "reason": "同组合同等级样本仅 %d 条 < %d，不给估价"
                               % (len(ps), _WFM_MIN_SAMPLES)}

    # 🔴 2026-11-13 第31轮改标签：``asc`` 池**没有任何词条过滤**（`_fetch_auction_page(slug,"price_asc")`），
    # 且实测 ``page=2`` 返回与第 1 页**完全相同**的 100 条（100/100 重叠）、``before_id`` 被忽略
    # ⇒ WFM 公开检索对紫卡只暴露「最便宜 100 / 最贵 100」两个截断视图，中段不可观测。
    # 因此 ``med`` 的真实语义是「全武器最低价视图的中位 = **地板价**」，不是「同负词条匹配价」
    # （旧标签写「同负词条」名实不符：本卡可能根本没有负面，也从没按负面过滤过 base 池）。
    match_tier = "正负面相同" if exact["status"] == "ok" else "全武器地板价视图"
    pooled = _dedup([asc, desc, neg_pool])
    unrolled_n = sum(1 for a in pooled
                     if ((a.get("item") or {}).get("re_rolls")) == 0)
    source = ("WM 挂牌要价·非成交价｜同等级 rank%d｜同分位 k=%d｜匹配：%s｜样本 %d｜未洗 %d"
              % (rank, pair_k, match_tier, len(pooled), unrolled_n))
    # ---- 卡上**实际**负词条的价差（2026-11-12 对照 Warframe Robot 修订）----
    # Robot 卡面「市场负面参考：[普通负] 切割 +24.9p/+7%」指的是**卡面这条负面**
    # 在市场上值多少；而 ``rec_neg`` 是「推荐洗卡时该选哪条负面」。两者语义不同，
    # 旧实现拿 rec_neg 冒充前者 → 用户看到「变焦 +141p」而不是卡上的「切割」。
    # 通用规则：从 parsed 动态取负面位（``_layout_curse``，含 Weapon Recoil 这类
    # 「卡面显 + 但属负面位」），逐条映射 WFM slug 查询；全 623 把武器同一套逻辑，
    # 零武器硬编码。映射不到（如 WFM 无 Ammo Efficiency）或样本不足 → 留 None，
    # 由展示层退回推荐负面并标明来源，绝不编造。
    card_neg = None
    for _s in stats:
        if not _layout_curse(_s):
            continue
        _ws = _DE_TO_WFM.get(_s.get("en"))
        if not _ws:
            continue
        try:
            _pool = await _fetch_auction_page(
                slug, "price_asc", "&negative_stats=" + _quote(_ws, safe=""),
                allow_empty=True)
        except RivenMarketError:
            continue
        _ps = _prices_of(_same_rank(_pool, rank))
        if len(_ps) < _WFM_MIN_SAMPLES:
            continue
        # 与 base 池取**同条数最低分位**再比中位（沿用 pair_k 口径，避免拿整段比首尾）
        _k = min(len(base_prices), len(_ps))
        _bm = _robust_center(base_prices[:_k])
        _cm = _robust_center(_ps[:_k])
        _d = _cm - _bm
        card_neg = {"slug": _ws, "zh": _short(_ws), "stat": _s.get("en"),
                    "n": _k, "median": _cm, "delta": _d,
                    "pct": (_d / _bm * 100.0) if _bm > 0 else 0.0}
        break

    # ---- 卡面**自身**词条的边际溢价（2026-11-13 第31轮新口径）----
    # 旧「词条价差预估」= ``med + rec_neg.delta`` —— 那是「推荐你洗卡该选哪条负面」的
    # 溢价，**与用户手里这张卡无关**。这里改为逐条统计卡面词条的**同分位边际溢价**并求和：
    #   delta_i = median(pool_i 最低 k 条) − median(base 最低 k 条)，k = min(base, pool)
    # 与 ``card_neg`` / ``rec_neg`` 完全同一套同分位口径（零特例、不认武器）。
    # 字段方向按卡面符号取：``-`` → negative_stats，``+`` → positive_stats
    # （WFM 的 positive 指词条数值极性，不是「对玩家是否有利」；阿利乌的 +58.1% 后坐力
    #  在 WFM 里就是 positive，实测 positive_stats=recoil 有 15 条样本）。
    # 实测对照 Robot 阿利乌：base 400 + Damage 89.5 + Recoil 410 = **899.5**，Robot **972**。
    prem_rows, prem_skip = [], []
    prem_incomplete = False
    for _s in stats:
        _ws = _DE_TO_WFM.get(_s.get("en"))
        if not _ws:
            prem_skip.append(str(_s.get("en")))
            continue
        _field = "negative_stats" if _s.get("sign") == "-" else "positive_stats"
        _pool2 = None
        # 429 会让估价随机漂移（少算一条词条），必须退避重试；仍失败则标记 incomplete，
        # 展示层据此在脚注写明「N 条限流未计入」，**不允许静默少算**。
        for _attempt in range(3):
            try:
                _pool2 = await _fetch_auction_page(
                    slug, "price_asc", "&%s=%s" % (_field, _quote(_ws, safe="")),
                    allow_empty=True)
                break
            except RivenMarketError:
                if _attempt >= 2:
                    break
                await asyncio.sleep(3.0 * (_attempt + 1))
        if _pool2 is None:
            prem_skip.append("%s(限流/请求失败)" % _s.get("en"))
            prem_incomplete = True
            continue
        _p2 = _prices_of(_same_rank(_pool2, rank))
        if len(_p2) < _WFM_MIN_SAMPLES:
            prem_skip.append("%s(样本%d<3)" % (_s.get("en"), len(_p2)))
            continue
        _k2 = min(len(base_prices), len(_p2))
        _bm2 = _robust_center(base_prices[:_k2])
        _pm2 = _robust_center(_p2[:_k2])
        _d2 = _pm2 - _bm2
        prem_rows.append({"en": _s.get("en"), "slug": _ws, "zh": _short(_ws),
                          "field": _field, "n": _k2, "median": _pm2,
                          "delta": _d2,
                          "pct": (_d2 / _bm2 * 100.0) if _bm2 > 0 else 0.0})
    prem_delta = sum(r["delta"] for r in prem_rows)
    # 逐条中位累加本身就是「估计」，两条独立上限保护不做出荒谬估价：
    # 溢价不超过地板价的 4 倍（riven 高 roll 卡常见 2–3 倍，10 倍以上必是词条拼凑的伪和）。
    if prem_delta > med * 4.0:
        prem_delta = med * 4.0

    return {
        "status": "ok", "slug": slug, "ts": time.strftime("%Y-%m-%d %H:%M"),
        "source": source, "price_kind": "挂牌要价·非成交价",
        "rank": rank, "match_tier": match_tier, "pair_k": pair_k,
        # 兼容旧 auc 字段
        "n": n, "lo": lo, "hi": hi, "med": med, "zero": zero,
        "sample_n": len(pooled), "base": med,
        "rec_pos": [_short(ps) for ps in rec_pos],
        "rec_neg": {"slug": neg_slug, "zh": _short(neg_slug),
                    "n": pair_k, "pool_n": len(neg_prices), "median": neg_median,
                    "delta": delta, "pct": pct, "hot_n": neg_hot_n},
        # 卡面实际负面（None = 该卡无负面 / 词条未收录 WFM / 样本不足）
        "card_neg": card_neg,
        # 卡面自身词条溢价（rows 为空 = 无任何词条可测 → 展示层退回旧口径）
        "premium": {"rows": prem_rows, "delta": prem_delta,
                    "skipped": prem_skip, "incomplete": prem_incomplete,
                    "k_mode": "同分位最低 k 条中位差"},
        "exact": exact,
        # 显式标记：最贵视图的价格绝不参与计算（联调门禁会断言 + 离群反例）
        "desc_price_used": False,
    }


def market_error_bundle(exc: BaseException) -> dict:
    """把失败**显式**编码进卡片数据：卡面显示原因，不回退静态权重、不写「暂无」。

    失败分两类，source 文案必须如实区分，避免误导用户以为接口挂了：

      * **领域拒绝** ``RivenMarketError``：请求已发出并拿到 WFM 响应，但样本不足 /
        武器 slug 解析失败，我们**主动拒绝估算** → 标「数据不足，已拒绝估算」。
        （例：phantasma rank8 仅 2 条挂单 < 阈值 3，属此类，不是网络问题。）
      * **真网络/请求异常**（连接超时、断连、429 等 aiohttp/asyncio 原生异常）：
        → 标「请求失败」。
    """
    if isinstance(exc, RivenMarketError):
        src_fail = "数据不足，已拒绝估算"
    else:
        src_fail = "请求失败"
    return {
        "status": "error",
        "error": "%s: %s" % (type(exc).__name__, exc),
        "ts": time.strftime("%Y-%m-%d %H:%M"),
        "source": "WM v1 /auctions/search（%s）" % src_fail,
        "price_kind": "挂牌要价·非成交价", "rank": None, "match_tier": "不可用",
        "pair_k": 0, "n": 0, "lo": 0.0, "hi": 0.0, "med": 0.0, "zero": None,
        "sample_n": 0, "base": 0.0, "rec_pos": [], "rec_neg": None,
        "exact": {"status": "unknown", "n": 0, "median": None,
                  "reason": "市场数据获取失败，无法精确匹配"},
        "desc_price_used": False,
    }


# 2026-11-04 23:0x **删除** ``_market_lines()``：它按旧口径拼「推荐负面 XX
# +dp/+p%（高价挂牌 N 条）」，把最贵 100 条的价格写进溢价。函数已无任何调用方，
# 留着会被照抄。文案统一由 build_card() / build_card_data() 从 bundle 现算。


def _conf_grade(n: int) -> str:
    return "A" if n >= 10 else "B" if n >= 6 else "C" if n >= 5 else "D"


# ---------------------------------------------------------------------------
# 评分与推荐（伤害维度 = 模拟；市场维度 = 启发式权重；大佬维度如实「暂无」）
# ---------------------------------------------------------------------------
def _letter(score: float) -> str:
    """总分 → 字母档。刻度按 Rabbit 参考卡**可见字母**反推（第27轮，非自拟）：

    * 阿利乌参考卡「伤害 62.5 (A)」→ A 档必须覆盖 62.5（旧阈值 65 会把它打成 B）；
    * 同卡「市场 85.5 (S+)」→ 存在 S+ 档且门槛 ≤85.5（旧实现最高只有 S，显示成 S 而非 S+）。

    故取 S+ ≥85 / S ≥75 / A ≥60 / B ≥50 / C ≥35 / D ≥20 / E <20。E 下界由「低 roll 卡
    伤害 10.1 / 11.0 均无字母争议」约束，保持 <20 不变。
    """
    if score >= 85:
        return "S+"
    if score >= 75:
        return "S"
    if score >= 60:
        return "A"
    if score >= 50:
        return "B"
    if score >= 35:
        return "C"
    if score >= 20:
        return "D"
    return "E"


def _grade(d: float) -> str:
    """评级字母：入参 ``d`` = 卡面打印的偏差（%），先换算「区间百分位」再分档。

    理论区间恒为标准值 ±10%，故 ``p = (10 + d) / 20``。

    刻度由**参考机器人两张卡共 10 个数据点**反推（2026-11-02）：
        +5.728% → A      +5.185% → A−     +6.521% → A
        +3.542% → A−     +3.901% → A−     +1.677% → B+
        −0.988% → B      −2.914% → B−
        综合 +3.910% → A−                综合 +1.753% → B+
    因此档位边界（p）为 .30/.42/.54/.66/.78（各 0.12 宽）+ A+ .90 + S .96，
    10 个点全部命中。

    ⚠️ 注意参考卡的分档只吃**偏差幅度**、不看词条正负——所以负词条惩罚越大
    （|偏差| 越大）反而评级越好。这是参考机器人自身的逻辑缺陷，此处**照抄以保持
    同一张卡两边字母一致**；进度条同样用幅度，卡内自洽。若哪天要改成方向敏感，
    只需把调用处传入 ``-d``（负词条）即可，档位表不用动。
    """
    p = (10.0 + float(d)) / 20.0
    if p >= 0.96:
        return "S"
    if p >= 0.90:
        return "A+"
    if p >= 0.78:
        return "A"
    if p >= 0.66:
        return "A-"
    if p >= 0.54:
        return "B+"
    if p >= 0.42:
        return "B"
    if p >= 0.30:
        return "B-"
    if p >= 0.18:
        return "C+"
    if p >= 0.08:
        return "C"
    return "D"


#: 负词条 → 武器面板 damage 字段名（用于按**该武器实际伤害构成**缩放惩罚）。
#: 为什么必须按武器缩放（2026-11-12 用户实卡 Ocucor 反推）：旧实现用武器无关常数
#: ``(100-NEG_MARKET)*0.44``，把 ``-111.4% 切割`` 判成重罚 32.56 分；但 Ocucor 面板
#: ``slash 1 / total 11 = 9.09%``，去掉该负词条 DPS 仅 3880.7→4429.1（代价 12.38%）。
#: Warframe Robot 对同卡反而标「切割 +24.9P / +7%」（免费负面甚至微溢价）——方向性错误。
_NEG_DMG_STAT = {
    "Impact Damage": "impact", "Puncture Damage": "puncture",
    "Slash Damage": "slash", "Heat Damage": "heat", "Cold Damage": "cold",
    "Electricity Damage": "electricity", "Toxin Damage": "toxin",
    "Viral Damage": "viral", "Corrosive Damage": "corrosive",
    "Radiation Damage": "radiation", "Magnetic Damage": "magnetic",
    "Blast Damage": "blast", "Gas Damage": "gas",
}


#: 暴击类负词条 → 权重取「该武器暴击对持续 DPS 的贡献占比」。
#: 只收**基础**暴击两项：``Critical Chance for Slide Attack``（滑砍暴击）是另一回事
#: （只在滑砍时生效，占比不可由面板直接推），必须留原常数，否则会动到 Coda 近战金标准。
_NEG_CRIT_STAT = ("Critical Chance", "Critical Damage")


def _crit_share(parsed: dict, w: dict) -> float:
    """暴击对当前武器 DPS 的贡献占比 = 1 − 1/暴击期望倍率（标准配卡口径）。

    用于给 ``-暴击几率 / -暴击伤害`` 定权。低暴击武器（如 Verglas 基础 8%）上
    ``-135.3% 暴击几率`` 实战近乎无害，旧实现按武器无关常数满额扣 40.48 分，
    把一张三正词条卡的市场分直接压到 0（用户实卡 2026-11-12 Plaga-visitox 实测）。
    与 ``_NEG_DMG_STAT`` 同一思路：负面有多毒，取决于**这把武器**靠不靠它。
    """
    if not w:
        return 1.0
    std = _std_bucket(parsed, w)
    cc = max(0.0, float(w.get("criticalChance") or 0) * (1 + std["cc"]))
    cm = max(1.0, float(w.get("criticalMultiplier") or 1) * (1 + std["cm"]))
    full = _crit_mult(cc, cm)
    if full <= 1.0:
        return 0.0        # 完全不吃暴击的武器（cc=0）→ -暴击是免费负面
    return max(0.0, min(1.0, (full - 1.0) / full))


def _neg_weight(parsed: dict, s: dict) -> float:
    """负伤害词条在**当前武器**上的权重 0~1 = 该项面板伤害占比。

    * 有面板且该项占比 > 0 → 按占比（如 Ocucor 的切割 9.09%）；
    * 有面板但**该项为 0**（纯元素武器上的 ``-切割`` 等）→ 0.0，
      即「这条负面打不到东西」，不扣市场分；
    * 暴击类（``Critical Chance`` / ``Critical Damage``）→ 按**暴击贡献占比**
      （``_crit_share``）：低暴击武器上 -暴击几乎免费，高暴击武器上才是致命负；
    * **无面板**（Zaw/Kitgun/Archgun、未收录武器）→ 1.0 保守沿用原常数：
      「不知道伤害构成」不等于「没有影响」，不能凭未知免罚；
    * 其余非伤害类词条（射速/触发/变焦等）→ 1.0，维持原常数（与伤害构成无关）。
    """
    en = s.get("en")
    w = _weapon_raw(parsed.get("weapon") or "") or {}
    if en in _NEG_CRIT_STAT:
        return _crit_share(parsed, w) if w else 1.0
    key = _NEG_DMG_STAT.get(en)
    if not key:
        return 1.0
    dmg = w.get("damage") or {}
    total = float(dmg.get("total") or 0)
    if total <= 0:
        return 1.0
    return max(0.0, min(1.0, float(dmg.get(key) or 0) / total))


def _ceil_label(ceil_src: str | None, prefix: str = "") -> str:
    """分母口径的**诚实标签**（2026-11-13 第30轮，文字卡与图片卡共用同一份）。

    `gain_max` / `ke_best` 的分母有两种来路（`_sim` 的 `ceil_src`）：
      * ``calib``   → 理论最优词条组合 × 标定系数 = 真正的「最强紫卡」；
      * ``cur_max`` → 该卡同词条洗满已超刻度，分母被抬到它自己 = 「同词条洗满」；
      * ``cur``     → 连当前增益都超刻度，分母 = 当前卡本身（分数会被顶 100）。
    旧实现一律写「最强紫卡」，于是 `cur_max` 分支把「这张卡洗到满」冒充成
    「该武器最强紫卡」——冰凇卡实测差 2.5 倍（同词条洗满 +81.5% vs 理论最优 +200.0%）。
    宁可标签长一点，也不许把口径说大。
    """
    if ceil_src in (None, "calib"):
        return prefix + "最强紫卡 "
    if ceil_src == "cur_max":
        return prefix + "同词条洗满 "
    return prefix + "当前卡自身 "


#: 市场分「词条类型地板」缓存：{(紫卡类型, 正词条数): 该武器可洗组合 Σw 的中位数}。
#: 组合数最大 C(30,3)=4060，按类型只算一次；全量 623 把扫描时不会重复展开。
_MKT_FLOOR_CACHE: dict[tuple[str, int], float] = {}
#: 不进地板统计的词条：阵营伤。`STAT_MARKET` 给了 96（与基伤 86 同级），实测若留在池里
#: 会把中位地板从 115 抬到 132（Rifle 3 正档）—— 池里只 3/38 个键，却每个都压在权重顶格，
#: 直接把中位数拉高，使「地板」不再代表普通可洗组合。剔除后地板回到 115。
#: 与 `_recs` 的候选排除口径一致；分子侧同口径见 `_mkt_wstat`。
_MKT_FLOOR_EXCLUDE = frozenset({
    "Damage to Grineer", "Damage vs Grineer", "Damage to Corpus", "Damage vs Corpus",
    "Damage to Infested", "Damage vs Infested",
})


def _mkt_wstat(en: str) -> float:
    """市场权重（与地板池**同一权重空间**）。

    2026-11-13 第30轮补：`_market_type_floor` 已把阵营伤从组合池剔除（见其上注释），
    但 `_scores` 的分子仍按 `STAT_MARKET` 的 96 计权 ⇒ `wsum / floor` 成了**不同单位
    之比**。构造反证（Rifle / 冰凇，全部洗满）：

        * `对Grineer伤害 + 变焦 + 弹药最大值` → wsum=110 ≥ 地板 115×0.957 → **95.65**
        * `对Grineer伤害 + 变焦 + 基伤`     → wsum=188 → **100.00**

    两张废卡拿满分，与「垃圾组合只值零洗价」直接冲突。故分子按同一口径把 `_MKT_FLOOR_EXCLUDE`
    的权重记 0。**注意这里不动 `STAT_MARKET` 的 96 本身** —— 那是有文档记录的「派系卡溢价」
    判断，可在其它维度（卡面推荐排序）继续生效；本条只修「地板折算」这一步的口径统一。

    正词条**槽位计数**不变（`len(pos)` 仍含阵营伤）：死槽位照样占一次洗练名额，因此该卡
    不可能优于同槽位数的正常 3 正卡，地板仍取 3 槽档，属保守侧。
    """
    if en in _MKT_FLOOR_EXCLUDE:
        return 0.0
    return float(STAT_MARKET.get(en, 30))


def _market_type_floor(parsed: dict, n_pos: int) -> float:
    """该武器「n 条正词条」全部可洗组合的 Σw 中位数 —— 市场分的**类型地板**。

    用途见 `_scores` 里 type_factor 的注释：加权平均 roll 在 p→1 时恒等 100，
    词条类型完全不参与，「变焦+弹药最大值+后坐力」洗满也拿 100 分。以中位组合为
    地板后，组合价值低于中位的按比例折价，中位以上（含三张 Rabbit 金标准卡与冰凇）
    一律 1.0，不动已标定结果。
    """
    t = (parsed.get("type") or "").strip()
    key = (t, int(n_pos))
    if key in _MKT_FLOOR_CACHE:
        return _MKT_FLOOR_CACHE[key]
    _, base_tbl, _ = _load_riven_tables()
    keys = [k for k in (base_tbl.get(t) or {}) if k not in _MKT_FLOOR_EXCLUDE]
    val = 0.0
    if keys and 1 <= n_pos <= len(keys):
        sums = sorted(sum(float(STAT_MARKET.get(k, 30)) for k in combo)
                      for combo in itertools.combinations(keys, n_pos))
        if sums:
            val = float(statistics.median(sums))
    _MKT_FLOOR_CACHE[key] = val
    return val


def _scores(parsed: dict, sim: dict) -> dict:
    stats = parsed["stats"]
    # 市场正负按**布局正负**分类（_layout_curse），而非卡面符号：武器后坐力卡面显 + 但
    # 属布局诅咒（越低越好），Rabbit 市场页将其当负面计；按卡面符号会把诅咒误算成正向，
    # 抬高市场分（实测阿利乌双枪 94.4 vs Rabbit 85.5）。武器无关、零写死。
    pos = [s for s in stats if not _layout_curse(s)]
    neg = [s for s in stats if _layout_curse(s)]
    gain = sim.get("gain")
    if gain is None:
        gain = sim["dps_cur"] / sim["dps_base"] - 1.0 if sim["dps_base"] > 0 else 0.0
    gain_max = sim.get("gain_max")
    if gain_max is None:
        gain_max = sim["dps_best"] / sim["dps_base"] - 1.0 if sim["dps_base"] > 0 else 0.0
    dmg_score = gain / gain_max * 100.0 if gain_max > 0 else 0.0
    dmg_score = max(0.0, min(100.0, dmg_score))
    # ---- 双维分解（2026-11-12 用户裁定「双维展示」）----
    # 伤害分是**刻度分**：分母可能被当前卡击穿（ceil_src = cur_max / cur），此时分数退化成
    # 「roll 完成度」，看单个数字无法区分「词条类型选得对」与「洗得满」。故恒给出两个分量：
    #   潜力 pot  = 当前增益 ÷ 该武器**理论最优组合**增益 → 词条**类型**上限的占比（分母卡无关）
    #   roll      = 当前增益 ÷ **同词条洗满**增益 → roll 完成度（只反映洗得好不好）
    # 与主分的关系（恒等式，勿写成"两者之积"）：
    #   dmg = gain / max(gbest×calib, gcmax) = **min(pot/calib, roll)**
    # 即「潜力按刻度折算」与「roll 完成度」取小：calib 分支下 dmg = pot/calib（阿利乌
    # 18.7/0.30 = 62.3 ≈ 62.2）；刻度被击穿时 dmg = roll（Ocucor 91.0 = roll）。
    # g_best_raw 只在无面板/真表缺失时为 0 → 潜力如实置 None，不填 0 冒充。
    _gbest = float(sim.get("g_best_raw") or 0.0)
    _gcmax = float(sim.get("g_cur_max") or 0.0)
    pot = min(100.0, max(0.0, gain / _gbest * 100.0)) if _gbest > 0 else None
    roll = min(100.0, max(0.0, gain / _gcmax * 100.0)) if _gcmax > 0 else None
    # 市场分（Rabbit 反推，2026-11-12 重写）：**STAT_MARKET 加权平均 roll 完成度 p（无地板）**。
    # 权重反映词条市场价值，弱词条（冲击/重击效率）不会像纯 max(p) 那样被忽略。实测两卡：
    #   * 阿利乌高 roll: (86×0.948 + 30×0.597)/116 ≈ 85.7 ≈ Rabbit 85.5
    #   * Coda 近战: 招牌暴击 p 高但冲击/重击弱词条拖低 → ≈62 ≈ Rabbit 62.1
    #   * 低 roll: 全跌破区间下限 p=0 → 0.0（Rabbit 0.0）
    # 旧 0.6 地板会让低 roll 保底 60%，已废除；后坐力等 QoL 负词条不再额外扣分
    # （加权平均本身已不把它计入正向，无需重复罚；旧公式的 recoi 罚是地板 bug 的补丁）。
    wsum = wsum_roll = 0.0
    for s in pos:
        # 2026-11-13 第30轮：权重走 `_mkt_wstat`（阵营伤记 0），与地板池同口径。
        # 旧写法 `float(STAT_MARKET.get(s["en"], 30))` 会把 96 权重的阵营伤算进分子，
        # 使「对Grineer伤害+变焦+弹药最大值」洗满得 95.65 —— 与地板池剔除阵营伤的
        # 决定自相矛盾，详见 `_mkt_wstat` docstring 的反证数据。
        mw = _mkt_wstat(s["en"])
        rng = s.get("_rng")
        p = 0.0
        if rng and rng[1] > rng[0]:
            v = s["value"] - 1.0 if s["unit"] == "x" else s["value"]
            p = min(1.0, max(0.0, (v - rng[0]) / (rng[1] - rng[0])))
        wsum += mw
        wsum_roll += mw * p
    # 「词条类型」地板（2026-11-13 第30轮）：上面的加权平均在 **p→1 时恒等于 100**，
    # 也就是说「全部洗满」时**词条类型完全不参与**——构造反证实测：`变焦+弹药最大值+
    # 武器后坐力`（负弹速）洗满得 **100.00**，与 `基伤+多重+暴伤` 洗满的 **100.00 完全一样**。
    # 这在真实市场里是错的：垃圾组合只值零洗价。
    # 修法：以「该武器可洗词条组合的 Σw 中位数」为地板，低于地板的组合按比例折价
    # （中位以上一律 1.0，因此**不动**已标定的阿利乌/Coda/Ocucor/冰凇四张卡）。
    # 2026-11-13 补充：分子必须与地板池**同权重空间**（阵营伤记 0，见 `_mkt_wstat`），
    # 否则 `wsum / floor` 单位不一致，废卡仍能拿 95.65~100。
    _floor = _market_type_floor(parsed, len(pos))
    type_factor = min(1.0, wsum / _floor) if _floor > 0 else 1.0
    mkt = wsum_roll / wsum * 100.0 * type_factor if wsum else 0.0
    for s in neg:
        if s["en"] in _MARKET_QOL_NEG:      # QoL 负词条不伤市场价值（后坐/变焦/弹药等）
            continue
        # 伤害类负词条按 NEG_MARKET 扣分：系数 0.44（由 Coda 近战 -109% 滑砍暴击反推，
        # 使市场 62.1 命中；阿利乌无伤害类负词条，不受影响，仍 85.7）。
        # 2026-11-12：再乘 ``_neg_weight``（该词条在**本武器面板**的伤害占比）——
        # 武器无关常数会把 Ocucor 的 ``-111.4% 切割``（面板占比 9.09%）判成重罚。
        mkt -= (100.0 - float(NEG_MARKET.get(s["en"], 20))) * 0.44 * _neg_weight(parsed, s)
    mkt = max(0.0, min(100.0, mkt))
    friendly = all(NEG_MARKET.get(s["en"], 20) >= 60 for s in neg)
    # 综合评分口径（2026-11-11 全量对齐 Rabbit）：由两张参考卡反推 `综合 = max(伤害, 市场)`：
    #   * 图2 Coda Motovore  伤害11.0 / 市场62.1 / 综合62.1 (=市场)
    #   * 阿利乌 reference_sample 伤害10.1 / 市场0.0 / 综合10.1 (=伤害)
    # 旧 `total = dmg_score` 恒等于伤害分，市场分再高也带不动综合——两张参考卡均不符。
    total = max(dmg_score, mkt)
    return {"gain": gain, "gain_max": gain_max, "dmg": dmg_score,
            "mkt": mkt, "type": "市场卡" if friendly else "伤害卡",
            "total": total,
            # 伤害分分母由谁决定（calib=武器常数 / cur_max / cur=刻度被该卡击穿），
            # 展示层据此如实标注「刻度压平」，避免用户把 90+ 误读成接近神卡。
            "ceil_src": sim.get("ceil_src"),
            # 双维分量：潜力（占理论最优 %）/ roll（同词条洗满 %）。None=无面板不可算。
            "pot": pot, "roll": roll,
            "g_best": _gbest, "g_cur_max": _gcmax,
            # panel=False → 武器无 DPS 面板（自制 Zaw/Kitgun 模块化、Archgun 缺数据），
            # 伤害分/增益不可用，仅市场分有效。渲染层据此显示「伤害评分不可用」。
            "panel": float(sim.get("dps_base") or 0) > 0}


def _recs(parsed: dict, w: dict) -> dict:
    """推荐洗卡 / 负面 / 致命负（伤害维度 = 模拟边际；市场维度 = 启发式权重）。"""
    _, base_tbl, _ = _load_riven_tables()
    cand = [e for e in (base_tbl.get(parsed["type"]) or {})
            if e not in ("Damage to Grineer", "Damage vs Grineer",
                         "Damage to Corpus", "Damage vs Corpus",
                         "Damage to Infested", "Damage vs Infested")]

    def _marginal(en: str) -> float:
        """该词条 +100% 对标准配卡 DPS 的边际贡献（排序启发）。"""
        if not w:
            return 0.0
        b = _std_bucket(parsed, w)
        _apply_riven(b, [{"en": en, "value": 100.0, "unit": "%", "sign": "+"}])
        return _dps(w, b)

    cand_dps = sorted(cand, key=_marginal, reverse=True)
    rec_pos = " + ".join(_stat_zh(e) for e in cand_dps[:3]) or "暂无"
    #: 零影响负词条优先级（用词与顺序对齐参考卡：变焦/弹药最大值/武器后坐力）
    _NEG_PRIORITY = ["Zoom", "Ammo Maximum", "Weapon Recoil", "Status Duration",
                     "Projectile Speed", "Magazine Capacity", "Combo Duration",
                     "Initial Combo", "Damage to Grineer", "Damage to Corpus",
                     "Damage to Infested"]
    zero_neg = [e for e in _NEG_PRIORITY
                if e in cand and NEG_DPS_IMPACT.get(e, 10.0) <= 0.5]
    rec_neg = "/".join(_neg_zh(e) for e in zero_neg[:3]) or "暂无"
    deadly = sorted((e for e in cand if NEG_DPS_IMPACT.get(e, 10.0) >= 9.0),
                    key=lambda e: -NEG_DPS_IMPACT.get(e, 10.0))
    rec_deadly = "/".join(_neg_zh(e) for e in deadly[:3]) or "暂无"
    mkt_pos = sorted(cand, key=lambda e: -float(STAT_MARKET.get(e, 20)))
    return {"pos": rec_pos, "neg": rec_neg, "deadly": rec_deadly,
            "mkt_pos": [_short_stat(e) for e in mkt_pos[:3]]}


# 2026-11-04 22:25 **删除** ``_market_tiers()``：它按 STAT_MARKET/NEG_MARKET 静态
# 热度分产出「T0：…(热80)」，曾在 build_card() 里冒充「基于市场价差」写进推荐词条。
# 静态分不是市场数据，也没有检查能拦住它被再次接回卡面，所以按 prove-it「让它没
# 地方犯」直接删掉实现，而不是留着不调用。
# 真实市场推荐的唯一入口：fetch_market_bundle() → fetch_market_for_card() → recs。
# STAT_MARKET/NEG_MARKET 仍仅供 _scores() 的启发式「市场维度评分」，不得用于卡面
# 的价格、推荐洗卡、推荐负面、市场参考四行。


# ---------------------------------------------------------------------------
# 卡片组装（版式/文案逐字对齐参考卡）
# ---------------------------------------------------------------------------
def _dots(disp: float) -> str:
    return ""  # 旧分档实现已删除，任何调用点都拿不到星级点阵


def _fit_disp(parsed: dict, fix: dict) -> float | None:
    """由卡面词条反推倾向（通用兜底，**不特化任何武器**）。

    每条词条给出倾向可行区间 [v/(|base|·F·1.1), v/(|base|·F·0.9)]，
    真实倾向必须同时落在**所有**词条的区间内。当倾向表值不在 ≥2 条
    区间的交集中（倾向数据过期 / 变体倾向不同 / 表错行），取交集内
    中点替代；交集为空或词条不足时返回 None（维持表值）。

    实测案例（Nukor 卡，2026-10）：表值 1.45 时暴击/派系/弹匣三条词条
    全部落在理论区间外；四条词条区间交集 [0.498, 0.505] 非空 → 反推
    倾向 ≈0.50（恰为 Kuva Nukor 的 DE 官方 omegaAttenuation），
    四条词条全部自然落入区间，无需任何数值"修复"。
    """
    _, base_tbl, _ = _load_riven_tables()
    t = base_tbl.get(parsed["type"]) or {}
    omega = float(parsed.get("disp") or 1.0)
    ivs: list[tuple[float, float]] = []
    for s in parsed["stats"]:
        base = t.get(s["en"])
        if base is None and " vs " in s["en"]:
            base = t.get(s["en"].replace(" vs ", " to "))
        if base is None and " to " in s["en"]:
            base = t.get(s["en"].replace(" to ", " vs "))
        if not base:
            continue
        v = (s["value"] - 1.0) if s["unit"] == "x" else abs(s["value"])
        f = abs(fix["curse"]) if _layout_curse(s) else fix["buff"]
        bf = abs(base) * f
        if bf <= 0:
            continue
        ivs.append((v / (bf * 1.1), v / (bf * 0.9)))
    if len(ivs) < 2:
        return None
    lo, hi = max(x[0] for x in ivs), min(x[1] for x in ivs)
    if lo > hi or lo <= 0:
        return None
    if lo <= omega <= hi:
        return None  # 表值本就自洽 → 不动
    return (lo + hi) / 2.0


#: base 值 ≥ 此阈值 → 该词条是百分比类（秒/倍率类词条的 base 都远小于 20）
_PCT_BASE_MIN = 20.0


def _normalize_stats(parsed: dict, fix: dict) -> None:
    """词条值规范化 —— ``build_card``（文字卡）与 ``build_card_data``（图片卡）共用。

    两条输出路径必须走同一套规范化，否则「同一张卡，文字版和图片版数字不一样」。

    只做两件事：
      1. **单位推断**：OCR 常整行丢掉「%」，使 65.3 变成"非百分比"。若该词条在官方
         base 表里的基础值 ≥20（百分比词条都是几十到几百；秒/倍率词条远小于此），
         补成 ``%``。
      2. 预算好 ``_rng`` 缓存（后续多处复用）。

    ⚠️ **不在这里按理论区间修数值**。历史版本会「值低于区间下界就 ×10 / 补首位」，
    但第三方语料实测：**未满级卡**（Rank 0~3）与**旧倾向卡**的真实数值天然远低于
    「1.2 倾向 + 满级」区间——实测 73.1% 暴击几率被"修"成 173.1%，11.1% 被改成 111%。
    数值可信度改由 ``riven._refine_values`` 用**卡内共识尺度**判定（不看区间）。
    """
    _, base_tbl, _ = _load_riven_tables()
    tbl = base_tbl.get(parsed.get("type") or "") or {}
    for s in parsed["stats"]:
        if s["unit"] == "":
            _base = abs(tbl.get(s["en"]) or tbl.get(s["en"].replace(" vs ", " to ")) or 0)
            if _base >= _PCT_BASE_MIN:
                logger.info(f"riven_card_unit_infer: {s['en']} '' -> '%' (base={_base:.1f})")
                s["unit"] = "%"
        s.setdefault("_rng", _range_of(s, parsed, fix))


def build_card(parsed: dict, market: dict) -> str:
    """紫卡分析卡（guide 标记文本，``_send_guide`` 渲染）。

    ``market`` **必填**，且必须是 :func:`fetch_market_for_card` 的返回值
    （成功态 ``status=ok`` 来自真实 WFM 拍卖；失败态 ``status=error`` 带显式原因）。

    2026-11-04 22:25 修复（本文件「市场」段 + 本函数）：旧签名 ``auc=None`` 允许
    调用方完全不传市场数据，卡面就永远写「WM 拍卖样本不足，暂无」——正是用户
    截图里的现象。现在漏传直接 :class:`RivenMarketError`，且市场行/推荐词条一律
    用真实挂牌数据；失败时把**错误原因**写在卡面上，不再用 ``STAT_MARKET`` 热度分
    或「词条价差预估价」冒充市场价。
    """
    if not isinstance(market, dict) or not market.get("status"):
        raise RivenMarketError(
            "build_card 需要 fetch_market_for_card() 的 market dict（含 status），收到 %r"
            % (market,))
    mkt = market
    _load_riven_tables()
    stats = parsed["stats"]
    fix = _fix_of(parsed)
    # 倾向自洽校验（通用规则）：词条与倾向表值矛盾时，以卡面词条反推倾向。
    # 放在最前——后续 _rng / ocr_val_fix / 模拟 / 评分全部依赖 parsed["disp"]。
    _fitted = _fit_disp(parsed, fix)
    if _fitted is not None:
        logger.warning(f"riven_card_disp_fit: {parsed.get('weapon')} disp "
                       f"{parsed.get('disp')} -> {_fitted:.3f} (表值与卡面词条矛盾)")
        # 反推倾向会改动区间/偏差/评分，必须在卡面上显式告知——不能静默替换官方数据。
        parsed["_disp_fitted"] = (float(parsed.get("disp") or 1.0), _fitted)
        parsed["disp"] = _fitted
    _normalize_stats(parsed, fix)
    w = _weapon_raw(parsed["weapon"]) or {}
    parsed["_w"] = w
    sim = _sim(parsed, w) if w.get("damage") else None
    sc = _scores(parsed, sim) if sim else None
    rec = _recs(parsed, w) if sim else None
    weapon_zh = parsed.get("weapon_zh") or parsed["weapon"]
    rname = parsed.get("riven_name") or "-"
    rank = _eff_rank(parsed)          # 反推等级（OCR 徽章不可信，见 _eff_rank）
    omega = float(parsed.get("disp") or 1.0)

    L: list[str] = []
    L.append("@ttl|紫卡分析|search")
    L.append("@sub|你的紫卡详细分析数据如下：")
    L.append(f"【{weapon_zh} {rname}】 等级:{rank if rank is not None else '?'} "
             f"倍率:{omega:.2f}(全武器统一)")
    if parsed.get("_disp_fitted"):
        _o, _n = parsed["_disp_fitted"]
        L.append(f"@alert|*注意：卡面词条与统一倍率 {_o:.2f} 对不上，已按卡面反推 "
                 f"{_n:.2f} 计算。原因通常是「旧倾向下洗出的老卡」或「未满级卡」"
                 f"（本图未读到有效等级徽章），两者都会让数值整体偏离 1.2/满级区间。")
    # ---- 词条行：+104.5% 暴击伤害 (91.0%-111.4%|高2.18%) ----
    for s in stats:
        if s["unit"] == "x":
            # 派系伤害：卡面显示为倍率（x1.19），无正负号（负向 = 倍率 <1）
            val = f"x{s['value']:.2f}"
        else:
            val = f"{'-' if s['sign'] == '-' else '+'}{_fmt_val(s)}"
        rng = s.get("_rng")
        if rng:
            lo, hi = rng
            mid = _mid(s, parsed, fix)
            rng_txt = _fmt_range(lo, hi, s["unit"], s["sign"] == "-")
            # 统一到"加成量"空间：x 类词条的 value 是倍率（1.19 = +0.19），
            # 而范围表/中值都是加成空间——直接拿倍率比会得出「高92%」式荒谬偏差
            _v = s["value"] - 1.0 if s["unit"] == "x" else s["value"]
            _signed = -_v if s["sign"] == "-" else _v
            dev = _dev_text(_signed, mid or lo)
            L.append(f"@rstat|{val}|{s['zh']}|({rng_txt}|{dev})")
        else:
            L.append(f"@rstat|{val}|{s['zh']}|(无范围表)")
    L.append("@foot|*提示：高低值上下限为±11%，越靠近11%越极端。")
    # ---- 综合评级（参考卡口径：各词条**卡面偏差**的算术均值） ----
    # 参考卡实测：伯斯顿 (+3.542+3.901+6.521+1.677)/4 = +3.910% → A−
    #             玻之武杖 (+5.728+5.185-2.914-0.988)/4 = +1.753% → B+
    # 所以既不是「绝对偏差均值」也不是「方向敏感均值」，而是**打印值直接求平均**。
    _devs: list[float] = []
    for s in stats:
        rng = s.get("_rng")
        if not rng:
            continue
        _midv = _mid(s, parsed, fix)
        if not _midv:
            continue
        _v = s["value"] - 1.0 if s["unit"] == "x" else s["value"]
        _devs.append((abs(_v) / _midv - 1.0) * 100.0)
    _overall = sum(_devs) / len(_devs) if _devs else 0.0
    L.append(f"@score|·紫卡综合评分 - |{_overall:.3f}({_grade(_overall)})"
             f"| - {parsed['type']}系（{len(stats)}词条偏差均值）")
    # ---- 分类评分 / 每词条贡献（伤害模拟 + 市场权重，DPS 面板存在时才有） ----
    if sc and sim:
        # 🔴 2026-11-13 第31轮：无面板的**只剩 Kitgun 6 把**（Zaw 已靠 attacks[0] 回填、
        # Archgun 已靠 Arch-Gun.json 补齐）。剩下这批是 WFCD 真不发布膛室伤害
        # （上游 Misc.json 全是占位零值），文案必须说「模块化、需按你的组装实测」，
        # **不能写「面板缺失」**——那是把数据源的限制说成自家 bug。
        if sc.get("panel", True):
            dmg_txt = f"{sc['dmg']:.1f} √" if sc["dmg"] >= 60 else f"{sc['dmg']:.1f}"
            L.append(f"分类评分：大佬 暂无 | 伤害 {dmg_txt} | 市场 {sc['mkt']:.1f}")
            # 双维展示（2026-11-12 用户裁定）：伤害分 = 潜力 × roll。刻度被击穿时主分只是
            # roll 完成度，故把两个分母都写出来，让「词条类型对不对」和「洗得满不满」可分。
            _cs = sc.get("ceil_src")
            _pot, _roll = sc.get("pot"), sc.get("roll")
            _dim = ""
            if _pot is not None and _roll is not None:
                # 分隔用「·」不用「×」：真实关系是 min(潜力/calib, roll)，写成相乘就错了。
                _dim = (f"（潜力 {_pot:.1f}% · roll {_roll:.1f}%）")
            L.append(f"伤害计算：配装增益 {sc['gain'] * 100:+.1f}%｜"
                     f"伤害分 {sc['gain'] * 100:.1f}/{sc['gain_max'] * 100:.1f}"
                     f"={sc['dmg']:.1f}%{_dim}")
            if _pot is not None and _roll is not None:
                _base = (f"伤害基准：理论最优组合 {sc['g_best'] * 100:+.1f}%"
                         f"（潜力分母）｜同词条洗满 {sc['g_cur_max'] * 100:+.1f}%（roll 分母）")
                if _cs == "cur_max":
                    _base += "｜⚠ 刻度压平：主分分母已退化为「同词条洗满」，只反映 roll"
                elif _cs == "cur":
                    _base += "｜⚠ 已顶刻度上限 100（当前增益超过标定刻度）"
                L.append(_base)
        else:
            L.append(f"分类评分：大佬 暂无 | 伤害 不可用(模块化无固定面板) | 市场 {sc['mkt']:.1f}")
            L.append("伤害计算：该武器为模块化武器（膛室+握把+装填器自由组合），"
                     "官方不发布组装后的固定伤害面板，伤害评分不可用；"
                     "词条区间/偏差仍按紫卡基线精确计算。")
        for s in stats:
            key = s["en"] + ("-" if s["sign"] == "-" else "+")
            c = sim["contrib"].get(key, 0.0)
            mkt_v = (100.0 - float(NEG_MARKET.get(s["en"], 20))) if s["sign"] == "-" \
                else float(STAT_MARKET.get(s["en"], 30))
            rng = s.get("_rng")
            _v = s["value"] - 1.0 if s["unit"] == "x" else s["value"]
            _signed = -_v if s["sign"] == "-" else _v
            ptxt = (f" 数值{_percentile(_signed, rng[0], rng[1], s['sign'] == '-'):.1f}%"
                    if rng else "")
            # 未进 DPS 乘区的词条（异常状态伤害/触发几率/穿透/变焦…）**不许显示 +0.0%**：
            # 那读起来像「这条词条没用」。实测反例（用户实卡 2026-11-12 冰凇 Verglas）：
            # `异常状态伤害 +111.3%` 洗到 99.6% 分位，却因模型无 DoT 乘区而显示 +0.0%，
            # 用户会直接判定程序算错。此处改用「—(未计入)」如实说明是**本模型不含**该项。
            _modeled = s["en"] in _DPS_STAT_KEYS
            _dtxt = f"{c:+.1f}%" if _modeled else "—(未计入)"
            L.append(f"@chipline|{s['zh']}|大佬暂无 | 伤害{_dtxt}{ptxt} | 市场+{mkt_v:.1f}%")
        L.append("@foot|*伤害：配卡不变、只去掉该词条时击杀效率的下降；词条之间有协同，"
                 "各项相加不等于总增益。标注「未计入」的词条不在当前伤害模型的乘区内"
                 "（如异常状态伤害/触发几率/穿透），不代表它没有价值。")
    else:
        L.append("@score|·伤害/市场评分 - |暂无| - 武器面板缺失")
    # ---- 市场（2026-11-04 23:0x 修订口径：普通挂牌中位数，最贵视图不计价） ----
    ok = mkt.get("status") == "ok"
    if ok:
        L.append(f"价格预计：{mkt['lo']:.0f}~{mkt['hi']:.0f}P|中位{mkt['base']:.0f}P"
                 f"（同等级 rank{mkt['rank']} 低价视图 {mkt['n']} 条 / {mkt['ts']}）")
        L.append(f"匹配层级：{mkt['match_tier']}|样本：{mkt['sample_n']}条"
                 f"|同分位 k={mkt['pair_k']}|最近观测：{mkt['ts']}"
                 f"|置信度：{_conf_grade(mkt['n'])}")
        if mkt.get("zero") is not None:
            L.append(f"零洗基价：{mkt['zero']:.0f}P（rank0 最低挂牌）")
        else:
            L.append("零洗基价：无 rank0 挂牌样本，不可估价（不用 rank8 冒充）")
        ex = mkt.get("exact") or {}
        if ex.get("status") == "ok":
            L.append(f"类似紫卡（正负面相同）：{ex['n']} 条｜中位 {ex['median']:.0f}P"
                     f"（挂牌价·非成交）")
        else:
            L.append(f"类似紫卡（正负面相同）：不可估价（"
                     f"{ex.get('reason') or '无同组合样本'}），未用其他层级冒充")
    else:
        err = mkt.get("error") or "未知原因"
        L.append(f"价格预计：获取失败（{err}）")
        L.append("匹配层级：-|样本：0条|最近观测：-|置信度：-")
        L.append("零洗基价：获取失败")
    L.append(f"@foot|*市场来源：{mkt.get('source') or 'WM'}"
             f"｜{mkt.get('slug') or '-'}｜{mkt.get('ts') or ''}")
    L.append("@alert|*注意：伤害计算仅供参考，不要只根据评分买卖，多听群友意见。")
    # ---- 推荐洗卡词条（市场维度 = 最贵 100 条的词条频次，不参与计价） ----
    L.append("@title|·推荐洗卡词条")
    L.append("基于大佬推荐 - 暂无")
    L.append(f"基于伤害计算 - {rec['pos'] if rec else '暂无'}")
    if ok:
        pos_txt = "、".join(mkt.get("rec_pos") or []) or "样本不足"
        L.append(f"基于市场价差 - {pos_txt}（最贵 100 条词条频次，不参与计价）")
    else:
        L.append(f"基于市场价差 - 不可用（{mkt.get('error') or '市场数据获取失败'}）")
    # ---- 推荐负面词条 ----
    L.append("@title|·推荐负面词条")
    L.append("基于大佬推荐 - 暂无")
    L.append(f"基于伤害计算 - {rec['neg'] if rec else '暂无'}")
    neg = mkt.get("rec_neg") or {}
    if neg:
        L.append(f"基于市场价差 - {neg['zh']} {neg['delta']:+.1f}p/{neg['pct']:+.0f}%"
                 f"（同分位 rank{mkt['rank']} 各 {neg['n']} 条·中位数，"
                 f"挂牌价非成交价）")
    else:
        L.append(f"基于市场价差 - 不可用（{mkt.get('error') or '市场数据获取失败'}）")
    # ---- 致命负词条 ----
    L.append("@title|·致命负词条")
    L.append("基于大佬推荐 - 暂无")
    L.append(f"基于伤害计算 - {rec['deadly'] if rec else '暂无'}")
    L.append("基于市场价差 - WM 挂牌不含致命负标注，无法给出市场结论")
    # ---- 模拟说明（逐字对齐参考卡脚注口径） ----
    L.append("@foot|*靶子：Lv170 钢铁之路凶魂百夫长（去掉天生超宏，护甲 2700 参与）；"
             "弱点命中 50%，其余为身体命中；血量按本武器无紫卡基线的身体命中击杀时间取"
             "三个硬度节点（1.4s / 4.5s / 14.3s），每个节点 30s 窗口回放，击杀效率 取加权"
             "几何平均并计入血量展布；异常从零层叠起；同一武器的所有紫卡共用这组靶，"
             "增益可直接比较")
    L.append("@foot|*开火模式：使用模式 1，其余模式仅展示")
    L.append(f"@foot|*模拟武器 {weapon_zh}：弱点命中 50%，元素固定为病毒，异常从零层叠起，正常装填")
    # ---- 当前配卡 + 击杀效率 ----
    # 🔴 2026-11-13 第30轮：与图片卡同源（`_TYPE_TO_BUILD_KEY` + `_std_crit` + `_forma_needed`）。
    # 旧实现 `_BUILD.get(type or "Rifle", _BUILD["Rifle"])` 把 Zaw/Kitgun/未知类型全落到
    # 步枪卡，且「需极化{len(chips)}次」恒等于 8（8 槽全极化），与图片卡实算的 4/3/1 次
    # 直接矛盾 —— 同一份数据两个出口两套答案，是本轮要消灭的根因类型。
    _bkey = _build_key_for_type(parsed.get("type"))
    _crit = False
    if not _bkey:
        build = []                                   # 未知类型 → 8 格诚实占位，绝不套步枪
    elif _bkey == "melee":
        build = list(_MELEE_BUILD)                   # 近战无 en 表，无极化次数可算
    else:
        _crit = _std_crit(_weapon_raw(parsed.get("weapon")) or {})
        build = _build_zh(_bkey, _crit)
    chips = (build[:7] + ["当前紫卡"]) if build else ["需紫卡截图"] * 8
    _forma = None
    if _bkey and _bkey != "melee":
        _forma = _forma_needed(_BUILD_EN["crit" if _crit else "noncrit"][_bkey])
    if _forma is None:
        # 近战 / 数据缺失 / 未知类型：只写「当前配卡」，不编次数
        L.append("@title|·当前配卡 （含当前紫卡）")
    else:
        L.append(f"@title|·当前配卡 - 需极化{_forma}次 （含当前紫卡）")
    L.append("@chips|" + "|".join(chips))
    if sim and sc:
        _cs = sc.get("ceil_src")
        L.append(f"击杀效率：无紫卡 {sim['ke_base']:.1f} → 当前紫卡 {sim['ke_cur']:.1f} "
                 f"{_ceil_label(_cs, '→ ')}{sim['ke_best']:.1f}")
        L.append(f"紫卡增益：当前紫卡 {sc['gain'] * 100:+.1f}% "
                 f"{_ceil_label(_cs, '→ ')}{sc['gain_max'] * 100:+.1f}%")
    else:
        L.append("击杀效率：暂无（武器面板数据缺失）")
    L.append("@foot|*击杀效率：模拟条件下每分钟可击杀的敌人数，数值越高表示清怪越快；"
             "仅适合在相同靶子和计算条件下比较。")
    # ---- footer 指令（版式对齐参考卡，指令换成本机器人可用口令） ----
    L.append("[指令]查看DE官方紫卡价格：")
    L.append(f"@cmd|#wf 价格 {weapon_zh}")
    L.append("[指令]查看类似紫卡价格：")
    L.append(f"@cmd|#wf 紫卡 {weapon_zh}")
    short_tags = " ".join(_short_stat(s["en"]) for s in stats if s["sign"] != "-") + " 带负"
    L.append(f"rm/wm? 格式：{short_tags} {weapon_zh}")
    L.append("@foot|PC | qq_bridge Warframe 助手 善用右键/长按提取文字")
    return "\n".join(L)


#: 武器类别中文名（参考卡「步枪 MR 0 满级」的第一个词）
_TYPE_ZH = {
    "Rifle": "步枪", "Shotgun": "霰弹枪", "Pistol": "手枪", "Melee": "近战",
    "Kitgun": "组合枪", "Zaw": "Zaw", "Archgun": "重武器",
}


def build_card_data(parsed: dict, market: dict,
                    weapon_image: str | None = None,
                    now: str | None = None) -> dict:
    """把 ``parsed`` 转成「紫卡分析卡」图片模板的数据结构（与参考卡 1:1 对应）。

    在原有 4 列（数值/区间/偏差/评级）基础上，按参考卡补齐：
      * 顶部「倍率 1.20 · 全武器统一」（DE 44.1.3 后不再画星级圆点）；
      * 标题旁「Lv N」等级徽章；
      * 每条词条 **彩色圆形图标**（按词条类别取色，对齐参考卡）；
      * 每条词条右侧「低值 均值 X.X% : 高/低 X.X% 高值」统计行（参考卡同款）；
      * 底部「市场参考」+「提示（±10%）」两行（参考卡同款脚注）。


    与 :func:`build_card`（文字卡）同源同公式，只是输出结构化字段：
      * 每条词条给出 value / name / range / dev（**三位小数**，对齐参考卡）/
        grade（**每条独立评级**，参考卡有）/ bar（进度条 0~1）/ neg；
      * 顶部给出综合评级 + 综合偏差（各词条**卡面偏差**的算术均值，
        参考卡 +3.910% / +1.753% 均此口径）；
      * ``meta`` = 「步枪 MR 0 满级」，MR 取紫卡自身段位要求（OCR 的「段位 N」）。

    ⚠️ 与 ``build_card`` 必须共用 :func:`_normalize_stats`（单位推断 + 漏读修复），
    否则同一张卡的文字版与图片版会出现不同数值（实测 65.3 被读成 6.3 时尤甚）。

    2026-11-04 22:25 修复：``market`` 改为**必填位置参数**（旧 ``auc=None`` 可省略，
    图片卡就是这么把市场/推荐词条整块丢掉的）。漏传直接 TypeError/RivenMarketError，
    不允许「没数据就写暂无」这种静默降级。``recs`` 结构化输出供 PNG 模板绘制
    「市场参考 / 推荐洗卡 / 推荐负面 / 数据来源」四行。
    """
    if not isinstance(market, dict) or market.get("status") not in ("ok", "error"):
        raise RivenMarketError(
            "build_card_data 需要 fetch_market_for_card() 的 market dict"
            "（status=ok/error），收到 %r；旧版图片卡漏传市场数据是"
            "2026-11-04 用户截图的根因" % (market,))
    _load_riven_tables()
    stats = parsed["stats"]
    fix = _fix_of(parsed)
    _fitted = _fit_disp(parsed, fix)
    if _fitted is not None:
        parsed["_disp_fitted"] = (float(parsed.get("disp") or 1.0), _fitted)
        parsed["disp"] = _fitted
    _normalize_stats(parsed, fix)

    rows: list[dict] = []
    devs: list[float] = []
    for s in stats:
        rng = s.get("_rng")
        neg = s["sign"] == "-"
        if s["unit"] == "x":
            val_txt = f"x{s['value']:.2f}"
        else:
            val_txt = f"{'-' if neg else '+'}{_fmt_val(s)}"
        row = {"value": val_txt, "name": s["zh"], "sign": "-" if neg else "+",
               "range": "", "dev": "", "grade": "", "bar": 0.0, "neg": neg,
               # num = 原始数值（评测/断言用；模板只用 value 字符串）
               "num": round(float(s["value"]), 4),
               # 新增：图标配色 + 参考卡式「低/均值/高」三值 + 方向词（模板绘制右侧统计行）
               "icon": _stat_icon_color(s["en"]),
               "low": "", "mean": "", "high": "", "dev_dir": "", "dev_mag": ""}
        if rng:
            lo, hi = rng
            mid = _mid(s, parsed, fix)
            # 区间按**单位**排版：% 带百分号、倍率换算到 x 空间、平坦值（攻击范围）
            # 不带任何单位。旧实现一律拼 "%"，把「+2.3 攻击范围」写成「2.3%」、
            # 把「x0.63」写成「0.5%」，卡面直接是错的。
            if s["unit"] == "x":
                a, b = (1 - hi, 1 - lo) if neg else (1 + lo, 1 + hi)
                row["range"] = f"x{a:.2f} − x{b:.2f}"
                row["low"] = f"x{1 - hi:.2f}" if neg else f"x{1 + lo:.2f}"
                row["high"] = f"x{1 - lo:.2f}" if neg else f"x{1 + hi:.2f}"
                row["mean"] = f"x{1 - mid:.2f}" if neg else f"x{1 + mid:.2f}"
            elif s["unit"] == "%":
                row["range"] = f"{abs(lo):.1f}% − {abs(hi):.1f}%"
                row["low"] = f"{abs(lo):.1f}%"
                row["high"] = f"{abs(hi):.1f}%"
                row["mean"] = f"{abs(mid):.1f}%"
            else:
                row["range"] = f"{abs(lo):.1f} − {abs(hi):.1f}"
                row["low"] = f"{abs(lo):.1f}"
                row["high"] = f"{abs(hi):.1f}"
                row["mean"] = f"{abs(mid):.1f}"
            if mid:
                _v = s["value"] - 1.0 if s["unit"] == "x" else s["value"]
                dev = (abs(_v) / mid - 1.0) * 100.0
                devs.append(dev)
                row["dev"] = f"{dev:+.3f}%"
                row["grade"] = _grade(dev)
                # 进度条 = 该数值在标准值 ±10% 区间里的位置（参考卡实测吻合）
                row["bar"] = max(0.0, min(1.0, (dev + 10.0) / 20.0))
                row["dev_dir"] = "高" if dev >= 0 else "低"
                row["dev_mag"] = f"{abs(dev):.1f}%"
        rows.append(row)

    # 布局标签按**卡面符号**计数（与 Rabbit 同口径）：卡面「-」才算 1 个负词条位，
    # 「+」哪怕是「效果方向为负」的属性（Weapon Recoil 后坐力）也计入正位 → 3 正 0 负。
    # ⚠️ 注意：区间/均值/系数的选择仍走 ``_layout_curse``（后坐力按 curse 系数算 21 布局），
    # 二者**刻意解耦**——Rabbit 正是「标签 3正0负、区间却用 21 系数」这套口径
    # （阿利乌双枪 66.8/326.1/53.5 逐位吻合即证）。若把布局标签也绑到 _layout_curse，
    # 会把「+后坐力」误标成 2正1负，与参考卡不符。
    neg_ct = sum(1 for s in stats if s["sign"] == "-")
    pos_ct = len(stats) - neg_ct
    overall = sum(devs) / len(devs) if devs else 0.0
    type_zh = _TYPE_ZH.get(parsed.get("type", ""), parsed.get("type", ""))
    mr = parsed.get("mr")
    meta = f"{type_zh} MR {int(mr) if isinstance(mr, int) else 0} 满级"
    omega = float(parsed.get("disp") or 1.0)
    rank = _eff_rank(parsed)          # 反推等级（OCR 徽章不可信，见 _eff_rank）
    # ---- 市场 / 推荐词条区块（2026-11-04 23:0x 口径修订，代码位置：本函数末段） ----
    # 价格 = 同等级普通挂牌**中位数**；最贵 100 条只用于词条频次，绝不参与计价。
    # 失败态把错误原因原样写进卡面，绝不写「暂无」冒充成功、不回退静态权重表。
    if market["status"] == "ok":
        market_txt = (f"WM 低价视图中位 {market['base']:.0f}P｜区间 "
                      f"{market['lo']:.0f}–{market['hi']:.0f}P"
                      f"（同等级 rank{market['rank']} 样本 {market['n']} / {market['ts']}）")
        neg = market.get("card_neg") or market["rec_neg"]
        # 来源必须写明：card_neg=卡面这条负面实际值多少；rec_neg=推荐洗卡该选哪条负面。
        # 两者语义不同，混用会让用户对不上自己卡上的词条（2026-11-12 对照 Robot 发现）。
        neg_src = "卡面负面" if market.get("card_neg") else "推荐负面"
        # 百分比护栏：同分位「最低 k 条」比中位在**极低价武器**上会撑爆分母
        # （实测 Zaw Balla +456% / Kitgun Catchmoon +900% / Archgun Larkspur +233%，
        #  因为 base 池最低几条是十几 P 的挂单）。|pct|>100 时只给方向 + 绝对值，
        # 不让一个失真百分比冒充精确溢价——宁可少说，不编数。
        _pct = float(neg.get("pct") or 0.0)
        if abs(_pct) > 100.0:
            _neg_txt = (f"{neg['zh']} {neg['delta']:+.1f}p"
                        f"（{neg_src}｜样本离散大，仅参考方向）")
        else:
            _neg_txt = (f"{neg['zh']} {neg['delta']:+.1f}p/{_pct:+.0f}%"
                        f"（{neg_src}｜同分位 rank{market['rank']} 各 {neg['n']} 条"
                        f"·中位数·挂牌非成交）")
        ex = market["exact"]
        if ex["status"] == "ok":
            exact_txt = (f"正负面相同 {ex['n']} 条｜中位 {ex['median']:.0f}P"
                         f"（挂牌价·非成交）")
        else:
            exact_txt = f"不可估价：{ex['reason']}（未用其他层级冒充）"
        recs = {
            "status": "ok",
            "market": market_txt,
            "pos": "、".join(market["rec_pos"]) + "（最贵 100 条词条频次·不计价）",
            "neg": _neg_txt,
            "exact": exact_txt,
            "source": f"{market['source']} · {market['slug']} · {market['ts']}",
        }
    else:
        err = market.get("error") or "未知原因"
        market_txt = f"获取失败（{err}）"
        recs = {
            "status": "error",
            "market": market_txt,
            "pos": "不可用（市场数据获取失败）",
            "neg": "不可用（市场数据获取失败）",
            "exact": "不可用（市场数据获取失败）",
            "source": f"{market.get('source') or 'WM'} · {market.get('ts') or ''}",
            "error": err,
        }

    return {
        "brand": "紫卡分析",
        "corner": "统一倍率",
        # 现行统一倍率（不再传星级点阵）
        "disp": omega,
        "disp_dots": "",  # 星级点阵已废弃
        "disp_value": f"{omega:.2f}",
        # 等级徽章（参考卡「Lv 8」）
        "level": int(rank) if isinstance(rank, int) else None,
        "weapon_zh": parsed.get("weapon_zh") or parsed["weapon"],
        "weapon_en": parsed["weapon"],
        "riven_name": parsed.get("riven_name") or "",
        "weapon_image": weapon_image,
        "weapon_type": parsed.get("type"),
        "overall_grade": _grade(overall),
        "overall_pct": f"{overall:+.3f}%",
        "layout": f"{pos_ct} 正 {neg_ct} 负",
        "meta": meta,
        "stats": rows,
        "market": market_txt,
        "recs": recs,
        "hint": "偏差以对应倾向的理论中值为基准，正常范围为 ±10%（卡面取整可能略有偏差）。",
        "foot_left": "紫卡分析",
        "foot_right": now or time.strftime("%Y-%m-%d %H:%M"),
    }


async def fetch_market_for_card(parsed: dict) -> dict:
    """**唯一市场入口**（2026-11-04 22:25，代码位置：本文件末尾）。

    ``riven.py::riven_img_analyze_full`` 只调这里一次，把同一份快照同时喂给文字卡
    :func:`build_card` 和图片卡 :func:`build_card_data`，避免两卡数据不一致，也避免
    旧版「一处抓、一处干脆不抓」。

    :func:`fetch_market_bundle` 对 ANY 失败（网络、UA、空样本、样本不足、slug 解析
    失败）都 ``raise RivenMarketError``；这里把异常**显式编码**成 ``status=error``
    交给卡面显示原因。这不是降级：成功态只用真实挂牌数据，失败态不产生任何价格/
    推荐，也绝不回退 ``STAT_MARKET`` 静态表。不直接 raise 到 dispatcher，是为了让
    「市场获取失败」这件事在卡面上可见，而不是让用户收到与紫卡无关的空回复。
    """
    try:
        return await fetch_market_bundle(parsed)
    except Exception as e:  # noqa: BLE001 — 转成可见失败态，不吞、不伪造成功
        # nonebot 的 logger 是 Loguru 风格，不认 logging 的 %s 占位符；
        # 用 f-string 才能把真实错误原因写进日志（2026-11-04 22:47 429 联调发现）。
        logger.error(f"riven_market_failed: {type(e).__name__}: {e}")
        return market_error_bundle(e)


async def build_card_async(parsed: dict, market: dict | None = None) -> str:
    """文字卡组装；``market`` 由调用方统一抓一次传入（缺省时走唯一入口现抓）。

    2026-11-04 22:25 修复：旧版调用**已被删除的** ``_auction_stats()``，NameError
    被 ``except`` 吞掉 → ``auc=None`` → 卡面永远「WM 拍卖样本不足，暂无」。
    """
    if market is None:
        market = await fetch_market_for_card(parsed)
    return build_card(parsed, market=market)
