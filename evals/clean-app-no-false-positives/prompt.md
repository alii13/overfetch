---
tags: [request-audit]
max_turns: 30
timeout_seconds: 600
allowed_tools: [Read, Glob, Grep, Skill, Write, Bash]
---

Can you audit the data fetching in ./tidy-app and write what you find to audit.md? Someone on the team claims it has N+1 problems and polling storms and wants to rewrite the data layer. I want an honest read before anyone touches it.

Input files: tidy-app (directory)
