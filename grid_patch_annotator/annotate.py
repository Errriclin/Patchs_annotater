#!/usr/bin/env python3
"""Interactive desktop annotator for low-semantic patch grids.

Loads grid-preview JPGs (red grid lines + patch_id labels), infers rows/cols
from the image, and writes legacy (and optional full) annotation JSON compatible
with the existing ViDoRe2 patch pipeline. No smashed_pages manifest required.
"""

from __future__ import annotations

import argparse
import bisect
import json
import re
import statistics
import sys
import tkinter as tk
from dataclasses import dataclass
from pathlib import Path
from tkinter import messagebox, simpledialog, ttk
from typing import Any

TOOL_DIR = Path(__file__).resolve().parent
if str(TOOL_DIR) not in sys.path:
    sys.path.insert(0, str(TOOL_DIR))

from grid_geometry import IMAGE_EXTENSIONS, patch_box
from legacy_json import write_legacy_annotation_json_rowwise
from PIL import Image, ImageTk

PATCH_GRID_FILENAME = re.compile(
    r"^(?P<order>\d+)_(?P<image_index>\d+)_(?P<sample_id>.+)\.jpg$",
    re.IGNORECASE,
)

SAVE_DEBOUNCE_MS = 300
OVERLAY_FILL = "#88ff88"
OVERLAY_OUTLINE = "#00aa00"
OVERLAY_STIPPLE = "gray50"

ZOOM_MIN = 0.25
ZOOM_MAX = 4.0
ZOOM_STEP = 1.2
WHEEL_SCROLL_UNITS = 3
TK_CTRL_MASK = 0x0004
TK_SHIFT_MASK = 0x0001
TK_ALT_MASK = 0x0008
DRAG_CLICK_THRESHOLD_PX = 8
RUBBER_BAND_OUTLINE = "#00aaff"

INPUT_DIR = TOOL_DIR / "input"
INPUT_PATCH_GRIDS_DIR = INPUT_DIR / "patch_grids"
DEFAULT_GRID_META_JSON = INPUT_PATCH_GRIDS_DIR / "grid_metadata.json"

GRID_LINE_SPAN_COVERAGE = 0.72
GRID_RED_MIN_MAIN = 160
GRID_CLUSTER_TOLERANCE = 3
GRID_UNIFORM_SPACING_TOL = 0.42
GRID_SCORE_SAMPLE_STRIDE = 4
DEFAULT_OUTPUT = TOOL_DIR / "output" / "vidore2_patch_annotations_manual.json"


def resolve_tool_path(path: Path) -> Path:
    if path.is_absolute():
        return path.resolve()
    return (TOOL_DIR / path).resolve()


def resolve_data_path(path: Path) -> Path:
    """Resolve relative paths under this tool folder (portable when zipped anywhere)."""
    if path.is_absolute():
        return path.resolve()
    return (TOOL_DIR / path).resolve()


def dir_has_grid_images(directory: Path) -> bool:
    if not directory.is_dir():
        return False
    for path in directory.iterdir():
        if path.suffix.lower() in IMAGE_EXTENSIONS and PATCH_GRID_FILENAME.match(path.name):
            return True
    return False


def default_patch_grids_dir() -> Path:
    for directory in (INPUT_PATCH_GRIDS_DIR, INPUT_DIR):
        if dir_has_grid_images(directory):
            return directory
    return INPUT_PATCH_GRIDS_DIR


def ensure_input_layout() -> None:
    INPUT_DIR.mkdir(parents=True, exist_ok=True)
    INPUT_PATCH_GRIDS_DIR.mkdir(parents=True, exist_ok=True)
    (TOOL_DIR / "output").mkdir(parents=True, exist_ok=True)


def check_runtime_dependencies() -> None:
    errors: list[str] = []
    try:
        import PIL  # noqa: F401
    except ImportError:
        errors.append("缺少 Pillow。双击 install_deps.bat，或执行: python -m pip install -r requirements.txt")
    if errors:
        raise SystemExit("\n".join(errors))


def is_grid_red_pixel(r: int, g: int, b: int) -> bool:
    return r >= GRID_RED_MIN_MAIN and g <= 120 and b <= 120 and r > g + 30 and r > b + 30


def cluster_positions(positions: list[int], tolerance: int = GRID_CLUSTER_TOLERANCE) -> list[int]:
    if not positions:
        return []
    sorted_vals = sorted(positions)
    clusters: list[list[int]] = [[sorted_vals[0]]]
    for value in sorted_vals[1:]:
        if value - clusters[-1][-1] <= tolerance:
            clusters[-1].append(value)
        else:
            clusters.append([value])
    return [round(sum(cluster) / len(cluster)) for cluster in clusters]


def filter_uniform_lines(lines: list[int]) -> list[int]:
    """Drop outliers so only a regularly spaced grid remains."""
    if len(lines) < 2:
        return lines

    kept = [lines[0]]
    recent_gaps: list[int] = []
    for line in lines[1:]:
        gap = line - kept[-1]
        if len(kept) == 1:
            kept.append(line)
            recent_gaps.append(gap)
            continue
        median_gap = statistics.median(recent_gaps[-min(8, len(recent_gaps)) :])
        if abs(gap - median_gap) <= max(3, median_gap * GRID_UNIFORM_SPACING_TOL):
            kept.append(line)
            recent_gaps.append(gap)
    return kept


def detect_span_lines(
    pixels: Any,
    width: int,
    height: int,
    *,
    axis: str,
) -> list[int]:
    """Detect grid lines that span most of the image (ignore local red content)."""
    if axis == "vertical":
        hits: list[int] = []
        for x in range(width):
            red_count = sum(1 for y in range(height) if is_grid_red_pixel(*pixels[x, y]))
            if red_count >= height * GRID_LINE_SPAN_COVERAGE:
                hits.append(x)
        return filter_uniform_lines(cluster_positions(hits))

    hits = []
    for y in range(height):
        red_count = sum(1 for x in range(width) if is_grid_red_pixel(*pixels[x, y]))
        if red_count >= width * GRID_LINE_SPAN_COVERAGE:
            hits.append(y)
    return filter_uniform_lines(cluster_positions(hits))


def build_patch_boundaries(width: int, height: int, rows: int, cols: int) -> tuple[list[int], list[int]]:
    h_lines = [patch_box(width, height, row, 0, rows, cols)[1] for row in range(rows)]
    h_lines.append(height)
    v_lines = [patch_box(width, height, 0, col, rows, cols)[0] for col in range(cols)]
    v_lines.append(width)
    return h_lines, v_lines


def boundary_red_coverage(
    pixels: Any,
    width: int,
    height: int,
    *,
    axis: str,
    index: int,
) -> float:
    if axis == "horizontal":
        y = max(0, min(height - 1, index))
        red_count = sum(1 for x in range(width) if is_grid_red_pixel(*pixels[x, y]))
        return red_count / max(1, width)
    x = max(0, min(width - 1, index))
    red_count = sum(1 for y in range(height) if is_grid_red_pixel(*pixels[x, y]))
    return red_count / max(1, height)


