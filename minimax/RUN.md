# 运行说明

本文件说明如何在**干净环境**中从零跑通本作品。

- 一页技术说明与交付物索引：[README.md](README.md)
- 验证样例：[validation/README.md](validation/README.md)

---

## 1. 环境要求

| 项目 | 要求 | 说明 |
| --- | --- | --- |
| Python | ≥ 3.11（验证于 3.12.16） | Windows / Linux / macOS |
| 数据库 | seekdb 或 OceanBase（MySQL 协议） | 本地用 seekdb v1.4.0.0（`5.7.25-OceanBase seekdb-v1.4.0.0`），也可用 OceanBase CE |
| 模型 | MiniMax 开放平台 API Key | Anthropic 兼容端点 |
| PowerContext | `powercontext[builtin]==1.2.0` | 进程内 Core SDK，SQLite 文件后端 |

无需 `git`、`patch`、`uv`；补丁应用与测试执行都由 Python 完成。

---

## 2. 目录结构

```
minimax/
├── README.md                     一页技术说明 + 交付物索引
├── RUN.md                        运行说明（本文）
├── requirements.txt              运行依赖
├── .env.example                 配置示例（占位值，不含真实凭据）
├── pyproject.toml                可选：pip install -e . 提供 sdd-context 命令
├── docker-compose.oceanbase.yaml 可选：用 OceanBase CE 起一个数据库
├── sdd_context/                  上下文模块源码
│   ├── cli.py                    命令行入口
│   ├── config.py                 环境变量配置
│   ├── documents.py              规格 / 代码 / 测试 的解析与切分
│   ├── textnorm.py               检索文本归一化（CJK 与标识符）
│   ├── db.py                     seekdb/OceanBase 存储与原生全文检索
│   ├── ingest.py                 工程资料导入
│   ├── retrieve.py               检索与来源选择
│   ├── context.py                上下文组装与模型提示词
│   ├── state.py                  PowerContext 任务状态读写
│   ├── minimax_client.py         MiniMax API 客户端
│   ├── patcher.py                unified diff 提取与应用
│   └── agent.py                  闭环编排（上下文→改码→测试→状态）
├── demo_project/                 被修改的业务工程（可运行、有测试）
├── validation/                   验证样例（任务描述、期望来源、端到端脚本）
├── code/                         修改前/后代码快照与代码差异
├── records/                      技术使用记录（MiniMax / 数据库 / PowerContext / 日志）
└── tools/                        辅助脚本（快照生成）
```

---

## 3. 安装依赖

```bash
cd minimax
python -m venv .venv
# Linux / macOS
source .venv/bin/activate
# Windows PowerShell
# .\.venv\Scripts\Activate.ps1

python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

`requirements.txt` 固定了本次验证所用的版本：

```
pymysql>=1.2,<1.3
httpx>=0.28,<1
pyyaml>=6,<7
powercontext[builtin]==1.2.0
pytest>=8,<10
```

也可以把模块装成命令行工具（可选）：

```bash
python -m pip install -e .          # 提供 sdd-context 命令
```

下文统一用 `python -m sdd_context` 表述，两种方式等价。若不安装为包，需要把 `minimax/`
加入 `PYTHONPATH`：

```bash
export PYTHONPATH="$PWD:$PYTHONPATH"          # Linux / macOS
$env:PYTHONPATH = "$PWD;$env:PYTHONPATH"     # Windows PowerShell
```

---

## 4. 准备数据库

三种方式任选其一。

### 4.1 用 docker 起 OceanBase CE（推荐）

```bash
cd minimax
docker compose -f docker-compose.oceanbase.yaml up -d
# 首次启动需要 1~3 分钟，等待健康检查通过
docker compose -f docker-compose.oceanbase.yaml ps
```

连接信息：`127.0.0.1:2881`，用户 `root@test`，密码 `powercontext-demo`。

### 4.2 用已有的 seekdb / OceanBase 实例

设置 `SDD_DB_HOST` / `SDD_DB_PORT` / `SDD_DB_USER` / `SDD_DB_PASSWORD` 即可。

### 4.3 Linux 上的嵌入式 seekdb

PowerContext 官方自测使用 `pylibseekdb`（Linux / macOS）。本模块只使用 MySQL 协议，
因此嵌入式实例同样适用。

---

## 5. 配置

复制配置示例并按环境填写（**不要提交真实凭据**）：

```bash
cd minimax
cp .env.example .env      # Windows: Copy-Item .env.example .env
```

模块**只读环境变量**，不自动解析 `.env`，因此导出变量即可：

```bash
set -a; . ./.env; set +a          # Linux / macOS
```

```powershell
# Windows PowerShell
Get-Content .env | Where-Object { $_ -match '=' -and $_ -notmatch '^#' } | ForEach-Object {
  $name, $value = $_ -split '=', 2
  Set-Item -Path "env:$($name.Trim())" -Value $value.Trim()
}
```

必填项：

| 变量 | 说明 |
| --- | --- |
| `SDD_DB_HOST` / `SDD_DB_PORT` / `SDD_DB_USER` / `SDD_DB_PASSWORD` | 数据库连接 |
| `MINIMAX_API_KEY` | MiniMax 开放平台的 API Key（形如 `sk-...`） |
| `MINIMAX_BASE_URL` | Anthropic 兼容端点，默认 `https://api.minimaxi.com/anthropic` |
| `MINIMAX_MODEL` | 账号实际可用的模型，例如 `MiniMax-M3` |

