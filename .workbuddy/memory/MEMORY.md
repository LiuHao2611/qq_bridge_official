# qq_bridge_official 长期笔记
> 只留「踩过坑别改回去」口径 + 守门测试名。细节读 docs/ 与 AGENTS.md。

## 0. 🚨 最高声明
玩家数据**必须进库禁 JSON**：新玩法=独立表+真实字段，禁塞 `players.data` blob；库路径只由 `DATA_PATH` 推导；原子写。提交走 GitHub API 增量推送，仓库 `LiuHao2611/qq_bridge_official`（**别错推只读的 `qq_bridge` 原件**）。

## 1. 源码问答沙盒
`doupo_code_ask`→`POST qq-doupo-agent:8765/ask`→agent 自己 grep 带行号答案，整句传原话别压关键词。底座锁 **openai-agents 禁换 smolagents**（丢 tool_calls→多轮退化）。只留 `doupo_code_explain` 一个源码工具（守门 `test_doupo_code_explain.py::test_module_is_off_the_chat_path`）。四硬约束：① Dockerfile `mkdir -p /repo`（read_only 不建挂载目标）；② **一进程一事件循环**禁每请求 `asyncio.run()`；③ `AGENT_MAX_TOOLS=10` 防 max_turns 丢答案；④ `max_retries=0` 回 500+原因，**绝不回空答案**。答案禁 Markdown。证明 `tests/evidence/prove_agent_sandbox.py`。

## 2. 🚨 容器数据只落挂载卷
盘符路径反推→写进镜像层重建回滚；判据 `docker diff <container>` 出现该文件即错。容器内路径一律 `/app/...` 禁盘符；原子写（tmp+replace+fsync）；新增持久化同时改 compose+`.dockerignore`+`test_runtime_data_paths.py`。存档 SQLite `/app/data/{doupo,xiuxian}/*.sqlite` 一人一行，库路径**只由 DATA_PATH 推导禁第二环境变量**（守门 `test_db_path_follows_data_path_single_source`）；`get_player()` 返活字典、`put_player()` 单行 UPSERT；禁回退 JSON。守门 `test_game_save_sqlite.py`11条 + `test_user_data_persistence.py`8条；测试里裸 `XxxStore()` 一律红。

## 3. 部署/通道
身份 openid→`pseudo_id()` md5 稳定负整数，主人 ID 写 `.env` 的 `BOT_OWNER_QQ`（member_openid 按群不同）。部署 `docker build -f docker/Dockerfile.bridge -t qq-bridge-official:latest .`→`docker compose -f docker-compose.official.yml up -d --force-recreate`（`--build` 偶发 exit 1）。msg_type 互斥 0文本/2markdown+keyboard/7图片；图+按钮须素材上传+内嵌同段，默认 `OFFICIAL_MERGE_FALLBACK_MODE=image_only`。被动回复 msg_seq 严格递增。测试：`docker run --rm -v "$(pwd -W)":/work -w /work -e QQ_DATA_DIR=/tmp/qdata <img> bash -lc "/opt/venv/bin/python -m pytest ..."`（Git Bash 加 `MSYS_NO_PATHCONV=1`）；全量 20 collection error 是基线，按文件隔离跑。

## 4. 🚨 推送
API 绕 502：`tmp/push_incremental.py <基线commit>` 走 base_tree 只提 diff（全量 tree 422）；blob 缓存 key 必须 `hashlib.sha1(raw)`。**验收 grep 本轮标志串比内容特征**，只信 PUSH_OK=假通过。**行尾跟远端该文件既有行尾对齐**（工作区 CRLF/提交 LF，仓库混行尾）；推完看 get_commit add/del = 本地 diff --stat。

