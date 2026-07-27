# =============================================================================
# Multi-stage build for ultra-slim inverter-dashboard image (~40MB target)
# =============================================================================
# Stage 1: Builder - install dependencies with uv into a virtual environment
# =============================================================================
FROM python@sha256:26730869004e2b9c4b9ad09cab8625e81d256d1ce97e72df5520e806b1709f92 AS builder

WORKDIR /app

# Install uv for fast, reproducible dependency resolution
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Install build dependencies only (removed at runtime)
RUN apk add --no-cache gcc musl-dev libffi-dev

# Copy full project (needed for package installation with pyproject.toml)
COPY . .

# Install production dependencies + package into isolated venv
RUN uv sync --frozen --no-dev --no-editable --python python


# =============================================================================
# Stage 2: Runtime - minimal image with only the venv and app code
# =============================================================================
FROM python@sha256:26730869004e2b9c4b9ad09cab8625e81d256d1ce97e72df5520e806b1709f92 AS runtime

WORKDIR /app

# Runtime-only dependencies: bash for entrypoint, tini for signal handling
RUN apk add --no-cache bash tini

# Create non-root user
RUN addgroup -g 1000 app && adduser -D -u 1000 -G app app

# Copy virtual environment from builder
COPY --from=builder /app/.venv /app/.venv

# Copy app source (excluding tests, configs, etc.)
COPY --from=builder /app/src /app/src
COPY --from=builder /app/VERSION /app/VERSION

# Config and entrypoint
COPY local_config.example.py entrypoint.sh ./

RUN chmod +x entrypoint.sh && \
    mkdir -p /app/config && \
    chown -R app:app /app

USER app

ENV PATH="/app/.venv/bin:${PATH}"
ENV INVERTER_DASHBOARD_CONFIG=/app/config
ENV WEB_PORT=8080
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
  CMD python -m inverter_dashboard.scripts.docker_healthcheck || exit 1

EXPOSE 8080

ENTRYPOINT ["/sbin/tini", "--", "/app/entrypoint.sh"]
CMD ["--mqtt-host", "Cerbo", "--port", "8080"]
