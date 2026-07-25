# python:3.14-alpine — pinned by digest only (avoid tag+digest duplication).
FROM python@sha256:26730869004e2b9c4b9ad09cab8625e81d256d1ce97e72df5520e806b1709f92

WORKDIR /app

# bash: entrypoint uses bash ([[ ]], arrays). git: clone at build time and git fetch in entrypoint.
# uv: installs from the committed lockfile for reproducible, hash-verified dependency resolution.
COPY --from=ghcr.io/astral-sh/uv@sha256:ba4857bf2a068e9bc0e64eed8563b065908a4cd6bfb66b531a9c424c8e25e142 /uv /usr/local/bin/uv
RUN apk add --no-cache bash git

# Install pinned dependencies (incl. transitive) from uv.lock — no unresolved version ranges.
COPY pyproject.toml uv.lock ./
COPY src/ ./src/
COPY VERSION ./
RUN uv sync --frozen --no-dev --no-editable
ENV PATH="/app/.venv/bin:${PATH}"

# Overlay local_config.example.py at repo root (used by entrypoint / Docker config mount)
COPY local_config.example.py entrypoint.sh ./

# Non-root user (Docker Scout / smaller attack surface).
# UID/GID 1000 is a common default Linux user; override in compose if needed.
RUN chmod +x entrypoint.sh && mkdir -p /app/config && \
    addgroup -g 1000 app && adduser -D -u 1000 -G app app && \
    chown -R app:app /app

USER app

ENV INVERTER_DASHBOARD_CONFIG=/app/config
ENV WEB_PORT=8080

HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
  CMD python -m inverter_dashboard.scripts.docker_healthcheck || exit 1

EXPOSE 8080

ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["--mqtt-host", "Cerbo", "--port", "8080"]
