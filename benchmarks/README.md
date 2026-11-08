# Benchmarks

Does the skill make an agent's request audit better than the same agent without it? Each eval case runs twice: once by an agent told to load `overfetch` and follow it, once by an agent with no skill (the control). Both get the same prompt and the same input files. A separate grader agent then checks each report against the case's assertions, strictly, and quotes the evidence for every pass or fail.

The cases, prompts and assertions live in [`../evals/`](../evals). Every assertion and its evidence for both iterations is in [`results.json`](results.json).

## Latest: iteration 2, 2026-11-04

A fourth case, `eager-offscreen-tabs` (reads mounted for tabs the user has not opened, against an owner who asked for fetch on demand), was added on 2026-11-08 and run once per configuration with `claude plugin eval` (executor and judge Claude Opus 5.5, three judge votes per assertion): with overfetch 5 / 5, without 4 / 5. Both runs found the eager tab reads, left the fetch-on-open dialog alone and did not recommend prefetching every tab. The baseline stated its request counts as measured and never said the audit was static-only. The prompt states the owner's intent, which hands the baseline the deciding fact, so this case does not yet show the new guidance making a difference on its own.

| Case | With overfetch | Without |
| --- | --- | --- |
| App audit: planted issues, file routes, a lazily loaded flag-gated dialog | 11 / 11 | 8 / 11 |
| HAR analysis: capture only, no source tree | 10 / 11 | 6 / 11 |
| Clean app: must not invent problems | 7 / 7 | 6 / 7 |
| **All assertions** | **28 / 29 (97%)** | **20 / 29 (69%)** |

| Cost per run, mean | With overfetch | Without |
| --- | --- | --- |
| Tokens | 189,964 | 147,169 |
| Wall time | 425 s | 284 s |

### What separated the runs

- **Coverage.** The skill's app report has a coverage section naming the routes, the lazily loaded dialog and its flag gate as read in code only. The baseline mentions them in passing.
- **Library semantics.** In the HAR case the baseline wrote that two TanStack Query observers with `refetchInterval` produce two offset clocks. They do not: same-key observers restart their intervals on every query update and share one fetch (query-core 5.x, `Query#dispatch` → `onQueryUpdate` → `#updateTimers`). The skill's report used that fact to rule the explanation out.
- **What a capture cannot show.** The skill's HAR report labels each cause as an inference, lists the states the session never exercised, and names the captures that would close the gap. The baseline's limits stop at what the HAR file lacks.
- **Scope discipline.** The baseline recommended a change to the component that was clean on purpose and had no explicit list of what it ruled out.

Pattern detection alone did not separate them. Both configurations found the per-row burst, the two billing clocks, the duplicate reads, the serial boot chain and the 404 retries.

The skill's one miss: in the HAR report, three "because" clauses were stated as fact under a single "every cause is inferred" disclaimer. `SKILL.md` now asks for the hedge on each causal clause.

## Iteration 1, 2026-11-04

| Case | With overfetch | Without |
| --- | --- | --- |
| App audit | 8 / 8 | 6 / 8 |
| HAR analysis | 8 / 8 | 7 / 8 |
| Clean app | 5 / 5 | 5 / 5 |
| **All assertions** | **21 / 21 (100%)** | **18 / 21 (86%)** |

Iteration 1 graded the skill at 100%, yet the grader found the skill repeating a wrong library fact that no assertion checked, and the clean-app assertions could not tell the two configurations apart because the fixture's comments gave the answers away. Between iterations the comments were stripped, assertions were added for coverage, attribution, labelled inference and library semantics, the fixture gained file routes and a lazily loaded dialog, and the skill gained the coverage map. The two iterations are not comparable to each other; each compares the skill against its own baseline.

## Method and limits

- **Runs.** One run per configuration per case. There is no variance estimate, so treat a difference of one assertion as noise.
- **Fixtures.** Small synthetic apps and a synthetic HAR built to contain known patterns. They test whether the agent finds, ranks and qualifies those patterns; they say nothing about production request volume.
- **Grading.** An LLM grader with the case's ground truth and strict pass rules. Graders also critiqued the assertions, which is how the iteration-1 blind spots were found.
- **Models.** Executor and grader agents were Claude Code subagents on the session's model: Claude Fable 5.1 for iteration 1 and Claude Opus 5.5 for iteration 2.
- **Harness.** In iteration 2, both HAR runs had their file write blocked by a subagent rule; the text each agent returned was saved verbatim and graded.
- **Reproduce.** From the repository root, `claude plugin eval . --allow-tools Read,Glob,Grep,Bash,Write --scaffold` runs the same cases with and without the skill.
