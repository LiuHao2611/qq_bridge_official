# 斗破 AI 源码讲解模块 —— 权威项目调研与重构方案

> 调研时间：2026-11-17
> 触发问题：群友问「宗门权重是怎么算的」，AI 答不出；再问细一点就完全不知道。
> 用户要求：**不许再「想一个模块改一次」**，去 GitHub 找 2026 年权威、有完整源码的项目，
> 多拉几个，搬运成熟方案，我方只做代码兼容，**先调研清楚再动手**。

---

## 0. 一句话结论

主流 AI 编码工具（aider / repomix / 多个 code-index MCP 服务）在**同一套三段式架构**上收敛：

```
① 概览（outline）  只给签名，便宜，回答「这模块有什么」
② 取体（body）     按符号名取完整实现，回答「这个函数到底怎么写的」
③ 邻域（neighbors） callers / callees / imports，回答「谁用了它、它用了谁」
```

我方现版本**只有 ①（而且退化成一行摘要）**，②③ 完全没有。这是"问细致就答不出"的
架构级原因，不是检索排序问题，加别名表永远治不好。

---

## 1. 现状缺陷（机器实测，非推测）

实测命令（直接加载模块，绕开包 `__init__`）：

```
read_module('factions', '宗门的权重是怎么算的')
```

输出 `4549` 字符，段落 `【斗破模块】/【设计说明】/【关键定义】/【代码节选】`，其中：

| 关键词 | 出现次数 | 含义 |
|---|---|---|
| `random_faction` | 1 | **只有签名那一行** |
| `weight` | 3 | 全在 docstring 文字里 |
| `pool_map` | **0** | 权重算法的核心变量，根本没进上下文 |
| `acc +=` | **0** | 累积抽样的实际逻辑，根本没进上下文 |

而 `random_faction` 的真实实现只有 **18 行**（factions.py 1340–1357）：

```python
1340| def random_faction(pool: str = "starter", lv: int | None = None, rng=None) -> str | None:
1341|     """按 weight 抽一个宗门 id；rng 可注入（测试用 lambda: 0.0 / 1.0）。"""
1342|     r = rng or random.random
1343|     pool_map = by_pool(pool, lv if lv is not None else POOL_UNLOCK.get(pool, 1))
1344|     ...
1347|     weights = [max(0, int(pool_map[i]["weight"])) for i in ids]
1348|     total = sum(weights)
1351|     pick = r() * total
1352|     acc = 0
1353|     for fid, w in zip(ids, weights):
1354|         acc += w
1355|         if pick < acc:
1356|             return fid
```

**18 行、约 700 字符就能完整回答这个问题，但当前实现一行都没给。**
更糟的是「代码节选」段从第 3 行开始 —— 因为中文话题词命中的是**模块 docstring 的说明文字**，
节选把文档散文当成了代码。两个独立缺陷叠加：

1. `_defs()` 只输出 `签名 // 一行摘要`，函数体永不进入上下文；
2. `_excerpt()` 是**行级**匹配（命中行 ±3/+4），命中文档散文就只给散文，且无法围绕符号边界裁剪。

对比参考：整个 doupo 目录 52 个 py（其中 19 个 test_ 已排除）、22388 行、**877 个 def + 18 个 class**。
单模块最大 `core.py` 2138 行。符号总量不到 900，**全量符号索引在内存里可以忽略不计**。

---

## 2. 调研对象与可搬运资产

### 2.1 aider / grep-ast —— `TreeContext`（★ 主力搬运对象）

- 仓库：`Aider-AI/grep-ast`（aider 的代码理解底座，291★，Apache-2.0）
- 源码：`grep_ast/grep_ast.py`，单文件即可用
- 解决的问题：**给几行"感兴趣的行"，返回包含它们的完整语法作用域**，其余折叠成 `⋮`

核心数据结构（逐个行号建表，`O(行数)`）：

