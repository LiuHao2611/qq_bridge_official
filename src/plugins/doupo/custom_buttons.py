# -*- coding: utf-8 -*-
"""斗破 · 玩家自定义每张卡片底部按钮（2026-11-18）。

## 用户诉求
> 每个玩家自定义每张卡片底部按钮；目前每张卡的按钮都是固定的。比如买材料，
> 可以固定几种常买的材料按钮，不用手动输出文字。玩家对每个卡片的每个按钮
> 都有增删改权限，除了菜单卡片。先做斗破。

## 存在哪（遵守用户最高声明：用户数据必须落盘）
配置存在**玩家存档** ``p["custom_buttons"]``，形状::

    {"shop": [{"label": "血灵草", "cmd": "买 血灵草 2"}, ...], "panel": [...]}

玩家存档走 ``doupo/store.py`` 的 SQLite（一人一行、WAL + 事务、原子落盘、
``DATA_PATH`` 单一来源），所以自定义按钮天然跟着存档一起持久化、跨进程安全、
重建容器不丢。**不新建 Store**：多一个落盘点就多一处要过持久化门禁、多一处
可能被漏挂载（2026-11-16 六库写进镜像层的教训）。

## 按钮 data 为什么用哨兵前缀
按钮点击 ``qq_interaction`` 现在只把 data 当 canonical 口令分发（斗破走
``button_dispatch._PLAN``）。玩家自定义口令是带参串（如 ``买 血灵草 2``），
直接下发会被判「非斗破口令」落到 game_tools → 点了没反应。故自定义按钮的
data 编码为 ``doupo:cbtn:<真实口令>``，点击侧解码后走带参分发。

## 硬不变量（都由 tests/test_doupo_custom_buttons.py 守门 + 注入反例）
1. **菜单卡禁配**：``menu`` / ``help`` 拒绝（用户明确「除了菜单卡片」）。
2. **真口令校验**：cmd 首词必须 ∈ ``commands.MENU_GROUPS`` 的真实口令全集，
   杜绝编造一个点了没反应的假口令。带参口令只校验首词。
3. **平台限制**：显示名 ≤10 字（QQ 按钮 label 上限），每卡 ≤5 个按钮。
4. **打平桩**：内存里读到的旧档/坏档不炸，一律按空配置处理（只增不覆盖）。
"""
from __future__ import annotations

from typing import Any

#: 玩家存档里存自定义按钮的字段名（单一来源，别在别处硬编码字符串）
FIELD = "custom_buttons"

#: 禁止自定义的卡片 key（菜单类）。用户明确「除了菜单卡片」。
#: menu = 斗破菜单卡，help = 帮助/玩法卡 —— 都是导航入口，改了会让人找不到功能。
FORBIDDEN_CARDS = frozenset({"menu", "help"})

#: QQ 平台 button label 上限（qq_keyboard 也守这个数）
LABEL_MAX = 10
#: 每卡按钮上限（平台每行≤5，当前实现单行排列）
PER_CARD_MAX = 5

#: 自定义按钮 data 哨兵前缀。与 doupo_buttons 的 quiz/page 前缀同源风格：
#: 非 canonical 口令、含冒号，绝不可能与真实口令冲突。
CUSTOM_PREFIX = "doupo:cbtn:"

#: 引用卡片识别（2026-11-18）：官方群消息被引用的卡片正文只有一张内嵌图片
#: markdown（``![alt #宽px #高px](url)``），拿不到 msg_id / 卡片类型文字。
#: 故发卡时把 card_key 编进图片 alt（``dpq:<key>``），引用时从 reply.content
#: 正则反解。信息随消息走，重启不丢，零内存状态。
QUOTE_ALT_PREFIX = "dpq:"

#: 多页卡（炼药）的内部 key 前缀。翻页按钮占用按钮位，用户已决定「翻页优先，
#: 不做自定义」。发卡 alt 把它编码成 ``dpq:page_N``（冒号→下划线，避免破坏
#: markdown），引用时反解回 ``page:N`` 并给明确提示，绝不写入无效配置。
MULTIPAGE_PREFIX = "page:"
_MULTIPAGE_ALT = "page_"


