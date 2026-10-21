# syntax=docker/dockerfile:1
FROM python:3.12-slim

# Python behaviour inside containers:
#   PYTHONDONTWRITEBYTECODE — .pyc files are dead weight in an image
#   PYTHONUNBUFFERED        — without it, logs sit in a buffer and
#                             `docker logs` shows nothing until it flushes
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    LOG_DIR=/app/logs \
    UPLOAD_FOLDER=/app/uploads

WORKDIR /app

# curl is used by the container HEALTHCHECK below. Build tools are installed
# and removed in the same layer so they don't ship in the final image.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

# Requirements are copied on their own first: this layer is then cached and
# only re-runs when dependencies change, not on every source edit.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Run as a non-root user. If the app is ever compromised, the attacker lands
# as an unprivileged account rather than root inside the container.
RUN useradd --create-home --shell /bin/bash dealbridge \
    && mkdir -p /app/logs /app/uploads \
    && chown -R dealbridge:dealbridge /app
USER dealbridge

EXPOSE 5000

# Hits the real health endpoint, which checks the database — a process that
# is up but can't reach MySQL should not be reported as healthy.
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD curl -fsS http://localhost:5000/api/health || exit 1

CMD ["gunicorn", "--config", "gunicorn_conf.py", "wsgi:application"]
