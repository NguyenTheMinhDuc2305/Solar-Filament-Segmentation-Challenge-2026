---
description: Start the continuous competition agent loop from a clean state
allowed-tools: Bash, Read, Grep, Glob
---

Start the competition loop. Arguments (optional, e.g. `--cycles 3`): $ARGUMENTS

## 1. Preflight - do not skip

```bash
python scripts/env_setup.py                 # this machine's Kaggle identity + tokens
python scripts/kaggle_run.py limits         # submission slots available today
python orchestrator/run_loop.py --status    # existing state, if any
git status --short && git log -1 --oneline  # repo is clean and pushed
```

Stop and report if: a token is missing, the git working tree is dirty, or the
loop is already `running` (check for a live `run_loop.py` process first - two
loops racing on the same shared memory will corrupt the ledger).

## 2. Confirm the shape of the run

If `orchestrator/state.json` already exists with completed cycles, this is a
**restart, not a fresh start** - use `/continue` instead unless the user
explicitly wants to reset. Resetting means deleting `state.json`, which throws
away the cycle counter and scout timer but leaves `shared_memory/` intact.

## 3. Launch

Run in the background so it survives this session:

```bash
python orchestrator/run_loop.py
```

Pass through anything in `$ARGUMENTS` (`--cycles N`, `--once`, `--stage <name>`).
For a first run, `--cycles 1` is the sensible default so the user sees one full
scout -> research -> implement -> submit -> review pass before committing to an
open-ended run.

## 4. Report

Cycle and stage it started at, whether scout will fire immediately (it does if
`last_scout_at` is null or older than 24h), where to watch
(`shared_memory/STATE.md`, `orchestrator/logs/`), and how to stop it.
