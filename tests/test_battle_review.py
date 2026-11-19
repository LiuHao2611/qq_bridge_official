# -*- coding: utf-8 -*-
"""完整战斗回看 + 毒舌战斗评价门禁（2026-11-17）。

prove-it 反向验证：
* 把 core.record_last_battle 从 cmd_fight 拿掉 → test_cmd_fight_records_last_battle 必红；
* 把 fight() 的 mp_cost 固定写 0 → test_fight_log_has_full_trace 必红；
* 把 CARD_ACTIONS["battle"] 改成编造口令 → test_battle_card_button 必红；
* 把评语库清空 → test_comment_bank 必红。

运行：python tests/test_battle_review.py
"""
from __future__ import annotations

import asyncio
import importlib.util
import io
import json
import os
import random
import sys
import tempfile
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DP = os.path.join(ROOT, "src", "plugins", "doupo")


def _stub_nonebot():
    nb = types.ModuleType("nonebot")
    nb.get_driver = lambda: types.SimpleNamespace(on_startup=lambda f: f)

    def on_regex(*a, **k):
        m = types.SimpleNamespace()
        m.handle = lambda *a, **k: (lambda f: f)
        m.finish = lambda *a, **k: None
        m.priority = 5
        m.block = True
        return m
    nb.on_regex = on_regex
    sys.modules["nonebot"] = nb
    for n in ("nonebot.adapters", "nonebot.adapters.onebot",
              "nonebot.adapters.onebot.v11"):
        sys.modules[n] = types.ModuleType(n)
    v = sys.modules["nonebot.adapters.onebot.v11"]
    v.MessageEvent = object
    v.MessageSegment = object
    v.Message = object


_stub_nonebot()
for _p in (ROOT, os.path.join(ROOT, "src", "plugins")):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


from doupo import battle_comments as bc  # noqa: E402
from doupo import battle_review as br  # noqa: E402
from doupo import card  # noqa: E402
from doupo import button_dispatch  # noqa: E402
from doupo import commands  # noqa: E402
from doupo import config as cfg  # noqa: E402
from doupo import core  # noqa: E402
from doupo.store import Store  # noqa: E402

_CH = os.path.join(ROOT, "src", "plugins", "qq_bridge", "channel")
db = _load("doupo_buttons_br", os.path.join(_CH, "doupo_buttons.py"))
kb = _load("qq_keyboard_br", os.path.join(_CH, "qq_keyboard.py"))


def _player(qq="q1", mp=0):
    p = core.create_player(qq, "测试道友", "g1")
    p["hp"] = core.stats(p)["体力"]
    p["mp"] = mp
    return p


def _sample_battle() -> dict:
    p = _player(mp=600)
    mon = core.monster_scaled(p, core.cfg.MONSTERS[0])
    res = core.fight(p, mon, rng=random.Random(11).random)
    return core.record_last_battle(p, "遭遇", res, opponent=str(mon["name"]))


RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok), detail))


def test_fight_log_has_full_trace():
    p = _player(mp=0)  # 斗气为 0 → 必走武器普攻，验证「普攻也要逐条记录」
    mon = core.monster_scaled(p, core.cfg.MONSTERS[0])
    res = core.fight(p, mon, rng=random.Random(5).random)
    logs = [x for x in res["log"] if isinstance(x, dict)]
    check("战斗 log 非空", bool(logs), f"n={len(logs)}")
    check("逐条有招式名", all(x.get("move") for x in logs), str(logs[:1]))
    check("出现普通攻击", any(x.get("move") == "普攻" for x in logs),
          str([x.get("move") for x in logs]))
    check("逐条有气血/斗气快照",
          all("hp_after" in x and "mp_after" in x for x in logs), str(logs[:1]))
    check("普攻斗气消耗记为 0",
          all(int(x.get("mp_cost") or 0) == 0 for x in logs
              if x.get("move") == "普攻"), str(logs[:1]))
    skill_p = _player(mp=core.mp_max(_player()))
    skill_mon = core.monster_scaled(skill_p, core.cfg.MONSTERS[0])
    skill_res = core.fight(skill_p, skill_mon, rng=random.Random(3).random)
    spent = [int(x.get("mp_cost") or 0) for x in skill_res["log"]
             if isinstance(x, dict) and x.get("skill")]
    check("技能斗气消耗非零", bool(spent) and sum(spent) > 0, str(spent))


def test_record_last_battle_is_json_and_persistable():
    p = _player(mp=core.mp_max(_player()))
    mon = core.monster_scaled(p, core.cfg.MONSTERS[0])
    res = core.fight(p, mon, rng=random.Random(9).random)
    snap = core.record_last_battle(p, "遭遇", res, opponent=str(mon["name"]))
    raw = json.dumps(p["last_battle"], ensure_ascii=False)
    check("last_battle 可 JSON 序列化", bool(raw) and len(raw) > 100, f"bytes={len(raw)}")
    check("last_battle 字段齐备",
          all(k in snap for k in ("version", "ts", "kind", "win", "rounds",
                                  "opponent", "player", "summary", "log")),
          str(snap.keys()))
    check("log 条数与原战报一致", len(snap["log"]) == len(res["log"]),
          f"{len(snap['log'])} vs {len(res['log'])}")


def test_review_rows_include_resources_and_effects():
    battle = _sample_battle()
    rows = br.action_rows(battle)
    check("回看逐行非空", bool(rows) and rows[0] != "（本场没有逐招记录）", str(rows[:2]))
    check("回看含招式", any("回合" in r and "｜" in r for r in rows), str(rows[:1]))
    # 2026-11-19：气血/斗气残余从「战况」文字移到「终局」资源条（同一组数字不印两遍）。
    # 战况里再出现气血 = 信息重复，必须红。
    data = br.build_data(battle)
    head = data["groups"][0]["rows"]
    check("战况不再重复气血/斗气（已交给终局条）",
          not any("气血" in r for r in head), str(head))
    end = data.get("end") or {}
    check("终局资源条三行齐备",
          [r.get("label") for r in (end.get("rows") or [])] ==
          ["我气血", "我斗气", "敌气血"], str(end))
    # 战斗结束那一刻的真实气血：取最后一条 log 的 hp_after（负数按 0 算）
    logs = [x for x in (battle.get("log") or []) if isinstance(x, dict)]
    last_hp = max(0, int(logs[-1].get("hp_after"))) if logs else 0
    check("终局气血 = 战斗结束值（不是战后重置的 70%）",
          (end.get("rows") or [{}])[0].get("value") == last_hp,
          f"{end.get('rows')} last_hp={last_hp}")
    check("终局条画得出比例（有分母）",
          int((end.get("rows") or [{}])[0].get("max") or 0) > 0, str(end))
    check("收场原因已写进 verdict", bool(data["verdict"].get("end_text")),
          str(data["verdict"]))
    log_n = len([x for x in battle["log"] if isinstance(x, dict)])
    check("逐招一条一行（不再一条占两行）", len(rows) == log_n,
          f"rows={len(rows)} log={log_n}")
    text = br.text(battle)
    check("文本兜底含分组", "【战况】" in text and "【逐招明细】" in text, text[:80])
    synthetic = {"win": False, "rounds": 12, "player": {"hp_max": 1000, "mp": 0, "mp_max": 500},
                 "summary": {"taken": 700},
                 "log": [{"move": "普攻", "dmg": 1, "hp_after": 100,
                          "mp_after": 0, "mp_cost": 0, "notes": ["破甲"]}]}
    tags = br.tags(synthetic)
    for need in ("lose", "low_dmg", "no_mp", "high_taken", "many_rounds"):
        check(f"标签命中 {need}", need in tags, str(tags))


