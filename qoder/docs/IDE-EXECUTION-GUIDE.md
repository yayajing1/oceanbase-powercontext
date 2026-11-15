# 在 Qoder IDE 中执行本项目的开发指南

> 这份指南给**执行者**（你）看：照着做即可，不需要理解技术细节。
> 全程约 7 段提示词，每段都有明确产出物。做完一段跟我说一声，我会立刻验收并反馈。

---

## 0. 开工前

### 0.1 打开正确的目录

在 Qoder IDE 里打开这个文件夹作为工作区：

```
D:\oceanbase work\powercontext-eco
```

> 必须是这个仓库根目录，不是 `qoder/` 子目录 —— Qoder 需要看到整个仓库上下文，
> 而且产物要落在 `qoder/` 下面，赛题要求所有资产都在 `qoder/` 目录中。

### 0.2 环境已经替你准备好了

| 项 | 状态 |
| --- | --- |
| seekdb 实例 | ✅ 已启动，`127.0.0.1:2882`（`5.7.25-OceanBase seekdb-v1.4.0.0`） |
| 项目数据库 | ✅ `paperdesk` 库已创建（当前为空，等 Qoder 建表） |
| PowerContext | ✅ `powercontext 1.2.0` 已安装 |
| Python 环境 | 建议用 `D:\oceanbase work\_scratch\pcvenv\Scripts\python.exe`（已含 pymysql / powercontext / pytest / pyyaml）；Qoder 写出的 `qoder/requirements.txt` 就是给评审方用的依赖清单 |

### 0.3 确认额度已生效

打开 Qoder IDE，随便发一句话「你好」，**能立刻收到回复**就说明额度已就绪。
如果还在转圈或提示排队，先去领赛题的 2000 Qoder CN Credits
（入口见 `qoder/records/qoder-account.md` 第 4.1 节）。

---

## 1. 七段提示词，逐段执行

每段做完，**在同一个会话里继续贴下一段**（保持上下文连续，Qoder 能记住前面的设计）。

| 段 | 提示词文件 | 期望产出 | 完成后检查 |
| --- | --- | --- | --- |
| 1 | `qoder/docs/prompts/01-requirements-analysis.md` | `qoder/docs/technical-design.md` | 有模块划分、seekdb 表结构与全文索引方案、PowerContext 记忆设计、取舍清单 |
| 2 | `qoder/docs/prompts/02-data-and-import.md` | `qoder/paperdesk/` 包、`qoder/requirements.txt`、`qoder/.env.example` | 能 `python -m paperdesk import qoder/data/sample_notes` 且不重复 |
| 3 | `qoder/docs/prompts/03-hybrid-search.md` | `qoder/paperdesk/search.py` | 中文关键词能命中；`--explain` 能打印出 SQL 与 EXPLAIN |
| 4 | `qoder/docs/prompts/04-cards-and-review.md` | `cards.py` / `review.py` | 无模型 Key 时给出明确提示而不是空卡片 |
| 5 | `qoder/docs/prompts/05-powercontext-memory.md` | `memory.py` | 记忆落盘到 `qoder/.state/`，重启进程后读回并影响输出 |
| 6 | `qoder/docs/prompts/06-cli-and-validation.md` | `cli.py`、`qoder/validation/run_validation.py`、`qoder/README.md` | 端到端脚本退出码 0 |
| 7 | `qoder/docs/prompts/07-site-sources.md` | `qoder/site/index.html` + `qoder/site/README.md` | 浏览器直接打开 `index.html` 正常显示 |

**贴法**：打开提示词文件 → 全选复制 → 粘到 Qoder 的对话框 → 回车。

> 提示词文件里每个代码块的内容就是投喂给 Qoder 的原话，**整段复制，不要改写**。
> 改了反而会让阶段之间的产出对不上。

---

## 2. 每段做完之后

跟 MCode 说一句「**阶段 N 做完了**」，我会立刻：

1. 跑独立验收脚本 `python qoder/validation/acceptance_check.py`；
2. 对照 PRD 的 A1~A10 逐条核对；
3. 跑 Qoder 自己写的端到端脚本；
4. 把发现的问题写成**下一轮提示词**给你，让你贴回 Qoder 修。

这样形成的「人 → Qoder → 人验收 → Qoder 修正」循环，就是赛题里
「Qoder 在需求拆解、编码、**调试**环节都参与了工作」的直接证据。

---

## 3. 常见问题

**Q：Qoder 说数据库连不上？**
A：确认连接参数走环境变量，默认 `127.0.0.1:2882` / `root` / 空密码 / 库名 `paperdesk`。
`paperdesk` 库我已经建好了，不用再 `create database`。

**Q：Qoder 问要不要写 `.env`？**
A：让它写 `.env.example`（占位值），`.env` 本身不进仓库。

**Q：某一段做完了但和上一段对不上？**
A：贴一句「**请回读 `qoder/docs/technical-design.md`，按其中的设计对齐当前实现**」，
让它自己纠偏。

**Q：额度一直没生效 / 我不想自己执行？**
A：告诉我，我用 Qoder CLI 无头驱动跑完全部 7 段（CLI 登录态我已经打通了，
就差额度）。两种方式产出的都是 Qoder 的开发记录，赛题都认；
CLI 方式还额外产出机器可读的会话 transcript。

**Q：会不会把我的 git 历史搞乱？**
A：不会。`qoder/` 目录目前只有 2 个 commit（PRD + 提示词 + 记录），
最终我会统一整理成符合 conventional commits 的提交再 push。

---

## 4. 最后两件事（需要你在 IDE 里做）

1. **发布站点**：在 Qoder IDE 里用 **Sites** 创建并发布 `qoder/site/`，
   可见范围设为「**任何人都可访问**」，把发布后的完整网址给我。
   具体步骤见 `qoder/site/README.md`（Qoder 会写）。
2. **提供截图/录屏**：赛题要求「关键开发环节的 Qoder 对话截图或录屏，并附 Qoder 账号 UID」。
   账号 UID 是：`01a0b9b1-9532-7ea1-8ed1-60d365e81c29`
   （见 `qoder/records/qoder-account.md`）。截图建议覆盖：阶段 1 的需求拆解对话、
   阶段 2/3 的编码与执行 SQL、阶段 5 的记忆读写、阶段 6 的端到端脚本跑通。
