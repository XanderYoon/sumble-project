# User delivery overrides during implementation

These explicit user instructions supersede the earlier delivery actions in the
approved PRD/architecture/board. Preserve that history; do not rewrite frozen
plan hashes or discard completed tickets. DECISIONS.md records the changes for
T007 through the SDLC decision command.

## No Google form submission

User: "Do not submit to the google form!"

Do not submit, upload, or send project artifacts to the Google form. Do not ask
for form identity details or attempt authentication. Final source/code review
and local delivery are the current scope.

## No ZIP creation before codebase approval

User: "don't zip until I give my approval for entire codebase (omit that
requirement and proceed)"

Continue implementation, tests, Docker verification, benchmarks, documentation,
and independent code review. Do not create any ZIP, including temporary test
ZIPs. Do not execute the build_submission command in the original T007 test
list, inspect a generated ZIP, or run extraction-from-ZIP checks yet.

The packaging function may be implemented and inspected as source, but must not
be invoked before the user's explicit approval of the entire codebase. Record
archive generation/verification as deferred instead of claiming those checks
passed. A fresh copied source directory can verify clean Compose startup
without producing an archive.

Before approval, present the completed source and actual validation evidence
for review. GitHub code review/merge-request work in the invoked workflow can
continue; these instructions prohibit Google submission and premature archives.
