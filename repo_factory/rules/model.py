"""A rule: one executable pytest scenario plus the metadata that says where it came from
and what the factory currently believes about it (docs/design/rules.md)."""

from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass, field

KINDS = ("requirement", "interaction", "edge", "regression")
STATUSES = ("candidate", "active", "disputed", "retired")
VISIBILITIES = ("visible", "held")
MODES = ("requirements", "interactions", "edges")
# Lower-case prefixes so rule ids never look like checklist ids (R1, R2, ...).
PREFIX = {"requirement": "q", "interaction": "x", "edge": "e", "regression": "g"}
VERDICTS = ("code_wrong", "rule_wrong", "ambiguous")
# Build v3 §4 (the controller): the arbiter's fourth verdict, offered only with it on.
DESIGN_FLAW = "design_flaw"


@dataclass
class Rule:
    id: str
    title: str
    kind: str  # requirement | interaction | edge | regression
    source: str  # the spec sentence(s) or answer it traces to, quoted
    code: str  # a self-contained pytest module (one or more test_*)
    milestones: list[str] = field(default_factory=list)
    expect: str = ""  # the expected behaviour in one plain sentence
    status: str = "candidate"  # candidate | active | disputed | retired
    reason: str = ""  # why it has its status (validation, arbitration)
    origin: dict = field(default_factory=dict)  # {"mode", "prompt", "pass", "after", "sample"}
    visibility: str = "visible"  # visible (workers see it) | held (never in a workspace)
    source_ref: str = ""  # where the quote was found: "spec" | "answer:q1" | ...
    version: int = 1  # bumped by a repair
    checks: dict = field(default_factory=dict)  # validation results {a, b, c, d}
    verdicts: list = field(default_factory=list)  # arbitration verdicts, oldest first
    caught: int = 0  # code_wrong verdicts (the learning hook)
    fixed: bool = False  # passed on a kept candidate after a code_wrong verdict

    @property
    def test_file(self) -> str:
        return f"test_rule_{self.id}.py"

    def digest(self) -> str:
        return hashlib.sha256(normalize_code(self.code).encode()).hexdigest()[:16]

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> Rule:
        known = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in d.items() if k in known})

    def brief(self) -> str:
        ms = ",".join(self.milestones) or "-"
        return f"{self.id} [{self.kind}; {ms}] {self.title}"


def normalize_code(code: str) -> str:
    """Whitespace- and comment-insensitive text of a rule (dedupe and hashing)."""
    import ast

    try:
        return ast.unparse(ast.parse(code))
    except SyntaxError:
        return re.sub(r"\s+", " ", re.sub(r"#.*", "", code)).strip()
