# 安全设计

本文说明 Quant Terminal 的安全模型：防什么、怎么防、以及**不防什么**。
安全默认值是"单人/小团队自托管"的合理基线，不宣称满足等保/金融合规要求。

## 1. 威胁模型

| 想防的 | 手段 |
| --- | --- |
| 网络窃听、中间人 | 生产由 Caddy 终结 TLS，HSTS + 安全响应头 |
| 数据库被拖走 | 敏感字段密文（AES-256-GCM）、密码 Argon2id 哈希、refresh 只存摘要 |
| 撞库 / 暴力破解 | Argon2id、登录失败锁定、写请求限流、统一失败提示 |
| 会话盗用 | access 15 分钟短命、refresh 一次性轮换、可按 jti 精确吊销、可查会话列表 |
| 悄悄改账 | 审计日志 SHA-256 哈希链 + 完整性校验接口 |
| 密文挪位置（AAD 混淆） | 字段级 AAD 绑定上下文（见 §3） |
| 容器越权 | 非 root、最小镜像、无源码热挂载、密钥不进镜像 |
| XSS 偷 token | CSP、access token 不落盘；refresh 可吊销且轮换 |

| 不防的 | 说明 |
| --- | --- |
| 拿到主密钥 + 数据库的人 | 可解密全部字段——主密钥必须离线妥善保管 |
| 有数据库写权限的人重算审计链 | 哈希链是证据链，不是权限隔离 |
| 恶意数据源投毒 | 行情可信度由数据源决定，系统只做降级与来源标注 |
| 宿主机被完全控制 | 属于基础设施安全，不在应用层职责内 |

## 2. 密钥体系

```
Master Key（32 字节，环境变量 / 文件 / Docker secret）
   │ HKDF-SHA256
   ├─ info="quant/field-encryption" → 字段加密子密钥（凭据、TOTP 种子）
   ├─ info="quant/jwt-signing"      → JWT HS256 签名密钥
   └─ info="quant/audit"            → 预留
```

- **来源优先级**：`QUANT_MASTER_KEY` → `QUANT_MASTER_KEY_FILE` →
  `/run/secrets/quant_master_key`（Docker secret）→ 开发环境自动生成 `data/master.key`（权限 600）。
- **生产环境拒绝自动生成**：`QUANT_ENV=production` 且无密钥时启动即报错，
  避免"以为加密了，其实用的是临时密钥"。
- 密钥支持 32 字节 base64（标准/URL）或 64 位 hex；全零密钥被拒绝。
- 指纹 `SHA-256(key)[:16]` 可在 `GET /api/system/info` 查看，用于确认多实例共享同一密钥。
- **丢失主密钥 = 已加密凭据与 TOTP 种子不可恢复**。备份见 DEPLOY.md §6。

## 3. 字段加密

- 算法：**AES-256-GCM**（认证加密），每次加密随机 12 字节 nonce。
- 格式：`v1.<base64url(nonce || ciphertext || tag)>`，版本号预留算法轮换。
- **AAD 绑定字段上下文**：

  | 用途 | AAD |
  | --- | --- |
  | 通用凭据 payload | `credential:{owner_id}:{name}` |
  | TOTP 种子 | `totp:{user_id}` |

  因此把 A 用户的密文复制到 B 用户字段上，解密会直接失败（`CryptoError`），
  而不是悄悄解出错误明文。
- 明文只在写入/解密请求的内存中存在；列表接口（`GET /api/credentials`）不返回 payload；
  `POST /api/credentials/{id}/reveal` 仅管理员可用，且**每次都会写审计**。

## 4. 账号与口令

- **哈希**：Argon2id（`argon2-cffi`，参数取库默认安全档）。
  环境缺失时回退标准库 scrypt（仍安全，但属降级，日志会提示）。
  登录成功时检测旧哈希并自动升级（`needs_rehash`）。
- **强度**：`validate_strength` 拒绝短于 10 位、纯数字、常见弱口令组合。
- **登录失败锁定**：默认 5 次失败锁定 15 分钟（`QUANT_LOGIN_MAX_ATTEMPTS` /
  `QUANT_LOGIN_LOCK_MINUTES`）；成功登录清零。
- **时序防护**：用户不存在时也对固定哈希做一次校验，响应耗时一致。
- **统一提示**："用户名或密码错误"不区分具体原因；审计日志里记录真实原因。
- **停用**：`is_active=False` 的账号立即无法登录，已签发 access 在认证依赖里也会被拒
  （每次请求都回查用户状态）。

## 5. 会话与令牌

| 项 | 值 |
| --- | --- |
| 算法 | HS256（密钥由主密钥派生，多实例互认） |
| access TTL | 900 秒（`QUANT_ACCESS_TOKEN_TTL`） |
| refresh TTL | 604800 秒（`QUANT_REFRESH_TOKEN_TTL`） |
| 声明的 `type` | `access` / `refresh`，校验时**必须匹配**，防止 refresh 当 access 用 |

- **轮换**：每次 `/auth/refresh` 签发新 refresh 并立刻吊销旧的。
- **重放检测**：收到的 refresh 若与库中 `token_hash`（SHA-256 摘要，不存原文）不符，
  视为令牌泄露，立即吊销该 jti 并写 `auth.refresh.reuse` 审计。
- **吊销**：`DELETE /api/auth/sessions/{id}`、登出（吊销当前 refresh）、改密（吊销全部）。
- **前端**：access token 只在内存；refresh 在 `localStorage`；
  任意 401 触发一次静默刷新，重放失败即清空会话回登录页。

## 6. 两步验证（TOTP）

