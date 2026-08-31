#!/usr/bin/env python
# 可视化 Agent —— 真能看见它干活的浏览器 Agent。
# 说人话给它目标，它打开真实 Edge 浏览器，像隐形的人一样移动鼠标、点击、输入，
# 一步步完成；干完用一句人话告诉你结果。
# 多厂商模型适配器：不内置任何 key，用户自己选厂商、填自己的 key（本机记住）。
# 支持任意 OpenAI 兼容端点。打包后双击即用。
import os, json, re, sys, time, threading, queue
import webview
import requests
from playwright.sync_api import sync_playwright


log_q = queue.Queue()
stop_ev = threading.Event()
_busy = threading.Lock()

DEFAULT_CFG = {"base": "https://apihub.agnes-ai.com/v1", "model": "agnes-2.5-flash", "key": ""}


def local_key():
    """本机自动填 key：只读你本机已有的 OpenClaw 配置，不进二进制、不分发。
    优先读 OPENCLAW_STATE_DIR 环境变量（OpenClaw 桌面版已设），回退标准位置。
    其他机器没有这些路径则返回空，由用户自己填 key。"""
    candidates = []
    sdir = os.environ.get("OPENCLAW_STATE_DIR")
    if sdir:
        candidates.append(os.path.join(sdir, "openclaw.json"))
    candidates.append(os.path.expanduser("~/.openclaw/openclaw.json"))
    candidates.append(os.path.expanduser("~/.config/openclaw/openclaw.json"))
    for path in candidates:
        try:
            cfg = json.load(open(path, encoding="utf-8"))
            k = cfg.get("models", {}).get("providers", {}).get("agnes", {}).get("apiKey") or ""
            if k.strip():
                return k.strip()
        except Exception:
            continue
    return ""


def _chat(cfg, sys_p, usr, temperature=0.3, timeout=120, retries=3):
    base = (cfg.get("base") or "").rstrip("/")
    model = (cfg.get("model") or "").strip()
    key = (cfg.get("key") or "").strip()
    if not base:
        raise RuntimeError("未配置 Base URL")
    if not model:
        raise RuntimeError("未配置模型名")
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = "Bearer " + key
    elif "127.0.0.1" not in base and "localhost" not in base:
        log_q.put("⚠️ 这个模型源需要 API Key：在「API Key」框填好你自己的 key 再开始")
        raise RuntimeError("缺 API Key")
    last = None
    for _ in range(retries):
        try:
            r = requests.post(base + "/chat/completions",
                              headers=headers,
                              json={"model": model, "messages": [{"role": "system", "content": sys_p},
                                                                 {"role": "user", "content": usr}],
                                    "temperature": temperature},
                              timeout=timeout)
            r.raise_for_status()
            return r.json()["choices"][0]["message"]["content"]
        except Exception as ex:
            last = ex
            time.sleep(2)
    raise last


def _extract_json(txt):
    # 兼容推理模型：剥离 <think> 思考块和 markdown 围栏，只取 JSON
    txt = re.sub(r"<think>.*?</think>", "", txt, flags=re.S)
    txt = txt.replace("```json", "```")
    s = txt.find("{"); e = txt.rfind("}")
    if s >= 0 and e > s:
        return json.loads(txt[s:e+1])
    return None


def plan_task(goal, cfg):
    sys_p = ("你是任务规划器。用户会用口语、零散、省略主语、甚至跳跃的方式，描述想让浏览器Agent帮它做的事。"
             "请把意图拆成 2-6 个明确的浏览器操作步骤（中文），并补全常识与默认站点："
             "『知乎』→打开 zhihu.com；『B站/哔哩哔哩』→打开 bilibili.com；『必应』→打开 bing.com；"
             "『百度』→打开 baidu.com；『微博』→打开 weibo.com；『淘宝』→打开 taobao.com；『谷歌』→打开 google.com；"
             "『搜/查/找 X』→在搜索框输入X并回车；『点开第一个结果/第一篇/第一个视频』→点击结果列表第一项；"
             "『发送/提交/回复 X』→在输入框输入X内容并按回车发送；"
             "『下载』→找到下载按钮并点击；『登录』→点击登录入口。步骤要具体到可被点击执行。"
             "只返回一段 JSON，不要任何解释：{\"steps\":[\"步骤1\",\"步骤2\",...]}")
    usr = f"用户想干的事（口语，可能不完整）：{goal}\n\n明确步骤 JSON："
    try:
        txt = _chat(cfg, sys_p, usr)
        d = _extract_json(txt)
        if d:
            steps = d.get("steps") or []
            if steps:
                return [str(x) for x in steps]
    except Exception as ex:
        log_q.put(f"⚠️ 规划失败，退回单步模式：{ex}")
        return [goal]
    log_q.put("⚠️ 规划失败，退回单步模式")
    return [goal]


