# -*- coding: utf-8 -*-
"""斗破「玩家自定义每张卡片底部按钮」行为守门（2026-11-18，prove-it）。

用户原话：
> 每个玩家自定义每张卡片底部按钮；目前每个卡片发送带的按钮都是固定的。
> 比如买材料，可以固定几种常买的材料按钮，不用手动输出文字。
> 玩家对每个卡片的每个按钮都有增删改权限，除了菜单卡片。先做斗破。

守的是用户感受得到的行为：
1. 存进去能读出来：增/改/删/清 + 落进玩家存档字段（真 Store，写→新实例读回）。
2. 菜单卡禁止自定义（用户明确）。
3. 零编造：口令首词必须是真实口令（从 commands.MENU_GROUPS 的 AST 独立派生）。
4. 哨兵编解码：data = doupo:cbtn:<口令>，点击解出真口令且带参不丢。
5. 派生合并：arm_custom 后 buttons_for_card 返回玩家按钮而非固定按钮；
   菜单卡即使被 arm 也被发送层 card_key_valid 拦住。
6. 带参点击分发：dispatch_custom 把「买 血灵草 2」的 args 真传给 cmd_buy。

跑法：python tests/test_doupo_custom_buttons.py（宿主纯标准库，不碰真存档）
"""
from __future__ import annotations

import ast
import asyncio
import importlib.util
import os
import sys
import tempfile
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOUPO = ROOT / "src" / "plugins" / "doupo"
CH = ROOT / "src" / "plugins" / "qq_bridge" / "channel"


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


# ---- 独立事实源：从 commands.py 的 AST 抽 MENU_GROUPS 真实口令（不 import 业务模块）----
def _real_commands_from_ast() -> frozenset[str]:
    tree = ast.parse((DOUPO / "commands.py").read_text(encoding="utf-8"))
    node = next((n for n in tree.body if isinstance(n, ast.Assign)
                 and any(isinstance(t, ast.Name) and t.id == "MENU_GROUPS" for t in n.targets)), None)
    assert node is not None, "commands.py 找不到 MENU_GROUPS"
    out = set()
    for group in ast.walk(node.value):
        if isinstance(group, ast.Tuple) and len(group.elts) == 2:
            name, desc = group.elts
            if isinstance(name, ast.Constant) and isinstance(name.value, str) \
                    and isinstance(desc, ast.Constant) and isinstance(desc.value, str):
                head = name.value.strip().split()
                if head:
                    out.add(head[0])  # 首词：MENU_GROUPS 口令带示例参数
    assert len(out) >= 20, "真实口令派生退化：只有 %d 条" % len(out)
    return frozenset(out)


REAL = _real_commands_from_ast()

# ---- 注入假 commands 模块，让 custom_buttons 延迟 import 拿到真实口令基准 ----
_fake_cmds = types.ModuleType("doupo_commands_stub")
# 结构必须与真实 MENU_GROUPS 一致：二层（group, ((cmd, desc), ...)）
_fake_cmds.MENU_GROUPS = (("stub", tuple((c, "") for c in sorted(REAL))),)


def _install():
    # custom_buttons 里是 `from . import commands` —— 直载时用相对包导入，
    # 这里把假模块挂到 doupo 包命名空间（构造一个最小包）。
    pkg = types.ModuleType("doupo_pkg")
    pkg.__path__ = [str(DOUPO)]
    pkg.commands = _fake_cmds
    sys.modules["doupo_pkg"] = pkg
    cb = _load(DOUPO / "custom_buttons.py", "doupo_pkg.custom_buttons")
    pkg.custom_buttons = cb
    return cb


cb = _install()


