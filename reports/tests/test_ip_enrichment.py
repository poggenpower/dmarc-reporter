from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.test import SimpleTestCase

from reports.ip_enrichment import lookup_source_ip


class IpEnrichmentTests(SimpleTestCase):
    def test_country_flag_returns_regional_flag_or_globe_fallback(self):
        from reports.templatetags.querystring import country_flag

        self.assertEqual(country_flag("us"), "🇺🇸")
        self.assertEqual(country_flag(""), "🌐")
        self.assertEqual(country_flag("USA"), "🌐")

    def test_lookup_returns_ptr_asn_and_location(self):
        answer = [Mock()]
        answer[0].to_text.return_value = "mail.example.net."
        city_record = SimpleNamespace(
            country=SimpleNamespace(iso_code="US", name="United States"),
            city=SimpleNamespace(name="Example City"),
        )
        asn_record = SimpleNamespace(
            autonomous_system_number=64500,
            autonomous_system_organization="Example Network",
        )
        with (
            patch("reports.ip_enrichment.dns.resolver.resolve", return_value=answer) as resolve,
            patch(
                "reports.ip_enrichment._get_reader",
                side_effect=[
                    Mock(city=Mock(return_value=city_record)),
                    Mock(asn=Mock(return_value=asn_record)),
                ],
            ),
        ):
            result = lookup_source_ip("8.8.8.8")

        self.assertEqual(result["source_hostname"], "mail.example.net")
        self.assertEqual(result["source_asn"], 64500)
        self.assertEqual(result["source_organization"], "Example Network")
        self.assertEqual(result["source_country_code"], "US")
        self.assertEqual(result["source_country"], "United States")
        self.assertEqual(result["source_city"], "Example City")
        resolve.assert_called_once()

    def test_reserved_addresses_are_not_resolved(self):
        with (
            patch("reports.ip_enrichment.dns.resolver.resolve") as resolve,
            patch("reports.ip_enrichment._get_reader") as get_reader,
        ):
            result = lookup_source_ip("192.0.2.1")

        self.assertEqual(result["source_hostname"], "")
        self.assertIsNone(result["source_asn"])
        resolve.assert_not_called()
        get_reader.assert_not_called()
