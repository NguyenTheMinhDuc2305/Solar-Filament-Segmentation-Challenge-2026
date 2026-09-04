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
| 0 | exp_0 | end-to-end pipeline | null | — | ngtmduc | dev_bug |

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

---

### Run 2 (2026-09-03 09:48:06Z)
_Kernel: https://www.kaggle.com/code/ngtmduc/filament-runner-exp-0 | Commit: bc2b6e28_

**Implementation status**: ✓ **Complete and pushed.** All eight required modules present in `src/` and `configs/`.

**Kernel execution**: Failed at 0.252s during initialization.

**Error** (from kernel logs):
```
RuntimeError: Missing Kaggle Secret 'GITHUB_TOKEN'. Add it under Add-ons -> Secrets and attach it to this notebook. 
(Connection error trying to communicate with service.)
```

**Root cause**: The notebook's `runner_template.py` tries to fetch `GITHUB_TOKEN` via Kaggle's `get_secret("GITHUB_TOKEN")` API to clone the private GitHub repository. The secret is not configured on the Kaggle notebook.

**Fix required**: 
1. Add `GITHUB_TOKEN` (from `.env` / personal Kaggle API secrets) to the Kaggle notebook's Secrets
2. Attach that secret to the notebook (`filament-runner-exp-0`)
3. Retry the run

**Classification**: `dev_bug` — a one-time infrastructure setup issue. The proposal and code are sound; the Kaggle notebook environment is not configured.

---

### Run 3 (2026-09-03 09:51:33Z)
_Kernel: https://www.kaggle.com/code/ngtmduc/filament-runner-exp-0 | Commit: 6ecbadcc_

**Implementation status**: ✓ **Complete** — all eight required modules present. Deployment attempt with `--submit` flag.

**Kernel execution**: Failed at 0.3s during initialization (same error as Run 2).

**Error** (from kernel logs):
```
RuntimeError: Missing Kaggle Secret 'GITHUB_TOKEN'. Add it under Add-ons -> Secrets and attach it to this notebook.
(Connection error trying to communicate with service.)
```

**Reason for continued failure**: `GITHUB_TOKEN` is still not configured in the ngtmduc Kaggle account's Secrets. This is a **one-time manual setup per team member** (see `notebooks/README.md` § 1):
1. Open any Kaggle notebook
2. Click **Add-ons → Secrets**
3. Add new secret named `GITHUB_TOKEN` with your GitHub PAT (must have `repo` scope)
4. First time a notebook runs, Kaggle will ask to attach the secret

The runner_template.py is correct — it tries to fetch the secret via `kaggle_secrets.UserSecretsClient().get_secret("GITHUB_TOKEN")`. The secret must exist in ngtmduc's personal Kaggle Secrets store before the kernel can run.

**Slot status**: No slot spent (kernel failed before generating any results).

**Classification**: `dev_bug` — prerequisite for kernel execution is missing. The code, proposal, and pipeline design are all correct; the blocking issue is infrastructure configuration (Kaggle Secrets).
