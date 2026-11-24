# 游戏侧长期红线（qq_bridge_official 斗破/修仙/紫卡）
> 从 MEMORY.md 拆出，避免主记忆超限。改动前必读。

## 0. 最高声明
玩家数据**必须进库禁 JSON**：新玩法=独立表+真实字段，禁塞 `players.data` blob；库路径只由 `DATA_PATH` 推导；原子写。推送只推 `LiuHao2611/qq_bridge_official`（别推只读 `qq_bridge`）。

## 1. 容器/部署/推送
数据只落挂载卷、容器内一律 `/app/...`；新增持久化同改 compose + `.dockerignore` + `test_runtime_data_paths.py`。存档 `/app/data/{doupo,xiuxian}/*.sqlite` 一人一行，`get_player()` 返活字典、`put_player()` 单行 UPSERT。守门 `test_db_path_follows_data_path_single_source`、`test_game_save_sqlite.py`、`test_user_data_persistence.py`。部署 `docker build -f docker/Dockerfile.bridge -t qq-bridge-official:latest .` → `compose up -d --force-recreate`（`--build` 偶发 exit 1）。msg_type 互斥 0文本/2markdown+keyboard/7图片。镜像无 pytest：按文件隔离跑、脚本式自跑。推送绕 502 `tmp/push_incremental.py <基线commit>` 走 base_tree；blob 缓存 key=`sha1(raw)`；行尾跟远端；**验收标志串先 grep 再写**，只信 PUSH_OK=假通过。

## 2. 战斗/回看
属性↔战斗唯一源 `config.STAT_BATTLE_ROLE`；战报统一 `_battle_line()`、特殊字段一律 `.get`。爬塔难度只由层号 `tower_ref_player(floor_ref_lv(n))`。周副本周一快照、通关 7 层才 commit。最后一场唯一写入口 `core.record_last_battle()`。**终局只有一个数**：`fight()` 算 `hp_end`，存档/结算/回看/人物卡/逐招末行五处同源；战败保底 `LOSE_HP_FLOOR` 5%。回看分页 `PAGE_MAX_H=2600/MAX_PAGES=5`、回合边界切。守门 `test_battle_review.py`218 + `tools/mutate_battle_review.py`38。菜单严格 52 条。自动调息只挂 `Store.get_player()`（出锁后 regen）。自动服药 <35%血/<20%斗气。天焚塔火能 1 点=10 分钟只结算整块、塔中禁修炼。⚠️ `test_typography.py::doupo_menu`(720,3398)、`test_pic_templates.py` 3 红 = 基线红。

## 3. 异火三层投放
23 种；「八荒破灭焱」二手错名，正文作「荒破灭焱」。`cfg.FLAME_TIER`：**S5** 帝炎/虚无吞炎/净莲妖火/三千焱炎火/陨落心炎（剧情锁）、**A7** 金帝焚天炎等（`A_FLAME_SIDE` 隐藏支线静默收服）、**B11** 无特效走 `flame_bonus.py`。入道不送火，首火走九星突破 BOSS：`is_nine_star_breakthrough`→`make_breakthrough_boss`（纯快照反解）→`roll_breakthrough_flame`（other45%>bonus18%）。BOSS 难度靠血量不靠攻击。`FACTION_FLAME_GRANT={}`。帝炎走 `p["diyan"]` 绝不进 `flames`。`feed_flame()` 的 `cur<=0→unowned` 门禁必留。文案禁「本宗异火/传承」。守门 `test_flame_tiers.py`41 + `test_doupo_flame_grant.py`29 + `test_nine_star_breakthrough.py`31。

## 4. 结构雷/输出契约
**包属性被子模块同名遮蔽** → 要成员用 `from .<mod> import <name>`。**卡片渲染首参不统一**：`render_puppet(roster,elig,p)`/`render_ruin(state,day)`/`render_rank(top)`/`render_battle(last_battle)` 必须特判（守门 `test_doupo_buttons.py::test_card_renderer_shapes_match_dispatch`）。一处输出=一条消息。丹药 `desc=原著考据/impl=本作承诺`，卡面只印 `cfg.recipe_impl()`；测试日期用 `date.today().isoformat()`。**批量连炼** `core.craft_batch()` 禁改单发 `craft()` 语义、整批一次 CD；防刷双闸 `low_grade_exp_factor` + 七品以上 `SOUL_REALM_BATTLES` 禁删（`test_doupo_craft_batch.py`25）。

