---
type: llm
focus: trace
weight: 1
---

audit.md names the per-row query in src/components/agent-row.tsx as a top-severity (P0) finding and recommends one batch read keyed on the set of ids instead of one read per row
