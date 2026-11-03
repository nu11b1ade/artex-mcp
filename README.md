# ARTEX Web MCP

在外部 Agent 所在机器运行的 stdio MCP。通过 ARTEX 的 Web 地址管理应用，适用于 Docker、二进制和反向代理部署。所需权限是 Web 访问和 ARTEX 管理员凭据。全程使用 ARTEX 自身的认证流程。

接口目录基于 [nu11b1ade/ARTEX @ 380930b](https://github.com/nu11b1ade/ARTEX/commit/380930b08002102ea5220649de3a6842f32e6fc6)。`routes.json` 收录源码的 **315 个 HTTP 方法与路由组合**，包含动态注册的流量证据和旁路追问。MCP 提供 **10 个工具**，目录按需查询。

## 安装和连接 Hermes

在 **Agent 所在机器**解压后执行：

```bash
cd artex-mcp
uv sync --locked
```

将下列配置合并到 Hermes 的 `config.yaml`。路径换成你机器上的实际路径。未开启 ARTEX 内置 Basic Auth 时，去掉两项 Basic 配置。需要文件上传或下载时，先创建专用传输目录。

```yaml
mcp_servers:
  artex:
    command: /absolute/artex-mcp/.venv/bin/python
    args: [/absolute/artex-mcp/artex_mcp.py]
    timeout: 180
    env:
      ARTEX_URL: https://artex.example.com
      ARTEX_ADMIN_PASSWORD_FILE: /absolute/secrets/artex-admin
      ARTEX_BASIC_USER: YOUR_BASIC_USERNAME
      ARTEX_BASIC_PASSWORD_FILE: /absolute/secrets/artex-basic
      ARTEX_TRANSFER_DIR: /absolute/artex-files
```

Windows 使用虚拟环境的 `.venv\Scripts\python.exe`。其他 MCP 客户端使用同样的 stdio `command`、`args`、`env` 配置。密码文件应只对运行 MCP 的账号可读。在 Hermes 重载 MCP 后，Agent 可按以下顺序使用：

1. 调用 `artex_status` 检查 Web 可达性、Basic Auth 和初始化状态。
2. 已设置管理员密码时调用 `artex_login`。全新实例且用户明确要求初始化时调用 `artex_initialize(confirm=true)`。两者使用配置的 `ARTEX_ADMIN_PASSWORD_FILE`。如果先收到 401，检查 Basic 用户和密码。
3. 调用 `artex_find_routes` 按路径或 Go handler 搜索操作，再调用 `artex_call`。状态检查可读实例版本号。

ARTEX 内置 Basic Auth 的 Cookie 和应用登录 JWT 由进程管理。能识别认证中间件拒绝的普通请求会续登后重试一次。上传遇到 401 时应先重新登录再重试。登出清除 MCP 的会话，ARTEX 发出的其他 JWT 仍按服务端有效期处理。

## 安装和连接 Pi

在运行 Pi 的机器上安装依赖。已经克隆仓库时，从 `uv sync` 开始。

```bash
git clone https://github.com/nu11b1ade/artex-mcp.git
cd artex-mcp
uv sync --locked
```

将 `artex` 条目合并到 `~/.pi/agent/mcp.json` 的 `mcpServers` 下，保留已有服务器。以下示例对应启用了 Basic Auth、使用自签证书的 ARTEX 实例。所有文件路径都是 **Pi 所在机器**上的绝对路径。

```json
{
  "mcpServers": {
    "artex": {
      "command": "/absolute/artex-mcp/.venv/bin/python",
      "args": ["/absolute/artex-mcp/artex_mcp.py"],
      "timeout": 180,
      "env": {
        "ARTEX_URL": "https://artex.example.com",
        "ARTEX_CA_FILE": "/absolute/certs/artex-ca.pem",
        "ARTEX_BASIC_USER": "YOUR_BASIC_USERNAME",
        "ARTEX_BASIC_PASSWORD_FILE": "/absolute/secrets/artex-basic",
        "ARTEX_ADMIN_PASSWORD_FILE": "/absolute/secrets/artex-admin"
      }
    }
  }
}
```

- 使用系统已信任的证书时可省略 `ARTEX_CA_FILE`。未开启 Basic Auth 时可省略两项 Basic 配置。将 `YOUR_BASIC_USERNAME` 替换为你的 Basic 网关用户名。应用登录用户名由 MCP 按 ARTEX 接口要求发送为大写 `ARTEX`。
- 支持内置 MCP 的 Pi 可运行 `pi mcp list` 查看 stdio 连接，再在会话中用 `/reload` 读取修改后的配置，通过 `/mcp` 查看工具。若已使用 `pi-mcp-adapter`，将同一个 `mcpServers.artex` 条目合并到它当前读取的配置，再重启 Pi。参考 [Pi MCP 文档](https://pi.dev/docs/latest/mcp) 和 [适配器配置文档](https://github.com/nicobailon/pi-mcp-adapter/blob/main/docs/configuration.md)。
- stdio 连接成功后，让 Pi 依次调用 `artex_status`、`artex_login`、`artex_call(method="GET", route="/api/tasks")`。最后一步可验证 Pi、MCP 和 ARTEX 的认证链路。

### Pi 中的 TLS 和 Basic Auth 排障

如果 `pi mcp list` 显示 MCP 已连接，而 `artex_status` 报 transport 失败，先检查 MCP 到 ARTEX 的 HTTPS。自签叶证书用已从可信渠道取得的服务端证书 PEM，私有 CA 签发的证书用 CA PEM。把文件放在 Pi 机器上，在 MCP 的 `env` 中设置 `ARTEX_CA_FILE`，并确认 `ARTEX_URL` 中的域名或 IP 在证书 SAN 里。修改配置后重新加载 Pi。

若已收到 HTTP 401，再检查认证。ARTEX 内置 Basic 网关发放的是 Cookie。直连时按顺序完成这三步：

1. 带 Basic 凭据请求 `GET /api/basic-auth/check`，保存响应的网关 Cookie。
2. 带同一个 Cookie 请求 `POST /api/auth/login`，用应用账号 `ARTEX` 和管理员密码换取 JWT。
3. 请求 `GET /api/tasks` 时，同时发送网关 Cookie 和 `Authorization: Bearer <JWT>`。MCP 会自动保留 Cookie 并为业务请求附上 Bearer。

可用下面的命令在 Pi 机器上只读验证链路。先替换网关用户名，`curl` 会提示输入 Basic 密码。应用管理员密码从本地文件读取。

```bash
(
  set -e
  set -o pipefail
  umask 077
  url=https://artex.example.com
  ca=/absolute/certs/artex-ca.pem
  basic_user=YOUR_BASIC_USERNAME
  admin_file=/absolute/secrets/artex-admin
  jar=$(mktemp)
  trap 'rm -f "$jar"' EXIT

  curl -fsS --cacert "$ca" --user "$basic_user" --cookie-jar "$jar" \
    "$url/api/basic-auth/check"

  token=$(
    jq -Rs '{username:"ARTEX",password:(rtrimstr("\n") | rtrimstr("\r"))}' "$admin_file" |
      curl -fsS --cacert "$ca" --cookie "$jar" --cookie-jar "$jar" \
        -H 'Content-Type: application/json' --data-binary @- "$url/api/auth/login" |
      jq -er '.token | strings | select(length > 0)'
  )

  printf 'Authorization: Bearer %s\n' "$token" |
    curl -fsS --cacert "$ca" --cookie "$jar" --header @- "$url/api/tasks"
)
```

## 工具和使用示例

| 工具 | 用途 |
| --- | --- |
| `artex_status`、`artex_login`、`artex_initialize`、`artex_logout` | 从无会话开始检查、登录、初始化、清理会话 |
| `artex_change_password`、`artex_configure_basic_auth` | 修改应用密码或 Basic Auth。新密码通过 `ARTEX_NEW_ADMIN_PASSWORD_FILE` 或 `ARTEX_NEW_BASIC_PASSWORD_FILE` 读取。成功后更新持久凭据配置，避免重启后仍使用旧密码 |
| `artex_find_routes` | 搜索接口目录。支持按方法筛选和分页，并返回固定提交的源码位置 |
| `artex_call` | 执行注册的 JSON、表单或文件请求 |
| `artex_read_events` | 限时读取日志、任务活动、旁路追问和更新进度的 SSE |
| `artex_browser` | 可选的隔离 Chromium。操作只在浏览器保存的 UI 状态或查看页面 |

以下是工具参数示例：

```json
{"query":"task control","method":"POST"}
```

```json
{"method":"GET","route":"/api/tasks"}
```

```json
{"method":"POST","route":"/api/tasks/{id}/control","path_params":{"id":"TASK_ID"},"body":{"action":"pause"}}
```

```json
{"method":"POST","route":"/api/skills/upload","query":{"overwrite":false},"upload_files":["example.zip"]}
```

```json
{"method":"GET","route":"/api/workspace/download","query":{"path":"reports/report.pdf"},"save_as":"report.pdf"}
```

```json
{"route":"/api/logs/stream","query":{"since":0},"seconds":5,"max_events":10}
```

任务、排程、会话、旁路追问、资产、漏洞、流量证据、Repeater 发包、审批、统计、工作区、Skills、MCP、LLM、通知及更新等，均由对应的 Web 路由执行。业务请求字段以返回的 Go handler 和 [同一提交的前端客户端](https://github.com/nu11b1ade/ARTEX/blob/380930b08002102ea5220649de3a6842f32e6fc6/web/src/lib/api.ts) 为准。

- 大结果默认返回截断标记和预览。任何成功的非 SSE 响应都能用 `save_as` 保存完整内容。目标路径相对传输目录，默认拒绝覆盖。使用 `preview_chars` 调整模型看到的返回长度。
- SSE 单次有时间、事件数和体积上限。任务活动可继续传 `last_event_id`。日志用上一批返回的 `since` 继续，旁路追问重连可能再次收到最新快照。
- 该 MCP 拥有配置凭据对应的管理员权限。业务路由可产生真实副作用，包括执行 Repeater 请求和修改审批或 MCP 配置。调用者应约束授权范围，并把网页、日志和流量正文当作数据处理。测试时优先使用只读接口。
- 版本管理限于 Web 的二进制更新。Docker 镜像重建、容器故障恢复及宿主机备份不在 Web 管理能力之内。

## 可选 Chromium

```bash
uv sync --locked --extra browser
uv run --locked --extra browser playwright install chromium
```

在同一个 MCP 中调用 `artex_browser(action="sign_in")`，再用 `navigate`、`snapshot`、`click`、`fill` 等操作实际页面。页面定位参数采用 Playwright locator，例如 `text=系统配置`。它支持标签页切换、截图、拖放、上传和下载。浏览器模式可处理 Repeater 本地工作区、主题等没有服务端接口的功能。

默认使用独立的临时 Chromium 会话。设置 `ARTEX_BROWSER_PROFILE` 为一个 **专用**配置目录可保留浏览器状态。它不会读取你平时使用的浏览器配置。若设了该目录，需保护其中的登录态和 Web 数据。可选 `ARTEX_CHROMIUM_EXECUTABLE` 指向本机 Chromium。Chromium 需要可用的浏览器沙箱和系统依赖。自签证书也需在 Chromium 侧建立信任。

## 配置细节与源码更新

- 密码也可由不带 `_FILE` 的同名环境变量提供。两种形式选其一。
- 对自签名或私有 CA 的 HTTPS，`ARTEX_CA_FILE` 指向 PEM 信任文件，域名仍需与证书匹配。API 默认要求 HTTPS 或回环 HTTP。受信私网的 HTTP 可显式设置 `ARTEX_ALLOW_HTTP=1`。
- `ARTEX_HTTP_TIMEOUT` 默认 120 秒。MCP 客户端的工具超时设得比它长。传输上限 `ARTEX_MAX_FILE_BYTES` 默认 256 MiB，ARTEX 自身也可能对个别上传接口限制体积。
- 适配 ARTEX 源码中的内置 Basic Auth。若入口另外叠加了独立反代认证、SSO 或验证码，入口需允许这个 MCP 客户端按既有认证方式访问，或使用可达的私网 Web 地址。
- 路由以固定提交的 Web API 为准。若服务器实际镜像使用其他版本，先用 `artex_status` 查版本，再从对应的 ARTEX checkout 执行 `python sync_routes.py /path/to/ARTEX`。生成器会对未知的路由注册方式报错。升级后仍须复核请求字段和认证行为。
- 可运行 `uv run --locked python -m unittest discover -s tests -v`。测试覆盖目录路由参数、MCP stdio、双层认证、初始化、续登、写操作、上传下载和 SSE。可选 Chromium 集成测试需提供 `TEST_CHROMIUM_EXECUTABLE`。
