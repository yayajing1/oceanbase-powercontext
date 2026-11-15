# 研读台 PaperDesk — 技术设计

> 本文是阶段 1「需求拆解」的产出，只做设计，不含业务代码。
> 目标：把 PRD 的 F1~F6 翻译成可实现的模块边界、数据模型、记忆模型和完整数据链路。
> 技术底线：**seekdb 存「资料」，PowerContext 存「关于你的记忆」**；检索必须是数据库内查询。

---

## 0. 设计原则

1. **数据库负责「找得到」**：全文匹配与结构化过滤都下推到 seekdb，Python 只做归一化、编排和展示，不把全表拉回内存再算匹配。
2. **PowerContext 负责「记得你」**：用户画像、复习历史、薄弱点以 PowerContext 的记忆项落盘，进程重启后读回，并显式影响检索、卡片、复习、提纲四处输出。
3. **可离线运行**：没有模型 API 时，导入、检索、卡片手工填写、复习、提纲全部可用；模型只是卡片生成的增强项。
4. **可被独立验收**：表名、字段、CLI 子命令、落盘位置都按 `qoder/validation/acceptance_check.py` 的检查方式设计，不依赖项目自造的命令命名。
5. **用户产出不可被导入覆盖**：重新导入只替换「资料」（文档与块），卡片是用户产出，采用快照 + 失效标记，绝不级联删除。
6. **纯函数优先**：切块、归一化、复习算法、提纲渲染都是不依赖数据库的纯函数，便于单元测试。

### 0.1 总体架构

```
                 ┌──────────────────────────────────────────┐
                 │                CLI (paperdesk)           │
                 │ init-db / import / search / card /       │
                 │ review / outline / memory / demo         │
                 └───────────────┬──────────────┬───────────┘
                                 │              │
             ┌───────────────────▼───┐    ┌─────▼─────────────────────┐
             │       seekdb           │    │  PowerContext 1.2.0       │
             │  「资料」               │    │  「关于你的记忆」           │
             │  documents / chunks    │    │  SQLite 文件后端            │
             │  FULLTEXT + B-tree     │    │  .state/powercontext.db    │
             │  卡片与复习调度字段     │    │  profile / reviews /      │
             └───────────────────────┘    │  weakness / task outcome  │
                                          └───────────────────────────┘
```

**分工边界（评审要点）**：

| 数据 | 存放位置 | 理由 |
| --- | --- | --- |
| 文献原文、元数据、块、全文索引 | seekdb | 需要 SQL 全文检索与结构化过滤，需要看执行计划 |
| 研究卡片正文与当前调度字段 | seekdb | 卡片是「资料」的一部分，需要可检索、可聚合 |
| 用户画像 | PowerContext | 跨会话、关于用户本人，不属于任何一篇文献 |
| 复习评分历史与下次复习时间 | PowerContext（seekdb 保留当前调度镜像） | 跨会话「记得你的进度」 |
| 薄弱点/低分主题 | PowerContext | 由复习历史推导出的用户状态 |
| 复习事件审计 | seekdb `review_events` | 本机调试与统计；丢失不影响跨会话记忆 |

---

## 1. 模块划分

### 1.1 建议的包结构

```
qoder/
├── paperdesk/
│   ├── __init__.py          # 版本号、包级常量
│   ├── __main__.py          # python -m paperdesk 入口
│   ├── config.py            # 环境变量与默认值、路径解析
│   ├── db.py                # pymysql 连接、建表、全文解析器探测、事务、EXPLAIN
│   ├── models.py            # 纯数据类：NoteMeta / ChunkDraft / SearchHit / Card / ReviewState
│   ├── textnorm.py          # 文本归一化、MATCH 表达式构造、标签/作者切分
│   ├── chunking.py          # front matter 解析、Markdown 分节、限量切块、行号/偏移回溯
│   ├── importer.py          # 文件发现、指纹、幂等写入、过期块清理、目录级 prune
│   ├── search.py            # 混合检索 SQL、多级 MATCH 回退、画像加权、EXPLAIN
│   ├── llm.py              # 可插拔模型接口（OpenAI 兼容 HTTP，可选）
│   ├── cards.py             # 卡片 CRUD、从块生成/手工填写、来源快照
│   ├── review.py            # 复习队列、SM-2 变体调度、评分落库
│   ├── memory.py            # PowerContext 记忆读写、画像/复习/薄弱点、回灌数据库
│   ├── outline.py           # 按标签/年份/作者聚合、Markdown 提纲渲染
│   └── cli.py               # argparse 子命令、输出格式、退出码
├── tests/
│   ├── conftest.py
│   ├── unit/                # 不依赖数据库
│   └── e2e/                 # 依赖 seekdb，按环境变量跳过
├── validation/
│   ├── acceptance_check.py  # 评审方提供，不改
│   └── run_validation.py    # 项目端到端脚本
├── requirements.txt
├── .env.example
├── .gitignore
└── README.md
```

### 1.2 模块职责与对外入口

| 模块 | 职责 | 对外函数/类 | 对应 CLI |
| --- | --- | --- | --- |
| `config.py` | 读取 `PAPERDESK_*` 环境变量和可选 `.env`；统一默认值；解析 `qoder/.state` 绝对路径 | `Config.from_env() -> Config`；`Config.state_db` / `scheduler_db` | 全局 |
| `db.py` | 连接 seekdb；`init_schema()`；全文解析器探测；事务上下文；`explain()`；SQL 日志 | `Database(config)`；`.connect()`；`.init_schema() -> SchemaReport`；`.transaction()`；`.explain(sql, args)` | `init-db` |
| `models.py` | 纯 dataclass，不依赖 DB/SDK | `NoteMeta`、`ChunkDraft`、`SearchHit`、`SearchFilters`、`Card`、`ReviewState`、`MemorySnapshot` | — |
| `textnorm.py` | NFKC/大小写/全半角/标点归一化；中英文加空格；构造布尔 MATCH 候选；标签去重；作者切分 | `normalize_text(text)`；`build_match_candidates(query)`；`split_tags(raw)`；`split_authors(raw)`；`normalize_tag(tag)` | 被 `search`/`import` 调用 |
| `chunking.py` | front matter；按标题层级分节；超长按段落切；单块硬上限；行号与字符偏移 | `parse_note(text, rel_path) -> ParsedNote`；`chunk_note(parsed, max_chars) -> list[ChunkDraft]` | 被 `import` 调用 |
| `importer.py` | 扫描文件、全文件 sha256、文档/标签/作者/块写入、过期块删除、目录 prune | `import_paths(db, paths, max_chars, prune) -> ImportReport` | `import` |
| `search.py` | 归一化查询；一条 SQL 内同时 MATCH + 过滤；多级回退；画像加权；`--explain` | `search(db, query, filters, limit, memory, explain) -> SearchResponse`；`build_search_sql(...)`（纯函数） | `search` |
| `llm.py` | 可插拔模型；未配置 Key 时返回 `None` 而不是空文本 | `ModelClient.from_env()`；`.generate_card(chunk, profile) -> CardDraft | None` | `card add --generate` |
| `cards.py` | 卡片固定字段校验、来源快照、标签/作者冗余、CRUD、卡片内检索 | `create_card(...)`；`list_cards(...)`；`get_card(...)`；`update_card(...)`；`delete_card(...)`；`search_cards(...)` | `card add/list/show/edit/delete/search` |
| `review.py` | 入队、今日队列、评分、SM-2 变体（纯函数） | `compute_next_review(score, state, now) -> ReviewState`（纯）；`enqueue(...)`；`due_queue(...)`；`score_card(...)` | `review add/queue/score` |
| `memory.py` | 三类记忆的读写；Scope 幂等；Source 版本；Task Outcome；回灌卡片调度 | `MemoryStore(config)`；`.load_profile()`；`.save_profile(...)`；`.load_reviews()`；`.save_review(...)`；`.load_weakness()`；`.recompute_weakness()`；`.sync_to_cards(db)`；`.show()`；`.reset()` | `memory show/set-profile/sync/reset` |
| `outline.py` | 聚合查询 + Markdown 渲染（渲染为纯函数） | `build_outline(db, by, filters, profile) -> OutlineDoc`；`render_markdown(doc) -> str`（纯） | `outline` |
| `cli.py` | argparse 子命令；统一 `--json`；退出码约定 | `main(argv) -> int` | 全部 |

