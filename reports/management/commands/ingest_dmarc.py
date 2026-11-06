import imaplib
import os
import re
import ssl
import time
from email import message_from_bytes
from email.policy import default
from email.utils import parsedate_to_datetime
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from reports.ip_enrichment import lookup_source_ip
from reports.models import ImportedMessage, Record, Report
from reports.parser import MAX_EMAIL_BYTES, ReportParseError, parse_reports_from_message

UID_SIZE_PATTERN = re.compile(rb"RFC822\.SIZE\s+(\d+)", re.IGNORECASE)


def _read_credentials():
    credentials_path = os.environ.get("IMAP_CREDENTIALS_FILE")
    if credentials_path:
        lines = Path(credentials_path).read_text(encoding="utf-8").splitlines()
        if len(lines) < 2 or not lines[0].strip() or not lines[1]:
            raise CommandError(
                "IMAP credentials file must contain username and password on separate lines."
            )
        return lines[0].strip(), lines[1]
    username = os.environ.get("IMAP_USERNAME", "").strip()
    password_path = os.environ.get("IMAP_PASSWORD_FILE")
    password = os.environ.get("IMAP_PASSWORD")
    if password_path:
        password = Path(password_path).read_text(encoding="utf-8").rstrip("\r\n")
    if not username or not password:
        raise CommandError(
            "Configure IMAP credentials using a credentials file or username and password."
        )
    return username, password


def _message_size(client, uid):
    status, data = client.uid("FETCH", str(uid), "(RFC822.SIZE)")
    if status != "OK":
        raise OSError("Could not read message size from IMAP server.")
    match = UID_SIZE_PATTERN.search(b" ".join(part for part in data if isinstance(part, bytes)))
    if not match:
        raise OSError("IMAP server did not return a message size.")
    return int(match.group(1))


def _message_body(client, uid):
    status, data = client.uid("FETCH", str(uid), "(BODY.PEEK[])")
    if status != "OK":
        raise OSError("Could not fetch message from IMAP server.")
    body = next((part[1] for part in data if isinstance(part, tuple)), None)
    if body is None:
        raise OSError("IMAP server returned no message body.")
    return body


def _message_metadata(raw_message):
    message = message_from_bytes(raw_message, policy=default)
    sent_at = None
    try:
        sent_at = parsedate_to_datetime(str(message["Date"]))
        if timezone.is_naive(sent_at):
            sent_at = timezone.make_aware(sent_at)
    except (IndexError, TypeError, ValueError, OverflowError):
        pass
    return {
        "subject": str(message.get("Subject", "")),
        "sender": str(message.get("From", "")),
        "sent_at": sent_at,
    }


def _uidvalidity(client):
    values = client.untagged_responses.get("UIDVALIDITY", [])
    if not values:
        raise OSError("IMAP server did not provide UIDVALIDITY.")
    return values[-1].decode("ascii", "strict")


