"""`repo-factory loop add "..."` works as a first command: the default or configured home is
started on first use, as `repo-factory ui` starts it; a --home that does not exist is refused."""

import pytest

from repo_factory.cli import main
from repo_factory.loop.ledger import Ledger


def test_the_configured_home_is_started_on_first_use(tmp_path, monkeypatch, capsys):
    home = tmp_path / "fresh" / "home"
    monkeypatch.setenv("REPO_FACTORY_HOME", str(home))
    main(["loop", "add", "small clinics still schedule patients on paper"])
    assert home.is_dir() and (home / "loop.db").is_file()
    [idea] = Ledger(home / "loop.db").all()
    assert idea["text"].startswith("small clinics") and idea["state"] == "new"
    main(["loop", "list"])
    assert idea["id"] in capsys.readouterr().out


def test_a_home_passed_by_hand_that_does_not_exist_is_refused(tmp_path):
    typo = tmp_path / "hmoe"
    with pytest.raises(SystemExit, match="no home at"):
        main(["loop", "--home", str(typo), "add", "a task"])
    assert not typo.exists()
