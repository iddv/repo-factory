"""The loop's ledger: every task (an "idea" row), its state and its history
(``<home>/loop.db``).

States, in order (``STATES``):

- ``new``: typed in, waiting for its brief (the pitch);
- ``rejected``: the brief found no buildable product (``reason``); ``repo-factory loop
  repitch`` re-opens it;
- ``skipped``: set aside without a brief (kept for rows that carry it);
- ``error``: the brief's call failed ``pitch.max_errors`` times (it can be tried again);
- ``queued``: accepted; its task exists (``task_dir``) and waits for a build slot;
- ``building``: ``repo-factory build`` is running (``build.pid``);
- ``paused``: no longer made (Pause build went into Stop round); a record with it had its
  build stopped with its round kept: Resume queues it, Stop round puts it back as it was;
- ``held``: the release gate said no, or the build failed twice, or the pre-push scan found
  something (``reason`` and ``gate.reasons``); ``repo-factory loop retry`` re-queues it;
- ``ready``: the gate passed; publishing (private) is next;
- ``published``: a private repo with a v0.1.0 release (``publish.url``);
- ``public``: the owner approved (``repo-factory loop approve``) and the repo is public;
- ``closed``: the owner said no (``repo-factory loop close``, with a reason; ``repo-factory loop reject`` with
  its reason chips): nothing more happens to it, and it leaves "needs you". A closed idea can
  be sent again (``repo-factory loop send``).

Two decisions are fields, not states: a ``published`` idea the owner keeps private
(``repo-factory loop keep``) has ``publish.kept`` and leaves "needs you" (approve can still make it
public); an open idea put off (``repo-factory loop later``) has ``later_until`` and is not pitched, nor
shown as waiting, until then.

Every change appends a ``history`` row: the feed, the digest and the UI read those. Spend
on the loop's own calls (pitches, summaries, honesty checks) is in ``spend``; build spend
is read from each task's ``.den/ledger.jsonl`` (``spend_between``). Each ``spend`` row also
has the call's ``in_tokens``/``out_tokens`` (NULL on rows from before they were recorded);
``token_summary`` sums tokens the way ``spend_summary`` sums dollars."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from urllib.parse import quote

from .. import migrate
from ..errors import LoopError
from ..stored import ideas as stored_ideas
from ..stored import kept
from ..stored.base import Stored

STATES = (
    "new",
    "rejected",
    "skipped",
    "error",
    "queued",
    "building",
    "paused",
    "held",
    "ready",
    "published",
    "public",
    "closed",
)
JSON_FIELDS = ("urls", "card", "pitch", "build", "gate", "publish", "signals")
# each JSON field's contract (``repo_factory.stored.ideas``): read and written through it
CONTRACTS: dict[str, type[Stored]] = {
    "urls": stored_ideas.Urls,
    "card": stored_ideas.IdeaCard,
    "pitch": stored_ideas.Pitch,
    "build": stored_ideas.Build,
    "gate": stored_ideas.Gate,
    "publish": stored_ideas.Publish,
    "signals": stored_ideas.Signals,
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS ideas (
  id TEXT PRIMARY KEY, fingerprint TEXT UNIQUE NOT NULL, source TEXT NOT NULL,
  source_ref TEXT, title TEXT, text TEXT NOT NULL, urls TEXT, context TEXT, card TEXT,
  state TEXT NOT NULL, reason TEXT, pitch TEXT, task_dir TEXT, build TEXT, gate TEXT,
  publish TEXT, signals TEXT, created REAL NOT NULL, updated REAL NOT NULL);
CREATE UNIQUE INDEX IF NOT EXISTS ideas_ref ON ideas(source_ref) WHERE source_ref IS NOT NULL;
CREATE TABLE IF NOT EXISTS history (
  id INTEGER PRIMARY KEY AUTOINCREMENT, idea TEXT NOT NULL, ts REAL NOT NULL,
  state TEXT NOT NULL, note TEXT, data TEXT);
CREATE TABLE IF NOT EXISTS spend (
  id INTEGER PRIMARY KEY AUTOINCREMENT, idea TEXT, ts REAL NOT NULL, kind TEXT NOT NULL,
  usd REAL NOT NULL);
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS projects (
  id TEXT PRIMARY KEY, name TEXT NOT NULL, kind TEXT NOT NULL, idea TEXT, repos TEXT NOT NULL,
  charter TEXT, charter_v INTEGER NOT NULL DEFAULT 0, topic TEXT, settings TEXT, stats TEXT,
  health TEXT, state TEXT NOT NULL DEFAULT 'active', created REAL NOT NULL,
  updated REAL NOT NULL);
CREATE TABLE IF NOT EXISTS charter_versions (
  project TEXT NOT NULL, v INTEGER NOT NULL, charter TEXT NOT NULL, by TEXT NOT NULL,
  note TEXT, at REAL NOT NULL, PRIMARY KEY (project, v));
CREATE TABLE IF NOT EXISTS changes (
  id TEXT PRIMARY KEY, project TEXT NOT NULL, fp TEXT NOT NULL, title TEXT NOT NULL,
  kind TEXT NOT NULL, class TEXT NOT NULL, state TEXT NOT NULL, card TEXT NOT NULL,
  filed TEXT, later_until REAL, created REAL NOT NULL, updated REAL NOT NULL,
  UNIQUE (project, fp));
"""
# ``projects``, ``charter_versions`` and ``changes`` are a project's record
# (``repo_factory/projects/``): a repo that keeps evolving from the idea it came from, its charter's
# versions (append-only) and the change ideas its loop proposes.
# Columns added after the table first shipped: (name, declaration), each by a guarded
# ALTER TABLE. ``kind`` is the task's output kind; ``later_until`` is when a task put off
# with Later comes back.
ADDED_COLUMNS = (("kind", "TEXT NOT NULL DEFAULT 'app'"), ("later_until", "REAL"))
# The same, for the spend table: the tokens of a call (input counts cache reads and writes),
# NULL on rows written before tokens were recorded (never made up from dollars); ``project``,
# the project a call was for (an evolve run, a charter draft, an adopted repo's fix), so its
# dollars count for the project even when no idea is behind it.
SPEND_ADDED_COLUMNS = (("in_tokens", "INTEGER"), ("out_tokens", "INTEGER"), ("project", "TEXT"))
# Numbered (``repo_factory/migrate.py``): 1 is the schema above, the added columns included.
# A new migration is the next number, and only adds.
# 2: retired (a table this version does not use); kept as a number so the order stays.
# 3: the loop's own runs as they go (``activity.py``): a mark per step of a
# run, ``started`` (the process that runs it) and ``ended`` (its state, its error, its spend),
# for the tick's steps that did work (a brief, a maintenance poll that acted, a publish). Builds, sessions and the detached jobs keep their own records.
ACTIVITY = """
CREATE TABLE IF NOT EXISTS activity (
  id INTEGER PRIMARY KEY AUTOINCREMENT, run TEXT NOT NULL, kind TEXT NOT NULL,
  ts REAL NOT NULL, mark TEXT NOT NULL, state TEXT, title TEXT, words TEXT, error TEXT,
  idea TEXT, project TEXT, repo TEXT, pid INTEGER, pstart INTEGER, usd REAL, detail TEXT);
CREATE INDEX IF NOT EXISTS activity_run ON activity(run, id);
CREATE INDEX IF NOT EXISTS activity_ts ON activity(ts);
"""
MIGRATIONS = [
    (
        1,
        SCHEMA,
        tuple(("ideas", n, d) for n, d in ADDED_COLUMNS)
        + tuple(("spend", n, d) for n, d in SPEND_ADDED_COLUMNS),
    ),
    (2, "", ()),
    (3, ACTIVITY, ()),
]

