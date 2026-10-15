
#!/usr/bin/env python3
"""Split TreeAngle COCO exports and optionally upload to Roboflow.

Run directly from the QGIS Python console.
"""

import csv
import json
import os
import random
import re
import shutil
from collections import defaultdict
from pathlib import Path
from tempfile import TemporaryDirectory


# ============================================================
# CONFIGURATION — EDIT THESE
# ============================================================

EXPORT = Path("/home/gaia/W/projects/enderlin/3.6/run_261011_2203/run_261011_2204/")
OUTPUT = Path("/home/gaia/W/projects/enderlin/3.6/run_261011_2203/run_261011_2204/dataset")

WORKSPACE = "tornado-ml"
PROJECT = "v0-11"
API_KEY = os.getenv("ROBOFLOW_API_KEY", "")

UPLOAD = False         # True = split AND upload
SEED = 42
RATIOS = (0.70, 0.20, 0.10)

# Optional previously reviewed group_splits.csv.
# Use None for automatic group assignment.
ASSIGNMENTS = None

SPLITS = ("train", "valid", "test")

TAG_FIELDS = (
    "region",
    "overlap",
    "visibility",
    "confidence",
    "damage_class",
    "failure_mode",
    "branch_loss",
    "crown_intactness",
    "exposure",
    "tree_type",
    "ground_type",
    "health",
)


# ============================================================
# METADATA AND TAGS
# ============================================================

def clean_tag(value):
    return re.sub(
        r"[^a-zA-Z0-9_-]+", "_", str(value)
    ).strip("_")


def image_tags(meta):
    tags = set()

    # Mandatory searchable group tag.
    tags.add(f"splitgroup_{clean_tag(meta['split_group'])}")

    # Preserve explicitly exported tags.
    existing = meta.get("tags", [])
    if isinstance(existing, str):
        existing = [existing]
    tags.update(str(t) for t in existing if t)

    for key in TAG_FIELDS:
        value = meta.get(key)

        if value is None or value == "":
            continue

        values = value if isinstance(value, list) else [value]

        for item in values:
            if item is None or item == "":
                continue
            tag = clean_tag(item)
            if tag:
                tags.add(f"{key}_{tag}")

    return sorted(tags)


def read_groups(path):
    """Read split groups and optional metadata columns"""
    result = {}

    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)

        required = {"file_name", "split_group"}
        if not required.issubset(reader.fieldnames or []):
            raise ValueError(
                "split_groups.csv requires file_name, split_group"
            )

        for row in reader:
            name = (row["file_name"] or "").strip()
            group = (row["split_group"] or "").strip()

            if not name or not group:
                raise ValueError("Blank file name or split group")

            if name in result:
                if result[name]["split_group"] != group:
                    raise ValueError(f"Conflicting groups: {name}")
                raise ValueError(f"Duplicate CSV row: {name}")

            result[name] = {
                k: v for k, v in row.items()
                if v is not None and v != ""
            }

    return result


# ============================================================
# GROUP-BASED SPLITTING
# ============================================================

def choose_group_splits(groups, ratios, seed):
    """Assign complete groups, targeting image-count ratios"""
    if len(ratios) != 3 or any(r < 0 for r in ratios):
        raise ValueError("Invalid ratios")

    if sum(ratios) <= 0:
        raise ValueError("Ratios must sum to more than zero")

    total = sum(len(names) for names in groups.values())

    targets = {
        split: total * ratio / sum(ratios)
        for split, ratio in zip(SPLITS, ratios)
    }

    counts = dict.fromkeys(SPLITS, 0)
    items = sorted(groups.items())

    random.Random(seed).shuffle(items)
    items.sort(key=lambda item: len(item[1]), reverse=True)

    assignment = {}

    for group, names in items:
        split = max(
            (s for s in SPLITS if targets[s] > 0),
            key=lambda s: (targets[s] - counts[s], -counts[s])
        )

        assignment[group] = split
        counts[split] += len(names)

    return assignment