## 5. 沉浸式文案铁律
玩家可见处禁出现 `未载/不代编/代编/游戏原创/游戏命名/游戏设定/语料/原文只有/非原著角色/原著无`。守门 `tests/test_doupo_immersion_gate.py`43 + `tmp/mut_immersion.py`21。登场三段式 appearance→title→intro，`cfg.CHARACTER_APPEARANCE` 唯一真值。零元说明洗在数据层 `factions._strip_cite_tags(FACTIONS)`（剥前快照 `FACTION_CITE_LEDGER` 禁渲染）。技能效果逐型验落地 `tests/test_doupo_skill_effects_land.py`23，新增 effect 必补一条。目标闪避 `mon["dodge"]`→`m_fx["evade"]` 在 `on_hit` 前结算；`t_ev<=0` 绝不掷骰。

## 6. 命中↔闪避 + 怪物面板
唯一口径 `实际闪避%=max(0, 目标闪避%−攻击方命中%)`，三处都扣：`fight()` / `_hit(atk_hit=)` / `on_incoming(hit=)`。玩家命中绝不吃敏捷。`core.monster_panel(lv,kind,is_boss)`：眼力=`level_dodge(lv)−MON_HIT_RESIDUAL(50)`；暴击/身法 `2+0.8k`/`3+1.6k` ×精英1.20/BOSS1.45，眼力不加系数。面板只挂 `monster_scaled`，绝不进 hp/atk 反解（手搓怪零面板 → 200+ 快照零回归）。唯一削弱旋钮 `MON_HIT_RESIDUAL`（50 ⇒ 胜率73%）。九星 `BOSS_BREAK_HP_MULT 1.15`/`ATK_MULT 1.10`（必须>1）。守门 `test_doupo_hit_rate.py`25 + `tools/mutate_hit_rate.py`9。**手搓怪用例必须满级怪+拖满 30 回合**。⚠️ 禁再引入「基准闪避补偿」常量（`DODGE_DESIGN` 已否决删除）；别手算面板，必须真跑 `core.monster_panel()`。已知未改：精英系数对塔/周本 kind（`"塔·elite"`）失效；「压闪避」7 门斗技 PVE 全废。

## 7. 傀儡三件套（配方/锚定/护主）
判档唯一入口 `puppet_core.recipe_tier(slots)`（`min(五维)−1` 封顶 2，缺维 −1），`first_craft_tier()` 已弃用。材料名必须原著真名（魔核只 火属性/七阶/高阶；金属 精铁/暗金玄铁/寒铁/焰陨玄铁）。`PUPPET_MP_FACTOR=(0.60,0.80,1.00)`。配方 18 条，`tier`(实况)/`tier_nominal`(分组)；傀儡料只进 `puppet_out`（`test_doupo_puppet_recipes.py`82）。
**战力锚定（三轮定稿别改回去）**：绝对常数与倍率常量全删；现行 = `min(躯体境界标尺×材料系数, 玩家每回合输出×PUPPET_ATK_CAP[(0.55,0.80,0.95)])`。`puppet_realm_lv()` 按第 757 处折星（`PUPPET_BODY_STAR_PENALTY=2`），`PUPPET_TIER_REALM_LV=(55,68,73)` 仅 body_realm 缺失回落。🚨 封顶叫 `PUPPET_ATK_CAP`（`PUPPET_TIER_CAP` 已被材料档占用 → TypeError）。材料系数乘在境界标尺上、封顶管最终值；结阵/异火 ×1.30 同理，封顶落 `puppet_expected_damage.total`。🚨 `PUPPET_ENEMY_COMP=0.55` 是「带傀儡有没有意义」唯一开关（=1.0 完全对消）；`_pup_atk` 必须走 `puppet_expected_damage`（守门 `test_fight_single_source`）。命令层唯一取数口 `commands._puppet_ctx(qq,p)`；结阵上限 `PUPPET_FORMATION_MAX=3`（`test_doupo_puppet_anchor.py`83）。卡面标签 `puppet_realm_label()` 不许带括号。**白守教训**：测试自注入参数抓不到 cfg 被改，须另加 config 实际值/绝对境界名断言。
**护主挡伤** 全在 `puppet_core.guard_profile()`（卡面与 `core.fight` 同调）：`CHANCE=(0.18,0.25,0.32) CAP=0.40 LEAK=0.35 HP_RATIO=(0.25,0.40,0.55) ARMOR_TAKE=(0.78,0.68,0.58) THRESHOLD=0.06 HURT_AT=0.30/MULT=0.5 FORMATION_GUARD_STEP=0.06`。🚨 护甲绝不按玩家防御比例放大；正解锚穿透率反解 `armor=MITIGATION_W×(1/take−1)×材料系数`（`MITIGATION_W=800`），钉 `hasattr(cfg,"PUPPET_ARMOR_RATIO") is False`。🚨 吸满上限必须浮点 + `math.ceil`（int 两步截断 → hp_left=1 打不死）。rng 红线：挡伤骰与 `res["puppet_guard"]` 只在 `if puppet` 内。文案：note 必须 `notes.insert(0,…)`（append 被 `TAG_LIMIT=4` 截）、note 禁括号；`_beat_tag` 加 `puppet_guard→"护主"` 进 `_BEAT_ORDER`（tone=mine）。护主分摊红线 50%（实测 40/42/44%）。守门 `test_doupo_puppet_guard.py`128 + `tmp/mut_pup_guard.py`20。⚠️ 读 `guard_hp_left` 不能 `or -1`（0 是 falsy）。
⚠️ 傀儡库独立 `data/doupo/puppet_data.sqlite`（`puppets`/`puppet_materials`/`puppet_eligibility`/`puppet_ruin_daily`/`meta`），不在 doupo.sqlite；`puppets` 无 `active`/`name` 列；`qq='wild'` 是遗迹残傀池。`Store` 先 `await ds.load()`；`from doupo import store as st` 拿实例。

