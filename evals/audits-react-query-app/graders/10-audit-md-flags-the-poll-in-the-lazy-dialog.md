---
type: llm
focus: trace
weight: 1
---

audit.md flags the unconditional refetchInterval of 10 s in src/components/agent-details-dialog.tsx (in the lazily loaded, flag-gated dialog) and recommends a data-driven interval or stopping the poll when the dialog has what it needs
