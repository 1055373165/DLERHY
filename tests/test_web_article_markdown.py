"""A web article's Markdown (its reading copy and blog post) is read from the translated page, so it
keeps the page's pictures, tables, code, links and lists.

Harness design for long-running application development (anthropic.com) went to the blog as bare
paragraphs: eight pictures, three tables, a video, the code and every link were gone, and "our<a>
Labs</a>" read "ourLabs".
"""

from __future__ import annotations

import unittest

from book_agent.domain.structure import web
from book_agent.export.web_markdown import translated_page_html, translated_page_markdown

PAGE = """<!DOCTYPE html><html lang="en"><head><title>Harness design \\ Anthropic</title></head><body>
<header><nav><a href="/news">News</a></nav></header>
<main>
<section><a href="/engineering">Engineering at Anthropic</a>
<img alt="" src="https://cdn.example.com/hero-2554x2554.svg">
<h1>Harness design</h1><p>Published <time>Mar 24, 2026</time></p></section>
<article><div>
<p><em>Written by Prithvi, a member of our<a href="https://example.com/labs"> Labs</a> team.</em></p>
<p><br/></p>
<h2>Why naive implementations fall short</h2>
<p>We used <strong>a generator</strong> and <code>evaluator.py</code>, see <a href="/engineering/context">context engineering</a>.</p>
<ul><li><strong>Design quality:</strong> Does it feel whole?</li><li>Originality<ul><li>Nested point</li></ul></li></ul>
<figure><img alt="" src="https://example.com/_next/image?url=https%3A%2F%2Fcdn.example.com%2Fimages%2Fsolo.png&amp;w=3840&amp;q=75">
<figcaption>Initial screen of the solo harness.<br/></figcaption></figure>
<table><tbody><tr><th><strong>Harness</strong></th><th><strong>Cost</strong></th></tr><tr><td>Solo</td><td>$9</td></tr></tbody></table>
<blockquote><em>Create a 2D retro game maker.</em></blockquote>
<figure><video controls src="https://cdn.example.com/museum.mp4"></video></figure>
<pre><code class="python">print("plan")
print("done")</code><button><span>Copy</span></button></pre>
</div></article>
<div><h2>Get the developer newsletter</h2><form><input type="email"></form></div>
</main>
<footer><a href="/terms">Terms of service</a></footer>
</body></html>"""

ZH = {
    "Engineering at Anthropic": "Anthropic 工程团队",
    "Harness design": "harness 设计",
    "Published Mar 24, 2026": "发布于 2026 年 3 月 24 日",
    "Written by Prithvi, a member of our [Labs][1] team.": "作者：Prithvi，我们 [Labs][1] 团队的一员。",
    "Why naive implementations fall short": "为什么朴素实现难以奏效",
    "We used **a generator** and `evaluator.py`, see [context engineering][1].": "我们用了**生成器**和 `evaluator.py`，参见[上下文工程][1]。",
    "**Design quality:** Does it feel whole?": "**设计质量：**是否浑然一体？",
    "Originality": "原创性",
    "Nested point": "嵌套要点",
    "Initial screen of the solo harness.": "单代理 harness 的初始界面。",
    "Harness": "Harness",
    "Cost": "成本",
    "Solo": "单代理",
    "Create a 2D retro game maker.": "做一个 2D 复古游戏制作器。",
    "Get the developer newsletter": "订阅开发者资讯",
}


def _translate(page: str) -> tuple[str, dict[int, str], dict[int, str]]:
    root = web.parse_html(page)
    web.mark_translatable_nodes(root)
    marked = web.serialize(root)
    translations: dict[int, str] = {}
    sources: dict[int, str] = {}
    for node_id, element in web.marked_nodes(web.parse_html(marked)):
        source, _tags = web.node_source_text(element)
        sources[node_id] = web.plain_text(source)
        if source in ZH:
            translations[node_id] = ZH[source]
    return marked, translations, sources


