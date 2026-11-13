"""repo-factory's actions: one registry, every door.

Every action the owner can take on a task or a build is one ``Action`` here: its title and
its plain words (the page's tooltip), the subjects it applies to, its fields, its click class,
its key on the page, ``when`` (may it run now, and if not, why not, in words) and ``run``. The
page (``api/actions``, ``api/act``) and the command line read and run the same table.

**Where the guards sit.** ``when`` is the presentation check: buttons and errors in words. The ``Loop`` method behind each ``repo-factory loop`` verb is still the authority: every
action ends in a loop verb (through ``App.loop_act``) or an ``App`` method, which refuses a
wrong state whichever door the call came through. ``tests/test_registry.py`` checks the two
agree.

**The confirm class** is ``publish``, ``publish_anyway``, ``close``, ``build_anyway``, one
build's controls that stop work (``stop_round``, ``cancel``, ``restart_revise``) and ``accept_claim`` (it may start a build an unbuildable claim held): a
press plus a confirm dialog on the page, ``confirm: true`` over ``api/act``. ``run`` refuses
one without ``Ctx.confirm``. No action runs without a press."""

from __future__ import annotations

import contextvars
import json
import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from dataclasses import field as dc_field
from pathlib import Path
from typing import Literal

# ui, cli, honesty (the loop's own docs repair), auto (the loop's own repair round on a
# hold), agent:<name>
Origin = str
Click = Literal["click", "confirm"]  # confirm: click + confirm dialog
ORIGINS = ("ui", "cli", "honesty", "auto")
LOOP_ORIGINS = ("honesty", "auto")  # the loop's own: never a token's
# a named agent's token: its origin in every record
AGENT_ORIGIN = re.compile(r"agent:[A-Za-z0-9][A-Za-z0-9_.-]{0,39}")
CONFIRM_CLASS = (
    "publish",
    "make_public",
    "publish_anyway",
    "close",
    "build_anyway",
    "stop_round",
    "cancel",
    "restart_revise",
    "accept_claim",
    "charter_change",
    "github_app_create",
    "github_app_key",
    "github_app_remove",
    "github_app_link",
    "github_disconnect",
    "claude_login_forget",
    "maintain_merge_policy",
)
UNDOABLE = ("stop_round", "cancel")  # the flash offers Undo
# repo-factory's own GitHub App: the page and the command line only; never an agent's token
# (it makes, replaces or removes a credential)
GITHUB_KINDS = (
    "github_app_create",
    "github_app_cancel",
    "github_app_link",
    "github_app_refresh",
    "github_app_key",
    "github_app_remove",
    "github_connect",
    "github_disconnect",
)
# the Claude login builds use: the page and the command line only, as GITHUB_KINDS
CLAUDE_KINDS = ("claude_login_keep", "claude_login_forget")
CLAUDE_REFUSAL = "{kind} is the owner's: the Claude login is set up from Settings › Claude"
# the owner's writes to a product's repository outside a fix: the page and the command line
# only, as GITHUB_KINDS
OWNER_KINDS = ("maintain_merge_policy",)
# what an agent's token never runs: the owner's own, and a hand build's stop
AGENT_HIDDEN = ("stop", *GITHUB_KINDS, *OWNER_KINDS, *CLAUDE_KINDS)

_log = logging.getLogger("repo_factory.registry")
# ``api/act {check: true}`` checks a press's fields and runs nothing
ACT_CHECK = True


def valid_origin(origin) -> bool:
    """One of ``ORIGINS``, or ``agent:<name>``."""
    return origin in ORIGINS or bool(AGENT_ORIGIN.fullmatch(str(origin or "")))


MAX_TEXT = 4000
MAX_NOTE = 300
MAX_ISSUE_TITLE = 120
IDEA_SENDABLE = ("new", "rejected", "error", "skipped", "closed")
TICKET_LABELS = ("bug", "feature", "docs", "security", "question", "p1", "p2", "p3", "go")
REJECT_CHIPS = ("wrong idea", "wrong scope", "bugs", "install or README", "security", "not for me")


class ActionError(ValueError):
    """An action that cannot run: ``code`` 400 for a bad field, 409 for a state or confirm
    refusal (``confirm`` True: it needs ``confirm: true``)."""

    def __init__(
        self, msg: str, code: int = 409, confirm: bool = False, refusal: str | None = None
    ):
        super().__init__(msg)
        self.code, self.confirm = code, confirm
        self.refusal = refusal or ("fields" if code == 400 else "state")


# A refused or failed press's kind, for its record and the usage reports (never its words):
# a field, the subject's state, holds that changed since the dialog, the pre-push checks still
# running, the loop's own refusal, the door, or an error
REFUSALS = ("fields", "state", "holds_changed", "checking", "loop", "door", "error")
# what the loop says, as the kind it is (its LoopError's words; tests/test_refusals.py)
LOOP_REFUSALS = (
    ("the holds changed", "holds_changed"),
    ("pre-push checks", "checking"),
    ("confirm it with --yes", "state"),
    ("only a held build", "state"),
    ("say why, in 12 to 300", "fields"),
)


def refusal_of(exc: BaseException) -> str:
    """The kind of a press's refusal (``REFUSALS``) from what was raised."""
    if isinstance(exc, ActionError):
        return exc.refusal
    text = str(exc)
    for words, kind in LOOP_REFUSALS:
        if words in text:
            return kind
    from .errors import LoopError

    if isinstance(exc, LoopError) or type(exc).__name__ == "HttpError":
        return "loop"
    return "error"


@dataclass(frozen=True)
class Field:
    name: str
    kind: Literal["text", "line", "enum", "list", "int", "bool"]
    required: bool = False
    max: int = 300
    min: int = 0
    choices: tuple[str, ...] = ()
    help: str = ""
    ask: bool = False  # asked for at the press (the dialog)


@dataclass
class Subject:
    """``kind`` new|idea|build|loop; ``idea`` the
    snapshot's idea row; ``row`` its build row; ``cfg`` the loop's settings (loop.yaml)."""

    kind: str
    idea: dict | None = None
    row: dict | None = None
    cfg: dict | None = None
    id: str | None = None


@dataclass
class Ctx:
    app: object
    origin: str
    confirm: bool = False


@dataclass(frozen=True)
class Action:
    kind: str  # "revise"
    title: str  # button / card label
    says: str  # plain words: the page's tooltip
    subjects: tuple[str, ...]  # "new" | "idea" | "build" | "loop"
    fields: tuple[Field, ...]
    click: Click
    key: str | None  # the Decide key on the page
    when: Callable[[Subject], str | None]
    run: Callable[[Ctx, dict, Subject], dict]
    cmd: str = ""  # the command it runs, in words
    order: int = dc_field(default=0)


# -- the subject's state ---------------------------------------------------------------------
def _istate(s: Subject) -> str | None:
    return (s.idea or {}).get("state") or (s.row or {}).get("idea_state")


def _iid(s: Subject) -> str | None:
    if s.idea:
        return s.idea["id"]
    if s.row:
        return s.row.get("idea")
    return None


def _cfg(s: Subject, *keys, default=None):
    from .settings.store import DEFAULTS

    for src in (s.cfg or {}, DEFAULTS):
        node = src
        for k in keys:
            node = node.get(k) if isinstance(node, dict) else None
            if node is None:
                break
        if node is not None:
            return node
    return default


def one_line(s: str | None, n: int = MAX_NOTE) -> str:
    s = " ".join(str(s or "").split())
    return s if len(s) <= n else s[: n - 1] + "…"


# -- when: may it run now (None), else why not, in words -------------------------------------
def w_add_idea(s):
    return None if s.kind == "new" else "it is already a task"


def w_send_to_build(s):
    if s.kind == "new":
        return None
    idea, st = s.idea, (s.idea or {}).get("state")
    if st in IDEA_SENDABLE and not (st == "new" and idea.get("sent")):
        return None
    return "it is already sent or building" if st else "there is no task to send"


def w_build_anyway(s):
    idea = s.idea or {}
    return (
        None
        if idea.get("state") == "rejected" and (idea.get("pitch") or {}).get("product")
        else "only a task the brief passed on can be built anyway"
    )


def w_pitch_again(s):
    return (
        None
        if (s.idea or {}).get("state") in ("rejected", "error", "skipped", "closed")
        else "only a passed, skipped, failed or closed task gets its brief again"
    )


def w_retry(s):
    row = s.row
    if row and not row.get("idea"):
        return None if row["status"] in ("failed", "held") else "only a failed build is retried"
    return (
        None
        if (s.idea or {}).get("state") == "held" or (row or {}).get("idea_state") == "held"
        else "only a held build is retried"
    )


def w_close(s):
    st = _istate(s)
    if not st:
        return "only a loop task or build can be closed here"
    return None if st not in ("public", "closed") else f"it is already {st}"


def w_publish(s):
    row = s.row or {}
    return (
        None
        if _istate(s) == "ready" and (row.get("idea") or s.idea)
        else "only a ready build can be published"
    )


def w_make_public(s):
    return (
        None if _istate(s) == "published" else "only a published (private) repo can be made public"
    )


def w_keep(s):
    row = s.row or {}
    if _istate(s) != "published":
        return "only a published (private) repo can be kept private"
    if row.get("status") == "kept":
        return "it is already kept private"
    return None


def w_reject(s):
    row = s.row or {}
    if row and not row.get("idea"):  # a build started by hand: its verdict
        return (
            None
            if row.get("status") in ("held", "ready") and row.get("state") == "delivered"
            else "only a delivered build is rejected"
        )
    return (
        None
        if _istate(s) == "published"
        else "only a build ready to go public is rejected here; Close says no to the rest"
    )


def w_later(s):
    idea = s.idea or {}
    return (
        None
        if idea.get("state") in ("new", "rejected", "error", "skipped")
        else "only an open task can wait until later"
    )


def w_edit_brief(s):
    idea, row = s.idea, s.row or {}
    st = (idea or {}).get("state")
    if s.kind == "new":
        return "there is no task yet; Add task or Build carries the text"
    if st in ("new", "rejected", "closed", "error", "skipped"):
        return None
    if st == "queued" and not row.get("live") and row.get("state") == "new":
        return None
    return "the build has started; the brief can no longer change"


def w_answer(s):
    return None if (s.row or {}).get("question") else "the build is not asking anything"


def w_stop(s):
    row = s.row or {}
    if row.get("idea"):
        return "a loop build stops with Stop round"
    return None if row.get("state") in ("running", "asking") else "nothing is running"


# -- one build's controls (RC) ----------------------------------------------------------------
CONTROL_WORDS = {"stop": "stopping", "restart": "restarting"}


def _controlling(s) -> str | None:
    """Why not, when the owner's stop of this build is under way."""
    c = (s.row or {}).get("control") or {}
    return f"it is {CONTROL_WORDS.get(c.get('what'), 'stopping')} now" if c else None


def _loop_build(s) -> str | None:
    return (
        None
        if (s.row or {}).get("idea") or (s.idea and _istate(s))
        else "only a loop build is controlled here (Stop stops a build started by hand)"
    )