```python
self.scopes  = [set() for _ in range(self.num_lines)]  # 每行属于哪些作用域（以起始行号为 id）
self.header  = [list() for _ in range(self.num_lines)] # 作用域的"短签名"行区间
self.nodes   = [list() for _ in range(self.num_lines)] # 起始于此行的语法节点
```

`walk_tree()` 对每个节点：`scopes[start..end] += start`；`header[start].append((size, start, end))`。
之后 `header[i]` 取 **size 最小的那个**（就是最内层作用域的签名），并受 `header_max` 截断 ——
这正是"多行 Python 签名"能被完整带出来的原因。

上下文扩展三部曲：

```python
def add_parent_scopes(self, i):        # 层层向外：给父作用域签名 + 递归到父作用域末行
    for line_num in self.scopes[i]:
        head_start, head_end = self.header[line_num]
        self.show_lines.update(range(head_start, head_end))
        if self.last_line:
            self.add_parent_scopes(self.get_last_line_of_scope(line_num))

def add_child_context(self, i):        # 作用域太大时，只挑最大的几个子节点露头
    size = self.get_last_line_of_scope(i) - i
    if size < 5:  self.show_lines.update(range(i, last_line+1)); return
    max_to_show = max(min(size * 0.10, 25), 5)   # 10% 且夹在 [5, 25]

def close_small_gaps(self):            # 只差 1 行的空洞填上，避免满屏 ⋮
```

渲染（折叠连续空档为单个 `⋮`）：

```python
dots = not (0 in self.show_lines)
for i, line in enumerate(self.lines):
    if i not in self.show_lines:
        if dots:  output += "...⋮...\n" if self.line_number else "⋮\n"; dots = False
        continue
    spacer = "█" if (i in self.lines_of_interest and self.mark_lois) else "│"
    output += f"{spacer}{line}\n"; dots = True
```

**可搬运度：算法 100% 可搬，解析后端需替换**（见 §4）。

### 2.2 repomix `--compress`（★ 概览层搬运对象）

- 仓库：`yamadashy/repomix`，MIT
- 文档：`repomix.com/guide/code-compress`
- 做法：tree-sitter 解析后**保留签名、删掉函数体**，函数体替换为 `⋮----`

```typescript
// 压缩前
const calculateTotal = (items) => { let total = 0; for (...) {...} return total; }
// 压缩后
const calculateTotal = (
  items: ShoppingItem[]
) => {
  ⋮----
}
```

关键价值：**保留的是真实源码的多行签名，不是重新拼的字符串** —— 参数默认值、类型标注、
返回类型全都原样。这一条我方现版本完全没有（`_defs()` 用 `line.strip().rstrip(":")`，
遇到多行签名直接断在半截）。

另一个可借的设计：**单文件压缩失败静默回退原文**（`Failed to compress ${filePath}, using uncompressed content`），
不中断整体。对应我方：语法错误文件必须回退到正则/行级模式，不能让整个工具报错。

### 2.3 code-index MCP 系列（★ 工具面搬运对象，三家口径一致）

| 仓库 | 语言 | 关键工具面 |
|---|---|---|
| `johnhuang316/code-index-mcp` | Python，MIT | `file_summary` / `get_symbol_body`（**深索引后按名取体**） |
| `squirrelogic/code-index` | TS，MIT | `find_def` / `find_refs` / `callers` / `callees` / `symbols`，O(1) 哈希精确名 = 快路径 |
| `achreftlili/code-index` (mcp-code-index) | Python，MIT | `file_outline` / `symbol_lookup` / **`where_am_i(path,line)`→最内层符号+完整外层链** / `get_symbol_body` / `get_symbol_bodies`(批量 20) |

三家独立实现，工具面几乎一模一样 —— 说明这是**行业收敛的接口语义**，不是我方臆想：

