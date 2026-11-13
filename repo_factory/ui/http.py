"""What ``api.handle`` answers with: ``(status, content type, body)``, and the error a handler
raises for a 4xx. Shared by ``api.py`` and the route modules (``ui/routes/``)."""

from __future__ import annotations

import json
from pathlib import Path


class HttpError(Exception):
    def __init__(self, code: int, msg: str):
        super().__init__(msg)
        self.code = code


def _json(code: int, obj) -> tuple[int, str, bytes]:
    return code, "application/json; charset=utf-8", json.dumps(obj, default=str).encode()


def _file(p: Path, ctype: str) -> tuple[int, str, bytes]:
    return 200, ctype + ("; charset=utf-8" if ctype.startswith("text/") else ""), p.read_bytes()
