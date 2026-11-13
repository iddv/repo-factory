"""Publish: a ready build becomes a **private** GitHub repo with a v0.1.0 release.

Steps (``publish``), each recorded in the idea's ``publish`` field:
1. stage the delivered branch (``git archive``) under ``<home>/publish/<id>/<name>``,
   without the factory's own files (REPORT.md, bench-meta.json) and build output (``build/``,
   ``*.egg-info``, caches: ``tree.junk``, recorded as ``tidied``). The tree is the product's,
   as the build agent wrote it: repo-factory inserts no landing section and no document;
2. a ``LICENSE`` (``publish.license``, MIT by default) and a short, fixed ``NOTICE``
   (``notice``: an autonomous build, where the checks are published, the sources);
3. the machine-read maintenance policy (``publish.maintenance_files``: ``policy``, the
   default, writes ``.github/repo-factory.yml`` and ``.github/labels.yml``; ``all``: today's whole
   set; ``none``): CI, AGENTS.md and the rest are the product's;
4. the public text, written last from the product (``describe.py``: the README's
   description, AGENTS.md's what-this-is and the release text, from the code, the recorded
   help, the tests and the checks; never the pitch's words; the owner's release text as it
   is), then one review pass for overclaims whose replacements are applied and listed. Wording
   is never a hold; NOTICE is repo-factory's fixed text, never judged here;
5. the pre-push scan: ``secrets.scan``: a hit stops here and nothing is pushed. The provenance scan (``v5/jargon.py``: the ids this build
   minted and repo-factory's own words in the product's prose) feeds the review and is listed;
6. ``gh repo create --private --push`` (the policy files are in its first commit), the label
   scheme and repo settings (``maintenance_github``), then ``gh release create v0.1.0`` with
   a source tarball and the checks asset (``<name>-0.1.0-checks.tar.gz``: the round's
   evidence, ``exam.evidence_asset``).

**Nothing goes public without approval**: ``approve`` flips the
repo to public, and only ``repo-factory loop approve <id>`` or the UI's button call it, unless the
owner set ``publish.approval: auto``."""

from __future__ import annotations

import re
import time
from pathlib import Path

from .. import gitcmd
from ..gh import TOKEN_KEYS, Gh, PublishError, token_env  # noqa: F401
from .tree import DROP, export, git, readme_path  # noqa: F401

VERSION = "0.1.0"

MIT = """MIT License

Copyright (c) {year} {holder}

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""

APACHE = """Copyright {year} {holder}

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
"""


def repo_name(product: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (product or "product").lower()).strip("-")
    return s[:60] or "product"


def free_name(gh: Gh, owner: str, base: str) -> str:
    name, n = base, 2
    while gh.exists(f"{owner}/{name}"):
        name, n = f"{base}-{n}", n + 1
    return name


# -- staging -----------------------------------------------------------------------------------
def landing(p: dict, full: str, name: str, cmds: list[str]) -> str:
    lines = [
        "## At a glance",
        "",
        f"- **What it is:** {p.get('one_liner') or p.get('product')}",
        f"- **Who it's for:** {p.get('who')}",
        "- **Quick start:**",
        "",
        "  ```sh",
        f"  git clone https://github.com/{full}.git",
        f"  cd {name}",
    ]
    lines += [f"  {c}" for c in cmds] or ["  # then follow the install section below"]
    lines += [
        "  ```",
        "",
        "The rest of this README covers configuration, data, backup and the full guide.",
        "",
    ]
    return "\n".join(lines)


def add_landing(readme: Path, text: str) -> None:
    """After the README's title and its tagline paragraph (if any); at the top without a
    title."""
    lines = (readme.read_text(errors="replace") if readme.exists() else "").splitlines()
    at = next((i for i, ln in enumerate(lines) if ln.startswith("# ")), None)
    if at is None:
        cut = 0
    else:
        cut = at + 1
        while cut < len(lines) and not lines[cut].strip():
            cut += 1
        if cut < len(lines) and not lines[cut].startswith(("#", "```", "|", "- ", "* ")):
            while cut < len(lines) and lines[cut].strip():
                cut += 1  # the tagline paragraph
    head, tail = lines[:cut], lines[cut:]
    while tail and not tail[0].strip():
        tail = tail[1:]
    out = (
        (("\n".join(head).rstrip() + "\n\n") if head else "")
        + text.rstrip()
        + "\n\n"
        + "\n".join(tail)
    )
    readme.write_text(out.rstrip() + "\n")


def override_line(override: dict | None) -> str | None:
    """The disclosure of a release published over a hold (Publish anyway): which checks did
    not pass, in the gate's words."""
    if not override:
        return None
    det = override.get("details") or {}
    return (
        "Released by its maintainer although these checks did not pass: "
        + "; ".join(f"{k} ({det[k]})" if det.get(k) else k for k in override.get("overrides") or [])
        + "."
    )


