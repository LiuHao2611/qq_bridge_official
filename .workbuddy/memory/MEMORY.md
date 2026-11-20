# qq_bridge_official 长期笔记
> 只留「踩过坑别改回去」红线 + 守门测试名。细节读 `docs/` 与 `AGENTS.md`。

## 0. 🚨 最高声明
玩家数据**必须进库禁 JSON**：新玩法=独立表+真实字段，禁塞 `players.data` blob；库路径只由 `DATA_PATH` 推导；原子写。提交走 GitHub API 增量推送，仓库 `LiuHao2611/qq_bridge_official`（**别错推只读的 `qq_bridge` 原件**）。

## 1. 容器/部署/推送
**数据只落挂载卷**：盘符路径反推→写进镜像层重建回滚，判据 `docker diff` 出现该文件即错；容器内一律 `/app/...`；新增持久化同时改 compose+`.dockerignore`+`test_runtime_data_paths.py`。存档 SQLite `/app/data/{doupo,xiuxian}/*.sqlite` 一人一行，`get_player()` 返活字典、`put_player()` 单行 UPSERT。守门 `test_db_path_follows_data_path_single_source`、`test_game_save_sqlite.py`、`test_user_data_persistence.py`。
部署 `docker build -f docker/Dockerfile.bridge -t qq-bridge-official:latest .`→`docker compose -f docker-compose.official.yml up -d --force-recreate`（`--build` 偶发 exit 1）。msg_type 互斥 0文本/2markdown+keyboard/7图片，图+按钮须素材上传+内嵌同段。测试 `MSYS_NO_PATHCONV=1 docker run --rm -v "$(pwd -W)":/work -w /work -e QQ_DATA_DIR=/tmp/qdata <img> bash -lc "/opt/venv/bin/python -m pytest ..."`；**镜像无 pytest**（system python3 无 nonebot），测试是脚本式自跑；全量 20 collection error 是基线，按文件隔离跑。
推送绕 502：`tmp/push_incremental.py <基线commit>` 走 base_tree 只提 diff；blob 缓存 key 必须 `hashlib.sha1(raw)`；行尾跟远端该文件既有行尾对齐；`git diff --numstat` 中文路径必须 `-c core.quotepath=false`；**验收标志串先 grep 再写**，只信 PUSH_OK=假通过。

## 2. 战斗/回看
属性↔战斗唯一源 `config.STAT_BATTLE_ROLE`；战报统一 `_battle_line()`，特殊字段一律 `.get`。爬塔**难度只由层号决定** `tower_ref_player(floor_ref_lv(n))`。周副本周一快照画像、staged 通关7层才 commit。最后一场唯一写入口 `core.record_last_battle()`。**终局只有一个数**：`fight()` 算 `hp_end`，存档/结算/回看/人物卡/逐招末行五处同源；战败保底5%（`LOSE_HP_FLOOR`）。回看卡超长分页 `PAGE_MAX_H=2600/MAX_PAGES=5`，回合边界切、一回合不丢。守门 `test_battle_review.py` 218 条 + `tools/mutate_battle_review.py` 38 组。菜单/matcher 严格52条，改口令同步 MENU_GROUPS+多处测试。自动调息唯一挂载 `Store.get_player()`（**出锁后再 regen**）。自动服药<35%血/<20%斗气，深副本 `auto_pill=False`。天焚炼气塔 `alchemy_tower` 真列，火能1点=10分钟只结算整块，塔中禁修炼。⚠️`test_typography.py::doupo_menu` 尺寸(720,3398)、`test_pic_templates.py` 3红=基线红。

## 3. 异火三层投放
榜共23种（第63章），「八荒破灭焱」是二手错名，正文作「荒破灭焱」。`cfg.FLAME_TIER`：**S5** 帝炎/虚无吞炎/净莲妖火/三千焱炎火/陨落心炎（剧情锁）；**A7** 金帝焚天炎等（`A_FLAME_SIDE` 隐藏支线 `surprise_flame` 静默收服）；**B11** 无特效，`flame_bonus.py` 给数值加成。入道**不送火**，首火走**九星突破BOSS战掉落**：`is_nine_star_breakthrough`→`make_breakthrough_boss`（纯快照反解**不放大属性**）→`roll_breakthrough_flame`（other45%>bonus18%）。⚠️BOSS难度靠血量不靠攻击（闪避随等级非线性）。`FACTION_FLAME_GRANT={}` 禁按宗门发 S/A 火。帝炎走 `p["diyan"]` 绝不进 `flames`。`feed_flame()` 的 `cur<=0→unowned` 门禁必留（删=养火即收服后门）。文案禁「本宗异火/传承」。守门 `test_flame_tiers.py`41 + `test_doupo_flame_grant.py`29 + `test_nine_star_breakthrough.py`31（真实路径 `src/plugins/doupo/test_nine_star_breakthrough.py`）。

