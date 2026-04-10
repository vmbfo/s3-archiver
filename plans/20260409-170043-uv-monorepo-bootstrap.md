# Bootstrap `s3-archiver` as a Strictly Typed uv Monorepo

## Summary
- First mutation: create this file, then initialize git with `git init -b main`.
- Scaffold a uv workspace monorepo with a virtual root and two packages under `packages/`: `s3_archiver_core` and `s3_archiver_cli`. Only package source code lives under each package's `src/`; tests, scripts, Docker, docs, hooks, and plans stay at the repo root.
- Bootstrap one runnable behavior now: `s3-archiver check`, which loads config from `.env`, builds an OCI S3-compatible client, validates access to the configured bucket, and exercises the logging pipeline.
- Enforce repo-level quality as policy, not convention: 100% strict typing, 100% measured type-coverage gate, zero `Any` usage except where technically unavoidable and explicitly isolated, 100% test coverage for authored package code, warnings-as-errors, and a 300-LOC max per authored source file.

## Interfaces And Public Contract
- Workspace layout: root `pyproject.toml` is a non-packaged uv workspace root with shared dependency groups and one `uv.lock`; each package has its own `pyproject.toml` using `uv_build`.
- Package responsibilities: `s3_archiver_core` owns settings, validation, OCI/LocalStack endpoint logic, boto3 session/client construction, health checks, and logging setup. `s3_archiver_cli` owns the Typer CLI, console entrypoint, and exit-code mapping.
- CLI surface:
  - `s3-archiver check` validates config, bucket reachability, and log sink health.
  - `s3-archiver check` emits machine-readable JSON by default for container and ops use.
  - `s3-archiver check` maps `ConfigError`, `LoggingError`, and `HealthCheckError` to distinct non-zero exit codes so automation can distinguish failure classes.
- `.env` contract for OCI runtime:
  - `S3_PROVIDER=oci`
  - `S3_ACCESS_KEY_ID`
  - `S3_SECRET_ACCESS_KEY`
  - `S3_REGION`
  - `S3_NAMESPACE`
  - `S3_BUCKET`
  - `OCI_IAM_USER_OCID`
  - Optional: `S3_ENDPOINT_URL`, `S3_ADDRESSING_STYLE` default `path`, `LOG_LEVEL` default `INFO`, `LOG_DIR` default `/var/log/s3-archiver`
- Test/local contract:
  - Integration and e2e flows override runtime config to target LocalStack S3 with dummy credentials.
  - The app must support path-style addressing because LocalStack documents that non-`s3.` endpoints should use path-style requests.
- Logging contract:
  - All application logs are emitted to stdout first and also written as the same JSON-line records to a daily rotating file sink.
  - Retention is 30 files, one file per day.
  - Runtime log directory defaults to `/var/log/s3-archiver` in-container and is backed by a Docker named volume in compose.
  - `s3-archiver check` must fail non-zero if the file sink is not writable or rotation directory initialization fails.
- Versioning contract:
  - One repo-wide semver version shared by both packages.
  - Canonical version sources are `VERSION`, root `pyproject.toml`, and each package `pyproject.toml`.
  - Releases generate one root `CHANGELOG.md` and one git tag `vX.Y.Z`.

## Implementation Changes
- Workspace and packaging:
  - Add root workspace config, dependency groups for `dev`, `lint`, `test`, `typecheck`, and `release`, plus a root command surface through `Makefile` or `justfile`.
  - Use Python 3.12 as the managed project runtime; document one-time uv installation because `uv` is not installed in the current environment.
- Strict typing:
  - Use `basedpyright` as the primary static type gate with `typeCheckingMode = "all"` and all `reportUnknown*`, `reportAny`, and `reportExplicitAny` rules set to error.
  - Add a repo rule that forbids `typing.Any` and untyped defs in authored code unless there is a documented, isolated third-party boundary wrapper.
  - Add a `type-coverage` command that runs `pyright --verifytypes` for both published packages and fails unless the reported completeness score is exactly 100 for each package API.
  - Keep third-party untyped interactions behind narrow adapter modules with fully typed internal facades so the rest of the codebase remains strictly typed.
