"""A product's Python packages install inside the sandbox, and the host never starts the
product venv's interpreter. Offline: the packages are made here and installed from a local
folder; the "outside" file is a canary made here."""

from __future__ import annotations

import subprocess
import tarfile
import zipfile
from pathlib import Path

import pytest

from repo_factory.harness import venv as V
from repo_factory.harness.isolation import base_python
from repo_factory.v5 import pyinstall

from .term_common import needs_bwrap


def _run(cmd):
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    return p.returncode, (p.stderr or p.stdout or "").strip()


def empty_venv(at: Path) -> Path:
    rc, err = _run(["uv", "venv", "-q", "--python", base_python(), str(at)])
    assert rc == 0, err
    return at


def source_package(house: Path, report: Path, canary: Path) -> None:
    """A source-only package whose build code writes what it saw into ``report`` (inside the
    venv, which the install may write) and then stops the build."""
    root = "demo_src-0.1"
    backend = (
        "import os\n"
        "def _note():\n"
        f"    with open({str(report)!r}, 'w') as f:\n"
        f"        f.write('ran; outside visible=%s' % os.path.exists({str(canary)!r}))\n"
        "def get_requires_for_build_wheel(config_settings=None):\n"
        "    return []\n"
        "def build_wheel(wheel_directory, config_settings=None, metadata_directory=None):\n"
        "    _note()\n"
        "    raise RuntimeError('stopped on purpose')\n"
        "def prepare_metadata_for_build_wheel(d, config_settings=None):\n"
        "    _note()\n"
        "    raise RuntimeError('stopped on purpose')\n"
    )
    files = {
        f"{root}/pyproject.toml": (
            '[build-system]\nrequires = []\nbuild-backend = "backend"\nbackend-path = ["."]\n'
            '[project]\nname = "demo-src"\nversion = "0.1"\n'
        ),
        f"{root}/backend.py": backend,
        f"{root}/PKG-INFO": "Metadata-Version: 2.1\nName: demo-src\nVersion: 0.1\n",
    }
    house.mkdir(parents=True, exist_ok=True)
    src = house.parent / "sdist-src"
    for rel, text in files.items():
        (src / rel).parent.mkdir(parents=True, exist_ok=True)
        (src / rel).write_text(text)
    with tarfile.open(house / f"{root}.tar.gz", "w:gz") as t:
        t.add(src / root, arcname=root)


def startup_wheel(house: Path, marker: Path) -> None:
    """A wheel carrying a ``.pth`` whose line writes ``marker`` at every start of the
    interpreter it is installed for."""
    house.mkdir(parents=True, exist_ok=True)
    di = "demo_pth-0.1.dist-info"
    pth = f"import pathlib; pathlib.Path({str(marker)!r}).write_text('started')\n"
    with zipfile.ZipFile(house / "demo_pth-0.1-py3-none-any.whl", "w") as z:
        z.writestr("demo_pth/__init__.py", "")
        z.writestr("zz_demo_pth.pth", pth)
        z.writestr(f"{di}/METADATA", "Metadata-Version: 2.1\nName: demo-pth\nVersion: 0.1\n")
        z.writestr(
            f"{di}/WHEEL",
            "Wheel-Version: 1.0\nGenerator: test\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
        )
        z.writestr(f"{di}/RECORD", "")


@pytest.fixture
def scratch(tmp_path, monkeypatch):
    monkeypatch.setenv("REPO_FACTORY_VENV_UV_CACHE", str(tmp_path / "uv-cache"))
    canary = tmp_path / "outside" / "kept.txt"
    canary.parent.mkdir()
    canary.write_text("canary\n")
    return tmp_path, canary


def test_a_source_package_builds_only_inside_the_sandbox(scratch):
    needs_bwrap()
    tmp, canary = scratch
    v = empty_venv(tmp / "venv")
    report = v / "build-report.txt"
    source_package(tmp / "house", report, canary)
    rc, err = V.install_sandboxed(v, ["demo-src"], [], _run, wheelhouse=tmp / "house")
    assert rc != 0  # the build stops itself
    assert report.read_text() == "ran; outside visible=False", err


