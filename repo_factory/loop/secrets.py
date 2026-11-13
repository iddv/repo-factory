"""The pre-push scan: nothing with a secret in it is ever pushed.

Three passes over every file of the tree to publish:
- **files** that should never be in a repo: ``.env`` (``.env.example`` is fine), private keys
  and certificates' keys, ``.netrc``/``.pypirc``, credential JSON, and database files (they
  hold whatever data a run left behind);
- **known token shapes** (cloud keys, GitHub/Anthropic/OpenAI/Slack/Stripe/Google tokens,
  private key blocks, JWTs);
- **entropy**: a long random-looking value assigned to a secret-sounding name
  (``password``, ``token``, ``api_key``, ...). Test passwords like ``"s3cret-pass"`` are short
  and low-entropy and pass; a 32-character random string does not.

A line ending in ``# pragma: allowlist secret`` is skipped (the detect-secrets marker).
Hits come back with the value redacted; the caller holds the build and lists them."""

from __future__ import annotations

import math
import re
from pathlib import Path

SKIP_DIRS = {
    ".git",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".den",
}
LOCKFILES = {
    "uv.lock",
    "poetry.lock",
    "package-lock.json",
    "pnpm-lock.yaml",
    "yarn.lock",
    "Pipfile.lock",
    "Cargo.lock",
}
BAD_FILES = [
    (
        "env file",
        re.compile(r"^\.env(\.[\w-]+)?$"),
        re.compile(r"\.(example|sample|template|dist)$"),
    ),
    (
        "private key file",
        re.compile(r"(^id_(rsa|dsa|ecdsa|ed25519)$|\.(pem|key|p12|pfx|jks|keystore)$)"),
        None,
    ),
    (
        "credentials file",
        re.compile(
            r"^(\.netrc|\.pypirc|\.npmrc|credentials(\.json)?|"
            r"service[-_]account.*\.json|client_secret.*\.json)$"
        ),
        None,
    ),
    ("database file", re.compile(r"\.(db|sqlite|sqlite3|db-wal|db-shm)$"), None),
]
TOKENS = [
    ("AWS access key", re.compile(r"\b(AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("GitHub token", re.compile(r"\b(gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{40,})\b")),
    ("Anthropic key", re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}")),
    ("OpenAI key", re.compile(r"\bsk-(proj-)?[A-Za-z0-9]{32,}\b")),
    ("Slack token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}")),
    ("Stripe live key", re.compile(r"\b[sr]k_live_[0-9A-Za-z]{20,}")),
    ("Google API key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    ("private key block", re.compile(r"-----BEGIN ([A-Z]+ )?PRIVATE KEY-----")),
    ("JWT", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}")),
]
NAMED = re.compile(r"""(?ix)
    (?:password|passwd|pwd|secret|token|api[_-]?key|apikey|access[_-]?key|private[_-]?key|
       client[_-]?secret|auth[_-]?key|credential)s?\w*
    ["']?\s*[:=]\s*[rbuf]?["']([^"'\s]{20,})["']""")
PLACEHOLDER = re.compile(
    r"(?i)example|changeme|change-me|your[_-]|xxxx|placeholder|dummy|"
    r"\$\{|\{\{|<[^>]+>|not[_-]?a[_-]?secret|redacted|0123456789"
)
ALLOW = "pragma: allowlist secret"
MAX_BYTES = 2_000_000


def entropy(s: str) -> float:
    if not s:
        return 0.0
    n = len(s)
    return -sum(c / n * math.log2(c / n) for c in (s.count(ch) for ch in set(s)))


def looks_random(v: str) -> bool:
    classes = sum(bool(re.search(p, v)) for p in (r"[a-z]", r"[A-Z]", r"\d"))
    return len(v) >= 20 and classes >= 2 and entropy(v) >= 4.0 and not PLACEHOLDER.search(v)


def redact(s: str) -> str:
    return s[:4] + "…" + f"({len(s)} chars)" if len(s) > 8 else "…"


def scrub(text: str) -> str:
    """``text`` with every known token shape, and a random value assigned to a secret name,
    replaced by ``[redacted: <kind>]``: for words the page shows (an activity row, a press's
    error), never for files."""
    out = str(text)
    for kind, pat in TOKENS:
        out = pat.sub(f"[redacted: {kind}]", out)
    return NAMED.sub(
        lambda m: (
            m.group(0).replace(m.group(1), "[redacted: secret]")
            if looks_random(m.group(1))
            else m.group(0)
        ),
        out,
    )


def files(root: Path) -> list[Path]:
    out = []
    for p in sorted(Path(root).rglob("*")):
        rel = p.relative_to(root).parts
        if any(part in SKIP_DIRS for part in rel[:-1]) or p.is_dir() or p.is_symlink():
            continue
        out.append(p)
    return out


def scan_text(text: str) -> list[tuple[int, str, str]]:
    hits = []
    for n, line in enumerate(text.splitlines(), 1):
        if ALLOW in line:
            continue
        for kind, pat in TOKENS:
            m = pat.search(line)
            if m:
                hits.append((n, kind, redact(m.group(0))))
        for m in NAMED.finditer(line):
            if looks_random(m.group(1)):
                hits.append((n, "random value assigned to a secret name", redact(m.group(1))))
    return hits


def scan(root: Path) -> list[dict]:
    """``[{"file", "line", "kind", "match"}]`` for the tree at ``root``; empty: clean."""
    root = Path(root)
    hits = []
    for p in files(root):
        rel = p.relative_to(root).as_posix()
        for kind, pat, ok in BAD_FILES:
            if pat.search(p.name) and not (ok and ok.search(p.name)):
                hits.append({"file": rel, "line": 0, "kind": kind, "match": p.name})
        if p.name in LOCKFILES or p.stat().st_size > MAX_BYTES:
            continue
        raw = p.read_bytes()
        if b"\0" in raw[:8192]:
            continue  # binary
        for n, kind, m in scan_text(raw.decode("utf-8", errors="replace")):
            hits.append({"file": rel, "line": n, "kind": kind, "match": m})
    return hits