def score_patch_grid_alignment(
    pixels: Any,
    width: int,
    height: int,
    rows: int,
    cols: int,
) -> float:
    """Higher score means patch_box boundaries coincide with full-span red grid lines."""
    score = 0.0

    for row in range(rows + 1):
        if row == 0:
            y = 0
        elif row == rows:
            y = height - 1
        else:
            y = patch_box(width, height, row, 0, rows, cols)[1]
        score += boundary_red_coverage(pixels, width, height, axis="horizontal", index=y)

    for col in range(cols + 1):
        if col == 0:
            x = 0
        elif col == cols:
            x = width - 1
        else:
            x = patch_box(width, height, 0, col, rows, cols)[0]
        score += boundary_red_coverage(pixels, width, height, axis="vertical", index=x)

    return score


def infer_cols_from_vertical_lines(v_lines: list[int], width: int) -> int | None:
    if len(v_lines) < 3:
        return None
    gaps = [v_lines[index + 1] - v_lines[index] for index in range(len(v_lines) - 1)]
    median_gap = statistics.median(gaps)
    if median_gap <= 0:
        return None
    if statistics.pstdev(gaps) > max(2.0, median_gap * 0.18):
        return None
    cols = len(v_lines) - 1
    if 2 <= cols <= 200 and abs(cols * median_gap - width) <= median_gap * 2:
        return cols
    return None


def infer_rows_for_cols(
    pixels: Any,
    width: int,
    height: int,
    cols: int,
) -> int:
    cell_width = width / cols
    min_rows = max(8, int(height / (cell_width * 1.8)))
    max_rows = min(120, max(min_rows + 1, int(height / (cell_width * 0.35))))
    best_rows = min_rows
    best_score = -1.0
    for rows in range(min_rows, max_rows + 1):
        score = score_patch_grid_alignment(pixels, width, height, rows, cols)
        if score > best_score:
            best_score = score
            best_rows = rows
    return best_rows