STOP = set(
    [
        "a",
        "an",
        "the",
        "and",
        "or",
        "of",
        "to",
        "for",
        "in",
        "on",
        "at",
        "by",
        "with",
        "from",
        "into",
        "is",
        "are",
        "be",
        "this",
        "that",
        "it",
        "its",
        "can",
        "could",
        "should",
        "would",
        "will",
        "just",
        "some",
        "something",
        "someone",
        "build",
        "create",
        "make",
        "app",
        "tool",
        "software",
    ]
)
URL = re.compile(r"https?://[^\s<>()\"']+")


def norm_url(u: str) -> str:
    """Scheme, ``www.``, a trailing slash, fragments and tracking parameters don't make a
    different idea."""
    u = u.strip().rstrip(".,;:)")
    u = re.sub(r"^https?://(www\.)?", "", u, flags=re.I).split("#")[0]
    if "?" in u:
        base, q = u.split("?", 1)
        keep = [p for p in q.split("&") if p and not re.match(r"(utm_\w+|ref|fbclid|gclid)=", p)]
        u = base + ("?" + "&".join(sorted(keep)) if keep else "")
    return u.rstrip("/").lower()


def normalise(text: str) -> str:
    """The idea's words without case, punctuation, URLs' scheme noise or filler words."""
    urls = sorted(norm_url(u) for u in URL.findall(text or ""))
    rest = URL.sub(" ", text or "").lower()
    words = [w for w in re.findall(r"[a-z0-9]+", rest) if w not in STOP]
    return " ".join(words + urls)


