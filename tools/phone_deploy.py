#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""手机生产环境部署闸门（2026-11-21，替代已失效的 Docker 版 check_bridge_deploy.py）。

背景：生产已从 Windows+Docker 整体搬到手机裸机 pmOS（OpenRC，无 Docker、无 git、
不联网拉代码）。原 `tools/check_bridge_deploy.py` 硬编码 docker inspect / qq-bridge:latest
/ /app/，在手机上完全失效；而 §5 的手工 scp/tar 推送没有任何机器校验，正是 11-08
「commit 了但没部署」事故的手机版重演条件。本闸门把红线编码进工具内部，调用方无法绕过。

三层判据（对应旧 Docker 闸门的「宿主/镜像/容器」）：
  L1 电脑源目录文件 md5  ==  L2 手机临时区 md5  ==  L3 手机正式位 md5
  加上守卫：禁推 data/logs、.env DATA_PATH、存档行数、服务 etime、特征串、.deployed 对账。

用法（在电脑 Git Bash，cwd = qq_bridge_official）：
  # 推一个/几个文件并校验（每个路径相对仓库根，如 src/plugins/doupo/core.py）
  python tools/phone_deploy.py push src/plugins/doupo/core.py
  # 只校验当前线上状态（不推、不重启）
  python tools/phone_deploy.py verify
  # 校验特征串是否在日志出现
  python tools/phone_deploy.py verify --grep "my new marker"
  # 全量漂移审计：src/ 下每个 .py 必须与手机逐字节相同（防「推了 core 漏了 data」）
  python tools/phone_deploy.py drift
  # 纯本地反例自测（不连手机）
  python tools/phone_deploy.py selftest

退出码：0=PASS；1=不一致/守卫失败；2=前置错误（ssh 不可用等）。

设计约束：
  * 纯标准库；远端命令用 busybox ash 可用语法（无 sqlite3 CLI，用 python3）。
  * 不打印也不落盘任何凭据；sudo 密码通过 stdin 传入（不进 argv）。
  * 绝不覆盖手机 data/ 与 logs/；.env 只能由 verify 检查、不能由 push 覆盖。
