"""计划草稿存储（2026-09-12）：Web 面板「计划」Tab 的「我的计划（草稿）」。

与 queue.json 同级，存于 qq_bridge/data/plans.json。纯本地、与主项目零耦合（红线合规）。
每条计划：{plan_id, name, channel, json_text, created_at, last_run_batch, last_run_at}。

2026-09-13 新增「定时执行」（用户需求：在计划 Tab 对草稿设定时下发）：
  schedule_enabled        是否启用定时（到点由调度器自动下发）
  schedule_at             下次触发时刻（Unix 秒；0=未设定）
  schedule_repeat         "none"=只执行一次 ｜ "daily"=每天同一时刻
  schedule_last_fired_at  最近一次触发时间（0=从未）
  schedule_last_batch     最近一次定时下发的批次号

时区约定：全部用 **Unix 时间戳**存储与比较，前端负责本地时区 ↔ 时间戳互转，
避免服务器/浏览器时区不一致导致的定时漂移（跨时区部署也不会错点）。
"""
import json
import time
import uuid
from pathlib import Path

from .queue import DATA_DIR  # 复用 data 目录约定

PLANS_PATH = DATA_DIR / "plans.json"


def _log_error(msg: str) -> None:
    """写 ERROR：线上走 nonebot.log，裸跑单测退回 stdlib。"""
    try:
        from nonebot.log import logger as _lg
    except Exception:  # noqa: BLE001
        import logging

        _lg = logging.getLogger("qq_bridge.planstore")
    _lg.error(msg)


class PlanStoreError(RuntimeError):
    """计划落盘失败。**必须**向上抛：不能再出现「提示保存成功、重启就没了」。"""

# 定时字段默认值（老计划升级时补齐，保证前端读到的结构永远完整）
_SCHEDULE_DEFAULTS = {
    "schedule_enabled": False,
    "schedule_at": 0,
    "schedule_repeat": "none",
    "schedule_last_fired_at": 0,
    "schedule_last_batch": "",
}

DAY_SECONDS = 86400


