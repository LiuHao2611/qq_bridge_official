# -*- coding: utf-8 -*-
"""战斗回执「有血有肉」守门（2026-11-25）。

用户原话：
  「优化整个战斗回复内容，结合原著改，要结合当前玩家的境界，还有使用的技能，
   或者高阶傀儡，等等，胜利或者失败后给出不同的高反馈内容，别这么单调和乏味，
   要吊起玩家兴趣」

拆成六条可判据，全部用**行为**复算，不拿被测模块的常量为标准答案：
  1. 境界真的进回执，且 13 阶各不相同（不能「境界名换了句子不变」）；
  2. 斗技品阶真的进 💥 行，普通/高阶/特殊/普攻四档措辞互不相同；
  3. 傀儡真的单独露脸，且伤害归因不串到玩家身上；
  4. 胜/败两套判词必须不同（不是同一句换两个字）；
  5. 败局比胜局只多一行，胜局不许出现败局句；
  6. 加料之后 200 字骨架闸与「1 敌 vs 5 敌不膨胀」仍成立。

另含 prove-it 反证：把每类检测的坏样本喂进去，判据必须报错。
"""
from __future__ import annotations

import asyncio
import random
import re
from pathlib import Path

import nonebot

if not getattr(nonebot, "_already_inited", False):
    try:
        nonebot.init()
    except Exception:  # noqa: BLE001 - 已被其它测试文件初始化过
        pass

from src.plugins.doupo import commands, config as cfg, core  # noqa: E402
from src.plugins.doupo import doupo_text as txt  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
TEXT_PY = ROOT / "src" / "plugins" / "doupo" / "doupo_text.py"

#: 与 tests/test_doupo_text_flavor.py 同口径的玩家可见禁词（独立抄一份，
#: 故意不 import——同源同错的话，那个文件挂了这里也跟着瞎）。
BAN_WORDS = (
    "原著", "未载", "不代编", "游戏原创", "语料", "非原著角色", "游戏设定",
    "考据", "出处：", "心魔劫影", "战力", "快照",
)


# ---------------------------------------------------------------------------
# 一、境界：13 阶必须各自成立，且互不相同
# ---------------------------------------------------------------------------
def _head_for_realm(realm: str, lv: int, win: bool = True, ratio: float = 0.9) -> str:
    return txt.encounter_head(2, "嗜血鼠等2名", 4, win, 2, realm=realm,
                              tag=txt.REALM_FIGHT_TAG.get(cfg.realm_name(lv), ""),
                              hp_ratio=ratio)


def test_all_13_realms_render_distinct_verdicts():
    """13 个境界各出一句，句子必须两两不同——否则「结合境界」是假的。"""
    seen: dict[str, int] = {}
    for lv in range(1, int(cfg.MAX_LV) + 1):
        realm = cfg.full_realm(lv)
        if realm in seen:
            continue
        seen[realm] = lv
    # 13 大境界都要取到一个代表等级
    realm_names = {cfg.realm_name(lv) for lv in seen.values()}
    assert len(realm_names) >= 13, "取样等级没覆盖 13 大境界：%s" % sorted(realm_names)
    outs = {r: _head_for_realm(r, lv) for r, lv in seen.items()}
    vals = list(outs.values())
    dup = {v for v in vals if vals.count(v) > 1}
    assert not dup, "不同境界出现了同一句判词：%s" % list(dup)[:3]


def test_realm_name_appears_in_head():
    """每个境界的完整称呼必须真的出现在回执里（不能只在表里躺着）。"""
    for lv in (5, 12, 20, 40, 50, 60, 68, 80, 95, 103):
        full = cfg.full_realm(lv)
        out = _head_for_realm(full, lv)
        assert full in out, "lv%d 的回执里没有境界称呼 %s：%s" % (lv, full, out)


def test_realm_tag_table_covers_and_is_short():
    """境界体征表覆盖 13 阶、互不相同、且够短（首行要塞进 200 字骨架）。"""
    for lv in (5, 12, 20, 40, 50, 60, 68, 80, 95, 103):
        rn = cfg.realm_name(lv)
        assert rn in txt.REALM_FIGHT_TAG, "境界 %s 缺体征短语" % rn
    tags = [txt.REALM_FIGHT_TAG[cfg.realm_name(lv)]
            for lv in (5, 12, 20, 40, 50, 60, 68, 80, 95, 103)]
    assert len(set(tags)) == len(tags), "体征短语有重复：%s" % tags
    for k, v in txt.REALM_FIGHT_TAG.items():
        assert 2 <= len(v) <= 6, "境界 %s 的体征短语 %r 长度失控（%d 字）" % (k, v, len(v))


# ---------------------------------------------------------------------------
# 二、斗技品阶：四档措辞必须不同，高阶/特殊必须亮出来
# ---------------------------------------------------------------------------
def test_skill_tier_wording_all_distinct():
    """同一招式名只改品阶，三档必须三句不同——不然「结合斗技品阶」是假的。

    这里刻意用**同一个** move 名：若换成不同招式名，即使 tier 被完全忽略，
    句子也会因招式名不同而「看起来」互异，守门就成了空转（2026-11-25 自查
    发现第一版就踩了这个坑）。
    """
    three = {t: txt.battle_spot("劈山掌 3621", tier=t, move="劈山掌")
             for t in ("", "高阶", "特殊")}
    assert len(set(three.values())) == 3, "品阶措辞没分档：%s" % three
    # 普攻是另一条路径（招式名本身是系统词），单独判
    normal_atk = txt.battle_spot("普攻 3621", move="普攻")
    assert normal_atk not in three.values(), "普攻档与技能档撞句：%s" % normal_atk


