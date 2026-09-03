# Results ledger

_Append-only. One block per cycle. **CV is the decision signal**; the public LB is
compromised by a test-set leak, so it validates format, not ideas._

## Deadline check
_Maintained by the review agent each cycle._

- Deadline: **2026-11-15 06:00 UTC**
- Days remaining: **73** (as of 2026-09-03)
- Cycles completed: **0**
- Qualitative deliverables (30% of grade): 4-page PDF report **not started**,
  public repo **not yet public**, modular code **not started**

## Anchors

| Anchor | Value | Meaning |
| --- | --- | --- |
| Inter-annotator PQ | 0.341 | practical ceiling - two humans agree only this much |
| Classical CV baseline | 0.095 | below this is not progress |
| Empty submission | 0.000 | the current metric no longer rewards predicting nothing |
| Organizer "great value" bar | 0.35 | approximately human-level |
| Matched-pair IoU (human) | 0.634 | boundary agreement between annotators |
| Single-annotator filaments | 43.7% | why detection, not boundaries, is the bottleneck |

## Summary

| cycle | exp | primary variable | CV PQ | LB | member | verdict |
| --- | --- | --- | --- | --- | --- | --- |
| 0 | exp_0 | end-to-end pipeline | null | — | ngtmduc | inconclusive |

## Cycle 0 - exp_0
_Ran: 2026-09-03T16:25:00Z | member: ngtmduc | kaggle_user: ngtmduc | commit: incomplete_

### Setup
Primary variable: **a working end-to-end pipeline exists**.

Config highlights:
- Input size: 1024×1024
- Encoder: ResNet-34 (ImageNet pretrained)
- Training: 5-fold CV, 40 epochs, batch size 4, AdamW lr 3e-4 with cosine decay to 1e-6
- Loss: 0.5×BCEWithLogits + 0.5×SoftDice
- Post-processing: sigmoid → upsample to 2048 → threshold 0.5 → connected components → min area 200 px
- Expected cost: 5–7 GPU hours on Kaggle P100/T4

Kernel URL: _not queued (implementation incomplete)_
GPU hours: 0 (run did not execute)

### Metrics
| metric | value | vs cycle 0 | vs anchor |
| --- | --- | --- | --- |
| CV PQ | null | — | ceiling 0.341 / classical 0.095 |
| detection recall | null | — | — |
| mean matched IoU | null | — | 0.634 human |
| one-to-many rate | null | — | — |
| n_folds_completed | 0 | — | target 5 |

### Submission
**Slot not spent.** No submission attempted because the kernel did not run.

### Run notes
**BLOCKED: Implementation incomplete. Required files missing.**

The implement agent created a partial implementation with four of eight required modules:
- ✓ `src/metric.py` — Panoptic Quality scorer, ported from organizer's notebook
- ✓ `src/data.py` — COCO data loader, RLE cache layer
- ✓ `src/model.py` — ResNet-34 U-Net builder
- ✓ `src/postprocess.py` — connected-components post-processor
- **✗ `src/train.py`** — training loop
- **✗ `src/infer.py`** — inference and OOF prediction
- **✗ `src/run.py`** — notebook entrypoint (required by `runner_template.py`)
- **✗ `configs/exp_0.yaml`** — hyperparameter manifest

Without `src/run.py` and the config, the kernel runner cannot queue work on Kaggle. The partial implementation exists in local `src/` but has not been pushed to `origin/main`.

**Verdict: inconclusive.** The pipeline did not run; this is a code completeness issue (dev_bug), not a model or metric problem.