def agnes_decide(goal, elements, history, cfg):
    elist = "\n".join(f"{i+1}. [{e['role']}] {e['name']}" for i, e in enumerate(elements)) or "（无）"
    hist = "\n".join(history[-8:]) or "（还没开始）"
    sys_p = ("你是浏览器操作 Agent。用户想完成一个明确的『子任务』。请结合当前页面『可交互元素列表』决定下一步动作。"
             "注意：用户的原话可能口语化、省略主语、跳跃，请自行补全意图（例如『点开第一个』=点击结果列表第一项；"
             "『搜X』=在搜索框输入X回车；『进去看看』=点击进入该链接）。"
             "只返回一段 JSON，不要解释：{\"action\":\"click|type|enter|goto|scroll|subdone|done\","
             "\"idx\":元素编号(click/type用),\"value\":\"输入内容(type用)或按键名(enter用,默认Enter)\",\"url\":\"网址(goto用)\","
             "\"thought\":\"一句话说明这步要干什么\"}。"
             "规则：1) click 优先操作编号对应的元素；2) 填表/输入框打字用 type 并给 idx 和 value；"
             "3) 在输入框打完字需要提交/发送/搜索时，用 enter 按回车（先 type 再 enter）；4) 换网址用 goto；"
             "5) 这个子任务已达成（如已点到目标、已到目标页、消息已发出）返回 action=subdone 进入下一步；"
             "6) 所有子任务都完成或无法继续返回 action=done。")
    usr = (f"当前子任务：{goal}\n\n已完成步骤：\n{hist}\n\n当前页面可交互元素：\n{elist}\n\n下一步动作 JSON：")
    txt = _chat(cfg, sys_p, usr)
    d = _extract_json(txt)
    if d:
        return d
    return {"action": "done", "thought": "模型未返回可解析动作"}


def agnes_final_answer(goal, history, final_url, final_title, cfg):
    hist = "\n".join(history[-14:]) or "（没执行什么步骤）"
    sys_p = ("你是操作型 Agent，刚替用户在真实浏览器里干完活。现在用中文给用户一个最终回答："
             "一两句话说明干成了什么、结果是什么（比如打开了什么页面、点开了什么内容、看到了什么）。"
             "口语化、自然，像同事汇报，别列步骤清单，别提 JSON 或技术词。")
    usr = (f"用户当初的请求（口语）：{goal}\n\n实际执行的步骤：\n{hist}\n\n"
           f"最终所在页面：{final_title}（{final_url}）\n\n最终回答：")
    try:
        txt = _chat(cfg, sys_p, usr, temperature=0.5, timeout=60)
        return txt.strip() or "活干完了，看上面步骤。"
    except Exception:
        return "活干完了（总结没拿到，看上面步骤日志）。"


def grab_elements(page):
    items = []
    try:
        snap = page.accessibility.snapshot()
    except Exception:
        snap = None
    seen = set()

    def walk(node):
        if not isinstance(node, dict):
            return
        role = node.get("role", "")
        name = (node.get("name") or "").strip()
        if role in ("button", "link", "textbox", "combobox", "menuitem", "searchbox") and name and len(name) < 60:
            key = (role, name)
            if key not in seen:
                seen.add(key)
                items.append({"role": role, "name": name})
        for c in node.get("children", []) or []:
            walk(c)
    if snap:
        walk(snap)
    if not items:
        try:
            extra = page.evaluate("""() => {
                const out=[];
                document.querySelectorAll('button,a,input[type=text],input[type=search],textarea').forEach(el=>{
                    const t=(el.innerText||el.value||el.placeholder||'').trim();
                    if(t && t.length<60) out.push({role: el.tagName.toLowerCase(), name:t});
                });
                return out.slice(0,40);
            }""")
            items = extra
        except Exception:
            pass
    return items[:40]


