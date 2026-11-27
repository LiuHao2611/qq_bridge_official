# -*- coding: utf-8 -*-
"""丹雷机制（2026-11-27）：炼成高阶丹药引雷 → 玩家受伤 → 傀儡可抵挡并淬炼。

## 为什么独立成模块

``config.py`` / ``core.py`` / ``commands.py`` 三个文件贴 200KB 上传红线
（HEAD 均未超限、不豁免；commands.py 工作区仅余 198 B）。丹雷的常量与判定
全部收在本模块，core/commands 只加极薄接线，坚决不把新逻辑塞进那三个文件。

## 事实与设计的边界（见 docs/丹雷机制_炼药接入_2026-11-27.md）

* [事实·原著] 七品高级起引丹雷、八品二~九色、九品黑魔雷/丹雨/九玄金雷；
  第920处「傀儡抗一两道五色丹雷或许还行，度过丹雷痴人说梦」「吸收雷霆之力的
  傀儡…徐徐修复」。
* [设计·用户拍板] 七品起按品阶递增掉血；有出战傀儡 50% 全额免伤 + 淬炼；
  批量整批封顶 3 道 + 血量地板；抵挡不额外消耗雷淬石/金币。

## 分层（同步/异步边界）

本模块是**同步纯函数**：判定「这一炉成丹有没有雷、掉多少血、挡不挡」，
结果写入 ``res["danlei"]``；不读库、不写库、不改玩家字段。
傀儡淬炼落库在命令层（异步 pstore）完成，气血扣减由 core 在拿到判定后统一写。

单一真源：伤害比例/概率/封顶只在本模块定义；血量地板复用 cfg.LOSE_HP_FLOOR；
品阶档位走 cfg.alch_grade_idx；傀儡淬炼走 puppet_core.temper —— 本模块一律只调
别人，不另抄常数。
"""
from __future__ import annotations

from . import config as cfg

#: 引雷品阶下限（用户拍板「七品起」）。档位 = cfg.alch_grade_idx 的值：七品=6。
DANLEI_MIN_IDX = 6

#: 掉血 = 气血上限 × 该品阶比例（用户拍板「按品阶递增」）。
#: 七品 8% / 八品 15% / 九品 28% / 帝品 40%。索引 = alch_grade_idx - DANLEI_MIN_IDX。
DANLEI_DAMAGE_RATE = (0.08, 0.15, 0.28, 0.40)

#: 傀儡抵挡概率（用户拍板 50%，不改）。掷中=全额免伤；掷不中=玩家全吃。
DANLEI_GUARD_CHANCE = 0.50

#: 整批连炼最多结算的丹雷道数（用户拍板「封顶+地板」防自劈致死）。
DANLEI_BATCH_CAP = 3


def grade_triggers(grade: str) -> bool:
    """该品阶是否引丹雷（七品及以上）。唯一入口，卡面/文案/实炼共用。"""
    return cfg.alch_grade_idx(grade) >= DANLEI_MIN_IDX


def hurt_of(grade: str, hp_max: int) -> int:
    """单道丹雷伤害 = ceil(气血上限 × 品阶比例)，至少 1。

    纯函数：只算数、不改任何状态。品阶档位夹在 0..3（九品与帝品同为最高档）。
    """
    idx = min(len(DANLEI_DAMAGE_RATE) - 1,
              max(0, cfg.alch_grade_idx(grade) - DANLEI_MIN_IDX))
    return max(1, int(hp_max * DANLEI_DAMAGE_RATE[idx] + 0.9999))


def hp_floor(hp_max: int) -> int:
    """气血地板，与 battle_core 战败保底同源（cfg.LOSE_HP_FLOOR），禁另造常数。"""
    return max(1, int(hp_max * cfg.LOSE_HP_FLOOR))


def cap_n(n: int) -> int:
    """整批实际结算的丹雷道数上限（单炉 n=1 原样返回）。"""
    return max(0, min(int(n), DANLEI_BATCH_CAP))


