"""The release gate's rules, and publishing with ``gh`` faked: private by default, approve
flips public, the secrets scan blocks a push, the review rewords claims and never holds."""

import json
import random
import string
import subprocess
from pathlib import Path

import pytest

from repo_factory.errors import LoopError
from repo_factory.loop import gate, publish, secrets

from .loop_common import FAKE_BUILD, HONEST, FakeModel, TextModel, make_loop


@pytest.fixture(autouse=True)
def fake_gh(tmp_path, monkeypatch):
    d = tmp_path / "gh"
    monkeypatch.setenv("FAKE_GH_DIR", str(d))
    for k, v in (
        ("GIT_AUTHOR_NAME", "T"),
        ("GIT_AUTHOR_EMAIL", "t@t"),
        ("GIT_COMMITTER_NAME", "T"),
        ("GIT_COMMITTER_EMAIL", "t@t"),
    ):
        monkeypatch.setenv(k, v)
    return d


def built(tmp_path, fake=None, **over):
    """A loop with one idea built by the fake build (gate not yet run)."""
    lp = make_loop(tmp_path, **over)
    idea, _ = lp.add("small clinics still schedule patients on paper https://example.org/clinics")
    out = lp.pitch([idea["id"]])[0]
    task = Path(out["task_dir"])
    (task / "fake.json").write_text(json.dumps(fake or {}))
    r = subprocess.run(["python3", str(FAKE_BUILD), str(task)], capture_output=True, text=True)
    assert r.returncode == (1 if (fake or {}).get("outcome") == "error" else 0), r.stderr
    return lp, idea["id"], task


def test_gate_ready(tmp_path):
    _, _, task = built(tmp_path)
    g = gate.evaluate(task)
    assert g["verdict"] == "ready", g["reasons"]
    assert set(g["checks"]) == {
        "delivered",
        "documents",
        "security",
        "tests",
        "safe_defaults",
        "completeness",
    }


@pytest.mark.parametrize(
    "fake,why",
    [
        (
            {"findings_sec": [{"severity": "high", "status": "open", "title": "negative length"}]},
            "security: open: high: negative length",
        ),
        (
            {"findings_sec": [{"severity": "critical", "status": "addressed", "title": "x"}]},
            "security: open: critical",
        ),
        (
            {
                "readiness": {
                    "final": {
                        "items": [{"id": "tests_green", "status": "red", "why": "2 tests fail"}]
                    }
                }
            },
            "tests: 2 tests fail",
        ),
        (
            {
                "readiness": {
                    "final": {
                        "items": [
                            {"id": "tests_green", "status": "green"},
                            {
                                "id": "bind_localhost",
                                "status": "red",
                                "title": "Listens on localhost",
                                "why": "binds 0.0.0.0",
                            },
                        ]
                    }
                }
            },
            "safe_defaults: red: Listens on localhost",
        ),
        (
            {
                "findings": {
                    "completeness": {
                        "done": 5,
                        "total": 6,
                        "flows": [{"id": "F6", "title": "Reports", "status": "partial"}],
                    }
                }
            },
            "completeness: 5/6",
        ),
        ({}, None),
    ],
)
def test_gate_holds_with_reasons(tmp_path, fake, why):
    if why is None:  # a round without its evidence (the reports)
        _, _, task = built(tmp_path, {"evidence": False})
        g = gate.evaluate(task)
        assert g["verdict"] == "held" and any(
            r.startswith("documents: no evidence") for r in g["reasons"]
        ), g["reasons"]
        return
    _, _, task = built(tmp_path, fake)
    g = gate.evaluate(task)
    assert g["verdict"] == "held"
    assert any(r.startswith(why) for r in g["reasons"]), g["reasons"]


def test_low_findings_dont_hold(tmp_path):
    _, _, task = built(
        tmp_path,
        {
            "findings_sec": [
                {"severity": "low", "status": "open", "title": "a"},
                {"severity": "high", "status": "fixed", "title": "b"},
                {"severity": "high", "status": "unconfirmed", "title": "c"},
            ]
        },
    )
    assert gate.evaluate(task)["verdict"] == "ready"


