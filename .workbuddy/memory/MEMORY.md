# qq_bridge_official 项目长期笔记

> 只留「踩过坑、别改回去」的口径 + 守门测试名。细节读 `docs/` 与 AGENTS.md 变更记录。

## 0. 🚨 最高声明
「所有玩家数据必须保存到数据库，禁止存 JSON，这是最高声明」（2026-11-18）。新玩法持久化必须**建独立表 + 真实字段**，禁塞 `players.data` JSON blob；库路径只由 `DATA_PATH` 推导；原子写；纳入 `test_user_data_persistence.py` / `test_game_save_sqlite.py`；老档可迁移但代码不回退 JSON。
提交走 **GitHub MCP / API 增量推送**，仓库 `LiuHao2611/qq_bridge_official`，**别搞错成 `qq_bridge` 原件**（永久只读）。

## 1. 斗破源码问答 = 沙盒 agent 容器
链路 `doupo_code_ask(question)` → `POST http://qq-doupo-agent:8765/ask` → 沙盒 agent 自己 grep/读文件 → 带 `文件名:行号` 的答案。**整句传原话，别压成关键词。**
* 工具表只留一个源码工具：`doupo_code_explain` 已下线（模块+23测试保留），并存让模型横跳。守门 `test_doupo_code_explain.py::test_module_is_off_the_chat_path`。
* 底座锁 **openai-agents，禁换回 smolagents**：其 `get_clean_message_list()` 丢 `assistant.tool_calls` → 每轮压回文本 ReAct → 多轮必退化。
* 等待期不沉默：`Tool.progress_text` + `ToolContext.notify/notified`，handler 前发一句、每回合一次，文案取人设 `Persona.notice`。证明 `tests/evidence/prove_agent_notice.py`。
* 每步日志：agent 侧 `_StepLog(RunHooks)` 挂 `Runner.run(hooks=)`，工具入参只能在工具函数里打，`_log()` 加锁；bot 侧 `fc_round_start/fc_llm_done/tool_call_start/fc_tool_returned`。
* 答案**禁 Markdown**（`#`/`**`/表格/列表）。实测 20~40s/1.7~4万token，成本主因调用次数，`AGENT_MAX_TOOLS` 是省钱阀。
* 四条硬约束（真机事故）：① `read_only:true` 时 docker 不建挂载目标 → Dockerfile 必须 `mkdir -p /repo`；② **一进程一事件循环**，禁每请求 `asyncio.run()`（第2请求炸 Event loop is closed），用常驻线程 + `run_coroutine_threadsafe`；③ 撞 `max_turns` 答案全丢 → `AGENT_MAX_TOOLS=10` 机械刹车；④ 失败显式：`max_retries=0`，`/ask` 回 500+原因，**绝不回空答案**（openai 默认对 429 静默重试 → 挂死 300s）。
* 证明 `docker exec -i qq-doupo-agent python - < tests/evidence/prove_agent_sandbox.py`（17条）。`docker cp` 进 read_only 容器必失败，只走 stdin。详解 `docs/斗破源码问答沙盒_2026-11-17.md`。

## 2. 🚨 容器运行态数据只许落挂载卷（2026-11-16 事故）
事故：`.env` 的盘符路径反推 `data/` → Linux 下 `/app/D:/...` → 六个库写进镜像层，重建回滚。唯一判据：`docker diff <container>` 出现该文件即错（「重启不丢」≠「重建不丢」）。
* 会话态也算持久化（爬塔进度曾只在内存）。一律原子写（tmp+`os.replace`+fsync），失败抛错不静默。
* 新增持久化同时：① compose 覆盖 `/app/data/...`；② `.dockerignore` 排除镜像内默认目录；③ 纳入 `test_runtime_data_paths.py`（AST 发现默认落镜像的存档键）。
* **容器内禁止盘符路径**：`*_DIR/*_PATH/*_BASE` 一律 `/app/...`。改数据前备份到 `D:/AI/manju_agent/_<用途>_<时间戳>/`。别反复 `docker cp src/. /app/src/`（生 `/app/src/src` 陈旧副本）。
* 门禁 `test_user_data_persistence.py`（8条）：**R7 测试里裸 `XxxStore()` 一律红**（曾覆盖生产 `media_assets.json`/抹玩家层数）；只读生产须登记 `READONLY_IN_TESTS` 写理由；`tmp/evidence/*` 同样不许写生产。
* 存档已迁 SQLite `/app/data/{doupo,xiuxian}/*.sqlite`：一人一行，`lv/exp/gold/realm` 提成列。**库路径只由 `DATA_PATH` 推导，绝不开第二个环境变量**（曾加 `DOUPO_DB_PATH` → 测试塞 4 假玩家），守门 `test_db_path_follows_data_path_single_source`。建表在 `_tx()` 前 `_ensure_ready()`；`get_player()` 返回**活字典**，`put_player()` 单行 UPSERT；禁自动回退 JSON，出错抛 `StoreError`。门禁 `test_game_save_sqlite.py` 11条。

