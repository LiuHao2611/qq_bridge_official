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


def boss_fail(rounds: int, hp_end: str, taunt: str = "") -> str:
    """战败正文。taunt 为可选的原著嘲讽台词（2026-11-28），附在结尾。

    空串时不附加任何台词行——无原著台词的魔兽/龙套不会开口，不代编。
    """
    body = (f"　鏖战 {rounds} 回合，斗气不济而败（余血 {hp_end}）。\n"
            "　修为未散，调息再来——「突破」可再战此劫。")
    if taunt:
        body += f"\n　对手冷笑：「{taunt}」"
    return body


def boss_win(rounds: int, name: str) -> str:
    return f"　鏖战 {rounds} 回合，【{name}】终于溃散。"


def flame_drop(name: str, kind: str, duplicate: bool, dross: int | str,
               leveled: int = 0) -> str:
    if leveled:
        return (f"　劫后余烬里又见【{name}】，此火已在你身，"
                f"借机淬炼——升至 {leveled} 级。")
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

def chapter_line(ch: int, region: str, lv_lo: int, lv_hi: int = 0) -> str:
    """战斗首行上方的地点坐标行（2026-11-25 章节门禁 → 同日用户要求去掉章号）。

    为什么保留区域与等级：怪等级按章节走，同一个人在不同章遇到的怪强度差异
    很大（第 1 章 lv5、第 8 章 lv68）；不写清楚，玩家会以为是随机难度。

    🚨 2026-11-25 用户明确要求「去掉第几章节的声明」：**玩家可见的战报首行
    一律不出现章号**，只留区域名与妖物等级。``ch`` 形参仅为兼容既有调用点而
    保留，绝不参与渲染——任何人把它拼回去都会被守门打红。
    出处/章号只允许存在于数据表的注释列，不进玩家可见文案（沉浸感红线）。
    """
    lo = int(lv_lo or 0)
    hi = int(lv_hi or 0)
    if hi and hi != lo:
        lv = "lv%d-%d" % (lo, hi)
    elif lo:
        lv = "lv%d" % lo
    else:
        lv = ""
    tail = ("　妖物 %s" % lv) if lv else ""
    return "📍 %s%s" % (region or "未知之地", tail)


#: 战斗首行里的「境界具象」短语（2026-11-25 用户：战斗回执要结合当前玩家的境界）。
#: key 与 ``cfg.REALM_NAMES`` 的 13 阶一一对应，值取该阶在原著里的标志性体征：
#: 斗之气气旋未成、斗师斗气纱衣、大斗师斗晶、斗王斗气化翼、斗宗踏虚、斗帝帝炎。
#: 一律四字——首行要塞进境界称呼 + 回合 + 胜负，多一个字都会顶到 200 字骨架上限。
#: 缺键时调用点回落空串（不抛 KeyError），新境界加档不会炸老战斗。
REALM_FIGHT_TAG: dict[str, str] = {
    "斗之气": "气机鼓荡",
    "斗者": "气旋初凝",
    "斗师": "纱衣浮体",
    "大斗师": "斗晶映体",
    "斗灵": "掌生兵刃",
    "斗王": "翼展生风",
    "斗皇": "虚空下陷",
    "斗宗": "踏虚而立",
    "斗尊": "空间随念",
    "斗尊巅峰": "空间明灭",
    "半圣": "法则低鸣",
    "斗圣": "拳裂长空",
    "斗帝": "帝炎焚天",
}


def encounter_head(n: int, foes: str, rounds: int, win: bool, killed: int,
                   realm: str = "", tag: str = "", hp_ratio: float = 1.0,
                   exhausted: bool = False) -> str:
    """战斗首两行：遭遇 + 结算判词（2026-11-25 重构）。

    保留「遭遇」锚点（多个守门认这两个字）。第一行不动，第二行从旧的
    「{回合} 回合，大捷。」升级为三段信息：

      * **境界**——``realm``（``cfg.full_realm``，如「二星斗师」）+ ``tag``
        （本模块 ``REALM_FIGHT_TAG``，如「纱衣浮体」），让玩家一眼看到
        「我是拿这个境界在打」；
      * **胜负分档**——胜不是只有「大捷」：气血 ≥50% 大捷、20%~50% 惨胜、
        <20% 险胜；败单独一套「力竭·斩k/n」；
      * **收束**——胜配「斗气未散 / 气血翻涌 / 一线之差」，斗气耗尽改
        「斗气枯竭」；败配「气机溃散」。

    ``hp_ratio`` 是战后气血占体力上限的比例，由调用点算好传入（本模块不做
    数值计算，只出句）；缺省 1.0 等价于满血胜，老调用点行为可预期。
    """
    head = f"⚔️ 遭遇 {n} 敌——{foes}拦在去路上。"
    if win:
        if hp_ratio < 0.2:
            verdict, tail = "险胜", "一线之差"
        elif hp_ratio < 0.5:
            verdict, tail = "惨胜", "气血翻涌"
        else:
            verdict, tail = "大捷", "斗气未散"
        if exhausted:
            tail = "斗气枯竭"
    else:
        verdict, tail = f"力竭·斩{killed}/{n}", "气机溃散"
    r = str(realm or "").strip()
    t = str(tag or "").strip()
    who = f"{r}·{t}" if (r and t) else (r or t)
    prefix = f"{who}，" if who else ""
    return f"{head}\n　{prefix}{rounds} 回合{verdict}——{tail}。"


