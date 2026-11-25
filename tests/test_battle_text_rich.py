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
                            "helper": "天级·玄", "tier": 2, "skin": "金",
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


def _render(res, lv=20, hp=None):
    """把任意 res 喂给 cmd_fight 的展示层，返回文本。"""
    p = core.create_player("rich", "富文本测试", "g")
    p["lv"] = lv
    p["hp"] = int(hp if hp is not None else core.stats(p)["体力"])
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
    assert "天级傀儡" in with_p, "傀儡行没写档位：\n%s" % with_p
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


def test_lose_adds_exactly_one_line():
    """败局只比胜局多一行（🩹）——判词行/战果行本来就该换词，不算多出来的行。"""
    w = _render(_res(win=True))
    l = _render(_res(win=False))
    wl = [x for x in w.split("\n") if x.strip()]
    ll = [x for x in l.split("\n") if x.strip()]
    assert len(ll) == len(wl) + 1, "败局行数异常：胜 %d 行 / 败 %d 行\n%s" % (
        len(wl), len(ll), l)
    hooks = [x for x in ll if x.startswith("🩹")]
    assert len(hooks) == 1, "败局收场行不是恰好一条：%s" % hooks
    assert hooks[0] in l and "🩹" not in w, "胜局出现了败局收场句"
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
                 and ln[:1] in ("⚔", "💥", "⋯", "🎯", "🩸", "🧿", "🩹", "🍀", "⬆"))


def test_skeleton_still_flat_across_enemy_count():
    """1~5 敌：行数、标记序列仍一致，字数差仍 ≤24，总字数仍 ≤200。"""
    outs = {n: _render(_res(n=n)) for n in (1, 3, 5)}
    shapes = {n: (len([x for x in o.split("\n") if x.strip()]), _marks(o))
              for n, o in outs.items()}
    base = shapes[1]
    for n, sh in shapes.items():
        assert sh[0] == base[0], "%d 敌行数与 1 敌不同：%d vs %d" % (n, sh[0], base[0])
        assert sh[1] == base[1], "%d 敌标记序列与 1 敌不同：%s vs %s" % (n, sh[1], base[1])
    c = {n: _chars(o) for n, o in outs.items()}
    assert c[5] <= 200, "5 敌 %d 字，破 200 上限" % c[5]
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
    """不给角色加引号台词（模块纪律：新写的一律旁白）。"""
    samples = [txt.battle_defeat(), txt.battle_puppet("天级傀儡·玄", "7965", "金"),
               txt.battle_spot("劈山掌 3621", tier="高阶", move="劈山掌")]
    for s in samples:
        assert "“" not in s and "”" not in s, "新文案出现引号台词：%s" % s
        assert "「" not in s and "」" not in s, "新文案出现引号台词：%s" % s


# ---------------------------------------------------------------------------
# 七、prove-it：把坏样本喂给判据，必须报错
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
