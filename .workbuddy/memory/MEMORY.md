# qq_bridge_official 项目长期笔记

> 只留「踩过坑、别改回去」的口径。细节读 `docs/` 与 AGENTS.md 变更记录。

## 0. 🚨 最高声明

> 「所有玩家数据必须保存到数据库，禁止存 JSON，这是最高声明」（2026-11-18 用户原话）

* 玩家数据一律进 SQLite。新玩法的持久化必须**建独立表 + 真实字段**，禁止塞 `players.data` 的 JSON blob。
* 新表同时：① 库路径只由 `DATA_PATH` 推导；② 原子写；③ 纳入 `test_user_data_persistence.py` / `test_game_save_sqlite.py`；④ 老档可迁移但代码不回退 JSON。
* 提交走 **GitHub MCP**，不用 git CLI push；仓库 `LiuHao2611/qq_bridge_official`，**别搞错成 `qq_bridge` 原件**。

## 1. 斗破源码问答 = 沙盒 agent 容器

用户原话：「搬一个完整的 agent 项目，运行在沙盒里……怎么问都能找到，且不影响电脑文件」。

* 链路：`doupo_code_ask(question)` → `POST http://qq-doupo-agent:8765/ask` → 沙盒 agent（自己 grep/读文件/算数）→ 带 `文件名:行号` 的答案。**整句传原话，别压成关键词。**
* **工具表只留一个源码工具**：`doupo_code_explain` 已下线（模块 + 23 测试保留），并存会让模型横跳。守门 `test_doupo_code_explain.py::test_module_is_off_the_chat_path`。
* 证明脚本 `docker exec -i qq-doupo-agent python - < tests/evidence/prove_agent_sandbox.py`（17 条）。`docker cp` 进 `read_only` 容器必失败，只走 stdin。
* **等待期不沉默**：`Tool.progress_text` + `ToolContext.notify/notified`，dispatch 在 handler 前发一句、每回合一次；真正文案取人设 `Persona.notice`（`persona.resolve_notice`）。证明 `tests/evidence/prove_agent_notice.py`。
* **每步都要日志**：agent 侧 `_StepLog(RunHooks)` 挂 `Runner.run(hooks=)`；工具入参只能在工具函数里打（hooks 的 `tool_input` 是 None）；`_log()` 必须加锁。bot 侧 `fc_round_start / fc_llm_done / tool_call_start / fc_tool_returned`。
* **答案禁 Markdown**（`#`/`**`/表格/列表），群聊发不出格式。
* 实测 20~40 秒 / 1.7~4 万 token；成本主因是**调用次数**（每轮重发历史，in:out≈11:1），`AGENT_MAX_TOOLS` 也是省钱阀。
* 详解 `docs/斗破源码问答沙盒_2026-11-17.md`。

### 🚨 底座不许换回 smolagents
`get_clean_message_list()` 只拷 role/content，**丢掉 `assistant.tool_calls`** → 每轮压回文本 ReAct → 多轮必退化（`tool_calls=0`、跑满步数）。不是模型问题（同端点直测原生 FC 通）。`openai-agents` 一次通过。

### agent 四条硬约束（都有真机事故）
1. `read_only: true` 时 docker 不建挂载目标 → `/repo` 空壳，Dockerfile 必须 `mkdir -p`。
2. **一进程一事件循环**：每请求 `asyncio.run()` 会让第 2 个请求炸 `Event loop is closed`。用常驻线程 + `run_coroutine_threadsafe`。
3. **撞 `max_turns` = 答案全丢**（无部分结果）→ 用 `AGENT_MAX_TOOLS=10` 机械刹车，别靠提示词。
4. **失败必须显式**：`openai` 默认对 429 静默重试 → 上层"挂死 300 秒"。必须 `max_retries=0`，`/ask` 回 500 + 原因，绝不回空答案。

## 2. 🚨 容器运行态数据只许落挂载卷（2026-11-16 事故）

事故：`.env` 的 `STICKER_DIR` 是 Windows 盘符路径，`history` 用它反推 `data/` → Linux 下变 `/app/D:/...` → 六个库全写进**镜像层**，每次重建回滚。用户原话：「我都说了不要把库放到容器里，每次都被你覆盖，那这机器人还怎么用」。

