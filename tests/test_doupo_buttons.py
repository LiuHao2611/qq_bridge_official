# -*- coding: utf-8 -*-
"""斗破卡片按钮守门（2026-11-16，prove-it，独立基准）。

## 用户需求

> 既然官方机器人发的消息可以配置按钮，那就优化一下斗破小游戏，
> 给每个卡片配置上合理的操作按钮，注意功能不要重叠

## 本文件守什么（六道硬守门，全部用**第二套独立实现**，不信任被测模块）

① **零编造**：每个按钮 data 必须 ∈ 从 ``commands.MENU_GROUPS`` 独立 AST 派生的
   真实口令全集（本文件自己写一遍遍历，不调用 doupo_buttons.real_commands）；
② **不含自己**：任何卡的按钮都不得等于该卡自己的触发口令（点了等于没点）；
③ **不含主菜单**：不得出现 斗破菜单/斗破口令/修仙菜单/帮助（用户「去掉主菜单按钮」）；
④ **功能不重叠**：任意两张卡的按钮组合不得完全相同（杜绝「每张卡都一样」的全局导航）；
   且同一张卡内按钮不得重复；
⑤ **可分发性**：每个按钮口令都必须在 ``button_dispatch._PLAN`` 里有条目，且 plan
   指向的 commands 函数真实存在（AST 校验），否则点击会「无反应」；
⑥ **发送隔离**：``qq_keyboard.buttons_for("doupo:<card>")`` 返回斗破按钮，
   而 game_tools 既有口令（裂隙/赏金/帮助）行为**逐字节不变**。

注入验证 ``test_injection_guards``：把自己/主菜单/编造口令/漏配 plan 四类真实
错误放回，断言守门报错 —— 证明守门不是摆设。

跑法：python tests/test_doupo_buttons.py（宿主纯标准库）
      docker exec -i qq-bridge-official /opt/venv/bin/python -m pytest tests/test_doupo_buttons.py -v
"""
from __future__ import annotations

import ast
import importlib.util
import re
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_CH = _ROOT / "src/plugins/qq_bridge/channel"
_DOUPO = _ROOT / "src/plugins/doupo"
_CMD_PY = _DOUPO / "commands.py"
#: 命令层全族（commands.py / commands_puppet.py / 以后继续拆出来的 commands_*.py）。
#: 「函数是否存在」必须按全族判，只扫入口文件会把 re-export 的当成不存在。
_CMD_FAMILY = tuple(sorted(_DOUPO.glob("commands*.py")))
_DISPATCH_PY = _DOUPO / "button_dispatch.py"

_MENU_CMDS = frozenset({"斗破菜单", "斗破口令", "修仙菜单", "帮助"})

#: **不出图**的 card_key 族（不要求存在 render_<key>）：
#:   * 提示卡（auto_tower_prompt）：只挂 custom 带参按钮，本身就发一条确认提示；
#:   * 回执族（fight_text / craft_text …）：回复是文本，卡面另有 render_* 负责。
#: 事实源在 doupo_buttons.py 的 NO_RENDER_CARD_KEYS，这里只做兜底（模块加载失败时）。
_SPECIAL_CARD_KEYS_FALLBACK = frozenset({
    "auto_tower_prompt",
    "fight_text", "battle_text", "review_text",
    "task_panel", "main_submit", "task_submit",
    "tower_text", "weekly_text",
    "cult_text", "equip_text", "skill_text", "flame_text",
    "buy_text", "craft_text", "use_text", "shop_text",
    "enter_text", "puppet_text", "duel_text",
    "setup_text",
})


def _special_card_keys() -> frozenset:
    """优先读被测模块的事实源；读不到才用兜底集（纯 AST 模式下也能跑）。"""
    return frozenset(getattr(db, "NO_RENDER_CARD_KEYS", _SPECIAL_CARD_KEYS_FALLBACK))


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _safe_load(path: Path, name: str):
    try:
        return _load(path, name)
    except Exception:  # noqa: BLE001 — 宿主缺依赖时降级为纯 AST 校验
        return None


# ---------------------------------------------------------------------------
# 独立基准（自己写 AST 遍历，绝不调用 doupo_buttons.real_commands）
# ---------------------------------------------------------------------------
def _baseline_real_commands() -> tuple[str, ...]:
    """独立 AST：MENU_GROUPS 元组的每条口令取首词（保序去重）。"""
    tree = ast.parse(_CMD_PY.read_text(encoding="utf-8"))
    groups = None
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.value, (ast.Tuple, ast.List)):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id == "MENU_GROUPS":
                    groups = ast.literal_eval(node.value)
    assert groups, "独立基准：commands.MENU_GROUPS 解析失败"
    out: list[str] = []
    for _title, rows in groups:
        for row in rows:
            cmd = str(row[0]).strip()
            head = cmd.split()[0] if cmd else ""
            if head and head not in out:
                out.append(head)
    return tuple(out)


def _baseline_commands_functions() -> set[str]:
    """独立 AST：commands*.py 里所有 async def cmd_* 的函数名。

    2026-11-25：命令层已经按主题拆成 commands.py / commands_puppet.py 等多个模块
    （commands.py 逼近 200KB 上传红线）。只扫 commands.py 会把**已外迁但仍有
    re-export** 的函数判成「不存在」——`cmd_craft_puppet` 实际可调用
    （`__init__.py` 与 `button_dispatch` 都在用），却被报成点击无反应。
    改为扫 commands*.py 全族，以后继续拆分也不用再改这里。
    """
    out: set[str] = set()
    for path in _CMD_FAMILY:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        out |= {
            n.name for n in tree.body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
            and n.name.startswith("cmd_")
        }
    return out


def _baseline_dispatch_plan() -> dict[str, tuple[str, str, str]]:
    """独立 AST：button_dispatch._PLAN 字典字面量（不 import 模块，避免 nonebot 依赖）。"""
    tree = ast.parse(_DISPATCH_PY.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) \
                and node.target.id == "_PLAN":
            return ast.literal_eval(node.value)
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id == "_PLAN":
                    return ast.literal_eval(node.value)
    return {}


