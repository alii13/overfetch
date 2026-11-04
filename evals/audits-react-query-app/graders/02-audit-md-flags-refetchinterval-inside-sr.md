---
type: llm
focus: trace
weight: 1
---

audit.md flags refetchInterval inside src/lib/use-billing-summary.ts, states correctly how TanStack Query v5 treats several observers of one key (their intervals restart on every query update, so they fire together and share one fetch; the rate is the shortest interval, not one fetch per caller), and recommends a single owner for the poll
