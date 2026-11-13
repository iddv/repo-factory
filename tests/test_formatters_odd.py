"""The loop's formatters over every shape a stored idea can take (repo-factory-notes
plans/CTR-DESIGN.md §4.5): an idea's columns come from their contract types' strategies
(``stored.testing.strategy``) read back as the ledger reads them (``stored.kept``), so a field
added to a type is generated on the next run. A formatter never raises, and the page's idea
row keeps its type."""

from __future__ import annotations

import json

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from repo_factory.loop import describe
from repo_factory.loop.ledger import CONTRACTS, STATES
from repo_factory.stored import kept
from repo_factory.stored.testing import strategy
from repo_factory.ui import api
from repo_factory.ui import types as T

from .loop_common import make_loop

SETTINGS = settings(
    max_examples=150,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
)
TEXT = st.none() | st.text(max_size=30)


def column(name):
    """A column's value as a writer of its type stores it and the ledger reads it back."""
    return strategy(CONTRACTS[name]).map(lambda v: kept(CONTRACTS[name], v.to_json()))


ideas = st.fixed_dictionaries(
    {
        "id": st.just("iodd00001"),
        "state": st.sampled_from(STATES),
        "source": st.just("cli"),
        "source_ref": st.none(),
        "title": TEXT,
        "text": st.text(min_size=1, max_size=40),
        "context": TEXT,
        "reason": TEXT,
        "task_dir": st.none(),
        "kind": st.just("app"),
        "later_until": st.none() | st.floats(0, 4e9),
        "created": st.floats(0, 4e9),
        "updated": st.floats(0, 4e9),
        **{k: column(k) for k in CONTRACTS},
    }
)


@SETTINGS
@given(ideas)
def test_the_pitchs_sentences_and_the_release_text_come_from_any_idea(idea):
    out = describe.pitch_sentences(idea)
    assert all(isinstance(s, str) for s in out)
    text, by = describe.release(idea, None)
    assert by in ("owner", "product", "pitch") and (text is None or isinstance(text, str))


@pytest.fixture(scope="module")
def app(tmp_path_factory):
    lp = make_loop(tmp_path_factory.mktemp("odd-rows"), intake={"auto": {"mode": "manual"}})
    return api.App(lp.cfg.tasks_dir, "fake", lp.cfg.home)


@SETTINGS
@given(idea=ideas)
def test_the_pages_idea_row_is_built_from_any_idea_and_keeps_its_type(app, idea):
    row = app.idea_row(idea, {})
    json.dumps(row)
    assert T.problems(row, T.IdeaRow) == [], row
