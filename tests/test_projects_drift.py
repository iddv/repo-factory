"""Drift checks on trimmed copies of Receipts and Plotkeeper at their 2026-10-12 commits
(tests/fixtures/projects): the findings the PJ experiment verified by hand come out of code."""

from __future__ import annotations

import datetime
import shutil
from pathlib import Path

import pytest

from repo_factory.projects import drift

pytestmark = pytest.mark.usefixtures("projects_on")

FIX = Path(__file__).parent / "fixtures" / "projects"
RECEIPTS, PLOTKEEPER = FIX / "receipts", FIX / "plotkeeper"
RECEIPTS_TAGS = ["v0.1.0", "v0.1.1", "v0.1.2", "v0.1.3"]
PLOTKEEPER_TAGS = ["v0.1.0", "v0.1.1"]
TODAY = datetime.date(2026, 10, 12)
SHAPE = {"check", "severity", "what", "where", "fix_hint"}


def test_receipts_version_disagrees_with_the_tags_and_the_changelog():
    [f] = drift.version_tags(RECEIPTS, RECEIPTS_TAGS)
    assert set(f) == SHAPE and f["severity"] == "medium"
    assert "pyproject.toml says 1.0.0" in f["what"]
    assert "the newest tag is v0.1.3" in f["what"]
    assert "CHANGELOG.md is 0.1.0" in f["what"]
    assert f["where"] == "pyproject.toml"
    assert "0.1.3" in f["fix_hint"]


def test_tag_order_is_semver_not_text():
    assert drift.newest_tag(["v0.9.0", "v0.10.0", "v0.2.0", "latest"]) == "v0.10.0"
    assert drift.newest_tag(["v1.0.0-rc1", "v1.0.0"]) == "v1.0.0"


def test_versions_that_agree_give_nothing(tmp_path):
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "x"\nversion = "0.2.0"\n')
    (tmp_path / "CHANGELOG.md").write_text("# Changelog\n\n## 0.2.0\n\n- Two.\n")
    assert drift.version_tags(tmp_path, ["v0.1.0", "v0.2.0"]) == []


def test_factory_own_drift_is_not_found_on_the_projects_it_releases():
    """AU3 finding 2: 29 of the 46 live findings were repo-factory's own. Its release step writes the
    tag and nothing else, so the version in a file and CHANGELOG's Unreleased fall behind by
    construction; its templates pinned pytest. On Receipts and Plotkeeper as they were on
    2026-10-12 (both `release.on_merge: patch`), none of those is found any more."""
    for root, tags in ((RECEIPTS, RECEIPTS_TAGS), (PLOTKEEPER, PLOTKEEPER_TAGS)):
        assert drift.releases_by_factory(root)
        found = drift.run(root, tags=tags, latest={"pytest": "9.1.1"}, today=TODAY)
        assert not {"version_tags", "changelog_unreleased", "pins"} & {f["check"] for f in found}, (
            found
        )
    assert not hasattr(drift, "changelog_unreleased")


def test_an_adopted_repo_factory_does_not_release_still_gets_the_version_check(tmp_path):
    root = tmp_path / "r"
    shutil.copytree(RECEIPTS, root)
    (root / ".github" / "repo-factory.yml").unlink()
    assert not drift.releases_by_factory(root)
    assert [
        f["check"]
        for f in drift.run(root, tags=RECEIPTS_TAGS, today=TODAY)
        if f["check"] == "version_tags"
    ] == ["version_tags"]


def test_plotkeeper_readiness_names_a_workflow_that_does_not_exist():
    [f] = drift.doc_paths(PLOTKEEPER)
    assert f["where"] == "READINESS.md:23"
    assert ".github/workflows/test.yml" in f["what"]
    assert "ci.yml" in f["fix_hint"]


def test_receipts_documents_name_no_missing_file():
    assert drift.doc_paths(RECEIPTS) == []


def test_doc_paths_ignores_urls_globs_placeholders_and_home_paths(tmp_path):
    (tmp_path / "tools").mkdir()
    (tmp_path / "tools" / "real.py").write_text("")
    (tmp_path / "README.md").write_text(
        "Run `tools/real.py`, then read [the guide](docs/guide.md).\n"
        "Files: `~/.config/x/config.toml`, `/etc/x.yml`, `tests/acceptance/<file>.py`,\n"
        "`src/*.py`, `${HOME}/a.md`, https://example.org/a/b.md, `.aider.chat.history.md`,\n"
        "for example `examples/sample.toml`.\n"
        "```sh\npython scripts/missing.py\n```\n"
        "The bare `real.py` is found anywhere; the bare `gone.sh` is not.\n"
    )
    found = {f["what"].split(" names ")[1].split(",")[0] for f in drift.doc_paths(tmp_path)}
    assert found == {"docs/guide.md", "gone.sh"}


def test_plotkeeper_python_floor_is_end_of_life():
    [f] = drift.python_floor(PLOTKEEPER, TODAY)
    assert f["severity"] == "high" and f["where"] == "README.md:20"
    assert "Python 3.9 reached end of life on 2025-10-31" in f["what"]
    assert ".github/workflows/ci.yml:26" in f["what"]
    assert "owner's call" in f["fix_hint"]


def test_plotkeeper_python_floor_before_its_end_of_life():
    assert drift.python_floor(PLOTKEEPER, datetime.date(2025, 1, 1)) == []


