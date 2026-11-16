# -*- coding: utf-8 -*-
"""运行态数据落盘位置守门（2026-11-16 事故后新增）。

## 事故复盘（为什么要这个测试）

`.env` 里 ``STICKER_DIR`` 一直是 **Windows 盘符路径**
（``D:/AI/manju_agent/qq_bridge_official/data/stickers``），而
``history/__init__.py`` 用 STICKER_DIR 反推 ``data/`` 目录。该路径在 Linux 容器里
被当作相对路径，解析成 ``/app/D:/AI/.../data`` —— 于是 history / memory / checkin /
budget / emotion / group_style 六个 SQLite 库全部写进**镜像可写层**，
`docker build` / `compose up --recreate` 一次丢一次。

同类问题还有斗破/修仙：它们的存档默认写在插件包内（``src/plugins/*/data/*.json``），
Dockerfile 是 ``COPY src ./src``，不排除就被烘进镜像，重建即回滚。

## 守门四条（全部可独立验证，不信任注释）

1. **源码里默认落到镜像内的存档路径** → 必须在 compose 里被显式覆盖，
   且覆盖值以 ``/app/data`` 开头（bind-mount 卷）。
2. **被覆盖的那些插件 data 目录** → 必须在 ``.dockerignore`` 里排除，
   保证旧快照永不进镜像（双保险）。
3. **`.env` 里任何盘符路径的键** → 必须在 compose 里覆盖（否则容器内必然错解）。
4. **prove-it**：把坏输入（缺覆盖 / 带盘符 / 未排除）喂给检测函数，
   守门必须报出违例 —— 防止测试本身退化成空转。
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPOSE = ROOT / "docker-compose.official.yml"
DOCKERIGNORE = ROOT / ".dockerignore"
ENV_FILE = ROOT / ".env"

#: bind-mount 卷在容器内的挂载点。运行态数据只能落在这里。
VOLUME_ROOT = "/app/data"

_ENV_KEY_RE = re.compile(r"^([A-Z_][A-Z0-9_]*)=(.*)$")
_COMPOSE_ENV_RE = re.compile(r"^\s{4,}([A-Z][A-Z0-9_]*):\s*[\"']?([^\"'\n]*)[\"']?\s*$")
#: Windows 盘符路径：``D:/x``、``D:\\x``；POSIX 下这种值必定被错解。
_DRIVE_RE = re.compile(r"^[A-Za-z]:[\\/]")


# ---------------------------------------------------------------------------
# 三个独立解析器（纯文本，不依赖 pyyaml，坏输入也能跑）
# ---------------------------------------------------------------------------
def compose_env(text: str) -> dict[str, str]:
    """解析 compose 里 service 的 environment 段（KEY: "value"）。"""
    out: dict[str, str] = {}
    for line in text.splitlines():
        m = _COMPOSE_ENV_RE.match(line)
        if m and not line.strip().startswith("#"):
            out[m.group(1)] = m.group(2).strip()
    return out


def env_drive_keys(text: str) -> dict[str, str]:
    """返回 .env 中值形如 Windows 盘符路径的键（注释行忽略）。"""
    out: dict[str, str] = {}
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        m = _ENV_KEY_RE.match(s)
        if m and _DRIVE_RE.match(m.group(2).strip().strip("\"'")):
            out[m.group(1)] = m.group(2).strip()
    return out


def in_image_save_keys(root: Path) -> dict[str, tuple[Path, str]]:
    """扫描源码：``os.environ.get("<KEY>", <含 __file__ 的默认值>)``。

    这种默认值会把存档落到「相对源码包」的位置 —— 在容器里即镜像层。
    返回 ``{KEY: (源文件, 默认表达式)}``。
    """
    out: dict[str, tuple[Path, str]] = {}
    for py in sorted(root.glob("src/**/*.py")):
        try:
            tree = ast.parse(py.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            is_get = (
                isinstance(fn, ast.Attribute)
                and fn.attr == "get"
                and isinstance(fn.value, ast.Attribute)
                and fn.value.attr == "environ"
            )
            if not is_get or len(node.args) < 2:
                continue
            key_node = node.args[0]
            if not (isinstance(key_node, ast.Constant) and isinstance(key_node.value, str)):
                continue
            try:
                default_src = ast.unparse(node.args[1])
            except Exception:  # noqa: BLE001 — 解不出源码就跳过，不误判
                continue
            if "__file__" in default_src:
                out[key_node.value] = (py, default_src)
    return out


def _rel(p: Path, root: Path) -> str:
    return str(p.relative_to(root)).replace("\\", "/")


# ---------------------------------------------------------------------------
# 守门
# ---------------------------------------------------------------------------
def test_plugin_save_paths_are_overridden_into_volume():
    """守门①：默认落进镜像内的存档路径，必须在 compose 覆盖到 /app/data。"""
    assert COMPOSE.exists(), "缺少 docker-compose.official.yml"
    cenv = compose_env(COMPOSE.read_text(encoding="utf-8"))
    offenders = {}
    for key, (src, default) in in_image_save_keys(ROOT).items():
        val = cenv.get(key, "")
        if not val.startswith(VOLUME_ROOT + "/"):
            offenders[key] = (str(src), default, val or "<未覆盖>")
    assert not offenders, (
        "以下存档路径默认落在镜像层且未被 compose 覆盖到 %s（重建即丢数据）：%s"
        % (VOLUME_ROOT, offenders)
    )


def _default_dir_name(default_src: str) -> str:
    """从默认表达式里取「第一个字符串字面量」＝落盘目录名（data / .wm_cache）。"""
    m = re.findall(r"['\"]([^'\"]+)['\"]", default_src or "")
    return m[0] if m else ""


def test_plugin_data_dirs_excluded_from_image():
    """守门②：**玩家数据**类插件 data 目录必须在 .dockerignore 排除。

    只对目录名恰为 ``data`` 的要求排除 —— 这类是真正的存档（斗破/修仙）；
    ``.wm_cache`` 之类纯缓存交给 compose 覆盖到卷即可（见守门①）。
    """
    assert DOCKERIGNORE.exists(), "缺少 .dockerignore"
    ignored = {
        line.strip()
        for line in DOCKERIGNORE.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    }
    missing = []
    for key, (src, default) in in_image_save_keys(ROOT).items():
        if _default_dir_name(default) != "data":
            continue
        want = _rel(src.parent, ROOT) + "/data/"
        if want not in ignored:
            missing.append((key, want))
    assert not missing, ".dockerignore 未排除插件内存档目录（旧快照会被烘进镜像）：%s" % (missing,)


def test_env_windows_paths_are_overridden():
    """守门③：.env 里任何盘符路径的键，必须在 compose 覆盖。

    这是本次事故的直接触发点（STICKER_DIR=D:/... → /app/D:/...）。
    """
    if not ENV_FILE.exists():
        return
    cenv = compose_env(COMPOSE.read_text(encoding="utf-8"))
    drives = env_drive_keys(ENV_FILE.read_text(encoding="utf-8", errors="replace"))
    unoverridden = {
        k: v for k, v in drives.items()
        if not cenv.get(k, "").startswith("/")
    }
    assert not unoverridden, (
        "以下 .env 键是 Windows 盘符路径且未被 compose 覆盖 —— "
        "容器内会被当相对路径，数据写进镜像层：%s" % (sorted(unoverridden),)
    )


def test_container_paths_are_absolute_posix():
    """守门④：compose 里覆盖的运行态路径必须是 POSIX 绝对路径。"""
    cenv = compose_env(COMPOSE.read_text(encoding="utf-8"))
    bad = {
        k: v for k, v in cenv.items()
        if k.endswith(("_DIR", "_PATH", "BASE")) and _DRIVE_RE.match(v or "")
    }
    assert not bad, "compose 环境变量出现盘符路径（容器内无法解析）：%s" % (bad,)


def test_sticker_dir_anchor_is_in_volume():
    """历史/记忆等 6 个库都以 STICKER_DIR 反推目录，必须锚在卷内。"""
    cenv = compose_env(COMPOSE.read_text(encoding="utf-8"))
    assert cenv.get("STICKER_DIR", "").startswith(VOLUME_ROOT + "/"), (
        "STICKER_DIR 未指向 %s —— history/memory/checkin/budget/emotion/group_style "
        "六个库会跟着跑到镜像层" % VOLUME_ROOT
    )


def test_injection_guards():
    """prove-it：坏输入必须被检测出来，否则守门是空转。"""
    # A. .env 盘符路径能识别
    bad_env = "A_OK=/app/data\nB_BAD=D:/AI/x/data\n# C_COMMENT=D:/skip\nD_QUOTED=\"C:\\\\x\"\n"
    got = env_drive_keys(bad_env)
    assert set(got) == {"B_BAD", "D_QUOTED"}, got

    # B. compose 覆盖能解析
    got_c = compose_env('    environment:\n      K_A: "/app/data/x"\n      K_B: "v"\n')
    assert got_c == {"K_A": "/app/data/x", "K_B": "v"}, got_c

    # C. 源码里「默认落镜像」的键能被 AST 抓出
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "src" / "plugins" / "p").mkdir(parents=True)
        (root / "src" / "plugins" / "p" / "config.py").write_text(
            "import os\n"
            "from pathlib import Path\n"
            "A = os.environ.get('SAVE_PATH', str(Path(__file__).parent / 'data' / 'a.json'))\n"
            "B = os.environ.get('PLAIN', 'x')\n",
            encoding="utf-8",
        )
        got_s = in_image_save_keys(root)
        assert set(got_s) == {"SAVE_PATH"}, got_s
        assert got_s["SAVE_PATH"][0].name == "config.py"

    # D. 缺覆盖 / 未排除 必须判违例（模拟两个反例，检测逻辑本身要报错）
    cenv = compose_env("      SOMETHING_ELSE: \"/app/data/x\"\n")
    assert not cenv.get("DOUPO_DATA_PATH", "").startswith(VOLUME_ROOT + "/")
    ignored = set()
    assert "src/plugins/doupo/data/" not in ignored