* **唯一判据**：`docker diff <container>` 是否出现该文件。出现即错。「重启不丢」≠「重建不丢」。
* **会话态也算持久化**（爬塔进度曾只在内存）。凡是玩家期待回来还在的状态一律落盘，原子写（tmp + `os.replace` + `fsync`），失败抛错不静默。
* 新增持久化必须同时：① compose 覆盖到 `/app/data/...`；② `.dockerignore` 排除镜像内默认目录；③ 纳入 `test_runtime_data_paths.py`（AST 自动发现"默认落镜像"的存档键）。
* **容器内禁止盘符路径**：`*_DIR/*_PATH/*_BASE` 一律 `/app/...`。改数据前备份到 `D:/AI/manju_agent/_<用途>_<时间戳>/`。

### 门禁 `tests/test_user_data_persistence.py`（8 条）
改任何 `*Store`/存档读写前先跑。新 Store 只能落盘（原子写 + 失败抛错）或登记 `TRANSIENT` 并写清「丢了只损失一句话」。
* **R7「测试不许写生产数据」硬线**：`tests/` 里裸 `XxxStore()` 一律红。事故：`test_qq_button_merge` 把生产 `media_assets.json` 覆盖成 `TOKEN123`；`test_doupo_tower` 的 `TowerStore(ttl=1)` 抹掉玩家层数。只读生产须登记 `READONLY_IN_TESTS` 并写理由。`tmp/evidence/*` 同样不许写生产。
* 别反复 `docker cp src/. /app/src/`：会生成 `/app/src/src` 陈旧副本。

### 玩家存档已迁 SQLite（别改回 JSON）
* `/app/data/{doupo,xiuxian}/*.sqlite`；一人一行（`players.data` 存完整 JSON，`lv/exp/gold/realm` 提成列）。
* **库路径只由 `DATA_PATH` 推导，绝不开第二个环境变量**（曾加 `DOUPO_DB_PATH` → 测试往生产库塞了 4 个假玩家）。守门 `test_db_path_follows_data_path_single_source`。
* 建表放 `_tx()` 前 `_ensure_ready()`；`get_player()` 返回**活字典**，`put_player()` 单行 UPSERT。**不许自动回退 JSON**，出错抛 `StoreError`。门禁 `test_game_save_sqlite.py` 11 条。

## 3. 官方通道与部署

* 原件 `D:/AI/manju_agent/qq_bridge`（NapCat）**永久只读**，改动只在 `qq_bridge_official`。
* 官方身份是 **openid** → `channel/official.py::pseudo_id()` md5 成稳定负整数。主人命令要把伪 ID 写进 `.env` 的 `BOT_OWNER_QQ`（如 `1743124119,-268041124`），否则 `_is_owner` 静默拒绝。⚠️ `member_openid` 可能按群不同，换群要重登记。
* 部署：`docker build -f docker/Dockerfile.bridge -t qq-bridge-official:latest .` → `docker compose -f docker-compose.official.yml up -d --force-recreate`（`up -d --build` 会偶发 buildx bake exit 1）。
* 官方 msg_type 互斥：0 文本／2 markdown+keyboard／7 图片。图片+按钮必须「素材上传 → 内嵌 markdown + keyboard 同段」，GIF 合不了。**绝不分离**：默认 `OFFICIAL_MERGE_FALLBACK_MODE=image_only`。异火：无传承宗门纯文本、无图无按钮。
* 被动回复 `msg_id + msg_seq` 最多 5 次、`msg_seq` 严格递增（`passive_seq` 共享计数）。
* 跑测试（镜像没 pytest 也没 tests/）：挂宿主源码 `docker run --rm -v "$(pwd -W)":/work -w /work -e QQ_DATA_DIR=/tmp/qdata <img> bash -lc "/opt/venv/bin/python -m pip install -q pytest pytest-timeout anyio && /opt/venv/bin/python -m pytest ..."`，Git Bash 下加 `MSYS_NO_PATHCONV=1`。⚠️ 必须显式 `/opt/venv/bin/python`。
* 全量 pytest 有 20 个 collection error（`sys.modules["nonebot"]` 互相 stub），基线同样**不是回归**，按文件隔离跑。

