---
name: request-audit
description: Audit how a frontend app talks to its backend and produce a ranked, evidence-backed report of request amplification with concrete fixes - per-row N+1 reads, duplicate fetches, stacked poll timers, zero freshness, blanket invalidation, serial waterfalls, fetch-everything pagination, over-fetching, retry waste. Use this whenever someone asks why an app makes too many requests, wants a network / request / data-fetching / API-call audit, mentions N+1, waterfalls, polling, refetch storms, rate limits, backend load or RPS caused by the frontend, TanStack Query / React Query / SWR / Apollo / RTK Query cache behaviour, or hands over a HAR or network capture - even when they only say "the app feels chatty", "the backend is getting hammered", or "check our caching".
---

# Request audit

You are producing a report someone will act on: a ranked list of the ways this app makes more requests than its screens need, each with evidence, a fix, and the number it should move. The report is only useful if every finding survives a sceptical reader, so the method below spends most of its effort on verification and on saying plainly what was and was not checked.

## What you deliver

One markdown report (template in `references/report-template.md`) with:

1. Scope and evidence: what tree, which commit, which captures.
2. Findings ranked by how the request count grows, each with `file:line` or capture evidence, root cause, the fix pattern, expected delta, effort.
3. Non-findings: patterns you checked and ruled out, with the reason. These stop the next person re-investigating.
4. Open product decisions and backend asks: things that need a human or an API change, not a client patch.
5. Limits: what the audit could not measure.

Offer to implement fixes afterwards, one PR per finding, highest severity first. Do not start fixing during the audit.

## Principles

**A regex hit is a candidate, not a finding.** `scripts/static_scan.py` reads text. It cannot tell a 3-row tab strip from a 3,000-row table, or a deliberate root invalidation after a bulk import from a lazy one. Read the code behind every candidate you intend to report. Say what you read.

**Measured beats claimed.** A capture of the running app is the truth about request counts; the code is the truth about why. Comments, docs and old audit records are claims. When a doc says "fresh for 60 seconds" and the code says `staleTime: Infinity`, report the code, note the contradiction, and record the commit hash you read.

**Know the library's real semantics before you blame it.** The mistakes that cost the most are confident claims that are wrong: "the cache dedupes this" when the keys differ, "polling pauses when idle" when it only pauses when hidden, "three callers, three fetches a minute" when the library re-syncs same-key timers and shares the fetch. `references/library-facts.md` holds the facts for TanStack Query, SWR, Apollo and RTK Query, with the source path each was checked against. Read the section for the library in use before writing any sentence about caching or polling, and when a claim decides a finding, open the installed library's source and confirm it.

**Rank by growth, not by count.** A read that scales with rows, with mounted components, or with time is worse than a fixed duplicate. The severity rubric below is about growth class.

**Say what is fine.** A hover prefetch that is one deduped read per row is a non-finding. Write it down as one. A report that only lists problems makes the reader wonder what else was missed.

**Numbers come from somewhere.** Every count in the report traces to a capture file or a code path. Do not invent "6+" or "roughly 100" from intuition; if you cannot measure, say "unmeasured" and give the formula (rows × reads per row).

## Workflow

Tell the user up front how long this takes. A static-only audit of a mid-sized app is 15 to 30 minutes of reading. Adding live captures adds 15 to 45 minutes depending on how many journeys matter.

### 1. Scope

- Find the app root(s) and the data library from `package.json` (the scan prints both). Monorepos: scan each app separately.
- Find the data layer: query key factories, transport/service modules, the `QueryClient` construction (or SWR config, Apollo client, RTK `createApi`). Read the defaults first; they set the baseline every finding is measured against.
- List the screens that matter: usually the landing route after sign-in, the busiest list, and any screen left open for hours (a shell, a monitor). Ask the user if it is not obvious; otherwise pick those three and say so.
- Record the commit: `git rev-parse HEAD`. Every report line refers to this revision.
- Decide whether a live capture is possible: a running dev server plus a browser you can drive, or a HAR the user already has. If neither, the audit is static-only, and the report must say so in its first paragraph.

### 2. Static scan

```bash
python3 <skill-dir>/scripts/static_scan.py <app-root> --format md --out scan.md
python3 <skill-dir>/scripts/static_scan.py <app-root> --format json --out scan.json
```

