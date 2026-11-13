"""The cost ledger: every ``claude -p`` call a build makes, one JSON line each, in
``<den>/ledger.jsonl`` (docs/design/build-v3.md §6).

Two ways in, both through ``Ledger.append``:

- ``call_claude``: the one helper for single-shot calls (``--output-format json``, no
  tools). ``scoring.builder.run_claude_text`` and the rules engine's ``ClaudeLLM`` use it,
  so the builder (questions, checklist, tests), the judge, the planner, the miner,
  validator and arbiter, and the simulated owner are all recorded.
- ``record``: for calls whose cost is parsed elsewhere. The orchestrator records every
  worker run (the Claude Code harness) with its node and milestone, its own cost (a
  resumed session's cumulative cost minus the parent's, ``harness.claude_code.run_cost``)
  and the session's cumulative figure.

A ledger is *active* for the duration of a build (``activate``); calls made while none is
active (hibernate, unit tests) are not recorded. ``tags`` sets the caller, purpose,
milestone or node for every call made inside it (a context variable, so a background
thread started with ``contextvars.copy_context().run`` keeps its tags).

A record: ``ts``, ``build``, ``caller`` (``worker``, ``builder``, ``judge``, ``planner``,
``tech``, ``plan_review``, ``scoper``, ``miner``, ``validator``, ``arbiter``, ``sim_owner``,
...),
``purpose``, ``category`` (``workers``, ``mining``, ``judging``, ``planning``, ``other``),
``model``, ``in_tokens``, ``out_tokens``, ``usd`` (this call's own cost, ``None`` when
unknown), ``wall_s``, ``ok``, plus ``node``, ``milestone``, ``session_usd`` and
``base_usd`` where they apply, and ``error`` for a failed call.

Live activity: while a build's ledger is active with its event log (``activate(...,
events=log)``), every ``call_claude`` emits ``call_started`` / ``call_finished`` events
(``track``), so the UI sees each model call as it runs. ``track`` also wraps work that is
not a ``claude -p`` call itself (a fixture-backed scoper or miner in a fake build, the whole
pairwise tournament of a design task); a call nested in a span of the same caller on the
same thread is folded into it (its cost adds to the span's) instead of getting a row of its
own, so each dollar is reported once."""

from __future__ import annotations

import contextlib
import contextvars
import itertools
import json
import subprocess
import threading
import time
from pathlib import Path

FILENAME = "ledger.jsonl"
CATEGORIES = ("workers", "mining", "judging", "planning", "other")
CATEGORY = {
    "worker": "workers",
    "miner": "mining",
    "validator": "mining",
    "arbiter": "mining",
    "judge": "judging",
    "pairwise": "judging",
    "planner": "planning",
    "tech": "planning",
    "plan_review": "planning",
    "builder": "planning",
    "controller": "planning",
    "scoper": "planning",
    # build v4 design search
    "design": "planning",
    "design_judge": "planning",
    "rule_walk": "planning",
    # build v5 (docs/design/build-v5.md §3.8)
    "claims": "mining",
    "verifier": "mining",
    "oracle": "judging",
    "pair": "judging",
    "postmortem": "other",
    "scope": "planning",
    # the readiness stage (Addendum C): the security review and the operational checks
    "security": "mining",
    "readiness": "mining",
}

_tags: contextvars.ContextVar[dict] = contextvars.ContextVar("factory_ledger_tags", default={})
_active: Ledger | None = None
_active_lock = threading.Lock()
_span: contextvars.ContextVar[Span | None] = contextvars.ContextVar(
    "factory_ledger_span", default=None
)
_ids = itertools.count(1)
_ids_lock = threading.Lock()


def category(caller: str | None) -> str:
    return CATEGORY.get(str(caller or ""), "other")


def ledger_path(den_root: Path) -> Path:
    return Path(den_root) / FILENAME


