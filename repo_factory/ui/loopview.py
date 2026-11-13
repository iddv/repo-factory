"""``/api/loop``: the loop's status, every task in a compact form, and the recent history. Read-only: actions go through ``repo-factory loop`` (server.py)."""

from __future__ import annotations

from pathlib import Path

from ..events import load_state

PITCH_KEYS = (
    "product",
    "product_type",
    "one_liner",
    "who",
    "why_now",
    "buildability",
    "buildability_reasons",
    "risks",
    "brief",
    "reject_reason",
)


def _under(path: str | None, root: Path) -> bool:
    if not path:
        return False
    try:
        Path(path).resolve().relative_to(root)
        return True
    except ValueError:
        return False


def idea_view(i: dict, root: Path) -> dict:
    p = i.get("pitch") or {}
    b = i.get("build") or {}
    g = i.get("gate") or {}
    pub = i.get("publish") or {}
    out = {
        "id": i["id"],
        "state": i["state"],
        "title": i.get("title") or i["id"],
        "source": i["source"],
        "text": (i.get("text") or "")[:600],
        "reason": i.get("reason"),
        "created": i["created"],
        "updated": i["updated"],
        "urls": i.get("urls") or [],
        "pitch": {k: p.get(k) for k in PITCH_KEYS if k in p} or None,
        "task_dir": i.get("task_dir"),
        # the build view link works when the task is under repo-factory ui's root
        "build_link": _under(i.get("task_dir"), root),
        "build": {
            k: b.get(k)
            for k in (
                "pid",
                "round",
                "status",
                "cost_usd",
                "failures",
                "resumes",
                "started",
                "finished",
                "error",
            )
        }
        if b
        else None,
        "gate": {
            "verdict": g.get("verdict"),
            "reasons": g.get("reasons") or [],
            "checks": g.get("checks") or {},
        }
        if g
        else None,
        "publish": {
            k: pub.get(k)
            for k in (
                "repo",
                "url",
                "release_url",
                "visibility",
                "version",
                "published_at",
                "approved_at",
                "error",
            )
        }
        if pub.get("repo") or pub.get("error")
        else None,
        "signals": (i.get("signals") or {}).get("github"),
    }
    if i["state"] == "building" and i.get("task_dir"):
        st = load_state(Path(i["task_dir"])) or {}
        act = st.get("activity") or {}
        out["activity"] = {
            "phase": act.get("phase"),
            "stage": act.get("stage"),
            "cost_usd": (act.get("cost") or {}).get("total"),
            "fraction": (act.get("progress") or {}).get("fraction"),
        }
    return out


def view(lp, root: Path) -> dict:
    root = Path(root).resolve()
    ideas = lp.ledger.all()
    names = {i["id"]: i.get("title") or i["id"] for i in ideas}
    hist = [
        {
            "id": h["id"],
            "idea": h["idea"],
            "title": names.get(h["idea"]),
            "ts": h["ts"],
            "state": h["state"],
            "note": h["note"],
        }
        for h in lp.ledger.history(limit=40, ideas_only=True)
        if h.get("note")
    ]
    return {"status": lp.status(), "ideas": [idea_view(i, root) for i in ideas], "history": hist}
