# -*- coding: utf-8 -*-
"""斗破苍穹 · 口令业务层（纯函数 + 存储交互，零 nonebot 依赖，可直接单测）。

每个 cmd_* 输入「store + 玩家/群标识 + 参数」，返回要回群的文字。
__init__.py 的 matcher 只负责取参数 → 调 cmd_* → finish(文字)。

⚠️ 命名约定：斗破 / 修仙 共用纯中文口令，靠「按用户记录的当前模式」分流；
   菜单（斗破菜单 / 修仙菜单）是唯一切换口，其余口令两游戏完全一致（突破 / 闭关 / 商店…）。
"""

from __future__ import annotations

import re
import time

from . import config as cfg
from . import core
from . import duel_challenge as dc
from . import factions as fac
from . import flame_skills as fs
from . import skill_book as book
from . import tower_data as td
from . import tower_session as ts
from . import weekly_data as wd
from . import weekly_session as ws

NOT_ENTERED = "你尚未踏入斗气大陆，发送「入道」开启征程。"


async def _player(store, qq):
    return await store.get_player(str(qq))


def _bar(cur: int, mx: int, width: int = 10) -> str:
    if mx <= 0:
        return ""
    n = max(0, min(width, int(cur * width / mx)))
    return "█" * n + "░" * (width - n)


def _fmt(n) -> str:
    n = int(n)
    if n >= 10000:
        return f"{n / 10000:.1f}万"
    return str(n)


def _battle_line(it: dict, who: str = "p", who_label: str = "",
                 foe: str = "兽击") -> str:
    """把一条战报 log 渲染成「行动 + 伤害 + 属性归因」的内联文案。

    2026-11-16 用户诉求：人物卡每个属性要和战斗强关联，不能「卡上摆数字、
    战斗只播成败」。故每条行动后括注真正起作用的那项属性/机制，让玩家一眼
    看懂「我堆的属性换来了什么」。特殊条目（持续伤害/反伤/被控/闪避）字段
    不齐，一律用 .get 取值，杜绝 KeyError（见 2026-11-16 战斗回包崩溃事故）。

    :param who: "p"=PVE 我方视角；"v"=PVP 对手视角（前缀用「对方」）。
    :param who_label: 自定义行动方前缀（切磋时换真人昵称），空串沿用默认。
    :param foe: 闪避文案里的对手称呼，PVE 默认「兽击」，切磋改「来招」。
    """
    if it == "...":
        return "　…"
    kind = str(it.get("kind") or "出手")
    dmg = int(it.get("dmg") or 0)
    pre = "　→ " if who != "v" else "　对方 "
    if who_label:
        pre = f"　{who_label} "

    # 贵人相助：机缘彩蛋，一招制敌
    if it.get("aid"):
        helper = str(it.get("helper") or "无名斗皇")
        return f"✨ {helper}破空而至，一招制敌！（机缘）"

    # 被控 / 闪避：都不造成伤害，归因到控场斗技 / 敏捷
    if it.get("miss"):
        if kind == "被控":
            return f"{pre}{kind}，无法出手（斗技·控场）"
        return f"{pre}身形一晃，避开了{foe}（敏捷·闪避）"

    # 持续伤害 / 反伤：斗技余威与反击
    if it.get("dot"):
        return f"{pre}{kind} {dmg}（斗技·持续）"
    if it.get("reflect"):
        return f"{pre}{kind} {dmg}（斗技·反伤）"

    # 兽击（敌方命中我方）：归因到我方防御减伤
    if it.get("taken"):
        return f"　← {kind} {dmg}（防御减伤）"

    # 我方出手：技能=力量+斗气；普攻=力量+武器；暴击再点敏捷
    via = "力量·斗气" if it.get("skill") else "力量·武器"
    if it.get("crit"):
        via += "·暴击(敏捷)"
    return f"{pre}{kind} {dmg}（{via}）"


def _battle_highlights(log: list, limit: int = 4) -> tuple[list[dict], int]:
    """从一条战报里剪出「高光条目」，中间回合折叠（用户 2026-11-16 需求）。

    参考热门回合制/赛事战报的写法（梦幻西游武神坛解说：只讲「第 N 回合 +
    技能名 + 暴击万伤 + 击杀」，不做逐招流水账）：
      * 必留：**首击**（开手招式）、**终结一击**（最后一个有效攻击）、
        **暴击**（伤害最突出的 1~2 式）、**机缘/控制/闪避**等特殊高光；
      * 折叠：其余中间普通回合，只报「…（略 N 式）」；
      * 上限 limit 条高光，超出时优先保留暴击大的。

    返回 ``(高光条目列表, 被折叠的条目数)``。条目一律是原 dict 引用，
    不改字段、不做硬取（特殊条目字段不齐，渲染全靠 _battle_line 的 .get）。
    """
    if not log:
        return [], 0
    if limit == 1:
        # 固定骨架结算面板专用：整场只留「最强一击」——机缘 > 暴击 > 最高伤害。
        # 首击虽是开手招式，但信息量低；结算面板只给玩家看最亮的那一式。
        # 不写死某个 kind，特殊条目同样走 .get，避免字段不齐 KeyError。
        hits = [(i, it) for i, it in enumerate(log) if isinstance(it, dict)
                and int(it.get("dmg") or 0) > 0
                and not it.get("taken") and not it.get("miss")]
        if hits:
            def _score(pair):
                _i, it = pair
                return (int(it.get("dmg") or 0)
                        + (10 ** 8 if it.get("aid") else 0)
                        + (10 ** 6 if it.get("crit") else 0))
            best = max(hits, key=_score)
            return [best[1]], len(log) - 1
        # 整场没打成有效伤害（极端：全闪避/被控），仍保留一条原始条目兜底
        pool = [(i, it) for i, it in enumerate(log) if isinstance(it, dict)]
        if pool:
            return [pool[0][1]], len(log) - 1
        return [], 0
    # 有效攻击条目：有 dmg>0 且非兽击/被控/闪避这类敌方动作
    attacks = [(i, it) for i, it in enumerate(log)
               if isinstance(it, dict) and int(it.get("dmg") or 0) > 0
               and not it.get("taken") and not it.get("miss")]
    keep: set[int] = set()
    # 首击
    if attacks:
        keep.add(attacks[0][0])
    # 终结一击（最后一个攻击条目）
    if attacks and len(attacks) > 1:
        keep.add(attacks[-1][0])
    # 特殊高光：暴击 / 机缘 / 反伤 / 持续 / 被控（控场也有看点）
    special = [(i, it) for i, it in enumerate(log) if isinstance(it, dict)
               and (it.get("crit") or it.get("aid") or it.get("reflect")
                    or it.get("dot") or (it.get("miss") and it.get("kind") == "被控"))]
    # 伤害最高的几式（不含已选），凑到 limit
    ranked = sorted(attacks, key=lambda x: int(x[1].get("dmg") or 0), reverse=True)
    for i, _it in [(i, it) for i, it in ranked] + special:
        if len(keep) >= limit:
            break
        keep.add(i)
    # 按原顺序返回，去重
    ordered = [(i, it) for i, it in enumerate(log) if i in keep][:limit]
    return [it for _i, it in ordered], max(0, len(log) - len(ordered))


def _chance_lines(ch: dict | None, qq: str = "") -> list[str]:
    """把任务途中触发的机遇拼成回复末尾独立小段。

    单独成段，不塞进战报/炼丹的语义行里，避免严格验收把机遇文案误判成
    口令本身的失败提示；也方便玩家一眼看出「这不是任务奖励，是路上撞见的」。

    有奖竞猜（``ch["quiz"]``）：注册答题会话 + arm 按钮指令（ContextVar），
    返回题目与选项文字（NapCat 无按钮也能手打选项）；**答案与奖励不在此发放**，
    由按钮点击回调 :func:`cmd_answer_quiz` 判定答对后才发奖。
    """
    if not ch:
        return []
    mark = {"good": "🍀", "bad": "⚡", "neutral": "🔎"}.get(ch.get("tone"), "✦")
    quiz = ch.get("quiz")
    if quiz and qq:
        from . import quiz_session as qs  # noqa: PLC0415 — 延迟避免包初始化副作用

        q = qs.STORE.start(qq, quiz["question"], quiz["options"],
                           quiz["answer"], quiz.get("reward") or {})
        qs.arm_button(q.token)   # 让发送层给本条回复挂选项按钮
        # 序号从 1 开始：文案让玩家「直接回复 1/2/3」，若这里按 0 开始展示，
        # 玩家照屏幕上的号回答就会错位（真实踩过：显示 2.二十二，回 2 判成二十）。
        opts = " / ".join("%d.%s" % (i + 1, o) for i, o in enumerate(q.options))
        return ["", f"{mark} 机遇 · {ch['name']}",
                f"　{ch['text']}", f"　选项 {opts}",
                "　点选选项、或直接回复 1/2/3 作答；答对方可得奖"
                "（限 %d 秒，超时作废并失去奖励）" % cfg.CHOICE_TTL]
    return ["", f"{mark} 机遇 · {ch['name']}",
            f"　{ch['text']}", f"　结算：{ch['gain']}"]


def _grant_reward(p: dict, rw: dict) -> str:
    """把奖励 dict 真正结算到玩家（与 core._apply_chance 同口径），返回可读增益串。"""
    parts: list[str] = []
    rw = rw or {}
    gold = int(rw.get("gold") or 0)
    if gold:
        p["gold"] = max(0, int(p.get("gold", 0)) + gold)
        parts.append("金币 %+d" % gold)
    exp = int(rw.get("exp") or 0)
    if exp:
        p["exp"] = int(p.get("exp", 0)) + exp
        parts.append("修为 %+d" % exp)
    luck = int(rw.get("luck") or 0)
    if luck:
        p["luck"] = min(9999, max(0, int(p.get("luck", 0)) + luck))
        parts.append("机缘 %+d" % luck)
    for name, n in (rw.get("item") or {}).items():
        if int(n) > 0:
            core.add_item(p, name, int(n))
            parts.append("%s ×%d" % (name, int(n)))
    return " · ".join(parts) if parts else "无"


async def cmd_answer_quiz(store, qq: str, token: str, idx) -> str:
    """有奖竞猜·选项按钮点击回调：判定对错，答对才发奖（一次性）。"""
    from . import quiz_session as qs  # noqa: PLC0415

    qq = str(qq)
    res = qs.STORE.answer(qq, token, idx)
    if not res.valid:
        if res.reason == "no_session":
            if qs.STORE.take_expired(qq):
                return ("⏰ 这题已超时作废（限 %d 秒），本题奖励已失去。"
                        % cfg.CHOICE_TTL)
            return "这题已失效（或已作答），重新做任务碰碰新机遇吧。"
        return "作答无效，请点选当前这道题的选项。"
    q = res.quiz
    if not res.correct:
        return ("❌ 答错了。正确答案是 %d.%s" %
                (res.answer_idx + 1, q.options[res.answer_idx]) +
                "\n　这题没有奖励，继续修行再觅机缘。")
    p = await _player(store, qq)
    if not p:
        return "答题成功，但你尚未入道，奖励无处安放。"
    gain = _grant_reward(p, q.reward)
    await store.put_player(p)
    return "✅ 答对！%s。机缘不负有心人。" % gain


