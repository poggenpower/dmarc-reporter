# DMARC Reporter

A Dockerized, authenticated dashboard for DMARC aggregate reports delivered to an IMAP mailbox.

## What it does

- Reads XML reports attached directly, as ZIP files, or as GZIP files.
- Connects to IMAP over verified TLS and selects the configured folder read-only.
- Fetches messages with `BODY.PEEK`; it never marks messages read, deletes them, or moves them.
- Saves normalized report data and IMAP processing state in SQLite under `/data`.
- Deduplicates both mailbox messages and report identities.
- Provides an authenticated, interactive dashboard with domain, policy disposition, and report destination charts. Selecting a chart legend value applies it as a filter.
- Lets signed-in users set their report email address and create/revoke permission-scoped API tokens. The report API sends an HTML report by email.
- Filters by date, domain, destination/reporter, source IP, disposition, and alignment status. By default, only non-aligned records are shown and SPF-passing sources are excluded.
- Enriches globally routable source IPs with reverse-DNS hostnames, ASN/network attribution, and city/country from local DB-IP Lite databases. The top-source chart and report table both show attribution details.
- Shows the first and latest report dates for the current date/domain/destination selection.
- Makes the IMAP credential available to the web service only for SMTP report delivery; ingestion uses it to read the mailbox.

Raw email bodies are processed in memory and are not stored. Report contents (including source IPs and domains) are stored in the persistent database. Keep the Docker volume protected and back it up according to your data-retention policy.

## Run with Docker Compose

Requirements: Docker Engine with Docker Compose.

1. Prepare an IMAP credential file with the username on the first line and password on the second line. Restrict its permissions (for example, `chmod 600 /path/to/imap-credentials`).
2. Set deployment values in the shell or a local, untracked `.env` file:

   ```sh
   DJANGO_SECRET_KEY="$(python3 -c 'import secrets; print(secrets.token_urlsafe(64))')"
   DJANGO_ALLOWED_HOSTS=localhost,127.0.0.1
   DJANGO_DEBUG=true
   IMAP_HOST=imap.example.net
   IMAP_PORT=993
   IMAP_CREDENTIALS_FILE=/absolute/path/to/imap-credentials
   SMTP_HOST=mail.example.net
   SMTP_PORT=465
   SMTP_USE_TLS=true
   ```

   For production, use `DJANGO_DEBUG=false`, a long random `DJANGO_SECRET_KEY`, the actual host name in `DJANGO_ALLOWED_HOSTS`, and a TLS-terminating reverse proxy. Set `DJANGO_TRUST_PROXY_SSL=true` only when that proxy overwrites `X-Forwarded-Proto`; set `DJANGO_CSRF_TRUSTED_ORIGINS` to the public `https://` origin when needed. After confirming the proxy correctly forwards HTTPS, enable `DJANGO_SECURE_SSL_REDIRECT=true` and set `DJANGO_HSTS_SECONDS` to an appropriate max-age. Apply login rate limiting at the reverse proxy. Do not expose the HTTP application directly to the internet.

3. Start the services:

   ```sh
   docker compose up --build -d
   ```

   The first start applies migrations. The web service is at `http://localhost:8000` by default; the ingest worker polls the mailbox every five minutes and processes up to 100 unprocessed messages per poll. Set `IMAP_POLL_INTERVAL` and `IMAP_BATCH_SIZE` to change those limits.

4. Create an initial administrator account:

   ```sh
   docker compose run --rm web python manage.py createsuperuser
   ```

   Sign in at `/accounts/login/`. Staff users can manage accounts and groups at `/admin/`. Django Groups and permissions provide a path to later RBAC.

## Emailed report API

Sign in and open `/account/` to set the account email address and create an API token with the `Can request an emailed report through the API` permission. A token is displayed only once; store it securely and revoke it from the same page when it is no longer needed. Tokens are stored as hashes and are sent with API requests using the `Authorization: Bearer <token>` header.

Send a JSON `POST` to `/api/v1/reports/` to email the report to the token owner's configured email address:

```sh
curl -X POST http://localhost:8000/api/v1/reports/ \
  -H "Authorization: Bearer YOUR_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"start_date":"2025-01-01","end_date":"2025-01-07","domains":["example.com"]}'
```

`start_date` and `end_date` are optional ISO dates; when omitted, the API reports the latest seven calendar days. Omit `domains` to include all domains. Supplying a domain list filters the report to those domains. The domain and destination totals include all records; policy-violation and top-source sections include records where neither DKIM nor SPF alignment passes, excluding aligned SPF-passing sources. The API returns `200` with the selected period and totals after SMTP confirms the email was accepted.

SMTP host and port have no defaults and must be configured explicitly. SMTP authentication reuses the IMAP username and password; the sender address defaults to the IMAP username, or can be set with `SMTP_SENDER_EMAIL`. `SMTP_USE_TLS=true` enables implicit TLS, `SMTP_USE_TLS=starttls` enables STARTTLS, and an empty value or any other value uses an unencrypted connection. SMTP failures return the server/connection error and recommend checking the application log. The HTML email uses CSS/HTML-only charts and includes all report records matching the requested date and domain filters. Local development can configure `SMTP_USERNAME` and `SMTP_PASSWORD` instead of a credential file.

