"""visual_agent 单元测试。

只用标准库 unittest，不需要 playwright / pywebview / 任何浏览器：

    python3 -m unittest test_visual_agent -v
    python3 test_visual_agent.py

覆盖的是最容易静默出错的地方：
  - AI 返回的 JSON 怎么抠出来（推理模型会带 <think> 和 markdown 围栏）
  - Markdown 渲染时有没有转义（报告里的 XSS）
  - 本机配置读取会不会误读
"""
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch


# visual_agent 现在能在无 GUI 环境裸导入（webview/playwright 都是软依赖）
import visual_agent as va


class ExtractJsonTest(unittest.TestCase):
    """从模型回复里抠 JSON —— 这段错了整个任务就废了。"""

    def test_plain_json(self):
        self.assertEqual(va._extract_json('{"action":"click","x":10}'),
                         {"action": "click", "x": 10})

    def test_strips_think_block(self):
        """推理模型（deepseek-reasoner 等）会先输出 <think>...</think>。"""
        raw = '<think>让我想想该点哪里</think>{"action":"type","text":"你好"}'
        self.assertEqual(va._extract_json(raw), {"action": "type", "text": "你好"})

    def test_strips_markdown_fence(self):
        raw = '```json\n{"action":"scroll"}\n```'
        self.assertEqual(va._extract_json(raw), {"action": "scroll"})

    def test_plain_fence_without_json_tag(self):
        raw = '```\n{"ok":true}\n```'
        self.assertEqual(va._extract_json(raw), {"ok": True})

    def test_think_and_fence_together(self):
        raw = '<think>先看页面</think>\n```json\n{"n": 1}\n```'
        self.assertEqual(va._extract_json(raw), {"n": 1})

    def test_trailing_garbage(self):
        """模型经常在 JSON 后面又接一段话，或接第二个 JSON。

        json.loads 遇到 Extra data 会直接抛，把任务带崩；
        这里应该只取第一段。"""
        raw = '{"action":"click"} 另外我建议先刷新一下页面'
        self.assertEqual(va._extract_json(raw), {"action": "click"})

    def test_two_json_objects(self):
        raw = '{"a":1}{"b":2}'
        self.assertEqual(va._extract_json(raw), {"a": 1})

    def test_nested_braces(self):
        raw = '{"action":"type","sel":{"tag":"input","i":2}}'
        self.assertEqual(va._extract_json(raw),
                         {"action": "type", "sel": {"tag": "input", "i": 2}})

    def test_no_json_returns_none(self):
        self.assertIsNone(va._extract_json("我找不到那个按钮"))
        self.assertIsNone(va._extract_json(""))
        self.assertIsNone(va._extract_json("<think>只有思考</think>"))

    def test_broken_json_returns_none(self):
        """坏 JSON 不能抛异常 —— 返回 None 让上层走兜底。"""
        self.assertIsNone(va._extract_json('{"action": }'))


class EscapeTest(unittest.TestCase):
    """转义。报告里会放网页抓来的正文，不转义就是 XSS。"""

    def test_escapes_tags(self):
        self.assertEqual(va._esc("<script>alert(1)</script>"),
                         "&lt;script&gt;alert(1)&lt;/script&gt;")

    def test_escapes_ampersand(self):
        self.assertEqual(va._esc("A & B"), "A &amp; B")

    def test_escapes_quotes(self):
        self.assertEqual(va._esc('say "hi"'), "say &quot;hi&quot;")


class InlineTest(unittest.TestCase):
    """行内渲染：链接 + 转义。"""

    def test_link_becomes_anchor(self):
        out = va._inline("见 [文档](https://example.com/a)")
        self.assertIn('<a href="https://example.com/a"', out)
        self.assertIn('rel="noopener"', out)
        self.assertIn(">文档<", out)

    def test_escapes_link_text(self):
        """链接文字也要转义，否则 [xss](url) 里的尖括号会漏出去。"""
        out = va._inline("[<img onerror=1>](https://e.com)")
        self.assertNotIn("<img", out)
        self.assertIn("&lt;img", out)

    def test_non_http_link_untouched(self):
        """只认 http/https，javascript: 这种不能变成可点链接。"""
        out = va._inline("[点我](javascript:alert(1))")
        self.assertNotIn("<a ", out)

    def test_plain_text_escaped(self):
        out = va._inline("<b>粗</b>")
        self.assertEqual(out, "&lt;b&gt;粗&lt;/b&gt;")


