"""A tiny vault: stores notes under a data directory and issues an access token."""
import os
import secrets
from pathlib import Path


def data_dir() -> Path:
    return Path(os.environ.get("VAULT_DIR") or Path.home() / ".vault")


def new_token() -> str:
    # made at runtime, never written into the source: a canary shaped like a live key
    return "sk_" + "live_" + secrets.token_hex(16)


def put(name: str, text: str) -> Path:
    root = data_dir().resolve()
    p = (root / name).resolve()
    if root not in p.parents:
        raise ValueError("a note name may not leave the vault")
    root.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    return p
