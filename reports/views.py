import secrets

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Count, F, Max, Min, Q, Sum
from django.http import Http404, HttpResponseForbidden, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.html import format_html
from django.utils.http import urlencode

from .admin_users import is_admin_user
from .forms import AccountEmailForm, ApiTokenCreateForm, DashboardFilterForm
from .management.commands.ingest_dmarc import BACKFILL_ERRORS, backfill_imported_messages
from .models import ApiToken, ImportedMessage, Record, Report
from .record_filters import exclude_spf_passing_sources, non_aligned_records

PIE_COLORS = (
    "#176b87",
    "#32a27b",
    "#e1a546",
    "#d76e64",
    "#8066aa",
    "#4f83c2",
    "#a1a83e",
    "#bc6d9a",
    "#718096",
    "#3b9c9c",
)


def _pie_chart(rows, *, filter_name):
    total = sum(row["total"] for row in rows)
    segments = []
    stops = []
    position = 0.0
    for index, row in enumerate(rows):
        value = row["value"]
        end = position + (row["total"] * 100 / total if total else 0)
        color = PIE_COLORS[index % len(PIE_COLORS)]
        segments.append(
            {
                "value": value,
                "label": row["label"],
                "total": row["total"],
                "color": color,
            }
        )
        stops.append(f"{color} {position:.3f}% {end:.3f}%")
        position = end
    return {
        "total": total,
        "segments": segments,
        "style": format_html("background: conic-gradient({})", ", ".join(stops)) if stops else "",
        "filter_name": filter_name,
    }


@login_required
def dashboard(request):
    domains = Report.objects.order_by("domain").values_list("domain", flat=True).distinct()
    reporters = Report.objects.order_by("reporter").values_list("reporter", flat=True).distinct()
    form_data = request.GET or {"non_aligned": "on", "exclude_spf_pass": "on"}
    form = DashboardFilterForm(form_data, domains=domains, reporters=reporters)
    records = Record.objects.select_related("report", "report__source_email")
    filters = form.cleaned_data if form.is_valid() else {}
    if form.is_valid():
        if filters["begin"]:
            records = records.filter(report__begin__date__gte=filters["begin"])
        if filters["end"]:
            records = records.filter(report__begin__date__lte=filters["end"])
        if filters["domain"]:
            records = records.filter(report__domain=filters["domain"])
        if filters["reporter"]:
            records = records.filter(report__reporter=filters["reporter"])
        if filters["source_ip"]:
            records = records.filter(source_ip=filters["source_ip"])
        if filters["disposition"]:
            records = records.filter(disposition=filters["disposition"])
    summary_records = records
    if form.is_valid():
        if form.cleaned_data["non_aligned"]:
            records = non_aligned_records(records)
        if form.cleaned_data["exclude_spf_pass"]:
            records = exclude_spf_passing_sources(records)

    totals = summary_records.aggregate(
        messages=Sum("count"),
        aligned=Sum("count", filter=Q(dkim_aligned__iexact="pass") | Q(spf_aligned__iexact="pass")),
        first_record=Min("report__begin"),
        latest_record=Max("report__end"),
    )
    message_count = totals["messages"] or 0
    aligned_count = totals["aligned"] or 0
    report_count = (
        summary_records.values(
            "report__reporter", "report__report_id", "report__domain", "report__begin"
        )
        .distinct()
        .count()
    )
    domains_chart = _pie_chart(
        [
            {
                "value": row["report__domain"],
                "label": row["report__domain"],
                "total": row["total"],
            }
            for row in records.values("report__domain")
            .annotate(total=Sum("count"))
            .order_by("-total", "report__domain")
        ],
        filter_name="domain",
    )
    dispositions_chart = _pie_chart(
        [
            {
                "value": row["disposition"],
                "label": row["disposition"].title(),
                "total": row["total"],
            }
            for row in records.values("disposition")
            .annotate(total=Sum("count"))
            .order_by("-total", "disposition")
        ],
        filter_name="disposition",
    )
    reporters_chart = _pie_chart(
        [
            {
                "value": row["report__reporter"],
                "label": row["report__reporter"],
                "total": row["total"],
            }
            for row in records.values("report__reporter")
            .annotate(total=Sum("count"))
            .order_by("-total", "report__reporter")
        ],
        filter_name="reporter",
    )
    sources = list(
        records.values("source_ip")
        .annotate(
            total=Sum("count"),
            hostname=Max("source_hostname"),
            asn=Max("source_asn"),
            organization=Max("source_organization"),
            country_code=Max("source_country_code"),
            country=Max("source_country"),
            city=Max("source_city"),
        )
        .order_by("-total", "source_ip")[:10]
    )
    max_source = max((row["total"] for row in sources), default=0)
    source_bars = [
        {
            "ip": row["source_ip"],
            "label": row["hostname"] or row["source_ip"],
            "hostname": row["hostname"],
            "asn": row["asn"],
            "organization": row["organization"],
            "country_code": row["country_code"],
            "country": row["country"],
            "city": row["city"],
            "count": row["total"],
            "percent": round(row["total"] * 100 / max_source) if max_source else 0,
        }
        for row in sources
    ]
    page = Paginator(
        records.order_by("-report__begin", "report__domain", "source_ip"), 50
    ).get_page(request.GET.get("page"))

    return render(
        request,
        "reports/dashboard.html",
        {
            "form": form,
            "non_aligned_param": "on" if filters.get("non_aligned", True) else None,
            "exclude_spf_pass_param": "on" if filters.get("exclude_spf_pass", True) else None,
            "page": page,
            "report_count": report_count,
            "message_count": message_count,
            "aligned_count": aligned_count,
            "alignment_rate": round(aligned_count * 100 / message_count, 1) if message_count else 0,
            "first_record": totals["first_record"],
            "latest_record": totals["latest_record"],
            "domains_chart": domains_chart,
            "dispositions_chart": dispositions_chart,
            "reporters_chart": reporters_chart,
            "source_bars": source_bars,
            "geoip_city_available": settings.GEOIP_CITY_DB.is_file(),
            "geoip_asn_available": settings.GEOIP_ASN_DB.is_file(),
            "total_reports": Report.objects.count(),
        },
    )