class PlanStore:
    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path else PLANS_PATH
        self._plans: list = []
        self._load()

    # ---------------- 内部 ----------------
    @staticmethod
    def _norm(p: dict) -> dict:
        """补齐定时字段（老 plans.json 无这些键时不能 KeyError/undefined）。"""
        for k, v in _SCHEDULE_DEFAULTS.items():
            p.setdefault(k, v)
        return p

    def _load(self) -> None:
        try:
            if self.path.exists():
                data = json.loads(self.path.read_text(encoding="utf-8")) or []
            else:
                data = []
            # 过滤非 dict 项（坏文件不能让弹窗/调度器炸）
            self._plans = [self._norm(p) for p in data if isinstance(p, dict)]
        except Exception as e:  # noqa: BLE001
            # 坏文件不能拖垮启动，但**绝不能静默**：先备份，再以空列表启动。
            # （2026-11-17 用户最高声明：用户数据不许"看着没事其实没了"。）
            bak = str(self.path) + ".corrupt-" + time.strftime("%Y%m%d-%H%M%S")
            try:
                self.path.replace(bak)
            except Exception:  # noqa: BLE001
                _log_error("计划文件损坏且备份失败 %s：%r" % (self.path, e))
            else:
                _log_error("计划文件损坏，已备份为 %s（%r）" % (bak, e))
            self._plans = []

    def _save(self, strict: bool = True) -> bool:
        """原子落盘。

        :param strict: True=用户显式操作（新建/删除/设定时），失败抛 PlanStoreError，
                       让接口如实返回「没保存成功」；False=运行时记账（mark_run /
                       claim_due），失败打 ERROR 并返回 False，不拖垮调度器，
                       但**绝不静默**。
        """
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(self._plans, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(self.path)
            return True
        except Exception as e:  # noqa: BLE001
            _log_error("计划落盘失败 %s：%r" % (self.path, e))
            if strict:
                raise PlanStoreError("计划落盘失败（%s）" % type(e).__name__) from e
            return False

    # ---------------- 增删查 ----------------
    def save(self, name: str, channel: str, json_text: str) -> dict:
        pid = uuid.uuid4().hex[:12]
        plan = {
            "plan_id": pid,
            "name": (name or "").strip() or "未命名计划",
            "channel": str(channel or "local").strip().lower() or "local",
            "json_text": json_text or "",
            "created_at": time.time(),
            "last_run_batch": "",
            "last_run_at": 0,
        }
        plan.update(dict(_SCHEDULE_DEFAULTS))
        self._plans.append(plan)
        try:
            self._save()
        except PlanStoreError:
            self._plans.remove(plan)      # 回滚：内存与磁盘保持一致
            raise
        return {"ok": True, "plan_id": pid, "plan": plan}

    def list(self) -> list:
        return sorted(self._plans, key=lambda p: float(p.get("created_at") or 0), reverse=True)

    def get(self, plan_id: str):
        return next((p for p in self._plans if p.get("plan_id") == plan_id), None)

    def delete(self, plan_id: str) -> bool:
        old = list(self._plans)
        self._plans = [p for p in self._plans if p.get("plan_id") != plan_id]
        if len(self._plans) != len(old):
            try:
                self._save()
            except PlanStoreError:
                self._plans = old        # 回滚：删了但没写盘＝重启又回来
                raise
            return True
        return False

    def mark_run(self, plan_id: str, batch_id: str) -> None:
        p = self.get(plan_id)
        if p:
            p["last_run_batch"] = batch_id
            p["last_run_at"] = time.time()
            self._save(strict=False)

    def _apply_template(self, p: dict, template_id: str) -> None:
        """把模板写入/清除计划 json_text 的每一项（生图/放大计划共用「模板」键）。

        template_id 为空串 → 恢复默认（删除各条目的「模板」键）；
        否则逐条写入。解析失败则不动，避免把坏数据写崩。
        """
        try:
            arr = json.loads(p.get("json_text") or "[]")
        except Exception:
            return
        if not isinstance(arr, list):
            return
        for o in arr:
            if isinstance(o, dict):
                if template_id:
                    o["模板"] = template_id
                else:
                    o.pop("模板", None)
        p["json_text"] = json.dumps(arr, ensure_ascii=False)

    # ---------------- 定时执行 ----------------
    def set_schedule(self, plan_id: str, schedule_at: float, repeat: str = "none",
                     enabled: bool = True, template_id: "str | None" = None) -> dict:
        """设置/修改/取消某计划的定时。

        schedule_at: Unix 秒（enabled=True 时必须 > 0，且必须晚于当前时间——过去的时刻
                     只会立刻触发一次，属误操作，直接拒绝更安全）。
        repeat: "none"（一次）| "daily"（每天同一时刻）。
        enabled=False → 取消定时（清空时刻）。
        template_id: 传入则同步改写计划执行模板（空串=恢复默认，None=不改）。
        """
        p = self.get(plan_id)
        if not p:
            return {"ok": False, "error": "计划不存在"}
        snap = dict(p)          # 落盘失败要整体回滚，避免「界面改了、重启复原」
        # 模板修改：仅当显式传入（含空串）才动 json_text，None 保持原样
        if template_id is not None:
            self._apply_template(p, template_id)
        rep = str(repeat or "none").strip().lower()
        if rep not in ("none", "daily"):
            return {"ok": False, "error": "重复方式只能是 none（一次）或 daily（每天）"}
        if not enabled:
            p["schedule_enabled"] = False
            p["schedule_at"] = 0
            p["schedule_repeat"] = "none"
            try:
                self._save()
            except PlanStoreError:
                p.clear(); p.update(snap)
                raise
            return {"ok": True, "plan": p, "canceled": True}
        try:
            at = float(schedule_at or 0)
        except Exception:
            return {"ok": False, "error": "定时时间格式不正确"}
        if at <= 0:
            return {"ok": False, "error": "请选择定时执行的时间"}
        now = time.time()
        if at <= now:
            return {"ok": False, "error": "定时时间必须晚于当前时间"}
        p["schedule_enabled"] = True
        p["schedule_at"] = at
        p["schedule_repeat"] = rep
        try:
            self._save()
        except PlanStoreError:
            p.clear(); p.update(snap)
            raise
        return {"ok": True, "plan": p}

    def due_plans(self, now: float | None = None) -> list:
        """到点且启用的计划（调度器轮询用）。"""
        now = time.time() if now is None else float(now)
        out = []
        for p in self._plans:
            if not p.get("schedule_enabled"):
                continue
            at = float(p.get("schedule_at") or 0)
            if at > 0 and now >= at:
                out.append(p)
        return out

    def claim_due(self, plan_id: str, now: float | None = None) -> bool:
        """原子「抢单」：把到点计划移出 due 状态，返回是否抢到。

        必须在真正下发**之前**调用——否则调度器每轮（默认 15s）都会重复入队同一计划。
        一次性的执行后自动关闭；daily 的推进到下一个未来的同一时刻。
        """
        p = self.get(plan_id)
        if not p or not p.get("schedule_enabled"):
            return False
        now = time.time() if now is None else float(now)
        at = float(p.get("schedule_at") or 0)
        if at <= 0 or now < at:
            return False
        if str(p.get("schedule_repeat") or "none") == "daily":
            nxt = at
            # 极端情况（错过多个周期/系统休眠数天）一次性推到未来，避免连续补跑
            while nxt <= now:
                nxt += DAY_SECONDS
            p["schedule_at"] = nxt
            p["schedule_enabled"] = True
        else:
            p["schedule_enabled"] = False
        p["schedule_last_fired_at"] = now
        # 运行时记账：写盘失败→打 ERROR 并放弃本次抢单（返回 False 就不会下发，
        # 下一轮再抢），绝不静默当成功。
        if not self._save(strict=False):
            return False
        return True

    def mark_schedule_result(self, plan_id: str, batch_id: str) -> None:
        """记录定时下发的批次号（供前端显示「上次定时 → batch」）。"""
        p = self.get(plan_id)
        if p:
            p["schedule_last_batch"] = batch_id or ""
            self._save(strict=False)


# 模块级单例（插件入口直接用 plan_mgr；单测各自 new PlanStore 隔离）
plan_mgr = PlanStore()
