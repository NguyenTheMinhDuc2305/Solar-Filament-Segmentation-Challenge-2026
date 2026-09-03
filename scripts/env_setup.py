#!/usr/bin/env python
"""Per-machine credential + identity resolution.

This project is worked on by several people, each with their own Kaggle account.
Nothing about *who* is running is ever committed: `.env` is per-machine and
gitignored, and every Kaggle-facing script resolves identity through here at
runtime.  Only `shared_memory/` is shared, so results from every teammate land
in the same record while the compute stays on each person's own account.

Usage:
    from env_setup import load_env, kaggle_api, runner_identity

    load_env()                 # .env -> os.environ (never overwrites a real env var)
    api = kaggle_api()         # authenticated KaggleApi
    who = runner_identity(api) # {"kaggle_user": ..., "member": ..., "host": ...}
"""
from __future__ import annotations

import os
import platform
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = ROOT / ".env"

REQUIRED = ["KAGGLE_API_TOKEN"]
OPTIONAL = ["GITHUB_TOKEN", "TEAM_MEMBER"]


def parse_env_file(path: Path) -> dict:
    """Minimal dotenv parser - no dependency, tolerates quotes, `export `, and comments."""
    out = {}
    if not path.exists():
        return out
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].strip()
        if "=" not in line:
            continue
        key, val = line.split("=", 1)
        key, val = key.strip(), val.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
            val = val[1:-1]
        if key:
            out[key] = val
    return out


def load_env(required=None, strict=True) -> dict:
    """Load `.env` into os.environ. A pre-existing real env var always wins.

    Returns the merged view of the keys this project cares about.
    """
    required = REQUIRED if required is None else required
    file_vals = parse_env_file(ENV_PATH)
    for k, v in file_vals.items():
        os.environ.setdefault(k, v)

    missing = [k for k in required if not os.environ.get(k)]
    if missing and strict:
        sys.exit(
            "FATAL: missing {} .\n"
            "  Each teammate needs their own {} holding their personal tokens.\n"
            "  Copy .env.example to .env and fill it in - see docs in that file.".format(
                ", ".join(missing), ENV_PATH
            )
        )
    return {k: os.environ.get(k) for k in list(required) + OPTIONAL}


def kaggle_api():
    """Authenticated KaggleApi for whoever's token is in this machine's .env."""
    load_env()
    # kaggle reads KAGGLE_API_TOKEN from the environment at authenticate() time
    from kaggle.api.kaggle_api_extended import KaggleApi

    api = KaggleApi()
    try:
        api.authenticate()
    except Exception as e:  # noqa: BLE001 - surface the real cause to the agent
        sys.exit(
            "FATAL: Kaggle authentication failed for the token in {}.\n"
            "  {}: {}\n"
            "  Generate a fresh token at https://www.kaggle.com/settings/api".format(
                ENV_PATH, type(e).__name__, e
            )
        )
    return api


def resolve_kaggle_username(api=None) -> str:
    """The Kaggle account this machine actually pushes under - never hardcoded."""
    api = api or kaggle_api()
    for getter in (
        lambda: api.get_config_value("username"),
        lambda: api.config_values.get("username"),
    ):
        try:
            u = getter()
            if u:
                return str(u)
        except Exception:  # noqa: BLE001,S110 - fall through to the next strategy
            pass
    sys.exit(
        "FATAL: could not resolve a Kaggle username from the token in {}.\n"
        "  The token may be revoked or malformed.".format(ENV_PATH)
    )


def runner_identity(api=None) -> dict:
    """Who/where this run happened - stamped into RESULTS.md so the team can tell runs apart."""
    user = resolve_kaggle_username(api)
    return {
        "kaggle_user": user,
        "member": os.environ.get("TEAM_MEMBER") or user,
        "host": platform.node(),
    }


def github_remote() -> str:
    """Tokenised clone URL, built fresh from this machine's .env - never written to disk."""
    load_env(required=["GITHUB_TOKEN"])
    import json

    cfg = json.loads((ROOT / "orchestrator" / "config.json").read_text(encoding="utf-8"))
    gh = cfg["github"]
    return "https://{}@github.com/{}/{}.git".format(
        os.environ["GITHUB_TOKEN"], gh["owner"], gh["repo"]
    )


if __name__ == "__main__":
    load_env()
    api = kaggle_api()
    ident = runner_identity(api)
    print("Resolved identity for this machine:")
    for k, v in ident.items():
        print("  {:<12} {}".format(k + ":", v))
    print("  {:<12} {}".format("tokens:", ", ".join(
        k for k in REQUIRED + OPTIONAL if os.environ.get(k))))
