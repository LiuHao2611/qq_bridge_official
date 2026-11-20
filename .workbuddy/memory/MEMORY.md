# qq_bridge_official 长期笔记
> 只留「踩过坑别改回去」红线 + 守门测试名。细节读 `docs/` 与 `AGENTS.md`。

## 0. 最高声明
玩家数据**必须进库禁 JSON**：新玩法=独立表+真实字段，禁塞 `players.data` blob；库路径只由 `DATA_PATH` 推导；原子写。提交走 GitHub API 增量推送，仓库 `LiuHao2611/qq_bridge_official`（别错推只读的 `qq_bridge` 原件）。

## 1. 容器/部署/推送
数据只落挂载卷：盘符路径反推→写进镜像层重建回滚，判据 `docker diff` 出现该文件即错；容器内一律 `/app/...`；新增持久化同时改 compose + `.dockerignore` + `test_runtime_data_paths.py`。存档 SQLite `/app/data/{doupo,xiuxian}/*.sqlite` 一人一行，`get_player()` 返活字典、`put_player()` 单行 UPSERT。守门 `test_db_path_follows_data_path_single_source`、`test_game_save_sqlite.py`、`test_user_data_persistence.py`。
部署 `docker build -f docker/Dockerfile.bridge -t qq-bridge-official:latest .` → `docker compose -f docker-compose.official.yml up -d --force-recreate`（`--build` 偶发 exit 1）。msg_type 互斥 0文本/2markdown+keyboard/7图片，图+按钮须素材上传+内嵌同段。测试 `MSYS_NO_PATHCONV=1 docker run --rm -v "$(pwd -W)":/work -w /work -e QQ_DATA_DIR=/tmp/qdata <img> bash -lc "/opt/venv/bin/python -m pytest ..."`；**镜像无 pytest**（system python3 无 nonebot），测试脚本式自跑；全量 20 collection error 是基线，按文件隔离跑。
推送绕 502：`tmp/push_incremental.py <基线commit>` 走 base_tree 只提 diff；blob 缓存 key 必须 `sha1(raw)`；行尾跟远端该文件既有行尾；`git diff --numstat` 中文路径加 `-c core.quotepath=false`；**验收标志串先 grep 再写**，只信 PUSH_OK=假通过。

## 2. 战斗/回看
属性↔战斗唯一源 `config.STAT_BATTLE_ROLE`；战报统一 `_battle_line()`，特殊字段一律 `.get`。爬塔难度只由层号决定 `tower_ref_player(floor_ref_lv(n))`。周副本周一快照、staged 通关7层才 commit。最后一场唯一写入口 `core.record_last_battle()`。**终局只有一个数**：`fight()` 算 `hp_end`，存档/结算/回看/人物卡/逐招末行五处同源；战败保底5%（`LOSE_HP_FLOOR`）。回看超长分页 `PAGE_MAX_H=2600/MAX_PAGES=5`，回合边界切、一回合不丢。守门 `test_battle_review.py`218 + `tools/mutate_battle_review.py`38。菜单/matcher 严格52条，改口令同步 MENU_GROUPS + 多处测试。自动调息唯一挂载 `Store.get_player()`（出锁后再 regen）。自动服药 <35%血/<20%斗气，深副本 `auto_pill=False`。天焚炼气塔 `alchemy_tower` 真列，火能1点=10分钟只结算整块，塔中禁修炼。⚠️ `test_typography.py::doupo_menu` 尺寸(720,3398)、`test_pic_templates.py` 3红=基线红。

## 3. 异火三层投放
榜共23种（第63章），「八荒破灭焱」是二手错名，正文作「荒破灭焱」。`cfg.FLAME_TIER`：**S5** 帝炎/虚无吞炎/净莲妖火/三千焱炎火/陨落心炎（剧情锁）；**A7** 金帝焚天炎等（`A_FLAME_SIDE` 隐藏支线 `surprise_flame` 静默收服）；**B11** 无特效，`flame_bonus.py` 给数值。入道不送火，首火走九星突破BOSS掉落：`is_nine_star_breakthrough`→`make_breakthrough_boss`（纯快照反解不放大属性）→`roll_breakthrough_flame`（other45%>bonus18%）。⚠️BOSS难度靠血量不靠攻击。`FACTION_FLAME_GRANT={}` 禁按宗门发 S/A 火。帝炎走 `p["diyan"]` 绝不进 `flames`。`feed_flame()` 的 `cur<=0→unowned` 门禁必留。文案禁「本宗异火/传承」。守门 `test_flame_tiers.py`41 + `test_doupo_flame_grant.py`29 + `test_nine_star_breakthrough.py`31（真路径 `src/plugins/doupo/test_nine_star_breakthrough.py`）。

