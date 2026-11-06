from django.urls import path
from django.views.generic import RedirectView

from . import api, views

urlpatterns = [
    path("", RedirectView.as_view(pattern_name="dashboard", permanent=False)),
    path("dashboard/", views.dashboard, name="dashboard"),
    path("ingest/", views.ingest_history, name="ingest_history"),
    path(
        "ingest/email/<int:pk>/position/",
        views.ingest_email_position,
        name="ingest_email_position",
    ),
    path("account/", views.account_settings, name="account_settings"),
    path("api/v1/reports/", api.api_generate_report, name="api_generate_report"),
    path("healthz/", views.healthz, name="healthz"),
]