def battle_spot(spot: str, tier: str = "", move: str = "", after: str = "") -> str:
    """把高光条目包成一句叙述（2026-11-25 重构）。

    ``spot`` 仍是 ``commands._battle_line`` 剪出的「招式 伤害（归因）」字符串；
    ``tier``/``move`` 由调用点从 log 条目**直传**——品阶只存在于 log 里，
    从拼接后的字符串反解析必然丢，所以不做字符串猜谜。

    分档口径：普通/空品阶不加前缀（普通是缺省，每场刷「普通」是噪音），
    只有高阶/特殊才亮出「高阶·」「特殊·」；普攻走另一套，不写「【普攻】」
    这种把系统词当招式名的写法。

    ``after``（2026-11-25 第二轮）：高光之后接的**反应镜头**，如「林子里连虫鸣都
    静了」。并进同一行而不是另起一行——用户上一轮定的行数骨架要尽量少动，
    一行里放「动作 + 反应」本来就是小说写法。
    """
    import re
    m = re.match(r"^(.+?)\s+(\d+(?:\.\d+)?[万亿]?)\s*(?:（.*）)?$", spot)
    if not m:
        body = f"💥 最重的一记——{spot}。"
    else:
        name = str(move or m.group(1)).strip()
        dmg = m.group(2)
        t = str(tier or "").strip()
        if name in ("普攻", "拳脚", "拳头"):
            body = f"💥 一记拳脚砸实，{dmg} 点落在要害。"
        elif t == "特殊":
            body = f"💥 【特殊·{name}】气机一沉，{dmg} 点碾下。"
        elif t == "高阶":
            body = f"💥 【高阶·{name}】气劲贯体，{dmg} 点砸进。"
        else:
            body = f"💥 【{name}】一记砸实，{dmg} 点落在要害。"
    extra = str(after or "").strip()
    if not extra:
        return body
    return body[:-1] + "——" + extra if body.endswith("。") else body + "——" + extra


def battle_puppet(name: str, dmg: str, skin: str = "") -> str:
    """傀儡助阵行（2026-11-25 用户「或者高阶傀儡」）。

    为什么必须独立成行、不并进 💥 句：log 里傀儡击是 ``puppet=True`` 的独立
    条目，伤害归因是「傀儡的木桶五维」而不是玩家力量／斗气。并进高光句等于
    把傀儡的功劳记到玩家属性上，与「人物卡属性↔战斗强关联」的脸面冲突。

    ``name`` 取条目里的 ``helper``（如「天妖傀·玄」），``skin`` 取铜/银/金，
    有皮肤才加前缀（老档傀儡没有 skin 字段，不能硬拼「铜皮」）。
    """
    tag = f"{skin}皮" if skin else ""
    return f"🧿 {tag}{name}掠阵，补 {dmg} 点。"


def battle_defeat(seed: int = 0) -> str:
    """失败收场（2026-11-25 第一轮）／败局鼓励（第二轮改为轮换池）。

    为什么败要比胜多说一句：胜的反馈由判词 + 高光 + 吹捧承担，败只剩「力竭·斩k/n」
    一个数字，读起来像判词而不是邀请。这里给的是「不甘」而不是「你不行」——
    留钩子，才有下一场。

    第二轮改法：从 ``RALLY_LINES`` 里轮换，与上一条 🥀 嘲讽行配成一对
    「先被踩下去，再自己爬起来」——这是原著最常用的一次情绪起伏。
    """
    return "🩹 " + _pick(RALLY_LINES, seed)


def battle_gain(foes: str, exp: str, gold: str, drops: str = "",
                win: bool = True) -> str:
    """战果行。

    2026-11-25：败局不能再写「斩获」——打输了还说斩获，读起来自相矛盾。
    只换动词（斩获 / 未克），数值与锚点词「修为 +」「金币 +」原样保留，
    守门与快照口径不变。
    """
    verb = "斩获" if win else "未克"
    out = f"🎯 此战{verb} {foes} · 修为 +{exp} · 金币 +{gold}"
    return out + (" · " + drops if drops else "")


def battle_cost(taken: str, hp: str, pills: int = 0) -> str:
    out = f"🩸 代价 防御减伤后受伤 {taken} · 气血 {hp}"
    return out + (f" · 服药 {pills}" if pills else "")


# ---------------------------------------------------------------------------
# 二·五、小说式战报：铺垫 / 吹捧 / 嘲讽 / 鼓励 / 点睛（2026-11-25 第二轮）
#
# 用户原话：「要像写小说那样，机器人输出的战斗报告要有铺垫有激烈高潮，失败有
# 嘲讽和鼓励，成功有满满的吹捧，就算只掉落一个垃圾材料，也要吹的天花乱坠……
# 要把原著里作者的情绪，满满的通过文本内容，注入给玩家。」
#
# 落法：在一场战报里钉死五段——**取景（在哪）→ 登场（来了什么）→ 高潮（怎么赢的）
# → 吹捧/嘲讽（别人怎么看）→ 点睛（这一战留下什么）**。每段与敌人数无关，1 敌与
# 5 敌仍是同一套骨架（用户上一轮的硬红线：不许随敌人数膨胀）。
#
# 情绪来源不是形容词堆砌，而是天蚕土豆的三板斧，逐条对应：
#   * 反差——先把你踩到泥里（嘲讽），再让你站起来（鼓励）；
#   * 围观视角——赢不只是数值，是「林子里静了一瞬」；
#   * 越级/绝境加码——最重的吹捧留给最险的胜。
# ---------------------------------------------------------------------------