### 1.3 CLI 入口与输出契约

运行方式（评审按此执行）：

```bash
cd qoder
python -m paperdesk --help
```

子命令（`--help` 中必须全部出现，验收脚本会检查）：

```text
init-db   初始化表与全文索引，探测中文解析器
import    导入 Markdown/纯文本笔记目录或文件
search    混合检索（全文 + 年份/标签/作者）
card      研究卡片：add/list/show/edit/delete/search
review    复习：add/queue/score
outline   综述提纲：按 tag/year/author 聚合
memory    记忆：show/set-profile/sync/reset
demo      可选：一条命令跑演示链路
```

主要参数：

| 命令 | 输入 | 输出 |
| --- | --- | --- |
| `init-db [--force-rebuild]` | 连接参数 | 表、索引、解析器、`ngram_token_size`；已存在则幂等 |
| `import <path...> [--max-chars 800] [--prune/--no-prune] [--json]` | 文件或目录 | 文件数/新增块/更新块/删除块/跳过数 |
| `search "<query>" [--year-from Y] [--year-to Y] [--tag T]... [--author A] [--limit 10] [--explain] [--no-memory] [--profile-filter] [--json]` | 查询与过滤 | 片段正文、`source_path`、`start_line-end_line`、`score`、`matched_by`、`memory_boost` |
| `card add --chunk <id> [--problem ... --method ... --conclusion ... --relation ...] [--generate] [--yes]` | 来源块 + 四字段 | 卡片 id；无模型时明确提示并转手工 |
| `card list [--queue] [--json]` / `card show <id>` / `card edit <id> --field ...` / `card delete <id> [--yes]` / `card search "<kw>"` | 卡片 | 卡片详情与来源 |
| `review add <card_id>` / `review queue [--today] [--json]` / `review score <card_id> <0-5> [--note ...]` | 卡片 id、评分 | 队列、掌握度、下次复习时间、记忆来源 |
| `outline --by tag|year|author [--tag T]... [--year-from Y] [--year-to Y] [--out file.md]` | 聚合维度 | Markdown 提纲 |
| `memory show [--json]` / `memory set-profile ...` / `memory sync` / `memory reset [--yes]` | 记忆 | 原始记忆 + 来源（scope/source/revision）+ 生效点 |
| `demo [--reset]` | — | 导入→检索→卡片→复习→重启读回→提纲 的演示摘要 |

**退出码约定**（端到端脚本依赖）：

| 码 | 含义 |
| --- | --- |
| 0 | 成功 |
| 2 | 配置/数据库/初始化失败（含连接失败的可操作提示） |
| 3 | 缺少模型且未提供完整手工字段 |
| 4 | 指定的块/卡片不存在 |
| 5 | 端到端断言失败（仅 `run_validation.py`） |

默认人类可读输出；`--json` 输出稳定字段，便于脚本断言。所有「来自记忆」的内容必须在文本和 JSON 中用 `memory` 字段标注。

---

## 2. seekdb 数据模型

### 2.1 表清单与 ER 关系

```
documents 1──N chunks
documents 1──N document_tags
documents 1──N document_authors
chunks 1──N study_cards（逻辑引用，不做级联删除）
study_cards 1──N card_tags
study_cards 1──N card_authors
study_cards 1──N review_events（本机审计，可选）
schema_meta（键值：schema 版本、全文解析器、ngram 参数）
```

表名特意使用 `chunks` 与 `study_cards`：`show tables` 的字母序中 `chunks` 排在卡片表之前，验收脚本定位全文索引时会稳定落在 `chunks` 上；卡片表通过 `problem/method/conclusion/next_review_at` 被单独识别。

### 2.2 DDL 设计

字符集统一 `utf8mb4`；引擎 `InnoDB`；所有时间字段存本地时区的 `DATETIME`（见取舍 8.16）。

#### `schema_meta`

```sql
CREATE TABLE IF NOT EXISTS schema_meta (
  meta_key   VARCHAR(64)  NOT NULL,
  meta_value VARCHAR(512) NOT NULL,
  updated_at DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP
                          ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (meta_key)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;
```

写入：`schema_version=1`、`fulltext_parser=ik|beng|ngram|default`、`ngram_token_size=2`、`last_import_at`。

#### `documents`

```sql
CREATE TABLE IF NOT EXISTS documents (
  document_id   CHAR(32)      NOT NULL,           -- md5(rel_path)
  path_hash     CHAR(64)      NOT NULL,           -- sha256(rel_path)
  source_path   VARCHAR(1024) NOT NULL,           -- 绝对或项目相对路径
  rel_path      VARCHAR(512)  NOT NULL,
  import_root   VARCHAR(1024) NOT NULL,           -- 目录级 prune 用
  title         VARCHAR(512)  NOT NULL,
  authors_text  VARCHAR(512)  NOT NULL DEFAULT '',
  tags_text     VARCHAR(512)  NOT NULL DEFAULT '',
  pub_year      SMALLINT      NULL,
  content_hash  CHAR(64)      NOT NULL,           -- 整个文件的 sha256
  file_size     INT           NOT NULL DEFAULT 0,
  mtime         DATETIME      NULL,
  imported_at   DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at    DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP
                              ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (document_id),
  UNIQUE KEY uk_path_hash (path_hash),
  KEY idx_pub_year (pub_year),
  KEY idx_import_root (import_root(191))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;
```

`authors_text` / `tags_text` 只用于展示和 `LIKE` 诊断；真正的过滤走下面的两张关联表。

#### `document_tags` / `document_authors`

```sql
CREATE TABLE IF NOT EXISTS document_tags (
  document_id CHAR(32)     NOT NULL,
  tag         VARCHAR(128) NOT NULL,
  PRIMARY KEY (document_id, tag),
  KEY idx_tag_doc (tag, document_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

CREATE TABLE IF NOT EXISTS document_authors (
  document_id CHAR(32)     NOT NULL,
  author      VARCHAR(128) NOT NULL,
  PRIMARY KEY (document_id, author),
  KEY idx_author_doc (author, document_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;
```

- 标签过滤是 **OR**：`EXISTS (... AND dt.tag IN (...))`，走 `idx_tag_doc`。
- 作者默认精确匹配；`--author-contains` 才用 `LIKE '%x%'`（小表，可接受全扫描），默认路径能用索引。

#### `chunks`