- 精确名走哈希快路径（O(1)），模糊/子串才走倒排或 k-gram；
- `get_symbol_body` 一律**返回完整源码块**，不是摘要；
- `where_am_i` = 给 `path:line` 反查"我这一行属于哪个函数、外层是谁" —— 这正是 `add_parent_scopes` 的查询化封装；
- 结果一律**有界**（`bounded JSON`），不无限吐。

### 2.4 aider `RepoMap` —— 排序权重（部分搬运）

- 仓库：`Aider-AI/aider`，Apache-2.0，`aider/repomap.py`
- 算法：tree-sitter 抽 def/ref → 文件引用图 → **personalized PageRank**（对话中文件为种子）→
  在 token 预算内二分搜索，渲染**真实签名行**

权重公式（可独立搬运，与 PageRank 解耦）：

```python
mul = 1.0
if ident in mentioned_idents:                       mul *= 10   # 用户刚提到的标识符
if (is_snake or is_kebab or is_camel) and len(ident) >= 8:  mul *= 10   # 长标识符更有信息量
if ident.startswith("_"):                           mul *= 0.1  # 私有符号
if len(defines[ident]) > 5:                         mul *= 0.1  # 到处都定义的名字没区分度
use_mul *= 50                                       # 引用方在对话上下文中
```

**不搬的部分**：PageRank 与 networkx 依赖。理由见 §4.2 —— 44 个文件、900 符号的规模，
PageRank 的收益完全被"引入 networkx + 图构建成本"吃掉。

---

## 3. 三方方案横向对比

| 维度 | grep-ast TreeContext | repomix compress | code-index MCP | aider RepoMap |
|---|---|---|---|---|
| 定位 | 按行取作用域 | 全库瘦身 | 符号索引服务 | 全库地图 |
| 输出 | 真实代码行 + `⋮` | 签名 + `⋮----` | 符号体 / 调用图 | 真实签名行 + `⋮` |
| 解析 | tree-sitter | tree-sitter | tree-sitter | tree-sitter + pygments 回填 |
| 依赖 | tree-sitter-language-pack | tree-sitter WASM | tree-sitter / SQLite+向量 | tree-sitter + networkx |
| 许可证 | Apache-2.0 | MIT | MIT | Apache-2.0 |
| 对我方价值 | ★★★ 治"没函数体" | ★★★ 治"多行签名丢失" | ★★★ 治"工具面缺 symbol 参数" | ★★ 权重公式可用，图不用 |

---

## 4. 选型决策

### 4.1 为什么不引入 tree-sitter（硬约束，不是偏好）

实测本地与容器环境：

```
容器 Python 3.13.18
tree_sitter   MISSING
networkx      MISSING
pygments      MISSING
pathspec      MISSING
```

而 `docker/Dockerfile.bridge` 明确是**离线构建**：

```dockerfile
# ★ 离线 wheel（重要）★
#   本机 Docker 容器出网受限（apt 的 deb.debian.org 与 pip 均会卡死）
#   tools/build_docker_wheels.py 在宿主预拉 manylinux wheel 到 docker/wheels/
RUN pip install --no-cache-dir --no-index --find-links /wheels -r requirements.txt
```

`--no-index` 意味着**任何新依赖都必须先手工预拉 manylinux wheel 进 `docker/wheels/`**。
`tree-sitter-language-pack` 是把几十套 grammar 打进一个包（体量级 tens of MB），
为一个只需要 Python 的只读讲解工具付这个代价不划算，且每次升级都要重新预拉。

### 4.2 解析后端替换：tree-sitter → 标准库 `ast`

tree-sitter 对我方的**唯一**价值是多语言。而白名单里 44 个文件全是 Python + 5 个 md
（md 走标题行，不需要 AST）。Python 3.13 标准库 `ast` 提供 tree-sitter 同构能力：

