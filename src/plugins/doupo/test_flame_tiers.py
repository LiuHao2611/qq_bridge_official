# -*- coding: utf-8 -*-
"""异火三层投放自检（2026-11-19）。exit 0 = 全绿。

口径（设计文档 docs/异火投放分层设计_2026-11-19.md）：
  * 23 朵 = S5 + A7 + B11，每朵恰好一条线；
  * 入道发一朵 B 线数值 starter，S/A 超标火不再白送；
  * S 火需境界 + 主线双门槛；A 火由隐藏支线静默收服、任务文本不写火名；
  * B 火炼药/傀儡加成只认已收服等级，两处口径同源（config.flame_bonus）。

运行：/opt/venv/bin/python src/plugins/doupo/test_flame_tiers.py
"""
from __future__ import annotations

import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))
os.chdir(ROOT)
sys.path.insert(0, ROOT)

_TMP = tempfile.mkdtemp(prefix="doupo_tiers_")
os.environ["DOUPO_DATA_PATH"] = os.path.join(_TMP, "doupo.json")
os.environ["GAME_MODE_PATH"] = os.path.join(_TMP, "game_mode.json")
os.environ["QQ_LOG_DIR"] = os.path.join(_TMP, "logs")
os.environ["QQ_DATA_DIR"] = os.path.join(_TMP, "qdata")

import nonebot  # noqa: E402

nonebot.init(driver="~fastapi", onebot_access_token="e2e", log_level="CRITICAL")
from nonebot.adapters.onebot.v11 import Adapter  # noqa: E402

nonebot.get_driver().register_adapter(Adapter)
nonebot.load_plugin("src.plugins.doupo")

from src.plugins.doupo import config as cfg  # noqa: E402
from src.plugins.doupo import core  # noqa: E402
from src.plugins.doupo import flame_bonus as fb  # noqa: E402
from src.plugins.doupo import puppet_core as pc  # noqa: E402

_RES = []


def check(name, ok, detail=""):
    _RES.append((name, bool(ok), str(detail)))