def _card_action_keys_from_source() -> set[str]:
    """独立 AST：doupo/__init__.py 出图点 finish_card(...) 的 card_key 集合。"""
    src = (_DOUPO / "__init__.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    keys: set[str] = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) \
                and n.func.id == "finish_card" and len(n.args) >= 4:
            arg = n.args[3]
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                keys.add(arg.value)
    return keys


# ---------------------------------------------------------------------------
# 模块加载（宿主可能有/无 nonebot：按钮派生两者都能跑，qq_keyboard 需 channel 在 path）
# ---------------------------------------------------------------------------
db = _load(_CH / "doupo_buttons.py", "doupo_buttons")
if str(_CH) not in sys.path:
    sys.path.insert(0, str(_CH))
kb = _safe_load(_CH / "qq_keyboard.py", "qq_keyboard")
disp = _safe_load(_DISPATCH_PY, "doupo_button_dispatch")


# ---------------------------------------------------------------------------
# 守门① 零编造
# ---------------------------------------------------------------------------
def test_baseline_sane():
    """基准自检：canonical 真实口令、含关键动词、不含按钮哨兵。

    2026-11-16 新增「学斗技」口令（入道只领悟 1 门、逐一学习），基准由 29→30；
    同日新增无惩罚切磋（切磋/应战/拒战），基准 30→33。
    2026-11-17 新增爬塔副本（爬塔/塔层/塔结算）与装备，基准 33→37。
    2026-11-17 新增电梯 / 周副本 / 周本状态，基准 37→40。✦2026-11-25 电梯整条换成
    「自动爬塔」（用户「去掉电梯」），条数一换一，仍 40。
    2026-11-17 新增战斗回看 / 战斗评价，基准 40→42。
    2026-11-19 新增「收服」主动收服入口（三层投放补齐），基准 51→52。
    数量断言跟随 MENU_GROUPS 独立 AST，避免功能新增后基准漏同步导致假红。
    """
    real = _baseline_real_commands()
    # 2026-11-24：基准落后实际 1 条（历史新增口令未同步，HEAD 上就红），跟到真实 53。
    # 2026-11-24 晚：菜单补「外观 · 卡片主题」分组（用户「主题口令菜单上没写」）→ 53→54。
    # 2026-11-25：新增「查看 <物品名>」物品详情 → 54→55。
    assert len(real) == 55, "MENU_GROUPS 基准应为 55 条 canonical 口令，实际 %d" % len(real)
    for must in ("斗破菜单", "入道", "人物", "修炼", "突破", "战斗", "强化",
                 "斗技", "学斗技", "斗技详情", "升级", "上阵", "宗门", "转宗",
                 "异火", "收服", "养火", "宝库", "买", "炼药", "炼制", "服丹",
                 "背包", "任务", "接任务", "交任务", "领主线", "排行", "抢",
                 "切磋", "应战", "拒战", "爬塔", "自动爬塔", "塔层", "塔结算", "装备", "送",
                 "周副本", "周本状态", "战斗回看", "战斗评价",
                 "锁定", "解锁", "入塔", "出塔", "火能", "换卡", "主题"):
        assert must in real, "基准缺关键口令：%s" % must
    # 实现派生必须与独立基准逐字一致（漏一条都 FAIL）
    assert tuple(db.real_commands()) == tuple(real), \
        "real_commands 与独立基准不一致：impl=%s base=%s" % (db.real_commands(), real)


def test_no_fabricated_button_cmds():
    """守门①：每张卡的每个按钮 data 必须是真实口令（零编造）。"""
    real = set(_baseline_real_commands())
    for card, acts in db.CARD_ACTIONS.items():
        for c in acts:
            assert c in real, "卡 %s 的按钮 %r 不是真实口令（编造）" % (card, c)


# ---------------------------------------------------------------------------
# 守门②／③ 不含自己 / 不含主菜单
# ---------------------------------------------------------------------------
def test_no_self_and_no_menu_buttons():
    """守门②③：按钮不得等于当前卡自己，也不得是主菜单/切换口。"""
    for card, acts in db.CARD_ACTIONS.items():
        self_cmd = db.CARD_SELF.get(card)
        assert self_cmd, "卡 %s 未登记自身触发口令（无法守门）" % card
        for c in acts:
            assert c != self_cmd, "卡 %s 含当前卡自己按钮：%s" % (card, c)
            assert c not in _MENU_CMDS, "卡 %s 含主菜单/切换口按钮：%s" % (card, c)


# ---------------------------------------------------------------------------
# 守门④ 功能不重叠
# ---------------------------------------------------------------------------
def test_no_cross_card_duplicate_combos():
    """守门④：任意两张卡按钮组合不得完全相同；同卡内不得重复。"""
    combos: dict[tuple[str, ...], str] = {}
    for card, acts in db.CARD_ACTIONS.items():
        assert len(set(acts)) == len(acts), "卡 %s 按钮内部重复：%s" % (card, acts)
        key = tuple(sorted(acts))
        assert key not in combos, \
            "卡 %s 与卡 %s 按钮组合完全相同（功能重叠）：%s" % (card, combos.get(key), acts)
        combos[key] = card


def test_each_card_buttons_are_relevant_family():
    """每张卡的按钮必须落在该卡功能族（独立硬编码期望，防实现悄悄改坏）。"""
    expected = {
        "menu": {"入道", "人物", "修炼", "战斗", "宝库"},
        "panel": {"修炼", "突破", "战斗", "强化", "斗技"},
        "roster": {"斗技详情", "升级", "上阵"},
        "skills": {"上阵", "升级", "异火", "斗技详情"},
        "faction": {"转宗", "斗技", "异火"},
        "flames": {"养火", "上阵", "斗技详情"},
        "shop": {"买", "炼药", "背包"},
        "bag": {"服丹", "炼药", "宝库", "装备"},
        "rank": {"战斗", "修炼", "爬塔", "塔层"},
        "recipes": {"服丹", "养火", "宝库"},
        "help": {"人物", "炼药", "斗技"},
        "battle": {"战斗评价"},
        "puppet": {"炼制傀儡", "傀儡遗迹", "战斗"},
        "ruin": {"炼制傀儡", "傀儡", "战斗"},
        # 2026-11-20 傀儡炼制成卡（成傀后：看名录 / 回遗迹打料 / 打一场试刀）
        "craft": {"傀儡", "傀儡遗迹", "战斗"},
        # 2026-11-25 物品详情卡（看完详情：吃掉 / 去炼 / 再逛）
        "item": {"服丹", "炼药", "宝库", "背包"},
        # 2026-11-25 战斗**纯文本**回包按钮组（用户 Q2：文本+按钮同条）。
        # 与上面的 "battle"（战斗回看卡）刻意分开：那张卡只挂「战斗评价」。
        "fight_text": {"战斗回看", "战斗评价", "战斗", "任务"},
        # 2026-11-25 晚：按回复类型精确配按钮（用户「每类只放对应按钮」）。
        "task_panel": {"领主线", "交任务", "接任务", "战斗"},
        "main_submit": {"任务", "战斗", "修炼"},
        "task_submit": {"任务", "领主线", "战斗"},
        "battle_text": {"战斗回看", "战斗", "任务"},
        "review_text": {"战斗评价", "战斗", "任务"},
        "tower_text": {"塔层", "塔结算", "自动爬塔", "战斗"},
        "duel_text": {"战斗", "人物"},
        "weekly_text": {"周本状态", "战斗", "人物"},
        "cult_text": {"战斗", "突破", "人物"},
        "equip_text": {"强化", "人物", "战斗"},
        "skill_text": {"斗技", "上阵", "人物"},
        "flame_text": {"养火", "异火", "人物"},
        "buy_text": {"买", "炼药", "宝库"},
        "craft_text": {"炼药", "服丹", "背包"},
        "use_text": {"背包", "炼药", "查看"},
        "shop_text": {"买", "宝库", "背包"},
        "enter_text": {"人物", "修炼", "战斗"},
        "puppet_text": {"傀儡", "炼制傀儡", "傀儡遗迹"},
        # 2026-11-25 晚：按钮设置回执（隐藏口令）。配完就回去玩，给三个常用入口，
        # 绝不给「斗破菜单」那套默认主菜单按钮。
        "setup_text": {"人物", "背包", "战斗"},
    }
    assert set(db.CARD_ACTIONS) == set(expected), \
        "卡片集合不一致：impl-only=%s base-only=%s" % (
            sorted(set(db.CARD_ACTIONS) - set(expected)),
            sorted(set(expected) - set(db.CARD_ACTIONS)))
    for card, want in expected.items():
        got = set(db.CARD_ACTIONS[card])
        assert got == want, "卡 %s 按钮族错配：impl=%s expected=%s" % (card, sorted(got), sorted(want))


# ---------------------------------------------------------------------------
# 守门⑤ 可分发性（每个按钮都在 dispatch plan 里且指向真实函数）
# ---------------------------------------------------------------------------
def test_every_button_is_dispatchable():
    """守门⑤：按钮点下去必须有处理函数，否则用户看到「点了没反应」。"""
    plan = _baseline_dispatch_plan()
    real_fns = _baseline_commands_functions()
    assert plan, "button_dispatch._PLAN 解析失败"
    for card, acts in db.CARD_ACTIONS.items():
        for c in acts:
            assert c in plan, "卡 %s 的按钮 %r 没有 dispatch 条目（点击会无反应）" % (card, c)
            fn_name, shape, card_key = plan[c]
            assert fn_name in real_fns, \
                "按钮 %r 指向不存在的 commands 函数：%s" % (c, fn_name)
            # "ruin"：带结构化战况的副本口令（cmd(store, qq, None, report=…)），
            # 2026-11-18 起傀儡遗迹用它把战况并进卡，而不是另发一条文字。
            assert shape in ("qq", "qq_str", "gid", "enter", "ruin"), \
                "按钮 %r 参数形态非法：%s" % (c, shape)
            # card_key 非空时必须真的是本模块认知的卡片。
            # 例外：特判提示卡（如 auto_tower_prompt 在 buttons_for_card 里直接返回
            # 两个 custom 带参按钮）不在 CARD_ACTIONS 表里，但必须有按钮，否则
            # 等于发了一条没有按钮的提示。回执族（*_text）在 CARD_ACTIONS 里，走 else 分支。
            if card_key:
                if card_key in db.PROMPT_CARD_KEYS:
                    assert db.buttons_for_card(card_key), \
                        "特判卡 %r 没有任何按钮（提示卡会变成死胡同）" % card_key
                else:
                    assert card_key in db.CARD_ACTIONS, \
                        "按钮 %r 的出卡 key %r 不在 CARD_ACTIONS" % (c, card_key)
    # plan 不得混入非按钮口令（防止反向污染）。
    # 例外：custom 带参按钮的 head（如「炼制 破厄丹」拆出「炼制」）不在静态
    # CARD_ACTIONS 里——它是查看丹药卡的动态按钮，走 dispatch_custom 带参分发。
    # 白名单只收这类「带参动作的 head」，且必须真在 _item_detail_buttons 用到。
    _CUSTOM_ARG_HEADS = {"炼制", "自动爬塔"}
    extra = set(plan) - set(db.ALL_BUTTON_CMDS) - _CUSTOM_ARG_HEADS
    assert not extra, "dispatch plan 混入非按钮口令：%s" % sorted(extra)
    for head in _CUSTOM_ARG_HEADS:
        assert head in plan, "custom 带参按钮 head %r 缺 dispatch 条目（点击无反应）" % head


def test_card_renderer_shapes_match_dispatch():
    """守门⑥：`render_<card_key>` 首参不叫 p 的，dispatch 必须特判。

    2026-11-18 事故：``button_dispatch`` 统一用 ``renderer(p)`` 出卡，而
    ``render_puppet(roster, elig, p)`` / ``render_ruin(state, day)`` 首参不是玩家
    dict → 把玩家 dict 当名录/当日状态，渲出「持有 0 具 / 今日进度 0/3」的**假卡**，
    既不报错也不降级，比崩更难查。用 AST 读签名，不 import（避开 nonebot 依赖）。
    """
    card_tree = ast.parse((_DOUPO / "card.py").read_text(encoding="utf-8"))
    shapes: dict[str, list[str]] = {}
    for n in card_tree.body:
        if isinstance(n, (ast.AsyncFunctionDef, ast.FunctionDef)) \
                and n.name.startswith("render_"):
            shapes[n.name[len("render_"):]] = [a.arg for a in n.args.args]
    dispatch_src = _DISPATCH_PY.read_text(encoding="utf-8")
    bad = []
    for key in sorted({v[2] for v in _baseline_dispatch_plan().values() if v[2]}):
        # 不出图的 card_key 分两族（事实源 NO_RENDER_CARD_KEYS）：
        #   * 提示卡 auto_tower_prompt：只挂 custom 带参按钮，本身发一条确认提示；
        #   * 回执族 fight_text / craft_text …：回复是文本，卡面另有 render_* 负责。
        # 两族都不参与 render_* 形状校验，但都必须真能拿到按钮（由别的用例守）。
        if key in _special_card_keys():
            continue
        args = shapes.get(key)
        if args is None:
            bad.append("%s: 没有 render_%s" % (key, key))
            continue
        if args and args[0] != "p":
            if 'card_key == "%s"' % key not in dispatch_src \
                    or "render_%s_for(" % key not in dispatch_src:
                bad.append("%s(首参=%s) 未在 dispatch 特判" % (key, args[0]))
    assert not bad, "出图参数形状错配（会渲出假卡）：%s" % bad


def test_every_finish_card_key_has_actions():
    """每个出图点传入的 card_key 都必须有按钮组（防新增卡忘配按钮）。"""
    keys = _card_action_keys_from_source()
    assert keys, "doupo/__init__.py 未找到任何 finish_card(card_key) 调用"
    missing = keys - set(db.CARD_ACTIONS)
    assert not missing, "出图点 card_key 未配按钮：%s" % sorted(missing)


def test_platform_structure_limits():
    """平台硬限：每卡 1~5 个按钮、label ≤10、primary 恒 False。"""
    for card, acts in db.CARD_ACTIONS.items():
        assert 1 <= len(acts) <= 5, "卡 %s 按钮数越界：%d" % (card, len(acts))
        for c in acts:
            assert 0 < len(c) <= 10, "卡 %s 按钮 label 越界（>10）：%s" % (card, c)
    for card in db.CARD_ACTIONS:
        btns = db.buttons_for_card(card)
        assert btns, "卡 %s 零按钮（出图却不挂按钮）" % card
        assert all(not b.primary for b in btns), "卡 %s 出现 primary 按钮（用户要求全灰）" % card
        assert [b.cmd for b in btns] == list(db.CARD_ACTIONS[card]), \
            "卡 %s 按钮顺序/内容与声明不一致" % card
    assert db.buttons_for_card("") == () and db.buttons_for_card("bogus") == (), \
        "未知卡应返回零按钮"


# ---------------------------------------------------------------------------
# 守门⑥ 发送隔离（qq_keyboard fallback 不污染 game_tools）
# ---------------------------------------------------------------------------
def test_keyboard_doupo_fallback_and_gametools_isolation():
    """斗破哨兵走 doupo 派生；game_tools 既有口令行为逐字节不变。"""
    if kb is None:
        print("[skip] qq_keyboard 不可加载（宿主缺依赖），容器内会跑")
        return
    for card in db.CARD_ACTIONS:
        got = [b.cmd for b in kb.buttons_for(kb.DOUPO_CARD_PREFIX + card)]
        assert got == list(db.CARD_ACTIONS[card]), \
            "哨兵 %s 派生错：got=%s expected=%s" % (card, got, db.CARD_ACTIONS[card])
        assert all(not b.primary for b in kb.buttons_for(kb.DOUPO_CARD_PREFIX + card)), \
            "哨兵 %s 出现 primary" % card
    # 未知卡 → 零按钮（绝不构造空 keyboard）
    assert kb.buttons_for(kb.DOUPO_CARD_PREFIX + "bogus") == ()
    assert kb.make_keyboard(kb.DOUPO_CARD_PREFIX + "bogus") == (None, ())
    # game_tools 回归：既有档位族/零按钮行为不受影响
    assert [b.cmd for b in kb.buttons_for("裂隙")] == ["普通裂隙", "钢铁裂隙"], \
        "game_tools 裂隙卡被斗破 fallback 污染"
    assert kb.buttons_for("帮助") == (), "game_tools 帮助卡不该有按钮"
    assert kb.buttons_for("") == () and kb.buttons_for("闲聊") == (), \
        "非口令输入被误挂按钮"


def test_keyboard_prefix_does_not_collide_with_real_commands():
    """哨兵前缀必须不可能与真实口令/库存口令冲突。"""
    prefix = getattr(kb, "DOUPO_CARD_PREFIX", "doupo:")
    assert ":" in prefix, "哨兵前缀应含冒号以彻底避开中文口令空间"
    for c in _baseline_real_commands():
        assert not c.startswith(prefix), "真实口令 %r 与哨兵前缀冲突" % c
        assert prefix + c != c


# ---------------------------------------------------------------------------
# 2026-11-16 炼药卡翻页按钮守门（prove-it，独立口径）
# ---------------------------------------------------------------------------
def test_page_prefix_isolated_from_canonical_buttons():
    """翻页前缀必须独立，不进 CARD_ACTIONS / ALL_BUTTON_CMDS / _PLAN。"""
    page_prefix = getattr(db, "DOUPO_PAGE_PREFIX", "doupo:page:")
    assert page_prefix.startswith("doupo:"), "翻页前缀应在斗破命名空间"
    assert page_prefix != getattr(db, "DOUPO_CARD_PREFIX", "doupo:"), \
        "翻页不能与卡片通用前缀相同（否则被 when card_key 分支吞掉）"
    # 翻页哨兵绝不能混进 canonical 按钮全集（一旦混入，静态按钮点击会走翻页分发）
    for card, acts in db.CARD_ACTIONS.items():
        for c in acts:
            assert not c.startswith(page_prefix), \
                "卡 %s 的按钮 %r 误用了翻页前缀" % (card, c)
    assert not any(c.startswith(page_prefix) for c in db.ALL_BUTTON_CMDS)
    assert not any(k.startswith(page_prefix) for k in _baseline_dispatch_plan())


def test_page_buttons_derived_from_armed_state():
    """arm_page 后 page_buttons_for 应派生正确的「上一页/下一页」data。"""
    assert db.page_buttons_for("doupo:page:2") == (), \
        "未 arm 时不得凭空挂翻页按钮"
    t = db.arm_page(2, 4)
    try:
        btns = db.page_buttons_for("doupo:page:2")
        got = [(b.label, b.cmd) for b in btns]
        assert got == [("上一页", "doupo:page:1"), ("下一页", "doupo:page:3")], \
            "第2/4页翻页按钮错：%s" % got
        assert all(not b.primary for b in btns)
        # 首页只有下一页；末页只有上一页
        db.disarm_page(t)
        t = db.arm_page(1, 4)
        assert [b.cmd for b in db.page_buttons_for("doupo:page:1")] == ["doupo:page:2"]
        db.disarm_page(t)
        t = db.arm_page(4, 4)
        assert [b.cmd for b in db.page_buttons_for("doupo:page:4")] == ["doupo:page:3"]
    finally:
        db.disarm_page(t)
    # 单页不挂翻页按钮
    t = db.arm_page(1, 1)
    try:
        assert db.page_buttons_for("doupo:page:1") == ()
    finally:
        db.disarm_page(t)


def test_page_dispatch_source_contract():
    """qq_interaction 必须先识别翻页哨兵，再走 canonical 按钮 / game_tools。"""
    src = (_CH / "qq_interaction.py").read_text(encoding="utf-8")
    assert "parse_page_cmd" in src and "dispatch_page" in src, \
        "qq_interaction 未接入翻页分发"
    i_page = src.find("parse_page_cmd(cmd)")
    i_button = src.find("if cmd in _DOUPO_CMDS")
    i_gt = src.find("await dispatch_normalized(bot, full_ctx, pseudo, cmd)")
    assert i_page != -1 and i_button != -1 and i_gt != -1 \
        and i_page < i_button < i_gt, "翻页判定必须早于 canonical 按钮与 game_tools"
    # button_dispatch 必须能解析并消费该前缀
    if disp is not None:
        assert disp.parse_page_cmd("doupo:page:3") == 3
        assert disp.parse_page_cmd("doupo:page:0") is None
        assert disp.parse_page_cmd("doupo:page:abc") is None
        assert disp.parse_page_cmd("炼药") is None


# ---------------------------------------------------------------------------
# 2026-11-25 晚：按入站口令精确配按钮（用户「每个回复的按钮都处理好」）
# ---------------------------------------------------------------------------
def test_reply_card_key_for_inbound_mapping():
    """入站口令首词 → 纯文字回执 card_key：只认白名单，带参取首词，闲聊零按钮。"""
    cases = {
        "任务": "task_panel", "领主线": "main_submit", "交主线": "main_submit",
        "交任务": "task_submit", "接任务": "task_submit",
        "战斗评价": "battle_text", "战斗回看": "review_text",
        "爬塔": "tower_text", "塔层": "tower_text", "塔结算": "tower_text",
        "周副本": "weekly_text", "周本状态": "weekly_text",
        "修炼": "cult_text", "突破": "cult_text",
        "上阵": "skill_text", "学斗技": "skill_text",
        "服丹": "use_text", "锁定": "use_text", "解锁": "use_text",
        "买": "buy_text", "炼制": "craft_text", "入道": "enter_text", "主题": "enter_text",
        "傀儡": "puppet_text", "炼制傀儡": "puppet_text", "傀儡遗迹": "puppet_text",
        # 2026-11-25 第五轮：全量命令都必须有专属按钮族（不许回落主菜单）
        "战斗": "fight_text", "斗破菜单": "menu", "人物": "panel", "帮助": "help",
        "斗技": "skills", "宗门斗技": "faction", "宗门": "faction", "转宗": "skill_text",
        "异火": "flames", "宝库": "shop", "炼药": "recipes", "查看": "use_text",
        "背包": "bag", "排行": "rank", "抢": "rank", "送": "bag", "换卡": "cult_text",
        "切磋": "duel_text", "应战": "duel_text", "拒战": "duel_text",
    }
    for inbound, want in cases.items():
        assert db.reply_card_key_for(inbound) == want, \
            "入站 %r 应派生 card_key %r，实际 %r" % (
                inbound, want, db.reply_card_key_for(inbound))
    # 带参口令取首词
    assert db.reply_card_key_for("炼制 破厄丹") == "craft_text"
    assert db.reply_card_key_for("上阵 1 3") == "skill_text"
    assert db.reply_card_key_for("  任务  ") == "task_panel"
    # 闲聊 / 空 → 零按钮（绝不误挂）
    for s in ("", "   ", "闲聊", "你好", "今天天气不错", "/start"):
        assert db.reply_card_key_for(s) == "", \
            "非功能口令 %r 不该派生 card_key" % s
    # 白名单值必须都指向已知卡，键必须规范（无空白）
    for head, ck in db.REPLY_CARD_KEY.items():
        assert ck in db.CARD_ACTIONS or ck in db.PROMPT_CARD_KEYS, \
            "入站映射 %r 指向未知卡 %r" % (head, ck)
        assert head == head.strip() and head, "入站映射键不规范：%r" % head


def test_label_overlay_continue_battle():
    """「继续战斗」只是 label 覆盖，cmd 必须是真口令「战斗」且可分发。"""
    real = set(_baseline_real_commands())
    plan = _baseline_dispatch_plan()
    for card, pairs in db.CARD_ACTIONS_PAIRS.items():
        assert card in db.CARD_ACTIONS, "label 覆盖指向未知卡：%s" % card
        for label, cmd in pairs:
            assert cmd in real, "label 覆盖 %s 的 cmd %r 不是真口令" % (card, cmd)
            assert cmd in plan, "label 覆盖 %s 的 cmd %r 不可分发" % (card, cmd)
            assert 0 < len(label) <= 10, "label 越界：%r" % label
    # 战报 / 任务面板必须明确有「继续战斗」按钮，data=战斗
    for card in ("fight_text", "task_panel", "main_submit", "tower_text",
                 "battle_text", "review_text"):
        got = [(b.label, b.cmd) for b in db.buttons_for_card(card)]
        assert ("继续战斗", "战斗") in got, \
            "卡 %s 缺「继续战斗」按钮：%s" % (card, got)
    # 未覆盖的卡仍按 CARD_ACTIONS 原样（label == cmd）
    plain = [(b.label, b.cmd) for b in db.buttons_for_card("panel")]
    assert plain == [(c, c) for c in db.CARD_ACTIONS["panel"]]
    # 派生走覆盖表：cmd=战斗 时 label 必须是「继续战斗」，不能回退成「战斗」
    ft = {b.cmd: b.label for b in db.buttons_for_card("fight_text")}
    assert ft.get("战斗") == "继续战斗"


def test_item_detail_dynamic_buttons():
    """查看丹药卡：炼制此丹 + 买第一味材料；非丹药/未知回落通用按钮。"""
    dan = db.buttons_for_card("item:破厄丹")
    cmds = [b.cmd for b in dan]
    assert "doupo:cbtn:炼制 破厄丹" in cmds, "丹药卡缺炼制按钮：%s" % cmds
    assert any(c.startswith("doupo:cbtn:买 ") for c in cmds), \
        "丹药卡缺买材料按钮：%s" % cmds
    assert "炼药" in cmds and "背包" in cmds
    assert len(dan) <= 5 and all(not b.primary for b in dan)
    assert all(0 < len(b.label) <= 10 for b in dan)
    # 两味不同丹药的炼制按钮带参不同（不能全挂同一个）
    assert "doupo:cbtn:炼制 回灵丹" in [
        b.cmd for b in db.buttons_for_card("item:回灵丹")]
    # 非丹药 / 空名 → 通用 item 按钮，绝不带 custom 带参按钮
    for k in ("item:不存在的东西", "item:"):
        got = [b.cmd for b in db.buttons_for_card(k)]
        assert got == list(db.CARD_ACTIONS["item"]), \
            "%s 应回落通用按钮：%s" % (k, got)
        assert not any(c.startswith(db.DOUPO_CUSTOM_PREFIX) for c in got)


def test_auto_tower_prompt_buttons():
    """自动爬塔提示卡：两个 custom 带参按钮（随机加成=是 / 无加成=否），点击即开打。"""
    btns = db.buttons_for_card("auto_tower_prompt")
    assert len(btns) == 2, "自动爬塔提示必须有 2 个按钮：%s" % [b.label for b in btns]
    cmds = [b.cmd for b in btns]
    assert cmds == [db.DOUPO_CUSTOM_PREFIX + "自动爬塔 是",
                    db.DOUPO_CUSTOM_PREFIX + "自动爬塔 否"], cmds
    labels = [b.label for b in btns]
    assert "随机加成" in labels[0] and "无加成" in labels[1], labels
    assert all(0 < len(x) <= 10 for x in labels), "按钮文字过长会被截断：%s" % labels
    # 裸 card_key 不能误挂（没这一步就是通用卡按钮，点了不会开打）
    assert db.buttons_for_card("auto_tower_prompt") != db.buttons_for_card("tower_text")


def test_every_command_never_falls_back_to_menu():
    """守门：**每一条**命令的回执都必须挂自己那一族的按钮，绝不许回落主菜单。

    2026-11-25 用户实测：点「买 紫叶兰」/「炼制 凝火丹」后，回执底下是
    入道/人物/修炼/战斗/宝库（主菜单）——因为这两条口令当时没登记入站映射，
    兜底桥只能给 kb_fallback=menu。这类「默认按钮」必须绝迹。
    """
    real = list(db.real_commands())
    assert len(real) == 55
    bad = []
    for c in real:
        ck = db.reply_card_key_for(c)
        if not ck:
            bad.append("%s：无映射（会回落主菜单）" % c)
            continue
        assert ck in db.CARD_ACTIONS or ck in db.PROMPT_CARD_KEYS, ck
        # 只有「菜单/帮助」本身挂菜单按钮是合理的
        if ck == "menu" and c not in ("斗破菜单",):
            bad.append("%s：回落主菜单" % c)
    assert not bad, "命令回执仍会挂默认主菜单按钮：%s" % bad


def test_every_dispatch_entry_carries_keyboard():
    """守门：_PLAN 里每一条都必须带 card_key，否则按钮点出来的回复没有按钮。

    2026-11-25 用户实测：「继续战斗」点完，战斗结果一条按钮都没有——
    因为 ``_PLAN["战斗"]`` 的 card_key 是空串，文本兜底没键盘可挂。
    """
    plan = _baseline_dispatch_plan()
    assert plan, "button_dispatch._PLAN 解析失败"
    bad = ["%s(%s)" % (k, v[2]) for k, v in plan.items() if not v[2]]
    assert not bad, "这些按钮点完会发出「无按钮」的回复：%s" % bad
    # 战斗必须还原成战斗按钮族（而不是留着空串或挂到别的卡）
    assert plan["战斗"][2] == "fight_text", plan["战斗"]


def test_bridge_derives_card_key_from_inbound():
    """源码契约：兜底桥从入站口令派生 card_key（加 doupo: 前缀），未知口令不误挂。"""
    src = (_ROOT / "src/game_compat.py").read_text(encoding="utf-8")
    assert "reply_card_key_for" in src, "兜底桥未按入站口令派生 card_key"
    assert '_ck = "doupo:" + _ck' in src, "派生 card_key 未加 doupo: 前缀"
    # 派生调用必须早于兜底发送（否则按钮没带上）
    i_derive = src.find("_ck = reply_card_key_for(_plain)")
    i_send = src.find("if await _channel_send_text(event, message, _ck)", i_derive)
    assert i_derive != -1 and i_send != -1 and i_derive < i_send, \
        "card_key 派生必须早于兜底发送"
    # 查看卡出口必须传 item:<丹名> 动态 key
    init_src = (_DOUPO / "__init__.py").read_text(encoding="utf-8")
    assert '"item:%s" % name' in init_src, "查看卡未传 item:<丹名> 动态 card_key"
    # custom 带参按钮 head 必须在 dispatch plan（否则点击无反应）
    plan = _baseline_dispatch_plan()
    for head in ("炼制", "买"):
        assert head in plan, "custom 带参按钮 head %r 缺 dispatch" % head


# ---------------------------------------------------------------------------
# 注入验证（prove-it：四类真实错误必须被拦住）
# ---------------------------------------------------------------------------
def test_injection_guards():
    """把自己/主菜单/编造口令/漏配 dispatch 四类错误放回，守门必须报错。"""
    real = set(_baseline_real_commands())
    plan = _baseline_dispatch_plan()

    def _check(actions: dict[str, tuple[str, ...]]) -> None:
        """与 test_no_self_* / test_no_fabricated_* 同口径的复核函数。"""
        combos = {}
        for card, acts in actions.items():
            self_cmd = db.CARD_SELF.get(card)
            for c in acts:
                assert c in real, "编造：%s" % c
                assert c != self_cmd, "自己：%s" % c
                assert c not in _MENU_CMDS, "主菜单：%s" % c
            key = tuple(sorted(acts))
            assert key not in combos, "组合重复：%s" % (acts,)
            combos[key] = card

    # 基线（不含违规）应通过
    _check({k: tuple(v) for k, v in db.CARD_ACTIONS.items()})

    # 注入 1：把当前卡自己塞回 → 守门② 应拒绝
    bad1 = {k: tuple(v) for k, v in db.CARD_ACTIONS.items()}
    bad1["panel"] = bad1["panel"] + ("人物",)
    try:
        _check(bad1)
        raise AssertionError("守门②未拦住「当前卡自己」按钮")
    except AssertionError as e:
        assert "守门②未拦住" not in str(e), "守门②确实没拦住自己按钮"

    # 注入 2：把主菜单塞回 → 守门③ 应拒绝
    bad2 = {k: tuple(v) for k, v in db.CARD_ACTIONS.items()}
    bad2["shop"] = bad2["shop"] + ("帮助",)
    try:
        _check(bad2)
        raise AssertionError("守门③未拦住主菜单按钮")
    except AssertionError as e:
        assert "守门③未拦住" not in str(e), "守门③确实没拦住主菜单"

    # 注入 3：编造口令 → 守门① 应拒绝
    bad3 = {k: tuple(v) for k, v in db.CARD_ACTIONS.items()}
    bad3["flames"] = bad3["flames"] + ("御火",)
    try:
        _check(bad3)
        raise AssertionError("守门①未拦住编造口令")
    except AssertionError as e:
        assert "守门①未拦住" not in str(e), "守门①确实没拦住编造口令"

    # 注入 4：漏配 dispatch → 守门⑤ 应拒绝
    fake_plan = dict(plan)
    fake_plan.pop("养火", None)
    try:
        assert "养火" in fake_plan, "守门⑤应拒绝缺 dispatch 条目的按钮"
        raise AssertionError("守门⑤未拦住漏配 dispatch")
    except AssertionError as e:
        assert "守门⑤未拦住" not in str(e), "守门⑤确实没拦住漏配"

    # 恢复断言：正式实现仍全绿
    db.validate()
    assert db.buttons_for_card("panel") and db.buttons_for_card("flames")


# ---------------------------------------------------------------------------
# 源码契约：斗破出图必须走 finish_card（不能退回裸 matcher.finish(image_seg)）
# ---------------------------------------------------------------------------
def test_source_contract_finish_card_used():
    """9 张卡片全部走 finish_card；入道动图保留 image_seg（GIF 不内嵌 markdown）。"""
    src = (_DOUPO / "__init__.py").read_text(encoding="utf-8")
    assert src.count("finish_card(") >= 11, \
        "finish_card 调用点不足 11 处（有卡片没挂按钮）：%d" % src.count("finish_card(")
    for card in ("menu", "panel", "roster", "skills", "faction",
                 "flames", "shop", "bag", "rank", "recipes", "help"):
        assert '"%s"' % card in src, "源码里找不到 card_key %r" % card
    # 入道动图仍是 image_seg（官方 file_image），不能被误改成 finish_card
    assert 'image_seg(event, gif' in src, "入道动图出图方式被误改"


def test_source_contract_button_text_fallback_keeps_keyboard():
    """按钮回执的纯文本兜底也必须挂键盘（否则点进去是没有按钮的死提示）。

    2026-11-25 实测缺口：dispatch_button 文本分支只发 ``Outgoing(text=msg)``，
    提示型 card_key（auto_tower_prompt 没有 render_*）会退化成「有字没按钮」。
    """
    src = (_DISPATCH_PY).read_text(encoding="utf-8")
    assert "keyboard_cmd=_kcmd" in src, "文本兜底没把键盘一起发（提示卡会变死胡同）"
    assert '_kcmd = ("doupo:" + card_key) if card_key else ""' in src, \
        "keyboard 口令未按 doupo:<card_key> 组装"


def test_every_finish_text_carries_card_key():
    """斗破每一条纯文本回执都必须自带 card_key —— 否则底部会冒出默认主菜单按钮。

    2026-11-25 用户实测：「买材料炼药的回复内容自动携带了默认按钮」。
    根因不是某一张卡配错，而是**机制**：官方群纯文本必须与卡片同形
    （markdown + keyboard 同段）才送得到，而 `finish_text` 没拿到 card_key 时
    会退回 `_KB_FALLBACKS["doupo"] = "doupo:menu"` → 回执底下永远挂着
    「入道 / 人物 / 修炼 / 战斗 / 宝库」那一排主菜单。

    所以这里逐条 AST 扫 `doupo/*.py` 的 `finish_text(...)` 调用（含位置参数：
    签名是 ``(matcher, event, text, file_name, card_key)``），一旦发现没传
    card_key 就红；同时校验它指向的 key 真的存在（CARD_ACTIONS 或不出图族）。
    """
    bad = []
    for path in sorted(_DOUPO.glob("*.py")):
        src = path.read_text(encoding="utf-8")
        if "finish_text(" not in src:
            continue
        for node in ast.walk(ast.parse(src)):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id == "finish_text"):
                continue
            ck = None
            for kw in node.keywords:
                if kw.arg == "card_key":
                    ck = kw.value
            if ck is None and len(node.args) >= 5:
                ck = node.args[4]
            if ck is None or (isinstance(ck, ast.Constant) and not ck.value):
                bad.append("%s:%d 无 card_key" % (path.name, node.lineno))
                continue
            if isinstance(ck, ast.Constant):
                key = ck.value
                if key not in db.CARD_ACTIONS and key not in _special_card_keys():
                    bad.append("%s:%d card_key=%r 未注册" % (path.name, node.lineno, key))
    assert not bad, "纯文本回执会挂成默认主菜单按钮：%s" % bad


