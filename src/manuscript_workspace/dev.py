"""MCP Inspector entrypoint.

Set MANUSCRIPT_ROOT before running:
    mcp dev src/manuscript_workspace/dev.py
"""

from __future__ import annotations

import os
from pathlib import Path

from manuscript_workspace.server import create_mcp
from manuscript_workspace.store import ManuscriptStore

root = os.environ.get("MANUSCRIPT_ROOT")
if not root:
    raise RuntimeError("MANUSCRIPT_ROOT is required for MCP Inspector.")

mcp = create_mcp(ManuscriptStore(Path(root)))