The scan is fast (about ten seconds for 2,000 files) and needs only Python 3.9+. It emits candidates with a severity, a confidence, the detector that fired, `file:line`, why it matters, the fix pattern, and a "verify first" instruction. Read `scan.md` whole; the JSON is for scripting and for the eval harness.

Add `--exclude` for generated trees the defaults miss, `--min-confidence medium` when the list is long and you want the first pass tight.

### 3. Verify candidates

Work through the candidates in this order: P0 by confidence, then P1, then the rest. For each one you keep, you must have opened the file and be able to answer:

- What is the per-row or per-observer multiplier, and what bounds it? (A `.map` over a fixed enum is not N+1. A row count from a paginated list is.)
- Does the cache already absorb it? (Same key, warm entry, `enabled: false` reads, a parent that already fetched the object.)
- Is there a batch or projection endpoint the client could use instead? Read the API client or OpenAPI spec. If none exists, the fix is a backend ask, and the finding stays with "backend-blocked" on it.
- What is the fix pattern, by name, from `references/catalog.md`?

Drop candidates that fail these checks and keep the ones you drop that a reader would plausibly raise: they go in the non-findings table with a one-line reason.

The catalog entry for each detector lists the specific non-finding conditions. Read it for every detector id that appears in the scan; it is organised by the same ids.

### 4. Cross-cutting checks the scanner cannot do

These need reading, not grepping. Do all of them; most audits find at least one.

- **Key canonicality.** Pick the five most-read endpoints and find every query key that reads each one. Two keys for one read is two cache entries and two fetches. Look especially for list reads under different projections (full vs names-only) and for scope differences (org vs workspace) that are not really different data.
- **Shared in-flight reads.** Two components mounting in the same frame with the same key share one request only if the key is identical at that moment. Keys built from state that settles a tick later (an id that arrives from another query) produce two fetches: one with `undefined`, one real. Check `enabled` gates on dependent keys.
- **Freshness per surface.** Make a table of staleTime per data family (identity, lists, details, live status) and compare with how fast each really changes. The usual bug is one global value that is right for nothing.
- **Polling ownership.** For every poll option in a shared hook or options factory, list the components that can be mounted at once and the key each one uses. In SWR and Apollo each instance runs its own timer and the fetches multiply unless they land in a dedup window. In TanStack Query the timers restart on every query update and share one fetch, so same-key observers cost one fetch per shortest interval; stacking shows up across keys or clients, and the finding is cadence ownership. In RTK Query one timer per cache entry runs at the shortest interval.
- **Focus and visibility.** What fires focus refetches on this platform? A desktop shell (Tauri, Electron) often wires OS window focus, which fires far more often than tab visibility. Which polls pause when hidden, and which keep running while the user is idle on a visible screen?
- **Concurrency ceiling.** Is there a limiter for fan-out reads, and what is it? Note that a 6-in-flight limiter is not a 6-requests-per-second limit; do not write one as the other.
- **Pagination contract.** For unknown-total collections, does the client stop when a page comes back short, or does it keep asking until an empty page? Check page sizes against what the screen can show.
- **Retries.** What does the client do on 403/404/422? The library default retries three times; a 100-row N+1 with a forbidden chunk becomes 400 requests.
- **Persistence.** If the cache is persisted (IndexedDB, localStorage, a native store), what is `maxAge`, what busts it on deploy, and is the store per platform? A persisted cache changes what "cold load" means in a capture; say which you measured.
- **Mutations.** For the five most common writes, what gets invalidated? Root invalidations after a small write are the usual find.

### 5. Live capture

