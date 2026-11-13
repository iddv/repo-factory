"""A project's sources, drift and stats: the clone repo-factory keeps (a local bare repo made
from the Receipts fixture stands for GitHub; the fake ``gh`` clones it), the drift checks
after a move and only then, the numbers from the fake GitHub, the poll's cache and the ledger,
the Timeline lines that are news, ``gather`` and its fingerprint, and the tick's bounded
refresh. No model, no network."""

import json
import shutil
import subprocess
import time
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from repo_factory.gh import Gh
from repo_factory.maintain.github import Cache
from repo_factory.projects import clones, latest, life, sources, stats
from repo_factory.projects.charter import normalize, parse, save
from repo_factory.projects.record import Projects, create_for_idea

from .loop_common import make_loop
from .test_charter import RECEIPTS as RECEIPTS_REPLY
from .test_projects import LIVE, published

pytestmark = pytest.mark.usefixtures("projects_on")

FIX = Path(__file__).parent / "fixtures" / "projects"
TAGS = ["v0.1.0", "v0.1.1", "v0.1.2", "v0.1.3"]
WHO = [
    "-c",
    "user.name=t",
    "-c",
    "user.email=t@t",
    "-c",
    "commit.gpgsign=false",
    "-c",
    "tag.gpgsign=false",
]