#: 章 → 区域取景（3 句轮换）。**纯旁白**，只写风物与局势，不给任何角色加台词。
#: 取景意象逐条对得上正文：后山雾气与峭壁、魔兽山脉的血腥味、塔戈尔的黄沙与蛇人、
#: 云岚宗的云海山门、天墓二十年一开、兽域的十万大山（原文「十万大山已是处在兽域之内」）。
CHAPTER_SCENE: dict[int, tuple[str, ...]] = {
    1: ("后山的雾漫上来，山脚下的窃笑被隔在了雾外。",
        "峭壁之下是望不见底的悬崖，对面是雾气里的魔兽山脉。",
        "广场上的窃语还没散，你已经把指甲掐进了掌心。"),
    2: ("山脊上雾还没散，血腥味先一步漫了下来。",
        "低阶魔兽在林间游走，刀口都朝一个方向。",
        "险地才逼得出潜力——这话你一直记着。"),
    3: ("越往深处走，树越矮、风越冷、兽吼越近。",
        "血迹一路铺进去，没有一具尸体是完整的。",
        "这里是魔兽的地盘，人只是路过。"),
    4: ("再往前一步，就是连斗皇都要绕道的地方。",
        "核心区的天空常年被翼影遮着，光都是碎的。",
        "六阶魔兽的领地，连空气都压着人。"),
    5: ("黄沙一直铺到天边，风里全是烫人的腥气。",
        "日头把沙面烤得发白，夜里又能冻裂骨头。",
        "沙漠深处有蛇人的气息，佣兵都绕着走。"),
    6: ("石漠城的墙是黑石垒的，风一过就呜呜地响。",
        "蛇人的哨声在沙丘后响起，一支佣兵队正在溃退。",
        "禁地之内，连阳光都像是被抽走了一半。"),
    7: ("帝都的灯火亮得刺眼，墨家的车马堵了半条街。",
        "拍卖场的铜钟一响，整条街的人都抬起了头。",
        "墨家的门客站成一排，看你的眼神像在看死人。"),
    8: ("云海翻涌，山门在雾里浮着，像一座悬空的城。",
        "云岚宗的钟敲了九下，这一日终于还是来了。",
        "这里的一砖一瓦，都记着当年那纸休书。"),
    9: ("遗迹的石柱半埋在土里，符文还在隐隐发烫。",
        "兽魂在断墙之间游荡，风一过就是一片呜咽。",
        "远古的气息压下来，连斗气都慢了半拍。"),
    10: ("中州的风是硬的，这里的强者不问你从哪来。",
         "丹塔的影子压着半座城，药香飘出十里。",
         "魂殿的人从不露面，只留下满地的黑幡。"),
    11: ("天墓二十年一开，这一次你赶上了。",
         "墓道深处没有风，只有前人留下的杀意。",
         "八族的年轻一代都来了，谁也不肯先退。"),
    12: ("十万大山横在眼前，那里是魔兽家族的天下。",
         "兽域的天空属于翼族，地面属于爪牙。",
         "远古遗迹的争夺，从来都是血流成河。"),
    13: ("帝炎在体内烧着，万火俯首，天地安静得可怕。",
         "最后一段路没有同行者，只有你自己。",
         "走完这条路的人，这片大陆上屈指可数。"),
}

#: 胜局吹捧。key 是战况档，由调用点按「是否无伤 / 剩余气血 / 是否越级」判定。
#: 为什么分档而不是一句话：碾压局说「血流到脚踝」是错的，险胜说「衣角没脏」更假。
GLORY_LINES: dict[str, tuple[str, ...]] = {
    # 无伤：对手连身都没碰到
    "sweep": ("衣角都没脏——它连你的身都没碰到。",
              "从头到尾，你一步都没退，也没让它近身。",
              "这一场，连热身都算不上，你还没出汗。"),
    # 大捷：赢得干净
    "great": ("林子里静了一瞬，随后是压不住的哗然。",
              "你收回手，掌心那点斗气还没散。",
              "这样的手段放在外面，足够让人记一辈子。"),
    # 惨胜 / 险胜：靠意志赢的
    "narrow": ("血流到脚踝，你还站着——站着的才是赢家。",
               "最后一击落下时，你已经听不见自己的心跳。",
               "所有人都以为你要倒下，包括你自己。"),
    # 越级：等级差不是问题
    "crush": ("它比你高出整整一头，可先倒下的是它。",
              "等级从来不是道理，站着的那个说了算。",
              "越级斩杀这种事传出去，没人肯信。"),
}

#: 高潮反应镜头：紧跟在 💥 那一击后面的半句（并进同一行）。
#: 小说里的「高潮」从来不是伤害数字本身，而是**这一击落下去之后场面的变化**——
#: 虫鸣停了、骨头响了、对手连退都退不开。数字交给前面，反应交给这里。
CLIMAX_AFTER: dict[str, tuple[str, ...]] = {
    "sweep": ("它连惨叫都没来得及。", "这一下之后，它没能再站起来。"),
    "great": ("林子里连虫鸣都静了一瞬。", "它眼里那点凶光，散了个干净。"),
    "narrow": ("这一击，是你拿最后一口气换的。", "你和它，同时跪了下去——它先倒。"),
    "crush": ("它连后退都退不开半步。", "高出你那么多，连一招都没接住。"),
    "lose": ("这一击之后，你的手在抖——斗气见底了。",
             "这一掌拍在它身上，像拍在一块铁板上。"),
}


#: 战利吹捧。key 是掉落成色，阈值由 ``drop_tier`` 按药材价盘分位推导（不手抄）。
#: 用户点名「就算只掉落一个垃圾材料，也要吹的天花乱坠」——所以 trash 档不是贬低，
#: 而是**把不值钱的东西说成拿命换来的战利品**：这是爽文的常规操作，也是笑点。
DROP_GLORY: dict[str, tuple[str, ...]] = {
    "trash": ("就这点破烂？对，可它是从三头凶兽嘴里抠出来的。",
              "旁人眼里一文不值的东西，在你手里却是战利品。",
              "不值几个钱的东西，被血一浸，也就金贵了。"),
    "common": ("药材入袋，成色比预想的还要好上一分。",
               "这点收获看着不多，攒起来就是下一次突破。",
               "东西不算稀奇，可从它们身上拿，就是不一样。"),
    "rare": ("异宝入手，掌心的温度都不对了。",
             "这种成色的东西，拍卖场里能压住半条街。",
             "它一入手，你身后的林子都安静了。"),
}