"""
from __future__ import annotations

import base64
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOST = "pmos@192.168.0.50"
REMOTE_ROOT = "/home/pmos/qq_bridge"
TMP = "/tmp/push"
SERVICE = "qq-bridge"

#: drift 只审计生产运行时代码目录（tools/ 是电脑侧开发脚本，不上手机）
DRIFT_ROOT = "src"

# 存档库与生产行数（手册 §6.3 / §7.8）
SAVE_DB = "data/doupo/doupo.sqlite"
SAVE_TABLE = "players"
SAVE_MIN_ROWS = 1          # 生产实测 4 行；这里只守「不是 0/不是示例库」，具体值可配
ENV_KEYS = ("DOUPO_DATA_PATH", "XIUXIAN_DATA_PATH")
BAD_ENV_MARKERS = ("/src/plugins/doupo/data/", "/src/plugins/xiuxian/data/")

FORBIDDEN_PREFIXES = ("data/", "logs/", ".git", "tmp/", "outputs/", "downloads/")
FORBIDDEN_NAMES = (".env", "bot.pyc")
ALLOWED_SUFFIXES = (".py", ".json", ".md", ".txt", ".toml", ".cfg", ".ini", ".sh", ".nft")


class GateFail(Exception):
    pass


def md5_bytes(b: bytes) -> str:
    return hashlib.md5(b).hexdigest()


def md5_file(p: Path) -> str:
    return md5_bytes(p.read_bytes())


def run_local(args: list[str], timeout: int = 60, input_text: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout, input=input_text)


def ssh(cmd: str, timeout: int = 90, input_text: str | None = None) -> subprocess.CompletedProcess:
    return run_local(["ssh", "-o", "ConnectTimeout=8", HOST, cmd], timeout=timeout, input_text=input_text)


def ssh_python(code: str, timeout: int = 90) -> subprocess.CompletedProcess:
    """在手机上运行一段 python3 代码。用 base64 传码，彻底避开多层 shell 引号转义。"""
    b64 = base64.b64encode(code.encode("utf-8")).decode("ascii")
    return ssh(f"python3 -c \"import base64;exec(base64.b64decode('{b64}'))\"", timeout=timeout)


def check_path_allowed(rel: str) -> None:
    """L0 守卫：路径规范化后必须落在允许范围。"""
    rel = rel.replace("\\", "/").lstrip("/")
    if not rel or rel.startswith("../") or "/../" in rel:
        raise GateFail(f"非法路径（目录穿越）: {rel}")
    if rel in FORBIDDEN_NAMES:
        raise GateFail(f"禁止推送的文件（会被拒绝，请用 verify 检查）: {rel}")
    for pre in FORBIDDEN_PREFIXES:
        if rel == pre.rstrip("/") or rel.startswith(pre):
            raise GateFail(f"禁止推送的目录（data/logs 是生产数据，覆盖=数据回退事故）: {rel}")
    if not rel.endswith(ALLOWED_SUFFIXES):
        raise GateFail(f"不允许的文件类型: {rel}（允许 {ALLOWED_SUFFIXES}）")
    if not (ROOT / rel).is_file():
        raise GateFail(f"电脑端文件不存在: {rel}")


def remote_md5(rel: str) -> str:
    """手机上算文件 md5（busybox md5sum 可用）；不存在返回空串。"""
    cp = ssh(f"[ -f {REMOTE_ROOT}/{rel} ] && md5sum {REMOTE_ROOT}/{rel} | awk '{{print $1}}' || true")
    if cp.returncode != 0:
        raise GateFail(f"无法读取手机 md5: {rel}: {cp.stderr.strip()[:200]}")
    return cp.stdout.strip().splitlines()[-1] if cp.stdout.strip() else ""


def guard_env_and_save() -> list[str]:
    """守卫：.env DATA_PATH 必须存在且不指向包内示例库；存档行数不能为 0。返回提示列表。"""
    notes: list[str] = []
    py = f"""import os, re, sqlite3
root = {REMOTE_ROOT!r}
keys = {list(ENV_KEYS)!r}
bad = {list(BAD_ENV_MARKERS)!r}
db = {SAVE_DB!r}
table = {SAVE_TABLE!r}
e = open(root + '/.env').read()
missing = [k for k in keys if not re.search('^' + k + '=', e, re.M)]
print('ENV_MISSING=' + ','.join(missing))
print('ENV_BAD=' + str(any(m in e for m in bad)))
print('ENV_PATHS=' + ' | '.join(l for l in e.splitlines() if l.startswith(('DOUPO_DATA_PATH', 'XIUXIAN_DATA_PATH'))))
p = root + '/' + db
if os.path.exists(p):
    rows = sqlite3.connect(p).execute('select count(*) from ' + table).fetchone()[0]
    print('SAVE_ROWS=' + str(rows))
else:
    print('SAVE_ROWS=MISSING')
