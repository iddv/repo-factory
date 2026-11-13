"""Triage (MAINTAIN.md §5.1): a new issue gets a kind, a priority and one comment.

``classify`` asks a **no-tools** model (Haiku 5.5 by default, ``maintain.triage_model``) about
the issue's text, which sits inside document delimiters: anyone can write an issue, so its text
is data. The reply is parsed against a fixed vocabulary (``labels.KINDS``, ``labels.PRIORITIES``,
an open issue's number, one question, a summary, an answer); anything else in it is dropped, so
no reply can ever produce ``go`` or any other label. ``decide`` turns the reading and the trusted
facts (the policy, who has write access) into labels; only those facts can add ``go``.
``apply`` writes the labels and one templated comment, edited in place on a re-triage, with the
model's words passed through ``sanitize`` (no mentions, no links it wrote, capped length)."""

from __future__ import annotations

import re
from pathlib import Path
from string import Template

from ..loop.ledger import normalise
from . import labels as L

PROMPT = Path(__file__).resolve().parents[1] / "loop" / "prompts" / "triage.md"
MARKER = "<!-- repo-factory:triage v1 -->"
SIGNATURE = "repo-factory (automated triage; the owner reviews everything before anything changes)"
MAX_CHARS = 1200  # the model's words in one comment, at most
SUMMARY_WORDS = 40
KIND_WORDS = {
    "bug": "a bug report",
    "feature": "a feature request",
    "question": "a question",
    "security": "a security report",
    "dependency": "a dependency update",
    "docs": "a documentation issue",
}


def _doc(text: str) -> str:
    """Reporter text inside a document block cannot close it early."""
    return (text or "").replace("<<<", "‹‹‹").replace(">>>", "›››")


def prompt(
    item: dict,
    *,
    product: str,
    one_liner: str,
    open_issues: list[dict],
    readme: str = "",
    comments: list[dict] | None = None,
) -> str:
    body = item.get("body") or "(no description)"
    for c in comments or []:
        body += f"\n\n--- a comment by the reporter ---\n{c['body']}"
    listing = (
        "\n".join(
            f"- #{i['number']}: {_doc(i['title'])}"
            for i in open_issues
            if i["number"] != item["number"]
        )
        or "(none)"
    )
    from ..custom_prompts import text

    return Template(text("triage")).safe_substitute(
        PRODUCT=product,
        ONE_LINER=one_liner or "",
        OPEN=listing,
        README=_doc(readme[:6000]) or "(no README)",
        WHAT="pull request" if item.get("is_pr") else "issue",
        NUMBER=item["number"],
        TITLE=_doc(item["title"]),
        BODY=_doc(body[:12000]),
    )


def parse(text: str, item: dict, open_numbers: set[int]) -> dict | None:
    """The model's reply, kept only where it is inside the vocabulary; None when it is not
    usable at all (no JSON, or no valid kind or priority)."""
    from ..loop.pitch import parse as first_json

    d = first_json(text)
    if not d:
        return None
    kind = str(d.get("kind") or "").strip().lower()
    prio = str(d.get("prio") or d.get("priority") or "").strip().lower()
    if kind not in L.KINDS or prio not in L.PRIORITIES:
        return None
    dup = d.get("duplicate_of")
    try:
        dup = int(str(dup).lstrip("#")) if dup not in (None, "", False) else None
    except ValueError:
        dup = None
    if dup == item["number"] or dup not in open_numbers:
        dup = None
    words = " ".join(str(d.get("summary") or "").split()).split(" ")
    summary = " ".join(words[:SUMMARY_WORDS]) + ("…" if len(words) > SUMMARY_WORDS else "")
    ask = " ".join(str(d.get("needs_info") or "").split()) or None
    answer = str(d.get("answer") or "").strip() or None
    return {
        "kind": kind,
        "prio": prio,
        "duplicate_of": dup,
        "needs_info": ask,
        "summary": summary.strip(),
        "answer": answer if kind == "question" else None,
    }


def classify(
    item: dict,
    model,
    *,
    product: str,
    one_liner: str = "",
    open_issues: list[dict] | None = None,
    readme: str = "",
    comments: list[dict] | None = None,
) -> tuple[dict | None, float]:
    """``(reading or None, usd)``: one call, and one retry on a reply outside the vocabulary.
    A cheap host check finds a duplicate whose normalised title is the same. The pair is a
    ``loop.models.Costed``: ``.tokens`` holds the calls' tokens (None when the model
    reported none)."""
    from ..loop.models import Costed, add, split

    open_issues = [i for i in open_issues or [] if not i.get("is_pr")]
    numbers = {i["number"] for i in open_issues if i["number"] != item["number"]}
    text = prompt(
        item,
        product=product,
        one_liner=one_liner,
        open_issues=open_issues,
        readme=readme,
        comments=comments,
    )
    usd, res, tokens = 0.0, None, None
    for _ in range(2):
        reply, cost, tok = split(model(text))
        usd += float(cost or 0)
        tokens = add(tokens, tok)
        res = parse(reply, item, numbers)
        if res:
            break
    if res and res["duplicate_of"] is None and not item.get("is_pr"):
        mine = normalise(item["title"])
        same = [
            i["number"]
            for i in open_issues
            if i["number"] != item["number"]
            and i["number"] < item["number"]
            and mine
            and normalise(i["title"]) == mine
        ]
        if same:
            res["duplicate_of"] = same[0]
    return Costed(res, round(usd, 6), tokens)