If you can run the app or the user gave you a capture, get numbers. `references/capture.md` has the journeys to run, how to capture (DevTools HAR, Playwright, the bundled `scripts/capture.mjs`, or the app's own request log), and how to keep captures comparable.

```bash
python3 <skill-dir>/scripts/har_analyze.py capture.har --format md --out capture.md
```

It reports API requests per page, per-row bursts on one path template, polling with the number of stacked timers per key, duplicates within a window (with body hash for POSTs), serial chains, repeated errors, and the largest responses. Static assets are excluded; use `--ignore` for telemetry hosts.

Two things to keep straight when reading it:

- A POST to the same URL twice is a duplicate only when the body hash matches. If bodies were not captured, the tool says "unverified"; report it that way.
- A serial chain is a waterfall only if the later request needed nothing from the earlier one. The tool finds the shape; you confirm the dependency in code.

### 6. Reconcile

Pair every static finding with capture evidence where a capture covers that screen. When they disagree, the capture wins for counts and the code wins for cause. When a capture shows amplification that no candidate explains, read the code until it does; that is usually a key-canonicality or dependent-key issue from step 4.

If the repo carries an earlier audit or performance doc, check each of its claims that touch your findings against the code at this commit, and list the contradictions in the report. Stale claims are how the same bug gets "fixed" twice.

### 7. Rank and write

Use the rubric:

| Severity | Growth class | Typical shapes |
| --- | --- | --- |
| P0 | Grows without a ceiling: with rows, with mounted observers, or with time | per-row query on a paginated list, per-instance poll timers in a shared hook (SWR, Apollo, hand-rolled), invalidation on every keystroke, uncleared interval |
| P1 | Fixed multiplier on a hot path | staleTime 0 on a list every screen reads, refetch on every focus, effect-driven fetch with no cache, unbounded `Promise.all` fan-out |
| P2 | Latency, bytes, or ownership, not count growth | serial awaits that could run together, fetch-everything page sizes, full detail read for a label, a TanStack poll defined in several callers |
| P3 | Hygiene that enables the above | inline keys that drift, HTTP calls outside the data layer |
| info | Worth recording, not a defect | hover prefetch that is one read per row, persistence and focus semantics, retry defaults |

For each finding, estimate the delta as a formula with the inputs named, then plug in what you measured: "100 rows × 1 read → 1 batch; measured 100 → 1 on the 100-row fixture". Give effort in hours for a developer who knows the codebase.

Write the report with `references/report-template.md`. Claim first, evidence second, fix third. Short sentences. No adjectives in place of numbers.

## Fix patterns, by name

The catalog has the detail; these are the names to use in the report so a reader can look each up.

| Pattern | Replaces |
| --- | --- |
| Batch names projection (one query per id set, chunked, labels only) | per-row detail reads for a label |
| Single poller (one owner polls, others read the cache) | poll option in a shared hook |
| Data-driven interval (function returns false when settled) | always-on poll |
| Canonical key factory | inline or drifting keys |
| Freshness per family (staleTime table) | staleTime 0 or one global value |
| Narrow invalidation / setQueryData from the write response | root invalidation |
| Dependent-key gating (`enabled` until the id exists) | double fetch with `undefined` then real id |
| Parallel start (Promise.all / two queries) | serial awaits |
| Short-page stop | paging until an empty page |
| Bounded fan-out (limiter + cancellation) | unbounded Promise.all |
| Retry on 5xx/network only | default retry on 4xx |

## Things that have gone wrong before

- Reporting "the cache dedupes this" without checking the keys were identical at the same instant. Dependent ids settle a render later.
- Writing "three callers, three fetches per minute" for a TanStack hook without reading `onQueryUpdate`. Same-key observers restart their intervals on every query update and share the fetch, so the rate is the shortest interval, not callers × rate. That claim reached a merged PR description before anyone opened the library source. A capture settles it: one URL shows one clock or several.
- Calling a hover prefetch a finding. For a bounded list with one deduped read per row it is working as designed; record it under non-findings.
- Counting POSTs with the same URL as duplicates. Different bodies are different reads.
- Trusting the repo's audit doc for staleTime values. Three of four were wrong at the commit being audited.
- Inventing a count ("6+") because it felt right. If there is no measurement, write the formula and "unmeasured".
- Treating a fixture capture as a production number. A fixture proves the amplification shape; it does not prove the daily request volume.
- Suggesting a batch endpoint that does not exist. Read the API client first; if it is missing, it is a backend ask, and the client fix is a limiter in the meantime.

## References

- `references/catalog.md`: every pattern the scanner and the capture tool report, with how to verify, the non-finding conditions, and the fix with a code sketch. Read the entries for the detector ids in your scan.
- `references/library-facts.md`: the cache, freshness, focus and polling semantics of TanStack Query (v4/v5), SWR, Apollo Client and RTK Query. Read the section for the library in use before writing about it.
- `references/capture.md`: journeys to capture, capture methods per environment (browser, Playwright, desktop shells), and how to make before/after captures comparable.
- `references/report-template.md`: the report, section by section, with what each must contain.
- `scripts/static_scan.py`, `scripts/har_analyze.py`, `scripts/capture.mjs`: the tools. `--help` on each.
