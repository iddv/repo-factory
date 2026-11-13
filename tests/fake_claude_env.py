#!/usr/bin/env python3
"""A ``claude`` stand-in that records how it was started (no model, no network): its argv, its
whole environment, and what it read from a ``*_FILE_DESCRIPTOR`` pipe, appended as one JSON line
to ``$FAKE_CLAUDE_LOG``. It then answers as ``claude -p`` does: one JSON object for
``--output-format json``, an init event and a result for ``stream-json``."""

import json
import os
import sys

FD_VARS = ("CLAUDE_CODE_OAUTH_TOKEN_FILE_DESCRIPTOR", "CLAUDE_CODE_API_KEY_FILE_DESCRIPTOR")

piped = {}
for v in FD_VARS:
    if os.environ.get(v):
        fd, chunks = int(os.environ[v]), []
        while True:
            b = os.read(fd, 4096)
            if not b:
                break
            chunks.append(b)
        os.close(fd)
        piped[v] = b"".join(chunks).decode()
prompt = sys.stdin.read()
with open(os.environ["FAKE_CLAUDE_LOG"], "a") as f:
    f.write(json.dumps({"argv": sys.argv[1:], "env": dict(os.environ), "piped": piped}) + "\n")
argv = sys.argv[1:]
fmt = argv[argv.index("--output-format") + 1] if "--output-format" in argv else "text"
result = {
    "type": "result",
    "subtype": "success",
    "is_error": False,
    "result": "ok",
    "session_id": "s-fake",
    "total_cost_usd": 0.01,
    "num_turns": 1,
    "usage": {"input_tokens": 1, "output_tokens": 1},
}
if fmt == "json":
    print(json.dumps(result))
else:
    tools = argv[argv.index("--tools") + 1].split(",") if "--tools" in argv else ["Bash"]
    print(
        json.dumps(
            {
                "type": "system",
                "subtype": "init",
                "session_id": "s-fake",
                "model": "fake-model",
                "tools": [t for t in tools if t],
                "mcp_servers": [],
                "permissionMode": "dontAsk",
            }
        )
    )
    print(json.dumps(result))
