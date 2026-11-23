# doupo_fonts —— 斗破卡片自带字体

生产机（postmarketOS / Alpine）**没有任何中文字体**，斗破 12 张卡的自绘文字
必须自带字形，否则牌匾标题、段名全是豆腐框。

| 文件 | 用途 | 进 git？ |
| --- | --- | --- |
| `doupo_bold.ttf` | 正文加粗（等线 Bold 子集，4284 字） | ✅ 早就进仓库（历史豁免） |
| `doupo_kai.ttf` | 牌匾 / 段名 / 分类标题（simkai 子集，7445 字） | ❌ 2.44MB，超 200KB 单文件红线 |

## 生成 `doupo_kai.ttf`

```bash
# Windows（读 C:/Windows/Fonts/simkai.ttf）
python tools/make_doupo_kai.py

# 其他系统指定字体路径
SIMKAI=/usr/share/fonts/xxx/simkai.ttf python tools/make_doupo_kai.py
```

脚本会自动做「零缺字」校验（`.notdef` 参照法），末尾打印 `OK` / `MISSING n`。

## 部署到手机

```bash
scp doupo_kai.ttf pmos@192.168.0.50:~/qq_bridge/src/plugins/qq_bridge/pic/templates/assets/doupo_fonts/
ssh pmos@192.168.0.50 'rc-service qq-bridge restart'
```

## 缺文件会怎样

**不会崩**，只是没有楷体味：

- `fonts.font_path("kai")` 回退到等线 Bold（`doupo_bold.ttf`）；
- 启动日志打一条 `[pic.fonts] 缺少 doupo_kai.ttf…` 告警（每个进程只打一次）；
- `tmp/render_all_doupo.py` 仍能出图，但牌匾字明显变细。

## 为什么不把 2.44MB 塞进 git

2026-11-16 用户定的上传红线：**任何单文件 >200KB 禁止上传**（`.githooks/pre-commit`
拦截）。拆成多卷只是绕开单文件检查、总体积一分没少，违背红线初衷，所以不拆。
中文字体本身无法压到 200KB（每字 ~330B × 7445 字），只能走「生成 + 单机各放一份」。