```sql
CREATE TABLE IF NOT EXISTS chunks (
  chunk_id      CHAR(32)  NOT NULL,        -- md5(document_id + content_hash + occurrence)
  document_id   CHAR(32)  NOT NULL,
  ordinal       INT       NOT NULL,        -- 文档内顺序，仅用于展示/稳定排序
  occurrence    INT       NOT NULL,        -- 同文档内相同内容的第几次出现
  heading_path  VARCHAR(512) NOT NULL DEFAULT '',
  content       TEXT      NOT NULL,        -- 块正文（不含标题行）
  search_text   TEXT      NOT NULL,        -- title + heading_path + content，用于跨字段检索
  content_hash  CHAR(64)  NOT NULL,
  start_line    INT       NOT NULL,
  end_line      INT       NOT NULL,
  start_offset  INT       NOT NULL,        -- 文件内字符偏移，硬切时仍可精确定位
  end_offset    INT       NOT NULL,
  char_count    INT       NOT NULL,
  created_at    DATETIME  NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at    DATETIME  NOT NULL DEFAULT CURRENT_TIMESTAMP
                          ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (chunk_id),
  UNIQUE KEY uk_doc_ordinal (document_id, ordinal),
  KEY idx_doc (document_id),
  KEY idx_doc_occurrence (document_id, content_hash, occurrence)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;
```

说明：

- `chunk_id` 采用**内容寻址**：同一文档中内容不变的块，即使前面插入/删除了别的块，`chunk_id` 也保持不变，卡片不会因为块序号变化而指错来源。`occurrence` 解决同文档内完全重复段落的 id 冲突。
- 唯一定位仍用 `(document_id, ordinal)`：重复导入时按序更新，最后删除本文件多余的 ordinal，保证「旧块消失」。
- `start_line/end_line` 给用户看；`start_offset/end_offset` 给硬切块提供精确到字符的定位。
- 两张全文索引在表创建后单独 `ALTER`，因为解析器是运行时探测的。

#### `study_cards`

```sql
CREATE TABLE IF NOT EXISTS study_cards (
  card_id          CHAR(32)  NOT NULL,
  source_chunk_id  CHAR(32)  NULL,          -- 逻辑引用，不做外键级联
  problem          TEXT      NOT NULL,
  method           TEXT      NOT NULL,
  conclusion       TEXT      NOT NULL,
  relation         TEXT      NOT NULL,      -- 与我的工作的关系，可为空串但会提示
  card_search_text TEXT      NOT NULL,      -- 四字段 + 来源标题，用于卡片检索

  -- 来源快照：块被重新导入/删除后卡片仍可自洽展示
  source_title        VARCHAR(512) NOT NULL DEFAULT '',
  source_path         VARCHAR(1024) NOT NULL DEFAULT '',
  source_start_line   INT NULL,
  source_end_line     INT NULL,
  source_year         SMALLINT NULL,
  source_tags_text    VARCHAR(512) NOT NULL DEFAULT '',
  source_authors_text VARCHAR(512) NOT NULL DEFAULT '',
  source_chunk_hash   CHAR(64) NULL,
  source_snapshot     TEXT NULL,             -- 创建时的正文摘录（前 500 字符）

  -- 复习调度（seekdb 中的当前镜像；历史在 PowerContext）
  in_queue        TINYINT  NOT NULL DEFAULT 0,
  mastery         DECIMAL(3,1) NULL,         -- 0.0~5.0
  last_score      TINYINT  NULL,
  review_count    INT      NOT NULL DEFAULT 0,
  interval_days   INT      NOT NULL DEFAULT 0,
  ease_factor     DECIMAL(4,2) NOT NULL DEFAULT 2.50,
  next_review_at  DATETIME NULL,
  last_reviewed_at DATETIME NULL,
  created_at      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
                           ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (card_id),
  KEY idx_source_chunk (source_chunk_id),
  KEY idx_due (in_queue, next_review_at),
  KEY idx_source_year (source_year)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;
```

固定语义字段严格按照 PRD：`问题 / 方法 / 结论 / 与我的工作的关系 / 来源片段 id`（`problem/method/conclusion/relation/source_chunk_id`）。其余是调度与来源自洽所需的技术字段。

#### `card_tags` / `card_authors`

```sql
CREATE TABLE IF NOT EXISTS card_tags (
  card_id CHAR(32)     NOT NULL,
  tag     VARCHAR(128) NOT NULL,
  PRIMARY KEY (card_id, tag),
  KEY idx_card_tag (tag, card_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

CREATE TABLE IF NOT EXISTS card_authors (
  card_id CHAR(32)     NOT NULL,
  author  VARCHAR(128) NOT NULL,
  PRIMARY KEY (card_id, author),
  KEY idx_card_author (author, card_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;
```

卡片创建时从来源文档复制标签/作者，保证综述提纲不依赖 chunk 是否还存在。

#### `review_events`（本机审计，可丢）

```sql
CREATE TABLE IF NOT EXISTS review_events (
  event_id       BIGINT    NOT NULL AUTO_INCREMENT,
  card_id        CHAR(32)  NOT NULL,
  score          TINYINT   NOT NULL,
  reviewed_at    DATETIME  NOT NULL DEFAULT CURRENT_TIMESTAMP,
  next_review_at DATETIME  NOT NULL,
  interval_days  INT       NOT NULL,
  note           VARCHAR(512) NOT NULL DEFAULT '',
  PRIMARY KEY (event_id),
  KEY idx_card_reviewed (card_id, reviewed_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;
```

> 这不是记忆的权威副本。跨会话的复习历史以 PowerContext 为准；此表只用于本机调试和统计，删库不影响 A5/A6。

### 2.3 全文索引与中文可检索

#### 2.3.1 索引建法

建表时**不写** `FULLTEXT`，由 `db.init_schema()` 在运行时探测解析器后执行：

```sql
-- 解析器按 ik → beng → ngram → 默认 的顺序尝试
ALTER TABLE chunks
  ADD FULLTEXT INDEX ft_chunks_content (content) WITH PARSER ik;

ALTER TABLE chunks
  ADD FULLTEXT INDEX ft_chunks_search (search_text) WITH PARSER ik;

ALTER TABLE study_cards
  ADD FULLTEXT INDEX ft_cards_search (card_search_text) WITH PARSER ik;
```

- 若 `ik` 不存在则整体换 `beng`，再换 `ngram`；三者都不可用时退化为不带 `WITH PARSER`，同时在 `schema_meta` 记录 `default` 并在 `init-db` 输出醒目警告。
- 实际使用的解析器写入 `schema_meta.fulltext_parser`，`init-db` / `search --explain` / `memory show` 都会显示来源。
- `ngram` 情况下读取 `SHOW VARIABLES LIKE 'ngram_token_size'`（通常为 2）并记录，查询表达式按该值调整（见 2.3.3）。
- `ft_chunks_content` 是**主检索索引**；`ft_chunks_search` 让标题/章节名也参与匹配。两个索引都在 `chunks` 上，验收脚本用 `MATCH(content)` 与 `MATCH(search_text)` 都能命中。

#### 2.3.2 为什么中文能命中

中文没有空格，默认解析器会把整段连续汉字当成一个 token，查询「对比学习」无法命中「图对比学习在分子性质预测中的应用」。因此：

