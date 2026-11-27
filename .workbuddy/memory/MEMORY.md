# qq_bridge_official 长期红线
> 只留「踩过坑别改回去」+ 守门测试名。细节读 `docs/生产环境说明_手机服务器_2026-11-21.md`、`AGENTS.md`、当日日志、游戏侧 `MEMORY_REDLINES.md`。

## A. 手机服务器（小米6/sagit，唯一生产环境）
- **红线原话**：「就在这个手机上部署纯linux系统…别再让我选其他的东西…别再让我装这个装那个…这次就直接给我一次弄好」⇒ 只此机、真·纯 Linux 直跑硬件（非 Android/Termux）；不给换机/LineageOS 选择题、不让用户装 apk/买硬件；障碍自查，仅物理不可逆操作时找用户。
- **硬件/分区**：sagit/MSM8998/arm64，已解锁非 A/B。boot=sde40（运行态可整段 dd 写 15724544 B 并回读校验；`reboot bootloader` 进不了 fastboot，改 boot 就在系统内 dd）、system=sde43(5G)、userdata=sda17(120G **绝不动**)。WiFi `FourSeasons_Plus_XA` 只有 5GHz ⇒ `802-11-wireless.band` 必须留空(auto)，钉 `bg` 永久连不上。
- **产物** `D:/AI/tools/pmdocker/output/`：`boot.img e2b6eb87`、`boot-rndis.img c4f6ae59`（**唯一起 usb0=172.16.42.1/16，SSH 救急入口**）、`pmos-xiaomi-sagit.zip d7caba3c`。pmOS v26.06=Alpine+OpenRC+6.0.0。
- **网络身份**：WiFi 静态 `192.168.0.50/24`（gw .1，DNS 223.5.5.5），`cloned-mac-address=02:53:41:47:49:54`（不固定则每次开机随机 MAC，路由器当新设备）。USB RNDIS `172.16.42.1`（电脑侧 .2/24）。sudo 密码 `123456`。无 RTC，开机回 1970，`/etc/local.d/50-wifi.start` 拉网+校时（不 ntpd 则 TLS 全红）。
- **✅ A-MSDU 停摆已根治**：ath10k 分片 bug，补丁编进 `ath10k_core.ko.xz` 模块（不在 vmlinuz，无需刷 boot），正式位 md5 `913e7eb3c007b9fd613a2dfa75f3eba0`；开机自动加载。断连解药=`nmcli connection up`（8~12s），**不是** rebind/重启/reapply。日常不插 USB。
- **🚨 固件 vdev hang（长跑约7.5h，与 A-MSDU 无关）**：wlan0 DOWN + `failed to create WMI vdev 0: -108` 且 amsdu/recovered/crashed=0。处置红线：**绝不能先 rmmod/modprobe「温和重置」**——会触发 `msa info req rejected: 90` → wlan0 设备整个消失；一旦 msa rejected，唯一解=USB SSH → `sudo reboot`。
- **🚨 v1 看门狗（crond + unbind/bind ath10k_snoc + nmcli down/up）是 wlan0 消失的元凶**：QMI 拒绝 MSA → probe 永败 → 自杀循环。**任何情况下不要把 rebind 写回看门狗/开机脚本**。
- **现行看门狗** `/etc/init.d/wifi-watchdogd`（OpenRC default，10s 一轮，开机 sleep 90s）：网关 2 轮不通就 `nmcli connection up`；仅 wlan0 消失才 reboot（≥8 轮+无 usb0+30min 限一次）。旧 crontab `wifi-watchdog` 已删。
- **🚨 重启后 `online:false` / `bots:[]` 且日志只有一次 `Failed to get gateway info` = 适配器一命不续**（nonebot QQ 适配器拉 gateway 只试一次；进程活着、事件循环正常，看门狗只看 wlan0/网关，不会动它）。真因是 **「DNS 热」≠「射频醒」**：dnsmasq 命中缓存时没出网卡，WiFi 休眠下第一发真实报文会丢。**别反复 restart 赌运气**——先 `nc -z -w 5 api.sgroup.qq.com 443`，通了再 restart 一次即好。`/etc/init.d/qq-bridge` 已加三道守卫（路由 / `bots.qq.com api.sgroup.qq.com api.qq.com` 逐个可解析 / 真实 TCP 443 握手），脚本已入库 `deploy/phone/qq-bridge.init`（旧版 `qq-bridge.bak-20261127`）——**改它要同步改仓库那份**，别再让生产脚本只活在手机上。注意：gateway 走 `api.sgroup.qq.com`，只验 `bots.qq.com` 兜不住（两个子域缓存互不相干）。
- **装包铁律**：`apk` 不可用 → 全走 `~/venv`。轮子必须 **musllinux**（musl，manylinux 被拒）；docker `pip download --platform musllinux_1_2_aarch64 ... --abi cp314 --abi abi3 --abi none` → scp → `pip install --no-index --find-links`（**abi3 必带**）。apk 有 DNS bug → `wget` 下 .apk + `apk add --allow-untrusted`。
- **手机无** `rsync`/`curl`/`sqlite3`/`ss`；`ps` 不支持 `-p`/`lstart`（用 `ps -o pid,etime,args`）；`rc-status` **别加 sudo**。日志面板 `http://192.168.0.50:8091`（自研 logview，Logdy 已退役，混时区逐模块 `tz_offset:auto` 学习）。
- **硬件红线**：长期满电鼓包→限充 80-85%/拆电池；摘壳散热；数据外部备份+外部存活监控。

