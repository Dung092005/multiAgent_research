"""Compatibility entry point; prefer the unified ``dataset-v1`` CLI command."""

from .studies.dataset_v1 import main

if __name__ == "__main__":
    main()
