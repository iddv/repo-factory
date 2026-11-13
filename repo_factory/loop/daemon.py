"""The loop itself: ``Loop`` does each step once; ``Loop.run`` is ``repo-factory loop run``'s daemon.

One tick, in order:
1. **watch** the running builds: a finished one goes through the gate (``ready`` or
   ``held``); one whose process died without finishing is resumed (``--resume <round>``,
   up to ``build.max_resumes``); a failed one is retried once (resumed when v5 saved its
   state, else from scratch), then held;
2. run the pre-push checks of the ready and held ones (``precheck.py``: no press waits on
   a model); a ready build then waits for the owner's Publish (a private repository), unless
   ``publish.when: ready`` publishes it privately on its own (``publish.approval: auto``, set
   by hand only, also makes it public);
3. unless paused (``PAUSE``) or over the optional ``daily_usd``: **maintain** the published
   repos when maintenance is on (every ``maintain.poll_s``: triage, fixes of ``go`` issues and
   releases on GitHub, ``repo_factory/maintain/poll.py``), write the **brief** of up to
   ``pitch_per_tick`` new tasks (``pitch.py``), and **start** queued builds up to
   ``concurrency`` (running fixes take build slots too).

A task comes in typed: on the page, or ``repo-factory loop add``. In the ledger it is an
"idea" row; the page calls it a build.

The owner can say no at any point: ``repo-factory loop close ID --reason ...`` ends a task (and
stops its build), so it leaves "needs you". And control any one build without closing it:
``stop-round`` (kill its workers and keep the round; back to held or ready as before the
round, and only ``resume-build`` continues it), ``cancel`` (a queued build, before it starts) and ``restart`` (a running revise round, with new
instructions). None waits: the kill is SIGTERM now and SIGKILL later, and ``watch`` makes the
change once the build is gone.

Builds run detached (``builds.py``), so the daemon can stop, crash or restart at any time:
the next one adopts the running builds by pid and resumes the dead ones. ``repo-factory loop pause``
stops new pitches and builds and leaves running builds alone; ``repo-factory loop stop`` also stops
the running builds (their rounds are resumed by the next ``repo-factory loop run``) and asks the
daemon to exit."""

from __future__ import annotations

import fcntl
import os
import re
import signal
import sys
import time
from pathlib import Path

from .. import proc
from ..errors import LoopError
from ..settings.store import Config
from . import activity, builds
from . import claims as cl
from . import gate as gate_mod
from . import pitch as pitch_mod
from . import precheck as pc
from . import publish as pub
from .ledger import Ledger, spend_summary, token_summary


def log(msg: str) -> None:
    print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} repo-factory loop: {msg}", flush=True)


def anyway_hist(override: dict, outcome: str) -> dict:
    """The history row of a Publish anyway press: what it overrode and what happened."""
    return {
        "origin": override.get("origin"),
        "kind": "publish_anyway",
        "overrides": override.get("overrides"),
        "reason": override.get("reason"),
        "outcome": outcome,
    }


def _maintained(got: dict) -> tuple[str | None, bool]:
    """What a maintenance poll did, in words, and whether it did anything (a run then)."""
    repos = got.get("repos") or []
    triaged = sum(len(r.get("triaged") or []) for r in repos)
    fixing = sum(len((r.get("revise") or {}).get("started") or []) for r in repos)
    released = sum(1 for r in repos if (r.get("release") or {}).get("tag"))
    merged = sum(len(r.get("merged") or []) for r in repos)
    parts = [
        f"{n} {w}"
        for n, w in (
            (triaged, "triaged"),
            (fixing, "fixes started"),
            (merged, "merged"),
            (released, "released"),
        )
        if n
    ]
    return (" · ".join(parts) or None), bool(parts or got.get("error"))


# a held build whose revise rounds ended on the hold they started from: "two rounds
# did not move it: …" closes its reason, and no automatic round follows
NOT_MOVED = "did not move it: Publish anyway, or say what to change"
NUMBERS = {2: "two", 3: "three", 4: "four", 5: "five", 6: "six"}


def _setting(section: dict, key: str, default):
    """A loop.yaml setting: its value, or ``default`` when it is absent or null (a key the
    owner left empty means the default, never None in a sum or a comparison)."""
    v = (section or {}).get(key)
    return default if v is None else v


SANDBOX_HOLD = (
    "Builds need bubblewrap, and it is missing or cannot make a sandbox here: install "
    "it (repo-factory doctor says how); held builds start again by themselves."
)


def _publishes_alone(cfg) -> bool:
    """A ready build is published (privately) without a press: ``publish.when: ready``, or the
    hand-set ``publish.approval: auto`` (which also makes it public)."""
    p = cfg["publish"]
    return p.get("when") == "ready" or p.get("approval") == "auto"