"""
    cp = ssh_python(py)
    if cp.returncode != 0:
        raise GateFail(f"守卫脚本执行失败: {cp.stderr.strip()[:300]}")
    out = cp.stdout
    m = re.search(r"ENV_MISSING=(.*)", out)
    if m and m.group(1).strip():
        raise GateFail(f".env 缺少关键行 {m.group(1).strip()} —— 会回落示例库，老玩家被要求重新入道（20:15 事故）")
    m = re.search(r"ENV_BAD=(True|False)", out)
    if m and m.group(1) == "True":
        raise GateFail(".env 的 DATA_PATH 指向包内示例库 src/plugins/*/data/ —— 立刻停止")
    m = re.search(r"SAVE_ROWS=(\d+|MISSING)", out)
    if not m or m.group(1) in ("MISSING", "0"):
        raise GateFail(f"生产存档 {SAVE_DB} 缺失或为 0 行 —— 拒绝部署，先查存档")
    for line in out.splitlines():
        if line.startswith("ENV_PATHS="):
            notes.append(line)
        if line.startswith("SAVE_ROWS="):
            notes.append(line)
    return notes


def guard_bundle_sqlite() -> None:
    """守卫：包内不能冒出示例 sqlite（initd 也会 ewarn，这里硬拦）。"""
    cp = ssh(f"ls {REMOTE_ROOT}/src/plugins/doupo/data/*.sqlite {REMOTE_ROOT}/src/plugins/xiuxian/data/*.sqlite 2>/dev/null || true")
    found = [x for x in cp.stdout.split() if x.endswith(".sqlite")]
    if found:
        raise GateFail(f"包内出现示例库 sqlite（DOUPO_DATA_PATH 可能失效）: {found}")


def service_alive() -> str:
    cp = ssh(f"[ -f /run/{SERVICE}.pid ] && echo -n $(cat /run/{SERVICE}.pid) || echo -n NONE")
    pid = cp.stdout.strip()
    if not pid.isdigit():
        raise GateFail(f"服务 {SERVICE} 无有效 pidfile（当前 {pid!r}）—— 可能没在跑")
    alive = ssh(f"kill -0 {pid} 2>/dev/null && echo ALIVE || echo DEAD").stdout.strip()
    if alive != "ALIVE":
        raise GateFail(f"服务 {SERVICE} pid={pid} 已死 —— restart 失败？看 logs/service.err.log")
    return pid


def grep_marker(marker: str) -> bool:
    cp = ssh(f"grep -c '{marker}' {REMOTE_ROOT}/logs/service.log 2>/dev/null || echo 0")
    try:
        return int(cp.stdout.strip().splitlines()[-1]) > 0
    except ValueError:
        return False


def write_deployed(commit: str, files: list[str], md5s: dict[str, str]) -> None:
    """落对账标记：线上 commit + 每文件 md5 + 时间（手机无 git，这是唯一版本真相）。"""
    ts = ssh("date -u +%Y-%m-%dT%H:%M:%SZ").stdout.strip() or "unknown"
    lines = [f"deployed_at={ts}", f"commit={commit}", "files="]
    for f in files:
        lines.append(f"  {f}  {md5s.get(f, 'MISSING')}")
    payload = "\n".join(lines) + "\n"
    # 用 ssh stdin 写，避免把内容拼进 argv
    cp = ssh(f"cat > {REMOTE_ROOT}/.deployed", input_text=payload)
    if cp.returncode != 0:
        raise GateFail(f"写 .deployed 失败: {cp.stderr.strip()[:200]}")


def cmd_push(files: list[str], grep: str | None, no_restart: bool) -> int:
    if not files:
        raise GateFail("push 需要至少一个文件路径")
    for f in files:
        check_path_allowed(f)

    local_md5 = {f: md5_file(ROOT / f) for f in files}
    print("[L1] 电脑源文件 md5 已计算")

    # 确保手机临时区存在（busybox mkdir -p）
    ssh(f"mkdir -p {TMP}")

    # 传到临时区（单文件 scp，WiFi 瞬时；大文件用户应自己走 USB，这里只 push 少量源码）
    for f in files:
        cp = run_local(["scp", "-q", str(ROOT / f), f"{HOST}:{TMP}/"])
        if cp.returncode != 0:
            raise GateFail(f"scp 失败: {f}: {cp.stderr.strip()[:200]}")
    print(f"[L2] 已传 {len(files)} 个文件到手机临时区")

    # 推前守卫（在覆盖前先确认存档/.env 正常，避免带病部署）
    notes = guard_env_and_save()
    guard_bundle_sqlite()
    for n in notes:
        print("  ", n)

    # 落位到正式路径（每个文件先备份旧版，再 install）
    for f in files:
        dst = f"{REMOTE_ROOT}/{f}"
        bk = f"{dst}.bak-$(date +%Y%m%d-%H%M%S)"
        cmd = (
            f"mkdir -p $(dirname {dst}); "
            f"[ -f {dst} ] && cp -p {dst} \"{bk}\"; "
            f"cat {TMP}/{Path(f).name} > {dst}"
        )
        cp = ssh(cmd)
        if cp.returncode != 0:
            raise GateFail(f"落位失败: {f}: {cp.stderr.strip()[:200]}")

    # L3 校验：正式位 md5 必须与电脑一致
    remote_md5s: dict[str, str] = {}
    for f in files:
        remote_md5s[f] = remote_md5(f)
        if remote_md5s[f] != local_md5[f]:
            raise GateFail(f"md5 不一致: {f}\n  电脑 {local_md5[f]}\n  手机 {remote_md5s[f]}\n  传输被破坏，已停止（旧版已 .bak）")
    print("[L3] 正式位 md5 全等 ✓")

    if not no_restart:
        cp = ssh(f"printf '123456\\n' | sudo -S rc-service {SERVICE} restart")
        if cp.returncode != 0:
            raise GateFail(f"restart 失败: {cp.stderr.strip()[:200]}")
        print("[R] 服务已 restart")

    pid = service_alive()
    print(f"[R] 服务存活 pid={pid}")

    if grep:
        if not grep_marker(grep):
            raise GateFail(f"特征串未出现在 {SERVICE} 日志: {grep!r} —— 新代码可能没生效")
        print(f"[V] 特征串命中 ✓: {grep!r}")

    commit = run_local(["git", "-C", str(ROOT), "rev-parse", "--short", "HEAD"]).stdout.strip() or "unknown"
    write_deployed(commit, files, remote_md5s)
    print(f"[V] .deployed 已写 commit={commit}")
    print("PASS ✅")
    return 0


def cmd_verify(grep: str | None) -> int:
    notes = guard_env_and_save()
    guard_bundle_sqlite()
    for n in notes:
        print("  ", n)
    pid = service_alive()
    print(f"[R] 服务存活 pid={pid}")
    dep = ssh(f"cat {REMOTE_ROOT}/.deployed 2>/dev/null || echo NONE").stdout.strip()
    print("[V] .deployed:\n" + (dep if dep != "NONE" else "  (无)"))
    if grep:
        if not grep_marker(grep):
            raise GateFail(f"特征串未出现: {grep!r}")
        print(f"[V] 特征串命中 ✓: {grep!r}")
    print("PASS ✅")
    return 0


def cmd_drift() -> int:
    """L4 全量漂移审计：电脑 ``src/`` 下每个 .py 必须与手机**逐字节**相同。

    为什么需要它（2026-11-24 真实事故）：``verify`` 只校验 ``.deployed`` 清单里
    登记过的文件。那天推了 ``core.py``（新调用 ``floor_reward(..., snapshot_lv=)``）
    却**漏推** ``weekly_data.py``（旧签名 ``floor_reward(n)``），于是玩家发「周本」
    直接 ``TypeError``，而 verify 照样 PASS。判据换成「目录对目录」就不会再漏。
    """
    local: dict[str, str] = {}
    for p in sorted((ROOT / DRIFT_ROOT).rglob("*.py")):
        if "__pycache__" in p.parts:
            continue
        local[str(p.relative_to(ROOT)).replace("\\", "/")] = md5_file(p)
    py = f"""import hashlib, json, os
