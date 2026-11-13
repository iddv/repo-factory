"""The JSON object in a model's answer: a fenced block (nested braces kept), else the outermost
braces. Moved from ``scoring/builder.py`` (S-5a): the rule set and v5 read answers with it."""

from __future__ import annotations

import json
import re


def extract_json(text: str):
    # Greedy inside the fence: a non-greedy match stops at the first "}" of nested JSON.
    m = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        raise ValueError("no JSON object in output")
    return json.loads(text[start : end + 1])