class Loop:
    def __init__(
        self,
        cfg: Config | None = None,
        *,
        model=None,
        summary_model=None,
        honesty_model=None,
        gh=None,
        triage_model=None,
        charter_model=None,
    ):
        self.cfg = (cfg or Config()).ensure()
        self.ledger = Ledger(self.cfg.db)
        self._model, self._summary, self._honesty = model, summary_model, honesty_model
        self._triage, self._charter = triage_model, charter_model
        self._gh = gh
        self._stop_flag = False
        self._spawned: dict[str, int] = {}  # pre-push checks this loop started: idea -> pid

    # -- collaborators (real ones made on first use) -------------------------------------------
    @property
    def model(self):
        if self._model is None:
            from .models import claude

            self._model = claude(self.cfg["pitch"].get("model"), "pitch", timeout_s=900)
        return self._model

    @property
    def summary_model(self):
        if self._summary is None:
            from .models import claude

            self._summary = claude(self.cfg["pitch"].get("summary_model"), "summary")
        return self._summary

    @property
    def honesty_model(self):
        if self._honesty is None:
            from .models import claude

            self._honesty = claude(self.cfg["publish"].get("honesty_model"), "honesty")
        return self._honesty

    @property
    def triage_model(self):
        if self._triage is None:
            from .models import claude

            self._triage = claude(
                (self.cfg.get("maintain") or {}).get("triage_model"), "triage", timeout_s=300
            )
        return self._triage

    @property
    def charter_model(self):
        """The charter's model (``projects.charter.model``), or None when charters are not
        drafted here: projects off, or ``$REPO_FACTORY_CHARTER=0`` (the tests) and none given."""
        if self._charter is None:
            if (
                not (self.cfg.get("projects") or {}).get("enabled", True)
                or os.environ.get("REPO_FACTORY_CHARTER") == "0"
            ):
                return None
            from ..projects.charter import model_for

            self._charter = model_for(self.cfg)
        return self._charter

    @property
    def gh(self):
        if self._gh is None:
            from ..github_user import connection

            self._gh = connection(self.cfg)
        return self._gh

    # -- the task in, and its brief --------------------------------------------------------------
    def add(self, text: str, source: str = "cli") -> tuple[dict, bool]:
        """A typed task: a new ledger row (or the same text's row already there)."""
        text = (text or "").strip()
        if not text:
            raise ValueError("say what to build")
        return self.ledger.add(text, source)

    def pitch(self, ids: list[str] | None = None, limit: int | None = None) -> list[dict]:
        if ids:
            todo = [self.ledger.must(i) for i in ids]
        else:
            now = time.time()  # an idea put off with Later waits it out
            todo = [
                i
                for i in self.ledger.all("new")
                if not (i.get("later_until") or 0) > now
                and not (i.get("pitch") or {}).get("cancelled")
            ][: limit or None]
        out = []
        for idea in todo:
            if idea["state"] not in ("new", "rejected", "error", "skipped"):
                raise LoopError(
                    f"{idea['id']} is {idea['state']}: only new, rejected or "
                    "failed ideas are pitched"
                )
            try:
                out.append(pitch_mod.pitch_idea(self.ledger, idea, self.model, self.cfg))
            except Exception as e:  # noqa: BLE001 - counted, then error
                errors = int((idea.get("pitch") or {}).get("errors", 0)) + 1
                state = "error" if errors >= int(self.cfg["pitch"]["max_errors"]) else "new"
                msg = f"{type(e).__name__}: {e}"[:300]
                log(f"{idea['id']}: pitch failed: {msg}")
                out.append(
                    self.ledger.update(
                        idea["id"],
                        state=state,
                        reason=f"pitch failed: {msg}",
                        pitch={"errors": errors, **pitch_mod.sent_mark(idea.get("pitch"))},
                        note=f"pitch failed: {msg}",
                        history={"failure": True},
                    )
                )
        return out

    def repitch(self, idea_id: str, now: bool = True) -> dict:
        idea = self.ledger.must(idea_id)
        if idea["state"] not in ("rejected", "error", "skipped", "new"):
            raise LoopError(
                f"{idea['id']} is {idea['state']}; only a rejected, skipped or "
                "failed pitch can be pitched again"
            )
        idea = self.ledger.update(
            idea["id"],
            state="new",
            pitch={**(idea.get("pitch") or {}), "errors": 0},
            note="re-pitch requested",
        )
        return self.pitch([idea["id"]])[0] if now else idea

    # -- the owner's two words: send and close -------------------------------------------------
    def resolve(self, ref: str) -> dict:
        """A task by ledger id (or a unique prefix)."""
        return self.ledger.must(ref)

    def send(self, ref: str, build_anyway: bool = False) -> dict:
        """Send to build: the task's brief is written on the next tick. ``build_anyway``: a
        passed brief (rejected) is built all the same, from its product name and its brief
        (else the task as written)."""
        idea = self.resolve(ref)
        if build_anyway:
            return self.build_anyway(idea["id"])
        if idea["state"] not in ("new", "rejected", "error", "skipped", "closed"):
            raise LoopError(
                f"{idea['id']} is {idea['state']}; only an open, passed, skipped, "
                "failed or closed task can be sent to build"
            )
        return self.ledger.update(
            idea["id"],
            state="new",
            reason=None,
            later_until=None,
            pitch={"errors": 0, "sent": time.time(), "by": self.by()},
            note="sent to build by the owner; the brief is written on the loop's next tick",
        )

    def by(self, origin: str | None = None) -> dict:
        """Who asks for a round: ``{origin, at}``, kept on the build record until the
        round starts (``start_builds`` makes it the round's ``started_by``). ``origin``: the
        verb's own, else the door the command came through (``--origin``, ``cli`` by
        default); None is the loop itself."""
        return {"origin": origin or self.ledger.origin, "at": time.time()}

    def build_anyway(self, idea_id: str) -> dict:
        idea = self.ledger.must(idea_id)
        p = dict(idea.get("pitch") or {})
        if idea["state"] != "rejected" or not p.get("product"):
            raise LoopError(
                f"{idea['id']} is {idea['state']}; Build anyway is for an idea the pitch passed on"
            )
        brief = (p.get("brief") or "").strip() or (idea.get("text") or "").strip()
        p.update(brief=brief, overridden=True, by=self.by())
        task = pitch_mod.make_task(idea, p, self.cfg.tasks_dir)
        return self.ledger.update(
            idea["id"],
            state="queued",
            pitch=p,
            task_dir=str(task),
            reason=None,
            title=p["product"],
            note=f"built anyway by the owner (the pitch passed: "
            f"{idea.get('reason') or 'no reason given'}); task "
            f"{task.name}",
        )

    def edit(self, idea_id: str, text: str) -> dict:
        """Replace an idea's text (the brief) before any build starts: an open, passed,
        failed, skipped or closed idea's ``text``; a queued idea whose build has not started
        also gets its task's ``spec.md`` rewritten (the scope stage reads it)."""
        idea = self.ledger.must(idea_id)
        text = (text or "").strip()
        if not text:
            raise LoopError("repo-factory loop edit: the new text is empty")
        if idea["state"] in ("new", "rejected", "error", "skipped", "closed"):
            return self.ledger.update(
                idea["id"], text=text, note="the brief was edited by the owner"
            )
        task = Path(idea.get("task_dir") or "")
        started = (task / ".den" / "events.jsonl").exists() or (idea.get("build") or {}).get("runs")
        if idea["state"] == "queued" and (task / "spec.md").is_file() and not started:
            p = dict(idea.get("pitch") or {})
            p["brief"] = text
            head = (task / "spec.md").read_text().splitlines()[:1]
            title = head[0] if head and head[0].startswith("# ") else f"# {idea['title']}"
            (task / "spec.md").write_text(
                pitch_mod.spec_text({**p, "product": title[2:].strip() or idea["title"]})
            )
            return self.ledger.update(
                idea["id"], pitch=p, note="the brief was edited by the owner; spec.md rewritten"
            )
        raise LoopError(
            f"{idea['id']} is {idea['state']}; the brief can change only before its build starts"
        )

    # -- Claims: what a claim means, the owner's say -------------------------------------------
    def _claim(self, idea: dict, k: str) -> tuple[list[dict], dict]:
        if not cl.open_for(idea):
            raise LoopError(
                f"{idea['id']} is {idea['state']}: claims are read when the build "
                "starts (and a published project's charter follows them), never "
                "between a build and its publish"
            )
        cs = cl.of(idea)
        c = next((x for x in cs if x["id"] == k), None)
        if c is None:
            raise LoopError(
                f"{idea['id']} has no claim {k!r}"
                + (f" (it has {', '.join(x['id'] for x in cs)})" if cs else "")
            )
        return cs, c

    def _claims_saved(
        self, idea: dict, cs: list[dict], note: str, quotes=None, history: dict | None = None
    ) -> dict:
        """The owner changed a claim or the release text: recorded, and an idea held before
        building because no reading kept its spirit is queued once none is left waiting."""
        p = {**(idea.get("pitch") or {}), "claims": cs}
        fields = {"pitch": p}
        g = idea.get("gate") or {}
        if idea["state"] == "held" and g.get("before_build") and not cl.needs_owner({"pitch": p}):
            task = Path(idea["task_dir"])
            if (task / "spec.md").is_file():
                (task / "spec.md").write_text(pitch_mod.spec_text(p))
            rec = dict(idea.get("build") or {})
            rec["by"] = self.by()
            fields.update(state="queued", reason=None, gate=None, build=rec)
            note += "; the build is queued with the claims as read"
        out = self.ledger.update(idea["id"], note=note, history=history, **fields)
        k = (history or {}).get("claim")
        if k:  # the interpretation is the charter's promise
            try:
                from ..projects.charter import apply_claim

                apply_claim(self, idea["id"], k)
            except Exception as e:  # noqa: BLE001 - the claim is saved either way
                log(f"{idea['id']}: the charter was not updated for claim {k}: {e}")
        return out

    def claim_accept(self, idea_id: str, k: str, origin: str = "cli") -> dict:
        """Accept claim ``k``'s interpretation as it is (status ``accepted``). When it held the
        idea before building (no other reading kept its spirit), the build is queued."""
        idea = self.ledger.must(idea_id)
        cs, c = self._claim(idea, k)
        p = idea.get("pitch") or {}
        if c.get("status") == "needs_owner" and p.get("release_proposed"):
            idea = {**idea, "pitch": cl.set_release(p, p["release_proposed"], "owner")}
        c.update(
            status="accepted",
            at=time.time(),
            spirit={
                **(c.get("spirit") or {}),
                "keeps": True,
                "why": (c.get("spirit") or {}).get("why") or "accepted by the owner",
            },
        )
        return self._claims_saved(
            idea,
            cs,
            f'the owner accepted how "{c["claim"]}" is read: "{c["interpreted"]}" ({origin})',
            [c["claim"]],
            {"kind": "claim", "claim": k, "did": "accept", "origin": origin},
        )

    def claim_edit(
        self, idea_id: str, k: str, text: str, default: str | None = None, origin: str = "cli"
    ) -> dict:
        """The owner's own reading of claim ``k`` (and its setting's default): accepted as
        written, source ``owner``. Accept queues a build it held; Edit only records."""
        idea = self.ledger.must(idea_id)
        cs, c = self._claim(idea, k)
        text = " ".join(str(text or "").split())
        if not 3 <= len(text) <= cl.TEXT_MAX:
            raise LoopError(f"say how the claim is read, in 3 to {cl.TEXT_MAX} characters")
        held = c.get("status") == "needs_owner"  # Accept, not Edit, starts the build
        c.update(
            interpreted=text,
            source="owner",
            at=time.time(),
            status="needs_owner" if held else "accepted",
            spirit={
                "keeps": not held,
                "why": "the owner's reading" + ("; Accept builds it" if held else ""),
            },
        )
        if default is not None and str(default).strip():
            s = dict(c.get("setting") or {})
            if not s.get("key"):
                raise LoopError(f"claim {k} has no setting to give a default")
            s["default"] = " ".join(str(default).split())[:80]
            c["setting"] = s
        return self._claims_saved(
            {**idea, "pitch": {**(idea.get("pitch") or {}), "claims": cs}},
            cs,
            f'the owner edited how "{c["claim"]}" is read: "{text}" ({origin})',
            [c["claim"]],
            {"kind": "claim", "claim": k, "did": "edit", "origin": origin},
        )

    def set_release_text(self, idea_id: str, text: str, origin: str = "cli") -> dict:
        """The owner's own release text (the repo description and the release notes' first
        line), used as it is: saved now, no model. An unpublished build's pre-push checks see a
        new key and stage it on their next run; a published build's repo gets it in the
        background (``text_check``)."""
        idea = self.ledger.must(idea_id)
        if idea["state"] in ("new", "rejected", "error", "skipped", "closed") or not (
            idea.get("pitch") or {}
        ).get("product"):
            raise LoopError(
                f"{idea['id']} is {idea['state']}; the release text belongs to a pitched build"
            )
        text = " ".join(str(text or "").split())
        if not cl.TEXT_MIN <= len(text) <= cl.TEXT_MAX:
            raise LoopError(
                f"the release text is one line of {cl.TEXT_MIN} to {cl.TEXT_MAX} characters"
            )
        old = cl.release_text(idea)
        p = cl.set_release(idea.get("pitch") or {}, text, "owner")
        p.pop("release_proposed", None)
        out = self._claims_saved(
            {**idea, "pitch": p},
            cl.of(idea),
            f'the owner set the release text ({origin}): "{text}" (was "{old}")',
            None,
            {"kind": "release_text", "origin": origin},
        )
        if (out.get("publish") or {}).get("url"):
            self._send_release_text(out["id"], origin=origin)
        return self.ledger.must(out["id"])

    def _send_release_text(self, idea_id: str, origin: str = "cli") -> dict:
        """A published build's release text, put on GitHub in the background (``text_check``,
        run by ``repo-factory loop precheck``); the command line runs it here."""
        idea = self.ledger.must(idea_id)
        if self.text_status(idea)["state"] == "running":
            return idea
        if self.may_wait(origin):
            self.text_check(idea["id"])
            return self.ledger.must(idea["id"])
        pid = pc.spawn(self.cfg, idea["id"])
        log(
            f"{idea['id']} {idea['title']}: the release text update started"
            + (f" (pid {pid})" if pid else "")
        )
        cur = dict(idea.get("publish") or {})
        cur["textcheck"] = {
            "state": "running",
            "pid": pid,
            "started": time.time(),
            "start": proc.start_time(pid) if pid else None,
        }
        return self.ledger.update(idea["id"], publish=cur)

    def text_status(self, idea: dict) -> dict:
        """The release text's state: a published build's update (``publish.textcheck``), else
        the pre-push checks' (``precheck.status``)."""
        pub_ = idea.get("publish") or {}
        if pub_.get("url"):
            rec = dict(pub_.get("textcheck") or {})
            if rec.get("state") == "running" and not proc.running(rec.get("pid"), rec.get("start")):
                rec["state"] = "failed"
                rec.setdefault("error", "the update stopped before it finished")
            return {"state": rec.get("state") or "none", "record": rec}
        st = pc.status(idea, self.cfg)
        return {"state": st["state"], "record": st["record"]}

    def close(
        self, idea_id: str, reason: str, *, word: str = "closed", data: dict | None = None
    ) -> dict:
        """The owner's no: the idea is closed with ``reason`` and nothing more happens to it.
        A running build is stopped. A public repo stays public: close is not undo. ``word``
        and ``data`` say how in the history (a reject with its chips)."""
        idea = self.ledger.must(idea_id)
        reason = " ".join((reason or "").split()) or "no reason given"
        if idea["state"] in ("public", "closed"):
            raise LoopError(f"{idea['id']} is {idea['state']}; there is nothing to close")
        rec = dict(idea.get("build") or {})
        was = idea["state"]
        out = self.ledger.update(
            idea["id"],
            state="closed",
            reason=f"{word}: {reason}",
            note=f"{word} by the owner ({was}): {reason}",
            history=data,
        )
        if was == "building":  # never waits: SIGTERM now, SIGKILL later
            builds.kill(rec.get("pid"), Path(idea.get("task_dir") or "."), self.kill_grace())
            rec.update(pid=None, status="stopped", stopping=False, finished=time.time())
            rec.pop("kill", None)
            out = self.ledger.update(idea["id"], build=rec)
        log(f"{idea['id']} {idea['title']}: {word} ({was}): {reason}")
        return out

    def reject(self, idea_id: str, reasons: list[str], note: str = "") -> dict:
        """Reject a build with the owner's reason chips (LEARNING.md 3.3), and an optional
        note: the idea is closed, and the chips stay in its history as data."""
        chips = [" ".join(str(r).split()) for r in reasons or [] if str(r).strip()]
        note = " ".join((note or "").split())
        if not chips and not note:
            raise LoopError("repo-factory loop reject: say why, with a reason or a note")
        why = ", ".join(chips) + (f" ({note})" if chips and note else note)
        return self.close(
            idea_id,
            why,
            word="rejected",
            data={"verdict": "reject", "reasons": chips, "note": note or None},
        )

    def keep(self, idea_id: str) -> dict:
        """Keep a published repo private: the owner has looked and said not public, not no.
        It leaves "needs you"; approve can still make it public later."""
        idea = self.ledger.must(idea_id)
        if idea["state"] != "published":
            raise LoopError(
                f"{idea['id']} is {idea['state']}; only a published (private) "
                "repo can be kept private"
            )
        if (idea.get("publish") or {}).get("kept"):
            raise LoopError(f"{idea['id']} is already kept private")
        rec = {**(idea.get("publish") or {}), "kept": time.time()}
        out = self.ledger.update(
            idea["id"],
            publish=rec,
            note="kept private by the owner: "
            f"{rec.get('url') or rec.get('repo') or 'the repo'} "
            "stays private",
        )
        return out

    def later(self, ref: str, days: float = 7.0) -> dict:
        """Put an open task off: no brief, and not waiting for you, for ``days``."""
        idea = self.resolve(ref)
        if idea["state"] not in ("new", "rejected", "error", "skipped"):
            raise LoopError(
                f"{idea['id']} is {idea['state']}; only an open task can wait until later"
            )
        if not 0 < float(days) <= 365:
            raise LoopError("repo-factory loop later: days must be between 0 and 365")
        until = time.time() + float(days) * 86400
        p = {k: v for k, v in (idea.get("pitch") or {}).items() if k not in ("sent", "sent_from")}
        return self.ledger.update(
            idea["id"],
            later_until=until,
            pitch=p or None,
            note=f"later: put off by the owner until "
            f"{time.strftime('%Y-%m-%d', time.localtime(until))}",
        )

    # -- spend ---------------------------------------------------------------------------------
    def spend(self) -> dict:
        return spend_summary(self.ledger)

    def over_cap(self) -> bool:
        cap = self.cfg.get("daily_usd")
        return bool(cap) and self.spend()["today"]["total"] >= float(
            cap
        )  # stored-shape: bool(cap) first

    # -- builds --------------------------------------------------------------------------------
    def building(self) -> list[dict]:
        return self.ledger.all("building")

    def revising(self) -> list[dict]:
        """The maintenance fixes running now (MAINTAIN.md §5.2): they share the build slots."""
        from ..maintain.github import Cache
        from ..maintain.revise import running

        try:
            return running(Cache(self.cfg.home))
        except Exception:  # noqa: BLE001 - a bad cache is no fix
            return []

    def start_builds(self, only: list[str] | None = None) -> list[dict]:
        from ..settings.schema import describe, refuses_builds

        bad = refuses_builds(getattr(self.cfg, "problems", []))
        if bad:  # a build would run without the owner's caps
            if getattr(self, "_settings_said", None) != bad:
                self._settings_said = bad
                log(f"no build starts until loop.yaml is fixed: {describe(bad)}")
            return []
        self._settings_said = None
        if not self.login_ready(only) or not self.sandbox_ready(only):
            return []
        slots = int(self.cfg["concurrency"]) - len(self.building()) - len(self.revising())
        started = []
        for idea in self.ledger.all("queued"):
            if slots <= 0:
                break
            if only and not any(idea["id"].startswith(o) for o in only):
                continue
            if (self.ledger.get(idea["id"]) or {}).get("state") != "queued":
                continue  # closed by the owner a moment ago
            task = Path(idea["task_dir"] or "")
            if not (task / "spec.md").is_file():
                self.ledger.update(
                    idea["id"], state="held", reason=f"the task folder {task} is missing"
                )
                continue
            rec = dict(idea.get("build") or {})
            rec.pop("held_for", None)  # a login hold the owner's Retry ended
            resume = rec.pop("resume_round", None)
            change = rec.get("change")  # Ask for changes: a revise round
            r = builds.start(
                self.cfg, task, resume, fresh=bool(rec.pop("fresh", False)), change=change
            )
            # who asked for this round: the owner's verb that queued it (``by``), the
            # send behind a first build (the pitch's), else the loop itself; a round the loop
            # resumes on its own keeps who started it
            by = rec.pop("by", None)
            if by or not (resume and rec.get("started_by")):
                by = by or ((idea.get("pitch") or {}).get("by") if not rec.get("runs") else None)
                rec["started_by"] = {"origin": (by or {}).get("origin"), "at": r["started"]}
            rec.update(
                pid=r["pid"],
                started=r["started"],
                round=resume,
                stopping=False,
                prior_started=r.get("prior_started"),
                status="running",
                runs=int(rec.get("runs", 0)) + 1,
            )
            if not change:  # which flow builds it (build.flow)
                rec["flow"] = builds.flow_of(r["cmd"])
            self._round_by(rec)
            what = (
                ("revise round resumed" if change else "build resumed") + f" (round {resume})"
                if resume
                else "revise round started"
                if change
                else "build started"
            )
            who = rec["started_by"].get("origin")
            self.ledger.update(
                idea["id"],
                state="building",
                build=rec,
                note=f"{what}, pid {r['pid']}" + (f", asked by {who}" if who else ""),
                history={"origin": who, "kind": "round_started"} if who else None,
            )
            log(f"{idea['id']} {idea['title']}: {what}")
            started.append(self.ledger.get(idea["id"]))
            slots -= 1
        return started

    def login_ready(self, only: list[str] | None = None) -> bool:
        """BL: whether builds have a Claude login they may use (``handoff.build_login``: your
        plan's login token, the unit's own token, Claude Code's sign-in, or the watcher's API
        key with ``build.allow_api_key``). Without one no build starts and none spends: each
        queued build is held with ``handoff.HOLD`` (``build.held_for: login``), and once a login
        is there again those holds are queued again by themselves. A ``build.command`` or
        ``build.revise_command`` of your own, or a harness other than Claude Code (tests),
        starts no claude of repo-factory's: not held."""
        from ..harness import handoff

        b = self.cfg["build"]
        if (
            b.get("command")
            or b.get("revise_command")
            or (b.get("harness") or "claude-code") != "claude-code"
        ):
            return True
        login = handoff.build_login(cfg=self.cfg)
        if login["on"] is not None:
            for idea in self.ledger.all("held"):
                rec = dict(idea.get("build") or {})
                if rec.pop("held_for", None) != "login":
                    continue
                why = f"queued again: builds run on {login['words']} now"
                log(f"{idea['id']} {idea['title']}: {why}")
                self.ledger.update(
                    idea["id"], state="queued", build=rec, reason=None, gate=None, note=why
                )
            return True
        for idea in self.ledger.all("queued"):
            if only and not any(idea["id"].startswith(o) for o in only):
                continue
            rec = dict(idea.get("build") or {}, held_for="login")
            log(f"{idea['id']} {idea['title']}: held: {handoff.HOLD}")
            self.ledger.update(
                idea["id"],
                state="held",
                build=rec,
                reason=handoff.HOLD,
                gate={"verdict": "held", "reasons": [handoff.HOLD]},
                note=f"held before it started: {handoff.HOLD}",
            )
        return False

    def sandbox_ready(self, only: list[str] | None = None) -> bool:
        """Whether bubblewrap can make a sandbox here. Every session and every run of what a
        model wrote happens inside it, so without it no build starts and none spends: each
        queued build is held with ``SANDBOX_HOLD`` (``build.held_for: sandbox``), and queued
        again by itself once bubblewrap works. As ``login_ready``, a build command of your
        own or a harness other than Claude Code (tests) is not held."""
        from ..harness import sandbox

        b = self.cfg["build"]
        if (
            b.get("command")
            or b.get("revise_command")
            or (b.get("harness") or "claude-code") != "claude-code"
        ):
            return True
        if sandbox.available():
            for idea in self.ledger.all("held"):
                rec = dict(idea.get("build") or {})
                if rec.pop("held_for", None) != "sandbox":
                    continue
                why = "queued again: bubblewrap can make a sandbox now"
                log(f"{idea['id']} {idea['title']}: {why}")
                self.ledger.update(
                    idea["id"], state="queued", build=rec, reason=None, gate=None, note=why
                )
            return True
        for idea in self.ledger.all("queued"):
            if only and not any(idea["id"].startswith(o) for o in only):
                continue
            rec = dict(idea.get("build") or {}, held_for="sandbox")
            log(f"{idea['id']} {idea['title']}: held: {SANDBOX_HOLD}")
            self.ledger.update(
                idea["id"],
                state="held",
                build=rec,
                reason=SANDBOX_HOLD,
                gate={"verdict": "held", "reasons": [SANDBOX_HOLD]},
                note=f"held before it started: {SANDBOX_HOLD}",
            )
        return False

    @staticmethod
    def _round_by(rec: dict) -> None:
        """Each round's record of who started it (``build.rounds_by[<round>]``), once the
        round's number is known; the first start of a round is the one kept."""
        if rec.get("round") is not None and isinstance(rec.get("started_by"), dict):
            rec.setdefault("rounds_by", {}).setdefault(str(rec["round"]), rec["started_by"])

    def watch(self) -> list[dict]:
        """Reconcile every ``building`` idea with its process and its event log."""
        changed = []
        for idea in self.building():
            task = Path(idea["task_dir"])
            rec = dict(idea.get("build") or {})
            if builds.alive(rec.get("pid"), task):
                k = rec.get("kill")
                if (
                    k
                    and not k.get("forced")
                    and time.time() - float(k.get("at") or 0) > self.kill_grace() + 20
                ):
                    # the owner's stop outlived its reaper: SIGKILL it now (the same checks)
                    builds.finish(builds.signal_build(rec["pid"], signal.SIGKILL, task))
                    rec["kill"] = {**k, "forced": time.time()}
                    self.ledger.update(idea["id"], build=rec)
                    continue
                if rec.get("round") is None:
                    st = builds.load_state(task)
                    rnd = st.get("build") if st else None
                    if rnd is not None and builds.is_fresh(st, rec):
                        rec["round"] = rnd
                        self._round_by(rec)
                        self.ledger.update(idea["id"], build=rec)
                continue
            changed.append(self._finished(idea, task, rec))
        return changed

    def _finished(self, idea: dict, task: Path, rec: dict) -> dict:
        now = self.ledger.get(idea["id"]) or {}
        if now.get("state") != "building":  # closed by the owner while we looked
            return now
        out = builds.outcome(task, rec)
        rec.update(
            status=out["status"],
            round=out["round"] or rec.get("round"),
            cost_usd=out["cost_usd"],
            finished=time.time(),
        )
        self._round_by(rec)
        if rec.get("kill"):  # the owner's Stop round, Pause or restart
            return self._controlled(idea, task, rec)
        if rec.get("stopping"):
            return self._requeue(idea, task, rec, "stopped by repo-factory loop stop")
        if out["status"] == "ok":
            rec.pop("before", None)  # the round ended: nothing to put back
            if rec.get("change"):
                return self._revised(idea, task, rec)
            return self.gate(idea["id"], rec=rec)
        b = self.cfg["build"]
        if rec.get("change") and not (out["interrupted"] and out["resumable"]):
            rec.pop("before", None)
            return self._revise_failed(idea, task, rec, out)
        if (
            out["interrupted"]
            and out["resumable"]
            and int(rec.get("resumes", 0)) < int(_setting(b, "max_resumes", 3))
        ):
            rec["resumes"] = int(rec.get("resumes", 0)) + 1
            return self._requeue(
                idea,
                task,
                rec,
                f"the build process died without finishing ({out['error']}); it resumes",
            )
        rec["failures"] = int(rec.get("failures", 0)) + 1
        rec["error"] = out["error"]
        if out.get("stage"):
            rec["failed_stage"] = out["stage"]
        if out.get("failure") == "auth":
            # the Claude login: retrying now fails the same way; held for the owner, whose
            # Retry resumes the round at the failed stage
            reason = out["error"]
            rec.pop("before", None)
            log(f"{idea['id']} {idea['title']}: held: {reason}")
            return self.ledger.update(
                idea["id"],
                state="held",
                build=rec,
                reason=reason,
                gate={"verdict": "held", "reasons": [reason]},
                note=f"held: {reason}",
                history={"failure": True},
            )
        if rec["failures"] <= int(_setting(b, "retries", 1)):
            rec["resumes"] = 0
            return self._requeue(
                idea,
                task,
                rec,
                f"build failed ({out['error']}); retrying "
                f"({rec['failures']} of {_setting(b, 'retries', 1)})",
                failure=True,
                resume=out["resumable"],
            )
        reason = f"the build failed {rec['failures']} time(s): {out['error']}"
        rec.pop("before", None)
        log(f"{idea['id']} {idea['title']}: held: {reason}")
        return self.ledger.update(
            idea["id"],
            state="held",
            build=rec,
            reason=reason,
            gate={"verdict": "held", "reasons": [reason]},
            note=f"held: {reason}",
            history={"failure": True},
        )

    # -- Ask for changes (AR-PLAN.md §2): a revise round of a held or ready build -----------
    def revise(
        self,
        idea_id: str,
        instructions: str,
        mode: str = "auto",
        origin: str = "cli",
        note: str | None = None,
    ) -> dict:
        """Queue a revise round of a held or ready build that has no repo yet: the owner's
        ``instructions`` (20 to 4000 characters) go to ``<task>/.den/changes/<k>.md``, the hold
        reasons beside it, and the build takes a slot like any build (``start_builds``). The
        gate is kept: the page says what was held while it revises. ``mode`` auto|docs|code
        (auto: docs when only the pre-push wording checks held it). ``note``: the history line
        (default: ``revise requested (<origin>): <the first 160 characters>``)."""
        from ..v5.change import MODES, base_round, pick_mode

        idea = self.ledger.must(idea_id)
        rv = self.cfg.get("revise") or {}
        if not rv.get("enabled", True):
            raise LoopError("revise is off here (revise.enabled: false in loop.yaml)")
        if idea["state"] not in ("held", "ready"):
            raise LoopError(
                f"{idea['id']} is {idea['state']}; only a held or ready build is revised"
            )
        pub = idea.get("publish") or {}
        if pub.get("url"):
            raise LoopError(
                "a published product changes through an issue: Open issue (with "
                "go to have repo-factory fix it)"
            )
        text = (instructions or "").strip()
        if not 20 <= len(text) <= 4000:
            raise LoopError("revise: say what should change, in 20 to 4000 characters")
        if mode not in MODES:
            raise LoopError(f"revise: mode is one of {', '.join(MODES)}")
        task = Path(idea.get("task_dir") or ".")
        g = idea.get("gate") or {}
        delivered = ((g.get("checks") or {}).get("delivered") or {}).get("ok")
        if not delivered or base_round(task / ".den") is None:
            raise LoopError(
                f"{idea['id']}: its build delivered nothing to change (Retry builds it again)"
            )
        rec = dict(idea.get("build") or {})
        cap = rv.get("max_per_build")
        if cap and len(rec.get("changes") or []) >= int(cap):
            raise LoopError(
                f"{idea['id']} has had {len(rec['changes'])} revise round(s), the "
                "most revise.max_per_build allows"
            )
        prepush = (idea.get("reason") or "").startswith("not pushed:")
        hold = self._hold_text(idea)
        self._refuse_identical(idea, text, hold)
        if mode == "auto":
            mode = pick_mode(g, pub if prepush else None)
        k = time.strftime("%Y%m%d-%H%M%S") + f"-{len(rec.get('changes') or []) + 1}"
        d = task / ".den" / "changes"
        d.mkdir(parents=True, exist_ok=True)
        f = d / f"{k}.md"
        f.write_text(text + "\n")
        issues = (
            [
                f"{i.get('file') or 'README'}: {i.get('quote', '')!r}: {i.get('problem')}"
                for i in ((pub.get("honesty") or {}).get("issues") or [])
            ]
            if prepush
            else []
        )
        failing = [c for c, v in (g.get("checks") or {}).items() if not v.get("ok")]
        reasons = list(g.get("reasons") or []) + ([idea["reason"]] if prepush else [])
        import json as _json

        (d / f"{k}.holds.json").write_text(
            _json.dumps({"reasons": reasons, "issues": issues, "failing": failing}, indent=1)
        )
        self._snapshot(idea, rec)
        # reasons: the gate's holds the round starts from, to compare its outcome with (AU1 #2)
        rec.update(
            change={
                "k": k,
                "mode": mode,
                "origin": origin,
                "requested": time.time(),
                "file": str(f),
                "reasons": list(g.get("reasons") or []),
            },
            failures=0,
            resumes=0,
            resume_round=None,
            pid=None,
            by=self.by(origin),
        )
        rec.pop("fresh", None)
        said = note or f"revise requested ({origin}): {' '.join(text.split())[:160]}"
        out = self.ledger.update(
            idea["id"],
            state="queued",
            build=rec,
            note=said,
            history={
                "origin": origin,
                "kind": "revise",
                "k": k,
                "mode": mode,
                "instructions": text[:4000],
                "hold": cl.fp(hold) if hold else None,
            },
        )
        log(f"{idea['id']} {idea['title']}: {said}")
        return out

    @staticmethod
    def _hold_text(idea: dict) -> str:
        """What holds a build now, in the words a round is asked to change (the gate's reasons
        when the reason carries the line that rounds did not move it)."""
        r = str(idea.get("reason") or "")
        if r.endswith(NOT_MOVED):
            r = ""
        return r or "; ".join((idea.get("gate") or {}).get("reasons") or [])

    def _auto_repair(self, idea: dict, g: dict) -> dict | None:
        """AU1 #2: a build the gate holds gets an automatic code revise round (origin
        ``auto``) with the hold as its brief, as the owner's Ask for changes would; at most
        ``build.auto_repair`` (default 1; 0: off) per build, counted when each starts, and
        ``revise.max_per_build``. Only queued: the loop's pause decides when it runs. Returns
        the idea (queued), or None: it stays held."""
        n = max(0, int(_setting(self.cfg["build"], "auto_repair", 1)))
        if (idea.get("build") or {}).get("flow") in ("simple", "lean"):
            return None  # the flow's own fix rounds were its repair (BF D7; lean:
            #                         one fix session, then it holds)
        reasons = list(g.get("reasons") or [])
        if (
            not n
            or g.get("verdict") == "ready"
            or not reasons
            or not ((g.get("checks") or {}).get("delivered") or {}).get("ok")
            or any(gate_mod.NOTHING_MEASURED in r for r in reasons)
        ):
            return None  # a cut-off build: a new build helps, not a fix
        why = gate_mod.cant_revise(g, idea.get("task_dir"), g)
        if why:  # ASKF: repo-factory's own sandbox; no round changes it
            log(f"{idea['id']} {idea['title']}: no automatic repair: {why}")
            return None
        rec = dict(idea.get("build") or {})
        used = int(rec.get("auto_repairs") or 0)
        if used >= n:
            return None
        text = "\n".join(
            [
                "The release checks held this build. Fix the product so each of "
                "these passes, and change nothing else:",
                *[f"- {r}" for r in reasons],
            ]
        )[:4000]
        rec["auto_repairs"] = used + 1
        self.ledger.update(idea["id"], build=rec)
        try:
            return self.revise(
                idea["id"],
                text,
                mode="code",
                origin="auto",
                note=f"held by the gate: an automatic repair round runs on the "
                f"hold ({used + 1} of {n}, build.auto_repair)",
            )
        except LoopError as e:  # revise off, its bound, or nothing to change
            log(f"{idea['id']} {idea['title']}: no automatic repair: {e}")
            return None

    @staticmethod
    def _not_moved(changes: list[dict], reasons: list[str]) -> int:
        """How many rounds ended on this same hold: the latest revise rounds that started
        from exactly these reasons (and ended on them), plus the round that first held."""
        k = 0
        for c in reversed(changes or []):
            if c.get("verdict") == "ready" or sorted(c.get("reasons") or []) != sorted(reasons):
                break
            k += 1
        return k + 1 if k else 0

    def _refuse_identical(self, idea: dict, text: str, hold: str) -> None:
        """Never a third identical round (NG-PLAN §6): refused when ``revise.max_identical``
        rounds (default 2; stopped and failed ones count: each request is a history row) were
        already asked against this same hold, which is still here unchanged, with the same
        instructions or the hold's own words. Different instructions are a material change and
        run. Says what to do next; never whose fault it is."""
        cap = (self.cfg.get("revise") or {}).get("max_identical", 2)
        if not cap or not hold:
            return
        # the hold pasted with its "two rounds did not move it" line is still the hold's words
        text = re.sub(r";?\s*\S+ rounds " + re.escape(NOT_MOVED), "", text)
        h = cl.fp(hold)
        prior = [
            r
            for r in self.ledger.history(idea["id"])
            if (r.get("data") or {}).get("kind") == "revise"
            and (r.get("data") or {}).get("k")
            and (r.get("data") or {}).get("hold") == h
        ]
        seen = {(r["data"]["k"]) for r in prior}
        same = [
            r
            for r in prior
            if cl.same(text, r["data"].get("instructions") or "") or cl.same(text, hold)
        ]
        if len(seen) >= int(cap) and (same or cl.same(text, hold)):
            raise LoopError(
                f"{len(seen)} revise rounds already ran against this same hold and it did not "
                "change; a third with the same instructions would change nothing. Next: say "
                "what is different (the file and the fix), accept or edit the interpretation "
                "under Claims when a claim is what holds it, or Publish anyway when the hold "
                "is one you accept"
            )

    def _revised(self, idea: dict, task: Path, rec: dict) -> dict:
        """A revise round ended ok: the gate on its round; the change moves to
        ``build.changes``; ready publishes privately on the next tick, as any build."""
        g = gate_mod.evaluate(task, build=rec.get("round"))
        gate_mod.record(task, g)  # the verdict kept beside its round (UXI)
        ch = dict(rec.pop("change") or {})
        rec["changes"] = list(rec.get("changes") or []) + [
            {
                **ch,
                "round": rec.get("round"),
                "verdict": g["verdict"],
                "usd": rec.get("cost_usd"),
                "at": time.time(),
            }
        ]
        said = (
            f"revised in round {rec.get('round')} ({ch.get('origin')}): the gate says "
            f"{g['verdict']}" + (f"; {'; '.join(g['reasons'])}" if g["reasons"] else "")
        )
        hist = {
            "origin": ch.get("origin"),
            "kind": "revise",
            "round": rec.get("round"),
            "verdict": g["verdict"],
        }
        if g["verdict"] == "ready":
            out = self.ledger.update(
                idea["id"], state="ready", build=rec, gate=g, reason=None, note=said, history=hist
            )
            log(f"{idea['id']} {idea['title']}: {said}")
            return out
        # AU1 #2: the outcome, not the instructions: a round that left the hold exactly as it
        # found it stops the automatic rounds, with one line
        n = (
            self._not_moved(rec["changes"], g["reasons"])
            if sorted(ch.get("reasons") or []) == sorted(g["reasons"])
            else 0
        )
        reason = "; ".join(g["reasons"])
        if n:
            reason += f"; {NUMBERS.get(n, str(n))} rounds {NOT_MOVED}"
            said += f"; {NUMBERS.get(n, str(n))} rounds {NOT_MOVED}"
        out = self.ledger.update(
            idea["id"], state="held", build=rec, gate=g, reason=reason, note=said, history=hist
        )
        log(f"{idea['id']} {idea['title']}: {said}")
        if not n:
            return self._auto_repair(out, g) or out
        return out

    def _revise_failed(self, idea: dict, task: Path, rec: dict, out: dict) -> dict:
        """A revise round failed: held, the gate as it was on the round it revised (the
        failed round stays visible; Retry resumes it while it can)."""
        from ..v5.change import base_round

        prev = base_round(task / ".den")
        g = gate_mod.evaluate(task, build=prev) if prev else (idea.get("gate") or {})
        rec["failures"] = int(rec.get("failures", 0)) + 1
        rec["error"] = out["error"]
        if out.get("stage"):
            rec["failed_stage"] = out["stage"]
        why = str(out["error"] or "it stopped")
        if why.startswith("revise failed in "):
            why = why[len("revise failed in ") :]
            stage, _, why = why.partition(": ")
        else:
            stage = out.get("stage") or "the round"
        reason = f"revise failed in {stage}: {why}"
        reason += "" if "the build is as it was" in reason else "; the build is as it was"
        log(f"{idea['id']} {idea['title']}: held: {reason}")
        return self.ledger.update(
            idea["id"],
            state="held",
            build=rec,
            gate=g,
            reason=reason,
            note=f"held: {reason}",
            history={
                "failure": True,
                "kind": "revise",
                "origin": (rec.get("change") or {}).get("origin"),
            },
        )

    def _requeue(self, idea, task, rec, why, failure=False, resume=True) -> dict:
        rnd = rec.get("round") or builds.current_round(task)
        rec["resume_round"] = rnd if resume and rnd and builds.resumable(task, rnd) else None
        rec.update(pid=None, stopping=False)
        log(f"{idea['id']} {idea['title']}: {why}")
        return self.ledger.update(
            idea["id"],
            state="queued",
            build=rec,
            note=why,
            history={"failure": True} if failure else None,
        )

    def gate(self, idea_id: str, rec: dict | None = None, repair: bool = True) -> dict:
        """The gate on a built round: ready or held. ``repair``: a held build may get its
        automatic repair round (``_auto_repair``); false when the gate is only run again."""
        idea = self.ledger.must(idea_id)
        rec = rec or dict(idea.get("build") or {})
        g = gate_mod.evaluate(Path(idea["task_dir"]), build=rec.get("round"))
        gate_mod.record(Path(idea["task_dir"]), g)  # the verdict kept beside its round
        # what repo-factory could not measure is said, never held
        gaps = ("; not measured (not held): " + "; ".join(g["gaps"])) if g.get("gaps") else ""
        if g["verdict"] == "ready":
            note = "built; the gate passed: ready to publish" + gaps  # no dollars here
            out = self.ledger.update(
                idea_id, state="ready", build=rec, gate=g, reason=None, note=note
            )
        else:
            note = "built; held by the gate: " + "; ".join(g["reasons"]) + gaps
            out = self.ledger.update(
                idea_id, state="held", build=rec, gate=g, reason="; ".join(g["reasons"]), note=note
            )
        log(f"{idea_id} {idea['title']}: {note}")
        if repair and g["verdict"] != "ready":  # the hold is the round's brief
            return self._auto_repair(out, g) or out
        return out

    def retry(self, idea_id: str, rebuild: bool = False, note: str | None = None) -> dict:
        """A held idea goes again. Held by the pre-push checks with a ready gate: publish is
        tried again. Otherwise the build: its round resumed when v5 saved an unfinished
        state (a failed stage runs again; a failed build stage continues its committed tree),
        else a new round, which continues an earlier round's stopped build when there is one
        (v5 ``carry_from``); ``rebuild`` forces a new round from nothing. ``note``: one line
        kept with the retry in the idea's history (the chat's diagnosis, say)."""
        idea = self.ledger.must(idea_id)
        if idea["state"] != "held":
            raise LoopError(f"{idea['id']} is {idea['state']}; only a held idea is retried")
        note = " ".join(str(note or "").split())[:300] or None
        said = f"; {note}" if note else ""
        hist = {"note": note} if note else None
        if (idea.get("gate") or {}).get("verdict") == "ready" and not rebuild:
            return self.ledger.update(
                idea["id"],
                state="ready",
                reason=None,
                publish={**(idea.get("publish") or {}), "attempts": 0},
                note="retry: publishing again" + said,
                history=hist,
            )
        task = Path(idea["task_dir"])
        rec = dict(idea.get("build") or {})
        g = idea.get("gate") or {}
        if (
            not rebuild
            and rec.get("flow") == "simple"
            and g.get("verdict") == "held"
            and ((g.get("checks") or {}).get("delivered") or {}).get("ok")
            and not builds.resumable(task, rec.get("round") or 0)
        ):
            # the simple flow: Retry is the loop once more on the delivered tree, the hold as
            # its brief (BF D6); Retry with a rebuild builds it again
            try:
                return self.revise(
                    idea["id"],
                    self._hold_text(idea) or "; ".join(g.get("reasons") or []),
                    mode="code",
                    origin="retry",
                    note="retry: one more fix round on the hold" + said,
                )
            except LoopError as e:
                log(f"{idea['id']} {idea['title']}: retry as a fix round: {e}; a new round")
        self._snapshot(idea, rec)
        rnd = rec.get("round")
        resume = None if rebuild else (rnd if rnd and builds.resumable(task, rnd) else None)
        if resume is None:  # a revise round resumes as one; otherwise it is over
            rec.pop("change", None)
            rec.pop("auto_repairs", None)  # a new build gets its own automatic repair round
        rec.update(
            failures=0, resumes=0, resume_round=resume, pid=None, fresh=bool(rebuild), by=self.by()
        )
        carried = None
        if resume is None and not rebuild:
            from ..v5.pipeline import carry_from

            rounds = [
                int(p.name[1:]) for p in (task / ".den" / "v5").glob("r*") if p.name[1:].isdigit()
            ]
            carried = carry_from(task / ".den", max(rounds) + 1) if rounds else None
        what = (
            f"resuming {'revise ' if rec.get('change') else ''}round {resume}"
            if resume
            else f"a new round, continuing round {carried.name[1:]}'s stopped build"
            if carried
            else "a fresh build"
        )
        return self.ledger.update(
            idea["id"],
            state="queued",
            build=rec,
            reason=None,
            note="retry: " + what + said,
            history=hist,
        )

    # -- one build's controls (RC): Stop round, Resume build, Cancel, Restart ------------------
    # Each press returns at once: a kill is SIGTERM now (only this build: its pid's cmdline
    # names its task, and only its own process tree) and SIGKILL after ``kill_grace`` by a
    # detached reaper. The idea stays ``building`` with ``build.kill`` ("stopping…") until
    # the loop's watch sees the build gone, then ``_controlled`` makes the change. (Pause build
    # went into Stop round, AU1 #9: a "pause" kill recorded before that is a stop.)
    CONTROL_WORDS = {"stop": "stopping", "pause": "stopping", "restart": "restarting"}

    def kill_grace(self) -> float:
        return float(self.cfg["build"].get("kill_grace_s") or builds.KILL_GRACE_S)

    @staticmethod
    def _snapshot(idea: dict, rec: dict) -> None:
        """What Stop round and Cancel put back: the idea as it was when this round was
        queued (Retry, Ask for changes, Resume). A round stopped before is history now."""
        rec["before"] = {
            "state": idea["state"],
            "reason": idea.get("reason"),
            "gate": idea.get("gate") or None,
            "publish": idea.get("publish") or None,
            "round": rec.get("round") or (idea.get("gate") or {}).get("round"),
        }
        rec.pop("stopped", None)

    def _kill(self, idea: dict, what: str, origin: str, **extra) -> dict:
        rec = dict(idea.get("build") or {})
        if rec.get("kill"):
            raise LoopError(f"{idea['id']} is already {self.CONTROL_WORDS[rec['kill']['what']]}")
        task = Path(idea.get("task_dir") or ".")
        rec["kill"] = {"what": what, "at": time.time(), "origin": origin, **extra}
        word = {"stop": "stop round", "restart": "restart"}[what]
        self.ledger.update(
            idea["id"],
            build=rec,
            note=f"{word} requested ({origin}): its workers are being stopped",
            history={"origin": origin, "kind": f"{word.replace(' ', '_')}"},
        )
        hit = builds.kill(rec.get("pid"), task, self.kill_grace())
        log(
            f"{idea['id']} {idea['title']}: {word} ({origin}): signalled "
            f"{len(hit)} process(es) of pid {rec.get('pid')}"
        )
        if not builds.alive(rec.get("pid"), task):  # nothing ran (or it ended): now
            return self._finished(self.ledger.must(idea["id"]), task, rec)
        return self.ledger.must(idea["id"])

    def stop_round(self, idea_id: str, origin: str = "cli") -> dict:
        """Stop round: kill this build's workers and put the idea back as it was before the
        round (held or ready, that round's gate, reason and publish record); the stopped
        round stays on disk and in the history, resumable only by the owner's Resume. A
        build paused before Pause build went into Stop round (AU1 #9) stops at once (nothing
        runs)."""
        idea = self.ledger.must(idea_id)
        rnd = (idea.get("build") or {}).get("round")
        if idea["state"] == "paused":
            snap = self._snap(idea)
            out = self._stopped(
                idea, Path(idea.get("task_dir") or "."), dict(idea.get("build") or {}), origin
            )
            self._remember_undo(
                idea["id"], "stop_round", origin, round=rnd, snapshot=snap, after=out["state"]
            )
            return out
        if idea["state"] != "building":
            raise LoopError(
                f"{idea['id']} is {idea['state']}; only a building or paused round is "
                "stopped"
                + (" (Cancel withdraws a queued one)" if idea["state"] == "queued" else "")
            )
        self._remember_undo(idea["id"], "stop_round", origin, round=rnd, was=self._snap(idea))
        return self._kill(idea, "stop", origin)

    # -- the flash's Undo (OI): Stop round and Cancel, within seconds ---------------------------
    UNDO_GRACE_S = 10  # the loop takes an Undo this long after the page stops offering it
    SNAP_FIELDS = ("state", "reason", "gate", "publish", "pitch", "task_dir", "build")

    def undo_s(self) -> float:
        return float(self.cfg["build"].get("undo_s") or 10)

    def _snap(self, idea: dict) -> dict:
        return {k: idea.get(k) for k in self.SNAP_FIELDS}

    def _remember_undo(self, iid: str, of: str, origin: str, **extra) -> None:
        now = time.time()
        self.ledger.meta(
            f"undo:{iid}",
            {"of": of, "at": now, "until": now + self.undo_s(), "origin": origin, **extra},
        )

    def undo(self, idea_id: str, of: str | None = None, origin: str = "cli") -> dict:
        """Undo the owner's last Stop round or Cancel of this build, within ``build.undo_s``
        (and a grace): a stopped round continues where it was
        (Resume), a stop still under way continues the round once its workers are gone, and a
        cancelled build is queued again exactly as it was (its task folder moved back). No
        kill and no model call."""
        idea = self.ledger.must(idea_id)
        key = f"undo:{idea['id']}"
        u = self.ledger.meta(key) or {}
        if not u.get("of") or (of and u["of"] != of):
            raise LoopError(
                f"{idea['id']}: there is nothing to undo"
                + (
                    f" (its last control was not {of.replace('_', ' ')})"
                    if of and u.get("of")
                    else ""
                )
            )
        if time.time() > float(u.get("until") or 0) + self.UNDO_GRACE_S:
            raise LoopError(f"{idea['id']}: too late to undo the {u['of'].replace('_', ' ')}")
        what = "Cancel" if u["of"] == "cancel" else "Stop round"
        rec = dict(idea.get("build") or {})
        if u.get("snapshot"):  # Cancel, or Stop round of a paused build
            if idea["state"] != u.get("after"):
                raise LoopError(f"{idea['id']} is {idea['state']} now: the {what} cannot be undone")
            self.ledger.meta(key, {})
            return self._restore(idea, u, what, origin)
        if idea["state"] == "building" and rec.get("kill"):
            # still stopping: the round continues once its workers are gone (_controlled)
            self.ledger.meta(key, {})
            rec["kill"] = {
                **rec["kill"],
                "undo": {"origin": origin, "at": time.time(), "of": u["of"], "was": u.get("was")},
            }
            return self.ledger.update(
                idea["id"],
                build=rec,
                note=f"{what} undone by the owner ({origin}): the round "
                "continues once its workers are gone",
                history={"origin": origin, "kind": "undo", "of": u["of"]},
            )
        if idea["state"] in ("paused", "held", "ready"):
            self.ledger.meta(key, {})
            try:
                return self.resume_build(idea["id"], origin=origin, word="undone")
            except LoopError:
                if not u.get("was"):
                    raise
                return self._requeue_as_was(idea, u["was"], what, origin)
        raise LoopError(f"{idea['id']} is {idea['state']} now: the {what} cannot be undone")

    def _requeue_as_was(self, idea: dict, was: dict, what: str, origin: str) -> dict:
        """Undo of a stop whose round was not saved (it cannot continue where it was): the
        build is queued again as it was queued for that round, so the round starts again."""
        b = dict(was.get("build") or {})
        for k in ("kill", "stopped", "paused"):
            b.pop(k, None)
        task, rnd = Path(idea.get("task_dir") or "."), b.get("round")
        if rnd:
            (task / ".den" / "v5" / f"r{int(rnd)}" / builds.STOPPED).unlink(
                missing_ok=True
            )  # stored-shape: not rnd raised above
        b.update(
            pid=None,
            status="queued",
            stopping=False,
            by=self.by(origin),
            resume_round=rnd if rnd and builds.resumable(task, rnd) else None,
        )
        return self.ledger.update(
            idea["id"],
            state="queued",
            build=b,
            reason=None,
            note=f"{what} undone by the owner ({origin}): queued again as it was; its round was "
            "not saved, so it starts again",
            history={"origin": origin, "kind": "undo"},
        )

    def _restore(self, idea: dict, u: dict, what: str, origin: str) -> dict:
        """Put back the idea as the snapshot had it (Cancel's Undo): its task folder too."""
        snap = u["snapshot"]
        moved, task = u.get("moved"), snap.get("task_dir")
        if moved and task and Path(moved).is_dir() and not Path(task).exists():
            Path(moved).rename(task)
        b = snap.get("build") or {}
        rnd = b.get("round")
        if snap.get("state") == "paused" and rnd and task:  # Stop round of a paused build
            (Path(task) / ".den" / "v5" / f"r{int(rnd)}" / builds.STOPPED).unlink(missing_ok=True)
        return self.ledger.update(
            idea["id"],
            state=snap["state"],
            reason=snap.get("reason"),
            gate=snap.get("gate") or None,
            publish=snap.get("publish") or None,
            pitch=snap.get("pitch") or None,
            task_dir=task,
            build=b or None,
            note=f"{what} undone by the owner ({origin}): {snap['state']} again, as it was",
            history={"origin": origin, "kind": "undo", "of": u["of"]},
        )

    def restart_revise(
        self, idea_id: str, instructions: str, mode: str = "auto", origin: str = "cli"
    ) -> dict:
        """Restart with new instructions: Stop round of a running revise round, then Ask for
        changes… with ``instructions``, in one press."""
        from ..v5.change import MODES

        idea = self.ledger.must(idea_id)
        if idea["state"] != "building" or not (idea.get("build") or {}).get("change"):
            raise LoopError(
                f"{idea['id']} is {idea['state']}; only a running revise round is "
                "restarted (Ask for changes asks for one)"
            )
        text = (instructions or "").strip()
        if not 20 <= len(text) <= 4000:
            raise LoopError("restart: say what should change, in 20 to 4000 characters")
        if mode not in MODES:
            raise LoopError(f"restart: mode is one of {', '.join(MODES)}")
        return self._kill(idea, "restart", origin, instructions=text, mode=mode)

    def resume_build(self, idea_id: str, origin: str = "cli", word: str = "resumed") -> dict:
        """Resume build: a round the owner stopped (Stop round) is queued to continue where it
        stopped, when v5 saved it; so is a build paused before Pause build went into Stop
        round (AU1 #9).
        ``word``: how the history says it ("undone" for the flash's Undo)."""
        idea = self.ledger.must(idea_id)
        rec = dict(idea.get("build") or {})
        kind = "undo" if word == "undone" else "resume_build"
        if idea["state"] == "paused":
            rec.pop("paused", None)
            rr = rec.get("resume_round")
            return self.ledger.update(
                idea["id"],
                state="queued",
                build={**rec, "status": "queued", "by": self.by(origin)},
                note=f"{word} by the owner ({origin}): "
                + (
                    f"round {rr} continues where it was paused"
                    if rr
                    else "it starts again on the next free build slot"
                ),
                history={"origin": origin, "kind": kind},
            )
        st = rec.get("stopped") or {}
        task = Path(idea.get("task_dir") or ".")
        rnd = st.get("round")
        if idea["state"] not in ("held", "ready") or not st.get("resumable") or not rnd:
            raise LoopError(
                f"{idea['id']} is {idea['state']}; only a paused build, or a round "
                "you stopped, is resumed"
            )
        if (idea.get("publish") or {}).get("url"):
            raise LoopError(f"{idea['id']} is published: its stopped round is history now")
        if not builds.resumable(task, rnd, stopped_ok=True):
            raise LoopError(f"round {rnd} can no longer be resumed: Retry builds it again")
        self._snapshot(idea, rec)
        (task / ".den" / "v5" / f"r{int(rnd)}" / builds.STOPPED).unlink(
            missing_ok=True
        )  # stored-shape: not rnd raised above
        if st.get("change"):
            rec["change"] = st["change"]
        rec.update(
            resume_round=rnd,
            round=rnd,
            failures=0,
            resumes=0,
            pid=None,
            status="queued",
            by=self.by(origin),
        )
        return self.ledger.update(
            idea["id"],
            state="queued",
            build=rec,
            reason=None,
            note=f"{word} by the owner ({origin}): round {rnd} continues where it was stopped",
            history={"origin": origin, "kind": kind, "round": rnd},
        )

    def cancel(self, idea_id: str, origin: str = "cli") -> dict:
        """Cancel: withdraw a queued build before it starts (nothing runs, so nothing is
        killed). A queued revise (or Retry, or Resume) goes back to how it was before it was
        asked; a new build goes back to Ideas (its untouched task folder moves to
        ``<tasks>/.cancelled/``); a build that ran before is held."""
        idea = self.ledger.must(idea_id)
        if idea["state"] != "queued":
            raise LoopError(
                f"{idea['id']} is {idea['state']}; only a queued build is cancelled"
                + (" (Stop round stops a running one)" if idea["state"] == "building" else "")
            )
        rec = dict(idea.get("build") or {})
        task = Path(idea.get("task_dir") or ".")
        if rec.get("pid") and builds.alive(rec["pid"], task):
            raise LoopError(f"{idea['id']} has started: Stop round stops it")
        snap, moved = self._snap(idea), []
        out = self._cancel(idea, rec, task, origin, moved)
        self._remember_undo(
            idea["id"],
            "cancel",
            origin,
            snapshot=snap,
            after=out["state"],
            moved=str(moved[0]) if moved else None,
        )
        return out

    def _cancel(self, idea: dict, rec: dict, task: Path, origin: str, moved: list) -> dict:
        before = rec.pop("before", None)
        ch = rec.pop("change", None)
        rec.pop("fresh", None)
        rec.update(resume_round=None, pid=None, status="cancelled")
        hist = {"origin": origin, "kind": "cancel", **({"k": ch.get("k")} if ch else {})}
        what = "revise round" if ch else "build"
        if before:
            return self.ledger.update(
                idea["id"],
                state=before["state"],
                reason=before.get("reason"),
                gate=before.get("gate"),
                publish=before.get("publish"),
                build={**rec, "round": before.get("round")},
                note=f"cancelled by the owner ({origin}) before the {what} started: back to "
                f"{before['state']} as it was",
                history=hist,
            )
        started = rec.get("runs") or (task / ".den" / "events.jsonl").exists()
        if not started:
            p = {
                k: v for k, v in (idea.get("pitch") or {}).items() if k not in ("sent", "sent_from")
            }
            p["cancelled"] = time.time()
            if idea.get("task_dir") and task.is_dir():
                dest = (
                    Path(self.cfg.tasks_dir).expanduser()
                    / ".cancelled"
                    / f"{task.name}-{time.strftime('%Y%m%d-%H%M%S')}"
                )
                dest.parent.mkdir(parents=True, exist_ok=True)
                task.rename(dest)
                moved.append(dest)
            return self.ledger.update(
                idea["id"],
                state="new",
                reason=None,
                pitch=p,
                task_dir=None,
                build=None,
                note=f"cancelled by the owner ({origin}) before its build started: back to Ideas"
                + (f" (its task folder is kept in {moved[0]})" if moved else ""),
                history=hist,
            )
        reason = f"cancelled by the owner ({origin}) before it started again: Retry builds it"
        return self.ledger.update(
            idea["id"],
            state="held",
            reason=reason,
            build=rec,
            gate=idea.get("gate") or {"verdict": "held", "reasons": [reason]},
            note=reason,
            history=hist,
        )

    def _controlled(self, idea: dict, task: Path, rec: dict) -> dict:
        """The build the owner stopped or restarted is gone: make the change."""
        k = rec.pop("kill")
        origin = k.get("origin") or "cli"
        out = self._stopped(idea, task, rec, origin)
        if k.get("undo") and k["what"] != "restart":
            return self._undone(out, k)
        if k["what"] != "restart":
            return out
        try:
            return self.revise(
                idea["id"],
                k["instructions"],
                mode=k.get("mode") or "auto",
                origin=origin,
                note=f"restarted with new instructions ({origin}): "
                f"{' '.join(k['instructions'].split())[:160]}",
            )
        except LoopError as e:
            said = f"restart: the round stopped, but the new one could not be asked for: {e}"
            log(f"{idea['id']} {idea['title']}: {said}")
            return self.ledger.update(idea["id"], note=said, history={"failure": True})

    def _undone(self, idea: dict, k: dict) -> dict:
        """An Undo pressed while the stop was under way: the round continues now."""
        u = k["undo"]
        try:
            return self.resume_build(idea["id"], origin=u.get("origin") or "cli", word="undone")
        except LoopError as e:
            if u.get("was"):
                return self._requeue_as_was(
                    self.ledger.must(idea["id"]), u["was"], "Stop round", u.get("origin") or "cli"
                )
            said = f"undo: the round could not continue ({e}); Retry builds it again"
            log(f"{idea['id']} {idea['title']}: {said}")
            return self.ledger.update(idea["id"], note=said, history={"failure": True})

    def _stopped(self, idea: dict, task: Path, rec: dict, origin: str) -> dict:
        """Stop round's change: the round is marked stopped on disk (``stopped.json``: Retry
        and carry_from leave it alone) and kept in the history; the idea is back as it was
        before the round, or held when there was nothing before (a first build)."""
        rnd = rec.get("round")
        rec.pop("kill", None)
        resumable = bool(rnd and builds.resumable(task, rnd))
        root = task / ".den" / "v5" / f"r{int(rnd)}" if rnd else None
        if root is not None and root.is_dir():
            import json as _json

            (root / builds.STOPPED).write_text(
                _json.dumps({"by": "owner", "origin": origin, "at": time.time()}) + "\n"
            )
        before = rec.pop("before", None)
        ch = rec.pop("change", None)
        for x in ("paused", "fresh"):
            rec.pop(x, None)
        rec.update(
            pid=None,
            stopping=False,
            status="stopped",
            resume_round=None,
            finished=time.time(),
            stopped={
                "round": rnd,
                "at": time.time(),
                "origin": origin,
                "resumable": resumable,
                **({"change": ch} if ch else {}),
            },
        )
        hist = {"origin": origin, "kind": "stop_round", "round": rnd}
        said = (
            f"round {rnd} stopped by the owner ({origin})"
            if rnd
            else f"stopped by the owner ({origin}) before its round began"
        )
        log(f"{idea['id']} {idea['title']}: {said}")
        if before:
            rec["round"] = before.get("round")
            return self.ledger.update(
                idea["id"],
                state=before["state"],
                reason=before.get("reason"),
                gate=before.get("gate"),
                publish=before.get("publish"),
                build=rec,
                note=f"{said}; back to {before['state']} as it was before the round"
                + ("; Resume continues the stopped round" if resumable else ""),
                history=hist,
            )
        reason = f"{said}: Retry builds it again" + (
            "; Resume continues the stopped round" if resumable else ""
        )
        return self.ledger.update(
            idea["id"],
            state="held",
            reason=reason,
            build=rec,
            gate=idea.get("gate") or {"verdict": "held", "reasons": [reason]},
            note=f"held: {reason}",
            history=hist,
        )

    # -- the pre-push checks, before anyone is asked (precheck.py) ------------------------------
    def may_wait(self, origin: str | None = None) -> bool:
        """Whether this call may wait on a model: the loop's own work and the command line may;
        a verb the UI, repo-factory mcp or a chat action ran (``--origin``) never does (the owner's
        rule, 2026-10-11)."""
        if os.environ.get("REPO_FACTORY_NO_WAIT"):  # a verb run for a request (App.run)
            return False
        o = origin if origin is not None else self.ledger.origin
        return o in (None, "cli", "honesty")

    def precheck(self, idea_id: str) -> dict:
        """Stage a held or ready build's tree, write its public text from the product and
        review it once (the model calls), and run every pre-push check on it now, kept in
        ``publish.precheck`` with the tree's key. Background work: the daemon's tick and
        ``repo-factory loop precheck``, never a request. Returns the record (another process's, still
        running, when it holds the idea's lock). A published build's release text is sent to
        GitHub instead (``text_check``)."""
        idea = self.ledger.must(idea_id)
        if (idea.get("publish") or {}).get("url"):
            return self.text_check(idea["id"])
        sha = pc.tree_sha(idea)  # one commit: staged, checked and keyed
        key = pc.tree_key(idea, self.cfg, sha)
        if key is None:
            raise LoopError(f"{idea['id']}: nothing was delivered: there is nothing to check")
        with pc.lock(self.cfg.home, idea["id"]) as mine:
            if not mine:
                return pc.record(self.ledger.must(idea["id"]))
            old = pc.record(idea)
            tries = int(old.get("attempts") or 0) if old.get("key") == key else 0
            run = {
                "key": key,
                "state": "running",
                "pid": os.getpid(),
                "started": time.time(),
                "start": proc.start_time(os.getpid()),
                "attempts": tries,
            }
            self._set_precheck(idea["id"], run)
            try:
                p, rec = self.cfg["publish"], idea.get("publish") or {}
                owner = p.get("owner") or rec.get("owner") or self.gh.login()
                name = rec.get("name") or pub.free_name(
                    self.gh, owner, pub.repo_name(idea["pitch"]["product"])
                )
                from .models import add, spend_kw, split

                seen: list = [None]  # the public text calls' tokens, as reported
                model = self.honesty_model

                def counted(text):
                    r = model(text)
                    seen[0] = add(seen[0], split(r)[2])
                    return r

                # the public text is written from the product and reviewed once, its
                # replacements applied (describe.py); then the scans: only facts hold
                staged = pub.stage(
                    idea,
                    self.cfg,
                    counted,
                    owner,
                    name,
                    self.cfg.layout.publish / idea["id"],
                    ref=sha,
                )
                self.ledger.spend("honesty", staged["usd"], idea["id"], **spend_kw(seen[0]))
                done = pub.finish(staged, self.cfg)
                self._charter_draft(idea["id"], key, done)
                out = {
                    **run,
                    **pc.kept(done),
                    "key": key,
                    "owner": owner,
                    "name": name,
                    "state": "done",
                    "finished": time.time(),
                    "attempts": 0,
                }
                said = ", ".join(k for k, _ in pc.holds(out)) or "nothing holds the push"
                log(f"{idea['id']} {idea['title']}: pre-push checks done: {said}")
            except Exception as e:  # noqa: BLE001 - kept, and tried again later
                out = {
                    **run,
                    "state": "failed",
                    "finished": time.time(),
                    "attempts": tries + 1,
                    "error": f"{type(e).__name__}: {e}"[:300],
                }
                log(f"{idea['id']} {idea['title']}: pre-push checks failed: {out['error']}")
            self._set_precheck(idea["id"], out)
            self._clear_if_clean(idea["id"], out)
            return out

    def text_check(self, idea_id: str) -> dict:
        """A published build's release text, put on GitHub (the owner's, as it is: the
        owner's decision, 2026-10-16, wording is never checked again): the repo's description
        and the release notes' first paragraph (``publish.update_release_text``). No model, no
        code, no file and no visibility changes. Background work: the command line, or a
        process the owner's Save started."""
        idea = self.ledger.must(idea_id)
        rec = dict(idea.get("publish") or {})
        if not rec.get("url"):
            raise LoopError(
                f"{idea['id']} has no repo: its release text goes out when it is published"
            )
        with pc.lock(self.cfg.home, idea["id"]) as mine:
            if not mine:
                return dict(rec.get("textcheck") or {})
            run = {
                "state": "running",
                "pid": os.getpid(),
                "started": time.time(),
                "start": proc.start_time(os.getpid()),
            }
            self._set_textcheck(idea["id"], run)
            try:
                text = cl.release_text(idea)
                was = rec.get("release_text") or (idea.get("pitch") or {}).get("one_liner")
                updated = None
                if text != was:
                    updated = pub.update_release_text(
                        self.gh,
                        rec["repo"],
                        (idea.get("pitch") or {}).get("product") or idea["title"],
                        text,
                    )
                    self.ledger.note(
                        idea["id"],
                        f'the release text on {rec["repo"]} was updated: "{text}" (was "{was}")',
                        {"kind": "release_text", "repo": rec["repo"]},
                    )
                out = {
                    **run,
                    "state": "done",
                    "finished": time.time(),
                    "text": text,
                    "issues": [],
                    "updated": updated,
                }
                log(
                    f"{idea['id']} {idea['title']}: release text "
                    + (f"updated on {rec['repo']}" if updated else "unchanged")
                )
            except Exception as e:  # noqa: BLE001 - kept, shown on the page
                out = {
                    **run,
                    "state": "failed",
                    "finished": time.time(),
                    "error": f"{type(e).__name__}: {e}"[:300],
                }
                log(f"{idea['id']} {idea['title']}: the release text update failed: {out['error']}")
            self._set_textcheck(idea["id"], out, out.get("text") if out.get("updated") else None)
            return out

    def _set_textcheck(self, idea_id: str, rec: dict, published: str | None = None) -> None:
        cur = dict(self.ledger.must(idea_id).get("publish") or {})
        cur["textcheck"] = rec
        if published:
            cur["release_text"] = published
        self.ledger.update(idea_id, publish=cur)

    def _clear_if_clean(self, idea_id: str, rec: dict) -> None:
        """A build held only because the pre-push checks stopped its push, whose checks now
        pass (a held build from before wording stopped holding, 2026-10-16, or a tree fixed
        since), is ready again: the loop publishes it privately, as any ready build."""
        idea = self.ledger.must(idea_id)
        if (
            rec.get("state") != "done"
            or pc.holds(rec)
            or idea["state"] != "held"
            or not str(idea.get("reason") or "").startswith("not pushed:")
            or (idea.get("gate") or {}).get("verdict") != "ready"
        ):
            return
        log(f"{idea['id']} {idea['title']}: the pre-push checks pass now: ready")
        self.ledger.update(
            idea_id,
            state="ready",
            reason=None,
            publish={**(idea.get("publish") or {}), "attempts": 0},
            note="the pre-push checks pass now (nothing holds the push): ready",
        )

    def _charter_draft(self, idea_id: str, key: str, done: dict) -> None:
        """PJ-PLAN.md §3.2: a ready build's charter is drafted here, in the background pre-push
        process, from the idea and the staged tree, once per tree (a Retry, a new round or a
        changed pitch drafts again); kept as ``publish.charter_draft`` until the publish makes
        the project. Never holds or fails the checks."""
        idea = self.ledger.must(idea_id)
        old = (idea.get("publish") or {}).get("charter_draft") or {}
        if idea["state"] != "ready" or old.get("key") == key or self.charter_model is None:
            return
        from ..projects.charter import draft_for_idea

        try:
            rec = draft_for_idea(
                self, idea, Path(done["stage"]) if done.get("stage") else None, self.charter_model
            )
        except Exception as e:  # noqa: BLE001 - the project waits for one
            rec = {"charter": None, "at": time.time(), "error": f"{type(e).__name__}: {e}"[:300]}
        cur = dict(self.ledger.must(idea_id).get("publish") or {})
        cur["charter_draft"] = {**rec, "key": key}
        self.ledger.update(idea_id, publish=cur)
        log(
            f"{idea_id} {idea['title']}: charter "
            + ("drafted" if rec.get("charter") else f"not drafted: {rec.get('error')}")
        )

    def _set_precheck(self, idea_id: str, rec: dict) -> None:
        cur = dict(self.ledger.must(idea_id).get("publish") or {})
        cur["precheck"] = rec
        if rec.get("state") == "done":
            cur.update(owner=rec["owner"], name=rec["name"])
        self.ledger.update(idea_id, publish=cur)

    def _precheck_bg(self, idea: dict) -> str:
        """The tick's way to run a build's pre-push checks: ``repo-factory loop precheck`` in a process
        of its own (``publish.precheck_concurrency`` at a time, default 1), never waited on.
        A ready build whose checks failed ``precheck.MAX_ATTEMPTS`` times on its tree is held
        with why. Returns what happened, in a word."""
        st = pc.status(idea, self.cfg)
        if st["state"] == "running":
            return "running"
        rec = st["record"]
        if (
            st["state"] == "failed"
            and idea["state"] == "ready"
            and int(rec.get("attempts") or 0) >= pc.MAX_ATTEMPTS
        ):
            reason = (
                f"the pre-push checks failed {rec.get('attempts')} times: "
                f"{rec.get('error') or 'no reason'}"
            )
            log(f"{idea['id']} {idea['title']}: held: {reason}")
            self.ledger.update(
                idea["id"],
                state="held",
                reason=reason,
                note=f"held: {reason}",
                history={"failure": True},
            )
            return "held"
        if not pc.due(idea, self.cfg):
            return "waiting"
        live = {i for i, pid in self._spawned.items() if builds.alive(pid)}
        live |= {
            i["id"]
            for i in self.ledger.all(("held", "ready"))
            if pc.record(i).get("state") == "running"
            and proc.running(pc.record(i).get("pid"), pc.record(i).get("start"))
        }
        if idea["id"] in live:  # started, not yet recorded as running
            return "running"
        if len(live) >= int(self.cfg["publish"].get("precheck_concurrency") or 1):
            return "queued"
        pid = pc.spawn(self.cfg, idea["id"])
        if not pid:
            return "off"
        self._spawned[idea["id"]] = pid
        log(f"{idea['id']} {idea['title']}: pre-push checks started (pid {pid})")
        return "started"

    def precheck_start(self, idea: dict) -> None:
        """A press found the checks not done: start them in the background unless they run."""
        if pc.status(idea, self.cfg)["state"] in ("running", "nothing"):
            return
        pid = pc.spawn(self.cfg, idea["id"])
        if pid:
            log(f"{idea['id']} {idea['title']}: pre-push checks started (pid {pid})")

    # -- re-check with current repo-factory (EX-PLAN §7) -----------------------------------------------
    def recheck(self, idea_id: str) -> dict:
        """The installed repo-factory's checks on a held or ready build's delivered tree, compared
        with the version that built it (``recheck.run``): ``gate.recheck`` and a history line.
        A held build's re-check that only turned holds into passes is its verdict (AU1 #3,
        ``recheck.clears``): the gate re-recorded, judged by this version, and the build ready
        when nothing else holds it; any other re-check changes nothing about the build. A press
        from the UI, MCP or the chat
        starts it detached (``recheck.spawn``) and returns at once; the command line runs it."""
        from . import recheck as rc

        idea = self.ledger.must(idea_id)
        if idea["state"] not in ("held", "ready"):
            raise LoopError(
                f"{idea['id']} is {idea['state']}; only a held or ready build is re-checked"
            )
        g = dict(idea.get("gate") or {})
        if not ((g.get("checks") or {}).get("delivered") or {}).get("ok"):
            raise LoopError(f"{idea['id']}: nothing was delivered: there is nothing to re-check")
        cur = g.get("recheck") or {}
        # another process's re-check, still running (the spawned one finds its own pid there:
        # the press recorded it; that one runs)
        if (
            cur.get("state") == "running"
            and cur.get("pid") != os.getpid()
            and proc.running(cur.get("pid"), cur.get("start"))
        ):
            return idea
        if not self.may_wait():
            pid = rc.spawn(self.cfg, idea["id"])
            g["recheck"] = {
                "state": "running",
                "pid": pid,
                "started": time.time(),
                "start": proc.start_time(pid) if pid else None,
                "version": rc.installed(),
            }
            log(
                f"{idea['id']} {idea['title']}: re-check with repo-factory {rc.installed()} started"
                + (f" (pid {pid})" if pid else "")
            )
            return self.ledger.update(idea["id"], gate=g)
        g["recheck"] = {
            "state": "running",
            "pid": os.getpid(),
            "started": time.time(),
            "start": proc.start_time(os.getpid()),
            "version": rc.installed(),
        }
        self.ledger.update(idea["id"], gate=g)
        try:
            rec = rc.run(idea, self.cfg)
        except Exception as e:  # noqa: BLE001 - recorded, shown
            rec = {
                "state": "failed",
                "version": rc.installed(),
                "at": time.time(),
                "error": f"{type(e).__name__}: {e}"[:300],
            }
        now = self.ledger.must(idea["id"])
        g = dict(now.get("gate") or {})
        g["recheck"] = rc.summary(rec)
        said = rc.words(rec) or f"re-check failed: {rec.get('error')}"
        won = rc.clears(g, rec) if now["state"] == "held" else None
        if won is None:
            log(f"{idea['id']} {idea['title']}: {said}")
            return self.ledger.update(idea["id"], gate=g, note=said)
        # AU1 #3: it only turned holds into passes: the verdict, judged by this version
        gate_mod.record(Path(idea["task_dir"]), won)
        said += f"; the verdict is recorded, judged by repo-factory {won['judged_by']}: " + (
            "ready to publish"
            if won["verdict"] == "ready"
            else "held: " + "; ".join(won["reasons"])
        )
        log(f"{idea['id']} {idea['title']}: {said}")
        if won["verdict"] == "ready":
            return self.ledger.update(idea["id"], state="ready", gate=won, reason=None, note=said)
        return self.ledger.update(idea["id"], gate=won, reason="; ".join(won["reasons"]), note=said)

    # -- publish -------------------------------------------------------------------------------
    def publish(self, idea_id: str) -> dict:
        idea = self.ledger.must(idea_id)
        if idea["state"] != "ready":
            raise LoopError(f"{idea['id']} is {idea['state']}; only a ready build is published")
        return self._publish(idea, wait=self.may_wait())

    def override_plan(self, idea: dict) -> tuple[dict, dict]:
        """``(gate.override_plan, the gate)`` for a held idea: the gate evaluated again on
        its delivered round, and the pre-push checks that would hold its push (its current
        ``publish.precheck``; none when they have not run on this tree). The gate is
        ``gate.now``: the evaluation the page's dialog shows (ASKF)."""
        rnd = (idea.get("gate") or {}).get("round") or (idea.get("build") or {}).get("round")
        g = gate_mod.judged(idea.get("task_dir"), rnd, idea.get("gate"))
        if not g.get("checks"):  # no verdict at all: judged on the records
            g = gate_mod.evaluate(Path(idea.get("task_dir") or "."), build=rnd)
        st = pc.status(idea, self.cfg)
        hs = pc.holds(st["record"]) if st["state"] == "done" else []
        may = (self.cfg["publish"].get("anyway") or {}).get("may_override")
        return gate_mod.override_plan(g["checks"], [k for k, _ in hs], may), g

    def publish_anyway(
        self,
        idea_id: str,
        reason: str,
        origin: str = "cli",
        yes: bool = False,
        holds: list[str] | None = None,
    ) -> dict:
        """The owner publishes a held build over its holds (AR-PLAN.md §4): into a **private**
        repo, with the override recorded and disclosed in its NOTICE and release notes. Only
        the holds in ``publish.anyway.may_override`` are overridden; nothing delivered,
        security, a secrets hit and a red bind_localhost never are. Going public is still the
        separate approve. ``yes``: the confirm (``--yes``); never run by a setting.

        Deterministic (PA): the holds are the gate's and the pre-push checks' that ran when
        the build stopped (``precheck``); a press from the UI, MCP or the chat runs no model
        and is refused while they run. ``holds``: the ones the owner was shown: refused when
        they are no longer exactly what it would override."""
        idea = self.ledger.must(idea_id)
        if not yes:
            raise LoopError(
                "publish anyway publishes over a hold: confirm it with --yes (it "
                "lands as a private repo)"
            )
        reason = " ".join(str(reason or "").split())
        if not 12 <= len(reason) <= 300:
            raise LoopError("publish anyway: say why, in 12 to 300 characters")
        if idea["state"] != "held":
            raise LoopError(
                f"{idea['id']} is {idea['state']}; only a held build is published anyway"
            )
        plan, g = self.override_plan(idea)
        if plan["refused"]:
            raise LoopError(
                "publish anyway refused: " + "; ".join(f"{k}: {why}" for k, why in plan["refused"])
            )
        st = pc.status(idea, self.cfg)
        if st["state"] != "done":
            if not self.may_wait(origin):
                self.precheck_start(idea)
                raise LoopError("publish anyway: " + pc.waiting(st))
            self.precheck(idea["id"])  # the command line waits for them
            idea = self.ledger.must(idea["id"])
            plan, g = self.override_plan(idea)
            if plan["refused"]:
                raise LoopError(
                    "publish anyway refused: "
                    + "; ".join(f"{k}: {why}" for k, why in plan["refused"])
                )
            if pc.status(idea, self.cfg)["state"] != "done":
                raise LoopError("publish anyway: " + pc.waiting(pc.status(idea, self.cfg)))
        if not plan["overrides"]:
            raise LoopError(
                f"{idea['id']} is not held by a check: there is nothing to "
                "publish over (Retry tries publishing again)"
            )
        names = [k for k, _ in plan["overrides"]]
        if holds is not None and sorted(set(holds)) != sorted(set(names)):
            raise LoopError(
                "publish anyway: the holds changed since you looked: it would now "
                f"publish over {', '.join(names)}; look again"
            )
        ov = {
            "reason": reason,
            "at": time.time(),
            "origin": origin,
            "overrides": names,
            "details": {k: d for k, d in plan["overrides"]},
            "round": g.get("round"),
        }
        self.ledger.update(idea["id"], gate=g)
        log(f"{idea['id']} {idea['title']}: publishing over a hold ({origin}): " + ", ".join(names))
        return self._publish(self.ledger.must(idea["id"]), override=ov, wait=False)

    def _publish(self, idea: dict, override: dict | None = None, wait: bool = True) -> dict:
        """Push a build whose pre-push checks are done (``precheck``): the deterministic half
        (``publish.finish``) runs again on the staged tree, then the push. ``wait``: the
        checks may run here first when they are not done (the loop's own tick, the command
        line); else the press is refused while they run (LoopError, with how long)."""
        p = self.cfg["publish"]
        st = pc.status(idea, self.cfg)
        if st["state"] != "done" and not wait:
            self.precheck_start(idea)
            raise LoopError(pc.waiting(st))
        rec = dict(idea.get("publish") or {})
        if override:
            rec["override"] = override
        try:
            if st["state"] != "done":
                ran = self.precheck(idea["id"])
                if ran.get("state") != "done":
                    raise pub.PublishError(
                        "the pre-push checks did not run: "
                        + str(ran.get("error") or ran.get("state"))
                    )
                idea = self.ledger.must(idea["id"])
                rec = {
                    **dict(idea.get("publish") or {}),
                    **({"override": override} if override else {}),
                }
                st = pc.status(idea, self.cfg)
            chk = st["record"]
            rec.update(owner=chk["owner"], name=chk["name"])
            prep = pub.finish({k: chk.get(k) for k in pc.KEEP}, self.cfg, override)
            rec.update(
                stage=prep["stage"],
                repo=prep["repo"],
                honesty=prep["honesty"],
                secrets=prep["secrets"][:20],
                jargon=prep["jargon"][:20],
                tidied=prep["tidied"],
                rea_scan=(prep.get("rea_scan") or [])[:20],
                studied=prep.get("studied") or [],
            )
            rec["described"] = prep.get("described")
            blocked = prep["blocked"]
            if override:  # the overridden pre-push checks; secrets never
                gone = {
                    k
                    for k, v in gate_mod.PREPUSH.items()
                    if v in override["overrides"] and v != "secrets"
                }
                blocked = [b for b in blocked if not any(b.startswith(k) for k in gone)]
            if blocked:  # only facts hold now: never wording
                reason = "not pushed: " + "; ".join(blocked)
                log(f"{idea['id']} {idea['title']}: held: {reason}")
                if override:  # what the press did, truly: nothing was pushed
                    rec.pop("override", None)
                    rec["anyway_tried"] = {**override, "outcome": "held"}
                    return self.ledger.update(
                        idea["id"],
                        state="held",
                        publish=rec,
                        reason=reason,
                        note=f"publish anyway ({override['origin']}) held again, nothing was "
                        f"pushed: {reason}",
                        history=anyway_hist(override, "held"),
                    )
                return self.ledger.update(
                    idea["id"], state="held", publish=rec, reason=reason, note=f"held: {reason}"
                )
            rec.update(pub.push(prep, idea, self.gh))
        except pub.PublishError as e:
            rec["attempts"] = int(rec.get("attempts", 0)) + 1
            rec["error"] = str(e)
            if override:  # the owner's press: held, and pressed again
                rec.pop("override", None)
                rec["anyway_tried"] = {**override, "outcome": "failed"}
                return self.ledger.update(
                    idea["id"],
                    state="held",
                    publish=rec,
                    reason=f"publishing over the hold failed: {e}",
                    note=f"held: publishing over the hold failed: {e}",
                    history={"failure": True, **anyway_hist(override, "failed")},
                )
            if rec["attempts"] >= int(_setting(p, "max_attempts", 3)):
                return self.ledger.update(
                    idea["id"],
                    state="held",
                    publish=rec,
                    reason=f"publishing failed: {e}",
                    note=f"held: publishing failed: {e}",
                    history={"failure": True},
                )
            return self.ledger.update(
                idea["id"], publish=rec, note=f"publishing failed: {e}", history={"failure": True}
            )
        where = f"{rec['url']} (release v{rec['version']})"
        if override:
            said = ", ".join(
                f"{k} ({d})" if d else k
                for k, d in (
                    (k, (override.get("details") or {}).get(k)) for k in override["overrides"]
                )
            )
            out = self.ledger.update(
                idea["id"],
                state="published",
                publish=rec,
                note=f"published over a hold by the owner ({override['origin']}): "
                f"{override['reason']} · overrode: {said} · privately: {where}",
                history=anyway_hist(override, "published"),
            )
        else:
            out = self.ledger.update(
                idea["id"],
                state="published",
                publish=rec,
                note=f"published privately: {where}; waiting for approval to go public",
            )
        log(f"{idea['id']} {idea['title']}: published privately: {rec['url']}")
        self._project_born(out)
        if p.get("approval") == "auto" and not override:  # over a hold: the owner decides
            return self.approve(idea["id"], auto=True)
        return out

    def approve(self, idea_id: str, auto: bool = False) -> dict:
        """The approval gate: make the private repo public. Only the owner calls this
        (``repo-factory loop approve``, the UI's button), unless ``publish.approval: auto``."""
        idea = self.ledger.must(idea_id)
        if idea["state"] == "ready":
            idea = self.publish(idea["id"])
            if auto or idea["state"] != "published":
                return idea
        if idea["state"] != "published":
            raise LoopError(
                f"{idea['id']} is {idea['state']}; only a published (private) repo "
                "can be approved to go public"
            )
        rec = dict(idea["publish"])
        pub.make_public(self.gh, rec["repo"])
        rec.update(
            visibility="public", approved_at=time.time(), approved_by="auto" if auto else "owner"
        )
        log(f"{idea['id']} {idea['title']}: public: {rec['url']}")
        out = self.ledger.update(
            idea["id"],
            state="public",
            publish=rec,
            note=f"approved ({'auto' if auto else 'by the owner'}): {rec['url']} is public",
        )
        self._project_born(out, public=True)
        return out

    def _project_born(self, idea: dict, public: bool = False) -> None:
        """A published build is a project (``projects.life.on_published``);
        never fails the publish."""
        try:
            from ..projects.life import on_published

            p = on_published(self, idea, public=public)
            if p:
                log(
                    f"{idea['id']} {idea['title']}: project {p['id']}"
                    + (f", charter v{p['charter_v']}" if p.get("charter_v") else "")
                )
        except Exception as e:  # noqa: BLE001 - the backfill makes it later
            log(f"{idea['id']} {idea['title']}: no project made: {type(e).__name__}: {e}")

    # -- maintenance (GitHub is the record; this only polls it) ----------------------------------
    def maintain(self) -> dict:
        """One poll of the maintained repos (``maintain.poll.maintain_tick``). A failure is
        logged and kept out of the rest of the tick."""
        from ..maintain.poll import maintain_tick

        try:
            out = maintain_tick(self)
        except Exception as e:  # noqa: BLE001 - the tick goes on
            self.ledger.meta("maintain_last_poll", time.time())
            log(f"maintenance poll failed: {type(e).__name__}: {e}")
            return {"error": f"{type(e).__name__}: {e}"}
        n = sum(len(r.get("triaged") or []) for r in out.get("repos") or [])
        if n:
            log(f"maintenance: triaged {n} issue(s) across {len(out['repos'])} repo(s)")
        for r in out.get("repos") or []:
            for k in (r.get("revise") or {}).get("started") or []:
                log(f"maintenance: fixing {r['repo']}#{k}")
            for e in (r.get("revise") or {}).get("ended") or []:
                log(
                    f"maintenance: the fix of {r['repo']}#{e['number']} ended: {e.get('outcome')}"
                    + (f" (PR #{e['pr']})" if e.get("pr") else "")
                )
            if (r.get("release") or {}).get("tag"):
                log(f"maintenance: released {r['repo']} {r['release']['tag']}")
        try:  # projects: charters
            from ..projects.life import tick as projects_tick

            out["projects"] = got = projects_tick(self)
            for pid in got["drafting"]:
                log(f"projects: drafting the charter of {pid} in the background")
            for e in got["errors"]:
                log(f"projects: {e}")
        except Exception as e:  # noqa: BLE001 - the tick goes on
            log(f"projects: {type(e).__name__}: {e}")
        return out

    def maintain_due(self) -> bool:
        m = self.cfg.get("maintain") or {}
        if not m.get("enabled", False):
            return False
        last = self.ledger.meta("maintain_last_poll") or 0
        return time.time() - last >= float(m.get("poll_s") or 900)

    # -- the kill switch -----------------------------------------------------------------------
    def pause(self) -> None:
        self.cfg.pause_file.write_text(f"paused {time.strftime('%Y-%m-%d %H:%M:%S')}\n")

    def resume(self) -> None:
        self.cfg.pause_file.unlink(missing_ok=True)

    def stop(self, grace_s: float = 20.0) -> list[str]:
        """Ask the daemon to exit and stop the running builds (their rounds resume later)."""
        self.cfg.stop_file.write_text(f"stop {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
        stopped = []
        for idea in self.building():
            rec = dict(idea.get("build") or {})
            rec["stopping"] = True
            self.ledger.update(idea["id"], build=rec)
            task = Path(idea["task_dir"])
            builds.stop(rec.get("pid"), grace_s, task)
            rec.update(round=rec.get("round") or builds.current_round(task))
            if rec.get("kill"):  # the owner's Stop round or Pause: as asked
                self._controlled(idea, task, rec)
            else:
                self._requeue(
                    idea,
                    task,
                    rec,
                    "stopped by repo-factory loop stop; the next repo-factory loop run resumes it",
                )
            stopped.append(idea["id"])
        return stopped

    # -- status --------------------------------------------------------------------------------
    def daemon_pid(self) -> int | None:
        p = self.cfg.layout.pid
        try:
            pid = int(p.read_text().strip())
        except (OSError, ValueError):
            return None
        return pid if builds.alive(pid) else None

    def status(self) -> dict:
        ideas = self.ledger.all()
        counts: dict[str, int] = {}
        for i in ideas:
            counts[i["state"]] = counts.get(i["state"], 0) + 1
        running = []
        for i in ideas:
            if i["state"] != "building":
                continue
            # a building idea whose task folder is not recorded (the column allows it): no state
            st = (builds.load_state(Path(i["task_dir"])) if i.get("task_dir") else None) or {}
            act = st.get("activity") or {}
            running.append(
                {
                    "id": i["id"],
                    "title": i["title"],
                    "task_dir": i["task_dir"],
                    "pid": (i.get("build") or {}).get("pid"),
                    "phase": act.get("phase"),
                    "stage": act.get("stage"),
                    "cost_usd": (act.get("cost") or {}).get("total"),
                    "started": (i.get("build") or {}).get("started"),
                }
            )
        return {
            "home": str(self.cfg.home),
            "paused": self.cfg.paused(),
            "stop_requested": self.cfg.stop_file.exists(),
            "daemon_pid": self.daemon_pid(),
            "concurrency": self.cfg["concurrency"],
            "daily_usd": self.cfg.get("daily_usd"),
            "settings_problems": getattr(self.cfg, "problems", []),
            "approval": self.cfg["publish"].get("approval"),
            "spend": self.spend(),
            # {"today", "week"}: each {"in", "out"} (builds' ledgers plus the loop's own
            # calls), None when no call in the window reported tokens
            "tokens": token_summary(self.ledger),
            "counts": counts,
            "running": running,
            "awaiting_approval": [
                {"id": i["id"], "title": i["title"], "url": (i.get("publish") or {}).get("url")}
                for i in ideas
                if i["state"] == "published"
            ],
            "held": [
                {"id": i["id"], "title": i["title"], "reason": i.get("reason")}
                for i in ideas
                if i["state"] == "held"
            ],
        }

    def tick(self, only: list[str] | None = None) -> dict:
        """One pass of the loop."""
        out: dict = {"watched": [i["id"] for i in self.watch()]}
        if time.time() - float(self.ledger.meta("activity_pruned") or 0) > 86400:
            try:  # the loop's marks past their keep, daily
                activity.prune(self.ledger)
            except Exception as e:  # noqa: BLE001 - the tick goes on
                log(f"activity: {type(e).__name__}: {e}")
            self.ledger.meta("activity_pruned", time.time())
        from .. import github_user as U

        renewed = U.ensure_fresh()  # repo-factory's GitHub sign-in, in its last half hour
        if renewed["state"] in ("refreshed", "failed"):
            out["github"] = renewed["state"]
        if self._gh is not None and U.stale(self._gh):
            self._gh = None  # signed out, or signed in again: re-read
        if _setting(self.cfg["publish"], "enabled", True):
            # the pre-push checks (a model call) run in a process of their own, never in the
            # tick: the tick stays within seconds. A ready build waits for the owner's Publish
            # once they are done (approval auto: publishes on a later tick); a held build's are
            # run so Publish anyway is decided from them (precheck.py).
            inline = os.environ.get(pc.INLINE_ENV) == "1"
            auto = _publishes_alone(self.cfg)
            for idea in self.ledger.all("ready"):
                if only and not any(idea["id"].startswith(o) for o in only):
                    continue
                if self._stop_flag:  # SIGTERM: start no long step
                    return out
                if pc.status(idea, self.cfg)["state"] != "done":
                    # run before the owner's press, so Publish never waits on a model
                    out.setdefault("prechecks", []).append(
                        self.precheck(idea["id"])["state"] if inline else self._precheck_bg(idea)
                    )
                    continue
                if not auto:  # waits for the owner's press
                    continue
                with activity.step(
                    self.ledger, "loop.publish", title=idea.get("title"), idea=idea["id"]
                ):
                    out.setdefault("published", []).append(self.publish(idea["id"])["state"])
            for idea in self.ledger.all("held"):
                if only and not any(idea["id"].startswith(o) for o in only):
                    continue
                if self._stop_flag:
                    return out
                if pc.due(idea, self.cfg):
                    out.setdefault("prechecks", []).append(
                        self.precheck(idea["id"])["state"] if inline else self._precheck_bg(idea)
                    )
        if self.cfg.paused() or self.cfg.stop_file.exists() or self._stop_flag:
            out["paused"] = True
            return out
        if self.over_cap():
            out["over_cap"] = True
            return out
        if self.maintain_due() and not self._stop_flag:
            with activity.step(self.ledger, "loop.maintain", always=False) as s:
                out["maintain"] = got = self.maintain()
                s.words, s.worked = _maintained(got)
                s.error = got.get("error")
        new = [
            i
            for i in self.ledger.all("new")
            if (not only or any(i["id"].startswith(o) for o in only))
            and not (i.get("pitch") or {}).get("cancelled")
        ]  # Cancel: until sent again
        new.sort(key=lambda i: not (i.get("pitch") or {}).get("sent"))  # sent ones first
        k = int(self.cfg["pitch_per_tick"])
        if new and k > 0:
            out["pitched"] = []
            for i in new[:k]:  # one activity run a task
                with activity.step(
                    self.ledger, "loop.pitch", title=i.get("title"), idea=i["id"]
                ) as s:
                    got = self.pitch([i["id"]])
                    out["pitched"] += [p["state"] for p in got]
                    why = str((got[-1] if got else {}).get("reason") or "")
                    if why.startswith("pitch failed"):  # counted on the idea, said here
                        s.error = why
        if not self.over_cap():
            out["started"] = [i["id"] for i in self.start_builds(only)]
        return out

    def idle(self, only: list[str] | None = None) -> bool:
        def mine(i):
            return not only or any(i["id"].startswith(o) for o in only)

        waiting = (
            ("building", "ready")
            if _setting(self.cfg["publish"], "enabled", True) and _publishes_alone(self.cfg)
            else ("building",)
        )
        busy = [i for i in self.ledger.all(waiting) if mine(i)]
        if self.cfg.paused():
            return not busy
        busy += [
            i
            for i in self.ledger.all(("queued", "new"))
            if mine(i) and not (i.get("pitch") or {}).get("cancelled")
        ]
        return not busy

    def run(
        self, once: bool = False, until_idle: bool = False, only: list[str] | None = None
    ) -> None:
        lock = open(self.cfg.layout.lock, "w")
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise LoopError(
                f"the loop is already running (pid {self.daemon_pid()}): another "
                "repo-factory loop run, or repo-factory ui"
            )
        self.cfg.stop_file.unlink(missing_ok=True)
        pidf = self.cfg.layout.pid
        pidf.write_text(f"{os.getpid()}\n")
        old = {s: signal.getsignal(s) for s in (signal.SIGTERM, signal.SIGINT)}

        def on_signal(signum, frame):
            self._stop_flag = True

        for s in old:
            signal.signal(s, on_signal)
        log(
            f"running (home {self.cfg.home}, tasks {self.cfg.tasks_dir}, concurrency "
            f"{self.cfg['concurrency']}{', paused' if self.cfg.paused() else ''})"
        )
        try:
            while not self._stop_flag:
                try:
                    self.tick(only)
                except SystemExit:
                    raise
                except Exception as e:  # noqa: BLE001 - the daemon keeps going
                    log(f"tick failed: {type(e).__name__}: {e}")
                if once or self.cfg.stop_file.exists():
                    break
                if until_idle and self.idle(only):
                    log("idle: nothing queued, building or waiting to publish")
                    break
                deadline = time.time() + float(self.cfg["tick_s"])
                while (
                    time.time() < deadline
                    and not self._stop_flag
                    and not self.cfg.stop_file.exists()
                ):
                    time.sleep(0.5)
        finally:
            for s, h in old.items():
                signal.signal(s, h)
            pidf.unlink(missing_ok=True)
            fcntl.flock(lock, fcntl.LOCK_UN)
            lock.close()
            log(
                "stopped (running builds keep going; the next repo-factory loop run adopts them)"
                if not self.cfg.stop_file.exists()
                else "stopped"
            )
            sys.stdout.flush()