def w_stop_round(s):
    st = _istate(s)
    if _loop_build(s):
        return _loop_build(s)
    if st == "paused":
        return None
    if st != "building":
        return "only a running or paused round is stopped" + (
            "; Cancel withdraws a queued one" if st == "queued" else ""
        )
    return _controlling(s)


def w_resume_build(s):
    st = _istate(s)
    if _loop_build(s):
        return _loop_build(s)
    if st == "paused":
        return None
    stopped = (s.row or {}).get("stopped") or {}
    if st in ("held", "ready") and stopped.get("resumable") and not (s.row or {}).get("url"):
        return None
    return "only a round you stopped is resumed"


def w_cancel(s):
    if _loop_build(s):
        return _loop_build(s)
    st = _istate(s)
    return (
        None
        if st == "queued"
        else "only a queued build is cancelled"
        + ("; Stop round stops a running one" if st == "building" else "")
    )


def w_restart_revise(s):
    if _loop_build(s):
        return _loop_build(s)
    if _istate(s) != "building" or not (s.row or {}).get("change"):
        return "only a running revise round is restarted (Ask for changes asks for one)"
    return _controlling(s)


def change_text(s: Subject) -> str:
    """The running revise round's instructions (Restart's box starts from them)."""
    f = ((s.row or {}).get("change") or {}).get("file")
    try:
        return Path(f).read_text().strip()[:MAX_TEXT] if f else ""
    except OSError:
        return ""


def w_undo(s):
    """The last Stop round or Cancel of this build, within its window (the row's ``undo``,
    from the loop's record; ``Loop.undo`` checks again)."""
    if _loop_build(s):
        return _loop_build(s)
    u = (s.row or {}).get("undo") or (s.idea or {}).get("undo") or {}
    if not u.get("of"):
        return "there is nothing to undo: only Stop round and Cancel are undone"
    if time.time() > float(u.get("until") or 0):
        return "too late to undo: it was more than a few seconds ago"
    return None


def w_cut_ticket(s):
    row = s.row or {}
    st = row.get("idea_state") or (s.idea or {}).get("state")
    if st in ("published", "public") and row.get("repo"):
        return None
    return "only a published build has a repository to open an issue on"


def w_revise(s):
    row, st = s.row or {}, _istate(s)
    if not row.get("idea"):
        return "only a loop build is revised here (repo-factory revise <task> revises one by hand)"
    if st in ("published", "public") or row.get("url"):
        return (
            "a published product changes through an issue: Open issue (with go to have "
            "repo-factory fix it)"
        )
    if st == "queued" and (row.get("change") or row.get("status") == "queued"):
        return "it is waiting for a build slot"
    if st == "building" or row.get("live"):
        return "it is building now"
    if st not in ("held", "ready"):
        return "only a held or ready build is revised"
    checks = (row.get("gate") or {}).get("checks") or {}
    if not (checks.get("delivered") or {}).get("ok"):
        return "its build delivered nothing to change: Retry builds it again"
    if st == "held" and row.get("cant_revise"):  # the hold is repo-factory's own sandbox
        return row["cant_revise"]
    if not _cfg(s, "revise", "enabled", default=True):
        return "revise is off (revise.enabled: false in loop.yaml)"
    return None


def suggest_mode(s: Subject) -> str:
    """The mode Ask for changes preselects (``change.pick_mode``)."""
    from .v5.change import pick_mode

    reason = (s.idea or {}).get("reason") or (s.row or {}).get("reason") or ""
    return pick_mode(
        (s.row or {}).get("gate"), reason if reason.startswith("not pushed:") else None
    )


def anyway_plan(s: Subject) -> dict:
    """``gate.override_plan`` from the gate as the installed repo-factory judges the build's round
    now (``gate.now``: the same evaluation the press makes, ASKF) and its pre-push checks
    (``precheck``, run when the build stopped), plus ``checking`` (``{state, since, error}``
    while they are not done on this tree, else None), ``details`` (each pre-push hold's line)
    and ``rejudged`` (the holds the stored verdict named, when today's gate names others: an
    upgrade changed a check)."""
    from .loop import gate

    row = s.row or {}
    stored = row.get("gate") or {}
    # the snapshot's rows: ``round`` is the idea's last build's; ``build`` there is a key
    rnd = stored.get("round") or (row.get("round") if isinstance(row.get("round"), int) else None)
    task = (s.idea or {}).get("task_dir") or row.get("dir")
    checks = stored.get("checks") or {}
    try:
        checks = gate.judged(task, rnd, stored).get("checks") or checks
    except Exception as e:  # noqa: BLE001 - the stored verdict, said so
        _log.warning("the gate could not be judged again for %s: %s", task, e)
    if not checks:
        checks = {"delivered": {"ok": False, "detail": "no gate result: the build did not deliver"}}
    pre = row.get("precheck") or {"state": "none"}
    done = pre.get("state") == "done"
    holds = [(k, d) for k, d in pre.get("holds") or []] if done else []
    plan = gate.override_plan(
        checks, [k for k, _ in holds], _cfg(s, "publish", "anyway", "may_override")
    )
    plan["checking"] = (
        None
        if done
        else {"state": pre.get("state"), "since": pre.get("since"), "error": pre.get("error")}
    )
    plan["details"] = dict(holds)
    was, now = gate.failing(stored), gate.failing({"checks": checks})
    plan["rejudged"] = {"stored": was, "now": now} if stored.get("checks") and was != now else None
    return plan


CHECK_WORDS = {
    "safe_defaults": "localhost and quick start",
    "rea_scan": "rea scan",
    "secrets": "secrets scan",
    "documents": "studied record",
}


def plan_view(plan: dict) -> dict:
    """The Publish anyway dialog's list: every hold, the gate's and the pre-push checks',
    each overridable (``may``) or never, with why: ``{"holds": [{check, label, detail, may,
    why}], "checking", "ok"}`` (``ok``: the press may run once a reason is written)."""
    det = plan.get("details") or {}
    out = [
        {
            "check": k,
            "label": CHECK_WORDS.get(k, k),
            "detail": det.get(k) or d,
            "may": True,
            "why": None,
        }
        for k, d in plan["overrides"]
    ]
    out += [
        {
            "check": k,
            "label": CHECK_WORDS.get(k, k),
            "detail": det.get(k) or "",
            "may": False,
            "why": why,
        }
        for k, why in plan["refused"]
    ]
    rj = plan.get("rejudged")
    return {
        "holds": out,
        "checking": plan.get("checking"),
        "ok": bool(plan["overrides"]) and not plan["refused"] and not plan.get("checking"),
        "rejudged": (
            {
                "was": [CHECK_WORDS.get(k, k) for k in rj["stored"]],
                "now": [CHECK_WORDS.get(k, k) for k in rj["now"]],
                "version": _installed(),
            }
            if rj
            else None
        ),
    }


def _installed() -> str | None:
    try:
        from .loop.recheck import installed

        return installed()
    except Exception:  # noqa: BLE001 - said without the number
        return None


def checking_words(c: dict) -> str:
    if c.get("state") == "running":
        return "the pre-push checks are running; Publish despite the holds waits until they finish"
    if c.get("state") == "nothing":
        return "its delivered tree is not there: the pre-push checks have nothing to check"
    if c.get("state") == "failed":
        return (
            f"the pre-push checks failed ({c.get('error') or 'no reason'}); they run again "
            "on the loop's next tick"
        )
    return "the pre-push checks have not run on this tree yet; the loop runs them on its next tick"


def w_publish_anyway(s):
    if _istate(s) != "held":
        return "only a held build is published anyway"
    if not (s.row or {}).get("idea"):
        return "only a loop build is published anyway"
    plan = anyway_plan(s)
    if plan["refused"]:
        return "; ".join(why for _, why in plan["refused"])
    if plan["checking"]:
        return checking_words(plan["checking"])
    if not plan["overrides"]:
        return "it is not held by a check: Retry tries publishing again"
    return None


# -- run: through the page's own methods -----------------------------------------------------
_ORIGIN: contextvars.ContextVar = contextvars.ContextVar("factory_action_origin", default=None)


def current_origin() -> str | None:
    """The origin of the action running now (``App.loop_act`` passes it to the loop verb as
    ``--origin``); None outside ``run``."""
    return _ORIGIN.get()


def _loop(ctx: Ctx, what: str, body: dict) -> dict:
    return ctx.app.loop_act(what, body)


def r_add_idea(ctx, f, s):
    r = _loop(ctx, "add", {"text": f.get("text")})
    return {
        "result": r,
        "subject": {"kind": "idea", "id": r.get("id")} if r.get("id") else None,
        "say": "Added to Ideas.",
        "what": f"added {r.get('id') or 'the idea'}",
    }


def r_send_to_build(ctx, f, s):
    iid, new = _iid(s), None
    if not iid:
        r = _loop(ctx, "add", {"text": f.get("text")})
        iid = r.get("id")
        if not iid:
            raise ActionError("the idea could not be added: " + (r.get("out") or ""))
        new = {"kind": "idea", "id": iid}
    r = _loop(ctx, "send", {"id": iid})
    return {
        "result": r,
        "subject": new,
        "say": "Sent to build: its brief is written on the "
        "loop's next tick, then the build starts and shows in Builds.",
        "what": f"sent {r.get('id') or iid} to build",
    }


def r_pitch_again(ctx, f, s):
    return {
        "result": _loop(ctx, "send", {"id": _iid(s)}),
        "say": "Sent to the pitch again.",
        "what": "sent to the pitch again",
    }


def r_build_anyway(ctx, f, s):
    return {
        "result": _loop(ctx, "send", {"id": _iid(s), "anyway": True}),
        "say": "Queued to build anyway.",
        "what": "queued to build anyway",
    }


def r_retry(ctx, f, s):
    row = s.row
    if row and not row.get("idea"):
        return {
            "result": ctx.app.start(Path(row["dir"]), "v5"),
            "say": "Building again, from the beginning.",
            "what": "started building again",
        }
    note = one_line(f.get("note") or f.get("why"))
    iid = _iid(s)
    return {
        "result": _loop(ctx, "retry", {"id": iid, "note": note} if note else {"id": iid}),
        "say": "Queued again.",
        "what": f"queued {iid} again" + (f" ({note})" if note else ""),
        "note": note,
    }


def r_close(ctx, f, s):
    reason = " ".join((f.get("reason") or "").split())
    if not reason:
        raise ActionError("say why you close it, in a few words", 400)
    return {
        "result": _loop(ctx, "close", {"id": _iid(s), "reason": reason}),
        "say": "Closed.",
        "what": f"closed: {one_line(reason, 120)}",
    }


def _after(ctx, iid: str | None) -> dict:
    """The idea as the verb left it: what the result says is what happened, not what was
    asked (a publish that held again says so)."""
    try:
        return ctx.app.loop().ledger.get(iid) or {} if iid else {}
    except Exception:  # noqa: BLE001 - no loop: say what was asked
        return {}


def _held_again(i: dict) -> str:
    from .loop.gate import prepush_holds

    names = prepush_holds(i.get("reason"))
    return ", ".join(CHECK_WORDS.get(k, k) for k in names) or one_line(i.get("reason"), 160)


