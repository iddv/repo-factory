"""Builds that don't stop for the wrong reasons (track X, 2026-10-09).

1. Stage caps are settings, none by default: no cap means no ``--max-budget-usd``; a set cap
   passes the flag; repair windows still work (test_v5_pipeline's round-2 test, with caps).
2. The Claude login: a session that ends on a login failure is retried once with a fresh
   copy; a second failure fails the stage with "Claude login expired". The copied login is
   mirrored while a session runs. A token in the environment is used without a copy.
3. A build stage with nothing valid fails the build (not delivered); a stop on turns, time or
   a cap that left a tree that builds and whose tests run is a partial build that goes on.
4. Retry keeps the work: the round resumes at the build stage and continues its tree; a new
   round after a stopped build continues that build's tree and scope.

Offline: no model, no network (the sessions are scripted)."""

import dataclasses
import json
import secrets
from pathlib import Path

import pytest

from repo_factory import models
from repo_factory.events import read_events
from repo_factory.harness import claude_code, profile
from repo_factory.harness.base import HarnessResult
from repo_factory.settings.store import Config
from repo_factory.v5 import pipeline
from repo_factory.v5.common import STAGE_CAPS_ENV, Budget, stage_caps, usd_or_none

from .test_v5_pipeline import BUGGY, Scripted, llm, task  # noqa: F401  (fixture)
from .test_worker_profiles import box  # noqa: F401  (fixture)

POLICY = Path(__file__).resolve().parents[1] / "policies" / "v5"
BUILD_MARK = "Work in the current directory."


# -- 1. caps ----------------------------------------------------------------------------------


def test_no_cap_means_no_budget_flag_and_a_set_cap_passes_it(box):  # noqa: F811
    home, ws = box
    cmd = profile.command(ws, config_dir=home / "cfg", max_turns=250, max_usd=None)
    assert "--max-budget-usd" not in cmd and cmd[cmd.index("--max-turns") + 1] == "250"
    cmd = profile.command(ws, config_dir=home / "cfg", max_turns=250, max_usd=6.0)
    assert cmd[cmd.index("--max-budget-usd") + 1] == "6.00"
    # the budget: nothing caps a session unless something is set
    b = Budget(None)
    assert b.stage_caps == {} and b.session_cap("build", None) is None
    assert Budget(None, stage_caps={"build": 6.0}).session_cap("build", None) == 6.0
    assert Budget(10.0).session_cap("build", None) == 10.0  # the global cap
    assert Budget(None).session_cap("build", 3.0) == 3.0  # a session max_usd


def test_stage_caps_come_from_the_policy_and_the_loop():
    rules = pipeline.load_rules(POLICY)
    assert stage_caps(rules) == {}  # none by default
    rules["build"]["stage_caps"] = {"build": 6, "verify": "2.5"}
    assert stage_caps(rules) == {"build": 6.0, "verify": 2.5}
    # the loop's map goes over the policy's; null there removes a policy cap
    assert stage_caps(rules, json.dumps({"verify": None, "repair": 4})) == {
        "build": 6.0,
        "repair": 4.0,
    }
    with pytest.raises(ValueError, match="unknown stage"):
        stage_caps(rules, {"bogus": 1})
    with pytest.raises(ValueError, match="dollars"):
        stage_caps(rules, {"build": "lots"})
    assert usd_or_none("none") is None and usd_or_none(0) is None and usd_or_none("$3") == 3.0


def test_the_loop_passes_its_stage_caps_to_the_build(tmp_path, monkeypatch):
    home = tmp_path / "loop"
    home.mkdir()
    (home / "loop.yaml").write_text("build:\n  stage_caps:\n    build: 6.0\n")
    monkeypatch.delenv(models.V5_MODELS_ENV, raising=False)
    env = models.build_env(Config(home))
    assert json.loads(env[STAGE_CAPS_ENV]) == {"build": 6.0}
    (home / "loop.yaml").write_text("concurrency: 1\n")
    assert models.build_env(Config(home)) is None  # nothing set: inherit


