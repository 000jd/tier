"""Repository-index cache resolution.

`repo.tier` is a derived artifact, so requiring users to track one in their tree
is friction. This module resolves where the map for a given root lives:

1. An explicit ``*.tier`` path always wins (caller-supplied).
2. ``<root>/repo.tier`` when it exists, which keeps checked-in maps working and
   the CI freshness gate meaningful.
3. ``<root>/.git/info/tier/repo.tier`` when ``.git`` is a directory. Git ignores
   everything under ``.git/info``, so this never dirties the working tree and
   needs no ``.gitignore`` entry.
4. ``<cache_root>/<sha256(realpath(root))[:12]>/repo.tier`` otherwise, which is
   outside the worktree entirely.

Nothing here writes unless ``ensure_doc`` is called.
"""
from __future__ import annotations

import hashlib
import os
import sys

from .models import _atomic_write
from .parser import _load, dumps, is_repo_doc
from .repo import from_repo, sync_repo

CACHE_DIR_ENV = "TIER_CACHE_DIR"
CACHE_FILENAME = "repo.tier"


def cache_root() -> str:
    """Root directory for caches that cannot live inside the repository."""
    override = os.environ.get(CACHE_DIR_ENV)
    if override:
        return os.path.abspath(os.path.expanduser(override))
    if sys.platform.startswith("win"):
        base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
        if base:
            return os.path.join(base, "tier", "cache")
    xdg = os.environ.get("XDG_CACHE_HOME")
    if xdg:
        return os.path.join(os.path.abspath(os.path.expanduser(xdg)), "tier")
    return os.path.join(os.path.expanduser("~"), ".cache", "tier")


def _git_dir(root: str) -> str:
    """Return the git metadata directory for `root`, or "" when not a worktree."""
    dot_git = os.path.join(root, ".git")
    if os.path.isdir(dot_git):
        return dot_git
    return ""


def _writable_dir(path: str) -> bool:
    if os.path.isdir(path):
        return os.access(path, os.W_OK)
    parent = os.path.dirname(path)
    while parent and not os.path.exists(parent):
        nxt = os.path.dirname(parent)
        if nxt == parent:
            break
        parent = nxt
    return os.path.isdir(parent) and os.access(parent, os.W_OK)


def cache_path(root: str) -> str:
    """The invisible cache location for a repository root."""
    root = os.path.abspath(root)
    tracked = os.path.join(root, CACHE_FILENAME)
    if os.path.isfile(tracked):
        return tracked
    git = _git_dir(root)
    if git:
        info_dir = os.path.join(git, "info", "tier")
        candidate = os.path.join(info_dir, CACHE_FILENAME)
        if _writable_dir(info_dir):
            return candidate
    digest = hashlib.sha256(os.path.realpath(root).encode("utf-8", "replace")).hexdigest()[:12]
    return os.path.join(cache_root(), digest, CACHE_FILENAME)


def cache_kind(path: str, root: str) -> str:
    """Classify a resolved path: ``tracked``, ``git``, or ``cache``."""
    path = os.path.abspath(path)
    tracked = os.path.join(os.path.abspath(root), CACHE_FILENAME)
    if path == tracked:
        return "tracked"
    if os.sep + os.path.join(".git", "info") + os.sep in path:
        return "git"
    return "cache"


def is_cache_dir(path: str, root: str = "") -> bool:
    """True when `path` is a directory this module owns (so listings skip it)."""
    path = os.path.abspath(path)
    parts = path.replace(os.sep, "/").split("/")
    if ".git/info/tier" in "/".join(parts):
        return True
    roots = {os.path.abspath(cache_root())}
    digest_dir = os.path.dirname(cache_path(root)) if root else ""
    if digest_dir:
        roots.add(digest_dir)
    return any(path == r or path.startswith(r + os.sep) for r in roots if r)


def _work_root_for(path: str, hint: str = "") -> str:
    """Infer the repository a map describes when it lives outside that repo.

    An invisible cache sits under `<git>/info/tier/` or `<cache_root>/<digest>/`,
    neither of which contains the sources, so the owning root is recovered from
    the git metadata path or from the digest directory's manifest.
    """
    path = os.path.abspath(path)
    norm = path.replace(os.sep, "/")
    marker = "/.git/info/tier/"
    if marker in norm:
        return norm.split(marker, 1)[0].replace("/", os.sep) or os.sep
    parent = os.path.dirname(path)
    owner = os.path.join(parent, ".owner")
    if os.path.isfile(owner):
        try:
            with open(owner, encoding="utf-8") as f:
                value = f.read().strip()
            if value:
                return value
        except OSError:
            pass
    return hint or os.path.dirname(path)


def resolve_doc(target=None, root=None):
    """Resolve a target to ``(doc_path, work_root, kind)``.

    `target` may be an explicit ``.tier`` file, a directory, or None.
    """
    base = os.path.abspath(root or os.getcwd())
    if target is None or str(target).strip() in ("", "."):
        target_path = base
    elif os.path.isabs(str(target)):
        target_path = os.path.abspath(str(target))
    else:
        target_path = os.path.abspath(os.path.join(base, str(target)))

    if os.path.isfile(target_path):
        if not target_path.endswith(".tier"):
            raise ValueError(f"not a .tier document: {target}")
        return target_path, _work_root_for(target_path, os.path.dirname(target_path)), \
            cache_kind(target_path, base)
    if os.path.isdir(target_path):
        return cache_path(target_path), target_path, cache_kind(cache_path(target_path), target_path)
    raise ValueError(f"no such file or directory: {target}")


def ensure_doc(target=None, root=None, session=None, session_key: str = "",
               write: bool = True):
    """Return ``(path, doc, work_root)``, building or syncing the map as needed.

    ``work_root`` is the directory the map describes, which is not always the
    directory holding the map: an invisible cache lives elsewhere, so slices and
    impact need the original root to resolve source files.
    """
    path, work_root, _kind = resolve_doc(target, root)
    if os.path.isfile(path):
        doc = _load(path)
        if is_repo_doc(doc):
            try:
                res = sync_repo(path, root=work_root, session=session,
                                session_key=session_key or path, write=write)
                return path, res["doc"], work_root
            except (OSError, ValueError):
                return path, doc, work_root
        return path, doc, work_root

    doc = from_repo(work_root)
    if write:
        _atomic_write(path, dumps(doc))
        _write_owner(path, work_root)
    return path, doc, work_root


def _write_owner(path: str, work_root: str) -> None:
    """Record which repository a cached map describes.

    Only needed when the map lives outside its repository; a tracked
    `<root>/repo.tier` is already next to its sources.
    """
    norm = os.path.abspath(path).replace(os.sep, "/")
    if "/.git/info/tier/" in norm:
        return
    if os.path.dirname(os.path.abspath(path)) == os.path.abspath(work_root):
        return
    try:
        _atomic_write(os.path.join(os.path.dirname(path), ".owner"), work_root + "\n")
    except OSError:
        pass
