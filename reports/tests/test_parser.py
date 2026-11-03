import gzip
import io
import zipfile
from email.message import EmailMessage
from pathlib import Path

from django.test import SimpleTestCase

from reports.parser import ReportParseError, parse_report_xml, parse_reports_from_message

FIXTURES = Path(__file__).resolve().parents[2] / "tests" / "fixtures"


def make_message(payload, content_type, filename):
    message = EmailMessage()
    message["Subject"] = "test report"
    message.set_content("DMARC report fixture")
    main_type, sub_type = content_type.split("/", 1)
    message.add_attachment(payload, maintype=main_type, subtype=sub_type, filename=filename)
    return message.as_bytes()


class ReportParserTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.xml = (FIXTURES / "report_xml.xml").read_bytes()
        cls.gzip_xml = (FIXTURES / "report_gzip.xml").read_bytes()
        cls.extended_xml = (FIXTURES / "report_extended.xml").read_bytes()

    def test_parses_sanitized_real_xml(self):
        report = parse_report_xml(self.xml)

        self.assertEqual(report.reporter, "Example Reporting Service")
        self.assertEqual(report.domain, "example.com")
        self.assertEqual(report.report_id, "fixture-1")
        self.assertEqual(report.begin.isoformat(), "2025-01-01T00:00:00+00:00")
        self.assertEqual(report.records[0].source_ip, "192.0.2.1")
        self.assertGreaterEqual(report.records[0].count, 0)

    def test_parses_optional_schema_fields(self):
        report = parse_report_xml(self.extended_xml)

        self.assertEqual(report.domain, "example.com")
        self.assertEqual(report.records[0].source_ip, "192.0.2.3")

    def test_parses_xml_attachment(self):
        reports = parse_reports_from_message(
            make_message(self.xml, "application/xml", "report.xml")
        )

        self.assertEqual(len(reports), 1)
        self.assertEqual(reports[0].report_id, "fixture-1")

    def test_parses_gzip_attachment(self):
        message = make_message(gzip.compress(self.gzip_xml), "application/gzip", "report.xml.gz")

        reports = parse_reports_from_message(message)

        self.assertEqual(len(reports), 1)
        self.assertEqual(reports[0].report_id, "fixture-2")

    def test_parses_zip_attachment(self):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("aggregate.xml", self.xml)
        message = make_message(buffer.getvalue(), "application/zip", "report.zip")

        reports = parse_reports_from_message(message)

        self.assertEqual(len(reports), 1)
        self.assertEqual(reports[0].report_id, "fixture-1")

    def test_ignores_non_report_email(self):
        message = EmailMessage()
        message.set_content("No report attached")

        self.assertEqual(parse_reports_from_message(message.as_bytes()), ())

    def test_rejects_unsafe_xml(self):
        unsafe_xml = b'<!DOCTYPE feedback [<!ENTITY xxe SYSTEM "file:///etc/passwd">]><feedback>&xxe;</feedback>'

        with self.assertRaises(ReportParseError):
            parse_report_xml(unsafe_xml)
