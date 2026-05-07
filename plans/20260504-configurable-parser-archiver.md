# Configurable Parser Archiver Rewrite

## Summary

- Rewrite runtime configuration around one required env var, `ARCHIVER_CONFIG_JSON`, containing a JSON array of route objects.
- Remove whitelist, blacklist, retention, and cleanup concepts.
- Each route selects one parser and one copy mode. The parser decides which objects are eligible and which timestamp source is authoritative.
- Each JSON object is one route worker. The run freezes `run_started_at_utc`, builds a deterministic manifest, then executes one worker per route.

## Interfaces

- `ARCHIVER_CONFIG_JSON` shape:

  ```json
  [
    {
      "name": "fae-daily",
      "parser": "filename_timestamp",
      "copy_mode": "daily_tar_gz",
      "source": {
        "provider": "localstack",
        "endpoint_url": "http://localstack:4566",
        "region": "us-east-1",
        "bucket": "source-bucket",
        "path": "data/fae/",
        "access_key_id": "${S3_SOURCE_ACCESS_KEY_ID}",
        "secret_access_key": "${S3_SOURCE_SECRET_ACCESS_KEY}",
        "addressing_style": "path"
      },
      "destination": {
        "provider": "localstack",
        "endpoint_url": "http://localstack:4566",
        "region": "us-east-1",
        "bucket": "archive-bucket",
        "path": "archives/fae/",
        "access_key_id": "${S3_DESTINATION_ACCESS_KEY_ID}",
        "secret_access_key": "${S3_DESTINATION_SECRET_ACCESS_KEY}",
        "addressing_style": "path"
      }
    }
  ]
  ```

- Add `ParserKind`: `direct`, `filename_timestamp`, `folder_timestamp`.
- Add `CopyMode`: `direct`, `daily_tar_gz`.
- Parser contract: given an S3 listed object plus optional head/metadata data, return either `SelectedObject(timestamp, timestamp_source, archive_root)` or `SkippedObject(reason)`.
- Startup validates parser and copy-mode enum values. Invalid selections fail startup; there is no fallback parser.

## Parser Behavior

- `direct` parser preserves the original system behavior: select objects using S3 object data, including `LastModified`, metadata, size, version id, hash/checksum or ETag, headers, and tags where available.
- `filename_timestamp` parser selects objects using timestamps parsed from the object key filename/path. It must not use S3 `LastModified` as a fallback selection timestamp.
- `folder_timestamp` parser selects objects using timestamp-bearing folder segments. It must not use S3 `LastModified` as a fallback selection timestamp.
- Custom parsers added later follow the same protocol and own their selection logic completely.

## Key Changes

- Replace `PathFilterSettings`, `SourcePathFilter`, `ARCHIVER_RETENTION_DAYS`, `ARCHIVER_ENABLE_CLEANUP`, and global `ARCHIVER_MAX_WORKERS` with validated route config models.
- Create `packages/s3_archiver_core/src/s3_archiver_core/parsers/` with a parser protocol, enum registry, concrete parser files, and a non-registered copy-paste template parser.
- Move current filename/path timestamp logic from `archive_timestamp.py` into `parsers/filename_timestamp.py`.
- Build a global manifest containing route name, parser kind, copy mode, source/destination identity, source key, version id, parser-selected timestamp/source, destination key, and archive group when relevant.
- Reject overlapping `source.path` prefixes for routes that point at the same normalized source storage location. Also fail manifest building if duplicate source object identities or duplicate destination object identities still appear.
- Remove cleanup phase, cleanup preview command, demo cleanup mode, cleanup payload fields, and source delete calls. Archive phases become `list -> copy -> verify`.

## Copy Behavior

- `copy_mode: direct` copies each parser-selected source object to the destination using the full original source key.
- `copy_mode: daily_tar_gz` writes deterministic day archives under `destination.path`, grouped by parser-selected UTC date and archive root.
- Copy mode never changes eligibility or substitutes timestamps. It only chooses the destination write format for parser-selected objects.
- Existing destination objects are verified before being treated as complete; conflicting destination objects fail the run rather than being overwritten silently.

## Compose

- Update `compose.yaml` so `app` and `scheduler` show `ARCHIVER_CONFIG_JSON` as a readable YAML block scalar.
- Keep secrets referenced through Compose interpolation inside the JSON string.
- Remove compose examples and env references for whitelist, blacklist, retention, cleanup, and max workers.

## Test Plan

- Unit-test config JSON decoding, required fields, enum validation, invalid parser/copy-mode failures, duplicate route names, source/destination identity rejection, and overlapping source path rejection.
- Unit-test parser registry behavior, direct parser S3 timestamp/metadata behavior, filename parser parity with current behavior, folder parser behavior, and template parser non-registration.
- Unit-test that parser-selected timestamps control eligibility in both copy modes.
- Unit-test that only the `direct` parser uses S3 timestamps, and non-direct parsers do not fall back to S3 timestamps.
- Unit-test manifest construction for frozen run timestamp handling, direct full-key destinations, daily archive grouping, parser skip reasons, duplicate source object failure, and duplicate destination object failure across routes.
- Unit-test archive execution with one worker per route, no cleanup phase, existing destination verification, direct-copy fingerprint conflicts, and deterministic tar metadata.
- Run quick validation only: focused unit tests plus `make format-check`, `make lint`, `make typecheck`, and `make type-coverage`. Full CI/reviewer loop remains a pre-push gate.

## Assumptions

- `ARCHIVER_CONFIG_JSON` is the only source/destination archive configuration for `check`, `archive`, and `schedule`.
- A parser-selected timestamp later than the frozen `run_started_at_utc` is skipped as not yet stable for that run.
- `parser: direct` and `copy_mode: direct` may be used together for original 1:1 copy behavior, but they remain separate validated fields.
- Parser failures skip only the affected object with a manifest reason unless the failure is a configuration or infrastructure error.
