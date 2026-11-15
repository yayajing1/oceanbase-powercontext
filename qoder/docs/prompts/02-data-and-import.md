# 阶段提示词：02-data-and-import.md

> 直接复制下面代码块的内容，粘贴给 Qoder（IDE / CLI 均可）。

```text
按 `qoder/docs/technical-design.md` 的设计开始实现。本轮只做「数据层 + 文献导入」：

0. **先读 `qoder/docs/fts-facts.md`**：那是本机 seekdb（`127.0.0.1:2882`，
   `5.7.25-OceanBase seekdb-v1.4.0.0`）上**实测**出来的中文全文索引结论，
   技术设计文档 2.3.2 节里的假设与实测不符。建全文索引时必须按实测结论来：
   默认 `space` 解析器切不动中文，要用 `WITH PARSER ngram`（`ngram_token_size=2`）。

1. 在 `qoder/paperdesk/` 下建立 Python 包 `paperdesk`，实现：
   - 配置读取（环境变量 + 可选 `.env`），数据库连接与建表（seekdb / OceanBase，MySQL 协议）；
   - 表设计按技术设计文档落地，块字段必须建**原生全文索引**，保证中文关键词能被 MATCH 命中。
2. 实现 Markdown 笔记导入：
   - 解析 YAML front matter（title / authors / year / tags）与正文里的 `## 问题` `## 方法` `## 结论` 小节；
   - 按标题层级切块；没有标题时按段落切块；单块不超过可配置的最大字符数（默认 800）；
   - 记录每块的来源文件与起止行号；
   - **幂等**：同一文件重复导入不产生重复块；文件内容变化后，被删除的旧块必须消失。
3. 写单元测试：front matter 解析、分节、标题层级切块、段落切块、超长块切分、幂等指纹逻辑，
   这些测试**不依赖数据库**（用 pytest，临时目录 + monkeypatch）。
4. 依赖写进 `qoder/paperdesk/requirements.txt`；提供 `qoder/.env.example`，
   连接参数用占位值，不提交任何真实凭据。

约束：
- 数据库默认连 `127.0.0.1:2882`，库名用环境变量 `PAPERDESK_DB_NAME` 覆盖（默认 `paperdesk`）；
- 连接失败要给出可操作的诊断信息（而不是抛裸异常）；
- 这一轮先不要做检索、卡片、复习、记忆、提纲。

完成后说明：建了哪几张表、全文索引的建法、幂等是怎么实现的。
```
