"""Dataset access: COCO annotations -> RLE ground truth, union masks, folds.

Layout of the competition dataset (verified against `competition_list_files`):

    MAGFiLO_1.0_Kaggle_2026/
        train/MAGFiLO_1.0_Annotations_kaggle2026_train.json
        train/train_images/<image_stem>.jpeg      707 images
        test/test_images/<image_stem>.jpeg        180 images

The annotation file is COCO-like with two twists that matter:

* `image_id` is `"<annotator_id>-<image_stem>"`, so the 707 images appear as
  1154 *annotator-images* - 411 images have one annotator, 145 have two, 151
  have three. Ground truth is per annotator, and PQ scores one shared prediction
  set against each annotator separately.
* `segmentation` is a JSON *string*, not a list, and every annotation is a
  single polygon with `iscrowd` 0. All three used categories (Left, Right,
  Unidentifiable) are filaments, so nothing is filtered by category.
"""
from __future__ import annotations

import json
from pathlib import Path

import cv2
import numpy as np
import pandas as pd
from pycocotools import mask as mask_util
from sklearn.model_selection import KFold

DATASET_DIRNAME = "MAGFiLO_1.0_Kaggle_2026"
ANNOTATIONS_NAME = "MAGFiLO_1.0_Annotations_kaggle2026_train.json"
MASK_SIZE = 2048


# ------------------------------------------------------------------ locating it
def find_dataset_root(data_root):
    """Return the `MAGFiLO_1.0_Kaggle_2026` directory under `data_root`.

    The notebook passes `/kaggle/input`, where the competition mounts one level
    down under its slug; a local fixture may point straight at it. Searching
    keeps the entrypoint free of a hardcoded slug.
    """
    root = Path(data_root)
    if root.name == DATASET_DIRNAME:
        return root
    direct = root / DATASET_DIRNAME
    if direct.is_dir():
        return direct
    # Depth-robust: Kaggle mounts competition data at varying depths under
    # /kaggle/input (e.g. plain `<slug>/...` locally vs.
    # `competitions/<slug>/...` on the hosted kernel), so walk the tree
    # instead of hardcoding a level count.
    for cand in sorted(root.rglob(DATASET_DIRNAME)):
        if cand.is_dir():
            return cand
    raise FileNotFoundError(
        "could not find {} under {}".format(DATASET_DIRNAME, root))


def annotations_path(dataset_root):
    return Path(dataset_root) / "train" / ANNOTATIONS_NAME


def train_images_dir(dataset_root):
    return Path(dataset_root) / "train" / "train_images"


def test_images_dir(dataset_root):
    return Path(dataset_root) / "test" / "test_images"


# ------------------------------------------------------------- ground truth RLE
def polygon_to_rle(segmentation, height=MASK_SIZE, width=MASK_SIZE):
    """One annotation's polygon(s) -> a single compressed COCO RLE string."""
    polys = json.loads(segmentation) if isinstance(segmentation, str) else segmentation
    rles = mask_util.frPyObjects(polys, height, width)
    merged = mask_util.merge(rles)
    return merged["counts"].decode("utf-8")


def build_gt_rles(coco, height=MASK_SIZE, width=MASK_SIZE):
    """`{"<annotator_id>-<image_stem>": [rle, ...]}` for every annotator-image.

    Annotator-images with zero annotations are kept with an empty list: they are
    real rows of the GT table and dropping them would quietly remove images on
    which every prediction is a false positive.
    """
    rles = {im["id"]: [] for im in coco["images"]}
    for ann in coco["annotations"]:
        rles[ann["image_id"]].append(polygon_to_rle(ann["segmentation"], height, width))
    return rles


def load_gt_rles(dataset_root, cache_dir=None):
    """`build_gt_rles` with an on-disk cache; rasterizing 8199 polygons is slow."""
    cache = Path(cache_dir) / "gt_rles.json" if cache_dir else None
    if cache and cache.exists():
        return json.loads(cache.read_text(encoding="utf-8"))
    coco = json.loads(annotations_path(dataset_root).read_text(encoding="utf-8"))
    rles = build_gt_rles(coco)
    if cache:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(rles), encoding="utf-8")
    return rles