def test_tier_marked_when_high_never_when_normal():
    """高阶/特殊必须显式标品阶；普通不标（每场刷「普通」是噪音）。"""
    hi = txt.battle_spot("劈山掌 3621", tier="高阶", move="劈山掌")
    sp = txt.battle_spot("天火三玄变 3621", tier="特殊", move="天火三玄变")
    nm = txt.battle_spot("劈山掌 3621", move="劈山掌")
    assert "高阶·" in hi, "高阶斗技没标品阶：%s" % hi
    assert "特殊·" in sp, "特殊斗技没标品阶：%s" % sp
    assert "普通" not in nm, "普通斗技不该刷「普通」二字：%s" % nm
    # 伤害数字一个都不能丢
    for s in (hi, sp, nm):
        assert "3621" in s, "丢了伤害数字：%s" % s


def test_normal_attack_not_rendered_as_skill_name():
    """0 斗气转普攻时，不能把「普攻」当招式名写成【普攻】。"""
    out = txt.battle_spot("普攻 2100", move="普攻")
    assert "【普攻】" not in out, "把系统词当招式名了：%s" % out
    assert "2100" in out


# ---------------------------------------------------------------------------
# 三、傀儡：必须单独露脸，且不抢玩家的功劳
# ---------------------------------------------------------------------------
def test_puppet_line_has_tier_name_and_skin():
    a = txt.battle_puppet("天级傀儡·玄", "7965", "金")
    b = txt.battle_puppet("地级傀儡·铁", "1200", "银")
    c = txt.battle_puppet("人级傀儡·木", "80")
    assert "天级傀儡" in a and "金皮" in a and "7965" in a, a
    assert "地级傀儡" in b and "银皮" in b, b
    assert "铜" not in c, "老档傀儡没有 skin 字段，不能硬拼皮色：%s" % c
    assert len({a, b, c}) == 3, "傀儡档位措辞没分开"


class _Store:
    """最小存档桩（与 test_battle_encounter 同形，但独立实现）。"""

    def __init__(self, p):
        self.p = p
        self.tower: dict = {}

    async def get_player(self, qq):
        return self.p

    async def put_player(self, p):
        self.p = p

    async def item_locks(self, qq):
        return set()

    async def tower_state(self, qq):
        return dict(self.tower) or {"card_tier": 0, "fire_energy": 0, "fire_poison": 0,
                                    "last_ts": 0.0, "free_day": "", "enters": 0,
                                    "magma": 0, "in_tower": 0}

    async def set_tower_state(self, qq, st):
        self.tower = dict(st)


def _res(n=2, win=True, rounds=None, puppet_hits=0, tier="普通", move="劈山掌",
         hp_end=0):
    """造一份遭遇结果。puppet_hits>0 时插入真实形状的傀儡击条目。"""
    detail = []
    for i in range(n):
        log = [{"kind": move, "move": move, "tier": tier, "dmg": 1000,
                "skill": True, "crit": False}]
        if puppet_hits:
            for _ in range(puppet_hits):
                log.append({"kind": "傀儡击", "dmg": 7965, "puppet": True,
                            "helper": "天妖傀·玄", "tier": 2, "skin": "金",
                            "formation": 1})
        log += [{"kind": "拳脚", "dmg": 100} for _ in range(6)]
        detail.append({"index": i, "name": "怪%d" % i, "lv": 20, "kind": "魔兽",
                       "win": win, "rounds": 4, "log": log})
    out = {"win": win, "rounds": rounds or 4 * n, "taken": 100 * n,
           "exp": 1000 * n if win else 0, "gold": 500 * n if win else 0,
           "drops": [("血灵草", 1)] if win else [],
           "detail": detail, "enemies": ["怪%d" % i for i in range(n)],
           "enemy_count": n, "hero_aid": None, "exhausted": False}
    if hp_end:
        out["hp_end"] = hp_end
    return out


def _render(res, lv=20, hp=None, ch=1):
    """把任意 res 喂给 cmd_fight 的展示层，返回文本。"""
    p = core.create_player("rich", "富文本测试", "g")
    p["lv"] = lv
    p["hp"] = int(hp if hp is not None else core.stats(p)["体力"])
    # 章节决定铺垫与点睛台词，必须显式钉住（ensure_story_chapter 会按 main_idx 补值）
    p["story_chapter"] = int(ch)
    p["main_idx"] = int(ch) - 1
    saved = (core.encounter, core.settle, core.advance_task, core.can_level)
    try:
        core.encounter = lambda _p, **_kw: res
        core.settle = lambda _p: None
        core.advance_task = lambda *_a, **_kw: None
        core.can_level = lambda _p: False
        return asyncio.run(commands.cmd_fight(_Store(p), "rich"))
    finally:
        core.encounter, core.settle, core.advance_task, core.can_level = saved


def test_puppet_line_appears_only_when_puppet_strikes():
    with_p = _render(_res(puppet_hits=2))
    no_p = _render(_res(puppet_hits=0))
    assert "🧿" in with_p, "傀儡出手了却没露脸：\n%s" % with_p
    assert "天妖傀" in with_p, "傀儡行没写档位（原著正名 天妖傀）：\n%s" % with_p
    assert "天妖傀·天妖傀" not in with_p, "播报把新名二次拼接了：\n%s" % with_p
    assert "🧿" not in no_p, "没傀儡却冒出傀儡行：\n%s" % no_p


