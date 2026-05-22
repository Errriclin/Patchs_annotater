#!/usr/bin/env python3
"""Build a ViDoRe2 image-patch annotation template with ColQwen3 defaults.

The script samples images from a local dataset, runs the local ColQwen3 processor
to recover the default visual-token grid, and writes a JSON annotation template.
Patch ids are row-major over the ColQwen3 merged visual-token grid:

    patch_id = row * num_cols + col

Writes one JSON file per sampled image under ``--json-output-dir`` (basename
matches the grid JPG). Each file has ``sample_id``, ``image_index``, ``rows``,
``cols``, ``patch_count``, and optional ``grid_preview``. Use ``--auto-suggest``
to add ``suggested_low_semantic_patches`` in that same per-image file.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import random
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence


IMAGE_EXTENSIONS = {".bmp", ".gif", ".jpeg", ".jpg", ".png", ".tif", ".tiff", ".webp"}
COMMON_IMAGE_COLUMNS = ("image", "images", "page_image", "page", "img", "picture")
COMMON_ID_COLUMNS = ("sample_id", "id", "doc-id", "doc_id", "corpus-id", "corpus_id", "image_id")


@dataclass(frozen=True)
class ImageSample:
    sample_id: str
    image_index: int
    image: Any
    source: str


@dataclass(frozen=True)
class PatchGrid:
    rows: int
    cols: int
    raw_grid_thw: tuple[int, int, int]
    merge_size: int

    @property
    def count(self) -> int:
        return self.rows * self.cols


def dedupe_samples_by_image_index(samples: list[ImageSample]) -> list[ImageSample]:
    """Keep one entry per image_index so random sampling never repeats the same page."""
    seen: set[int] = set()
    unique: list[ImageSample] = []
    for sample in samples:
        if sample.image_index in seen:
            continue
        seen.add(sample.image_index)
        unique.append(sample)
    dropped = len(samples) - len(unique)
    if dropped:
        print(f"Dropped {dropped} duplicate image_index entries before sampling.", file=sys.stderr)
    return unique


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Sample local ViDoRe2 images and create ColQwen3 patch annotation JSON."
    )
    parser.add_argument("--dataset-path", required=True, help="Local dataset path, image directory, or manifest JSON/JSONL.")
    parser.add_argument("--processor-path", required=True, help="Local ColQwen3 processor/model directory.")
    parser.add_argument(
        "--json-output-dir",
        default=None,
        help="Directory for one JSON per image (default: same as --grid-output-dir).",
    )
    parser.add_argument(
        "--sample-size",
        type=int,
        default=500,
        help="Number of distinct images to sample (random without replacement).",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="Random seed. Use different values (e.g. %%RANDOM%% on Windows) for a new draw.",
    )
    parser.add_argument("--split", default="test", help="Dataset split to read when using datasets.")
    parser.add_argument(
        "--dataset-name",
        default=None,
        help=(
            "Optional HF dataset name, for example vidore/esg_reports_v2. "
            "When set, --dataset-path is used as cache_dir/local cache root."
        ),
    )
    parser.add_argument(
        "--dataset-config",
        default=None,
        help="Optional HF dataset config, for example corpus or docs.",
    )
    parser.add_argument("--image-column", default=None, help="Override image column name for dataset/manifest input.")
    parser.add_argument("--id-column", default=None, help="Override sample id column name for dataset/manifest input.")
    parser.add_argument(
        "--trust-remote-code",
        action="store_true",
        default=True,
        help="Allow local custom ColQwen3 processor code. Enabled by default.",
    )
    parser.add_argument(
        "--no-trust-remote-code",
        action="store_false",
        dest="trust_remote_code",
        help="Disable trust_remote_code when loading the processor.",
    )
    parser.add_argument(
        "--max-num-visual-tokens",
        type=int,
        default=None,
        help="Optional override passed to ColQwen3Processor.from_pretrained.",
    )
    parser.add_argument(
        "--grid-output-dir",
        default=None,
        help="Optional directory for numbered patch-grid preview images.",
    )
    parser.add_argument(
        "--grid-line-width",
        type=int,
        default=2,
        help="Line width in pixels for patch-grid preview images.",
    )
    parser.add_argument(
        "--grid-label-font-size",
        type=int,
        default=12,
        help="Font size in pixels for patch id labels in patch-grid preview images.",
    )
    parser.add_argument(
        "--auto-suggest",
        action="store_true",
        help="Add suggested_low_semantic_patches to each per-image JSON file.",
    )
    parser.add_argument(
        "--low-detail-std-threshold",
        type=float,
        default=12.0,
        help="Patch grayscale standard-deviation threshold used by the heuristic suggestion mode.",
    )
    parser.add_argument(
        "--low-detail-edge-threshold",
        type=float,
        default=6.0,
        help="Patch edge proxy threshold used by the heuristic suggestion mode.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    dataset_path = Path(args.dataset_path)
    processor_path = Path(args.processor_path)

    processor = load_processor(processor_path, args.trust_remote_code, args.max_num_visual_tokens)
    samples = list(iter_image_samples(args, dataset_path))
    if not samples:
        raise ValueError(f"No images were found in dataset path: {dataset_path}")

    samples = dedupe_samples_by_image_index(samples)
    rng = random.Random(args.seed)
    sample_size = min(args.sample_size, len(samples))
    selected = rng.sample(samples, sample_size)

    grid_output_dir = Path(args.grid_output_dir) if args.grid_output_dir else None
    json_output_dir = Path(args.json_output_dir) if args.json_output_dir else grid_output_dir
    if json_output_dir is None:
        raise ValueError("Set --json-output-dir or --grid-output-dir to write per-image JSON files.")
    json_output_dir.mkdir(parents=True, exist_ok=True)
    if grid_output_dir is not None:
        grid_output_dir.mkdir(parents=True, exist_ok=True)

    written_json_paths: list[tuple[Path, dict[str, Any]]] = []
    for output_index, sample in enumerate(selected):
        image = sample.image.convert("RGB")
        grid = get_colqwen3_patch_grid(processor, image)
        page_stem = f"{output_index:04d}_{sample.image_index}_{safe_filename(sample.sample_id)}"
        preview_name: str | None = None
        if grid_output_dir is not None:
            preview_name = f"{page_stem}.jpg"
            save_patch_grid_preview(
                image,
                grid,
                grid_output_dir / preview_name,
                line_width=args.grid_line_width,
                label_font_size=args.grid_label_font_size,
            )

        entry: dict[str, Any] = {
            "sample_id": sample.sample_id,
            "image_index": sample.image_index,
            "rows": grid.rows,
            "cols": grid.cols,
            "patch_count": grid.count,
        }
        if preview_name is not None:
            entry["grid_preview"] = preview_name
        if args.auto_suggest:
            entry["suggested_low_semantic_patches"] = suggest_low_semantic_patches(
                image,
                grid,
                std_threshold=args.low_detail_std_threshold,
                edge_threshold=args.low_detail_edge_threshold,
            )

        json_path = json_output_dir / f"{page_stem}.json"
        write_json(json_path, entry)
        written_json_paths.append((json_path, entry))

    print(
        f"Wrote {len(written_json_paths)} JSON file(s) to {json_output_dir}. "
        f"Available images: {len(samples)}; seed: {args.seed}."
    )
    for json_path, record in written_json_paths:
        preview = record.get("grid_preview", "")
        preview_note = f", grid_preview={preview}" if preview else ""
        print(
            f"  {json_path.name}: {record['sample_id']} (image_index={record['image_index']}) "
            f"{record['rows']}x{record['cols']} = {record['patch_count']} patches{preview_note}"
        )


def load_processor(processor_path: Path, trust_remote_code: bool, max_num_visual_tokens: int | None) -> Any:
    try:
        from transformers import AutoProcessor
    except ImportError as exc:
        raise ImportError("Please install transformers before running this script.") from exc

    kwargs: dict[str, Any] = {
        "local_files_only": True,
        "trust_remote_code": trust_remote_code,
    }
    if max_num_visual_tokens is not None:
        kwargs["max_num_visual_tokens"] = max_num_visual_tokens

    return AutoProcessor.from_pretrained(str(processor_path), **kwargs)


def require_pillow() -> tuple[Any, Any, Any, Any]:
    try:
        from PIL import Image, ImageDraw, ImageFont, ImageStat
    except ImportError as exc:
        raise ImportError("Please install Pillow before loading images: pip install Pillow") from exc
    return Image, ImageDraw, ImageFont, ImageStat


def iter_image_samples(args: argparse.Namespace, dataset_path: Path) -> Iterator[ImageSample]:
    if args.dataset_name:
        yield from iter_hf_dataset(
            dataset_path=dataset_path,
            dataset_name=args.dataset_name,
            dataset_config=args.dataset_config,
            split=args.split,
            image_column=args.image_column,
            id_column=args.id_column,
        )
        return

    if dataset_path.is_file() and dataset_path.suffix.lower() in {".json", ".jsonl"}:
        yield from iter_manifest(dataset_path, args.image_column, args.id_column)
        return

    if dataset_path.is_dir():
        try:
            yield from iter_load_from_disk(dataset_path, args.split, args.image_column, args.id_column)
            return
        except Exception:
            pass

        image_paths = sorted(path for path in dataset_path.rglob("*") if path.suffix.lower() in IMAGE_EXTENSIONS)
        if image_paths:
            Image, _, _, _ = require_pillow()
            for index, image_path in enumerate(image_paths):
                yield ImageSample(
                    sample_id=str(image_path.relative_to(dataset_path)).replace("\\", "/"),
                    image_index=index,
                    image=Image.open(image_path).convert("RGB"),
                    source=str(image_path),
                )
            return

    raise ValueError(
        "Could not load images. Provide a load_from_disk dataset, an image directory, a JSON/JSONL manifest, "
        "or pass --dataset-name/--dataset-config for a local Hugging Face cache."
    )


def iter_hf_dataset(
    dataset_path: Path,
    dataset_name: str,
    dataset_config: str | None,
    split: str,
    image_column: str | None,
    id_column: str | None,
) -> Iterator[ImageSample]:
    try:
        from datasets import load_dataset
    except ImportError as exc:
        raise ImportError("Please install datasets before reading a Hugging Face dataset cache.") from exc

    previous_offline = os.environ.get("HF_DATASETS_OFFLINE")
    os.environ["HF_DATASETS_OFFLINE"] = "1"
    try:
        dataset = load_dataset(
            dataset_name,
            dataset_config,
            split=split,
            cache_dir=str(dataset_path),
            trust_remote_code=True,
        )
    finally:
        if previous_offline is None:
            os.environ.pop("HF_DATASETS_OFFLINE", None)
        else:
            os.environ["HF_DATASETS_OFFLINE"] = previous_offline

    yield from iter_dataset_rows(dataset, image_column, id_column)


def iter_load_from_disk(
    dataset_path: Path,
    split: str,
    image_column: str | None,
    id_column: str | None,
) -> Iterator[ImageSample]:
    try:
        from datasets import DatasetDict, load_from_disk
    except ImportError as exc:
        raise ImportError("Please install datasets before reading a saved Hugging Face dataset.") from exc

    loaded = load_from_disk(str(dataset_path))
    if isinstance(loaded, DatasetDict):
        if split not in loaded:
            raise KeyError(f"Split {split!r} not found. Available splits: {list(loaded)}")
        loaded = loaded[split]

    yield from iter_dataset_rows(loaded, image_column, id_column)


def iter_dataset_rows(dataset: Any, image_column: str | None, id_column: str | None) -> Iterator[ImageSample]:
    columns = list(getattr(dataset, "column_names", []) or [])
    image_col = image_column or first_existing(columns, COMMON_IMAGE_COLUMNS)
    id_col = id_column or first_existing(columns, COMMON_ID_COLUMNS)
    if image_col is None:
        raise KeyError(f"Could not infer image column from columns: {columns}")

    for index, row in enumerate(dataset):
        image_value = row[image_col]
        images = as_image_list(image_value)
        for image_index, image in enumerate(images):
            sample_id = str(row[id_col]) if id_col and id_col in row else str(index)
            if len(images) > 1:
                sample_id = f"{sample_id}_{image_index}"
            yield ImageSample(
                sample_id=sample_id,
                image_index=index,
                image=image.convert("RGB"),
                source=f"dataset:{index}",
            )


def iter_manifest(manifest_path: Path, image_column: str | None, id_column: str | None) -> Iterator[ImageSample]:
    if manifest_path.suffix.lower() == ".jsonl":
        records = [json.loads(line) for line in manifest_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    else:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        records = data["data"] if isinstance(data, dict) and isinstance(data.get("data"), list) else data

    if not isinstance(records, list):
        raise ValueError("Manifest must be a JSON list, a {'data': [...]} object, or JSONL records.")

    for index, record in enumerate(records):
        if not isinstance(record, dict):
            raise ValueError("Manifest records must be JSON objects.")
        image_key = image_column or first_existing(record.keys(), ("image_path", "path", "file", *COMMON_IMAGE_COLUMNS))
        id_key = id_column or first_existing(record.keys(), COMMON_ID_COLUMNS)
        if image_key is None:
            raise KeyError(f"Could not infer image path key from manifest record keys: {list(record)}")

        image_path = Path(record[image_key])
        if not image_path.is_absolute():
            image_path = manifest_path.parent / image_path
        sample_id = str(record[id_key]) if id_key else str(index)
        yield ImageSample(
            sample_id=sample_id,
            image_index=index,
            image=Image.open(image_path).convert("RGB"),
            source=str(image_path),
        )


def as_image_list(value: Any) -> list[Image.Image]:
    Image, _, _, _ = require_pillow()
    if isinstance(value, Image.Image):
        return [value]
    if isinstance(value, list):
        images: list[Image.Image] = []
        for item in value:
            images.extend(as_image_list(item))
        return images
    if isinstance(value, dict):
        if isinstance(value.get("path"), str):
            return [Image.open(value["path"]).convert("RGB")]
        if isinstance(value.get("bytes"), (bytes, bytearray)):
            from io import BytesIO

            return [Image.open(BytesIO(value["bytes"])).convert("RGB")]
    if isinstance(value, (str, Path)):
        return [Image.open(value).convert("RGB")]
    raise TypeError(f"Unsupported image value type: {type(value)!r}")


def get_colqwen3_patch_grid(processor: Any, image: Image.Image) -> PatchGrid:
    batch = process_single_image(processor, image)
    if "image_grid_thw" not in batch:
        raise KeyError("Processor output does not contain image_grid_thw; cannot infer ColQwen3 patch grid.")

    grid_value = batch["image_grid_thw"][0]
    if hasattr(grid_value, "detach"):
        grid_value = grid_value.detach().cpu().tolist()
    elif hasattr(grid_value, "tolist"):
        grid_value = grid_value.tolist()

    temporal, raw_rows, raw_cols = [int(value) for value in grid_value]
    merge_size = get_merge_size(processor)
    rows = raw_rows // merge_size
    cols = raw_cols // merge_size
    if temporal != 1:
        raise ValueError(f"Expected a single image frame, got temporal grid size {temporal}.")
    if rows <= 0 or cols <= 0:
        raise ValueError(f"Invalid patch grid from image_grid_thw={grid_value}, merge_size={merge_size}.")

    return PatchGrid(rows=rows, cols=cols, raw_grid_thw=(temporal, raw_rows, raw_cols), merge_size=merge_size)


def process_single_image(processor: Any, image: Image.Image) -> Any:
    if hasattr(processor, "process_images"):
        return processor.process_images([image])

    text = getattr(processor, "visual_prompt_prefix", "")
    return processor(images=[image], text=[text], padding="longest", return_tensors="pt")


def get_merge_size(processor: Any) -> int:
    image_processor = getattr(processor, "image_processor", None)
    merge_size = getattr(image_processor, "merge_size", None) or getattr(image_processor, "spatial_merge_size", None)
    if merge_size is None:
        raise ValueError("ColQwen3 image processor is missing merge_size/spatial_merge_size.")
    return int(merge_size)


def suggest_low_semantic_patches(
    image: Image.Image,
    grid: PatchGrid,
    std_threshold: float,
    edge_threshold: float,
) -> list[int]:
    _, _, _, ImageStat = require_pillow()
    width, height = image.size
    grayscale = image.convert("L")
    suggestions: list[int] = []

    for row in range(grid.rows):
        for col in range(grid.cols):
            box = patch_box(width, height, row, col, grid.rows, grid.cols)
            patch = grayscale.crop(box)
            stat = ImageStat.Stat(patch)
            stddev = float(stat.stddev[0])
            edge_score = simple_edge_score(patch)
            if stddev <= std_threshold and edge_score <= edge_threshold:
                suggestions.append(row * grid.cols + col)

    return suggestions


def simple_edge_score(patch: Image.Image) -> float:
    small = patch.resize((max(2, min(32, patch.width)), max(2, min(32, patch.height))))
    pixels = list(small.tobytes())
    width, height = small.size
    total = 0.0
    count = 0
    for y in range(height):
        for x in range(width):
            value = pixels[y * width + x]
            if x + 1 < width:
                total += abs(value - pixels[y * width + x + 1])
                count += 1
            if y + 1 < height:
                total += abs(value - pixels[(y + 1) * width + x])
                count += 1
    return total / count if count else 0.0


def save_patch_grid_preview(
    image: Image.Image,
    grid: PatchGrid,
    output_path: Path,
    line_width: int = 2,
    label_font_size: int = 12,
) -> None:
    _, ImageDraw, ImageFont, _ = require_pillow()
    preview = image.convert("RGB").copy()
    draw = ImageDraw.Draw(preview)
    font_size = max(1, int(label_font_size))
    try:
        font = ImageFont.truetype("arial.ttf", font_size)
    except OSError:
        try:
            font = ImageFont.truetype("DejaVuSans.ttf", font_size)
        except OSError:
            font = ImageFont.load_default()
    width, height = preview.size

    for row in range(grid.rows):
        for col in range(grid.cols):
            left, upper, right, lower = patch_box(width, height, row, col, grid.rows, grid.cols)
            patch_id = row * grid.cols + col
            draw.rectangle((left, upper, right, lower), outline=(255, 0, 0), width=max(1, line_width))
            label = str(patch_id)
            label_box = draw.textbbox((0, 0), label, font=font)
            label_width = label_box[2] - label_box[0]
            label_height = label_box[3] - label_box[1]
            draw.rectangle((left, upper, left + label_width + 2, upper + label_height + 2), fill=(255, 255, 255))
            draw.text((left + 1, upper + 1), label, fill=(0, 0, 0), font=font)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    preview.save(output_path, quality=90)


def patch_box(width: int, height: int, row: int, col: int, rows: int, cols: int) -> tuple[int, int, int, int]:
    left = math.floor(col * width / cols)
    right = math.floor((col + 1) * width / cols)
    upper = math.floor(row * height / rows)
    lower = math.floor((row + 1) * height / rows)
    return left, upper, max(left + 1, right), max(upper + 1, lower)


def first_existing(keys: Iterable[str], candidates: Sequence[str]) -> str | None:
    key_set = set(keys)
    for candidate in candidates:
        if candidate in key_set:
            return candidate
    return None


def write_json(path: Path, records: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True) if path.parent != Path(".") else None
    path.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")


def safe_filename(value: str) -> str:
    return "".join(char if char.isalnum() or char in "._-" else "_" for char in value)[:120] or "sample"


if __name__ == "__main__":
    main()