## 8. 紫卡/429
洗卡链 WFCD→`riven_disp.json`→`riven_stat_base.json`→`wfm_riven_attrs.json`→`riven_recommend_attrs.py`，禁手写 slug 表；击杀效率 vs 市场价差两维度不可混，价差必须 `k=min(n)` 同分位；WFM UA `curl/8.4.0`+`Platform: pc`（`test_riven_recommend_spread.py`45）。429 直连 `POST {AI_API_BASE}/chat/completions` 看 `error.code`。

## 9. 源码问答沙盒
`doupo_code_ask`→`POST qq-doupo-agent:8765/ask`，底座锁 openai-agents 禁换 smolagents（丢 tool_calls）。四硬约束：`mkdir -p /repo`；一进程一事件循环；`AGENT_MAX_TOOLS=10`；`max_retries=0` 回 500+原因绝不回空；答案禁 Markdown。

## 10. 自选炼制/遗迹连打/赠送
自选材料 `炼制傀儡 自定 <躯体> <魔核> <灵魂> <金属>` 只造 `custom=True` 临时配方，判档/五闸/扣料/落库全走原链路；火不自选（按 `fire_grade(p)`）；错名必须报错（`test_doupo_puppet_custom.py`33）。遗迹连打 `while idx<total`，fight 原地改 hp/mp；三防刷 `drops if win else {}`/`ruin_clear(idx+1)` 只胜场/全通先返。**服药整趟共用一份 `pill_budget`**（`PUPPET_RUIN_PILL_CAP=2`，建在循环里=每段重置）。赠送 `Store.transfer_item()` 一个 `_tx()` 写两行，失败 `clear()+update()` 就地回滚；候选池取包裹真实键；`parse_gift` 只认整串数字=金币；入口 `if item:` 分流。傀儡材料可送（`puppet_materials` 主键 `(qq,name)`），送竹简同事务 UPSERT `puppet_eligibility.lore=1`（`test_doupo_gift.py`75）。全量回归每文件一个 `QQ_DATA_DIR`；基线红 = 镜像无 pytest×3 + `test_doupo_boss_cast` 2 条。

## 11. 傀儡成卡/子命令回执
🚨 `PUPPET_SUBCMDS` + `puppet_is_subcmd(arg)` 是「出声 vs 出图」唯一真值；子命令必须 `finish_text`+return，只有看卡走 `render_puppet_for`。新卡上线同步三处：`pic/__init__.py`、`channel/doupo_buttons.py`、`tests/test_doupo_buttons.py` family 表。**真 matcher 层才有用**（命令层单测绕过 handler，回执被吞也全绿）→ 守门落 `test_e2e.py` 的 `expect_kw`。探针假象：`PYTHONPATH` 含 `src/plugins` 会造第二个 Store 单例；`get_player` 把 mp clamp 到 mp_max。

