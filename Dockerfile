# Phase 2 local runner. It deliberately exposes the same CLI as local Python.
FROM node:22-bookworm-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src

RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 python3-venv python3-pip curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Current Phase 2 workflow uses only Python's standard library. Keep the
# sender dependency available for the imported Reply.io workbook module.
COPY vendor/reply-io-email-sender/requirements.txt /tmp/reply-requirements.txt
RUN python3 -m pip install --break-system-packages --no-cache-dir -r /tmp/reply-requirements.txt

COPY pyproject.toml README.md ./
COPY src ./src
COPY migrations ./migrations
COPY config ./config
COPY vendor ./vendor

RUN useradd --create-home --uid 10001 outreach \
    && mkdir -p /app/data /app/artifacts \
    && chown -R outreach:outreach /app/data /app/artifacts

USER outreach
ENTRYPOINT ["python3", "-m", "outreach"]
CMD ["--help"]