## 3. 官方通道与部署
* 官方身份 **openid** → `channel/official.py::pseudo_id()` md5 成稳定负整数；主人伪 ID 写 `.env` 的 `BOT_OWNER_QQ`（如 `1743124119,-268041124`），否则 `_is_owner` 静默拒绝。⚠️ `member_openid` 按群不同，换群重登记。
* 部署：`docker build -f docker/Dockerfile.bridge -t qq-bridge-official:latest .` → `docker compose -f docker-compose.official.yml up -d --force-recreate`（`up -d --build` 偶发 buildx bake exit 1）。
* msg_type 互斥：0文本／2markdown+keyboard／7图片。图片+按钮须「素材上传 → 内嵌 markdown + keyboard 同段」，GIF 合不了。**绝不分离**：默认 `OFFICIAL_MERGE_FALLBACK_MODE=image_only`。异火：无传承宗门纯文本、无图无按钮。
* 被动回复 `msg_id+msg_seq` 最多5次、`msg_seq` 严格递增（passive_seq 共享计数）。
* 测试（镜像没 pytest/tests）：`docker run --rm -v "$(pwd -W)":/work -w /work -e QQ_DATA_DIR=/tmp/qdata <img> bash -lc "/opt/venv/bin/python -m pip install -q pytest pytest-timeout anyio && /opt/venv/bin/python -m pytest ..."`。Git Bash 加 `MSYS_NO_PATHCONV=1`，必须显式 `/opt/venv/bin/python`。全量 pytest 有 20 collection error（nonebot stub 互相覆盖）是基线，按文件隔离跑。

## 4. 🚨 推送：内容指纹 + 行尾对齐
* `tmp/push_via_api.py` 走 Git Data API 绕 HTTPS 502；blob 缓存 key **必须 `hashlib.sha1(raw)`**（路径+长度会命中旧 sha 却报 PUSH_OK）。
* 全量 tree 422（644 条 input too large）→ `tmp/push_incremental.py <本地基线commit>` 走 `base_tree` 只提 diff。大文件 contents API 会 `IncompleteRead`（AGENTS.md 430KB）→ 分块读 + 捕获保留 `partial`。
* **验收必须比对内容特征**（curl raw 或 contents API 后 grep 本轮标志串），只信 PUSH_OK = 假通过。别用 `git hash-object` 比 tree sha（autocrlf 造假象）。
* 🚨 **行尾跟远端该文件既有行尾对齐**：`core.autocrlf=true` 工作区 CRLF、提交 LF，push_incremental 读磁盘字节 → 整文件重写。但一刀切折 LF 也不行（仓库混行尾）。脚本按远端已有行尾输出（新文件 LF），推完看 get_commit add/del 是否等于本地 `git diff --stat`。

