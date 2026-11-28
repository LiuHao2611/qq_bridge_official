# qq_bridge_official 长期红线
> 只留「踩过坑别改回去」+ 守门测试名。细节读 `docs/生产环境说明_手机服务器_2026-11-21.md`、`AGENTS.md`、当日日志、游戏侧 `MEMORY_REDLINES.md`。

## A. 手机服务器（小米6/sagit，唯一生产环境）
- 红线原话：「就在这个手机上部署纯linux系统…别再让我选其他的东西…别再让我装这个装那个…这次就直接给我一次弄好」⇒ 只此机、真·纯 Linux 直跑（非 Android/Termux）；不给换机/LineageOS 选择题、不让装 apk/买硬件；障碍自查，仅物理不可逆操作时找用户。
- 分区：boot=sde40（系统内可整段 dd 写 15724544B 并回读）、system=sde43(5G)、userdata=sda17(120G **绝不动**)。WiFi `FourSeasons_Plus_XA` 仅 5GHz ⇒ `802-11-wireless.band` 必须留空(auto)，钉 bg 永连不上。
- 产物 `D:/AI/tools/pmdocker/output/`：`boot.img e2b6eb87`、`boot-rndis.img c4f6ae59`（**唯一起 usb0=172.16.42.1/16，SSH 救急入口**）、`pmos-xiaomi-sagit.zip d7caba3c`。pmOS v26.06=Alpine+OpenRC+6.0.0。
- 网络：WiFi 静态 `192.168.0.50/24`(gw .1,DNS 223.5.5.5)+`cloned-mac-address=02:53:41:47:49:54`（不固定则每次开机随机 MAC 被路由当新设备）；USB RNDIS `172.16.42.1`(电脑侧 .2/24)。sudo=123456。无 RTC 开机回 1970，`/etc/local.d/50-wifi.start` 拉网+校时（不校时 TLS 全红）。
- ✅ A-MSDU 停摆根治：ath10k 分片补丁编进 `ath10k_core.ko.xz`(md5 913e7eb3c007b9fd613a2dfa75f3eba0)开机自动加载；断连解药=`nmcli connection up`(8~12s)，**不是** rebind/重启/reapply。日常不插 USB。
- 🚨 vdev hang（长跑约7.5h）：**绝不能先 rmmod/modprobe「温和重置」**——触发 `msa info req rejected: 90`→wlan0 整段消失；一旦 msa rejected 唯一解=USB SSH→`sudo reboot`。**任何情况下别把 rebind 写回看门狗/开机脚本**（v1 看门狗是元凶）。
- 现行看门狗 `/etc/init.d/wifi-watchdogd`(OpenRC default,10s/轮,开机 sleep90s)：网关 2 轮不通→`nmcli connection up`；仅 wlan0 消失才 reboot(≥8轮+无usb0+30min限一次)。旧 crontab 已删。
- 🚨 重启后 `online:false`/`bots:[]` 且仅一次 `Failed to get gateway info`=适配器一命不续；真因「DNS 热≠射频醒」。**别反复 restart 赌运气**——先 `nc -z -w5 api.sgroup.qq.com 443`，通了再 restart 一次。`qq-bridge.init` 三守卫（路由/三子域逐个可解析/真实 TCP443），脚本入库 `deploy/phone/qq-bridge.init`（改它要同步）。gateway 走 `api.sgroup.qq.com`，只验 `bots.qq.com` 兜不住。
- 🚨「纯文字+按钮回复慢」根因=DNS 冷解析，**不是 httpx timeout**（11-27 实测定案）：`getaddrinfo` 冷 2.5~5s/命中 0。已修 `channel/dns_keepwarm.py`(每45s解析两域名,开关 `OFFICIAL_DNS_KEEPWARM`)+`qq_interaction.py` ACK 后台 `_schedule_ack`（不再串行 await）。守门 `tools/check_official_slow_reply_fix_20261127.py`(17/17)。发送侧重试策略别动（`_is_connect_phase_error` 已覆盖 gaierror/ConnectError，改次数有重复投递风险）。
- 装包铁律：`apk` 不可用→全走 `~/venv`；轮子必须 **musllinux**(`--platform musllinux_1_2_aarch64 --abi cp314 --abi abi3 --abi none`，abi3 必带)→scp→`pip install --no-index --find-links`。
- 手机无 `rsync/curl/sqlite3/ss`；`ps` 不支持 `-p/lstart`(用 `ps -o pid,etime,args`)；`rc-status` 别加 sudo。日志面板 `http://192.168.0.50:8091`(自研 logview)。
- 硬件：长期满电鼓包→限充 80-85%/拆电池；摘壳散热；数据外部备份+外部存活监控。

