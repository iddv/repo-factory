"""The conflict protocol on a fixture cut from run 10's events (docs/design/build-v5.md §3.5,
"Run 10 replayed under this protocol").

Run 10 (``tests/fixtures/v5_run10/``, cut from ``runs/010/task/.den`` by
``scratch/cut_run10.py``): hardening round 1 repaired the two failing rules q25 and q27; both
passed afterwards, but one acceptance test the factory generated from its own assumptions
broke, and build v4 rejected the whole repair, q27's fix included. Under v5, with one commit
per finding: the broken test is traced to q25's commit, the pair (q25, test) goes to the
panel, q25's commit alone is reverted and q27's fix is kept."""

import json
import re
import subprocess
from pathlib import Path
from types import SimpleNamespace

from repo_factory.v5 import accept, repair

FIX = Path(__file__).parent / "fixtures" / "v5_run10"
GIT = repair.GIT


def run10():
    evs = [json.loads(ln) for ln in (FIX / "events.jsonl").read_text().splitlines()]
    rounds = [e for e in evs if e["type"] == "harden_round"]
    done = next(e for e in rounds if e["status"] == "done" and e["round"] == 1)
    broken = re.search(r"passed before: (\S+)", done["why"]).group(1)
    rules = {r["id"]: r for r in json.loads((FIX / "rules.json").read_text())}
    return done, broken, rules


def git(ws, *a):
    subprocess.run([*GIT, *a], cwd=ws, check=True, capture_output=True)


class Ctx:
    """``repair.run_round``'s context over a toy repo whose behaviour is files: a fix for
    finding X adds ``X.fix``; the generated acceptance test passes while ``q25.fix`` is
    absent (or once the builder updated it, ``test.updated``)."""

    def __init__(
        self,
        ws,
        scratch,
        broken,
        rules,
        verdict="compatible",
        reading=None,
        y_user=False,
        fixup_updates_test=False,
        setting=None,
    ):
        self.ws, self.scratch, self.broken, self.rules = ws, scratch, broken, rules
        self.verdict, self.reading, self.y_user = verdict, reading, y_user
        self.setting, self.settings_made = setting, []
        self.fixup_updates_test = fixup_updates_test
        self.fixup_usd = 1.0
        self.pairs, self.sessions, self.events, self.retired, self.asked = [], [], [], [], []

    def measure(self, d):
        d = Path(d)
        test_ok = not (d / "q25.fix").exists() or (d / "test.updated").exists()
        t = {self.broken: "passed" if test_ok else "failed", "tests/test_own.py::test_ok": "passed"}
        user = {}
        if self.y_user:
            user, t = {"tests/test_existing.py::test_fee": t.pop(self.broken)}, t
        return {
            "valid": all(v == "passed" for v in user.values()),
            "scenarios": {
                i: "passed" if (d / f"{i}.fix").exists() else "failed" for i in ("q25", "q27")
            },
            "held": {},
            "tests": t,
            "user": user,
        }

    def session(self, prompt, purpose, stage):
        self.sessions.append(purpose)
        if purpose.startswith("repair"):
            for fid in ("q25", "q27"):  # one commit per finding
                (self.ws / f"{fid}.fix").write_text("fixed\n")
                git(self.ws, "add", "-A")
                git(self.ws, "commit", "-q", "-m", f"fix({fid}): {self.rules[fid]['title']}")
        elif self.fixup_updates_test:
            (self.ws / "test.updated").write_text("updated to the finding's expectation\n")
            git(self.ws, "add", "-A")
            git(self.ws, "commit", "-q", "-m", "fixup(q25): update the stale fee test")
        return SimpleNamespace(cost_usd=0.1)

    def brief(self, defects):
        return "repair " + ",".join(d["id"] for d in defects)

    def fixup_brief(self, pairs):
        return repair.pairs_md(pairs)

    def export(self, ids):
        return ids

    def item(self, i):
        r = self.rules[i]
        return {
            "kind": "scenario",
            "basis": "spec_explicit",
            "required": 3,
            "votes": 3,
            "source_ref": r["source_ref"],
            "situation": r["title"],
            "expect": r["expect"],
        }

    def reg_item(self, reg):
        if reg["group"] == "user":
            return {"kind": "user_test", "prose": reg["id"]}
        # The run 10 test was generated from the factory's own assumptions: rank 5.
        return {"kind": "builder_test", "source_ref": "answer:assumptions", "prose": reg["id"]}

    def pair(self, x, y):
        self.pairs.append((x, y))
        return {"verdict": self.verdict, "votes": [self.verdict] * 3, "setting": self.setting}

    def pair_setting(self, xs, reg, setting):
        self.settings_made.append((xs, reg["id"], setting["key"]))
        return "s1"

    def interpret(self, items):
        self.asked.append(items)
        return {
            k: {"reading": self.reading, "votes": [self.reading or "either"] * 3} for k in items
        }

    def retire(self, i, why):
        self.retired.append(i)

    def promote(self, i):
        pass

    def emit(self, type, **f):
        self.events.append({"type": type, **f})

    def allows(self, usd):
        return True


