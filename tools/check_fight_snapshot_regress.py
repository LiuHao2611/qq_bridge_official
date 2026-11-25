# -*- coding: utf-8 -*-
"""快照回归（**冻结夹具渲染 + 旧口径数值**），2026-11-25 章节门禁改造。

## 为什么这么设计

`cmd_fight` 的正文本来就**必须**变——G2「怪等级按章节走」之后，同一个 lv40 玩家
在第 1 章遇到的怪从「按玩家等级反解的奎木兽」变成「lv5 的嗜血鼠」，回合数/战果
自然不同。拿它做跨版本字节比对，等于要求「改造不能生效」，是错的口径。

真正能证伪「改造溢出」的三组对照：

1. **冻结夹具渲染**：`last_battle` 从真实战斗里冻成 JSON（`snap_make_fixtures.py`），
   两边喂**同一份**夹具 → `cmd_battle_review` / `cmd_battle_comment` 必须逐字节相同。
   证明回看卡/评价渲染器没被章节闸污染。
2. **旧口径数值**：`core.encounter(p, chapter=None)` / `pick_encounter(p, n)` 不传章节
   时，敌种、等级、回合、经验、金币全序列必须**完全一致**（爬塔/周本/切磋/突破全靠它）。
3. **九星四段式**：`cmd_break` 在 lv9 / lv45 的两段真实文本必须逐字节相同
   （章节闸绝不能钳原著 BOSS 的等级）。

跑法：
    python tools/make_doupo_battle_fixtures.py              # 一次性生成夹具
    python tools/check_fight_snapshot_regress.py <out.json>  # HEAD 与工作区各跑一次
    python tools/check_fight_snapshot_regress.py --compare a b
"""
from __future__ import annotations