## 4. 结构雷/输出契约
**包属性被子模块同名遮蔽**：要成员就 `from .<mod> import <name>`。**卡片渲染签名不统一**：`render_puppet(roster,elig,p)`/`render_ruin(state,day)`/`render_rank(top)`/`render_battle(last_battle)` 首参不叫 p 必须特判（守门 `test_doupo_buttons.py::test_card_renderer_shapes_match_dispatch`）。一处输出=一条消息。验收战斗命令必须用库副本（含 `-wal`）禁直连生产库。丹药契约 `desc=原著考据/impl=本作承诺`，卡面只印 `cfg.recipe_impl()`；测试日期一律 `date.today().isoformat()`。
**批量连炼** `core.craft_batch()` 禁改单发 `craft()` 语义；批量=连续开炉各判成败、禁一炉多丹、整批一次CD。防刷双闸禁删：`low_grade_exp_factor`、七品以上 `SOUL_REALM_BATTLES`。守门 `test_doupo_craft_batch.py`25。

## 5. 沉浸式文案铁律
玩家可见处（角色包 `intro`/`title`、`CHARACTER_APPEARANCE`、火焰 `desc`、斗技列表/详情、卡面来源标记、突破文案）禁出现 `未载/不代编/代编/游戏原创/游戏命名/游戏设定/语料/原文只有/非原著角色/原著无`。守门 `tests/test_doupo_immersion_gate.py`43 + `tmp/mut_immersion.py`21。
登场三段式：剪影(appearance)→报名号(title)→台词(intro)，`cfg.CHARACTER_APPEARANCE` 唯一真值。零元说明洗在数据层（`factions._strip_cite_tags(FACTIONS)` 导入即剥，剥前快照 `FACTION_CITE_LEDGER` 禁渲染）；审计靠 `tmp/audit_player_text.py` 真跑业务。技能效果逐型验落地 `tests/test_doupo_skill_effects_land.py`23（带/不带效果各打一场），新增 effect 必补一条。目标侧闪避 `mon["dodge"]`→`m_fx["evade"]` 在 `on_hit` 前结算；`t_ev<=0` 绝不掷骰（否则218条快照全红）。

## 6. 命中↔闪避 + 怪物面板
唯一口径 `实际闪避% = max(0, 目标闪避% − 攻击方命中%)`，三处都扣：`fight()` dodge 阈值、`_hit(atk_hit=)`、`on_incoming(hit=)`。玩家命中绝不吃敏捷（`6 + 大境界×1.5 + bonus["命中"]`）。
`core.monster_panel(lv,kind,is_boss)`：眼力=`level_dodge(lv)−MON_HIT_RESIDUAL(50)`（抵掉等级白送身法）；暴击/身法 `2+0.8k`/`3+1.6k` ×精英1.20/BOSS1.45，眼力不加系数。面板只挂 `monster_scaled` 实例，绝不进 hp/atk 反解；手搓怪零面板→200+条快照零回归。削弱旋钮只 `MON_HIT_RESIDUAL`（50 采用：胜率73%）。九星突破连带重标定 `BOSS_BREAK_HP_MULT 1.15`/`ATK_MULT 1.10`（必须>1）。守门 `test_doupo_hit_rate.py`25 + `tools/mutate_hit_rate.py`9。**手搓怪用例必须满级怪+拖满30回合**。
⚠️ 别再引入「基准闪避补偿」常量（`DODGE_DESIGN=0.30` 会让低等级同样放大1.43×，已否决删除）。别手算面板，必须真跑 `core.monster_panel()`。清单 `docs/怪物隐藏面板清单_2026-11-20.md`。
已知未改缺陷：精英系数在塔/周本 kind（`"塔·elite"`）失效；「压闪避」7门斗技 PVE 全废，只对 PVP 有效。

