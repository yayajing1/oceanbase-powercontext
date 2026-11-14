# MiniMax × OceanBase —— 规有所依：面向软件持续演进的 SDD 上下文管理系统

> 为 AI 开发助手实现上下文模块：提供任务所需的规格、代码和测试，并使用 MiniMax
> 完成一次实际代码修改。

| 交付物 | 文件 |
| --- | --- |
| 完整源码 | [`sdd_context/`](sdd_context/)、[`demo_project/`](demo_project/)、[`validation/`](validation/)、[`tools/`](tools/) |
| 运行说明 | [`RUN.md`](RUN.md) |
| 验证样例 | [`validation/README.md`](validation/README.md)、[`validation/sample_task.json`](validation/sample_task.json) |
| 代码与测试 | [`code/`](code/)、[`demo_project/tests/`](demo_project/tests/)、[`records/logs/`](records/logs/) |
| 技术使用记录 | [`records/`](records/) |
| 一页技术说明 | 本文 |

---

## 1. 问题与做法

AI 开发助手改代码时需要三样东西：**当前应遵守哪些要求**、**应该改哪些代码**、
**如何验证结果**。资料分散、规格变化或任务中断，就会遗漏约束、使用过时信息或重复劳动。

本模块把「找资料」这件事从助手的上下文窗口里挪到**可复用的外部系统**里：

```
任务描述 ──►  seekdb / OceanBase  ──►  开发上下文  ──►  MiniMax  ──►  unified diff
                    (规格/代码/测试 + 全文索引)      (含上一轮状态)        │
                                                                      ▼
                            PowerContext  ◄── 任务状态（已完成/待办/验证结果）
                          (跨进程可读回)          │
                                                                      ▼
                                                          pytest（目标 + 回归）
```

---

## 2. 模块结构

| 文件 | 职责 |
| --- | --- |
| `sdd_context/config.py` | 全部配置来自环境变量；API Key 不落盘、不打印 |
| `sdd_context/documents.py` | 把规格 / Python 源码 / 测试解析成**可定位的条目**：规格按规则切分（`SPEC-ORD-003.R2`），代码按符号切分（`filtering.py::filter_orders`），测试按用例切分 |
| `sdd_context/textnorm.py` | 检索文本归一化：CJK 逐字拆开、标识符按下划线拆分，使中文规格在英文解析器下也能命中 |
| `sdd_context/db.py` | seekdb / OceanBase 存储：`sdd_document` 表 + 原生全文索引 `ft_sdd_search`，全部检索都是 SQL |
| `sdd_context/ingest.py` | 幂等导入：同 `doc_id` 覆盖，文件里消失的旧条目删除 |
| `sdd_context/retrieve.py` | 上下文选择：符号定位 → 规格关联 → 测试覆盖 → 全文排序 |
| `sdd_context/context.py` | 上下文组装（Markdown / JSON）与模型提示词 |
| `sdd_context/state.py` | **PowerContext** 任务状态：content Source + Task Outcome 双写 |
| `sdd_context/minimax_client.py` | MiniMax API 客户端（Anthropic 兼容协议） |
| `sdd_context/patcher.py` | unified diff 提取与**严格**应用（纯 Python，无需 git/patch） |
| `sdd_context/agent.py` | 闭环编排：上下文 → MiniMax → 打补丁 → 跑测试 → 写状态 |
| `sdd_context/cli.py` | 命令行入口 |

---

## 3. 上下文怎么选

检索完全在数据库内完成，模块不在 Python 里重做匹配。每次查询依次执行：

| 步骤 | SQL 思路 | 解决的问题 |
| --- | --- | --- |
| ① 符号定位 | `select distinct symbol … where symbol in (…)` | 任务文本里的标识符哪些是真实符号 |
| ② 规格关联 | `where kind='spec' and applies_to like '%filter_orders%'` | 哪些规格条目适用于该符号 |
| ③ 测试覆盖 | `where kind='test' and find_in_set('filter_orders', ref_symbols) > 0` | 哪些用例覆盖该符号 |
| ④ 全文排序 | `where match(search_text) against (…)` | 用自然语言描述的任务，补齐关键词命中的材料 |

①②③保证「该出现的来源一定出现」（结构化关联），④负责「按相关性排序并补齐」（全文索引）。
两类结果合并去重后，每类最多 20 条。

> 全文解析器在 `init` 时探测：`ik` → `beng` → `ngram` → 内置，实际使用的解析器写在
> `sdd_meta.fulltext_parser`。中文检索依赖前三种；内置解析器不切分中文。

上下文输出分四段，每一段都带可核对的来源：

1. **适用规格条目** —— `SPEC-ORD-003.R1 … R7`、`SPEC-ORD-001.R1 … R5`（含版本号）
2. **需要修改的代码** —— `src/orderbook/filtering.py::filter_orders`
3. **验证用测试** —— `tests/test_filter_amount.py::test_min_amount_…` 等 17 个用例
4. **任务状态** —— 来自 PowerContext 的已完成事项、待办事项、已有验证结果

