"""The pre-push checks, run before anyone is asked (no request the page or the command line
serves waits on a model call: a model-backed step is either done before its button is shown,
or started in the background with a state the page shows).

When a build lands held or ready, the loop's tick stages its tree as it would be pushed and
runs every pre-push check on it: the public text written from the product and its one
review (a model; their replacements are applied and listed, never a hold), the jargon,
secrets scan (``publish.stage`` and ``publish.finish``). The
result is kept in the idea's ``publish.precheck`` with ``key``, the hash of what it covered
(``tree_key``: the delivered commit, the pitch's fields the checks read (``PITCH_READ``), the
evidence that NOTICE quotes, the publish settings and the two prompts' versions). The scans
run again, with the installed repo-factory, at every press (``publish.finish``).

Publish anyway lists every hold from it, the gate's and these, and its press overrides exactly
those: it scans the staged tree again (no model) and pushes. A record that is still running, or
whose key no longer matches the tree, is "checking": the press is refused, and the page says
how long the checks have run. ``repo-factory loop precheck ID`` runs them by hand (and is what a press
starts when the daemon has not yet)."""

from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import os
import sys
import time
from pathlib import Path

from .. import paths, proc
from . import gate as gate_mod
from .tree import git

# a failed run (gh not logged in, the model unreachable) runs again on a later tick, waiting
# RETRY_S times the attempts, at most MAX_ATTEMPTS times for the same tree
RETRY_S = 300
MAX_ATTEMPTS = 3
SPAWN_ENV = "REPO_FACTORY_PRECHECK_SPAWN"  # "0": a press never starts one (the tests)
KEEP = (
    "owner",
    "name",
    "stage",
    "repo",
    "honesty",
    "tidied",
    "maintenance",
    "usd",
    "blocked",
    "secrets",
    "jargon",
    "round_dir",
    "evidence",
    "asset",
    "described",
    "release_text",
)
# The pitch's fields the checks read: the product's name, the sentences the public text may
# never copy (describe.pitch_sentences) and the evidence NOTICE names. Not the owner's release
# text (the push reads it as it is then) nor the claims (read at build start): Sidedoor's two
# saves of an unchanged release text each started the model again (AU2 #4)
PITCH_READ = ("product", "one_liner", "brief", "who", "why_now", "evidence_used")
INLINE_ENV = "REPO_FACTORY_PRECHECK_INLINE"  # "1": the tick runs the checks itself (the tests)


def tree_sha(idea: dict) -> str | None:
    """The delivered branch's commit now; None when nothing was delivered."""
    g = idea.get("gate") or {}
    if not g.get("out") or not g.get("branch"):
        return None
    try:
        return git("rev-parse", g["branch"], cwd=Path(g["out"])).strip() or None
    except Exception:  # noqa: BLE001 - no tree: nothing to check
        return None


def tree_key(idea: dict, cfg, sha: str | None = None) -> str | None:
    """The hash of what the pre-push checks look at (``sha``: the commit they staged; default
    the branch's now); None when nothing was delivered."""
    g = idea.get("gate") or {}
    sha = sha or tree_sha(idea)
    if sha is None:
        return None
    p = cfg["publish"]
    try:
        from ..custom_prompts import effective

        prompt = [effective("honesty").version, effective("describe").version]
    except Exception:  # noqa: BLE001 - the default prompts
        prompt = None
    card = idea.get("card") or {}
    pitch = idea.get("pitch") or {}
    blob = {
        "sha": sha,
        "round": g.get("round"),
        "pitch": {k: pitch.get(k) for k in PITCH_READ},
        "evidence": card.get("evidence"),
        "urls": idea.get("urls"),
        "prompt": prompt,
        "publish": {
            k: p.get(k)
            for k in (
                "owner",
                "license",
                "copyright",
                "honesty_model",
                "maintenance_files",
                "evidence_asset",
            )
        },
    }
    return hashlib.sha256(json.dumps(blob, sort_keys=True, default=str).encode()).hexdigest()[:16]


def record(idea: dict) -> dict:
    return dict((idea.get("publish") or {}).get("precheck") or {})


def status(idea: dict, cfg, key: str | None = None) -> dict:
    """``{"state", "since", "record"}``. state: ``done`` (current: its key is the tree's),
    ``running`` (by a live process), ``failed`` (on this tree), ``stale`` (an older tree, or a
    run whose process died), ``none`` (never ran), ``nothing`` (nothing delivered)."""
    rec = record(idea)
    key = key or tree_key(idea, cfg)
    if key is None:
        return {"state": "nothing", "since": None, "record": rec}
    if not rec:
        return {"state": "none", "since": None, "record": rec}
    if rec.get("key") != key:
        return {"state": "stale", "since": None, "record": rec}
    st = rec.get("state")
    if st == "running" and not proc.running(rec.get("pid"), rec.get("start")):
        st = "stale"
    return {
        "state": st if st in ("done", "running", "failed") else "stale",
        "since": rec.get("started"),
        "record": rec,
    }