#: 败局嘲讽：**旁白转述围观者**，不加引号（模块纪律：不给角色新编台词）。
#: 嘲讽的分寸：笑的是「你不自量力」，不是「你菜」——前者能激人，后者只会劝退。
MOCK_LINES: tuple[str, ...] = (
    "四周几声嗤笑，没人觉得意外——不自量力罢了。",
    "有人在远处摇了摇头：又一个来送死的。",
    "它们甚至没有追，像是懒得再多看你一眼。",
    "围观的人已经转身，连嘲笑都懒得多给一句。",
)

#: 败局鼓励：第二人称，全部落在「还没完」上。
#: 与嘲讽合成一对：先被踩下去，再自己爬起来——这是原著最常用的一次情绪起伏。
RALLY_LINES: tuple[str, ...] = (
    "可你还是爬起来了——这笔账，记下了。",
    "牙咬碎了咽回去，下一场不会是这般结局。",
    "斗气可以散，这口气不能散，咽不下去。",
    "倒下不可怕，躺着不动才可怕——你动了。",
)

#: 章 → 原著台词（**逐字**，出处只写在这里，绝不进回执正文）。
#: 2026-11-25 用 2133 章全文语料逐字核过；语料里有源站替换字符（『药』），
#: 核对时做了归一化，落地的是**原文形态**。给不出逐字原文的章一律留空，
#: 由 ``quote_line`` 回落通用旁白——宁可不引，也不编。
#:
#: 2026-11-25 收尾把原先缺台词的 7 章（5/6/7/9/10/11/12）补齐，**13 章全覆盖**：
#: 每条都用 ``tmp/novel/q.py <逐字片段>`` 反查命中，出处取命中章回标题。补句原则与
#: 最初 6 句相同——必须能独立成句、带说话当时的情绪，绝不为凑数截半句叙述。
#: 例如第 12 章取蝎龙族强者的「…一群蝼蚁！」（兽域＝魔兽家族的天下），
#: 而不是把「十万大山已是处在兽域之内」这种纯叙述塞进引号冒充台词。
CHAPTER_QUOTE: dict[int, tuple[tuple[str, str], ...]] = {
    1: (("三十年河东，三十年河西，莫欺少年穷！", "第7章 休！"),),
    2: (("处于险地，潜力才能爆发。", "第100章 极崩的暗劲"),),
    3: (("强者为尊，永远都是这个世界的主调", "第450章 安顿"),),
    4: (("这个世界，没有实力，连一坨狗屎都不如，至少，狗屎还没人敢去踩！",
         "第8章 神秘的老者"),),
    5: (("最后的修行之地，塔戈尔大沙漠，终于抵达了！", "第175章 抵达"),),
    6: (("我就不信，你这乌龟壳还真的打不烂！", "第243章 击杀大斗师！"),),
    7: (("若真是那样，我不介意血洗了墨家！", "第253章 搜寻以及墨家的野心"),),
    8: (("这张契约，不是解除婚约的契约，而是本少爷把你逐出萧家的休证！",
         "第213章 倔着骨，咬着牙，忍着辱"),),
    9: (("老师，保重，魂婴果，弟子定给您老人家带回！", "第1210章 远古遗迹"),),
    10: (("在绝对的实力面前，这些都没有用", "第1200章 戏耍"),),
    11: (("放心，迟早有一天，这句话你会自己吞回去的。", "第1299章 魂崖"),),
    12: (("既然你们找死，那本王就成全你们，一群蝼蚁！", "第1056章 蝎龙族"),),
    13: (("那异火，在功成之后，自命帝炎，而后再度修炼千载，方才破世而出。",
          "第1614章 异火榜第一！"),),
}

#: 没有逐字台词的章用这几句旁白收（不冒充任何角色）。
#: 13 章补齐后 ``quote_line`` 只对脏档（ch=0 / 将来扩章未补）回落，正常流程不再用。
QUOTE_FALLBACK: tuple[str, ...] = (
    "斗气大陆的规矩：活下来的才配说话。",
    "这一战不会写进谁的传记，但你会记得。",
    "风把血腥味吹散了，前面的路还长。",
)

# ---------------------------------------------------------------------------
# 二·六、NPC 插话：角色出处台账 + 说话人绑定（2026-11-25 设计文档 §4.2 收尾）
#
# 用户原话：「最好要有原著的一些出圈台词或者 NPC 事件」。第二轮的 CHAPTER_QUOTE
# 只做了「点睛台词」——**没有说话人**，谁在喊分不清；这里补上带说话人的插话：
# 某章是魔兽山脉，就由药老开口；到了石漠城，就是萧厉在旁边看。
#
# 🚨 三条铁律（改表前先看）：
#   1. 台词必须逐字，出处只写进台账第三列，**绝不渲染给玩家**；
#   2. 说话人必须与台词**同一条**绑定（不能「云棱」配美杜莎的话），且说话人
#      只能取自 ``CHAPTER_NPC[ch]`` 白名单——否则第 1 章会冒出斗帝的角色；
#   3. 只在险战（苦战/越级/败局）出、一场最多一条，顺风局不插话——否则每场
#      同一句话会把玩家烦死，也会吃掉 350 字骨架预算。
# ---------------------------------------------------------------------------

