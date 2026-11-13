"""Runs a README's commands as written, inside the readiness sandbox (``commands.py`` builds
the plan and starts this file under bwrap: a private filesystem view, its own network
namespace with only loopback, so every server the product starts listens there and nowhere
else). Standard library only; no import of repo_factory.

    python cmdrunner.py <plan.json>

The plan: ``{"repo", "home", "logs", "path_prefix": [dirs], "env": {k: v}, "quick": [step],
"restart": {"recipes": [recipe]} | null, "tests": [step] | null, "timeouts": {...}}``; a step
is ``{"cmd", "where", "kind"}`` (with ``"path": [dirs]``: an install step the check skips,
whose dirs join PATH for the steps after it), a recipe ``{"setup": [cmd], "start": cmd, "env": {k: v},
"health": path, "port": int | null, "source"}``.

Phases, in order, in one fresh copy (``repo``) and one ``HOME``, like an operator at a
terminal: **quick** (the quick start: install, run, backup; a command that starts serving
is left running, as in a second terminal), then everything is stopped; **restart** (start,
use, stop gracefully, start again at once and check it serves; use, SIGKILL, start again at
once and check it serves); **tests** (each documented test command; the exam runs a failing
one again as a series, ``exam.py``'s flake protocol). Writes ``<logs>/result.json``."""

import json
import os
import re
import signal
import socket
import subprocess
import sys
import time


def assignment_only(cmd):
    """``export KEY=...`` or a bare ``KEY=value`` with no command after it (``KEY=v cmd``
    runs ``cmd``)."""
    if re.match(r"^export\s+[A-Za-z_]\w*(=|\s*$)", cmd):
        return True
    m = re.match(r"^[A-Za-z_]\w*=", cmd)
    if not m:
        return False
    depth, quote, i, rest = 0, None, 0, cmd[m.end() :]
    while i < len(rest):
        c = rest[i]
        if quote:
            if c == quote:
                quote = None
            elif c == "\\" and quote == '"':
                i += 1
        elif c in "'\"":
            quote = c
        elif rest.startswith("$(", i):
            depth += 1
            i += 1
        elif c == ")" and depth:
            depth -= 1
        elif c.isspace() and not depth:
            return rest[i:].strip() == "" or rest[i:].lstrip().startswith("#")
        i += 1
    return True


def listeners():
    """The TCP ports something listens on in this network namespace (all ours)."""
    out = set()
    for f in ("/proc/net/tcp", "/proc/net/tcp6"):
        try:
            with open(f) as fh:
                lines = fh.read().splitlines()[1:]
        except OSError:
            continue
        for ln in lines:
            parts = ln.split()
            if len(parts) > 3 and parts[3] == "0A":
                out.add(int(parts[1].rsplit(":", 1)[1], 16))
    return out


def _addr(hexaddr):
    """``/proc/net/tcp*``'s local address (little-endian hex words) as text."""
    import socket
    import struct

    raw = bytes.fromhex(hexaddr)
    words = b"".join(
        struct.pack("<I", struct.unpack(">I", raw[i : i + 4])[0]) for i in range(0, len(raw), 4)
    )
    return socket.inet_ntop(socket.AF_INET if len(raw) == 4 else socket.AF_INET6, words)


def bound(ports):
    """The addresses each of ``ports`` listens on: ``{port: [address, ...]}``."""
    out = {}
    for f in ("/proc/net/tcp", "/proc/net/tcp6"):
        try:
            with open(f) as fh:
                lines = fh.read().splitlines()[1:]
        except OSError:
            continue
        for ln in lines:
            parts = ln.split()
            if len(parts) > 3 and parts[3] == "0A":
                a, p = parts[1].rsplit(":", 1)
                if int(p, 16) in ports:
                    try:
                        out.setdefault(str(int(p, 16)), []).append(_addr(a))
                    except (ValueError, OSError):
                        continue
    return out