# ---------------------------------------------------------------------------
# 菜单（口令总览）
# ---------------------------------------------------------------------------
#: 菜单 = 唯一源，纯文本渲染直接从这里取
MENU_GROUPS = (
    ("入门 · 建立角色", (
        ("斗破菜单", "就是这条，随时回来看全部口令"),
        ("入道", "创建角色：斗之气一段，赠 10000 金币 + 玄重尺 + 回灵丹×5"),
        ("人物", "人物面板：境界星级 / 炼药师品阶 / 六维属性 / 装备"),
        ("帮助", "玩法细则：伤害、强化、炼药、异火、斗帝，全部数值"),
    )),
    ("修炼 · 涨修为升境界", (
        ("修炼", "结算离线修为（每分钟自动 +6，陨落心炎每级 +50%）"),
        ("突破", "修为满时晋升一星；102 级九星斗圣冲斗帝看机缘"),
        ("战斗", "随机遭遇一只同阶妖兽，回合制结算"),
        # 注意：MENU_GROUPS 必须保持**纯字面量**——tests/test_doupo_buttons.py 用
        # ast.literal_eval 静态解析它；写成 "…%d 秒" % cfg.CHOICE_TTL 会让 BinOp
        # 解析失败（4 条按钮门禁直接 ValueError）。改口径先改那条门禁。
        ("爬塔", "试炼之塔：无限层冲榜，每10层守塔者，宝箱极低概率掉神兵；"
                 "战利品直接回 1/2/3（1 分钟内）"),
        ("电梯", "花金币直达上次止步层，满状态重打；旧层已领奖励不重复"),
        ("塔层", "查看爬塔进度：层数 / 气血 / 增益 / 距下个守塔者"),
        ("塔结算", "收手结算本轮爬塔奖励（死亡也会自动结算）"),
        ("周副本", "7层一命通关；周一快照锁定难度，失败奖励全清含神兵"),
        ("周本状态", "看本周快照难度 / 当前层 / 暂存奖励 / 是否已通关"),
    )),
    ("养成 · 变强", (
        ("强化", "强化当前武器：等级对任何武器生效；玄重尺十级觉醒异火恒古尺"),
        ("斗技", "看四槽上阵卡；发「斗技 全部」出本宗斗技名录卡（含未解锁）"),
        ("斗技详情 狂狮怒罡", "带技名看完整说明；只发「斗技详情」也列出本宗全部斗技"),
        ("学斗技", "达等级+战力后随机领悟一门未学斗技（花金币）；也可「学习 <技名>」点名领悟，入道只给 1 门"),
        ("升级 龙吟碎", "用焚血散升斗技，每级伤害 +20%"),
        ("上阵 1 3 5 7", "按「斗技 全部」名录卡序号选四槽；也可打技名，异火主动技同样能上阵"),
        ("宗门", "看本门与可转宗门（进阶池 lv40 / 隐世池 lv90）"),
        ("转宗 迦南学院", "改换门庭，重配四槽（不发该派入道物品）"),
        ("异火", "看 5 种异火的主动技 / 被动、原著出处与喂养进度"),
        ("养火 青莲地心火", "用药渣喂养异火（炼药失败会出药渣）"),
        ("装备", "装备 <武器名>：切换持有的神兵（爬塔宝箱掉落）"),
    )),
    ("炼药 · 宝库", (
        ("宝库", "随身商店：药材 + 炎星石，看价"),
        ("买 血灵草 2", "买 2 份血灵草"),
        ("炼药", "看丹方：帝品 / 五品 / 四品"),
        ("炼制 回灵丹", "开炉炼丹，成功率看炼药师品阶，失败得药渣"),
        ("服丹 回灵丹", "服用丹药"),
        ("背包", "包裹（24 格）"),
    )),
    ("任务 · 江湖", (
        ("任务", "看主线进度与当前支线；途中概率触发机遇"),
        ("接任务", "随机接一条支线"),
        ("交任务", "支线达标后结算"),
        ("领主线", "主线达标后结算，推进章节"),
        ("排行", "本群战力榜前 10"),
        ("抢 @某人", "掠夺对方 10% 金币（输了自己掉）"),
        ("切磋 @某人", "无惩罚切磋：对方「应战」后自动随机放技，只记荣誉战绩"),
        ("应战", "接受本群道友的切磋邀请（1 分钟内有效）"),
        ("拒战", "婉拒切磋邀请"),
        ("送 100 @某人", "赠金币"),
    )),
)


def menu_total() -> int:
    return sum(len(rows) for _, rows in MENU_GROUPS)


def menu_text() -> str:
    out = [f"📖 斗破菜单 · 共 {menu_total()} 条口令（纯中文口令，菜单是唯一切换口）"]
    for group, rows in MENU_GROUPS:
        out.append("")
        out.append(f"【{group}】")
        for cmd, desc in rows:
            out.append(f"{cmd} —— {desc}")
    out.append("")
    out.append("⚠️ 与「修仙」重名的 商店/突破/任务/排行榜/炼丹 现在靠「当前游戏模式」分流，无需前缀。")
    return "\n".join(out)


def cmd_menu() -> str:
    return menu_text()


#: 玩法细则的**唯一源**：(分组名, [该组各行])。纯文本兜底 cmd_help 与图片卡
#: card.build_help_data 都从这里读，保证「图与文说的永远是一回事」。
#: 每组第一行为【 group】后接的首行（不带缩进），其余为缩进两格的细则行。
HELP_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("属性", (
        "力量=普攻，斗气=技能，敏捷→暴击+闪避，防御=减伤，体力=血量",
        "成长：值 = 基础×10 + 阶²×基础×10 + 基础×等级，阶 = 大境界序号（斗之气 0 → 斗帝 12）",
        "暴击% = 10 + (敏捷+30)/100，闪避% = 5 + (敏捷+20)/200",
    )),
    ("伤害", (
        "我方 = 攻击 × 800/(800+敌防)，暴击 ×1.5",
        "敌方 = 敌攻 × 800/(800+我方防御)",
    )),
    ("强化", (
        "强化等级属于「武器」而非某一把：换武器后等级继续生效",
        "L 级时属性 = 该武器基础×(1+0.1·L(L+1)/2)",
        "每次消耗炎星石 = L+1 块；L=9（界面 10 级）玄重尺硬编码 2000/2000/1000/1200",
        "玄重尺十级即「异火恒古尺」（其它武器不触发觉醒文案）",
    )),
    ("炼药", (
        "成功率 = 55% + 5%/品阶（最高 95%）；失败给药渣，药渣喂异火",
    )),
    ("异火", (
        "青莲地心火 斗气槽+25%/级；陨落心炎 经验+50%/级；",
        "三千焱炎火 吸血+2%/级；净莲妖火 暴击闪避+10%/级；帝炎 斗帝专属",
    )),
    ("斗帝", (
        "102 级九星斗圣冲 103 级：成功率 = 30% + 机缘/2000，帝炎 100%",
    )),
    ("斗技", (
        "三阶四槽：",
        "阶位分三档 —— 普通（黄阶/玄阶）· 高阶（地阶/天阶）· 特殊（秘法/血脉/体质/异火）",
        "上阵最多 4 门，三阶任意搭配（后期可四门全特殊）",
        "战斗与掠夺每回合在「已上阵且斗气够」的技里随机放一门",
        "斗气按阶扣：普通 20% ｜ 高阶 34% ｜ 特殊 50%（占 mp_max）",
        "斗气不够放任何一门 → 强制武器普攻，伤害大减且持续掉血",
        "升一级伤害 +20%（耗焚血散，最高 5 级）；同名技跨宗共享等级",
        "入道只领悟 1 门低阶技，其余靠「学斗技」逐个领悟：达解锁等级+战力门槛、花金币，随机得一门未学技",
        "领悟的技要用「上阵 <技名>」或「上阵 1 3 5 7」（名录卡序号）放进四槽才会自动释放；未领悟的技不能上阵",
        "解锁按原著阶位：黄/玄 lv1 ｜ 地阶 20/30/40 ｜ 天阶 50/60/70 ｜ 秘法 40 ｜ 体质血脉 50 ｜ 异火 60",
        "完整说明见「斗技详情 <技名>」（施展流程 + 数值 + 出处 + 原著章句）",
    )),
    ("宗门", (
        "入道按 QQ 定种子随机分宗，不可重掷：",
        "新手池 8 派 lv1 可进 ｜ 进阶池 11 派 lv40 ｜ 隐世池 6 派 lv90",
        "转宗价 = 3000 + 等级×200 金币；只换师门并重配四槽，",
        "不发该派入道物品；已升的技能等级按技名保留（转回去还在）",
        "少数门派原著斗技就少（蛇人族 / 毒宗 / 炼药师公会各 3 门、慕兰谷 2 门），",
        "进池时四槽填不满属正常，随等级解锁会补齐",
    )),
    ("挂机", (
        "每分钟自动 +6 修为，不登录也涨，不强制打卡。",
    )),
    ("机遇", (
        "推进任务进度时有 22% 概率撞见原版随机事件。",
        "天降炎星石 / 张大善人 / 神秘老者索丹 / 有奖竞猜 / 时空风暴…",
        "也有劫匪、深渊、女大王等破财事；金币不够时对方嫌你穷，不会倒扣。",
    )),
    ("选择", (
        "凡是要你回话的地方（爬塔战利品 / 竞猜选项 / 切磋应战）都直接回 1、2、3。",
        "限 %d 秒内作答；超时即失效，对应的奖励一并失去。" % cfg.CHOICE_TTL,
    )),
)


def cmd_help() -> str:
    """纯文本兜底，逐字沿用重构前文案（结构来自 HELP_GROUPS，与图片卡同源）。"""
    lines = ["📘 斗破玩法细则", ""]
    for gi, (group, rows) in enumerate(HELP_GROUPS):
        if gi:
            lines.append("")
        for ri, row in enumerate(rows):
            if ri == 0:
                lines.append("【%s】%s" % (group, row))
            else:
                lines.append("  " + row)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 入道 / 人物
# ---------------------------------------------------------------------------
async def cmd_enter(store, qq, name, gid) -> str:
    p = await _player(store, qq)
    if p:
        return "你已踏入斗气大陆，发送「人物」查看面板。"
    p = core.create_player(qq, name, gid)
    await store.put_player(p)
    # 私聊用 pv:<user_id> 虚拟作用域，不登记进「启用群」列表，避免污染群态
    if not str(gid).startswith("pv:"):
        await store.enable_group(str(gid))
    first = "、".join((p.get("learned") or [])[:1]) or "本宗基础技"
    return (
        "🔥 你自云岚山下睁眼，斗气绕体三周——\n"
        f"【{cfg.full_realm(1)}】\n"
        "手中握着那柄黑黝黝的巨尺「玄重尺」。\n"
        f"初入门径，只领悟了一门【{first}】——斗技要一门门学，不可贪多。\n\n"
        "发送「人物」看面板，「战斗」试刀，「学斗技」领悟新斗技，「帮助」看全部规则。"
    )


async def cmd_panel(store, qq) -> str:
    p = await _player(store, qq)
    if not p:
        return NOT_ENTERED
    core.settle(p)
    await store.put_player(p)

    st = core.stats(p)
    lv = int(p["lv"])
    need = cfg.exp_need(lv)
    exp_line = f"{_fmt(p['exp'])}/{_fmt(need)}" if need else f"{_fmt(p['exp'])}（已至巅峰）"
    alch = cfg.ALCHEMIST[int(p.get("alch", 0))]
    alch_need = alch[1]
    wv = core.weapon_panel(p)

    rows = [
        f"👤 【{p['name']}】",
        f"境界　{cfg.full_realm(lv)}",
        f"　　　{exp_line}　{_bar(int(p['exp']), need)}",
        f"炼药　{alch[0]}炼药师",
        f"　　　{_fmt(p.get('alch_exp', 0))}/{_fmt(alch_need) if alch_need else '—'}"
        f"　{_bar(int(p.get('alch_exp', 0)), alch_need)}",
        "",
        f"🗡️ {wv['name']}　{wv['level']} 级",
    ]
    wp = wv["stats"]
    rows.append(f"　力量 {wp['力量']}　敏捷 {wp['敏捷']}")
    rows.append(f"　斗气 {wp['斗气']}　防御 {wp['防御']}")
    role = cfg.STAT_BATTLE_ROLE
    rows += [
        "",
        f"　力量 {st['力量']}　敏捷 {st['敏捷']}　斗气 {st['斗气']}",
        f"　防御 {st['防御']}　机缘 {st['机缘']}　体力 {st['体力']}",
        "　战斗："
        + "／".join(f"{k}→{role[k]}" for k in cfg.PANEL_ORDER),
        "",
        f"❤️ 气血 {_bar(p['hp'], st['体力'])} {p['hp']}/{st['体力']}",
        f"💠 斗气 {_bar(p['mp'], core.mp_max(p))} {p['mp']}/{core.mp_max(p)}",
        f"💰 金币 {p['gold']}　💎 炎星石 {core.count_item(p, '炎星石')}",
        f"⚔️ 战力 {core.power(p)}（{p.get('wins', 0)}胜 {p.get('losses', 0)}负）",
        f"🤝 切磋荣誉 {int(p.get('duel_wins', 0))}胜 "
        f"{int(p.get('duel_losses', 0))}负（无惩罚，仅本群）",
    ]
    return "\n".join(rows)