def test_every_regex_keyword_maps_to_buttons():
    """`doupo/__init__.py` 里**每一个**能触发的口令词都必须派生到按钮组。

    比「55 条菜单口令」更狠：菜单只列主词，而玩家实际会打「打怪 / 历练 / 包裹 /
    行囊 / 商店 / 坊市 / 周塔」这些别名。别名不登记 → `reply_card_key_for` 返回空
    → 官方群纯文本兜底成 `doupo:menu` → 回执底部又是一排默认主菜单按钮。
    这里直接从正则里抠中文词，逐个问实现要 card_key，一个落空就红。
    """
    src = (_DOUPO / "__init__.py").read_text(encoding="utf-8")
    words = set()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
                and node.func.id == "_pat":
            for arg in node.args:
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    words.update(re.findall(r"[\u4e00-\u9fa5]{2,}", arg.value))
    assert len(words) > 80, "口令词抽取退化（只抽到 %d 个）" % len(words)
    miss = sorted(w for w in words if not db.reply_card_key_for(w))
    assert not miss, "这些口令没有按钮组（回执会挂默认主菜单）：%s" % miss


def test_reply_alias_points_to_real_key():
    """别名表只许指向已有主口令，且不许和主表重名（重名会被主表静默盖掉）。"""
    alias = getattr(db, "REPLY_ALIAS", {})
    assert alias, "别名表缺失（同一张卡的各种说法会一半有按钮一半没有）"
    bad = [(a, c) for a, c in alias.items()
           if c not in db.REPLY_CARD_KEY or a in db.REPLY_CARD_KEY]
    assert not bad, "别名表有问题（指向不存在的主口令 / 与主表重名）：%s" % bad
    # 抽查：三个高频别名必须真落到对应的卡
    for word, want in (("打怪", "fight_text"), ("包裹", "bag"), ("商店", "shop")):
        got = db.reply_card_key_for(word)
        assert got == want, "%s → %r，应为 %r" % (word, got, want)


