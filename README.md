# Gemini-Remote

> 通过 MCP 把任务交给本机浏览器里已登录的 Gemini 网页版，异步取回结果。

## 项目简介 / Overview

MCP 客户端调用 `gemini_send` 提交任务，立即拿到 job_id。任务经 Gateway 转到 owner 电脑上的 Relay，再由浏览器扩展在已登录的 `gemini.google.com` 页面里输入并发送。完成后，回复正文和 Canvas 文档作为结果返回，调用方用 `gemini_status` / `gemini_result` 查询。

设计原则：

- Gemini 登录态只留在浏览器里，Gateway、Relay 和扩展都不读取、不保存它。
- 每个任务只发送一次；无法确认是否已发出时标记为不确定，不自动重发。
- 页面相关的选择器集中在一个 adapter 里，Gemini 改版只改这一处。

当前状态：**工程骨架阶段。** 已有 CLI、配置校验、Job 状态定义、集中的页面选择器和只读 preflight 扩展；Gateway、Relay、MCP 工具和发送链路尚未实现。调研结论见 [design/](design/README.md)。

## 使用 / Usage

尚未实现。

## 开发 / Development

技术栈：Python 3.13（uv 管理）+ Chromium MV3 扩展（原生 JavaScript，pnpm 管理测试依赖）。

| 路径 | 职责 |
| --- | --- |
| `src/gemini_remote/` | Python 包：CLI、配置、Job 合同 |
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
pnpm build:extension
pnpm test
```

`uv run gemini-remote init-local` 在当前目录生成 ignored `.env`，包含新的本机总线凭证；文件已存在时拒绝执行，也不会打印 token。配置项见 `.env.example`。

默认端口：Gateway 18797，Relay 18776。

加载扩展：在 Edge 的 `edge://extensions` 打开开发者模式，选择「加载解压缩的扩展」并指向 `extensions/chromium/dist/`。打开 `https://gemini.google.com/app` 后，页面 console 里应出现 `[gemini-remote] <版本> preflight ok`。当前扩展只做只读检查，不输入、不点击。

## License

MIT，见 [LICENSE](LICENSE)。
