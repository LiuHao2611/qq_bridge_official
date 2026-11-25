# -*- coding: utf-8 -*-
"""手机业务服务看门狗守门（2026-11-25，prove-it，宿主纯标准库）。

守的是用户红线原话：
  「服务只要掉了，就先强制关闭所有服务，然后启动，不要走单独启动，避免出现问题」

三条脚本（tools/phone_ops/）：
  * qq-service-restart-all   全停全启唯一出口
  * qq-service-watchdogd     常驻巡检
  * qq-service-watchdogd.init OpenRC 服务定义

跑法：python tools/phone_ops/test_service_watchdog.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
RESTART = (_HERE / "qq-service-restart-all").read_text(encoding="utf-8")
WATCH = (_HERE / "qq-service-watchdogd").read_text(encoding="utf-8")
INIT = (_HERE / "qq-service-watchdogd.init").read_text(encoding="utf-8")


def _code_lines(src: str) -> list[str]:
    """去掉整行注释后的有效代码行（注释里提到 restart 不算违规）。"""
    out = []
    for line in src.splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        out.append(s)
    return out


def t_no_single_service_restart() -> None:
    """红线①：重启脚本绝不出现 `rc-service X restart`（单独启动）。"""
    code = "\n".join(_code_lines(RESTART))
    # 允许注释讲历史事故，但代码行里不许有 `rc-service ... restart`
    bad = re.findall(r"rc-service\s+\S+\s+restart\b", code)
    assert not bad, "全停全启脚本不得调用单服务 restart，实际 %r" % bad
    assert "rc-service qq-bridge stop" in code
    assert "rc-service qq-bridge start" in code
    assert "rc-service qq-doupo-agent stop" in code
    assert "rc-service qq-doupo-agent start" in code


def t_stop_reverse_start_forward() -> None:
    """红线②：反序停（agent→bridge）、正序启（bridge→agent）。"""
    code = _code_lines(RESTART)
    stop_agent = next(i for i, l in enumerate(code) if "qq-doupo-agent stop" in l)
    stop_bridge = next(i for i, l in enumerate(code) if "qq-bridge stop" in l)
    start_bridge = next(i for i, l in enumerate(code) if "qq-bridge start" in l)
    start_agent = next(i for i, l in enumerate(code) if "qq-doupo-agent start" in l)
    assert stop_agent < stop_bridge, "必须先停 agent 再停 bridge"
    assert start_bridge < start_agent, "必须先启 bridge 再启 agent"
    assert stop_bridge < start_bridge, "必须先全停再全启"


def t_hard_kill_and_root_guard() -> None:
    """红线③：kill -9 兜底覆盖两个服务 + root 自检（非 root 手动跑会停死服务）。"""
    code = "\n".join(_code_lines(RESTART))
    assert "kill -9" in code, "stop 卡住时要有 kill -9 兜底"
    assert "/run/qq-bridge.pid" in code and "/run/qq-doupo-agent.pid" in code, (
        "两个业务服务的 pidfile 都要检查")
    assert 'id -u' in code and 'exit 2' in code, (
        "必须 root 自检：pmos 手动跑会 rm/stop 失败并把服务停死（演练踩过）")
    # 不手动 rm pidfile：OpenRC start 自己处理；手动 rm 会让 start-stop-daemon 误判
    assert not re.search(r"rm\s+-f\s+\"?\$pf", code), (
        "不要手动 rm pidfile，OpenRC 的 start 会处理，手动删反而导致误判")


def t_port_verification() -> None:
    """红线④：全启后必须验证 8081 + 8765 端口，不能启完就算成功。"""
    code = "\n".join(_code_lines(RESTART))
    assert "port_open" in code
    assert "8081" in code and "8765" in code
    assert "exit 0" in code and "exit 1" in code, "要有明确的成功/失败返回"


def t_watchdog_never_touches_infra() -> None:
    """红线⑤：看门狗绝不 reboot / nmcli / rebind / 停基础设施服务。"""
    code = "\n".join(_code_lines(WATCH))
    for forbidden in ("reboot", "nmcli", "rebind", "rmmod", "modprobe",
                      "rc-service sshd", "rc-service networkmanager",
                      "rc-service nftables", "rc-service wifi-watchdogd"):
        assert forbidden not in code, "看门狗不得执行 %s（会把 wlan0 玩消失/失联）" % forbidden


def t_watchdog_judges_by_8081() -> None:
    """红线⑥：判死只盯 8081，agent 抖动不误杀 bot。"""
    code = "\n".join(_code_lines(WATCH))
    assert re.search(r"^PORT=8081$", code, re.M), "判死端口必须是 8081"
    assert "8765" not in code, "判死口径不得掺入 8765（agent 抖动会误杀 bot）"


def t_watchdog_debounce_and_cooldown() -> None:
    """红线⑦：防抖三件套——宽限 / 连续判死 / 冷却 / usb0 跳过，缺一不可。"""
    code = "\n".join(_code_lines(WATCH))
    assert "sleep 90" in code, "开机 90s 宽限"
    assert "DEAD_ROUNDS=3" in code, "连续 3 轮判死"
    assert "COOLDOWN=900" in code, "15min 重启冷却"
    assert "172\\.16\\.42\\." in code, (
        "usb0 跳过必须判『有来自 172.16.42.x 的活跃 SSH』；"
        "用 ip addr|grep inet 会永远为真（usb0 常态有 IP），看门狗永不重启（实测踩过）")
    assert not re.search(r"ip\s+-4\s+addr\s+show\s+usb0", code), (
        "禁止用 usb0 有 IP 当『有人操作』判据——usb0 常态 UP，会卡死重启")
    assert "qq-service-restart-all" in code, "必须走全停全启唯一出口"


def t_init_script_sane() -> None:
    """红线⑧：OpenRC 服务定义首行 shebang + background + pidfile + depend 顺序。"""
    lines = INIT.splitlines()
    assert lines[0] == "#!/sbin/openrc-run", "首行必须 shebang（BACKUP_OK 事故）"
    assert "command_background=\"yes\"" in INIT
    assert "pidfile=" in INIT
    dep = re.search(r"depend\(\)\s*\{([^}]*)\}", INIT, re.S)
    assert dep, "缺 depend"
    body = dep.group(1)
    assert "need net" in body
    assert body.index("qq-bridge") < body.index("wifi-watchdogd") or "after" in body


def t_mutation_restart_turns_red() -> None:
    """真变异反证：把全停全启脚本里的 `start` 改成 `restart`，守门必须打红。"""
    mutated = RESTART.replace("rc-service qq-bridge start",
                              "rc-service qq-bridge restart")
    assert mutated != RESTART, "变异锚点没找到"
    code = "\n".join(_code_lines(mutated))
    bad = re.findall(r"rc-service\s+\S+\s+restart\b", code)
    assert bad, "变异后守门应检测到单服务 restart（证明 detector 非空转）"


CASES = (
    ("no_single_service_restart", t_no_single_service_restart),
    ("stop_reverse_start_forward", t_stop_reverse_start_forward),
    ("hard_kill_and_root_guard", t_hard_kill_and_root_guard),
    ("port_verification", t_port_verification),
    ("watchdog_never_touches_infra", t_watchdog_never_touches_infra),
    ("watchdog_judges_by_8081", t_watchdog_judges_by_8081),
    ("watchdog_debounce_and_cooldown", t_watchdog_debounce_and_cooldown),
    ("init_script_sane", t_init_script_sane),
    ("mutation_restart_turns_red", t_mutation_restart_turns_red),
)


def main() -> int:
    bad = 0
    for name, fn in CASES:
        try:
            fn()
        except Exception as e:  # noqa: BLE001
            bad += 1
            print("FAIL  %-38s %s: %s" % (name, type(e).__name__, e))
        else:
            print("ok    %s" % name)
    total = len(CASES)
    print("\n%d/%d 通过" % (total - bad, total))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