def setup(tmp_path, **kw):
    done, broken, rules = run10()
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "app.py").write_text("FEES = 'per started day'\n")
    repair.ensure_repo(ws, "the base build")
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    return Ctx(ws, scratch, broken, rules, **kw), done


def defects():
    return [{"id": "q25", "class": "defect"}, {"id": "q27", "class": "defect"}]


def test_the_fixture_is_run_10():
    done, broken, rules = run10()
    assert done["design_rules"]["failing"] == ["q25", "q27"]
    assert done["accepted"] is False and done["after"]["rules"] == [32, 32]  # v4 lost q27
    assert broken.endswith("test_R15_R16_overdue_fee_accrues_per_started_day_and_caps_at_10_euros")
    assert {r["source_ref"] for r in rules.values()} == {"answer:assumptions"}
    # rank 5 against rank 5: both sides trace to the factory's own assumptions
    assert (
        accept.rank(
            {
                "kind": "scenario",
                "source_ref": rules["q25"]["source_ref"],
                "basis": "spec_explicit",
                "required": 3,
                "votes": 3,
            }
        )
        == 5
    )


def test_run10_pair_adjudication_reverts_q25_alone_and_keeps_q27(tmp_path):
    ctx, done = setup(tmp_path, verdict="compatible")
    r = repair.run_round(ctx, 1, defects())
    # The broken test is traced to q25's commit, and that pair alone went to the panel.
    assert len(ctx.pairs) == 1
    x, y = ctx.pairs[0]
    assert x["situation"] == ctx.rules["q25"]["title"] and y["kind"] == "builder_test"
    assert r["pairs"][0]["x"] == ["q25"] and r["pairs"][0]["y"]["id"] == ctx.broken
    assert (r["pairs"][0]["x_rank"], r["pairs"][0]["y_rank"]) == (5, 5)
    # compatible: one fix-up with both; the test still fails, so q25's commit is reverted
    assert ctx.sessions == ["repair1", "fixup1"]
    assert [v["findings"] for v in r["reverted"]] == [["q25"]]
    assert r["accepted"] and r["fixed"] == ["q27"]
    assert (ctx.ws / "q27.fix").exists() and not (ctx.ws / "q25.fix").exists()
    log = subprocess.run(
        [*GIT, "log", "--format=%s"], cwd=ctx.ws, capture_output=True, text=True
    ).stdout
    assert 'Revert "fix(q25)' in log and "fix(q27)" in log
    assert ctx.measure(ctx.ws)["tests"][ctx.broken] == "passed"
    assert [e for e in ctx.events if e["type"] == "conflict"][0]["action"] == "fixup"


def test_run10_undecidable_is_complex_and_the_build_keeps_its_reading_without_a_vote(tmp_path):
    # Addendum A: fee day-counting is complex: the implementation decides, nobody votes.
    ctx, _ = setup(tmp_path, verdict="undecidable", reading=None)
    r = repair.run_round(ctx, 1, defects())
    assert not ctx.asked and r["interpretations"][0]["kind"] == "complex"
    assert r["interpretations"][0]["how"].startswith("complex")
    assert r["pairs"][0]["action"] == "revert_x" and ctx.sessions == ["repair1"]
    assert r["accepted"] and r["fixed"] == ["q27"]


