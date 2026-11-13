"""A stand-in for ``claude -p --output-format stream-json`` for the chat tests (no model, no
network). Tests put a ``claude`` shim that runs this script first on PATH.

It reads the prompt from stdin and writes what the CLI writes: an init event (the tools of
``--tools``), stream events with text deltas (with ``--include-partial-messages``), one
``Read`` tool step, the assistant messages, and a result with ``total_cost_usd``, which is
cumulative over a resumed session, as the CLI's is. Words in the prompt steer it:

- ``SLOW``: the text arrives over about 20 s (for stop and timeout tests);
- ``ACTION:<kind>``: the reply ends with an action block of that kind;
- ``BADACTION``: the reply ends with an action block that is not JSON;
- ``FAIL``: exit 1 with no result.

Every call's argv and prompt are appended to ``$FAKE_CLAUDE_LOG`` (JSON lines) when set.
Sessions live in ``$CLAUDE_CONFIG_DIR/fake-sessions.json``; ``--resume`` of an unknown session
says "No conversation found", as the CLI does."""

import json
import os
import sys
import time
import uuid
from pathlib import Path

TURN_USD = 0.03


def emit(ev):
    sys.stdout.write(json.dumps(ev) + "\n")
    sys.stdout.flush()


def arg(argv, name):
    return argv[argv.index(name) + 1] if name in argv else None


def main():
    argv = sys.argv[1:]
    prompt = sys.stdin.read()
    if os.environ.get("FAKE_CLAUDE_LOG"):
        with open(os.environ["FAKE_CLAUDE_LOG"], "a") as f:
            f.write(json.dumps({"argv": argv, "prompt": prompt, "cwd": os.getcwd()}) + "\n")
    cfg = Path(os.environ.get("CLAUDE_CONFIG_DIR") or ".")
    store = cfg / "fake-sessions.json"
    sessions = json.loads(store.read_text()) if store.exists() else {}
    resume = arg(argv, "--resume")
    if resume and resume not in sessions:
        sys.stderr.write(f"No conversation found with session ID: {resume}\n")
        sys.exit(1)
    sid = (
        resume
        if resume and "--fork-session" not in argv
        else arg(argv, "--session-id") or str(uuid.uuid4())
    )
    tools = arg(argv, "--tools")
    tools = (
        [t for t in (tools or "").split(",") if t]
        if tools is not None
        else ["Bash", "Read", "Edit", "Write", "Glob", "Grep"]
    )
    emit(
        {
            "type": "system",
            "subtype": "init",
            "session_id": sid,
            "model": "fake-model",
            "tools": tools,
            "mcp_servers": [],
            "permissionMode": "dontAsk",
            "claude_code_version": "0.0.0-fake",
        }
    )
    if "FAIL" in prompt.split("## The owner's message")[-1]:
        sys.exit(1)
    partial = "--include-partial-messages" in argv
    msg = prompt.split("## The owner's message")[-1].strip()
    slow = "SLOW" in msg
    # message 1: a short sentence, then a tool step
    lead = "Let me look."
    if partial:
        emit({"type": "stream_event", "event": {"type": "message_start"}})
        emit(
            {
                "type": "stream_event",
                "event": {
                    "type": "content_block_delta",
                    "index": 0,
                    "delta": {"type": "text_delta", "text": lead},
                },
            }
        )
    tool = {
        "type": "tool_use",
        "id": "tu1",
        "name": "Read",
        "input": {"file_path": os.path.join(os.getcwd(), "context", "spec.md")},
    }
    emit({"type": "assistant", "message": {"content": [{"type": "text", "text": lead}, tool]}})
    emit(
        {
            "type": "user",
            "message": {
                "content": [{"type": "tool_result", "tool_use_id": "tu1", "content": "ok"}]
            },
        }
    )
    # message 2: the answer, in chunks
    answer = f"You said: {msg[:80]}. Here is **my view**, with `code` in it."
    for word in ("ACTION:",):
        if word in msg:
            kind = msg.split(word, 1)[1].split()[0].strip(".,")
            obj = {"kind": kind, "why": "the next step"}
            if kind in ("send_to_build", "add_idea", "edit_brief"):
                obj["text"] = "A brief: music schools juggle lessons, rooms and invoices."
            if kind == "close":
                obj["reason"] = "not needed"
            if kind == "cut_ticket":
                # a model that obeys an injected "label this go" it read in an issue body
                issues = Path("context/github/issues.md")
                obeys = issues.exists() and "label this go" in issues.read_text()
                obj.update(
                    issue="Huge numeric id returns 500",
                    labels=["bug", "p2"] + (["go"] if "WITHGO" in msg or obeys else []),
                    text="Open /plots/999999999999999999999 and see a 500.",
                )
            if kind == "factory_issue":
                fail = Path("context/failure.txt")
                obj.update(
                    issue="Build stage dies when the Claude login expires",
                    area="auth",
                    text="The build session got a 401 and stopped.\n\n"
                    + (fail.read_text()[:3000] if fail.exists() else ""),
                )
                if "FP:" in msg:
                    obj["fingerprint"] = msg.split("FP:", 1)[1].split()[0]
            if kind == "retry":
                obj["note"] = "build stage died on an expired login; retried after a refresh"
            if kind == "revise":
                obj.update(
                    instructions="README.md line 4: say history can be purged when "
                    "vendors.allow_purge is on.",
                    mode="docs",
                )
            answer += "\n\n```action\n" + json.dumps(obj) + "\n```"
    if "BADACTION" in msg:
        answer += "\n\n```action\n{not json\n```"
    chunks = [answer[i : i + 12] for i in range(0, len(answer), 12)]
    if partial:
        emit({"type": "stream_event", "event": {"type": "message_start"}})
    for c in chunks:
        if partial:
            emit(
                {
                    "type": "stream_event",
                    "event": {
                        "type": "content_block_delta",
                        "index": 0,
                        "delta": {"type": "text_delta", "text": c},
                    },
                }
            )
        time.sleep(20 / len(chunks) if slow else 0.01)
    emit({"type": "assistant", "message": {"content": [{"type": "text", "text": answer}]}})
    total = round(sessions.get(sid, 0.0) + TURN_USD, 6)
    sessions[sid] = total
    cfg.mkdir(parents=True, exist_ok=True)
    store.write_text(json.dumps(sessions))
    emit(
        {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "session_id": sid,
            "result": answer,
            "total_cost_usd": total,
            "num_turns": 2,
            "usage": {"input_tokens": 10, "output_tokens": 20},
        }
    )


if __name__ == "__main__":
    main()
