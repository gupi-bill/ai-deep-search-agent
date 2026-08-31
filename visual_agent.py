#!/usr/bin/env python
# 可视化 Agent —— 真能看见它干活的浏览器 Agent。
# 说人话给它目标，它打开真实 Edge 浏览器，像隐形的人一样移动鼠标、点击、输入，
# 一步步完成；干完用一段话汇报。
# 两种模式：
#   ⚡ 快速操作 —— 去某个网页点、打字、发送（控制任意本地/外网页面）
#   🔬 深度研究 —— 跨多个网页读资料、抓正文，最后给你一份带来源引用的研究报告
# 多厂商模型适配器：不内置任何 key，用户自己选厂商、填自己的 key（本机记住）。
# 支持任意 OpenAI 兼容端点；推理模型（deepseek-reasoner 等）可直接当大脑。
# 打包后双击即用。
import os, json, re, sys, time, threading, queue, html, datetime
from urllib.parse import quote
import webview
import requests
from playwright.sync_api import sync_playwright


log_q = queue.Queue()
stop_ev = threading.Event()
_busy = threading.Lock()

def _eval_to(page, js, arg=None, timeout=10):
    """统一入口 + 异常兜底。

    ⚠️ 千万别把 page.evaluate 丢进线程池/其他线程：Playwright 的 sync API 跑在 greenlet 上，
    跨线程调用会直接炸 `greenlet.error: Cannot switch to a different thread`。
    （踩过一次，元素识别直接全废成 0 个。）
    所以这里老老实实同步调用，只做异常兜底：导航中、上下文销毁时返回 None，
    不让一次 evaluate 失败把整个任务带崩。
    """
    try:
        return page.evaluate(js, arg)
    except Exception:
        return None

DEFAULT_CFG = {"base": "https://apihub.agnes-ai.com/v1", "model": "agnes-2.5-flash", "key": ""}

# 研究报告与截图落盘目录（运行时生成，不进分发物）
REPORT_DIR = os.path.join(os.path.expanduser("~"), "Documents", "AI深度搜索助手_报告")
SHOT_DIR = os.path.join(REPORT_DIR, "shots")


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


def _chat(cfg, sys_p, usr, temperature=0.3, timeout=45, retries=2):
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
        seg = txt[s:e+1]
        try:
            return json.loads(seg)
        except Exception:
            # 模型偶尔在一段 JSON 后面又接一段废话/第二个 JSON，
            # json.loads 会报 "Extra data" 直接把任务带崩。
            # 用 raw_decode 只取第一段合法对象，后面的一律忽略。
            try:
                obj, _ = json.JSONDecoder().raw_decode(seg)
                return obj
            except Exception:
                return None
    return None