# ---------------------------------------------------------------------------
# 修炼 / 突破
# ---------------------------------------------------------------------------
async def cmd_cultivate(store, qq) -> str:
    p = await _player(store, qq)
    if not p:
        return NOT_ENTERED
    res = core.settle(p)
    await store.put_player(p)
    lv = int(p["lv"])
    need = cfg.exp_need(lv)
    tip = "，可发送「突破」晋升" if core.can_level(p) else ""
    head = f"🧘 静修 {res['minutes']} 分钟，得修为 +{res['gain']}。" if res["gain"] else "🧘 你刚静修过，气机未动。"
    return (f"{head}\n修为 {_fmt(p['exp'])}/{_fmt(need) if need else '—'}{tip}\n"
            f"当前：{cfg.full_realm(lv)}")


async def cmd_break(store, qq) -> str:
    p = await _player(store, qq)
    if not p:
        return NOT_ENTERED
    core.settle(p)
    lv = int(p["lv"])
    if lv >= cfg.MAX_LV:
        await store.put_player(p)
        return "你已至斗帝之境，此界再无敌手。"
    if not core.can_level(p):
        need = cfg.exp_need(lv) - int(p["exp"])
        return f"修为不足，距下一星还差 {_fmt(need)}。去「战斗」或「修炼」。"
    res = core.level_up(p)
    await store.put_player(p)
    if res.get("tiandi"):
        return (
            "🌩️ 九霄雷动，天地齐鸣——\n"
            "🔥 你踏碎最后一层壁障，成就【斗帝】！\n"
            "帝炎自燃于体，全属性 ×1.2，机缘值全满。"
        )
    if res.get("reason") == "tiandi_fail":
        return (f"💥 冲击斗帝失败（成功率 {int(res['rate'] * 100)}%）！\n"
                "斗气倒灌，修为折损。多积机缘（支线任务 / 九幽散）再来。")
    # 文案把起点终点一起写清楚（用户 2026-11-14：「三星晋升到一星…这顺序不对」）。
    # 九星制下境界内是 三星→四星 这种正常递增，只有跨大境界才会星数归零，
    # 所以跨境界用「境界突破」措辞明确告知「这是换大境界，不是掉星」：
    #   同境界：突破成功！由【三星斗师】晋入【四星斗师】
    #   跨境界：境界突破！由【九星斗者】晋入【一星斗师】
    old_name = cfg.full_realm(lv)
    new_lv = int(p.get("lv", lv))
    new_name = res.get("name") or cfg.full_realm(new_lv)
    if cfg.realm_name(new_lv) != cfg.realm_name(lv):
        return f"✨ 境界突破！由【{old_name}】晋入【{new_name}】。气血斗气已满。"
    return f"✨ 突破成功！由【{old_name}】晋入【{new_name}】。气血斗气已满。"


# ---------------------------------------------------------------------------
# 战斗
# ---------------------------------------------------------------------------
async def cmd_fight(store, qq) -> str:
    p = await _player(store, qq)
    if not p:
        return NOT_ENTERED
    core.settle(p)
    now = time.time()
    left = int(cfg.FIGHT_CD_SEC - (now - float(p.get("last_fight", 0))))
    if left > 0:
        await store.put_player(p)
        return f"气血未复，需调息 {left} 秒。"

    # 一场遭遇 1~5 个敌人（魔兽 / 悍匪 / 原著反派），逐个调用单敌内核 fight。
    # 难度仍按玩家等级反解，不会出现越级秒杀；斗气跨战结转（见底转普攻）、
    # 气血结转+小幅回复。回满斗气只能靠突破/升级。
    res = core.encounter(p)
    p["last_fight"] = now
    chance = None
    if res["win"]:
        # 一次动作只走一个统一入口：推进支线+主线，并在途中掷**一次**机遇。
        # 多敌算多次击杀（任务进度按实际放倒数推进），药材同推 herb 任务。
        kinds = {"kill": int(res.get("enemy_count") or 1)}
        herb_n = sum(int(c) for n, c in (res.get("drops") or []) if n in cfg.HERB_NAMES)
        if herb_n > 0:
            kinds["herb"] = herb_n
        chance = core.advance_task(p, kinds)
    await store.put_player(p)

    # ---- 战报展示：固定骨架结算面板（2026-11-16 二次优化）----
    # 用户硬要求：敌人 1~5 随机，但字数/行数不能随敌人数膨胀；中间回合全折叠。
    # 故不再「每敌一节」，而是整场只取 1 条最强高光 + 固定几行结算数据。
    # 1 敌与 5 敌的骨架完全相同，只有「代表名等N名」处差几个字。
    detail = res.get("detail") or []
    n_total = len(res.get("enemies") or []) or int(res.get("enemy_count") or 1)

    # 跨整场汇总 log，只剪一条最强一击，其余中间回合一律折叠（不逐敌展开）。
    all_log: list[dict] = []
    for x in detail:
        for it in (x.get("log") or []):
            if isinstance(it, dict):
                all_log.append(it)
    hi, folded = _battle_highlights(all_log, limit=1)
    spot = _battle_line(hi[0], who="p").replace("　→ ", "", 1).strip() if hi else "未及出手"
    # 拍平「暴击(敏捷)」嵌套括号，结算面板一行读完不换行
    spot = spot.replace("暴击(敏捷)", "暴击·敏捷")

    # 敌人代表取等级最高的那位（原著反派优先露出名号），其余压成「等N名」。
    if detail:
        top = max(detail, key=lambda x: int(x.get("lv") or 0))
        foes = str(top.get("name") or "敌手")
        if n_total > 1:
            foes += f"等{n_total}名"
    else:
        foes = f"{n_total}名敌手"
    killed = sum(1 for x in detail if x.get("win"))
    verdict = "大捷" if res["win"] else f"力竭·斩{killed}/{n_total}"

    # 「防御」字样固定出现在代价行：属性↔战斗强关联的稳定锚点，
    # 即使最强一击恰好是「贵人相助」（无属性括注）也不会丢掉归因。
    lines = [
        f"⚔️ 遭遇 {n_total} 敌 · {res['rounds']}回合 · {verdict}",
        f"💥 {spot}",
        f"🎯 斩获 {foes}",
        f"🩸 代价 防御减伤后受伤 {_fmt(res['taken'])}　气血 {_fmt(p.get('hp') or 0)}",
        f"💰 收益 修为 +{_fmt(res['exp'])}　金币 +{_fmt(res['gold'])}",
    ]
    if folded > 0:
        lines.insert(2, f"⋯ 略去中间 {folded} 式常规攻防")
    drops = list(res.get("drops") or [])
    if drops:
        lines.append("🎁 战利 " + "、".join(f"{n}×{c}" for n, c in drops[:3]))

    if res.get("hero_aid"):
        lines.append("🍀 机缘际会，贵人相助——此战毫发无伤")
    lv = int(p["lv"])
    if core.can_level(p):
        lines.append(f"⬆️ 修为已满（{cfg.full_realm(lv)}），发送「突破」晋升")
    lines.extend(_chance_lines(chance, qq))
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 强化
# ---------------------------------------------------------------------------
async def cmd_enhance(store, qq) -> str:
    p = await _player(store, qq)
    if not p:
        return NOT_ENTERED
    pv = core.enhance_preview(p)
    if int(p.get("weapon", 0)) > 8:
        return f"🗡️ {core.weapon_panel(p)['name']} 已至十级巅峰，无法再强化。"

    head = [
        f"🗡️ {core.weapon_panel(p)['name']}",
        f"　{pv['disp']} 级 ↗ {pv['next_disp']} 级",
        f"　力量 ↗{pv['next']['力量'] - pv['cur']['力量']}　"
        f"敏捷 ↗{pv['next']['敏捷'] - pv['cur']['敏捷']}",
        f"　斗气 ↗{pv['next']['斗气'] - pv['cur']['斗气']}　"
        f"防御 ↗{pv['next']['防御'] - pv['cur']['防御']}",
        f"本次强化需要 {pv['cost']} 块炎星石（现有 {core.count_item(p, '炎星石')}）",
    ]
    res = core.enhance(p)
    chance = core.advance_task(p, "weapon", 1) if res.get("ok") else None
    await store.put_player(p)
    if not res.get("ok"):
        head.append("❌ 炎星石不足，无法强化。")
        head.append("　（打怪掉落 / 「买 炎星石」）")
        return "\n".join(head)

    head.append("✅ 强化成功！")
    if res["evolved"]:
        head.append("🔥 玄重尺寸寸崩裂，黑铁尽褪——【异火恒古尺】现世！")
        head.append("　力量 2000　敏捷 2000　斗气 1000　防御 1200")
    else:
        head.append(f"　现为 {res['display']} 级")
    head.extend(_chance_lines(chance, qq))
    return "\n".join(head)


# ---------------------------------------------------------------------------
# 斗技 / 异火
# ---------------------------------------------------------------------------
async def cmd_skills(store, qq) -> str:
    p = await _player(store, qq)
    if not p:
        return NOT_ENTERED
    pool = core.skill_pool(p)
    lo = core.loadout_entries(p)
    fac_name = p.get("faction_name") or "无宗门"
    own = sum(1 for e in pool if e.get("origin") == "本宗")
    common = sum(1 for e in pool if e.get("origin") == "通用")
    out = [f"📜 {fac_name} · 斗技三阶（本宗 {own} / 通用 {common} · 上阵 {len(lo)}/{cfg.LOADOUT_SIZE}）"]
    for tier in cfg.SKILL_TIERS:
        rows = [e for e in pool if e["tier"] == tier and e.get("faction") != "异火"]
        if not rows:
            continue
        out.append(f"〔{tier}〕斗气 {int(cfg.SKILL_TIER_MP_FRAC[tier] * 100)}%·mp_max")
        for e in rows:
            lv = int((p.get("skills") or {}).get(e["name"], 1))
            mark = "★上阵" if e["equipped"] else ("可学" if e["unlocked"] else "🔒未解锁")
            tag = "〔通用〕" if e.get("origin") == "通用" else ""
            selfmade = "〔自设〕" if e.get("core") else ""
            out.append(f"　{tag}{selfmade}{e['name']} Lv{lv}（lv{e['unlock']} 解锁·{mark}）")
    flames = [e for e in pool if e.get("faction") == "异火"]
    if flames:
        out.append(f"〔异火技〕斗气 {int(cfg.SKILL_TIER_MP_FRAC['特殊'] * 100)}%·mp_max　收服即解锁")
        for e in flames:
            mark = "★上阵" if e["equipped"] else "可学"
            out.append(f"　{e['name']}（{e.get('flame', '异火')}·{mark}）")
    out.append("")
    out.append("上阵：" + "、".join(f"{e['name']}[{e['tier']}]" for e in lo))
    out.append("　〔通用〕= 斗技堂/拍卖会流通的基础斗技，非本宗传承；〔自设〕= 原著无载的游戏补白")
    out.append("　炼药、丹会、品阶考核、大长老院表决等是宗门典故，不是斗技，已不在此列")
    out.append("　斗气不足将强制武器普攻（伤害大减、持续掉血）")
    out.append(f"　升一级伤害 +20%，消耗 {cfg.SKILL_UP_ITEM}；用法：升级 <技名> / 上阵 <4 技名>")
    return "\n".join(out)


