# -*- coding: utf-8 -*-
"""异火发放自检（2026-11-18 A 方案 → 2026-11-19 三层投放重构）。exit 0 = 全绿。

口径（与 config.STARTER_FLAME / FLAME_TIER 同源）：
  * 入道/转宗只发一朵 B 线数值 starter（火云水炎），不再白送青莲/陨落/三千三朵超标火；
  * 青莲=A线隐藏彩蛋、陨落/三千/净莲/帝炎=S线大后期，见 test_flame_tiers.py；
  * 只在玩家一朵异火都没有时发放 → 老档可补发、转宗不能刷；
  * 未收服不可喂养（堵住 0→1 后门）；
  * 卡面/文本「用法」指向玩家真能养的那一朵。

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

from src.plugins.doupo import card as card_mod  # noqa: E402
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

    # ---- 1. starter 配置本身有据 ----
    starter = cfg.STARTER_FLAME
    check("starter 登记在可持有表", starter in cfg.OWNED_FLAME_NAMES, starter)
    check("starter 是 B 线数值火", cfg.FLAME_TIER.get(starter) == "B", starter)
    check("starter 在 BONUS_FLAMES 有数值定义", starter in cfg.BONUS_FLAME_BY_NAME, starter)
    check("不再有按宗门发 S/A 的旧映射", not cfg.FACTION_FLAME_GRANT,
          str(cfg.FACTION_FLAME_GRANT))

    # ---- 2. 入道发放：任何宗门都只发 starter ----
    qq = find_start_qq("sherenzu")
    check("能构造蛇人族新号（种子稳定）", bool(qq), qq)
    p = core.create_player(qq, "火主", 900000077)
    check("蛇人族入道即得 starter",
          p["faction"] == "sherenzu" and owned(p).get(starter) == 1, str(owned(p)))
    check("入道不发超标 S/A 火",
          not any(cfg.FLAME_TIER.get(n) in ("S", "A") for n in owned(p)), str(owned(p)))

    qq2 = find_start_qq("xiaojia")
    p2 = core.create_player(qq2, "无火", 900000077)
    check("任何宗门都发 starter（不再有「无火宗门」）",
          p2["faction"] == "xiaojia" and owned(p2).get(starter) == 1, str(owned(p2)))

    # ---- 3. 幂等 / 防刷 ----
    before = owned(p)[starter]
    again = core.grant_faction_flame(p)
    check("重复发放幂等（不会 1→2）",
          again == "" and owned(p)[starter] == before, str(owned(p)))

    p3 = dict(p)
    p3["faction"] = "jianan"
    p3["faction_name"] = fac.FACTIONS["jianan"]["name"]
    got = core.grant_faction_flame(p3)
    check("已有异火者转宗不再发（防刷）",
          got == "" and owned(p3) == {starter: 1}, str(owned(p3)))

    # ---- 4. 老档补发：全 0 玩家转宗 ----
    legacy = core.create_player(qq2, "老档", 900000077)
    legacy["flames"] = {n: 0 for n in cfg.OWNED_FLAME_NAMES}
    legacy["lv"] = 40
    legacy["gold"] = 999999
    res = core.change_faction(legacy, "jianan")
    check("老档全 0 转宗补发 starter",
          res.get("ok") and res.get("flame") == starter
          and owned(legacy) == {starter: 1}, str(res))

    # ---- 5. 未收服不可养 ----
    q = core.create_player("12001", "喂火", 900000077)
    q["flames"] = {n: 0 for n in cfg.OWNED_FLAME_NAMES}
    core.add_item(q, "药渣", 500)
    r = core.feed_flame(q, "净莲妖火")
    check("未收服喂养被拒", r.get("ok") is False and r.get("reason") == "unowned",
          str(r))
    check("被拒后不扣药渣", core.count_item(q, "药渣") == 500,
          str(core.count_item(q, "药渣")))

    q["flames"][starter] = 1
    r2 = core.feed_flame(q, starter)
    check("已收服 B 线数值火可正常喂养", r2.get("ok") is True, str(r2)[:120])

    # ---- 6. 命令层文案 ----
    qq3 = find_start_qq("sherenzu")
    txt = await cmds.cmd_enter(store, qq3, "入道火主", 900000077)
    check("入道回包说明数值异火（不再写本宗异火）",
          "数值异火" in txt and starter in txt and "本宗异火" not in txt, txt[:120])
    fed = await cmds.cmd_feed(store, qq3, "净莲妖火")
    check("养火拒绝时按 S 线给门槛提示（不再写本宗异火）",
          "尚未收服" in fed and "本宗异火" not in fed and "大后期" in fed, fed[:160])
    p_after = await store.get_player(qq3)
    check("命令回包写入后存档带 starter", owned(p_after).get(starter) == 1,
          str(owned(p_after)))

    # ---- 7. 最高声明：不加新玩家字段 ----
    base_keys = set(core.create_player("12002", "键", 900000077).keys())
    check("发放不新增玩家字段（无 grant 标记）",
          not any("flame_grant" in k or "flame_ver" in k or "unlock" in k
                  for k in base_keys),
          str([k for k in base_keys if "flame" in k or "unlock" in k]))

    # ---- 8. 帝炎不受影响 ----
    q4 = core.create_player("12003", "斗帝", 900000077)
    check("帝炎仍只由 diyan 成就位表达",
          "帝炎" not in owned(q4) and q4.get("diyan") is False, "")

    # ---- 9. 异火卡/文本底部「用法」必须指向玩家真能养的那一朵 ----
    h_own = cmds.flame_usage_hint({"flames": {starter: 1}, "faction": "jianan"})
    check("用法提示用玩家已收服的火", starter in h_own, h_own)
    h_wait = cmds.flame_usage_hint({"flames": {}, "faction": "jianan"})
    check("未收服时用法改提示先收服+starter 名",
          "先收服" in h_wait and starter in h_wait, h_wait)
    h_none = cmds.flame_usage_hint({"flames": {}, "faction": "xiaojia"})
    check("未收服时用法仍指向 starter（全宗门统一）",
          "先收服" in h_none and starter in h_none, h_none)
    h_card = card_mod.build_flames_data(
        {"faction": "jianan", "faction_name": "迦南学院",
         "flames": {starter: 1}})["tip"]
    check("异火卡 tip 与文本同一口径", h_card == h_own, h_card)

    # ---- 10. 「收服 <异火名>」主动收服入口（三层投放补齐）----
    async def _fresh_claim_qq(tag, lv=1, main=0):
        qq = "13900" + tag
        np_ = core.create_player(qq, "收服" + tag, 900000077)
        np_["flames"] = {n: 0 for n in cfg.OWNED_FLAME_NAMES}  # 清掉 starter，专测收服
        np_["lv"] = lv
        np_["main_idx"] = main
        await store.put_player(np_)
        return qq

    # B 线数值火：可直接收服并落盘
    qb = await _fresh_claim_qq("01")
    cb = await cmds.cmd_claim_flame(store, qb, "荒破灭焱")
    pb = await store.get_player(qb)
    check("B 线火可主动收服", owned(pb).get("荒破灭焱") == 1 and "收服了" in cb, cb[:120])

    # S 线低境界：拒绝且不写入
    qs = await _fresh_claim_qq("02", lv=1, main=0)
    cs = await cmds.cmd_claim_flame(store, qs, "陨落心炎")
    ps = await store.get_player(qs)
    check("S 线低境界收服被拒", "非你此刻所能承载" in cs and owned(ps).get("陨落心炎", 0) == 0,
          cs[:120])

    # S 线达标：境界(斗尊)+主线(2) → 收服成功
    qh = await _fresh_claim_qq("03", lv=cfg.realm_lv(8), main=2)
    ch = await cmds.cmd_claim_flame(store, qh, "陨落心炎")
    ph = await store.get_player(qh)
    check("S 线达标可收服", owned(ph).get("陨落心炎") == 1 and "驭住" in ch, ch[:120])

    # A 线隐藏彩蛋：主动收服拒绝，回话不暴露火名（保持「不明示」）
    qa = await _fresh_claim_qq("04")
    ca = await cmds.cmd_claim_flame(store, qa, "生灵之焱")
    pa = await store.get_player(qa)
    check("A 线主动收服被拒且不暴露火名",
          "生灵之焱" not in ca and owned(pa).get("生灵之焱", 0) == 0, ca[:120])

    # 帝炎：显式挡住，不进 flames
    qd = await _fresh_claim_qq("05")
    cd = await cmds.cmd_claim_flame(store, qd, "帝炎")
    pd = await store.get_player(qd)
    check("帝炎不能被口令收服", "斗帝" in cd and owned(pd).get("帝炎", 0) == 0, cd[:120])

    # 已收服幂等：再收一次不报错、不重复
    cb2 = await cmds.cmd_claim_flame(store, qb, "荒破灭焱")
    check("重复收服幂等", "已在你身" in cb2, cb2[:120])


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
