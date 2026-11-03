import ipaddress
from functools import lru_cache
from pathlib import Path

import dns.exception
import dns.resolver
import dns.reversename
import geoip2.database
from django.conf import settings
from geoip2.errors import AddressNotFoundError


@lru_cache(maxsize=4)
def _reader(path, modified_at):
    return geoip2.database.Reader(path)


def _get_reader(path):
    path = Path(path)
    try:
        modified_at = path.stat().st_mtime_ns
    except FileNotFoundError:
        return None
    return _reader(str(path), modified_at)


def _empty_metadata():
    return {
        "source_hostname": "",
        "source_asn": None,
        "source_organization": "",
        "source_country_code": "",
        "source_country": "",
        "source_city": "",
    }


def lookup_source_ip(source_ip):
    address = ipaddress.ip_address(source_ip)
    metadata = _empty_metadata()
    if not address.is_global:
        return metadata

    try:
        answer = dns.resolver.resolve(
            dns.reversename.from_address(address.compressed),
            "PTR",
            lifetime=1,
        )
    except (dns.exception.DNSException, OSError):
        pass
    else:
        metadata["source_hostname"] = answer[0].to_text().rstrip(".")[:255]

    city_reader = _get_reader(settings.GEOIP_CITY_DB)
    if city_reader is not None:
        try:
            city = city_reader.city(address.compressed)
        except AddressNotFoundError:
            pass
        else:
            metadata["source_country_code"] = city.country.iso_code or ""
            metadata["source_country"] = (city.country.name or "")[:128]
            metadata["source_city"] = (city.city.name or "")[:128]

    asn_reader = _get_reader(settings.GEOIP_ASN_DB)
    if asn_reader is not None:
        try:
            asn = asn_reader.asn(address.compressed)
        except AddressNotFoundError:
            pass
        else:
            metadata["source_asn"] = asn.autonomous_system_number
            metadata["source_organization"] = (asn.autonomous_system_organization or "")[:255]

    return metadata
