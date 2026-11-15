# 本机实测事实：PowerContext 1.2.0 内置 Runtime 的正确接法（2026-11-15 实测，评审方提供）

> 下面是**在本机实际跑通**的 PowerContext 接入方式（`powercontext 1.2.0`，
> 安装在独立 venv 里）。技术设计文档 3.1 节的伪代码与真实 API 有出入，
> 请以本文件为准，直接照抄即可跑通，省掉反复试错。

## 1. 打开 runtime：异步上下文管理器 + `BuiltinConfig`

```python
import asyncio
from powercontext.builtin.runtime import open_builtin_runtime, BuiltinConfig
from powercontext.builtin.persistence.sqlite.profile import SQLiteConfig

def build_config(state_path: str) -> BuiltinConfig:
    # 注意：SQLiteConfig 用的是 url，不是 path；path 会被 pydantic 拒绝
    url = "sqlite+aiosqlite:///" + state_path.replace("\\", "/")
    return BuiltinConfig(database=SQLiteConfig(url=url))

async with open_builtin_runtime(
    build_config(r"D:\...\.state\powercontext.db"),
    scheduler_path=r"D:\...\.state\powercontext.scheduler.db",
) as rt:
    ...
```

要点：

| 项 | 实测结论 |
| --- | --- |
| `open_builtin_runtime` | **异步上下文管理器**，`async with` 才能拿到 runtime |
| 第一个参数 | `BuiltinConfig`，不是路径字符串 |
| `SQLiteConfig` | 字段是 `url="sqlite+aiosqlite:///..."`，**没有 `path` 字段**（传 `path=` 会报 `Extra inputs are not permitted`） |
| `scheduler_path` | 单独 kwarg，默认 `powercontext.scheduler.db` |
| 落盘 | 进程结束/关闭后，`powercontext.db` 是真实文件（实测 610 KB 的 SQLite 文件） |

## 2. 必须先有 scope，否则写入报 `ScopeNotFoundError`

```python
scope = await rt.scopes.bootstrap_default()   # -> ScopeDescriptor
scope_id = scope.scope_id                     # 形如 'scp_1s2b79c55w64qn63n14849b4ed'
```

`scope_id` 是**随机生成**的，跨进程读回时不要写死，要从 runtime 里查：

```python
scopes  = await rt.scopes.list()
default = await rt.scopes.default_scope()
scope_id = default.scope_id if default else scopes[0].scope_id
```

## 3. 写入两类记忆

### 3.1 Source（content Source）：原始素材

```python
from powercontext.builtin.runtime import CaptureSource

receipt = await rt.sources.for_scope(scope_id).capture(
    CaptureSource(
        source_id="src-profile-1",          # 必填
        content="我最近在研究图神经网络与对比学习，准备写一篇综述。",
        metadata={"kind": "profile"},       # 必填
    )
)
# -> SourceReceipt(source_ref=SourceRef(source_type='content', source_id='src-profile-1'), sequence=1)
```

`CaptureSource` 的 `source_id` / `content` / `metadata` **三个字段都是必填**。

### 3.2 Memory：结构化记忆条目

```python
from powercontext.builtin.runtime import (
    MemoryEntryInput, RememberMemoryRequest,
)

result = await rt.memory.for_scope(scope_id).remember(
    RememberMemoryRequest(
        entries=(
            MemoryEntryInput(
                kind="profile",              # 必填，条目类型（profile / review / weakness ...）
                text="研究方向：图神经网络；常用标签：对比学习、图神经网络",  # 必填
            ),
        )
    )
)
# -> MemoryMutationResult(previous_revision=None, memory_ref=..., entry=MemoryEntryRecord(...))
```

要点：`RememberMemoryRequest.entries` 是**元组**，一次可以写多条；
`kind` 与 `text` 都必填。

## 4. 读回：跨进程可用（实测已验证）

写入进程退出后，**另起一个 Python 进程**，用同一个 state 文件：

```python
async with open_builtin_runtime(build_config(state), scheduler_path=sched) as rt:
    scope = await rt.scopes.default_scope()
    mscoped = rt.memory.for_scope(scope.scope_id)

    page = await mscoped.list()
    # -> MemoryEntriesPage(memory_ref=..., entries=(MemoryEntryRecord(... state='active' ...),))

    hits = await mscoped.search(
        SearchMemoryRequest(query="图神经网络", mode="fts")
    )
    # -> MemorySearchPage(mode='fts', hits=(MemoryHit(text='研究方向：图神经网络；...',
    #                                               score=0.0163, matched_by=('fts',)),))
```

实测输出（全新进程读回）：

```
scopes on disk: (ScopeDescriptor(scope_id='scp_1s2b79c55w64qn63n14849b4ed', ...),)
READ BACK list(): entries=(MemoryEntryRecord(state='active', entry=MemoryEntryVersion(
    kind='profile', text='研究方向：图神经网络；常用标签：对比学习、图神经网络', ...)),)
search(mode=fts): hits=(MemoryHit(text='研究方向：图神经网络；常用标签：对比学习、图神经网络',
    score=0.0163, matched_by=('fts',)),)
```

**中文能被 FTS 命中**（`matched_by=('fts',)`），所以检索记忆可以用 `mode="fts"`；
`mode="auto"` 在没有 embedding 模型时也会自动降级到 `fts`。
不要依赖 `auto` 的语义检索（需要模型）。

`ScopedMemoryApplication` 可用方法：
`remember` / `get` / `list` / `search` / `revise` / `retire` / `changes` /
`flush` / `compact` / `cursor` / `capacity`。

## 5. 对 PaperDesk 的落地建议

1. 状态目录固定在 `qoder/.state/`（已被 `.gitignore` 忽略，不要提交）。
2. 文件名固定 `powercontext.db`，便于验收脚本按 `.db` 后缀发现落盘文件。
3. scope 每次 `bootstrap_default()` + 读时 `default_scope()`，**不要写死 scope_id**。
4. 三类记忆用 `kind` 区分：`profile`（用户画像）、`review`（复习记录）、
   `weakness`（薄弱点）；再加一个 `outcome`（Task Outcome，旁证）。
5. `memory show` 子命令要把读回来的内容原样打印，并标明来源是 PowerContext。
6. 跨进程复用的验收方式：写记忆 → **起一个全新进程** → 读回并断言内容存在。
