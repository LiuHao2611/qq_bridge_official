# -*- coding: utf-8 -*-
"""斗破苍穹 · PVE 回合制战斗核心（2026-11-25 从 core.py 拆出）。

为什么独立成模块
----------------
``core.py`` 逼近项目的 **200KB 上传红线**（204800 B，pre-commit 按 HEAD 判定，
历史豁免不适用），任何新改动都会被拦。``fight()`` 单函数 668 行 / 42KB、
且是完整自洽的战斗内核，迁出后 core.py 立减约 42KB。

兼容性（红线）
--------------
* ``core.py`` 在原位置保留 ``from .battle_core import fight`` —— 对外仍是
  ``core.fight``，所有既有调用（encounter / weekly_floor / tower_floor /
  duel 对照 / commands / 测试 monkeypatch）**逐字节不变**。
* 本模块**不复制**任何存档字段/伤害公式，只把原函数体搬过来；core 助手通过
  ``_core.xxx`` 运行时取，避免循环导入（core 在本文件定义处仍是半初始化状态）。
* ``inspect.getsource(core.fight)`` 依旧拿到完整战斗代码（源码守门测试依赖）。
"""
from __future__ import annotations

import math
import random

from . import config as cfg
from . import danwen as dw
from . import skill_effects as fx
from . import sect_mechanics as sm
from . import core as _core


