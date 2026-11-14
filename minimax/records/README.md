# 技术使用记录

| 记录 | 文件 | 内容 |
| --- | --- | --- |
| MiniMax 开发记录 | [`minimax_development.md`](minimax_development.md) | 本次任务、提供的上下文、模型返回的补丁、补丁到代码差异的对应关系、迭代过程 |
| 数据库使用记录 | [`database_usage.md`](database_usage.md) | 存了什么、查了什么 SQL、EXPLAIN 证明全文索引被使用、导入日志 |
| PowerContext 状态记录 | [`powercontext_state.md`](powercontext_state.md) | 接入方式、写入记录、重启后读回记录、从空状态开始的流程 |

原始材料：

| 目录 | 内容 |
| --- | --- |
| [`minimax/`](minimax/) | 每次 MiniMax 调用的 prompt、回答、usage 与补丁原文（见该目录下的 `README.md`） |
| [`logs/`](logs/) | 端到端验证的完整日志：每一步的命令、工作目录、退出码、期望、实际输出与代码版本 |

验证日志文件名格式：`validation-<时间戳>.json`。每次端到端验证生成一份，其中
`code_version` 字段记录了本次运行对应的代码版本（`git rev-parse HEAD`），
每个步骤都记录了 `command`、`cwd`、`exit_code`、`expected`、`passed` 与输出片段。

| 日志 | 结果 | 说明 |
| --- | --- | --- |
| `validation-clean-clone-20261114-165844.json` | **19/19 通过** | **干净克隆** `yayajing1/oceanbase-powercontext@2026-competition-ecosystem`（commit `96e48297`）后重跑，代码版本字段即该 commit |
| `validation-20261114-165205.json` | **19/19 通过** | 开发目录中的最终验证 |
| `validation-20261114-164738.json` | 18/19 | 上一轮：补丁锚点过严导致补丁未应用（已修复） |
| `validation-20261114-164415.json` | 16/19 | 更早一轮：状态读取的分页参数越界（已修复） |

后两份是迭代过程中真实产生的失败记录，保留下来是为了说明问题是如何被发现和修复的。

干净克隆的复现方式：

```bash
git clone --depth 1 --branch 2026-competition-ecosystem \
    https://github.com/yayajing1/oceanbase-powercontext.git
cd oceanbase-powercontext/minimax
python -m pip install -r requirements.txt
export MINIMAX_API_KEY=<你的 Key>
export SDD_DB_PORT=2881
python validation/run_validation.py
```
