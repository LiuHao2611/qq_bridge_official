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
属性↔战斗唯一源 `config.STAT_BATTLE_ROLE`；战报统一 `_battle_line()`，特殊字段一律 `.get`。PVP 两段式仅本群，matcher 禁叫 `duel_challenge`（用 `duel_ask_cmd`）。爬塔奖励只用水位线差值；**难度只由层号决定** `tower_ref_player(floor_ref_lv(n))`→`monster_scaled`，禁改回 `monster_scaled(fp,...)`。周副本周一快照画像，奖励先进 staged 通关7层才 commit，失败整段丢弃。最后一场唯一写入口 `core.record_last_battle()`。**终局只有一个数**：`fight()` 算 `hp_end`，存档/结算/回看/人物卡/逐招末行五处同源；战败保底5%（`LOSE_HP_FLOOR=0.05`），`LOSE_HP_ON_DEFEAT=0.30` 只给斗帝进阶。收场六态禁死亡向 `kill/exhausted_win/exhausted_lose/outmatched/rescued/fled`。回看卡=结算界面+一回合一个气泡左右分列；**超长分页** `split_pages()` 实测高度 `PAGE_MAX_H=2600/MAX_PAGES=5`，回合边界切、二分均填、一回合不丢，多页仍一条消息。守门 `test_battle_review.py`211条 + `tools/mutate_battle_review.py`38组。菜单/matcher 严格52条，改口令同步 MENU_GROUPS+多处测试。自动调息唯一挂载 `Store.get_player()`（**出锁后再 regen**，恢复量放 `_LAST_REGEN` 禁挂 p）；180s/tick 气血4%斗气6%。自动服药<35%血/<20%斗气，**深副本 auto_pill=False**。天焚炼气塔 `alchemy_tower` 真列，火能1点=10分钟只结算整块，塔中禁修炼，命名一律 `alchemy_*`/`cmd_furnace_*`。⚠️别拿战后 hp 比战前高当服药证据。⚠️`test_typography.py::doupo_menu` 尺寸(720,3398)、`test_pic_templates.py`3红=基线红非本轮。

## 6. 异火三层投放（2026-11-19 定稿）
榜共23种（原著第六十三章）；「八荒破灭焱」是二手错名正文作「荒破灭焱」。`cfg.FLAME_TIER` 三层：**S5** 帝炎/虚无吞炎/净莲妖火/三千焱炎火/陨落心炎（超标特效大后期/剧情锁，`unlock_flame()`+`S_FLAME_GATE` 境界+主线双门槛）；**A7** 金帝焚天炎/生灵之焱/九幽金祖火/火山石焰/骨灵冷火/九龙雷罡火/青莲地心火（`A_FLAME_SIDE` 隐藏支线 `surprise_flame` 静默收服、不明示玩家）；**B11** 无招式特效，`flame_bonus.py` 统一给炼药/傀儡数值加成（按等级累加+封顶），防卡面与数值两张皮。入道**不送火**（2026-11-19 改：太廉价），首火改走**九星突破BOSS战掉落**：`is_nine_star_breakthrough`（`realm_index(lv)!=realm_index(lv+1)`且经验满）→`make_breakthrough_boss`（纯快照反解**不放大属性**，hp×1.6/atk×1.15，⚠️放大属性低等级卡死）→战胜`level_up`+`roll_breakthrough_flame`（other45%>bonus18%，A/S/帝炎不进池）。⚠️闪避随等级非线性，BOSS难度靠血量不靠攻击。`cmd_break(rng=None)`支持测试注入。`FACTION_FLAME_GRANT={}` 禁按宗门发 S/A 火。帝炎走 `p["diyan"]` 绝不进 `flames`。`feed_flame()` 的 `cur<=0→unowned` 门禁必留（删=养火即收服后门）。文案禁「本宗异火/传承」。另有10朵原著兽火/其他火焰（`OTHER_FLAMES`，`other_flame_ratio`聚合适stats）。守门 `test_flame_tiers.py`39 + `test_doupo_flame_grant.py`29 + `test_nine_star_breakthrough.py`31。考据 `docs/异火作用与特效_原著考据.md`+设计`docs/异火投放分层设计_2026-11-19.md`+`docs/九星突破BOSS战与火焰掉落_2026-11-19.md`。

