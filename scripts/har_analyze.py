#!/usr/bin/env python3
"""Analyse a captured request log for amplification patterns.

Reads a HAR 1.2 file (Chrome DevTools, Firefox, Playwright) or a plain JSON /
JSONL list of requests, and reports what the static scan cannot know: how many
requests a screen really made, which ones were the same read twice, which
endpoint was hit once per row, which URLs are being polled and by how many
timers, and where requests ran one after another when they could have run
together.

Plain JSON rows need at least `url` and a start time; everything else is
optional:

    {"method": "GET", "url": "https://api.example.com/v1/items/123",
     "started": "2026-11-04T10:00:00.120Z" | 1762250400120 (ms) | 12.5 (s),
     "duration_ms": 84, "status": 200, "size": 1024, "body": "...", "page": "home"}

Usage:
    har_analyze.py CAPTURE [--format json|md] [--out FILE]
                   [--api-filter REGEX] [--ignore REGEX]
                   [--dup-window-ms 2000] [--burst-min 5] [--burst-ms 1500]
                   [--chain-gap-ms 120] [--large-kb 200]

Standard library only. Exit code 0 when the analysis ran, 2 on a usage error.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import statistics
import sys
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple
from urllib.parse import parse_qsl, urlsplit

STATIC_EXT_RE = re.compile(
    r"\.(?:js|mjs|cjs|ts|tsx|jsx|vue|svelte|css|scss|less|map|png|jpe?g|gif|svg|webp|avif|ico|woff2?|ttf|otf|eot"
    r"|mp4|webm|mp3|wasm|html?)(?:$|\?)",
    re.I,
)
# Dev-server internals (Vite, webpack-dev-server) and the capture script's own markers.
DEV_PATH_RE = re.compile(r"/(?:@vite|@id|@fs|@react-refresh|node_modules|__vite_ping|__webpack_hmr|__overfetch__)(?:/|$)")
STATIC_HOST_RE = re.compile(r"(?:^|\.)(?:fonts\.googleapis\.com|fonts\.gstatic\.com|cdn\.|cdnjs\.cloudflare\.com|unpkg\.com|jsdelivr\.net)", re.I)
ID_SEGMENT_RES = [
    re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I),  # uuid
    re.compile(r"^[0-7][0-9A-HJKMNP-TV-Z]{25}$"),  # ulid
    re.compile(r"^[a-z]+_[0-9A-Za-z]{10,}$"),  # kind_ULID style handles
    re.compile(r"^[0-9a-f]{16,}$", re.I),  # hex ids
    re.compile(r"^\d{2,}$"),  # numeric ids
    re.compile(r"^[A-Za-z0-9_-]{22,}$"),  # base64url / nanoid
]


@dataclass
class Request:
    index: int
    method: str
    url: str
    host: str
    path: str
    template: str
    query_keys: str
    start_ms: float
    end_ms: float
    duration_ms: float
    status: int
    size: int
    body_hash: Optional[str]
    page: Optional[str]
    is_static: bool
    batch_size: Optional[int]

    @property
    def signature(self) -> str:
        base = f"{self.method} {self.host}{self.path}"
        if self.query_keys:
            base += "?" + self.query_keys
        if self.body_hash:
            base += f" body:{self.body_hash}"
        return base

    @property
    def exact_key(self) -> str:
        base = f"{self.method} {self.url}"
        if self.body_hash:
            base += f" body:{self.body_hash}"
        return base


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

def parse_time(value: object) -> Optional[float]:
    """Milliseconds since epoch, or a relative ms value for numeric inputs."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        v = float(value)
        if v < 1e9:  # seconds offset or small ms offset; treat < 1e6 as seconds if fractional
            return v * 1000 if v < 1e6 and not v.is_integer() else v
        if v < 1e12:  # epoch seconds
            return v * 1000
        return v
    if isinstance(value, str):
        text = value.strip()
        if re.fullmatch(r"-?\d+(?:\.\d+)?", text):
            return parse_time(float(text))
        try:
            if text.endswith("Z"):
                text = text[:-1] + "+00:00"
            dt = datetime.fromisoformat(text)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.timestamp() * 1000
        except ValueError:
            return None
    return None


