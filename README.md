# AI 深度搜索助手 · Visual Browser Agent for Deep Search

一个能「看见自己干活」的浏览器 Agent。你用口语把目标告诉它，它打开真实的 Edge 浏览器，像隐形的人一样移动鼠标、点击、输入，一步步把事办成，最后用一段话汇报结果。

## 两种模式
- **⚡ 快速操作**：给它一个网页动作目标（去哪点、打字、发送），它像真人一样打开浏览器一步步办成，干完一句话汇报。可控制任意本地/外网网页（如 `http://localhost:4098`、OpenCode 网页版）。
- **🔬 深度研究**：给它一个问题，它会跨多个网页查资料、**抓取每个页面的正文**带回给模型读懂，最后生成一份**带来源引用的研究报告**（Markdown），并自动**截图存档**。报告保存在本机 `文档/AI深度搜索助手_报告/`，UI 内可一键打开。

## What it does
- **听懂人话**：口语、省略主语、跳跃的需求，自动拆成浏览器操作步骤（补全常识：B站→bilibili.com 等）。
- **真浏览器操作**：Playwright 驱动系统已装的 Edge，真实可见的鼠标移动与点击，不是无头模拟。
- **深度研究 / 正文抓取**：进入内容页自动抓取正文（去噪），多来源汇总；深度研究模式产出带来源链接的研究报告。
- **多厂商模型**：24+ 厂商预设（China / Global / Local / Custom），纯 OpenAI 兼容适配，不绑定任何一家。
- **推理模型兼容**：用 deepseek-reasoner 这类推理模型当「大脑」，自动剥离 `<think>` 思考块再解析。
- **key 只存本机**：API Key 存在浏览器 localStorage；二进制绝不内置、绝不分发。
- **最终汇报**：快速操作用一句话汇报；深度研究用一份结构化报告（结论 + 要点 + 来源）汇报。

## Quick start
```bash
pip install -r requirements.txt
playwright install msedge      # 或确保本机已装 Microsoft Edge
python visual_agent.py
```
可选参数：加 `--headless` 无界面运行。

## 本地服务模式（推荐 · 浏览器打开即用）
不依赖原生窗口、不依赖 PyInstaller：起一个本地 HTTP 服务，浏览器访问即可。
```bash
python server.py
# 打开 http://localhost:8080
```
UI、快速操作、深度研究、带来源报告全部可用；Agent 在后端无头驱动真实 Edge，日志/报告通过 SSE 实时推到前端。

## 打包成 exe（可选）
```bash
pip install pyinstaller
pyinstaller --onefile --noconsole --collect-all playwright \
  --hidden-import webview --hidden-import pythonnet --hidden-import clr_loader \
  visual_agent.py
```
产物在 `dist/`。**二进制不含任何 key**；本机若装有 OpenClaw 且设了 `OPENCLAW_STATE_DIR`，会自动填入 Agnes key，开箱即用。

## 支持的厂商（24+）
- **China**：Agnes, SiliconFlow, DeepSeek, Zhipu GLM, Moonshot Kimi, Tongyi Qwen, Volcengine Doubao, Baidu Qianfan, StepFun, Lingyi Wanwu, Tencent Hunyuan
- **Global**：OpenRouter, OpenAI, Anthropic, Google Gemini, xAI Grok, Groq, Mistral, Together, Fireworks, DeepInfra, Cerebras
- **Local**：Ollama, LM Studio（免 key）
- **Custom**：任意 OpenAI 兼容端点

## 安全与边界（请辩证看待）
- **能力**：可自动化操作任意网页，包括已登录态的站点。请只在你有权操作的账号与页面上使用。
- **隐私**：API Key 仅存本机浏览器 localStorage；`local_key()` 只在本机读取你已有的 OpenClaw 配置，不写入二进制、不分发。仓库源码不含任何个人密钥或绝对路径。
- **风险**：模型决策并非 100% 可靠，复杂页面可能点错。关键操作建议人工盯审。`stuck>=4` 自动跳步防卡死。

## 架构
规划层 `plan_task`（按模式拆 2-8 步）→ 执行层 `run_agent`（逐子任务 `agnes_decide` 决策；深度研究模式每到一个内容页调用 `read_page_text` 抓正文入 `notes`）→ 快速操作用 `agnes_final_answer` 一句汇报，深度研究用 `agnes_research_report` 基于 `notes` 生成带来源引用的 Markdown 报告并落盘。UI 用 pywebview 原生窗口（浅色专业风、模式/双视图），JS 经 `window.pywebview.api` 与 Python 双向通信。

## License
MIT
