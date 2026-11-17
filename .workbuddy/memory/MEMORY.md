# qq_bridge_official 项目长期笔记

## 斗破源码问答 = 沙盒 agent 容器（2026-11-18 定稿，取代片段检索）

用户原话：**「你直接搬一个完整的agent项目吧，运行在沙盒里，docker应该可以满足，
这样无论我怎么问他都能找到我想要的东西，而且不会影响我的电脑文件风险」**

* 链路：主 bot 工具 `doupo_code_ask(question)` → `POST http://qq-doupo-agent:8765/ask`
  → 沙盒容器里的 openai-agents agent（自己 grep/读文件/写 Python 算数）→ 带
  `文件名:行号` 的中文答案。**整句传用户原话，别压成关键词。**
* **工具表里只有一个源码工具**。旧的 `doupo_code_explain` 已下线（模块+24 条测试保留），
  并存会让模型在「快但浅 / 慢但深」之间横跳，最后又退化成只给签名。
  `tests/test_doupo_code_explain.py::test_module_is_off_the_chat_path` 守这条。
* **底座必须是 openai-agents，不能是 smolagents**（实测证据见下方铁律）。
* 沙盒 17 条边界证明：`docker exec -i qq-doupo-agent python - < tests/evidence/prove_agent_sandbox.py`
  （只读挂载 / read_only 根 / uid 10001 / 无 .env / 无 docker.sock / 命令黑名单）。
  注意 `docker cp` 进 `read_only` 容器会失败（rootfs is marked read-only），只走 stdin。
* 详解文档：`docs/斗破源码问答沙盒_2026-11-17.md`（含 7 条真机踩坑）。
* 已知限制：**单次 40~90 秒**（深度换来的）；额度按模型独立计，容易一烧就光。

## 🚨 铁律：agent 底座不许换回 smolagents（2026-11-18 实证）

`smolagents 1.26` 的 `get_clean_message_list()` **只拷 role/content、
`assistant.tool_calls` 整个字段丢掉**，而 `tool_role_conversions` 把
`TOOL_RESPONSE → USER`。于是每轮历史都被压回「文本 ReAct」，模型看不到自己上一轮的
结构化调用，第二轮就退回纯文本吐 `Calling tools: [...]` → 解析失败 → 跑满步数。
真机 dump：`tool_calls=0` 恒成立、`msgs` 角色在 assistant→user 之间交替。
症状是「短问题能答、多轮必崩」，**别误诊成模型不支持 FC**——同端点直测原生
tool_calls 是通的。换 `openai-agents`（官方 SDK）后同题一次通过。

### agent 侧四条硬约束（都有真机事故背书）
1. **`read_only: true` 时 docker 不会自动建挂载目标目录** → 挂载静默不生效、`/repo` 空壳、
   agent 回「源码仓库不存在」。必须在 Dockerfile 里 `mkdir -p` 那 4 个目录。
2. **一个进程一个事件循环**（常驻线程 + `run_coroutine_threadsafe`）。每请求
   `asyncio.run()` 会让第 2 个请求炸 `Event loop is closed`（AsyncOpenAI 连接池绑循环）。
3. **`max_turns` 撞顶 = 答案全丢**（SDK 抛 `MaxTurnsExceeded`，没有部分结果）。
   所以要有 `AGENT_MAX_TOOLS=10` 的机械刹车逼它作答，不是靠调大 max_turns。
   提示词写「控制在 6 次」模型照样查到 11~14 次（最长 141s）。
4. **失败必须显式**：`openai` 客户端默认对 429 静默重试退避 → 上层看到"挂死 300 秒"。
   必须 `max_retries=0`；`/ask` 失败返回 500 + 原因，绝不返回空答案。

## AI 源码检索模块（tools/code_explain.py，已下线但保留）

用户硬要求：群里问斗破某功能设计，AI 要**能讲到实现细节**；且**不许"想一个模块改一次"**
（否决过中文别名表）。该模块现**不在聊天链路上**（2026-11-18 换成沙盒 agent），
保留作离线/排障工具与回归基线。改它之前先读这里：