## 5. 战斗/爬塔/回看
属性↔战斗唯一源 `config.STAT_BATTLE_ROLE`；战报统一 `_battle_line()`，特殊字段一律 `.get`。PVP 两段式仅本群，matcher 禁叫 `duel_challenge`（用 `duel_ask_cmd`）。爬塔奖励只用水位线差值；**难度只由层号决定** `tower_ref_player(floor_ref_lv(n))`→`monster_scaled`，禁改回 `monster_scaled(fp,...)`。周副本周一快照画像，奖励先进 staged 通关7层才 commit，失败整段丢弃。最后一场唯一写入口 `core.record_last_battle()`。**终局只有一个数**：`fight()` 算 `hp_end`，存档/结算/回看/人物卡/逐招末行五处同源；战败保底5%（`LOSE_HP_FLOOR=0.05`），`LOSE_HP_ON_DEFEAT=0.30` 只给斗帝进阶。收场六态禁死亡向 `kill/exhausted_win/exhausted_lose/outmatched/rescued/fled`。回看卡=结算界面+一回合一个气泡左右分列；**超长分页** `split_pages()` 实测高度 `PAGE_MAX_H=2600/MAX_PAGES=5`，回合边界切、二分均填、一回合不丢，多页仍一条消息。守门 `test_battle_review.py`211条 + `tools/mutate_battle_review.py`38组。菜单/matcher 严格47条，改口令同步 MENU_GROUPS+4处测试。自动调息唯一挂载 `Store.get_player()`（**出锁后再 regen**，恢复量放 `_LAST_REGEN` 禁挂 p）；180s/tick 气血4%斗气6%。自动服药<35%血/<20%斗气，**深副本 auto_pill=False**。天焚炼气塔 `alchemy_tower` 真列，火能1点=10分钟只结算整块，塔中禁修炼，命名一律 `alchemy_*`/`cmd_furnace_*`。⚠️别拿战后 hp 比战前高当服药证据。⚠️`test_typography.py::doupo_menu` 尺寸(720,3398)、`test_pic_templates.py`3红=基线红非本轮。

## 6. 异火三层投放（2026-11-19 定稿）
榜共23种（原著第六十三章）；「八荒破灭焱」是二手错名正文作「荒破灭焱」。`cfg.FLAME_TIER` 三层：**S5** 帝炎/虚无吞炎/净莲妖火/三千焱炎火/陨落心炎（超标特效大后期/剧情锁，`unlock_flame()`+`S_FLAME_GATE` 境界+主线双门槛）；**A7** 金帝焚天炎/生灵之焱/九幽金祖火/火山石焰/骨灵冷火/九龙雷罡火/青莲地心火（`A_FLAME_SIDE` 隐藏支线 `surprise_flame` 静默收服、不明示玩家）；**B11** 无招式特效，`flame_bonus.py` 统一给炼药/傀儡数值加成（按等级累加+封顶），防卡面与数值两张皮。入道统一发 B 线 starter「火云水炎」（`STARTER_FLAME`），**`FACTION_FLAME_GRANT={}` 禁按宗门发 S/A 火**。帝炎走 `p["diyan"]` 绝不进 `flames`。`feed_flame()` 的 `cur<=0→unowned` 门禁必留（删=养火即收服后门）。文案禁「本宗异火/传承」（starter 是全宗统一数值火）。守门 `test_flame_tiers.py`39条 + `test_doupo_flame_grant.py`23条。考据 `docs/异火作用与特效_原著考据.md`（76条证据机器校验）+ 设计 `docs/异火投放分层设计_2026-11-19.md`。

## 7. 紫卡/429
洗卡链 WFCD→`riven_disp.json`→`riven_stat_base.json`→`wfm_riven_attrs.json`→`riven_recommend_attrs.py`，禁手写 slug 表；击杀效率 vs 市场价差两维度不可混，价差必须 `k=min(n)` 同分位；WFM UA 用 `curl/8.4.0`+`Platform: pc`。守门 `test_riven_recommend_spread.py`45条。429 直连 `POST {AI_API_BASE}/chat/completions` 看 `error.code`（SetLimitExceeded/ModelNotOpen），限额按模型独立计。

## 8. 结构雷/输出契约
**包属性被子模块同名遮蔽**：要成员就 `from .<mod> import <name>`，实例名带 `_inst`（文字口令能跑、按钮崩即此因）。**卡片渲染签名不统一**：`render_puppet(roster,elig,p)`/`render_ruin(state,day)`/`render_rank(top)`/`render_battle(last_battle)` 首参不叫 p 必须特判（守门 `test_doupo_buttons.py::test_card_renderer_shapes_match_dispatch`）。**一处输出=一条消息**：有结果的出图口令把结果并进卡片本体，禁拆文字+卡两条。验收战斗命令必须用库副本（tmp/ 含 -wal）禁直连生产库。丹药契约 `desc=原著考据/impl=本作承诺`，卡面只印 `cfg.recipe_impl()`，测试日期一律 `date.today().isoformat()` 禁硬编码。
