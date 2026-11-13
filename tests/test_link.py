"""`repo-factory link`: a new one-time sign-in link for the running page, from its home."""

import pytest

from repo_factory import tokens
from repo_factory.ui.server import URL_FILE, sign_in_link


def test_a_link_for_the_running_page_signs_in_once(tmp_path):
    (tmp_path / URL_FILE).write_text("http://127.0.0.1:8791/\n")
    link = sign_in_link(tmp_path)
    assert link.startswith("http://127.0.0.1:8791/auth/open?code=")
    code = link.split("code=", 1)[1]
    store = tokens.Store(home=tmp_path)
    assert store.redeem_code(code) is True
    assert store.redeem_code(code) is False


def test_without_a_running_page_it_says_so(tmp_path):
    with pytest.raises(SystemExit, match="no page is running"):
        sign_in_link(tmp_path)