# a command's own words for "the port is taken" (Python, Node, Go, a product's own message)
PORT_BUSY = re.compile(
    r"address already in use|EADDRINUSE|port \d+ is (?:already )?in use|"
    r"port is already in use|already running on port",
    re.I,
)

# a data file a user brings (never a script: a missing ``app.js`` is the README's bug)
FILE_ARG = re.compile(
    r"^\w[\w.-]*\.(?:txt|csv|tsv|json|jsonl|md|xml|html?|opml|ics|ya?ml|log|"
    r"eml|mbox|pdf|epub|bib|ris|enw|xlsx?|ods)$",
    re.I,
)


def inputs_missing(cmd, cwd):
    """The bare data-file names ``cmd`` passes (``tabs.txt``) that aren't in ``cwd`` when it
    runs: a file of the user's own in a README's example (Nextread's ``nextread import
    tabs.txt``). Not a failure of the README when the command fails saying so
    (``commands.items``); a file an earlier step wrote is there by then."""
    import shlex

    try:
        toks = shlex.split(cmd)
    except ValueError:
        toks = cmd.split()
    out = []
    for t in toks[1:]:
        if t in ("&&", "||", "|", ";"):
            break
        if FILE_ARG.match(t) and not os.path.exists(os.path.join(cwd, t)):
            out.append(t)
    return out


def tail(path, n=2500):
    try:
        with open(path, "rb") as fh:
            fh.seek(0, 2)
            size = fh.tell()
            fh.seek(max(0, size - n))
            return fh.read().decode("utf-8", "replace")
    except OSError:
        return ""


