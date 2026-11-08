# Report template

Use these sections in this order. Claim first, evidence second, fix third. A reader with the merge button and no context must be able to check every line.

```markdown
# Request audit: <app name>, <date>

<One paragraph. The headline finding and its measured size. Whether this is static-only or includes captures. The commit audited.>

## Scope and evidence

| Item | Value |
| --- | --- |
| Tree | `<path>` at `<commit>` (<branch>) |
| Data library | <TanStack Query 5.x / SWR / Apollo / RTK Query / none> |
| Defaults | staleTime <value>, gcTime <value>, retry <policy>, focus refetch <on/off + listener> |
| Static scan | <files> files, <N> candidates (<P0>/<P1>/<P2>), <verified> read and kept |
| Captures | <none> or one row per capture: journey, data size, file |
| Intent | <per screen: instant open (prefetch) or on demand, as the owner said; or "not asked, assumed on demand"> |
| Not covered | <routes, platforms, or flows not examined> |

## Coverage

<One sentence: what the static scan read (all N modules) and what the captures exercised.>

| Surface | Read in code | Exercised in a capture |
| --- | --- | --- |
| Routes | 83 | 12 (list in coverage.md) |
| Data modules | 559 | 140 (dev-server capture) |
| Lazy chunks with reads | 195 | 9 |
| Gated data modules (flag / platform / permission) | 38 | 4 |
| Endpoint paths in source | 151 | 47 |
| States (empty, error, slow, offline, idle, hidden, roles, cold/warm) | n/a | <which> |

Not exercised and why: <the high-read routes and lazy chunks no capture reached, with the reason: no fixture data, needs a role, platform not available>. Findings in that code are marked "static only".

## Findings

| # | Severity | Finding | Growth | Measured | Fix pattern | Effort |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | P0 | <one line> | rows | 100 → 1 (fixture, 100 rows) | Batch names projection | 2 h |

### 1. <Finding title>

Evidence: `<file:line>`; capture `<file>` section <name>.
Cause: <two sentences on why the code does this>.
Fix: <the pattern, with the specific key / hook / endpoint to change>. <Backend ask, if any.>
Delta: <formula with inputs> → <measured or "unmeasured">.

<repeat per finding>

## Non-findings

| Checked | Why it is fine |
| --- | --- |
| Hover prefetch on <list> | One deduped read per row; 10 hovers → 10 reads, 0 repeats in capture <file> |
| Fetch on open in <dialog> | Owner wants on demand; the read starts when the dialog mounts, one read per open, none on the page that holds the trigger |

## Open decisions

<Things that need a product or owner call: a polling cadence, a freshness window users would notice, a feature that depends on a fetch-all.>

## Backend asks

| Ask | Unblocks |
| --- | --- |
| Id filter on `GET /things` | Finding 1 |

## Contradictions with existing docs

<Claims in README / audit docs / comments that the code at this commit does not support, with the file for each.>

## Limits

<What the audit did not measure: production volume, authenticated flows, other platforms, data sizes above the fixture.>

## Suggested order of work

1. <Finding n>: <why first>
2. ...
```

Rules for filling it in:

- Every number names its source (a capture file and section, or a code path with the inputs to the formula).
- "Measured" is a before → after from a capture, or "unmeasured" with the formula. Never a guess.
- The effort column is hours for someone who knows the codebase. Include tests and review.
- If the audit is static-only, the first paragraph says so and the Measured column says "unmeasured" throughout. The Coverage table still lists the surface, with "none" in the exercised column.
- Each finding says whether its code was exercised in a capture or read only ("static only"). A non-finding about code no capture reached is a non-finding of the code, not of the behaviour.
- Keep the findings table to the ones you verified. Candidates you did not read go under Limits as "not reviewed: <count> <detector> candidates", not in the table.