* 工具面照搬 aider/grep-ast、repomix `--compress`、三家 code-index MCP 的收敛口径：
  **① outline（真实签名+行号+说明）→ ② body（完整函数体）→ ③ where（`文件:行号`→
  最内层符号+外层链）**。单工具三参数 `topic / file / symbol`。
* **解析后端是标准库 `ast`，不是 tree-sitter**：容器离线 wheel 构建，装不动
  tree-sitter-language-pack；白名单全 Python，`ast` 同构。零新增依赖是硬约束。
* **两件必须记住的事**（真机踩出来的）：
  1. **模块级数据常量必须进索引**。`FACTIONS` 1209 行、`POOL_UNLOCK`、`tower_data` 32 常量、
     `config` 90 常量。只索引 def/class 时 `symbol=POOL_UNLOCK` 直接没找到。
     `_collect(allow_data=)`：模块级/类体内收顶层赋值，**进函数体就关掉**。
     数据常量按行开窗；按行号跳读必须 `window()` **以目标行为中心**，否则整段首屏重复。
  2. **必须有机械刹车**。模型首轮已拿到答案仍继续查 6 轮 16 次，烧光
     `AI_FC_MAX_ROUNDS=8` 后一句都没答。
* **预算自己算账**：`_MAX_TOTAL_CHARS=5300`（registry 硬截 6000）+ `_clamp()` 按行边界切。
  概览两级（签名段→紧凑名单），保证 `core.py` 100 个符号**名字一个不漏**。
* 守门：`tests/test_doupo_code_explain.py` 24 条 + `tmp/prove_code_explain.py` 11 条反例。


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
* **会话态也算持久化**（2026-11-17 事故）：爬塔进度原先只在进程内存，重建容器就清零，
  用户直接质问「爬塔层数被你重置掉了」。凡是「玩家会期待回来还在」的状态一律落盘，
  不许用「反正是临时的」当借口。落盘路径跟 `doupo.json` 同目录（`config.TOWER_SESSION_PATH`），
  写盘必须原子（tmp + `os.replace` + `fsync`），失败要抛错而不是静默。
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
| 斗破存档（**SQLite**） | `/app/data/doupo/doupo.sqlite` | 同左（卷） |
| 修仙存档（**SQLite**） | `/app/data/xiuxian/xiuxian.sqlite` | 同左（卷） |
| 旧 JSON（**只读备份/导入源**） | `/app/data/doupo/doupo.json`、`xiuxian/xiuxian.json` | 同左（卷） |
| 爬塔会话 | `/app/data/doupo/tower_session.json` | 同左（卷） |
| 周副本会话 | `/app/data/doupo/weekly_session.json` | 同左（卷） |
| warframe 缓存 | `/app/data/wm_cache` | 同左（卷） |
| 表情包库 | `/app/data/stickers` | 同左（卷） |
| 素材映射（image_id 缓存） | `/app/data/media_assets.json` | 同左（卷） |
| 计划草稿 | `/app/data/plans.json` | 同左（卷） |

### 门禁：`tests/test_user_data_persistence.py`（8 条，2026-11-17 建）
改任何 `*Store` / 存档读写前先跑它。新增 Store 只有两条路：落盘（原子写 + 失败抛错
或 ERROR）或登记进 `TRANSIENT` 白名单并写清「丢了只损失一句话」的理由。
* **R7「测试不许写生产数据」是硬线**：`tests/` 里裸 `XxxStore()` 一律红。
  真实事故：`test_qq_button_merge` 的 LRU 用例把生产 `media_assets.json` 覆盖成
  `TOKEN123`；`test_doupo_tower` 的 `TowerStore(ttl=1)` 会抹掉玩家爬塔层数。
  要直连生产**只读**（如验真实人设库）必须登记 `READONLY_IN_TESTS` 并写明理由。
