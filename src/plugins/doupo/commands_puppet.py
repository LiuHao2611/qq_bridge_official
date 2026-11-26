# -*- coding: utf-8 -*-
"""斗破苍穹 · 傀儡命令组（2026-11-25 从 commands.py 拆出）。

为什么独立成模块
----------------
``commands.py`` 逼近项目的 **200KB 上传红线**（204800 B，pre-commit 按 HEAD
判定）。本组 11 个函数、687 行、约 30KB，块外零调用，是最干净的切分点。

兼容性（红线）
--------------
* ``commands.py`` 末尾 re-export 同名函数 —— ``__init__.py`` 的 matcher、
  ``button_dispatch.py``、既有测试一律仍走 ``commands.cmd_*``。
* ``_player`` / ``_finalize_battle`` / ``NOT_ENTERED`` / ``DEFAULT_PUPPET_STORE``
  通过 ``_cmds.xxx`` 运行时取，循环导入下安全；不复制任何逻辑。
"""
from __future__ import annotations

import re
from datetime import date

from . import commands as _cmds
from . import config as cfg
from . import core
from .puppet_store import PUPPET_STORE as DEFAULT_PUPPET_STORE


def puppet_is_subcmd(arg) -> bool:
    """该参数走子命令（文本回执）还是「看傀儡卡」（出图）。真值见上。"""
    a = str(arg or "").strip()
    return bool(a) and a.startswith(_cmds.PUPPET_SUBCMDS)


async def cmd_puppet(store, qq, arg="", pstore=None) -> str:
    """查看傀儡卡 / 子命令：淬炼、结阵、融魂、收服。
    不拦未开通者——展示差哪道资格，作引导。
    """
    from . import puppet_core as pc
    pstore = pstore or _cmds.DEFAULT_PUPPET_STORE
    p = await _cmds._player(store, qq)
    if not p:
        return _cmds.NOT_ENTERED
    qq = str(qq)
    arg = (arg or "").strip()
    elig = await pstore.eligibility(qq)
    roster = await pstore.list_puppets(qq)
    # 「傀儡 配方」随时可看（不必先有傀儡）——多配方后玩家第一句总是「要什么料」
    if arg.startswith("配方") or arg.startswith("谱"):
        return _puppet_recipe_text(p, await pstore.materials(qq) or {})
    # 「傀儡 图鉴」= 配方的出图版；文本兜底由 cmd_puppet_codex 给，卡片由 handler 出。
    if arg.startswith("图鉴"):
        return await cmd_puppet_codex(store, qq, pstore)
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
    if arg.startswith("出战"):
        return await _cmd_puppet_deploy(store, qq, p, roster, elig, pstore, arg)
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


async def cmd_puppet_deploy(store, qq, pstore=None, arg="") -> str:
    """「傀儡出战」按钮入口：无参列出可切换清单，带序号则切换。"""
    pstore = pstore or _cmds.DEFAULT_PUPPET_STORE
    p = await _cmds._player(store, qq)
    if not p:
        return _cmds.NOT_ENTERED
    qq = str(qq)
    roster = await pstore.list_puppets(qq)
    elig = await pstore.eligibility(qq)
    return await _cmd_puppet_deploy(store, qq, p, roster, elig, pstore, arg)


