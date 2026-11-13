"""The per-repo policy file, ``.github/repo-factory.yml`` (MAINTAIN.md §4).

``DEFAULTS`` are the documented values a new file starts from; ``MISSING`` is what a repo
without the file gets: repo-factory answers and labels, and spends nothing (``status: as-is``,
``revise.trigger: off``, every merge ``never``, no releases). ``parse`` rejects unknown keys
and bad values so a typo cannot silently turn something on.

YAML 1.1 reads a bare ``off`` as ``false``; for the settings whose values include ``off`` the
parser reads ``false`` back as ``off``."""

from __future__ import annotations

import copy
import json

import yaml

from . import labels as _labels

DEFAULTS: dict = {
    "repo-factory": 1,
    "status": "maintained",
    "triage": {"enabled": True, "close_duplicates": False, "answer_questions": True},
    "revise": {
        "trigger": "go",
        "auto_go": "off",
        "kinds": ["bug", "security", "docs", "dependency"],
        "max_attempts": 2,
        "lease_hours": 6,
        "max_usd": None,
        "models": {},
    },
    "merge": {
        "factory_patch": "never",
        "kinds": ["bug", "docs"],
        "max_lines": 80,
        "dependabot": "never",
    },
    "release": {"on_merge": "patch", "prefix": "v"},
    "spend": {"note": ""},
}

MISSING: dict = copy.deepcopy(DEFAULTS)
MISSING["status"] = "as-is"
MISSING["revise"]["trigger"] = "off"
MISSING["release"]["on_merge"] = "off"

ENUMS = {
    ("status",): ("maintained", "as-is", "archived"),
    ("revise", "trigger"): ("go", "owner", "off"),
    ("revise", "auto_go"): ("off", "patch"),
    ("merge", "factory_patch"): ("never", "ci-green"),
    ("merge", "dependabot"): ("never", "patch", "minor"),
    ("release", "on_merge"): ("patch", "minor", "off"),
}
BOOLS = {("triage", "enabled"), ("triage", "close_duplicates"), ("triage", "answer_questions")}
POSITIVE_INTS = {("repo-factory",), ("revise", "max_attempts"), ("merge", "max_lines")}
# what merge.factory_patch: ci-green may merge (merge.kinds); a feature never, whatever it says
MERGE_KINDS = ("bug", "docs", "security", "dependency")


class PolicyError(ValueError):
    pass


def _walk(doc: dict, ref: dict, path: tuple, errors: list) -> None:
    for k, v in doc.items():
        p = (*path, str(k))
        name = ".".join(p)
        if k not in ref:
            errors.append(f"unknown key {name}")
            continue
        r = ref[k]
        if isinstance(r, dict) and p != ("revise", "models"):
            if not isinstance(v, dict):
                errors.append(f"{name} must be a mapping")
            else:
                _walk(v, r, p, errors)
            continue
        if p in ENUMS:
            if v is False and "off" in ENUMS[p]:
                doc[k] = v = "off"
            if v not in ENUMS[p]:
                errors.append(f"{name} must be one of {', '.join(ENUMS[p])} (got {v!r})")
        elif p in BOOLS:
            if not isinstance(v, bool):
                errors.append(f"{name} must be true or false (got {v!r})")
        elif p in POSITIVE_INTS:
            if isinstance(v, bool) or not isinstance(v, int) or v < 1:
                errors.append(f"{name} must be a whole number of at least 1 (got {v!r})")
        elif p == ("revise", "lease_hours"):
            if isinstance(v, bool) or not isinstance(v, (int, float)) or v <= 0:
                errors.append(f"{name} must be a positive number of hours (got {v!r})")
        elif p == ("revise", "max_usd"):
            if v is not None and (isinstance(v, bool) or not isinstance(v, (int, float)) or v <= 0):
                errors.append(f"{name} must be empty (no cap) or a positive amount (got {v!r})")
        elif p == ("revise", "kinds"):
            if not isinstance(v, list) or any(x not in _labels.KINDS for x in v):
                errors.append(f"{name} must be a list of {', '.join(_labels.KINDS)} (got {v!r})")
        elif p == ("merge", "kinds"):
            if isinstance(v, list) and "feature" in v:
                errors.append(
                    f"{name}: repo-factory never merges a feature, whatever this says; take it out"
                )
            elif not isinstance(v, list) or any(x not in MERGE_KINDS for x in v):
                errors.append(f"{name} must be a list of {', '.join(MERGE_KINDS)} (got {v!r})")
        elif p == ("revise", "models"):
            if not isinstance(v, dict) or any(not isinstance(x, str) for x in v.values()):
                errors.append(f"{name} must map a stage to a model id (got {v!r})")
        elif p in (("release", "prefix"), ("spend", "note")):
            if not isinstance(v, str):
                errors.append(f"{name} must be text (got {v!r})")