def healthz(request):
    return JsonResponse({"status": "ok", "version": settings.APP_VERSION})


INGEST_FILTERS = {
    "all": Q(),
    "needs_backfill": Q(subject="")
    | Q(sender="")
    | Q(sent_at__isnull=True)
    | Q(linked_report_count__lt=F("report_count")),
    "processed": Q(status="processed"),
    "no_reports": Q(status="no_reports"),
    "error": Q(status="error"),
}
NEEDS_BACKFILL = INGEST_FILTERS["needs_backfill"]
INGEST_PAGE_SIZE = 50
MAX_BULK_BACKFILL = 200


def _ingest_queryset(active_filter, search):
    emails = ImportedMessage.objects.annotate(linked_report_count=Count("reports"))
    emails = emails.filter(INGEST_FILTERS.get(active_filter, Q()))
    if search:
        emails = emails.filter(Q(subject__icontains=search) | Q(sender__icontains=search))
    return emails.order_by("-processed_at", "-uid", "-pk")


def _backfill_redirect(request):
    query = {
        "filter": request.POST.get("filter", "all"),
        "q": request.POST.get("q", ""),
        "page": request.POST.get("page", ""),
    }
    query = {key: value for key, value in query.items() if value and value != "all"}
    url = reverse("ingest_history")
    return redirect(f"{url}?{urlencode(query)}" if query else url)


