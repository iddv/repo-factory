"""Test-run / build detection, test-result parsers, skip markers and test-file patterns."""
import fnmatch
import re
import shlex

# ---------------------------------------------------------------- commands

_EVAL_RE = re.compile(r"""\beval\s+('(?:[^']|'\\'')*'|"(?:[^"\\]|\\.)*")""")


def normalize_command(cmd):
    """Unwrap agent harness wrappers, e.g. Claude Code's `... eval '<cmd>' ... pwd -P >| /tmp/...`."""
    if not cmd:
        return cmd
    m = _EVAL_RE.search(cmd)
    if m and ("shell-snapshots" in cmd or "pwd -P" in cmd or "< /dev/null" in cmd):
        try:
            inner = shlex.split(m.group(1))
            if inner:
                return inner[0].strip()
        except ValueError:
            pass
    return cmd.strip()


def is_infra(cmd):
    """Commands the agent harness runs for itself (not the agent's own work)."""
    return bool(cmd) and (".claude/shell-snapshots" in cmd and "eval" not in cmd)


def squash(cmd):
    return " ".join((cmd or "").split())


def _segments(cmd):
    for seg in re.split(r"&&|\|\||;|\||\n", cmd or ""):
        try:
            toks = shlex.split(seg, comments=True)
        except ValueError:
            toks = seg.split()
        while toks and (re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", toks[0]) or toks[0] in ("env", "time", "sudo", "exec", "command", "nice")):
            toks = toks[1:]
        if toks and toks[0] == "timeout" and len(toks) > 2:
            toks = toks[2:]
        if toks:
            yield toks


def _prog(tok):
    return tok.rsplit("/", 1)[-1]


_RUNNER_PREFIX = {"npx", "pnpx", "bunx"}
_PKG = {"npm", "yarn", "pnpm", "bun"}


def detect_runner(cmd, test_cmd=None):
    """Return the runner name if ``cmd`` is a test run, else None."""
    for toks in _segments(cmd):
        t = list(toks)
        p = _prog(t[0])
        if p in ("uv", "poetry", "pipenv", "pdm", "hatch") and len(t) > 2 and t[1] == "run":
            t = t[2:]
            p = _prog(t[0])
        if p in _RUNNER_PREFIX and len(t) > 1:
            t = t[1:]
            p = _prog(t[0])
        if p == "bundle" and len(t) > 2 and t[1] == "exec":
            t = t[2:]
            p = _prog(t[0])
        if re.match(r"^python[0-9.]*$", p) and len(t) > 2 and t[1] == "-m":
            if t[2] in ("pytest", "py.test"):
                return "pytest"
            if t[2] == "unittest":
                return "unittest"
        if p in ("pytest", "py.test"):
            return "pytest"
        if p in ("jest", "vitest", "mocha", "rspec", "phpunit"):
            return p
        if p == "go" and len(t) > 1 and t[1] == "test":
            return "go"
        if p == "cargo" and len(t) > 1 and t[1] in ("test", "nextest"):
            return "cargo"
        if p in ("mvn", "mvnw") and any(x in ("test", "verify", "install", "package") for x in t[1:]):
            return "maven"
        if p in ("gradle", "gradlew") and any(x in ("test", "check", "build") or x.endswith(":test") for x in t[1:]):
            return "gradle"
        if p == "dotnet" and len(t) > 1 and t[1] == "test":
            return "dotnet"
        if p in _PKG and len(t) > 1 and (t[1] in ("test", "t") or (t[1] == "run" and len(t) > 2 and t[2].startswith("test"))):
            return "npm"
    if test_cmd and squash(test_cmd) in squash(cmd):
        return "custom"
    return None


_BUILD = [
    (r"^(npm|yarn|pnpm|bun)$", lambda t: len(t) > 1 and (t[1] == "build" or (t[1] == "run" and len(t) > 2 and t[2].startswith("build")))),
    (r"^make$", lambda t: not any(x in ("test", "check", "clean") for x in t[1:])),
    (r"^cargo$", lambda t: len(t) > 1 and t[1] in ("build", "check")),
    (r"^go$", lambda t: len(t) > 1 and t[1] in ("build", "vet")),
    (r"^(mvn|mvnw)$", lambda t: any(x in ("compile", "package") for x in t[1:])),
    (r"^(gradle|gradlew)$", lambda t: any(x in ("build", "assemble", "compileJava") for x in t[1:])),
    (r"^dotnet$", lambda t: len(t) > 1 and t[1] == "build"),
    (r"^(tsc|vite|webpack|next)$", lambda t: True if _prog(t[0]) == "tsc" else len(t) > 1 and t[1] == "build"),
    (r"^python[0-9.]*$", lambda t: len(t) > 2 and t[1] == "-m" and t[2] in ("build", "compileall")),
]


def is_build(cmd):
    for toks in _segments(cmd):
        t = list(toks)
        if _prog(t[0]) in _RUNNER_PREFIX and len(t) > 1:
            t = t[1:]
        for pat, pred in _BUILD:
            if re.match(pat, _prog(t[0])) and pred(t):
                return True
    return False


# ---------------------------------------------------------------- parsers

def _n(m, g=1):
    return int(m.group(g)) if m and m.group(g) else 0


def _pytest(out):
    lines = [l for l in out.splitlines() if re.search(r"\b\d+ (passed|failed|error|errors|skipped|xfailed|xpassed)\b", l)
             and re.search(r"(=+|in [\d.]+s)", l)]
    if not lines:
        return None
    l = lines[-1]
    c = lambda k: sum(int(x) for x in re.findall(r"(\d+) %s\b" % k, l))
    return {"passed": c("passed") + c("xpassed"), "failed": c("failed") + c("errors?"),
            "skipped": c("skipped"), "xfail": c("xfailed")}


def _unittest(out):
    m = re.findall(r"^Ran (\d+) tests? in", out, re.M)
    if not m:
        return None
    total = int(m[-1])
    tail = out[out.rfind("Ran "):]
    f = sum(int(x) for x in re.findall(r"(?:failures|errors)=(\d+)", tail))
    s = sum(int(x) for x in re.findall(r"skipped=(\d+)", tail))
    xf = sum(int(x) for x in re.findall(r"expected failures=(\d+)", tail))
    return {"passed": total - f - s - xf, "failed": f, "skipped": s, "xfail": xf}


def _jest(out):
    m = [l for l in out.splitlines() if re.match(r"^\s*Tests:\s", l)]
    if not m:
        return None
    l = m[-1]
    g = lambda k: sum(int(x) for x in re.findall(r"(\d+) %s" % k, l))
    return {"passed": g("passed"), "failed": g("failed"), "skipped": g("skipped") + g("todo"), "xfail": 0}


def _vitest(out):
    out = re.sub(r"\x1b\[[0-9;]*m", "", out)
    m = [l for l in out.splitlines() if re.match(r"^\s*Tests\s+\d", l)]
    if not m:
        return None
    l = m[-1]
    g = lambda k: sum(int(x) for x in re.findall(r"(\d+) %s" % k, l))
    return {"passed": g("passed"), "failed": g("failed"), "skipped": g("skipped") + g("todo"), "xfail": 0}


def _mocha(out):
    p = re.findall(r"^\s*(\d+) passing", out, re.M)
    if not p:
        return None
    f = re.findall(r"^\s*(\d+) failing", out, re.M)
    s = re.findall(r"^\s*(\d+) pending", out, re.M)
    return {"passed": int(p[-1]), "failed": int(f[-1]) if f else 0, "skipped": int(s[-1]) if s else 0, "xfail": 0}


def _go(out):
    if not re.search(r"^(ok|FAIL|---|PASS|\?)\s", out, re.M):
        return None
    return {"passed": len(re.findall(r"^\s*--- PASS:", out, re.M)),
            "failed": len(re.findall(r"^\s*--- FAIL:", out, re.M)) + len(re.findall(r"^FAIL\s+\S+\s+\[build failed\]", out, re.M)),
            "skipped": len(re.findall(r"^\s*--- SKIP:", out, re.M)), "xfail": 0}


def _cargo(out):
    m = re.findall(r"test result: \w+\. (\d+) passed; (\d+) failed; (\d+) ignored", out)
    if not m:
        return None
    return {"passed": sum(int(x[0]) for x in m), "failed": sum(int(x[1]) for x in m),
            "skipped": sum(int(x[2]) for x in m), "xfail": 0}


def _rspec(out):
    m = re.findall(r"(\d+) examples?, (\d+) failures?(?:, (\d+) pending)?", out)
    if not m:
        return None
    e, f, p = m[-1]
    p = int(p or 0)
    return {"passed": int(e) - int(f) - p, "failed": int(f), "skipped": p, "xfail": 0}


def _phpunit(out):
    m = re.search(r"OK \((\d+) tests?", out)
    if m:
        return {"passed": int(m.group(1)), "failed": 0, "skipped": 0, "xfail": 0}
    m = re.findall(r"^Tests: (\d+),.*$", out, re.M)
    if not m:
        return None
    line = re.findall(r"^Tests: \d+,.*$", out, re.M)[-1]
    g = lambda k: int((re.search(r"%s: (\d+)" % k, line) or [0, 0])[1])
    t, f, s = int(m[-1]), g("Failures") + g("Errors"), g("Skipped") + g("Incomplete")
    return {"passed": t - f - s, "failed": f, "skipped": s, "xfail": 0}


def _maven(out):
    m = re.findall(r"Tests run: (\d+), Failures: (\d+), Errors: (\d+), Skipped: (\d+)(?!,\s*Time)", out)
    if not m:
        return None
    t, f, e, s = (int(x) for x in m[-1])
    return {"passed": t - f - e - s, "failed": f + e, "skipped": s, "xfail": 0}


def _gradle(out):
    m = re.search(r"(\d+) tests completed(?:, (\d+) failed)?(?:, (\d+) skipped)?", out)
    if m:
        t, f, s = int(m.group(1)), _n(m, 2), _n(m, 3)
        return {"passed": t - f - s, "failed": f, "skipped": s, "xfail": 0}
    if re.search(r"BUILD SUCCESSFUL", out):
        return {"passed": 0, "failed": 0, "skipped": 0, "xfail": 0, "count_unknown": True}
    return None


def _dotnet(out):
    m = re.findall(r"Failed:\s*(\d+),\s*Passed:\s*(\d+),\s*Skipped:\s*(\d+)", out)
    if m:
        f, p, s = (int(x) for x in m[-1])
        return {"passed": p, "failed": f, "skipped": s, "xfail": 0}
    t = re.search(r"Total tests:\s*(\d+)", out)
    if t:
        g = lambda k: int((re.search(r"^\s*%s:\s*(\d+)" % k, out, re.M) or [0, 0])[1])
        return {"passed": g("Passed"), "failed": g("Failed"), "skipped": g("Skipped"), "xfail": 0}
    return None


PARSERS = {"pytest": [_pytest], "unittest": [_unittest], "jest": [_jest], "vitest": [_vitest],
           "mocha": [_mocha], "go": [_go], "cargo": [_cargo], "rspec": [_rspec], "phpunit": [_phpunit],
           "maven": [_maven], "gradle": [_gradle], "dotnet": [_dotnet],
           "npm": [_jest, _vitest, _mocha], "custom": []}
ALL_PARSERS = [_pytest, _unittest, _jest, _vitest, _mocha, _cargo, _rspec, _phpunit, _maven, _gradle, _dotnet]


def parse_results(runner, output):
    """Return {passed, failed, skipped, xfail} parsed from the output tail, or None."""
    if not output:
        return None
    for p in PARSERS.get(runner, []):
        r = p(output)
        if r is not None:
            return r
    return None


def test_outcome(runner, exit_code, output):
    counts = parse_results(runner, output)
    failed = exit_code != 0 or bool(counts and counts.get("failed", 0) > 0)
    return {"runner": runner, "exit": exit_code, "counts": counts, "status": "fail" if failed else "pass"}


# ---------------------------------------------------------------- files & skips

_TEST_FILE_GLOBS = ["test_*", "*_test.*", "*.test.*", "*.spec.*"]
_TEST_DIRS = {"tests", "test", "__tests__", "spec"}


def is_test_file(path):
    parts = path.replace("\\", "/").split("/")
    if any(p in _TEST_DIRS for p in parts[:-1]):
        return True
    return any(fnmatch.fnmatch(parts[-1], g) for g in _TEST_FILE_GLOBS)


SKIP_MARKERS = [
    ("pytest skip", r"@pytest\.mark\.(skip|skipif|xfail)\b"),
    ("unittest skip", r"@unittest\.(skip|skipIf|skipUnless|expectedFailure)\b"),
    ("JS skip", r"\b(it|describe|test)\.skip\s*\(|\b(xit|xdescribe|xtest)\s*\("),
    ("go t.Skip", r"\bt\.Skip(f|Now)?\("),
    ("rust #[ignore]", r"#\[ignore"),
    ("JUnit @Disabled/@Ignore", r"@(Disabled|Ignore)\b"),
    ("xUnit/NUnit skip", r"\[Fact\s*\(\s*Skip\s*=|\[Theory\s*\(\s*Skip\s*=|\[Ignore\b"),
    ("PHPUnit markTestSkipped", r"markTestSkipped\s*\("),
    ("RSpec pending/skip", r"^\s*(pending|skip)\b|\b(xit|xspecify|xcontext)\b"),
]


def skip_markers_added(diff, path=""):
    """Return [(marker kind, line)] for skip markers on added diff lines."""
    found = []
    for line in (diff or "").splitlines():
        if not line.startswith("+") or line.startswith("+++"):
            continue
        body = line[1:]
        for kind, pat in SKIP_MARKERS:
            if kind.startswith("RSpec") and not path.endswith(".rb"):
                continue
            if re.search(pat, body):
                found.append((kind, body.strip()[:200]))
                break
    return found
