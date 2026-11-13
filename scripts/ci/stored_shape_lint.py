#!/usr/bin/env python3
"""The stored-shape lint (RCA 2026-10-23, A2; repo-factory-notes plans/CTR-DESIGN.md §3.3).

A value read from stored JSON and used as if it could not be None: the RCA's own line was
``f"sim {v.get('median_similarity', 0):.2f}"`` with the similarity stored as null. Neither mypy
nor pyright inspects an f-string's format spec, typed or not, so this lint runs beside the type
check. Standard library only; it reads the code (AST), it never runs it.

Rules (each finding printed as ``path:line: RULE: words``):

  GET-FORMAT   a ``.get(...)`` result (direct, or through one assignment in the same function)
               formatted with a numeric spec (``:.2f``, ``:d``, ``:,``, ``:%``), passed to
               round()/int()/float()/len()/abs()/sum()/max()/min(), in arithmetic or in an ordered
               comparison. A ``.get(k, default)`` counts only on a dict from stored JSON (a local
               counter's ``.get(k, 0) + 1`` is the counting idiom).
  GET-DEFAULT  ``.get(k, default)`` with a non-None default on a dict from stored JSON (from
               ``json.loads(...)``, a ``.validation``/``.card``/``.summary``/... attribute or key,
               or ``X or {}`` over one): the default does not cover a key stored as null. Read it
               through its contract type (``stored/``).
  ATTR-FORMAT  a field some class declares ``| None`` (and no class declares plain), formatted
               with a numeric spec with no guard on that field in the enclosing ``if``, the
               ``and``/``or`` chain or the conditional expression: the typed f-string the type
               checkers miss.
  JSON-OUTSIDE a stored column with a contract type (``COLUMN = (table, column)`` in
               ``stored/``) loaded or dumped outside its contract: ``json.loads(row["col"])``,
               ``json.loads(d.get("col"))``, ``"col": json.dumps(...)``, or
               ``set_setting(<a typed key>, json.dumps(...))``. Readers never see the dict;
               writers store ``T.to_json()``.

Guards it understands: ``x is not None``, ``x is None`` (the other arm), a bare ``x``,
``isinstance(x, ...)``, ``str(x or "").isdigit()``, ``and``/``or`` chains, and a ``try`` that
catches TypeError. It does not follow a value across functions: a finding that is guarded
elsewhere is dismissed on its line with ``# stored-shape: <why>``.

The baseline (``scripts/ci/stored_shape_baseline.txt``) lists the findings that were already
there, as ``path:RULE:<hash of the line's text>``, so a line that moves keeps its entry: a new
finding fails, and so does an entry that no longer matches anything (it was fixed: take it out,
with ``--update``). The baseline empties as each column gets its contract.

Usage:
  stored_shape_lint.py [PATH...]          check (default: repo_factory); exit 1 on a new finding
                                          or a stale baseline entry
  stored_shape_lint.py --all [PATH...]    print every finding, baseline or not
  stored_shape_lint.py --update [PATH...] write the baseline: today's findings
"""

from __future__ import annotations

import ast
import fnmatch
import hashlib
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
BASELINE = Path(__file__).resolve().parent / "stored_shape_baseline.txt"
DEFAULT_PATHS = ["repo_factory"]
SKIP_PARTS = {"tests", ".venv", "node_modules", "examples", "lab", "__pycache__"}
DISMISS = "# stored-shape:"

NUMERIC_CALLS = {"round", "int", "float", "len", "abs", "sum", "max", "min"}
STORED_ATTRS = {
    "validation",
    "card",
    "summary",
    "detail",
    "payload",
    "grants",
    "context",
    "params",
    "checks",
    "items",
    "scores",
    "terms",
    "pitch",
    "build",
    "gate",
    "publish",
    "signals",
    "settings",
    "stats",
    "health",
    "fields",
    "because",
    "needs",
    "dropped",
    "search",
    "quality",
    "density",
    "trajectory",
    "source_mix",
    "topic_mix",
    "origin_mix",
    "cites",
    "shares",
    "material",
    "report",
    "chain",
}

Finding = tuple[int, str, str]  # (line, rule, words)


