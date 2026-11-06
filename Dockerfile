FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

COPY . .
ARG APP_VERSION=0.1.0
ENV APP_VERSION=${APP_VERSION}
RUN DJANGO_SECRET_KEY=build-only python manage.py collectstatic --noinput \
    && addgroup --system --gid 10001 dmarc \
    && adduser --system --uid 10001 --ingroup dmarc dmarc \
    && mkdir -p /data \
    && chown dmarc:dmarc /data

USER 10001:10001
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --start-period=20s --retries=3 \
    CMD python -c "from urllib.request import urlopen; urlopen('http://127.0.0.1:8000/healthz/', timeout=2)"

CMD ["gunicorn", "dmarc_reporter.wsgi:application", "--bind", "0.0.0.0:8000", "--control-socket", "/tmp/gunicorn.ctl", "--workers", "2", "--access-logfile", "-", "--error-logfile", "-"]
