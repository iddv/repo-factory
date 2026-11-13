"""Maintaining published products through GitHub (MAINTAIN.md): labels as the protocol,
``.github/repo-factory.yml`` as the per-repo policy, ``AGENTS.md`` as the instructions any agent reads,
and CI that keeps that file honest. GitHub is the record; nothing here keeps state.

This package's static half (track M1): the label scheme (``labels``), the policy file
(``policy``), the AGENTS.md renderer and drift doctor (``agentsmd``), the templates the
products carry (``templates/``) and the backfill that gives an existing product all of them in
one pull request (``backfill``). ``repo-factory maintain labels|policy|doctor|backfill`` (``cli``).

Its polling half (track M2): the GitHub client and the poll's disposable cache (``github``),
triage with a no-tools model (``triage``) and the step of the loop's tick that runs it and
handles Dependabot (``poll``); ``repo-factory maintain tick|status``.

Its revise half (track M3): the fix of a ``go`` issue (``revise``) and the release after a merge
(``release``). Track M4: who repo-factory is on GitHub (``identity``: the owner's login, or a
fine-grained token of the owner's or a bot account's, ``repo-factory maintain whoami`` and the line in
``repo-factory doctor``), and the weekly drift doctor on GitHub (``doctor``); publishing writes these
files into every new build's first commit (``loop.publish.maintenance_files``)."""

from __future__ import annotations

import re
from pathlib import Path

TEMPLATES = Path(__file__).with_name("templates")

# ``{{name}}`` is ours; ``${{ expr }}`` is a GitHub Actions expression and is left alone.
PLACEHOLDER = re.compile(r"(?<!\$)\{\{([a-z_][a-z0-9_]*)\}\}")
LEFTOVER = re.compile(r"(?<!\$)\{\{")


class TemplateError(ValueError):
    pass


def template(name: str) -> str:
    return (TEMPLATES / name).read_text()


def fill(text: str, values: dict) -> str:
    """Replace every ``{{name}}`` with ``values[name]``; a missing value is an error, so a
    rendered file can never carry a placeholder."""
    missing = sorted({m for m in PLACEHOLDER.findall(text) if m not in values})
    if missing:
        raise TemplateError("no value for " + ", ".join(missing))
    out = PLACEHOLDER.sub(lambda m: str(values[m.group(1)]), text)
    if LEFTOVER.search(out):
        raise TemplateError("a placeholder is left after rendering")
    return out


def render(name: str, values: dict) -> str:
    return fill(template(name), values)