def _is_get(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "get"
    )


def _none_default(call: ast.Call) -> bool:
    return len(call.args) < 2 or (
        isinstance(call.args[1], ast.Constant) and call.args[1].value is None
    )


def numeric_spec(fmt: ast.FormattedValue) -> bool:
    """``:.2f``, ``:d``, ``:,``, ``:5.1%``: a spec only a number takes (``:>10`` is not)."""
    spec = fmt.format_spec
    if spec is None or not isinstance(spec, ast.JoinedStr):
        return False
    text = "".join(
        c.value for c in spec.values if isinstance(c, ast.Constant) and isinstance(c.value, str)
    )
    if not text or ("%" in text and not text.endswith("%")):  # empty, or a date's %Y-%m-%d
        return False
    return (
        text.endswith(("f", "d", "%", "e", "g", "F", "E", "G", "n")) or "," in text or "_" in text
    )


class Function(ast.NodeVisitor):
    """The GET- rules, inside one function body."""

    def __init__(self, optional: set[str] | None = None) -> None:
        self.optional = optional or set()  # field names some contract declares `| None`
        self.from_get: set[str] = set()  # names assigned from a .get() call
        self.from_field: set[str] = set()  # names assigned from a `| None` field
        self.stored: set[str] = set()  # names holding a dict parsed from stored JSON
        self.in_try = 0  # inside a try that catches TypeError
        self.out: list[Finding] = []

    # what is a stored dict
    def stored_expr(self, node: ast.AST) -> bool:
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "loads"
        ):
            return True
        if isinstance(node, ast.Attribute) and node.attr in STORED_ATTRS:
            return True
        if (
            isinstance(node, ast.Subscript)
            and isinstance(node.slice, ast.Constant)
            and node.slice.value in STORED_ATTRS
        ):
            return True
        if isinstance(node, ast.BoolOp) and isinstance(node.op, ast.Or):
            return any(self.stored_expr(v) for v in node.values)
        if isinstance(node, ast.Name) and node.id in self.stored:
            return True
        if _is_get(node):
            assert isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            return self.stored_expr(node.func.value)  # a dict inside a stored dict
        return False

    def visit_Assign(self, node: ast.Assign) -> None:
        for t in node.targets:
            if isinstance(t, ast.Name):
                if _is_get(node.value) and self.suspect(node.value):
                    self.from_get.add(t.id)
                else:
                    self.from_get.discard(t.id)
                if (
                    isinstance(node.value, ast.Attribute)
                    and node.value.attr in self.optional
                    and not self.in_try
                ):
                    self.from_field.add(t.id)  # sim = v.median_similarity
                else:
                    self.from_field.discard(t.id)
                if self.stored_expr(node.value):
                    self.stored.add(t.id)
                elif t.id in self.stored and not isinstance(node.value, ast.Name):
                    self.stored.discard(t.id)
        self.generic_visit(node)

    # the uses
    def suspect(self, node: ast.AST) -> str | None:
        """Why this expression may be a None read from stored data, or None."""
        if self.in_try:
            return None
        if _is_get(node):
            assert isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            stored = self.stored_expr(node.func.value)
            if not _none_default(node) and not stored:
                return None
            return "a .get() result" + (" of a stored dict" if stored else "")
        if isinstance(node, ast.Name) and node.id in self.from_get:
            return f"{node.id}, assigned from .get()"
        return None

    def visit_JoinedStr(self, node: ast.JoinedStr) -> None:
        for v in node.values:
            if isinstance(v, ast.FormattedValue) and numeric_spec(v):
                why = self.suspect(v.value)
                if why:
                    self.out.append((node.lineno, "GET-FORMAT", f"{why} formatted as a number"))
                elif isinstance(v.value, ast.Name) and v.value.id in self.from_field:
                    self.out.append(
                        (
                            node.lineno,
                            "ATTR-FORMAT",
                            f"{v.value.id}, assigned from a field declared `| None`, "
                            "formatted as a number",
                        )
                    )
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        if isinstance(node.func, ast.Name) and node.func.id in NUMERIC_CALLS:
            for a in node.args:
                why = self.suspect(a)
                if why:
                    self.out.append(
                        (node.lineno, "GET-FORMAT", f"{why} passed to {node.func.id}()")
                    )
        if _is_get(node) and not _none_default(node):
            assert isinstance(node.func, ast.Attribute)
            if self.stored_expr(node.func.value):
                key = node.args[0].value if isinstance(node.args[0], ast.Constant) else "?"
                self.out.append(
                    (
                        node.lineno,
                        "GET-DEFAULT",
                        f".get({key!r}, default) on a stored dict: the default does not "
                        "cover a key stored as null",
                    )
                )
        self.generic_visit(node)

    def visit_BinOp(self, node: ast.BinOp) -> None:
        if isinstance(node.op, (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod)):
            for side in (node.left, node.right):
                why = self.suspect(side)
                if why and not (isinstance(node.op, ast.Add) and self._stringy(node)):
                    self.out.append((node.lineno, "GET-FORMAT", f"{why} in arithmetic"))
        self.generic_visit(node)

    @staticmethod
    def _stringy(node: ast.BinOp) -> bool:
        return any(
            isinstance(s, ast.JoinedStr)
            or (isinstance(s, ast.Constant) and isinstance(s.value, str))
            for s in (node.left, node.right)
        )

    def visit_Compare(self, node: ast.Compare) -> None:
        if any(isinstance(op, (ast.Lt, ast.LtE, ast.Gt, ast.GtE)) for op in node.ops):
            for side in (node.left, *node.comparators):
                why = self.suspect(side)
                if why:
                    self.out.append((node.lineno, "GET-FORMAT", f"{why} in an ordered comparison"))
        self.generic_visit(node)

    # guards: the name is covered in the guarded region (an if's body, the true arm of
    # ``a if t else b``, what follows it in an ``and`` chain); ``x is None`` covers the other arm.
    @staticmethod
    def _guards(test: ast.AST) -> tuple[set[str], set[str]]:
        """(names covered when the test is true, names covered when it is false)."""
        yes: set[str] = set()
        no: set[str] = set()
        if isinstance(test, ast.Name):
            yes.add(test.id)
        elif isinstance(test, ast.Compare) and isinstance(test.left, ast.Name):
            if any(isinstance(op, ast.IsNot) for op in test.ops):
                yes.add(test.left.id)
            elif any(isinstance(op, ast.Is) for op in test.ops) and all(
                isinstance(c, ast.Constant) and c.value is None for c in test.comparators
            ):
                no.add(test.left.id)
        elif (
            isinstance(test, ast.Call)
            and isinstance(test.func, ast.Name)
            and test.func.id == "isinstance"
            and test.args
            and isinstance(test.args[0], ast.Name)
        ):
            yes.add(test.args[0].id)
        elif (
            isinstance(test, ast.Call)
            and isinstance(test.func, ast.Attribute)
            and test.func.attr in ("isdigit", "isnumeric", "isdecimal")
        ):
            inner: ast.AST = test.func.value  # str(x or "").isdigit(): a number's text
            if isinstance(inner, ast.Call) and inner.args:
                inner = inner.args[0]
            if isinstance(inner, ast.BoolOp):
                inner = inner.values[0]
            if isinstance(inner, ast.Name):
                yes.add(inner.id)
        elif isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not):
            n, y = Function._guards(test.operand)
            yes, no = n, y
        elif isinstance(test, ast.BoolOp) and isinstance(test.op, ast.And):
            for v in test.values:
                yes |= Function._guards(v)[0]
        elif isinstance(test, ast.BoolOp) and isinstance(test.op, ast.Or):
            for v in test.values:
                no |= Function._guards(v)[1]
        return yes, no

    def _with_cleared(self, names: set[str], nodes: list[ast.AST]) -> None:
        cleared, fields = names & self.from_get, names & self.from_field
        self.from_get -= cleared
        self.from_field -= fields
        for n in nodes:
            self.visit(n)
        self.from_get |= cleared
        self.from_field |= fields

    def visit_Try(self, node: ast.Try) -> None:
        """``try: int(x.get(k)) except (TypeError, ValueError)``: the None is handled."""
        caught: set[str | None] = set()
        for h in node.handlers:
            t = h.type
            for n in [t] if t is None or not isinstance(t, ast.Tuple) else list(t.elts):
                caught.add(None if n is None else getattr(n, "id", None))
        if caught & {None, "TypeError", "Exception", "BaseException"}:
            saved, saved_f = set(self.from_get), set(self.from_field)
            self.in_try += 1
            self.from_get.clear()
            self.from_field.clear()
            for stmt in node.body:
                self.visit(stmt)
            self.from_get, self.from_field, self.in_try = saved, saved_f, self.in_try - 1
        else:
            for stmt in node.body:
                self.visit(stmt)
        for h in node.handlers:
            self.visit(h)
        for stmt in node.orelse + node.finalbody:
            self.visit(stmt)

    def visit_If(self, node: ast.If) -> None:
        yes, no = self._guards(node.test)
        self.visit(node.test)
        self._with_cleared(yes, list(node.body))
        self._with_cleared(no, list(node.orelse))
        if ends(node.body):
            # ``if x is None: return``: what follows runs only when the test was false
            self.from_get -= no
            self.from_field -= no
        elif node.orelse and ends(node.orelse):
            self.from_get -= yes
            self.from_field -= yes

    def visit_IfExp(self, node: ast.IfExp) -> None:
        yes, no = self._guards(node.test)
        self.visit(node.test)
        self._with_cleared(yes, [node.body])
        self._with_cleared(no, [node.orelse])

    def visit_BoolOp(self, node: ast.BoolOp) -> None:
        # ``a and b``: b runs only when a is true; ``a or b``: only when a is false
        covered: set[str] = set()
        side = 0 if isinstance(node.op, ast.And) else 1
        for v in node.values:
            self._with_cleared(covered, [v])
            covered |= self._guards(v)[side]

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        return  # a nested function is its own Function

    visit_AsyncFunctionDef = visit_FunctionDef  # type: ignore[assignment]


