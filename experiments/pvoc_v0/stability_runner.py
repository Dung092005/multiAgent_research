"""Compatibility entry point; prefer the unified ``stability`` CLI command."""

from .studies.stability import main

if __name__ == "__main__":
    main()
