# Competition intelligence
_Last scouted: 2026-09-03T08:00:00Z by human seed (not yet by the scout agent)_

> Seed file. The scout agent rewrites this in full on its first run and every 24h
> after. Everything below was verified against the Kaggle API on 2026-09-03.

## Snapshot

| Field | Value | Source |
| --- | --- | --- |
| Slug | `filament-segmentation-2026` | API |
| Metric | `panoptic_quality` | API `competitions_list` |
| Deadline | 2026-11-15 06:00 UTC (**73 days left**) | API |
| Teams entered | 496 | API |
| Daily submission limit | **5** | API `competition_get_submission_limits` |
| Kernels-only submissions | **No** - file submission, notebooks are ours to organise | API `is_kernels_submissions_only=False` |
| Prize | $3,000 (IEEE BigData Cup, NSF/NSO) | competition page |
| Winners announced | 2026-11-30 | competition page |

## Findings

_Newest first. The scout agent maintains this section; the four items below are
the carried-over baseline and should be re-verified, not re-derived._

1. **[LEAK] The public leaderboard is compromised.** All 180 test images (139
   byte-identical) exist in the public MAGFiLO 1.0 release on Harvard Dataverse
   (doi:10.7910/DVN/J6JNVK) **with** ground truth. On 2026-07-27 the organizers
   ruled that using external MAGFiLO annotations means the submission is
   disregarded. Top LB scores (~0.55) are widely believed to be leak-derived.
   On 2026-08-24 the organizers stated that "any PQ score above 0.35 is of great
   value." _Source: competition forum, organizer posts._
2. **[STALE METRIC] The metric changed on 2026-08-07**, from mean Dice to
   Panoptic Quality; the board was re-scored on 2026-08-12. Under mean Dice, an
   empty mask scored 0.93 - which is why pre-August forum numbers are worthless
   for comparison. One participant's identical file went 0.66 -> 0.31 across the
   change. **Check the date on every forum score you read.**
3. **The leaderboard is only part of the grade.** Winners are selected on a
   rubric: 70% quantitative (PQ plus Dice/IoU/one-to-many distributions) + 30%
   qualitative (a 4-page PDF report submitted via Google Form, a public Git repo,
   modular code). The report and public repo are **mandatory to be judged at all**.
4. **[HARD CONSTRAINT] Submitted masks must be pixel-disjoint per image.**
   Overlapping instances make the submission ERROR and burn one of the 5 daily
   slots.

## Landscape

_Empty - the scout agent fills this on its first run: public notebooks and their
approaches, related datasets, relevant papers, and for each one whether its
reported score is comparable under the current metric and whether it is usable
given the leak ruling._

## Actionable

1. Establish an honest end-to-end baseline that produces a valid `submission.csv`
   and a real local OOF `cv_pq` - the prior code (`scripts/eda.py`,
   `scripts/eval_baseline.py`) is **gone from the repo** and only its measured
   numbers survive, in `## Rejected` below and in the anchors table.
2. Attack **detection recall**, not boundary quality: 43.7% of filaments are seen
   by only one of two annotators, so that is where PQ is lost.
3. Budget real calendar time for the 4-page report and the public repo - they are
   30% of the grade and cannot be produced in the final week.
4. The repo is currently **private**; it must be public before judging.

## Rejected

- **Intensity thresholding** - filament vs quiet-disk intensity histograms overlap
  66% (means 117 vs 128). Cannot separate the classes. _Measured._
- **Trusting CLAHE as a universal preprocessing win** - tuned for a network, it
  made the classical baseline 16x worse by amplifying granulation. Re-ablate
  preprocessing per model family. _Measured._
- **Using MAGFiLO annotations for the test images** - explicit disqualification
  per the 2026-07-27 organizer ruling. Not an option at any score.
- **Optimising against the public LB** - compromised by the leak; a submission
  buys format validation, not evidence.