def test_source_contract_interaction_routes_doupo():
    """qq_interaction 必须先识别斗破口令再走 game_tools（否则点击无反应）。"""
    src = (_CH / "qq_interaction.py").read_text(encoding="utf-8")
    assert "ALL_BUTTON_CMDS" in src, "qq_interaction 未引用斗破按钮全集"
    assert "dispatch_button" in src, "qq_interaction 未调用斗破分发"
    # 斗破分支必须在 dispatch_normalized 之前（否则被 game_tools 先吞）
    i_doupo = src.find("dispatch_button")
    i_gt = src.find("await dispatch_normalized(bot, full_ctx, pseudo, cmd)", i_doupo)
    assert i_doupo != -1 and i_gt != -1 and i_doupo < i_gt, \
        "斗破分发必须早于 game_tools dispatch_normalized"


# ---------------------------------------------------------------------------
# 2026-11-26 用户「官方的机器人是不是支持各种类型按钮，都做出来测试看看」
# ---------------------------------------------------------------------------
def test_sample_buttons_cover_all_three_action_types():
    """样例键盘必须覆盖平台**全部三种** action.type，一颗不缺。

    平台只有 回传(1) / 指令(2) / 跳转(0)，此前本项目只用过回传。样例少做一种，
    就等于「没测过那种按钮」，用户点开也看不出区别。
    """
    kinds = [getattr(b, "kind", "callback") for b in db.SAMPLE_BUTTONS]
    assert set(kinds) == {"callback", "command", "link"}, kinds
    assert len(db.SAMPLE_BUTTONS) <= 5, "按钮数越界（平台每卡上限 5 行）"
    for b in db.SAMPLE_BUTTONS:
        assert 0 < len(b.label) <= 10, "label 越界：%r" % b.label
        assert b.cmd.strip(), "样例按钮 data 不得为空：%r" % b.label
    # 跳转按钮的 data 必须是 http(s)，否则点了没反应
    links = [b for b in db.SAMPLE_BUTTONS if b.kind == "link"]
    assert links and all(b.cmd.startswith("https://") for b in links), links


