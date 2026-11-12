# GLaDOS 自动签到（Cookie 接口 / 浏览器）

默认复用已有 Cookie，通过 GitHub Actions 每天在北京时间 **09:30** 触发签到，也支持手动运行。

## 推荐：复用 Cookie 调用签到接口

Cookie API 模式先从 `glados.cloud` 获取一次性签到校验，再用同一会话向同一域名提交签到，
成功后查询会员天数和积分余额。临时网络故障最多重试一次，每次使用新的校验。
会话、设备或自动化请求被服务端拒绝时，停止并提示人工检查。此模式可直接复用已有会话，无需 Chromium。
新版流程参照 [lankerr/2026-glados-checkin 的 Issue #28](https://github.com/lankerr/2026-glados-checkin/issues/28)。

登录风控与已有会话签到是不同步骤：邮箱发码接口返回 `captcha_required`，
只说明本次申请验证码需要人机验证；有效 Cookie 可以复用已有登录态。
这不保证 Cookie 永不过期，也不保证签到接口永远不要求验证；服务端明确拒绝时会停止。

仓库 **Settings → Secrets and variables → Actions** 中设置：

| 名称 | 类型 | 值 |
| --- | --- | --- |
| `GLADOS_AUTH_MODE` | Variable | `cookie`；从邮箱模式切换时必须修改原来的 `email` |
| `GLADOS_CHECKIN_METHOD` | Variable，可选 | `api`（默认）；`browser` 则复用 Cookie 用浏览器点击 |
| `GLADOS_COOKIE` | Secret | 已登录浏览器中复制的完整 Cookie 请求头 |
| `GLADOS_CHECKIN_TOKEN` | Variable，可选 | 默认 `glados.cloud`；旧值 `glados.one` 会自动纠正 |
| `GLADOS_FP` | Secret，可选 | 仅在原浏览器请求确实携带 `fp1.` 开头的 Authorization 时填写对应值；没有就留空 |
| `GLADOS_USER_AGENT` | Variable，可选 | 同一登录浏览器的完整 User-Agent |

Cookie 获取步骤：

1. 用你平时的浏览器打开 [GLaDOS](https://glados.cloud)，手动完成登录及站点要求的人机验证。
2. F12 → **Network**，刷新控制台，点开一次成功的 `/api/user/status` 或 `/api/user/session` 请求。
3. 在 **Request Headers → Cookie** 中复制完整值（不要复制 `Set-Cookie`），保存为 `GLADOS_COOKIE`。
4. 保留该请求中实际出现的所有字段。完整 `gld:sess` / `gld:sess.sig` 或完整 `koa:sess` / `koa:sess.sig`
   都允许提交，不能只根据字段名称判断是否过期；是否有效由服务端回复决定。
5. 同步最新代码后手动 **Run workflow**，确认日志出现 `Cookie 接口模式` 和明确的签到成功回复。

Cookie 支持完整单行请求头、带 `Cookie:` 前缀、外层引号或单域名 Cookie-Editor JSON。
签名值不做 HTML/URL 解码，完整请求头不重排、不丢字段；缺少完整会话或明显复制截断会报错。
Cookie 是登录凭据，只保存到 Secret，不提交到仓库，也不要贴到聊天中。
Cookie 失效后需要在浏览器重新登录、更新 Secret；脚本不会静默切回邮箱模式反复申请验证码。

若接口明确返回 **`device-mismatch`**，先核对 **同一已登录浏览器** 的完整 Cookie 与 UA。
UA 可从该请求的 Request Headers → User-Agent 复制，也可在网页控制台读取：

```javascript
navigator.userAgent                // 完整结果存为 Variable GLADOS_USER_AGENT
```

`GLADOS_USER_AGENT` 在本工作流中从 **Variables** 读取，放在 Secrets 中不会生效。
运行日志会显示 UA 与 FP 是否已配置，不显示实际凭据。

**找不到 Authorization，且 `localStorage.getItem('glados.fp.v1')` 返回 `null` 时，
不要填写 GLADOS_FP，更不要把字面量 `null` 当作值。** 2026-10-18 检查公开前端代码发现，
`/login` 加载的 `app.bundle.js` 有缓存指纹并设置 Authorization 的逻辑，
而 `/console` 加载的另一套前端，其 API 请求拦截器没有这段逻辑；因此这个字段并非所有页面都有。
这也不能反向证明服务端没有其他设备校验。只有原浏览器请求实际发送对应字段时才配置它。

更新 UA 后可手动运行一次工作流。若仍返回设备不匹配，在原浏览器手动签到核实：
浏览器也失败时按站点提示重新登录，再复制新 Cookie；浏览器成功但 Actions 失败时，
对比 `/api/user/checkin` 的回复，尤其是 `loginDevice` 和 `currentDevice`（如果返回），
以及该请求实际携带的头。读取 `/api/user/status` 成功只能确认登录态，不能确认签到通过设备校验。

`print_fingerprint.py` 会新建独立浏览器，不能导出你原浏览器
已有的登录缓存，因此其探测值不能与原浏览器 Cookie 混用。
`没有权限` 本身不能证明 TLS/浏览器指纹有问题，更不能据此认定所有 koa Cookie 已失效。
旧版 `diag_status.py` 曾漏发 Cookie，会造成“没有权限”的误判；现已补齐请求头并加入回归测试。

## 可选：QQ 邮箱验证码登录

需要邮箱模式时将 `GLADOS_AUTH_MODE` 设为 `email`。运行时通过浏览器申请验证码、
用 QQ IMAP 读取、在同一浏览器完成登录与签到；这个模式每次都会重新登录。
站点要求人机验证时脚本会停止，需要人工处理，不会自动解决验证码挑战。

在 [QQ 邮箱](https://mail.qq.com/) 设置中开启 IMAP/SMTP 服务并生成客户端授权码，
按 [QQ 官方说明](https://help.mail.qq.com/detail/106/985) 操作。配置：

| 名称 | 类型 | 内容 |
| --- | --- | --- |
| `GLADOS_EMAIL` | Secret | GLaDOS 登录邮箱，即验证码收件地址 |
| `QQ_IMAP_AUTH_CODE` | Secret | QQ 邮箱客户端授权码，不是 QQ 密码或临时验证码 |
| `GLADOS_MAIL_FROM` | Secret | 验证码邮件 From 中的完整发件地址，不是你自己的收件邮箱 |
| `QQ_IMAP_USERNAME` | Secret，可选 | QQ IMAP 登录邮箱，默认使用 `GLADOS_EMAIL` |
| `GLADOS_CODE_REGEX` | Variable，可选 | 默认 `\b[0-9]{6}\b`，六位纯数字无需另填 |
| `GLADOS_MAIL_TIMEOUT_SECONDS` | Variable，可选 | 默认 180 秒，允许 15–600 秒 |

邮件读取仅限 INBOX、指定发件人和 To 收件人，排除申请前已有的邮件与过早到达的邮件。
使用只读 IMAP 和 `BODY.PEEK[]`，不标记已读、不删除邮件。转发改变 To、进入垃圾箱或提取
出多个不同验证码时会失败；正则最多一个捕获组，可按真实邮件文字调整。
邮箱模式忽略外部 Cookie、FP 与 UA。`--dry-run` 会拒绝邮箱模式，防止只读检查意外发码。

登录页先等待主文档响应，再等待邮箱输入框；仅发码前的页面访问会重试。
登录响应体可能因立即跳转而丢失，脚本会复核本站控制台跳转与 `/api/user/session`，
服务端返回 `HTTP 200 / code=0` 才继续。明确的 JSON 拒绝和人机验证不会进入成功兜底。
日志只记录阶段、HTTP/内容类型、固定资源分类和报错数量，不输出验证码或登录页截图。

## 凭据与故障判断

| 现象 | 处理 |
| --- | --- |
| 发码接口 `captcha_required` | 本次邮箱登录需要人工验证；可复用手动登录后的有效 Cookie |
| Cookie API `没有权限` / `code=-2` | 本次会话未被接受，检查是否复制完整、是否已过期或被吊销 |
| 签到接口 `device-mismatch` | 先核对同一登录浏览器的 Cookie 与 UA；Authorization/FP 仅在原请求确实存在时配置 |
| `please checkin via ...` | 核对签到 token，当前默认 `glados.cloud` |
| `Automated check-in detected` / `captcha_required` | 服务端拒绝本次签到，请在官网手动处理 |
| `Checkin! Got 1 Points` | 服务端确认本次签到获得 1 积分，可在官网积分流水核对 |
| `未配置完整 SMTP` | 未启用失败邮件通知，与 QQ IMAP 或签到认证是否成功无关 |

本地 Python 需要可用的 SSL/TLS；CI 使用 Python 3.12。Cookie API 只依赖 `requirements.txt`；
邮箱模式和浏览器点击备选还需要 `requirements-browser.txt` 与 Chromium 或已安装的 Chrome/Edge。

## 固定排程与迁移

签到工作流每天按 UTC 01:30 调度（北京时间 09:30），实际开始时间取决于 GitHub Actions。
更新默认分支后，可移除旧的 `SCHEDULE_TOKEN` Secret。如果该令牌只用于旧排程，也可以撤销它。
在 Actions 的 **GLaDOS Checkin → Run workflow** 可立即触发一次。若要核对奖励，请在当天首次签到时执行，
对照签到消息和官网积分流水。实际积分由 GLaDOS 决定。

## 配置项

在 **Settings → Secrets and variables → Actions**：

| 名称 | 类型 | 默认与用途 |
| --- | --- | --- |
| `GLADOS_COOKIE` | Secret | 必填；完整 Cookie |
| `GLADOS_FP` | Secret | 可选；明确出现设备不匹配时，从同一登录网页读取的真实缓存值 |
| `GLADOS_USER_AGENT` | Variable | 可选；同一登录浏览器的完整 UA |
| `PUSHPLUS_TOKEN` | Secret | 可选，PushPlus 推送 Token |
| `SERVERCHAN_SENDKEY` | Secret | 可选，Server 酱 Turbo 或 Server 酱³ 的 SendKey |
| `TELEGRAM_BOT_TOKEN` | Secret | 可选，Telegram Bot Token，与 `TELEGRAM_CHAT_ID` 一起配置 |
| `TELEGRAM_CHAT_ID` | Secret | 可选，接收通知的 Telegram 聊天 ID |
| `PUSH_LEVEL` | Variable | Actions 默认 `all`，每次运行均推送。设为 `fail_only` 时仅推送失败 |

本地还可以用 `GLADOS_FP_FILE` 指向存放指纹的文件（省去复制粘贴），
以及 `GLADOS_BROWSER_CHANNEL=chrome|msedge` 复用系统浏览器。

可同时启用多个推送渠道。要接收 Server 酱的微信汇报，把 SendKey 保存为 `SERVERCHAN_SENDKEY` Secret。
汇报包含脱敏账号、本次奖励和积分余额、剩余天数、官网提供的兑换选项、北京时间及运行链接。
兑换选项仅供查看。本地直接运行时，`PUSH_LEVEL` 默认是 `fail_only`。
`--dry-run` 跳过通知。未配置任何推送渠道时，失败邮件沿用旧 SMTP 配置（可留空）：
`SMTP_HOST` / `SMTP_PORT` / `SMTP_USERNAME` /
`SMTP_PASSWORD` / `SMTP_USE_SSL` / `MAIL_TO` / `MAIL_FROM`。
邮件正文只包含脱敏后的错误说明与 Actions 链接，不含 Cookie。

## 排查工具

| 脚本 | 用途 |
| --- | --- |
| `diag_status.py` | 只发一次 `/api/user/status`，判断 Cookie 现在还有没有效（不起浏览器） |
| `print_fingerprint.py` | 独立测试浏览器的指纹探测；不能替代原登录网页的缓存导出 |
| `checkin.py --dry-run` | Cookie API 模式只查询 status，不提交签到；浏览器模式检查页面和按钮 |

常见对照：

| 日志 | 结论 |
| --- | --- |
| `code=-2 没有权限` | Cookie 无效/不完整（先用 `diag_status.py` 确认） |
| `code=4 reason=device-mismatch` | 指纹或 UA 与登录设备不一致，重新导出 `GLADOS_FP` |
| `页面 error：net::ERR_CONNECTION_TIMED_OUT` | runner 到站点网络抖动，脚本会自动重试 |
| `发起签到请求：POST …` 没出现 | 点击没触发接口，看重试后的现场日志与截图 |

## 结果与退出码

- `Checkin! Got … Points` 表示本次签到成功，消息中的分值为本次奖励。
- `Checkin Repeats! Please Try Tomorrow` 或 `Today's observation logged` 表示今日已签到。
- 按钮同时处于 `disabled` 和 `is-done` 状态时，按今日已签到处理。
- `没有权限` / `HTTP 401/403` → 致命：检查 Cookie 是否被接受或站点是否要求验证；
- `code=4` 且 `reason=device-mismatch` → 致命：指纹/UA 需要与登录设备对齐；
- 超时、429、5xx → Cookie API 最多请求两次（间隔 5 秒）；浏览器模式按原有 60 秒间隔重试。

退出码：`0` 成功，`1` 配置/认证类错误，`2` 临时故障。
浏览器签到失败时可上传 `checkin-failure-screenshots`（保留 7 天）；Cookie API 模式不生成截图。

## 本机运行（依赖装在仓库内的隔离虚拟环境）

不会往系统默认 Python 里装任何包：

~~~powershell
pwsh -File setup_env.ps1          # 创建 .venv，安装 requests + playwright，并尽量下载 Chromium

# 从已登录网页 Network 复制 Cookie 后，检查会话（不签到、不起浏览器）
$env:GLADOS_AUTH_MODE='cookie'
$env:GLADOS_CHECKIN_METHOD='api'
$env:GLADOS_COOKIE='gld:sess=...; gld:sess.sig=...'
& .venv\Scripts\python.exe checkin.py --dry-run

# 真正签到（立即执行；--no-jitter 仅为兼容旧命令）
& .venv\Scripts\python.exe checkin.py --no-jitter

# 离线单测（不需要浏览器）
& .venv\Scripts\python.exe -B -m unittest -q
~~~

需要浏览器点击备选时，设 `GLADOS_CHECKIN_METHOD=browser`。
`--channel chrome`（或 `msedge`）让 Playwright 直接用系统里已装好的浏览器，
不必下载 Chromium；CI 上则用 `python -m playwright install --with-deps chromium`。
浏览器缓存默认写到 `.ms-playwright/`（已在 `.gitignore`），不污染用户目录。

`.venv/`、`artifacts/`、`__pycache__/` 同样已加入 `.gitignore`。

## 定时任务与保活

工作流支持手动 `Run workflow` 测试；`concurrency` 保证不会同时跑两个签到。
保活工作流每天北京时间 08:17 检查最近一次提交，距离上次提交至少 45 天时创建空提交。
空提交保活需要仓库 Settings → Actions → General → Workflow permissions 开启 Read and write。