## 4. 结构雷/输出契约
**包属性被子模块同名遮蔽**：要成员就 `from .<mod> import <name>`。**卡片渲染签名不统一**：`render_puppet(roster,elig,p)`/`render_ruin(state,day)`/`render_rank(top)`/`render_battle(last_battle)` 首参不叫 p 必须特判（守门 `test_doupo_buttons.py::test_card_renderer_shapes_match_dispatch`）。**一处输出=一条消息**。验收战斗命令必须用库副本（含 `-wal`）禁直连生产库。丹药契约 `desc=原著考据/impl=本作承诺`，卡面只印 `cfg.recipe_impl()`；测试日期一律 `date.today().isoformat()`。
**批量连炼**：`core.craft_batch()` **禁改单发 `craft()` 语义**；批量=连续开炉各判成败、**禁一炉多丹**、整批一次CD。防刷双闸禁删：`low_grade_exp_factor`、七品以上 `SOUL_REALM_BATTLES`。守门 `test_doupo_craft_batch.py`25。

## 5. 🚨 沉浸式文案铁律
玩家可见处（角色包 `intro`/`title`、`CHARACTER_APPEARANCE`、火焰 `desc`、斗技列表/详情、卡面来源标记、突破文案）**禁**出现 `未载/不代编/代编/游戏原创/游戏命名/游戏设定/语料/原文只有/非原著角色/原著无`。守门 `tests/test_doupo_immersion_gate.py` 43 条 + `tmp/mut_immersion.py` 21 组。
**登场三段式**：剪影(appearance)→报名号(title)→台词(intro)，表 `cfg.CHARACTER_APPEARANCE` 唯一真值。
**零元说明洗在数据层不是渲染层**：`factions._strip_cite_tags(FACTIONS)` 导入即剥引文标记，剥前快照 `FACTION_CITE_LEDGER`（禁渲染）；审计靠 `tmp/audit_player_text.py` 真跑业务，别逐字段猜。
**技能效果必须逐型验落地**：`tests/test_doupo_skill_effects_land.py` 23 条做同参数对照实验（带/不带效果各打一场）。新增 effect 必须同时补一条。
**目标侧闪避**：`mon["dodge"]`→`m_fx["evade"]`，在 `on_hit` **之前**结算；**`t_ev<=0` 绝不掷骰**（否则 218 条回看快照全红）。

## 6. 命中↔闪避 + 怪物面板
**唯一口径** `实际闪避% = max(0, 目标闪避% − 攻击方命中%)`，三处都扣：`fight()` 的 dodge 阈值、`_hit(atk_hit=)`、`on_incoming(hit=)`。**玩家命中绝不吃敏捷**（`6 + 大境界×1.5 + bonus["命中"]`）。
**怪物面板** `core.monster_panel(lv, kind, is_boss)`：眼力 = `level_dodge(lv) − cfg.MON_HIT_RESIDUAL(50)`（抵掉等级白拿的身法）；暴击/身法按 `2+0.8k`/`3+1.6k` × 精英1.20/BOSS1.45，**眼力不加系数**。面板**只挂 `monster_scaled` 实例，绝不进 hp/atk 反解**；手搓怪零面板 → 200+ 条快照零回归。削弱旋钮只有一个 `MON_HIT_RESIDUAL`（50 采用：胜率73%、高层闪避80→50）。九星突破必须连带重标定 `BOSS_BREAK_HP_MULT 1.15`/`ATK_MULT 1.10`（必须>1）。守门 `test_doupo_hit_rate.py`25 + `tools/mutate_hit_rate.py`9。**手搓怪用例必须满级怪+拖满30回合**。
⚠️ **别再引入「基准闪避补偿」常量**（`DODGE_DESIGN=0.30` 那条路会让低等级同样放大1.43×，已否决并删）。**别手算面板**，必须真跑 `core.monster_panel()`（漏「kind 字符串没命中」这类判定 bug）。清单 `docs/怪物隐藏面板清单_2026-11-20.md`。
**已知未改缺陷**：精英系数在塔/周本 kind（`"塔·elite"`）失效；「压闪避」7门斗技 PVE 全废（玩家命中恒 ≥ 怪身法），只对 PVP 有效。