def plan(grade: str, hp_max: int, has_puppet: bool, rng, n: int = 1) -> dict | None:
    """结算一炉/一批丹雷的判定（同步纯函数，不改 p、不读库）。

    :param grade: 丹药品阶（如「七品」）
    :param hp_max: 玩家气血上限（core.stats(p)["体力"]，调用方传入）
    :param has_puppet: 是否有出战傀儡（命令层在读库后传入；core 层恒 False）
    :param rng: 可注入随机函数，返回 0..1
    :param n: 本批成功炉数（单炉=1）；实际结算道数被 cap_n 封顶
    :return: None=不引雷（低阶丹，行为与改造前逐字节相同）；否则 dict:
        grade / hurt（单道伤害）/ n（实际结算道数）/
        blocked（被傀儡挡下的道数）/ hurt_total（最终扣血总额）
    """
    if not grade_triggers(grade):
        return None
    times = cap_n(n)
    if times <= 0:
        return None
    hurt = hurt_of(grade, hp_max)
    blocked = 0
    if has_puppet:
        for _ in range(times):
            if rng() < DANLEI_GUARD_CHANCE:
                blocked += 1
    return {
        "grade": grade,
        "hurt": hurt,
        "n": times,
        "blocked": blocked,
        "hurt_total": hurt * (times - blocked),
    }


def apply_hp(p: dict, hurt_total: int, hp_max: int) -> int:
    """把丹雷伤害写回玩家气血（带地板），返回实际扣除量。

    唯一写 p["hp"] 的丹雷入口。地板用 hp_floor（同源 cfg.LOSE_HP_FLOOR）。
    """
    before = int(p.get("hp", 0))
    # 地板只防「扣过头」，绝不反过来给玩家加血（before 可能已低于地板，如残血）
    after = max(min(before, hp_floor(hp_max)), before - max(0, int(hurt_total)))
    p["hp"] = after
    return before - after


def settle(p: dict, grade: str, hp_max: int, has_puppet: bool, rng,
           n: int = 1) -> dict | None:
    """core 层唯一入口：判定 + 扣血 + 挂 hurt_done，一步到位。返回 plan dict 或 None。

    低阶丹（plan 返回 None）时不碰 p、不掷 rng —— 与改造前逐字节相同。
    hp_max 由调用方传（core.stats(p)["体力"]），避免 danlei↔core 循环导入。
    """
    info = plan(grade, hp_max, has_puppet, rng, n)
    if not info:
        return None
    info["hurt_done"] = apply_hp(p, info["hurt_total"], hp_max)
    return info


def line(res: dict | None, hurt_done: int, puppet_name: str = "",
         purity: int | None = None, maxed: bool = False) -> str:
    """玩家可见的丹雷回执行（无雷返回空串）。禁章号/工程口吻（红线 §5、§17）。

    :param res: plan() 结果
    :param hurt_done: apply_hp 实际扣除量（可能因地板小于 hurt_total）
    :param puppet_name: 出战傀儡名（挡下时才传）
    :param purity: 淬炼后纯度（挡下且成功淬炼时传）
    :param maxed: 傀儡雷纹已满（挡下但仍免伤，只是纯度不涨）
    """
    if not res:
        return ""
    n = int(res["n"])
    blocked = int(res["blocked"])
    grade = str(res["grade"])
    if blocked >= n:
        # 全挡：傀儡扛下所有雷并借雷淬身
        tail = ("，雷纹已满、纯度不再涨" if maxed
                else ("，纯度 %d%%" % purity if purity is not None else ""))
        return ("⚡ %s丹雷 %d 道齐落，【%s】替你尽数扛下、借雷淬身%s；你未伤分毫。"
                % (grade, n, puppet_name or "傀儡", tail))
    if blocked > 0:
        return ("⚡ %s丹雷 %d 道：【%s】替你挡下 %d 道并借雷淬身，余下 %d 道劈在你身上，气血 -%d。"
                % (grade, n, puppet_name or "傀儡", blocked, n - blocked, hurt_done))
    return "⚡ %s丹雷 %d 道劈落，你硬生生扛下，气血 -%d。" % (grade, n, hurt_done)
