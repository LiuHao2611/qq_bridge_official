# -*- coding: utf-8 -*-
"""斗破苍穹 · 口令业务层（纯函数 + 存储交互，零 nonebot 依赖，可直接单测）。

每个 cmd_* 输入「store + 玩家/群标识 + 参数」，返回要回群的文字。
__init__.py 的 matcher 只负责取参数 → 调 cmd_* → finish(文字)。

⚠️ 命名约定：斗破 / 修仙 共用纯中文口令，靠「按用户记录的当前模式」分流；
   菜单（斗破菜单 / 修仙菜单）是唯一切换口，其余口令两游戏完全一致（突破 / 闭关 / 商店…）。
"""

from __future__ import annotations

import random
import re
import time
from datetime import date

from . import battle_comments as bc
from . import battle_review as br
from . import bagview
from . import card_theme
from . import config as cfg
from . import core
from . import doupo_text as txt
from . import temp_buff as tb
from . import danwen as dw
from . import duel_challenge as dc
from . import factions as fac
from . import flame_drop as _fdrop
from . import flame_skills as fs
from . import skill_book as book
from . import tower_data as td
from . import tower_session as ts
from . import weekly_data as wd
from . import weekly_session as ws
# ⚠️ 必须 `from .puppet_store import ...`，**不能** `from . import puppet_store`：
#    doupo/__init__.py 里 `puppet_store = puppet_store_mod.PUPPET_STORE` 会把包属性
#    `puppet_store` 盖成 Store 实例，`from . import puppet_store` 拿到的是实例而不是模块，
#    再取 `.PUPPET_STORE` 就 AttributeError。2026-11-18 事故：按钮点击走
#    `button_dispatch`（不传 pstore）→ 三条傀儡按钮全部报「按钮处理出错」。
#    从子模块直接取名字永远拿的是模块属性，与包属性是否被遮蔽无关。
from .puppet_store import PUPPET_STORE as DEFAULT_PUPPET_STORE

NOT_ENTERED = "你尚未踏入斗气大陆，发送「入道」开启征程。"


#: 调息恢复量的旁路缓存（唯一源在 store，见其中 `REGEN_NOTICE` 的注释）。
#: 2026-11-19 事故后从 commands 层**下沉到 Store.get_player()**：按钮点击、
#: 人物卡/战报渲染等入口直接读 store，绕过 _player，导致点一上午都不恢复。
#: 这里只保留一个别名，给人物卡面板的 `_regen_line()` 读。
from .store import REGEN_NOTICE as _LAST_REGEN  # noqa: E402


async def _player(store, qq):
    # 调息已在 Store.get_player() 里统一结算，本函数不再重复（重复会吞掉余数）。
    p = await store.get_player(str(qq))
    # 老档火焰迁移（2026-11-24）：阳火/化生火已下线，惰性折算药渣后移除（一次性）
    if p and _fdrop.migrate_legacy_flames(p, core.add_item):
        await store.put_player(p)
    # 丹纹改名迁移（2026-11-25）：`破厄丹·丹纹` → `一纹破厄丹`（物品名就是主键）。
    # 放在唯一取档口，任何口令第一次读到都会顺手迁好；锁定表同步改键。
    if p:
        moved = core.migrate_danwen_bag(p)
        if moved:
            await store.put_player(p)
            rename = getattr(store, "rename_item_lock", None)
            if rename is not None:
                for old, new in moved:
                    try:
                        await rename(str(qq), old, new)
                    except Exception:  # noqa: BLE001 — 锁表迁移是增强项，失败不影响服丹
                        pass
    return p


async def _puppet_ctx(qq, p):
    """出战傀儡上下文（2026-11-20 第二轮）：五处战斗入口唯一取数口。

    返回 ``(puppet, formation_n, flame_mult)``。没有出战傀儡时返回
    ``(None, 0, 1.0)`` —— 这三个缺省值与「不传参」逐字节等价，所以调用方
    可以无条件透传，不必写 if。

    为什么必须走这里，而不是从玩家存档推：
      * 傀儡存在**独立表** ``puppet_materials / puppets``（玩家数据禁 JSON blob），
        只能由 ``PUPPET_STORE`` 异步取，不在 ``p`` 字典里。
      * ``flame_mult`` 与 B 线异火同源：**同一个倍率**同时喂怪血反解与实战傀儡击
        （红线）。漏传会让「卡面写的加成」与「实际打出来的伤害」变成两张皮。

    用户原话（2026-11-20）：「其他战斗都要带上，不然炼制傀儡没有意义」。
    任何新战斗入口都必须调本函数并透传三个参数，否则守门测试会红。
    """
    from . import flame_bonus as fb
    from . import puppet_core as pc

    qq = str(qq)
    try:
        pu = await DEFAULT_PUPPET_STORE.active_puppet(qq)
    except Exception:
        return (None, 0, 1.0)
    if not pu:
        return (None, 0, 1.0)
    try:
        roster = await DEFAULT_PUPPET_STORE.list_puppets(qq)
    except Exception:
        roster = []
    n = pc.formation_count(roster)
    # P0-1：成阵只在这里生成只读临时副本（3 具同档 → 领头临时升一档）。
    # 绝不写回 PuppetStore —— 副本随本条命令结束即丢弃，战报另有退档提示。
    pu = pc.formation_burst(pu, roster, n)
    try:
        flame_mult = 1.0 + float(fb.puppet_atk_bonus(p))
    except Exception:
        flame_mult = 1.0
    return (pu, max(1, int(n)), float(flame_mult))


async def _writeback_damage(qq, res: dict, puppet: dict = None) -> None:
    """按护主战果写回傀儡战损（P1-1 唯一写回口径）。

    「信号自带 id」（`res["puppet_guard"]["id"]`）保证写回的是真实库行，而非
    结阵升档临时副本。duel 无护主机制、res 里没有 puppet_guard，自然跳过。

    🚨 纪律：写回失败**如实上抛**（用户最高声明禁止兜底/静默吞）。不让一场
    战斗「看似成功、战损却没记上」——那会让玩家带着满状态傀儡去送死。
    """
    guard = res.get("puppet_guard") or {}
    if guard.get("broken"):
        pid = int(guard.get("id") or (puppet or {}).get("id") or 0)
        if pid:
            await DEFAULT_PUPPET_STORE.update_puppet(str(qq), pid, damaged=True)