def test_damage_bars_encode_value():
    """条形长度必须编码数值：不同输出不得同长，相等输出才允许同长。"""
    a = br.damage_bars({"rounds": 1, "log": [{"dmg": 300}, {"dmg": 100, "taken": True}]})
    check("大的一方条更长", a[0]["ratio"] == 1.0 and a[1]["ratio"] < a[0]["ratio"],
          str([(b["label"], b["ratio"]) for b in a]))
    check("ratio 与数值成比例（不是写死的）",
          abs(a[1]["ratio"] - 100 / 300) < 0.01, str(a[1]["ratio"]))
    check("占比加总为 100", sum(b["share"] for b in a) == 100,
          str([b["share"] for b in a]))
    check("数值带千分位文案", a[0]["text"] == "300", a[0]["text"])

    eq = br.damage_bars({"rounds": 1, "log": [{"dmg": 100}, {"dmg": 100, "taken": True}]})
    check("等输出等长（不假装有差）", eq[0]["ratio"] == eq[1]["ratio"] == 1.0,
          str([b["ratio"] for b in eq]))

    zero = br.damage_bars({"rounds": 3, "log": [{"dmg": 90}]})
    check("敌方零输出 = 零长条", zero[1]["ratio"] == 0.0, str(zero[1]))

    big = br.damage_bars({"rounds": 1, "log": [{"dmg": 12846}, {"dmg": 3902, "taken": True}]})
    check("大数千分位", big[0]["text"] == "12,846", big[0]["text"])


def test_beats_cover_new_battle_events():
    """今天新加的战斗事件（心火共鸣/踉跄/自动服药）必须能进关键节点。"""
    log = [
        {"index": 1, "round": 1, "move": "普攻", "dmg": 100},
        {"index": 2, "round": 1, "move": "贵人相助", "dmg": 900, "aid": {"name": "药老"}},
        {"index": 3, "round": 2, "move": "心火共鸣", "dmg": 0, "kind": "心火共鸣",
         "fortune": True, "notes": ["心火冲顶，攻势自涨"]},
        {"index": 4, "round": 2, "move": "踉跄", "dmg": 0, "kind": "失误",
         "mishap": True, "notes": ["气机紊乱，这一手落了空"]},
        {"index": 5, "round": 3, "move": "服用", "pill": "三纹青灵丹", "amount": 500, "dmg": 0},
        {"index": 6, "round": 3, "move": "兽击", "dmg": 200, "taken": True},
        {"index": 7, "round": 4, "move": "控制", "dmg": 0, "kind": "被控", "miss": True},
        {"index": 8, "round": 4, "move": "反震", "dmg": 50, "reflect": True},
        {"index": 9, "round": 5, "move": "佛怒火莲", "dmg": 1200, "crit": True},
    ]
    battle = {"rounds": 5, "log": log, "win": True}
    allb = br.beats(battle, limit=9)
    got = [b["tag"] for b in allb]
    for need in ("机缘", "心火", "踉跄", "暴击", "服药", "被控", "反伤"):
        check(f"关键节点识别 {need}", need in got, str(got))
    check("关键节点按回合先后排", [b["round"] for b in allb] == sorted(b["round"] for b in allb),
          str([b["round"] for b in allb]))
    cap = br.beats(battle)
    check("默认最多 5 条", len(cap) <= br.BEAT_LIMIT, str(len(cap)))
    check("高优先级优先入选", "机缘" in [b["tag"] for b in cap], str([b["tag"] for b in cap]))
    # 心火/踉跄这类 0 伤害事件必须带说明，不能只剩一个类型标签
    texts = {b["tag"]: b["text"] for b in allb}
    check("心火带文案", "心火冲顶" in texts["心火"], texts["心火"])
    check("服药带数值", "500" in texts["服药"], texts["服药"])


def test_actions_hide_zero_damage_as_dash():
    battle = {"rounds": 2, "log": [
        {"index": 1, "round": 1, "move": "心火共鸣", "dmg": 0, "kind": "心火共鸣",
         "notes": ["心火冲顶"]},
        {"index": 2, "round": 1, "move": "服用", "pill": "回气散", "amount": 260, "dmg": 0},
        {"index": 3, "round": 2, "move": "佛怒火莲", "dmg": 3120, "crit": True},
    ]}
    acts = br.action_items(battle)
    check("零伤害事件写「—」", acts[0]["value"] == "—", acts[0]["value"])
    check("服药写 +回血", acts[1]["value"] == "+260", acts[1]["value"])
    check("真实伤害带千分位", acts[2]["value"] == "3,120", acts[2]["value"])
    toks = [t for t in acts[0]["tags"].split("·") if t]
    check("词条不复读招式名", "心火" not in toks, acts[0]["tags"])
    check("服药标记为丹药色", acts[1]["tone"] == "pill", acts[1]["tone"])


def test_totals_split_sides_for_duel():
    """切磋（who=v）没有 taken 字段，阵营判据若只看 taken 会把对手输出算成我方。"""
    battle = {"rounds": 3, "log": [
        {"index": 1, "round": 1, "move": "青莲剑歌", "dmg": 620, "who": "p"},
        {"index": 2, "round": 1, "move": "紫云翼", "dmg": 1480, "who": "v"},
        {"index": 3, "round": 2, "move": "紫云翼", "dmg": 1610, "who": "v"},
    ]}
    t = br.totals(battle)
    check("切磋我方输出只算自己", t["mine"] == 620, str(t))
    check("切磋敌方输出分离", t["foe"] == 3090, str(t))
    ae = br.action_items(battle)
    check("切磋敌方行标「对方」", ae[1]["side"] == "对方", ae[1]["side"])
    check("切磋敌方行用冷色", ae[1]["tone"] == "foe", ae[1]["tone"])


def test_miss_and_mishap_are_enemy_actions():
    """2026-11-19 实拍：闪避/踉跄/被控是敌人出手没打中，不是我方动作。

    core.fight 写这些条目时无 who、无 taken（没造成伤害），_actor 旧判据只看
    taken → 全归我方 → 气泡「我方」列混进敌人的闪避、「敌方」列反而空白。
    """
    battle = {"rounds": 4, "log": [
        {"index": 1, "round": 1, "move": "铁山拳", "dmg": 100},
        {"index": 2, "round": 1, "move": "踉跄", "dmg": 0, "kind": "失误", "mishap": True},
        {"index": 3, "round": 2, "move": "普攻", "dmg": 80},
        {"index": 4, "round": 2, "move": "闪避", "dmg": 0, "kind": "闪避", "miss": True},
        {"index": 5, "round": 3, "move": "普攻", "dmg": 80},
        {"index": 6, "round": 3, "move": "被控", "dmg": 0, "kind": "被控", "miss": True},
        {"index": 7, "round": 4, "move": "服药", "pill": "回气散", "amount": 200, "dmg": 0},
        {"index": 8, "round": 4, "move": "兽击", "dmg": 30, "taken": True},
    ]}
    acts = br.action_items(battle)
    sides = {a["idx"]: a["side"] for a in acts}
    check("踉跄归敌方", sides[2] == "敌方", str(sides))
    check("闪避归敌方", sides[4] == "敌方", str(sides))
    check("被控归敌方", sides[6] == "敌方", str(sides))
    check("正常攻击仍归我方", sides[1] == "我方" and sides[3] == "我方", str(sides))
    check("服药仍归我方", sides[7] == "我方", str(sides))
    # 「闪避」是玩家闪避了怪的反击，归敌方列后文案必须点明方向，
    # 否则读成「敌人闪避了」（用户 2026-11-19 实拍卡）。
    moves = {a["idx"]: a["move"] for a in acts}
    check("闪避文案点明方向", moves[4] == "被我方闪避", moves[4])
    check("踉跄不被改写", moves[2] == "踉跄", moves[2])
    check("被控不被改写", moves[6] == "被控", moves[6])
    # 之前这些回合的敌方列是空的；现在每回合两列都有动作
    bubbles = br.round_bubbles(battle)
    for b in bubbles:
        check("回合%d 我方列非空" % b["round"], bool(b["mine"]), str(b))
        check("回合%d 敌方列非空" % b["round"], bool(b["foe"]), str(b))


