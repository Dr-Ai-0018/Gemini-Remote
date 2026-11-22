# Gemini-Remote

> 通过 MCP 把任务交给本机浏览器里已登录的 Gemini 网页版，异步取回结果。

## 项目简介 / Overview

MCP 客户端调用 `gemini_send` 提交任务，立即拿到 job_id。任务经 Gateway 转到 owner 电脑上的 Relay，再由浏览器扩展在已登录的 `gemini.google.com` 页面里输入并发送。完成后，回复正文和 Canvas 文档作为结果返回，调用方用 `gemini_status` / `gemini_result` 查询。

设计原则：

- Gemini 登录态只留在浏览器里，Gateway、Relay 和扩展都不读取、不保存它。
- 每个任务只发送一次；无法确认是否已发出时标记为不确定，不自动重发。
- 页面相关的选择器集中在一个 adapter 里，Gemini 改版只改这一处。

当前状态：**设计阶段，尚无可运行代码。** 调研结论见 [design/](design/README.md)。

## 使用 / Usage

尚未实现。

## 开发 / Development

尚未确定技术栈与命令。预期：Python（Gateway / Relay，uv 管理）+ Chromium MV3 扩展（原生 JavaScript）。

## License

MIT，见 [LICENSE](LICENSE)。
