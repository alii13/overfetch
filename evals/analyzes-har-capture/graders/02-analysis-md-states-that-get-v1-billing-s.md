---
type: llm
focus: trace
weight: 1
---

analysis.md states that GET /v1/billing/summary is polled by two interleaved timers (about 60 s each, roughly 30 s effective) and recommends keeping a single poller