def _find_center(page, name):
    return page.evaluate("""(nm) => {
        const sel="a,button,input,textarea,select,[role=button],[role=link]";
        const els=[...document.querySelectorAll(sel)];
        const el=els.find(e=>{const t=(e.innerText||e.value||e.placeholder||e.getAttribute('aria-label')||'').trim();return t && t.includes(nm);});
        if(!el) return null;
        el.scrollIntoView({block:'center'});
        const r=el.getBoundingClientRect();
        if(r.width===0 && r.height===0) return null;
        return {x:r.x+r.width/2, y:r.y+r.height/2};
    }""", name)


def human_click(page, el):
    c = _find_center(page, el["name"])
    if not c:
        return False
    page.mouse.move(c["x"], c["y"], steps=12)  # 鼠标可见地滑过去
    time.sleep(0.3)
    page.mouse.click(c["x"], c["y"])            # 真点下去
    return True


def human_type(page, el, value):
    ok = page.evaluate("""(nm) => {
        const els=[...document.querySelectorAll('input,textarea,[contenteditable=true]')];
        const el=els.find(e=>{const t=(e.innerText||e.value||e.placeholder||e.getAttribute('aria-label')||'').trim();return t && t.includes(nm);});
        if(!el) return false;
        el.scrollIntoView({block:'center'});
        try { el.value=''; } catch(e) {}
        el.focus();
        return true;
    }""", el["name"])
    if not ok:
        return False
    page.keyboard.type(value, delay=40)
    return True


def run_agent(goal, cfg, headless):
    history = []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(channel="msedge", headless=headless)
            page = browser.new_page()
            start = "https://www.bing.com"
            if "http" in goal:
                for u in ("https://", "http://"):
                    if u in goal:
                        start = goal[goal.find(u):].split()[0]
            page.goto(start, wait_until="load", timeout=30000)
            time.sleep(1.5)
            log_q.put(f"🌐 已打开：{page.url}")
            log_q.put(f"🤖 大脑模型：{cfg.get('model')}")
            log_q.put("🧠 正在把你的话翻译成执行计划…")
            steps = plan_task(goal, cfg)
            log_q.put("📋 计划 " + str(len(steps)) + " 步：" + " ｜ ".join(steps))
            si = 0
            stuck = 0
            last_sig = ""
            for step in range(1, 40):
                if stop_ev.is_set():
                    log_q.put("⏹ 已停止"); break
                if si >= len(steps):
                    log_q.put("✅ 所有计划步骤完成。"); break
                sub = steps[si]
                els = grab_elements(page)
                try:
                    dec = agnes_decide(sub, els, history, cfg)
                except Exception as e:
                    log_q.put(f"⚠️ 决策出错：{e}"); break
                act = dec.get("action", "done")
                thought = dec.get("thought", "")
                log_q.put(f"🧠 第{step}步（计划{si+1}/{len(steps)}：{sub}）：{thought}")
                if act == "done":
                    log_q.put("✅ Agent 判断全部完成。"); break
                elif act == "subdone":
                    si += 1
                    nxt = steps[si] if si < len(steps) else "（无更多）"
                    log_q.put(f"✓ 子任务完成，进入下一步：{nxt}")
                    stuck = 0
                    time.sleep(1)
                    continue
                elif act == "click":
                    idx = int(dec.get("idx", 0)) - 1
                    if 0 <= idx < len(els):
                        ok = human_click(page, els[idx])
                        log_q.put(f"👆 点击 [{els[idx]['role']}] {els[idx]['name']} -> {'成功' if ok else '失败'}")
                        history.append(f"点击 {els[idx]['name']}")
                    else:
                        log_q.put("⚠️ 编号越界，跳过")
                elif act == "type":
                    idx = int(dec.get("idx", 0)) - 1
                    val = dec.get("value", "")
                    if 0 <= idx < len(els):
                        ok = human_type(page, els[idx], val)
                        log_q.put(f"⌨️ 在 [{els[idx]['name']}] 输入：{val} -> {'成功' if ok else '失败'}")
                        history.append(f"在 {els[idx]['name']} 输入 {val}")
                    else:
                        log_q.put("⚠️ 编号越界，跳过")
                elif act == "enter":
                    key_name = (dec.get("value") or "Enter").strip() or "Enter"
                    page.keyboard.press(key_name)
                    log_q.put(f"⌨️ 按下 {key_name}（提交/发送）")
                    history.append(f"按 {key_name} 提交")
                elif act == "goto":
                    url = dec.get("url", "")
                    if url:
                        page.goto(url, timeout=30000); time.sleep(1.5)
                        log_q.put(f"🔗 跳转：{page.url}")
                        history.append(f"跳转 {url}")
                elif act == "scroll":
                    page.mouse.wheel(0, 600); time.sleep(0.6)
                    log_q.put("📜 滚动页面"); history.append("滚动")
                else:
                    log_q.put("✅ 结束"); break
                sig = page.url + "|" + str(len(history))
                if sig == last_sig:
                    stuck += 1
                else:
                    stuck = 0
                    last_sig = sig
                if stuck >= 4:
                    log_q.put("⏭ 连续无进展，跳过当前子任务")
                    si += 1
                    stuck = 0
                time.sleep(1.2)
            final_url = page.url
            try:
                final_title = page.title()
            except Exception:
                final_title = ""
            log_q.put("🏁 干活结束，整理结果…")
            ans = agnes_final_answer(goal, history, final_url, final_title, cfg)
            log_q.put("ANSWER::" + ans)
            if headless:
                browser.close()
    except Exception as e:
        log_q.put(f"❌ 运行出错：{e}")


