"""Tests for the Markdown renderer, with an emphasis on HTML safety."""

import markdown


def test_headings_get_slugs_and_outline():
    result = markdown.render_document("# Hello World\n\n## Part One\n\n### Part One\n")
    assert result.html == (
        '<h1 id="hello-world">Hello World</h1>\n'
        '<h2 id="part-one">Part One</h2>\n'
        '<h3 id="part-one-2">Part One</h3>'
    )
    assert [h["id"] for h in result.headings] == ["hello-world", "part-one", "part-one-2"]
    assert result.headings[0] == {"level": 1, "text": "Hello World", "id": "hello-world"}


def test_inline_emphasis():
    assert markdown.render("**bold** and *italic* and ~~gone~~") == (
        "<p><strong>bold</strong> and <em>italic</em> and <del>gone</del></p>"
    )


def test_underscore_emphasis_does_not_break_snake_case():
    html = markdown.render("call snake_case_name here and _this_ works")
    assert "snake_case_name" in html
    assert "<em>this</em>" in html


def test_bold_is_not_read_as_italic():
    assert markdown.render("**a**") == "<p><strong>a</strong></p>"


def test_code_span_content_is_escaped_and_not_parsed():
    html = markdown.render("`<script>alert(1)</script> **not bold**`")
    assert "<script>" not in html
    assert "&lt;script&gt;" in html
    assert "<strong>" not in html


def test_raw_html_is_escaped_everywhere():
    html = markdown.render('<img src=x onerror="alert(1)">\n\n<b>no</b>')
    assert "<img src=x" not in html
    assert "<b>" not in html
    assert "&lt;b&gt;" in html


def test_javascript_links_are_refused():
    html = markdown.render("[click](javascript:alert(1))")
    assert "javascript:" in html  # shown literally, not as a link
    assert "<a " not in html


def test_data_url_images_are_refused():
    html = markdown.render("![x](data:text/html;base64,PHNjcmlwdD4=)")
    assert "<img" not in html


def test_allowed_links_and_images():
    html = markdown.render("[docs](https://example.com/a?b=1&c=2) ![logo](https://example.com/l.png)")
    assert '<a href="https://example.com/a?b=1&amp;c=2" rel="noopener noreferrer">docs</a>' in html
    assert '<img src="https://example.com/l.png" alt="logo">' in html


def test_mailto_autolink():
    assert markdown.render("<mailto:hi@example.com>") == (
        '<p><a href="mailto:hi@example.com" rel="noopener noreferrer">mailto:hi@example.com</a></p>'
    )


def test_fenced_code_block_keeps_language_and_content():
    html = markdown.render("```python\ndef f(x):\n    return x < 1\n```")
    assert html == (
        '<pre><code class="language-python">def f(x):\n    return x &lt; 1</code></pre>'
    )


def test_unclosed_fence_runs_to_end_of_document():
    html = markdown.render("```\nstill code")
    assert html.startswith("<pre><code>")


def test_blockquote_contains_parsed_blocks():
    html = markdown.render("> quoted **text**\n> more")
    assert html == "<blockquote><p>quoted <strong>text</strong>\nmore</p></blockquote>"


def test_unordered_and_ordered_lists():
    html = markdown.render("- one\n- two\n\n1. first\n2. second")
    assert html == "<ul><li>one</li><li>two</li></ul>\n<ol><li>first</li><li>second</li></ol>"


def test_ordered_list_start_attribute():
    assert markdown.render("3. three\n4. four") == '<ol start="3"><li>three</li><li>four</li></ol>'


def test_nested_lists():
    html = markdown.render("- outer\n  - inner\n  - inner two\n- outer two")
    assert html == (
        "<ul><li>outer<ul><li>inner</li><li>inner two</li></ul></li>"
        "<li>outer two</li></ul>"
    )


def test_lazy_continuation_belongs_to_the_item():
    assert markdown.render("- wrapped\n  line two") == "<ul><li>wrapped line two</li></ul>"


def test_horizontal_rule():
    assert markdown.render("---") == "<hr>"
    assert markdown.render("***") == "<hr>"


def test_paragraph_break_on_blank_line():
    assert markdown.render("one\n\ntwo") == "<p>one</p>\n<p>two</p>"


def test_empty_document_renders_empty_string():
    assert markdown.render("") == ""
    assert markdown.render_document("   \n\n").headings == []


def test_nul_bytes_are_dropped():
    assert "\x00" not in markdown.render("a\x00b")


def test_slugify_handles_punctuation_and_empty_input():
    assert markdown.slugify("Hello, World!") == "hello-world"
    assert markdown.slugify("!!!") == "section"
    assert markdown.slugify("<em>x</em>") == "x"
