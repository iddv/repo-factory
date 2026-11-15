"""No fix runs on Claude Code's own sign-in file unless you allowed it.

A fix's task is someone else's words (an issue's text), given to a session with tools. When
builds run on Claude Code's own sign-in (``claude auth login``), the sign-in file is copied into
each session's config folder, where that session can read it; with the GitHub hosts on the
fix sessions' allow-list it could also send it away. So while that is how sessions would sign
in, maintenance starts no fix and the page, the switch's confirm and ``repo-factory doctor``
say: run ``claude setup-token`` first (Settings › Claude keeps the token, which a session gets
through a pipe, never as a file).

``maintain.allow_signin_file: true`` (Settings › Maintenance, off by default) lifts this for a
person who accepts it.
"""

from __future__ import annotations

WORDS = (
    "fixes wait: Claude Code's own sign-in file would be copied into each fix session, where "
    "the text of an issue steers a session with tools. Run `claude setup-token` first and keep "
    "the token in Settings › Claude"
)


def allowed(cfg) -> bool:
    """``maintain.allow_signin_file`` is exactly true (a stored null, text or a missing key:
    false)."""
    try:
        return (cfg.get("maintain") or {}).get("allow_signin_file") is True
    except AttributeError:
        return False


def blocks(cfg) -> str | None:
    """Why no fix may start now (the words above), or None."""
    if allowed(cfg):
        return None
    from ..harness import handoff

    try:
        on = handoff.build_login()["on"]
    except Exception:  # noqa: BLE001 - what sessions sign in with cannot be told: wait
        return WORDS
    return WORDS if on == "signin" else None
