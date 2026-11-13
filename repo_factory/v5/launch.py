"""The one import point for starting a Claude Code session (docs/design/build-v5.md §4.1).

Every v5 session (the build, the verifier, repairs, fix-ups) starts here, through
``harness/profile.launch``. Tests pass ``harness=`` (a FakeHarness or a scripted one).
``spec``: the session's ``harness.launch.Launch`` (binds, env, the workspace's path inside);
a test double gets it as ``launch=`` when there is one."""

from __future__ import annotations

from pathlib import Path

from ..harness.base import HarnessResult
from ..harness.profile import launch as _profile_launch


def launch(
    prompt: str,
    workspace: Path,
    *,
    config_dir: Path,
    max_turns: int,
    max_usd: float,
    timeout_s: int,
    session: dict | None = None,
    model: str | None = None,
    harness=None,
    spec=None,
) -> HarnessResult:
    """One full-profile session in ``workspace``. ``session``: ``{"id", "resume",
    "base_cost_usd"}`` as ``ClaudeCodeHarness.command`` takes it (``resume`` forks)."""
    Path(config_dir).mkdir(parents=True, exist_ok=True)
    if harness is not None:
        more = {"launch": spec} if spec else {}
        return harness.run(
            prompt,
            Path(workspace),
            timeout_s,
            max_turns=max_turns,
            max_usd=max_usd,
            session=session,
            tools="full",
            **more,
        )
    return _profile_launch(
        prompt,
        Path(workspace),
        config_dir=Path(config_dir),
        max_turns=max_turns,
        max_usd=max_usd,
        timeout_s=timeout_s,
        session=session,
        model=model,
        spec=spec,
    )
