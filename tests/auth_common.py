"""The tests' stand-in for a signed-in browser (OI): the UI's token from the session's token
store (``REPO_FACTORY_TOKENS_DIR``, a temporary folder made by conftest), sent as a bearer header."""

from repo_factory.tokens import Store


def ui_bearer() -> dict:
    return {"Authorization": "Bearer " + (Store().value("ui") or "")}


def bearer(name: str) -> dict:
    return {"Authorization": "Bearer " + (Store().value(name) or "")}