async def cmd_skill_info(store, qq, name) -> str:
    """斗技详情：完整说明（施展→效果→增益/副作用→限制）+ 数值 + 来源 + 原著。

    desc 写在 skill_book.SKILL_BOOK 里，与图鉴文档同源；卡片只放两行短释，
    想看全的就走这条口令。
    """
    p = await _player(store, qq)
    if not p:
        return NOT_ENTERED
    name = (name or "").strip()
    pool = core.skill_pool(p)
    if not pool:
        return "你还没有斗技。"
    if not name:
        return ("用法：斗技详情 <技名>\n本宗可选：" + "、".join(e["name"] for e in pool))

    hit = next((e for e in pool if e["name"] == name), None) or next(
        (e for e in pool if name in e["name"] or e["name"] in name), None)
    if not hit:
        book_hit = book.get(name)
        if book_hit is None:
            return "无此斗技。本宗可选：" + "、".join(e["name"] for e in pool)
        return f"【{book_hit['name']}】不属于你的宗门，无法查看细节。"

    lv = int((p.get("skills") or {}).get(hit["name"], 1))
    st = core.stats(p)
    raw = (st["力量"] * 0.5 + st["斗气"] * 1.5) * float(hit["dmg"]) * (1 + 0.2 * (lv - 1))
    dmg = int(round(raw * (1 + core.crit_rate(p) / 100.0 * 0.5)))
    state = "★上阵" if hit.get("equipped") else ("可学" if hit.get("unlocked") else "未解锁")

    # 只有真打得出去的技才显示伤害系数；功法/增益/技艺的 130% 是倍率不是伤害，写出来会误导
    nfmt = f"斗气 {int(hit['mp'] * 100)}%·mp_max ｜ 冷却 {hit.get('cd', '-')} 回合"
    if hit.get("kind") in ("攻击", "控制", "召唤", "秘法"):
        nfmt += f" ｜ 基础 {int(hit['dmg'] * 100)}% ｜ 当前约 {dmg} 伤害"
    else:
        nfmt += " ｜ 非直接伤害技（增益/功能）"
    out = [f"📖 {hit['name']}　Lv{lv}　{state}",
           f"{hit.get('rank', '')} · {hit.get('kind', '')} · {hit.get('scope', '')}［{hit['tier']}］",
           nfmt,
           ""]
    out.append(hit.get("desc") or "（暂无说明）")
    if hit.get("source"):
        out += ["", f"出处：{hit['source']}"]
    if hit.get("novel"):
        out.append(f"原著：{hit['novel']}")
    out += ["", f"　升级 +20%/级（需 {cfg.SKILL_UP_ITEM}）｜ 用法：升级 {hit['name']}"]
    return "\n".join(out)


async def cmd_faction(store, qq, args) -> str:
    """宗门：查看本门与可转宗门；`转宗 <宗门名>` 改换门庭。

    为什么要有转宗：入道只能进新手池 8 派，进阶池（lv40）与隐世池（lv90）
    共 17 派的斗技没有转宗就永远见不到。
    """
    p = await _player(store, qq)
    if not p:
        return NOT_ENTERED
    POOL_CN = {"starter": "新手池", "advanced": "进阶池", "hidden": "隐世池"}
    name = p.get("faction_name") or "无宗门"
    lv = int(p.get("lv", 1))
    key = (args or "").strip()

    if key:
        res = core.change_faction(p, key)
        if not res.get("ok"):
            r = res.get("reason")
            if r == "same":
                return f"你已在【{res['name']}】，无需转宗。"
            if r == "gold":
                return (f"❌ 转宗需 {res['need']:,} 金币，你有 {res['have']:,}。\n"
                        f"　（境界越高，越难改换门庭）")
            if r == "nopool":
                return "当前境界还没有可转的宗门。"
            if r == "notfound":
                return ("当前境界没有这个宗门。可转："
                        + "、".join(res.get("options") or ["（无）"]))
            return "转宗失败，请检查宗门名。"
        await store.put_player(p)
        return (f"🏯 已转投【{res['name']}】（原【{res['old']}】），耗 {res['cost']:,} 金币。\n"
                f"　四槽已按新门重配：" + "、".join(res["loadout"])
                + "\n　已升的技能等级按技名保留；本门斗技见「斗技」。")

    fac_name = p.get("faction") or ""
    cur_pool = ""
    try:
        cur_pool = fac.FACTIONS.get(fac_name, {}).get("pool", "")
    except Exception:  # noqa: BLE001
        cur_pool = ""
    out = [f"🏯 宗门：{name}"
           + (f"（{POOL_CN.get(cur_pool, cur_pool)}）" if cur_pool else "")]
    pools = core.faction_pools_open(p)
    out.append(f"境界 lv{lv} ｜ 已开放卡池：" + " ".join(POOL_CN[x] for x in pools))

    cands = core.faction_candidates(p)
    if not cands:
        out.append("当前无可转宗门。")
        return "\n".join(out)
    out.append("")
    out.append(f"可转宗门（需 {core.faction_switch_cost(p):,} 金币）：")
    for pool in ("hidden", "advanced", "starter"):
        rows = [c for c in cands if c["pool"] == pool]
        if not rows:
            continue
        out.append(f"〔{POOL_CN[pool]}〕")
        line = "　"
        for c in rows:
            seg = f"{c['name']} {c['skills']}门"
            if len(line) + len(seg) > 30:
                out.append(line)
                line = "　"
            line += seg + " · "
        if line.strip():
            out.append(line.rstrip(" ·"))
    out.append("")
    out.append("用法：转宗 <宗门名>")
    out.append("　只换师门并重配四槽，不发该派入道物品；已升技能等级按技名保留。")
    return "\n".join(out)


async def cmd_skill_up(store, qq, name) -> str:
    p = await _player(store, qq)
    if not p:
        return NOT_ENTERED
    name = (name or "").strip()
    # 新模型：只能升级已领悟的技；老档 learned_names 返回全部可学技（兼容）
    from . import skills as _sk
    names = list(dict.fromkeys(_sk.learned_names(p))) or [e["name"] for e in core.skill_pool(p)]
    if not name:
        return "用法：升级 <技名>（可选：" + "、".join(names) + "）"
    hit = next((n for n in names if n == name), None) or next(
        (n for n in names if name in n or n in name), None)
    if not hit:
        hit = next((n for n in cfg.SKILL_NAMES if name in n or n in name), None)
    if not hit:
        return "无此斗技。可选：" + "、".join(names)
    res = core.skill_up(p, hit)
    await store.put_player(p)
    if not res.get("ok"):
        if res.get("reason") == "max":
            return f"【{hit}】已至 {res['lv']} 级圆满。"
        if res.get("reason") == "noskill":
            return f"你还没领悟【{hit}】——先发「学斗技」领悟，或「斗技详情」看本宗能学什么。"
        return (f"❌ {res.get('item', cfg.SKILL_UP_ITEM)}不足：需要 {res['need']} 份，"
                f"你有 {res['have']}。\n　去「炼制 {cfg.SKILL_UP_ITEM}」自己炼。")
    return f"✅【{hit}】升至 {res['lv']}/{res['max']} 级，技能伤害再进一层。"


def _migration_note(res: dict) -> str:
    """老档首次学斗技时的迁移提示（保留原有技，只是补齐新玩法字段）。"""
    if res.get("migrated") is None:
        return ""
    n = len(res["migrated"])
    return f"📜 存档已升级：保留你在用的 {n} 门斗技，现在可以继续逐一领悟了。\n"


async def cmd_insight(store, qq) -> str:
    """学斗技：达到等级+战力后随机领悟一门未学斗技。"""
    p = await _player(store, qq)
    if not p:
        return NOT_ENTERED
    res = core.insight(p)
    note = _migration_note(res)
    if not res.get("ok"):
        # 老档迁移即使本次没学成也要落盘（learned 已写入 p），否则下次又当老档
        if res.get("migrated") is not None:
            await store.put_player(p)
        why = res.get("reason")
        if why == "level":
            return note + (f"本宗斗技已领悟到当前境界上限，升到 Lv.{res['need_lv']} "
                           f"再回来（你现在 Lv.{res['lv']}）。")
        if why == "power":
            return note + (f"战力不足，领悟下一门需战力 {res['need']}，你现在 {res['have']}。"
                           "去「战斗」「强化」提升战力再来。")
        if why == "gold":
            return note + (f"金币不足，领悟需 {res['need']} 金，你现在 {res['have']} 金。"
                           "去「战斗」赚金币。")
        return note + "已领悟本宗全部斗技，无可再学。"
    await store.put_player(p)
    learned = p.get("learned") or []
    out = [
        note.rstrip("\n"),
        f"✨ 福至心灵——领悟了【{res['name']}】（{res['tier']}斗技）！",
        f"　耗金 {res['cost']}，已领悟 {res['count']} 门。",
    ]
    if len(learned) < cfg.LOADOUT_SIZE:
        need = cfg.LOADOUT_SIZE - len(learned)
        out.append(f"　四槽还有 {need} 个空位，发「上阵 {res['name']}」把它带上场。")
    else:
        out.append("　四槽已满，可用「上阵 1 3 5 7」（名录卡序号）调整。")
    return "\n".join(x for x in out if x)


async def cmd_learn_named(store, qq, name) -> str:
    """学习 <技名>：定向领悟玩家点名的那门斗技（与学斗技同门槛）。"""
    p = await _player(store, qq)
    if not p:
        return NOT_ENTERED
    name = str(name or "").strip()
    if not name:
        return "要学哪门斗技？例：「学习 风灵分形剑」。发「斗技 全部」看名录。"
    res = core.learn_named(p, name)
    note = _migration_note(res)
    if not res.get("ok"):
        if res.get("migrated") is not None:
            await store.put_player(p)
        why = res.get("reason")
        if why == "noskill":
            return note + f"本宗没有【{name}】这门斗技，发「斗技 全部」看名录。"
        if why == "learned":
            return note + f"【{name}】你已经领悟了，发「上阵 {name}」把它带上场。"
        if why == "level":
            return note + (f"【{name}】要 Lv.{res['need_lv']} 才能领悟"
                           f"（你现在 Lv.{res['lv']}）。")
        if why == "power":
            return note + (f"战力不足，领悟【{name}】需战力 {res['need']}，"
                           f"你现在 {res['have']}。去「战斗」「强化」提升再来。")
        if why == "gold":
            return note + (f"金币不足，领悟【{name}】需 {res['need']} 金，"
                           f"你现在 {res['have']} 金。去「战斗」赚金币。")
        return note + "暂无法领悟这门斗技。"
    await store.put_player(p)
    learned = p.get("learned") or []
    out = [
        note.rstrip("\n"),
        f"✨ 福至心灵——领悟了【{res['name']}】（{res['tier']}斗技）！",
        f"　耗金 {res['cost']}，已领悟 {res['count']} 门。",
    ]
    if len(learned) < cfg.LOADOUT_SIZE:
        out.append(f"　发「上阵 {res['name']}」把它带上场。")
    return "\n".join(x for x in out if x)