def ends(body: list[ast.stmt]) -> bool:
    """A block that never falls through: it ends in return, raise, continue or break."""
    return bool(body) and isinstance(body[-1], (ast.Return, ast.Raise, ast.Continue, ast.Break))


def is_optional(ann: ast.AST) -> bool:
    """``X | None``, ``Optional[X]``, ``None | X``."""
    if isinstance(ann, ast.BinOp) and isinstance(ann.op, ast.BitOr):
        return (
            any(isinstance(s, ast.Constant) and s.value is None for s in (ann.left, ann.right))
            or is_optional(ann.left)
            or is_optional(ann.right)
        )
    if (
        isinstance(ann, ast.Subscript)
        and isinstance(ann.value, ast.Name)
        and ann.value.id == "Optional"
    ):
        return True
    if isinstance(ann, ast.Constant) and isinstance(ann.value, str):
        try:
            return is_optional(ast.parse(ann.value, mode="eval").body)
        except SyntaxError:
            return False
    return False


def optional_fields(trees: list[ast.Module]) -> set[str]:
    """The annotated class fields declared ``| None`` in every class that declares them: a name
    one class has as ``float`` and another as ``float | None`` is left out, since the lint cannot
    tell which class a value came from. The contract types' exact names make it precise."""
    optional: set[str] = set()
    plain: set[str] = set()
    for tree in trees:
        for cls in ast.walk(tree):
            if isinstance(cls, ast.ClassDef):
                for stmt in cls.body:
                    if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
                        (optional if is_optional(stmt.annotation) else plain).add(stmt.target.id)
    return optional - plain