async def _finalize_battle(p: dict, qq, res: dict, kind: str,
                           opponent: str = "", extra: dict = None,
                           puppet: dict = None) -> None:
    """战斗结束唯一漏斗：先同步落回看，再写回傀儡战损（P1-1）。

    写回与「这场战斗真的打完了」绑定：凡是调 ``record_last_battle`` 的入口都
    改调本函数，就不会漏写回。突破战走自己的渲染、不落回看，单独调
    ``_writeback_damage`` —— 两者共用同一个写回口径，不复制逻辑。
    """
    core.record_last_battle(p, kind, res, opponent=opponent, extra=extra)
    await _writeback_damage(qq, res, puppet)


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
        ("入塔", "进天焚炼气塔借心火修炼：按火晶卡成倍涨修为，同时累积火毒"),
        ("出塔", "离开炼气塔，开始消解火毒（每分钟 -2）"),
        ("火能", "看聚火壶状态：火晶卡 / 火能 / 火毒 / 心火倍率 / 岩浆世界"),
        ("换卡", "升一档火晶卡（黑→蓝→青→赤→紫，消耗火能，解锁更深塔层）"),
        ("突破", "修为满时晋升一星；九星跨境界须先战一名强敌（胜则概率掉兽火/异火）；斗帝看机缘"),
        ("战斗", "随机遭遇一只同阶妖兽，回合制结算"),
        ("战斗回看", "查看最后一次战斗完整过程：逐招、气血/斗气、词条（出卡片）"),
        ("战斗评价", "根据最后一场战斗的真实数据，给一句/一段毒舌评语"),
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
        ("斗技", "一张卡看完：已上阵四槽 + 已领悟全部斗技（含转宗保留的他宗技）"),
        ("宗门斗技", "看当前所属宗门的斗技池（本门传承+通用，含未解锁、上阵编号）"),
        ("斗技详情 狂狮怒罡", "带技名看完整说明；只发「斗技详情」也列出本宗全部斗技"),
        ("学斗技", "达等级+战力后随机领悟一门未学斗技（花金币）；也可「学习 <技名>」点名领悟，入道只给 1 门"),
        ("升级 龙吟碎", "用焚血散升斗技，每级伤害 +20%"),
        ("上阵 1 3 5 7", "按「宗门斗技」名录卡序号选四槽；也可打技名，异火主动技同样能上阵"),
        ("宗门", "看本门与可转宗门（进阶池 lv40 / 隐世池 lv90）"),
        ("转宗 迦南学院", "改换门庭，重配四槽（不发该派入道物品）"),
        ("异火", "看 5 种异火的主动技 / 被动、来历与喂养进度"),
        ("收服 火云水炎", "主动收服异火：数值火可直接收，大后期火须境界+主线达标；隐藏彩蛋火不在此列"),
        ("养火 <火名>", "用药渣喂养已收服的异火/兽火（炼药失败会出药渣）；先得火才行"),
        ("装备", "装备 <武器名>：切换持有的神兵（爬塔宝箱掉落）"),
        ("炼制傀儡", "按配方开炉（材料越好档位越高）；「炼制傀儡 地妖傀」指定配方"),
        ("傀儡", "查看傀儡卡（出图）；子命令 配方/淬炼/结阵/收服/融魂/换躯/养魂"),
        ("傀儡遗迹", "每日副本七段自动连打至力竭：库房→能量之层→雷池→宗骸祭坛→尊骨陵寝→圣地残殿→斗圣主殿"),
    )),
    ("炼药 · 宝库", (
        ("宝库", "随身商店：药材 + 炎星石，看价"),
        ("买 血灵草 2", "买 2 份血灵草"),
        ("炼药", "看丹方：帝品 / 五品 / 四品"),
        ("炼制 回灵丹", "开炉炼丹；「炼制 回灵丹 5次」连炼5炉，失败得药渣"),
        ("服丹 回灵丹", "服用丹药；带纹丹写成「服丹 二纹破厄丹」"),
        ("查看 破厄丹", "看物品详情：本效、丹纹各档概率与赐福、从何而来"),
        # 2026-11-25：背包已改「不翻页、一卡到底」，旧文案还写着「24 格一页 / 背包2 翻页」，
        # 玩家照着发「背包2」只会拿到同一张满包卡 —— 菜单不许承诺做不到的事。
        ("背包", "包裹：丹药优先、一卡到底不翻页；格子上「锁」=已锁定，战斗不自动服用"),
        ("锁定 回灵丹", "锁定物品：只拦「战斗自动服药」，手动服丹照旧"),
        ("解锁 回灵丹", "解除锁定，恢复自动服用"),
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
        ("送 回灵丹 5 @某人", "赠金币 / 物品 / 傀儡材料"),
    )),
    # 2026-11-24 用户「切换卡片主题的口令，菜单上没写」：主题之前只在纯文本菜单
    # 末尾补了一句、图片菜单卡压根没有，玩家翻卡看不到。收成正式分组，图与文同源。
    # ⚠️ 只登一条：菜单卡口令列走 menu_cmd_label 只留首词，「主题 游戏/主题 浅色/
    # 主题 跟随全局」在卡面会渲染成四个一模一样的「主题」，看着像坏了。
    # 具体三种写法交给说明列说清。
    ("外观 · 卡片主题", (
        # 说明列只有 176px（≈10 汉字/行），多写必折成三行还把「跟随全局」劈开；
        # 这里只列可选项，动词由分组名「卡片主题」承担，一行干净。
        ("主题", "游戏 / 浅色 / 跟随全局"),
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


# ---------------------------------------------------------------------------
# 卡片主题切换（2026-11-23：游戏本体风 / 浅色现代风 双主题）
# ---------------------------------------------------------------------------
def cmd_theme(qq: str, arg: str = "") -> str:
    """卡片主题：个人偏好（口令即时切）> 全局默认（配置/运维口令）。

    用法：
        主题                 查看当前主题与可选值
        主题 游戏 / 主题 浅色   切自己的卡片主题（个人偏好，立即生效）
        主题 跟随全局         清除个人偏好，回到全局默认
        主题默认 游戏/浅色      (运维) 设全局默认；已有个人偏好不受影响
    """
    arg = (arg or "").strip()
    if not arg:
        st = card_theme.status(qq)
        personal = card_theme.label(st["user"]) if st["user"] else "（未设置，跟随全局）"
        return (
            f"卡片主题 · 当前生效：{card_theme.label(st['effective'])}\n"
            f"　个人偏好：{personal}\n"
            f"　全局默认：{card_theme.label(st['default'])}\n"
            f"切换：主题 游戏 / 主题 浅色 / 主题 跟随全局"
        )
    if arg == "跟随全局" or arg in ("取消", "重置", "默认"):
        eff = card_theme.clear_user(qq)
        return f"已清除个人主题偏好，跟随全局默认：{card_theme.label(eff)}"
    style = card_theme.normalize(arg)
    if style is None:
        return ("没听懂的主题：%s。可选：主题 游戏 / 主题 浅色 / 主题 跟随全局"
                % arg)
    card_theme.set_user(qq, style)
    st = card_theme.status(qq)
    extra = ""
    if st["user"] == "":
        extra = "（与全局默认一致，未单独记录）"
    return f"已切换卡片主题：{card_theme.label(style)}　下次出图立即生效。{extra}"


def cmd_theme_default(arg: str = "") -> str:
    """(运维) 查看/设置全局默认主题：主题默认 / 主题默认 浅色。"""
    arg = (arg or "").strip()
    if not arg:
        return f"全局默认卡片主题：{card_theme.label(card_theme.resolve_default())}"
    style = card_theme.normalize(arg)
    if style is None:
        return "没听懂的主题：%s。可选：主题默认 游戏 / 主题默认 浅色" % arg
    card_theme.set_default(style)
    return (f"全局默认卡片主题已设为：{card_theme.label(style)}　"
            f"已设个人偏好的玩家不受影响。")


#: 玩法细则的**唯一源**：(分组名, [该组各行])。纯文本兜底 cmd_help 与图片卡
#: card.build_help_data 都从这里读，保证「图与文说的永远是一回事」。
#: 每组第一行为【 group】后接的首行（不带缩进），其余为缩进两格的细则行。
HELP_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("属性", (
        "力量=普攻，斗气=技能，敏捷→暴击+闪避，防御=减伤，体力=血量",
        "成长：值 = 基础×10 + 阶²×基础×10 + 基础×等级，阶 = 大境界序号（斗之气 0 → 斗帝 12）",
        "暴击% = 10 + (敏捷+30)/100，闪避% = 5 + (敏捷+20)/200",
        # 2026-11-20 用户「应该有一个和闪避对应的词条」：命中与闪避对冲，
        # 两侧共用一条公式（core.hit_rate / cfg.MON_HIT_RESIDUAL 是唯一真值）。
        "命中% = 6 + 大境界×1.5 + 丹药；实际闪避% = max(0, 我的闪避 − 对方命中)",
        # 2026-11-20 订正：旧文案写「等级越高越会闪你的招」，但实测小怪/精英的
        # 身法（3~26.6）全程低于玩家命中（6~24）→ 普通战斗里**闪不到**，
        # 只有硬仗对手（精英/BOSS/野外反派）才真会闪过。文案不能承诺不发生的事。
        "怪物同样有眼力/身法/暴击：等级越高越会破你闪避；硬仗对手会闪招，凝识丹堆命中可反压",
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
        "成丹把握看炼药师品阶与丹药品阶：四品炼一品九成、炼四品七成（十拿九稳）",
        "高品阶炼低阶每低1档加成；越1档骤降勉强、越2档以上几无可能",
        "连炼：发「炼制 回灵丹 5次」一次开5炉，各炉独立成败，整批只占一次冷却",
        "连炼耗斗气、按实有药材收炉；低阶丹经验递减，七品以上须实战历练升灵魂境界",
        "失败给药渣，药渣喂异火；丹名前〔回斗气〕〔回血〕为功效标签",
        "丹纹：成功时按品阶概率出纹（一品20%递减至帝品1%），出纹多得一枚带纹丹",
        "带纹丹服下除本效外另得随机赐福：焚天之威(前3击伤害×2)/一击必中(首技必暴击)/"
        "蛮牛之力(力+斗气+25%)/疾风之势(敏+防+25%)/虎躯之固(血+30%)/丹田盈溢(斗气回满)，"
        "全部限次或限时30分钟，给足但不过量",
    )),
    ("异火", (
        "青莲地心火 斗气槽+25%/级；陨落心炎 经验+50%/级；",
        "三千焱炎火 吸血+2%/级；净莲妖火 暴击闪避+10%/级；帝炎 斗帝专属",
        "九星跨境界突破须先战一名强敌（药老/云棱/摘星老鬼/魂灭生等，各有台词与绝技）：胜则概率掉兽火/异火，入「异火」查看",
    )),
    ("斗帝", (
        "102 级九星斗圣冲 103 级：成功率 = 30% + 机缘/2000，帝炎 100%",
    )),
    ("斗技", (
        "「斗技」= 一张卡看完我的斗技：已上阵四槽 + 已领悟全部"
        "（含转宗后依原著「技艺不失」保留的他宗技）；"
        "「宗门斗技」= 当前宗门的斗技池（本门传承+通用，含未解锁、上阵编号）",
        "三阶四槽：",
        "阶位分三档 —— 普通（黄阶/玄阶）· 高阶（地阶/天阶）· 特殊（秘法/血脉/体质/异火）",
        "上阵最多 4 门，三阶任意搭配（后期可四门全特殊）",
        "战斗与掠夺每回合在「已上阵且斗气够」的技里随机放一门",
        "斗气按阶扣：普通 20% ｜ 高阶 34% ｜ 特殊 50%（占 mp_max）",
        "斗气不够放任何一门 → 强制武器普攻，伤害大减且持续掉血",
        "升一级伤害 +20%（耗焚血散，最高 5 级）；同名技跨宗共享等级",
        "入道只领悟 1 门低阶技，其余靠「学斗技」逐个领悟：达解锁等级+战力门槛、花金币，随机得一门未学技",
        "领悟的技要用「上阵 <技名>」或「上阵 1 3 5 7」（「宗门斗技」名录卡序号）放进四槽才会自动释放；未领悟的技不能上阵",
        "解锁按阶位：黄/玄 lv1 ｜ 地阶 20/30/40 ｜ 天阶 50/60/70 ｜ 秘法 40 ｜ 体质血脉 50 ｜ 异火 60",
        "完整说明见「斗技详情 <技名>」（施展流程 + 数值 + 来历）",
    )),
    ("宗门", (
        "入道按 QQ 定种子随机分宗，不可重掷：",
        "新手池 8 派 lv1 可进 ｜ 进阶池 11 派 lv40 ｜ 隐世池 6 派 lv90",
        "转宗价 = 3000 + 等级×200 金币；只换师门并重配四槽，",
        "不发该派入道物品；已升的技能等级按技名保留（转回去还在）",
        "少数门派斗技本就稀少（蛇人族 / 毒宗 / 炼药师公会各 3 门、慕兰谷 2 门），",
        "进池时四槽填不满属正常，随等级解锁会补齐",
        "魂殿（邪道·lv40 进阶池）：拜入即不可再转宗；已领悟斗技全部保留，"
        "自带「吞噬炼化」——每斩精英/BOSS 额外吞其修为（怪物 exp 的 50%），"
        "但吞来的修为记入根基，实战越强越虚浮",
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
        f"初入门径，只领悟了一门【{first}】——斗技要一门门学，不可贪多。\n"
        "异火举世罕见，不会凭空而来——九星突破战胜强敌，或炼制兽火单方，方得一火。\n"
        "\n"
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
    _alch_idx = int(p.get("alch", 0))
    alch_label = cfg.alch_label(_alch_idx)
    alch_need = cfg.alch_tier(_alch_idx)[1]
    wv = core.weapon_panel(p)

    rows = [
        f"👤 【{p['name']}】",
        f"境界　{cfg.full_realm(lv)}",
        f"　　　{exp_line}　{_bar(int(p['exp']), need)}",
        f"炼药　{alch_label}",
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
        _regen_line(p, st),
    ]
    # 2026-11-24 限时增益：药力未散时必须看得见剩余时间（否则玩家以为丹没效果）。
    rows.extend(tb.lines(p))
    # 丹纹赐福剩次数同理：给了就得看得见，否则玩家不知道还没用完。
    rows.extend(dw.lines(p))
    return "\n".join(rows)


def _regen_line(p, st) -> str:
    """面板上的自动调息说明（2026-11-19）。

    没有这一行，玩家只会发现「血自己涨了」却不知道为什么；写清速率与「离线也算」
    才能让人放心下线。
    """
    mins = int(cfg.REGEN_TICK_SEC // 60)
    hp_tick = int(int(st["体力"]) * float(cfg.REGEN_HP_PCT))
    mp_tick = int(core.mp_max(p) * float(cfg.REGEN_MP_PCT))
    last = _LAST_REGEN.get(str(p.get("qq") or ""))
    head = "🌿 调息"
    if last:
        head += f" 本次 气血+{last['hp']}、斗气+{last['mp']}"
    return (f"{head}\n　每 {mins} 分钟 气血+{hp_tick}、斗气+{mp_tick}"
            f"（离线同样累计）")


# ---------------------------------------------------------------------------
# 修炼 / 突破
# ---------------------------------------------------------------------------
async def cmd_cultivate(store, qq) -> str:
    p = await _player(store, qq)
    if not p:
        return NOT_ENTERED
    # 2026-11-19：塔中不静修。同一段时间不能被「修炼」和「入塔」各领一次。
    tst = await store.tower_state(str(qq))
    if int(tst.get("in_tower") or 0):
        return ("🔥 你正在天焚炼气塔中心火淬体，此时静修是白费。\n"
                "　发「入塔」结算塔修，或发「出塔」离开。")
    res = core.settle(p)
    await store.put_player(p)
    lv = int(p["lv"])
    need = cfg.exp_need(lv)
    tip = "，可发送「突破」晋升" if core.can_level(p) else ""
    head = (txt.cultivate(res["minutes"], _fmt(res["gain"]))
            if res["gain"] else "🧘 你刚调息过，气机未动。")
    return (f"{head}\n修为 {_fmt(p['exp'])}/{_fmt(need) if need else '—'}{tip}\n"
            f"当前：{cfg.full_realm(lv)}\n"
            f"（想快些可发「入塔」借心火成倍修炼，需火能与火晶卡）")


async def cmd_break(store, qq, rng=None) -> str:
    """突破口令。rng 可选注入（测试用必胜/必败随机源；线上 None=random.random）。"""
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
        return txt.need_exp(_fmt(need))
    # 2026-11-20 九星突破 BOSS 战重做（用户「BOSS 登场要有引言台词」）：
    # 跨大境界须先战胜一名原著反派（药老/云棱/鹜护法/摘星老鬼/魂灭生/美杜莎/魂天帝）。
    # 战力快照只在 core 内部锁难度，**这里绝不渲染快照战力数字**（用户明确要求）。
    if core.is_nine_star_breakthrough(p):
        locked = await store.item_locks(str(qq))
        # 出战傀儡（2026-11-20 第二轮）：突破是唯一的境界关口，不带傀儡等于白炼。
        _pu, _pu_n, _pu_fm = await _puppet_ctx(qq, p)
        br = core.nine_star_breakthrough(p, rng=rng or random.random, locked=locked,
                                        puppet=_pu, formation_n=_pu_n,
                                        puppet_flame_mult=_pu_fm)
        await store.put_player(p)
        # P1-1：突破战不落回看（走自己的四段式渲染），但护甲打空同样要写回战损。
        await _writeback_damage(qq, br, _pu)
        _ch = br.get("character") or {}
        _name = br.get("boss_name") or "强敌"
        _title = br.get("boss_title") or ""
        _intro = str(_ch.get("intro") or "").strip()
        _appear = str(_ch.get("appearance") or "").strip()
        # 结算四段式（用户「结算乱七八糟」）：①登场（剪影→报名号→台词）②鏖战结果
        # ③原著台词收尾 ④突破/掉落。出场描写与引言缺一不可（用户 2026-11-20 两次强调）。
        head = [f"⚡ 【{cfg.full_realm(lv)}】壁垒将破——"]
        if _appear:
            head.append(f"　{_appear}")
        head.append(f"　【{_name}】{'·' + _title if _title else ''}拦在突破之路上！")
        if _intro and _intro != _appear:
            head.append(f"　{_intro}")
        if not br["win"]:
            return "\n".join(head + [txt.boss_fail(br["rounds"], _fmt(br["hp_end"]))])
        # 战胜：挑一条原著台词作收尾（不渲染内部战力快照数字）。
        _quotes = tuple(_ch.get("quotes") or ())
        _end_quote = ("　「%s」——话音未落，强敌已溃。" % _quotes[-1]) if _quotes else ""
        lines = head + [txt.boss_win(br["rounds"], _name)]
        if _end_quote:
            lines.append(_end_quote)
        fl = br.get("flame") or {}
        if fl.get("ok"):
            _kind = "兽火" if fl.get("kind") == "beast" else "异火"
            lines.append(txt.flame_drop(fl["name"], _kind, bool(fl.get("duplicate")),
                                        fl.get("dross", 30)))
        if br.get("tiandi"):
            lines.append(txt.tiandi_success())
            return "\n".join(lines)
        if br.get("tiandi_fail"):
            lines.append(txt.tiandi_fail(int((br.get("rate") or 0) * 100)))
            return "\n".join(lines)
        lines.append(txt.break_result(cfg.full_realm(lv), cfg.full_realm(br["new_lv"]),
                                      cfg.realm_name(br["new_lv"]), True))
        return "\n".join(lines)
    res = core.level_up(p)
    await store.put_player(p)
    if res.get("tiandi"):
        return (
            "🌩️ 九霄雷动，天地齐鸣——\n"
            "🔥 你踏碎最后一层壁障，成就【斗帝】！\n"
            "帝炎自燃于体，五维（力/敏/气/防/体）×1.2，机缘值全满。"
        )
    if res.get("reason") == "tiandi_fail":
        return txt.tiandi_fail_plain(int(res["rate"] * 100))
    # 文案把起点终点一起写清楚（用户 2026-11-14：「三星晋升到一星…这顺序不对」）。
    # 九星制下境界内是 三星→四星 这种正常递增，只有跨大境界才会星数归零，
    # 所以跨境界用「境界突破」措辞明确告知「这是换大境界，不是掉星」：
    #   同境界：突破成功！由【三星斗师】晋入【四星斗师】
    #   跨境界：境界突破！由【九星斗者】晋入【一星斗师】
    # 2026-11-23：正文改由 doupo_text 按境界分档，锚点词保留给既有测试。
    old_name = cfg.full_realm(lv)
    new_lv = int(p.get("lv", lv))
    new_name = res.get("name") or cfg.full_realm(new_lv)
    return txt.break_result(old_name, new_name, cfg.realm_name(new_lv),
                            cfg.realm_name(new_lv) != cfg.realm_name(lv))


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
        return txt.battle_cd(left)

    # 一场遭遇 1~5 个敌人（魔兽 / 悍匪 / 原著反派），逐个调用单敌内核 fight。
    # 难度仍按玩家等级反解，不会出现越级秒杀；斗气跨战结转（见底转普攻）、
    # 气血结转+小幅回复。回满斗气只能靠突破/升级。
    # 2026-11-19 自动服药：锁定物品绝不参与（用户硬要求）。
    locked = await store.item_locks(str(qq))
    # 出战傀儡（2026-11-20 第二轮）：野外遭遇是最主要的刷怪入口，必须带。
    _pu, _pu_n, _pu_fm = await _puppet_ctx(qq, p)
    res = core.encounter(p, locked=locked, puppet=_pu, formation_n=_pu_n,
                         puppet_flame_mult=_pu_fm)
    # 2026-11-17：统一「战斗结束」落盘点。普通回包仍只给高光，完整 log 只落盘，
    # 玩家主动「战斗回看」时才展开，绝不把 1~5 敌的中间回合塞进普通回包。
    await _finalize_battle(
        p, qq, res, "遭遇", opponent="、".join(str(x) for x in (res.get("enemies") or [])),
        puppet=_pu,
    )
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

    # 2026-11-19 原著「战斗中夺取火能」：胜利的火能进 alchemy_tower 真实列。
    # 只增不减，失败不给；数值由 core 按怪物等级算好（lv//TOWER_ENERGY_PER_LV）。
    fire_gain = int(res.get("fire_energy_gain") or 0)
    if fire_gain > 0:
        tst = await store.tower_state(str(qq))
        core.tower_add_energy(tst, fire_gain)
        await store.set_tower_state(str(qq), tst)
    # 2026-11-20 傀儡多配方材料：野外/周本掉的料进**傀儡库真实列**（puppet_materials），
    # 不进 players.data 的 bag（最高声明）。写失败不静默——在回包里如实说出来。
    pup_fail: list = []
    for _nm, _n in (res.get("puppet_drops") or []):
        try:
            await DEFAULT_PUPPET_STORE.add_material(str(qq), _nm, int(_n))
        except Exception as e:  # noqa: BLE001
            pup_fail.append("%s（%s）" % (_nm, type(e).__name__))
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

    # 2026-11-20 结算重排（用户「结算展示乱七八糟」）：固定三段、顺序恒定，
    # 不再用 insert() 往中间插行打乱结构。①战果 ②代价 ③机缘（仅触发时出现）。
    # 2026-11-23：首行/高光/战果/代价改由 doupo_text 出句，保留原有锚点词。
    lines = [
        txt.encounter_head(n_total, foes, res["rounds"], bool(res["win"]), killed),
    ]
    # 2026-11-24 限时增益：药力还有余就得在战报里露一行，否则玩家以为丹白吃了。
    _bl = tb.battle_line(p)
    if _bl:
        lines.append(_bl)
    # 丹纹赐福：本场用掉几次后还剩几次，战报里给一行（用完自然消失）
    for _dl in dw.lines(p):
        lines.append(_dl)
    lines.append(txt.battle_spot(spot))
    if folded > 0:
        lines.append(f"⋯ 略去中间 {folded} 式常规攻防")
    # ① 战果（修为/金币/敌人 + 战利）
    drops = list(res.get("drops") or [])
    _drop_txt = "、".join(f"{n}×{c}" for n, c in drops[:3])
    lines.append(txt.battle_gain(foes, _fmt(res["exp"]), _fmt(res["gold"]), _drop_txt))
    # 2026-11-24 魂殿邪修：吞噬修为单独一行（已写 p["exp"]/p["pill_exp"]，此处仅反馈）。
    if int(res.get("devour") or 0) > 0:
        lines.append(f"🕯️ 吞噬炼化：斩精英/BOSS 吞噬修为 +{_fmt(res['devour'])}"
                     "（邪道速成，根基随之虚浮）")
    # 傀儡料单独一行：不进 bag、直接落傀儡库，与上面「战利」不是同一本账。
    pup = list(res.get("puppet_drops") or [])
    if pup:
        lines.append("🧩 傀儡料 " + "、".join("%s×%d" % (n, int(c)) for n, c in pup))
    if pup_fail:
        lines.append("⚠️ 傀儡料未入傀儡库：" + "、".join(pup_fail))
    # 对应魔兽掉火（用户 2026-11-24）：只有真正新得的才列出，重复已折算药渣
    _bf = list(res.get("flame_drops") or [])
    if _bf:
        lines.append("🔥 收服兽火 " + "、".join(_bf))
    # ② 代价（「防御」字样固定出现：属性↔战斗强关联的稳定锚点）
    auto = res.get("auto_pills") or {}
    _pill = int(auto.get("hp", 0)) + int(auto.get("mp", 0))
    lines.append(txt.battle_cost(_fmt(res["taken"]), _fmt(p.get("hp") or 0), _pill))
    # ③ 机缘（恒定排最后，触发才出现）
    if res.get("hero_aid"):
        lines.append(txt.battle_hero_aid())
    lv = int(p["lv"])
    if core.can_level(p):
        lines.append(txt.battle_ready(cfg.full_realm(lv)))
    lines.extend(_chance_lines(chance, qq))
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 完整战斗回看 / 战斗评价（2026-11-17 用户硬要求）
# ---------------------------------------------------------------------------
async def cmd_battle_review(store, qq) -> str:
    """最后一次战斗的完整逐招文本兜底；正常路径由 matcher 出卡片。"""
    p = await _player(store, qq)
    if not p:
        return NOT_ENTERED
    last = p.get("last_battle")
    if not isinstance(last, dict) or not last.get("log"):
        return "还没有可回看的战斗记录。先打一场「战斗」再来。"
    return br.text(last)


async def cmd_battle_comment(store, qq) -> str:
    """基于最后一场战斗真实数据给一句毒舌；同一场结果稳定。"""
    p = await _player(store, qq)
    if not p:
        return NOT_ENTERED
    last = p.get("last_battle")
    if not isinstance(last, dict) or not last.get("log"):
        return "还没有可评价的战斗。先打一场「战斗」，再看看自己表现。"
    return bc.pick(last, str(qq))


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

    head.append(txt.enhance_ok())
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
    """「斗技」= 已上阵 + 已领悟（一张卡看完），2026-11-24 用户口径。

    原「斗技」（四槽上阵）与「斗技 全部」（已学习名录）合并成一条口令：
      ① 已上阵：四槽真实出战技（含等级）；
      ② 已领悟：其余已学技，按三阶分组，含转宗保留的他宗技；
      ③ 异火技：已收服但未上阵的主动技（收服即会）。
    「宗门斗技」仍单独看本宗池 + 上阵编号，两者职责不重叠。
    """
    p = await _player(store, qq)
    if not p:
        return NOT_ENTERED
    learned = core.learned_skill_pool(p)
    lo = core.loadout_entries(p)
    lo_names = {e["name"] for e in lo}
    fac_name = p.get("faction_name") or "无宗门"
    n_ben = sum(1 for e in learned if e.get("origin") == "本宗")
    n_other = sum(1 for e in learned if e.get("origin") == "他宗")
    n_common = sum(1 for e in learned if e.get("origin") == "通用")
    out = [f"📜 我的斗技 · 已上阵 {len(lo)}/{cfg.LOADOUT_SIZE} · 已领悟 {len(learned)} 门"
           f"（本宗 {n_ben} / 他宗 {n_other} / 通用 {n_common}）"]

    def _tag(e: dict) -> str:
        return {"通用": "〔通用〕", "他宗": "〔他宗〕", "本宗": "〔本宗〕",
                "异火": "〔异火〕"}.get(e.get("origin"), "")

    # ① 已上阵（四槽顺序 = 出战顺序）
    if lo:
        out.append("〔已上阵〕")
        for e in lo:
            lv = int((p.get("skills") or {}).get(e["name"], 1))
            out.append(f"　{_tag(e)}{e['name']} Lv{lv}（{e['tier']}）")

    # ② 其余已领悟，按三阶分组（与「宗门斗技」同序）
    for tier in cfg.SKILL_TIERS:
        rows = [e for e in learned if e["tier"] == tier
                and e["name"] not in lo_names and e.get("origin") != "异火"]
        if not rows:
            continue
        out.append(f"〔{tier}〕斗气 {int(cfg.SKILL_TIER_MP_FRAC[tier] * 100)}%·mp_max")
        for e in rows:
            lv = int((p.get("skills") or {}).get(e["name"], 1))
            out.append(f"　{_tag(e)}{e['name']} Lv{lv}")

    # ③ 已收服异火技（收服即会；已上阵的不重复列，避免同一门出现两次）
    flames = [e for e in learned
              if (e.get("faction") == "异火" or e.get("origin") == "异火")
              and e["name"] not in lo_names]
    if flames:
        out.append(f"〔异火技〕斗气 {int(cfg.SKILL_TIER_MP_FRAC['特殊'] * 100)}%·mp_max　收服即解锁")
        for e in flames:
            out.append(f"　{e['name']}（{e.get('flame', '异火')}·可学）")

    out.append("")
    out.append(f"　当前宗门【{fac_name}】；本门传承与上阵编号见「宗门斗技」")
    out.append("　〔他宗〕= 转宗前领悟、依原著「技艺已学不会忘」保留的斗技")
    out.append(f"　升一级伤害 +20%，消耗 {cfg.SKILL_UP_ITEM}；用法：升级 <技名> / 上阵 <4 技名>")
    return "\n".join(out)


async def cmd_faction_skills(store, qq) -> str:
    """「宗门斗技」= 当前所属宗门的技能池（含未解锁），2026-11-24 新增。

    与「斗技」（已上阵 + 已领悟合并卡）互补：这条**只**看本门传承 + 通用 +
    已收服异火技，数据走 core.skill_pool（本宗池）；卡上的序号圆是
    「上阵 1 3 5 7」的唯一编号源，故合并卡不能替代它。
    """
    p = await _player(store, qq)
    if not p:
        return NOT_ENTERED
    pool = core.skill_pool(p)
    lo = core.loadout_entries(p)
    fac_name = p.get("faction_name") or "无宗门"
    own = sum(1 for e in pool if e.get("origin") == "本宗")
    common = sum(1 for e in pool if e.get("origin") == "通用")
    out = [f"🏯 {fac_name} · 本门斗技（本宗 {own} / 通用 {common} · 上阵 {len(lo)}/{cfg.LOADOUT_SIZE}）"]
    for tier in cfg.SKILL_TIERS:
        rows = [e for e in pool if e["tier"] == tier and e.get("faction") != "异火"]
        if not rows:
            continue
        out.append(f"〔{tier}〕斗气 {int(cfg.SKILL_TIER_MP_FRAC[tier] * 100)}%·mp_max")
        for e in rows:
            lv = int((p.get("skills") or {}).get(e["name"], 1))
            mark = "★上阵" if e["equipped"] else ("可学" if e["unlocked"] else "🔒未解锁")
            tag = "〔通用〕" if e.get("origin") == "通用" else ""
            out.append(f"　{tag}{e['name']} Lv{lv}（lv{e['unlock']} 解锁·{mark}）")
    flames = [e for e in pool if e.get("faction") == "异火"]
    if flames:
        out.append(f"〔异火技〕斗气 {int(cfg.SKILL_TIER_MP_FRAC['特殊'] * 100)}%·mp_max　收服即解锁")
        for e in flames:
            mark = "★上阵" if e["equipped"] else "可学"
            out.append(f"　{e['name']}（{e.get('flame', '异火')}·{mark}）")
    out.append("")
    out.append("　我领悟的全部斗技（含转宗保留的他宗技）见「斗技」")
    out.append("　「宗门斗技」出本门名录卡，序号即上阵编号（上阵 1 3 5 7）")
    return "\n".join(out)


#: 出戏词：出现在「出处」里就等于把考据台账摊给玩家看，一律不印。
_SOURCE_META = ("游戏自设", "游戏补白", "原著无载", "原著无", "语料", "考据",
                "游戏原创", "游戏命名", "本作")
#: 考据标签（[C1] / [G1] / [S2]…）是写给我自己溯源用的，不进玩家视野。
_SOURCE_TAG = re.compile(r"\s*\[[A-Z]\d+\]")


def _clean_source(src) -> str:
    """把「出处」洗成玩家能看的一句话；纯考据来源直接返回空串（整行不印）。"""
    t = _SOURCE_TAG.sub("", str(src or "")).strip()
    if not t or any(w in t for w in _SOURCE_META):
        return ""
    return t


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
    src = _clean_source(hit.get("source"))
    if src:
        out += ["", f"出处：{src}"]
    # 2026-11-20 沉浸式铁律（第二轮加严）：章节引文（novel）是考据台账，
    # 玩家不需要在斗技详情里看到「第X章「标题」」——那是写给我自己核对的。
    # novel 字段继续保留给 _verify_novel / 守门逐字校验，只是不再渲染。
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
            if r == "devour_locked":
                return ("🕯️ 你已入魂殿，邪道不归路——拜入魂殿者终生不得再转宗门。\n"
                        "　既已噬魂为生，便再无回头之日（吞噬炼化随行，斩精英/BOSS 吞其修为）。")
            if r == "notfound":
                return ("当前境界没有这个宗门。可转："
                        + "、".join(res.get("options") or ["（无）"]))
            return "转宗失败，请检查宗门名。"
        await store.put_player(p)
        flame_line = ""  # 2026-11-24：转宗不再补发异火（异火稀有）
        # 2026-11-24 魂殿邪修：转入魂殿是不可逆邪道，明确告知吞噬被动与锁死。
        devour_line = ""
        if str(res.get("faction") or "") == cfg.DEVOUR_SECT:
            devour_line = ("\n🕯️ 邪道不归：自此不得再转宗；已领悟斗技尽数保留，"
                           "且自带【吞噬炼化】——每斩精英/BOSS 吞其修为（根基随之虚浮）。")
        return (txt.sect_change(res["name"]).rstrip("。")
                + f"（原【{res['old']}】），耗 {res['cost']:,} 金币。\n"
                f"　四槽已按新门重配：" + "、".join(res["loadout"])
                + flame_line
                + "\n　已升的技能等级按技名保留；本门斗技见「宗门斗技」，全部已学见「斗技」。"
                + devour_line)

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
    # 2026-11-25 用户「升级斗技不行」：`升级 帝印诀`（诀）对不上 `帝印决`，旧代码只做
    # 全字/子串匹配 → 回「无此斗技」。这里先走同音字归一，能唯一解出就用正式名。
    canon = core.resolve_skill_name(p, name, names)
    if canon:
        name = canon
    hit = next((n for n in names if n == name), None) or next(
        (n for n in names if name in n or n in name), None)
    if not hit:
        hit = next((n for n in cfg.SKILL_NAMES if name in n or n in name), None)
    if not hit:
        # 2026-11-25 用户「升级斗技不行」（`升级 帝炎破世`）：异火主动技**能上阵却不能升级**
        # ——它不在 learned 里（收服即有），skill_up 也只认 learned。旧文案「无此斗技」
        # 还把「可选」列成六门本宗技，玩家会以为自己打错字。这里单独讲清楚：去养火。
        try:
            flame_names = [e["name"] for e in _sk.fs.entries_for(p)]
        except Exception:  # noqa: BLE001 — 查不到异火池就按普通「无此斗技」处理
            flame_names = []
        fl = core.resolve_skill_name(p, name, flame_names) if flame_names else ""
        if fl:
            return (f"【{fl}】是异火主动技，不走升技材料：它的强度随「异火等级」走。\n"
                    f"　发「异火」看当前火种，养火升阶才涨伤害。")
        return "无此斗技。可选：" + "、".join(names)
    res = core.skill_up(p, hit)
    await store.put_player(p)
    if not res.get("ok"):
        if res.get("reason") == "max":
            return f"【{hit}】已至 {res['lv']} 级圆满。"
        if res.get("reason") == "noskill":
            return f"你还没领悟【{hit}】——先发「学斗技」领悟，或「斗技详情」看本宗能学什么。"
        # 2026-11-25：「❌ 焚血散不足」不说升哪门技，玩家无从判断是不是点错了技名
        item = str(res.get("item", cfg.SKILL_UP_ITEM))
        return (f"❌ 升【{hit}】要 {item} ×{res['need']}，你有 {res['have']}。\n"
                f"　发「炼制 {item}」自己炼，或「宝库」看看有没有存货。")
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
        return "要学哪门斗技？例：「学习 风灵分形剑」。发「宗门斗技」看名录。"
    res = core.learn_named(p, name)
    note = _migration_note(res)
    if not res.get("ok"):
        if res.get("migrated") is not None:
            await store.put_player(p)
        why = res.get("reason")
        if why == "noskill":
            sug = res.get("suggest") or []
            if sug:
                # 2026-11-25：同音字/漏字（「帝印诀」）别只说「没有这门」，
                # 直接给候选，玩家点一下就能学。
                return note + (f"本宗没有【{name}】这门斗技。你是不是想学："
                               + "、".join(f"「学习 {n}」" for n in sug) + "？")
            return note + f"本宗没有【{name}】这门斗技，发「宗门斗技」看名录。"
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
        # 🚨 2026-11-25 用户「上阵 11 14 就不行了」：旧文案把 skill_pool（已解锁）**全部**
        # 写进「可上阵」，其中含「可学但未领悟」的技——用户照着序号打必然被拒，看着
        # 就像口令坏了。这里按 is_equippable 分成「可上阵（已领悟）」与「待领悟」两档，
        # 序号仍与名录卡严格同源（enumerate 顺序不变，只是分组显示）。
        learned = set(core.equippable_names(p))
        pool_all = core.skill_pool_sorted(p)
        ready = [f"{i}.{e['name']}" for i, e in enumerate(pool_all, 1) if e["name"] in learned]
        todo = [f"{i}.{e['name']}" for i, e in enumerate(pool_all, 1) if e["name"] not in learned]
        lines = ["当前上阵：" + ("、".join(f"{e['name']}[{e['tier']}]" for e in cur) or "（空）"),
                 "可上阵（已领悟）：" + ("、".join(ready) or "（无）")]
        if todo:
            lines.append("待领悟（先发「学习 <技名>」）：" + "、".join(todo))
        lines.append(f"用法：上阵 1 3 5 7（名录卡序号）或 <技名>（最多 {cfg.LOADOUT_SIZE} 门）")
        return "\n".join(lines)
    chosen = []
    pool_names = [e["name"] for e in pool]
    for n in names:
        # 2026-11-25 同「升级」：`上阵 帝印诀`（诀）也要能对上 `帝印决`（决）。
        canon = core.resolve_skill_name(p, n, pool_names)
        if canon:
            n = canon
        hit = next((e for e in pool if e["name"] == n), None) or next(
            (e for e in pool if n in e["name"] or e["name"] in n), None)
        if not hit:
            # 同一口径：这里也只列「已领悟」的，别再把未领悟的技当成可上阵（见上）。
            return (f"「{n}」不可上阵（未解锁或非本宗）。当前可上阵："
                    + ("、".join(core.equippable_names(p)) or "（无，先发「学斗技」）"))
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


def _unflame_hint(name: str) -> str:
    """未收服异火的获取方向，单一真值取自 config 的分线表（不另建真值源）。

    三层投放后不再有「本宗异火入道即得」的通用话术：starter 是全宗门统一的
    B 线数值火，S 走境界+主线门槛，A 藏隐藏支线（不明示），其余走常规机缘。
    """
    tier = (getattr(cfg, "FLAME_TIER", {}) or {}).get(name, "")
    if name == cfg.FLAME_DIYAN:
        return "帝炎须踏斗帝之路，诸火自燃归一身。"
    if tier == "S":
        gate = (getattr(cfg, "S_FLAME_GATE", {}) or {}).get(name) or {}
        realm_idx = int(gate.get("realm", 0))
        realm_txt = cfg.full_realm(cfg.realm_lv(realm_idx)) if realm_idx else "大后期"
        return "此火为大后期之火，须修至%s、主线推进到位方可收服。" % realm_txt
    if tier == "A":
        return "此火隐于某段机缘，了结相应之事自会现身，事前不会言明。"
    if name in cfg.BEAST_FLAME_NAMES:
        return "此火为兽火，九星突破战胜强敌或炼制对应兽火单方可得。"
    return "游历机缘或九星突破可得，先收服再喂养。"


def flame_usage_hint(p: dict) -> str:
    """异火卡/文本底部的「用法」一行：必须指向玩家**真能养**的那一朵。

    A 方案前这里写死「养火 青莲地心火」——迦南玩家手里是陨落心炎，照抄必然被
    「尚未收服」挡回，属于自己给自己挖坑的提示。
    """
    owned = [n for n, v in (p.get("flames") or {}).items() if int(v or 0) > 0]
    if owned:
        return "用法：养火 %s（一次喂光现有药渣）" % owned[0]
    return "用法：先得一朵火——九星突破掉落兽火/异火，或炼制兽火单方；再「养火 <火名>」"


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
    out.append("　以下异火须先收服才可喂养（未收服不可养）：")
    for f in cfg.FLAMES:
        name = f["name"]
        lv = int(fl.get(name, 0))
        if name == cfg.FLAME_DIYAN:
            state = "已收服" if p.get("diyan") else "未得（成就斗帝自燃）"
            out.append(f"　{f['icon']} {name}　{state}")
        elif lv <= 0:
            out.append(f"　{f['icon']} {name}　未收服")
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
        # 招式章节引文（a["novel"]）只作内部考据，不印——玩家看主动/被动说明就够。
    # B 线数值异火：只列已收服（新号 1 朵 starter），无招式特效、只给数值加成。
    b_owned = [f for f in getattr(cfg, "BONUS_FLAMES", [])
               if int(fl.get(f["name"], 0)) > 0]
    if b_owned:
        out.append("　数值异火（无招式，按等级给加成）：")
        for f in b_owned:
            name = f["name"]
            lv = int(fl.get(name, 0))
            kind = "炼药" if f["kind"] == "alchemy" else "傀儡"
            out.append(f"　　{f['icon']} {name}　{lv}/{cfg.FLAME_MAX_LV} 级"
                       f"　{kind}+{int(round(float(f['value']) * 100))}%/级")
    # 兽火 + 新生异火（九星掉落 / 兽火单方）：只列已收服。
    o_owned = [f for f in getattr(cfg, "OTHER_FLAMES", [])
               if int(fl.get(f["name"], 0)) > 0]
    if o_owned:
        out.append("　兽火 · 新生异火（九星突破或炼制单方所得，按等级给加成）：")
        for f in o_owned:
            name = f["name"]
            lv = int(fl.get(name, 0))
            kind = "兽火" if name in cfg.BEAST_FLAME_NAMES else "异火"
            stat = {"alchemy": "炼药", "atk": "攻击", "hp": "气血"}.get(
                f["bonus"], "数值")
            out.append(f"　　{f['icon']} {name}（{kind}）　{lv}/{cfg.FLAME_MAX_LV} 级"
                       f"　{stat}+{int(round(float(f['value']) * 100))}%/级")
    out.append("")
    out.append("　收服后主动技可用「上阵 <技名>」放进四槽，「斗技详情 <技名>」看全文与出处")
    out.append("　" + flame_usage_hint(p))
    return "\n".join(out)


async def cmd_feed(store, qq, name) -> str:
    p = await _player(store, qq)
    if not p:
        return NOT_ENTERED
    name = (name or "").strip()
    all_names = getattr(cfg, "OWNED_FLAME_NAMES", cfg.FLAME_NAMES)
    if not name:
        return "用法：养火 火云水炎（可选：" + "、".join(all_names) + "）"
    hit = next((n for n in all_names if name in n or n in name), None)
    if not hit:
        return "无此异火。可选：" + "、".join(all_names)
    res = core.feed_flame(p, hit)
    chance = core.advance_task(p, "flame", 1) if res.get("ok") else None
    await store.put_player(p)
    if not res.get("ok"):
        if res.get("reason") == "nofire":
            return "🔥 你尚未拥有火焰，无从养起——九星突破得火或炼制兽火单方后再来。"
        if res.get("reason") == "nodross":
            return "你没有药渣。炼药失败会产药渣——去「炼制」试试。"
        if res.get("reason") == "diyan":
            return "帝炎不靠喂养，只认斗帝。"
        if res.get("reason") == "unowned":
            tip = _unflame_hint(hit)
            return "❌ 【%s】尚未收服，不能直接喂养。%s" % (hit, tip)
        if res.get("reason") == "max":
            return txt.flame_full(hit, res["lv"])
        return "无可喂养。"
    if res["done"]:
        out = [txt.feed_flame(hit, res["lv"])]
    else:
        out = [txt.feed_flame_partial(hit, res["give"], _fmt(res["left"]))]
    out.extend(_chance_lines(chance, qq))
    return "\n".join(out)


async def cmd_claim_flame(store, qq, name) -> str:
    """「收服 <异火名>」：玩家主动收服入口（2026-11-19 三层投放补齐）。

    分线口径（单一真值在 config.FLAME_TIER / S_FLAME_GATE / A_FLAME_SIDE）：
      * S 线：走 core.unlock_flame() 的境界+主线双门槛，未达标回「why」；
      * A 线：隐藏支线专属，**不给主动收服**——否则「不明示/完成后惊喜」形同虚设，
        统一回模糊提示，不暴露该火存在；
      * B 线：数值载体，可直接收服（starter 入道已自带，其余靠此入口补齐获取方式）。
    帝炎走 diyan 斗帝成就位，不进 flames，此处显式挡住。
    """
    p = await _player(store, qq)
    if not p:
        return NOT_ENTERED
    name = (name or "").strip()
    # 收服解析用全部 23 朵（FLAME_TIER），不能用 OWNED_FLAME_NAMES——后者只含
    # 旧五火+B十一火，会漏掉虚无吞炎/A 线 6 朵，导致文档写了却收不到（真机事故）。
    all_names = list((getattr(cfg, "FLAME_TIER", {}) or {}).keys()) or cfg.FLAME_NAMES
    hit = next((n for n in all_names if name in n or n in name), None)
    if not hit:
        return "无此异火，无从收服。可收服的异火见「异火」。"
    # 帝炎只认斗帝成就，不能被口令收服。
    if hit == cfg.FLAME_DIYAN:
        return "帝炎不靠收服，只认斗帝——冲上斗帝之日，诸火自燃归一身。"
    tier = (getattr(cfg, "FLAME_TIER", {}) or {}).get(hit, "")
    # A 线隐藏彩蛋：主动收服一律拒，且回话不暴露「这朵火可由口令拿到」。
    if tier == "A":
        return "此火不在可寻之列，游历中自有机缘——先去做做任务、四处走走吧。"
    res = core.unlock_flame(p, hit)
    if not res.get("ok"):
        if res.get("reason") == "owned":
            return "【%s】已在你身，无须再收服。" % hit
        if res.get("reason") == "locked":
            return "❌ 【%s】非你此刻所能承载。%s" % (hit, res.get("why", "境界或主线未至"))
        return "收服失败，机缘未至。"
    await store.put_player(p)
    if tier == "S":
        return ("🔥 你以意志与境界驭住了【%s】——此为大后期之火，"
                "「异火」可看其威，「养火」可养其力。" % hit)
    return txt.flame_capture(hit)


# ---------------------------------------------------------------------------
# 宝库 / 炼药 / 背包
# ---------------------------------------------------------------------------
async def cmd_shop(store, qq, beast: bool = False) -> str:
    """宝库文本。``beast``（2026-11-24）：只列兽火单方材料（卡面同源分卡）。"""
    p = await _player(store, qq)
    if not p:
        return NOT_ENTERED
    from . import card as _card
    items = [it for it in core.shop_list()
             if _card._shop_is_beast(it["name"]) == bool(beast)]
    out = [("🏦 随身商店·兽火材料" if beast else "🏦 随身商店") + f"　💰 {p['gold']}"]
    for it in items:
        out.append(f"　{it['name']}　{it['price']} 金")
        out.append(f"　　{it['desc']}")
    out.append("")
    out.append("　用法：买 蓝晶草 2" if beast else "　用法：买 血灵草 2　（兽火材料发「宝库 兽火」）")
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
        n = max(1, min(cfg.SHOP_BUY_MAX, int(parts[1])))
    else:
        # 支持「买 血灵草2」这种没空格的
        for it in core.shop_list():
            if args.startswith(it["name"]):
                name = it["name"]
                rest = args[len(it["name"]):].strip()
                if rest.isdigit():
                    n = max(1, min(cfg.SHOP_BUY_MAX, int(rest)))
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
        # 2026-11-23 修速通：丹药品阶即修为门槛，等级不够时**如实报门槛**，
        # 别落到底下「没有这样货」——那会让玩家以为商店没上货。
        if res.get("reason") == "lv":
            return (f"🚫 【{name}】是 {cfg.full_realm(int(res['need_lv']))} 的丹药，"
                    f"你如今 {cfg.full_realm(int(p.get('lv', 1)))} —— 药力太猛，"
                    f"强服只会爆体（到 {res['need_lv']} 级再来）。")
        return f"没有【{name}】这样货。发「宝库」看全部。"
    out = [f"✅ 购入【{res['name']}】×{res['n']}，花费 {res['total']} 金，余 {p['gold']}。"]
    out.extend(_chance_lines(chance, qq))
    return "\n".join(out)


async def cmd_recipes(store, qq, page=1) -> str:
    """丹方清单。等级不够的丹药不展示；丹药多了分页（与卡片同源）。"""
    p = await _player(store, qq)
    if not p:
        return NOT_ENTERED
    alch_idx = int(p.get("alch", 0))
    alch_label = cfg.alch_label(alch_idx)
    chunk, page, total_pages = core.recipes_page(p, page)
    avail = core.available_recipes(p)
    # 2026-11-18：成功率按「炼药师品阶 vs 丹药品阶」变化，成数表述不用百分号。
    # 每枚丹药行给该玩家真实把握，杜绝「四品炼一品和炼四品一个概率」。
    out = [f"⚗️ 炼药　{alch_label}",
           f"　高品阶炼低阶更稳（每低1档加成）· 越阶骤降（越1档剩二成、越2档几无）",
           f"　境界 {cfg.full_realm(int(p.get('lv', 1)))} · 可用丹方 {len(avail)} 枚 · 第 {page}/{total_pages} 页"]
    for grade in cfg.GRADE_ORDER:
        rows = [r for r in chunk if r["grade"] == grade]
        if not rows:
            continue
        out.append(f"【{grade}丹药】")
        for r in rows:
            need = r.get("need") or {}
            rate, diff = cfg.craft_rate_for(alch_idx, r.get("grade", ""))
            tag = cfg.effect_tag(r.get("effect", ""))
            badge = f"〔{tag}〕" if tag else ""
            if diff > 0:
                rate_txt = f"{cfg.rate_cn(rate)}把握 · 越{diff}档"
            else:
                rate_txt = f"{cfg.rate_cn(rate)}把握"
            if need:
                mats = "、".join(f"{k}×{v}" for k, v in need.items())
                ok = all(core.count_item(p, k) >= v for k, v in need.items())
                mark = "✅" if ok else "　"
                out.append(f"　{mark}{badge}{r['name']}　（{mats}）　{rate_txt}")
            else:
                out.append(f"　{badge}{r['name']}　（{int(r.get('buy', 0))} 金购买）　{rate_txt}")
            # 2026-11-18 假药普查：先说「本作真会发生什么」（唯一源 cfg.recipe_impl），
            # 2026-11-20 起不再补「原著对照」那一行：玩家只看本作承诺，
            # 功效出处留在 rec["desc"] / rec["src"] 内部字段。
            body = cfg.recipe_impl(r)
            if body:
                out.append(f"　　效果：{body}")

    out.append("")
    if total_pages > 1:
        out.append(f"　发「炼药2」…「炼药{total_pages}」翻页，或点下方按钮")
    out.append("　用法：炼制 回灵丹（有药方）／买 丹名（无药方）")
    return "\n".join(out)


def _parse_craft_count(arg: str) -> tuple[str, int]:
    """从「炼制」参数里拆出 (丹名片段, 炉数)。

    只认明确的批量写法，避免误伤丹名里的数字：
      * 「回灵丹 5次」/「回灵丹5炉」/「回灵丹 5」→ 5
      * 「回灵丹×5」/「回灵丹x5」/「回灵丹*5」→ 5
      * 其余（含「炼制 回灵丹」）→ 炉数 1
    """
    import re
    s = (arg or "").strip()
    m = re.search(r"[×xX*]\s*(\d+)\s*$", s)
    if m:
        return s[:m.start()].strip(), max(1, int(m.group(1)))
    m = re.search(r"\s+(\d+)\s*(?:次|炉|个|副)?\s*$", s)
    if m and int(m.group(1)) > 1:
        return s[:m.start()].strip(), max(1, min(int(m.group(1)), cfg.CRAFT_BATCH_MAX))
    return s, 1


async def cmd_craft(store, qq, name) -> str:
    p = await _player(store, qq)
    if not p:
        return NOT_ENTERED
    name = (name or "").strip()
    if not name:
        return "用法：炼制 回灵丹（发「炼药」看全部丹方）"
    name, n_furnace = _parse_craft_count(name)
    if not name:
        return "用法：炼制 回灵丹 5次（一次连炼5炉）／炼制 回灵丹（单炉）"
    if n_furnace > 1:
        return await _cmd_craft_batch(store, qq, p, name, n_furnace)
    hit = next((n for n in cfg.RECIPE_NAMES if name in n or n in name), None)
    if not hit:
        return "无此丹方。发「炼药」看全部。"
    res = core.craft(p, hit)
    if res.get("reason") == "nofire":
        return "🔥 你尚未拥有火焰，无法开炉炼药——异火举世罕见，可于九星突破战胜强敌，或炼制兽火单方获得。"
    if res.get("reason") == "buyonly":
        return f"【{hit}】你手上没有此方，无法开炉——去「宝库」直接「买 {hit}」（{res['buy']} 金），或先寻得丹方。"
    if res.get("reason") == "cd":
        await store.put_player(p)
        return txt.craft_cd(res["left"])
    chance = core.advance_task(p, "craft", 1) if res.get("ok") else None
    await store.put_player(p)
    # 2026-11-18 越阶提示：原著第495章四品勉强炼五品「成功率太小」，
    # 越2档以上「几无可能」。diff>0 时把原因写进回包，玩家才知道为什么老失败。
    over = int(res.get("diff") or 0)
    over_hint = txt.craft_over(over) if over else ""
    if not res.get("ok"):
        if res.get("reason") == "mat":
            return txt.craft_mat("、".join(res["missing"]))
        if res.get("reason") in ("fail",):
            msg = txt.craft_fail(res["dross"], cfg.rate_cn(res["rate"]), over_hint,
                                 seed=int(res["dross"]))
            if res.get("alch_up"):
                msg += "\n" + txt.alch_up(res["alch_up"])
            return msg
        return txt.craft_fail_plain()
    _grade = str(cfg.RECIPE_BY_NAME.get(hit, {}).get("grade") or "")
    msg = txt.craft_success(hit, _grade, cfg.rate_cn(res["rate"]))
    if res.get("danwen"):
        msg += "\n" + txt.craft_danwen(hit, 1)
    if over:
        msg += "\n" + over_hint.rstrip("\n")
    if res.get("alch_up"):
        msg += "\n" + txt.alch_up(res["alch_up"])
    out = [msg]
    out.extend(_chance_lines(chance, qq))
    return "\n".join(out)


async def _cmd_craft_batch(store, qq, p, name, n) -> str:
    """批量连炼回包（一条消息，纯中文成数）。

    一处输出=一条消息：成败、经验、斗气、晋阶全并进这段文本，不拆两条。
    """
    hit = next((nm for nm in cfg.RECIPE_NAMES if name in nm or nm in name), None)
    if not hit:
        return "无此丹方。发「炼药」看全部。"
    res = core.craft_batch(p, hit, n)
    if res.get("reason") == "nofire":
        return "🔥 你尚未拥有火焰，无法开炉炼药——可于九星突破得火，或炼制兽火单方。"
    if res.get("reason") == "buyonly":
        return f"【{hit}】你手上没有此方，无法开炉——去「宝库」直接「买 {hit}」。"
    if res.get("reason") == "cd":
        await store.put_player(p)
        return txt.craft_cd(res["left"])
    if not res.get("ok"):
        # craft_batch 的 not-ok 只有 nodrug / buyonly / cd / nofire；mat/mp 是 limited。
        if res.get("reason") == "mat" and res.get("missing"):
            return txt.craft_mat("、".join(res["missing"]))
        return txt.craft_generic_fail()

    # 任务推进按实际成功炉数（一炉一丹，成功才算练手）
    chance = core.advance_task(p, "craft", int(res["n_ok"])) if res["n_ok"] else None
    await store.put_player(p)

    lines = [txt.craft_batch_head(hit, res["n_done"], res["n_ok"], res["n_fail"],
                                  cfg.rate_cn(res["rate"]))]
    detail = txt.craft_batch_detail(res["n_ok"], res["dross"], res["exp"])
    if res.get("exp_factor", 1.0) < 1.0:
        detail += f"（低阶丹，经验{cfg.rate_cn(res['exp_factor'])}计）"
    lines.append(detail)
    if res.get("n_danwen"):
        lines.append(txt.craft_danwen(hit, int(res["n_danwen"])))
    lines.append(f"　斗气 -{res['mp_used']}（余{res['mp_left']}）")
    if res.get("limited") in ("mat", "mp"):
        lines.append(txt.craft_batch_limited(res["limited"]))
    if res.get("alch_up"):
        lines.append(txt.alch_up(res["alch_up"]))
    out = ["\n".join(lines)]
    out.extend(_chance_lines(chance, qq))
    return "\n".join(out)


async def cmd_bag(store, qq) -> str:
    p = await _player(store, qq)
    if not p:
        return NOT_ENTERED
    items = bagview.sorted_items(p)
    locked = await store.item_locks(str(qq))
    # 2026-11-24：用户「背包别搞翻页，延长」—— 一次列全，丹药优先排前，
    # 带纹丹显示基础丹名 + 括号纹数（不出现「·三纹」这种后缀）。
    out = [f"🎒 包裹（{len(items)} 种物品 · 丹药优先）　💰 {p['gold']}"]
    # 包裹里的丹药同样只显示「本作真会发生的事」，不印原著承诺。
    # 2026-11-23：按 RECIPE_BY_NAME 遍历，别名（复紫灵丹）也有说明，不会在包裹里变哑巴。
    known = {n: (cfg.recipe_impl(r) or r.get("desc", ""))
             for n, r in cfg.RECIPE_BY_NAME.items()}
    known.update({h["name"]: h["desc"] for h in cfg.HERBS})
    if not items:
        out.append("　空空如也。")
    # 2026-11-25：带纹丹改为**档位前置**（`一纹破厄丹`），名字自带纹数，
    # 不再需要「基础名 + 括号纹数」那套补偿写法。
    for i, (name, n) in enumerate(items, 1):
        mark = "🔒" if (name in locked) else ""
        out.append(f"　[{i}] {mark}{name} ×{n}")
        if name in known:
            out.append(f"　　　{known[name]}")
    out.append("")
    out.append("　用法：服丹 回灵丹　｜　查看 回灵丹 看详情　｜　"
               "锁定 回灵丹（锁定后战斗不会自动服用）")
    return "\n".join(out)


# ---------------------------------------------------------------------------
# 背包锁定（2026-11-19 用户「背包加上锁定物品功能，锁定的物品不会被自动使用」）
# ---------------------------------------------------------------------------
def _match_item_name(name: str) -> str:
    """把玩家输入对到真实物品名：先精确，再包含匹配（防子串抢先）。"""
    name = (name or "").strip()
    if not name:
        return ""
    pool = (list(cfg.RECIPE_NAMES) + list(cfg.MATERIAL_NAMES)
            + list(cfg.HERB_NAMES) + ["炎星石"])
    if name in pool:
        return name
    hits = [x for x in pool if x in name or name in x]
    return sorted(hits, key=len)[0] if hits else ""


# ---------------------------------------------------------------------------
# 「带纹丹」输入容错（2026-11-25 用户「我要服用有丹纹的丹药，应该怎么输入」）
# ---------------------------------------------------------------------------
# 背包**显示名**刻意剥掉了纹数后缀（用户「丹药名称不要变」，纹数交给右上角角标），
# 所以玩家眼里那格就叫「聚气散」，照卡面输入只会命中**不带纹**那枚；而带纹丹的真名
# 是「聚气散·一纹 / ·二纹 / ·三纹」（旧档还有「·丹纹」），玩家无从得知该打什么。
# 这里把「·／.／空格／括号／无分隔、纹数在前在后」各种写法统一吃掉，
# 并且**只认背包里真有的那一枚**——绝不拿别的纹顶替（三纹很稀有，误吃就是坑）。
_TIER_WORDS = (("三纹", 3), ("二纹", 2), ("一纹", 1), ("丹纹", 1),
               ("3纹", 3), ("2纹", 2), ("1纹", 1))


def _norm_item_text(s: str) -> str:
    """物品名归一化：各种「点」→ ·，去空格/横线/括号，压掉重复的 ·。"""
    t = str(s or "").strip()
    for ch in ("．", ".", "・", "•", "‧", "｡", "*", "＊"):
        t = t.replace(ch, "·")
    for ch in (" ", "\t", "-", "—", "_", "(", ")", "（", "）",
               "【", "】", "《", "》", "、", "，", ","):
        t = t.replace(ch, "")
    while "··" in t:
        t = t.replace("··", "·")
    return t.strip("·")


def resolve_owned_item(p, raw: str) -> tuple[str, bool]:
    """玩家手打的名称 → **背包里真实存在、且可服用/可炼化**的物品名。

    :return: ``(真实物品名, 是否自动换成了带纹丹)``；解不出返回 ``("", False)``。

    规则（顺序即优先级）：
      ① 原样命中（含 ``·二纹`` 后缀）→ 直接用；
      ② 拆出纹数词（前/后置都认）→ 命中该纹；**指定了纹数就不许换别的纹**；
      ③ 没写纹数 → 有纯丹先吃纯丹（带纹的留着），没纯丹才按 一→二→三 取最低纹。
    """
    usable = set(cfg.RECIPE_BY_NAME) | set(cfg.MATERIAL_BY_NAME)
    bag = core.bag(p)
    t = _norm_item_text(raw)
    if not t:
        return "", False
    if t in bag and t in usable:
        return t, False
    tier = 0
    base = t
    for word, n in _TIER_WORDS:
        if base.endswith(word) and len(base) > len(word):
            tier, base = n, base[: -len(word)].strip("·")
            break
        if base.startswith(word) and len(base) > len(word):
            tier, base = n, base[len(word):].strip("·")
            break
    base = base.strip("·")
    if not base:
        return "", False
    if tier:
        # 🚨 旧后缀「·丹纹 / ·一纹」**只等价一纹**：只把 1 纹的候选带上它们。
        # 旧实现无条件把 legacy 塞进候选 → 玩家要「二纹」而包里只有「·丹纹」时会被
        # 顶替成一纹（线上实测 2026-11-25 08:18 抓到），等于骗玩家吃掉另一枚丹。
        cands = [cfg.danwen_name(base, tier)]
        if tier == 1:
            cands += [base + cfg.DANWEN_LEGACY, base + "·一纹"]
        for cand in cands:
            if cand in bag and cand in usable:
                return cand, True
        return "", False          # 指定了纹数就没有替代品，交给上层回「你没有」
    if base in bag and base in usable:
        return base, False
    for i in (1, 2, 3):
        cand = cfg.danwen_name(base, i)
        if cand in bag and cand in usable:
            return cand, True
    for cand in (base + cfg.DANWEN_LEGACY, base + "·一纹"):
        if cand in bag and cand in usable:
            return cand, True
    return "", False


# ---------------------------------------------------------------------------
# 「查看 <物品名>」物品详情（2026-11-25 用户要求）
# ---------------------------------------------------------------------------
def _split_tier(t: str) -> tuple[str, int]:
    """「二纹破厄丹」/「破厄丹二纹」/「破厄丹·二纹」→ (破厄丹, 2)。"""
    for word, n in _TIER_WORDS:
        if t.startswith(word) and len(t) > len(word):
            return t[len(word):].strip("·"), n
        if t.endswith(word) and len(t) > len(word):
            return t[: -len(word)].strip("·"), n
    return t, 0


def item_lookup(raw: str) -> str:
    """玩家手打的物品名 → **已知物品名**（不要求持有，宝库里没买的也能看）。

    :return: 归一后的物品名（可能是带纹丹全名）；查无此物返回 ``""``。

    与 ``resolve_owned_item`` 的分工：那个查**背包**（能否服用/锁定，要求持有）；
    这个查**图鉴**（有没有这件东西），所以宝库药材、天材地宝、系统杂项都认。
    分类判据走 ``cfg.item_kind``（与卡片层同一张表），不在这里另判一套。
    """
    t = _norm_item_text(raw)
    if not t:
        return ""
    if cfg.item_kind(t):
        return t
    base, tier = _split_tier(t)
    if tier and base:
        cand = cfg.danwen_name(base, tier)
        if cfg.item_kind(cand):
            return cand
        # 玩家问「破厄丹二纹」但这款丹没有带纹版 —— 退回基础丹给他看本效
        if cfg.item_kind(base):
            return base
    return ""


def _view_text(name: str) -> str:
    """详情卡的**文本兜底**（只在卡片渲染失败时用；正常一律出图）。

    只给最关键的三件：本效、来历、丹纹，不铺赐福池 —— 那是卡片的活。
    """
    kind = cfg.item_kind(name)
    base = cfg.base_of_danwen(name) if kind == "danwen" else name
    lines = ["【%s】" % name]
    if kind in ("pill", "danwen"):
        rec = cfg.RECIPE_BY_NAME.get(base) or {}
        lines.append("本效：%s" % (cfg.recipe_impl(rec) or rec.get("desc") or "—"))
        need = rec.get("need") or {}
        if need:
            lines.append("炼制：%s" % "、".join("%s×%d" % (k, int(v))
                                              for k, v in need.items()))
        elif int(rec.get("buy", 0) or 0) > 0:
            lines.append("宝库：%d 金一枚" % int(rec["buy"]))
        if cfg.danwen_allowed(rec):
            lines.append("丹纹：成功后有 %d%% 几率出纹，一/二/三纹再按 %s 分。"
                         % (int(round(cfg.danwen_rate(str(rec.get("grade") or "")) * 100)),
                            "/".join("%d%%" % int(round(w * 100)) for w in cfg.DANWEN_TIER_W)))
        return "\n".join(lines)
    if kind == "herb":
        h = cfg.HERB_BY_NAME.get(name) or {}
        lines.append("本效：%s" % (h.get("desc") or "—"))
        lines.append("宝库：%d 金一味" % int(h.get("price", 0) or 0))
        return "\n".join(lines)
    if kind == "material":
        m = cfg.MATERIAL_BY_NAME.get(name) or {}
        lines.append("本效：%s" % (m.get("desc") or "—"))
        if int(m.get("price", 0) or 0) > 0:
            lines.append("宝库：%d 金一份" % int(m["price"]))
        return "\n".join(lines)
    misc = cfg.MISC_ITEMS.get(name)
    if misc:
        return "\n".join(["【%s】%s" % (name, misc["kind"])] + list(misc["rows"]))
    return "【%s】傀儡材料，凑齐配方即开炉。" % name


async def cmd_view(store, qq, arg: str = "") -> str:
    """「查看 <物品名>」的兜底文本。正常路径由 ``card.render_item_detail`` 出图。"""
    arg = (arg or "").strip()
    if not arg:
        return ("用法：查看 <物品名>　例：查看 破厄丹 / 查看 二纹破厄丹 /"
                " 查看 冰晶石\n　丹药看得见本效、丹纹各档概率与赐福、从何而来；"
                "药材与天材地宝看得见价格与用途。")
    name = item_lookup(arg)
    if not name:
        return ("没找到「%s」这件东西。\n　发「包裹」看自己有什么 · 发「宝库」看药材 · "
                "发「炼药」看能炼的丹方" % arg)
    return _view_text(name)


async def cmd_lock(store, qq, name, on: bool = True) -> str:
    """锁定 / 解锁一件物品。锁定只拦「战斗自动服药」，手动服丹照旧可以。"""
    p = await _player(store, qq)
    if not p:
        return NOT_ENTERED
    # 与服丹同一套归一：`锁定 聚气散二纹` 也要落到真实物品名上
    owned, _auto = resolve_owned_item(p, name)
    hit = owned or _match_item_name(name)
    if not hit:
        return "用法：锁定 回灵丹　/　解锁 回灵丹"
    if core.count_item(p, hit) <= 0:
        return f"包裹里没有【{hit}】。"
    changed = await store.set_item_lock(str(qq), hit, bool(on))
    locks = await store.item_locks(str(qq))
    verb = "已锁定" if on else "已解锁"
    head = (f"🔒 {verb}【{hit}】：战斗中不会自动服用，手动「服丹 {hit}」不受影响。"
            if on else f"🔓 {verb}【{hit}】：恢复自动服用。")
    if not changed:
        head = f"（{hit} 本来就是{'锁定' if on else '未锁定'}状态）" + head
    row = "、".join(sorted(locks)) if locks else "无"
    return f"{head}\n当前锁定：{row}"


# ---------------------------------------------------------------------------
# 后期天焚炼气塔（聚火壶）——2026-11-19 修炼大改
# ---------------------------------------------------------------------------
def _tower_head(st: dict) -> str:
    tier = core.tower_tier(st.get("card_tier", 0))
    name = cfg.FACTION_CARD_NAMES[tier]
    return (f"🔥 天焚炼气塔 · {name}"
            f"（可入第 1~{core.tower_max_floor(tier)} 层）"
            f"　心火 ×{core.tower_mult(tier, bool(st.get('magma'))):.1f}")


async def cmd_furnace_status(store, qq) -> str:
    p = await _player(store, qq)
    if not p:
        return NOT_ENTERED
    st = await store.tower_state(str(qq))
    got = core.tower_daily_energy(st, date.today().isoformat())
    # 不在塔中时按离塔时间自然消解火毒（只读展示也要如实结算）
    if not int(st.get("in_tower") or 0):
        core.alchemy_settle(p, st, entering=False)
    await store.set_tower_state(str(qq), st)
    tier = core.tower_tier(st.get("card_tier", 0))
    lv = int(p.get("lv", 1))
    cap = int(cfg.TOWER_POISON_CAP)
    poison = int(st.get("fire_poison", 0))
    magma = bool(st.get("magma"))
    lines = [_tower_head(st), ""]
    lines.append(f"　火能 {int(st.get('fire_energy', 0))}"
                 f"（1 点 = {int(cfg.TOWER_MINUTES_PER_ENERGY)} 分钟塔修）")
    lines.append(f"　火毒 {poison}/{cap}（每分钟 +{core.tower_poison_rate(tier, lv, magma):.1f}"
                 f"；离塔每分钟 -{float(cfg.TOWER_POISON_COOL_PER_MIN):.1f}）")
    if poison >= cap:
        lines.append("　⚠️ 火毒已满，经脉如焚 —— 必须先离塔冷却，硬练会废掉根基。")
    lines.append("　状态：" + ("塔中修炼中" if int(st.get("in_tower") or 0) else "塔外"))
    lines.append(f"　今日免费火能：{'已领 +%d' % got if got else '已领过'}"
                 f"　｜　累计入塔 {int(st.get('enters', 0))} 次")
    nxt = tier + 1
    if nxt < len(cfg.FACTION_CARD_NAMES):
        lines.append(f"　换卡：{cfg.FACTION_CARD_NAMES[tier]} → "
                     f"{cfg.FACTION_CARD_NAMES[nxt]} 需 "
                     f"{int(cfg.FACTION_CARD_UPGRADE_COST[tier])} 火能（发「换卡」）")
    else:
        lines.append("　已持紫火晶卡，再无可换。")
    if core.tower_magma_open(p, tier):
        lines.append("　🌋 塔底岩浆世界已开启：心火 ×%.1f，火毒也 ×%.1f（自动生效）"
                     % (float(cfg.TOWER_MAGMA_MULT), float(cfg.TOWER_MAGMA_POISON_MULT)))
    elif tier >= len(cfg.FACTION_CARD_MULT) - 1:
        lines.append(f"　🌋 岩浆世界需斗灵（lv{int(cfg.TOWER_DOU_LING_LV)}）才扛得住。")
    else:
        lines.append("　🌋 塔底岩浆世界：紫火晶卡 + 斗灵方可入。")
    return "\n".join(lines)


async def cmd_furnace_enter(store, qq) -> str:
    p = await _player(store, qq)
    if not p:
        return NOT_ENTERED
    st = await store.tower_state(str(qq))
    got = core.tower_daily_energy(st, date.today().isoformat())
    was_in = bool(int(st.get("in_tower") or 0))
    res = core.alchemy_settle(p, st, entering=True)
    await store.set_tower_state(str(qq), st)
    await store.put_player(p)
    head = _tower_head(st)
    daily = f"（今日免费火能 +{got}）" if got else ""
    if res["mode"] == "enter":
        cool = f"，火毒消解 -{res['cooled']}" if res.get("cooled") else ""
        return (f"{head}\n🚪 你踏入塔中，心火扑面{cool}{daily}。\n"
                f"　火能 {res['energy']}　火毒 {res['poison']}/{int(cfg.TOWER_POISON_CAP)}\n"
                f"　再发一次「入塔」即可结算这段时间的修炼；发「出塔」离开。")
    if res["mode"] == "wait":
        return (f"{head}\n🔥 你正在塔中，心火淬体。\n"
                f"　火能 {res['energy']}　火毒 {res['poison']}/{int(cfg.TOWER_POISON_CAP)}"
                f"{daily}\n　（不足 1 分钟，稍后再发「入塔」）")
    if res["mode"] == "no_energy":
        return (f"{head}\n⚠️ 火能耗尽，心火渐弱 —— 你被请出塔外。\n"
                f"　火能 0（战斗胜利可夺取火能，每日也有免费额度）"
                f"{daily}\n　火毒 {res['poison']}/{int(cfg.TOWER_POISON_CAP)}")
    # settle / capped
    lines = [head,
             f"🧘 塔中修炼 {res['minutes']} 分钟，修为 +{res['gain']}"
             f"（心火 ×{res['mult']:.1f}）" + (f"，消耗火能 {res['energy_spent']}" if res["energy_spent"] else ""),
             f"　火能 {res['energy']}　火毒 {res['poison']}/{int(cfg.TOWER_POISON_CAP)}"
             f"（本次 +{res['poison_add']}）"]
    if res.get("leftover_min"):
        lines.append(f"　火能不足，剩余 {res['leftover_min']} 分钟未能化开心火。")
    if res["capped"]:
        lines.append("　⚠️ 火毒触顶，你被强行送出塔外 —— 必须冷却后才能再入"
                     f"（每分钟 -{float(cfg.TOWER_POISON_COOL_PER_MIN):.1f}）。")
    lv = int(p.get("lv", 1))
    if core.can_level(p):
        lines.append("修为已满，可发「突破」晋升。")
    return "\n".join(lines)


async def cmd_furnace_exit(store, qq) -> str:
    p = await _player(store, qq)
    if not p:
        return NOT_ENTERED
    st = await store.tower_state(str(qq))
    if not int(st.get("in_tower") or 0):
        # 已在塔外：冷却按时间自然进行，这里只做一次如实结算并展示
        res = core.alchemy_settle(p, st, entering=False)
        await store.set_tower_state(str(qq), st)
        await store.put_player(p)
        cool = f"，火毒消解 -{res['cooled']}" if res.get("cooled") else ""
        return (f"{_tower_head(st)}\n你已在塔外{cool}。\n"
                f"　火毒 {res['poison']}/{int(cfg.TOWER_POISON_CAP)}"
                f"　火能 {res['energy']}")
    res = core.alchemy_settle(p, st, entering=False)
    await store.set_tower_state(str(qq), st)
    await store.put_player(p)
    lines = [_tower_head(st),
             f"🚪 你退出天焚炼气塔（本次 {res['minutes']} 分钟，修为 +{res['gain']}）",
             f"　火能 {res['energy']}　火毒 {res['poison']}/{int(cfg.TOWER_POISON_CAP)}"
             f"（本次修炼 +{res['poison_add']}）",
             f"　离开后每分钟火毒 -{float(cfg.TOWER_POISON_COOL_PER_MIN):.1f}，"
             f"冷却完再来（火毒未散，须歇一夜）。"]
    return "\n".join(lines)


async def cmd_furnace_upgrade(store, qq) -> str:
    p = await _player(store, qq)
    if not p:
        return NOT_ENTERED
    st = await store.tower_state(str(qq))
    res = core.tower_upgrade_card(st)
    if not res["ok"]:
        if res.get("reason") == "max":
            return (f"🔥 你已持有最高档【{res['name']}】，"
                    f"塔中再无可换（发「火能」看状态）。")
        return (f"🔥 换卡需 {res['need']} 火能，你只有 {res['have']}。\n"
                f"　{res['name']} → {res['next']}（战斗胜利可夺火能，每日也有免费额度）")
    await store.set_tower_state(str(qq), st)
    tier = int(res["tier"])
    return (f"🔥 换卡成功：【{res['name']}】→【{res['next']}】"
            f"（-{res['cost']} 火能，余 {res['energy']}）\n"
            f"　可入第 1~{core.tower_max_floor(tier)} 层，心火 ×"
            f"{core.tower_mult(tier, bool(core.tower_magma_open(p, tier))):.1f}")


async def cmd_use(store, qq, name) -> str:
    p = await _player(store, qq)
    if not p:
        return NOT_ENTERED
    name = (name or "").strip()
    if not name:
        return ("用法：服丹 回灵丹\n"
                "　带纹丹：服丹 回灵丹·二纹　或　服丹 回灵丹 二纹（一/二/三纹）")
    # 2026-11-25：背包显示名剥掉了纹数（用户要求名称不变、纹数走角标），
    # 玩家照卡面打「聚气散」只能吃到不带纹那枚。这里先按「背包里真有的」解析。
    owned, auto_danwen = resolve_owned_item(p, name)
    # 2026-11-18：丹药 + 天材地宝统一走「服丹/炼化」入口，材料也有实际作用。
    # 先精确匹配（防「复灵紫丹」被「复紫灵丹」的子串抢先命中），再宽松匹配。
    # 2026-11-23：别名（复紫灵丹）也要能被点到，否则老存档里的丹成了无主物品。
    pool = (list(cfg.RECIPE_NAMES) + list(cfg.RECIPE_ALIAS)
            + list(cfg.MATERIAL_NAMES) + list(cfg.DANWEN_RECIPE_NAMES))
    if owned:
        hit = owned
    else:
        hit = name if name in pool else next(
            (n for n in pool if name in n or n in name), None)
    if not hit:
        return "无此丹药。"
    # 「一阶限 N 枚」只对破境类丹药生效（原著抗性），普通丹药读库没意义。
    rec = cfg.RECIPE_BY_NAME.get(hit) or {}
    used = None
    if rec.get("effect") == "breakthrough":
        used = await store.pill_used_counts(str(qq), hit)
    res = core.use_item(p, hit, used_counts=used)
    if res.get("mark_used") is not None:
        await store.mark_pill_used(str(qq), hit, int(res["mark_used"]))
    await store.put_player(p)
    if not res.get("ok"):
        if res.get("reason") == "none":
            return f"你没有【{hit}】。去「炼制 {hit}」或「宝库」看看。"
        if res.get("reason") == "resisted":
            # 丹药已被扣掉（原著就是「白白浪费」），必须让玩家看见这件事。
            return f"💊 服下【{hit}】，{res.get('msg')}"
        if res.get("reason") == "full":
            # 2026-11-23：满血/满气时**不扣物**（core 拦在扣物之前），要让玩家看见
            # 「丹还在」，而不是收到一句「无法服用」以为丢了一枚。
            return (f"💚 气血斗气都是满的 —— 【{hit}】还在包里，"
                    f"受了伤再服才不浪费。")
        if res.get("reason") == "need_puppet":
            # P3 养魂：养魂涎不是口服丹。**一枚都没扣**（拦在扣物之前），
            # 这里必须把「丹还在」和「该去哪用」都说清楚。
            return ("🧿 【%s】要渡进傀儡体内，不能口服 —— 丹还在你包里。\n"
                    "　发「傀儡 养魂」把它渡给出战傀儡：每枚 +%d 灵魂强度，"
                    "上限 %d。" % (hit, int(cfg.PUPPET_SOUL_FEED_GAIN),
                                   int(cfg.PUPPET_SOUL_FEED_MAX)))
        if res.get("reason") == "notuse":
            # 2026-11-23 修 P0 吞丹：焚血散是升斗技的材料，**一枚都没扣**（拦在
            # 扣物之前）。旧版落到下面那句只回 msg，玩家不知道丹还在，还会以为被骗。
            return ("🧪 【%s】不是口服之物 —— 丹还在你包里。\n"
                    "　发「升级 技能名」把它用在斗技上（每级 1 份，最高 5 级）。" % hit)
        return res.get("msg") or "无法服用。"
    note = ""
    if auto_danwen:
        note = "\n　（包里没有不带纹的，这枚带纹丹直接服下了）"
    if hit in cfg.MATERIAL_BY_NAME:
        return f"✨ {res['msg']}{note}"
    return f"💊 服下【{hit}】，{res['msg']}{note}"


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
        # 2026-11-20：进度必须走 core.main_value —— 武器类是「状态要求」，
        # 玩家在任务轮到之前就练满时事件计数恒 0（用户反馈的真 bug）。
        cur = core.main_value(p)
        out.append(f"【主线·{m['name']}】{m['desc']}")
        out.append(f"　进度 {cur}/{m['target']}　{_bar(cur, m['target'])}")
        rw = m["reward"]
        out.append(f"　奖励：{rw['gold']} 金 / {rw['exp']} 修为 / 机缘 +{rw['luck']}")
    else:
        out.append("【主线】已全部完成。")
    t = p.get("task")
    if t:
        cur = core.task_value(p)
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
        return f"你手上还有支线未交：{t['name']}（{core.task_value(p)}/{t['target']}）"
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
        return f"尚未完成：{t['name']}（{core.task_value(p)}/{t['target']}）"
    rw = res["reward"]
    # 2026-11-19：提交必须存档（此前只在内存改 p，重启丢奖励）；A 线惊喜在此揭晓。
    await store.put_player(p)
    lines = [f"✅ 支线【{res['task']['name']}】完成！",
             f"　金币 +{rw['gold']}　修为 +{rw['exp']}　机缘 +{rw['luck']}"]
    surprise = res.get("surprise_flame") or ""
    if surprise:
        lines.append(f"　✨ 更意外的是——你在任务中引出一缕奇火，竟是【{surprise}】！")
        lines.append("　（已自动收服，发送「异火」查看）")
    return "\n".join(lines)


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
    # 2026-11-20：主线提交必须落库（此前只在内存改 p —— 支线在 2026-11-19 修过同一个错，
    # 主线漏了：交完主线不重启还好，一重启章节回退、奖励蒸发）。
    await store.put_player(p)
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
    # 傀儡（2026-11-20 第二轮）：双方对称带出战傀儡——谁炼了谁受益，不是单向碾压。
    _pu, _pu_n, _pu_fm = await _puppet_ctx(qq, p)
    _vu, _vu_n, _vu_fm = await _puppet_ctx(str(target), vic)
    res = core.duel(p, vic, puppet=_pu, formation_n=_pu_n,
                    puppet_flame_mult=_pu_fm,
                    vic_puppet=_vu, vic_formation_n=_vu_n,
                    vic_flame_mult=_vu_fm)
    await _finalize_battle(
        p, qq, res, "掠夺",
        opponent=str(vic.get("name") or f"道友{str(target)[-4:]}"),
        puppet=_pu,
    )
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
        lines.append(txt.rob_win(_fmt(amt)))
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
    # 傀儡（2026-11-20 第二轮）：双方对称带出战傀儡。
    _pu, _pu_n, _pu_fm = await _puppet_ctx(ch.challenger, rival)
    _vu, _vu_n, _vu_fm = await _puppet_ctx(qq, p)
    res = core.duel(rival, p, puppet=_pu, formation_n=_pu_n,
                    puppet_flame_mult=_pu_fm,
                    vic_puppet=_vu, vic_formation_n=_vu_n,
                    vic_flame_mult=_vu_fm)
    a_name = str(rival.get("name") or f"道友{ch.challenger[-4:]}")
    b_name = str(p.get("name") or f"道友{str(qq)[-4:]}")
    # 无惩罚切磋：双方都留一份回看；发起方是 log 里的 who=p，应战方是 who=v。
    await _finalize_battle(rival, ch.challenger, res, "切磋", opponent=b_name)
    await _finalize_battle(p, qq, res, "切磋", opponent=a_name)
    seq = res["log"]
    show = seq[:4] + (["..."] if len(seq) > 6 else []) + (seq[-2:] if len(seq) > 6 else [])
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
    return txt.give_ok(amount=str(amt))


# ---------------------------------------------------------------------------
# 赠送背包物品（2026-11-20 用户「加一个新功能赠送，可以赠送其他玩家任意背包物品」）
# ---------------------------------------------------------------------------
def parse_gift(text: str):
    """把「送」后面的正文解析成 ``(物品名, 数量)``；物品名为空 = 金币模式。

    ``送 100``        → ("", 100)      金币（旧路径，必须继续能用）
    ``送 回灵丹 5``    → ("回灵丹", 5)
    ``送 回灵丹×5``    → ("回灵丹", 5)
    ``送 回灵丹``      → ("回灵丹", 1)

    **纯函数**：不碰 store、不读事件，专门为了能在测试里逐条钉死解析口径。
    只认 ASCII 数字 —— 物品名里的「七阶魔核」「三纹青灵丹」是中文数字，撞不上。
    """
    s = re.sub(r"^\s*(?:赠送给|赠送|送给|送)", "", str(text or "")).strip()
    s = s.strip("，,、。 ")
    if not s:
        return "", 0
    if s.isdigit():
        return "", int(s)
    m = re.search(r"(?:[×xX*]\s*|\s)(\d+)\s*$", s)
    if m:
        name = s[:m.start()].strip(" ，,、×xX*")
        if name:
            return name, int(m.group(1))
    # 「送回灵丹5」这种不带分隔符的写法也要认（物品名里没有 ASCII 数字，不会误切）
    m = re.search(r"(\d+)\s*$", s)
    if m:
        name = s[:m.start()].strip(" ，,、×xX*")
        if name:
            return name, int(m.group(1))
    return s, 1


def _gift_item_name(p, name: str) -> str:
    """把玩家输入对到**自己包裹里真实有的**物品名（先精确，再包含）。

    与 ``_match_item_name`` 的区别：候选池取包裹本身而不是宝库目录，
    所以「药渣」这类不在宝库清单里的物品也能送；同时天然挡掉「输入一个
    你根本没有的东西」。
    """
    name = (name or "").strip()
    if not name:
        return ""
    pool = [k for k, v in core.bag(p).items() if int(v) > 0]
    if name in pool:
        return name
    hits = [x for x in pool if name in x or x in name]
    return sorted(hits, key=len)[0] if hits else ""


def _gift_material_name(mats: dict, name: str) -> str:
    """把玩家输入对到**自己傀儡材料里真实有的**那种（先精确，再包含）。

    与 ``_gift_item_name`` 同策略，候选池换成 ``puppet_materials`` 的键。
    两个池子零重名（17 种傀儡材料 vs 153 种背包物品，2026-11-20 实测），
    所以「先背包后材料」只是固定顺序，不会二义。
    """
    name = (name or "").strip()
    if not name:
        return ""
    pool = [k for k, v in (mats or {}).items() if int(v) > 0]
    if name in pool:
        return name
    hits = [x for x in pool if name in x or x in name]
    return sorted(hits, key=len)[0] if hits else ""


async def cmd_gift(store, qq, target, item, n=1, pstore=None) -> str:
    """赠送一件背包物品 / 一种傀儡材料。金币仍走 :func:`cmd_give`。

    两条路各自在自己的库里**一个事务写完**：
      * 背包物品 → ``Store.transfer_item``（`doupo.sqlite`）；
      * 傀儡材料 → ``PuppetStore.transfer_material``（`puppet_data.sqlite`）。
    一次赠送只会命中其中一条，不存在跨库半成功。

    「一处输出=一条消息」：成功/失败都只回一条纯文本，不另发卡片。
    """
    pstore = pstore or DEFAULT_PUPPET_STORE
    p = await _player(store, qq)
    if not p:
        return NOT_ENTERED
    if not target:
        return ("用法：送 回灵丹 5 @某人（物品/傀儡材料）　｜　送 100 @某人（金币）")
    if str(target) == str(qq):
        return "不能赠送给自己。"
    if int(n) <= 0:
        return "数量无效。"
    hit = _gift_item_name(p, item)
    mat = ""
    mats: dict = {}
    if not hit:
        # 背包里没有才查傀儡材料：少一次库读，且顺序固定
        mats = await pstore.materials(str(qq))
        mat = _gift_material_name(mats, item)
    if not hit and not mat:
        return ("包裹与傀儡材料里都没有【%s】。发「背包」「傀儡 材料」看现有物品；"
                "送金币用「送 100 @某人」。" % str(item or "").strip())
    if hit:
        have = core.count_item(p, hit)
        if have < int(n):
            return "【%s】只有 %d 个，不够送 %d 个。" % (hit, have, int(n))
    else:
        have = int(mats.get(mat, 0))
        if have < int(n):
            return "【%s】只有 %d 个，不够送 %d 个。" % (mat, have, int(n))
    if await store.get_player(str(target)) is None:
        return "对方尚未入道。"
    if hit:
        ok = await store.transfer_item(str(qq), str(target), hit, int(n))
        kind = ""
        left = core.count_item(p, hit)
    else:
        ok = await pstore.transfer_material(str(qq), str(target), mat, int(n))
        kind = "（傀儡材料）"
        left = int((await pstore.materials(str(qq))).get(mat, 0))
    if not ok:
        # 走到这里说明 get 与 transfer 之间状态变了（对方掉档 / 物品被别处扣掉）
        return "赠送失败：对方存档或你的%s已变动，请重试。" % ("包裹" if hit else "材料")
    return (txt.give_ok(item="%s×%d%s" % (hit or mat, int(n), kind))
            + "\n　余 %d 个%s" % (left, "" if left else "（已送空）"))


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
    _loot = res.get("loot") or {}
    _loot_txt = ("、".join(f"{nm}×{qty}" for nm, qty in _loot.items())) if _loot else ""
    return ("⏰ 上一轮闲置超时（%d 小时），已自动结算："
            "+%d 金币、+%d 修为，止步第 %d 层%s%s"
            % (ts.DEFAULT_TTL // 3600, res["gold"], res["exp"], res["floors"],
               ("　🎁 " + _loot_txt) if _loot_txt else "",
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
        # 2026-11-18：回春散/回气丹是「立刻」类，已把恢复写进永久存档 p；
        # 必须在返回前落盘，否则人物卡读到的还是旧值（用户实测「斗气没回满」）。
        await store.put_player(p)
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
    # 出战傀儡（2026-11-20 第二轮）：爬塔也带。怪血锚标尺（同层同一只怪），
    # 傀儡击锚真人 —— 详见 core.tower_floor 的 docstring。
    _pu, _pu_n, _pu_fm = await _puppet_ctx(qq, p)
    r = core.tower_floor(p, st, rng,
                         temp_bonus=_temp_bonus(st), temp_effects=_temp_effects(st),
                         puppet=_pu, formation_n=_pu_n,
                         puppet_flame_mult=_pu_fm)
    await _finalize_battle(
        p, qq, r, "爬塔", opponent=f"第{r['floor']}层 {r['name']}",
        extra={"floor": int(r["floor"]), "kind_tag": str(r.get("kind") or "")},
        puppet=_pu,
    )
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
        _loot = res.get("loot") or {}
        _loot_txt = ("\n　🎁 掉落：" + "、".join(
            f"{nm}×{qty}" for nm, qty in _loot.items())) if _loot else ""
        lines = [
            f"{header}🗼 第 {r['floor']} 层【{kind_tag}】{r['name']} {aff}",
            txt.tower_down(res["floors"]),
            f"　结算：+{res['gold']} 金币、+{res['exp']} 修为" + _loot_txt
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
    """应用爬塔三选一战利品。

    2026-11-18 修复「增益虚加」：
      * 「立刻」类（回春散 hp / 回气丹 mp）——文案承诺即时生效，必须写回
        永久存档 p（人物卡/面板读的就是 p），并同步会话 temp_* 让下一层承接。
        调用方 cmd_tower 选完必须 put_player 落盘。塔战斗本身仍只在深副本打，
        不污染 p 的 hp/mp/wins——这里改的是玩家主动服下的战利品，不是战斗损耗。
      * 「本轮」类（4 属性 + 4 效果）——只写会话临时态，由下一层 tower_floor
        经 temp_bonus / tower_mod 真实注入战斗，本轮结束清空，不写永久存档。

    任何新增 loot kind 都必须落进下面某个分支并写清「即时 or 本轮」，否则就是
    一个新的虚加。tests/test_tower_loot_effective.py 用穷举注入守这条。
    """
    kind, key, v = loot["kind"], loot["key"], float(loot.get("v", 0))
    if kind == "hp":
        mx = core.stats(p)["体力"]
        # 基准取会话当前血（塔内已损耗的血量），不受 p['hp'] 陈旧值影响。
        cur = int(st.get("temp_hp") if st.get("temp_hp") is not None else p.get("hp", mx))
        new_hp = min(mx, cur + int(mx * v))
        p["hp"] = new_hp
        st["temp_hp"] = new_hp
    elif kind == "mp":
        new_mp = core.mp_max(p)
        p["mp"] = new_mp
        st["temp_mp"] = new_mp
    elif kind == "attr":
        tb = dict(st.get("temp_bonus", {}))
        mx = core.stats(p).get(key, 100)
        tb[key] = int(tb.get(key, 0)) + int(mx * v)
        st["temp_bonus"] = tb
    elif kind == "effect":
        items = list(st.get("temp_effect_items", []))
        items.append(loot)
        st["temp_effect_items"] = items
    else:
        raise ValueError("未知爬塔战利品类型 %r（%s）——拒绝静默虚加" % (kind, key))


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
    loot = res.get("loot") or {}
    loot_txt = ("\n　🎁 掉落：" + "、".join(f"{nm}×{qty}" for nm, qty in loot.items())) if loot else ""
    return (f"🗼 收手结算：通过 {res['floors']} 层，+{res['gold']} 金币、+{res['exp']} 修为"
            + loot_txt
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
    # 出战傀儡（2026-11-20 第二轮）：周本也带。怪血锚当周快照（难度锁定），
    # 傀儡击锚真人 —— 见 core.weekly_floor 的 docstring。
    _pu, _pu_n, _pu_fm = await _puppet_ctx(qq, p)
    r = core.weekly_floor(p, snap, st, rng,
                          puppet=_pu, formation_n=_pu_n,
                          puppet_flame_mult=_pu_fm)
    await _finalize_battle(
        p, qq, r, "周副本", opponent=f"第{r['floor']}/7层 {r['name']}",
        extra={"floor": int(r["floor"]), "week": week, "kind_tag": str(r.get("kind") or "")},
        puppet=_pu,
    )

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
        lines.append("　" + txt.weekly_clear() + f"+{res['gold']} 金币、+{res['exp']} 修为"
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


# ---------------------------------------------------------------------------
# 傀儡系统（2026-11-18，考据见 docs/斗破傀儡_原文证据台账_2026-11-18.md）
# 所有傀儡数据在独立 SQLite 真实列（puppet_store），绝不进 players.data 的 JSON blob。
# ---------------------------------------------------------------------------
# 子命令 = 「动作/清单回执」，回复必须是文本；不带子命令的「傀儡」才是出图口令。
# ⚠️ 这张表是「出声 vs 出图」的唯一真值：命令层分派与 ``__init__`` 的 handler 分流
# 都读它。此前 handler 无条件出傀儡卡，把淬炼/结阵/收服/融魂/配方的回执整条丢掉
# ——玩家花了雷淬石和金币、只收到一张没变化的名录卡（2026-11-20 第十一轮，用户报
# 「傀儡淬炼口令没触发」，真相是命令触发了、也真扣了料，只是回执被卡吞了）。
PUPPET_SUBCMDS = ("配方", "谱", "淬炼", "结阵", "收服", "融魂", "换躯", "养魂")


def puppet_is_subcmd(arg) -> bool:
    """该参数走子命令（文本回执）还是「看傀儡卡」（出图）。真值见上。"""
    a = str(arg or "").strip()
    return bool(a) and a.startswith(PUPPET_SUBCMDS)


async def cmd_puppet(store, qq, arg="", pstore=None) -> str:
    """查看傀儡卡 / 子命令：淬炼、结阵、融魂、收服。
    不拦未开通者——展示差哪道资格，作引导。
    """
    from . import puppet_core as pc
    pstore = pstore or DEFAULT_PUPPET_STORE
    p = await _player(store, qq)
    if not p:
        return NOT_ENTERED
    qq = str(qq)
    arg = (arg or "").strip()
    elig = await pstore.eligibility(qq)
    roster = await pstore.list_puppets(qq)
    # 「傀儡 配方」随时可看（不必先有傀儡）——多配方后玩家第一句总是「要什么料」
    if arg.startswith("配方") or arg.startswith("谱"):
        return _puppet_recipe_text(p, await pstore.materials(qq) or {})
    if not roster:
        lines = ["【傀儡】你尚未拥有傀儡。", "炼一具须先过五道资格（挂已有养成）："]
        checks = [
            ("境界", pc.gate_realm(p, 0)), ("灵魂境界", pc.gate_soul(p)),
            ("控火", pc.gate_fire_ctrl(p)), ("异火", pc.gate_flame(p)),
            ("斗气", pc.mp_endurance_ok(p, 0)),
        ]
        for name, (ok, reason) in checks:
            lines.append("  %s %s%s" % ("✅" if ok else "❌", name,
                                        "" if ok else "（" + reason + "）"))
        # 2026-11-20：材料与竹简一次列全（玩家问「到底要什么」时不用猜）
        mats = await pstore.materials(qq) or {}
        rec0 = cfg.PUPPET_RECIPE_BY_NAME.get("铜尸傀") or cfg.PUPPET_RECIPES[0]
        need = pc.recipe_materials(rec0)
        lines.append("  📦 开炉材料（%s）：%s" % (rec0["name"], "、".join(
            "%s %d/%d" % (n, int(mats.get(n, 0) or 0), q) for n, q in need.items())))
        lines.append("  📜 傀儡之技：%s" % ("已习得" if elig["lore"] else "未习得（须先得「天妖傀竹简」）"))
        lines.append("资格齐后发「炼制傀儡」。发「傀儡 配方」看全部 %d 条配方。"
                     % len(cfg.PUPPET_RECIPES))
        return "\n".join(lines)
    if arg.startswith("淬炼"):
        # P1-2 批量雷淬（Q1）：「傀儡 淬炼 5」一次结算 5 次，消除重复点击疲劳。
        _cnt = arg[len("淬炼"):].strip()
        _times = int(_cnt) if _cnt.isdigit() else 1
        return await _cmd_puppet_temper(store, qq, p, roster, elig, pstore,
                                        times=max(1, min(50, _times)))
    if arg.startswith("结阵"):
        return await _cmd_puppet_formation(qq, roster, elig, pstore)
    if arg.startswith("收服"):
        return await _cmd_puppet_claim(store, qq, p, arg, pstore)
    if arg.startswith("融魂"):
        return await _cmd_puppet_awaken(store, qq, p, roster, elig, pstore, arg)
    if arg.startswith("换躯"):
        return await _cmd_puppet_reforge(store, qq, p, roster, elig, pstore, arg)
    if arg.startswith("养魂"):
        return await _cmd_puppet_soul_feed(store, qq, p, roster, elig, pstore, arg)
    # 默认：展示当前傀儡
    cur = next((x for x in roster if x["id"] == elig["active_id"]), roster[0])
    tier_name, skin = cfg.PUPPET_TIER_BY_IDX.get(cur["tier"], ("人级傀儡", "铜"))
    # atk 必须传 p：不传 p 会走躯体境界标尺，卡面数字与实战（fight 传真实 p）
    # 就是两张皮（2026-11-20 第三轮口径：min(躯体境界标尺, 玩家输出 × CAP)）。
    atk = pc.puppet_attack(cur, p)
    # 护主面板与 fight() 同源（同一个 guard_profile），结阵具数也走同一套封顶。
    g = pc.guard_profile(cur, p, pc.formation_count(roster))
    core_name = pc.core_name_of(cur)
    core_txt = ("%s（%d阶%s系）" % (core_name, cur["core_tier"], cur["core_attr"] or "无")
                if core_name else "%d阶%s系" % (cur["core_tier"], cur["core_attr"] or "无"))
    rec = pc.match_recipe(cur)
    fire_word = {1: "兽火", 2: "异火", 3: "高阶异火"}.get(
        pc.fire_grade_of_tier(cur["fire_tier"]), "兽火")
    si = pc.soul_feed_info(cur)
    lines = [
        "【%s】%s档 · %s皮" % (cur["name"], tier_name, skin),
        "　躯体：%s（%s%s）" % (cur["body_name"] or "—",
                              cur["body_realm"] or "凡躯",
                              ("·%d星" % cur["body_star"]) if cur["body_star"] else ""),
        "　魔核：%s　灵魂：%s" % (core_txt, cur["soul_source"] or "—"),
        "　金属：%s　炼制之火：%s" % (cur["metal_name"] or "—", fire_word),
        "　锻法：%s" % (("%s（%s）" % (rec["name"], rec["note"])) if rec else "自定组合"),
        # 战力对标（2026-11-20 第三轮）：数字旁边必须给出「这是几品战力」，
        # 否则「肉身攻击 28357」对玩家只是一个无量纲的数。标签走实际发挥，
        # 被玩家境界压制时会自动降档，不会出现「写着斗宗级、打出来一千七」。
        # ⚠️ label 已自带「级别 · 受你境界所限」，别再用括号包它 → 嵌套括号。
        "　肉身攻击 %d　%s（不吃斗气/斗技）" % (
            int(atk), pc.puppet_realm_label(cur, p)),
        # 护主三件事必须一起给出（2026-11-20 第三轮）：气血/护甲是能力，
        # 挡伤概率是机制，大额线是「什么时候会触发」——缺一个就是隐式数值。
        "　护主：气血 %d　护甲 %d（挡下伤害时减伤 %.0f%%）" % (
            g["hp_max"], g["armor"],
            (1.0 - core.mitigation(g["armor"])) * 100),
        "　替主挡伤 %.0f%% 概率（单次伤害 ≥ %d 才算大额；挡下后你仍自担 %.0f%%）" % (
            g["chance"] * 100, g["threshold"], g["leak"] * 100),
        "　雷淬纯度 %d%% %s%s%s" % (
            cur["purity"], pc.dye_bar(int(cur["purity"])),
            (" 紫纹%d层" % g["vein"]) if g.get("vein") else "",
            "　⚠受损，待雷淬修复" if cur["damaged"] else ""),
        # P3 养魂：灵魂强度是**可养**的（第900章养魂涎），卡面必须给出进度，
        # 否则玩家喂了几枚看不出变化，会以为丹被吞了。
        "　养魂 %d/%d%s　有效灵魂 %d（基准 %d）" % (
            si["fed"], si["cap"], "（已满）" if si["maxed"] else "",
            si["total"], pc.soul_base(cur)),
        "　%s%s" % ("已认主·永不背叛" if cur["bonded"] else "未认主",
                    "　有灵（北王式）" if cur["awakened"] else ""),
    ]
    if len(roster) > 1:
        lines.append("　持有 %d 具；发「傀儡 结阵」查看合击" % len(roster))
    lines.append("　子命令：配方 / 淬炼 / 结阵 / 收服 / 融魂 / 换躯 / 养魂")
    return "\n".join(lines)


def _puppet_recipe_text(p, mats: dict) -> str:
    """「傀儡 配方」：18 条配方逐条列五维用料 + 玩家当前能不能凑齐。

    分组用**配方标称档**（火按配方写死的字母），不是玩家实况档——否则只有 A 火的
    玩家看到「金身傀」被归到地级，会以为配方写错了。实况档差在炼制时如实提示。
    """
    from . import puppet_core as pc
    ready = {x["recipe"]["name"]: x for x in pc.craftable_recipes(p, mats)}
    fg = pc.fire_grade(p)
    fire_word = {1: "兽火", 2: "异火", 3: "高阶异火"}.get(int(fg["grade"]), "兽火")
    lines = ["【傀儡配方】共 %d 条（档位＝五维里最弱那一环，天为金·地为银·人为铜）"
             % len(cfg.PUPPET_RECIPES),
             "　你如今的炼制之火：%s（%s）" % (fire_word, fg["name"])]
    last_tier = None
    for r in cfg.PUPPET_RECIPES:
        item = ready.get(r["name"]) or {}
        tier = int(item.get("tier_nominal", pc.recipe_tier(pc.recipe_slots(r))))
        if tier != last_tier:
            lines.append("— %s —" % (cfg.PUPPET_TIER_BY_IDX[max(0, tier)][0]
                                     if tier >= 0 else "五维不全"))
            last_tier = tier
        mark = "✅" if item.get("ok") else "⬜"
        lines.append("%s %s：%s ＋ %s ＋ %s ＋ %s ＋ %s火" % (
            mark, r["name"], r["body"], r["core"], r["soul"], r["metal"], r["fire"]))
        if item.get("lack"):
            lines.append("　　缺 %s" % "、".join(item["lack"]))
    lines.append("发「炼制傀儡 <配方名>」开炉；留空自动选能凑齐的最高档。")
    return "\n".join(lines)


async def cmd_craft_puppet(store, qq, pstore=None, arg="", report=None) -> str:
    """炼制傀儡：选配方 → 过五道资格 → 扣真实列材料 → 建真实列傀儡行。

    2026-11-20 多配方：``arg`` 是配方名（「炼制傀儡 地妖傀」）；留空则自动选
    「材料齐 + 境界够」的最高档。档位由五维材料木桶判定（原著第900/906章），
    不再是「首炼恒人级铜」——材料好就该出好傀。

    ``report``（2026-11-20 第十一轮）：成傀时把结构化结果写进去，供 handler 出
    「炼制成功卡」。**只有成功才写**——失败/引导类回复是逐条解释缺什么，天然是
    文本，不能塞进卡片（一处输出=一条消息，成卡与失败文本互斥）。
    """
    from . import puppet_core as pc
    pstore = pstore or DEFAULT_PUPPET_STORE
    p = await _player(store, qq)
    if not p:
        return NOT_ENTERED
    qq = str(qq)
    mats = await pstore.materials(qq) or {}
    # 传承从独立资格表读（不进 p / 不进 JSON）
    elig = await pstore.eligibility(qq)
    p_view = dict(p)
    p_view["puppet_lore"] = elig["lore"]
    arg = str(arg or "").strip()
    if arg in ("配方", "配方表", "列表", "谱"):
        return _puppet_recipe_text(p, mats)

    # ---- 0. 资格预检 / 传承（必须在「选配方」之前）----
    # 旧版先查材料：新玩家材料必然是空的，于是「境界不足」「没竹简」全被盖成
    # 「材料不足」——玩家永远看不到真正卡住他的那一道。门禁先于耗材。
    pre = pc.puppet_gate(p_view, 0, need_mp=False)
    if not pre["ok"]:
        return "❌ 未能开炉：" + pre["reason"]
    if not (p_view.get("puppet_lore") or p_view.get("lore")):
        return ("❌ 尚未习得傀儡之技（无竹简，E01）——去「傀儡遗迹」第三段"
                "「虚空雷池」打赢守者取《天妖傀》竹简。")

    # ---- 1. 定配方 ----
    # 自定材料（2026-11-20 第十轮）：手里料多时不必被固定配方绑住。
    # 只在这里造一条**临时配方**，后续判档/五闸/扣料/落库全部复用原路径 ——
    # 重开一条自定分支是最容易把「木桶判档」和「门禁先于耗材」写漏的做法。
    if arg.startswith("自定"):
        rec, custom_err = pc.custom_recipe_from_arg(arg, p_view)
        if rec is None:
            return custom_err
        lack = pc.missing_materials(mats, rec)
    elif arg:
        rec = pc.recipe_by_name(arg)
        if not rec:
            return ("无此配方「%s」。现有 %d 条：%s\n　发「傀儡 配方」看每条的用料与缺什么。"
                    % (arg, len(cfg.PUPPET_RECIPES),
                       "、".join(r["name"] for r in cfg.PUPPET_RECIPES)))
        lack = pc.missing_materials(mats, rec)
    else:
        item = pc.best_recipe(p_view, mats)
        if item is None:
            near = [x for x in pc.craftable_recipes(p_view, mats) if int(x["tier"]) >= 0][:2]
            tip = "".join("\n　%s：缺 %s" % (x["recipe"]["name"], "、".join(x["lack"]))
                          for x in near)
            return ("❌ 材料不足：凑不齐任何一条配方。" + tip
                    + "\n　发「傀儡 配方」看全部用料，发「傀儡遗迹」去打料。")
        rec = item["recipe"]
        lack = []
    if lack:
        rows = []
        for name, q in pc.recipe_materials(rec).items():
            have = int(mats.get(name, 0))
            if have < int(q):
                rows.append("　缺 %s×%d（有 %d）—— %s" % (
                    name, int(q) - have, have,
                    cfg.PUPPET_MATERIAL_SRC.get(name, "野外掉落")))
        return ("❌ 材料不足（%s 需 %s）：\n%s" % (
            rec["name"], "、".join(sorted(pc.recipe_materials(rec))), "\n".join(rows)))

    # ---- 2. 判档 + 五道资格 ----
    slots = pc.recipe_slots(rec, p_view)
    tier = pc.recipe_tier(slots)
    # 自定配方的「标称火」没有意义（火本就按玩家实况取，不耗材），
    # 若拿占位 B 火算 nominal 会误报「本可出 X 级但降档」。
    nominal = tier if rec.get("custom") else pc.recipe_tier(pc.recipe_slots(rec))
    if tier < 0:
        return "❌ 配方「%s」五维不全，无法开炉。" % rec["name"]
    gate = pc.puppet_gate(p_view, tier, need_mp=True)
    if not gate["ok"]:
        return "❌ 未能开炉：" + gate["reason"]

    # ---- 3. 材料与躯体校验（E10 冰封保鲜 / E15 炼尽神智）----
    b_row = pc.slot_row("body", rec["body"])
    body = {"body_name": rec["body"], "body_realm": b_row[1], "body_star": b_row[2],
            "body_fresh": True, "body_fresh_until": 0, "mind_stripped": True}
    cost = pc.recipe_materials(rec)
    chk = pc.craft_checks(p_view, mats, body, tier, need=cost)
    if not chk["ok"]:
        return "❌ " + chk["reason"]

    # ---- 4. 扣料 → 扣斗气 → 落库 ----
    if not await pstore.take_materials(qq, cost):
        return "❌ 材料在校验后被他人取用，炼制中断、材料未扣。"
    # 斗气按天烧：见底则中断、材料不退（此处扣一笔斗气续航）
    need_mp = int(core.mp_max(p) * cfg.PUPPET_MP_FACTOR.get(tier, 1.0))
    p["mp"] = max(0, int(p.get("mp", 0)) - need_mp)
    await store.put_player(p)
    c_row = pc.slot_row("core", rec["core"])
    s_row = pc.slot_row("soul", rec["soul"])
    m_row = pc.slot_row("metal", rec["metal"])
    fg = pc.fire_grade(p)
    puppet = pc.make_puppet(
        p, body,
        {"core_name": rec["core"], "core_tier": c_row[1], "core_attr": c_row[2]},
        {"soul_source": rec["soul"], "soul_strength": s_row[1], "mind_stripped": True},
        {"metal_name": rec["metal"], "metal_grade": m_row[1]},
        {"fire_tier": fg["fire_tier"], "fire_attr": "火", "fire_grade": fg["grade"]},
    )
    pid = await pstore.add_puppet(qq, puppet)
    if not elig["ready"]:
        await pstore.set_ready(qq, True)
    tier_name, skin = cfg.PUPPET_TIER_BY_IDX[puppet["tier"]]
    atk = int(pc.puppet_attack(puppet, p))
    realm_label = pc.puppet_realm_label(puppet, p)
    fire_note = ""
    downgrade = ""
    if int(puppet["tier"]) < int(nominal):
        fg_word = {1: "兽火", 2: "异火", 3: "高阶异火"}.get(int(fg["grade"]), "兽火")
        downgrade = ("此配方本可出%s，但你的炼制之火只到%s——降为%s"
                     % (cfg.PUPPET_TIER_BY_IDX[max(0, nominal)][0], fg_word, tier_name))
        fire_note = "\n　⚠ " + downgrade + "。"
    # 成卡取数（只有成功路径写；失败/引导回复保持文本，二者互斥）
    if report is not None:
        report.update({
            "ok": True,
            "name": puppet["name"], "recipe": rec["name"],
            "custom": bool(rec.get("custom")), "pid": int(pid),
            "tier": int(puppet["tier"]), "nominal_tier": int(nominal),
            "tier_name": tier_name, "skin": skin,
            "atk": atk, "realm_label": realm_label,
            "cost": dict(cost), "need_mp": int(need_mp), "mp_left": int(p["mp"]),
            "downgrade": downgrade,
            "purity": int(puppet.get("purity") or 0),
            "bonded": bool(puppet.get("bonded")),
            "awakened": bool(puppet.get("awakened")),
            "body_name": puppet.get("body_name") or "",
            "body_realm": puppet.get("body_realm") or "",
            "body_star": int(puppet.get("body_star") or 0),
        })
    return ("⚗️ 炼制成功！【%s】%s·%s皮（%s，id=%d）\n"
            "　用料：%s\n"
            "　肉身攻击 %d　%s（不吃斗气/斗技）\n"
            "　斗气 -%d（余 %d）%s\n"
            "　发「傀儡」查看；雷淬可再升档。" % (
                puppet["name"], tier_name, skin, rec["name"], pid,
                "、".join(cost), atk, realm_label,
                need_mp, int(p["mp"]), fire_note))


async def cmd_puppet_ruin(store, qq, pstore=None, report=None) -> str:
    """傀儡遗迹日副本：**自动逐段往下打，力竭为止**（2026-11-20 第十轮）。

    旧版一次调用只打当前一段，玩家要点六次；现在一条口令连打到战败或全通。
    ``fight`` 本来就原地改 p 的 hp/mp，所以连打天然实现「力竭」—— 不另造
    气血/斗气归零的新判定（那两个字段在战斗里并不判负，斗气见底转普攻）。

    难度走 ``monster_scaled`` 反解；出战傀儡的期望输出同时进反解与 ``fight``
    （``puppet_expected_damage`` 唯一口径，且两处都传真实 p）。

    ``pill_budget``（2026-11-20 第十轮补）：六段**共用一份**自动服药预算，
    上限 ``cfg.PUPPET_RUIN_PILL_CAP``。默认 ``auto_pill=True`` 时每场都能吃 2 颗，
    六段一趟能吞十几颗回灵丹且玩家毫不知情；而丹药管够时「力竭」永远不会到来，
    连打就成了无限续杯。共用一个 dict 才能真的累计封顶（``fight`` 会就地累加
    ``pill_used``），与 ``core`` 里多敌连战同一写法。

    ``report``：写 ``runs`` 列表（每段一条），顶层保留**最后一段**的旧单段字段，
    兼容既有卡片/测试。一处输出=一条消息，战报并卡不发多条。
    """
    from . import puppet_core as pc
    from . import flame_bonus as fb
    pstore = pstore or DEFAULT_PUPPET_STORE
    p = await _player(store, qq)
    if not p:
        return NOT_ENTERED
    qq = str(qq)
    day = date.today().isoformat()
    st = await pstore.ruin_state(qq, day)
    stages = cfg.PUPPET_RUIN_STAGES
    total = len(stages)
    if int(st["stage"]) >= total:
        if report is not None:
            report.update({"cleared": True, "total": total,
                           "stage_idx": total - 1, "win": True})
        return "⚔️ 今日傀儡遗迹已全通（%d/%d），明日再来。" % (total, total)

    idx = int(st["stage"])
    roster = await pstore.list_puppets(qq)
    pu = await pstore.active_puppet(qq)
    n = pc.formation_count(roster)
    # B 线傀儡异火：一个 flame_mult 同时喂怪血反解与实战傀儡击（同源，红线）。
    flame_mult = 1.0 + fb.puppet_atk_bonus(p)
    runs = []
    text_rows = []
    lore_gain = False
    wild_id = 0
    last = None
    # 整趟共用一份服药预算：dict 被 fight 就地累加，跨段累计封顶。
    pill_budget = {"hp": 0, "mp": 0, "cap": int(cfg.PUPPET_RUIN_PILL_CAP)}

    while idx < total:
        sg = stages[idx]
        # 🚨 固定难度（2026-11-22 用户选定，与爬塔同款）：怪数值按 sg["power_rec"]
        #    反查的标尺玩家反解，与真人战力无关 —— 斗尊打斗宗段是真碾压。
        #    物种名在 ``monster``（段名是地点，不是对手）；bonus 同样锚标尺，
        #    真人那具更强傀儡在 fight 里全额出手 = 净收益（见 ruin_scaled_monster）。
        mon = core.ruin_scaled_monster(sg, pu, n, puppet_flame_mult=flame_mult)
        res = core.fight(p, mon, puppet=pu, formation_n=n,
                         puppet_flame_mult=flame_mult, pill_budget=pill_budget)
        await _finalize_battle(p, qq, res, "傀儡遗迹",
                               opponent=sg["monster"], puppet=pu)
        win = bool(res["win"])
        drops = dict(sg["drops"]) if win else {}
        run = {
            "stage_idx": idx, "stage_name": sg["name"],
            "monster": sg["monster"], "win": win,
            "rounds": int(res["rounds"]),
            "taken": int(res.get("taken") or 0),
            "mon_hp_left": int(res.get("mon_hp_left") or 0),
            "drops": drops,
        }
        runs.append(run)
        last = run
        if not win:
            text_rows.append("❌ 第 %d 段 %s 力竭（%d 回合，敌余血 %d），今日仍在第 %d 段" % (
                idx + 1, sg["name"], run["rounds"], run["mon_hp_left"], idx + 1))
            break
        text_rows.append("✅ 第 %d 段 %s（%d 回合，承伤 %d）—— %s" % (
            idx + 1, sg["name"], run["rounds"], run["taken"],
            "、".join("%s×%d" % (k, int(v)) for k, v in drops.items()) or "无掉落"))
        for name, qty in drops.items():
            await pstore.add_material(qq, name, int(qty))
        if cfg.PUPPET_LORE_MATERIAL in drops:
            await pstore.set_lore(qq, True)
            lore_gain = True
            wild = pc.make_puppet(
                p, {"body_name": "残破傀儡躯", "body_realm": "斗宗", "body_star": 0,
                    "body_fresh": True, "body_fresh_until": 0},
                {"core_tier": 7, "core_attr": "火"},
                {"soul_source": "无主残魂", "soul_strength": 600,
                 "mind_stripped": True},
                {"metal_name": "暗金玄铁", "metal_grade": 2},
                {"fire_tier": 7, "fire_attr": "火"}, origin="wild")
            wild_id = await pstore.add_wild_puppet(wild)
        # 水位线只升不降（ruin_clear 内部 MAX），已通关的段不会被重复领奖。
        await pstore.ruin_clear(qq, day, idx + 1)
        idx += 1

    # 连打跨场结转 hp/mp：只在最后写回一次，避免中间状态半落库。
    await store.put_player(p)

    if report is not None:
        report.update({
            "runs": runs,
            # 以下为旧单段字段（顶层 = 最后一段），兼容既有卡片与守门测试。
            "stage_idx": int(last["stage_idx"]) if last else idx,
            "stage_name": str(last["stage_name"]) if last else "",
            "monster": str(last["monster"]) if last else "",
            "win": bool(last["win"]) if last else False,
            "rounds": int(last["rounds"]) if last else 0,
            "taken": int(last["taken"]) if last else 0,
            "mon_hp_left": int(last["mon_hp_left"]) if last else 0,
            "drops": dict(last["drops"]) if last else {},
            "lore": lore_gain, "wild_id": wild_id or None,
            "cleared": bool(idx >= total), "total": total,
        })
    tail = ""
    if lore_gain:
        tail = ("\n📜 得《天妖傀》竹简——傀儡之技已习得。"
                "\n🩸 遗迹无主残傀（id=%d），发「傀儡 收服 %d」可降服。" % (wild_id, wild_id))
    return ("【傀儡遗迹·自动连打】今日进度 %d/%d\n%s%s"
            % (idx, total, "\n".join(text_rows) or "　未挑战", tail))




_PUPPET_COLUMNS = frozenset({
    "name", "tier", "tier_name", "skin", "body_name", "body_realm", "body_star",
    "body_fresh", "body_fresh_until", "core_tier", "core_attr", "soul_source",
    "soul_strength", "soul_fed", "mind_stripped", "metal_name", "metal_grade",
    "fire_tier", "fire_attr", "purity", "bonded", "awakened", "origin", "damaged",
})


async def _cmd_puppet_temper(store, qq, p, roster, elig, pstore, times: int = 1) -> str:
    """雷淬（P1-2 批量）：一次结算 ``times`` 次，原子扣费、单次判档语义不变。

    批量 = 连续开炉各判各的，不是一炉多丹；N 次共享一次 CD（这里无 CD）与一次
    写库。雷淬石/金币按 N 倍一次性校验并扣取，任何一步失败整笔不扣（禁兜底）。
    """
    from . import puppet_core as pc
    cur = next((x for x in roster if x["id"] == elig["active_id"]), roster[0])
    times = max(1, int(times))
    # 天级雷纹满层（purity 到硬上限）才拒绝；天级未满层仍可淬（黑魔雷长线）。
    if cur["tier"] >= 2 and int(cur.get("purity", 0)) >= pc.purity_hard_max():
        return "【%s】雷纹已满（%d 层），此界圆满，无需再淬。" % (
            cur["name"], int(cfg.PUPPET_VEIN_MAX))
    # 2026-11-20 修 bug：雷淬石以前**永不消耗**（只扣金币），掉落的石头毫无用处。
    # P1-2：按 times 倍一次性校验总消耗，绝不「扣一半中断留烂摊子」。
    mats = await pstore.materials(qq)
    stone_need = int(cfg.PUPPET_TEMPER_STONE) * times
    stone_have = int(mats.get("雷淬石", 0))
    if stone_have < stone_need:
        return "雷淬石不足（有 %d，%d 次需 %d）——去「傀儡遗迹」第三段「虚空雷池」取。" % (
            stone_have, times, stone_need)
    gold_need = int(cfg.PUPPET_TEMPER_COST) * times
    if int(p.get("gold", 0)) < gold_need:
        return "金币不足，%d 次雷淬需 %d 金。" % (times, gold_need)
    # 先扣料再扣金：料被并发取走时直接中断，不会白扣金币。
    if not await pstore.take_materials(qq, {"雷淬石": stone_need}):
        return "雷淬石在校验后被取用，雷淬中断、金币未扣。"
    p["gold"] = int(p.get("gold", 0)) - gold_need
    await store.put_player(p)

    # 逐次开炉：每次按**当前档位**取雷力增量，各判各的升档；派生标记不入写库 payload。
    new = dict(cur)
    tier_ups = []
    for i in range(times):
        step = pc.temper(new)
        if step.get("tier", cur["tier"]) > int(new.get("tier", 0)):
            tier_ups.append((i + 1, step["tier_name"], step["skin"]))
        new = dict(new, **step)
    payload = {k: v for k, v in new.items() if k in _PUPPET_COLUMNS}
    await pstore.update_puppet(qq, cur["id"], **payload)

    # 回执：批量只出汇总 + 每次升档节点（消除 15~24 次重复点击的刷屏）。
    lei = ["丹雷", "五色丹雷", "黑魔雷"][max(0, min(2, int(cur["tier"])))]
    vein = int(new.get("vein", 0))
    vein_txt = ("　紫纹 %d 层" % vein) if vein else ""
    if tier_ups:
        ups = "、".join("第%d次→【%s】%s皮" % (i, n, s) for i, n, s in tier_ups)
        return "⚡ 雷霆淬炼×%d（%s）：%s；当前纯度 %d%%，%s·%s皮%s。" % (
            times, lei, ups, int(new["purity"]), new["tier_name"], new["skin"], vein_txt)
    return "⚡ 雷霆淬炼×%d（%s）：纯度 %d%%，当前 %s·%s皮%s。" % (
        times, lei, int(new["purity"]), new["tier_name"], new["skin"], vein_txt)


async def _cmd_puppet_reforge(store, qq, p, roster, elig, pstore, arg) -> str:
    """P2 换躯重炼（E10/U11）：消耗新躯体材料，五维木桶重算档位，存量全继承。

    🚨 旧躯不返还（原著换躯是覆盖），新躯体名必须是 PUPPET_BODY_TIERS 里的真名；
    非法名/缺料如实报错、不扣任何东西。purity/bonded/awakened/soul_strength
    原样保留 —— 换躯只换「躯体」这一维。
    """
    from . import puppet_core as pc
    cur = next((x for x in roster if x["id"] == elig["active_id"]), roster[0])
    name = (arg or "").replace("换躯", "").strip()
    if not name:
        options = "、".join(cfg.PUPPET_BODY_TIERS)
        return "用法：傀儡 换躯 <躯体名>。可选：%s（去遗迹四~六段取）。" % options
    if name not in cfg.PUPPET_BODY_TIERS:
        return "无此躯体「%s」。可选：%s。" % (name, "、".join(cfg.PUPPET_BODY_TIERS))
    if str(name) == str(cur.get("body_name") or ""):
        return "【%s】已是%s之躯，无须重炼。" % (cur["name"], name)
    new = pc.reforge_body(cur, name)
    if not new:
        return "换躯失败：躯体数据异常，未扣任何材料。"
    mats = await pstore.materials(qq)
    if int(mats.get(name, 0)) < 1:
        return "缺少躯体「%s」（遗迹四~六段可得），未扣任何材料。" % name
    if not await pstore.take_materials(qq, {name: 1}):
        return "躯体在校验后被取用，换躯中断。"
    payload = {k: v for k, v in new.items() if k in _PUPPET_COLUMNS}
    await pstore.update_puppet(qq, cur["id"], **payload)
    old_tier = cfg.PUPPET_TIER_BY_IDX[int(cur["tier"])][0]
    new_tier = cfg.PUPPET_TIER_BY_IDX[int(new["tier"])][0]
    return ("🦴 换躯重炼：【%s】躯体换为【%s】（%s%s），档位 %s→%s；"
            "雷淬纯度/有灵/认主全部保留，旧躯不返还。"
            % (cur["name"], name, new["body_realm"],
               ("·%d星" % new["body_star"]) if new["body_star"] else "",
               old_tier, new_tier))


async def _cmd_puppet_soul_feed(store, qq, p, roster, elig, pstore, arg) -> str:
    """P3 养魂（E09/N08，第900章「养魂涎」）：把养魂涎渡进傀儡，稳定加灵魂强度。

    为什么要有这条命令：养魂涎是五品丹，玩家从宝库买来只会「服丹」，而丹药
    结算只认玩家自己（气血/斗气/机缘…），**没有傀儡的上下文**。所以养魂必须有
    自己的入口，否则丹要么被当成机缘吃掉、要么吞了没反应 —— 两种都是把玩家的
    钱扔进水里。

    规则（确定性、不掷骰）：
      * 每次 +``PUPPET_SOUL_FEED_GAIN``，累计上限 ``PUPPET_SOUL_FEED_MAX``；
      * **只扣真正用得上的丹**：喂到 190 再要求喂 5 枚，只吃 1 枚到 200；
      * 已满 / 没丹 / 没傀儡三种分支都先报错后返回，**一枚都不扣**；
      * 灵魂进益走 ``soul_total``，同一份数值喂给攻击/气血/护甲三个出口
        （卡面与实战同源，不做两张皮）。
    """
    from . import puppet_core as pc
    cur = next((x for x in roster if x["id"] == elig["active_id"]), roster[0])
    tail = arg[len("养魂"):].strip()
    want = int(tail) if tail.isdigit() else 1
    want = max(1, min(50, want))
    pill = cfg.PUPPET_SOUL_PILL
    info = pc.soul_feed_info(cur)
    n_formation = pc.formation_count(roster)
    head = "🧿 【%s】灵魂强度 %d/%d（基准 %d + 养魂 %d）" % (
        cur["name"], info["fed"], info["cap"], pc.soul_base(cur), info["fed"])
    if info["maxed"]:
        return (head + "\n　已至养魂上限，再喂也是白费 —— 丹留在你包里。\n"
                "　换一具傀儡养：发「傀儡」看名录。")
    have = core.count_item(p, pill)
    if have <= 0:
        return (head + "\n　你身上没有【%s】（五品丹，宝库有售）。\n"
                "　还差 %d 点可养满，每枚 +%d。"
                % (pill, info["left"], info["gain"]))
    res = pc.feed_soul(cur, min(want, have))
    if not res.get("ok"):
        # 上面两道闸已挡住 maxed/none；这里只兜不可达分支，如实报错不扣丹。
        return head + "\n　这次没有可渡之物，丹一枚未动。"
    used = int(res["used"])
    core.take_item(p, pill, used)
    await pstore.update_puppet(qq, cur["id"], soul_fed=int(res["fed"]))
    await store.put_player(p)
    after = pc.soul_feed_info(dict(cur, soul_fed=int(res["fed"])))
    g0 = pc.guard_profile(cur, p, n_formation)
    g1 = pc.guard_profile(dict(cur, soul_fed=int(res["fed"])), p, n_formation)
    atk0 = int(pc.puppet_attack(cur, p))
    atk1 = int(pc.puppet_attack(dict(cur, soul_fed=int(res["fed"])), p))
    lines = [
        "🧿 养魂：「%s」渡入【%s】×%d" % (pill, cur["name"], used),
        "　灵魂强度 +%d（%d/%d）· 有效灵魂 %d"
        % (used * int(res["gain"]), after["fed"], after["cap"], after["total"]),
        # 数值必须给「看得见的落点」：灵魂不是独立的孤立数值，它同时喂攻击/气血/护甲。
        "　护主气血 %d→%d　护甲 %d→%d" % (
            g0["hp_max"], g1["hp_max"], g0["armor"], g1["armor"]),
    ]
    if atk1 > atk0:
        lines.append("　肉身攻击 %d→%d" % (atk0, atk1))
    else:
        # 攻击已贴「不抢风头」封顶时如实说明，别让玩家以为养魂对攻击无效。
        lines.append("　肉身攻击 %d（已贴封顶，养魂的进益落在气血与护甲上）" % atk0)
    if res["maxed"]:
        lines.append("　⭐ 已养满 %d 点，此傀魂魄圆满。" % after["cap"])
    left = have - used
    if left > 0:
        lines.append("　余【%s】%d 枚%s" % (
            pill, left, "（超出上限的部分没动）" if res["maxed"] else ""))
    return "\n".join(lines)


async def _cmd_puppet_formation(qq, roster, elig, pstore) -> str:
    """结阵回执（P0-1）：3 具同档 → 领头本场临时升一档，战后退回。

    🚨 旧文案写「每多一具 +15%，十具封顶 +135%」而 ``FORMATION_MAX=3``、
    低等级实测 +0%，是骗人文案，已删除。这里只说实际生效的规则。
    """
    from . import puppet_core as pc
    n = pc.formation_count(roster)
    if n < 2:
        return "结阵须至少两具傀儡。你目前 %d 具。" % n
    names = "、".join(x["name"] for x in roster[:10])
    need = int(getattr(cfg, "PUPPET_FORMATION_BURST_N", 3))
    cur = next((x for x in roster if elig and x["id"] == elig.get("active_id")), roster[0])
    tier = max(0, min(2, int(cur.get("tier", 0) or 0)))
    same = sum(1 for x in roster if x and max(0, min(2, int(x.get("tier", 0) or 0))) == tier)
    if n < need or same < need or tier not in tuple(
            getattr(cfg, "PUPPET_FORMATION_BURST_TIERS", (0, 1))):
        return ("【傀儡结阵】%d 具同场：%s\n"
                "　本场须 %d 具同档方可成阵；当前同档 %d 具。成阵后领头临时升一档，战后退回，"
                "结阵本身零消耗。" % (n, names, need, same))
    nxt = cfg.PUPPET_TIER_BY_IDX[min(2, tier + 1)][0]
    return ("【傀儡结阵】%d 具同场：%s\n"
            "　阵成！【%s】本场暂化【%s】；金光散后复为原档，不永久升档、零消耗。"
            % (n, names, cur["name"], nxt))


async def _cmd_puppet_claim(store, qq, p, arg, pstore) -> str:
    """E37：收服遗迹里的无主残傀。须灵魂境界达标（控得住别人的傀儡）。"""
    from . import puppet_core as pc
    ok, why = pc.gate_soul(p)
    if not ok:
        return "❌ 收服无主傀儡须灵魂境界达标：" + why
    m = re.search(r"收服\s*(\d+)", arg or "")
    if not m:
        wild = await pstore.wild_puppets()
        if not wild:
            return "遗迹里暂无无主残傀。打通「傀儡遗迹」第三段可遇。"
        listing = "、".join("id=%s %s" % (x["id"], x["name"]) for x in wild[:8])
        return "遗迹无主残傀：%s。发「傀儡 收服 <id>」降服（需 %d 金）。" % (
            listing, cfg.PUPPET_CLAIM_COST)
    pid = int(m.group(1))
    # P2 印记强度（D06/E03）：先找到残傀，按档位算印记，确定性判定能否抹除。
    wild = await pstore.wild_puppets()
    target = next((x for x in wild if int(x["id"]) == pid), None)
    if not target:
        return "该傀儡已被他人收服或并不存在，收服失败。"
    craft_n = int(p.get("craft_count", 0))
    strength = pc.mark_strength(target)
    if craft_n < int(cfg.PUPPET_MARK_DISCOVER_MIN):
        return "你尚未亲手炼制过傀儡，探不破残傀的原主印记——先炼一具再来。"
    need = pc.mark_exp_needed(target)
    if craft_n < need:
        # 失败如实反馈退金币（这里还没扣任何东西），绝不静默扣钱
        return ("🩸 原主印记强度 %d，你炼制经验 %d 不足（需 %d）——抹除失败，"
                "未扣金币。多炼几具再来。" % (strength, craft_n, need))
    if int(p.get("gold", 0)) < cfg.PUPPET_CLAIM_COST:
        return "金币不足，收服需 %d 金。" % cfg.PUPPET_CLAIM_COST
    if not await pstore.claim_puppet(qq, pid):
        return "该傀儡已被他人收服或并不存在，收服失败。"
    p["gold"] = int(p.get("gold", 0)) - cfg.PUPPET_CLAIM_COST
    await store.put_player(p)
    return ("🩸 收服成功：原主印记强度 %d 已被你的灵魂覆写，残傀认主。"
            "发「傀儡」查看。" % strength)


async def _cmd_puppet_awaken(store, qq, p, roster, elig, pstore, arg="") -> str:
    """E43/E44 + P2 融魂反噬（D07/Q3）：熔入残魂生灵智，须先设陷阱拦反噬。

    原著（E04 北王）：融魂必有反噬，北王是先布陷阱再动手 —— 本作 100% 发生，
    由「设陷阱」这一步确定性拦下，不掷骰（养成链全确定）。

    口令：
      * ``傀儡 融魂 设陷阱`` —— 布置陷阱并熔魂（额外耗 PUPPET_AWAKEN_TRAP_SOUL
        枚无主灵魂），一次完成；
      * 裸 ``傀儡 融魂`` —— 拒绝，如实提示反噬会反扑，未扣任何材料。
    """
    from . import puppet_core as pc
    cur = next((x for x in roster if x["id"] == elig["active_id"]), roster[0])
    if cur.get("awakened"):
        return "【%s】已有灵智，无需再熔。" % cur["name"]
    ok, why = pc.gate_soul(p)
    if not ok:
        return "❌ 熔魂须灵魂力量够强：" + why
    trap = "设陷阱" in (arg or "")
    if not trap:
        return ("⚠️ 融魂必有反噬：残魂入体时会反扑夺舍。须发「傀儡 融魂 设陷阱」"
                "先布下陷阱拦住反噬，再一举熔成（额外耗 %d 枚无主灵魂）。"
                "未布陷阱，未扣任何材料。" % int(cfg.PUPPET_AWAKEN_TRAP_SOUL))
    mats = await pstore.materials(qq)
    need = {"无主灵魂": int(cfg.PUPPET_AWAKEN_SOUL) + int(cfg.PUPPET_AWAKEN_TRAP_SOUL)}
    lack = [k for k, v in need.items() if int(mats.get(k, 0)) < int(v)]
    if lack:
        return "设陷阱+融魂缺少：" + "、".join(lack) + "（去「傀儡遗迹」第二段取）"
    if int(p.get("gold", 0)) < cfg.PUPPET_AWAKEN_COST:
        return "金币不足，融魂需 %d 金。" % cfg.PUPPET_AWAKEN_COST
    await pstore.take_materials(qq, need)
    p["gold"] = int(p.get("gold", 0)) - cfg.PUPPET_AWAKEN_COST
    await store.put_player(p)
    await pstore.update_puppet(qq, cur["id"], awakened=True)
    return ("🔥 陷阱已成，反噬被拦下！熔魂入体：【%s】生出灵智，可主动出手——"
            "已是北王式改良傀儡，非基础款。" % cur["name"])