def test_a_set_stage_cap_reaches_the_session(task, tmp_path, monkeypatch):  # noqa: F811
    monkeypatch.setenv(STAGE_CAPS_ENV, json.dumps({"build": 6.0, "verify": 2.5}))
    h = Scripted()
    out = run(task, tmp_path, h)
    o = dict(h.runs)
    assert o["build"]["max_usd"] == 6.0 and o["verify"]["max_usd"] == 2.5
    assert o["repair"]["max_usd"] is None  # no cap on that stage
    assert out["failed"] is None


def test_the_spend_api_lists_every_cap(tmp_path, monkeypatch):
    from repo_factory.ui import api

    home = tmp_path / "loop"
    home.mkdir()
    (home / "loop.yaml").write_text("concurrency: 1\n")
    (tmp_path / "tasks").mkdir()
    monkeypatch.delenv(STAGE_CAPS_ENV, raising=False)
    app = api.App(tmp_path / "tasks", "fake", home)

    def call(method, path, body=None):
        code, ctype, data = api.handle(
            method, path, None, json.dumps(body).encode() if body is not None else None, app=app
        )
        return code, json.loads(data)

    code, caps = call("GET", "/api/spend/caps")
    # a stranger's defaults: $50 a day, $25 a build
    assert code == 200 and caps["uncapped"] is False
    assert caps["daily_usd"] == 50.0 and caps["build_max_usd"] == 25.0
    assert [s["stage"] for s in caps["stage_caps"]][:2] == ["scope", "build"]
    assert all(s["usd"] is None for s in caps["stage_caps"])
    build = next(s for s in caps["sessions"] if s["session"] == "build")
    assert build == {"session": "build", "max_usd": None, "max_turns": 250, "timeout_s": 7200}
    code, out = call("POST", "/api/spend", {"stage_caps": {"build": 6, "verify": 2.5}})
    assert code == 200
    got = {s["stage"]: (s["usd"], s["set_in"]) for s in out["caps"]["stage_caps"]}
    assert got["build"] == (6.0, "loop.yaml") and got["claims"] == (None, None)
    assert out["caps"]["uncapped"] is False
    code, out = call("POST", "/api/spend", {"stage_caps": {"build": None}})
    assert {s["stage"]: s["usd"] for s in out["caps"]["stage_caps"]}["build"] is None
    assert call("POST", "/api/spend", {"stage_caps": {"nope": 1}})[0] == 400
    assert call("POST", "/api/spend", {"stage_caps": {"build": -1}})[0] == 400
    assert "caps" in app.spend_breakdown()


# -- the scripted build sessions ------------------------------------------------------------------


class Builds(Scripted):
    """``Scripted``, with the build sessions following ``plan``: one item per build session,
    each ``(exit_reason, writes)``: ``writes`` True writes the (buggy) product as the fake
    builder does; ``auth`` ends on a login failure."""

    def __init__(self, plan, **kw):
        super().__init__(**kw)
        self.plan = list(plan)
        self.build_prompts = []

    def run(self, prompt, workspace, timeout_s, **opts):
        if BUILD_MARK not in prompt:
            return super().run(prompt, workspace, timeout_s, **opts)
        ws = Path(workspace)
        self.build_prompts.append(prompt)
        self.runs.append(("build", opts))
        reason, writes = self.plan.pop(0) if self.plan else ("ok", True)
        if writes:
            (ws / "meta.json").write_text(json.dumps({"problem": "repo"}))
            res = self.fake.run(prompt, ws, timeout_s, **opts)
            (ws / "meta.json").unlink()
        else:
            res = self.result(ws, opts.get("session"), 0.2)
        if reason == "ok":
            return res
        return dataclasses.replace(
            res, exit_ok=False, exit_reason=reason, auth_failed=reason == "auth"
        )


def run(task, tmp_path, harness, **kw):  # noqa: F811
    sol = tmp_path / "solution" / "textstats"
    if not sol.exists():
        sol.mkdir(parents=True)
        (sol / "__init__.py").write_text("")
        (sol / "core.py").write_text(BUGGY)
    harness.fake.solution = sol.parent
    return pipeline.build(
        task,
        den=tmp_path / "den",
        out=tmp_path / "out",
        harness=harness,
        llm=llm(),
        policy=POLICY,
        **kw,
    )