async def cmd_loadout(store, qq, args) -> str:
    """上阵：从本宗三阶斗技里自由选 ≤4 门。"""
    p = await _player(store, qq)
    if not p:
        return NOT_ENTERED
    pool = [e for e in core.skill_pool(p) if e["unlocked"]]
    raw = (args or "").replace("，", ",").replace("、", ",").replace("/", ",").replace(" ", ",")
    names = [x.strip() for x in raw.split(",") if x.strip()]
    # 名录卡序号 → 技名（2026-11-16 用户「加序号方便输出上阵口令」）：
    # 序号口径与 doupo_roster 卡上的圆标号严格同源（core.roster_no_map）。
    # 纯数字 token 先翻译成技名，再走下面既有的解锁/去重校验，未解锁照样被拒。
    no_map = core.roster_no_map(p)
    # 序号 token 兼容多种用户写法：3 / 技3 / 按3 / 第3 / 3号 / 技3号。
    # 「技N」来自旧提示「上阵 技1 技2 技3 技4」与菜单卡历史文案；用户照着打
    # 或点按钮拼出「技3」都必须能上阵，不能当成技名去找（2026-11-16 实测事故）。
    no_re = re.compile(r"^(?:技|按|第)?\s*(\d+)\s*号?$")
    translated: list[str] = []
    for tok in names:
        m = no_re.match(tok)
        if m:
            name = no_map.get(int(m.group(1)))
            translated.append(name if name else tok)  # 越界序号保留原样→下面报不可上阵
        else:
            translated.append(tok)
    names = translated
    if not names:
        cur = core.loadout_entries(p)
        return ("当前上阵：" + "、".join(f"{e['name']}[{e['tier']}]" for e in cur)
                + "\n可上阵：" + "、".join(f"{i}.{e['name']}" for i, e in enumerate(
                    core.skill_pool_sorted(p), 1))
                + f"\n用法：上阵 1 3 5 7（名录卡序号）或 <技名>（最多 {cfg.LOADOUT_SIZE} 门）")
    chosen = []
    for n in names:
        hit = next((e for e in pool if e["name"] == n), None) or next(
            (e for e in pool if n in e["name"] or e["name"] in n), None)
        if not hit:
            return f"「{n}」不可上阵（未解锁或非本宗）。可上阵：" + "、".join(e["name"] for e in pool)
        if hit["name"] not in chosen:
            chosen.append(hit["name"])
    if len(chosen) > cfg.LOADOUT_SIZE:
        return f"最多上阵 {cfg.LOADOUT_SIZE} 门，你给了 {len(chosen)} 门。"
    # 未领悟的技不能上阵——明确提示，不要静默过滤成默认四槽。
    # 用户照名录卡序号选时，「可学」态（已解锁未领悟）最常见，必须讲清下一步。
    owned = core.equippable_names(p)
    unlearned = [n for n in chosen if n not in owned]
    if unlearned:
        tips = []
        for n in unlearned:
            no = next((i for i, nm in no_map.items() if nm == n), None)
            tips.append(f"{n}（名录卡 {no} 号）" if no else n)
        return ("还没领悟：" + "、".join(tips) + "。先发「学斗技」或「学习 "
                + unlearned[0] + "」领悟后再上阵。")
    core.set_loadout(p, chosen)
    await store.put_player(p)
    return "✅ 已上阵：" + "、".join(f"{e['name']}[{e['tier']}]" for e in core.loadout_entries(p))


def has_tradition_flame(p: dict) -> bool:
    """本宗是否有传承异火（与宗门卡/异火卡同一口径：factions[fid]['flames']）。

    2026-11-16 用户硬要求：无传承异火的宗门，「异火」口令只给纯文本一句提示，
    不渲染卡片、不挂按钮；有传承才展示完整异火面板供收服/养成。
    """
    fid = str(p.get("faction") or "")
    return bool(list(fac.FACTIONS.get(fid, {}).get("flames", []) or []))


def no_tradition_flame_msg(p: dict) -> str:
    """无传承异火时的纯文本提示（无图无按钮）。"""
    fid = str(p.get("faction") or "")
    fname = str(p.get("faction_name") or fac.FACTIONS.get(fid, {}).get("name") or "本宗")
    return f"🔥 {fname}本宗无传承异火，暂无异火可展示或养成。"


async def cmd_flames(store, qq) -> str:
    p = await _player(store, qq)
    if not p:
        return NOT_ENTERED
    # 2026-11-16 用户硬要求：无传承异火 → 纯文本一句，不出图不挂按钮。
    if not has_tradition_flame(p):
        return no_tradition_flame_msg(p)
    fl = p.get("flames") or {}
    fid = str(p.get("faction") or "")
    trad = list(fac.FACTIONS.get(fid, {}).get("flames", []) or [])
    fname = str(p.get("faction_name") or fac.FACTIONS.get(fid, {}).get("name") or "本宗")
    out = [f"🔥 异火　药渣 ×{core.count_item(p, '药渣')}"]
    out.append(f"　【{fname}·本宗传承】")
    for t in trad:
        out.append(f"　　{t}")
    out.append("　以下为游历中可自行收服、喂养的异火（与本宗传承无关）：")
    for f in cfg.FLAMES:
        name = f["name"]
        lv = int(fl.get(name, 0))
        if name == cfg.FLAME_DIYAN:
            state = "已收服" if p.get("diyan") else "未得（成就斗帝自燃）"
            out.append(f"　{f['icon']} {name}　{state}")
        elif lv >= cfg.FLAME_MAX_LV:
            out.append(f"　{f['icon']} {name}　{lv} 级（圆满）")
        else:
            need = int(f["need"]) * (lv + 1)
            out.append(f"　{f['icon']} {name}　{lv}/{cfg.FLAME_MAX_LV} 级　下级需 {_fmt(need)} 药渣")
        a = fs.active_of(name)
        pv = fs.passive_of(name)
        if a:
            out.append(f"　　主动【{a['name']}】{a['brief']}")
        if pv:
            out.append(f"　　被动【{pv['name']}】{pv['effect']}")
        if a:
            cite = a["novel"].split(" —— ")[0].split("；")[0]
            out.append(f"　　原著：{cite}")
    out.append("")
    out.append("　收服后主动技可用「上阵 <技名>」放进四槽，「斗技详情 <技名>」看全文与出处")
    out.append("　用法：养火 青莲地心火（一次喂光现有药渣）")
    return "\n".join(out)


async def cmd_feed(store, qq, name) -> str:
    p = await _player(store, qq)
    if not p:
        return NOT_ENTERED
    name = (name or "").strip()
    if not name:
        return "用法：养火 青莲地心火（可选：" + "、".join(cfg.FLAME_NAMES) + "）"
    hit = next((n for n in cfg.FLAME_NAMES if name in n or n in name), None)
    if not hit:
        return "无此异火。可选：" + "、".join(cfg.FLAME_NAMES)
    res = core.feed_flame(p, hit)
    chance = core.advance_task(p, "flame", 1) if res.get("ok") else None
    await store.put_player(p)
    if not res.get("ok"):
        if res.get("reason") == "nodross":
            return "你没有药渣。炼药失败会产药渣——去「炼制」试试。"
        if res.get("reason") == "diyan":
            return "帝炎不靠喂养，只认斗帝。"
        if res.get("reason") == "max":
            return f"【{hit}】已至 {res['lv']} 级圆满，再多药渣也烧不动了。"
        return "无可喂养。"
    if res["done"]:
        out = [f"🔥 药渣尽数化入【{hit}】，晋为 {res['lv']} 级！"]
    else:
        out = [f"🔥 投入 {res['give']} 份药渣，{hit} 距下一级还差 "
               f"{_fmt(res['left'])}。"]
    out.extend(_chance_lines(chance, qq))
    return "\n".join(out)


# ---------------------------------------------------------------------------
# 宝库 / 炼药 / 背包
# ---------------------------------------------------------------------------
async def cmd_shop(store, qq) -> str:
    p = await _player(store, qq)
    if not p:
        return NOT_ENTERED
    out = [f"🏦 随身商店　💰 {p['gold']}"]
    for it in core.shop_list():
        out.append(f"　{it['name']}　{it['price']} 金")
        out.append(f"　　{it['desc']}")
    out.append("")
    out.append("　用法：买 血灵草 2")
    return "\n".join(out)


async def cmd_buy(store, qq, args) -> str:
    p = await _player(store, qq)
    if not p:
        return NOT_ENTERED
    args = (args or "").strip()
    if not args:
        return "用法：买 血灵草 2（发「宝库」看货）"
    parts = args.split()
    name = parts[0]
    n = 1
    if len(parts) > 1 and parts[1].isdigit():
        n = max(1, min(99, int(parts[1])))
    else:
        # 支持「买 血灵草2」这种没空格的
        for it in core.shop_list():
            if args.startswith(it["name"]):
                name = it["name"]
                rest = args[len(it["name"]):].strip()
                if rest.isdigit():
                    n = max(1, min(99, int(rest)))
                break
    res = core.shop_buy(p, name, n)
    # 采办药材也算推进「药农之托」一类 herb 任务（买炎星石不算）。
    herb_n = (int(res.get("n") or 0)
              if (res.get("ok") and res.get("name") in cfg.HERB_NAMES) else 0)
    chance = core.advance_task(p, "herb", herb_n) if herb_n else None
    await store.put_player(p)
    if not res.get("ok"):
        if res.get("reason") == "poor":
            return f"💰 金币不足：需要 {res['need']}，你有 {res['have']}。"
        return f"没有【{name}】这样货。发「宝库」看全部。"
    out = [f"✅ 购入【{res['name']}】×{res['n']}，花费 {res['total']} 金，余 {p['gold']}。"]
    out.extend(_chance_lines(chance, qq))
    return "\n".join(out)


async def cmd_recipes(store, qq, page=1) -> str:
    """丹方清单。等级不够的丹药不展示；丹药多了分页（与卡片同源）。"""
    p = await _player(store, qq)
    if not p:
        return NOT_ENTERED
    alch = cfg.ALCHEMIST[int(p.get("alch", 0))]
    chunk, page, total_pages = core.recipes_page(p, page)
    avail = core.available_recipes(p)
    out = [f"⚗️ 炼药　{alch[0]}炼药师（成功率 {int(cfg.craft_rate(int(p.get('alch', 0))) * 100)}%）",
           f"　境界 {cfg.full_realm(int(p.get('lv', 1)))} · 可用丹方 {len(avail)} 枚 · 第 {page}/{total_pages} 页"]
    for grade in cfg.GRADE_ORDER:
        rows = [r for r in chunk if r["grade"] == grade]
        if not rows:
            continue
        out.append(f"【{grade}丹药】")
        for r in rows:
            need = r.get("need") or {}
            if need:
                mats = "、".join(f"{k}×{v}" for k, v in need.items())
                ok = all(core.count_item(p, k) >= v for k, v in need.items())
                mark = "✅" if ok else "　"
                out.append(f"　{mark}{r['name']}　（{mats}）")
            else:
                out.append(f"　{r['name']}　（{int(r.get('buy', 0))} 金购买）")
            out.append(f"　　{r['desc']}")
    out.append("")
    if total_pages > 1:
        out.append(f"　发「炼药2」…「炼药{total_pages}」翻页，或点下方按钮")
    out.append("　用法：炼制 回灵丹（有药方）／买 丹名（无药方）")
    return "\n".join(out)


