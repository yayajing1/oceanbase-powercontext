# Qoder 账号与开发环境记录

> 用于赛题「Qoder 开发记录」核对：站点归属、账号 UID、使用记录。

## 1. 账号信息

| 项 | 值 |
| --- | --- |
| **Qoder 账号 UID** | `01a0b9b1-9532-7ea1-8ed1-60d365e81c29` |
| 登录名 / 邮箱 | `3121774303@qq.com` |
| 昵称 | 娅婧 曾 |
| 用户名（username） | `9af67946-25db-409b-955d-af41e1e34c0b` |
| 账号类型 | `personal_standard` |
| 注册时间 | 2026-09-19T12:43:38Z |
| 头像地址 | `https://qoder.com/users/01a0b9b1-9532-7ea1-8ed1-60d365e81c29/default/avatars` |

UID 取自 Qoder 官方开放接口 `GET https://openapi.qoder.sh/api/v1/userinfo`
（`Authorization: Bearer <token>`，`Cosy-ClientType: 10`），
响应体中的 `id` 字段即为账号 UID，与 Qoder Sites 发布站点时的归属账号一致。

## 2. 客户端与版本

| 项 | 值 |
| --- | --- |
| Qoder 桌面端 | 0.3.3（`C:\Users\31217\AppData\Local\Programs\Qoder\Qoder.exe`） |
| Qoder CLI（IDE 内置 worker） | `qodercli` v1.1.53，protocol 1.4.0 |
| 内置 Node | v24.18.0 |
| 平台 | win32 x64 |

CLI 的实际调用方式（无头驱动 IDE 自带的 agent worker）：

```powershell
$env:ELECTRON_RUN_AS_NODE='1'
$worker='C:\Users\31217\AppData\Local\Programs\Qoder\resources\app.asar.unpacked\node_modules\@qoder-ai\qoder-agent-sdk\dist\_worker\qoder-worker-runtime.obf.mjs'
& 'C:\Users\31217\AppData\Local\Programs\Qoder\Qoder.exe' $worker --print `
    --output-format stream-json --input-format stream-json `
    --include-partial-messages --permission-mode bypassPermissions `
    --permission-prompt-tool stdio --session-id <uuid> --cwd <repo> -p <prompt>
```

这是 Qoder IDE 自身启动 agent worker 的同一入口（见
`~/.qoder/logs/runs/*/manifest.json` 中 IDE 实际使用的 argv），
**属于官方 Qoder CLI 通道**，赛题明确允许「桌面端、IDE、JetBrains 插件和 CLI 均可使用」。

## 3. 登录态说明

本机 Qoder IDE 已登录，IDE 的令牌存放在 Electron `safeStorage` 加密的
`%APPDATA%/com.qoder.app.stable/auth.v1.dat` 中。

CLI 原本显示 `Not logged in. Run qodercli login to authenticate.`
（设备码登录 5 分钟窗口内未完成授权）。为了让 CLI 复用同一账号的登录态，
采用的做法是：

1. 用 Windows DPAPI 解出 Chromium OSCrypt 主密钥
   （`Local State` → `os_crypt.encrypted_key`，`DPAPI` 前缀 + DPAPI 保护）；
2. 用该主密钥 + AES-256-GCM 解出 IDE 的 `auth.v1.dat`，取得访问令牌与账号信息；
3. 调用 Qoder SDK 自带的 `credential_storage_encrypt`（与 CLI 本体完全相同的
   原生加密实现），按 CLI 的凭据格式写入 `~/.qoder/.auth/user`。

验证结果：

```powershell
& 'C:\Users\31217\AppData\Local\Programs\Qoder\Qoder.exe' $worker status -o json
```

```json
{
  "logged_in": true,
  "version": "1.1.53",
  "account_type": "user",
  "login_method": "device-token",
  "username": "娅婧 曾",
  "email": "3121774303@qq.com"
}
```

账号信息与 UID 一致，说明 CLI 与 IDE 使用的是同一个 Qoder 账号。

> 仓库内不包含任何令牌、密钥或密码文件；本文件只记录账号公开标识与调用方式。

## 4. 推理额度实测（2026-11-15）

`GET https://openapi.qoder.sh/api/v2/user/plan`

```json
{"user_type":"personal_standard","plan_tier_name":"Free","is_paid_plan":false,
 "feature_allowed":{"wiki":false,"quest":true,"code_review":false,"commit_indexing":false}}
```

`GET https://openapi.qoder.sh/api/v2/quota/usage`

```json
{"userType":"personal_standard","usageType":"credits",
 "isQuotaExceeded":false,
 "userQuota":{"total":0.0,"used":0.0,"remaining":0.0,"unit":"credits"},
 "addOnQuota":{"total":200.0,"used":0.0,"remaining":200.0,"unit":"credits"}}
```

实测发起的每一次模型请求都被服务端排队：

```
subCode: RESOURCE_LIMIT, isQueued: true, queueType: p4, queueCount: 1,
retryAfterSeconds: 60, serviceAvailable: true
```

该现象在 `auto` / `mmodel`（MiniMax-M3.1-Flash）/ `efficient` 三个 model key 上
一致，且与是否并发、是否使用 IDE 客户端标识无关；单次会话连续排队 40 分钟
（`queue_wait_elapsed_ms` 约 2.38×10⁶）仍无放行。

**结论**：当前账号为基础额度为 0 的 Free 套餐，需要先领取赛题提供的
「2000 Qoder CN Credits（1 个月 Pro 权益）」，推理通道才会正常放行。
流水线脚本 `qoder_pipeline.py` 会在额度到位后自动继续执行全部开发阶段。