def gaps_line(gate: dict | None) -> str | None:
    """The release evidence's statement of what the checks could not measure (a gap of
    repo-factory's own, never a hold), from the gate's checks; None when nothing is missing."""
    gs = [
        c["gap"]
        for c in ((gate or {}).get("checks") or {}).values()
        if isinstance(c, dict) and c.get("gap")
    ]
    return ("Not every check could run before this release: " + "; ".join(gs) + ".") if gs else None


def _source(e: dict) -> str:
    return f"- {e.get('url')} ({e.get('source') or 'source'}, {(e.get('observed_at') or '')[:10]})"


def checks_asset(name: str, version: str = VERSION) -> str:
    """The release asset holding the checks that ran before it."""
    return f"{name}-{version}-checks.tar.gz"


def notice(
    product: str,
    idea: dict,
    year: int,
    holder: str,
    override: dict | None = None,
    asset: str | None = None,
) -> str:
    """The NOTICE, with no model call: the one text repo-factory writes into a product, fixed
    and covered by a fixture test, never re-judged at publish. Attribution claims only what
    the pitch judged relevant: the evidence lines it named in ``evidence_used`` (E1…En)
    "show the problem"; every other source, and every source when the pitch named none, is
    background that shows no demand. ``asset``: the checks asset's file name (None: the
    checks stay with the build, ``exam.evidence_asset: false``)."""
    card = idea.get("card") or {}
    evidence = card.get("evidence") or []
    used = {str(x).strip().upper() for x in (idea.get("pitch") or {}).get("evidence_used") or []}
    shown = [e for n, e in enumerate(evidence, 1) if e.get("url") and f"E{n}" in used]
    rest = [e for n, e in enumerate(evidence, 1) if e.get("url") and f"E{n}" not in used]
    known = {e.get("url") for e in shown + rest}
    links = [u for u in idea.get("urls") or [] if u not in known]
    lines = [
        f"{product} · Copyright {year} {holder}. See LICENSE.",
        "",
        "This software was generated by an autonomous software build from a short "
        "product brief. The checks that ran before each release (its own tests, an "
        "end-to-end walk of every documented flow, a security review and an operational "
        "readiness review) are "
        + (
            f"published with the release as `{asset}`; read them before you rely on it."
            if asset
            else "kept with the build that made it; ask its maintainer for them "
            "before you rely on it."
        ),
        "",
    ]
    if shown:
        lines += [
            "These sources show the problem this software addresses:",
            "",
            *[_source(e) for e in shown],
            "",
            "None of them made, reviewed or endorsed it.",
            "",
        ]
    if rest or links:
        lines += [
            "The idea was gathered from these sources as background; they do not show "
            "demand for this software:",
            "",
            *[_source(e) for e in rest],
            *[f"- {u}" for u in links],
            "",
            "None of them made, reviewed or endorsed it.",
            "",
        ]
    if not (shown or rest or links):
        lines += [
            "The idea came from the maintainer's own notes; no outside source made, "
            "reviewed or endorsed this software.",
            "",
        ]
    said = override_line(override)
    if said:
        lines += [said, ""]
    return "\n".join(lines).rstrip("\n") + "\n"


def license_text(kind: str, holder: str, year: int) -> str | None:
    k = (kind or "MIT").lower()
    if k in ("none", "no", ""):
        return None
    if k.startswith("apache"):
        return APACHE.format(year=year, holder=holder)
    return MIT.format(year=year, holder=holder)


def holder_name(cfg) -> str:
    if cfg["publish"].get("copyright"):
        return cfg["publish"]["copyright"]
    p = gitcmd.run(["config", "user.name"], capture_output=True, text=True)
    return p.stdout.strip() or "the authors"