def test_a_wheels_start_up_code_never_runs_on_the_host(scratch, monkeypatch):
    needs_bwrap()
    tmp, _ = scratch
    v = empty_venv(tmp / "venv")
    marker = tmp / "started.txt"
    startup_wheel(tmp / "house", marker)
    rc, err = V.install_sandboxed(v, ["demo-pth"], [], _run, wheelhouse=tmp / "house")
    assert rc == 0, err
    # the control: the line is live, it runs whenever this venv's interpreter starts
    subprocess.run([str(v / "bin" / "python"), "-c", "pass"], check=True)
    assert marker.read_text() == "started"
    marker.unlink()

    # what the host does with a product venv: its packaging wheels, its launchers, its tests
    seen: list[list[str]] = []
    real = subprocess.run

    def recorder(cmd, *a, **k):
        seen.append([str(x) for x in cmd])
        return real(["false"], *a, **{**k, "check": False})

    monkeypatch.setattr(pyinstall.subprocess, "run", recorder)
    pyinstall.download(["setuptools"], tmp / "wheels", v)
    monkeypatch.undo()
    assert seen and not any(str(v / "bin" / "python") in c for c in seen), seen
    V.relink(v)
    got = V.run_pytest(tmp / "venv", {}, [], v, timeout_s=120, network=False)
    assert got.get("isolation", "bwrap") == "bwrap"
    assert not marker.exists()


def test_a_requirement_by_address_is_refused_and_named(tmp_path):
    (tmp_path / "requirements.txt").write_text(
        "requests==2.32.0\n"
        "-e .\n"
        ".\n"
        "--index-url https://example.invalid/simple\n"
        "thing @ https://example.invalid/thing.tar.gz\n"
        "git+https://example.invalid/other.git\n"
        "../sibling\n"
    )
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "p"\nversion = "1"\ndependencies = ["click>=8", "x @ file:///tmp/x"]\n'
    )
    assert V.declared_dependencies(tmp_path) == ["click>=8", "requests==2.32.0"]
    assert V.refused_dependencies(tmp_path) == [
        "../sibling",
        "git+https://example.invalid/other.git",
        "thing @ https://example.invalid/thing.tar.gz",
        "x @ file:///tmp/x",
    ]


def test_refused_requirements_are_named_in_what_the_install_says(tmp_path, monkeypatch):
    (tmp_path / "requirements.txt").write_text("thing @ https://example.invalid/t.tar.gz\n")
    monkeypatch.setattr(V, "ensure_venv", lambda reqs: tmp_path / "task-venv")
    venv, info = V.candidate_venv(tmp_path, [])
    assert venv == tmp_path / "task-venv" and info["status"] == "none"
    assert info["refused"] == ["thing @ https://example.invalid/t.tar.gz"]
    assert "not installed: thing @ https://example.invalid/t.tar.gz" in info["detail"]


def test_without_bubblewrap_nothing_installs(tmp_path, monkeypatch):
    monkeypatch.setenv("REPO_FACTORY_VENV_ROOT", str(tmp_path / "venvs"))
    monkeypatch.setattr(V, "isolation", lambda: "none")
    path, err = V._build_venv(["some-package-not-cached"])
    assert path is None and "bubblewrap" in err
    assert not list(tmp_path.glob("venvs/*/bin/python"))


def test_the_install_reaches_only_the_allowed_indexes(monkeypatch):
    monkeypatch.delenv(V.EXAM_ENV, raising=False)
    assert V.indexes() == ["pypi.org", "files.pythonhosted.org"]
    monkeypatch.setenv(V.EXAM_ENV, '{"indexes": ["pypi.example.org"]}')
    assert V.indexes() == ["pypi.example.org"]
    monkeypatch.setenv(V.EXAM_ENV, '{"indexes": []}')
    assert V.indexes() == []