- Testing and coverage:
  - Use `pytest`, `pytest-cov`, and explicit suite markers for `unit`, `integration`, and `e2e`.
  - Require 100% statement coverage for all authored code under `packages/`.
  - Add coverage fail-under gates and make missing branch coverage impossible by keeping authored functions small and directly exercised.
  - Add tests for the test infrastructure itself where it affects product confidence: fixture validation, LocalStack bucket bootstrap, compose health assumptions, and log directory/write behavior.
- Logging and observability:
  - Implement centralized logging setup in core using stdlib logging with a stdout handler plus a timed rotating file handler.
  - Emit structured JSON lines containing timestamp, level, logger, event, correlation fields, and exception details.
  - Filter both sinks from the same configured log level so console and retained file logs stay consistent.
  - Compose mounts a named volume to the container log directory; README documents how to inspect and back up logs from that volume.
- Docker and compose:
  - Add a multi-stage `Dockerfile` that builds wheels in a builder stage and runs as a fixed non-root user in the runtime stage.
  - Keep the runtime container rootless.
  - Keep compose host-native by default for local M4 development; document `docker buildx build --platform linux/amd64` for deployment image builds targeting Ubuntu 22 on amd64.
  - Add `compose.yaml` with the app service and a `test` profile that starts LocalStack S3 plus a ready hook to create the test bucket.
- Git, hooks, and release automation:
  - Configure Commitizen for conventional commits, commit-msg validation, changelog generation, version bumping, tag creation, and `VERSION` updates.
  - Add pre-push hooks that run formatter checks, lint, strict type checks, type-coverage, unit/integration/e2e suites, build validation, and coverage gates.
  - Add `scripts/release.sh` to run the local semantic release flow: verify clean tree, sync tooling, run `cz bump --changelog --check-consistency --yes`, update tags, and push `HEAD` plus tags to `origin`.
  - Keep release automation local-script-driven only; no hosted CI release job is added in this bootstrap.
- Repo hygiene and docs:
  - Add `.gitignore`, `.dockerignore`, `.env.example`, `.pre-commit-config.yaml`, `README.md`, and the plans file.
  - README includes copy-paste commands for installing uv, syncing the workspace, starting the compose stack, running the health check, running all suites, and performing a release.

## Test Plan
- Unit tests:
  - Env parsing and validation for required OCI settings.
  - Endpoint construction for OCI namespace/region and explicit endpoint overrides.
  - Strictly typed boto3 adapter behavior behind mocks.
  - Logging bootstrap: stdout handler present, file handler present, JSON formatting shape, level filtering, and startup failure when the file sink is unwritable.
  - CLI success/failure paths and exit-code mapping for config, auth, connectivity, and logging failures.
- Integration tests:
  - LocalStack bucket bootstrap and readiness.
  - Real S3-compatible client operations against LocalStack for bucket head/list and object round-trip needed by the health-check path.
  - Logging sink writes JSON lines to the mounted runtime log directory and creates the expected daily file name.
- E2E tests:
  - Build the image, start the compose `test` profile, run `s3-archiver check` inside the rootless container, and assert successful exit.
  - Verify the same invocation emits expected stdout logs and persisted rotated-file logs.
  - Verify the app fails fast when the log sink cannot initialize.
- Quality gates:
  - `ruff format --check`
  - `ruff check`
  - `basedpyright`
  - `pyright --verifytypes` gate at 100 for both packages
  - `pytest` with 100% coverage fail-under
  - Source-file policy test enforcing max 300 LOC for authored package modules

## Commit Plan
- `chore(repo): bootstrap uv workspace and strict quality gates`
- `feat(core): add typed config health check and logging bootstrap`
- `feat(cli): add check command and structured output`
- `build(docker): add rootless image compose stack and log volume`
- `test(qa): add unit integration and e2e suites with coverage gates`
- `chore(release): add conventional commits hooks and semver release flow`
- `docs(readme): add quickstart release and log operations guide`

## Assumptions And Defaults
- Use one repo-wide release stream rather than independently versioned packages.
- Use LocalStack S3 for local integration and e2e testing.
- Use JSON-line logs for both stdout and retained files.
- Honor the latest explicit tooling choice to store retained logs in a Docker named volume, which relaxes the earlier request for a host `./logs` bind mount.
- Treat "100% type coverage" as two separate enforced guarantees:
  - no implicit or explicit `Any` and fully strict static typing across authored code
  - `pyright --verifytypes` completeness score of 100 for each published package API
- Treat "health-check the test suite" as part of the normal full suites rather than a separate harness command.
