FROM python:3.14-alpine@sha256:003970a263347645cd23d4f90929ad16ba7ce7d808ee4674ffcc93cb21cc289f

WORKDIR /app

# bash: entrypoint uses bash ([[ ]], arrays). git: clone at build time and git fetch in entrypoint.
RUN apk add --no-cache bash git

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Same layout as before: tree from GitHub, then overlay files from the build context.
RUN git clone --depth 1 https://github.com/victron-venus/inverter-dashboard.git /app/repo && \
    cp -a /app/repo/. /app/ && \
    rm -rf /app/repo

COPY *.py ./
COPY ha_client.py ha_secrets.example.py ./
COPY scripts/docker_healthcheck.py scripts/
COPY entrypoint.sh .
COPY VERSION .

RUN chmod +x entrypoint.sh && mkdir -p /app/config

# Non-root user (Docker Scout / smaller attack surface).
# UID/GID 1000 is a common default Linux user; override in compose if needed.
RUN addgroup -g 1000 app && adduser -D -u 1000 -G app app && \
    chown -R app:app /app

USER app

ENV INVERTER_DASHBOARD_CONFIG=/app/config
ENV WEB_PORT=8080

HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
  CMD python /app/scripts/docker_healthcheck.py || exit 1

EXPOSE 8080

ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["--mqtt-host", "192.168.160.150", "--port", "8080"]
