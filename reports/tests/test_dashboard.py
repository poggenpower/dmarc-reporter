from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from reports.admin_users import ADMIN_GROUP_NAME, is_admin_user
from reports.models import ImportedMessage, Record, Report
from reports.parser import parse_report_xml

FIXTURE = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "report_xml.xml"


class DashboardTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        parsed = parse_report_xml(FIXTURE.read_bytes())
        report = Report.objects.create(
            reporter=parsed.reporter,
            report_id=parsed.report_id,
            domain=parsed.domain,
            begin=parsed.begin,
            end=parsed.end,
            policy_disposition=parsed.policy_disposition,
            policy_pct=parsed.policy_pct,
            policy_adkim=parsed.policy_adkim,
            policy_aspf=parsed.policy_aspf,
        )
        for record in parsed.records:
            Record.objects.create(
                report=report,
                source_ip=record.source_ip,
                count=record.count,
                disposition=record.disposition,
                dkim_aligned=record.dkim_aligned,
                spf_aligned=record.spf_aligned,
                header_from=record.header_from,
                envelope_from=record.envelope_from,
                dkim_domain=record.dkim_domain,
                dkim_result=record.dkim_result,
                spf_domain=record.spf_domain,
                spf_result=record.spf_result,
            )
        other_report = Report.objects.create(
            reporter="Another Example Reporter",
            report_id="other-fixture",
            domain="other.example",
            begin=parsed.begin + timedelta(days=2),
            end=parsed.end + timedelta(days=2),
        )
        Record.objects.create(
            report=other_report,
            source_ip="203.0.113.99",
            count=13,
            disposition="reject",
            dkim_aligned="fail",
            spf_aligned="fail",
            header_from="other.example",
        )
        Record.objects.create(
            report=other_report,
            source_ip="203.0.113.77",
            count=7,
            disposition="quarantine",
            dkim_aligned="fail",
            spf_aligned="fail",
            header_from="other.example",
            spf_result="pass",
        )
        cls.user = get_user_model().objects.create_user(
            username="report-viewer",
            password="Correct-horse-battery-42",
        )

    def test_dashboard_requires_login(self):
        response = self.client.get("/dashboard/")

        self.assertRedirects(response, "/accounts/login/?next=/dashboard/")

    def test_first_created_user_is_added_to_admin_group(self):
        self.assertTrue(self.user.is_superuser)
        self.assertTrue(self.user.groups.filter(name=ADMIN_GROUP_NAME).exists())
        self.assertTrue(is_admin_user(self.user))

    def test_ingest_history_requires_login(self):
        response = self.client.get("/ingest/")

        self.assertRedirects(response, "/accounts/login/?next=/ingest/")

    def test_ingest_history_lists_email_metadata_and_latest_ingest_time(self):
        imported_message = ImportedMessage.objects.create(
            folder="INBOX",
            uidvalidity="1234",
            uid=7,
            subject="Example DMARC report",
            sender="reports@example.net",
            sent_at="2025-11-07T12:00:00Z",
            status="processed",
            report_count=1,
        )
        self.client.force_login(self.user)

        response = self.client.get("/ingest/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Ingested emails")
        self.assertContains(response, "Example DMARC report")
        self.assertContains(response, "reports@example.net")
        self.assertContains(response, "Last ingest:")
        self.assertContains(response, f'id="email-{imported_message.pk}"')

    def test_admin_can_backfill_email_metadata_from_ingest_history(self):
        imported_message = ImportedMessage.objects.create(
            folder="INBOX",
            uidvalidity="1234",
            uid=7,
            status="processed",
            report_count=1,
        )
        self.client.force_login(self.user)

        with patch("reports.views.backfill_imported_messages", return_value=(1, [])) as backfill:
            response = self.client.post(
                "/ingest/",
                {"action": "backfill_selected", "email_ids": [imported_message.pk]},
            )

        self.assertRedirects(response, "/ingest/")
        backfill.assert_called_once()
        self.assertEqual(list(backfill.call_args.args[0]), [imported_message])

    def test_admin_can_bulk_backfill_all_needing_backfill_in_view(self):
        needs_backfill = ImportedMessage.objects.create(
            folder="INBOX", uidvalidity="1234", uid=8, status="processed", report_count=1
        )
        ImportedMessage.objects.create(
            folder="INBOX",
            uidvalidity="1234",
            uid=9,
            status="processed",
            subject="Complete",
            sender="reports@example.net",
            sent_at=timezone.now(),
        )
        self.client.force_login(self.user)

        with patch("reports.views.backfill_imported_messages", return_value=(0, [])) as backfill:
            response = self.client.post(
                "/ingest/",
                {"action": "backfill_all", "filter": "needs_backfill", "q": "", "page": "1"},
            )

        self.assertRedirects(response, "/ingest/?filter=needs_backfill&page=1")
        self.assertEqual(list(backfill.call_args.args[0]), [needs_backfill])

    def test_ingest_list_supports_quick_filters_and_last_page_link(self):
        for uid in range(1, 61):
            ImportedMessage.objects.create(
                folder="INBOX",
                uidvalidity="1234",
                uid=uid,
                status="error" if uid == 1 else "processed",
                subject=f"Subject {uid}",
                sender="reports@example.net",
                sent_at=timezone.now(),
            )
        self.client.force_login(self.user)

        response = self.client.get("/ingest/", {"filter": "error"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["page"].paginator.count, 1)

        response = self.client.get("/ingest/")
        self.assertContains(response, "?page=2")
        self.assertContains(response, "Last")
        self.assertEqual(response.context["page"].paginator.num_pages, 2)
        self.assertEqual(response.context["page"].object_list[0].uid, 60)

    def test_non_admin_cannot_backfill_email_metadata(self):
        imported_message = ImportedMessage.objects.create(
            folder="INBOX",
            uidvalidity="1234",
            uid=7,
            status="processed",
        )
        user = get_user_model().objects.create_user(username="regular-user")
        self.assertFalse(user.is_staff)
        self.assertFalse(is_admin_user(user))
        self.client.force_login(user)

        with patch("reports.views.backfill_imported_messages") as backfill:
            response = self.client.post(
                "/ingest/",
                {"action": "backfill_selected", "email_ids": [imported_message.pk]},
            )

        self.assertEqual(response.status_code, 403)
        backfill.assert_not_called()

    def test_dashboard_links_records_to_the_source_email(self):
        source_email = ImportedMessage.objects.create(
            folder="INBOX",
            uidvalidity="1234",
            uid=8,
            subject="Traceable DMARC report",
            sender="reports@example.net",
            status="processed",
            report_count=1,
        )
        report = Report.objects.get(domain="other.example")
        report.source_email = source_email
        report.save(update_fields=["source_email"])
        self.client.force_login(self.user)

        response = self.client.get("/dashboard/")

        self.assertContains(response, 'aria-label="Open source email: Traceable DMARC report"')
        self.assertContains(response, f"/ingest/#email-{source_email.pk}")

    def test_dashboard_shows_filtered_report_after_login(self):
        self.client.force_login(self.user)

        response = self.client.get(
            "/dashboard/",
            {"domain": "example.com"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "DMARC dashboard")
        self.assertContains(response, "example.com")
        self.assertContains(response, "192.0.2.1")
        self.assertContains(response, "2025-01-01")
        self.assertEqual(
            response.context["message_count"],
            sum(record.count for record in parse_report_xml(FIXTURE.read_bytes()).records),
        )
        self.assertContains(response, "Destination / reporter")

    def test_dashboard_source_ip_filter_excludes_other_domains(self):
        self.client.force_login(self.user)

        response = self.client.get("/dashboard/", {"source_ip": "203.0.113.99"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["message_count"], 13)
        self.assertEqual(response.context["report_count"], 1)

    def test_dashboard_shows_source_ip_attribution_and_location(self):
        self.client.force_login(self.user)
        Record.objects.filter(source_ip="203.0.113.99").update(
            source_hostname="mail.example.net",
            source_asn=64500,
            source_organization="Example Network",
            source_country_code="US",
            source_country="United States",
            source_city="Example City",
        )

        with TemporaryDirectory() as database_directory:
            city_database = Path(database_directory) / "GeoLite2-City.mmdb"
            city_database.touch()
            with self.settings(GEOIP_CITY_DB=city_database):
                response = self.client.get("/dashboard/")

        self.assertContains(response, "mail.example.net")
        self.assertContains(response, "Example Network")
        self.assertContains(response, 'data-asn="64500"')
        self.assertContains(response, 'data-city="Example City"')
        self.assertContains(response, 'data-country="United States"')
        self.assertContains(
            response,
            '<span class="country-flag" aria-hidden="true">🇺🇸</span>',
        )

    def test_top_sources_show_attribution_but_filter_by_ip(self):
        self.client.force_login(self.user)
        Record.objects.filter(source_ip="203.0.113.99").update(
            source_hostname="mail.example.net",
            source_asn=64500,
            source_organization="Example Network",
            source_country_code="US",
            source_country="United States",
            source_city="Example City",
        )

        response = self.client.get("/dashboard/")

        self.assertContains(response, "mail.example.net")
        self.assertContains(response, 'data-ip="203.0.113.99"')
        self.assertContains(response, 'data-asn="64500"')
        self.assertContains(response, 'data-city="Example City"')
        source = next(bar for bar in response.context["source_bars"] if bar["ip"] == "203.0.113.99")
        self.assertEqual(source["label"], "mail.example.net")
        self.assertContains(
            response,
            "?source_ip=203.0.113.99&amp;non_aligned=on&amp;exclude_spf_pass=on",
        )

    def test_dashboard_defaults_to_non_aligned_records_and_excludes_spf_pass(self):
        self.client.force_login(self.user)

        response = self.client.get("/dashboard/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["page"].paginator.count, 2)
        source_ips = {record.source_ip for record in response.context["page"].object_list}
        self.assertEqual(source_ips, {"203.0.113.77", "203.0.113.99"})
        self.assertTrue(response.context["form"]["non_aligned"].value())
        self.assertTrue(response.context["form"]["exclude_spf_pass"].value())

    def test_chart_links_apply_values_and_preserve_default_filters(self):
        self.client.force_login(self.user)

        response = self.client.get("/dashboard/")

        self.assertContains(
            response,
            "?domain=other.example&amp;non_aligned=on&amp;exclude_spf_pass=on",
        )
        self.assertContains(
            response,
            "?disposition=reject&amp;non_aligned=on&amp;exclude_spf_pass=on",
        )
        self.assertContains(
            response,
            "?reporter=Another+Example+Reporter&amp;non_aligned=on&amp;exclude_spf_pass=on",
        )

    def test_dashboard_can_include_aligned_and_spf_passing_records(self):
        self.client.force_login(self.user)

        response = self.client.get(
            "/dashboard/",
            {"begin": "2024-01-01"},
        )

        self.assertEqual(response.context["page"].paginator.count, 3)
        self.assertFalse(response.context["form"]["non_aligned"].value())
        self.assertFalse(response.context["form"]["exclude_spf_pass"].value())

    def test_dashboard_excludes_spf_pass_only_for_fully_aligned_none_verdicts(self):
        self.client.force_login(self.user)
        report = Report.objects.get(report_id="other-fixture")
        Record.objects.create(
            report=report,
            source_ip="203.0.113.88",
            count=3,
            disposition="none",
            dkim_aligned="pass",
            spf_aligned="pass",
            spf_result="pass",
        )
        Record.objects.create(
            report=report,
            source_ip="203.0.113.89",
            count=5,
            disposition="reject",
            dkim_aligned="pass",
            spf_aligned="pass",
            spf_result="pass",
        )

        response = self.client.get(
            "/dashboard/",
            {"exclude_spf_pass": "on"},
        )

        source_ips = {record.source_ip for record in response.context["page"].object_list}
        self.assertNotIn("203.0.113.88", source_ips)
        self.assertIn("203.0.113.89", source_ips)
        self.assertIn("203.0.113.77", source_ips)

    def test_dashboard_shows_first_and_latest_record_dates(self):
        self.client.force_login(self.user)

        response = self.client.get("/dashboard/")

        self.assertEqual(response.context["first_record"].date().isoformat(), "2025-01-01")
        self.assertEqual(response.context["latest_record"].date().isoformat(), "2025-01-04")
        self.assertContains(response, "First record")
        self.assertContains(response, "Latest record")

    def test_dashboard_groups_charts_by_reporter_destination(self):
        self.client.force_login(self.user)

        response = self.client.get(
            "/dashboard/",
            {"begin": "2024-01-01"},
        )

        reporters = response.context["reporters_chart"]["segments"]
        self.assertEqual(len(reporters), 2)
        self.assertContains(response, "By destination")
        self.assertContains(response, "Example Reporting Service")
        self.assertContains(response, "Another Example Reporter")

    def test_chart_value_link_filters_by_reporter_and_keeps_default_filters(self):
        self.client.force_login(self.user)

        response = self.client.get(
            "/dashboard/",
            {
                "reporter": "Another Example Reporter",
                "non_aligned": "on",
                "exclude_spf_pass": "on",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["page"].paginator.count, 2)
        source_ips = {record.source_ip for record in response.context["page"].object_list}
        self.assertEqual(source_ips, {"203.0.113.77", "203.0.113.99"})
        self.assertContains(response, "Select a destination to filter")

    def test_dashboard_validates_source_ip_filter(self):
        self.client.force_login(self.user)

        response = self.client.get("/dashboard/", {"source_ip": "not-an-ip"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Please correct the highlighted filter values.")

    def test_health_endpoint_is_available_without_login(self):
        response = self.client.get("/healthz/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ok")

    def test_logout_requires_post(self):
        self.client.force_login(self.user)

        response = self.client.get("/accounts/logout/")

        self.assertEqual(response.status_code, 405)
