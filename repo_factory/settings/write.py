"""Writing the settings store, ``loop.yaml`` (written by the settings pages, not by hand).

A value is set, or unset (back to the default in ``config.DEFAULTS``), at a dotted path such
as ``build.models.verify``. The file is edited as text, so the owner's comments and the
order of keys survive; the result is parsed and checked before it replaces the file in one
step. A file the text edit cannot handle (a flow-style mapping on the path) is rewritten
from its parsed form instead, keeping its leading comment.

Only the keys in ``RULES``, each checked by ``check`` (and each role's model,
``repo_factory.models.ROLES``, and the caps of Settings › Spend) are written through the API.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

import yaml

UNSET = object()
# Spend caps: USD or null (no cap). Settings › Spend.
CAPS = {"daily_usd": ("daily_usd",), "build_max_usd": ("build", "max_usd")}


def _scalar(value) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return repr(value)
    if isinstance(value, list):
        return json.dumps(value)  # a flow sequence: JSON is YAML
    return json.dumps(str(value))


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _quiet(line: str) -> bool:
    """Blank or a comment: belongs to no key."""
    return not line.strip() or line.lstrip().startswith("#")


def _span(lines: list[str], lo: int, hi: int, ind: int, key: str) -> tuple[int, int] | None:
    pat = re.compile(rf"^ {{{ind}}}{re.escape(key)}:(\s|$)")
    for i in range(lo, hi):
        if pat.match(lines[i]):
            end = i + 1
            while end < hi and (_quiet(lines[end]) or _indent(lines[end]) > ind):
                end += 1
            while end > i + 1 and _quiet(lines[end - 1]):
                end -= 1
            return i, end
    return None


def _split(head: str) -> tuple[str, str]:
    """A ``key: value  # note`` line's value and comment (with its leading space)."""
    rest = head.rstrip("\n").split(":", 1)[1]
    m = re.search(r"\s+#.*$", rest)
    return (rest[: m.start()], m.group(0)) if m else (rest, "")


class _Flow(Exception):
    """The path runs through a flow-style mapping: the text edit gives up."""


def edit_text(text: str, keys: tuple[str, ...], value=UNSET) -> str:
    """``text`` with ``keys`` set to ``value``, or removed when ``value`` is ``UNSET``."""
    lines = text.splitlines(keepends=True)
    if lines and not lines[-1].endswith("\n"):
        lines[-1] += "\n"
    lo, hi, ind = 0, len(lines), 0
    parent: int | None = None
    for d, key in enumerate(keys):
        last = d == len(keys) - 1
        sp = _span(lines, lo, hi, ind, key)
        if sp is None:
            if value is UNSET:
                return "".join(lines)
            at = hi
            while at > lo and _quiet(lines[at - 1]):
                at -= 1
            block = [
                " " * (ind + 2 * j)
                + k
                + ":"
                + (f" {_scalar(value)}\n" if j == len(keys) - d - 1 else "\n")
                for j, k in enumerate(keys[d:])
            ]
            lines[at:at] = block
            return "".join(lines)
        i, end = sp
        val, note = _split(lines[i])
        if last:
            if value is UNSET:
                del lines[i:end]
                if parent is not None and not _has_children(lines, parent):
                    # an empty block would parse as null: keep it a mapping
                    note = _split(lines[parent])[1]
                    lines[parent] = lines[parent].split(":", 1)[0] + ": {}" + note + "\n"
                return "".join(lines)
            lines[i:end] = [" " * ind + key + ": " + _scalar(value) + note + "\n"]
            return "".join(lines)
        v = val.strip()
        if v in ("{}", "null", "~"):
            lines[i] = " " * ind + key + ":" + note + "\n"
        elif v:
            raise _Flow(".".join(keys[: d + 1]))
        child = next((_indent(ln) for ln in lines[i + 1 : end] if not _quiet(ln)), ind + 2)
        parent, lo, hi, ind = i, i + 1, end, child
    return "".join(lines)


def _has_children(lines: list[str], at: int) -> bool:
    ind = _indent(lines[at])
    for line in lines[at + 1 :]:
        if not _quiet(line):
            return _indent(line) > ind
    return False


def _get(data, keys):
    for k in keys:
        if not isinstance(data, dict) or k not in data:
            return UNSET
        data = data[k]
    return data


def _rewrite(text: str, keys: tuple[str, ...], value) -> str:
    data = yaml.safe_load(text) or {}
    node = data
    for k in keys[:-1]:
        if not isinstance(node.get(k), dict):
            node[k] = {}
        node = node[k]
    if value is UNSET:
        node.pop(keys[-1], None)
    else:
        node[keys[-1]] = value
    head = []
    for line in text.splitlines():
        if not line.startswith("#"):
            break
        head.append(line)
    return "\n".join(head + [yaml.safe_dump(data, sort_keys=False).rstrip()]) + "\n"


