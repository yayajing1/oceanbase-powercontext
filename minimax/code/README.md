# 代码与测试材料

| 文件 / 目录 | 内容 |
| --- | --- |
| [`before/`](before/) | **修改前**的业务工程完整快照（`filter_orders` 尚不支持金额区间筛选） |
| [`after/`](after/) | **修改后**的业务工程完整快照（由 MiniMax 生成的补丁应用后的结果） |
| [`orderbook_min_amount.patch`](orderbook_min_amount.patch) | 两个快照之间的 unified diff |

`before/` 与 `after/` 的差异**只有 `src/orderbook/filtering.py`**：

```
--- a/src/orderbook/filtering.py
+++ b/src/orderbook/filtering.py
@@ -20,19 +20,36 @@
     status: Optional[str] = None,
     category: Optional[str] = None,
+    min_amount: Optional[object] = None,
+    max_amount: Optional[object] = None,
 ) -> List[Order]:
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

**测试文件与规格文件在两个快照中完全一致** —— 这正是题目要求的：
测试集在修改前后保持一致，没有删除断言、跳过测试或修改预期结果。

新增的功能测试在 `before/` 快照中就已经存在（`tests/test_filter_amount.py`），
它在修改前因为「目标行为未实现」而失败：

```
TypeError: filter_orders() got an unexpected keyword argument 'min_amount'
```

相关的回归测试（`tests/test_filter_status.py`、`tests/test_filter_category.py`）
在两个快照中逐字相同，修改前后都通过。

## 快照与工作区的关系

仓库中 `demo_project/` 目录保存的是**修改后**的状态（即 `after/`）。两个快照用于
在验证过程中来回切换：

```bash
python -m sdd_context reset --to before   # 回到修改前
python -m sdd_context reset --to after    # 回到修改后
```

生成快照的脚本：[`../tools/make_snapshot.py`](../tools/make_snapshot.py)。
