# Container image for MCP registry / Glama deployment of the TIER stdio server.
# Stdlib only: no optional extras, no Node tokenizer, no tree-sitter grammars.
FROM python:3.12-slim

WORKDIR /app

# Only what the sdist needs: metadata, long description, and the package itself.
COPY pyproject.toml README.md ./
COPY src/ ./src/

RUN pip install --no-cache-dir .

# The workspace the server indexes. Mount a repository here, or leave it empty:
# `tier` builds the map on first use and caches it outside the worktree.
RUN mkdir -p /workspace

# `tier-mcp [ROOT]` speaks MCP over stdio for the directory it is given.
ENTRYPOINT ["tier-mcp", "/workspace"]
