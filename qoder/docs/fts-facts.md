# 本机实测事实：seekdb 中文全文索引（2026-11-15 实测，评审方提供）

> 这是在 **本机 seekdb `5.7.25-OceanBase seekdb-v1.4.0.0`**（`127.0.0.1:2882`）上
> 真实执行 SQL 得到的结论，不是推测。技术设计文档 2.3.2 节里的假设与实测不符，
> 请以本文件为准，并据此修正设计文档相应章节。

## 1. 默认解析器是 `space`，中文无法命中

不写 `WITH PARSER` 时，seekdb 的默认全文解析器是 `space`：

```sql
CREATE TABLE t (id BIGINT PRIMARY KEY AUTO_INCREMENT, content TEXT,
  FULLTEXT KEY ft (content));
-- SHOW CREATE TABLE t =>
--   FULLTEXT KEY `ft` (`content`) WITH PARSER space
--     PARSER_PROPERTIES=(min_token_size=3,max_token_size=84)
```

`space` 按空白切词，而连续汉字之间没有空白，一整段中文会变成**一个超长 token**
（超过 `max_token_size=84` 后直接被丢弃）。因此中文全文检索**一条都命中不了**。

## 2. `ik` 是词级切分，查询串**不会**被服务端切分

```sql
CREATE TABLE p_ik (id BIGINT PRIMARY KEY, zh TEXT,
  FULLTEXT KEY ft (zh) WITH PARSER ik);
-- SHOW CREATE TABLE p_ik =>
--   FULLTEXT KEY `ft` (`zh`) WITH PARSER ik PARSER_PROPERTIES=(ik_mode="smart")
```

实测（样例笔记 8 篇入 `p_ik`）：

| 查询串 | 命中 |
| --- | --- |
| `对比` | ✅ 命中 |
| `学习` | ✅ 命中 |
| `图` | ✅ 命中（单字也进索引） |
| `对比学习` | ❌ 0 条 |
| `检索增强` | ❌ 0 条 |
| `图神经网络` | ❌ 0 条 |
| `比学` | ❌ 0 条（不是 ik 词典里的词） |
| `+对比 +学习` | ✅ 命中 |
| `对比 学习` | ✅ 命中 |
| `"对比学习"`（短语） | ❌ 0 条 |
| `%对比学习%` | ❌ 0 条 |

**结论**：`ik` 侧是**词级**索引，而**服务端不会切分查询串**——多字连续汉字会被当成
**一个** term 去查词典，查不到就 0 条。所以「按空格拆成多个必需词项」才有效。

## 3. `ngram` 是滑动二元索引，配合**应用侧二元 AND** 才是正解

```sql
CREATE TABLE p_ngram (id BIGINT PRIMARY KEY, zh TEXT,
  FULLTEXT KEY ft (zh) WITH PARSER ngram);
-- SHOW CREATE TABLE p_ngram =>
--   FULLTEXT KEY `ft` (`zh`) WITH PARSER ngram PARSER_PROPERTIES=(ngram_token_size=2)
```

`ngram` 索引里确实存在跨词边界的滑动二元词元：

| 查询串 | 命中 | 说明 |
| --- | --- | --- |
| `比学` | ✅ | 跨「对比|学习」词边界的滑动二元 |
| `图神` | ✅ | |
| `索增` | ✅ | 跨「检索|增强」词边界 |
| `经网` | ✅ | |

**应用侧把连续汉字切成滑动二元串，再用 `+` 做 AND**，即可得到「连续子串」语义：

| 用户输入 | 构造出的 MATCH 表达式 | 实测命中（样例 8 篇） |
| --- | --- | --- |
| 对比学习 | `+对比 +比学 +学习` | `[p01]` ✅ 精确 |
| 检索增强 | `+检索 +索增 +增强` | `[p04, p05]` ✅ 精确 |
| 图神经网络 | `+图神 +神经 +经网 +网络` | `[p01, p02]` ✅ |
| 大语言模型 | `+大语 +语言 +言模 +模型` | `[p03, p04, p07]` ✅ |
| 持续学习 | `+持续 +续学 +学习` | `[p07]` ✅ 精确 |
| 分子性质 | `+分子 +子性 +性质` | `[p01]` ✅ 精确 |
| 可持续 | `+可持 +持续` | `[]`（语料里确实没有，符合预期） |

`EXPLAIN` 确认走的是原生索引：

```
|1 |└─TEXT RETRIEVAL SCAN|s(ft)|8       |264         |
      calc_relevance=true, match_expr(MATCH(s.content) AGAINST(...)), 
      pushdown_match_filter(MATCH(s.content) AGAINST(...))
```

英文一切正常（`contrastive` / `+contrastive +molecular` 均命中），所以问题只在中文切分。

## 4. 因此实现要求

1. **建索引用 `WITH PARSER ngram`**（`ngram_token_size=2`）。若探测到别的解析器不可用，
   可以回退，但**回退后中文检索必须走 LIKE 并如实标注 `matched_by='like'`**，
   不能谎称是全文命中。
2. **查询表达式在应用侧构造**：把连续汉字切成滑动二元串（长度 2），用 `+` 连接成 AND；
   零命中时降级为二元 OR（提高召回），再零命中才退化为 `LIKE`。
3. **英文/数字词**保持原样作为独立 term，用 `+` 与二元串一起 AND。
4. `init-db` / `search --explain` 要打印**实际生效的解析器**与**实际执行的表达式**，
   便于评审核对「原生全文索引确实被使用」。
5. 把以上结论回写到技术设计文档的 2.3.1 / 2.3.2 / 2.3.3 节，替换掉原来
   「`ngram/beng/ik` 会把 `%` 当标点忽略并切出中文 token，因此可命中」这句错误描述。