def test_an_undecidable_pair_that_is_a_binary_policy_becomes_a_setting(tmp_path):
    pol = {
        "key": "fees.count",
        "values": ["started_day", "full_day"],
        "default": "full_day",
        "scope": "global",
        "claim_value": "started_day",
    }
    ctx, _ = setup(tmp_path, verdict="undecidable", setting=pol)
    r = repair.run_round(ctx, 1, defects())
    p = r["pairs"][0]
    assert p["action"] == "setting" and ctx.settings_made == [(["q25"], ctx.broken, "fees.count")]
    # the default is the earlier behaviour: X's commit goes, nothing is retired, no vote
    assert [v["findings"] for v in r["reverted"]] == [["q25"]] and not ctx.retired
    assert not ctx.asked and r["interpretations"][0]["kind"] == "binary"
    assert r["accepted"] and r["fixed"] == ["q27"] and ctx.sessions == ["repair1"]


def test_y_wins_reverts_x_without_a_fixup(tmp_path):
    ctx, _ = setup(tmp_path, verdict="y_wins")
    r = repair.run_round(ctx, 1, defects())
    assert ctx.sessions == ["repair1"] and r["fixed"] == ["q27"] and r["accepted"]
    assert ctx.retired == ["q25"]  # X is retired, not only reverted


def test_a_broken_user_test_outranks_a_claim_whatever_the_panel_says(tmp_path):
    ctx, _ = setup(tmp_path, verdict="x_wins", y_user=True)
    r = repair.run_round(ctx, 1, defects())
    p = r["pairs"][0]
    assert (p["x_rank"], p["y_rank"], p["verdict"], p["action"]) == (5, 1, "x_wins", "revert_x")
    assert r["accepted"] and r["fixed"] == ["q27"]
    assert ctx.measure(ctx.ws)["valid"]


def test_a_repair_with_no_regression_is_accepted_whole(tmp_path):
    ctx, _ = setup(tmp_path)
    ctx.broken = "tests/test_other.py::test_unrelated"
    ctx.measure = lambda d: {
        "valid": True,
        "held": {},
        "user": {},
        "tests": {},
        "scenarios": {
            i: "passed" if (Path(d) / f"{i}.fix").exists() else "failed" for i in ("q25", "q27")
        },
    }
    r = repair.run_round(ctx, 1, defects())
    assert r["accepted"] and r["fixed"] == ["q25", "q27"] and not ctx.pairs


# -- the lending run (2026-10-04): a revert that conflicts must not undo the round ----------
# Round 1 fixed e20 and e22 and added settings s1-s3 (in the run: 4 defects, 7 settings).
# s2 broke scenario e31 under its default. Each setting added a row to one README table and
# an entry to one settings module, side by side, so `git revert <s2>` conflicts; the
# pipeline then reset the round: every fix and every setting was lost.
README = (
    "# Lib\n\n## Configuration\n\n| key | env |\n|---|---|\n\n## Rules\n"
    "renewal: later of due date and now\n"
)
SETTINGS = "DEFAULTS = {\n}\n"
SERVICE = "def renew(due, now):\n    return max(due, now) + 21\n"
SKEYS = {"s1": "hold.window_start", "s2": "renewal.extend_from", "s3": "renewal.when_overdue"}