其余变量见 `.env.example`。

> 模块同时发送 `x-api-key` 与 `Authorization: Bearer` 两种请求头，因此平台 Key 与
> Agent 网关 Token 都能工作；API Key 不会写入任何输出文件。

---

## 6. 检查环境

```bash
python -m sdd_context doctor
```

输出 JSON：`database.ok` 表示数据库可达，`powercontext.ok` 表示 PowerContext 可用，
`minimax.ok` 表示已配置模型 Key。退出码为 0 表示数据库与 PowerContext 均正常。

---

## 7. 初始化数据

```bash
python -m sdd_context init      # 建库、建表、创建原生全文索引
python -m sdd_context import    # 导入规格、代码与测试
python -m sdd_context stats     # 查看已入库内容
```

`init` 会在 `sdd_context` 库中建立：

| 对象 | 说明 |
| --- | --- |
| `sdd_document` | 每行一个可定位的来源（规格条目 / 代码符号 / 测试函数） |
| `sdd_meta` | 记录实际使用的全文解析器（`ik` → `beng` → `ngram` → 内置） |
| `sdd_import_log` | 每次导入的批次、时间、文档数量 |

`sdd_document` 上有原生全文索引 `ft_sdd_search`，检索时使用
`MATCH(search_text) AGAINST(...)`，匹配在数据库内完成。

`import` 是幂等的：重新导入会覆盖同一 `doc_id` 的内容，并删除同一文件中已经不存在的旧条目，
因此**规格更新后重新导入即可生效**，不会残留旧版本。

---

## 8. 调用入口

模块提供 CLI 入口（`python -m sdd_context` 或 `sdd-context`）。

| 命令 | 输入 | 输出 |
| --- | --- | --- |
| `doctor` | 环境变量 | JSON：数据库 / PowerContext / MiniMax 可用性 |
| `init` | 环境变量 | JSON：建表与全文索引结果（含实际解析器） |
| `import` | 工程目录（默认 `demo_project`） | JSON：导入批次、文档数量、各类文档数 |
| `stats` | — | JSON：库中各类文档数量、全文索引、导入日志 |
| `context --task-file <json>` | 任务描述文件 | 上下文的 Markdown（默认）或 JSON（`--json`） |
| `state show --task-id <id>` | 任务 ID | JSON：PowerContext 中保存的任务状态 |
| `state list` | — | JSON：所有已保存的任务状态 |
| `state save --task-id <id> --file <json>` | 状态 JSON | JSON：写入位置（Scope / Source / position） |
| `state reset` | — | JSON：清空本地任务状态（用于「从空状态开始」） |
| `run --task-file <json>` | 任务描述文件 | JSON：上下文、模型调用、补丁、测试结果、状态 |
| `reset --to before\|after` | — | JSON：从快照恢复业务工程 |

### 8.1 任务描述文件格式

```json
{
  "task_id": "TASK-ORD-003",
  "title": "为 filter_orders 增加金额区间筛选",
  "objective": "……",
  "task_text": "要改什么、哪些原有行为必须保持……",
  "target_tests": ["tests/test_filter_amount.py"],
  "regression_tests": ["tests/test_filter_status.py", "tests/test_filter_category.py"]
}
```

`task_text` 会被送入检索：其中的标识符用于定位符号与规格，剩余文本用于全文排序。

### 8.2 上下文输出

`context` 的 Markdown 输出包含四段：**适用规格条目**、**需要修改的代码**、
**验证用测试**、**任务状态**；每一项都带可核对的来源（文件路径、符号、规格编号）。
`--json` 输出额外带 `db_queries`（本次实际执行的 SQL），用于核对数据库确实参与了检索。

---

## 9. 执行验证样例

一条命令跑完全部 19 个步骤：