import asyncio
import json
import os
import random
import sys
import tempfile
import time
import types

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLUGINS_DIR = os.path.join(REPO_ROOT, "src", "plugins")
for _p in (REPO_ROOT, PLUGINS_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

_TMP = tempfile.mkdtemp(prefix="doupo_snapr_")
os.environ["DOUPO_DATA_PATH"] = os.path.join(_TMP, "doupo.json")
os.environ["QQ_LOG_DIR"] = os.path.join(_TMP, "logs")
os.environ["QQ_DATA_DIR"] = os.path.join(_TMP, "qdata")
os.environ["DOUPO_CHANCE_RATE"] = "0"
os.environ.setdefault("DOUPO_TOWER_SESSION_PATH",
                      os.path.join(_TMP, "tower_session.json"))

FIXTURES = os.path.join(REPO_ROOT, "tools", "fixtures",
                        "doupo_battle_fixtures.json")


def _stub_nonebot():
    nb = types.ModuleType("nonebot")
    nb.get_driver = lambda: types.SimpleNamespace(on_startup=lambda f: f)
    nb.get_bot = lambda: types.SimpleNamespace(send=lambda *a, **k: None)

    def on_regex(*a, **k):
        m = types.SimpleNamespace()
        m.handle = lambda *a, **k: (lambda f: f)
        m.finish = lambda *a, **k: None
        return m

    nb.on_regex = on_regex
    sys.modules["nonebot"] = nb
    for n in ("nonebot.adapters", "nonebot.adapters.onebot",
              "nonebot.adapters.onebot.v11"):
        sys.modules[n] = types.ModuleType(n)
    sys.modules["nonebot.adapters.onebot.v11"].MessageEvent = object
    sys.modules["nonebot.adapters.onebot.v11"].MessageSegment = \
        types.SimpleNamespace(image=lambda data: ("[image]", len(data or b"")))
    sys.modules["nonebot.adapters.onebot.v11"].Message = object


_stub_nonebot()

from doupo import commands, core, config as cfg  # noqa: E402
from doupo.store import Store  # noqa: E402

G = "900000012"


def newp(lv, qq):
    p = core.create_player(qq, "档" + qq[-3:], G)
    p["lv"] = int(lv)
    p["exp"] = 0
    core._refill(p)
    return p


async def collect():
    out = {}

    # ---- 1. 冻结夹具渲染（回看卡 + 评价）----
    with open(FIXTURES, encoding="utf-8") as fh:
        fx = json.load(fh)
    for key in sorted(fx):
        battle = fx[key]["battle"]
        qq = fx[key]["qq"]
        tmp = tempfile.mkdtemp(prefix="doupo_snapr_fx_")
        store = Store(os.path.join(tmp, "doupo.json"))
        await store.load()
        p = newp(int(battle.get("player", {}).get("lv") or 1), qq)
        p["last_battle"] = battle
        p["last_settle"] = time.time()
        await store.put_player(p)
        out[key + ".review"] = await commands.cmd_battle_review(store, qq)
        out[key + ".comment"] = await commands.cmd_battle_comment(store, qq)

    # ---- 2. 旧口径数值（chapter=None，逐字节不变）----
    for lv in (1, 5, 9, 40):
        p = newp(lv, "7600000%02d" % lv)
        r = core.encounter(p, rng=random.Random(7).random, auto_pill=False)
        out["legacy.enc_lv%d" % lv] = json.dumps(
            {"detail": [[str(d["name"]), int(d["lv"]), bool(d["win"]),
                         int(d["rounds"]), int(d["taken"])] for d in r["detail"]],
             "exp": int(r["exp"]), "gold": int(r["gold"]),
             "rounds": int(r["rounds"]), "taken": int(r["taken"]),
             "win": bool(r["win"])},
            ensure_ascii=False, sort_keys=True)
    for lv in (1, 40):
        p = newp(lv, "7500000%02d" % lv)
        rng = random.Random(5).random
        seq = []
        for _ in range(40):
            seq.append([str(s["name"]) for s in core.pick_encounter(p, 6, rng)])
        out["legacy.pick_lv%d" % lv] = json.dumps(seq, ensure_ascii=False)

    # ---- 3. 九星四段式（章节闸不得钳原著 BOSS）----
    for lv, seed in ((9, 7), (45, 13)):
        tmp = tempfile.mkdtemp(prefix="doupo_snapr_br_")
        store = Store(os.path.join(tmp, "doupo.json"))
        await store.load()
        qq = "7400000%03d" % lv
        p = newp(lv, qq)
        p["exp"] = cfg.exp_need(int(lv))
        core._refill(p)
        await store.put_player(p)
        p = await store.get_player(qq)
        p["last_settle"] = time.time()
        await store.put_player(p)
        random.seed(seed)
        out["brk.lv%d" % lv] = await commands.cmd_break(store, qq)
    return out


def compare(a_path, b_path):
    with open(a_path, encoding="utf-8") as fh:
        a = json.load(fh)
    with open(b_path, encoding="utf-8") as fh:
        b = json.load(fh)
    keys = sorted(set(a) | set(b))
    bad = []
    for k in keys:
        x, y = a.get(k), b.get(k)
        if x == y:
            print("%-22s SAME ✓  (%d 字符)" % (k, len(x or "")))
        else:
            bad.append(k)
            note = ""
            if isinstance(x, str) and isinstance(y, str):
                lx, ly = x.splitlines(), y.splitlines()
                for i in range(max(len(lx), len(ly))):
                    p1 = lx[i] if i < len(lx) else "<缺>"
                    p2 = ly[i] if i < len(ly) else "<缺>"
                    if p1 != p2:
                        note = "；首处差异行%d: HEAD=%r WORK=%r" % (i, p1[:70], p2[:70])
                        break
            print("%-22s DIFF ✗  head=%s work=%s%s"
                  % (k, x is not None, y is not None, note))
    print("-" * 70)
    print("SNAP_TOTAL=%d SAME=%d DIFF=%d" % (len(keys), len(keys) - len(bad), len(bad)))
    return 0 if not bad else 1


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--compare":
        sys.exit(compare(sys.argv[2], sys.argv[3]))
    if not os.path.exists(FIXTURES):
        print("缺夹具：先跑 python tools/make_doupo_battle_fixtures.py")
        sys.exit(2)
    data = asyncio.run(collect())
    dest = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        REPO_ROOT, "tmp", "_snap.json")
    os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
    with open(dest, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=1, sort_keys=True)
    print("SNAP_WRITTEN %s (%d blocks)" % (dest, len(data)))
