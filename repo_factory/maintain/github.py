"""The GitHub side of maintenance (MAINTAIN.md §1, §5): one product repository read and written
through the ``gh`` CLI's REST calls, and the poll's disposable cache.

``Repo`` reads issues, pull requests, labels, collaborators, the latest release and the CI state
of a commit, and writes labels and marker comments. Every write is idempotent: labels are added
or removed only when that changes something, and a comment is found by its ``<!-- repo-factory:… -->``
marker before one is posted. The ``gh`` is ``publish.Gh`` (its ``_run(args, cwd)`` is
replaceable), so tests use a fake and never the network.

``Cache`` is ``<loop home>/maintain/cache.json``: when each repo was last polled and what the
poll saw (the rows Today shows). It holds nothing that GitHub does not: delete it and the next
poll reads the last 30 days again and arrives at the same labels and comments."""

from __future__ import annotations

import json
import os
import re
import tempfile
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote

from .. import paths

COLD_DAYS = 30  # a cold cache reads issues updated in the last 30 days
SECRETS = re.compile(r"(^|/)secrets(/|$)")  # actions/, dependabot/, codespaces/, envs
BOTS = ("dependabot[bot]", "app/dependabot", "dependabot")
WRITERS = ("OWNER", "MEMBER", "COLLABORATOR")  # author_association with write on a user repo


class GitHubError(RuntimeError):
    pass


def iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def epoch(s: str | None) -> float | None:
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return None


def _pages(text: str) -> list:
    """``gh api --paginate`` prints one JSON document per page; join their lists."""
    out, dec, i, text = [], json.JSONDecoder(), 0, (text or "").strip()
    while i < len(text):
        doc, i = dec.raw_decode(text, i)
        while i < len(text) and text[i].isspace():
            i += 1
        if isinstance(doc, list):
            out.extend(doc)
        elif isinstance(doc, dict) and isinstance(doc.get("items"), list):
            out.extend(doc["items"])
        else:
            out.append(doc)
    return out


def issue(x: dict) -> dict:
    """An issue or pull request as the REST API returns it, in the fields maintenance uses."""
    user = x.get("user") or {}
    return {
        "number": x.get("number"),
        "title": x.get("title") or "",
        "body": x.get("body") or "",
        "labels": [lb["name"] if isinstance(lb, dict) else str(lb) for lb in x.get("labels") or []],
        "author": user.get("login") or "",
        "association": x.get("author_association") or "",
        "state": x.get("state") or "open",
        "created": epoch(x.get("created_at")),
        "updated": epoch(x.get("updated_at")),
        "comments": x.get("comments") or 0,
        "assignees": [a.get("login") for a in x.get("assignees") or []],
        "is_pr": "pull_request" in x or "head" in x,
        "url": x.get("html_url") or "",
        "draft": bool(x.get("draft")),
        "head": ((x.get("head") or {}).get("sha")) or None,
    }


def _commit(c: dict) -> dict:
    """A commit as the REST API returns it, in the fields ``current.py`` reads."""
    com = c.get("commit") or {}
    return {
        "sha": c.get("sha") or "",
        "parents": [p.get("sha") for p in c.get("parents") or []],
        "committer": (c.get("committer") or {}).get("login") or "",
        "committer_name": (com.get("committer") or {}).get("name") or "",
        "date": epoch((com.get("committer") or {}).get("date")),
        "message": com.get("message") or "",
    }


