import json
import logging
from datetime import date, timedelta
from smtplib import SMTPException, SMTPResponseException

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.db.models import Max, Sum
from django.http import JsonResponse
from django.template.loader import render_to_string
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from .models import ApiToken, Record, Report
from .record_filters import exclude_spf_passing_sources, non_aligned_records

logger = logging.getLogger(__name__)
REPORT_PERMISSION = "reports.api_request_report"
CHART_COLORS = ("#176b87", "#32a27b", "#e1a546", "#d76e64", "#8066aa", "#4f83c2")


def _error(message, status):
    return JsonResponse({"error": message}, status=status)


def _smtp_error_message(exc):
    if isinstance(exc, SMTPResponseException):
        response = exc.smtp_error
        if isinstance(response, bytes):
            response = response.decode("utf-8", errors="replace")
        detail = f"SMTP server replied with {exc.smtp_code}: {response}"
    else:
        detail = str(exc).strip() or exc.__class__.__name__
    return f"The report could not be sent: {detail}. Check the application log for details."


def _pie_chart(rows, *, label_key, value_key):
    total = sum(row[value_key] for row in rows)
    position = 0.0
    stops = []
    segments = []
    for index, row in enumerate(rows):
        end = position + (row[value_key] * 100 / total if total else 0)
        color = CHART_COLORS[index % len(CHART_COLORS)]
        segments.append(
            {
                "label": row[label_key],
                "messages": row[value_key],
                "color": color,
                "percent": f"{row[value_key] * 100 / total:.2f}" if total else "0",
            }
        )
        stops.append(f"{color} {position:.2f}% {end:.2f}%")
        position = end
    return {
        "segments": segments,
        "style": f"background: conic-gradient({', '.join(stops)});" if stops else "",
    }


def _parse_request(body):
    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise ValueError("Request body must be valid JSON.") from None
    if not isinstance(payload, dict):
        raise ValueError("Request body must be a JSON object.")

    today = timezone.localdate()
    try:
        end_date = date.fromisoformat(payload["end_date"]) if "end_date" in payload else today
    except (TypeError, ValueError):
        raise ValueError("end_date must be an ISO date (YYYY-MM-DD).") from None
    try:
        start_date = (
            date.fromisoformat(payload["start_date"])
            if "start_date" in payload
            else end_date - timedelta(days=6)
        )
    except (TypeError, ValueError):
        raise ValueError("start_date must be an ISO date (YYYY-MM-DD).") from None
    if start_date > end_date:
        raise ValueError("start_date must not be after end_date.")

    domains = payload.get("domains")
    if domains is not None:
        if not isinstance(domains, list) or any(
            not isinstance(domain, str) or not domain for domain in domains
        ):
            raise ValueError("domains must be a list of non-empty domain names.")
        if not domains:
            raise ValueError("Omit domains to include all domains.")
        domains = list(dict.fromkeys(domains))
    return start_date, end_date, domains


def _get_token(request):
    authorization = request.headers.get("Authorization", "")
    scheme, separator, raw_token = authorization.partition(" ")
    if not separator or scheme.lower() != "bearer" or not raw_token:
        return None
    try:
        token = ApiToken.objects.select_related("user").get(
            token_hash=ApiToken.hash_token(raw_token),
            revoked_at__isnull=True,
        )
    except ApiToken.DoesNotExist:
        return None
    if not token.user.is_active:
        return None
    return token