def gt_dataframe(gt_rles, stems=None):
    """GT in submission format: `filament_id = "<annotator_id>-<stem>_<k>"`.

    `stems` restricts the table to one fold's images. Annotator-images with no
    filaments contribute no rows but are still scored - `get_overlap_df` derives
    its row set from this table, so an empty annotator-image simply cannot
    appear, exactly as in the organizer's own scorer.
    """
    rows = []
    for annotator_image, rles in gt_rles.items():
        if stems is not None and annotator_image.split("-", 1)[1] not in stems:
            continue
        for k, rle in enumerate(rles):
            rows.append((annotator_image + "_" + str(k), rle))
    return pd.DataFrame(rows, columns=["filament_id", "segmentation_rle"])


def image_stems(gt_rles):
    """The 707 distinct image stems, sorted - the unit a fold split works on."""
    return sorted({key.split("-", 1)[1] for key in gt_rles})


def annotators_by_stem(gt_rles):
    out = {}
    for key in gt_rles:
        annotator, stem = key.split("-", 1)
        out.setdefault(stem, []).append(annotator)
    return {stem: sorted(v) for stem, v in out.items()}


# --------------------------------------------------------------- training target
def union_mask(gt_rles, stem, annotators, size=MASK_SIZE):
    """Binary union of every annotator's filaments for one image.

    The union is the cycle-0 training target: 43.7% of filaments are seen by
    only one of two annotators, so intersecting would train the network to miss
    exactly the filaments PQ is lost on.
    """
    dicts = []
    for annotator in annotators:
        for rle in gt_rles["{}-{}".format(annotator, stem)]:
            dicts.append({"size": [MASK_SIZE, MASK_SIZE], "counts": rle.encode("utf-8")})
    if not dicts:
        merged = np.zeros((MASK_SIZE, MASK_SIZE), dtype=np.uint8)
    else:
        merged = mask_util.decode(mask_util.merge(dicts, intersect=False))
    if size != MASK_SIZE:
        merged = cv2.resize(merged, (size, size), interpolation=cv2.INTER_AREA)
        merged = (merged > 0).astype(np.uint8)
    return merged


# -------------------------------------------------------------------- image i/o
def load_image(path, size):
    """Grayscale full-disk image, resized to `size`, as uint8 HxW."""
    img = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
    if img is None:
        raise FileNotFoundError("could not read image: {}".format(path))
    if img.shape[0] != size or img.shape[1] != size:
        img = cv2.resize(img, (size, size), interpolation=cv2.INTER_AREA)
    return img


def cache_train_arrays(dataset_root, gt_rles, stems, size, cache_dir):
    """Materialise resized image/mask PNG pairs once, reused by every fold.

    Decoding 707 2048x2048 JPEGs and rasterizing their masks on every epoch of
    every fold would cost close to an hour of the kernel's wall clock; on a warm
    start (the cache is kernel output and mounts into the next run) it costs
    nothing at all.
    """
    img_dir = Path(cache_dir) / "img_{}".format(size)
    msk_dir = Path(cache_dir) / "mask_{}".format(size)
    img_dir.mkdir(parents=True, exist_ok=True)
    msk_dir.mkdir(parents=True, exist_ok=True)

    by_stem = annotators_by_stem(gt_rles)
    src_dir = train_images_dir(dataset_root)
    built = 0
    for stem in stems:
        img_path = img_dir / (stem + ".png")
        msk_path = msk_dir / (stem + ".png")
        if img_path.exists() and msk_path.exists():
            continue
        cv2.imwrite(str(img_path), load_image(src_dir / (stem + ".jpeg"), size))
        cv2.imwrite(str(msk_path), union_mask(gt_rles, stem, by_stem[stem], size) * 255)
        built += 1
    return img_dir, msk_dir, built


def make_folds(stems, n_splits, seed):
    """Random image-level folds.

    Deliberately *not* grouped by date: 136 of 176 test days lie within three
    days of a train day and 31 share the exact day, so train/test is a random
    split of one pool and a temporal split would be pessimistically biased.
    Splitting on image stems keeps every annotator-image of one image together,
    which is what stops an annotator's view of an image leaking into its own
    validation score.
    """
    kf = KFold(n_splits=n_splits, shuffle=True, random_state=seed)
    stems = list(stems)
    return [([stems[i] for i in tr], [stems[i] for i in va])
            for tr, va in kf.split(stems)]


def test_stems(dataset_root):
    return sorted(p.stem for p in test_images_dir(dataset_root).glob("*.jpeg"))
