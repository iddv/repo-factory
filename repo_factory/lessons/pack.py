"""Spec packs (build v5 §2.1): the loader, the hashes and the row classes.

A pack lives in ``$REPO_FACTORY_PACKS/<id>/`` (default ``~/factory-packs``, a private git repo outside
every sandbox mount). ``spec.md`` is the only file a builder sees; ``suite/`` (driver,
contract, conformance, scenarios, manifest) is given to the scorer and, minus the scenarios
and manifest, to adapter writers. Moved from ``hsuite/`` (S-5b): ``/history`` reads them."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

CLASSES = ("settled", "ruled", "either", "heavy", "config")
RULINGS = ("literal", "expected", "either", "unruled")
READINGS = ("literal", "expected")
# Addendum A: a binary item is an operator setting (its default is the owner's reading); a
# complex one is the implementation's call, so its rows are `either` (reported, never scored).
KINDS = ("binary", "complex")
RULED_BY = ("owner", "sim-owner")
STATUSES = ("draft", "candidate", "frozen", "retired")
# What an adapter writer may see (§2.5). Everything else in suite/ stays with the scorer.
ADAPTER_FILES = ("driver.py", "contract.md", "conformance.py")


def packs_root() -> Path:
    return Path(os.environ.get("REPO_FACTORY_PACKS") or Path.home() / "factory-packs")


def bench_root() -> Path:
    return Path(os.environ.get("REPO_FACTORY_BENCH") or Path.home() / "factory-bench")


def dir_hash(root: Path) -> str:
    """sha256 over sorted relative paths and contents; bytecode and caches excluded."""
    root = Path(root)
    h = hashlib.sha256()
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root)
        if not p.is_file() or "__pycache__" in rel.parts or p.suffix == ".pyc":
            continue
        h.update(rel.as_posix().encode() + b"\0" + p.read_bytes() + b"\0")
    return h.hexdigest()


def file_hash(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class PackError(Exception):
    pass


@dataclass
class Pack:
    root: Path
    meta: dict
    manifest: list[dict] = field(default_factory=list)
    interpretations: list[dict] = field(default_factory=list)

    @classmethod
    def load(cls, ref: str | Path) -> Pack:
        """``ref`` is a pack id under packs_root() or a path to a pack directory."""
        root = Path(ref) if (os.sep in str(ref) or Path(ref).is_dir()) else packs_root() / str(ref)
        root = root.expanduser()
        if not (root / "pack.yaml").is_file():
            raise PackError(f"no pack at {root} (pack.yaml missing)")
        meta = yaml.safe_load((root / "pack.yaml").read_text()) or {}
        manifest = (
            json.loads((root / "suite" / "manifest.json").read_text())
            if (root / "suite" / "manifest.json").is_file()
            else []
        )
        interp = (
            (yaml.safe_load((root / "interpretations.yaml").read_text()) or [])
            if (root / "interpretations.yaml").is_file()
            else []
        )
        return cls(root, meta, manifest, interp)

    # -- metadata
    @property
    def id(self) -> str:
        return str(self.meta.get("id") or self.root.name)

    @property
    def version(self) -> int:
        return int(self.meta.get("version") or 0)

    @property
    def split(self) -> str:
        return str(self.meta.get("split") or "dev")

    @property
    def suite_dir(self) -> Path:
        return self.root / "suite"

    @property
    def requirements(self) -> list[str]:
        """Extra packages the suite runner needs next to the build's own dependencies."""
        return list(self.meta.get("requirements") or [])

    # -- hashes
    def suite_sha256(self) -> str:
        return dir_hash(self.suite_dir)

    def spec_sha256(self) -> str:
        return file_hash(self.root / "spec.md")

    def interpretations_sha256(self) -> str | None:
        p = self.root / "interpretations.yaml"
        return file_hash(p) if p.is_file() else None

    def hash_problems(self) -> list[str]:
        """Differences between the stamped hashes in pack.yaml and the files on disk. A pack
        without stamped hashes (a draft) has none to differ from."""
        out = []
        for key, fn in (
            ("suite_sha256", self.suite_sha256),
            ("spec_sha256", self.spec_sha256),
            ("interpretations_sha256", self.interpretations_sha256),
        ):
            want = self.meta.get(key)
            if want and want != fn():
                out.append(
                    f"{key}: pack.yaml says {str(want)[:12]}..., files hash to {fn()[:12]}..."
                )
        return out

    # -- rows
    def row_counts(self) -> dict[str, int]:
        out = {c: 0 for c in CLASSES}
        for r in self.manifest:
            c = self.effective_class(r)
            out[c] = out.get(c, 0) + 1
        return out

    def interpretation(self, iid: str | None) -> dict | None:
        return next((i for i in self.interpretations if i.get("id") == iid), None) if iid else None

    def scored_as(self, row: dict) -> str:
        """Which score a row counts in: ``core`` (settled), ``interp`` (a ruled row whose
        reading the owner chose), ``config`` (a binary item's row run under a forced setting,
        Addendum A), or ``none`` (either, heavy, unruled, the other reading, or a row of a
        ``complex`` item the owner left to the implementation)."""
        cls = row.get("class")
        if cls == "settled":
            return "core"
        i = self.interpretation(row.get("interp"))
        if cls == "config":
            return "config" if (i or {}).get("kind") == "binary" else "none"
        if cls == "ruled":
            ruling = (i or {}).get("ruling", "unruled")
            if ruling in READINGS and row.get("reading", "expected") == ruling:
                return "interp"
        return "none"

    def effective_class(self, row: dict) -> str:
        """The class a row reports under: a ruled row of an item ruled ``either`` (every
        ``complex`` item unless the owner settled it) is ``either``."""
        if row.get("class") == "ruled":
            if (self.interpretation(row.get("interp")) or {}).get("ruling") == "either":
                return "either"
        return row.get("class")

    def config_variants(self) -> list[dict]:
        """``[{"scenario", "configure": {key: value}, "name"}]``: each scenario with config
        rows, once per forced setting value (the sandbox runner runs each as its own case)."""
        out, seen = [], set()
        for r in self.manifest:
            if r.get("class") != "config":
                continue
            name = variant_name(r["scenario"], r["configure"])
            if name not in seen:
                seen.add(name)
                out.append({"scenario": r["scenario"], "configure": r["configure"], "name": name})
        return out

    def manifest_problems(self) -> list[str]:
        out, seen = [], set()
        for r in self.manifest:
            rid = r.get("id")
            if rid in seen:
                out.append(f"duplicate row {rid}")
            seen.add(rid)
            if r.get("class") not in CLASSES:
                out.append(f"{rid}: class {r.get('class')!r} not in {CLASSES}")
            if r.get("on_unsupported", "fail") not in ("fail", "pass"):
                out.append(f"{rid}: on_unsupported must be fail or pass")
            if not str(rid).startswith(f"{r.get('scenario')}/"):
                out.append(f"{rid}: id must start with its scenario name")
            if (
                r.get("class") in ("ruled", "config")
                and self.interpretation(r.get("interp")) is None
            ):
                out.append(f"{rid}: {r.get('class')} row without a known interpretation")
            if r.get("class") == "config":
                conf = r.get("configure")
                if not isinstance(conf, dict) or len(conf) != 1:  # stored-shape: isinstance first
                    out.append(f"{rid}: a config row needs configure with one key")
                if r.get("expect") not in ("pass", "fail"):
                    out.append(f"{rid}: a config row needs expect: pass or fail")
                if not str(rid).startswith(str(r.get("base_row")) + "@"):
                    out.append(f"{rid}: a config row's id must be <base_row>@<key>=<value>")
            if not (self.suite_dir / "scenarios" / f"{r.get('scenario')}.py").is_file():
                out.append(f"{rid}: scenarios/{r.get('scenario')}.py missing")
        for i in self.interpretations:
            if i.get("ruling", "unruled") not in RULINGS:
                out.append(f"{i.get('id')}: ruling {i.get('ruling')!r} not in {RULINGS}")
            if i.get("kind") is not None and i["kind"] not in KINDS:
                out.append(f"{i.get('id')}: kind {i.get('kind')!r} not in {KINDS}")
            if i.get("kind") == "binary":
                out += [f"{i.get('id')}: {p}" for p in setting_problems(i)]
        return out

    # -- reference builds
    def refs(self) -> dict[str, Path]:
        d = self.root / "refs"
        return (
            {p.name: p for p in sorted(d.iterdir()) if p.is_dir() and p.name.startswith("ref-")}
            if d.is_dir()
            else {}
        )

    def ref_for_build(self, build_sha256: str) -> Path | None:
        """The reference whose run.json records this build hash (its adapter is reused)."""
        for p in self.refs().values():
            rj = p / "run.json"
            if rj.is_file() and json.loads(rj.read_text()).get("build_sha256") == build_sha256:
                return p
        return None

    # -- writing
    def stamp(self) -> dict:
        """Recompute hashes and row counts into pack.yaml (key order kept). Used when a
        suite changes: bump ``version`` by hand, then stamp."""
        self.meta["spec_sha256"] = self.spec_sha256()
        self.meta["suite_sha256"] = self.suite_sha256()
        if self.interpretations_sha256():
            self.meta["interpretations_sha256"] = self.interpretations_sha256()
        self.meta["rows"] = self.row_counts()
        (self.root / "pack.yaml").write_text(yaml.safe_dump(self.meta, sort_keys=False))
        return self.meta


