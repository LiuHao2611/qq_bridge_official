# -*- coding: utf-8 -*-
"""赠送背包物品守门（2026-11-20 第十轮）。

用户诉求：「加一个新功能赠送，可以赠送其他玩家任意背包物品」。

要守住的不变量：
  * **一次事务写两行**：先扣后加若各走一次 ``put_player``，第二步失败就会
    凭空吞物品。必须一次 ``transfer_item``，失败整段回滚；
  * 金币旧路径逐字节不变：``送 100 @某人`` 仍走 ``cmd_give``；
  * 失败一律「零副作用」：物品不够 / 对方未入道 / 自己送自己 / 数量为 0，
    谁的包裹都不许动一个字节；
  * 能送的候选池是**自己包裹里真实有的**，不是宝库目录（否则「药渣」送不掉）；
  * 落盘真发生：重新从磁盘开一个 Store 也要能读到对方的物品。

自带 runner：python tests/test_doupo_gift.py
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLUGINS = os.path.join(ROOT, "src", "plugins")
for _p in (ROOT, PLUGINS):
    if _p not in sys.path:
        sys.path.insert(0, _p)

os.environ["DOUPO_CHANCE_RATE"] = "0"


def _stub():
    nb = types.ModuleType("nonebot")
    nb.get_driver = lambda: types.SimpleNamespace(on_startup=lambda f: f)

    def on_x(*a, **k):
        m = types.SimpleNamespace()
        m.handle = lambda *a, **k: (lambda f: f)
        m.finish = lambda *a, **k: None
        return m
    nb.on_regex = on_x
    nb.on_command = on_x
    nb.on_notice = on_x
    nb.on_message = on_x
    nb.require = lambda *a, **k: (lambda f: f)
    nb.logger = types.SimpleNamespace(error=lambda *a, **k: None,
                                      warning=lambda *a, **k: None,
                                      info=lambda *a, **k: None)
    sys.modules["nonebot"] = nb
    for name in ("nonebot.adapters", "nonebot.adapters.onebot"):
        sys.modules[name] = types.ModuleType(name)
    v11 = types.ModuleType("nonebot.adapters.onebot.v11")
    v11.MessageEvent = object
    v11.MessageSegment = object
    v11.Message = object
    v11.Bot = object
    sys.modules["nonebot.adapters.onebot.v11"] = v11


_stub()

from doupo import config as cfg  # noqa: E402
from doupo import core  # noqa: E402
from doupo import commands  # noqa: E402
import importlib  # noqa: E402

store_mod = importlib.import_module("doupo.store")

A = "20001"
B = "20002"
G = "30001"

_FAILS = []
_N = 0


def check(cond, msg):
    global _N
    _N += 1
    if not cond:
        _FAILS.append(msg)


# ---------------------------------------------------------------------------
# 1. 解析口径（纯函数，不碰库）
# ---------------------------------------------------------------------------
def test_parse_gift():
    cases = [
        ("送 100", ("", 100), "纯数字 = 金币"),
        ("赠送 100", ("", 100), "「赠送」前缀也要剥"),
        ("送100", ("", 100), "无空格"),
        ("送 回灵丹 5", ("回灵丹", 5), "空格分隔"),
        ("送 回灵丹×5", ("回灵丹", 5), "× 号"),
        ("送 回灵丹x5", ("回灵丹", 5), "x 号"),
        ("送 回灵丹*3", ("回灵丹", 3), "* 号"),
        ("送 回灵丹", ("回灵丹", 1), "不给数量 = 1"),
        ("送回灵丹5", ("回灵丹", 5), "无分隔符也要能切数量"),
        ("赠送 七阶魔核 2", ("七阶魔核", 2), "中文数字物品名不能被当数量"),
        ("", ("", 0), "空串"),
        ("送", ("", 0), "只有前缀"),
    ]
    for text, want, why in cases:
        got = commands.parse_gift(text)
        check(got == want, "parse_gift(%r) 应为 %r，实得 %r（%s）"
              % (text, want, got, why))
    # 金币模式与物品模式必须互斥：金币那一串绝不能落进物品名
    name, n = commands.parse_gift("送 100")
    check(name == "" and commands._gift_item_name(
        {"bag": {"100": 1}}, "100") == "100",
          "金币模式与物品模式靠「整串是数字」区分，不能互相污染")


# ---------------------------------------------------------------------------
# 2. 端到端：成功 / 各类失败零副作用 / 落盘
# ---------------------------------------------------------------------------
async def _mk_store(tmp):
    store = store_mod.Store(os.path.join(tmp, "doupo.json"))
    await store.load()
    a = core.create_player(A, "施主", G)
    a["gold"] = 5000
    a["bag"] = {"回灵丹": 10, "药渣": 4, "炎星石": 7}
    await store.put_player(a)
    b = core.create_player(B, "受主", G)
    # 入道礼包自带回灵丹，清空才能验证「只多了赠送的那几个」
    b["bag"] = {}
    await store.put_player(b)
    return store


async def e2e():
    tmp = tempfile.mkdtemp(prefix="gift_")
    store = await _mk_store(tmp)

    # --- 成功：物品从 A 到 B ---
    txt = await commands.cmd_gift(store, A, B, "回灵丹", 3)
    check("回灵丹" in txt and "3" in txt, "成功回包应点名物品与数量：%s" % txt[:80])
    pa, pb = await store.get_player(A), await store.get_player(B)
    check(core.count_item(pa, "回灵丹") == 7, "施主应剩 7 个，实得 %d"
          % core.count_item(pa, "回灵丹"))
    check(core.count_item(pb, "回灵丹") == 3, "受主应得 3 个，实得 %d"
          % core.count_item(pb, "回灵丹"))
    check(core.count_item(pa, "药渣") == 4 and core.count_item(pb, "药渣") == 0,
          "没送的东西一个都不许动")

    # --- 落盘：另开一个 Store 读磁盘 ---
    fresh = store_mod.Store(os.path.join(tmp, "doupo.json"))
    await fresh.load()
    fb = await fresh.get_player(B)
    check(core.count_item(fb, "回灵丹") == 3,
          "赠予必须真落库（新进程读不到 = 只在内存里）")

    # --- 模糊匹配：包裹里真有的才能送 ---
    txt = await commands.cmd_gift(store, A, B, "回灵", 1)
    pa = await store.get_player(A)
    check("回灵丹" in txt and core.count_item(pa, "回灵丹") == 6,
          "「回灵」应模糊命中回灵丹：%s" % txt[:80])
    # 宝库目录里有、但自己包里没有的，不许送
    txt = await commands.cmd_gift(store, A, B, "血灵草", 1)
    check("没有" in txt, "包里没有的东西必须拒绝：%s" % txt[:80])

    # --- 失败：数量超过持有 ---
    pa, pb = await store.get_player(A), await store.get_player(B)
    before_a = dict(core.bag(pa))
    before_b = dict(core.bag(pb))
    txt = await commands.cmd_gift(store, A, B, "药渣", 99)
    check("不够" in txt, "超额应报不够：%s" % txt[:80])
    check(core.bag(pa) == before_a and core.bag(pb) == before_b,
          "超额失败必须零副作用")

    # --- 失败：数量 0 / 负数 ---
    for n in (0, -3):
        txt = await commands.cmd_gift(store, A, B, "药渣", n)
        check("无效" in txt, "数量 %d 应被挡：%s" % (n, txt[:60]))
    check(core.bag(await store.get_player(A)) == before_a, "数量非法必须零副作用")

    # --- 失败：送自己 / 未入道 / 没目标 ---
    txt = await commands.cmd_gift(store, A, A, "药渣", 1)
    check("自己" in txt, "自赠应被挡：%s" % txt[:60])
    txt = await commands.cmd_gift(store, A, "999999", "药渣", 1)
    check("未入道" in txt, "对方未入道应被挡：%s" % txt[:60])
    txt = await commands.cmd_gift(store, A, None, "药渣", 1)
    check("用法" in txt and "送 100" in txt,
          "缺目标应回用法且同时给出金币写法：%s" % txt[:80])
    check(core.bag(await store.get_player(A)) == before_a, "三类拒绝都必须零副作用")

    # --- 送空即清键（不留 0 值占格） ---
    txt = await commands.cmd_gift(store, A, B, "炎星石", 7)
    pa = await store.get_player(A)
    check("炎星石" not in core.bag(pa), "送空后应清掉这个键，实得 %s"
          % core.bag(pa))
    check("送空" in txt, "送空应有提示：%s" % txt[:80])

    # --- 原子性：第二步写库失败，双方都回滚 ---
    pa, pb = await store.get_player(A), await store.get_player(B)
    live_a, live_b = pa["bag"], pb["bag"]      # 活字典引用（外部代码手里拿的就是它）
    snap_a, snap_b = dict(live_a), dict(live_b)
    orig = store._upsert_player
    calls = {"n": 0}

    def boom(con, qq, p, s, ts):
        calls["n"] += 1
        if calls["n"] >= 2:
            raise RuntimeError("模拟写盘故障")
        return orig(con, qq, p, s, ts)

    store._upsert_player = staticmethod(boom)
    try:
        raised = False
        try:
            await store.transfer_item(A, B, "药渣", 2)
        except store_mod.StoreError:
            raised = True
        check(raised, "写盘故障必须抛 StoreError，不能静默半成功")
        check(core.bag(pa) == snap_a, "失败后施主包裹必须就地回滚，实得 %s"
              % core.bag(pa))
        check(core.bag(pb) == snap_b, "失败后受主包裹必须就地回滚，实得 %s"
              % core.bag(pb))
        # 活字典语义：回滚只能就地改，不能换 dict 对象
        check(pa["bag"] is live_a and pb["bag"] is live_b,
              "回滚替换了 bag dict 对象 —— 外部持有的引用会停在旧内容上")
    finally:
        store._upsert_player = orig
    # 回滚后磁盘也必须没变
    fresh = store_mod.Store(os.path.join(tmp, "doupo.json"))
    await fresh.load()
    check(core.count_item(await fresh.get_player(A), "药渣") == 4,
          "回滚必须连磁盘一起回滚（事务未 commit）")

    # 同一件物品两边都有时，只加不减错位
    pa, pb = await store.get_player(A), await store.get_player(B)
    core.add_item(pa, "药渣", 5)
    await store.put_player(pa)
    await commands.cmd_gift(store, A, B, "药渣", 5)
    pa, pb = await store.get_player(A), await store.get_player(B)
    check(core.count_item(pa, "药渣") == 4 and core.count_item(pb, "药渣") == 5,
          "两边各有存货时应各算各的：A=%d B=%d"
          % (core.count_item(pa, "药渣"), core.count_item(pb, "药渣")))

    # 金币旧路径必须还在
    pa, pb = await store.get_player(A), await store.get_player(B)
    ga, gb = int(pa["gold"]), int(pb["gold"])
    txt = await commands.cmd_give(store, A, B, 100)
    pa, pb = await store.get_player(A), await store.get_player(B)
    check("赠予" in txt and int(pa["gold"]) == ga - 100
          and int(pb["gold"]) == gb + 100,
          "金币旧路径不能因为赠物品而失效：%s" % txt[:60])


# ---------------------------------------------------------------------------
# 3. 源码级红线
# ---------------------------------------------------------------------------
def _read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return f.read()


def test_source_gates():
    cmds = _read("src/plugins/doupo/commands.py")
    st = _read("src/plugins/doupo/store.py")
    init = _read("src/plugins/doupo/__init__.py")

    # 赠送不许自己拿两次 put_player 拼（那正是会吞物品的写法）
    i = cmds.index("async def cmd_gift(")
    body = cmds[i:i + 2200].split("\n# ---")[0]
    check("store.transfer_item(" in body,
          "cmd_gift 必须走 store.transfer_item（原子双写）")
    check("put_player(" not in body,
          "cmd_gift 里出现 put_player = 又回到「两次独立事务」的老坑")
    # 不许把金币逻辑复制进来（金币仍归 cmd_give 唯一一处）
    check("gold" not in body, "cmd_gift 不该碰金币，金币走 cmd_give")

    # transfer_item 必须一次事务写两行
    j = st.index("async def transfer_item(")
    # 切到下一个方法为止，别把后面方法的事务算进来
    tbody = st[j:st.index("\n    async def ", j + 10)]
    check(tbody.count("with self._tx()") == 1,
          "transfer_item 必须恰好一个事务：%d" % tbody.count("with self._tx()"))
    check(tbody.count("_upsert_player(") == 2,
          "transfer_item 必须写两行：%d" % tbody.count("_upsert_player("))
    check("bag_a.clear()" in tbody and "bag_b.clear()" in tbody,
          "回滚必须就地 clear+update（替换 dict 会让别人手里的引用失效）")

    # matcher 必须按 parse_gift 分流，且保留 _extract_at
    i0 = init.index("@give.handle()")
    gbody = init[i0:init.index("# ---", i0)]
    check("commands.parse_gift(" in gbody,
          "give handler 必须用 parse_gift 分流金币/物品")
    # 「分流」这件事只看有没有调用是不够的：`if False:` 也保留调用文本。
    # 必须钉住条件本身，否则物品入口可以静默变成死代码。
    check("if item:" in gbody,
          "入口必须真的按 parse_gift 的结果分流（缺 `if item:` 即物品入口失效）")
    check("commands.cmd_gift(" in gbody and "commands.cmd_give(" in gbody,
          "give handler 必须同时保留 cmd_gift 与 cmd_give 两条路")
    check("_extract_at(event)" in gbody, "give handler 必须仍取 @ 目标")


# ---------------------------------------------------------------------------
# runner
# ---------------------------------------------------------------------------
def main():
    test_parse_gift()
    asyncio.run(e2e())
    test_source_gates()
    print("=" * 60)
    if _FAILS:
        for f in _FAILS:
            print("FAIL", f)
        print("FAILED", len(_FAILS), "/", _N)
        sys.exit(1)
    print("PASS", _N, "条断言")


if __name__ == "__main__":
    main()
