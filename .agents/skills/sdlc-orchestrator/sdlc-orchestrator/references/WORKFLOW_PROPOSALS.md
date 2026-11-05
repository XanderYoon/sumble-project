# Optional SDLC workflow changes — awaiting human approval

The features below are **not implemented**. Approve/veto each independently.

| ID | Proposed change | Benefit | Cost/tradeoff |
|---|---|---|---|
| P1 | Add stable `REQ-###` IDs in PRD, and require each acceptance criterion to map to an epic, ticket and test. | Fewer missing requirements; traceable tests. | More planning metadata and validation. |
| P2 | Add a mandatory cross-epic integration test/review before release after all MRs land. | Catches failures between individually reviewed features. | Slower final merge/release. |
| P3 | Implement mid-implementation change requests as new, versioned requirements with explicit impact/replanning instead of blocking all plan edits. | Safe response to evolving product behavior. | More complex state migration and dependency handling. |
| P4 | Track actual model calls/token usage/cost per ticket and recommend promotion/demotion after one failed attempt. | Better dollar efficiency with evidence. | Needs provider usage adapters and consent for tracking. |
| P5 | Allow independent tickets within the same epic to execute simultaneously using ticket-scoped worktrees, then integrate/cherry-pick in dependency order. | Better concurrency for large epics. | Merge complexity, extra branches, conflict resolution. |
| P6 | Require CI status and verified MR merge metadata from GitHub/GitLab before allowing `mark-merged`. | Prevents a human from accidentally declaring an unmerged squash MR complete. | Requires authenticated forge APIs/CLIs; offline repos need exception path. |

**Default:** veto by omission. Do not change the runtime behavior until the
user explicitly approves an ID. An approved change should itself have a PRD
amendment, architecture update, tests, and versioned skill release.
