---
tags: [overfetch]
max_turns: 30
timeout_seconds: 600
allowed_tools: [Read, Glob, Grep, Skill, Write, Bash]
---

Audit the data fetching in ./tabbed-app and write the report to audit.md. One thing to know before you start: we want screens to fetch on demand. A spinner when someone opens a tab or a dialog is fine with us; what we do not want is paying for requests nobody looks at.

Input files: tabbed-app (directory)
