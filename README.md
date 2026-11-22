# Gemini-Remote

> 通过 MCP 把任务交给本机浏览器里已登录的 Gemini 网页版，异步取回结果。

## 项目简介 / Overview

MCP 客户端调用 `gemini_send` 提交任务，立即拿到 job_id。任务经 Gateway 转到你自己电脑上的 Relay，再由浏览器扩展在已登录的 `gemini.google.com` 页面里输入并发送。完成后，回复正文和 Canvas 文档作为结果返回，调用方用 `gemini_status` / `gemini_result` 查询。

设计原则：

- Gemini 登录态只留在浏览器里，Gateway、Relay 和扩展都不读取、不保存它。
- 每个任务只发送一次；无法确认是否已发出时标记为不确定，不自动重发。
- 页面相关的选择器集中在一个 adapter 里，Gemini 改版只改这一处。

当前状态：**开发中。** Gateway 和 Relay 已可在本机运行：三个 MCP 工具可用，任务存进 SQLite 排队；Relay 通过签名 WebSocket 领取任务、先落盘再确认，再交给本机扩展，扩展回报的结果经 outbox 送回 Gateway。扩展是通用构建，装好后在 Gemini 页面的悬浮面板里用配对码连上 Relay；它还不会操作页面（下一阶段），所以任务暂时会停在等待浏览器的状态。

## 免责声明 / Disclaimer

- 本项目仅供学习和技术交流使用，是非官方项目，与 Google 没有任何关联，也未获得其认可。
- 它通过浏览器扩展操作你自己已登录的 Gemini 网页。使用前请自行确认你的用法符合 Google 服务条款、Gemini 相关使用政策，以及你所在地的法律法规。
- 使用本项目产生的一切后果，包括但不限于账号受限或被封禁、数据丢失、违反平台规则，均由使用者自行承担，作者和贡献者不承担任何责任。
- 本软件按 MIT 许可证「按原样」提供，不附带任何形式的担保。

This is an unofficial project for learning and research only, and is not affiliated with or endorsed by Google. It automates your own logged-in Gemini web session; you are responsible for making sure your use complies with Google's terms, Gemini's usage policies and applicable laws. You use it entirely at your own risk; the authors and contributors accept no liability for any consequence, including account restrictions or bans. The software is provided "as is" under the MIT License, without warranty of any kind.

## 使用 / Usage

尚未实现。

## 开发 / Development

技术栈：Python 3.13（uv 管理）+ Chromium MV3 扩展（原生 JavaScript，pnpm 管理测试依赖）。

| 路径 | 职责 |
| --- | --- |
| `src/gemini_remote/` | Python 包：CLI、配置、Job 合同与签名信封、SQLite 存储、Gateway、Relay |
| `adapters/gemini/` | 与 Gemini 页面结构相关的选择器和 adapter，改版只改这里 |
| `extensions/chromium/` | 扩展源文件与 manifest；构建产物在 ignored `dist/` |
| `tools/` | 构建脚本 |
| `tests/unit/`、`tests/integration/`、`tests/browser/` | Python 单测；只在回环地址上跑的 Gateway / Relay 集成测试；基于 jsdom 合成页面的浏览器侧测试，不碰真实 Gemini |

```powershell
uv python install 3.13
uv sync --group dev
uv run gemini-remote --version
uv run pytest -q
pnpm install
pnpm test
```

### 本地配置

所有与具体安装相关的设置都放在仓库根目录的 ignored `.env` 里，代码和 git 里不写死：总线凭证、端口、默认发送模式。进程环境变量优先于 `.env`。浏览器扩展不读 `.env`，它和 Relay 的信任关系靠配对建立（见下文）。

```powershell
uv run gemini-remote init-local
```