## 12. 养魂口径 + 远古人傀（P3）
**养魂**：`soul_strength`=材料基准值永不改写，养魂量进 `soul_fed`(0..200,+10/枚)，有效灵魂唯一读 `puppet_core.soul_total()`。🚨 禁把养魂加进 `soul_strength`。服丹 `puppet_soul` 拦在 `take_item` 之前返 `need_puppet`。老库迁移只许 `_MIGRATIONS`+`PRAGMA table_info` 幂等 ALTER（`DB_VER 1→2`，`test_doupo_puppet_soul.py`54）。
**远古人傀**：第七段 lv95 hard0.70 `is_boss=True`；`kind` 不能带 `boss`。`cloth=0.60` 只加 `m_def` 且加在 `m_hp` 之前。不可收服双证：不进 `PUPPET_MATERIALS`/wild 池。🚨 段名 vs 物种名：`monster_scaled` 用 `_species=monster or name`，`ruin_scaled_monster` 内部传 `dict(sg,name=sg["monster"])`（`test_doupo_puppet_ancient.py`41）。🚨 校验禁 `if os.path.isfile(语料)` 跳过（容器无盘符语料）→ 用夹具 `tests/fixtures/doupo_1235_renkui.txt`。探针造玩家：改 `lv` 后必须补满 hp/mp。

## 13. 遗迹固定难度 + 后台战力推荐（2026-11-22，用户二选一定稿）
🚨 **难度唯一真值 = 每段后台 `power_rec`**，怪由 `core.ruin_scaled_monster(sg,...)` 按 `core.ruin_ref_lv(power_rec)` 的**标尺玩家**反解，与真人战力无关（斗尊打斗宗段=真碾压）。回退 `monster_scaled(p,...)`=自适应=永远没碾压感 ⇒ E2E 守门红。`power_rec==power(tower_ref_player(该段 lv))` 非手拍常数，**后台字段绝不展示前台**。傀儡 bonus 也按标尺算（同 tower_floor），真人强傀儡在 fight 全额出手=净收益。守门 `test_doupo_ruin_fixed.py`206（含 E2E 走真命令比对不同等级落库 foe_hp_max——白盒只测 helper 会被 mutant 骗过）。
🚨 **残血暴毙是正常设计不许修**：用户原话「残血就残血，只要不是BUG，残血就应该自动吃药，没药吃就等死，这是正常的」。连打跨段结转 hp/mp + 服药整趟共用 `pill_budget`(cap=2) + 战败即停三件套原样；禁加段间回满血/`_refill(p)`/提高药预算。

