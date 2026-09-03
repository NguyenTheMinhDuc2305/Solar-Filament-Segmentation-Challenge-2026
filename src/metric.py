"""Panoptic Quality, ported from the organizer's `self-evaluation-notebook`.

The port keeps the organizer's function decomposition (`rles_to_layers`,
`get_overlap_matrices`, `fp_count_hit`, `fn_count_hit`, `get_overlap_df`,
`get_pq_score`) so the two can be diffed side by side. The only substitution is
numpy for torch: the arithmetic is the same float32 matmul, and it keeps the
scorer importable on a machine with no torch, which is what lets the
annotator-ceiling gate run in the CPU smoke test.

Both dataframes carry the submission's two columns, `filament_id` and
`segmentation_rle` (compressed COCO RLE at 2048x2048). The identifiers are
**asymmetric**, exactly as in the competition:

    ground truth   "<annotator_id>-<image_stem>_<k>"
    prediction     "<image_stem>_<k>"

so one shared prediction set is scored separately against every annotator of
that image, and PQ is pooled over annotator-images.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from pycocotools import mask as mask_util

IOU_THRESHOLD = 0.5
MASK_SIZE = 2048


def rles_to_layers(rles, height=MASK_SIZE, width=MASK_SIZE):
    """Decode compressed COCO RLE strings into an (n_masks, H, W) {0,1} stack."""
    if not rles:
        return np.zeros((0, height, width), dtype=np.float32)
    rle_dicts = [{"size": [height, width], "counts": rle} for rle in rles]
    masks = mask_util.decode(rle_dicts)          # (H, W, n_masks)
    return masks.transpose(2, 0, 1).astype(np.float32)


def get_overlap_matrices(gt_layers, pred_layers):
    """Pairwise (IoU, Dice) between GT and predicted layers of one image.

    Returns two (n_gt, n_pred) arrays; rows are GT filaments, columns are
    predictions.
    """
    n_gt, height, width = gt_layers.shape
    n_pred = pred_layers.shape[0]

    gt_flat = gt_layers.reshape(n_gt, height * width)
    pred_flat = pred_layers.reshape(n_pred, height * width)

    intersection = gt_flat @ pred_flat.T

    gt_areas = gt_flat.sum(axis=1).reshape(-1, 1)
    pred_areas = pred_flat.sum(axis=1).reshape(1, -1)
    union = gt_areas + pred_areas - intersection

    iou = np.where(union == 0, 0.0, intersection / np.where(union == 0, 1.0, union))
    denom = gt_areas + pred_areas
    dice = np.where(denom == 0, 0.0, 2 * intersection / np.where(denom == 0, 1.0, denom))
    return iou.astype(np.float64), dice.astype(np.float64)


def fp_count_hit(hit_matrix):
    """Predictions that hit no GT filament: columns whose sum is zero."""
    return int((hit_matrix.sum(axis=0) == 0).sum())


def fn_count_hit(hit_matrix):
    """GT filaments that no prediction hit: rows whose sum is zero."""
    return int((hit_matrix.sum(axis=1) == 0).sum())


def _split_id(series):
    """`"<prefix>_<k>"` -> `"<prefix>"`. Image stems contain no underscore."""
    return series.astype(str).str.split("_", n=1).str[0]


def get_overlap_df(gt_df, pred_df, height=MASK_SIZE, width=MASK_SIZE):
    """One row per annotator-image, holding its IoU/Dice matrices and counts."""
    gt_keys = _split_id(gt_df["filament_id"])
    pred_keys = _split_id(pred_df["filament_id"])

    rows = []
    for annotator_image in gt_keys.unique():
        image_id = annotator_image.split("-", maxsplit=1)[1]

        gt_rles = gt_df.loc[gt_keys == annotator_image, "segmentation_rle"].tolist()
        pred_rles = pred_df.loc[pred_keys == image_id, "segmentation_rle"].tolist()

        gt_layers = rles_to_layers(gt_rles, height, width)
        pred_layers = rles_to_layers(pred_rles, height, width)
        iou, dice = get_overlap_matrices(gt_layers, pred_layers)

        rows.append({
            "annotator_image": annotator_image,
            "iou_matrix": iou,
            "dice_matrix": dice,
            "n_gt": len(gt_rles),
            "n_pred": len(pred_rles),
        })
    return pd.DataFrame(rows, columns=[
        "annotator_image", "iou_matrix", "dice_matrix", "n_gt", "n_pred"])


def get_pq_score(overlap_df):
    """PQ = sum(IoU of TP pairs) / (|TP| + 0.5|FP| + 0.5|FN|), pooled over rows."""
    return summarize(overlap_df)["cv_pq"]


def summarize(overlap_df):
    """`get_pq_score` plus the diagnostics the proposal asks every cycle to record.

    `detection_recall` and `mean_matched_iou` say *which half* of PQ a change
    moved: a human annotator scores 0.634 mean matched IoU yet still loses most
    of the denominator to FP/FN, so a cycle that only sharpens boundaries cannot
    be distinguished from one that finds filaments without both numbers.
    """
    tp_iou_scores = []
    fp_count = 0
    fn_count = 0
    n_gt_total = 0
    n_pred_total = 0
    one_to_many = 0          # GT filaments overlapping (IoU > 0) more than one prediction
    many_to_one = 0          # predictions overlapping more than one GT filament

    for row in overlap_df.itertuples(index=False):
        iou_matrix = row.iou_matrix
        n_gt, n_pred = row.n_gt, row.n_pred
        n_gt_total += n_gt
        n_pred_total += n_pred

        if n_gt == 0:
            fp_count += n_pred
            continue
        if n_pred == 0:
            fn_count += n_gt
            continue

        hit_matrix = iou_matrix > IOU_THRESHOLD
        tp_iou_scores.extend(iou_matrix[hit_matrix].tolist())
        fp_count += fp_count_hit(hit_matrix)
        fn_count += fn_count_hit(hit_matrix)

        touch = iou_matrix > 0
        one_to_many += int((touch.sum(axis=1) > 1).sum())
        many_to_one += int((touch.sum(axis=0) > 1).sum())

    tp_count = len(tp_iou_scores)
    denominator = tp_count + 0.5 * fp_count + 0.5 * fn_count
    n_rows = max(len(overlap_df), 1)

    return {
        "cv_pq": (sum(tp_iou_scores) / denominator) if denominator > 0 else 0.0,
        "tp": tp_count,
        "fp": fp_count,
        "fn": fn_count,
        "detection_recall": (tp_count / n_gt_total) if n_gt_total else 0.0,
        "mean_matched_iou": float(np.mean(tp_iou_scores)) if tp_iou_scores else 0.0,
        "one_to_many_rate": (one_to_many / n_gt_total) if n_gt_total else 0.0,
        "many_to_one_rate": (many_to_one / n_pred_total) if n_pred_total else 0.0,
        "n_pred_per_image": n_pred_total / n_rows,
        "n_gt_per_annotator_image": n_gt_total / n_rows,
        "n_annotator_images": int(len(overlap_df)),
    }


def score(gt_df, pred_df, height=MASK_SIZE, width=MASK_SIZE):
    """Convenience wrapper: dataframes in, PQ plus diagnostics out."""
    return summarize(get_overlap_df(gt_df, pred_df, height, width))


def encode_mask(mask):
    """Binary HxW array -> compressed COCO RLE `counts` string."""
    rle = mask_util.encode(np.asfortranarray(mask.astype(np.uint8)))
    return rle["counts"].decode("utf-8")


def assert_disjoint(rles, where=""):
    """Fail loudly if two masks of one image share a pixel.

    An overlap makes the Kaggle submission ERROR and burns one of the five daily
    slots, so this is checked before anything is written, never after.
    """
    if len(rles) < 2:
        return
    dicts = [{"size": [MASK_SIZE, MASK_SIZE], "counts": r} for r in rles]
    areas = mask_util.area(dicts)
    merged = mask_util.merge(dicts, intersect=False)
    if int(mask_util.area(merged)) != int(areas.sum()):
        raise ValueError(
            "overlapping masks in {}: union area {} < sum of areas {}".format(
                where or "image", int(mask_util.area(merged)), int(areas.sum())))