class Ledger:
    """Appends records to ``path`` (thread-safe: one whole line per record, under a lock)."""

    def __init__(self, path: Path, build: int | None = None, events=None):
        self.path = Path(path)
        self.build = build
        self.events = events  # the build's EventLog: call_* activity events
        self.lock = threading.Lock()
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, rec: dict) -> dict:
        rec = {"ts": round(time.time(), 3), "build": self.build, **rec}
        rec.setdefault("category", category(rec.get("caller")))
        with self.lock, open(self.path, "a") as f:
            f.write(json.dumps(rec, default=str, sort_keys=True) + "\n")
        return rec


def read(path: Path, build: int | None = None) -> list[dict]:
    """Records in a ledger file (a den dir or the file itself), optionally of one build;
    a torn last line is skipped."""
    p = Path(path)
    if p.is_dir():
        p = ledger_path(p)
    if not p.exists():
        return []
    out = []
    for line in p.read_text(errors="replace").splitlines():
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(r, dict) and (build is None or r.get("build") == build):
            out.append(r)
    return out


def summarize(records: list[dict]) -> dict:
    """``{"total_usd", "breakdown": {category: usd}, "by_caller": {caller: {"usd", "calls"}},
    "calls", "unknown_cost"}`` (calls whose cost is unknown count 0 and are counted)."""
    br = {c: 0.0 for c in CATEGORIES}
    by: dict[str, dict] = {}
    unknown = 0
    for r in records:
        usd = r.get("usd")
        if usd is None:
            unknown += 1
        c = r.get("category") or category(r.get("caller"))
        br[c if c in br else "other"] += float(usd or 0.0)
        b = by.setdefault(str(r.get("caller")), {"usd": 0.0, "calls": 0})
        b["usd"] += float(usd or 0.0)
        b["calls"] += 1
    return {
        "total_usd": round(sum(br.values()), 4),
        "breakdown": {k: round(v, 4) for k, v in br.items()},
        "by_caller": {
            k: {"usd": round(v["usd"], 4), "calls": v["calls"]} for k, v in sorted(by.items())
        },
        "calls": len(records),
        "unknown_cost": unknown,
    }


# -- the active ledger and tags ---------------------------------------------------------
def active() -> Ledger | None:
    return _active


@contextlib.contextmanager
def activate(path: Path, build: int | None = None, events=None):
    """Make a ledger at ``path`` the process's active one for the duration (a build).
    ``events``: the build's ``EventLog``, which then gets the ``call_*`` events."""
    global _active
    led = Ledger(path, build, events)
    with _active_lock:
        prev, _active = _active, led
    try:
        yield led
    finally:
        with _active_lock:
            _active = prev


@contextlib.contextmanager
def tags(**kw):
    """Caller, purpose, milestone, node... for every call recorded inside the block
    (inner blocks override outer ones)."""
    tok = _tags.set({**_tags.get(), **{k: v for k, v in kw.items() if v is not None}})
    try:
        yield
    finally:
        _tags.reset(tok)


def current_tags() -> dict:
    return dict(_tags.get())


def record(caller: str | None = None, **fields) -> dict | None:
    """Append one record to the active ledger (None when no ledger is active). The
    current ``tags`` fill in what ``fields`` leave out."""
    led = _active
    if led is None:
        return None
    rec = {
        **_tags.get(),
        **{k: v for k, v in fields.items() if v is not None or k in ("usd", "model")},
    }
    rec.setdefault("usd", None)
    if caller is not None:
        rec["caller"] = caller
    rec.setdefault("caller", "other")
    return led.append(rec)


# -- live activity: call_started / call_finished ----------------------------------------
class Span:
    """One model call (or a span of work standing for one) under way. ``usd`` and
    ``attempts`` accumulate over the call's attempts and any same-caller nested calls;
    ``ok = False`` marks it failed even when no exception ends the block."""

    def __init__(self, id: str | None, caller: str):
        self.id, self.caller = id, caller
        self.thread = threading.get_ident()
        self.usd: float | None = None
        self.attempts = 0
        self.ok: bool | None = None
        self.error: str | None = None

    def add(self, usd) -> None:
        if usd is not None:
            self.usd = round((self.usd or 0.0) + float(usd), 7)


