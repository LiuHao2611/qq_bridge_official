# qq_bridge_official 项目长期笔记

> 只留「踩过坑、别改回去」的口径。细节读 `docs/` 与 AGENTS.md 的变更记录。

## 1. 斗破源码问答 = 沙盒 agent 容器（2026-11-18 定稿）

用户原话：「直接搬一个完整的 agent 项目，运行在沙盒里……无论我怎么问他都能找到我想
要的东西，而且不会影响我的电脑文件风险」。

* 链路：主 bot 工具 `doupo_code_ask(question)` → `POST http://qq-doupo-agent:8765/ask`
  → 沙盒容器里的 agent（自己 grep/读文件/写 Python 算数）→ 带 `文件名:行号` 的答案。
  **整句传用户原话，别压成关键词。**
* **工具表里只有一个源码工具**：旧的 `doupo_code_explain` 已下线（模块 + 23 条测试保留），
  并存会让模型在「快但浅／慢但深」之间横跳。守门 `test_doupo_code_explain.py::
  test_module_is_off_the_chat_path`。
* 证明脚本：`docker exec -i qq-doupo-agent python - < tests/evidence/prove_agent_sandbox.py`
  （17 条边界）。`docker cp` 进 `read_only` 容器必失败，只走 stdin。
* **等待期不沉默**（2026-11-18 "他不理我" 事故）：`Tool.progress_text` +
  `ToolContext.notify` + `ToolContext.notified`，dispatch 在 handler 前发一句，
  每回合一次、发送失败不影响工具。目前只有 `doupo_code_ask` 声明了文案。
  证明：`tests/evidence/prove_agent_notice.py`。
* 实测：**20~40 秒 / 1.7万~4万 token**（4~8 次工具调用）。成本主因是**调用次数**
  （每轮重发整段历史，input:output ≈ 11:1），所以 `AGENT_MAX_TOOLS` 也是省钱阀。
* 详解：`docs/斗破源码问答沙盒_2026-11-17.md`（11 节，含 8 条踩坑）。

### 🚨 底座不许换回 smolagents
`smolagents 1.26` 的 `get_clean_message_list()` 只拷 role/content，**`assistant.tool_calls`
整个字段丢掉**，`tool_role_conversions` 又把 `TOOL_RESPONSE → USER` → 每轮历史被压回
文本 ReAct → 多轮必退化（`tool_calls=0`、`does not contain any JSON blob`、跑满步数）。
**不是模型不支持 FC**（同端点直测原生 tool_calls 通）。换 `openai-agents` 后同题一次通过。

### agent 四条硬约束（都有真机事故）
1. `read_only: true` 时 docker 不自动建挂载目标 → `/repo` 空壳。Dockerfile 必须 `mkdir -p`。
2. **一进程一事件循环**：每请求 `asyncio.run()` 会让第 2 个请求炸 `Event loop is closed`。
   用常驻线程 + `run_coroutine_threadsafe`。
3. **撞 `max_turns` = 答案全丢**（抛 `MaxTurnsExceeded`，无部分结果）→ 用
   `AGENT_MAX_TOOLS=10` 机械刹车，别靠提示词（写"控制 6 次"实测照样 11~14 次）。
4. **失败必须显式**：`openai` 客户端默认对 429 静默重试退避 → 上层"挂死 300 秒"。
   必须 `max_retries=0`；`/ask` 失败回 500 + 原因，绝不回空答案。

## 2. 🚨 容器运行态数据只许落挂载卷（2026-11-16 事故）

事故：`.env` 里 `STICKER_DIR` 是 Windows 盘符路径，`history` 用它反推 `data/` 基准，
Linux 下变成 `/app/D:/AI/.../data` → 六个 SQLite 库全写进**镜像层**，每次重建回滚。
用户原话：「我都说了不要把库放到容器里，每次都被你覆盖，那这机器人还怎么用」。

* **唯一判据**：`docker diff <container>` 里是否出现该文件。出现即错。
  「重启不丢」≠「重建不丢」。
* **会话态也算持久化**（爬塔进度曾只在内存，重建清零 → 用户质问）。凡是玩家期待
  回来还在的状态一律落盘，原子写（tmp + `os.replace` + `fsync`），失败抛错不静默。
* 新增持久化必须同时：① compose 覆盖到 `/app/data/...`；② `.dockerignore` 排除镜像内默认目录；
  ③ 纳入 `tests/test_runtime_data_paths.py`（AST 自动发现"默认落镜像"的存档键）。
* **容器内禁止盘符路径**：`*_DIR/*_PATH/*_BASE` 一律 `/app/...`。
* 改数据前先备份到 `D:/AI/manju_agent/_<用途>_<时间戳>/`，禁止就地覆盖。

