"""Operator settings for binary business rules (docs/design/build-v5.md, Addendum A).

When the spec leaves a *binary* business rule open (two reasonable policies an operator
could want either way, needing nothing beyond a guard), the delivered software exposes it as
a setting: a named key, its values, a documented default and a scope (``global``, per
``site``, or per ``pair`` of sites). Nobody deliberates over it, asks the owner, or forks.
Anything else is ``complex``: the implementation decides and ``ASSUMPTIONS.md`` records it.

A setting is ``{"key", "values": [a, b], "default", "scope", "means": {value: text}}``.
From the claims, ``values`` is ``[literal reading's value, expected reading's value]`` and
``default`` the recommended one (Addendum A: the expected reading).

How the factory checks a setting: every setting the repair adds can be forced by an
environment variable (``env_name(key)``), read when the setting is used, applying the value
everywhere (all sites and pairs). A verifier scenario that checks one value of a setting is
*pinned*: it gets a ``# setting: key=value`` header and an autouse fixture that sets the
variable (``pin_code``), so the harness's ordinary runs test that value, while every other
scenario runs under the default."""

from __future__ import annotations

import re

KINDS = ("binary", "complex")
SCOPES = ("global", "site", "pair")
HEADER = re.compile(r"^#\s*setting:\s*([\w.\-]+)\s*=\s*(\S+)\s*$", re.M)
PIN_BEGIN = "# --- repo-factory setting pin (begin) ---"
PIN_END = "# --- repo-factory setting pin (end) ---"


def norm_key(key) -> str:
    """``hold.window_after_idle``: lower case, dots between words, ``_`` inside them."""
    k = re.sub(r"[^a-z0-9_.]+", "_", str(key or "").strip().lower())
    return re.sub(r"\.+", ".", re.sub(r"_+", "_", k)).strip("._")


def norm_value(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    return re.sub(r"\s+", "_", str(v if v is not None else "").strip())


def scope_of(s) -> str:
    s = str(s or "").strip().lower()
    if s in SCOPES:
        return s
    if "pair" in s or "between" in s or "↔" in s:
        return "pair"
    if any(w in s for w in ("site", "branch", "location", "store", "per ")):
        return "site"
    return "global"


def clean(obj, default_hint: str | None = None) -> dict | None:
    """A proposed setting, normalised; None if it isn't a usable two-value switch (then
    the item is ``complex``). ``default_hint``: the value to default to when ``obj`` names
    none (or one that isn't among its values)."""
    if not isinstance(obj, dict):
        return None
    key = norm_key(obj.get("key"))
    vals = [norm_value(v) for v in obj.get("values") or [] if norm_value(v)]
    vals = list(dict.fromkeys(vals))
    if not key or len(vals) != 2:
        return None
    d = norm_value(obj.get("default"))
    if d not in vals:
        d = default_hint if default_hint in vals else vals[1]
    means = obj.get("means") if isinstance(obj.get("means"), dict) else {}
    means = {norm_value(k): str(v) for k, v in means.items() if norm_value(k) in vals}
    out = {"key": key, "values": vals, "default": d, "scope": scope_of(obj.get("scope"))}
    if means:
        out["means"] = means
    return out


def env_name(key: str) -> str:
    """The environment variable that forces ``key`` everywhere: ``HOLD_WINDOW_AFTER_IDLE``."""
    return re.sub(r"[^A-Z0-9]+", "_", norm_key(key).upper()).strip("_")


def pinned(code: str) -> dict[str, str]:
    """The ``# setting: key=value`` headers of a scenario."""
    return {norm_key(k): v for k, v in HEADER.findall(code or "")}


def unpin(code: str) -> str:
    """``code`` without a pin block ``pin_code`` added."""
    if PIN_BEGIN not in code:
        return code
    head, _, rest = code.partition(PIN_BEGIN)
    _, _, tail = rest.partition(PIN_END)
    return head + tail.lstrip("\n")


def pin_code(code: str, pins: dict[str, str]) -> str:
    """``code`` pinned to ``pins`` (key -> value): one ``# setting:`` header per key and an
    autouse fixture that sets each key's environment variable for every test in the file.
    Idempotent; replaces an earlier pin."""
    code = unpin(code)
    if not pins:
        return code
    L = [PIN_BEGIN]
    L += [f"# setting: {norm_key(k)}={v}" for k, v in sorted(pins.items())]
    L += [
        "import pytest as _factory_pytest",
        "",
        "",
        "@_factory_pytest.fixture(autouse=True)",
        "def _factory_setting_pin(monkeypatch):",
    ]
    L += [f"    monkeypatch.setenv({env_name(k)!r}, {str(v)!r})" for k, v in sorted(pins.items())]
    L += [PIN_END, ""]
    return "\n".join(L) + "\n" + code


def other(setting: dict, value: str) -> str:
    a, b = setting["values"]
    return b if value == a else a


def reading_value(setting: dict, reading: str | None) -> str | None:
    """The value a reading label stands for: ``values`` is ``[literal, expected]``."""
    return {"literal": setting["values"][0], "expected": setting["values"][1]}.get(reading or "")


def item_md(it: dict) -> str:
    """One setting for the repair brief."""
    s = it["setting"]
    means = s.get("means") or {}
    vals = ", ".join(f"`{v}`" + (f" ({means[v]})" if means.get(v) else "") for v in s["values"])
    scope = {
        "global": "one value for the whole system",
        "site": "a value per site (a global value applies where none is set)",
        "pair": "a value per pair of sites (a global value applies where none is set)",
    }
    L = [
        f"### {it['id']}: setting `{s['key']}`",
        f"Settles: {it.get('question') or '-'}",
        f"Values: {vals}. **Default: `{s['default']}`.** Scope: {scope[s['scope']]}.",
        f"Environment override: `{env_name(s['key'])}=<value>` (applies everywhere).",
        "The build currently behaves as: "
        + (
            f"`{it['current']}`"
            if it.get("current") in s["values"]
            else "neither value, or unknown"
        )
        + (" (keep that behaviour as the default)" if it.get("current") == s["default"] else ""),
    ]
    pins = it.get("pins") or {}
    if pins:
        L.append(
            "Scenarios: "
            + "; ".join(
                f"`/work/exam/_verify/scenarios/{f}` runs with`{env_name(s['key'])}={v}`"
                for f, v in sorted(pins.items())
            )
            + ". They must pass."
        )
    if it.get("fix_value"):
        L.append(
            f"The build already has this setting, but `{it['fix_value']}` doesn't behave "
            "as described: make it."
        )
    return "\n".join(L)
