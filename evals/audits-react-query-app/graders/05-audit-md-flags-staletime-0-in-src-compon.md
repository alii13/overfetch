---
type: llm
focus: trace
weight: 1
---

audit.md flags staleTime: 0 in src/components/agent-list.tsx, the useEffect fetch and the never-cleared setInterval in src/components/legacy-panel.tsx, and at least two of: the sequential awaits, the unbounded Promise.all, the limit 1000 fetch-all loop in src/lib/loaders.ts
