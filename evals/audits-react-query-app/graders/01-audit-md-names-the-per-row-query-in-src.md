---
type: llm
focus: trace
weight: 1
---

audit.md names the per-row query in src/components/agent-row.tsx as a top-severity (P0) finding and recommends removing the per-row read, either by passing the name the list already returned down to the row or by one batch read keyed on the set of ids