```bash
cd minimax
python validation/run_validation.py
```

脚本把每个步骤作为**独立进程**执行，并记录命令、工作目录、退出码、期望结果与代码版本
（`git rev-parse HEAD`），结果写入 `records/logs/validation-<时间戳>.json`。

步骤概览：

| 步骤 | 内容 | 期望 |
| --- | --- | --- |
| 01 | `doctor` | 数据库与 PowerContext 可用 |
| 02 | 恢复修改前快照 | 退出码 0 |
| 03 | **修改前**跑目标测试 | **失败**，且失败原因是 `filter_orders` 不支持 `min_amount` |
| 04 | **修改前**跑回归测试 | 通过 |
| 05 | 清空任务状态 | 从空状态开始 |
| 06 | 建表 + 全文索引 | 退出码 0 |
| 07 | 导入工程资料 | 退出码 0 |
| 08 | 构建上下文 | 包含样例规定的全部规格 / 代码 / 测试来源 |
| 09 | **MiniMax 改码** | 补丁被应用 |
| 10 | **修改后**跑目标测试 | 全部通过 |
| 11 | **修改后**跑回归测试 | 全部通过 |
| 12 | **新进程**读回任务状态 | 上下文含已完成事项与验证结果 |
| 13–15 | 更新规格 → 重新导入 → 查上下文 | 上下文含新规则 `R8` 且版本为 2 |
| 16–18 | 还原规格 → 重新导入 → 查上下文 | 上下文不再含 `R8`（证明不是固定答案） |
| 19 | 恢复修改后快照 | 退出码 0 |

只想复跑「不重新调用模型」的部分时：

```bash
python validation/run_validation.py --skip-agent
```

### 9.1 手工逐步复现

```bash
# 0) 回到修改前
python -m sdd_context reset --to before

# 1) 修改前：目标测试失败（功能未实现），回归测试通过
cd demo_project && python -m pytest tests/test_filter_amount.py            # 失败
cd .. && python -m pytest demo_project/tests/test_filter_status.py \
                  demo_project/tests/test_filter_category.py              # 通过

# 2) 导入工程资料并查看上下文
python -m sdd_context init
python -m sdd_context import
python -m sdd_context context --task-file validation/sample_task.json

# 3) 让 MiniMax 改码并跑测试
python -m sdd_context run --task-file validation/sample_task.json

# 4) 修改后：目标测试与回归测试都通过
cd demo_project && python -m pytest && cd ..

# 5) 重启模块后读取并使用任务状态
python -m sdd_context state show --task-id TASK-ORD-003
python -m sdd_context context --task-file validation/sample_task.json | tail -20
```

---

## 10. 测试命令

```bash
# 上下文模块自身的单元测试（不需要数据库）
python -m pytest tests

# 业务工程全部用例
cd demo_project && python -m pytest

# 目标测试（修改后应全绿）
python -m pytest tests/test_filter_amount.py

# 回归测试（修改前后都应全绿）
python -m pytest tests/test_filter_status.py tests/test_filter_category.py

# 端到端验证（19 步）
cd .. && python validation/run_validation.py
```

---

## 11. 已知限制

1. **全文解析器依赖服务端**：模块按 `ik` → `beng` → `ngram` → 内置的顺序探测可用解析器，
   并把实际使用的解析器写进 `sdd_meta.fulltext_parser`。内置解析器不切分中文，
   因此中文规格的检索质量会下降；`init` 的输出会明确显示实际使用的解析器。
2. **Windows 支持**：PowerContext 官方把 Windows 标为 experimental，嵌入式 seekdb
   （`pylibseekdb`）在 Windows 不可用。本作品因此让 PowerContext 使用 **SQLite 文件**后端
   （PowerContext 自带的后端），数据库侧使用 MySQL 协议连接外部 seekdb/OceanBase，
   两者都不受该限制影响。
3. **模型输出必须是 unified diff**：模块要求模型输出标准 diff；解析不到时会用更大的
   输出预算重试一次。若模型连续两次都不给出 diff，本次运行会如实记录
   `patch_error`，不会伪造修改结果。
4. **补丁应用是严格匹配**：diff 的上下文行必须与文件逐字一致，否则拒绝应用并记录
   `patch_error`，以免把错误的修改写进工程。
5. **任务状态保存在本地文件**：`minimax/.state/powercontext.db`（已 gitignore）。
   换机器或清空该文件后，任务状态从空开始。
6. **规格更新的验证范围**：验证样例演示的是「规格内容变化 → 重新导入 → 上下文随之变化」，
   覆盖题目要求的「不能始终返回固定答案」；未覆盖多版本规格并存时的历史版本检索。
