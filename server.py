#!/usr/bin/env python
# AI 深度搜索助手 · 本地 Web 服务版
# 不再依赖原生窗口/PyInstaller 冻结：起一个本地 HTTP 服务，浏览器打开
# http://localhost:8080 即可使用。Agent 在后端用 Playwright 无头驱动真实 Edge，
# 日志/报告通过 SSE 实时推到前端。多厂商、深度研究模式、去隐私化全部复用 visual_agent。
import json, threading, queue, os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import visual_agent as va

PORT = 8080

HTML = r"""<!doctype html><html lang="zh"><head><meta charset="utf-8">
<style>
  *{box-sizing:border-box;margin:0}
  html,body{height:100%}
  body{font-family:-apple-system,"PingFang SC","Microsoft YaHei",sans-serif;background:#f3f4f6;color:#1c1f24;font-size:14px}
  .wrap{height:100%;display:flex;flex-direction:column;padding:16px;gap:12px;max-width:760px;margin:0 auto}
  .top{display:flex;align-items:center;justify-content:space-between;padding:2px 2px 0}
  .brand{font-size:16px;font-weight:750;letter-spacing:.2px;display:flex;align-items:baseline;gap:8px}
  .brand em{font-style:normal;font-size:12px;font-weight:500;color:#8a9098}
  .status{font-size:12px;padding:4px 12px;border-radius:99px;background:#eef1f5;color:#7a8088;font-weight:600;display:flex;align-items:center;gap:6px}
  .status i{width:7px;height:7px;border-radius:50%;background:#b6bcc4;display:inline-block}
  .status.run{background:#e9f6ee;color:#1e9e50}
  .status.run i{background:#22b45e;animation:pulse 1.2s infinite}
  @keyframes pulse{0%{opacity:1}50%{opacity:.3}100%{opacity:1}}
  .modes{display:flex;gap:8px}
  .mode{flex:1;border:1px solid #e3e6ea;background:#fff;color:#5a6068;border-radius:11px;padding:10px;font-size:13px;font-weight:600;cursor:pointer;transition:.15s;display:flex;flex-direction:column;gap:2px;align-items:flex-start}
  .mode small{font-weight:400;font-size:11px;color:#9aa0a8;line-height:1.4}
  .mode.active{border-color:#2f6df6;background:#f5f8ff;color:#1f4fc0;box-shadow:0 1px 2px rgba(47,109,246,.06)}
  .mode.active small{color:#7d94c8}
  .card{background:#fff;border:1px solid #e6e8ec;border-radius:13px;padding:13px}
  .cfg-row{display:flex;gap:8px;margin-top:9px;align-items:center}
  .cfg-row:first-child{margin-top:0}
  select,.inp{border:1px solid #dfe3e8;border-radius:9px;padding:9px 10px;font-size:13px;background:#fbfcfd;color:#333;outline:none;transition:border .15s,box-shadow .15s;min-width:0;font-family:inherit}
  select{flex:1}
  .inp{flex:1}
  .inp:focus,select:focus{border-color:#2f6df6;box-shadow:0 0 0 3px rgba(47,109,246,.12);background:#fff}
  .hint{font-size:12px;color:#8a9098;line-height:1.55;margin-bottom:9px}
  textarea{width:100%;height:60px;border:1px solid #dfe3e8;border-radius:10px;padding:10px 12px;font-size:14px;font-family:inherit;background:#fbfcfd;resize:vertical;outline:none;transition:border .15s,box-shadow .15s}
  textarea:focus{border-color:#2f6df6;box-shadow:0 0 0 3px rgba(47,109,246,.12);background:#fff}
  .actions{display:flex;gap:8px;margin-top:10px}
  button.go{background:#2f6df6;color:#fff;border:0;border-radius:10px;padding:10px 20px;font-size:14px;font-weight:700;cursor:pointer;transition:background .15s}
  button.go:hover{background:#2559d6}
  button.go:active{transform:translateY(1px)}
  button.ghost{background:#eef1f5;color:#3a3f46;font-weight:600;border:0;border-radius:10px;padding:10px 14px;font-size:13px;cursor:pointer;transition:.15s}
  button.ghost:hover{background:#e3e7ec}
  .vis{margin-left:auto;display:flex;align-items:center;gap:6px;font-size:12.5px;font-weight:600;color:#5a6068;cursor:pointer;user-select:none;white-space:nowrap}
  .vis input{width:15px;height:15px;accent-color:#2f6df6;cursor:pointer}
  .vishint{font-size:11.5px;color:#8a9098;line-height:1.5;margin-top:8px}
  .vishint.off{color:#b08a2a}
  .result{flex:1;min-height:160px;display:flex;flex-direction:column;gap:8px}
  .tabs{display:flex;gap:6px}
  .tab{border:1px solid #e3e6ea;background:#fff;color:#6a7078;border-radius:9px;padding:6px 14px;font-size:12.5px;font-weight:600;cursor:pointer}
  .tab.active{border-color:#2f6df6;color:#1f4fc0;background:#f5f8ff}
  .panel{flex:1;min-height:0;display:flex}
  .panel[hidden]{display:none}
  .log{flex:1;overflow:auto;background:#0e1116;color:#c9d4c9;font-family:Consolas,"Courier New",monospace;font-size:12px;line-height:1.75;padding:12px 14px;border-radius:12px;white-space:pre-wrap;word-break:break-all}
  .report{flex:1;overflow:auto;background:#fff;border:1px solid #e6e8ec;border-radius:12px;padding:16px 18px}
  .rep-head{font-size:12px;color:#8a9098;margin-bottom:10px;padding-bottom:8px;border-bottom:1px solid #eef0f3}
  .sources{margin-bottom:14px;display:flex;flex-direction:column;gap:6px}
  .src{font-size:12.5px;display:flex;gap:7px;align-items:flex-start;line-height:1.5}
  .src .dot{width:6px;height:6px;border-radius:50%;background:#2f6df6;margin-top:6px;flex:none}
  .src a{color:#2f6df6;text-decoration:none;word-break:break-all}
  .src a:hover{text-decoration:underline}
  .rep-body h2{font-size:17px;margin:6px 0 10px;color:#14181f}
  .rep-body h3{font-size:14.5px;margin:16px 0 8px;color:#1f2530}
  .rep-body h4{font-size:13.5px;margin:12px 0 6px;color:#2a313c}
  .rep-body p{line-height:1.8;margin:8px 0;color:#2b313a}
  .rep-body ul{margin:8px 0;padding-left:20px}
  .rep-body li{line-height:1.8;margin:5px 0;color:#2b313a}
  .rep-body a{color:#2f6df6;text-decoration:none}
  .rep-body a:hover{text-decoration:underline}
  .placeholder{color:#aab0b8;font-size:13px;line-height:1.7}
</style></head><body><div class="wrap">
<div class="top">
  <div class="brand">AI 深度搜索助手 <em>本地服务 · 浏览器打开即用</em></div>
  <div class="status" id="st"><i></i><span>就绪</span></div>
</div>

<div class="modes">
  <button class="mode active" data-mode="quick" onclick="setMode('quick')">⚡ 快速操作<small>去任意网页点按、打字、发送</small></button>
  <button class="mode" data-mode="research" onclick="setMode('research')">🔬 深度研究<small>跨多网页读资料，给你一份带来源的报告</small></button>
</div>

<div class="card">
  <div class="cfg-row">
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
  <div class="cfg-row">
    <input id="model" class="inp" list="mlist" placeholder="模型名">
    <datalist id="mlist"></datalist>
    <input id="key" class="inp" placeholder="API Key（仅存本机浏览器）">
  </div>
  <div class="cfg-row" id="baserow" style="display:none">
    <input id="base" class="inp" placeholder="Base URL，如 https://api.xx.com/v1">
  </div>
  <div class="hint" id="hint"></div>
  <textarea id="goal" placeholder=""></textarea>
  <div class="actions">
    <button class="go" onclick="start()">🔍 开始</button>
    <button class="ghost" onclick="stop()">⏹ 停止</button>
    <label class="vis"><input type="checkbox" id="vis" checked onchange="saveVis()"> 看得见浏览器</label>
  </div>
  <div class="vishint" id="vishint">✓ 会弹出真实 Edge 窗口，你能亲眼看到鼠标自己移动、点击、打字（隐形手）。不想让它弹窗口就取消勾选，改后台静默跑。</div>
</div>

<div class="result">
  <div class="tabs">
    <button class="tab active" data-tab="log" onclick="setTab('log')">执行日志</button>
    <button class="tab" data-tab="report" onclick="setTab('report')">研究报告</button>
  </div>
  <div class="panel" id="logpanel"><div class="log" id="log"></div></div>
  <div class="panel" id="reportpanel" hidden><div class="report" id="report"><div class="placeholder">还没有报告。<br>切到「🔬 深度研究」模式，输入你的问题，开始一次任务后这里会显示带来源引用的研究报告（同时存到本机 文档/AI深度搜索助手_报告/）。</div></div></div>
</div>
</div>
<script>
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
const HINTS={
  quick:"快速操作：让它打开某个网页去点、打字、发送。例如「打开 http://localhost:4098 在输入框发一句『你好』」「去B站搜猫片点第一个视频」。",
  research:"深度研究：给它一个问题，它会跨多个网页读资料、抓正文，最后给你一份带来源引用的研究报告并存到本机。例如「2026 年最值得用的 AI Agent 工具有哪些？各自侧重是什么？」。"
};
const GOAL_PH={
  quick:"想让它去哪个网页干什么，直接说：打开 http://localhost:4098 在输入框发「你好」 / 去B站搜猫片点第一个视频",
  research:"想研究什么问题，直接说：2026 年最值得用的 AI Agent 工具有哪些？各自侧重是什么？"
};
let MODE="quick";
let store={prov:"agnes",keys:{},models:{},base:""};
try{ store=Object.assign(store, JSON.parse(localStorage.getItem('va_cfg')||'{}')); store.keys=store.keys||{}; store.models=store.models||{}; }catch(e){}
function saveStore(){ try{ localStorage.setItem('va_cfg', JSON.stringify(store)); }catch(e){} }
function appendLog(s){ var d=document.getElementById('log'); d.textContent += s+"\n"; d.scrollTop=d.scrollHeight; }
function appendAnswer(s){ setTab('report'); var r=document.getElementById('report'); r.innerHTML='<div class="rep-head">一句话结论</div><div class="rep-body"><p style="font-size:14.5px;line-height:1.8">'+s+'</p></div>'; }
function appendReport(s){ setTab('report'); document.getElementById('report').innerHTML=s; }
function setState(s){ var b=document.getElementById('st'); if(s==='running'){ b.className='status run'; b.innerHTML='<i></i><span>搜索中</span>'; } else { b.className='status'; b.innerHTML='<i></i><span>就绪</span>'; } }
function setMode(m){ MODE=m; document.querySelectorAll('.mode').forEach(function(x){ x.classList.toggle('active', x.dataset.mode===m); }); document.getElementById('hint').textContent=HINTS[m]; document.getElementById('goal').placeholder=GOAL_PH[m]; }
function setTab(t){ document.querySelectorAll('.tab').forEach(function(x){ x.classList.toggle('active', x.dataset.tab===t); }); document.getElementById('logpanel').hidden=(t!=='log'); document.getElementById('reportpanel').hidden=(t!=='report'); }
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
  if(!g){ alert('先说说你想查什么 / 要它干什么'); return; }
  store.prov=v; store.models[v]=model; store.keys[v]=key; if(v==='custom') store.base=base;
  saveStore();
  var cfg={base:base, model:model, key:key};
  document.getElementById('log').textContent='';
  document.getElementById('report').innerHTML='<div class="placeholder">任务进行中…报告会在干完后出现在这里。</div>';
  setState('running');
  fetch('/api/start',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({goal:g,mode:MODE,cfg:cfg,visible:document.getElementById('vis').checked})});
  if(window._es) try{window._es.close();}catch(e){}
  window._es=new EventSource('/api/stream');
  window._es.onmessage=function(ev){
    var m=JSON.parse(ev.data);
    if(m.type==='log') appendLog(m.text);
    else if(m.type==='answer') appendAnswer(m.text);
    else if(m.type==='report') appendReport(m.text);
    else if(m.type==='state') setState(m.text);
  };
  window._es.onerror=function(){ /* 连接断开会自动重连，忽略 */ };
}
function stop(){ fetch('/api/stop',{method:'POST'}); }
function saveVis(){
  var c=document.getElementById('vis').checked, h=document.getElementById('vishint');
  store.vis=c; saveStore();
  if(c){ h.className='vishint'; h.textContent='✓ 会弹出真实 Edge 窗口，你能亲眼看到鼠标自己移动、点击、打字（隐形手）。不想让它弹窗口就取消勾选，改后台静默跑。'; }
  else { h.className='vishint off'; h.textContent='⚠ 后台静默运行：屏幕上不会弹浏览器窗口，只在下面的日志里看它干了什么。'; }
}
async function fillLocalKey(){
  try{
    var p=document.getElementById('prov').value;
    if(p==='agnes' && !document.getElementById('key').value){
      var r=await fetch('/api/local_key'); var k=(await r.text()).trim();
      if(k){ document.getElementById('key').value=k; store.keys['agnes']=k; saveStore(); }
    }
  }catch(e){}
}
window.onload=function(){
  document.getElementById('prov').value=store.prov||'agnes';
  document.getElementById('vis').checked=(store.vis!==false);
  provChange();
  setMode('quick');
  setTab('log');
  saveVis();
  fillLocalKey();
};
</script></body></html>"""


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="text/html; charset=utf-8"):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            self._send(200, HTML)
        elif self.path == "/api/stream":
            self.sse()
        elif self.path == "/api/local_key":
            self._send(200, va.local_key() or "", "text/plain; charset=utf-8")
        else:
            self._send(404, "not found")

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0) or 0)
        raw = self.rfile.read(n) if n else b"{}"
        try:
            data = json.loads(raw or b"{}")
        except Exception:
            data = {}
        if self.path == "/api/start":
            goal = (data.get("goal") or "").strip()
            mode = data.get("mode") or "quick"
            # visible=True -> 真实 Edge 窗口弹出，能亲眼看见鼠标自己移动点击（隐形手）
            # visible=False -> 后台无头跑，屏幕上不弹窗口
            headless = (data.get("visible") is not True)
            cfg = {**va.DEFAULT_CFG, **{k: v for k, v in (data.get("cfg") or {}).items() if v}}
            va.stop_ev.clear()
            if va._busy.locked():
                va.log_q.put("⚠️ 已有任务在跑，先点「停止」或等它干完")
                self._send(200, '{"ok":false}')
                return
            if not goal:
                va.log_q.put("⚠️ 没收到目标")
                self._send(200, '{"ok":false}')
                return

            def runner():
                # 用 _busy 锁防并发；finally 保证一定回到 idle，
                # 否则前端状态永远卡在「搜索中」，看着就像死了没干活。
                with va._busy:
                    try:
                        va.log_q.put("STATE::running")
                        va.run_agent(goal, cfg, headless, mode)
                    except Exception as e:
                        va.log_q.put(f"❌ 运行出错：{e}")
                    finally:
                        va.log_q.put("STATE::idle")

            threading.Thread(target=runner, daemon=True).start()
            self._send(200, '{"ok":true}')
        elif self.path == "/api/stop":
            va.stop_ev.set()
            va.close_last_browser()
            self._send(200, '{"ok":true}')
        else:
            self._send(404, "not found")

    def sse(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        self.wfile.flush()
        while True:
            try:
                line = va.log_q.get(timeout=30)
            except queue.Empty:
                try:
                    self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
                except Exception:
                    break
                continue
            if line.startswith("ANSWER::"):
                t, d = "answer", line[8:]
            elif line.startswith("REPORT::"):
                t, d = "report", line[7:]
            elif line.startswith("STATE::"):
                t, d = "state", line[7:]
            else:
                t, d = "log", line
            try:
                self.wfile.write(("data: " + json.dumps({"type": t, "text": d}, ensure_ascii=False) + "\n\n").encode("utf-8"))
                self.wfile.flush()
            except Exception:
                break

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    print(f"AI 深度搜索助手 · 本地服务已启动： http://localhost:{PORT}")
    print("在浏览器打开上面的地址即可使用。Ctrl+C 停止。")
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
