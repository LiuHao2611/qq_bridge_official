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
🚨 **「真名」与「背包展示」是两件事，绝不许统一**（2026-11-25 用户第二次强调：「别改我背包的丹药展示效果，那是我的独特设计，只是让你改口令和命名，不要改其他的」）：
　　· **真名**（存档键 / 口令输入）= 前缀式 `一纹破厄丹`，唯一生成入口 `cfg.danwen_name(base, tier)`；`DANWEN_INDEX` 是「带纹名→(基础丹名,纹数)」唯一真源，**禁用前缀/后缀猜测**（原著丹 `三纹青灵丹`/`二纹青灵丹` 会被误判）。旧后缀（`·丹纹`/`·一纹`/…）**只作迁移源**，`core.migrate_danwen_bag` 幂等改名合并 + `store.rename_item_lock` 同步锁表。
　　· **背包格子展示**（`card.build_bag_data`）= `cfg.base_of_danwen(name)` + `corner_tr` 纹字角标 + `corner_tl="锁"`，副标题走「丹纹丹：…」。这是用户亲手定的视觉，**禁改成「一纹聚气散」塞进标题**、禁以「信息重复」为由删角标（本轮已犯过一次，被用户明确纠正）。
　　· **「背包」纯文本**（`commands.cmd_bag`）同口径：`基础丹名 ×N（一纹）`，不把纹数写进名字。
🚨 **角标字色只能取调色板 `gold`**（浅 `#B07A16` / 深 `#FFD058`），因为要和「包裹」牌匾同族金；禁用 `accent_color`/硬编码色（`test_bagview.py` + `tmp/check_bag_boxes.py` 双主题守门）。
🚨 **格子角标 = 楷体 + 金色光晕 + 骑在格子上边框（截断框线）**（2026-11-25 用户二次定稿；**2026-11-25 起只服务左上「锁」，纹数已进名称**）：
唯一入口 `doupo_list.draw_corner_badge()`。①字体必须 `UI.font(size,"kai")`（doupo_kai.ttf，与「包裹」牌匾同族），禁退回等线；②光晕必须 `GaussianBlur` 且光晕层比色块大 `CORNER_GLOW`，**禁叠两层 + 禁竖向伪粗**（小字会糊死），伪粗只取横向 ±1；③必须先用 `p["panel"]` 实心块盖断格顶框线再压字（这是「截断边框」的全部观感来源）；④`CORNER_INSET` 必须 > 格子圆角半径 8，否则把圆角啃成方角。
🚨 **`S.small` 默认 `anchor="lm"`（垂直中线）**：给它是「中线」不是「上缘」（旧代码传 `cell_y+3` 让小字浮到格子外）。角标一律走 `corner_cy()/corner_top()/corner_bottom()`；名称中线 `GRID_NAME_CY` **恒为常量**，禁按「有无角标」偏移（同排基线必须一致）。
⚠️ **斗破卡存在两套主题**：本地默认渲染的是深色 game 风，**手机生产实际出的是浅色**（浅 lavender 格 + 深色字）。改任何卡片配色/角标后，`light` 与 `dark` 都要实渲染看图，别只按一边下结论。
🚨 **丹纹档位必须真有效果**，不能只是标签：`_TIER_SCALE=(1,1,1.5,2)` **只放大次数/时长、百分比一分不动**（用户定的平衡杠杆=限时/限次）；三纹额外多赐一条福且躲开同 id（同名 `max()` 刷新会白吃）。存量 `·丹纹` 永久保留为别名按一纹读，**禁删**（老档会失效）。
🚨 **变异反证禁 `git checkout -- <file>`**：当天 23:40 用它还原 `card.py`，把未提交的 build_bag_data 重写一起冲掉了（退回分页版）。只许 `cp` 到 /tmp 备份、`cp` 回来，再 `git diff --stat` 复核增删行数。

