"""Small, reusable artifact I/O helpers for PVoC studies."""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Iterable
from pathlib import Path


def serialize_json(record: object) -> str:
    if hasattr(record, "model_dump_json"):
        return record.model_dump_json()
    return json.dumps(record, ensure_ascii=False)


def append_jsonl(path: Path, records: Iterable[object]) -> None:
    with path.open("a", encoding="utf-8") as output_file:
        for record in records:
            output_file.write(serialize_json(record))
            output_file.write("\n")
        output_file.flush()
        os.fsync(output_file.fileno())


def read_jsonl(path: Path, model: type) -> list[object]:
    if not path.exists():
        return []
    return [
        model.model_validate_json(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def read_json_objects(path: Path) -> list[dict[str, object]]:
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def atomic_write_json(path: Path, payload: object) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def atomic_write_jsonl(path: Path, records: Iterable[object]) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    with temporary.open("w", encoding="utf-8") as output_file:
        for record in records:
            output_file.write(serialize_json(record))
            output_file.write("\n")
        output_file.flush()
        os.fsync(output_file.fileno())
    temporary.replace(path)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def artifact_checksums(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): sha256_file(path)
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }
