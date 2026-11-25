# -*- coding: utf-8 -*-
"""丹纹赐福（danwen blessing）—— 最高丹纹丹药附带的限时/限次特殊加成。

设计文档：``docs/炼药模块优化_原著考据与设计_2026-11-24.md`` §10.5。

为什么单独一个模块
------------------
``core.py`` 长期贴着 200KB 提交红线；赐福的「随机抽取 / 次数扣减 / 命中注入」
是一套独立纯逻辑，与 ``temp_buff``（分钟级五维百分比）互补：

* ``temp_buff`` 管**限时属性**（B03/B04/B05 +25%/30 分钟，到期自然消失）；
* 本模块管**限次战斗触发**（B01 前 3 击×2 / B02 首击必暴击 / B06 斗气回满 1 场）。

红线
----
1. **加成给足、靠边界平衡**：用户明确「别低于 10%、给足」，所有效果 ≥ +20%；
   平衡只靠「次数/时长」封顶，不靠削弱数值。
2. **次数型不掷骰**：达成条件（服了带纹丹）就挂上，扣次数确定性；随机只在「掷哪条赐福」。
3. **不写 ``p["bonus"]``**：限时属性走 temp_buff，次数型走 ``danwen_charges``，
   都不污染永久加成（2026-11-18 假药事故同类坑）。
4. **零值显式判空**：``n <= 0`` 视为无，禁止 ``or`` 兜底（0 是合法耗尽态）。
5. 纯函数、零 IO、**不 import core / config**（core 会 import 本模块）。
"""
from __future__ import annotations

import random
import time

#: 丹纹独立随机源——不碰战斗/成败注入的 rng，保证既有调用序列逐字节不变。
_R = random.Random()

#: 赐福池唯一真源。id 稳定（存档只存 id），改数值只改这里。
#: kind=charges 走 danwen_charges；kind=timed 走 temp_buff.add。
#: 文案里的 ``{n}``/``{min}`` 是丹纹档位缩放后的实数值（见 :func:`_fmt`）。
BLESSINGS: tuple[dict, ...] = (
    {"id": "B01", "name": "焚天之威", "kind": "charges", "n": 3,
     "desc": "每场战斗前 {n} 次出手伤害 ×2", "effect": "dmg_x2"},
    {"id": "B02", "name": "一击必中", "kind": "charges", "n": 1,
     "desc": "每场战斗首次技能伤害必定暴击", "effect": "crit_first"},
    {"id": "B03", "name": "蛮牛之力", "kind": "timed", "key": "力量", "mul": 0.25,
     "minutes": 30, "desc": "力量、斗气 +25%（{min} 分钟）", "effect": "stat_atk"},
    {"id": "B04", "name": "疾风之势", "kind": "timed", "key": "敏捷", "mul": 0.25,
     "minutes": 30, "desc": "敏捷、防御 +25%（{min} 分钟）", "effect": "stat_def"},
    {"id": "B05", "name": "虎躯之固", "kind": "timed", "key": "体力", "mul": 0.30,
     "minutes": 30, "desc": "气血上限 +30%（{min} 分钟）", "effect": "stat_hp"},
    {"id": "B06", "name": "丹田盈溢", "kind": "charges", "n": 1,
     "desc": "斗气上限 +20%，且下一场开战时斗气回满 1 次", "effect": "mp_refill"},
)

#: 丹纹档位对赐福的缩放（下标=纹数）：**百分比不动，只放大次数/时长**。
#: 用户定的平衡杠杆是「限时/限次」，所以档位差距落在「撑多久、能打几下」，
#: 而不是把 ×2 变成 ×4 去破坏伤害曲线。
_TIER_SCALE = (1.0, 1.0, 1.5, 2.0)
#: 三纹额外多赐一条福（用户：「一个丹药的最高丹纹，额外给特殊加成」）。
_TIER_EXTRA = 3

_BY_ID = {b["id"]: b for b in BLESSINGS}
#: 限时型的第二属性键（B03 力量→斗气，B04 敏捷→防御）。
_TWIN = {"B03": "斗气", "B04": "防御"}


def get(bid: str) -> dict | None:
    return _BY_ID.get(str(bid))