## 7. 紫卡/429
洗卡链 WFCD→`riven_disp.json`→`riven_stat_base.json`→`wfm_riven_attrs.json`→`riven_recommend_attrs.py`，禁手写 slug 表；击杀效率 vs 市场价差两维度不可混，价差必须 `k=min(n)` 同分位；WFM UA 用 `curl/8.4.0`+`Platform: pc`。守门 `test_riven_recommend_spread.py`45条。429 直连 `POST {AI_API_BASE}/chat/completions` 看 `error.code`（SetLimitExceeded/ModelNotOpen），限额按模型独立计。

## 8. 结构雷/输出契约
**包属性被子模块同名遮蔽**：要成员就 `from .<mod> import <name>`，实例名带 `_inst`（文字口令能跑、按钮崩即此因）。**卡片渲染签名不统一**：`render_puppet(roster,elig,p)`/`render_ruin(state,day)`/`render_rank(top)`/`render_battle(last_battle)` 首参不叫 p 必须特判（守门 `test_doupo_buttons.py::test_card_renderer_shapes_match_dispatch`）。**一处输出=一条消息**：有结果的出图口令把结果并进卡片本体，禁拆文字+卡两条。验收战斗命令必须用库副本（tmp/ 含 -wal）禁直连生产库。丹药契约 `desc=原著考据/impl=本作承诺`，卡面只印 `cfg.recipe_impl()`，测试日期一律 `date.today().isoformat()` 禁硬编码。
**批量连炼（2026-11-19）**：`core.craft_batch()` 新入口**禁改单发 `craft()` 语义**（test_core 钉死CD/经验/药渣）。批量=连续开炉N炉各判成败、**禁一炉多丹**（原著第111章一炉一丹失败报废）；整批只占一次CD、逐炉扣药材+斗气（`CRAFT_MP_PER=0.08`）、药材不足按实收炉。防刷双闸禁删：低阶丹经验递减 `low_grade_exp_factor`（`CRAFT_LOW_EXP_FACTOR`）、七品以上 `SOUL_REALM_BATTLES` 以 `wins+losses` 代理灵魂境界（第1103章，与根基裁定同源，经验不足/境界不足都保留不吞）。`_alch_level_up` 已修末行 `idx!=p["alch"]` 恒False的bug（存 start 比较）。口令 `_parse_craft_count` 认「炼制 回灵丹 5次/×3/x2」，n>1 走 `_cmd_craft_batch` 一条消息回包。守门 `test_doupo_craft_batch.py`25 + `_cmd.py`11。考据 `docs/炼药加速与快速炼丹_原著考据_2026-11-19.md`。

## 9. 🚨 沉浸式文案铁律（2026-11-20，用户连说两遍）
玩家可见处（角色包 `intro`/`title`、`CHARACTER_APPEARANCE`、火焰 `desc`、斗技列表/详情、卡面来源标记、突破文案）**禁**出现 `未载/不代编/代编/游戏原创/游戏命名/游戏设定/语料/原文只有/非原著角色/原著无`；`source`/`novel`/`note` 是内部字段不扫描，正向引文（第X章「标题」）保留。守门 `tests/test_doupo_immersion_gate.py`（36 条）+ 反证 `tmp/mut_immersion.py`（14 组）。
**登场三段式**（对齐原著写法/同类游戏）：剪影(appearance)→报名号(title)→台词(intro)。表 `cfg.CHARACTER_APPEARANCE` 唯一真值，`character_appearance()` 读；`fight()` 登场 log 写 `[appearance,intro]` 两条 notes（缺则回落查表）；`cmd_break` 第①段同序；`intro_text()` 返 `quote`+`paras`，模板按段折行 + `_wrap_no_orphan()` 消寡行。
**技能效果别只看文案**：`poison` 必须挂 `p_fx["dot"]`（旧 `poison_turns` 计数器已删，两套并存=4 回合毒）；玩家出手必须读自己的 `fx_self`（`debuff_atk` 才真降伤）；`_try_auto_pill` 回血必须乘 `heal_mult`（否则减疗无感）。⚠️ mech 的 dot base 由 `skill_effects.on_hit` 用本次 `dmg` 决定，core 里 `_mech_eff["base"]` 是死代码（已删）。