## 16. 官方通道纯文本只能走 OfficialChannel（2026-11-25，「上阵/学习/锁定/主题」打字没反应）
🚨 **官方通道的纯文本回复必须经 `OfficialChannel.send`，原生 `matcher.finish(str)` 会被平台静默吞掉**：当日 00:15–00:25 七条纯文本口令全部「命中 matcher + `running complete` + 无异常 + 用户收不到」，同批卡片全部正常。修复=`game_compat.install_official_text_bridge()` 拦 `QQBot.send`。
🚨 **补丁边界不许放宽**：只拦 `isinstance(message, str)` 且非空白。**一旦放进 `Message`/`MessageSegment`（图片段）就会丢图**；通道返回 False 或抛异常**必须回退原生**（宁可发得难看也不能不发）。`tests/test_official_text_bridge.py` 守这四条。
🚨 **`msg_seq` 必须与原生 `event._reply_seq` 共用一个计数器**（`_seq_seed` / `_seq_commit`）：通道自带 `reply_ref["msg_seq"]` 从 1 起，原生 `QQBot.send` 也是 `event._reply_seq += 1`；两边各数各的 → 同一 msg_id 的第二次发送必撞 seq=1 → `40054005 消息被去重`。
🚨 **`_seq_commit` 里禁 import 通道包**（`src.plugins.qq_bridge.channel.*` 顶层会拉 httpx），否则宿主纯标准库测试直接不可跑；只读 `reply_ref["msg_seq"]` 字段即可，口径与 `passive_seq.peek_msg_seq` 一致。
⚠️ **排障先看 `logs/service.log` 而不是 `qq_bridge.log`**：前者有**每条入站原文 + 命中 matcher 的 lineno + `running complete`/`running failed`**；后者只有出站与 pic_render。「没反应」三分类照它查：①无 `Event will be handled` = 无 matcher 匹配（口令写错/模式门）；②有 handled 有 complete 无出站 = 发送出口问题；③有 failed + 栈 = 代码异常。
⚠️ **「running complete」≠ 发成功**：nonebot 只有抛异常才打 failed，平台静默吞消息时日志一片祥和。
⚠️ **测试里别用「假通道抛异常」当哨兵**：会被回退 `except` 吞掉，用例假通过。要用「记账断言通道是否被调用过」。
⚠️ **`帮助` 存在双 matcher 重复**（`doupo:178` + `game_tools.official_bridge:364`），靠 `40054005` 去重才没变成两条回包；根治要收敛出口，**不许只改 seq**（会立刻变成两条）。
🚨 **单行正文必须补成多行**（2026-11-25 第三轮，同一事故的更深一层）：走通道只是必要条件，**不是充分条件**。「多行 172 字到达、单行 42 字消失」同晚成对出现（00:57:05 到达 / 00:57:16 消失），与 msg_type 无关。所有官方文本出口统一过 `game_compat.prepare_official_text()`：`pad_single_line`（单行补第二行页脚，多行/空白原样、必须幂等）+ `escape_markdown_angles`（`<技名>` → `＜技名＞`，否则 markdown 当 HTML 标签，轻则吞字重则 `40034011 无效 markdown content`）。**禁止**在 `_channel_send_text` 里绕过这两个函数直接发原文（`tests/test_official_text_bridge.py::channel_text_is_padded` 结构门禁）。方括号 `[特殊]`/`[已领悟]` 不用转义（markdown 里无 `(url)` 的 `[x]` 按字面渲染）。

