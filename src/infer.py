"""Fold models -> probability maps -> submission-format rows.

Out-of-fold rows use each fold's own held-out images (what `cv_pq` is computed
on); test rows average every completed fold's probability map before
thresholding, so a run that stops early under the wall-clock guard still
produces an honest test prediction from whatever folds finished.
"""
from __future__ import annotations

import pandas as pd

from src.postprocess import probs_to_rles
from src.train import predict_prob


def predict_stems(model, img_dir, stems, img_size, device):
    """`{stem: sigmoid probability map}` for every stem in `stems`."""
    return {stem: predict_prob(model, img_dir, stem, img_size, device) for stem in stems}


def probs_to_submission_rows(probs_by_stem, cfg):
    """`{stem: prob map}` -> `[(filament_id, segmentation_rle), ...]`."""
    rows = []
    for stem, prob in probs_by_stem.items():
        rles = probs_to_rles(
            prob,
            threshold=cfg["prob_threshold"],
            min_area_px=cfg["min_area_px"],
            where=stem,
        )
        for k, rle in enumerate(rles):
            rows.append((stem + "_" + str(k), rle))
    return rows


def accumulate_probs(accum, probs_by_stem):
    """Running `{stem: (sum_of_probs, n_folds)}` += one fold's probability maps."""
    for stem, prob in probs_by_stem.items():
        if stem in accum:
            total, count = accum[stem]
            accum[stem] = (total + prob, count + 1)
        else:
            accum[stem] = (prob.copy(), 1)
    return accum


def mean_probs(accum):
    return {stem: total / count for stem, (total, count) in accum.items()}


def rows_to_df(rows):
    return pd.DataFrame(rows, columns=["filament_id", "segmentation_rle"])
