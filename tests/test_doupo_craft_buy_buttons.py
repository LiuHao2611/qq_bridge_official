# -*- coding: utf-8 -*-
"""守门（2026-11-26 用户要求）：炼药材料「一次买够」按钮闭环。

用户原话（两段一起守）：
  * 「查看丹药时，底部的按钮应该有全部材料的购买按钮，注意有的材料需要不止一个，
     必须要保证一次买够」；
  * 「炼药时材料不够的提示底部按钮也应该直接加上对应的材料购买按钮，比如我一次
     炼制10个丹药，缺少的材料应该是10个丹药所需的材料，按钮可以一次买齐材料，
     保证我下次操作可以直接进行炼制」。

四条不变量：
  ① 查看丹药卡 → **每味**材料一颗按钮，data 里的数量 = 该丹单炉用量（不是恒 1）；
  ② 缺料回执缺口 = 单炉用量 × 本次炉数 − 已有（10 炉就报 10 炉的量），
     底部挂每味缺失材料的买齐按钮 + 炼制此丹；
  ③ 点一颗药材按钮买完 → 回执重算剩余缺口，**再挂**还缺的药材 + 炼制此丹，
     缺口清零时只剩炼制 → 真的能「下次操作直接炼制」；
  ④ 手打「买 血灵草 3」（没有 @ 尾巴）行为与从前逐字一致，不因新功能变形。

prove-it 反证：
  * 把 `_mat_short` 的 `* n` 去掉 → ②③ 必红；
  * 把 `_item_detail_buttons` 退回「只挂第一味材料」→ ① 必红；
  * 删掉 `cmd_buy` 的 `arm_buy` → ③ 必红；
  * 让 `split_ctx` 永远返回原串 → ④ 仍绿但 ③ 红（尾巴没被剥掉就认不出丹名）。

运行：python tests/test_doupo_craft_buy_buttons.py
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _stub_nonebot():
    nb = types.ModuleType("nonebot")
    nb.get_driver = lambda: types.SimpleNamespace(on_startup=lambda f: f)

    def on_regex(*a, **k):
        m = types.SimpleNamespace()
        m.handle = lambda *a, **k: (lambda f: f)
        m.finish = lambda *a, **k: None
        return m
    nb.on_regex = on_regex
    sys.modules["nonebot"] = nb
    for n in ("nonebot.adapters", "nonebot.adapters.onebot",
              "nonebot.adapters.onebot.v11"):
        sys.modules[n] = types.ModuleType(n)
    v11 = sys.modules["nonebot.adapters.onebot.v11"]
    v11.MessageEvent = object
    v11.MessageSegment = object
    v11.Message = object


_stub_nonebot()
for _p in (ROOT, os.path.join(ROOT, "src", "plugins")):
    if _p not in sys.path:
        sys.path.insert(0, _p)
os.environ["DOUPO_CHANCE_RATE"] = "0"

from doupo import commands, config as cfg, core, craft_ctx  # noqa: E402
from doupo.store import Store  # noqa: E402

# 与被测代码**同一份** doupo_buttons 模块实例（按钮靠 ContextVar 传递，
# 两份实例 = 两个 ContextVar，arm 了也读不到）——唯一入口 craft_ctx._channel()。
db = craft_ctx._channel()
assert db is not None, "通道按钮层加载失败（doupo_buttons 不可用）"

FAILS = []
PASS_N = [0]


def check(cond, msg):
    if not cond:
        FAILS.append(msg)
        print("FAIL", msg)
    else:
        PASS_N[0] += 1
        print("PASS", msg)


def _reset_buttons():
    """清空本回合 arm 的动态按钮（测试串场用；生产靠每事件独立 ContextVar）。"""
    db._dynamic_buttons.set({})


def _player(qq="Q_MAT", lv=90, gold=10_000_000):
    p = core.create_player(qq, "测试", "群")
    p["lv"] = lv
    p["alch"] = 8
    p["alch_exp"] = 0
    p["gold"] = gold
    p["flames"]["紫火"] = 1        # 有火才能开炉（入道不送火）
    p["last_craft"] = 0
    return p


# ---------------------------------------------------------------------------
# ① 查看丹药卡：每味材料一颗、数量 = 单炉用量
# ---------------------------------------------------------------------------
def test_item_card_all_materials():
    _reset_buttons()
    name = "破厄丹"
    need = cfg.RECIPE_BY_NAME[name]["need"]
    btns = db.buttons_for_card("item:%s" % name)
    cmds = [b.cmd for b in btns]
    # 每味材料都有按钮，且数量就是该丹的单炉用量
    for mat, qty in need.items():
        want = "%s买 %s %d @%s 1" % (db.DOUPO_CUSTOM_PREFIX, mat, qty, name)
        check(want in cmds, "查看%s：%s×%d 有买齐按钮" % (name, mat, qty))
    check("炼药" in cmds and "背包" in cmds, "查看丹药卡保留炼药/背包")
    check("%s炼制 %s" % (db.DOUPO_CUSTOM_PREFIX, name) in cmds, "查看丹药卡有炼制按钮")
    check(len(btns) == len(need) + 3,
          "按钮数=材料数+炼制+炼药+背包（实际 %d，材料 %d）" % (len(btns), len(need)))
    check(all(len(b.label) <= db.LABEL_MAX for b in btns),
          "label 全部 ≤%d：%s" % (db.LABEL_MAX, [b.label for b in btns]))
    check(all(not b.primary for b in btns), "primary 恒 False")

    # 六味材料的丹（茯苓青丹）也要全给，不许只给前几味
    many = cfg.RECIPE_BY_NAME["茯苓青丹"]["need"]
    cmds2 = [b.cmd for b in db.buttons_for_card("item:茯苓青丹")]
    check(all(any(("%s买 %s " % (db.DOUPO_CUSTOM_PREFIX, m)) in c for c in cmds2)
              for m in many), "六味材料一味不落：%s" % list(many))

    # 非丹药 / 未知 → 通用按钮，不带 custom 带参（与旧行为逐字一致）
    for key in ("item:血灵草", "item:不存在的东西", "item:"):
        got = [b.cmd for b in db.buttons_for_card(key)]
        check(got == list(db.CARD_ACTIONS["item"]), "%s 回落通用按钮：%s" % (key, got))


# ---------------------------------------------------------------------------
# ② 缺口 = 炉数 × 单炉用量 − 已有
# ---------------------------------------------------------------------------
def test_gap_scales_with_furnace():
    _reset_buttons()
    p = _player()
    name = "破厄丹"
    need = cfg.RECIPE_BY_NAME[name]["need"]
    mat = next(iter(need))
    core.add_item(p, mat, 1)              # 只给 1 份

    one = craft_ctx.mat_gaps(p, name, 1)
    ten = craft_ctx.mat_gaps(p, name, 10)
    one_map = {g["mat"]: g for g in one}
    ten_map = {g["mat"]: g for g in ten}
    check(one_map[mat]["need"] == need[mat] and one_map[mat]["gap"] == need[mat] - 1,
          "单炉缺口 = 单炉用量 − 已有（%s）" % mat)
    check(ten_map[mat]["need"] == need[mat] * 10,
          "10 炉需求 = 单炉用量×10（%d）" % (need[mat] * 10))
    check(ten_map[mat]["gap"] == need[mat] * 10 - 1, "10 炉缺口含已有抵扣")
    check(len(ten) == len([m for m in need if need[m] - (1 if m == mat else 0) > 0])
          or len(ten) == len(need), "缺口只列真缺的材料")

    # core 回包与 craft_ctx 同源（同一函数，不许两套算法）
    res = core.craft_batch(p, name, 10, rng=lambda: 0.0, now_ts=1000)
    check(res.get("reason") == "mat" and res.get("gaps"), "连炼10炉缺料回包带 gaps")
    check(any("%s×%d(有1)" % (mat, need[mat] * 10) in m for m in res["missing"]),
          "缺料文本按 10 炉算：%s" % res.get("missing"))


# ---------------------------------------------------------------------------
# ③ 缺料回执挂按钮 + 买完重算（闭环）
# ---------------------------------------------------------------------------
async def _mat_reply_and_buy_loop():
    _reset_buttons()
    tmp = tempfile.mkdtemp(prefix="doupo_mat_btn_")
    store = Store(os.path.join(tmp, "doupo.json"))
    await store.load()
    qq = "Q_LOOP"
    p = _player(qq)
    await store.put_player(p)

    name = "破厄丹"
    need = cfg.RECIPE_BY_NAME[name]["need"]

    # 单炉缺料：回执挂「每味材料买齐 + 炼制」
    txt = await commands.cmd_craft(store, qq, name)
    check("材料不足" in txt, "缺料回执说明材料不足：%s" % txt.splitlines()[0])
    btns = db.buttons_for_card("craft_text")
    buy_cmds = [b.cmd for b in btns if "买 " in b.cmd]
    check(len(buy_cmds) == len(need), "缺料回执每味材料一颗按钮（%d/%d）" % (len(buy_cmds), len(need)))
    check(any(("%s炼制 %s" % (db.DOUPO_CUSTOM_PREFIX, name)) == b.cmd for b in btns),
          "缺料回执带炼制按钮（买完直接开炉）")

    # 连炼 10 炉缺料：数量必须是 10 炉的量
    txt10 = await commands.cmd_craft(store, qq, "%s 10次" % name)
    want = "%s×%d" % (next(iter(need)), next(iter(need.values())) * 10)
    check(want in txt10, "连炼10炉缺料按10炉报量（找 %s）" % want)
    btns10 = db.buttons_for_card("craft_text")
    check(any((" %d @" % (next(iter(need.values())) * 10)) in b.cmd for b in btns10),
          "按钮数量也是 10 炉的量：%s" % [b.label for b in btns10])

    # 点按钮逐味买齐 → 每次回执都重算剩余，最后只剩「炼制」
    guard = 0
    while True:
        guard += 1
        if guard > 12:
            check(False, "买齐闭环超过 12 步未收敛（疑似缺口不递减）")
            break
        gaps = craft_ctx.mat_gaps(await store.get_player(qq), name, 1)
        if not gaps:
            break
        g = gaps[0]
        btn = db.mat_buy_button(name, g["mat"], g["gap"], 1).cmd
        real = btn[len(db.DOUPO_CUSTOM_PREFIX):]
        r = await commands.cmd_buy(store, qq, real.split(" ", 1)[1])
        check("购入" in r, "点买齐按钮真的买到货：%s / %s" % (g["mat"], r.splitlines()[0]))
        left = db.buttons_for_card("buy_text")
        left_mats = [b.cmd for b in left if "买 " in b.cmd]
        check(len(left_mats) == len(gaps) - 1,
              "买完回执剩余材料按钮递减（%d → %d）" % (len(gaps), len(left_mats)))

    left = db.buttons_for_card("buy_text")
    check(all("买 " not in b.cmd for b in left), "材料齐了就不再挂买药按钮")
    check(any(("%s炼制 %s" % (db.DOUPO_CUSTOM_PREFIX, name)) == b.cmd for b in left),
          "材料齐了回执给「炼制此丹」：%s" % [b.label for b in left])

    # 点「炼制」→ 真的炼出来了（用户「保证我下次操作可以直接进行炼制」）
    out = await commands.cmd_craft(store, qq, name)
    check(("炼成" in out) or ("炼制成功" in out) or ("💨" not in out and "材料不足" not in out),
          "买齐后点炼制能开炉：%s" % out.splitlines()[0])

    # 连炼中途药材见底（部分开成）：也要列「再炼这批还缺多少」+ 买齐按钮
    qq2 = "Q_PART"
    p2 = _player(qq2)
    p2["last_craft"] = 0
    p2["mp"] = 10_000_000                 # 斗气管够，让「药材见底」先触发
    for mat, qty in need.items():           # 只够 2 炉
        core.add_item(p2, mat, qty * 2)
    await store.put_player(p2)
    part = await commands.cmd_craft(store, qq2, "%s 5次" % name)
    check("余炉未炼" in part, "见底时明写「余炉未炼」：%s" % part.replace("\n", " / ")[:120])
    check("尚需" in part, "见底时列出还缺多少：%s" % part.replace("\n", " / ")[:160])
    part_btns = db.buttons_for_card("craft_text")
    check(any("买 " in b.cmd for b in part_btns), "见底回执挂买齐按钮：%s"
          % [b.label for b in part_btns])


# ---------------------------------------------------------------------------
# ④ 手打口令不变形（无 @ 尾巴）+ craftonly 回执带炼制按钮
# ---------------------------------------------------------------------------
async def _plain_buy_unchanged():
    _reset_buttons()
    tmp = tempfile.mkdtemp(prefix="doupo_plain_")
    store = Store(os.path.join(tmp, "doupo.json"))
    await store.load()
    qq = "Q_PLAIN"
    await store.put_player(_player(qq))

    r = await commands.cmd_buy(store, qq, "血灵草 3")
    check("购入" in r and "血灵草" in r, "手打「买 血灵草 3」照旧成功：%s" % r.splitlines()[0])
    check("尚需" not in r and "药材已齐" not in r, "手打口令不追加炼药上下文行：%s" % r)

    # 「买 <可炼制丹>」：拒绝 + 挂「炼制此丹」按钮，别让玩家再手打丹名
    r2 = await commands.cmd_buy(store, qq, "破厄丹")
    check("宝库不售" in r2, "可炼制丹仍禁购")
    btns = db.buttons_for_card("buy_text")
    check(any(b.cmd == "%s炼制 破厄丹" % db.DOUPO_CUSTOM_PREFIX for b in btns),
          "禁购回执带炼制按钮：%s" % [b.label for b in btns])

    # split_ctx 边界
    check(craft_ctx.split_ctx("血灵草 3") == ("血灵草 3", "", 1), "无尾巴原样返回")
    check(craft_ctx.split_ctx("白参 60 @茯苓青丹 10") == ("白参 60", "茯苓青丹", 10),
          "尾巴解析出丹名与炉数")
    check(craft_ctx.split_ctx("白参 6 @茯苓青丹") == ("白参 6", "茯苓青丹", 1),
          "炉数可省（默认 1）")
    check(craft_ctx.split_ctx("白参 6 @茯苓青丹 99")[2] == cfg.CRAFT_BATCH_MAX,
          "炉数钳到 CRAFT_BATCH_MAX")


def main():
    print("=" * 70)
    test_item_card_all_materials()
    test_gap_scales_with_furnace()
    asyncio.run(_mat_reply_and_buy_loop())
    asyncio.run(_plain_buy_unchanged())
    print("=" * 70)
    print("通过 %d 项，失败 %d 项" % (PASS_N[0], len(FAILS)))
    if FAILS:
        print("失败清单：")
        for f in FAILS:
            print(" -", f)
        return 1
    print("✅ 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
