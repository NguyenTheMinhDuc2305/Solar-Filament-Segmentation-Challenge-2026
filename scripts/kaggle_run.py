#!/usr/bin/env python
"""Drive one experiment through Kaggle: push kernel -> poll -> pull output -> submit -> score.

Every identity here is resolved from this machine's `.env` at runtime
(see scripts/env_setup.py), so a teammate can run the exact same command on
their own laptop under their own Kaggle account with no edits.

Typical use by the `submit` agent:

    python scripts/kaggle_run.py run --exp exp_0007 --commit <sha> --submit
    python scripts/kaggle_run.py limits
    python scripts/kaggle_run.py score --exp exp_0007
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from env_setup import ROOT, kaggle_api, load_env, runner_identity  # noqa: E402

CONFIG = json.loads((ROOT / "orchestrator" / "config.json").read_text(encoding="utf-8"))
WORK = ROOT / ".kaggle_work"
TEMPLATE = ROOT / "notebooks" / "runner_template.py"

TERMINAL_OK = {"complete"}
TERMINAL_BAD = {"error", "cancelacknowledged", "cancelrequested"}


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def log(msg: str) -> None:
    print("[{}] {}".format(now_iso(), msg), flush=True)


def slugify(s: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9-]+", "-", s.lower()).strip("-")
    return re.sub(r"-{2,}", "-", s)[:48]


def git_head() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=str(ROOT),
            capture_output=True, text=True, check=True,
        )
        return out.stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return ""


# ------------------------------------------------------------------ notebook gen
def build_notebook(exp: str, commit: str, ident: dict) -> dict:
    """Wrap notebooks/runner_template.py into a 2-cell .ipynb.

    The template is kept as plain Python so it stays diffable and lintable;
    only the parameter cell differs between runs.
    """
    if not TEMPLATE.exists():
        sys.exit("FATAL: missing {}".format(TEMPLATE))
    gh = CONFIG["github"]
    params = "\n".join([
        "# --- injected by scripts/kaggle_run.py, do not edit in the Kaggle UI ---",
        "PARAMS = {",
        "    'exp': {!r},".format(exp),
        "    'commit': {!r},".format(commit),
        "    'repo_owner': {!r},".format(gh["owner"]),
        "    'repo_name': {!r},".format(gh["repo"]),
        "    'branch': {!r},".format(gh["branch"]),
        "    'competition': {!r},".format(CONFIG["competition"]["slug"]),
        "    'artifact_dataset': {!r},".format(CONFIG["kaggle"]["artifact_dataset_slug"]),
        "    'kaggle_user': {!r},".format(ident["kaggle_user"]),
        "    'member': {!r},".format(ident["member"]),
        "    'launched_at': {!r},".format(now_iso()),
        "}",
        "print('PARAMS', PARAMS)",
    ])
    body = TEMPLATE.read_text(encoding="utf-8")

    def cell(src: str) -> dict:
        lines = src.splitlines(keepends=True)
        return {"cell_type": "code", "metadata": {}, "execution_count": None,
                "outputs": [], "source": lines}

    return {
        "cells": [cell(params), cell(body)],
        "metadata": {
            "kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
            "language_info": {"name": "python", "version": "3.11"},
        },
        "nbformat": 4,
        "nbformat_minor": 5,
    }


def kernel_slug(exp: str) -> str:
    return "{}-{}".format(CONFIG["kaggle"]["kernel_slug_prefix"], slugify(exp))


def kernel_id(exp: str, ident: dict) -> str:
    return "{}/{}".format(ident["kaggle_user"], kernel_slug(exp))


def stage_kernel(exp: str, commit: str, ident: dict) -> Path:
    """Materialise the push folder: kernel-metadata.json + runner.ipynb."""
    folder = WORK / kernel_slug(exp)
    if folder.exists():
        shutil.rmtree(folder)
    folder.mkdir(parents=True)

    kcfg = CONFIG["kaggle"]
    meta = {
        "id": kernel_id(exp, ident),
        "title": kernel_slug(exp),
        "code_file": "runner.ipynb",
        "language": "python",
        "kernel_type": "notebook",
        "is_private": True,
        "enable_gpu": bool(kcfg["enable_gpu"]),
        "enable_internet": bool(kcfg["enable_internet"]),
        "dataset_sources": [],
        "competition_sources": [CONFIG["competition"]["slug"]],
        "kernel_sources": [],
    }
    # Warm-start from this member's own artifact dataset if it already exists.
    art = "{}/{}".format(ident["kaggle_user"], kcfg["artifact_dataset_slug"])
    if dataset_exists(art):
        meta["dataset_sources"].append(art)
        log("attaching artifact dataset {}".format(art))

    (folder / "kernel-metadata.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    (folder / "runner.ipynb").write_text(
        json.dumps(build_notebook(exp, commit, ident), indent=1), encoding="utf-8")
    return folder


def dataset_exists(ref: str) -> bool:
    api = kaggle_api()
    owner, slug = ref.split("/", 1)
    try:
        res = api.dataset_list(user=owner, search=slug)
        items = getattr(res, "datasets", None) or res
        return any(str(getattr(d, "ref", "")).lower() == ref.lower() for d in items)
    except Exception:  # noqa: BLE001 - absence is the common case, treat errors as absent
        return False


# ---------------------------------------------------------------------- actions
def cmd_push(args) -> int:
    api = kaggle_api()
    ident = runner_identity(api)
    commit = args.commit or git_head()
    if not commit:
        log("WARNING: no git commit resolved; the notebook will clone branch HEAD")
    folder = stage_kernel(args.exp, commit, ident)
    log("pushing kernel {} (commit {})".format(kernel_id(args.exp, ident), commit[:8] or "HEAD"))
    resp = api.kernels_push(str(folder))
    url = getattr(resp, "url", None) or getattr(resp, "ref", "")
    err = getattr(resp, "error", None)
    if err:
        log("push ERROR: {}".format(err))
        return 1
    log("pushed -> {}".format(url))
    return 0


def poll_kernel(exp: str, ident: dict) -> tuple[str, str]:
    api = kaggle_api()
    kid = kernel_id(exp, ident)
    kcfg = CONFIG["kaggle"]
    deadline = time.time() + kcfg["kernel_timeout_seconds"]
    last = None
    while time.time() < deadline:
        try:
            st = api.kernels_status(kid)
            status = str(getattr(st, "status", "")).lower()
            fail = getattr(st, "failure_message", "") or ""
        except Exception as e:  # noqa: BLE001 - transient API blips shouldn't kill the wait
            log("status poll error ({}); retrying".format(e))
            time.sleep(kcfg["kernel_poll_seconds"])
            continue
        status = status.replace("kernelworkerstatus_", "")
        if status != last:
            log("kernel {} -> {}".format(kid, status))
            last = status
        if status in TERMINAL_OK:
            return "complete", ""
        if status in TERMINAL_BAD:
            return "error", fail or status
        time.sleep(kcfg["kernel_poll_seconds"])
    return "timeout", "exceeded {}s".format(kcfg["kernel_timeout_seconds"])


def cmd_wait(args) -> int:
    ident = runner_identity()
    status, detail = poll_kernel(args.exp, ident)
    log("kernel finished: {} {}".format(status, detail))
    return 0 if status == "complete" else 1


def cmd_pull(args) -> int:
    api = kaggle_api()
    ident = runner_identity(api)
    out = WORK / "output" / slugify(args.exp)
    out.mkdir(parents=True, exist_ok=True)
    kid = kernel_id(args.exp, ident)
    log("downloading output of {} -> {}".format(kid, out))
    api.kernels_output(kid, str(out), force=True, quiet=False)
    got = sorted(p.name for p in out.rglob("*") if p.is_file())
    log("pulled {} file(s): {}".format(len(got), ", ".join(got[:12])))
    metrics = out / "metrics.json"
    if metrics.exists():
        log("metrics.json: {}".format(metrics.read_text(encoding="utf-8")[:600]))
    else:
        log("WARNING: metrics.json not found in kernel output")
    return 0


def cmd_limits(args) -> int:
    api = kaggle_api()
    lim = api.competition_get_submission_limits(CONFIG["competition"]["slug"])
    info = {
        "num_today": getattr(lim, "num_today", None),
        "num_allowed_now": getattr(lim, "num_allowed_now", None),
        "limited_by_total": getattr(lim, "limited_by_total", None),
    }
    print(json.dumps(info, indent=2))
    return 0


def _norm_status(s) -> str:
    """'SubmissionStatus.PENDING' / 'PENDING' / 0 -> 'pending'."""
    txt = str(getattr(s, "name", s) or "").lower()
    return txt.rsplit(".", 1)[-1].replace("submissionstatus_", "").strip()


def _find_submission(api, comp, target_ref=None, target_msg=None):
    """The submission we care about: by ref, else by description, else the newest."""
    subs = api.competition_submissions(comp) or []
    if not subs:
        return None
    if target_ref:
        for s in subs:
            if str(getattr(s, "ref", "")) == str(target_ref):
                return s
    if target_msg:
        for s in subs:
            if str(getattr(s, "description", "")) == target_msg:
                return s
    return subs[0]


def wait_for_score(api, comp, target_ref=None, target_msg=None,
                   timeout=1800, interval=20):
    """Block until Kaggle finishes scoring, then return the verdict.

    Kaggle scores asynchronously, so submitting is only half the job - this is the
    loop that turns a submission into a number the review agent can act on.
    Returns a dict that is always safe to serialise into RESULTS.md.
    """
    started = time.time()
    attempt = 0
    last_status = None

    while True:
        attempt += 1
        elapsed = time.time() - started

        try:
            s = _find_submission(api, comp, target_ref, target_msg)
        except Exception as e:  # noqa: BLE001 - transient API errors must not abort the wait
            log("poll #{} failed ({}); retrying in {}s".format(attempt, e, interval))
            s = None

        if s is not None:
            status = _norm_status(getattr(s, "status", ""))
            if status != last_status:
                log("poll #{} | {:.0f}s | status={}".format(attempt, elapsed, status or "?"))
                last_status = status
            elif attempt % 5 == 0:
                log("poll #{} | {:.0f}s | still {}".format(attempt, elapsed, status or "?"))

            if status == "complete":
                pub = getattr(s, "public_score", None)
                prv = getattr(s, "private_score", None)
                log("SCORED after {:.0f}s: public={} private={}".format(elapsed, pub, prv))
                return {
                    "status": "complete",
                    "public_score": _as_float(pub),
                    "private_score": _as_float(prv),
                    "description": str(getattr(s, "description", "")),
                    "file_name": str(getattr(s, "file_name", "")),
                    "date": str(getattr(s, "date", "")),
                    "url": str(getattr(s, "url", "")),
                    "ref": str(getattr(s, "ref", "")),
                    "waited_seconds": round(elapsed, 1),
                    "polls": attempt,
                }
            if status == "error":
                err = str(getattr(s, "error_description", "") or "unknown error")
                log("SUBMISSION ERRORED after {:.0f}s: {}".format(elapsed, err))
                return {
                    "status": "error",
                    "error": err,
                    "ref": str(getattr(s, "ref", "")),
                    "waited_seconds": round(elapsed, 1),
                    "polls": attempt,
                    "hint": ("A common cause here is overlapping instance masks - the "
                             "competition requires pixel-disjoint masks per image. "
                             "This still consumed one of the 5 daily slots."),
                }

        if elapsed > timeout:
            log("TIMED OUT after {:.0f}s and {} polls; last status={}".format(
                elapsed, attempt, last_status))
            return {
                "status": "timeout",
                "last_status": last_status,
                "waited_seconds": round(elapsed, 1),
                "polls": attempt,
                "hint": "Scoring may still finish later - re-check with `kaggle_run.py score`.",
            }

        time.sleep(interval)


def _as_float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _save_result(args, result: dict) -> None:
    """Persist the verdict so the submit agent reads a file, not scrollback."""
    exp = getattr(args, "exp", None)
    if not exp:
        return
    out = WORK / "output" / slugify(exp)
    out.mkdir(parents=True, exist_ok=True)
    (out / "submission_result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    log("wrote {}".format(out / "submission_result.json"))


def cmd_submit(args) -> int:
    api = kaggle_api()
    ident = runner_identity(api)
    comp = CONFIG["competition"]["slug"]

    lim = api.competition_get_submission_limits(comp)
    allowed = getattr(lim, "num_allowed_now", 0) or 0
    if allowed <= 0:
        log("REFUSING to submit: Kaggle reports 0 slots remaining today "
            "(num_today={}).".format(getattr(lim, "num_today", "?")))
        return 2

    path = Path(args.file)
    if not path.is_absolute():
        path = ROOT / path
    if not path.exists():
        log("FATAL: submission file not found: {}".format(path))
        return 1

    msg = args.message or "{} | {} | {}".format(args.exp or "run", ident["member"], now_iso())
    log("submitting {} ({} slot(s) left) :: {}".format(path.name, allowed, msg))

    resp = api.competition_submit(str(path), msg, comp)
    ref = getattr(resp, "ref", None)
    log("accepted (ref={}); waiting for Kaggle to score it".format(ref or "n/a"))

    result = wait_for_score(
        api, comp, target_ref=ref, target_msg=msg,
        timeout=getattr(args, "score_timeout", 1800) or 1800,
        interval=getattr(args, "poll_interval", 20) or 20,
    )
    result["submitted_by"] = ident["member"]
    result["kaggle_user"] = ident["kaggle_user"]
    result["slots_left_before"] = allowed
    _save_result(args, result)
    print(json.dumps(result, indent=2))
    return 0 if result["status"] == "complete" else 1


def cmd_score(args) -> int:
    """Wait on the most recent submission without creating a new one."""
    api = kaggle_api()
    comp = CONFIG["competition"]["slug"]
    result = wait_for_score(
        api, comp,
        timeout=getattr(args, "score_timeout", 1800) or 1800,
        interval=getattr(args, "poll_interval", 20) or 20,
    )
    _save_result(args, result)
    print(json.dumps(result, indent=2))
    return 0 if result["status"] == "complete" else 1


def cmd_run(args) -> int:
    """push -> wait -> pull -> (optionally) submit, the whole experiment in one call."""
    ident = runner_identity()
    if cmd_push(args) != 0:
        return 1
    status, detail = poll_kernel(args.exp, ident)
    if status != "complete":
        log("kernel did not complete: {} {}".format(status, detail))
        log("fetch logs with: python scripts/kaggle_run.py logs --exp {}".format(args.exp))
        return 1
    if cmd_pull(args) != 0:
        return 1
    if not args.submit:
        log("--submit not set; stopping after pulling artifacts (CV-only cycle)")
        return 0
    sub = WORK / "output" / slugify(args.exp) / "submission.csv"
    if not sub.exists():
        log("FATAL: kernel produced no submission.csv")
        return 1
    args.file = str(sub)
    return cmd_submit(args)


def cmd_logs(args) -> int:
    api = kaggle_api()
    ident = runner_identity(api)
    kid = kernel_id(args.exp, ident)
    try:
        print(api.kernels_logs(kid))
    except Exception as e:  # noqa: BLE001
        log("could not fetch logs: {}".format(e))
        return 1
    return 0


def cmd_whoami(args) -> int:
    api = kaggle_api()
    print(json.dumps(runner_identity(api), indent=2))
    return 0


def main() -> int:
    load_env()
    ap = argparse.ArgumentParser(description="Kaggle experiment driver")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def add(name, fn, needs_exp=True):
        p = sub.add_parser(name)
        p.set_defaults(fn=fn)
        if needs_exp:
            p.add_argument("--exp", required=True, help="experiment id, e.g. exp_0007")
        return p

    add("push", cmd_push).add_argument("--commit", help="git sha the notebook should check out")
    add("wait", cmd_wait)
    add("pull", cmd_pull)
    add("logs", cmd_logs)

    def add_wait_args(p):
        p.add_argument("--score-timeout", type=int, default=1800,
                       help="seconds to keep polling for a score (default 1800)")
        p.add_argument("--poll-interval", type=int, default=20,
                       help="seconds between score polls (default 20)")

    p_sub = add("submit", cmd_submit, needs_exp=False)
    p_sub.add_argument("--exp")
    p_sub.add_argument("--file", required=True)
    p_sub.add_argument("--message")
    add_wait_args(p_sub)

    p_score = add("score", cmd_score, needs_exp=False)
    p_score.add_argument("--exp")
    add_wait_args(p_score)

    p_run = add("run", cmd_run)
    p_run.add_argument("--commit")
    p_run.add_argument("--submit", action="store_true", help="also submit submission.csv")
    p_run.add_argument("--message")
    add_wait_args(p_run)

    sub.add_parser("limits").set_defaults(fn=cmd_limits)
    sub.add_parser("whoami").set_defaults(fn=cmd_whoami)

    args = ap.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
