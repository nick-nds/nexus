<!--
Thanks for the PR. Keep titles short (~70 chars) and conventional:

  feat(query): add list_modules tool
  fix(graph): handle dangling edges from vendor classes
  docs(error-codes): document key_not_found

The body fields below are the contract. Keep them — empty
sections are fine, but don't delete the headings.
-->

## Summary

<!-- 1–3 bullets, "why" not "what". -->

-

## Linked issue

<!-- "Closes #123", or "n/a — chore" -->



## Phase scope

<!--
Per CLAUDE.md and internal_docs/MASTER-PLAN.md, work happens in
phases. Confirm this change is in scope for the current phase, or
explain why it's out-of-band.
-->

- [ ] In scope for the current phase, OR
- [ ] Out-of-band (explain): _____

## Changes

<!-- Bullet list of files / behaviour added, modified, removed. -->

-

## Testing

<!--
Cover what you ran locally. CI runs the same gates but loud
local results catch problems faster.
-->

- [ ] `uv run pytest -q` — all tests pass
- [ ] `uv run mypy --strict nexus/` — clean
- [ ] `uv run ruff check . && uv run ruff format --check .` — clean
- [ ] Coverage stays ≥ 90% (`pytest --cov-fail-under=90`)
- [ ] PHP package (if touched): `composer lint && composer stan && composer test`

## Documentation impact

<!-- If you changed an interface, schema, or error code, update the matching doc. -->

- [ ] No public-facing change
- [ ] Updated `docs/cli-reference.md` / `docs/mcp-reference.md`
- [ ] Updated `docs/error-codes.md` (and `scripts/list_error_codes.py --strict` passes)
- [ ] Updated the relevant `internal_docs/PHASE-*.md` checklist
- [ ] Bumped a `schema_version` and added a migration

## Decision-log impact

<!-- Anything that contradicts a prior D# entry, or new design call worth recording. -->

- [ ] No design decision changed
- [ ] Added entry to `internal_docs/13-decision-log.md` (D## …)

## Backwards-compatibility

- [ ] No public-API changes (CLI surface, MCP tool names, output schemas)
- [ ] Public-API change behind a deprecation path
- [ ] **Breaking** change — and the freeze test (`tests/unit/test_interface_freeze.py`) is updated

## Anything reviewers should look at first

<!-- Optional. Tricky diff, contentious naming, alternative approaches considered. -->
