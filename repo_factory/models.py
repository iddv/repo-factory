"""The models repo-factory can use, and every role that calls one (Settings › Building › Models).

``CATALOG`` is the one list of models the app offers: the roles run through the Claude Code
CLI and take ``id``; ``router`` is the model's name on an OpenAI-compatible router. A new model
is one line here. No prices: a price is a fact about one provider on one day, and the UI shows
what a thing used in tokens; Settings › Spend has the dollars.

``ROLES`` are repo_factory's model calls, with where each one's setting lives in the loop's
``loop.yaml`` (any role can be left at its default: ``null`` is the CLI's own default model).
The build stages are the v5 policy's (``policies/v5/rules.yaml``); the loop passes the ones
set under ``build.models`` to each build as ``$REPO_FACTORY_V5_MODELS``, which the pipeline lays
over the policy (``v5.pipeline.with_models``).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import yaml

from .settings.schema import MODEL_NAME, check_model  # noqa: F401  (read here too)

# id (Claude Code, the Anthropic API), router name, label, what it is for
CATALOG = [
    (
        "claude-haiku-5-5",
        "anthropic/claude-haiku-5.5",
        "Haiku 5.5",
        "fastest and lightest: high-volume judging, short checks and summaries",
    ),
    (
        "claude-sonnet-5-5",
        "anthropic/claude-sonnet-5.5",
        "Sonnet 5.5",
        "balanced: writing and reasoning",
    ),
    (
        "claude-opus-5-5",
        "anthropic/claude-opus-5.5",
        "Opus 5.5",
        "strongest for code and long sessions; slower",
    ),
    (
        "claude-fable-5-1",
        "anthropic/claude-fable-5.1",
        "Fable 5.1",
        "the largest and slowest, for the hardest reasoning",
    ),
    ("claude-sonnet-5", "anthropic/claude-sonnet-5", "Sonnet 5", "the previous Sonnet"),
    ("claude-haiku-4-5-20251001", "anthropic/claude-haiku-4.5", "Haiku 4.5", "the previous Haiku"),
]

V5_MODELS_ENV = "REPO_FACTORY_V5_MODELS"

# key, label, what it does, its setting in loop.yaml, "loop" (read each tick) or "build" (a
# v5 stage: read when a build starts)
ROLES = [
    (
        "pitch",
        "Pitch",
        "Grades an idea and writes its pitch; decides whether it is buildable.",
        ("pitch", "model"),
        "loop",
    ),
    (
        "summary",
        "Link summaries",
        "Summarises the pages an idea links to before the pitch.",
        ("pitch", "summary_model"),
        "loop",
    ),
    (
        "honesty",
        "Public text",
        "Writes a built product's description from its code, help "
        "and tests before publishing, and rewords overclaims once; never holds a publish.",
        ("publish", "honesty_model"),
        "loop",
    ),
    (
        "triage",
        "Triage",
        "Labels new issues on the published repos and writes one comment; no tools.",
        ("maintain", "triage_model"),
        "loop",
    ),
    # the models that were set on other pages, beside the rest
    (
        "charter",
        "Charter",
        "Drafts a project's charter from its idea, its claims and the repo's own documents.",
        ("projects", "charter", "model"),
        "loop",
    ),
    (
        "evolve",
        "Checks for ideas",
        "A project's check for what could improve it, given its "
        "charter, its repo, its issues and its topic.",
        ("projects", "evolve", "model"),
        "loop",
    ),
    (
        "build",
        "Builder",
        "The build session itself, and the v1 brief (SCOPE.md) for a short idea.",
        ("build", "models", "build"),
        "build",
    ),
    (
        "claims",
        "Claims",
        "Lists what the spec promises and where it is ambiguous.",
        ("build", "models", "claims"),
        "build",
    ),
    (
        "verify",
        "Verifier",
        "A fresh session that writes and runs scenarios against the build; also re-verify.",
        ("build", "models", "verify"),
        "build",
    ),
    (
        "oracle",
        "Oracle panel",
        "Votes on whether each failing scenario is a real defect.",
        ("build", "models", "oracle"),
        "build",
    ),
    ("repair", "Repair", "Fixes what the verifier found.", ("build", "models", "repair"), "build"),
    (
        "readiness",
        "Readiness",
        "The security review, the operational checks and their repair.",
        ("build", "models", "readiness"),
        "build",
    ),
]


def catalog() -> list[dict]:
    return [{"id": i, "router": r, "name": n, "note": note} for i, r, n, note in CATALOG]


def _get(data: dict, path: tuple):
    node = data
    for k in path:
        node = node.get(k) if isinstance(node, dict) else None
    return node


def policy_models(policy: str | Path) -> dict:
    """Each build role's model in the policy (``rules.yaml``), before any override."""
    try:
        rules = yaml.safe_load((Path(policy).expanduser() / "rules.yaml").read_text()) or {}
    except (OSError, yaml.YAMLError):
        rules = {}
    from .v5.pipeline import MODEL_SECTIONS

    return {role: _get(rules, paths[0] + ("model",)) for role, paths in MODEL_SECTIONS.items()}


def roles(cfg) -> list[dict]:
    """Every role with its model now, its default and where it is set. ``cfg``: the loop's
    ``Config``."""
    from .settings.store import DEFAULTS

    policy = policy_models(cfg["build"]["policy"])
    overrides = cfg["build"].get("models") or {}
    out = []
    for key, name, does, path, kind in ROLES:
        if kind == "build":
            default = policy.get(key)
            model = overrides[key] if key in overrides else default
        else:
            default = _get(DEFAULTS, path)
            model = _get(cfg.data, path)
        out.append(
            {
                "role": key,
                "name": name,
                "does": does,
                "kind": kind,
                "setting": ".".join(path),
                "model": model,
                "default": default,
                "set": (key in overrides) if kind == "build" else model != default,
            }
        )
    return out


def build_env(cfg) -> dict | None:
    """The environment a build runs with: this process's, plus the stage models chosen under
    ``build.models`` and the stage caps under ``build.stage_caps`` (``$REPO_FACTORY_V5_STAGE_CAPS``,
    v5.common.stage_caps); None (inherit) when neither is set."""
    from .v5.common import STAGE_CAPS_ENV
    from .v5.exam import DEFAULTS as EXAM
    from .v5.exam import SETTINGS_ENV as EXAM_ENV

    models = cfg["build"].get("models") or {}
    caps = cfg["build"].get("stage_caps") or {}
    ex = {
        k: v
        for k, v in ((cfg.get("exam") if hasattr(cfg, "get") else None) or {}).items()
        if k in EXAM and v != EXAM[k]
    }  # the exam's settings the owner changed
    rounds = cfg["build"].get("fix_rounds")
    if not models and not caps and not ex and rounds is None:
        return None
    env = dict(os.environ)
    if rounds is not None:  # the simple flow's fix.rounds (v5.simple)
        env["REPO_FACTORY_FIX_ROUNDS"] = str(int(rounds))
    if models:
        env[V5_MODELS_ENV] = json.dumps(models, sort_keys=True)
    if caps:
        env[STAGE_CAPS_ENV] = json.dumps(caps, sort_keys=True)
    if ex:
        env[EXAM_ENV] = json.dumps(ex, sort_keys=True)
    return env


def role_path(key: str) -> tuple:
    for k, _, _, path, _ in ROLES:
        if k == key:
            return path
    raise KeyError(key)