## 5. 战斗系统 / 爬塔 / 战斗回看
* 属性↔战斗强关联（六维作用唯一源 `config.STAT_BATTLE_ROLE`）。机缘 `core.hero_aid_rate()` 基础0.3%硬顶2%，触发走正常结算 `res["hero_aid"]` 供标注。战报统一 `commands._battle_line()`，特殊 log 字段不齐**一律 `.get`**（回包 SENT=0 事故）。人物卡「→战斗作用」短标守门 `test_battle_depth.py`。
* PVP 两段式发起/应战，仅本群，发起方先手，`duel_wins/losses` 与 PVE 分账；会话按 `(group,target)` 隔离；**matcher 变量名禁叫 `duel_challenge`**（遮同名子模块，用 `duel_ask_cmd`）。
* 爬塔电梯奖励**只用水位线差值** `clear_reward(到达层)-clear_reward(tower_paid_floor)`，禁逐段相加，宝箱 skip `floor<=paid`。
* 🚨 **爬塔难度只由层号决定**（用户硬要求）：`core.tower_ref_player(td.floor_ref_lv(n))` → `monster_scaled(ref,species)`，**禁改回 `monster_scaled(fp,...)`**（升级同层怪变厚原地跑）。`floor_ref_lv(n)=min(103,1+(n-1)*2)`，52层后靠 `floor_difficulty`；`affixes_for(n,rng=None)` 按层号定种子。⚠️ 高等级打低层不掉血是正确行为，测连爬掉血用同级玩家。
* 周副本难度源头**周一快照画像** `monster_scaled(snapshot)`；奖励先进 `staged`，通关第7层才 `weekly_commit`；**失败/超时整段丢弃（含武器）**，失败分支禁出现 `weekly_commit`。
* 完整过程只在「战斗回看」出卡；最后一场落 `p["last_battle"]`，唯一写入口 `core.record_last_battle()`（遭遇/掠夺/切磋/爬塔/周副本五入口）。`battle_review.py` 只把 log 变行不重算；评语 `battle_comments.py` 40条只嘲讽表现不攻击玩家。
* 回看卡必须是**结算界面**（胜负徽标/终局资源条/KPI/输出条形图/关键节点/逐招），禁退回日志打印稿。逐招明细**一回合一个气泡、左右分列我方/敌方、数值右锚定**；多敌回合用 `display_rounds()` 拉平（foe 变化或 round 变小判换段）；新事件 `exhausted/auto_pills/fortune` 必须落 `last_battle.summary`。守门 `test_battle_review.py` 211条 + `tools/mutate_battle_review.py`（38组变异全红）+ 几何 check_boxes + 肉眼截图。
* **超长必须分页（用户 2026-11-19「超长就分页」）**：`doupo_battle.split_pages()` 用 `_bubble_plan` 实测高度切页（`card_height()` 与 build 的 h 同式，禁「回合数×估计值」），`PAGE_MAX_H=2600`/`MAX_PAGES=5`；切点只在回合边界，首页留结算区、续页只留徽标+逐招，副标题「第 x/N 页 · 回合 a-b」，每页都带 tip。贪心后**二分容量均匀填充**（否则末页只剩一回合）；超上限按回合均分 + `page["capped"]`，**一回合都不许丢**（禁尾巴堆末页=39,937px 巨图）。多页仍是**一条消息**：官方 `Outgoing(images=[...])` 走既有 merge 内嵌多图（按钮照挂），NapCat `matcher.finish(*segs)`；`finish_card`/`send_card_via_channel` 收 `bytes|list[bytes]`，单页逐字节不变。`pic.split_pages()` 是唯一入口（`_PAGE_SPLITTERS`），渲染层不复制高度常量。
* 🚨 **战斗终局只有一个数**：`fight()` 存档改写前算 `downed=int(hp)<=0`，`hp_end = max(1,hp) if win else max(1,int(体力×cfg.LOSE_HP_FLOOR))`，`p["hp"]=hp_end` 且 `res["hp_end"]` 同值 → 存档/结算/回看/人物卡/逐招末行五处同源（用户 2026-11-19「回看 0 血、人物卡有血」就是各写各的）。**战败不归零，保底 5%**（`LOSE_HP_FLOOR=0.05`，用户「留百分之五就行」）；`LOSE_HP_ON_DEFEAT=0.30` 自此只给斗帝进阶失败的经验惩罚，禁接回战败气血。`downed` 时把 log 尾部 `hp_after<=0` 倒序回填成 `hp_end`，否则逐招末行印 0、终局印 832。
* 收场文案六态（禁「死/战败」死亡向）：`kill 击杀对手／exhausted_win 力竭判胜／exhausted_lose 力竭判负／outmatched 甘拜下风／rescued 被小医仙所救／fled 落荒而逃`；外部调研（篝火失败设计、战败CG、DiGRA 复活动画）结论=叙事化收场比判死挫败感低。终局条**归零=空条**、5% 是一小截（`0<n0<n1<n2`，条宽进盒名 `n{i}_bar{px}`）。`battle_review` 零依赖不 import config，用镜像 `LEGACY_HP_FLOOR`（门禁断言与 cfg 相等）+ `_LEGACY_END` 把旧档 `hp_zero/lose` 换成新代号并按 5% 换算，旧档无 `end_reason` 整块不画。
* ⚠️ 别拿「战后 `p["hp"]` 比战前高」当自动服药证据（`test_auto_pill_on_low_hp` 曾这么写）：战败盘走保底 5%，比战前残血还低是正确行为。判据改看 log 里「服药」条 `amount>0` + 该条 `hp_after` 高于前一条快照。
* 每回合战损：气泡抬头「我受 X · 敌受 Y」、底部「余血/余气 · 敌血」（`hp_after` 必须 `max(0,·)`，致死那击 core 写负数）；动作下伤害构成「面板 X · 防御 -Y（· 技能 -Z）」，**不重复印实收**，靠 `wrap_cjk` 折行不用 `_fit` 截断。
* **菜单/matcher 数严格相等（现47条）**：改口令同步 `MENU_GROUPS`、`test_doupo_buttons`、`test_menu_full.CASES`、`test_commands`。`周本状态` 用 PRI-1 抢在 `周本` 前。原著**没有七层塔**（游戏化设计）。
* 自动调息唯一源 `core.regen(p,elapsed)`，**挂载点唯一 `Store.get_player()`**（挂 `commands._player` 时按钮/卡片渲染绕过 → 点一上午不恢复）；`get_player` **出锁后**再 `_maybe_regen`（同锁不可重入），core 用 `importlib.import_module` 延迟取。180s/tick，气血4%/斗气6%上限，基准进 `regen_clock(qq,last_at)` 表。三条不许破：只推进整 tick 秒数（余数留着）；满状态照吃时间不攒 tick；首次见只落基准。恢复量放模块级 `_LAST_REGEN` **禁挂 `p["_regen"]`**（活字典进存档）。疗伤丹 `pct` 取 max(固定值,上限×pct)；`_roll_drops` 疗伤丹判定留所有既有判定之后（rng 只追加）。
* **不控制回合**：15回合上限已删，`HARD_ROUND_CAP=300` 只脏数据熔断（按剩余血量比例判胜 `exhausted=True`，禁静默判负）。⚠️ 战败盘 hp 重置上限×0.7，比胜者残血高——比残血断言先钉「双方同胜负」。
* 自动服药 <35%血/<20%斗气，取最小疗伤丹，每场各2枚，多敌共用 `pill_budget`；**深副本（爬塔/周本）必须 `auto_pill=False`**（`take_item` 只改 deepcopy，真实背包不减=两张皮）。背包锁定 `item_lock` 真实列只拦自动服用。怪物技能 `MONSTER_SKILLS`+`SKILL_BY_KIND`，效果**并入「兽击」那条 log 的 notes**，禁另插 dmg=0 条目（回看卡算重）。心火共鸣递减用 `fortune_round` 记发放回合，回合末 `rounds>fortune_round` 才减。
* 天焚炼气塔 `alchemy_tower` 真实列；火能1点=10分钟**只结算整块**（零头留 `last_ts`）；火毒触顶100强制出塔；斗灵(lv37)火毒减半；**塔中禁「修炼」**。⚠️ 命名：`tower_settle`/`cmd_tower_status` 与爬塔重名被静默覆盖，新塔一律 `alchemy_*`/`cmd_furnace_*`。`code_explain` 符号概览紧凑续列只留名字不带行号且**不按字符预算截断**。