class MdToHtmlTest(unittest.TestCase):
    """Markdown 渲染。"""

    def test_headings(self):
        out = va._md_to_html("# 一\n## 二\n### 三")
        self.assertIn("<h2>一</h2>", out)
        self.assertIn("<h3>二</h3>", out)
        self.assertIn("<h4>三</h4>", out)

    def test_heading_order_matters(self):
        """### 必须先判，否则会被 ## 分支吃掉。"""
        out = va._md_to_html("### 三级")
        self.assertIn("<h4>", out)
        self.assertNotIn("<h3>", out)

    def test_list(self):
        out = va._md_to_html("- 甲\n- 乙")
        self.assertIn("<ul>", out)
        self.assertEqual(out.count("<li>"), 2)
        self.assertIn("</ul>", out)

    def test_list_closed_at_end(self):
        """文件末尾的列表必须闭合，否则 HTML 一直开到 </body>。"""
        out = va._md_to_html("- 甲\n- 乙")
        self.assertTrue(out.rstrip().endswith("</ul>"))

    def test_list_closed_by_paragraph(self):
        out = va._md_to_html("- 甲\n普通段落")
        self.assertIn("</ul>", out)
        self.assertIn("<p>普通段落</p>", out)

    def test_list_closed_by_heading(self):
        out = va._md_to_html("- 甲\n## 新章节")
        self.assertIn("</ul>", out)
        self.assertIn("<h3>新章节</h3>", out)

    def test_escapes_in_heading(self):
        out = va._md_to_html("# <script>x</script>")
        self.assertNotIn("<script>", out)
        self.assertIn("&lt;script&gt;", out)

    def test_escapes_in_list_item(self):
        out = va._md_to_html("- <img onerror=1>")
        self.assertNotIn("<img", out)

    def test_empty_input(self):
        self.assertEqual(va._md_to_html(""), "")
        self.assertEqual(va._md_to_html("\n\n\n"), "")


class LocalKeyTest(unittest.TestCase):
    """读本机 OpenClaw 配置里的 key。"""

    def _write_cfg(self, root, key):
        """local_key 读的是 <STATE_DIR>/openclaw.json（不是再嵌一层 openclaw/）。"""
        with open(os.path.join(root, "openclaw.json"), "w", encoding="utf-8") as f:
            json.dump({"models": {"providers": {"agnes": {"apiKey": key}}}}, f)
        return root

    def test_reads_from_state_dir_env(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._write_cfg(tmp, "sk-test-123")
            with patch.dict(os.environ, {"OPENCLAW_STATE_DIR": tmp}):
                self.assertEqual(va.local_key(), "sk-test-123")

    def test_strips_whitespace(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._write_cfg(tmp, "  sk-padded  \n")
            with patch.dict(os.environ, {"OPENCLAW_STATE_DIR": tmp}):
                self.assertEqual(va.local_key(), "sk-padded")

    def test_missing_dir_returns_empty(self):
        """没有这个目录就是没有，不是崩。"""
        with patch.dict(os.environ, {"OPENCLAW_STATE_DIR": "/nonexistent/xyz"}):
            with patch("os.path.expanduser", side_effect=lambda p: "/nonexistent/" + p):
                self.assertEqual(va.local_key(), "")

    def test_broken_json_returns_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "openclaw.json"), "w") as f:
                f.write("{ 这不是 json")
            with patch.dict(os.environ, {"OPENCLAW_STATE_DIR": tmp}):
                self.assertEqual(va.local_key(), "")

    def test_empty_key_returns_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._write_cfg(tmp, "   ")
            with patch.dict(os.environ, {"OPENCLAW_STATE_DIR": tmp}):
                self.assertEqual(va.local_key(), "")


class SoftDependencyTest(unittest.TestCase):
    """webview / playwright 必须是软依赖。"""

    def test_module_imports_without_gui(self):
        """能 import 就说明软依赖生效了（测试本身就是证据）。"""
        self.assertTrue(hasattr(va, "_extract_json"))
        self.assertTrue(hasattr(va, "_md_to_html"))

    def test_missing_deps_have_clear_message(self):
        """缺依赖时 run_agent 要说人话，不是 NoneType 报错。"""
        old = va.sync_playwright
        try:
            va.sync_playwright = None
            out = va.run_agent("随便搜点什么", {}, headless=True)
            self.assertIn("playwright", out)
            self.assertIn("install", out)
        finally:
            va.sync_playwright = old


class NoHardcodedPathTest(unittest.TestCase):
    """回归防护：启动脚本曾硬编码作者本机路径，导致别人 clone 就跑不起来。"""

    def test_launchers_use_dp0_not_absolute(self):
        import glob
        import re
        root = os.path.dirname(os.path.abspath(__file__))
        for f in glob.glob(os.path.join(root, "*.bat")) + \
                 glob.glob(os.path.join(root, "*.sh")):
            text = open(f, encoding="utf-8", errors="replace").read()
            for m in re.finditer(r"[A-Za-z]:\\Users\\[^\\\s\"']+", text):
                self.fail(f"{os.path.basename(f)} 里硬编码了个人路径: {m.group(0)}")
            for m in re.finditer(r"/Users/[a-z][^/\s\"']*", text):
                self.fail(f"{os.path.basename(f)} 里硬编码了个人路径: {m.group(0)}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