def test_multi_enemy_rounds_are_cumulative():
    """多敌遭遇战里每个对手的 fight() 都从第 1 回合重数，回看必须拉平成连续回合。"""
    battle = {"rounds": 4, "log": [
        {"index": 1, "round": 1, "foe": "血战佣兵", "move": "普攻", "dmg": 10},
        {"index": 2, "round": 1, "foe": "血战佣兵", "move": "兽击", "dmg": 5, "taken": True},
        {"index": 3, "round": 2, "foe": "血战佣兵", "move": "普攻", "dmg": 10},
        {"index": 4, "round": 1, "foe": "暴雪魔猿", "move": "普攻", "dmg": 10},
        {"index": 5, "round": 2, "foe": "暴雪魔猿", "move": "普攻", "dmg": 10},
    ]}
    acts = br.action_items(battle)
    rounds = [a["round"] for a in acts]
    check("多敌回合拉平连续", rounds == [1, 1, 2, 3, 4], str(rounds))
    check("同回合双方两条不误判换段", rounds[0] == rounds[1] == 1, str(rounds))
    check("最后一行与表头回合数一致", rounds[-1] == battle["rounds"],
          f"{rounds[-1]} vs {battle['rounds']}")
    check("行内不再出现两个「回合1」",
          sum(1 for r in br.action_rows(battle) if "回合1 " in r) == 2,
          str([r for r in br.action_rows(battle) if "回合1 " in r]))
    bb = br.beats({"rounds": 4, "log": [
        {"index": 1, "round": 2, "foe": "甲", "move": "普攻", "dmg": 10},
        {"index": 2, "round": 1, "foe": "乙", "move": "佛怒火莲", "dmg": 900, "crit": True},
    ]})
    check("关键节点用拉平后的回合", bb and bb[0]["round"] == 3, str(bb))


def test_last_battle_persists_new_events():
    """今天的战斗结论必须落进 last_battle，否则回看卡画不出力竭/自动服药/心火。"""
    p = _player("q9")
    res = {"win": True, "rounds": 300, "log": [], "taken": 0, "exp": 1, "gold": 1,
           "exhausted": True, "auto_pills": {"hp": 2, "mp": 1}, "fortune": True}
    snap = core.record_last_battle(p, "遭遇", res, opponent="测试傀儡")
    s = snap["summary"]
    check("落盘 exhausted", s.get("exhausted") is True, str(s.get("exhausted")))
    check("落盘 auto_pills", s.get("auto_pills") == {"hp": 2, "mp": 1},
          str(s.get("auto_pills")))
    check("落盘 fortune", s.get("fortune") is True, str(s.get("fortune")))
    v = br.build_data(snap)["verdict"]
    check("力竭进卡片 verdict", v["exhausted"] is True, str(v))
    raw = json.dumps(p["last_battle"], ensure_ascii=False)
    check("新字段可 JSON 序列化", "auto_pills" in raw and '"exhausted": true' in raw, raw[:120])


def test_comment_bank():
    texts = bc.all_texts()
    check("评语库非空", len(texts) >= 30, f"n={len(texts)}")
    check("评语不重复", len(set(texts)) == len(texts), f"n={len(texts)}")
    keys = ("伤害", "斗气", "血条", "回合", "战绩", "输出", "招式",
            "招", "操作", "战报", "战斗", "日志", "暴击", "闪避",
            "控制", "被控", "反伤", "贵人", "敌人", "赢", "寂寞", "战果")
    check("全部文本带嘲讽语境",
          all(any(k in t for k in keys) for t in texts),
          str([t for t in texts if not any(k in t for k in keys)]))
    battle = _sample_battle()
    a = bc.pick(battle, "q1", force_length="short")
    b = bc.pick(battle, "q1", force_length="short")
    long = bc.pick(battle, "q1", force_length="long")
    check("短评格式", a.startswith("【战斗评价】") and len(a) < 80, a)
    check("长评确实更长", len(long) > len(a), f"short={len(a)} long={len(long)}")
    check("同一场评价稳定", a == b, f"{a} vs {b}")


def test_cmd_fight_records_last_battle():
    async def run():
        with tempfile.TemporaryDirectory() as d:
            store = Store(os.path.join(d, "data.json"))
            p = _player("q2")
            await store.put_player(p)
            msg = await commands.cmd_fight(store, "q2")
            got = await store.get_player("q2")
            return msg, got
    msg, got = asyncio.run(run())
    check("战斗回包仍是高光骨架", "遭遇" in msg and "略去中间" in msg, msg[:80])
    last = got.get("last_battle") or {}
    check("cmd_fight 落盘 last_battle", last.get("kind") == "遭遇" and bool(last.get("log")),
          str(last.get("kind")))


def test_battle_card_and_button():
    battle = _sample_battle()
    data = card.build_battle_data(battle)
    check("卡片数据含战况与逐招", [g["name"] for g in data["groups"]] == ["战况", "逐招明细"],
          str([g["name"] for g in data["groups"]]))
    check("卡片数据带结算界面字段",
          all(data.get(k) for k in ("verdict", "kpis", "bars", "actions")),
          str(sorted(k for k in data if k != "groups")))
    check("胜负徽标字段齐全",
          isinstance(data["verdict"].get("win"), bool)
          and bool(data["verdict"].get("opponent"))
          and data["verdict"].get("rounds", 0) > 0, str(data["verdict"]))
    check("KPI 三格", len(data["kpis"]) == 3, str(data["kpis"]))
    pic = card._load_pic()
    check("pic 包可加载", pic is not None)
    if pic is None:
        return
    boxes = []
    from PIL import Image
    png = pic.render_sync("doupo_battle", "light", card.CARD_BATTLE_NAME,
                          width=720, data=data, boxes=boxes)
    img = Image.open(io.BytesIO(png))
    check("战斗卡输出 PNG", png.startswith(b"\x89PNG") and img.width == 720,
          f"size={img.size} bytes={len(png)}")
    check("boxes 全部在画布内",
          all(0 <= x1 < x2 <= img.width and 0 <= y1 < y2 <= img.height
              for _lb, x1, y1, x2, y2 in boxes),
          f"n={len(boxes)} h={img.height}")
    check("战斗卡按钮 = 战斗评价", db.CARD_ACTIONS.get("battle") == ("战斗评价",),
          str(db.CARD_ACTIONS.get("battle")))
    check("按钮口令真实", "战斗评价" in set(db.real_commands()), "缺真实口令")
    plan = button_dispatch.plan()
    check("按钮可分发", plan.get("战斗评价") == ("cmd_battle_comment", "qq", "")
          and "战斗评价" in db.ALL_BUTTON_CMDS, str(plan.get("战斗评价")))
    check("键盘派生同口令", [b.cmd for b in kb.buttons_for("doupo:battle")] == ["战斗评价"],
          str(kb.buttons_for("doupo:battle")))


