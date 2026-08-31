# AI 深度搜索助手 · Visual Browser Agent for Deep Search

一个能「看见自己干活」的浏览器 Agent。你用口语把目标告诉它，它打开真实的 Edge 浏览器，像隐形的人一样移动鼠标、点击、输入，一步步把事办成，最后用一段话汇报结果。

## 两种模式
- **⚡ 快速操作**：给它一个网页动作目标（去哪点、打字、发送），它像真人一样打开浏览器一步步办成，干完一句话汇报。可控制任意本地/外网网页（如 `http://localhost:4098`、OpenCode 网页版）。
- **🔬 深度研究**（**寻找 → 深度搜索 → 提取抓取**）：① 去必应搜你的问题；② 从结果页**批量抠出候选来源链接**（适配 Bing / Baidu / Google，自动滤掉搜索引擎自身页和登录页）；③ 逐个打开，先砍掉导航·广告·页脚·评论区，再用**文字密度算法**（正文段落字数 − 链接字数×0.5）挑出真正的正文容器；④ 汇总成**带 `[1][2]` 引用编号的研究报告**（Markdown），自动**截图存档**，存到本机 `文档/AI深度搜索助手_报告/`。
  - 这条链路不走"逐点击决策"的慢循环（抓一个来源要点十几步、还容易点歪），而是**几秒一个来源**，抓得多也抓得准。

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

**Windows 一键启动**：双击 `启动AI深度搜索助手.bat`（自动开浏览器，关掉黑窗口即停服务）。

```bash
python server.py
# 打开 http://localhost:8080
```

**「看得见浏览器」开关（默认开）**：勾上会弹出真实 Edge 窗口，**你能亲眼看到鼠标自己移动、点击、打字**（就是那个"隐形手"）；取消勾选则后台无头静默跑，屏幕上不弹窗口，只看日志。

两种模式、深度研究、带来源报告、截图存档全部可用；日志与报告通过 SSE 实时推到前端。

## 为什么它能「真干活」（几个关键防呆）
模型驱动浏览器最容易死在几个坑上，这里都堵了：
- **`done` 降级**：模型常把"当前这一小步做完了"误报成 `done`。只要计划还有后续步骤，就降级为"这步完成、继续下一步"，绝不让它干 1 步就收工。
- **输入长度截断**：模型偶尔把一整段文字塞进 `value`，逐字敲会敲到天荒地老 —— 截断到 200 字、delay 压到 15ms。
- **`page.evaluate` 硬超时**：Playwright 原生 evaluate **不受任何 timeout 控制**，页面 JS 一卡死整个 agent 就永久阻塞。这里用线程池套了 10s 硬超时。
- **15 分钟看门狗**：单次任务到点自动收尾。
- **元素双向匹配**：模型说"搜索框"、页面上写"搜索"也能对上；可访问性树漏掉的搜索框，再补一轮 DOM 扫描（元素数 9 → 60）。
- **结束必定回 `idle`**：任务无论成功失败都推 `STATE::idle`，前端状态不会永远卡在"搜索中"。

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