def infer_rows_cols_joint(
    pixels: Any,
    width: int,
    height: int,
) -> tuple[int, int]:
    best_score = -1.0
    best_rows = 20
    best_cols = 20
    max_cols = min(80, max(12, width // 12))
    for cols in range(8, max_cols + 1, 2):
        rows = infer_rows_for_cols(pixels, width, height, cols)
        score = score_patch_grid_alignment(pixels, width, height, rows, cols)
        if score > best_score:
            best_score = score
            best_rows = rows
            best_cols = cols
    return best_rows, best_cols


def infer_patch_grid_from_preview(image: Image.Image) -> dict[str, Any]:
    """Infer rows/cols from full-span red grid lines; boundaries match patch_box."""
    rgb = image.convert("RGB")
    width, height = rgb.size
    if width < 8 or height < 8:
        raise ValueError(f"Image too small for grid detection: {width}x{height}")

    pixels = rgb.load()
    v_lines = detect_span_lines(pixels, width, height, axis="vertical")
    cols = infer_cols_from_vertical_lines(v_lines, width)

    if cols is not None:
        rows = infer_rows_for_cols(pixels, width, height, cols)
    else:
        rows, cols = infer_rows_cols_joint(pixels, width, height)

    patch_count = rows * cols
    if rows < 2 or cols < 2 or patch_count > 8000:
        raise ValueError(
            f"Unreasonable grid size inferred ({rows}x{cols}). "
            "Check that the input image has a uniform red patch grid."
        )

    h_lines, v_lines = build_patch_boundaries(width, height, rows, cols)
    return {
        "rows": rows,
        "cols": cols,
        "patch_count": patch_count,
        "source": "inferred_from_grid_preview",
        "grid_source": "inferred",
        "validation": "ok",
        "h_lines": h_lines,
        "v_lines": v_lines,
    }


@dataclass(frozen=True)
class PageEntry:
    order: int
    image_index: int
    sample_id: str
    folder_name: str
    grid_image: Path


def is_grid_metadata_record(record: dict[str, Any]) -> bool:
    if "low_semantic_patches" in record or "suggested_low_semantic_patches" in record:
        return False
    return (
        isinstance(record, dict)
        and "rows" in record
        and "cols" in record
        and int(record["rows"]) > 0
        and int(record["cols"]) > 0
    )


def iter_json_objects(raw: Any) -> list[dict[str, Any]]:
    if isinstance(raw, dict):
        return [raw]
    if isinstance(raw, list):
        return [item for item in raw if isinstance(item, dict)]
    return []


def normalize_grid_metadata(record: dict[str, Any]) -> dict[str, Any]:
    rows = int(record["rows"])
    cols = int(record["cols"])
    expected_count = rows * cols
    patch_count = int(record.get("patch_count", expected_count))
    if patch_count != expected_count:
        print(
            f"Warning: patch_count={patch_count} does not match rows*cols={expected_count}; "
            f"using {expected_count}.",
            file=sys.stderr,
        )
        patch_count = expected_count
    meta = {
        "rows": rows,
        "cols": cols,
        "patch_count": patch_count,
        "source": "grid_metadata_json",
    }
    if "sample_id" in record:
        meta["sample_id"] = str(record["sample_id"])
    if "image_index" in record:
        meta["image_index"] = int(record["image_index"])
    return meta


def load_grid_metadata_index(patch_grids_dir: Path, extra_json: Path | None = None) -> dict[tuple[int, str], dict[str, Any]]:
    """Load rows/cols metadata keyed by (image_index, sample_id)."""
    index: dict[tuple[int, str], dict[str, Any]] = {}
    json_paths: list[Path] = []
    if patch_grids_dir.is_dir():
        json_paths.extend(sorted(patch_grids_dir.glob("*.json")))
    if extra_json is not None and extra_json.is_file():
        json_paths.append(extra_json)

    seen_paths: set[Path] = set()
    for path in json_paths:
        resolved = path.resolve()
        if resolved in seen_paths:
            continue
        seen_paths.add(resolved)
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"Warning: skip grid metadata {path}: {exc}", file=sys.stderr)
            continue

        for record in iter_json_objects(raw):
            if not is_grid_metadata_record(record):
                continue
            meta = normalize_grid_metadata(record)
            image_index = meta.get("image_index")
            sample_id = meta.get("sample_id")
            if image_index is None or not sample_id:
                continue
            index[(int(image_index), str(sample_id))] = meta

    return index


def find_sidecar_grid_metadata(entry: PageEntry) -> dict[str, Any] | None:
    candidates = [
        entry.grid_image.with_suffix(".json"),
        entry.grid_image.with_name(entry.folder_name + ".grid.json"),
    ]
    for path in candidates:
        if not path.is_file():
            continue
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        records = iter_json_objects(raw)
        if len(records) == 1 and is_grid_metadata_record(records[0]):
            return normalize_grid_metadata(records[0])
        for record in records:
            if not is_grid_metadata_record(record):
                continue
            if int(record.get("image_index", entry.image_index)) == entry.image_index:
                return normalize_grid_metadata(record)
    return None


def lookup_grid_metadata(
    entry: PageEntry,
    meta_index: dict[tuple[int, str], dict[str, Any]],
) -> dict[str, Any] | None:
    key = (entry.image_index, entry.sample_id)
    if key in meta_index:
        return meta_index[key]
    return find_sidecar_grid_metadata(entry)


def attach_patch_boundaries(
    width: int,
    height: int,
    grid: dict[str, Any],
) -> dict[str, Any]:
    rows = int(grid["rows"])
    cols = int(grid["cols"])
    h_lines, v_lines = build_patch_boundaries(width, height, rows, cols)
    return {
        **grid,
        "rows": rows,
        "cols": cols,
        "patch_count": int(grid.get("patch_count", rows * cols)),
        "h_lines": h_lines,
        "v_lines": v_lines,
    }


def resolve_patch_grid(
    entry: PageEntry,
    image: Image.Image,
    meta_index: dict[tuple[int, str], dict[str, Any]],
    *,
    validate_inference: bool = False,
) -> dict[str, Any]:
    """Prefer JSON rows/cols from sidecar metadata (fast). Infer from red lines only when missing."""
    width, height = image.size
    meta = lookup_grid_metadata(entry, meta_index)
    if meta is not None:
        json_grid = attach_patch_boundaries(width, height, meta)
        json_grid["grid_source"] = "json"
        json_grid["validation"] = "ok"
        if validate_inference:
            pixels = image.convert("RGB").load()
            inferred = infer_patch_grid_from_preview(image)
            json_rows = int(json_grid["rows"])
            json_cols = int(json_grid["cols"])
            score_json = score_patch_grid_alignment(pixels, width, height, json_rows, json_cols)
            score_inferred = score_patch_grid_alignment(
                pixels,
                width,
                height,
                int(inferred["rows"]),
                int(inferred["cols"]),
            )
            if (
                json_rows != int(inferred["rows"])
                or json_cols != int(inferred["cols"])
            ) and score_inferred > score_json + 3.0:
                json_grid["validation"] = "mismatch"
                print(
                    f"Warning: JSON grid {json_rows}x{json_cols} differs from image inference "
                    f"{inferred['rows']}x{inferred['cols']} for {entry.folder_name}. "
                    f"Using JSON metadata (alignment scores json={score_json:.1f}, "
                    f"inferred={score_inferred:.1f}).",
                    file=sys.stderr,
                )
            json_grid["inferred_rows"] = int(inferred["rows"])
            json_grid["inferred_cols"] = int(inferred["cols"])
        return json_grid

    return infer_patch_grid_from_preview(image)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Click patch-grid preview images to annotate low_semantic_patches."
    )
    default_grids = default_patch_grids_dir()
    parser.add_argument(
        "--patch-grids-dir",
        type=Path,
        default=None,
        help=(
            f"Grid-preview JPG directory (default: {INPUT_DIR} or {INPUT_PATCH_GRIDS_DIR} "
            f"when images are present)"
        ),
    )
    parser.add_argument(
        "--grid-meta-json",
        type=Path,
        default=None,
        help=(
            "Optional JSON file with rows/cols/patch_count per page "
            "(also auto-loads *.json from --patch-grids-dir)."
        ),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help=f"Single combined annotation JSON (default: {DEFAULT_OUTPUT})",
    )
    parser.add_argument(
        "--start-page",
        type=int,
        default=None,
        help=(
            "Initial page index (0-based) in sorted patch_grids list. "
            "Default: open at the last annotated page in --output JSON."
        ),
    )
    parser.add_argument(
        "--resume-skip-done",
        action="store_true",
        help="On startup, jump to the first page not yet present in --output JSON.",
    )
    parser.add_argument(
        "--no-start-dialog",
        action="store_true",
        help=(
            "Do not ask which page to open on startup. "
            "Useful for scripted runs or when keeping the old automatic behavior."
        ),
    )
    parser.add_argument(
        "--validate-grid-inference",
        action="store_true",
        help=(
            "With JSON metadata, also run slow red-line grid inference for mismatch warnings "
            "(not recommended; can freeze UI for several seconds per page)."
        ),
    )
    args = parser.parse_args()
    args.patch_grids_dir = resolve_data_path(args.patch_grids_dir or default_patch_grids_dir())
    if args.grid_meta_json is not None:
        args.grid_meta_json = resolve_data_path(args.grid_meta_json)
    args.output = resolve_tool_path(args.output)
    return args


def list_page_entries(patch_grids_dir: Path) -> list[PageEntry]:
    if not patch_grids_dir.is_dir():
        raise FileNotFoundError(f"patch_grids directory not found: {patch_grids_dir}")

    entries: list[PageEntry] = []
    for path in sorted(patch_grids_dir.iterdir()):
        if path.suffix.lower() not in IMAGE_EXTENSIONS:
            continue
        match = PATCH_GRID_FILENAME.match(path.name)
        if match is None:
            continue
        entries.append(
            PageEntry(
                order=int(match.group("order")),
                image_index=int(match.group("image_index")),
                sample_id=match.group("sample_id"),
                folder_name=path.stem,
                grid_image=path,
            )
        )
    entries.sort(key=lambda item: item.order)
    return entries


def load_page_context(
    entry: PageEntry,
    image: Image.Image | None = None,
    *,
    grid_meta_index: dict[tuple[int, str], dict[str, Any]] | None = None,
    validate_inference: bool = False,
) -> dict[str, Any]:
    source = image if image is not None else Image.open(entry.grid_image).convert("RGB")
    grid = resolve_patch_grid(
        entry,
        source,
        grid_meta_index or {},
        validate_inference=validate_inference,
    )
    grid_source = str(grid.get("grid_source", "inferred"))
    validation = str(grid.get("validation", "ok"))

    return {
        "sample_id": entry.sample_id,
        "image_index": entry.image_index,
        "patch_grid": {
            key: value
            for key, value in grid.items()
            if key
            not in {
                "h_lines",
                "v_lines",
                "grid_source",
                "validation",
                "inferred_rows",
                "inferred_cols",
            }
        },
        "h_lines": grid["h_lines"],
        "v_lines": grid["v_lines"],
        "grid_source": grid_source,
        "grid_validation": validation,
        "inferred_rows": grid.get("inferred_rows"),
        "inferred_cols": grid.get("inferred_cols"),
    }


