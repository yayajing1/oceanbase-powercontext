# PowerContext 任务状态使用记录

本文件记录 **PowerContext 如何承担「保存任务进展与验证结果，并在后续运行中读取、使用」**。

| 项目 | 内容 |
| --- | --- |
| 版本 | `powercontext==1.2.0`（PyPI） |
| 接入方式 | **进程内 Core SDK**（`powercontext.builtin.runtime.open_builtin_runtime`），不起 HTTP 服务 |
| 存储后端 | PowerContext 自带的 **SQLite 文件**（`minimax/.state/powercontext.db`） |
| 代码位置 | [`sdd_context/state.py`](../sdd_context/state.py) |
| 作用域 | 一个幂等键 `sdd-context` 对应一个 Scope，本次为 `scp_4j0fp6f2fpqcjjr0czy3h0qvvk` |

> 题目允许 SDK / HTTP API / MCP / 插件任选其一。本作品选 **SDK**：评审无需先拉起一个
> 服务进程，验证步骤更短，且状态读写都在同一个进程模型里，行为可预期。

---

## 1. 存了什么

每次保存任务状态时，写入**两份**记录：

| 记录 | PowerContext 概念 | 内容 |
| --- | --- | --- |
| 状态文档 | content Source（`source_type='content'`） | 整个任务状态的 JSON：`task_id` / `objective` / `status` / `summary` / `done[]` / `todo[]` / `verification[]` / `updated_at` |
| 任务结果 | Task Outcome（`kind='task-outcome'`） | 结构化的 `objective` / `status` / `observations` / `checks` / `remaining_work`，便于直接查看「已验证/未验证」 |

对应 PowerContext 的 API：

```python
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import BuiltinConfig, open_builtin_runtime
from powercontext.builtin.runtime.models import CaptureSource
from powercontext.builtin.scope import ScopeDraft
from powercontext.builtin.work import RecordTaskOutcome, TaskCheck, TaskOutcome, WorkClaim

config = BuiltinConfig(database=SQLiteConfig(url="sqlite+aiosqlite:///<abs path>/powercontext.db"))
async with open_builtin_runtime(config, scheduler_path="<abs path>/scheduler.db") as runtime:
    scope = await runtime.scopes.create(ScopeDraft(title=..., summary=..., idempotency_key="sdd-context"))
    receipt = await runtime.sources.for_scope(scope.scope_id).capture(
        CaptureSource(source_id="sdd-task-TASK-ORD-003", content=json.dumps(state), metadata={...}))
    await runtime.work.for_scope(scope.scope_id).record_outcome(
        RecordTaskOutcome(source_id="sdd-task-TASK-ORD-003-outcome", outcome=TaskOutcome(...)))
```

Scope 的创建是**幂等**的：同一个 `idempotency_key` + 同一份 draft 摘要会返回同一个
`scope_id`，因此多次运行共用同一份状态。

同一任务的多次保存会写入新的 Source 修订（`sdd-task-<id>`、`sdd-task-<id>-v2`……），
读取时取修订号最大的那一条。

## 2. 写入记录（来自最终验证运行）

```
scope_id : scp_4j0fp6f2fpqcjjr0czy3h0qvvk
source_id: sdd-task-TASK-ORD-003
position : 1
kind     : task-outcome（position 2）
```

写入的状态文档：

```json
{
  "task_id": "TASK-ORD-003",
  "objective": "让 orderbook.filtering.filter_orders 支持 min_amount / max_amount 金额区间筛选，同时保持 SPEC-ORD-001 定义的 status / category 过滤语义与输出顺序不变。",
  "status": "completed",
  "summary": "patch_applied=True; tests_passed=2/2; status=completed",
  "done": [
    "MiniMax 生成的补丁已应用：src/orderbook/filtering.py",
    "验证通过：target tests: tests/test_filter_amount.py",
    "验证通过：regression tests: tests/test_filter_status.py, tests/test_filter_category.py"
  ],
  "todo": [],
  "verification": [
    {"name": "target tests: tests/test_filter_amount.py", "status": "passed", "details": "8 passed in 0.01s"},
    {"name": "regression tests: tests/test_filter_status.py, tests/test_filter_category.py", "status": "passed", "details": "9 passed in 0.02s"}
  ],
  "updated_at": "2026-11-14T08:52:56.141236+00:00"
}
```

## 3. 读回记录（重启之后）

验证脚本的步骤 12 是**另一个进程**（`python -m sdd_context context …`），它重新打开
同一个 SQLite 文件、重新解析同一个幂等键得到同一个 Scope，再从 Source 日志里读回
状态。读到的内容：

```json
{
  "_powercontext": {
    "scope_id": "scp_4j0fp6f2fpqcjjr0czy3h0qvvk",
    "source_id": "sdd-task-TASK-ORD-003",
    "position": 1,
    "content_digest": "sha256:0ddc2b56…"
  },
  "status": "completed",
  "done": [ … 3 项 … ],
  "todo": [],
  "verification": [ … 2 条 passed … ]
}
```

这份状态不只是被读出来，还被**用于后续处理**：

1. `context` 命令把它渲染进上下文的「任务状态（来自 PowerContext）」一段；
2. `run` 命令把它作为「上一轮的任务状态」写进给 MiniMax 的提示词，模型因此知道
   上一轮做到哪里、还剩什么；
3. 新的状态在原状态基础上追加 `done` / `todo`，形成连续的进展记录。

## 4. 从空状态开始

验证脚本的步骤 05 先清空状态：

```bash
python -m sdd_context state reset      # 删除本地 PowerContext 存储文件
```

随后步骤 09 写入任务状态，步骤 12 在新进程里读回。整个过程在
`records/logs/validation-20261114-165205.json` 中有完整记录（命令、退出码、代码版本）。

## 5. 复现方式

```bash
cd minimax
python -m sdd_context state reset                 # 从空状态开始
python -m sdd_context run --task-file validation/sample_task.json   # 写入状态
python -m sdd_context state show --task-id TASK-ORD-003             # 读回（新进程）
python -m sdd_context state list                                     # 列出全部任务
```

## 6. 备注

- 任务状态保存在 `minimax/.state/powercontext.db`（已 gitignore），因此换机器或清空该
  文件后状态从空开始；这正是「从空状态开始保存任务记录」所需的初始条件。
- PowerContext 的 SQLite 后端是它自带的默认后端；本作品**没有**修改 PowerContext 的
  任何代码，只通过公开 SDK 调用它。
