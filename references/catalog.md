# Pattern catalog

One entry per pattern the tools report, keyed by the detector id that appears in `scan.md` and `capture.md`. Each entry has the shape of the problem, how to confirm it, what makes it a non-finding, and the fix with a sketch. Sketches use TanStack Query; the idea carries to SWR, Apollo and RTK Query with the names in `library-facts.md`.

Contents: [per-row-query](#per-row-query) · [shared-poll-timer](#shared-poll-timer) · [always-on-poll](#always-on-poll) · [hand-rolled-poll](#hand-rolled-poll) · [default-stale-zero](#default-stale-zero) · [zero-freshness](#zero-freshness) · [focus-refetch](#focus-refetch) · [broad-invalidation](#broad-invalidation) · [key-drift](#key-drift) · [dependent-key-double-fetch](#dependent-key-double-fetch) · [effect-fetch](#effect-fetch) · [fetch-in-view](#fetch-in-view) · [serial-awaits](#serial-awaits) · [suspense-waterfall](#suspense-waterfall) · [eager-offscreen](#eager-offscreen) · [fetch-all](#fetch-all) · [pagination-overshoot](#pagination-overshoot) · [detail-for-label](#detail-for-label) · [unbounded-parallel](#unbounded-parallel) · [hover-prefetch](#hover-prefetch) · [retry-policy](#retry-policy) · [error-replay](#error-replay) · [remount-refetch](#remount-refetch) · [event-invalidation-storm](#event-invalidation-storm) · [platform-notes](#platform-notes)

---

## per-row-query

**Shape.** A component rendered once per list item calls a query hook keyed on that item's id.

```tsx
{agents.map((a) => <AgentRow key={a.id} agentId={a.id} />)}
// AgentRow: useQuery({ queryKey: ["agent", agentId], queryFn: () => getAgent(agentId) })
```

**Cost.** Requests = rows. A list refetch repeats them all. With the default retry policy, a forbidden row costs four. Growth: unbounded (P0).

**Confirm.** Open the list site the scan names and find where the array comes from. A paginated or search-backed list is unbounded. Then check the hook: is the key per row, and is `enabled` true?

**Non-finding when:** the array is a fixed enum or a tab strip; the hook is `enabled: false` (a cache read that a parent batch fills); a parent already fetched every object and the row's key hits a warm entry seeded with `setQueryData`; the row only mounts on expand and expansion is one at a time.

**Fix: batch names projection.** One query per id set, chunked to the backend's limit, returning only the fields rows need. Rows read a map.

```ts
export function useAgentNames(ids: string[]) {
  const chunks = chunk(unique(ids), 100)
  const results = useQueries({
    queries: chunks.map((c) => ({
      queryKey: keys.names(c),
      queryFn: () => api.listAgents({ filter: `id in [${c.join(",")}]`, fields: "id,name" }),
      staleTime: 5 * 60_000,
    })),
  })
  return useMemo(() => toMap(results), [results])
}
```

If no batch endpoint exists, keep the per-row reads but bound them (see `unbounded-parallel`) and record the backend ask: "list endpoint accepts an id filter".

**Delta.** rows → ceil(rows / chunk). 100 → 1.

---

## shared-poll-timer

**Shape.** `refetchInterval` (TanStack), `refreshInterval` (SWR) or `pollInterval` (Apollo) inside a hook or options factory that more than one component calls.

**Cost.** Depends on the library, and this is where audits overclaim.

- TanStack Query: one interval per observer, but every observer's interval restarts on each query update and the fetches share the in-flight promise, so observers on one key fire together and the endpoint sees one fetch per interval. The shortest interval any caller passes wins. The cost is cadence ownership: the poll runs as long as any caller is mounted, and its period is defined in several places. Growth: none by itself (P2). Timers stack only across different keys or clients.
- SWR and Apollo: one independent timer per hook instance; fetches multiply unless they land inside the dedup window (2 s for SWR, in-flight only for Apollo). Growth: with mounted observers (P0 when always-on, P1 when the interval is a function that stops).
- RTK Query: one timer per cache entry at the shortest interval; nothing stacks.
- Hand-rolled `setInterval`: always one fetch per timer.

**Confirm.** List the importers the scan found and ask which can be mounted at the same time on one screen. A capture shows stacking as one URL with k interleaved clocks; `har_analyze.py` prints `timers: k`. In a TanStack app, k > 1 on one URL means k different query keys or clients, so find the key difference rather than counting observers.

**Non-finding when:** only one caller can ever be mounted; the library is TanStack or RTK Query and every caller uses the same key and interval (record it as cadence ownership, P2 or info); the option is passed by exactly one caller and the hook only forwards it.

**Fix: single poller.** The poll lives in one owner that is always mounted (the shell, a status provider). Everyone else uses the same key with no interval and reads the cache.

```ts
// shell
useQuery({ ...billingSummaryQuery(orgId), refetchInterval: 60_000 })
// everywhere else
useQuery(billingSummaryQuery(orgId))
```

**Delta.** SWR/Apollo: k fetches per period → 1. TanStack: unchanged request count; one definition of the cadence instead of k, and a poll that stops when the owner unmounts.

---

## always-on-poll

**Shape.** A literal or constant interval with no condition.

**Cost.** Polls while the user is idle on a visible screen and after the thing being watched has finished. Hidden tabs pause by default (TanStack, SWR); visible idle ones do not. Growth: with time (P1 under 10 s, P2 otherwise).

**Confirm.** What is the poll waiting for? If the data has a terminal state (job done, import finished), the poll should stop there. If it is a status that never settles (a health check), ask whether a push channel exists.

**Non-finding when:** the interval is already a function that returns `false` when settled, or the hook is gated by `enabled` on a running flag.

**Fix: data-driven interval.**

```ts
refetchInterval: (query) => (isRunning(query.state.data) ? 5_000 : false)
```

For a long-lived shell poll, add an idle gate (`document.visibilityState`, or an idle timer that disables the query after N minutes without input).

---

## hand-rolled-poll

**Shape.** `setInterval` whose callback fetches, refetches or invalidates.

**Cost.** Ignores visibility, idle and the cache. Without `clearInterval` on unmount, each mount adds a timer that outlives the component. Growth: with mounts and time (P0 without cleanup, P2 with).

**Confirm.** Look for the cleanup. Mount and unmount the component twice in a capture and count the reads per period.

**Fix.** Use the library's interval option on one owner (see `single poller`), or at least return `() => clearInterval(id)` from the effect.

---

## default-stale-zero

**Shape.** `new QueryClient()` with no `defaultOptions.queries.staleTime`.

**Cost.** Every new observer refetches on mount; every window focus refetches every mounted query; two components mounting a second apart each hit the network. This is the single most common source of duplicate reads in TanStack apps. Growth: fixed multiplier across the whole app (P1).

**Confirm.** Check for `setQueryDefaults` or a wrapper that builds the client with options passed as a variable (the scan lowers confidence in that case).

**Fix: freshness per family.** A floor in the defaults (30 to 60 s), then a table per data family: identity minutes, lists tens of seconds, details a minute, live status seconds.

---

## zero-freshness

**Shape.** `staleTime: 0`, `gcTime: 0`, `refetchOnMount: "always"`, `refetchOnWindowFocus: "always"`, SWR `dedupingInterval: 0`, Apollo `fetchPolicy: "network-only"` at a call site.

**Cost.** Same as above, per call site. `"always"` variants bypass staleTime entirely.

**Confirm.** Ask what would break if the data were five seconds old. Usually nothing. The legitimate cases are a value the user just wrote elsewhere (then invalidate on the write instead) and a security-sensitive read (then say so in the report).

**Fix.** Give the read the freshness its family has, and invalidate on the write that changes it.

---

## focus-refetch

**Shape.** Focus refetching is on (the default) and either staleTime is 0 or the platform fires focus often.

**Cost.** Every alt-tab back refetches every stale mounted query. On a desktop shell wired to OS window focus, "often" can be every few seconds while the user works across windows.

**Confirm.** Find `focusManager.setEventListener` (TanStack) or the equivalent. Read what event it subscribes to. Then look at staleTime: that is the throttle.

**Fix.** staleTime per family does most of it. Keep `"always"` off. On desktop, subscribe to visibility rather than raw focus if the UX allows.

---

## broad-invalidation

**Shape.** `invalidateQueries()` with no filter, with a root key, or with a one-element key, especially from an input handler.

**Cost.** Every mounted query under the key refetches. One write becomes a page reload. From `onChange` it is one reload per keystroke. Growth: with mounted queries (P0 in an input handler, P2 otherwise).

**Confirm.** Count the mounted queries under that key on the screen where the write happens. One or two is fine.

**Non-finding when:** the write really changes everything under the key (a workspace switch, a bulk import), or the key family is small.

**Fix: narrow invalidation**, or update the cache from the write response:

```ts
onSuccess: (updated) => {
  queryClient.setQueryData(keys.detail(updated.id), updated)
  void queryClient.invalidateQueries({ queryKey: keys.list(orgId) })
}
```

---

## key-drift

**Shape.** The same read under keys that differ by spelling (`["agentNames"]` vs `["agent-names"]`), by shape, or by an extra element one caller includes and another does not.

**Cost.** Two cache entries, two fetches, and invalidation that hits one of them. Growth: fixed (P2), but it hides behind "the cache should have had it".

**Confirm.** For the five most-read endpoints, list every key that reads each. The scan reports literal collisions; the rest is reading the key factory and its callers.

**Non-finding when:** the keys carry different scopes or projections on purpose (a names-only list and a full list are different data).

**Fix: canonical key factory.** One module, one function per scope, imported everywhere. Delete inline arrays.

---

## dependent-key-double-fetch

**Shape.** A query keyed on an id that arrives from another query, without `enabled`.

```ts
const { data: org } = useQuery(orgQuery(slug))
const { data } = useQuery({ queryKey: ["members", org?.id], queryFn: () => listMembers(org!.id) })
```

**Cost.** First render fetches with `undefined` (often a 400 or a wrong empty list), second fetches for real. Two reads, one wrong, every mount. The scan does not catch this; the capture shows it as a request with `undefined` in the path or a 4xx followed by the real one.

**Fix: dependent-key gating.** `enabled: Boolean(org?.id)`, or a single query that resolves both.

---

## effect-fetch

**Shape.** `useEffect` that calls `fetch`/the SDK and stores the result in state.

**Cost.** No cache, no dedup across components, no cancellation, runs again on every dependency change and on StrictMode double-mount. Growth: fixed per mount (P1).

**Fix.** Make it a query. If the data is local-only, say so and leave it.

---

## fetch-in-view

**Shape.** `fetch`/`axios` in a component or route file rather than the data layer.

**Cost.** Invisible to the cache, to invalidation, and to any limiter. Hygiene (P3).

**Non-finding when:** it is an upload or a download, which are not cacheable reads.

---

## serial-awaits

**Shape.** Two awaits in sequence where the second needs nothing from the first.

```ts
const user = await api.getUser()
const orgs = await api.listOrgs()   // independent
```

**Cost.** The user pays both latencies in series. Count is unchanged. Growth: latency (P2).

**Confirm.** Read the second call's arguments. If they do not use the first result and no side effect (an auth refresh, a cookie) orders them, they can start together. The capture shows a chain: `har_analyze.py` lists chains where each request started only after the previous ended.

**Fix: parallel start.** `const [user, orgs] = await Promise.all([api.getUser(), api.listOrgs()])`, or two queries so the library overlaps them.

---

## suspense-waterfall

**Shape.** Nested components each using `useSuspenseQuery`: the child cannot start until the parent resolves.

**Cost.** Latency grows with nesting depth.

**Fix.** Prefetch in the route loader (`ensureQueryData` for each query the first paint needs, started together), or `useSuspenseQueries` at the level that knows all the keys. Prefetch only what the first paint shows; a loader that warms every tab trades the waterfall for `eager-offscreen`.

---

## eager-offscreen

**Shape.** A read for UI the user has not opened: every tab's query mounted with the tab strip, a dialog's query placed on the trigger rather than the body, a loader that `ensureQueryData`s every panel on the route, a collapsed section whose body stays mounted.

```tsx
function AgentPage({ id }: { id: string }) {
  const overview = useQuery(agentQueries.overview(id))
  const runs = useQuery(agentQueries.runs(id))         // only shown on the Runs tab
  const settings = useQuery(agentQueries.settings(id)) // only shown on the Settings tab
  const [tab, setTab] = useState("overview")
  // ...renders one panel
}
```

**Cost.** Every visit pays for every panel; the user sees one. Growth: a fixed multiplier per visit (P1 on a landing or hot screen, P2 elsewhere). Each eager read is also one more mounted observer for focus refetches, invalidation and polling to multiply.

**Confirm.** Open the screen in code and list what the first paint renders. A read whose result is only rendered behind a tab, a dialog, an accordion or a scroll position is eager. Then check the owner's answer from scoping: if they asked for the tab to open with no spinner, the prefetch is the design and this is a non-finding. In a capture the shape is N reads on route entry for a screen that shows one panel.

**Non-finding when:** the owner asked for instant open; the hidden panel reads the same cache entry the visible one does (`select` over one read); the read fires only on intent (hover, focus, router `preload: "intent"`); the panels are a handful, the reads are cheap, and the owner knows the trade.

**Fix: fetch on open.** Move the query into the panel component so it mounts with the panel, or gate it: `useQuery({ ...agentQueries.runs(id), enabled: tab === "runs" })`. For a dialog, put the query in the dialog body, not the trigger. To keep the open instant without paying at mount, prefetch on intent: `onPointerEnter={() => queryClient.prefetchQuery(agentQueries.runs(id))}` on the tab trigger, or `preload: "intent"` on a tab route, with a staleTime so the open does not refetch what the hover just loaded.

The inverse, a click that then fetches, is on demand by design. It is a finding only when the owner wants instant open, and the fix is then prefetch on intent, never fetch at mount. The capture analyser already treats it that way: a chain breaks at the `--chain-gap-ms` gap, and a fetch that waits for a click sits well past it.

---

## fetch-all

**Shape.** A page size of 500 or more, or a loop that walks every page before rendering.

**Cost.** Bytes and latency grow with the tenant's data; the screen shows a fraction. Growth: with data (P2).

**Confirm.** What does the caller do with the rows? A count, a facet or a name lookup belongs in its own endpoint. A client-side filter belongs server-side.

**Non-finding when:** the collection has a hard small ceiling (a settings enum) or the screen really shows everything (an export).

**Fix.** Ask the server for the aggregate; page what the user can scroll.

---

## pagination-overshoot

**Shape.** Unknown-total paging that keeps requesting until an empty page.

**Cost.** One extra request per collection at best; with a large page size and a "fetch all" loop, several. Seen as offsets 0, 500, 1000, 1500, 2000 for 600 rows.

**Fix: short-page stop.** A page shorter than the page size is the last one; do not ask for the next.

---

## detail-for-label

**Shape.** A component fetches a whole object to print its name.

**Cost.** Per-id read plus a payload the screen mostly discards. Often the same root cause as `per-row-query`. Growth: with rows (P2 alone, P0 when per-row).

**Confirm.** Trace the uses of the result. If only `name`/`title`/`label` is read and a parent list or a names batch could supply it, it is over-fetch.

**Fix.** Batch names projection, or `select` so the subscription is to the field while the cache entry stays shared. Do not seed a full-detail cache entry from a names-only read; a partial object masquerading as a full one is a worse bug.

---

## unbounded-parallel

**Shape.** `Promise.all(ids.map((id) => get(id)))` with no ceiling.

**Cost.** A burst of N requests at once; trips rate limits and starves other reads. Growth: with N (P1).

**Confirm.** Where does the array come from and what bounds it? Is a limiter referenced nearby that actually wraps this call?

**Fix: bounded fan-out.** Batch where the API allows. Otherwise a small concurrency limiter (5 or 6 in flight) with cancellation on unmount. Say in the report that a 6-in-flight limit is not a 6-RPS limit: six 50 ms reads are 120 requests per second.

---

## hover-prefetch

**Shape.** `onPointerEnter` → `prefetchQuery` / `ensureQueryData` / router preload.

**Cost.** Usually one deduped read per hovered row. Worth a look only if the prefetch fetches a full profile for a tooltip label, or a sweep across a long list fires dozens of reads with no staleTime so repeated hovers refetch.

**Confirm.** Hover across ten rows in a capture: ten reads, no repeats, is the expected result. Record that as a non-finding.

**Fix, when needed.** Prefetch the projection the target screen needs first; set a staleTime so a second hover is free.

---

## retry-policy

**Shape.** Default retry (3 attempts with backoff) applied to reads that fail with 4xx.

**Cost.** A read that cannot succeed costs four requests. Multiply by any per-row pattern.

**Fix.** `retry: (count, error) => !is4xx(error) && count < 2` in the client defaults; keep retries for 5xx and network errors. Treat 408 and 429 as retryable.

---

## error-replay

**Shape.** The capture shows the same failing request repeated at short intervals.

**Cost.** Pure waste, and a sign that an error state is not being held: a component re-mounts on error, or an effect re-runs because the error object changes identity.

**Confirm.** Match the repeated URL to its query and read what happens on error. If the UI shows a retry button, make sure it is the only retry.

---

## remount-refetch

**Shape.** The capture shows the same GET twice a few hundred milliseconds apart with no overlap.

**Cost.** One extra read per mount. The common causes: a `key` prop that changes on each render, conditional rendering that unmounts and remounts a subtree on a loading flag, StrictMode in development (which is not a production finding), staleTime 0 with two observers mounting in sequence.

**Confirm.** Find the two observers. If they are the same component mounted twice in sequence, look at the parent's render path.

---

## event-invalidation-storm

**Shape.** A socket or server-sent event handler calls a root invalidation on every message.

**Cost.** Request bursts proportional to event rate × mounted queries.

**Fix.** Map event types to the narrowest key; debounce bursts; prefer applying the event payload with `setQueryData` when it carries the row.

---

## platform-notes

Facts to record, not defects:

- **Persisted cache** (`persistQueryClient`, IndexedDB, a native store): what `maxAge` is, what `buster` changes on deploy, and whether each platform (web, desktop shell) has its own store. A capture's "cold load" is not cold if the persisted cache was restored; say which you measured.
- **Focus listener** (`focusManager.setEventListener`): what event drives focus refetches on this platform.
- **Online listener**: a flapping signal refetches every stale query on each flip.
- **Device seeding**: a desktop app may seed lists from a local store before the network; the first request after launch may be a revalidation, not a load.