class CustomButtonError(ValueError):
    """玩家配置不合法。**必须**向上抛成可见提示，不许静默吞（禁兜底）。"""


def card_key_valid(card_key: str) -> bool:
    """卡片 key 是否可自定义：非空、非菜单卡。"""
    k = (card_key or "").strip()
    return bool(k) and k not in FORBIDDEN_CARDS


def real_commands() -> frozenset[str]:
    """真实口令**首词**全集（canonical）。延迟 import 避免循环；零编造校验的事实源。

    MENU_GROUPS 的口令带示例参数（如「买 血灵草 2」「服丹 回灵丹」），玩家自定义
    口令也带参，所以校验只比对**首词**（买/服丹/…），参数由业务自己解析。
    """
    from . import commands as cmd_mod  # noqa: PLC0415

    heads = set()
    for _, rows in cmd_mod.MENU_GROUPS:
        for name, _desc in rows:
            head = (name or "").strip().split()
            if head:
                heads.add(head[0])
    return frozenset(heads)


def encode_data(cmd: str) -> str:
    """真实口令 → 按钮 data（带哨兵前缀）。"""
    return CUSTOM_PREFIX + (cmd or "").strip()


def decode_data(data: str) -> str | None:
    """按钮 data → 真实口令；非自定义哨兵返回 None（交给其它分发）。"""
    raw = (data or "").strip()
    if not raw.startswith(CUSTOM_PREFIX):
        return None
    cmd = raw[len(CUSTOM_PREFIX):].strip()
    return cmd or None


def quote_alt(card_key: str) -> str | None:
    """发卡时图片 alt 里嵌入的卡片标记；非法卡（如菜单卡）返回 None（用默认 alt）。

    多页卡 key ``page:N`` 编码成 ``dpq:page_N``（冒号→下划线），让引用时能识别
    出「这是多页卡」并明确拒绝，而不是在存档里写入永远不生效的配置。
    """
    import re as _re  # noqa: PLC0415

    k = (card_key or "").strip()
    if k.startswith(MULTIPAGE_PREFIX) and k[len(MULTIPAGE_PREFIX):].isdigit():
        return QUOTE_ALT_PREFIX + _MULTIPAGE_ALT + k[len(MULTIPAGE_PREFIX):]
    if not card_key_valid(k) or not _re.fullmatch(r"[a-z0-9_]+", k):
        return None
    return QUOTE_ALT_PREFIX + k


def card_key_from_content(content: str) -> str | None:
    """从被引用的卡片 markdown content 反解 card_key；非斗破卡/过期消息返 None。

    content 形如 ``![dpq:shop #720px #2840px](https://...)``。只有带我们发的
    ``dpq:`` 标记才算识别成功——引用别人的图、引用旧卡、引用文字都不处理
    （用户明确「识别不了就不理，绝不猜卡片」）。多页卡 ``dpq:page_N`` 反解成
    ``page:N``，交给 handle_config 明确拒绝。
    """
    import re as _re  # noqa: PLC0415

    m = _re.search(r"!\[" + _re.escape(QUOTE_ALT_PREFIX) + r"([a-z0-9_]+)\s+#\d+px",
                   content or "")
    if not m:
        return None
    key = m.group(1)
    if key.startswith(_MULTIPAGE_ALT) and key[len(_MULTIPAGE_ALT):].isdigit():
        return MULTIPAGE_PREFIX + key[len(_MULTIPAGE_ALT):]
    return key if key in CARD_LABELS else None


def is_multipage_key(card_key: str | None) -> bool:
    """是否多页卡内部 key（炼药卡）——这类卡翻页优先，不支持自定义按钮。"""
    k = (card_key or "").strip()
    return k.startswith(MULTIPAGE_PREFIX) and k[len(MULTIPAGE_PREFIX):].isdigit()


