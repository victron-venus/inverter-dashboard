# syntax=docker/dockerfile:1
# =============================================================================
# Multi-stage build for ultra-slim inverter-dashboard image (~40MB target)
# =============================================================================
# Stage 1: Builder - install dependencies with uv into a virtual environment
# =============================================================================
FROM python@sha256:26730869004e2b9c4b9ad09cab8625e81d256d1ce97e72df5520e806b1709f92 AS builder

WORKDIR /app

# Install uv for fast, reproducible dependency resolution
COPY --from=ghcr.io/astral-sh/uv@sha256:ba4857bf2a068e9bc0e64eed8563b065908a4cd6bfb66b531a9c424c8e25e142 /uv /usr/local/bin/uv

# Install build dependencies only (removed at runtime). Required because not
# all dependencies ship prebuilt cp314 musllinux wheels, so some must be
# compiled from source during uv sync.
RUN apk add --no-cache gcc libffi-dev musl-dev

# Copy only what's needed for package installation with pyproject.toml
COPY pyproject.toml uv.lock VERSION ./
COPY src ./src

# Install production dependencies + package into isolated venv
RUN uv sync --frozen --no-dev --no-editable --python python


# =============================================================================
# Stage 2: Runtime - minimal image with only the venv and app code
# =============================================================================
FROM python@sha256:26730869004e2b9c4b9ad09cab8625e81d256d1ce97e72df5520e806b1709f92 AS runtime

WORKDIR /app

# Build arg to opt into bundling .git for the SELF_UPDATE_ENABLED entrypoint path.
# Disabled by default so the default image stays close to the ~40MB target and
# doesn't ship full commit history/metadata.
ARG INCLUDE_GIT_FOR_SELF_UPDATE=false

# Runtime-only dependencies: bash for entrypoint, tini for signal handling,
# libffi for compiled extensions (e.g. cffi/cryptography) linked at build time.
# git is only installed when self-update support is explicitly requested.
# Create non-root user in the same layer to keep RUN instructions consolidated
RUN apk add --no-cache bash libffi tini && \
    if [ "$INCLUDE_GIT_FOR_SELF_UPDATE" = "true" ]; then apk add --no-cache git; fi && \
    addgroup -g 1000 app && adduser -D -u 1000 -G app app

# Copy virtual environment from builder
COPY --from=builder /app/.venv /app/.venv

# Copy app source (excluding tests, configs, etc.)
COPY --from=builder /app/src /app/src
COPY --from=builder /app/VERSION /app/VERSION

# Bind-mount (not COPY) the .git checkout so it never lands in an image layer
# unless self-update support is explicitly requested. This keeps the default
# image close to the ~40MB target and avoids shipping full git history/metadata.
RUN --mount=type=bind,source=.git,target=/tmp/git-src \
    if [ "$INCLUDE_GIT_FOR_SELF_UPDATE" = "true" ]; then \
        cp -a /tmp/git-src /app/.git; \
    fi

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
