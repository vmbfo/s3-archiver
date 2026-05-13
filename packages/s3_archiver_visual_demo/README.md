# s3-archiver visual demo

Manual, compose-backed stakeholder demo for `s3-archiver`.

Run from the repository root:

```sh
./scripts/run_visual_demo.sh
```

This is a manual CLI package, not a regular e2e test. It starts LocalStack with Docker
Compose, seeds a stakeholder-sized source bucket, runs the app container's normal
`s3-archiver archive` command, verifies the result, and prints a presentation-friendly
summary.