class Repo:
    """One repository, ``owner/name``, through ``gh`` (a ``publish.Gh``)."""

    def __init__(self, gh, full: str):
        if full.count("/") != 1 or not all(full.split("/")):
            raise GitHubError(f"not a repository name: {full!r}")
        self.gh, self.full = gh, full
        self.owner = full.split("/")[0]

    # -- the transport ---------------------------------------------------------------------
    def api(
        self,
        path: str,
        *,
        method: str = "GET",
        payload: dict | None = None,
        paginate: bool = False,
        raw: bool = False,
    ) -> str:
        if SECRETS.search(path.split("?")[0]):
            # MX-PLAN §3.6: repository and organisation secrets are never read, whoever asks
            raise GitHubError(f"repo-factory never reads secrets: {path.split('?')[0]}")
        args = ["api"]
        if method != "GET":
            args += ["-X", method]
        if paginate:
            args.append("--paginate")
        if raw:
            args += ["-H", "Accept: application/vnd.github.raw+json"]
        tmp = None
        if payload is not None:
            fd, tmp = tempfile.mkstemp(prefix="repo-factory-gh-", suffix=".json")
            with os.fdopen(fd, "w") as f:
                json.dump(payload, f)
            args += ["--input", tmp]
        try:
            code, out, err = self.gh._run([*args, path], None)
        finally:
            if tmp:
                os.unlink(tmp)
        if code != 0:
            raise GitHubError(
                f"gh api {method} {path.split('?')[0]}: {(err or out or '').strip()[:300]}"
            )
        return out

    def get(self, path: str, paginate: bool = False):
        out = self.api(path, paginate=paginate)
        return _pages(out) if paginate else (json.loads(out) if out.strip() else None)

    # -- reading ---------------------------------------------------------------------------
    def issues(
        self, labels: str | None = None, state: str = "open", since: float | None = None
    ) -> list[dict]:
        """Issues **and** pull requests (the issues API lists both), newest first."""
        q = f"repos/{self.full}/issues?state={state}&per_page=100&sort=updated"
        if labels:
            q += "&labels=" + quote(labels, safe=",")
        if since:
            q += "&since=" + iso(since)
        return [issue(x) for x in self.get(q, paginate=True)]

    def issues_since(self, since: float | None, now: float | None = None) -> list[dict]:
        """Open and closed issues and PRs updated since ``since`` (a cold cache: the last
        ``COLD_DAYS`` days)."""
        since = since or (now or time.time()) - COLD_DAYS * 86400
        return self.issues(state="all", since=since)

    def prs(self) -> list[dict]:
        return [
            issue(x)
            for x in self.get(f"repos/{self.full}/pulls?state=open&per_page=100", paginate=True)
        ]

    def labels(self) -> list[str]:
        return [
            x["name"] for x in self.get(f"repos/{self.full}/labels?per_page=100", paginate=True)
        ]

    def collaborators(self) -> set[str]:
        """Logins with write (push) access: the trusted fact ``auto_go`` checks, never the
        text of an issue."""
        rows = self.get(
            f"repos/{self.full}/collaborators?affiliation=all&per_page=100", paginate=True
        )
        return {
            x["login"]
            for x in rows
            if (x.get("permissions") or {}).get("push") or (x.get("permissions") or {}).get("admin")
        }

    def latest_release(self) -> dict | None:
        try:
            r = self.get(f"repos/{self.full}/releases/latest")
        except GitHubError as e:
            if "404" in str(e) or "Not Found" in str(e):
                return None
            raise
        return (
            {
                "tag": r.get("tag_name"),
                "name": r.get("name"),
                "url": r.get("html_url"),
                "published": epoch(r.get("published_at")),
            }
            if r
            else None
        )

    def ci_status(self, ref: str = "main") -> dict:
        """The check runs on ``ref`` folded into one word: ``success`` (every run finished
        green), ``failure``, ``pending`` or ``none`` (no CI ran, e.g. Actions is off)."""
        r = (
            self.get(f"repos/{self.full}/commits/{quote(ref, safe='')}/check-runs?per_page=100")
            or {}
        )
        runs = r.get("check_runs") or []
        if not runs:
            return {"state": "none", "runs": 0, "updated": None, "url": None}
        done = [x for x in runs if x.get("status") == "completed"]
        bad = [x for x in done if x.get("conclusion") not in ("success", "neutral", "skipped")]
        state = "failure" if bad else "pending" if len(done) < len(runs) else "success"
        upd = max((epoch(x.get("completed_at") or x.get("started_at")) or 0) for x in runs)
        pick = (bad or runs)[0]
        return {
            "state": state,
            "runs": len(runs),
            "updated": upd or None,
            "url": pick.get("html_url"),
            "failed": [x.get("name") for x in bad],
        }

    def search(self, q: str) -> list[dict]:
        r = self.get("search/issues?per_page=100&q=" + quote(f"repo:{self.full} {q}")) or {}
        return [issue(x) for x in r.get("items") or []]

    def comments(self, number: int) -> list[dict]:
        rows = self.get(
            f"repos/{self.full}/issues/{int(number)}/comments?per_page=100", paginate=True
        )
        return [
            {
                "id": c.get("id"),
                "body": c.get("body") or "",
                "author": (c.get("user") or {}).get("login") or "",
                "created": epoch(c.get("created_at")),
                "updated": epoch(c.get("updated_at")),
            }
            for c in rows
        ]

    def pr_diff(self, number: int, limit: int = 60000) -> str:
        """A pull request's diff (unified), cut at ``limit`` characters."""
        args = [
            "api",
            "-H",
            "Accept: application/vnd.github.diff",
            f"repos/{self.full}/pulls/{int(number)}",
        ]
        code, out, err = self.gh._run(args, None)
        if code != 0:
            raise GitHubError(
                f"gh api GET repos/{self.full}/pulls/{int(number)} (diff): "
                f"{(err or out or '').strip()[:300]}"
            )
        return out[:limit]

    def tree(self, ref: str = "main") -> list[dict]:
        """Every path on ``ref``: ``[{"path", "type": blob|tree}]`` (one call)."""
        r = self.get(f"repos/{self.full}/git/trees/{quote(ref, safe='')}?recursive=1") or {}
        return [
            {"path": x.get("path"), "type": x.get("type")}
            for x in r.get("tree") or []
            if x.get("path")
        ]

    def file(self, path: str, ref: str = "main") -> str | None:
        """One file's text on ``ref``; None when it is not there."""
        try:
            return self.api(
                f"repos/{self.full}/contents/{quote(path)}?ref={quote(ref, safe='')}", raw=True
            )
        except GitHubError as e:
            if "404" in str(e) or "Not Found" in str(e):
                return None
            raise

    def head(self, branch: str = "main", etag: str | None = None) -> dict:
        """The branch's head commit, asked conditionally: ``{"sha", "etag", "changed"}``. With
        the ``etag`` of the last answer, an unchanged branch is GitHub's 304, which costs no
        rate limit (``sha`` None, ``changed`` False). GitHub cannot push to a loopback-only
        repo-factory, so this is how every poll learns that main moved."""
        args = ["api", "-i", "-H", "Accept: application/vnd.github.sha"]
        if etag:
            args += ["-H", f"If-None-Match: {etag}"]
        code, out, err = self.gh._run(
            [*args, f"repos/{self.full}/commits/{quote(branch, safe='')}"], None
        )
        head, _, body = (
            (out or "").replace("\r\n", "\n").partition("\n\n")
            if (out or "").startswith("HTTP/")
            else ("", "", out or "")
        )
        status = head.split("\n", 1)[0]
        if " 304" in status or (code != 0 and "HTTP 304" in (err or "")):
            return {"sha": None, "etag": etag, "changed": False}
        if code != 0:
            raise GitHubError(
                f"gh api GET repos/{self.full}/commits/{branch}: {(err or out or '').strip()[:300]}"
            )
        tag = next(
            (
                ln.split(":", 1)[1].strip()
                for ln in head.split("\n")
                if ln.lower().startswith("etag:")
            ),
            None,
        )
        body = body.strip()
        sha = body if re.fullmatch(r"[0-9a-f]{40}", body) else None
        if sha is None:
            try:
                sha = (json.loads(body) or {}).get("sha")
            except (ValueError, AttributeError):
                sha = None
        if not sha:
            raise GitHubError(f"gh api GET repos/{self.full}/commits/{branch}: no commit")
        return {"sha": sha, "etag": tag, "changed": True}

    def readme(self, limit: int = 6000) -> str:
        try:
            return self.api(f"repos/{self.full}/readme", raw=True)[:limit]
        except GitHubError:
            return ""

    # -- the chat's snapshot (read calls only) ---------------------------------------------
    def summary(self) -> dict:
        r = self.get(f"repos/{self.full}") or {}
        return {
            k: r.get(k)
            for k in (
                "full_name",
                "description",
                "visibility",
                "default_branch",
                "stargazers_count",
                "forks_count",
                "open_issues_count",
                "pushed_at",
                "html_url",
                "archived",
            )
        }

    def commits(self, n: int = 20) -> list[dict]:
        rows = self.get(f"repos/{self.full}/commits?per_page={int(n)}") or []
        return [
            {
                "sha": (c.get("sha") or "")[:7],
                "date": ((c.get("commit") or {}).get("author") or {}).get("date"),
                "author": (
                    (c.get("author") or {}).get("login")
                    or ((c.get("commit") or {}).get("author") or {}).get("name")
                    or ""
                ),
                "message": ((c.get("commit") or {}).get("message") or "").split("\n", 1)[0],
            }
            for c in rows[:n]
            if isinstance(c, dict)
        ]

    def failed_run(self) -> dict | None:
        """The newest workflow run that failed, or None."""
        r = self.get(f"repos/{self.full}/actions/runs?status=failure&per_page=1") or {}
        runs = r.get("workflow_runs") or []
        if not runs:
            return None
        x = runs[0]
        return {
            "id": x.get("id"),
            "name": x.get("name"),
            "branch": x.get("head_branch"),
            "sha": (x.get("head_sha") or "")[:7],
            "event": x.get("event"),
            "conclusion": x.get("conclusion"),
            "created": x.get("created_at"),
            "url": x.get("html_url"),
        }

    def run_log_failed(self, run_id: int, lines: int = 200) -> str:
        """The failed steps' log of a workflow run (``gh run view --log-failed``), its last
        ``lines`` lines."""
        code, out, err = self.gh._run(
            ["run", "view", str(int(run_id)), "--repo", self.full, "--log-failed"], None
        )
        if code != 0:
            raise GitHubError(f"gh run view {int(run_id)}: {(err or out or '').strip()[:300]}")
        return "\n".join(out.splitlines()[-lines:])

    # -- writing (idempotent) --------------------------------------------------------------
    def set_labels(self, number: int, add=(), remove=(), have=None) -> dict:
        """Add and remove labels; nothing is sent for a label already right. ``have``: the
        issue's labels as read (else they are read here)."""
        have = set(have if have is not None else self.issue(number)["labels"])
        add = [x for x in dict.fromkeys(add) if x not in have]
        remove = [x for x in dict.fromkeys(remove) if x in have and x not in add]
        if add:
            self.api(
                f"repos/{self.full}/issues/{int(number)}/labels",
                method="POST",
                payload={"labels": add},
            )
        for x in remove:
            try:
                self.api(
                    f"repos/{self.full}/issues/{int(number)}/labels/{quote(x, safe='')}",
                    method="DELETE",
                )
            except GitHubError as e:  # already gone: the end state is the same
                if "404" not in str(e) and "Not Found" not in str(e):
                    raise
        return {"added": add, "removed": remove}

    def issue(self, number: int) -> dict:
        return issue(self.get(f"repos/{self.full}/issues/{int(number)}"))

    def find_marker(self, number: int, marker: str, comments: list | None = None) -> dict | None:
        for c in comments if comments is not None else self.comments(number):
            if marker in c["body"]:
                return c
        return None

    def comment_with_marker(
        self,
        number: int,
        marker: str,
        body: str,
        *,
        edit: bool = True,
        comments: list | None = None,
    ) -> dict:
        """The one comment carrying ``marker`` on the issue: edited when it exists (or left
        alone with ``edit=False``), posted when it does not. ``body`` must contain the
        marker, so the next call finds it."""
        if marker not in body:
            raise ValueError("the comment must carry its marker")
        old = self.find_marker(number, marker, comments)
        if old:
            if edit and old["body"].strip() != body.strip():
                self.api(
                    f"repos/{self.full}/issues/comments/{old['id']}",
                    method="PATCH",
                    payload={"body": body},
                )
                return {"id": old["id"], "action": "edited"}
            return {"id": old["id"], "action": "unchanged"}
        r = json.loads(
            self.api(
                f"repos/{self.full}/issues/{int(number)}/comments",
                method="POST",
                payload={"body": body},
            )
            or "{}"
        )
        return {"id": r.get("id"), "action": "posted"}

    def delete_comment(self, comment_id: int) -> None:
        try:
            self.api(f"repos/{self.full}/issues/comments/{int(comment_id)}", method="DELETE")
        except GitHubError as e:  # already gone: the end state is the same
            if "404" not in str(e) and "Not Found" not in str(e):
                raise

    def close(self, number: int, reason: str = "not_planned") -> None:
        self.api(
            f"repos/{self.full}/issues/{int(number)}",
            method="PATCH",
            payload={"state": "closed", "state_reason": reason},
        )

    def merge(self, number: int, title: str | None = None, sha: str | None = None) -> None:
        """Squash-merge; with ``sha``, only while that is still the head (GitHub refuses
        otherwise, so a push after the checks can never slip through)."""
        self.api(
            f"repos/{self.full}/pulls/{int(number)}/merge",
            method="PUT",
            payload={
                "merge_method": "squash",
                **({"commit_title": title} if title else {}),
                **({"sha": sha} if sha else {}),
            },
        )

    # -- repo-factory's own pull requests: kept current, and merged on green (current.py, automerge.py)
    def pull(self, number: int) -> dict:
        """One pull request with what the REST list leaves out: the head commit and branch,
        whose repository the branch is in, mergeability and the diff's size."""
        x = self.get(f"repos/{self.full}/pulls/{int(number)}") or {}
        head, base = x.get("head") or {}, x.get("base") or {}
        return {
            **issue(x),
            "head": head.get("sha"),
            "head_ref": head.get("ref") or "",
            "head_repo": ((head.get("repo") or {}).get("full_name")) or "",
            "base_ref": base.get("ref") or "",
            "mergeable": x.get("mergeable"),
            "mergeable_state": x.get("mergeable_state") or "",
            "additions": int(x.get("additions") or 0),
            "deletions": int(x.get("deletions") or 0),
            "changed_files": int(x.get("changed_files") or 0),
            "merged": bool(x.get("merged")),
        }

    def pull_changes(self, number: int) -> dict[str, int]:
        """``{file: lines changed (added plus removed)}`` of a pull request."""
        return {
            f.get("filename") or "": int(f.get("changes") or 0)
            for f in self.get(
                f"repos/{self.full}/pulls/{int(number)}/files?per_page=100", paginate=True
            )
        }

    def reviews(self, number: int) -> list[dict]:
        return [
            {
                "user": (x.get("user") or {}).get("login") or "",
                "state": x.get("state") or "",
                "at": epoch(x.get("submitted_at")),
            }
            for x in self.get(
                f"repos/{self.full}/pulls/{int(number)}/reviews?per_page=100", paginate=True
            )
        ]

    def pull_commits(self, number: int) -> list[dict]:
        return [
            _commit(c)
            for c in self.get(
                f"repos/{self.full}/pulls/{int(number)}/commits?per_page=100", paginate=True
            )
        ]

    def commit(self, sha: str) -> dict:
        return _commit(self.get(f"repos/{self.full}/commits/{quote(sha, safe='')}") or {})

    def behind(self, head: str, base: str = "main") -> int:
        """How many commits ``base`` has that ``head`` does not (0: up to date)."""
        r = (
            self.get(
                f"repos/{self.full}/compare/{quote(base, safe='')}..."
                f"{quote(head, safe='')}?per_page=1"
            )
            or {}
        )
        return int(r.get("behind_by") or 0)

    def update_branch(self, number: int, expected_head: str) -> None:
        """GitHub's *Update branch*: main merged into the pull request's branch, only while
        ``expected_head`` is its head. A conflict is a 422."""
        self.api(
            f"repos/{self.full}/pulls/{int(number)}/update-branch",
            method="PUT",
            payload={"expected_head_sha": expected_head},
        )

    def edit_pr(self, number: int, **fields) -> None:
        """``body``, ``title`` or ``state`` (``closed``) of a pull request."""
        self.api(f"repos/{self.full}/pulls/{int(number)}", method="PATCH", payload=fields)

    def put_file(
        self,
        path: str,
        text: str,
        branch: str,
        message: str,
        sha: str | None = None,
        author: dict | None = None,
    ) -> dict:
        """One file written on ``branch`` through the contents API (a commit)."""
        import base64

        r = json.loads(
            self.api(
                f"repos/{self.full}/contents/{quote(path)}",
                method="PUT",
                payload={
                    "message": message,
                    "branch": branch,
                    "content": base64.b64encode(text.encode()).decode(),
                    **({"sha": sha} if sha else {}),
                    **({"author": author, "committer": author} if author else {}),
                },
            )
            or "{}"
        )
        return {"commit": ((r.get("commit") or {}).get("sha")) or None}

    def blob_sha(self, path: str, ref: str) -> str | None:
        try:
            r = self.get(f"repos/{self.full}/contents/{quote(path)}?ref={quote(ref, safe='')}")
        except GitHubError as e:
            if "404" in str(e) or "Not Found" in str(e):
                return None
            raise
        return (r or {}).get("sha")

    def set_branch(self, branch: str, sha: str) -> None:
        """``refs/heads/<branch>`` at ``sha``: created, or moved there (forced) when it
        exists. Only ever a branch repo-factory owns (``repo-factory/…``)."""
        if not branch.startswith("repo-factory/"):
            raise GitHubError(
                f"repo-factory only writes its own repo-factory/ branches, not {branch}"
            )
        try:
            self.api(
                f"repos/{self.full}/git/refs",
                method="POST",
                payload={"ref": f"refs/heads/{branch}", "sha": sha},
            )
        except GitHubError as e:
            if "already exists" not in str(e) and "422" not in str(e):
                raise
            self.api(
                f"repos/{self.full}/git/refs/heads/{quote(branch, safe='/')}",
                method="PATCH",
                payload={"sha": sha, "force": True},
            )

    # -- revise and release (M3) -----------------------------------------------------------
    def create_issue(self, title: str, body: str, labels=()) -> dict:
        r = json.loads(
            self.api(
                f"repos/{self.full}/issues",
                method="POST",
                payload={"title": title, "body": body, "labels": list(labels)},
            )
            or "{}"
        )
        return {"number": r.get("number"), "url": r.get("html_url")}

    def create_pr(
        self, title: str, head: str, body: str, base: str = "main", draft: bool = False
    ) -> dict:
        r = json.loads(
            self.api(
                f"repos/{self.full}/pulls",
                method="POST",
                payload={"title": title, "head": head, "base": base, "body": body, "draft": draft},
            )
            or "{}"
        )
        return {"number": r.get("number"), "url": r.get("html_url")}

    def ready(self, number: int) -> None:
        """A draft pull request marked ready for review (``gh pr ready``: GraphQL's
        ``markPullRequestReadyForReview``)."""
        code, out, err = self.gh._run(["pr", "ready", str(int(number)), "--repo", self.full], None)
        if code != 0:
            raise GitHubError(f"gh pr ready {int(number)}: {(err or out or '').strip()[:300]}")

    def request_reviewers(self, number: int, who: list[str]) -> None:
        users = [x for x in who if "/" not in x]
        teams = [x.split("/", 1)[1] for x in who if "/" in x]
        self.api(
            f"repos/{self.full}/pulls/{int(number)}/requested_reviewers",
            method="POST",
            payload={"reviewers": users, "team_reviewers": teams},
        )

    def tags(self) -> list[dict]:
        return [
            {"name": t.get("name"), "sha": (t.get("commit") or {}).get("sha")}
            for t in self.get(f"repos/{self.full}/tags?per_page=100", paginate=True)
        ]

    def compare(self, base: str, head: str = "main") -> list[dict]:
        """The commits on ``head`` since ``base``, oldest first: ``[{"sha", "message"}]``."""
        r = (
            self.get(f"repos/{self.full}/compare/{quote(base, safe='')}...{quote(head, safe='')}")
            or {}
        )
        return [
            {"sha": c.get("sha"), "message": (c.get("commit") or {}).get("message") or ""}
            for c in r.get("commits") or []
        ]

    def commit_pulls(self, sha: str) -> list[dict]:
        """The merged pull requests a commit on main came from."""
        return [issue(x) for x in self.get(f"repos/{self.full}/commits/{sha}/pulls") or []]

    def create_release(self, tag: str, target: str = "main") -> dict:
        r = json.loads(
            self.api(
                f"repos/{self.full}/releases",
                method="POST",
                payload={
                    "tag_name": tag,
                    "name": tag,
                    "target_commitish": target,
                    "generate_release_notes": True,
                },
            )
            or "{}"
        )
        return {"tag": r.get("tag_name") or tag, "url": r.get("html_url")}


