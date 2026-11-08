---
type: llm
focus: trace
weight: 1
---

audit.md does not recommend prefetching or warming every tab's query up front (for example ensureQueryData for all tabs in a route loader, or prefetching all three queries at mount); if it mentions prefetch at all it is on intent (hover or focus on the tab trigger) and only as the way to keep an instant open, which the owner did not ask for