def template_path(path: str) -> Tuple[str, Optional[int]]:
    segments = path.split("/")
    out = []
    for seg in segments:
        if seg and any(r.match(seg) for r in ID_SEGMENT_RES):
            out.append("{id}")
        else:
            out.append(seg)
    return "/".join(out), None


def batch_size_from_query(query: str) -> Optional[int]:
    """Count ids in common batch-filter spellings (`filter[id][in]=a,b`, `ids=a,b`, `id in [...]`)."""
    best: Optional[int] = None
    for key, value in parse_qsl(query, keep_blank_values=True):
        if re.search(r"\bin\b|\bids?\b|\[in\]", key, re.I) or re.search(r"\bin\s*[\[(]", value):
            bracketed = re.search(r"[\[(](.*)[\])]", value)
            haystack = bracketed.group(1) if bracketed else value
            ids = re.findall(r"[A-Za-z0-9_-]{6,}", haystack)
            if len(ids) > 1:
                best = max(best or 0, len(ids))
    return best


def body_hash(text: Optional[str]) -> Optional[str]:
    if not text:
        return None
    return hashlib.sha1(text.encode("utf-8", "replace")).hexdigest()[:10]


def make_request(index: int, method: str, url: str, start: Optional[float], duration: Optional[float],
                 status: int, size: int, body: Optional[str], page: Optional[str]) -> Optional[Request]:
    if start is None:
        return None
    parts = urlsplit(url)
    path = parts.path or "/"
    template, _ = template_path(path)
    query_keys = "&".join(sorted({k for k, _ in parse_qsl(parts.query, keep_blank_values=True)}))
    batch = batch_size_from_query(parts.query)
    if batch is None and body:
        ids = re.findall(r"\"(?:ids?|artifact_ids?|keys?)\"\s*:\s*\[([^\]]*)\]", body)
        if ids:
            batch = max(len(re.findall(r"[A-Za-z0-9_-]{6,}", group)) for group in ids) or None
    is_static = (bool(STATIC_EXT_RE.search(path)) or bool(STATIC_HOST_RE.search(parts.netloc))
                 or bool(DEV_PATH_RE.search(path)))
    duration = duration or 0.0
    return Request(
        index=index, method=method.upper(), url=url, host=parts.netloc, path=path, template=template,
        query_keys=query_keys, start_ms=start, end_ms=start + duration, duration_ms=duration,
        status=int(status or 0), size=int(size or 0), body_hash=body_hash(body), page=page,
        is_static=is_static, batch_size=batch,
    )


def load_har(data: dict) -> List[Request]:
    entries = data.get("log", {}).get("entries", [])
    out: List[Request] = []
    for i, entry in enumerate(entries):
        req = entry.get("request", {})
        res = entry.get("response", {})
        post = req.get("postData") or {}
        size = res.get("bodySize") or (res.get("content") or {}).get("size") or 0
        r = make_request(
            i, req.get("method", "GET"), req.get("url", ""), parse_time(entry.get("startedDateTime")),
            float(entry.get("time") or 0), res.get("status", 0), size if size and size > 0 else 0,
            post.get("text"), entry.get("pageref"),
        )
        if r:
            out.append(r)
    return out


def load_rows(rows: List[dict]) -> List[Request]:
    out: List[Request] = []
    for i, row in enumerate(rows):
        start = None
        for key in ("started", "startedDateTime", "timestamp", "ts", "start", "time_ms", "startTime"):
            if key in row:
                start = parse_time(row[key])
                if start is not None:
                    break
        duration = None
        for key in ("duration_ms", "duration", "time", "elapsed_ms"):
            if key in row and isinstance(row[key], (int, float)):
                duration = float(row[key])
                break
        r = make_request(
            i, row.get("method", "GET"), row.get("url", ""), start, duration, row.get("status", 0),
            row.get("size", 0) or 0, row.get("body") or row.get("postData"), row.get("page") or row.get("route"),
        )
        if r:
            out.append(r)
    return out