## 17. 「查看 <物品名>」详情卡 + 炼药卡精简（2026-11-25，用户「加一个查看物品的功能…几纹有什么概率出什么加成。从何而来」）
🚨 **不给出纹版的丹，出货路径必须先过闸**：`cfg.danwen_allowed(rec)`（跳过 `beast_flame`/`skill`/`puppet_soul`）。旧版 `core.craft`/`craft_batch` **不看这道闸**，给升技材料 `焚血散` 掷出了 `焚血散·一纹`，而注册表不给它建带纹版 ⇒ 线上躺着一枚**既不能服、不能锁、卖不掉**的死物品（实测抓到）。`test_use_danwen` 用「`_t := dw.roll(` 出现次数 == 被 `cfg.danwen_allowed` 包住的次数」做结构门禁，**改出货路径必看这条**。
🚨 **孤儿带纹名迁移回基础丹**：`migrate_danwen_name` 算出的新名不在 `DANWEN_INDEX` 里时退回 `base`（`焚血散·一纹` → `焚血散`），不要把死名搬到新命名上。
🚨 **物品分类唯一源 `cfg.item_kind` + `cfg.MISC_ITEMS`**：卡（`card.build_item_detail_data`）与文本（`commands.cmd_view`）**共用同一张表**，禁止各判一套（会出现「卡里有丹纹段、文本说不认识」）。`查看` 查**图鉴**不查背包 —— 宝库没买的药材也要看得见；`服丹`/`锁定` 才要求背包真有。
🚨 **丹纹段不许自己重算数值**：`danwen.scale_of/fmt/tier_rule/tier_table` 是唯一出口。`tier_table` **只标「变了的那一个数」**，且**只在文案含 `{n}`/`{min}` 时才标**（B02/B06 的 `n` 是充能数，句子不引用它，照缩放映成「二纹 2 次」会与前半句「首次」自相矛盾）；单位只写一次，否则每条多折一行。
🚨 **玩家可见文案禁出现章节号与工程口吻**：`RECIPE_IMPL_OVERRIDE` 里 44 条「（无封印系统）」「（无寿命系统）」「（玩法未上线）」已全部清掉，**别再写回去**；`天火融雪丹` desc 原本是考据笔记（含「第900章」），已改成一句药性、考据留在 `src`。`test_view_item.py::test_no_chapter_number_or_meta_on_card` 全量扫「第N章 / 无X系统 / 未上线」。
🚨 **炼药卡 `desc` 只印 `cfg.recipe_impl`**：禁再拼〔功效标签〕/把握/越档/丹纹率（那是详情卡与「炼药」文本的活）。`RECIPES_PER_PAGE=14`；材料行必须**按「、」整项换行**（`Canvas.wrap` 按像素硬折会把「黑岩根×2」劈成「黑岩」+「根×2」）。
⚠️ **`core.py` 204648 B / `commands.py` 202304 B，离 200KB 红线只剩 152 / 2496 B**：任何一次编辑都会被 `.githooks/pre-commit` 拦死。下一步必须真拆（建议把 `core.fight()` ~670 行挪到 `battle_core.py`），**禁靠 --no-verify 或删注释凑数**。
⚠️ **`tools/phone_deploy.py` 临时区按相对路径落盘**：早先平铺 `/tmp/push/`，一次 push 两个 `__init__.py` 会互踩（L3 报「传输被破坏」，易误判成网络）；`mkdir` 必须用 `os.path.dirname`（`Path().parent` 在 Windows 给反斜杠）。

## 18. 官方通道「按钮在同一次回复里只许出现一次」（2026-11-25，用户「机器人重复发送按钮问题，不要再出现」）
🚨 **重复按钮事故（已修，禁改回去）**：用户截图 = 战斗文本挂「战斗回看/战斗评价/任务」，紧接着又一条标题「## 快捷指令」挂**同一组**按钮。根因：纯文本为修「客户端空白」已改成 `markdown + keyboard` 同段（`seg_kind="text_card"`，键盘在文本段上），而下面还留着旧降级条件 `has_buttons and (not wanted_card or ...)`——`wanted_card` 只在「有图+有口令+有按钮」时为真，**纯文本恰好为假** → 成立 → 又补发一条独立按钮段。
🚨 **唯一判定口 `official.needs_standalone_keyboard(has_buttons, wanted_card, image_only_fallback, text_has_kb)`**：文本段挂上键盘即置 `text_has_kb=True`，之后一律不再发独立按钮段。**不要**把判定改回裸条件，也不要把「按钮段」挪到别处再补一遍。守门 `tests/test_official_no_dup_keyboard.py`（7/7，含三处变异反证：忽略 `text_has_kb` 立刻复现事故）。
⚠️ **纯按钮回复不能误关**：无文本、无图的纯按钮消息仍须发独立按钮段（`text_has_kb=False` 分支），否则按钮直接消失——这比重复更糟。
⚠️ **官通打点在 `logs/service.err.log`，不在 `service.log`**（`service.log` 只有 `official_event_arrived` 那类 print）。查发送链路一律 `grep official_sent service.err.log`；同一次回复两条就是 `seq=1 text_card` + `seq=2 keyboard` 的指纹。
⚠️ **`at_user` 分支（群内 @人）不挂键盘**（mention 段与 markdown/keyboard 混段未实测），那里仍会跟一条独立按钮段——这是**必要**的两条，不是重复，别当 bug 修。

