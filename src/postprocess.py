"""Probability map -> pixel-disjoint filament instances -> COCO RLE strings.

Connected components are disjoint *by construction* (each pixel gets exactly
one label), which is what makes them the cheapest way to satisfy the
competition's disjointness constraint on cycle 0. `assert_disjoint` is still
run before anything is written, because an overlapping submission burns one
of five daily slots and is not a risk worth taking on an assumption.
"""
from __future__ import annotations

import cv2
import numpy as np
from scipy import ndimage

from src.metric import assert_disjoint, encode_mask

MASK_SIZE = 2048
CONNECTIVITY_8 = np.ones((3, 3), dtype=np.uint8)


def upsample_prob(prob, size=MASK_SIZE):
    """Model-resolution probability map (HxW float) -> `size`x`size` bilinear."""
    if prob.shape[0] == size and prob.shape[1] == size:
        return prob
    return cv2.resize(prob.astype(np.float32), (size, size), interpolation=cv2.INTER_LINEAR)


def mask_to_instances(binary_mask, min_area_px):
    """Binary HxW mask -> list of disjoint instance masks via connected components."""
    labels, n = ndimage.label(binary_mask, structure=CONNECTIVITY_8)
    instances = []
    for label_id in range(1, n + 1):
        inst = labels == label_id
        if inst.sum() >= min_area_px:
            instances.append(inst)
    return instances


def probs_to_rles(prob, threshold=0.5, min_area_px=200, size=MASK_SIZE, where=""):
    """Probability map (any resolution) -> list of compressed-RLE instance strings.

    Upsamples to `size` before thresholding so instance boundaries and the area
    filter are evaluated at the resolution the ground truth (and the scorer)
    use, not at the model's native resolution.
    """
    prob_full = upsample_prob(prob, size)
    binary = prob_full >= threshold
    instances = mask_to_instances(binary, min_area_px)
    rles = [encode_mask(inst) for inst in instances]
    assert_disjoint(rles, where=where)
    return rles
