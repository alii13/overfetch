---
type: llm
focus: trace
weight: 1
---

audit.md ranks the bare invalidateQueries() in src/components/search-box.tsx onChange handler as P0 or the highest severity, because it refetches every mounted query on every keystroke