## 6. 紫卡洗卡推荐（别重造）
数据源链 WFCD → `riven_disp.json` → `riven_stat_base.json` → `wfm_riven_attrs.json`（快照，**禁手写 slug 表**）→ `riven_recommend_attrs.py`。两维度不可混用：击杀效率=裸面板边际 DPS，市场价差=同分位中位差。价差三坑：① 整段比整段算假比例（必须 `k=min(n)` 同分位）；② `negative_stats=X` 是3正1负完整卡，换基到负面池中位；③ 候选要 desc+base 双路频次。WFM UA 必须 `curl/8.4.0`+`Platform: pc`（Chrome UA 返 200 但 0 条），15请求≈20s。**算的必须画得下**。守门 `test_riven_recommend_spread.py` 45条。

## 7. 大模型 429 / 额度
`llm.py` 429 带不出 body → 直连 `POST {AI_API_BASE}/chat/completions` 看 `error.code`：`SetLimitExceeded`=安全体验额度到顶（换模型可绕），`ModelNotOpen`=未开通。`GET /api/v3/models` 列可用。**限额按模型独立计**；根治去方舟控制台调安全体验模式。`AGENT_MODEL` 可给源码问答单独分池。

## 8. 异火获取（A 方案，别改回去）
写 `p["flames"]` 只两条：① `cfg.FACTION_FLAME_GRANT` 三处本宗异火在入道·转宗发1级；② 帝炎走 `p["diyan"]` **绝不进 `flames`**。`core.grant_faction_flame()` **只在一朵异火都没有时发放**（老档补发、转宗刷不出，改成每次=刷火器）。`core.feed_flame()` 的 `cur<=0 → reason="unowned"` **门禁必须留**（删=「养火即收服」后门），被拒不扣药渣。净莲妖火、焚炎谷九龙雷罡火**都不发**。文案统一「本宗异火」禁写「传承」。异火卡 `lv<=0` 显示「未收服」且不显 0/N 与药渣需求。守门 `doupo/test_doupo_flame_grant.py` 17条。