class Registry:
    """The columns and settings keys with a contract type, read from the contract modules'
    source (``COLUMN = ("table", "column")``, ``SETTING = "key"``)."""

    def __init__(self, trees: list[ast.Module]) -> None:
        self.columns: set[str] = set()
        self.settings: set[str] = set()
        for tree in trees:
            for cls in ast.walk(tree):
                if not isinstance(cls, ast.ClassDef):
                    continue
                for stmt in cls.body:
                    if not (
                        isinstance(stmt, ast.Assign)
                        and len(stmt.targets) == 1
                        and isinstance(stmt.targets[0], ast.Name)
                    ):
                        continue
                    name, value = stmt.targets[0].id, stmt.value
                    if (
                        name == "COLUMN"
                        and isinstance(value, ast.Tuple)
                        and len(value.elts) == 2
                        and all(isinstance(e, ast.Constant) for e in value.elts)
                    ):
                        self.columns.add(str(value.elts[1].value))  # type: ignore[attr-defined]
                    if (
                        name == "SETTING"
                        and isinstance(value, ast.Constant)
                        and isinstance(value.value, str)
                    ):
                        self.settings.add(value.value)

    def setting(self, key: str, partial: bool = False) -> bool:
        """Whether ``key`` is a typed settings key (a pattern's ``*`` matches any part); with
        ``partial``, ``key`` is only an f-string's constant start (``f"money.broker.{a}"``)."""
        for s in self.settings:
            if fnmatch.fnmatchcase(key, s):
                return True
            fixed = s.split("*")[0]
            if partial and key and (key.startswith(fixed) or fixed.startswith(key)) and "*" in s:
                return True
        return False