def gh_calls(fake_gh):
    return [json.loads(x) for x in (fake_gh / "calls.jsonl").read_text().splitlines()]


def repos(fake_gh):
    return json.loads((fake_gh / "repos.json").read_text())


def test_publish_private_then_approve_public(tmp_path, fake_gh):
    lp, iid, task = built(tmp_path, publish={"enabled": True})
    assert lp.gate(iid)["state"] == "ready"
    out = lp.publish(iid)
    assert out["state"] == "published"
    pub = out["publish"]
    assert pub["repo"] == "owner/clinicbook" and pub["visibility"] == "private"
    assert pub["url"] == "https://github.com/owner/clinicbook"
    assert pub["release_url"].endswith("/releases/tag/v0.1.0")
    r = repos(fake_gh)["owner/clinicbook"]
    assert r["visibility"] == "private" and r["releases"][0]["tag"] == "v0.1.0"
    # two assets: the source and the checks that ran before the release (EX-PLAN §4.4)
    assert [Path(a).name for a in r["releases"][0]["assets"]] == [
        "clinicbook-0.1.0.tar.gz",
        "clinicbook-0.1.0-checks.tar.gz",
    ]
    import tarfile

    with tarfile.open(r["releases"][0]["assets"][1]) as t:
        names = t.getnames()
    assert "clinicbook-0.1.0-checks/security.md" in names
    assert "clinicbook-0.1.0-checks/exam/_verify/scenarios/test_rule_c1.py" in names
    assert pub["assets"] == ["clinicbook-0.1.0.tar.gz", "clinicbook-0.1.0-checks.tar.gz"]
    create = [c for c in gh_calls(fake_gh) if c[:2] == ["repo", "create"]][0]
    assert "--private" in create and "--public" not in create
    visibility = lambda: [
        c
        for c in gh_calls(fake_gh)
        if c[:3] == ["api", "-X", "PATCH"]  # noqa: E731
        and any(x.startswith("visibility=") for x in c)
    ]
    assert not visibility()
    # what was pushed: the product as delivered, LICENSE and NOTICE; no REPORT.md, nothing
    # inserted into its README (EX-PLAN §4)
    ls = subprocess.run(
        ["git", "ls-tree", "-r", "--name-only", "main"],
        cwd=r["bare"],
        capture_output=True,
        text=True,
    ).stdout.split()
    assert (
        {"README.md", "SECURITY.md", "SCOPE.md", "LICENSE", "NOTICE", "app.py"} <= set(ls)
        and "REPORT.md" not in ls
        and "READINESS.md" not in ls
    )
    readme = subprocess.run(
        ["git", "show", "main:README.md"], cwd=r["bare"], capture_output=True, text=True
    ).stdout
    assert readme == "# Clinicbook\n\nBook patients in.\n\n## Install\n\n./run.sh\n"
    lic = subprocess.run(
        ["git", "show", "main:LICENSE"], cwd=r["bare"], capture_output=True, text=True
    ).stdout
    assert lic.startswith("MIT License") and "Test Owner" in lic
    notice = subprocess.run(
        ["git", "show", "main:NOTICE"], cwd=r["bare"], capture_output=True, text=True
    ).stdout
    assert "autonomous software build" in notice and "https://example.org/clinics" in notice
    assert "Test Owner. See LICENSE." in notice
    assert "published with the release as `clinicbook-0.1.0-checks.tar.gz`" in notice
    assert "SECURITY.md" not in notice and "READINESS.md" not in notice
    # the approval gate
    out = lp.approve(iid)
    assert out["state"] == "public" and repos(fake_gh)["owner/clinicbook"]["visibility"] == "public"
    # through the REST API, which every gh version supports (2.45's ``repo edit`` lacks
    # --accept-visibility-change-consequences, which newer releases require)
    assert visibility()[0][3:] == ["repos/owner/clinicbook", "-f", "visibility=public"]
    assert not any(c[:2] == ["repo", "edit"] for c in gh_calls(fake_gh))


