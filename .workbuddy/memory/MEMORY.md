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
* **图片+按钮绝不分离（2026-11-16 用户硬要求）**：`official.py` 的
  `OFFICIAL_MERGE_FALLBACK_MODE=image_only`（默认）——素材上传失败合并不了时只发纯图片、
  绝不另发按钮条。想回退两条设 `two_messages`。异火：无传承宗门纯文本一句、无图无按钮
  （`commands.has_tradition_flame`），有传承才出完整面板。

## 🚨 铁律：GitHub API 推送缓存必须用内容指纹（2026-11-16 事故后确立）
* `tmp/push_via_api.py` 走 Git Data API 绕过 git HTTPS CONNECT 502。
  blob 缓存 key **必须**是 `hashlib.sha1(raw)`（内容指纹），**绝不能用路径+长度**——
  改动前后字节数碰巧不变会命中旧 sha，改动推不上去但脚本仍报 PUSH_OK。
* **验收远端必须比对内容特征**：curl `raw.githubusercontent.com/<owner>/<repo>/main/<path>?t=<时间戳>`
  后 grep 本轮改动的**标志性字符串**（如新函数名/开关名）。**只信 PUSH_OK = 假通过**。
  ⚠️ 别用 `git hash-object` 对比 GitHub tree 的 blob sha——autocrlf 会把工作区 LF 转成
  CRLF 算 sha，与脚本 POST 的 LF raw 得到的 GitHub sha 必然不同，会产生"远端是旧内容"的假象。
  内容特征 grep 才是唯一可靠判据（加 `?t=时间戳` 破 CDN 缓存）。
* 缓存污染时 `echo {} > tmp/blob_sha.json` 清空全量重推（642 文件约 15 分钟），别做"迁移"。

* 部署命令：`docker compose -f docker-compose.official.yml up -d --build`；
  健康检查 `docker ps --filter name=qq-bridge-official`。

## 战斗系统口径（2026-11-16 确立）
* **属性↔战斗强关联是硬要求**（用户原话：不能人物卡摆一堆数字、战斗只播成败）。
  六维战斗作用**唯一源**是 `config.STAT_BATTLE_ROLE`，人物卡 / 战报归因 / cmd_panel
  三处共用；改这一处即可同步，别在三处各写一份文案。
* 机缘的战斗落点 = `core.hero_aid_rate()` / `roll_hero_aid()`，开场掷一次，基础 0.3%、
  硬顶 2%——**刻意压低以免改变 TTK/胜率**，不许为了"彩蛋好看到处触发"而调高。
  触发时一招制敌、毫发无伤，仍走正常胜负结算，`res["hero_aid"]` 供文案标注因果。
* 战报内联归因统一走 `commands._battle_line()`。dot / reflect / miss / taken / aid
  等特殊 log 条目**字段不齐，一律 `.get`**，禁止硬取 `it['crit']`（2026-11-16 回包 SENT=0 事故）。
* 人物卡六维格子右端的「→战斗作用」短标有几何守门：
  `test_battle_depth.py::test_statcell_label_role_no_overlap` 用模板同一套 text_width
  校验 label+role 不超过 `cell_w-40`。改战斗作用文案前先看这条会不会红。
* **PVP 已拍板方向**：无惩罚切磋；自动随机放技（不做同时回合报招）；仅本群；
  不要赛季；真实损失机制虽被认可但先不启用。`core.duel()` 是现成双人模拟器底座，
  别重写战斗内核；切磋只更新荣誉战绩，不扣血不掉钱。
