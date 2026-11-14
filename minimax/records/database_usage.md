# 数据库（seekdb / OceanBase）使用记录

本文件记录数据库在本次任务中**实际承担**的工作：存了什么、查了什么、查询结果如何
进入开发上下文。

| 项目 | 内容 |
| --- | --- |
| 数据库 | seekdb v1.4.0.0（`5.7.25-OceanBase seekdb-v1.4.0.0`），MySQL 协议 |
| 连接方式 | `pymysql` 直连（[`sdd_context/db.py`](../sdd_context/db.py)） |
| 库 | `sdd_context` |
| 表 | `sdd_document`、`sdd_meta`、`sdd_import_log` |
| 索引 | 原生全文索引 `ft_sdd_search`（`search_text`），解析器 `ik` |
| 客户端 | [`sdd_context/db.py`](../sdd_context/db.py)、[`sdd_context/retrieve.py`](../sdd_context/retrieve.py) |

> 本次验证用的实例是本机编译的 seekdb；换成 OceanBase CE（`docker-compose.oceanbase.yaml`）
> 或任意兼容实例，SQL 与表结构完全相同。

---

## 1. 存了什么

工程资料被解析成**可定位的条目**后入库，共 46 条：

| 类别 | 条数 | 定位方式 | 示例 |
| --- | ---: | --- | --- |
| `spec` | 14 | 规格编号 + 规则号 | `SPEC-ORD-003.R2` |
| `code` | 7 | 文件 + 符号 | `src/orderbook/filtering.py::filter_orders` |
| `test` | 24 | 文件 + 用例名 | `tests/test_filter_amount.py::test_min_amount_keeps_orders_at_or_above_the_bound` |
| `doc` | 1 | 文件 | `README.md` |

表结构（`init` 命令创建）：

```sql
create table if not exists sdd_document (
    doc_id       varchar(320) not null,   -- 稳定主键，如 spec:SPEC-ORD-003:R2
    kind         varchar(16)  not null,   -- spec / code / test / doc
    source_path  varchar(512) not null,
    symbol       varchar(255) not null default '',
    locator      varchar(512) not null default '',
    title        varchar(512) not null default '',
    applies_to   varchar(512) not null default '',  -- 规格适用的符号
    ref_symbols  varchar(512) not null default '',  -- 该条目引用/覆盖的符号
    tags         varchar(512) not null default '',
    body         mediumtext   not null,            -- 条目原文
    search_text  mediumtext   not null,            -- 归一化后的检索文本
    meta         text         not null,            -- JSON：版本、规则号等
    content_hash char(64)     not null,
    updated_at   timestamp    not null default current_timestamp on update current_timestamp,
    primary key (doc_id),
    key idx_kind (kind),
    key idx_applies_to (applies_to),
    key idx_symbol (symbol)
) default charset = utf8mb4;

alter table sdd_document add fulltext index ft_sdd_search (search_text) with parser ik;
```

`search_text` 是归一化后的文本：CJK 逐字拆开（`金额筛选` → `金 额 筛 选`），
标识符按下划线拆分（`filter_orders` → `filter orders`）。这样即使数据库只提供英文分词器，
中文规格也能被命中。

## 2. 查了什么

一次 `context` 命令依次执行 7 条 SQL。下面的语句来自实际运行（`db_queries` 字段）：

```sql
-- ① 任务文本里的标识符，哪些是真实符号
select distinct symbol from `sdd_context`.`sdd_document`
 where symbol in ('orderbook','filter_orders','min_amount','max_amount','Decimal','ValueError', ...);

-- ② 适用于该符号的规格条目
select ... from `sdd_context`.`sdd_document`
 where kind = 'spec' and (applies_to like '%filter_orders%')
 order by source_path, symbol limit 20;

-- ③ 需要修改的代码符号
select ... from `sdd_context`.`sdd_document`
 where kind = 'code' and (symbol = 'filter_orders')
 order by source_path, symbol limit 20;

-- ④ 覆盖该符号的测试
select ... from `sdd_context`.`sdd_document`
 where kind = 'test' and (find_in_set('filter_orders', ref_symbols) > 0)
 order by source_path, symbol limit 20;

-- ⑤⑥⑦ 全文排序：规格 / 代码 / 测试各一次
select ..., match(search_text) against ('+orderbook +项 +目 +中 +为 +filter +orders +增 +加 +金 +额 +区 +间 +筛 +选 ...') as score
  from `sdd_context`.`sdd_document`
 where match(search_text) against ('...') and kind = 'spec'
 order by score desc limit 40;
```

**查询结果直接决定上下文内容**：上面 ①②③④ 的命中项是「必须出现」的来源，
⑤⑥⑦ 的命中项按相关度补齐并排序。模块不在 Python 里重做匹配。

## 3. 全文索引确实被使用

对第 ⑤ 类语句做 `EXPLAIN`：

```
============================================================================
|ID|OPERATOR             |NAME                       |EST.ROWS|EST.TIME(us)|
----------------------------------------------------------------------------
|0 |TOP-N SORT           |                           |14      |2121        |
|1 |└─TEXT RETRIEVAL SCAN|sdd_document(ft_sdd_search)|30      |2098        |
============================================================================
  1 - output([...]), filter([sdd_document.kind = 'spec']), rowset=256
      access([...]), partitions(p0)
      is_index_back=true, calc_relevance=true,
      match_expr(MATCH(sdd_document.search_text) AGAINST('+filter +orders +金 额')),
      pushdown_match_filter(MATCH(sdd_document.search_text) AGAINST('+filter +orders +金 额'))
```

执行计划走的是 `TEXT RETRIEVAL SCAN`（原生全文索引），不是全表扫描后在 Python 里算匹配。

## 4. 导入日志

`import` 是幂等的，每次导入都在 `sdd_import_log` 留下一条：

```
| batch_id             | imported_at           | doc_count | kind_counts                                   |
| 20261114-164558-959  | 2026-11-14 16:45:59   | 47        | {"code": 7, "doc": 1, "spec": 15, "test": 24}  |  ← 规格更新后导入
| 20261114-164601-165  | 2026-11-14 16:46:01   | 46        | {"code": 7, "doc": 1, "spec": 14, "test": 24}  |  ← 规格还原后导入
```

`spec` 从 14 变成 15 再变回 14，说明规格更新 → 重新导入 → 上下文变化这条链路
是真的走数据库，而不是模块里写死的答案。

## 5. 复现方式

```bash
cd minimax
python -m sdd_context init       # 建表 + 建全文索引（输出里会显示实际使用的解析器）
python -m sdd_context import     # 导入工程资料
python -m sdd_context stats      # 各类文档数量、全文索引、导入日志
python -m sdd_context context --task-file validation/sample_task.json --json | head -40
```

`context --json` 的输出里带 `db_queries`，可以逐条核对上面记录过的 SQL。