## 9. 🚨 斗破插件两条结构雷
* **包属性被子模块同名实例遮蔽**：`__init__.py` 里 `store=store_mod.STORE` 后，别处 `from . import store` 拿到的是实例，`.STORE` 就 AttributeError。规矩：**要成员就 `from .<mod> import <name>`**，实例名带 `_inst`。文字口令能跑、按钮点击必崩就是这个原因。
* **卡片渲染签名不统一**：多数 `render_*(p)`，但 `render_puppet(roster,elig,p)`/`render_ruin(state,day)`/`render_rank(top)`/`render_battle(last_battle)` 不是；`button_dispatch` 通用 `renderer(p)` 只对 `(p)` 安全，首参不叫 p 的必须特判。守门 `test_doupo_puppet.py` + `test_doupo_buttons.py::test_card_renderer_shapes_match_dispatch`（AST）。
* **出图口令带结果用 `finish_card(caption=…)`**：先发卡后发结果，别让卡吞掉结果（傀儡遗迹曾只发进度卡，胜负掉落全丢）。

## 10. 一处输出 = 一条消息（用户明确要求）
「有结果的出图口令」**不许**拆成「文字+卡」两条，结果并进卡片本体（命令写进可选 `report` dict，`build_*_data(...,report)` 并成首段）。`finish_card` 禁加 caption/分条发送类参数（加过被否）。`test_menu_full.py` 用例默认 `parts=1`，真需要多条才写 `parts=N`。验收战斗类命令**必须用库副本**（tmp/ 复制含 `-wal`），禁直连生产库。

## 11. 丹药契约：desc=原著考据，impl=本作承诺
事故：三纹青灵丹 desc 写「助斗师突破成大斗师」却 `effect="exp" value=1000`，吃8枚永不破境——**desc 承诺=必须有一行代码兑现**（假药事故）。2026-11-19 普查75枚：仅4枚真兑现，50+枚承诺本作没有的系统。契约：**卡面/包裹/「炼药」文本只印 `cfg.recipe_impl(rec)`**；原著 desc 只在文本层标「原著：」且仅当配方手写 `impl`。impl 三来源：手写 `RECIPE_IMPL_OVERRIDE` → breakthrough 自动拼 → `IMPL_BY_EFFECT`；desc 带承诺词而 effect 非 breakthrough **必须手写 impl**，否则 `test_doupo_recipe_honesty` 红。破境丹统一 `effect="breakthrough"`+`to_realm/star_chance/break_boundary/max_per_realm`；「一阶限N枚」落 `pill_use(id,qq,name,realm_idx,used_at)`（**不用复合主键**）。已接：三纹青灵丹/紫心破障丹/聚气散/斗灵丹。🚨 测试里「今天」相关键一律 `date.today().isoformat()`，**禁硬编码日期**。
