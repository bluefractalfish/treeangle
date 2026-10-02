#!/usr/bin/env python3
"""Split one TreeAngle export into grouped COCO train/valid/test folders.

The input export directory must contain:
    png/_annotations.coco.json
    png/<image>.png
    split_groups.csv

Each split_group is assigned as one unit, so overlapping crops and multiple
appearances of a tree cannot leak between training and evaluation.
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import shutil
from collections import defaultdict
from pathlib import Path


SPLITS = ("train", "valid", "test")


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("export_run", type=Path, help="TreeAngle export run directory")
    parser.add_argument("output", type=Path, help="new Roboflow dataset directory")
    parser.add_argument("--seed", type=int, default=42, help="repeatable group shuffle seed")
    parser.add_argument("--ratios", type=float, nargs=3, metavar=("TRAIN", "VALID", "TEST"),
                        default=(0.70, 0.20, 0.10), help="split proportions; default: 0.70 0.20 0.10")
    return parser.parse_args()


def read_groups(path: Path) -> dict[str, str]:
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        required = {"file_name", "split_group"}
        if reader.fieldnames is None or not required.issubset(reader.fieldnames):
            raise ValueError(f"{path} needs columns: file_name, split_group")

        result: dict[str, str] = {}
        for row in reader:
            name = (row["file_name"] or "").strip()
            group = (row["split_group"] or "").strip()
            if not name or not group:
                raise ValueError(f"blank file_name or split_group in {path}")
            previous = result.setdefault(name, group)
            if previous != group:
                raise ValueError(f"{name!r} appears in more than one split group")
    return result


def choose_group_splits(groups: dict[str, list[str]], ratios: tuple[float, float, float],
                        seed: int) -> dict[str, str]:
    if any(r < 0 for r in ratios) or sum(ratios) <= 0:
        raise ValueError("ratios must be non-negative and add up to more than zero")

    total = sum(len(names) for names in groups.values())
    targets = dict(zip(SPLITS, (total * r / sum(ratios) for r in ratios), strict=True))
    counts = dict.fromkeys(SPLITS, 0)
    items = list(groups.items())
    random.Random(seed).shuffle(items)
    items.sort(key=lambda item: len(item[1]), reverse=True)

    assignment: dict[str, str] = {}
    for group, names in items:
        size = len(names)
        # Fill the split furthest below its requested number of images.
        split = max(SPLITS, key=lambda name: (targets[name] - counts[name], -counts[name]))
        assignment[group] = split
        counts[split] += size
    return assignment


def subset_coco(coco: dict, names: set[str]) -> dict:
    images = [image for image in coco["images"] if image["file_name"] in names]
    image_ids = {image["id"] for image in images}
    annotations = [ann for ann in coco["annotations"] if ann["image_id"] in image_ids]
    return {
        **coco,
        "images": images,
        "annotations": annotations,
    }


def main() -> None:
    args = arguments()
    export_run = args.export_run.expanduser().resolve()
    png_dir = export_run / "png"
    source_json = png_dir / "_annotations.coco.json"
    groups_csv = export_run / "split_groups.csv"
    output = args.output.expanduser().resolve()

    if output.exists():
        raise SystemExit(f"Refusing to overwrite existing output directory: {output}")
    if not source_json.is_file() or not groups_csv.is_file():
        raise SystemExit("Expected png/_annotations.coco.json and split_groups.csv in the export run")

    coco = json.loads(source_json.read_text(encoding="utf-8"))
    if not isinstance(coco.get("images"), list) or not isinstance(coco.get("annotations"), list):
        raise SystemExit("COCO JSON needs images and annotations lists")

    group_for_name = read_groups(groups_csv)
    image_names = [image.get("file_name") for image in coco["images"]]
    if any(not isinstance(name, str) for name in image_names):
        raise SystemExit("every COCO image needs a string file_name")
    names = set(image_names)
    missing_groups = sorted(names - group_for_name.keys())
    extra_groups = sorted(group_for_name.keys() - names)
    if missing_groups:
        raise SystemExit(f"{len(missing_groups)} COCO images have no split group; first: {missing_groups[0]}")
    if extra_groups:
        raise SystemExit(f"{len(extra_groups)} CSV images are absent from COCO; first: {extra_groups[0]}")

    image_ids = {image["id"] for image in coco["images"]}
    orphaned = [ann.get("id") for ann in coco["annotations"] if ann.get("image_id") not in image_ids]
    if orphaned:
        raise SystemExit(f"COCO has annotations for unknown images; first annotation id: {orphaned[0]}")

    grouped_names: dict[str, list[str]] = defaultdict(list)
    for name in names:
        image_path = png_dir / name
        if not image_path.is_file():
            raise SystemExit(f"COCO references a missing PNG: {image_path}")
        grouped_names[group_for_name[name]].append(name)

    assignment = choose_group_splits(grouped_names, tuple(args.ratios), args.seed)
    names_by_split: dict[str, set[str]] = {split: set() for split in SPLITS}
    for group, group_names in grouped_names.items():
        names_by_split[assignment[group]].update(group_names)

    try:
        for split, split_names in names_by_split.items():
            split_dir = output / split
            split_dir.mkdir(parents=True)
            for name in sorted(split_names):
                shutil.copy2(png_dir / name, split_dir / name)
            destination_json = split_dir / "_annotations.coco.json"
            destination_json.write_text(json.dumps(subset_coco(coco, split_names), indent=2), encoding="utf-8")
    except Exception:
        # The destination was newly created by this program, so it is safe to remove.
        shutil.rmtree(output, ignore_errors=True)
        raise

    print(f"Wrote grouped COCO dataset: {output}")
    for split in SPLITS:
        print(f"{split:5} {len(names_by_split[split]):5} images")


if __name__ == "__main__":
    main()