def validate_entry(label: str, cmd: str, *, real: frozenset[str] | None = None) -> tuple[str, str]:
    """校验一条自定义按钮，返回归一化后的 ``(label, cmd)``；违例抛 :class:`CustomButtonError`。

    * label 非空且 ≤ LABEL_MAX 字（平台限制）；
    * cmd 首词（空格切分）必须 ∈ 真实口令全集 —— 带参口令只校验首词，
      这样 ``买 血灵草 2`` 合法而 ``乱写 东西`` 被拒。
    """
    lab = (label or "").strip()
    if not lab:
        raise CustomButtonError("按钮显示名不能为空")
    if len(lab) > LABEL_MAX:
        raise CustomButtonError("按钮显示名最多 %d 个字" % LABEL_MAX)
    full = (cmd or "").strip()
    if not full:
        raise CustomButtonError("按钮口令不能为空")
    head = full.split()[0]
    real = real if real is not None else real_commands()
    if head not in real:
        raise CustomButtonError("「%s」不是有效口令（发「帮助」看口令表）" % head)
    return lab, full


def _store(p: dict[str, Any]) -> dict[str, list[dict[str, str]]]:
    """取玩家存档里的配置字典；脏数据一律当空（不炸、不回写其它字段）。"""
    raw = p.get(FIELD)
    if not isinstance(raw, dict):
        return {}
    out: dict[str, list[dict[str, str]]] = {}
    for key, items in raw.items():
        if not isinstance(key, str) or not isinstance(items, list):
            continue
        clean = []
        for it in items:
            if isinstance(it, dict) and isinstance(it.get("label"), str) and isinstance(it.get("cmd"), str):
                clean.append({"label": it["label"], "cmd": it["cmd"]})
        if clean:
            out[key] = clean
    return out


def get_buttons(p: dict[str, Any], card_key: str) -> tuple[tuple[str, str], ...]:
    """取某卡自定义按钮 ``((label, cmd), ...)``；无配置/菜单卡返空。"""
    if not card_key_valid(card_key):
        return ()
    entries = _store(p).get((card_key or "").strip(), [])
    return tuple((e["label"], e["cmd"]) for e in entries[:PER_CARD_MAX])


def set_button(p: dict[str, Any], card_key: str, label: str, cmd: str,
               *, allow_replace: bool = True) -> int:
    """新增或按显示名覆盖一条按钮，返回该卡按钮总数。

    ``allow_replace=False``（引用快捷新增用）：同名直接抛 :class:`CustomButtonError`
    停止，绝不静默覆盖——用户明确「新增有重名时直接停止增加并提示」。
    默认 True 保留旧语法的就地改语义（测试与手动覆盖仍可用）。
    """
    k = (card_key or "").strip()
    if not card_key_valid(k):
        raise CustomButtonError("菜单卡不允许自定义按钮")
    lab, full = validate_entry(label, cmd)
    cfg = _store(p)
    items = list(cfg.get(k, []))
    # 同显示名就地改（保持按钮位置不跳动）；不同名追加
    idx = next((i for i, x in enumerate(items) if x["label"] == lab), None)
    if idx is None:
        items.append({"label": lab, "cmd": full})
    else:
        if not allow_replace:
            raise CustomButtonError(
                "【%s】已有「%s」按钮（→ %s），没有新增。要改它请发「按钮设置 %s -%s」删掉后再加，"
                "或直接把它写全（旧语法「%s %s=%s」）。"
                % (CARD_LABELS.get(k, k), lab, items[idx]["cmd"],
                   k, lab, k, lab, full))
        items[idx] = {"label": lab, "cmd": full}
    if len(items) > PER_CARD_MAX:
        raise CustomButtonError("每张卡最多 %d 个按钮（QQ 平台限制）" % PER_CARD_MAX)
    cfg[k] = items
    p[FIELD] = cfg
    return len(items)