## B. 手机端生产部署
- 手机=唯一生产环境，Docker 全停。代码 `/home/pmos/qq_bridge`，venv `/home/pmos/venv`。开机自启：`networkmanager sshd nftables syslog crond logview qq-bridge qq-doupo-agent`。
- **qq-bridge** `/etc/init.d/qq-bridge`：8081 官方通道（Bot 1906371548）。`command_background=yes`+`pidfile` 必填。**qq-doupo-agent**：8765 本机，`DOUPO_AGENT_URL=http://127.0.0.1:8765`。
- **🚨 裸机必写 `.env` 的 `DOUPO_DATA_PATH`/`XIUXIAN_DATA_PATH`（曾致老玩家被要求重新入道）**：`DOUPO_DATA_PATH=/home/pmos/qq_bridge/data/doupo/doupo.json`、`XIUXIAN_DATA_PATH=.../xiuxian/xiuxian.json`、`QQ_BRIDGE_CACHE_DIR=.../data/wm_cache`。库路径由 `Path(DATA_PATH).with_suffix('.sqlite')` 推导，**不要新开 DB_PATH**。生产应 `select count(*) from players`≥4 行（11-26 实测 5 行）。
- **🚨 部署后必跑 `python tools/phone_deploy.py drift`**：`verify` 只看 `.deployed` 清单，漏推文件不进清单⇒PASS 线上却坏（11-23 周本 `snapshot_lv` TypeError 事故）。drift=本地 `src/**/*.py` md5 vs 手机全量，正常 304 个 .py 两侧相等。`tools/` 不上手机属正常差异。

## 索引
- **斗技图鉴已拆三文件（2026-11-27，原单文件 196KB 顶到 200KB 红线）**：`skill_book_schema.py`（阶位常量/`tier_of`/`_s`）+ `skill_book_data.py`（`BOOK_ROWS` 逐条数据，已 180KB）+ `skill_book.py`（索引/`FACTION_SKILLS`/`BRIEFS`，对外入口，原样再导出全部符号）。**下一批扩斗技前必须先把 `skill_book_data.py` 按宗门段拆成 `skill_book_data_*.py`**。改斗技只改 data 文件，`BRIEFS` 漏登记会让模块导入直接 RuntimeError。
- 游戏侧红线（数据入库存真列/战斗回看/异火/傀儡三件套/沉浸文案/命中闪避面板/紫卡/沙盒/赠送/成卡回执/养魂）见同目录 `MEMORY_REDLINES.md`，动这些模块前必读。**§25 = 力竭必真扣血 / 塔两层两套层号 / 出卡三段计时 / 按钮三类型**。
- 全部运维细节/六类故障剧本/危险操作黑名单/验收数据见 `docs/生产环境说明_手机服务器_2026-11-21.md`（13 章，唯一权威）。
- 当日细节见同目录 `YYYY-MM-DD.md` 日志。
