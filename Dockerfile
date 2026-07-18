FROM python:3.14-alpine@sha256:26730869004e2b9c4b9ad09cab8625e81d256d1ce97e72df5520e806b1709f92

WORKDIR /app

# bash: entrypoint uses bash ([[ ]], arrays). git: clone at build time and git fetch in entrypoint.
RUN apk add --no-cache bash git

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install as editable package so `python -m inverter_dashboard` works
COPY pyproject.toml ./
COPY src/ ./src/
COPY VERSION ./
RUN pip install --no-cache-dir -e .

# Overlay site_config.example.py at repo root (used by entrypoint / Docker config mount)
COPY site_config.example.py entrypoint.sh ./

RUN chmod +x entrypoint.sh && mkdir -p /app/config

# Non-root user (Docker Scout / smaller attack surface).
# UID/GID 1000 is a common default Linux user; override in compose if needed.
RUN addgroup -g 1000 app && adduser -D -u 1000 -G app app && \
    chown -R app:app /app

USER app

ENV INVERTER_DASHBOARD_CONFIG=/app/config
ENV WEB_PORT=8080

HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
  CMD python -m inverter_dashboard.scripts.docker_healthcheck || exit 1

EXPOSE 8080

ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["--mqtt-host", "Cerbo", "--port", "8080"]