def finished(out) -> dict:
    return [e for e in read_events(Path(out["events"])) if e["type"] == "build_finished"][-1]


# -- 3. a failed build stage stops the build ----------------------------------------------------


def test_a_build_session_error_fails_the_build_and_nothing_is_delivered(task, tmp_path):  # noqa: F811
    h = Builds([("error", False)])
    out = run(task, tmp_path, h)
    assert [r[0] for r in h.runs] == ["build"]  # claims .. deliver: not run
    e = finished(out)
    assert e["status"] == "error" and e["stage"] == "build" and e["failure"] == "error"
    assert e["error"].startswith("build stage: the build session ended with an error")
    assert out["failed"]["stage"] == "build" and out["finished"]
    assert not (tmp_path / "out" / "REPORT.md").exists()  # not delivered
    st = json.loads((Path(out["den"]) / "v5" / "r1" / "state.json").read_text())
    assert "build" not in st["done"] and "deliver" not in st["done"]
    stage = [
        x
        for x in read_events(Path(out["events"]))
        if x["type"] == "v5_stage" and x["stage"] == "build" and x["status"] != "started"
    ]
    assert stage[-1]["status"] == "failed"


def test_a_stop_with_nothing_written_fails_the_build(task, tmp_path):  # noqa: F811
    h = Builds([("max_turns", False)])
    out = run(task, tmp_path, h)
    e = finished(out)
    assert e["status"] == "error" and e["failure"] == "stopped"
    assert "ran out of turns (max_turns) before it wrote any of the product" in e["error"]


def test_a_stop_that_left_a_tree_that_builds_and_runs_its_tests_is_a_partial_build(task, tmp_path):  # noqa: F811
    h = Builds([("max_turns", True)])
    out = run(task, tmp_path, h)
    # verify, repair and the rest ran on the stopped tree, and it was delivered
    assert [r[0] for r in h.runs][:3] == ["build", "verify", "repair"]
    assert finished(out)["status"] == "ok" and out["failed"] is None
    assert out["partial"]["exit_reason"] == "max_turns"
    assert "tests pass" in out["partial"]["why"]
    rep = (tmp_path / "out" / "REPORT.md").read_text()
    assert "**A partial build:** the build session ran out of turns" in rep
    assert [e for e in read_events(Path(out["events"])) if e["type"] == "build_partial"]


def test_a_stopped_tree_whose_tests_dont_run_fails_the_build(task, tmp_path, monkeypatch):  # noqa: F811
    monkeypatch.setattr(
        pipeline.Run,
        "tree_check",
        lambda self: {"ok": False, "why": "tests don't run (pytest exit 2)"},
    )
    h = Builds([("timeout", True)])
    out = run(task, tmp_path, h)
    e = finished(out)
    assert e["status"] == "error" and e["failure"] == "stopped"
    assert "ran out of time (timeout_s) and its tree tests don't run" in e["error"]


# -- 4. Retry keeps the work --------------------------------------------------------------------


def test_a_resumed_round_continues_the_failed_build_stage_on_its_tree(task, tmp_path):  # noqa: F811
    h = Builds([("error", True)])
    out = run(task, tmp_path, h)
    assert finished(out)["status"] == "error"
    ws = Path(out["den"]) / "v5" / "r1" / "build"
    assert "[:n + 1]" in (ws / "textstats" / "core.py").read_text()  # the work, committed
    # Retry: the round resumes at the build stage, and the session continues the tree
    h2 = Builds([("ok", False)])
    out2 = run(task, tmp_path, h2, resume=1)
    assert "Continue the build already in this directory" in h2.build_prompts[0]
    assert "textstats/core.py" in h2.build_prompts[0]
    assert finished(out2)["status"] == "ok" and out2["round"] == 1
    assert [r[0] for r in h2.runs][:2] == ["build", "verify"]
    # the attempts are two sessions with their own purposes (a crash finds the right one)
    t = Path(out["den"]) / "v5" / "r1" / "transcripts"
    assert (t / "build.transcript.jsonl").exists() and (t / "build2.transcript.jsonl").exists()