1. **服务端**：优先使用中文感知解析器 `ik/beng/ngram`。`ik` 做词级切分；`beng` 与 `ngram` 做字级/二元切分，保证任意中文子串可被切出。
2. **应用侧归一化**：查询与索引文本都经过 NFKC、全半角统一、英文小写、标点转空格、中英文之间插入空格，避免 `%`、`（）`、`，` 等把查询切成非法布尔表达式。
3. **查询表达式**：把用户输入拆成词项，用布尔模式短语/必需项构造，例如 `+"对比学习"`；中文多词查询用 `+"图神经网络" +"对比学习"`（AND），零命中时退化为 OR，再退化为数据库内 `LIKE`（标记为 `matched_by='like'`，不是主路径）。
4. **验收口径**：`acceptance_check.py` 会直接执行 `MATCH(content) AGAINST('%对比学习%' IN BOOLEAN MODE)`。`ngram/beng/ik` 会把 `%` 当标点忽略并切出中文 token，因此在 seekdb v1.4.0 上可命中；`init-db` 必须显式打印最终解析器，便于评审核对。

#### 2.3.3 归一化与 MATCH 候选（纯函数）

```python
# textnorm.py 设计，不是实现
def normalize_text(text: str) -> str:
    # NFKC；英文小写；全角标点→空格；保留汉字/字母/数字；
    # 连续空白折叠；汉字与 ASCII 之间插空格
    ...

def build_match_candidates(query: str) -> list[str]:
    # 1) 短语 AND： +"对比学习"
    # 2) 词项 AND： +对比学习
    # 3) 词项 OR：  对比学习
    # 每个候选都先去除布尔元字符（+ - @ " ( ) ~ * < >），并做 % 转义
    ...
```

多级策略只在「MATCH 命中数为 0」时继续尝试，**每一级仍然是一条包含结构化过滤的 SQL**，不会把数据拉到应用层。

#### 2.3.4 混合检索 SQL（核心）

关键词 + 结构化条件在同一条 SQL：

```sql
SELECT
  c.chunk_id,
  c.content,
  c.heading_path,
  c.start_line,
  c.end_line,
  d.source_path,
  d.title,
  d.pub_year,
  d.authors_text,
  d.tags_text,
  MATCH(c.search_text) AGAINST (%(match_expr)s IN BOOLEAN MODE)
    + CASE WHEN EXISTS (
        SELECT 1 FROM document_tags dt2
        WHERE dt2.document_id = d.document_id
          AND dt2.tag IN (%(profile_tags)s)
      ) THEN %(profile_boost)s ELSE 0 END AS score,
  'fts' AS matched_by
FROM chunks c
JOIN documents d ON d.document_id = c.document_id
WHERE MATCH(c.search_text) AGAINST (%(match_expr)s IN BOOLEAN MODE)
  AND (%(year_from)s IS NULL OR d.pub_year >= %(year_from)s)
  AND (%(year_to)s   IS NULL OR d.pub_year <= %(year_to)s)
  AND (%(has_tags)s = 0 OR EXISTS (
        SELECT 1 FROM document_tags dt
        WHERE dt.document_id = d.document_id
          AND dt.tag IN (%(tag_placeholders)s)
      ))
  AND (%(has_author)s = 0 OR EXISTS (
        SELECT 1 FROM document_authors da
        WHERE da.document_id = d.document_id
          AND da.author = %(author)s
      ))
ORDER BY score DESC, d.pub_year DESC, c.chunk_id
LIMIT %(limit)s;
```

- 结构化条件全部是普通 `WHERE` + 关联表，与 MATCH 互不干扰；A4 用「年份 + 标签 + 作者 + 关键词」同时验证。
- 画像加权在 SQL 中用 `CASE EXISTS` 完成，不在 Python 里重排；命中结果的 `memory_boost` 字段来自该表达式。
- 无关键词、只有过滤条件时执行 `matched_by='filter'` 的查询：`score=0.0`，按年份/标题排序。默认不把「过滤命中」混进关键词结果；需要时用 `--include-filter-only` 显式 UNION。
- `--explain` 打印：实际 SQL（参数已内联用于阅读）、`EXPLAIN <sql>`、`SHOW INDEX FROM chunks`、当前解析器。验收 A2 直接看这段输出。

#### 2.3.5 索引清单与预期执行计划

| 表 | 索引 | 用途 |
| --- | --- | --- |
| `chunks` | `FULLTEXT ft_chunks_content(content)` | 主全文匹配（A2/A3） |
| `chunks` | `FULLTEXT ft_chunks_search(search_text)` | 标题 + 章节 + 正文联合匹配 |
| `chunks` | `uk_doc_ordinal(document_id, ordinal)` | 幂等更新与顺序展示 |
| `chunks` | `idx_doc_occurrence(document_id, content_hash, occurrence)` | 内容寻址 id |
| `documents` | `uk_path_hash`、`idx_pub_year` | 幂等定位、年份过滤 |
| `document_tags` | `PRIMARY(document_id, tag)`、`idx_tag_doc(tag, document_id)` | 标签 OR 过滤 |
| `document_authors` | `PRIMARY`、`idx_author_doc(author, document_id)` | 作者过滤 |
| `study_cards` | `idx_due(in_queue, next_review_at)` | 今日队列 |
| `study_cards` | `FULLTEXT ft_cards_search(card_search_text)` | 卡片检索 |
| `card_tags` | `idx_card_tag(tag, card_id)` | 按标签聚合提纲 |

预期 `EXPLAIN`：`chunks` 上出现 `fulltext`/`index` 类型（seekdb 对全文索引的显示可能是 `fulltext`），`documents` 走主键，标签/作者子查询走 `idx_tag_doc` / `idx_author_doc`。若 `ngram` 下优化器选择全表扫描，`--explain` 会如实展示并给出 `FORCE INDEX` 的提示开关，但默认不强制。

### 2.4 导入幂等与删除语义

**块身份**：`chunk_id = md5(document_id + content_hash + occurrence)`。

- 同一文件重复导入：内容不变 → `content_hash` 不变 → `chunk_id` 不变；按 `uk_doc_ordinal` 更新行，不新增。
- 文件内容变化：变化的块生成新 `chunk_id`；本次没有出现的旧块按 `document_id` 删除。
- 文件被删除后重新导入所在目录：目录模式默认 `--prune`，删除该 `import_root` 下本次未见到的文档及其块/标签/作者。
- 单文件导入默认不 prune，避免「只导一篇」误删同目录其他文献；需要清理时显式 `--prune`。
- 卡片不级联删除：块消失后卡片保留 `source_*` 快照；`card show` 显示「来源已失效/来源已变更」，并对比 `source_chunk_hash`。

`importer.py` 的写入顺序（每个文件一个事务）：

```
upsert documents（path_hash 唯一）
  → delete + insert document_tags / document_authors
  → 逐块 upsert chunks（uk_doc_ordinal）
  → delete chunks where document_id=? and ordinal >= 本次块数
  → 记录 schema_meta.last_import_at
```

目录级 prune 在全部文件成功导入后执行，避免半途失败导致误删。

---

## 3. PowerContext 使用设计

### 3.1 版本与接入方式

| 项 | 选择 |
| --- | --- |
| 版本 | `powercontext==1.2.0`（PyPI） |
| 接入 | 进程内 Core SDK：`powercontext.builtin.runtime.open_builtin_runtime` |
| 后端 | 自带 SQLite 文件：`qoder/.state/powercontext.db` |
| 调度器 | `qoder/.state/scheduler.db` |
| Scope | 一个固定幂等键：`paperdesk-memory-v1`，title=`PaperDesk 研读台` |
| 重置 | 删除上述两个文件；`memory reset --yes` |

关键 API（与已安装版本一致）：

```python
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import BuiltinConfig, open_builtin_runtime
from powercontext.builtin.runtime.models import CaptureSource
from powercontext.builtin.scope import ScopeDraft
from powercontext.builtin.work import RecordTaskOutcome, TaskCheck, TaskOutcome, WorkClaim
```

