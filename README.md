# Inverter Dashboard

[![Docker Hub](https://img.shields.io/docker/v/alvit/inverter-dashboard?label=Docker%20Hub&logo=docker)](https://hub.docker.com/r/alvit/inverter-dashboard)
[![GitHub](https://img.shields.io/github/license/victron-venus/inverter-dashboard)](LICENSE)

Real-time web dashboard for monitoring Victron inverter systems via MQTT. Designed to work with [inverter-control](https://github.com/victron-venus/inverter-control) on Cerbo GX.

![Inverter Dashboard](images/Screenshot.png)

## Features

- Real-time power monitoring (Grid, Solar, Battery, Consumption)
- Interactive controls via WebSocket
- Live power charts with uPlot
- EV charging status
- Water system monitoring
- Home automation controls
- Mobile-friendly responsive UI

## Quick Start

### Docker (Recommended)

```bash
docker run -d \
  --name inverter-dashboard \
  -p 8080:8080 \
  -e MQTT_HOST=192.168.1.100 \
  alvit/inverter-dashboard:latest
```

### Docker Compose

```yaml
version: '3.8'
services:
  dashboard:
    image: alvit/inverter-dashboard:latest
    ports:
      - "8080:8080"
    environment:
      - MQTT_HOST=192.168.1.100
      - MQTT_PORT=1883
    restart: unless-stopped
```

### Portainer Stack

See [portainer-stack.yml](portainer-stack.yml) for Portainer deployment.

## Configuration

| Environment Variable | Default | Description |
|---------------------|---------|-------------|
| `MQTT_HOST` | `192.168.160.150` | MQTT broker hostname |
| `MQTT_PORT` | `1883` | MQTT broker port |
| `WEB_PORT` | `8080` | Web server port (inside the container) |
| `INVERTER_DASHBOARD_CONFIG` | `/app/config` | Host folder mounted read-only: `ha_secrets.py` and optional TLS files |

### Config directory (`ha_secrets.py` + optional HTTPS)

Committed template only: [`ha_secrets.example.py`](ha_secrets.example.py). Real secrets live in **`config/ha_secrets.py`** and/or **`ha_secrets.py`** next to `server.py` — both filenames are **gitignored** (never push).

**Synology NAS (deploy path used in this repo):**

| Location | Purpose |
|---------|---------|
| `/volume1/docker/inverter-dashboard/config` | Host folder mounted read-only into the container as **`/app/config`**. Put **`ha_secrets.py`**, optional **`dashboard.crt`** / **`dashboard.key`** here. Same path in [`docker-compose.yml`](docker-compose.yml) and [`portainer-stack.yml`](portainer-stack.yml). |

- **After clone (any machine):** `./scripts/init-config.sh` creates **`config/ha_secrets.py`** from the example; fill in **`HA_TOKEN`** / **`HA_URL`** (typically the same long-lived token as inverter-control **`secrets.py`**).
- **`postinstall.sh`** (in repo root): run **on your Mac/PC** (not on the NAS). Set SSH to the Synology, then:

  ```bash
  export SYNOLOGY_SSH='alvit@192.168.x.x'   # or Host from ~/.ssh/config; key-based SSH, no password
  ./postinstall.sh
  ```

  Expects **passwordless `sudo`** on the NAS for **`docker` / `docker compose`** and for writing under **`/volume1/docker/...`** (user `alvit` is usually not in the `docker` group). Files are uploaded to a temp dir, then **`sudo install`** into config. Env: **`REMOTE_BASE`**, **`SOURCE_CONFIG`**, **`STACK_FILE`**, **`DOCKER`** (default `sudo docker`).

If **both** cert files exist in that folder, the **entrypoint enables HTTPS on the same port** as HTTP would use; otherwise HTTP only.

### Command Line Arguments

```bash
python server.py --mqtt-host 192.168.1.100 --mqtt-port 1883 --port 8080
```

### HTTPS (why you still see `http://`)

By default the app and the published Docker image listen on **plain HTTP** (port `8080`). Nothing is wrong with your deploy — TLS is not enabled unless you add it.

**Choose one approach:**

1. **Reverse proxy (recommended for production / LAN DNS)**  
   Put **Caddy**, **Traefik**, or **nginx** in front of the container on port **443**, terminate Let’s Encrypt (or your certs) there, and proxy to `http://inverter-dashboard:8080`. You open `https://dashboard.example.com` in the browser; the container keeps HTTP internally.

2. **TLS inside the Python app** (good for quick tests / single host)

   **Docker (recommended layout):** mount your host config folder to **`/app/config`**, put **`dashboard.crt`** and **`dashboard.key`** next to **`ha_secrets.py`**. The entrypoint detects both files and passes **`--ssl-cert`** / **`--ssl-key`** automatically on the **same** port as without TLS (default 8080). Map ports e.g. `"8443:8080"` if you want HTTPS on 8443 externally.

   Generate certs (repo includes a helper):

   ```bash
   ./scripts/ssl-local-deploy.sh
   # Optional: TLS_CN=myhost.local ./scripts/ssl-local-deploy.sh
   ```

   Trust the cert on your Mac (script prints the exact `security add-trusted-cert` command).

   **Local run (paths arbitrary):**

   ```bash
   python server.py --mqtt-host … --port 8443 \
     --ssl-cert .certs/dashboard.crt --ssl-key .certs/dashboard.key
   ```

   **Docker Compose / Portainer:** host config path (Synology):

   ```yaml
   volumes:
     - /volume1/docker/inverter-dashboard/config:/app/config:ro
   ```

   On a dev PC without `/volume1`, comment out this volume or bind a local `./config` instead.

   If you omit **`dashboard.crt`** / **`dashboard.key`** on the host, the app stays on HTTP.

   The image **`HEALTHCHECK`** uses **`scripts/docker_healthcheck.py`**, which calls **`/api/state`** over HTTP or HTTPS depending on whether `dashboard.crt` + `dashboard.key` exist in the config directory.

3. **`mkcert`** — alternative to OpenSSL for local dev trust; still point the app at the generated `.pem` paths with `--ssl-cert` / `--ssl-key`.

## MQTT Topics

### Subscribed (incoming data)
- `inverter/state` - JSON with current system state
- `inverter/console` - Console log messages

### Published (commands)
- `inverter/cmd/toggle` - Toggle boolean entities
- `inverter/cmd/press` - Press button entities
- `inverter/cmd/setpoint` - Set power setpoint
- `inverter/cmd/dry_run` - Toggle dry run mode
- `inverter/cmd/limits` - Set power limits
- `inverter/cmd/ess_mode` - Toggle ESS mode
- `inverter/cmd/loop_interval` - Set control loop interval

## Expected State Format

```json
{
  "gt": 150,
  "g1": 100,
  "g2": 50,
  "tt": 2500,
  "t1": 1500,
  "t2": 1000,
  "solar_total": 3500,
  "battery_soc": 85,
  "battery_power": -500,
  "battery_voltage": 52.4,
  "setpoint": 0,
  "inverter_state": "Inverting",
  "dry_run": false,
  "ess_mode": {
    "mode_name": "Optimized (with BatteryLife)",
    "is_external": false
  },
  "booleans": {
    "auto_mode": true,
    "ev_boost": false
  },
  "daily_stats": {
    "produced_today": 25.5,
    "produced_dollars": 7.65,
    "grid_kwh": 2.3
  }
}
```

## GitHub release

Keep **`release.txt`** filled with notes for the next version (create the file locally if your `.gitignore` excludes it). Then:

```bash
chmod +x release.sh
./release.sh
```

The script **`git fetch origin --tags`** first, bumps **`VERSION`**, commits if the file changed, creates the new **annotated tag**, **`git push`** branch + tag, runs **`gh release create`**, then **`git fetch --tags`** and **`git pull --ff-only`** so the next run sees the latest tag and does not propose the same version again.

## Development

### Local Setup

```bash
# Clone repository
git clone https://github.com/victron-venus/inverter-dashboard.git
cd inverter-dashboard

# Create virtual environment
python -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Optional: config/ha_secrets.py for Home Assistant direct mode (gitignored)
./scripts/init-config.sh

# Run
python server.py --mqtt-host your-mqtt-broker
```

### Build Docker Image Locally

```bash
docker build -t inverter-dashboard .
docker run -p 8080:8080 -e MQTT_HOST=192.168.1.100 inverter-dashboard
```

## Multi-Architecture Support

Docker images are built for:
- `linux/amd64` (x86_64)
- `linux/arm64` (Raspberry Pi 4, Apple Silicon, etc.)

## Related Projects

This project is part of a Victron Venus OS integration suite:

| Project | Description |
|---------|-------------|
| [inverter-control](https://github.com/victron-venus/inverter-control) | ESS external control with web dashboard |
| **inverter-dashboard** (this) | Remote web dashboard via MQTT (Docker) |
| [dbus-mqtt-battery](https://github.com/victron-venus/dbus-mqtt-battery) | MQTT to D-Bus bridge for BMS integration |
| [dbus-tasmota-pv](https://github.com/victron-venus/dbus-tasmota-pv) | Tasmota smart plug as PV inverter on D-Bus |
| [esphome-jbd-bms-mqtt](https://github.com/victron-venus/esphome-jbd-bms-mqtt) | ESP32 Bluetooth monitor for JBD BMS |
| [inverter-monitoring](https://github.com/victron-venus/inverter-monitoring) | Telegraf + InfluxDB + Grafana monitoring stack |

## Author

Created by [@4alvit](https://github.com/4alvit)

## License

MIT License - see [LICENSE](LICENSE)
