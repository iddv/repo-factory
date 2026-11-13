"""The provenance scan (EX-PLAN §4.6) and the fixed NOTICE (§4.5), PR 3.

The scan matches the exact ids a build minted, next to their kind word, and repo-factory's own
literal words, in the product's prose only; never id shapes. A product whose own domain is
AI tooling ("oracle", "verifier", "claim c17", "the panel voted") yields nothing."""

import json
import shutil
from pathlib import Path

from repo_factory.loop import publish
from repo_factory.v5 import jargon

from .exam_fixture import FIXTURES, expected

NOTICE = Path(__file__).parent / "fixtures" / "notice" / "NOTICE.expected"


def tree(tmp_path, name="ai_tooling"):
    dest = tmp_path / "product"
    shutil.copytree(FIXTURES / name / "product", dest)
    return dest, FIXTURES / name / "round"


def test_an_ai_tooling_products_own_words_are_not_factory(tmp_path):
    exp = expected("ai_tooling")
    root, rnd = tree(tmp_path)
    assert jargon.scan_round(rnd, root) == exp["provenance"]
    # the same README with one line naming an id this round minted, next to its kind
    readme = root / "README.md"
    lines = readme.read_text().splitlines()
    lines.insert(exp["minted_hit"]["line"] - 1, exp["with_minted_line"])
    readme.write_text("\n".join(lines) + "\n")
    hits = jargon.scan_round(rnd, root)
    assert [(h["file"], h["line"], h["kind"]) for h in hits] == [
        ("README.md", exp["minted_hit"]["line"], exp["minted_hit"]["kind"])
    ]


def test_ids_count_only_next_to_their_kind_and_only_when_minted(tmp_path):
    ids = jargon.minted(FIXTURES / "ai_tooling" / "round")
    assert ids["scenario"] == {"c3", "x9"} and ids["rule"] == {"e4", "x2"}
    assert ids["claim"] == {"c3", "a1"} and ids["setting"] == {"s2"}
    assert ids["finding"] == {"S1", "s1"} and "test_rule_e4.py" in ids["files"]
    pats = jargon.patterns(ids)
    for line in (
        "see scenario x9",
        "fixed in rule e4",
        "claim a1 is open",
        "setting s2",
        "finding S1 (low)",
        "the file test_rule_e4.py",
        "test_x9.py fails",
    ):
        assert jargon.scan_text(line, pats), line
    for line in (
        "scenario x12",
        "claim c17 passed",
        "item s9",
        "x9 on its own",
        "No state item s9; run `recall state` to list ids.",
        "rule D1 applies",
        "the oracle and the verifier agreed",
    ):
        assert not jargon.scan_text(line, pats), line


def test_factory_own_words_are_found_as_words():
    pats = jargon.patterns({})
    for line in (
        "Built by brb",
        "see REPORT.md",
        "from _verify.driver import x",
        "the scenarios in acceptance/findings",
        "the oracle panel voted",
        "files under /work/product",
        "marked xfail at delivery",
    ):
        assert jargon.scan_text(line, pats), line
    for line in (
        "Use the application factory pattern",
        "a factory reset wipes the data",
        "The signature verifier rejects forged requests",
        "brbx",
        "run `pytest`",
        "python -m pytest tests/acceptance",
    ):
        assert not jargon.scan_text(line, pats), line


def test_only_the_products_prose_is_read(tmp_path):
    root, rnd = tree(tmp_path)
    (root / "tests").mkdir()
    (root / "tests" / "test_x.py").write_text("# scenario x9 from brb's _verify\n")
    (root / "notes.txt").write_text("brb REPORT.md\n")
    assert jargon.scan_round(rnd, root) == []
    assert [p.name for p in jargon.product_files(root)] == ["README.md", "concepts.md"]


def test_repo_recalls_five_lines_are_not_a_hold(tmp_path):
    """The lines that held Repo Recall's publish (2026-10-11): its own ids, in repo-factory's own
    suite files. Those files are not in a tree any more; in the README they are prose."""
    rnd = tmp_path / "round"
    rnd.mkdir()
    (rnd / "findings.json").write_text(
        json.dumps({"scenarios": [{"id": "x1", "rule": "e1"}], "settings": [{"id": "s1"}]})
    )
    root = tmp_path / "p"
    root.mkdir()
    (root / "README.md").write_text(
        "No state item s9; run `recall state` to list ids.\n"
        "Drop an item: `recall drop s9` (rule D1 of your own list applies).\n"
    )
    assert jargon.scan_round(rnd, root) == []


def test_the_notice_is_factory_fixed_text_byte_for_byte():
    idea = {
        "card": {
            "evidence": [
                {
                    "url": "https://example.org/clinics",
                    "source": "hn",
                    "observed_at": "2026-10-01T09:00",
                },
                {
                    "url": "https://example.org/forum",
                    "source": "reddit",
                    "observed_at": "2026-09-30T10:00",
                },
            ]
        },
        "pitch": {"evidence_used": ["E1"]},
        "urls": [],
    }
    text = publish.notice(
        "Clinicbook", idea, 2026, "Test Owner", asset=publish.checks_asset("clinicbook")
    )
    assert text == NOTICE.read_text()
    # without the asset (exam.evidence_asset: false) it says where the checks are instead
    bare = publish.notice("Clinicbook", idea, 2026, "Test Owner")
    assert "kept with the build that made it" in bare and "checks.tar.gz" not in bare