def _run_bulk_backfill(request, emails):
    emails = list(emails[:MAX_BULK_BACKFILL])
    if not emails:
        messages.info(request, "No emails to backfill.")
        return
    try:
        linked_total, failures = backfill_imported_messages(emails)
    except BACKFILL_ERRORS as exc:
        messages.error(request, str(exc))
        return
    succeeded = len(emails) - len(failures)
    messages.success(
        request,
        f"Backfilled {succeeded} email(s); linked {linked_total} previously unlinked reports.",
    )
    if failures:
        details = "; ".join(f"UID {email.uid}: {error}" for email, error in failures[:3])
        messages.error(request, f"{len(failures)} email(s) could not be backfilled. {details}")
    if len(emails) == MAX_BULK_BACKFILL:
        messages.info(
            request,
            f"Only the first {MAX_BULK_BACKFILL} emails were processed; run the backfill again "
            "for the rest.",
        )


@login_required
def ingest_history(request):
    active_filter = request.GET.get("filter", "all")
    if active_filter not in INGEST_FILTERS:
        active_filter = "all"
    search = request.GET.get("q", "").strip()[:200]
    can_backfill = is_admin_user(request.user)

    if request.method == "POST":
        if not can_backfill:
            return HttpResponseForbidden()
        action = request.POST.get("action")
        scope = _ingest_queryset(
            request.POST.get("filter", "all"), request.POST.get("q", "").strip()[:200]
        )
        if action == "backfill_selected":
            ids = []
            for value in request.POST.getlist("email_ids"):
                try:
                    ids.append(int(value))
                except ValueError:
                    raise Http404("Imported email not found.") from None
            if not ids:
                messages.info(request, "Select at least one email to backfill.")
            else:
                _run_bulk_backfill(request, ImportedMessage.objects.filter(pk__in=ids))
        elif action == "backfill_all":
            _run_bulk_backfill(request, scope.filter(NEEDS_BACKFILL))
        else:
            raise Http404("Unknown action.")
        return _backfill_redirect(request)

    emails = _ingest_queryset(active_filter, search)
    paginator = Paginator(emails, INGEST_PAGE_SIZE)
    page = paginator.get_page(request.GET.get("page"))
    return render(
        request,
        "reports/ingest_history.html",
        {
            "page": page,
            "page_range": paginator.get_elided_page_range(page.number, on_each_side=2, on_ends=1),
            "active_filter": active_filter,
            "search": search,
            "needs_backfill_count": emails.filter(NEEDS_BACKFILL).count() if can_backfill else 0,
            "last_ingest_at": ImportedMessage.objects.aggregate(last_ingest=Max("processed_at"))[
                "last_ingest"
            ],
            "can_backfill": can_backfill,
        },
    )


@login_required
def account_settings(request):
    email_form = AccountEmailForm(initial={"email": request.user.email})
    token_form = ApiTokenCreateForm()
    generated_token = None

    if request.method == "POST":
        action = request.POST.get("action")
        if action == "save_email":
            email_form = AccountEmailForm(request.POST)
            if email_form.is_valid():
                request.user.email = email_form.cleaned_data["email"]
                request.user.save(update_fields=["email"])
                return redirect("account_settings")
        elif action == "create_token":
            token_form = ApiTokenCreateForm(request.POST)
            if token_form.is_valid():
                generated_token = secrets.token_urlsafe(32)
                token = ApiToken.objects.create(
                    user=request.user,
                    name=token_form.cleaned_data["name"],
                    token_hash=ApiToken.hash_token(generated_token),
                )
                token.permissions.set(token_form.cleaned_data["permissions"])
                token_form = ApiTokenCreateForm()
        elif action == "revoke_token":
            try:
                token = request.user.api_tokens.get(pk=request.POST.get("token_id"))
            except (ApiToken.DoesNotExist, ValueError):
                raise Http404("API token not found.") from None
            token.revoked_at = timezone.now()
            token.save(update_fields=["revoked_at"])
            return redirect("account_settings")

    response = render(
        request,
        "reports/account_settings.html",
        {
            "email_form": email_form,
            "token_form": token_form,
            "tokens": request.user.api_tokens.all(),
            "generated_token": generated_token,
        },
    )
    if generated_token:
        response["Cache-Control"] = "no-store"
    return response