class LendingCtx(Ctx):
    """Behaviour is file content: e20/e22 pass once their module exists; e31 (an overdue
    renewal gives a full period from today) passes while ``renew`` keeps ``max(due, now)``;
    each setting's scenario passes once its key is in the settings module. ``e31_group``:
    where e31 lives (``scenarios``: a verifier scenario, rank 5; ``user``: rank 1)."""

    def __init__(
        self,
        ws,
        scratch,
        s3_touches_s2=False,
        undo_works=True,
        e31_group="scenarios",
        e20_breaks_suite=False,
    ):
        super().__init__(ws, scratch, "e31", {})
        self.s3_touches_s2, self.undo_works = s3_touches_s2, undo_works
        self.e31_group, self.e20_breaks_suite = e31_group, e20_breaks_suite
        self.shas, self.undo_briefs = {}, []

    def measure(self, d):
        d = Path(d)
        svc = (d / "library" / "service.py").read_text()
        st = (d / "library" / "settings.py").read_text()
        scen = {
            "e20": "passed" if (d / "library" / "validate.py").exists() else "failed",
            "e22": "passed" if (d / "library" / "search.py").exists() else "failed",
        }
        scen.update({f"x_{s}": "passed" if key in st else "failed" for s, key in SKEYS.items()})
        e31 = "passed" if "max(due, now)" in svc else "failed"
        user = {}
        if self.e31_group == "user":
            user["tests/test_user.py::test_e31"] = e31
        else:
            scen["e31"] = e31
        broken = (d / "broken.marker").exists()
        return {
            "valid": not broken and all(v == "passed" for v in user.values()),
            "scenarios": scen,
            "held": {},
            "tests": {},
            "user": user,
        }

    def commit(self, fid, msg):
        git(self.ws, "add", "-A")
        git(self.ws, "commit", "-q", "-m", f"fix({fid}): {msg}")
        self.shas[fid] = repair.head(self.ws)

    def add_setting(self, sid):
        key = SKEYS[sid]
        rd, st = self.ws / "README.md", self.ws / "library" / "settings.py"
        rd.write_text(
            rd.read_text().replace("\n\n## Rules", f"\n| {key} | {key.upper()} |\n\n## Rules")
        )
        st.write_text(st.read_text().replace("\n}\n", f"\n    '{key}': 'default',\n}}\n"))

    def session(self, prompt, purpose, stage):
        self.sessions.append(purpose)
        ws = self.ws
        svc = ws / "library" / "service.py"
        if purpose.startswith("repair"):
            (ws / "library" / "validate.py").write_text("def ok(x):\n    return x\n")
            if self.e20_breaks_suite:
                (ws / "broken.marker").write_text("the suite no longer runs\n")
            self.commit("e20", "validate identifiers")
            (ws / "library" / "search.py").write_text("def like(t):\n    return t\n")
            self.commit("e22", "literal search")
            self.add_setting("s1")
            self.commit("s1", "add hold.window_start")
            self.add_setting("s2")
            svc.write_text(
                svc.read_text().replace(
                    "    return max(due, now) + 21\n",
                    "    base = due  # renewal.extend_from\n    return base + 21\n",
                )
            )
            self.commit("s2", "add renewal.extend_from")
            self.add_setting("s3")
            if self.s3_touches_s2:  # s3 edits s2's own line: no hunk-level undo
                svc.write_text(
                    svc.read_text().replace(
                        "# renewal.extend_from", "# renewal.extend_from, when_overdue"
                    )
                )
            self.commit("s3", "add renewal.when_overdue")
        elif purpose.startswith("undo") and self.undo_works:
            # the repairer takes s2 out by hand: its row, its key, its code
            rd, st = ws / "README.md", ws / "library" / "settings.py"
            rd.write_text(
                rd.read_text().replace("| renewal.extend_from | RENEWAL.EXTEND_FROM |\n", "")
            )
            st.write_text(st.read_text().replace("    'renewal.extend_from': 'default',\n", ""))
            svc.write_text(
                re.sub(
                    r"    base = due .*\n    return base \+ 21\n",
                    "    return max(due, now) + 21\n",
                    svc.read_text(),
                )
            )
            git(ws, "add", "-A")
            git(ws, "commit", "-q", "-m", "revert(s2): take renewal.extend_from out")
        return SimpleNamespace(cost_usd=0.1)

    def undo_brief(self, commits):
        self.undo_briefs.append([c["findings"] for c in commits])
        return "undo " + ",".join(c["sha"][:10] for c in commits)

    def item(self, i):
        return {
            "kind": "scenario",
            "basis": "spec_explicit",
            "required": 3,
            "votes": 3,
            "situation": i,
            "expect": i,
        }

    def reg_item(self, reg):
        if reg["group"] == "user":
            return {"kind": "user_test", "prose": reg["id"]}
        return {"kind": "scenario", "prose": reg["id"]}  # never confirmed: rank 5


def lending(tmp_path, **kw):
    ws = tmp_path / "ws"
    (ws / "library").mkdir(parents=True)
    (ws / "README.md").write_text(README)
    (ws / "library" / "settings.py").write_text(SETTINGS)
    (ws / "library" / "service.py").write_text(SERVICE)
    repair.ensure_repo(ws, "v5: the base build")
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    return LendingCtx(ws, scratch, **kw)


