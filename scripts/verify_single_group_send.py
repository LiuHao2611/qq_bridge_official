"""机器验收：机器人只允许向 GROUP_WHITELIST 里的那一个群发消息。

背景（2026-11-10 事故）：
  GROUP_WHITELIST 原本只在 dispatcher（priority=10）生效，而 doupo / xiuxian
  是 priority=5 + block=True，抢先消费群消息。日志实证 17:49:07 非白名单群
  的 `dj` 口令 → 17:49:11 `media_sent action=send_group_msg msg_len=288795`，
  一张 288KB 图直接发到了不该发的群。
  修复把闸放到框架层：Bot.call_api 常驻出站闸 + event_preprocessor 入站收窄，
  与插件优先级、消息类型（文字 / 图片 / 转发 / 公告）全部无关。

本脚本不 mock 判定逻辑本身，只 mock 底层 call_api，验证：
  A 组·纯函数：白名单解析、fail-closed、非群操作不受管辖
  B 组·wrapper：允许群放行 / 其它群拦 / 私聊与管理操作放行 / 总闸与空配置
  C 组·接线：bot.py 无条件装闸、media 不写假历史、面板手发前置校验
  D 组·现网 .env：GROUP_WHITELIST 必须恰好一个群号（可选 --env）

测试群号一律 9xxxxxxx 假号（AGENTS.md §51），禁止复用任何真实 QQ / 群号。

用法：
    .venv\\Scripts\\python.exe scripts\\verify_single_group_send.py
    .venv\\Scripts\\python.exe scripts\\verify_single_group_send.py --env D:/AI/manju_agent/qq_bridge/.env
退出码：0 = 全部 PASS；1 = 有 FAIL。
"""
from __future__ import annotations

import asyncio
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from src.group_switch import (  # noqa: E402
    blocked_reason,
    group_send_target,
    group_send_whitelist,
    install_outbound_guard,
    is_blocked_group_send,
    is_group_send_allowed,
)

_ALLOWED = 900000001   # 假群号：唯一被允许的群
_OTHER = 900000002     # 假群号：不该收到任何消息的群
_USER = 900000003      # 假用户号


def _env(wl: str, enabled: str = "true") -> None:
    os.environ["GROUP_WHITELIST"] = wl
    os.environ["GROUP_ENABLED"] = enabled


def _pure_checks() -> list[tuple[str, object, object]]:
    rows: list[tuple[str, object, object]] = []
    rows.append(("解析单群白名单", group_send_whitelist(str(_ALLOWED)) == {_ALLOWED}, True))
    rows.append((
        "解析中文逗号/空格/脏项",
        group_send_whitelist(" 900000001， x, 900000002 ") == {_ALLOWED, _OTHER},
        True,
    ))
    rows.append(("空配置 = 空集合（fail-closed）", group_send_whitelist("") == set(), True))
    rows.append(("目标群号可归一化", group_send_target("send_group_msg", {"group_id": str(_ALLOWED)}) == _ALLOWED, True))
    rows.append(("目标群号缺失 = None", group_send_target("send_msg", {"message_type": "group"}) is None, True))
    rows.append(("判定：send_group_msg 属群发送", is_blocked_group_send("send_group_msg", {"group_id": 1}), True))
    rows.append(("判定：get_group_list 不属群发送", is_blocked_group_send("get_group_list", {}), False))

    _env(str(_ALLOWED))
    rows.append(("白名单群放行", is_group_send_allowed("send_group_msg", {"group_id": _ALLOWED}), True))
    rows.append(("非白名单群拒绝", is_group_send_allowed("send_group_msg", {"group_id": _OTHER}), False))
    rows.append(("群发送无 group_id 拒绝", is_group_send_allowed("send_msg", {"message_type": "group"}), False))
    rows.append(("私聊不受管辖", is_group_send_allowed("send_private_msg", {"user_id": _USER}), True))
    rows.append(("管理操作不受管辖", is_group_send_allowed("set_group_ban", {"group_id": _OTHER}), True))
    rows.append(("总闸优先级高于白名单", blocked_reason("send_group_msg", {"group_id": _ALLOWED}), ""))
    _env(str(_ALLOWED), "false")
    rows.append(("GROUP_ENABLED=false 拦白名单群", blocked_reason("send_group_msg", {"group_id": _ALLOWED}), "group_disabled"))
    _env(str(_ALLOWED))
    rows.append(("非白名单群拦截原因", blocked_reason("send_group_msg", {"group_id": _OTHER}), "group_not_whitelisted"))
    return rows