## 7. 傀儡三件套（配方/锚定/护主）
**五维木桶判档唯一入口** `puppet_core.recipe_tier(slots)`（`min(五维)−1` 封顶2，缺维−1）；`first_craft_tier()` 已弃用。材料名必须原著真名（正文无「八阶/九阶」；魔核只 火属性/七阶/高阶；金属 精铁/暗金玄铁/寒铁/焰陨玄铁）。金属/火进公式但基值锚定旧档（`grade2/fire7` 恒1.0，改锚点牵动怪物反解）。`PUPPET_MP_FACTOR=(0.60,0.80,1.00)`。门禁顺序 资格→竹简→材料→斗气。配方18条，`craftable_recipes` 分 `tier`（实况）与 `tier_nominal`（分组）；傀儡料只进 `puppet_out`。守门 `test_doupo_puppet_recipes.py`82 + `tmp/mut_pup_recipes.py`12。
**战力锚定（三轮定稿别改回去）**：① 绝对常数 `PUPPET_TIER_BASE/CORE_K1/SOUL_K2/ATK_FACTOR` 已删；② `PUPPET_TIER_RATIO` 也已删；③ 现行=战力锚躯体境界 `min(躯体境界标尺输出×材料系数, 玩家每回合输出×PUPPET_ATK_CAP[(0.55,0.80,0.95)])`。`puppet_realm_lv()` 按第757处折星（`PUPPET_BODY_STAR_PENALTY=2`），`PUPPET_TIER_REALM_LV=(55,68,73)` 是 body_realm 缺失回落。**🚨 封顶叫 `PUPPET_ATK_CAP`，`PUPPET_TIER_CAP` 已被材料档位占用**（同名会被覆盖成 int 而 TypeError）。**材料系数乘在境界标尺上、封顶管最终值**（乘在 min 后顶配1.31倍会把天级顶成131%）；结阵×1.30/异火×1.30 同理，封顶落 `puppet_expected_damage` 的 total。`北龙王之躯` star 0→5 是 lv94+ 唯一成长路径。
**🚨 `PUPPET_ENEMY_COMP=0.55` 是「带傀儡有没有意义」唯一开关**（=1.0 完全对消）。`fight` 的 `_pup_atk` 必须走 `puppet_expected_damage`（含结阵/异火），用 `puppet_attack` 会让带结阵反而吃亏（守门 `test_fight_single_source`）。
五处战斗锚：野外锚真人 p；爬塔怪血锚 `tower_ref_player` 实战锚真人 fp；周本锚周一快照；九星锚触发快照；PVP 对称。命令层唯一取数口 `commands._puppet_ctx(qq,p)`，`flame_mult` 同喂反解与实战。结阵封顶 `PUPPET_FORMATION_MAX=3`。守门 `test_doupo_puppet_anchor.py`83 + `tmp/mut_pup_anchor.py`16。**白守教训**：测试自己注入参数抓不到 cfg 被改，须另加「config 实际值」或绝对境界名断言；老档断言精确1.0。
卡面标签 `puppet_realm_label()`：未压制标材料境界，跨大境界被压才降档注「·受你境界所限」，同境界内不降档；反查 `puppet_effective_lv()` 二分。⚠️ label 不许带括号（卡面已有中文括号）。文档 `docs/傀儡战力按境界锚定_2026-11-20.md`。
**护主挡伤** 口径全在 `puppet_core.guard_profile()`（卡面与 `core.fight` 同调）：`CHANCE=(0.18,0.25,0.32)` `CAP=0.40` `LEAK=0.35` `HP_RATIO=(0.25,0.40,0.55)` `ARMOR_TAKE=(0.78,0.68,0.58)` `THRESHOLD=0.06` `HURT_AT=0.30/MULT=0.5` `FORMATION_GUARD_STEP=0.06`。
🚨 护甲绝不能按玩家防御比例放大（lv10 570→lv81 8982 会让减伤19.9%→79.7%）；正解锚穿透率反解 `armor=MITIGATION_W×(1/take−1)×材料系数`（`MITIGATION_W=800`）。守门钉 `hasattr(cfg,"PUPPET_ARMOR_RATIO") is False`。🚨 吸满上限必须浮点+`math.ceil`（`int` 两步截断会停在 hp_left=1 永远打不死）。
rng 红线：挡伤骰与 `res["puppet_guard"]` 只在 `if puppet` 内。文案三坑：note 必须 `notes.insert(0,…)`（append 被 `TAG_LIMIT=4` 截掉）；note 内禁括号；`_beat_tag` 加 `puppet_guard→"护主"` 进 `_BEAT_ORDER`、tone=`mine`。单场消耗跨场满血（`temper()`）。护主分摊实测40/42/44%，红线50%——别再提概率。守门 `test_doupo_puppet_guard.py`128 + `tmp/mut_pup_guard.py`20。**白守教训**：逐场都查别 break；ceil 与浮点 cap 双保险一起改；怪必须 chance=1.0 强制放技能。⚠️ 读 `guard_hp_left` 不能用 `or -1`（0 是 falsy）。
⚠️ 傀儡库独立 `data/doupo/puppet_data.sqlite`（`puppets`/`puppet_materials`/`puppet_eligibility`/`puppet_ruin_daily`/`meta`），不在 `doupo.sqlite`；`puppets` 无 `active`/`name` 列；`qq='wild'` 是遗迹残傀池。`Store` 懒加载先 `await ds.load()`；`from doupo import store as st` 拿到的是实例。

