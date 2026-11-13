"""Folders a session could write keep their links to themselves: after a session, a link
that is absolute or leads out of its folder is gone before the host reads the folder, every
copy keeps links as links, and the checks tarball holds regular files and folders only.
Offline: the "session" is a stand-in harness, the outside file a canary made here."""

from __future__ import annotations

import os
import tarfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from repo_factory.harness import links, profile
from repo_factory.harness.base import HarnessResult
from repo_factory.harness.launch import Launch
from repo_factory.v5 import exam, readiness, verify

from .test_profile import init, owner_config, transcript

CANARY = "canary-outside-the-folder"


def canary(tmp_path: Path) -> Path:
    f = tmp_path / "outside" / "kept.txt"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(CANARY + "\n")
    return f


def plant(folder: Path, target: Path, tag: str = "") -> None:
    """What a session can leave: an absolute link, a relative one that climbs out, a link to
    a folder outside, and one that stays inside (kept)."""
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"inside{tag}.txt").write_text("stays\n")
    (folder / f"abs{tag}.txt").symlink_to(target)
    (folder / f"climb{tag}.txt").symlink_to(os.path.relpath(target, folder))
    (folder / f"dir-out{tag}").symlink_to(target.parent, target_is_directory=True)
    (folder / f"ok{tag}.txt").symlink_to(f"inside{tag}.txt")


def every_text(root: Path) -> str:
    out = []
    for p in root.rglob("*"):
        if p.is_file():
            try:
                out.append(p.read_text(errors="replace"))
            except OSError:
                pass
    return "\n".join(out)


# -- the module --------------------------------------------------------------------------
def test_scrub_removes_links_that_leave_and_keeps_the_rest(tmp_path):
    out = canary(tmp_path)
    d = tmp_path / "ws"
    plant(d, out)
    (d / "sub").mkdir()
    (d / "sub" / "up.txt").symlink_to("../inside.txt")  # inside: kept
    (d / "sub" / "chain").symlink_to("../dir-out")  # through a link that leaves
    os.mkfifo(d / "pipe")
    removed = links.scrub(d)
    # sub/chain goes too, or is left dangling inside once dir-out is gone (either is safe)
    assert set(removed) - {"sub/chain"} == {"abs.txt", "climb.txt", "dir-out", "pipe"}
    assert not (d / "sub" / "chain").exists()
    assert (d / "ok.txt").is_symlink() and (d / "sub" / "up.txt").read_text() == "stays\n"
    assert CANARY not in every_text(d)


def test_scrub_of_a_folder_that_is_itself_a_link(tmp_path):
    out = canary(tmp_path)
    d = tmp_path / "exam"
    d.symlink_to(out.parent, target_is_directory=True)
    assert links.scrub(d) == ["."] and not d.exists() and out.exists()
    assert links.scrub(tmp_path / "missing") == [] and links.scrub(None) == []


def test_copytree_keeps_links_as_links_and_leaves_out_what_leaves(tmp_path):
    out = canary(tmp_path)
    src = tmp_path / "src"
    plant(src, out)
    dst = links.copytree(src, tmp_path / "dst")
    assert sorted(p.name for p in dst.iterdir()) == ["inside.txt", "ok.txt"]
    assert (dst / "ok.txt").is_symlink() and os.readlink(dst / "ok.txt") == "inside.txt"
    with pytest.raises(links.UnsafeFolder):
        links.copytree(src / "dir-out", tmp_path / "dst2")


def test_copy_file_refuses_a_link(tmp_path):
    out = canary(tmp_path)
    (tmp_path / "l").symlink_to(out)
    assert links.copy_file(tmp_path / "l", tmp_path / "c") is False
    assert not (tmp_path / "c").exists()