def roll(grade: str, rng=None) -> int:
    """按品阶掷纹数：返回 **0=不出纹 / 1=一纹 / 2=二纹 / 3=三纹**。

    默认用独立源 ``_R``（不动注入的成败 rng，既有序列逐字节不变）；单测传 rng 可钉死。
    档位分布取 ``config.DANWEN_TIER_W``（70%/25%/5%），只在出纹之后才掷第二颗骰子。
    config 延迟 import 避免环。
    """
    from . import config as cfg
    r = _R.random if rng is None else rng
    if r() >= cfg.danwen_rate(grade):
        return 0
    x = r()
    acc = 0.0
    for i, w in enumerate(cfg.DANWEN_TIER_W, 1):
        acc += float(w)
        if x < acc:
            return i
    return 1                      # 权重和 <1 的兜底：给最低档，绝不返回 0（已判定出纹）


def _clamp_tier(tier) -> int:
    try:
        t = int(tier or 0)
    except (TypeError, ValueError):
        t = 0
    return max(1, min(3, t)) if t > 0 else 1


def tier_of(name: str) -> int:
    """从丹名反推纹数（``·二纹``→2，存量 ``·丹纹``→1）；非带纹丹返回 0。"""
    from . import config as cfg
    return int(cfg.danwen_tier(name))


def _scale_of(tier) -> float:
    return _TIER_SCALE[_clamp_tier(tier)]


def _n_of(blessing: dict, scale: float) -> int:
    """缩放后的次数：至少 1（缩放到 0 就等于没给）。"""
    base = int(blessing.get("n", 1) or 1)
    return max(1, int(round(base * float(scale))))


def _mins_of(blessing: dict, scale: float) -> float:
    return float(blessing.get("minutes", 30) or 30) * float(scale)


def _fmt(blessing: dict, scale: float = 1.0) -> str:
    """把赐福文案里的 ``{n}``/``{min}`` 换成缩放后的真实数字。"""
    return str(blessing["desc"]).format(
        n=_n_of(blessing, scale),
        min=int(_mins_of(blessing, scale)) if _mins_of(blessing, scale) % 1 == 0
        else round(_mins_of(blessing, scale), 1))


def pick(rng=None) -> dict:
    """等权随机抽一条赐福（6 选 1）。rng 可注入，默认 random.random。"""
    r = random.random if rng is None else rng
    options = list(BLESSINGS)
    return options[min(len(options) - 1, int(r() * len(options)))]


def _charges(p: dict) -> dict:
    c = p.get("danwen_charges")
    if not isinstance(c, dict):
        return {}
    return c


def attach(p: dict, blessing: dict, src: str, now=None, scale: float = 1.0) -> str:
    """把一条赐福挂到玩家身上，返回给玩家的可见文案。

    charges 型写 ``p["danwen_charges"][id] = {"n": n, "src": src}``（同名刷新取较大次数，
    不叠加，防囤丹刷满）；timed 型交给调用方通过 ``timed_items()`` 写 temp_buff，
    以免本模块 import temp_buff 形成环（其实 temp_buff 不 import 本模块，可安全调用，
    但保持纯函数边界，显式由 core 落库更清晰）。

    ``scale``：丹纹档位缩放（一纹 1.0 / 二纹 1.5 / 三纹 2.0），只放大次数与时长。
    """
    bid = str(blessing["id"])
    if blessing["kind"] == "charges":
        c = dict(_charges(p))
        n = _n_of(blessing, scale)
        old = c.get(bid)
        if isinstance(old, dict):
            n = max(n, int(old.get("n", 0)))  # 同名刷新：取较大剩余次数，不累加
        c[bid] = {"n": n, "src": str(src)}
        p["danwen_charges"] = c
    return _fmt(blessing, scale)


def timed_items(p: dict, blessing: dict, now=None,
                scale: float = 1.0) -> list[tuple[str, float, float, str]]:
    """返回限时型赐福要写进 temp_buff 的条目 ``(key, mul, minutes, src)``。

    B03/B04 是双属性，返回两条；B05 单属性一条。由 core 统一调 temp_buff.add。
    百分比 ``mul`` 不随档位变，只有 ``minutes`` 按 ``scale`` 放大。
    """
    if blessing["kind"] != "timed":
        return []
    key = str(blessing["key"])
    mul = float(blessing.get("mul", 0))
    mins = _mins_of(blessing, scale)
    src = str(blessing["name"])
    out = [(key, mul, mins, src)]
    twin = _TWIN.get(blessing["id"])
    if twin:
        out.append((twin, mul, mins, src))
    return out