def r_publish(ctx, f, s):
    """Publish a ready build: a private repository with a release (the owner's press)."""
    iid = _iid(s)
    r = _loop(ctx, "publish", {"id": iid})
    i = _after(ctx, iid)
    url = (i.get("publish") or {}).get("url")
    if i and i.get("state") not in ("published", "public"):
        if i.get("state") == "held":
            why = _held_again(i)
            return {
                "result": r,
                "say": f"Not published: held by {why}. Nothing was pushed.",
                "what": f"held: {why}",
                "outcome": "held",
            }
        return {
            "result": r,
            "say": f"Not published yet: it is {i.get('state')}.",
            "what": f"not published: {i.get('state')}",
            "outcome": i.get("state"),
        }
    return {
        "result": r,
        "say": f"Published privately: {url}." if url else "Published privately.",
        "what": f"published privately: {url}" if url else "published privately",
        "url": url,
        "outcome": "published",
    }


def r_make_public(ctx, f, s):
    iid = _iid(s)
    r = _loop(ctx, "approve", {"id": iid})
    i = _after(ctx, iid)
    url = (i.get("publish") or {}).get("url") or (s.row or {}).get("url")
    if i and i.get("state") != "public":
        if i.get("state") == "held":
            why = _held_again(i)
            return {
                "result": r,
                "say": f"Not published: held again by {why}. Nothing was pushed.",
                "what": f"held again: {why}",
                "outcome": "held",
            }
        return {
            "result": r,
            "say": f"Not public yet: it is {i.get('state')}.",
            "what": f"not public: {i.get('state')}",
            "outcome": i.get("state"),
        }
    return {
        "result": r,
        "say": f"Public: {url}." if url else "Public.",
        "what": f"made public: {url}" if url else "made public",
        "url": url,
        "outcome": "public",
    }


def r_publish_anyway(ctx, f, s):
    reason = " ".join((f.get("reason") or "").split())
    iid, holds = _iid(s), f.get("holds")
    if holds is not None:  # what the owner saw is what it overrides
        names = [k for k, _ in anyway_plan(s)["overrides"]]
        if sorted(holds) != sorted(names):
            raise ActionError(
                "the holds changed since you looked: it would now publish over "
                f"{', '.join(names) or 'nothing'}; look again",
                refusal="holds_changed",
            )
    r = _loop(
        ctx,
        "publish-anyway",
        {
            "id": iid,
            "reason": reason,
            "yes": ctx.confirm,
            **({"holds": holds} if holds is not None else {}),
        },
    )
    i = _after(ctx, iid)
    if i and i.get("state") != "published":
        why = _held_again(i)
        return {
            "result": r,
            "say": f"Not published: held again by {why}. Nothing was pushed.",
            "what": f"publish anyway held again: {why}",
            "outcome": "held",
        }
    url = (i.get("publish") or {}).get("url")
    return {
        "result": r,
        "say": f"Published privately over the hold{': ' + url if url else ''}. "
        "Publish makes it public.",
        "url": url,
        "outcome": "published",
        "what": f"published privately over a hold{': ' + url if url else ''} "
        f"({one_line(reason, 120)})",
    }


def r_keep(ctx, f, s):
    return {
        "result": _loop(ctx, "keep", {"id": _iid(s)}),
        "say": "Kept private.",
        "what": "kept private",
    }


def r_reject(ctx, f, s):
    row = s.row or {}
    reasons, note = list(f.get("reasons") or []), one_line(f.get("note"))
    if row and not row.get("idea"):
        return {
            "result": ctx.app.verdict(Path(row["dir"]), "reject", note),
            "say": "Rejected.",
            "what": "rejected",
        }
    if not reasons and not note:
        raise ActionError("say why: pick a reason, or write a few words", 400)
    return {
        "result": _loop(
            ctx, "verdict", {"id": _iid(s), "verdict": "reject", "reasons": reasons, "note": note}
        ),
        "say": "Rejected.",
        "what": "rejected: " + ", ".join(reasons + ([note] if note else [])),
    }


def r_later(ctx, f, s):
    days = int(f.get("days") or 7)
    r = _loop(ctx, "later", {"id": _iid(s), "days": days})
    return {"result": r, "say": f"Put off for {days} days.", "what": f"put off for {days} days"}


def r_edit_brief(ctx, f, s):
    return {
        "result": _loop(ctx, "edit", {"id": _iid(s), "text": f.get("text")}),
        "say": "Brief saved.",
        "what": "saved the brief",
    }


def r_answer(ctx, f, s):
    row = s.row or {}
    return {
        "result": ctx.app.answer(Path(row["dir"]), str(f.get("qid") or ""), f.get("text") or ""),
        "say": "Answer sent. The build continues.",
        "what": "answered the build's question",
    }


def r_stop(ctx, f, s):
    return {
        "result": ctx.app.stop(Path((s.row or {})["dir"])),
        "say": "Stopping.",
        "what": "asked the build to stop",
    }


def _control_say(ctx, iid, gone: str, going: str) -> tuple[str, str]:
    """``(say, outcome)``: what the press did, from the idea as the verb left it."""
    i = _after(ctx, iid)
    if i.get("state") == "building":
        return going, "stopping"
    return gone.format(state=i.get("state") or "as it was"), i.get("state") or "done"


def _undoable(ctx, iid, kind: str) -> dict:
    """What the flash's Undo needs: the control, the round and when the offer ends."""
    i = _after(ctx, iid)
    u = (ctx.app.loop().ledger.meta(f"undo:{iid}") or {}) if i else {}
    b = i.get("build") or {}
    rnd = (
        (b.get("kill") or {}).get("round")
        or b.get("round")
        or (b.get("stopped") or {}).get("round")
        or (u.get("round"))
    )
    return {"of": kind, "until": u.get("until"), "round": rnd} if u.get("of") == kind else {}


def r_stop_round(ctx, f, s):
    iid = _iid(s)
    rnd = (s.row or {}).get("round")
    r = _loop(ctx, "stop-round", {"id": iid})
    say, out = _control_say(
        ctx,
        iid,
        "Stopped: the round is kept; it is {state} as it was before the round.",
        "Stopping… its workers are being stopped; it goes back to how it was before the round.",
    )
    u = _undoable(ctx, iid, "stop_round")
    u["round"] = u.get("round") or rnd
    return {"result": r, "say": say, "outcome": out, "what": "stop round: " + out, "undoable": u}


def r_undo(ctx, f, s):
    iid = _iid(s)
    r = _loop(ctx, "undo", {"id": iid, **({"of": f["of"]} if f.get("of") else {})})
    st = _after(ctx, iid).get("state")
    say = {
        "queued": "Undone: it is queued again, as it was.",
        "building": "Undone: once its workers are gone, the round continues.",
    }.get(st, f"Undone: it is {st}.")
    return {"result": r, "say": say, "outcome": st, "what": f"undo: {st}"}


def r_resume_build(ctx, f, s):
    iid = _iid(s)
    return {
        "result": _loop(ctx, "resume-build", {"id": iid}),
        "outcome": "queued",
        "say": "Queued: it continues its round when a build slot is free.",
        "what": "resumed: queued",
    }


def r_cancel(ctx, f, s):
    iid = _iid(s)
    r = _loop(ctx, "cancel", {"id": iid})
    st = _after(ctx, iid).get("state") or "cancelled"
    word = {"new": "back in Ideas"}.get(st, f"{st} as it was")
    return {
        "result": r,
        "say": f"Cancelled before it started: {word}.",
        "outcome": st,
        "what": f"cancelled: {st}",
        "undoable": _undoable(ctx, iid, "cancel"),
    }


def r_restart_revise(ctx, f, s):
    iid, mode = _iid(s), f.get("mode") or "auto"
    r = _loop(ctx, "restart", {"id": iid, "text": f.get("instructions"), "mode": mode})
    say, out = _control_say(
        ctx,
        iid,
        "Restarted: the new revise round is {state}.",
        "Stopping the round… the new one is asked for once it is gone.",
    )
    return {
        "result": r,
        "say": say,
        "outcome": out,
        "what": f"restart ({mode}): {one_line(f.get('instructions'), 120)}",
    }


def r_cut_ticket(ctx, f, s):
    row = s.row or {}
    labels = list(f.get("labels") or [])
    r = ctx.app.maintain_issue_create(
        {"repo": row["repo"], "title": f.get("issue"), "body": f.get("text"), "labels": labels}
    )
    said = f"Opened #{r['number']} on {row['repo']}: {r['url']}"
    if r.get("dropped_go"):
        said += (
            " (without go: this repository's .github/repo-factory.yml does not let repo-factory fix "
            "issues)"
        )
    return {
        "result": {"id": r["number"], "out": r["url"], **r},
        "say": said + ".",
        "what": f"opened #{r['number']} on {row['repo']}",
        "url": r["url"],
    }


def r_revise(ctx, f, s):
    mode = f.get("mode") or "auto"
    r = _loop(ctx, "revise", {"id": _iid(s), "text": f.get("instructions"), "mode": mode})
    return {
        "result": r,
        "say": "Queued: a revise round runs when no other build is running; "
        "the gate and the pre-push checks run again.",
        "what": f"asked for changes ({mode}): {one_line(f.get('instructions'), 120)}",
    }


def w_recheck(s):
    st = _istate(s)
    row = s.row or {}
    if not row.get("idea"):
        return "only a loop build is re-checked here"
    if st not in ("held", "ready") or row.get("url"):
        return "only a held or ready build is re-checked"
    if not (((row.get("gate") or {}).get("checks") or {}).get("delivered") or {}).get("ok"):
        return "its build delivered nothing to check"
    if ((row.get("gate") or {}).get("recheck") or {}).get("state") == "running":
        return "a re-check is running"
    return None


def r_recheck(ctx, f, s):
    r = _loop(ctx, "recheck", {"id": _iid(s)})
    return {
        "result": r,
        "say": "Re-checking with the installed repo-factory in the background; the "
        "build page shows what differs. If it only turns holds into passes, that becomes "
        "the verdict; otherwise the build's state does not change.",
        "what": "started a re-check with the installed repo-factory",
    }


# -- Claims: what a claim means, read at build start; none waits on a model ------------------
def _claims(s: Subject) -> dict:
    """The row's Claims (``App.claims_of``), else the idea's pitch."""
    c = (s.row or {}).get("claims")
    if c:
        return c
    from .loop import claims

    idea = s.idea or {}
    return claims.view(idea) if (idea.get("pitch") or {}).get("product") else {}


def _claim_live(s: Subject) -> str | None:
    if not _iid(s):
        return "only a loop build's claims are read here"
    if _istate(s) in ("new", "rejected", "error", "skipped", "closed") or not _claims(s):
        return "only a pitched build has claims"
    return None


def _claim_open(s: Subject) -> str | None:
    """Claims are read at build start, where they shape what gets built, and a published
    project's charter follows them: never between a build and
    its publish (``claims.open_for``)."""
    why = _claim_live(s)
    if why:
        return why
    from .loop.claims import open_for

    if open_for({**(s.idea or {}), "state": _istate(s)}):
        return None
    return "claims are read when the build starts; this build is between its build and its publish"


