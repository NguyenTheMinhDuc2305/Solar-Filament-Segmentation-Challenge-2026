"""Body of the Kaggle runner notebook.

`scripts/kaggle_run.py` wraps this file into a 2-cell .ipynb: cell 1 is the
injected PARAMS dict, cell 2 is this file verbatim.  Keeping it as plain Python
means it stays diffable, reviewable and lintable in the repo instead of rotting
inside notebook JSON.

Contract with the rest of the pipeline
--------------------------------------
Reads   : /kaggle/input/<competition>/            competition data
          /kaggle/input/<previous kernel slug>/   previous weights + processed cache (optional)
Writes  : /kaggle/working/submission.csv          the file that gets submitted
          /kaggle/working/metrics.json            {"cv_pq": float, ...}  <- the loop's real signal
          /kaggle/working/run_log.txt             full stdout, for post-mortems
          /kaggle/working/artifacts/            weights + processed cache, kept as
                                                kernel output and chained into the
                                                next run via `kernel_sources`

Secrets required on the Kaggle side (Add-ons -> Secrets, attached to the notebook):
          GITHUB_TOKEN       PAT with repo scope, to clone this private repo.
                             This is the ONLY secret this notebook needs - every
                             Kaggle API call (submit, poll, fetch results, forum
                             scouting) happens locally with the token in .env.
"""

import json
import os
import shutil
import subprocess
import sys
import time
import traceback
from pathlib import Path

WORKING = Path("/kaggle/working")
REPO_DIR = WORKING / "repo"
LOG_PATH = WORKING / "run_log.txt"

_t0 = time.time()


def log(msg):
    line = "[{:>7.1f}s] {}".format(time.time() - _t0, msg)
    print(line, flush=True)
    with LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def sh(cmd, cwd=None, check=True, secret=None):
    """Run a shell command, echoing it with any secret redacted."""
    shown = cmd.replace(secret, "***") if secret else cmd
    log("$ " + shown)
    p = subprocess.run(cmd, shell=True, cwd=cwd, text=True,
                       capture_output=True, encoding="utf-8", errors="replace")
    out = (p.stdout or "") + (p.stderr or "")
    if out.strip():
        redacted = out.replace(secret, "***") if secret else out
        log(redacted[-4000:])
    if check and p.returncode != 0:
        raise RuntimeError("command failed ({}): {}".format(p.returncode, shown))
    return p.returncode, out


def get_secret(name, required=True):
    """Read a Kaggle Secret, and say precisely why when it is not readable.

    The failure that actually happens is a ConnectionError from the secrets
    service, which reads like a network problem and is not one. Secrets attach to
    a *notebook* through the Kaggle UI; a notebook with none attached reports an
    empty KAGGLE_KERNEL_INTEGRATIONS and every lookup fails identically,
    whatever the name. Distinguishing the two saves a cycle of misdiagnosis.
    """
    attached = os.environ.get("KAGGLE_KERNEL_INTEGRATIONS", "")
    try:
        from kaggle_secrets import UserSecretsClient
        return UserSecretsClient().get_secret(name)
    except Exception as e:  # noqa: BLE001
        if not required:
            log("optional secret '{}' unavailable: {}".format(name, e))
            return None
        if not attached.strip():
            raise RuntimeError(
                "No Kaggle Secret is attached to this notebook "
                "(KAGGLE_KERNEL_INTEGRATIONS is empty), so '{}' cannot be read. "
                "The secret existing on the account is not enough - open this "
                "notebook on kaggle.com once, Add-ons -> Secrets, and attach it. "
                "Later API pushes are new versions of the same notebook and keep "
                "the attachment. Underlying error: {}".format(name, e)
            )
        raise RuntimeError(
            "Secret '{}' could not be read although this notebook has "
            "integrations attached ({}). Check the name and that this secret is "
            "among them. Underlying error: {}".format(name, attached, e)
        )


# --------------------------------------------------------------------- 1. clone
def clone_repo(params, gh_token):
    if REPO_DIR.exists():
        shutil.rmtree(REPO_DIR)
    url = "https://{}@github.com/{}/{}.git".format(
        gh_token, params["repo_owner"], params["repo_name"])
    sh("git clone --quiet --branch {} {} {}".format(params["branch"], url, REPO_DIR),
       secret=gh_token)
    if params.get("commit"):
        sh("git checkout --quiet {}".format(params["commit"]), cwd=str(REPO_DIR))
    _, head = sh("git log -1 --oneline", cwd=str(REPO_DIR))
    log("repo at: " + head.strip())


def install_requirements():
    req = REPO_DIR / "requirements-kaggle.txt"
    if not req.exists():
        log("no requirements-kaggle.txt; using the Kaggle base image as-is")
        return
    sh("pip install --quiet --no-input -r {}".format(req), check=False)