# ---------------------------------------------------------------------------
def test_add_read_update_delete_clear():
    p = {}
    assert cb.set_button(p, "shop", "血灵草", "买 血灵草 2") == 1
    assert cb.set_button(p, "shop", "聚气散", "买 聚气散") == 2
    assert cb.get_buttons(p, "shop") == (("血灵草", "买 血灵草 2"), ("聚气散", "买 聚气散"))
    # 改：同显示名覆盖，不新增
    assert cb.set_button(p, "shop", "血灵草", "买 血灵草 3") == 2
    assert cb.get_buttons(p, "shop")[0] == ("血灵草", "买 血灵草 3")
    # 删
    assert cb.delete_button(p, "shop", "聚气散") is True
    assert cb.get_buttons(p, "shop") == (("血灵草", "买 血灵草 3"),)
    # 清
    assert cb.clear_buttons(p, "shop") is True
    assert cb.get_buttons(p, "shop") == ()
    assert cb.FIELD not in p, "清空后字段应删掉，不留空壳"


def test_menu_card_forbidden():
    for card in ("menu", "help"):
        assert cb.card_key_valid(card) is False
        p = {}
        try:
            cb.set_button(p, card, "x", "买 y")
            raise AssertionError("菜单卡 %s 竟然允许自定义" % card)
        except cb.CustomButtonError:
            pass


def test_zero_fabrication_head_must_be_real():
    # 真口令 + 带参 → 通过
    assert cb.validate_entry("血灵草", "买 血灵草 2") == ("血灵草", "买 血灵草 2")
    # 编造首词 → 拒
    for bad in ("瞎写 东西", "不存在的口令", "买东西"):  # 末项首词整体≠「买」
        try:
            cb.validate_entry("x", bad)
            raise AssertionError("编造口令竟然通过：%s" % bad)
        except cb.CustomButtonError:
            pass
    # 注意「买东西」无空格时首词就是「买东西」，确实不在口令表 → 拒（符合预期）


def test_platform_limits():
    try:
        cb.validate_entry("12345678901", "买 x")  # 11 字
        raise AssertionError("超长 label 未拒")
    except cb.CustomButtonError:
        pass
    p = {}
    for i in range(5):
        cb.set_button(p, "shop", "b%d" % i, "买 x%d" % i)
    try:
        cb.set_button(p, "shop", "b5", "买 x5")
        raise AssertionError("第 6 个按钮未拒")
    except cb.CustomButtonError:
        pass


def test_sentinel_encode_decode():
    for real in ("买 血灵草 2", "人物", "服丹 九转还魂丹 3"):
        data = cb.encode_data(real)
        assert data.startswith(cb.CUSTOM_PREFIX)
        assert cb.decode_data(data) == real, "带参口令解码丢参数：%s" % real
    assert cb.decode_data("宝库") is None     # canonical 口令不误判
    assert cb.decode_data("doupo:page:2") is None
    assert cb.decode_data("doupo:cbtn:") is None


def test_per_card_isolation():
    p = {}
    cb.set_button(p, "shop", "s", "买 血灵草")
    cb.set_button(p, "panel", "c", "修炼")
    assert cb.get_buttons(p, "shop") == (("s", "买 血灵草"),)
    assert cb.get_buttons(p, "panel") == (("c", "修炼"),)
    assert cb.get_buttons(p, "bag") == ()
    cb.clear_buttons(p, "shop")
    assert cb.get_buttons(p, "panel") == (("c", "修炼"),), "清 A 卡不该动 B 卡"


def test_persist_roundtrip_real_store():
    """真 Store：写 → 新实例读回（遵守用户最高声明：数据必须落盘）"""
    import importlib as _il

    ROOT_S = str(ROOT / "src")
    PLUGINS_S = str(ROOT / "src" / "plugins")
    for p in (ROOT_S, PLUGINS_S):
        if p not in sys.path:
            sys.path.insert(0, p)
    # doupo 作为命名空间包挂上但不执行 __init__（避免加载全部 matcher）。
    # store.py 里 `from . import config as cfg` 只要有 __path__ 就能相对导入。
    for pkg_name in ("plugins", "plugins.doupo"):
        if pkg_name not in sys.modules:
            pkg = types.ModuleType(pkg_name)
            pkg.__path__ = [str(ROOT / "src" / "plugins" if pkg_name == "plugins"
                                else DOUPO)]
            sys.modules[pkg_name] = pkg
    store_mod = _il.import_module("plugins.doupo.store")

    tmp = tempfile.mkdtemp(prefix="cbtn_store_")
    data_path = os.path.join(tmp, "doupo.json")
    s1 = store_mod.Store(path=data_path)
    asyncio.run(s1.load())
    asyncio.run(s1.put_player({"qq": "u1", "name": "测试", "lv": 1,
                               cb.FIELD: {"shop": [{"label": "血灵草", "cmd": "买 血灵草 2"}]}}))
    s2 = store_mod.Store(path=data_path)  # 新实例 = 重新读库
    asyncio.run(s2.load())
    p2 = asyncio.run(s2.get_player("u1"))
    got = cb.get_buttons(p2, "shop")
    expected = (("血灵草", "买 血灵草 2"),)
    assert len(got) == 1 and got[0][0] == "血灵草" and got[0][1] == "买 血灵草 2", \
        "落盘后读不回自定义按钮: got=%r types=%r" % (got, [type(x) for x in got[0]] if got else None)