### 3.2 为什么用「content Source + Task Outcome」而不是原生 Memory Artifact

PowerContext 1.2.0 同时提供 `runtime.memory.for_scope(...).remember/search/list`。本项目**不把它作为三类记忆的权威存储**，原因：

1. **中文检索**：SQLite 后端的原生 Memory 索引使用 FTS5 `unicode61`，对连续中文的切分不可靠；本项目无法假定评审环境配置了 embedding 模型。
2. **零模型可用**：原生 `search(mode='auto')` 在无向量/模型时会退化，而记忆读回必须在「没有任何模型 API」时也稳定工作。
3. **确定性**：content Source 是追加式日志，`source_id + revision` 可直接核对；`memory show` 能原样打印，评审容易验证「确实落盘、确实读回」。
4. **与已验证路径一致**：同仓库的 `minimax` 项目已用 `Source + TaskOutcome` 在 PowerContext 1.2.0 上跑通跨进程读回。

因此：**Source 是记忆的 system of record，Task Outcome 是结构化旁证**。若后续需要原生 Memory 的语义检索，可作为镜像追加，不改变现有读路径。

### 3.3 记忆项定义

所有记忆都是 JSON 文档，`schema` 字段带版本；`source_id` 固定，重复写入生成 `-v2`、`-v3` 修订。

#### M1 用户画像

`source_id = paperdesk-profile`

```json
{
  "schema": "paperdesk.profile.v1",
  "research_direction": "图表示学习与检索增强生成",
  "frequent_tags": ["图神经网络", "检索增强生成", "对比学习"],
  "current_survey_topic": "图对比学习在分子性质预测中的应用",
  "updated_at": "2026-11-15T10:00:00+08:00"
}
```

- 手工入口：`memory set-profile --direction ... --tags ... --topic ...`
- 自动维护：`card add` 与 `review score` 后，按用户实际使用的卡片标签重算 `frequent_tags`（取最近 90 天卡片标签 Top 5），写新修订。
- 不会因为「导入了一堆某方向论文」就自动画像；画像反映用户**主动建卡/复习**的行为，避免把资料统计误当记忆。

#### M2 复习记录（每张卡片一条）

`source_id = paperdesk-review-<card_id>`

```json
{
  "schema": "paperdesk.review.v1",
  "card_id": "a1b2c3d4e5f60718293a4b5c6d7e8f90",
  "card_title": "图对比学习在分子性质预测中的应用",
  "tags": ["对比学习", "图神经网络"],
  "history": [
    {"at": "2026-11-15T10:03:00+08:00", "score": 3,
     "next_review_at": "2026-11-16T10:03:00+08:00", "interval_days": 1},
    {"at": "2026-11-16T09:00:00+08:00", "score": 4,
     "next_review_at": "2026-11-19T09:00:00+08:00", "interval_days": 3}
  ],
  "mastery": 3.6,
  "next_review_at": "2026-11-19T09:00:00+08:00",
  "last_score": 4,
  "review_count": 2,
  "updated_at": "2026-11-16T09:00:00+08:00"
}
```

- 每张卡片独立 Source，避免多张卡片同时复习时互相覆盖。
- 读回时按 `source_id` 前缀聚合，取每个 card 的最大修订。

#### M3 薄弱点

`source_id = paperdesk-weakness`

```json
{
  "schema": "paperdesk.weakness.v1",
  "topics": [
    {"tag": "持续学习", "avg_score": 1.5, "low_count": 2,
     "card_ids": ["..."], "weight": 0.8},
    {"tag": "可解释性", "avg_score": 2.0, "low_count": 1,
     "card_ids": ["..."], "weight": 0.5}
  ],
  "low_cards": ["..."],
  "updated_at": "2026-11-16T09:00:00+08:00"
}
```

- 每次评分后由 `memory.recompute_weakness()` 全量重算并写新修订；规则见 6.4。
- 薄弱点永远由复习记录派生，不手工编辑；手工干预通过重新评分完成。

#### M4 会话结果（Task Outcome，结构化旁证）

每次 `review score` 或一次 `review queue` 会话结束时：

```python
TaskOutcome(
    objective="PaperDesk 复习会话 2026-11-15",
    status="partial" | "succeeded",
    summary="评了 3 张卡片，2 张仍待复习",
    observations=[WorkClaim(text="score: <card_id>=4"), ...],
    checks=[TaskCheck(name="scored 3 cards", status="passed")],
    remaining_work=["card <id> 下次复习 2026-11-16"],
)
```

通过 `runtime.work.for_scope(scope_id).record_outcome(RecordTaskOutcome(...))` 写入。`memory show` 会展示它，用于证明 PowerContext 不只是键值存储。

### 3.4 写入、版本与落盘

```python
# memory.py 设计，不是实现
def _runtime():
    config = BuiltinConfig(database=SQLiteConfig(
        url=f"sqlite+aiosqlite:///{Config.state_db().as_posix()}"))
    return open_builtin_runtime(config, scheduler_path=str(Config.scheduler_db()))

async def _capture(rt, scope_id, base_id, payload, metadata):
    for v in range(1, 100):
        sid = base_id if v == 1 else f"{base_id}-v{v}"
        try:
            return await rt.sources.for_scope(scope_id).capture(
                CaptureSource(source_id=sid,
                              content=json.dumps(payload, ensure_ascii=False, sort_keys=True),
                              metadata=metadata))
        except SourceConflictError:
            continue           # 已存在同号修订，顺延
    raise MemoryWriteError(...)
```

- 每次公开函数用 `asyncio.run()` 打开 runtime、创建（幂等）Scope、写入、关闭；CLI 是同步的，进程退出即关闭文件。
- Scope 创建：`runtime.scopes.create(ScopeDraft(title="PaperDesk 研读台", summary="文献研读记忆", idempotency_key="paperdesk-memory-v1"))`；同键同摘要返回同一 `scope_id`。
- 读回：`runtime.records.for_scope(scope_id).list_sources(limit=100, cursor=...)` 翻页拿全部 Source；按 `source_id` 的 `-vN` 取最大修订，`json.loads` 后返回；同时在返回值里附 `_powercontext: {scope_id, source_id, position, content_digest}`，供输出标注。
- 落盘保证：写入后文件存在于 `qoder/.state/`，大小随记忆条目增长（profile ~400B、每张卡 ~500B、weakness ~500B、outcome 若干）；`memory reset` 只在 `--yes` 时删除文件。
- `.state/` 写入 `.gitignore`；验收脚本会扫描该目录下的 `.db` 并检查记录数。

### 3.5 读回后在哪里生效

| 流程 | 读取的记忆 | 可见影响 | 输出标注 |
| --- | --- | --- | --- |
| `search` | 画像 | 画像标签加权 `+0.05/命中`；`--profile-filter` 时变成硬过滤 | 头部 `[memory] 画像标签加权: ...`；每条 `memory_boost` |
| `card add` | 画像 | 「与我的工作的关系」预填 `研究方向 / 当前综述主题` 提示；无模型时手工填写界面展示画像 | `[memory] 画像上下文` |
| `review queue` | 复习记录 + 薄弱点 | 启动时把记忆中的 `next_review_at/mastery` 回灌 `study_cards`；直接列出上次的待复习卡片；薄弱主题置顶提示 | `[memory] 上次复习 N 张；薄弱主题: ...` |
| `review score` | 复习记录 + 薄弱点 | 新评分追加到历史；薄弱点重算 | `[memory] 已写入 PowerContext` |
| `outline` | 画像 | 默认标题用 `current_survey_topic`；标签分组顺序按 `frequent_tags` | `[memory] 综述主题/排序来自画像` |
| `memory show` | 全部 | 原样打印 + scope/source/revision/digest | 全部 |

