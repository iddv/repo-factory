"""Where a GitHub Actions job may run: on GitHub's own runners, or "cannot tell".

A fix's pull request runs every ``pull_request`` workflow of the default branch on the fix's
code; a merge or a release runs the others. That is fine on GitHub's own hosted runners and not
on a machine of someone's (a self-hosted runner gets the model's code with that machine's
reach). So repo-factory reads each job's ``runs-on`` as an allow-list: a job is hosted only
when every label is one of GitHub's own images, ``ubuntu-*``, ``windows-*`` or ``macos-*``.
Anything else is "cannot tell" and counts as not hosted: ``self-hosted``, any other label, a
runner ``group``, an expression (``${{ vars.RUNNER }}``, ``${{ fromJSON(...) }}``), a job with
no ``runs-on``, and a job that calls a reusable workflow (``uses:``), which may run anywhere.

One expression is read, because model-written CI uses it everywhere: ``runs-on`` that is exactly
``${{ matrix.<key> }}``, when the job's ``strategy.matrix`` is written out and every value that
key can take (its list and each ``include`` entry's) is a plain label. Each of those labels must
then be hosted; any other value (an expression, a missing key) is "cannot tell".

Used by maintenance (no fix starts or is pushed, nothing merges: ``maintain/doctor.py``,
``watch.py``) and by the publish hold (``loop/workflows.py``).
"""

from __future__ import annotations

import re

import yaml

HOSTED = re.compile(r"(?:ubuntu|windows|macos)-[A-Za-z0-9][A-Za-z0-9.\-]*")
MATRIX = re.compile(r"\$\{\{\s*matrix\.([A-Za-z_][A-Za-z0-9_-]*)\s*\}\}")


def _matrix_values(spec: dict, key: str) -> list[str] | None:
    """Every value ``matrix.<key>`` can take in this job, when all are plain labels written
    out (its list and each ``include`` entry's); else None (cannot tell)."""
    strategy = spec.get("strategy")
    matrix = strategy.get("matrix") if isinstance(strategy, dict) else None
    if not isinstance(matrix, dict):
        return None  # missing, or itself an expression
    out: list = []
    listed = matrix.get(key)
    if listed is not None:
        if not isinstance(listed, list):
            return None
        out += listed
    include = matrix.get("include")
    if include is not None:
        if not isinstance(include, list):
            return None
        for entry in include:
            if not isinstance(entry, dict):
                return None
            if key in entry:
                out.append(entry[key])
    if not out or not all(isinstance(x, str) and "${{" not in x for x in out):
        return None
    return out


def _labels(runs_on) -> tuple[list[str], str | None]:
    """``(labels, why)``: the runner labels of ``runs-on``, or why they cannot be told."""
    if isinstance(runs_on, str):
        return [runs_on], None
    if isinstance(runs_on, list) and all(isinstance(x, str) for x in runs_on) and runs_on:
        return list(runs_on), None
    if isinstance(runs_on, dict):
        if runs_on.get("group") is not None:
            return [], f"runs in the runner group {runs_on['group']!s}"
        labels = runs_on.get("labels")
        if isinstance(labels, str):
            return [labels], None
        if isinstance(labels, list) and labels and all(isinstance(x, str) for x in labels):
            return list(labels), None
    if runs_on is None:
        return [], "names no runner"
    return [], "names its runner in a form that cannot be read"


def job(name: str, spec) -> str | None:
    """Why job ``name`` may run off GitHub's own runners, or None when it cannot."""
    if not isinstance(spec, dict):
        return f"job {name} cannot be read"
    if spec.get("uses") is not None:
        return f"job {name} calls a reusable workflow, which may run anywhere"
    labels, why = _labels(spec.get("runs-on"))
    if why:
        return f"job {name} {why}"
    if len(labels) == 1 and (m := MATRIX.fullmatch(labels[0].strip())):
        values = _matrix_values(spec, m.group(1))
        if values is None:
            return f"job {name} chooses its runner by an expression ({labels[0].strip()})"
        labels = values
    for x in labels:
        if "${{" in x:
            return f"job {name} chooses its runner by an expression ({x.strip()})"
        if not HOSTED.fullmatch(x.strip()):
            return f"job {name} runs on {x.strip()!r}, which is not one of GitHub's own runners"
    return None


def not_hosted(text: str) -> list[str]:
    """One line for each job of the workflow ``text`` that may run off GitHub's own runners
    (``[]``: every job runs on them). Text that is not valid YAML is one line too (GitHub's
    reader might take what this one refuses); an empty file, or YAML with no ``jobs`` mapping,
    is no workflow GitHub would run, and runs nowhere."""
    try:
        doc = yaml.safe_load(text or "")
    except yaml.YAMLError:
        return ["it is not valid YAML here, so where it runs cannot be told"]
    jobs = doc.get("jobs") if isinstance(doc, dict) else None
    if not isinstance(jobs, dict):
        return []
    return [w for name, spec in jobs.items() if (w := job(str(name), spec))]