def test_tick_waits_for_the_press_and_never_approves(tmp_path, fake_gh):
    lp, iid, task = built(tmp_path, publish={"enabled": True})
    lp.gate(iid)
    lp.tick()
    assert lp.ledger.get(iid)["state"] == "ready"
    assert lp.publish(iid)["state"] == "published"
    lp.tick()
    assert repos(fake_gh)["owner/clinicbook"]["visibility"] == "private"
    with pytest.raises(LoopError):
        lp.approve("nope")


def test_auto_approval_when_the_owner_decides(tmp_path, fake_gh):
    lp, iid, _ = built(tmp_path, publish={"enabled": True, "approval": "auto"})
    lp.gate(iid)
    assert lp.publish(iid)["state"] == "public"


def test_name_taken_gets_a_suffix(tmp_path, fake_gh):
    (fake_gh).mkdir(parents=True, exist_ok=True)
    (fake_gh / "repos.json").write_text(
        json.dumps({"owner/clinicbook": {"visibility": "private", "releases": []}})
    )
    lp, iid, _ = built(tmp_path, publish={"enabled": True})
    lp.gate(iid)
    assert lp.publish(iid)["publish"]["repo"] == "owner/clinicbook-2"


def test_secrets_scan_blocks_the_push(tmp_path, fake_gh):
    key = "ghp_" + "".join(random.Random(3).choices(string.ascii_letters + string.digits, k=40))
    lp, iid, _ = built(
        tmp_path,
        {"files": {"config.py": f'TOKEN = "{key}"\n', ".env": "SECRET=1\n"}},
        publish={"enabled": True},
    )
    lp.gate(iid)
    out = lp.publish(iid)
    assert out["state"] == "held"
    assert "secrets scan" in out["reason"] and "config.py:1 GitHub token" in out["reason"]
    assert ".env" in out["reason"]
    assert not (fake_gh / "calls.jsonl").exists() or not any(
        c[:2] == ["repo", "create"] for c in gh_calls(fake_gh)
    )
    assert key not in json.dumps(out)  # redacted in the ledger
    # retry publishes again (the owner fixed the tree); still blocked: nothing pushed
    assert lp.retry(iid)["state"] == "ready"


def test_jargon_never_holds_the_push(tmp_path, fake_gh):
    """Wording is never a hold (the owner, 2026-10-16): the provenance scan's hits in README and
    AGENTS.md go to the one review as lines to say plainly, and what is left is listed."""
    hon = TextModel()
    lp, iid, _ = built(
        tmp_path,
        {"files": {"README.md": "# X\n\nBuilt by brb, see REPORT.md\n"}},
        honesty=hon,
        publish={"enabled": True},
    )
    lp.gate(iid)
    out = lp.publish(iid)
    assert out["state"] == "published", out.get("reason")
    assert {h["kind"] for h in out["publish"]["jargon"]} and "--- BUILD WORDS" in hon.of("review")[
        0
    ]


def test_the_provenance_scan_holds_only_ids_this_build_minted(tmp_path, fake_gh):
    """EX-PLAN §4.6: the product's own ids (Repo Recall's "item s9") are its words; an id this
    build minted, next to its kind word, is repo-factory's process in the product's prose."""
    fs = [{"ref": "s1", "id": "S1", "severity": "low", "status": "open", "title": "t"}]
    lp, iid, _ = built(
        tmp_path,
        {
            "files": {
                "README.md": "# Clinicbook\n\nNo state item s9; drop it with `recall drop s9`.\n"
            },
            "findings_sec": fs,
        },
        publish={"enabled": True},
    )
    lp.gate(iid)
    assert lp.publish(iid)["state"] == "published"
    lp, iid, _ = built(
        tmp_path / "b",
        {
            "files": {"SECURITY.md": "# Security\n\nSee finding s1 for the slow read.\n"},
            "findings_sec": fs,
        },
        publish={"enabled": True},
    )
    lp.gate(iid)
    out = lp.publish(iid)  # found and listed, never a hold
    assert out["state"] == "published"
    assert [(h["file"], h["line"], h["kind"]) for h in out["publish"]["jargon"]] == [
        ("SECURITY.md", 3, "finding id")
    ]


