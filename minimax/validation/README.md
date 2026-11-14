# 验证样例

一个可端到端执行的样例：它规定**要改什么**、**哪些原有行为必须保持**，并给出
每一步的预期结果与重置方法。

- 机器可读的任务描述：[`sample_task.json`](sample_task.json)
- 端到端脚本：[`run_validation.py`](run_validation.py)
- 规格变更脚本：[`apply_spec_update.py`](apply_spec_update.py)

---

## 1. 任务描述

| 字段 | 值 |
| --- | --- |
| 任务 ID | `TASK-ORD-003` |
| 标题 | 为 `filter_orders` 增加金额区间筛选 |
| 要改什么 | `orderbook.filtering.filter_orders`（文件 `src/orderbook/filtering.py`）新增 `min_amount` / `max_amount` 两个关键字参数；金额比较使用 `decimal.Decimal`；`min_amount > max_amount` 时抛 `ValueError` |
| 哪些原有行为必须保持 | `status` / `category` 过滤语义（`SPEC-ORD-001` R1–R5）、输出顺序、输入不被修改 |
| 目标测试 | `tests/test_filter_amount.py`（8 个用例） |
| 回归测试 | `tests/test_filter_status.py`、`tests/test_filter_category.py`（9 个用例） |

完整的任务文本见 [`sample_task.json`](sample_task.json) 的 `task_text` 字段，
它同时是送入检索的查询串。

---

## 2. 有效规格

上下文必须包含下列**规格条目**（条目编号即定位符，可在 `demo_project/specs/` 中逐条核对）：

### `SPEC-ORD-003` 订单金额区间筛选（本次目标，版本 1）

| 条目 | 内容 |
| --- | --- |
| `SPEC-ORD-003.R1` | 必须接受 `min_amount` 与 `max_amount` 两个可选关键字参数，默认均为 `None` |
| `SPEC-ORD-003.R2` | `min_amount` 生效时只保留 `amount >= min_amount` 的订单（边界含在内） |
| `SPEC-ORD-003.R3` | `max_amount` 生效时只保留 `amount <= max_amount` 的订单（边界含在内） |
| `SPEC-ORD-003.R4` | 金额比较统一使用 `decimal.Decimal`；`str` 按十进制字符串解析；`None` 表示不启用 |
| `SPEC-ORD-003.R5` | `min_amount` 与 `max_amount` 同时给出且 `min_amount > max_amount` 时必须抛 `ValueError` |
| `SPEC-ORD-003.R6` | 不得改变 `SPEC-ORD-001` 定义的 `status` / `category` 语义，也不得改变输出顺序 |
| `SPEC-ORD-003.R7` | 不得修改传入的订单对象，金额比较只读 |

### `SPEC-ORD-001` 订单状态与品类过滤（既有行为，版本 1）

| 条目 | 内容 |
| --- | --- |
| `SPEC-ORD-001.R1` | `status` 为 `None` 时不过滤，否则只保留 `order.status == status`，区分大小写 |
| `SPEC-ORD-001.R2` | `category` 同理 |
| `SPEC-ORD-001.R3` | 两者同时给出时按 AND 组合 |
| `SPEC-ORD-001.R4` | 输出保持输入顺序，不含重复项 |
| `SPEC-ORD-001.R5` | 过滤过程不得修改传入对象或输入序列 |

---

## 3. 上下文中必须包含的来源

`context` 命令的输出必须能定位到下列来源（`sample_task.json` 的 `expected` 字段，
由 `run_validation.py` 的步骤 08 自动核对）：

**规格条目**（14 条）

```
SPEC-ORD-003.R1  SPEC-ORD-003.R2  SPEC-ORD-003.R3  SPEC-ORD-003.R4
SPEC-ORD-003.R5  SPEC-ORD-003.R6  SPEC-ORD-003.R7  SPEC-ORD-003
SPEC-ORD-001.R1  SPEC-ORD-001.R2  SPEC-ORD-001.R3  SPEC-ORD-001.R4
SPEC-ORD-001.R5  SPEC-ORD-001
```

**代码**

```
src/orderbook/filtering.py::filter_orders
```

**测试**（17 个用例，覆盖 3 个文件）

```
tests/test_filter_amount.py::test_min_amount_keeps_orders_at_or_above_the_bound
tests/test_filter_amount.py::test_max_amount_keeps_orders_at_or_below_the_bound
tests/test_filter_amount.py::test_amount_bounds_accept_decimal_strings
tests/test_filter_amount.py::test_min_amount_greater_than_max_amount_raises_value_error
tests/test_filter_status.py::test_status_filters_to_matching_orders
tests/test_filter_category.py::test_category_filters_to_matching_orders
（其余用例同样在上下文中）
```