# ---------------- 原生窗口 UI（pywebview） ----------------
HTML = r"""<!doctype html><html lang="zh"><head><meta charset="utf-8">
<style>
  *{box-sizing:border-box;margin:0}
  html,body{height:100%}
  body{font-family:-apple-system,"PingFang SC","Microsoft YaHei",sans-serif;background:#f6f7f9;color:#1c1f24}
  .wrap{height:100%;display:flex;flex-direction:column;padding:18px 18px 14px;gap:12px}
  .top{display:flex;align-items:center;justify-content:space-between}
  .top h1{font-size:17px;font-weight:700;letter-spacing:.2px}
  .top h1 em{font-style:normal;color:#2f6df6}
  .badge{font-size:12px;padding:4px 11px;border-radius:99px;background:#eef1f5;color:#7a8088;font-weight:600}
  .badge.run{background:#e8f6ec;color:#1e9e50}
  .badge.run i{display:inline-block;width:7px;height:7px;border-radius:50%;background:#22b45e;margin-right:6px;animation:pulse 1.2s infinite}
  @keyframes pulse{0%{opacity:1}50%{opacity:.3}100%{opacity:1}}
  .sub{font-size:12px;color:#8a9098;line-height:1.5;margin-top:-4px}
  .card{background:#fff;border:1px solid #e8eaee;border-radius:14px;padding:14px;box-shadow:0 1px 2px rgba(16,24,40,.04)}
  .row{display:flex;gap:8px;margin-top:10px;align-items:center}
  .row:first-child{margin-top:0}
  select,.inp{border:1px solid #dfe3e8;border-radius:10px;padding:9px 10px;font-size:13px;background:#fbfcfd;color:#333;outline:none;transition:border .15s,box-shadow .15s;min-width:0}
  select{flex:1}
  .inp{flex:1;font-family:inherit}
  .inp:focus{border-color:#2f6df6;box-shadow:0 0 0 3px rgba(47,109,246,.12);background:#fff}
  textarea{width:100%;height:56px;border:1px solid #dfe3e8;border-radius:10px;padding:10px 12px;font-size:14px;font-family:inherit;background:#fbfcfd;resize:vertical;outline:none;transition:border .15s,box-shadow .15s}
  textarea:focus{border-color:#2f6df6;box-shadow:0 0 0 3px rgba(47,109,246,.12);background:#fff}
  button{background:#2f6df6;color:#fff;border:0;border-radius:10px;padding:10px 18px;font-size:14px;font-weight:700;cursor:pointer;transition:background .15s}
  button:hover{background:#2559d6}
  button:active{transform:translateY(1px)}
  button.ghost{background:#eef1f5;color:#3a3f46;font-weight:600}
  button.ghost:hover{background:#e3e7ec}
  .log{flex:1;min-height:130px;overflow:auto;background:#0e1116;color:#c9d4c9;font-family:Consolas,"Courier New",monospace;font-size:12px;line-height:1.75;padding:12px 14px;border-radius:12px;white-space:pre-wrap;word-break:break-all}
  .ans{display:none;background:#f0f5ff;border:1px solid #d9e4ff;border-left:4px solid #2f6df6;border-radius:12px;padding:12px 14px}
  .ans .t{font-size:11px;color:#5b7bd8;font-weight:700;margin-bottom:6px;letter-spacing:.5px}
  .ans .c{font-size:14px;line-height:1.7;color:#1c2a4a}
</style></head><body><div class="wrap">
<div class="top"><h1>AI 深度搜索 <em>·</em> 会自己上网查资料的助手</h1><div class="badge idle" id="st">○ Ready</div></div>
<div class="sub">输入想查的问题或要办的事，它像真人一样打开浏览器去搜、去点、去打字发送，干完用一段话汇报。要<b>深度思考</b>：模型选 deepseek-reasoner 这类推理模型。key 只存本机。</div>
<div class="card">
  <div class="row">
    <select id="prov" onchange="provChange()">
      <optgroup label="China">
        <option value="agnes">Agnes</option>
        <option value="siliconflow">SiliconFlow</option>
        <option value="deepseek">DeepSeek</option>
        <option value="zhipu">Zhipu GLM</option>
        <option value="moonshot">Moonshot Kimi</option>
        <option value="qwen">Tongyi Qwen</option>
        <option value="doubao">Volcengine Doubao</option>
        <option value="qianfan">Baidu Qianfan</option>
        <option value="stepfun">StepFun</option>
        <option value="lingyi">Lingyi Wanwu</option>
        <option value="hunyuan">Tencent Hunyuan</option>
      </optgroup>
      <optgroup label="Global">
        <option value="openrouter">OpenRouter (300+ models, one key)</option>
        <option value="openai">OpenAI</option>
        <option value="anthropic">Anthropic Claude</option>
        <option value="gemini">Google Gemini</option>
        <option value="grok">xAI Grok</option>
        <option value="groq">Groq</option>
        <option value="mistral">Mistral</option>
        <option value="together">Together</option>
        <option value="fireworks">Fireworks</option>
        <option value="deepinfra">DeepInfra</option>
        <option value="cerebras">Cerebras</option>
      </optgroup>
      <optgroup label="Local">
        <option value="ollama">Ollama (no key needed)</option>
        <option value="lmstudio">LM Studio (no key needed)</option>
      </optgroup>
      <optgroup label="Custom">
        <option value="custom">Custom (any OpenAI-compatible)</option>
      </optgroup>
    </select>
  </div>
  <div class="row">
    <input id="model" class="inp" list="mlist" placeholder="模型名">
    <datalist id="mlist"></datalist>
    <input id="key" class="inp" placeholder="API Key（你自己的）">
  </div>
  <div class="row" id="baserow" style="display:none">
    <input id="base" class="inp" placeholder="Base URL，如 https://api.xx.com/v1">
  </div>
  <textarea id="goal" placeholder="想查什么/要它干什么直接说，比如：帮我查下 2026 年最值得用的 AI Agent 工具，点开一篇细看 / 打开 http://localhost:4098 在输入框里发一句「你好」 / 查下今天天气"></textarea>
  <div class="row">
    <button onclick="start()">🔍 开始搜索</button>
    <button class="ghost" onclick="stop()">⏹ 停止</button>
  </div>
</div>
<div class="log" id="log"></div>
<div class="ans" id="anscard"><div class="t">AGENT 的回答</div><div class="c" id="ans"></div></div>
</div>
<script>
const DEFAULT_GOAL="去必应搜「2026 年最值得用的 AI Agent 工具」，点开第一篇看看讲什么";
const PRESETS={
  agnes:{base:"https://apihub.agnes-ai.com/v1",models:["agnes-2.5-flash","agnes-2.5-pro"],needKey:true},
  siliconflow:{base:"https://api.siliconflow.cn/v1",models:["deepseek-ai/DeepSeek-V3","Qwen/Qwen2.5-72B-Instruct","THUDM/glm-4-9b-chat"],needKey:true},
  deepseek:{base:"https://api.deepseek.com/v1",models:["deepseek-chat","deepseek-reasoner"],needKey:true},
  zhipu:{base:"https://open.bigmodel.cn/api/paas/v4",models:["glm-4-flash","glm-4-plus"],needKey:true},
  moonshot:{base:"https://api.moonshot.cn/v1",models:["moonshot-v1-8k","moonshot-v1-32k"],needKey:true},
  qwen:{base:"https://dashscope.aliyuncs.com/compatible-mode/v1",models:["qwen-max","qwen-plus","qwen-turbo"],needKey:true},
  doubao:{base:"https://ark.cn-beijing.volces.com/api/v3",models:["doubao-pro-32k","doubao-lite-32k"],needKey:true},
  qianfan:{base:"https://qianfan.baidubce.com/v2",models:["ernie-4.0-8k","ernie-speed-128k"],needKey:true},
  stepfun:{base:"https://api.stepfun.com/v1",models:["step-1-8k","step-2-16k"],needKey:true},
  lingyi:{base:"https://api.lingyiwanwu.com/v1",models:["yi-large"],needKey:true},
  hunyuan:{base:"https://api.hunyuan.cloud.tencent.com/v1",models:["hunyuan-turbo","hunyuan-standard"],needKey:true},
  openrouter:{base:"https://openrouter.ai/api/v1",models:["openai/gpt-4o-mini","anthropic/claude-3.5-sonnet","deepseek/deepseek-chat","google/gemini-flash-1.5"],needKey:true},
  openai:{base:"https://api.openai.com/v1",models:["gpt-4o","gpt-4o-mini"],needKey:true},
  anthropic:{base:"https://api.anthropic.com/v1",models:["claude-3-5-sonnet-20241022"],needKey:true},
  gemini:{base:"https://generativelanguage.googleapis.com/v1beta/openai",models:["gemini-1.5-flash","gemini-1.5-pro"],needKey:true},
  grok:{base:"https://api.x.ai/v1",models:["grok-2-latest"],needKey:true},
  groq:{base:"https://api.groq.com/openai/v1",models:["llama-3.3-70b-versatile"],needKey:true},
  mistral:{base:"https://api.mistral.ai/v1",models:["mistral-large-latest"],needKey:true},
  together:{base:"https://api.together.xyz/v1",models:["meta-llama/Llama-3.3-70B-Instruct-Turbo"],needKey:true},
  fireworks:{base:"https://api.fireworks.ai/inference/v1",models:["accounts/fireworks/models/llama-v3p3-70b-instruct"],needKey:true},
  deepinfra:{base:"https://api.deepinfra.com/v1/openai",models:["meta-llama/Llama-3.3-70B-Instruct"],needKey:true},
  cerebras:{base:"https://api.cerebras.ai/v1",models:["llama-3.3-70b"],needKey:true},
  ollama:{base:"http://127.0.0.1:11434/v1",models:["qwen2.5vl-tools:7b","qwen2.5:7b"],needKey:false},
  lmstudio:{base:"http://127.0.0.1:1234/v1",models:["local-model"],needKey:false},
  custom:{base:"",models:[],needKey:true}
};
let store={prov:"agnes",keys:{},models:{},base:""};
try{ store=Object.assign(store, JSON.parse(localStorage.getItem('va_cfg')||'{}')); store.keys=store.keys||{}; store.models=store.models||{}; }catch(e){}
function saveStore(){ try{ localStorage.setItem('va_cfg', JSON.stringify(store)); }catch(e){} }
function appendLog(s){ var d=document.getElementById('log'); d.textContent += s+"\n"; d.scrollTop=d.scrollHeight; }
function appendAnswer(s){ document.getElementById('ans').textContent=s; document.getElementById('anscard').style.display='block'; document.getElementById('anscard').scrollIntoView({behavior:'smooth',block:'nearest'}); }
function setState(s){ var b=document.getElementById('st'); if(s==='running'){ b.className='badge run'; b.innerHTML='<i></i>搜索中'; } else { b.className='badge idle'; b.textContent='○ Ready'; } }
function provChange(){
  var v=document.getElementById('prov').value, p=PRESETS[v];
  var dl=document.getElementById('mlist'); dl.innerHTML='';
  p.models.forEach(function(m){ var o=document.createElement('option'); o.value=m; dl.appendChild(o); });
  document.getElementById('model').value=store.models[v]||p.models[0]||'';
  document.getElementById('key').value=store.keys[v]||'';
  document.getElementById('key').style.display=p.needKey?'block':'none';
  document.getElementById('baserow').style.display=(v==='custom')?'flex':'none';
  if(v==='custom') document.getElementById('base').value=store.base||'';
}
function start(){
  var v=document.getElementById('prov').value, p=PRESETS[v];
  var g=document.getElementById('goal').value.trim();
  var model=document.getElementById('model').value.trim()||p.models[0];
  var key=document.getElementById('key').value.trim();
  var base=(v==='custom')?document.getElementById('base').value.trim():p.base;
  if(p.needKey && !key){ alert('这个厂商需要填你自己的 API Key'); return; }
  if(v==='custom' && !base){ alert('自定义源需要填 Base URL'); return; }
  store.prov=v; store.models[v]=model; store.keys[v]=key; if(v==='custom') store.base=base;
  saveStore();
  var cfg={base:base, model:model, key:key};
  document.getElementById('log').textContent='';
  document.getElementById('anscard').style.display='none';
  setState('running');
  window.pywebview.api.start(g||DEFAULT_GOAL, JSON.stringify(cfg));
}
function stop(){ window.pywebview.api.stop(); }
async function fillLocalKey(){
  try{
    var p=document.getElementById('prov').value;
    if(p==='agnes' && !document.getElementById('key').value){
      var k=await window.pywebview.api.local_key();
      if(k){ document.getElementById('key').value=k; store.keys['agnes']=k; saveStore(); }
    }
  }catch(e){}
}
window.onload=function(){
  document.getElementById('prov').value=store.prov||'agnes';
  provChange();
  fillLocalKey().then(function(){ start(); });
};
</script></body></html>"""


