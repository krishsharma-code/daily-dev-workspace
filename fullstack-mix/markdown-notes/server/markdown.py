"""A small, escape-first Markdown renderer.

Notes are written by the user and rendered to HTML in the browser, so the
renderer is deliberately conservative: every byte of input is HTML-escaped
*before* any markup is generated, and links are only emitted for a fixed
allow-list of URL schemes. Raw HTML in the source is therefore never passed
through -- it shows up as literal text.

Supported syntax
----------------
Blocks: ATX headings (``#`` .. ``######``), fenced code blocks (``` ``` ``` or
``~~~``), block quotes, nested ordered/unordered lists, horizontal rules and
paragraphs.

Inline: code spans, bold, italic, strikethrough, links and images (http,
https and mailto only), and autolinks written as ``<https://example.com>``.

Not supported (by design): raw HTML, tables, footnotes and reference-style
links.

    >>> from markdown import render
    >>> render("# Hi")
    '<h1 id="hi">Hi</h1>'
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from typing import Optional

__all__ = ["MarkdownResult", "render", "render_document", "slugify"]

#: Schemes allowed in ``[text](url)`` and ``![alt](url)``.
ALLOWED_SCHEMES = ("http://", "https://", "mailto:")

MAX_HEADING_TEXT = 120

_FENCE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})[ \t]*([^\s`]*)[ \t]*$")
_HEADING_RE = re.compile(r"^ {0,3}(#{1,6})[ \t]+(.*?)[ \t]*#*[ \t]*$")
_HR_RE = re.compile(r"^ {0,3}([-*_])[ \t]*(?:\1[ \t]*){2,}$")
_QUOTE_RE = re.compile(r"^ {0,3}>[ \t]?(.*)$")
_LIST_ITEM_RE = re.compile(r"^([ \t]*)([-*+]|\d{1,9}[.)])[ \t]+(.*)$")
_SLUG_STRIP_RE = re.compile(r"<[^>]*>")

_CODE_SPAN_RE = re.compile(r"(`+)(.+?)\1")
_AUTOLINK_RE = re.compile(r"<((?:https?://|mailto:)[^\s<>]+)>")
_LINK_RE = re.compile(r"(!?)\[([^\]]*)\]\(([^()\s]*)\)")
_STRONG_RE = re.compile(r"\*\*(?=\S)(.+?)(?<=\S)\*\*")
_STRIKE_RE = re.compile(r"~~(?=\S)(.+?)(?<=\S)~~")
_EM_STAR_RE = re.compile(r"(?<![\w*])\*(?=\S)(.+?)(?<=\S)\*(?![\w*])")
_EM_UNDER_RE = re.compile(r"(?<![\w_])_(?=\S)(.+?)(?<=\S)_(?![\w_])")


@dataclass
class MarkdownResult:
    """Rendered HTML plus the outline extracted while rendering."""

    html: str
    headings: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"html": self.html, "headings": self.headings}


def slugify(text: str, used: Optional[dict] = None) -> str:
    """Turn heading text into a stable, unique ``id``.

    Duplicates get a ``-2``, ``-3`` .. suffix so anchors stay valid when a
    document repeats a heading.
    """
    slug = _SLUG_STRIP_RE.sub("", text).lower()
    slug = re.sub(r"[^a-z0-9]+", "-", slug).strip("-") or "section"
    if used is None:
        return slug
    seen = used.get(slug, 0) + 1
    used[slug] = seen
    return slug if seen == 1 else f"{slug}-{seen}"


def _placeholder(store: list, value: str) -> str:
    store.append(value)
    return f"\x00{len(store) - 1}\x00"


def _restore(text: str, store: list) -> str:
    for index, value in enumerate(store):
        text = text.replace(f"\x00{index}\x00", value)
    return text


def _safe_url(url: str) -> Optional[str]:
    """Return ``url`` if its scheme is allowed, else ``None``."""
    candidate = html.unescape(url).strip()
    lowered = candidate.lower()
    if lowered.startswith(ALLOWED_SCHEMES):
        # Reject control characters that could smuggle a second scheme.
        if any(ch in candidate for ch in "\x00\r\n\t"):
            return None
        return html.escape(candidate, quote=True)
    return None


def render_inline(text: str) -> str:
    """Render inline Markdown in ``text`` to HTML."""
    text = text.replace("\x00", "")
    stash: list = []

    # 1. Code spans and autolinks are pulled out before escaping so their
    #    contents are not treated as emphasis.
    def take_code(match: "re.Match[str]") -> str:
        body = html.escape(match.group(2).strip(), quote=False)
        return _placeholder(stash, f"<code>{body}</code>")

    def take_autolink(match: "re.Match[str]") -> str:
        href = _safe_url(match.group(1))
        if href is None:
            return match.group(0)
        label = html.escape(html.unescape(match.group(1)), quote=False)
        return _placeholder(
            stash, f'<a href="{href}" rel="noopener noreferrer">{label}</a>'
        )

    text = _CODE_SPAN_RE.sub(take_code, text)
    text = _AUTOLINK_RE.sub(take_autolink, text)

    # 2. Escape everything that is left. From here on, the only markup in the
    #    string is markup this module emits itself.
    text = html.escape(text, quote=False)

    # 3. Links and images. Disallowed URLs degrade to their literal source.
    def take_link(match: "re.Match[str]") -> str:
        bang, label, url = match.groups()
        href = _safe_url(url)
        if href is None:
            return match.group(0)
        inner = label.strip() or href
        if bang:
            return _placeholder(
                stash, f'<img src="{href}" alt="{html.escape(html.unescape(label), quote=True)}">'
            )
        return _placeholder(
            stash,
            f'<a href="{href}" rel="noopener noreferrer">{inner}</a>',
        )

    text = _LINK_RE.sub(take_link, text)

    # 4. Emphasis. Strong runs before italic so ``**`` is not read as ``*``.
    text = _STRONG_RE.sub(r"<strong>\1</strong>", text)
    text = _STRIKE_RE.sub(r"<del>\1</del>", text)
    text = _EM_STAR_RE.sub(r"<em>\1</em>", text)
    text = _EM_UNDER_RE.sub(r"<em>\1</em>", text)

    return _restore(text, stash)


def _is_block_start(line: str) -> bool:
    return bool(
        _FENCE_RE.match(line)
        or _HEADING_RE.match(line)
        or _HR_RE.match(line)
        or _QUOTE_RE.match(line)
        or _LIST_ITEM_RE.match(line)
    )


def _indent_width(raw: str) -> int:
    return len(raw.expandtabs(4))


def _nest(items: list) -> list:
    """Build a tree from a flat list of ``{indent, ordered, number, text}``."""
    roots: list = []
    stack: list = []
    for item in items:
        node = dict(item, children=[])
        while stack and item["indent"] <= stack[-1]["indent"]:
            stack.pop()
        if stack:
            stack[-1]["children"].append(node)
        else:
            roots.append(node)
        stack.append(node)
    return roots


def _render_list(nodes: list) -> str:
    if not nodes:
        return ""
    ordered = nodes[0]["ordered"]
    tag = "ol" if ordered else "ul"
    attrs = ""
    if ordered and nodes[0]["number"] not in (1, None):
        attrs = f' start="{nodes[0]["number"]}"'
    parts = [f"<{tag}{attrs}>"]
    for node in nodes:
        body = render_inline(" ".join(node["text"]).strip())
        parts.append(f"<li>{body}{_render_list(node['children'])}</li>")
    parts.append(f"</{tag}>")
    return "".join(parts)


def _collect_list(lines: list, start: int) -> tuple[list, int]:
    """Gather one list block starting at ``start``; return items and next index."""
    items: list = []
    index = start
    while index < len(lines):
        line = lines[index]
        match = _LIST_ITEM_RE.match(line)
        if match:
            marker = match.group(2)
            items.append(
                {
                    "indent": _indent_width(match.group(1)),
                    "ordered": marker[0].isdigit(),
                    "number": int(marker[:-1]) if marker[0].isdigit() else None,
                    "text": [match.group(3)],
                }
            )
            index += 1
            continue

        if not line.strip():
            # A blank line only continues the list if the next item is nested.
            lookahead = index + 1
            while lookahead < len(lines) and not lines[lookahead].strip():
                lookahead += 1
            if lookahead < len(lines):
                nxt = _LIST_ITEM_RE.match(lines[lookahead])
                if nxt and _indent_width(nxt.group(1)) > 0:
                    index = lookahead
                    continue
            break

        if items and (_indent_width(line[: len(line) - len(line.lstrip())]) > 0):
            items[-1]["text"].append(line.strip())
            index += 1
            continue

        # Lazy continuation: plain text directly under an item.
        if items and not _is_block_start(line):
            items[-1]["text"].append(line.strip())
            index += 1
            continue

        break
    return items, index


def _parse_blocks(lines: list, used_slugs: dict, headings: list) -> str:
    out: list = []
    index = 0
    while index < len(lines):
        line = lines[index]

        if not line.strip():
            index += 1
            continue

        fence = _FENCE_RE.match(line)
        if fence:
            marker, lang = fence.group(1), fence.group(2)
            body: list = []
            index += 1
            while index < len(lines):
                closing = _FENCE_RE.match(lines[index])
                if closing and closing.group(1)[0] == marker[0]:
                    index += 1
                    break
                body.append(lines[index])
                index += 1
            code = html.escape("\n".join(body), quote=False)
            language = re.sub(r"[^A-Za-z0-9_+-]", "", lang)
            css = f' class="language-{language}"' if language else ""
            out.append(f"<pre><code{css}>{code}</code></pre>")
            continue

        heading = _HEADING_RE.match(line)
        if heading:
            level = len(heading.group(1))
            inner = render_inline(heading.group(2))
            slug = slugify(inner, used_slugs)
            if len(headings) < 500:
                headings.append(
                    {
                        "level": level,
                        "text": _SLUG_STRIP_RE.sub("", inner)[:MAX_HEADING_TEXT],
                        "id": slug,
                    }
                )
            out.append(f'<h{level} id="{slug}">{inner}</h{level}>')
            index += 1
            continue

        if _HR_RE.match(line):
            out.append("<hr>")
            index += 1
            continue

        quote = _QUOTE_RE.match(line)
        if quote:
            quoted: list = []
            while index < len(lines):
                inner_match = _QUOTE_RE.match(lines[index])
                if inner_match is None:
                    break
                quoted.append(inner_match.group(1))
                index += 1
            out.append(
                "<blockquote>"
                + _parse_blocks(quoted, used_slugs, headings)
                + "</blockquote>"
            )
            continue

        if _LIST_ITEM_RE.match(line):
            items, index = _collect_list(lines, index)
            out.append(_render_list(_nest(items)))
            continue

        paragraph: list = []
        while index < len(lines) and lines[index].strip() and not _is_block_start(
            lines[index]
        ):
            paragraph.append(lines[index].strip())
            index += 1
        out.append(f"<p>{render_inline(chr(10).join(paragraph))}</p>")

    return "\n".join(out)


def render_document(text: str) -> MarkdownResult:
    """Render ``text`` and return both the HTML and the heading outline."""
    source = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    headings: list = []
    body = _parse_blocks(source.split("\n"), {}, headings)
    return MarkdownResult(html=body, headings=headings)


def render(text: str) -> str:
    """Render ``text`` to an HTML fragment."""
    return render_document(text).html
