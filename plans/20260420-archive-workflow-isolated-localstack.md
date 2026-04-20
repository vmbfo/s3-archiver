# TDD Plan For The Archive Workflow With Isolated LocalStack Tests

## Summary
- Keep `check`, add explicit `s3-archiver archive`, and make bare `s3-archiver` print help and exit `0`.
- Standardize runtime config on separate source and destination S3 connection settings so the two buckets can use entirely different credentials, users, and endpoints.
- Add `ARCHIVER_RETENTION_DAYS`, `ARCHIVER_ENABLE_CLEANUP=false`, and `ARCHIVER_MAX_WORKERS=16`.
- Freeze one `run_started_at_utc` timestamp at the beginning of every archive invocation and use that same timestamp for eligibility, verification, and cleanup decisions for the entire run.
- Implement the archive job as strict phases: `list -> copy -> verify -> cleanup`. Each phase may run bounded parallel workers, but the next phase never starts until the previous one completed successfully for every eligible object.
- Treat cleanup as globally gated: unset or `false` means cleanup code must not run at all, and even when `true`, any copy or verify failure blocks all deletes.
- Run the archive job from a daily task scheduler. Each daily run takes a fresh frozen timestamp and retries from the start if the previous day failed.

## Runtime And Safety Contract
- `check` verifies access to both buckets and emits JSON with both bucket names.
- Source and destination S3 settings are independent. Each side must support its own provider, credentials, region, bucket, endpoint override, and addressing style so the archive job can bridge between different S3 users or even different S3-compatible systems.
- All env vars must be parsed and validated at startup before any archive work begins. Type checks, enum validation, and numeric range checks must happen there so runtime invariants are established once and relied on afterwards.
- The runtime contract uses separate env groups for source and destination:
  - source: `S3_SOURCE_PROVIDER`, `S3_SOURCE_ACCESS_KEY_ID`, `S3_SOURCE_SECRET_ACCESS_KEY`, `S3_SOURCE_REGION`, `S3_SOURCE_BUCKET`
  - destination: `S3_DESTINATION_PROVIDER`, `S3_DESTINATION_ACCESS_KEY_ID`, `S3_DESTINATION_SECRET_ACCESS_KEY`, `S3_DESTINATION_REGION`, `S3_DESTINATION_BUCKET`
  - optional per-side overrides: `S3_SOURCE_ENDPOINT_URL`, `S3_DESTINATION_ENDPOINT_URL`, `S3_SOURCE_ADDRESSING_STYLE`, `S3_DESTINATION_ADDRESSING_STYLE`
  - OCI-only per-side fields when relevant: `S3_SOURCE_NAMESPACE`, `S3_SOURCE_IAM_USER_OCID`, `S3_DESTINATION_NAMESPACE`, `S3_DESTINATION_IAM_USER_OCID`
- Optional env vars may fall back to explicit defaults, but required auth and bucket settings must hard-fail startup when missing. In particular, non-LocalStack providers must not start without valid S3 auth inputs for that side.
- Invalid env values must crash startup immediately, print a clear error to the console, and emit the same failure into the structured container logs so configuration mistakes surface immediately in Docker.
- Startup must include a real S3 connectivity/auth check against the configured source and destination buckets so invalid credentials or unreachable endpoints fail before the archive phases begin.
- `archive` captures `run_started_at_utc` once when the command starts and archives objects where `LastModified < run_started_at_utc - retention_days`. Objects exactly on the cutoff remain in the source bucket.
- Source and destination keys are preserved `1:1`.
- Copy must stay S3-to-S3 only. The app must never download object payloads into the container.
- The same frozen `run_started_at_utc` must be reused during cleanup so no object can become newly eligible mid-run because the task took a long time.
- Cleanup verification gate compares source and destination via S3 metadata only:
  - size must match
  - checksum fields must match when both sides expose them
  - otherwise ETag must match
  - any missing required signal or mismatch is a hard failure
