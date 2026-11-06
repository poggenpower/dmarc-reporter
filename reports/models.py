import hashlib

from django.conf import settings
from django.db import models


class Report(models.Model):
    reporter = models.CharField(max_length=255)
    report_id = models.CharField(max_length=255)
    source_email = models.ForeignKey(
        "ImportedMessage",
        related_name="reports",
        blank=True,
        null=True,
        on_delete=models.SET_NULL,
    )
    domain = models.CharField(max_length=253, db_index=True)
    begin = models.DateTimeField(db_index=True)
    end = models.DateTimeField()
    policy_disposition = models.CharField(max_length=16, default="none")
    policy_pct = models.PositiveSmallIntegerField(default=100)
    policy_adkim = models.CharField(max_length=8, blank=True)
    policy_aspf = models.CharField(max_length=8, blank=True)
    imported_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("reporter", "report_id", "domain", "begin"),
                name="unique_dmarc_report_identity",
            )
        ]
        ordering = ("-begin", "domain")

    def __str__(self):
        return f"{self.domain} — {self.reporter} ({self.begin:%Y-%m-%d})"


class Record(models.Model):
    report = models.ForeignKey(Report, related_name="records", on_delete=models.CASCADE)
    source_ip = models.GenericIPAddressField(db_index=True)
    source_hostname = models.CharField(max_length=255, blank=True)
    source_asn = models.PositiveBigIntegerField(blank=True, null=True)
    source_organization = models.CharField(max_length=255, blank=True)
    source_country_code = models.CharField(max_length=2, blank=True)
    source_country = models.CharField(max_length=128, blank=True)
    source_city = models.CharField(max_length=128, blank=True)
    count = models.PositiveBigIntegerField()
    disposition = models.CharField(max_length=16, default="none", db_index=True)
    dkim_aligned = models.CharField(max_length=16, blank=True)
    spf_aligned = models.CharField(max_length=16, blank=True)
    header_from = models.CharField(max_length=253, blank=True, db_index=True)
    envelope_from = models.CharField(max_length=253, blank=True)
    dkim_domain = models.CharField(max_length=253, blank=True)
    dkim_result = models.CharField(max_length=16, blank=True)
    spf_domain = models.CharField(max_length=253, blank=True)
    spf_result = models.CharField(max_length=16, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=("report", "disposition"), name="record_report_disp_idx"),
        ]


class ImportedMessage(models.Model):
    folder = models.CharField(max_length=255)
    uidvalidity = models.CharField(max_length=64)
    uid = models.PositiveBigIntegerField()
    subject = models.TextField(blank=True)
    sender = models.TextField(blank=True)
    sent_at = models.DateTimeField(blank=True, null=True)
    status = models.CharField(max_length=16)
    report_count = models.PositiveIntegerField(default=0)
    error = models.CharField(max_length=255, blank=True)
    processed_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("folder", "uidvalidity", "uid"),
                name="unique_imap_message",
            )
        ]
        indexes = [models.Index(fields=("status", "processed_at"), name="imap_status_date_idx")]


class ApiToken(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, related_name="api_tokens", on_delete=models.CASCADE
    )
    name = models.CharField(max_length=100)
    token_hash = models.CharField(max_length=64, unique=True)
    permissions = models.ManyToManyField("auth.Permission", blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    last_used_at = models.DateTimeField(blank=True, null=True)
    revoked_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        ordering = ("-created_at",)
        permissions = [
            ("api_request_report", "Can request an emailed report through the API"),
        ]

    @staticmethod
    def hash_token(token):
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def permits(self, permission):
        app_label, codename = permission.split(".", 1)
        return self.permissions.filter(
            content_type__app_label=app_label,
            codename=codename,
        ).exists()
