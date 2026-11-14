---
id: SPEC-ORD-001
title: 订单状态与品类过滤
version: 1
applies_to: orderbook.filtering.filter_orders
tags: [filter, status, category, order]
---

# 订单状态与品类过滤

本规格定义 `orderbook.filtering.filter_orders` 的既有过滤语义。任何改动都不得破坏以下条目。

- R1: `status` 为 `None` 时不按状态过滤；否则只保留 `order.status == status` 的订单，比较区分大小写。
- R2: `category` 为 `None` 时不按品类过滤；否则只保留 `order.category == category` 的订单，比较区分大小写。
- R3: `status` 与 `category` 同时给出时，两个条件按 AND 组合。
- R4: 输出必须保持输入顺序，返回列表不得包含重复项。
- R5: 过滤过程不得修改传入的订单对象，也不得修改调用方持有的输入序列。
