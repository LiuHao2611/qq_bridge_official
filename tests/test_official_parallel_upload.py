# -*- coding: utf-8 -*-
"""P1 守门：官方通道多图上传并行（docs/斗破卡片渲染性能_深度调研与优化方案 §5.3）。

三条红色的根因：
  1. 必须并发（不是 git 里旧的 for + await 串行）；
  2. markdown 里图片顺序必须严格等于输入顺序（gather 保序）；
  3. 任一上传失败依旧抛错，让 send() 走降级两条发送（不能静默吞）。

运行：python -m pytest tests/test_official_parallel_upload.py -q
"""
from __future__ import annotations

import asyncio
import ast
from pathlib import Path

from src.plugins.qq_bridge.channel import official

SRC = Path(official.__file__).read_text(encoding="utf-8")


class FakeMsg:
    def __init__(self):
        self.parts: list = []

    def __iadd__(self, item):
        self.parts.append(item)
        return self


class FakeSeg:
    @staticmethod
    def markdown(text):
        return ("markdown", text)

    @staticmethod
    def keyboard(model):
        return ("keyboard", model)


def _fn_src(name: str) -> str:
    tree = ast.parse(SRC)
    fn = next((n for n in tree.body
               if isinstance(n, ast.AsyncFunctionDef) and n.name == name), None)
    assert fn is not None, "找不到 %s" % name
    return "\n".join(ast.unparse(n) for n in ast.walk(fn))


def test_source_uses_gather_and_keeps_order():
    blob = _fn_src("_build_merged_card_segment")
    assert "asyncio.gather" in blob, "上传没有并行（回退成串行 for+await）"
    assert "for i, data in enumerate(image_datas)" in blob
    # 必须在 gather 之后才拼 parts，否则保序无从谈起
    g = blob.index("asyncio.gather")
    p = blob.index("markdown_image", g + 1) if "markdown_image" in blob[g:] else -1
    assert p > g, "parts 拼接必须在 gather 之后，才能保持输入顺序"


def test_gather_runs_concurrently_and_preserves_order(monkeypatch):
    entered: list[int] = []
    release = asyncio.Event()
    started = asyncio.Event()
    calls = 0

    async def fake_ensure(bot, data, file_name=""):
        nonlocal calls
        idx = int(file_name.split("_")[-1].split(".")[0])
        entered.append(idx)
        calls += 1
        if calls == 3:
            started.set()          # 三张全部进入 → 证明不是串行
        await release.wait()
        return {"url": "u%d" % idx, "width": 10 + idx, "height": 20 + idx}

    monkeypatch.setattr("src.plugins.qq_bridge.channel.media_asset.ensure_material",
                        fake_ensure, raising=True)
    # official.py 在函数体内 import，所以 monkeypatch 必须打在源模块上（已做）

    async def run():
        # 让 fake 在 gather 里真的被用到：函数体 import 的是同一模块对象
        task = asyncio.create_task(official._build_merged_card_segment(
            object(), [b"a", b"b", b"c"], "doupo:test", FakeMsg, FakeSeg))
        await asyncio.wait_for(started.wait(), timeout=1.0)  # 串行会在这里超时
        release.set()
        seg, _ = await task
        return seg

    seg = asyncio.run(run())
    assert sorted(entered) == [0, 1, 2]
    content = str(seg.parts)
    assert content.index("u0") < content.index("u1") < content.index("u2"), \
        "markdown 图片顺序与输入不一致"


def test_upload_failure_still_propagates(monkeypatch):
    from src.plugins.qq_bridge.channel.media_asset import MaterialUploadError

    async def bad(bot, data, file_name=""):
        raise MaterialUploadError("network")

    monkeypatch.setattr("src.plugins.qq_bridge.channel.media_asset.ensure_material",
                        bad, raising=True)

    async def run():
        return await official._build_merged_card_segment(
            object(), [b"a", b"b"], "doupo:test", FakeMsg, FakeSeg)

    try:
        asyncio.run(run())
    except MaterialUploadError:
        return
    raise AssertionError("上传失败被吞了 —— send() 将无法降级两条发送")
