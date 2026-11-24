# -*- coding: utf-8 -*-
"""九星突破 BOSS 掉火的**决策**层（2026-11-24 改造，纯函数零 core 依赖）。

按玩家突破后的境界分档（用户拍板「按难度从兽火到异火，兽火50/异火100」）：
  * 低境界（realm < YIHUO_DROP_MIN_REALM，斗宗以下）：兽火档，50% 掉一朵兽火；
  * 高境界（realm >= 阈值，斗宗及以上）：异火档，100% 掉一朵异火（池限 4 朵）。

单一真源：常量全在 config（YIHUO_DROP_MIN_REALM / YIHUO_DROP_POOL /
BOSS_FLAME_DROP_BEAST / BOSS_FLAME_DROP_YIHUO / BEAST_FLAME_NAMES）。
本模块只回答"掉什么"，不写存档（写 flames / 折算药渣由 core.fight 侧完成）。
"""
from __future__ import annotations

from . import config as cfg


def flame_tier_for_lv(lv: int) -> str:
    """按突破后的境界返回掉落档："beast"（兽火档）或 "yihuo"（异火档）。"""
    realm = cfg.realm_index(int(lv))
    return "yihuo" if realm >= int(cfg.YIHUO_DROP_MIN_REALM) else "beast"


def yihuo_pool_for_lv(lv: int) -> list[str]:
    """异火池：只纳入境地下限已达标的那几朵（rank 越高门槛越高）。

    cfg.YIHUO_DROP_POOL = ((realm_gate, name), ...)，随境界解锁，保留稀有度梯度。
    """
    realm = cfg.realm_index(int(lv))
    return [name for gate, name in cfg.YIHUO_DROP_POOL if realm >= int(gate)]


def beast_pool_for_lv(lv: int) -> list[str]:
    """兽火池：同口径按境界解锁（0/2/4/6 四档），低境界不会掉到高阶兽火。"""
    realm = cfg.realm_index(int(lv))
    return [name for gate, name in cfg.BEAST_DROP_POOL if realm >= int(gate)]


def s_flame_gate(p, name: str) -> tuple[bool, str]:
    """S 线异火解锁判定：境界索引 + 主线进度双门槛（从 core 外迁，core re-export 保入口）。

    返回 ``(可解锁, 未满足原因)``。帝炎不在此列（走 diyan 斗帝成就）。
    """
    gate = (getattr(cfg, "S_FLAME_GATE", {}) or {}).get(name)
    if not gate:
        return True, ""
    if int(p.get("lv", 1)) < cfg.realm_lv(int(gate["realm"])):
        return False, "境界未至（需斗尊以上）"
    if int(p.get("main_idx", 0)) < int(gate["main"]):
        return False, "主线剧情未推进到相应章节"
    return True, ""


def beast_pill_result(p: dict, rec: dict) -> dict | None:
    """兽火单方（effect=beast_flame）的服用结果，None 表示不是兽火丹。

    返回 {"ok", "reason"?}；真正的扣丹/写 flames 由 core.use_item 做（需 take_item）。
    已持有该兽火 → flame_dup（不吞丹，与掉落重复同口径）。
    """
    if str(rec.get("effect") or "") != "beast_flame":
        return None
    bname = str(rec.get("flame") or "")
    fl = p.get("flames") or {}
    if int(fl.get(bname, 0)) > 0:
        return {"ok": False, "reason": "flame_dup", "flame": bname}
    return {"ok": True, "flame": bname}


def roll_flame_drop(lv: int, rng=None, rng_val: float | None = None) -> dict:
    """掉火决策：返回 {"ok", "kind"(beast/yihuo), "name", "rate"}。

    ``rng`` 是可调用对象；``rng_val`` 供测试直接注入 [0,1) 的值（不改存档）。
    兽火档掷 BOSS_FLAME_DROP_BEAST（500‰），异火档掷 BOSS_FLAME_DROP_YIHUO（1000‰=必中）。
    """
    if rng is None:
        import random
        rng = random.random
    r = int((rng_val if rng_val is not None else float(rng())) * 1000)
    tier = flame_tier_for_lv(lv)
    if tier == "yihuo":
        rate = int(cfg.BOSS_FLAME_DROP_YIHUO)
        pool = yihuo_pool_for_lv(lv)
        if r < rate and pool:
            # 用 rng 再抽一朵（rng_val 固定时取首朵，保证测试确定性）
            idx = 0 if rng_val is not None else int(float(rng()) * len(pool)) % len(pool)
            return {"ok": True, "kind": "yihuo", "name": pool[idx], "rate": rate}
    else:
        rate = int(cfg.BOSS_FLAME_DROP_BEAST)
        pool = beast_pool_for_lv(lv)
        if r < rate and pool:
            idx = 0 if rng_val is not None else int(float(rng()) * len(pool)) % len(pool)
            return {"ok": True, "kind": "beast", "name": pool[idx], "rate": rate}
    return {"ok": False, "kind": "", "name": "", "rate": rate}


def wild_flame_drops(p: dict, won_names, rng=None, add_item_fn=None) -> list[str]:
    """野外「对应魔兽掉火」（2026-11-24 用户口径）：每只被击败的兽火魔兽各掷一次。

    只掉 ``cfg.MONSTER_FLAME`` 里登记的对应兽火（不是随机池）；千分率
    ``BEAST_MONSTER_DROP_RATE``（500=50%）。已持有 → 折算药渣，不重复给（防刷）。
    返回本场**实际新得**的火名列表（已持有/没掉的不在内），供战报展示。
    """
    if rng is None:
        import random
        rng = random.random
    fl = p.setdefault("flames", {})
    got: list[str] = []
    for nm in won_names:
        flame = cfg.MONSTER_FLAME.get(str(nm))
        if not flame or int(float(rng()) * 1000) >= int(cfg.BEAST_MONSTER_DROP_RATE):
            continue
        if int(fl.get(flame, 0)) > 0:
            if add_item_fn is not None:
                add_item_fn(p, "药渣", 30)
            continue
        fl[flame] = 1
        got.append(flame)
    return got


def migrate_legacy_flames(p: dict, add_item_fn=None) -> list[str]:
    """老档火焰迁移（2026-11-24）：已下线的「阳火/化生火」折算药渣后移除。

    用户「整个系统只保留异火和兽火的说法」——旧档可能持有已删除身份的火，
    它们的加成数据已删。**不静默失效**：按持有等级折算药渣（每级 30），
    一次性移除键并返回被移出的火名（供命令层提示，空列表=无需迁移）。
    """
    fl = p.get("flames")
    if not isinstance(fl, dict):
        return []
    gone = [n for n in ("阳火", "化生火") if int(fl.get(n, 0) or 0) > 0]
    for n in gone:
        lv = max(1, int(fl.pop(n, 0) or 1))
        if add_item_fn is not None:
            add_item_fn(p, "药渣", 30 * lv)
    return gone
