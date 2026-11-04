# request-audit

An agent skill that audits how a frontend app talks to its backend and tells you what to fix, in order.

It finds the ways a web app makes more requests than its screens need: one read per row, the same read twice, poll timers that stack, caches that never count as fresh, invalidations that reload the page, requests that run one after another when they could run together, page sizes that fetch everything, and retries that hammer a 404. It ranks them by how the request count grows, backs each with a `file:line` or a capture, and names the fix.

Works with TanStack Query (React Query), SWR, Apollo Client, RTK Query, and apps that call `fetch` by hand.

## What you get

A markdown report:

- **Scope and evidence**: tree, commit, library, cache defaults, captures.
- **Findings**, ranked P0 to P3, each with evidence, cause, fix pattern, measured or formula-based delta, and effort.
- **Non-findings**: what was checked and ruled out, so nobody re-investigates.
- **Open decisions** and **backend asks**: the parts that are not a client patch.
- **Contradictions** with existing docs, and **limits** of the audit.

Excerpt from a run against a fixture app:

```
| # | Severity | Finding                                                   | Growth | Measured                 | Fix pattern             | Effort |
| 1 | P0       | AgentRow fetches each agent to print its name             | rows   | 100 → 1 (fixture, 100)   | Batch names projection  | 2 h    |
| 2 | P2       | useBillingSummary defines the 60 s poll in a hook 3 callers share | cadence | unmeasured (same key re-syncs) | Single poller      | 1 h    |
| 3 | P0       | SearchBox invalidates every query on each keystroke       | time   | unmeasured: keys × mounted | Narrow invalidation   | 1 h    |
```

## Install

The skill is a folder. Put it where your agent looks for skills:

- **Claude Code, personal**: `~/.claude/skills/request-audit/`
- **Claude Code, per project**: `<repo>/.claude/skills/request-audit/`
- **Codex**: `<repo>/.codex/skills/request-audit/` (or symlink the Claude folder)
- Any other agent that reads `SKILL.md`: point it at the folder.

```bash
git clone https://github.com/alii13/request-audit ~/.claude/skills/request-audit
```

The scripts need Python 3.9+ and nothing else. `scripts/capture.mjs` needs Node 18+ and Playwright installed in the project you are capturing.

## Use

In a Claude Code session inside the app's repo:

> Audit the network requests in this app and tell me what to fix.

> The backend team says our dashboard hammers them. Do a request audit of `apps/web`.

> Here's a HAR from the home page (`capture.har`). What's wrong with our request pattern?

The agent runs the static scan, reads the code behind each candidate, captures the running app when it can (or analyses the HAR you give it), and writes the report. Expect 15 to 30 minutes for a static audit of a mid-sized app, more with captures.

You can also run the tools without an agent:

```bash
# candidates from the source tree
python3 scripts/static_scan.py path/to/app --format md --out scan.md

# facts from a capture (HAR from DevTools, Playwright, a proxy, or plain JSON rows)
python3 scripts/har_analyze.py capture.har --format md --out capture.md

# record a HAR of a journey (needs Playwright in the project)
node scripts/capture.mjs http://localhost:5173/ capture.har --journey journey.json --idle 180000
```

## What it checks

| Pattern | Detected by | Severity |
| --- | --- | --- |
| Query hook inside a row component (N+1) | scan + capture bursts | P0 |
| Poll interval in a hook several components call (timers stack in SWR/Apollo; re-sync in TanStack, so cadence ownership) | scan + capture `timers: k` | P0 to P2 by library |
| Unconditional poll; `setInterval` driving reads; uncleared timers | scan + capture | P0 to P2 |
| No default `staleTime`; `staleTime: 0`; `"always"` refetch modes | scan + capture duplicates | P1 |
| Blanket invalidation, especially from input handlers | scan | P0 / P2 |
| `useEffect` fetching by hand; HTTP calls in the view layer | scan | P1 / P3 |
| Independent awaits in series; suspense waterfalls | scan + capture chains | P2 |
| Page sizes ≥ 500; loops over every page; pagination overshoot | scan + capture | P2 |
| Whole object fetched for a label | scan | P2 |
| `Promise.all` fan-out with no ceiling | scan + capture peak in flight | P1 |
| Keys that differ only by spelling | scan | P2 |
| Repeated identical 4xx (retry waste) | capture | P1 |
| Hover prefetch, persisted caches, custom focus listeners | scan, recorded as info | info |

The full list, with how each is verified and fixed, is in [`references/catalog.md`](references/catalog.md). Library semantics the audit relies on (per-observer timers, dedup windows, focus throttles) are in [`references/library-facts.md`](references/library-facts.md).

## How it works

1. **Scan**: `static_scan.py` walks the tree, builds an import graph, finds components and data hooks, and emits candidates with a confidence per detector.
2. **Verify**: the agent reads the code behind every candidate it keeps. A regex hit is never reported as a finding on its own.
3. **Cross-cutting reads** the scanner cannot do: key canonicality, dependent keys, freshness per data family, poll ownership, focus semantics, pagination contracts, retry policy, persistence, mutation invalidation.
4. **Capture**: a HAR or request log is analysed for bursts, stacked timers, duplicates (POST bodies hashed), serial chains, repeated errors and large responses.
5. **Reconcile and rank**: measured counts win over assumptions; the code explains the cause; findings are ranked by growth class; non-findings and limits are written down.

## Limits

- The scanner reads text, not types. It cannot see through dynamic component maps, string-built keys, or hooks composed more than one level deep. Confidence tells you how often a detector is right, not how bad the issue is.
- The import graph resolves relative paths and common aliases (`@/`, `~/`, `src/`); exotic alias maps fall back to basename matching.
- A capture from fixture data proves a shape, not production volume.
- RTK Query's per-entry polling and Apollo's normalized cache change what counts as a finding; the library facts file covers the differences, but the scanner's severities assume per-observer semantics.

## Tests

```bash
python3 -m unittest discover -s scripts/tests -v
```

Thirty-one tests cover both scripts against a fixture app with planted issues and a synthetic HAR. Add a fixture file and a test for any new detector.

## Evals

`evals/` holds three cases in the layout `claude plugin eval` reads: an app audit, a HAR analysis, and a clean app that must not produce false positives. Run them with:

```bash
claude plugin eval . --allow-tools Read,Glob,Grep,Bash,Write --scaffold
```

## Contributing

- One detector per pull request, with a fixture that triggers it and a clean case that does not.
- Every fact in `references/library-facts.md` names the library version it was checked against.
- Keep the scripts on the standard library.
- Severity changes need a sentence on the growth class that justifies them.

## License

MIT. See [LICENSE](LICENSE).