def write(path: Path, keys: tuple[str, ...], value=UNSET) -> None:
    """Set (or unset) ``keys`` in the YAML file at ``path`` and replace it in one step."""
    path = Path(path)
    text = path.read_text() if path.exists() else ""
    try:
        new = edit_text(text, keys, value)
        ok = (
            _get(yaml.safe_load(new) or {}, keys) == value
            if value is not UNSET
            else _get(yaml.safe_load(new) or {}, keys) is UNSET
        )
    except (_Flow, yaml.YAMLError):
        ok = False
    if not ok:
        new = _rewrite(text, keys, value)
    parsed = yaml.safe_load(new) or {}
    if (value is UNSET and _get(parsed, keys) is not UNSET) or (
        value is not UNSET and _get(parsed, keys) != value
    ):
        raise ValueError(f"could not set {'.'.join(keys)} in {path.name}")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(new)
    try:
        if path.exists():
            os.chmod(tmp, path.stat().st_mode & 0o777)
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()


REPO_NAME = re.compile(r"[A-Za-z0-9-]{1,39}/[A-Za-z0-9._-]{1,100}")


# Settings › Publishing › Projects: each key under ``projects`` the page may write, with its
# check: ("bool",) | ("enum", choices) | ("int", lo, hi) | ("float", lo, hi) | ("usd",) |
# ("model",) | ("list", choices). A per-project override (the project page's Settings) takes the
# same keys but the first two, which are factory-wide.
PROJECTS = {
    "enabled": ("bool",),
    "create.on": ("enum", ("publish", "public")),
    "charter.model": ("model",),
    "evolve.every_days": ("int", 1, 365),
    "evolve.skip_when_quiet": ("bool",),
    "evolve.min_hours_between": ("int", 0, 720),
    "evolve.model": ("model",),
    "evolve.max_ideas": ("int", 1, 20),
    "evolve.max_maintenance": ("int", 0, 20),
    "evolve.concurrency": ("int", 0, 4),
    "evolve.dup_sim": ("float", 0.0, 1.0),
    "autonomy.preset": ("enum", ("quiet", "hands-on", "off")),
    "autonomy.patch": ("enum", ("auto", "ask", "list", None)),
    "autonomy.minor": ("enum", ("auto", "ask", "list", None)),
    # a scope change always goes to the owner: never auto
    "autonomy.scope": ("enum", ("ask", "list", None)),
    "gate.min_confidence": ("float", 0.0, 1.0),
    "gate.min_agree_runs.patch": ("int", 1, 10),
    "gate.min_agree_runs.minor": ("int", 1, 10),
    "gate.require_evidence_kinds": ("list", ("customer_ask", "feature", "maintenance")),
    "changes.file_issues_outside": ("bool",),
    "decline_days": ("int", 1, 365),
    "budget.monthly_usd": ("usd",),
    "health.stale_pr_days": ("int", 1, 365),
}
PROJECT_WIDE = ("enabled", "create.on")

