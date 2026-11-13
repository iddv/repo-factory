"""The product's own GitHub workflows, looked at before the push.

Publishing starts the product's CI on the owner's account. A workflow the build wrote can ask
for more than a small public project needs: a self-hosted runner (model-written code on the
owner's own machine, under an organisation with shared runners), ``pull_request_target`` (a
stranger's pull request run with the repository's secrets), or a token that may write to the
repository. Any of those holds the publish, in words; nothing here runs a workflow."""

from __future__ import annotations

from pathlib import Path

import yaml

WRITE = ("write", "write-all")


def _on(doc: dict):
    # YAML 1.1 reads a bare `on:` as True
    return doc.get("on", doc.get(True))


def _events(on) -> set[str]:
    if isinstance(on, str):
        return {on}
    if isinstance(on, list):
        return {str(x) for x in on}
    if isinstance(on, dict):
        return {str(k) for k in on}
    return set()


def _writes(perms) -> list[str]:
    if isinstance(perms, str):
        return [perms] if perms in WRITE else []
    if isinstance(perms, dict):
        return [f"{k}: {v}" for k, v in perms.items() if str(v) in WRITE]
    return []


def _labels(runs_on) -> list[str]:
    if isinstance(runs_on, str):
        return [runs_on]
    if isinstance(runs_on, list):
        return [str(x) for x in runs_on]
    if isinstance(runs_on, dict):
        return [str(x) for x in (runs_on.get("labels") or [])] + (
            [str(runs_on["group"])] if runs_on.get("group") else []
        )
    return []


def scan(tree: Path, skip=()) -> list[str]:
    """What in ``tree``'s ``.github/workflows`` holds the publish, one line each (``file:
    why``); [] when nothing does. ``skip``: files repo-factory staged itself (its maintenance
    templates), relative to ``tree``."""
    out: list[str] = []
    d = Path(tree) / ".github" / "workflows"
    if not d.is_dir():
        return out
    for f in sorted([*d.glob("*.yml"), *d.glob("*.yaml")]):
        if f.is_symlink() or not f.is_file():
            continue
        rel = f.relative_to(tree).as_posix()
        if rel in set(skip):
            continue
        try:
            doc = yaml.safe_load(f.read_text(errors="replace"))
        except yaml.YAMLError:
            out.append(f"{rel}: not valid YAML, so it could not be checked")
            continue
        if not isinstance(doc, dict):
            continue
        if "pull_request_target" in _events(_on(doc)):
            out.append(f"{rel}: runs on pull_request_target (a stranger's change with secrets)")
        for w in _writes(doc.get("permissions")):
            out.append(f"{rel}: asks for a token that may write ({w})")
        for name, job in (doc.get("jobs") or {}).items():
            if not isinstance(job, dict):
                continue
            if any("self-hosted" in x.lower() for x in _labels(job.get("runs-on"))):
                out.append(f"{rel}: job {name} runs on a self-hosted runner")
            for w in _writes(job.get("permissions")):
                out.append(f"{rel}: job {name} asks for a token that may write ({w})")
    return out