async def cmd_craft(store, qq, name) -> str:
    p = await _player(store, qq)
    if not p:
        return NOT_ENTERED
    name = (name or "").strip()
    if not name:
        return "用法：炼制 回灵丹（发「炼药」看全部丹方）"
    hit = next((n for n in cfg.RECIPE_NAMES if name in n or n in name), None)
    if not hit:
        return "无此丹方。发「炼药」看全部。"
    res = core.craft(p, hit)
    if res.get("reason") == "buyonly":
        return f"【{hit}】原著未载药方，无法炼制——去「宝库」或直接「买 {hit}」（{res['buy']} 金）。"
    if res.get("reason") == "cd":
        await store.put_player(p)
        return f"丹炉尚温，需再候 {res['left']} 秒。"
    chance = core.advance_task(p, "craft", 1) if res.get("ok") else None
    await store.put_player(p)
    if not res.get("ok"):
        if res.get("reason") == "mat":
            return "❌ 材料不足：" + "、".join(res["missing"]) + "\n　去「宝库」买药。"
        if res.get("reason") in ("fail",):
            msg = (f"💨 炼制失败（成功率 {int(res['rate'] * 100)}%）——\n"
                   f"　药渣 +{res['dross']}，可拿去「养火」。")
            if res.get("alch_up"):
                msg += f"\n　炼药经验积累，晋为【{res['alch_up']}炼药师】！"
            return msg
        return "炼制失败。"
    msg = f"⚗️ 丹成！【{hit}】×1（成功率 {int(res['rate'] * 100)}%）"
    if res.get("alch_up"):
        msg += f"\n　炼药经验积累，晋为【{res['alch_up']}炼药师】！"
    out = [msg]
    out.extend(_chance_lines(chance, qq))
    return "\n".join(out)


async def cmd_bag(store, qq) -> str:
    p = await _player(store, qq)
    if not p:
        return NOT_ENTERED
    items = core.bag_slots(p)
    out = [f"🎒 包裹（{len(items)}/24 格）　💰 {p['gold']}"]
    known = {r["name"]: r["desc"] for r in cfg.RECIPES}
    known.update({h["name"]: h["desc"] for h in cfg.HERBS})
    if not items:
        out.append("　空空如也。")
    for i, (name, n) in enumerate(items[:24]):
        out.append(f"　[{i + 1}] {name} ×{n}")
        if name in known:
            out.append(f"　　　{known[name]}")
    out.append("")
    out.append("　用法：服丹 回灵丹")
    return "\n".join(out)


async def cmd_use(store, qq, name) -> str:
    p = await _player(store, qq)
    if not p:
        return NOT_ENTERED
    name = (name or "").strip()
    if not name:
        return "用法：服丹 回灵丹"
    hit = next((n for n in cfg.RECIPE_NAMES if name in n or n in name), None)
    if not hit:
        return "无此丹药。"
    res = core.use_item(p, hit)
    await store.put_player(p)
    if not res.get("ok"):
        if res.get("reason") == "none":
            return f"你没有【{hit}】。去「炼制 {hit}」或「宝库」看看。"
        return res.get("msg") or "无法服用。"
    return f"💊 服下【{hit}】，{res['msg']}"


# ---------------------------------------------------------------------------
# 任务
# ---------------------------------------------------------------------------
async def cmd_tasks(store, qq) -> str:
    p = await _player(store, qq)
    if not p:
        return NOT_ENTERED
    out = ["📜 任务"]
    m = core.current_main(p)
    if m:
        cur = int(p.get("main_progress", 0))
        out.append(f"【主线·{m['name']}】{m['desc']}")
        out.append(f"　进度 {cur}/{m['target']}　{_bar(cur, m['target'])}")
        rw = m["reward"]
        out.append(f"　奖励：{rw['gold']} 金 / {rw['exp']} 修为 / 机缘 +{rw['luck']}")
    else:
        out.append("【主线】已全部完成。")
    t = p.get("task")
    if t:
        cur = int(t.get("progress", 0))
        out.append(f"【支线·{t['name']}】{t['desc']}")
        out.append(f"　进度 {cur}/{t['target']}　{_bar(cur, t['target'])}")
    else:
        out.append("【支线】无。发送「接任务」接一条。")
    return "\n".join(out)


async def cmd_task_accept(store, qq) -> str:
    p = await _player(store, qq)
    if not p:
        return NOT_ENTERED
    res = core.task_accept(p)
    await store.put_player(p)
    if not res.get("ok"):
        t = res["task"]
        return f"你手上还有支线未交：{t['name']}（{t.get('progress', 0)}/{t['target']}）"
    t = res["task"]
    return f"📌 接取支线【{t['name']}】：{t['desc']}（0/{t['target']}）"


async def cmd_task_submit(store, qq) -> str:
    p = await _player(store, qq)
    if not p:
        return NOT_ENTERED
    res = core.task_submit(p)
    if not res.get("ok"):
        if res.get("reason") == "none":
            return "你没有在做的支线。发送「接任务」接一条。"
        t = res["task"]
        return f"尚未完成：{t['name']}（{t.get('progress', 0)}/{t['target']}）"
    rw = res["reward"]
    return (f"✅ 支线【{res['task']['name']}】完成！\n"
            f"　金币 +{rw['gold']}　修为 +{rw['exp']}　机缘 +{rw['luck']}")


async def cmd_main_submit(store, qq) -> str:
    p = await _player(store, qq)
    if not p:
        return NOT_ENTERED
    res = core.main_submit(p)
    if not res.get("ok"):
        if res.get("reason") == "done":
            return "主线已全部完成，你已是此界巅峰。"
        t = res["task"]
        return f"主线未完成：{t['name']}（{res.get('progress', 0)}/{t['target']}）"
    rw = res["reward"]
    msg = (f"🏅 主线【{res['task']['name']}】完成！\n"
           f"　金币 +{rw['gold']}　修为 +{rw['exp']}　机缘 +{rw['luck']}")
    if res.get("next"):
        msg += f"\n📖 下一章：{res['next']['name']} —— {res['next']['desc']}"
    else:
        msg += "\n🎉 主线全部走完。"
    return msg


# ---------------------------------------------------------------------------
# 排行 / 抢 / 送
# ---------------------------------------------------------------------------
async def cmd_rank(store, gid) -> str:
    ps = await store.all_players(str(gid))
    if not ps:
        return "本群尚无人踏入斗气大陆，发送「入道」！"
    ps = core.rank(ps)
    out = ["🏆 本群战力榜（前 10）"]
    for i, q in enumerate(ps[:10], 1):
        out.append(f"{i}. {q['name']}　{cfg.full_realm(int(q['lv']))}　战力 {core.power(q)}")
    return "\n".join(out)


async def cmd_rob(store, qq, target) -> str:
    p = await _player(store, qq)
    if not p:
        return NOT_ENTERED
    if not target:
        return "用法：抢 @某人"
    vic = await store.get_player(str(target))
    if not vic:
        return "对方尚未入道。"
    if int(vic.get("gold", 0)) <= 0:
        return "对方囊中空空。"
    # 掠夺也走四槽：双方各用自己上阵的 4 门斗技随机互殴（核心模块骨架）
    res = core.duel(p, vic)
    seq = res["log"]
    show = seq[:4] + (["..."] if len(seq) > 6 else []) + (seq[-2:] if len(seq) > 6 else [])
    lines = [f"⚔️ 与对手交手 {res['rounds']} 回合"]
    for it in show:
        if it == "...":
            lines.append("　…")
            continue
        who = "你" if it.get("who") == "p" else "对方"
        tag = it["kind"] + ("（暴击！）" if it.get("crit") else "")
        lines.append(f"　{who}·{tag} {it['dmg']}")
    if res["win"]:
        amt = max(1, int(int(vic["gold"]) * 0.10))
        vic["gold"] = int(vic["gold"]) - amt
        p["gold"] = int(p.get("gold", 0)) + amt
        lines.append(f"😈 夺得 {amt} 金币。")
    else:
        amt = max(1, int(int(p["gold"]) * 0.10))
        p["gold"] = int(p.get("gold", 0)) - amt
        vic["gold"] = int(vic["gold"]) + amt
        lines.append(f"💢 反被击退，赔了 {amt} 金币。")
    await store.put_player(p)
    await store.put_player(vic)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 无惩罚切磋（2026-11-16 用户拍板：仅本群 / 自动随机放技 / 只记荣誉战绩）
# ---------------------------------------------------------------------------
#: 进程级挑战会话。按 (群, 目标) 隔离，超时惰性清理，避免半开会话堆积。
duels = dc.ChallengeStore(cfg.DUEL_CHALLENGE_TTL)

#: 私聊虚拟群前缀，与 __init__.py 的 PRIVATE_SCOPE_PREFIX 同源。
_PRIVATE_SCOPE_PREFIX = "pv:"


async def cmd_duel_challenge(store, qq, target, group, name="") -> str:
    """发起切磋：仅本群、需对方应战；发起方先手，无惩罚。"""
    p = await _player(store, qq)
    if not p:
        return NOT_ENTERED
    group = str(group or "")
    if not group or group.startswith(_PRIVATE_SCOPE_PREFIX):
        return "切磋只在群聊中进行，拉个道友来群里比试吧～"
    if not target:
        return "用法：切磋 @某人"
    target = str(target)
    if target == str(qq):
        return "不能和自己切磋，换个道友吧。"
    vic = await store.get_player(target)
    if not vic:
        return "对方尚未入道。"
    if str(vic.get("group")) != group:
        return "对方不在本群，只有本群道友能切磋。"
    duels.ask(str(qq), target, group)
    who = str(name or p.get("name") or f"道友{str(qq)[-4:]}")
    rival = str(vic.get("name") or f"道友{target[-4:]}")
    return (f"⚔️ {who} 向 {rival} 发起切磋！\n"
            f"　无惩罚：不扣血、不掉钱、不损修为，只记荣誉战绩。\n"
            f"　请 {rival} 在 {cfg.DUEL_CHALLENGE_TTL} 秒内回复「应战」，"
            f"或「拒战」婉拒。")


async def cmd_duel_accept(store, qq, group) -> str:
    """应战：消费挑战，复用 core.duel 自动随机放技，只写荣誉战绩。"""
    group = str(group or "")
    if not group or group.startswith(_PRIVATE_SCOPE_PREFIX):
        return "切磋只在群聊中进行～"
    ch = duels.pending_for(str(qq), group)
    if ch is None:
        return "你没有待应的切磋（或已超时）；发起请用「切磋 @某人」。"
    p = await _player(store, str(qq))              # 应战方
    if not p:
        return NOT_ENTERED                          # 校验失败不消费挑战
    rival = await store.get_player(ch.challenger)  # 发起方（先手）
    if not rival or str(rival.get("group")) != group:
        return "发起者已不在本群，切磋取消。"
    ch = duels.take(str(qq), group)                 # 校验通过才一次性消费
    if ch is None:
        return "你没有待应的切磋（或已超时）。"

    # 发起方先手，复用现成 duel 内核：四槽自动随机放技，不改任何 hp/gold。
    res = core.duel(rival, p)
    seq = res["log"]
    show = seq[:4] + (["..."] if len(seq) > 6 else []) + (seq[-2:] if len(seq) > 6 else [])
    a_name = str(rival.get("name") or f"道友{ch.challenger[-4:]}")
    b_name = str(p.get("name") or f"道友{str(qq)[-4:]}")
    lines = [
        f"⚔️ 切磋 · {a_name} vs {b_name}（{res['rounds']} 回合）",
        "　无惩罚切磋：不扣血、不掉钱，只记荣誉战绩",
        "",
    ]
    for it in show:
        if it == "...":
            lines.append("　…")
            continue
        who = "p" if it.get("who") == "p" else "v"
        label = a_name if who == "p" else b_name
        lines.append(_battle_line(it, who=who, who_label=label, foe="来招"))

    # 唯一写入点：荣誉战绩。刻意不碰 hp / mp / gold / exp / bag / wins / losses。
    if res["win"]:
        rival["duel_wins"] = int(rival.get("duel_wins", 0)) + 1
        p["duel_losses"] = int(p.get("duel_losses", 0)) + 1
        lines.append(f"🏆 {a_name} 胜！荣誉 {rival['duel_wins']}胜 "
                     f"{int(rival.get('duel_losses', 0))}负")
    else:
        p["duel_wins"] = int(p.get("duel_wins", 0)) + 1
        rival["duel_losses"] = int(rival.get("duel_losses", 0)) + 1
        lines.append(f"🏆 {b_name} 胜！荣誉 {p['duel_wins']}胜 "
                     f"{int(p.get('duel_losses', 0))}负")
    lines.append("　（本次未扣血、未掉钱、未损修为）")
    await store.put_player(rival)
    await store.put_player(p)
    return "\n".join(lines)


