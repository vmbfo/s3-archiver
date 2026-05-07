# s3-archiver-core

Typed configuration, S3 clients, route manifest building, archive execution, logging, and health checks for `s3-archiver`.

## Route Model

`AppSettings.from_env` reads `ARCHIVER_CONFIG_JSON`, a JSON array of route objects. Each route has a unique `name`, a `parser`, a `copy_mode`, a `source` S3 location, and a `destination` S3 location.

Built-in parsers:

- `filename_timestamp`: selects objects with reliable UTC timestamps in the source key basename.
- `folder_timestamp`: selects objects with reliable UTC timestamps in parent folders.
- `direct`: selects objects from S3 `LastModified`.

Custom parsers can be added by copying `s3_archiver_core/parsers/template.py` to a new
snake_case module in the parser package. Modules that expose a `Parser` class are
registered automatically by filename, excluding the template itself.

Supported copy modes:

- `daily_tar_gz`: groups selected objects by route, archive root, and data day, then writes deterministic `.tar.gz` archives.
- `direct`: copies each selected object to the destination path.

Route names must be unique, source and destination storage identities must differ, and OCI locations require `namespace` and `iam_user_ocid`. Source paths are validated per storage location with directory-boundary prefix semantics, so sibling prefixes such as `data` and `database` are separate routes while `data` and `data/fae` overlap.
