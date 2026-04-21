"""
Print per-step durations for the latest GitHub Actions workflow run.

Requires a token with at least `actions:read` (classic) or equivalent for the repo.

Usage (PowerShell):
  $env:GH_PAT_SAUVIGNON = "<pat>"   # preferred; matches repo scripts
  python -m scripts.gha_workflow_step_timings

Optional:
  python -m scripts.gha_workflow_step_timings --run-id 12345678901
  python -m scripts.gha_workflow_step_timings --workflow refresh-snapshot-from-release.yml
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path


def _project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _token_from_dotenv() -> str:
    env_path = _project_root() / ".env"
    if not env_path.exists():
        return ""
    try:
        for line in env_path.read_text(encoding="utf-8").splitlines():
            s = line.strip()
            if not s or s.startswith("#") or "=" not in s:
                continue
            key, value = s.split("=", 1)
            if key.strip() != "GH_PAT_SAUVIGNON":
                continue
            token = value.strip().strip('"').strip("'")
            if token:
                return token
    except OSError:
        return ""
    return ""


def _token() -> str:
    return (
        os.environ.get("GH_PAT_SAUVIGNON", "").strip()
        or _token_from_dotenv()
        or os.environ.get("GITHUB_TOKEN", "").strip()
        or os.environ.get("GH_TOKEN", "").strip()
        or os.environ.get("GH_PAT", "").strip()
    )


def _repo_from_git() -> str | None:
    try:
        out = subprocess.check_output(
            ["git", "remote", "get-url", "origin"],
            cwd=_project_root(),
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (subprocess.CalledProcessError, FileNotFoundError, OSError):
        return None
    if "github.com" not in out:
        return None
    s = out.replace("https://", "").replace("http://", "").rstrip("/")
    if s.endswith(".git"):
        s = s[:-4]
    # git@github.com:owner/repo
    if s.startswith("git@github.com:"):
        s = s.split(":", 1)[1]
    parts = s.split("/")
    if "github.com" in parts:
        i = parts.index("github.com")
        if i + 2 < len(parts):
            return f"{parts[i + 1]}/{parts[i + 2]}"
    if len(parts) >= 2:
        return f"{parts[-2]}/{parts[-1]}"
    return None


def _http_json(method: str, url: str, token: str) -> dict:
    req = urllib.request.Request(
        url,
        method=method,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    with urllib.request.urlopen(req, timeout=60) as resp:  # noqa: S310 — fixed GitHub API URL
        return json.loads(resp.read().decode("utf-8"))


def _parse_ts(s: str | None) -> datetime | None:
    if not s:
        return None
    s = str(s).replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def _fmt_duration(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.1f}s"
    m, s = divmod(seconds, 60)
    if m < 60:
        return f"{int(m)}m {s:.0f}s"
    h, m = divmod(m, 60)
    return f"{int(h)}h {int(m)}m"


def main() -> None:
    parser = argparse.ArgumentParser(description="List GitHub Actions step durations for a workflow run.")
    parser.add_argument("--repo", default=None, help="owner/name (default: from git remote origin)")
    parser.add_argument(
        "--workflow",
        default="refresh-snapshot-from-release.yml",
        help="Workflow file name under .github/workflows/",
    )
    parser.add_argument("--run-id", default=None, help="Specific run id (default: latest for workflow)")
    args = parser.parse_args()

    token = _token()
    if not token:
        print(
            "Set GH_PAT_SAUVIGNON (or GITHUB_TOKEN / GH_TOKEN) with repo access (actions:read).",
            file=sys.stderr,
        )
        sys.exit(2)

    repo = args.repo or _repo_from_git()
    if not repo:
        print("Could not resolve repo; pass --repo owner/name", file=sys.stderr)
        sys.exit(2)

    base = f"https://api.github.com/repos/{repo}"

    if args.run_id:
        run_id = str(args.run_id).strip()
    else:
        wf_url = f"{base}/actions/workflows/{args.workflow}/runs?per_page=1"
        try:
            data = _http_json("GET", wf_url, token)
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")
            print(f"HTTP {e.code} listing runs: {body[:500]}", file=sys.stderr)
            sys.exit(1)
        runs = data.get("workflow_runs") or []
        if not runs:
            print("No workflow runs found.", file=sys.stderr)
            sys.exit(1)
        run_id = str(runs[0]["id"])

    jobs_url = f"{base}/actions/runs/{run_id}/jobs?per_page=100"
    try:
        jobs_payload = _http_json("GET", jobs_url, token)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        print(f"HTTP {e.code} listing jobs: {body[:500]}", file=sys.stderr)
        sys.exit(1)

    meta_url = f"{base}/actions/runs/{run_id}"
    try:
        run_meta = _http_json("GET", meta_url, token)
    except urllib.error.HTTPError:
        run_meta = {}

    name = run_meta.get("name") or run_id
    status = run_meta.get("status") or "?"
    conclusion = run_meta.get("conclusion") or "?"
    html = run_meta.get("html_url") or ""
    created = run_meta.get("created_at") or ""
    updated = run_meta.get("updated_at") or ""

    print(f"Run: {name} ({run_id})")
    print(f"Status: {status}  Conclusion: {conclusion}")
    print(f"Created: {created}  Updated: {updated}")
    if html:
        print(f"URL: {html}")
    print()

    rows: list[tuple[str, str, float | None]] = []
    for job in jobs_payload.get("jobs") or []:
        job_name = str(job.get("name") or "job")
        for step in job.get("steps") or []:
            step_name = str(step.get("name") or "")
            st = _parse_ts(step.get("started_at"))
            en = _parse_ts(step.get("completed_at"))
            sec: float | None = None
            if st and en:
                sec = max(0.0, (en - st).total_seconds())
            concl = str(step.get("conclusion") or step.get("status") or "")
            label = f"{job_name} :: {step_name}"
            rows.append((label, concl, sec))

    # GitHub often includes setup/checkout substeps; keep all rows, sort by duration desc
    known = [r for r in rows if r[2] is not None]
    unknown = [r for r in rows if r[2] is None]
    known.sort(key=lambda x: x[2] or 0.0, reverse=True)

    total = sum(s for _, _, s in known if s is not None)
    print(f"{'Step':<72} {'Sec':>8}  Conclusion")
    print("-" * 92)
    for label, concl, sec in known:
        s = f"{sec:.1f}" if sec is not None else "—"
        print(f"{label[:72]:<72} {s:>8}  {concl}")
    if unknown:
        print()
        print("(Steps without start/end timestamps)")
        for label, concl, _ in unknown:
            print(f"  {label[:80]}  {concl}")

    print("-" * 92)
    print(f"{'TOTAL (sum of step durations)':<72} {total:>8.1f}")
    print()
    print("Note: for a single job, steps run sequentially so the sum is close to wall-clock.")

if __name__ == "__main__":
    main()
