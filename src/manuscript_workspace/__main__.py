"""Command line entrypoint for Manuscript Workspace."""

from __future__ import annotations

import argparse
import logging
import os
from pathlib import Path

import uvicorn

from manuscript_workspace.errors import ManuscriptError
from manuscript_workspace.server import create_app
from manuscript_workspace.store import ManuscriptStore


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the Manuscript Workspace MCP server.")
    parser.add_argument("--root", help="Absolute path to the manuscript root. Overrides MANUSCRIPT_ROOT.")
    parser.add_argument("--host", default="127.0.0.1", help="Host to bind. Defaults to 127.0.0.1.")
    parser.add_argument("--port", type=int, default=8000, help="Port to bind. Defaults to 8000.")
    parser.add_argument("--log-level", default="info", choices=["debug", "info", "warning", "error", "critical"])
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    logging.basicConfig(level=args.log_level.upper(), format="%(asctime)s %(levelname)s %(name)s %(message)s")
    root = args.root or os.environ.get("MANUSCRIPT_ROOT")
    if not root:
        raise SystemExit("MANUSCRIPT_ROOT is required, or pass --root /absolute/path/to/book.")
    try:
        store = ManuscriptStore(Path(root))
    except ManuscriptError as exc:
        raise SystemExit(f"{exc.code}: {exc.message}") from exc
    uvicorn.run(create_app(store), host=args.host, port=args.port, log_level=args.log_level)


if __name__ == "__main__":
    main()
