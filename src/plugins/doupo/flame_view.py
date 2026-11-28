# -*- coding: utf-8 -*-
"""火焰查看 · 主动/被动 + 获取途径（2026-11-28，用户「加火焰查看功能，禁止遗漏」）。

设计口径
--------
* **全集 33 朵，一个不漏**：23 朵 FLAME_TIER（5 可养异火 + 虚无吞炎 + 6 A 线
  支线火 + 11 B 线数值火）+ 8 兽火 + 2 新生异火。守门逐朵校验每朵都有途径文案。
* **主动/被动**从权威表读，不重写：可养五火走 ``flame_skills.FLAME_SKILLS``；
  数值火走 config 的 desc + flame_bonus 口径，不另造数值。
* **获取途径逐朵据实**：全部来自 config 的投放真源（S_FLAME_GATE / A_FLAME_SIDE /
  YIHUO_DROP_POOL / BEAST_DROP_POOL / MONSTER_FLAME / BEAST_FLAMES.pill / 兽火单方），
  不编造。玩家可见的"怎么拿到"必须与代码里真实的发放入口一致。

为什么独立模块
--------------
config 贴 200KB 红线；查看是纯展示逻辑，独立成模块便于守门逐朵校验覆盖率。
"""
from __future__ import annotations

try:
    from . import config as cfg
    from . import flame_skills as fsk
except (ImportError, ValueError):  # 单文件直载兜底
    import config as cfg  # type: ignore[no-redef]
    import flame_skills as fsk  # type: ignore[no-redef]

#: 逐朵获取途径（玩家口径，据 config 投放真源）。key 必须覆盖全部 33 朵。
#: 分号分隔多条途径；措辞与代码里的实际发放入口对齐，不写没有的获取方式。
HOW: dict[str, str] = {
    # ---- 可养异火 5 ----
    "青莲地心火": "隐藏支线「沙漠深处的蛇影」完成后静默收服；九星突破异火档掉落池随机",
    "陨落心炎": "S 线：斗尊(境界)+主线第2章后收服；天焚炼气塔相关剧情",
    "三千焱炎火": "S 线：斗尊(境界)+主线第3章后收服；丹塔星域剧情",
    "净莲妖火": "S 线：斗圣(境界)+主线第3章后收服；妖火空间剧情",
    "帝炎": "斗帝成就——冲上斗帝时诸火自燃归一身，不靠喂养/收服口令",
    # ---- S 线剩余 ----
    "虚无吞炎": "S 线：斗圣(境界)+魂族主线第4章后收服",
    # ---- A 线隐藏支线 6 ----
    "生灵之焱": "隐藏支线「神农谷的灵田」完成后静默收服",
    "骨灵冷火": "隐藏支线「药老的旧友」完成后静默收服",
    "九龙雷罡火": "隐藏支线「焚炎谷的试炼」完成后静默收服",
    "金帝焚天炎": "隐藏支线「古族来客」完成后静默收服",
    "九幽金祖火": "隐藏支线「炎族的窖藏」完成后静默收服",
    "火山石焰": "隐藏支线「星域的余烬」完成后静默收服",
    # ---- B 线数值异火 11（九星异火档掉落池 4 朵 + 其余靠收服入口）----
    "海心焰": "九星突破异火档掉落（斗宗起入池）；也可发「收服 海心焰」",
    "火云水炎": "九星突破异火档掉落（斗宗起入池）；也可发「收服 火云水炎」",
    "万兽灵火": "九星突破异火档掉落（高阶斗宗起入池）；也可发「收服 万兽灵火」",
    "玄黄炎": "九星突破异火档掉落（高阶斗宗起入池）；也可发「收服 玄黄炎」",
    "九幽风炎": "发「收服 九幽风炎」收服（B 线数值异火）",
    "风怒龙炎": "发「收服 风怒龙炎」收服（B 线数值异火）",
    "幽冥毒火": "发「收服 幽冥毒火」收服（B 线数值异火）",
    "阴阳双炎": "发「收服 阴阳双炎」收服（B 线数值异火）",
    "荒破灭焱": "发「收服 荒破灭焱」收服（B 线数值异火）",
    "红莲业火": "发「收服 红莲业火」收服（B 线数值异火）",
    "龟灵地火": "发「收服 龟灵地火」收服（B 线数值异火）",
    # ---- 新生异火 2（萧炎融合，游戏内以收服入口获得）----
    "琉璃莲心火": "发「收服 琉璃莲心火」获得（萧炎融合青莲+陨落所创新生异火）",
    "三千莲心火": "发「收服 三千莲心火」获得（萧炎融合三火所创新生异火）",
    # ---- 兽火 8（对应魔兽野外掉落 / 兽火丹方 / 九星兽火档）----
    "紫火": "击败紫晶翼狮王掉落；或炼制服用「紫火丹」；九星突破兽火档掉落池",
    "桃花火": "击败奎木兽掉落；或炼制服用「桃花丹」；九星兽火档掉落池",
    "乾蓝水炎": "击败惊涛龙兽掉落；或炼制服用「乾蓝丹」；九星兽火档掉落池",
    "天寒火": "击败天骨蟒掉落；或炼制服用「天寒丹」；九星兽火档掉落池",
    "海魔炎": "击败海魔兽掉落；或炼制服用「海魔丹」；九星兽火档掉落池",
    "天阴魔火": "击败天阴三头狮掉落；或炼制服用「天阴丹」；九星兽火档掉落池",
    "血妖焰火": "炼制服用「血妖丹」；或九星突破兽火档掉落池（无单一魔兽）",
    "幻金火": "击败幻火蝎龙兽掉落；或炼制服用「幻金火丹」；九星兽火档掉落池",
}


