"""Entrypoint for the socrata-mcp server (stdio transport)."""

from __future__ import annotations

import logging
import sys

from .mcp import tools  # noqa: F401  (imports register the tools)
from .mcp.app import server


def main() -> None:
    # stdout carries the MCP protocol; logs must go to stderr.
    logging.basicConfig(
        stream=sys.stderr,
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