@contextlib.contextmanager
def track(caller: str | None = None, purpose: str | None = None, model: str | None = None, **meta):
    """``call_started`` now and ``call_finished`` when the block ends, on the active
    ledger's event log; yields a ``Span``. Without an active ledger with an event log no
    events are written. The current ``tags`` fill in the caller, purpose and milestone;
    ``meta`` (e.g. ``unit``, ``task_id``, ``n``) goes on ``call_started``. Thread-safe:
    ids come from a locked counter and ``EventLog.emit`` writes under its lock."""
    t = _tags.get()
    caller = caller or t.get("caller") or "other"
    purpose = purpose or t.get("purpose")
    outer = _span.get()
    if outer is not None and outer.caller == caller and outer.thread == threading.get_ident():
        yield outer  # folded into the enclosing span
        return
    led = _active
    log = getattr(led, "events", None)
    span = Span(None, caller)
    if log is not None:
        with _ids_lock:
            n = next(_ids)
        span.id = f"c{led.build}-{n}" if led.build is not None else f"c{n}"
        common = {
            "call": span.id,
            "caller": caller,
            "purpose": purpose,
            "model": model,
            "category": category(caller),
        }
        start = {
            **common,
            **{k: t[k] for k in ("milestone", "node") if t.get(k) is not None},
            **{k: v for k, v in meta.items() if v is not None},
        }
        if threading.current_thread() is not threading.main_thread():
            start["background"] = True
        t0 = time.time()
        _emit(log, "call_started", start)
    tok = _span.set(span)
    try:
        yield span
    except BaseException as e:
        span.ok = False
        span.error = span.error or f"{type(e).__name__}: {e}"[:300]
        raise
    finally:
        _span.reset(tok)
        if log is not None:
            fin = {
                **common,
                "usd": span.usd,
                "wall_s": round(time.time() - t0, 3),
                "ok": span.ok is not False,
            }
            if span.attempts > 1:
                fin["attempts"] = span.attempts
            if span.ok is False and span.error:
                fin["error"] = span.error
            _emit(log, "call_finished", fin)


def _emit(log, type: str, fields: dict) -> None:
    try:
        log.emit(type, **fields)
    except Exception:  # activity is display only: never fail a call
        pass


# -- single-shot calls ------------------------------------------------------------------
def claude_command(
    model: str | None = None, tools: tuple[str, ...] = (), max_usd: float | None = None
) -> list[str]:
    """``claude -p`` with no tools, or with exactly ``tools`` (allowed without asking: the
    research desk's ``WebSearch``), and ``--max-budget-usd`` when ``max_usd`` is set. The
    binary is ``build.claude_command`` when that is set, else ``claude`` on PATH."""
    from ..claude_cli import configured_exe

    cmd = [
        configured_exe() or "claude",
        "-p",
        "--output-format",
        "json",
        "--no-session-persistence",
        "--tools",
        ",".join(tools),
        "--strict-mcp-config",
    ]
    if tools:
        cmd += ["--allowedTools", ",".join(tools)]
    if max_usd is not None:
        cmd += ["--max-budget-usd", f"{float(max_usd):g}"]
    return cmd + (["--model", model] if model else [])


def usage_of(data: dict) -> dict:
    """Tokens and cost from a ``claude -p --output-format json`` result."""
    u = data.get("usage") or {}
    models = list((data.get("modelUsage") or {}).keys())
    return {
        "usd": None
        if data.get("total_cost_usd") is None
        else round(float(data["total_cost_usd"]), 7),
        "in_tokens": int(
            u.get("input_tokens", 0)
            + u.get("cache_read_input_tokens", 0)
            + u.get("cache_creation_input_tokens", 0)
        ),
        "out_tokens": int(u.get("output_tokens", 0)),
        "model_used": models[0] if len(models) == 1 else (models or None),
    }