* `tmp/evidence/*` 证据脚本同样不许写生产：必须把 `DOUPO_TOWER_SESSION_PATH` 等
  环境变量指向 tmp（`proof001` 假条目就是这么混进生产档的，已清）。
* 别反复 `docker cp src/. /app/src/`：会生成 `/app/src/src` 陈旧副本，R1/R3 会红，
  那是真有第二份旧源码在容器里，不是误报。

### 玩家存档已迁 SQLite（2026-11-17，别改回 JSON）
* 位置：`/app/data/doupo/doupo.sqlite`、`/app/data/xiuxian/xiuxian.sqlite`；
  旧 `*.json` 降级为**一次性导入源 + 回滚备份**，代码绝不改写它。
* 结构：一人一行（`players`，`data` 列存完整 JSON，`lv/exp/gold/realm` 提成列便于查/排行）。
* **库路径只由 `DATA_PATH` 推导，绝不开第二个环境变量**（曾加 `DOUPO_DB_PATH`，
  compose 注入生产路径而测试只改 `DOUPO_DATA_PATH` → 跑测试往生产库塞了 4 个假玩家）。
  同一个东西只许有一个来源。门禁 `test_db_path_follows_data_path_single_source`。
* 建表在 `_tx()` 前自动 `_ensure_ready()`，不能只放 `load()`（e2e 会先 put_player）。
* 写法约定：`get_player()` 返回的仍是**活字典**（36 个调用点就地把改），
  `put_player()` 单行 UPSERT；`save()` 是兜底 diff（防"改了没 put"的未来写法）。
* **不许加自动回退到 JSON**：出错抛 `StoreError`。迁移门禁 `tests/test_game_save_sqlite.py` 11 条。
* 回滚：先用 sqlite 导出回 JSON，再 git 回退到迁移前的 commit（命令见 2026-11-17 日志）。

## 其他既有事实（沿用）
* 原件 `D:/AI/manju_agent/qq_bridge`（NapCat）**永久只读**，所有改动只在
  `qq_bridge_official`；从原件再同步会覆盖官方专属改造，需重新打补丁。
* 官方通道身份是 **openid**，与 NapCat 的 QQ 号不是同一命名空间，旧档不继承。
  openid 经 `channel/official.py::pseudo_id()` md5 映射成**稳定负整数**供 int 型业务层使用。
  **主人命令也一样认 ID**：官方通路要用 `/ai on` 等主人命令，必须把该负数伪 ID 一并写进
  `.env` 的 `BOT_OWNER_QQ`（如 `1743124119,-268041124`），否则 `_is_owner` 静默拒绝
  （日志 `ai_cmd_denied`），用户只会看到「没反应」。
  `config_api._validate_and_format` 的 intlist 校验已改为放行负整数（旧版 isdigit 会在配置页
  保存时抹掉负数 ID）；`Config._as_int_set` 与 dispatcher `is_owner` 均已支持负数，守门见
  `tests/test_config_api.py` / `tests/test_dispatcher.py`。
  ⚠️ `member_openid` 可能按群不同，换群需重新登记该群对应的伪 ID。
* 部署踩坑：`docker compose up -d --build` 偶发 `failed to execute bake: exit status 1`
  （buildx 0.38.1 / compose bake 兼容问题）。绕过：`docker build -f docker/Dockerfile.bridge
  -t qq-bridge-official:latest .` 后再 `docker compose -f docker-compose.official.yml up -d
  --force-recreate`，数据卷与 .env 挂载不变。
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
* **PVP 已实现（无惩罚切磋，2026-11-16）**：发起 → 应战/拒战两段式，仅本群，
  自动随机放技（不做同时回合报招），发起方先手，只更新 `duel_wins/duel_losses`，
  与 PVE `wins/losses` 分账。真实损失机制虽被认可但用户明确先不启用；不要赛季。
  `core.duel()` 是现成双人模拟器底座，别重写战斗内核。
  * 会话模块 `duel_challenge.py` 按 `(group,target)` 隔离、惰性超时；
    **matcher 变量名绝不能叫 `duel_challenge`**，会遮住同名子模块（已改 `duel_ask_cmd`）。
  * 菜单 / matcher 数严格相等（现 42 条），新增口令必须同时改 `MENU_GROUPS`、
    `tests/test_doupo_buttons.py` 基准、`test_menu_full.CASES`、`test_commands` 菜单数。

