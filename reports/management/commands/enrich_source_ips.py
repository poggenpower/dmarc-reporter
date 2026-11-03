from itertools import islice

from django.core.management.base import BaseCommand

from reports.ip_enrichment import lookup_source_ip
from reports.models import Record

BATCH_SIZE = 500
ENRICHMENT_FIELDS = (
    "source_hostname",
    "source_asn",
    "source_organization",
    "source_country_code",
    "source_country",
    "source_city",
)


class Command(BaseCommand):
    help = "Refresh hostname, ASN, and geolocation data for stored source IPs."

    def handle(self, *args, **options):
        records = Record.objects.only("id", "source_ip", *ENRICHMENT_FIELDS).iterator(
            chunk_size=BATCH_SIZE
        )
        updated_count = 0
        while batch := list(islice(records, BATCH_SIZE)):
            metadata = {
                source_ip: lookup_source_ip(source_ip)
                for source_ip in {record.source_ip for record in batch}
            }
            for record in batch:
                for field, value in metadata[record.source_ip].items():
                    setattr(record, field, value)
            Record.objects.bulk_update(batch, ENRICHMENT_FIELDS, batch_size=BATCH_SIZE)
            updated_count += len(batch)
        self.stdout.write(f"Enriched {updated_count} records.")