#: 角色出处台账：说话人 → ((逐字台词, 出处), ...)。出处仅维护者核对用，不进回执。
#: 与 CHAPTER_QUOTE 相同口径：用 tmp/novel/q.py 反查命中，落地原文形态。
NPC_SPEAKERS: dict[str, tuple[tuple[str, str], ...]] = {
    # 2026-11-26 按战况重排（用户「人物台词太生硬、不符合实时战斗情况」）：
    # 每条仍是逐字原文 + 出处（tmp/novel/q.py 与 dlg.py 反查命中），但三档各有
    # 贴合此刻的话——惨胜是淡然宽慰、越级是真心赞许、败局是护短调侃，不再
    # 一句走天下。具体哪句配哪档见 NPC_TIER_FIT。
    "药老": (
        ("就是因为没有，才能显出我这功法的独特啊！", "第9章 药老！"),
        ("斗技有什么了不起的？等你以后会了炼药术，高级斗技，直接有人抢着给你送上门来。",
         "第18章 玄阶高级斗技：八极崩"),
        ("小家伙，不错…竟然能想到用这招来强化吸掌。", "第28章 强化吸掌"),
        # 2026-11-26 黑盒 P6：败局原句提「强化吸掌」，但新手学的根本不是吸掌，
        # 听着像串戏。换一句同为逐字原文、且贴合败局宽慰的话（不绑定任何斗技名）。
        ("可在这三年的嘲骂中，你成长了不是？", "第8章 药老现身"),
    ),
    "美杜莎女王": (("若是进化成功，今日此处的所有人类，都得永留沙漠！",
                "第215章 进化开始"),),
    "萧厉": (("脱离了那把古怪黑尺的小炎子，才是最强的状态！",
             "第243章 击杀大斗师！"),),
    # 旧句「我是云岚宗的大长老，云棱」是自我介绍，塞进苦战/败局极度违和 →
    # 换成云岚宗强者当场放话的逐字原文（第351章，dlg.py 反查命中）。
    "云棱": (("朋友，虽然不知道你究竟是何人，不过，这般得罪我云岚宗，可并不是什么明智之举啊。",
             "第351章 斗皇，凌影"),),
    "摘星老鬼": (("呵呵，终于打算拼命了么？", "第1200章 戏耍"),),
    "魂殿殿主": (("你的灵魂力量很强，但可惜，本体太弱", "第1471章 魂殿殿主！"),),
    "蝎龙族": (("既然你们找死，那本王就成全你们，一群蝼蚁！", "第1056章 蝎龙族"),),
}

#: 2026-11-26：说话人 → 战况档 → 用第几条台词（NPC_SPEAKERS 里的下标）。
#: 只对需要分档的角色登记；未登记的角色/档位回落到该角色第 0 条，保持老行为。
#: 这样「惨胜/越级/败局」各说各话，而不是同一句重复到玩家烦。
NPC_TIER_FIT: dict[str, dict[str, int]] = {
    # 败局：药老以「你成长了不是」宽慰（2026-11-26 换掉旧的「强化吸掌」句——
    # 新手学的根本不是吸掌，听着串戏）；惨胜：淡然宽慰「斗技有什么了不起」；
    # 越级：护短式得意「功法独特」。三档仍是逐字原文、互不相同。
    "药老": {"lose": 3, "narrow": 1, "crush": 0},
}

#: 章 → 本章可开口的说话人白名单。空元组表示该章没有核到可独立成句的 NPC 话，
#: 宁可不出这一行，也不从别的章借角色（那会造成第 1 章冒出魂殿殿主）。
CHAPTER_NPC: dict[int, tuple[str, ...]] = {
    1: ("药老",),             # 乌坦城：药老刚现身
    5: ("美杜莎女王",),        # 塔戈尔：蛇人族女王
    6: ("萧厉",),             # 石漠城：漠铁佣兵团
    8: ("云棱",),             # 云岚宗：大长老
    10: ("摘星老鬼",),         # 中州：魂殿天尊
    11: ("魂殿殿主",),         # 天墓：魂殿殿主
    # 12（兽域）故意留空：蝎龙族那句「…一群蝼蚁！」已是本章点睛台词，
    # 再拿来当插话会在同一场里连出两遍（真机自查撞到过）。宁可不出，也不凑数。
}

#: 只有这几种险战才插话；sweep（无伤）/great（大捷）不插，避免刷屏与字数膨胀。
_NPC_TIERS = frozenset(("lose", "narrow", "crush"))


