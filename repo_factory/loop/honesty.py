"""The honesty check as a measure (EX-PLAN §4.6): the product's README and AGENTS.md judged by
a model, its replacements applied, then checked again. Only the corpus's replay with a model
uses it now; at publish the honesty prompt is one review pass whose replacements are applied
and listed, never a hold (``describe.review``; the owner, 2026-10-16)."""

from __future__ import annotations

from pathlib import Path
from string import Template

from .tree import readme_path


def honesty(
    root: Path,
    model,
    rounds: int = 2,
    release: str | None = None,
    claims: str | None = None,
    factory_files: list[str] | tuple = (),
) -> dict:
    """Check the product's own text, README and AGENTS.md (what an agent is told the product
    is), and repo-factory's own ``release`` text (the repo description and the release notes:
    ``RELEASE``), judged against the idea's ``claims`` (the interpretations in force,
    NG-PLAN.md); apply the model's replacements to README and AGENTS.md; check again. NOTICE
    is repo-factory's fixed text and is not read (EX-PLAN §4.6). Every issue left says who wrote the
    text it quotes (``author``: ``repo-factory`` for RELEASE and the files in ``factory_files``, else
    ``product``); repo-factory's text is never rewritten here. ``{"ok",
    "issues" (left), "fixed" (applied), "usd"}``."""
    from ..custom_prompts import effective
    from . import claims as cl

    usd, fixed, issues = 0.0, [], []
    readme = readme_path(root)
    eff = effective("honesty")  # the owner's additions; never an override
    for attempt in range(rounds):
        parts = []
        for p, cap in ((readme, 16000), (root / "AGENTS.md", 12000)):
            if p.exists():
                parts.append(f"--- {p.name}\n{p.read_text(errors='replace')[:cap]}")
        if release:
            parts.append(f"--- {cl.RELEASE_FILE}\n{release}")
        if claims:
            parts.append(f"--- CLAIMS\n{claims}")
        text, cost = model(Template(eff.text).safe_substitute(TEXT="\n\n".join(parts)))
        usd += cost or 0.0
        from .pitch import parse

        res = parse(text)
        if res is None:
            issues = [{"problem": "the honesty check's reply was unusable"}]
            continue
        # NOTICE is repo-factory's fixed text, never shown to the model: an issue naming it is none
        raised = [i for i in res.get("issues") or [] if isinstance(i, dict) and i.get("quote")]
        issues = [
            {**i, "author": cl.author(i.get("file"), factory_files)}
            for i in raised
            if not str(i.get("file", "")).upper().startswith(("NOTICE", "CLAIMS"))
        ]
        if not issues and (res.get("ok") is True or raised):
            return {
                "ok": True,
                "issues": [],
                "fixed": fixed,
                "usd": round(usd, 4),
                "prompt": {"id": "honesty", "version": eff.version, "n": eff.n},
            }
        if attempt == rounds - 1:
            break
        rel = [i for i in issues if str(i.get("file", "")).upper().startswith(cl.RELEASE_FILE)]
        if len(rel) == len(issues):
            break  # nothing here to fix: a second look says the same
        for i in issues:
            if i in rel:  # repo-factory's release text: reworded in repo-factory, not here
                continue
            f = str(i.get("file", "")).upper()
            target = root / "AGENTS.md" if f.startswith("AGENTS") else readme
            body = target.read_text(errors="replace") if target.exists() else ""
            if i["quote"] in body:
                target.write_text(body.replace(i["quote"], i.get("replacement") or ""))
                fixed.append(i)
    return {
        "ok": False,
        "issues": issues,
        "fixed": fixed,
        "usd": round(usd, 4),
        "prompt": {"id": "honesty", "version": eff.version, "n": eff.n},
    }