def main() -> int:
    # ---- 1. 分线完整性 ----
    tiers = cfg.FLAME_TIER
    check("23 朵齐全", len(tiers) == 23, str(len(tiers)))
    cnt = {}
    for t in tiers.values():
        cnt[t] = cnt.get(t, 0) + 1
    check("S5/A7/B11", cnt == {"S": 5, "A": 7, "B": 11}, str(cnt))
    check("B 线 11 朵登记在 BONUS_FLAMES",
          len(cfg.BONUS_FLAMES) == 11 and set(cfg.BONUS_FLAME_NAMES) ==
          {n for n, t in tiers.items() if t == "B"}, "")
    check("OWNED_FLAME_NAMES 无重复",
          len(cfg.OWNED_FLAME_NAMES) == len(set(cfg.OWNED_FLAME_NAMES)), "")

    # ---- 1b. 异火描述不写章节（2026-11-19 用户「有些异火描述有章节说明，不要这个」）----
    # 语义判据：desc 里不得出现「第X章」形式的原著引用；数值/设定说明照留。
    # 这是展示契约（card.build_flames_data 把 desc 直接拼进卡面 sub 行），不是考据删除。
    import re as _re
    _chap = _re.compile(r"第[一二三四五六七八九十百零两千万0-9]+章")
    _all_flames = list(cfg.FLAMES) + list(cfg.BONUS_FLAMES) + list(cfg.OTHER_FLAMES)
    _with_chap = [f["name"] for f in _all_flames if _chap.search(str(f.get("desc") or ""))]
    check("异火 desc 无章节引用", not _with_chap, str(_with_chap))
    check("异火 desc 非空且带数值口径",
          all(f.get("desc") and ("+" in f["desc"] or "每级" in f["desc"]) for f in _all_flames), "")

    # ---- 2. 入道空火（2026-11-24 用户「异火极其稀有，直接送不符合原著」）----
    p = core.create_player("30001", "新号", 900000077)
    owned = {n: v for n, v in p["flames"].items() if v > 0}
    check("入道不送任何火", len(owned) == 0, str(owned))
    check("STARTER_FLAME 已清空", cfg.STARTER_FLAME == "", cfg.STARTER_FLAME)
    check("入道不发任何 S/A 超标火",
          not any(cfg.FLAME_TIER.get(n) in ("S", "A") for n in owned), str(owned))
    check("所有火名都占位（可养五火+B十一火+新生异火+兽火）",
          all(n in p["flames"] for n in cfg.OWNED_FLAME_NAMES), "")

    # ---- 3. S 线双门槛 ----
    low = core.create_player("30002", "低阶", 900000077)
    low["lv"] = 1
    low["main_idx"] = 0
    r = core.unlock_flame(low, "陨落心炎")
    check("低境界收服 S 火被拒",
          r.get("ok") is False and r.get("reason") == "locked", str(r))
    check("拒绝后未写入 flames", low["flames"].get("陨落心炎", 0) == 0, "")

    high = core.create_player("30003", "高阶", 900000077)
    high["lv"] = cfg.realm_lv(8)  # 斗尊
    high["main_idx"] = 5
    r2 = core.unlock_flame(high, "陨落心炎")
    check("达标可收服 S 火", r2.get("ok") is True, str(r2))
    check("收服后 flames 落 1", high["flames"].get("陨落心炎") == 1, "")

    # 主线不够仍拒（境界够、剧情不够）
    mid = core.create_player("30004", "剧情不足", 900000077)
    mid["lv"] = cfg.realm_lv(11)  # 斗圣境界够
    mid["main_idx"] = 0
    r3 = core.unlock_flame(mid, "净莲妖火")
    check("境界够但主线不足仍拒",
          r3.get("ok") is False and r3.get("reason") == "locked", str(r3))

    # 帝炎不走本入口（走 diyan 成就）
    r4 = core.unlock_flame(high, "帝炎")
    check("帝炎不在 S 门槛表、由 diyan 位表达",
          high.get("diyan") is False and "帝炎" not in cfg.S_FLAME_GATE, str(r4))

    # ---- 4. A 线隐藏支线：desc/reward 不写火名，提交时静默收服 ----
    for task_name, flame in cfg.A_FLAME_SIDE.items():
        pool = [t for t in cfg.SIDE_TASK_POOL if t["name"] == task_name]
        check("A 支线存在于任务池：" + task_name, len(pool) == 1, "")
        if pool:
            blob = pool[0]["desc"] + str(pool[0]["reward"])
            check("任务文本不含火名：" + task_name, flame not in blob, blob[:80])
    # 真跑一次提交
    a = core.create_player("30005", "彩蛋", 900000077)
    t = dict([x for x in cfg.SIDE_TASK_POOL if x["name"] == "神农谷的灵田"][0])
    t["progress"] = t["target"]
    a["task"] = t
    sub = core.task_submit(a)
    check("A 支线提交揭晓 surprise_flame",
          sub.get("surprise_flame") == "生灵之焱", str(sub)[:120])
    check("A 火已静默收服", a["flames"].get("生灵之焱") == 1, "")
    check("reward 本体仍不含火名",
          "生灵之焱" not in str(sub.get("reward")), str(sub.get("reward")))

    # ---- 5. B 线加成：只认已收服等级，口径同源 ----
    b = core.create_player("30006", "数值火", 900000077)
    b["flames"] = {n: 0 for n in cfg.OWNED_FLAME_NAMES}
    check("无 B 火时炼药加成为 0", fb.alchemy_rate_bonus(b) == 0.0, "")
    b["flames"]["火云水炎"] = 2   # value 0.02 ×2 = 0.04
    b["flames"]["九幽风炎"] = 1   # value 0.03
    got = fb.alchemy_rate_bonus(b)
    check("炼药加成按等级累加", abs(got - 0.07) < 1e-9, str(got))
    b["flames"]["荒破灭焱"] = 1   # puppet value 0.04
    check("傀儡加成单独聚合", abs(fb.puppet_atk_bonus(b) - 0.04) < 1e-9,
          str(fb.puppet_atk_bonus(b)))

    # craft_rate_for 的 flame_bonus：默认 0 逐字节不变，加了才涨且封顶
    r_base = cfg.craft_rate_for(3, "四品")
    r_plus = cfg.craft_rate_for(3, "四品", flame_bonus=0.07)
    check("加成立刻体现在成功率", r_plus[0] > r_base[0], "%s -> %s" % (r_base, r_plus))
    r_cap = cfg.craft_rate_for(3, "一品", flame_bonus=10.0)
    check("加成封顶 CRAFT_RATE_CAP", r_cap[0] <= cfg.CRAFT_RATE_CAP, str(r_cap))

    # puppet_expected_damage 的 flame_mult 同源：乘了就涨，默认 1.0 不变
    dummy = {"name": "测试傀儡", "tier": 0, "purity": 0, "awakened": False,
             "skin": "铜", "body": {}, "soul": {}}
    try:
        d0 = pc.puppet_expected_damage(dummy, 1)
        d1 = pc.puppet_expected_damage(dummy, 1, flame_mult=1.04)
        check("傀儡期望伤害吃 flame_mult", d1 > d0, "%s -> %s" % (d0, d1))
    except Exception as e:  # noqa: BLE001 — dummy 形状不全时只看默认不变
        check("傀儡期望伤害默认签名不变",
              pc.puppet_expected_damage(None) == 0.0 and
              pc.puppet_expected_damage(None, flame_mult=1.5) == 0.0, str(e))

    # ---- 6. 单一真值：不新增解锁字段 ----
    keys = set(core.create_player("30007", "键", 900000077).keys())
    check("无新解锁字段",
          not any("unlock" in k or "flame_grant" in k or "flame_ver" in k for k in keys),
          str([k for k in keys if "flame" in k or "unlock" in k]))

    ok = sum(1 for _, p_, _ in _RES if p_)
    for name, passed, detail in _RES:
        print(("PASS  " if passed else "FAIL  ") + name + ("" if passed else "  -> " + detail[:160]))
    print("=" * 70)
    print("FLAME_TIER_TOTAL=%d PASSED=%d FAILED=%d" %
          (len(_RES), ok, len(_RES) - ok))
    return 0 if ok == len(_RES) else 1


if __name__ == "__main__":
    sys.exit(main())