# -- the poll's cache --------------------------------------------------------------------------
class Cache:
    """``<loop home>/maintain/cache.json``: ``{"repos": {full: {...}}}``. Disposable."""

    def __init__(self, home: Path):
        self.path = paths.Layout(home).maintain / "cache.json"
        self.lock = threading.Lock()

    def read(self) -> dict:
        try:
            d = json.loads(self.path.read_text())
            return d if isinstance(d, dict) and isinstance(d.get("repos"), dict) else {"repos": {}}
        except (OSError, ValueError):
            return {"repos": {}}

    def repo(self, full: str) -> dict:
        return dict(self.read()["repos"].get(full) or {})

    def write_repo(self, full: str, rec: dict) -> None:
        with self.lock:
            d = self.read()
            d["repos"][full] = rec
            d["written"] = time.time()
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_name(f".cache.{os.getpid()}.tmp")
            tmp.write_text(json.dumps(d, indent=1, sort_keys=True))
            os.replace(tmp, self.path)

    def drop_row(self, full: str, kind: str, number: int) -> None:
        """An action from repo-factory (Try again, Close) took a row off: drop it now rather than at
        the next poll."""
        rec = self.repo(full)
        if rec:
            rec["rows"] = [
                r
                for r in rec.get("rows") or []
                if not (r.get("kind") == kind and r.get("number") == number)
            ]
            self.write_repo(full, rec)