def test_ruin_card_uses_legacy_skeleton():
    """傀儡遗迹复用 doupo_battle 骨架：没有新字段就必须走旧分组路径（像素级不变）。"""
    pic = card._load_pic()
    check("pic 包可加载（ruin）", pic is not None)
    if pic is None:
        return
    tpl = pic._doupo_battle
    ruin_data = {"title": "傀儡遗迹", "subtitle": "今日进度 1/3 · 每日重置",
                 "groups": [{"name": "第一段 · 魔炎谷库房",
                             "rows": ["守库尸卫 ｜ 掉 强者躯体×1 ｜ 已通关"]}],
                 "tip": "发「傀儡遗迹」逐段挑战"}
    battle_data = card.build_battle_data(_sample_battle())
    check("遗迹数据走旧骨架", tpl._rich(ruin_data) is False, str(ruin_data.keys()))
    check("战报数据走结算界面", tpl._rich(battle_data) is True, str(battle_data.keys()))
    png = pic.render_sync("doupo_ruin", "light", card.CARD_RUIN_NAME,
                          width=720, data=ruin_data)
    from PIL import Image
    img = Image.open(io.BytesIO(png))
    check("遗迹卡仍能渲染", png.startswith(b"\x89PNG") and img.width == 720,
          f"size={img.size}")


def _overlap_2d(a, b) -> bool:
    return not (a[2] <= b[0] or b[2] <= a[0] or a[3] <= b[1] or b[3] <= a[1])


def _check_boxes(bounds, boxes, min_gap: int = 2) -> list[str]:
    """与 card-design skill 同规则：溢出 / 两方向重叠 / 同行间距过小。"""
    bx0, by0, bx1, by1 = bounds
    problems: list[str] = []
    items = [x for x in boxes if x[1] >= 0]
    for label, x0, y0, x1, y1 in items:
        if x0 < bx0 - 1 or y0 < by0 - 1 or x1 > bx1 + 1 or y1 > by1 + 1:
            problems.append("[溢出] %s 越界: (%d,%d,%d,%d)" % (label, x0, y0, x1, y1))
    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            la, a = items[i][0], items[i][1:]
            lb, b = items[j][0], items[j][1:]
            if _overlap_2d(a, b):
                problems.append("[重叠] %s <-> %s: %s vs %s" % (la, lb, a, b))
            if a[1] < b[3] and b[1] < a[3]:
                gap_x = b[0] - a[2] if a[0] <= b[0] else a[0] - b[2]
                if 0 <= gap_x < min_gap:
                    problems.append("[间距] %s <-> %s 水平间距 %dpx" % (la, lb, gap_x))
    return problems


def test_round_bubbles_group_by_round_and_side():
    """逐招明细一回合一个气泡：按连续回合分组，左右两列分别装我方 / 敌方行为。"""
    battle = {"rounds": 4, "log": [
        {"index": 1, "round": 1, "foe": "甲", "move": "普攻", "dmg": 100},
        {"index": 2, "round": 1, "foe": "甲", "move": "兽击", "dmg": 50, "taken": True},
        {"index": 3, "round": 2, "foe": "甲", "move": "普攻", "dmg": 100},
        {"index": 4, "round": 1, "foe": "乙", "move": "服药", "pill": "回气散", "amount": 200, "dmg": 0},
        {"index": 5, "round": 1, "foe": "乙", "move": "兽击", "dmg": 60, "taken": True},
        {"index": 6, "round": 2, "foe": "乙", "move": "普攻", "dmg": 100},
    ]}
    bubbles = br.round_bubbles(battle)
    check("气泡数 = 连续回合数", len(bubbles) == 4, str([b["round"] for b in bubbles]))
    check("回合号连续", [b["round"] for b in bubbles] == [1, 2, 3, 4],
          str([b["round"] for b in bubbles]))
    total = sum(len(b["mine"]) + len(b["foe"]) for b in bubbles)
    check("动作一条不丢", total == len(battle["log"]), f"{total}/{len(battle['log'])}")
    check("服药归我方列", any(a["move"] == "服用回气散"
                          for b in bubbles for a in b["mine"]),
          str([b["mine"] for b in bubbles]))
    check("敌方兽击不进我方列",
          all(a["move"] != "兽击" for b in bubbles for a in b["mine"]), "敌方串列")
    data = br.build_data(battle)
    check("build_data 带 rounds 增量字段", bool(data.get("rounds")), str(data.keys()))
    check("旧 groups 仍给遗迹卡复用",
          [g["name"] for g in data["groups"]] == ["战况", "逐招明细"],
          str([g["name"] for g in data["groups"]]))
    duel = {"rounds": 1, "log": [{"index": 1, "round": 1, "who": "v",
                                  "move": "紫云翼", "dmg": 300}]}
    db = br.round_bubbles(duel)
    check("切磋对手归敌方列", db[0]["foe"] and not db[0]["mine"], str(db))


def test_round_bubble_card_layout_passes_check_boxes():
    """card-design 硬门禁：气泡卡渲染后溢出 0 / 重叠 0 / 间距 0。"""
    pic = card._load_pic()
    check("pic 包可加载（气泡自检）", pic is not None)
    if pic is None:
        return
    battle = _sample_battle()
    # 加长到多回合多事件，逼出窄列里的招式名/数值/词条碰撞
    extra = []
    for r in range(2, 13):
        extra.extend([
            {"index": len(extra) + 1, "round": r, "move": "佛怒火莲·毁灭火莲",
             "dmg": 10233 + r, "crit": True, "mp_cost": 1807,
             "notes": ["破防30%", "火属性"], "kind": "斗技"},
            {"index": len(extra) + 2, "round": r, "move": "狂暴撞击", "dmg": 2400 + r,
             "taken": True, "notes": ["伤害×1.6"]},
        ])
    battle["log"] = list(battle["log"]) + extra
    battle["rounds"] = 12
    data = card.build_battle_data(battle)
    for width in (720,):   # 与生产一致；其它宽度在 test_menu_full 里覆盖
        boxes = []
        png = pic.render_sync("doupo_battle", "light", card.CARD_BATTLE_NAME,
                              width=width, data=data, boxes=boxes)
        from PIL import Image
        img = Image.open(io.BytesIO(png))
        # bar 的 rec 是「图形占位」而非文字，宽度小；自检只查文字盒
        texts = [b for b in boxes if "_bar" not in b[0]]
        problems = _check_boxes((12, 12, img.width - 12, img.height - 12), texts)
        check("气泡卡 %d 宽 boxes 足量" % width, len(texts) > 40, f"n={len(texts)}")
        # 光靠「boxes 不重叠」会在模板回退成平面列表时假通过：必须断言气泡特征盒存在
        round_heads = [b for b in texts if b[0].endswith("_h")]
        check("气泡真的渲染（每回合一个回合号盒）",
              len(round_heads) == len(data["rounds"]),
              f"boxes={len(round_heads)} rounds={len(data['rounds'])}")
        check("气泡左右列头都在",
              any(b[0].endswith("_mine_s") for b in texts)
              and any(b[0].endswith("_foe_s") for b in texts), "缺我方/敌方列头")
        # 右锚定：同一列所有数值的右边界必须**彼此相等**（不随招式名长度漂移）。
        # 只查绝对值 col_edge 会在字体度量不稳定时假红；右锚定本质是「同列对齐」这个相对量。
        # 必须覆盖该列**全部**数值盒（r{回合}_{我方|敌方}{序号}_v），
        # 只查第 0 个会让「从第 1 个起漂移」的回退假通过。
        for key, label in (("mine", "我方"), ("foe", "敌方")):
            edges = [b[3] for b in texts
                     if ("_%s" % key) in b[0] and b[0].endswith("_v")]
            # 敌方未必每回合都出手，盒数不能和回合数比；只需至少 2 个才谈得上「对齐」。
            check("%s列数值统一右锚定" % label,
                  len(edges) >= 2 and (max(edges) - min(edges)) <= 2,
                  f"n={len(edges)} min={min(edges) if edges else None} "
                  f"max={max(edges) if edges else None}")
        check("气泡卡 %d 宽几何自检 0 问题" % width, not problems,
              " | ".join(problems[:5]))
        check("气泡卡 %d 宽输出 PNG" % width,
              png.startswith(b"\x89PNG") and img.width == width, f"size={img.size}")


