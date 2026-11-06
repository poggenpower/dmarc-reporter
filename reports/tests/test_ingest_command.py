import gzip
import os
from email.message import EmailMessage
from pathlib import Path
from unittest.mock import patch

from django.core.management import call_command
from django.test import TestCase

from reports.management.commands.ingest_dmarc import poll_mailbox
from reports.models import ImportedMessage, Record, Report

FIXTURE = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "report_gzip.xml"


def make_email():
    message = EmailMessage()
    message["Subject"] = "Example DMARC report"
    message["From"] = "reports@example.net"
    message["Date"] = "Fri, 7 Nov 2025 12:00:00 +0000"
    message.set_content("DMARC aggregate report")
    message.add_attachment(
        gzip.compress(FIXTURE.read_bytes()),
        maintype="application",
        subtype="gzip",
        filename="aggregate.xml.gz",
    )
    return message.as_bytes()


class FakeIMAP:
    def __init__(self, *args, **kwargs):
        self.untagged_responses = {"UIDVALIDITY": [b"1234"]}
        self.calls = []
        self.body = make_email()

    def login(self, username, password):
        self.calls.append(("LOGIN", username))
        return "OK", []

    def select(self, folder, readonly=False):
        self.calls.append(("SELECT", folder, readonly))
        return "OK", [b"1"]

    def uid(self, command, *args):
        self.calls.append((command, *args))
        if command == "SEARCH":
            return "OK", [b"7"]
        if command == "FETCH" and "RFC822.SIZE" in args[1]:
            return "OK", [b"(UID 7 RFC822.SIZE 500)"]
        if command == "FETCH" and "BODY.PEEK[]" in args[1]:
            return "OK", [(b"message", self.body)]
        raise AssertionError(f"Unexpected IMAP command: {command}")

    def logout(self):
        self.calls.append(("LOGOUT",))
        return "BYE", []


class IngestCommandTests(TestCase):
    def test_imports_reports_without_mutating_mailbox(self):
        fake_imap = FakeIMAP()
        with (
            patch.dict(
                os.environ,
                {
                    "IMAP_HOST": "imap.example.invalid",
                    "IMAP_USERNAME": "test-user",
                    "IMAP_PASSWORD": "test-password",
                    "IMAP_FOLDER": "INBOX",
                },
                clear=False,
            ),
            patch(
                "reports.management.commands.ingest_dmarc.imaplib.IMAP4_SSL",
                return_value=fake_imap,
            ),
        ):
            result = poll_mailbox()

        self.assertEqual(result, {"scanned": 1, "reports_added": 1, "failures": 0})
        self.assertEqual(Report.objects.count(), 1)
        imported_message = ImportedMessage.objects.get()
        self.assertEqual(imported_message.status, "processed")
        self.assertEqual(imported_message.subject, "Example DMARC report")
        self.assertEqual(imported_message.sender, "reports@example.net")
        self.assertEqual(imported_message.sent_at.isoformat(), "2025-11-07T12:00:00+00:00")
        self.assertEqual(Report.objects.get().source_email, imported_message)
        self.assertIn(("SELECT", "INBOX", True), fake_imap.calls)
        self.assertTrue(
            any("BODY.PEEK[]" in call[-1] for call in fake_imap.calls if call[0] == "FETCH")
        )
        self.assertFalse(
            any(call[0] in {"STORE", "COPY", "MOVE", "EXPUNGE"} for call in fake_imap.calls)
        )
        self.assertEqual(fake_imap.calls[-1], ("LOGOUT",))

    def test_repeated_poll_deduplicates_processed_message(self):
        fake_imap = FakeIMAP()
        with (
            patch.dict(
                os.environ,
                {
                    "IMAP_HOST": "imap.example.invalid",
                    "IMAP_USERNAME": "test-user",
                    "IMAP_PASSWORD": "test-password",
                    "IMAP_FOLDER": "INBOX",
                },
                clear=False,
            ),
            patch(
                "reports.management.commands.ingest_dmarc.imaplib.IMAP4_SSL",
                return_value=fake_imap,
            ),
        ):
            poll_mailbox()
            result = poll_mailbox()

        self.assertEqual(result, {"scanned": 0, "reports_added": 0, "failures": 0})
        self.assertEqual(Report.objects.count(), 1)

    def test_import_saves_source_ip_enrichment(self):
        fake_imap = FakeIMAP()
        enrichment = {
            "source_hostname": "mail.example.net",
            "source_asn": 64500,
            "source_organization": "Example Network",
            "source_country_code": "US",
            "source_country": "United States",
            "source_city": "Example City",
        }
        with (
            patch.dict(
                os.environ,
                {
                    "IMAP_HOST": "imap.example.invalid",
                    "IMAP_USERNAME": "test-user",
                    "IMAP_PASSWORD": "test-password",
                    "IMAP_FOLDER": "INBOX",
                },
                clear=False,
            ),
            patch(
                "reports.management.commands.ingest_dmarc.imaplib.IMAP4_SSL",
                return_value=fake_imap,
            ),
            patch(
                "reports.management.commands.ingest_dmarc.lookup_source_ip",
                return_value=enrichment,
            ) as lookup,
        ):
            poll_mailbox()

        record = Record.objects.get()
        self.assertEqual(record.source_hostname, "mail.example.net")
        self.assertEqual(record.source_asn, 64500)
        self.assertEqual(record.source_organization, "Example Network")
        self.assertEqual(record.source_country_code, "US")
        self.assertEqual(record.source_country, "United States")
        self.assertEqual(record.source_city, "Example City")
        self.assertEqual(
            lookup.call_count,
            Record.objects.values("source_ip").distinct().count(),
        )

    def test_enrich_source_ips_command_updates_existing_records(self):
        report = Report.objects.create(
            reporter="Example Reporter",
            report_id="existing-report",
            domain="example.com",
            begin="2025-01-01T00:00:00Z",
            end="2025-01-02T00:00:00Z",
        )
        record = Record.objects.create(
            report=report,
            source_ip="192.0.2.1",
            count=1,
        )
        enrichment = {
            "source_hostname": "mail.example.net",
            "source_asn": 64500,
            "source_organization": "Example Network",
            "source_country_code": "US",
            "source_country": "United States",
            "source_city": "Example City",
        }

        with patch(
            "reports.management.commands.enrich_source_ips.lookup_source_ip",
            return_value=enrichment,
        ):
            call_command("enrich_source_ips")

        record.refresh_from_db()
        self.assertEqual(record.source_hostname, "mail.example.net")
        self.assertEqual(record.source_organization, "Example Network")
        self.assertEqual(record.source_city, "Example City")