def fingerprint(text: str) -> str:
    return hashlib.sha256(normalise(text).encode()).hexdigest()


class Ledger:
    def __init__(self, path: str | Path, read_only: bool = False):
        """``read_only``: a reader that is not the loop opens the file as it is, never
        creating or migrating it."""
        self.path = Path(path)
        self.read_only = read_only
        # the door an owner's action came through (ui, cli, honesty: the registry's origins), kept in each history row this ledger writes; None: the loop's
        # own steps, which carry none
        self.origin: str | None = None
        if read_only:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.db() as c:
            migrate.apply(c, MIGRATIONS, "meta")

    @contextmanager
    def db(self) -> Iterator[sqlite3.Connection]:
        if self.read_only:
            c = sqlite3.connect(f"file:{quote(str(self.path))}?mode=ro", uri=True, timeout=30)
        else:
            c = sqlite3.connect(self.path, timeout=30)
        c.row_factory = sqlite3.Row
        try:
            if not self.read_only:
                migrate.wal(c)
            yield c
            c.commit()
        finally:
            c.close()

    # -- reading -----------------------------------------------------------------------------
    @staticmethod
    def _row(r: sqlite3.Row) -> dict[str, Any]:
        """An idea as a dict, each JSON field read through its contract: NULL, unreadable or
        not an object reads as ``{}`` (``urls`` as ``[]``), a declared field of the wrong type
        as its default (noted once), every other key as stored."""
        d = dict(r)
        for k, t in CONTRACTS.items():
            d[k] = kept(t, d.get(k))
        return d

    def get(self, idea_id: str) -> dict[str, Any] | None:
        with self.db() as c:
            r = c.execute(
                "SELECT * FROM ideas WHERE id = ? OR id LIKE ? ORDER BY id LIMIT 2",
                (idea_id, f"{idea_id}%"),
            ).fetchall()
        exact = [x for x in r if x["id"] == idea_id]
        if exact or len(r) == 1:
            return self._row((exact or r)[0])
        return None

    def must(self, idea_id: str) -> dict[str, Any]:
        d = self.get(idea_id)
        if d is None:
            raise LoopError(
                f"no idea {idea_id!r} in the loop's ledger (see repo-factory loop list)"
            )
        return d

    def all(self, state: str | tuple[str, ...] | None = None) -> list[dict[str, Any]]:
        q = "SELECT * FROM ideas"
        args: tuple[str, ...] = ()
        if state:
            states = (state,) if isinstance(state, str) else tuple(state)
            q += f" WHERE state IN ({','.join('?' * len(states))})"
            args = states
        with self.db() as c:
            return [self._row(r) for r in c.execute(q + " ORDER BY created, id", args)]

    def by_ref(self, ref: str) -> dict[str, Any] | None:
        with self.db() as c:
            r = c.execute("SELECT * FROM ideas WHERE source_ref = ?", (ref,)).fetchone()
        return self._row(r) if r else None

    def history(
        self,
        idea_id: str | None = None,
        since: float = 0.0,
        limit: int | None = None,
        ideas_only: bool = False,
    ) -> list[dict[str, Any]]:
        """History rows, newest first. ``ideas_only``: not a project's Timeline rows (their
        ``idea`` is a project's id, ``p…``; ``repo_factory/projects/record.py``)."""
        q = "SELECT * FROM history WHERE ts >= ?"
        args: list[Any] = [since]
        if idea_id:
            q += " AND idea = ?"
            args.append(idea_id)
        if ideas_only:
            q += " AND substr(idea, 1, 1) != 'p'"
        q += " ORDER BY id DESC" + (f" LIMIT {int(limit)}" if limit else "")
        with self.db() as c:
            rows = [dict(r) for r in c.execute(q, args)]
        for r in rows:
            r["data"] = kept(stored_ideas.HistoryData, r["data"])
        return rows

    # -- writing -----------------------------------------------------------------------------
    def add(
        self,
        text: str,
        source: str,
        *,
        title: str | None = None,
        source_ref: str | None = None,
        card: dict[str, Any] | None = None,
        fp_text: str | None = None,
        state: str = "new",
        reason: str | None = None,
        kind: str = "app",
    ) -> tuple[dict[str, Any], bool]:
        """Record an idea; ``(idea, created)``. A duplicate (same fingerprint, or the same
        ``source_ref``) is not added again: the existing idea comes back with False.
        ``kind`` is its output kind."""
        fp = fingerprint(fp_text or text)
        if not normalise(fp_text or text):
            raise ValueError("an idea needs some words")
        with self.db() as c:
            r = c.execute(
                "SELECT * FROM ideas WHERE fingerprint = ? OR (source_ref IS NOT NULL "
                "AND source_ref = ?)",
                (fp, source_ref),
            ).fetchone()
            if r:
                return self._row(r), False
            now = time.time()
            iid = "i" + fp[:8]
            n = 2
            while c.execute("SELECT 1 FROM ideas WHERE id = ?", (iid,)).fetchone():
                iid, n = f"i{fp[:8]}{n}", n + 1
            urls = sorted(set(URL.findall(text or "")))
            c.execute(
                "INSERT INTO ideas (id, fingerprint, source, source_ref, title, text, urls,"
                " card, state, reason, kind, created, updated) VALUES "
                "(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    iid,
                    fp,
                    source,
                    source_ref,
                    title or first_line(text),
                    text,
                    _stored("urls", urls),
                    _stored("card", card) if card else None,
                    state,
                    reason,
                    kind or "app",
                    now,
                    now,
                ),
            )
            self._hist(
                c,
                iid,
                state,
                f"taken in from {source}" + (f": {reason}" if reason else ""),
                {"source": source, "ref": source_ref},
            )
        return self.must(iid), True

    def update(
        self,
        idea_id: str,
        *,
        note: str | None = None,
        history: dict[str, Any] | None = None,
        **fields: Any,
    ) -> dict[str, Any]:
        """Set fields (dicts are stored as JSON); a new ``state`` (or a ``note``) appends a
        history row."""
        if "state" in fields and fields["state"] not in STATES:
            raise ValueError(f"unknown state {fields['state']!r}")
        with self.db() as c:
            old = c.execute("SELECT state FROM ideas WHERE id = ?", (idea_id,)).fetchone()
            if old is None:
                raise KeyError(idea_id)
            sets = {
                k: (_stored(k, v) if k in CONTRACTS and v is not None else v)
                for k, v in fields.items()
            }
            sets["updated"] = time.time()
            c.execute(
                f"UPDATE ideas SET {', '.join(f'{k} = ?' for k in sets)} WHERE id = ?",
                (*sets.values(), idea_id),
            )
            new = fields.get("state", old["state"])
            if new != old["state"] or note:
                self._hist(c, idea_id, new, note, history)
        return self.must(idea_id)

    def _note_quiet(self, idea_id: str, note: str) -> None:
        try:
            with self.db() as c:
                self._hist(c, idea_id, self.must(idea_id)["state"], note, None)
        except Exception:  # noqa: BLE001 - nothing more to do
            pass

    def note(self, idea_id: str, note: str, data: dict[str, Any] | None = None) -> None:
        d = self.must(idea_id)
        with self.db() as c:
            self._hist(c, idea_id, d["state"], note, data)

    def _hist(
        self,
        c: sqlite3.Connection,
        idea_id: str,
        state: str,
        note: str | None,
        data: dict[str, Any] | None,
    ) -> None:
        if self.origin and not (data or {}).get("origin"):
            data = {**(data or {}), "origin": self.origin}
        c.execute(
            "INSERT INTO history (idea, ts, state, note, data) VALUES (?,?,?,?,?)",
            (idea_id, time.time(), state, note, _history(data) if data else None),
        )

    # -- spend and meta ----------------------------------------------------------------------
    def spend(
        self,
        kind: str,
        usd: float | None,
        idea_id: str | None = None,
        *,
        in_tokens: int | None = None,
        out_tokens: int | None = None,
        project: str | None = None,
    ) -> None:
        """One call's spend: its dollars, and its tokens when the call reported them (NULL
        otherwise), and the project it was for. A call with neither is not recorded."""
        tin = int(in_tokens) if in_tokens is not None else None
        tout = int(out_tokens) if out_tokens is not None else None
        if usd or tin or tout:
            with self.db() as c:
                c.execute(
                    "INSERT INTO spend (idea, ts, kind, usd, in_tokens, out_tokens, "
                    "project) VALUES (?,?,?,?,?,?,?)",
                    (idea_id, time.time(), kind, float(usd or 0), tin, tout, project),
                )

    def own_tokens(
        self, start: float, end: float | None = None, kind: str | tuple[str, ...] | None = None
    ) -> dict[str, Any] | None:
        """The tokens of the loop's own calls between ``start`` and ``end``: ``{"in", "out"}``
        over the rows that carry tokens, or None when no row in the window does."""
        q = (
            "SELECT COUNT(*), COALESCE(SUM(in_tokens), 0), COALESCE(SUM(out_tokens), 0) "
            "FROM spend WHERE ts >= ? AND ts < ? AND (in_tokens IS NOT NULL OR "
            "out_tokens IS NOT NULL)"
        )
        args: list[Any] = [start, end or 1e18]
        if kind:
            kinds = (kind,) if isinstance(kind, str) else tuple(kind)
            q += f" AND kind IN ({','.join('?' * len(kinds))})"
            args.extend(kinds)
        with self.db() as c:
            r = c.execute(q, args).fetchone()
        return {"in": int(r[1]), "out": int(r[2])} if r[0] else None

    def own_spend(self, start: float, end: float | None = None, kind: str | None = None) -> float:
        q = "SELECT COALESCE(SUM(usd), 0) FROM spend WHERE ts >= ? AND ts < ?"
        args: list[Any] = [start, end or 1e18]
        if kind:
            q += " AND kind = ?"
            args.append(kind)
        with self.db() as c:
            r = c.execute(q, args).fetchone()
        return float(r[0])

    def metas(self, prefix: str) -> dict[str, Any]:
        """Every ``meta`` value whose key starts with ``prefix``, by the rest of the key."""
        with self.db() as c:
            rows = c.execute(
                "SELECT k, v FROM meta WHERE substr(k, 1, ?) = ?", (len(prefix), prefix)
            ).fetchall()
        return {r[0][len(prefix) :]: _meta_value(r[1]) for r in rows}

    def meta(self, k: str, v: Any = None) -> Any:
        with self.db() as c:
            if v is None:
                r = c.execute("SELECT v FROM meta WHERE k = ?", (k,)).fetchone()
                return _meta_value(r[0]) if r else None
            c.execute("INSERT OR REPLACE INTO meta (k, v) VALUES (?, ?)", (k, json.dumps(v)))
        return v