def test_empty_column_gets_placeholder():
    """空列（敌人已死/本回合没动作）要给淡色「无动作」占位，不留纯空白。"""
    pic = card._load_pic()
    check("pic 可加载（空列占位）", pic is not None)
    if pic is None:
        return
    battle = {"rounds": 2, "log": [
        {"index": 1, "round": 1, "move": "铁山拳", "dmg": 100},
        # 回合2 敌人已死，只有我方动作 → 敌方列空
        {"index": 2, "round": 2, "move": "普攻", "dmg": 80},
    ]}
    data = card.build_battle_data(battle)
    check("第2回合敌方列确实空", not data["rounds"][1]["foe"], str(data["rounds"][1]))
    boxes = []
    png = pic.render_sync("doupo_battle", "light", card.CARD_BATTLE_NAME,
                          width=720, data=data, boxes=boxes)
    ph = [b for b in boxes if b[0].endswith("_ph")]
    check("空列渲染出占位盒", len(ph) >= 1, f"ph={len(ph)}")
    # 占位盒必须有正宽度（真的画了「无动作」文字，而不是空壳）
    check("占位盒画了文字", all(b[3] - b[1] > 10 for b in ph),
          str([(b[0], b[3] - b[1]) for b in ph]))
    # 占位不能产出数值盒（否则污染右锚定 / 把空列算成有伤害）
    check("空列无数值盒", not any("foe" in b[0] and b[0].endswith("_v") for b in ph),
          str([b[0] for b in boxes if "_v" in b[0]][:6]))
    from PIL import Image
    img = Image.open(io.BytesIO(png))
    check("空列占位卡能出图", png.startswith(b"\x89PNG") and img.width == 720, f"size={img.size}")


def test_round_bubbles_carry_damage_and_remainder():
    """每回合必须有「我受多少 / 敌受多少 / 回合末余血余气」（用户 2026-11-19 要的细节）。"""
    battle = {"rounds": 2, "player": {"hp_max": 1000, "mp_max": 600}, "log": [
        {"index": 1, "round": 1, "move": "铁山拳", "dmg": 300,
         "hp_after": 1000, "mp_after": 500, "foe_hp_after": 700},
        {"index": 2, "round": 1, "move": "兽击", "dmg": 250, "taken": True,
         "hp_after": 750, "mp_after": 500},
        {"index": 3, "round": 1, "move": "普攻", "dmg": 120,
         "hp_after": 750, "mp_after": 500, "foe_hp_after": 580},
        {"index": 4, "round": 2, "move": "兽击", "dmg": 900, "taken": True,
         "hp_after": -150, "mp_after": 500},
    ]}
    b = br.round_bubbles(battle)
    check("气泡按回合分组", [x["round"] for x in b] == [1, 2], str([x["round"] for x in b]))
    check("我受 = 敌方列伤害之和", b[0]["hurt"] == 250, str(b[0]["hurt"]))
    check("敌受 = 我方列伤害之和", b[0]["hurt_foe"] == 420, str(b[0]["hurt_foe"]))
    check("回合摘要两栏都在", b[0]["sum_text"] == "我受 250 · 敌受 420", b[0]["sum_text"])
    check("回合末余血取最后一条快照", b[0]["hp"] == 750, str(b[0]["hp"]))
    check("回合末余气取最后一条快照", b[0]["mp"] == 500, str(b[0]["mp"]))
    check("敌方余血取我方出手那条", b[0]["foe_hp"] == 580, str(b[0]["foe_hp"]))
    check("余量行带上限", b[0]["foot_l"] == "余血 750/1,000 · 余气 500/600", b[0]["foot_l"])
    check("敌方余量行", b[0]["foot_r"] == "敌血 580", b[0]["foot_r"])
    # 致死那击 core 会写负数（hp_after=-150）：卡上必须显示 0，不能印 -150
    check("致死回合余血钳到 0（不印负数）", b[1]["hp"] == 0, str(b[1]["hp"]))
    check("致死回合余量行仍成立", b[1]["foot_l"].startswith("余血 0/1,000"),
          b[1]["foot_l"])
    # 没伤害的回合不能写「我受 0 · 敌受 0」，要说人话
    calm = br.round_bubbles({"rounds": 1, "log": [
        {"index": 1, "round": 1, "move": "服用回气散", "pill": "回气散",
         "amount": 100, "dmg": 0}]})
    check("无伤害回合写明无伤", calm[0]["sum_text"] == "本回合无伤", calm[0]["sum_text"])


