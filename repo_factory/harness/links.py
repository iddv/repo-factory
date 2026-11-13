"""Folders a sandboxed session (or a product's own code) could write, made safe for the host.

A session writes its workspace, its exam folder, the proposals folder and its own config dir
from inside bubblewrap, where most of the machine is hidden. Anything it leaves there is
read or copied later by the host, which sees the whole home. So a link in such a folder is
only kept when it stays inside the folder: ``scrub`` removes every symbolic link that is
absolute or leads out of the folder it is in, and every entry that is not a regular file,
a folder or such a link (a pipe, a socket, a device), right after the session ends and
before the host reads anything. ``copytree`` is the one way the host copies such a folder:
links stay links, and the same kinds of entries are left out, so a copy never carries
what lies outside.

Nothing here follows a link. A removed entry is named in the list ``scrub`` returns, so the
caller can say so."""

from __future__ import annotations

import os
import shutil
import stat
from collections.abc import Callable, Iterable
from pathlib import Path


class UnsafeFolder(RuntimeError):
    """The folder to copy is itself a link (or not a folder): refused."""


def _inside(target: str, link_dir: str, root: str) -> bool:
    """Does a relative link at ``link_dir`` pointing at ``target`` stay within ``root``?
    Judged on the text alone (``..`` components), then on the real path, so a chain of
    links that leaves the folder also counts as leaving it."""
    if os.path.isabs(target):
        return False
    joined = os.path.normpath(os.path.join(link_dir, target))
    if joined != root and not joined.startswith(root + os.sep):
        return False
    real = os.path.realpath(os.path.join(link_dir, target))
    real_root = os.path.realpath(root)
    return real == real_root or real.startswith(real_root + os.sep)


def keep(path: str, root: str) -> bool:
    """Whether the entry at ``path`` (inside ``root``) may stay: a regular file, a folder,
    or a relative link that stays inside ``root``. Never follows the entry itself."""
    try:
        st = os.lstat(path)
    except OSError:
        return False
    if stat.S_ISREG(st.st_mode) or stat.S_ISDIR(st.st_mode):
        return True
    if stat.S_ISLNK(st.st_mode):
        try:
            target = os.readlink(path)
        except OSError:
            return False
        return _inside(target, os.path.dirname(path), root)
    return False


def scrub(root: Path | str | None) -> list[str]:
    """Remove from ``root`` every entry ``keep`` refuses; returns their paths relative to
    ``root`` (sorted). A ``root`` that is missing is fine (nothing to do); a ``root`` that is
    itself a link is removed and named as ``"."``. Never raises on an entry it cannot
    remove: it is named with `` (could not remove)``."""
    if root is None:
        return []
    root_s = os.path.abspath(str(root))
    if os.path.islink(root_s):
        try:
            os.unlink(root_s)
            return ["."]
        except OSError:
            return [". (could not remove)"]
    if not os.path.isdir(root_s):
        return []
    root_s = os.path.realpath(root_s)
    removed: list[str] = []
    stack = [root_s]
    while stack:
        d = stack.pop()
        try:
            entries = list(os.scandir(d))
        except OSError:
            continue
        for e in entries:
            if e.is_dir(follow_symlinks=False):
                stack.append(e.path)
                continue
            if keep(e.path, root_s):
                continue
            rel = os.path.relpath(e.path, root_s)
            try:
                os.unlink(e.path)
                removed.append(rel)
            except OSError:
                removed.append(rel + " (could not remove)")
    return sorted(removed)


def scrub_all(roots: Iterable[Path | str | None]) -> dict[str, list[str]]:
    """``scrub`` on each folder; returns ``{folder: removed}`` for those with something
    removed."""
    out = {}
    for r in dict.fromkeys(str(x) for x in roots if x):
        got = scrub(r)
        if got:
            out[r] = got
    return out


Ignore = Callable[[str, list[str]], Iterable[str]]


def copytree(
    src: Path | str, dst: Path | str, *, ignore: Ignore | None = None, dirs_exist_ok: bool = False
) -> Path:
    """``shutil.copytree`` for a folder a session could write: links are copied as links,
    and an entry ``keep`` refuses (judged against ``src``) is left out. ``src`` itself must
    be a real folder, not a link. ``ignore`` as for ``shutil.copytree``."""
    s = os.path.abspath(str(src))
    if os.path.islink(s) or not os.path.isdir(s):
        raise UnsafeFolder(f"not a folder (or a link to one): {src}")
    root = os.path.realpath(s)

    def drop(d: str, names: list[str]) -> set[str]:
        out = set(ignore(d, names)) if ignore else set()
        real_d = os.path.join(root, os.path.relpath(d, s)) if d != s else root
        for n in names:
            if n not in out and not keep(os.path.join(real_d, n), root):
                out.add(n)
        return out

    shutil.copytree(s, dst, symlinks=True, ignore=drop, dirs_exist_ok=dirs_exist_ok)
    return Path(dst)


def copy_file(src: Path | str, dst: Path | str) -> bool:
    """Copy one file only when it is a regular file (not a link, not a pipe); returns
    whether it was copied."""
    try:
        st = os.lstat(str(src))
    except OSError:
        return False
    if not stat.S_ISREG(st.st_mode):
        return False
    shutil.copy2(str(src), str(dst), follow_symlinks=False)
    return True
