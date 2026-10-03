# GLaDOS 自动签到

使用 Python requests，通过 GitHub Actions 每天北京时间 09:00 签到。
本版本参考 [pyx13638516490/glados_checkin](https://github.com/pyx13638516490/glados_checkin/blob/623e3dcd686ff11addaf628b6561ca7478c4f6d7/checkin.py)
已验证的请求流程重写：完整 Cookie 原样发送，先签到，再查询会员天数与积分；
默认请求头与朋友的实现一致（Chrome/126.0）。

## Cookie 配置

在自己仓库的 Settings → Secrets and variables → Actions → **Repository secrets**
中编辑 GLADOS_COOKIE。推荐使用以下第一种格式：

### 完整 Cookie 请求头（推荐）

登录 https://glados.cloud，按 F12 → Network，刷新页面，
在成功的 /api/user/status 请求的 Request Headers 中复制 Cookie 的值：

~~~text
koa:sess=完整会话值; koa:sess.sig=完整签名; 其他Cookie=原始值
~~~

不要复制响应中的 Set-Cookie 或 Cookie 列表中的 Domain、Path、Expires 属性。
完整请求头不会进行 HTML 解码、URL 解码或重新排序，附加字段和签名原样保留。
可以粘贴带 Cookie: 前缀的单行请求头，脚本会移除前缀。

### 分别复制的两个值

先复制 koa:sess 的值，再复制 koa:sess.sig 的值，用空格分隔：

~~~text
完整会话值 完整签名
~~~

支持两项之间的换行、聊天软件生成的 &#x20; 空格及整段外侧引号。
只在这种没有字段名的格式中解码 HTML 空格；Cookie 值本身保持不变。

### Cookie-Editor JSON

支持单个域名、单个账号的 Cookie-Editor 导出格式：

~~~json
[
  {"name": "koa:sess", "value": "完整会话值", "domain": "glados.cloud"},
  {"name": "koa:sess.sig", "value": "完整签名", "domain": "glados.cloud"}
]
~~~

不要混合不同域名或多个账号的 Cookie。

日志仅显示输入与请求头的长度及 SHA-256 前 12 位指纹，不显示 Cookie。
输入完整请求头时两组指纹应一致。更新 Secret 后运行新的任务；
指纹不变表示实际传入的内容未变化（或更新的是其他仓库/Secret）。

## 请求流程与错误处理

1. 默认直接 POST https://glados.cloud/api/user/checkin，发送 {"token":"glados.cloud"}。
2. 明确成功或今日已签到后，再查询 /api/user/status 和 /api/user/points。
3. 状态或积分查询失败只产生警告，不会把已经成功的签到判为失败。

默认使用与朋友相同的 Session 请求头、JSON 序列化和 data= 发送方式。
仅连接异常、超时、HTTP 429 或 5xx 最多重试三次，默认间隔 10、20 秒。
主域名网络重试仍失败时，尝试 glados.rocks、glados.network。
-2/没有权限、HTTP 401/403、token 错误和未知结果不会反复重试。
code=4/reason=device-mismatch 会单独提示登录设备与签到设备不匹配。
认证失败本身不能区分 Cookie 过期、签名不配对、Secret 内容或账户会话限制。

退出码：0 成功/今日已签到，1 配置或认证等错误，2 临时网络/服务故障。
工作流直接保留脚本退出码，不使用 continue-on-error 隐藏失败。

## 可选配置

在 Settings → Secrets and variables → Actions → **Variables** 配置：

| Variable | 默认值与用途 |
| --- | --- |
| GLADOS_BASE_URL | https://glados.cloud；必须是受支持的官方 HTTPS 域名 |
| GLADOS_CHECKIN_TOKEN | 默认取请求域名；cloud 的旧值 glados.one 自动改为 glados.cloud |
| GLADOS_USER_AGENT | 与参考实现相同的 Chrome/126.0；可填浏览器实际 User-Agent |
| GLADOS_DOMAIN_FALLBACK | 1；设为 0 关闭网络故障时的备用域名 |
| GLADOS_RETRY_DELAY_SECONDS | 不填使用 10、20 秒；可设为 0 到 60 的固定秒数 |

空的可选变量不会覆盖默认值。Cookie 始终放 Secret，不放 Variables 或代码。

## 本地运行与验证

~~~powershell
pip install -r requirements.txt
$env:GLADOS_COOKIE='koa:sess=完整值; koa:sess.sig=完整签名'
python -u checkin.py
python -B -m unittest -v
~~~

GitHub Actions 使用 Python 3.12，运行前自动执行不连接真实账户的回归测试，
支持手动 Run workflow。任务最多运行 15 分钟，并避免同仓库签到任务并发。

## 失败邮件与仓库保活

保留可选失败邮件。认证/配置错误立即通知；网络故障重试耗尽后通知。
在 Repository secrets 设置以下字段；缺少配置时跳过邮件，签到仍返回失败。

| Secret | 说明 |
| --- | --- |
| SMTP_HOST | SMTP 主机 |
| SMTP_PORT | 默认 587；SSL 通常为 465 |
| SMTP_USERNAME | 登录用户名 |
| SMTP_PASSWORD | SMTP 密码或应用专用密码 |
| SMTP_USE_SSL | 465 通常设 true；默认 false 使用 STARTTLS |
| MAIL_TO | 收件人 |
| MAIL_FROM | 可选；默认使用 SMTP 用户名 |

邮件只包含脱敏的错误说明与 Actions 链接。

保活工作流保持原配置：每天检查提交时间，达到 45 天时创建空提交。
它需要仓库 Settings → Actions → General → Workflow permissions 中的
Read and write permissions，工作流同时声明 contents: write。
