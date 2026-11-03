import gzip
import io
import ipaddress
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime
from email import message_from_bytes
from email.policy import default

from defusedxml import ElementTree
from defusedxml.common import DefusedXmlException

MAX_EMAIL_BYTES = 20 * 1024 * 1024
MAX_ATTACHMENT_BYTES = 8 * 1024 * 1024
MAX_ARCHIVE_MEMBERS = 32


class ReportParseError(ValueError):
    pass


@dataclass(frozen=True)
class RecordData:
    source_ip: str
    count: int
    disposition: str
    dkim_aligned: str
    spf_aligned: str
    header_from: str
    envelope_from: str
    dkim_domain: str
    dkim_result: str
    spf_domain: str
    spf_result: str


@dataclass(frozen=True)
class ReportData:
    reporter: str
    report_id: str
    domain: str
    begin: datetime
    end: datetime
    policy_disposition: str
    policy_pct: int
    policy_adkim: str
    policy_aspf: str
    records: tuple[RecordData, ...]


def _local_name(tag):
    return tag.rsplit("}", 1)[-1]


def _child(parent, name):
    if parent is None:
        return None
    return next((element for element in parent if _local_name(element.tag) == name), None)


def _value(parent, name, default_value=""):
    element = _child(parent, name)
    return (element.text or "").strip() if element is not None else default_value


def _epoch(parent, name):
    value = _value(parent, name)
    try:
        return datetime.fromtimestamp(int(value), tz=UTC)
    except (ValueError, OverflowError, OSError) as exc:
        raise ReportParseError(f"Invalid report date field: {name}") from exc


def _xml_payloads(raw_message):
    if len(raw_message) > MAX_EMAIL_BYTES:
        raise ReportParseError("Email exceeds the maximum supported size.")
    message = message_from_bytes(raw_message, policy=default)
    for part in message.walk():
        if part.get_content_maintype() == "multipart":
            continue
        payload = part.get_payload(decode=True) or b""
        if not payload:
            continue
        filename = (part.get_filename() or "").lower()
        content_type = part.get_content_type().lower()
        if payload.startswith(b"PK\x03\x04") or content_type == "application/zip":
            try:
                with zipfile.ZipFile(io.BytesIO(payload)) as archive:
                    members = [member for member in archive.infolist() if not member.is_dir()]
                    if len(members) > MAX_ARCHIVE_MEMBERS:
                        raise ReportParseError("ZIP attachment contains too many files.")
                    expanded_size = sum(member.file_size for member in members)
                    if expanded_size > MAX_ATTACHMENT_BYTES:
                        raise ReportParseError("ZIP attachment exceeds the expanded size limit.")
                    for member in members:
                        if member.filename.lower().endswith(".xml"):
                            yield archive.read(member)
            except zipfile.BadZipFile as exc:
                raise ReportParseError("Invalid ZIP attachment.") from exc
        elif payload.startswith(b"\x1f\x8b") or content_type in {
            "application/gzip",
            "application/x-gzip",
        }:
            try:
                with gzip.GzipFile(fileobj=io.BytesIO(payload)) as compressed:
                    xml = compressed.read(MAX_ATTACHMENT_BYTES + 1)
                if len(xml) > MAX_ATTACHMENT_BYTES:
                    raise ReportParseError("GZIP attachment exceeds the expanded size limit.")
                yield xml
            except OSError as exc:
                raise ReportParseError("Invalid GZIP attachment.") from exc
        elif "xml" in content_type or filename.endswith(".xml"):
            if len(payload) > MAX_ATTACHMENT_BYTES:
                raise ReportParseError("XML attachment exceeds the size limit.")
            yield payload


def parse_report_xml(xml_bytes):
    try:
        root = ElementTree.fromstring(xml_bytes)
    except (ElementTree.ParseError, DefusedXmlException) as exc:
        raise ReportParseError("Invalid or unsafe XML report.") from exc
    if _local_name(root.tag) != "feedback":
        raise ReportParseError("XML document is not a DMARC feedback report.")

    metadata = _child(root, "report_metadata")
    policy = _child(root, "policy_published")
    if metadata is None or policy is None:
        raise ReportParseError("DMARC report is missing required metadata.")
    date_range = _child(metadata, "date_range")
    if date_range is None:
        raise ReportParseError("DMARC report is missing its date range.")

    domain = _value(policy, "domain").lower()
    if not domain or len(domain) > 253:
        raise ReportParseError("DMARC report has an invalid policy domain.")
    try:
        pct = int(_value(policy, "pct", "100"))
        if not 0 <= pct <= 100:
            raise ValueError
    except ValueError as exc:
        raise ReportParseError("DMARC report has an invalid policy percentage.") from exc

    records = []
    for record in root:
        if _local_name(record.tag) != "record":
            continue
        row = _child(record, "row")
        identifiers = _child(record, "identifiers")
        evaluated = _child(row, "policy_evaluated")
        auth_results = _child(record, "auth_results")
        try:
            source_ip = str(ipaddress.ip_address(_value(row, "source_ip")))
            count = int(_value(row, "count"))
            if count < 0:
                raise ValueError
        except ValueError as exc:
            raise ReportParseError("DMARC report contains an invalid source IP or count.") from exc

        dkim_auth = _child(auth_results, "dkim")
        spf_auth = _child(auth_results, "spf")
        records.append(
            RecordData(
                source_ip=source_ip,
                count=count,
                disposition=_value(evaluated, "disposition", "none").lower(),
                dkim_aligned=_value(evaluated, "dkim").lower(),
                spf_aligned=_value(evaluated, "spf").lower(),
                header_from=_value(identifiers, "header_from").lower()[:253],
                envelope_from=_value(identifiers, "envelope_from").lower()[:253],
                dkim_domain=_value(dkim_auth, "domain").lower()[:253],
                dkim_result=_value(dkim_auth, "result").lower(),
                spf_domain=_value(spf_auth, "domain").lower()[:253],
                spf_result=_value(spf_auth, "result").lower(),
            )
        )

    if not records:
        raise ReportParseError("DMARC report contains no records.")
    reporter = _value(metadata, "org_name")[:255]
    report_id = _value(metadata, "report_id")[:255]
    if not reporter or not report_id:
        raise ReportParseError("DMARC report is missing its reporter or report ID.")
    return ReportData(
        reporter=reporter,
        report_id=report_id,
        domain=domain,
        begin=_epoch(date_range, "begin"),
        end=_epoch(date_range, "end"),
        policy_disposition=_value(policy, "p", "none").lower(),
        policy_pct=pct,
        policy_adkim=_value(policy, "adkim").lower(),
        policy_aspf=_value(policy, "aspf").lower(),
        records=tuple(records),
    )


def parse_reports_from_message(raw_message):
    return tuple(parse_report_xml(payload) for payload in _xml_payloads(raw_message))