NO_CLAUDE = (
    "Claude Code is not installed here (no claude command on PATH): install it and "
    "sign in; repo-factory doctor checks it"
)


def _said(stdout: str, stderr: str) -> str:
    """What a failed ``claude -p`` said, in words: the JSON answer's ``result`` (a usage limit,
    a sign-in problem), else the end of what it printed."""
    try:
        data = json.loads(stdout or "")
    except (ValueError, TypeError):
        data = None
    if isinstance(data, dict) and isinstance(data.get("result"), str) and data["result"].strip():
        return data["result"].strip()[:500]
    return (stderr or stdout or "")[-500:]


def call_claude(
    prompt: str,
    model: str | None = None,
    timeout_s: int = 300,
    retries: int = 3,
    caller: str | None = None,
    purpose: str | None = None,
    env: dict | None = None,
    tools: tuple[str, ...] = (),
    max_usd: float | None = None,
    **meta,
) -> tuple[str, dict]:
    """``claude -p`` once without tools; returns ``(result text, data)`` where ``data`` is
    the parsed JSON output. Timeouts are retried with more time, a non-zero exit too
    (``retries`` attempts in all). Every attempt is recorded in the active ledger, failed
    ones with ``ok: false`` and ``usd: null``. Raises ``RuntimeError`` when all fail.
    ``env``: variables added to the process environment (build v5: ``CLAUDE_CONFIG_DIR``,
    so no-tools calls run under a clean config dir too). The Claude token goes through a
    pipe, never in the environment (``harness/handoff.py``). ``tools`` and ``max_usd``: as
    ``claude_command`` (the research desk: ``("WebSearch",)`` and its cap)."""
    from ..harness import handoff

    cmd = claude_command(model, tools, max_usd)
    last = None
    with track(caller, purpose, model) as span:
        for attempt in range(retries):
            t0 = time.time()
            err = None
            span.attempts += 1
            penv = handoff.process_env(env)
            try:
                with handoff.piped(penv) as fds:
                    p = subprocess.run(
                        cmd,
                        input=prompt,
                        capture_output=True,
                        text=True,
                        timeout=timeout_s * (attempt + 1),
                        env=penv,
                        pass_fds=fds,
                    )
            except FileNotFoundError:
                raise RuntimeError(NO_CLAUDE) from None
            except subprocess.TimeoutExpired:
                err = f"timed out after {timeout_s * (attempt + 1)}s"
            else:
                if p.returncode == 0:
                    try:
                        data = json.loads(p.stdout)
                    except json.JSONDecodeError:
                        err = "unparsable output: " + p.stdout[-300:]
                    else:
                        use = usage_of(data)
                        span.add(use["usd"])
                        if data.get("is_error"):
                            span.ok = False
                        record(
                            caller,
                            purpose=purpose,
                            model=model,
                            ok=not data.get("is_error"),
                            wall_s=round(time.time() - t0, 3),
                            chars=len(prompt),
                            attempt=attempt + 1,
                            **use,
                            **meta,
                        )
                        return data.get("result") or "", data
                else:
                    err = f"exit {p.returncode}: {_said(p.stdout, p.stderr)}"
            last = err
            record(
                caller,
                purpose=purpose,
                model=model,
                ok=False,
                usd=None,
                wall_s=round(time.time() - t0, 3),
                chars=len(prompt),
                attempt=attempt + 1,
                error=err[:300],
                **meta,
            )
        span.error = (last or "")[:300]
        raise RuntimeError(
            f"claude -p failed ({caller or _tags.get().get('caller')}/"
            f"{purpose or _tags.get().get('purpose')}): {last}"
        )
