# -*- coding: utf-8 -*-
"""斗破/修仙 双通道兼容层回归（2026-11-16 官方通道「发斗破菜单没回」故障）。

线上根因（证据链）：
  * 日志：``Event will be handled by Matcher(module=src.plugins.doupo, lineno=137)``
    → ``running complete``，**没有任何 official_sent 记录、没有异常栈**；
  * 斗破 handler 注解原为 ``onebot.v11.MessageEvent``，QQ 官方事件
    ``GroupMessageCreateEvent`` 在 NoneBot 依赖注入阶段抛 TypeMisMatch
    (SkippedException) → handler 被静默跳过；
  * 发图原用 onebot ``MessageSegment.image(bytes)``，官方 Bot 不认该段。

本测试锁死修复后的契约：
  1. handler 注解必须是 ``nonebot.adapters.Event``（基类），不能再是 OneBot 具体类；
  2. 兼容层 EvView 能正确从官方事件取 openid / group_openid / 纯文本；
  3. image_seg 在官方通道返回 ``file_image``（必须带 file_name）；
  4. ``_mode_is`` 的 rule 对官方事件也能正确判模式（不再 TypeMisMatch→False）。
"""
from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src import game_compat as gc  # noqa: E402


# ---------------------------------------------------------------------------
# 1. 兼容层单元：用「假官方事件」验证取值（不依赖 nonebot-adapter-qq 的具体模型）
# ---------------------------------------------------------------------------
class _Author:
    def __init__(self, member_openid="OPENID_MEMBER", user_openid=None, username="测试道友"):
        self.member_openid = member_openid
        self.user_openid = user_openid
        self.username = username
        self.id = "AUTHOR_ID"


class _Mention:
    def __init__(self, openid, bot=False, is_you=False):
        self.member_openid = openid
        self.id = openid
        self.bot = bot
        self.is_you = is_you


class FakeQQGroupEvent:
    """鸭子类型的 QQ 官方群事件；模块名伪装成 nonebot.adapters.qq.* 。"""

    def __init__(self, text="斗破菜单", mentions=None, self_id="BOT_SELF"):
        self.author = _Author()
        self.group_openid = "GROUP_OPENID_1"
        self.mentions = mentions or []
        self.content = text
        self.self_id = self_id

    def get_plaintext(self):
        return self.content

    def get_message(self):
        return None


# 让 is_official 认它为官方事件
FakeQQGroupEvent.__module__ = "nonebot.adapters.qq.event"


class FakeNapCatEvent:
    def __init__(self, user_id=10001, group_id=20001, text="斗破菜单"):
        self.user_id = user_id
        self.group_id = group_id
        self.self_id = "999"
        self.message = []
        self.sender = types.SimpleNamespace(card="", nickname="老玩家")
        self._text = text

    def get_plaintext(self):
        return self._text


def test_is_official_distinguishes_channels():
    assert gc.is_official(FakeQQGroupEvent()) is True
    assert gc.is_official(FakeNapCatEvent()) is False


def test_uid_gid_official_vs_napcat():
    qq = FakeQQGroupEvent()
    assert gc.uid(qq) == "OPENID_MEMBER"
    assert gc.gid(qq) == "GROUP_OPENID_1"

    nb = FakeNapCatEvent()
    assert gc.uid(nb) == "10001"
    assert gc.gid(nb) == "20001"


def test_official_c2c_has_no_group():
    ev = FakeQQGroupEvent()
    ev.group_openid = ""  # C2C：没有群
    ev.author = _Author(member_openid="", user_openid="USER_OPENID")
    assert gc.gid(ev) is None
    assert gc.uid(ev) == "USER_OPENID"


def test_evview_plaintext_and_identity():
    v = gc.view(FakeQQGroupEvent(text="斗破菜单"))
    assert v.get_plaintext() == "斗破菜单"
    assert v.user_id == "OPENID_MEMBER"
    assert v.group_id == "GROUP_OPENID_1"
    assert v.official is True


def test_evview_mentions_filtered_to_at_segments():
    """官方 mentions → OneBot at 段；跳过机器人自己与发送者本人。"""
    me = "OPENID_MEMBER"
    v = gc.view(FakeQQGroupEvent(mentions=[
        _Mention("OTHER_1"),
        _Mention(me, is_you=True),      # 自己，跳过
        _Mention("BOT_OPENID", bot=True),  # 机器人，跳过
        _Mention("OTHER_2"),
    ]))
    segs = v.message
    assert [s.type for s in segs] == ["at", "at"]
    assert [s.data["qq"] for s in segs] == ["OTHER_1", "OTHER_2"]


def test_view_is_idempotent():
    v = gc.view(FakeQQGroupEvent())
    assert gc.view(v) is v


def test_napcat_view_passthrough():
    """NapCat 通道行为必须与改造前完全一致（原样返回 sender / message）。"""
    nb = FakeNapCatEvent()
    v = gc.view(nb)
    assert v.official is False
    assert v.sender is nb.sender
    assert v.message == []
    assert v.get_plaintext() == "斗破菜单"


# ---------------------------------------------------------------------------
# 2. 发图段：官方必须是 file_image 且带 file_name
# ---------------------------------------------------------------------------
def test_image_seg_official_uses_file_image():
    pytest.importorskip("nonebot.adapters.qq")
    seg = gc.image_seg(FakeQQGroupEvent(), b"\x89PNG_fake", file_name="doupo_menu.png")
    assert getattr(seg, "type", "") == "file_image"
    assert seg.data.get("file_name") == "doupo_menu.png"
    assert seg.data.get("content") == b"\x89PNG_fake"


def test_image_seg_napcat_uses_image():
    pytest.importorskip("nonebot.adapters.onebot.v11")
    seg = gc.image_seg(FakeNapCatEvent(), b"\x89PNG_fake")
    assert getattr(seg, "type", "") == "image"


# ---------------------------------------------------------------------------
# 3. 源码契约：斗破插件不得回退到 OneBot 具体事件注解
# ---------------------------------------------------------------------------
def test_doupo_handlers_annotated_with_base_event():
    src = (ROOT / "src/plugins/doupo/__init__.py").read_text(encoding="utf-8")
    assert "async def _(event: Event):" in src
    # 绝不能再用 OneBot 具体事件类型做 handler 注解（会让官方事件被 Skip）
    assert "async def _(event: MessageEvent)" not in src
    # 每个 handler 都必须先过兼容层
    assert src.count("async def _(event: Event):") == src.count("event = view(event)")
    # 发图必须走通道感知的 image_seg，不能直接 MessageSegment.image
    assert "MessageSegment.image" not in src
    assert "image_seg(event," in src


def test_doupo_pat_accepts_official_mention():
    """正则前缀必须同时吃 OneBot 的 [CQ:...] 与官方的 <@openid>。"""
    src = (ROOT / "src/plugins/doupo/__init__.py").read_text(encoding="utf-8")
    assert "<@[^>]*>" in src


def test_mode_rule_uses_compat_uid():
    """_mode_is 的 rule 必须走 view(...).user_id，否则官方事件取不到身份。"""
    src = (ROOT / "src/plugins/doupo/__init__.py").read_text(encoding="utf-8")
    assert "get_mode(str(view(event).user_id))" in src