PENDING = object()  # ``Reader.cached``'s default: no answer yet, one is being fetched


class Reader:
    """GitHub reads for the UI: the Maintenance box, Settings › Publishing › Maintenance and
    Settings › System › Learning.

    A page never waits on GitHub for an answer it has seen before (SL): ``cached`` returns
    the last answer at once, and one older than ``ttl`` seconds (``maintain.cache_s``) is
    fetched again on a background thread, one fetch per key at a time. With ``path``, the
    answers are also kept in that file (``<loop home>/maintain/reads.json``, disposable, no
    token in it), so a restart serves them too. An answer never seen is fetched in the
    request, unless the caller gives a ``default``: then it waits at most ``first_wait``
    seconds for the background fetch and returns the default if it is not back."""

    first_wait = 2.0

    def __init__(self, gh, ttl: float = 300, path: Path | None = None, ns: str = ""):
        self.gh, self.ttl, self.path, self.ns = gh, float(ttl), path, ns
        self._hits: dict = {}
        self._busy: dict = {}
        self.lock = threading.Lock()
        self._load()

    def _load(self) -> None:
        if self.path is None:
            return
        try:
            d = json.loads(self.path.read_text())
        except (OSError, ValueError):
            return
        if not isinstance(d, dict) or d.get("ns") != self.ns:
            return
        for row in d.get("hits") or []:
            try:
                k, at, value = row
                self._hits[tuple(k)] = (float(at), value, None)
            except (TypeError, ValueError):
                continue

    def _save(self) -> None:
        if self.path is None:
            return
        with self.lock:
            rows = [[list(k), at, v] for k, (at, v, err) in self._hits.items() if err is None]
        try:
            text = json.dumps({"ns": self.ns, "hits": rows})
        except (TypeError, ValueError):
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_name(f".reads.{os.getpid()}.{threading.get_ident()}.tmp")
            tmp.write_text(text)
            os.replace(tmp, self.path)
        except OSError:
            pass

    def _fetch(self, key: tuple, fn) -> None:
        try:
            value, err = fn(), None
        except Exception as e:  # noqa: BLE001 - raised to the next reader
            value, err = None, e
        with self.lock:
            self._hits[key] = (time.time(), value, err)
            done = self._busy.pop(key, None)
        if err is None:
            self._save()
        if done is not None:
            done.set()

    def _refresh(self, key: tuple, fn) -> threading.Event:
        """Start one background fetch of ``key`` (or join the one running)."""
        with self.lock:
            ev = self._busy.get(key)
            if ev is not None:
                return ev
            ev = self._busy[key] = threading.Event()
        threading.Thread(
            target=self._fetch, args=(key, fn), daemon=True, name=f"factory-read {key[-1]}"
        ).start()
        return ev

    def ask(self, fns: dict) -> None:
        """Fetch every key of ``fns`` not known or too old, all at once in the background, and
        wait at most ``first_wait`` seconds in all for the ones never seen."""
        waits = []
        now = time.time()
        for key, fn in fns.items():
            with self.lock:
                hit = self._hits.get(key)
            if hit is None:
                waits.append(self._refresh(key, fn))
            elif now - hit[0] >= self.ttl:
                self._refresh(key, fn)
        end = time.monotonic() + self.first_wait
        for ev in waits:
            ev.wait(max(0.0, end - time.monotonic()))

    def cached(self, key: tuple, fn, default=None, wait: float | None = None):
        with self.lock:
            hit = self._hits.get(key)
        if hit is None:
            if default is None:  # the old way: fetched here
                self._fetch(key, fn)
            else:
                self._refresh(key, fn).wait(self.first_wait if wait is None else wait)
            with self.lock:
                hit = self._hits.get(key)
            if hit is None:
                return default
        elif time.time() - hit[0] >= self.ttl:
            self._refresh(key, fn)
        if hit[2] is not None:
            raise hit[2]
        return hit[1]

    def forget(self, full: str) -> None:
        with self.lock:
            for k in [k for k in self._hits if k and k[0] == full]:
                del self._hits[k]
        self._save()
