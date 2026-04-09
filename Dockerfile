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
COPY entrypoint.sh .
COPY VERSION .

RUN chmod +x entrypoint.sh

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/api/state')" || exit 1

EXPOSE 8080

ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["--mqtt-host", "192.168.160.150", "--port", "8080"]