## 19. 战斗播报：去章号 + 13 章台词 + NPC 插话（2026-11-25，用户「去掉第几章节的声明，然后继续把没做完的做完」）
🚨 **战报首行不许出现章号**：`chapter_line` 只渲染 `📍 区域　妖物 lvX`；`ch` 形参**保留但绝不参与渲染**。任务面板（📜 任务）不在范围、仍写「第 N 章」，别误删。守门 `test_battle_head_has_no_chapter_number`（含源码 AST 变异反证）。
🚨 **13 章逐字台词全覆盖，出处只留数据表**：`CHAPTER_QUOTE` 13 章齐，出处在第二列、绝不进玩家可见文案。新句必须 `tmp/novel/q.py` 逐字反查命中，不许硬截叙述凑数。⚠️ `quote_line` 的 seed 轮换回落旁白是**原设计**（不是没做完），`special=True`（苦战/败局）才必出台词。
🚨 **NPC 插话三件事**：`NPC_SPEAKERS`＝角色出处台账（说话人+逐字台词+出处三元组，出处不渲染），`CHAPTER_NPC`＝章节→说话人白名单（不许跨章借角色），只在 `war_tier ∈ {lose,narrow,crush}` 于 💥 后插一条 `🗣 说话人：台词`，顺风局静默。第 12 章**故意留空**——蝎龙族那句已作点睛，加回去会连出两遍（守门 `test_npc_only_in_hard_fights_and_no_duplicate`）。
⚠️ **三个池禁止重复**：取景 `CHAPTER_SCENE` / 点睛 `CHAPTER_QUOTE` / 插话 `NPC_SPEAKERS` 各司其职，同一句话不得在两个池出现。
⚠️ **deploy drift 检的是 `src/**/*.py` 全量**：测试文件也在这个树里，改了 `src/plugins/doupo/test_*.py` 也必须一起 push，否则 drift 报「旧版 1 个」。

## 20. 战斗没反应：DNS/gateway + NetworkError 降级丢 card_key + 启动脚本污染（2026-11-25，用户「我发战斗没反应」）
🚨 **启动必须先等 DNS 可解析**：重启时「默认路由已起、DNS 未就绪」→ QQ 适配器拉 gateway
失败一次（`Failed to get gateway info`）后**不自愈**，消息从此进不来。`start_pre` 有 DNS
守卫（只等不修：`getent hosts bots.qq.com`，18×3s）；**绝不**把 rebind/relogin/restart
写进 connwatch（红线：detect+alert only）。新 bot 连上以日志 `Bot 1906371548 connected`
+ `[EventType.READY]` 为准，别只看进程在不在。
🚨 **网络失败降级会丢战斗按钮**：`finish_text(card_key="fight_text")` 通道首发遇
NetworkError → sent=False → 降级兜底桥 `_channel_send_text(text)` 没带 kcmd → 挂回主菜单
按钮。修复在 `game_compat._channel_send_text`：网络类失败（NetworkError/ConnectError/
Timeout/Try again/API request failed）**即时重试一次且保留同一 keyboard_cmd**；**业务失败
（去重/配额/4005xxx）绝不重试**（会再撞 40054005）。重试由 `official.py::next_msg_seq`
拿新 seq，不会撞车；首发 NetworkError 无 `official_sent` = 未送达，重试不双发。
守门 `tests/test_official_text_bridge.py`（17/17，删重试打红 2 条）。
🚨 **`/etc/init.d/qq-bridge` 首行必须 shebang**：曾被误写首行 `BACKUP_OK`（加固 DNS 时
echo 标记混进文件头）→ shebang 掉第二行 → `rc-service` 报 `Exec format error`，进程还在但
**下次必启不来**。改完启动脚本必查 `head -1`=`#!/sbin/openrc-run` + `sh -n` 语法 +
`rc-service status`=started，三样缺一不可。
⚠️ 排查顺序：`service.log` 看入站/matcher，`service.err.log` 看 `official_sent`/
`channel_send_failed`/gateway；`buttons=入道,人物,修炼,战斗,宝库` = 降级丢了 card_key 的指纹，
正常战斗回执应 `buttons=战斗回看,战斗评价,继续战斗,任务`（2026-11-25 晚加了「继续战斗」，见 §22）。

