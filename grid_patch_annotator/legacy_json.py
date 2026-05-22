"""Write combined legacy annotation JSON (row-wise low_semantic_patches lists)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def write_legacy_annotation_json_rowwise(path: Path, records: list[dict[str, Any]]) -> None:
    legacy_records = [
        {
            "sample_id": record["sample_id"],
            "image_index": record["image_index"],
            "low_semantic_patches": record["low_semantic_patches"],
            "_format_grid_cols": record.get("_format_grid_cols")
            or record.get("cols")
            or (record.get("patch_grid") or {}).get("cols"),
        }
        for record in records
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["["]
    for record_index, record in enumerate(legacy_records):
        block = format_legacy_record_rowwise(record.copy())
        suffix = "," if record_index < len(legacy_records) - 1 else ""
        lines.append(indent_block(block, 2) + suffix)
    lines.append("]")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def format_legacy_record_rowwise(record: dict[str, Any]) -> str:
    cols = record.pop("_format_grid_cols", None)
    grid = {"cols": cols} if cols else {}
    lines = ["{"]
    lines.append(f"  \"sample_id\": {json.dumps(record['sample_id'], ensure_ascii=False)},")
    lines.append(f"  \"image_index\": {int(record['image_index'])},")
    formatted_patches = format_patch_list_by_row(record["low_semantic_patches"], grid)
    patch_lines = formatted_patches.splitlines()
    lines.append(f"  \"low_semantic_patches\": {patch_lines[0]}")
    lines.extend(f"  {line}" for line in patch_lines[1:])
    lines.append("}")
    return "\n".join(lines)


def format_patch_list_by_row(patches: list[int], grid: dict[str, Any]) -> str:
    if not patches:
        return "[]"
    cols = int(grid.get("cols") or 0)
    if cols <= 0:
        return json.dumps(sorted(set(patches)), ensure_ascii=False)

    grouped: dict[int, list[int]] = {}
    for patch_id in sorted(set(patches)):
        grouped.setdefault(patch_id // cols, []).append(patch_id)

    lines = ["["]
    rows = sorted(grouped)
    for row_index, row in enumerate(rows):
        row_values = ", ".join(str(value) for value in grouped[row])
        suffix = "," if row_index < len(rows) - 1 else ""
        lines.append(f"  {row_values}{suffix}")
    lines.append("]")
    return "\n".join(lines)


def indent_block(text: str, spaces: int) -> str:
    prefix = " " * spaces
    return "\n".join(prefix + line if line else line for line in text.splitlines())
