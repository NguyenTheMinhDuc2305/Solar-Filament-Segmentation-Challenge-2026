# Kaggle runner notebook

`runner_template.py` is the body of the notebook that runs every experiment.
`scripts/kaggle_run.py` wraps it into a 2-cell `.ipynb` at push time - cell 1 is
the injected `PARAMS` dict, cell 2 is the template verbatim. **Do not edit the
notebook in the Kaggle UI**; edit the template and re-push, or the change is lost
on the next run.

## Why a bootstrap notebook

The notebook is deliberately thin and stable: it clones the repo at a pinned
commit and hands control to `src/run.py`. A broken experiment then never requires
re-pushing the notebook, and every run is reproducible from a SHA.

```
push kernel ──> clone repo @ <sha> ──> pip install ──> python -m src.run
                                                            │
        submission.csv + metrics.json <─────────────────────┘
        artifacts/ ──> versioned as a private Kaggle dataset
```

## One-time setup per team member

Each member trains on their **own** Kaggle account, so each does this once:

1. **Kaggle Secrets** - open any notebook, `Add-ons -> Secrets`, and add:
   | Secret name | Value |
   | --- | --- |
   | `GITHUB_TOKEN` | your GitHub PAT with `repo` scope - lets the kernel clone this repo |
   | `KAGGLE_API_TOKEN` | your own Kaggle token - lets the kernel version the artifact dataset |

   Attach both to the notebook the first time it runs (Kaggle asks).

2. **Local `.env`** - copy `.env.example` to `.env` and fill in the same two
   tokens. `.env` is gitignored; it is the only place your identity lives.

3. Verify: `python scripts/env_setup.py` should print your Kaggle username.

Kernel slugs (`filament-runner-<exp>`) and the artifact dataset
(`<you>/filament-artifacts`) are namespaced under whoever runs them, so two
members can run the same experiment without colliding. Only `shared_memory/` is
shared, through git.

## What `src/run.py` must honour

| Path | Direction | Meaning |
| --- | --- | --- |
| `/kaggle/input/filament-segmentation-2026/` | read | competition data |
| `/kaggle/input/filament-artifacts/` | read | previous weights + processed cache, if any |
| `configs/artifact_input.json` | read | written by the notebook when the above exists |
| `/kaggle/working/submission.csv` | write | **pixel-disjoint masks per image** |
| `/kaggle/working/metrics.json` | write | must contain a real local OOF `cv_pq` |
| `/kaggle/working/artifacts/` | write | persisted to the Kaggle dataset |

`run_status.json` is always written, even on failure, so the submit agent can
tell a crash from a bad score.
