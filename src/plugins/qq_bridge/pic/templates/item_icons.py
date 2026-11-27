# -*- coding: utf-8 -*-
"""斗破物品图标（丹药 / 异火）—— 资源定位 + 最近邻缩放，pic 层零插件依赖。

为什么单独成模块
----------------
物品详情卡 ``doupo_item`` 和背包网格 ``doupo_list`` 都要贴丹药/异火图标，而这两个
模板都在 ``pic`` 包里、**不能 import 插件层**（``doupo.config`` 里有 nonebot 依赖）。
所以图标只按「游戏内名称」直接查本目录下的 PNG，名称即文件名，不做前缀解析——
落盘时已用游戏真源的名称重命名（见 ``assets/doupo_item_icons`` 的放行规则）。

素材来源与红线
--------------
109 张（丹药 83 + 异火 26）全部来自游戏美术素材的同风格重制，**未重绘、未改配色**，
原件在 ``D:/AI/斗破丹药异火图鉴``。本目录是运行必需的一份副本（约 1.36MB）。
放大一律 ``Image.NEAREST``（像素图平滑插值会糊成一团，这是马赛克风的底线）；
缩小时改走 ``LANCZOS``，理由见 :func:`_scaled`。

缓存
----
``lru_cache`` 以 ``(name, box_w, box_h)`` 为键缓存缩放后的 RGBA 图层。卡片渲染是
同步 Pillow，同一张卡里同一物品可能出现多次（背包 + 详情），缓存避免重复解码。
"""
from __future__ import annotations

import os
from functools import lru_cache

from PIL import Image

#: 图标目录（与 doupo_rank_icons 同级，随源码交付）
ICON_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "assets", "doupo_item_icons")


def icon_path(name: str | None) -> str | None:
    """游戏内名称 → 图标文件绝对路径；无此项返回 None（调用方回退纯文字）。"""
    n = str(name or "").strip()
    if not n:
        return None
    p = os.path.join(ICON_DIR, n + ".png")
    return p if os.path.isfile(p) else None


def has_icon(name: str | None) -> bool:
    """有没有这张图（供数据层决定是否声明 image 字段）。"""
    return icon_path(name) is not None


@lru_cache(maxsize=256)
def _scaled(name: str, box_w: int, box_h: int) -> Image.Image | None:
    """按框等比缩放到「不超过 box_w×box_h」的 RGBA 图层。

    插值分两种，**不能一刀切 NEAREST**：
      * 放大（``scale >= 1``）走 ``NEAREST`` —— 像素块放大才是马赛克风，平滑插值会糊；
      * 缩小（``scale < 1``）走 ``LANCZOS`` —— 源图分辨率差得极远（丹药 27~43px，
        异火 86~168px 的细颗粒像素画），拿 NEAREST 缩到 40~60px 会**整列整行丢像素**，
        龙形/焰舌直接断成碎点；LANCZOS 只是稍软，形状还在。实测对照见
        ``tmp_ic/scale.png``（同日渲染）。缩到接近 1:1 时两者几乎无差别。
    """
    path = icon_path(name)
    if not path:
        return None
    try:
        with Image.open(path) as im:
            src = im.convert("RGBA")
    except Exception:  # noqa: BLE001 — 坏图不许拖垮整张卡
        return None
    w, h = src.size
    if w <= 0 or h <= 0:
        return None
    scale = min(box_w / w, box_h / h)
    nw = max(1, int(round(w * scale)))
    nh = max(1, int(round(h * scale)))
    if (nw, nh) == (w, h):
        return src
    resample = Image.NEAREST if scale >= 1 else Image.LANCZOS
    return src.resize((nw, nh), resample)


def icon_layer(name: str | None, box_w: int, box_h: int) -> Image.Image | None:
    """取等比缩放后的图标图层；缺图返回 None。"""
    n = str(name or "").strip()
    if not n:
        return None
    return _scaled(n, max(1, int(box_w)), max(1, int(box_h)))


def paste_icon(c, name: str | None, cx: int, cy: int,
               box_w: int, box_h: int) -> bool:
    """在画布 ``(cx, cy)`` 居中贴图标；成功返回 True，缺图 False。

    ``Canvas`` 没有公开的 ``.image``，统一走 ``paste_alpha``（真 alpha 合成 + 越界安全）。
    """
    layer = icon_layer(name, box_w, box_h)
    if layer is None:
        return False
    w, h = layer.size
    c.paste_alpha(layer, (int(cx) - w // 2, int(cy) - h // 2))
    return True