def _stored(k: str, v: Any) -> str:
    """A JSON field's text: a contract's own, or the writer's dict (a list for ``urls``) as
    it gave it, checked against the field's contract (a value the type cannot read is noted
    once). The dict is kept whole, nulls too: the loop's readers index keys a writer set to
    None (``rec["round"]``), which the contract's own text would leave out."""
    if isinstance(v, Stored):
        return v.to_json()
    text = json.dumps(v)
    CONTRACTS[k].parse(text)
    return text


def _meta_value(text: str | None) -> Any:
    """A ``meta`` value (any JSON, of its key's own shape: ``stored.OPAQUE``); text that is not
    JSON reads as None, never a raise."""
    try:
        return json.loads(text) if text else None
    except ValueError:
        return None


def _history(data: dict[str, Any]) -> str:
    """A history row's data: as the writer gave it, checked against ``HistoryData``."""
    text = json.dumps(data)
    stored_ideas.HistoryData.parse(text)
    return text


def first_line(text: str, n: int = 90) -> str:
    t = " ".join((text or "").split())
    m = re.match(r"(.+?[.!?])(\s|$)", t)
    t = m.group(1) if m else t
    return t if len(t) <= n else t[: n - 1].rstrip() + "…"


# The spend kinds of maintaining published repos (MAINTAIN.md §5): part of "loop" too.
MAINTAIN_KINDS = ("triage", "revise", "lesson", "adopt", "notes")  # lesson: §9; adopt, notes: MX
# The spend kinds of projects (PJ-PLAN.md §4): a charter draft and an evolve run.
PROJECT_KINDS = ("charter", "evolve")