def prepare(
    idea: dict, cfg, model, gh_owner: str, name: str, stage_root: Path, override: dict | None = None
) -> dict:
    """Stage the tree and run the checks; nothing leaves the machine. ``{"stage",
    "blocked": [reasons], "described", "secrets", "jargon", "usd"}``. ``override``: published
    over a hold (Publish anyway): the NOTICE and the release notes say which checks did not
    pass. ``stage`` (the model's half, run before anyone is asked: ``precheck``), then
    ``finish`` (no model)."""
    return finish(stage(idea, cfg, model, gh_owner, name, stage_root), cfg, override)


def stage(
    idea: dict, cfg, model, gh_owner: str, name: str, stage_root: Path, ref: str | None = None
) -> dict:
    """The tree as it would be pushed, staged under ``stage_root/name`` (LICENSE, NOTICE, the
    maintenance files), with its public text written from the product and reviewed once
    (``describe.run``): the pre-push step that calls a model, so it runs in the background
    (``precheck``), never in a request. ``ref``: the commit to stage (default: the delivered
    branch as it is now). ``{"stage", "repo", "described", "release_text", "tidied", "usd",
    "maintenance"}``; ``finish`` scans it and says what blocks."""
    g = idea["gate"]
    stage = stage_root / name
    tidied = export(Path(g["out"]), ref or g["branch"], stage)  # build output left out
    p = idea["pitch"]
    full = f"{gh_owner}/{name}"
    year = time.localtime().tm_year
    rdir = Path(idea["task_dir"]) / ".den" / "v5" / f"r{g.get('round')}"
    ev = rdir / "exam" / "evidence"
    asset = (
        checks_asset(name)
        if (cfg.get("exam") or {}).get("evidence_asset", True) and ev.is_dir()
        else None
    )
    holder = holder_name(cfg)
    lic = license_text(cfg["publish"].get("license"), holder, year)
    if lic:
        (stage / "LICENSE").write_text(lic)
    (stage / "NOTICE").write_text(notice(p.get("product") or name, idea, year, holder, None, asset))
    which = maintenance_which(cfg)
    maint = maintenance_files(stage, full, which) if which != "none" else None
    # the public text is written last, from the product
    # (never the pitch), then reviewed once; the review's replacements are applied and
    # listed. Wording is never a hold (describe.py)
    from ..v5 import jargon
    from . import describe

    rd = rdir if rdir.is_dir() else None
    d = describe.run(
        stage,
        idea,
        model,
        rd,
        Path(idea["task_dir"]) if idea.get("task_dir") else None,
        p.get("product") or name,
        jargon.scan_round(rd, stage),
    )
    # ``honesty`` keeps its shape for the readers of older records: never an issue now
    hon = {"ok": True, "issues": [], "fixed": d["reworded"], "usd": d["usd"]}
    return {
        "stage": str(stage),
        "repo": full,
        "honesty": hon,
        "tidied": tidied,
        "release_text": d["release_text"],
        "described": d,
        "usd": d["usd"],
        "maintenance": maint,
        "round_dir": str(rdir),
        "evidence": str(ev) if asset else None,
        "asset": asset,
    }


OVERRIDE_LINE = "Released by its maintainer although"


def set_override(path: Path, override: dict | None) -> None:
    """NOTICE's last paragraph: the override's disclosure (``override_line``), or none."""
    text = path.read_text(errors="replace") if path.exists() else ""
    lines = [ln for ln in text.rstrip("\n").split("\n") if not ln.startswith(OVERRIDE_LINE)]
    while lines and not lines[-1].strip():
        lines.pop()
    said = override_line(override)
    path.write_text("\n".join(lines) + ("\n\n" + said if said else "") + "\n")