def test_the_review_rewords_once_and_lists_what_it_changed(tmp_path, fake_gh):
    fix = {
        "file": "README.md",
        "quote": "Book patients in.",
        "problem": "vague",
        "replacement": "Book patients in, on your own machine.",
    }
    hon = TextModel(review=lambda p: [fix])
    lp, iid, _ = built(tmp_path, honesty=hon, publish={"enabled": True})
    lp.gate(iid)
    out = lp.publish(iid)
    # the description could not be written (an unusable reply, asked twice: the product's text
    # stays), then one review whose replacement is applied: no second look, no hold
    assert out["state"] == "published" and hon.calls == ["describe", "describe", "review"]
    d = out["publish"]["described"]
    # the sentence stands in the README and in the AGENTS.md repo-factory rendered from it: fixed in both
    assert d["reworded"] == [
        {
            "file": "README.md and AGENTS.md",
            "was": "Book patients in.",
            "now": "Book patients in, on your own machine.",
            "why": "vague",
        }
    ]
    r = repos(fake_gh)["owner/clinicbook"]
    readme = subprocess.run(
        ["git", "show", "main:README.md"], cwd=r["bare"], capture_output=True, text=True
    ).stdout
    assert "on your own machine" in readme
    assert (
        "repo-factory reworded 1 sentence before this release: README.md and AGENTS.md: "
        '"Book patients in." → '
        '"Book patients in, on your own machine."' in r["releases"][0]["notes"]
    )


def test_a_review_issue_never_holds(tmp_path, fake_gh):
    """An issue whose quote is not in the text can't be applied: it is noted, and the build
    publishes all the same (the owner, 2026-10-16: wording is never a hold)."""
    bad = {
        "file": "README.md",
        "quote": "trusted by 500 clinics",
        "problem": "no users",
        "replacement": "",
    }
    lp, iid, _ = built(
        tmp_path, honesty=TextModel(review=lambda p: [bad]), publish={"enabled": True}
    )
    lp.gate(iid)
    out = lp.publish(iid)
    assert out["state"] == "published"
    d = out["publish"]["described"]
    assert d["reworded"] == [] and d["noted"][0]["was"] == "trusted by 500 clinics"


def test_secrets_scanner_shapes(tmp_path):
    # Secret-shaped values are built at runtime: never a literal in the repo (GitGuardian).
    rnd = "".join(random.Random(7).choices(string.ascii_letters + string.digits, k=30))
    assert secrets.scan_text('password = "correct-horse-1"') == []  # a test password
    assert secrets.scan_text(f'api_key = "{rnd}"')
    assert secrets.scan_text("-----BEGIN RSA " + "PRIVATE KEY-----")
    assert secrets.scan_text('k = "sk-ant-' + "x" * 30 + '"')
    assert secrets.scan_text(f'api_key = "{rnd}"  # pragma: allowlist secret') == []
    assert secrets.scan_text('token = "your_token_here_example_xxxxxxxxxxxx"') == []
    (tmp_path / "uv.lock").write_text('hash = "sha256:' + "ab12" * 16 + '"\n')
    (tmp_path / "data.db").write_bytes(b"\0sqlite")
    (tmp_path / ".env.example").write_text("SECRET=\n")
    hits = secrets.scan(tmp_path)
    assert [h["kind"] for h in hits] == ["database file"]


def test_landing_goes_after_title_and_tagline(tmp_path):
    r = tmp_path / "README.md"
    r.write_text("# Name\n\nA tagline\nover two lines.\n\n## Install\n\nx\n")
    publish.add_landing(r, "## At a glance\n\n- y\n")
    assert r.read_text() == (
        "# Name\n\nA tagline\nover two lines.\n\n## At a glance\n\n- y\n\n## Install\n\nx\n"
    )
    r.write_text("no title\n")
    publish.add_landing(r, "## At a glance\n")
    assert r.read_text().startswith("## At a glance\n\nno title")


def test_daemon_end_to_end_to_published(tmp_path, fake_gh):
    lp = make_loop(tmp_path, publish={"enabled": True})
    lp.add("small clinics still schedule patients on paper")
    lp.run(until_idle=True)
    [i] = lp.ledger.all()
    assert i["state"] == "ready"  # a ready build waits for the owner's press
    assert not (fake_gh / "repos.json").exists() or "owner/clinicbook" not in repos(fake_gh)
    i = lp.publish(i["id"])
    assert i["state"] == "published" and i["publish"]["visibility"] == "private"
    assert repos(fake_gh)["owner/clinicbook"]["visibility"] == "private"