## 8. 紫卡/429
洗卡链 WFCD→`riven_disp.json`→`riven_stat_base.json`→`wfm_riven_attrs.json`→`riven_recommend_attrs.py`，禁手写 slug 表；击杀效率 vs 市场价差两维度不可混，价差必须 `k=min(n)` 同分位；WFM UA `curl/8.4.0`+`Platform: pc`。守门 `test_riven_recommend_spread.py`45。429 直连 `POST {AI_API_BASE}/chat/completions` 看 `error.code`。

## 9. 源码问答沙盒
`doupo_code_ask`→`POST qq-doupo-agent:8765/ask`，底座锁 openai-agents 禁换 smolagents（丢 tool_calls）。四硬约束：`mkdir -p /repo`；一进程一事件循环；`AGENT_MAX_TOOLS=10`；`max_retries=0` 回500+原因，绝不回空答案；答案禁 Markdown。

## 10. 自选炼制/遗迹连打/赠送
自选材料 `炼制傀儡 自定 <躯体> <魔核> <灵魂> <金属>` 只造 `custom=True` 临时配方，判档/五闸/扣料/落库全走原链路。火不自选（按 `fire_grade(p)`，占位 B 会凭空降一档）；nominal 对 custom 直接取 tier；错名必须报错。守门 `test_doupo_puppet_custom.py`33 / `tmp/mut_pup_custom.py`9。
遗迹连打 `while idx < total`，fight 原地改 hp/mp 即力竭。三防刷：`drops if win else {}`、`ruin_clear(idx+1)` 只在胜场、全通先返回。反解与实战都传真实 p。**服药整趟共用一份 `pill_budget`**（`PUPPET_RUIN_PILL_CAP=2`，建在循环里=每段重置）。顺带修 `core.pill_cap` 的 `or` falsy 坑。守门 `test_doupo_puppet_ruin_auto.py` / `tmp/mut_ruin_auto.py`7。
赠送 `Store.transfer_item()` 一个 `_tx()` 写两行，失败 `clear()+update()` 就地回滚（禁换 dict）。候选池取包裹真实键；`parse_gift` 纯函数只认整串数字=金币；入口 `if item:` 分流（禁 `if False:`）。傀儡材料可送：`puppet_materials` 主键 `(qq,name)`，`PuppetStore.transfer_material()` 一个 `_tx()`，不需并库。送竹简必须同事务 UPSERT `puppet_eligibility.lore=1`；`cfg.PUPPET_LORE_MATERIAL` 单一常量。守门 `test_doupo_gift.py`75 / `tmp/mut_gift.py`22。
全量回归 `tmp/run_all_doupo.py`：**每文件一个 QQ_DATA_DIR**，共用会互相污染。基线红只剩：镜像无 pytest ×3 文件 + `test_doupo_boss_cast` 2条（读盘符语料）。

