# Gemini-Remote

> 通过 MCP 把任务交给本机浏览器里已登录的 Gemini 网页版，异步取回结果。

## 项目简介 / Overview

MCP 客户端调用 `gemini_send` 提交任务，立即拿到 job_id。任务经 Gateway 转到你自己电脑上的 Relay，再由浏览器扩展在已登录的 `gemini.google.com` 页面里输入并发送。完成后，回复正文和 Canvas 文档作为结果返回，调用方用 `gemini_status` / `gemini_result` 查询。

设计原则：

- Gemini 登录态只留在浏览器里，Gateway、Relay 和扩展都不读取、不保存它。
- 每个任务只发送一次；无法确认是否已发出时标记为不确定，不自动重发。
- 页面相关的选择器集中在一个 adapter 里，Gemini 改版只改这一处。

当前状态：**开发中。** Gateway 已可在本机运行：三个 MCP 工具可用，任务存进 SQLite 排队，Relay 通过签名 WebSocket 领取任务、回报结果。Relay 和浏览器端的发送链路还没有实现，所以任务暂时只会停在排队状态。

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
| `src/gemini_remote/` | Python 包：CLI、配置、Job 合同与签名信封、SQLite 任务存储、Gateway |
| `adapters/gemini/` | 与 Gemini 页面结构相关的选择器和 adapter，改版只改这里 |
| `extensions/chromium/` | 扩展源文件与 manifest；构建产物在 ignored `dist/` |
| `tools/` | 构建脚本 |
| `tests/unit/`、`tests/browser/` | Python 单测；基于 jsdom 合成页面的浏览器侧测试，不碰真实 Gemini |

```powershell
uv python install 3.13
uv sync --group dev
uv run gemini-remote --version
uv run pytest -q
pnpm install
pnpm test
```

### 本地配置

所有与具体安装相关的设置都放在仓库根目录的 ignored `.env` 里，代码和 git 里不写死：总线凭证、端口、默认发送模式、扩展签名公钥（决定扩展 ID）。进程环境变量优先于 `.env`。

```powershell
uv run gemini-remote init-local
```

这条命令生成 `.env`：随机凭证、默认端口（Gateway 18797，Relay 18776）、默认模式 `task`，以及一把新的扩展公钥（私钥不保存）。文件已存在时拒绝执行，也不会打印 token。可调整的项见 `.env.example`。

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

### 构建与加载扩展

```powershell
pnpm build:extension
```

构建时从 `.env` 读取扩展公钥和 Relay 端口，写进 `extensions/chromium/dist/manifest.json`，并在 `dist/extension-origin.txt` 输出本机扩展的 origin。仓库里的 `manifest.json` 只是模板。

在 Edge 的 `edge://extensions` 打开开发者模式，选择「加载解压缩的扩展」并指向 `extensions/chromium/dist/`。打开 `https://gemini.google.com/app` 后，页面 console 里应出现 `[gemini-remote] <版本> preflight ok`。当前扩展只做只读检查，不输入、不点击。

## License

MIT，见 [LICENSE](LICENSE)。