## 21. 业务服务看门狗：掉了就全停全启，绝不单独 restart（2026-11-25，用户「服务只要掉了，就先强制关闭所有服务，然后启动，不要走单独启动」）
🚨 **重启唯一出口 `qq-service-restart-all`（root）**：反序停（qq-doupo-agent→qq-bridge）
→ `hard_kill_if_alive` 兜底 → 正序启（qq-bridge→qq-doupo-agent）→ 验证 8081/8765 端口。
**绝不 `rc-service <svc> restart`**（单独 restart 撞脚本坏 = Exec format error，服务起不来）、
**绝不手动 rm pidfile**（OpenRC start 自己处理，手删反致 start-stop-daemon 误判）、
**必须 root 自检**（pmos 手动跑 rm/stop 失败会把服务停死，实测踩过）。只动业务两服务，
**绝不碰 sshd/networkmanager/nftables/wifi-watchdogd，绝不 reboot/rebind/nmcli**（停基础服务
会失联断网，rebind 会把 wlan0 玩消失）。
🚨 **看门狗 `qq-service-watchdogd`（OpenRC default 自启，root 常驻）**：判死只盯 **8081**
（agent 8765 抖动不误杀 bot），10s 巡检、连续 3 轮判死、开机 90s 宽限、15min 重启冷却。
**usb0 跳过判据必须是「有来自 172.16.42.x 的活跃 SSH」**（`netstat -tn|grep ':22 .*ESTAB.*172\.16\.42\.'`），
**绝不能用 `ip addr show usb0|grep inet`** —— usb0 常态有 172.16.42.1/16 且 UP，那样看门狗
永不重启（实测 20:21/20:22 连续跳过）。WiFi SSH（192.168.0.x）不触发跳过。
🚨 资产在 `tools/phone_ops/`（三个脚本 + `test_service_watchdog.py` 9/9），手机装
`/usr/local/bin/` + `/etc/init.d/`，**不在 `src/` 不影响 drift**。改看门狗必跑该守门 +
真机受控演练（停 bot→看门狗 30s 内自动全停全启→端口恢复+Bot connected+READY）。
日志 `grep qq-svc-wd /var/log/messages`（不是 service.log/err.log）。

## 22. 每个回复按类型配对应按钮，绝不批量塞主菜单（2026-11-25，用户「处理好每一个回复内容下面的按钮，无论是卡片还是纯文字，别让我再强调这一点」）
🚨 **用户已明确强调过、不要再犯**：不能所有回复都挂同一组主菜单按钮（`入道,人物,修炼,战斗,宝库`），
也不给每张卡塞「帮助/菜单」。每张卡/每类纯文字回执只挂**该语境的下一步功能按钮**。
- **唯一派生源** `doupo_buttons.py`：`CARD_ACTIONS`（每卡功能族，组合两两不同）+ `CARD_ACTIONS_PAIRS`
  （label 覆盖，如 cmd=「战斗」显示成「继续战斗」）+ `REPLY_CARD_KEY`/`reply_card_key_for()`
  （入站口令首词→回执 card_key）+ `item:<丹名>` 动态按钮（丹药→炼制此丹/买主材料/炼药/背包）。