# -- #65.2: the NOTICE claims only the evidence the pitch judged relevant ------------------------
def _ev_idea(used=None):
    ev = [
        {"url": "https://example.org/e1", "source": "HN", "observed_at": "2026-09-01"},
        {"url": "https://example.org/e2", "source": "Reddit", "observed_at": "2026-09-02"},
    ]
    pitch = {"product": "P"} if used is None else {"product": "P", "evidence_used": used}
    return {"card": {"evidence": ev}, "pitch": pitch, "urls": []}


def test_notice_without_evidence_used_calls_every_source_background():
    text = publish.notice("P", _ev_idea(), 2026, "O")
    assert "describe the problem" not in text and "show the problem" not in text
    assert "as background; they do not show demand" in text
    assert "https://example.org/e1" in text and "https://example.org/e2" in text
    assert "None of them made, reviewed or endorsed it." in text


def test_notice_lists_only_the_used_evidence_as_showing_the_problem():
    text = publish.notice("P", _ev_idea(["E1"]), 2026, "O")
    shown, _, rest = text.partition("as background")
    assert "These sources show the problem this software addresses" in shown
    assert "https://example.org/e1" in shown and "https://example.org/e2" not in shown
    assert "https://example.org/e2" in rest and "describe the problem" not in text


# -- publish anyway (AR-PLAN.md §4) ---------------------------------------------------------------
def held(tmp_path, fake, **over):
    lp, iid, task = built(tmp_path, fake, publish={"enabled": True}, **over)
    out = lp.gate(iid)
    assert out["state"] == "held", out
    return lp, iid


RED_TESTS = {
    "readiness": {
        "final": {
            "items": [
                {
                    "id": "tests_green",
                    "status": "red",
                    "why": "4 of 58 of the product's own tests fail",
                }
            ]
        }
    }
}


def created(fake_gh):
    return (fake_gh / "calls.jsonl").exists() and any(
        c[:2] == ["repo", "create"] for c in gh_calls(fake_gh)
    )


def test_publish_anyway_over_red_tests_lands_private_with_the_override(tmp_path, fake_gh):
    lp, iid = held(tmp_path, RED_TESTS)
    # a press from the UI never runs the pre-push checks (a model call): they run first, in
    # the background (the tick, or repo-factory loop precheck)
    with pytest.raises(LoopError, match="have not run on this tree"):
        lp.publish_anyway(iid, "the four failures are flaky clock tests", origin="ui", yes=True)
    assert not created(fake_gh)
    assert lp.precheck(iid)["state"] == "done"
    out = lp.publish_anyway(
        iid, "the four failures are flaky clock tests", origin="ui", yes=True, holds=["tests"]
    )
    assert out["state"] == "published" and out["publish"]["visibility"] == "private"
    ov = out["publish"]["override"]
    assert ov["overrides"] == ["tests"] and ov["origin"] == "ui"
    assert ov["reason"] == "the four failures are flaky clock tests"
    create = [c for c in gh_calls(fake_gh) if c[:2] == ["repo", "create"]][0]
    assert "--private" in create
    notice = subprocess.run(
        ["git", "show", "main:NOTICE"],
        cwd=repos(fake_gh)["owner/clinicbook"]["bare"],
        capture_output=True,
        text=True,
    ).stdout
    assert "Released by its maintainer although these checks did not pass: tests (4 of 58" in notice
    rel = [c for c in gh_calls(fake_gh) if c[:2] == ["release", "create"]][0]
    assert any("did not pass" in x for x in rel)
    notes = [h["note"] for h in lp.ledger.history(iid)]
    assert any(
        n.startswith("published over a hold by the owner (ui): the four failures")
        and "overrode: tests (4 of 58" in n
        for n in notes
    )
    assert repos(fake_gh)["owner/clinicbook"]["visibility"] == "private"