反例：`import` **不读也不写**记忆；`search --no-memory` 不使用画像；`memory show --json` 的 `_powercontext` 字段是诊断信息，不属于记忆内容。

### 3.6 一致性、冲突与丢失

- **双写顺序**：`review score` 先更新 seekdb 的 `study_cards`（当前调度），再写 PowerContext（历史与掌握度），最后写 `review_events`。若 PowerContext 写失败，命令返回非 0 并保留 seekdb 更新，提示「记忆未写入，下次运行可能显示旧进度」；不静默。
- **读回暖启动**：`review queue` 启动时调用 `memory.sync_to_cards(db)`：对每条记忆，若 `memory.updated_at > study_cards.updated_at`，把 `mastery/last_score/review_count/interval_days/ease_factor/next_review_at/last_reviewed_at` 回灌数据库。这样即使有人清空了调度字段，记忆仍能恢复队列。
- **卡片不存在**：记忆中引用但数据库没有的卡片（换机器/删库），不重建卡片，只在 `review queue` 输出「记忆中存在但资料库缺失」列表；不伪造内容。
- **并发**：单用户 CLI；PowerContext 写入用 `SourceConflictError` 顺延修订；seekdb 用事务。不做多进程锁。
- **重置**：`memory reset` 只删记忆文件；`demo --reset` 才清空实验表（需显式 `--yes`）。

---

## 4. 数据流

### 4.1 导入 Markdown 笔记

```
扫描路径（目录递归，扩展名 .md/.markdown/.txt）
  → 读 UTF-8 文本（失败则 UTF-8-BOM/GB18030 回退并提示）
  → 计算文件 sha256；与 documents.content_hash 相同则跳过（快路径）
  → parse_note：
      - 解析 YAML front matter（title/authors/year/tags）
      - 无 front matter：用一级标题作 title，正文首段推断年份/标签为空
      - 按 ATX 标题（#~######）分节，记录 heading_path、行号、字符偏移
      - 去掉 front matter 与纯标题 preamble；单节正文按空行分段
  → chunk_note：
      - 单块 ≤ max_chars（默认 800）
      - 超长先按段落边界切；单段超限再按字符硬切
      - 不重叠；每块带 start_line/end_line/start_offset/end_offset
      - content=正文；search_text=title + heading_path + content
  → 事务写 documents / document_tags / document_authors / chunks
  → 删除该文档本次未出现的旧 ordinal
  → 目录模式下 prune 掉 import_root 下已不存在的文件
  → 输出 ImportReport（文件/新增/更新/删除/跳过）
```

样例数据预期：8 个文件，每篇约 4 个块（标题 preamble 后的问题/方法/结论，或标题并入首节），共约 30 块；同标签「检索增强生成」下有 p04、p05 两篇，用于验证聚合与提纲。

### 4.2 生成研究卡片

```
card add --chunk <chunk_id>
  → 从 chunks JOIN documents 取正文、标题、标签、作者、行号
  → 读画像（PowerContext）
  → 有模型：ModelClient.generate_card(chunk, profile)
        Prompt 要求输出严格 JSON：problem/method/conclusion/relation
        返回空或缺字段 → 视为失败，转手工，不写空卡片
  → 无模型/模型失败：
        打印明确提示「未配置模型 API，请手工填写以下字段」
        TTY：逐项交互输入；非 TTY：必须提供 --problem/--method/--conclusion
        缺少 problem/method/conclusion → 退出码 3，不写库
        relation 允许为空，但输出提示「与我的工作的关系未填写」
  → study_cards 插入固定四字段 + source_* 快照
  → card_tags / card_authors 从来源文档复制
  → 更新画像 frequent_tags（若本次卡片标签进入 Top 5）
  → 输出 card_id、来源路径与行号、记忆来源
```

### 4.3 进入复习队列

```
review add <card_id>
  → 校验卡片存在
  → in_queue=1, next_review_at=NOW()（当天到期）, interval_days=0
  → 输出「已加入队列，今天到期」

review queue [--today]
  → memory.sync_to_cards(db)          # 记忆回灌
  → SELECT ... FROM study_cards
     WHERE in_queue=1 AND next_review_at < 明日 00:00
     ORDER BY next_review_at ASC, card_id
  → 读薄弱点，给命中标签的卡片打 [memory] 标记并置顶建议
  → 输出：今日到期、逾期、未来 7 天；每张卡的 mastery/next_review_at/来源
```

### 4.4 评分与下次复习时间

```
review score <card_id> <0-5> [--note]
  → 读 study_cards 当前调度 + PowerContext 历史
  → compute_next_review(score, state, now)   # 纯函数
  → UPDATE study_cards（mastery/last_score/review_count/interval_days/
                        ease_factor/next_review_at/last_reviewed_at）
  → INSERT review_events（审计）
  → PowerContext 写 paperdesk-review-<card_id> 新修订
  → memory.recompute_weakness() 写 paperdesk-weakness 新修订
  → runtime.work.record_outcome(...) 写会话旁证
  → 输出：本次评分、掌握度、间隔天数、下次复习时间、记忆来源
```

### 4.5 输出综述提纲

```
outline --by tag|year|author [--tag ...] [--year-from ...]
  → 读画像（默认标题=current_survey_topic；标签排序=frequent_tags）
  → SQL 聚合：
      tag:    study_cards JOIN card_tags
      year:   study_cards.source_year
      author: study_cards JOIN card_authors
  → 每组：组名 + 卡片列表（problem 作为标题 + source_path:start-end）
  → 卡片按 source_year DESC, created_at 排序
  → render_markdown（纯函数）
  → stdout 或 --out 写文件
  → 头部标注 [memory] 哪些内容来自画像
```

### 4.6 端到端链路（验收脚本按此执行）

```
[进程 1]
init-db
  → import qoder/data/sample_notes
  → search "对比学习" --year-from 2023 --tag 图神经网络
  → card add --chunk <命中块> --problem ... --method ... --conclusion ...
  → review add <card_id>
  → review score <card_id> 3
  → memory set-profile --direction "..." --tags "..." --topic "..."

[进程 2：重启，验证跨会话]
memory show                 → 画像/复习/薄弱点 + scope/source/digest
review queue                → 上次评分的卡片出现在待复习清单
search "对比学习"            → 画像标签加权可见
outline --by tag            → 提纲标题/排序来自画像
```

---

## 5. 关键算法

### 5.1 分块

- 标题层级：`#` 到 `######` 都视为节边界；`heading_path` 用 ` > ` 连接祖先标题。
- 首段 preamble（一级标题下的导语）若存在，作为独立块并附带 `heading_path=title`；若只有标题无正文则跳过。
- 单块上限 `PAPERDESK_MAX_CHARS`，默认 800。段落切分后仍超限时硬切；硬切的块共享同一 `start_line`，用 `start_offset/end_offset` 精确区分。
- 不重叠：重叠会让重复导入的块数不稳定，也会让卡片来源重复；检索召回不足靠多级 MATCH 与标题/标签联合索引弥补。
- 代码围栏（```）内的 `#` 不当作标题；解析时维护 fence 状态。

### 5.2 复习调度（SM-2 变体）

纯函数签名：`compute_next_review(score: int, state: ReviewState, now: datetime) -> ReviewState`。

