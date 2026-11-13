"""What one full-profile session carries beyond the profile itself: ``Launch``.

``ClaudeCodeHarness.command`` turns it into flags (its allow rules after the profile's) and
the sandbox into binds, ``--setenv`` and the workspace's path inside. v5 uses it for the
exam's mounts and the fixed product path (``/work/product``)."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Launch:
    """One session's extras. ``allow``: permission rules beside the profile's; ``only``:
    these tools (plus ``allow``) instead of the profile's allow rules; ``env``: non-secret
    variables for the session (``--setenv``); ``mounts``: sandbox binds (``{kind, src, dst,
    why}``); ``hosts``: names to add to the proxy's allow-list; ``workdir``: where the
    workspace appears inside the sandbox (None: at its own path)."""

    allow: list = field(default_factory=list)
    only: list | None = None
    env: dict = field(default_factory=dict)
    mounts: list = field(default_factory=list)
    hosts: list = field(default_factory=list)
    workdir: str | None = None

    def __bool__(self) -> bool:
        return bool(
            self.allow or self.only or self.env or self.mounts or self.hosts or self.workdir
        )
