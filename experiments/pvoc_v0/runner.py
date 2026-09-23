"""Compatibility entry point; prefer ``python -m experiments.pvoc_v0.cli smoke``."""

from .studies.smoke import main

if __name__ == "__main__":
    main()
