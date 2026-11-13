"""The repository picker's list (WZ-DESIGN §4.1, WZ-4): reads only, through a fake gh that
records every call; forks, archived, empty and already-maintained repositories left out;
newest push first; two pages at most; one organisation on request; the App's own list."""

from __future__ import annotations

import json

from repo_factory import github_repos as R

from .fake_github_user import repo


class FakeGh:
    def __init__(self, pages: dict[str, list]):
        self.pages, self.calls = pages, []

    def __call__(self, *args, **kw):
        self.calls.append(args)
        assert args[0] == "api" and len(args) == 2, args  # reads: gh api PATH, no -X
        return json.dumps(self.pages.get(args[1], []))


def test_the_list_is_the_persons_own_newest_first_without_what_is_not_a_project():
    rows = [
        repo("ann/old", pushed="2025-01-01T00:00:00Z"),
        repo("ann/new", pushed="2026-10-16T00:00:00Z"),
        repo("ann/fork", fork=True),
        repo("ann/gone", archived=True),
        repo("ann/empty", size=0),
        repo("ann/kept", private=True, issues=3),
        repo("ann/done"),
    ]
    gh = FakeGh(
        {
            "user/repos?affiliation=owner&sort=pushed&per_page=100&page=1": rows,
            "user/orgs?per_page=100": [{"login": "acme"}],
        }
    )
    got = R.listing(gh, maintained=["Ann/Done"])
    assert [r["repo"] for r in got["repos"]] == ["ann/new", "ann/kept", "ann/old"]
    assert got["repos"][1] == {
        "repo": "ann/kept",
        "private": True,
        "language": "Python",
        "pushed_at": "2026-10-15T10:00:00Z",
        "open_issues": 3,
        "can_push": True,
    }
    assert got["left_out"] == 4 and got["orgs"] == ["acme"]
    assert all(c[1].startswith(("user/repos", "user/orgs")) for c in gh.calls)


def test_two_pages_at_most_and_an_organisation_on_request():
    full = [repo(f"ann/r{i}") for i in range(100)]
    gh = FakeGh(
        {f"user/repos?affiliation=owner&sort=pushed&per_page=100&page={n}": full for n in (1, 2, 3)}
    )
    assert len(R.listing(gh)["repos"]) == 200
    assert not any("page=3" in c[1] for c in gh.calls)
    gh = FakeGh({"orgs/acme/repos?sort=pushed&per_page=100&page=1": [repo("acme/site")]})
    got = R.listing(gh, "acme")
    assert [r["repo"] for r in got["repos"]] == ["acme/site"] and got["orgs"] == []


def test_factory_app_alone_lists_what_it_was_given():
    gh = FakeGh(
        {
            "installation/repositories?per_page=100&page=1": {
                "total_count": 1,
                "repositories": [repo("ann/picked")],
            }
        }
    )
    gh.pages["installation/repositories?per_page=100&page=1"] = {
        "total_count": 1,
        "repositories": [repo("ann/picked")],
    }
    assert [r["repo"] for r in R.listing(gh, app_only=True)["repos"]] == ["ann/picked"]


def test_the_cache_keeps_ten_minutes_unless_fresh():
    n = []
    R._cache.clear()
    assert R.cached(("t",), lambda: n.append(1) or {"x": len(n)}) == {"x": 1}
    assert R.cached(("t",), lambda: n.append(1) or {"x": len(n)}) == {"x": 1}
    assert R.cached(("t",), lambda: n.append(1) or {"x": len(n)}, fresh=True) == {"x": 2}