def w_accept_claim(s):
    why = _claim_open(s)
    if why:
        return why
    cs = _claims(s).get("claims") or []
    if not cs:
        return "no claim of this idea needed a reading"
    if all(c.get("status") == "accepted" for c in cs):
        return "every claim's reading is accepted"
    return None


def w_edit_claim(s):
    why = _claim_open(s)
    if why:
        return why
    return None if _claims(s).get("claims") else "no claim of this idea needed a reading"


def w_edit_release_text(s):
    return _claim_live(s)


def r_accept_claim(ctx, f, s):
    r = _loop(ctx, "claim", {"id": _iid(s), "claim": f.get("claim"), "accept": True})
    return {
        "result": r,
        "say": "Accepted. The build reads the claim this way; a build held for it is queued.",
        "what": f"accepted claim {f.get('claim')}'s reading",
    }


def r_edit_claim(ctx, f, s):
    r = _loop(
        ctx,
        "claim",
        {
            "id": _iid(s),
            "claim": f.get("claim"),
            "text": f.get("text"),
            "default": f.get("default"),
        },
    )
    return {
        "result": r,
        "say": "Saved. The build reads the claim this way.",
        "what": f"edited claim {f.get('claim')}: {one_line(f.get('text'), 120)}",
    }


def r_edit_release_text(ctx, f, s):
    r = _loop(ctx, "release-text", {"id": _iid(s), "text": f.get("text")})
    return {
        "result": r,
        "say": "Saved. It is used as it is: the repo's description and the "
        "release notes' first line.",
        "what": f"set the release text: {one_line(f.get('text'), 120)}",
    }


# -- maintenance (off and unlisted): the repo is a field, the subject any thread ---------------
def w_repo(s):
    """The repo actions are checked by the ``App`` method behind them; the page shows its
    refusal in words."""
    return None


def r_maintain_merge_policy(ctx, f, s):
    r = ctx.app.maintain_merge_policy(
        f["repo"], bool(f.get("on")), list(f.get("kinds") or []), origin=ctx.origin
    )
    return {
        "result": {"id": r["repo"]},
        "say": r["say"],
        "what": f"started the merge policy PR of {r['repo']}",
    }


def r_maintain_recheck(ctx, f, s):
    r = ctx.app.maintain_recheck(f["repo"], origin=ctx.origin)
    return {"result": {"id": r["repo"]}, "say": r["say"], "what": f"re-checked {r['repo']}"}


# -- the table -------------------------------------------------------------------------------
TEXT = Field("text", "text", max=MAX_TEXT)
ACTIONS: dict[str, Action] = {}


def _add(a: Action) -> None:
    ACTIONS[a.kind] = Action(**{**a.__dict__, "order": len(ACTIONS)})


_add(
    Action(
        "add_idea",
        "Add task",
        "Adds the task; its brief is written on the loop's next tick.",
        ("new",),
        (Field("text", "text", True, MAX_TEXT),),
        "click",
        None,
        w_add_idea,
        r_add_idea,
        "repo-factory loop add --source ui --file -",
    )
)
_add(
    Action(
        "send_to_build",
        "Build",
        "Sends it to build: the brief is written, then the build starts within about a minute.",
        ("new", "idea"),
        (TEXT,),
        "click",
        "b",
        w_send_to_build,
        r_send_to_build,
        "repo-factory loop send {idea}",
    )
)
_add(
    Action(
        "build_anyway",
        "Build anyway",
        "Builds it from the brief's product name and brief, although the brief passed on it.",
        ("idea",),
        (),
        "confirm",
        None,
        w_build_anyway,
        r_build_anyway,
        "repo-factory loop send {idea} --build-anyway",
    )
)
_add(
    Action(
        "pitch_again",
        "Write the brief again",
        "Writes its brief once more, on the loop's next tick.",
        ("idea",),
        (),
        "click",
        None,
        w_pitch_again,
        r_pitch_again,
        "repo-factory loop send {idea}",
    )
)
_add(
    Action(
        "retry",
        "Retry",
        "Queues the build again: it resumes its last round if that was "
        "saved, else it builds afresh.",
        ("idea", "build"),
        (Field("note", "line", max=MAX_NOTE),),
        "click",
        "r",
        w_retry,
        r_retry,
        "repo-factory loop retry {idea}",
    )
)
_add(
    Action(
        "close",
        "Close",
        "Closes it with your reason. A running build is stopped; it leaves Needs you.",
        ("idea", "build"),
        (Field("reason", "line", True, MAX_NOTE, ask=True),),
        "confirm",
        None,
        w_close,
        r_close,
        'repo-factory loop close {idea} --reason "{reason}"',
    )
)
_add(
    Action(
        "publish",
        "Publish",
        "Publishes the ready build as a private GitHub "
        "repository with a release, after you confirm. Nothing leaves this machine until "
        "you confirm.",
        ("idea", "build"),
        (),
        "confirm",
        "p",
        w_publish,
        r_publish,
        "repo-factory loop publish {idea}",
    )
)
_add(
    Action(
        "make_public",
        "Make public",
        "Makes the private repository public, after you confirm.",
        ("idea", "build"),
        (),
        "confirm",
        None,
        w_make_public,
        r_make_public,
        "repo-factory loop approve {idea}",
    )
)
_add(
    Action(
        "keep",
        "Keep private",
        "Keeps the published repo private: it leaves Needs you, "
        "and Publish can still make it public later.",
        ("idea", "build"),
        (),
        "click",
        None,
        w_keep,
        r_keep,
        "repo-factory loop keep {idea}",
    )
)
_add(
    Action(
        "reject",
        "Reject",
        "Says no to the build with your reasons: it is closed, and "
        "the reasons stay in its history.",
        ("build",),
        (Field("reasons", "list", choices=REJECT_CHIPS), Field("note", "line", max=MAX_NOTE)),
        "click",
        "r",
        w_reject,
        r_reject,
        "repo-factory loop reject {idea}",
    )
)
_add(
    Action(
        "later",
        "Later",
        "Puts an open task off: no brief and not waiting for you, for some days.",
        ("idea",),
        (Field("days", "int", min=1, max=365),),
        "click",
        "l",
        w_later,
        r_later,
        "repo-factory loop later {idea} --days N",
    )
)
_add(
    Action(
        "edit_brief",
        "Save brief",
        "Replaces the idea's text with this brief (before a build starts, the task's spec.md too).",
        ("idea",),
        (Field("text", "text", True, MAX_TEXT),),
        "click",
        None,
        w_edit_brief,
        r_edit_brief,
        "repo-factory loop edit {idea} --file -",
    )
)
_add(
    Action(
        "answer",
        "Answer",
        "Answers the question the build is asking; empty lets it use its judgement.",
        ("build",),
        (Field("qid", "line", True, 80), TEXT),
        "click",
        None,
        w_answer,
        r_answer,
        "repo-factory answer {task} {qid}",
    )
)
_add(
    Action(
        "stop",
        "Stop",
        "Stops a build started by hand: its workers are killed.",
        ("build",),
        (),
        "confirm",
        None,
        w_stop,
        r_stop,
        "repo-factory loop stop-round",
    )
)
# one loop build's controls: each press returns at once; the kill (SIGTERM, then SIGKILL
# after build.kill_grace_s) touches only this build's processes
_add(
    Action(
        "stop_round",
        "Stop round",
        "Stops this build's round now: its workers are killed "
        "(nothing else), and it goes back to how it was before the round (held or ready, "
        "with that round's gate). The stopped round is kept; only Resume build continues "
        "it.",
        ("idea", "build"),
        (),
        "confirm",
        "s",
        w_stop_round,
        r_stop_round,
        "repo-factory loop stop-round {idea}",
    )
)
_add(
    Action(
        "resume_build",
        "Resume build",
        "Queues a round you stopped to continue where it stopped when a build slot is free.",
        ("idea", "build"),
        (),
        "click",
        "z",
        w_resume_build,
        r_resume_build,
        "repo-factory loop resume-build {idea}",
    )
)
_add(
    Action(
        "cancel",
        "Cancel build",
        "Withdraws a queued build before it starts: a queued revise "
        "goes back to how it was before you asked, a new build goes back to its task. Nothing "
        "runs, so nothing is killed.",
        ("idea", "build"),
        (),
        "confirm",
        "s",
        w_cancel,
        r_cancel,
        "repo-factory loop cancel {idea}",
    )
)
_add(
    Action(
        "restart_revise",
        "Restart with new instructions",
        "Stops this revise round and "
        "asks for changes again with your new instructions, in one step.",
        ("idea", "build"),
        (
            Field(
                "instructions",
                "text",
                True,
                MAX_TEXT,
                20,
                help="what should change now (starts from the running round's)",
            ),
            Field(
                "mode",
                "enum",
                choices=("auto", "docs", "code"),
                help="docs: only wording is wrong; code: code and docs; auto: repo-factory picks",
            ),
        ),
        "confirm",
        None,
        w_restart_revise,
        r_restart_revise,
        "repo-factory loop restart {idea} --file - --mode {mode}",
    )
)
_add(
    Action(
        "cut_ticket",
        "Open issue",
        "Opens an issue on the product's GitHub repository "
        "with these labels. With go, repo-factory picks it up on its next poll and proposes a fix "
        "as a pull request; nothing merges until you merge it.",
        ("idea", "build"),
        (
            Field("issue", "line", True, MAX_ISSUE_TITLE),
            Field("text", "text", True, MAX_TEXT),
            Field("labels", "list", True, choices=TICKET_LABELS),
        ),
        "click",
        None,
        w_cut_ticket,
        r_cut_ticket,
        "POST repos/{repo}/issues",
    )
)
_add(
    Action(
        "recheck",
        "Re-check with current repo-factory",
        "Runs the installed repo-factory's checks on "
        "the build's delivered tree (its tests, the end-to-end checks, the scans; no "
        "model) and shows where they differ from the version that built it. When it only "
        "turns holds into passes, that is the build's verdict; otherwise it changes nothing "
        "about the build.",
        ("idea", "build"),
        (),
        "click",
        None,
        w_recheck,
        r_recheck,
        "repo-factory loop recheck {idea}",
    )
)
# Claims: a task's claims are a starting point; how each is read is the owner's to accept or
# edit before the build starts. Accept may start a build it held, so it confirms. Publishing
# never asks about claims.
CLAIM = Field("claim", "line", True, 4, help="the claim's id (k1, k2, ...)")
_add(
    Action(
        "accept_claim",
        "Accept interpretation",
        "Accepts how this claim is read, "
        "before the build starts: the build uses this reading. A task held because it "
        "can't be built as stated is queued to build this way.",
        ("idea", "build"),
        (CLAIM,),
        "confirm",
        None,
        w_accept_claim,
        r_accept_claim,
        "repo-factory loop claim {idea} --accept {claim}",
    )
)
_add(
    Action(
        "edit_claim",
        "Edit interpretation",
        "Sets your own reading of this claim, and its "
        "setting's default if it has one, before the build starts.",
        ("idea", "build"),
        (
            CLAIM,
            Field("text", "line", True, 300, 3, help="how the claim is read"),
            Field("default", "line", max=80, help="the setting's default (if it has one)"),
        ),
        "click",
        None,
        w_edit_claim,
        r_edit_claim,
        "repo-factory loop claim {idea} --edit {claim} --file -",
    )
)
_add(
    Action(
        "edit_release_text",
        "Save release text",
        "Saves your one-line text about the "
        "build: the repo's description and the release notes' first line, used as you "
        "wrote it (repo-factory neither rewrites nor checks it). A published build's repo is "
        "updated in the background.",
        ("idea", "build"),
        (Field("text", "line", True, 300, 20),),
        "click",
        None,
        w_edit_release_text,
        r_edit_release_text,
        "repo-factory loop release-text {idea} --file -",
    )
)
_add(
    Action(
        "revise",
        "Ask for changes",
        "Asks repo-factory to change a held or ready build that "
        "has no repo yet: a revise round of the build runs your instructions when no other "
        "build is running, then the gate and the pre-push checks run again. docs changes "
        "Markdown only; code changes code and docs. No cap is set.",
        ("idea", "build"),
        (
            Field(
                "instructions",
                "text",
                True,
                MAX_TEXT,
                20,
                help="what should change, exactly: file, line, what to say or fix",
            ),
            Field(
                "mode",
                "enum",
                choices=("auto", "docs", "code"),
                help="docs: only wording is wrong; code: code and docs; auto: repo-factory picks",
            ),
        ),
        "click",
        "a",
        w_revise,
        r_revise,
        "repo-factory loop revise {idea} --file - --mode {mode}",
    )
)
_add(
    Action(
        "publish_anyway",
        "Publish despite the holds",
        "Publishes a held build over its holds, as a "
        "private repo, with your reason; its NOTICE and release notes say which checks did "
        "not pass. Security, secrets, nothing delivered and a product listening beyond "
        "loopback are never overridden. Making it public is the separate Publish step.",
        ("idea", "build"),
        (
            Field("reason", "line", True, 300, 12, ask=True),
            Field(
                "holds",
                "list",
                help="the holds you were shown (the dialog's list): refused when they changed",
            ),
        ),
        "confirm",
        None,
        w_publish_anyway,
        r_publish_anyway,
        'repo-factory loop publish-anyway {idea} --reason "{reason}" --yes',
    )
)
REPO = Field("repo", "line", True, 300, help="owner/name, or its GitHub address")
# a read, so no confirm: what the rows say about a repo (its drift, its project's) made current
_add(
    Action(
        "maintain_recheck",
        "Re-check",
        "Reads one maintained repository again now: main's "
        "latest commit, AGENTS.md against the tree and the README, and its project's drift. "
        "It runs in the background and changes nothing on GitHub; the row shows the result "
        "and its age. Every poll already does this when main moves.",
        ("new",),
        (REPO,),
        "click",
        None,
        w_repo,
        r_maintain_recheck,
        "repo-factory maintain recheck {repo}",
    )
)
# the owner's switch: repo-factory merging its own fixes on green CI is the repo's policy, so
# the switch is one small pull request on .github/repo-factory.yml that the owner merges; repo-factory never
# merges a .github/ change
_add(
    Action(
        "maintain_merge_policy",
        "Merge repo-factory's fixes when CI is green…",
        "Opens one small "
        "pull request on a product's .github/repo-factory.yml that turns repo-factory's merging of its own "
        "fix pull requests on (merge.factory_patch: ci-green, for the kinds you choose) or "
        "off. You merge it on GitHub; repo-factory never merges a .github/ change. Once it is on, "
        "repo-factory merges one fix a poll, oldest first, only when every check on it is green, "
        "it is up to date with main and under merge.max_lines. Never a feature, a change "
        "to .github/, a licence or a lockfile, one labelled hold or needs-you, one with "
        "changes requested, or one you pushed to.",
        ("new",),
        (
            REPO,
            Field("on", "bool", True, help="true: ci-green; false: never"),
            Field(
                "kinds",
                "list",
                False,
                4,
                choices=("bug", "docs", "security", "dependency"),
                help="what repo-factory may merge (default bug, docs); feature never",
            ),
        ),
        "confirm",
        None,
        w_repo,
        r_maintain_merge_policy,
        "repo-factory maintain merge-policy {repo} --on --kinds bug,docs --detach --yes",
    )
)
REPO_KINDS = ("maintain_recheck", "maintain_merge_policy")


