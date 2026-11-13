"""The README's test commands get as long as the exam's own pytest run of the product's tests
(900 s), so a README command that runs the product's whole suite is not held for taking
longer than a shorter limit (Sidedoor's suite took about 330 s)."""

import yaml

from repo_factory.resources import POLICIES
from repo_factory.v5 import commands, exam, readiness


def test_a_test_command_gets_as_long_as_the_exams_own_run():
    suite = exam.TIMEOUTS["pytest_s"]
    policy = yaml.safe_load((POLICIES / "v5" / "rules.yaml").read_text())["readiness"]["commands"]
    for t in (commands.TIMEOUTS, policy, readiness.DEFAULTS["commands"], exam.TIMEOUTS):
        assert t["test_s"] >= suite, t
        assert t["tests_total_s"] >= t["test_s"], t
