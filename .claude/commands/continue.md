---
description: Resume the competition agent loop from wherever it stopped (usage limit, crash, manual stop)
allowed-tools: Bash, Read, Grep, Glob
---

Resume the continuous competition loop. Arguments (optional): $ARGUMENTS

## 1. Find out where it stopped

```bash
python orchestrator/run_loop.py --status
```

Read `stage`, `status`, `blocked_reason` and the tail of `history`. Also check
`shared_memory/STATE.md` for the human-readable mirror. If a stage was mid-flight
when the process died, its log is the newest file under
`orchestrator/logs/cycle-*/`; read its last few lines to see how far it got.

## 2. Diagnose before resuming

Match what you find to one of these:

| `blocked_reason` contains | Meaning | What to do |
| --- | --- | --- |
| `usage limit` | Claude quota exhausted mid-stage | Confirm the limit has actually reset, then resume. Resuming too early just re-blocks. |
| `3 consecutive failures` | The same stage failed three times | **Do not blindly resume.** Read that stage's log, find the real error, fix it, then resume. |
| `null` with status `running` | The process was killed (reboot, Ctrl-C) | Safe to resume - the stage re-runs from the start. |
| `null` with status `pending` | Clean stop | Safe to resume. |

Stages are idempotent by design: re-running `research` rewrites `PROPOSAL.md`,
re-running `implement` re-edits and re-pushes. The one stage to look at twice is
`submit` - check whether it already spent a Kaggle slot before letting it re-run:

```bash
python scripts/kaggle_run.py limits
```

If `num_today` shows a submission already went out for this cycle, skip ahead
instead of resubmitting:

```bash
python orchestrator/run_loop.py --resume --stage review
```

## 3. Verify this machine can actually run the loop

Credentials are per-machine and never committed. Confirm they resolve before
starting a long run:

```bash
python scripts/env_setup.py
```

If it reports a missing token, stop and tell the user to fill in `.env` from
`.env.example`. Do not resume into a stage that will fail on authentication.

## 4. Resume

Launch the loop in the background so it survives this Claude session ending:

```bash
python orchestrator/run_loop.py --resume
```

If the user passed a stage name in `$ARGUMENTS`, add `--stage <name>`. If they
asked for a single step, add `--once`.

## 5. Report back

Tell the user, briefly:

- where it resumed from (cycle + stage) and why it had stopped
- anything you had to fix first
- how to watch it: `shared_memory/STATE.md`, or `orchestrator/logs/cycle-<N>/`
- how to stop it: kill the `run_loop.py` process

Do not summarise the whole competition state - just the resume.
