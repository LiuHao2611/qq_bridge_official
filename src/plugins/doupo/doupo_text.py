# -*- coding: utf-8 -*-
"""斗破 · 玩家可见文案池（2026-11-23）。

## 为什么集中在这里
玩家可见文案以前散在 commands.py / core.py 的几十个 f-string 里，想统一换皮只能满仓
grep，改完还容易漏。现在把「会被玩家读到的句子」集中到本模块，调用点只负责传数据，
好处有三：① 一处改全量生效；② 守门测试一处扫全量禁词；③ 你想自己改句子不用碰逻辑。

## 硬纪律（2026-11-23 用户拍板）
1. 玩家可见零元说明：不出现考据/出处/原创声明。
2. 原著角色说话只用已逐字核对的原文（在 config.BREAK_BOSSES.quotes 等池子里），
   本模块新写的句子**一律是旁白/叙述**，不给角色加引号台词。
3. 不出现快照战力数字、不出现「心魔劫影」。
4. 只改文案，不动任何数值字段与概率。
5. 保留现有测试锚点词（突破成功/境界突破/修为不足/遭遇/丹成/炼制失败/材料不足/
   丹炉尚温/强化成功/晋为/夺得/服下 等），避免把功能守门误伤成回归。
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# 一、突破 / 破境
# ---------------------------------------------------------------------------

#: 跨大境界的破境旁白。key = cfg.realm_name(new_lv)，{new} 由调用点填完整称呼。
CROSS_LINES: dict[str, str] = {
    "斗之气": "气机在经脉里反复冲撞，气旋仍未成形——你还在等那一线灵光。",
    "斗者": "气机沉入丹田，一缕气旋缓缓成形——你，终于成了斗者。",
    "斗师": "气旋翻涌，斗气由气凝液，一层薄纱自体表浮起——{new}。",
    "大斗师": "液态斗气凝成棱形斗晶，铠甲自肤下升起——{new}。",
    "斗灵": "斗晶生刺，一柄斗气凝成的兵刃落在掌心——{new}。",
    "斗王": "斗气化翼，双翼乍展，你第一次离地三尺——{new}。",
    "斗皇": "天地间同属性能量奔涌入体，足下虚空微微下陷——{new}。",
    "斗宗": "一步踏虚，脚下空间泛起涟漪——{new}。",
    "斗尊": "斗气凝晶，空间随手而裂——{new}。",
    "斗尊巅峰": "斗气几近实质，一呼一吸之间空间随之明灭——{new}。",
    "半圣": "半步跨过生死界限，天地法则已在身侧低鸣——{new}。",
    "斗圣": "拳可裂天，脚可碎地；一方空间自虚无中为你洞开——{new}。",
    "斗帝": "帝炎自燃，血脉蜕变，天地俯首——{new}。",
}

#: 同境界内升星旁白。比跨境界轻一档，避免每升一星都像「重新破境」。
STAR_LINES: dict[str, str] = {
    "斗之气": "气息在经脉里又凝实一分，段位再进——{new}。",
    "斗者": "气旋一转，斗气更凝——{new}。",
    "斗师": "斗气纱衣又厚了一层——{new}。",
    "大斗师": "斗晶上多出一道棱面，铠甲随之收紧——{new}。",
    "斗灵": "掌中兵刃嗡然一震，斗气又凝三分——{new}。",
    "斗王": "斗翼舒展，离地更近一丈——{new}。",
    "斗皇": "周身气流俯首，足下虚空再陷一寸——{new}。",
    "斗宗": "一步落下，空间涟漪比先前更远——{new}。",
    "斗尊": "掌心开合，空间裂痕随念而生——{new}。",
    "斗尊巅峰": "斗气将凝未凝，已能听见天地低鸣——{new}。",
    "半圣": "圣意又凝一分，半步之隔愈浅——{new}。",
    "斗圣": "一拳未出，远处空间已然皱起——{new}。",
    "斗帝": "帝炎不灭，万火皆听号令——{new}。",
}


def break_result(old_name: str, new_name: str, new_realm: str, cross: bool) -> str:
    """普通突破回包。保留「突破成功/境界突破」锚点，正文按境界分档。"""
    lead = "境界突破" if cross else "突破成功"
    pool = CROSS_LINES if cross else STAR_LINES
    body = pool.get(new_realm) or pool["斗者"]
    return (f"✨ {lead}！由【{old_name}】晋入【{new_name}】。\n"
            f"　{body.format(new=new_name)}　气血斗气已满。")


def need_exp(need: str) -> str:
    """修为不足。保留锚点「修为不足」+ 修炼入口。"""
    return (f"修为不足，气旋尚未圆满——距下一星还差 {need} 道修为。"
            "去「战斗」或「修炼」积蓄。")


def boss_fail(rounds: int, hp_end: str) -> str:
    return (f"　鏖战 {rounds} 回合，斗气不济而败（余血 {hp_end}）。\n"
            "　修为未散，调息再来——「突破」可再战此劫。")


def boss_win(rounds: int, name: str) -> str:
    return f"　鏖战 {rounds} 回合，【{name}】终于溃散。"


def flame_drop(name: str, kind: str, duplicate: bool, dross: int | str) -> str:
    if duplicate:
        return (f"　劫后余烬里又见【{name}】，此火已在你身，"
                f"化作药渣 ×{dross}。")
    return (f"　溃散处遗下一缕【{name}】（{kind}），火苗尚在颤动——"
            "已收入囊中。")


def tiandi_success() -> str:
    return "🔥 你踏碎最后一层壁障，成就【斗帝】！帝炎自燃，机缘全满。"


def tiandi_fail(rate: int) -> str:
    return (f"💥 冲击斗帝未成（成功率 {rate}%）——帝路最后一关未过，"
            "斗气倒灌经脉，修为折损。多积机缘再来。")


def tiandi_fail_plain(rate: int) -> str:
    return (f"💥 冲击斗帝失败（成功率 {rate}%）！\n"
            "　帝路最后一关未过，斗气倒灌经脉，修为折损——机缘不满，大器难成。\n"
            "　多积机缘（支线任务 / 九幽散）再来。")


# ---------------------------------------------------------------------------
# 二、遭遇 / 战斗播报
# ---------------------------------------------------------------------------

def encounter_head(n: int, foes: str, rounds: int, win: bool, killed: int) -> str:
    """战斗首行。保留「遭遇」锚点，补一句临场感。"""
    verdict = "大捷" if win else f"力竭·斩{killed}/{n}"
    return (f"⚔️ 遭遇 {n} 敌——{foes}拦在去路上。\n"
            f"　{rounds} 回合，{verdict}。")


def battle_spot(spot: str) -> str:
    """把 commands._battle_line 剪出的高光整段包装成世界观叙述。

    spot 形如「佛怒火莲 6240（力量·斗气）」；拆不出伤害时原样包住，
    绝不猜数值。
    """
    import re
    m = re.match(r"^(.+?)\s+(\d+(?:\.\d+)?[万亿]?)\s*(?:（.*）)?$", spot)
    if m:
        return f"💥 这一记【{m.group(1).strip()}】最重——{m.group(2)} 点伤害落在要害。"
    return f"💥 最重的一记——{spot}。"


def battle_gain(foes: str, exp: str, gold: str, drops: str = "") -> str:
    out = f"🎯 此战斩获 {foes} · 修为 +{exp} · 金币 +{gold}"
    return out + (" · " + drops if drops else "")


def battle_cost(taken: str, hp: str, pills: int = 0) -> str:
    out = f"🩸 代价 防御减伤后受伤 {taken} · 气血 {hp}"
    return out + (f" · 服药 {pills}" if pills else "")


def battle_hero_aid() -> str:
    return "🍀 机缘际会，贵人破空而至，一招制敌——此战毫发无伤"


def battle_ready(realm: str) -> str:
    return f"⬆️ 气机已满（{realm}），发送「突破」冲击下一境"


def battle_cd(left: int) -> str:
    return f"气血未复，需调息 {left} 秒。"


# ---------------------------------------------------------------------------
# 三、炼药
# ---------------------------------------------------------------------------

#: 按丹品分档的开炉旁白。key = cfg.RECIPE_BY_NAME[name]["grade"]。
CRAFT_SUCCESS: dict[str, str] = {
    "一品": "丹香初起，【{name}】在炉中成形——药力虽薄，却是你一炉一炉炼出来的。",
    "二品": "丹香初起，【{name}】在炉中成形——药力虽薄，却是你一炉一炉炼出来的。",
    "三品": "丹香初起，【{name}】在炉中成形——药力虽薄，却是你一炉一炉炼出来的。",
    "四品": "丹香四溢，炉盖一掀便扑了满脸——【{name}】成。",
    "五品": "丹成的刹那，虚空荡开一圈实质涟漪——【{name}】。",
    "六品": "炉中异动引得天地变色，怕是已有强者循香而来——【{name}】成。",
    "七品": "丹雷炸响，【{name}】在雷光里挣了一下，终究没逃出手心。",
    "八品": "丹雷滚滚，【{name}】几欲破炉，被你灵魂死死锁回。",
    "九品": "丹雨倾盆，【{name}】已具灵智，俯首归主。",
    "帝品": "丹雨倾盆，【{name}】已具灵智，俯首归主。",
}

#: 失败旁白池。按 seed 取一条，避免每次失败都同一句。
CRAFT_FAIL_LINES: tuple[str, ...] = (
    "火候差了一线，整炉药材尽化飞灰——只余药渣 ×{dross}。",
    "药香骤散，炉中只余焦黑残渣——药渣 ×{dross}，尚可养火。",
    "炉火忽地一沉，药力尽数散尽——只余药渣 ×{dross}。",
)


def craft_success(name: str, grade: str, rate_cn: str) -> str:
    body = CRAFT_SUCCESS.get(grade) or CRAFT_SUCCESS["四品"]
    return f"⚗️ 丹成！{body.format(name=name)}（{rate_cn}把握）"


def craft_fail(dross: int | str, rate_cn: str, over_hint: str, seed: int = 0) -> str:
    body = CRAFT_FAIL_LINES[seed % len(CRAFT_FAIL_LINES)].format(dross=dross)
    return f"💨 炼制失败（{rate_cn}把握）——\n{over_hint}　{body}"


def craft_fail_plain() -> str:
    return "炼制失败。"


def craft_cd(left: int) -> str:
    return f"丹炉尚温，炉火未熄，丹香尚在——再候 {left} 秒开炉。"


def craft_mat(missing: str) -> str:
    return f"❌ 材料不足：炉火已起，药材却凑不齐——缺 {missing}。\n　去「宝库」买药。"


def craft_mp() -> str:
    return "❌ 未能开炉：斗气不足以续火，炉温渐降——先调息回气。"


def craft_generic_fail() -> str:
    return "❌ 未能开炉：炉中火候难成——先看「炼药」丹方，再备齐药材与丹方。"


def craft_over(diff: int) -> str:
    if diff == 1:
        return "　⚠ 越1档勉强开炉（药力未熟，成功率骤降）\n"
    return f"　⚠ 越{diff}档——几无可能，仅可勉强尝试\n"


def alch_up(name: str) -> str:
    return f"　火候掌控又一分精进，晋为【{name}炼药师】！"


def craft_batch_head(name: str, done: int, ok: int, fail: int, rate_cn: str) -> str:
    return f"⚗️ 连炼【{name}】{done}炉　成{ok}·败{fail}（{rate_cn}把握/炉）"


def craft_batch_detail(ok: int, dross: int, exp: int) -> str:
    return f"　丹 +{ok}　药渣 +{dross}　炼药经验 +{exp}"


def craft_batch_limited(kind: str) -> str:
    if kind == "mat":
        return "　⚠ 药材见底，余炉未炼"
    return "　⚠ 斗气耗尽，提前收炉"


# ---------------------------------------------------------------------------
# 四、服丹 / 破境丹（core.py 调用）
# ---------------------------------------------------------------------------

def pill_break(realm: str, full: str) -> str:
    return f"丹力冲关，壁障寸寸碎裂——晋入【{full}】，气血斗气已满。"


def pill_star(full: str) -> str:
    return f"药力化开，硬生生拔起一星——晋入【{full}】。"


def pill_no_star(val: int) -> str:
    return f"药力在经脉里打了个转，星位未动（无药效反噬），余力化作修为 +{val}。"


def pill_resist(realm: str, max_per: int) -> str:
    return (f"药体已生抗性，同阶再服只是白费——【{realm}】这一阶"
            f"至多服 {max_per} 枚见效，晋阶后可再服。")


def pill_wrong_realm(realm: str, val: int) -> str:
    return f"此丹专为破【{realm}】而炼，当前境界药力不显，化作修为 +{val}。"


def pill_gate(val: int) -> str:
    """九星位不跨大境界（2026-11-23）：境界关口须自己打突破战，丹药只到九星为止。"""
    return (f"药力已到九星极处，大境界的壁障得靠你自己撞——"
            f"发「突破」迎战守关者。余力化作修为 +{val}。")


# ---------------------------------------------------------------------------
# 五、长尾高频口令（第三批）
# ---------------------------------------------------------------------------

def cultivate(minutes: int, exp: str) -> str:
    return f"🧘 吐纳 {minutes} 分钟，气机渐稳，修为 +{exp}。"


def rest_tick(hp: str, mp: str) -> str:
    return f"🌿 调息之间，气血 +{hp}、斗气 +{mp}。"


def feed_flame(name: str, lv: int) -> str:
    return f"🔥 药渣被【{name}】一口吞尽，火势暴涨——晋为 {lv} 级！"


def feed_flame_partial(name: str, give: int, need: str) -> str:
    return f"🔥 投入 {give} 份药渣，【{name}】火势又涨一分，距下一级还差 {need}。"


def flame_full(name: str, lv: int) -> str:
    return f"【{name}】已至 {lv} 级圆满，再多药渣也烧不动了。"


def flame_capture(name: str) -> str:
    return f"🔥 那簇火在你掌中几番暴走，终被你以意志压住——【{name}】认主。"


def enhance_ok() -> str:
    return "✅ 锤落火起，玄重尺的纹路又亮了一分——强化成功！"


def sect_change(name: str) -> str:
    return f"🏯 已转投【{name}】——山门易主，初心未改。"


def rob_win(gold: str) -> str:
    return f"😈 得手，夺得 {gold} 金币。"


def give_ok(amount: str = "", item: str = "") -> str:
    if item:
        return f"🎁 你赠予对方【{item}】，善缘结下，来日必有回响。"
    return f"🎁 你赠予对方 {amount} 金币，善缘结下，来日必有回响。"


def tower_down(floor: int) -> str:
    return f"💀 力竭倒下，止步第 {floor} 层。"


def weekly_clear() -> str:
    return "🏆 七层通关！结算到账——此行不虚。"


def battle_tip(win: bool) -> str:
    if win:
        return "发「战斗评价」听一句点评 · 只保留最后一场战斗"
    return "先「服丹」回血再战 · 只保留最后一场战斗"


#: 所有「不含 {动态占位}」的玩家可见静态句，守门测试直接扫这些。
#: 含占位的模板由 CALL_SAMPLES 展开后一起扫。
STATIC_TEXTS: tuple[str, ...] = (
    "修为不足，气旋尚未圆满——距下一星还差 {need} 道修为。去「战斗」或「修炼」积蓄。",
    "　修为未散，调息再来——「突破」可再战此劫。",
    "🔥 你踏碎最后一层壁障，成就【斗帝】！帝炎自燃，机缘全满。",
    "气血未复，需调息 {left} 秒。",
    "❌ 未能开炉：斗气不足以续火，炉温渐降——先调息回气。",
    "　火候掌控又一分精进，晋为【{name}炼药师】！",
    "　⚠ 药材见底，余炉未炼",
    "　⚠ 斗气耗尽，提前收炉",
    "🏯 已转投【{name}】——山门易主，初心未改。",
    "🎁 善缘结下，来日必有回响。",
    "💀 力竭倒下，止步第 {floor} 层。",
    "🏆 七层通关！结算到账——此行不虚。",
    "发「战斗评价」听一句点评 · 只保留最后一场战斗",
    "先「服丹」回血再战 · 只保留最后一场战斗",
)

#: 守门测试用的展开样本：把每个带占位的模板填成玩家真的会看到的样子。
CALL_SAMPLES: tuple[str, ...] = (
    break_result("三星斗者", "四星斗者", "斗者", False),
    break_result("九星斗者", "一星斗师", "斗师", True),
    need_exp("一百"),
    boss_fail(7, "两千"),
    boss_win(9, "药老·药尘"),
    flame_drop("青莲地心火", "异火", False, 30),
    flame_drop("青莲地心火", "异火", True, 30),
    tiandi_success(),
    tiandi_fail(35),
    tiandi_fail_plain(35),
    encounter_head(3, "血灵蟒等3名", 6, True, 3),
    encounter_head(3, "血灵蟒等3名", 6, False, 1),
    battle_spot("佛怒火莲 6240（力量·斗气）"),
    battle_spot("身形一晃，避开了兽击（敏捷·闪避）"),
    battle_gain("血灵蟒", "一千八", "九百", "血灵草×2"),
    battle_cost("三千", "一万二", 2),
    battle_hero_aid(),
    battle_ready("四星斗者"),
    battle_cd(20),
    craft_success("回灵丹", "四品", "十成"),
    craft_success("九幽散", "帝品", "八成"),
    craft_fail(3, "十成", "", 0),
    craft_fail(3, "十成", "　⚠ 越2档——几无可能，仅可勉强尝试\n", 1),
    craft_fail_plain(),
    craft_generic_fail(),
    craft_cd(42),
    craft_mat("血灵草、紫叶兰"),
    craft_mp(),
    craft_over(2),
    alch_up("五品"),
    craft_batch_head("回灵丹", 5, 3, 2, "十成"),
    craft_batch_detail(3, 6, 180),
    craft_batch_limited("mat"),
    pill_break("大斗师", "一星大斗师"),
    pill_star("二星大斗师"),
    pill_no_star(3000),
    pill_resist("大斗师", 1),
    pill_wrong_realm("斗宗", 5000),
    cultivate(30, "五百"),
    rest_tick("一千二", "三百"),
    feed_flame("青莲地心火", 2),
    feed_flame_partial("青莲地心火", 10, "三十"),
    flame_full("青莲地心火", 4),
    flame_capture("青莲地心火"),
    enhance_ok(),
    sect_change("天府联盟"),
    rob_win("八百"),
    give_ok("八百"),
    give_ok(item="血灵丹"),
    tower_down(17),
    weekly_clear(),
    battle_tip(True),
    battle_tip(False),
)


def all_player_texts() -> tuple[str, ...]:
    """守门测试用：全部玩家可见静态句 + 展开后的模板样本。"""
    out = list(STATIC_TEXTS) + list(CALL_SAMPLES)
    for obj in (CROSS_LINES, STAR_LINES, CRAFT_SUCCESS):
        out.extend(str(x) for x in obj.values())
    out.extend(CRAFT_FAIL_LINES)
    return tuple(dict.fromkeys(out))
