"""Body of the Kaggle runner notebook.

`scripts/kaggle_run.py` wraps this file into a 2-cell .ipynb: cell 1 is the
injected PARAMS dict, cell 2 is this file verbatim.  Keeping it as plain Python
means it stays diffable, reviewable and lintable in the repo instead of rotting
inside notebook JSON.

Contract with the rest of the pipeline
--------------------------------------
Reads   : /kaggle/input/<competition>/            competition data
          /kaggle/input/<artifact_dataset>/       previous weights + processed cache (optional)
Writes  : /kaggle/working/submission.csv          the file that gets submitted
          /kaggle/working/metrics.json            {"cv_pq": float, ...}  <- the loop's real signal
          /kaggle/working/run_log.txt             full stdout, for post-mortems
Pushes  : <kaggle_user>/<artifact_dataset>        weights + processed cache, versioned

Secrets required on the Kaggle side (Add-ons -> Secrets, attached to the notebook):
          GITHUB_TOKEN       PAT with repo scope, to clone this private repo
          KAGGLE_API_TOKEN   this member's own token, to version the artifact dataset
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
ARTIFACT_STAGE = WORKING / "_artifact_stage"
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
    try:
        from kaggle_secrets import UserSecretsClient
        return UserSecretsClient().get_secret(name)
    except Exception as e:  # noqa: BLE001
        if required:
            raise RuntimeError(
                "Missing Kaggle Secret '{}'. Add it under Add-ons -> Secrets and "
                "attach it to this notebook. ({})".format(name, e)
            )
        log("optional secret '{}' unavailable: {}".format(name, e))
        return None


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

    env_note = REPO_DIR / "configs" / "artifact_input.json"
    art_in = Path("/kaggle/input") / params["artifact_dataset"]
    if art_in.exists():
        log("previous artifacts available at {}".format(art_in))
        env_note.parent.mkdir(parents=True, exist_ok=True)
        env_note.write_text(json.dumps({"artifact_input": str(art_in)}), encoding="utf-8")

    sh(cmd, cwd=str(REPO_DIR))


# ------------------------------------------------------- 3. persist artifacts
def push_artifacts(params):
    """Version weights + processed cache as a private Kaggle dataset.

    Weights never travel through anyone's laptop: they are written here and read
    back by the next kernel as an attached data source.
    """
    src = WORKING / "artifacts"
    if not src.exists() or not any(src.rglob("*")):
        log("no /kaggle/working/artifacts to persist; skipping dataset push")
        return

    token = get_secret("KAGGLE_API_TOKEN", required=False)
    if not token:
        log("KAGGLE_API_TOKEN secret absent; leaving artifacts in kernel output only")
        return
    os.environ["KAGGLE_API_TOKEN"] = token

    if ARTIFACT_STAGE.exists():
        shutil.rmtree(ARTIFACT_STAGE)
    shutil.copytree(src, ARTIFACT_STAGE)

    ref = "{}/{}".format(params["kaggle_user"], params["artifact_dataset"])
    meta = {
        "title": params["artifact_dataset"],
        "id": ref,
        "licenses": [{"name": "unknown"}],
    }
    (ARTIFACT_STAGE / "dataset-metadata.json").write_text(
        json.dumps(meta, indent=2), encoding="utf-8")

    from kaggle.api.kaggle_api_extended import KaggleApi
    api = KaggleApi()
    api.authenticate()
    notes = "{} | commit {} | {}".format(
        params["exp"], (params.get("commit") or "HEAD")[:8], params["launched_at"])
    try:
        api.dataset_create_version(str(ARTIFACT_STAGE), version_notes=notes,
                                   dir_mode="zip", quiet=False)
        log("artifact dataset versioned: {}".format(ref))
    except Exception as e:  # noqa: BLE001 - first run has no dataset to version yet
        log("version failed ({}); trying to create the dataset".format(e))
        api.dataset_create_new(str(ARTIFACT_STAGE), public=False,
                               dir_mode="zip", quiet=False)
        log("artifact dataset created: {}".format(ref))
    finally:
        shutil.rmtree(ARTIFACT_STAGE, ignore_errors=True)


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
        push_artifacts(params)
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
