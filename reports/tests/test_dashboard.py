from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

from django.contrib.auth import get_user_model
from django.test import TestCase

from reports.models import Record, Report
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
