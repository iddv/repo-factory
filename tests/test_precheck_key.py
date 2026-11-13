"""The pre-push checks are keyed on what they read, and the press scans with the installed repo-factory
(AU2 #4, #7, #8).

The checks' key held the whole pitch, so every owner press made them stale and started the
model again. Sidedoor's owner saved the release text twice with the text already there (10-12
07:20 and 10-16 20:54: the history's "was" equals "now"), and each save cost another run. The
key now holds only the pitch's fields the checks read; the push reads the owner's release text
as it is then. Sidedoor is replayed from HC's fixtures (copied read-only from the live
loop.db)."""

from repo_factory.loop import precheck

from .test_public_text import fake_gh, repos, sidedoor  # noqa: F401 (fake_gh: autouse)


def test_sidedoors_identical_release_text_saves_no_longer_rerun_the_checks(tmp_path, fake_gh):
    lp, iid, hon, model, fx = sidedoor(tmp_path)
    assert lp.retry(iid)["state"] == "ready"
    assert lp.precheck(iid)["state"] == "done"
    assert hon.calls == ["describe", "review"]
    text = fx["pitch"]["release"]["text"]
    for _ in range(2):  # the owner's two ritual saves
        lp.set_release_text(iid, text, origin="ui")
        assert precheck.status(lp.ledger.get(iid), lp.cfg)["state"] == "done"
    # a new text is saved, and still no new run: the push reads it as it is now
    lp.set_release_text(
        iid, "Sidedoor checks where a coding agent gets its secrets from.", origin="ui"
    )
    assert precheck.status(lp.ledger.get(iid), lp.cfg)["state"] == "done"
    lp.tick()
    assert lp.ledger.get(iid)["state"] == "ready"  # it waits for the owner's press
    assert lp.publish(iid)["state"] == "published"
    assert hon.calls == ["describe", "review"]  # the model ran once, all along
    r = repos(fake_gh)["owner/sidedoor"]
    assert r["description"] == "Sidedoor checks where a coding agent gets its secrets from."
    assert r["releases"][0]["notes"].startswith(
        "First release of Sidedoor: Sidedoor checks where a coding agent gets its secrets from."
    )


def test_a_change_the_checks_read_still_makes_them_stale(tmp_path):
    lp, iid, hon, model, fx = sidedoor(tmp_path)
    lp.retry(iid)
    lp.precheck(iid)
    i = lp.ledger.get(iid)
    lp.ledger.update(iid, pitch={**i["pitch"], "product": "Sidedoor Two"})
    assert precheck.status(lp.ledger.get(iid), lp.cfg)["state"] == "stale"