async def cmd_duel_decline(qq, group) -> str:
    """婉拒切磋。"""
    group = str(group or "")
    if not group or group.startswith(_PRIVATE_SCOPE_PREFIX):
        return "切磋只在群聊中进行～"
    if duels.pending_for(str(qq), group) is None:
        return "你没有待应的切磋。"
    duels.decline(str(qq), group)
    return "已婉拒切磋，下次再战。"


async def cmd_give(store, qq, target, amount) -> str:
    p = await _player(store, qq)
    if not p:
        return NOT_ENTERED
    if not target or not amount:
        return "用法：送 100 @某人"
    amt = int(amount)
    if amt <= 0:
        return "数量无效。"
    if int(p.get("gold", 0)) < amt:
        return "金币不足。"
    to = await store.get_player(str(target))
    if not to:
        return "对方尚未入道。"
    p["gold"] = int(p["gold"]) - amt
    to["gold"] = int(to.get("gold", 0)) + amt
    await store.put_player(p)
    await store.put_player(to)
    return f"🎁 你赠予对方 {amt} 金币，善缘结下。"


# ---------------------------------------------------------------------------
# 爬塔副本（2026-11-17）—— 无限层冲榜 / 武器宝箱掉落 / 三选一
# ---------------------------------------------------------------------------
def _expired_settle_line(p, qq) -> str:
    """把「闲置超时」的上一轮补结算，返回提示行（没有则空串）。

    超时不是丢弃：设计口径是「死亡/收手/超时都结算一次」，早期实现只在 get 里
    把过期会话删掉了，等于玩家挂机久一点就白爬 —— 2026-11-17 一并修掉。
    """
    stale = ts.STORE.take_expired(str(qq))
    if not stale:
        return ""
    res = core.tower_settle(p, stale)
    if not res.get("ok"):
        return ""
    return ("⏰ 上一轮闲置超时（%d 小时），已自动结算："
            "+%d 金币、+%d 修为，止步第 %d 层%s"
            % (ts.DEFAULT_TTL // 3600, res["gold"], res["exp"], res["floors"],
               "　🏆 新纪录！" if res["new_record"] else ""))


async def cmd_tower(store, qq, arg="") -> str:
    """爬塔：无参开新塔/继续下一层；带 1/2/3 选三选一战利品。

    2026-11-17 用户要求：三选一不必打「爬塔 1」，直接回 1/2/3 即可；
    且所有「等玩家回话」的业务统一 60 秒（超时奖励作废）。
    """
    p = await _player(store, qq)
    if not p:
        return NOT_ENTERED
    st = ts.STORE.get(str(qq))
    arg = (arg or "").strip()

    # 先处理超时：待选过期即作废（奖励不补发），并在本次回复里提示。
    warn = ""
    if st is not None and ts.expire_loot(st):
        warn = "⏰ 上一层的战利品已超时作废（限 %d 秒），该层奖励已失去。\n" % cfg.CHOICE_TTL

    # 选战利品（爬塔 1/2/3 与裸数字 1/2/3 共用这里）
    if arg in ("1", "2", "3"):
        loot, reason = ts.take_loot(st, int(arg) - 1)
        if reason == "none":
            return warn + "当前没有待选的战利品。"
        if reason == "expired":
            return ("⏰ 战利品已超时作废（限 %d 秒），该层奖励已失去。\n"
                    "发送「爬塔」继续登塔。" % cfg.CHOICE_TTL)
        if reason == "bad_index":
            return "没有第 %s 个选项。" % arg
        _apply_loot(p, st, loot)
        st["loot_taken"].append(loot["key"])
        ts.STORE.save(qq, st)
        return warn + f"✅ 获得「{loot['label']}」：{loot['desc']}\n发送「爬塔」继续登塔。"

    # 新一轮 / 继续
    if st is None:
        if arg and arg not in ("新", "重开"):
            return "用法：爬塔（开始/继续）；战利品直接回 1/2/3；塔层；塔结算。"
        # 上一轮若只是「久没动」超时，必须先补结算：层数与奖励都不能白丢
        # （2026-11-17 用户：爬塔层数被你重置掉了）。
        header = _expired_settle_line(p, qq)
        if header:
            header += "\n"
        st = ts.STORE.begin(qq)
        next_floor = 1
        header += f"🗼 【试炼之塔】开启！历史最高 {int(p.get('tower_best', 0))} 层\n"
    else:
        if st.get("pending_loot"):
            return ("请先选战利品：直接回复 1 / 2 / 3（还剩 %d 秒，超时作废）。"
                    % ts.loot_left(st))
        next_floor = int(st.get("floor", 0)) + 1
        header = ""

    st["floor"] = next_floor - 1
    rng = ts.STORE.rng(qq)
    r = core.tower_floor(p, st, rng,
                         temp_bonus=_temp_bonus(st), temp_effects=_temp_effects(st))
    st["temp_hp"], st["temp_mp"] = r["hp"], r["mp"]
    if r["win"]:
        # 通过层数必须推进，否则「爬塔」会永远停在第 1 层（真实踩过的坑）。
        st["floor"] = int(r["floor"])

    kind_tag = {"normal": "普通", "elite": "精英", "boss": "BOSS"}[r["kind"]]
    aff = ("｜词缀：" + "、".join(r["affixes"])) if r["affixes"] else ""
    if r["win"]:
        lines = [
            f"{header}🗼 第 {r['floor']} 层【{kind_tag}】{r['name']} {aff}",
            f"　{core.equipped_weapon(p)}·{cfg.weapon_display_level(int(p.get('weapon', 0)))} 级出战"
            f"　{r['rounds']} 回合破敌",
            f"　气血 {r['hp']}/{core.stats(p)['体力']}" +
            (f"（歇息 +{r['rest_heal']}）" if r.get("rest_heal") else ""),
        ]
        # 开宝箱（按**本层**层数判定，不能读 st['floor']：胜利后 st 已推进到本层）
        chest = core.tower_open_chest(p, st, rng, floor=int(r["floor"]))
        if chest:
            st.setdefault("chests", []).extend([{"floor": r["floor"], **c} for c in chest])
            for c in chest:
                if c["type"] == "weapon":
                    lines.append(f"🎁 宝箱开出神兵【{c['name']}】！已入背包，发「装备 {c['name']}」佩戴。")
                else:
                    lines.append(f"🎁 宝箱：{c['name']}×{c['n']}")
        else:
            lines.append("　（本层无宝箱）")
        # 三选一（60 秒时效：直接回 1/2/3，超时该层奖励作废）
        choices = td.roll_loot_choices(rng)
        ts.set_loot(st, choices, cfg.CHOICE_TTL)
        lines.append("")
        lines.append("📜 战利品三选一（%d 秒内直接回复 1 / 2 / 3，超时作废）："
                     % cfg.CHOICE_TTL)
        for i, c in enumerate(choices, 1):
            lines.append(f"　{i}. {c['label']} — {c['desc']}")
        # 会话是进程内状态：begin() 返回副本，必须显式 save 才推进（否则永远第1层）。
        ts.STORE.save(qq, st)
    else:
        # 阵亡：结算
        res = core.tower_settle(p, st)
        ts.STORE.clear(qq)
        lines = [
            f"{header}🗼 第 {r['floor']} 层【{kind_tag}】{r['name']} {aff}",
            f"　💀 力竭倒下，止步第 {res['floors']} 层。",
            f"　结算：+{res['gold']} 金币、+{res['exp']} 修为"
            + ("　🏆 新纪录！" if res["new_record"] else f"（最高 {res['best']} 层）"),
        ]
        # 电梯提示（2026-11-17）：止步层 > 1 才值得坐；已领过的层不重复给奖励。
        cp = int(p.get("tower_checkpoint", 0))
        if cp > 1:
            cost = td.elevator_cost(cp)
            lines.append(f"　🛗 发「电梯」花 {cost} 金币直达第 {cp} 层"
                         f"（满状态；旧层已领的奖励不再重复）。")
        lines.append("　发「爬塔」从第 1 层再冲。")
    if warn:
        lines.insert(0, warn.rstrip("\n"))
    await store.put_player(p)
    return "\n".join(lines)


def _temp_bonus(st) -> dict:
    """会话里的三选一属性增益（累加后的绝对值）。"""
    # 属性类在 _apply_loot 时已并入 st['temp_bonus']
    return dict(st.get("temp_bonus", {}))


def _temp_effects(st) -> list:
    return list(st.get("temp_effect_items", []))


def _labels(items) -> str:
    """增益/词缀标签聚合：同名只写一次并带 ×N（战报里连选三次破防不该列三遍）。"""
    order, seen = [], {}
    for it in items:
        lb = str((it or {}).get("label") or "").strip()
        if not lb:
            continue
        if lb not in seen:
            seen[lb] = 0
            order.append(lb)
        seen[lb] += 1
    return "、".join(f"{lb}×{seen[lb]}" if seen[lb] > 1 else lb for lb in order)


def _apply_loot(p, st, loot) -> None:
    """应用三选一（只写会话临时态，不写永久存档）。"""
    kind, key, v = loot["kind"], loot["key"], float(loot.get("v", 0))
    if kind == "hp":
        mx = core.stats(p)["体力"]
        st["temp_hp"] = min(mx, int(st.get("temp_hp") or mx) + int(mx * v))
    elif kind == "mp":
        st["temp_mp"] = core.mp_max(p)
    elif kind == "attr":
        tb = dict(st.get("temp_bonus", {}))
        mx = core.stats(p).get(key, 100)
        tb[key] = int(tb.get(key, 0)) + int(mx * v)
        st["temp_bonus"] = tb
    elif kind == "effect":
        items = list(st.get("temp_effect_items", []))
        items.append(loot)
        st["temp_effect_items"] = items