def _persist_reports(parsed_reports, source_email):
    added = 0
    source_ips = {record.source_ip for parsed in parsed_reports for record in parsed.records}
    source_metadata = {source_ip: lookup_source_ip(source_ip) for source_ip in source_ips}
    with transaction.atomic():
        for parsed in parsed_reports:
            report, created = Report.objects.get_or_create(
                reporter=parsed.reporter,
                report_id=parsed.report_id,
                domain=parsed.domain,
                begin=parsed.begin,
                defaults={
                    "source_email": source_email,
                    "end": parsed.end,
                    "policy_disposition": parsed.policy_disposition,
                    "policy_pct": parsed.policy_pct,
                    "policy_adkim": parsed.policy_adkim,
                    "policy_aspf": parsed.policy_aspf,
                },
            )
            if not created and report.source_email_id is None:
                report.source_email = source_email
                report.save(update_fields=["source_email"])
            if created:
                Record.objects.bulk_create(
                    [
                        Record(
                            report=report,
                            source_ip=record.source_ip,
                            **source_metadata[record.source_ip],
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
                        for record in parsed.records
                    ]
                )
                added += 1
    return added


def poll_mailbox(*, retry_failed=False):
    host = os.environ.get("IMAP_HOST", "").strip()
    if not host:
        raise CommandError("IMAP_HOST must be configured.")
    username, password = _read_credentials()
    folder = os.environ.get("IMAP_FOLDER", "INBOX")
    port = int(os.environ.get("IMAP_PORT", "993"))
    batch_size = int(os.environ.get("IMAP_BATCH_SIZE", "100"))
    if not 1 <= batch_size <= 1000:
        raise CommandError("IMAP_BATCH_SIZE must be between 1 and 1000.")

    context = ssl.create_default_context()
    client = imaplib.IMAP4_SSL(host, port, ssl_context=context, timeout=30)
    try:
        client.login(username, password)
        status, _ = client.select(folder, readonly=True)
        if status != "OK":
            raise OSError("Could not select the configured IMAP folder in read-only mode.")
        uidvalidity = _uidvalidity(client)
        status, data = client.uid("SEARCH", None, "ALL")
        if status != "OK":
            raise OSError("Could not search the configured IMAP folder.")

        uids = [int(value) for value in (data[0] or b"").split()]
        processed = ImportedMessage.objects.filter(
            folder=folder, uidvalidity=uidvalidity
        ).values_list("uid", "status")
        processed_uids = {uid for uid, state in processed if state != "error" or not retry_failed}
        pending = [uid for uid in uids if uid not in processed_uids][:batch_size]
        imported = 0
        failures = 0
        scanned = 0
        for uid in pending:
            scanned += 1
            metadata = {"subject": "", "sender": "", "sent_at": None}
            try:
                if _message_size(client, uid) > MAX_EMAIL_BYTES:
                    raise ReportParseError("Email exceeds the maximum supported size.")
                raw_message = _message_body(client, uid)
                metadata = _message_metadata(raw_message)
                parsed_reports = parse_reports_from_message(raw_message)
                source_email, _ = ImportedMessage.objects.get_or_create(
                    folder=folder,
                    uidvalidity=uidvalidity,
                    uid=uid,
                    defaults={"status": "processing", **metadata},
                )
                imported += _persist_reports(parsed_reports, source_email)
                state = "processed" if parsed_reports else "no_reports"
                error = ""
            except (ReportParseError, OSError, ValueError, UnicodeError) as exc:
                state = "error"
                error = f"Could not process message ({type(exc).__name__})."
                failures += 1
            ImportedMessage.objects.update_or_create(
                folder=folder,
                uidvalidity=uidvalidity,
                uid=uid,
                defaults={
                    **metadata,
                    "status": state,
                    "report_count": len(parsed_reports) if state != "error" else 0,
                    "error": error,
                },
            )
        return {"scanned": scanned, "reports_added": imported, "failures": failures}
    finally:
        client.logout()


class Command(BaseCommand):
    help = "Import DMARC aggregate reports over TLS without changing mailbox messages."

    def add_arguments(self, parser):
        parser.add_argument(
            "--once", action="store_true", help="Poll once instead of running continuously."
        )
        parser.add_argument(
            "--retry-failed",
            action="store_true",
            help="Retry previously failed messages during this poll.",
        )

    def handle(self, *args, **options):
        interval = int(os.environ.get("IMAP_POLL_INTERVAL", "300"))
        if interval < 15:
            raise CommandError("IMAP_POLL_INTERVAL must be at least 15 seconds.")
        while True:
            try:
                result = poll_mailbox(retry_failed=options["retry_failed"])
            except (imaplib.IMAP4.error, OSError, TimeoutError) as exc:
                if options["once"]:
                    raise CommandError(f"IMAP polling failed ({type(exc).__name__}).") from exc
                self.stderr.write(
                    f"IMAP polling failed ({type(exc).__name__}); retrying in {interval}s."
                )
            else:
                self.stdout.write(
                    f"Scanned {result['scanned']} messages; "
                    f"added {result['reports_added']} reports; "
                    f"{result['failures']} message errors."
                )
                if options["once"]:
                    return
            time.sleep(interval)