def load_capture(path: str) -> List[Request]:
    with open(path, encoding="utf-8") as handle:
        text = handle.read()
    stripped = text.strip()
    if stripped.startswith("{"):
        data = json.loads(stripped)
        if "log" in data:
            return load_har(data)
        if "requests" in data:
            return load_rows(data["requests"])
        raise ValueError("JSON object is neither a HAR (log.entries) nor {requests: [...]}")
    if stripped.startswith("["):
        return load_rows(json.loads(stripped))
    rows = [json.loads(line) for line in stripped.splitlines() if line.strip()]
    return load_rows(rows)


# ---------------------------------------------------------------------------
# Analyses
# ---------------------------------------------------------------------------

def summarize(reqs: List[Request], api: List[Request]) -> dict:
    if not reqs:
        return {"total": 0}
    t0 = min(r.start_ms for r in reqs)
    t1 = max(r.end_ms for r in reqs)
    by_method = defaultdict(int)
    by_status = defaultdict(int)
    for r in api:
        by_method[r.method] += 1
        by_status[f"{r.status // 100}xx" if r.status else "unknown"] += 1
    pages = defaultdict(int)
    for r in api:
        pages[r.page or "(unlabelled)"] += 1
    return {
        "total": len(reqs),
        "api": len(api),
        "static_excluded": len(reqs) - len(api),
        "span_ms": round(t1 - t0),
        "by_method": dict(by_method),
        "by_status": dict(by_status),
        "bytes_api": sum(r.size for r in api),
        "per_page": dict(pages) if len(pages) > 1 or "(unlabelled)" not in pages else {},
        "batched_requests": sum(1 for r in api if r.batch_size),
    }


def top_templates(api: List[Request], limit: int = 25) -> List[dict]:
    groups: Dict[str, List[Request]] = defaultdict(list)
    for r in api:
        groups[f"{r.method} {r.host}{r.template}" + (f"?{r.query_keys}" if r.query_keys else "")].append(r)
    rows = []
    for key, items in groups.items():
        distinct = len({r.url for r in items})
        rows.append({
            "template": key,
            "count": len(items),
            "distinct_urls": distinct,
            "bytes": sum(r.size for r in items),
            "p50_ms": round(statistics.median(r.duration_ms for r in items)) if items else 0,
            "errors": sum(1 for r in items if r.status >= 400),
            "max_batch": max((r.batch_size or 0) for r in items) or None,
        })
    rows.sort(key=lambda row: (-row["count"], row["template"]))
    return rows[:limit]


def duplicates(api: List[Request], window_ms: float) -> List[dict]:
    groups: Dict[str, List[Request]] = defaultdict(list)
    for r in api:
        groups[r.exact_key].append(r)
    out = []
    for key, items in groups.items():
        if len(items) < 2:
            continue
        items.sort(key=lambda r: r.start_ms)
        cluster: List[Request] = [items[0]]
        clusters: List[List[Request]] = []
        for r in items[1:]:
            if r.start_ms - cluster[0].start_ms <= window_ms:
                cluster.append(r)
            else:
                if len(cluster) > 1:
                    clusters.append(cluster)
                cluster = [r]
        if len(cluster) > 1:
            clusters.append(cluster)
        for c in clusters:
            overlapping = sum(1 for a, b in zip(c, c[1:]) if b.start_ms < a.end_ms)
            unverified = any(r.method != "GET" and r.body_hash is None for r in c)
            out.append({
                "key": key,
                "count": len(c),
                "first_at_ms": round(c[0].start_ms),
                "spread_ms": round(c[-1].start_ms - c[0].start_ms),
                "in_flight_overlap": overlapping,
                "page": c[0].page,
                "verified_same_body": not unverified,
                "note": (
                    "Overlapping in-flight copies: no shared promise or key for this read."
                    if overlapping else "Sequential repeats: a mount/refetch policy or a missing freshness window."
                ) + ("" if not unverified else " Body not captured, so the POSTs may differ; verify before counting."),
            })
    out.sort(key=lambda row: (-row["count"], row["first_at_ms"]))
    return out