def test_a_new_round_after_a_stopped_build_continues_its_tree_and_scope(task, tmp_path):  # noqa: F811
    h = Builds([("error", True)])
    out = run(task, tmp_path, h)
    assert finished(out)["status"] == "error"
    # an old-style round 2: stopped (cut off) with nothing written, delivered anyway
    r2 = Path(out["den"]) / "v5" / "r2"
    (r2 / "build").mkdir(parents=True)
    (r2 / "build" / "SCOPE.md").write_text("# Scope: x\n")
    pipeline.repair.ensure_repo(r2 / "build", "v5: the base build")
    (r2 / "state.json").write_text(
        json.dumps(
            {
                "cut_off": True,
                "done": ["scope", "build", "claims", "verify", "deliver"],
                "out": str(tmp_path / "out"),
            }
        )
    )
    assert pipeline.carry_from(Path(out["den"]), 3) == Path(out["den"]) / "v5" / "r1"
    # a fresh build (the loop's Retry of a finished round): round 3 continues round 1's work
    from repo_factory.den.store import Den

    Den(Path(out["den"])).new_round("v5.0", ["textstats"], "build")  # r2's record
    h3 = Builds([("ok", False)])
    out3 = run(task, tmp_path, h3)
    assert out3["round"] == 3
    assert "Continue the build already in this directory" in h3.build_prompts[0]
    ws = Path(out3["den"]) / "v5" / "r3" / "build"
    assert (
        "[:n + 1]" in (ws / "textstats" / "core.py").read_text()
        or "[:n]" in (ws / "textstats" / "core.py").read_text()
    )
    started = [e for e in read_events(Path(out3["events"])) if e["type"] == "build_started"]
    assert started[-1]["continues"] == "r1"
    assert finished(out3)["status"] == "ok"
    assert "continued round 1's stopped build" in (tmp_path / "out" / "REPORT.md").read_text()
    # --fresh: nothing carried
    h4 = Builds([("ok", True)])
    out4 = run(task, tmp_path, h4, carry=False)
    assert "Continue the build" not in h4.build_prompts[0] and out4["round"] == 4


def test_a_finished_build_is_not_carried(tmp_path):
    den = tmp_path / "den"
    r1 = den / "v5" / "r1"
    (r1 / "build" / ".git").mkdir(parents=True)
    (r1 / "build" / "app.py").write_text("x = 1\n")
    (r1 / "state.json").write_text(json.dumps({"cut_off": False, "done": ["scope", "build"]}))
    assert pipeline.carry_from(den, 2) is None
    (r1 / "state.json").write_text(json.dumps({"cut_off": True, "done": ["scope", "build"]}))
    assert pipeline.carry_from(den, 2) == r1


# -- 2. the Claude login --------------------------------------------------------------------------


def test_a_login_failure_in_the_build_fails_it_with_the_reason(task, tmp_path):  # noqa: F811
    h = Builds([("auth", True)])
    out = run(task, tmp_path, h)
    e = finished(out)
    assert e["status"] == "error" and e["failure"] == "auth" and e["stage"] == "build"
    assert (
        "Claude login expired: run `claude` once on this machine, or set up a long-lived "
        "token" in e["error"]
    )
    # Retry resumes the round: the auth-failed session is not taken as done
    h2 = Builds([("ok", False)])
    out2 = run(task, tmp_path, h2, resume=1)
    assert finished(out2)["status"] == "ok" and h2.build_prompts


def test_a_login_failure_in_a_later_stage_fails_that_stage(task, tmp_path):  # noqa: F811
    class VerifyAuth(Builds):
        def run(self, prompt, workspace, timeout_s, **opts):
            res = super().run(prompt, workspace, timeout_s, **opts)
            if "You are verifying" in prompt and not getattr(self, "once", False):
                self.once = True
                return dataclasses.replace(res, exit_ok=False, exit_reason="auth", auth_failed=True)
            return res

    out = run(task, tmp_path, VerifyAuth([]))
    e = finished(out)
    assert e["stage"] == "verify" and e["failure"] == "auth"
    again = VerifyAuth([])
    again.once = True  # logged in again: verify runs
    out2 = run(task, tmp_path, again, resume=1)
    assert finished(out2)["status"] == "ok" and [r[0] for r in again.runs][0] == "verify"


