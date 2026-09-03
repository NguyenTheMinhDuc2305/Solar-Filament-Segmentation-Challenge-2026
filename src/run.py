"""Entrypoint the Kaggle notebook calls.

    python -m src.run --exp exp_0 --out /kaggle/working --data-root /kaggle/input \
        [--config configs/exp_0.yaml] [--smoke]

Trains `cfg["folds"]` folds of a ResNet-34 U-Net on 1024x1024 full-disk images,
predicts out-of-fold for `cv_pq` (the loop's real decision signal), predicts the
test set by averaging every completed fold's probability map, and writes
`submission.csv` + `metrics.json` + `artifacts/` into `--out`.

Resumable by construction: a fold whose checkpoint already exists under
`artifacts/` (restored via `configs/artifact_input.json`, the notebook's
warm-start contract) is loaded instead of retrained, so the 9h wall-clock guard
degrades to "pick up the remaining folds next cycle" rather than losing work.
"""
from __future__ import annotations

import argparse
import json
import time
import traceback
from pathlib import Path

import torch
import yaml

from src import data, metric
from src.infer import (accumulate_probs, mean_probs, predict_stems,
                        probs_to_submission_rows, rows_to_df)
from src.model import build_model
from src.train import train_fold

DEFAULT_CONFIG = {
    "img_size": 1024,
    "encoder": "resnet34",
    "encoder_weights": "imagenet",
    "folds": 5,
    "epochs": 40,
    "batch_size": 4,
    "lr": 3.0e-4,
    "lr_min": 1.0e-6,
    "prob_threshold": 0.5,
    "min_area_px": 200,
    "target": "union",
    "seed": 42,
    "time_budget_hours": 9,
    "num_workers": 2,
}


def load_config(path):
    cfg = dict(DEFAULT_CONFIG)
    if path and Path(path).exists():
        user_cfg = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        cfg.update(user_cfg)
    return cfg


def apply_smoke_overrides(cfg):
    """Cap to a couple of folds/epochs/images so this finishes in under 2 minutes
    on a CPU laptop. `--smoke` is the harness the implement agent proves against
    before ever pushing; it is not a code path Kaggle runs."""
    cfg = dict(cfg)
    cfg["epochs"] = 1
    cfg["folds"] = min(cfg["folds"], 2)
    cfg["batch_size"] = 2
    cfg["img_size"] = min(cfg["img_size"], 64)
    cfg["num_workers"] = 0
    cfg["time_budget_hours"] = 0.2
    return cfg


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--exp", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--data-root", required=True)
    p.add_argument("--config", default=None)
    p.add_argument("--smoke", action="store_true")
    return p.parse_args()