* **GitHub 全量 tree 推送会 422**：644 条一次 POST 偶发 "input too large"。
  用 `tmp/push_incremental.py <本地基线commit>` 走 `base_tree` 只提交 diff 文件，
  秒级完成；铁律不变——blob key 用内容 sha1，推送后拉 raw grep 特征。

## 爬塔/周副本口径（2026-11-17 确立，别改回）
* **电梯奖励去重只能用"水位线差值"**：`tower_settle` 发
  `clear_reward(到达层) - clear_reward(tower_paid_floor)`，结算后抬水位线。
  绝不逐段相加 `clear_reward`（会重复发低层）。宝箱也 skip `floor <= paid`。
  玩家档字段 `tower_paid_floor`/`tower_checkpoint` 老档缺省 0 自然迁移。
* **周副本难度源头是周一快照画像**：`monster_scaled(snapshot)` 反解怪，玩家用当前 p 打
  → 一周内变强怪不变（用户要的"提升机会"）。新入道无快照走固定 `ROOKIE_PROFILE(lv5)`。
  ISO 周 key（如 `2026-W47`）做时间锚。奖励先入会话 `staged`（武器只记名），
  通关第 7 层才 `weekly_commit` 一次性入账；失败/超时整段丢弃，绝不调 commit。
  ⚠ 失败含武器全清是用户硬要求，`cmd_weekly` 失败分支不许出现 `weekly_commit`。
* 菜单 42 条（含电梯/周副本/周本状态 + 战斗回看/战斗评价），matcher 数必须 == 菜单数（e2e 等量守门）。
  `周本状态` 用 PRI-1 抢在短口令 `周本` 之前命中。
* 原著**没有七层塔**，只有天焚炼气塔/试炼之塔九层；7 层是游戏化设计，文案别写"原著七层塔"。

## 完整战斗回看/战斗评价口径（2026-11-17 确立）
* 普通战斗回包继续只给 `_battle_highlights(limit=1)`，中间细节不膨胀；完整过程只在
  用户主动「战斗回看」时出卡。最后一场战斗统一落 `p["last_battle"]`（SQLite data JSON），
  唯一写入函数 `core.record_last_battle()`；五个入口：遭遇/掠夺/切磋/爬塔/周副本。
  切磋双方各留一份；爬塔/周本传**真实玩家 p**，不传战斗深副本。
* `fight()`/`duel()` 的 log 增补 `move/round/hp_before/hp_after/mp_before/mp_after/
  mp_cost/foe_hp_before/foe_hp_after`；特殊条目字段仍不齐，渲染一律 `.get`。
* 数据源分层：`battle_review.py` 只把 log 变行/文本兜底/标签，不重算数值；
  `battle_comments.py` 40 条毒舌评语（24 短 + 16 长），原创改写并带 source，
  只嘲讽战斗表现、不攻击玩家；同场用 openid+ts+log_len 做种子保证稳定。
* **大模型 429 先拉原始 body（2026-11-17 教训）**：llm.py 的 429 分支只报
  「请求太频繁或额度不足」，不带服务端 body，**看日志分不清额度还是频率**。
  直连 `POST {AI_API_BASE}/chat/completions` 看 `error.code`：
  `SetLimitExceeded` = 账户「安全体验模式」该模型用量到顶（服务被暂停，换模型可绕）；
  `ModelNotOpen` = 该模型未开通。`GET /api/v3/models` 可列全部可用模型 + `api_supported`。
  **限额按模型独立计**，换模型只是换通道；根治要去方舟控制台关/调安全体验模式。
  实测清单（2026-11-17，512 tokens）：`deepseek-v4-1-flash-261017` 2.6s 最快且 FC 正常（现用）；
  `doubao-seed-2-0-lite-260428` 8.0s；`glm-5-3-flash-260828` 4.9s；
  `doubao-seed-2-1-pro-260915` 默认思考 64.8s（群聊不可用，关思考仍 11s）；
  `doubao-seed-2-1-lite/turbo` 均已 429；`kimi-k3-1-pro` 未开通。