async def cmd_tower_status(store, qq) -> str:
    """塔层：查看当前爬塔状态。"""
    p = await _player(store, qq)
    if not p:
        return NOT_ENTERED
    st = ts.STORE.get(str(qq))
    best = int(p.get("tower_best", 0))
    if st is None:
        line = _expired_settle_line(p, qq)
        if line:
            await store.put_player(p)
            return (line + "\n"
                    f"🗼 试炼之塔 · 历史最高 {int(p.get('tower_best', 0))} 层\n"
                    "　发「爬塔」开始新一轮。")
        return (f"🗼 试炼之塔 · 历史最高 {best} 层\n"
                "　发「爬塔」开始攀登；无限层、每10层一位守塔强者。\n"
                "　武器从宝箱极低概率掉落，可「装备 <名>」替换玄重尺。\n"
                "　进度已存档（重启不丢）；闲置 %d 小时才自动结算。"
                % (ts.DEFAULT_TTL // 3600))
    expired = ts.expire_loot(st)
    floor = int(st.get("floor", 0))
    nxt = floor + 1
    to_boss = (10 - nxt % 10) % 10
    bonus = st.get("temp_bonus") or {}
    effs = st.get("temp_effect_items") or []
    lines = [
        f"🗼 试炼之塔 · 当前通过 {floor} 层（下一层第 {nxt} 层）",
        f"　历史最高 {best} 层　距下个守塔者 {to_boss} 层",
        f"　气血 {st.get('temp_hp', '—')}　斗气 {st.get('temp_mp', '—')}",
        f"　本轮增益：{('、'.join(f'{k}+{v}' for k, v in bonus.items()) or '无')}"
        + (("｜" + _labels(effs)) if effs else ""),
        f"　宝箱 {len(st.get('chests', []))} 次",
    ]
    if st.get("pending_loot"):
        lines.append("　⏳ 有战利品待选：剩 %d 秒，直接回复 1/2/3" % ts.loot_left(st))
    elif expired:
        lines.append("　⏰ 战利品已超时作废（限 %d 秒），该层奖励已失去。" % cfg.CHOICE_TTL)
    else:
        lines.append("　发「爬塔」继续；「塔结算」收手领奖。")
    return "\n".join(lines)


async def cmd_tower_settle(store, qq) -> str:
    """塔结算：主动收手，按已通过层数结算一次。"""
    p = await _player(store, qq)
    if not p:
        return NOT_ENTERED
    st = ts.STORE.get(str(qq))
    if st is None or int(st.get("floor", 0)) <= 0:
        # 超时的上一轮同样要结算，不能因为「摸了一会儿鱼」就白爬。
        line = _expired_settle_line(p, qq)
        if not line:
            return "当前没有可结算的爬塔进度。"
        await store.put_player(p)
        return line + "\n　发「爬塔」开新一轮。"
    res = core.tower_settle(p, st)
    ts.STORE.clear(qq)
    await store.put_player(p)
    return (f"🗼 收手结算：通过 {res['floors']} 层，+{res['gold']} 金币、+{res['exp']} 修为"
            + ("　🏆 新纪录！" if res["new_record"] else f"（最高 {res['best']} 层）"))


async def cmd_tower_elevator(store, qq) -> str:
    """电梯（2026-11-17）：花金币直达上次止步层，满状态重打，旧层奖励不重复。

    硬约束：
      * 必须有止步层（checkpoint > 1），否则没意义；
      * 当前不能有未结算会话（防止叠加刷层）；
      * 金币必须够，不够明说差额，不扣不进；
    扣费与开会话在同一处完成，put_player 落盘后才回包。
    """
    p = await _player(store, qq)
    if not p:
        return NOT_ENTERED
    cp = max(0, int(p.get("tower_checkpoint", 0)))
    if cp <= 1:
        return ("🛗 还没有可直达的层数——先「爬塔」打到第 2 层及以上，"
                "失败或收手后就能坐电梯了。")
    if ts.STORE.get(str(qq)) is not None:
        return "当前还有未结算的爬塔进度，请先「爬塔」继续或「塔结算」收手，再坐电梯。"
    cost = td.elevator_cost(cp)
    gold = int(p.get("gold", 0))
    if gold < cost:
        return f"🛗 直达第 {cp} 层需 {cost} 金币，你还差 {cost - gold} 金币。"
    p["gold"] = gold - cost
    paid = max(0, int(p.get("tower_paid_floor", 0)))
    # 从 cp-1 起（下一层就是止步层 cp）；满状态：temp_* 全空，增益清空。
    st = ts.STORE.begin(qq, floor=cp - 1)
    await store.put_player(p)
    reuse = "；第 %d 层及以下的奖励已领过，重打不再发" % paid if paid >= cp - 1 else ""
    return (f"🛗 花 {cost} 金币直达第 {cp} 层！满状态挑战"
            f"（气血/斗气全满，无上轮增益）{reuse}。\n"
            f"　发「爬塔」迎战第 {cp} 层。")


# ---------------------------------------------------------------------------
# 周副本（2026-11-17）—— 7 层一命通关 / 周一快照难度 / 奖励暂存锁定
# ---------------------------------------------------------------------------
def _staged_line(st: dict) -> str:
    s = st.get("staged") or {}
    parts = [f"+{int(s.get('gold',0))} 金币", f"+{int(s.get('exp',0))} 修为"]
    items = s.get("items") or {}
    if items:
        parts.append("、".join(f"{k}×{v}" for k, v in items.items()))
    w = s.get("weapons") or []
    if w:
        parts.append("神兵" + "、".join(w))
    return "、".join(parts) or "无"


def _weekly_expired_line(st: dict) -> str:
    """超时会话：按失败处理——暂存奖励整段丢弃（失败路径绝不调用 weekly_commit）。"""
    if not st:
        return ""
    return ("⏰ 上周副本闲置超时，本轮挑战失败——"
            "暂存的 %s 全部清空（含神兵），需从第 1 层重来。" % _staged_line(st))


async def cmd_weekly(store, qq) -> str:
    """周副本：无参开新轮/继续下一层。7 层一命通关，中途失败奖励全清。"""
    p = await _player(store, qq)
    if not p:
        return NOT_ENTERED
    week = wd.week_key()
    cleared = str(p.get("weekly_cleared_week", "")) == week
    st = ws.STORE.get(str(qq))

    # 超时：按失败处理（暂存全清），本次回复明示，不给任何奖励。
    stale = ws.STORE.take_expired(str(qq))
    stale_line = _weekly_expired_line(stale) if stale else ""

    if cleared and st is None:
        tail = "\n　本周已通关周副本，下周再来。" if not stale_line else ""
        return stale_line + ("🗼 周副本 · 本周（%s）已通关，奖励已入账。%s" % (week, tail))

    if st is None:
        snap = core.weekly_snapshot(p, week)   # 锁定本周难度（新入道固定新手档）
        st = ws.STORE.begin(qq, week=week)
        header = (f"{stale_line}\n" if stale_line else "") + (
            f"🗼 【周副本】{week} · 7 层一命通关开启！\n"
            f"　难度按本周快照锁定（画像 lv{snap.get('lv', '?')}）；"
            f"这一周变强，怪不会跟着变强。\n")
    else:
        if st.get("pending_loot"):
            return "请先选战利品。"
        header = (stale_line + "\n") if stale_line else ""

    floor = int(st.get("floor", 0))
    if floor >= wd.WEEKLY_FLOORS:
        # 理论上不含这种会话（通关即 clear），兜底：放行重开提示
        ws.STORE.clear(qq)
        return header + "本周已通关，发「周副本」下周再来。"

    snap = core.weekly_snapshot(p, week)
    rng = ws.STORE.rng(qq)
    r = core.weekly_floor(p, snap, st, rng)

    kind_tag = {"normal": "普通", "elite": "精英", "boss": "BOSS"}[r["kind"]]
    aff = ("｜" + "、".join(r["affixes"])) if r["affixes"] else ""
    head = f"{header}🗼 第 {r['floor']}/7 层【{kind_tag}】{r['name']}{aff}\n　{r['rounds']} 回合交锋"

    if not r["win"]:
        # 一命通关：失败 → 暂存奖励整段丢弃（含武器），会话清空，从头再来。
        lost = _staged_line(st)
        ws.STORE.clear(qq)
        await store.put_player(p)   # 快照已更新，但奖励一律不入账
        return (head + f"\n　💀 力竭倒下，止步第 {r['floor']} 层。"
                f"\n　本轮暂存奖励（{lost}）全部清空，含神兵。"
                f"\n　发「周副本」从第 1 层重来。")

    # 胜利：奖励入暂存区（不写永久），开宝箱也只入暂存
    core.weekly_stage(st, r)
    chest = core.weekly_open_chest(p, snap, st, int(r["floor"]), rng)
    st["temp_hp"], st["temp_mp"] = r["hp"], r["mp"]
    st["floor"] = int(r["floor"])

    lines = [
        head,
        f"　气血 {r['hp']}/{core.stats(p)['体力']}"
        + (f"（歇息 +{r['rest_heal']}）" if r.get("rest_heal") else ""),
        f"　本层 +{r['reward']['gold']} 金币 / +{r['reward']['exp']} 修为（暂存，通关才到账）",
    ]
    if chest:
        for c in chest:
            lines.append("　🎁 宝箱暂存：" + (f"神兵【{c['name']}】" if c["type"] == "weapon"
                                      else f"{c['name']}×{c['n']}"))
    lines.append(f"　【暂存合计】{_staged_line(st)}")

    if r["floor"] >= wd.WEEKLY_FLOORS:
        # 通关：一次性 commit 到永久存档（含武器）
        res = core.weekly_commit(p, st)
        p["weekly_cleared_week"] = week
        ws.STORE.clear(qq)
        await store.put_player(p)
        lines.append(f"　🏆 七层通关！结算到账：+{res['gold']} 金币、+{res['exp']} 修为"
                     + (f"，神兵 {('、'.join(res['weapons']))} 入包" if res["weapons"] else "")
                     + "。")
        lines.append("　本周已通关，下周再来挑战自己的新快照。")
    else:
        ws.STORE.save(qq, st)
        await store.put_player(p)   # 本周快照落盘（难度锁定）
        lines.append(f"　发「周副本」挑战第 {r['floor'] + 1} 层；"
                     f"中途失败则暂存奖励（含神兵）全部清零。")
    return "\n".join(lines)


async def cmd_weekly_status(store, qq) -> str:
    """周本状态：本周快照难度、当前层、暂存奖励、是否已通关。"""
    p = await _player(store, qq)
    if not p:
        return NOT_ENTERED
    week = wd.week_key()
    snap = dict((p.get("weekly_snapshot") or {}).get("profile") or {})
    cleared = str(p.get("weekly_cleared_week", "")) == week
    lines = [f"🗼 周副本 · {week}"]
    if snap:
        lv = snap.get("lv", "?")
        tag = "固定新手档" if int(lv or 0) <= int(wd.ROOKIE_PROFILE["lv"]) else "本周锁定画像"
        lines.append(f"　难度：{tag}（快照 lv{lv}）")
    else:
        lines.append("　难度：尚未锁定（发「周副本」开启）")
    lines.append(f"　本周状态：{'✅ 已通关' if cleared else '未通关'}")
    st = ws.STORE.get(str(qq))
    if st and not cleared:
        lines.append(f"　当前：第 {int(st.get('floor',0)) + 1} 层 / 7 层")
        lines.append(f"　暂存奖励：{_staged_line(st)}（失败即全清，含神兵）")
    else:
        lines.append("　发「周副本」开始/继续 7 层一命通关。")
    return "\n".join(lines)


async def cmd_equip(store, qq, name) -> str:
    """装备 <武器名>：切换当前武器。"""
    p = await _player(store, qq)
    if not p:
        return NOT_ENTERED
    if not name:
        ws = core.owned_weapons(p)
        return "可装备：" + "、".join(ws) + "（发「装备 <名>」切换）"
    res = core.equip_weapon(p, name.strip())
    if not res["ok"]:
        return res["msg"]
    await store.put_player(p)
    st = core.stats(p)
    return (f"🗡️ {res['msg']}　力量 {st['力量']} 敏捷 {st['敏捷']} "
            f"斗气 {st['斗气']} 防御 {st['防御']}")


async def cmd_pick(store, qq, n) -> str:
    """裸数字选择（1/2/3…）：优先爬塔战利品，其次机遇有奖竞猜。

    返回**空串**表示当前没有待选业务 —— 调用方必须保持沉默（不回复任何东西），
    否则会把群里普通的数字聊天吞掉。
    2026-11-17 用户要求：凡是等玩家回话的业务都用 1/2/3，且 60 秒内有效。
    """
    qq = str(qq)
    p = await _player(store, qq)
    if not p:
        return ""
    st = ts.STORE.get(qq)
    if st is not None:
        if ts.expire_loot(st):        # 战利品刚超时 → 明说奖励没了，别静默
            return "⏰ 战利品已超时作废（限 %d 秒），该层奖励已失去。" % cfg.CHOICE_TTL
        if st.get("pending_loot"):
            # 交给 cmd_tower 统一处理（含越界、应用增益、剩余时间提示）
            return await cmd_tower(store, qq, str(n))
    from . import quiz_session as qs  # noqa: PLC0415

    q = qs.STORE.pending_for(qq)
    if q is not None:
        idx = int(n) - 1
        if 0 <= idx < len(q.options):
            return await cmd_answer_quiz(store, qq, q.token, idx)
        return "没有第 %d 个选项（本题共 %d 项）。" % (int(n), len(q.options))
    if qs.STORE.take_expired(qq):
        return "⏰ 这题已超时作废（限 %d 秒），本题奖励已失去。" % cfg.CHOICE_TTL
    return ""