def variant_name(scenario: str, configure: dict) -> str:
    """``<scenario>@<key>=<value>``: the runner's name for a run with a forced setting."""
    return scenario + "".join(f"@{k}={_val(v)}" for k, v in sorted(configure.items()))


def _val(v) -> str:
    return v if isinstance(v, str) else json.dumps(v)


def setting_problems(item: dict) -> list[str]:
    """A binary item's ``setting``: ``{key, values, default, scope, readings}``. ``readings``
    maps each reading to the value that implements it; the default is the value of the ruled
    reading (the expected one while unruled)."""
    st = item.get("setting")
    if not isinstance(st, dict):
        return ["binary item without a setting"]
    out = []
    if not st.get("key"):
        out.append("setting has no key")
    vals = list(st.get("values") or [])
    if len(vals) != 2 or vals[0] == vals[1]:
        out.append("setting needs two distinct values")
    rd = st.get("readings") or {}
    if (
        set(rd) != set(READINGS)
        or any(v not in vals for v in rd.values())
        or rd.get("literal") == rd.get("expected")
    ):
        out.append("setting.readings must map literal and expected to its two values")
    if st.get("default") not in vals:
        out.append("setting.default is not one of its values")
    reading = item.get("ruling") if item.get("ruling") in READINGS else "expected"
    if rd and st.get("default") != rd.get(reading):
        out.append(
            f"setting.default {st.get('default')!r} is not the value of the {reading} "
            f"reading ({rd.get(reading)!r})"
        )
    return out


