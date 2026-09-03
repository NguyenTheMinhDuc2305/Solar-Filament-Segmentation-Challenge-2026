#!/usr/bin/env python
"""Push the current branch to GitHub without the token ever becoming visible.

The implement agent has to push before the Kaggle kernel can clone its commit,
but every obvious way of doing that leaks the PAT:

  * `git remote set-url origin https://<token>@github.com/...` writes it into
    `.git/config`, where the next `git remote -v` prints it;
  * `git push https://<token>@github.com/... main` puts it in argv, so it lands
    in the shell history and in the orchestrator's stage log;
  * printing `github_remote()` to compose either of those leaks it directly.

All three happened on the first real cycle. This script keeps the token inside
the process: it is passed to git through a credential helper on stdin-free
`-c` config, and never printed, logged, or written to disk.

Usage (this is the only push path the implement agent should use):

    uv run python scripts/git_push.py            # push the current branch
    uv run python scripts/git_push.py --branch main
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from env_setup import ROOT, load_env  # noqa: E402


def run(args, **kw):
    return subprocess.run(args, cwd=str(ROOT), capture_output=True, text=True,
                          encoding="utf-8", errors="replace", **kw)


def current_branch() -> str:
    out = run(["git", "rev-parse", "--abbrev-ref", "HEAD"])
    return out.stdout.strip() or "main"


def redact(text: str, secret: str) -> str:
    return text.replace(secret, "***") if secret else text


def main() -> int:
    ap = argparse.ArgumentParser(description="Push to GitHub using the token in .env")
    ap.add_argument("--branch", help="branch to push (default: current)")
    ap.add_argument("--remote", default="origin")
    args = ap.parse_args()

    env = load_env(required=["GITHUB_TOKEN"])
    token = env["GITHUB_TOKEN"]
    branch = args.branch or current_branch()

    cfg = json.loads((ROOT / "orchestrator" / "config.json").read_text(encoding="utf-8"))
    gh = cfg["github"]
    url = "https://github.com/{}/{}.git".format(gh["owner"], gh["repo"])

    # The helper is a shell snippet git invokes; the token is substituted here,
    # inside this process, and git never echoes helper output.
    helper = "!f(){{ echo username=x-access-token; echo password={}; }};f".format(token)

    print("pushing {} -> {}/{}".format(branch, gh["owner"], gh["repo"]), flush=True)
    res = run(["git", "-c", "credential.helper=" + helper,
               "push", args.remote, branch])

    out = redact((res.stdout or "") + (res.stderr or ""), token).strip()
    if out:
        print(out, flush=True)

    if res.returncode != 0:
        print("PUSH FAILED (exit {})".format(res.returncode), flush=True)
        return res.returncode

    sha = run(["git", "rev-parse", "HEAD"]).stdout.strip()
    ahead = run(["git", "rev-list", "--count", "{}/{}..HEAD".format(args.remote, branch)])
    remaining = ahead.stdout.strip() or "?"
    if remaining not in ("0", "?"):
        print("WARNING: still {} commit(s) ahead of {}/{}".format(
            remaining, args.remote, branch), flush=True)
        return 1

    # The submit agent reads this line to pin the Kaggle kernel to the exact code.
    print("COMMIT: {}".format(sha), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
