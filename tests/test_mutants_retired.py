"""The mutants stage is retired (AU1 #4): its score decided nothing, and across 13 runs it cost
$3.55 and 229 minutes. What an old round recorded stays readable: a real round's log (FrontDesk
Book r1, the one round whose survivor round ran; trimmed, paths scrubbed) with its ``v5_stage
stage=mutants`` lines and its ``mutation`` event folds, shows and prints without the stage
coming back, and a loop.yaml or policy that still names the stage loads as if it did not.
Offline: no model, no build."""

import json
import shutil
from pathlib import Path

from repo_factory import events, models, progress
from repo_factory.resources import POLICIES
from repo_factory.settings.store import Config
from repo_factory.ui import runs, v5view
from repo_factory.v5 import common, pipeline

FIX = Path(__file__).parent / "fixtures" / "v5_mutants_round" / "events.jsonl"


def old_round(tmp_path) -> Path:
    d = tmp_path / "frontdesk-book"
    (d / ".den").mkdir(parents=True)
    (d / "spec.md").write_text("# FrontDesk Book\n")
    shutil.copy(FIX, d / ".den" / "events.jsonl")
    return d


def test_an_old_round_with_the_mutants_stage_still_folds_shows_and_prints(tmp_path):
    d = old_round(tmp_path)
    evs = events.read_events(d / ".den" / "events.jsonl")
    assert any(e["type"] == "mutation" for e in evs)  # the real record has it
    st = events.fold(evs, 1)
    v = st["v5"]
    assert "mutation" not in v  # nothing reads it now
    assert v["stages"]["mutants"]["usd"] == 0.5988  # the stage's own record stays
    # the build page's strip: the stage keeps its place (after verify) in the round that ran it
    page = v5view.view(d, st)
    names = [s["stage"] for s in page["stages"]]
    assert names[names.index("verify") + 1] == "mutants"
    assert names[-1] == "deliver" and "mutation" not in page
    assert page["cost"]["by_stage"]["mutants"] == 0.5988
    # one Build's timeline (Svelte) and `repo-factory status`
    b = runs.run(d, 1)
    tl = [s["stage"] for s in b["stages"]]
    assert tl[tl.index("verify") + 1] == "mutants" and tl[-1] == "deliver"
    lines = progress.v5_lines(v)
    assert lines and not any(ln.startswith("Mutants:") for ln in lines)


def test_a_new_round_has_no_mutants_stage():
    assert "mutants" not in common.STAGES and "mutants" not in v5view.STAGES
    assert "mutants" not in common.TYPICAL_USD and "mutants" not in pipeline.MODEL_SECTIONS
    assert not (POLICIES / "v5" / "prompts" / "mutants.md").exists()
    st = events.fold(
        [
            {
                "v": 1,
                "ts": 1.0,
                "task": "t",
                "build": 1,
                "type": "build_started",
                "pipeline": "v5",
                "stages": list(common.STAGES),
            }
        ],
        1,
    )
    assert [s["stage"] for s in v5view.stages(st["v5"], True, {})] == list(v5view.STAGES)


def test_a_loop_yaml_that_still_names_the_stage_loads_and_builds(tmp_path, monkeypatch):
    home = tmp_path / "loop"
    home.mkdir()
    (home / "loop.yaml").write_text(
        "build:\n  models:\n    mutants: claude-sonnet-5\n    verify: claude-sonnet-5-5\n"
        "  stage_caps:\n    mutants: 0.7\n    verify: 2.5\n"
    )
    cfg = Config(home)
    assert cfg.problems == []
    assert "mutants" not in {r["role"] for r in models.roles(cfg)}
    env = models.build_env(cfg)
    monkeypatch.setenv(models.V5_MODELS_ENV, env[models.V5_MODELS_ENV])
    rules = pipeline.load_rules(POLICIES / "v5")  # no "unknown role"
    assert rules["verify"]["model"] == "claude-sonnet-5-5" and "mutants" not in rules
    caps = common.stage_caps(rules, env[common.STAGE_CAPS_ENV])  # no "unknown stage"
    assert caps == {"verify": 2.5}
    assert json.loads(env[common.STAGE_CAPS_ENV])["mutants"] == 0.7  # the file is untouched
