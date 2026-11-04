"""Build a synthetic HAR with known patterns, for tests and for the eval fixture.

Run as a script to write the file:  python3 har_fixture.py OUT.har
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone

BASE = datetime(2026, 11, 4, 10, 0, 0, tzinfo=timezone.utc)
API = "https://api.example.test"


def entry(method: str, url: str, at_ms: float, duration_ms: float, status: int = 200,
          size: int = 512, body: str | None = None, page: str = "home") -> dict:
    started = (BASE + timedelta(milliseconds=at_ms)).isoformat().replace("+00:00", "Z")
    req = {"method": method, "url": url, "headers": [], "queryString": [], "headersSize": -1, "bodySize": -1}
    if body is not None:
        req["postData"] = {"mimeType": "application/json", "text": body}
    return {
        "pageref": page,
        "startedDateTime": started,
        "time": duration_ms,
        "request": req,
        "response": {"status": status, "statusText": "", "headers": [], "content": {"size": size, "mimeType": "application/json"}, "bodySize": size},
        "timings": {"send": 0, "wait": duration_ms, "receive": 0},
    }


def build() -> dict:
    entries = []
    # Static assets, excluded from API counts.
    entries.append(entry("GET", f"{API}/assets/app-8f3a.js", -300, 40, size=250_000))
    entries.append(entry("GET", f"{API}/assets/app-8f3a.css", -290, 20, size=40_000))
    entries.append(entry("GET", "https://fonts.gstatic.com/s/geist/v1/abc.woff2", -280, 30, size=60_000))
    # A serial chain of four independent-looking reads.
    entries.append(entry("GET", f"{API}/v1/me", 0, 120))
    entries.append(entry("GET", f"{API}/v1/orgs", 130, 100))
    entries.append(entry("GET", f"{API}/v1/orgs/01HZXK3M9Q8R2T4V6W8Y0A1B2C/settings", 240, 80))
    entries.append(entry("GET", f"{API}/v1/flags", 330, 60))
    # Three copies of one read within two seconds, two of them overlapping.
    entries.append(entry("GET", f"{API}/api/v1/workspaces?limit=100", 400, 200))
    entries.append(entry("GET", f"{API}/api/v1/workspaces?limit=100", 450, 200))
    entries.append(entry("GET", f"{API}/api/v1/workspaces?limit=100", 900, 200))
    # A per-row burst: twenty agent detail reads.
    for i in range(20):
        ulid = f"01HZXK3M9Q8R2T4V6W8Y0A1B{i:02d}"
        entries.append(entry("GET", f"{API}/api/v1/agents/agent_{ulid}", 500 + i * 20, 150))
    # A batch read, so the analyser can show what a fix looks like.
    ids = ",".join(f"agent_01HZXK3M9Q8R2T4V6W8Y0A1B{i:02d}" for i in range(20))
    entries.append(entry("GET", f"{API}/api/v1/agents?ids={ids}", 950, 180, page="library"))
    # Two timers on one key: both every 60 s, offset by 20 s.
    for k in range(4):
        entries.append(entry("GET", f"{API}/v1/billing/summary", 1000 + k * 60_000, 90, page="shell"))
        entries.append(entry("GET", f"{API}/v1/billing/summary", 21_000 + k * 60_000, 90, page="shell"))
    # One timer every 5 s on a job.
    for k in range(5):
        entries.append(entry("GET", f"{API}/v1/jobs/01HZXK3M9Q8R2T4V6W8Y0A1BZZ", 2000 + k * 5000, 70, page="jobs"))
    # The same 404 four times: a retry of a read that cannot succeed.
    for k in range(4):
        entries.append(entry("GET", f"{API}/api/v1/datasets/01HZXK3M9Q8R2T4V6W8Y0A1BQQ", 1200 + k * 1100, 60, status=404, size=80, page="evals"))
    # POST with the same body twice (a duplicate) and once with a different body (not one).
    body_a = json.dumps({"q": "lineage", "limit": 20})
    body_b = json.dumps({"q": "lineage", "limit": 40})
    entries.append(entry("POST", f"{API}/v1/search", 3000, 300, body=body_a, page="search"))
    entries.append(entry("POST", f"{API}/v1/search", 3200, 300, body=body_a, page="search"))
    entries.append(entry("POST", f"{API}/v1/search", 3300, 300, body=body_b, page="search"))
    # One large response.
    entries.append(entry("GET", f"{API}/v1/export", 4000, 900, size=600 * 1024, page="reports"))
    return {
        "log": {
            "version": "1.2",
            "creator": {"name": "overfetch fixture", "version": "1"},
            "pages": [{"id": p, "title": p, "startedDateTime": BASE.isoformat(), "pageTimings": {}}
                      for p in ("home", "library", "shell", "jobs", "evals", "search", "reports")],
            "entries": entries,
        }
    }


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else "capture.har"
    with open(out, "w", encoding="utf-8") as handle:
        json.dump(build(), handle, indent=1)
    print(out)