class Runner:
    def __init__(self, plan):
        self.plan = plan
        self.repo, self.home, self.logs = plan["repo"], plan["home"], plan["logs"]
        self.t = plan.get("timeouts") or {}
        path = list(plan.get("path_prefix") or []) + [
            os.path.join(self.home, ".local", "bin"),
            "/usr/local/bin",
            "/usr/bin",
            "/bin",
        ]
        self.env = {
            "HOME": self.home,
            "PATH": ":".join(path),
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "TERM": "dumb",
            "USER": "operator",
            "LOGNAME": "operator",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONUNBUFFERED": "1",
            "TMPDIR": "/tmp",
            "SHELL": "/bin/bash",
            **(plan.get("env") or {}),
        }
        self.cwd = self.repo
        self.procs = []
        self.n = 0

    # -- processes ------------------------------------------------------------------------
    def popen(self, cmd, env=None):
        self.n += 1
        log = os.path.join(self.logs, f"step{self.n}.log")
        fh = open(log, "wb")
        p = subprocess.Popen(
            ["bash", "-c", cmd],
            cwd=self.cwd,
            env=env or self.env,
            stdin=subprocess.DEVNULL,
            stdout=fh,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        fh.close()
        self.procs.append(p)
        return p, log

    @staticmethod
    def signal_group(p, sig):
        try:
            os.killpg(p.pid, sig)
        except (ProcessLookupError, PermissionError):
            pass

    def stop_all(self):
        """Every process this runner started, then anything else left in the sandbox (a
        daemon that left its process group): nothing serves between phases."""
        for p in self.procs:
            if p.poll() is None:
                self.signal_group(p, signal.SIGTERM)
        deadline = time.time() + 5
        for p in self.procs:
            while p.poll() is None and time.time() < deadline:
                time.sleep(0.1)
            if p.poll() is None:
                self.signal_group(p, signal.SIGKILL)
                p.wait()
        self.procs = []
        keep = {os.getpid(), 1}
        pid = os.getpid()
        while pid > 1:  # our ancestors (bwrap's init and its child)
            try:
                with open(f"/proc/{pid}/stat") as fh:
                    pid = int(fh.read().rsplit(")", 1)[1].split()[1])
            except (OSError, ValueError, IndexError):
                break
            keep.add(pid)
        for _ in range(2):
            others = [int(d) for d in os.listdir("/proc") if d.isdigit() and int(d) not in keep]
            for o in others:
                try:
                    os.kill(o, signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    pass
            time.sleep(0.3)
        deadline = time.time() + 5
        while listeners() and time.time() < deadline:
            time.sleep(0.2)

    # -- one command ------------------------------------------------------------------------
    def step(self, s, serve=True, timeout=None):
        """Run one step. ``serve``: a command that starts listening on a port while it runs
        is a server: it is left running (``serving``)."""
        cmd = s["cmd"]
        rec = {"cmd": cmd, "where": s.get("where"), "kind": s.get("kind")}
        t0 = time.time()
        if s.get("path"):
            # an install step the check skips (no network): what it would put on PATH is
            # there for the commands after it
            self.env["PATH"] = ":".join(list(s["path"]) + [self.env["PATH"]])
            rec.update(status="skipped", why=s.get("why"))
            return rec
        if assignment_only(cmd):
            # ``export KEY=$(...)``: evaluated once, kept for every later command
            p = subprocess.run(
                ["bash", "-c", cmd + "\nenv -0"],
                cwd=self.cwd,
                env=self.env,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                timeout=30,
            )
            if p.returncode == 0:
                for item in p.stdout.decode("utf-8", "replace").split("\0"):
                    if "=" in item:
                        k, v = item.split("=", 1)
                        if k not in ("PWD", "SHLVL", "_", "OLDPWD"):
                            self.env[k] = v
            rec.update(
                status="ok" if p.returncode == 0 else "failed",
                rc=p.returncode,
                output=p.stderr.decode("utf-8", "replace")[-1500:],
                env=True,
            )
            return rec
        m = re.match(r"^cd\s+(\S+)$", cmd)
        if m:
            d = os.path.normpath(os.path.join(self.cwd, os.path.expanduser(m.group(1))))
            if os.path.isdir(d) and d.startswith(self.repo):
                self.cwd = d
                rec.update(status="ok", rc=0, output="")
            else:
                rec.update(status="skipped", why="the fresh copy is the checkout")
            return rec
        missing = inputs_missing(cmd, self.cwd)
        if missing:
            rec["missing"] = missing
        before = listeners()
        p, log = self.popen(cmd)
        timeout = timeout or float(self.t.get("step_s", 60))
        status = None
        while True:
            rc = p.poll()
            if rc is not None:
                status = "ok" if rc == 0 else "failed"
                break
            new = listeners() - before
            if serve and new and (not s.get("finishes") or time.time() - t0 > timeout):
                time.sleep(0.5)
                if p.poll() is None:
                    status = "serving"
                    rec["ports"] = sorted(new)
                    rec["addrs"] = bound(new)
                    break
            if time.time() - t0 > timeout:
                status = "timeout"
                self.signal_group(p, signal.SIGKILL)
                p.wait()
                break
            time.sleep(0.2)
        rec.update(
            status=status,
            rc=p.returncode if status != "serving" else None,
            wall_s=round(time.time() - t0, 1),
            output=tail(log),
        )
        if status != "serving" and p.poll() is not None:
            # a command that exited can still leave a daemon serving (install.sh, nohup)
            gone = sorted(listeners() - before)
            if gone:
                rec["left_serving"] = gone
                rec["addrs"] = bound(set(gone))
        return rec

    # -- phases -----------------------------------------------------------------------------
    def up(self, out):
        """The quick-start step that started the server still serving, or None."""
        now = listeners()
        return next(
            (
                r
                for r in out
                if (r.get("status") == "serving" or r.get("left_serving"))
                and set(r.get("ports") or r.get("left_serving") or []) & now
            ),
            None,
        )

    def quick(self):
        """Each step in order. One start command per quick start: while a step's server
        still serves, a later start command is how the README says to start it again (after
        Ctrl+C), not a second server beside it. A step offered as an alternative (``# or``)
        runs only when the step before it didn't work."""
        out = []
        for s in self.plan.get("quick") or []:
            up = self.up(out) if s.get("start") else None
            prev = out[-1] if out else None
            if up:
                why = (
                    f"another start command: `{up['cmd']}` ({up.get('where')}) already "
                    "started it and still serves (one start command per quick start)"
                )
            elif s.get("alt") and prev and prev.get("status") in ("ok", "serving"):
                why = f"an alternative to `{prev['cmd']}`, which worked"
            else:
                was_up = self.up(out)
                rec = self.step(s)
                if not (
                    rec.get("status") == "failed"
                    and was_up
                    and PORT_BUSY.search(rec.get("output") or "")
                ):
                    out.append(rec)
                    continue
                # a start command the patterns don't know (``frontdesk.py --demo``, offered
                # "instead"): it failed only because the server before it holds the port
                why = (
                    f"another way to start it: `{was_up['cmd']}` ({was_up.get('where')}) "
                    "still serves on its port (one start command per quick start)"
                )
            out.append(
                {
                    "cmd": s["cmd"],
                    "where": s.get("where"),
                    "kind": s.get("kind"),
                    "status": "skipped",
                    "alternative": True,
                    "why": why,
                }
            )
        # a quick-start command that served is how this product starts: a restart recipe
        self.served = [
            {
                "setup": [],
                "start": r["cmd"],
                "env": {},
                "health": "/",
                "port": (r.get("ports") or [None])[0],
                "source": r.get("where"),
            }
            for r in out
            if r.get("status") == "serving"
        ]
        return out

    def tests(self):
        out, budget_end = [], time.time() + float(self.t.get("tests_total_s", 1500))
        for s in self.plan.get("tests") or []:
            if time.time() > budget_end:
                out.append(
                    {
                        "cmd": s["cmd"],
                        "where": s.get("where"),
                        "kind": "test",
                        "status": "not_run",
                        "why": "the time for the test commands ran out",
                    }
                )
                continue
            left = max(30.0, budget_end - time.time())
            out.append(
                self.step(s, serve=False, timeout=min(float(self.t.get("test_s", 900)), left))
            )
            self.stop_all()
        return out

    def serves(self, port, p, deadline):
        """``p`` is alive and something accepts connections on ``port`` before ``deadline``."""
        while time.time() < deadline:
            if p.poll() is not None:
                return False
            if port in listeners():
                try:
                    socket.create_connection(("127.0.0.1", port), 1).close()
                    return True
                except OSError:
                    pass
            time.sleep(0.1)
        return False

    @staticmethod
    def use(port, path, n=5):
        """``n`` requests, each on its own connection, read to the end: the server closes
        first, so its side of each connection is left in TIME_WAIT (what blocks a rebind
        without address reuse)."""
        got = []
        for _ in range(n):
            try:
                s = socket.create_connection(("127.0.0.1", port), 3)
                s.settimeout(3)
                s.sendall(
                    f"GET {path} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n"
                    "Connection: close\r\n\r\n".encode()
                )
                data = b""
                while True:
                    chunk = s.recv(65536)
                    if not chunk:
                        break
                    data += chunk
                s.close()
                got.append(data.split(b"\r\n", 1)[0].decode("latin-1")[:60] or "(no answer)")
            except OSError as e:
                got.append(f"error: {e}")
        return got

    def start(self, recipe, env):
        before = listeners()
        p, log = self.popen(recipe["start"], env)
        deadline = time.time() + float(self.t.get("start_s", 25))
        port = None
        while time.time() < deadline and p.poll() is None:
            new = listeners() - before
            if recipe.get("port") in new:
                port = recipe["port"]
            elif new:
                port = min(new)
            if port:
                break
            time.sleep(0.1)
        return p, log, port

    def restart(self):
        spec = self.plan.get("restart") or {}
        attempts = []
        serve_s = float(self.t.get("serve_s", 15))
        given = list(spec.get("recipes") or [])
        # the operational checks' recipe first, then what served in the quick start, then
        # the README's other start commands
        lead = [r for r in given if r.get("source") == "the operational checks"]
        rest = [r for r in given if r not in lead]
        recipes = (
            lead
            + [
                r
                for r in getattr(self, "served", [])
                if not any(r["start"] == g["start"] for g in lead)
            ]
            + [
                r
                for r in rest
                if not any(r["start"] == s["start"] for s in getattr(self, "served", []))
            ]
        )
        for recipe in recipes[:4]:
            env = dict(self.env)
            env.update({str(k): str(v) for k, v in (recipe.get("env") or {}).items()})
            saved, self.env = self.env, env
            setup = [
                self.step({"cmd": c, "kind": "setup"}, serve=False)
                for c in recipe.get("setup") or []
            ]
            self.env = saved
            p, log, port = self.start(recipe, env)
            if not port:
                attempts.append(
                    {
                        "recipe": recipe,
                        "setup": setup,
                        "why": (
                            f"exited with {p.returncode} before listening"
                            if p.poll() is not None
                            else "didn't listen on a port"
                        ),
                        "output": tail(log),
                    }
                )
                self.stop_all()
                continue
            path = recipe.get("health") or "/"
            res = {
                "ran": True,
                "recipe": recipe,
                "setup": setup,
                "port": port,
                "first_use": self.use(port, path),
            }
            # 1. a graceful stop (SIGTERM, as systemd and Ctrl+C do), then start at once
            t0 = time.time()
            self.signal_group(p, signal.SIGTERM)
            while p.poll() is None and time.time() - t0 < 10:
                time.sleep(0.05)
            res["graceful"] = {
                "exited": p.poll() is not None,
                "rc": p.poll(),
                "exit_s": round(time.time() - t0, 2),
            }
            if p.poll() is None:
                self.signal_group(p, signal.SIGKILL)
                p.wait()
            res["restart"] = self.again(recipe, env, port, path, serve_s, time.time())
            # 2. a crash (SIGKILL), then start at once
            if res["restart"]["ok"]:
                q = res["restart"].pop("_proc")
                res["second_use"] = self.use(port, path)
                self.signal_group(q, signal.SIGKILL)
                q.wait()
                res["crash"] = self.again(recipe, env, port, path, serve_s, time.time())
                res["crash"].pop("_proc", None)
            else:
                res["restart"].pop("_proc", None)
                res["crash"] = {
                    "ok": None,
                    "why": "not checked: the restart after a stop already failed",
                }
            res["attempts"] = attempts
            self.stop_all()
            return res
        return {
            "ran": False,
            "attempts": attempts,
            "why": "no documented start command served" if attempts else "no start command to try",
        }

    def again(self, recipe, env, port, path, serve_s, stopped):
        """Start once more right after the stop (no wait) and check it serves."""
        lag = round(time.time() - stopped, 2)
        q, log = self.popen(recipe["start"], env)
        t0 = time.time()
        ok = self.serves(port, q, t0 + serve_s)
        out = {"ok": ok, "started_after_s": lag, "serve_s": round(time.time() - t0, 2)}
        if ok:
            out["use"] = self.use(port, path, 1)
            out["_proc"] = q
        else:
            out.update(
                rc=q.poll(),
                output=tail(log),
                why=(
                    f"exited with {q.poll()} before serving"
                    if q.poll() is not None
                    else f"not serving on port {port} within {serve_s:g} s"
                ),
            )
            if q.poll() is None:
                self.signal_group(q, signal.SIGKILL)
                q.wait()
        return out


def main():
    plan = json.load(open(sys.argv[1]))
    r = Runner(plan)
    out = {"quick": [], "restart": None, "tests": None}
    try:
        out["quick"] = r.quick()
        r.stop_all()
        if plan.get("restart") is not None:
            out["restart"] = r.restart()
            r.stop_all()
        if plan.get("tests") is not None:
            r.cwd = r.repo
            out["tests"] = r.tests()
    finally:
        r.stop_all()
        with open(os.path.join(plan["logs"], "result.json"), "w") as fh:
            json.dump(out, fh)


if __name__ == "__main__":
    main()
