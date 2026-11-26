# -*- coding: utf-8 -*-
"""炼药「一次买齐材料」按钮闭环（2026-11-26 用户要求）。

## 用户原话（两段一起实现）

* 「查看丹药时，底部的按钮应该有全部材料的购买按钮，注意有的材料需要不止一个，
  必须要保证一次买够」→ 按钮派生在 ``channel/doupo_buttons.py`` 的
  :func:`craft_mat_buttons`（每味一颗、数量=单炉用量）；
* 「炼药时材料不够的提示底部按钮也应该直接加上对应的材料购买按钮，比如我一次
  炼制10个丹药，缺少的材料应该是10个丹药所需的材料，按钮可以一次买齐材料，
  保证我下次操作可以直接进行炼制」→ 本模块（缺口按炉数算 + 挂按钮）。

## 缺口 = 炉数 × 单炉用量 − 已有

批量连炼被药材卡住时，缺的绝不只是「下一炉」那点料：玩家明明想炼 10 炉，
只告诉他缺 3 份血灵草，他买回来还是只够 1 炉，得再发一遍口令再看一遍缺料。
所以缺口一律按**本次请求的炉数**算（:func:`core._mat_short` 唯一入口）。

## @ 上下文尾巴

按钮 data 形如 ``doupo:cbtn:买 血灵草 30 @茯苓青丹 10``。``@`` 之后不是玩家该
手打的字面指令，而是本作的**安全上下文**：``cmd_buy`` 先由
:func:`split_ctx` 剥掉尾巴，买完再据此重算剩余缺口，把「还缺的药材 + 炼制此丹」
挂回回执——点完一味，下一味就在手边，最后一下直接开炉，全程不用手打。
玩家自己打的 ``买 血灵草 3`` 没有尾巴，行为与以前逐字一致。

## 按钮失败绝不阻断回执

按钮是增强项：通道模块缺失（纯业务单测 / NapCat）、arm 抛异常，一律静默跳过，
回执正文照发。与 ``commands_puppet._arm_deploy_buttons`` 同一纪律。
"""
from __future__ import annotations

import re

from . import config as cfg
from . import core

#: ``@<丹名> <炉数>`` 尾巴（丹名不含空格，炉数可省）。
_CTX_RE = re.compile(r"\s*@(\S+)(?:\s+(\d+))?\s*$")


def split_ctx(args: str) -> tuple[str, str, int]:
    """从「买」的参数里剥出 ``@<丹名> <炉数>`` → ``(净参数, 丹名, 炉数)``。

    没尾巴就原样返回、丹名空串、炉数 1——手打口令的语义一个字节都不变。
    """
    s = str(args or "").strip()
    m = _CTX_RE.search(s)
    if not m:
        return s, "", 1
    dan = m.group(1)
    n = max(1, min(int(m.group(2) or 1), int(cfg.CRAFT_BATCH_MAX)))
    return s[:m.start()].strip(), dan, n


def mat_gaps(p, dan: str, furnace: int = 1) -> list[dict]:
    """某丹药按 ``furnace`` 炉算的缺口明细（``{mat, need, have, gap}``，只列缺的）。

    与 ``core.craft`` / ``core.craft_batch`` 回包的 ``gaps`` 同一形状、同一口径：
    都走 ``core._mat_short``，`单炉用量 × 炉数 − 已有`，绝不各算一套。
    """
    rec = cfg.RECIPE_BY_NAME.get(dan or "")
    if not rec or not rec.get("need"):
        return []
    _missing, gaps = core._mat_short(p, rec, furnace)
    return gaps


_DB = None


def _channel():
    """通道按钮层（缺失/异常时返 None：按钮是增强项，不能拖垮回执）。

    两条路都返回**同一个模块实例**——按钮靠 ContextVar 传递，两份实例等于两个
    独立的 ContextVar，arm 了也白搭（点了没按钮）。所以：
      1. 生产：包内正常 import；
      2. 宿主/无 httpx（纯标准库单测）：按文件加载，且优先复用
         ``sys.modules["doupo_buttons"]``（与 tests/test_doupo_buttons.py 同源）。
    """
    global _DB
    if _DB is not None:
        return _DB
    try:
        from src.plugins.qq_bridge.channel import doupo_buttons as _db  # noqa: PLC0415

        _DB = _db
        return _DB
    except Exception:  # noqa: BLE001 — 落到文件加载兜底
        pass
    try:
        import importlib.util  # noqa: PLC0415
        import sys as _sys  # noqa: PLC0415
        from pathlib import Path as _Path  # noqa: PLC0415

        _db = _sys.modules.get("doupo_buttons")
        if _db is None:
            _file = _Path(__file__).resolve().parents[1] / "qq_bridge" / "channel" / "doupo_buttons.py"
            _spec = importlib.util.spec_from_file_location("doupo_buttons", _file)
            if _spec is None or _spec.loader is None:
                return None
            _db = importlib.util.module_from_spec(_spec)
            _sys.modules["doupo_buttons"] = _db
            _spec.loader.exec_module(_db)
        _DB = _db
        return _DB
    except Exception:  # noqa: BLE001 — 无通道环境（单测）静默跳过
        return None


def _arm(card_key: str, buttons) -> None:
    db = _channel()
    if db is None or not buttons:
        return
    try:
        db.arm_dynamic(card_key, tuple(buttons))
    except Exception:  # noqa: BLE001 — 同上
        return


def arm_materials(card_key: str, dan: str, gaps: list[dict], furnace: int = 1,
                  tail: bool = True) -> None:
    """把「每味材料一次买齐 + 炼制此丹（+ 炼药）」挂到本回合某张卡的按钮上。"""
    db = _channel()
    if db is None:
        return
    try:
        pairs = [(g["mat"], int(g["gap"])) for g in (gaps or []) if int(g["gap"]) > 0]
        btns = list(db.craft_mat_buttons(dan, pairs, furnace))
        if tail:
            btns.append(db.Btn("炼药", "炼药", False))
        _arm(card_key, btns)
    except Exception:  # noqa: BLE001 — 同上
        return


def arm_craft(p, dan: str, furnace: int = 1) -> list[dict]:
    """炼药回执缺料/见底：算缺口 → 挂按钮 → 返回缺口（供正文列明细）。"""
    gaps = mat_gaps(p, dan, furnace)
    arm_materials("craft_text", dan, gaps, furnace)
    return gaps


def arm_buy(p, dan: str, furnace: int = 1) -> None:
    """买完药材的回执：重算剩余缺口 → 「还缺的药材 + 炼制此丹」再挂一遍。

    这是「点完一味，下一味在手边」的那一半；缺口清零时只剩「炼制此丹」，
    点下去就是整批开炉（炉数 >1 时按钮带炉数）。
    """
    arm_materials("buy_text", dan, mat_gaps(p, dan, furnace), furnace)


def arm_done(p, dan: str, furnace: int = 1) -> None:
    """炼成回执：服丹 / 查看 / 再炼 / 炼药 —— 炼出来最自然的下一步是吃掉。"""
    db = _channel()
    if db is None or not dan:
        return
    try:
        n = max(1, int(furnace))
        pre = db.DOUPO_CUSTOM_PREFIX
        btns = [
            db.Btn(db._fit_label("服丹", dan), pre + "服丹 " + dan, False),
            db.Btn(db._fit_label("查看", dan), pre + "查看 " + dan, False),
        ]
        btns.extend(db.craft_mat_buttons(dan, (), n))       # 只剩「炼制此丹」
        btns.append(db.Btn("炼药", "炼药", False))
        _arm("craft_text", btns)
    except Exception:  # noqa: BLE001 — 同上
        return
