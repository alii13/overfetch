---
type: llm
focus: trace
weight: 1
---

audit.md names the runs and settings queries in src/components/agent-page.tsx as reads mounted with the page for tabs the user has not opened, reports it as a finding (P1 or P2, not P0), and recommends moving each query into its tab panel or gating it on the active tab (enabled) so the read starts when the tab opens