def lending_send():
    return [{"id": "e20", "class": "defect"}, {"id": "e22", "class": "defect"}] + [
        {"id": s, "class": "setting", "key": key, "scenarios": [f"x_{s}"]}
        for s, key in SKEYS.items()
    ]


def files(ctx):
    return {
        p: (ctx.ws / p).read_text() if (ctx.ws / p).exists() else None
        for p in (
            "README.md",
            "library/settings.py",
            "library/service.py",
            "library/validate.py",
            "library/search.py",
        )
    }


def fixed_settings(r):
    return {s["id"]: s["fixed"] for s in r["settings"]}


def test_the_fixture_reproduces_the_conflicting_revert(tmp_path):
    ctx = lending(tmp_path)
    ctx.session("", "repair1", "repair")
    copy = repair.checkout_copy(ctx.ws, repair.head(ctx.ws), tmp_path / "copy")
    assert not repair.revert(copy, ctx.shas["s2"])  # what undid the lending round


def test_a_setting_that_breaks_a_check_is_dropped_alone_and_the_round_is_kept(tmp_path):
    ctx = lending(tmp_path)
    r = repair.run_round(ctx, 1, lending_send())
    assert r["accepted"], r["why"]
    # the setting's commit came out hunk by hunk; no panel, no fix-up for a setting
    assert [(v["sha"], v["findings"], v["how"]) for v in r["reverted"]] == [
        (ctx.shas["s2"], ["s2"], "hunks")
    ]
    assert ctx.sessions == ["repair1"] and not ctx.pairs
    assert r["pairs"][0]["action"] == "drop_setting" and r["pairs"][0]["y"]["id"] == "e31"
    assert {"e20", "e22", "x_s1", "x_s3"} <= set(r["fixed"])
    assert fixed_settings(r) == {"s1": True, "s2": False, "s3": True}
    f = files(ctx)
    assert "hold.window_start" in f["README.md"] and "renewal.when_overdue" in f["README.md"]
    assert "renewal.extend_from" not in f["README.md"] + f["library/settings.py"]
    assert f["library/service.py"] == SERVICE
    assert f["library/validate.py"] and f["library/search.py"]
    assert r["now"]["scenarios"]["e31"] == "passed" and not r["open_regressions"]


def test_a_later_commit_for_the_same_setting_goes_with_it(tmp_path):
    # The lending run's history: a `fixup(s2)` commit after s7 edited s2's own line, so
    # neither a revert nor a hunk-level undo of s2 alone applies; with its follow-up, both do.
    ctx = lending(tmp_path)
    real = ctx.session

    def with_followup(prompt, purpose, stage):
        res = real(prompt, purpose, stage)
        svc = ctx.ws / "library" / "service.py"
        svc.write_text(
            svc.read_text().replace(
                "    base = due  # renewal.extend_from\n",
                "    base = due  # renewal.extend_from (fixed)\n",
            )
        )
        git(ctx.ws, "add", "-A")
        git(ctx.ws, "commit", "-q", "-m", "fixup(s2): due_date renewal never leaves it overdue")
        ctx.shas["s2b"] = repair.head(ctx.ws)
        return res

    ctx.session = with_followup
    r = repair.run_round(ctx, 1, lending_send())
    assert r["accepted"] and ctx.sessions == ["repair1"]
    assert [(v["sha"], v["how"]) for v in r["reverted"]] == [
        (ctx.shas["s2b"], "revert"),
        (ctx.shas["s2"], "hunks"),
    ]
    assert files(ctx)["library/service.py"] == SERVICE
    assert fixed_settings(r) == {"s1": True, "s2": False, "s3": True}


def test_when_no_hunk_level_undo_works_a_narrow_session_takes_the_change_out(tmp_path):
    ctx = lending(tmp_path, s3_touches_s2=True)
    r = repair.run_round(ctx, 1, lending_send())
    assert ctx.sessions == ["repair1", "undo1"] and ctx.undo_briefs == [[["s2"]]]
    assert r["accepted"] and [v["how"] for v in r["reverted"]] == ["session"]
    assert fixed_settings(r) == {"s1": True, "s2": False, "s3": True}
    assert files(ctx)["library/validate.py"] and r["now"]["scenarios"]["e31"] == "passed"