class WebArticleMarkdownTests(unittest.TestCase):
    def setUp(self) -> None:
        self.page, self.translations, self.sources = _translate(PAGE)
        self.markdown = translated_page_markdown(
            self.page, self.translations, base_url="https://www.anthropic.com/engineering/harness", skip_title="harness 设计"
        )

    def test_the_article_keeps_its_pictures_tables_code_links_and_lists(self) -> None:
        md = self.markdown
        # The picture behind the site's resizer is linked at its original address, its caption under it.
        self.assertIn("![](https://cdn.example.com/images/solo.png)\n\n*单代理 harness 的初始界面。*", md)
        self.assertIn("| **Harness** | **成本** |\n| --- | --- |\n| 单代理 | $9 |", md)
        self.assertIn('```python\nprint("plan")\nprint("done")\n```', md)
        self.assertIn("我们用了**生成器**和 `evaluator.py`，参见[上下文工程](https://www.anthropic.com/engineering/context)。", md)
        self.assertIn("- **设计质量：**是否浑然一体？\n- 原创性\n   - 嵌套要点", md)
        self.assertIn("> *做一个 2D 复古游戏制作器。*", md)
        self.assertIn("[▶ 视频](https://cdn.example.com/museum.mp4)", md)
        self.assertIn("## 为什么朴素实现难以奏效", md)

    def test_the_site_around_the_article_is_left_out(self) -> None:
        md = self.markdown
        for site_text in ("News", "Terms of service", "订阅开发者资讯", "Get the developer newsletter", "Copy", "hero"):
            self.assertNotIn(site_text, md)
        # The title is set above the body by the caller: the heading, and the section label above it, go.
        self.assertNotIn("harness 设计", md)
        self.assertNotIn("工程团队", md)
        self.assertTrue(md.startswith("发布于 2026 年 3 月 24 日\n\n*作者：Prithvi，我们 [Labs](https://example.com/labs) 团队的一员。*"))

    def test_the_bilingual_edition_quotes_the_english_under_each_passage(self) -> None:
        md = translated_page_markdown(self.page, self.translations, sources=self.sources, skip_title="harness 设计")
        self.assertIn("## 为什么朴素实现难以奏效\n\n> Why naive implementations fall short", md)
        self.assertIn("- 原创性\n   > Originality\n   - 嵌套要点\n      > Nested point", md)
        self.assertNotIn("> Harness design\n", md)

    def test_the_reading_copy_html_keeps_the_page_markup_without_its_site(self) -> None:
        page = translated_page_html(
            self.page, self.translations, base_url="https://www.anthropic.com/engineering/harness", skip_title="harness 设计"
        )
        self.assertIn('<figure><img src="https://cdn.example.com/images/solo.png" alt="" loading=\'lazy\'> <figcaption>单代理 harness 的初始界面。</figcaption></figure>', page)
        self.assertIn("<table><tbody><tr><th><strong>Harness</strong></th><th><strong>成本</strong></th></tr>", page)
        self.assertIn('<pre><code class="python">print(&quot;plan&quot;)\nprint(&quot;done&quot;)</code></pre>'.replace("&quot;", '"'), page)
        self.assertIn('<a href="https://www.anthropic.com/engineering/context" target="_blank" rel="noopener">上下文工程</a>', page)
        self.assertIn('<video src="https://cdn.example.com/museum.mp4" controls></video>', page)
        self.assertIn("<ul><li><strong>设计质量：</strong>是否浑然一体？</li><li>原创性<ul><li>嵌套要点</li></ul></li></ul>", page)
        self.assertIn("<h2>为什么朴素实现难以奏效</h2>", page)
        for site in ("News", "Terms of service", "订阅开发者资讯", "Copy", "harness 设计<", "工程团队", "style=", "<p><br></p>"):
            self.assertNotIn(site, page)
        self.assertTrue(page.startswith("<p>发布于 2026 年 3 月 24 日</p>"), page[:80])

    def test_a_space_just_inside_a_link_stays_between_the_words(self) -> None:
        root = web.parse_html('<p>a member of our<a href="/labs"> Labs</a> team, see<em> this </em>now.</p>')
        text, _tags = web.node_source_text(web.find_first(root, "p"))
        self.assertEqual(text, "a member of our [Labs][1] team, see *this* now.")


if __name__ == "__main__":
    unittest.main()