def config_rows(pack: Pack) -> list[dict]:
    """Config rows for every binary item (Addendum A), derived from its ruled rows: the row's
    scenario runs with the setting forced to each value. Under the value that implements the
    row's reading the row's check must pass; under the other value it must not (the check was
    written to tell the two readings apart)."""
    out = []
    for base in pack.manifest:
        if base.get("class") != "ruled":
            continue
        item = pack.interpretation(base.get("interp"))
        if not item or item.get("kind") != "binary" or setting_problems(item):
            continue
        st = item["setting"]
        mine = st["readings"][base.get("reading", "expected")]
        for v in st["values"]:
            out.append(
                {
                    "id": f"{base['id']}@{st['key']}={_val(v)}",
                    "scenario": base["scenario"],
                    "class": "config",
                    "quote": base.get("quote", ""),
                    "interp": item["id"],
                    "base_row": base["id"],
                    "configure": {st["key"]: v},
                    "expect": "pass" if v == mine else "fail",
                    "needs": sorted(set(base.get("needs") or []) | {f"config.{st['key']}"}),
                    "on_unsupported": "fail",
                    "origin": f"addendum-A/config/{item['id']}",
                }
            )
    return out


def with_config_rows(pack: Pack) -> list[dict]:
    """The manifest with its config rows regenerated: old ones removed, new ones placed
    after the last row of their scenario."""
    rows = [r for r in pack.manifest if r.get("class") != "config"]
    cfg = config_rows(Pack(pack.root, pack.meta, rows, pack.interpretations))
    out = []
    for i, r in enumerate(rows):
        out.append(r)
        if i + 1 == len(rows) or rows[i + 1]["scenario"] != r["scenario"]:
            out += [c for c in cfg if c["scenario"] == r["scenario"]]
    return out


def write_manifest(pack: Pack, rows: list[dict]) -> None:
    pack.manifest = rows
    (pack.suite_dir / "manifest.json").write_text(
        json.dumps(rows, indent=1, ensure_ascii=False) + "\n"
    )


def write_interpretations(pack: Pack, items: list[dict], header: str = "") -> None:
    pack.interpretations = items
    text = yaml.safe_dump(items, sort_keys=False, allow_unicode=True, width=100)
    (pack.root / "interpretations.yaml").write_text(header + text)


def list_packs(root: Path | None = None) -> list[Pack]:
    root = root or packs_root()
    if not root.is_dir():
        return []
    out = []
    for p in sorted(root.iterdir()):
        if (p / "pack.yaml").is_file():
            try:
                out.append(Pack.load(p))
            except Exception:  # noqa: BLE001 - a broken pack is listed by `pack list` as such
                continue
    return out
