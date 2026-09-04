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

The repo is **public**, so the notebook clones anonymously and **no Kaggle Secret
is required**. This is not a convenience - the Kaggle API has no field for
attaching a secret to a notebook it pushes, so any token-based clone needs a
manual UI step on every newly created notebook, which an unattended loop cannot
do. A public repo removes the problem instead of working around it.

Each member only needs their own `.env`:

1. Copy `.env.example` to `.env` and fill in `KAGGLE_API_TOKEN` (and
   `GITHUB_TOKEN` for pushing code from your machine). `.env` is gitignored; it
   is the only place your identity lives.
2. Verify: `uv run python scripts/env_setup.py` should print your Kaggle username.

Kernel slugs (`filament-runner-<exp>`) are namespaced under whoever runs them, so
two members can run the same experiment without colliding. Weights and the
processed cache persist as **kernel output** and chain into the next run via
`kernel_sources`.

If the repo is ever made private again, the notebook falls back to a
`GITHUB_TOKEN` Kaggle Secret - which then has to be attached by hand, per
notebook, and `stable_kernel_slug` in `orchestrator/config.json` must be set to
`true` so there is only one notebook to attach it to.

## What `src/run.py` must honour

| Path | Direction | Meaning |
| --- | --- | --- |
| `/kaggle/input/filament-segmentation-2026/` | read | competition data |
| `/kaggle/input/<previous kernel>/` | read | previous weights + processed cache, if any |
| `configs/artifact_input.json` | read | written by the notebook when the above exists |
| `/kaggle/working/submission.csv` | write | **pixel-disjoint masks per image** |
| `/kaggle/working/metrics.json` | write | must contain a real local OOF `cv_pq` |
| `/kaggle/working/artifacts/` | write | kept as kernel output, chained into the next run |

`run_status.json` is always written, even on failure, so the submit agent can
tell a crash from a bad score.