def test_receipts_python_floor_is_fine():
    assert drift.python_floor(RECEIPTS, TODAY) == []


def test_an_end_of_life_python_in_the_matrix_above_the_floor(tmp_path):
    shutil.copytree(RECEIPTS / ".github", tmp_path / ".github")
    (tmp_path / "pyproject.toml").write_text('[project]\nrequires-python = ">=3.8"\n')
    wf = tmp_path / ".github" / "workflows" / "ci.yml"
    wf.write_text(wf.read_text().replace('["3.11", "3.12"]', '["3.8", "3.9", "3.12"]'))
    out = drift.python_floor(tmp_path, TODAY)
    assert [f["severity"] for f in out] == ["high", "medium"]
    assert "Python 3.9" in out[1]["what"] and out[1]["where"].endswith("ci.yml:26")


def test_a_pin_of_the_product_s_own_is_a_major_behind(tmp_path):
    root = tmp_path / "r"
    shutil.copytree(RECEIPTS, root)
    ci = root / ".github" / "workflows" / "ci.yml"
    ci.write_text(ci.read_text() + "      - run: python -m pip install pytest==8.3.3 hypothesis\n")
    out = drift.pins(root, {"pytest": "9.1.1"})
    [f] = out
    assert "pytest is pinned to 8.3.3" in f["what"] and "a major version behind" in f["what"]
    assert "ci.yml is the owner's file" in f["fix_hint"]
    assert "fix PRs may not touch" in f["fix_hint"]


def test_the_pin_factory_templates_wrote_is_not_the_product_s():
    """Receipts' AGENTS.md:30 and ci.yml:38 are the template's own install line."""
    assert drift.pins(RECEIPTS, {"pytest": "9.1.1"}) == []


def test_no_pin_finding_without_latest_or_when_current():
    assert drift.pins(RECEIPTS, None) == []
    assert drift.pins(RECEIPTS, {"pytest": "8.3.3"}) == []
    assert drift.pins(RECEIPTS, {"ruff": "1.0.0"}) == []


def test_actions_pins_report_a_comment_version_behind():
    [f] = drift.actions_pins(RECEIPTS, {"actions/checkout": "v7.1.0"})
    assert f["check"] == "actions_pins" and "v7.0.1" in f["what"]
    assert drift.actions_pins(RECEIPTS, {"actions/checkout": "v7.0.1"}) == []
    assert drift.actions_pins(RECEIPTS, None) == []


def test_readme_promises():
    kept = "Runs no network services and makes no network calls."
    charter = {"promises": [kept, {"text": "Ignored.", "anchor": "It runs on Linux and macOS"}]}
    assert drift.readme_promises(RECEIPTS, charter) == []
    gone = "Uploads every session to a shared dashboard for the team."
    [f] = drift.readme_promises(RECEIPTS, {"promises": [kept, gone]})
    assert f["severity"] == "high" and "promise 2" in f["what"]
    assert drift.readme_promises(RECEIPTS, None) == []


def test_anchor_helpers():
    assert (
        drift.anchor_of("One two three four five six seven eight nine ten")
        == "One two three four five six seven eight"
    )
    assert drift.anchor_of({"text": "a b", "anchor": "the anchor"}) == "the anchor"
    assert drift.anchor_present(
        "It **runs no network\nservices**, and so on.", "Runs no network services"
    )
    assert not drift.anchor_present("runs no services network", "runs no network services")


def test_eol_table_loads():
    eol = drift.load_eol()
    assert eol["python"]["3.9"] == datetime.date(2025, 10, 31)
    assert eol["python"]["3.10"] == datetime.date(2026, 10, 31)
    assert {"python", "node", "go", "ruby"} <= set(eol)


def test_run_skips_checks_without_their_inputs():
    checks = {f["check"] for f in drift.run(PLOTKEEPER, today=TODAY)}
    assert checks == {"doc_paths", "python_floor"}
    every = drift.run(
        RECEIPTS,
        tags=RECEIPTS_TAGS,
        latest={"pytest": "9.1.1"},
        charter={"promises": ["Runs no network services and makes no network calls."]},
        today=TODAY,
    )
    assert every == []
    assert all(set(f) == SHAPE and f["severity"] in {"high", "medium", "low"} for f in every)


def test_a_promise_is_drift_only_when_the_readme_was_seen_to_say_it(tmp_path):
    """A drafted promise often paraphrases the README: an anchor the README never had is not
    drift (it made six false findings a project on the first live run)."""
    (tmp_path / "README.md").write_text(
        "# Tool\n\nRuns no network services and makes no network calls.\n"
    )
    charter = {
        "promises": [
            {"text": "Runs no network services and makes no network calls."},
            {"text": "Keeps every record forever in an append-only log."},
        ]
    }
    assert len(drift.readme_promises(tmp_path, charter)) == 1  # unseen: old rule
    assert drift.readme_promises(tmp_path, charter, set()) == []  # first look: none
    seen = set(drift.anchors_in_readme(tmp_path, charter))
    assert seen == {"Runs no network services and makes no network"}
    (tmp_path / "README.md").write_text("# Tool\n\nA tool.\n")
    got = drift.readme_promises(tmp_path, charter, seen)
    assert len(got) == 1 and "promise 1" in got[0]["what"]
    assert [
        f["check"]
        for f in drift.run(tmp_path, charter=charter, seen_anchors=set())
        if f["check"] == "readme_promises"
    ] == []