```
q = score（0~5）
EF' = max(1.3, EF + (0.1 - (5-q) * (0.08 + (5-q) * 0.02)))
if q == 0:
    repetitions = 0
    interval_days = 0
    due = now + 4 小时          # 当天重来，但避免立刻刷屏
elif q < 3:
    repetitions = 0
    interval_days = 1
    due = now + 1 天
else:
    if repetitions == 0: interval_days = 1
    elif repetitions == 1: interval_days = 3
    else: interval_days = round(interval_days * EF')
    repetitions += 1
    due = now + interval_days 天
mastery = round(0.6 * old_mastery + 0.4 * q, 1)   # 首次直接用 q
```

- 评分越高间隔越长；0 分当天重来；1/2 分次日重来；EF 下限 1.3。
- 测试覆盖：0、1、2、3、4、5 的间隔单调性、0 分当天、连续高分递增、EF 下限。
- 参数集中在 `review.py` 常量，后续可配置，但本轮不引入配置复杂度。

### 5.3 薄弱点计算

```
对每张有复习历史的卡片：
  recent = 最近 3 次评分
  if min(recent) <= 2 and avg(recent) < 3.0:
      该卡片为 low_card
对每个标签：
  low_count = 该标签下 low_card 数
  avg_score = 该标签下所有最近评分的平均
  weight = low_count / (low_count + 1) * (3.0 - avg_score) / 3.0
取 weight 最大的 Top 5 作为 topics
```

- 只有低分才会进入薄弱点；后续连续 4/5 分会把卡片移出 low_card。
- 无复习历史时薄弱点为空，不虚构。

### 5.4 提纲聚合

- 卡片可属于多个标签：在每个标签组下重复出现，但同一组内按 `card_id` 去重。
- 作者同理：一篇多作者会在每个作者组下出现。
- 年份缺失（`source_year=NULL`）归入「年份未知」组，排在最后。
- 每条支撑项格式：`- **<problem 首行/来源标题>** — <source_path>:<start_line>-<end_line>`；来源缺失时显示 `（来源已失效）` + 快照摘录。

---

## 6. 配置、依赖、测试与验收映射

### 6.1 环境变量

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `PAPERDESK_DB_HOST` | `127.0.0.1` | seekdb 主机 |
| `PAPERDESK_DB_PORT` | `2882` | seekdb 端口（MySQL 协议） |
| `PAPERDESK_DB_USER` | `root` | 用户名 |
| `PAPERDESK_DB_PASSWORD` | 空 | 密码；只从环境变量读 |
| `PAPERDESK_DB_NAME` | `paperdesk` | 库名 |
| `PAPERDESK_MAX_CHARS` | `800` | 单块字符上限 |
| `PAPERDESK_STATE_DIR` | `qoder/.state` | PowerContext 落盘目录 |
| `PAPERDESK_SCOPE_KEY` | `paperdesk-memory-v1` | Scope 幂等键 |
| `PAPERDESK_MODEL_API_KEY` | 空 | 可选；空则卡片走手工路径 |
| `PAPERDESK_MODEL_BASE_URL` | `https://api.openai.com/v1` | OpenAI 兼容端点 |
| `PAPERDESK_MODEL_NAME` | 空 | 模型名 |
| `PAPERDESK_PROFILE_BOOST` | `0.05` | 画像标签加权值 |
| `PAPERDESK_TZ` | 系统本地时区 | 复习时间计算 |

`.env.example` 只放占位值（如 `PAPERDESK_MODEL_API_KEY=your-api-key-here`），`.env` 与 `.state/` 写入 `.gitignore`。代码中不出现任何真实 Key/密码。

### 6.2 依赖

```
pymysql>=1.1
powercontext==1.2.0
PyYAML>=6.0
pytest>=8.0
```

不引入 ORM、Web 框架、向量库；模型调用用标准库 `urllib.request`（或可选 `httpx`），避免额外依赖。

### 6.3 测试矩阵

**单元测试（不依赖数据库）**

| 文件 | 覆盖 |
| --- | --- |
| `tests/unit/test_frontmatter.py` | YAML front matter、缺字段、非法年份、标签/作者切分 |
| `tests/unit/test_chunking.py` | 标题分节、heading_path、行号、段落切分、硬切、代码围栏、空节 |
| `tests/unit/test_textnorm.py` | 全半角、标点、中英文空格、布尔元字符转义、MATCH 候选顺序 |
| `tests/unit/test_search_sql.py` | SQL 片段生成：有/无关键词、标签 OR、作者、年份边界、无过滤 |
| `tests/unit/test_review_schedule.py` | SM-2 全评分、0 分当天、间隔递增、EF 下限、mastery 平滑 |
| `tests/unit/test_weakness.py` | 低分聚合、Top 5、恢复后移除 |
| `tests/unit/test_outline.py` | 多标签重复、缺年份、来源失效、Markdown 结构 |
| `tests/unit/test_idempotency.py` | chunk_id 内容寻址、同内容 occurrence、旧块删除计划 |

**端到端（依赖 seekdb，`PAPERDESK_RUN_E2E=1` 时运行）**

| 场景 | 断言 |
| --- | --- |
| 初始化 | 表存在、`chunks` 上有 FULLTEXT、解析器已记录 |
| 导入两次 | 块数不变；再次导入返回 skipped |
| 中文检索 | 「对比学习」命中 p01；出处行号正确 |
| 结构化过滤 | 年份 2023 + 标签「图神经网络」+ 作者「张伟」的组合结果正确 |
| 卡片 | 无模型时退出码 3 且不落库；手工字段后落库 |
| 复习 | 评分 0 当天到期；评分 4 间隔变长；`next_review_at` 非空 |
| 记忆 | 新进程 `memory show` 能读回；`review queue` 显示上次卡片 |
| 提纲 | 按标签聚合含 p04/p05 两张「检索增强生成」卡片 |
| 删除文件 | 目录重导后旧块消失，卡片保留并标记来源失效 |

### 6.4 验收项映射

| 验收 | 设计落点 |
| --- | --- |
| A1 能运行 | `qoder/README.md` 运行说明、`requirements.txt`、`python -m paperdesk --help` |
| A2 seekdb 参与 | `chunks` 原生 FULLTEXT；`search --explain` 打印 SQL/EXPLAIN/SHOW INDEX |
| A3 中文检索 | 解析器探测 + 归一化 + 多级 MATCH；样例中文关键词 |
| A4 结构化过滤 | `pub_year` + `document_tags` + `document_authors` 同条 SQL |
| A5 PowerContext 参与 | `.state/powercontext.db`；`memory show`；搜索/卡片/复习/提纲的 `[memory]` 标注 |
| A6 跨会话 | 新进程 `review queue` 读回 M2；`memory.sync_to_cards` |
| A7 卡片闭环 | `study_cards` 固定字段 + `next_review_at`；`review score` 计算 |
| A8 导入幂等 | 内容寻址 chunk_id + `uk_doc_ordinal` + 旧 ordinal 清理 + 目录 prune |
| A9 无凭据 | 全部走环境变量；`.env.example` 占位；无硬编码 |
| A10 网站 | 后续阶段 `qoder/site/`，本设计不涉及 |

---

## 7. PRD 未写清楚、需要技术取舍的清单

