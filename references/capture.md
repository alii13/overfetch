# Capturing a request log

A capture turns "this looks like N+1" into "100 rows made 100 reads". It is worth an extra half hour whenever a running app or a HAR is within reach.

## What to capture

Pick the screens from the scope step (landing route, busiest list, long-lived shell) and run these journeys on each. Keep one capture file per journey, or label pages in the HAR so `har_analyze.py` can split them.

| Journey | What it shows |
| --- | --- |
| Cold load of the route, then wait until no request has started for 2 s | Requests per screen; bursts; serial chains; duplicates on mount |
| Navigate away and back (A → B → A) | Remount refetches; freshness windows; cache splits across projections |
| Open the biggest list with real or fixture data (50 to 100 rows) | Per-row reads; page sizes; fetch-all loops |
| Hover across ten rows | Prefetch fan-out and whether repeated hovers refetch |
| Leave the screen open and idle for 3 to 5 minutes | Polling: period and number of timers per key; idle gating |
| Switch to another window and back three times | Focus refetches; which queries are stale enough to refetch |
| Make one write (rename, toggle) | Invalidation breadth: how many reads one mutation triggers |
| Scroll a paginated list to the end | Pagination overshoot; page size |

Note the data size you used (rows in the list, items in the tenant). The report must say "100 rows" not "a list".

## Capture methods

**Chrome / Edge DevTools.** Network tab → tick "Preserve log" → run the journey → right-click → "Save all as HAR with content" (or without content for a smaller file). Pages are labelled by navigation. Bodies of POSTs are included, which lets the analyser tell duplicates apart.

**Firefox DevTools.** Network → gear → "Save All As HAR".

**Playwright (library).** `scripts/capture.mjs` in this skill records a HAR with Playwright if it is installed in the project (or globally):

```bash
node <skill-dir>/scripts/capture.mjs http://localhost:5175/orgs/acme capture.har --settle 2000 --idle 180000
node <skill-dir>/scripts/capture.mjs http://localhost:5175/ capture.har --journey journey.json
```

`journey.json` is a list of steps: `{"goto": "/path"}`, `{"click": "text=Agents"}`, `{"hover": "[data-row]"}`, `{"wait": 3000}`, `{"blur": true}` (simulates leaving the window), `{"focus": true}`. The script waits for network idle plus the settle window after each step. Run it with `--help` for the options.

**Playwright MCP (agent-driven browser).** Drive the journey with the MCP tools, then read the request list the server exposes (`browser_network_requests` or equivalent). Convert it to the plain-rows JSON the analyser accepts: one object per request with `method`, `url`, `started` (ISO time or epoch ms), `duration_ms`, `status`, `size`, optional `body` and `page`. Write the list to a file and run `har_analyze.py` on it.

**The app's own request log.** Some apps keep an in-memory request log for support (method, path, status, timing, no bodies). If one exists, export it; it already excludes static assets. Without bodies, POST duplicates stay "unverified".

**Desktop shells (Tauri, Electron).** The webview's inspector exports a HAR like a browser. If the inspector is off in the build, use the app's request log or point the shell's dev server at a browser for the capture and say in the report that focus semantics differ on the real shell. Keep the window visible while capturing: hidden windows pause polls and understate the count.

**Proxy capture (mitmproxy, Charles).** Works for any client; export to HAR. Bodies are included; TLS interception must be trusted by the app.

## Keeping captures comparable

- Same commit, same fixture data, same journey, same browser profile. Say all four in the report.
- Start each capture from a fresh page load, after a server restart if the backend caches.
- If the cache is persisted (IndexedDB, native store), decide whether you are measuring cold (clear the store first) or warm (restore it) and label the capture.
- Exclude static assets and telemetry. The analyser drops asset extensions and font CDNs; pass `--ignore 'sentry|segment|mixpanel|launchdarkly'` for telemetry hosts in your app.
- Count API requests per journey, not RPS. Requests per journey is what the client controls; RPS depends on how many users run the journey.
- A fixture capture proves a shape (100 rows → 100 reads). It does not prove production volume. Do not write a daily request number from a fixture.

## Reading the analysis

```bash
python3 <skill-dir>/scripts/har_analyze.py capture.har --format md
```

| Section | What a hit means | What to do |
| --- | --- | --- |
| Per-row bursts | One path template hit with many distinct ids inside the window | Find the row component; `per-row-query` in the catalog |
| Polling | A URL repeating at a steady period; `timers > 1` means interleaved clocks | Find every observer with the option; `shared-poll-timer` |
| Duplicates | Same request within the window; "Overlapping" means two in flight at once | Overlapping: keys differ or no shared promise. Sequential: mount or freshness. `key-drift`, `remount-refetch`, `default-stale-zero` |
| Serial chains | Each request started only after the previous ended | Confirm independence in code; `serial-awaits` |
| Errors | Repeated identical 4xx | `retry-policy`, `error-replay` |
| Largest responses | Big payloads | `fetch-all`, `detail-for-label` |
| Top endpoints | Count and distinct URLs per template; `Max batch` shows id batches already in use | The baseline table for the report |

Tune `--burst-min`, `--burst-ms`, `--dup-window-ms` and `--chain-gap-ms` when a capture is unusually slow or fast; state any non-default values in the report.
