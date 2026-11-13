"""Settings: models, spend and caps, the Claude login."""

from __future__ import annotations

from .base import ok


def _claude_login(app, r):
    """Settings › Claude: what builds log in with (``handoff.build_login``) and whether
    repo-factory keeps a login token (``claude_login``). Never a value. Writes go through
    api/act (claude_login_keep, claude_login_forget) and the one settings writer
    (build.allow_api_key)."""
    from ... import claude_login
    from ...harness import handoff

    out = handoff.build_login(cfg=app.loop_cfg())
    st = claude_login.Store(root=claude_login.store_dir(home=app.loop_home)).status()
    return {
        **out,
        "kept_at": st["kept_at"],
        "loose": st["loose"],
        "command": "claude setup-token",
        "hold": handoff.HOLD,
    }


ROUTES = [
    ("GET", "/api/claude/login", ok(_claude_login)),
    ("GET", "/api/models", ok(lambda a, r: a.models())),
    ("GET", "/api/spend/breakdown", ok(lambda a, r: a.spend_breakdown())),
    ("GET", "/api/spend/caps", ok(lambda a, r: a.caps())),
    (
        "POST",
        "/api/models",
        ok(lambda a, r: a.set_model(str(r.data.get("role") or ""), r.data.get("model"))),
    ),
    ("POST", "/api/spend", ok(lambda a, r: a.set_caps(r.data))),
]
