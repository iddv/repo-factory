"""Pausing maintenance on a repository is written in repo-factory (``maintain/pauses.py``); the
page's pause (``api/maintenance/pause``) and the poll's skipping of it are tested with the poll
(``test_f3_maintenance.py``). Here: a pause file that cannot be read pauses nothing, and no
registry action reaches the projects package any more."""

from repo_factory import registry as R
from repo_factory.maintain import pauses


def test_a_pause_file_that_cannot_be_read_pauses_nothing(tmp_path):
    from .loop_common import make_loop

    lp = make_loop(tmp_path)
    pauses.path(lp.cfg.home).parent.mkdir(parents=True, exist_ok=True)
    for text in ("not json", "[1, 2]", '{"o/x": null, "o/y": 3}', "null"):
        pauses.path(lp.cfg.home).write_text(text)
        assert pauses.read(lp.cfg.home) == {}
    assert pauses.words(None) is None


def test_no_action_reaches_the_projects_package():
    gone = (
        "pause_project",
        "pause_maintenance",
        "edit_charter",
        "redraft_charter",
        "restore_charter",
        "evolve_now",
        "accept_change",
        "decline_change",
        "charter_change",
        "undo_change",
        "maintain_merge_policy",
    )
    assert not [k for k in gone if k in R.ACTIONS]
    assert not [a for a in R.ACTIONS.values() if "repo-factory projects " in a.cmd]