## 4. 🚨 推送缓存用内容指纹 + 行尾对齐

* `tmp/push_via_api.py` 走 Git Data API 绕 HTTPS 502；blob 缓存 key **必须是 `hashlib.sha1(raw)`**（路径+长度会命中旧 sha，改动推不上去却报 PUSH_OK）。
* 全量 tree 会 422（644 条 "input too large"）→ 用 `tmp/push_incremental.py <本地基线commit>` 走 `base_tree` 只提 diff。
* **验收必须比对内容特征**：curl `raw.githubusercontent.com/.../main/<path>?t=<ts>` 后 grep 本轮标志字符串。只信 PUSH_OK = 假通过。别用 `git hash-object` 比 tree sha（autocrlf 造假象）。
* 🚨 **行尾必须跟远端既有行尾对齐**：`core.autocrlf=true` 让工作区 CRLF、提交 LF，而 `push_incremental.py` 读磁盘字节 → `commands.py` 变「+2277/-2261」整文件重写。但**一刀切折 LF 也不行**（仓库混着 LF 与 CRLF）。脚本按「远端该文件已有行尾」输出（新文件 LF），推完看 `get_commit` 的 additions/deletions 是否等于本地 `git diff --stat`。

## 5. 战斗系统 / 爬塔 / 战斗回看

* **属性↔战斗强关联是硬要求**（不能人物卡摆一堆数字、战斗只播成败）。六维作用唯一源 `config.STAT_BATTLE_ROLE`。
* 机缘落点 `core.hero_aid_rate()`：基础 0.3%、硬顶 2%；触发走正常结算，`res["hero_aid"]` 供标注。战报统一 `commands._battle_line()`，特殊 log 条目字段不齐**一律 `.get`**（回包 SENT=0 事故）。
* 人物卡「→战斗作用」短标守门 `test_battle_depth.py::test_statcell_label_role_no_overlap`。
* PVP（无惩罚切磋）：两段式发起/应战，仅本群，发起方先手，`duel_wins/losses` 与 PVE 分账；`core.duel()` 是现成底座。会话模块按 `(group,target)` 隔离——**matcher 变量名不许叫 `duel_challenge`**（遮同名子模块，已改 `duel_ask_cmd`）。
* 爬塔电梯奖励**只能用"水位线差值"**：`clear_reward(到达层) - clear_reward(tower_paid_floor)`；绝不平逐段相加，宝箱 skip `floor <= paid`。
* 周副本难度源头是**周一快照画像**（`monster_scaled(snapshot)`）；奖励先进 `staged`，通关第 7 层才 `weekly_commit`；**失败/超时整段丢弃（含武器）**，失败分支不许出现 `weekly_commit`。
* 完整过程只在「战斗回看」出卡；最后一场落 `p["last_battle"]`，唯一写入口 `core.record_last_battle()`（遭遇/掠夺/切磋/爬塔/周副本五入口）。`battle_review.py` 只把 log 变行不重算；评语 `battle_comments.py` 40 条，只嘲讽表现不攻击玩家。
* **菜单 / matcher 数严格相等（现 47 条）**：改口令要同时改 `MENU_GROUPS`、`test_doupo_buttons`、`test_menu_full.CASES`、`test_commands`。`周本状态` 用 PRI-1 抢在 `周本` 前。原著**没有七层塔**（游戏化设计）。

### 🚨 爬塔难度只由层号决定（用户硬要求）
* 用户原话：「爬塔每层难度固定，不按玩家战力来」。`core.tower_ref_player(td.floor_ref_lv(n))` → `monster_scaled(ref, species)`；**禁止改回 `monster_scaled(fp, ...)`**（一升级同层怪同步变厚，原地跑）。
* `floor_ref_lv(n) = min(103, 1 + (n-1)*2)`；52 层封顶后靠 `floor_difficulty`。`affixes_for(n, rng=None)` 按层号定种子 → 同层同词缀。
* ⚠️ **高等级打低层就是不掉血**，这是正确行为；测「连爬掉血」必须用同级玩家。