async def _cmd_puppet_deploy(store, qq, p, roster, elig, pstore, arg) -> str:
    """傀儡出战（2026-11-26）：主动切换当前出战傀儡，带明确回执。

    出战此前是隐式的（入库即 active_id），玩家无从切换。本命令读独立库的
    ``list_puppets`` 序号，写 ``puppet_store.set_active`` 真实列——与战斗
    ``active_puppet()`` 同一真值，切完立刻生效（下场战斗就换人）。

    用法：``傀儡 出战`` 列清单；``傀儡 出战 2`` 或 ``傀儡 出战 <名中关键字>`` 切换。
    """
    from . import puppet_core as pc

    qq = str(qq)
    a = str(arg or "").strip()[len("出战"):].strip()
    cur = next((x for x in roster if x["id"] == elig.get("active_id")), None)
    if not roster:
        return "【傀儡出战】你尚未拥有傀儡，先发「炼制傀儡」炼一具。"
    if not a:
        lines = ["【傀儡出战】当前出战：%s" % (cur["name"] if cur else "无"),
                 "发「傀儡 出战 序号」切换（序号见下）："]
        for i, x in enumerate(roster, 1):
            tier_name, _skin = cfg.PUPPET_TIER_BY_IDX.get(
                int(x.get("tier", 0)), ("人级傀儡", "铜"))
            mark = " ★出战" if cur and x["id"] == cur["id"] else ""
            lines.append("　%d. %s（%s）· 攻 %d%s" % (
                i, x["name"], tier_name, int(pc.puppet_attack(x, p)), mark))
        return "\n".join(lines)
    # 解析目标：优先纯序号，其次名字包含
    target = None
    if a.isdigit():
        idx = int(a)
        if 1 <= idx <= len(roster):
            target = roster[idx - 1]
        else:
            return "【傀儡出战】序号超出范围（你有 %d 具，填 1~%d）。" % (len(roster), len(roster))
    else:
        target = next((x for x in roster if a in str(x.get("name") or "")), None)
        if target is None:
            return "【傀儡出战】没有名字含「%s」的傀儡，发「傀儡 出战」看清单。" % a
    if cur and target["id"] == cur["id"]:
        return "【傀儡出战】%s 已在出战中。" % target["name"]
    await pstore.set_active(qq, int(target["id"]))
    tier_name, _skin = cfg.PUPPET_TIER_BY_IDX.get(
        int(target.get("tier", 0)), ("人级傀儡", "铜"))
    return "⚔️ 傀儡出战已切换：【%s】（%s）· 肉身攻击 %d。下一场战斗即刻改用这具。" % (
        target["name"], tier_name, int(pc.puppet_attack(target, p)))


async def cmd_puppet_codex(store, qq, pstore=None, report=None) -> str:
    """傀儡图鉴（2026-11-26）：全部 18 条配方 + 玩家已有材料高亮/数量。

    与 ``傀儡 配方`` 纯文本同源（都读 cfg.PUPPET_RECIPES + pstore.materials），
    但走出图：把结构化数据写进 ``report`` 供 ``card.render_codex`` 消费。文本
    兜底也完整可读——图渲染失败时调用方仍能拿到这份清单（不静默丢内容）。
    """
    from . import puppet_core as pc

    p = await _cmds._player(store, qq)
    if not p:
        return _cmds.NOT_ENTERED
    mats = await (pstore or _cmds.DEFAULT_PUPPET_STORE).materials(str(qq)) or {}
    if report is not None:
        report["mats"] = {str(k): int(v or 0) for k, v in mats.items()}
    fg = pc.fire_grade(p)
    fire_word = {1: "兽火", 2: "异火", 3: "高阶异火"}.get(int(fg["grade"]), "兽火")
    lines = ["【傀儡图鉴】共 %d 条 · 你的炼制之火：%s（%s）"
             % (len(cfg.PUPPET_RECIPES), fire_word, fg["name"])]
    for r in cfg.PUPPET_RECIPES:
        need = pc.recipe_materials(r)
        have = [("%s %d/%d" % (n, int(mats.get(n, 0) or 0), q))
                for n, q in need.items()]
        lines.append("　%s：%s" % (r["name"], "、".join(have)))
    lines.append("发「炼制傀儡 <配方名>」开炉；齐料项在图鉴卡上以金色高亮。")
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
    pstore = pstore or _cmds.DEFAULT_PUPPET_STORE
    p = await _cmds._player(store, qq)
    if not p:
        return _cmds.NOT_ENTERED
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
    pstore = pstore or _cmds.DEFAULT_PUPPET_STORE
    p = await _cmds._player(store, qq)
    if not p:
        return _cmds.NOT_ENTERED
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
        await _cmds._finalize_battle(p, qq, res, "傀儡遗迹",
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

