"""Token counting bridge: optional Node o200k BPE helper, otherwise chars/4."""
from __future__ import annotations

import json
import math
import os
import shutil
import subprocess

_PACKAGE_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(os.path.dirname(_PACKAGE_DIR))
_HERE = _PACKAGE_DIR


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


def count_tokens(strings: list):
    """Returns (counts, backend). backend is 'o200k' or 'estimate'."""
    js = _count_js()
    if (
        js
        and shutil.which("node")
        and (os.environ.get("TIER_COUNT_JS") or os.path.isdir(os.path.join(os.path.dirname(js), "node_modules")))
        and not os.environ.get("TIER_NO_NODE")
    ):
        try:
            r = subprocess.run(
                ["node", js], input=json.dumps(strings), capture_output=True, text=True, timeout=60
            )
            if r.returncode == 0:
                counts = json.loads(r.stdout)
                if isinstance(counts, list) and len(counts) == len(strings):
                    return counts, "o200k"
        except Exception:
            pass
    return [max(1, math.ceil(len(s) / 4)) for s in strings], "estimate"