## 7. 傀儡三件套（配方/锚定/护主）
**五维木桶判档唯一入口** `puppet_core.recipe_tier(slots)`（`min(五维)−1` 封顶2，缺维返−1）；`first_craft_tier()` 已弃用。**材料名必须原著真名**（正文无「八阶/九阶」；魔核只 火属性/七阶/高阶；金属 精铁/暗金玄铁/寒铁/焰陨玄铁）。金属/火进公式但**基值锚定在旧档**（`grade2/fire7` 恒 1.0，别改锚点会牵动怪物反解）。`PUPPET_MP_FACTOR=(0.60,0.80,1.00)`（旧 2.4 是死锁）。门禁顺序 资格→竹简→材料→斗气。配方18条，`craftable_recipes` 分 `tier`（实况/火按玩家）与 `tier_nominal`（列表分组）；同档排序按 cfg 索引。傀儡料只进 `puppet_out` 不进 `out`。守门 `test_doupo_puppet_recipes.py`82 + `tmp/mut_pup_recipes.py`12。
**战力锚定（三轮，别再改回去）**：① 绝对常数 `PUPPET_TIER_BASE/CORE_K1/SOUL_K2/ATK_FACTOR` 已删（lv81 人级=0.62%）；② `PUPPET_TIER_RATIO(0.15/0.30/0.50)` **本轮也已删**（纯比例与「地妖傀=五星六星斗宗」对不上）；③ 现行 = **战力锚躯体境界**：`min(躯体境界标尺输出×材料系数, 玩家每回合输出 × PUPPET_ATK_CAP[(0.55,0.80,0.95)])`。`puppet_realm_lv()` 按第757处把「七星斗宗之躯」折成「五星斗宗」（`PUPPET_BODY_STAR_PENALTY=2`，躯体 n 星→n−2 星），`PUPPET_TIER_REALM_LV=(55,68,73)` 是 body_realm 缺失时的回落。**🚨 战力封顶叫 `PUPPET_ATK_CAP`，别叫 `PUPPET_TIER_CAP`**（后者已被「材料档位封顶天级=2」占用，同名会被文件后段覆盖成 int，`TypeError`）。**材料系数必须乘在境界标尺上、封顶管最终值**：乘在 min() 之后会让顶配 1.31 倍把天级顶成 131%（实测）；结阵×1.30/异火×1.30 同理，所以封顶落在 `puppet_expected_damage` 的 total。`北龙王之躯` star 0→5（表注释本就写五星斗圣），是 lv94+ 唯一成长路径（三星斗圣 lv96）。
**🚨 `PUPPET_ENEMY_COMP=0.55` 是「带傀儡有没有意义」的唯一开关**（=1.0 时怪血同步变厚，数学上完全对消）。**`fight` 的 `_pup_atk` 必须走 `puppet_expected_damage`**（含结阵/异火），用 `puppet_attack` 会漏结阵而反解按它算 → 带结阵反而吃亏（第二轮遗留 bug，已修，守门 `test_fight_single_source`）。
**五处战斗锚必须分清**：野外锚真人 p；爬塔怪血锚 `tower_ref_player` 而实战锚真人 fp；周本锚周一快照；九星锚触发快照；PVP 双方对称。命令层唯一取数口 `commands._puppet_ctx(qq,p)`，`flame_mult` 必须同时喂反解与实战。结阵封顶 `PUPPET_FORMATION_MAX=3`。守门 `test_doupo_puppet_anchor.py` **83 条** + `tmp/mut_pup_anchor.py` **16 组抓住 16 白守 0**。**白守教训**：测试自己显式注入参数时抓不到 cfg 被改，必须另加「config 实际值」或**绝对境界名**断言（拿 `cfg.PUPPET_TIER_REALM_LV` 当期望，被改成 (1,1,1) 也照样过）；老档必须断言**精确等于 1.0**。
**卡面标签** `puppet_realm_label(puppet,p)`：未压制标材料境界（「斗尊级」），跨大境界被压才降档并注「· 受你境界所限」，同境界内被压不降档。反查走 `puppet_effective_lv()` 二分。⚠️ **label 不许带括号**（卡面格式 `肉身攻击 N　{label}（不吃斗气/斗技）` 会套成两层中文括号，真实数据实测踩到）。设计文档 `docs/傀儡战力按境界锚定_2026-11-20.md`。
**护主挡伤**：口径全在 `puppet_core.guard_profile()`（卡面 `cmd_puppet`/`card.build_puppet_data` 与 `core.fight` 同调）：`CHANCE=(0.18,0.25,0.32)` `CAP=0.40` `LEAK=0.35` `HP_RATIO=(0.25,0.40,0.55)` `ARMOR_TAKE=(0.78,0.68,0.58)` `THRESHOLD=0.06` `HURT_AT=0.30/MULT=0.5` `FORMATION_GUARD_STEP=0.06`。
🚨 **护甲绝不能按玩家防御比例放大**：`mitigation=W/(W+d)` 双曲线，玩家防御 lv10 570→lv81 8982 → 按比例会让减伤 19.9%→79.7%，高等级永不掉血。正解：锚「穿透率」反解 `armor = MITIGATION_W×(1/take−1)×材料系数`（`MITIGATION_W=800` 已提 cfg）。守门有 `hasattr(cfg,"PUPPET_ARMOR_RATIO") must be False`。
🚨 **吸满上限必须浮点 + `math.ceil`**：`int(hp/mig)`+`int(soak×mig)` 两步截断会停在 `hp_left=1` **永远打不死**。守门钉死「任何一场不许 hp_left==1」。
**rng 红线**：挡伤骰与 `res["puppet_guard"]` 只在 `if puppet` 内。文案三坑：① 护主 note 必须 `notes.insert(0,…)`（`append` 被 `TAG_LIMIT=4` 截掉）；② note 内**禁括号**；③ `_beat_tag` 加 `puppet_guard→"护主"` 进 `_BEAT_ORDER`，tone 必须 `mine`。**单场消耗、跨场满血**（`temper()` 是既有修复口）。护主分摊比实测 40/42/44%，红线 50% —— **别再提概率**。守门 `test_doupo_puppet_guard.py`128 + `tmp/mut_pup_guard.py`20。**白守教训**：逐场都查别 break；`ceil` 与浮点 cap 是双保险要一起改；怪必须 `chance=1.0` 强制放技能。⚠️ 读 `guard_hp_left` **不能用 `or -1`**（0 是 falsy）。
⚠️ **傀儡库是独立库** `data/doupo/puppet_data.sqlite`（表 `puppets`/`puppet_materials`/`puppet_eligibility`/`puppet_ruin_daily`/`meta`），**不在** `doupo.sqlite`；`puppets` 无 `active`/`name` 列；`qq='wild'` 是遗迹残傀池。`Store` 懒加载必须先 `await ds.load()`；`from doupo import store as st` 拿到的是实例。

## 8. 紫卡/429
洗卡链 WFCD→`riven_disp.json`→`riven_stat_base.json`→`wfm_riven_attrs.json`→`riven_recommend_attrs.py`，禁手写 slug 表；击杀效率 vs 市场价差两维度不可混，价差必须 `k=min(n)` 同分位；WFM UA `curl/8.4.0`+`Platform: pc`。守门 `test_riven_recommend_spread.py`45。429 直连 `POST {AI_API_BASE}/chat/completions` 看 `error.code`。

## 9. 源码问答沙盒
`doupo_code_ask`→`POST qq-doupo-agent:8765/ask`，底座锁 **openai-agents 禁换 smolagents**（丢 tool_calls→多轮退化）。四硬约束：`mkdir -p /repo`；**一进程一事件循环**；`AGENT_MAX_TOOLS=10`；`max_retries=0` 回 500+原因**绝不回空答案**。答案禁 Markdown。
