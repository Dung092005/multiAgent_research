"""Single human-facing command line for the four PVoC research milestones."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable

from .studies import dataset_v1, pilot, smoke, stability

COMMANDS: dict[str, Callable[[], None]] = {
    "smoke": smoke.main,
    "stability": stability.main,
    "pilot": pilot.main,
    "dataset-v1": dataset_v1.main,
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run an isolated PVoC research milestone."
    )
    parser.add_argument("study", choices=COMMANDS)
    return parser


def main() -> None:
    parser = build_parser()
    if len(sys.argv) < 2 or sys.argv[1] in {"-h", "--help"}:
        parser.print_help()
        return
    study = sys.argv[1]
    if study not in COMMANDS:
        parser.error(f"unknown study: {study}")
    sys.argv = [f"{sys.argv[0]} {study}", *sys.argv[2:]]
    COMMANDS[study]()


if __name__ == "__main__":
    main()