async def _wrapper_checks() -> list[tuple[str, object, object]]:
    """只替换底层 call_api 为探针，装真闸，看是否触达底层。"""
    from nonebot.adapters.onebot.v11 import Bot as V11Bot

    async def one(wl: str, enabled: str, api: str, data: dict,
                  expect_blocked: bool, expect_reason: str = "") -> tuple[bool, str]:
        _env(wl, enabled)
        hits: list[tuple[str, dict]] = []

        async def fake(self, api_: str, **data_):
            hits.append((api_, data_))
            return {"message_id": 999}

        V11Bot.call_api = fake  # type: ignore[method-assign]
        V11Bot._qqbridge_outbound_guard = False  # type: ignore[attr-defined]
        install_outbound_guard()
        ret = await V11Bot.call_api(object(), api, **data)

        blocked = isinstance(ret, dict) and ret.get("blocked") is True
        if blocked != expect_blocked:
            return False, "blocked=%r expect=%r ret=%r" % (blocked, expect_blocked, ret)
        if blocked:
            if hits:
                return False, "被拦却触达了底层 API: %r" % (hits,)
            if expect_reason and ret.get("reason") != expect_reason:
                return False, "reason=%r expect=%r" % (ret.get("reason"), expect_reason)
        else:
            if len(hits) != 1 or ret.get("message_id") != 999:
                return False, "放行但未触达底层 API: hits=%r ret=%r" % (hits, ret)
        return True, ""

    A = str(_ALLOWED)
    cases = [
        ("允许群·文字", A, "true", "send_group_msg", {"group_id": _ALLOWED, "message": "灵化"}, False, ""),
        ("允许群·图片段", A, "true", "send_group_msg", {"group_id": _ALLOWED, "message": "[CQ:image,file=abc.jpg]"}, False, ""),
        ("允许群·大图 base64", A, "true", "send_group_msg", {"group_id": _ALLOWED, "message": "[CQ:image,file=base64://" + "A" * 1000 + "]"}, False, ""),
        ("允许群·转发消息", A, "true", "send_group_forward_msg", {"group_id": _ALLOWED, "messages": []}, False, ""),
        ("允许群·群公告", A, "true", "send_group_notice", {"group_id": _ALLOWED, "content": "x"}, False, ""),
        ("允许群·通用 send_msg", A, "true", "send_msg", {"group_id": _ALLOWED, "message": "x"}, False, ""),
        ("允许群·group_id 字符串", A, "true", "send_group_msg", {"group_id": str(_ALLOWED), "message": "x"}, False, ""),
        ("非白名单群·文字", A, "true", "send_group_msg", {"group_id": _OTHER, "message": "dj"}, True, "group_not_whitelisted"),
        ("非白名单群·图片", A, "true", "send_group_msg", {"group_id": _OTHER, "message": "[CQ:image,file=abc.jpg]"}, True, "group_not_whitelisted"),
        ("非白名单群·转发", A, "true", "send_group_forward_msg", {"group_id": _OTHER, "messages": []}, True, "group_not_whitelisted"),
        ("非白名单群·群公告", A, "true", "send_group_notice", {"group_id": _OTHER, "content": "x"}, True, "group_not_whitelisted"),
        ("非白名单群·send_msg+group_id", A, "true", "send_msg", {"group_id": _OTHER, "message": "x"}, True, "group_not_whitelisted"),
        ("非白名单群·只有 message_type", A, "true", "send_msg", {"message_type": "group", "message": "x"}, True, "group_not_whitelisted"),
        ("私聊·send_private_msg", A, "true", "send_private_msg", {"user_id": _USER, "message": "x"}, False, ""),
        ("私聊·send_msg+user_id", A, "true", "send_msg", {"user_id": _USER, "message": "x"}, False, ""),
        ("管理操作·get_group_list", A, "true", "get_group_list", {}, False, ""),
        ("管理操作·set_group_ban", A, "true", "set_group_ban", {"group_id": _OTHER, "duration": 60}, False, ""),
        ("群总闸关闭·白名单群也拦", A, "false", "send_group_msg", {"group_id": _ALLOWED, "message": "x"}, True, "group_disabled"),
        ("白名单为空·全拦（fail-closed）", "", "true", "send_group_msg", {"group_id": _ALLOWED, "message": "x"}, True, "group_not_whitelisted"),
    ]

    rows: list[tuple[str, object, object]] = []
    for label, wl, enabled, api, data, blocked, reason in cases:
        ok, detail = await one(wl, enabled, api, data, blocked, reason)
        rows.append((label, ok, True))
        if not ok:
            print("       ↳ %s" % detail)
    return rows


