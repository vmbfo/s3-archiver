# Daily Filename-Timestamp Archives

## Summary

- Change the archive unit from individual S3 objects to one complete UTC target day per run.
- Derive the target day as `run_started_at_utc.date() - ARCHIVER_RETENTION_DAYS`; process only `[YYYY-MM-DDT00:00:00Z, next day)`.
- Keep source whitelist/blacklist behavior unchanged: filters only decide which source keys are considered.
- Build a frozen manifest before transfer, group objects by inferred archive root plus target day, create deterministic `.tar.gz` files in the container, upload them to S3, then cleanup only verified groups when cleanup is enabled.

## Key Changes

- Add timestamp parsing and scoring:
  - Parse basename first; basename timestamps are the primary signal.
  - If basename has no timestamp, use path timestamps.
  - Path timestamps only corroborate basename timestamps; they do not override them.
  - S3 `LastModified` is never a primary archive timestamp, only a tie-breaker between key-derived candidates.
  - If no reliable filename/path timestamp can be selected, skip the object and never cleanup it.
- Add path flattening:
  - Destination archive key is derived from the source key parent with timestamp-like path suffixes removed.
  - Examples:
    - `data/fae/2026/04/13/07/2026-04-13T07-00-00.xml` -> `data/fae/2026-04-13.tar.gz`
    - `data/harmonie/HARMONIE_DINI_SF_2026-04-24T000000Z_2026-04-24T000000Z.bz2` -> `data/harmonie/2026-04-24.tar.gz`
  - Tar members use the full original S3 key.
- Replace S3-to-S3 copy:
  - Download each manifest object into container temp storage or stream through the container.
  - Create deterministic `tar.gz` output with stable member ordering and metadata.
  - Upload the archive object to the destination bucket.
- Store destination metadata:
  - `s3-archiver-archive-sha256`
  - `s3-archiver-manifest-sha256`
  - `s3-archiver-target-day`
  - `s3-archiver-source-count`
  - `s3-archiver-schema-version`
- Existing destination archive behavior:
  - If archive exists and metadata manifest hash matches the current group, treat it as verified and allow cleanup if enabled.
  - If archive exists but metadata is missing or differs, skip that group and do not cleanup its source objects.

## Interface Updates

- Extend manifest models with selected timestamp, timestamp source, target day, archive root, destination archive key, grouped archive entries, and skipped-object reasons.
- Add S3 adapter methods for source download/read and archive upload; stop using native S3 copy in the archive workflow.
- Update CLI payloads, cleanup preview, visual demo, and run records to report target day, archive count, source object count, skipped object count, destination archive keys, and cleanup status per archive group.

## Test Plan

- Unit-test timestamp parsing for ISO variants, `Z`, dash-separated times, path segment dates, multiple equal timestamps, conflicting timestamps, and no-timestamp skips.
- Unit-test target-day selection so runs never include partial days or multiple UTC dates.
- Unit-test flattening for `data/fae/...` and `data/harmonie/...`.
- Unit-test deterministic tar creation, full S3 key tar member names, manifest hash metadata, and archive hash metadata.
- Unit-test existing archive cases: matching hash permits cleanup, differing/missing metadata skips cleanup.
- Unit-test relaxed cleanup: no source `LastModified` recheck before delete; versioned deletes still use exact version IDs.
- Run the quick validation pass only: focused unit tests plus type/lint checks for changed modules. Full suite/reviewer loop remains reserved for push.

## Assumptions

- Timestamp strings without an explicit timezone are UTC.
- One run may produce multiple archives for the same target day if multiple archive roots are present, but it must never produce archives for multiple dates.
- Source objects older than the retention target day are assumed stable; cleanup relies on the verified daily archive rather than per-object destination copies.