@pytest.mark.parametrize(
    "fake, why",
    [
        (
            {"findings_sec": [{"severity": "high", "status": "open", "title": "path traversal"}]},
            "security",
        ),
        (
            {
                "readiness": {
                    "final": {
                        "items": [
                            {"id": "tests_green", "status": "green"},
                            {
                                "id": "bind_localhost",
                                "status": "red",
                                "title": "Listens on localhost",
                                "why": "binds 0.0.0.0",
                            },
                        ]
                    }
                }
            },
            "loopback",
        ),
    ],
)
def test_publish_anyway_never_overrides_security_or_exposure(tmp_path, fake_gh, fake, why):
    lp, iid = held(tmp_path, fake)
    with pytest.raises(LoopError, match=why):
        lp.publish_anyway(iid, "ship it anyway, I checked", yes=True)
    assert not created(fake_gh) and lp.ledger.get(iid)["state"] == "held"


def test_a_review_that_did_not_run_is_a_gap_not_a_hold(tmp_path, fake_gh):
    # AU1 #1: Sidedoor r1-r2 carried "the security review did not run", never overridable;
    # it is repo-factory's own gap: the tests hold may be overridden, and the release notes say it
    lp, iid = held(tmp_path, {"readiness": {"security": {"ran": False}, **RED_TESTS["readiness"]}})
    g = lp.ledger.get(iid)["gate"]
    assert [r.split(":")[0] for r in g["reasons"]] == ["tests"]
    assert g["gaps"] == ["security: the security review did not run"]
    out = lp.publish_anyway(iid, "the four failures are flaky clock tests", yes=True)
    assert out["state"] == "published"
    rel = [c for c in gh_calls(fake_gh) if c[:2] == ["release", "create"]][0]
    assert any(
        "Not every check could run before this release: the security review did not run." in x
        for x in rel
    )


def test_a_failing_quick_start_may_be_overridden(tmp_path, fake_gh):
    lp, iid = held(
        tmp_path,
        {
            "readiness": {
                "final": {
                    "items": [
                        {"id": "tests_green", "status": "green"},
                        {
                            "id": "commands",
                            "status": "red",
                            "title": "The README's quick-start commands work",
                            "why": "`app backup x.db` exits 1",
                        },
                    ]
                }
            }
        },
    )
    assert (
        lp.publish_anyway(iid, "the backup step needs a running server", yes=True)["state"]
        == "published"
    )


def test_publish_anyway_never_pushes_a_secret(tmp_path, fake_gh):
    key = "ghp_" + "".join(random.Random(5).choices(string.ascii_letters + string.digits, k=40))
    lp, iid, _ = built(
        tmp_path,
        {"files": {"config.py": f'TOKEN = "{key}"\n'}, **RED_TESTS},
        publish={"enabled": True},
    )
    assert lp.gate(iid)["state"] == "held"
    lp.precheck(iid)
    with pytest.raises(LoopError, match="secrets"):
        lp.publish_anyway(iid, "the four failures are flaky clock tests", yes=True)
    # the red tests alone are overridable; a secret found again at push time still blocks (the
    # press scans the staged tree again, whatever the record of the checks says)
    pub = lp.ledger.get(iid)["publish"]
    pub["precheck"]["blocked"] = [
        b for b in pub["precheck"]["blocked"] if not b.startswith("secrets scan")
    ]
    lp.ledger.update(iid, publish=pub)
    out = lp.publish_anyway(iid, "the four failures are flaky clock tests", yes=True)
    assert out["state"] == "held" and "secrets scan" in out["reason"]
    assert not created(fake_gh)
    last = next(
        h for h in lp.ledger.history(iid) if (h["data"] or {}).get("kind") == "publish_anyway"
    )
    assert last["note"].startswith("publish anyway (cli) held again, nothing was pushed")
    assert last["data"]["outcome"] == "held"


