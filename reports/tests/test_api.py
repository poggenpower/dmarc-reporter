from datetime import UTC, date, datetime
from smtplib import SMTPAuthenticationError
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core import mail
from django.test import Client, TestCase

from dmarc_reporter.settings import _smtp_transport_options
from reports.models import ApiToken, Record, Report


class SmtpTransportOptionsTests(TestCase):
    def test_tls_modes(self):
        self.assertEqual(_smtp_transport_options("true"), (True, False))
        self.assertEqual(_smtp_transport_options("starttls"), (False, True))
        self.assertEqual(_smtp_transport_options(""), (False, False))
        self.assertEqual(_smtp_transport_options("false"), (False, False))
        self.assertEqual(_smtp_transport_options("ssl"), (False, False))


class ReportApiTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="api-user",
            password="test-password",
            email="reports@example.com",
        )
        self.permission = Permission.objects.get(
            content_type__app_label="reports",
            codename="api_request_report",
        )
        self.token, self.raw_token = self.create_token(grant_permission=True)
        self.first_report = self.create_report(
            "example.com", "First Destination", datetime(2025, 1, 2, tzinfo=UTC)
        )
        self.create_record(
            self.first_report,
            "192.0.2.1",
            10,
            disposition="reject",
            dkim_aligned="fail",
            spf_aligned="fail",
        )
        Record.objects.filter(source_ip="192.0.2.1").update(source_country_code="US")
        self.create_record(
            self.first_report,
            "192.0.2.2",
            20,
            disposition="none",
            dkim_aligned="pass",
            spf_aligned="pass",
            spf_result="pass",
        )
        second_report = self.create_report(
            "other.example",
            "Second Destination",
            datetime(2025, 1, 3, tzinfo=UTC),
        )
        self.create_record(
            second_report,
            "192.0.2.3",
            5,
            disposition="quarantine",
            dkim_aligned="fail",
            spf_aligned="fail",
        )

    def create_token(self, *, grant_permission):
        raw_token = "test_" + str(ApiToken.objects.count())
        token = ApiToken.objects.create(
            user=self.user,
            name="test token",
            token_hash=ApiToken.hash_token(raw_token),
        )
        if grant_permission:
            token.permissions.add(self.permission)
        return token, raw_token

    @staticmethod
    def create_report(domain, reporter, begin):
        return Report.objects.create(
            reporter=reporter,
            report_id=f"{domain}-{reporter}",
            domain=domain,
            begin=begin,
            end=begin.replace(hour=1),
        )

    @staticmethod
    def create_record(
        report,
        source_ip,
        count,
        *,
        disposition,
        dkim_aligned,
        spf_aligned,
        spf_result="fail",
    ):
        return Record.objects.create(
            report=report,
            source_ip=source_ip,
            count=count,
            disposition=disposition,
            dkim_aligned=dkim_aligned,
            spf_aligned=spf_aligned,
            spf_result=spf_result,
        )

    def api_request(self, payload, token=None):
        return self.client.post(
            "/api/v1/reports/",
            data=payload,
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {token or self.raw_token}",
        )

    def test_api_requires_token_and_permission(self):
        response = self.client.post("/api/v1/reports/", data="{}", content_type="application/json")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response["WWW-Authenticate"], "Bearer")

        unprivileged_token, raw_token = self.create_token(grant_permission=False)
        response = self.api_request({}, token=raw_token)

        self.assertEqual(response.status_code, 403)
        self.assertIsNone(unprivileged_token.last_used_at)

    def test_bearer_authenticated_api_does_not_require_session_csrf(self):
        csrf_checked_client = Client(enforce_csrf_checks=True)
        with self.settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend"):
            response = csrf_checked_client.post(
                "/api/v1/reports/",
                data="{}",
                content_type="application/json",
                HTTP_AUTHORIZATION=f"Bearer {self.raw_token}",
            )

        self.assertEqual(response.status_code, 200, response.content)

    def test_report_email_has_unfiltered_domain_destination_and_violation_summaries(self):
        with self.settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend"):
            response = self.api_request({"start_date": "2025-01-01", "end_date": "2025-01-07"})

        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["message_count"], 35)
        self.assertEqual(response.json()["violation_count"], 15)
        self.assertEqual(set(response.json()["domains"]), {"example.com", "other.example"})
        self.assertEqual(response.json()["recipient"], self.user.email)
        self.assertEqual(self.token.token_hash, ApiToken.hash_token(self.raw_token))
        self.assertEqual(mail.outbox[0].to, [self.user.email])
        html_body = mail.outbox[0].alternatives[0][0]
        self.assertIn("By domain", html_body)
        self.assertIn("By destination", html_body)
        self.assertIn("First Destination", html_body)
        self.assertIn("Second Destination", html_body)
        self.assertIn("Top policy-violating sources", html_body)
        self.assertIn("192.0.2.1", html_body)
        self.assertIn("conic-gradient", html_body)
        self.assertIn("<th>Country code</th>", html_body)
        self.assertIn("US", html_body)
        self.assertIn('style="max-height:420px; overflow-y:auto; overflow-x:hidden;', html_body)
        self.assertIn("Authentication details", html_body)
        self.assertEqual(html_body.count('class="chart-legend"'), 3)
        self.assertEqual(html_body.count('class="chart-breakdown"'), 3)
        self.assertLess(
            html_body.index('class="chart-legend"'),
            html_body.index('class="chart-breakdown"'),
        )
        self.assertIn(".summary-cell", html_body)
        self.assertIn("Report records", html_body)
        self.assertIn("192.0.2.2", html_body)
        self.assertIn("DKIM:", html_body)
        self.assertIn("SPF:", html_body)
        self.assertIsNotNone(self.token.__class__.objects.get(pk=self.token.pk).last_used_at)

    def test_domain_selection_filters_unfiltered_totals(self):
        with self.settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend"):
            response = self.api_request(
                {
                    "start_date": "2025-01-01",
                    "end_date": "2025-01-07",
                    "domains": ["example.com"],
                }
            )

        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["message_count"], 30)
        self.assertEqual(response.json()["violation_count"], 10)
        self.assertEqual(response.json()["domains"], ["example.com"])
        self.assertNotIn("Second Destination", mail.outbox[0].alternatives[0][0])

    def test_default_period_is_seven_days_and_all_domains(self):
        outside_report = self.create_report(
            "outside.example",
            "Outside Destination",
            datetime(2024, 12, 31, tzinfo=UTC),
        )
        self.create_record(
            outside_report,
            "192.0.2.4",
            100,
            disposition="reject",
            dkim_aligned="fail",
            spf_aligned="fail",
        )

        with (
            patch("reports.api.timezone.localdate", return_value=date(2025, 1, 7)),
            self.settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend"),
        ):
            response = self.api_request({})

        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["start_date"], "2025-01-01")
        self.assertEqual(response.json()["end_date"], "2025-01-07")
        self.assertEqual(response.json()["message_count"], 35)
        self.assertNotIn("outside.example", mail.outbox[0].alternatives[0][0])

    def test_invalid_requests_do_not_send_email(self):
        for payload in (
            {"start_date": "not-a-date"},
            {"start_date": "2025-01-08", "end_date": "2025-01-07"},
            {"domains": "example.com"},
            {"domains": ["missing.example"]},
        ):
            with self.subTest(payload=payload):
                response = self.api_request(payload)
                self.assertEqual(response.status_code, 400)
        self.assertEqual(mail.outbox, [])

    def test_missing_email_and_smtp_failure_are_reported(self):
        self.user.email = ""
        self.user.save(update_fields=["email"])
        response = self.api_request({})
        self.assertEqual(response.status_code, 400)

        self.user.email = "reports@example.com"
        self.user.save(update_fields=["email"])
        with (
            patch(
                "reports.api.EmailMultiAlternatives.send",
                side_effect=SMTPAuthenticationError(535, b"Authentication credentials invalid"),
            ),
            self.assertLogs("reports.api", level="ERROR") as captured,
        ):
            response = self.api_request({})
        self.assertEqual(response.status_code, 502)
        self.assertIn("535", response.json()["error"])
        self.assertIn("Authentication credentials invalid", response.json()["error"])
        self.assertIn("Check the application log", response.json()["error"])
        self.assertIn("Could not send", captured.output[0])

    def test_revoked_token_cannot_request_report(self):
        self.token.revoked_at = datetime.now(tz=UTC)
        self.token.save(update_fields=["revoked_at"])

        response = self.api_request({})

        self.assertEqual(response.status_code, 401)


class AccountSettingsTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="account-user",
            password="test-password",
        )
        self.client.force_login(self.user)

    def test_user_can_update_email_and_create_permission_scoped_token(self):
        permission = Permission.objects.get(
            content_type__app_label="reports",
            codename="api_request_report",
        )
        response = self.client.post(
            "/account/",
            {"action": "save_email", "email": "new@example.com"},
        )
        self.assertRedirects(response, "/account/")
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, "new@example.com")

        response = self.client.post(
            "/account/",
            {
                "action": "create_token",
                "name": "nightly",
                "permissions": [permission.pk],
            },
        )
        self.assertEqual(response.status_code, 200)
        raw_token = response.context["generated_token"]
        token = ApiToken.objects.get(user=self.user, name="nightly")
        self.assertNotEqual(token.token_hash, raw_token)
        self.assertEqual(list(token.permissions.all()), [permission])
        self.assertContains(response, raw_token)
        self.assertEqual(response["Cache-Control"], "no-store")

        response = self.client.post(
            "/account/",
            {"action": "revoke_token", "token_id": token.pk},
        )
        self.assertRedirects(response, "/account/")
        token.refresh_from_db()
        self.assertIsNotNone(token.revoked_at)

    def test_user_cannot_revoke_another_users_token(self):
        other_user = get_user_model().objects.create_user(username="other-user")
        token = ApiToken.objects.create(
            user=other_user,
            name="other token",
            token_hash=ApiToken.hash_token("other-secret"),
        )

        response = self.client.post(
            "/account/",
            {"action": "revoke_token", "token_id": token.pk},
        )

        self.assertEqual(response.status_code, 404)
        token.refresh_from_db()
        self.assertIsNone(token.revoked_at)