---

## 4. 三项技术的分工

| 技术 | 承担的作用 | 在代码中的位置 | 证据 |
| --- | --- | --- | --- |
| **seekdb / OceanBase** | 存储工程资料与索引，实际查询结果用于构造上下文 | `db.py`、`ingest.py`、`retrieve.py` | `records/database_usage.md` |
| **MiniMax** | 参与本次任务的代码生成与修改 | `minimax_client.py`、`agent.py` | `records/minimax_development.md` |
| **PowerContext** | 保存任务进展与验证结果，并在后续运行中读取、使用 | `state.py` | `records/powercontext_state.md` |

- **seekdb / OceanBase**：库 `sdd_context`，表 `sdd_document`，原生全文索引
  `ft_sdd_search`。每次 `context` 命令都会在输出里带上**实际执行的 SQL**（`db_queries`）。
- **MiniMax**：`MiniMax-M3.1-Flash`（Anthropic 兼容端点）。模型收到的是上下文本身，
  返回的是 unified diff；原始请求、回答、usage 与补丁原文全部留档。
- **PowerContext**：`powercontext==1.2.0`，进程内 Core SDK
  （`open_builtin_runtime`），SQLite 文件后端。任务状态写成一个 content Source
  （JSON，可带任意字段）与一条 Task Outcome（结构化的 checks / remaining_work），
  同一个幂等键对应同一个 Scope，因此**重启模块后能读回并继续使用**。

---

## 5. 验证样例速览

业务工程 `demo_project/`（可运行、有测试）：

- 规格：`SPEC-ORD-001`（状态/品类过滤，既有行为）、`SPEC-ORD-003`（金额区间筛选，本次目标）
- 要改的符号：`orderbook.filtering.filter_orders`
- 目标测试：`tests/test_filter_amount.py`（8 个用例）
- 回归测试：`tests/test_filter_status.py`、`tests/test_filter_category.py`（9 个用例）

一次完整验证（19 步，每步独立进程，记录命令 / 退出码 / 代码版本）：

```bash
cd minimax
python validation/run_validation.py
```

关键结论：

- 修改前目标测试**因功能未实现而失败**（`TypeError: filter_orders() got an unexpected
  keyword argument 'min_amount'`），不是环境错误；修改后目标测试与回归测试**全部通过**。
- 规格更新并重新导入后，上下文返回的是**更新后的**条目；还原并重新导入后，新规则消失，
  证明上下文不是固定答案。
- 清空状态后写入任务状态，**换一个进程**再读，状态仍在并进入下一轮上下文。

详见 [`validation/README.md`](validation/README.md) 与 [`records/logs/`](records/logs/)。

---

## 6. 扩展能力（题面列为非必做项）

| 能力 | 实现情况 |
| --- | --- |
| 规格变更处理 | 导入按 `doc_id` 幂等覆盖，文件内消失的旧条目自动删除；规格改版后重新导入即生效（验证步骤 13–18 完整覆盖往返） |
| 跨会话任务恢复 | 任务状态存在 PowerContext 的 SQLite 文件里，换进程、换终端都能读回，并作为「上一轮的任务状态」进入下一轮提示词 |
| 跨模块协作 | 模块不绑定任何特定 Agent：`context` 输出 Markdown / JSON，`run` 输出结构化报告，任何宿主（CLI、CI、Agent 插件）都能直接调用 |
| 成本与时延 | 一次上下文构建 = 7 条 SQL、约 **235–265 ms**（本机 3 次实测），全部匹配在数据库内完成（`TEXT RETRIEVAL SCAN`）；模型侧的 input / output token 与耗时逐次留档在 `records/minimax/` |

## 7. 第三方与来源

| 内容 | 来源 | 说明 |
| --- | --- | --- |
| `powercontext` | [oceanbase/powercontext](https://github.com/oceanbase/powercontext)，Apache-2.0 | **未修改**，通过 PyPI 安装并用公开 SDK（`open_builtin_runtime`）调用 |
| `pymysql` / `httpx` / `pyyaml` / `pytest` | PyPI | 仅作为运行依赖 |
| `docker-compose.oceanbase.yaml` | 镜像 `ghcr.io/oceanbase/oceanbase-ce:4.3.5.6-...` | 只引用官方镜像，未内嵌任何 OceanBase 代码或数据 |
| `demo_project/`（orderbook） | 本作品原创 | 小型业务工程，规格、代码与测试均为本次编写 |
| `sdd_context/` | 本作品原创 | 上下文模块全部代码 |
| 规格条目、任务描述、验证样例 | 本作品原创 | 任务与规格为本次设计，不含任何私有资料 |

模型仅接收本作品自己的规格、代码与测试内容；本次开发**没有**向任何在线模型发送
仓库中的第三方源码。

## 8. 已知限制

见 [`RUN.md` §11](RUN.md#11-已知限制)。
