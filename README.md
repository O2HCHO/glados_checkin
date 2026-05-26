# GLaDOS 自动签到

这个仓库提供一个基于 Python `requests` 的 `glados.cloud` 自动签到脚本，并通过 GitHub Actions 每天定时执行。

## 技术方案

脚本按固定两步执行：

1. 调用签到接口 `POST https://glados.cloud/api/user/checkin`
2. 调用状态接口 `GET https://glados.cloud/api/user/status`，读取 `data.leftDays`

鉴权方式直接使用完整的 Cookie 字符串，从环境变量 `GLADOS_COOKIE` 读取，并原样放入请求头 `Cookie`。这适合包含 `koa:sess` 和 `koa:sess.sig` 的场景，不需要在脚本里单独拆分。

默认签到请求体为：

```json
{
  "token": "glados.one"
}
```

如果后续站点调整了 token 或域名，可以通过环境变量覆盖：

- `GLADOS_BASE_URL`
- `GLADOS_CHECKIN_TOKEN`

## 目录结构

```text
.
|-- .github
|   `-- workflows
|       `-- checkin.yml
|-- checkin.py
|-- requirements.txt
`-- README.md
```

## 本地运行

1. 安装依赖

```bash
pip install -r requirements.txt
```

2. 设置环境变量

```bash
export GLADOS_COOKIE='koa:sess=xxx; koa:sess.sig=yyy'
```

Windows PowerShell:

```powershell
$env:GLADOS_COOKIE='koa:sess=xxx; koa:sess.sig=yyy'
```

3. 执行脚本

```bash
python checkin.py
```

## GitHub Actions 配置

工作流文件为 `.github/workflows/checkin.yml`。

- 触发时间：`0 1 * * *`
- 含义：每天 `01:00 UTC`
- 换算为北京时间：每天早上 `09:00`（UTC+8）

同时保留了 `workflow_dispatch`，可以在 GitHub 页面手动点一次运行，方便测试。

## GitHub 上的配置步骤

1. 在 GitHub 新建仓库，并把本目录文件推送上去。
2. 进入仓库页面的 `Settings`。
3. 打开 `Secrets and variables` -> `Actions`。
4. 点击 `New repository secret`。
5. 名称填写 `GLADOS_COOKIE`。
6. 值填写浏览器里复制出来的完整 Cookie 字符串，至少要包含：

```text
koa:sess=...; koa:sess.sig=...
```

7. 保存后进入 `Actions` 页面。
8. 首次可以手动执行 `GLaDOS Checkin` 工作流，确认日志正常。

## 日志输出

脚本会在控制台打印：

- 签到接口 URL
- 签到接口 HTTP 状态码
- 签到接口返回 JSON
- 状态接口 HTTP 状态码
- 状态接口返回 JSON
- 当前剩余天数

## Cookie 获取说明

在浏览器登录 `https://glados.cloud` 后：

1. 打开开发者工具
2. 进入 `Application` 或 `Storage`
3. 找到站点 Cookie
4. 复制完整 Cookie 字符串
5. 确保其中包含 `koa:sess` 和 `koa:sess.sig`

建议不要只复制单个字段，直接复制整段 Cookie 并写入 `GLADOS_COOKIE`。
