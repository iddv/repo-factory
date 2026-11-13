"""Expected states answered as states, not errors (QA B20, REV3 fix 12): the page's console
showed an error for each on a fresh install. GitHub not connected yet lists no repositories
(200 with ``listed: false`` and why; it was a 409); every live-feed place taken says ``busy``
(tests/test_live.py; it was a 503). The map is asked for only once there is one (the page's
own read, map.svelte.test.ts)."""

from types import SimpleNamespace

from repo_factory import github_repos
from repo_factory.gh import PublishError
from repo_factory.ui.routes import github as routes


def test_github_not_connected_lists_no_repositories_and_says_why(tmp_path, monkeypatch):
    def refuse(key, fn, fresh=False):
        raise PublishError("repo-factory is not connected to GitHub")

    monkeypatch.setattr(github_repos, "cached", refuse)
    out = routes._repos(SimpleNamespace(loop_home=tmp_path), SimpleNamespace(data={}))
    assert out["listed"] is False and out["repos"] == []
    assert out["why"].startswith("GitHub did not list the repositories: ")
    assert "not connected" in out["why"]
