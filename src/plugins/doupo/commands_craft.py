# -*- coding: utf-8 -*-
"""斗破苍穹 · 炼药命令组（2026-11-27 从 commands.py 拆出）。

为什么独立成模块
----------------
``commands.py`` 逼近 200KB 上传红线（204800 B，工作区仅余 198 B）。本组 4 个函数、
约 160 行、丹雷接线在内约 7KB，块外只被 matcher/测试经 ``commands.cmd_craft`` 引用，
是最干净的切分点。与 ``commands_puppet.py`` 同构。

兼容性（红线）
--------------
* ``commands.py`` 末尾 re-export 同名函数 —— ``__init__.py`` matcher、
  ``button_dispatch``、既有测试一律仍走 ``commands.cmd_craft``。
* ``_player`` / ``NOT_ENTERED`` / ``_chance_lines`` / ``_detail_of`` / ``cfg`` /
  ``core`` / ``txt`` / ``cctx`` 通过 ``_cmds.xxx`` 运行时取，循环导入下安全。

丹雷接线（2026-11-27）
----------------------
单发/批量在调 ``core.craft`` 前用 ``danlei_cmd.has_puppet(qq)`` 异步读出战傀儡传入；
成功后在回执追加 ``danlei_cmd.finish(qq,res)``（挡下则淬炼落库 + 出文案）。
低阶丹 res 无 danlei 键，两行接线对老玩家逐字节无输出变化。
"""
from __future__ import annotations

from . import commands as _cmds


def _parse_craft_count(arg: str) -> tuple[str, int]:
    """从「炼制」参数里拆出 (丹名片段, 炉数)。"""
    import re
    s = (arg or "").strip()
    m = re.search(r"[×xX*]\s*(\d+)\s*$", s)
    if m:
        return s[:m.start()].strip(), max(1, int(m.group(1)))
    m = re.search(r"\s+(\d+)\s*(?:次|炉|个|副)?\s*$", s)
    if m and int(m.group(1)) > 1:
        return s[:m.start()].strip(), max(1, min(int(m.group(1)), _cmds.cfg.CRAFT_BATCH_MAX))
    return s, 1


def _craft_flame_aid_line(p, success: bool = True) -> str:
    """炼药播报的护炉行：按玩家实有且真进成功率的火分组。"""
    from . import flame_bonus as _fb
    detail = _fb.alchemy_flame_names(p)
    xian = tuple(d["name"] for d in detail if d["identity"] == "异火")
    beast = tuple(d["name"] for d in detail if d["identity"] == "兽火")
    return _cmds.txt.craft_flame_aid(xian, beast, success=success)


async def cmd_craft(store, qq, name) -> str:
    p = await _cmds._player(store, qq)
    if not p:
        return _cmds.NOT_ENTERED
    name = (name or "").strip()
    if not name:
        return "用法：炼制 回灵丹（发「炼药」看全部丹方）"
    name, n_furnace = _parse_craft_count(name)
    if not name:
        return "用法：炼制 回灵丹 5次（一次连炼5炉）／炼制 回灵丹（单炉）"
    if n_furnace > 1:
        return await _cmd_craft_batch(store, qq, p, name, n_furnace)
    cfg = _cmds.cfg
    core = _cmds.core
    txt = _cmds.txt
    hit = next((n for n in cfg.RECIPE_NAMES if name in n or n in name), None)
    if not hit:
        return "无此丹方。发「炼药」看全部。"
    from . import danlei_cmd as _dcmd
    _pu = await _dcmd.has_puppet(qq)
    res = core.craft(p, hit, has_puppet=_pu)
    if res.get("reason") == "nofire":
        return "🔥 你尚未拥有火焰，无法开炉炼药——异火举世罕见，可于九星突破战胜强敌，或炼制兽火单方获得。"
    if res.get("reason") == "buyonly":
        return f"【{hit}】你手上没有此方，无法开炉——去「宝库」直接「买 {hit}」（{res['buy']} 金），或先寻得丹方。"
    if res.get("reason") == "cd":
        await store.put_player(p)
        return txt.craft_cd(res["left"])
    # D2：带丹名，别的丹不能凑数。
    chance = core.advance_task(p, "craft", 1, item=hit) if res.get("ok") else None
    await store.put_player(p)
    # 越阶提示：只有真越阶（diff>0）才提示，且只在失败时提示。
    over = max(0, int(res.get("diff") or 0))
    over_hint = txt.craft_over(over) if over else ""
    if not res.get("ok"):
        if res.get("reason") == "mat":
            _cmds.cctx.arm_craft(p, hit, 1)
            return txt.craft_mat("、".join(res["missing"]), 1)
        if res.get("reason") in ("fail",):
            msg = txt.craft_fail(res["dross"], cfg.rate_cn(res["rate"]), over_hint,
                                 seed=int(res["dross"]))
            aid = _craft_flame_aid_line(p, success=False)
            if aid:
                msg += "\n" + aid
            if res.get("alch_up"):
                msg += "\n" + txt.alch_up(res["alch_up"])
            return msg
        return txt.craft_fail_plain()
    _grade = str(cfg.RECIPE_BY_NAME.get(hit, {}).get("grade") or "")
    msg = txt.craft_success(hit, _grade, cfg.rate_cn(res["rate"]))
    aid = _craft_flame_aid_line(p)
    if aid:
        msg += "\n" + aid
    if res.get("danwen"):
        msg += "\n" + txt.craft_danwen(hit, 1)
    if res.get("alch_up"):
        msg += "\n" + txt.alch_up(res["alch_up"])
    # 丹雷（2026-11-27）：挡下则淬炼落库 + 追加文案；低阶无 danlei 键不产出。
    if res.get("danlei"):
        _dl_line = await _dcmd.finish(qq, res)
        if _dl_line:
            msg += "\n" + _dl_line
    out = [msg]
    out.extend(_cmds._chance_lines(chance, qq))
    _cmds.cctx.arm_done(p, hit, 1)
    return "\n".join(out)