## B. 手机端生产部署
- 代码 `/home/pmos/qq_bridge`，venv `/home/pmos/venv`。开机自启 `networkmanager sshd nftables syslog crond logview qq-bridge qq-doupo-agent`。
- qq-bridge 8081(Bot 1906371548)，`command_background=yes`+pidfile 必填；qq-doupo-agent 8765 本机，`DOUPO_AGENT_URL=http://127.0.0.1:8765`。
- 🚨 裸机必写 `.env`: `DOUPO_DATA_PATH=.../data/doupo/doupo.json`、`XIUXIAN_DATA_PATH`、`QQ_BRIDGE_CACHE_DIR`（曾致老玩家被要求重新入道）。库路径由 `Path(DATA_PATH).with_suffix('.sqlite')` 推导，**别另开 DB_PATH**。生产 `select count(*) from players`≥4 行。
- 🚨 部署后必跑 `python tools/phone_deploy.py drift`（verify 只看 `.deployed` 清单会漏推，11-23 周本事故）。drift=本地 `src/**/*.py` md5 vs 手机全量；`tools/` 不上手机属正常差异。
- 🚨 部署撞车：`push` 自带 restart 会撞常驻 `qq-service-watchdogd` 全停全启→crashed+15min 冷却不拉起。用 `push ... --no-restart` 落位再手动 `sudo sh /usr/local/bin/qq-service-restart-all`；已 crashed 直接全停全启。手机有 test_*.py，改测试也要 push 否则 drift 不过。

