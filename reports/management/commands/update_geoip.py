import gzip
import os
import stat
import tempfile
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import geoip2.database
from django.conf import settings
from django.core.management import BaseCommand, CommandError, call_command
from maxminddb.errors import InvalidDatabaseError

CHECK_INTERVAL = timedelta(days=25)
WATCH_INTERVAL_SECONDS = 24 * 60 * 60
MAX_COMPRESSED_BYTES = 128 * 1024 * 1024
MAX_DATABASE_BYTES = 512 * 1024 * 1024
DOWNLOAD_BASE = "https://download.db-ip.com/free"
DATABASES = (
    ("city", "dbip-city-lite", "GEOIP_CITY_DB", "DBIP-City-Lite"),
    ("asn", "dbip-asn-lite", "GEOIP_ASN_DB", "DBIP-ASN-Lite"),
)


def _month_offset(year, month, offset):
    month_index = year * 12 + month - 1 + offset
    return month_index // 12, month_index % 12 + 1


def _release_candidates(now):
    current = (now.year, now.month)
    previous = _month_offset(now.year, now.month, -1)
    return (current, previous)


def _release_url(database_name, year, month):
    return f"{DOWNLOAD_BASE}/{database_name}-{year}-{month:02d}.mmdb.gz"


def _database_specs():
    return tuple(
        (label, name, Path(getattr(settings, setting_name)), expected_type)
        for label, name, setting_name, expected_type in DATABASES
    )


def _installed_release_month(database_specs):
    releases = []
    try:
        for _, _, path, expected_type in database_specs:
            if not path.is_file():
                return None
            with geoip2.database.Reader(str(path)) as reader:
                metadata = reader.metadata()
            if not metadata.database_type.startswith(expected_type):
                return None
            built_at = datetime.fromtimestamp(metadata.build_epoch, UTC)
            releases.append((built_at.year, built_at.month))
    except (InvalidDatabaseError, OSError):
        return None
    return releases[0] if len(set(releases)) == 1 else ()


def _update_due(database_specs, marker_path, now):
    installed_release = _installed_release_month(database_specs)
    if installed_release is None:
        return True
    if installed_release == (now.year, now.month):
        return False
    if marker_path.exists():
        checked_at = datetime.fromtimestamp(marker_path.stat().st_mtime, UTC)
        if now - checked_at < CHECK_INTERVAL:
            return False
    if any(not path.is_file() for _, _, path, _ in database_specs):
        return True
    oldest_mtime = min(path.stat().st_mtime for _, _, path, _ in database_specs)
    return not oldest_mtime or now.timestamp() - oldest_mtime >= CHECK_INTERVAL.total_seconds()


def _download_database(url, directory, expected_database_type):
    compressed_path = None
    database_path = None
    validated = False
    try:
        with tempfile.NamedTemporaryFile(dir=directory, delete=False) as compressed:
            compressed_path = Path(compressed.name)
            request = Request(url, headers={"User-Agent": "DMARC-Reporter GeoIP updater"})
            with urlopen(request, timeout=60) as response:
                total = 0
                while chunk := response.read(1024 * 1024):
                    total += len(chunk)
                    if total > MAX_COMPRESSED_BYTES:
                        raise CommandError("DB-IP download exceeded the compressed size limit.")
                    compressed.write(chunk)

        with tempfile.NamedTemporaryFile(dir=directory, delete=False) as database_file:
            database_path = Path(database_file.name)
        total = 0
        with gzip.open(compressed_path, "rb") as source, database_path.open("wb") as target:
            while chunk := source.read(1024 * 1024):
                total += len(chunk)
                if total > MAX_DATABASE_BYTES:
                    raise CommandError("DB-IP database exceeded the expanded size limit.")
                target.write(chunk)

        with geoip2.database.Reader(str(database_path)) as reader:
            database_type = reader.metadata().database_type
        if not database_type.startswith(expected_database_type):
            raise CommandError(f"Unexpected DB-IP database type: {database_type}.")
        os.chmod(database_path, stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP | stat.S_IROTH)
        validated = True
        return database_path
    except HTTPError as exc:
        if exc.code == 404:
            raise FileNotFoundError(url) from exc
        raise CommandError(f"DB-IP download failed with HTTP {exc.code}.") from exc
    except CommandError:
        raise
    except (
        URLError,
        TimeoutError,
        OSError,
        EOFError,
        gzip.BadGzipFile,
        InvalidDatabaseError,
    ) as exc:
        raise CommandError(
            f"Could not download or validate DB-IP data ({type(exc).__name__})."
        ) from exc
    finally:
        if compressed_path is not None:
            compressed_path.unlink(missing_ok=True)
        if database_path is not None and not validated:
            database_path.unlink(missing_ok=True)