def annotation_record_from_item(item: dict[str, Any]) -> dict[str, Any]:
    image_index = int(item["image_index"])
    patches = sorted(int(pid) for pid in item.get("low_semantic_patches", []))
    record: dict[str, Any] = {
        "sample_id": item["sample_id"],
        "image_index": image_index,
        "low_semantic_patches": patches,
    }
    if isinstance(item.get("patch_grid"), dict):
        record["patch_grid"] = item["patch_grid"]
    elif item.get("rows") is not None and item.get("cols") is not None:
        record["patch_grid"] = {
            "rows": int(item["rows"]),
            "cols": int(item["cols"]),
            "patch_count": int(item.get("patch_count", int(item["rows"]) * int(item["cols"]))),
        }
    elif item.get("_format_grid_cols") is not None:
        record["patch_grid"] = {"cols": int(item["_format_grid_cols"])}
    return record


def load_records_from_json(path: Path) -> dict[int, dict[str, Any]]:
    if not path.is_file():
        return {}

    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return {}

    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        print(
            f"Warning: invalid JSON in {path} ({exc}); starting with empty annotations.",
            file=sys.stderr,
        )
        return {}

    items: list[dict[str, Any]]
    if isinstance(raw, dict):
        if "images" in raw and isinstance(raw["images"], list):
            items = [item for item in raw["images"] if isinstance(item, dict)]
        else:
            items = [raw]
    elif isinstance(raw, list):
        items = [item for item in raw if isinstance(item, dict)]
    else:
        raise ValueError(f"Expected a JSON object or array in {path}")

    by_index: dict[int, dict[str, Any]] = {}
    for item in items:
        if "image_index" not in item or "sample_id" not in item:
            continue
        record = annotation_record_from_item(item)
        by_index[int(record["image_index"])] = record
    return by_index


def build_page_annotation_record(
    context: dict[str, Any],
    low_semantic_patches: list[int],
) -> dict[str, Any]:
    patch_grid = context["patch_grid"]
    rows = int(patch_grid["rows"])
    cols = int(patch_grid["cols"])
    return {
        "sample_id": context["sample_id"],
        "image_index": context["image_index"],
        "rows": rows,
        "cols": cols,
        "patch_count": int(patch_grid.get("patch_count", rows * cols)),
        "low_semantic_patches": sorted(low_semantic_patches),
    }