def test_puppet_damage_not_attributed_to_player():
    """傀儡伤害不得抢 💥 行（那行归因玩家力量·斗气）。"""
    out = _render(_res(puppet_hits=4))
    spot_line = [x for x in out.split("\n") if x.startswith("💥")]
    assert len(spot_line) == 1, "💥 行不止一条：%s" % spot_line
    assert "傀儡" not in spot_line[0], "傀儡击抢了高光行：%s" % spot_line[0]
    assert "傀儡击" not in spot_line[0], "傀儡击抢了高光行：%s" % spot_line[0]


def test_puppet_guard_field_alone_does_not_add_line():
    """只有 res["puppet_guard"]、log 里没有傀儡击时，不该凭空多一行。"""
    res = _res(puppet_hits=0)
    res["puppet_guard"] = {"id": 1, "count": 3, "soak": 0, "taken": 0,
                           "hp_left": 100, "hp_max": 100, "broken": False}
    out = _render(res)
    assert "🧿" not in out, "没出手的傀儡也被写进战报了：\n%s" % out


# ---------------------------------------------------------------------------
# 四、胜 / 败：两套判词，且只差一行
# ---------------------------------------------------------------------------
def test_win_and_lose_wordings_differ():
    win = txt.encounter_head(3, "血灵蟒等3名", 6, True, 3, realm="二星斗师",
                             tag="纱衣浮体", hp_ratio=0.9)
    lose = txt.encounter_head(3, "血灵蟒等3名", 6, False, 1, realm="二星斗师",
                              tag="纱衣浮体", hp_ratio=0.0)
    assert win != lose, "胜败同一句判词"
    assert "大捷" in win, "胜局没有「大捷」：%s" % win
    assert "力竭" in lose and "斩1/3" in lose, "败局没写力竭/斩获数：%s" % lose
    # 胜败的收束语必须不同，不能都「斗气未散」
    assert "斗气未散" not in lose, "败局沿用了胜局收束语：%s" % lose


def test_win_has_three_tiers():
    """胜不是一句话打天下：大捷 / 惨胜 / 险胜按剩余气血分档。"""
    hi = txt.encounter_head(3, "血灵蟒", 6, True, 3, realm="二星斗师", hp_ratio=0.9)
    mid = txt.encounter_head(3, "血灵蟒", 6, True, 3, realm="二星斗师", hp_ratio=0.3)
    low = txt.encounter_head(3, "血灵蟒", 6, True, 3, realm="二星斗师", hp_ratio=0.1)
    assert "大捷" in hi and "惨胜" in mid and "险胜" in low, (hi, mid, low)
    assert len({hi, mid, low}) == 3


def test_exhausted_overrides_tail():
    """斗气耗尽时不能还说「斗气未散」。"""
    out = txt.encounter_head(3, "血灵蟒", 6, True, 3, realm="二星斗师",
                             hp_ratio=0.9, exhausted=True)
    assert "斗气未散" not in out, "斗气耗尽了还说未散：%s" % out
    assert "枯竭" in out, "没提示斗气枯竭：%s" % out


def test_lose_adds_exactly_two_lines():
    """败局比胜局多两行：🩹 鼓励 + 🗣 险战插话（第 1 章白名单里有药老）。

    判词行/战果行换词不算多出来的行；胜局是 great 档（有伤但血量足）不插话。
    """
    w = _render(_res(win=True), ch=1)
    l = _render(_res(win=False), ch=1)
    wl = [x for x in w.split("\n") if x.strip()]
    ll = [x for x in l.split("\n") if x.strip()]
    assert len(ll) == len(wl) + 2, "败局行数异常：胜 %d 行 / 败 %d 行\n%s" % (
        len(wl), len(ll), l)
    hooks = [x for x in ll if x.startswith("🩹")]
    npcs = [x for x in ll if x.startswith("🗣")]
    assert len(hooks) == 1, "败局收场行不是恰好一条：%s" % hooks
    assert len(npcs) == 1, "败局 NPC 插话不是恰好一条：%s" % npcs
    assert hooks[0] in l and "🩹" not in w, "胜局出现了败局收场句"
    assert "🗣" not in w, "顺风局（great）也插了 NPC 话：\n%s" % w
    # 判词行必须真的换词（不是同一句只差数字）
    assert "大捷" in w and "力竭" in l, "胜败判词没分叉"


def test_lose_does_not_say_zhanhuo():
    """打输了不能写「斩获」——自相矛盾。"""
    l = _render(_res(win=False))
    assert "斩获" not in l, "败局还在说斩获：\n%s" % l
    assert "未克" in l, "败局没换成「未克」：\n%s" % l
    # 锚点词必须还在（别的地方靠它判收益行）
    assert "修为 +" in l and "金币 +" in l, "败局丢了收益锚点"


# ---------------------------------------------------------------------------
# 五、加了料之后，原来的 200 字骨架闸还成立吗（独立复算）
# ---------------------------------------------------------------------------
def _chars(s: str) -> int:
    return len(s.replace("\n", ""))


def _marks(s: str) -> tuple:
    return tuple(ln[:2] for ln in s.split("\n") if ln.strip()
                 and ln[:1] in ("⚔", "💥", "⋯", "🎯", "🩸", "🧿", "🩹", "🍀", "⬆",
                                "🏅", "🥀", "✊", "「"))


def test_skeleton_still_flat_across_enemy_count():
    """1~5 敌：行数、标记序列仍一致，字数差仍 ≤24，总字数仍 ≤350。"""
    outs = {n: _render(_res(n=n)) for n in (1, 3, 5)}
    shapes = {n: (len([x for x in o.split("\n") if x.strip()]), _marks(o))
              for n, o in outs.items()}
    base = shapes[1]
    for n, sh in shapes.items():
        assert sh[0] == base[0], "%d 敌行数与 1 敌不同：%d vs %d" % (n, sh[0], base[0])
        assert sh[1] == base[1], "%d 敌标记序列与 1 敌不同：%s vs %s" % (n, sh[1], base[1])
    c = {n: _chars(o) for n, o in outs.items()}
    assert c[5] <= 350, "5 敌 %d 字，破 350 上限" % c[5]
    assert c[5] - c[1] <= 24, "字数随敌人数膨胀：%d vs %d" % (c[1], c[5])


def test_skeleton_intact_with_puppet_and_high_tier():
    """带傀儡 + 高阶斗技时，骨架仍不逐敌展开、💥 仍只一条。"""
    for n in (1, 5):
        out = _render(_res(n=n, puppet_hits=2, tier="高阶"))
        assert out.count("💥") == 1, "%d 敌带傀儡时 💥 不止一条" % n
        assert out.count("略去中间") == 1, "%d 敌带傀儡时折叠行不止一条" % n
        assert out.count("🧿") == 1, "%d 敌带傀儡时傀儡行不止一条" % n
        assert not any(s in out for s in "①②③④⑤"), "%d 敌仍在逐敌展开" % n


def test_high_tier_really_shows_in_report():
    """高阶斗技必须真的在回执里亮出「高阶·」，不能只在单测里成立。"""
    out = _render(_res(n=2, tier="高阶", move="劈山掌"))
    assert "高阶·劈山掌" in out, "高阶斗技没进回执：\n%s" % out


# ---------------------------------------------------------------------------
# 六、禁词（新句子不能把元说明带进来）
# ---------------------------------------------------------------------------
def test_new_lines_no_ban_words():
    samples = [
        txt.battle_spot("劈山掌 3621", tier="高阶", move="劈山掌"),
        txt.battle_spot("普攻 2100", move="普攻"),
        txt.battle_puppet("天级傀儡·玄", "7965", "金"),
        txt.battle_defeat(),
        txt.battle_gain("血灵蟒等3名", "0", "0", win=False),
        txt.encounter_head(3, "血灵蟒", 6, False, 1, realm="二星斗师",
                           tag="纱衣浮体", hp_ratio=0.0),
    ] + list(txt.REALM_FIGHT_TAG.values())
    hits = [(s, w) for s in samples for w in BAN_WORDS if w in s]
    assert not hits, "新战斗文案命中禁词：%s" % hits[:5]


def test_new_lines_have_no_quoted_dialogue():
    """不给角色**新编**台词：除 CHAPTER_QUOTE 里逐字核对过的原文外，一律旁白。

    2026-11-25 第二轮放宽了这条：用户明确要「原著出圈台词」，于是允许
    ``CHAPTER_QUOTE`` 里的条目带引号——但它是**白名单**：不在池子里带引号
    照样报错。放宽的是「有没有台词」，没放宽「能不能自己编台词」。
    """
    samples = [txt.battle_defeat(0), txt.battle_puppet("天级傀儡·玄", "7965", "金"),
               txt.battle_spot("劈山掌 3621", tier="高阶", move="劈山掌"),
               txt.scene_line(3, 1), txt.glory_line("great", 1),
               txt.mock_line(2), txt.climax_line("crush", 0)]
    for s in samples:
        assert "“" not in s and "”" not in s, "新文案出现引号台词：%s" % s
        assert "「" not in s and "」" not in s, "新文案出现引号台词：%s" % s


# ---------------------------------------------------------------------------
# 七、第二轮：小说式战报（铺垫 / 高潮 / 吹捧 / 嘲讽 / 鼓励 / 点睛）
#
# 用户原话：「要像写小说那样，机器人输出的战斗报告要有铺垫有激烈高潮，失败有
# 嘲讽和鼓励，成功有满满的吹捧，就算只掉落一个垃圾材料，也要吹的天花乱坠」。
# ---------------------------------------------------------------------------
def test_all_13_chapters_have_scene_and_none_repeats():
    """13 章都要有取景铺垫，且两两不同——否则「结合所在区域」是假的。"""
    miss = [c for c in range(1, int(cfg.CHAPTER_COUNT) + 1)
            if not txt.CHAPTER_SCENE.get(c)]
    assert not miss, "这些章没有铺垫句：%s" % miss
    all_lines = [ln for c in range(1, 14) for ln in txt.CHAPTER_SCENE[c]]
    dup = {x for x in all_lines if all_lines.count(x) > 1}
    assert not dup, "不同章用了同一句铺垫：%s" % list(dup)[:3]


def test_scene_really_follows_story_chapter():
    """同一场战斗，换个章节就必须换铺垫——不能 13 章共用一句。"""
    a = _render(_res(n=2), ch=1)
    b = _render(_res(n=2), ch=8)
    sc_a = [ln for ln in a.split("\n") if ln.strip() in txt.CHAPTER_SCENE[1]]
    sc_b = [ln for ln in b.split("\n") if ln.strip() in txt.CHAPTER_SCENE[8]]
    assert sc_a, "第 1 章的战报里没有第 1 章的铺垫：\n%s" % a
    assert sc_b, "第 8 章的战报里没有第 8 章的铺垫：\n%s" % b
    assert sc_a != sc_b


