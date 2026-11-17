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
    check("回看含气血/斗气轨迹", any("气血" in r and "斗气" in r for r in rows), str(rows[:2]))
    text = br.text(battle)
    check("文本兜底含分组", "【战况】" in text and "【逐招明细】" in text, text[:80])
    synthetic = {"win": False, "rounds": 12, "player": {"hp_max": 1000, "mp": 0, "mp_max": 500},
                 "summary": {"taken": 700},
                 "log": [{"move": "普攻", "dmg": 1, "hp_after": 100,
                          "mp_after": 0, "mp_cost": 0, "notes": ["破甲"]}]}
    tags = br.tags(synthetic)
    for need in ("lose", "low_dmg", "no_mp", "high_taken", "many_rounds"):
        check(f"标签命中 {need}", need in tags, str(tags))


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