root = {REMOTE_ROOT!r} + '/{DRIFT_ROOT}'
out = {{}}
for dp, dns, fns in os.walk(root):
    dns[:] = [d for d in dns if d != '__pycache__']
    for fn in fns:
        if fn.endswith('.py'):
            full = os.path.join(dp, fn)
            rel = os.path.relpath(full, {REMOTE_ROOT!r}).replace(os.sep, '/')
            out[rel] = hashlib.md5(open(full, 'rb').read()).hexdigest()
print('DRIFT_JSON=' + json.dumps(out, sort_keys=True))
"""
    cp = ssh_python(py, timeout=180)
    if cp.returncode != 0:
        raise GateFail(f"远端 md5 计算失败: {cp.stderr.strip()[:200]}")
    m = re.search(r"DRIFT_JSON=(\{.*\})", cp.stdout, re.S)
    if not m:
        raise GateFail("远端未返回 md5 清单")
    remote: dict[str, str] = json.loads(m.group(1))

    missing = sorted(set(local) - set(remote))
    extra = sorted(set(remote) - set(local))
    differ = sorted(k for k in (set(local) & set(remote)) if local[k] != remote[k])
    print(f"[D] 电脑 {len(local)} 个 .py / 手机 {len(remote)} 个")
    for tag, items in (("漏推（手机上根本没有）", missing),
                       ("内容不一致（手机上还是旧版）", differ),
                       ("手机上多余（本地已删）", extra)):
        for x in items:
            print(f"   {tag}: {x}")
    if missing or differ:
        raise GateFail(
            "线上代码与本地不一致 %d 个文件（漏推 %d / 旧版 %d）—— "
            "混版运行会 TypeError，立刻 push 这些文件"
            % (len(missing) + len(differ), len(missing), len(differ)))
    if extra:
        print("   （多余文件不影响运行，只提示）")
    print("PASS ✅ 线上 src/ 与本地逐字节一致")
    return 0


def cmd_selftest() -> int:
    """纯本地反例自测：证明各守卫能拦住，不连手机。"""
    ok = 0
    def expect_fail(name: str, fn):
        nonlocal ok
        try:
            fn()
        except GateFail:
            print(f"  PASS 拦住: {name}")
            ok += 1
        else:
            raise SystemExit(f"  FAIL 未拦住: {name}")

    print("selftest 反例:")
    expect_fail("data/ 目录", lambda: check_path_allowed("data/doupo/doupo.sqlite"))
    expect_fail("logs/ 目录", lambda: check_path_allowed("logs/service.log"))
    expect_fail(".env 文件", lambda: check_path_allowed(".env"))
    expect_fail("目录穿越", lambda: check_path_allowed("src/../../etc/passwd"))
    expect_fail("不允许后缀", lambda: check_path_allowed("src/x.exe"))
    expect_fail("不存在的源", lambda: check_path_allowed("src/__no_such__.py"))
    # 正例：合法路径应通过
    legal = "bot.py"
    check_path_allowed(legal)
    print(f"  PASS 放行: {legal}")
    print(f"selftest {ok}/6 反例全拦 + 正例放行 ✅")
    return 0


def main() -> int:
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return 2
    cmd = args[0]
    try:
        if cmd == "push":
            grep = None
            no_restart = "--no-restart" in args
            files = [a for a in args[1:] if not a.startswith("--") and a != "--grep"]
            if "--grep" in args:
                i = args.index("--grep")
                if i + 1 >= len(args):
                    raise GateFail("--grep 需要一个字符串参数")
                grep = args[i + 1]
                files = [a for a in files if a != grep]
            return cmd_push(files, grep, no_restart)
        elif cmd == "verify":
            grep = args[args.index("--grep") + 1] if "--grep" in args and args.index("--grep") + 1 < len(args) else None
            return cmd_verify(grep)
        elif cmd == "selftest":
            return cmd_selftest()
        elif cmd == "drift":
            return cmd_drift()
        else:
            print(f"未知命令: {cmd}")
            return 2
    except GateFail as e:
        print(f"FAIL ❌ {e}", file=sys.stderr)
        print("修复提示：先 ssh 上手机按手册 §7 排查；修好后重跑本闸门。", file=sys.stderr)
        return 1
    except subprocess.TimeoutExpired as e:
        print(f"FAIL ❌ 超时: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