def _wiring_checks(env_path: str | None) -> list[tuple[str, object, object]]:
    rows: list[tuple[str, object, object]] = []

    bot_src = open(os.path.join(ROOT, "bot.py"), encoding="utf-8").read()
    lines = bot_src.splitlines()
    call_lines = [i for i, ln in enumerate(lines) if ln.strip() == "install_outbound_guard()"]
    top_level = any(not lines[i].startswith((" ", "\t")) for i in call_lines)
    rows.append(("bot.py 出站闸是顶层无条件调用", top_level, True))
    rows.append(("bot.py 入站白名单 preprocess 已注册", "_block_non_whitelist_groups" in bot_src, True))

    media_src = open(
        os.path.join(ROOT, "src", "plugins", "qq_bridge", "media", "__init__.py"),
        encoding="utf-8",
    ).read()
    rows.append(("media 层识别 blocked，不写假历史", "media_send_blocked" in media_src, True))

    main_src = open(
        os.path.join(ROOT, "src", "plugins", "qq_bridge", "__init__.py"),
        encoding="utf-8",
    ).read()
    rows.append(("面板手发前置白名单校验", "目标群不在 GROUP_WHITELIST 中" in main_src, True))
    rows.append(("面板手发识别 blocked 兜底", "出站闸拒绝发送" in main_src, True))

    if env_path:
        text = open(env_path, encoding="utf-8").read()
        vals = [
            ln.split("=", 1)[1].strip()
            for ln in text.splitlines()
            if ln.strip().startswith("GROUP_WHITELIST=") and not ln.strip().startswith("#")
        ]
        ids = [p.strip() for p in (vals[-1] if vals else "").replace("，", ",").split(",") if p.strip()]
        rows.append(("现网 .env 恰好一个群号且非空", len(ids) == 1 and ids[0].isdigit(), True))
        if len(ids) == 1:
            print("       现网 GROUP_WHITELIST = %s" % ids[0])
    return rows


def main() -> int:
    env_path = None
    if "--env" in sys.argv:
        idx = sys.argv.index("--env")
        if idx + 1 < len(sys.argv):
            env_path = sys.argv[idx + 1]

    rows: list[tuple[str, object, object]] = []
    rows += _pure_checks()
    rows += asyncio.run(_wrapper_checks())
    rows += _wiring_checks(env_path)

    fail = 0
    print("=" * 74)
    for label, actual, expect in rows:
        ok = actual == expect
        fail += 0 if ok else 1
        print("  %-5s %-40s actual=%-20r expect=%r"
              % ("PASS" if ok else "FAIL", label, actual, expect))
    print("=" * 74)
    print("%d PASS / %d FAIL" % (len(rows) - fail, fail))
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
