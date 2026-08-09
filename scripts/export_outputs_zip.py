"""Create a flat zip of validated output JSON files (EC_001 … EC_050)."""

import argparse
import asyncio
import zipfile
from pathlib import Path

from scripts.validate_outputs import validate_outputs


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=Path("output"))
    parser.add_argument("--input-dir", type=Path, default=Path("input"))
    parser.add_argument("--zip-file", type=Path, default=Path("output_export.zip"))
    args = parser.parse_args()
    errors = asyncio.run(validate_outputs(args.output_dir, args.input_dir))
    if errors:
        for error in errors:
            print(f"FAIL: {error}")
        return 1
    names = [f"EC_{index:03d}.json" for index in range(1, 51)]
    with zipfile.ZipFile(args.zip_file, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name in names:
            archive.write(args.output_dir / name, arcname=name)
    with zipfile.ZipFile(args.zip_file) as archive:
        entries = archive.namelist()
    if entries != names:
        args.zip_file.unlink(missing_ok=True)
        raise RuntimeError(f"Export zip entries are invalid: {entries}")
    for name in entries:
        print(name)
    print(f"PASS: wrote {args.zip_file} with exactly {len(entries)} JSON files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
