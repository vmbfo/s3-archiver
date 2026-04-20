# TDD Plan For The Archive Workflow With Isolated LocalStack Tests

## Summary
- Keep `check`, add explicit `s3-archiver archive`, and make bare `s3-archiver` print help and exit `0`.
- Standardize runtime config on `S3_SOURCE_BUCKET` and `S3_DESTINATION_BUCKET` for both `check` and `archive`.
- Add `ARCHIVER_RETENTION_DAYS`, `ARCHIVER_ENABLE_CLEANUP=false`, and `ARCHIVER_MAX_WORKERS=16`.
- Implement the archive job as strict phases: `list -> copy -> verify -> cleanup`. Each phase may run bounded parallel workers, but the next phase never starts until the previous one completed successfully for every eligible object.
- Treat cleanup as globally gated: unset or `false` means cleanup code must not run at all, and even when `true`, any copy or verify failure blocks all deletes.

## Runtime And Safety Contract
- `check` verifies access to both buckets and emits JSON with both bucket names.
- `archive` archives objects where `LastModified < now_utc - retention_days`. Objects exactly on the cutoff remain in the source bucket.
- Source and destination keys are preserved `1:1`.
- Copy must stay S3-to-S3 only. The app must never download object payloads into the container.
- Cleanup verification gate compares source and destination via S3 metadata only:
  - size must match
  - checksum fields must match when both sides expose them
  - otherwise ETag must match
  - any missing required signal or mismatch is a hard failure
- Every failure must be surfaced in stdout/stderr JSON and structured logs with phase, key, source bucket, destination bucket, and mismatch details.
- Reject identical source and destination buckets.
- Listing must use `ListObjectsV2` pagination with continuation tokens and `MaxKeys=1000`.

## Test Isolation And LocalStack Guard Rails
- Integration and e2e tests must create fresh UUID-named source and destination buckets for every test case, not per session.
- Bucket names should include a stable test prefix plus a lowercase UUID suffix so reruns are idempotent and isolated.
- Each test fixture must create its own bucket pair, seed only those buckets, and in teardown empty and delete both buckets even on failure.
- Remove reliance on one static integration bucket for test execution. LocalStack readiness should verify the S3 API is up, not depend on a shared test bucket.
- Test code must fail fast unless the target is LocalStack:
  - `S3_PROVIDER` must be `localstack`
  - endpoint host must match a strict LocalStack allowlist such as `127.0.0.1`, `localhost`, `localstack`, or `localhost.localstack.cloud`
  - test scripts must not load the normal production `.env`
- Keep LocalStack test env generation inside fixtures/scripts so integration and e2e runs cannot accidentally point at OCI or any live S3 endpoint.
- The LocalStack-only timestamp seeding path must be mounted only into the LocalStack test service and must never be part of the app runtime image.

## Implementation Changes
- Add core archive modules for settings, manifest building, copy orchestration, verification, cleanup, and structured archive reporting.
- Extend the typed S3 boundary to support paginated listing, `copy_object`, `head_object`, `delete_object`, and any checksum-capable metadata lookups needed for verification.
- Add a test-only LocalStack extension or equivalent in-container hook that rewrites object `last_modified` for deterministic retention tests.
- Add a seed helper that uploads predictable keys into the per-test source bucket and then sets exact timestamps through the LocalStack-only hook.
- Update the Compose test profile so LocalStack gets the test-only extension, while the app container remains the production-style runtime image.
- Keep test assets outside the shipped application packages. The current wheel-only runtime already excludes repo tests; preserve that as an explicit packaging rule and add a regression check for it.
- Add a runtime image check that asserts the production app container does not contain repo `tests/`, LocalStack seed helpers, or the LocalStack test extension.

## Test Plan
- Unit tests first, run red before any implementation:
  - env parsing and validation for the new two-bucket contract
  - bare-command help behavior
  - strict cleanup gating for unset and `false`
  - exact cutoff boundary for `59`, `60`, and `61` day objects
  - paginated multi-page listing
  - phase ordering and global cleanup blocking
  - verification mismatch behavior and error payload shape
  - LocalStack endpoint safety guard behavior
- Integration tests against LocalStack:
  - per-test bucket pair creation and teardown
  - deterministic timestamp seeding in isolated buckets
  - retention `60` with cleanup disabled
  - retention `60` with cleanup enabled
  - alternate retention such as `30`
  - exact expected source and destination key sets after each run
  - verification failure produces non-zero status and zero deletes
- E2E compose tests:
  - `docker compose run --rm app` shows help and does not archive
  - explicit `s3-archiver archive` with cleanup unset
  - explicit `s3-archiver archive` with cleanup `false`
  - explicit `s3-archiver archive` with cleanup `true`
  - runtime image excludes test suite assets and test-only LocalStack tooling
- Seed dataset for archive assertions:
  - one known object per day for ages `0..365`
  - explicit boundary fixtures for `59`, `60`, and `61`
  - destination bucket starts empty
  - expected split is asserted exactly for each configured retention

## Assumptions And Defaults
- `ARCHIVER_MAX_WORKERS` defaults to `16`.
- Time comparisons use UTC-aware datetimes only.
- Parallelism is allowed only inside a phase, never across phases.
- The LocalStack-only timestamp hook is test infrastructure, not application functionality.
- The production app image continues to be built from wheels only and must not ship the repository test suite or any LocalStack-only test code.