def plan_task(goal, cfg, research):
    mode_note = ("你正在规划一次『深度研究』：Agent 会跨多个网页查资料并抓取正文，最后汇总成报告。"
                 "请把意图拆成 4-8 个步骤，尽量覆盖多个信息来源（不同站点/不同文章），"
                 "并包含『进入某篇具体内容页』的步骤以便抓正文。")
    if not research:
        mode_note = ("你正在规划一次『快速操作』：Agent 去某个网页点按、打字、发送即可，步骤 2-5 个。"
                     "无需进入深读页面，达成动作即止。")
    sys_p = ("你是任务规划器。用户会用口语、零散、省略主语、甚至跳跃的方式，描述想让浏览器Agent帮它做的事。"
             + mode_note +
             "请把意图拆成明确的浏览器操作步骤（中文），并补全常识与默认站点："
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


def agnes_decide(goal, elements, history, cfg, research):
    elist = "\n".join(f"{i+1}. [{e['role']}] {e['name']}" for i, e in enumerate(elements)) or "（无）"
    hist = "\n".join(history[-8:]) or "（还没开始）"
    extra = ""
    if research:
        extra = ("这是『深度研究』模式：进入一篇具体内容/文章页后，请优先返回 action=subdone 让系统抓正文；"
                 "不要反复在同一页空操作。多个来源都看过后，最后返回 action=done。")
    sys_p = ("你是浏览器操作 Agent。用户想完成一个明确的『子任务』。请结合当前页面『可交互元素列表』决定下一步动作。"
             "注意：用户的原话可能口语化、省略主语、跳跃，请自行补全意图（例如『点开第一个』=点击结果列表第一项；"
             "『搜X』=在搜索框输入X回车；『进去看看』=点击进入该链接）。"
             + extra +
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


def agnes_research_report(goal, notes, cfg):
    """基于抓取的多页笔记，写一份带来源引用的研究报告（Markdown）。"""
    if not notes:
        return "# 研究报告\n\n（没抓到任何页面正文，可能网站反爬或需要登录。换种说法再试，或切到『快速操作』。）"
    sys_p = ("你是一个研究助理。用户提出一个问题，你的浏览器 Agent 已经去多个网页查资料并抓取了正文片段。"
             "请综合这些资料，用中文写一份简洁的研究报告，结构如下（严格用 Markdown）：\n"
             "# 一句话结论\n"
             "用一两句话直接回答用户的问题。\n\n"
             "## 关键要点\n"
             "用 - 列出 4-8 条要点，每条一句话说清观点 + 依据，"
             "**并在句末用 [1] [2] 这样的编号标出依据来自哪个来源**（编号对应下面来源列表）。\n\n"
             "## 信息来源\n"
             "按编号逐条列出：`- [1] [来源标题](链接) — 一句话说明它提供了什么`。\n\n"
             "口语、自然、像真人整理。不要编造资料里没有的信息；资料不足就明说。"
             "只输出 Markdown，不要解释。")
    notes_txt = "\n\n".join(f"【来源 {i+1}】{n['title']}\n{n['url']}\n{n['text']}" for i, n in enumerate(notes))
    usr = f"用户的问题：{goal}\n\n抓取到的资料：\n{notes_txt}\n\n研究报告（Markdown）："
    try:
        return _chat(cfg, sys_p, usr, temperature=0.4, timeout=180)
    except Exception as e:
        return f"# 研究报告\n\n（报告生成失败：{e}）\n\n## 抓到的原始资料\n" + notes_txt[:2000]


def read_page_text(page, max_chars=3500):
    """提取页面正文（去噪），用于深度研究时让模型读懂页面。"""
    try:
        txt = _eval_to(page, """() => {
            const root = document.querySelector('main') || document.querySelector('article') || document.body;
            let s = root ? root.innerText : document.body.innerText;
            s = (s || '').replace(/\\s+/g, ' ').trim();
            return s.slice(0, 7000);
        }""")
        return (txt or "").strip()[:max_chars]
    except Exception:
        return ""


def _md_to_html(md):
    """极简 Markdown -> HTML（标题/列表/链接/段落），用于报告面板展示。"""
    out = []
    in_list = False
    for line in md.splitlines():
        s = line.rstrip()
        if not s.strip():
            if in_list:
                out.append("</ul>"); in_list = False
            continue
        if s.startswith("### "):
            if in_list: out.append("</ul>"); in_list = False
            out.append(f"<h4>{_esc(s[4:])}</h4>")
        elif s.startswith("## "):
            if in_list: out.append("</ul>"); in_list = False
            out.append(f"<h3>{_esc(s[3:])}</h3>")
        elif s.startswith("# "):
            if in_list: out.append("</ul>"); in_list = False
            out.append(f"<h2>{_esc(s[2:])}</h2>")
        elif s.startswith("- "):
            if not in_list:
                out.append("<ul>"); in_list = True
            out.append(f"<li>{_inline(s[2:])}</li>")
        else:
            if in_list: out.append("</ul>"); in_list = False
            out.append(f"<p>{_inline(s)}</p>")
    if in_list:
        out.append("</ul>")
    return "\n".join(out)


def _esc(t):
    return html.escape(t)


def _inline(t):
    # 链接 [text](url)
    t = re.sub(r"\[([^\]]+)\]\((https?://[^)]+)\)",
               lambda m: f'<a href="{m.group(2)}" target="_blank" rel="noopener">{_esc(m.group(1))}</a>', t)
    return _esc(t)


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
    # 再补一轮 DOM 扫描：可访问性树经常漏掉搜索框、带 placeholder 的输入框
    try:
        extra = _eval_to(page, """() => {
            const out=[];
            document.querySelectorAll('button,a,input,textarea,select,[role=button],[role=link]').forEach(el=>{
                const t=(el.innerText||el.value||el.placeholder||el.getAttribute('aria-label')||'').trim();
                if(t && t.length<60) out.push({role:(el.tagName||'').toLowerCase(), name:t});
            });
            return out.slice(0,80);
        }""")
        for e in extra or []:
            key = (e.get("role", ""), e.get("name", ""))
            if key not in seen:
                seen.add(key)
                items.append({"role": e.get("role", ""), "name": e.get("name", "")})
    except Exception:
        pass
    return items[:60]


def _find_center(page, name):
    return _eval_to(page, """(nm) => {
        const sel="a,button,input,textarea,select,[role=button],[role=link]";
        const els=[...document.querySelectorAll(sel)];
        // 双向包含：模型说"搜索框"、页面上写"搜索"也能对上，反之亦然
        const el=els.find(e=>{const t=(e.innerText||e.value||e.placeholder||e.getAttribute('aria-label')||'').trim();return t && (t.includes(nm)||nm.includes(t));});
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
    ok = _eval_to(page, """(nm) => {
        const els=[...document.querySelectorAll('input,textarea,[contenteditable=true]')];
        // 双向包含：模型说"搜索框"、页面上写"搜索"也能对上，反之亦然
        const el=els.find(e=>{const t=(e.innerText||e.value||e.placeholder||e.getAttribute('aria-label')||'').trim();return t && (t.includes(nm)||nm.includes(t));});
        if(!el) return false;
        el.scrollIntoView({block:'center'});
        try { el.value=''; } catch(e) {}
        el.focus();
        return true;
    }""", el["name"])
    if not ok:
        return False
    # 逐字敲是给你看的（隐形手）；delay 压到 15ms，长文本也不至于敲半天
    page.keyboard.type((value or "")[:200], delay=15)
    return True


_last_browser = None


def close_last_browser():
    """关掉上一次可见模式留下的 Edge 窗口（防反复开任务堆积一堆浏览器）。"""
    global _last_browser
    b, _last_browser = _last_browser, None
    if b is not None:
        try:
            b.close()
        except Exception:
            pass


# ---------- 深度研究三件套：找得到 / 抓得下 / 提得准 ----------

# 1) 从搜索结果页(SERP)批量抠出候选来源：标题 + 链接
SERP_JS = """(lim) => {
    const out=[], seen=new Set();
    const badHost=/(^|\\.)(bing|baidu|google|so\\.com|sogou|duckduckgo|yandex|baidu\\.com|microsoft|msn|w3\\.org)\\./i;
    const badPath=/\\/(search|login|signin|signup|register|404)(\\?|\\/|$)/i;
    const sels=[
        '#b_results li.b_algo h2 a',
        '#b_results h2 a',
        '#content_left .result h3 a',
        '#content_left .t a',
        '#search .g a h3',
        'div.yuRUbf a',
        '#rso a',
        '.result a'
    ];
    let els=[];
    for(const s of sels){
        const r=document.querySelectorAll(s);
        if(r && r.length>=2){ els=[...r]; break; }
    }
    if(!els.length){
        els=[...document.querySelectorAll('a[href^="http"]')].filter(a=>{
            const t=(a.innerText||'').trim();
            return t.length>=8 && t.length<=120;
        });
    }
    for(const a of els){
        const u=(a.href||'').trim();
        const t=(a.innerText||a.getAttribute('aria-label')||a.title||'').trim();
        if(!u || !u.startsWith('http')) continue;
        if(seen.has(u)) continue;
        try{ if(badHost.test(new URL(u).hostname)) continue; }catch(e){ continue; }
        if(badPath.test(u)) continue;
        seen.add(u);
        out.push({title:t||u, url:u});
        if(out.length>=lim) break;
    }
    return out;
}"""

# 2) 正文提取：先砍掉导航/广告/页脚，再按"文字密度"挑出真正的正文容器
ARTICLE_JS = """() => {
    const kill='script,style,nav,header,footer,aside,form,iframe,noscript,svg,button,select,'+
               '.ad,.ads,.advert,.advertisement,.sidebar,.comment,.comments,.nav,.menu,'+
               '.breadcrumb,.pagination,.share,.related,.recommend,.footer,.header,.popup,.modal,'+
               '[class*=advert],[id*=advert],[class*=sidebar],[class*=comment]';
    try{ document.querySelectorAll(kill).forEach(e=>e.remove()); }catch(e){}
    function score(el){
        if(!el) return {el:null,den:-1,len:0,ps:0};
        const ps=[...el.querySelectorAll('p')];
        let plen=0; ps.forEach(p=>plen+=(p.innerText||'').trim().length);
        let llen=0; el.querySelectorAll('a').forEach(a=>llen+=(a.innerText||'').trim().length);
        // 链接文字要打折：全是链接的块多半是导航/推荐位，不是正文
        return {el:el, den: plen - llen*0.5, len: plen, ps: ps.length};
    }
    const cands=[...document.querySelectorAll(
        'article,main,[class*=content],[class*=article],[class*=post],[class*=detail],'+
        '[id*=content],[id*=article],.post,.entry,.post-content,.article-content')];
    let best=score(document.body);
    for(const c of cands){ const s=score(c); if(s.den>best.den) best=s; }
    const root=(best&&best.el)||document.body;
    let txt='';
    if(best && best.ps>=2){
        txt=[...root.querySelectorAll('h1,h2,h3,p,li,pre,blockquote')]
            .map(e=>(e.innerText||'').trim())
            .filter(t=>t && t.length>0)
            .join('\\n');
    }
    if(!txt || txt.length<200) txt=(document.body.innerText||'');
    txt=txt.replace(/\\n{3,}/g,'\\n\\n').replace(/[ \\t]{2,}/g,' ').trim();
    return txt.slice(0,12000);
}"""


def extract_article(page):
    """抓当前页正文（去噪后的纯文本）。失败返回空串。"""
    try:
        return (_eval_to(page, ARTICLE_JS, timeout=12) or "").strip()
    except Exception:
        return ""


def _shot(page, shots, tag):
    """截图存档（最多 6 张）。"""
    if len(shots) >= 6:
        return
    try:
        os.makedirs(SHOT_DIR, exist_ok=True)
        ts = datetime.datetime.now().strftime("%H%M%S")
        sp = os.path.join(SHOT_DIR, f"shot_{ts}_{tag}.png")
        page.screenshot(path=sp, full_page=False)
        shots.append(sp)
    except Exception:
        pass


def deep_research(goal, cfg, page, shots, max_sources=8):
    """深度研究快车道：搜索 → 结果页批量提取来源 → 逐个打开抓正文。

    不走"逐点击决策"的慢循环（那玩意儿抓一个来源要点十几步、还容易点歪），
    这里几秒一个来源，抓得多也抓得准。浏览器仍是可见的，你能看着它一页页翻。
    """
    notes = []
    log_q.put(f"🔎 1/3 搜索｜去必应找「{goal[:30]}」…")
    try:
        page.goto("https://www.bing.com/search?q=" + quote(goal),
                  wait_until="domcontentloaded", timeout=30000)
        time.sleep(2)
    except Exception as e:
        log_q.put(f"⚠️ 搜索页打开失败：{e}")
        return notes
    _shot(page, shots, "serp")

    links = _eval_to(page, SERP_JS, max_sources + 4, timeout=12) or []
    links = [l for l in links if l.get("url")][:max_sources]
    log_q.put(f"🔗 2/3 提取｜结果页抠出 {len(links)} 个候选来源")
    if not links:
        log_q.put("⚠️ 没抠到来源链接（可能是搜索页结构变了），退回普通模式")
        return notes

    for i, l in enumerate(links, 1):
        if stop_ev.is_set():
            log_q.put("⏹ 已停止")
            break
        title = (l.get("title") or l.get("url"))[:44]
        log_q.put(f"📄 3/3 抓取｜{i}/{len(links)}：{title}")
        try:
            page.goto(l["url"], wait_until="domcontentloaded", timeout=25000)
            time.sleep(1.5)
        except Exception as e:
            log_q.put(f"   ✗ 打不开：{str(e)[:60]}")
            continue
        if stop_ev.is_set():
            break
        txt = extract_article(page)
        if txt and len(txt) > 200:
            t = ""
            try:
                t = page.title()
            except Exception:
                pass
            notes.append({"url": page.url, "title": t or l.get("title") or page.url, "text": txt})
            log_q.put(f"   ✓ 抓到 {len(txt)} 字")
            _shot(page, shots, f"s{i}")
        else:
            log_q.put("   ✗ 正文太少（可能是反爬/需登录），跳过")

    log_q.put(f"📚 抓完：{len(notes)} 个有效来源，正在汇总成报告…")
    return notes


def run_agent(goal, cfg, headless, mode="quick"):
    history = []
    notes = []          # 深度研究：抓取的正文笔记
    shots = []          # 深度研究：截图文件路径
    research = (mode == "research")
    global _last_browser
    # 看门狗：单次任务最多跑 15 分钟，到点自动收尾，别无限吊着
    _t0 = time.time()

    def _watchdog():
        while not stop_ev.is_set():
            if time.time() - _t0 > 900:
                log_q.put("⏰ 已跑满 15 分钟，自动收尾（防无限卡住）")
                stop_ev.set()
                break
            time.sleep(5)

    threading.Thread(target=_watchdog, daemon=True).start()
    try:
        os.makedirs(SHOT_DIR, exist_ok=True)
    except Exception:
        pass
    try:
        with sync_playwright() as p:
            close_last_browser()
            # 可见模式：窗口最大化，让你看得清它在页面上干了什么
            launch_args = ["--start-maximized"] if not headless else []
            browser = p.chromium.launch(channel="msedge", headless=headless, args=launch_args)
            if not headless:
                _last_browser = browser
            page = browser.new_page(viewport=None if not headless else {"width": 1280, "height": 800})
            start = "https://www.bing.com"
            if "http" in goal:
                for u in ("https://", "http://"):
                    if u in goal:
                        start = goal[goal.find(u):].split()[0]
            page.goto(start, wait_until="load", timeout=30000)
            if not headless:
                try:
                    page.bring_to_front()   # 弹到最前面，别藏在别的窗口后面
                except Exception:
                    pass
            time.sleep(1.5)
            log_q.put(f"🌐 已打开：{page.url}")
            log_q.put(f"🤖 大脑模型：{cfg.get('model')}  ｜  模式：{'🔬 深度研究' if research else '⚡ 快速操作'}")
            if research:
                # 🔬 深度研究走快车道：搜索 → 结果页批量提取来源 → 逐个打开抓正文
                #    （不走"逐点击决策"的慢循环，那玩意儿抓一个来源要点十几步还容易点歪）
                notes = deep_research(goal, cfg, page, shots)
                steps = []          # 空计划 = 直接跳过下面的 agent 决策循环
            else:
                log_q.put("🧠 正在把你的话翻译成执行计划…")
                steps = plan_task(goal, cfg, research)
                log_q.put("📋 计划 " + str(len(steps)) + " 步：" + " ｜ ".join(steps))
            si = 0
            stuck = 0
            last_sig = ""
            shot_n = 0
            last_note_url = ""
            for step in range(1, 50):
                if stop_ev.is_set():
                    log_q.put("⏹ 已停止"); break
                if si >= len(steps):
                    if not research:
                        log_q.put("✅ 所有计划步骤完成。")
                    break
                sub = steps[si]
                els = grab_elements(page)
                # 先给个"我在动"的信号，免得等模型那十几秒看着像死了
                log_q.put(f"💭 第{step}步思考中…（当前页识别到 {len(els)} 个可点元素）")
                try:
                    dec = agnes_decide(sub, els, history, cfg, research)
                except Exception as e:
                    # 一次决策出错不该把整个任务带走：跳过这一步，继续后面的
                    log_q.put(f"⚠️ 决策出错：{e}")
                    si += 1
                    if si >= len(steps):
                        break
                    continue
                act = dec.get("action", "done")
                thought = dec.get("thought", "")
                log_q.put(f"🧠 第{step}步（计划{si+1}/{len(steps)}：{sub}）：{thought}")
                if act == "done":
                    # 关键兜底：计划还有后续步骤时，模型十有八九是把"当前这一小步做完了"
                    # 误报成 done。这里降级成"这步完成、继续下一步"，绝不让它提前收工。
                    if si < len(steps) - 1:
                        si += 1
                        if research:
                            _maybe_capture(page, notes, shots, lambda: shots.__len__(), lambda n: shots.append(n))
                        nxt = steps[si] if si < len(steps) else "（无更多）"
                        log_q.put(f"✓ 这步干完了（计划还有后续，继续）：{nxt}")
                        stuck = 0
                        time.sleep(0.8)
                        continue
                    log_q.put("✅ Agent 判断全部完成。"); break
                elif act == "subdone":
                    si += 1
                    # 深度研究：到达一个内容页就抓正文
                    if research:
                        _maybe_capture(page, notes, shots, lambda: shots.__len__(), lambda n: shots.append(n))
                    nxt = steps[si] if si < len(steps) else "（无更多）"
                    log_q.put(f"✓ 子任务完成，进入下一步：{nxt}")
                    stuck = 0
                    time.sleep(0.8)
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
                    # 模型偶尔会把一整段文字塞进 value，逐字敲会敲到天荒地老，截断
                    val = (dec.get("value", "") or "").strip()[:200]
                    if 0 <= idx < len(els):
                        log_q.put(f"⌨️ 准备在 [{els[idx]['name'][:24]}] 输入 {len(val)} 字…")
                        ok = human_type(page, els[idx], val)
                        log_q.put(f"⌨️ 输入完成「{val[:50]}」-> {'成功' if ok else '失败'}")
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
                # 卡死检测用"动作指纹"：同一个动作 + 同一个值反复来，就是在原地打转
                # （旧版只看 url+history 长度，模型重复输入同样的字会被判成"有进展"，傻打五六次）
                sig = (f"{act}|{str(dec.get('value') or '')[:30]}"
                       f"|{str(dec.get('idx') or '')}|{page.url}")
                if sig == last_sig:
                    stuck += 1
                else:
                    stuck = 0
                    last_sig = sig
                if stuck >= 3:
                    log_q.put("⏭ 同一个动作反复执行，判定卡住，跳过当前子任务")
                    si += 1
                    stuck = 0
                # 深度研究：每完成一个动作也尝试抓一次（去重 by url），并偶尔截图
                if research:
                    _maybe_capture(page, notes, shots, lambda: shots.__len__(), lambda n: shots.append(n))
                time.sleep(0.6)
            final_url = page.url
            try:
                final_title = page.title()
            except Exception:
                final_title = ""
            log_q.put("🏁 干活结束，整理结果…")
            if research:
                # 收尾再抓一次
                _maybe_capture(page, notes, shots, lambda: shots.__len__(), lambda n: shots.append(n))
                report_md = agnes_research_report(goal, notes, cfg)
                path = _save_report(goal, report_md, shots)
                report_html = _md_to_html(report_md)
                src_html = "".join(
                    f'<div class="src"><span class="dot"></span><a href="{html.escape(n["url"])}" target="_blank" rel="noopener">{html.escape(n["title"] or n["url"])}</a></div>'
                    for n in notes)
                full = (f'<div class="rep-head">共读取 {len(notes)} 个来源 · 截图 {len(shots)} 张</div>'
                        f'<div class="sources">{src_html}</div><div class="rep-body">{report_html}</div>')
                log_q.put("REPORT::" + full)
                if path:
                    log_q.put(f"💾 报告已保存：{path}")
                    log_q.put("OPENPATH::" + path)
            else:
                ans = agnes_final_answer(goal, history, final_url, final_title, cfg)
                log_q.put("ANSWER::" + ans)
            if headless:
                browser.close()
    except Exception as e:
        log_q.put(f"❌ 运行出错：{e}")


def _maybe_capture(page, notes, shots, shot_len_fn, shot_add_fn):
    """深度研究：把当前页正文抓进 notes（按 url 去重）；每抓几页截一张图。"""
    try:
        url = page.url
        if url and url not in [n["url"] for n in notes]:
            txt = read_page_text(page)
            if txt and len(txt) > 120:
                title = ""
                try:
                    title = page.title()
                except Exception:
                    pass
                notes.append({"url": url, "title": title, "text": txt})
                log_q.put(f"📄 已抓取正文：{title or url}（{len(txt)} 字）")
                # 截图（最多 6 张）
                if shot_len_fn() < 6:
                    ts = datetime.datetime.now().strftime("%H%M%S")
                    sp = os.path.join(SHOT_DIR, f"shot_{ts}_{len(notes)}.png")
                    try:
                        page.screenshot(path=sp, full_page=False)
                        shot_add_fn(sp)
                        log_q.put(f"🖼 截图已存：{os.path.basename(sp)}")
                    except Exception:
                        pass
    except Exception:
        pass


def _save_report(goal, md, shots):
    try:
        os.makedirs(REPORT_DIR, exist_ok=True)
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        path = os.path.join(REPORT_DIR, f"research_{ts}.md")
        shots_rel = "\n".join(f"![]({os.path.abspath(s)})\n" for s in shots)
        header = f"# 深度研究｜{goal}\n\n> 生成时间：{ts}  ｜  截图 {len(shots)} 张\n\n"
        with open(path, "w", encoding="utf-8") as f:
            f.write(header + md + "\n\n---\n\n## 截图\n\n" + shots_rel)
        return path
    except Exception:
        return ""


# ---------------- 原生窗口 UI（pywebview） ----------------
HTML = r"""<!doctype html><html lang="zh"><head><meta charset="utf-8">
<style>
  *{box-sizing:border-box;margin:0}
  html,body{height:100%}
  body{font-family:-apple-system,"PingFang SC","Microsoft YaHei",sans-serif;background:#f3f4f6;color:#1c1f24;font-size:14px}
  .wrap{height:100%;display:flex;flex-direction:column;padding:16px;gap:12px}
  /* 顶栏 */
  .top{display:flex;align-items:center;justify-content:space-between;padding:2px 2px 0}
  .brand{font-size:16px;font-weight:750;letter-spacing:.2px;display:flex;align-items:baseline;gap:8px}
  .brand em{font-style:normal;font-size:12px;font-weight:500;color:#8a9098}
  .status{font-size:12px;padding:4px 12px;border-radius:99px;background:#eef1f5;color:#7a8088;font-weight:600;display:flex;align-items:center;gap:6px}
  .status i{width:7px;height:7px;border-radius:50%;background:#b6bcc4;display:inline-block}
  .status.run{background:#e9f6ee;color:#1e9e50}
  .status.run i{background:#22b45e;animation:pulse 1.2s infinite}
  @keyframes pulse{0%{opacity:1}50%{opacity:.3}100%{opacity:1}}
  /* 模式切换 */
  .modes{display:flex;gap:8px}
  .mode{flex:1;border:1px solid #e3e6ea;background:#fff;color:#5a6068;border-radius:11px;padding:10px;font-size:13px;font-weight:600;cursor:pointer;transition:.15s;display:flex;flex-direction:column;gap:2px;align-items:flex-start}
  .mode small{font-weight:400;font-size:11px;color:#9aa0a8;line-height:1.4}
  .mode.active{border-color:#2f6df6;background:#f5f8ff;color:#1f4fc0;box-shadow:0 1px 2px rgba(47,109,246,.06)}
  .mode.active small{color:#7d94c8}
  /* 卡片 */
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
  button.ghost[hidden]{display:none}
  /* 结果区 */
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
  <div class="brand">AI 深度搜索助手 <em>会自己上网查、读、整理的助手</em></div>
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
    <input id="key" class="inp" placeholder="API Key（仅存本机）">
  </div>
  <div class="cfg-row" id="baserow" style="display:none">
    <input id="base" class="inp" placeholder="Base URL，如 https://api.xx.com/v1">
  </div>
  <div class="hint" id="hint"></div>
  <textarea id="goal" placeholder=""></textarea>
  <div class="actions">
    <button class="go" onclick="start()">🔍 开始</button>
    <button class="ghost" onclick="stop()">⏹ 停止</button>
    <button class="ghost" id="openrep" onclick="openRep()" hidden>📂 打开报告</button>
  </div>
</div>

<div class="result">
  <div class="tabs">
    <button class="tab active" data-tab="log" onclick="setTab('log')">执行日志</button>
    <button class="tab" data-tab="report" onclick="setTab('report')">研究报告</button>
  </div>
  <div class="panel" id="logpanel"><div class="log" id="log"></div></div>
  <div class="panel" id="reportpanel" hidden><div class="report" id="report"><div class="placeholder">还没有报告。<br>切到「🔬 深度研究」模式，输入你的问题，开始一次任务后这里会显示带来源引用的研究报告。</div></div></div>
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
  research:"深度研究：给它一个问题，它会跨多个网页读资料、抓正文，最后给你一份带来源引用的研究报告并存到本机。例如「2026 年最值得用的 AI Agent 工具有哪些？各有什么侧重」。"
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
function appendAnswer(s){ setTab('report'); var r=document.getElementById('report'); r.innerHTML='<div class="rep-head">一句话结论</div><div class="rep-body"><p style="font-size:14.5px;line-height:1.8">'+s+'</p></div>'; document.getElementById('openrep').hidden=true; }
function appendReport(s){ setTab('report'); document.getElementById('report').innerHTML=s; document.getElementById('openrep').hidden=false; }
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
  window.pywebview.api.start(g, MODE, JSON.stringify(cfg));
}
function stop(){ window.pywebview.api.stop(); }
function openRep(){ try{ window.pywebview.api.open_report_folder(); }catch(e){} }
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
  setMode('quick');
  setTab('log');
  fillLocalKey().then(function(){ /* 不自动开始，等用户点 */ });
};
</script></body></html>"""


class Api:
    def local_key(self):
        return local_key()

    def open_report_folder(self):
        try:
            os.makedirs(REPORT_DIR, exist_ok=True)
            os.startfile(REPORT_DIR)
        except Exception:
            pass

    def start(self, goal, mode, cfg_json):
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
        mode = mode or "quick"

        def runner():
            with _busy:
                try:
                    run_agent(goal.strip(), cfg, "--headless" in sys.argv, mode)
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
                elif line.startswith("REPORT::"):
                    webview.windows[0].evaluate_js(f"appendReport({json.dumps(line[7:])})")
                elif line.startswith("OPENPATH::"):
                    pass  # 路径仅日志，UI 用固定报告目录按钮
                elif line.startswith("STATE::"):
                    webview.windows[0].evaluate_js(f"setState({json.dumps(line[7:])})")
                else:
                    webview.windows[0].evaluate_js(f"appendLog({json.dumps(line)})")
            except Exception:
                pass
        time.sleep(0.2)


if __name__ == "__main__":
    threading.Thread(target=pump, daemon=True).start()
    webview.create_window("AI 深度搜索助手", html=HTML, js_api=Api(), width=580, height=820)
    webview.start(gui="edgechromium")