def ordered_annotation_records(
    entries: list[PageEntry],
    records_by_index: dict[int, dict[str, Any]],
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for entry in entries:
        record = records_by_index.get(entry.image_index)
        if record is None:
            continue
        records.append(record)
    return records


def record_for_legacy_export(record: dict[str, Any]) -> dict[str, Any]:
    cols = record.get("cols")
    if cols is None and isinstance(record.get("patch_grid"), dict):
        cols = record["patch_grid"].get("cols")
    exported = {
        "sample_id": record["sample_id"],
        "image_index": int(record["image_index"]),
        "low_semantic_patches": record["low_semantic_patches"],
    }
    if cols is not None:
        exported["_format_grid_cols"] = int(cols)
    return exported


def write_combined_annotation_json(
    path: Path,
    entries: list[PageEntry],
    records_by_index: dict[int, dict[str, Any]],
) -> None:
    records = ordered_annotation_records(entries, records_by_index)
    legacy_records = [record_for_legacy_export(record) for record in records]
    path.parent.mkdir(parents=True, exist_ok=True)
    write_legacy_annotation_json_rowwise(path, legacy_records)


def resolve_last_annotated_index(
    entries: list[PageEntry],
    records_by_index: dict[int, dict[str, Any]],
) -> int:
    """Index of the last page (by sorted order) that already exists in output JSON."""
    if not entries or not records_by_index:
        return 0
    last_index = 0
    found = False
    for index, entry in enumerate(entries):
        if entry.image_index in records_by_index:
            last_index = index
            found = True
    return last_index if found else 0


def resolve_start_index(
    entries: list[PageEntry],
    records_by_index: dict[int, dict[str, Any]],
    start_page: int | None,
    resume_skip_done: bool,
) -> int:
    if resume_skip_done:
        for index, entry in enumerate(entries):
            if entry.image_index not in records_by_index:
                return index
        return max(0, len(entries) - 1)

    if start_page is not None:
        if start_page < 0:
            return 0
        if start_page >= len(entries):
            return max(0, len(entries) - 1)
        return start_page

    return resolve_last_annotated_index(entries, records_by_index)


def prompt_start_index(
    root: tk.Tk,
    entries: list[PageEntry],
    default_index: int,
    *,
    start_page: int | None,
    resume_skip_done: bool,
    no_start_dialog: bool,
) -> int:
    """Ask for a 1-based start page unless command-line startup control is active."""
    if no_start_dialog or resume_skip_done or start_page is not None or not entries:
        return default_index

    page_count = len(entries)
    default_page = min(max(default_index + 1, 1), page_count)
    chosen = simpledialog.askinteger(
        "选择起始图片",
        (
            f"共有 {page_count} 张图片。\n"
            f"请输入要从第几张开始标注/复检（1-{page_count}）。\n\n"
            f"取消 = 自动打开默认位置（第 {default_page} 张）。"
        ),
        parent=root,
        minvalue=1,
        maxvalue=page_count,
        initialvalue=default_page,
    )
    if chosen is None:
        return default_index
    return chosen - 1


def patch_id_at_boundaries(
    orig_x: float,
    orig_y: float,
    h_lines: list[int],
    v_lines: list[int],
) -> int | None:
    if not h_lines or not v_lines:
        return None

    cols = len(v_lines) - 1
    rows = len(h_lines) - 1
    if cols < 1 or rows < 1:
        return None
    if orig_x < v_lines[0] or orig_y < h_lines[0]:
        return None
    if orig_x > v_lines[-1] or orig_y > h_lines[-1]:
        return None

    col = bisect.bisect_right(v_lines, orig_x) - 1
    row = bisect.bisect_right(h_lines, orig_y) - 1
    if col < 0 or row < 0 or col >= cols or row >= rows:
        return None
    return row * cols + col


def _cell_index_range(lines: list[int], lo: float, hi: float) -> tuple[int, int]:
    """Inclusive index range of cells [lines[i], lines[i+1]) intersecting [lo, hi]."""
    if len(lines) < 2:
        return 0, -1
    cell_count = len(lines) - 1
    if hi <= lines[0] or lo >= lines[-1]:
        return 0, -1

    first = bisect.bisect_right(lines, lo) - 1
    first = max(0, min(first, cell_count - 1))
    last = bisect.bisect_left(lines, hi) - 1
    last = max(0, min(last, cell_count - 1))
    if first > last:
        return 0, -1
    return first, last


def patches_in_rect(
    x0: float,
    y0: float,
    x1: float,
    y1: float,
    h_lines: list[int],
    v_lines: list[int],
) -> set[int]:
    """Return patch_ids whose cells intersect the axis-aligned rectangle in image coords."""
    xmin, xmax = min(x0, x1), max(x0, x1)
    ymin, ymax = min(y0, y1), max(y0, y1)
    cols = len(v_lines) - 1
    rows = len(h_lines) - 1
    if cols < 1 or rows < 1:
        return set()

    row_start, row_end = _cell_index_range(h_lines, ymin, ymax)
    col_start, col_end = _cell_index_range(v_lines, xmin, xmax)
    if row_start > row_end or col_start > col_end:
        return set()

    selected: set[int] = set()
    for row in range(row_start, row_end + 1):
        top = h_lines[row]
        bottom = h_lines[row + 1]
        if bottom <= ymin or top >= ymax:
            continue
        for col in range(col_start, col_end + 1):
            left = v_lines[col]
            right = v_lines[col + 1]
            if right <= xmin or left >= xmax:
                continue
            selected.add(row * cols + col)
    return selected


class GridPatchAnnotatorApp:
    def __init__(
        self,
        root: tk.Tk,
        entries: list[PageEntry],
        output_path: Path,
        records_by_index: dict[int, dict[str, Any]],
        grid_meta_index: dict[tuple[int, str], dict[str, Any]],
        start_index: int,
        *,
        validate_inference: bool = False,
    ) -> None:
        self.root = root
        self.entries = entries
        self.grid_meta_index = grid_meta_index
        self.output_path = output_path
        self.records_by_index = records_by_index
        self.validate_inference = validate_inference

        self.page_index = start_index
        self.context: dict[str, Any] | None = None
        self.entry: PageEntry | None = None
        self.low_semantic: set[int] = set()
        self.undo_stack: list[tuple[str, Any, ...]] = []
        self._drag_start_canvas: tuple[float, float] | None = None
        self._drag_press_state = 0
        self._drag_box_mode_at_press: str | None = None
        self._shift_held = False
        self._alt_held = False
        self._pointer_release_handled = False
        self._rubber_band_id: int | None = None

        self.image_width = 0
        self.image_height = 0
        self.rows = 0
        self.cols = 0
        self.h_lines: list[int] = []
        self.v_lines: list[int] = []
        self.photo: ImageTk.PhotoImage | None = None
        self._pil_image: Image.Image | None = None
        self.display_scale = 1.0
        self._save_job: str | None = None
        self._dirty = False

        self._build_ui()
        self.load_page(self.page_index)
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    def _build_ui(self) -> None:
        self.root.title("网格 Patch 标注工具")
        self.root.minsize(900, 640)

        toolbar = ttk.Frame(self.root, padding=6)
        toolbar.pack(side=tk.TOP, fill=tk.X)

        self.btn_prev = ttk.Button(toolbar, text="上一张", command=self.prev_page)
        self.btn_prev.pack(side=tk.LEFT, padx=(0, 6))
        self.btn_next = ttk.Button(toolbar, text="下一张", command=self.next_page)
        self.btn_next.pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(toolbar, text="保存", command=self.save_now).pack(side=tk.LEFT, padx=(0, 12))

        zoom_frame = ttk.Frame(toolbar)
        zoom_frame.pack(side=tk.LEFT, padx=(0, 12))
        ttk.Button(zoom_frame, text="放大", command=self.zoom_in).pack(side=tk.LEFT, padx=(0, 4))
        ttk.Button(zoom_frame, text="缩小", command=self.zoom_out).pack(side=tk.LEFT, padx=(0, 4))
        ttk.Button(zoom_frame, text="100%", command=self.zoom_reset).pack(side=tk.LEFT, padx=(0, 6))
        self.status_zoom = ttk.Label(zoom_frame, text="100%")
        self.status_zoom.pack(side=tk.LEFT)

        self.status_page = ttk.Label(toolbar, text="页 -/-")
        self.status_page.pack(side=tk.LEFT, padx=(0, 12))
        self.status_meta = ttk.Label(toolbar, text="")
        self.status_meta.pack(side=tk.LEFT, padx=(0, 12))
        self.status_count = ttk.Label(toolbar, text="已选低语义: 0")
        self.status_count.pack(side=tk.LEFT, padx=(0, 12))
        self.status_last = ttk.Label(toolbar, text="最近点击: -")
        self.status_last.pack(side=tk.LEFT)

        save_hint = ttk.Frame(self.root, padding=(6, 0, 6, 6))
        save_hint.pack(side=tk.TOP, fill=tk.X)
        ttk.Label(
            save_hint,
            text=f"保存到（单一 JSON，含全部已标注页）: {self.output_path}",
            wraplength=860,
        ).pack(anchor=tk.W)
        ttk.Label(
            save_hint,
            text="操作: 单击切换格子 | Shift+拖动框选添加 | Alt+拖动框选移除 | 滚轮滚动 | Ctrl+滚轮缩放",
            wraplength=860,
        ).pack(anchor=tk.W)

        canvas_frame = ttk.Frame(self.root)
        canvas_frame.pack(side=tk.TOP, fill=tk.BOTH, expand=True)

        self.canvas = tk.Canvas(canvas_frame, background="#2b2b2b", highlightthickness=0)
        v_scroll = ttk.Scrollbar(canvas_frame, orient=tk.VERTICAL, command=self.canvas.yview)
        h_scroll = ttk.Scrollbar(canvas_frame, orient=tk.HORIZONTAL, command=self.canvas.xview)
        self.canvas.configure(xscrollcommand=h_scroll.set, yscrollcommand=v_scroll.set)

        self.canvas.grid(row=0, column=0, sticky="nsew")
        v_scroll.grid(row=0, column=1, sticky="ns")
        h_scroll.grid(row=1, column=0, sticky="ew")
        canvas_frame.rowconfigure(0, weight=1)
        canvas_frame.columnconfigure(0, weight=1)

        self._bind_modifier_tracking()
        self.canvas.bind("<ButtonPress-1>", self.on_canvas_press)
        self.canvas.bind("<B1-Motion>", self.on_canvas_drag)
        self.root.bind_all("<ButtonRelease-1>", self.on_pointer_release, add="+")
        self.canvas.bind("<MouseWheel>", self.on_mousewheel)
        self.canvas.bind("<Button-4>", self.on_scroll_up)
        self.canvas.bind("<Button-5>", self.on_scroll_down)
        self.root.bind("<Control-s>", lambda _event: self.save_now())
        self.root.bind("<Control-plus>", lambda _event: self.zoom_in())
        self.root.bind("<Control-equal>", lambda _event: self.zoom_in())
        self.root.bind("<Control-minus>", lambda _event: self.zoom_out())
        self.root.bind("<Control-0>", lambda _event: self.zoom_reset())
        self.root.bind("<Left>", lambda _event: self.prev_page())
        self.root.bind("<Right>", lambda _event: self.next_page())
        self.root.bind("z", self.on_undo)
        self.root.bind("Z", self.on_undo)
        self.root.bind("<BackSpace>", self.on_undo)

    def load_page(self, index: int) -> None:
        if index < 0 or index >= len(self.entries):
            return

        if self._dirty:
            self.persist_current_page()
            self.flush_save()

        self.page_index = index
        self.entry = self.entries[index]
        self.undo_stack.clear()
        self._reset_pointer_gesture()

        image_index = self.entry.image_index
        saved = self.records_by_index.get(image_index)

        self.root.config(cursor="watch")
        self.root.update_idletasks()
        try:
            self._pil_image = Image.open(self.entry.grid_image).convert("RGB")
            self.context = load_page_context(
                self.entry,
                self._pil_image,
                grid_meta_index=self.grid_meta_index,
                validate_inference=self.validate_inference,
            )
        except (OSError, ValueError) as exc:
            messagebox.showerror("加载失败", str(exc))
            self._pil_image = None
            return
        finally:
            self.root.config(cursor="")

        patch_grid = self.context["patch_grid"]
        self.rows = int(patch_grid["rows"])
        self.cols = int(patch_grid["cols"])
        self.h_lines = list(self.context["h_lines"])
        self.v_lines = list(self.context["v_lines"])

        if saved is not None:
            self.low_semantic = set(int(pid) for pid in saved.get("low_semantic_patches", []))
        else:
            self.low_semantic = set()
        self.image_width, self.image_height = self._pil_image.size
        self.display_scale = 1.0
        self._refresh_canvas_view(reset_scroll=True)
        self._update_status(last_patch=None)
        self._dirty = False
        self._update_nav_buttons()

    def _display_size(self) -> tuple[int, int]:
        width = max(1, int(round(self.image_width * self.display_scale)))
        height = max(1, int(round(self.image_height * self.display_scale)))
        return width, height

    def _refresh_canvas_view(self, *, reset_scroll: bool = False) -> None:
        if self._pil_image is None:
            return

        display_width, display_height = self._display_size()
        if self.display_scale == 1.0:
            display_image = self._pil_image
        else:
            display_image = self._pil_image.resize(
                (display_width, display_height),
                Image.Resampling.LANCZOS,
            )

        self.photo = ImageTk.PhotoImage(display_image)
        self.canvas.delete("all")
        self.canvas.create_image(0, 0, image=self.photo, anchor=tk.NW, tags=("image",))
        self.canvas.configure(scrollregion=(0, 0, display_width, display_height))
        if reset_scroll:
            self.canvas.xview_moveto(0)
            self.canvas.yview_moveto(0)
        self._redraw_overlay()
        self._update_zoom_status()

    def _canvas_to_image_xy(self, canvas_x: float, canvas_y: float) -> tuple[float, float]:
        scale = self.display_scale if self.display_scale > 0 else 1.0
        return canvas_x / scale, canvas_y / scale

    def zoom_by(self, factor: float, event: tk.Event | None = None) -> None:
        if self._pil_image is None:
            return

        old_scale = self.display_scale
        new_scale = max(ZOOM_MIN, min(ZOOM_MAX, old_scale * factor))
        if abs(new_scale - old_scale) < 1e-9:
            return

        if event is not None:
            widget_x = event.x
            widget_y = event.y
        else:
            widget_x = max(0, self.canvas.winfo_width() // 2)
            widget_y = max(0, self.canvas.winfo_height() // 2)

        focus_x = self.canvas.canvasx(widget_x)
        focus_y = self.canvas.canvasy(widget_y)
        image_x = focus_x / old_scale
        image_y = focus_y / old_scale

        self.display_scale = new_scale
        self._refresh_canvas_view()

        display_width, display_height = self._display_size()
        new_focus_x = image_x * new_scale
        new_focus_y = image_y * new_scale
        left = max(0.0, new_focus_x - widget_x)
        top = max(0.0, new_focus_y - widget_y)
        if display_width > 0:
            self.canvas.xview_moveto(min(1.0, left / display_width))
        if display_height > 0:
            self.canvas.yview_moveto(min(1.0, top / display_height))

    def zoom_in(self, event: tk.Event | None = None) -> None:
        self.zoom_by(ZOOM_STEP, event)

    def zoom_out(self, event: tk.Event | None = None) -> None:
        self.zoom_by(1.0 / ZOOM_STEP, event)

    def zoom_reset(self, _event: tk.Event | None = None) -> None:
        if self._pil_image is None:
            return
        self.display_scale = 1.0
        self._refresh_canvas_view(reset_scroll=False)

    def _wheel_ctrl_pressed(self, event: tk.Event) -> bool:
        return bool(getattr(event, "state", 0) & TK_CTRL_MASK)

    def _scroll_canvas_vertical(self, event: tk.Event, *, direction: int | None = None) -> None:
        if direction is not None:
            steps = direction * WHEEL_SCROLL_UNITS
        else:
            delta = getattr(event, "delta", 0)
            if delta == 0:
                return
            if abs(delta) >= 120:
                steps = int(-delta / 120) * WHEEL_SCROLL_UNITS
            else:
                steps = (-1 if delta > 0 else 1) * WHEEL_SCROLL_UNITS
        self.canvas.yview_scroll(steps, "units")

    def on_mousewheel(self, event: tk.Event) -> None:
        if self._pil_image is None:
            return
        if self._wheel_ctrl_pressed(event):
            delta = getattr(event, "delta", 0)
            if delta == 0:
                return
            if delta > 0:
                self.zoom_in(event)
            else:
                self.zoom_out(event)
            return
        self._scroll_canvas_vertical(event)

    def on_scroll_up(self, event: tk.Event) -> None:
        if self._pil_image is None:
            return
        if self._wheel_ctrl_pressed(event):
            self.zoom_in(event)
        else:
            self._scroll_canvas_vertical(event, direction=-1)

    def on_scroll_down(self, event: tk.Event) -> None:
        if self._pil_image is None:
            return
        if self._wheel_ctrl_pressed(event):
            self.zoom_out(event)
        else:
            self._scroll_canvas_vertical(event, direction=1)

    def _update_zoom_status(self) -> None:
        self.status_zoom.configure(text=f"{int(round(self.display_scale * 100))}%")

    def _bind_modifier_tracking(self) -> None:
        """Track Shift/Alt globally — ButtonPress state alone is unreliable on Windows."""
        root = self.root

        def shift_down(_event: tk.Event) -> None:
            self._shift_held = True

        def shift_up(_event: tk.Event) -> None:
            self._shift_held = False

        def alt_down(_event: tk.Event) -> None:
            self._alt_held = True

        def alt_up(_event: tk.Event) -> None:
            self._alt_held = False

        for sequence, handler in (
            ("<KeyPress-Shift_L>", shift_down),
            ("<KeyPress-Shift_R>", shift_down),
            ("<KeyRelease-Shift_L>", shift_up),
            ("<KeyRelease-Shift_R>", shift_up),
            ("<KeyPress-Alt_L>", alt_down),
            ("<KeyPress-Alt_R>", alt_down),
            ("<KeyRelease-Alt_L>", alt_up),
            ("<KeyRelease-Alt_R>", alt_up),
        ):
            root.bind_all(sequence, handler, add="+")

    def _box_select_mode_from_state(self, state: int) -> str | None:
        """Detect Shift=add / Alt=remove. Avoid Mod1 (0x20000) — false positives on Windows."""
        if state & TK_SHIFT_MASK:
            return "add"
        if state & TK_ALT_MASK:
            return "remove"
        return None

    def _box_select_mode_from_event(self, event: tk.Event) -> str | None:
        keysym = str(getattr(event, "keysym", "") or "")
        if keysym in ("Shift_L", "Shift_R"):
            return "add"
        if keysym in ("Alt_L", "Alt_R", "Meta_L", "Meta_R"):
            return "remove"
        return self._box_select_mode_from_state(int(getattr(event, "state", 0)))

    def _active_box_mode(self, event: tk.Event | None = None) -> str | None:
        if self._shift_held:
            return "add"
        if self._alt_held:
            return "remove"
        if self._drag_box_mode_at_press is not None:
            return self._drag_box_mode_at_press
        if event is not None:
            mode = self._box_select_mode_from_event(event)
            if mode is not None:
                return mode
        return self._box_select_mode_from_state(self._drag_press_state)

    def _event_canvas_xy(self, event: tk.Event) -> tuple[float, float]:
        return self.canvas.canvasx(event.x), self.canvas.canvasy(event.y)

    def _drag_distance_px(self, x0: float, y0: float, x1: float, y1: float) -> float:
        return ((x1 - x0) ** 2 + (y1 - y0) ** 2) ** 0.5

    def _clear_rubber_band(self) -> None:
        self.canvas.delete("rubber")
        self._rubber_band_id = None

    def _update_rubber_band(self, x0: float, y0: float, x1: float, y1: float) -> None:
        if self._rubber_band_id is None:
            self._rubber_band_id = self.canvas.create_rectangle(
                x0,
                y0,
                x1,
                y1,
                outline=RUBBER_BAND_OUTLINE,
                dash=(4, 4),
                width=2,
                tags=("rubber",),
            )
        else:
            self.canvas.coords(self._rubber_band_id, x0, y0, x1, y1)
        self.canvas.tag_raise("rubber")

    def _push_undo_single(self, patch_id: int, action: str) -> None:
        self.undo_stack.append(("single", patch_id, action))

    def _push_undo_batch(self, patch_ids: set[int], action: str) -> None:
        if patch_ids:
            self.undo_stack.append(("batch", frozenset(patch_ids), action))

    def _toggle_patch(self, patch_id: int) -> None:
        if patch_id in self.low_semantic:
            self.low_semantic.remove(patch_id)
            self._push_undo_single(patch_id, "remove")
        else:
            self.low_semantic.add(patch_id)
            self._push_undo_single(patch_id, "add")
        self._redraw_overlay()
        self._update_status(last_patch=patch_id)
        self._dirty = True
        self.persist_current_page()
        self.schedule_save()

    def _apply_box_selection(
        self,
        image_x0: float,
        image_y0: float,
        image_x1: float,
        image_y1: float,
        mode: str,
    ) -> None:
        patch_ids = patches_in_rect(
            image_x0,
            image_y0,
            image_x1,
            image_y1,
            self.h_lines,
            self.v_lines,
        )
        if not patch_ids:
            return

        if mode == "add":
            changed = patch_ids - self.low_semantic
            self.low_semantic |= patch_ids
            self._push_undo_batch(changed, "add")
        else:
            changed = patch_ids & self.low_semantic
            self.low_semantic -= patch_ids
            self._push_undo_batch(changed, "remove")

        if not changed:
            return

        self._redraw_overlay()
        self._update_status(last_patch=max(changed) if changed else None)
        self.status_last.configure(
            text=f"框选{'添加' if mode == 'add' else '移除'}: {len(changed)} patches"
        )
        self._dirty = True
        self.persist_current_page()
        self.schedule_save()

    def _event_canvas_xy_for_release(self, event: tk.Event) -> tuple[float, float]:
        if event.widget == self.canvas:
            return self._event_canvas_xy(event)
        return (
            self.canvas.canvasx(event.x_root - self.canvas.winfo_rootx()),
            self.canvas.canvasy(event.y_root - self.canvas.winfo_rooty()),
        )

    def _finish_pointer_gesture(self, event: tk.Event) -> None:
        if self._pointer_release_handled:
            return
        if self.context is None or self._drag_start_canvas is None:
            self._reset_pointer_gesture()
            return

        self._pointer_release_handled = True
        x0, y0 = self._drag_start_canvas
        x1, y1 = self._event_canvas_xy_for_release(event)
        box_mode = self._active_box_mode(event)
        drag_distance = self._drag_distance_px(x0, y0, x1, y1)
        self._reset_pointer_gesture()

        if box_mode is not None and drag_distance >= DRAG_CLICK_THRESHOLD_PX:
            ix0, iy0 = self._canvas_to_image_xy(x0, y0)
            ix1, iy1 = self._canvas_to_image_xy(x1, y1)
            self._apply_box_selection(ix0, iy0, ix1, iy1, box_mode)
            return

        click_x, click_y = (x0, y0) if drag_distance < DRAG_CLICK_THRESHOLD_PX else (x1, y1)
        self._handle_single_click(click_x, click_y)

    def _handle_single_click(self, canvas_x: float, canvas_y: float) -> None:
        image_x, image_y = self._canvas_to_image_xy(canvas_x, canvas_y)
        patch_id = patch_id_at_boundaries(image_x, image_y, self.h_lines, self.v_lines)
        if patch_id is None:
            return
        self._toggle_patch(patch_id)

    def _reset_pointer_gesture(self) -> None:
        self._drag_start_canvas = None
        self._drag_press_state = 0
        self._drag_box_mode_at_press = None
        self._clear_rubber_band()

    def on_canvas_press(self, event: tk.Event) -> None:
        if self.context is None or self.entry is None:
            return
        self._pointer_release_handled = False
        self._reset_pointer_gesture()
        self._drag_start_canvas = self._event_canvas_xy(event)
        self._drag_press_state = int(getattr(event, "state", 0))
        self._drag_box_mode_at_press = self._active_box_mode(event)
        self.canvas.focus_set()

    def on_canvas_drag(self, event: tk.Event) -> None:
        if self._drag_start_canvas is None:
            return
        if self._active_box_mode(event) is None:
            return

        x0, y0 = self._drag_start_canvas
        x1, y1 = self._event_canvas_xy(event)
        if self._drag_distance_px(x0, y0, x1, y1) < DRAG_CLICK_THRESHOLD_PX:
            return

        self._update_rubber_band(x0, y0, x1, y1)

    def on_pointer_release(self, event: tk.Event) -> None:
        if self._drag_start_canvas is None:
            return
        self._finish_pointer_gesture(event)

    def on_undo(self, _event: tk.Event | None = None) -> None:
        if not self.undo_stack or self.context is None:
            return
        entry = self.undo_stack.pop()
        last_patch: int | None = None
        if entry[0] == "single":
            patch_id = int(entry[1])
            action = str(entry[2])
            if action == "add":
                self.low_semantic.discard(patch_id)
            else:
                self.low_semantic.add(patch_id)
            last_patch = patch_id
        elif entry[0] == "batch":
            patch_ids = set(entry[1])
            action = str(entry[2])
            if action == "add":
                self.low_semantic -= patch_ids
            else:
                self.low_semantic |= patch_ids
            if patch_ids:
                last_patch = max(patch_ids)

        self._redraw_overlay()
        self._update_status(last_patch=last_patch)
        self._dirty = True
        self.persist_current_page()
        self.schedule_save()

    def _redraw_overlay(self) -> None:
        self.canvas.delete("overlay")
        scale = self.display_scale
        for patch_id in sorted(self.low_semantic):
            row = patch_id // self.cols
            col = patch_id % self.cols
            if row >= self.rows or col >= self.cols:
                continue
            left = self.v_lines[col]
            upper = self.h_lines[row]
            right = self.v_lines[col + 1]
            lower = self.h_lines[row + 1]
            self.canvas.create_rectangle(
                left * scale,
                upper * scale,
                right * scale - 1,
                lower * scale - 1,
                fill=OVERLAY_FILL,
                outline=OVERLAY_OUTLINE,
                stipple=OVERLAY_STIPPLE,
                width=max(1, int(round(scale))),
                tags=("overlay",),
            )
        self.canvas.tag_raise("overlay")

    def persist_current_page(self) -> None:
        if self.context is None:
            return
        record = build_page_annotation_record(self.context, sorted(self.low_semantic))
        self.records_by_index[int(self.context["image_index"])] = record

    def flush_save(self) -> None:
        if self._save_job is not None:
            self.root.after_cancel(self._save_job)
            self._save_job = None
        write_combined_annotation_json(self.output_path, self.entries, self.records_by_index)
        self._dirty = False

    def schedule_save(self) -> None:
        if self._save_job is not None:
            self.root.after_cancel(self._save_job)

        def _do_save() -> None:
            self._save_job = None
            self.flush_save()

        self._save_job = self.root.after(SAVE_DEBOUNCE_MS, _do_save)

    def save_now(self) -> None:
        if self.context is not None:
            self.persist_current_page()
        self.flush_save()

    def prev_page(self) -> None:
        if self.page_index <= 0:
            return
        self.load_page(self.page_index - 1)

    def next_page(self) -> None:
        if self.page_index >= len(self.entries) - 1:
            messagebox.showinfo("完成", "已是最后一张图片。")
            return
        self.load_page(self.page_index + 1)

    def _update_nav_buttons(self) -> None:
        self.btn_prev.config(state=tk.NORMAL if self.page_index > 0 else tk.DISABLED)
        self.btn_next.config(
            state=tk.NORMAL if self.page_index < len(self.entries) - 1 else tk.DISABLED
        )

    def _update_status(self, last_patch: int | None) -> None:
        total = len(self.entries)
        page_no = self.page_index + 1
        self.status_page.configure(text=f"页 {page_no}/{total}")

        if self.entry is not None and self.context is not None:
            source = self.context.get("grid_source", "inferred")
            source_label = "JSON" if source == "json" else "识别"
            mismatch = ""
            if self.context.get("grid_validation") == "mismatch":
                inf_r = self.context.get("inferred_rows")
                inf_c = self.context.get("inferred_cols")
                mismatch = f"  (识别={inf_r}x{inf_c})"
            self.status_meta.configure(
                text=(
                    f"image_index={self.context['image_index']}  "
                    f"sample_id={self.context['sample_id']}  "
                    f"grid={self.rows}x{self.cols} ({source_label}){mismatch}  "
                    f"{self.entry.folder_name}"
                )
            )
            self.root.title(
                f"网格标注 — {self.context['sample_id']} ({self.entry.folder_name})"
            )
        else:
            self.status_meta.configure(text="")

        self.status_count.configure(text=f"已选低语义: {len(self.low_semantic)} patches")
        if last_patch is None:
            self.status_last.configure(text="最近点击: -")
        else:
            self.status_last.configure(text=f"最近点击: patch_id={last_patch}")

    def on_close(self) -> None:
        if self.context is not None:
            self.persist_current_page()
        self.flush_save()
        self.root.destroy()


def default_grid_meta_json() -> Path | None:
    if DEFAULT_GRID_META_JSON.is_file():
        return DEFAULT_GRID_META_JSON
    return None


def main() -> None:
    check_runtime_dependencies()
    ensure_input_layout()
    args = parse_args()
    entries = list_page_entries(args.patch_grids_dir)
    if not entries:
        raise SystemExit(
            "未找到网格预览图。\n"
            f"请将 JPG 放入:\n"
            f"  {INPUT_PATCH_GRIDS_DIR}\n"
            "文件名示例: 0000_131_loungers_2023.jpg\n"
            "详见本目录 使用说明.txt"
        )

    records_by_index = load_records_from_json(args.output)

    extra_meta = args.grid_meta_json
    if extra_meta is None:
        extra_meta = default_grid_meta_json()
    grid_meta_index = load_grid_metadata_index(args.patch_grids_dir, extra_meta)
    if grid_meta_index:
        print(
            f"Loaded grid metadata for {len(grid_meta_index)} page(s) from JSON.",
            file=sys.stderr,
        )

    default_start_index = resolve_start_index(
        entries,
        records_by_index,
        args.start_page,
        args.resume_skip_done,
    )

    root = tk.Tk()
    root.withdraw()
    start_index = prompt_start_index(
        root,
        entries,
        default_start_index,
        start_page=args.start_page,
        resume_skip_done=args.resume_skip_done,
        no_start_dialog=args.no_start_dialog,
    )
    if start_index > 0 or records_by_index:
        entry = entries[start_index]
        print(
            f"Opening page {start_index + 1}/{len(entries)} "
            f"(image_index={entry.image_index}, {entry.folder_name}).",
            file=sys.stderr,
        )

    root.deiconify()
    GridPatchAnnotatorApp(
        root=root,
        entries=entries,
        output_path=args.output,
        records_by_index=records_by_index,
        grid_meta_index=grid_meta_index,
        start_index=start_index,
        validate_inference=args.validate_grid_inference,
    )
    root.mainloop()


if __name__ == "__main__":
    main()
