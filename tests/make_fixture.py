"""Builds a tiny MAGFiLO-shaped dataset for the CPU smoke test.

Not real filament data - synthetic squares on synthetic disks, just enough
structure (COCO annotation dialect, multi-annotator images, a train/test
split) to exercise every stage of the pipeline end to end in under two
minutes with no GPU.
"""
from __future__ import annotations

import json
from pathlib import Path

import cv2
import numpy as np

CANVAS = 2048  # ground-truth polygons are always in this coordinate system
RAW_IMG = 512  # synthetic "photograph" resolution; src/data.py resizes it anyway

TRAIN_STEMS = ["train_a", "train_b", "train_c", "train_d"]
TEST_STEMS = ["test_a", "test_b"]

# stem -> {annotator_id: [(x, y, side), ...] squares in the 2048 GT canvas}
ANNOTATIONS = {
    "train_a": {
        "ann1": [(300, 300, 60), (900, 900, 50)],
        "ann2": [(320, 320, 60), (1400, 400, 45)],
    },
    "train_b": {
        "ann1": [(500, 1200, 55)],
    },
    "train_c": {
        "ann1": [(200, 1600, 70), (1000, 1000, 40)],
        "ann3": [(220, 1620, 65)],
    },
    "train_d": {
        "ann2": [(1500, 1500, 50)],
    },
}


def _square_polygon(x, y, side):
    return [[x, y, x + side, y, x + side, y + side, x, y + side]]


def _synthetic_image(seed):
    rng = np.random.default_rng(seed)
    img = rng.normal(loc=120, scale=10, size=(RAW_IMG, RAW_IMG)).clip(0, 255).astype(np.uint8)
    cv2.circle(img, (RAW_IMG // 2, RAW_IMG // 2), RAW_IMG // 2 - 5, 200, thickness=-1)
    return img


def build(root: Path):
    """Populate `root/MAGFiLO_1.0_Kaggle_2026/...`; returns that directory."""
    root = Path(root)
    ds_root = root / "MAGFiLO_1.0_Kaggle_2026"
    train_img_dir = ds_root / "train" / "train_images"
    test_img_dir = ds_root / "test" / "test_images"
    train_img_dir.mkdir(parents=True, exist_ok=True)
    test_img_dir.mkdir(parents=True, exist_ok=True)

    for i, stem in enumerate(TRAIN_STEMS):
        cv2.imwrite(str(train_img_dir / (stem + ".jpeg")), _synthetic_image(i))
    for i, stem in enumerate(TEST_STEMS):
        cv2.imwrite(str(test_img_dir / (stem + ".jpeg")), _synthetic_image(100 + i))

    images = []
    annotations = []
    ann_counter = 0
    for stem, by_annotator in ANNOTATIONS.items():
        for annotator, squares in by_annotator.items():
            image_id = "{}-{}".format(annotator, stem)
            images.append({
                "id": image_id,
                "file_name": stem + ".jpeg",
                "height": CANVAS,
                "width": CANVAS,
                "date_captured": "2020-01-01 00:00:00",
            })
            for (x, y, side) in squares:
                ann_counter += 1
                annotations.append({
                    "id": "fixture-{}".format(ann_counter),
                    "image_id": image_id,
                    "category_id": 1,
                    "iscrowd": 0,
                    "area": float(side * side),
                    "segmentation": json.dumps(_square_polygon(x, y, side)),
                })

    coco = {
        "info": {},
        "licenses": [],
        "categories": [
            {"id": 1, "name": "Left", "supercategory": "filament"},
            {"id": 2, "name": "Right", "supercategory": "filament"},
            {"id": 3, "name": "Unidentifiable", "supercategory": "filament"},
        ],
        "images": images,
        "annotations": annotations,
    }
    ann_path = ds_root / "train" / "MAGFiLO_1.0_Annotations_kaggle2026_train.json"
    ann_path.write_text(json.dumps(coco), encoding="utf-8")
    return ds_root