def test_damage_composition_and_end_state():
    """伤害构成（面板/防御/技能）+ 终局败因必须是真值，不能拿战后回血冒充。"""
    # ---- 1. 真实战斗：两侧都要能给出「面板 → 防御」 ----
    battle = _sample_battle()
    items = br.action_items(battle)
    mine = [a for a in items if a["side"] == "我方" and a["dmg"] > 0]
    foe = [a for a in items if a["side"] != "我方" and a["dmg"] > 0]
    check("我方出手带伤害构成", bool(mine) and all(a["comp"].startswith("面板 ")
                                            for a in mine), str(mine[:1]))
    check("敌方出手带伤害构成", bool(foe) and all(a["comp"].startswith("面板 ")
                                           for a in foe), str(foe[:1]))
    check("构成含防御削减", any("防御 -" in a["comp"] for a in items),
          str([a["comp"] for a in items][:3]))
    check("构成不重复印实收（右侧大数字已是实收）",
          all("实收" not in a["comp"] for a in items), str(items[:1]))
    check("面板 ≥ 实收（防御只减不增；暴击例外）",
          all(a["raw"] == 0 or "暴击" in a["tags"] or a["raw"] >= a["dmg"] - 1
              for a in items),
          str([(a["raw"], a["dmg"], a["tags"]) for a in items][:3]))

    # ---- 2. 战败：不归零，气血保底「上限 × LOSE_HP_FLOOR（5%）」 ----
    # 用户 2026-11-19：「不要 0 血量，留百分之五就行」，且回看与人物卡必须是同一个数
    p = _player("q9")
    hp_max = int(core.stats(p)["体力"])
    floor_hp = max(1, int(hp_max * cfg.LOSE_HP_FLOOR))
    res = {"win": False, "rounds": 3, "taken": 99999, "hp_end": floor_hp,
           "mp_end": 84, "foe_hp_end": 7120, "foe_hp_max": 12000, "downed": True,
           "log": [{"index": 1, "round": 1, "move": "兽击", "dmg": 99999,
                    "taken": True, "hp_after": -287, "mp_after": 84}]}
    snap = core.record_last_battle(p, "遭遇", res, opponent="血战佣兵")
    data = br.build_data(snap)
    rows = {r["label"]: r for r in (data.get("end") or {}).get("rows") or []}
    check("战败气血 = 上限 5%（不是 0）",
          rows.get("我气血", {}).get("value") == floor_hp and
          abs(rows["我气血"]["value"] - hp_max * 0.05) <= 1,
          f"{rows.get('我气血')} hp_max={hp_max}")
    check("终局斗气 = 结束值", rows.get("我斗气", {}).get("value") == 84,
          str(rows.get("我斗气")))
    check("终局敌方余血", rows.get("敌气血", {}).get("value") == 7120,
          str(rows.get("敌气血")))
    check("被打到 0 以下 → 被小医仙所救",
          data["verdict"]["end_text"] == "被小医仙所救", str(data["verdict"]))
    check("收场原因代号正确", snap["summary"]["end_reason"] == "rescued",
          str(snap["summary"].get("end_reason")))
    check("玩家面板血量 = 回看终局（同一个数）",
          snap["player"]["hp"] == rows["我气血"]["value"],
          f"{snap['player']['hp']} vs {rows['我气血']['value']}")
    # 纯文本兜底（出图失败时用户看到的就是它）必须同时保留玩法与收场
    txt = br.text(snap)
    head = txt.splitlines()[0]
    check("文本兜底同时保留玩法与收场",
          "遭遇" in head and "被小医仙所救" in head, head)

    # ---- 3. 还有血却输（血量比判负）→ 落荒而逃，不许写「被救」 ----
    res2 = {"win": False, "rounds": 2, "taken": 300, "hp_end": 640, "mp_end": 0,
            "foe_hp_end": 900, "foe_hp_max": 1000, "downed": False,
            "log": [{"index": 1, "round": 1, "who": "v", "move": "紫云翼",
                     "dmg": 300, "hp_after": 640, "mp_after": 0}]}
    snap2 = core.record_last_battle(p, "遭遇", res2, opponent="对手")
    d2 = br.build_data(snap2)
    check("有血判负写「落荒而逃」", d2["verdict"]["end_text"] == "落荒而逃",
          str(d2["verdict"]))
    check("有血判负终局血量是剩余值",
          (d2["end"]["rows"][0]["value"]) == 640, str(d2["end"]["rows"][0]))
    # 切磋零惩罚：谈不上逃，也谈不上被救
    snap2b = core.record_last_battle(p, "切磋", res2, opponent="对手")
    check("切磋判负写「甘拜下风」",
          br.build_data(snap2b)["verdict"]["end_text"] == "甘拜下风",
          str(snap2b["summary"].get("end_text")))

    # ---- 4. 力竭（300 回合熔断）判胜：必须显式标注，不许冒充击杀 ----
    res3 = {"win": True, "rounds": 300, "taken": 500, "hp_end": 300, "mp_end": 10,
            "foe_hp_end": 800, "foe_hp_max": 1000, "exhausted": True, "log": []}
    snap3 = core.record_last_battle(p, "遭遇", res3, opponent="X")
    check("力竭判胜标注", br.build_data(snap3)["verdict"]["end_text"] == "力竭判胜",
          str(snap3["summary"].get("end_text")))

    # ---- 5. 旧档没有 end_reason → 不画终局（宁缺勿错） ----
    old = {"win": False, "rounds": 1, "player": {"hp": 700, "hp_max": 1000,
                                                 "mp": 10, "mp_max": 100},
           "summary": {"taken": 1}, "log": []}
    check("旧档不出终局条（不拿回血冒充）", br.build_data(old).get("end") == {},
          str(br.build_data(old).get("end")))
    check("旧档战况仍用文字兜住资源",
          any("气血" in r for r in br.build_data(old)["groups"][0]["rows"]),
          str(br.build_data(old)["groups"][0]["rows"]))


def test_defeat_keeps_five_percent_and_matches_cards():
    """战败不归零：气血 = 上限 5%，且「fight 结算值 / 存档 / 回看终局」同一个数。

    用户 2026-11-19：「回看显示 0 血量，人物卡片显示还有血量」「不要 0 血量，
    留百分之五就行」。旧版回看用 hp_end（-287 钳 0）、人物卡用 70% 重置值，
    同一场战斗两个数 —— 这条门禁钉死「只许有一个数」。
    """
    p = _player("q5")
    hp_max = int(core.stats(p)["体力"])
    mon = dict(core.monster_scaled(p, core.cfg.MONSTERS[-1]))
    mon["hp"] = 10 ** 6          # 打不动
    mon["atk"] = 10 ** 6         # 一击必倒
    mon["def"] = 0
    res = core.fight(p, mon, rng=random.Random(1).random, roll_aid=False)
    floor_hp = max(1, int(hp_max * cfg.LOSE_HP_FLOOR))
    check("这一场确实输了", res["win"] is False, str(res.get("win")))
    check("战败气血不归零（上限 5%）", int(p["hp"]) == floor_hp,
          f"hp={p['hp']} floor={floor_hp} max={hp_max}")
    check("fight 结算值 = 存档值", int(res["hp_end"]) == int(p["hp"]),
          f"{res.get('hp_end')} vs {p['hp']}")
    check("被真打到 0 以下 → downed", res.get("downed") is True, str(res.get("downed")))
    snap = core.record_last_battle(p, "遭遇", res, opponent=str(mon["name"]))
    data = br.build_data(snap)
    end_hp = (data.get("end") or {}).get("rows", [{}])[0].get("value")
    check("回看终局 = 存档值（不再是 0）", end_hp == int(p["hp"]),
          f"end={end_hp} hp={p['hp']}")
    check("收场写「被小医仙所救」", data["verdict"].get("end_text") == "被小医仙所救",
          str(data["verdict"]))
    check("战败卡给了下一步（服丹）", "服丹" in (data.get("tip") or ""),
          str(data.get("tip")))
    # 逐招末行的「余血」必须与终局同一个数（旧版这里是 0、终局是保底值 → 又两处对不上）
    bubbles = data.get("rounds") or []
    check("逐招末行余血 = 终局余血",
          bool(bubbles) and bubbles[-1]["hp"] == int(p["hp"]),
          f"footer={bubbles[-1]['hp'] if bubbles else None} hp={p['hp']}")
    check("逐招末行余血不印 0",
          bool(bubbles) and "余血 0/" not in bubbles[-1]["foot_l"],
          str(bubbles[-1]["foot_l"]) if bubbles else "")

    # 保底常量必须与 config 同源（battle_review 零依赖，镜像了一份）
    check("保底比例与 cfg.LOSE_HP_FLOOR 一致",
          abs(br.LEGACY_HP_FLOOR - float(cfg.LOSE_HP_FLOOR)) < 1e-9,
          f"{br.LEGACY_HP_FLOOR} vs {cfg.LOSE_HP_FLOOR}")

    # 旧档（上一版录的战报：气血 0 + 代号 hp_zero）按新口径换算，别再显示 0 血
    old = dict(snap)
    old["summary"] = dict(snap["summary"], end_reason="hp_zero", end_text="气血耗尽")
    old["player"] = dict(snap["player"], hp=0)
    old_data = br.build_data(old)
    old_end = (old_data.get("end") or {}).get("rows") or [{}]
    check("旧档 0 血按新规则换算成保底值",
          old_end[0].get("value") == max(1, int(hp_max * br.LEGACY_HP_FLOOR)),
          str(old_end[0]))
    check("旧档收场文案改成「被小医仙所救」",
          old_data["verdict"]["end_text"] == "被小医仙所救",
          str(old_data["verdict"]))