def read_assignments(path, groups):
    with Path(path).open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))

    assignment = {
        row["split_group"]: row["split"]
        for row in rows
    }

    if (
        len(rows) != len(assignment)
        or set(assignment) != set(groups)
        or any(s not in SPLITS for s in assignment.values())
    ):
        raise ValueError("Invalid group_splits.csv assignments")

    return assignment


def subset_coco(coco, names):
    images = [
        image for image in coco["images"]
        if image["file_name"] in names
    ]

    ids = {image["id"] for image in images}

    annotations = [
        ann for ann in coco["annotations"]
        if ann["image_id"] in ids
    ]

    return {
        **coco,
        "images": images,
        "annotations": annotations,
    }


# ============================================================
# DATASET PREPARATION
# ============================================================

def prepare_dataset():
    png_dir = EXPORT.expanduser().resolve() / "png"
    source_json = png_dir / "_annotations.coco.json"
    groups_csv = EXPORT.expanduser().resolve() / "split_groups.csv"
    output = OUTPUT.expanduser().resolve()

    if output.exists():
        raise FileExistsError(
            f"Output already exists: {output}\n"
            "Choose a new OUTPUT to avoid overwriting data."
        )

    if not source_json.is_file() or not groups_csv.is_file():
        raise FileNotFoundError(
            "Expected png/_annotations.coco.json "
            "and split_groups.csv"
        )

    coco = json.loads(source_json.read_text(encoding="utf-8"))
    rows = read_groups(groups_csv)

    if not isinstance(coco.get("images"), list):
        raise ValueError("COCO images must be a list")
    if not isinstance(coco.get("annotations"), list):
        raise ValueError("COCO annotations must be a list")

    images = coco["images"]
    names = [image["file_name"] for image in images]

    if len(names) != len(set(names)):
        raise ValueError("Duplicate filenames in COCO")

    if set(names) != set(rows):
        missing = set(names) - set(rows)
        extra = set(rows) - set(names)
        raise ValueError(
            f"COCO/CSV mismatch: {len(missing)} missing, "
            f"{len(extra)} extra"
        )

    ids = {image["id"] for image in images}
    for ann in coco["annotations"]:
        if ann["image_id"] not in ids:
            raise ValueError(
                f"Orphan annotation: {ann.get('id')}"
            )

    groups = defaultdict(list)

    for image in images:
        name = image["file_name"]
        path = png_dir / name

        if not path.is_file():
            raise FileNotFoundError(path)

        # Merge CSV columns and image-level metadata.
        meta = {
            **rows[name],
            **image.get("treeangle", {}),
        }

        # The CSV is authoritative for group membership.
        if (
            meta.get("split_group") != rows[name]["split_group"]
        ):
            raise ValueError(
                f"Conflicting split group for {name}"
            )

        meta["split_group"] = rows[name]["split_group"]
        meta["tags"] = image_tags(meta)
        image["treeangle"] = meta

        groups[meta["split_group"]].append(name)

    assignment = (
        read_assignments(ASSIGNMENTS, groups)
        if ASSIGNMENTS
        else choose_group_splits(groups, RATIOS, SEED)
    )

    names_by_split = {s: set() for s in SPLITS}

    for group, group_names in groups.items():
        names_by_split[assignment[group]].update(group_names)

    # Annotate splits in each image's searchable tags.
    for image in images:
        meta = image["treeangle"]
        split = assignment[meta["split_group"]]
        meta["tags"] = sorted(set(
            meta["tags"] + [f"split_{split}"]
        ))

    try:
        for split in SPLITS:
            directory = output / split
            directory.mkdir(parents=True)

            selected = names_by_split[split]

            for name in sorted(selected):
                destination = directory / name
                destination.parent.mkdir(
                    parents=True, exist_ok=True
                )
                shutil.copy2(png_dir / name, destination)

            selected_coco = subset_coco(coco, selected)

            (directory / "_annotations.coco.json").write_text(
                json.dumps(selected_coco, indent=2),
                encoding="utf-8"
            )

        with (output / "group_splits.csv").open(
            "w", newline="", encoding="utf-8"
        ) as handle:
            writer = csv.writer(handle)
            writer.writerow(
                ["split_group", "n_images", "split"]
            )

            for group in sorted(groups):
                writer.writerow([
                    group,
                    len(groups[group]),
                    assignment[group]
                ])

    except Exception:
        shutil.rmtree(output, ignore_errors=True)
        raise

    print(f"\nDataset created: {output}")

    ann_count = defaultdict(int)
    for ann in coco["annotations"]:
        ann_count[ann["image_id"]] += 1

    for split in SPLITS:
        selected_images = [
            image for image in images
            if image["file_name"] in names_by_split[split]
        ]
        nulls = sum(
            ann_count[image["id"]] == 0
            for image in selected_images
        )

        print(
            f"{split:5}  "
            f"{len(selected_images):6} images  "
            f"{nulls:5} nulls"
        )

    print(f"Groups: {len(groups)}")

    return output