# Every loop.yaml setting the app writes, by its dotted key, with its check (one writer,
# ``POST api/loop/settings``, for Settings › Building, and the check Settings › Publishing ›
# Maintenance and Projects use too). The kinds are PROJECTS' plus ("int?", lo, hi)
# and ("float?", lo, hi), where empty is null (off), and ("repos",), a list of owner/name.
# A key not here is set in loop.yaml by hand on purpose: commands and test hooks and credentials
# are never written from a request. The kinds for the rest: ("path",) a folder that exists
# and repo-factory can write; ("hosts",) host names; ("policy",) a bundled policy's name. Only the page's and the command line's tokens
# write here at all (``api.caller``), and the keys in SENSITIVE also need the request's
# ``confirm: true``, which the page sends after its dialog says what changes.
_SCORE = ("float?", 0.0, 1.0)
RULES: dict[str, tuple] = {
    "concurrency": ("int", 1, 8),
    "daily_usd": ("usd",),
    "build.max_usd": ("usd",),
    "build.auto_repair": ("int", 0, 5),
    "build.flow": ("enum", ("v5", "lean")),
    "pitch.min_buildability": ("float", 0.0, 1.0),
    **{
        f"pitch.min_{k}": _SCORE
        for k in ("insight", "differentiation", "grounding", "durability", "breadth")
    },
    "pitch.interpret_claims": ("bool",),
    "exam.reruns": ("int", 0, 10),
    "exam.intermittent_hold_min": ("int", 1, 5),
    "exam.flaky_retire": ("bool",),
    "exam.adopt": ("enum", ("fixes", "all", "off")),
    "exam.evidence_asset": ("bool",),
    "publish.approval": ("enum", ("required", "auto")),
    "publish.when": ("enum", ("ask", "ready")),
    "publish.maintenance_files": ("enum", ("policy", "all", "none")),
    "revise.enabled": ("bool",),
    "revise.max_per_build": ("int?", 1, 100),
    "build.allow_api_key": ("bool",),
    "maintain.enabled": ("bool",),
    "maintain.poll_s": ("int", 60, 86400),
    "maintain.cache_s": ("int", 0, 86400),
    "maintain.triage_max_per_poll": ("int", 1, 86400),
    "maintain.repos": ("repos",),
    "maintain.concurrency": ("int", 0, 8),
    "maintain.max_usd": ("usd",),
    "maintain.net": ("enum", ("allowlist", "public")),
    "maintain.lesson_label": ("bool",),
    "maintain.doctor_days": ("int", 0, 90),
    "maintain.identity": ("enum", ("auto", "app", "token", "login")),
    "maintain.refresh_max": ("int", 0, 5),
    "maintain.exam": ("enum", ("report", "require", "off")),
    # a token file is named in loop.yaml by hand; the page only removes the setting
    # (Remove this token setting, once GitHub refused the token): {key, clear: true}
    "maintain.token_env_file": ("remove",),
    "tasks_dir": ("path",),
    "build.policy": ("policy",),
    "exam.indexes": ("hosts",),
    "publish.anyway.may_override": ("list", ("tests", "completeness", "safe_defaults")),
    **{f"projects.{k}": r for k, r in PROJECTS.items()},
}
# what each sensitive key's change does, said in the page's confirm dialog before it is written
SENSITIVE = {
    "tasks_dir": "Builds make their task folders in this folder from the next pitch on; the "
    "folders already made stay where they are.",
    "build.policy": "Every build from the next one runs this pipeline.",
    "exam.indexes": "The install phase of the checks may reach these package indexes, through "
    "the proxy, and nothing else; empty means no network at all.",
    "publish.anyway.may_override": "Publish anyway may publish over these holds, into a "
    "private repo with the override disclosed. Nothing "
    "delivered, an open security finding, secrets and a product "
    "listening beyond loopback are never overridden.",
    # the default is the Claude plan; the dialog says what turning it on bills
    "build.allow_api_key": "Builds will bill your Claude API key: without a Claude login token, "
    "builds, fixes and briefs log in with the API key in repo-factory's "
    "environment, at API prices. Off, a build is held until you sign "
    "in to your Claude plan.",
    "maintain.token_env_file": "Maintenance stops using this token file and acts as the identity "
    "shown instead. The file itself stays where it is: delete it, and "
    "the token on GitHub, yourself.",
}
HOST = re.compile(r"(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}")
# Settings › Publishing › Maintenance's keys (under ``maintain``), in the order the page sends them
MAINTAIN = (
    "enabled",
    "poll_s",
    "cache_s",
    "triage_max_per_poll",
    "repos",
    "concurrency",
    "max_usd",
    "net",
    "lesson_label",
    "doctor_days",
    "identity",
    "refresh_max",
)
_WORDS = {True: "true", False: "false", None: "empty"}


def policies() -> list[str]:
    """The bundled pipelines' names (``resources.POLICIES``' folders), the default first."""
    from ..resources import POLICIES

    names = sorted(
        p.name for p in POLICIES.iterdir() if p.is_dir() and not p.name.startswith(("_", "."))
    )
    return sorted(names, key=lambda n: n != "v5")


def rule_view(rule: tuple) -> dict:
    """A check as the page's form reads it: ``{kind, choices?, min?, max?, empty?}``."""
    kind = rule[0].rstrip("?")
    if kind == "policy":
        return {"kind": "enum", "choices": policies(), "empty": False}
    if kind == "path":
        return {"kind": kind, "empty": False}
    if kind == "hosts":
        return {"kind": "hosts", "empty": True}
    if kind == "remove":
        return {"kind": "remove", "empty": True}
    return {
        "kind": kind,
        **({"choices": list(rule[1])} if kind in ("enum", "list") else {}),
        **({"min": rule[1], "max": rule[2]} if kind in ("int", "float") else {}),
        "empty": rule[0].endswith("?")
        or kind in ("usd", "model")
        or (kind == "enum" and None in rule[1]),
    }


def check(key: str, value):
    """A loop.yaml value from a request, checked against ``RULES``; ValueError says why, in
    words. Never a model."""
    rule = RULES.get(key)
    if rule is None:
        raise ValueError(
            f"{key or '(no key)'} is not a setting the app writes; set it in loop.yaml"
        )
    return checked(rule, key, value)