# -- projects (off and unlisted): the project is a field, as the repo is; the App method behind each
# is the authority. None waits on a model: a draft or a run starts in the background.
def w_project(s):
    return None


def r_edit_charter(ctx, f, s):
    r = ctx.app.charter_edit(f["project"], f["charter"], f.get("note") or "", origin=ctx.origin)
    return {
        "result": {"id": r["id"]},
        "say": f"Saved: charter v{r['charter_v']}. The next check for ideas reads it.",
        "what": f"edited the charter of {r['name']} (v{r['charter_v']})",
    }


def r_redraft_charter(ctx, f, s):
    r = ctx.app.charter_redraft(f["project"], origin=ctx.origin)
    return {
        "result": {"id": r["id"]},
        "say": f"Drafting {r['name']}'s charter again from the "
        "repo, in the background; your versions stay under Versions.",
        "what": f"asked for a new draft of {r['name']}'s charter",
    }


def r_restore_charter(ctx, f, s):
    r = ctx.app.charter_restore(f["project"], int(f["v"]), origin=ctx.origin)
    return {
        "result": {"id": r["id"]},
        "say": f"Charter v{f['v']} is in use again, as v{r['charter_v']}.",
        "what": f"restored charter v{f['v']} of {r['name']}",
    }


PROJECT = Field("project", "line", True, 9, 9, help="the project's id (p…)")
_add(
    Action(
        "edit_charter",
        "Edit charter",
        "Saves your text of the project's charter as a "
        "new version. Earlier versions stay, and any can be used again. Nothing runs; the "
        "next check for ideas reads it.",
        ("new",),
        (
            PROJECT,
            Field(
                "charter", "text", True, 12000, 40, help="the charter's text, one heading per field"
            ),
            Field("note", "line", max=80),
        ),
        "click",
        None,
        w_project,
        r_edit_charter,
        "repo-factory projects charter {project} --file -",
    )
)
_add(
    Action(
        "redraft_charter",
        "Redraft charter",
        "Drafts the charter again from the repo as it "
        "is now (one model call, in the background). It becomes a new version; your edits "
        "stay in the earlier versions.",
        ("new",),
        (PROJECT,),
        "click",
        None,
        w_project,
        r_redraft_charter,
        "repo-factory projects charter {project}",
    )
)
_add(
    Action(
        "restore_charter",
        "Use this version",
        "Makes an earlier version of the charter the current one again, as a new version.",
        ("new",),
        (PROJECT, Field("v", "int", True, min=1, max=10**6)),
        "click",
        None,
        w_project,
        r_restore_charter,
        "repo-factory projects charter {project} --restore {v}",
    )
)
PROJECT_KINDS = ["edit_charter", "redraft_charter", "restore_charter"]


def r_evolve_now(ctx, f, s):
    r = ctx.app.evolve_now(f["project"], origin=ctx.origin)
    return {
        "result": {"id": r["id"]},
        "say": f"Checking {r['name']} for ideas in the background; the page shows what it finds.",
        "what": f"asked for ideas on {r['name']}",
    }


def r_accept_change(ctx, f, s):
    r = ctx.app.change_accept(f["change"], origin=ctx.origin)
    filed = r.get("filed") or {}
    where = f" {filed.get('where')}" if filed.get("where") else ""
    until = ((r.get("card") or {}).get("undo") or {}).get("until")
    return {
        "result": {"id": r["id"]},
        "url": filed.get("url"),
        "say": f"Accepted: {r['title']}."
        + (f" Filed{where}." if filed else " It is filed on the loop's next tick."),
        "what": f"accepted {r['title']}",
        **(
            {"undoable": {"of": "accept_change", "until": until, "change": r["id"]}}
            if until
            else {}
        ),
    }


def r_decline_change(ctx, f, s):
    r = ctx.app.change_decline(f["change"], f["how"], f.get("why") or "", origin=ctx.origin)
    return {
        "result": {"id": r["id"]},
        "say": "Not now: it sleeps a while."
        if f["how"] == "not_now"
        else "Declined for good: a later check that proposes it again is counted, not shown.",
        "what": f"declined ({f['how']}): {r['title']}",
    }


def r_charter_change(ctx, f, s):
    yes = f["answer"] == "accept"
    r = ctx.app.change_charter(f["change"], yes, f.get("why") or "", origin=ctx.origin)
    return {
        "result": {"id": r["id"]},
        "say": "Accepted: the charter has a new version and the change is filed."
        if yes
        else "Declined for good.",
        "what": f"{'accepted' if yes else 'declined'} the scope change {r['title']}",
    }


CHANGE = Field("change", "line", True, 9, 9, help="the change idea's id (c…)")
_add(
    Action(
        "evolve_now",
        "Check for ideas now",
        "Asks what can improve this project now (one "
        "model call, in the background), even when nothing changed since the last check.",
        ("new",),
        (PROJECT,),
        "click",
        None,
        w_project,
        r_evolve_now,
        "repo-factory projects evolve {project} --force",
    )
)
_add(
    Action(
        "accept_change",
        "Accept: repo-factory fixes it",
        "Accepts a proposed change: its issue is filed on the "
        "repo (with go when the repo's policy lets repo-factory fix it), or a request is kept in "
        "repo-factory for a repo you do not own, and the fix opens a draft pull request. The merge "
        "stays yours.",
        ("new",),
        (CHANGE,),
        "click",
        None,
        w_project,
        r_accept_change,
        "repo-factory projects accept {change}",
    )
)
_add(
    Action(
        "decline_change",
        "Decline",
        "Not now puts a proposed change to sleep for a while; "
        "Never declines it for good, with your reason, so it does not come back as new.",
        ("new",),
        (
            CHANGE,
            Field("how", "enum", True, choices=("not_now", "never")),
            Field("why", "line", max=MAX_NOTE),
        ),
        "click",
        None,
        w_project,
        r_decline_change,
        "repo-factory projects decline {change} --how {how}",
    )
)
_add(
    Action(
        "charter_change",
        "Decide",
        "Answers a change that would change what "
        "the project is: Accept writes the charter's new version and files the change; "
        "Decline declines it for good with your reason.",
        ("new",),
        (
            CHANGE,
            Field("answer", "enum", True, choices=("accept", "decline")),
            Field("why", "line", max=MAX_NOTE),
        ),
        "confirm",
        None,
        w_project,
        r_charter_change,
        "repo-factory projects scope {change} --accept|--decline",
    )
)
PROJECT_KINDS += ["evolve_now", "accept_change", "decline_change", "charter_change"]


def r_undo_change(ctx, f, s):
    r = ctx.app.change_undo(f["change"], origin=ctx.origin)
    return {
        "result": {"id": r["id"]},
        "say": f"Undone: {r['title']} is a proposal again; its issue is closed unworked.",
        "what": f"undid the accept: {r['title']}",
    }


_add(
    Action(
        "undo_change",
        "Undo",
        "Undoes Accept: repo-factory fixes it within a few seconds of it: the issue just "
        "filed is closed unworked (or the request withdrawn) and the idea is proposed "
        "again.",
        ("new",),
        (CHANGE,),
        "click",
        None,
        w_project,
        r_undo_change,
        "repo-factory projects undo {change}",
    )
)
PROJECT_KINDS.append("undo_change")