def bursts(api: List[Request], min_count: int, window_ms: float) -> List[dict]:
    groups: Dict[str, List[Request]] = defaultdict(list)
    for r in api:
        if "{id}" not in r.template:
            continue
        groups[f"{r.method} {r.host}{r.template}"].append(r)
    out = []
    for key, items in groups.items():
        items.sort(key=lambda r: r.start_ms)
        i = 0
        while i < len(items):
            j = i
            while j + 1 < len(items) and items[j + 1].start_ms - items[i].start_ms <= window_ms:
                j += 1
            cluster = items[i:j + 1]
            distinct = {r.url for r in cluster}
            if len(distinct) >= min_count:
                max_inflight = peak_concurrency(cluster)
                out.append({
                    "template": key,
                    "requests": len(cluster),
                    "distinct_ids": len(distinct),
                    "span_ms": round(cluster[-1].end_ms - cluster[0].start_ms),
                    "peak_in_flight": max_inflight,
                    "page": cluster[0].page,
                    "examples": [r.path for r in cluster[:3]],
                    "errors": sum(1 for r in cluster if r.status >= 400),
                    "note": "One read per row; a batch read keyed on the id set replaces these with one request per chunk.",
                })
                i = j + 1
            else:
                i += 1
    out.sort(key=lambda row: -row["distinct_ids"])
    return out


def peak_concurrency(items: List[Request]) -> int:
    events = []
    for r in items:
        events.append((r.start_ms, 1))
        events.append((r.end_ms, -1))
    events.sort(key=lambda e: (e[0], e[1]))
    cur = peak = 0
    for _, delta in events:
        cur += delta
        peak = max(peak, cur)
    return peak


def periodic(ts: List[float], tolerance: float = 0.25) -> Optional[float]:
    if len(ts) < 3:
        return None
    gaps = [b - a for a, b in zip(ts, ts[1:])]
    med = statistics.median(gaps)
    if med < 500:
        return None
    if all(abs(g - med) <= tolerance * med for g in gaps):
        return med
    return None


def polling(api: List[Request]) -> List[dict]:
    groups: Dict[str, List[Request]] = defaultdict(list)
    for r in api:
        groups[r.exact_key].append(r)
    out = []
    for key, items in groups.items():
        if len(items) < 3:
            continue
        ts = sorted(r.start_ms for r in items)
        period = periodic(ts)
        timers = 1
        if period is None:
            for k in (2, 3, 4):
                if len(ts) < 3 * k:
                    break
                lanes = [periodic(ts[offset::k]) for offset in range(k)]
                if all(lanes) and max(lanes) - min(lanes) <= 0.25 * max(lanes):
                    period = statistics.median(lanes)
                    timers = k
                    break
        if period is None:
            continue
        out.append({
            "key": key,
            "occurrences": len(items),
            "period_ms": round(period),
            "timers": timers,
            "effective_period_ms": round(period / timers),
            "span_ms": round(ts[-1] - ts[0]),
            "page": items[0].page,
            "note": (
                f"{timers} interleaved clocks on one URL. With TanStack Query that means {timers} different query keys "
                "or clients (observers on one key re-sync and share a fetch); with SWR, Apollo or hand-rolled timers "
                f"it can be {timers} observers of one read."
                if timers > 1 else "Single periodic poll; check it stops when the data settles and when the user is idle."
            ),
        })
    out.sort(key=lambda row: (-row["timers"], row["effective_period_ms"]))
    return out


def solitary(r: Request, items: List[Request]) -> bool:
    """True when no other request is in flight at any point while `r` is."""
    return not any(o.index != r.index and o.start_ms < r.end_ms and o.end_ms > r.start_ms for o in items)