### 自动调息
* 唯一源 `core.regen(p, elapsed)`；**挂载点唯一 `Store.get_player()`**（事故：挂在 `commands._player` 时按钮/卡片渲染绕过它，玩家点一上午不恢复）。
* ⚠️ `get_player` 必须**出锁后**再 `_maybe_regen`（同锁不可重入）；core 用 `importlib.import_module` 延迟取（避开包属性 `store` 被实例遮蔽）。
* 180 秒一 tick、气血 4%/斗气 6% 上限；基准只进 `regen_clock(qq,last_at)` 表。
* 三条不许破：只推进**整 tick 秒数**（余数留着）；**满状态照吃时间不攒 tick**；**首次见到只落基准**。
* 恢复量放模块级 `_LAST_REGEN`，**不许挂 `p["_regen"]`**（活字典会进存档）。疗伤丹 `pct`（取 max(固定值, 上限×pct)）；`_roll_drops` 的疗伤丹判定必须留在**所有既有判定之后**（rng 只追加不插队）。

### 战斗丰富化 + 天焚炼气塔
* **不控制回合**：15 回合上限已删，`HARD_ROUND_CAP=300` 只是脏数据熔断（按剩余血量比例判胜，`exhausted=True`，绝不静默判负）。⚠️ **战败盘 hp 重置成上限×0.7**，比胜者残血还高 —— 比残血的断言必须先钉「双方同胜负」。
* **自动服药**：<35% 气血 / <20% 斗气，取最小可用疗伤丹，每场各 2 枚，多敌共用一个 `pill_budget`。**深副本（爬塔/周本）必须 `auto_pill=False`**（`take_item` 只改 deepcopy，真实背包不减 = 文案与数据两张皮）。
* **背包锁定** `item_lock` 真实列，只拦自动服用。
* **怪物技能** `MONSTER_SKILLS` + `SKILL_BY_KIND`，效果**并入「兽击」那条 log 的 notes**，不许另插 dmg=0 条目（回看卡会算重）。
* **心火共鸣**递减：用 `fortune_round` 记发放回合，回合末 `rounds > fortune_round` 才减（不能在出手后、也不能当回合减）。
* **天焚炼气塔**：`alchemy_tower` 真实列；火能 1 点=10 分钟**只结算整块**（零头留 `last_ts`）；火毒触顶 100 强制出塔；斗灵(lv37)火毒减半。**塔中禁止「修炼」**。
* ⚠️ 命名坑：`tower_settle`/`cmd_tower_status` 与**爬塔**重名会被静默覆盖，新塔一律 `alchemy_*`/`cmd_furnace_*`。
* ⚠️ `code_explain` 符号概览：紧凑续列只留名字不带行号且**不按字符预算截断**（名字是 hard requirement）。

## 6. 紫卡洗卡推荐（别重造）

* 数据源链：WFCD → `riven_disp.json` → `riven_stat_base.json` → `wfm_riven_attrs.json`（快照，**不许手写 slug 表**）→ `riven_recommend_attrs.py`。
* 两维度不可混用：击杀效率 = 裸面板边际 DPS；市场价差 = 同分位中位差。
* 价差三坑：① 整段比整段算假比例（必须 `k=min(n)` 同分位）；② `negative_stats=X` 是 3正1负完整卡，要换基到负面池中位；③ 候选要 desc+base **双路频次**。
* WFM：UA 必须 `curl/8.4.0` + `Platform: pc`（Chrome UA 返 200 但 0 条）；15 请求≈20s 上限。**算的必须画得下**。守门 `test_riven_recommend_spread.py` 45 条。

## 7. 大模型 429 / 额度

* `llm.py` 的 429 带不出 body → 直连 `POST {AI_API_BASE}/chat/completions` 看 `error.code`：`SetLimitExceeded`=安全体验额度到顶（换模型可绕）；`ModelNotOpen`=未开通。`GET /api/v3/models` 列可用模型。
* **限额按模型独立计**；根治去方舟控制台调安全体验模式。`AGENT_MODEL` 可给源码问答单独分池。