def r_pause_project(ctx, f, s):
    r = ctx.app.project_state(f["project"], f["state"], origin=ctx.origin)
    word = {"active": "Resumed", "paused": "Paused", "archived": "Archived"}[r["state"]]
    return {
        "result": {"id": r["id"]},
        "say": f"{word}: {r['name']}."
        + (
            " No check for ideas, charter draft or filing runs until you resume it; maintenance "
            "of its repo goes on (Pause maintenance stops it)."
            if r["state"] != "active"
            else ""
        ),
        "what": f"{word.lower()} {r['name']}",
    }


def r_pause_maintenance(ctx, f, s):
    paused = f["state"] == "paused"
    r = ctx.app.maintenance_state(f["project"], paused, origin=ctx.origin)
    if paused:
        say = (
            f"Maintenance paused on {r['name']}: no fix, merge, release, label, comment or "
            "model call on its repo until you resume it"
            + (
                f"; the running fix of #{r['running']} goes on (Stop round on its row stops it)"
                if r.get("running")
                else ""
            )
            + "."
        )
    else:
        say = f"Maintenance resumed on {r['name']}: it picks up from the next poll."
    return {
        "result": {"id": r["id"]},
        "say": say,
        "what": f"{'paused' if paused else 'resumed'} maintenance on {r['name']}",
    }


_add(
    Action(
        "pause_project",
        "Pause project",
        "Pauses a project's ideas (no check for ideas, no "
        "charter draft, nothing filed), resumes it, or archives it (it leaves the list; its "
        "record stays). Maintenance of its repo goes on: Pause maintenance stops that.",
        ("new",),
        (PROJECT, Field("state", "enum", True, choices=("active", "paused", "archived"))),
        "click",
        None,
        w_project,
        r_pause_project,
        "repo-factory projects state {project} {state}",
    )
)
PROJECT_KINDS.append("pause_project")
# the owner, 2026-10-29: pause maintenance on one project, in repo-factory, at once (maintain/pauses.py)
_add(
    Action(
        "pause_maintenance",
        "Pause maintenance",
        "Pauses maintenance of a project's repo "
        "in repo-factory, at once: no fix run, merge, release, label, comment or model call on it "
        "until you resume it (a fix already running goes on). No pull request; the repo's "
        ".github/repo-factory.yml is left as it is. state on resumes it from the next poll.",
        ("new",),
        (PROJECT, Field("state", "enum", True, choices=("paused", "on"))),
        "click",
        None,
        w_project,
        r_pause_maintenance,
        "repo-factory projects maintenance {project} {state}",
    )
)
PROJECT_KINDS.append("pause_maintenance")
# the flash's Undo after Stop round or Cancel: nothing is killed or asked
_add(
    Action(
        "undo",
        "Undo",
        "Undoes the last Stop round or Cancel of this build, within a few "
        "seconds of it: a stopped round continues where it was, a "
        "cancelled build is queued again as it was.",
        ("idea", "build"),
        (
            Field(
                "of", "enum", choices=UNDOABLE, help="the control to undo (default: the last one)"
            ),
        ),
        "click",
        None,
        w_undo,
        r_undo,
        "repo-factory loop undo {idea}",
    )
)


# -- repo-factory's own GitHub App (off and unlisted): the page and the command line; each a
# background job the page polls (api/github/app), so no request waits on GitHub
def w_github(s):
    from . import github_app as G

    if s.kind != "loop":
        return "repo-factory's GitHub App is factory-wide: no idea or build"
    return G.available()


def _github_call(fn, *a, **kw):
    from .github_app import AppError

    try:
        return fn(*a, **kw)
    except AppError as e:
        raise ActionError(str(e), 400 if e.kind in ("host", "code", "key") else 409) from None


def r_github_app_create(ctx, f, s):
    """Start one creation: the state and where the browser must come back, then what the
    page posts to GitHub (the manifest, as GitHub's form expects it)."""
    from . import github_app as G

    host = _github_call(G.check_host, f["host"])
    port = f.get("port")
    mount = str(f.get("mount") or "/")  # where the page is served from; "/": the root
    mount = "" if mount == "/" else mount.rstrip("/")
    if mount and not re.fullmatch(r"/[A-Za-z0-9_-]{1,40}", mount):
        raise ActionError("mount is the page's path prefix, or / for the root", 400)
    store = G.Store()
    if store.state() in ("created", "installed"):
        raise ActionError(
            "repo-factory already has a GitHub App: remove it first (Settings › "
            "GitHub › Remove repo-factory's app) to make another"
        )
    login = None
    try:
        login = ctx.app.loop().cfg["publish"].get("owner") or ctx.app.loop().gh.login()
    except Exception:  # noqa: BLE001 - no login: GitHub fills it
        login = None
    state = store.pending_write(host, port)
    m = G.manifest(host, port, login, mount)
    # result.form: what the page posts to GitHub (api/act passes "form" through)
    return {
        "result": {
            "id": state,
            "out": G.post_url(state, f.get("org") or None),
            "form": {
                "post_url": G.post_url(state, f.get("org") or None),
                "manifest": m,
                "state": state,
            },
        },
        "say": f"Opening GitHub to create {m['name']}…",
        "what": f"started creating repo-factory's GitHub App {m['name']} (redirect to {host})",
    }


def r_github_app_cancel(ctx, f, s):
    from . import github_app as G

    had = G.Store().cancel()
    return {
        "result": {"id": "cancelled" if had else None},
        "say": (
            "Stopped waiting for GitHub. If you did click Create GitHub App there, link "
            "that app here instead of making another."
        )
        if had
        else "repo-factory was not waiting for GitHub",
        "what": "stopped waiting for GitHub to create repo-factory's GitHub App"
        if had
        else "nothing to cancel",
    }


def r_github_app_link(ctx, f, s):
    from . import github_app as G

    j = _github_call(G.start_link, f["app_id"], f["pem"])
    return {
        "result": {"id": j.get("what")},
        "say": "Checking the app and its key with GitHub…",
        "what": f"started linking GitHub App {f['app_id']} as repo-factory's GitHub App",
    }


def r_github_app_refresh(ctx, f, s):
    from . import github_app as G

    j = _github_call(G.start_refresh)
    return {
        "result": {"id": j.get("what")},
        "say": "Reading the installation back…",
        "what": "started reading repo-factory's GitHub App installation back",
    }


def r_github_app_key(ctx, f, s):
    from . import github_app as G

    j = _github_call(G.start_key, f["pem"])
    return {
        "result": {"id": j.get("what")},
        "say": "Checking the new key with GitHub…",
        "what": "started replacing repo-factory's GitHub App key",
    }


def r_github_app_remove(ctx, f, s):
    from . import github_app as G

    j = _github_call(G.start_remove)
    return {
        "result": {"id": j.get("what")},
        "say": "Removing repo-factory's app: it is uninstalled on GitHub and forgotten here. The "
        "registration itself is deleted on GitHub, under the app's settings.",
        "what": "started removing repo-factory's GitHub App",
    }


_add(
    Action(
        "github_app_create",
        "Create repo-factory's GitHub App",
        "Starts creating repo-factory's own "
        "GitHub App on your account, after you confirm: GitHub shows a prefilled form "
        "(issues, pull requests and contents write; metadata, checks and actions read; "
        "no webhook), and the private key comes back to this machine only.",
        ("loop",),
        (
            Field(
                "host",
                "line",
                True,
                80,
                help="the page's host, 127.0.0.1:<port> or "
                "localhost:<port>: GitHub sends the browser "
                "back there",
            ),
            Field("port", "int", min=1, max=65535),
            Field(
                "mount",
                "line",
                max=40,
                help="the page's path prefix: / (the root) unless it is served under one",
            ),
            Field("org", "line", max=80, help="create it under an organisation instead"),
        ),
        "confirm",
        None,
        w_github,
        r_github_app_create,
        "POST https://github.com/settings/apps/new (the manifest)",
    )
)
_add(
    Action(
        "github_app_cancel",
        "Cancel",
        "Stops waiting for GitHub to send the browser back "
        "after Create: Create comes back at once. Nothing on GitHub changes; an app GitHub "
        "still sends back within ten minutes is kept.",
        ("loop",),
        (),
        "click",
        None,
        w_github,
        r_github_app_cancel,
        "repo-factory github cancel",
    )
)
_add(
    Action(
        "github_app_link",
        "Link",
        "Links a GitHub App you already made as repo-factory's GitHub "
        "App, instead of making a second one: its App ID and a private key generated under "
        "its settings, checked with GitHub before anything is kept. Nothing is created.",
        ("loop",),
        (
            Field("app_id", "line", True, 12, help="the App ID on the app's settings page"),
            Field("pem", "text", True, 8000, help="the whole .pem file GitHub downloaded"),
        ),
        "confirm",
        None,
        w_github,
        r_github_app_link,
        "repo-factory github link --app-id <id> --file <pem>",
    )
)
_add(
    Action(
        "github_app_refresh",
        "Refresh",
        "Reads repo-factory's GitHub App installation back: "
        "which repositories it reaches. No model; a few GitHub calls in the background.",
        ("loop",),
        (),
        "click",
        None,
        w_github,
        r_github_app_refresh,
        "repo-factory github refresh",
    )
)
_add(
    Action(
        "github_app_key",
        "Replace the key",
        "Replaces repo-factory's GitHub App private key "
        "with one you generated on GitHub, after it is checked there. The old key file "
        "is overwritten; delete the old key on GitHub afterwards.",
        ("loop",),
        (Field("pem", "text", True, 8000, help="the whole .pem file GitHub downloaded"),),
        "confirm",
        None,
        w_github,
        r_github_app_key,
        "repo-factory github key --file <pem>",
    )
)
_add(
    Action(
        "github_app_remove",
        "Remove repo-factory's app…",
        "Uninstalls repo-factory's GitHub App from "
        "your account and deletes its key and record here. Maintenance acts as your gh "
        "login again from the next poll.",
        ("loop",),
        (),
        "confirm",
        None,
        w_github,
        r_github_app_remove,
        "repo-factory github remove",
    )
)


def w_github_user(s):
    if s.kind != "loop":
        return "repo-factory's GitHub connection is factory-wide: no idea or build"
    return None


def _loop_cfg(ctx):
    from .settings.store import Config

    return Config(getattr(ctx.app, "loop_home", None))


def r_github_connect(ctx, f, s):
    """Start GitHub's device flow (WZ-DESIGN §3.6): the code and the page come back; the
    page then polls ``api/github/user/poll``. Nothing is written on GitHub."""
    from . import github_user as U
    from .github_app import AppError

    try:
        got = U.start(U.client_id(_loop_cfg(ctx)))
    except AppError as e:
        raise ActionError(str(e), 409) from None
    return {
        "result": got,
        "say": f"Open {got['verification_uri']} and enter {got['user_code']}",
        "what": "started connecting repo-factory to GitHub",
    }


