# Qoder 开发记录（records/）

本目录是赛题「Qoder × OceanBase」提交材料中 **「Qoder 开发记录」** 一栏的载体，
用于证明「项目的主要开发工作是在 Qoder 中完成的」。

## 目录内容

| 路径 | 内容 |
| --- | --- |
| `qoder-account.md` | Qoder 账号 UID、客户端与版本、CLI 无头驱动方式、登录态打通过程、推理额度实测与排队对策 |
| `sessions-index.md` | **会话索引**：每个阶段一次会话的轮次、工具调用数、起止时间，以及它实际写过的文件清单 |
| `session-stats.json` | 同一份索引的机器可读版本 |
| `sessions/*.jsonl` | **Qoder 会话原始记录**：每个阶段一次会话，Qoder 的思考、工具调用、文件读写全部在内（逐行 JSON） |
| `cli-logs/*.log.gz` | Qoder CLI 运行日志（gzip），含服务端排队参数、模型请求与工具调用的完整时序 |
| `development-log.md` | 人可读的「Qoder 在每个阶段做了什么」逐阶段说明（对应提交材料里的「Qoder 开发记录」正文） |

## 怎么读这些记录

`sessions/*.jsonl` 是 Qoder 自己的会话落盘格式（Qoder IDE 与 CLI 共用同一存储：
`%USERPROFILE%/.qoder/projects/<项目路径编码>/<session-id>.jsonl`）。
逐行 JSON，常见 `type`：

- `user` —— 人（或本项目驱动脚本）发出的指令；
- `assistant` —— Qoder 的回复，`message.content` 里依次是 `thinking`（推理）、
  `text`（说明文字）、`tool_use`（Read / Write / Edit / Bash 等工具调用，
  含 `name` 与 `input`）、`tool_result`（工具返回）；
- `file-history-snapshot` —— Qoder 对被修改文件的版本快照；
- `active-leaf` —— 会话游标。

按 `docs/prompts/` 的 7 个阶段依次阅读，可以完整还原「需求拆解 → 设计 → 编码 →
测试 → 站点」的推导过程。

### 核对清单（评审方可用）

1. 打开 `sessions-index.md`，每个阶段一行，能看到 **assistant 轮次**与**工具调用数**；
2. 对某个阶段，把 `sessions/<session-id>.jsonl` 里的 `tool_use` 事件筛出来，
   `input.file_path` 就是 Qoder 实际写下的文件，`input.command` 就是它实际跑过的命令；
3. 这些文件现在都在仓库里，可以逐个打开比对；
4. `cli-logs/*.log.gz` 解压后是同一次运行的 CLI 侧日志，可与服务端请求一一对应。

## 为什么记录的是这些

赛题要求「保留开发过程记录，说明 Qoder 在需求拆解、编码、调试等环节中完成了哪些
工作」。本项目把这三个环节都做成了**可核对的物证**：

1. **需求拆解** —— 阶段 1 的会话里只有 Qoder 自己读 PRD、定模块边界、定数据模型，
   产出 `qoder/docs/technical-design.md`；技术设计里的取舍清单是 Qoder 提的，
   不是人写的。
2. **编码** —— 阶段 2~7 的会话里能看到 Qoder 逐个文件 Write/Edit，
   以及它自己跑测试、自己跑端到端脚本的过程。
3. **调试** —— 人发现的真实缺陷（中文 MATCH 命中不了、解析器顺序错误、
   导入幂等指纹、重启后复习队列为空、端到端脚本退出码），
   以及回抛给 Qoder 的修正提示词，对应的会话同样在 `sessions/` 里；
   每一处修正都能在会话里看到「Qoder 改了什么、为什么改、怎么验证」。

> 仓库内不包含任何令牌、密钥或密码。`qoder-account.md` 只记录账号公开标识
> （UID / 邮箱 / 昵称）与公开的调用方式。
