"""Command-line arguments for starting the MCP server."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit


@dataclass(frozen=True, slots=True)
class StartupOptions:
    """An empty URL selects today's feed; None leaves URL selection disabled."""

    schedule_url: str | None = None
    schedule_file: Path | None = None


def _http_url(value: str) -> str:
    if value == "":
        return value
    error_message = "Use an HTTP or HTTPS URL to a GTFS ZIP."
    try:
        parsed = urlsplit(value)
        valid = parsed.scheme in {"http", "https"} and parsed.hostname is not None
    except ValueError as exc:
        raise argparse.ArgumentTypeError(error_message) from exc
    if not valid:
        raise argparse.ArgumentTypeError(error_message)
    return value


def parse_args(argv: Sequence[str] | None = None) -> StartupOptions:
    """Parse startup options without starting services or configuring logging."""
    parser = argparse.ArgumentParser(description="Run the ZTM Poznań MCP server.")
    source = parser.add_mutually_exclusive_group()
    source.add_argument(
        "--schedule-url",
        nargs="?",
        const="",
        type=_http_url,
        metavar="URL",
        help="Download a single GTFS ZIP from URL; omit URL to select today's latest ZTM schedule.",
    )
    source.add_argument(
        "--schedule-file",
        type=Path,
        metavar="PATH",
        help="Load a single GTFS ZIP from this local file, relative to the working directory.",
    )
    args = parser.parse_args(argv)
    return StartupOptions(
        schedule_url=args.schedule_url,
        schedule_file=args.schedule_file,
    )
