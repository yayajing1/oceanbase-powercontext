# 阶段提示词：06-cli-and-validation.md

> 直接复制下面代码块的内容，粘贴给 Qoder（IDE / CLI 均可）。

```text
继续实现「综述提纲」与统一的 CLI 入口，并补齐测试：

1. **综述提纲**：按标签 / 年份 / 作者聚合已有卡片，输出结构化 Markdown 提纲，
   每条提纲后面附上支撑它的卡片（标题 + 出处）。
2. **CLI 入口**（`python -m paperdesk`），子命令至少包括：
   `init-db` / `import` / `search` / `card` / `review` / `memory` / `outline` / `demo`。
   每个子命令都要有 `--help`，说明输入与输出。
3. **端到端验证脚本** `qoder/validation/run_validation.py`：
   从空库开始，依次执行
   导入样例笔记 → 中文检索 + 结构化过滤 → 生成卡片 → 加入队列 → 打分 →
   **重新启动进程** → 读回记忆与待复习清单 → 输出综述提纲，
   每一步打印「预期 / 实际」，最后给出 PASS/FAIL 汇总和退出码（全通过为 0）。
   脚本要记录每一步实际执行的 SQL 摘要，便于核对 seekdb 与 PowerContext 真的参与了。
4. 补单元测试：切块、归一化、复习算法、提纲聚合。

约束：
- 不要把模型 API Key、数据库密码写进代码或提交物；
- 端到端脚本要能在干净环境按 `qoder/README.md` 的步骤跑通；
- 本轮结束后，再补一份 `qoder/README.md`（运行环境、依赖版本、部署与接入方式、
  从安装到启动的完整步骤、测试命令、已知限制）。

```
