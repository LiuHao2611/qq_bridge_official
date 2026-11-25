# -*- coding: utf-8 -*-
"""学斗技 / 入道1技 / 异火传承 验收（零依赖、exit-code 可验证）。

运行：
    python src/plugins/doupo/test_insight.py

覆盖（2026-11-16 用户三条需求）：
  1. 入道只领悟 1 门低阶（普通阶）斗技，且已上阵；同一 qq 稳定不重掷；
  2. 「学斗技」随机领悟：达解锁等级 + 战力门槛 + 金币足够才成功，失败各有明确 reason；
  3. 已领悟技才能升级/上阵；老存档（无 learned）视为全会，不削弱；
  4. 异火面板先列本宗传承（本宗无传承则明确提示），养成列表仍在。
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import types

PLUGINS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO_ROOT = os.path.dirname(os.path.dirname(PLUGINS_DIR))
for _p in (REPO_ROOT, PLUGINS_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def _stub_nonebot():
    nb = types.ModuleType("nonebot")

    def on_regex(*a, **k):
        m = types.SimpleNamespace()
        m.handle = lambda *a, **k: (lambda f: f)
        m.finish = lambda *a, **k: None
        return m

    nb.on_regex = on_regex
    nb.get_driver = lambda: types.SimpleNamespace(on_startup=lambda f: f)
    sys.modules["nonebot"] = nb
    for name in ("nonebot.adapters", "nonebot.adapters.onebot",
                 "nonebot.adapters.onebot.v11"):
        sys.modules[name] = types.ModuleType(name)
    v11 = sys.modules["nonebot.adapters.onebot.v11"]
    v11.MessageEvent = object
    v11.MessageSegment = object
    v11.Message = object


_stub_nonebot()

from doupo import card, commands, core, factions as fac, skills as sk  # noqa: E402

_PASSED = 0
_FAILED = 0


def check(name, cond, detail=""):
    global _PASSED, _FAILED
    if cond:
        _PASSED += 1
        print("PASS  %s" % name)
    else:
        _FAILED += 1
        print("FAIL  %s  ->  %s" % (name, detail))


class MemStore:
    def __init__(self, p):
        self.p = p

    async def get_player(self, qq):
        return self.p

    async def put_player(self, p):
        self.p = p


# ---------------------------------------------------------------------------
# 1. 入道只有 1 门低阶技
# ---------------------------------------------------------------------------
def test_enter_one_low_skill():
    for qq in ("9001", "777", "778", "10001"):
        p = core.create_player(qq, "测试", "g1")
        learned = p.get("learned") or []
        check("入道领悟恰好一门(%s)" % qq, len(learned) == 1, f"learned={learned}")
        learned_tier = next((e["tier"] for e in sk.catalog(p["faction"])
                             if e["name"] == learned[0]), None)
        check("入门技是低阶普通(%s)" % qq, learned_tier == "普通",
              f"{learned[0]} tier={learned_tier}")
        check("入门技已上阵(%s)" % qq, (p.get("loadout") or [])[:1] == learned,
              f"loadout={p.get('loadout')}")
    a = core.create_player("stable-one", "甲", "g1")
    b = core.create_player("stable-one", "甲", "g1")
    check("入门技按 qq 稳定", a["learned"] == b["learned"],
          f"{a['learned']} vs {b['learned']}")


# ---------------------------------------------------------------------------
# 2. 学斗技门槛与随机领悟
# ---------------------------------------------------------------------------
def _rich_player():
    p = core.create_player("insight-rich", "富", "g1")
    p["lv"] = 50
    p["gold"] = 100000
    p["loadout"] = list(p["learned"])
    return p


def test_insight_gates_and_success():
    p = _rich_player()
    n0 = len(p["learned"])
    before = p["gold"]
    res = core.insight(p, rng=lambda: 0.0)
    check("领悟成功", res.get("ok") is True, str(res))
    check("领悟后 learned+1", len(p["learned"]) == n0 + 1, str(p["learned"]))
    check("领悟扣金币", p["gold"] == before - res["cost"], f"{before}->{p['gold']}")
    check("领悟的是未学新技", res["name"] not in p["learned"][:-1], str(res["name"]))

    # 金币不足
    poor = _rich_player()
    poor["gold"] = 0
    r = core.insight(poor, rng=lambda: 0.0)
    check("金币不足被挡", r.get("reason") == "gold", str(r))

    # 战力不足：高门槛（已领悟很多门）且给低等级
    high = _rich_player()
    high["learned"] = [e["name"] for e in sk.learnable(high)
                       if e.get("origin") != "异火"]
    high["lv"] = 1
    r = core.insight(high, rng=lambda: 0.0)
    check("战力不足被挡", r.get("reason") in ("power", "level", "all"), str(r))

    # 老档无 learned → 惰性迁移后照常领悟（2026-11-16 用户「学斗技不要提示老版本」）
    legacy = core.create_player("legacy-x", "老", "g1")
    legacy.pop("learned", None)
    loadout_before = list(legacy.get("loadout") or [])
    r = core.insight(legacy, rng=lambda: 0.0)
    check("老档能领悟(不再legacy)", r.get("ok") is True and r.get("reason") != "legacy", str(r))
    check("老档迁移保留在用技", all(n in legacy["learned"] for n in loadout_before),
          f"learned={legacy.get('learned')} loadout={loadout_before}")
    check("老档迁移有标记", isinstance(r.get("migrated"), list), str(r))


def test_insight_random_candidate():
    seen = set()
    for i in range(12):
        p = _rich_player()
        r = core.insight(p, rng=lambda k=i: (k * 0.079) % 1)
        if r.get("ok"):
            seen.add(r["name"])
    check("领悟结果可随机(≥2种)", len(seen) >= 2, f"seen={seen}")


# ---------------------------------------------------------------------------
# 3. 已领悟才升/才上阵；老档兼容
# ---------------------------------------------------------------------------
def test_learned_gates_upgrade_and_loadout():
    p = core.create_player("gate-x", "关", "g1")
    unlearned = [e["name"] for e in sk.learnable(p)
                 if e["name"] not in p["learned"]][0]
    p["bag"] = {"焚血散": 10}
    r = core.skill_up(p, unlearned)
    check("未领悟不能升级", r.get("reason") == "noskill", str(r))
    r2 = core.skill_up(p, p["learned"][0])
    check("已领悟能升级", r2.get("ok") is True, str(r2))

    # 老档：全会可升级、四槽自动补满
    legacy = core.create_player("legacy-y", "老", "g1")
    legacy.pop("learned", None)
    legacy["loadout"] = []
    sk.ensure_loadout(legacy)
    check("老档四槽自动补满", 1 <= len(legacy["loadout"]) <= 4,
          str(legacy["loadout"]))


# ---------------------------------------------------------------------------
# 4. cmd_insight / 异火传承提示
# ---------------------------------------------------------------------------
def test_cmd_insight_text():
    p = _rich_player()
    out = asyncio.run(commands.cmd_insight(MemStore(p), "x"))
    check("学斗技命令回领悟文案", "领悟" in out and "已领悟" in out, out[:80])
    poor = _rich_player()
    poor["gold"] = 0
    out2 = asyncio.run(commands.cmd_insight(MemStore(poor), "x"))
    check("学斗技金币不足提示", "金币不足" in out2, out2[:60])


def test_learn_named_directed():
    """学习 <技名>：定向领悟指定技，与学斗技同门槛（2026-11-16 用户截图诉求）。"""
    p = _rich_player()
    pool = [e["name"] for e in sk.learnable(p) if e.get("origin") != "异火"]
    unlearned = next((n for n in pool if n not in p["learned"]), None)
    check("存在可定向学习的未学技", unlearned is not None, str(pool))
    if unlearned:
        gold0 = int(p["gold"])
        r = core.learn_named(p, unlearned)
        check("定向领悟成功", r.get("ok") is True, str(r))
        check("领悟的是点名的技", unlearned in p["learned"] and r["name"] == unlearned, str(r))
        check("定向领悟扣金币", int(p["gold"]) == gold0 - r["cost"], f"{gold0}->{p['gold']}")
        # 重复学同一门 → learned 拒绝
        r2 = core.learn_named(p, unlearned)
        check("已学会的技不重复领悟", r2.get("reason") == "learned", str(r2))
    # 不存在的技 → noskill
    r3 = core.learn_named(p, "不存在的斗技")
    check("不存在的技报 noskill", r3.get("reason") == "noskill", str(r3))


def test_legacy_migration_then_learn_named():
    """老档（无 learned、有四槽）先迁移，再定向学习用户点名的技。"""
    p = core.create_player("legacy-learn", "老", "g1")
    p["lv"] = 50
    p["gold"] = 100000
    loadout_before = list(p.get("loadout") or [])
    p.pop("learned", None)  # 造老档
    pool = [e["name"] for e in sk.learnable(p) if e.get("origin") != "异火"]
    target = next((n for n in pool if n not in loadout_before), None)
    check("老档迁移目标技可用", target is not None, str(pool))
    if target:
        r = core.learn_named(p, target)
        check("老档定向领悟成功(不报legacy)", r.get("ok") is True and r.get("reason") != "legacy", str(r))
        check("老档标记迁移", isinstance(r.get("migrated"), list), str(r))
        check("迁移保留原四槽技", all(n in p["learned"] for n in loadout_before),
              f"learned={p['learned']}")
        check("点名技也进 learned", target in p["learned"], str(p["learned"]))


def test_learn_named_alias_and_suggest():
    """🔴 2026-11-25 用户「学习 帝印诀」被判 noskill（同音字）。

    ① 同音字（诀/绝 → 决）必须能学到正确那门；
    ② 真解不出时不能只说「没有这门」，要给出候选技名（suggest），
       让玩家一句「学习 X」就能学会。
    """
    p = _rich_player()
    pool = [e["name"] for e in sk.learnable(p) if e.get("origin") != "异火"]
    canon = next((n for n in pool if "决" in n), None)
    check("存在含「决」的可学技（别名回归样本）", canon is not None, str(pool))

    if canon:
        # 同音字：诀 / 绝 都应解析到 canon
        for alias_char in ("诀", "绝"):
            alias = canon.replace("决", alias_char)
            check("同音字解析到正式名(%s)" % alias_char,
                  core.resolve_skill_name(p, alias) == canon,
                  "%s -> %r" % (alias, core.resolve_skill_name(p, alias)))
        r = core.learn_named(p, canon.replace("决", "诀"))
        check("同音字也能领悟", r.get("ok") is True, str(r))
        check("领悟的是正式名", r.get("name") == canon, str(r))

        # 反序（真解不出）→ noskill + 候选里含 canon
        rev = canon[::-1]
        r2 = core.learn_named(p, rev)
        check("反序技名仍报 noskill", r2.get("reason") == "noskill", str(r2))
        check("noskill 给出候选", canon in (r2.get("suggest") or []), str(r2.get("suggest")))
        out = asyncio.run(commands.cmd_learn_named(MemStore(p), "x", rev))
        check("命令层文案给「你是不是想学」", "你是不是想学" in out, out[:120])

    # 完全不沾边的名字：不许瞎给候选
    r3 = core.learn_named(p, "不存在的斗技")
    check("无关技名无候选", r3.get("suggest") == [], str(r3.get("suggest")))
    check("无关技名不写「你是不是想学」",
          "你是不是想学" not in asyncio.run(
              commands.cmd_learn_named(MemStore(p), "x", "不存在的斗技")))


def test_skill_up_and_loadout_accept_typo():
    """🔴 2026-11-25 用户「升级斗技不行」：`升级 帝印诀`（诀）对不上 `帝印决`（决）。

    升级/上阵原来只做全字+子串匹配 → 直接回「无此斗技」/「不可上阵」，玩家以为坏了。
    同音字归一必须覆盖这两个口令，不能只修「学习」。
    """
    p = _rich_player()
    pool = [e["name"] for e in sk.learnable(p) if e.get("origin") != "异火"]
    canon = next((n for n in pool if "决" in n), None)
    check("有含「决」的可学技（升级/上阵别名样本）", canon is not None, str(pool))
    if not canon:
        return
    check("先领悟目标技", core.learn_named(p, canon).get("ok") is True, canon)
    typo = canon.replace("决", "诀")

    out = asyncio.run(commands.cmd_skill_up(MemStore(p), "x", typo))
    check("升级：错字不再报「无此斗技」", "无此斗技" not in out, out[:90])
    check("升级：认出正式技名", canon in out, out[:90])

    out2 = asyncio.run(commands.cmd_loadout(MemStore(p), "x", typo))
    check("上阵：错字不再报「不可上阵」", "不可上阵" not in out2, out2[:90])
    check("上阵：认出正式技名", canon in out2, out2[:90])


def test_skill_up_flame_skill_explains_instead_of_noskill():
    """🔴 2026-11-25 用户「升级斗技不行」（`升级 帝炎破世`）。

    异火主动技能上阵（`equippable_names` 含它）却不在 `learned` 里，
    `skill_up` 只认 learned → 旧代码回「无此斗技」，还列一串本宗技，
    玩家只会以为自己打错字。必须明确指向「异火」养火。
    """
    p = _rich_player()
    # 造一个「已收服异火」的档（帝炎看 diyan 位，其余看 flames 喂养等级）
    flame = next(iter(sk.fs.FLAME_SKILLS))
    p.setdefault("flames", {})[flame] = 1
    flames = [e["name"] for e in sk.fs.entries_for(p)]
    check("异火主动技样本可用", bool(flames), str(flame))
    if not flames:
        return
    target = flames[0]
    check("异火技不在 learned 里（复现前提）",
          target not in (p.get("learned") or []), target)
    check("异火技可上阵（口径不一致的另一半）",
          target in core.equippable_names(p), target)
    out = asyncio.run(commands.cmd_skill_up(MemStore(p), "x", target))
    check("异火技不报「无此斗技」", "无此斗技" not in out, out[:100])
    check("异火技文案指向养火", "异火等级" in out, out[:100])
    check("异火技文案点名该技", target in out, out[:100])


def test_cmd_learn_named_no_old_version_prompt():
    """命令层：老档学斗技/定向学习都不再出现「老版本/无需领悟」提示。"""
    p = _rich_player()
    loadout_before = list(p.get("loadout") or [])
    p.pop("learned", None)  # 老档
    target = next((e["name"] for e in sk.learnable(p)
                   if e.get("origin") != "异火" and e["name"] not in loadout_before), None)
    if target:
        out = asyncio.run(commands.cmd_learn_named(MemStore(p), "x", target))
        check("定向学习无老版本提示",
              "老版本" not in out and "无需领悟" not in out and "领悟了" in out, out[:120])
        check("命令层带存档升级提示", "存档已升级" in out, out[:120])

    # 随机学斗技的老档也不再出现老版本提示
    p2 = _rich_player()
    p2.pop("learned", None)
    out2 = asyncio.run(commands.cmd_insight(MemStore(p2), "x"))
    check("学斗技无老版本提示",
          "老版本" not in out2 and "无需领悟" not in out2 and "领悟" in out2, out2[:120])


def test_learn_named_gates():
    """定向学习的等级/战力/金币门槛与学斗技一致。"""
    p = _rich_player()
    target = next((e["name"] for e in sk.learnable(p)
                   if e.get("origin") != "异火" and e["name"] not in p["learned"]), None)
    if not target:
        check("定向门槛有可测目标", False, "无可学技")
        return
    # 金币不足
    poor = _rich_player()
    poor["gold"] = 0
    check("定向学习金币不足", core.learn_named(poor, target).get("reason") == "gold")
    # 等级不足（挑 unlock>1 的技，把等级压到 1）
    gated = next((e for e in sk.entries_for(p)
                  if int(e.get("unlock", 1)) > 1 and e.get("origin") != "异火"), None)
    if gated:
        low = _rich_player()
        low["lv"] = 1
        r = core.learn_named(low, gated["name"])
        check("定向学习等级不足报level", r.get("reason") == "level", str(r))
    else:
        check("有可测的等级门槛技", False, "本宗无 unlock>1 的技")


def test_flames_shows_faction_tradition():
    # 云岚宗：无传承异火 → 纯文本一句提示，无图无按钮、无养成列表
    # （2026-11-16 用户硬要求：没异火传承的宗门不要展示任何异火）
    p = core.create_player("flame-none", "云", "g1")
    p["faction"] = "yunlanzong"
    p["faction_name"] = fac.FACTIONS["yunlanzong"]["name"]
    check("无传承判定为 False", commands.has_tradition_flame(p) is False, str(p.get("faction")))
    out = asyncio.run(commands.cmd_flames(MemStore(p), "x"))
    check("本宗无传承异火提示", "本宗无传承异火" in out, out[:100])
    check("无传承不列养成列表（纯文本一句）", "须先收服才可喂养" not in out and "青莲地心火" not in out,
          out[:160])
    check("无传承提示不含按钮/图片引导", "上阵" not in out and "斗技详情" not in out, out[:160])

    # 萧家：有传承异火 → 列出来（完整养成面板）
    p2 = core.create_player("flame-has", "萧", "g1")
    p2["faction"] = "xiaojia"
    p2["faction_name"] = fac.FACTIONS["xiaojia"]["name"]
    # create_player 的入道随机宗门可能会顺手发一朵本宗异火（A 方案），
    # 这里要的是「一朵都没有的萧家」，清空才对得上断言（否则随机 flaky）。
    p2["flames"] = {}
    check("有传承判定为 True", commands.has_tradition_flame(p2) is True, str(p2.get("faction")))
    out2 = asyncio.run(commands.cmd_flames(MemStore(p2), "x"))
    check("本宗有传承异火列出", "萧玄" in out2 and "本宗传承" in out2, out2[:120])
    # 2026-11-18 A 方案：段标题从「可自行收服的异火」改为「须先收服才可喂养」；
    # 未收服的火只标「未收服」，不再显示 0/N 级与下级药渣需求（避免误导成可直接养）。
    check("有传承仍列养成列表", "须先收服才可喂养" in out2, out2[:160])
    check("未收服的火标未收服且不给下级需求",
          "未收服" in out2 and "下级需" not in out2, out2[:200])


def test_flames_handler_no_card_without_tradition():
    """守门：异火 handler 必须先判 has_tradition_flame，无传承直接纯文本 finish，
    绝不走 render_flames/finish_card（2026-11-16「没传承不要展示任何异火/无图无按钮」）。

    用 AST 检查，防止有人把『无传承也出图』的旧逻辑接回来。
    """
    import ast as _ast
    from pathlib import Path as _Path

    init_py = _Path(__file__).with_name("__init__.py").read_text(encoding="utf-8")
    tree = _ast.parse(init_py)
    flames_fn = None
    # flames handler 是注册在 flames matcher 上的装饰器函数体：找含
    # has_tradition_flame 调用的那个 async 函数
    for node in _ast.walk(tree):
        if isinstance(node, _ast.AsyncFunctionDef):
            blob = _ast.unparse(node)
            if "cmd_flames" in blob and "get_player" in blob:
                flames_fn = node
                break
    check("找到异火 handler", flames_fn is not None, "AST 未定位 cmd_flames handler")
    if flames_fn is None:
        return
    blob = _ast.unparse(flames_fn)
    check("handler 调用 has_tradition_flame", "has_tradition_flame" in blob, blob[:200])
    # 关键：render_flames 必须在 has_tradition_flame 的『有传承』分支之后，
    # 即 handler 里存在 if not ...has_tradition_flame ... finish / return 的早退守卫。
    early_return = any(
        isinstance(n, _ast.If)
        and "has_tradition_flame" in _ast.unparse(n.test)
        and "return" in _ast.unparse(n)
        for n in _ast.walk(flames_fn)
    )
    check("无传承早退守卫（不渲染卡片）", early_return,
          "handler 缺『无传承→纯文本早退』守卫，可能又发异火图+按钮")


def _run():
    fns = [(k, v) for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    for name, fn in fns:
        fn()
    print("=" * 70)
    print("INSIGHT_TOTAL=%d PASSED=%d FAILED=%d"
          % (_PASSED + _FAILED, _PASSED, _FAILED))
    return 0 if _FAILED == 0 else 1


if __name__ == "__main__":
    sys.exit(_run())
