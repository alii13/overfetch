---
type: llm
focus: trace
weight: 1
---

audit.md states that LogsDialog (src/components/logs-dialog.tsx) is fine or by design: its query lives in the dialog body so the read starts when the dialog opens and the page holding the trigger pays nothing; it does not call that click-then-fetch a waterfall, a latency defect, or a finding