---

## 4. 初始数据

导入业务工程后，数据库 `sdd_context` 中有 46 条可定位来源：

| 类别 | 条数 | 说明 |
| --- | ---: | --- |
| `spec` | 14 | 2 个规格文件的总览 + 12 条规则 |
| `code` | 7 | `filtering.py`、`models.py`、`__init__.py` 的符号与文件级条目 |
| `test` | 24 | 17 个用例 + 3 个测试辅助函数 + 4 个文件级条目 |
| `doc` | 1 | `README.md` |

其中 `code` 与 `test` 的**文件级条目**（`__file__`）用于给模型提供完整文件内容，
使生成的 diff 的上下文行与行号都能对上真实文件。

---

## 5. 操作步骤

完整流程见 [`../RUN.md` §9](../RUN.md#9-执行验证样例)。最小步骤：

```bash
cd minimax

# 0) 回到修改前
python -m sdd_context reset --to before

# 1) 修改前：目标测试失败、回归测试通过
python -m pytest demo_project/tests/test_filter_amount.py                     # 失败
python -m pytest demo_project/tests/test_filter_status.py \
                  demo_project/tests/test_filter_category.py                   # 通过

# 2) 导入工程资料
python -m sdd_context init
python -m sdd_context import

# 3) 查看上下文（应含 §3 列出的全部来源）
python -m sdd_context context --task-file validation/sample_task.json

# 4) 让 MiniMax 改码并跑测试
python -m sdd_context run --task-file validation/sample_task.json

# 5) 修改后：目标与回归测试全部通过
python -m pytest demo_project

# 6) 换一个进程读回任务状态
python -m sdd_context state show --task-id TASK-ORD-003
```

一步到位的版本：

```bash
python validation/run_validation.py
```

---

## 6. 预期结果

| # | 操作 | 预期 |
| --- | --- | --- |
| 1 | 修改前跑 `tests/test_filter_amount.py` | **退出码 1**，8 个用例全部失败，失败原因是 `TypeError: filter_orders() got an unexpected keyword argument 'min_amount'`（目标行为未实现，**不是**环境错误） |
| 2 | 修改前跑回归测试 | **退出码 0**，9 个用例通过 |
| 3 | `context` | 包含 §3 的全部规格 / 代码 / 测试来源，并输出实际执行的 SQL |
| 4 | `run` | 模型返回 unified diff，补丁被应用；目标测试 **8/8 通过**，回归测试 **9/9 通过**；任务状态写入 PowerContext |
| 5 | 新进程 `state show` | 返回任务状态：已完成事项、待办事项、两条验证结果（`passed`） |
| 6 | 新进程 `context` | 「任务状态」一段显示上述内容，说明状态被读取并使用 |
| 7 | 更新规格 → 重新导入 → `context` | 上下文出现 `SPEC-ORD-003.R8`，且版本号为 `2` |
| 8 | 还原规格 → 重新导入 → `context` | 上下文不再出现 `SPEC-ORD-003.R8`（证明不是固定答案） |

---

## 7. 重置方法

| 想回到的状态 | 命令 |
| --- | --- |
| 修改前的业务工程 | `python -m sdd_context reset --to before` |
| 修改后的业务工程 | `python -m sdd_context reset --to after` |
| 空的任务状态 | `python -m sdd_context state reset` |
| 空的数据库 | `python -m sdd_context init` 后重新 `import`（`import` 幂等，可反复执行） |
| 规格更新前的规格 | `python validation/apply_spec_update.py --revert` |

快照位于 `code/before/` 与 `code/after/`，是业务工程在两个阶段的完整副本。

---

## 8. 规格更新（验证「不是固定答案」）

`apply_spec_update.py --apply` 做三件事：

1. `SPEC-ORD-003` 的 `version` 从 `1` 改成 `2`；
2. 规则 `R2` 改写为「……金额以人民币元为单位，不做汇率换算。」；
3. 新增规则 `R8`：「金额筛选必须与 `status` / `category` 条件同时生效，两个条件不允许互相覆盖。」

`R8` 只陈述实现已经保证的行为，因此**测试集在更新前后完全一致**，不需要改动任何断言。
`--revert` 是它的精确逆操作。