def checked(rule: tuple, key: str, value):
    kind = rule[0]
    if kind == "remove":  # a credential's file: removed here, never named
        if value is None:
            return None
        raise ValueError(
            f"{key} is only removed here (clear: true); to name a file, set it in loop.yaml"
        )
    if kind.endswith("?"):
        if value is None or value == "":
            return None
        kind = kind[:-1]
    if kind == "bool":
        if not isinstance(value, bool):
            raise ValueError(f"{key} is true or false")
        return value
    if kind == "enum":
        if value == "" and None in rule[1]:
            value = None
        if value not in rule[1] or (
            isinstance(value, bool) and not any(c is value for c in rule[1])
        ):
            raise ValueError(
                f"{key} is one of "
                + ", ".join(_WORDS.get(c, str(c)) if not isinstance(c, str) else c for c in rule[1])
            )
        return value
    if kind == "int":
        if isinstance(value, bool) or not isinstance(value, int) or not rule[1] <= value <= rule[2]:
            raise ValueError(f"{key} is a whole number from {rule[1]} to {rule[2]}")
        return value
    if kind == "float":
        if (
            isinstance(value, bool)
            or not isinstance(value, int | float)
            or not rule[1] <= value <= rule[2]
        ):
            raise ValueError(f"{key} is a number from {rule[1]:g} to {rule[2]:g}")
        return round(float(value), 3)
    if kind == "usd":
        if isinstance(value, str) and value.strip().lower() in ("none", "null"):
            return None
        if isinstance(value, str) and value:  # a form's field: a number as text
            try:
                value = float(value)
            except ValueError:
                pass
        return usd_or_none(value, key)
    if kind == "model":
        from .schema import check_model

        return check_model(value)
    if kind == "repos":
        if isinstance(value, str):
            value = value.replace(",", " ").split()
        if (
            not isinstance(value, list)
            or len(value) > 100
            or any(not isinstance(x, str) or not REPO_NAME.fullmatch(x) for x in value)
        ):
            raise ValueError(f"{key} is a list of owner/name")
        return list(dict.fromkeys(value))
    if kind == "path":
        return _folder(key, value)
    if kind == "hosts":
        if isinstance(value, str):
            value = value.replace(",", " ").split()
        if (
            not isinstance(value, list)
            or len(value) > 20
            or any(not isinstance(x, str) or not HOST.fullmatch(x.strip().lower()) for x in value)
        ):
            raise ValueError(
                f"{key} is a list of host names (pypi.org files.pythonhosted.org): "
                "no scheme, port or path; at most 20"
            )
        return list(dict.fromkeys(x.strip().lower() for x in value))
    if kind == "policy":
        from ..resources import POLICIES

        name = str(value or "").strip().rstrip("/").split("/")[-1]
        if name not in policies():
            raise ValueError(f"{key} is one of " + ", ".join(policies()))
        return str(POLICIES / name)
    if isinstance(value, str):  # a form's field: the choices as words
        value = value.replace(",", " ").split()
    if not isinstance(value, list) or any(x not in rule[1] for x in value):
        raise ValueError(f"{key} is a list of " + ", ".join(rule[1]))
    return list(dict.fromkeys(value))


def _folder(key: str, value) -> str:
    """A folder repo-factory writes in: ``~`` allowed, absolute once expanded, there, and writable."""
    text = str(value or "").strip()
    if not text or "\n" in text or "\x00" in text:
        raise ValueError(f"{key} is a folder: ~/tasks or /srv/tasks")
    p = Path(os.path.expanduser(text))
    if not p.is_absolute():
        raise ValueError(f"{key} is a whole path from / (or ~): {text} is not")
    if not p.is_dir():
        raise ValueError(f"{key}: there is no folder {p}; make it first")
    if not os.access(p, os.W_OK | os.X_OK):
        raise ValueError(f"{key}: repo-factory cannot write in {p}")
    return text


def project_value(key: str, value):
    """A Settings › Publishing › Projects value from a request, checked; ValueError says why (in words)."""
    rule = PROJECTS.get(key)
    if rule is None:
        raise ValueError(f"projects.{key} is not a setting this page writes")
    return checked(rule, key, value)


def usd_or_none(value, what: str) -> float | None:
    """A cap from a request: None (no cap) or a number of USD above 0."""
    if value is None or value == "":
        return None
    if (
        isinstance(value, bool)
        or not isinstance(value, int | float)
        or not 0 < value <= 1e6
        or value != value
    ):
        raise ValueError(f"{what} must be a number of USD above 0, or empty for no cap")
    return round(float(value), 2)
