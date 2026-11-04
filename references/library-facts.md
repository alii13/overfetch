# Library facts

The semantics that decide whether a pattern is a finding. Read the section for the library in use before writing any sentence about caching, freshness, focus or polling. Where a fact was verified against source, the version is given; re-check against the installed version when it differs by a major.

## TanStack Query v5 (`@tanstack/react-query`, `vue-query`, `svelte-query`, `solid-query`)

Verified against `@tanstack/query-core` 5.102.

**Freshness.**
- `staleTime` default `0`: data is stale the moment it arrives. `Infinity` means never stale (refetch only on invalidation or manual refetch).
- `gcTime` default 5 minutes (`cacheTime` in v4): how long an entry with no observers stays in memory.
- `refetchOnMount`, `refetchOnWindowFocus`, `refetchOnReconnect` default `true`, which means "refetch if stale". `"always"` means refetch regardless of staleTime. So staleTime is the throttle for focus and mount refetches; with staleTime 0 every focus refetches every mounted query.

**Dedup.** Concurrent fetches on the same key share one promise. Different keys never share, even if the `queryFn` hits the same URL. Keys are compared structurally after deterministic hashing; `["a", {b: 1, c: 2}]` equals `["a", {c: 2, b: 1}]`, but `["a", 1]` is not `["a", "1"]`.

**Polling.** `refetchInterval` is implemented per observer: each `useQuery` call with the option schedules its own timer (`QueryObserver#updateRefetchInterval`). N mounted callers of a hook that sets the option produce N timers, each firing its own fetch; the fetches do not dedupe unless two timers happen to fire inside one in-flight window. `refetchIntervalInBackground` default `false` pauses timers when `focusManager.isFocused()` is false (document hidden, or whatever the custom listener reports). A visible idle tab keeps polling. A function value `(query) => number | false` is evaluated per tick and is the standard way to stop when the data settles.

**Focus.** `focusManager` listens to `visibilitychange` by default. `focusManager.setEventListener` replaces it; desktop shells often subscribe to OS window focus, which fires on every window switch.

**Online.** `onlineManager` listens to `online`/`offline`; a flap refetches every stale query if `refetchOnReconnect` is on.

**useQueries.** N observers. `combine` reshapes results; it does not merge requests. Batching is something the `queryFn` does, not the hook.

**enabled: false.** No fetch, ever, from this observer; it still subscribes to the cache entry. Use it for "read if someone else fetched it".

**select.** A projection applied on read. The cache entry stays whole and shared; the component re-renders only when the selected slice changes.

**placeholderData / keepPreviousData.** Rendering help; no extra requests.

**Invalidation.** `invalidateQueries({ queryKey })` matches by prefix. Active (mounted) matches refetch immediately (`refetchType: "active"` default); inactive ones are marked stale. `invalidateQueries()` with no filter matches everything. `resetQueries` and `removeQueries` drop data.

**Retry.** Default 3 retries with exponential backoff (1 s, 2 s, 4 s), applied to every error including 4xx unless `retry` is a function that says otherwise. Mutations default to 0.

**Persistence.** `persistQueryClient` / `PersistQueryClientProvider`: `maxAge` default 24 h; `buster` string invalidates the whole store when changed (set it to the build id). Restored queries are stale by their own staleTime and refetch on first observe if stale.

**Suspense.** `useSuspenseQuery` in nested components waterfalls: the child does not render until the parent resolves. `useSuspenseQueries` or loader prefetch fixes it.

**TanStack Router integration.** `loader: ({ context }) => context.queryClient.ensureQueryData(opts)` fetches before render and dedupes with the component's `useQuery` on the same key. Router `preload: "intent"` fires loaders on hover; combined with per-row loaders it is a hover fan-out. `defaultPreloadStaleTime` throttles repeat preloads.

**v4 differences.** `cacheTime` instead of `gcTime`; `keepPreviousData: true` instead of `placeholderData: keepPreviousData`; `useQuery(key, fn, opts)` overloads; `isLoading` means what v5 calls `isPending`. Polling and focus semantics are the same.

**v3 (`react-query`).** Same per-observer polling. `refetchOnWindowFocus` default true. No `useQueries` `combine`.

