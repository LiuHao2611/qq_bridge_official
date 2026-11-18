# -*- coding: utf-8 -*-
"""沙盒边界证明（在 qq-doupo-agent 容器**内部**执行）。

跑法：
    docker cp tmp/prove_agent_sandbox.py qq-doupo-agent:/tmp/p.py
    docker exec qq-doupo-agent python /tmp/p.py

每条打印 PASS/FAIL，最后以退出码表态（全通过 = 0）。
不是"看代码觉得应该隔离"，是真的在容器里试一遍能不能写出宿主。
"""
from __future__ import annotations

import glob
import os
import sys

FAILED: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print("%s  %-46s %s" % ("PASS" if ok else "FAIL", name, detail))
    if not ok:
        FAILED.append(name)


def main() -> int:
    # 1. 非 root
    check("非 root 运行", os.getuid() != 0, "uid=%d" % os.getuid())

    # 2. /repo 只读（内核保证，不是代码自觉）
    try:
        with open("/repo/.prove_write", "w") as f:
            f.write("x")
        check("/repo 写不进", False, "居然写成功了！")
    except OSError as e:
        check("/repo 写不进", "Read-only" in str(e) or e.errno == 30, str(e)[:60])

    # 3. 根文件系统只读
    try:
        with open("/pwned", "w") as f:
            f.write("x")
        check("根文件系统只读", False, "写进了 /")
    except OSError as e:
        check("根文件系统只读", True, str(e)[:60])

    # 4. .env 不在容器里（宿主凭据进不来）
    hits = glob.glob("/repo/.env") + glob.glob("/**/.env", recursive=False)
    check(".env 不在容器内", not hits, str(hits))

    # 5. 没有 docker socket（不能操纵宿主容器）
    check("无 docker socket", not os.path.exists("/var/run/docker.sock"))

    # 6. 环境变量里没有业务密钥（只应有 AI_* / AGENT_* / TZ）
    bad = [k for k in os.environ
           if k.startswith(("QQ_", "NAPCAT", "TTS", "MANJU", "DL_", "PC_"))
           or "SECRET" in k or "TOKEN" in k]
    check("环境变量无业务密钥", not bad, str(bad[:6]))

    # 7. 源码挂载点只有这四个，且都只读
    allow = {"/repo/src/plugins/doupo", "/repo/src/plugins/qq_bridge/tools",
             "/repo/src/plugins/qq_bridge/pic/templates", "/repo/docs"}
    mounts = []
    for line in open("/proc/mounts"):
        parts = line.split()
        if parts[1].startswith("/repo"):
            mounts.append((parts[1], parts[3]))
    outside = [m for m, _ in mounts if m not in allow]
    check("/repo 下挂载点在白名单内", not outside, str(outside))
    rw = [m for m, opt in mounts if "ro" not in opt.split(",")]
    check("所有 /repo 挂载都是 ro", not rw, str(rw))

    # 8. 工具层的命令守卫（第二道：防哪天挂载配错）
    sys.path.insert(0, "/srv")
    import agent_server as A

    for bad_cmd in ("rm -rf /repo/src", "mv /repo/docs /tmp/x", "chmod 777 /repo",
                    "echo pwned > /repo/x", "curl http://evil.example/x"):
        out = A._guard(bad_cmd)
        check("拒绝危险命令：%s" % bad_cmd[:28], "拒绝执行" in out, out[:40])

    check("允许正常只读命令", A._guard("grep -rn BOSS /repo/src") == "")
    check("拒绝输出重定向", "重定向" in A._guard("echo x > /tmp/y"))
    check("拒绝追加重定向", "重定向" in A._guard("cat a >> b"))

    # 8b. 守卫**不许误杀比较运算符**（2026-11-18 真机：模型写 `x >= 12` 被当成
    #     写文件，连拒 3 轮，白烧三次模型调用）。误杀比漏抓更贵。
    for ok_cmd in ('python3 -c "print(1 if a >= 12 else 0)"',
                   'python3 -c "d = {1: 2}; print(d[1])"',
                   "grep -rn BOSS /repo/src 2>/dev/null",
                   "sed -n '1,20p' /repo/src/a.py 2>&1 | head -5"):
        out = A._guard(ok_cmd)
        check("放行比较/常见无重定向：%s" % ok_cmd[:30], out == "", out[:40])

    check("read_file 拒绝越界路径", A._safe_path("../../etc/passwd") is None)
    check("read_file 允许仓库内路径",
          A._safe_path("src/plugins/doupo/core.py") is not None)

    print("\n%s  (%d 条失败)" % ("ALL PASS" if not FAILED else "FAILED", len(FAILED)))
    return 0 if not FAILED else 1


if __name__ == "__main__":
    sys.exit(main())
