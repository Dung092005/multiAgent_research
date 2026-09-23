"""Compatibility entry point; prefer the unified ``pilot`` CLI command."""

from .studies.pilot import main

if __name__ == "__main__":
    main()