def build_spend(task_dirs: list[str | Path], start: float, end: float | None = None) -> float:
    """Spend recorded in the tasks' cost ledgers (every model call of every build) between
    ``start`` and ``end``."""
    total = 0.0
    for d in task_dirs:
        p = Path(d) / ".den" / "ledger.jsonl"
        if not p.exists():
            continue
        for line in p.read_text(errors="replace").splitlines():
            try:
                r = json.loads(line)
                if not isinstance(r, dict) or not r.get("usd"):
                    continue  # a line that is not a call's record
                ts, usd = float(r.get("ts") or 0), float(r["usd"])
            except (json.JSONDecodeError, TypeError, ValueError):
                continue
            if start <= ts < (end or 1e18):
                total += usd
    return total


def tokens_of(r: dict[str, Any]) -> tuple[int, int] | None:
    """A ledger.jsonl line's ``(in_tokens, out_tokens)``, or None when it carries neither."""
    tin, tout = r.get("in_tokens"), r.get("out_tokens")
    if tin is None and tout is None:
        return None
    try:
        return int(tin or 0), int(tout or 0)
    except (TypeError, ValueError):
        return None


def add_tokens(
    a: dict[str, Any] | None, b: dict[str, Any] | tuple[Any, Any] | None
) -> dict[str, Any] | None:
    """Sum two token pairs (``{"in", "out"}`` or ``(in, out)``); None stays None only when
    both are None (no token data is not zero tokens)."""
    if b is None:
        return a
    bi, bo = (b["in"], b["out"]) if isinstance(b, dict) else b
    a = a or {"in": 0, "out": 0}
    return {"in": a["in"] + int(bi or 0), "out": a["out"] + int(bo or 0)}


