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
      ARTEX_BASIC_USER: artex-web
      ARTEX_BASIC_PASSWORD_FILE: /absolute/secrets/artex-basic
      ARTEX_TRANSFER_DIR: /absolute/artex-files
```

Windows 使用虚拟环境的 `.venv\Scripts\python.exe`。其他 MCP 客户端使用同样的 stdio `command`、`args`、`env` 配置。密码文件应只对运行 MCP 的账号可读。在 Hermes 重载 MCP 后，Agent 可按以下顺序使用：

1. 调用 `artex_status` 检查 Web 可达性、Basic Auth 和初始化状态。
2. 已设置管理员密码时调用 `artex_login`。全新实例且用户明确要求初始化时调用 `artex_initialize(confirm=true)`。两者使用配置的 `ARTEX_ADMIN_PASSWORD_FILE`。如果先收到 401，检查 Basic 用户和密码。
3. 调用 `artex_find_routes` 按路径或 Go handler 搜索操作，再调用 `artex_call`。状态检查可读实例版本号。

ARTEX 内置 Basic Auth 的 Cookie 和应用登录 JWT 由进程管理。能识别认证中间件拒绝的普通请求会续登后重试一次。上传遇到 401 时应先重新登录再重试。登出清除 MCP 的会话，ARTEX 发出的其他 JWT 仍按服务端有效期处理。

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