def serial_chains(api: List[Request], gap_ms: float, min_len: int = 3) -> List[dict]:
    """Runs of requests where each started alone, just after the previous one ended.

    A request that overlaps anything else is parallel work (a burst, a fan-out),
    so it can neither start nor extend a chain; otherwise a 20-read burst with
    staggered starts reads as a 20-step waterfall.
    """
    items = sorted(api, key=lambda r: r.start_ms)
    alone = {r.index for r in items if solitary(r, items)}
    chains: List[List[Request]] = []
    used = set()
    for i, head in enumerate(items):
        if head.index in used or head.index not in alone:
            continue
        chain = [head]
        last = head
        for nxt in items[i + 1:]:
            if nxt.index in used or nxt.index not in alone:
                continue
            if nxt.start_ms < last.end_ms:
                continue
            if nxt.start_ms - last.end_ms > gap_ms:
                if nxt.start_ms - last.end_ms > gap_ms * 10:
                    break
                continue
            chain.append(nxt)
            last = nxt
        if len(chain) >= min_len:
            for r in chain:
                used.add(r.index)
            chains.append(chain)
    out = []
    for chain in chains:
        total = chain[-1].end_ms - chain[0].start_ms
        out.append({
            "length": len(chain),
            "total_ms": round(total),
            "sum_of_durations_ms": round(sum(r.duration_ms for r in chain)),
            "page": chain[0].page,
            "steps": [f"{r.method} {r.path}" for r in chain[:8]],
            "note": "Each request started only after the previous one ended. If a later step needs nothing from an earlier one, they can run together.",
        })
    out.sort(key=lambda row: -row["total_ms"])
    return out[:10]


def errors(api: List[Request]) -> List[dict]:
    groups: Dict[str, List[Request]] = defaultdict(list)
    for r in api:
        if r.status >= 400:
            groups[f"{r.status} {r.method} {r.host}{r.template}"].append(r)
    out = []
    for key, items in groups.items():
        repeats = defaultdict(int)
        for r in items:
            repeats[r.exact_key] += 1
        retried = sum(1 for v in repeats.values() if v > 1)
        out.append({
            "key": key,
            "count": len(items),
            "same_url_repeated": retried,
            "note": "Repeated identical failures look like automatic retries of a request that cannot succeed." if retried else "",
        })
    out.sort(key=lambda row: -row["count"])
    return out


def large_responses(api: List[Request], threshold_bytes: int) -> List[dict]:
    rows = [
        {"request": f"{r.method} {r.path}", "kb": round(r.size / 1024), "page": r.page}
        for r in api if r.size >= threshold_bytes
    ]
    rows.sort(key=lambda row: -row["kb"])
    return rows[:15]


def analyze(reqs: List[Request], args: argparse.Namespace) -> dict:
    api = [r for r in reqs if not r.is_static]
    if args.api_filter:
        pat = re.compile(args.api_filter)
        api = [r for r in api if pat.search(r.url)]
    if args.ignore:
        pat = re.compile(args.ignore)
        api = [r for r in api if not pat.search(r.url)]
    api.sort(key=lambda r: r.start_ms)
    return {
        "summary": summarize(reqs, api),
        "peak_in_flight": peak_concurrency(api) if api else 0,
        "templates": top_templates(api),
        "duplicates": duplicates(api, args.dup_window_ms),
        "bursts": bursts(api, args.burst_min, args.burst_ms),
        "polling": polling(api),
        "serial_chains": serial_chains(api, args.chain_gap_ms),
        "errors": errors(api),
        "large_responses": large_responses(api, args.large_kb * 1024),
    }


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def md_table(rows: List[dict], columns: List[Tuple[str, str]]) -> List[str]:
    if not rows:
        return ["None found."]
    out = ["| " + " | ".join(label for _, label in columns) + " |", "| " + " | ".join("---" for _ in columns) + " |"]
    for row in rows:
        cells = []
        for key, _ in columns:
            value = row.get(key, "")
            if isinstance(value, list):
                value = "<br>".join(str(v) for v in value)
            cells.append(str(value).replace("|", "\\|"))
        out.append("| " + " | ".join(cells) + " |")
    return out


