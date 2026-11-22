# One image for every service (apps, API, scheduler, backup). The command differs per service
# in docker-compose.yml. Built and run on a GitHub Actions Linux runner.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PORTFOLIO_MODE=server \
    PORTFOLIO_DB_PATH=/data/portfolio.db \
    PORTFOLIO_BACKUP_DIR=/backups

# tzdata so TZ works (stored timestamps are naive local time); util-linux provides flock.
RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata util-linux \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .
RUN sed -i 's/\r$//' scripts/docker-entrypoint.sh \
    && chmod +x scripts/docker-entrypoint.sh \
    && useradd --create-home --uid 1000 portfolio \
    && mkdir -p /data /backups \
    && chown -R portfolio:portfolio /data /backups /app
USER portfolio

VOLUME ["/data", "/backups"]
ENTRYPOINT ["/app/scripts/docker-entrypoint.sh"]
CMD ["python", "run.py", "--no-browser"]
