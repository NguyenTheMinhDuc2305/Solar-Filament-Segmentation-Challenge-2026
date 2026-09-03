---
description: Show where the competition loop is, what it last did, and whether it is healthy
allowed-tools: Bash, Read, Grep, Glob
---

Report the state of the competition loop. Be concise - this is a status check,
not a review.

## Gather

```bash
python orchestrator/run_loop.py --status
python scripts/kaggle_run.py limits
ls -t orchestrator/logs/cycle-*/ 2>/dev/null | head
git log --oneline -5
```

Also read the `## Summary` table in `shared_memory/RESULTS.md` and the newest
`### Insight` block.

## Report, in this order

1. **Now**: cycle N, stage X, status. If blocked, the reason and the fix.
2. **Progress**: CV PQ trend across cycles against the anchors (ceiling 0.341,
   classical 0.095, target 0.35). Best cycle so far.
3. **Budget**: submission slots left today, days to the 2026-11-15 deadline.
4. **Health**: flag any of these if true -
   - three or more consecutive `inconclusive` verdicts (the loop is spinning)
   - `repair_attempts` or `replan_attempts` above zero (the current cycle is
     fighting a defect rather than testing an idea) - say which and why
   - CV flat for three or more cycles
   - the scout has not run in over 48h
   - the 4-page report / public repo still not started with under 21 days left
5. **Next**: what the loop will do when it next runs.

If nothing is wrong, say so in one line rather than padding the report.