def finish(prep: dict, cfg, override: dict | None = None) -> dict:
    """The deterministic half, run again at the press: the override's disclosure in NOTICE,
    then the scans of the staged tree (secrets, jargon) and what blocks the push: only facts
    (a secret), never wording. No model call."""
    stage = Path(prep["stage"])
    set_override(stage / "NOTICE", override)
    from ..v5 import jargon
    from . import secrets

    # every scan runs again here, with the installed repo-factory: they take under a second
    sec = secrets.scan(stage)
    rdir = Path(prep["round_dir"]) if prep.get("round_dir") else None
    jar = jargon.scan_round(rdir if rdir is not None and rdir.is_dir() else None, stage)
    # who wrote each hit's file: the scan reads the product's prose, so a hit is
    # the product's unless repo-factory staged that file itself (publish.maintenance_files: all)
    from .claims import author

    mine = (prep.get("maintenance") or {}).get("files") or []
    jar = [{**h, "author": h.get("author") or author(h.get("file"), mine)} for h in jar]
    blocked = []
    if sec:
        blocked.append(
            "secrets scan: " + "; ".join(f"{h['file']}:{h['line']} {h['kind']}" for h in sec[:10])
        )
    # wording is never a hold: the review reworded what the jargon
    # scan found in README and AGENTS.md; the hits left are listed on the build page
    return {**prep, "blocked": blocked, "secrets": sec, "jargon": jar, "override": override}


# -- maintenance from the first commit -----------------------------------------------------------
UNDESCRIBED = "Not described yet: read the module itself (it has no docstring)."


MAINTENANCE_FILES = ("policy", "all", "none")


def maintenance_which(cfg) -> str:
    """``publish.maintenance_files`` (EX-PLAN Q5): ``policy`` (default), ``all`` or ``none``."""
    v = str(cfg["publish"].get("maintenance_files") or "policy")
    return v if v in MAINTENANCE_FILES else "policy"


def maintenance_files(stage: Path, full: str, which: str = "policy") -> dict:
    """The maintenance files written into the staged tree so they are in its first commit.
    ``policy`` (the default): ``.github/repo-factory.yml`` (``maintained``, fixes only on ``go``) and
    the label scheme, both machine-read, never prose; the product's AGENTS.md, CI, templates
    and CHANGELOG are its own (the build agent writes them). A product the build left without
    an AGENTS.md gets one rendered from the template all the same (Beliefline, 2026-10-11: a
    maintained repo without it is a dead end for every fix). ``all``: the whole set as
    before (``AGENTS.md`` from a template, ``CLAUDE.md``, CI, the labels workflow and its
    scripts, Dependabot, the issue forms, the PR template, ``CHANGELOG.md``, a
    ``.gitignore``), rendered from the build's own documents with no model call. ``{"files",
    "doctor"}``: what was written and the drift doctor's findings (whenever repo-factory wrote
    AGENTS.md)."""
    from ..maintain import agentsmd, backfill

    if which == "policy":
        res = backfill.write_files(stage, {}, only=backfill.POLICY_FILES)
        if (stage / "AGENTS.md").is_file():  # the product's own wins
            return {"files": res["written"], "kept": [], "removed": [], "doctor": []}
        values = {**_values(stage, full), "commands_run_by": agentsmd.COMMANDS_RUN_BY_BEARS}
        (stage / "AGENTS.md").write_text(agentsmd.render(values))
        return {
            "files": [*res["written"], "AGENTS.md"],
            "kept": [],
            "removed": [],
            "doctor": agentsmd.check(stage),
            "rendered": "AGENTS.md",
        }
    res = backfill.write_files(stage, _values(stage, full))
    return {
        "files": res["written"],
        "kept": res["kept"],
        "removed": res["removed"],
        "doctor": agentsmd.check(stage),
    }


def _values(stage: Path, full: str) -> dict:
    from ..maintain import agentsmd

    values = agentsmd.values_from_tree(stage, full, lambda mods: {m: UNDESCRIBED for m in mods})
    values.update(repo=full, first_version=VERSION, release_date=time.strftime("%Y-%m-%d"))
    return values


def maintenance_github(gh: Gh, full: str) -> dict:
    """Right after ``gh repo create``: the label scheme (created, never pruned) and the repo
    settings (``delete_branch_on_merge``, Dependabot alerts). A failure is recorded, never
    fatal: the repo is published either way and the backfill command can redo it."""
    from ..maintain import backfill, labels

    out: dict = {"errors": []}
    try:
        lab = labels.apply(gh, full)
        out["labels"] = {k: len(v) for k, v in lab.items() if k != "extra"}
    except PublishError as e:
        out["errors"].append(f"labels: {e}")
    try:
        out["settings"] = backfill.repo_settings(gh, full)
    except Exception as e:  # noqa: BLE001 - recorded, not fatal
        out["errors"].append(f"settings: {type(e).__name__}: {e}")
    return out