def test_end_bar_and_comp_are_drawn():
    """卡片必须真的画出终局条与构成行（数据有了但模板不画 = 用户还是看不见）。"""
    pic = card._load_pic()
    check("pic 可加载（终局条自检）", pic is not None)
    if pic is None:
        return
    p = _player("q8")
    hp_max = int(core.stats(p)["体力"])
    floor_hp = max(1, int(hp_max * cfg.LOSE_HP_FLOOR))
    mp_left = max(1, int(core.mp_max(p) * 0.4))   # 必须 ≤ 上限，否则条恒满测不出比例
    res = {"win": False, "rounds": 1, "taken": 5000, "hp_end": floor_hp,
           "mp_end": mp_left, "foe_hp_end": 7120, "foe_hp_max": 12000, "downed": True,
           "log": [
               {"index": 1, "round": 1, "move": "铁山拳", "dmg": 1800,
                "raw": 3000, "def_cut": 1200, "hp_after": hp_max, "mp_after": mp_left,
                "foe_hp_after": 7120},
               {"index": 2, "round": 1, "move": "兽击", "dmg": 5000,
                "taken": True, "atk_raw": 7000, "def_cut": 1200,
                "absorbed": 800, "hp_after": -287, "mp_after": mp_left}]}
    snap = core.record_last_battle(p, "遭遇", res, opponent="血战佣兵")
    data = card.build_battle_data(snap)
    boxes = []
    png = pic.render_sync("doupo_battle", "light", card.CARD_BATTLE_NAME,
                          width=720, data=data, boxes=boxes)
    labels = [b[0] for b in boxes]
    check("终局三条标签都画了",
          all(("n%d_l" % i) in labels for i in range(3)), str(labels)[:120])
    # 条宽写在盒名里（n{i}_bar{px}）：几何自检只认矩形，认不出「5% 画成满条」
    bars = {x.split("_bar")[0]: int(x.split("_bar")[1])
            for x in labels if "_bar" in x and x.startswith("n")}
    check("终局气血条画了", "n0" in bars, str(bars))
    check("战败也留得下一口气（条不是空的）", bars.get("n0", 0) > 0, str(bars))
    check("5% 就是一小截（比斗气/敌血都短）",
          0 < bars.get("n0", 0) < bars.get("n1", 0) < bars.get("n2", 0), str(bars))
    check("终局数值盒画了 3 个",
          len([x for x in labels if x.startswith("n") and x.endswith("_v")]) == 3,
          str([x for x in labels if x.startswith("n")]))
    # 构成行：两侧各一条，各一行（放得下不折行）
    check("我方构成行画了", "r0_mine0_c0" in labels, str([x for x in labels if "_c" in x]))
    check("敌方构成行画了", "r0_foe0_c0" in labels, str([x for x in labels if "_c" in x]))
    comp_txt = [a["comp"] for a in br.action_items(snap) if a["comp"]]
    check("构成数据含技能吸收", any("技能 -" in c for c in comp_txt), str(comp_txt))
    check("构成三段加总等于实收",
          any(c == "面板 7,000 · 防御 -1,200 · 技能 -800" for c in comp_txt),
          str(comp_txt))
    # 回合摘要 + 余量行（sum / fl / fr）
    check("回合战损摘要画了", any(x.endswith("_sum") for x in labels),
          str([x for x in labels if x.endswith("_sum")]))
    check("余量行画了", any(x.endswith("_fl") for x in labels)
          and any(x.endswith("_fr") for x in labels),
          str([x for x in labels if x.endswith("_fl") or x.endswith("_fr")]))
    from PIL import Image
    img = Image.open(io.BytesIO(png))
    texts = [b for b in boxes if "_bar" not in b[0]]
    problems = _check_boxes((12, 12, img.width - 12, img.height - 12), texts)
    check("补料后的卡几何 0 问题", not problems, " | ".join(problems[:5]))
    check("补料后的卡能出图", png.startswith(b"\x89PNG") and img.width == 720,
          f"size={img.size}")

    # 「0 值 = 空条」这条门禁换个行继续守：击杀后敌气血归零，条必须是空的
    p2 = _player("q7")
    win_res = {"win": True, "rounds": 2, "taken": 500, "hp_end": 3000, "mp_end": 900,
               "foe_hp_end": 0, "foe_hp_max": 12000,
               "log": [{"index": 1, "round": 1, "move": "铁山拳", "dmg": 900,
                        "raw": 1200, "def_cut": 300, "hp_after": 3000,
                        "mp_after": 900, "foe_hp_after": 0}]}
    snap_win = core.record_last_battle(p2, "遭遇", win_res, opponent="X")
    boxes2 = []
    png2 = pic.render_sync("doupo_battle", "light", card.CARD_BATTLE_NAME,
                           width=720, data=card.build_battle_data(snap_win),
                           boxes=boxes2)
    bars2 = {x.split("_bar")[0]: int(x.split("_bar")[1])
             for x in [b[0] for b in boxes2] if "_bar" in x and x.startswith("n")}
    check("击杀后敌气血 0 = 空条（不画长度）", bars2.get("n2") == 0, str(bars2))
    check("胜场收场写「击杀对手」",
          br.build_data(snap_win)["verdict"]["end_text"] == "击杀对手",
          str(br.build_data(snap_win)["verdict"]))


def test_battle_review_text_after_roundtrip():
    async def run():
        with tempfile.TemporaryDirectory() as d:
            store = Store(os.path.join(d, "data.json"))
            p = _player("q4")
            await store.put_player(p)
            await commands.cmd_fight(store, "q4")
            return await commands.cmd_battle_review(store, "q4")
    out = asyncio.run(run())
    check("落盘后能回看完整过程", "【逐招明细】" in out and "回合" in out, out[:100])


# ---------------------------------------------------------------------------
# 超长战报分页（2026-11-19 用户「超长就分页」）
# ---------------------------------------------------------------------------
def _tpl():
    """取 pic 独立包里的回看模板（与 card._load_pic 同一份，绝不二次加载）。"""
    pic = card._load_pic()
    if pic is None:
        return None, None
    return pic, importlib.import_module(pic.__name__ + ".templates.doupo_battle")


def _tank_battle(cap: int) -> dict:
    """造一场「打不死」的拉锯战：必走 HARD_ROUND_CAP 熔断，正好 cap 个回合。

    ``mon["hp"]=1e9`` 保证打不完、``atk=1`` 保证玩家不被秒，回合数由熔断决定
    —— 分页要验的是「回合多」这一件事，不依赖随机伤害。
    """
    old = cfg.HARD_ROUND_CAP
    try:
        cfg.HARD_ROUND_CAP = int(cap)
        p = _player("qp")
        p["lv"] = 40
        p["hp"] = int(core.stats(p)["体力"])
        p["bonus"] = {"防御": 20000}
        mon = core.monster_scaled(p, cfg.MONSTERS[0])
        mon["hp"] = 10 ** 9
        mon["atk"] = 1
        mon["def"] = 0
        res = core.fight(p, mon, rng=lambda: 0.5)
        return core.record_last_battle(p, "遭遇", res, opponent=str(mon["name"]))
    finally:
        cfg.HARD_ROUND_CAP = old


