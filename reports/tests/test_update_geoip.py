import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from reports.management.commands.update_geoip import (
    _database_specs,
    _release_candidates,
    _release_url,
    _update_due,
)


def reader_context(database_type):
    reader = MagicMock()
    reader.__enter__.return_value.metadata.return_value = SimpleNamespace(
        database_type=database_type,
        build_epoch=int(datetime(2026, 10, 1, tzinfo=UTC).timestamp()),
    )
    return reader


class GeoIpUpdateScheduleTests(SimpleTestCase):
    def test_release_candidates_include_previous_month_across_year_boundary(self):
        self.assertEqual(
            _release_candidates(datetime(2027, 1, 2, tzinfo=UTC)),
            ((2027, 1), (2026, 12)),
        )

    def test_release_url_uses_provider_monthly_snapshot_format(self):
        self.assertEqual(
            _release_url("dbip-city-lite", 2026, 11),
            "https://download.db-ip.com/free/dbip-city-lite-2026-11.mmdb.gz",
        )

    def test_updater_skips_recent_checks_but_runs_when_database_is_missing(self):
        now = datetime(2026, 11, 3, tzinfo=UTC)
        with TemporaryDirectory() as directory:
            city_path = Path(directory) / "city.mmdb"
            asn_path = Path(directory) / "asn.mmdb"
            marker = Path(directory) / ".dbip-last-check"
            specs = (
                ("city", "city", city_path, "DBIP-City-Lite"),
                ("asn", "asn", asn_path, "DBIP-ASN-Lite"),
            )
            self.assertTrue(_update_due(specs, marker, now))
            city_path.touch()
            asn_path.touch()
            marker.touch()
            os.utime(marker, (now.timestamp(), now.timestamp()))
            with patch("reports.management.commands.update_geoip.geoip2.database.Reader") as reader:
                reader.side_effect = [
                    reader_context("DBIP-City-Lite"),
                    reader_context("DBIP-ASN-Lite"),
                ]
                self.assertFalse(_update_due(specs, marker, now))
                self.assertEqual(reader.call_count, 2)

    def test_updater_checks_after_25_days(self):
        now = datetime(2026, 12, 3, tzinfo=UTC)
        with TemporaryDirectory() as directory:
            city_path = Path(directory) / "city.mmdb"
            asn_path = Path(directory) / "asn.mmdb"
            marker = Path(directory) / ".dbip-last-check"
            for path in (city_path, asn_path, marker):
                path.touch()
            old_timestamp = (now - timedelta(days=26)).timestamp()
            for path in (city_path, asn_path, marker):
                os.utime(path, (old_timestamp, old_timestamp))
            specs = (
                ("city", "city", city_path, "DBIP-City-Lite"),
                ("asn", "asn", asn_path, "DBIP-ASN-Lite"),
            )
            with patch("reports.management.commands.update_geoip.geoip2.database.Reader") as reader:
                reader.side_effect = [
                    reader_context("DBIP-City-Lite"),
                    reader_context("DBIP-ASN-Lite"),
                ]
                self.assertTrue(_update_due(specs, marker, now))

    def test_database_paths_follow_runtime_settings(self):
        with (
            patch(
                "reports.management.commands.update_geoip.settings.GEOIP_CITY_DB",
                Path("/tmp/city.mmdb"),
            ),
            patch(
                "reports.management.commands.update_geoip.settings.GEOIP_ASN_DB",
                Path("/tmp/asn.mmdb"),
            ),
        ):
            specs = _database_specs()

        self.assertEqual(specs[0][2], Path("/tmp/city.mmdb"))
        self.assertEqual(specs[1][2], Path("/tmp/asn.mmdb"))
