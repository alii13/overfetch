---
type: llm
focus: trace
weight: 1
---

audit.md flags refetchInterval inside src/lib/use-billing-summary.ts, explains that each calling component runs its own timer so the endpoint is polled once per mounted caller, and recommends a single owner for the poll
