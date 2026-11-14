---
id: SPEC-ORD-003
title: 订单金额区间筛选
version: 1
applies_to: orderbook.filtering.filter_orders
tags: [filter, amount, order, range]
---

# 订单金额区间筛选

本规格要求 `orderbook.filtering.filter_orders` 支持按金额区间筛选订单。实现前必须确认 `SPEC-ORD-001` 的既有语义保持不变。

- R1: `filter_orders` 必须接受 `min_amount` 与 `max_amount` 两个可选关键字参数，默认值均为 `None`。
- R2: `min_amount` 生效时，只保留 `amount >= min_amount` 的订单（边界值包含在内）。
- R3: `max_amount` 生效时，只保留 `amount <= max_amount` 的订单（边界值包含在内）。
- R4: 金额比较统一使用 `decimal.Decimal`；入参为 `str` 时按十进制字符串解析，`None` 表示不启用该条件。
- R5: 当 `min_amount` 与 `max_amount` 同时给出且 `min_amount > max_amount` 时，必须抛出 `ValueError`。
- R6: 不得改变 `SPEC-ORD-001` 定义的 `status` / `category` 过滤语义，也不得改变输出顺序。
- R7: 不得修改传入的订单对象，金额比较只读。