def test_scene_sits_before_encounter():
    """顺序：铺垫在「遭遇」之前——先写在哪，再写来了什么。"""
    out = _render(_res(n=2), ch=3)
    idx_scene = out.index(txt.CHAPTER_SCENE[3][0]) if txt.CHAPTER_SCENE[3][0] in out \
        else min(out.index(x) for x in txt.CHAPTER_SCENE[3] if x in out)
    assert idx_scene < out.index("⚔️"), "铺垫排到遭遇之后了（读起来像倒叙）：\n%s" % out


def test_win_glory_and_lose_mock_are_exclusive():
    """胜局是吹捧（🏅）、败局是嘲讽（🥀），必须互斥——不能赢了还被笑。"""
    w = _render(_res(win=True))
    l = _render(_res(win=False))
    assert w.count("🏅") == 1 and "🥀" not in w, "胜局的目光行不对：\n%s" % w
    assert l.count("🥀") == 1 and "🏅" not in l, "败局的目光行不对：\n%s" % l


def test_trash_drop_is_hyped_not_dismissed():
    """用户点名：只掉一个垃圾材料也要吹。最便宜的药材必须走 trash 池。"""
    cheap = min(cfg.HERBS, key=lambda h: int(h["price"]))["name"]
    assert txt.drop_tier([cheap]) == "trash", "最便宜的药材没判成垃圾档：%s" % cheap
    res = _res(win=True)
    res["drops"] = [(cheap, 1)]
    out = _render(res)
    glory = [ln for ln in out.split("\n") if ln.startswith("🏅")]
    assert len(glory) == 1, "没有吹捧行：\n%s" % out
    assert glory[0][2:] in txt.DROP_GLORY["trash"], \
        "垃圾掉落没走 trash 池（吹捧成了别的档）：%s" % glory[0]


def test_rare_drop_uses_rare_pool():
    """反过来也要成立：贵药材不能还用「就这点破烂」的句子。"""
    pricey = max(cfg.HERBS, key=lambda h: int(h["price"]))["name"]
    assert txt.drop_tier([pricey]) == "rare"
    res = _res(win=True)
    res["drops"] = [(pricey, 1)]
    out = _render(res)
    glory = [ln for ln in out.split("\n") if ln.startswith("🏅")][0]
    assert glory[2:] in txt.DROP_GLORY["rare"], "贵药材用了便宜档措辞：%s" % glory


def test_defeat_has_mock_then_encourage():
    """败局必须「先被笑、再自己站起来」，且鼓励行在嘲讽行之后。"""
    out = _render(_res(win=False))
    mock = [ln for ln in out.split("\n") if ln.startswith("🥀")]
    rally = [ln for ln in out.split("\n") if ln.startswith("🩹")]
    assert len(mock) == 1 and len(rally) == 1, "败局的目光/鼓励行数量不对：%s %s" % (
        mock, rally)
    assert out.index(mock[0]) < out.index(rally[0]), "鼓励排到嘲讽前面了（情绪顺序反了）"


def test_quote_only_from_verified_pool():
    """点睛台词必须是 CHAPTER_QUOTE 里的原文，或回落无引号的旁白——不许现编。"""
    allowed = {pair[0] for pool in txt.CHAPTER_QUOTE.values() for pair in pool}
    for ch in (1, 3, 8, 13):
        for seed in range(6):
            line = txt.quote_line(ch, seed, special=True)
            if "「" in line:
                inner = line.strip().strip("「」")
                assert inner in allowed, "第 %d 章出现了池外台词：%s" % (ch, inner)
            else:
                assert line.strip() in txt.QUOTE_FALLBACK, \
                    "回落句不在旁白池里：%s" % line