def _report_data(start_date, end_date, domains):
    records = Record.objects.select_related("report").filter(
        report__begin__date__range=(start_date, end_date)
    )
    if domains is not None:
        known_domains = set(Report.objects.values_list("domain", flat=True).distinct())
        unknown_domains = sorted(set(domains) - known_domains)
        if unknown_domains:
            raise ValueError(f"Unknown domain(s): {', '.join(unknown_domains)}.")
        records = records.filter(report__domain__in=domains)

    totals = records.aggregate(messages=Sum("count"))
    by_domain = list(
        records.values("report__domain").annotate(messages=Sum("count")).order_by("report__domain")
    )
    by_destination = list(
        records.values("report__reporter")
        .annotate(messages=Sum("count"))
        .order_by("report__reporter")
    )
    violations = exclude_spf_passing_sources(non_aligned_records(records))
    policy_totals = list(
        violations.values("disposition").annotate(messages=Sum("count")).order_by("disposition")
    )
    violation_count = violations.aggregate(messages=Sum("count"))["messages"] or 0
    top_sources = list(
        violations.values("source_ip")
        .annotate(
            messages=Sum("count"),
            country_code=Max("source_country_code"),
        )
        .order_by("-messages", "source_ip")[:10]
    )
    report_records = list(
        records.order_by("-report__begin", "report__domain", "source_ip").values(
            "report__begin",
            "report__domain",
            "report__reporter",
            "source_ip",
            "source_country_code",
            "count",
            "disposition",
            "dkim_aligned",
            "spf_aligned",
            "header_from",
            "envelope_from",
            "dkim_domain",
            "dkim_result",
            "spf_domain",
            "spf_result",
        )
    )
    return {
        "message_count": totals["messages"] or 0,
        "report_count": records.values(
            "report__reporter", "report__report_id", "report__domain", "report__begin"
        )
        .distinct()
        .count(),
        "by_domain": by_domain,
        "by_destination": by_destination,
        "domain_chart": _pie_chart(by_domain, label_key="report__domain", value_key="messages"),
        "destination_chart": _pie_chart(
            by_destination, label_key="report__reporter", value_key="messages"
        ),
        "policy_totals": policy_totals,
        "policy_chart": _pie_chart(
            [{**row, "label": row["disposition"].title()} for row in policy_totals],
            label_key="label",
            value_key="messages",
        ),
        "top_sources": top_sources,
        "report_records": report_records,
        "violation_count": violation_count,
        "domains": [row["report__domain"] for row in by_domain],
    }


@require_POST
@csrf_exempt
def api_generate_report(request):
    if request.content_type != "application/json":
        return _error("Content-Type must be application/json.", 415)

    token = _get_token(request)
    if token is None:
        response = _error("A valid bearer token is required.", 401)
        response["WWW-Authenticate"] = "Bearer"
        return response
    if not token.permits(REPORT_PERMISSION):
        return _error("This token is not permitted to request reports.", 403)
    if not token.user.email:
        return _error("Add an email address in account settings before requesting reports.", 400)
    token.last_used_at = timezone.now()
    token.save(update_fields=["last_used_at"])

    try:
        start_date, end_date, domains = _parse_request(request.body)
        report_data = _report_data(start_date, end_date, domains)
    except ValueError as exc:
        return _error(str(exc), 400)

    context = {
        **report_data,
        "start_date": start_date,
        "end_date": end_date,
        "generated_at": timezone.now(),
    }
    html_body = render_to_string("reports/email_report.html", context)
    text_body = (
        f"DMARC report for {start_date.isoformat()} through {end_date.isoformat()}. "
        f"{report_data['message_count']} messages across {report_data['report_count']} reports; "
        f"{report_data['violation_count']} policy-violating messages."
    )
    email = EmailMultiAlternatives(
        subject=f"DMARC report: {start_date.isoformat()} to {end_date.isoformat()}",
        body=text_body,
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[token.user.email],
    )
    email.attach_alternative(html_body, "text/html")
    try:
        sent = email.send(fail_silently=False)
    except (OSError, SMTPException) as exc:
        logger.exception("Could not send an API-generated DMARC report.")
        return _error(_smtp_error_message(exc), 502)
    if sent != 1:
        logger.error("SMTP backend did not confirm delivery of an API-generated report.")
        return _error(
            "The SMTP backend did not confirm delivery. Check the application log for details.",
            502,
        )

    return JsonResponse(
        {
            "status": "sent",
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "domains": report_data["domains"],
            "recipient": token.user.email,
            "report_count": report_data["report_count"],
            "message_count": report_data["message_count"],
            "violation_count": report_data["violation_count"],
        }
    )