def r_github_disconnect(ctx, f, s):
    from . import github_user as U

    had = U.Store().disconnect()
    return {
        "result": {"disconnected": had, "revoke_url": U.REVOKE_URL},
        "say": (
            "Disconnected: repo-factory acts as your gh login again. Revoke the grant on "
            f"GitHub too: {U.REVOKE_URL}"
        )
        if had
        else "repo-factory was not connected",
        "what": "disconnected repo-factory from GitHub" if had else "nothing to disconnect",
    }


_add(
    Action(
        "github_connect",
        "Connect GitHub",
        "Signs repo-factory in to your GitHub account with a "
        "code you type at github.com/login/device (scopes repo, workflow, read:org): repo-factory "
        "then publishes builds and reads the repositories you pick as you, without your gh "
        "login. Nothing on GitHub changes.",
        ("loop",),
        (),
        "click",
        None,
        w_github_user,
        r_github_connect,
        "repo-factory setup github",
    )
)
_add(
    Action(
        "github_disconnect",
        "Disconnect",
        "Deletes repo-factory's GitHub connection on this "
        "machine; repo-factory acts as your gh login again. The grant itself is revoked on GitHub "
        "(Settings › Applications).",
        ("loop",),
        (),
        "confirm",
        None,
        w_github_user,
        r_github_disconnect,
        "repo-factory github disconnect",
    )
)


def w_claude_login(s):
    if s.kind != "loop":
        return "the Claude login builds use is factory-wide: no idea or build"
    return None


def r_claude_login_keep(ctx, f, s):
    """BL: the token ``claude setup-token`` printed, kept in repo-factory's own store at 0600
    (``claude_login``); every launch then gets it by pipe. Never echoed back."""
    from . import claude_login

    try:
        claude_login.Store().keep(f["token"], how=ctx.origin)
    except claude_login.LoginError as e:
        raise ActionError(str(e), 400) from None
    return {
        "result": {},
        "say": "Kept: builds run on your Claude plan (login token).",
        "what": "kept a Claude login token for builds",
    }


def r_claude_login_forget(ctx, f, s):
    from . import claude_login

    had = claude_login.Store().forget()
    return {
        "result": {"forgotten": had},
        "say": (
            "Forgotten here. Builds use the unit's own token or Claude Code's sign-in, "
            "else they are held until you set up your plan again."
        )
        if had
        else "repo-factory kept no Claude login token",
        "what": "forgot the Claude login token" if had else "nothing to forget",
    }


_add(
    Action(
        "claude_login_keep",
        "Set up",
        "Keeps the Claude login token 'claude setup-token' "
        "printed in repo-factory's own store (0600, never shown again): builds, fixes, pitches "
        "and Open in Claude Code run on your Claude plan. It replaces a token kept before.",
        ("loop",),
        (Field("token", "line", True, 400, help="what claude setup-token printed"),),
        "click",
        None,
        w_claude_login,
        r_claude_login_keep,
        "repo-factory setup building",
    )
)
_add(
    Action(
        "claude_login_forget",
        "Forget",
        "Deletes the Claude login token repo-factory keeps. "
        "Builds then use the unit's own token or Claude Code's sign-in, else they are held "
        "until you set up your plan again.",
        ("loop",),
        (),
        "confirm",
        None,
        w_claude_login,
        r_claude_login_forget,
        "repo-factory setup building",
    )
)


# -- who may run what: the caller is the token, never the request -----------------------------
def agent_default_actions() -> tuple[str, ...]:
    """What a named agent's token may run when its ``actions`` is null: every action but the
    owner's own (``AGENT_HIDDEN``), the confirm class with ``confirm: true``."""
    return tuple(k for k in ACTIONS if k not in AGENT_HIDDEN)


OWNER_REFUSAL = (
    "{kind} is the owner's: it opens a pull request on your repository; start "
    "it from the page or the command line"
)


def door_refuses(
    origin: str, kind: str, actions: list | None = None, cfg: dict | None = None
) -> str | None:
    """Why the caller ``origin`` may not run ``kind`` over ``api/act`` (a 403), else None.
    The page and the command line run every action; an agent its token's ``actions`` (null:
    ``agent_default_actions``). The confirm class still needs ``confirm: true`` from every
    door (``run``)."""
    if origin in ("ui", "cli"):
        return None
    if AGENT_ORIGIN.fullmatch(str(origin or "")):
        if kind in GITHUB_KINDS:
            return f"{kind} is the owner's: repo-factory's GitHub App is set up from Settings › Publishing › GitHub"
        if kind in OWNER_KINDS:
            return OWNER_REFUSAL.format(kind=kind)
        if kind in CLAUDE_KINDS:
            return CLAUDE_REFUSAL.format(kind=kind)
        allowed = agent_default_actions() if actions is None else tuple(actions)
        return None if kind in allowed else f"{origin} may not run {kind}"
    return f"{origin} does not run actions over the API"


# -- the functions ---------------------------------------------------------------------------
def get(kind: str) -> Action:
    a = ACTIONS.get(kind)
    if a is None:
        raise ActionError(f"unknown action {kind!r}", 400)
    return a


def validate(kind: str, raw: dict, *, card: bool = False) -> dict:
    """The action's fields from ``raw``, checked and cleaned; ActionError (400) says why.
    ``card``: a proposal's fields, where a field the dialog asks for (``ask``) may be
    missing."""
    a = get(kind)
    raw = raw if isinstance(raw, dict) else {}
    out: dict = {}
    hook = _CHECKS.get(kind)
    for f in a.fields:
        if kind in _HOOKED and f.name != "text":
            continue  # the kind's own check reads it
        if card and f.ask:
            continue  # the dialog asks for it at the press
        v = raw.get(f.name)
        if isinstance(v, str) and f.kind in ("text", "line"):
            v = v.strip() if f.kind == "text" else " ".join(v.split())
        if v is None or v == "" or v == []:
            if f.required and not (card and f.ask) and kind not in _HOOKED:
                raise ActionError(
                    f"{kind} needs the text" if f.name == "text" else f"{kind} needs {f.name}", 400
                )
            continue
        if f.kind in ("text", "line"):
            if not isinstance(v, str):
                raise ActionError(f"{f.name} must be text", 400)
            if len(v) > f.max:
                if f.name in TRUNCATED:
                    v = one_line(v, f.max)
                else:
                    raise ActionError(f"the {f.name} is over {f.max} characters", 400)
            if f.min and len(v) < f.min:
                raise ActionError(f"the {f.name} needs at least {f.min} characters", 400)
        elif f.kind == "enum":
            if v not in f.choices:
                raise ActionError(f"{f.name} is one of " + ", ".join(f.choices), 400)
        elif f.kind == "int":
            if isinstance(v, bool):
                raise ActionError(f"{f.name} must be a whole number", 400)
            try:
                v = int(float(v))
            except (TypeError, ValueError):
                raise ActionError(f"{f.name} must be a whole number", 400) from None
            if not f.min <= v <= f.max:
                raise ActionError(f"{f.name} is from {f.min} to {f.max}", 400)
        elif f.kind == "bool":
            v = bool(v)
        elif f.kind == "list":
            if not isinstance(v, list) or any(not isinstance(x, str) for x in v):
                raise ActionError(f"{f.name} must be a list of words", 400)
            if f.choices and any(x not in f.choices for x in v):
                raise ActionError(f"{f.name} must come from " + ", ".join(f.choices), 400)
            v = list(dict.fromkeys(v))
        out[f.name] = v
    if hook:
        hook(raw, out, card)
    return out


def _check_ticket(raw, out, card):
    issue, labels = raw.get("issue"), raw.get("labels") or []
    if not out.get("text"):
        raise ActionError("cut_ticket needs the text", 400)
    if not isinstance(issue, str) or not issue.strip():
        raise ActionError("cut_ticket needs the issue's title", 400)
    if not isinstance(labels, list) or any(x not in TICKET_LABELS for x in labels):
        raise ActionError("labels must come from " + ", ".join(TICKET_LABELS), 400)
    kinds = [x for x in labels if x in ("bug", "feature", "docs", "security", "question")]
    if len(kinds) != 1 or len([x for x in labels if x in ("p1", "p2", "p3")]) > 1:
        raise ActionError("one kind and at most one priority", 400)
    out["issue"] = " ".join(issue.split())[:MAX_ISSUE_TITLE]
    out["labels"] = list(dict.fromkeys(labels))


def _check_reject(raw, out, card):
    out["reasons"] = list(out.get("reasons") or [])


_CHECKS = {"cut_ticket": _check_ticket, "reject": _check_reject}
_HOOKED = ("cut_ticket",)  # its fields are read by its own check
TRUNCATED = ("note",)  # a long note is cut to one line, not refused


def check(kind: str, subject: Subject) -> str | None:
    """None when ``kind`` can run now on ``subject``; else why not, in words."""
    a = ACTIONS.get(kind)
    if a is None:
        return "unknown action"
    if kind in REPO_KINDS or kind in PROJECT_KINDS:  # a field names it: any thread may ask
        return a.when(subject)
    # an idea and its build are one subject (both rows are resolved); a new thread is not
    if subject.kind == "new" and "new" not in a.subjects:
        return "there is no idea or build yet"
    if subject.kind in ("idea", "build") and not {"idea", "build"} & set(a.subjects):
        return "it is already a task" if "loop" not in a.subjects else None
    if subject.kind == "loop" and "loop" not in a.subjects:
        return "it needs an idea or a build"
    return a.when(subject)


def describe(a: Action) -> dict:
    return {
        "kind": a.kind,
        "title": a.title,
        "says": a.says,
        "click": a.click,
        "key": a.key,
        "fields": [
            {
                k: v
                for k, v in f.__dict__.items()
                if v not in ((), "", False) or k in ("name", "kind", "required")
            }
            for f in a.fields
        ],
    }


# The build pane's action row: the kinds it draws as buttons (an answer has
# its box in Decision; an issue and a factory issue come from Maintenance and proposals).
PAGE_ROW = (
    "publish",
    "make_public",
    "keep",
    "reject",
    "retry",
    "close",
    "stop",
    "revise",
    "publish_anyway",
    "stop_round",
    "resume_build",
    "cancel",
    "restart_revise",
)


def on_page(kind: str, s: Subject, enabled: set) -> bool:
    """Whether the build pane's action row shows ``kind`` (when it is enabled; Publish
    anyway… also hidden, with why, on a held build): one "no" per build (Reject on a build
    ready to go public, Close elsewhere), and Retry on a build started by hand only when it
    failed (a held one has Accept and Reject, or its question)."""
    row = s.row or {}
    if s.kind != "build" or kind not in PAGE_ROW:
        return False
    if kind == "close":
        return "reject" not in enabled
    if kind == "retry":
        return bool(row.get("idea")) or row.get("status") == "failed"
    if kind == "publish_anyway":
        return _istate(s) == "held"
    return True


