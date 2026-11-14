# MiniMax 开发记录

本文件记录 **MiniMax 如何参与本次任务的代码修改**：给了它什么、它返回了什么、
返回的内容如何变成提交的代码差异。

| 项目 | 内容 |
| --- | --- |
| 模型 | `MiniMax-M3.1-Flash` |
| 端点 | Anthropic 兼容协议 `POST {MINIMAX_BASE_URL}/messages` |
| 客户端 | [`sdd_context/minimax_client.py`](../sdd_context/minimax_client.py) |
| 鉴权 | `x-api-key` 与 `Authorization: Bearer` 同时发送；Key 只存在环境变量，不写盘、不打印 |
| 原始留档 | [`records/minimax/`](minimax/) —— 每次调用的 prompt、回答、usage 与补丁原文 |

> 配置示例见 [`.env.example`](../.env.example)（占位值，不含真实凭据）。

---

## 1. 本次任务

| 项目 | 内容 |
| --- | --- |
| 任务 ID | `TASK-ORD-003` |
| 要改什么 | `orderbook.filtering.filter_orders` 新增 `min_amount` / `max_amount` 金额区间筛选 |
| 必须保持 | `SPEC-ORD-001` 的 `status` / `category` 过滤语义、输出顺序、输入不被修改 |
| 验证方式 | 目标测试 `tests/test_filter_amount.py`（8 个）+ 回归测试 `tests/test_filter_status.py`、`tests/test_filter_category.py`（9 个） |

任务描述文件：[`validation/sample_task.json`](../validation/sample_task.json)。

## 2. 提供给 MiniMax 的上下文

上下文**不是**人工整理的文字，而是由模块从 seekdb / OceanBase 中检索出来、
再组装成提示词的。每一条都带来源：

| 提示词段落 | 内容 | 来源 |
| --- | --- | --- |
| `## 任务` | 任务描述（要改什么 / 哪些行为必须保持） | `validation/sample_task.json` |
| `## 适用规格条目` | `SPEC-ORD-003.R1 … R7`、`SPEC-ORD-001.R1 … R5`（14 条，含版本号） | 数据库 `sdd_document`（`applies_to` 关联 + 全文排序） |
| `## 需要修改的代码` | `src/orderbook/filtering.py` 的**完整文件内容** + 要改的符号 `filter_orders` | 数据库 `sdd_document`（符号精确命中） |
| `## 验证用测试` | 3 个测试文件的完整内容（17 个用例） | 数据库 `sdd_document`（`find_in_set` 覆盖关系） |
| `## 上一轮的任务状态` | 已完成事项、待办事项、已有验证结果 | PowerContext |

提示词里给出的文件是**完整文件**而不是函数片段：这样模型产出的 diff 的上下文行与行号
才能与工作区的真实文件对上（见 §5 的迭代记录）。

提示词原文保存在每次调用的 `*_answer.md` 中（`## 尝试 N · Prompt` 一节）。

## 3. 模型返回

模型返回的是**一个 unified diff**（不是方案说明、不是伪代码）。例如：

```diff
--- a/src/orderbook/filtering.py
+++ b/src/orderbook/filtering.py
@@ -18,21 +18,38 @@
 def filter_orders(
     orders: Iterable[Order],
     *,
     status: Optional[str] = None,
     category: Optional[str] = None,
+    min_amount: Optional[object] = None,
+    max_amount: Optional[object] = None,
 ) -> List[Order]:
-    """Return the orders that match ``status`` and ``category``.
+    """Return the orders that match ``status``, ``category``, and amount bounds.
...
+    min_bound = Decimal(str(min_amount)) if min_amount is not None else None
+    max_bound = Decimal(str(max_amount)) if max_amount is not None else None
+    if (
+        min_bound is not None
+        and max_bound is not None
+        and min_bound > max_bound
+    ):
+        raise ValueError("min_amount must not be greater than max_amount")
...
+        if min_bound is not None and order.amount < min_bound:
+            continue
+        if max_bound is not None and order.amount > max_bound:
+            continue
```

（以上为 `records/minimax/TASK-ORD-003_20261114-164050.patch` 的节选，完整补丁见同目录。）

模型产出的实现与规格逐条对应：

| 规格条目 | 模型实现 |
| --- | --- |
| `SPEC-ORD-003.R1` | 新增 `min_amount` / `max_amount` 关键字参数，默认 `None` |
| `SPEC-ORD-003.R2` / `R3` | `amount < min_bound` / `amount > max_bound` 时跳过 → 边界含在内 |
| `SPEC-ORD-003.R4` | `Decimal(str(x))` 统一按十进制解析 |
| `SPEC-ORD-003.R5` | `min_bound > max_bound` 时 `raise ValueError(...)` |
| `SPEC-ORD-003.R6` | 未触碰 `status` / `category` 分支，输出顺序不变 |
| `SPEC-ORD-003.R7` | 只读 `order.amount`，不修改订单对象 |
| `SPEC-ORD-001.R1 … R5` | 原有分支与顺序原样保留 |

## 4. 从返回到提交的代码差异

1. 模块用 [`patcher.extract_diff`](../sdd_context/patcher.py) 从回答中取出 diff，
   原样写入 `records/minimax/<task>_<时间戳>.patch`；
2. 用 `patcher.apply_unified_diff` 应用到业务工程（纯 Python 实现，严格校验上下文行）；
3. 应用后的文件即业务工程中的 `src/orderbook/filtering.py`；
4. 修改前后的**完整快照**分别保存在 [`code/before/`](../code/before/) 与
   [`code/after/`](../code/after/)，两者的差异即提交材料中的「代码差异」
   （[`code/orderbook_min_amount.patch`](../code/orderbook_min_amount.patch)）。

因此：**提交的代码差异 = MiniMax 生成的 diff 应用后的结果**，一一对应。
模型返回的原始补丁留档在 `records/minimax/`，可逐行核对。

## 5. 迭代过程（如实记录）

开发过程中出现过三次失败，都已修复并保留说明：

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| 回答里没有 diff | `max_tokens=8192` 被模型的思考过程耗尽，输出被截断 | 默认预算提高到 `32768`；仍拿不到 diff 时用双倍预算重试一次，并把失败如实写进 `patch_error` |
| diff 应用失败：`context mismatch ... at line 3` | 提示词只给了函数片段，模型推测的文件行号与真实文件差 1 行 | 提示词改为提供**完整文件**；补丁应用器同时像 `patch` 一样在 hunk 声明位置附近小窗口内搜索锚点（上下文行仍须逐字一致） |
| `TaskOutcome` 校验失败 | 状态值与观察项的类型不符合 PowerContext 的契约 | 状态映射到 `succeeded/partial/failed/…`，观察项构造为 `WorkClaim` |

## 6. 复现方式

```bash
cd minimax
export MINIMAX_API_KEY=<你的 Key>
export MINIMAX_BASE_URL=https://api.minimaxi.com/anthropic
export MINIMAX_MODEL=MiniMax-M3

python -m sdd_context reset --to before
python -m sdd_context import
python -m sdd_context run --task-file validation/sample_task.json
```

`run` 会依次完成：构建上下文 → 调用 MiniMax → 保存补丁 → 应用补丁 → 跑目标与回归测试 →
把任务状态写入 PowerContext，并把本次调用的 prompt、回答、usage 与补丁留在
`records/minimax/`。

评审不要求模型重新生成完全相同的补丁：只要补丁满足规格条目、目标测试与回归测试通过即可。