def delete_button(p: dict[str, Any], card_key: str, label: str) -> bool:
    """按显示名删除一条按钮；返回是否删掉了。"""
    k = (card_key or "").strip()
    if not card_key_valid(k):
        raise CustomButtonError("菜单卡不允许自定义按钮")
    cfg = _store(p)
    items = cfg.get(k, [])
    kept = [x for x in items if x["label"] != (label or "").strip()]
    changed = len(kept) != len(items)
    if kept:
        cfg[k] = kept
    else:
        cfg.pop(k, None)
    if cfg:
        p[FIELD] = cfg
    else:
        p.pop(FIELD, None)
    return changed


def clear_buttons(p: dict[str, Any], card_key: str) -> bool:
    """清空某卡全部自定义按钮（恢复默认）；返回是否有改动。"""
    k = (card_key or "").strip()
    if not card_key_valid(k):
        raise CustomButtonError("菜单卡不允许自定义按钮")
    cfg = _store(p)
    changed = k in cfg
    cfg.pop(k, None)
    if cfg:
        p[FIELD] = cfg
    else:
        p.pop(FIELD, None)
    return changed


def list_buttons(p: dict[str, Any]) -> dict[str, tuple[tuple[str, str], ...]]:
    """列出全部配置（供「按钮设置」查看），只含非空卡。"""
    return {
        k: tuple((e["label"], e["cmd"]) for e in items[:PER_CARD_MAX])
        for k, items in _store(p).items()
        if k in CARD_LABELS
    }


#: 卡片 key → 中文名（配置提示用）。与 doupo_buttons.CARD_ACTIONS 的 key 同源；
#: 显式列出而非直接引用，避免 channel 层与业务层耦合；新增卡时同步这里。
CARD_LABELS = {
    "panel": "人物卡", "roster": "斗技名册", "skills": "斗技卡", "faction": "宗门卡",
    "flames": "异火卡", "shop": "宝库卡", "bag": "背包卡", "rank": "排行卡",
    "recipes": "炼药卡", "battle": "战斗回看卡",
}

#: 玩家配置时可用的卡片别名（含中文口语名 + key）。菜单卡不在其中（禁配）。
#: 「引用卡片」落地为发卡名/key，例如「按钮设置 宝库 …」= 引用宝库卡。
CARD_ALIASES = {
    "人物": "panel", "人物卡": "panel", "面板": "panel", "属性": "panel", "panel": "panel",
    "斗技名册": "roster", "名录": "roster", "roster": "roster",
    "斗技": "skills", "斗技卡": "skills", "skills": "skills",
    "宗门": "faction", "宗门卡": "faction", "faction": "faction",
    "异火": "flames", "异火卡": "flames", "flames": "flames",
    "宝库": "shop", "商店": "shop", "宝库卡": "shop", "shop": "shop",
    "背包": "bag", "背包卡": "bag", "bag": "bag",
    "排行": "rank", "排行榜": "rank", "rank": "rank",
    "炼药": "recipes", "丹方": "recipes", "炼药卡": "recipes", "recipes": "recipes",
    "战斗回看": "battle", "战斗回看卡": "battle", "battle": "battle",
}


def resolve_card(token: str) -> str | None:
    """玩家输入的卡片名/key → 规范 card_key；不认识返 None。"""
    t = (token or "").strip().lower()
    return CARD_ALIASES.get(t)


def help_text() -> str:
    """配置帮助（玩家发「按钮设置」无参时回）。"""
    cards = "、".join(CARD_LABELS[k] for k in (
        "panel", "skills", "faction", "flames", "shop", "bag", "rank", "recipes", "battle"))
    return (
        "自定义卡片按钮（只对斗破生效，菜单卡除外）\n"
        "· 按钮设置 <卡片> —— 看该卡当前按钮\n"
        "· 按钮设置 <卡片> 显示名=口令 —— 增/改一个按钮（如：按钮设置 宝库 血灵草=买 血灵草 2）\n"
        "· 按钮设置 <卡片> -显示名 —— 删一个按钮\n"
        "· 按钮设置 <卡片> 清空 —— 恢复默认按钮\n"
        "· 可配卡片：%s\n"
        "配好后发对应卡片即可看到你的专属按钮。" % cards
    )