* **AI 群聊「斗破源码讲解」工具 `doupo_code_explain`（2026-11-17）**：群里玩斗破时问
  「某功能怎么设计的」，模型自动读源码+设计文档讲清楚。**safe 权限（群聊可用）**，
  纯只读（白名单 `src/plugins/doupo/*.py` 排 test/_gen/_verify/_throttle + `pic/templates/doupo_*.py`
  + `docs/斗破*.md`；路径安全拒绝绝对/盘符/../反斜杠；模块源码无任何写操作）。
  讲解时**不主动声明「只读/不能改」**（用户硬要求）——安全在代码层，不在话术。
  守门 `tests/test_doupo_code_explain.py`（9 条，tmp 树隔离，绝不读生产）+ `tmp/prove_code_explain.py`
  四反例。相关度打分：文件名×20 / 标题×6 / 正文封顶（初版按 count 累加被长文件 commands.py 刷分，
  设计文档被挤出前三——别改回）。**部署坑**：Dockerfile 只 COPY src/tools，不含 docs/；
  已加 `COPY docs/斗破*.md ./docs/` + `.dockerignore` 的 `docs/*` 放行 `!docs/斗破*.md`，
  改白名单加 docs 路径时务必同步确认镜像真的带该文件。
* 卡片 `doupo_battle` 复用 help 单列版式（sword 图标 + boxes 自检），
  `card_key="battle"` → `CARD_ACTIONS["battle"]=("战斗评价",)`；
  官方通道图片+按钮同条发送，素材上传失败仍 image_only，绝不分离按钮。
* 守门：`tests/test_battle_review.py`（35 条）+ `tmp/prove_battle_review.py` 四条反例；
  新增口令后菜单/matcher 42 条，typography 新卡必须实图查看后再交付。

## 紫卡洗卡推荐卡口径（2026-11-16 确立，别重造）
* **数据源唯一链**：WFCD `data/warframe_items/*.json`（面板，含 puncture/slash 互换 bug，
  `_fix_ips` 修）→ `riven_disp.json`（disp/type，执法者 1.2）→ `riven_stat_base.json`
  （满 roll，×disp）→ `wfm_riven_attrs.json`（`/v2/riven/attributes` 50 条快照，
  **不许再手写 slug 表**）→ `riven_recommend_attrs.py` 建索引。
* **两个维度不可混用**：击杀效率 = 裸面板边际 DPS；市场价差 = 同分位中位差。卡面并列但不能互相推导。
* **价差三个坑（都踩过）**：① 整段比整段会算出 1/3 的假比例，必须 `k=min(n)` 同分位；
  ② `negative_stats=X` 返回 3正1负完整卡（1000p vs 全量 468p），必须换基到负面池中位；
  ③ 候选要用 desc+base **双路频次**，只用 desc 会漏「震地」这类高价卡不常见的词条。
* **WFM 请求**：UA 必须 `curl/8.4.0` + `Platform: pc`（Chrome UA 返 200 但 0 条）；
  15 次请求 ≈ 20s 是上限，16+ 触发 429（retry-after 60~117s）。
* **算的必须画得下**：后端候选数与模板行数、脚注计数三者要相等（曾 9/8/9 三处不一致）。
  脚注「有效词条」排除致命项。卡面文案禁用 markdown（`**` 会画成字面星号）。
* 守门 `tests/test_riven_recommend_spread.py` 45 条（A~E 五段），改口径先跑它。
