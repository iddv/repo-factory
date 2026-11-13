"""The AGENTS.md repo-factory renders carries no sentence of SCOPE.md's (AU2 #1).

SCOPE.md is the scope stage's spec, written from the pitch before the build. Sidedoor's
rendered AGENTS.md took its "Does:" line from it ("…and records every credential that
reaches the target."), and that sentence held the push on every check, whatever was fixed:
staging rendered it again from the unchanged SCOPE.md. The replay is Sidedoor's staged
SCOPE.md paragraph, copied read-only (publish/i235df527/sidedoor/SCOPE.md:3)."""

from __future__ import annotations

from pathlib import Path

from repo_factory.loop import describe, publish
from repo_factory.maintain import agentsmd

SIDEDOOR_SCOPE = (
    "# Scope: Sidedoor\n\n"
    "Sidedoor is a command-line tool that a developer, platform engineer or security engineer "
    "runs on their own machine (or in CI) to test whether a coding agent fetches secrets from "
    "the intended broker or quietly takes them from somewhere else. For each trial it builds a "
    "throwaway sandbox, plants unique decoy credentials in the usual hiding spots, and starts a "
    "local mock broker and a local target API. It then gives the operator's chosen agent a task "
    "that needs a credential, and records every credential that reaches the target. At the end "
    'it states plainly either "went through the broker" or "snuck around it via <door>", '
    "with evidence. v1 covers repeat runs, comparison across agents, and exportable reports.\n\n"
    "## Users\n\n- **Operator**: the person at the terminal. Records are never deleted.\n\n"
    "## Core flows\n\n### F1: Install and first run\n### F2: Run a trial\n"
)
README = (
    "# Sidedoor\n\nSidedoor is a command-line tool that checks whether a coding agent gets "
    "its secrets from the intended broker or from somewhere else on the machine.\n\n"
    "## Install\n\n```sh\n./install.sh\n```\n"
)


def staged(root: Path) -> Path:
    root.mkdir(parents=True)
    (root / "SCOPE.md").write_text(SIDEDOOR_SCOPE)
    (root / "README.md").write_text(README)
    (root / "sidedoor").mkdir()
    (root / "sidedoor" / "__init__.py").write_text('"""Sidedoor."""\n')
    return root


def scope_sentences() -> list[str]:
    body = SIDEDOOR_SCOPE.split("\n\n", 1)[1]
    return [s for s in describe.split_sentences(body) if len(s.split()) >= 5]


def test_sidedoor_replayed_its_rendered_agents_md_copies_no_scope_sentence(tmp_path):
    stage = staged(tmp_path / "sidedoor")
    rec = publish.maintenance_files(stage, "iddv/sidedoor", "policy")
    assert rec["rendered"] == "AGENTS.md"
    agents = (stage / "AGENTS.md").read_text()
    assert "records every credential that reaches the target" not in agents
    assert [s for s in scope_sentences() if s in agents] == []
    assert f"- **Does:** {agentsmd.DOES}" in agents
    assert "SCOPE.md" in agents  # pointed to, never quoted
    assert "F1–F2" in agents  # the flow numbers are facts


def test_the_text_written_from_the_product_still_replaces_the_does_line(tmp_path):
    stage = staged(tmp_path / "sidedoor")
    publish.maintenance_files(stage, "iddv/sidedoor", "policy")
    was = describe.put_agents(
        stage / "AGENTS.md",
        None,
        "unused",
        "Plants decoy credentials and reports whether the agent used the broker or a decoy.",
    )
    assert was["does"] == agentsmd.DOES
    assert "- **Does:** Plants decoy credentials" in (stage / "AGENTS.md").read_text()