def is_contract_module(path: Path) -> bool:
    parts = path.parts
    return "stored" in parts[:-1] or path.name == "stored.py"


class Module(ast.NodeVisitor):
    def __init__(self, optional: set[str], registry: Registry, contract: bool) -> None:
        self.optional = optional
        self.registry = registry
        self.contract = contract
        self.out: list[Finding] = []

    def visit_FunctionDef(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        f = Function(self.optional)
        for stmt in node.body:
            f.visit(stmt)
        self.out.extend(f.out)
        self._attr_formats(node, set())
        self.generic_visit(node)  # nested functions and classes, each on its own

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Call(self, node: ast.Call) -> None:
        if not self.contract:
            self._json_outside(node)
        self.generic_visit(node)

    def visit_Dict(self, node: ast.Dict) -> None:
        if not self.contract:
            for k, v in zip(node.keys, node.values, strict=True):
                if (
                    isinstance(k, ast.Constant)
                    and k.value in self.registry.columns
                    and _is_dumps(v)
                ):
                    self.out.append(
                        (
                            node.lineno,
                            "JSON-OUTSIDE",
                            f"{k.value!r} stored with json.dumps: store its contract "
                            "type's to_json()",
                        )
                    )
        self.generic_visit(node)

    def _json_outside(self, node: ast.Call) -> None:
        f = node.func
        if isinstance(f, ast.Attribute) and f.attr == "loads" and node.args:
            col = _column_read(node.args[0])
            if col and col in self.registry.columns:
                self.out.append(
                    (
                        node.lineno,
                        "JSON-OUTSIDE",
                        f"{col!r} loaded with json.loads: read it through its contract "
                        "type (the package's stored/)",
                    )
                )
        if (
            isinstance(f, ast.Attribute)
            and f.attr == "set_setting"
            and len(node.args) >= 2
            and _is_dumps(node.args[1])
        ):
            key = _text_start(node.args[0])
            partial = isinstance(node.args[0], ast.JoinedStr)
            if key is not None and self.registry.setting(key, partial):
                self.out.append(
                    (
                        node.lineno,
                        "JSON-OUTSIDE",
                        f"setting {key!r} stored with json.dumps: use its contract "
                        "type (stored.settings)",
                    )
                )

    # ATTR-FORMAT: a field a contract declares ``| None``, formatted as a number with no guard
    def _attr_formats(self, node: ast.AST, covered: set[str]) -> None:
        if isinstance(node, (ast.If, ast.IfExp)):
            yes, no = self._attr_guards(node.test)
            self._attr_formats(node.test, covered)
            body = node.body if isinstance(node, ast.If) else [node.body]
            orelse = node.orelse if isinstance(node, ast.If) else [node.orelse]
            for b in body:
                self._attr_formats(b, covered | yes)
            for b in orelse:
                self._attr_formats(b, covered | no)
            return
        if isinstance(node, ast.BoolOp):
            seen = set(covered)
            side = 0 if isinstance(node.op, ast.And) else 1
            for v in node.values:
                self._attr_formats(v, seen)
                seen |= self._attr_guards(v)[side]
            return
        if isinstance(node, ast.JoinedStr):
            for v in node.values:
                if isinstance(v, ast.FormattedValue) and numeric_spec(v):
                    name = self._field(v.value)
                    if name and name not in covered:
                        self.out.append(
                            (
                                node.lineno,
                                "ATTR-FORMAT",
                                f"{name}, declared `| None`, formatted as a number",
                            )
                        )
        for _, value in ast.iter_fields(node):
            if isinstance(value, list) and value and isinstance(value[0], ast.stmt):
                seen = set(covered)  # a block: an early return guards what follows
                for stmt in value:
                    if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        continue  # visited as its own function
                    self._attr_formats(stmt, seen)
                    if isinstance(stmt, ast.If):
                        yes, no = self._attr_guards(stmt.test)
                        if ends(stmt.body):
                            seen |= no
                        elif stmt.orelse and ends(stmt.orelse):
                            seen |= yes
                continue
            for child in value if isinstance(value, list) else [value]:
                if isinstance(child, ast.AST) and not isinstance(
                    child, (ast.FunctionDef, ast.AsyncFunctionDef)
                ):
                    self._attr_formats(child, covered)

    def _attr_guards(self, test: ast.AST) -> tuple[set[str], set[str]]:
        """Field names covered when ``test`` is true, and when it is false."""
        yes: set[str] = set()
        no: set[str] = set()
        if isinstance(test, (ast.Attribute, ast.Subscript)):
            n = self._field(test)
            if n:
                yes.add(n)
        elif isinstance(test, ast.Compare):
            n = self._field(test.left)
            if n and any(isinstance(op, ast.IsNot) for op in test.ops):
                yes.add(n)
            elif n and any(isinstance(op, ast.Is) for op in test.ops):
                no.add(n)
            elif n:
                yes.add(n)  # ``x.cap > 0``: a None would have raised here
        elif (
            isinstance(test, ast.Call)
            and isinstance(test.func, ast.Name)
            and test.func.id == "isinstance"
            and test.args
        ):
            n = self._field(test.args[0])
            if n:
                yes.add(n)
        elif isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not):
            no, yes = self._attr_guards(test.operand)
        elif isinstance(test, ast.BoolOp) and isinstance(test.op, ast.And):
            for v in test.values:
                yes |= self._attr_guards(v)[0]
        elif isinstance(test, ast.BoolOp) and isinstance(test.op, ast.Or):
            for v in test.values:
                no |= self._attr_guards(v)[1]
        return yes, no

    def _field(self, node: ast.AST) -> str | None:
        if isinstance(node, ast.Attribute) and node.attr in self.optional:
            return node.attr
        if (
            isinstance(node, ast.Subscript)
            and isinstance(node.slice, ast.Constant)
            and node.slice.value in self.optional
        ):
            return str(node.slice.value)
        return None


