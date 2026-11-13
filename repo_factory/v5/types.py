"""Types shared between the bench and the v5 pipeline (build v5 §3)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass
class Prebuilt:  # a base build made by hsuite.arms.base_build (the bench shares it across arms)
    workspace: Path
    session_id: str
    config_dir: Path
    usd: float
    wall_s: float
    transcript: Path