| TreeContext 需要 | tree-sitter | 标准库 `ast` |
|---|---|---|
| 节点起止行 | `node.start_point[0]` / `end_point[0]` | `node.lineno` / `node.end_lineno` |
| 节点类型 | `node.type` 字符串 | `isinstance(n, ast.FunctionDef)` |
| 子树遍历 | `node.children` | `ast.walk()` / `node.body` |
| 多行签名原文 | `node.text` 截取 | `ast.unparse(node)` 或按 `body[0].lineno-1` 切行号 |
| docstring | 解析节点 | `ast.get_docstring(node)` |
| 调用关系 | 自定义 query | `ast.Call` + `ast.Name.id` |

**结论：算法照搬 grep-ast，解析层换成 `ast`，零新增依赖、零 Dockerfile 改动、零 wheel 预拉。**
这就是用户要求的"只做代码兼容"。

### 4.3 一个 bonus：`ast` 比 tree-sitter 更准的地方

tree-sitter 的 Python grammar 对 `match` 语句、PEP 695 泛型、`except*` 的支持滞后；
`ast` 是解释器本体，永远与运行环境一致（3.13 全覆盖）。对本仓库（有 `match`、
有 `str | None` 新式标注）反而更稳。

---

## 5. 搬运映射表（改什么 / 不改什么）

| # | 来源 | 搬运内容 | 落到我方 | 需要改的兼容点 |
|---|---|---|---|---|
| P1 | grep-ast `TreeContext` | `scopes` / `header` / `nodes` 三张行表；`add_parent_scopes`、`add_child_context`、`close_small_gaps`；`format()` 的 `⋮` 折叠 | `_ScopeMap`（新类，单文件内） | `walk_tree` 的 tree-sitter 遍历 → `ast.walk`；`header` 截断用 `body[0].lineno-1` 求签名末行 |
| P2 | repomix `--compress` | 保留签名 / 折叠函数体；**失败静默回退原文** | `_outline()` | tree-sitter query → `ast` 遍历 + 按行号切原文（保多行签名原样） |
| P3 | code-index MCP ×3 | 工具面语义：`file_outline` / `get_symbol_body` / `where_am_i` / 精确名 O(1) 快路径 / 结果有界 | `explain(symbol=...)`、`read_module(symbol=...)` | 索引结构 `dict[name] -> list[_Sym]`；不引入 SQLite/向量（900 符号内存足够） |
| P4 | aider `RepoMap` | `mul` 权重公式（提及×10 / 长名×10 / `_`×0.1 / 多定义×0.1） | `_symbol_score()` | **丢掉 PageRank 与 networkx**；权重直接当符号排序分 |
| — | — | **不搬**：tree-sitter、networkx、pygments、pathspec、向量检索、SQLite 索引、git 热点分析 | — | 规模不匹配，全是净负担 |

---

## 6. 新接口设计（保持单工具，只加一个参数）

维持「一个工具 + 模型自选」的既定方向，不新增工具（工具数暴涨会挤占模型注意力）。
只给 `doupo_code_explain` 加第三个可选参数 `symbol`：

| 调用方式 | 返回 |
|---|---|
| `topic="宗门"` | 候选模块清单（现状不变） |
| `file="factions"` | **模块概览**：docstring + 全部符号的多行签名 + 行号 + 一行说明 + 嵌套缩进 |
| `file="factions", topic="权重怎么算"` | 概览 + **按符号打分取 top-N 的完整函数体**（含父作用域签名，非命中函数的区域 `⋮` 折叠） |
| `file="factions", symbol="random_faction"` | **该符号完整实现**（含父作用域链签名，无省略） |
| `symbol="random_faction"` | 全库符号定位：`name | kind | file:line-line | 一行说明`，可再传 file 精读 |

对应 code-index 三家的 `where_am_i`：`symbol` 支持 `"factions.py:1340"` 形式 → 返回该行所属
最内层符号 + 完整外层链。这样模型看到概览里的行号就能直接跳进去。

### 6.1 预算（必须显式，否则必然被 6000 字截断）

