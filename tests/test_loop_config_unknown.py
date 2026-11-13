"""A rolled-back repo-factory runs older code on a newer loop.yaml: a settings page of the newer version may have written keys this version does not know.
The loop's Config merges loop.yaml over DEFAULTS and must keep ignoring such keys, never
reject them, at the top level and inside a section."""

from repo_factory.settings.store import DEFAULTS, Config


def test_unknown_keys_are_kept_and_ignored(tmp_path):
    (tmp_path / "loop.yaml").write_text(
        "concurrency: 1\n"
        "a_setting_from_the_future: {nested: true}\n"
        "build:\n  retries: 2\n  a_newer_build_key: 7\n"
        "maintain:\n  a_newer_maintain_key: [1, 2]\n"
    )
    cfg = Config(tmp_path)
    assert cfg["concurrency"] == 1
    assert cfg["build"]["retries"] == 2
    # everything this version knows still has its default
    assert cfg["build"]["max_resumes"] == DEFAULTS["build"]["max_resumes"]
    assert cfg["maintain"]["poll_s"] == DEFAULTS["maintain"]["poll_s"]
    assert cfg["tick_s"] == DEFAULTS["tick_s"]


def test_an_unknown_key_does_not_change_the_defaults(tmp_path):
    (tmp_path / "loop.yaml").write_text("publish:\n  anyway:\n    newer: x\n")
    Config(tmp_path)
    assert "newer" not in DEFAULTS["publish"]["anyway"]
