# Archive safety and large weather-model datasets

Automatic source deletion defaults to `CLEANUP=false`, including `.env.example-prod`.
Manual `s3-archiver cleanup` still explicitly deletes verified sources regardless of
that setting.

## Late arrivals and reprocessing

Each new tar has a compressed JSONL membership sidecar:
`<archive-key>.members.<manifest-sha256>.jsonl.gz`. The tar's metadata references it.
Records include the original key, source namespace/bucket, version, ETag, size,
and the routing/parser identity used by the existing manifest digest.

Before overwriting an archive, the current group must contain **every exact old
member identity**. A sorted streaming comparison authenticates the old sidecar
against the digest recorded on the tar. Object counts alone never authorize a
refresh. A changed version or ETag also prevents replacement, even for the same key.
Each chunk is checked independently; a changed chunk boundary can therefore block
refresh instead of discarding members from the previous chunk.

Sidecars are published before the tar and addressed by manifest digest. A failed
publication can leave an unused sidecar, but cannot change the previous tar's
membership reference. Preserve referenced sidecars with their archives; do not
apply a lifecycle policy that deletes them first.

If cleanup already removed old source members, a late object cannot replace their
archive. The run reports a group failure, keeps the old archive, and leaves the
late source object intact. To drain those arrivals, archive them under a **new
destination prefix** while retaining the original archive. Reprocessed versions
likewise need a separate destination if the old versions must be preserved.
Legacy archives without sidecars may be reused when their manifest metadata
matches exactly, but cannot be refreshed. A sidecar is never reconstructed from
only the current source listing.

Archive and cleanup writers must continue to share the existing run lock. These
checks do not coordinate independent deployments using different lock directories.

## Integrity checks

- Source GETs use the listed version and ETag (`IfMatch`). Short and oversized
  streams fail the group before upload; mutable null versions also require ETags.
- SHA-256 is calculated over the compressed archive as it is written. Each uploaded
  part sends `Content-MD5` for provider validation. Final HEAD `ContentLength` must
  match the staged file's byte count before the archive qualifies for cleanup.
- Newly written archives record their byte count for subsequent reuse checks.
  SHA-256 metadata is a reference digest, not evidence of a later full readback.
- Cleanup manifests contain only groups/direct objects that passed copy and
  verification. A conflicting group keeps the overall run in error but does not
  prevent verified groups on that route or other routes from being cleaned.

The explicit part digest follows the [S3 UploadPart integrity contract](https://docs.aws.amazon.com/AmazonS3/latest/API/API_UploadPart.html).
Automatic botocore optional checksum behavior remains disabled for compatibility
with S3-compatible providers; explicit part digests are not disabled. Validate
provider compatibility in staging before enabling automatic cleanup.

## Capacity and performance

Membership checks stream small sidecars rather than downloading or merging old
50–100 GiB archives. Their memory usage is independent of member count. Source
manifests and verification journals use disk-backed SQLite, and tar member header
caches are discarded as members are processed.

Gzip uses level 1 for throughput on already packed weather-model data. Hashing
happens inline, eliminating a second full local archive read. File uploads use
four concurrent requests per route and a bounded part buffer. Native copies also
use four concurrent requests, with bounded submission so a failed part does not
leave thousands of queued requests. Multipart file,
streaming, and native-copy paths all calculate part sizes from object size to
stay within 10,000 parts; 100 GiB native copies are covered by range-only tests.

Provision local staging space for the **sum of active route archives**, plus
SQLite journals, sidecars, gzip/tar overhead and operating headroom. One worker
runs per route, and each route stages one tar at a time. A 20 TiB source bucket
is not staged at once. The default group limit is 100 GiB of source payload;
`ARCHIVER_MAX_DESTINATION_ARCHIVE_SIZE_MIB` also considers estimated tar overhead,
so configure the group payload limit below the destination-size limit. Use
`ARCHIVER_ARCHIVE_GROUP_MAX_BYTES` to reduce concurrent staging requirements.
Restoration requires disk for the selected uncompressed members, but does not
stage another compressed archive.

A local synthetic check of 1,000,000 members produced a 3.58 MB sidecar, built its
digest and sidecar in 12.8 seconds, and authenticated a 1,000,001-member superset
in 8.8 seconds, with 55 MB peak process RSS. This measures membership processing
with repetitive synthetic keys, not S3 throughput or a complete 20 TiB run.

## List, re-verify, and restore

Commands use the named configured route's destination credentials and accept the
full destination object key:

```bash
uv run s3-archiver archive-tools list ROUTE path/2026-05-21.tar.gz
uv run s3-archiver archive-tools verify ROUTE path/2026-05-21.tar.gz
uv run s3-archiver archive-tools extract ROUTE path/2026-05-21.tar.gz ./restored
uv run s3-archiver archive-tools extract ROUTE path/2026-05-21.tar.gz ./selected \
  --member data/model/2026-05-21T00-00-00Z.grib
```

All three commands stream the **entire archive** and compare its compressed
SHA-256 and byte count. They incur one full download even when extracting only
one member; gzip does not support efficient random member access. Listing emits
JSON lines with original keys, stored member names and sizes; only its final
`status=ok` confirms verification. Failures exit nonzero.

Extraction publishes a new output directory only after full verification. It
refuses existing output directories, links, special files and path traversal.
Keys encoded by the archiver for safe tar storage retain their encoded filenames;
the list command reports the original key alongside that filename. No restore
command writes back to S3 or deletes sources.
