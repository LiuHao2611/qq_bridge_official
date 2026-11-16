# qq_bridge_official 项目长期笔记

## 🚨 铁律：容器运行态数据只许落挂载卷（2026-11-16 事故后确立）

**事故**：`STICKER_DIR` 在 `.env` 里是 Windows 盘符路径（`D:/AI/.../data/stickers`），
`history._resolve_data_dir()` 用它反推 `data/` 基准目录。Linux 容器里 `dirname()` 得到
`D:/AI/.../data`，被当**相对路径**解析成 `/app/D:/AI/.../data` —— history / memory /
checkin / budget / emotion / group_style **六个 SQLite 库全部写进容器可写层（镜像层）**，
`docker build` / `compose up --recreate` 一次回滚一次。斗破/修仙存档另有同类问题
（默认写 `src/plugins/*/data/*.json`，被 `COPY src ./src` 烘进镜像）。

**用户原话**：`我都说了不要把库放到容器里，每次都被你覆盖，那这机器人还怎么用`

### 判定与做法
* **唯一判断标准**：`docker diff <container>` 里是否出现该文件。出现即「写进镜像层」，必错。
* **"重启不丢" ≠ "重建不丢"**。模块注释里写「重启不丢」不能作为安全依据。
* 任何新增持久化，必须同时满足：
  1. `docker-compose.official.yml` 里把路径覆盖到 `/app/data/...`（卷内绝对路径）；
  2. `.dockerignore` 排除其在镜像内的默认目录；
  3. 纳入 `tests/test_runtime_data_paths.py`（AST 自动发现 `os.environ.get(KEY, ...__file__...)`
     形式的"默认落镜像"存档键，缺覆盖即失败）。
* **容器内禁止出现盘符路径**。`*_DIR` / `*_PATH` / `*_BASE` 一律 `/app/...`。
* 改数据前先备份到 `D:/AI/manju_agent/_<用途>_<时间戳>/`，禁止就地覆盖。

### 当前已归位的路径
| 数据 | 容器路径 | 宿主路径 |
|---|---|---|
| 6 个 SQLite 库 | `/app/data/*.db` | `qq_bridge_official/data/` |
| 斗破存档 | `/app/data/doupo/doupo.json` | 同左（卷） |
| 修仙存档 | `/app/data/xiuxian/xiuxian.json` | 同左（卷） |
| warframe 缓存 | `/app/data/wm_cache` | 同左（卷） |
| 表情包库 | `/app/data/stickers` | 同左（卷） |

## 其他既有事实（沿用）
* 原件 `D:/AI/manju_agent/qq_bridge`（NapCat）**永久只读**，所有改动只在
  `qq_bridge_official`；从原件再同步会覆盖官方专属改造，需重新打补丁。
* 官方通道身份是 **openid**，与 NapCat 的 QQ 号不是同一命名空间，旧档不继承。
* 官方 msg_type 互斥：0=文本 / 2=markdown+keyboard / 7=富媒体图片；
  图片与按钮合并必须「素材上传 → 内嵌 markdown + keyboard 同段」，GIF 无法合并。
* 部署命令：`docker compose -f docker-compose.official.yml up -d --build`；
  健康检查 `docker ps --filter name=qq-bridge-official`。
