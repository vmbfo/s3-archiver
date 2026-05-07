# s3-archiver-cli

Typer-based CLI package for the `s3-archiver` entrypoint.

## Commands

- `s3-archiver check`: load configuration, validate all routes, and verify source and destination bucket access.
- `s3-archiver archive`: run one archive invocation for every configured route.
- `s3-archiver schedule`: run the built-in once-per-day UTC scheduler loop.
- `s3-archiver demo`: run the compose-backed visual demo workflow.

## Runtime Environment

The CLI loads `.env` by default. `APP_ENV_FILE` or `ENV_FILE` can point at another env file, and process environment variables override file values. Set `APP_ENV_FILE=/dev/null` when the runtime should fail closed unless every required variable is supplied by Compose or the process environment.

Archive routes come from `ARCHIVER_CONFIG_JSON`. Each route chooses a registered parser, a copy mode (`daily_tar_gz` or `direct`), and source and destination S3 locations. Built-in parser names are `filename_timestamp`, `folder_timestamp`, and `direct`; custom parser modules that expose a `Parser` class are registered by filename. `daily_tar_gz` writes deterministic grouped archives by data day. `direct` copy mode writes one destination object per selected source key under the route destination path.