# -- what may be posted ------------------------------------------------------------------------
MENTION = re.compile(r"(?<![\w`])@([A-Za-z0-9](?:[A-Za-z0-9-]{0,38}))(/[A-Za-z0-9_.-]+)?")
URL = re.compile(r"(?i)\b(?:https?://|ftp://|www\.)\S+|\[([^\]]*)\]\([^)]*\)")


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


def decide(
    item: dict,
    res: dict,
    policy: dict,
    *,
    writers: set[str] | None = None,
    owner: str | None = None,
    bot: str | None = None,
) -> dict:
    """The labels a reading leads to. ``go`` comes only from trusted facts: ``revise.trigger:
    owner`` and the owner opened it (or repo-factory did, as its own app, ``bot``: a Projects
    change idea is the owner's), or ``revise.auto_go: patch`` for a p1/p2 bug or security
    report by someone with write access. Re-triage replaces the kind and priority."""
    have = set(item.get("labels") or [])
    add = (
        [res["kind"]]
        if item.get("is_pr") and res["kind"] == "dependency"
        else [res["kind"], res["prio"]]
    )
    remove = [x for x in have if x in L.KINDS + L.PRIORITIES and x not in add]
    remove += [x for x in ("triage",) if x in have]
    if res.get("needs_info"):
        add.append("needs-info")
    elif "needs-info" in have:
        remove.append("needs-info")
    if res.get("duplicate_of"):
        add.append("duplicate")
    why_go = None
    rv = policy.get("revise") or {}
    if (
        not item.get("is_pr")
        and policy.get("status") == "maintained"
        and not res.get("duplicate_of")
        and rv.get("trigger") != "off"
    ):
        ours = {x for x in (owner, bot) if x}
        if rv.get("trigger") == "owner" and ours and item.get("author") in ours:
            why_go = "you opened it and this repository's policy fixes your own issues"
        elif (
            rv.get("auto_go") == "patch"
            and res["kind"] in ("bug", "security")
            and res["prio"] in ("p1", "p2")
            and writers is not None
            and item.get("author") in writers
        ):
            why_go = "a collaborator with write access reported it and the policy allows it"
        if why_go and res["kind"] not in (rv.get("kinds") or []):
            why_go = None
    if why_go:
        add.append("go")
    return {"add": add, "remove": [x for x in dict.fromkeys(remove) if x not in add], "go": why_go}


def comment(res: dict, decision: dict, policy: dict) -> str:
    """The one comment, templated by the host; only the summary, the question and the answer
    are the model's words, each sanitised."""
    lab = [x for x in decision["add"] if x in L.KINDS + L.PRIORITIES]
    lines = [
        MARKER,
        f"**Triage:** {sanitize(res['summary'], 400) or 'read.'}",
        "",
        f"Labelled {' and '.join(f'`{x}`' for x in lab)}: repo-factory reads this as "
        f"{KIND_WORDS.get(res['kind'], res['kind'])} at priority {res['prio'][1:]}. "
        "Change either label if that is wrong.",
    ]
    if res.get("duplicate_of"):
        lines += [
            "",
            f"This looks like #{int(res['duplicate_of'])} (labelled `duplicate`)."
            + (
                ""
                if (policy.get("triage") or {}).get("close_duplicates")
                else " Remove the label if it is not the same."
            ),
        ]
    if res.get("needs_info"):
        lines += ["", f"**A question for the reporter:** {sanitize(res['needs_info'], 300)}"]
    if res.get("answer") and (policy.get("triage") or {}).get("answer_questions", True):
        room = max(200, MAX_CHARS - len(res["summary"]) - len(res.get("needs_info") or ""))
        lines += ["", f"**From the README:** {sanitize(res['answer'], room)}"]
    if decision.get("go"):
        lines += ["", f"Labelled `go`: {decision['go']}."]
    lines += ["", f"_{SIGNATURE}_"]
    return "\n".join(lines) + "\n"


def apply(
    repo,
    item: dict,
    res: dict,
    policy: dict,
    *,
    writers: set[str] | None = None,
    comments: list[dict] | None = None,
    owner: str | None = None,
    bot: str | None = None,
) -> dict:
    """Labels and the comment on GitHub through ``repo`` (a ``github.Repo``); a duplicate is
    closed as not planned only with ``triage.close_duplicates``. ``owner``: the login repo-factory
    acts as (``revise.trigger: owner`` fixes that login's own issues, MX-PLAN §3.1), never the
    repository's owner, which for an organisation's repo is the organisation; ``bot``: repo-factory's
    own app, whose issues count as the owner's."""
    d = decide(item, res, policy, writers=writers, owner=owner, bot=bot)
    changed = repo.set_labels(item["number"], d["add"], d["remove"], have=item["labels"])
    posted = repo.comment_with_marker(
        item["number"], MARKER, comment(res, d, policy), comments=comments
    )
    closed = False
    if (
        res.get("duplicate_of")
        and (policy.get("triage") or {}).get("close_duplicates")
        and not item.get("is_pr")
    ):
        repo.close(item["number"], "not_planned")
        closed = True
    return {
        "number": item["number"],
        "kind": res["kind"],
        "prio": res["prio"],
        "labels": changed,
        "comment": posted["action"],
        "go": bool(d["go"]),
        "duplicate_of": res.get("duplicate_of"),
        "closed": closed,
    }