For a one-time mailbox scan instead of the continuous worker:

```sh
docker compose run --rm ingest python manage.py ingest_dmarc --once
```

Failed messages are recorded without storing their contents. Retry them with:

```sh
docker compose run --rm ingest python manage.py ingest_dmarc --once --retry-failed
```

For a published image, set `DMARC_IMAGE` to `ghcr.io/<owner>/<repository>:v<version>`, then run `docker compose pull` and `docker compose up -d`. The Compose file binds the web port to loopback by default; keep that binding when using a host-level reverse proxy.

## Run on Kubernetes with Kustomize

The sample in [`k8s/`](./k8s/) deploys the migration Job, web service, IMAP ingestion worker, GeoIP updater, and a weekly email-report CronJob. It generates a ConfigMap and separate Secrets (Django, IMAP, SMTP, and report API token) from local environment files. It also creates persistent volume claims for the SQLite data and GeoIP databases.

1. Build and publish the application image, then replace `ghcr.io/example-org/dmarc-reporter:latest` in the workload manifests with its image reference.
2. Copy the secret templates to the files consumed by Kustomize:

   ```sh
   cp k8s/secret-files/django.env.example k8s/secret-files/django.env
   cp k8s/secret-files/imap.env.example k8s/secret-files/imap.env
   cp k8s/secret-files/smtp.env.example k8s/secret-files/smtp.env
   cp k8s/secret-files/report-api.env.example k8s/secret-files/report-api.env
   ```

   Replace every `replace-...` value before deploying. Generate a Django key with `python3 -c 'import secrets; print(secrets.token_urlsafe(64))'`. Create a token with the report permission at `/account/` and set it as `REPORT_API_TOKEN` in `report-api.env`. The `.env` files are ignored by Git; do not commit them.
3. Edit [`k8s/config/app.env`](./k8s/config/app.env) with your host, CSRF origin, IMAP server, SMTP server, and a `REPORT_API_URL` reachable from the cluster over HTTPS. On Kubernetes 1.27+, the CronJob runs every Monday at 09:00 UTC and emails the report for the latest seven calendar days. For a TLS-terminating reverse proxy, set `DJANGO_TRUST_PROXY_SSL=true` only when it overwrites `X-Forwarded-Proto`; enable `DJANGO_SECURE_SSL_REDIRECT` and HSTS only after verifying the proxy configuration.
4. Apply the sample and wait for the migration Job:

   ```sh
   kubectl apply -k k8s/
   kubectl wait --for=condition=complete job/dmarc-reporter-migrate --timeout=5m
   ```

   The web Service is ClusterIP-only; expose it through your TLS-terminating Ingress or use `kubectl port-forward svc/dmarc-reporter-web 8000:8000` for local access. Create an administrator with `kubectl exec` in the web pod and `python manage.py createsuperuser`.

The sample is intended for a single-node deployment using storage that supports SQLite locking. It uses ReadWriteOnce claims shared by the app pods, so keep each workload at one replica; use a database designed for concurrent multi-node access before scaling out. The GeoIP updater downloads DB-IP data and needs outbound HTTPS. No NetworkPolicy is included because ingress-controller labels and outbound IMAP/SMTP ports vary by cluster; add policies that allow only your ingress controller, DNS, and the required outbound services. Kubernetes Secret values are base64-encoded, not encrypted: enable encryption at rest and restrict Secret access with RBAC. Tune PVC sizes, resource limits, image references, and host values for your cluster. All containers run as UID/GID 10001, with a read-only root filesystem, no Linux capabilities, no privilege escalation, RuntimeDefault seccomp, and no service-account token mount. Jobs are immutable; delete the completed `dmarc-reporter-migrate` Job before reapplying after changing its image, configuration, or secrets.

## Local development and tests

Python 3.12+ is required.

