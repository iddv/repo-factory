"""A failed `claude -p` is reported in its own words (a usage limit, a sign-in problem), not
as the raw JSON it printed."""

import json

from repo_factory.costs.ledger import _said


def test_a_json_answer_is_reported_by_its_result():
    out = json.dumps(
        {
            "type": "result",
            "is_error": True,
            "api_error_status": 429,
            "result": "You've hit your weekly limit · resets Monday",
        }
    )
    assert _said(out, "") == "You've hit your weekly limit · resets Monday"


def test_anything_else_is_the_end_of_what_it_printed():
    assert _said("not json", "the error") == "the error"
    assert _said("x" * 900, "") == "x" * 500
