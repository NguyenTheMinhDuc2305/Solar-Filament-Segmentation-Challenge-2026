"""CPU smoke test: proves the pipeline runs end to end before anything is pushed.

    python -m tests.test_smoke

Builds a tiny synthetic fixture (no GPU, no real data needed), runs
`src.run.main` through it with `--smoke`, and asserts the output contract:
`submission.csv` parses and is pixel-disjoint per image, `metrics.json` has a
numeric `cv_pq`, and `import src.run` has no import-time side effects.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src import data, metric  # noqa: E402
from tests.make_fixture import build as build_fixture  # noqa: E402


def check_rle_round_trip():
    """A GT polygon, encoded then decoded, must round-trip at exactly its own
    area - catches an RLE-dialect mismatch (bytes vs str counts, wrong canvas
    size) before it can silently corrupt a submission."""
    poly = [[300, 300, 360, 300, 360, 360, 300, 360]]
    rle = data.polygon_to_rle(poly)
    layer = metric.rles_to_layers([rle])[0]
    assert layer.sum() == 60 * 60, "round-tripped area mismatch: {}".format(layer.sum())
    print("OK  RLE round trip")


def check_no_import_side_effects():
    result = subprocess.run(
        [sys.executable, "-c", "import src.run"], cwd=str(ROOT),
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    assert result.returncode == 0, "import src.run failed:\n" + result.stderr
    print("OK  import src.run is clean")


def check_submission_disjoint(sub_path):
    df = pd.read_csv(sub_path)
    assert list(df.columns) == ["filament_id", "segmentation_rle"], list(df.columns)
    by_image = {}
    for _, row in df.iterrows():
        stem = str(row["filament_id"]).rsplit("_", 1)[0]
        by_image.setdefault(stem, []).append(row["segmentation_rle"])
    for stem, rles in by_image.items():
        metric.assert_disjoint(rles, where=stem)
    print("OK  submission.csv: {} row(s) over {} image(s), pixel-disjoint".format(
        len(df), len(by_image)))


def check_metrics(met_path):
    m = json.loads(met_path.read_text(encoding="utf-8"))
    assert "cv_pq" in m, "metrics.json missing cv_pq"
    assert isinstance(m["cv_pq"], (int, float)), "cv_pq is not numeric: {!r}".format(m["cv_pq"])
    for key in ("detection_recall", "mean_matched_iou", "one_to_many_rate",
                "n_train", "epochs", "seed"):
        assert key in m, "metrics.json missing '{}'".format(key)
    print("OK  metrics.json: cv_pq={}".format(m["cv_pq"]))


def run_pipeline(out_dir, data_root):
    argv = [
        sys.executable, "-m", "src.run",
        "--exp", "exp_0", "--out", str(out_dir),
        "--data-root", str(data_root), "--smoke",
    ]
    result = subprocess.run(argv, cwd=str(ROOT), capture_output=True, text=True,
                             encoding="utf-8", errors="replace")
    print(result.stdout[-4000:])
    if result.returncode != 0:
        print(result.stderr[-4000:])
        raise AssertionError("src.run --smoke exited {}".format(result.returncode))


def main():
    check_rle_round_trip()
    check_no_import_side_effects()

    tmp = Path(tempfile.mkdtemp(prefix="filament_smoke_"))
    try:
        data_root = tmp / "input"
        out_dir = tmp / "out"
        build_fixture(data_root)

        run_pipeline(out_dir, data_root)

        check_submission_disjoint(out_dir / "submission.csv")
        check_metrics(out_dir / "metrics.json")
        print("SMOKE TEST PASSED")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