def test_a_rank_5_check_is_given_up_when_the_change_cannot_come_out(tmp_path):
    ctx = lending(tmp_path, s3_touches_s2=True, undo_works=False)
    r = repair.run_round(ctx, 1, lending_send())
    assert r["accepted"] and r["tolerated"] == [{"y": "e31", "x": ["s2"], "commit": ctx.shas["s2"]}]
    assert ctx.retired == ["e31"] and {"e20", "e22"} <= set(r["fixed"])
    # s2 stays in the code but isn't counted as added: it changed the default behaviour
    assert fixed_settings(r) == {"s1": True, "s2": False, "s3": True}
    assert "kept with a regression: e31" in r["why"]


def test_a_higher_rank_check_rebuilds_the_head_without_the_change(tmp_path):
    # e31 is the user's own test (rank 1): the commit must go; s3 built on it and goes too,
    # everything else stays
    ctx = lending(tmp_path, s3_touches_s2=True, undo_works=False, e31_group="user")
    r = repair.run_round(ctx, 1, lending_send())
    assert r["accepted"], r["why"]
    assert [v["how"] for v in r["reverted"]] == ["rebuild"]
    assert [c["findings"] for c in r["dropped"]] == [["s3"]]
    assert fixed_settings(r) == {"s1": True, "s2": False, "s3": False}
    assert {"e20", "e22", "x_s1"} <= set(r["fixed"]) and r["now"]["valid"]
    assert files(ctx)["library/service.py"] == SERVICE


def test_settings_are_kept_when_the_defect_fixes_are_rejected(tmp_path):
    # e20's fix breaks the user's suite: the defect fixes go, the settings that work stay
    # (s2, which broke e31, is dropped on its own as before)
    ctx = lending(tmp_path, e20_breaks_suite=True)
    r = repair.run_round(ctx, 1, lending_send())
    assert r["accepted"] and r["kept"] == "settings", r["why"]
    f = files(ctx)
    assert f["library/validate.py"] is None and f["library/search.py"] is None
    assert not (ctx.ws / "broken.marker").exists() and f["library/service.py"] == SERVICE
    assert fixed_settings(r) == {"s1": True, "s2": False, "s3": True}
    assert "the defect fixes are undone and 2 setting(s) kept" in r["why"]
    assert {v["how"] for v in r["reverted"]} == {"settings only"}
    assert {x for v in r["reverted"] for x in v["findings"]} == {"e20", "e22", "s2"}
    assert r["now"]["valid"] and r["now"]["scenarios"]["e31"] == "passed"


def test_settings_alone_are_kept_when_the_defect_fixes_fix_nothing(tmp_path):
    ctx = lending(tmp_path, e20_breaks_suite=True)
    ctx.measure = (
        lambda m: (
            lambda d: {
                **m(d),
                "scenarios": {k: v for k, v in m(d)["scenarios"].items() if k != "e31"},
            }
        )
    )(ctx.measure)
    r = repair.run_round(ctx, 1, lending_send())  # no regression: a plain reject
    assert r["accepted"] and r["kept"] == "settings" and not r["pairs"]
    assert fixed_settings(r) == {"s1": True, "s2": True, "s3": True}
    assert r["why"].startswith("the user's tests fail after the repair; the defect fixes")


def test_a_later_round_that_is_undone_never_undoes_accepted_settings(tmp_path):
    ctx = lending(tmp_path)
    assert repair.run_round(ctx, 1, lending_send())["accepted"]
    head1 = repair.head(ctx.ws)

    def bad_fix(prompt, purpose, stage):  # round 2 breaks the suite
        ctx.sessions.append(purpose)
        (ctx.ws / "broken.marker").write_text("x\n")
        ctx.commit("e40", "a bad fix")
        return SimpleNamespace(cost_usd=0.1)

    ctx.session = bad_fix
    r2 = repair.run_round(ctx, 2, [{"id": "e40", "class": "defect"}])
    assert not r2["accepted"] and repair.head(ctx.ws) == head1
    f = files(ctx)
    assert "hold.window_start" in f["library/settings.py"]
    assert "renewal.when_overdue" in f["README.md"]
