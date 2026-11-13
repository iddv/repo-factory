"""Harness protocol: an adapter around a coding agent."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass
class HarnessResult:
    artifact_dir: Path  # what the worker produced
    tool_calls: int
    input_tokens: int
    output_tokens: int
    cost_usd: float | None
    transcript_path: Path  # raw event log for inspection
    exit_ok: bool
    wall_s: float = 0.0
    exit_reason: str = "ok"  # ok | max_turns | max_budget | timeout | auth | error
    session_id: str | None = None  # the worker's Claude Code session (resumable runs only)
    # A resumed session reports cost cumulatively over its whole history; ``cost_usd`` is
    # always this run's own cost, ``session_cost_usd`` the session's cumulative figure (what
    # a run resuming this one subtracts).
    session_cost_usd: float | None = None
    model: str | None = None  # the model the worker actually ran on (its init event)
    num_turns: int | None = None
    resume_failed: bool = False  # a resumed run whose session could not be found (the
    #                                 executor reruns it fresh, build v3 §3)
    fingerprint: dict | None = None  # profile.launch runs: profile.fingerprint(transcript)
    auth_failed: bool = False  # the run ended because the Claude login failed (an
    #                                 expired OAuth session, a 401): exit_reason "auth"
    tools_by_server: dict | None = None  # MCP tool calls per server ({"rea": 14})
    skills: dict | None = None  # Skill tool calls by skill ({"frontend-design:frontend-design": 1})
    agents: dict | None = None  # subagents started by type ({"feature-dev:code-explorer": 2})


class Harness(Protocol):
    name: str

    def run(self, prompt: str, workspace: Path, timeout_s: int, **opts) -> HarnessResult: ...

    # opts (policy v2 milestone runs; a harness may ignore what it can't do):
    #   max_turns, max_usd: per-run caps that override the harness defaults;
    #   session: {"id": uuid, "resume": parent session id or None, "base_cost_usd": the
    #   parent session's cumulative cost}: keep the session and, with "resume", continue
    #   (fork) the parent's conversation in the same workspace path. Build v3 §3: a design
    #   task's forks resume from their own copies of the workspace ("lineage": the session
    #   ids up the tree, which the Claude Code harness makes resumable from there).


def get_harness(name: str, **kw) -> Harness:
    if name == "fake":
        from .fake import FakeHarness

        return FakeHarness(**kw)
    if name in ("claude-code", "claude_code"):
        from .claude_code import ClaudeCodeHarness

        return ClaudeCodeHarness(**kw)
    raise KeyError(f"unknown harness {name!r}")
