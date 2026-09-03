# Solar Filament Segmentation Challenge 2026

A continuous five-agent loop that competes autonomously: it scouts the outside
world, proposes an experiment, implements it, runs it on Kaggle, and reviews the
result to steer the next proposal.

## The loop

```
        ┌──────────────── every 24h ────────────────┐
        │                                           │
        ▼                                           │
     scout ──> COMPETITION.md                       │
                    │                               │
                    ▼                               │
   ┌─────────> research ──> PROPOSAL.md             │
   │                │                               │
   │                ▼                               │
   │           implement ──> git push ──────────────┤
   │                │                               │
   │                ▼                               │
   │             submit ──> Kaggle kernel ──> RESULTS.md
   │                │                               │
   │                ▼                               │
   └───────────  review ──> RESULTS.md ### Insight ─┘
```

A run that produces no usable score is a defect, not a cycle, and the two causes
route differently:

```
             submit classifies the run in orchestrator/handoff.json
                                   │
      ┌────────────────────────────┼────────────────────────────┐
      ▼                            ▼                            ▼
  success                      dev_bug                    logic_error
  real cv_pq               our code is broken        the proposal itself
  (even a bad one)                                      cannot work
      │                            │                            │
      ▼                            ▼                            ▼
   review              ──> implement (same proposal)      research (new proposal)
                        max 3 repairs, then escalates ──────────┘
                                              max 2 replans, then parks for a human
```

`orchestrator/run_loop.py` drives it. Each stage is a **separate headless Claude
session** (`claude -p --agent <name>`), so context never accumulates and any stage
can be retried alone. All durable state is in `orchestrator/state.json`.

| Agent | Reads | Writes | Model / effort |
| --- | --- | --- | --- |
| `scout` | Kaggle API, forum, web | `shared_memory/COMPETITION.md` | opus / max |
| `research` | COMPETITION.md, RESULTS.md | `shared_memory/PROPOSAL.md` | opus / max |
| `implement` | PROPOSAL.md | `src/`, `configs/`, git push | sonnet / high |
| `submit` | PROPOSAL.md, Kaggle | `RESULTS.md`, `handoff.json` | haiku 4.5 / medium |
| `review` | PROPOSAL.md, RESULTS.md | `RESULTS.md` `### Insight` | opus / max |

## Commands

| Command | Purpose |
| --- | --- |
| `/loop-start` | start the loop (preflight + launch) |
| `/continue` | resume after a usage limit, crash, or manual stop |
| `/loop-status` | where it is, how it is trending, whether it is healthy |

Direct equivalents: `python orchestrator/run_loop.py [--resume] [--once] [--stage X] [--cycles N] [--status]`.

## shared_memory/ is the team's shared record

`shared_memory/` is committed to git and is the **only** shared state. Everything
about *who* is running stays machine-local.

| File | Owner | Discipline |
| --- | --- | --- |
| `COMPETITION.md` | scout | rewritten in full every 24h |
| `PROPOSAL.md` | research | overwritten each cycle |
| `RESULTS.md` | submit + review | **append-only** - never rewrite a past cycle |
| `STATE.md` | orchestrator | auto-generated mirror, do not hand-edit |
| `archive/` | review | cycle blocks older than the last 5 |

## Team setup - each member, their own Kaggle account

Credentials live only in `.env` (gitignored). Nothing identifying is committed:
`orchestrator/config.json` has `kaggle.username: null` on purpose, resolved at
runtime by `scripts/env_setup.py`.

```bash
cp .env.example .env      # fill in KAGGLE_API_TOKEN and GITHUB_TOKEN
python scripts/env_setup.py   # should print your Kaggle username
```

Kernel slugs are namespaced per member, so nobody collides.

`GITHUB_TOKEN` is the **only** Kaggle Secret needed (`notebooks/README.md`): the notebook runs on Kaggle's
machines and cannot see your `.env`, so it needs that token to clone this private
repo. Every Kaggle API call - submit, poll, fetch results, forum scouting - runs
locally with the token in `.env`.

## Compute

This machine has **no CUDA GPU**. Never train locally. The flow is:

```
implement pushes commit ──> kernel clones that SHA ──> trains + infers on Kaggle
                                                            │
      weights + processed cache ──> kept as kernel output ─┘  (never touch a laptop,
                                    chained into the next run via kernel_sources)
              submission.csv + metrics.json ──> pulled locally ──> submitted
```

`python scripts/kaggle_run.py run --exp exp_N --commit <sha> [--submit]` does
push → poll → pull → submit → **wait for the score** in one call.

## Rules that override any local reasoning

1. **CV, not LB.** The public leaderboard is compromised - all 180 test images are
   in the public MAGFiLO 1.0 release with ground truth. `cv_pq` in `metrics.json`
   must be a real local out-of-fold score. Never write an LB score into it.
2. **Never use MAGFiLO annotations for the test images.** Organizer ruling
   2026-07-27: the submission is disregarded. Not an option at any score.
3. **Masks must be pixel-disjoint per image.** An overlap ERRORs the submission
   and burns one of the 5 daily slots.
4. **Never fabricate a metric.** `"cv_pq": null` plus an `error` explaining why is
   far more useful than a plausible number.
5. **30% of the grade is not the model**: a 4-page PDF report, a public repo, and
   modular code are mandatory to be judged at all. The repo is **deliberately
   private for now**; it must be flipped to public before judging.

## Anchors

| Anchor | Value |
| --- | --- |
| Inter-annotator PQ (ceiling) | **0.341** |
| Classical CV baseline | **0.095** |
| Organizer "great value" bar | **0.35** |
| Single-annotator filaments | **43.7%** - detection is the bottleneck, not boundaries |
| Filament/quiet-disk intensity overlap | **66%** - thresholding cannot work |

Deadline **2026-11-15 06:00 UTC**. 5 submissions/day.