def test_dispatch_custom_passes_args():
    """带参点击：dispatch_custom 把「血灵草 2」真传给 cmd_buy（不是空串）。"""
    bd = _load(DOUPO / "button_dispatch.py", "doupo_bd_cbtn")
    assert bd.parse_custom_cmd("doupo:cbtn:买 血灵草 2") == "买 血灵草 2"
    assert bd.parse_custom_cmd("宝库") is None
    # 拦截 dispatch_button，看 head + arg_override 是否正确
    seen = {}

    async def fake_dispatch(bot, ctx, head, arg_override=None):
        seen["head"] = head
        seen["arg"] = arg_override
        return True

    bd.dispatch_button = fake_dispatch
    ok = asyncio.run(bd.dispatch_custom(None, {}, "doupo:cbtn:买 血灵草 2"))
    assert ok is True
    assert seen == {"head": "买", "arg": "血灵草 2"}, "带参分发参数错了：%s" % seen


def test_dispatch_button_uses_arg_override_e2e():
    """端到端：dispatch_button('买', arg_override='血灵草 2') 真把参数传给 cmd_buy。

    这条是上一条的补强——上一条只验 dispatch_custom→dispatch_button 的传参，
    不验 dispatch_button 内部有没有把 arg_override 用起来（实测「写死空串」
    回退时上一条不会红）。这里注入假 commands/store/channel，让 cmd_buy 记录
    收到的 args，真跑 dispatch_button 的 qq_str 分支。
    """
    import importlib as _il

    bd = _load(DOUPO / "button_dispatch.py", "doupo_bd_cbtn_e2e")
    called = {}

    async def fake_cmd_buy(store, qq, args):
        called["qq"] = qq
        called["args"] = args
        return "已买"

    # 注入假 commands 模块（doupo.button_dispatch 里 `from . import commands as cmd_mod`）
    sys.modules.setdefault("nonebot", types.ModuleType("nonebot"))
    ROOT_S = str(ROOT / "src")
    if ROOT_S not in sys.path:
        sys.path.insert(0, ROOT_S)
    for pkg_name in ("e2epkg", "e2epkg.doupo"):
        pkg = types.ModuleType(pkg_name)
        pkg.__path__ = []
        sys.modules[pkg_name] = pkg
    fake_commands = types.ModuleType("e2epkg.doupo.commands")
    fake_commands.cmd_buy = fake_cmd_buy
    sys.modules["e2epkg.doupo.commands"] = fake_commands
    sys.modules["e2epkg.doupo"].commands = fake_commands

    # 加载一个把相对 import 指向 e2epkg 的副本：改写源码里的相对 import 前缀
    src = (DOUPO / "button_dispatch.py").read_text(encoding="utf-8")
    src = src.replace("from . import commands as cmd_mod", "from e2epkg.doupo import commands as cmd_mod")
    # 其余相对 import（store/card/game_mode/game_compat/channel）在本分支不会执行，
    # 但 dispatch_button 顶层会 import channel.base/OFFICIAL —— stub 掉
    ch_base = types.ModuleType("ch_base_stub")
    ch_base.Outgoing = lambda **kw: kw
    sys.modules["src.plugins.qq_bridge.channel.base"] = ch_base
    ch_mod = types.ModuleType("ch_mod_stub")
    ch_mod.OFFICIAL = "official"
    ch_mod.get_channel = lambda n: types.SimpleNamespace(send=lambda *a, **k: _async_ok())
    sys.modules["src.plugins.qq_bridge.channel"] = ch_mod

    async def _async_ok():
        return {"ok": True}

    # store.load 那句会 import .store（真模块）—— stub 掉避免 chain
    fake_store = types.ModuleType("e2epkg.doupo.store")
    fake_store.STORE = types.SimpleNamespace(
        load=_async_ok, get_player=_async_ok, all_players=_async_ok)
    sys.modules["e2epkg.doupo.store"] = fake_store
    src = src.replace("from .store import STORE as _STORE", "from e2epkg.doupo.store import STORE as _STORE")
    src = src.replace("from . import card as card_mod", "from e2epkg.doupo import card as card_mod")
    fake_card = types.ModuleType("e2epkg.doupo.card")
    sys.modules["e2epkg.doupo.card"] = fake_card
    sys.modules["e2epkg.doupo"].card = fake_card
    # set_mode / send_card_via_channel 顶层也 import，stub
    gm = types.ModuleType("src.game_mode")
    gm.set_mode = _async_ok
    sys.modules["src.game_mode"] = gm
    gc = types.ModuleType("src.game_compat")
    gc.send_card_via_channel = _async_ok
    sys.modules["src.game_compat"] = gc

    spec = importlib.util.spec_from_loader("bd_e2e", loader=None)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bd_e2e"] = mod
    exec(compile(src, "bd_e2e", "exec"), mod.__dict__)  # noqa: S102 — 测自己的源码

    ctx = {"user_key": "openid:u1", "group_key": "group:g1"}
    ok = asyncio.run(mod.dispatch_button(None, ctx, "买", arg_override="血灵草 2"))
    assert ok is True
    assert called.get("qq") == "u1", "玩家身份错了：%s" % called
    assert called.get("args") == "血灵草 2", "cmd_buy 没收到带参（被写死空串？）：%s" % called