def parse_config_arg(arg: str) -> tuple[str, str, str]:
    """解析「<卡片> <动作>」参数 → (card_token, action, payload)。

    动作三态：``add``（payload 为 label=cmd）、``del``（payload 为 label）、
    ``clear`` / ``list`` / ``help``（payload 空）。无卡片 token 时 card_token=""。
    """
    parts = (arg or "").strip().split(None, 1)
    card = parts[0] if parts else ""
    rest = parts[1].strip() if len(parts) > 1 else ""
    if not rest or rest in ("查看", "看", "list"):
        return card, "list", ""
    if rest == "清空":
        return card, "clear", ""
    if rest.startswith("-"):
        return card, "del", rest[1:].strip()
    if "=" in rest:
        label, cmd = rest.split("=", 1)
        return card, "add", "%s=%s" % (label.strip(), cmd.strip())
    return card, "bad", rest


#: 引用卡片后，第二词若是这些子命令词则走旧语法（删/清空/查看），不当口令参数。
#: 「斗技 全部」的「全部」是口令参数、不在此列 —— 这正是 2026-11-19 要支持的场景。
_QUOTE_SUBCOMMAND_WORDS = frozenset({"清空", "查看", "看", "list", "help", "帮助"})


def is_quick_add_arg(arg: str) -> bool:
    """判断「引用卡片后的 arg」是不是一条纯口令快捷新增（而非删/清空/查看等子命令）。

    历史 bug 与两次修复：
    * 2026-11-18：原来只判 ``\"=\" not in arg``，引用着卡发 ``宝库 -血灵草``（删）
      /``宝库 清空``/``宝库``（查看）都被当新增，加上垃圾按钮。
    * 2026-11-19（用户方案②「引用时首词是口令就按口令处理，优先于卡片名」）：
      旧判据把「首词是卡片名」一律排除，导致 ``斗技 全部``（斗技既是口令又是
      卡片别名）被挡掉、报「不认识这张卡」。改为**口令优先于卡片名**：首词是
      真实口令即按口令新增，卡片名让位。但必须先挡住子命令结构，否则
      ``宝库 -血灵草``（宝库也是口令）会被误判成新增。

    判据（按顺序，任一命中即非快捷新增）：
    1. 含 ``=`` → 旧语法 add；
    2. 首词以 ``-`` 开头 → 删；
    3. 第二词以 ``-`` 开头，或是 ``清空/查看/看/list/help/帮助`` → 子命令；
    4. 整句只有一个词且它是卡片名 → 查看该卡（如引用后只发「宝库」）；
    5. 否则：首词 ∈ 真实口令 → 快捷新增（口令优先，哪怕它同时是卡片名）。
    """
    raw = (arg or "").strip()
    parts = raw.split()
    if not parts or "=" in raw:
        return False
    first = parts[0]
    if first.startswith("-"):
        return False
    if len(parts) > 1:
        second = parts[1]
        if second.startswith("-") or second in _QUOTE_SUBCOMMAND_WORDS:
            return False
    if len(parts) == 1 and resolve_card(first) is not None:
        return False  # 单发卡片名 = 查看该卡
    return first in real_commands()


def auto_label(cmd: str) -> str:
    """从完整口令自动取显示名：跳过动作首词，取其后第一个非纯数字词。

    ``买 血灵草 2`` → ``血灵草``；``炼制 回灵丹`` → ``回灵丹``；
    ``修炼`` → ``''``（没有参数词，调用方回退到整句或报错）。
    """
    parts = (cmd or "").strip().split()
    if len(parts) < 2:
        return ""
    for word in parts[1:]:
        if not word.isdigit():
            return word[:LABEL_MAX]
    return ""


