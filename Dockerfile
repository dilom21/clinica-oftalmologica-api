# Linux container for both the FastAPI server and the Resend outbox worker.
# PostgreSQL 18 client covers pg_dump/pg_restore/psql for current PG <= 18.
FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    APP_TIMEZONE=America/La_Paz \
    SAAS_BACKUP_PRIVATE_DIR=/opt/app/private-backups

# Use the official PostgreSQL APT repository for a recent client toolchain.
# Never install/start PostgreSQL SERVER inside this image.
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends ca-certificates curl gnupg; \
    install -d -m 0755 /etc/apt/keyrings; \
    curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc \
      | gpg --dearmor -o /etc/apt/keyrings/postgresql.gpg; \
    echo 'deb [signed-by=/etc/apt/keyrings/postgresql.gpg] https://apt.postgresql.org/pub/repos/apt bookworm-pgdg main' \
      > /etc/apt/sources.list.d/pgdg.list; \
    apt-get update; \
    apt-get install -y --no-install-recommends postgresql-client-18; \
    apt-get purge -y --auto-remove curl gnupg; \
    rm -rf /var/lib/apt/lists/*

WORKDIR /opt/app

COPY requirements.txt ./
RUN python -m pip install --no-cache-dir -r requirements.txt \
    && python -m pip check \
    && python -c 'import httpx, fastapi, psycopg; print("Python dependencies OK")' \
    && pg_dump --version && pg_restore --version && psql --version

RUN set -eux; \
    groupadd --gid 10001 clinic; \
    useradd --uid 10001 --gid clinic --no-create-home --shell /usr/sbin/nologin clinic; \
    mkdir -p /opt/app/private-backups; \
    chown -R clinic:clinic /opt/app/private-backups; \
    chmod 0700 /opt/app/private-backups

COPY --chown=clinic:clinic . /opt/app/
USER 10001:10001

EXPOSE 8000
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips=*"]