| 段 | 上限 | 说明 |
|---|---|---|
| 设计说明（docstring） | 900 字符 | 比现在的 1200 略降，把余量让给代码 |
| 模块概览（outline） | 1600 字符 | 超了截断并提示「还有 N 个符号，用 symbol 读」 |
| 代码段（函数体） | 3200 字符 | 按分数从高到低整段装，装不下的整个跳过（不切半截函数） |
| 合计目标 | ≤ 4800 字符 | `AI_TOOL_RESULT_MAX_CHARS=6000` 兜底，留 1200 余量 |

单符号模式（`symbol=`）例外：代码段上限提到 3600，因为只有一段。

---

## 7. 验收计划（含"细致问题"回归）

### 7.1 机器门禁（新增，进 `tests/`）

1. `test_detailed_question_gets_real_body` —— **核心回归**：
   `explain(file="factions", topic="宗门的权重是怎么算的")` 输出必须包含
   `acc +=`、`pool_map`、`pick = r() * total`。当前实现此条必红。
2. `test_symbol_mode_returns_full_body` —— `symbol="random_faction"` 输出行数 ≥ 16 且含
   `weights = [max(0, int(...` 与原函数首尾行。
3. `test_outline_keeps_multiline_signature` —— 多行签名的函数，概览里必须保留完整括号块。
4. `test_every_symbol_in_every_module_is_retrievable` —— **遍历式**：对白名单每个文件，
   用 `ast` 取出每个顶层符号名，逐个 `symbol=` 读取必须成功（回应用户"一百个模块"）。
5. `test_line_number_lookup` —— `symbol="factions.py:1340"` 必须命中 `random_faction`。
6. `test_broken_syntax_falls_back` —— 塞一个语法错误的 py 进 tmp 树，工具必须回退不抛错。
7. `test_output_bounded` —— 任何调用输出 ≤ 5600 字符。
8. 既有的 13 条（路径安全、白名单、两段式、无别名表、零写操作）全部保持绿。

### 7.2 反例门禁（`tmp/prove_code_explain.py` 扩）

- G：把 `_ScopeMap` 换成"只给签名" → 1 号用例必须红。
- H：`symbol=` 退化为打摘要 → 2 号用例必须红。
- I：去掉 `ast` 回退 → 6 号用例必须红。

### 7.3 真机端到端

在群里连问三级，逐级加深：
1. 「宗门怎么设计的」→ 应给模块概览
2. 「宗门权重怎么算的」→ 应给 `random_faction` 完整实现
3. 「`random_faction` 有哪些地方调用」→ 应走 `symbol` 全库定位（P3 邻域，本期先给"出现在哪些文件"）

---

## 8. 风险与回滚

| 风险 | 处置 |
|---|---|
| `ast.parse` 遇到语法错误文件 | `try/except SyntaxError` → 回退行级模式（repomix 同款静默回退），并仍能出 outline（正则签名） |
| 输出超限被 registry 截断 | §6.1 三段独立预算 + 装不下整个跳过，绝不切半截函数 |
| 大模块 outline 刷屏（`core.py` 2138 行） | outline 上限 1600 字符 + 「还有 N 个符号」提示 |
| 性能 | 索引按 mtime 缓存，44 文件全量 `ast.parse` 预计 < 200ms（实测后再定） |
| 回滚 | 纯新增：`_ScopeMap` / `_outline` / `_index` 都是新方法，`explain()` 签名向后兼容（`symbol` 默认 `""`）。git 回退单个 commit 即可 |

---

## 9. 施工顺序

1. `_index()`：`ast` 建符号表（name/kind/start/end/sig 范围/doc/parent/calls）
2. `_ScopeMap`：搬 P1 三张表 + `add_parent_scopes` / `add_child_context` / `close_small_gaps` / `format`
3. `_outline()`：搬 P2，多行签名原样 + 嵌套缩进
4. `_symbol_score()`：搬 P4 权重公式
5. `read_module()` 重写：概览 → 代码段（按符号取体）
6. `explain()` 加 `symbol` 参数 + 全库定位
7. `builtins.py` 工具描述加第三参数引导（措辞不出现「只读」）
8. 测试 8 条 + 反例 3 条
9. 重建部署 → 容器内端到端三级提问 → 补 memory

