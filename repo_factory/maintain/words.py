"""What repo-factory writes on GitHub and reads back, in one leaf module (it imports nothing of
repo-factory, so it stays out of maintenance's import cycle): its signature, the
``revise-meta`` line of a fix pull request's body, the ticket marker of an issue it opened,
and how text that came from a model or from someone's issue is made safe: ``sanitize`` before
it is posted, ``_doc`` before it goes inside a prompt's document markers."""

from __future__ import annotations

import re

SIGNATURE = (
    "repo-factory (automated; the owner reviews everything, and nothing merges unless "
    "`.github/repo-factory.yml` says so)"
)
META = "<!-- repo-factory:revise-meta"
META_FIELDS = re.compile(r"(\w+)=(\S+)")
TICKET = "<!-- repo-factory:ticket v1 -->"  # an issue repo-factory opened (Open issue)
MAX_CHARS = 1200  # a model's words in one post, at most
MENTION = re.compile(r"(?<![\w`])@([A-Za-z0-9](?:[A-Za-z0-9-]{0,38}))(/[A-Za-z0-9_.-]+)?")
URL = re.compile(r"(?i)\b(?:https?://|ftp://|www\.)\S+|\[([^\]]*)\]\([^)]*\)")


def meta(body: str) -> dict | None:
    """The fields of repo-factory's ``<!-- repo-factory:revise-meta ... -->`` line in a pull request body."""
    for line in (body or "").splitlines():
        if line.strip().startswith(META):
            return dict(META_FIELDS.findall(line.strip()[len(META) :].rstrip("->").strip()))
    return None


def _doc(text: str) -> str:
    """Someone's text inside a document block cannot close it early."""
    return (text or "").replace("<<<", "‹‹‹").replace(">>>", "›››")


def sanitize(text: str | None, limit: int = MAX_CHARS) -> str:
    """Model text made safe to post: no HTML comments (a forged marker), no links it wrote
    (a Markdown link keeps its words), every ``@mention`` neutralised as code, capped."""
    t = str(text or "")
    t = t.replace("<!--", "").replace("-->", "").replace("`", "'")
    t = re.sub(r"<[^>]{0,200}>", "", t)  # no HTML tags at all
    t = URL.sub(lambda m: m.group(1) or "", t)
    t = MENTION.sub(lambda m: f"`@{m.group(1)}{m.group(2) or ''}`", t)
    t = re.sub(r"[ \t]+", " ", t).strip()
    if len(t) > limit:
        t = t[: limit - 1].rstrip() + "…"
    return t
