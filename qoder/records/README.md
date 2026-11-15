# Qoder 开发记录（records/）

本目录是赛题「Qoder × OceanBase」提交材料中 **「Qoder 开发记录」** 一栏的载体，
用于证明「项目的主要开发工作是在 Qoder 中完成的」。

## 目录内容

| 路径 | 内容 |
| --- | --- |
| `qoder-account.md` | Qoder 账号 UID、客户端与版本、CLI 无头驱动方式、登录态打通过程、推理额度实测与解除路径 |
| `qoder-sessions/*.jsonl` | **Qoder 会话原始记录**：每个阶段一次会话，Qoder 的思考、工具调用、文件读写全部在内（机器可读，逐行 JSON） |
| `qoder-sessions/index.json` | 会话索引：每个会话的文件名、字节数、事件类型统计 |
| `qoder-cli-logs/*.log` | Qoder CLI 运行日志（含工具调用、SQL 执行、检索命中、记忆读写等原始记录） |

## 怎么读这些记录

`qoder-sessions/*.jsonl` 是 Qoder 自己的会话落盘格式（Qoder IDE 与 CLI 共用同一
存储：`%USERPROFILE%/.qoder/projects/<项目路径编码>/<session-id>.jsonl`）。
逐行 JSON，常见 `type`：

- `user` / `assistant` —— 对话与模型回复；
- `tool_use` / `tool_result` —— Qoder 的工具调用与结果（Read / Write / Edit / Bash 等）；
- `system` —— 会话初始化、模型队列、产物更新等系统事件。

按 `docs/prompts/` 的 7 个阶段依次阅读，可以完整还原「需求拆解 → 设计 → 编码 →
测试 → 站点」的推导过程。

## 为什么记录的是这些

赛题要求「保留开发过程记录，说明 Qoder 在需求拆解、编码、调试等环节中完成了哪些
工作」。本项目把这三个环节都做成了**可核对的物证**：

1. **需求拆解** —— 阶段 1 的会话里只有 Qoder 自己读 PRD、定模块边界、定数据模型，
   产出 `qoder/docs/technical-design.md`；技术设计里的取舍清单是 Qoder 提的，
   不是人写的。
2. **编码** —— 阶段 2~7 的会话里能看到 Qoder 逐个文件 Write/Edit，
   以及它自己跑测试、自己跑端到端脚本的过程。
3. **调试** —— `docs/qoder-prompts.md` 末尾的「反馈轮」列出了人发现的真实缺陷
   （中文 MATCH 语法、导入幂等指纹、重启后复习队列为空、端到端脚本退出码），
   以及回抛给 Qoder 的修正提示词，对应的会话同样在 `qoder-sessions/` 里。

> 仓库内不包含任何令牌、密钥或密码。`qoder-account.md` 只记录账号公开标识
> （UID / 邮箱 / 昵称）与公开的调用方式。