def creds(tag: str, expires: float = 0) -> bytes:
    """A credentials file whose values are made here (no secret-shaped literal)."""
    oauth = {k + "Token": "-".join((tag, secrets.token_hex(4))) for k in ("access", "refresh")}
    return json.dumps({"claudeAiOauth": {**oauth, "expiresAt": expires}}).encode()


class Stand:
    """A stand-in harness for ``profile.launch``: ``plan`` items are ``"auth"`` or ``"ok"``;
    it records the session and the credentials it saw."""

    name = "stand"

    def __init__(self, plan, on_run=None):
        self.plan, self.seen, self.on_run = list(plan), [], on_run

    def run(self, prompt, workspace, timeout_s, **opts):
        cfg = Path(opts["config_dir"])
        c = cfg / profile.CREDENTIALS
        self.seen.append(
            {
                "prompt": prompt,
                "session": opts["session"],
                "creds": c.read_bytes() if c.is_file() else None,
            }
        )
        if self.on_run:
            self.on_run(len(self.seen))
        what = self.plan.pop(0)
        t = Path(workspace) / "transcript.jsonl"
        t.write_text(
            json.dumps(
                {
                    "type": "system",
                    "subtype": "init",
                    "n": len(self.seen),
                    "tools": list(profile.CORE_TOOLS),
                    "permissionMode": "dontAsk",
                }
            )
            + "\n"
        )
        return HarnessResult(
            Path(workspace),
            3,
            100,
            10,
            0.25,
            t,
            what == "ok",
            1.0,
            exit_reason="ok" if what == "ok" else "auth",
            session_id=opts["session"]["id"],
            session_cost_usd=0.25,
            num_turns=2,
            auth_failed=what == "auth",
        )


def test_launch_retries_once_after_copying_the_login_again(box, monkeypatch):  # noqa: F811
    home, ws = box
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    src = home / ".claude"
    first, newer = creds("old"), creds("new")
    (src / profile.CREDENTIALS).write_bytes(first)

    def refreshed_elsewhere(n):  # another process on the machine refreshes it
        if n == 1:
            (src / profile.CREDENTIALS).write_bytes(newer)

    h = Stand(["auth", "ok"], on_run=refreshed_elsewhere)
    r = profile.launch(
        "build it",
        ws,
        config_dir=home / "cfg",
        max_turns=5,
        max_usd=None,
        timeout_s=60,
        harness=h,
        source=src,
    )
    assert len(h.seen) == 2
    assert h.seen[0]["creds"] == first and h.seen[1]["creds"] == newer  # copied again
    # the retry resumes the session it cut off, and is told to carry on
    assert h.seen[1]["session"]["resume"] == h.seen[0]["session"]["id"]
    assert h.seen[1]["prompt"] == profile.AUTH_CONTINUE
    assert r.exit_reason == "ok" and not r.auth_failed
    assert r.cost_usd == pytest.approx(0.5) and r.tool_calls == 6 and r.num_turns == 4
    # both runs are in the transcript; the copied login is gone after the run
    assert len(Path(r.transcript_path).read_text().splitlines()) == 2
    assert not (home / "cfg" / profile.CREDENTIALS).exists()
    assert (src / profile.CREDENTIALS).read_bytes() == newer


def test_launch_gives_up_after_one_retry(box, monkeypatch):  # noqa: F811
    home, ws = box
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    (home / ".claude" / profile.CREDENTIALS).write_bytes(creds("x"))
    h = Stand(["auth", "auth"])
    r = profile.launch(
        "build it",
        ws,
        config_dir=home / "cfg",
        max_turns=5,
        max_usd=None,
        timeout_s=60,
        harness=h,
        source=home / ".claude",
    )
    assert len(h.seen) == 2 and r.auth_failed and r.exit_reason == "auth"