- Every failure must be surfaced in stdout/stderr JSON and structured logs with phase, key, source bucket, destination bucket, and mismatch details.
- Reject identical source and destination buckets.
- Listing must use `ListObjectsV2` pagination with continuation tokens and `MaxKeys=1000`.
- If any copy worker fails, the run stops after the copy phase and does not progress to verify or cleanup. The next daily scheduled run starts again from the copy phase with a new frozen timestamp.
- If any verify worker fails, the run stops after the verify phase and does not progress to cleanup. The next daily scheduled run starts again from the copy phase with a new frozen timestamp.
- Any failure at any stage must terminate the current invocation cleanly with a non-zero result, stop all further progress for that run, and leave retry to the next scheduled daily invocation.

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
- Add core archive modules for per-side settings, dual-client run context with frozen timestamp, manifest building, copy orchestration, verification, cleanup, and structured archive reporting.
- Add a dedicated env decoding layer that validates every variable up front and models parse results in a pureenv/result-style boundary so defaults and hard failures are handled explicitly and consistently.
- Extend the typed S3 boundary to support separate source and destination clients plus paginated listing, `copy_object`, `head_object`, `delete_object`, and any checksum-capable metadata lookups needed for verification.
- Validate type and range invariants at startup, including booleans, retention days, worker counts, providers, addressing styles, and any per-side required field combinations.
- If direct server-side copy across the two configured clients is not supported by the backing S3 systems, fail fast with a clear configuration/runtime error rather than silently falling back to downloading object payloads through the container.
- Add scheduler-facing runtime support so the archive command is the unit of daily execution, with one fresh frozen timestamp per scheduled run.
- Add a test-only LocalStack extension or equivalent in-container hook that rewrites object `last_modified` for deterministic retention tests.
- Add a seed helper that uploads predictable keys into the per-test source bucket and then sets exact timestamps through the LocalStack-only hook.
- Update the Compose test profile so LocalStack gets the test-only extension, while the app container remains the production-style runtime image.
- Keep test assets outside the shipped application packages. The current wheel-only runtime already excludes repo tests; preserve that as an explicit packaging rule and add a regression check for it.
- Add a runtime image check that asserts the production app container does not contain repo `tests/`, LocalStack seed helpers, or the LocalStack test extension.
- Add scheduler documentation and local orchestration helpers so the archive task runs only locally and on the production machine. Do not rely on GitHub Actions for the orchestrator or for archive scheduling.

## Test Plan
- Unit tests first, run red before any implementation:
  - env parsing and validation for the new per-side S3 contract
  - defaults are applied only where explicitly allowed
  - invalid type, enum, and range values fail fast at startup
  - missing required auth settings fail fast at startup
  - bare-command help behavior
  - strict cleanup gating for unset and `false`
  - frozen `run_started_at_utc` is captured once and reused across all phases
  - separate source and destination credentials are loaded independently
  - exact cutoff boundary for `59`, `60`, and `61` day objects
  - paginated multi-page listing
  - phase ordering and global cleanup blocking
  - copy failure prevents verify and cleanup from starting
  - verify failure prevents cleanup from starting
  - any stage failure exits the invocation cleanly and prevents later stages from running
  - verification mismatch behavior and error payload shape
  - LocalStack endpoint safety guard behavior
- Integration tests against LocalStack:
  - per-test bucket pair creation and teardown
  - isolated source and destination clients with distinct credentials
  - startup bucket/auth validation happens before any archive phase work
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
- Scheduler behavior assertions:
  - each run uses a fresh frozen timestamp
  - a failed run does not leave cleanup partially executed
  - a failed run exits cleanly and does not continue into later phases
  - the next scheduled run restarts from copy using the new run timestamp

## Assumptions And Defaults
- `ARCHIVER_MAX_WORKERS` defaults to `16`.
- Time comparisons use UTC-aware datetimes only.
- Each archive run owns one immutable `run_started_at_utc` timestamp from process start to process exit.
- Env validation is a startup boundary: after startup succeeds, the rest of the runtime can assume env-derived settings are present, correctly typed, and within valid ranges.
- Source and destination may use different S3 identities and independent configuration, but the implementation still forbids downloading object payloads through the app container.
- Parallelism is allowed only inside a phase, never across phases.
- The LocalStack-only timestamp hook is test infrastructure, not application functionality.
- The production app image continues to be built from wheels only and must not ship the repository test suite or any LocalStack-only test code.