## SWR

**Freshness.** No staleTime concept: every `useSWR` mount revalidates unless `revalidateOnMount: false` or `revalidateIfStale: false`. Data is served from cache immediately and revalidated in the background.

**Dedup.** Requests with the same key within `dedupingInterval` (default 2000 ms) share one fetch. Outside that window, a second mount revalidates again.

**Focus.** `revalidateOnFocus` default `true`, throttled by `focusThrottleInterval` (default 5000 ms). `revalidateOnReconnect` default `true`.

**Polling.** `refreshInterval` is per hook call (each `useSWR` instance runs its own timer loop). Timers that fire within one `dedupingInterval` collapse into one fetch; timers offset by more than 2 s each fetch. `refreshWhenHidden` and `refreshWhenOffline` default `false`.

**Invalidation.** `mutate(key)` revalidates one key; `mutate(matcherFn)` or the global `mutate` with a filter revalidates many. `useSWRConfig().cache` is the store.

**Retry.** `shouldRetryOnError` default `true` with exponential backoff (`errorRetryInterval` 5000 ms, `errorRetryCount` unlimited by default unless set). 4xx are retried unless `onErrorRetry` says otherwise.

**useSWRInfinite.** Each page is its own key; `revalidateFirstPage` default `true` means the first page refetches whenever a new page loads.

## Apollo Client (v3)

**Freshness.** `fetchPolicy` default `cache-first`: serve from the normalized cache if every field is present, otherwise fetch. `cache-and-network` always fetches and also serves cache. `network-only` and `no-cache` bypass it. `nextFetchPolicy` changes the policy after the first fetch.

**Dedup.** `queryDeduplication` default `true`: identical in-flight operations (same document and variables) share one request.

**Normalization.** Objects are stored by `__typename:id`; a list query and a detail query that return the same object share its fields. Missing fields on one query still force a fetch for the other (cache-first requires every requested field).

**Polling.** `pollInterval` is per `useQuery` instance (per `ObservableQuery`). Two components polling the same operation run two timers; dedup only collapses them if they fire inside one in-flight window. `skipPollAttempt` can gate on visibility.

**Focus.** No focus refetching by default.

**Invalidation.** Mutations use `refetchQueries` (by name or document), `update` to write the cache from the response, or `cache.evict` + `gc`. `refetchQueries: "active"` refetches every active query.

**Retry.** None by default; `RetryLink` adds it (and retries by default on network errors, not GraphQL errors).

## RTK Query (`@reduxjs/toolkit/query`)

**Freshness.** No staleTime. A query runs on first subscription; later subscribers with the same args share the cache entry and do not refetch unless `refetchOnMountOrArgChange` is `true` or a number of seconds.

**Dedup.** One cache entry per endpoint + serialized args; concurrent subscribers share the in-flight request.

**Polling.** `pollingInterval` is per cache entry, not per subscriber: when several components subscribe with different intervals, the shortest wins and one timer runs. This is the one library where a shared poll option does not stack. `skipPollingIfUnfocused` gates on focus.

**Focus and reconnect.** `refetchOnFocus` and `refetchOnReconnect` default `false`; they need `setupListeners(store.dispatch)`.

**Retention.** `keepUnusedDataFor` default 60 s after the last subscriber unsubscribes.

**Invalidation.** Tag-based: `providesTags` on queries, `invalidatesTags` on mutations. A list-level tag (`{ type: "Agent", id: "LIST" }`) refetches every subscribed list; per-id tags narrow it.

**Retry.** None by default; wrap the base query in `retry()` to add it.

## urql

`requestPolicy` default `cache-first`; `cache-and-network` fetches on every mount. No built-in polling (use `reexecuteQuery` on a timer, which is per component). Graphcache normalizes; the document cache invalidates every query that shares a `__typename` with a mutation result, which can be a broad refetch.

## Plain `fetch` / axios with React state

No cache, no dedup, no cancellation unless written by hand. Every mount is a request; two components wanting the same data make two requests. The audit's first recommendation for such apps is usually to introduce a query library rather than to patch individual reads, and the report should say that plainly with the request counts that justify it.