def holds(rec: dict) -> list[tuple[str, str]]:
    """``[(check, detail)]``: the pre-push checks that would hold the push, from a done
    record's blocked lines, in ``gate.PREPUSH``'s names."""
    out = []
    for line in rec.get("blocked") or []:
        for k, v in gate_mod.PREPUSH.items():
            if line.startswith(k + ":"):
                out.append((v, line[len(k) + 1 :].strip()))
                break
    return out


def summary(idea: dict, cfg) -> dict | None:
    """What the page shows: ``{"state", "since", "holds": [[check, detail]], "error",
    "finished"}``; None for an idea the checks never run on."""
    if idea.get("state") not in ("held", "ready") or (idea.get("publish") or {}).get("url"):
        return None
    st = status(idea, cfg)
    rec = st["record"]
    return {
        "state": st["state"],
        "since": st["since"],
        "finished": rec.get("finished"),
        "holds": [list(h) for h in holds(rec)] if st["state"] == "done" else [],
        "error": rec.get("error") if st["state"] == "failed" else None,
    }


def waiting(st: dict) -> str:
    """Why a press waits, in words (the refusal a press gets while the checks are not done)."""
    if st["state"] == "running":
        n = int(time.time() - (st.get("since") or time.time()))
        return (
            f"the pre-push checks are running ({n} s): try again when they finish; "
            "nothing was published"
        )
    if st["state"] == "failed":
        return (
            "the pre-push checks failed on this tree ("
            + str(st["record"].get("error") or "no reason")
            + "); they run again on the loop's next tick; nothing was "
            "published"
        )
    if st["state"] == "nothing":
        return "nothing was delivered: there is nothing to check or publish"
    return (
        "the pre-push checks have not run on this tree yet: they start now; try again "
        "when they finish; nothing was published"
    )


def due(idea: dict, cfg, now: float | None = None) -> bool:
    """Whether the tick should run the checks on ``idea`` now: a held or ready build with a
    delivered tree and no repo, whose hold could ever be published over, and whose checks are
    missing, stale, or failed long enough ago."""
    if idea.get("state") not in ("held", "ready") or (idea.get("publish") or {}).get("url"):
        return False
    g = idea.get("gate") or {}
    if not ((g.get("checks") or {}).get("delivered") or {}).get("ok"):
        return False
    if idea["state"] == "held":
        may = (cfg["publish"].get("anyway") or {}).get("may_override")
        if gate_mod.override_plan(g.get("checks") or {}, [], may)["refused"]:
            return False  # never published over: no model call
    st = status(idea, cfg)
    if st["state"] in ("done", "running", "nothing"):
        return False
    if st["state"] == "failed":
        rec = st["record"]
        n = int(rec.get("attempts") or 1)
        return n < MAX_ATTEMPTS and (now or time.time()) - (rec.get("finished") or 0) >= RETRY_S * n
    return True


@contextlib.contextmanager
def lock(home: Path, idea_id: str):
    """One run per idea at a time, across processes: yields False when another holds it."""
    d = paths.Layout(home).publish
    d.mkdir(parents=True, exist_ok=True)
    f = open(d / f".{idea_id}.precheck.lock", "w")
    try:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        try:
            yield True
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)
    finally:
        f.close()


def kept(prep: dict) -> dict:
    """The parts of a staged and checked tree the record keeps (lists cut at 20)."""
    out = {k: prep.get(k) for k in KEEP if k in prep}
    for k in ("secrets", "jargon"):
        out[k] = list(out.get(k) or [])[:20]
    return out


def spawn(cfg, idea_id: str) -> int | None:
    """Start ``repo-factory loop precheck ID`` detached (its output in ``publish/<id>.precheck.log``):
    what a press does when the daemon has not run the checks yet. Returns its pid."""
    if os.environ.get(SPAWN_ENV) == "0":
        return None
    d = cfg.layout.publish
    d.mkdir(parents=True, exist_ok=True)
    p = proc.spawn_detached(
        [
            sys.executable,
            "-m",
            "repo_factory.cli",
            "loop",
            "--home",
            str(cfg.home),
            "precheck",
            idea_id,
        ],
        log=d / f"{idea_id}.precheck.log",
    )
    return p.pid
