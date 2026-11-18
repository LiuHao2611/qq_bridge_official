# -*- coding: utf-8 -*-
"""异火发放 A 方案自检（2026-11-18）。exit 0 = 全绿。

口径（与 config.FACTION_FLAME_GRANT 同源）：
  * 只发原著有据、且落在可养四火内的三处「本宗异火」：蛇人族/迦南/丹塔；
  * 文案禁写「传承」（原著里这三处都不是门派传承，焚炎谷的传承之火不在可养表）；
  * 只在玩家一朵异火都没有时发放 → 老档可补发、转宗不能刷；
  * 未收服不可喂养（堵住 0→1 后门）。

运行：/opt/venv/bin/python src/plugins/doupo/test_doupo_flame_grant.py
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))
os.chdir(ROOT)
sys.path.insert(0, ROOT)

_TMP = tempfile.mkdtemp(prefix="doupo_flame_")
os.environ["DOUPO_DATA_PATH"] = os.path.join(_TMP, "doupo.json")
os.environ["GAME_MODE_PATH"] = os.path.join(_TMP, "game_mode.json")
os.environ["QQ_LOG_DIR"] = os.path.join(_TMP, "logs")
os.environ["QQ_DATA_DIR"] = os.path.join(_TMP, "qdata")

import nonebot  # noqa: E402

nonebot.init(driver="~fastapi", onebot_access_token="e2e", log_level="CRITICAL")
from nonebot.adapters.onebot.v11 import Adapter  # noqa: E402

nonebot.get_driver().register_adapter(Adapter)
nonebot.load_plugin("src.plugins.doupo")

import importlib  # noqa: E402

from src.plugins.doupo import commands as cmds  # noqa: E402
from src.plugins.doupo import config as cfg  # noqa: E402
from src.plugins.doupo import core  # noqa: E402
from src.plugins.doupo import factions as fac  # noqa: E402

store_mod = importlib.import_module("src.plugins.doupo.store")

_RESULTS: list[tuple[str, bool, str]] = []


def check(name, ok, detail=""):
    _RESULTS.append((name, bool(ok), str(detail)))


def owned(p):
    return {k: int(v) for k, v in (p.get("flames") or {}).items() if int(v or 0) > 0}


def find_start_qq(fid):
    """真跑 create_player 找一个落在指定新手宗门的 qq（不复制随机逻辑）。"""
    for qq in range(10001, 16000):
        if core.create_player(str(qq), "probe", 900000077).get("faction") == fid:
            return str(qq)
    return ""


async def main():
    store = store_mod.Store(os.path.join(_TMP, "doupo.json"))
    await store.load()

    # ---- 1. 映射表本身有据 ----
    bad = [k for k in cfg.FACTION_FLAME_GRANT
           if k not in fac.FACTIONS or cfg.FACTION_FLAME_GRANT[k] not in cfg.FLAME_NAMES]
    check("映射只指向真实宗门与可养异火", not bad, str(bad))
    check("映射不含帝炎（帝炎只认斗帝）",
          cfg.FLAME_DIYAN not in cfg.FACTION_FLAME_GRANT.values(), "")
    check("焚炎谷传承之火不在可养表故未映射",
          "fenyangu" not in cfg.FACTION_FLAME_GRANT, "")

    # ---- 2. 入道发放 ----
    qq = find_start_qq("sherenzu")
    check("能构造蛇人族新号（种子稳定）", bool(qq), qq)
    p = core.create_player(qq, "火主", 900000077)
    check("蛇人族入道即得青莲地心火",
          p["faction"] == "sherenzu" and owned(p).get("青莲地心火") == 1, str(owned(p)))

    qq2 = find_start_qq("xiaojia")
    p2 = core.create_player(qq2, "无火", 900000077)
    check("无对应所藏的宗门不发火",
          p2["faction"] == "xiaojia" and not owned(p2), str(owned(p2)))

    # ---- 3. 幂等 / 防刷 ----
    before = owned(p)["青莲地心火"]
    again = core.grant_faction_flame(p)
    check("重复发放幂等（不会 1→2）",
          again == "" and owned(p)["青莲地心火"] == before, str(owned(p)))

    p3 = dict(p)
    p3["faction"] = "jianan"
    p3["faction_name"] = fac.FACTIONS["jianan"]["name"]
    got = core.grant_faction_flame(p3)
    check("已有异火者转宗不再发（防刷满三朵）",
          got == "" and owned(p3) == {"青莲地心火": 1}, str(owned(p3)))

    # ---- 4. 老档补发：全 0 玩家转去迦南 ----
    legacy = core.create_player(qq2, "老档", 900000077)
    legacy["flames"] = {n: 0 for n in cfg.FLAME_NAMES}
    legacy["lv"] = 40
    legacy["gold"] = 999999
    res = core.change_faction(legacy, "jianan")
    check("老档全 0 转宗补发本宗异火",
          res.get("ok") and res.get("flame") == "陨落心炎"
          and owned(legacy) == {"陨落心炎": 1}, str(res))

    # ---- 5. 未收服不可养 ----
    q = core.create_player("12001", "喂火", 900000077)
    q["flames"] = {n: 0 for n in cfg.FLAME_NAMES}
    core.add_item(q, "药渣", 500)
    r = core.feed_flame(q, "净莲妖火")
    check("未收服喂养被拒", r.get("ok") is False and r.get("reason") == "unowned",
          str(r))
    check("被拒后不扣药渣", core.count_item(q, "药渣") == 500,
          str(core.count_item(q, "药渣")))

    q["flames"]["青莲地心火"] = 1
    r2 = core.feed_flame(q, "青莲地心火")
    check("已收服可正常喂养", r2.get("ok") is True, str(r2)[:120])

    # ---- 6. 命令层文案 ----
    qq3 = find_start_qq("sherenzu")
    txt = await cmds.cmd_enter(store, qq3, "入道火主", 900000077)
    check("入道回包说明本宗异火", "本宗异火" in txt and "青莲地心火" in txt,
          txt[:120])
    fed = await cmds.cmd_feed(store, qq3, "净莲妖火")
    check("养火拒绝时指向本宗异火", "尚未收服" in fed and "青莲地心火" in fed,
          fed[:120])
    p_after = await store.get_player(qq3)
    check("命令回包写入后存档带火", owned(p_after).get("青莲地心火") == 1,
          str(owned(p_after)))

    # ---- 7. 最高声明：不加新玩家字段 ----
    base_keys = set(core.create_player("12002", "键", 900000077).keys())
    check("发放不新增玩家字段（无 grant 标记）",
          not any("flame_grant" in k or "flame_ver" in k for k in base_keys),
          str([k for k in base_keys if "flame" in k]))

    # ---- 8. 帝炎不受影响 ----
    q4 = core.create_player("12003", "斗帝", 900000077)
    check("帝炎仍只由 diyan 成就位表达",
          "帝炎" not in owned(q4) and q4.get("diyan") is False, "")


def _run():
    asyncio.run(main())
    ok = 0
    for name, passed, detail in _RESULTS:
        print(("PASS  " if passed else "FAIL  ") + name +
              ("" if passed else "  -> " + detail[:200]))
        ok += 1 if passed else 0
    print("=" * 70)
    print("FLAME_TOTAL=%d PASSED=%d FAILED=%d" %
          (len(_RESULTS), ok, len(_RESULTS) - ok))
    return 0 if ok == len(_RESULTS) else 1


if __name__ == "__main__":
    sys.exit(_run())