def test_publish_anyway_refuses_a_failed_build_an_empty_reason_and_no_yes(tmp_path, fake_gh):
    lp, iid, task = built(tmp_path, {"outcome": "error"}, publish={"enabled": True})
    lp.ledger.update(
        iid,
        state="held",
        reason="the build failed 2 time(s): boom",
        gate={"verdict": "held", "reasons": ["boom"]},
    )
    with pytest.raises(LoopError, match="nothing was delivered"):
        lp.publish_anyway(iid, "please publish this one", yes=True)
    lp2, iid2 = held(tmp_path / "b", RED_TESTS)
    with pytest.raises(LoopError, match="12 to 300"):
        lp2.publish_anyway(iid2, "  ", yes=True)
    with pytest.raises(LoopError, match="--yes"):
        lp2.publish_anyway(iid2, "the four failures are flaky")
    assert not created(fake_gh)


def test_the_pages_publish_on_a_loop_builds_row_reaches_it_through_api_act(tmp_path, fake_gh):
    """The page's Publish on a ready loop build's row (``subject: {kind: build, id: <key>}``)
    goes through ``api/act`` to the loop's publish: a private repository, never a 404 from a
    build-by-hand route."""
    import yaml

    from repo_factory import registry as R
    from repo_factory.ui import api

    lp, iid, task = built(tmp_path, publish={"enabled": True})
    assert lp.gate(iid)["state"] == "ready"
    assert lp.precheck(iid)["state"] == "done"  # the checks ran: the press waits on none
    (lp.cfg.home / "loop.yaml").write_text(yaml.safe_dump(lp.cfg.data))
    app = api.App(lp.cfg.tasks_dir, "fake", lp.cfg.home)
    key = R.subject_of(app, "idea", iid).row["key"]
    assert key == task.name
    body = {
        "kind": "publish",
        "subject": {"kind": "build", "id": key},
        "origin": "ui",
        "fields": {},
    }
    code, _, data = api.handle("POST", "/api/act", None, json.dumps(body).encode(), app=app)
    assert code == 409 and json.loads(data)["error"] == "publish needs confirm: true"
    code, _, data = api.handle(
        "POST", "/api/act", None, json.dumps({**body, "confirm": True}).encode(), app=app
    )
    out = json.loads(data)
    assert code == 200 and out["outcome"] == "published", out
    assert lp.ledger.get(iid)["state"] == "published"
    assert repos(fake_gh)["owner/clinicbook"]["visibility"] == "private"


def test_publish_anyway_from_the_cli_needs_yes_and_records_the_origin(
    tmp_path, fake_gh, capsys, monkeypatch
):
    import yaml

    from repo_factory.cli import main
    from repo_factory.loop.daemon import Loop

    # the command builds its own Loop: its honesty check is the fake (no model in tests)
    monkeypatch.setattr(Loop, "honesty_model", property(lambda self: FakeModel(HONEST)))
    lp, iid = held(tmp_path, RED_TESTS)
    (lp.cfg.home / "loop.yaml").write_text(yaml.safe_dump(lp.cfg.data))
    home = ["loop", "--home", str(lp.cfg.home)]
    with pytest.raises(SystemExit):
        main([*home, "publish-anyway", iid, "--reason", "flaky clock tests only"])
    with pytest.raises(SystemExit, match="pre-push checks"):  # an agent never waits on a model
        main(
            [
                *home,
                "publish-anyway",
                iid,
                "--reason",
                "flaky clock tests only",
                "--yes",
                "--origin",
                "agent:rc",
            ]
        )
    main([*home, "precheck", iid])
    assert "nothing holds the push" in capsys.readouterr().out
    with pytest.raises(SystemExit, match="holds changed"):
        main(
            [
                *home,
                "publish-anyway",
                iid,
                "--reason",
                "flaky clock tests only",
                "--yes",
                "--origin",
                "agent:rc",
                "--holds",
                "tests,jargon",
            ]
        )
    main(
        [
            *home,
            "publish-anyway",
            iid,
            "--reason",
            "flaky clock tests only",
            "--yes",
            "--origin",
            "agent:rc",
            "--holds",
            "tests",
        ]
    )
    assert "private repo" in capsys.readouterr().out
    i = lp.ledger.get(iid)
    assert i["state"] == "published" and i["publish"]["override"]["origin"] == "agent:rc"
    assert any((h["data"] or {}).get("origin") == "agent:rc" for h in lp.ledger.history(iid))