- 标准 TOTP（`pyotp`，30 秒步长），兼容 Google Authenticator / 1Password 等。
- 种子用字段子密钥加密，AAD 为 `totp:{user_id}`，明文不落库。
- 启用流程：`/totp/setup` 生成并暂存 → 用户输入验证码 → `/totp/enable` 才真正生效。
- **一次性恢复码** 8 枚：库中只存 SHA-256，使用后立即删除；登录时可代替 TOTP。
- `QUANT_REQUIRE_TOTP=true` 时（生产建议），前端会引导开启。
  注意：该开关当前是"引导"而非服务端强制拦截登录，多管理员场景建议配合管理员审查。

## 7. 审计哈希链

```
hash_n = SHA-256( hash_{n-1} + "|" + canonical_json(body_n) )
body   = {ts, actor, action, target, detail, ip}
```

- `canonical`：键排序、无空格、保留中文，保证同一内容哈希稳定。
- 覆盖事件：登录/失败/刷新/重放/改密、TOTP 变更、凭据增删改查（含 reveal）、
  用户管理、回测/纸盘提交等。
- 校验：`GET /api/system/audit/verify` 返回 `valid/count/error`，
  任何一条被改动或删除都会定位到断点。
- 审计写入与业务同事务：业务回滚则审计不落库，避免"有记录但没发生"。
- **边界**：哈希链不能防拥有写权限者整链重算。需要更强保证时，把
  `GET /api/system/audit/verify` 的结果定期导出到外部不可变存储。

## 8. 传输与浏览器侧

- **TLS**：生产由 Caddy 自动申请证书（Let's Encrypt），HTTP 自动跳转 HTTPS。
- **HSTS**：应用在 `QUANT_ENV=production` 时注入
  `Strict-Transport-Security: max-age=31536000; includeSubDomains`。
- **响应头**（应用与 Nginx 双重注入）：
  `X-Content-Type-Options: nosniff`、`X-Frame-Options: DENY`、
  `Referrer-Policy: no-referrer`、`Permissions-Policy` 收紧、
  严格 CSP（`default-src 'self'`，禁止内联脚本）。
- **同源部署**：Caddy/Nginx 反代 `/api` 与 `/ws`，前端不接触跨域凭据。
- **CORS**：仅在分离部署时使用，`QUANT_ALLOWED_ORIGINS` 白名单，生产禁止 `*`。

## 9. 反滥用与输入处理

- **写请求限流**：按 IP 每分钟上限（默认 120，`QUANT_RATE_LIMIT_PER_MINUTE`），
  超限返回 429 + `Retry-After`。
- **请求 ID**：每个请求注入 `X-Request-ID`，错误响应带 `request_id`，便于对日志。
- **输入校验**：所有请求走 pydantic schema；未处理异常在生产环境不回显堆栈。
- **文件系统**：仅数据目录可写；前端静态资源路径做目录穿越检查。

## 10. 容器与部署加固

- 后端镜像：`python:3.12-slim`，非 root 用户 `quant`（uid 10001），
  仅装运行时依赖，源码随镜像复制（只读使用），主密钥通过环境变量/Docker secret 注入。
- 前端镜像：构建阶段 Node（构建产物以外的内容不进最终镜像），运行阶段 Nginx，
  `server_tokens off`，只暴露 80。
- 数据库端口不对宿主机暴露（compose 内部网络）；生产只暴露 Caddy 的 80/443。
- SQLite 文件与 `data/` 在命名卷里；备份与主密钥分开保管（见 DEPLOY.md §6）。
- 日志轮转：compose 层 `max-size 10m × 3`，避免磁盘被日志吃满。

## 11. 生产上线清单

- [ ] 用 `./scripts/init-secrets.sh <域名>` 生成 `.env`，`chmod 600`，不要提交进 Git。
- [ ] 主密钥离线备份（密码管理器/加密 U 盘），与数据库备份分开存放。
- [ ] `QUANT_ENV=production`、`QUANT_DEBUG=false`、`QUANT_SECURE_COOKIES=true`。
- [ ] `QUANT_ALLOWED_ORIGINS` 只保留真实域名（同源部署时它不生效，但别留 `*`）。
- [ ] 首次登录立即改管理员密码，开启 TOTP，抄写恢复码。
- [ ] 为每位使用者单独创建账号（设置 → 用户管理），不要多人共用一个 admin。
- [ ] 跑 `./scripts/verify.sh`，确认健康检查与审计链校验通过。
- [ ] 配置数据库/卷的定期备份与恢复演练。
- [ ] 前置防火墙/安全组：只放行 80/443（以及 SSH 的受限来源）。
- [ ] 视需要设置 `QUANT_REQUIRE_TOTP=true`，并检查所有账号都已启用。

## 12. 已知限制与取舍

- 审计链、限流、失败计数都在单进程内存/单库中实现，多副本部署时行为是"每副本各自一份"。
- `live` 纸盘命令在单实例内执行，无分布式调度；横向扩展需要外置队列。
- 前端 refresh token 存在 `localStorage` 是可用性与无状态后端之间的取舍；
  若部署环境对 XSS 有更高要求，可改为 HttpOnly Cookie 方案（需要服务端改动）。
- 退避策略基于固定窗口，非自适应；面向公网的部署建议在边缘再加一层 WAF/限流。


## 注册审核与反馈

- 自助注册需要图形验证码（HMAC 签名令牌、5 分钟过期）+ 管理员审核；
  待审核/已拒绝账号不能登录。
- 注册与审核动作写入审计链；审核通过前账号 `is_active=false`。
- 用户反馈仅管理员可读；普通用户只有提交接口。
