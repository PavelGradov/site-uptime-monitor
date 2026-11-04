#!/usr/bin/env python3
"""Check a website, save CSV history and manage GitHub outage issues."""
from __future__ import annotations
import csv
import json
import os
import re
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

BASE_DIR = Path(__file__).resolve().parent
LOG_DIR = BASE_DIR / "logs"
STATE_FILE = BASE_DIR / "status.json"
FIELDS = ["checked_at_utc", "url", "status", "http_code", "response_ms", "attempts", "error"]

def fetch_once(url: str, timeout: float = 12) -> dict:
    start = time.perf_counter()
    req = Request(url, headers={"User-Agent": "PavelGradov-UptimeMonitor/1.0"}, method="GET")
    try:
        with urlopen(req, timeout=timeout) as response:
            response.read(512)
            code = response.status
            error = ""
    except HTTPError as exc:
        code = exc.code
        error = f"HTTP {exc.code}"
    except (URLError, TimeoutError, OSError) as exc:
        code = None
        error = f"{type(exc).__name__}: {exc}"
    return {
        "ok": code is not None and 200 <= code < 400,
        "http_code": code,
        "response_ms": int(round((time.perf_counter() - start) * 1000)),
        "error": error,
    }

def check_site(url: str, attempts: int = 2) -> dict:
    last = None
    for index in range(attempts):
        last = fetch_once(url)
        if last["ok"]:
            last["attempts"] = index + 1
            return last
        if index + 1 < attempts:
            time.sleep(2)
    assert last is not None
    last["attempts"] = attempts
    return last

def load_state() -> dict:
    if not STATE_FILE.exists():
        return {}
    try:
        state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        return state if isinstance(state, dict) else {}
    except (ValueError, OSError):
        return {}

def gh(*args: str) -> str:
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    token = os.environ.get("GH_TOKEN", "")
    if not repo or not token:
        raise RuntimeError("GitHub credentials unavailable")
    p = subprocess.run(["gh", "issue", *args, "--repo", repo], capture_output=True,
                       text=True, check=True, timeout=40)
    return p.stdout.strip()

def new_incident(url: str, started: str, checked_at: str, result: dict) -> int:
    body = (f"## Website availability incident\n\n"
            f"- URL: {url}\n- First failure (UTC): {started}\n"
            f"- Confirmed (UTC): {checked_at}\n"
            f"- HTTP: {result['http_code'] or 'no response'}\n"
            f"- Error: {result['error'] or 'no successful HTTP response'}\n\n"
            "Automatically created after two consecutive failed scheduled checks. "
            "Will be closed on recovery.\n")
    args = ["create", "--title", f"[Uptime] Website unavailable: {url}", "--body", body]
    assignee = os.environ.get("MONITOR_ASSIGNEE", "").strip()
    if assignee:
        args += ["--assignee", assignee]
    output = gh(*args)
    match = re.search(r"/(\d+)\s*$", output)
    if not match:
        raise RuntimeError(f"Unexpected GitHub issue URL: {output}")
    return int(match.group(1))

def close_incident(number: int, started: str, recovered: str) -> None:
    gh("close", str(number), "--comment",
       f"Site recovered at {recovered} (UTC). First observed failure: {started}.")

def update_state(previous: dict, result: dict, checked_at: str, url: str,
                 alerts: bool = True) -> dict:
    state = dict(previous)
    prior_failures = int(state.get("consecutive_failures", 0) or 0)
    if result["ok"]:
        state["current_status"] = "up"
        state["consecutive_failures"] = 0
        state["last_success_utc"] = checked_at
        issue_number = state.get("open_issue_number")
        if issue_number and alerts:
            try:
                close_incident(int(issue_number),
                               str(state.get("incident_started_utc", "")), checked_at)
                state.pop("open_issue_number", None)
                state.pop("incident_started_utc", None)
                state.pop("alert_error", None)
            except Exception as exc:
                state["alert_error"] = str(exc)[:300]
        elif not issue_number:
            state.pop("incident_started_utc", None)
            state.pop("alert_error", None)
    else:
        state["consecutive_failures"] = prior_failures + 1
        state["last_failure_utc"] = checked_at
        if prior_failures == 0:
            state["incident_started_utc"] = checked_at
        state["current_status"] = "down" if state["consecutive_failures"] >= 2 else "suspect"
        if state["current_status"] == "down" and not state.get("open_issue_number") and alerts:
            try:
                state["open_issue_number"] = new_incident(
                    url, state["incident_started_utc"], checked_at, result)
                state.pop("alert_error", None)
            except Exception as exc:
                state["alert_error"] = str(exc)[:300]
    state.update({"checked_at_utc": checked_at, "url": url,
                  "http_code": result["http_code"], "response_ms": result["response_ms"]})
    return state

def record_check(url: str, result: dict, checked_at: str) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    filename = LOG_DIR / (checked_at[:10] + ".csv")
    fresh = not filename.exists() or filename.stat().st_size == 0
    row = {"checked_at_utc": checked_at, "url": url,
           "status": "up" if result["ok"] else "down",
           "http_code": result["http_code"] if result["http_code"] is not None else "",
           "response_ms": result["response_ms"], "attempts": result["attempts"],
           "error": result["error"]}
    with filename.open("a", newline="", encoding="utf-8") as output:
        writer = csv.DictWriter(output, fieldnames=FIELDS)
        if fresh:
            writer.writeheader()
        writer.writerow(row)

def main() -> None:
    url = os.environ.get("MONITOR_URL", "https://pavelgradov.ru/")
    checked_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    result = check_site(url)
    previous = load_state()
    alerts = bool(os.environ.get("GITHUB_REPOSITORY") and os.environ.get("GH_TOKEN"))
    state = update_state(previous, result, checked_at, url, alerts=alerts)
    record_check(url, result, checked_at)
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n",
                          encoding="utf-8")
    print(f"{checked_at} site={url} state={state['current_status']} "
          f"HTTP={result['http_code']} response_ms={result['response_ms']} "
          f"attempts={result['attempts']}")
    if state.get("alert_error"):
        print(f"WARNING: incident notification failed: {state['alert_error']}")

if __name__ == "__main__":
    main()
