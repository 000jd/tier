#!/bin/sh
# Downloads the real-world Markdown files used by benchmarks/bench.py Track D.
set -e
DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
ROOT=$(dirname "$DIR")
mkdir -p "$ROOT/corpus"
cd "$ROOT/corpus"
for u in "pytorch/pytorch/main/CONTRIBUTING.md:pytorch-contributing.md" \
         "nodejs/node/main/BUILDING.md:node-building.md" \
         "redis/redis/unstable/README.md:redis-readme.md" \
         "expressjs/express/master/Readme.md:express-readme.md" \
         "anthropics/claude-code/main/README.md:claude-code.md"; do
  curl -sfL "https://raw.githubusercontent.com/${u%%:*}" -o "${u##*:}" || echo "failed: ${u##*:}"
done