def build_tokens(
    task_dirs: list[str | Path], start: float, end: float | None = None
) -> dict[str, Any] | None:
    """The tokens in the tasks' cost ledgers between ``start`` and ``end`` (windowed like
    ``build_spend``); None when no line in the window carries token fields."""
    total = None
    for d in task_dirs:
        p = Path(d) / ".den" / "ledger.jsonl"
        if not p.exists():
            continue
        for line in p.read_text(errors="replace").splitlines():
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(r, dict):
                continue
            try:
                ts = float(r.get("ts") or 0)
            except (TypeError, ValueError):
                continue  # a time that is not a number: not a call's record
            if start <= ts < (end or 1e18):
                total = add_tokens(total, tokens_of(r))
    return total


def tokens_between(ledger: Ledger, start: float, end: float | None = None) -> dict[str, Any] | None:
    """Builds' ledger tokens plus the loop's own calls' tokens in the window; None when
    neither has any token data."""
    tasks = [i["task_dir"] for i in ledger.all() if i.get("task_dir")]
    return add_tokens(build_tokens(tasks, start, end), ledger.own_tokens(start, end))


def token_summary(ledger: Ledger, now: float | None = None) -> dict[str, Any]:
    return {
        "today": tokens_between(ledger, day_start(now)),
        "week": tokens_between(ledger, week_start(now)),
    }


def day_start(now: float | None = None) -> float:
    t = time.localtime(now or time.time())
    return time.mktime((t.tm_year, t.tm_mon, t.tm_mday, 0, 0, 0, 0, 0, -1))


def week_start(now: float | None = None) -> float:
    now = now or time.time()
    return day_start(now) - time.localtime(now).tm_wday * 86400


def spend_between(ledger: Ledger, start: float, end: float | None = None) -> dict[str, Any]:
    tasks = [i["task_dir"] for i in ledger.all() if i.get("task_dir")]
    own = ledger.own_spend(start, end)
    builds = build_spend(tasks, start, end)
    return {
        "loop": round(own, 4),
        "builds": round(builds, 4),
        "total": round(own + builds, 4),
        "chat": round(ledger.own_spend(start, end, "chat"), 4),  # part of "loop"
        "maintain": round(sum(ledger.own_spend(start, end, k) for k in MAINTAIN_KINDS), 4),
    }


def spend_summary(ledger: Ledger, now: float | None = None) -> dict[str, Any]:
    return {
        "today": spend_between(ledger, day_start(now)),
        "week": spend_between(ledger, week_start(now)),
    }