def push(prep: dict, idea: dict, gh: Gh) -> dict:
    stage, full = Path(prep["stage"]), prep["repo"]
    name = full.split("/")[1]
    p = idea["pitch"]
    if not (stage / ".git").exists():
        git("init", "-q", "-b", "main", cwd=stage)
    git("add", "-A", cwd=stage)
    if gitcmd.run(["diff", "--cached", "--quiet"], cwd=stage).returncode != 0:
        git("commit", "-q", "-m", f"{p.get('product') or name} {VERSION}", cwd=stage)
    # the owner's release text as it is now (a save after the checks ran needs no new run),
    # else the one the pre-push checks wrote from the product
    from . import claims as cl
    from .describe import notes_line, release

    mine, by = release(idea, None)
    text = mine if by == "owner" else prep.get("release_text") or mine
    desc = " ".join(text.split())[:300]
    gh(
        "repo",
        "create",
        full,
        "--private",
        "--description",
        desc,
        "--source",
        str(stage),
        "--remote",
        "origin",
        "--push",
    )
    maint = maintenance_github(gh, full) if prep.get("maintenance") is not None else None
    tarball = stage.parent / f"{name}-{VERSION}.tar.gz"
    git(
        "archive",
        "--format=tar.gz",
        f"--prefix={name}-{VERSION}/",
        "-o",
        str(tarball),
        "HEAD",
        cwd=stage,
    )
    assets = [str(tarball)]
    notes = cl.release_notes(p.get("product") or name, text)
    if prep.get("evidence") and Path(prep["evidence"]).is_dir():
        from ..v5 import exam

        asset = prep.get("asset") or checks_asset(name)
        checks = exam.evidence_tarball(
            Path(prep["evidence"]),
            stage.parent / asset,
            asset[: -len(".tar.gz")].rsplit("-checks", 1)[0],
        )
        assets.append(str(checks))
        notes += f" The checks that ran before this release are attached: `{asset}`."
    reworded = (prep.get("described") or {}).get("reworded") or []
    if notes_line(reworded):
        notes += "\n\n" + notes_line(reworded)
    gaps = gaps_line(idea.get("gate"))
    if gaps:
        notes += "\n\n" + gaps
    said = override_line(prep.get("override"))
    if said:
        notes += "\n\n" + said
    rel = gh(
        "release",
        "create",
        f"v{VERSION}",
        *assets,
        "--repo",
        full,
        "--title",
        f"{p.get('product') or name} {VERSION}",
        "--notes",
        notes,
    ).strip()
    return {
        "repo": full,
        "url": f"https://github.com/{full}",
        "release_url": rel or f"https://github.com/{full}/releases/tag/v{VERSION}",
        "tarball": str(tarball),
        "assets": [Path(a).name for a in assets],
        "release_text": desc,
        "version": VERSION,
        "visibility": "private",
        "published_at": time.time(),
        **({"maintenance": {**prep["maintenance"], **maint}} if maint is not None else {}),
    }


def update_release_text(
    gh: Gh, full: str, product: str, text: str, tag: str = f"v{VERSION}"
) -> dict:
    """repo-factory's own text on a published repo, reworded (NG-PLAN §5): the description, and the
    first paragraph of the release notes (the checks asset's sentence and any override
    disclosure after it stay as they were). Code, files and visibility are never touched."""
    from . import claims as cl

    desc = " ".join(text.split())[:300]
    gh("repo", "edit", full, "--description", desc)
    body = gh("release", "view", tag, "--repo", full, "--json", "body", "-q", ".body", check=False)
    paras = (body or "").rstrip("\n").split("\n\n")
    head = cl.first_line(product, desc)
    if paras and paras[0].startswith("First release of"):
        paras[0] = head
    else:
        paras = [head, *[x for x in paras if x.strip()]]
    if len(paras) == 1:
        paras.append("Install and run it as the README says.")
    gh("release", "edit", tag, "--repo", full, "--notes", "\n\n".join(paras))
    return {"description": desc, "notes": paras[0], "at": time.time()}


def make_public(gh: Gh, full: str) -> None:
    # The REST call, not ``gh repo edit --visibility``: that needs
    # ``--accept-visibility-change-consequences``, which older gh releases (Ubuntu 24.04's
    # 2.45) don't know, and newer ones refuse to run without.
    gh("api", "-X", "PATCH", f"repos/{full}", "-f", "visibility=public")