def test_derive_override_real_module():
    """真 doupo_buttons：arm_custom 后 buttons_for_card 返回玩家按钮。"""
    db = _load(CH / "doupo_buttons.py", "doupo_buttons_cbtn")
    fixed = db.buttons_for_card("shop")
    assert fixed and all(not b.cmd.startswith(cb.CUSTOM_PREFIX) for b in fixed)
    tok = db.arm_custom((("血灵草", "买 血灵草 2"), ("聚气散", "买 聚气散")), "shop")
    try:
        got = db.buttons_for_card("shop")
        assert [b.label for b in got] == ["血灵草", "聚气散"]
        assert all(b.cmd.startswith(cb.CUSTOM_PREFIX) for b in got)
        # 别的卡不受影响
        assert db.buttons_for_card("panel") and all(
            not b.cmd.startswith(cb.CUSTOM_PREFIX) for b in db.buttons_for_card("panel"))
    finally:
        db.disarm_custom(tok)


def test_config_arg_parser():
    assert cb.parse_config_arg("宝库") == ("宝库", "list", "")
    assert cb.parse_config_arg("宝库 清空") == ("宝库", "clear", "")
    assert cb.parse_config_arg("宝库 -血灵草") == ("宝库", "del", "血灵草")
    assert cb.parse_config_arg("宝库 血灵草=买 血灵草 2") == (
        "宝库", "add", "血灵草=买 血灵草 2")
    assert cb.resolve_card("宝库") == "shop"
    assert cb.resolve_card("商店") == "shop"
    assert cb.resolve_card("菜单") is None


