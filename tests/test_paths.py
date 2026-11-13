"""The one home (``repo_factory/paths.py``): where it is, and what is inside it."""

from __future__ import annotations

import re
from pathlib import Path

from repo_factory import paths

ROOT = Path(__file__).resolve().parents[1]


def test_the_home_is_the_flag_then_the_environment_then_the_default(monkeypatch, tmp_path):
    monkeypatch.delenv(paths.HOME_ENV, raising=False)
    assert paths.home() == Path("~/.repo-factory").expanduser().resolve()
    monkeypatch.setenv(paths.HOME_ENV, str(tmp_path / "env"))
    assert paths.home() == (tmp_path / "env").resolve()
    assert paths.home(tmp_path / "flag") == (tmp_path / "flag").resolve()


def test_everything_is_inside_the_home(tmp_path):
    lay = paths.Layout(tmp_path)
    for name in (
        "settings",
        "db",
        "lock",
        "pid",
        "log",
        "stop",
        "pause",
        "actions",
        "maintain",
        "projects",
        "publish",
        "tokens",
        "claude",
        "github",
        "prompts",
        "tasks",
    ):
        assert getattr(lay, name).parent == tmp_path, name


def test_the_settings_tasks_folder_defaults_to_the_homes(tmp_path):
    from repo_factory.settings.store import Config

    cfg = Config(tmp_path)
    assert cfg.tasks_dir == tmp_path / "tasks"


def test_the_stores_live_in_the_home(tmp_path, monkeypatch):
    from repo_factory import claude_login, github_app, tokens

    for k in ("REPO_FACTORY_TOKENS_DIR", "REPO_FACTORY_CLAUDE_DIR", "REPO_FACTORY_GITHUB_DIR"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv(paths.HOME_ENV, str(tmp_path))
    assert tokens.tokens_dir() == tmp_path.resolve() / "tokens"
    assert claude_login.store_dir() == tmp_path.resolve() / "claude"
    assert github_app.store_dir() == tmp_path.resolve() / "github"


def test_nothing_writes_under_dot_config():
    """No module keeps a folder of its own under ~/.config: the home holds everything."""
    bad = []
    for f in (ROOT / "repo_factory").rglob("*.py"):
        for n, line in enumerate(f.read_text().splitlines(), 1):
            code = line.split("#", 1)[0]
            if re.search(r"""\.config["']\s*/\s*["']repo""", code):
                bad.append(f"{f.relative_to(ROOT)}:{n}")
    assert not bad, bad