def validate(doc) -> list[str]:
    """Problems with a parsed policy document, as plain sentences; ``[]`` when it is valid.
    Normalises ``false`` to ``off`` in place where that is a value."""
    if not isinstance(doc, dict):
        return ["the file must be a mapping of settings"]
    errors: list[str] = []
    _walk(doc, DEFAULTS, (), errors)
    if "repo-factory" in doc and doc["repo-factory"] != 1 and not errors:
        errors.append(
            f"repo-factory: schema version {doc['repo-factory']} is not known (expected 1)"
        )
    return errors


def merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict) and k != "models":
            out[k] = merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def parse(text: str | None) -> dict:
    """The effective policy: the file's settings over ``DEFAULTS``; ``MISSING`` when there
    is no file (``None``) or it is empty. Raises ``PolicyError`` listing every problem."""
    if text is None:
        return copy.deepcopy(MISSING)
    try:
        doc = yaml.safe_load(text)
    except yaml.YAMLError as e:
        raise PolicyError(f"not valid YAML: {e}") from None
    if doc is None:
        return copy.deepcopy(MISSING)
    errors = validate(doc)
    if errors:
        raise PolicyError("; ".join(errors))
    return merge(DEFAULTS, doc)


def load(gh, repo: str, ref: str | None = None) -> dict:
    """Read ``.github/repo-factory.yml`` from the repo's default branch (or ``ref``) through the
    contents API. A 404 is the missing-file policy; any other failure raises."""
    path = f"repos/{repo}/contents/.github/repo-factory.yml" + (f"?ref={ref}" if ref else "")
    code, out, err = gh._run(["api", "-H", "Accept: application/vnd.github.raw+json", path], None)
    if code != 0:
        if "404" in (err or "") or "Not Found" in (err or "") or "Not Found" in (out or ""):
            return parse(None)
        raise PolicyError(
            f"could not read .github/repo-factory.yml from {repo}: {(err or out).strip()[:300]}"
        )
    return parse(out)


def effective(loop, target, gh=None) -> dict:
    """One reader for the poll, the fix and the page: a product's
    ``.github/repo-factory.yml`` (``load``). ``target``: a ``poll.targets`` entry or a repo
    name."""
    full = target if isinstance(target, str) else target["repo"]
    if gh is None:
        from .poll import gh_for

        gh = gh_for(loop)
    return load(gh, full)


def render(values: dict | None = None) -> str:
    """The repo's ``.github/repo-factory.yml`` from the commented template, with ``values`` (a
    partial policy) over ``DEFAULTS``. The result always parses back to the same policy."""
    from . import render as _render

    p = merge(DEFAULTS, values or {})
    errors = validate(copy.deepcopy(p))
    if errors:
        raise PolicyError("; ".join(errors))

    def y(v):
        if v is None:
            return "null"
        if isinstance(v, bool):
            return "true" if v else "false"
        if isinstance(v, (list, dict)):
            return json.dumps(v)
        if isinstance(v, str) and v in ("off", "on", "yes", "no", ""):
            return v if v == "off" else json.dumps(v)
        return str(v)

    flat = {}
    for sect, body in p.items():
        if isinstance(body, dict):
            for k, v in body.items():
                flat[f"{sect}_{k}"] = y(v)
        else:
            flat[sect] = y(body)
    flat["spend_note"] = json.dumps(p["spend"]["note"])
    # a placeholder is a word: the schema key "repo-factory" fills {{repo_factory}}
    return _render("repo-factory.yml", {k.replace("-", "_"): v for k, v in flat.items()})