def test_long_battle_splits_into_pages():
    """26 回合长战报：按回合切页，页间不重不漏，每页渲染 0 违规。"""
    pic, tpl = _tpl()
    check("pic/模板可加载（分页自检）", pic is not None and tpl is not None)
    if pic is None or tpl is None:
        return
    data = card.build_battle_data(_tank_battle(26))
    rounds = data["rounds"]
    pages = pic.split_pages("doupo_battle", data, width=card.CARD_WIDTH)
    check("26 回合必须分页", len(pages) >= 2, f"pages={len(pages)}")
    check("总高超过单页预算（前提成立）",
          tpl.card_height(data, card.CARD_WIDTH - 2 * tpl.PAD) > tpl.PAGE_MAX_H,
          f"h={tpl.card_height(data, card.CARD_WIDTH - 2 * tpl.PAD):.0f}")

    got = [r["round"] for pg in pages for r in pg["rounds"]]
    check("回合不重不漏且顺序不变",
          got == [r["round"] for r in rounds], f"{got[:3]}…{got[-3:]} n={len(got)}")
    check("每页页码/总数正确",
          [pg["page"]["index"] for pg in pages] == list(range(1, len(pages) + 1))
          and all(pg["page"]["total"] == len(pages) for pg in pages),
          str([pg.get("page") for pg in pages]))
    check("页副标题写「第 x/N 页 · 回合 a-b」",
          all(("第 %d/%d 页" % (pg["page"]["index"], len(pages))) in pg["subtitle"]
              and ("回合 %d-%d" % (pg["page"]["from"], pg["page"]["to"])) in pg["subtitle"]
              for pg in pages), str([pg["subtitle"] for pg in pages]))
    check("首页仍带结算区（终局/输出/节点）",
          bool(pages[0].get("end")) and bool(pages[0].get("kpis"))
          and bool(pages[0].get("bars")),
          str(sorted(pages[0].keys())))
    check("续页不重复结算区（只讲这一页的回合）",
          all(not pg.get("end") and not pg.get("bars") for pg in pages[1:]), "续页仍有终局条")
    check("每页都有 tip（不靠上一页提示）", all(pg.get("tip") for pg in pages), "丢 tip")
    for i, pg in enumerate(pages, 1):
        h = tpl.card_height(pg, card.CARD_WIDTH - 2 * tpl.PAD)
        check("第 %d/%d 页高度守预算" % (i, len(pages)), h <= tpl.PAGE_MAX_H + 0.5,
              f"{h:.0f} > {tpl.PAGE_MAX_H}")
        boxes = []
        png = pic.render_sync("doupo_battle", "light", card.CARD_BATTLE_NAME,
                              width=card.CARD_WIDTH, data=pg, boxes=boxes)
        from PIL import Image
        img = Image.open(io.BytesIO(png))
        texts = [b for b in boxes if "_bar" not in b[0]]
        problems = _check_boxes((12, 12, img.width - 12, img.height - 12), texts)
        check("第 %d/%d 页几何 0 违规" % (i, len(pages)), not problems, str(problems[:3]))
        heads = [b for b in texts if b[0].endswith("_h")]
        check("第 %d/%d 页气泡数 = 该页回合数" % (i, len(pages)),
              len(heads) == len(pg["rounds"]), f"boxes={len(heads)} rounds={len(pg['rounds'])}")


def test_short_battle_stays_single_page():
    """短战报（8 回合）不许被分页：渲染字节与不分页完全一致。"""
    pic, tpl = _tpl()
    if pic is None or tpl is None:
        check("pic 可加载（短战报分页）", False)
        return
    data = card.build_battle_data(_sample_battle())
    pages = pic.split_pages("doupo_battle", data, width=card.CARD_WIDTH)
    check("短战报仍是一页", len(pages) == 1, f"pages={len(pages)}")
    check("单页不加 page/subtitle（逐字节不变）",
          "page" not in pages[0] and not pages[0].get("subtitle"),
          str(sorted(pages[0].keys())))
    png_a = pic.render_sync("doupo_battle", "light", card.CARD_BATTLE_NAME,
                            width=card.CARD_WIDTH, data=data)
    png_b = pic.render_sync("doupo_battle", "light", card.CARD_BATTLE_NAME,
                            width=card.CARD_WIDTH, data=pages[0])
    check("单页渲染与分页入口产物逐字节相同", png_a == png_b,
          f"{len(png_a)} vs {len(png_b)}")


def test_page_cap_never_drops_rounds():
    """300 回合脏数据：页数封顶 5，但一回合都不许丢（capped 明示超高是妥协）。"""
    pic, tpl = _tpl()
    if pic is None or tpl is None:
        check("pic 可加载（页数封顶）", False)
        return
    data = card.build_battle_data(_tank_battle(300))
    pages = pic.split_pages("doupo_battle", data, width=card.CARD_WIDTH)
    check("页数封顶 = MAX_PAGES", len(pages) == tpl.MAX_PAGES, f"pages={len(pages)}")
    got = [r["round"] for pg in pages for r in pg["rounds"]]
    check("封顶后回合仍然不丢",
          got == [r["round"] for r in data["rounds"]], f"n={len(got)}")
    check("封顶页明确标记 capped（门禁据此放行页高）",
          all(pg["page"].get("capped") for pg in pages), str(pages[0]["page"]))
    counts = [len(pg["rounds"]) for pg in pages]
    per = (len(data["rounds"]) + tpl.MAX_PAGES - 1) // tpl.MAX_PAGES
    check("封顶时按回合均分（不许把尾巴全堆在末页）",
          max(counts) <= 2 * per, f"{counts} per={per}")


def test_finish_card_wiring_accepts_pages():
    """接线门禁：多页必须仍走「一条消息」——官方内嵌多图 / NapCat 多图片段。"""
    src = open(os.path.join(ROOT, "src", "game_compat.py"), encoding="utf-8").read()
    check("send_card_via_channel 接受多图列表",
          "images = [d for d in (data if isinstance(data, (list, tuple)) else [data]) if d]"
          in src, "多图归一化丢了")
    check("官方通道仍用 Outgoing(images=images) 一条消息",
          "Outgoing(images=images, keyboard_cmd=kcmd)" in src, "退回单图发送")
    check("NapCat 多页走多图片段 finish(*segs)",
          "await matcher.finish(*segs)" in src, "NapCat 只发第一页")
    check("NapCat 正常分支与降级分支都发全部页（两处）",
          src.count("await matcher.finish(*segs)") == 2, "有一处只发了第一页")
    import inspect
    from src.game_compat import finish_card
    check("finish_card 签名声明支持 list[bytes]",
          "list[bytes]" in str(inspect.signature(finish_card)), "签名未提多页")


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn()
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"[FAIL] {fn.__name__}: {type(e).__name__}: {e}")
    for name, ok, detail in RESULTS:
        print(f"{'PASS' if ok else 'FAIL'} {name}" + (f" :: {detail}" if detail and not ok else ""))
    print(f"\n{len(RESULTS) - failed}/{len(RESULTS)} 通过，失败 {failed}")
    sys.exit(1 if failed or not all(x[1] for x in RESULTS) else 0)