## 14. 兽火/异火两类身份 + 入道零送火（2026-11-24，用户「送异火不合原著」）
🚨 **入道/转宗绝不送火**：`STARTER_FLAME=""`、`FACTION_FLAME_GRANT={}`。原著异火极稀有，白送=毁设定。禁任何"新手福利/补偿"式恢复送火（`test_doupo_flame_beast.py` 有守门用例）。
🚨 **火焰身份只有两类**：异火 = `FLAME_NAMES + BONUS_FLAME_NAMES + FUSION_FLAMES`（琉璃莲心火/三千莲心火 属异火）；兽火 = `BEAST_FLAMES`（8 朵）。禁再出现"天然火/人造火/融合火/其他火焰"分类（玩家可见文案守门）。`p["flames"][name]` 是唯一真值，禁新增玩家字段。阳火/化生火数据已删，台账留 `docs/九星突破BOSS战与火焰掉落_2026-11-19.md`；`化生火` 只剩**弄焰诀技能文案**（原著第一百四十章，不入 `p["flames"]`），别当残留删掉。
🚨 **引火通道不许并进普通炼药率**：`BEAST_SEAL_RATE=0.70`，兽火单方不吃 `craft_rate_for` 越阶惩罚。原因：新号 `alch=ALCH_NONE(-1)`、紫火丹四品越 4 档 = `0.5×0.05=0.025`，与"空火禁炼药"互锁 → 第一朵火永远炼不出。入口唯一 `config.rate_for_recipe(p, rec, flame_bonus)`，`craft`/`craft_batch` 共用（禁双真源）。
🚨 **掉落门槛唯一**：`YIHUO_DROP_MIN_REALM=7`（斗宗）。realm<7 → 兽火池 500‰(50%)；realm≥7 → 异火池 1000‰(必得)。两池都按 realm 梯度（`BEAST_DROP_POOL` 0/2/4/6；`YIHUO_DROP_POOL` 7/9），禁"全池随机"（会 1 级掉血妖焰火）。野外 `MONSTER_FLAME` 由 `MONSTERS.flame` 汇总，禁另抄一份表；血妖焰火无单一魔兽（近千火魔兽血凝），只能单方/异火档。
🚨 **幻金火无原著出处**（语料 0 命中）→ 必须 `game_only=True` + 标"游戏原创"，**禁编章节号**。紫晶翼狮王六阶(131章)/奎木兽五阶(216章)/海魔兽七阶(1075章) 有语料可引。
🚨 **宝库必须分两卡**：93 种挤一卡 4744px > 3600 上限不可读。按 `HERB_BY_NAME.src` 前缀 `"兽火单方"` 分（基础 65／兽火 28），入口「宝库 兽火」；`test_card` 守两卡高度。
⚠️ **测试解释器**：PATH 上 `python` 是 3.13.12.old（无 pytest）。用 `/c/Users/L/AppData/Local/Programs/Python/Python313/python.exe`。`src/plugins/doupo/test_*.py` 是**自跑脚本**（`python 文件`），pytest 收集必报 NoneBot 未初始化。
⚠️ **`tmp/push_incremental.py` 文件模式**：已改读 `git ls-files -s`（原 `os.access(X_OK)` 在 Windows 恒真 → `.txt` 被推成 100755，远端 tree 与本地永久不等）。有第 2 参可指定 parent，重做误推时不叠噪音提交；`/git/commits/{sha}` **必须全 SHA**（短 SHA 404）。

## 15. 背包全量不翻页 + 丹纹三档 + 格子角标（2026-11-24，用户「背包别搞翻页，延长…改完直接上线」）
🚨 **背包不许再分页/截断**：`bagview.page_of` / `PER_PAGE` 已**删除**，`build_bag_data(p, locked=None)` / `render_bag(p, locked=None)` / `cmd_bag(store, qq)` **都没有 page 形参**。禁恢复 `items[:24]`、禁再加翻页按钮、禁往卡面塞 `page`/`total_pages` 字段（`test_bagview.py::t_no_truncation` 守门，64 种必须 64 行）。卡高随物品数增长，可到 1300px+ 属正常。
🚨 **带纹丹卡面/文本都不改名**：显示 `cfg.base_of_danwen(name)`，纹数只走行 dict 的 `corner_tr`（一纹/二纹/三纹，几纹写几），锁定只走 `corner_tl="锁"`。禁再把「纹·丹名」写进 title、禁回退成 `〔纹〕/〔锁〕` 前缀。
🚨 **角标字色只能取调色板 `gold`**（浅 `#B07A16` / 深 `#FFD058`），因为要和「包裹」牌匾同族金；禁用 `accent_color`/硬编码色（`test_bagview.py` + `tmp/check_bag_boxes.py` 双主题守门）。
🚨 **`S.small` 默认 `anchor="lm"`（垂直中线）**：给它是「中线」不是「上缘」。旧代码传 `cell_y+3` 导致 14px 小字浮到格子上方 4px（真图抓到）。用 `doupo_list.corner_cy(y)`，`CORNER_TOP=2`；名称中线 `GRID_NAME_CY` **恒为常量**，禁按「有无角标」偏移（同排基线必须一致）。
🚨 **丹纹档位必须真有效果**，不能只是标签：`_TIER_SCALE=(1,1,1.5,2)` **只放大次数/时长、百分比一分不动**（用户定的平衡杠杆=限时/限次）；三纹额外多赐一条福且躲开同 id（同名 `max()` 刷新会白吃）。存量 `·丹纹` 永久保留为别名按一纹读，**禁删**（老档会失效）。
🚨 **变异反证禁 `git checkout -- <file>`**：当天 23:40 用它还原 `card.py`，把未提交的 build_bag_data 重写一起冲掉了（退回分页版）。只许 `cp` 到 /tmp 备份、`cp` 回来，再 `git diff --stat` 复核增删行数。
