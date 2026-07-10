"""CLI: generate a dataset report with no MCP client or model in the loop."""

from __future__ import annotations

import argparse
import sys

from .cache import DiskCache
from .config import Config
from .errors import SocrataMCPError
from .http_client import HttpClient
from .providers.socrata import SocrataProvider


def _make_provider() -> SocrataProvider:
    config = Config.from_env()
    return SocrataProvider(
        config=config, http=HttpClient(config), cache=DiskCache(config.cache_dir)
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="socrata-mcp-report",
        description="Generate a self-contained HTML report for a Socrata dataset.",
    )
    parser.add_argument("domain", help='portal hostname, e.g. "data.seattle.gov"')
    parser.add_argument("dataset_id", help='Socrata 4x4 id, e.g. "tazs-3rd5"')
    parser.add_argument(
        "-o", "--out", help="output path (default: ./<dataset_id>-report.html)"
    )
    parser.add_argument("--where", help="SoQL filter for the trend query")
    parser.add_argument("--title", help="report title (default: dataset name)")
    args = parser.parse_args(argv)

    out = args.out or f"{args.dataset_id}-report.html"
    try:
        result = _make_provider().generate_report(
            args.domain, args.dataset_id, out, where=args.where, title=args.title
        )
    except (SocrataMCPError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    for note in result["notes"]:
        print(f"note: {note}", file=sys.stderr)
    print(result["path"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