## 8. 异火获取（A 方案，别改回去）

* 写 `p["flames"]` 只有两条：① `cfg.FACTION_FLAME_GRANT` 三处本宗异火在入道·转宗时发 1 级；② 帝炎走 `p["diyan"]`，**绝不进 `flames`**。
* `core.grant_faction_flame()` **只在一朵异火都没有时发放** → 老档补发、转宗刷不出来。改成每次都发 = 刷火器。
* `core.feed_flame()` 的 `cur <= 0 → reason="unowned"` **门禁必须留**（删掉就是「养火即收服」后门）。被拒不许扣药渣。
* 净莲妖火、焚炎谷九龙雷罡火**都不发**。文案统一「本宗异火」，禁写「传承」。
* 异火卡 `lv<=0` 必须显示「未收服」，且不显示 0/N 级与药渣需求。守门 `doupo/test_doupo_flame_grant.py` 17 条。

## 9. 🚨 斗破插件两条结构雷

* **包属性被子模块同名实例遮蔽**：`__init__.py` 里 `store = store_mod.STORE` 一旦这么写，别处 `from . import store` 拿到的是**实例**，再取 `.STORE` 就 AttributeError。规矩：**要成员就 `from .<mod> import <name>`**。实例名带 `_inst`。文字口令能跑、按钮点击必崩就是这个原因。
* **卡片渲染签名不统一**：多数 `render_*(p)`，但 `render_puppet(roster, elig, p)`、`render_ruin(state, day)`、`render_rank(top)`、`render_battle(last_battle)` 不是。`button_dispatch` 的通用 `renderer(p)` 只对 `(p)` 安全，首参不叫 `p` 的必须特判。守门 `test_doupo_puppet.py` + `test_doupo_buttons.py::test_card_renderer_shapes_match_dispatch`（AST）。
* **出图口令带结果时用 `finish_card(caption=…)`**：战报先发卡后发，别让卡吞掉结果（傀儡遗迹曾只发进度卡，胜负与掉落全丢）。

## 10. 一处输出 = 一条消息（用户明确要求）

* 「有结果的出图口令」**不许**拆成「文字 + 卡」两条。结果并进卡片本体：命令写进可选 `report` dict，`build_*_data(..., report)` 并成首段。
* `finish_card` 不允许再加 caption/分条发送类参数（加过被否）。
* `test_menu_full.py` 用例默认 `parts=1`，只有真需要多条才写 `parts=N`。
* 验收战斗类命令**必须用库副本**（`tmp/` 下复制，含 `-wal`），不许直接连生产库。

## 11. 丹药契约：desc=原著（考据），impl=本作（承诺）

* 事故：三纹青灵丹 desc 写「助斗师突破成大斗师」却 `effect="exp" value=1000`，玩家吃 8 枚永远破不了境。**desc 承诺 = 必须有一行代码兑现**（假药事故）。
* 2026-11-19 全表普查（75 枚）：只有 4 枚真按承诺兑现；50+ 枚承诺的是本作没有的系统（短时增益/升级成功率/封印/寿命/假死/火毒/化形/灵魂值/丹雷）。
* 契约：**卡面 / 包裹 /「炼药」文本只印 `cfg.recipe_impl(rec)`**；原著 desc 只在文本层标「原著：」，且仅当配方手写 `impl`。
* `impl` 三条来源：手写 `impl`（`RECIPE_IMPL_OVERRIDE`）→ breakthrough 自动拼 → `IMPL_BY_EFFECT` 模板。desc 带承诺词而 effect 非 breakthrough **必须手写 impl**，否则 `test_doupo_recipe_honesty` 红。
* 破境丹统一 `effect="breakthrough"` + `to_realm/star_chance/break_boundary/max_per_realm`；「一阶限 N 枚」落 `pill_use(id,qq,name,realm_idx,used_at)`（**不用复合主键**）。已接：三纹青灵丹、紫心破障丹、聚气散、斗灵丹。
* 🚨 测试里「今天」相关的键一律 `date.today().isoformat()`，**禁止硬编码日期**。