def consume(p: dict, bid: str) -> bool:
    """消耗一次 charges 型赐福；剩余耗尽则删键。返回是否还有有效的这次。"""
    c = _charges(p)
    e = c.get(str(bid))
    if not isinstance(e, dict):
        return False
    n = int(e.get("n", 0))
    if n <= 0:            # 显式判 0，不用 or 兜底
        c.pop(str(bid), None)
        p["danwen_charges"] = c
        return False
    e["n"] = n - 1
    if e["n"] <= 0:
        c.pop(str(bid), None)
    p["danwen_charges"] = c
    return True


def has(p: dict, effect: str) -> bool:
    """按 effect 类型查是否有可用次数（不改状态；真正扣减用 consume）。"""
    for bid, e in _charges(p).items():
        if not isinstance(e, dict) or int(e.get("n", 0)) <= 0:
            continue
        b = _BY_ID.get(str(bid))
        if b and b.get("effect") == effect:
            return True
    return False


def bid_of(p: dict, effect: str) -> str | None:
    """返回持有某 effect 的赐福 id（供 consume 精确扣）。"""
    for bid, e in _charges(p).items():
        if not isinstance(e, dict) or int(e.get("n", 0)) <= 0:
            continue
        b = _BY_ID.get(str(bid))
        if b and b.get("effect") == effect:
            return str(bid)
    return None


def grant(p: dict, src_name: str, rng=None, add_fn=None, tier=0) -> dict:
    """服带纹丹后挂赐福，返回 ``{"id","name","desc","tier","extra","lines"}``。

    ``add_fn(key, mul, minutes, src)`` 由 core 注入 temp_buff.add（避免本模块 import）。
    次数型写 danwen_charges，限时型通过 add_fn 写 temp_buff。

    档位（``tier`` 不传就从丹名后缀反推）：
      * 一纹：1 条赐福，原始次数/时长；
      * 二纹：1 条赐福，次数与时长 ×1.5；
      * 三纹：**2 条**赐福（第二条避开同 id），各自原始次数/时长 —— 用户要的
        「最高丹纹额外给特殊加成」。
    百分比永远不动，档位只买「撑更久 / 打更多下」，符合「限时限次即平衡」。
    """
    t = _clamp_tier(tier if tier else tier_of(src_name))
    scale = _scale_of(t)
    b = pick(rng)
    pairs = [(b, scale)]
    extra = None
    if t >= _TIER_EXTRA:
        extra = pick(rng)
        if str(extra["id"]) == str(b["id"]):
            # 抽到同一条 → 顺延一条，否则同名刷新会把第二条白白吃掉
            i = (BLESSINGS.index(b) + 1) % len(BLESSINGS)
            extra = BLESSINGS[i]
        pairs.append((extra, 1.0))
    lines = []
    for bl, sc in pairs:
        txt = attach(p, bl, src_name, scale=sc)
        if add_fn is not None:
            for key, mul, mins, src in timed_items(p, bl, scale=sc):
                add_fn(key, mul, mins, src)
        lines.append((str(bl["name"]), txt))
    # 纹数与「额外那条」都拼进 desc：core 只需把 desc 交给文本层，不再单独传参
    desc = "（%s纹）%s" % ("一二三"[t - 1], lines[0][1])
    if extra is not None:
        desc += "；并额外得【%s】%s" % (extra["name"], lines[1][1])
    return {"id": b["id"], "name": b["name"], "desc": desc, "tier": t,
            "extra": str(extra["name"]) if extra else "", "lines": lines}


def fight_state(p: dict) -> dict:
    """战斗开场取赐福快照（局部计数，命中时由 core 调 consume 写回 p）。

    ``crit``=本场还有几次必暴击，``dmg_x2``=本场还有几次伤害×2，
    ``mp_refill``=本场是否斗气回满。次数型赐福跨场保留，这里只是本场可用量。
    """
    def _n(effect):
        bid = bid_of(p, effect)
        if not bid:
            return 0
        e = _charges(p).get(bid, {})
        return int(e.get("n", 0)) if isinstance(e, dict) else 0
    return {"crit": min(1, _n("crit_first")), "dmg_x2": _n("dmg_x2"),
            "mp_refill": _n("mp_refill") > 0}