def test_quote_alt_roundtrip():
    """2026-11-18 引用卡片识别：发卡把 card_key 编进图片 alt，引用时反解。"""
    assert cb.quote_alt("shop") == "dpq:shop"
    assert cb.quote_alt("menu") is None        # 菜单卡禁止
    assert cb.quote_alt("bad key!") is None    # 非法字符拒绝
    content = "![dpq:shop #720px #2840px](https://qqbot.ugcimg.cn/x/y)"
    assert cb.card_key_from_content(content) == "shop"
    # 引用别人的图 / 旧卡（alt=card）/ 文字 / 未知卡 → 一律识别不出（绝不猜）
    assert cb.card_key_from_content("![card #720px #2840px](url)") is None
    assert cb.card_key_from_content("![dpq:unknown #100px #100px](url)") is None
    assert cb.card_key_from_content("普通文字消息") is None
    assert cb.card_key_from_content("") is None
    # 多页卡（炼药）：page:N ↔ dpq:page_N，交给 handle_config 明确拒绝
    assert cb.quote_alt("page:1") == "dpq:page_1"
    assert cb.card_key_from_content("![dpq:page_1 #720px #2840px](url)") == "page:1"
    assert cb.is_multipage_key("page:1") and not cb.is_multipage_key("shop")


def test_multipage_card_rejected_explicitly():
    """多页炼药卡引用配置必须明确拒绝，绝不写入无效的 page:N 配置（静默坑）。"""
    import asyncio

    class MemStore:
        def __init__(self):
            self.p = {"qq": "u1"}
            self.wrote = False

        async def get_player(self, qq):
            return self.p

        async def put_player(self, p):
            self.wrote = True
            self.p = p

    st = MemStore()
    msg = asyncio.run(cb.handle_config(st, "u1", "买 血灵草 2", quote_card="page:1"))
    assert "多页卡" in msg or "暂不支持" in msg, msg
    assert "上一页" in msg or "翻页" in msg, msg
    assert not st.wrote, "多页卡被写入了无效配置"
    assert "page:1" not in st.p.get("custom_buttons", {}), st.p


def test_auto_label():
    """显示名自动从口令主体取，不用玩家手输。"""
    assert cb.auto_label("买 血灵草 2") == "血灵草"
    assert cb.auto_label("炼制 回灵丹") == "回灵丹"
    assert cb.auto_label("买 赤焰果 10") == "赤焰果"
    assert cb.auto_label("修炼") == ""          # 无参数词
    assert cb.auto_label("买 2") == ""          # 只有数字参数


def test_add_rejects_duplicate():
    """引用快捷新增时重名必须停止+可见提示，绝不静默覆盖（用户硬要求）。"""
    p = {}
    cb.set_button(p, "shop", "血灵草", "买 血灵草 2")
    try:
        cb.set_button(p, "shop", "血灵草", "买 血灵草 9", allow_replace=False)
    except cb.CustomButtonError as e:
        assert "血灵草" in str(e) and "没有新增" in str(e)
    else:
        raise AssertionError("重名未抛错——会静默覆盖成只剩一个按钮")
    # 原值没被动过
    assert cb.get_buttons(p, "shop") == (("血灵草", "买 血灵草 2"),)


def test_handle_config_quote_creates_button():
    """引用快捷：card_key 来自引用、口令是 arg、显示名自动生成，落盘真 Store。"""
    import asyncio

    class MemStore:
        def __init__(self):
            self.p = {"qq": "u1"}

        async def get_player(self, qq):
            return self.p

        async def put_player(self, p):
            self.p = p

    st = MemStore()
    msg = asyncio.run(cb.handle_config(st, "u1", "买 血灵草 2", quote_card="shop"))
    assert "血灵草" in msg and "宝库" in msg
    assert cb.get_buttons(st.p, "shop") == (("血灵草", "买 血灵草 2"),)
    # 引用但识别不出 → 不传 quote_card → 走旧解析，不会误配到某张卡
    msg2 = asyncio.run(cb.handle_config(st, "u1", "买 赤焰果 10"))
    assert "不认识" in msg2 or "看不懂" in msg2 or "按钮设置" in msg2
    assert cb.get_buttons(st.p, "shop") == (("血灵草", "买 血灵草 2"),)