def fight(p, mon, rng=random.random, aid=None, roll_aid=True, tower_mod=None,
          puppet=None, formation_n=0, locked=None, auto_pill=True,
          pill_budget=None, puppet_flame_mult=1.0, exp_override=None) -> dict:
    """回合制模拟，返回战报。不直接发消息，只改 p。

    ``puppet`` / ``formation_n``（2026-11-18 傀儡）：出战傀儡真实列 dict 与同场
    傀儡数。默认 None/0 → 既有调用逐字节不变。傀儡每回合追加一次「傀儡击」，
    伤害口径走 ``puppet_core.puppet_attack``（不吃 mp/斗技，忠于原著），
    log 进 dmg_log 供战斗回看。它的期望输出已在 ``monster_scaled(p, mon,
    puppet_bonus=...)`` 反解里计入怪血，两处同源（红线）。

    ``tower_mod``（2026-11-17 爬塔扩展点，默认 None 时逐字节不影响任何既有调用）：
      塔词缀/三选一增益通过它注入，**不改内核战斗逻辑、不新增 effect 类型**：
        atk_mult / hp_mult / def_mult —— 怪物数值缩放（狂暴/难度）
        self_effects  —— 玩家每次出手追加的 effect 列表（连击/破防/吸血）
        evade_add / crit_add —— 玩家闪避/暴击率加成（百分点）
        heal_mult —— 玩家吸血/回血倍率（Boss 禁疗 <1）
        reflect —— 怪物反伤比例（Boss 反弹玩家伤害的占比）

    斗气是**跨战斗结转的资源**（2026-11-16 用户「去掉强制回满斗气，不然突破
    就失去了意义」）：开战用玩家当前剩余 ``p["mp"]``，战中按技能阶位逐次扣减，
    战后写回剩余值——不再每战白送满。斗气见底后自动转武器普攻（伤害约 42%），
    仍有输出、能慢慢攒经验，但效率大降；要回满只能靠**突破/升级**（``_refill``）
    或稀有回气手段。这让斗气上限成长（升级/青莲地心火 +25%）与突破真正有意义。

    :param aid: 已掷好的机缘结果（dict）或 None。仅当 ``roll_aid=False`` 时生效。
    :param roll_aid: 是否在开场自己掷机缘。**默认 True**——单敌直接调用
      （PVP / 平衡脚本 / 既有测试）语义完全不变；多敌 ``encounter`` 传 False，
      把掷骰权上收到整场一次，避免每敌各掷导致累积概率突破 2% 硬顶。
    :param locked: 背包里被玩家锁定的物品名集合。自动服药**绝不碰**这些药
      （2026-11-19 用户「锁定的物品不会被自动使用」）。
    :param auto_pill: 是否允许自动服药。爬塔/周本打在深副本上，传 False
      ——它们有专属三选一补给，不能消耗真实背包。

    2026-11-19 战斗过程丰富化（用户要求）：
      * **不控制回合**：删掉 15 回合硬上限（双方每回合至少 1 点伤害，必分胜负）；
        只留 ``HARD_ROUND_CAP=300`` 作异常熔断，触发时按血量比例判胜。
      * **自动服药**：气血 <35% / 斗气 <20% 时从背包取最小可用疗伤丹，
        各限 2 枚，锁定物品跳过。
      * **怪物技能**：每个怪按表概率施放 burst / poison / drain，战报可查。
      * **战斗意外**：怪物小概率崴脚/自乱阵脚（本回合不攻击）；
        玩家小概率「心火共鸣」1~2 回合增伤减伤（原著依据见设计文档）。
    """
    st = _core.stats(p)
    root = cfg.root_factor(p)   # 根基裁定（隐形）：PVE 承伤侧打折；输出侧在 _hit 内
    hp = int(p.get("hp") or st["体力"])
    # 开战沿用当前斗气；clamp 到上限防越界存档。
    mp = max(0, min(_core.mp_max(p), int(p.get("mp", _core.mp_max(p)))))
    # 丹纹赐福：本场快照；斗气回满型开战生效一次
    _dw = dw.fight_state(p)
    if _dw["mp_refill"] and dw.consume(p, "B06"):
        mp = _core.mp_max(p)
    m_hp = int(mon["hp"])
    m_atk = int(mon["atk"])
    m_def = int(mon["def"])

    tm = tower_mod or {}
    if tm:
        m_hp = max(1, int(m_hp * float(tm.get("hp_mult", 1.0))))
        m_atk = max(1, int(m_atk * float(tm.get("atk_mult", 1.0))))
        if tm.get("def_mult"):
            m_def = max(0, int(m_def * float(tm["def_mult"])))

    rounds, dmg_log, taken = 0, [], 0
    # 2026-11-19 战斗事件状态（全是本场局部变量，绝不写回存档）
    locked_set = set(locked or ())
    #: 自动服药计数。多敌遭遇由 encounter 传同一份预算，防 5 连战各嗑 2 枚。
    pill_used = pill_budget if pill_budget is not None else {"hp": 0, "mp": 0}
    # ⚠️ 不能用 or：cap=0 是合法「一颗不吃」，0 是 falsy 会回落默认 2 颗。
    pill_cap = (int(pill_used["cap"]) if "cap" in pill_used
                else int(cfg.AUTO_PILL_MAX_PER_FIGHT))
    fortune_left = 0
    fortune_seen = False
    fortune_round = 0       # 发放回合号（当回合不递减）
    exhausted = False
    _kind = str(mon.get("kind") or "")

    def _try_auto_pill(kind: str) -> None:
        """血/蓝低于阈值时自动服一枚最小可用疗伤丹（锁定物品跳过）。"""
        nonlocal hp, mp
        if not auto_pill or pill_used.get(kind, 0) >= pill_cap:
            return
        cap = int(st["体力"]) if kind == "hp" else _core.mp_max(p)
        cur = hp if kind == "hp" else mp
        ratio = (cfg.AUTO_PILL_HP_RATIO if kind == "hp"
                 else cfg.AUTO_PILL_MP_RATIO)
        if cap <= 0 or cur >= cap * float(ratio):
            return
        for _gain, _name in _core.auto_pill_candidates(p, kind, locked_set):
            if not _core.take_item(p, _name, 1):
                continue
            # 2026-11-20 减疗真落地：BOSS 的 debuff_heal 也压低自动服药回血量。
            # 旧版只压吸血，而玩家主要靠服药回血 → "减疗"几乎无感，是假机制。
            _gain = max(1, int(_gain * fx.heal_mult(p_fx)))
            actual = min(cap, cur + _gain) - cur
            if kind == "hp":
                hp += actual
            else:
                mp += actual
            pill_used[kind] += 1
            dmg_log.append(_mark({"dmg": 0, "kind": "服药", "pill": _name,
                                  "amount": int(actual), "move": "服药",
                                  "auto": True}))
            return

    crit_add = float(tm.get("crit_add", 0))
    lifesteal = 0.02 * int((p.get("flames") or {}).get("三千焱炎火", 0))
    lifesteal += sm.venom_lifesteal(p)  # 毒宗吸血×根基
    heal_mult = float(tm.get("heal_mult", 1.0))
    self_effects = list(tm.get("self_effects") or [])
    reflect = float(tm.get("reflect", 0))
    var = max(0.0, float(cfg.MON_DMG_VAR))
    # 2026-11-15 多维效果：本场临时状态（战后即弃，绝不写回 p/mon）
    p_fx, m_fx = fx.new_state(), fx.new_state()
    # 四技能候选池 + 大招冷却；无 skills 的怪继续走旧分支。
    _boss_skills = [dict(s) for s in (mon.get("skills") or []) if isinstance(s, dict)]
    _boss_ult_cd = 0
    # 2026-11-20 怪物隐藏面板（命中/暴击/闪避，%）。**只认 monster_scaled 挂在
    # 实例上的 panel**：手搓怪（测试/工具直接给 hp/atk 的字典）没有 panel →
    # 三项全 0 → 判定阈值逐字节等于旧版，既有逐招快照零回归。
    _panel = mon.get("panel") or {}
    m_hit = max(0.0, float(_panel.get("hit") or 0.0))
    m_crit = max(0.0, float(_panel.get("crit") or 0.0))
    # 怪物闪避的既有口径是**分数**（规格里手写 mon["dodge"]=0.25 表示 25%），
    # 面板是百分数 —— 这里显式区分：实例显式带走 dodge 就用它，否则读面板换算。
    if "dodge" in mon:
        m_dodge_frac = max(0.0, min(0.9, float(mon["dodge"] or 0)))
    else:
        m_dodge_frac = max(0.0, min(0.9, float(_panel.get("dodge") or 0.0) / 100.0))
    m_fx["evade"] = m_dodge_frac
    _seal = sm.apply_seal(m_fx, p)
    _seal_noted = False
    # 命中对冲（玩家防守侧）：怪物的命中直接减在玩家闪避上，与 _hit 里
    # 「玩家命中减怪闪避」是同一条公式的另一半。m_hit=0（手搓怪）时不变。
    p_hit = _core.hit_rate(p)
    dodge = max(0.0, min(95.0, _core.dodge_rate(p) + float(tm.get("evade_add", 0))) - m_hit)
    # Q5/B3：BOSS / 精英对定身有控制抗性，普通怪按基础概率判定。
    _root_resist = (float(fx.BOSS_ROOT_RESIST)
                    if (mon.get("is_boss") or _core.mon_tier(_kind) in ("elite", "boss"))
                    else 0.0)
    # E3/Q5：藤蔓技门控传参——地形来自 core.encounter 按章节地域挂到 mon 上，
    # 火属性由怪物名 / 自带兽火推断。手搓怪与无 chapter 的爬塔/周本不挂 terrain
    # → 空串按「沃土」处理，木灵缚正常生效，既有战斗逐字节不变。
    m_ctx = {"target_flags": set(), "target_hp_ratio": 1.0,
             "boss_resist": _root_resist,
             "terrain": str(mon.get("terrain") or ""),
             "target_fire": fx.monster_is_fire(mon)}
    # Q6：每场战斗独立的斗技冷却表（技名 → 上次释放回合），跨场不结转。
    cd_state: dict[str, int] = {}

    # 2026-11-17 完整战斗回看的「逐条快照」：每条 log 都带当时的敌我血量/斗气，
    # 回看卡直接读字段，不再靠上下文猜。只加字段，不改任何结算顺序。
    def _mark(entry: dict, foe_hp: int | None = None) -> dict:
        entry["round"] = max(1, rounds)
        entry["foe"] = str(mon.get("name") or "敌手")
        entry["hp_after"] = int(hp)
        entry["mp_after"] = int(mp)
        if foe_hp is not None:
            entry["foe_hp_after"] = max(0, int(foe_hp))
        return entry

    # 2026-11-20 原著 BOSS 登场：沉浸式"突如其来的惊喜/惊吓"。
    # 三段式（对齐原著写法与同类游戏惯例）：
    #   ① 剪影 / 外貌气势（appearance，先让玩家看见"什么东西来了"）
    #   ② 报身份称号（move = title，再让玩家知道"这是谁"）
    #   ③ 接台词或动作引子（intro，最后听他说第一句话）
    # 用 intro=True 标记，dmg=0 —— 不进任何伤害/回合统计，回看卡单独提成引言块。
    _character = mon.get("character")
    _appear = str((_character or {}).get("appearance") or "").strip()
    if not _appear and _character:
        # 双保险：角色包没挂 appearance 时（老档 log、手搓怪、PVP 拼出来的角色包）
        # 统一回落到 CHARACTER_APPEARANCE 这张唯一真值表，避免「有的怪有出场描写、
        # 有的没有」这种靠路径运气的不一致。
        _appear = str(cfg.character_appearance(
            _character.get("name") or mon.get("name")) or "").strip()
    _intro = str((_character or {}).get("intro") or "").strip()
    if _character and (_appear or _intro):
        _notes = []
        if _appear:
            _notes.append(_appear)
        if _intro and _intro != _appear:
            _notes.append(_intro)
        dmg_log.append(_mark({
            "dmg": 0, "kind": "登场", "intro": True,
            "move": str(_character.get("title") or "强敌"),
            "speaker": str(_character.get("name") or mon.get("name") or "敌手"),
            "notes": _notes,
        }))

    # 傀儡每回合原始输出（2026-11-20 第二轮）：只依赖 puppet / 玩家 / 异火倍率，
    # 是循环不变量 → 提到循环外算一次。锚定玩家输出（puppet_attack(puppet, p)），
    # 与命令层喂给 monster_scaled 的 puppet_bonus 用**同一个 p 与同一个函数**。
    _pup_atk = 0.0
    # 傀儡护主面板（2026-11-20 第三轮）：气血/护甲/挡伤概率，同样循环不变量。
    # 🚨 红线：挡伤骰**只在 `if puppet` 分支内消耗 rng**。不传傀儡的战斗一次都
    #    不多掷 —— 否则 218 条逐招回看快照会整片翻红（含 test_battle_review）。
    _pup_prof = {"hp_max": 0, "armor": 0, "chance": 0.0,
                 "threshold": 0, "leak": 0.0, "tier": 0, "awakened": False}
    _pup_hp = 0
    # P0-1：临时升档标记来自命令层的只读副本；只本场有效，战后不写回库。
    _burst = bool(puppet and puppet.get("_formation_burst"))
    _burst_noted = False
    # 护主累计（只供 res 展示；不带傀儡时恒为 0，且不会写进 res）
    _guard_n = 0
    _guard_soak_sum = 0
    _guard_taken_sum = 0
    if puppet:
        from . import puppet_core as _pc0  # 延迟导入避免与 core 环
        # 🚨 必须走 ``puppet_expected_damage``（唯一函数），不能用 ``puppet_attack``：
        #    后者不含结阵与异火，而 `monster_scaled` 的反解是按前者让怪变厚的
        #    —— 两处不同源就是「带结阵反而吃亏」（第二轮遗留 bug，本轮修）。
        _pup_atk = _pc0.puppet_expected_damage(
            puppet, formation_n, flame_mult=puppet_flame_mult, p=p)
        _pup_prof = _pc0.guard_profile(puppet, p, formation_n)
        _pup_hp = int(_pup_prof["hp_max"])

    # 机缘·贵人相助：默认单敌调用自己掷一次（极低概率），命中则一招制敌。
    # 多敌 encounter 传 roll_aid=False + 传入整场预掷的 aid，把掷骰权上收到
    # 整场一次——修复「每敌各掷 → 累积概率突破 2% 硬顶」的 bug（2026-11-16）。
    # 无论哪种路径，都只消耗一次 rng，不影响战斗内随机序列的可复现性。
    if roll_aid:
        aid = _core.roll_hero_aid(p, rng)
    if aid:
        m_hp = 0
        dmg_log.append(_mark({"dmg": int(mon["hp"]), "kind": "贵人相助", "source": "机缘",
                              "helper": aid["name"], "aid": True, "skill": False,
                              "move": "贵人相助"}, m_hp))

    while not aid and rounds < int(cfg.HARD_ROUND_CAP) and m_hp > 0 and hp > 0:
        rounds += 1
        hp_before, mp_before, foe_hp_before = hp, mp, m_hp
        # 怪物毒技能：DoT 统一由 skill_effects 状态机结算（见下），
        # 旧的 poison_turns 计数器已删除——两套并存会把两回合毒算成四回合。
        # 2026-11-20 原著 BOSS 机制：玩家回合开始结算挂在自己身上的 DoT / 眩晕。
        # 普通怪 p_fx 全空 → on_turn_start 返回全 0，既有战斗逐字节不变。
        _pfx_start = fx.on_turn_start(p_fx, rng)
        if _pfx_start["dot_dmg"]:
            hp -= _pfx_start["dot_dmg"]
            taken += _pfx_start["dot_dmg"]
            dmg_log.append(_mark({"dmg": _pfx_start["dot_dmg"], "kind": "中毒",
                                  "taken": True, "dot": True, "move": "中毒",
                                  "notes": _pfx_start["notes"]}))
            if hp <= 0:
                break
        # 自动服药：先补给再出手（血低时先吃药是当前玩家的直觉操作）
        _try_auto_pill("hp")
        _try_auto_pill("mp")
        # 随机释放「已上阵、斗气够且冷却已好」的技（Q6）
        entry, cost = _core._pick_skill(p, mp, rng, round_no=rounds, cd_state=cd_state)
        # 禁技（2026-11-19 修）：被怪挂 silence 或眩晕时本回合只能武器普攻，斗气不扣。
        if int(p_fx.get("silence", 0)) > 0 or _pfx_start["skip"]:
            entry, cost = None, 0
        m_ctx["target_hp_ratio"] = m_hp / max(1, mon["hp"])
        _ca = crit_add + (100 if (_dw["crit"] and entry is not None) else 0)
        if _ca != crit_add and dw.consume(p, "B02"):
            _dw["crit"] = 0
        hit = _core._hit(p, m_def, entry, rng, p_fx, m_fx, m_ctx,
                   crit_add=_ca, extra_effects=self_effects, atk_hit=p_hit)
        # 丹纹赐福·焚天之威：前 N 次出手伤害 ×2（命中才扣次数）
        if _dw["dmg_x2"] > 0 and hit["dmg"] > 0 and dw.consume(p, "B01"):
            hit["dmg"] *= 2; _dw["dmg_x2"] -= 1; hit["danwen_x2"] = True
        # 心火共鸣：玩家 1~2 回合增伤（承伤减免在怪击那段）。
        # 不在本处递减，放回合双方行动完，保证增减伤同进同出。
        if fortune_left > 0:
            hit["dmg"] = max(1, int(hit["dmg"] * float(cfg.FORTUNE_DMG_MULT)))
            hit["fortune"] = True
        if entry is not None:
            mp -= cost
        m_hp -= hit["dmg"]
        hit.update({
            "hp_before": int(hp_before), "mp_before": int(mp_before),
            "mp_cost": int(cost if entry is not None else 0),
            "foe_hp_before": max(0, int(foe_hp_before)),
        })
        dmg_log.append(_mark(hit, m_hp))
        # 傀儡击：玩家出手后常驻追击一次（E32/E33/E35，非概率外援）。
        # 肉身伤害不吃 mp/斗技、不吃暴击加成；用同一 mitigation 减伤；
        # log 字段齐 .get() 消费（2026-11-16 回包事故）。
        if puppet and _pup_hp > 0 and m_hp > 0:
            from . import puppet_core as pc  # 延迟导入避免与 core 环
            # 2026-11-20 第二轮：锚定玩家输出，故传入 p（与反解同一个锚）。
            # 2026-11-20 第三轮：`_pup_hp > 0` —— 被轰到报废的傀儡不能再出手
            # （原著第952处「已经不能够再用来抗敌」）。
            p_atk = _pup_atk
            p_dmg = max(1, int(p_atk * _core.mitigation(m_def)))
            p_dmg = min(p_dmg, m_hp)
            m_hp -= p_dmg
            p_entry = pc.puppet_log_entry(puppet, p_dmg, formation_n)
            if _burst and not _burst_noted:
                # 阵成高光：只在第一击插一次，不新增 log kind（回看卡白名单不动）。
                p_entry["notes"] = ["阵成，金光暂取代银芒"] + list(p_entry.get("notes") or [])
                _burst_noted = True
            dmg_log.append(_mark(p_entry, m_hp))
        # 吸血：异火被动 + 技能自带（只回血，不改伤害主值）× 塔禁疗倍率
        # 减疗（2026-11-19 修）：对手挂到 p_fx 的 debuff_heal 压低本次回血。
        heal_frac = (lifesteal + float(hit.get("heal_ratio", 0))) * heal_mult * fx.heal_mult(p_fx)
        if heal_frac > 0:
            hp = min(st["体力"], hp + int(hit["dmg"] * heal_frac))
        # 塔·怪物反伤：玩家造成伤害后按比例反弹（Boss 词缀）
        if reflect > 0 and hit["dmg"] > 0:
            back = max(1, int(hit["dmg"] * reflect))
            hp -= back
            taken += back
            dmg_log.append(_mark({"dmg": back, "kind": "反伤", "taken": True,
                                  "reflect": True, "move": "反伤"}, m_hp))
        # 吸血/反伤都发生在本次出手之后，回头把第一条的 hp_after 改成最终值
        hit["hp_after"] = int(hp)
        # 逐维文案并入主 hit 的 notes（主日志 kind/dmg/skill 字段一律不动）
        if _seal > 0 and not _seal_noted:  # 封印咒首击可见，只插一次
            hit["notes"] = ["蛇之封印咒，敌攻 -%.0f%%" % (_seal * 100)] + list(hit.get("notes") or [])
            _seal_noted = True

        if m_hp <= 0:
            break
        # 目标行动前：结算挂在怪身上的 DoT + 眩晕/定身
        start = fx.on_turn_start(m_fx, rng)
        if start["dot_dmg"]:
            m_hp -= start["dot_dmg"]
            dmg_log.append(_mark({"dmg": start["dot_dmg"], "kind": "持续伤害", "dot": True,
                                  "move": "持续伤害", "notes": start["notes"]}, m_hp))
        if m_hp <= 0:
            break
        # 怪反击（被眩晕/定身则跳过）
        if not start["skip"]:
            # 怪物失误：原著「脚掌一崴，一屁股坐在地上」（第191章）/
            # 「手忙脚乱，自乱阵脚」（第601章）。本回合怪不攻击。
            mishap = float(cfg.MISHAP_CHANCE)
            # 档位判定统一走 mon_tier：塔/周本的 kind 是 "塔·elite"/"周本·elite"，
            # 旧版 `"精英" in _kind` 永远 False → 精英层失误率没减半。
            if mon.get("is_boss") or _core.mon_tier(_kind) in ("elite", "boss"):
                mishap *= float(cfg.MISHAP_BOSS_FACTOR)
            if rng() < mishap:
                _why = ("脚掌一崴，一屁股坐在地上" if "魔兽" in _kind
                        else "手忙脚乱，自乱阵脚")
                # move 只放短标签（战报一行要读得下去），完整描写进 notes
                dmg_log.append(_mark({"dmg": 0, "kind": "失误", "mishap": True,
                                      "move": "踉跄", "notes": [_why]}))
            elif rng() * 100 < dodge:
                dmg_log.append(_mark({"dmg": 0, "kind": "闪避", "miss": True,
                                      "move": "闪避"}))
            else:
                roll = 1.0 - var + 2.0 * var * rng()
                # 回看卡「承伤构成」：atk_raw = 防御前原始，dealt0 = 防御后/技能前。
                # 三个字段只供展示，不参与任何数值计算（旧行为逐字节不变）。
                atk_raw = max(1, int(m_atk * roll))
                # 降攻效果（2026-11-19 修）：玩家技挂 debuff_atk 到 m_fx，
                # 怪实际出手前按 target_atk_mult 打折。无降攻时系数=1，逐字节不变。
                m_atk_eff = m_atk * fx.target_atk_mult(m_fx)
                dealt_atk_raw = max(1, int(m_atk_eff * roll))
                # 根基裁定（隐形）：虚浮者防御同样打折 → 同怪受到更多伤害。
                # PVE 玩家承伤处；root=1（适量/正常修炼）时逐字节不变。
                dealt = max(1, int(m_atk_eff * _core.mitigation(st["防御"] * root) * roll))
                dealt0 = dealt
                # 怪物技能（2026-11-19）：概率施放，只作用于本回合，不改面板。
                # 效果一律并入「兽击」那一条 log 的 notes —— 不额外插 dmg=0 的条目，
                # 否则回看卡会把一次攻击渲染成两行、并把总量算重。
                sk_note, sk_name = [], ""
                _tier = "normal"
                # 禁技时不出手；四技能每回合一次权重选择，旧 skill 路径保留。
                sk = None
                _sk_hit = False
                if int(m_fx.get("silence", 0)) <= 0:
                    if _boss_skills:
                        _boss_ult_cd = max(0, int(_boss_ult_cd) - 1)
                        _pool, _cum = [], 0.0
                        for _cand in _boss_skills:
                            if bool(_cand.get("ultimate")) and (
                                    rounds < int(cfg.BOSS_ULTIMATE_MIN_ROUND)
                                    or _boss_ult_cd > 0):
                                continue
                            _raw_chance = _cand.get("chance")
                            _p = float(cfg.MONSTER_SKILL_BASE_CHANCE
                                       if _raw_chance is None else _raw_chance)
                            if mon.get("is_boss"):
                                _p *= 2.0
                            elif _core.mon_tier(_kind) == "elite":
                                _p *= 1.5
                            _p = min(1.0, _p)
                            if _p <= 0:
                                continue
                            _cum += _p
                            _pool.append((_cum, _cand))
                        _cap = min(float(cfg.BOSS_SKILL_MAX_CHANCE), _cum)
                        _roll = rng()
                        if _pool and _cap > 0 and _roll < _cap:
                            _scaled = _roll * _cum / _cap  # 按原权重缩放到 cap
                            for _lim, _cand in _pool:
                                if _scaled < _lim:
                                    sk = _cand
                                    break
                            if sk is not None:
                                _sk_hit = True
                                _tier = "ultimate" if bool(sk.get("ultimate")) else "normal"
                                if _tier == "ultimate":
                                    _boss_ult_cd = int(
                                        sk.get("cooldown") or cfg.BOSS_ULTIMATE_COOLDOWN)
                    else:
                        sk = mon.get("skill")
                        if sk:
                            _raw_chance = sk.get("chance")
                            _sc = float(cfg.MONSTER_SKILL_BASE_CHANCE
                                        if _raw_chance is None else _raw_chance)
                            if mon.get("is_boss"):
                                _sc *= 2.0
                            elif _core.mon_tier(_kind) == "elite":
                                _sc *= 1.5
                            if rng() < _sc:
                                _sk_hit = True
                if _sk_hit and sk:
                    _se, _sm = str(sk.get("effect") or ""), float(sk.get("mult") or 0)
                    sk_name = str(sk.get("name") or "怪技")
                    _disp = ("【大招】" if _tier == "ultimate" else "") + sk_name
                    if _se == "burst":
                        dealt = max(1, int(dealt * _sm))
                        sk_note.append("%s！伤害×%.1f" % (_disp, _sm))
                    elif _se == "drain":
                        _drain = max(1, int(_core.mp_max(p) * _sm))
                        mp = max(0, mp - _drain)
                        sk_note.append("%s！斗气被夺 -%d" % (_disp, _drain))
                    elif _se == "poison":
                        # 2026-11-20 修真 bug：旧版只算 poison_dmg 写进 notes，
                        # 从没挂到 p_fx["dot"] —— 玩家根本不掉血，是假效果。
                        # 现在真挂 DoT：base=本次毒伤，v=1.0，两回合各扣一次。
                        # ⚠️ 只走 p_fx 一条路：旧 poison_turns 计数器会与 DoT
                        # 叠加成 4 次扣血，已整段删除（见 on_turn_start 调用处）。
                        poison_dmg = max(1, int(dealt * _sm))
                        p_fx["dot"].append({"v": 1.0, "d": 2,
                                            "kind": sk_name, "base": poison_dmg})
                        sk_note.append("%s！中毒 %d×2 回合" % (_disp, poison_dmg))
                    else:
                        dealt = max(1, int(dealt * max(1.0, _sm)))
                        sk_note.append(_disp)
                    # 命中滚原著台词；四技能只用技能自带 mech，旧路径用角色包 mech。
                    if _character:
                        _quotes = tuple(_character.get("quotes") or ())
                        if _quotes:
                            _q = _quotes[int(rng() * len(_quotes)) % len(_quotes)]
                            sk_note.append("「%s」" % _q)
                        _mech = sk.get("mech") if _boss_skills else _character.get("mech")
                        if _mech:
                            _mn = str(_mech.get("kind") or sk_name)
                            _mech_eff = dict(_mech)
                            _mech_eff["t"] = _mech.get("t") or "debuff_atk"
                            if _mech_eff["t"] == "reflect":
                                # reflect 写 self_state：self=p_fx 才能在受击侧读到。
                                fx.on_hit([_mech_eff], p_fx, p_fx, dealt, rng,
                                          ctx={"target_flags": set(), "target_hp_ratio": 0.5})
                            else:
                                fx.on_hit([_mech_eff], m_fx, p_fx, dealt, rng,
                                          ctx={"target_flags": set(), "target_hp_ratio": 0.5})
                            sk_note.append("%s！" % _mn)
                # 怪击暴击（2026-11-20 隐藏面板）：与伤害浮动**共用同一次采样**。
                # roll 是那次采样的单调变换，判「roll 落在区间顶端 2·var·c 内」
                # 等价于独立掷一次暴击（概率精确等于 c），却不多消耗一次 rng ——
                # 既有逐招快照的随机序列不被打乱（test_battle_review 211 条）。
                # ⚠️ MON_DMG_VAR=0 时没有浮动也就没有暴击（同一次采样的必然结果）。
                _m_crit = (m_crit > 0 and var > 0
                           and roll > 1.0 + var - 2.0 * var * min(1.0, m_crit / 100.0))
                if _m_crit:
                    dealt = max(1, int(dealt * 1.5))
                # 心火共鸣：承伤减免（原著「短暂晕眩足以令战局顷刻间转变」）
                if fortune_left > 0:
                    dealt = max(1, int(dealt * float(cfg.FORTUNE_TAKEN_MULT)))
                    sk_note.append("心火护体，伤害减轻")
                # 玩家侧减伤/闪避/反伤（卸力水镜/雷蝠天翼/燕返击）
                # 命中对冲：怪物的命中同样压玩家的**临时**闪避（技能给的身法），
                # 否则「命中」只对基础闪避生效，留一个白嫖洞。
                inc = fx.on_incoming(p_fx, dealt, rng, hit=m_hit / 100.0)
                # 傀儡护主（2026-11-20 第三轮）：只挡**大额**伤害，且挡下后玩家仍吃不
                # 低于 PUPPET_GUARD_LEAK 的漏伤 —— 挡伤是减伤不是免疫，不抢主角风头。
                # 🚨 rng 只在 `if puppet` 分支内消耗：不带傀儡的战斗随机序列逐字节不变。
                # 用**实收**（inc["final"]）判大额，不用原始 dealt：玩家自己闪掉/减掉的
                # 部分不需要傀儡再拿肉身去填（否则傀儡白挨，观感更假）。
                _guard_soak = 0
                _guard_take = 0
                # P0-2：有灵＝主动拦截，小额伤害也进判定，且自身残血不打折
                # （原著「北王」心念一动便出手、悍不畏死拖住魂焱）。
                # 🚨 概率上限 GUARD_CAP/LEAK 不变，仍不抢玩家风头；rng 只在这一条分支消耗。
                # 门槛已由 guard_profile() 按有灵状态算好（唯一口径，卡面同源）。
                if (puppet and _pup_hp > 0
                        and int(inc["final"]) >= int(_pup_prof["threshold"])):
                    _gch = float(_pup_prof["chance"])
                    if (not _pup_prof.get("awakened")
                            and _pup_hp < float(_pup_prof["hp_max"]) * float(cfg.PUPPET_GUARD_HURT_AT)):
                        # 无灵傀儡快报废时反而护不住（原著第952处：频临报废就不能再抗敌）
                        _gch *= float(cfg.PUPPET_GUARD_HURT_MULT)
                    if rng() < _gch:
                        _g_raw = int(inc["final"])
                        _g_mig = _core.mitigation(int(_pup_prof["armor"]))
                        _g_leak = max(1, int(_g_raw * float(_pup_prof["leak"])))
                        # 傀儡倾尽剩余气血能吸收的原始伤害量（护甲越高吸得越多）。
                        # 🚨 上限必须走**浮点**：初版写 int(_pup_hp / _g_mig) 再
                        # int(soak×mig)，两步向下取整让「刚好吸满」永远差 1 点 ——
                        # 实测 lv60 人级挡到 hp_left=1 就再也不动，`_pup_hp > 0`
                        # 这道「报废即停手」的门形同虚设，傀儡会以 1 点血无限挡下去。
                        _g_cap = float(_pup_hp) / max(1e-6, _g_mig)
                        _g_soak = int(min(float(max(0, _g_raw - _g_leak)), _g_cap))
                        if _g_soak > 0:
                            # ceil：吸满时正好把最后 1 点血扣掉（宁可多扣 1 点，
                            # 也不能出现「余 1 血」这种既不死也不活的僵尸态）。
                            _g_take = min(_pup_hp,
                                          max(1, int(math.ceil(_g_soak * _g_mig))))
                            _pup_hp -= _g_take
                            _guard_take = _g_take
                            _guard_soak = _g_soak
                            _guard_n += 1
                            _guard_soak_sum += _g_soak
                            _guard_taken_sum += _guard_take
                            inc["final"] = max(1, _g_raw - _g_soak)
                hp -= inc["final"]
                taken += inc["final"]
                log_entry = {"dmg": inc["final"], "kind": "兽击", "taken": True,
                             "move": "兽击",
                             # 怪击暴击：回看卡词条行据此打「暴击」标记，
                             # 解释「实收 > 面板−防御」的缺口（纯展示字段）。
                             "crit": bool(_m_crit),
                             # 回看卡「承伤构成」：原始 → 防御削减 → 减伤吸收 → 实际。
                             # 纯展示字段，不参与结算（旧行为逐字节不变）。
                             "atk_raw": int(dealt_atk_raw),
                             "def_cut": max(0, int(dealt_atk_raw - dealt0)),
                             "absorbed": max(0, int(dealt - inc["final"] - _guard_soak)),
                             # 降攻生效展示：原始攻击被降攻压低（纯展示，不参与结算）
                             "atk_debuff": max(0, atk_raw - dealt_atk_raw)}
                # 傀儡护主（纯展示字段，结算已在上面做完）：让玩家看清「这一下本来
                # 要打我多少、傀儡替我扛了多少、它自己掉了多少血」——三个数缺一个就是
                # 隐式数值，玩家会以为挡伤是玄学。
                if _guard_soak > 0:
                    log_entry["guard_raw"] = int(_g_raw)
                    log_entry["guard_soak"] = int(_guard_soak)
                    log_entry["guard_taken"] = int(_guard_take)
                    log_entry["guard_hp_left"] = max(0, int(_pup_hp))
                    log_entry["guard_hp_max"] = int(_pup_prof["hp_max"])
                    log_entry["puppet_guard"] = True
                notes = list(sk_note) + list(inc["notes"] or [])
                if _guard_soak > 0:
                    from . import puppet_core as _pcg
                    # 🚨 必须插到**最前面**：回看卡的逐招明细只取 notes[:TAG_LIMIT]（4 条），
                    # 而 sk_note 里可能已经装了「技能名 + 原著台词 + 机制」三条，append
                    # 到末尾会被静默截掉 —— 玩家一辈子看不见护主，等于机制没做。
                    notes.insert(0, _pcg.guard_note(puppet, _g_raw, _guard_soak,
                                                    _guard_take, _pup_hp,
                                                    int(_pup_prof["hp_max"]),
                                                    awakened=bool(_pup_prof.get("awakened"))))
                if notes:
                    log_entry["notes"] = notes
                if sk_name:
                    log_entry["monster_skill"] = sk_name
                    # 2026-11-23 四技能：只进战报/日志，不渲染成玩家可见元说明。
                    log_entry["monster_skill_tier"] = _tier
                if fortune_left > 0:
                    log_entry["fortune_guard"] = True
                dmg_log.append(_mark(log_entry, m_hp))
                if inc["reflect"]:
                    m_hp -= inc["reflect"]
                    dmg_log.append(_mark({"dmg": inc["reflect"], "kind": "反伤",
                                          "reflect": True, "move": "反伤"}, m_hp))
        else:
            dmg_log.append(_mark({"dmg": 0, "kind": "被控", "miss": True,
                                  "move": "被控"}))
        # 玩家福利：每场最多一次，1~2 回合的短时气机顺畅（不是永久 buff）
        if not fortune_seen and rng() < float(cfg.FORTUNE_CHANCE):
            lo, hi = cfg.FORTUNE_ROUNDS
            fortune_left = int(rng() * (int(hi) - int(lo) + 1)) + int(lo)
            fortune_seen = True
            fortune_round = rounds      # 本回合刚开始生效，不许当回合就递减
            dmg_log.append(_mark({"dmg": 0, "kind": "心火共鸣", "fortune": True,
                                  "move": "心火共鸣",
                                  "notes": ["气机顺畅，接下来 %d 回合增伤减伤" % fortune_left]}))
        # 本回合双方都行动完才递减（且发放当回合不递减），保证增伤与减伤同进同出
        if fortune_left > 0 and rounds > fortune_round:
            fortune_left -= 1
        fx.expire_turn(p_fx)
        fx.expire_turn(m_fx)

    win = m_hp <= 0
    if rounds >= int(cfg.HARD_ROUND_CAP) and m_hp > 0 and hp > 0:
        # 异常熔断（不是玩法回合上限）：按剩余血量比例判胜，绝不静默判负。
        ratio_p = hp / max(1, int(st["体力"]))
        ratio_m = m_hp / max(1, int(mon["hp"]))
        win = ratio_p > ratio_m
        exhausted = True
    # 回看卡「终局」：先留住**战斗结束时**的真实血/斗气，再改写存档。
    # 2026-11-19 用户：「回看显示 0 血量，人物卡片显示还有血量」—— 旧版回看读
    # hp_end（真值，可能 -287），人物卡读战败重置的 70%，两处对不上。现在统一：
    # 战败**不归零**，结算就是「上限 × LOSE_HP_FLOOR（5%）」，回看卡与人物卡
    # 用同一个数；`downed` 只用来挑收场文案（被打到 0 以下 = 差点没命 = 被救）。
    downed = int(hp) <= 0
    floor_hp = max(1, int(st["体力"] * cfg.LOSE_HP_FLOOR))
    hp_end = max(1, int(hp)) if win else floor_hp
    mp_end = max(0, int(mp))
    foe_hp_end = max(0, int(m_hp))
    if _burst and dmg_log:
        # 退档收场：原著「金光消散，再度化为地妖傀」。必须让玩家看见它是临时的。
        _last = dmg_log[-1]
        _last["notes"] = ["金光散，复为地妖傀"] + list(_last.get("notes") or [])
    p["hp"] = hp_end
    p["mp"] = mp   # 斗气结转：写回本场消耗后的剩余值，不再强制回满
    if downed:
        # 战败收尾：把「被打到 0 以下」那几条 log 的气血快照统一成保底值。
        # 不改就是逐招末行写「余血 0」、终局块写「余血 832」——正是用户 2026-11-19
        # 报的「两处对不上」。保底规则下，那一刻的真实剩余就是这 5%。
        for e in reversed(dmg_log):
            if e.get("hp_after") is None:
                break
            if int(e["hp_after"]) > 0:
                break
            e["hp_after"] = hp_end

    # 贵人相助：毫发无伤、1 回合速胜，胜负结算照常走下方 win 分支。
    shown_rounds = max(1, rounds)
    res = {
        "win": win, "rounds": shown_rounds, "log": dmg_log,
        "taken": taken, "mon": mon, "mon_hp_left": max(0, m_hp),
        "hero_aid": aid,  # None 或 {name,rate,luck}，文案层据此标注机缘因果
        "puppet": (str(puppet.get("name") or "傀儡") if puppet else None),
        "formation": int(formation_n or 0),
        # P0-1：本场是否真的临时升了档（纯展示，供战报/回看标记「阵成」）。
        "formation_burst": bool(_burst),
        # 2026-11-19 战斗事件（供战报/回看卡渲染；数值层不消费）
        # 终局快照：战斗结束那一刻的真实血/斗气（不是战后重置值），回看卡据此
        # 显示「气血归零而败」而不是误导性的战后血量。
        "hp_end": hp_end, "mp_end": mp_end, "foe_hp_end": foe_hp_end,
        # downed：这一场有没有被真打到气血 0 以下（判负的直接原因）。
        # 只影响收场文案（被小医仙所救 / 落荒而逃），不参与任何数值结算。
        "downed": bool(downed),
        # 对手血量上限：回看卡「终局」条要按比例画条，没有分母就画不出形变
        "foe_hp_max": max(0, int(mon.get("hp") or 0)),
        "exhausted": bool(exhausted),
        "auto_pills": {"hp": int(pill_used.get("hp", 0)),
                       "mp": int(pill_used.get("mp", 0))},
        "fortune": bool(fortune_seen),
        # 原著「战斗中夺取火能」：由命令层写进 alchemy_tower 表（真实列）。
        "fire_energy_gain": max(1, int(mon.get("lv", 1)) // int(cfg.TOWER_ENERGY_PER_LV)),
    }
    # 护主战果（只在**真的带了傀儡**时才挂这个键）：不带傀儡时 res 的键集与旧版
    # 逐字节相同 —— 否则所有断言 res 结构的回看/结算测试会一起红。
    if puppet:
        res["puppet_guard"] = {
            # P1-1：信号自带真实库行 id（formation_burst 浅拷贝保留 id），
            # 命令层据此写回 damaged，绝不用 active_id 另查避免升档副本错位。
            "id": int(puppet.get("id") or 0),
            "count": int(_guard_n),          # 本场替主挡下的次数
            "soak": int(_guard_soak_sum),    # 累计替主挡下的伤害
            "taken": int(_guard_taken_sum),  # 傀儡自身累计受损
            "hp_left": max(0, int(_pup_hp)),
            "hp_max": int(_pup_prof["hp_max"]),
            # 傀儡被打到不能再战（原著第952处「不能够再用来抗敌」）
            "broken": bool(_pup_hp <= 0),
        }
    if win:
        p["wins"] = int(p.get("wins", 0)) + 1
        # 经验平衡：遭遇战传 exp_override=整场预算的单敌份额，
        # 由 encounter_exp_share 算出（已含异火，不再二次乘）。None 时走原单敌口径
        # （遗迹连段 / PVP / 直接单敌调用），逐字节不变。
        if exp_override is None:
            exp = int(mon["exp"] * _core.flame_exp_mult(p))
        else:
            exp = max(0, int(exp_override))
        p["exp"] = int(p.get("exp", 0)) + exp
        gold = int(mon["gold"] * (cfg.DROP_GOLD_MULT[0] + rng()))
        p["gold"] = int(p.get("gold", 0)) + gold
        # 魂殿吞噬：仅触发时挂 res 键，非魂殿字节不变
        _devour = _core.devour_of(p, mon)
        if _devour > 0:
            p["exp"] = int(p.get("exp", 0)) + _devour
            p["pill_exp"] = int(p.get("pill_exp", 0)) + _devour
        # 傀儡专用材料单独收集（真实列 puppet_materials，不进 bag）：
        # rng 调用全部追加在既有判定之后，老路径掉落序列逐字节不变。
        _pup_drops: list = []
        res.update(exp=exp, gold=gold,
                   drops=_core._roll_drops(p, rng, mon, _pup_drops))
        if _devour > 0:
            res["devour"] = _devour
        if _pup_drops:
            res["puppet_drops"] = _pup_drops
    else:
        p["losses"] = int(p.get("losses", 0)) + 1
        # 气血已在上面统一成「上限 × LOSE_HP_FLOOR」，这里不再二次改写：
        # 回看卡的终局条与人物卡必须是同一个数（2026-11-19 用户指出两处不一致）。
        res["hp_after"] = p["hp"]
    return res

