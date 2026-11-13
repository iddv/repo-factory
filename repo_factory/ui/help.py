"""/help: the longer explanations the app's ? tips link to (UX-PLAN.md 2.4), from ``help.md``
beside this file, rendered here as a small read-only page in the app's theme. The Markdown is a
small subset: headings (``## Title {#id}``, the id a tip links to), paragraphs, ``code``,
**bold** and links. Every text is escaped; nothing from a request goes into the page."""

import html
import re
from pathlib import Path

HELP = Path(__file__).with_name("help.md")


def _inline(text: str) -> str:
    out = html.escape(text, quote=True)
    out = re.sub(r"`([^`]+)`", r"<code>\1</code>", out)
    out = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", out)
    # a link: the web, a heading here, or a place in the app (./#/…: Send feedback's sheet)
    return re.sub(
        r"\[([^\]]+)\]\((https?://[^)\s]+|#[\w-]+|\./#/[\w/?=&;:.-]*)\)",
        r'<a href="\2">\1</a>',
        out,
    )


def sections(text: str | None = None) -> list[tuple[str, str]]:
    """The (id, title) of every ``##`` heading: what a tip may link to."""
    text = HELP.read_text() if text is None else text
    return [
        (m.group(2), m.group(1).strip())
        for m in re.finditer(r"^## (.+?) \{#([\w-]+)\}\s*$", text, re.M)
    ]


def render(text: str) -> str:
    body, para = [], []

    def flush():
        if para:
            body.append("<p>" + _inline(" ".join(para)) + "</p>")
            para.clear()

    for line in text.splitlines():
        m = re.match(r"^(#{1,3}) (.+?)(?: \{#([\w-]+)\})?\s*$", line)
        if m:
            flush()
            n, title, hid = len(m.group(1)), m.group(2), m.group(3)
            attr = f' id="{hid}"' if hid else ""
            body.append(f"<h{n}{attr}>{_inline(title)}</h{n}>")
        elif not line.strip():
            flush()
        else:
            para.append(line.strip())
    flush()
    return "\n".join(body)


def page() -> str:
    return (
        '<!doctype html>\n<html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        '<meta name="color-scheme" content="light dark"><title>Help · repo-factory</title>'
        '<script src="static/theme.js"></script>'
        '<link rel="stylesheet" href="static/repo-factory.css"><link rel="stylesheet" href="static/looks.css">'
        '</head><body class="helpdoc"><main class="helpdoc-in prose">'
        '<p class="small"><a href="./">‹ repo-factory</a></p>\n'
        + render(HELP.read_text())
        + "\n</main></body></html>\n"
    )