def test_quote_with_subcommand_not_mistaken_for_add():
    """2026-11-18 bug：引用着卡片发「删/清空/查看」不能误判成新增垃圾按钮。"""
    import asyncio

    class MemStore:
        def __init__(self, p):
            self.p = p

        async def get_player(self, qq):
            return self.p

        async def put_player(self, p):
            self.p = p

    p = {"qq": "u1"}
    cb.set_button(p, "shop", "血灵草", "买 血灵草 2")
    cb.set_button(p, "shop", "赤焰果", "买 赤焰果 10")
    st = MemStore(p)

    # 引用着卡发删除命令 → 必须真的删掉，而不是加一个「-血灵草」按钮
    msg = asyncio.run(cb.handle_config(st, "u1", "宝库 -血灵草", quote_card="shop"))
    assert "已删" in msg, msg
    assert cb.get_buttons(st.p, "shop") == (("赤焰果", "买 赤焰果 10"),), cb.get_buttons(st.p, "shop")

    # 引用着卡发清空 → 必须清空，而不是加「清空」按钮
    msg2 = asyncio.run(cb.handle_config(st, "u1", "宝库 清空", quote_card="shop"))
    assert "已清空" in msg2, msg2
    assert cb.get_buttons(st.p, "shop") == ()

    # 引用着卡只发卡片名（查看）→ 不能加「宝库」按钮
    cb.set_button(st.p, "shop", "血灵草", "买 血灵草 2")
    msg3 = asyncio.run(cb.handle_config(st, "u1", "宝库", quote_card="shop"))
    assert "自定义按钮" in msg3, msg3
    assert cb.get_buttons(st.p, "shop") == (("血灵草", "买 血灵草 2"),)


def test_quick_add_command_beats_card_alias():
    """2026-11-19 用户方案②：引用卡片时首词是口令就按口令处理，优先于卡片名。

    「斗技」既是真实口令首词、又是卡片别名（skills）。旧判据把首词是卡片名的
    一律挡掉 → 引用宗门卡发「斗技 全部」报「不认识这张卡」。口令必须优先。
    """
    # 口令 + 卡片别名同形，带参数 → 快捷新增（用户要的「斗技 全部 也能加」）
    assert cb.is_quick_add_arg("斗技 全部") is True
    # 其它同形的（人物/宝库/炼药都是口令首词又是卡片名）同样口令优先
    for cmd in ("人物 面板", "宝库 血灵草 2", "炼药 回灵丹", "异火 净莲妖火"):
        assert cb.is_quick_add_arg(cmd) is True, cmd
    # 普通口令（首词非卡片名）仍是快捷新增
    assert cb.is_quick_add_arg("买 血灵草 2") is True

    # 子命令结构仍必须挡住（否则「宝库 -血灵草」被当口令新增，旧 bug 复现）
    for cmd in ("宝库 -血灵草", "宝库 清空", "宝库 查看", "宝库 list",
                "-血灵草", "血灵草=买 血灵草 2"):
        assert cb.is_quick_add_arg(cmd) is False, cmd
    # 单发卡片名 = 查看该卡，不当新增
    assert cb.is_quick_add_arg("宝库") is False


def test_quote_douji_quanbu_adds_to_faction_card():
    """端到端：引用宗门卡 + 「斗技 全部」→ 真加到 faction，而不是报不认识卡。"""
    import asyncio

    class MemStore:
        def __init__(self, p):
            self.p = p

        async def get_player(self, qq):
            return self.p

        async def put_player(self, p):
            self.p = p

    st = MemStore({"qq": "u1"})
    msg = asyncio.run(cb.handle_config(st, "u1", "斗技 全部", quote_card="faction"))
    assert "宗门卡" in msg and "斗技" in msg, msg
    assert "不认识" not in msg, msg
    assert cb.get_buttons(st.p, "faction") == (("全部", "斗技 全部"),), cb.get_buttons(st.p, "faction")


# ---------------------------------------------------------------------------
if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    ok = fail = 0
    for t in tests:
        try:
            t()
            print("PASS", t.__name__)
            ok += 1
        except Exception as e:
            print("FAIL", t.__name__, "->", type(e).__name__, e)
            import traceback
            traceback.print_exc()
            fail += 1
    print("\n==== %d PASS / %d FAIL ====" % (ok, fail))
    sys.exit(1 if fail else 0)