def sweep(p: dict) -> bool:
    """清掉坏数据（非 dict / 次数 <=0 / src 空），返回是否改过 p。镜像 temp_buff.sweep。"""
    c = p.get("danwen_charges")
    if not isinstance(c, dict):
        if c is not None:
            p.pop("danwen_charges", None)
            return True
        return False
    kept = {}
    for bid, e in c.items():
        if not isinstance(e, dict):
            continue
        try:
            n = int(e.get("n", 0))
        except (TypeError, ValueError):
            continue
        if n <= 0 or bid not in _BY_ID:
            continue
        kept[bid] = e
    if kept != c:
        if kept:
            p["danwen_charges"] = kept
        else:
            p.pop("danwen_charges", None)
        return True
    return False


def lines(p: dict) -> list[str]:
    """人物卡/背包可见：「🔥 焚天之威 · 剩 3 击」。"""
    out = []
    for bid, e in _charges(p).items():
        if not isinstance(e, dict):
            continue
        n = int(e.get("n", 0))
        if n <= 0:
            continue
        b = _BY_ID.get(str(bid))
        if b:
            out.append("🔥 %s · 剩 %d 次" % (b["name"], n))
    return out


# ---------------------------------------------------------------------------
# 「查看 <物品名>」详情卡的取数口径（2026-11-25）
# 用户：「几纹有什么概率出什么加成」「尽量展示详细」。卡面不许自己重算，
# 否则改一次数值就要改三处（卡面 / 文本 / 实际结算）。以下三个函数是唯一出口。
# ---------------------------------------------------------------------------
def scale_of(tier) -> float:
    """档位 → 缩放倍率（一纹 1.0 / 二纹 1.5 / 三纹 2.0）。"""
    return _scale_of(tier)


def fmt(blessing: dict, tier=1) -> str:
    """按档位把某条赐福渲染成实数值文案（次数 / 时长已缩放）。"""
    return _fmt(blessing, _scale_of(tier))


def tier_rule(tier) -> str:
    """档位规则一句话（详情卡「丹纹」段用）。

    一纹 1 条按原次数/时长；二纹 1 条但次数时长 ×1.5；三纹 1 条 ×2.0
    并**另加一条**（用户：「最高丹纹额外给特殊加成」）。
    """
    t = _clamp_tier(tier)
    if t >= _TIER_EXTRA:
        return "三纹：赐福 1 条，次数/时长 ×2.0，另额外再得 1 条"
    if t == 2:
        return "二纹：赐福 1 条，次数/时长 ×1.5"
    return "一纹：赐福 1 条，按原次数/时长"


def tier_table(blessing: dict) -> str:
    """同一条赐福的「一纹原文 + 更高档差值」：「每场战斗前 3 次出手 ×2（二纹 4 次 / 三纹 6 次）」。

    早先版本把整句按档重述三遍（``…×2 ｜ 二纹 …×2；三纹 …×2``），
    一条占两行、六条就顶掉半张卡。玩家要的是「高档到底多给多少」，
    所以只把**变了的那一个数**（次数 / 分钟）标出来。
    """
    base = _fmt(blessing, _scale_of(1))
    desc = str(blessing.get("desc") or "")
    # 🚨 只在**文案本身用了占位符**时才算档位差：
    # B02「首次技能伤害必定暴击」/ B06「回满 1 次」的 ``n`` 是充能数、句子不引用它，
    # 若照缩放映成「二纹 2 次 / 三纹 2 次」会与前半句「首次」自相矛盾（实测截图抓到）。
    if "{n}" in desc:
        unit, get, fmtv = "次", _n_of, lambda v: str(v)
    elif "{min}" in desc:
        unit, get, fmtv = "分钟", _mins_of, lambda v: (
            str(int(v)) if float(v) % 1 == 0 else str(round(v, 1)))
    else:
        return base
    b0 = get(blessing, _scale_of(1))
    bits = []
    for t in (2, 3):
        v = get(blessing, _scale_of(t))
        if v != b0:
            bits.append("%s纹 %s" % ("一二三"[t - 1], fmtv(v)))
    if bits:
        # 单位只写一次：「二纹 45 / 三纹 60 分钟」（每条都带单位会多折一行）
        return "%s ｜ %s %s" % (base, " / ".join(bits), unit)
    return base