def npc_line(ch: int, seed: int = 0, tier: str = "") -> str:
    """战斗中插话：本章白名单里的角色说一句逐字原文；不该出时返回空串。

    与 ``quote_line`` 互补：那条是「这一战留下什么」的无主点睛，这条是
    「当场有谁开口」的有主插话。两者台词来自不同池，绝不会同一句出现两遍。
    """
    if str(tier or "") not in _NPC_TIERS:
        return ""
    who_list = CHAPTER_NPC.get(int(ch or 0), ())
    if not who_list:
        return ""
    who = str(who_list[int(seed) % len(who_list)])
    pool = NPC_SPEAKERS.get(who, ())
    if not pool:
        return ""
    # 2026-11-26：按战况档选贴合的逐字台词（惨胜/越级/败局各说各话）。
    # 登记过 NPC_TIER_FIT 的角色按档取句；未登记的回落到第 0 条（老行为）。
    idx = NPC_TIER_FIT.get(who, {}).get(str(tier or ""))
    if idx is None:
        idx = int(seed // len(who_list)) % len(pool)
    idx = max(0, min(idx, len(pool) - 1))
    text = str(pool[idx][0])
    return "🗣 %s：%s" % (who, text)


def _pick(pool, seed: int) -> str:
    """按 seed 轮换，池空返回空串（新章加档不会炸老战报）。"""
    if not pool:
        return ""
    return str(pool[int(seed) % len(pool)])


def scene_line(ch: int, seed: int = 0) -> str:
    """取景行（铺垫）。行首用全角空格缩进——与结算数据行在视觉上分层。

    章号不在表里（脏档 0、或将来扩章还没补文案）时返回空串，由调用点**丢弃**，
    不要留一个只有全角空格的行——那会在 QQ 里显示成一条空白的空隙。
    """
    body = _pick(CHAPTER_SCENE.get(int(ch or 0), ()), seed)
    return ("　" + body) if body else ""


def war_tier(win: bool, taken: int, hp_ratio: float, foe_max_lv: int,
             my_lv: int) -> str:
    """战况分档：越级 > 无伤 > 大捷 > 惨胜/险胜。

    顺序即优先级：越级斩杀哪怕只剩一丝血，也该按「越级」吹——那是玩家最想被看见的事。
    """
    if not win:
        return "lose"
    if int(foe_max_lv or 0) >= int(my_lv or 0) + 2:
        return "crush"
    if int(taken or 0) <= 0:
        return "sweep"
    return "narrow" if float(hp_ratio) < 0.5 else "great"


def glory_line(tier: str, seed: int = 0) -> str:
    """胜局吹捧行（🏅 领起，与结算数据行的 emoji 体系一致）。"""
    return "🏅 " + _pick(GLORY_LINES.get(tier) or GLORY_LINES["great"], seed)


def climax_line(tier: str, seed: int = 0) -> str:
    """高潮反应镜头（并进 💥 行尾，不单独占行）。"""
    return _pick(CLIMAX_AFTER.get(tier) or CLIMAX_AFTER["great"], seed)


def drop_tier(names) -> str:
    """掉落成色：药材按 ``config.HERBS`` 的价盘分位，非药材按 ``item_kind`` 归类。

    为什么不全按价：丹药没有 price 字段，强化石也不在药材表里；早先「查不到就当
    rare」的写法会让「白参 + 炎星石」被判成「异宝入手」——炎星石是日常强化料，
    这么吹反而显得战报在敷衍。现在的口径：
      * 带纹丹 / 丹药 → rare（能炼丹本身就不便宜）；
      * 药材 → 按价盘三分位（低=trash、中=common、高=rare）；
      * 其余（强化石/傀儡料/杂物）→ common；
      * 多件取最高档（一场里出过好东西就按好东西吹）。
    阈值由价盘推导，**不手抄**——新材料进表就自动有档。
    """
    from . import config as _cfg  # noqa: PLC0415 - 函数内导入，避免与 config 循环

    names = list(names or ())
    if not names:
        return "common"
    prices = sorted(int(h.get("price") or 0) for h in getattr(_cfg, "HERBS", ()))
    lo = prices[len(prices) // 3] if prices else 0
    hi = prices[(len(prices) * 2) // 3] if prices else 0
    # 顺序即「值钱程度」：多件取最高的那一档（出过好东西就按好东西吹）。
    # 早先把 trash 排在 common 之上，会让「白参 + 炎星石」被判成 trash——
    # 一件便宜货把整包东西拉低，与「越有收获越该被看见」正好相反。
    order = {"trash": 0, "common": 1, "rare": 2}
    best = "trash"
    for n in names:
        kind = _cfg.item_kind(str(n))
        if kind in ("danwen", "pill"):
            tier = "rare"
        elif kind == "herb":
            p = int((_cfg.HERB_BY_NAME.get(str(n)) or {}).get("price") or 0)
            tier = "trash" if p <= lo else ("rare" if p > hi else "common")
        else:
            tier = "common"
        if order[tier] > order[best]:
            best = tier
    return best


def drop_glory_line(names, seed: int = 0) -> str:
    """战利吹捧行（垃圾料也吹）。无掉落时返回空串，由调用点改用战况吹捧。"""
    if not names:
        return ""
    return "🏅 " + _pick(DROP_GLORY[drop_tier(names)], seed)


def mock_line(seed: int = 0) -> str:
    """败局嘲讽行（🥀）：与胜局 🏅 同一位置，互斥。"""
    return "🥀 " + _pick(MOCK_LINES, seed)


def quote_line(ch: int, seed: int = 0, special: bool = False) -> str:
    """点睛行：本章逐字台词，没核到就回落通用旁白。

    只在苦战/败局出现这句台词（``special``），其余场次按 seed 三中取一——
    一章只有一句台词，每场都喊就成了口号，喊三次就不值钱了。
    不加「原著」二字（禁词）；出处留在 ``CHAPTER_QUOTE`` 第二列供维护者核对。
    """
    pool = CHAPTER_QUOTE.get(int(ch or 0), ())
    if pool and (special or int(seed) % 3 == 0):
        text = str(pool[int(seed) % len(pool)][0])
        return f"　「{text}」"
    return "　" + _pick(QUOTE_FALLBACK, seed)


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


#: 火焰护炉旁白（2026-11-26）：只讲「哪朵火在护持」，不报数字、不说「加成/成功
#: 率+X%」。玩家看得见的口径是火候被托住、丹成更稳，符合药老控火的原著调性。
#: 三类由调用点按玩家实有火焰分组传入；无炼药火则整行不出现。
CRAFT_FLAME_AID_XIAN = (
    "　异火【{names}】自炉底托起，火候被一寸寸稳住——这一炉，本就占了几分天时。",
    "　【{names}】在鼎中沉浮，将躁动的药力缓缓按下，丹纹未成先有三分定相。",
)
CRAFT_FLAME_AID_BEAST = (
    "　兽火【{names}】温养着炉温，不猛不燥，药材的性子被一点点揉开。",
    "　【{names}】伏在炉壁，火色温吞却绵长，火候便少了大起大落。",
)
CRAFT_FLAME_AID_MIX = (
    "　【{names}】同炉而燃，异火主势、兽火托底，火候被烘得恰到好处。",
)

#: 失败专用护炉旁白（2026-11-26 用户反馈「都炼药失败了还在说火焰恰到好处」）。
#: 成功旁白讲的是「火候被稳住/恰到好处」，用在失败回执里自相矛盾。失败时火确实
#: 仍在护炉，只是这次没能压住药力——另起一组旁白，只讲「火尽了力却没能挽回」，
#: 同样不报数字、不说术语。三类仍按异火/兽火/混合分组，与成功组一一对应。
CRAFT_FLAME_AID_FAIL_XIAN = (
    "　异火【{names}】也全力托住炉底，只是这一炉药力太烈，终究没能压住。",
    "　【{names}】在鼎中拼命镇压躁动的药力，奈何差了最后一线。",
)
CRAFT_FLAME_AID_FAIL_BEAST = (
    "　兽火【{names}】已把炉温守到极稳，可药性一生变，温火也护不住这一炉。",
    "　【{names}】伏在炉壁苦苦支撑，火色虽稳，仍没能拉回散尽的药力。",
)
CRAFT_FLAME_AID_FAIL_MIX = (
    "　【{names}】同炉而燃，异火兽火都已竭力相护，这一炉却仍失了分寸。",
)


def craft_flame_aid(xian: tuple[str, ...] = (), beast: tuple[str, ...] = (),
                    seed: int = 0, success: bool = True) -> str:
    """炼药播报的护炉行：有炼药火才回一句原著化旁白，无火回空串。

    xian / beast 是调用点按 ``flame_bonus.alchemy_flame_names`` 分组的名字。
    只描述火在护持，绝不出现百分比、成数、「加成」「成功率」等开发向术语。

    ``success``（2026-11-26 新增）：成功/失败用**两组互不混用**的文案。
    失败时绝不能再出现「恰到好处」「稳住」「三分定相」这类只属于成功的措辞。
    """
    xian = tuple(n for n in xian if n)
    beast = tuple(n for n in beast if n)
    if not xian and not beast:
        return ""
    k = max(0, int(seed))
    if success:
        x_pool, b_pool, m_pool = (CRAFT_FLAME_AID_XIAN, CRAFT_FLAME_AID_BEAST,
                                  CRAFT_FLAME_AID_MIX)
    else:
        x_pool, b_pool, m_pool = (CRAFT_FLAME_AID_FAIL_XIAN, CRAFT_FLAME_AID_FAIL_BEAST,
                                  CRAFT_FLAME_AID_FAIL_MIX)
    if xian and beast:
        body = m_pool[k % len(m_pool)]
        names = "、".join(xian + beast)
    elif xian:
        body = x_pool[k % len(x_pool)]
        names = "、".join(xian)
    else:
        body = b_pool[k % len(b_pool)]
        names = "、".join(beast)
    return body.format(names=names)


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


def craft_mat(missing: str, n: int = 1) -> str:
    """缺料回执（2026-11-26：缺口按本次炉数算，并指向下方买齐按钮）。"""
    n = max(1, int(n))
    if n > 1:
        head = (f"❌ 材料不足：欲炼 {n} 炉，药材却凑不齐——缺 {missing}"
                f"（{n} 炉所需）。")
    else:
        head = f"❌ 材料不足：炉火已起，药材却凑不齐——缺 {missing}。"
    return head + "\n　点下方药材按钮可一次买齐，买完再点「炼制」即可开炉（也可发「宝库」自己挑）。"


def craft_need(missing: str, n: int = 1) -> str:
    """连炼中途药材见底：列「再炼这批还缺多少」（与 craft_mat 的括号口径一致）。"""
    n = max(1, int(n))
    return f"　再炼 {n} 炉尚需：{missing}——点下方药材按钮一次买齐。"


def craft_mp() -> str:
    return "❌ 未能开炉：斗气不足以续火，炉温渐降——先调息回气。"


def craft_generic_fail() -> str:
    return "❌ 未能开炉：炉中火候难成——先看「炼药」丹方，再备齐药材与丹方。"


def craft_over(diff: int) -> str:
    """越阶开炉的风险提示。**只有正数才算越阶**。

    diff 的语义是「丹药所需品阶 − 炼药师品阶」：负数代表高品阶炼低阶的**加成**
    （四品炼一品十拿九稳），不是越阶。2026-11-25 玩家实测骂过——炼制成功了，
    回执里还印着「⚠ 越-3档——几无可能」，纯属胡说。这里直接吞掉非正数，
    任何调用方传错也印不出来；调用方那边同样有 max(0, …) 的一道闸。
    """
    try:
        diff = int(diff or 0)
    except (TypeError, ValueError):
        return ""
    if diff <= 0:
        return ""
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


def craft_danwen(name: str, n: int, tier: int = 1) -> str:
    """开炉出纹的回执（丹成卡/纯文本共用）。tier=几纹（1~3）。"""
    t = "一二三"[max(1, min(3, int(tier))) - 1] if int(tier) else "一"
    return f"✨ {t}纹浮现！{n} 枚【{name}】出炉——丹身缠纹，品相绝佳，服下另有奇效。"


def danwen_grant(bless_name: str, desc: str, tier: int = 0) -> str:
    """服带纹丹后挂赐福的可见回执。文案不透露概率/考据。"""
    t = ("（%s纹）" % "一二三"[max(1, min(3, int(tier))) - 1]) if int(tier or 0) else ""
    return f"　✨ 丹纹赐福【{bless_name}】入体{t}——{desc}。"


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


#: 一次连服多枚的调侃（2026-11-25 用户「吃太多加几种不同调侃语句」）。
#: 按本次成功服下的枚数分档，越吃越夸张；纯沉浸口吻，不提概率/机制/开发。
_PILL_GUZZLE = (
    (2, "药老在旁捋须：一口两枚，急什么，药力又不会长腿跑了。"),
    (4, "药老皱眉：你这是服药还是嗑糖？丹田怕是都要溢出来了。"),
    (8, "旁的药师看傻了眼——一把丹药往嘴里倒，跟嚼炒豆似的。"),
    (16, "丹气冲得周身药雾缭绕，你活像座行走的药炉，三步外都闻得到。"),
    (32, "天地都看不下去：这哪是修炼，分明是把整座药库搬进了肚子。"),
)


def pill_guzzle(n: int) -> str:
    """一次连服 n 枚的调侃句；n 未达调侃门槛返空串（2 枚起）。"""
    n = int(n or 0)
    hit = ""
    for threshold, line in _PILL_GUZZLE:
        if n >= threshold:
            hit = line
        else:
            break
    return hit


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


def tower_down(fallen: int, cleared: int | None = None) -> str:
    """力竭文案。``fallen`` = **倒下那一层**（正在打的层），``cleared`` = 已通过层数。

    2026-11-26 用户实测口径矛盾：「从第 25 层开始」却「止步 24 层」。
    根因是旧版只传 ``st['floor']``（已通过层），而出发层是 checkpoint+1 ——
    在 25 层开打、25 层就输，通过数仍是 24，于是「出发 25 / 止步 24」自相矛盾。
    现在一律报**倒下那一层**，必要时附上「此前已通 N 层」，两句话就对得上了。
    """
    fallen = int(fallen)
    if cleared is None or int(cleared) <= 0 or int(cleared) == fallen:
        return f"💀 力竭倒下，倒在第 {fallen} 层。"
    return f"💀 力竭倒下，倒在第 {fallen} 层（此前已通 {int(cleared)} 层）。"


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
    "💀 力竭倒下，倒在第 {floor} 层。",
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
    # 2026-11-25 新分支全登记：不登记就等于禁词守门扫不到这批句子。
    encounter_head(3, "血灵蟒等3名", 6, True, 3, realm="二星斗师",
                   tag="纱衣浮体", hp_ratio=0.9),
    encounter_head(3, "血灵蟒等3名", 6, True, 3, realm="二星斗师",
                   tag="纱衣浮体", hp_ratio=0.3),
    encounter_head(3, "血灵蟒等3名", 6, True, 3, realm="二星斗师",
                   tag="纱衣浮体", hp_ratio=0.1, exhausted=True),
    encounter_head(3, "血灵蟒等3名", 6, False, 1, realm="二星斗师",
                   tag="纱衣浮体", hp_ratio=0.0),
    battle_spot("佛怒火莲 6240（力量·斗气）"),
    battle_spot("身形一晃，避开了兽击（敏捷·闪避）"),
    battle_spot("劈山掌 3621", tier="高阶", move="劈山掌"),
    battle_spot("天火三玄变 9000", tier="特殊", move="天火三玄变"),
    battle_spot("普攻 2100", move="普攻"),
    battle_puppet("天妖傀·玄", "7965", "金"),
    battle_puppet("铜傀·铁", "120"),
    battle_defeat(),
    battle_gain("血灵蟒", "一千八", "九百", "血灵草×2"),
    battle_gain("血灵蟒等3名", "0", "0", win=False),
    battle_cost("三千", "一万二", 2),
    # 2026-11-25 第二轮小说式战报：五段新句全部登记（不登记 = 禁词守门扫不到）
    scene_line(1, 0), scene_line(6, 1), scene_line(8, 2), scene_line(13, 0),
    glory_line("sweep", 0), glory_line("great", 1), glory_line("narrow", 2),
    glory_line("crush", 0),
    climax_line("sweep", 0), climax_line("great", 1), climax_line("narrow", 2),
    climax_line("crush", 0), climax_line("lose", 1),
    drop_glory_line(["黑岩根"], 0), drop_glory_line(["白参"], 1),
    drop_glory_line(["冰晶石"], 2), drop_glory_line([], 0),
    mock_line(0), mock_line(3), battle_defeat(0), battle_defeat(1),
    quote_line(1, 0), quote_line(8, 0), quote_line(13, 0), quote_line(5, 0),
    battle_hero_aid(),
    battle_ready("四星斗者"),
    battle_cd(20),
    craft_success("回灵丹", "四品", "十成"),
    craft_success("九幽散", "帝品", "八成"),
    craft_fail(3, "十成", "", 0),
    craft_fail(3, "十成", "　⚠ 越2档——几无可能，仅可勉强尝试\n", 1),
    craft_flame_aid(("九幽风炎",), ()),
    craft_flame_aid(("九幽风炎",), (), seed=1),
    craft_flame_aid((), ("桃花火",)),
    craft_flame_aid((), ("桃花火",), seed=1),
    craft_flame_aid(("九幽风炎", "海心焰"), ("桃花火", "天寒火")),
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
    tower_down(18, 17),
    weekly_clear(),
    battle_tip(True),
    battle_tip(False),
)


def all_player_texts() -> tuple[str, ...]:
    """守门测试用：全部玩家可见静态句 + 展开后的模板样本。"""
    out = list(STATIC_TEXTS) + list(CALL_SAMPLES)
    for obj in (CROSS_LINES, STAR_LINES, CRAFT_SUCCESS, REALM_FIGHT_TAG,
                CHAPTER_SCENE, GLORY_LINES, CLIMAX_AFTER, DROP_GLORY):
        for v in obj.values():
            if isinstance(v, str):
                out.append(v)
            else:
                out.extend(str(x) for x in v)
    out.extend(CRAFT_FAIL_LINES)
    out.extend(MOCK_LINES)
    out.extend(RALLY_LINES)
    out.extend(QUOTE_FALLBACK)
    # 台词池只扫台词本身：第二列是出处（「第N章 …」），不面向玩家，
    # 混进来会把「第7章 休！」当玩家可见句去扫禁词，属于自欺。
    for pool in CHAPTER_QUOTE.values():
        out.extend(str(pair[0]) for pair in pool)
    return tuple(dict.fromkeys(out))