def sh(*args, cwd):
    return subprocess.run(
        ["git", *WHO, *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout


class Remote:
    """A repo's GitHub side on disk: a work tree from a fixture, pushed to a bare repo."""

    def __init__(self, tmp: Path, fixture: Path, tags=TAGS):
        self.work, self.bare = tmp / "work", tmp / "remote.git"
        shutil.copytree(fixture, self.work)
        # releases cut by hand, so the version in the files is the product's to keep (AU3-2)
        pol = self.work / ".github" / "repo-factory.yml"
        if pol.is_file():
            pol.write_text(pol.read_text().replace("on_merge: patch", "on_merge: off"))
        sh("init", "-q", "-b", "main", cwd=self.work)
        sh("add", "-A", cwd=self.work)
        sh("commit", "-q", "-m", "the fixture", cwd=self.work)
        for t in tags:
            sh("tag", t, cwd=self.work)
        sh("clone", "-q", "--bare", str(self.work), str(self.bare), cwd=tmp)

    def commit(self, rel: str, text: str) -> None:
        (self.work / rel).write_text(text)
        sh("add", "-A", cwd=self.work)
        sh("commit", "-q", "-m", f"change {rel}", cwd=self.work)
        sh("push", "-q", str(self.bare), "main", cwd=self.work)

    def head(self) -> str:
        return sh("rev-parse", "HEAD", cwd=self.work).strip()


class Hub:
    """``gh``: ``repo clone`` from the local bare repos, ``api repos/<r>`` with stars."""

    def __init__(self, remotes: dict, stars: int = 4):
        self.remotes, self.stars, self.calls = remotes, stars, []

    def __call__(self, args, cwd=None):
        a = list(args)
        self.calls.append(a)
        if a[:2] == ["repo", "clone"]:
            full, dest = a[2], a[3]
            if full not in self.remotes:
                return 1, "", f"GraphQL: Could not resolve to a Repository ({full})"
            p = subprocess.run(
                [
                    "git",
                    "clone",
                    "-q",
                    "--depth",
                    "200",
                    "file://" + str(self.remotes[full].bare),
                    dest,
                ],
                capture_output=True,
                text=True,
            )
            return p.returncode, p.stdout, p.stderr
        if a[0] == "api" and urlsplit(a[1]).path.startswith("repos/"):
            full = a[1][len("repos/") :]
            if full not in self.remotes:
                return 1, "", "HTTP 404: Not Found"
            return (
                0,
                json.dumps(
                    {
                        "stargazers_count": self.stars,
                        "forks_count": 2,
                        "subscribers_count": 3,
                        "open_issues_count": 5,
                    }
                ),
                "",
            )
        return 2, "", f"unknown {a}"


@pytest.fixture
def world(tmp_path):
    lp = make_loop(tmp_path)
    remote = Remote(tmp_path / "gh", FIX / "receipts")
    hub = Hub({"iddv/receipts": remote})
    idea = published(lp, "Receipts", "receipts")
    ps = Projects(lp.ledger)
    p, _ = create_for_idea(ps, idea)
    p = save(ps, p["id"], normalize(parse(RECEIPTS_REPLY)), "repo-factory", "drafted")
    return lp, ps, p, idea, remote, hub


def seed(lp, p, idea, now):
    Cache(lp.cfg.home).write_repo(
        "iddv/receipts",
        {
            "last_poll": now,
            "counts": {"issues": 2, "prs": 1, "dependabot": 1, "factory_prs": 0},
            "rows": [
                {
                    "kind": "needs_you",
                    "number": 5,
                    "title": "It crashes on a deep bundle",
                    "url": "https://github.com/iddv/receipts/issues/5",
                    "updated": 100.0,
                    "created": 50.0,
                },
                {
                    "kind": "dependabot",
                    "number": 7,
                    "title": "Bump pytest from 8.3.3 to 9.1.1",
                    "url": "https://github.com/iddv/receipts/pull/7",
                    "updated": 90.0,
                    "created": 80.0,
                },
            ],
            "release": {
                "tag": "v0.1.3",
                "url": "https://github.com/iddv/receipts/releases/v0.1.3",
                "at": 99.0,
            },
            "ci": {"state": "success", "runs": 2},
        },
    )
    lp.ledger.spend("charter", 0.25, project=p["id"], in_tokens=1000, out_tokens=200)
    lp.ledger.spend("triage", 0.05, idea["id"])  # the origin idea's own calls
    lp.ledger.spend("evolve", 9.0, project="p00000000")  # another project's
    fix = Path(lp.cfg.tasks_dir) / "fix-iddv-receipts-5"
    (fix / ".den").mkdir(parents=True)
    (fix / "loop.json").write_text(json.dumps({"kind": "fix", "repo": "iddv/receipts", "issue": 5}))
    (fix / ".den" / "ledger.jsonl").write_text(
        json.dumps({"ts": now - 10, "usd": 1.5, "in_tokens": 10, "out_tokens": 5}) + "\n"
    )
    with lp.ledger.db() as c:
        for i, state in enumerate(("new", "new", "accepted")):
            c.execute(
                "INSERT INTO changes (id, project, fp, title, kind, class, state, card, "
                "created, updated) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    f"c{i}",
                    p["id"],
                    f"fp{i}",
                    f"idea {i}",
                    "maintenance",
                    "patch",
                    state,
                    "{}",
                    now,
                    now,
                ),
            )


def drift_events(ps, pid):
    return [
        e["note"]
        for e in ps.events(pid)
        if (e.get("data") or {}).get("kind") == "maintenance" and e["note"].startswith("drift")
    ]


def test_stats_from_github_the_cache_the_ledger_and_the_clone(world):
    lp, ps, p, idea, remote, hub = world
    now = time.time()
    seed(lp, p, idea, now)
    st = stats.refresh(lp, p, gh=Gh(run=hub), now=now, latest={"pytest": "9.1.1"})
    assert (st["stars"], st["forks"], st["watchers"], st["open_issues"]) == (4, 2, 3, 5)
    assert (st["issues"], st["prs"], st["dependabot"], st["needs_you"]) == (2, 1, 1, 1)
    assert st["release"]["tag"] == "v0.1.3" and st["ci"]["state"] == "success"
    assert st["builds"] == {"round": None, "changes": 0, "fixes": 1}
    assert st["spend"] == {"month": 1.8, "all": 1.8}  # 0.25 + 0.05 + the fix's 1.5
    assert st["tokens"]["all"] == {"in": 1010, "out": 205}
    assert st["ideas"] == {"new": 2, "accepted": 1}
    assert st["error"] is None and st["at"] == now and st["clone_head"] == remote.head()
    checks = {f["check"] for f in st["drift"]["findings"]}
    assert "version_tags" in checks and "pins" not in checks, (
        "the template's pin is repo-factory's own"
    )
    vt = next(f for f in st["drift"]["findings"] if f["check"] == "version_tags")
    assert "pyproject.toml says 1.0.0" in vt["what"] and "v0.1.3" in vt["what"]
    assert st["drift"]["commit"] == remote.head()
    assert ps.must(p["id"])["stats"] == st  # what record.health reads
    [note] = drift_events(ps, p["id"])
    assert note.startswith("drift: 1 finding: ") and len(st["drift"]["findings"]) == 1
    clone = Path(lp.cfg.home) / "projects" / "clones" / "iddv-receipts"
    assert (clone / "pyproject.toml").is_file()
    assert json.loads(latest.cache_path(lp.cfg.home).read_text())["pytest"]["version"] == "9.1.1"
    assert ["repo", "clone", "iddv/receipts", str(clone), "--", "-q", "--depth", "200"] in hub.calls


def test_no_move_no_second_drift_run_a_move_runs_it_again(world):
    lp, ps, p, idea, remote, hub = world
    gh = Gh(run=hub)
    first = stats.refresh(lp, p, gh=gh, now=1000.0, latest={})
    again = stats.refresh(lp, p, gh=gh, now=2000.0, latest={})
    assert again["drift"] == first["drift"] and again["drift"]["at"] == 1000.0
    assert len(drift_events(ps, p["id"])) == 1
    remote.commit("NOTES.md", "nothing that changes a finding\n")
    moved = stats.refresh(lp, p, gh=gh, now=3000.0, latest={})
    assert moved["drift"]["commit"] == remote.head() and moved["drift"]["at"] == 3000.0
    assert len(drift_events(ps, p["id"])) == 1  # the same findings: no line
    init = remote.work / "receipts" / "__init__.py"
    init.write_text(init.read_text().replace('"1.0.0"', '"0.1.3"'))
    text = (remote.work / "pyproject.toml").read_text().replace('"1.0.0"', '"0.1.3"')
    remote.commit("pyproject.toml", text)  # both files, one commit
    fixed = stats.refresh(lp, p, gh=gh, now=4000.0, latest={})
    assert not any("1.0.0" in f["what"] for f in fixed["drift"]["findings"])
    assert len(drift_events(ps, p["id"])) == 2


def test_each_part_fails_alone_and_milestones_are_said_once(world):
    lp, ps, p, idea, remote, hub = world
    hub.stars = 0
    gone = Hub({}, stars=0)  # the clone cannot be made
    gh = Gh(run=lambda a, cwd=None: (hub if a[0] == "api" else gone)(a, cwd))
    st = stats.refresh(lp, p, gh=gh, now=1000.0)
    assert st["stars"] == 0 and "clone: CloneError" in st["error"] and "drift" not in st
    hub.stars = 1
    stats.refresh(lp, p, gh=gh, now=2000.0)
    hub.stars = 2
    stats.refresh(lp, p, gh=gh, now=3000.0)
    notes = [e["note"] for e in ps.events(p["id"]) if (e.get("data") or {}).get("kind") == "stats"]
    assert notes == ["Receipts reached the first star"]
    broken = Gh(run=lambda a, cwd=None: (1, "", "HTTP 502"))
    st = stats.refresh(lp, ps.must(p["id"]), gh=broken, now=4000.0)
    assert st["stars"] == 2 and "github:" in st["error"]  # the last numbers stay


def test_an_adopted_repo_reads_its_maintenance_clone_as_it_is(tmp_path):
    lp = make_loop(tmp_path)
    remote = Remote(tmp_path / "gh", FIX / "plotkeeper", tags=["v0.1.0", "v0.1.1"])
    ps = Projects(lp.ledger)
    p, _ = ps.create(
        name="plotkeeper",
        kind="adopted",
        repo={"repo": "acme/plotkeeper", "default_branch": "main"},
    )
    work = Path(lp.cfg.home) / "maintain" / "work" / "acme-plotkeeper" / "clone"
    sh("clone", "-q", str(remote.bare), str(work), cwd=tmp_path)
    no_gh = Gh(run=lambda a, cwd=None: pytest.fail(f"no gh call expected: {a}"))
    got = clones.refresh(lp, p, gh=no_gh)
    assert got["shared"] and got["path"] == str(work) and got["moved"]
    assert got["head"] == remote.head() and set(got["tags"]) == {"v0.1.0", "v0.1.1"}
    found = stats.run_drift(lp.cfg.home, p, got, latest={}, now=time.time())["findings"]
    assert any(f["check"] == "doc_paths" and "test.yml" in f["what"] for f in found)


def test_gather_reads_everything_and_says_what_changed(world):
    lp, ps, p, idea, remote, hub = world
    now = time.time()
    seed(lp, p, idea, now)
    gh = Gh(run=hub)
    stats.refresh(lp, p, gh=gh, now=now, latest={"pytest": "9.1.1"})
    p = ps.must(p["id"])
    g = sources.gather(lp, p, gh=gh, now=now)
    assert "SKIP_MARKERS" in g["digest"]
    assert [s["id"] for s in g["backlog"]["security"]] == ["S3", "S4"]
    assert [i["number"] for i in g["issues"]["items"]] == [5, 7]
    assert "iddv/receipts: 2 open issues, 1 open pull request" in g["issues"]["text"]
    assert g["observations"] == [] and g["parent"] == []  # nothing outside the repository
    assert g["drift"] == p["stats"]["drift"]["findings"]  # the tick's, for this head
    assert g["head"] == remote.head() and g["charter_v"] == 1 and g["errors"] == []
    assert g["changed"]["any"]
    again = sources.gather(lp, p, gh=gh, now=now + 60, last=g["fingerprint"])
    assert not again["changed"]["any"], again["changed"]
    rec = Cache(lp.cfg.home).repo("iddv/receipts")
    rec["rows"][0]["updated"] = 200.0  # issue 5 got a comment
    Cache(lp.cfg.home).write_repo("iddv/receipts", rec)
    third = sources.gather(lp, p, gh=gh, now=now + 180, last=g["fingerprint"])
    assert third["changed"]["issues"] == ["iddv/receipts#5"]
    assert not third["changed"]["head"]


def test_issues_says_plainly_when_there_are_none():
    out = sources.issues(
        [("iddv/quiet", {"last_poll": 1.0, "counts": {}, "rows": []}), ("iddv/new", {})]
    )
    assert out["items"] == []
    assert out["text"] == (
        "iddv/quiet: no open issues or pull requests.\n"
        "iddv/new: not polled yet, so its issues are not known."
    )


def test_latest_skips_a_name_it_cannot_read_and_keeps_a_day(tmp_path):
    def fetch(name):
        if name == "ruff":
            raise OSError("no network here")
        return {"pytest": "9.1.1"}.get(name)

    assert latest.lookup(tmp_path, ["pytest", "ruff", "nothing"], fetch=fetch, now=100.0) == {
        "pytest": "9.1.1"
    }
    assert latest.lookup(tmp_path, ["pytest"], fetch={}, now=200.0) == {"pytest": "9.1.1"}
    assert latest.lookup(tmp_path, ["pytest"], fetch={}, now=100.0 + 86400) == {}
    assert latest.pinned_names(FIX / "receipts") == ["pytest"]


def test_the_tick_refreshes_a_few_projects_and_goes_on_past_a_failure(tmp_path, monkeypatch):
    lp = make_loop(tmp_path)
    for product, name in LIVE[:6]:
        published(lp, product, name)
    life.backfill(lp, charters=False)
    ps = Projects(lp.ledger)
    ids = [p["id"] for p in ps.all()]
    calls = []

    def fake(loop, p, now=None, **kw):
        calls.append(p["id"])
        if p["id"] == ids[1]:
            raise RuntimeError("the clone is broken")
        ps.update(p["id"], stats={"at": now})
        return {"at": now}

    monkeypatch.setattr(stats, "refresh", fake)
    out = life.tick(lp, now=10_000.0)
    assert calls == ids[:4] and out["stats"] == [ids[0], ids[2], ids[3]]
    assert any(e.startswith(f"{ids[1]} stats: RuntimeError") for e in out["errors"])
    assert ps.must(ids[1])["stats"]["error"] == "RuntimeError: the clone is broken"
    out = life.tick(lp, now=10_001.0)
    assert calls[4:] == ids[4:]  # the rest, round-robin
    out = life.tick(lp, now=10_002.0)
    assert len(calls) == 6  # not due before maintain.poll_s
    life.tick(lp, now=10_000.0 + 900)
    assert calls[6:] == ids[:4]  # the stalest first