def test_keyboard_maps_kind_to_action_type():
    """``make_keyboard`` 必须真的按 kind 生成 action.type=0/1/2，不许全写死 1。

    prove-it：把 ``_to_button`` 改回「恒 type=1」，这里立刻为假。
    适配器模型在宿主没装，故本守门走源码契约（真 payload 由手机端实测复核）。
    """
    src = (_CH / "qq_keyboard.py").read_text(encoding="utf-8")
    body = src.split("def _to_button(b):", 1)[1].split("rows = [", 1)[0]
    assert 'kind == "link"' in body and "type=0" in body, "缺跳转按钮分支"
    assert 'kind == "command"' in body and "type=2" in body, "缺指令按钮分支"
    assert "type=1" in body, "缺回调按钮分支（既有按钮全靠它）"
    assert "unsupport_tips" in body, "跳转按钮缺 unsupport_tips（低版本端点了没反应）"
    # 默认仍必须是 callback：既有按钮一个字节都不许变
    assert "callback" in body, "默认 kind 丢了"


def test_btn_kind_defaults_to_callback():
    """缺省 kind 必须是 callback —— 全部既有按钮都是回传，改默认值=全线改行为。"""
    assert db.Btn("x", "y").kind == "callback"
    # qq_keyboard 依赖 nonebot 适配器（宿主未装），故第二份 Btn 走源码契约。
    src = (_CH / "qq_keyboard.py").read_text(encoding="utf-8")
    body = src.split("class Btn:", 1)[1].split("def ", 1)[0]
    assert "kind: str = \"callback\"" in body, body


if __name__ == "__main__":
    import traceback

    # 2026-11-26：原来是**手写清单**，新增用例忘了登记就静默不跑——实测已漏
    # ``test_card_renderer_shapes_match_dispatch``（31 个只跑 27 个），与
    # 「check() 只 print 不抛」是同一类自欺。改为自动收集，新增用例不可能漏跑。
    tests = [(n, v) for n, v in sorted(globals().items())
             if n.startswith("test_") and callable(v)]
    passed = failed = 0
    for _name, t in tests:
        try:
            t()
            print("PASS  %s" % t.__name__)
            passed += 1
        except Exception as e:  # noqa: BLE001
            print("FAIL  %s -> %s: %s" % (t.__name__, type(e).__name__, e))
            traceback.print_exc()
            failed += 1
    print("\n==== 结果: %d PASS / %d FAIL ====" % (passed, failed))
    sys.exit(1 if failed else 0)