这条命令生成 `.env`：随机凭证、默认端口（Gateway 18797，Relay 18776）、默认模式 `task`。文件已存在时拒绝执行，也不会打印 token。可调整的项见 `.env.example`。0.1.4 之前生成的 `.env` 里如果还有 `GEMINI_REMOTE_BROWSER_TOKEN`、`GEMINI_REMOTE_EXTENSION_KEY`，它们已不再使用，可以删掉（`doctor` 会提示）。

### 运行 Gateway（本机）

```powershell
uv run gemini-remote gateway
```

默认只监听 `127.0.0.1`，端口取 `GEMINI_REMOTE_GATEWAY_PORT`。可以用 `--host` / `--port` 覆盖。

| 路径 | 用途 | 鉴权 |
| --- | --- | --- |
| `/mcp/` | MCP（Streamable HTTP）：`gemini_send` / `gemini_status` / `gemini_result` | `Authorization: Bearer <GEMINI_REMOTE_CONTROL_TOKEN>` |
| `/ws/device` | Relay 连入，消息全部 HMAC 签名 | device token + device id |
| `/health` | 版本号，以及 Relay 是否在线 | 无 |

任务数据存放在 `GEMINI_REMOTE_DATA_DIR/gateway.sqlite3`。

### 运行 Relay（本机）

```powershell
uv run gemini-remote relay
```

Relay 主动连到 `GEMINI_REMOTE_GATEWAY_URL`，自己只在 `127.0.0.1:<GEMINI_REMOTE_RELAY_PORT>` 上监听，供扩展连入。

还没有配对过扩展时，Relay 启动后会在终端显示一个 6 位配对码（5 分钟有效，只能用一次）。在 Gemini 页面右下角点 **GR** 按钮，把配对码填进面板即可。之后扩展用配对时拿到的 token 自动连入。

```powershell
uv run gemini-remote pair          # 生成新的配对码（过期了、或换浏览器时）
uv run gemini-remote pair --reset  # 解除配对
```

扩展连接的校验：握手时 Origin 必须是 `chrome-extension://`，普通网页一律拒绝；之后必须是已配对的那个扩展、带着它的 token。Relay 只保存 token 和配对码的哈希；配对码连续输错 5 次即作废。同一时间只接受一个扩展连接。

收到的任务先写进 `GEMINI_REMOTE_DATA_DIR/relay.sqlite3` 再确认，每个任务最多交给浏览器一次。扩展回报的事件先进 outbox，等 Gateway 确认后才算送达；断线重连后会补发，两端都按 ID 去重。

### 诊断

```powershell
uv run gemini-remote doctor
```

只读检查：`.env` 里各角色的配置是否齐全，端口，是否已配对扩展，以及正在运行的 Relay 的状态——是否连上 Gateway、扩展是否在线，扩展和 Relay 的版本是否与当前代码一致。它不发起任何网络连接，也不打印凭证。一切正常时退出码为 0。

### 构建与加载扩展

```powershell
pnpm build:extension
```

构建产物在 `extensions/chromium/dist/`，是通用的：不读 `.env`，不含任何按安装定制的 key、端口或 token，谁构建出来都一样。构建时会核对 manifest、`pyproject.toml` 和 Python 包的版本号是否一致。

在 Edge 的 `edge://extensions`（或 Chrome 的 `chrome://extensions`）打开开发者模式，选择「加载解压缩的扩展」并指向 `extensions/chromium/dist/`。然后：

1. 运行 `uv run gemini-remote relay`，记下终端里的配对码。
2. 打开 `https://gemini.google.com/app`，右下角出现 **GR** 按钮；第一次会自动展开面板。
3. 输入配对码，面板显示「已连接 Relay」即可。Relay 端口不是 18776 时，先在面板里改端口。工具栏上的扩展图标打开的是同一个面板。

面板和按钮放在 closed shadow DOM 里，不影响 Gemini 页面本身。当前扩展只做只读的页面结构检查，不输入、不点击。

## License

MIT，见 [LICENSE](LICENSE)。