async def _cmd_craft_batch(store, qq, p, name, n) -> str:
    """批量连炼回包（一条消息，纯中文成数）。"""
    cfg = _cmds.cfg
    core = _cmds.core
    txt = _cmds.txt
    hit = next((nm for nm in cfg.RECIPE_NAMES if name in nm or nm in name), None)
    if not hit:
        return "无此丹方。发「炼药」看全部。"
    from . import danlei_cmd as _dcmd
    _pu = await _dcmd.has_puppet(qq)
    res = core.craft_batch(p, hit, n, has_puppet=_pu)
    if res.get("reason") == "nofire":
        return "🔥 你尚未拥有火焰，无法开炉炼药——可于九星突破得火，或炼制兽火单方。"
    if res.get("reason") == "buyonly":
        return f"【{hit}】你手上没有此方，无法开炉——去「宝库」直接「买 {hit}」。"
    if res.get("reason") == "cd":
        await store.put_player(p)
        return txt.craft_cd(res["left"])
    if not res.get("ok"):
        if res.get("reason") == "mat" and res.get("missing"):
            _cmds.cctx.arm_craft(p, hit, n)
            return txt.craft_mat("、".join(res["missing"]), n)
        if res.get("reason") == "mp":
            return txt.craft_mp()
        return txt.craft_generic_fail()

    chance = core.advance_task(p, "craft", int(res["n_ok"]),
                               item=hit) if res["n_ok"] else None
    await store.put_player(p)

    lines = [txt.craft_batch_head(hit, res["n_done"], res["n_ok"], res["n_fail"],
                                  cfg.rate_cn(res["rate"]))]
    aid = _craft_flame_aid_line(p, success=(int(res.get("n_fail", 0)) == 0))
    if aid:
        lines.append(aid)
    detail = txt.craft_batch_detail(res["n_ok"], res["dross"], res["exp"])
    if res.get("exp_factor", 1.0) < 1.0:
        detail += f"（低阶丹，经验{cfg.rate_cn(res['exp_factor'])}计）"
    lines.append(detail)
    if res.get("n_danwen"):
        lines.append(txt.craft_danwen(hit, int(res["n_danwen"])))
    lines.append(f"　斗气 -{res['mp_used']}（余{res['mp_left']}）")
    if res.get("limited") in ("mat", "mp"):
        lines.append(txt.craft_batch_limited(res["limited"]))
        if res["limited"] == "mat" and res.get("gaps"):
            _cmds.cctx.arm_craft(p, hit, n)
            lines.append(txt.craft_need(_cmds._detail_of(res["gaps"]), n))
    if res.get("alch_up"):
        lines.append(txt.alch_up(res["alch_up"]))
    if res.get("n_ok") and res.get("limited") != "mat":
        _cmds.cctx.arm_done(p, hit, n)
        if res.get("danlei"):
            _dl_line = await _dcmd.finish(qq, res)
            if _dl_line:
                lines.append(_dl_line)
    out = ["\n".join(lines)]
    out.extend(_cmds._chance_lines(chance, qq))
    return "\n".join(out)