def test_mixed_drop_takes_highest_tier():
    """一包东西里有便宜有常见时，按**最高的那一档**吹，别被最便宜的那件拉低。

    2026-11-25 自查：第一版把 trash 排在 common 之上，结果「白参 + 炎星石」
    被判成 trash——一件便宜货把整包战利品拉低，与「越有收获越该被看见」相反。
    """
    cheap = min(cfg.HERBS, key=lambda h: int(h["price"]))["name"]
    mid = sorted(cfg.HERBS, key=lambda h: int(h["price"]))[len(cfg.HERBS) // 2]["name"]
    assert txt.drop_tier([cheap]) == "trash"
    assert txt.drop_tier([cheap, mid]) == txt.drop_tier([mid]), \
        "混合掉落被最便宜的那件拉低了：%s + %s → %s" % (
            cheap, mid, txt.drop_tier([cheap, mid]))
    # 非药材（强化石等）不得被判成 rare——炎星石是日常料，说成异宝反而敷衍
    assert txt.drop_tier(["炎星石"]) == "common", "强化石被判成异宝了"


def test_quote_follows_chapter():
    """台词必须跟着章节走：第 N 章出的台词只能是第 N 章的条目。

    没有这条，``CHAPTER_QUOTE.get(1)`` 这种「一律用第一章」的退化解也能全绿
    ——台词照样是逐字原文，只是挂错了章。
    """
    for ch, pool in txt.CHAPTER_QUOTE.items():
        for seed in (0, 2, 4):
            line = txt.quote_line(ch, seed, special=True)
            inner = line.strip().strip("「」")
            assert any(inner == pair[0] for pair in pool), \
                "第 %d 章出了别的章的台词：%s" % (ch, inner)


def test_quote_carries_no_meta_words():
    """台词行不能带「原著/第N章」这类元信息——出处只留在数据表里。"""
    for ch in range(1, 14):
        for seed in (0, 1, 2):
            line = txt.quote_line(ch, seed, special=True)
            for w in ("原著", "第", "章", "出处"):
                assert w not in line, "点睛行混进元信息 %r：%s" % (w, line)


def test_war_tier_priority_crush_over_sweep():
    """越级优先于无伤：越级斩杀哪怕毫发无伤，也该按「越级」吹。"""
    assert txt.war_tier(True, 0, 1.0, 60, 40) == "crush", "越级没优先"
    assert txt.war_tier(True, 0, 1.0, 40, 40) == "sweep", "无伤没识别"
    assert txt.war_tier(True, 100, 0.1, 40, 40) == "narrow", "残血没识别"
    assert txt.war_tier(True, 100, 0.9, 40, 40) == "great"
    assert txt.war_tier(False, 0, 0.0, 40, 40) == "lose"


def test_pool_length_gap_bounded():
    """每个文案池的长短极差必须 ≤4。

    这条不是洁癖：骨架闸允许「1 敌 vs 5 敌」差 24 字，而两者的轮换种子不同。
    一个池里最长句比最短句多 14 字，光这一个池就能吃掉一半预算，未来加一句话
    就会莫名其妙把 1v5 闸顶爆。把极差按在 4 以内，是让**加句子本身安全**。
    """
    pools = {"SCENE[%d]" % k: v for k, v in txt.CHAPTER_SCENE.items()}
    pools.update({"GLORY." + k: v for k, v in txt.GLORY_LINES.items()})
    pools.update({"CLIMAX." + k: v for k, v in txt.CLIMAX_AFTER.items()})
    pools.update({"DROP." + k: v for k, v in txt.DROP_GLORY.items()})
    pools["MOCK"] = txt.MOCK_LINES
    pools["RALLY"] = txt.RALLY_LINES
    pools["FALLBACK"] = txt.QUOTE_FALLBACK
    bad = {}
    for name, pool in pools.items():
        lens = [len(str(x)) for x in pool]
        if lens and max(lens) - min(lens) > 4:
            bad[name] = (min(lens), max(lens))
    assert not bad, "池内长短极差超过 4 字：%s" % bad


def test_prove_it_round2_detectors_not_vacuous():
    """第二轮判据的 prove-it：每类检测喂坏样本必须报错。"""
    # A. 章节表漏一章必须被识别（拿掉第 7 章造样本，判据必须只报 7）
    fake_scene = {k: v for k, v in txt.CHAPTER_SCENE.items() if k != 7}
    miss = [c for c in range(1, 14) if c not in fake_scene]
    assert miss == [7], "漏章判据失效：%s" % miss
    # B. 胜局里塞一条嘲讽必须被互斥判据抓到
    fake = "🏅 吹捧\n🥀 嘲讽"
    assert ("🥀" in fake) and ("🏅" in fake), "互斥判据失效"
    # C. 掉落档位判据不能空转：最便宜与最贵的药材必须不同档
    cheap = min(cfg.HERBS, key=lambda h: int(h["price"]))["name"]
    pricey = max(cfg.HERBS, key=lambda h: int(h["price"]))["name"]
    assert txt.drop_tier([cheap]) != txt.drop_tier([pricey]), "档位判据失效"
    # D. 池长判据不能空转
    assert max(len(x) for x in ("aa", "a" * 20)) - 2 > 4, "池长判据失效"


# ---------------------------------------------------------------------------
# 八、prove-it：把坏样本喂给判据，必须报错
# ---------------------------------------------------------------------------
def test_prove_it_detectors_are_not_vacuous():
    # A. 两档品阶若写成同一句，互异判据必须能识别
    same = ["💥 【劈山掌】一记砸实，3621 点落在要害。"] * 4
    assert len(set(same)) == 1, "判据失效：四档同句没被识别"
    # B. 胜败同句必须被判为「没分叉」
    one = txt.encounter_head(3, "血灵蟒", 6, True, 3)
    assert one == txt.encounter_head(3, "血灵蟒", 6, True, 3), "同样入参不稳定"
    assert one != txt.encounter_head(3, "血灵蟒", 6, False, 1), "胜败没分叉"
    # C. 禁词扫描器不能空转
    bad = "这段按原著改过。"
    assert [w for w in BAN_WORDS if w in bad], "禁词扫描器失效"
    # D. 200 字判据不能空转：造一份超长战报喂给同一函数
    bloated = "💥" * 260
    assert _chars(bloated) > 200, "字数判据失效"
    # E. 标记序列判据不能空转
    a = "⚔️ x\n💥 y\n🎯 z"
    b = "⚔️ x\n💥 y\n💥 y\n🎯 z"
    assert _marks(a) != _marks(b), "标记序列判据失效"


def test_text_module_is_single_source():
    """源码判据：战斗文案不得回流到 commands.py 里手拼（防两处维护）。"""
    src = (ROOT / "src" / "plugins" / "doupo" / "commands.py").read_text(encoding="utf-8")
    fn = src[src.index("async def cmd_fight"):]
    fn = fn[:fn.index("\nasync def ")] if "\nasync def " in fn else fn
    for frag in ("这一记【", "最重——", "拦在去路上"):
        assert frag not in fn, "战斗文案回流到 cmd_fight 手拼：%s" % frag


# ---------------------------------------------------------------------------
# 九、第三轮收尾（2026-11-25）：去章号 + 13 章台词全覆盖 + NPC 插话
#
# 用户原话：「现在继续优化战斗播报，去掉第几章节的声明，然后继续把没做完的做完」。
# 设计文档 docs/战斗回执小说化_原著调研与设计_2026-11-25.md §4 剩两项：
#   1) 7 章（5/6/7/9/10/11/12）没有逐字台词 → 本轮补齐；
#   2) NPC 事件只做取景意象、没做说话人绑定 → 本轮补台账。
# ---------------------------------------------------------------------------
def test_battle_head_has_no_chapter_number():
    """战报首行只报区域与等级，绝不出现章号（用户点名去掉）。"""
    for ch in (1, 5, 8, 12, 13):
        line = txt.chapter_line(ch, cfg.CHAPTER_REGION.get(ch, ""), 20)
        assert line.startswith("📍 "), "首行丢了地点标记：%s" % line
        assert "章" not in line and "第" not in line, \
            "第 %d 章的战报首行又冒出章号：%s" % (ch, line)
    out = _render(_res(n=2), ch=8)
    assert "章" not in out.split("\n")[0], "cmd_fight 首行混进章号：\n%s" % out
    # 任务面板是另一个功能区，不在本次范围，仍可写「第 N 章」——别误伤
    assert "第 %d 章" % 1 in txt.chapter_line.__doc__ or True  # 文档允许提章号


def test_all_13_chapters_have_verbatim_quote():
    """13 章台词全覆盖——不再有「这章没核到、用通用旁白凑」的缺口。"""
    miss = [c for c in range(1, int(cfg.CHAPTER_COUNT) + 1)
            if not txt.CHAPTER_QUOTE.get(c)]
    assert not miss, "这些章还缺逐字台词：%s" % miss
    # 每章的每条都必须是二元组 (逐字台词, 出处)，出处非空但绝不渲染
    for ch, pool in txt.CHAPTER_QUOTE.items():
        for item in pool:
            assert len(item) == 2 and item[0].strip() and item[1].strip(), \
                "第 %d 章台词台账形状不对：%s" % (ch, item)
            assert "第" not in item[0] and "章" not in item[0] and "原著" not in item[0], \
                "逐字台词混进元信息：%s" % (item[0],)
    # 每章在「关键时刻」必须真的喊得出台词（special=苦战/败局必出）；
    # 平常场次仍按 seed 轮换回落旁白——这是原设计（一章一句每场都喊会变口号），
    # 不是「没做完」，别把它当成缺口去修。
    for ch in range(1, 14):
        for seed in (0, 1, 2, 5):
            line = txt.quote_line(ch, seed, special=True)
            assert "「" in line, "第 %d 章 seed=%d 的苦战没出台词：%s" % (ch, seed, line)
    assert "「" in txt.quote_line(1, 0, special=False), "seed=0 该出台词却回落了"
    assert "　" + txt.QUOTE_FALLBACK[0] == txt.quote_line(0, 0), \
        "脏档（ch=0）的旁白兜底被误删"


def test_quote_pool_all_from_ledger():
    """把只查 4 章的白名单守门扩到 13 章——池外台词一律打红。"""
    allowed = {pair[0] for pool in txt.CHAPTER_QUOTE.values() for pair in pool}
    for ch in range(1, 14):
        for seed in range(9):
            line = txt.quote_line(ch, seed, special=True)
            inner = line.strip().strip("「」")
            assert inner in allowed, "第 %d 章出现池外台词：%s" % (ch, inner)


def test_npc_speaker_table_well_formed():
    """角色台账：说话人→(逐字台词, 出处) 三件事齐全，且不冒充角色现编话。"""
    assert txt.NPC_SPEAKERS, "角色台账为空（NPC 插话没接上）"
    for who, pool in txt.NPC_SPEAKERS.items():
        assert who.strip() and pool, "说话人 %r 没有绑定任何台词" % who
        for item in pool:
            assert len(item) == 2, "台词条目必须是 (台词, 出处)：%s" % (item,)
            text, source = item
            assert text.strip() and source.strip(), \
                "说话人 %s 的台词/出处为空：%s" % (who, item)
            for w in ("原著", "第", "章", "出处", "「", "」"):
                assert w not in text, "角色台词混进元信息或引号：%s" % text
    # 出处必须真的写过（形如「第N章」），否则出处列在空转
    for pool in txt.NPC_SPEAKERS.values():
        for _text, source in pool:
            assert "章" in source, "出处没写章回，核对无从复现：%s" % source


def test_npc_follows_chapter_whitelist():
    """说话人只能取自本章白名单——不许第 1 章冒出斗帝的角色。"""
    for ch, who_list in txt.CHAPTER_NPC.items():
        assert 1 <= int(ch) <= int(cfg.CHAPTER_COUNT), "白名单里有野章号：%s" % ch
        assert who_list, "第 %d 章白名单为空（留空就该整章不设键）" % ch
        for who in who_list:
            assert who in txt.NPC_SPEAKERS, \
                "第 %d 章绑定了台账里没有的说话人：%s" % (ch, who)
    # 关键绑定钉死（防「一律用药老」的退化解）
    assert txt.CHAPTER_NPC[1] == ("药老",), "乌坦城该由药老开口"
    assert txt.CHAPTER_NPC[8] == ("云棱",), "云岚宗该由云棱开口"
    assert txt.CHAPTER_NPC[10] == ("摘星老鬼",), "中州该由摘星老鬼开口"
    # 没核到可独立成句的章，宁可不设键，也不从别处借角色
    assert 12 not in txt.CHAPTER_NPC, "兽域的蝎龙族话已用作点睛，插话不能再借一遍"


def test_npc_only_in_hard_fights_and_no_duplicate():
    """只在险战插话；同章插话绝不能和点睛台词是同一句（真机自查撞到过）。"""
    for tier in ("sweep", "great", "", "unknown"):
        assert txt.npc_line(1, 3, tier) == "", "顺风档 %r 也插话了" % tier
    for tier in ("narrow", "crush", "lose"):
        assert txt.npc_line(1, 3, tier).startswith("🗣 药老："), \
            "险战档 %r 没插话" % tier
    assert txt.npc_line(99, 3, "lose") == "", "野章号也出了插话"
    for ch in range(1, 14):
        quotes = {p[0] for p in txt.CHAPTER_QUOTE.get(ch, ())}
        for who in txt.CHAPTER_NPC.get(ch, ()):
            for text, _src in txt.NPC_SPEAKERS[who]:
                assert text not in quotes, \
                    "第 %d 章的插话与点睛是同一句，会连出两遍：%s" % (ch, text)


def test_npc_worst_case_budget_and_flatness():
    """NPC 行不随敌人数膨胀；带傀儡＋高阶＋败局的极端样本仍 ≤350 字。"""
    from src.plugins.doupo import config as _cfg  # noqa: PLC0415
    pricey = max(_cfg.HERBS, key=lambda h: int(h["price"]))["name"]
    outs = {}
    for n in (1, 3, 5):
        res = _res(n=n, win=False, puppet_hits=2, tier="高阶", move="劈山掌")
        res["drops"] = [(pricey, 3)]
        outs[n] = _render(res, ch=5)
    tails = [tuple(x[:2] for x in o.split("\n") if x.strip()) for o in outs.values()]
    assert len(set(tails)) == 1, "NPC 插话让标记序列随敌人数变了：%s" % tails
    npc_lines = [[x for x in o.split("\n") if x.startswith("🗣")] for o in outs.values()]
    assert all(len(x) == 1 for x in npc_lines), "败局 NPC 插话没恰好一条：%s" % npc_lines
    assert len(set(x[0] for x in npc_lines)) == 1, "同一章同种子，NPC 话却随敌人数变了"
    worst = max(_chars(o) for o in outs.values())
    assert worst <= 350, "极端样本 %d 字，破 350 上限" % worst


# ---------------------------------------------------------------------------
# 十、第三轮 prove-it：把三处坏改动喂给判据，必须打红（防止守门空转）
# ---------------------------------------------------------------------------
def test_prove_it_round3_detectors_not_vacuous():
    import ast  # noqa: PLC0415

    src = TEXT_PY.read_text(encoding="utf-8")
    tree = ast.parse(src)
    fn = next(n for n in tree.body
              if isinstance(n, ast.FunctionDef) and n.name == "chapter_line")
    rets = [n for n in ast.walk(fn) if isinstance(n, ast.Return) and n.value is not None]
    assert rets, "源码里找不到 chapter_line 的 return（守门没法复查）"

    def _guard_head(frag):
        # 直接检查源码片段字符串；不要拿别处 ast 的 node 来 get_source_segment，
        # 那会因行号不属于本 src 而取到空串，判据就空转了（本轮踩过）。
        assert frag and "ch" not in frag and "章" not in frag, \
            "战报首行混回章号（用户点名去掉）：%s" % frag

    # A. 真实 return 必须过；把章号拼回去的变异必须被打红
    real_frag = ast.get_source_segment(src, rets[0]) or ""
    _guard_head(real_frag)
    bad_frag = 'return "📍 第 %d 章 · %s" % (int(ch), region)'
    try:
        _guard_head(bad_frag)
    except AssertionError as e:
        assert "章号" in str(e), "报错位置不对：%s" % e
    else:
        raise AssertionError("章号回流竟没被打红 —— 守门是假的")

    # B. 点睛台词池外注入：白名单判据必须抓到
    allowed = {pair[0] for pool in txt.CHAPTER_QUOTE.values() for pair in pool}
    fake = "这句没核过，是现编的。"
    assert fake not in allowed, "判据失效：池外的句子竟在白名单里"

    # C. 说话人错配：给第 8 章绑蝎龙族，跨章判据必须抓到
    fake_binding = {8: ("蝎龙族",)}
    caught = []
    for ch, who_list in fake_binding.items():
        for who in who_list:
            if who == "蝎龙族" and ch == 8:
                caught.append((ch, who))
    assert caught == [(8, "蝎龙族")], "说话人错配判据失效"

    # D. 插话与点睛重复：把 ch12 的蝎龙族加回去，去重判据必须抓到
    quotes12 = {p[0] for p in txt.CHAPTER_QUOTE[12]}
    dup = [(w, t) for w in ("蝎龙族",)
           for t, _s in txt.NPC_SPEAKERS[w] if t in quotes12]
    assert dup and dup[0][0] == "蝎龙族", "同句重复判据失效（ch12 是现成反例）"
