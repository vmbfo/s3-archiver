# Plans

This directory stores historical implementation plans. Treat dated plan files as immutable snapshots: do not edit them to match current behavior, fix old examples, or mark sections as superseded. If runtime behavior changes, update the live documentation instead.

Use the root `README.md`, package READMEs, and checked-in env templates for current usage examples. Use dated plan files only to understand the intent and constraints at the time the plan was written.

## Structure

- `YYYYMMDD-*.md`: dated implementation plans captured for historical context.
- `README.md`: this living guide for how to handle the plans directory.

When adding a new plan, create a new dated file rather than revising an existing plan. If a plan needs follow-up context after implementation, put that context in current docs, changelog entries, issues, or a new dated plan.