class Command(BaseCommand):
    help = "Refresh DB-IP Lite databases monthly and update stored source attribution."

    def add_arguments(self, parser):
        parser.add_argument(
            "--force",
            action="store_true",
            help="Check for a newer monthly database regardless of file age.",
        )
        parser.add_argument(
            "--watch",
            action="store_true",
            help="Check daily and keep running as a scheduled updater.",
        )

    def handle(self, *args, **options):
        while True:
            try:
                self.update_databases(force=options["force"])
            except CommandError as exc:
                if not options["watch"]:
                    raise
                self.stderr.write(f"DB-IP update failed: {exc}; retrying tomorrow.")
            if not options["watch"]:
                return
            time.sleep(WATCH_INTERVAL_SECONDS)

    def update_databases(self, *, force=False):
        city_path = Path(settings.GEOIP_CITY_DB)
        database_specs = _database_specs()
        database_paths = tuple(spec[2] for spec in database_specs)
        now = datetime.now(UTC)
        for path in database_paths:
            path.parent.mkdir(parents=True, exist_ok=True)

        check_marker = city_path.parent / ".dbip-last-check"
        marker_path = city_path.parent / ".dbip-enrichment-pending"
        if marker_path.exists():
            self.stdout.write("Resuming pending source attribution refresh.")
            call_command("enrich_source_ips", stdout=self.stdout, stderr=self.stderr)
            marker_path.unlink()
            return
        if not force and not _update_due(database_specs, check_marker, now):
            self.stdout.write(
                "DB-IP databases are current or were checked within the last 25 days."
            )
            return

        for year, month in _release_candidates(now):
            installed_release = _installed_release_month(database_specs)
            if installed_release == (year, month):
                check_marker.touch()
                self.stdout.write(f"DB-IP {year}-{month:02d} is already installed.")
                return

            staged = []
            try:
                for _, name, path, database_type in database_specs:
                    staged.append(
                        (
                            path,
                            _download_database(
                                _release_url(name, year, month),
                                path.parent,
                                database_type,
                            ),
                        )
                    )
            except FileNotFoundError:
                for _, temporary_path in staged:
                    temporary_path.unlink(missing_ok=True)
                self.stdout.write(
                    f"DB-IP {year}-{month:02d} release is not available; trying the previous month."
                )
                continue
            except CommandError:
                for _, temporary_path in staged:
                    temporary_path.unlink(missing_ok=True)
                raise

            for path, temporary_path in staged:
                os.replace(temporary_path, path)
            check_marker.touch()
            marker_path.touch()
            call_command("enrich_source_ips", stdout=self.stdout, stderr=self.stderr)
            marker_path.unlink()
            self.stdout.write(f"Installed DB-IP {year}-{month:02d} City and ASN databases.")
            return

        if _installed_release_month(database_specs) == _month_offset(now.year, now.month, -1):
            check_marker.touch()
            self.stdout.write("The previous month's DB-IP release is the latest available.")
            return
        raise CommandError("No current or previous monthly DB-IP release is available.")
