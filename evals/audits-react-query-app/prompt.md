---
tags: [request-audit]
max_turns: 40
timeout_seconds: 900
allowed_tools: [Read, Glob, Grep, Skill, Write, Bash]
---

Our React app in ./sample-app makes far more API calls than it should and the backend team is complaining about load. Do a full request / data-fetching audit and write the report to audit.md: tell me exactly what to fix, in priority order, with file and line references, and what I should leave alone. We use TanStack Query v5.

Input files: sample-app (directory)