## 11. 傀儡成卡/子命令回执/调研
🚨 `PUPPET_SUBCMDS` + `puppet_is_subcmd(arg)` 是「出声 vs 出图」唯一真值。handler 曾无条件出傀儡卡→淬炼/结阵/收服/融魂/配方五子命令回执全被吞（真扣了雷淬石金币却只收到没变化的名录卡）。子命令必须 `finish_text`+return；只有看卡走 `render_puppet_for`；`cmd_puppet` 里每个 `arg.startswith("…")` 必须登记（源码守门比对）。
成卡 `cmd_craft_puppet(...,report=)` 只在成功写 rep；`card.build_craft_data(rep)` 只读不重算；`render_craft_for` 无数据返 None（禁拿模板 `_DEMO` 糊）。新卡上线同步三处：`pic/__init__.py`、`channel/doupo_buttons.py`、`tests/test_doupo_buttons.py` family 硬编码表；按钮只许用菜单真实口令。
白守三教训：① `find("调用")>=0` 抓不住 `if x() and False:`，源码守门要正则钉整段结构；② stub 的 `_load_pic()` 返 None，行为测抓不到「无数据出 DEMO」，加源码级断言；③ 测试读 report 一律 `.get(k) or 默认`（硬下标会崩，判据不可信）。**真 matcher 层才有用**：命令层单测绕过 handler，回执被吞也全绿；守门落 `test_e2e.py` 的 `expect_kw`。探针假象：`PYTHONPATH` 含 `src/plugins` 会让 `doupo` 与 `src.plugins.doupo` 各加载一份造出第二个 Store 单例；`get_player` 会把 mp clamp 到 mp_max。
调研（`docs/傀儡培养系统_原著调研_2026-11-20.md`）：养成链=得法→炼制→认主→雷淬→换躯体重炼。实证缺口：`awakened`/`damaged`/`bonded` 是零机制或死字段（`core.py` grep 零命中），`purity` 是唯一有真效果的字段。第二轮证据与 P0-1 设计见 `docs/傀儡培养系统_原著深度调研第二轮_2026-11-20.md`、`docs/远古人傀_怪物面板设计_2026-11-20.md`、`docs/P0-1_结阵临时升档_设计方案_2026-11-20.md`。

## 12. 养魂口径 + 远古人傀（P3）
**养魂**：`soul_strength` = 材料基准值**永不改写**（木桶判档/`_material_mult` 的锚），养魂量进 `soul_fed`（0..200，每枚 +10，不掷骰），有效灵魂唯一读 `puppet_core.soul_total()`。🚨 禁把养魂加进 `soul_strength`（还原不出已喂量、上限失去定义）。服丹 `puppet_soul` 必须拦在 **`take_item` 之前**返 `need_puppet`（否则丹没了什么都没发生）。喂到 190 再要 5 枚只吃 1 枚。老库迁移只许 `_MIGRATIONS` + `PRAGMA table_info` 幂等 `ALTER TABLE ADD COLUMN`（`CREATE TABLE IF NOT EXISTS` 老库不加列），`DB_VER 1→2`。守门 `test_doupo_puppet_soul.py`54 + `tmp/mut_pup_soul.py`15。
**远古人傀**：第七段「斗圣主殿」lv95 hard0.70 `is_boss=True`；`kind` 不能带 `boss`（BOSS 面板 + 技能概率翻倍 → lv75 只有 58%，改普通面板 75~82%）。`cloth=0.60` 只加 `m_def` 且**加在 `m_hp` 之前**（同 `mitigation` 换算 = 回合数不变）。不可收服双证：不进 `PUPPET_MATERIALS`/wild 池。技能「裂山枪」是原著真名。
🚨 **段名 vs 物种名**：段 dict 的 `name` 是段名、`monster` 是物种名；`monster_scaled` 必须用 `_species = monster or name` 查角色包/`MONSTER_SKILLS`，`cmd_puppet_ruin` 传 `dict(sg, name=sg["monster"])`，否则遗迹怪永远只拿 kind 兜底技、战报印段名。守门 `test_doupo_puppet_ancient.py`41 + `tmp/mut_pup_ancient.py`13。
🚨 **台词/原著校验禁 `if os.path.isfile(语料)` 跳过**：容器里没有 `D:/…全本`，跳过=白守（变异实证）。用仓库夹具 `tests/fixtures/doupo_1235_renkui.txt`，缺则报红。**探针造玩家**：`create_player` 只按 lv1 写 hp/mp，改 `lv` 后必须补满 hp/mp，否则「面板满级、血槽 1 级」两回合力竭，截图会骗人。
