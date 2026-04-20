FROM python:3.14-slim

WORKDIR /app

# Install git for auto-updates
RUN apt-get update && apt-get install -y --no-install-recommends git && \
    rm -rf /var/lib/apt/lists/*

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Clone repo (for auto-update capability)
RUN git clone --depth 1 https://github.com/victron-venus/inverter-dashboard.git /app/repo && \
    mv /app/repo/* /app/ && rm -rf /app/repo

# Copy all Python modules as fallback (baked-in version)
COPY *.py ./
COPY ha_client.py ha_secrets.example.py ./
COPY entrypoint.sh .
COPY VERSION .

RUN chmod +x entrypoint.sh

# Default port (override in docker run / compose; healthcheck uses the same)
ENV WEB_PORT=8080

# Health check (HTTP; if you run TLS-only on the same port, override healthcheck in compose)
HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD python -c "import os,urllib.request; p=os.environ.get('WEB_PORT','8080'); urllib.request.urlopen('http://127.0.0.1:'+p+'/api/state')" || exit 1

EXPOSE 8080

ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["--mqtt-host", "192.168.160.150", "--port", "8080"]