# ============================================================
# ROBOFLOW UPLOAD
# ============================================================

def upload_dataset(output):
    if not API_KEY:
        raise ValueError(
            "Set ROBOFLOW_API_KEY or API_KEY first"
        )

    from roboflow import Roboflow

    project = (
        Roboflow(api_key=API_KEY)
        .workspace(WORKSPACE)
        .project(PROJECT)
    )

    if project.type != "keypoint-detection":
        raise ValueError(
            "Roboflow project must be keypoint-detection"
        )

    with TemporaryDirectory() as temp:
        annotation_path = Path(temp) / "_annotations.coco.json"

        for split in SPLITS:
            directory = output / split

            coco = json.loads(
                (directory / "_annotations.coco.json")
                .read_text(encoding="utf-8")
            )

            annotation_counts = defaultdict(int)
            for ann in coco["annotations"]:
                annotation_counts[ann["image_id"]] += 1

            for image in coco["images"]:
                name = image["file_name"]
                path = directory / name
                meta = image["treeangle"]

                # Null samples have no annotation to upload.
                is_null = annotation_counts[image["id"]] == 0

                if not is_null:
                    annotation_path.write_text(
                        json.dumps(subset_coco(coco, {name})),
                        encoding="utf-8"
                    )

                result = project.upload(
                    image_path=str(path),
                    annotation_path=(
                        None if is_null
                        else str(annotation_path)
                    ),
                    split=split,
                    tag_names=meta["tags"],
                    metadata={
                        "treeangle_split_group":
                            meta["split_group"],
                    },
                    num_retry_uploads=3,
                )

                results = (
                    result if isinstance(result, list)
                    else [result]
                )

                if len(results) != 1:
                    raise RuntimeError(
                        f"Unexpected response for {name}: {result}"
                    )

                response = results[0]
                uploaded_image = response.get("image") or {}
                uploaded_annotation = response.get("annotation")

                if (
                    not uploaded_image.get("id")
                    or uploaded_image.get("duplicate")
                    or uploaded_image.get("success") is False
                    or (
                        not is_null
                        and (
                            not isinstance(
                                uploaded_annotation, dict
                            )
                            or uploaded_annotation.get(
                                "success"
                            ) is False
                        )
                    )
                ):
                    raise RuntimeError(
                        f"Upload requires review: {name}\n"
                        f"{response}"
                    )

                print(
                    f"UPLOADED {split}: {name}"
                    + (" [null]" if is_null else "")
                )

    print("\nUpload completed.")


# ============================================================
# EXECUTION
# ============================================================

def main():
    output = prepare_dataset()
    print("running")
    if UPLOAD:
        upload_dataset(output)
    else:
        print(
            "\nLOCAL EXPORT COMPLETE "
            "set UPLOAD = True to upload a new export run"
        )


if __name__ == "__main__":
    main()

