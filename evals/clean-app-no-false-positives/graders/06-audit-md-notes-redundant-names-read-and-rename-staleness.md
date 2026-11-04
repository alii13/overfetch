---
type: llm
focus: trace
weight: 1
---

audit.md notes that the list response already carries each agent name, so the separate names read in AgentList is redundant, and that the rename mutation invalidates only the list key so the names cache keeps the old name