---

## 10. 落地后追加的两条（原方案没预见，真机暴露）

### 10.1 必须索引**模块级数据常量**（不是可选优化）

斗破是**数据密集**仓库，「设计」相当一部分写在常量里：

| 常量 | 位置 | 规模 | 为什么必须有 |
|---|---|---|---|
| `FACTIONS` | factions.py L64 | **1209 行** | 每个宗门的 `weight` 数值就在这里 |
| `POOL_UNLOCK` | factions.py L54 | 1 行 | `{"starter":1,"advanced":40,"hidden":90}` 池子门槛 |
| `SPECIAL_UNLOCK_STEPS` | factions.py L57 | 1 行 | `(20,40,60,80)` 解锁阶梯 |
| `WEAPONS` | weapon_data.py | 157 行 | 武器表 |
| `tower_data` | 32 个常量 | — | 塔层参数 |
| `config.py` | 90 个常量 | — | 全局数值 |

原方案只索引 `def/class`，真机第一次跑就露馅：模型问「各宗门权重是多少」，
`symbol=POOL_UNLOCK` 直接返回「没找到符号」。**只索引函数的工具，在数据驱动项目里
是残废的。**

落地做法：`_collect(allow_data=...)` —— 模块级与类体内把顶层赋值登记为
`kind="data"` 符号，**进到函数体就关掉**（否则满屏局部变量）。

数据常量不能像函数那样抽"骨架"（`FACTIONS` 抽子语句只剩首尾两行），所以
`_render_data()` 按**行开窗**：给开头 N 行真实条目（能看到 `'weight': 12` 这种实际数值），
末尾标「共 1209 行」。按行号跳读时改走 `window()`，**以目标行为中心**开窗 ——
否则 180/230/290/350 行都得到一模一样的首屏，模型会以为没变化而反复换行号重试。

### 10.2 必须有**机械刹车**，不能只靠提示词劝

真机第一次跑：模型在第 2 轮就已经拿到了 `acc += w` / `pool_map` / `POOL_UNLOCK`
（首轮 `file=factions` 返回 4232 字里全都有），却继续查了 **6 轮 16 次调用**，
把 `AI_FC_MAX_ROUNDS=8` 烧光，**最终一句回答都没给** —— 比改造前更糟。

在提示词里写「最多查 2 次」有用但不可靠（实测仍出现 1011 字长回答）。真正起作用的是
让工具自己喊停：`_doupo_code_explain` 按 `trace_id` 计数，第 3 次起在结果开头插入
「你本轮已经查了 N 次，材料够了立刻回答，不要再调用工具」。

改造后稳定为 **3 次调用 / 3~4 轮**，回答 1000 字左右且五项口径全对。

### 10.3 遗留：`AI_MAX_REPLY_LEN=800` 会截断讲解

实测回答 1011~1298 字，被 `ai_mode.py` 硬截到 800 + 「……（太长，本喵省略）」。
已在提示词里要求「600 字以内」但模型不一定遵守。彻底解决要把该值调到 1200 左右
（属全局配置，不擅改，留待主人定夺）。

---

## 11. 引用清单

- `Aider-AI/grep-ast` · `grep_ast/grep_ast.py` · Apache-2.0 · TreeContext 算法原文
- `Aider-AI/aider` · `aider/repomap.py` · Apache-2.0 · 权重公式与二分预算
- `yamadashy/repomix` · `--compress` · MIT · 签名保留 / 折叠 / 静默回退
- `johnhuang316/code-index-mcp` · MIT · `get_symbol_body` / 深索引
- `squirrelogic/code-index` · MIT · `find_def` / `callers` / `callees` / O(1) 精确名
- `achreftlili/code-index` · MIT · `where_am_i` / `file_outline` / 批量 `get_symbol_bodies`