## 索引（已完成功能 + 守门；细节读当日日志）
- **物品图**：素材 `assets/doupo_item_icons/`(109 张=丹药83+异火26,文件名=游戏真名)、访问层 `item_icons.py`。🚨 放大 NEAREST/缩小必须 LANCZOS（异火细颗粒图 NEAREST 缩小断成碎点）。详情卡副标题下居中 88×88 头像(深底 plaq_bot,**绝不贴牌匾角**——回纹占(19,19)~(79,79))。背包 `GRID_CELL_H=92/ICON_BOX=40/ICON_CY=40/NAME_CY=76`，图标落角标下缘之下。部署 `phone_deploy.py push-assets <dir>`。守门 `tests/test_doupo_item_icons.py`+`test_bagview` 8/8。
- **丹雷**：`doupo/danlei.py`(七品起,`DANLEI_MIN_IDX=6`,`DAMAGE_RATE=(.08,.15,.28,.40)`),出战傀儡 50% 全额免伤+`puppet_core.temper()` 落库,`BATCH_CAP=3`。🚨 apply_hp 地板=`max(min(before,floor),before-hurt)`（只拦扣过头、绝不反向给残血加血）。炼药 4 函数已迁 `commands_craft.py`(re-export 不变,commands 破 200KB)。守门 `tools/check_danlei.py --check/--selftest`+`test_danlei.py` 9。
- **境界描述**：`realm_lore.py`(13阶 `REALM_DESC`/`desc_of`)只在人物卡；`S.wrap_cjk` 折行（`_fit` 截断是回归），空串不撑高。守门 `tools/check_realm_lore.py --check`+`test_pic_templates`。
- **斗技名录卡**：名称 `S.wrap_cjk` 不省略；完整阶位分组 `card.py:_rank_groups()`（g["label"]标题/family 取色）；像素徽章 `assets/doupo_rank_icons/`(`_draw_rank_icon()` 用 `c.paste_alpha`，Canvas 无 `.image`)。🚨 字体红线（用户看截图纠正）：「技能的字体不要变，改回去」⇒ 文字一律走 theme 原字体，模板 build **不得**设 `PIXEL_FAMILY`/`font_family=PIXEL_FAMILY`/`set_measure_family(PIXEL_FAMILY)`/`_dc_replace`；像素化只留图标。`DoupoPixel` ttf+生成器+`fonts.py:_BUNDLED_PIXEL`+canvas 度量 ContextVar 闲置保留别再接。守门 `tools/check_doupo_card_visual_20261127.py`(8/8)。
- **玄重尺 999**：`config.WEAPON` 三值别混——`awaken_internal=9`(觉醒点,L=9 仍原著 2000/2000/1000/1200)、`max_internal=998`(上限,界面999)、`awaken_grow=.04`。🚨 觉醒后每级必须真涨属性：`weapon_curve.py` `gain=max_stats×.04×√(L-9)+(L-9)`，**`+lv` 项必须有**（单√在 L=909 整数坍缩）。`cfg.weapon_stat` 只管 L=0..8,L>=9 返 0 是哨兵别当属性。`enhance` 挡回 `w>=max_internal`，`evolved` 只在 w+1==9 报一次。守门 `test_core` 35/35 含 `test_enhance_awakened_grows_every_level`(全扫9→998每级>0,非抽查)。
- **出售/经济**：`econ.py` 唯一真源——买价×0.5(`RECYCLE_RATE`)/材料成本×2.0(`CRAFT_PROFIT_MULT`)/丹纹×(1/1.15/1.3/1.5)。🚨 绝不拿 `RECIPES[*]["price"]`(市场参考价,25枚成本>标价)当卖价；买价唯一入口 `econ.buy_price_of`(HERB→SHOP_EXTRA→RECIPE.buy→MATERIAL.price)。`core.sell_item` 拒 `n<=0`；带纹丹名解析 `item_text.py::resolve_sell_target`(精确→基础名歧义拒绝→非带纹包含,顺序不可换)。守门 `test_econ.py` 24/24。
- 🚨 别在手机跑 `test_commands`/`test_roster_card`（会建 `puppet_data.sqlite` 致 push 预检 FAIL）；误建库**改名不删**；要跑先 `export DOUPO_PUPPET_DATA_PATH=.../data/doupo/puppet_data.sqlite`。
- 菜单条数真值 **58**：`test_commands` 与 `test_duel` 两份断言必须同改。
- `config.py` 贴 200KB 红线：pre-commit 按工作区 CRLF 字节算(3191 行)，新数值拆独立模块+re-export（如 `weapon_curve.py`）。
- **斗技图鉴三文件**：`skill_book_schema.py`/`skill_book_data.py`(180KB)/`skill_book.py`。下一批扩斗技前先按宗门段拆 `skill_book_data_*.py`；改斗技只改 data，`BRIEFS` 漏登记→RuntimeError。
- 🚨 **补斗技三处缺一不可**（11-28 陨杀漏挂教训）：`BOOK_ROWS` 定义一次 + `FACTION_SKILLS[宗门]` 挂表 + `BRIEFS` 短释。`cmd_faction_skills`→`core.skill_pool`→`skills.catalog` 只读 `FACTION_SKILLS`，**只在 novel 补归属文字不挂表 = 宗门斗技里看不到**。同名技可跨宗共享归属（BOOK_ROWS 不重复即可，FACTION_SKILLS 多宗收录合法）。守门 `tools/check_20261128_four_fixes.py` C7 钉死。
- **火焰里程碑/查看**（11-28）：`flame_milestone.py` 唯一真源——`milestone_level`=所有持有火等级之和(+帝炎1)，`bonus_pct` 取已达成最高档**不累加**（4/6/10/16/25/40/60/85→+4/7/12/20/30/45/65/85%）；`core.stats` 末尾统一乘。重复授予唯一入口 `core.grant_flame`（新=1级/重复+1/满级折药渣30），满级火由 `flame_drop._exclude_maxed()` 剔出掉落池。`flame_view.py` 覆盖**33朵**逐朵主动/被动/获取途径，剥掉「(core.stats 已实现)」内部注记；`cmd_view` 落空查火名。🚨 `core.py 204459`/`config.py 204730` 贴 204800 红线，新数值**一律拆独立模块**。守门 `tools/check_flame_milestone_view_20261128.py`(26项)+手机 `test_doupo_flame_grant`26/`test_flame_tiers`40。
- 游戏侧模块红线见 `MEMORY_REDLINES.md`；运维六类故障剧本/危险操作黑名单见 `docs/生产环境说明_手机服务器_2026-11-21.md`(13章唯一权威)。当日细节见 `YYYY-MM-DD.md`。
