# orderbook — SDD 验证用业务工程

这是一个可运行、有测试的小型业务工程，用作赛题「规有所依——面向软件持续演进的 SDD
上下文管理系统」的验证对象。

## 结构

```
orderbook/
├── pytest.ini
├── specs/                     # 规格（Markdown + YAML front matter）
│   ├── spec_status_filter.md  # SPEC-ORD-001：状态与品类过滤（既有行为）
│   └── spec_amount_filter.md  # SPEC-ORD-003：金额区间筛选（本次目标）
├── src/orderbook/
│   ├── __init__.py
│   ├── models.py              # Order 数据模型与金额转换
│   └── filtering.py           # filter_orders —— 本次要改的符号
└── tests/
    ├── conftest.py
    ├── test_filter_status.py     # 回归测试（改动前后都必须通过）
    ├── test_filter_category.py   # 回归测试（改动前后都必须通过）
    └── test_filter_amount.py     # 目标测试（改动前失败，改动后通过）
```

## 运行测试

```bash
cd demo_project
python -m pytest            # 全部用例
python -m pytest tests/test_filter_amount.py   # 仅目标测试
```

依赖只有 `pytest`（见 `requirements.txt`）。测试通过 `tests/conftest.py` 把 `src/`
加入 `sys.path`，因此不需要安装本工程。

## 当前状态

* 仓库中提交的是**修改后**的状态：`filter_orders` 已支持 `min_amount` /
  `max_amount`（由 MiniMax 生成的补丁实现）。
* `../code/before/` 保存修改前的快照，`../code/after/` 保存修改后的快照，
  两者的差异见 `../code/orderbook_min_amount.patch`。

```bash
# 回到修改前（目标测试会因为功能未实现而失败）
python -m sdd_context reset --to before

# 回到修改后
python -m sdd_context reset --to after
```