def to_markdown(result: dict, source: str) -> str:
    s = result["summary"]
    lines = [f"# Capture analysis: {os.path.basename(source)}", ""]
    if not s.get("total"):
        lines.append("No requests with a start time were found.")
        return "\n".join(lines)
    lines.append(
        f"{s['api']} API requests ({s['static_excluded']} static assets excluded) over {s['span_ms'] / 1000:.1f} s; "
        f"peak {result['peak_in_flight']} in flight; {s['batched_requests']} requests carried an id batch."
    )
    lines.append("By method: " + ", ".join(f"{k} {v}" for k, v in sorted(s["by_method"].items())) + ". By status: "
                 + ", ".join(f"{k} {v}" for k, v in sorted(s["by_status"].items())) + ".")
    if s.get("per_page"):
        lines.append("Per page: " + ", ".join(f"{k} {v}" for k, v in s["per_page"].items()) + ".")
    lines.append("")
    lines.append("## Per-row bursts (N+1)")
    lines.append("")
    lines += md_table(result["bursts"], [("template", "Template"), ("distinct_ids", "Distinct ids"), ("requests", "Requests"),
                                         ("span_ms", "Span ms"), ("peak_in_flight", "Peak in flight"), ("errors", "Errors"), ("page", "Page")])
    lines.append("")
    lines.append("## Polling")
    lines.append("")
    lines += md_table(result["polling"], [("key", "Request"), ("occurrences", "Hits"), ("period_ms", "Period ms"),
                                          ("timers", "Timers"), ("effective_period_ms", "Effective ms"), ("note", "Read")])
    lines.append("")
    lines.append("## Duplicates (same request within the window)")
    lines.append("")
    lines += md_table(result["duplicates"], [("key", "Request"), ("count", "Copies"), ("spread_ms", "Spread ms"),
                                             ("in_flight_overlap", "Overlapping"), ("verified_same_body", "Same body"), ("note", "Read")])
    lines.append("")
    lines.append("## Serial chains")
    lines.append("")
    lines += md_table(result["serial_chains"], [("length", "Steps"), ("total_ms", "Total ms"), ("sum_of_durations_ms", "Sum ms"), ("steps", "Chain")])
    lines.append("")
    lines.append("## Errors")
    lines.append("")
    lines += md_table(result["errors"], [("key", "Request"), ("count", "Count"), ("same_url_repeated", "Repeated URLs"), ("note", "Read")])
    lines.append("")
    lines.append("## Largest responses")
    lines.append("")
    lines += md_table(result["large_responses"], [("request", "Request"), ("kb", "KB"), ("page", "Page")])
    lines.append("")
    lines.append("## Top endpoints")
    lines.append("")
    lines += md_table(result["templates"], [("template", "Template"), ("count", "Count"), ("distinct_urls", "Distinct URLs"),
                                            ("max_batch", "Max batch"), ("p50_ms", "p50 ms"), ("errors", "Errors"), ("bytes", "Bytes")])
    lines.append("")
    lines.append("Templates replace ids with `{id}`. A POST with the same URL is only a duplicate when its body hash matches; captures without bodies say so.")
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("capture", help="HAR file, JSON array/object, or JSONL of requests")
    parser.add_argument("--format", choices=("json", "md"), default="md")
    parser.add_argument("--out")
    parser.add_argument("--api-filter", help="regex; keep only URLs that match (after static assets are dropped)")
    parser.add_argument("--ignore", help="regex; drop URLs that match (telemetry, analytics, sourcemaps)")
    parser.add_argument("--dup-window-ms", type=float, default=2000)
    parser.add_argument("--burst-min", type=int, default=5, help="distinct ids on one template to call it a burst")
    parser.add_argument("--burst-ms", type=float, default=1500)
    parser.add_argument("--chain-gap-ms", type=float, default=120)
    parser.add_argument("--large-kb", type=int, default=200)
    args = parser.parse_args(argv)
    if not os.path.isfile(args.capture):
        print(f"not a file: {args.capture}", file=sys.stderr)
        return 2
    try:
        reqs = load_capture(args.capture)
    except (ValueError, json.JSONDecodeError) as exc:
        print(f"could not parse capture: {exc}", file=sys.stderr)
        return 2
    result = analyze(reqs, args)
    output = json.dumps(result, indent=2) if args.format == "json" else to_markdown(result, args.capture)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as handle:
            handle.write(output + "\n")
    else:
        sys.stdout.write(output + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
