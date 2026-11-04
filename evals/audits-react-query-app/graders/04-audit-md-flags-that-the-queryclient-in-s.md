---
type: llm
focus: trace
weight: 1
---

audit.md flags that the QueryClient in src/main.tsx sets no default staleTime (so it is 0) and recommends a default
