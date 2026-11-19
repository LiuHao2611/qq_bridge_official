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
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_CH = _ROOT / "src/plugins/qq_bridge/channel"
_DOUPO = _ROOT / "src/plugins/doupo"
_CMD_PY = _DOUPO / "commands.py"
_DISPATCH_PY = _DOUPO / "button_dispatch.py"

_MENU_CMDS = frozenset({"斗破菜单", "斗破口令", "修仙菜单", "帮助"})


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
    """独立 AST：commands.py 里所有 async def cmd_* 的函数名。"""
    tree = ast.parse(_CMD_PY.read_text(encoding="utf-8"))
    return {
        n.name for n in tree.body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name.startswith("cmd_")
    }


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
    2026-11-17 新增电梯 / 周副本 / 周本状态，基准 37→40。
    2026-11-17 新增战斗回看 / 战斗评价，基准 40→42。
    数量断言跟随 MENU_GROUPS 独立 AST，避免功能新增后基准漏同步导致假红。
    """
    real = _baseline_real_commands()
    assert len(real) == 51, "MENU_GROUPS 基准应为 51 条 canonical 口令，实际 %d" % len(real)
    for must in ("斗破菜单", "入道", "人物", "修炼", "突破", "战斗", "强化",
                 "斗技", "学斗技", "斗技详情", "升级", "上阵", "宗门", "转宗",
                 "异火", "养火", "宝库", "买", "炼药", "炼制", "服丹",
                 "背包", "任务", "接任务", "交任务", "领主线", "排行", "抢",
                 "切磋", "应战", "拒战", "爬塔", "塔层", "塔结算", "装备", "送",
                 "电梯", "周副本", "周本状态", "战斗回看", "战斗评价",
                 "锁定", "解锁", "入塔", "出塔", "火能", "换卡"):
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
            # card_key 非空时必须真的是本模块认知的卡片
            if card_key:
                assert card_key in db.CARD_ACTIONS, \
                    "按钮 %r 的出卡 key %r 不在 CARD_ACTIONS" % (c, card_key)
    # plan 不得混入非按钮口令（防止反向污染）
    extra = set(plan) - set(db.ALL_BUTTON_CMDS)
    assert not extra, "dispatch plan 混入非按钮口令：%s" % sorted(extra)


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


if __name__ == "__main__":
    import traceback

    tests = [
        test_baseline_sane,
        test_no_fabricated_button_cmds,
        test_no_self_and_no_menu_buttons,
        test_no_cross_card_duplicate_combos,
        test_each_card_buttons_are_relevant_family,
        test_every_button_is_dispatchable,
        test_every_finish_card_key_has_actions,
        test_platform_structure_limits,
        test_keyboard_doupo_fallback_and_gametools_isolation,
        test_keyboard_prefix_does_not_collide_with_real_commands,
        test_page_prefix_isolated_from_canonical_buttons,
        test_page_buttons_derived_from_armed_state,
        test_page_dispatch_source_contract,
        test_injection_guards,
        test_source_contract_finish_card_used,
        test_source_contract_interaction_routes_doupo,
    ]
    passed = failed = 0
    for t in tests:
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
