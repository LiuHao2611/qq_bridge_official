# -*- coding: utf-8 -*-
"""斗破苍穹 · 全部口令的业务级测试（自带 runner，exit-code 可验证）。

策略：不启动 QQ / nonebot，直连 commands.*（真实业务层）+ 临时 Store，
在**同一个 asyncio 事件循环**里按真实玩家顺序走一遍全流程，断言：
  - 未入道时每条口令都回 NOT_ENTERED（防"没角色也能改数据"）
  - 返回文案符合预期（命中关键字）
  - 存档状态被正确写入（金币 / 修为 / 武器 / 背包 / 异火）

运行：
    python src/plugins/doupo/test_commands.py     # exit 0 = 全绿
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import time
import types

# PLUGINS_DIR 供 `from doupo import ...`；REPO_ROOT 供插件 __init__.py 里的
# `from src.game_mode import ...`。裸跑脚本时 CWD 不保证是仓库根，两者缺一即 ModuleNotFoundError。
PLUGINS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO_ROOT = os.path.dirname(os.path.dirname(PLUGINS_DIR))
for _p in (REPO_ROOT, PLUGINS_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# 机遇是概率事件，会让既有金币/文本断言变 flaky；这里默认关掉，
# 随机逻辑本身由 test_chance.py 用注入 rng 做确定性验收。
os.environ["DOUPO_CHANCE_RATE"] = "0"
# 爬塔会话现在会落盘（2026-11-17）：给单测一个临时文件，
# 免得把测试会话写进插件包 data/ 或相互串档。
os.environ.setdefault(
    "DOUPO_TOWER_SESSION_PATH",
    os.path.join(tempfile.mkdtemp(prefix="doupo_tower_"), "tower_session.json"),
)


def _stub_nonebot():
    nb = types.ModuleType("nonebot")
    nb.get_driver = lambda: types.SimpleNamespace(on_startup=lambda f: f)
    nb.get_bot = lambda: types.SimpleNamespace(send=lambda *a, **k: None)

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
    sys.modules["nonebot.adapters.onebot.v11"].MessageEvent = object
    sys.modules["nonebot.adapters.onebot.v11"].MessageSegment = \
        types.SimpleNamespace(image=lambda data: ("[image]", len(data or b"")))
    sys.modules["nonebot.adapters.onebot.v11"].Message = object


_stub_nonebot()

from doupo import commands, core, config as cfg  # noqa: E402
from doupo.store import Store  # noqa: E402

G = "900000001"
A, B = "100001", "100002"

_results = []


def check(name, cond, detail=""):
    _results.append((name, bool(cond), detail))


async def main():
    tmp = tempfile.mkdtemp(prefix="doupo_cmd_")
    store = Store(os.path.join(tmp, "doupo.json"))
    await store.load()

    # ------------------------------------------------------------------
    # 0. 未入道：所有需要角色的口令都必须挡回去
    # ------------------------------------------------------------------
    guarded = [
        ("人物", commands.cmd_panel(store, A)),
        ("修炼", commands.cmd_cultivate(store, A)),
        ("突破", commands.cmd_break(store, A)),
        ("战斗", commands.cmd_fight(store, A)),
        ("强化", commands.cmd_enhance(store, A)),
        ("斗技", commands.cmd_skills(store, A)),
        ("异火", commands.cmd_flames(store, A)),
        ("宝库", commands.cmd_shop(store, A)),
        ("炼药", commands.cmd_recipes(store, A)),
        ("背包", commands.cmd_bag(store, A)),
        ("任务", commands.cmd_tasks(store, A)),
    ]
    for label, coro in guarded:
        txt = await coro
        check(f"未入道 · {label} 被拦截", txt == commands.NOT_ENTERED, txt[:40])

    # ------------------------------------------------------------------
    # 1. 菜单 / 帮助
    # ------------------------------------------------------------------
    menu = commands.cmd_menu()
    check("菜单含 斗破菜单 口令", "斗破菜单" in menu)
    # 关键：菜单条数必须等于 __init__.py 里注册的 matcher 数（e2e 里再做静态断言）
    # 2026-11-16 新增「学斗技」→ 29 + 1 = 30（必须仍等于 __init__.py 的 matcher 数）
    # 2026-11-17 新增爬塔副本 4 条（爬塔/塔层/塔结算/装备）→ 33 + 4 = 37
    # 2026-11-17 新增电梯/周副本/周本状态 3 条 → 37 + 3 = 40
    # 2026-11-19 新增「收服」主动收服入口（三层投放补齐）→ 51 + 1 = 52
    # 2026-11-24 新增「宗门斗技」（本门技能池，与「斗技」= 已学全部互补）→ 52 + 1 = 53
    # 2026-11-25 新增「外观 · 卡片主题」分组（主题口令此前图卡上根本没写）→ 53 + 1 = 54
    # 2026-11-25 新增「查看 <物品名>」物品详情 → 54 + 1 = 55
    # 2026-11-25 晚「电梯」整条换成「自动爬塔」（一换一）→ 仍 55
    check("菜单条数 = menu_total()", commands.menu_total() == 55,
          f"menu_total={commands.menu_total()}")
    # 新设计：菜单是唯一切换口（斗破菜单），其余口令全部纯中文、不再带前缀
    lines = [l.split(" —— ")[0].strip() for l in menu.splitlines() if " —— " in l]
    bad = [l for l in lines if "\u0064\u0070" in l]
    check("菜单内不再含任何前缀残留", not bad, f"违例={bad}")
    # 卡片 tip / 游戏内错误提示同样用户可见，只查菜单文本会漏（2026-11-07 实测漏过 8 处）。
    # 用 ast 扫全部生产模块的字符串常量，docstring 也算，防止旧写法留在说明里被照抄。
    # 测试文件不扫：它们用转义写法做自举断言。
    import ast
    import pathlib
    here = pathlib.Path(__file__).resolve().parent
    offenders = []
    for fn in ("__init__.py", "commands.py", "card.py", "core.py",
               "config.py", "store.py", "avatar.py", "_throttle.py"):
        src = (here / fn).read_text(encoding="utf-8")
        for node in ast.walk(ast.parse(src)):
            if (isinstance(node, ast.Constant) and isinstance(node.value, str)
                    and "\u0064\u0070" in node.value):
                offenders.append(f"{fn}:{node.lineno}")
    check("全模块用户可见文案无前缀残留", not offenders, f"违例={offenders[:6]}")
    check("帮助含暴击公式", "暴击% = 10" in commands.cmd_help())

    # ------------------------------------------------------------------
    # 2. 入道 → 人物
    # ------------------------------------------------------------------
    r = await commands.cmd_enter(store, A, "萧炎", G)
    check("入道 成功", "玄重尺" in r and "斗之气一段" in r, r[:50])
    p = await store.get_player(A)
    check("入道写入存档", p is not None and p["name"] == "萧炎")
    check("入道赠金币", p["gold"] == cfg.START_GOLD, f"gold={p['gold']}")
    check("入道赠回灵丹×5", core.count_item(p, "回灵丹") == cfg.START_HP_POTION,
          f"回灵丹={core.count_item(p, '回灵丹')}")
    # 新模型：入道只领悟 1 门低阶技（用户 2026-11-16 要求）
    check("入道只领悟一门", isinstance(p.get("learned"), list) and len(p["learned"]) == 1,
          f"learned={p.get('learned')}")
    check("入门技已上阵", bool(p.get("loadout")) and p["loadout"][0] == p["learned"][0],
          f"loadout={p.get('loadout')}")
    # 后续用例（升级/战斗/斗技列表）按「已逐一学完核心技」推进，补领悟并上阵
    from doupo import skills as _sk
    p["learned"] = list(dict.fromkeys(
        (p.get("learned") or []) + [s["name"] for s in cfg.SKILLS]))
    p["loadout"] = p["learned"][:cfg.LOADOUT_SIZE]
    await store.put_player(p)
    check("群被登记", str(G) in await store.groups())
    r2 = await commands.cmd_enter(store, A, "萧炎", G)
    check("重复入道被挡", "已踏入" in r2, r2[:30])

    panel = await commands.cmd_panel(store, A)
    check("人物 含境界", "斗之气一段" in panel)
    check("人物 含玄重尺", "玄重尺" in panel)
    check("人物 含战力", "战力" in panel)

    # ------------------------------------------------------------------
    # 3. 修炼 / 突破
    # ------------------------------------------------------------------
    r = await commands.cmd_cultivate(store, A)
    check("修炼 有回包", "修为" in r, r[:40])
    r = await commands.cmd_break(store, A)
    check("突破 修为不足被挡", "修为不足" in r, r[:40])
    p = await store.get_player(A)
    p["exp"] = cfg.exp_need(1) + 10
    await store.put_player(p)
    r = await commands.cmd_break(store, A)
    check("突破 成功", "突破成功" in r and "斗之气二段" in r, r[:60])
    p = await store.get_player(A)
    check("突破后等级 =2", int(p["lv"]) == 2, f"lv={p['lv']}")
    check("突破后回满血", p["hp"] == core.stats(p)["体力"])

    # 跨大境界（2026-11-20 起须先战原著 BOSS）：九星斗师(27) → 一星大斗师(28)。
    # 用必胜 rng 注入，验证原著 BOSS 登场引言 + 胜利后突破 + 文案含起终点。
    p = await store.get_player(A)
    p["lv"] = 27                      # 九星斗师（斗师境最后一星）
    p["exp"] = cfg.exp_need(27) + 10
    core._refill(p)
    await store.put_player(p)
    r = await commands.cmd_break(store, A, rng=lambda: 0.01)
    check("九星跨境界 必遇原著 BOSS（云棱）", "云棱" in r, r[:100])
    check("突破文案无「快照战力」", "快照战力" not in r and "心魔劫影" not in r, r[:100])
    check("突破文案有原著引言", "我是云岚宗的大长老，云棱" in r, r[:120])
    check("跨境界 文案含起终点", "九星斗师" in r and "一星大斗师" in r, r[:120])
    check("跨境界 用境界突破措辞", "境界突破" in r, r[:120])
    p = await store.get_player(A)
    check("跨境界 等级 =28", int(p["lv"]) == 28, f"lv={p['lv']}")
    # 境界内晋升：三星斗师(12) → 四星斗师(13)，这才是用户要的顺序
    p["lv"] = 21
    p["exp"] = cfg.exp_need(21) + 10
    await store.put_player(p)
    r = await commands.cmd_break(store, A)
    check("同境界 三星→四星", "三星斗师" in r and "四星斗师" in r, r[:80])
    check("同境界 用突破成功措辞", "突破成功" in r, r[:40])
    p = await store.get_player(A)
    check("同境界 等级 =22", int(p["lv"]) == 22, f"lv={p['lv']}")
    # 还原成 2 级，别把后面的战斗/数值断言带偏
    p["lv"] = 2
    await store.put_player(p)

    # ------------------------------------------------------------------
    # 4. 战斗（含 CD）
    # ------------------------------------------------------------------
    r = await commands.cmd_fight(store, A)
    check("战斗 遭遇妖兽", "遭遇" in r, r[:60])
    p = await store.get_player(A)
    check("战斗写入 last_fight", float(p.get("last_fight", 0)) > 0)
    r = await commands.cmd_fight(store, A)
    check("战斗 CD 生效", "调息" in r, r[:40])
    # 清 CD 后必须仍能打（斗气见底也只是转普攻，战斗照常开打）
    p = await store.get_player(A)
    p["last_fight"] = 0
    await store.put_player(p)
    r = await commands.cmd_fight(store, A)
    check("战斗 第二场正常开打", "遭遇" in r, r[:40])

    # ------------------------------------------------------------------
    # 5. 强化
    # ------------------------------------------------------------------
    p = await store.get_player(A)
    core.take_item(p, "炎星石", core.count_item(p, "炎星石"))   # 清空（take 数量不足时不扣）
    await store.put_player(p)
    r = await commands.cmd_enhance(store, A)
    check("强化 缺石被挡", "炎星石不足" in r, r[:60])
    core.add_item(p, "炎星石", 30)
    await store.put_player(p)
    r = await commands.cmd_enhance(store, A)
    check("强化 成功", "强化成功" in r, r[:60])
    p = await store.get_player(A)
    check("强化后武器 =1", int(p["weapon"]) == 1, f"weapon={p['weapon']}")
    # 一路强到十级：必须蜕变且封顶
    core.add_item(p, "炎星石", 200)
    await store.put_player(p)
    evolved = ""
    for _ in range(12):
        t = await commands.cmd_enhance(store, A)
        if "异火恒古尺" in t:
            evolved = t
        if "巅峰" in t:
            break
    check("强化 十级蜕变", "异火恒古尺" in evolved, evolved[:60])
    p = await store.get_player(A)
    check("武器封顶 internal=9", int(p["weapon"]) == 9, f"weapon={p['weapon']}")
    r = await commands.cmd_enhance(store, A)
    check("强化 满级后挡回", "无法再强化" in r, r[:40])
    # 还原成 0 级，别影响后面战力断言
    p["weapon"] = 0
    await store.put_player(p)

    # ------------------------------------------------------------------
    # 6. 斗技 / 升级
    # ------------------------------------------------------------------
    r = await commands.cmd_skills(store, A)
    check("斗技 列出 4 门", all(s["name"] in r for s in cfg.SKILLS))
    r = await commands.cmd_skill_up(store, A, "龙吟碎")
    # 2026-11-25 线上反馈「❌ 焚血散不足」不说升哪门技，玩家无从判断是不是点错了技名。
    # 契约从「含『不足』」收紧：技名 / 材料名 / 需求量 / 持有量，四件都要有。
    check("升级 缺料文案点名技名与数量",
          "龙吟碎" in r and "焚血散 ×1" in r and "你有 0" in r, r[:80])
    check("升级 缺料文案给出补救路径", "炼制" in r, r[:120])
    p = await store.get_player(A)
    core.add_item(p, cfg.SKILL_UP_ITEM, 10)
    await store.put_player(p)
    r = await commands.cmd_skill_up(store, A, "龙吟碎")
    check("升级 成功", "升至" in r, r[:60])
    p = await store.get_player(A)
    check("斗技等级 =2", int(p["skills"]["龙吟碎"]) == 2)
    r = await commands.cmd_skill_up(store, A, "不存在")
    check("升级 未知斗技被挡", "无此斗技" in r, r[:40])

    # ------------------------------------------------------------------
    # 6b. 斗技详情：说明必须是真的（有数值/有来源/有原著），不是空壳
    # ------------------------------------------------------------------
    p = await store.get_player(A)
    one = core.skill_pool(p)[0]["name"]
    r = await commands.cmd_skill_info(store, A, one)
    check("详情 含技名", one in r, r[:40])
    check("详情 含完整说明", "出处：" in r and len(r) > 120, f"len={len(r)}")
    check("详情 含数值口径", "斗气" in r and "%" in r, r[:80])
    r = await commands.cmd_skill_info(store, A, "")
    check("详情 缺参数给用法", "用法" in r and "可选" in r, r[:60])
    r = await commands.cmd_skill_info(store, A, "不存在的斗技")
    check("详情 未知斗技被挡", "无此斗技" in r, r[:40])


    # ------------------------------------------------------------------
    # 7. 异火 / 养火
    # ------------------------------------------------------------------
    r = await commands.cmd_flames(store, A)
    check("异火 列出 5 种", all(f["name"] in r for f in cfg.FLAMES))
    # 2026-11-18 A 方案：未收服不可养 → 测试前置先把青莲地心火置为已收服
    p = await store.get_player(A)
    p["flames"]["青莲地心火"] = 1
    await store.put_player(p)
    r = await commands.cmd_feed(store, A, "青莲地心火")
    check("养火 无药渣被挡", "药渣" in r, r[:40])
    p = await store.get_player(A)
    core.add_item(p, "药渣", 500)
    await store.put_player(p)
    r = await commands.cmd_feed(store, A, "青莲地心火")
    check("养火 喂养生效", "药渣" in r and "级" in r, r[:60])
    p = await store.get_player(A)
    check("异火等级上升", int(p["flames"].get("青莲地心火", 0)) >= 1,
          f"lv={p['flames'].get('青莲地心火')}")
    # 喂到圆满后必须封顶，不能无限涨
    core.add_item(p, "药渣", 10000)
    await store.put_player(p)
    for _ in range(10):
        await commands.cmd_feed(store, A, "青莲地心火")
    p = await store.get_player(A)
    check("异火封顶于 FLAME_MAX_LV",
          int(p["flames"].get("青莲地心火", 0)) == cfg.FLAME_MAX_LV,
          f"lv={p['flames'].get('青莲地心火')} max={cfg.FLAME_MAX_LV}")
    r = await commands.cmd_feed(store, A, "青莲地心火")
    check("养火 满级后挡回", "圆满" in r, r[:40])
    r = await commands.cmd_feed(store, A, "帝炎")
    check("养火 帝炎拒绝喂养", "只认斗帝" in r, r[:40])

    # ------------------------------------------------------------------
    # 8. 宝库 / 买
    # ------------------------------------------------------------------
    r = await commands.cmd_shop(store, A)
    check("宝库 有货", "炎星石" in r)
    r = await commands.cmd_buy(store, A, "血灵草 2")
    check("买 成功", "购入" in r and "血灵草" in r, r[:60])
    p = await store.get_player(A)
    check("背包收到血灵草", core.count_item(p, "血灵草") >= 2,
          f"血灵草={core.count_item(p, '血灵草')}")
    r = await commands.cmd_buy(store, A, "不存在的东西")
    check("买 未知货物被挡", "没有" in r, r[:40])
    p = await store.get_player(A)
    p["gold"] = 1
    await store.put_player(p)
    r = await commands.cmd_buy(store, A, "炎星石 5")
    check("买 金币不足被挡", "金币不足" in r, r[:40])
    p["gold"] = cfg.START_GOLD
    await store.put_player(p)
    # 2026-11-20 用户「买东西别限制 99」：给足金币买 150，必须真收到 150。
    p = await store.get_player(A)
    p["gold"] = 10 ** 7
    await store.put_player(p)
    before150 = core.count_item(p, "血灵草")
    r = await commands.cmd_buy(store, A, "血灵草 150")
    p = await store.get_player(A)
    check("买 150 不被夹到 99",
          core.count_item(p, "血灵草") - before150 == 150,
          f"实际+{core.count_item(p, '血灵草') - before150} | {r[:50]}")
    p["gold"] = cfg.START_GOLD
    await store.put_player(p)

    # ------------------------------------------------------------------
    # 9. 炼药 / 炼制 / 服丹 / 背包
    # ------------------------------------------------------------------
    r = await commands.cmd_recipes(store, A)
    # 2026-11-16：等级不够的丹药不展示。A 为低境界，应见一~四品常用丹方，
    # 且帝品「灭神丹/九幽散」不得出现（min_lv 按品阶映射到斗帝境界）。
    check("炼药 列出丹方（等级过滤）",
          "回灵丹" in r and ("炼药师" in r or cfg.ALCH_NONE_NAME in r)
          and "灭神丹" not in r and "九幽散" not in r,
          r[:80])
    p = await store.get_player(A)
    p["hp"] = 1
    core.add_item(p, "回灵丹", 3)
    # 清空回灵丹所需药材：前面几轮炼制有成有败，余量不确定，
    # 不清就会偶发"材料居然够"，让这条断言飘（2026-11-15 实测 3 次跑 1 次红）。
    for k in cfg.RECIPE_BY_NAME["回灵丹"]["need"]:
        core.add_item(p, k, -core.count_item(p, k))
    await store.put_player(p)
    r = await commands.cmd_craft(store, A, "回灵丹")
    check("炼制 材料不足被挡", "材料不足" in r, r[:60])
    # 备齐材料（血灵草×2 + 紫叶兰×1）后必须炼得出来
    for _ in range(3):
        for k, v in cfg.RECIPE_BY_NAME["回灵丹"]["need"].items():
            core.add_item(p, k, v)
        await store.put_player(p)
        r = await commands.cmd_craft(store, A, "回灵丹")
        if "丹成" in r or "失败" in r:
            break
        p["last_craft"] = 0          # 清冷却继续试
    check("炼制 有回包（丹成/失败）", ("丹成" in r) or ("失败" in r), r[:60])
    r = await commands.cmd_craft(store, A, "不存在的丹")
    check("炼制 未知丹方被挡", "无此丹方" in r, r[:40])
    r = await commands.cmd_use(store, A, "回灵丹")
    check("服丹 回血", "气血" in r or "服下" in r, r[:60])
    p = await store.get_player(A)
    check("服丹后回血", int(p["hp"]) > 1, f"hp={p['hp']}")
    bag = await commands.cmd_bag(store, A)
    check("背包 有内容", "包裹" in bag and "×" in bag)

    # ------------------------------------------------------------------
    # 10. 任务
    # ------------------------------------------------------------------
    r = await commands.cmd_tasks(store, A)
    check("任务 含主线", "主线" in r, r[:60])
    r = await commands.cmd_task_accept(store, A)
    check("接任务 成功", "接取支线" in r, r[:60])
    r = await commands.cmd_task_accept(store, A)
    check("接任务 重复接被挡", "未交" in r, r[:60])
    r = await commands.cmd_task_submit(store, A)
    check("交任务 未达标被挡", "尚未完成" in r, r[:60])
    r = await commands.cmd_main_submit(store, A)
    # 2026-11-25：门槛分两种（进度不足 / 进度够但境界不够），都算「被挡」；
    # 唯一硬判据是**没有出现完成标志 🏅**（出现即等于发了奖励）。
    check("领主线 未达标被挡",
          "🏅" not in r and ("未完成" in r or "修为未到" in r), r[:60])

    # 主线提交必须**落库**（2026-11-20：旧版只改内存 p，重启章节回退、奖励蒸发）。
    # 验证方式：另起一个 Store 从同一文件读回，不信内存里那个 dict。
    pa = await store.get_player(A)
    if int(pa.get("main_idx", 0)) < len(cfg.MAIN_TASKS):
        _mt = cfg.MAIN_TASKS[int(pa["main_idx"])]
        pa["main_progress"] = int(_mt["target"])
        # 2026-11-25 章节门槛（用户拍板 G1=A）：交任务须达本章境界上限，
        # 故先把等级提到 require_lv 再交；「不够必须挡」由 test_core 的反证守。
        if _mt.get("require_lv"):
            pa["lv"] = int(_mt["require_lv"])
            pa["hp"] = core.stats(pa)["体力"]
            pa["mp"] = core.mp_max(pa)
        await store.put_player(pa)
        before_idx = int(pa.get("main_idx", 0))
        before_gold = int(pa.get("gold", 0))
        r = await commands.cmd_main_submit(store, A)
        st2 = Store(os.path.join(tmp, "doupo.json"))
        await st2.load()
        q = await st2.get_player(A)
        check("主线提交落库（章节推进 + 金币入账）",
              int(q.get("main_idx", 0)) == before_idx + 1
              and int(q.get("gold", 0)) > before_gold,
              "idx %s→%s gold %s→%s" % (before_idx, q.get("main_idx"),
                                        before_gold, q.get("gold")))

    # ------------------------------------------------------------------
    # 11. 排行 / 抢 / 送
    # ------------------------------------------------------------------
    await commands.cmd_enter(store, B, "药老", G)
    r = await commands.cmd_rank(store, G)
    check("排行 列出两人", "萧炎" in r and "药老" in r, r[:80])
    r = await commands.cmd_rank(store, "999999")
    check("排行 空群有提示", "尚无人" in r, r[:40])

    pa = await store.get_player(A)
    pb = await store.get_player(B)
    gold_a, gold_b = int(pa["gold"]), int(pb["gold"])
    r = await commands.cmd_give(store, A, B, 1000)
    check("送 成功", "赠予" in r, r[:40])
    pa = await store.get_player(A)
    pb = await store.get_player(B)
    check("送 金额正确", int(pb["gold"]) == gold_b + 1000
          and int(pa["gold"]) == gold_a - 1000,
          f"A {gold_a}->{pa['gold']}  B {gold_b}->{pb['gold']}")
    r = await commands.cmd_give(store, A, B, -5)
    check("送 负数被挡", "无效" in r, r[:40])
    r = await commands.cmd_give(store, A, "999999", 100)
    check("送 对方未入道被挡", "尚未入道" in r, r[:40])

    r = await commands.cmd_rob(store, A, B)
    check("抢 有结果", "金币" in r, r[:60])
    r = await commands.cmd_rob(store, A, None)
    check("抢 缺目标被挡", "用法" in r, r[:40])

    # ------------------------------------------------------------------
    # 12. 爬塔副本 / 装备替换（2026-11-17）
    # ------------------------------------------------------------------
    # 塔会话是进程内 STORE，先清干净避免跨用例串味。
    commands.ts.STORE.clear(A)
    r = await commands.cmd_tower_status(store, A)
    check("塔层 无会话给入口", "试炼之塔" in r and "历史最高" in r, r[:60])

    r = await commands.cmd_tower(store, A, "")
    check("爬塔 首层出战报", "第 1 层" in r, r[:60])
    check("爬塔 有战利品或阵亡结算", "1." in r or "力竭" in r, r[:60])

    # 强制一段进度，验结算奖与防重复领
    st = commands.ts.STORE.begin(A, seed="cmdtest")
    st["floor"] = 3
    st["temp_hp"] = 100
    commands.ts.STORE.save(A, st)
    gold_before = int((await store.get_player(A))["gold"])
    r = await commands.cmd_tower_settle(store, A)
    check("塔结算 按层发奖", "3 层" in r and "金币" in r, r[:60])
    pa2 = await store.get_player(A)
    check("塔结算 金币到手", int(pa2["gold"]) > gold_before,
          f"{gold_before} -> {pa2['gold']}")
    check("塔结算 记录最高层", int(pa2.get("tower_best", 0)) >= 3, str(pa2.get("tower_best")))
    r = await commands.cmd_tower_settle(store, A)
    check("塔结算 不可重复领", "没有可结算" in r, r[:40])
    commands.ts.STORE.clear(A)

    r = await commands.cmd_equip(store, A, "")
    check("装备 无参列可装备", "可装备" in r and "玄重尺" in r, r[:60])
    r = await commands.cmd_equip(store, A, "血斧")
    check("装备 未拥有被挡", "没有" in r or "未拥有" in r, r[:40])
    pa3 = await store.get_player(A)
    core.grant_weapon(pa3, "血斧")
    await store.put_player(pa3)
    r = await commands.cmd_equip(store, A, "血斧")
    check("装备 换成血斧", "血斧" in r and "力量" in r, r[:60])
    pa4 = await store.get_player(A)
    check("装备 已写回存档", core.equipped_weapon(pa4) == "血斧", core.equipped_weapon(pa4))
    r = await commands.cmd_equip(store, A, "玄重尺")
    check("装备 换回玄重尺", "玄重尺" in r, r[:40])

    # 层数推进：真实踩过的坑——cmd_tower 忘了 st["floor"]=r["floor"] 且没 save，
    # 导致「爬塔」永远停在第 1 层，可以无限刷第一层奖励。
    C = "100009"
    pc = core.create_player(C, "塔客", G)
    pc["lv"] = 100
    pc["hp"] = core.stats(pc)["体力"]
    pc["mp"] = core.mp_max(pc)
    await store.put_player(pc)
    won = False
    stx = None
    for _ in range(6):
        commands.ts.STORE.clear(C)
        r = await commands.cmd_tower(store, C, "")
        stx = commands.ts.STORE.get(C)
        if "力竭" not in r:
            won = True
            break
    check("爬塔 高等级首层必胜", won, r[:50])
    if won:
        check("爬塔 胜利后层数推进", int(stx["floor"]) == 1, str(stx["floor"]))
        # 2026-11-17 用户要求：战利品直接回 1/2/3，不要「爬塔 1」
        r = await commands.cmd_pick(store, C, 1)
        check("裸数字 1 直接领取战利品", "获得" in r, r[:50])
        check("领取后待选清空",
              not (commands.ts.STORE.get(C) or {}).get("pending_loot"))
        r = await commands.cmd_tower(store, C, "")        # 再打下一层
        st2 = commands.ts.STORE.get(C)
        check("爬塔 能进入第 2 层", "第 2 层" in r or (st2 and int(st2["floor"]) >= 2),
              (r[:50], st2 and st2.get("floor")))

    # 超时作废：把截止时间拨到 2 分钟前，再回 1 必须领不到（奖励失去）
    s = commands.ts.STORE.begin(C, seed="ttl")
    commands.ts.set_loot(s, [
        {"kind": "hp", "key": "回春散", "v": 0.35, "label": "回春散", "desc": "回复35%"},
        {"kind": "hp", "key": "b", "v": 0.1, "label": "备选B", "desc": "x"},
        {"kind": "hp", "key": "c", "v": 0.1, "label": "备选C", "desc": "x"},
    ], ttl=60.0, now=time.time() - 120)
    commands.ts.STORE.save(C, s)
    r = await commands.cmd_pick(store, C, 1)
    check("超时 1 分钟领不到奖励", "超时作废" in r, r[:50])
    check("超时后待选被清空",
          not (commands.ts.STORE.get(C) or {}).get("pending_loot"))
    r = await commands.cmd_pick(store, C, 2)
    check("无待选时保持沉默（不吞群聊）", r == "", r[:40])
    commands.ts.STORE.clear(C)

    # 超时不是丢：把会话 ts 拨到 TTL 之前，「塔结算」必须补结算并发奖
    # （2026-11-17 用户「爬塔层数被你重置掉了」：早期实现直接把过期会话删掉）。
    s = commands.ts.STORE.begin(C, seed="overtime")
    s["floor"] = 5
    s["temp_hp"] = 123
    commands.ts.STORE.save(C, s)
    s["ts"] = time.time() - (commands.ts.DEFAULT_TTL + 60)   # save 只改 ts，这里再拨旧
    gold_before = int((await store.get_player(C))["gold"])
    r = await commands.cmd_tower_settle(store, C)
    gold_after = int((await store.get_player(C))["gold"])
    check("超时会话 塔结算仍发奖", "自动结算" in r and gold_after > gold_before, r[:60])
    check("超时结算后会话已清空", commands.ts.STORE.get(C) is None)

    # 机遇竞猜：属于同一类「等玩家回话」的业务，必须同样教会裸数字作答 + 60 秒时限，
    # 且裸数字入口要能直接作答（2026-11-17 用户要求统一口径）。
    from doupo import quiz_session as _qs  # noqa: PLC0415
    ch_quiz = {"name": "古墓残卷", "text": "残卷上写着一道题。", "tone": "good",
               "quiz": {"question": "异火榜共收录多少种异火？",
                        "options": ["十九", "二十", "二十二"], "answer": 1,
                        "reward": {"gold": 120}}}
    txt = "\n".join(commands._chance_lines(ch_quiz, A))
    check("机遇竞猜 教会直接回 1/2/3", "1/2/3" in txt, txt[-60:])
    check("机遇竞猜 说明时限秒数", "%d 秒" % cfg.CHOICE_TTL in txt, txt[-60:])
    r = await commands.cmd_pick(store, A, 2)
    check("竞猜 裸数字 2 直接作答", "答对" in r and "金币" in r, r[:50])
    # 序号必须从 1 开始，且「照屏幕上的数字回答」必须命中同一个选项。
    # 真实踩过的错位：选项按 0 开始打印，玩家回 2 却判到第 2 个（0-based 下标 2）。
    txt2 = "\n".join(commands._chance_lines(ch_quiz, A))
    check("竞猜选项从 1 编号", "1.十九 / 2.二十 / 3.二十二" in txt2, txt2[-60:])
    r = await commands.cmd_pick(store, A, 3)
    check("回 3 命中屏幕上第 3 项（判错并报正确项）",
          "答错" in r and "2.二十" in r, r[:60])
    _qs.STORE.clear(A)

    # ------------------------------------------------------------------
    # 13. 存档落盘可回读
    # ------------------------------------------------------------------
    store2 = Store(os.path.join(tmp, "doupo.json"))
    await store2.load()
    back = await store2.get_player(A)
    check("存档可回读", back is not None and back["name"] == "萧炎")
    check("回读等级一致", int(back["lv"]) == int(pa["lv"]),
          f"{back.get('lv')} vs {pa.get('lv')}")


def _run():
    asyncio.run(main())
    ok = 0
    for name, passed, detail in _results:
        print(("PASS  " if passed else "FAIL  ") + name + ("" if passed else f"  -> {detail}"))
        ok += 1 if passed else 0
    print("=" * 70)
    print(f"CMD_TOTAL={len(_results)} PASSED={ok} FAILED={len(_results) - ok}")
    return 0 if ok == len(_results) else 1


if __name__ == "__main__":
    sys.exit(_run())
