"""Auto-detection of the agent's final message (and reported tool calls).

Checked in order: Claude Code transcript, Codex CLI session file, Aider chat history,
the agent's captured stdout (non-interactive runs).
"""
import calendar
import glob
import json
import os
import re
import time

SLACK = 120  # seconds of tolerance around the session window


def _ts(s):
    if not s:
        return None
    try:
        s = s.replace("Z", "")
        main = s.split(".")[0].split("+")[0]
        return calendar.timegm(time.strptime(main, "%Y-%m-%dT%H:%M:%S"))
    except (ValueError, AttributeError):
        return None


def _in_window(ts, start, end):
    t = _ts(ts)
    return t is None or (start - SLACK <= t <= end + SLACK)


def claude_code(cwd, start, end, home=None):
    home = home or os.path.expanduser("~")
    key = re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(cwd))
    files = glob.glob(os.path.join(home, ".claude", "projects", key, "*.jsonl"))
    files = [f for f in files if os.path.getmtime(f) >= start - SLACK]
    best = None
    for f in sorted(files, key=os.path.getmtime):
        final, tools, last_ts = None, [], None
        for line in open(f, errors="replace"):
            try:
                d = json.loads(line)
            except ValueError:
                continue
            if d.get("type") != "assistant" or not _in_window(d.get("timestamp"), start, end):
                continue
            if d.get("cwd") and os.path.realpath(d["cwd"]) != os.path.realpath(cwd):
                continue
            content = (d.get("message") or {}).get("content") or []
            if isinstance(content, str):
                content = [{"type": "text", "text": content}]
            texts = [c.get("text", "") for c in content if isinstance(c, dict) and c.get("type") == "text"]
            for c in content:
                if isinstance(c, dict) and c.get("type") == "tool_use" and c.get("name") == "Bash":
                    cmd = (c.get("input") or {}).get("command")
                    if cmd:
                        tools.append(cmd)
            if any(t.strip() for t in texts):
                final = "\n".join(t for t in texts if t.strip())
                last_ts = d.get("timestamp")
        if final:
            best = {"source": "claude-code:" + f, "text": final, "tool_calls": tools, "ts": last_ts}
    return best


def codex(cwd, start, end, home=None):
    home = home or os.path.expanduser("~")
    files = glob.glob(os.path.join(home, ".codex", "sessions", "**", "*.jsonl"), recursive=True)
    files = [f for f in files if os.path.getmtime(f) >= start - SLACK]
    best = None
    for f in sorted(files, key=os.path.getmtime):
        final, tools, session_cwd = None, [], None
        for line in open(f, errors="replace"):
            try:
                d = json.loads(line)
            except ValueError:
                continue
            item = d.get("payload") if isinstance(d.get("payload"), dict) else d
            if d.get("type") == "session_meta" or item.get("type") == "session_meta":
                session_cwd = item.get("cwd")
            if item.get("type") == "turn_context" or d.get("type") == "turn_context":
                session_cwd = item.get("cwd") or session_cwd
            it = item.get("type")
            if it == "message" and item.get("role") == "assistant":
                texts = [c.get("text", "") for c in item.get("content") or [] if c.get("type") in ("output_text", "text")]
                if any(t.strip() for t in texts):
                    final = "\n".join(texts)
            elif it in ("function_call", "local_shell_call"):
                args = item.get("arguments") or item.get("action") or {}
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except ValueError:
                        args = {}
                cmd = args.get("command")
                if isinstance(cmd, list):
                    cmd = cmd[-1] if len(cmd) >= 3 and cmd[-2] in ("-c", "-lc") else " ".join(cmd)
                if cmd:
                    tools.append(cmd)
        if session_cwd and os.path.realpath(session_cwd) != os.path.realpath(cwd):
            continue
        if final:
            best = {"source": "codex:" + f, "text": final, "tool_calls": tools}
    return best


def aider(cwd, start, end):
    p = os.path.join(cwd, ".aider.chat.history.md")
    if not os.path.exists(p) or os.path.getmtime(p) < start - SLACK:
        return None
    text = open(p, errors="replace").read()
    idx = text.rfind("\n#### ")
    if idx < 0:
        return None
    block = text[idx + 1:].split("\n", 1)
    if len(block) < 2:
        return None
    lines = [l for l in block[1].splitlines() if not l.startswith(">") and not l.startswith("#### ")]
    final = "\n".join(lines).strip()
    return {"source": "aider:" + p, "text": final, "tool_calls": None} if final else None


def stdout_block(agent_output):
    if not agent_output or not agent_output.strip():
        return None
    text = re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", agent_output).strip()
    return {"source": "agent-stdout", "text": text, "tool_calls": None}


def detect(m):
    meta = m["meta"]
    cwd = meta.get("cwd") or "."
    start = _ts(meta.get("started")) or 0
    end = _ts((m.get("run_end") or {}).get("ts")) or time.time()
    for fn in (lambda: claude_code(cwd, start, end), lambda: codex(cwd, start, end), lambda: aider(cwd, start, end),
               lambda: stdout_block(m.get("agent_output"))):
        try:
            r = fn()
        except OSError:
            r = None
        if r:
            return r
    return None