| # | 问题 | 选择 | 理由 | 影响 |
| --- | --- | --- | --- | --- |
| 1 | 中文全文索引用哪个解析器？ | 运行时按 `ik → beng → ngram → 默认` 探测，记录实际值 | seekdb 不同构建包含的解析器不同；硬编码会导致换环境不可用 | `init-db` 多一步探测；`search --explain` 显示解析器 |
| 2 | 全文索引建在哪些列？ | `content`（主）与 `search_text`（标题+章节+正文）双索引 | 既要正文命中，也要标题/章节可检索；验收脚本用 `MATCH(content)` | 写入略增；两张全文索引 |
| 3 | 切块是否重叠？ | 不重叠，段落优先、超长硬切 | 重叠会让幂等块数不稳定、卡片来源重复；召回靠 MATCH 多级与联合索引 | 极长段落可能切断语义；用 `search_text` 与相邻块缓解 |
| 4 | 块 id 用位置还是内容？ | 内容寻址 `md5(doc + content_hash + occurrence)` | 前文插入/删除不会让已有卡片指错块；重复导入天然稳定 | 内容变化会产生新 id；旧卡片保留快照并提示 |
| 5 | 标签/作者怎么存？ | 关联表 `document_tags` / `document_authors`，展示再冗余文本 | 多选 OR、按标签聚合需要索引；逗号文本无法有效过滤 | 导入多两次写；查询多两个 EXISTS |
| 6 | 导入目录时是否删除已消失文件？ | 目录模式默认 `--prune`，单文件默认不 prune | A8 要求删除文件后旧块消失；但单文件导入不应误删同目录 | 用户需知道两种语义；README 写明 |
| 7 | 无关键词时「结构化命中」怎么返回？ | 只跑 filter 查询，`matched_by='filter'`；默认不与关键词结果混合 | 避免稀释相关度；「被谁命中」语义清晰 | 需要 `--include-filter-only` 才合并 |
| 8 | 关键词零命中怎么办？ | 依次尝试短语 AND → 词项 AND → OR → `LIKE`（默认开启，标记 `like`） | 中文解析器差异大；纯 MATCH 可能因表达式不合法漏召回 | `LIKE` 不是索引路径；`--explain` 会暴露并提示 |
| 9 | 画像标签是硬过滤还是加权？ | 默认软加权 `+0.05`；`--profile-filter` 才硬过滤 | 避免画像不完整时把结果过滤空；PRD 允许「过滤/加权」 | 输出显示 `memory_boost`；`--no-memory` 可关 |
| 10 | 无模型时卡片怎么生成？ | 明确提示 + 交互/命令行手工填写；缺 problem/method/conclusion 直接退出码 3 | PRD 明确禁止静默空卡片 | 自动化环境需传四个字段；`relation` 可空但提示 |
| 11 | 卡片来源块被重新导入/删除怎么办？ | 不做外键级联；卡片存来源快照与 hash，失效时标注 | 卡片是用户产出，不能被资料更新毁掉 | `study_cards` 多若干快照列；`card show` 显示来源状态 |
| 12 | 复习算法参数？ | SM-2 变体：0 分 4 小时后重来；1/2 分次日；3+ 按 1/3/EF 递增；EF 下限 1.3 | 满足「评分越高间隔越长、0 分当天重来」，参数少、可测试 | 用户不能自定义间隔；后续可加配置 |
| 13 | 掌握度用最近一次还是平滑？ | EWMA：`0.6*old + 0.4*score`，首次直接用分数 | 单次失误不应把掌握度打到 0；单次高分也不应过度乐观 | 队列显示为一位小数；测试覆盖 |
| 14 | 薄弱点阈值？ | 最近 3 次中存在 ≤2 且均值 <3.0；按权重取 Top 5 | 低分才叫薄弱；连续高分可移出，避免永久标签 | 阈值写死在 `memory.py` 常量；`memory show` 可查看 |
| 15 | 记忆用什么 PowerContext 原语？ | content Source（主）+ Task Outcome（旁证），不用原生 Memory Artifact | 原生 SQLite FTS `unicode61` 中文切分不可靠，且 `auto` 检索依赖模型；Source 确定性好、可核对 | 需要自己做 `-vN` 版本与读最大修订；`memory show` 更直观 |
| 16 | 复习记录按卡片一条还是全局一条？ | 每张卡一个 Source：`paperdesk-review-<card_id>` | 避免多卡同时复习时全局文档冲突；读回按前缀聚合 | Source 数量随卡片增长；读取需翻页 |
| 17 | 复习历史存两处是否重复？ | PowerContext 权威；seekdb `study_cards` 只存当前调度镜像，`review_events` 仅审计 | 队列查询要 SQL 索引；跨会话记忆要 PowerContext；职责不同 | 双写；写明冲突时 PowerContext 优先 |
| 18 | 启动时记忆与数据库谁覆盖谁？ | 以 `updated_at` 较新者为准，记忆回灌 `study_cards` | 满足 A6「重启后队列非空」；避免旧数据库覆盖新记忆 | 时钟偏差可能误判；单机单用户可接受 |
| 19 | 卡片检索用全文还是 LIKE？ | `study_cards.card_search_text` 上建 FULLTEXT，失败时 LIKE | 卡片量小，但全文索引与其他检索一致；A2 定位仍优先 `chunks`（表名字母序） | 多一张全文索引；解析器同源 |
| 20 | 综述提纲的标题/排序从哪来？ | 默认用画像 `current_survey_topic` 与 `frequent_tags` 排序 | PRD 要求记忆影响输出；让提纲体现「用户当前在写什么」 | 无画像时用中性标题「文献综述提纲」 |
| 21 | 时间与时区？ | 数据库存本地时区 `DATETIME`；复习「当天」按 `PAPERDESK_TZ` 计算 | 用户按本地日历复习；跨时区部署罕见 | 换时区需重算；README 说明 |
| 22 | 输出格式与退出码？ | 默认人类可读 + `--json`；退出码 0/2/3/4/5 | 端到端脚本要可断言；评审要可读 | CLI 需要统一 formatter；避免各命令自造格式 |
| 23 | 模型调用怎么实现？ | 可插拔 `ModelClient`，OpenAI 兼容 HTTP，标准库实现；无 Key 返回 None | 不绑定厂商、不引入重依赖；离线也能跑 | 模型输出需 JSON 校验；失败转手工 |
| 24 | 测试数据库怎么隔离？ | 单元测试不连库；E2E 用 `PAPERDESK_DB_NAME` 指定独立库，`init-db` 幂等 | 评审环境只有一个 `paperdesk` 库，不能假设可建库 | E2E 需显式 `PAPERDESK_RUN_E2E=1`；默认跳过 |

---

## 8. 阶段衔接

本设计直接对应后续阶段：

| 阶段 | 依赖本设计的部分 |
| --- | --- |
| 2 数据层与导入 | 2.2 表结构、2.4 幂等、5.1 分块、6.1 配置 |
| 3 混合检索 | 2.3 全文索引/归一化/SQL 模板、3.5 画像加权 |
| 4 卡片与复习 | 2.2 `study_cards`、4.2/4.3/4.4、5.2 SM-2 |
| 5 PowerContext | 3.3 记忆项、3.4 写入/读回、3.5 生效点、3.6 一致性 |
| 6 CLI 与验证 | 1.3 命令契约、4.6 链路、6.3 测试矩阵 |
| 7 站点 | 总体架构与技术分工（seekdb 资料 / PowerContext 记忆） |

**本轮不做**：不创建 `paperdesk/` 业务代码、不改 `qoder/PRD.md`、不改 `qoder/data/` 下任何文件。DDL 与伪代码仅用于表达设计。
