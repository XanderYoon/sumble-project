# Epic review report template

Write this report *after* a separate high-reasoning coding agent inspects the actual epic diff.
Store it at `.sdlc/reviews/E001.md` (replace ID). The `review-pass` command can also copy
an external report here. The reviewed HEAD must be the final commit **after** all fixes.

```markdown
# E001 independent review
Reviewer: <exact reviewer model and thinking setting>
Reviewed HEAD: <full 40-character git SHA>
Verdict: PASS

## Findings
- <file:line, defect, impact; or "None">

## Fixes and verification
- <fix commit and test command with observed result>

## Quality gate
- <ruff format/lint, ty, test commands and observed results>

## Remaining risks
- <non-blocking limitations; or "None">
```

Use `Verdict: FAIL` while any critical/major defect remains. Passing requires
explicit evidence, not a blanket "looks good" judgment. The CLI checks the
presence of `Verdict: PASS` and matching HEAD; the reviewer is responsible for the
substance of the review and rerunning tests. An edited branch invalidates its PASS.
