# DMARC Reporter agent summary

## Project purpose
This repo is a Django-based DMARC reporting dashboard and ingestion service. It reads DMARC XML reports from an IMAP mailbox, normalizes them into SQLite, enriches source IPs with GeoIP data, and exposes the data through an authenticated dashboard and API.

## Main areas
- `dmarc_reporter/`: Django project settings, URLs, and app wiring.
- `reports/`: report parsing, ingestion, filters, API endpoints, views, templates, and management commands.
- `geoip/`: local DB-IP GeoIP database files used for enrichment.
- `k8s/`: sample Kubernetes deployment and secret templates.
- `tests/` and `reports/tests/`: Django test suite.

## Key workflows
- Run the full test suite: `python manage.py test`
- Run a focused test module: `python manage.py test reports.tests.test_ingest_command`
- Lint/format with Ruff: `ruff check .` and `ruff format .`
- Local app startup in Docker Compose: `docker compose up --build -d`
- One-off report ingest: `docker compose run --rm ingest python manage.py ingest_dmarc --once`

## Operational constraints
- Preserve the SQLite data volume and avoid committing credentials or local env files.
- Keep IMAP/SMTP configuration in environment variables or untracked local files, not in source control.
- Prefer surgical changes and update tests when behavior changes.
- The app intentionally keeps raw email bodies in memory and does not persist them.
- Authentication and API tokens are sensitive; treat them as secrets.

## Safety and setup notes
- Use the repo’s Django conventions and existing patterns in `reports/` before adding new logic.
- If a change affects ingestion, parsing, dashboard behavior, or GeoIP enrichment, validate the relevant tests.
- Prefer small, targeted fixes over broad rewrites.
- For deployment/config work, follow the README guidance and keep production security settings explicit.