def _is_dumps(node: ast.AST) -> bool:
    return isinstance(node, ast.Call) and (
        (isinstance(node.func, ast.Attribute) and node.func.attr == "dumps")
        or (isinstance(node.func, ast.Name) and node.func.id == "dumps")
    )


def _column_read(node: ast.AST) -> str | None:
    """The column name in ``row["col"]``, ``row.get("col")``, ``row["col"] or "{}"``."""
    if isinstance(node, ast.BoolOp):
        node = node.values[0]
    if (
        isinstance(node, ast.Subscript)
        and isinstance(node.slice, ast.Constant)
        and isinstance(node.slice.value, str)
    ):
        return node.slice.value
    if _is_get(node):
        assert isinstance(node, ast.Call)
        if (
            node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            return node.args[0].value
    return None


def _text_start(node: ast.AST) -> str | None:
    """A settings key's text, or an f-string's constant start (``f"money.tools_approved.{a}"``)."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr) and node.values and isinstance(node.values[0], ast.Constant):
        return str(node.values[0].value)
    return None


# -- running --


def files_under(paths: list[Path]) -> list[Path]:
    out = []
    for root in paths:
        for f in [root] if root.is_file() else sorted(root.rglob("*.py")):
            if not any(part in SKIP_PARTS for part in f.parts):
                out.append(f)
    return out


def lint_files(
    files: list[Path], context: list[Path] | None = None
) -> list[tuple[Path, int, str, str, str]]:
    """(path, line, rule, words, the line's text) for every finding in ``files`` not dismissed
    on its line. ``context``: the files whose classes say which fields are ``| None`` and whose
    contracts say which columns are typed (the whole package; ``files`` when None)."""
    trees = {f: ast.parse(f.read_text(errors="replace"), filename=str(f)) for f in files}
    ctx = dict(trees)
    for f in context or []:
        if f not in ctx:
            ctx[f] = ast.parse(f.read_text(errors="replace"), filename=str(f))
    optional = optional_fields(list(ctx.values()))
    registry = Registry([t for f, t in ctx.items() if is_contract_module(f)])
    out = []
    for f, tree in trees.items():
        lines = f.read_text(errors="replace").splitlines()
        m = Module(optional, registry, is_contract_module(f))
        m.visit(tree)
        for line, rule, words in sorted(set(m.out)):
            text = lines[line - 1] if 0 < line <= len(lines) else ""
            if DISMISS in text:
                continue
            out.append((f, line, rule, words, text))
    return out


def key_of(path: Path, rule: str, text: str) -> str:
    h = hashlib.sha256(text.strip().encode()).hexdigest()[:10]
    return f"{path}:{rule}:{h}"


def rel(p: Path) -> Path:
    try:
        return p.resolve().relative_to(ROOT)
    except ValueError:
        return p


def load_baseline(path: Path = BASELINE) -> Counter[str]:
    if not path.exists():
        return Counter()
    return Counter(
        ln.strip() for ln in path.read_text().splitlines() if ln.strip() and not ln.startswith("#")
    )


def write_baseline(keys: list[str], path: Path = BASELINE) -> None:
    head = (
        "# stored_shape_lint.py's findings that were already there (path:RULE:line hash).\n"
        "# A new finding fails the check; so does a line here that matches nothing. Each\n"
        "# column's contract (the package's stored/) takes its lines out.\n"
    )
    path.write_text(head + "".join(k + "\n" for k in sorted(keys)))


def dismissed(files: list[Path]) -> int:
    return sum(f.read_text(errors="replace").count(DISMISS) for f in files)


def main(argv: list[str]) -> int:
    args = [a for a in argv if not a.startswith("--")]
    whole = files_under([ROOT / p for p in DEFAULT_PATHS])
    files = files_under([Path(a) for a in args]) if args else whole
    findings = lint_files(files, whole)
    keys = [key_of(rel(f), rule, text) for f, _, rule, _, text in findings]
    if "--update" in argv:
        if args:
            print("stored-shape: --update writes the whole baseline: give no paths")
            return 2
        write_baseline(keys)
        print(f"stored-shape: baseline written: {len(keys)} findings")
        return 0
    linted = {str(rel(f)) for f in files}
    base = Counter({k: n for k, n in load_baseline().items() if k.rsplit(":", 2)[0] in linted})
    now = Counter(keys)
    new = now - base
    stale = base - now
    shown = 0
    for (f, line, rule, words, _), k in zip(findings, keys, strict=True):
        if "--all" in argv or new[k] > 0:
            if "--all" not in argv:
                new[k] -= 1
            print(f"{rel(f)}:{line}: {rule}: {words}")
            shown += 1
    for k in sorted(stale.elements()):
        print(f"stale baseline line (fixed? take it out): {k}")
    n_new = sum((now - base).values())
    print(
        f"stored-shape: {len(findings)} findings, {sum(base.values())} in the baseline, "
        f"{n_new} new, {sum(stale.values())} stale; {dismissed(files)} dismissed with "
        f"'{DISMISS} <why>'"
    )
    if n_new or stale:
        print(
            "stored-shape: FAILED. A new finding: read the value through its contract type "
            "(the package's stored/), guard it, or dismiss it on its line with "
            f"'{DISMISS} <why>'. A stale line: run `python scripts/ci/stored_shape_lint.py "
            "--update` and commit the baseline."
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