def all_flame_names() -> list[str]:
    """全集 33 朵，去重保序（守门用）。"""
    out: list[str] = []
    for n in (list(getattr(cfg, "FLAME_TIER", {}).keys())
              + list(getattr(cfg, "BEAST_FLAME_NAMES", []))
              + list(getattr(cfg, "FUSION_FLAME_NAMES", []))):
        if n not in out:
            out.append(n)
    return out


def how_of(name: str) -> str:
    return HOW.get(str(name), "获取途径待补（守门会拦）")


def _effect_of(name: str) -> tuple[str, str]:
    """返回 (主动描述, 被动描述)。可养五火取权威表，数值火取 desc。"""
    rec = fsk.FLAME_SKILLS.get(name)
    if rec:
        a = rec["active"]
        pv = rec["passive"]
        # 源 effect 里可能带内部实现注记（如「全属性 +20%（core.stats 已实现）」），
        # 玩家可见文本剥掉括号注记，只留效果本身。
        import re
        eff = re.sub(r"（[^）]*已实现[^）]*）", "", str(pv["effect"])).strip()
        return ("主动【%s】%s" % (a["name"], a["brief"]),
                "被动【%s】%s" % (pv["name"], eff))
    # 数值火/兽火/新生异火：无主动技，被动读 config desc 里的加成句
    d = (cfg.FLAME_BY_NAME.get(name) or
         getattr(cfg, "BONUS_FLAME_BY_NAME", {}).get(name) or
         getattr(cfg, "BEAST_FLAME_BY_NAME", {}).get(name) or {})
    desc = str(d.get("desc") or "")
    # 取最后一句（都是"...每级 +N%"的加成句）
    tail = desc.rsplit("。", 2)
    eff = tail[-2].strip() if len(tail) >= 2 and tail[-2].strip() else desc
    return ("主动：无（数值型火焰）", "被动：" + eff)


def view_text(name: str) -> str:
    """火焰详情文本（查看 <火焰名>）。含主动 + 被动 + 获取途径。"""
    active, passive = _effect_of(name)
    tier = (getattr(cfg, "FLAME_TIER", {}) or {}).get(name, "")
    ident = "兽火" if name in getattr(cfg, "BEAST_FLAME_NAMES", []) else "异火"
    lines = ["🔥 【%s】%s%s" % (name, ident, ("·" + tier + " 线") if tier else "")]
    lines.append("　" + active)
    lines.append("　" + passive)
    lines.append("　获取：" + how_of(name))
    return "\n".join(lines)