- **纯文字收口**在 `game_compat.py` 兜底桥 `QQBot.send`：从入站 plaintext 派生 `doupo:<card_key>` 传通道，
  一处覆盖全部 `matcher.finish(str)`，**不要逐个改 47 处 handler**。查看卡出口传 `item:<丹名>`。
- **新按钮必须同时补三处**否则点击无反应：① `CARD_ACTIONS`（+`CARD_SELF` 虚拟 self）；
  ② `button_dispatch._PLAN`（含 custom 带参 head 如「炼制」）；③ 测试 expected 表。
  带参按钮链路已通：`doupo:cbtn:` → qq_interaction → `dispatch_custom` → `cmd_craft/cmd_buy(name)`。
- **守门**：`tests/test_doupo_buttons.py`（20 项，含入站映射/label/动态按钮/兜底桥派生）、
  `tests/test_official_text_bridge.py`（18 项）。改这块必跑两文件 + 真变异反证（删功能守门必须打红）。
- ⚠️ 本机 `py -m pytest tests/` 全量有 55 failed/39 errors，全是 py3.13+pytest9 本地环境问题
  （HEAD 基线同样红，worktree 对比证实），**不要误判为本次改动回归**；改动相关守门单独跑必绿。

## §23 斗技序号 / 批量服丹 / 自动爬塔（2026-11-25 晚，用户三连要求）

- **上阵序号唯一真值源 = `core.learned_no_map`**（只含已领悟技，「斗技」卡连续序号：
  ①已上阵四槽 ②其余按三阶）。**宗门卡（`build_roster_data`）序号必须永远 None**——
  宗门池含未解锁技会跳号，玩家照号上阵必错位（本次 BUG 根因）。
  `card.build_learned_data` 与 `cmd_loadout` **不得各写一份 sorted**，否则再次错位（守门会抓）。
  学习成功必须回执「它在「斗技」卡是 N 号」（`core.learned_no_of`）。
- **`roster_no_map` 只许用于宗门卡展示/历史兼容，上阵链路禁止再用**。
- **混吃拆分必须保守**：`_split_use_segments` 只要有一段对不上已知物品名就**整串不拆**，
  退回单丹解析——`服丹 回灵丹 二纹` 里的「二纹」是后缀不是丹名，拆错等于口令坏了。
  批量服丹硬顶 `cfg.PILL_USE_BATCH_CAP` 只防手抖，**不是业务限制**（用户「不限制数量」）。
- **电梯已彻底删除**（matcher / `cmd_tower_elevator` / `td.elevator_cost` / 两个费用常量），
  **爬塔默认从 `tower_checkpoint + 1` 层开**，不许改回第 1 层（`cmd_tower` 与 `cmd_auto_tower` 同口径）。
- **自动爬塔**：裸口令只回确认提示 + `card_key="auto_tower_prompt"`（`doupo_buttons` 特判两个
  custom 按钮），点击走 `doupo:cbtn:自动爬塔 是/否` → `_PLAN` → `cmd_auto_tower`。
  「否」= 不吃加成 + `chest_bonus` 掉落补偿；单次上限 `AUTO_TOWER_MAX_FLOORS` 防死循环刷屏。
- **守门清单（改这三块必跑）**：`src/plugins/doupo/test_roster_card.py` + `test_skill_display.py`（34）、
  `test_use_danwen.py`（85，自跑 runner **才是正确跑法**，`-p _pytest_stub_plugin` 下变异抓不到）、
  `test_commands.py`（125）、`tests/test_doupo_tower.py`（21）、`tests/test_doupo_buttons.py`（21）。
- **变异反证只用 /tmp 备份还原，绝对禁止 `git checkout --`**（会把整文件未提交改动全撤销）。