def test_a_token_in_the_environment_is_used_without_a_copy(box, monkeypatch):  # noqa: F811
    home, ws = box
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "t-" + secrets.token_hex(6))
    (home / ".claude" / profile.CREDENTIALS).write_bytes(creds("file"))
    (home / "cfg").mkdir()
    (home / "cfg" / profile.CREDENTIALS).write_bytes(creds("stale"))  # an old copy
    h = Stand(["ok"])
    assert profile.auth_source() == "env"
    profile.launch(
        "x",
        ws,
        config_dir=home / "cfg",
        max_turns=5,
        max_usd=None,
        timeout_s=60,
        harness=h,
        source=home / ".claude",
    )
    assert h.seen[0]["creds"] is None  # no copy: claude gets it by pipe
    from repo_factory.harness import sandbox

    assert "CLAUDE_CODE_OAUTH_TOKEN" not in sandbox.process_env(home / "cfg")
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN")
    assert profile.auth_source() == "file"


def test_the_mirror_copies_the_owners_login_in_and_never_writes_it_back(tmp_path):
    own, cfg = tmp_path / "own", tmp_path / "cfg"
    own.mkdir()
    cfg.mkdir()
    a = creds("a", 1000)
    (own / profile.CREDENTIALS).write_bytes(a)
    profile.clean_config(cfg, own)
    m = profile.Mirror(cfg, own, a)
    assert m.step() is None
    b = creds("b", 2000)  # another process refreshed the owner's
    (own / profile.CREDENTIALS).write_bytes(b)
    assert m.step() == "to_session" and (cfg / profile.CREDENTIALS).read_bytes() == b
    # what a sandboxed session writes into its copy (a refresh, or anything) stays there,
    # whatever its expiry says
    for planted in (creds("c", 3000), creds("ATTACKER", 9_999_999), b"not json"):
        (cfg / profile.CREDENTIALS).write_bytes(planted)
        assert m.step() is None and (own / profile.CREDENTIALS).read_bytes() == b
    d = creds("d", 5000)  # the owner's next refresh still goes in
    (own / profile.CREDENTIALS).write_bytes(d)
    assert m.step() == "to_session" and (cfg / profile.CREDENTIALS).read_bytes() == d
    assert oct((cfg / profile.CREDENTIALS).stat().st_mode & 0o777) == "0o600"


def test_after_a_run_the_sessions_copy_is_removed_and_nothing_written_back(tmp_path):
    own, cfg = tmp_path / "own", tmp_path / "cfg"
    own.mkdir()
    cfg.mkdir()
    a = creds("a", 1000)
    (own / profile.CREDENTIALS).write_bytes(a)
    profile.clean_config(cfg, own)
    (cfg / profile.CREDENTIALS).write_bytes(creds("ATTACKER", 9_999_999))
    profile._sync_credentials(cfg, own, a)
    assert not (cfg / profile.CREDENTIALS).exists()
    assert (own / profile.CREDENTIALS).read_bytes() == a


def test_the_stream_parser_spots_a_login_failure(tmp_path):
    t = tmp_path / "t.jsonl"
    t.write_text(
        "\n".join(
            json.dumps(e)
            for e in [
                {"type": "system", "subtype": "init", "session_id": "s"},
                {
                    "type": "system",
                    "subtype": "api_retry",
                    "error_status": 401,
                    "error": "authentication_failed",
                },
                {
                    "type": "assistant",
                    "message": {
                        "model": "<synthetic>",
                        "content": [
                            {
                                "type": "text",
                                "text": "Failed to authenticate: OAuth session expired and "
                                "could not be refreshed",
                            }
                        ],
                    },
                },
                {
                    "type": "result",
                    "subtype": "success",
                    "is_error": True,
                    "total_cost_usd": 0.34,
                    "usage": {},
                },
            ]
        )
        + "\n"
    )
    assert claude_code.parse_stream(t)["auth_error"] is True
    ok = tmp_path / "ok.jsonl"
    ok.write_text(json.dumps({"type": "result", "subtype": "success", "usage": {}}) + "\n")
    assert claude_code.parse_stream(ok)["auth_error"] is False
    assert claude_code.AUTH_ERROR.search("Invalid API key · Please run /login")
