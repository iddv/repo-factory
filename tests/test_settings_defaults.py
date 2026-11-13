"""The defaults, no key nothing reads, and an override that is used. One build at a time unless
configured; the keys no code read (among them a spend cap that capped nothing,
projects.evolve.max_usd) are gone; a project's own evolve.model is the model its check calls."""

import pytest

from repo_factory.settings import schema, store
from repo_factory.settings import write as W

GONE = (
    "maintain.reruns",
    "maintain.adopt.clone_timeout_s",
    "projects.charter.redraft_after_merges",
    "projects.topic.max_sources",
    "projects.evolve.tools",
    "projects.evolve.max_usd",
    "projects.health.quiet_days",
    "ideas.editor.read_budget",
    "ideas.editor.beats",
    "ideas.editor.memory_weeks",
    "ideas.editor.timeout_s",
    "ideas.research.timeout_s",
)


def has(d, key):
    for k in key.split("."):
        if not isinstance(d, dict) or k not in d:
            return False
        d = d[k]
    return True


def nest(key, value):
    out = value
    for k in reversed(key.split(".")):
        out = {k: out}
    return out


def test_one_build_at_a_time_unless_configured():
    assert store.DEFAULTS["concurrency"] == 1


@pytest.mark.parametrize("key", GONE)
def test_a_key_nothing_read_is_gone(key):
    assert not has(store.DEFAULTS, key)
    assert key not in W.RULES and key not in schema.USD_CAPS


def test_a_projects_own_evolve_model_is_the_one_called(monkeypatch):
    from repo_factory.loop import models
    from repo_factory.projects import evolve

    seen = []
    monkeypatch.setattr(models, "claude", lambda m, **kw: seen.append(m) or (lambda t: t))
    cfg = {"projects": {"evolve": {"model": "claude-sonnet-5-5"}}}
    evolve.model_for(cfg, {"settings": {"evolve.model": "claude-opus-5-5"}})
    evolve.model_for(cfg, {"settings": {}})
    evolve.model_for(cfg)
    assert seen == ["claude-opus-5-5", "claude-sonnet-5-5", "claude-sonnet-5-5"]