```sh
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements-dev.txt
export DJANGO_SECRET_KEY=local-development-only
export DJANGO_DEBUG=true
export DATA_DIR=./data
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

Run checks:

```sh
ruff check .
python manage.py makemigrations --check --dry-run
python manage.py test
```

The test fixtures are sanitized examples derived from the supplied mailbox; reporter names, domains, email addresses, report IDs, dates, and IP addresses are replaced with documentation-safe values.

## Configuration

| Variable | Purpose | Default |
|---|---|---|
| `DJANGO_SECRET_KEY` | Django signing/session secret; required unless debug mode is enabled | — |
| `DJANGO_DEBUG` | Enable local development settings | `false` |
| `DJANGO_ALLOWED_HOSTS` | Comma-separated accepted host names | `localhost,127.0.0.1` |
| `DJANGO_CSRF_TRUSTED_ORIGINS` | Comma-separated public origins, including scheme | empty |
| `DJANGO_TRUST_PROXY_SSL` | Trust `X-Forwarded-Proto` from a sanitizing reverse proxy | `false` |
| `DJANGO_SECURE_SSL_REDIRECT` | Redirect HTTP requests to HTTPS | `false` |
| `DJANGO_HSTS_SECONDS` | HSTS max-age; enable only after HTTPS is verified | `0` |
| `DJANGO_HSTS_INCLUDE_SUBDOMAINS` | Extend HSTS to all subdomains; use only if all support HTTPS | `false` |
| `DATA_DIR` | Persistent database directory | `/data` |
| `GEOIP_DB_DIR` | Host directory containing DB-IP Lite files for Docker Compose | `./geoip` |
| `GEOIP_CITY_DB` | Path to the DB-IP City Lite database | `/geoip/dbip-city-lite.mmdb` in Docker |
| `GEOIP_ASN_DB` | Path to the DB-IP ASN Lite database | `/geoip/dbip-asn-lite.mmdb` in Docker |
| `WEB_BIND_ADDRESS` | Host interface to bind the web port | `127.0.0.1` |
| `WEB_PORT` | Host port for the web service | `8000` |
| `IMAP_HOST` | TLS IMAP host | required by ingest worker |
| `IMAP_PORT` | TLS IMAP port | `993` |
| `IMAP_FOLDER` | Folder to inspect | `INBOX` |
| `IMAP_CREDENTIALS_FILE` | Docker secret file: username then password, one per line | required by Compose |
| `IMAP_POLL_INTERVAL` | Worker delay between polls, minimum 15 seconds | `300` seconds |
| `IMAP_BATCH_SIZE` | Maximum unprocessed messages per poll | `100` |
| `SMTP_HOST` | SMTP server hostname | Required; no default |
| `SMTP_PORT` | SMTP server port | Required; no default |
| `SMTP_USE_TLS` | `true` for implicit TLS, `starttls` for STARTTLS, anything else for no encryption | empty |
| `SMTP_SENDER_EMAIL` | Sender address for emailed reports | IMAP/SMTP username |
| `SMTP_USERNAME` / `SMTP_PASSWORD` | SMTP credentials when no credentials file is configured (local development) | empty |

The worker also supports `IMAP_USERNAME` with `IMAP_PASSWORD_FILE` (or `IMAP_PASSWORD`) when not using Compose.

## Source IP enrichment

The application resolves PTR names through DNS and reads city/country and ASN ownership from [DB-IP Lite](https://db-ip.com) MMDB files. DB-IP Lite is updated monthly and licensed under [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). Download the City and ASN files into `./geoip` for Docker Compose. The dashboard provides the required DB-IP attribution link.

The `geoip-updater` Compose service checks daily and downloads the current DB-IP monthly release when due (no more often than every 25 days). It falls back to the prior month's release if the current one is not yet available. Both databases are downloaded and validated before replacement, then stored source-IP attribution is refreshed. DB-IP supplies monthly full snapshots rather than incremental patches, so each update downloads both databases. The updater has read/write access to `./geoip`; the web and ingest services mount it read-only. Set `GEOIP_DB_DIR` to use another host directory and `GEOIP_CITY_DB` or `GEOIP_ASN_DB` to override either path. For a one-time manual check, run:

```sh
docker compose run --rm --no-deps geoip-updater python manage.py update_geoip
```

PTR names are provided by the IP operator and should not be treated as proof of the sending party's identity. Private, reserved, and documentation-only IP addresses are not looked up.

## Versioning and releases

Prefix every commit message with `major:`, `feat:`, `sec:`, `fix:`, `doc:`, or `chore:`. A merged pull request to `main` uses its highest-impact prefix: `major:` increments the major version, `feat:` and `sec:` increment the minor version, and `fix:` and `doc:` increment the build (patch) number. The patch number also increases once for each non-`chore:` commit in the pull request. `chore:`-only pull requests do not change the version or create a release. For mixed pull requests, the highest-impact prefix determines the major/minor change.

The repository includes a `commit-msg` hook to verify the subject prefix. Enable it after cloning:

```sh
git config core.hooksPath .githooks
```

After a qualifying pull request is merged, GitHub Actions creates or updates one release pull request with the accumulated version changes since the latest stable tag. Release pull requests receive the normal required CI checks; merging one creates the matching `v<version>` tag and publishes the versioned and `latest` images to GitHub Container Registry, then creates a GitHub Release. The release automation uses the `RELEASE_TOKEN` repository Actions secret so updates to its release branch trigger regular pull-request CI. Configure it as a fine-grained token with Contents and Pull requests read/write access, scoped to this repository. A missed merge event can be recovered by manually dispatching the Prepare release PR workflow; it recalculates the accumulated changes from `main`.

Pushes to non-`main` branches in this repository also run the test and image-build jobs. When they pass, the workflow publishes a development image tagged with a prerelease version and a branch alias, for example `0.1.2-feature-email-report.42.abcdef0` and `dev-feature-email-report`. Development versions increment only the patch component, include a sanitized branch name, the CI workflow run number, and the commit SHA. Pull-request builds and fork branches do not publish images. Development tags never update `latest` or create GitHub Releases. Registry consumers need package read access to pull these images.

Dependabot checks pip and GitHub Actions dependencies weekly and uses the `sec:` prefix. Enable package publishing in repository Actions settings and configure the required pull-request checks before merging.