def read_artifact_input(repo_root=Path(".")):
    """Warm-start contract: the notebook writes this when a previous kernel's
    output is mounted. Returns the previous run's `artifacts/` dir, or None."""
    note = repo_root / "configs" / "artifact_input.json"
    if not note.exists():
        return None
    try:
        payload = json.loads(note.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    path = payload.get("artifact_input")
    return Path(path) if path else None


def restore_warm_start(artifacts_dir, repo_root=Path("."), log=print):
    import shutil
    prev = read_artifact_input(repo_root)
    if not prev or not prev.exists():
        log("no warm start available; starting cold")
        return
    shutil.copytree(prev, artifacts_dir, dirs_exist_ok=True)
    log("warm-started artifacts/ from {}".format(prev))


def main():
    args = parse_args()
    cfg = load_config(args.config)
    if args.smoke:
        cfg = apply_smoke_overrides(cfg)

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    artifacts_dir = out_dir / "artifacts"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    cache_dir = artifacts_dir / "cache"

    def log(msg):
        print("[run] " + str(msg), flush=True)

    torch.manual_seed(cfg["seed"])
    restore_warm_start(artifacts_dir, log=log)

    metrics = {"seed": cfg["seed"], "epochs": cfg["epochs"]}
    try:
        run(args, cfg, out_dir, artifacts_dir, cache_dir, metrics, log)
    except Exception as e:  # noqa: BLE001 - never crash without a metrics.json
        metrics["cv_pq"] = None
        metrics["error"] = "{}: {}".format(type(e).__name__, e)
        log("FAILED: " + metrics["error"])
        log(traceback.format_exc())
        write_metrics(out_dir, metrics)
        raise


def run(args, cfg, out_dir, artifacts_dir, cache_dir, metrics, log):
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    log("device: {}".format(device))

    dataset_root = data.find_dataset_root(args.data_root)
    gt_rles = data.load_gt_rles(dataset_root, cache_dir=cache_dir)
    stems = data.image_stems(gt_rles)
    if args.smoke:
        stems = stems[: max(cfg["folds"] * 2, 4)]
    metrics["n_train"] = len(stems)
    log("{} training image stems".format(len(stems)))

    folds = data.make_folds(stems, cfg["folds"], cfg["seed"])
    img_dir, mask_dir, n_built = data.cache_train_arrays(
        dataset_root, gt_rles, stems, cfg["img_size"], cache_dir)
    log("cached {} new image/mask pairs at {}px".format(n_built, cfg["img_size"]))

    try:
        test_stems = data.test_stems(dataset_root)
    except FileNotFoundError:
        test_stems = []
    if args.smoke:
        test_stems = test_stems[:4]
    log("{} test image stems".format(len(test_stems)))
    test_img_dir = img_and_prep_test(dataset_root, test_stems, cfg["img_size"], cache_dir, log)

    start_time = time.time()
    budget_seconds = cfg["time_budget_hours"] * 3600
    oof_rows = []
    oof_stems_scored = []
    test_accum = {}
    n_folds_completed = 0

    for k, (train_stems, val_stems) in enumerate(folds):
        elapsed = time.time() - start_time
        ckpt_path = artifacts_dir / "fold{}.pt".format(k)
        if elapsed > budget_seconds and not ckpt_path.exists():
            log("time budget exhausted ({:.1f}h); stopping before fold {}".format(
                elapsed / 3600, k))
            break

        if ckpt_path.exists():
            model = build_model(cfg["encoder"], cfg.get("encoder_weights", "imagenet"))
            model.load_state_dict(torch.load(ckpt_path, map_location=device))
            model = model.to(device).eval()
            log("fold {}: resumed from checkpoint".format(k))
        else:
            log("fold {}: training on {} images".format(k, len(train_stems)))
            model = train_fold(img_dir, mask_dir, train_stems, cfg, device, log=log)
            torch.save(model.state_dict(), ckpt_path)
            log("fold {}: saved {}".format(k, ckpt_path))

        val_probs = predict_stems(model, img_dir, val_stems, cfg["img_size"], device)
        oof_rows.extend(probs_to_submission_rows(val_probs, cfg))
        oof_stems_scored.extend(val_stems)

        if test_stems:
            test_probs = predict_stems(model, test_img_dir, test_stems, cfg["img_size"], device)
            test_accum = accumulate_probs(test_accum, test_probs)

        n_folds_completed += 1
        del model
        if device.type == "cuda":
            torch.cuda.empty_cache()

    metrics["n_folds_completed"] = n_folds_completed
    metrics["n_folds_configured"] = cfg["folds"]

    if n_folds_completed == 0:
        metrics["cv_pq"] = None
        metrics["error"] = "no fold completed within the time budget"
        write_metrics(out_dir, metrics)
        write_empty_submission(out_dir)
        return

    oof_pred_df = rows_to_df(oof_rows)
    gt_df = data.gt_dataframe(gt_rles, stems=oof_stems_scored)
    oof_summary = metric.score(gt_df, oof_pred_df)
    for key in ("cv_pq", "detection_recall", "mean_matched_iou",
                "one_to_many_rate", "many_to_one_rate", "n_pred_per_image",
                "n_gt_per_annotator_image", "n_annotator_images", "tp", "fp", "fn"):
        metrics[key] = oof_summary[key]

    if test_accum:
        test_rows = probs_to_submission_rows(mean_probs(test_accum), cfg)
    else:
        test_rows = []
    submission = rows_to_df(test_rows)
    if submission.empty:
        log("WARNING: no test predictions produced; writing an empty submission")
    submission.to_csv(out_dir / "submission.csv", index=False)
    write_metrics(out_dir, metrics)
    log("cv_pq = {:.4f} over {} fold(s)".format(metrics["cv_pq"], n_folds_completed))


def img_and_prep_test(dataset_root, test_stems, img_size, cache_dir, log):
    """Resize + cache test images the same way training images are cached."""
    import cv2

    test_img_dir = Path(cache_dir) / "test_img_{}".format(img_size)
    test_img_dir.mkdir(parents=True, exist_ok=True)
    src_dir = data.test_images_dir(dataset_root)
    built = 0
    for stem in test_stems:
        dst = test_img_dir / (stem + ".png")
        if dst.exists():
            continue
        cv2.imwrite(str(dst), data.load_image(src_dir / (stem + ".jpeg"), img_size))
        built += 1
    log("cached {} new test images at {}px".format(built, img_size))
    return test_img_dir


def write_empty_submission(out_dir):
    rows_to_df([]).to_csv(out_dir / "submission.csv", index=False)


def write_metrics(out_dir, metrics):
    (out_dir / "metrics.json").write_text(json.dumps(metrics, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