已归位：6 个 SQLite 库 `/app/data/*.db`；斗破/修仙存档 `doupo.sqlite` `xiuxian.sqlite`
（旧 `*.json` 降级为一次性导入源 + 回滚备份，代码绝不改写）；`tower_session.json`
`weekly_session.json` `wm_cache/` `stickers/` `media_assets.json` `plans.json` 全在卷内。

### 门禁 `tests/test_user_data_persistence.py`（8 条）
改任何 `*Store`/存档读写前先跑。新增 Store 只能：落盘（原子写 + 失败抛错）或登记
`TRANSIENT` 并写清「丢了只损失一句话」。
* **R7「测试不许写生产数据」硬线**：`tests/` 里裸 `XxxStore()` 一律红。真实事故：
  `test_qq_button_merge` 把生产 `media_assets.json` 覆盖成 `TOKEN123`；
  `test_doupo_tower` 的 `TowerStore(ttl=1)` 抹掉玩家爬塔层数。确需只读生产要登记
  `READONLY_IN_TESTS` 并写理由。`tmp/evidence/*` 同样不许写生产（`proof001` 假条目事故）。
* 别反复 `docker cp src/. /app/src/`：会生成 `/app/src/src` 陈旧副本，R1/R3 红是真有第二份源码。

### 玩家存档已迁 SQLite（2026-11-17，别改回 JSON）
* 位置 `/app/data/{doupo,xiuxian}/*.sqlite`；一人一行（`players.data` 存完整 JSON，
  `lv/exp/gold/realm` 提成列）。
* **库路径只由 `DATA_PATH` 推导，绝不开第二个环境变量**（曾加 `DOUPO_DB_PATH` → 测试往
  生产库塞了 4 个假玩家）。守门 `test_db_path_follows_data_path_single_source`。
* 建表在 `_tx()` 前 `_ensure_ready()`（不能只放 `load()`）；`get_player()` 返回**活字典**，
  `put_player()` 单行 UPSERT，`save()` 是兜底 diff。**不许自动回退 JSON**，出错抛 `StoreError`。
  迁移门禁 `tests/test_game_save_sqlite.py` 11 条。

## 3. 官方通道与部署

* 原件 `D:/AI/manju_agent/qq_bridge`（NapCat）**永久只读**，改动只在 `qq_bridge_official`。
* 官方身份是 **openid**，经 `channel/official.py::pseudo_id()` md5 成**稳定负整数**。
  主人命令也认 ID：要把伪 ID 写进 `.env` 的 `BOT_OWNER_QQ`（如 `1743124119,-268041124`），
  否则 `_is_owner` 静默拒绝（日志 `ai_cmd_denied`），用户只看到「没反应」。
  ⚠️ `member_openid` 可能按群不同，换群要重新登记。
* 部署：`docker build -f docker/Dockerfile.bridge -t qq-bridge-official:latest .` 再
  `docker compose -f docker-compose.official.yml up -d --force-recreate`
  （`up -d --build` 会偶发 buildx bake exit 1）。健康 `docker ps --filter name=qq-bridge-official`。
* 官方 msg_type 互斥：0 文本／2 markdown+keyboard／7 图片。图片与按钮合并必须
  「素材上传 → 内嵌 markdown + keyboard 同段」，GIF 合不了。
  **绝不分离（用户硬要求）**：默认 `OFFICIAL_MERGE_FALLBACK_MODE=image_only`，
  合并不了就只发纯图片，不另发按钮条。异火：无传承宗门纯文本、无图无按钮。
* 被动回复 `msg_id + msg_seq` 最多 5 次、`msg_seq` 严格递增（`passive_seq` 共享计数）。
* 跑测试（镜像里没 pytest、也没 tests/）：挂宿主源码
  `docker run --rm -v "$(pwd -W)":/work -w /work -e QQ_DATA_DIR=/tmp/qdata <img> bash -lc
  "/opt/venv/bin/python -m pip install -q pytest pytest-timeout anyio && /opt/venv/bin/python -m pytest ..."`
  ⚠️ 默认 `python` 是系统解释器，必须显式 `/opt/venv/bin/python`。
* 全量 pytest 有 20 个 collection error（`sys.modules["nonebot"]` 被互相 stub），
  基线同样，**不是回归**；改完按文件隔离跑。

## 4. 🚨 GitHub 推送缓存必须用内容指纹（2026-11-16 事故）

* `tmp/push_via_api.py` 走 Git Data API 绕 HTTPS 502；blob 缓存 key **必须是
  `hashlib.sha1(raw)`**，绝不能用路径+长度（字节数不变会命中旧 sha，改动推不上去却报 PUSH_OK）。