async def handle_config(store, qq: str, arg: str, *,
                        quote_card: str | None = None) -> str:
    """配置口令主逻辑：返回给用户的话。全程落盘走 store.put_player（SQLite 原子写）。

    ``quote_card``：引用卡片时从卡片图片标记反解出的 card_key。给了它且没写
    卡片名时，走「引用快捷新增」——arg 整体是口令，显示名自动取，卡片就是被引用的卡。
    """
    # ---- 多页卡（炼药）：翻页按钮占用按钮位，用户已决定不做自定义 ----
    # 必须明确拒绝，绝不能写入 p["custom_buttons"]["page:N"]（发送层不读，
    # 那就是个永远不生效的静默坑）。
    if is_multipage_key(quote_card):
        return "【炼药卡】是多页卡，靠「上一页/下一页」翻页，翻页按钮占用了底部按钮位，暂不支持自定义按钮。其它卡（宝库/人物/斗技等）都可以配。"

    # ---- 引用快捷新增：arg 是纯口令（非删/清空/查看），卡片来自被引用卡片 ----
    if quote_card and is_quick_add_arg(arg):
        card_key = quote_card
        cmd = (arg or "").strip()
        if not cmd:
            return "引用了【%s】，但没写口令。例：按钮设置 买 血灵草 2" % CARD_LABELS.get(card_key, card_key)
        label = auto_label(cmd) or cmd[:LABEL_MAX]
        p = await store.get_player(qq)
        if not p:
            return "你还没入道，先发「入道」再配置按钮。"
        try:
            n = set_button(p, card_key, label, cmd, allow_replace=False)
        except CustomButtonError as e:
            return "配置失败：%s" % e
        await store.put_player(p)
        return "已在【%s】加按钮「%s」→「%s」（现有 %d 个）。" % (
            CARD_LABELS.get(card_key, card_key), label, cmd, n)

    card_token, action, payload = parse_config_arg(arg)
    if not card_token or action == "help":
        return help_text()

    card_key = resolve_card(card_token)
    if not card_key:
        return "不认识「%s」这张卡。发「按钮设置」看可配卡片。" % card_token

    if action == "list":
        cfg = _store(await store.get_player(qq) or {})
        items = cfg.get(card_key, [])
        if not items:
            return "【%s】当前没有自定义按钮（用的默认按钮）。" % CARD_LABELS.get(card_key, card_key)
        lines = ["【%s】自定义按钮：" % CARD_LABELS.get(card_key, card_key)]
        lines += ["  %d. %s → %s" % (i, e["label"], e["cmd"])
                  for i, e in enumerate(items, 1)]
        return "\n".join(lines)

    p = await store.get_player(qq)
    if not p:
        return "你还没入道，先发「入道」再配置按钮。"

    try:
        if action == "add":
            label, cmd = payload.split("=", 1)
            n = set_button(p, card_key, label, cmd)
            await store.put_player(p)
            return "已加【%s】→「%s」（%s 现有 %d 个按钮）。" % (
                label, cmd, CARD_LABELS.get(card_key, card_key), n)
        if action == "del":
            label = payload.strip()
            if not label:
                return "要删哪个按钮？如「按钮设置 宝库 -血灵草」。"
            ok = delete_button(p, card_key, label)
            if not ok:
                return "【%s】没有叫「%s」的按钮。" % (CARD_LABELS.get(card_key, card_key), label)
            await store.put_player(p)
            return "已删【%s】的「%s」按钮。" % (CARD_LABELS.get(card_key, card_key), label)
        if action == "clear":
            ok = clear_buttons(p, card_key)
            if not ok:
                return "【%s】本来就没有自定义按钮。" % CARD_LABELS.get(card_key, card_key)
            await store.put_player(p)
            return "已清空【%s】的自定义按钮，恢复默认。" % CARD_LABELS.get(card_key, card_key)
    except CustomButtonError as e:
        return "配置失败：%s" % e
    return "看不懂这个配置。发「按钮设置」看用法。"
