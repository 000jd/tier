"""Token counting bridge: optional Node o200k BPE worker, otherwise chars/4.

Exact counts use a persistent stdio Node worker (`benchmarks/count-server.mjs`)
so a multi-call session pays Node startup at most once. Repeated counts of the
same string are served from an in-memory content-hash cache. When Node or the
tokenizer package is unavailable, a deterministic characters/4 estimate is used.
Set TIER_TOKEN_FAST=1 (or call `fast_mode(True)`) to force the estimate.
"""
from __future__ import annotations

import atexit
import hashlib
import json
import math
import os
import queue
import shutil
import subprocess
import threading
from collections import OrderedDict

_PACKAGE_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(os.path.dirname(_PACKAGE_DIR))
_HERE = _PACKAGE_DIR

_WORKER_SCRIPT = "count-server.mjs"
_CACHE_MAX = 8192
_CACHE = OrderedDict()
_CACHE_LOCK = threading.Lock()
_WORKER = None
_WORKER_LOCK = threading.RLock()
_EXACT = None
_FAST = bool(os.environ.get("TIER_TOKEN_FAST"))


def _count_js() -> str:
    candidates = [
        os.environ.get("TIER_COUNT_JS", ""),
        os.path.join(_PROJECT_ROOT, "benchmarks", "count.mjs"),
        os.path.join(os.getcwd(), "benchmarks", "count.mjs"),
        os.path.join(os.getcwd(), "count.mjs"),
        os.path.join(_PACKAGE_DIR, "count.mjs"),
    ]
    for candidate in candidates:
        if candidate and os.path.isfile(candidate):
            return candidate
    return ""


def fast_mode() -> bool:
    return _FAST


def set_fast(value: bool) -> None:
    global _FAST
    _FAST = bool(value)


class _fast_default:
    """Context manager that temporarily toggles the default estimator mode."""

    def __init__(self, value: bool):
        self.value = bool(value)
        self.previous = None

    def __enter__(self):
        self.previous = fast_mode()
        set_fast(self.value)
        return self

    def __exit__(self, *exc):
        set_fast(self.previous)
        return False


def _cache_key(s: str) -> bytes:
    return hashlib.blake2b(s.encode("utf-8", "replace"), digest_size=16).digest()


def _cache_get(s: str):
    if _CACHE_MAX <= 0:
        return None
    key = _cache_key(s)
    with _CACHE_LOCK:
        value = _CACHE.get(key)
        if value is not None:
            _CACHE.move_to_end(key)
        return value


def _cache_put(s: str, count: int) -> None:
    if _CACHE_MAX <= 0:
        return
    key = _cache_key(s)
    with _CACHE_LOCK:
        _CACHE[key] = int(count)
        _CACHE.move_to_end(key)
        while len(_CACHE) > _CACHE_MAX:
            _CACHE.popitem(last=False)


def _estimate(strings: list) -> list:
    return [max(1, math.ceil(len(s) / 4)) for s in strings]


def _node_ready() -> bool:
    global _EXACT
    if _EXACT is None:
        js = _count_js()
        node = shutil.which("node")
        _EXACT = bool(
            js
            and node
            and not os.environ.get("TIER_NO_NODE")
            and (
                os.environ.get("TIER_COUNT_JS")
                or os.path.isdir(os.path.join(os.path.dirname(js), "node_modules"))
            )
        )
    return _EXACT


class _NodeWorker:
    """A single long-lived `node count-server.mjs` process speaking JSON lines."""

    def __init__(self, node: str, script: str):
        self.proc = subprocess.Popen(
            [node, script],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            bufsize=1,
        )
        self.lines: "queue.Queue" = queue.Queue()
        self.lock = threading.Lock()
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()

    def _read(self) -> None:
        try:
            for line in self.proc.stdout:
                self.lines.put(line)
        except Exception:
            pass
        finally:
            self.lines.put(None)

    def alive(self) -> bool:
        return self.proc.poll() is None

    def request(self, strings: list, timeout: float = 60.0) -> list:
        with self.lock:
            if not self.alive():
                raise RuntimeError("token worker exited")
            try:
                self.proc.stdin.write(json.dumps(strings) + "\n")
                self.proc.stdin.flush()
            except (BrokenPipeError, OSError, ValueError) as e:
                raise RuntimeError(f"token worker write failed: {e}")
            try:
                line = self.lines.get(timeout=timeout)
            except queue.Empty:
                raise RuntimeError("token worker timed out")
            if line is None:
                raise RuntimeError("token worker closed its stream")
            counts = json.loads(line)
            if not isinstance(counts, list) or len(counts) != len(strings):
                raise RuntimeError("token worker returned a malformed response")
            return counts

    def close(self) -> None:
        proc = self.proc
        try:
            if proc.stdin:
                proc.stdin.close()
        except Exception:
            pass
        try:
            proc.wait(timeout=2)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass


def _discard_worker() -> None:
    global _WORKER
    with _WORKER_LOCK:
        worker, _WORKER = _WORKER, None
    if worker is not None:
        worker.close()


def _worker():
    global _WORKER
    with _WORKER_LOCK:
        if _WORKER is not None and _WORKER.alive():
            return _WORKER
        js = _count_js()
        if not js:
            return None
        script = os.path.join(os.path.dirname(js), _WORKER_SCRIPT)
        if not os.path.isfile(script):
            return None
        node = shutil.which("node")
        if not node:
            return None
        try:
            _WORKER = _NodeWorker(node, script)
        except Exception:
            _WORKER = None
        return _WORKER


def _one_shot(strings: list):
    js = _count_js()
    if not js or not shutil.which("node"):
        return None
    try:
        r = subprocess.run(
            ["node", js], input=json.dumps(strings), capture_output=True, text=True, timeout=60
        )
        if r.returncode != 0:
            return None
        counts = json.loads(r.stdout)
        if isinstance(counts, list) and len(counts) == len(strings):
            return counts
    except Exception:
        pass
    return None


def _node_counts(strings: list):
    worker = _worker()
    if worker is not None:
        try:
            return worker.request(strings)
        except Exception:
            _discard_worker()
            worker = _worker()
            if worker is not None:
                try:
                    return worker.request(strings)
                except Exception:
                    _discard_worker()
    return _one_shot(strings)


def count_tokens(strings: list, fast: bool = None):
    """Returns (counts, backend). backend is 'o200k' or 'estimate'.

    `fast=True` forces the deterministic characters/4 estimate. `fast=None`
    follows the process-wide default set by `set_fast`/TIER_TOKEN_FAST.
    """
    strings = [s if isinstance(s, str) else str(s) for s in strings]
    if fast is None:
        fast = _FAST
    if fast:
        return _estimate(strings), "estimate"
    if not strings:
        return [], "o200k" if _node_ready() else "estimate"
    if not _node_ready():
        return _estimate(strings), "estimate"

    counts = [None] * len(strings)
    missing = {}
    for i, s in enumerate(strings):
        cached = _cache_get(s)
        if cached is not None:
            counts[i] = cached
        else:
            missing.setdefault(s, []).append(i)
    if not missing:
        return counts, "o200k"
    unique = list(missing)
    fresh = _node_counts(unique)
    if fresh is None:
        return _estimate(strings), "estimate"
    for s, n in zip(unique, fresh):
        _cache_put(s, n)
        for i in missing[s]:
            counts[i] = n
    return counts, "o200k"


def cache_clear() -> None:
    with _CACHE_LOCK:
        _CACHE.clear()


atexit.register(_discard_worker)
