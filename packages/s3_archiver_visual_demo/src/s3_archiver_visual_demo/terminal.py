"""Terminal renderer for the manual visual demo."""

from __future__ import annotations

from datetime import UTC, datetime


class VisualDemoPrinter:
    """Print sampled visual-demo walkthrough output for terminal presentation."""

    def __init__(self, archive_start_age_days: int) -> None:
        self._printer: _SampledDemoPrinter = _SampledDemoPrinter(archive_start_age_days)

    def emit(self, line: str) -> None:
        """Print one raw walkthrough line in stakeholder-friendly form."""

        self._printer.print_line(line)

    def finish(self) -> None:
        """Flush any sampled output rows."""

        self._printer.finish()


def print_image_build_intro() -> None:
    """Print the runtime image build section."""

    _print_demo_header("Preparing the runtime image")
    print("  Building the app image quietly. Build logs are shown only if the build fails.")


def build_failure_message(stdout: str, stderr: str) -> str:
    """Return a readable app image build failure message."""

    return "\n".join(
        (
            "failed to build the app image",
            f"stdout:\n{stdout}",
            f"stderr:\n{stderr}",
        )
    )


def print_demo_intro(*, seeded_count: int) -> None:
    """Print the compose-backed demo section."""

    _print_demo_header("Running the compose-backed demo")
    print("  LocalStack has fresh source and destination buckets for this test run.")
    print(
        "  Seeded "
        + f"{seeded_count} source objects: "
        + "valid, invalid, and unsafe-key timestamp examples."
    )
    print("  Archive selection is configured by the app route parser.")
    print("  The next lines are live output from the manual demo CLI, with JSON hidden.")
    print()


class _SampledDemoPrinter:
    def __init__(self, archive_start_age_days: int) -> None:
        self.archive_start_age_days: int = archive_start_age_days
        self.object_count: int = 0
        self.tail: list[str] = []

    def print_line(self, line: str) -> None:
        if line.startswith(("SOURCE ", "DEST   ", "COPY   ", "DELETE ", "GROUP  ", "DIRECT ")):
            formatted = _friendly_demo_line(line)
            self.object_count += 1
            if self.object_count <= 3:
                print(f"  {formatted}")
                return
            self.tail = [*self.tail[-2:], formatted]
            return
        self.finish()
        _print_visual_demo_line(line)

    def finish(self) -> None:
        if self.object_count > 3:
            omitted = self.object_count - 6
            if omitted > 0:
                print(f"  ... {omitted} rows omitted; showing the last 3 rows ...")
            for line in self.tail:
                print(f"  {line}")
        self.object_count = 0
        self.tail = []


def _print_visual_demo_line(line: str) -> None:
    stripped = line.strip()
    if not stripped:
        print()
        return
    if (
        stripped.startswith("{")
        or stripped == "Demo summary JSON follows on the next line."
        or stripped.startswith("Container ")
        or stripped.startswith("Volume ")
    ):
        return
    match line:
        case "== S3 Archiver Visual Demo ==":
            _print_demo_header("S3 Archiver visual demo")
            print("  This is a real Docker Compose run against LocalStack S3.")
        case "== Preflight ==":
            _print_step("1/4", "Preflight checks")
            print("  Confirming configuration, logging, and bucket access before archiving.")
        case "== Before archive ==":
            _print_step("2/4", "Starting bucket state")
            print("  s3 ls-style view before archive: timestamped source; empty destination.")
        case "== Archive Candidates ==":
            _print_step("3/4", "Archive selection")
            print("  Applying configured route selection and grouping by each data day.")
            print(
                "  The seed includes flat filenames, YYYY/MM/DD folders, compact dates, "
                + "offsets, Z suffixes, and newer unarchived objects."
            )
        case "Running archive workflow against the configured buckets...":
            _print_step("4/4", "Archive execution")
            print("  The app is listing, copying, and verifying archive output.")
        case "== Archive Result ==":
            print()
            print("  Archive phase results")
        case "== After archive ==":
            print()
            print("  s3 ls-style view after archive")
        case _:
            print(f"  {_friendly_demo_line(line)}")


def _friendly_demo_line(line: str) -> str:
    object_line_prefixes = {
        "SOURCE ": "source",
        "DEST   ": "dest",
        "COPY   ": "copy",
        "DELETE ": "delete",
    }
    for prefix, label in object_line_prefixes.items():
        if line.startswith(prefix):
            return _s3_ls_style_line(label, line[len(prefix) :])
    return line


def _s3_ls_style_line(label: str, fields: str) -> str:
    key = _field(fields, "key")
    size = _field(fields, "size")
    last_modified = _field(fields, "last_modified")
    if key is None or size is None or last_modified is None:
        return f"{label:<6} | {fields}"
    timestamp = datetime.fromisoformat(last_modified).astimezone(UTC)
    row = f"{label:<6} | {timestamp:%Y-%m-%d %H:%M:%S} {int(size):>10} {key}"
    details: list[str] = []
    eligible = _field(fields, "eligible")
    if eligible is not None:
        details.append(f"archive_candidate={eligible.lower()}")
    present_in_destination = _field(fields, "present_in_destination")
    if present_in_destination is not None:
        details.append(f"in_destination={present_in_destination.lower()}")
    for name in ("version_id", "source_last_modified"):
        value = _field(fields, name)
        if value is not None:
            details.append(f"{name}={value}")
    if details:
        return f"{row} | {', '.join(details)}"
    return row


def _field(fields: str, name: str) -> str | None:
    prefix = f"{name}="
    for part in fields.split():
        if part.startswith(prefix):
            return part.removeprefix(prefix)
    return None


def _print_demo_header(title: str) -> None:
    print()
    print("=" * 78)
    print(title.upper())
    print("=" * 78)


def _print_step(number: str, title: str) -> None:
    print()
    print(f"[{number}] {title}")
    print("-" * (len(number) + len(title) + 4))