# ----------------------------------------------------------------- 2. run the exp
def run_experiment(params):
    """Hand control to the repo's own entrypoint.

    The implement agent owns src/run.py; this notebook stays a thin, stable
    bootstrap so a bad experiment never requires re-pushing the notebook.
    """
    entry = REPO_DIR / "src" / "run.py"
    if not entry.exists():
        raise RuntimeError(
            "src/run.py not found in the repo. The implement agent must provide it; "
            "it has to accept --exp/--config and write submission.csv + metrics.json "
            "into /kaggle/working."
        )
    cfg = REPO_DIR / "configs" / "{}.yaml".format(params["exp"])
    cmd = "{} -m src.run --exp {} --out {} --data-root {}".format(
        sys.executable, params["exp"], WORKING, "/kaggle/input")
    if cfg.exists():
        cmd += " --config {}".format(cfg)
    else:
        log("WARNING: {} not found; src/run.py must fall back to its defaults".format(cfg))

    art_in = find_previous_artifacts(params)
    if art_in:
        log("warm start available: {}".format(art_in))
        note = REPO_DIR / "configs" / "artifact_input.json"
        note.parent.mkdir(parents=True, exist_ok=True)
        note.write_text(json.dumps({"artifact_input": str(art_in)}), encoding="utf-8")
    else:
        log("no previous artifacts mounted; this run starts cold")

    sh(cmd, cwd=str(REPO_DIR))


def find_previous_artifacts(params):
    """Locate the previous run's artifacts among the mounted inputs.

    kaggle_run.py attaches the prior kernel via `kernel_sources`, so its output
    mounts at /kaggle/input/<that kernel slug>/. Prefer the slug we were told to
    warm from; otherwise fall back to any mounted runner kernel.
    """
    root = Path("/kaggle/input")
    if not root.exists():
        return None

    named = params.get("warm_from")
    if named:
        cand = root / named.split("/")[-1]
        if cand.exists():
            return cand / "artifacts" if (cand / "artifacts").exists() else cand

    for d in sorted(root.iterdir(), reverse=True):
        if not d.is_dir() or d.name == params["competition"]:
            continue
        if (d / "artifacts").exists():
            return d / "artifacts"
    return None


# ------------------------------------------------------- 3. persist artifacts
def describe_artifacts(params):
    """Artifacts persist as *kernel output* - no Kaggle token needed in here.

    Kaggle already keeps everything left in /kaggle/working when a kernel
    completes, and the next run attaches this kernel via `kernel_sources` in
    kernel-metadata.json (scripts/kaggle_run.py wires that up). So weights and
    the processed cache stay on Kaggle and chain forward on their own, and this
    notebook needs no Kaggle credentials at all.
    """
    src = WORKING / "artifacts"
    if not src.exists() or not any(src.rglob("*")):
        log("no /kaggle/working/artifacts produced; next run starts cold")
        return
    files = sorted(p for p in src.rglob("*") if p.is_file())
    total = sum(p.stat().st_size for p in files)
    log("persisting {} artifact file(s), {:.1f} MB, as kernel output".format(
        len(files), total / 1e6))
    for p in files[:20]:
        log("    {} ({:.1f} MB)".format(p.relative_to(WORKING), p.stat().st_size / 1e6))
    if total > 19e9:
        log("WARNING: kernel output limit is ~20GB; prune artifacts/ or the save will fail")


# ------------------------------------------------------------------- 4. verify
def verify_outputs(params):
    """Fail loudly here rather than letting the loop record a phantom success."""
    problems = []
    sub = WORKING / "submission.csv"
    met = WORKING / "metrics.json"

    if not sub.exists():
        problems.append("submission.csv was not produced")
    elif sub.stat().st_size == 0:
        problems.append("submission.csv is empty")

    if not met.exists():
        problems.append("metrics.json was not produced")
    else:
        try:
            m = json.loads(met.read_text(encoding="utf-8"))
            if "cv_pq" not in m:
                problems.append("metrics.json has no 'cv_pq' key (the loop's decision signal)")
        except json.JSONDecodeError as e:
            problems.append("metrics.json is not valid JSON: {}".format(e))

    if problems:
        raise RuntimeError("output contract violated: " + "; ".join(problems))
    log("outputs verified: submission.csv + metrics.json present")


def main():
    params = PARAMS  # noqa: F821 - injected by the parameter cell
    LOG_PATH.write_text("", encoding="utf-8")
    log("runner start | exp={} member={} user={}".format(
        params["exp"], params["member"], params["kaggle_user"]))

    status = "complete"
    error = None
    try:
        gh = get_secret("GITHUB_TOKEN")
        clone_repo(params, gh)
        install_requirements()
        run_experiment(params)
        verify_outputs(params)
        describe_artifacts(params)
    except Exception as e:  # noqa: BLE001 - always leave a machine-readable verdict
        status, error = "error", "{}: {}".format(type(e).__name__, e)
        log("FAILED: " + error)
        log(traceback.format_exc())
    finally:
        # A status file the submit agent can read even when the run blew up.
        (WORKING / "run_status.json").write_text(json.dumps({
            "status": status,
            "error": error,
            "exp": params["exp"],
            "commit": params.get("commit"),
            "member": params["member"],
            "kaggle_user": params["kaggle_user"],
            "launched_at": params["launched_at"],
            "duration_seconds": round(time.time() - _t0, 1),
        }, indent=2), encoding="utf-8")
        shutil.rmtree(REPO_DIR, ignore_errors=True)  # keep kernel output small
        log("runner done: {}".format(status))

    if status != "complete":
        raise SystemExit(1)


main()