* 全量 tree 会 422（644 条 "input too large"）→ 用 `tmp/push_incremental.py <本地基线commit>`
  走 `base_tree` 只提交 diff。
* **验收必须比对内容特征**：curl `raw.githubusercontent.com/.../main/<path>?t=<时间戳>`
  后 grep 本轮标志性字符串。只信 PUSH_OK = 假通过。别用 `git hash-object` 比 tree sha
  （autocrlf 会造「远端是旧内容」的假象）。

## 5. 战斗系统 / 爬塔 / 战斗回看

* **属性↔战斗强关联是硬要求**（用户原话：不能人物卡摆一堆数字、战斗只播成败）。
  六维作用唯一源 `config.STAT_BATTLE_ROLE`，人物卡/战报归因/cmd_panel 共用。
* 机缘落点 `core.hero_aid_rate()`：基础 0.3%、硬顶 2%，刻意压低不改 TTK/胜率；
  触发仍走正常结算，`res["hero_aid"]` 供文案标注。战报统一 `commands._battle_line()`，
  特殊 log 条目字段不齐**一律 `.get`**（回包 SENT=0 事故）。
* 人物卡「→战斗作用」短标有几何守门 `test_battle_depth.py::test_statcell_label_role_no_overlap`。
* PVP（无惩罚切磋）：两段式发起/应战，仅本群，发起方先手，`duel_wins/losses` 与 PVE 分账；
  `core.duel()` 是现成底座别重写。会话模块按 `(group,target)` 隔离——
  **matcher 变量名不许叫 `duel_challenge`**（遮同名子模块，已改 `duel_ask_cmd`）。
* 爬塔电梯奖励**只能用"水位线差值"**：`clear_reward(到达层) - clear_reward(tower_paid_floor)`，
  结算后抬线；绝不逐段相加，宝箱 skip `floor <= paid`。老档缺省 0 自然迁移。
* 周副本难度源头是**周一快照画像**（`monster_scaled(snapshot)`），一周内变强怪不变；
  无快照走 `ROOKIE_PROFILE(lv5)`。奖励先进 `staged`，通关第 7 层才 `weekly_commit` 入账；
  **失败/超时整段丢弃（含武器，用户硬要求）**，失败分支不许出现 `weekly_commit`。
* 完整过程只在「战斗回看」出卡；最后一场落 `p["last_battle"]`，唯一写入口
  `core.record_last_battle()`（遭遇/掠夺/切磋/爬塔/周副本五入口，切磋双方各一份）。
  `battle_review.py` 只把 log 变行不重算；评语 `battle_comments.py` 40 条，
  只嘲讽表现不攻击玩家，种子 = openid+ts+log_len。
* **菜单 / matcher 数严格相等（现 42 条）**：新增口令要同时改 `MENU_GROUPS`、
  `test_doupo_buttons`、`test_menu_full.CASES`、`test_commands`。
  `周本状态` 用 PRI-1 抢在 `周本` 之前。原著**没有七层塔**（七层是游戏化设计）。

## 6. 紫卡洗卡推荐（2026-11-16，别重造）

* 数据源链：WFCD `data/warframe_items/*.json`（`_fix_ips` 修 puncture/slash 互换）
  → `riven_disp.json` → `riven_stat_base.json` → `wfm_riven_attrs.json`（快照，
  **不许手写 slug 表**）→ `riven_recommend_attrs.py` 建索引。
* 两维度不可混用：击杀效率 = 裸面板边际 DPS；市场价差 = 同分位中位差。
* 价差三坑：① 整段比整段算出假比例（必须 `k=min(n)` 同分位）；② `negative_stats=X` 是
  3正1负完整卡，必须换基到负面池中位；③ 候选要 desc+base **双路频次**（只用 desc 漏「震地」）。
* WFM：UA 必须 `curl/8.4.0` + `Platform: pc`（Chrome UA 返 200 但 0 条）；15 次请求 ≈20s
  是上限。**算的必须画得下**（候选/行数/脚注计数三者相等）；卡面禁用 markdown。
  守门 `tests/test_riven_recommend_spread.py` 45 条。

## 7. 大模型 429 / 额度排查

* `llm.py` 的 429 只报「请求太频繁或额度不足」，**带不出 body** → 直连
  `POST {AI_API_BASE}/chat/completions` 看 `error.code`：`SetLimitExceeded` = 该模型
  安全体验额度到顶（换模型可绕）；`ModelNotOpen` = 未开通。`GET /api/v3/models` 列可用模型。
* **限额按模型独立计**（换 key 不解决别的模型）；根治要去方舟控制台调安全体验模式。
* `AGENT_MODEL` 可给源码问答单独分池；不配则跟随 `AI_MODEL`。
* 2026-11-18 起用 `glm-5-2-260617`（新账号唯一还有额度的池）。