# -- after a session ---------------------------------------------------------------------
class Planter:
    """A session stand-in that leaves links to a file outside in every folder it can write."""

    def __init__(self, target: Path):
        self.target = target

    def run(self, prompt, workspace, timeout_s, **opts):
        spec = opts.get("launch")
        for m in spec.mounts if spec else []:
            plant(Path(m["src"]), self.target)
        plant(Path(workspace), self.target)
        plant(Path(opts["config_dir"]) / "projects" / "-work-product", self.target)
        t = Path(workspace) / "transcript.jsonl"
        transcript(t, init())
        return HarnessResult(
            artifact_dir=workspace,
            tool_calls=1,
            input_tokens=1,
            output_tokens=1,
            cost_usd=0.1,
            transcript_path=t,
            exit_ok=True,
            session_id=opts["session"]["id"],
            session_cost_usd=0.1,
        )


def test_after_a_session_no_link_out_is_left_in_any_folder_it_could_write(tmp_path, monkeypatch):
    monkeypatch.delenv("REPO_FACTORY_WORKER_NET", raising=False)
    out = canary(tmp_path)
    ws, ex, props, cfg = (tmp_path / n for n in ("ws", "exam", "proposals", "cfg"))
    ws.mkdir()
    spec = Launch(
        mounts=exam.session_mounts(ex, True, props) if ex.mkdir() is None else [],
        workdir="/work/product",
    )
    r = profile.launch(
        "go",
        ws,
        config_dir=cfg,
        max_turns=3,
        max_usd=1.0,
        timeout_s=30,
        harness=Planter(out),
        source=owner_config(tmp_path),
        transcript=tmp_path / "t.jsonl",
        spec=spec,
    )
    for d in (ws, ex, props, cfg):
        left = [p for p in d.rglob("*") if p.is_symlink() and not links.keep(str(p), str(d))]
        assert left == [], d
        assert CANARY not in every_text(d), d
        assert (d if d != cfg else cfg / "projects" / "-work-product").joinpath("ok.txt").exists()
    assert set(r.scrubbed) == {str(ws), str(ex.resolve()), str(props.resolve()), str(cfg)}


# -- every copy, even of a folder no session was scrubbed from ----------------------------
def test_a_link_out_reaches_no_copy_the_host_makes(tmp_path):
    """The verifier's exam folder holding links out (as if no scrub had run): its content
    reaches none of the helpers, the exam every later session gets, a readiness session's
    exam, the evidence and the checks tarball."""
    out = canary(tmp_path)
    vex = tmp_path / "round" / "verify-exam"
    (vex / "_verify" / "scenarios").mkdir(parents=True)
    (vex / "_verify" / "driver.py").write_text("# the driver\n")
    plant(vex / "_verify", out)

    helpers = verify.helpers_dir(vex, tmp_path / "round" / "helpers")
    assert CANARY not in every_text(helpers)
    assert (helpers / "driver.py").is_file() and (helpers / "ok.txt").is_symlink()

    plant(helpers, out, "2")  # and again in the helpers themselves
    root = tmp_path / "round"
    exam.write_exam(root / "exam", [], helpers)
    assert CANARY not in every_text(root / "exam")

    built = tmp_path / "round" / "build"
    plant(built, out)
    run = SimpleNamespace(
        root=root,
        ws=built,
        helpers=helpers,
        spec="the spec",
        finished_session=lambda *a: None,
    )
    ws, ex = readiness.prepare(run, "probe", "probe")
    assert CANARY not in every_text(ws) and CANARY not in every_text(ex)

    plant(root / "exam" / "_verify", out, "3")
    ev = exam.evidence(run, {"tree": "t"}, {"readiness.md": "ok"}, "product")
    assert CANARY not in every_text(ev)

    plant(ev, out, "4")  # the tarball refuses links even when they are there
    tar = exam.evidence_tarball(ev, tmp_path / "product-0.1.0-checks.tar.gz", "product-0.1.0")
    with tarfile.open(tar) as t:
        members = t.getmembers()
        assert all(m.isreg() or m.isdir() for m in members)
        data = b"".join(t.extractfile(m).read() for m in members if m.isreg())
    assert CANARY.encode() not in data