class Api:
    def local_key(self):
        return local_key()

    def start(self, goal, cfg_json):
        try:
            cfg = json.loads(cfg_json or "{}")
        except Exception:
            cfg = {}
        cfg = {**DEFAULT_CFG, **{k: v for k, v in cfg.items() if v}}
        if not (goal or "").strip():
            goal = "打开必应，搜索「AI Agent 工具」，点开第一个结果"
        stop_ev.clear()
        if _busy.locked():
            log_q.put("⚠️ 已有任务在跑，先点「停止」或等它干完")
            return
        def runner():
            with _busy:
                try:
                    run_agent(goal.strip(), cfg, "--headless" in sys.argv)
                finally:
                    log_q.put("STATE::idle")
        threading.Thread(target=runner, daemon=True).start()

    def stop(self):
        stop_ev.set()


def pump():
    while True:
        while not log_q.empty():
            line = log_q.get()
            try:
                if line.startswith("ANSWER::"):
                    webview.windows[0].evaluate_js(f"appendAnswer({json.dumps(line[8:])})")
                elif line.startswith("STATE::"):
                    webview.windows[0].evaluate_js(f"setState({json.dumps(line[7:])})")
                else:
                    webview.windows[0].evaluate_js(f"appendLog({json.dumps(line)})")
            except Exception:
                pass
        time.sleep(0.2)


if __name__ == "__main__":
    threading.Thread(target=pump, daemon=True).start()
    webview.create_window("AI 深度搜索助手", html=HTML, js_api=Api(), width=560, height=800)
    webview.start(gui="edgechromium")