def available(subject: Subject) -> list[dict]:
    """Every action for ``subject``'s kind, in the table's order, each with ``enabled`` and,
    when it is not, ``why``; ``page``: the build pane draws it (``on_page``):
    ``[{kind, title, says, click, key, fields, enabled, why, page}]``."""
    out = []
    for a in ACTIONS.values():
        if subject.kind not in a.subjects:
            continue
        why = a.when(subject)
        out.append({**describe(a), "enabled": why is None, "why": why})
        if a.kind == "revise" and why is None:
            out[-1]["mode"] = suggest_mode(subject)
        if a.kind == "restart_revise" and why is None:
            out[-1]["instructions"] = change_text(subject)
            out[-1]["mode"] = ((subject.row or {}).get("change") or {}).get("mode") or "auto"
        if (
            a.kind == "publish_anyway"
            and _istate(subject) == "held"
            and (subject.row or {}).get("idea")
        ):
            out[-1]["plan"] = plan_view(anyway_plan(subject))
    on = {x["kind"] for x in out if x["enabled"]}
    for x in out:
        x["page"] = on_page(x["kind"], subject, on)
    return out


def target(subject: Subject) -> str | None:
    return _iid(subject)


def run(ctx: Ctx, kind: str, fields: dict, subject: Subject, *, record: bool = True) -> dict:
    """Validate, check, then run ``kind``; with ``record``, a history note and a line in
    ``<home>/actions.jsonl`` carry the origin. Returns ``{"result", "say", "what",
    "url"?, "undo"?, "subject"?, "confirm"?}``; raises ``ActionError``, or what the page's
    method raises (``HttpError``, ``LoopError``)."""
    if not valid_origin(ctx.origin):
        raise ActionError(f"origin must be one of {', '.join(ORIGINS)}, or agent:<name>", 400)
    try:
        return _run(ctx, kind, fields, subject, record)
    except ActionError as e:
        if e.confirm:
            raise
        _refused(ctx, kind, subject, e, "refused")
        raise
    except Exception as e:
        _refused(ctx, kind, subject, e, "error")
        raise


def _refused(ctx: Ctx, kind: str, subject: Subject, exc: BaseException, outcome: str) -> None:
    """A press that did not happen, on the record: why, in the subject's history (its
    page and ``factory_outcomes``), a line in ``actions.jsonl``, a failed run in Activity and
    the log. Never raises: the refusal is the caller's answer either way."""
    a = ACTIONS.get(kind)
    title = a.title if a else kind
    refusal = refusal_of(exc)
    if kind in GITHUB_KINDS or kind in CLAUDE_KINDS:
        # a credential's press: its words may quote what was pasted; only its kind is kept
        why = f"refused ({refusal}); a credential's words are not kept: the page said why"
    else:
        from .loop.secrets import scrub

        why = one_line(scrub(str(exc) or type(exc).__name__), MAX_NOTE)
    iid = _iid(subject)
    _log.warning(
        "action %s on %s %s by %s refused (%s): %s",
        kind,
        subject.kind,
        iid or subject.id,
        ctx.origin,
        refusal,
        why,
    )
    try:
        lp = getattr(ctx.app, "loop")()  # noqa: B009 - an App, or a stand-in
    except Exception:  # noqa: BLE001 - nothing to record into
        return
    if iid:
        try:
            lp.ledger.note(
                iid,
                f"{ctx.origin}: {title} did not happen: {why}",
                {"origin": ctx.origin, "refused": kind, "refusal": refusal},
            )
        except (SystemExit, Exception) as e:  # noqa: BLE001
            _log.warning("action %s refused; its note failed: %s", kind, e)
    try:
        with open(lp.cfg.layout.actions, "a") as f:
            f.write(
                json.dumps(
                    {
                        "ts": time.time(),
                        "origin": ctx.origin,
                        "kind": kind,
                        "idea": iid,
                        "build": (subject.row or {}).get("key"),
                        "outcome": outcome,
                        "refusal": refusal,
                        "why": why,
                    }
                )
                + "\n"
            )
    except Exception as e:  # noqa: BLE001 - never the caller's answer
        _log.warning("action %s refused; actions.jsonl not written: %s", kind, e)
    try:
        from .loop import activity

        activity.refused(
            lp.ledger,
            kind,
            title,
            why,
            idea=iid or None,
            name=(subject.row or {}).get("title") or (subject.idea or {}).get("title"),
        )
    except Exception as e:  # noqa: BLE001
        _log.warning("action %s refused; Activity not written: %s", kind, e)


def outcomes(lp, idea: str | None, build: str | None, n: int = 10) -> list[dict]:
    """The last ``n`` presses on a subject, newest first, from ``actions.jsonl``: each with
    ``kind``, ``title``, ``origin``, ``at``, ``outcome`` (ok, refused, error) and, when it did
    not happen, ``refusal`` and ``why`` ("why did my press fail"). With neither ``idea`` nor
    ``build``: the last presses on anything, each naming its ``idea`` and ``build``."""
    anything = idea is None and build is None
    path = Path(lp.cfg.layout.actions)
    try:
        with open(path, "rb") as f:  # the file's tail is enough
            f.seek(0, 2)
            f.seek(max(0, f.tell() - 512_000))
            lines = f.read().decode("utf-8", "replace").splitlines()
    except OSError:
        return []
    out: list[dict] = []
    for ln in reversed(lines):
        try:
            r = json.loads(ln)
        except ValueError:
            continue
        if not isinstance(r, dict) or not (
            anything or (idea and r.get("idea") == idea) or (build and r.get("build") == build)
        ):
            continue
        a = ACTIONS.get(str(r.get("kind")))
        out.append(
            {
                "kind": r.get("kind"),
                "title": a.title if a else r.get("kind"),
                "origin": r.get("origin"),
                **({"idea": r.get("idea"), "build": r.get("build")} if anything else {}),
                "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(float(r.get("ts") or 0))),
                "outcome": r.get("outcome") or "ok",
                **(
                    {"refusal": r.get("refusal"), "why": r.get("why")}
                    if r.get("outcome") in ("refused", "error")
                    else {"what": r.get("what")}
                ),
            }
        )
        if len(out) >= n:
            break
    return out


def _run(ctx: Ctx, kind: str, fields: dict, subject: Subject, record: bool) -> dict:
    a = get(kind)
    clean = validate(kind, fields or {})
    why = check(kind, subject)
    if why:
        raise ActionError(why)
    if a.click == "confirm" and not ctx.confirm:
        raise ActionError(f"{kind} needs confirm: true", 409, confirm=True)
    token = _ORIGIN.set(ctx.origin)
    try:
        out = a.run(ctx, clean, subject)
    finally:
        _ORIGIN.reset(token)
    if record:
        _record(ctx, a, subject, out)
    return out


def _record(ctx: Ctx, a: Action, subject: Subject, out: dict) -> None:
    iid = _iid(subject) or ((out.get("subject") or {}).get("id"))
    what = out.get("what") or (out.get("say") or "").rstrip(".")
    try:
        lp = ctx.app.loop()
    except Exception as e:  # noqa: BLE001 - nothing to record into
        _log.warning("action %s ran; no loop to record it in (%s)", a.kind, type(e).__name__)
        return
    if iid:
        try:
            lp.ledger.note(
                iid,
                f"{ctx.origin}: {what}",
                {"origin": ctx.origin, "kind": a.kind, "url": out.get("url")},
            )
        except (SystemExit, Exception) as e:  # noqa: BLE001 - the action ran either way
            _log.warning("action %s on %s ran; its note failed: %s", a.kind, iid, e)
    try:
        with open(lp.cfg.layout.actions, "a") as f:
            f.write(
                json.dumps(
                    {
                        "ts": time.time(),
                        "origin": ctx.origin,
                        "kind": a.kind,
                        "idea": iid,
                        "build": (subject.row or {}).get("key"),
                        "outcome": "ok",
                        "what": what,
                        "url": out.get("url"),
                    }
                )
                + "\n"
            )
    except OSError as e:
        _log.warning("action %s ran; actions.jsonl not written: %s", a.kind, e)


# -- JSON Schema of an action's fields (api/act) -----------------------------------------------
def _field_schema(f: Field) -> dict:
    d: dict
    if f.kind in ("text", "line"):
        d = {"type": "string", "maxLength": f.max}
        if f.min:
            d["minLength"] = f.min
    elif f.kind == "enum":
        d = {"type": "string", "enum": list(f.choices)}
    elif f.kind == "int":
        d = {"type": "integer", "minimum": f.min, "maximum": f.max}
    elif f.kind == "bool":
        d = {"type": "boolean"}
    else:
        d = {
            "type": "array",
            "items": {"type": "string", **({"enum": list(f.choices)} if f.choices else {})},
        }
    if f.help:
        d["description"] = f.help
    return d


def schema(kind: str) -> dict:
    """The JSON Schema of an action's arguments: the subject's id (``build`` or ``idea``),
    its fields, and ``confirm: true`` for the confirm class."""
    a = get(kind)
    props: dict = {}
    req: list = []
    subj = [k for k in ("build", "idea") if k in a.subjects]
    for k in subj:
        props[k] = {
            "type": "string",
            "maxLength": 200,
            "description": "the build's key" if k == "build" else "the task's id (i…)",
        }
    for f in a.fields:
        props[f.name] = _field_schema(f)
        if f.required:
            req.append(f.name)
    if a.click == "confirm":
        props["confirm"] = {
            "type": "boolean",
            "const": True,
            "description": "true: the owner confirmed this",
        }
        req.append("confirm")
    out = {"type": "object", "properties": props, "required": req, "additionalProperties": False}
    if len(subj) == 2 and "new" not in a.subjects:
        out["oneOf"] = [{"required": ["build"]}, {"required": ["idea"]}]
    elif len(subj) == 1 and "new" not in a.subjects:
        out["required"] = [subj[0], *req]
    return out


def subject_of(app, kind: str, ref: str | None) -> Subject:
    """The live subject behind ``{kind, id}`` (``resolve`` over the snapshot); LookupError
    when there is none."""
    cfg = app.loop().cfg.data
    if kind in ("new", None, ""):
        return Subject("new", cfg=cfg)
    if kind == "loop":  # factory-wide actions
        return Subject("loop", cfg=cfg)
    if kind not in ("idea", "build"):
        raise ActionError("subject kind must be new, idea, build or loop", 400)
    ref = str(ref or "")
    if not ref or not re.fullmatch(r"[\w./:@+-]{1,200}", ref):
        raise ActionError(f"say which {kind}", 400)
    idea, row = resolve(app, {"kind": kind, "id": ref})
    if idea is None and row is None:
        raise LookupError(f"no such {kind}: {ref}")
    return Subject(kind, idea, row, cfg, ref)


def resolve(app, subject: dict, snap: dict | None = None) -> tuple[dict | None, dict | None]:
    """The subject's live rows: ``(task row, build row)`` from the snapshot, either None."""
    kind, ref = (subject or {}).get("kind"), (subject or {}).get("id")
    if kind not in ("idea", "build"):
        return None, None
    snap = snap or app.snapshot()
    if kind == "idea":
        idea = next((i for i in snap["ideas"] if i["id"] == ref), None)
        row = next((b for b in snap["builds"] if idea and b["key"] == idea.get("build")), None)
        return idea, row
    row = next((b for b in snap["builds"] if b["key"] == ref), None)
    idea = next(
        (i for i in snap["ideas"] if row and row.get("idea") and i["id"] == row["idea"]), None
    )
    return idea, row
